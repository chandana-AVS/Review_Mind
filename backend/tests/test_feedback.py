"""Feedback model + store tests. Offline (fake Hindsight)."""

from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.feedback.models import FeedbackError, build_feedback
from app.feedback.store import FeedbackStore
from app.ingestion.history import LeakError

REVIEW = {
    "pr": 137, "title": "Add refund processing fee", "created_at": "2026-09-29T13:20:55+00:00",
    "findings": [
        {"title": "HTTP client must use make_client()", "file": "app/services/refund_fees.py", "line": 20,
         "severity": "high", "category": "architecture", "classification": "ARCHITECTURAL_DECISION",
         "basis": "hindsight_memory", "sources": ["ADR-006", "PR #113"]},
        {"title": "Fee calculated on wrong amount", "file": "app/services/refund_fees.py", "line": 35,
         "severity": "high", "category": "correctness", "classification": "NEW", "basis": "generic",
         "sources": []},
    ],
}
NOW = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)


def fb(decision="REJECTED", reason="Approved exception for this integration.", finding="F1", now=NOW):
    return build_feedback(REVIEW, "evaluation/results/reviews/pr-137-memory.json", finding, decision,
                          reason, "erin", now=now)


class FakeHS:
    def __init__(self):
        self.retained = []

    def retain(self, bank, content, **kw):
        self.retained.append({"bank": bank, "content": content, **kw})
        return {"success": True}


def settings():
    return Settings(hindsight_base_url="http://localhost:8888", _env_file=None)


def test_rejected_feedback__narrated_as_agent_experience_with_reason():
    f = fb()
    assert f.content.startswith("I (ReviewMind) reviewed pull request #137")
    assert "developer erin rejected this finding" in f.content
    assert 'Rejection reason: "Approved exception for this integration."' in f.content
    assert "outcome:rejected" in f.tags


def test_accepted_feedback__recorded_with_optional_comment():
    f = fb(decision="ACCEPTED", reason="")
    assert "developer erin accepted this finding" in f.content
    assert "Rejection reason" not in f.content
    assert f.metadata["decision"] == "ACCEPTED" and "outcome:accepted" in f.tags


def test_rejected_feedback__without_reason__raises():
    with pytest.raises(FeedbackError):
        fb(reason="   ")


def test_unknown_finding__raises_with_available_ids():
    with pytest.raises(FeedbackError, match="F1"):
        fb(finding="F9")


def test_provenance__metadata_links_pr_finding_decision_reason_and_review():
    m = fb().metadata
    assert m["pr_number"] == "137" and m["finding_id"] == "F1"
    assert m["decision"] == "REJECTED" and m["reason"] == "Approved exception for this integration."
    assert m["review_file"].endswith("pr-137-memory.json") and m["source"] == "review_feedback"
    assert m["file"] == "app/services/refund_fees.py" and m["line"] == "20"
    assert m["finding_sources"] == "ADR-006,PR #113"


def test_document_id__deterministic_per_pr_and_finding():
    assert fb().document_id == "feedback:payments-service:pr137:F1"
    assert fb(decision="ACCEPTED", reason="").document_id == fb().document_id  # decision change replaces
    assert fb(finding="F2", reason="x").document_id == "feedback:payments-service:pr137:F2"


def test_content_hash__same_feedback_later__is_identical():
    later = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
    assert fb().content_hash == fb(now=later).content_hash
    assert fb().content_hash != fb(reason="Different reason").content_hash


def test_store__resubmitting_identical_feedback__retains_once(tmp_path):
    hs = FakeHS()
    store = FeedbackStore(hs, bank="reviewmind-acme-payments-demo", settings=settings(), state_dir=tmp_path)

    first = store.submit(fb())
    second = store.submit(fb())

    assert first["status"] == "retained" and second["status"] == "unchanged"
    assert len(hs.retained) == 1
    call = hs.retained[0]
    assert call["document_id"] == "feedback:payments-service:pr137:F1"
    assert "speaker is ReviewMind" in call["context"]


def test_store__changed_decision__retained_again_under_same_document(tmp_path):
    hs = FakeHS()
    store = FeedbackStore(hs, bank="reviewmind-acme-payments-demo", settings=settings(), state_dir=tmp_path)

    store.submit(fb())
    store.submit(fb(decision="ACCEPTED", reason=""))

    assert [c["document_id"] for c in hs.retained] == ["feedback:payments-service:pr137:F1"] * 2


def test_store__baseline_bank__refused():
    with pytest.raises(LeakError):
        FeedbackStore(FakeHS(), bank="reviewmind-acme-payments", settings=settings())
