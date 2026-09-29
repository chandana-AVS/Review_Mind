from datetime import UTC, datetime

from app.domain.models import Charge, Payout, Refund


def make_charge(**overrides):
    defaults = dict(
        id="ch_test_1", merchant_id="m_1", amount_cents=10_000, captured_cents=10_000,
        refunded_cents=0, currency="EUR", status="captured",
        created_at=datetime(2026, 1, 15, tzinfo=UTC),
    )
    return Charge(**{**defaults, **overrides})


def make_refund(**overrides):
    defaults = dict(
        id="re_test_1", charge_id="ch_test_1", amount_cents=2_500, currency="EUR",
        status="pending", reason=None, created_at=datetime(2026, 1, 15, tzinfo=UTC),
    )
    return Refund(**{**defaults, **overrides})


def make_payout(**overrides):
    defaults = dict(
        id="po_test_1", merchant_id="m_1", amount_cents=50_000, currency="EUR",
        status="pending", created_at=datetime(2026, 1, 15, tzinfo=UTC),
    )
    return Payout(**{**defaults, **overrides})
