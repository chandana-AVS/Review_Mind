"""Feedback PRs #131-#136 (setup D only): reviewed by ReviewMind, answered by a simulated developer."""

from lib import PR, flag, modified, new_file, register, trap

# --- #131 Merchant balance endpoint ---------------------------------------------------------------

api_131 = new_file("app/api/balances.py", '''
    from datetime import datetime

    from fastapi import APIRouter

    from app.api.schemas import BalanceResponse
    from app.db import session_scope
    from app.repos import balances as balance_repo

    router = APIRouter(prefix="/merchants/{merchant_id}/balance", tags=["balances"])


    @router.get("", response_model=BalanceResponse)
    def get_balance(merchant_id: str) -> BalanceResponse:
        with session_scope() as session:
            totals = balance_repo.totals_by_currency(session, merchant_id)
        as_of = datetime.utcnow()
        return BalanceResponse(
            merchant_id=merchant_id,
            as_of=as_of,
            balances=[_format_balance(t) for t in totals],
        )


    def _format_balance(totals) -> dict[str, object]:
        available = totals.captured_cents - totals.refunded_cents
        return {
            "currency": totals.currency,
            "available_cents": available,
            "pending_cents": totals.pending_payout_cents,
        }
''')

tests_131 = new_file("tests/test_balances.py", '''
    from app.api.balances import _format_balance
    from tests.factories import make_balance_totals


    def test_format_balance__captured_and_refunded__returns_available(fake_repos):
        totals = make_balance_totals(currency="EUR", captured_cents=12_500, refunded_cents=2_500)

        result = _format_balance(totals)

        assert result["available_cents"] == 10_000


    def test_format_balance__pending_payout__reports_pending():
        totals = make_balance_totals(currency="EUR", captured_cents=5_000, pending_payout_cents=1_500)

        assert _format_balance(totals)["pending_cents"] == 1_500
''')

register(PR(
    131, "Merchant balance endpoint",
    '''
    Adds `GET /merchants/{id}/balance` for the merchant dashboard (DASH-14): available and pending
    amounts per currency.
    ''',
    "erin", "2026-08-03 10:00", [api_131, tests_131], labels=["merchants"],
    heldout={
        "should_flag": [
            flag("131-F1", "convention", api_131, "with session_scope() as session:",
                 "totals = balance_repo.totals_by_currency", convention="C2",
                 rationale="Handler opens a DB session and queries the repo directly (ADR-003).",
                 fix="Move the query into a balances service function and call it from the handler."),
            flag("131-F2", "convention", api_131, "as_of = datetime.utcnow()", convention="C7",
                 rationale="Uses datetime.utcnow() instead of the injectable clock.",
                 fix="as_of = clock.now()"),
            flag("131-F3", "generic_bug", api_131, "available = totals.captured_cents - totals.refunded_cents",
                 rationale="Available balance ignores pending payouts, so merchants can over-withdraw.",
                 fix="Subtract totals.pending_payout_cents from available."),
        ],
        "should_not_flag": [
            trap("131-X1", api_131, "def _format_balance(totals)", noise="N1",
                 trap="Add a docstring to _format_balance.",
                 reject="Private helper, the name says what it does. We only docstring public service functions."),
            trap("131-X2", tests_131, "captured_cents=12_500, refunded_cents=2_500", noise="N6",
                 trap="Extract 12_500 / 2_500 into named constants.",
                 reject="Inline literals are on purpose, each test should read on its own."),
            trap("131-X3", tests_131, "def test_format_balance__pending_payout__reports_pending():", noise="N2",
                 trap="Add type hints / -> None to the test functions.",
                 reject="We don't type tests, mypy excludes tests/."),
        ],
    },
))

# --- #132 Capture expiry job ----------------------------------------------------------------------

job_132 = new_file("app/jobs/expire_authorizations.py", '''
    from datetime import timedelta

    import httpx

    from app.utils import clock
    from app.db import session_scope
    from app.config import settings
    from app.repos import charges as charge_repo
    from app.utils.logging import get_logger

    log = get_logger(__name__)

    AUTH_TTL = timedelta(days=7)


    def run() -> int:
        """Void authorizations that were never captured within the network's 7-day window."""
        cutoff = clock.now() - AUTH_TTL
        with session_scope() as session:
            expired = charge_repo.list_uncaptured_before(session, cutoff)
        for charge in expired:
            _void_authorization(charge.id)
            with session_scope() as session:
                charge_repo.mark_expired(session, charge.id, at=clock.now())
        log.info(f"Voided {len(expired)} expired authorizations")
        return len(expired)


    def _void_authorization(charge_id: str) -> None:
        response = httpx.post(
            f"{settings.card_network_base_url}/v1/authorizations/{charge_id}/void", timeout=10.0
        )
        response.raise_for_status()
''')

register(PR(
    132, "Nightly job to void expired authorizations",
    '''
    Uncaptured authorizations hold customer funds until the network drops them (~7 days) and they
    show up as "pending" on statements, which drives support tickets (SUP-301). This job voids
    them explicitly once they're past 7 days.
    ''',
    "dan", "2026-08-06 09:30", [job_132], labels=["jobs", "charges"],
    heldout={
        "should_flag": [
            flag("132-F1", "convention", job_132, "response = httpx.post(", "timeout=10.0", convention="C3",
                 rationale="Raw httpx.post instead of a client built on make_client (ADR-006).",
                 fix="Add a void call to app/clients/card_network.py using its make_client-based _client."),
            flag("132-F2", "convention", job_132, 'log.info(f"Voided {len(expired)}', convention="C5",
                 rationale="f-string log message instead of a snake_case event with kwargs.",
                 fix='log.info("expired_authorizations_voided", count=len(expired))'),
        ],
        "should_not_flag": [
            trap("132-X1", job_132, "charge_repo.mark_expired(session, charge.id, at=clock.now())", noise="N7",
                 trap="Wrap the void + mark_expired in try/except, log the error and re-raise.",
                 reject="Let it propagate; the job runner logs failures. try/log/raise just double-logs."),
            trap("132-X2", job_132, "_void_authorization(charge.id)", noise="N5",
                 trap="Add retry with backoff around the void call, the network API is flaky.",
                 reject="make_client already retries transient errors; another retry layer is how INC-12 happened."),
            trap("132-X3", job_132, "from app.utils import clock", noise="N3",
                 trap="Imports are not sorted (app.utils before app.db).",
                 reject="ruff fixes import order in pre-commit, no need to comment on it."),
        ],
    },
))

# --- #133 Dispute fee debit -----------------------------------------------------------------------

api_133 = new_file("app/api/disputes.py", '''
    from fastapi import APIRouter

    from app.api.schemas import DisputeFeeResponse
    from app.services import dispute_fees

    router = APIRouter(prefix="/disputes", tags=["disputes"])


    @router.post("/{dispute_id}/fee", response_model=DisputeFeeResponse, status_code=201)
    def debit_fee(dispute_id: str) -> DisputeFeeResponse:
        fee_cents = dispute_fees.debit_dispute_fee(dispute_id)
        return DisputeFeeResponse(dispute_id=dispute_id, fee_cents=fee_cents)
''')

service_133 = new_file("app/services/dispute_fees.py", '''
    from fastapi import HTTPException

    from app.db import session_scope
    from app.domain.money import apply_bps
    from app.repos import disputes as dispute_repo
    from app.repos import ledger as ledger_repo
    from app.utils import clock
    from app.utils.logging import get_logger

    log = get_logger(__name__)

    NETWORK_FEES_CENTS = {"visa": 1_500, "mastercard": 1_500, "amex": 2_000}
    DISPUTE_FEE_BPS = 40


    def debit_dispute_fee(dispute_id: str) -> int:
        with session_scope() as session:
            dispute = dispute_repo.get(session, dispute_id)
            if dispute is None:
                raise HTTPException(status_code=404, detail="dispute not found")
            if dispute.fee_debited_at is not None:
                raise HTTPException(status_code=409, detail="fee already debited")
            fee_cents = _fee_for(dispute.network) + apply_bps(dispute.amount_cents, DISPUTE_FEE_BPS)
            ledger_repo.debit_merchant(
                session, dispute.merchant_id, fee_cents, dispute.currency, reference=dispute.id
            )
            dispute_repo.mark_fee_debited(session, dispute.id, at=clock.now())
        log.info("dispute_fee_debited", dispute_id=dispute_id, fee_cents=fee_cents)
        return fee_cents


    def _fee_for(network: str) -> int:
        return NETWORK_FEES_CENTS.get(network, 1_500)
''')

tests_133 = new_file("tests/test_dispute_fees.py", '''
    from app.services import dispute_fees
    from tests.factories import make_dispute


    def test_debit_dispute_fee__visa_dispute__charges_flat_fee_plus_bps(fake_repos, frozen_clock):
        fake_repos.disputes.add(make_dispute(network="visa", amount_cents=20_000))

        fee = dispute_fees.debit_dispute_fee("dp_test_1")

        assert fee == 1_580


    def test_debit_dispute_fee__amex_dispute__uses_amex_flat_fee(fake_repos, frozen_clock):
        fake_repos.disputes.add(make_dispute(network="amex", amount_cents=10_000))

        assert dispute_fees.debit_dispute_fee("dp_test_1") == 2_040
''')

register(PR(
    133, "Debit the dispute fee from the merchant balance",
    '''
    When a dispute is opened the network charges us a flat fee (+0.4% for some programs), which we
    pass on to the merchant. Adds `POST /disputes/{id}/fee`, called by the dispute consumer.
    ''',
    "carol", "2026-08-11 10:20", [api_133, service_133, tests_133], labels=["disputes", "money"],
    heldout={
        "should_flag": [
            flag("133-F1", "convention", api_133, '@router.post("/{dispute_id}/fee"', "def debit_fee(dispute_id: str)",
                 convention="C4",
                 rationale="Money-moving POST without @idempotent (ADR-004); a retry double-debits the merchant.",
                 fix="Decorate debit_fee with @idempotent."),
            flag("133-F2", "convention", service_133, 'raise HTTPException(status_code=404, detail="dispute not found")',
                 convention="C6",
                 rationale="Service raises HTTPException; services raise app.errors domain errors.",
                 fix="raise errors.NotFound(...) / errors.Conflict(...) and let api/errors.py map them."),
        ],
        "should_not_flag": [
            trap("133-X1", service_133, "fee_cents = _fee_for(dispute.network) + apply_bps", noise="N4",
                 trap="Use Decimal for the percentage fee to avoid rounding errors.",
                 reject="Int minor units per ADR-007, apply_bps does the rounding. No Decimal."),
            trap("133-X2", service_133, "def _fee_for(network: str) -> int:", noise="N1",
                 trap="Add a docstring to _fee_for.",
                 reject="Private one-liner, no docstring needed."),
            trap("133-X3", tests_133, 'make_dispute(network="visa", amount_cents=20_000)', noise="N6",
                 trap="Replace 20_000 and 1_580 with named constants.",
                 reject="Keep literals inline in tests so the expected numbers are visible."),
        ],
    },
))

# --- #134 Payout schedule settings ----------------------------------------------------------------

service_134 = new_file("app/services/payout_schedules.py", '''
    import os

    from app import errors
    from app.db import session_scope
    from app.domain.models import PayoutSchedule
    from app.repos import payout_schedules as schedule_repo
    from app.utils import clock

    VALID_INTERVALS = {"daily", "weekly", "monthly"}
    MIN_PAYOUT_CENTS = int(os.environ.get("PAYOUT_MIN_CENTS", "1000"))


    def update_schedule(*, merchant_id: str, interval: str, minimum_cents: int) -> PayoutSchedule:
        if interval not in VALID_INTERVALS:
            raise errors.Conflict(f"unsupported payout interval {interval!r}")
        if minimum_cents < MIN_PAYOUT_CENTS:
            raise errors.Conflict(f"minimum payout is {MIN_PAYOUT_CENTS} cents")
        schedule = PayoutSchedule(
            merchant_id=merchant_id,
            interval=interval,
            minimum_cents=minimum_cents,
            updated_at=clock.now(),
        )
        with session_scope() as session:
            schedule_repo.upsert(session, schedule)
        return schedule
''')

tests_134 = new_file("tests/test_payout_schedules.py", '''
    import pytest

    from app import errors
    from app.domain.models import PayoutSchedule
    from app.services import payout_schedules


    def test_update_schedule_weekly(fake_repos, frozen_clock):
        schedule = payout_schedules.update_schedule(merchant_id="m_1", interval="weekly", minimum_cents=5_000)

        assert schedule == PayoutSchedule(
            merchant_id="m_1", interval="weekly", minimum_cents=5_000, updated_at=frozen_clock
        )


    def test_update_schedule_rejects_hourly(fake_repos):
        with pytest.raises(errors.Conflict):
            payout_schedules.update_schedule(merchant_id="m_1", interval="hourly", minimum_cents=5_000)
''')

register(PR(
    134, "Let merchants configure their payout schedule",
    '''
    Merchants can pick daily/weekly/monthly payouts and a minimum amount (PAY-102). The global
    minimum is configurable so ops can change it without a deploy.
    ''',
    "erin", "2026-08-17 11:10", [service_134, tests_134], labels=["payouts"],
    heldout={
        "should_flag": [
            flag("134-F1", "convention", service_134, 'MIN_PAYOUT_CENTS = int(os.environ.get(', convention="C10",
                 rationale="Reads os.environ directly in app/ instead of app.config.settings.",
                 fix="Add payout_min_cents to Settings and use settings.payout_min_cents."),
            flag("134-F2", "convention", tests_134, "def test_update_schedule_weekly(fake_repos, frozen_clock):",
                 convention="C8",
                 rationale="Test names don't follow test_<unit>__<scenario>__<expected>; model built inline instead of via factories.",
                 fix="Rename to test_update_schedule__weekly__returns_schedule and use a factory."),
        ],
        "should_not_flag": [
            trap("134-X1", service_134, "schedule_repo.upsert(session, schedule)", noise="N7",
                 trap="Wrap the upsert in try/except, log the failure and re-raise.",
                 reject="Domain errors and DB errors propagate to the middleware which logs them; no try/log/raise."),
            trap("134-X2", tests_134, "def test_update_schedule_rejects_hourly(fake_repos):", noise="N2",
                 trap="Type-annotate the test function and fixture.",
                 reject="Tests aren't typed here, mypy skips tests/."),
        ],
    },
))

# --- #135 Merchant refund webhooks ---------------------------------------------------------------

service_135 = new_file("app/services/merchant_webhooks.py", '''
    import hashlib
    import hmac

    import httpx

    from app.db import session_scope
    from app.domain.models import Refund
    from app.repos import merchants as merchant_repo
    from app.utils.logging import get_logger

    log = get_logger(__name__)


    def send_refund_webhook(refund: Refund, merchant_id: str) -> None:
        with session_scope() as session:
            merchant = merchant_repo.get(session, merchant_id)
        if merchant is None or not merchant.webhook_url:
            return
        payload = {
            "type": "refund.created",
            "refund_id": refund.id,
            "amount_cents": refund.amount_cents,
            "currency": refund.currency,
        }
        signature = _sign(str(payload), merchant.webhook_secret)
        response = httpx.post(
            merchant.webhook_url,
            json=payload,
            headers={"Acme-Signature": signature},
            timeout=5.0,
        )
        response.raise_for_status()
        log.info("refund_webhook_sent", refund_id=refund.id, merchant_email=merchant.email)


    def _sign(body: str, secret: str) -> str:
        return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
''')

tests_135 = new_file("tests/test_merchant_webhooks.py", '''
    from app.services import merchant_webhooks
    from tests.factories import make_merchant, make_refund


    def test_send_refund_webhook__merchant_with_url__posts_signed_payload(fake_repos, http_mock):
        fake_repos.merchants.add(
            make_merchant(id="m_1", webhook_url="https://merchant.example/hooks", webhook_secret="whsec_1")
        )
        http_mock.post("https://merchant.example/hooks").respond(200)

        merchant_webhooks.send_refund_webhook(make_refund(amount_cents=4_999), "m_1")

        assert http_mock.calls.call_count == 1
        assert http_mock.calls.last.request.headers["Acme-Signature"]
''')

register(PR(
    135, "Notify merchants of refunds via their webhook URL",
    '''
    Merchants with a webhook URL configured now get a signed `refund.created` event (API-77).
    Signature is HMAC-SHA256 with the merchant's webhook secret, same scheme as the docs describe.
    ''',
    "dan", "2026-08-21 09:50", [service_135, tests_135], labels=["webhooks", "refunds"],
    heldout={
        "should_flag": [
            flag("135-F1", "convention", service_135, "response = httpx.post(", "timeout=5.0,", convention="C3",
                 rationale="Raw httpx.post instead of a make_client-based client (ADR-006).",
                 fix="Build a merchant_webhooks client module on make_client and post through it."),
            flag("135-F2", "convention", service_135, 'log.info("refund_webhook_sent"', convention="C5",
                 rationale="Logs the merchant's email address unredacted (PII).",
                 fix="Drop merchant_email or wrap it in redact()."),
            flag("135-F3", "generic_bug", service_135, "signature = _sign(str(payload)",
                 rationale="Signs the Python repr of the dict, not the JSON bytes actually sent; merchants can't verify it.",
                 fix="Serialise once with json.dumps, sign those bytes and send them as content."),
        ],
        "should_not_flag": [
            trap("135-X1", tests_135, "make_refund(amount_cents=4_999)", noise="N6",
                 trap="Pull 4_999 into a named constant.",
                 reject="Test literals stay inline, that's our style."),
            trap("135-X2", service_135, "response.raise_for_status()", noise="N5",
                 trap="Merchant endpoints are unreliable, add retries with exponential backoff.",
                 reject="Retries belong in the shared client, not ad hoc at call sites (INC-12)."),
            trap("135-X3", service_135, "def _sign(body: str, secret: str) -> str:", noise="N1",
                 trap="Document _sign with a docstring.",
                 reject="No docstrings on private helpers."),
        ],
    },
))

# --- #136 Ledger reconciliation report ------------------------------------------------------------

service_136 = new_file("app/services/reconciliation.py", '''
    from datetime import datetime, timezone

    from app.clients import bank_statements
    from app.db import session_scope
    from app.repos import ledger as ledger_repo
    from app.domain.money import apply_bps
    from app.utils.logging import get_logger

    log = get_logger(__name__)

    TOLERANCE_BPS = 5


    def reconcile_day(account: str, day: str) -> dict[str, object]:
        statement_cents = bank_statements.closing_balance_cents(account, day)
        with session_scope() as session:
            ledger_cents = ledger_repo.balance_at_end_of(session, account, day)
        diff_cents = statement_cents - ledger_cents
        tolerance_cents = apply_bps(abs(ledger_cents), TOLERANCE_BPS)
        report = {
            "account": account,
            "day": day,
            "statement_cents": statement_cents,
            "ledger_cents": ledger_cents,
            "diff_cents": diff_cents,
            "within_tolerance": abs(diff_cents) <= tolerance_cents,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        log.info("reconciliation_completed", account=account, day=day, diff_cents=diff_cents)
        return report
''')

migration_136 = new_file("migrations/versions/0027_drop_ledger_legacy_ref.py", '''
    """drop ledger_entries.legacy_ref

    Revision ID: 0027
    Revises: 0026
    """

    import sqlalchemy as sa
    from alembic import op

    revision = "0027"
    down_revision = "0026"


    def upgrade() -> None:
        op.drop_column("ledger_entries", "legacy_ref")


    def downgrade() -> None:
        op.add_column("ledger_entries", sa.Column("legacy_ref", sa.String(), nullable=True))
''')

repo_136 = modified("app/repos/ledger.py", before='''
    def entries_for_reference(session: Session, reference: str) -> list[Row]:
        return session.execute(
            select(ledger_table).where(
                (ledger_table.c.reference == reference) | (ledger_table.c.legacy_ref == reference)
            )
        ).all()
''', after='''
    def entries_for_reference(session: Session, reference: str) -> list[Row]:
        return session.execute(
            select(ledger_table).where(ledger_table.c.reference == reference)
        ).all()
''', start=31)

register(PR(
    136, "Daily bank-vs-ledger reconciliation report",
    '''
    Compares the bank's closing balance with our ledger per account per day and flags differences
    above 5 bps (FIN-33). Also removes `legacy_ref`, which nothing has written since the ledger
    migration in May.
    ''',
    "bob", "2026-08-26 10:40", [service_136, migration_136, repo_136], labels=["ledger", "finance"],
    heldout={
        "should_flag": [
            flag("136-F1", "convention", service_136, '"generated_at": datetime.now(timezone.utc).isoformat(),',
                 convention="C7",
                 rationale="Reads wall-clock time directly instead of app.utils.clock.now() (not freezable in tests).",
                 fix='"generated_at": clock.now().isoformat()'),
            flag("136-F2", "convention", migration_136, 'op.drop_column("ledger_entries", "legacy_ref")',
                 convention="C9",
                 rationale="Drops a column in the same PR that changes the code reading it (ADR-008, INC-19).",
                 fix="Ship the repo change first; drop legacy_ref in a separate later PR."),
        ],
        "should_not_flag": [
            trap("136-X1", service_136, "statement_cents = bank_statements.closing_balance_cents", noise="N7",
                 trap="Wrap the bank call in try/except, log and re-raise with context.",
                 reject="Errors propagate to the job runner which logs them, don't add try/log/raise."),
            trap("136-X2", service_136, "from app.domain.money import apply_bps", noise="N3",
                 trap="Import order: app.domain should come before app.repos.",
                 reject="ruff handles import order, not a review comment."),
            trap("136-X3", service_136, "tolerance_cents = apply_bps(abs(ledger_cents), TOLERANCE_BPS)", noise="N4",
                 trap="Use Decimal for the tolerance percentage calculation.",
                 reject="We use int cents and apply_bps (ADR-007), no Decimal."),
        ],
    },
))
