# ADR-007: Money as integer minor units

- Status: Accepted — supersedes the "use Decimal" guidance from reviews in #103 and #106
- Date: 2026-03-10
- Deciders: alice, bob, carol
- PR: #112

## Context
We moved from `float` to `Decimal` in January. Since then: Decimal values serialised
inconsistently to JSON (`"10.50"` vs `10.5`), the ledger and Stripe (which uses integer minor
units) disagreed on rounding for JPY/KWD, and three bugs came from `Decimal` + `float` mixing.

## Decision
- Money is stored, passed and computed as `int` minor units: `amount_cents: int` plus
  `currency: str` (ISO 4217). The field name is `*_cents` regardless of currency exponent.
- No `Decimal` or `float` for money anywhere in `app/`. Percent-based fees use integer
  arithmetic with explicit rounding helpers in `app.domain.money` (`apply_bps`, `round_half_even_div`).
- Display formatting happens only in API response schemas.

## Consequences
- One migration (`0012_amounts_to_minor_units`) converts existing columns.
- Reviewers should no longer suggest `Decimal`.
