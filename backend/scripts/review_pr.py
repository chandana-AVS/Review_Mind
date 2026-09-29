"""Review one PR from the dataset with ReviewMind.

    .venv/Scripts/python scripts/review_pr.py 137              # with Hindsight team memory
    .venv/Scripts/python scripts/review_pr.py 137 --no-memory  # same reviewer, empty bank

Writes evaluation/results/reviews/pr-<n>-<memory|generic>.json. Never writes to memory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.review.agent import ReviewAgent  # noqa: E402
from app.review.diff import load_pr  # noqa: E402

OUT_DIR = BACKEND_DIR.parent / "evaluation" / "results" / "reviews"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("pr_pos", nargs="?", type=int, metavar="PR")
    ap.add_argument("--pr", type=int, dest="pr_opt")
    ap.add_argument("--no-memory", action="store_true")
    ap.add_argument("--bank", default=None,
                    help=f"Hindsight bank to review against (default: {get_settings().team_bank_id})")
    args = ap.parse_args(argv)
    args.pr = args.pr_opt if args.pr_opt is not None else args.pr_pos
    if args.pr is None:
        ap.error("a PR number is required (positional or --pr)")
    args.bank = args.bank or get_settings().team_bank_id
    return args


def output_path(pr_number: int, use_memory: bool, bank: str) -> Path:
    name = f"pr-{pr_number}-{'memory' if use_memory else 'generic'}"
    if use_memory and bank != get_settings().team_bank_id:
        name += f"-{bank}"  # never overwrite the baseline review that feedback refers to
    return OUT_DIR / f"{name}.json"


def main() -> int:
    args = parse_args()
    pr = load_pr(args.pr)
    print(f"BANK: {args.bank}", flush=True)
    agent = ReviewAgent(bank=args.bank)
    result = agent.review(pr, use_memory=not args.no_memory)
    agent.hs.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = output_path(pr.number, result.use_memory, args.bank)
    out.write_text(json.dumps(result.to_dict(), indent=2, default=str) + "\n", encoding="utf-8")

    print(f"PR #{pr.number} {pr.title}  [{'Hindsight memory' if result.use_memory else 'NO memory'}]")
    print(f"timings: {result.seconds}  recalled memories: {result.memories_recalled}  probes: {result.recall_probes}")
    if result.team_brief:
        print("\nTEAM BRIEF")
        for b in result.team_brief:
            srcs = sorted({s for e in b["evidence"] for s in e["sources"]})
            print(f"  - {b['rule']}  {srcs}")
    print("\nFINDINGS")
    for f in result.findings:
        tag = "MEMORY " if f.basis == "hindsight_memory" else "GENERIC"
        print(f"  [{tag}] {f.classification:<22} {f.severity:<6} {f.file}:{f.line}  {f.title}")
        if f.basis == "hindsight_memory":
            print(f"           sources: {', '.join(f.sources) or '-'}; evidence: "
                  + "; ".join(f"{e.label}({e.type}) {e.text[:90]}" for e in f.memory_evidence[:2]))
    if result.suppressed:
        print("\nSUPPRESSED (team previously rejected / exception)")
        for s in result.suppressed:
            print(f"  - {s.get('file')}:{s.get('line')} {s.get('suggestion')}  {s.get('sources')}")
    if result.dropped_unsupported:
        print(f"\nDROPPED {len(result.dropped_unsupported)} team claim(s) without memory evidence")
    print(f"\nreport: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
