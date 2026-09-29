"""Verify the historical knowledge base in Hindsight: completeness, provenance, leak guard.

    .venv/Scripts/python scripts/phase2_verify.py [--expect-arch] [--out phase2-verification.json]

Checks are made against what is actually IN Hindsight (document ids, metadata, text),
not against what the ingestion script intended to send.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.hindsight import HindsightService  # noqa: E402
from app.ingestion.history import DEMO_DATA, FIRST_HELD_OUT_PR, HISTORY_PRS  # noqa: E402

RESULTS = BACKEND_DIR.parent / "evaluation" / "results"
EXPECTED_ARCH = {f"adr:payments-service:{n:03d}" for n in range(3, 9)} | {"contributing:payments-service"}
PR_DOC = re.compile(r"^pr:payments-service:(\d+)$")
HELD_OUT_REF = re.compile(r"(?:PR\s*)?#(13[1-9]|14[0-4])\b")
PR_KEYS = ["team", "repo", "source", "split", "pr_number", "thread_ids", "created_at", "merged_at",
           "outcomes", "state"]


def all_memories(hs: HindsightService, bank: str) -> list:
    items, offset = [], 0
    while True:
        resp = hs._call("list_memories", lambda: hs._client.list_memories(bank, limit=500, offset=offset),
                        idempotent=True)
        batch = resp.items or []
        items.extend(batch)
        offset += len(batch)
        if not batch or offset >= (resp.total or 0):
            return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect-arch", action="store_true")
    ap.add_argument("--out", default="phase2-verification.json")
    args = ap.parse_args()

    bank = get_settings().team_bank_id
    hs = HindsightService()
    raw = all_memories(hs, bank)
    cfg = hs.get_bank_config(bank)
    auto = (cfg.get("config") or cfg).get("enable_auto_consolidation")
    hs.close()

    by_doc: dict[str, Counter] = defaultdict(Counter)
    types = Counter()
    problems: list[str] = []
    leak_violations: list[str] = []
    for m in raw:
        doc, typ, meta = m.document_id, m.fact_type, dict(m.metadata or {})
        types[typ] += 1
        by_doc[doc or "(no document: consolidated)"][typ] += 1
        if HELD_OUT_REF.search(m.text or ""):
            leak_violations.append(f"unit {m.id} text references a held-out PR: {m.text[:120]}")
        if doc is None:
            continue
        pm = PR_DOC.match(doc)
        if pm:
            n = int(pm.group(1))
            if n >= FIRST_HELD_OUT_PR or n not in HISTORY_PRS:
                leak_violations.append(f"document {doc} is not a history PR")
            missing = [k for k in PR_KEYS if k not in meta]
            if missing:
                problems.append(f"{doc} unit {m.id} missing metadata {missing}")
            if meta.get("pr_number") != str(n):
                problems.append(f"{doc} metadata pr_number={meta.get('pr_number')}")
            if meta.get("split") != "history" or meta.get("source") != "pr_history":
                leak_violations.append(f"{doc} split={meta.get('split')} source={meta.get('source')}")
            if meta.get("team") != "acme-payments" or meta.get("repo") != "payments-service":
                problems.append(f"{doc} team/repo metadata wrong")
            if not meta.get("thread_ids") or not meta.get("outcomes") or not meta.get("created_at"):
                problems.append(f"{doc} empty thread_ids/outcomes/created_at")
            if meta.get("state") == "merged" and not meta.get("merged_at"):
                problems.append(f"{doc} merged but merged_at empty")
        elif doc in EXPECTED_ARCH:
            if meta.get("source") != "architecture_docs" or meta.get("split") != "history":
                problems.append(f"{doc} architecture metadata wrong: {meta}")
        else:
            leak_violations.append(f"unexpected document id {doc}")

    pr_docs = sorted(int(PR_DOC.match(d).group(1)) for d in by_doc if d and PR_DOC.match(d))
    arch_docs = sorted(d for d in by_doc if d in EXPECTED_ARCH)
    if pr_docs != list(HISTORY_PRS):
        problems.append(f"PR documents present: {pr_docs} (missing {sorted(set(HISTORY_PRS) - set(pr_docs))})")
    if args.expect_arch and set(arch_docs) != EXPECTED_ARCH:
        problems.append(f"architecture documents present: {arch_docs}")

    # Held-out titles must not appear anywhere in memory.
    held_out_titles = []
    for n in range(FIRST_HELD_OUT_PR, 145):
        pr = json.loads((DEMO_DATA / "pull-requests" / "payments-service" / f"pr-{n}.json").read_text(encoding="utf-8"))
        held_out_titles.append((n, pr["split"], pr["title"]))
    for n, split, title in held_out_titles:
        if any(title.lower() in (m.text or "").lower() for m in raw):
            leak_violations.append(f"{split} PR #{n} title found in memory: {title}")

    # Ingestion timing from the run reports.
    runs = {}
    for name in ("ingest-pilot.json", "ingest-history-all.json"):
        path = RESULTS / name
        if path.exists():
            for r in json.loads(path.read_text(encoding="utf-8"))["runs"]:
                if r["ok"]:
                    runs[r["pr"]] = r["seconds"]
    secs = list(runs.values())
    sample = next((m for m in raw if m.document_id == "pr:payments-service:113"), None)

    report = {
        "bank": bank,
        "auto_consolidation_enabled": auto,
        "total_memory_units": len(raw),
        "memory_units_by_type": dict(types),
        "memory_units_by_document": {k: dict(v) for k, v in sorted(by_doc.items(), key=lambda kv: str(kv[0]))},
        "observations": types.get("observation", 0),
        "history_pr_documents": pr_docs,
        "history_pr_document_count": len(pr_docs),
        "architecture_documents": arch_docs,
        "per_pr_retain_seconds": dict(sorted(runs.items())),
        "retain_seconds_avg": round(statistics.mean(secs), 1) if secs else None,
        "slowest_pr": max(runs, key=runs.get) if runs else None,
        "fastest_pr": min(runs, key=runs.get) if runs else None,
        "held_out_checked": [f"#{n} ({s})" for n, s, _ in held_out_titles],
        "leak_guard_violations": leak_violations,
        "problems": problems,
        "provenance_sample": None if sample is None else {
            "text": sample.text, "type": sample.fact_type, "document_id": sample.document_id,
            "tags": sample.tags, "metadata": sample.metadata},
    }
    (RESULTS / args.out).write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"auto_consolidation={auto} units={len(raw)} types={dict(types)}")
    print(f"history PR documents: {len(pr_docs)}/30  architecture documents: {len(arch_docs)}/7")
    print(f"retain avg={report['retain_seconds_avg']}s slowest=#{report['slowest_pr']} fastest=#{report['fastest_pr']}")
    print(f"leak violations: {len(leak_violations)}  provenance/completeness problems: {len(problems)}")
    for p in (leak_violations + problems)[:20]:
        print("  -", p)
    print(f"report: {RESULTS / args.out}")
    return 1 if (leak_violations or problems) else 0


if __name__ == "__main__":
    sys.exit(main())
