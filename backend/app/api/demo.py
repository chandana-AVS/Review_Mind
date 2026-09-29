"""Minimal demo API for the ReviewMind UI.

Serves REAL data only:
- PR diffs from demo-data (the code being reviewed)
- saved ReviewMind review runs from evaluation/results/reviews/
- feedback via the existing FeedbackStore -> Hindsight retain (demo bank only)
- stored feedback units and live recall from the demo bank

No second store: everything memory-related goes through HindsightService.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import BACKEND_DIR, get_settings
from app.feedback.models import FeedbackError, build_feedback, findings_of, load_review
from app.feedback.store import FeedbackStore
from app.hindsight import HindsightError, HindsightService
from app.review.agent import ReviewAgent
from app.review.diff import ChangedFile, PullRequest, detect_signals, load_pr, parse_patch, recall_probes

router = APIRouter(prefix="/api/demo")
REVIEWS = BACKEND_DIR.parent / "evaluation" / "results" / "reviews"
_local = threading.local()


def hs() -> HindsightService:
    # The sync Hindsight client binds to a per-thread event loop; FastAPI runs sync
    # endpoints in a thread pool, so each worker thread gets its own client.
    if not hasattr(_local, "hs"):
        _local.hs = HindsightService()
    return _local.hs


def review_runs(pr: int) -> dict[str, Path]:
    """Saved runs: 'baseline' = historical bank, 'latest' = demo bank (after feedback)."""
    s = get_settings()
    runs = {"baseline": REVIEWS / f"pr-{pr}-memory.json",
            "latest": REVIEWS / f"pr-{pr}-memory-{s.demo_bank_id}.json"}
    return {k: v for k, v in runs.items() if v.exists()}


def with_ids(review: dict) -> dict:
    for f, meta in zip(review.get("findings", []), findings_of(review)):
        f["id"] = meta.finding_id
    return review


@router.get("/status")
def status() -> dict:
    s = get_settings()
    try:
        server = hs().health()["server"]
        banks = {}
        for bank in (s.team_bank_id, s.demo_bank_id):
            if hs().bank_exists(bank):
                units = hs().list_memories(bank, limit=5000)
                banks[bank] = dict(Counter(m.type for m in units), total=len(units))
        return {"connected": True, "api_version": server.get("api_version"), "banks": banks,
                "baseline_bank": s.team_bank_id, "demo_bank": s.demo_bank_id}
    except HindsightError as exc:
        return {"connected": False, "error": str(exc), "baseline_bank": s.team_bank_id,
                "demo_bank": s.demo_bank_id}


@router.get("/pr/{number}")
def pr(number: int) -> dict:
    try:
        p = load_pr(number)
    except FileNotFoundError:
        raise HTTPException(404, f"PR #{number} not found")
    return {"number": p.number, "title": p.title, "author": p.author, "created_at": p.created_at,
            "body": p.body,
            "files": [{"path": f.path, "status": f.status,
                       "lines": [{"n": n, "kind": k, "text": t} for n, k, t in f.lines]} for f in p.files]}


@router.get("/pr/{number}/reviews")
def reviews(number: int) -> dict:
    runs = review_runs(number)
    if not runs:
        raise HTTPException(404, f"no saved review for PR #{number}")
    return {name: {"file": path.relative_to(BACKEND_DIR.parent).as_posix(), **with_ids(load_review(path))}
            for name, path in runs.items()}


class FeedbackIn(BaseModel):
    run: str = Field("baseline", pattern="^(baseline|latest)$")
    finding_id: str
    decision: str = Field(pattern="^(ACCEPTED|REJECTED)$")
    reason: str = ""
    developer: str = "erin"


def stored(store: FeedbackStore, document_id: str) -> list[dict]:
    return [{"type": m.type, "text": m.text, "document_id": m.document_id, "tags": m.tags,
             "metadata": m.metadata}
            for m in hs().list_memories(store.bank, limit=5000) if m.document_id == document_id]


@router.post("/pr/{number}/feedback")
def feedback(number: int, body: FeedbackIn) -> dict:
    runs = review_runs(number)
    if body.run not in runs:
        raise HTTPException(404, f"no {body.run} review for PR #{number}")
    path = runs[body.run]
    try:
        fb = build_feedback(load_review(path), path.relative_to(BACKEND_DIR.parent).as_posix(),
                            body.finding_id, body.decision, body.reason, body.developer)
    except FeedbackError as exc:
        raise HTTPException(422, str(exc))
    store = FeedbackStore(hs())  # demo bank only; refuses the baseline bank
    t = time.monotonic()
    try:
        store.ensure_bank()
        result = store.submit(fb)
    except HindsightError as exc:
        raise HTTPException(503, str(exc))
    return {**result, "bank": store.bank, "seconds": round(time.monotonic() - t, 1),
            "feedback": {"pr": fb.pr_number, "finding_id": fb.finding.finding_id,
                         "finding_title": fb.finding.title, "decision": fb.decision,
                         "reason": fb.reason, "developer": fb.developer,
                         "submitted_at": fb.submitted_at},
            "stored_units": stored(store, fb.document_id)}


@router.get("/pr/{number}/feedback/{finding_id}")
def feedback_state(number: int, finding_id: str) -> dict:
    store = FeedbackStore(hs())
    doc = f"feedback:payments-service:pr{number}:{finding_id}"
    return {"document_id": doc, "bank": store.bank, "stored_units": stored(store, doc)}


# --- review workspace: dataset PRs + custom code ----------------------------------------------

HELD_OUT = range(131, 145)  # feedback set 131-136, test set 137-144 (never ingested as history)


@router.get("/prs")
def prs() -> list[dict]:
    out = []
    for n in HELD_OUT:
        try:
            p = load_pr(n)
        except FileNotFoundError:
            continue
        runs = review_runs(n)
        out.append({"number": n, "title": p.title, "author": p.author, "split": p.split,
                    "files": len(p.files), "saved_runs": sorted(runs)})
    return out


class CheckIn(BaseModel):
    pr: int | None = None
    file_name: str | None = None
    code: str | None = None


def custom_pr(file_name: str, code: str) -> PullRequest:
    """User-supplied code: a unified diff (with @@ hunks) or plain code (all lines treated as added)."""
    name = (file_name or "snippet.py").strip() or "snippet.py"
    if any(line.startswith("@@") for line in code.splitlines()):
        changed = parse_patch(name, "modified", code)
    else:
        changed = ChangedFile(name, "added", [(i, "+", t) for i, t in enumerate(code.splitlines(), 1)])
    return PullRequest(0, f"Custom code · {name}", "", "you", datetime.now(UTC).isoformat(), "custom", [changed])


def resolve(body: CheckIn) -> PullRequest:
    if body.pr is not None:
        try:
            return load_pr(body.pr)
        except FileNotFoundError:
            raise HTTPException(404, f"PR #{body.pr} not found")
    if not (body.code or "").strip():
        raise HTTPException(422, "provide a PR number or some code")
    if len(body.code) > 20_000:
        raise HTTPException(413, "code too long for a fast memory check (20k chars max)")
    return custom_pr(body.file_name or "", body.code)


@router.post("/analyze")
def analyze(body: CheckIn) -> dict:
    """Read changed files + detect code patterns (no Hindsight)."""
    p = resolve(body)
    signals = detect_signals(p.files)
    return {"pr": p.number, "title": p.title,
            "files": [{"path": f.path, "status": f.status, "added": len(f.added),
                       "lines": [{"n": n, "kind": k, "text": t} for n, k, t in f.lines]} for f in p.files],
            "signals": signals,
            "probes": [{"name": n, "query": q} for n, q in recall_probes(p.files)]}


@router.post("/memory-recall")
def memory_recall(body: CheckIn) -> dict:
    """The review agent's real recall step (fast); no reflect."""
    p = resolve(body)
    bank = get_settings().demo_bank_id
    t = time.monotonic()
    labelled, probe_names = ReviewAgent(hs=hs(), bank=bank).recall(p)
    return {"bank": bank, "seconds": round(time.monotonic() - t, 2), "probes": probe_names,
            "memories": [{"label": lb, "id": m.id, "type": m.type, "text": m.text,
                          "document_id": m.document_id, "sources": srcs} for lb, m, srcs in labelled]}


@router.get("/recall")
def recall(q: str, types: str | None = None) -> dict:
    """Live recall against the demo bank (fast; RRF ranking)."""
    s = get_settings()
    t = time.monotonic()
    mems = hs().recall(s.demo_bank_id, q, types=types.split(",") if types else None,
                       budget="low", max_tokens=1500)
    return {"bank": s.demo_bank_id, "query": q, "seconds": round(time.monotonic() - t, 2),
            "memories": [{"type": m.type, "text": m.text, "document_id": m.document_id,
                          "metadata": m.metadata} for m in mems[:8]]}
