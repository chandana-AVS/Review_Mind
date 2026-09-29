"""History PRs #101-#107 (January - early February 2026).

Codebase state: stdlib logging, ORM models in app/models.py, money as float until #103 and
Decimal afterwards, card-network client on `requests` (#102), no shared HTTP client yet.
"""

from lib import PR, c, label, modified, new_file, register, thread

# --------------------------------------------------------------------------------------------
# #101 alice: Introduce service layer (ADR-003)
# --------------------------------------------------------------------------------------------

adr003 = new_file("docs/adr/ADR-003-service-layer.md", '''
    # ADR-003: Service layer between API handlers and persistence

    - Status: Accepted
    - Date: 2026-01-08
    - Deciders: alice, bob
    - PR: #101

    ## Context
    Handlers in `app/api/` grew to 150+ lines, mixing request parsing, SQL and provider calls. Two
    refund bugs in December came from duplicated query logic in two handlers.

    ## Decision
    - `app/api/` handlers only: validate input, call one service function, map the result/errors.
    - Handlers never receive or open a DB `Session`. Services get a session through
      `app.db.session_scope()`; queries live in `app/repos/`.
    - Services are plain functions/classes with no FastAPI imports.

    ## Consequences
    - Services are unit-testable without the HTTP layer.
    - Existing handlers move over one resource at a time; new endpoints start on the new layout.
''')

contributing = new_file("CONTRIBUTING.md", '''
    # Contributing to payments-service

    ## Layout
    - `app/api/` — FastAPI routers. Keep handlers thin: parse the request, call a service, map the
      result. Handlers must not open DB sessions or run queries; that belongs in `app/services/`
      and `app/repos/` (see ADR-003).
    - `app/services/` — business logic.
    - `app/repos/` — SQLAlchemy queries. Keep them seperate from business logic.

    ## Tests
    - Name tests `test_<unit>__<scenario>__<expected>`, e.g.
      `test_capture__already_captured__raises_conflict`.
    - Build objects with the factories in `tests/factories.py` instead of constructing models inline.

    ## Tooling
    - `pre-commit install` — ruff lint + format run on every commit.
''')

db = modified("app/db.py", before='''
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.config import settings

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
''', after='''
    from collections.abc import Iterator
    from contextlib import contextmanager

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.config import settings

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)


    @contextmanager
    def session_scope() -> Iterator[Session]:
        session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
''')

CHARGES_V1 = '''
    import logging

    from app import errors
    from app.db import session_scope
    from app.models import Charge
    from app.repos import charges as charge_repo
    from app.utils import clock
    from app.utils.ids import new_id

    logger = logging.getLogger(__name__)


    def create_charge(*, merchant_id: str, amount: float, currency: str) -> Charge:
        with session_scope() as session:
            charge = Charge(
                id=new_id("ch"),
                merchant_id=merchant_id,
                amount=amount,
                captured_amount=0.0,
                refunded_amount=0.0,
                currency=currency,
                status="authorized",
                created_at=clock.now(),
            )
            charge_repo.insert(session, charge)
        logger.info("charge created id=%s merchant=%s", charge.id, merchant_id)
        return charge


    def capture_charge(*, charge_id: str) -> Charge:
        with session_scope() as session:
            charge = charge_repo.get(session, charge_id)
            if charge is None:
                raise errors.NotFound(f"charge {charge_id} not found")
            charge_repo.mark_captured(session, charge.id, charge.amount, clock.now())
        return charge
'''

charges_svc = new_file("app/services/charges.py", CHARGES_V1)

register(PR(
    101, "Introduce service layer (ADR-003)",
    '''
    Proposes ADR-003: handlers in `app/api/` stop talking to the database. They validate, call one
    service function and map the result. Sessions come from the new `app.db.session_scope()`.

    - `app/services/charges.py` is the first service (create + capture), lifted out of
      `app/api/charges.py`. Switching the handler over is a follow-up so this stays reviewable.
    - Adds a first CONTRIBUTING.md with the layout and test conventions we keep repeating in reviews.

    Background: the two December refund bugs (duplicated query logic in two handlers).
    ''',
    "alice", "2026-01-06 09:30", [adr003, contributing, db, charges_svc], merged="2026-01-08 15:10",
    labels=["architecture"], adr="ADR-003",
    reviews=[
        ("carol", "approved", "2026-01-06 13:05", "Big +1, the refunds handler was getting impossible to test."),
        ("bob", "approved", "2026-01-07 10:22", "Agree with the direction. One question inline about ops endpoints, not blocking."),
    ],
    threads=[
        thread(adr003, "Handlers never receive or open a DB `Session`", [
            c("bob", "2026-01-06 16:40", """
                Fully agree with this line, it's what went wrong in both December refund bugs. One
                question before it becomes a rule: we'll want a health endpoint at some point that does
                a `SELECT 1` against postgres and pings Redis. Wrapping that in a service feels like
                ceremony. Do we carve that out now or deal with it later?"""),
            c("alice", "2026-01-07 09:12", """
                Later. We don't have a health endpoint yet and I don't want the first version of the ADR
                to start with an exception. Default is no sessions in handlers, full stop. When someone
                actually builds the health check we'll look at it and amend the ADR if it makes sense."""),
            c("bob", "2026-01-07 10:18", "Works for me."),
        ], "discussed", "alice", **label("C2")),
        thread(contributing, "Keep them seperate from business logic", [
            c("carol", "2026-01-06 12:58", "Tiny typo: seperate -> separate."),
            c("alice", "2026-01-06 13:30", "Fixed, thanks."),
        ], "noise"),
    ],
))

# --------------------------------------------------------------------------------------------
# #102 dan: Card-network status client
# --------------------------------------------------------------------------------------------

card_network = new_file("app/clients/card_network.py", '''
    import logging

    import requests

    from app.config import settings

    logger = logging.getLogger(__name__)

    KNOWN_STATUSES = {"operational", "degraded", "outage"}


    def network_status(network: str) -> str:
        response = requests.get(
            f"{settings.card_network_base_url}/v1/networks/{network}/status",
            timeout=3,
        )
        response.raise_for_status()
        return _parse_status(response.json())


    def _parse_status(payload: dict[str, object]) -> str:
        status = str(payload.get("status", "")).lower()
        if status not in KNOWN_STATUSES:
            logger.warning("unknown card network status %r", status)
            return "degraded"
        return status
''')

config_102 = modified("app/config.py", before='''
    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="PAYMENTS_", env_file=".env")

        database_url: str = "postgresql+psycopg://localhost/payments"
        redis_url: str = "redis://localhost:6379/0"
        stripe_api_key: str = ""


    settings = Settings()
''', after='''
    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="PAYMENTS_", env_file=".env")

        database_url: str = "postgresql+psycopg://localhost/payments"
        redis_url: str = "redis://localhost:6379/0"
        stripe_api_key: str = ""
        card_network_base_url: str = "https://status.cardnet.example"


    settings = Settings()
''', start=4)

test_card_network = new_file("tests/test_card_network.py", '''
    from app.clients import card_network


    def test_parse_status__known_status__returns_lowercased():
        assert card_network._parse_status({"status": "Operational"}) == "operational"


    def test_parse_status__unknown_status__falls_back_to_degraded():
        assert card_network._parse_status({"status": "maintenance"}) == "degraded"
''')

register(PR(
    102, "Card-network status client",
    '''
    Adds `app/clients/card_network.py` so we can check whether Visa/Mastercard report an outage
    before we blame our own authorizations. For now it's a single `network_status(network)` call
    against their public status API; ops wants to show it on the dashboard next.

    Unknown statuses fall back to `degraded` so a format change on their side doesn't crash us.

    Testing: unit tests for the parsing; called the real status API from a shell.
    ''',
    "dan", "2026-01-09 10:05", [card_network, config_102, test_card_network],
    merged="2026-01-12 11:40", labels=["integrations"],
    reviews=[
        ("frank", "commented", "2026-01-09 14:20", "A couple of notes inline."),
        ("carol", "approved", "2026-01-09 16:02", "Nice and small, thanks Dan."),
        ("bob", "approved", "2026-01-12 09:15", "Fine for now, see the thread about the HTTP library."),
    ],
    threads=[
        thread(card_network, "import requests", [
            c("carol", "2026-01-09 15:48", """
                Question more than a request: we already get httpx through the FastAPI test client. Is
                there a reason to pull in `requests` too, or should the clients all standardise on one
                library?"""),
            c("dan", "2026-01-09 17:10", "Honestly just habit, `requests` is what I know. Happy to switch if we have a preference."),
            c("bob", "2026-01-12 09:05", """
                requests is fine for now. This is one GET against a status page with a timeout, it can't
                hurt anything. We'll standardise outbound HTTP later: fraud scoring and FX are both
                coming this quarter, and I'd rather design one shared client with timeouts, retries and
                metrics once we know what those need than guess now. Leave it as is."""),
            c("dan", "2026-01-12 09:30", "Ok, leaving it. Timeout is already set to 3s."),
        ], "discussed", "bob", **label("C3", conflict="K2", strength="superseded")),
        thread(card_network, "def _parse_status(", [
            c("frank", "2026-01-09 14:18", """
                Please add a docstring here explaining the fallback behaviour. Every function should say
                what it does, especially when it silently maps unknown values to something else."""),
            c("carol", "2026-01-09 15:55", """
                I'd skip it. Private helpers are documented by their names, and this one is five lines
                you can read in one glance. We only put docstrings on the public service functions,
                otherwise they rot the first time someone edits the helper. The fallback is covered by
                the second test, which is a better description than a docstring anyway."""),
            c("frank", "2026-01-10 08:40", "Not how I'd do it, but it's your codebase."),
        ], "rejected", "carol",
            "Private helpers are documented by their names; we only document public service functions.",
            **label(noise="N1")),
    ],
))

# --------------------------------------------------------------------------------------------
# #103 carol: Store charge amounts precisely
# --------------------------------------------------------------------------------------------

models_103 = modified("app/models.py", before='''
    class Charge(Base):
        __tablename__ = "charges"

        id: Mapped[str] = mapped_column(String, primary_key=True)
        merchant_id: Mapped[str] = mapped_column(String, nullable=False)
        amount: Mapped[float] = mapped_column(Float, nullable=False)
        captured_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0)
        refunded_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0)
        currency: Mapped[str] = mapped_column(String(3), nullable=False)
        status: Mapped[str] = mapped_column(String, nullable=False)
''', after='''
    class Charge(Base):
        __tablename__ = "charges"

        id: Mapped[str] = mapped_column(String, primary_key=True)
        merchant_id: Mapped[str] = mapped_column(String, nullable=False)
        amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), nullable=False)
        captured_amount: Mapped[float] = mapped_column(
            Numeric(12, 2, asdecimal=False), nullable=False, default=0
        )
        refunded_amount: Mapped[float] = mapped_column(
            Numeric(12, 2, asdecimal=False), nullable=False, default=0
        )
        currency: Mapped[str] = mapped_column(String(3), nullable=False)
        status: Mapped[str] = mapped_column(String, nullable=False)
''', start=14)

migration_0004 = new_file("migrations/versions/0004_charge_amounts_numeric.py", '''
    """charges: amounts from float to numeric(12, 2)

    Revision ID: 0004
    Revises: 0003
    """

    import sqlalchemy as sa
    from alembic import op

    revision = "0004"
    down_revision = "0003"

    COLUMNS = ("amount", "captured_amount", "refunded_amount")


    def upgrade() -> None:
        for column in COLUMNS:
            op.alter_column(
                "charges",
                column,
                type_=sa.Numeric(12, 2),
                postgresql_using=f"round({column}::numeric, 2)",
            )


    def downgrade() -> None:
        for column in COLUMNS:
            op.alter_column("charges", column, type_=sa.Float())
''')

charges_103 = modified("app/services/charges.py", before=CHARGES_V1, after=CHARGES_V1.replace(
    "            amount=amount,\n", "            amount=round(amount, 2),\n"))

register(PR(
    103, "Store charge amounts precisely",
    '''
    Charge amounts are `Float` columns today, and finance found a merchant whose December settlement
    was off by 0.01 after summing a few thousand captures. This moves the three amount columns on
    `charges` to `NUMERIC(12, 2)` and rounds incoming amounts to 2 dp in `create_charge`.

    Migration rounds existing values in place. Ran it against a copy of prod: no row changes by more
    than 0.005.

    Refunds table follows in a separate PR once this has been live for a few days.
    ''',
    "carol", "2026-01-14 10:20", [models_103, migration_0004, charges_103],
    merged="2026-01-16 14:05", labels=["money", "database"],
    reviews=[
        ("bob", "changes_requested", "2026-01-14 15:40", "Column change is right, but we shouldn't convert back to float in Python. Details inline."),
        ("bob", "approved", "2026-01-15 17:22", "Thanks, Decimal all the way through looks good."),
        ("alice", "approved", "2026-01-16 10:03", ""),
    ],
    threads=[
        thread(models_103, "amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False)", [
            c("bob", "2026-01-14 15:32", """
                Float will bite us here even with a numeric column. `asdecimal=False` hands the value back
                to Python as a float, so every sum in settlement goes through binary floating point again
                and we're back to the 0.01 drift finance found. Same for `round(amount, 2)` in the service:
                `round(2.675, 2)` is 2.67, not 2.68.

                Please drop `asdecimal=False`, type these as `Mapped[Decimal]`, and have the services take
                and return `Decimal` (quantize to `Decimal("0.01")` on the way in). The API schema can
                keep accepting JSON numbers for now, pydantic will coerce."""),
            c("carol", "2026-01-14 16:50", """
                I kept float to avoid touching every call site, but you're right that it only moves the
                problem. There are only three callers of create_charge, I'll switch them."""),
            c("carol", "2026-01-15 14:10", "Done: Decimal in the model and services, quantize in create_charge, tests updated."),
            c("bob", "2026-01-15 17:20", "Looks good."),
        ], "applied", "bob", **label("C1", conflict="K1", strength="superseded")),
        thread(None, None, [
            c("ci-bot", "2026-01-14 10:31", "Alembic check: 1 new revision (0004), single head. Upgrade/downgrade round-trip passed on the test database."),
        ], "noise"),
    ],
))

# --------------------------------------------------------------------------------------------
# #104 bob: Partial refunds endpoint
# --------------------------------------------------------------------------------------------

refunds_api = new_file("app/api/refunds.py", '''
    from fastapi import APIRouter, HTTPException

    from app.api.schemas import CreateRefundRequest, RefundResponse
    from app.db import session_scope
    from app.repos import charges as charge_repo
    from app.services import refunds as refund_service

    router = APIRouter(prefix="/charges", tags=["refunds"])


    @router.post("/{charge_id}/refunds", response_model=RefundResponse, status_code=201)
    def create_refund(charge_id: str, body: CreateRefundRequest) -> RefundResponse:
        with session_scope() as session:
            charge = charge_repo.get(session, charge_id)
            if charge is None:
                raise HTTPException(status_code=404, detail="charge not found")
            refund = refund_service.create_refund(session, charge, body.amount, body.reason)
        return RefundResponse.model_validate(refund, from_attributes=True)
''')

refunds_svc = new_file("app/services/refunds.py", '''
    import logging
    from decimal import Decimal

    from sqlalchemy.orm import Session

    from app import errors
    from app.models import Charge, Refund
    from app.repos import charges as charge_repo
    from app.repos import refunds as refund_repo
    from app.utils import clock
    from app.utils.ids import new_id

    logger = logging.getLogger(__name__)


    def create_refund(session: Session, charge: Charge, amount: Decimal, reason: str | None) -> Refund:
        refundable = _refundable(charge)
        if amount > refundable:
            raise errors.RefundExceedsCapture(f"requested {amount}, refundable {refundable}")
        refund = Refund(
            id=new_id("re"),
            charge_id=charge.id,
            amount=amount,
            currency=charge.currency,
            status="pending",
            reason=reason,
            created_at=clock.now(),
        )
        refund_repo.insert(session, refund)
        charge_repo.add_refunded(session, charge.id, amount)
        logger.info("refund created id=%s charge=%s amount=%s", refund.id, charge.id, amount)
        return refund


    def _refundable(charge: Charge) -> Decimal:
        return charge.captured_amount - charge.refunded_amount
''')

schemas_104 = modified("app/api/schemas.py", before='''
    class CreateChargeRequest(BaseModel):
        merchant_id: str
        amount: Decimal = Field(gt=0)
        currency: str = Field(min_length=3, max_length=3)
''', after='''
    class CreateChargeRequest(BaseModel):
        merchant_id: str
        amount: Decimal = Field(gt=0)
        currency: str = Field(min_length=3, max_length=3)


    class CreateRefundRequest(BaseModel):
        amount: Decimal = Field(gt=0, decimal_places=2)
        reason: str | None = None


    class RefundResponse(BaseModel):
        id: str
        charge_id: str
        amount: Decimal
        currency: str
        status: str
''', start=6)

register(PR(
    104, "Partial refunds endpoint",
    '''
    `POST /charges/{charge_id}/refunds` with an amount, so support can refund part of a capture
    instead of voiding the whole charge. Multiple partial refunds are allowed until the refundable
    amount (captured - refunded) is used up; beyond that we raise `RefundExceedsCapture`.

    Amounts are `Decimal` end to end, same as charges since #103.

    Testing: service tests for exact, partial and over-refund cases; tried it on staging with a
    test card.
    ''',
    "bob", "2026-01-20 09:45", [refunds_api, refunds_svc, schemas_104], merged="2026-01-22 16:30",
    labels=["refunds", "api"],
    reviews=[
        ("frank", "commented", "2026-01-20 13:10", "Looks reasonable. One doc comment."),
        ("alice", "changes_requested", "2026-01-20 15:25", "Logic is fine, but the handler is doing the service's job. See inline."),
        ("alice", "approved", "2026-01-21 17:48", "Thanks Bob."),
    ],
    threads=[
        thread(refunds_api, "with session_scope() as session:", [
            c("alice", "2026-01-20 15:20", """
                This is the pattern ADR-003 is meant to stop: the handler opens the session, runs the
                charge lookup and passes the session into the service. Please move the lookup and the
                session into `refund_service.create_refund(charge_id=..., amount=..., reason=...)` and
                have the handler just call it. Missing charge should be `errors.NotFound` from the
                service; the handler shouldn't need `charge_repo` at all."""),
            c("bob", "2026-01-20 16:45", """
                I passed the session in so the lookup, the insert and the `refunded_amount` update share
                one transaction. But the service can do all three inside its own `session_scope()`, so
                there's no reason for the handler to own it. Moving it."""),
            c("bob", "2026-01-21 11:02", "Done. Handler is now four lines; the service owns the session and raises NotFound."),
            c("alice", "2026-01-21 17:45", "Perfect, that's the shape I want the rest of the handlers to end up in."),
        ], "applied", "alice", **label("C2", mistake="M4")),
        thread(refunds_svc, "def _refundable(", [
            c("frank", "2026-01-20 13:08", "Add a docstring to `_refundable`, e.g. whether it accounts for pending refunds. Future readers won't know."),
            c("bob", "2026-01-20 16:52", """
                Same answer as on #102: private helpers are documented by their names, and we only write
                docstrings for the public service functions. It's one subtraction; if the semantics
                change (say pending refunds get excluded) the name and the tests change with it."""),
        ], "rejected", "bob",
            "Private helpers are documented by their names; we only document public service functions.",
            **label(noise="N1")),
    ],
))

# --------------------------------------------------------------------------------------------
# #105 dan: Daily settlement report
# --------------------------------------------------------------------------------------------

settlement = new_file("app/services/settlement.py", '''
    import logging
    from datetime import datetime, timedelta
    from decimal import Decimal

    from app.repos import charges as charge_repo
    from app.db import session_scope
    from app.repos import refunds as refund_repo

    logger = logging.getLogger(__name__)


    def daily_settlement(merchant_id: str) -> dict[str, object]:
        """Totals for the previous UTC day: captured, refunded and net amount for one merchant."""
        end = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        start = end - timedelta(days=1)
        with session_scope() as session:
            charges = charge_repo.captured_between(session, merchant_id, start, end)
            refunds = refund_repo.created_between(session, merchant_id, start, end)
        gross = sum((c.captured_amount for c in charges), Decimal("0"))
        refunded = sum((r.amount for r in refunds), Decimal("0"))
        logger.info("settlement report merchant=%s charges=%d", merchant_id, len(charges))
        return {
            "merchant_id": merchant_id,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "gross": str(gross),
            "refunded": str(refunded),
            "net": str(gross - refunded),
        }
''')

test_settlement = new_file("tests/test_settlement.py", '''
    from datetime import datetime, timedelta
    from decimal import Decimal

    from app.services import settlement
    from tests.factories import make_charge, make_refund


    def test_daily_settlement__capture_and_refund__reports_net(fake_repos):
        yesterday = datetime.utcnow() - timedelta(days=1)
        fake_repos.charges.add(make_charge(captured_amount=Decimal("100.00"), captured_at=yesterday))
        fake_repos.refunds.add(make_refund(amount=Decimal("25.00"), created_at=yesterday))

        report = settlement.daily_settlement("m_1")

        assert report["net"] == "75.00"
''')

register(PR(
    105, "Daily settlement report",
    '''
    Finance asked for a per-merchant daily settlement summary (gross captured, refunded, net) for
    the previous UTC day, so they can stop exporting it from Metabase by hand.

    This adds the service function only; the endpoint and the nightly job come next.

    Testing: one service test with fake repos.
    ''',
    "dan", "2026-01-27 10:12", [settlement, test_settlement], merged="2026-01-29 12:20",
    labels=["reporting"],
    reviews=[
        ("frank", "commented", "2026-01-27 11:40", ""),
        ("bob", "changes_requested", "2026-01-27 14:30", "Needs to go through the clock so the date window is testable."),
        ("bob", "approved", "2026-01-28 16:05", "Much better test now."),
        ("alice", "approved", "2026-01-29 09:50", ""),
    ],
    threads=[
        thread(settlement, "end = datetime.utcnow()", [
            c("bob", "2026-01-27 14:25", """
                Please use `clock.now()` from `app.utils.clock` here instead of `datetime.utcnow()`. Two
                reasons. Tests freeze time through the `frozen_clock` fixture, which only works if the
                code asks the clock; that's why your test has to compute `yesterday` from utcnow itself,
                and it will fail if CI runs it across midnight. And `utcnow()` returns a naive datetime
                while `captured_at` is timezone-aware, so the comparison in the repo query will blow up
                on postgres."""),
            c("dan", "2026-01-27 15:40", "Didn't know about the fixture. So the test should use `frozen_clock` and fixed dates instead?"),
            c("bob", "2026-01-27 15:58", "Yes. The fixture freezes to 2026-01-15 12:00, so put the factory objects on the 14th and the window is deterministic."),
            c("dan", "2026-01-28 11:20", "Switched to `clock.now()` and rewrote the test with `frozen_clock`."),
        ], "applied", "bob", **label("C7", mistake="M2")),
        thread(settlement, "from app.db import session_scope", [
            c("frank", "2026-01-27 11:38", "Imports are out of order here, `app.db` should come before `app.repos`."),
            c("alice", "2026-01-27 12:15", """
                Let's not spend review comments on import order. ruff and ruff-format run in pre-commit
                and fix this automatically. Dan, looks like the hook isn't installed on your machine:
                `pre-commit install` and it'll sort itself out on the next commit."""),
            c("dan", "2026-01-27 12:40", "Oops, new laptop. Installed."),
        ], "rejected", "alice",
            "ruff and ruff-format run in pre-commit; formatting isn't a review topic.",
            **label(noise="N3")),
    ],
))

# --------------------------------------------------------------------------------------------
# #106 carol: Partial captures
# --------------------------------------------------------------------------------------------

charges_106 = modified("app/services/charges.py", before='''
        logger.info("charge created id=%s merchant=%s", charge.id, merchant_id)
        return charge


    def capture_charge(*, charge_id: str) -> Charge:
        with session_scope() as session:
            charge = charge_repo.get(session, charge_id)
            if charge is None:
                raise errors.NotFound(f"charge {charge_id} not found")
            charge_repo.mark_captured(session, charge.id, charge.amount, clock.now())
        return charge
''', after='''
        logger.info("charge created id=%s merchant=%s", charge.id, merchant_id)
        return charge


    def capture_charge(*, charge_id: str, amount: Decimal | None = None) -> Charge:
        """Capture an authorized charge in full, or partially when `amount` is given."""
        with session_scope() as session:
            charge = charge_repo.get(session, charge_id)
            if charge is None:
                raise errors.NotFound(f"charge {charge_id} not found")
            to_capture = charge.amount if amount is None else amount
            remaining = float(charge.amount) - float(charge.captured_amount)
            if float(to_capture) > remaining:
                raise errors.Conflict(f"capture {to_capture} exceeds remaining {remaining}")
            charge_repo.mark_captured(session, charge.id, to_capture, clock.now())
        return charge
''', start=29)

test_charges = new_file("tests/test_charges.py", '''
    from datetime import UTC, datetime
    from decimal import Decimal

    import pytest

    from app import errors
    from app.models import Charge
    from app.services import charges


    def _charge():
        return Charge(
            id="ch_1", merchant_id="m_1", amount=Decimal("100.00"),
            captured_amount=Decimal("0"), refunded_amount=Decimal("0"),
            currency="EUR", status="authorized", created_at=datetime(2026, 1, 30, tzinfo=UTC),
        )


    def test_partial_capture(fake_repos, frozen_clock):
        fake_repos.charges.add(_charge())

        charges.capture_charge(charge_id="ch_1", amount=Decimal("40.00"))

        assert fake_repos.charges.get("ch_1").captured_amount == Decimal("40.00")


    def test_capture_too_much(fake_repos, frozen_clock):
        fake_repos.charges.add(_charge())

        with pytest.raises(errors.Conflict):
            charges.capture_charge(charge_id="ch_1", amount=Decimal("100.01"))
''')

register(PR(
    106, "Partial captures",
    '''
    Marketplace merchants ship orders in several parcels and want to capture per parcel.
    `capture_charge` now takes an optional `amount`; without it we capture the full amount as before.
    Capturing more than the remaining authorized amount raises `Conflict`.

    The API change (`amount` in the capture body) comes in a follow-up once product signs off on
    the error messages.
    ''',
    "carol", "2026-02-02 09:55", [charges_106, test_charges], merged="2026-02-04 10:15",
    labels=["money", "charges"],
    reviews=[
        ("alice", "changes_requested", "2026-02-02 14:02", "Please don't reintroduce float, see inline."),
        ("bob", "commented", "2026-02-02 16:30", "Tests need the usual naming and factories."),
        ("alice", "approved", "2026-02-03 17:40", "Good to go."),
    ],
    threads=[
        thread(charges_106, "remaining = float(charge.amount) - float(charge.captured_amount)", [
            c("alice", "2026-02-02 13:58", """
                Keep Decimal end-to-end. We just spent #103 getting floats out of the charge path, and
                these `float()` casts put them straight back into the one comparison that decides
                whether a capture goes through: `float(Decimal("0.3")) - float(Decimal("0.1"))` is not
                0.2. Both sides are already Decimal from the model, so just subtract them."""),
            c("carol", "2026-02-02 15:12", "Leftover from debugging the comparison with a float in the test. Removing the casts."),
            c("carol", "2026-02-03 10:05", "Done, plain Decimal arithmetic now and the error message prints the Decimal."),
        ], "applied", "alice", **label("C1", conflict="K1", strength="superseded")),
        thread(test_charges, "def test_partial_capture(", [
            c("bob", "2026-02-02 16:26", """
                Could you rename these to the `test_<unit>__<scenario>__<expected>` form from
                CONTRIBUTING, e.g. `test_capture_charge__partial_amount__records_captured_amount` and
                `test_capture_charge__exceeds_remaining__raises_conflict`? When one fails in CI the
                name alone should tell you what broke. And please use `make_charge(...)` from
                `tests/factories.py` instead of the local `_charge()` builder. Every test file growing
                its own builder is how we ended up with six slightly different fake charges last year."""),
            c("carol", "2026-02-03 09:40", 'Fair on both. `make_charge` just needs `status="authorized"` and `captured_amount=Decimal("0")` overrides.'),
            c("carol", "2026-02-03 10:06", "Renamed and switched to the factory."),
        ], "applied", "bob", **label("C8")),
    ],
))

# --------------------------------------------------------------------------------------------
# #107 alice: Idempotency keys (ADR-004)
# --------------------------------------------------------------------------------------------

adr004 = new_file("docs/adr/ADR-004-idempotency-keys.md", '''
    # ADR-004: Idempotency keys for money-moving endpoints

    - Status: Accepted
    - Date: 2026-02-04
    - Deciders: alice, bob, carol
    - PR: #107

    ## Context
    Mobile clients retry on timeouts. In January a retried `POST /refunds` produced a duplicate
    refund of EUR 1,240 that ops had to claw back manually.

    ## Decision
    - Every `POST` endpoint that creates or moves money (charges, captures, refunds, payouts,
      transfers) is decorated with `@idempotent` from `app.utils.idempotency`.
    - Clients send an `Idempotency-Key` header; the decorator stores the key + response for 24 h and
      replays the stored response on repeats. Missing key → 400.

    ## Consequences
    - Read-only endpoints and internal admin endpoints do not need it.
''')

idempotency = new_file("app/utils/idempotency.py", '''
    """@idempotent decorator for money-moving POST endpoints (ADR-004)."""

    import functools
    from collections.abc import Callable
    from typing import Any

    from fastapi import HTTPException, Request

    from app.utils.idempotency_store import load_response, save_response


    def idempotent(handler: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(handler)
        def wrapper(*args: Any, request: Request, **kwargs: Any) -> Any:
            key = request.headers.get("Idempotency-Key")
            if not key:
                raise HTTPException(status_code=400, detail="Idempotency-Key header required")
            stored = load_response(request.url.path, key)
            if stored is not None:
                return stored
            response = handler(*args, **kwargs)
            save_response(request.url.path, key, response)
            return response

        return wrapper
''')

idempotency_store = new_file("app/utils/idempotency_store.py", '''
    import json
    from typing import Any

    import redis
    from fastapi.encoders import jsonable_encoder

    from app.config import settings

    TTL_SECONDS = 24 * 60 * 60

    _redis = redis.Redis.from_url(settings.redis_url)


    def load_response(path: str, key: str) -> Any | None:
        raw = _redis.get(f"idem:{path}:{key}")
        return json.loads(raw) if raw is not None else None


    def save_response(path: str, key: str, response: Any) -> None:
        payload = json.dumps(jsonable_encoder(response))
        _redis.set(f"idem:{path}:{key}", payload, ex=TTL_SECONDS)
''')

refunds_api_107 = modified("app/api/refunds.py", before='''
    from fastapi import APIRouter

    from app.api.schemas import CreateRefundRequest, RefundResponse
    from app.services import refunds as refund_service

    router = APIRouter(prefix="/charges", tags=["refunds"])


    @router.post("/{charge_id}/refunds", response_model=RefundResponse, status_code=201)
    def create_refund(charge_id: str, body: CreateRefundRequest) -> RefundResponse:
''', after='''
    from fastapi import APIRouter

    from app.api.schemas import CreateRefundRequest, RefundResponse
    from app.services import refunds as refund_service
    from app.utils.idempotency import idempotent

    router = APIRouter(prefix="/charges", tags=["refunds"])


    @router.post("/{charge_id}/refunds", response_model=RefundResponse, status_code=201)
    @idempotent
    def create_refund(charge_id: str, body: CreateRefundRequest) -> RefundResponse:
''')

register(PR(
    107, "Idempotency keys for money-moving endpoints (ADR-004)",
    '''
    After the duplicate EUR 1,240 refund last month (the mobile app retried a timed-out
    refund request), this proposes ADR-004: every POST that creates or moves money requires an
    `Idempotency-Key` header.

    - `@idempotent` in `app/utils/idempotency.py` stores key + response in Redis for 24 h and
      replays it on repeats. Missing key is a 400.
    - Applied to the refunds endpoint. Charges and captures follow once the mobile team ships the
      header (their release is 02-09).
    ''',
    "alice", "2026-02-04 11:00", [adr004, idempotency, idempotency_store, refunds_api_107],
    merged="2026-02-06 15:35", labels=["architecture", "api"], adr="ADR-004",
    reviews=[
        ("bob", "approved", "2026-02-04 15:10", "Yes please. This would have saved ops an afternoon."),
        ("carol", "approved", "2026-02-05 10:30", "Approving, one question inline for the record."),
        ("dan", "commented", "2026-02-05 11:45", ""),
    ],
    threads=[
        thread(adr004, "is decorated with `@idempotent` from `app.utils.idempotency`", [
            c("carol", "2026-02-05 10:22", """
                For the record, what about inbound webhooks? We'll have the Stripe receiver at some point
                and those are POSTs that move money too (payout.paid, charge.refunded). Stripe won't send
                our `Idempotency-Key` header, so this decorator would 400 every event."""),
            c("alice", "2026-02-05 11:05", """
                Right, webhooks are a different case. This rule is about our own clients retrying our
                API. Provider webhooks don't carry our key, but every Stripe event has a unique event id,
                so the receiver dedupes on that instead. We'll write that down when we build the receiver.
                For every endpoint our clients call, the rule is simple: moves money, gets `@idempotent`."""),
            c("carol", "2026-02-05 11:20", "Makes sense, thanks."),
        ], "discussed", "alice", **label("C4")),
        thread(idempotency, "raise HTTPException(status_code=400", [
            c("dan", "2026-02-05 11:40", """
                Will the 400 break older app versions that don't send the header yet? Or is the mobile
                release going out before this merges?"""),
            c("alice", "2026-02-05 13:15", """
                Only refunds is decorated in this PR. The mobile app has sent the header on refunds since
                4.12, and older versions are under 2% of refund traffic; a 400 for them beats another
                duplicate refund. Charges and captures wait for the 02-09 release."""),
            c("dan", "2026-02-05 13:30", "Got it, thanks."),
        ], "discussed", "alice"),
    ],
))
