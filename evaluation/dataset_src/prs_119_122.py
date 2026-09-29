"""History PRs #119-#122: Stripe webhook (E2), invoice PDFs (K4), ADR-008 (C9), payment methods."""

from lib import PR, c, label, modified, new_file, register, thread

# --- #119 Stripe webhook receiver (E2) -----------------------------------------------------------

api_119 = new_file("app/api/webhooks.py", '''
    import stripe
    from fastapi import APIRouter, Header, Request

    from app.config import settings
    from app.services import webhooks as webhook_service

    router = APIRouter(prefix="/webhooks", tags=["webhooks"])


    @router.post("/stripe", status_code=204)
    async def stripe_webhook(request: Request, stripe_signature: str = Header()) -> None:
        # Not @idempotent: Stripe retries with the same event id and the service dedupes on it.
        payload = await request.body()
        event = stripe.Webhook.construct_event(payload, stripe_signature, settings.stripe_webhook_secret)
        webhook_service.handle_stripe_event(event)
''')

service_119 = new_file("app/services/webhooks.py", '''
    import stripe

    from app.db import session_scope
    from app.repos import processed_events
    from app.services import charges as charge_service
    from app.utils.logging import get_logger

    log = get_logger(__name__)

    _HANDLERS = {
        "charge.captured": charge_service.on_provider_captured,
        "charge.refunded": charge_service.on_provider_refunded,
    }


    def handle_stripe_event(event: stripe.Event) -> None:
        handler = _HANDLERS.get(event.type)
        if handler is None:
            log.info("stripe_event_ignored", event_id=event.id, event_type=event.type)
            return
        with session_scope() as session:
            if not processed_events.claim(session, provider="stripe", event_id=event.id):
                log.info("stripe_event_duplicate", event_id=event.id)
                return
            handler(session, event.data.object)
        log.info("stripe_event_processed", event_id=event.id, event_type=event.type)
''')

register(PR(
    119, "Stripe webhook receiver for capture/refund events",
    '''
    Receives Stripe `charge.captured` / `charge.refunded` so our state catches up when ops act
    in the Stripe dashboard directly. Signature is verified; events are deduplicated in
    `processed_events` (unique on provider + event id) inside the same transaction as the update.
    ''',
    "alice", "2026-04-22 10:00", [api_119, service_119], merged="2026-04-23 16:45",
    labels=["integrations", "webhooks"],
    reviews=[
        ("dan", "commented", "2026-04-22 13:15", "Question about idempotency."),
        ("bob", "approved", "2026-04-22 17:30", "Claiming the event in the same transaction is the right call."),
    ],
    threads=[
        thread(api_119, "async def stripe_webhook(", [
            c("dan", "2026-04-22 13:10", "This is a POST that changes money state. Shouldn't it have @idempotent like the other ones (ADR-004)?"),
            c("alice", "2026-04-22 14:00", """
                Good question, and why I left the comment above it. @idempotent keys on the Idempotency-Key
                header, which Stripe doesn't send. Stripe retries with the same event id, so we dedupe on that in
                `processed_events.claim`, in the same transaction as the state change. Inbound provider webhooks
                are the exception; our own client-facing POSTs still need the decorator."""),
            c("dan", "2026-04-22 14:12", "Makes sense, thanks!"),
        ], "rejected", "alice", "Provider webhooks dedupe on the provider event id instead of @idempotent.",
            **label("C4", exception="E2")),
        thread(None, None, [
            c("ci-bot", "2026-04-22 10:12", "Coverage report: 91.0% (+0.3%)."),
        ], "noise"),
    ],
))

# --- #120 Invoice PDFs (K4 + utcnow) ------------------------------------------------------------

invoices_120 = new_file("app/services/invoices.py", '''
    from app import errors
    from app.clients import pdf_renderer
    from app.db import session_scope
    from app.repos import invoices as invoice_repo
    from app.repos import merchants as merchant_repo
    from app.utils import clock
    from app.utils.logging import get_logger

    log = get_logger(__name__)


    def generate_monthly_invoice(merchant_id: str, year: int, month: int) -> str:
        with session_scope() as session:
            merchant = merchant_repo.get(session, merchant_id)
            if merchant is None:
                raise errors.NotFound(f"merchant {merchant_id} not found")
            lines = invoice_repo.fee_lines(session, merchant_id, year, month)
            total_cents = sum(line.fee_cents for line in lines)
            document = {
                "merchant": {"id": merchant.id, "legal_name": merchant.legal_name, "vat_id": merchant.vat_id},
                "period": f"{year}-{month:02d}",
                "issued_at": clock.now().isoformat(),
                "currency": merchant.settlement_currency,
                "lines": [
                    {"description": line.description, "count": line.count, "fee_cents": line.fee_cents}
                    for line in lines
                ],
                "total_cents": total_cents,
            }
            pdf_key = pdf_renderer.render("monthly_invoice", document)
            invoice_repo.insert(session, merchant_id, year, month, total_cents, pdf_key)
        log.info("invoice_generated", merchant_id=merchant_id, period=document["period"])
        return pdf_key
''')

register(PR(
    120, "Monthly invoice PDFs for merchants",
    '''
    Generates a monthly fee invoice per merchant (FIN-19). Line items come from the fee ledger;
    rendering is done by the shared pdf-renderer service. The cron wiring is in the jobs repo.
    ''',
    "dan", "2026-04-28 09:20", [invoices_120], merged="2026-04-30 11:30",
    labels=["billing"],
    reviews=[
        ("frank", "changes_requested", "2026-04-28 12:00", "Function is too long."),
        ("carol", "approved", "2026-04-29 10:50", "Approving, see threads."),
    ],
    threads=[
        thread(invoices_120, "def generate_monthly_invoice(", [
            c("frank", "2026-04-28 11:55", """
                This function is ~30 lines. Functions should be under 20 lines, full stop. Split out
                `_build_document` and `_persist` at least."""),
            c("dan", "2026-04-28 14:30", """
                I'd rather not. It reads top to bottom as one transaction, and splitting it means passing the
                session and half the locals around. Nothing in CONTRIBUTING mentions a line limit."""),
            c("carol", "2026-04-29 10:40", "I agree with Dan. It's one cohesive unit and easy to follow. We don't have a length rule; we split when something gets hard to read."),
            c("frank", "2026-04-29 11:05", "Your call, I still think it's too long."),
        ], "rejected", "carol", "No line-length rule for functions here; this function is cohesive and readable.",
            **label(conflict="K4", strength="not_a_rule")),
        thread(invoices_120, '"issued_at": clock.now().isoformat(),', [
            c("carol", "2026-04-29 10:45", "This was `datetime.utcnow()` in the first version. Please use `clock.now()`: it's timezone-aware, and the invoice tests freeze the clock, so utcnow made them flaky around midnight."),
            c("dan", "2026-04-29 11:30", "Changed, same thing Bob caught in my settlement PR (#105). I need a pre-commit hook for this 😅"),
        ], "applied", "carol", **label("C7", mistake="M2")),
    ],
))

# --- #121 ADR-008 expand/contract -----------------------------------------------------------------

adr_008 = new_file("docs/adr/ADR-008-expand-contract-migrations.md", '''
    # ADR-008: Expand/contract database migrations

    - Status: Accepted
    - Date: 2026-05-12
    - Deciders: alice, bob

    ## Context
    INC-19 (2026-05-07): a hotfix dropped `charges.legacy_status` in the same deploy as the code that
    stopped reading it. During the rolling deploy old pods crashed for 6 minutes.

    ## Decision
    - A migration must work with both the currently deployed code and the new code.
    - Renames and drops happen in steps: expand (add) -> write both -> backfill -> switch reads ->
      contract (drop) in a separate, later PR.
    - A PR that drops or renames a column must not change application code in the same PR.
''')

migration_121 = new_file("migrations/versions/0019_restore_legacy_status.py", '''
    """restore charges.legacy_status (INC-19 follow-up)

    Revision ID: 0019
    Revises: 0018
    """

    import sqlalchemy as sa
    from alembic import op

    revision = "0019"
    down_revision = "0018"


    def upgrade() -> None:
        op.add_column("charges", sa.Column("legacy_status", sa.String(), nullable=True))
        op.execute("UPDATE charges SET legacy_status = status WHERE legacy_status IS NULL")


    def downgrade() -> None:
        op.drop_column("charges", "legacy_status")
''')

contributing_121 = modified("CONTRIBUTING.md", before='''
    ## Tooling
    - `pre-commit install` — ruff lint + format run on every commit.
''', after='''
    ## Database migrations
    - Migrations must be backwards compatible with the currently deployed code. Use expand/contract:
      add the new column, ship code that writes both, backfill, then drop the old column in a
      **separate** PR (see ADR-008).

    ## Tooling
    - `pre-commit install` — ruff lint + format run on every commit.
''', start=21)

register(PR(
    121, "ADR-008: expand/contract migrations (INC-19 follow-up)",
    '''
    Post-mortem action items from INC-19:
    1. Restore `charges.legacy_status` (still read by the reconciliation job; the drop was premature anyway)
    2. ADR-008: migrations must be compatible with both old and new code; drops go in a separate PR
    3. CONTRIBUTING section so new folks see it

    Post-mortem doc: INC-19 in the incidents folder.
    ''',
    "bob", "2026-05-08 10:30", [adr_008, migration_121, contributing_121], merged="2026-05-12 14:00",
    labels=["architecture", "database", "incident"], adr="ADR-008",
    reviews=[
        ("alice", "changes_requested", "2026-05-08 14:10", "Let's also document it where people will see it."),
        ("carol", "approved", "2026-05-11 09:30", ""),
        ("alice", "approved", "2026-05-11 16:20", "Thanks Bob."),
    ],
    threads=[
        thread(adr_008, "A PR that drops or renames a column must not change application code", [
            c("carol", "2026-05-08 12:40", "Should this also cover renames done via alembic's alter_column? That's what I'd have reached for."),
            c("bob", "2026-05-08 13:30", """
                Yes, a rename is a drop plus an add from the old pods' point of view. During a rolling deploy both
                versions run for several minutes, which is exactly what bit us. Add the new column, write both,
                backfill, switch reads, and drop in a later PR."""),
        ], "discussed", "bob", **label("C9")),
        thread(contributing_121, "## Database migrations", [
            c("alice", "2026-05-08 14:05", "Nobody reads ADRs before writing a migration. Please add a short section to CONTRIBUTING too."),
            c("bob", "2026-05-11 09:05", "Done."),
        ], "applied", "alice", **label("C9")),
    ],
))

# --- #122 Payment methods endpoint ---------------------------------------------------------------

api_122 = new_file("app/api/payment_methods.py", '''
    from fastapi import APIRouter

    from app.api.schemas import PaymentMethodResponse
    from app.services import payment_methods as pm_service

    router = APIRouter(prefix="/customers/{customer_id}/payment-methods", tags=["payment-methods"])


    @router.get("", response_model=list[PaymentMethodResponse])
    def list_payment_methods(customer_id: str) -> list[PaymentMethodResponse]:
        methods = pm_service.list_for_customer(customer_id)
        return [PaymentMethodResponse(**m.as_dict()) for m in methods]
''')

service_122 = new_file("app/services/payment_methods.py", '''
    from app.clients.http import make_client
    from app.config import settings
    from app.db import session_scope
    from app.domain.models import PaymentMethod
    from app.repos import payment_methods as pm_repo

    _tokenizer = make_client("tokenizer", settings.tokenizer_base_url)


    def list_for_customer(customer_id: str) -> list[PaymentMethod]:
        with session_scope() as session:
            rows = pm_repo.list_active(session, customer_id)
        return [_with_display_details(row) for row in rows]


    def _with_display_details(method: PaymentMethod) -> PaymentMethod:
        response = _tokenizer.get(f"/v1/tokens/{method.token}/display")
        response.raise_for_status()
        details = response.json()
        return method.with_display(brand=details["brand"], last4=details["last4"])
''')

config_122 = modified("app/config.py", before='''
        fx_base_url: str = "https://fx.internal.example"
        idempotency_ttl_seconds: int = 86_400
''', after='''
        fx_base_url: str = "https://fx.internal.example"
        tokenizer_base_url: str = "https://tokenizer.internal.example"
        idempotency_ttl_seconds: int = 86_400
''', start=13)

register(PR(
    122, "List a customer's saved payment methods",
    '''
    `GET /customers/{id}/payment-methods` for the new checkout (CHK-7). Card display details
    (brand, last4) come from the tokenizer; we never store them.
    ''',
    "carol", "2026-05-13 09:15", [api_122, service_122, config_122], merged="2026-05-15 10:30",
    labels=["checkout"],
    reviews=[
        ("bob", "changes_requested", "2026-05-13 12:30", "Config nit that isn't a nit."),
        ("alice", "changes_requested", "2026-05-13 14:45", "Layering."),
        ("frank", "commented", "2026-05-13 16:20", ""),
        ("alice", "approved", "2026-05-14 11:00", ""),
    ],
    threads=[
        thread(service_122, "_tokenizer = make_client(\"tokenizer\", settings.tokenizer_base_url)", [
            c("bob", "2026-05-13 12:25", """
                First version read `os.environ["TOKENIZER_URL"]` here. Please add it to `Settings` instead: env
                reads scattered around app/ are invisible to the config docs, crash at import time in tests,
                and skip the PAYMENTS_ prefix we use in every deployment."""),
            c("carol", "2026-05-13 13:40", "Fair. Added `tokenizer_base_url` to Settings."),
        ], "applied", "bob", **label("C10")),
        thread(api_122, "methods = pm_service.list_for_customer(customer_id)", [
            c("alice", "2026-05-13 14:40", "The first commit queried `pm_repo.list_active` with a session right in the handler. Same thing as #104: handlers call a service, the service owns the session."),
            c("carol", "2026-05-13 15:30", "Moved into `payment_methods.list_for_customer`."),
        ], "applied", "alice", **label("C2", mistake="M4")),
        thread(service_122, "def _with_display_details(method: PaymentMethod) -> PaymentMethod:", [
            c("frank", "2026-05-13 16:15", "Please add a docstring to `_with_display_details` explaining what it returns."),
            c("carol", "2026-05-13 17:00", "It's a private helper and the name plus the type hints say it all. We document public service functions, not every underscore helper."),
        ], "rejected", "carol", "Private helpers are self-documenting by name; we only document public service functions.",
            **label(noise="N1")),
    ],
))
