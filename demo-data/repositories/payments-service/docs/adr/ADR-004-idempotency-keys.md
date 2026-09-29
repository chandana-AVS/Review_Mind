# ADR-004: Idempotency keys for money-moving endpoints

- Status: Accepted
- Date: 2026-02-04
- Deciders: alice, bob, carol
- PR: #107

## Context
Mobile clients retry on timeouts. In January a retried `POST /refunds` produced a duplicate
refund of EUR 1,240 that ops had to claw back manually.

## Decision
- Every `POST` endpoint that creates or moves money (charges, captures, refunds, payouts,
  transfers) is decorated with `@idempotent` from `app.utils.idempotency`.
- Clients send an `Idempotency-Key` header; the decorator stores the key + response for 24 h and
  replays the stored response on repeats. Missing key → 400.

## Consequences
- Read-only endpoints and internal admin endpoints do not need it.
- Inbound provider webhooks deduplicate on the provider's own event id instead (see #119).
