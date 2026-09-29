"""Ingest ADRs + CONTRIBUTING into the team bank (separate from PR history).

    .venv/Scripts/python scripts/ingest_architecture.py

Same guarantees as ingest_history.py: deterministic ids, content-hash state, resumable,
auto-consolidation stays off.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BACKEND_DIR, get_settings  # noqa: E402
from app.hindsight import HindsightError, HindsightService  # noqa: E402
from app.hindsight.bank_setup import configure_team_bank  # noqa: E402
from app.ingestion.architecture import ArchDocument, load_architecture_docs  # noqa: E402

RESULTS_DIR = BACKEND_DIR.parent / "evaluation" / "results"
_local = threading.local()


def client() -> HindsightService:
    if not hasattr(_local, "hs"):
        _local.hs = HindsightService()
    return _local.hs


def retain(bank: str, doc: ArchDocument, t0: float) -> dict:
    start = time.monotonic() - t0
    try:
        client().retain(bank, doc.content, context=doc.context, document_id=doc.document_id,
                        tags=doc.tags, metadata=doc.metadata, timestamp=doc.timestamp)
        ok, err = True, None
    except HindsightError as exc:
        ok, err = False, f"{type(exc).__name__}: {exc}"
    end = time.monotonic() - t0
    return {"document_id": doc.document_id, "ok": ok, "error": err, "start_s": round(start, 1),
            "end_s": round(end, 1), "seconds": round(end - start, 1), "chars": len(doc.content)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    bank = get_settings().team_bank_id
    docs = load_architecture_docs()
    state_path = BACKEND_DIR / ".state" / f"ingest-arch-{bank}.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    todo = [d for d in docs if args.force or state.get(d.document_id, {}).get("hash") != d.content_hash]

    hs = HindsightService()
    configure_team_bank(hs, bank, auto_consolidation=False)
    print(f"found {len(docs)} architecture docs: {[d.document_id for d in docs]}")
    print(f"to_retain={[d.document_id for d in todo]}")
    t0, runs, lock = time.monotonic(), [], threading.Lock()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(retain, bank, d, t0): d for d in todo}
        for fut in as_completed(futures):
            doc, run = futures[fut], fut.result()
            runs.append(run)
            print(f"  {run['document_id']:<32} {run['seconds']:6.1f}s  {'ok' if run['ok'] else run['error']}", flush=True)
            if run["ok"]:
                with lock:
                    state[doc.document_id] = {"hash": doc.content_hash,
                                              "retained_at": datetime.now(UTC).isoformat()}
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    report = {"ran_at": datetime.now(UTC).isoformat(), "bank": bank,
              "documents_found": [d.document_id for d in docs],
              "skipped_unchanged": [d.document_id for d in docs if d not in todo],
              "wall_seconds": round(time.monotonic() - t0, 1), "runs": runs}
    hs.close()
    (RESULTS_DIR / "ingest-architecture.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    failed = [r for r in runs if not r["ok"]]
    print(f"wall={report['wall_seconds']}s ok={len(runs) - len(failed)} failed={len(failed)} "
          f"skipped={len(report['skipped_unchanged'])}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
