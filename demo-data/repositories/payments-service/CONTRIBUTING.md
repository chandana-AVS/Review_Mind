# Contributing to payments-service

## Layout
- `app/api/` — FastAPI routers. Keep handlers thin: parse the request, call a service, map the
  result. Handlers must not open DB sessions or run queries; that belongs in `app/services/`
  and `app/repos/` (see ADR-003).
- `app/services/` — business logic.
- `app/repos/` — SQLAlchemy persistence.
- `app/domain/` — domain types.
- `app/clients/` — integrations with external providers.

## Tests
- Name tests `test_<unit>__<scenario>__<expected>`, e.g.
  `test_refund__exceeds_capture__raises_refund_exceeds_capture`.
- Build objects with the factories in `tests/factories.py` instead of constructing models inline.

## Database migrations
- Migrations must be backwards compatible with the currently deployed code. Use expand/contract:
  add the new column, ship code that writes both, backfill, then drop the old column in a
  **separate** PR (see ADR-008).

## Tooling
- `pre-commit install` — ruff lint + format run on every commit.
- `make test` — pytest with coverage.
