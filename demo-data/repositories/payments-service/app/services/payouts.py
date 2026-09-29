from app.clients import bank_transfers
from app.db import session_scope
from app.domain.models import Payout
from app.repos import payouts as payout_repo
from app.utils import clock
from app.utils.ids import new_id
from app.utils.logging import get_logger

log = get_logger(__name__)


def create_payout(*, merchant_id: str, amount_cents: int, currency: str) -> Payout:
    payout = Payout(
        id=new_id("po"),
        merchant_id=merchant_id,
        amount_cents=amount_cents,
        currency=currency,
        status="pending",
        created_at=clock.now(),
    )
    with session_scope() as session:
        payout_repo.insert(session, payout)
    bank_transfers.submit(payout)
    log.info("payout_submitted", payout_id=payout.id, merchant_id=merchant_id)
    return payout
