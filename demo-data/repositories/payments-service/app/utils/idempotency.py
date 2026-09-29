"""@idempotent decorator for money-moving POST endpoints (ADR-004)."""

import functools
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request

from app.utils.idempotency_store import load_response, save_response


def idempotent(handler: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(handler)
    def wrapper(*args: Any, request: Request, **kwargs: Any) -> Any:
        key = request.headers.get("Idempotency-Key")
        if not key:
            raise HTTPException(status_code=400, detail="Idempotency-Key header required")
        stored = load_response(request.url.path, key)
        if stored is not None:
            return stored
        response = handler(*args, **kwargs)
        save_response(request.url.path, key, response)
        return response

    return wrapper
