from pydantic import BaseModel, Field


class CreateRefundRequest(BaseModel):
    charge_id: str
    amount_cents: int = Field(gt=0)
    reason: str | None = None


class RefundResponse(BaseModel):
    id: str
    charge_id: str
    amount_cents: int
    currency: str
    status: str


class CreatePayoutRequest(BaseModel):
    merchant_id: str
    amount_cents: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)


class PayoutResponse(BaseModel):
    id: str
    merchant_id: str
    amount_cents: int
    currency: str
    status: str
