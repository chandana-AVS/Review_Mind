"""History PRs #115-#118: disputes (K3 again), health check (E1), error mapping, FX (K1/N4)."""

from lib import PR, c, label, modified, new_file, register, thread

# --- #115 Disputes service -------------------------------------------------------------------------

models_115 = modified("app/domain/models.py", before='''
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
''', after='''
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


    @dataclass(frozen=True, slots=True)
    class Dispute:
        id: str
        charge_id: str
        amount_cents: int
        currency: str
        reason_code: str
        status: str
        evidence_due_at: datetime
        opened_at: datetime
''', start=38)

disputes_115 = new_file("app/services/disputes.py", '''
    from datetime import timedelta

    from app import errors
    from app.db import session_scope
    from app.domain.models import Dispute
    from app.repos import charges as charge_repo
    from app.repos import disputes as dispute_repo
    from app.utils import clock
    from app.utils.ids import new_id
    from app.utils.logging import get_logger

    log = get_logger(__name__)

    EVIDENCE_WINDOW = timedelta(days=7)


    def open_dispute(*, charge_id: str, amount_cents: int, reason_code: str) -> Dispute:
        with session_scope() as session:
            charge = charge_repo.get(session, charge_id)
            if charge is None:
                raise errors.NotFound(f"charge {charge_id} not found")
            if amount_cents > charge.captured_cents:
                raise errors.Conflict("dispute amount exceeds captured amount")
            opened_at = clock.now()
            dispute = Dispute(
                id=new_id("dp"),
                charge_id=charge_id,
                amount_cents=amount_cents,
                currency=charge.currency,
                reason_code=reason_code,
                status="needs_response",
                evidence_due_at=opened_at + EVIDENCE_WINDOW,
                opened_at=opened_at,
            )
            dispute_repo.insert(session, dispute)
        log.info("dispute_opened", dispute_id=dispute.id, charge_id=charge_id, reason_code=reason_code)
        return dispute
''')

register(PR(
    115, "Disputes: open a dispute from the card-network notification",
    '''
    Models disputes (chargebacks) and adds `open_dispute`, called by the card-network notification
    consumer. Merchants get 7 days to submit evidence.

    Consumer wiring and the merchant notification email are the next PR.
    ''',
    "dan", "2026-03-30 10:40", [models_115, disputes_115], merged="2026-04-01 09:30",
    labels=["disputes"],
    reviews=[
        ("frank", "commented", "2026-03-30 14:10", "Some thoughts on the model."),
        ("alice", "approved", "2026-03-31 11:15", "Looks good."),
    ],
    threads=[
        thread(models_115, "class Dispute:", [
            c("frank", "2026-03-30 14:05", """
                reason_code only has ~20 valid values and amount must be positive. If Dispute were a Pydantic
                model you'd get that validation for free instead of trusting the caller."""),
            c("dan", "2026-03-30 15:20", "I went with a dataclass because of ADR-005, but I don't have a strong opinion. Alice?"),
            c("alice", "2026-03-31 11:10", """
                Dataclass is right. Per ADR-005 validation happens once at the edge; here the edge is the
                notification consumer, which already validates reason codes against the network's enum. We decided
                this in #109, not reopening it per PR."""),
        ], "rejected", "alice", "ADR-005: domain types are dataclasses; validation happens at the edge.",
            **label(conflict="K3", strength="enforced")),
        thread(disputes_115, "EVIDENCE_WINDOW = timedelta(days=7)", [
            c("alice", "2026-03-31 11:12", "Is 7 days right for all networks? I thought Amex was 10."),
            c("dan", "2026-03-31 13:00", "It's 7 for Visa/MC which is all we support right now. Added a TODO in the ticket for Amex."),
        ], "discussed", "alice", **label(signal=False, strength=None)),
    ],
))

# --- #116 Health check (E1) -----------------------------------------------------------------------

health_116 = new_file("app/api/health.py", '''
    from fastapi import APIRouter
    from sqlalchemy import text

    from app.db import session_scope

    router = APIRouter(tags=["ops"])


    @router.get("/healthz")
    def healthz() -> dict[str, str]:
        # ADR-003 exception: operational endpoints check infrastructure directly.
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok"}
''')

adr3_116 = modified("docs/adr/ADR-003-service-layer.md", before='''
    ## Consequences
    - Services are unit-testable without the HTTP layer.
''', after='''
    ## Consequences
    - Services are unit-testable without the HTTP layer.
    - Exception: operational endpoints (health, metrics) may touch infrastructure directly — they
      exist to test the infrastructure itself. (Added 2026-04-02, see #116.)
''', start=22)

register(PR(
    116, "Add /healthz with a database check",
    '''
    k8s readiness probe currently hits `/docs`, which says nothing about whether we can reach Postgres.
    `/healthz` runs `SELECT 1`. Probe config change is in the infra repo.
    ''',
    "bob", "2026-04-01 13:20", [health_116, adr3_116], merged="2026-04-02 15:00",
    labels=["ops"],
    reviews=[
        ("carol", "commented", "2026-04-01 16:05", "Question inline."),
        ("alice", "approved", "2026-04-02 10:40", ""),
    ],
    threads=[
        thread(health_116, "with session_scope() as session:", [
            c("carol", "2026-04-01 16:00", "Handler opening a session directly: isn't that exactly what ADR-003 says not to do? Should this be a `health` service?"),
            c("bob", "2026-04-01 17:15", "I went back and forth. A service that just runs SELECT 1 felt like ceremony."),
            c("alice", "2026-04-02 10:35", """
                Health and metrics endpoints are the one place where touching infrastructure directly is the point:
                they exist to check the DB/Redis connection itself. I've added that exception to ADR-003 in this
                PR so we don't have this discussion every time. Business endpoints still go through services."""),
        ], "rejected", "alice", "Operational endpoints (health/metrics) are an explicit exception to ADR-003.",
            **label("C2", exception="E1")),
    ],
))

# --- #117 Map domain errors to HTTP ---------------------------------------------------------------

charges_117 = modified("app/services/charges.py", before='''
    def capture_charge(*, charge_id: str, amount_cents: int) -> Charge:
        with session_scope() as session:
            charge = charge_repo.get(session, charge_id)
            if charge is None:
                raise errors.NotFound(f"charge {charge_id} not found")
            charge_repo.mark_captured(session, charge.id, amount_cents, clock.now())
        return charge
''', after='''
    def capture_charge(*, charge_id: str, amount_cents: int) -> Charge:
        with session_scope() as session:
            charge = charge_repo.get(session, charge_id)
            if charge is None:
                raise errors.NotFound(f"charge {charge_id} not found")
            if charge.status != "authorized":
                raise errors.Conflict(f"charge {charge_id} is {charge.status}, expected authorized")
            if amount_cents > charge.amount_cents:
                raise errors.Conflict("capture amount exceeds authorized amount")
            charge_repo.mark_captured(session, charge.id, amount_cents, clock.now())
        return charge
''', start=11)

api_errors_117 = new_file("app/api/errors.py", '''
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    from app import errors
    from app.utils.logging import get_logger

    log = get_logger(__name__)

    _STATUS = {
        errors.NotFound: 404,
        errors.Conflict: 409,
        errors.PaymentDeclined: 402,
        errors.RefundExceedsCapture: 422,
        errors.ProviderUnavailable: 503,
    }


    def register(app: FastAPI) -> None:
        @app.exception_handler(errors.DomainError)
        async def _domain_error(request: Request, exc: errors.DomainError) -> JSONResponse:
            status = _STATUS.get(type(exc), 400)
            log.warning("domain_error", code=exc.code, path=request.url.path, status=status)
            return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=status)
''')

main_117 = modified("app/main.py", before='''
    from fastapi import FastAPI

    from app.api import health, payouts, refunds

    app = FastAPI(title="payments-service")
    for module in (health, refunds, payouts):
        app.include_router(module.router)
''', after='''
    from fastapi import FastAPI

    from app.api import errors, health, payouts, refunds

    app = FastAPI(title="payments-service")
    errors.register(app)
    for module in (health, refunds, payouts):
        app.include_router(module.router)
''')

register(PR(
    117, "Map domain errors to HTTP responses in one place",
    '''
    Adds a single exception handler that maps `app.errors.DomainError` subclasses to status codes,
    and makes `capture_charge` validate status/amount with domain errors.

    Before this, a mix of handlers caught errors and built responses by hand, and 409 vs 422 was
    inconsistent across endpoints (API-41).
    ''',
    "carol", "2026-04-08 09:30", [charges_117, api_errors_117, main_117], merged="2026-04-10 12:00",
    labels=["api"],
    reviews=[
        ("alice", "changes_requested", "2026-04-08 13:40", "One layering issue."),
        ("frank", "commented", "2026-04-08 15:20", ""),
        ("alice", "approved", "2026-04-09 10:10", "👍"),
    ],
    threads=[
        thread(charges_117, 'raise errors.Conflict(f"charge {charge_id} is {charge.status}', [
            c("alice", "2026-04-08 13:35", """
                The first commit raised `HTTPException(status_code=409)` here. Services shouldn't know about HTTP:
                they're also called from the payout worker and the CLI, where an HTTPException makes no sense.
                Raise the domain error and let the handler in api/errors.py pick the status."""),
            c("carol", "2026-04-08 14:20", "Makes sense, that's the whole point of this PR, embarrassing 🙂. Switched to errors.Conflict."),
        ], "applied", "alice", **label("C6", mistake="M6")),
        thread(api_errors_117, "from fastapi.responses import JSONResponse", [
            c("frank", "2026-04-08 15:15", "Imports aren't grouped the way I'd expect: stdlib, third-party, local, each alphabetised. Can you reorder?"),
            c("carol", "2026-04-08 16:00", """
                ruff's isort rule and ruff-format run in pre-commit and CI, so whatever lands is what the tool
                wants. I'd rather not hand-tune import order in review."""),
            c("alice", "2026-04-09 10:05", "Agreed, formatting is the tool's job, not the reviewer's."),
        ], "rejected", "carol", "Import order is enforced by ruff in pre-commit; not a review topic.",
            **label(noise="N3")),
    ],
))

# --- #118 Currency conversion for payouts (K1 / N4) ------------------------------------------------

fx_118 = new_file("app/services/fx.py", '''
    from app import errors
    from app.clients import fx_rates
    from app.domain.money import exponent, round_half_even_div

    RATE_SCALE = 1_000_000  # rates come from the provider as integer micro-units


    def convert(amount_cents: int, source: str, target: str) -> int:
        """Convert minor units of `source` into minor units of `target`."""
        if source == target:
            return amount_cents
        rate_micro = fx_rates.get_rate_micro(source, target)
        if rate_micro <= 0:
            raise errors.InvalidCurrency(f"no rate for {source}->{target}")
        numerator = amount_cents * rate_micro * 10 ** exponent(target)
        denominator = RATE_SCALE * 10 ** exponent(source)
        return round_half_even_div(numerator, denominator)


    def payout_amount(balance_cents: int, balance_currency: str, payout_currency: str) -> int:
        converted = convert(balance_cents, balance_currency, payout_currency)
        return converted - _conversion_fee(converted)


    def _conversion_fee(amount_cents: int) -> int:
        return round_half_even_div(amount_cents * 50, 10_000)
''')

rates_118 = new_file("app/clients/fx_rates.py", '''
    from app.clients.http import make_client
    from app.config import settings

    _client = make_client("fx", settings.fx_base_url)


    def get_rate_micro(source: str, target: str) -> int:
        response = _client.get("/v1/rates", params={"from": source, "to": target})
        response.raise_for_status()
        return int(response.json()["rate_micro"])
''')

register(PR(
    118, "FX conversion for cross-currency payouts",
    '''
    Merchants with a GBP balance can now be paid out in EUR. Conversion uses the FX service's
    integer micro-rates and our half-even rounding helper; a 0.5% conversion fee is deducted.

    Checked against 200 historical conversions from finance's spreadsheet: all match to the minor unit.
    ''',
    "dan", "2026-04-15 11:05", [fx_118, rates_118], merged="2026-04-17 10:20",
    labels=["payouts", "money"],
    reviews=[
        ("frank", "commented", "2026-04-15 14:30", "Worried about precision here."),
        ("bob", "changes_requested", "2026-04-15 17:10", "One real bug (fixed now?) and a reply to Frank."),
        ("bob", "approved", "2026-04-16 12:25", "Nice work on the spreadsheet check."),
    ],
    threads=[
        thread(fx_118, "numerator = amount_cents * rate_micro * 10 ** exponent(target)", [
            c("frank", "2026-04-15 14:25", """
                FX is exactly where you want Decimal. Integer micro-rates and hand-written rounding feels fragile;
                `Decimal(amount) * Decimal(rate)` with a quantize is clearer and is what every FX library does."""),
            c("bob", "2026-04-15 17:00", """
                We moved away from Decimal on purpose in ADR-007 (#112). Everything is int minor units and rounding
                happens once, in round_half_even_div, so the ledger and the bank agree to the unit. Dan's
                spreadsheet check is the proof. Introducing Decimal here would reintroduce the int<->Decimal
                boundary we just removed."""),
            c("frank", "2026-04-16 09:40", "OK, if finance signed off on the numbers."),
        ], "rejected", "bob", "ADR-007: money stays int minor units; round_half_even_div is the single rounding point.",
            **label("C1", noise="N4", conflict="K1")),
        thread(fx_118, "def _conversion_fee(amount_cents: int) -> int:", [
            c("bob", "2026-04-15 17:05", """
                The first version had `int(amount_cents * 0.005)`. That's float maths on money and it truncates
                instead of rounding. Please use the helper (`apply_bps(amount_cents, 50)` reads best) or at
                least integer maths."""),
            c("dan", "2026-04-16 10:15", "Oops, leftover from my prototype. Switched to integer maths with round_half_even_div."),
        ], "applied", "bob", **label("C1")),
    ],
))
