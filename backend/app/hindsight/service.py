"""The only module that talks to Hindsight. Everything else uses HindsightService.

Retry policy (one layer, no stacking):
- The official client already retries 429/503 with jittered backoff for recall/reflect.
- This wrapper adds retries for connection errors/timeouts on *idempotent* calls only:
  recall, reflect, reads, and retain calls that carry a document_id (Hindsight upserts by
  document_id, so repeating one replaces rather than duplicates).
- Retain without a document_id is never retried: a repeat could duplicate memories.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any, TypeVar

from hindsight_client import Hindsight

from app.config import Settings, get_settings
from app.hindsight.errors import HindsightError, HindsightNotFound, HindsightUnavailable, translate

log = logging.getLogger("reviewmind.hindsight")
T = TypeVar("T")


@dataclass(frozen=True)
class Memory:
    id: str
    text: str
    type: str | None
    document_id: str | None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)
    context: str | None = None
    occurred_start: str | None = None
    score: float | None = None
    source_fact_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, item: Any) -> "Memory":
        scores = getattr(item, "scores", None)
        final = None
        if scores is not None:
            final = scores.get("final") if isinstance(scores, dict) else getattr(scores, "final", None)
        return cls(
            id=str(item.id),
            text=item.text,
            type=getattr(item, "type", None) or getattr(item, "fact_type", None),
            document_id=getattr(item, "document_id", None),
            tags=list(getattr(item, "tags", None) or []),
            metadata=dict(getattr(item, "metadata", None) or {}),
            context=getattr(item, "context", None),
            occurred_start=str(item.occurred_start) if getattr(item, "occurred_start", None) else None,
            score=final,
            source_fact_ids=[str(x) for x in (getattr(item, "source_fact_ids", None)
                                              or getattr(item, "source_memory_ids", None) or [])],
        )


@dataclass(frozen=True)
class ReflectResult:
    text: str
    structured: dict[str, Any] | None
    structured_error: str | None
    based_on: Any


class HindsightService:
    def __init__(self, settings: Settings | None = None, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        if client is None:
            api_key = self.settings.hindsight_api_key
            # Only send the Cloud key to Cloud; a local server needs no auth.
            send_key = api_key is not None and self.settings.hindsight_mode == "cloud"
            client = Hindsight(
                base_url=self.settings.hindsight_base_url,
                api_key=api_key.get_secret_value() if send_key else None,
                timeout=self.settings.hindsight_timeout_s,
                max_attempts=self.settings.hindsight_max_attempts,
            )
        self._client = client

    # --- plumbing ---------------------------------------------------------------------------

    def _call(self, operation: str, fn: Callable[[], T], *, idempotent: bool) -> T:
        attempts = self.settings.hindsight_max_attempts if idempotent else 1
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                result = fn()
                log.debug("hindsight_%s ok in %.2fs", operation, time.monotonic() - started)
                return result
            except Exception as exc:  # noqa: BLE001 - translated below
                err = translate(operation, exc)
                # The client already retried 429/503 for reads; only connection-level
                # failures are worth another attempt here. Never retry a timeout: the server
                # may still be working on it (reflect can run for minutes), so a retry stacks load.
                timed_out = isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or isinstance(
                    exc.__cause__, (asyncio.TimeoutError, TimeoutError))
                retry_here = isinstance(err, HindsightUnavailable) and err.status is None and not timed_out
                if not (retry_here and attempt < attempts):
                    log.warning("hindsight_%s failed: %s", operation, type(err).__name__)
                    raise err from exc
                time.sleep(random.uniform(0, 0.5 * 2 ** (attempt - 1)))
        raise AssertionError("unreachable")

    # --- health -----------------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        version = self._call("version", self._client.get_version, idempotent=True)
        data = version.to_dict() if hasattr(version, "to_dict") else dict(version)
        return {"ok": True, "mode": self.settings.hindsight_mode,
                "base_url": self.settings.hindsight_base_url, "server": data}

    # --- banks ------------------------------------------------------------------------------

    def ensure_bank(self, bank_id: str, **config: Any) -> None:
        """Create the bank if needed, then apply config (both idempotent)."""
        create_keys = {"name", "mission", "background"}
        create_args = {k: v for k, v in config.items() if k in create_keys}
        try:
            self._call("get_bank_config", lambda: self._client.get_bank_config(bank_id), idempotent=True)
        except HindsightNotFound:
            self._call("create_bank", lambda: self._client.create_bank(bank_id, **create_args), idempotent=True)
        update = {k: v for k, v in config.items() if k not in create_keys}
        if update:
            self._call("update_bank_config", lambda: self._client.update_bank_config(bank_id, **update),
                       idempotent=True)

    def get_bank_config(self, bank_id: str) -> dict[str, Any]:
        return self._call("get_bank_config", lambda: self._client.get_bank_config(bank_id), idempotent=True)

    def bank_exists(self, bank_id: str) -> bool:
        try:
            self.get_bank_config(bank_id)
            return True
        except HindsightNotFound:
            return False

    def clone_bank(self, bank_id: str, target_bank_id: str) -> str:
        """Server-side copy of memories + config (no LLM work); returns the operation id."""
        return self._call("clone_bank", lambda: self._client.clone_bank(
            bank_id, target_bank_id, include_data=True, include_bank_config=True), idempotent=False)

    def delete_bank(self, bank_id: str) -> None:
        try:
            self._call("delete_bank", lambda: self._client.delete_bank(bank_id), idempotent=True)
        except HindsightNotFound:
            pass

    # --- retain / recall / reflect ------------------------------------------------------------

    def retain(self, bank_id: str, content: str, *, context: str | None = None,
               document_id: str | None = None, tags: list[str] | None = None,
               metadata: dict[str, str] | None = None, timestamp: datetime | None = None,
               update_mode: str | None = None) -> dict[str, Any]:
        resp = self._call(
            "retain",
            lambda: self._client.retain(
                bank_id, content, context=context, document_id=document_id, tags=tags,
                metadata=metadata, timestamp=timestamp, update_mode=update_mode,
            ),
            idempotent=document_id is not None and update_mode in (None, "replace"),
        )
        return {"success": resp.success, "items_count": resp.items_count,
                "usage": resp.usage.to_dict() if getattr(resp, "usage", None) else None}

    def recall(self, bank_id: str, query: str, *, types: list[str] | None = None,
               tags: list[str] | None = None, tags_match: str = "any", budget: str = "mid",
               max_tokens: int = 4096, query_timestamp: str | None = None,
               prefer_observations: bool = False) -> list[Memory]:
        resp = self._call(
            "recall",
            lambda: self._client.recall(
                bank_id, query, types=types, tags=tags, tags_match=tags_match, budget=budget,
                max_tokens=max_tokens, query_timestamp=query_timestamp,
                prefer_observations=prefer_observations,
            ),
            idempotent=True,
        )
        return [Memory.from_api(r) for r in (resp.results or [])]

    def recall_with_sources(self, bank_id: str, query: str, *, max_tokens: int = 2048,
                            budget: str = "mid", query_timestamp: str | None = None,
                            types: list[str] | None = None) -> tuple[list[Memory], dict[str, Memory]]:
        """Recall plus the raw facts behind any observations (for provenance)."""
        resp = self._call(
            "recall",
            lambda: self._client.recall(
                bank_id, query, types=types, budget=budget, max_tokens=max_tokens,
                query_timestamp=query_timestamp, include_source_facts=True,
            ),
            idempotent=True,
        )
        sources = {k: Memory.from_api(v) for k, v in (resp.source_facts or {}).items()}
        return [Memory.from_api(r) for r in (resp.results or [])], sources

    def reflect(self, bank_id: str, query: str, *, context: str | None = None,
                response_schema: dict[str, Any] | None = None, tags: list[str] | None = None,
                tags_match: str = "any", budget: str = "low", include_facts: bool = True,
                max_tokens: int | None = None) -> ReflectResult:
        resp = self._call(
            "reflect",
            lambda: self._client.reflect(
                bank_id, query, context=context, response_schema=response_schema, tags=tags,
                tags_match=tags_match, budget=budget, include_facts=include_facts,
                max_tokens=max_tokens,
            ),
            idempotent=True,
        )
        based_on = resp.based_on.to_dict() if getattr(resp, "based_on", None) is not None and hasattr(
            resp.based_on, "to_dict") else resp.based_on
        return ReflectResult(text=resp.text or "", structured=resp.structured_output,
                             structured_error=resp.structured_output_error, based_on=based_on)

    def list_memories(self, bank_id: str, *, type: str | None = None, limit: int = 100) -> list[Memory]:
        resp = self._call("list_memories",
                          lambda: self._client.list_memories(bank_id, type=type, limit=limit),
                          idempotent=True)
        return [Memory.from_api(item) for item in (resp.items or [])]

    # --- consolidation / operations / mental models --------------------------------------------

    def _http(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        """Endpoints the Python client doesn't wrap (consolidate, operation status)."""
        import json as _json
        import urllib.error
        import urllib.request

        headers = {"Content-Type": "application/json"}
        key = self.settings.hindsight_api_key
        if key is not None and self.settings.hindsight_mode == "cloud":
            headers["Authorization"] = f"Bearer {key.get_secret_value()}"
        req = urllib.request.Request(self.settings.hindsight_base_url.rstrip("/") + path, method=method,
                                     data=_json.dumps(body).encode() if body is not None else None,
                                     headers=headers)

        def call() -> dict[str, Any]:
            try:
                with urllib.request.urlopen(req, timeout=self.settings.hindsight_timeout_s) as resp:
                    return _json.loads(resp.read().decode() or "{}")
            except urllib.error.HTTPError as exc:
                from hindsight_client_api.exceptions import ApiException
                raise ApiException(status=exc.code, reason=exc.read().decode()[:300]) from None

        return self._call(f"{method} {path.split('/')[-1]}", call, idempotent=method == "GET")

    def consolidate(self, bank_id: str) -> str:
        """Trigger one consolidation pass; returns the operation id."""
        return self._http("POST", f"/v1/default/banks/{bank_id}/consolidate", {})["operation_id"]

    def get_operation(self, bank_id: str, operation_id: str) -> dict[str, Any]:
        return self._http("GET", f"/v1/default/banks/{bank_id}/operations/{operation_id}")

    def wait_operation(self, bank_id: str, operation_id: str, *, timeout_s: float = 3600,
                       poll_s: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while True:
            op = self.get_operation(bank_id, operation_id)
            if op.get("status") in ("completed", "failed", "cancelled"):
                return op
            if time.monotonic() > deadline:
                raise HindsightUnavailable("wait_operation", f"{operation_id} still {op.get('status')}")
            time.sleep(poll_s)

    def create_mental_model(self, bank_id: str, *, name: str, source_query: str, model_id: str,
                            tags: list[str] | None = None, max_tokens: int | None = None,
                            trigger: dict[str, Any] | None = None) -> Any:
        return self._call("create_mental_model", lambda: self._client.create_mental_model(
            bank_id, name=name, source_query=source_query, tags=tags, max_tokens=max_tokens,
            trigger=trigger, id=model_id), idempotent=False)

    def get_mental_model(self, bank_id: str, model_id: str) -> Any:
        return self._call("get_mental_model", lambda: self._client.get_mental_model(
            bank_id, model_id, detail="full"), idempotent=True)

    def update_mental_model(self, bank_id: str, model_id: str, *, source_query: str,
                            max_tokens: int | None = None) -> Any:
        return self._call("update_mental_model", lambda: self._client.update_mental_model(
            bank_id, model_id, source_query=source_query, max_tokens=max_tokens), idempotent=True)

    def refresh_mental_model(self, bank_id: str, model_id: str) -> Any:
        return self._call("refresh_mental_model", lambda: self._client.refresh_mental_model(
            bank_id, model_id), idempotent=False)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:  # noqa: BLE001 - best effort
            pass


@lru_cache
def get_hindsight() -> HindsightService:
    return HindsightService()


__all__ = ["HindsightError", "HindsightService", "Memory", "ReflectResult", "get_hindsight"]
