"""Assemble evaluation/benchmark.json from the per-PR answer keys in evaluation/labels/.

Run after validate_dataset.py passes:
    python evaluation/build_benchmark.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRS = ROOT / "demo-data" / "pull-requests" / "payments-service"
LABELS = ROOT / "evaluation" / "labels"
OUT = ROOT / "evaluation" / "benchmark.json"

TAXONOMY = {
    "conventions": {
        "C1": "Money is int minor units (*_cents); no Decimal/float (ADR-007)",
        "C2": "API handlers never open DB sessions or query; call services (ADR-003)",
        "C3": "Outbound HTTP only via app.clients.http.make_client (ADR-006)",
        "C4": "Money-moving POST endpoints use @idempotent (ADR-004)",
        "C5": "structlog snake_case events with kwargs; no f-strings; PII via redact()",
        "C6": "Services raise app.errors domain errors; HTTPException only in app/api",
        "C7": "Time from app.utils.clock.now(); never datetime.now()/utcnow()",
        "C8": "Tests named test_<unit>__<scenario>__<expected>; use tests/factories.py",
        "C9": "Expand/contract migrations; no drop/rename with code change (ADR-008)",
        "C10": "Config via app.config.settings; no os.environ in app/",
    },
    "noise": {
        "N1": "Docstrings on private helpers",
        "N2": "Type hints in tests",
        "N3": "Import order / formatting nits",
        "N4": "Use Decimal for money",
        "N5": "Add retry/backoff around client calls",
        "N6": "Extract magic numbers in tests into constants",
        "N7": "Wrap service calls in try/except that logs and re-raises",
    },
    "exceptions": {
        "E1": "Health/metrics endpoints may touch DB/Redis directly",
        "E2": "Stripe webhook receiver dedupes on event id; no @idempotent",
        "E3": "scripts/ one-offs may use print and os.environ",
    },
    "conflicts": {
        "K1": "Money: Decimal (Jan-Feb) superseded by int minor units (ADR-007, Mar)",
        "K2": "HTTP: requests (Jan) superseded by shared client (ADR-006, Feb)",
        "K3": "Pydantic in services: contested, resolved by ADR-005",
        "K4": "One reviewer's '<20 lines per function' opinion: not a rule",
    },
}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def held_out(split: str, numbers: range) -> list[dict]:
    items = []
    for n in numbers:
        pr = load(PRS / f"pr-{n}.json")
        label = load(LABELS / split / f"pr-{n}.json")
        items.append({
            "pr": n,
            "title": pr["title"],
            "author": pr["author"],
            "created_at": pr["created_at"],
            "demo": label.get("demo", False),
            "should_flag": label["should_flag"],
            "should_not_flag": label["should_not_flag"],
            "drift_trap": label.get("drift_trap"),
        })
    return items


def main() -> None:
    history = []
    for n in range(101, 131):
        pr = load(PRS / f"pr-{n}.json")
        history.append({"pr": n, "created_at": pr["created_at"], "author": pr["author"],
                        "adr": pr["adr"], "thread_labels": load(LABELS / "history" / f"pr-{n}.json")["threads"]})

    benchmark = {
        "version": 1,
        "team": "acme-payments",
        "repository": "payments-service",
        "taxonomy": TAXONOMY,
        "configs": {
            "A": "Generic reviewer, no memory",
            "B": "Naive RAG over docs + history threads",
            "C": "ReviewMind after history ingestion",
            "D": "ReviewMind after k simulated feedback events",
        },
        "feedback_steps": [0, 5, 10, 20],
        "simulated_developer": {
            "seed": 42,
            "flip_rate": 0.10,
            "line_tolerance": 3,
            "policy": "accept should_flag matches; reject should_not_flag matches with reject_reason; ignore others",
        },
        "history": history,
        "feedback": held_out("feedback", range(131, 137)),
        "test": held_out("test", range(137, 145)),
    }
    OUT.write_text(json.dumps(benchmark, indent=2) + "\n", encoding="utf-8")
    n_threads = sum(len(h["thread_labels"]) for h in history)
    n_flag = sum(len(t["should_flag"]) for t in benchmark["test"])
    n_trap = sum(len(t["should_not_flag"]) for t in benchmark["test"])
    print(f"wrote {OUT.relative_to(ROOT)}: {len(history)} history PRs ({n_threads} threads), "
          f"{len(benchmark['feedback'])} feedback PRs, {len(benchmark['test'])} test PRs "
          f"({n_flag} should_flag, {n_trap} should_not_flag)")


if __name__ == "__main__":
    main()
