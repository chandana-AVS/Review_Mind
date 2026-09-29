"""Live Hindsight test: retain -> recall -> reflect on a throwaway Acme Payments bank.

Skipped unless Hindsight is configured (backend/.env or env vars) and reachable.
Run:  cd backend && python -m pytest tests/test_hindsight_integration.py -v -s
"""

import uuid

import pytest

from app.config import get_settings
from app.hindsight import HindsightError, HindsightService

MEMORY = "Acme Payments prefers validation in the service layer."


@pytest.fixture(scope="module")
def hs():
    settings = get_settings()
    if settings.hindsight_mode == "cloud" and not settings.hindsight_api_key:
        pytest.skip("HINDSIGHT_API_KEY not set for Hindsight Cloud (see backend/.env.example)")
    svc = HindsightService()
    try:
        svc.health()
    except HindsightError as exc:
        svc.close()
        pytest.skip(f"Hindsight not reachable at {get_settings().hindsight_base_url}: {exc}")
    yield svc
    svc.close()


@pytest.fixture(scope="module")
def bank(hs):
    bank_id = f"{get_settings().hindsight_bank_prefix}-it-{uuid.uuid4().hex[:8]}"
    hs.ensure_bank(bank_id, name="ReviewMind integration test",
                   mission="I am ReviewMind, the code reviewer for the Acme Payments team.")
    yield bank_id
    hs.delete_bank(bank_id)


def test_health(hs):
    info = hs.health()
    assert info["ok"] is True
    print("\nhealth:", info["mode"], info["server"].get("api_version"))


def test_retain_then_recall_returns_the_convention(hs, bank):
    result = hs.retain(
        bank, MEMORY,
        context="Team convention stated by tech lead alice in a code review of payments-service",
        document_id="it-convention-validation",
        tags=["team:acme-payments", "repo:payments-service", "source:test"],
        metadata={"team": "acme-payments", "source": "integration-test"},
    )
    assert result["success"] is True

    memories = hs.recall(bank, "Where does Acme Payments want input validation to happen?")
    print("\nrecall:", [(m.type, m.text) for m in memories])
    assert memories, "recall returned nothing"
    joined = " ".join(m.text.lower() for m in memories)
    assert "validation" in joined and "service" in joined
    assert any(m.document_id == "it-convention-validation" for m in memories)


def test_recall_with_tag_filter(hs, bank):
    hit = hs.recall(bank, "validation", tags=["repo:payments-service"], tags_match="all_strict")
    miss = hs.recall(bank, "validation", tags=["repo:some-other-repo"], tags_match="all_strict")
    assert hit and not miss


def test_reflect_answers_from_memory(hs, bank):
    r = hs.reflect(bank, "A developer put input validation inside an API handler. "
                         "Is that consistent with how Acme Payments works? Answer briefly.")
    print("\nreflect:", r.text[:400])
    assert "service" in r.text.lower()


def test_reflect_structured_output(hs, bank):
    schema = {
        "type": "object",
        "properties": {
            "conventions": {"type": "array", "items": {"type": "string"}},
            "confident": {"type": "boolean"},
        },
        "required": ["conventions"],
    }
    r = hs.reflect(bank, "List Acme Payments' code review conventions you know about.",
                   response_schema=schema)
    print("\nstructured:", r.structured, r.structured_error)
    assert r.structured is not None, r.structured_error
    assert any("service" in c.lower() for c in r.structured["conventions"])
