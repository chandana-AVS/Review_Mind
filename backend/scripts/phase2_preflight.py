"""Inspect the dataset + bank before bulk history ingestion. Exit 1 (abort) on anything unexpected.

    .venv/Scripts/python scripts/phase2_preflight.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.hindsight import HindsightService  # noqa: E402
from app.ingestion.history import DEMO_DATA, FIRST_HELD_OUT_PR, HISTORY_PRS, LeakError, assert_history  # noqa: E402

EXPECTED_PILOT = {104, 112, 113}


def main() -> int:
    settings = get_settings()
    bank = settings.team_bank_id
    pr_dir = DEMO_DATA / "pull-requests" / "payments-service"
    records = {}
    for path in sorted(pr_dir.glob("pr-*.json")):
        pr = json.loads(path.read_text(encoding="utf-8"))
        records[pr["number"]] = pr.get("split")

    history = sorted(n for n, s in records.items() if s == "history")
    feedback = sorted(n for n, s in records.items() if s == "feedback")
    test = sorted(n for n, s in records.items() if s == "test")
    other = sorted(n for n, s in records.items() if s not in ("history", "feedback", "test"))
    claims_history_but_held_out = sorted(n for n in history if n >= FIRST_HELD_OUT_PR)

    # What --all would select, run through the real guard.
    selected, rejected = [], []
    for n in HISTORY_PRS:
        try:
            assert_history(n, records.get(n))
            selected.append(n)
        except LeakError:
            rejected.append(n)
    for n in sorted(records):
        if n not in HISTORY_PRS:
            try:
                assert_history(n, records[n])
                selected.append(n)  # would be a guard failure
            except LeakError:
                rejected.append(n)

    state_path = BACKEND_DIR / ".state" / f"ingest-{bank}.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    state_prs = sorted(v["pr"] for v in state.values())

    hs = HindsightService()
    bank_docs = sorted({m.document_id for m in hs.list_memories(bank, limit=2000) if m.document_id})
    hs.close()

    remaining = sorted(set(selected) - set(state_prs))
    report = {
        "total_pr_files": len(records), "history": history, "feedback": feedback, "test": test,
        "no_or_unknown_split": other, "history_claims_ge_131": claims_history_but_held_out,
        "selected_by_guard": sorted(selected), "rejected_by_guard": sorted(rejected),
        "already_ingested_state": state_prs, "bank_document_ids": bank_docs, "remaining": remaining,
    }
    for k, v in report.items():
        print(f"{k:<24} {len(v) if isinstance(v, list) else v:>3}  {v if isinstance(v, list) and len(v) <= 30 else ''}")

    problems = []
    if len(history) != 30 or history != list(HISTORY_PRS):
        problems.append(f"history PRs are {history}, expected 101-130")
    if sorted(selected) != list(HISTORY_PRS):
        problems.append("guard selection differs from 101-130")
    if any(n >= FIRST_HELD_OUT_PR for n in selected):
        problems.append("a PR >= 131 would be selected")
    if set(selected) & (set(feedback) | set(test)):
        problems.append("a feedback/test PR would be selected")
    if set(state_prs) != EXPECTED_PILOT:
        problems.append(f"already-ingested state is {state_prs}, expected {sorted(EXPECTED_PILOT)}")
    if bank_docs != sorted(f"pr:payments-service:{n}" for n in EXPECTED_PILOT):
        problems.append(f"bank documents are {bank_docs}")
    if len(remaining) != 27:
        problems.append(f"remaining is {len(remaining)}, expected 27")
    if problems:
        print("\nABORT:\n  - " + "\n  - ".join(problems))
        return 1
    print("\nPREFLIGHT OK: 30 history, 3 already ingested, 27 remaining, 0 feedback/test/>=131 selected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
