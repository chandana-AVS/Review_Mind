from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Charge:
    id: str
    merchant_id: str
    amount_cents: int
    captured_cents: int
    refunded_cents: int
    currency: str
    status: str
    created_at: datetime

    @property
    def refundable_cents(self) -> int:
        return self.captured_cents - self.refunded_cents


@dataclass(frozen=True, slots=True)
class Refund:
    id: str
    charge_id: str
    amount_cents: int
    currency: str
    status: str
    reason: str | None
    created_at: datetime

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Payout:
    id: str
    merchant_id: str
    amount_cents: int
    currency: str
    status: str
    created_at: datetime

    def as_dict(self) -> dict[str, object]:
        return asdict(self)
