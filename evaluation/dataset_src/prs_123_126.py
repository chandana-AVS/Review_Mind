"""History PRs #123-#126: payout retry job, ledger entries, erin's first PR, backfill script (E3)."""

from lib import PR, c, label, modified, new_file, register, thread

# --- #123 Retry failed payouts job ----------------------------------------------------------------

job_123 = new_file("app/jobs/retry_payouts.py", '''
    from app.clients import bank_transfers
    from app.db import session_scope
    from app.repos import payouts as payout_repo
    from app.utils import clock
    from app.utils.logging import get_logger

    log = get_logger(__name__)

    MAX_ATTEMPTS = 5


    def run() -> int:
        """Resubmit payouts that failed with a transient bank error. Returns how many were resubmitted."""
        resubmitted = 0
        with session_scope() as session:
            failed = payout_repo.list_retryable(session, max_attempts=MAX_ATTEMPTS, now=clock.now())
        for payout in failed:
            bank_transfers.submit(payout)
            with session_scope() as session:
                payout_repo.record_attempt(session, payout.id, at=clock.now())
            resubmitted += 1
        log.info("payout_retry_run", resubmitted=resubmitted)
        return resubmitted
''')

bank_123 = modified("app/clients/bank_transfers.py", before='''
    from app.clients.http import make_client
    from app.config import settings
    from app.domain.models import Payout

    _client = make_client("bank_transfers", settings.bank_transfers_base_url)


    def submit(payout: Payout) -> None:
        response = _client.post("/v2/transfers", json=_transfer_body(payout))
        response.raise_for_status()
''', after='''
    from app.clients.http import make_client
    from app.config import settings
    from app.domain.models import Payout

    _client = make_client("bank_transfers", settings.bank_transfers_base_url)


    def submit(payout: Payout) -> None:
        response = _client.post(
            "/v2/transfers",
            json=_transfer_body(payout),
            headers={"Idempotency-Key": f"payout-{payout.id}"},
        )
        response.raise_for_status()


    def transfer_status(transfer_id: str) -> str:
        response = _client.get(f"/v2/transfers/{transfer_id}")
        response.raise_for_status()
        return str(response.json()["status"])
''')

register(PR(
    123, "Nightly job to resubmit payouts that failed transiently",
    '''
    ~0.4% of payouts fail with a transient bank error (BANK_TIMEOUT / 503) and currently need ops to
    resubmit by hand. This job picks them up nightly, up to 5 attempts. Submissions now send an
    idempotency key derived from the payout id, so the bank dedupes if the first attempt actually
    went through.
    ''',
    "dan", "2026-05-19 10:10", [job_123, bank_123], merged="2026-05-21 09:40",
    labels=["payouts", "jobs"],
    reviews=[
        ("bob", "changes_requested", "2026-05-19 13:50", ""),
        ("frank", "commented", "2026-05-19 15:30", ""),
        ("alice", "approved", "2026-05-20 11:10", ""),
        ("bob", "approved", "2026-05-20 12:00", "Nice touch with the idempotency key."),
    ],
    threads=[
        thread(bank_123, "def transfer_status(transfer_id: str) -> str:", [
            c("bob", "2026-05-19 13:45", """
                The first commit added `transfer_status` with `requests.get(...)`. Same story as #113 and ADR-006:
                everything goes through the module's `_client`. requests isn't even in our dependencies anymore;
                it only worked because some dev dependency pulls it in."""),
            c("dan", "2026-05-19 14:30", "Ugh, copied from the bank's sample code. Fixed to use `_client`."),
        ], "applied", "bob", **label("C3", mistake="M1")),
        thread(job_123, "bank_transfers.submit(payout)", [
            c("frank", "2026-05-19 15:25", "Each resubmission should be wrapped in a retry with exponential backoff; banks are flaky and one attempt per night is too few."),
            c("alice", "2026-05-20 11:05", """
                The shared client already retries transient errors on every call, and this job *is* the slow outer
                retry. Adding a backoff loop per payout is how INC-12 happened. One retry layer in the client, plus
                the nightly job for anything that's still failing."""),
            c("frank", "2026-05-20 11:30", "Fine."),
        ], "rejected", "alice", "make_client already retries; the nightly job is the outer retry. Extra loops caused INC-12.",
            **label(noise="N5")),
    ],
))

# --- #124 Ledger entries -------------------------------------------------------------------------

ledger_124 = new_file("app/services/ledger.py", '''
    from dataclasses import dataclass

    from app import errors
    from app.db import session_scope
    from app.repos import ledger as ledger_repo
    from app.utils import clock
    from app.utils.ids import new_id
    from app.utils.logging import get_logger, redact

    log = get_logger(__name__)


    @dataclass(frozen=True, slots=True)
    class LedgerEntry:
        id: str
        account: str
        amount_cents: int
        currency: str
        reference: str


    def post_refund(*, refund_id: str, merchant_account: str, customer_email: str,
                    amount_cents: int, currency: str) -> list[LedgerEntry]:
        if amount_cents <= 0:
            raise errors.Conflict("refund ledger amount must be positive")
        entries = [
            LedgerEntry(new_id("le"), merchant_account, -amount_cents, currency, refund_id),
            LedgerEntry(new_id("le"), "customer_refunds_payable", amount_cents, currency, refund_id),
        ]
        with session_scope() as session:
            ledger_repo.insert_balanced(session, entries, posted_at=clock.now())
        log.info("ledger_refund_posted", refund_id=refund_id, customer=redact(customer_email),
                 amount_cents=amount_cents)
        return entries
''')

register(PR(
    124, "Double-entry ledger postings for refunds",
    '''
    First step towards FIN-30 (real double-entry ledger). Refunds now post two balanced entries.
    `insert_balanced` asserts the entries sum to zero per currency before writing.
    ''',
    "bob", "2026-05-26 09:40", [ledger_124], merged="2026-05-28 15:15",
    labels=["ledger", "money"],
    reviews=[
        ("frank", "commented", "2026-05-26 12:10", ""),
        ("carol", "changes_requested", "2026-05-26 14:20", "PII in logs."),
        ("alice", "changes_requested", "2026-05-26 15:00", ""),
        ("alice", "approved", "2026-05-27 10:30", ""),
    ],
    threads=[
        thread(ledger_124, "class LedgerEntry:", [
            c("frank", "2026-05-26 12:05", "LedgerEntry is the perfect case for Pydantic: you want amount/currency validation on every entry. A dataclass gives you nothing."),
            c("bob", "2026-05-26 13:00", "Validation happens in `insert_balanced` (it checks the entries balance), which is the invariant that actually matters. Field-level coercion is exactly what we don't want near money."),
            c("alice", "2026-05-26 14:55", "Dataclass per ADR-005. Frank, if you want to reopen the ADR, let's do it at the arch sync rather than in individual PRs; this is the third time (#109, #115)."),
        ], "rejected", "alice", "ADR-005: domain types are frozen dataclasses; reopen the ADR at arch sync, not per PR.",
            **label(conflict="K3", strength="enforced")),
        thread(ledger_124, "customer=redact(customer_email)", [
            c("carol", "2026-05-26 14:15", "This logged `customer_email=customer_email` in the first version. Emails are PII and Datadog retention is 30 days: please `redact()` it, or better, drop it and log the refund id only."),
            c("bob", "2026-05-26 16:00", "Good catch. Redacted; kept it for support's sake since the masked domain is still useful."),
        ], "applied", "carol", **label("C5", mistake="M3")),
        thread(ledger_124, 'raise errors.Conflict("refund ledger amount must be positive")', [
            c("alice", "2026-05-26 14:58", "First commit raised `HTTPException(422)` here. The ledger is called from the payout worker too, so domain error please (see #117)."),
            c("bob", "2026-05-26 16:02", "Yep, changed to errors.Conflict."),
        ], "applied", "alice", **label("C6", mistake="M6")),
    ],
))

# --- #125 erin's first PR: saved cards ------------------------------------------------------------

api_125 = new_file("app/api/saved_cards.py", '''
    from fastapi import APIRouter

    from app.api.schemas import SaveCardRequest, SavedCardResponse
    from app.services import saved_cards as saved_card_service
    from app.utils.idempotency import idempotent

    router = APIRouter(prefix="/customers/{customer_id}/cards", tags=["cards"])


    @router.post("", response_model=SavedCardResponse, status_code=201)
    @idempotent
    def save_card(customer_id: str, body: SaveCardRequest) -> SavedCardResponse:
        card = saved_card_service.save_card(customer_id=customer_id, setup_token=body.setup_token)
        return SavedCardResponse(**card.as_dict())
''')

service_125 = new_file("app/services/saved_cards.py", '''
    from app import errors
    from app.clients import tokenizer
    from app.db import session_scope
    from app.domain.models import SavedCard
    from app.repos import customers as customer_repo
    from app.repos import saved_cards as saved_card_repo
    from app.utils import clock
    from app.utils.ids import new_id


    def save_card(*, customer_id: str, setup_token: str) -> SavedCard:
        token = tokenizer.exchange_setup_token(setup_token)
        with session_scope() as session:
            if customer_repo.get(session, customer_id) is None:
                raise errors.NotFound(f"customer {customer_id} not found")
            card = SavedCard(
                id=new_id("card"),
                customer_id=customer_id,
                token=token.id,
                brand=token.brand,
                last4=token.last4,
                created_at=clock.now(),
            )
            saved_card_repo.insert(session, card)
        return card
''')

tests_125 = new_file("tests/test_saved_cards.py", '''
    from app.services import saved_cards
    from tests.factories import make_customer


    def test_save_card__valid_setup_token__stores_tokenized_card(fake_repos, fake_tokenizer, frozen_clock):
        fake_repos.customers.add(make_customer(id="cus_1"))
        fake_tokenizer.will_return(id="tok_1", brand="visa", last4="4242")

        card = saved_cards.save_card(customer_id="cus_1", setup_token="seti_1")

        assert card.token == "tok_1"
        assert card.created_at == frozen_clock


    def test_save_card__unknown_customer__raises_not_found(fake_repos, fake_tokenizer):
        fake_tokenizer.will_return(id="tok_1", brand="visa", last4="4242")

        with pytest.raises(errors.NotFound):
            saved_cards.save_card(customer_id="cus_missing", setup_token="seti_1")
''')

register(PR(
    125, "Save cards for returning customers",
    '''
    Hi all, my first PR here! 👋

    Adds `POST /customers/{id}/cards`, which exchanges a Stripe SetupIntent token with our tokenizer
    and stores the resulting token + display details. Part of CHK-9 (one-click checkout).

    I've tried to follow CONTRIBUTING, but please tell me if anything doesn't match how you do things.
    ''',
    "erin", "2026-06-03 10:30", [api_125, service_125, tests_125], merged="2026-06-05 16:20",
    labels=["checkout"],
    reviews=[
        ("bob", "changes_requested", "2026-06-03 14:10", "Welcome Erin! A couple of house rules inline, all quick."),
        ("carol", "changes_requested", "2026-06-03 15:40", "Welcome! Same, a few conventions."),
        ("frank", "commented", "2026-06-03 17:00", ""),
        ("bob", "approved", "2026-06-05 10:00", "Great first PR."),
        ("carol", "approved", "2026-06-05 11:30", ""),
    ],
    threads=[
        thread(api_125, "card = saved_card_service.save_card(", [
            c("bob", "2026-06-03 13:55", """
                The handler opened a session and inserted the card directly. Here we keep handlers thin: they call
                one service function, and the service owns the session and the queries (ADR-003, and CONTRIBUTING's
                Layout section). Makes the logic reusable from the jobs too."""),
            c("erin", "2026-06-03 15:05", "Ah, at my last company we did it in the route, but I see the point. Moved into `saved_cards.save_card`."),
        ], "applied", "bob", **label("C2", mistake="M4")),
        thread(api_125, "@idempotent", [
            c("bob", "2026-06-03 14:05", "Creating a card isn't moving money, but it does call the tokenizer, and a double submit from checkout would create two cards. We decorate client-facing POSTs like this with `@idempotent` (ADR-004)."),
            c("erin", "2026-06-03 15:07", "Added."),
        ], "applied", "bob", **label("C4", mistake="M5")),
        thread(service_125, "created_at=clock.now(),", [
            c("carol", "2026-06-03 15:30", "This was `datetime.utcnow()`. We always use `app.utils.clock.now()`: it's tz-aware UTC and the `frozen_clock` fixture can control it in tests."),
            c("erin", "2026-06-03 16:00", "Done, and I used frozen_clock in the test to assert on it."),
        ], "applied", "carol", **label("C7", mistake="M2")),
        thread(tests_125, "def test_save_card__valid_setup_token__stores_tokenized_card", [
            c("carol", "2026-06-03 15:35", "Test names follow `test_<unit>__<scenario>__<expected>` (see CONTRIBUTING), and please build the customer with `tests/factories.py` rather than constructing it inline."),
            c("erin", "2026-06-03 16:02", "Renamed both and switched to make_customer."),
        ], "applied", "carol", **label("C8")),
        thread(tests_125, "def test_save_card__unknown_customer__raises_not_found", [
            c("frank", "2026-06-03 16:55", "Add type hints to the test functions and fixtures (`fake_repos: FakeRepos`, `-> None`, etc.)."),
            c("erin", "2026-06-04 09:10", "Happy to. Is that the convention? The existing tests don't seem to have them."),
            c("bob", "2026-06-04 10:20", "It isn't: we don't type tests, and mypy excludes tests/. Leave them as they are."),
        ], "rejected", "bob", "We don't type tests; mypy excludes tests/.", **label(noise="N2")),
    ],
))

# --- #126 Backfill script (E3) ---------------------------------------------------------------------

script_126 = new_file("scripts/backfill_refund_reasons.py", '''
    """One-off: backfill refunds.reason from the legacy support-ticket export (June 2026)."""

    import csv
    import os
    import sys

    from sqlalchemy import create_engine, text

    engine = create_engine(os.environ["DATABASE_URL"])

    with open(sys.argv[1]) as f, engine.begin() as conn:
        for row in csv.DictReader(f):
            conn.execute(
                text("UPDATE refunds SET reason = :reason WHERE id = :id AND reason IS NULL"),
                {"reason": row["reason"], "id": row["refund_id"]},
            )
            print("updated", row["refund_id"])
''')

register(PR(
    126, "One-off script: backfill refund reasons from the support export",
    '''
    Support exported ~11k legacy tickets with refund reasons. This script fills `refunds.reason`
    where it is NULL. Run once from a bastion against a snapshot first, then prod.
    Will delete the script after it has run.
    ''',
    "carol", "2026-06-10 09:00", [script_126], merged="2026-06-10 16:30",
    labels=["scripts", "data"],
    reviews=[
        ("frank", "changes_requested", "2026-06-10 11:20", ""),
        ("alice", "approved", "2026-06-10 13:45", ""),
    ],
    threads=[
        thread(script_126, 'engine = create_engine(os.environ["DATABASE_URL"])', [
            c("frank", "2026-06-10 11:15", "`os.environ` and `print` in our codebase? This should use `settings.database_url` and the structured logger like everything else."),
            c("carol", "2026-06-10 12:30", "It's a one-off under scripts/, run by hand from the bastion with a different DATABASE_URL (the snapshot, then prod). Importing app.config would pull in all the app settings and their validation."),
            c("alice", "2026-06-10 13:40", "Fine for scripts/. The settings and logging rules are for app/. One-off scripts can use env vars and print; they're not deployed."),
        ], "rejected", "alice", "One-off scripts under scripts/ may use os.environ and print; the rules apply to app/.",
            **label("C10", exception="E3")),
    ],
))
