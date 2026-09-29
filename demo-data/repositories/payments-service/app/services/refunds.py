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
            raise errors.RefundExceedsCapture(
                f"requested {amount_cents}, refundable {charge.refundable_cents}"
            )
        refund = Refund(
            id=new_id("re"),
            charge_id=charge_id,
            amount_cents=amount_cents,
            currency=charge.currency,
            status="pending",
            reason=reason,
            created_at=clock.now(),
        )
        refund_repo.insert(session, refund)
        charge_repo.add_refunded(session, charge_id, amount_cents)
    log.info("refund_created", refund_id=refund.id, charge_id=charge_id, amount_cents=amount_cents)
    return refund
