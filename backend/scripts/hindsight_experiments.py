"""Phase 1 experiments against a live Hindsight: observe, don't assume.

E1  Re-retaining the same document_id with different content: replaced, duplicated, or merged?
E2  How are ReviewMind's own actions classified (experience vs world) under different phrasings?

Writes evaluation/results/hindsight_phase1.json. Uses throwaway banks that are deleted afterwards
(pass --keep to keep them for inspection in the Hindsight UI).

    cd backend && python scripts/hindsight_experiments.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.hindsight import HindsightService  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "evaluation" / "results" / "hindsight_phase1.json"

AGENT_MISSION = (
    "I am ReviewMind, the AI code reviewer for the Acme Payments team. I remember the team's "
    "conventions and my own past review findings."
)


def facts(hs: HindsightService, bank: str, document_id: str | None = None) -> list[dict]:
    items = hs.list_memories(bank, limit=200)
    return [
        {"type": m.type, "text": m.text, "document_id": m.document_id}
        for m in items
        if document_id is None or m.document_id == document_id
    ]


def e1_same_document_id(hs: HindsightService, bank: str) -> dict:
    doc = "e1-money-rule"
    v1 = "Acme Payments stores money amounts as Decimal values (decided January 2026)."
    v2 = "Acme Payments stores money amounts as integer minor units, amount_cents (decided March 2026)."

    hs.retain(bank, v1, document_id=doc, context="Team decision recorded by tech lead alice")
    after_v1 = facts(hs, bank, doc)
    hs.retain(bank, v2, document_id=doc, context="Team decision recorded by tech lead alice")
    after_v2 = facts(hs, bank, doc)
    recalled = hs.recall(bank, "How does Acme Payments store money amounts?")

    # Control: same content twice with NO document_id.
    ctrl = "Acme Payments requires an Idempotency-Key header on money-moving POST endpoints."
    hs.retain(bank, ctrl, context="Team decision recorded by tech lead alice")
    hs.retain(bank, ctrl, context="Team decision recorded by tech lead alice")
    ctrl_facts = [f for f in facts(hs, bank) if "idempotency" in f["text"].lower()]

    mentions_decimal = [f for f in after_v2 if "decimal" in f["text"].lower()]
    return {
        "question": "Does re-retaining the same document_id replace the previous memories?",
        "facts_after_v1": after_v1,
        "facts_after_v2": after_v2,
        "recall_after_v2": [{"type": m.type, "text": m.text, "document_id": m.document_id} for m in recalled],
        "v1_facts_still_present_for_document": bool(mentions_decimal),
        "verdict": "REPLACED" if after_v2 and not mentions_decimal else (
            "NOT REPLACED (old facts remain)" if mentions_decimal else "UNCLEAR (no facts for document)"),
        "control_no_document_id": {
            "facts_mentioning_idempotency": ctrl_facts,
            "count": len(ctrl_facts),
        },
    }


E2_VARIANTS = {
    "agent_first_person_with_speaker_context": dict(
        content="I flagged app/api/refunds.py line 88 in PR #57 of payments-service: the handler uses "
                "float for a currency amount. I recommended integer minor units per ADR-007.",
        context="ReviewMind's own review log. The speaker is ReviewMind, this bank's agent, "
                "describing a finding it produced.",
    ),
    "agent_third_person_no_context": dict(
        content="ReviewMind flagged app/api/refunds.py line 88 in PR #57 of payments-service because "
                "the handler uses float for a currency amount.",
        context=None,
    ),
    "human_reviewer_first_person": dict(
        content="I asked Dan to move the amount validation into RefundService in PR #42.",
        context="Code review comment written by alice, the Acme Payments tech lead (a human reviewer).",
    ),
    "developer_feedback_on_agent": dict(
        content="Carol rejected my suggestion to add a docstring to the private helper _to_cents in "
                "PR #57, saying private helpers don't need docstrings.",
        context="ReviewMind's own log of developer feedback on its findings. The speaker is "
                "ReviewMind, this bank's agent.",
    ),
}


def e2_experience_classification(hs: HindsightService, bank: str) -> dict:
    results = {}
    for name, v in E2_VARIANTS.items():
        doc = f"e2-{name}"
        hs.retain(bank, v["content"], context=v["context"], document_id=doc)
        results[name] = {"input": v, "facts": facts(hs, bank, doc)}
    exp = hs.recall(bank, "What did ReviewMind flag or get feedback on?", types=["experience"])
    world = hs.recall(bank, "What did ReviewMind flag or get feedback on?", types=["world"])
    summary = {
        name: sorted({f["type"] for f in r["facts"]}) for name, r in results.items()
    }
    return {
        "question": "How are ReviewMind's own actions classified?",
        "types_by_variant": summary,
        "variants": results,
        "recall_types_experience": [{"text": m.text, "document_id": m.document_id} for m in exp],
        "recall_types_world": [{"text": m.text, "document_id": m.document_id} for m in world],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the experiment banks")
    args = ap.parse_args()

    settings = get_settings()
    hs = HindsightService()
    report: dict = {"ran_at": datetime.now(UTC).isoformat(), "mode": settings.hindsight_mode,
                    "base_url": settings.hindsight_base_url}
    report["health"] = hs.health()["server"]
    run = uuid.uuid4().hex[:8]
    banks = {"e1": f"{settings.hindsight_bank_prefix}-exp-e1-{run}",
             "e2": f"{settings.hindsight_bank_prefix}-exp-e2-{run}"}
    try:
        for bank in banks.values():
            hs.ensure_bank(bank, name="ReviewMind phase-1 experiment", mission=AGENT_MISSION)
        t = time.monotonic()
        report["e1"] = e1_same_document_id(hs, banks["e1"])
        report["e1"]["seconds"] = round(time.monotonic() - t, 1)
        t = time.monotonic()
        report["e2"] = e2_experience_classification(hs, banks["e2"])
        report["e2"]["seconds"] = round(time.monotonic() - t, 1)
    finally:
        if not args.keep:
            for bank in banks.values():
                hs.delete_bank(bank)
        hs.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print("E1 verdict:", report["e1"]["verdict"])
    print("E1 control (no document_id, same content twice):",
          report["e1"]["control_no_document_id"]["count"], "matching facts")
    print("E2 types by variant:")
    for name, types in report["e2"]["types_by_variant"].items():
        print(f"  {name:<42} {types}")
    print(f"full report: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
