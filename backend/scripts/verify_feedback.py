"""Verify REVIEW -> REJECT -> RETAIN -> RECALL against the live demo bank.

    .venv/Scripts/python scripts/verify_feedback.py --pr 137 --finding F1

Prints the stored feedback memory units with provenance, then recalls them with a natural
question (no document-id lookup), and checks the baseline bank did NOT receive the feedback.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.feedback.models import findings_of, load_review  # noqa: E402
from app.hindsight import HindsightService  # noqa: E402

REVIEWS = BACKEND_DIR.parent / "evaluation" / "results" / "reviews"
RESULTS = BACKEND_DIR.parent / "evaluation" / "results"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--finding", required=True)
    args = ap.parse_args()

    settings = get_settings()
    review = load_review(REVIEWS / f"pr-{args.pr}-memory.json")
    finding = next(f for f in findings_of(review) if f.finding_id == args.finding)
    doc_id = f"feedback:payments-service:pr{args.pr}:{args.finding}"
    hs = HindsightService()

    stored = [m for m in hs.list_memories(settings.demo_bank_id, limit=2000) if m.document_id == doc_id]
    print(f"STORED in {settings.demo_bank_id} under {doc_id}: {len(stored)} unit(s)")
    for m in stored:
        print(f"  [{m.type}] {m.text}")
    if stored:
        print("  provenance metadata:", json.dumps(stored[0].metadata, indent=2))
        print("  tags:", stored[0].tags)

    query = f"What did developers say about ReviewMind's finding '{finding.title}' in {finding.file}?"
    recalled = hs.recall(settings.demo_bank_id, query, budget="low", max_tokens=1500)
    hits = [m for m in recalled if m.document_id == doc_id]
    print(f"\nRECALL: {query}")
    for m in recalled[:6]:
        mark = "<- feedback" if m.document_id == doc_id else ""
        print(f"  [{m.type:<11}] {str(m.document_id):<42} {m.text[:120]} {mark}")

    exp = hs.recall(settings.demo_bank_id, query, types=["experience"], budget="low", max_tokens=1500)
    exp_hits = [m for m in exp if m.document_id == doc_id]

    baseline_leak = [m for m in hs.list_memories(settings.team_bank_id, limit=2000)
                     if (m.document_id or "").startswith("feedback:")]
    hs.close()

    checks = {
        "stored": bool(stored),
        "stored_as_experience": any(m.type == "experience" for m in stored),
        "provenance_complete": bool(stored) and all(
            stored[0].metadata.get(k) for k in ("pr_number", "finding_id", "decision", "reason",
                                                "review_file", "source")),
        "recalled_by_question": bool(hits),
        "recalled_with_types_experience": bool(exp_hits),
        "baseline_bank_untouched": not baseline_leak,
    }
    print("\nCHECKS:", json.dumps(checks, indent=2))
    (RESULTS / f"feedback-verification-pr{args.pr}-{args.finding}.json").write_text(json.dumps({
        "document_id": doc_id, "checks": checks,
        "stored": [{"type": m.type, "text": m.text, "metadata": m.metadata, "tags": m.tags} for m in stored],
        "recalled": [{"type": m.type, "document_id": m.document_id, "text": m.text} for m in recalled],
    }, indent=2) + "\n", encoding="utf-8")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
