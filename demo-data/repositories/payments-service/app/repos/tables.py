from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table

metadata = MetaData()

charges_table = Table(
    "charges", metadata,
    Column("id", String, primary_key=True),
    Column("merchant_id", String, nullable=False),
    Column("amount_cents", BigInteger, nullable=False),
    Column("captured_cents", BigInteger, nullable=False, default=0),
    Column("refunded_cents", BigInteger, nullable=False, default=0),
    Column("currency", String(3), nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

refunds_table = Table(
    "refunds", metadata,
    Column("id", String, primary_key=True),
    Column("charge_id", String, nullable=False),
    Column("amount_cents", BigInteger, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("status", String, nullable=False),
    Column("reason", String, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

payouts_table = Table(
    "payouts", metadata,
    Column("id", String, primary_key=True),
    Column("merchant_id", String, nullable=False),
    Column("amount_cents", BigInteger, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
