"""Ingest historical PRs into the team's Hindsight bank: one narrated document per PR.

    cd backend
    .venv/Scripts/python scripts/ingest_history.py --prs 101,104,113     # pilot
    .venv/Scripts/python scripts/ingest_history.py --all                 # all 30 history PRs

- Only history PRs #101-#130 can be ingested (hard leak guard in app.ingestion.history).
- Idempotent: document_id = pr:payments-service:<n> (Hindsight replaces on re-retain), and a
  content-hash state file skips unchanged PRs without calling Hindsight at all. --force re-retains.
- Resumable: state is written after every successful PR.
- Automatic consolidation is disabled on the bank during ingestion; run it once afterwards.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.hindsight import HindsightError, HindsightService  # noqa: E402
from app.hindsight.bank_setup import configure_team_bank  # noqa: E402
from app.ingestion.history import FIRST_HELD_OUT_PR, HISTORY_PRS, PRDocument, load_history_prs  # noqa: E402

STATE_DIR = BACKEND_DIR / ".state"
RESULTS_DIR = BACKEND_DIR.parent / "evaluation" / "results"

_local = threading.local()
_state_lock = threading.Lock()


def thread_client() -> HindsightService:
    # The sync client binds to a per-thread event loop, so each worker thread gets its own.
    if not hasattr(_local, "hs"):
        _local.hs = HindsightService()
    return _local.hs


def load_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def retain_one(bank: str, doc: PRDocument, t0: float) -> dict:
    assert doc.number < FIRST_HELD_OUT_PR and doc.metadata["split"] == "history"
    start = time.monotonic() - t0
    try:
        result = thread_client().retain(
            bank, doc.content, context=doc.context, document_id=doc.document_id,
            tags=doc.tags, metadata=doc.metadata, timestamp=doc.timestamp,
        )
        ok, error = bool(result["success"]), None
    except HindsightError as exc:
        ok, error = False, f"{type(exc).__name__}: {exc}"
    end = time.monotonic() - t0
    return {"pr": doc.number, "document_id": doc.document_id, "ok": ok, "error": error,
            "start_s": round(start, 1), "end_s": round(end, 1), "seconds": round(end - start, 1),
            "content_chars": len(doc.content), "threads": len(doc.thread_ids)}


def max_overlap(runs: list[dict]) -> int:
    events = sorted([(r["start_s"], 1) for r in runs] + [(r["end_s"], -1) for r in runs],
                    key=lambda e: (e[0], e[1]))
    cur = best = 0
    for _, delta in events:
        cur += delta
        best = max(best, cur)
    return best


def memory_stats(hs: HindsightService, bank: str, doc_ids: set[str]) -> dict:
    items = hs.list_memories(bank, limit=1000)
    by_doc: dict[str, Counter] = defaultdict(Counter)
    for m in items:
        by_doc[m.document_id or "(none)"][m.type or "?"] += 1
    sample = next((m for m in items if m.document_id in doc_ids), None)
    return {
        "total_memory_units": len(items),
        "types_total": dict(Counter(m.type or "?" for m in items)),
        "by_document": {k: dict(v) for k, v in sorted(by_doc.items())},
        "observations": sum(1 for m in items if m.type == "observation"),
        "provenance_sample": None if sample is None else {
            "text": sample.text, "type": sample.type, "document_id": sample.document_id,
            "tags": sample.tags, "metadata": sample.metadata,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--prs", help="comma-separated history PR numbers, e.g. 101,104,113")
    group.add_argument("--all", action="store_true", help=f"all history PRs {HISTORY_PRS.start}-{HISTORY_PRS.stop - 1}")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--force", action="store_true", help="re-retain even if content hash is unchanged")
    ap.add_argument("--bank", default=None)
    ap.add_argument("--report", default=None, help="report file name under evaluation/results/")
    args = ap.parse_args()

    settings = get_settings()
    bank = args.bank or settings.team_bank_id
    numbers = list(HISTORY_PRS) if args.all else [int(x) for x in args.prs.split(",")]
    docs = load_history_prs(numbers)  # raises LeakError for anything outside #101-#130

    state_path = STATE_DIR / f"ingest-{bank}.json"
    state = load_state(state_path)
    todo = [d for d in docs if args.force or state.get(d.document_id, {}).get("hash") != d.content_hash]
    skipped = [d.number for d in docs if d not in todo]

    hs = HindsightService()
    configure_team_bank(hs, bank, auto_consolidation=False)
    auto = hs.get_bank_config(bank)
    auto_flag = (auto.get("config") or auto).get("enable_auto_consolidation")

    print(f"bank={bank} mode={settings.hindsight_mode} auto_consolidation={auto_flag} "
          f"to_retain={[d.number for d in todo]} skipped_unchanged={skipped}")
    t0 = time.monotonic()
    runs: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = {pool.submit(retain_one, bank, d, t0): d for d in todo}
        for fut in as_completed(futures):
            doc, run = futures[fut], fut.result()
            runs.append(run)
            status = "ok" if run["ok"] else f"FAILED {run['error']}"
            print(f"  PR #{run['pr']:<4} {run['seconds']:6.1f}s  {status}", flush=True)
            if run["ok"]:
                with _state_lock:
                    state[doc.document_id] = {"hash": doc.content_hash, "pr": doc.number,
                                              "retained_at": datetime.now(UTC).isoformat(),
                                              "seconds": run["seconds"]}
                    save_state(state_path, state)
    wall = round(time.monotonic() - t0, 1)

    report = {
        "ran_at": datetime.now(UTC).isoformat(), "bank": bank, "mode": settings.hindsight_mode,
        "requested": numbers, "retained": sorted(r["pr"] for r in runs if r["ok"]),
        "failed": sorted(r["pr"] for r in runs if not r["ok"]), "skipped_unchanged": skipped,
        "concurrency_limit": args.concurrency, "max_concurrency_observed": max_overlap(runs) if runs else 0,
        "wall_seconds": wall, "runs": sorted(runs, key=lambda r: r["pr"]),
        "auto_consolidation_enabled": auto_flag,
        "memory": memory_stats(hs, bank, {d.document_id for d in docs}),
    }
    hs.close()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / (args.report or f"ingest-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"wall={wall}s retained={report['retained']} failed={report['failed']} "
          f"max_concurrency={report['max_concurrency_observed']} "
          f"memory_units={report['memory']['total_memory_units']} "
          f"observations={report['memory']['observations']}")
    print(f"report: {out}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
