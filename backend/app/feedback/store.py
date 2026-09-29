"""Retain developer feedback in Hindsight and recall it later.

Feedback never goes into the historical baseline bank (that bank is the evaluation baseline
and must not see held-out PRs); it goes into the demo bank, a server-side clone of it.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.config import BACKEND_DIR, Settings, get_settings
from app.feedback.models import AGENT_CONTEXT, Feedback
from app.hindsight import HindsightService, Memory
from app.ingestion.history import LeakError


class FeedbackStore:
    def __init__(self, hs: HindsightService, bank: str | None = None,
                 settings: Settings | None = None, state_dir: Path | None = None) -> None:
        self.settings = settings or get_settings()
        self.hs = hs
        self.bank = bank or self.settings.demo_bank_id
        if self.bank == self.settings.team_bank_id:
            raise LeakError(f"refusing to write feedback into the historical baseline bank {self.bank!r}")
        self.state_path = (state_dir or BACKEND_DIR / ".state") / f"feedback-{self.bank}.json"

    def ensure_bank(self, *, source_bank: str | None = None, timeout_s: float = 900) -> bool:
        """Create the demo bank as a clone of the historical bank if needed. Returns True if cloned."""
        if self.hs.bank_exists(self.bank):
            return False
        op_id = self.hs.clone_bank(source_bank or self.settings.team_bank_id, self.bank)
        op = self.hs.wait_operation(source_bank or self.settings.team_bank_id, op_id, timeout_s=timeout_s)
        if op.get("status") != "completed":
            raise RuntimeError(f"cloning into {self.bank} failed: {op}")
        return True

    def _state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}

    def submit(self, fb: Feedback) -> dict:
        """Retain the feedback. Idempotent: same document id; unchanged content is skipped."""
        state = self._state()
        if state.get(fb.document_id, {}).get("hash") == fb.content_hash:
            return {"document_id": fb.document_id, "status": "unchanged", "retained": False}
        result = self.hs.retain(
            self.bank, fb.content, context=AGENT_CONTEXT, document_id=fb.document_id,
            tags=fb.tags, metadata=fb.metadata,
            timestamp=datetime.fromisoformat(fb.submitted_at),
        )
        state[fb.document_id] = {"hash": fb.content_hash, "decision": fb.decision,
                                 "submitted_at": fb.submitted_at}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        return {"document_id": fb.document_id, "status": "retained", "retained": bool(result["success"])}

    def stored_units(self, fb: Feedback) -> list[Memory]:
        return [m for m in self.hs.list_memories(self.bank, limit=2000) if m.document_id == fb.document_id]

    def recall_feedback(self, query: str, *, experience_only: bool = False) -> list[Memory]:
        return self.hs.recall(self.bank, query, types=["experience"] if experience_only else None,
                              budget="low", max_tokens=1500)
