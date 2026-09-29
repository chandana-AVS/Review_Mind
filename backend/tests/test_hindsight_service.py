"""Offline tests for the Hindsight wrapper: error mapping, retry policy, secret handling."""

import aiohttp
import pytest
from hindsight_client_api.exceptions import ApiException

from app.config import Settings
from app.hindsight import (
    HindsightAuthError,
    HindsightBadRequest,
    HindsightQuotaError,
    HindsightService,
    HindsightUnavailable,
)


class FakeClient:
    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0

    def _maybe_fail(self):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)

    def recall(self, *args, **kwargs):
        self._maybe_fail()
        return type("R", (), {"results": []})()

    def retain(self, *args, **kwargs):
        self._maybe_fail()
        return type("R", (), {"success": True, "items_count": 1, "usage": None})()


def settings():
    return Settings(hindsight_base_url="http://example.invalid", hindsight_api_key="sk-secret-value",
                    hindsight_max_attempts=3, _env_file=None)


def api_error(status):
    return ApiException(status=status, reason="x")


@pytest.mark.parametrize("status,expected", [
    (401, HindsightAuthError), (403, HindsightAuthError), (402, HindsightQuotaError),
    (422, HindsightBadRequest), (500, HindsightUnavailable), (429, HindsightUnavailable),
])
def test_recall__api_error__maps_to_typed_error(status, expected):
    svc = HindsightService(settings(), client=FakeClient([api_error(status)] * 5))
    with pytest.raises(expected):
        svc.recall("bank", "q")


def test_recall__aiohttp_response_error_401__maps_to_auth_error():
    err = aiohttp.ClientResponseError(request_info=None, history=(), status=401, message="Unauthorized")
    svc = HindsightService(settings(), client=FakeClient([err]))
    with pytest.raises(HindsightAuthError):
        svc.recall("bank", "q")


def test_recall__connection_error_then_success__retries_and_returns(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    client = FakeClient([aiohttp.ClientConnectionError("down")])
    svc = HindsightService(settings(), client=client)

    assert svc.recall("bank", "q") == []
    assert client.calls == 2


def test_recall__server_error__not_retried_again_by_wrapper():
    client = FakeClient([api_error(503)] * 5)
    svc = HindsightService(settings(), client=client)

    with pytest.raises(HindsightUnavailable):
        svc.recall("bank", "q")
    assert client.calls == 1  # the official client already retried 429/503


def test_recall__timeout__not_retried_because_server_may_still_be_working(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    client = FakeClient([TimeoutError()] * 3)
    svc = HindsightService(settings(), client=client)

    with pytest.raises(HindsightUnavailable):
        svc.recall("bank", "q")
    assert client.calls == 1


def test_retain__without_document_id__never_retried(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    client = FakeClient([aiohttp.ClientConnectionError("down")])
    svc = HindsightService(settings(), client=client)

    with pytest.raises(HindsightUnavailable):
        svc.retain("bank", "text")
    assert client.calls == 1


def test_retain__with_document_id__retried_because_upsert_is_idempotent(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    client = FakeClient([aiohttp.ClientConnectionError("down")])
    svc = HindsightService(settings(), client=client)

    assert svc.retain("bank", "text", document_id="doc-1")["success"] is True
    assert client.calls == 2


def test_errors__auth_failure__message_does_not_leak_api_key():
    svc = HindsightService(settings(), client=FakeClient([api_error(401)]))
    with pytest.raises(HindsightAuthError) as exc:
        svc.recall("bank", "q")
    assert "sk-secret-value" not in str(exc.value)
    assert "sk-secret-value" not in repr(settings())
