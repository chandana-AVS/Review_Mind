"""Refresh the two mental models in the DEMO bank only (after feedback has been retained there).

    .venv/Scripts/python scripts/refresh_demo_mental_models.py

- Targets settings.demo_bank_id; refuses to run against the historical baseline bank.
- Keeps the model ids (team-review-playbook, noise-profile) and the Phase 2 source queries.
- Refresh = Hindsight reflect over the demo bank's current memories, bounded by the server's
  HINDSIGHT_API_REFLECT_WALL_TIMEOUT (set in scripts/start_hindsight_local.ps1).
- Prints baseline model hashes before/after to prove the baseline was not touched.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.hindsight import HindsightNotFound, HindsightService  # noqa: E402
from phase2_finalize import MENTAL_MODELS  # noqa: E402  (same ids + source queries as Phase 2)

RESULTS = BACKEND_DIR.parent / "evaluation" / "results"


def snapshot(hs: HindsightService, bank: str) -> dict[str, dict]:
    out = {}
    for mm in MENTAL_MODELS:
        try:
            m = hs.get_mental_model(bank, mm["id"])
            content = m.content or ""
            out[mm["id"]] = {"length": len(content), "sha256": hashlib.sha256(content.encode()).hexdigest()[:16],
                             "last_refreshed_at": str(m.last_refreshed_at)}
        except HindsightNotFound:
            out[mm["id"]] = {"length": 0, "sha256": None, "last_refreshed_at": None, "missing": True}
    return out


def main() -> int:
    settings = get_settings()
    bank, baseline = settings.demo_bank_id, settings.team_bank_id
    if bank == baseline:
        print("refusing: demo bank id equals the baseline bank id")
        return 2
    hs = HindsightService()
    if not hs.bank_exists(bank):
        print(f"demo bank {bank} does not exist (it is created by scripts/feedback_review.py)")
        return 2

    baseline_before = snapshot(hs, baseline)
    demo_before = snapshot(hs, bank)
    report = {"bank": bank, "started_at": datetime.now(UTC).isoformat(), "models": {}}
    t_all = time.monotonic()
    for mm in MENTAL_MODELS:
        t = time.monotonic()
        if demo_before[mm["id"]].get("missing"):
            created = hs.create_mental_model(bank, name=mm["name"], source_query=mm["source_query"],
                                             model_id=mm["id"], max_tokens=4000)
            op_id = created.operation_id
        else:
            hs.update_mental_model(bank, mm["id"], source_query=mm["source_query"], max_tokens=4000)
            op_id = hs.refresh_mental_model(bank, mm["id"]).operation_id
        print(f"{mm['id']}: refreshing in {bank} (op={op_id})", flush=True)
        op = hs.wait_operation(bank, op_id, timeout_s=2400, poll_s=10)
        after = hs.get_mental_model(bank, mm["id"])
        report["models"][mm["id"]] = {"status": op.get("status"), "error": op.get("error_message"),
                                      "seconds": round(time.monotonic() - t, 1),
                                      "length_before": demo_before[mm["id"]]["length"],
                                      "length_after": len(after.content or ""), "content": after.content}
        print(f"{mm['id']}: {op.get('status')} in {report['models'][mm['id']]['seconds']}s, "
              f"{demo_before[mm['id']]['length']} -> {len(after.content or '')} chars", flush=True)

    baseline_after = snapshot(hs, baseline)
    hs.close()
    report["total_seconds"] = round(time.monotonic() - t_all, 1)
    report["baseline_unchanged"] = baseline_before == baseline_after
    report["baseline"] = baseline_after
    (RESULTS / "refresh-demo-mental-models.json").write_text(json.dumps(report, indent=2, default=str) + "\n",
                                                             encoding="utf-8")
    print(f"baseline unchanged: {report['baseline_unchanged']}  total {report['total_seconds']}s")
    ok = report["baseline_unchanged"] and all(
        m["status"] == "completed" and m["length_after"] > 0 for m in report["models"].values())
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
