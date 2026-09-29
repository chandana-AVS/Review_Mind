from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Charge
from app.repos.tables import charges_table


def get(session: Session, charge_id: str) -> Charge | None:
    row = session.execute(select(charges_table).where(charges_table.c.id == charge_id)).first()
    return Charge(**row._mapping) if row else None


def add_refunded(session: Session, charge_id: str, amount_cents: int) -> None:
    session.execute(
        charges_table.update()
        .where(charges_table.c.id == charge_id)
        .values(refunded_cents=charges_table.c.refunded_cents + amount_cents)
    )
