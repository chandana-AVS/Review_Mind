"""History PRs #111, #112, #114: payouts endpoint, ADR-007 minor units, structured logging."""

from lib import PR, c, label, modified, new_file, register, thread

# --- #111 Payouts endpoint -----------------------------------------------------------------------

api_111 = new_file("app/api/payouts.py", '''
    from fastapi import APIRouter

    from app.api.schemas import CreatePayoutRequest, PayoutResponse
    from app.services import payouts as payout_service
    from app.utils.idempotency import idempotent

    router = APIRouter(prefix="/payouts", tags=["payouts"])


    @router.post("", response_model=PayoutResponse, status_code=201)
    @idempotent
    def create_payout(body: CreatePayoutRequest) -> PayoutResponse:
        payout = payout_service.create_payout(
            merchant_id=body.merchant_id, amount=body.amount, currency=body.currency
        )
        return PayoutResponse(**payout.as_dict())
''')

service_111 = new_file("app/services/payouts.py", '''
    import logging
    from decimal import Decimal

    from app.clients import bank_transfers
    from app.db import session_scope
    from app.domain.models import Payout
    from app.repos import payouts as payout_repo
    from app.utils import clock
    from app.utils.ids import new_id

    logger = logging.getLogger(__name__)


    def create_payout(*, merchant_id: str, amount: Decimal, currency: str) -> Payout:
        payout = Payout(
            id=new_id("po"),
            merchant_id=merchant_id,
            amount=amount,
            currency=currency,
            status="pending",
            created_at=clock.now(),
        )
        with session_scope() as session:
            payout_repo.insert(session, payout)
        bank_transfers.submit(payout)
        return payout
''')

tests_111 = new_file("tests/test_payouts.py", '''
    from decimal import Decimal

    from app.services import payouts
    from tests.factories import make_merchant


    def test_create_payout__valid_request__returns_pending_payout(fake_repos, fake_bank, frozen_clock):
        fake_repos.merchants.add(make_merchant(id="m_1"))

        payout = payouts.create_payout(merchant_id="m_1", amount=Decimal("500.00"), currency="EUR")

        assert payout.status == "pending"
        assert fake_bank.submitted == [payout]


    def test_create_payout__bank_submission__uses_payout_currency(fake_repos, fake_bank):
        fake_repos.merchants.add(make_merchant(id="m_1"))

        payouts.create_payout(merchant_id="m_1", amount=Decimal("10.00"), currency="GBP")

        assert fake_bank.submitted[0].currency == "GBP"
''')

register(PR(
    111, "Payouts: POST /payouts",
    '''
    First slice of merchant payouts (PAY-88). Creates a pending payout and submits it to the bank
    transfer client. Settlement status updates come in a follow-up via the bank webhook.
    ''',
    "carol", "2026-03-02 10:20", [api_111, service_111, tests_111], merged="2026-03-04 09:45",
    labels=["payouts"],
    reviews=[
        ("bob", "changes_requested", "2026-03-02 14:05", "Blocking on idempotency, rest looks good."),
        ("frank", "commented", "2026-03-02 16:30", "Minor stuff on the tests."),
        ("bob", "approved", "2026-03-03 11:20", "Thanks!"),
    ],
    threads=[
        thread(api_111, "@idempotent", [
            c("bob", "2026-03-02 14:02", """
                This moves money, so it needs `@idempotent` (ADR-004). Without it a mobile retry after a timeout
                creates a second payout, the same way we double-refunded in January."""),
            c("carol", "2026-03-02 15:10", "Argh, I even reviewed the ADR. Added, plus a test for the replay path."),
        ], "applied", "bob", **label("C4", mistake="M5")),
        thread(tests_111, "def test_create_payout__valid_request__returns_pending_payout", [
            c("frank", "2026-03-02 16:25", "These test functions and the fixtures should have type annotations. mypy strict everywhere or it's not strict."),
            c("carol", "2026-03-02 17:40", """
                We deliberately don't type tests. mypy excludes tests/ in pyproject, and fixture types add a lot
                of noise for no real safety. Leaving as is."""),
            c("bob", "2026-03-03 11:18", "Agree with Carol."),
        ], "rejected", "carol", "We don't type tests; mypy excludes tests/ on purpose.",
            **label(noise="N2")),
    ],
))

# --- #112 ADR-007 integer minor units -------------------------------------------------------------

adr_007 = new_file("docs/adr/ADR-007-integer-minor-units.md", '''
    # ADR-007: Money as integer minor units

    - Status: Accepted. Supersedes the "use Decimal" guidance from #103 and #106
    - Date: 2026-03-10
    - Deciders: alice, bob, carol

    ## Context
    Since moving from float to Decimal in January: Decimal values serialise inconsistently
    ("10.50" vs 10.5), the ledger and Stripe (integer minor units) disagree on rounding for JPY/KWD,
    and three bugs came from Decimal + float mixing.

    ## Decision
    - Money is `int` minor units: `amount_cents: int` + `currency: str`, whatever the exponent.
    - No `Decimal` or `float` for money in `app/`. Fee maths uses `app.domain.money` helpers.
    - Display formatting happens only in API response schemas.
''')

money_112 = new_file("app/domain/money.py", '''
    """Integer minor-unit money helpers (ADR-007)."""

    ZERO_DECIMAL_CURRENCIES = frozenset({"JPY", "KRW", "VND"})
    THREE_DECIMAL_CURRENCIES = frozenset({"KWD", "BHD", "OMR"})


    def round_half_even_div(numerator: int, denominator: int) -> int:
        quotient, remainder = divmod(numerator, denominator)
        twice = remainder * 2
        if twice > denominator or (twice == denominator and quotient % 2 == 1):
            quotient += 1
        return quotient


    def apply_bps(amount_cents: int, bps: int) -> int:
        """Return amount * bps / 10_000, rounded half-even. 1 bps = 0.01%."""
        return round_half_even_div(amount_cents * bps, 10_000)


    def exponent(currency: str) -> int:
        if currency in ZERO_DECIMAL_CURRENCIES:
            return 0
        if currency in THREE_DECIMAL_CURRENCIES:
            return 3
        return 2
''')

migration_112 = new_file("migrations/versions/0012_amounts_to_minor_units.py", '''
    """amounts to integer minor units (ADR-007)

    Revision ID: 0012
    Revises: 0011
    """

    import sqlalchemy as sa
    from alembic import op

    revision = "0012"
    down_revision = "0011"


    def upgrade() -> None:
        for table in ("charges", "refunds", "payouts"):
            op.add_column(table, sa.Column("amount_cents", sa.BigInteger(), nullable=True))
            op.execute(f"UPDATE {table} SET amount_cents = ROUND(amount * 100)")


    def downgrade() -> None:
        for table in ("charges", "refunds", "payouts"):
            op.drop_column(table, "amount_cents")
''')

fees_112 = modified("app/services/fees.py", before='''
    from decimal import ROUND_HALF_EVEN, Decimal

    PLATFORM_FEE_RATE = Decimal("0.029")


    def platform_fee(amount: Decimal) -> Decimal:
        return (amount * PLATFORM_FEE_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
''', after='''
    from app.domain.money import apply_bps

    PLATFORM_FEE_BPS = 290


    def platform_fee(amount_cents: int) -> int:
        return apply_bps(amount_cents, PLATFORM_FEE_BPS)
''')

register(PR(
    112, "ADR-007: store and compute money as integer minor units",
    '''
    Proposal agreed at last week's arch sync. TL;DR: `amount_cents: int` everywhere, no Decimal.

    Why we're reversing the January move to Decimal:
    - JSON: Decimal serialises as "10.50" in some paths and 10.5 in others; two merchants' reconciliation broke
    - Stripe and our bank partners speak integer minor units; we converted back and forth at every edge
    - JPY/KWD rounding disagreed between the ledger and Stripe
    - three bugs from mixing Decimal and float

    This PR: ADR, `app/domain/money.py` helpers, migration 0012 (expand step: adds `amount_cents`;
    the old columns are dropped later), and converts `fees.py` as the first caller. Services move over
    in follow-ups this sprint.
    ''',
    "bob", "2026-03-09 10:12", [adr_007, money_112, migration_112, fees_112],
    merged="2026-03-11 16:40", labels=["architecture", "money"], adr="ADR-007",
    reviews=[
        ("frank", "commented", "2026-03-09 13:55", "Not convinced, see inline."),
        ("alice", "approved", "2026-03-10 10:30", "Approved, one request inline."),
        ("carol", "approved", "2026-03-10 11:00", "Finally. 🎉"),
    ],
    threads=[
        thread(fees_112, "PLATFORM_FEE_BPS = 290", [
            c("frank", "2026-03-09 13:50", """
                Why not keep Decimal and just fix the serialisation? Decimal is the textbook answer for money.
                Integer cents gets awkward the moment you have fractional fees or FX."""),
            c("bob", "2026-03-09 15:30", """
                The textbook answer assumes one system. We talk to Stripe and two banks, and they are all integer
                minor units. Every Decimal<->int conversion at the edge was a chance to round differently, and that's
                where the JPY/KWD mismatches came from. Fractional fees are basis points on an int with explicit
                half-even rounding (`apply_bps`), so the rounding happens in exactly one place. FX will do the same."""),
            c("alice", "2026-03-10 10:20", "We discussed this at length at the sync. Going with integer minor units; it's in the ADR."),
        ], "rejected", "bob",
            "Superseded by ADR-007: money is int minor units, fee maths via apply_bps with explicit rounding.",
            **label("C1", noise="N4", conflict="K1")),
        thread(money_112, "def apply_bps(amount_cents: int, bps: int) -> int:", [
            c("alice", "2026-03-10 10:28", "Can we make `apply_bps` the only way to do percentage maths on money? I'd rather people didn't write `amount * 29 // 1000` inline and pick their own rounding."),
            c("bob", "2026-03-10 12:05", "Yes. Added a docstring and called it out in the ADR. Reviewers should push back on inline percentage maths."),
        ], "applied", "alice", **label("C1")),
    ],
))

# --- #114 Structured logging -----------------------------------------------------------------------

logging_114 = new_file("app/utils/logging.py", '''
    """structlog setup. Use snake_case event names and keyword fields; no f-string messages."""

    import re

    import structlog

    _EMAIL = re.compile(r"[^@\\s]+@[^@\\s]+")
    _PAN = re.compile(r"\\b(\\d{6})\\d{3,9}(\\d{4})\\b")


    def get_logger(name: str) -> structlog.stdlib.BoundLogger:
        return structlog.get_logger(name)


    def redact(value: str) -> str:
        """Mask emails and card numbers before logging."""
        value = _EMAIL.sub("***@***", value)
        return _PAN.sub(r"\\1******\\2", value)
''')

refunds_114 = modified("app/services/refunds.py", before='''
    import logging

    from app import errors
    from app.db import session_scope
    from app.domain.models import Refund
    from app.repos import charges as charge_repo
    from app.repos import refunds as refund_repo
    from app.utils import clock
    from app.utils.ids import new_id

    logger = logging.getLogger(__name__)


    def create_refund(*, charge_id: str, amount_cents: int, reason: str | None) -> Refund:
        with session_scope() as session:
            charge = charge_repo.get(session, charge_id)
            if charge is None:
                raise errors.NotFound(f"charge {charge_id} not found")
            if amount_cents > charge.refundable_cents:
                logger.warning("Refund of %s rejected for charge %s", amount_cents, charge_id)
                raise errors.RefundExceedsCapture(
                    f"requested {amount_cents}, refundable {charge.refundable_cents}"
                )
''', after='''
    from app import errors
    from app.db import session_scope
    from app.domain.models import Refund
    from app.repos import charges as charge_repo
    from app.repos import refunds as refund_repo
    from app.utils import clock
    from app.utils.ids import new_id
    from app.utils.logging import get_logger

    log = get_logger(__name__)


    def create_refund(*, charge_id: str, amount_cents: int, reason: str | None) -> Refund:
        with session_scope() as session:
            charge = charge_repo.get(session, charge_id)
            if charge is None:
                raise errors.NotFound(f"charge {charge_id} not found")
            if amount_cents > charge.refundable_cents:
                log.warning(
                    "refund_rejected_exceeds_capture",
                    charge_id=charge_id,
                    amount_cents=amount_cents,
                    refundable_cents=charge.refundable_cents,
                )
                raise errors.RefundExceedsCapture(
                    f"requested {amount_cents}, refundable {charge.refundable_cents}"
                )
''')

register(PR(
    114, "Structured logging (structlog) for the refunds service",
    '''
    Introduces `app.utils.logging` (structlog + `redact()` for PII) and converts the refunds
    service as the pilot. Datadog can now facet on `charge_id` / `amount_cents` instead of us
    regex-parsing messages.

    Convention proposal: snake_case event name as the message, everything else as keyword fields.
    ''',
    "carol", "2026-03-23 09:50", [logging_114, refunds_114], merged="2026-03-25 14:15",
    labels=["observability"],
    reviews=[
        ("bob", "changes_requested", "2026-03-23 13:30", "Love this. One thing on the event name."),
        ("alice", "approved", "2026-03-24 10:05", "LGTM. Let's make this the standard."),
        ("bob", "approved", "2026-03-24 11:40", ""),
    ],
    threads=[
        thread(refunds_114, '"refund_rejected_exceeds_capture",', [
            c("bob", "2026-03-23 13:25", """
                The first version had `log.warning(f"Refund of {amount_cents} rejected for charge {charge_id}")`.
                With an f-string the message is different every time, so Datadog can't group it and we're back to
                regexes. Event name should be a constant snake_case string, values as kwargs."""),
            c("carol", "2026-03-23 15:00", "Yep, muscle memory. Fixed, and I'll put the rule in the module docstring."),
            c("alice", "2026-03-24 10:02", "+1, and anything with customer data goes through `redact()`. Let's hold every new log line to this."),
        ], "applied", "bob", **label("C5", mistake="M3")),
        thread(None, None, [
            c("ci-bot", "2026-03-23 10:02", "Coverage report: 90.8% (+0.0%)."),
        ], "noise"),
    ],
))
