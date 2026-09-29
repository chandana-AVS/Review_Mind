# ADR-003: Service layer between API handlers and persistence

- Status: Accepted
- Date: 2026-01-08
- Deciders: alice, bob
- PR: #101

## Context
Handlers in `app/api/` grew to 150+ lines, mixing request parsing, SQL and provider calls. Two
refund bugs in December came from duplicated query logic in two handlers.

## Decision
- `app/api/` handlers only: validate input, call one service function, map the result/errors.
- Handlers never receive or open a DB `Session`. Services get a session through
  `app.db.session_scope()`; queries live in `app/repos/`.
- Services are plain functions/classes with no FastAPI imports.

## Consequences
- Services are unit-testable without the HTTP layer.
- Exception: operational endpoints (health, metrics) may touch infrastructure directly — they
  exist to test the infrastructure itself. (Added 2026-04-02, see #116.)
