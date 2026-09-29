"""Accept or reject a finding from a saved review; the feedback is retained in Hindsight.

    .venv/Scripts/python scripts/feedback_review.py --pr 137 --list
    .venv/Scripts/python scripts/feedback_review.py --pr 137 --finding F1 --decision REJECTED \
        --reason "Approved exception for this integration." --developer erin

Feedback goes to the demo bank (clone of the historical bank), never to the baseline bank.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.feedback.models import build_feedback, findings_of, load_review  # noqa: E402
from app.feedback.store import FeedbackStore  # noqa: E402
from app.hindsight import HindsightService  # noqa: E402

REVIEWS = BACKEND_DIR.parent / "evaluation" / "results" / "reviews"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--review", help="review file (default: reviews/pr-<n>-memory.json)")
    ap.add_argument("--list", action="store_true", help="list findings and their ids")
    ap.add_argument("--finding")
    ap.add_argument("--decision", choices=["ACCEPTED", "REJECTED"])
    ap.add_argument("--reason", default="")
    ap.add_argument("--developer", default="developer")
    ap.add_argument("--bank", default=None, help="default: the demo bank")
    args = ap.parse_args()

    review_path = Path(args.review) if args.review else REVIEWS / f"pr-{args.pr}-memory.json"
    review = load_review(review_path)
    if int(review["pr"]) != args.pr:
        print(f"{review_path} is a review of PR #{review['pr']}, not #{args.pr}")
        return 2
    if args.list or not args.finding:
        for f in findings_of(review):
            print(f"{f.finding_id:<4} {f.classification:<23} {f.file}:{f.line}  {f.title}")
        return 0
    if not args.decision:
        ap.error("--decision is required with --finding")

    fb = build_feedback(review, review_path.relative_to(BACKEND_DIR.parent).as_posix(),
                        args.finding, args.decision, args.reason, args.developer)
    hs = HindsightService()
    store = FeedbackStore(hs, bank=args.bank)
    if store.ensure_bank():
        print(f"created demo bank {store.bank} as a clone of {get_settings().team_bank_id}")
    t = time.monotonic()
    result = store.submit(fb)
    result["seconds"] = round(time.monotonic() - t, 1)
    result["bank"] = store.bank
    print(json.dumps(result, indent=2))
    for m in store.stored_units(fb):
        print(f"  stored {m.type:<11} {m.text[:160]}")
    hs.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
