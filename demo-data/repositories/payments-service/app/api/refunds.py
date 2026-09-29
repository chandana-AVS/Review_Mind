from fastapi import APIRouter

from app.api.schemas import CreateRefundRequest, RefundResponse
from app.services import refunds as refund_service
from app.utils.idempotency import idempotent

router = APIRouter(prefix="/refunds", tags=["refunds"])


@router.post("", response_model=RefundResponse, status_code=201)
@idempotent
def create_refund(body: CreateRefundRequest) -> RefundResponse:
    refund = refund_service.create_refund(
        charge_id=body.charge_id, amount_cents=body.amount_cents, reason=body.reason
    )
    return RefundResponse(**refund.as_dict())
