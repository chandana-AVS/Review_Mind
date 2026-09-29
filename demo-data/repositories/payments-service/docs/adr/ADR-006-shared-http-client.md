# ADR-006: One shared HTTP client for all outbound calls

- Status: Accepted
- Date: 2026-02-23
- Deciders: alice, bob
- PR: #110

## Context
Outbound calls used a mix of `requests` and `httpx`, with inconsistent (often missing) timeouts.
A card-network outage on 2026-02-11 hung worker threads for 9 minutes because a `requests.get`
had no timeout.

## Decision
- All outbound HTTP goes through `app.clients.http.make_client(service_name)`, which returns a
  configured `httpx.Client` with: 3 s connect / 10 s read timeouts, 2 retries with jittered
  backoff on connect errors and 502/503/504, request-id propagation and metrics.
- Do not import `requests` or construct `httpx.Client` / call `httpx.get|post` anywhere else.
- Do **not** add extra retry loops around client calls (see INC-12).

## Consequences
- `requests` removed from dependencies.
- Provider SDKs with their own transport (Stripe) are exempt.
