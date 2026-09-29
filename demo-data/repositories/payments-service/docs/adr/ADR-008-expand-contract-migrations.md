# ADR-008: Expand/contract database migrations

- Status: Accepted
- Date: 2026-05-12
- Deciders: alice, bob
- PR: #121

## Context
INC-19 (2026-05-07): a hotfix dropped `charges.legacy_status` in the same deploy as the code that
stopped reading it. During the rolling deploy, old pods crashed for 6 minutes. #121 restores the
column and records this decision.

## Decision
- A migration must work with both the currently deployed code and the new code.
- Renames and drops are done in steps: expand (add) → write both → backfill → switch reads →
  contract (drop) in a **separate, later PR**.
- A PR that drops or renames a column must not change application code in the same PR.
