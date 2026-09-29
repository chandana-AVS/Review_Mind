"""ONE manual consolidation pass, then the two mental models.

    .venv/Scripts/python scripts/phase2_finalize.py

Guarded by .state/phase2-finalize.json so consolidation is not repeated by accident
(--force-consolidate overrides). Auto-consolidation stays OFF on the bank.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.hindsight import HindsightNotFound, HindsightService  # noqa: E402

RESULTS = BACKEND_DIR.parent / "evaluation" / "results"
STATE = BACKEND_DIR / ".state" / "phase2-finalize.json"

# Narrow queries: the first, broader versions exceeded the 300s reflect wall timeout.
MENTAL_MODELS = [
    {
        "id": "team-review-playbook",
        "name": "Team Review Playbook",
        "source_query": (
            "List the code review rules the Acme Payments team enforces in payments-service: coding "
            "conventions, architectural decisions (ADRs), how HTTP clients and retries must be done, how "
            "money is represented, and when docstrings are expected. One line per rule with its reason "
            "and the PR or ADR it comes from. Only rules found in memory."
        ),
    },
    {
        "id": "noise-profile",
        "name": "Noise Profile",
        "source_query": (
            "List the review suggestions the Acme Payments team rejected or treats as unnecessary, "
            "especially ones that conflict with existing infrastructure or caused incidents. One line "
            "per suggestion type with the team's stated reason and the PR it happened in. Only outcomes "
            "found in memory."
        ),
    },
]


def counts(hs: HindsightService, bank: str) -> dict:
    items = hs.list_memories(bank, limit=2000)
    return {"total": len(items), "by_type": dict(Counter(m.type for m in items))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-consolidate", action="store_true")
    args = ap.parse_args()

    bank = get_settings().team_bank_id
    hs = HindsightService()
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    cfg = hs.get_bank_config(bank)
    report = {"bank": bank, "auto_consolidation_enabled": (cfg.get("config") or cfg).get("enable_auto_consolidation")}

    # --- one consolidation pass -------------------------------------------------------------
    if state.get("consolidation", {}).get("status") == "completed" and not args.force_consolidate:
        print("consolidation already completed; not repeating")
        report["consolidation"] = state["consolidation"]
    else:
        before = counts(hs, bank)
        started = datetime.now(UTC)
        t = time.monotonic()
        op_id = hs.consolidate(bank)
        print(f"consolidation started op={op_id} at {started.isoformat()}", flush=True)
        op = hs.wait_operation(bank, op_id, timeout_s=3600, poll_s=10)
        after = counts(hs, bank)
        report["consolidation"] = {
            "operation_id": op_id, "status": op.get("status"), "error": op.get("error_message"),
            "started_at": started.isoformat(), "ended_at": datetime.now(UTC).isoformat(),
            "duration_s": round(time.monotonic() - t, 1), "before": before, "after": after,
        }
        state["consolidation"] = report["consolidation"]
        STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        print(f"consolidation {op.get('status')} in {report['consolidation']['duration_s']}s; "
              f"units {before} -> {after}", flush=True)
        if op.get("status") != "completed":
            hs.close()
            return 1

    # --- mental models --------------------------------------------------------------------
    report["mental_models"] = {}
    for mm in MENTAL_MODELS:
        existing = None
        try:
            existing = hs.get_mental_model(bank, mm["id"])
        except HindsightNotFound:
            pass
        if existing is not None and existing.content and existing.source_query == mm["source_query"]:
            print(f"mental model {mm['id']} already built; not refreshing")
            report["mental_models"][mm["id"]] = {"status": "exists", "content": existing.content}
            continue
        t = time.monotonic()
        if existing is None:
            created = hs.create_mental_model(bank, name=mm["name"], source_query=mm["source_query"],
                                             model_id=mm["id"], max_tokens=4000)
            op_id = created.operation_id
        else:
            # Record exists (e.g. an earlier refresh timed out): update the query and refresh it.
            hs.update_mental_model(bank, mm["id"], source_query=mm["source_query"], max_tokens=4000)
            refreshed = hs.refresh_mental_model(bank, mm["id"])
            op_id = refreshed.operation_id
        print(f"mental model {mm['id']}: building (op={op_id})", flush=True)
        op = hs.wait_operation(bank, op_id, timeout_s=2400, poll_s=10)
        model = hs.get_mental_model(bank, mm["id"])
        report["mental_models"][mm["id"]] = {
            "status": op.get("status"), "error": op.get("error_message"),
            "seconds": round(time.monotonic() - t, 1), "content": model.content,
        }
        print(f"mental model {mm['id']}: {op.get('status')} in {round(time.monotonic() - t, 1)}s, "
              f"{len(model.content or '')} chars", flush=True)
    hs.close()
    (RESULTS / "phase2-finalize.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    ok = all(v["status"] in ("completed", "exists") for v in report["mental_models"].values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
