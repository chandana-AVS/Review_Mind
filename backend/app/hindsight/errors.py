"""Typed errors for everything that can go wrong talking to Hindsight.

Callers catch these instead of the generated client's ApiException / aiohttp errors.
Messages never include credentials.
"""

import asyncio

import aiohttp
from hindsight_client_api.exceptions import ApiException


class HindsightError(Exception):
    """Base class. `retryable` says whether repeating the same call could succeed."""

    retryable = False

    def __init__(self, operation: str, message: str, status: int | None = None) -> None:
        self.operation = operation
        self.status = status
        super().__init__(f"hindsight {operation} failed" + (f" ({status})" if status else "") + f": {message}")


class HindsightUnavailable(HindsightError):
    """Network error, timeout, 429 or 5xx."""

    retryable = True


class HindsightAuthError(HindsightError):
    """401/403: missing, wrong or expired API key, or key not allowed on this bank."""


class HindsightQuotaError(HindsightError):
    """402: Hindsight Cloud credits exhausted."""


class HindsightBadRequest(HindsightError):
    """400/422: the request itself is invalid."""


class HindsightNotFound(HindsightError):
    """404: bank, document or mental model does not exist."""


def translate(operation: str, exc: BaseException) -> HindsightError:
    if isinstance(exc, HindsightError):
        return exc
    # Some client paths raise ApiException, others (e.g. bank config) aiohttp's ClientResponseError.
    status_error = isinstance(exc, (ApiException, aiohttp.ClientResponseError))
    if status_error:
        status = exc.status
        body = _short(getattr(exc, "body", None) or getattr(exc, "reason", None)
                      or getattr(exc, "message", None) or "")
        if status in (401, 403):
            return HindsightAuthError(operation, "check HINDSIGHT_API_KEY / bank access", status)
        if status == 402:
            return HindsightQuotaError(operation, "insufficient Hindsight credits", status)
        if status == 404:
            return HindsightNotFound(operation, body, status)
        if status in (400, 409, 422):
            return HindsightBadRequest(operation, body, status)
        if status == 429 or (status is not None and status >= 500):
            return HindsightUnavailable(operation, body, status)
        return HindsightError(operation, body, status)
    if isinstance(exc, (aiohttp.ClientConnectionError, asyncio.TimeoutError, TimeoutError, OSError)):
        return HindsightUnavailable(operation, f"{type(exc).__name__}: {_short(str(exc))}")
    return HindsightError(operation, f"{type(exc).__name__}: {_short(str(exc))}")


def _short(text: object, limit: int = 300) -> str:
    text = str(text).replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"
