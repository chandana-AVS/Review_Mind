from fastapi import APIRouter

from app.api.schemas import CreatePayoutRequest, PayoutResponse
from app.services import payouts as payout_service
from app.utils.idempotency import idempotent

router = APIRouter(prefix="/payouts", tags=["payouts"])


@router.post("", response_model=PayoutResponse, status_code=201)
@idempotent
def create_payout(body: CreatePayoutRequest) -> PayoutResponse:
    payout = payout_service.create_payout(
        merchant_id=body.merchant_id, amount_cents=body.amount_cents, currency=body.currency
    )
    return PayoutResponse(**payout.as_dict())
