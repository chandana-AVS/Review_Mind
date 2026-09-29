# ADR-005: Dataclasses for domain types, Pydantic only at the edges

- Status: Accepted
- Date: 2026-02-17
- Deciders: alice, bob
- PR: #109

## Context
We mixed Pydantic models and dataclasses in `app/services/`. Validation ran several times per
request (API schema → service model → repo), and Pydantic v2 coercion silently turned
`"10.5"` into numbers in one service path.

## Decision
- Pydantic models live in `app/api/schemas.py` (request/response) and `app/config.py` only.
- Domain and service types are `@dataclass(frozen=True, slots=True)` in `app/domain/`.
- Conversion happens once, in the handler.

## Consequences
- Services cannot rely on Pydantic validation; validate at the API edge.
- Supersedes the ad-hoc Pydantic `CustomerView` introduced in #108.
