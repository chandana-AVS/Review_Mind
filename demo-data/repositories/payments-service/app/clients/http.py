"""The only way to make outbound HTTP calls (ADR-006)."""

import httpx

from app.utils.logging import get_logger
from app.utils.request_context import current_request_id

log = get_logger(__name__)

_RETRY_STATUSES = {502, 503, 504}


class _RetryTransport(httpx.HTTPTransport):
    """Connect-level retries plus up to `retries` retries on 502/503/504."""

    def __init__(self, retries: int = 2) -> None:
        super().__init__(retries=retries)
        self._retries = retries

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = super().handle_request(request)
        attempt = 0
        while response.status_code in _RETRY_STATUSES and attempt < self._retries:
            attempt += 1
            response.close()
            response = super().handle_request(request)
        return response


def _log_response(service_name: str):
    def hook(response: httpx.Response) -> None:
        log.info("http_call", service=service_name, status=response.status_code)

    return hook


def make_client(service_name: str, base_url: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        timeout=httpx.Timeout(10.0, connect=3.0),
        transport=_RetryTransport(),
        headers={"X-Request-Id": current_request_id(), "User-Agent": f"payments/{service_name}"},
        event_hooks={"response": [_log_response(service_name)]},
    )
