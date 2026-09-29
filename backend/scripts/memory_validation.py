"""Memory validation check (NOT the evaluation): do targeted recalls return grounded history?

    .venv/Scripts/python scripts/memory_validation.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.hindsight import HindsightService  # noqa: E402

RESULTS = BACKEND_DIR.parent / "evaluation" / "results"

CHECKS = [
    {"query": "How should HTTP clients be created in payments-service?",
     "expect_all": ["make_client"],
     "expect_any": [["requests", "httpx"], ["timeout", "request-id", "request id", "metrics"]],
     "expect_docs": ["pr:payments-service:113", "adr:payments-service:006", "pr:payments-service:110"]},
    {"query": "Should we add another Tenacity retry layer around fraud scoring?",
     "expect_all": ["retr"],
     "expect_any": [["inc-12", "retry storm"], ["make_client", "shared client"]],
     "expect_docs": ["pr:payments-service:113"]},
    {"query": "How should money amounts be represented?",
     "expect_all": [],
     "expect_any": [["adr-007"], ["minor units", "amount_cents"], ["decimal"]],
     "expect_docs": ["pr:payments-service:112", "adr:payments-service:007"]},
    {"query": "Should private helper functions always have docstrings?",
     "expect_all": ["docstring"],
     "expect_any": [["private helper"], ["public service", "public"]],
     "expect_docs": ["pr:payments-service:104"]},
]


def main() -> int:
    bank = get_settings().team_bank_id
    hs = HindsightService()
    results, all_ok = [], True
    for check in CHECKS:
        mems = hs.recall(bank, check["query"], max_tokens=2500, budget="mid")[:10]
        text = " ".join(m.text.lower() for m in mems)
        docs = sorted({m.document_id for m in mems if m.document_id})
        ok_all = all(k in text for k in check["expect_all"])
        ok_any = all(any(k in text for k in group) for group in check["expect_any"])
        ok_docs = any(d in docs for d in check["expect_docs"])
        grounded = ok_all and ok_any and ok_docs
        all_ok &= grounded
        results.append({"query": check["query"], "grounded": grounded,
                        "keyword_checks": {"all": ok_all, "any_groups": ok_any, "expected_source_doc": ok_docs},
                        "document_ids": docs,
                        "memories": [{"type": m.type, "document_id": m.document_id, "text": m.text} for m in mems]})
        print(f"\n[{'GROUNDED' if grounded else 'NOT GROUNDED'}] {check['query']}")
        for m in mems[:6]:
            print(f"   {m.type:<11} {str(m.document_id):<30} {m.text[:150]}")
    hs.close()
    (RESULTS / "memory-validation.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"\n{sum(r['grounded'] for r in results)}/{len(results)} checks grounded")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
