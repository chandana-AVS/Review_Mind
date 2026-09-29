# Acme Payments demo dataset: specification

This is the contract for everything in `demo-data/` and `evaluation/labels/`.

- **Source of truth:** the Python modules in [evaluation/dataset_src/](../evaluation/dataset_src/). The JSON files are generated from them; never hand-edit the JSON.
- **Rebuild:** `python evaluation/dataset_src/build.py`
- **Validate:** `python evaluation/validate_dataset.py`
- **Assemble the benchmark file:** `python evaluation/build_benchmark.py`

- **Team:** Acme Payments ([demo-data/team.json](../demo-data/team.json)).
- **Repo:** `payments-service` (Python 3.12, FastAPI, SQLAlchemy 2, Alembic, structlog).
- **Code skeleton:** [demo-data/repositories/payments-service/](../demo-data/repositories/payments-service/) shows the code **as of the end of July 2026**, after all history PRs.
- **ADRs:** `docs/adr/ADR-003…008` in that repo.

## 1. Splits

| Split | PRs | Dates | State | Human reviews | Answer key |
|---|---|---|---|---|---|
| history | #101–#130 | 2026-01-06 → 07-22 | merged/closed | `demo-data/reviews/…/pr-<n>.json` | `evaluation/labels/history/pr-<n>.json` |
| feedback | #131–#136 | 2026-08-03 → 08-26 | open | none | `evaluation/labels/feedback/pr-<n>.json` |
| test | #137–#144 | 2026-09-01 → 09-25 | open | none | `evaluation/labels/test/pr-<n>.json` |

**Nothing in `demo-data/` may mention convention ids (C1, N4, …), labels, or "planted".**
The data must read like a real GitHub export. Answer keys live only in `evaluation/labels/`.

## 2. File formats

### 2.1 `demo-data/pull-requests/payments-service/pr-<n>.json`
```json
{
  "number": 112,
  "title": "ADR-007: store money as integer minor units",
  "body": "Markdown PR description, 3-10 lines, written by the author in their voice.",
  "author": "bob",
  "created_at": "2026-03-09T10:12:00Z",
  "merged_at": "2026-03-11T16:40:00Z",
  "state": "merged",
  "split": "history",
  "labels": ["architecture", "money"],
  "base_sha": "<40 lowercase hex>",
  "head_sha": "<40 lowercase hex>",
  "adr": "ADR-007",
  "files": [
    { "path": "app/services/charges.py", "status": "modified",
      "additions": 3, "deletions": 2,
      "patch": "@@ -10,6 +10,7 @@ def capture(...):\n context\n-old\n+new\n+new2\n context\n context\n context" }
  ]
}
```
- `merged_at` is `null` when `state` is `closed` or `open`. Held-out PRs are `state: "open"`.
- `adr` is `null` unless the PR introduces an ADR (then that ADR's markdown is one of the `files`).
- `patch` is a GitHub-style unified diff body with no `diff --git` or `---/+++` header lines, starting at the first `@@`. **Hunk header counts must be exact.** The validator recounts them. Every body line starts with `+`, `-` or a space.
- New files use `@@ -0,0 +1,N @@`. Prefer adding new files, or small hunks, to keep line maths simple.
- 1–4 files per PR, 15–90 changed lines in total. Code must be plausible Python matching the codebase **as it was at that date** (see §5).

### 2.2 `demo-data/reviews/payments-service/pr-<n>.json` (history only)
```json
{
  "pr": 112,
  "reviews": [
    { "reviewer": "alice", "state": "approved", "submitted_at": "2026-03-10T09:00:00Z",
      "body": "LGTM once the fx rounding thread is resolved." }
  ],
  "threads": [
    {
      "id": "t112-1",
      "path": "app/services/fx.py",
      "line": 41,
      "comments": [
        { "author": "frank", "created_at": "2026-03-09T14:02:00Z", "body": "Shouldn't this stay Decimal?" },
        { "author": "bob", "created_at": "2026-03-09T15:30:00Z", "body": "No, that's the point of ADR-007…" }
      ],
      "resolution": "rejected",
      "decided_by": "bob",
      "rejection_reason": "Superseded by ADR-007: money is int minor units.",
      "follow_up_commit": null
    }
  ]
}
```
- `path`/`line` point at a **new-file line visible in the patch** (added or context). Use `path: null, line: null` for PR-level conversation threads.
- `resolution`:
  - `applied`: the author changed the code. Set `follow_up_commit` to a 40-hex SHA.
  - `rejected`: the suggestion was not taken. `rejection_reason` is **required** and is a one-sentence summary written as a human would.
  - `discussed`: talk only, no decision.
  - `withdrawn`: the reviewer retracted.
  - `noise`: LGTM, bot or typo chatter.
- `decided_by`: who settled it (usually the reviewer for applied; for rejected, the person whose argument won).
- Comment authors are team handles, plus `ci-bot` for bot comments. Timestamps are after the PR's `created_at` and in order.
- Voices:
  - alice: decisive, cites ADRs.
  - bob: precise, explains the money and integration reasons.
  - carol: friendly.
  - dan: asks questions.
  - erin: new, polite, sometimes defends choices from their previous job.
  - frank: confident, sweeping generalisations, platform-wide opinions.
- Reviewers **do not** name conventions with ids. They explain *why*, often referencing incidents (INC-12, INC-19), ADRs or earlier PRs ("same as #104").

### 2.3 `evaluation/labels/history/pr-<n>.json`
```json
{ "pr": 112,
  "threads": {
    "t112-1": { "convention": "C1", "noise": "N4", "mistake": null, "exception": null,
                "conflict": "K1", "is_convention_signal": true, "expected_strength": "enforced" } } }
```
- There must be exactly one entry per thread id.
- `expected_strength`: what the rule should look like to a correct memory *after all history*. One of `enforced`, `preferred`, `contested`, `not_a_rule`, `superseded`, or `null` for noise and chatter.

### 2.4 `evaluation/labels/{feedback,test}/pr-<n>.json`
```json
{
  "pr": 137, "split": "test", "demo": true,
  "should_flag": [
    { "id": "137-F1", "kind": "convention", "convention": "C4",
      "path": "app/api/refund_fees.py", "line_range": [14, 16],
      "rationale": "New money-moving POST without @idempotent (ADR-004).",
      "expected_fix": "Decorate with @idempotent." },
    { "id": "137-F5", "kind": "generic_bug", "convention": null,
      "path": "app/services/refund_fees.py", "line_range": [35, 35],
      "rationale": "Fee computed on original amount instead of remaining refundable amount.",
      "expected_fix": "Use charge.refundable_cents." }
  ],
  "should_not_flag": [
    { "id": "137-X1", "noise": "N4", "exception": null,
      "path": "app/services/refund_fees.py", "line_range": [30, 30],
      "trap": "Generic reviewer suggests Decimal for currency arithmetic.",
      "reject_reason": "We use int minor units since ADR-007; apply_bps handles rounding." }
  ],
  "drift_trap": null
}
```
- `line_range` values are **new-file line numbers of added (`+`) lines**. The validator checks them.
- `reject_reason` is the exact text the simulated developer uses when rejecting that trap in setup D.
- `drift_trap`: `{ "conflict": "K1"|"K2", "expected": "…" }` or `null`.

## 3. Taxonomy

### Conventions (C)
| ID | Rule | Where learned |
|---|---|---|
| C1 | Money is `int` minor units (`*_cents`). No `Decimal`/`float`. Use `app.domain.money` helpers | ADR-007 (#112), #130 |
| C2 | `app/api/` handlers never open sessions or query. They call services | ADR-003 (#101), CONTRIBUTING, #104, #122, #125 |
| C3 | Outbound HTTP only via `app.clients.http.make_client()`. No `requests`, raw `httpx` | ADR-006 (#110), #113, #123, #127 |
| C4 | Money-moving `POST` needs `@idempotent` | ADR-004 (#107), #111, #125, #130 |
| C5 | structlog; snake_case event name + kwargs; no f-string messages; PII via `redact()` | #114, #124, #129 |
| C6 | Services raise `app.errors` domain errors. `HTTPException` only in `app/api/` | #117, #124, #127 |
| C7 | Time from `app.utils.clock.now()`. Never `datetime.now()`/`utcnow()` | #105, #120, #125 |
| C8 | Test names `test_<unit>__<scenario>__<expected>`; objects from `tests/factories.py` | CONTRIBUTING, #106, #125 |
| C9 | Expand/contract migrations. No drop/rename in the same PR as the code change | ADR-008 (#121), CONTRIBUTING, #128 |
| C10 | Config via `app.config.settings`, never `os.environ` in `app/` | #122 |

### Noise (N): suggestions the team rejects
| ID | Suggestion | Canonical reason | Appears in |
|---|---|---|---|
| N1 | Docstrings on private `_helpers` | "Private helpers are documented by their names; we only document public service functions." | history #102 #104 #122 #129 |
| N2 | Type hints in tests/fixtures | "We don't type tests; mypy excludes tests/." | history #111 #125 #128 |
| N3 | Import order / formatting nits | "ruff + ruff-format run in pre-commit; don't spend review comments on formatting." | history #105 #117 #129 |
| N4 | Use `Decimal` for money | "Superseded by ADR-007; int minor units + money helpers." | history #118 #130 |
| N5 | Add retry/backoff around client calls | "make_client() already retries; stacking retries caused INC-12." | history #113 #123 |
| N6 | Extract magic numbers in tests into constants | "Test literals stay inline so each test reads on its own." | **feedback/test only** |
| N7 | Wrap service calls in try/except that logs and re-raises | "Domain errors propagate; the error middleware logs them. This double-logs." | **feedback/test only** |

### Mistakes (M): recurring violations, always fixed after review
M1 raw http → C3 · M2 `utcnow` → C7 · M3 f-string/PII log → C5 · M4 session in handler → C2 · M5 missing `@idempotent` → C4 · M6 `HTTPException` in service → C6.

### Exceptions (E): must not be flagged
E1 health/metrics endpoints may touch DB/Redis directly (`app/api/health.py`) · E2 Stripe webhook receiver has no `@idempotent`, dedupes on event id (`app/api/webhooks.py`) · E3 `scripts/` one-offs may use `print` and `os.environ`.

### Conflicts (K)
- **K1, money drift:** Decimal accepted in #103 and #106 (`superseded`) → ADR-007 in #112 → Decimal rejected in #118 and #130.
- **K2, HTTP drift:** "requests is fine for now" in #102 (`superseded`) → ADR-006 in #110.
- **K3, contested:** frank wants Pydantic in services. Accepted in #108 (`contested`) → ADR-005 in #109 → rejected in #115 and #124.
- **K4, loud opinion:** frank's "functions must be under 20 lines" in #120. Rejected, nobody else agrees (`not_a_rule`).

## 4. History PR plan

Thread notation: `C2/M4 applied (bob)` means a thread labelled convention C2 and mistake M4, resolution applied, reviewer bob. Every PR also gets 0–1 filler thread (`noise` resolution: LGTM, typo, a `ci-bot` coverage comment) labelled all-null with `is_convention_signal: false`.

| # | Created | Author | Title | Threads |
|---|---|---|---|---|
| 101 | 01-06 | alice | Introduce service layer (ADR-003) | adds ADR-003 + CONTRIBUTING + `app/services/charges.py`; C2 discussed (bob agrees, asks about health endpoints → "later"); filler |
| 102 | 01-09 | dan | Card-network status client | K2: bob "requests is fine for now, we'll standardise later" applied-as-is (resolution `discussed`, C3, strength `superseded`); N1 frank asks docstring on `_parse_status` rejected (carol) |
| 103 | 01-14 | carol | Store charge amounts precisely | K1: bob "float will bite us, use Decimal" applied (C1, `superseded`); filler |
| 104 | 01-20 | bob | Partial refunds endpoint | C2/M4 alice: session in handler → move to service, applied; N1 frank docstring on `_refundable` rejected (bob) |
| 105 | 01-27 | dan | Daily settlement report | C7/M2 bob: `datetime.utcnow()` → `clock.now()` (tests freeze time), applied; N3 frank import order rejected (alice: pre-commit) |
| 106 | 02-02 | carol | Partial captures | K1: alice "keep Decimal end-to-end" applied (C1 `superseded`); C8 bob test naming + factories applied |
| 107 | 02-04 | alice | Idempotency keys (ADR-004) | adds ADR-004 + decorator; C4 discussed (carol asks about webhooks → "they dedupe on event id", E2 foreshadow); filler |
| 108 | 02-10 | dan | Customer lookup service | K3: frank "use a Pydantic model for CustomerView in the service" applied (`contested`); filler |
| 109 | 02-16 | bob | Domain dataclasses (ADR-005) | adds ADR-005, converts CustomerView; K3 frank objects, alice decides, rejected; filler |
| 110 | 02-23 | alice | Shared HTTP client (ADR-006) | adds ADR-006 + `app/clients/http.py`, migrates card_network off requests; C3 discussed (K2 resolved); dan asks "retries?", answered |
| 111 | 03-02 | carol | Payouts endpoint | C4/M5 bob missing `@idempotent` applied; N2 frank type hints in tests rejected (carol: mypy excludes tests) |
| 112 | 03-09 | bob | Money as integer minor units (ADR-007) | adds ADR-007, migration 0012, `app/domain/money.py`; K1/C1/N4 frank "why not keep Decimal?" rejected (bob); C1 alice asks for `apply_bps` helper applied |
| 113 | 03-16 | dan | Fraud-check client | C3/M1 alice raw `httpx.post` → `make_client` applied; N5 frank "add tenacity retry" rejected (bob: INC-12 retry storm) |
| 114 | 03-23 | carol | Structured logging for refunds | C5/M3 bob f-string event → snake_case + kwargs applied; filler |
| 115 | 03-30 | dan | Disputes service | K3 frank "Pydantic here for validation" rejected (alice: ADR-005); filler |
| 116 | 04-01 | bob | Health check endpoint | E1: carol "handler opens a session?" → alice "ops endpoints are the exception, updating ADR-003" rejected (C2, exception E1) |
| 117 | 04-08 | carol | Map domain errors to HTTP | C6/M6 alice `HTTPException` raised in `services/charges.py` applied; N3 frank import grouping rejected |
| 118 | 04-15 | dan | Currency conversion for payouts | K1/N4 frank "fx math should be Decimal" rejected (bob: ADR-007, `round_half_even_div`); C1 bob float leftover applied |
| 119 | 04-22 | alice | Stripe webhook receiver | E2: dan "missing @idempotent?" rejected (alice: dedupe on event id); filler |
| 120 | 04-28 | dan | Invoice PDF generation | K4 frank "functions must be under 20 lines" rejected (dan pushes back, carol agrees) (`not_a_rule`); C7/M2 carol utcnow applied |
| 121 | 05-08 | bob | Restore legacy_status + expand/contract (ADR-008) | adds ADR-008 + migration; C9 discussed (INC-19 post-mortem); alice asks for CONTRIBUTING update applied |
| 122 | 05-13 | carol | Payment methods endpoint | C10 bob `os.environ["TOKENIZER_URL"]` → settings applied; C2/M4 alice query in handler applied; N1 frank docstring rejected |
| 123 | 05-19 | dan | Retry failed payouts job | C3/M1 bob `requests.post` applied; N5 frank retry loop rejected (alice) |
| 124 | 05-26 | bob | Ledger entries | K3 frank Pydantic rejected (alice); C5/M3 carol logs customer email → `redact()` applied; C6/M6 alice HTTPException in service applied |
| 125 | 06-03 | erin | Saved cards (first PR) | C2/M4 bob applied; C7/M2 carol applied; C4/M5 bob applied; C8 carol test naming applied; N2 frank type hints rejected (bob) |
| 126 | 06-10 | carol | Backfill refund reasons script | E3: frank "os.environ + print?" rejected (alice: one-off script) (C10, exception E3) |
| 127 | 06-24 | erin | Chargeback notifications | C3/M1 alice raw httpx applied; C6/M6 bob HTTPException in service applied (erin: "we did it this way at my last job") |
| 128 | 07-06 | dan | Rename refunds.note → reason, expand step | C9 bob "drop note in a separate PR" applied; N2 frank type hints rejected |
| 129 | 07-14 | erin | Refund reason codes | C5/M3 carol f-string log applied; N1 frank docstring rejected (dan!); N3 frank formatting rejected |
| 130 | 07-22 | bob | Multi-currency payouts | C4/M5 alice missing `@idempotent` applied; K1/N4 erin "use Decimal for currency conversion" rejected (bob: ADR-007) |

As built: 65 threads (target was 85–100; every planted item is still covered, as checked by the validator's coverage report). Every rejected thread has a realistic rejection reason. Timestamps fall within 0–4 days after `created_at`, and `merged_at` comes after the last comment.

## 5. Codebase state over time (write diffs against this)

| From | State |
|---|---|
| start | `app/api/`, `app/errors.py` (domain errors), `app/config.py` settings, `app/utils/clock.py`, `tests/factories.py`, stdlib `logging.getLogger`. Money is `amount: float`. Outbound HTTP none yet |
| #101 01-06 | `app/services/`, `app.db.session_scope()` |
| #102 01-09 | `app/clients/card_network.py` uses `requests` |
| #103 01-14 | money → `amount: Decimal` |
| #107 02-04 | `app/utils/idempotency.py` `@idempotent` |
| #109 02-16 | `app/domain/models.py` frozen dataclasses |
| #110 02-23 | `app/clients/http.py make_client(service_name, base_url)`; `requests` removed |
| #112 03-09 | money → `amount_cents: int`; `app/domain/money.py` (`apply_bps`, `round_half_even_div`, `exponent`) |
| #114 03-23 | `app.utils.logging.get_logger` (structlog), `redact()` |
| #116 04-01 | `app/api/health.py` |
| #119 04-22 | `app/api/webhooks.py`, `app/services/webhooks.py` |
| #121 05-08 | ADR-008 |

## 6. Held-out PR plan

Every held-out PR is *realistic new work* with **no reviews file**. The labels decide scoring.

- **Feedback PRs:** each has 2–3 `convention` should_flags, 2–4 noise traps, 0–1 generic bug. Across the six, N6 and N7 appear **at least 3 times each**, and every N1–N5 appears at least once.
- **Test PRs:** each has **≥2 convention should_flags, ≥3 noise traps, exactly 1 generic bug**; drift traps and exception traps as listed.

| # | Split | Created | Author | Title | should_flag | should_not_flag | drift |
|---|---|---|---|---|---|---|---|
| 131 | feedback | 08-03 | erin | Merchant balance endpoint | C2, C7 | N1, N6, N2 | – |
| 132 | feedback | 08-06 | dan | Capture expiry job | C3, C5 | N7, N5, N3 | – |
| 133 | feedback | 08-11 | carol | Dispute fee debit | C4, C6 | N6, N1, N4 | – |
| 134 | feedback | 08-17 | erin | Payout schedule settings | C10, C8 | N7, N2 | – |
| 135 | feedback | 08-21 | dan | Merchant refund webhooks | C3, C5 (PII) | N6, N5, N1 | – |
| 136 | feedback | 08-26 | bob | Ledger reconciliation report | C7, C9 | N7, N3, N4 | – |
| 137 | test (**demo**) | 09-01 | erin | Add refund processing fee | C4, C3, C7 + bug: fee on original amount instead of refundable | N4, N1, N2, N6, N5 | – |
| 138 | test | 09-03 | dan | Payout batching | C2, C5 + bug: off-by-one drops last batch | N1, N3, N7 | – |
| 139 | test | 09-08 | carol | Card-network fallback | C3 (uses `requests`, comment says "fine for now"), C6 + bug: exception swallowed, reports network "up" | N5, N2, N6 | K2 |
| 140 | test | 09-10 | erin | Delete saved card | C5 (logs email), C8, C10 + bug: no ownership check | N1, N2, N7 | – |
| 141 | test | 09-14 | bob | Redis health check | C7, C10 + bug: returns 200 when Redis is down | **E1**, N3, N1, N6 | – |
| 142 | test | 09-17 | dan | Stripe payout webhook events | C6, C5 + bug: dedupe check after side effect | **E2**, N7, N5, N2 | – |
| 143 | test | 09-22 | carol | FX conversion for multi-currency refunds | C1 (uses Decimal), C3 + bug: JPY exponent treated as 2 | N1, N6, N3 | K1 |
| 144 | test | 09-25 | erin | Payout reversal endpoint | C4, C2 + bug: allows reversing an already reversed payout | N4, N2, N7, **E3** (`scripts/` file with print/os.environ) | – |

Traps must be **realistic bait**, i.e. code a generic reviewer would really comment on:
- N1: a private helper with no docstring.
- N2: an untyped test function or fixture.
- N3: slightly unsorted imports. Keep it valid Python.
- N4: integer percentage maths that invites "use Decimal".
- N5: a client call with no local retry.
- N6: literal numbers in tests.
- N7: a service call that isn't wrapped in try/except.

## 7. How the benchmark uses the data

| Split | A generic | B naive RAG | C ReviewMind cold | D ReviewMind + feedback |
|---|---|---|---|---|
| history | unused | index docs + threads | ingest | ingest |
| feedback | – | – | – | review → simulated developer feedback, chronological |
| test | score | score | score | score at k = 0, 5, 10, 20 |

**Simulated developer:**
- A finding that matches a `should_flag` (same path, overlapping `line_range` ±3, same intent) is accepted.
- A finding that matches a `should_not_flag` is rejected with its `reject_reason`.
- Anything else is ignored.
- 10% of decisions are flipped at random, with a fixed seed.

All recalls use `query_timestamp = PR.created_at`.
