import pytest

from app import errors
from app.services import refunds
from tests.factories import make_charge


def test_create_refund__within_capture__returns_pending_refund(fake_repos, frozen_clock):
    fake_repos.charges.add(make_charge(captured_cents=10_000))

    refund = refunds.create_refund(charge_id="ch_test_1", amount_cents=2_500, reason=None)

    assert refund.status == "pending"
    assert refund.amount_cents == 2_500


def test_create_refund__exceeds_capture__raises_refund_exceeds_capture(fake_repos):
    fake_repos.charges.add(make_charge(captured_cents=1_000))

    with pytest.raises(errors.RefundExceedsCapture):
        refunds.create_refund(charge_id="ch_test_1", amount_cents=1_001, reason=None)
