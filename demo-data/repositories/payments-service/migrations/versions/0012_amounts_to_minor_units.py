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
