"""History PRs #127-#130: chargeback notifications, expand migration, refund reasons, multi-currency payouts."""

from lib import PR, c, label, modified, new_file, register, thread

# --- #127 Chargeback notifications ----------------------------------------------------------------

client_127 = new_file("app/clients/merchant_notifications.py", '''
    from app.clients.http import make_client
    from app.config import settings

    _client = make_client("merchant_notifications", settings.notifications_base_url)


    def send(merchant_id: str, template: str, variables: dict[str, object]) -> None:
        response = _client.post(
            "/v1/notifications",
            json={"merchant_id": merchant_id, "template": template, "variables": variables},
        )
        response.raise_for_status()
''')

service_127 = new_file("app/services/chargeback_notifications.py", '''
    from app import errors
    from app.clients import merchant_notifications
    from app.db import session_scope
    from app.repos import disputes as dispute_repo
    from app.utils.logging import get_logger

    log = get_logger(__name__)


    def notify_dispute_opened(dispute_id: str) -> None:
        with session_scope() as session:
            dispute = dispute_repo.get(session, dispute_id)
            if dispute is None:
                raise errors.NotFound(f"dispute {dispute_id} not found")
            merchant_id = dispute_repo.merchant_for(session, dispute_id)
        merchant_notifications.send(
            merchant_id,
            "dispute_opened",
            {
                "dispute_id": dispute.id,
                "amount_cents": dispute.amount_cents,
                "currency": dispute.currency,
                "evidence_due_at": dispute.evidence_due_at.isoformat(),
            },
        )
        log.info("dispute_notification_sent", dispute_id=dispute_id, merchant_id=merchant_id)
''')

register(PR(
    127, "Email merchants when a chargeback is opened",
    '''
    Merchants currently find out about disputes from the dashboard, often too late to submit
    evidence (7-day window). This sends a `dispute_opened` notification via the notifications
    service right after `open_dispute`.
    ''',
    "erin", "2026-06-24 10:45", [client_127, service_127], merged="2026-06-26 14:00",
    labels=["disputes", "notifications"],
    reviews=[
        ("alice", "changes_requested", "2026-06-24 14:20", ""),
        ("bob", "changes_requested", "2026-06-24 15:10", ""),
        ("alice", "approved", "2026-06-25 11:30", "Thanks Erin, much cleaner."),
    ],
    threads=[
        thread(client_127, '_client = make_client("merchant_notifications", settings.notifications_base_url)', [
            c("alice", "2026-06-24 14:15", "The first version did `httpx.post(...)` inline in the service. All outbound calls go through a client module built on `make_client` (ADR-006), which gives you the timeouts, retries and request ids for free. See `app/clients/card_network.py` for the pattern."),
            c("erin", "2026-06-24 15:00", "Moved it into `app/clients/merchant_notifications.py` using make_client."),
        ], "applied", "alice", **label("C3", mistake="M1")),
        thread(service_127, 'raise errors.NotFound(f"dispute {dispute_id} not found")', [
            c("bob", "2026-06-24 15:05", "This raised `HTTPException(404)`. Services raise `app.errors` domain errors and the API layer maps them (#117). This function runs from the dispute consumer, where an HTTPException means nothing."),
            c("erin", "2026-06-24 16:20", "Fair. At my last job the services were only ever called from routes, so HTTPException everywhere was normal. Switched to errors.NotFound."),
            c("bob", "2026-06-24 16:40", "Makes sense, here they're also called from consumers and jobs."),
        ], "applied", "bob", **label("C6", mistake="M6")),
    ],
))

# --- #128 Expand step: refunds.note -> reason -----------------------------------------------------

migration_128 = new_file("migrations/versions/0023_refunds_add_reason.py", '''
    """refunds: add reason (expand step of note -> reason rename)

    Revision ID: 0023
    Revises: 0022
    """

    import sqlalchemy as sa
    from alembic import op

    revision = "0023"
    down_revision = "0022"


    def upgrade() -> None:
        op.add_column("refunds", sa.Column("reason", sa.String(), nullable=True))
        op.execute("UPDATE refunds SET reason = note WHERE reason IS NULL")


    def downgrade() -> None:
        op.drop_column("refunds", "reason")
''')

repo_128 = modified("app/repos/refunds.py", before='''
    def insert(session: Session, refund: Refund) -> None:
        session.execute(
            refunds_table.insert().values(
                id=refund.id,
                charge_id=refund.charge_id,
                amount_cents=refund.amount_cents,
                currency=refund.currency,
                status=refund.status,
                note=refund.reason,
                created_at=refund.created_at,
            )
        )
''', after='''
    def insert(session: Session, refund: Refund) -> None:
        session.execute(
            refunds_table.insert().values(
                id=refund.id,
                charge_id=refund.charge_id,
                amount_cents=refund.amount_cents,
                currency=refund.currency,
                status=refund.status,
                note=refund.reason,  # TODO(#128 follow-up): stop writing once reads use `reason`
                reason=refund.reason,
                created_at=refund.created_at,
            )
        )
''', start=9)

tests_128 = new_file("tests/test_refund_repo.py", '''
    from app.repos import refunds as refund_repo
    from tests.factories import make_refund


    def test_insert__with_reason__writes_note_and_reason(db_session):
        refund_repo.insert(db_session, make_refund(reason="duplicate"))

        row = db_session.execute("SELECT note, reason FROM refunds").one()

        assert row.note == "duplicate"
        assert row.reason == "duplicate"
''')

register(PR(
    128, "Rename refunds.note to reason, step 1: add column and dual-write",
    '''
    Expand step of the `note` -> `reason` rename, per ADR-008:
    1. (this PR) add `reason`, backfill from `note`, write both
    2. switch reads to `reason`
    3. drop `note`

    Steps 2 and 3 are separate PRs so every deploy is compatible with the previous version.
    ''',
    "dan", "2026-07-06 10:00", [migration_128, repo_128, tests_128], merged="2026-07-08 09:30",
    labels=["database"],
    reviews=[
        ("bob", "changes_requested", "2026-07-06 13:30", "Almost; see thread."),
        ("frank", "commented", "2026-07-06 15:00", ""),
        ("bob", "approved", "2026-07-07 10:15", "Textbook expand step, thanks Dan."),
    ],
    threads=[
        thread(migration_128, 'op.add_column("refunds", sa.Column("reason", sa.String(), nullable=True))', [
            c("bob", "2026-07-06 13:25", "The first version also dropped `note` in this migration, with the repo switched to `reason` in the same PR. That's the INC-19 pattern: old pods still read `note` during the rolling deploy. Keep the drop for a separate PR after reads have moved (ADR-008)."),
            c("dan", "2026-07-06 14:40", "Right, I split it up: this PR is add + dual-write only, and I've listed the steps in the description."),
        ], "applied", "bob", **label("C9")),
        thread(tests_128, "def test_insert__with_reason__writes_note_and_reason(db_session):", [
            c("frank", "2026-07-06 14:55", "`db_session` should be annotated and the test should declare `-> None`."),
            c("dan", "2026-07-06 16:10", "We don't type tests here; mypy excludes tests/ (came up in #111 and #125)."),
        ], "rejected", "dan", "We don't type tests; mypy excludes tests/.", **label(noise="N2")),
    ],
))

# --- #129 Refund reason codes ---------------------------------------------------------------------

reasons_129 = new_file("app/services/refund_reasons.py", '''
    from app import errors
    from app.utils.logging import get_logger

    log = get_logger(__name__)

    REASON_CODES = {
        "duplicate": "Duplicate charge",
        "fraudulent": "Fraudulent charge",
        "requested_by_customer": "Requested by customer",
        "product_not_received": "Product not received",
    }


    def normalize_reason(raw: str | None) -> str | None:
        if raw is None:
            return None
        code = _to_code(raw)
        if code not in REASON_CODES:
            log.warning("refund_reason_unknown", raw_reason=raw)
            raise errors.Conflict(f"unknown refund reason {raw!r}")
        return code


    def _to_code(raw: str) -> str:
        return raw.strip().lower().replace(" ", "_").replace("-", "_")
''')

refunds_129 = modified("app/services/refunds.py", before='''
    from app import errors
    from app.db import session_scope
    from app.domain.models import Refund
    from app.repos import charges as charge_repo
    from app.repos import refunds as refund_repo
    from app.utils import clock
''', after='''
    from app import errors
    from app.db import session_scope
    from app.domain.models import Refund
    from app.repos import charges as charge_repo
    from app.repos import refunds as refund_repo
    from app.services.refund_reasons import normalize_reason
    from app.utils import clock
''')

register(PR(
    129, "Normalise refund reasons to a fixed set of codes",
    '''
    Refund reasons were free text, so finance can't report on them. This maps inputs to four codes
    (same as Stripe's) and rejects unknown ones. The support tool already sends one of these
    strings; the API change is backwards compatible for existing clients.
    ''',
    "erin", "2026-07-14 09:30", [reasons_129, refunds_129], merged="2026-07-16 11:00",
    labels=["refunds"],
    reviews=[
        ("carol", "changes_requested", "2026-07-14 12:40", ""),
        ("frank", "commented", "2026-07-14 14:10", ""),
        ("dan", "approved", "2026-07-15 10:20", ""),
        ("carol", "approved", "2026-07-15 11:05", ""),
    ],
    threads=[
        thread(reasons_129, 'log.warning("refund_reason_unknown", raw_reason=raw)', [
            c("carol", "2026-07-14 12:35", "This was `log.warning(f\"Unknown refund reason: {raw}\")`. Constant snake_case event name plus kwargs, please, or Datadog can't group it (see app/utils/logging.py)."),
            c("erin", "2026-07-14 13:30", "Oops, I knew that one! Fixed."),
        ], "applied", "carol", **label("C5", mistake="M3")),
        thread(reasons_129, "def _to_code(raw: str) -> str:", [
            c("frank", "2026-07-14 14:05", "`_to_code` needs a docstring explaining the normalisation rules."),
            c("dan", "2026-07-14 15:20", "It's a one-line private helper and the body is the rule. We don't docstring underscore helpers here (same discussion as #104 and #122)."),
            c("erin", "2026-07-14 15:45", "Leaving it then, thanks Dan."),
        ], "rejected", "dan", "Private helpers don't get docstrings here; the name and body are the documentation.",
            **label(noise="N1")),
        thread(refunds_129, "from app.services.refund_reasons import normalize_reason", [
            c("frank", "2026-07-14 14:08", "This import breaks alphabetical grouping: `app.services` should come after `app.repos`... actually it does, but the block should be split into first-party subgroups."),
            c("carol", "2026-07-14 15:00", "ruff's isort config is the source of truth and it's happy with this. Not something we adjust by hand in review."),
        ], "rejected", "carol", "Import grouping is whatever ruff/isort in pre-commit produces; no manual review.",
            **label(noise="N3")),
    ],
))

# --- #130 Multi-currency payouts ------------------------------------------------------------------

api_130 = modified("app/api/payouts.py", before='''
    @router.post("", response_model=PayoutResponse, status_code=201)
    @idempotent
    def create_payout(body: CreatePayoutRequest) -> PayoutResponse:
        payout = payout_service.create_payout(
            merchant_id=body.merchant_id, amount_cents=body.amount_cents, currency=body.currency
        )
        return PayoutResponse(**payout.as_dict())
''', after='''
    @router.post("", response_model=PayoutResponse, status_code=201)
    @idempotent
    def create_payout(body: CreatePayoutRequest) -> PayoutResponse:
        payout = payout_service.create_payout(
            merchant_id=body.merchant_id, amount_cents=body.amount_cents, currency=body.currency
        )
        return PayoutResponse(**payout.as_dict())


    @router.post("/convert", response_model=PayoutResponse, status_code=201)
    @idempotent
    def create_converted_payout(body: CreateConvertedPayoutRequest) -> PayoutResponse:
        payout = payout_service.create_converted_payout(
            merchant_id=body.merchant_id,
            balance_currency=body.balance_currency,
            payout_currency=body.payout_currency,
            amount_cents=body.amount_cents,
        )
        return PayoutResponse(**payout.as_dict())
''', start=10)

service_130 = modified("app/services/payouts.py", before='''
        with session_scope() as session:
            payout_repo.insert(session, payout)
        bank_transfers.submit(payout)
        log.info("payout_submitted", payout_id=payout.id, merchant_id=merchant_id)
        return payout
''', after='''
        with session_scope() as session:
            payout_repo.insert(session, payout)
        bank_transfers.submit(payout)
        log.info("payout_submitted", payout_id=payout.id, merchant_id=merchant_id)
        return payout


    def create_converted_payout(
        *, merchant_id: str, balance_currency: str, payout_currency: str, amount_cents: int
    ) -> Payout:
        payout_cents = fx.payout_amount(amount_cents, balance_currency, payout_currency)
        log.info(
            "payout_converted",
            merchant_id=merchant_id,
            from_currency=balance_currency,
            to_currency=payout_currency,
            amount_cents=amount_cents,
            payout_cents=payout_cents,
        )
        return create_payout(merchant_id=merchant_id, amount_cents=payout_cents, currency=payout_currency)
''', start=21)

register(PR(
    130, "Payouts in a different currency than the balance",
    '''
    Exposes the FX conversion from #118 as `POST /payouts/convert`: the merchant chooses the payout
    currency, we convert the balance amount and deduct the conversion fee.
    ''',
    "bob", "2026-07-22 10:15", [api_130, service_130], merged="2026-07-24 12:30",
    labels=["payouts", "money"],
    reviews=[
        ("alice", "changes_requested", "2026-07-22 13:00", ""),
        ("erin", "commented", "2026-07-22 15:30", "Question about precision."),
        ("alice", "approved", "2026-07-23 09:40", ""),
    ],
    threads=[
        thread(api_130, '@router.post("/convert", response_model=PayoutResponse, status_code=201)', [
            c("alice", "2026-07-22 12:55", "New money-moving endpoint without `@idempotent`, which the first commit was missing. Even for us 🙂 (ADR-004)."),
            c("bob", "2026-07-22 13:20", "Ha, guilty. Added."),
        ], "applied", "alice", **label("C4", mistake="M5")),
        thread(service_130, "payout_cents = fx.payout_amount(amount_cents, balance_currency, payout_currency)", [
            c("erin", "2026-07-22 15:25", "Should the conversion use `Decimal` for the intermediate result? At my previous company we always converted currency with Decimal to avoid precision loss."),
            c("bob", "2026-07-22 16:10", """
                Good instinct in general, but we deliberately don't here. ADR-007: money is int minor units
                end to end, and the conversion in fx.py uses integer micro-rates with a single half-even rounding
                step, so nothing is lost. Decimal is what we moved away from in March (#112), because the
                int<->Decimal boundaries were where the rounding mismatches happened."""),
            c("erin", "2026-07-22 16:30", "Ah, that makes sense. Thanks for the context!"),
        ], "rejected", "bob", "ADR-007: int minor units end to end; fx.py does integer maths with one rounding step.",
            **label("C1", noise="N4", conflict="K1")),
    ],
))
