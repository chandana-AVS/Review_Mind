"""Held-out test PRs #137-#144 (September 2026). Scored; no reviews or threads."""

from lib import PR, flag, modified, new_file, register, trap

# ---------------------------------------------------------------------------
# #137 erin: Add refund processing fee (live demo PR)
# ---------------------------------------------------------------------------

p137_api = modified("app/api/refunds.py", before='''
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
''', after='''
    from fastapi import APIRouter

    from app.api.schemas import (
        CreateRefundRequest, RefundRemainingRequest, RefundResponse, RefundWithFeeResponse,
    )
    from app.services import refund_fees as refund_fee_service
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


    @router.post("/remaining", response_model=RefundWithFeeResponse, status_code=201)
    def refund_remaining_with_fee(body: RefundRemainingRequest) -> RefundWithFeeResponse:
        refund, fee_cents = refund_fee_service.refund_remaining_with_fee(
            charge_id=body.charge_id, reason=body.reason
        )
        return RefundWithFeeResponse(**refund.as_dict(), fee_cents=fee_cents)
''')

p137_schemas = modified("app/api/schemas.py", before='''
    class RefundResponse(BaseModel):
        id: str
        charge_id: str
        amount_cents: int
        currency: str
        status: str
''', after='''
    class RefundResponse(BaseModel):
        id: str
        charge_id: str
        amount_cents: int
        currency: str
        status: str


    class RefundRemainingRequest(BaseModel):
        charge_id: str
        reason: str | None = None


    class RefundWithFeeResponse(RefundResponse):
        fee_cents: int
''', start=10)

p137_service = new_file("app/services/refund_fees.py", '''
    from datetime import datetime

    import httpx

    from app import errors
    from app.config import settings
    from app.db import session_scope
    from app.domain.models import Refund
    from app.domain.money import apply_bps
    from app.repos import charges as charge_repo
    from app.services import refunds as refund_service
    from app.utils.logging import get_logger

    log = get_logger(__name__)

    # Pricing sheet v4: 1.5% of the refunded amount, minimum 0.30 in the charge currency.
    REFUND_FEE_BPS = 150
    MIN_REFUND_FEE_CENTS = 30

    _bank = httpx.Client(base_url=settings.bank_transfers_base_url, timeout=10.0)


    def refund_remaining_with_fee(*, charge_id: str, reason: str | None) -> tuple[Refund, int]:
        """Refund whatever is left on a charge and debit the merchant our processing fee."""
        with session_scope() as session:
            charge = charge_repo.get(session, charge_id)
        if charge is None:
            raise errors.NotFound(f"charge {charge_id} not found")
        if charge.refundable_cents == 0:
            raise errors.Conflict(f"charge {charge_id} is already fully refunded")

        refund = refund_service.create_refund(
            charge_id=charge_id, amount_cents=charge.refundable_cents, reason=reason
        )
        fee_cents = _processing_fee(charge.amount_cents)
        response = _bank.post("/v1/debits", json={
            "merchant_id": charge.merchant_id,
            "amount_cents": fee_cents,
            "currency": charge.currency,
            "reference": refund.id,
            "requested_at": datetime.utcnow().isoformat(),
        })
        response.raise_for_status()
        log.info("refund_fee_debited", refund_id=refund.id, fee_cents=fee_cents)
        return refund, fee_cents


    def _processing_fee(amount_cents: int) -> int:
        fee_cents = apply_bps(amount_cents, REFUND_FEE_BPS)
        return max(fee_cents, MIN_REFUND_FEE_CENTS)
''')

p137_tests = new_file("tests/test_refund_fees.py", '''
    import httpx
    import pytest

    from app.services import refund_fees
    from tests.factories import make_charge


    @pytest.fixture
    def bank_debits(monkeypatch):
        calls = []

        def fake_post(url, json):
            calls.append(json)
            return httpx.Response(201, json={"id": "dbt_1"}, request=httpx.Request("POST", url))

        monkeypatch.setattr(refund_fees._bank, "post", fake_post)
        return calls


    def test_refund_remaining_with_fee__no_prior_refunds__debits_150_bps(fake_repos, bank_debits):
        fake_repos.charges.add(make_charge(amount_cents=10_000, captured_cents=10_000))

        refund, fee_cents = refund_fees.refund_remaining_with_fee(charge_id="ch_test_1", reason=None)

        assert refund.amount_cents == 10_000
        assert fee_cents == 150
        assert bank_debits[0]["amount_cents"] == 150


    def test_refund_remaining_with_fee__small_charge__applies_minimum_fee(fake_repos, bank_debits):
        fake_repos.charges.add(make_charge(amount_cents=1_000, captured_cents=1_000))

        _, fee_cents = refund_fees.refund_remaining_with_fee(charge_id="ch_test_1", reason=None)

        assert fee_cents == 30
''')

register(PR(
    137, "Add refund processing fee",
    '''
    Adds `POST /refunds/remaining`: refunds whatever is still refundable on a charge and debits the
    merchant our refund processing fee through the bank transfers service.

    Fee follows pricing sheet v4: 1.5% of the refunded amount, with a 0.30 minimum. Support asked for
    this so they stop doing the refund and the manual fee debit as two separate steps.

    Testing: unit tests with the bank debit call stubbed out; tried it against bank-transfers sandbox.
    ''',
    "erin", "2026-09-01 10:14", [p137_api, p137_schemas, p137_service, p137_tests],
    labels=["refunds", "pricing"],
    heldout={
        "demo": True,
        "should_flag": [
            flag("137-F1", "convention", p137_api, '@router.post("/remaining"',
                 "def refund_remaining_with_fee", convention="C4",
                 rationale="New money-moving POST (refund + fee debit) has no @idempotent; a client retry "
                           "would refund and debit twice (ADR-004).",
                 fix="Decorate refund_remaining_with_fee with @idempotent, like create_refund above it."),
            flag("137-F2", "convention", p137_service, "_bank = httpx.Client(", convention="C3",
                 rationale="Builds its own httpx.Client instead of the shared client, losing retries, "
                           "request-id propagation and http_call metrics (ADR-006).",
                 fix='_bank = make_client("bank_transfers", settings.bank_transfers_base_url)'),
            flag("137-F3", "convention", p137_service, '"requested_at": datetime.utcnow()', convention="C7",
                 rationale="Reads the wall clock directly (and naive utcnow); tests can't freeze it via "
                           "frozen_clock.",
                 fix="Use clock.now().isoformat() from app.utils.clock."),
            flag("137-F4", "generic_bug", p137_service, "fee_cents = _processing_fee(charge.amount_cents)",
                 rationale="Fee is computed on the original charge amount, but the refund is only the "
                           "remaining refundable amount; partially refunded charges are overcharged.",
                 fix="Base the fee on the refunded amount: _processing_fee(refund.amount_cents) "
                     "(i.e. charge.refundable_cents)."),
        ],
        "should_not_flag": [
            trap("137-X1", p137_service, "fee_cents = apply_bps(amount_cents, REFUND_FEE_BPS)", noise="N4",
                 trap="Percentage fee on integers risks rounding errors; use Decimal for currency arithmetic.",
                 reject="Money is int minor units since ADR-007, and apply_bps already rounds half-even. No Decimal."),
            trap("137-X2", p137_service, "def _processing_fee(amount_cents: int) -> int:", noise="N1",
                 trap="Add a docstring to _processing_fee explaining the fee rule and minimum.",
                 reject="It's a private helper and the name says what it does; we only docstring public service functions."),
            trap("137-X3", p137_tests, "def bank_debits(monkeypatch):", noise="N2",
                 trap="Add type hints to the bank_debits fixture and its fake_post helper (monkeypatch: pytest.MonkeyPatch, -> list[dict]).",
                 reject="We don't type tests, mypy excludes tests/ anyway."),
            trap("137-X4", p137_tests, "assert fee_cents == 150", noise="N6",
                 trap="Extract magic numbers like 150 and 10_000 into named test constants.",
                 reject="Test literals stay inline on purpose so each test reads on its own."),
            trap("137-X5", p137_service, "response = _bank.post(", noise="N5",
                 trap="The bank debit call has no retry/backoff; wrap it in a retry for transient failures.",
                 reject="The shared client already retries; stacking another retry layer is how we got INC-12."),
        ],
        "drift_trap": None,
    },
))

# ---------------------------------------------------------------------------
# #138 dan: Payout batching
# ---------------------------------------------------------------------------

p138_service = new_file("app/services/payout_batches.py", '''
    from app.clients import bank_transfers
    from app.utils.logging import get_logger
    from app.domain.models import Payout
    from app.db import session_scope
    from app.repos import payouts as payout_repo
    from app.utils import clock
    from app.utils.ids import new_id

    log = get_logger(__name__)

    # The bank's bulk transfer API rejects files with more than 200 instructions.
    MAX_BATCH_SIZE = 200


    def submit_pending_batches(*, merchant_id: str, currency: str) -> list[str]:
        """Submit a merchant's pending payouts to the bank in batches of MAX_BATCH_SIZE."""
        with session_scope() as session:
            pending = payout_repo.list_pending(session, merchant_id=merchant_id, currency=currency)

        batch_ids = []
        for batch in _chunk(pending, MAX_BATCH_SIZE):
            batch_id = new_id("pb")
            bank_transfers.submit_batch(batch_id, batch)
            with session_scope() as session:
                payout_repo.mark_batched(session, [p.id for p in batch], batch_id, clock.now())
            batch_ids.append(batch_id)

        log.info(f"submitted {len(batch_ids)} payout batches for merchant {merchant_id}")
        return batch_ids


    def _chunk(payouts: list[Payout], size: int) -> list[list[Payout]]:
        batch_count = len(payouts) // size
        return [payouts[i * size : (i + 1) * size] for i in range(batch_count)]
''')

p138_api = modified("app/api/payouts.py", before='''
    from fastapi import APIRouter

    from app.api.schemas import CreatePayoutRequest, PayoutResponse
    from app.services import payouts as payout_service
    from app.utils.idempotency import idempotent

    router = APIRouter(prefix="/payouts", tags=["payouts"])


    @router.post("", response_model=PayoutResponse, status_code=201)
    @idempotent
    def create_payout(body: CreatePayoutRequest) -> PayoutResponse:
        payout = payout_service.create_payout(
            merchant_id=body.merchant_id, amount_cents=body.amount_cents, currency=body.currency
        )
        return PayoutResponse(**payout.as_dict())
''', after='''
    from fastapi import APIRouter

    from app.api.schemas import CreatePayoutRequest, PayoutResponse, SubmitPayoutBatchesRequest
    from app.db import session_scope
    from app.repos import payouts as payout_repo
    from app.services import payout_batches as batch_service
    from app.services import payouts as payout_service
    from app.utils.idempotency import idempotent

    router = APIRouter(prefix="/payouts", tags=["payouts"])


    @router.post("", response_model=PayoutResponse, status_code=201)
    @idempotent
    def create_payout(body: CreatePayoutRequest) -> PayoutResponse:
        payout = payout_service.create_payout(
            merchant_id=body.merchant_id, amount_cents=body.amount_cents, currency=body.currency
        )
        return PayoutResponse(**payout.as_dict())


    @router.post("/batches", status_code=202)
    @idempotent
    def submit_payout_batches(body: SubmitPayoutBatchesRequest) -> dict[str, list[str]]:
        batch_ids = batch_service.submit_pending_batches(
            merchant_id=body.merchant_id, currency=body.currency
        )
        return {"batch_ids": batch_ids}


    @router.get("/batches/{batch_id}", response_model=list[PayoutResponse])
    def list_batch_payouts(batch_id: str) -> list[PayoutResponse]:
        with session_scope() as session:
            payouts = payout_repo.list_by_batch(session, batch_id)
        return [PayoutResponse(**p.as_dict()) for p in payouts]
''')

p138_schemas = modified("app/api/schemas.py", before='''
    class PayoutResponse(BaseModel):
        id: str
        merchant_id: str
        amount_cents: int
        currency: str
        status: str
''', after='''
    class PayoutResponse(BaseModel):
        id: str
        merchant_id: str
        amount_cents: int
        currency: str
        status: str


    class SubmitPayoutBatchesRequest(BaseModel):
        merchant_id: str
        currency: str = Field(min_length=3, max_length=3)
''', start=24)

register(PR(
    138, "Payout batching",
    '''
    Merchants with high payout volume are hitting the bank's per-request rate limit. This groups a
    merchant's pending payouts into bulk transfer files of up to 200 instructions (the bank's limit).

    - `POST /payouts/batches` submits all pending payouts for a merchant/currency
    - `GET /payouts/batches/{batch_id}` lists what went into a batch, for support

    The repo queries (`list_pending`, `list_by_batch`, `mark_batched`) and the `batch_id` column landed
    with the payout status migration last week.
    ''',
    "dan", "2026-09-03 14:32", [p138_service, p138_api, p138_schemas],
    labels=["payouts"],
    heldout={
        "should_flag": [
            flag("138-F1", "convention", p138_api, "with session_scope() as session:",
                 "payouts = payout_repo.list_by_batch(", convention="C2",
                 rationale="The handler opens a session and queries the repo directly; handlers must "
                           "call a service (ADR-003).",
                 fix="Add payout_batches.list_batch_payouts(batch_id) and call it from the handler."),
            flag("138-F2", "convention", p138_service, 'log.info(f"submitted', convention="C5",
                 rationale="f-string log message: not a stable snake_case event, fields aren't queryable.",
                 fix='log.info("payout_batches_submitted", merchant_id=merchant_id, batch_count=len(batch_ids))'),
            flag("138-F3", "generic_bug", p138_service, "batch_count = len(payouts) // size",
                 rationale="Floor division drops the final partial batch: 450 pending payouts produce two "
                           "batches and the last 50 are never submitted.",
                 fix="Use ceil division, or simply [payouts[i:i + size] for i in range(0, len(payouts), size)]."),
        ],
        "should_not_flag": [
            trap("138-X1", p138_service, "def _chunk(payouts: list[Payout], size: int)", noise="N1",
                 trap="_chunk should have a docstring describing how it splits the list.",
                 reject="Private helper, the name is the documentation. Docstrings are for public service functions."),
            trap("138-X2", p138_service, "from app.utils.logging import get_logger",
                 "from app.db import session_scope", noise="N3",
                 trap="Imports are not sorted alphabetically (app.utils.logging before app.domain/app.db).",
                 reject="ruff sorts imports in pre-commit, please don't spend review comments on ordering."),
            trap("138-X3", p138_service, "bank_transfers.submit_batch(batch_id, batch)", noise="N7",
                 trap="Wrap submit_batch in try/except, log the failing batch and re-raise.",
                 reject="Let the error propagate, the error middleware already logs it; a local log-and-raise double-logs."),
        ],
        "drift_trap": None,
    },
))

# ---------------------------------------------------------------------------
# #139 carol: Card-network fallback
# ---------------------------------------------------------------------------

p139_client = modified("app/clients/card_network.py", before='''
    from app.clients.http import make_client
    from app.config import settings

    _client = make_client("card_network", settings.card_network_base_url)


    def network_status(network: str) -> str:
        response = _client.get(f"/v1/networks/{network}/status")
        response.raise_for_status()
        return str(response.json()["status"])
''', after='''
    import requests

    from app.clients.http import make_client
    from app.config import settings

    _client = make_client("card_network", settings.card_network_base_url)


    def network_status(network: str) -> str:
        response = _client.get(f"/v1/networks/{network}/status")
        response.raise_for_status()
        return str(response.json()["status"])


    def status_page_indicator(network: str) -> str:
        # Public status page is a static JSON feed, no auth; requests is fine here for now.
        response = requests.get(f"{settings.card_network_status_page_url}/{network}.json", timeout=5)
        response.raise_for_status()
        return str(response.json()["status"]["indicator"])
''')

p139_config = modified("app/config.py", before='''
    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="PAYMENTS_", env_file=".env")

        database_url: str = "postgresql+psycopg://localhost/payments"
        redis_url: str = "redis://localhost:6379/0"
        stripe_api_key: str = ""
        stripe_webhook_secret: str = ""
        card_network_base_url: str = "https://status.cardnet.example"
        fraud_base_url: str = "https://fraud.internal.example"
''', after='''
    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="PAYMENTS_", env_file=".env")

        database_url: str = "postgresql+psycopg://localhost/payments"
        redis_url: str = "redis://localhost:6379/0"
        stripe_api_key: str = ""
        stripe_webhook_secret: str = ""
        card_network_base_url: str = "https://status.cardnet.example"
        card_network_status_page_url: str = "https://statuspage.cardnet.example/api/v2/networks"
        fraud_base_url: str = "https://fraud.internal.example"
''', start=4)

p139_service = new_file("app/services/network_health.py", '''
    import httpx
    from fastapi import HTTPException

    from app.clients import card_network
    from app.utils.logging import get_logger

    log = get_logger(__name__)

    # statuspage.io indicators -> our network status values
    _INDICATOR_STATUS = {"none": "up", "minor": "degraded", "major": "down", "critical": "down"}


    def current_status(network: str) -> str:
        """Card network status; falls back to the public status page if the status API is down."""
        try:
            return card_network.network_status(network)
        except httpx.HTTPError:
            log.warning("card_network_status_api_failed", network=network)

        try:
            indicator = card_network.status_page_indicator(network)
        except Exception:
            indicator = "none"

        if indicator not in _INDICATOR_STATUS:
            raise HTTPException(status_code=502, detail=f"unknown status indicator {indicator!r}")
        return _INDICATOR_STATUS[indicator]
''')

p139_tests = new_file("tests/test_network_health.py", '''
    from unittest.mock import Mock

    import httpx
    import pytest

    from app.clients import card_network
    from app.services import network_health


    @pytest.fixture
    def status_api_down(monkeypatch):
        request = httpx.Request("GET", "https://status.cardnet.example/v1/networks/visa/status")
        error = httpx.HTTPStatusError("503", request=request, response=httpx.Response(503, request=request))
        monkeypatch.setattr(card_network, "network_status", Mock(side_effect=error))


    def test_current_status__api_up__returns_api_status(monkeypatch):
        monkeypatch.setattr(card_network, "network_status", Mock(return_value="degraded"))

        assert network_health.current_status("visa") == "degraded"


    def test_current_status__api_down_page_major__returns_down(status_api_down, monkeypatch):
        monkeypatch.setattr(card_network, "status_page_indicator", Mock(return_value="major"))

        assert network_health.current_status("visa") == "down"
''')

register(PR(
    139, "Card-network fallback",
    '''
    When the card network's status API is down we currently return a 500 from anything that checks
    network status (checkout banner, payout scheduler). This falls back to the network's public
    status page and maps its indicator onto our up/degraded/down values.

    The status API has been flaky this month (three outages since 08-20), so this should cut a lot of
    noisy pages.

    Adds `card_network_status_page_url` to settings.
    ''',
    "carol", "2026-09-08 11:05", [p139_client, p139_config, p139_service, p139_tests],
    labels=["integrations", "reliability"],
    heldout={
        "should_flag": [
            flag("139-F1", "convention", p139_client, "requests is fine here for now",
                 "response = requests.get(", convention="C3",
                 rationale="Uses requests for the status page call. The 'fine for now' stance (#102) was "
                           "superseded by ADR-006: all outbound HTTP goes through make_client().",
                 fix='Add _status_page = make_client("card_network_status_page", '
                     "settings.card_network_status_page_url) and drop the requests import."),
            flag("139-F2", "convention", p139_service, "raise HTTPException(status_code=502", convention="C6",
                 rationale="Service raises FastAPI's HTTPException; services raise app.errors domain errors "
                           "and app/api/errors.py maps them to HTTP.",
                 fix="raise errors.ProviderUnavailable(...) (already mapped to 503)."),
            flag("139-F3", "generic_bug", p139_service, "except Exception:", 'indicator = "none"',
                 rationale="If the fallback also fails, the exception is swallowed and indicator 'none' "
                           "maps to 'up': a total outage is reported as the network being healthy.",
                 fix="Let the error propagate (or raise ProviderUnavailable) instead of defaulting to 'none'."),
        ],
        "should_not_flag": [
            trap("139-X1", p139_service, "return card_network.network_status(network)", noise="N5",
                 trap="Retry the status API call with backoff before falling back to the status page.",
                 reject="The shared client already retries 502/503/504; another retry layer on top is the INC-12 pattern."),
            trap("139-X2", p139_tests, "def status_api_down(monkeypatch):", noise="N2",
                 trap="Add type annotations to the status_api_down fixture (monkeypatch: pytest.MonkeyPatch) -> None.",
                 reject="Tests aren't typed here; mypy doesn't even look at tests/."),
            trap("139-X3", p139_tests, 'error = httpx.HTTPStatusError("503"', noise="N6",
                 trap="Replace the literal 503 with a named constant or HTTPStatus.SERVICE_UNAVAILABLE.",
                 reject="Literals in tests stay inline so the test reads on its own."),
        ],
        "drift_trap": {
            "conflict": "K2",
            "expected": "Flag the requests call as a shared-client (ADR-006) violation despite the "
                        "'requests is fine here for now' comment; that position from #102 was superseded "
                        "by ADR-006 in #110.",
        },
    },
))

# ---------------------------------------------------------------------------
# #140 erin: Delete saved card
# ---------------------------------------------------------------------------

p140_vault = new_file("app/clients/vault.py", '''
    import os

    from app.clients.http import make_client

    _client = make_client("vault", os.environ["PAYMENTS_VAULT_URL"])


    def delete_token(token: str) -> None:
        response = _client.delete(
            f"/v1/tokens/{token}",
            headers={"Authorization": f"Bearer {os.environ['PAYMENTS_VAULT_API_KEY']}"},
        )
        response.raise_for_status()
''')

p140_service = modified("app/services/saved_cards.py", before='''
    from app.db import session_scope
    from app.domain.models import SavedCard
    from app.repos import saved_cards as saved_card_repo
    from app.utils.logging import get_logger

    log = get_logger(__name__)


    def save_card(*, customer_id: str, vault_token: str, brand: str, last4: str) -> SavedCard:
        with session_scope() as session:
            card = saved_card_repo.insert(session, customer_id, vault_token, brand, last4)
        log.info("saved_card_created", card_id=card.id, customer_id=customer_id)
        return card


    def list_saved_cards(*, customer_id: str) -> list[SavedCard]:
        with session_scope() as session:
            return saved_card_repo.list_for_customer(session, customer_id)
''', after='''    from sqlalchemy.orm import Session

    from app import errors
    from app.clients import vault
    from app.db import session_scope
    from app.domain.models import SavedCard
    from app.repos import customers as customer_repo
    from app.repos import saved_cards as saved_card_repo
    from app.utils import clock
    from app.utils.logging import get_logger

    log = get_logger(__name__)


    def save_card(*, customer_id: str, vault_token: str, brand: str, last4: str) -> SavedCard:
        with session_scope() as session:
            card = saved_card_repo.insert(session, customer_id, vault_token, brand, last4)
        log.info("saved_card_created", card_id=card.id, customer_id=customer_id)
        return card


    def list_saved_cards(*, customer_id: str) -> list[SavedCard]:
        with session_scope() as session:
            return saved_card_repo.list_for_customer(session, customer_id)


    def delete_saved_card(*, customer_id: str, card_id: str) -> None:
        """Remove a saved card and delete its token from the vault."""
        with session_scope() as session:
            card = _get_active_card(session, card_id)
            saved_card_repo.mark_deleted(session, card.id, clock.now())
            customer = customer_repo.get(session, card.customer_id)
        vault.delete_token(card.vault_token)
        log.info("saved_card_deleted", card_id=card.id, customer_email=customer.email)


    def _get_active_card(session: Session, card_id: str) -> SavedCard:
        card = saved_card_repo.get(session, card_id)
        if card is None or card.deleted_at is not None:
            raise errors.NotFound(f"saved card {card_id} not found")
        return card
''')

p140_api = modified("app/api/saved_cards.py", before='''
    @router.get("", response_model=list[SavedCardResponse])
    def list_saved_cards(customer_id: str = Header(alias="X-Customer-Id")) -> list[SavedCardResponse]:
        cards = saved_card_service.list_saved_cards(customer_id=customer_id)
        return [SavedCardResponse(**c.as_dict()) for c in cards]
''', after='''
    @router.get("", response_model=list[SavedCardResponse])
    def list_saved_cards(customer_id: str = Header(alias="X-Customer-Id")) -> list[SavedCardResponse]:
        cards = saved_card_service.list_saved_cards(customer_id=customer_id)
        return [SavedCardResponse(**c.as_dict()) for c in cards]


    @router.delete("/{card_id}", status_code=204)
    def delete_saved_card(card_id: str, customer_id: str = Header(alias="X-Customer-Id")) -> None:
        saved_card_service.delete_saved_card(customer_id=customer_id, card_id=card_id)
''', start=24)

p140_tests = new_file("tests/test_delete_saved_card.py", '''
    from datetime import UTC, datetime

    import pytest

    from app import errors
    from app.clients import vault
    from app.domain.models import SavedCard
    from app.services import saved_cards


    @pytest.fixture
    def vault_calls(monkeypatch):
        calls = []
        monkeypatch.setattr(vault, "delete_token", calls.append)
        return calls


    def test_delete_saved_card(fake_repos, vault_calls, frozen_clock):
        fake_repos.saved_cards.add(SavedCard(
            id="card_1", customer_id="cus_1", vault_token="tok_1", brand="visa", last4="4242",
            deleted_at=None, created_at=datetime(2026, 6, 1, tzinfo=UTC),
        ))

        saved_cards.delete_saved_card(customer_id="cus_1", card_id="card_1")

        assert fake_repos.saved_cards.get("card_1").deleted_at == frozen_clock
        assert vault_calls == ["tok_1"]


    def test_delete_missing_card_raises(fake_repos, vault_calls):
        with pytest.raises(errors.NotFound):
            saved_cards.delete_saved_card(customer_id="cus_1", card_id="card_missing")
''')

register(PR(
    140, "Delete saved card",
    '''
    Customers can now remove a saved card from their wallet: `DELETE /saved-cards/{card_id}`.

    The card row is soft-deleted (we keep it for dispute lookups) and the token is deleted from the
    vault straight away, per the PCI scope review.

    New small vault client; the URL and API key come from the vault team's env vars
    (`PAYMENTS_VAULT_URL`, `PAYMENTS_VAULT_API_KEY`), already set in staging and prod.
    ''',
    "erin", "2026-09-10 16:20", [p140_vault, p140_service, p140_api, p140_tests],
    labels=["saved-cards", "pci"],
    heldout={
        "should_flag": [
            flag("140-F1", "convention", p140_service, "customer_email=customer.email", convention="C5",
                 rationale="Logs the customer's email in plain text; PII must be redacted (or left out) of logs.",
                 fix="Drop the email or log customer_email=redact(customer.email); customer_id is usually enough."),
            flag("140-F2", "convention", p140_tests, "def test_delete_saved_card(", convention="C8",
                 rationale="Test names don't follow test_<unit>__<scenario>__<expected>, and the SavedCard "
                           "is built inline instead of via tests/factories.py.",
                 fix="Rename to test_delete_saved_card__active_card__marks_deleted_and_deletes_token / "
                     "test_delete_saved_card__unknown_card__raises_not_found; add make_saved_card to factories."),
            flag("140-F3", "convention", p140_vault, '_client = make_client("vault", os.environ[',
                 convention="C10",
                 rationale="Reads os.environ inside app/; config goes through app.config.settings "
                           "(the PAYMENTS_ prefix is already applied there).",
                 fix="Add vault_url / vault_api_key to Settings and use settings.vault_url, settings.vault_api_key."),
            flag("140-F4", "generic_bug", p140_service, "card = _get_active_card(session, card_id)",
                 rationale="No ownership check: customer_id is never compared to card.customer_id, so any "
                           "customer can delete another customer's saved card by id (IDOR).",
                 fix="Raise NotFound when card.customer_id != customer_id (or look the card up by both ids)."),
        ],
        "should_not_flag": [
            trap("140-X1", p140_service, "def _get_active_card(session: Session, card_id: str)", noise="N1",
                 trap="Add a docstring to _get_active_card documenting the NotFound behaviour.",
                 reject="Private helper; the name and the three lines say it all. We only docstring public service functions."),
            trap("140-X2", p140_tests, "def vault_calls(monkeypatch):", noise="N2",
                 trap="The vault_calls fixture should be typed: (monkeypatch: pytest.MonkeyPatch) -> list[str].",
                 reject="No type hints in tests, mypy excludes tests/."),
            trap("140-X3", p140_service, "vault.delete_token(card.vault_token)", noise="N7",
                 trap="Wrap the vault call in try/except and log the failure with the card id before re-raising.",
                 reject="Errors propagate to the error middleware, which logs them; wrapping it just logs twice."),
        ],
        "drift_trap": None,
    },
))

# ---------------------------------------------------------------------------
# #141 bob: Redis health check
# ---------------------------------------------------------------------------

p141_health = modified("app/api/health.py", before='''
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
''', after='''
    import os
    from datetime import datetime

    import redis
    from fastapi import APIRouter
    from sqlalchemy import text
    from fastapi.responses import JSONResponse

    from app.db import session_scope

    router = APIRouter(tags=["ops"])

    _redis = redis.Redis.from_url(
        os.environ.get("PAYMENTS_REDIS_URL", "redis://localhost:6379/0"), socket_timeout=1.0
    )


    @router.get("/healthz")
    def healthz() -> dict[str, str]:
        # ADR-003 exception: operational endpoints check infrastructure directly.
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok"}


    @router.get("/healthz/deep")
    def deep_health() -> JSONResponse:
        checks = {"database": _check_database(), "redis": _check_redis()}
        healthy = all(checks.values())
        body = {
            "status": "ok" if healthy else "degraded",
            "checks": checks,
            "checked_at": datetime.now().isoformat(),
        }
        return JSONResponse(body, status_code=200 if healthy else 503)


    def _check_database() -> str:
        try:
            with session_scope() as session:
                session.execute(text("SELECT 1"))
        except Exception:
            return "error"
        return "ok"


    def _check_redis() -> str:
        try:
            _redis.ping()
        except redis.RedisError:
            return "error"
        return "ok"
''')

p141_tests = new_file("tests/test_health.py", '''
    from unittest.mock import Mock

    import redis
    from fastapi.testclient import TestClient

    from app.api import health
    from app.main import app

    client = TestClient(app)


    def test_deep_health__all_up__returns_ok(monkeypatch):
        monkeypatch.setattr(health, "_check_database", lambda: "ok")
        monkeypatch.setattr(health._redis, "ping", Mock(return_value=True))

        response = client.get("/healthz/deep")

        assert response.status_code == 200
        assert len(response.json()["checks"]) == 2


    def test_check_redis__ping_times_out__returns_error(monkeypatch):
        monkeypatch.setattr(health._redis, "ping", Mock(side_effect=redis.TimeoutError()))

        assert health._check_redis() == "error"
''')

register(PR(
    141, "Redis health check",
    '''
    Adds `/healthz/deep`, which checks both Postgres and Redis and returns 503 if either is unhealthy.
    The load balancer keeps using `/healthz`; the deep check is for the on-call dashboard and the
    synthetic monitor, so we stop finding out about idempotency-store outages from merchants.

    Redis ping uses a 1s socket timeout so a hung Redis can't hang the probe.
    ''',
    "bob", "2026-09-14 09:48", [p141_health, p141_tests],
    labels=["ops", "reliability"],
    heldout={
        "should_flag": [
            flag("141-F1", "convention", p141_health, '"checked_at": datetime.now().isoformat()',
                 convention="C7",
                 rationale="datetime.now() reads the wall clock (and is naive local time); time comes "
                           "from app.utils.clock.now() so tests can freeze it.",
                 fix='"checked_at": clock.now().isoformat()'),
            flag("141-F2", "convention", p141_health, 'os.environ.get("PAYMENTS_REDIS_URL"', convention="C10",
                 rationale="Reads os.environ in app/ with a duplicated default; settings.redis_url already exists.",
                 fix="redis.Redis.from_url(settings.redis_url, socket_timeout=1.0)"),
            flag("141-F3", "generic_bug", p141_health, "healthy = all(checks.values())",
                 rationale="checks values are the strings 'ok'/'error', both truthy, so all() is always "
                           "True: the endpoint returns 200 even when Redis or the database is down.",
                 fix='healthy = all(v == "ok" for v in checks.values())'),
        ],
        "should_not_flag": [
            trap("141-X1", p141_health, "_redis.ping()", exception="E1",
                 trap="The API layer talks to Redis/the database directly; move this into a service/repository.",
                 reject="Health endpoints are the documented exception in ADR-003, they check infrastructure directly."),
            trap("141-X2", p141_health, "from fastapi.responses import JSONResponse", noise="N3",
                 trap="Import order: fastapi.responses should be grouped with fastapi before sqlalchemy.",
                 reject="Import order is ruff's job in pre-commit, not a review comment."),
            trap("141-X3", p141_health, "def _check_redis() -> str:", noise="N1",
                 trap="_check_redis needs a docstring explaining the return values.",
                 reject="Private helper, self-explanatory; we don't docstring those."),
            trap("141-X4", p141_tests, 'assert len(response.json()["checks"]) == 2', noise="N6",
                 trap="Magic number 2 in the assertion; use a named constant (e.g. EXPECTED_CHECKS).",
                 reject="We keep test literals inline, the test should read on its own."),
        ],
        "drift_trap": None,
    },
))


# ---------------------------------------------------------------------------
# #142 dan: Stripe payout webhook events
# ---------------------------------------------------------------------------

p142_api = modified("app/api/webhooks.py", before='''
    @router.post("/stripe", status_code=204)
    async def stripe_webhook(request: Request, stripe_signature: str = Header()) -> None:
        # Not @idempotent: Stripe retries with the same event id and the service dedupes on it.
        payload = await request.body()
        event = stripe.Webhook.construct_event(payload, stripe_signature, settings.stripe_webhook_secret)
        webhook_service.handle_stripe_event(event)
''', after='''
    @router.post("/stripe", status_code=204)
    async def stripe_webhook(request: Request, stripe_signature: str = Header()) -> None:
        # Not @idempotent: Stripe retries with the same event id and the service dedupes on it.
        payload = await request.body()
        event = stripe.Webhook.construct_event(payload, stripe_signature, settings.stripe_webhook_secret)
        webhook_service.handle_stripe_event(event)


    @router.post("/stripe/payouts", status_code=204)
    async def stripe_payout_webhook(request: Request, stripe_signature: str = Header()) -> None:
        payload = await request.body()
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, settings.stripe_payouts_webhook_secret
        )
        webhook_service.handle_stripe_payout_event(event)
''', start=10)

p142_service = modified("app/services/webhooks.py", before='''
    import stripe
    from sqlalchemy.orm import Session

    from app.db import session_scope
    from app.repos import webhook_events as webhook_event_repo
    from app.utils import clock
    from app.utils.logging import get_logger

    log = get_logger(__name__)


    def handle_stripe_event(event: stripe.Event) -> None:
        with session_scope() as session:
            if webhook_event_repo.exists(session, event.id):
                log.info("stripe_event_duplicate", event_id=event.id)
                return
            webhook_event_repo.insert(session, event.id, event.type, clock.now())
            _dispatch(session, event)


    def _dispatch(session: Session, event: stripe.Event) -> None:
        handler = _HANDLERS.get(event.type)
        if handler is None:
            log.info("stripe_event_unhandled", event_id=event.id, event_type=event.type)
            return
        handler(session, event.data.object)
''', after='''
    import stripe
    from fastapi import HTTPException
    from sqlalchemy.orm import Session

    from app.clients import merchant_notifications
    from app.db import session_scope
    from app.repos import balances as balance_repo
    from app.repos import payouts as payout_repo
    from app.repos import webhook_events as webhook_event_repo
    from app.utils import clock
    from app.utils.logging import get_logger

    log = get_logger(__name__)


    def handle_stripe_event(event: stripe.Event) -> None:
        with session_scope() as session:
            if webhook_event_repo.exists(session, event.id):
                log.info("stripe_event_duplicate", event_id=event.id)
                return
            webhook_event_repo.insert(session, event.id, event.type, clock.now())
            _dispatch(session, event)


    def _dispatch(session: Session, event: stripe.Event) -> None:
        handler = _HANDLERS.get(event.type)
        if handler is None:
            log.info("stripe_event_unhandled", event_id=event.id, event_type=event.type)
            return
        handler(session, event.data.object)


    _PAYOUT_STATUS = {"payout.paid": "paid", "payout.failed": "failed", "payout.canceled": "canceled"}


    def handle_stripe_payout_event(event: stripe.Event) -> None:
        """Apply a Stripe Connect payout event to our payout and the merchant balance."""
        status = _PAYOUT_STATUS.get(event.type)
        if status is None:
            log.info(f"ignoring stripe payout event {event.type} ({event.id})")
            return
        payout_id = event.data.object.metadata.get("acme_payout_id")
        if payout_id is None:
            raise HTTPException(status_code=422, detail=f"event {event.id} has no acme_payout_id")

        with session_scope() as session:
            payout = payout_repo.get(session, payout_id)
            payout_repo.set_status(session, payout_id, status, clock.now())
            if status == "failed":
                balance_repo.credit(session, payout.merchant_id, payout.amount_cents, reason="payout_failed")
            first_delivery = webhook_event_repo.insert_if_new(session, event.id, event.type, clock.now())

        if first_delivery and status == "failed":
            merchant_notifications.payout_failed(merchant_id=payout.merchant_id, payout_id=payout.id)
''')

p142_config = modified("app/config.py", before='''
    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="PAYMENTS_", env_file=".env")

        database_url: str = "postgresql+psycopg://localhost/payments"
        redis_url: str = "redis://localhost:6379/0"
        stripe_api_key: str = ""
        stripe_webhook_secret: str = ""
        card_network_base_url: str = "https://status.cardnet.example"
''', after='''
    class Settings(BaseSettings):
        model_config = SettingsConfigDict(env_prefix="PAYMENTS_", env_file=".env")

        database_url: str = "postgresql+psycopg://localhost/payments"
        redis_url: str = "redis://localhost:6379/0"
        stripe_api_key: str = ""
        stripe_webhook_secret: str = ""
        stripe_payouts_webhook_secret: str = ""
        card_network_base_url: str = "https://status.cardnet.example"
''', start=4)

p142_tests = new_file("tests/test_stripe_payout_webhooks.py", '''
    from types import SimpleNamespace

    import pytest

    from app.services import webhooks
    from tests.factories import make_payout


    @pytest.fixture
    def payout_event():
        def build(event_type, payout_id="po_test_1", event_id="evt_1"):
            obj = SimpleNamespace(metadata={"acme_payout_id": payout_id})
            return SimpleNamespace(id=event_id, type=event_type, data=SimpleNamespace(object=obj))

        return build


    def test_handle_stripe_payout_event__paid__marks_payout_paid(fake_repos, payout_event, frozen_clock):
        fake_repos.payouts.add(make_payout(status="in_transit"))

        webhooks.handle_stripe_payout_event(payout_event("payout.paid"))

        assert fake_repos.payouts.get("po_test_1").status == "paid"


    def test_handle_stripe_payout_event__failed__credits_merchant_balance(fake_repos, payout_event, frozen_clock):
        fake_repos.payouts.add(make_payout(status="in_transit", amount_cents=50_000))

        webhooks.handle_stripe_payout_event(payout_event("payout.failed"))

        assert fake_repos.balances.get("m_1") == 50_000
''')

register(PR(
    142, "Stripe payout webhook events",
    '''
    Stripe Connect sends payout lifecycle events to a separate endpoint (different signing secret),
    so this adds `POST /webhooks/stripe/payouts`.

    - `payout.paid` / `payout.canceled` update the payout status
    - `payout.failed` also credits the amount back to the merchant balance and notifies the merchant

    Needs `PAYMENTS_STRIPE_PAYOUTS_WEBHOOK_SECRET` in staging/prod, already requested from platform.
    ''',
    "dan", "2026-09-17 13:36", [p142_api, p142_service, p142_config, p142_tests],
    labels=["payouts", "webhooks", "stripe"],
    heldout={
        "should_flag": [
            flag("142-F1", "convention", p142_service, "raise HTTPException(status_code=422", convention="C6",
                 rationale="Service raises FastAPI HTTPException; services raise app.errors domain errors and "
                           "the API layer maps them.",
                 fix="Raise a domain error from app.errors (or log and ignore the event) instead of HTTPException."),
            flag("142-F2", "convention", p142_service, 'log.info(f"ignoring stripe payout event', convention="C5",
                 rationale="f-string log message instead of a snake_case event with keyword fields.",
                 fix='log.info("stripe_payout_event_ignored", event_id=event.id, event_type=event.type)'),
            flag("142-F3", "generic_bug", p142_service, 'if status == "failed":',
                 "first_delivery = webhook_event_repo.insert_if_new(",
                 rationale="Dedupe happens after the side effect: a redelivered payout.failed event credits the "
                           "merchant balance again (and resets status) before insert_if_new reports a duplicate.",
                 fix="Check/insert the event id first and return early on duplicates, as handle_stripe_event does."),
        ],
        "should_not_flag": [
            trap("142-X1", p142_api, '@router.post("/stripe/payouts"', "async def stripe_payout_webhook",
                 exception="E2",
                 trap="This POST changes money state but has no @idempotent decorator.",
                 reject="Stripe webhooks are the exception: no Idempotency-Key from Stripe, we dedupe on the event id."),
            trap("142-X2", p142_api, "webhook_service.handle_stripe_payout_event(event)", noise="N7",
                 trap="Wrap the service call in try/except and log the event id on failure before re-raising.",
                 reject="Let it propagate; the error middleware already logs it and Stripe will retry. Wrapping double-logs."),
            trap("142-X3", p142_service, "merchant_notifications.payout_failed(", noise="N5",
                 trap="The merchant notification call has no retry; add retry with exponential backoff.",
                 reject="Our clients are built on make_client(), which already retries. Extra retry layers gave us INC-12."),
            trap("142-X4", p142_tests, "def payout_event():", noise="N2",
                 trap="Type the payout_event fixture and its build() factory (return Callable[..., SimpleNamespace]).",
                 reject="We don't add type hints in tests; mypy excludes tests/."),
        ],
        "drift_trap": None,
    },
))

# ---------------------------------------------------------------------------
# #143 carol: FX conversion for multi-currency refunds
# ---------------------------------------------------------------------------

p143_client = new_file("app/clients/fx_quotes.py", '''
    import httpx

    from app.config import settings


    def refund_rate(from_currency: str, to_currency: str) -> str:
        response = httpx.get(
            f"{settings.fx_base_url}/v1/quotes/refund",
            params={"from": from_currency, "to": to_currency},
            timeout=5.0,
        )
        response.raise_for_status()
        return str(response.json()["rate"])
''')

p143_service = new_file("app/services/refund_fx.py", '''
    from decimal import ROUND_HALF_EVEN, Decimal

    from app.domain.money import exponent
    from app.clients import fx_quotes


    def convert_refund_amount(*, amount_cents: int, from_currency: str, to_currency: str) -> int:
        """Convert a refund requested in the merchant's currency into the charge currency."""
        if from_currency == to_currency:
            return amount_cents
        rate = Decimal(fx_quotes.refund_rate(from_currency, to_currency))
        converted = _to_major(amount_cents, from_currency) * rate
        return _to_minor(converted, to_currency)


    def _to_major(amount_cents: int, currency: str) -> Decimal:
        return Decimal(amount_cents).scaleb(-exponent(currency))


    def _to_minor(amount: Decimal, currency: str) -> int:
        return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
''')

p143_tests = new_file("tests/test_refund_fx.py", '''
    from app.clients import fx_quotes
    from app.services import refund_fx


    def test_convert_refund_amount__same_currency__returns_amount_unchanged():
        assert refund_fx.convert_refund_amount(amount_cents=2_500, from_currency="EUR", to_currency="EUR") == 2_500


    def test_convert_refund_amount__eur_to_usd__applies_quote_rate(monkeypatch):
        monkeypatch.setattr(fx_quotes, "refund_rate", lambda f, t: "1.0842")

        result = refund_fx.convert_refund_amount(amount_cents=10_000, from_currency="EUR", to_currency="USD")

        assert result == 10_842


    def test_convert_refund_amount__usd_to_gbp__rounds_to_nearest_penny(monkeypatch):
        monkeypatch.setattr(fx_quotes, "refund_rate", lambda f, t: "0.785")

        result = refund_fx.convert_refund_amount(amount_cents=1_010, from_currency="USD", to_currency="GBP")

        assert result == 793
''')

register(PR(
    143, "FX conversion for multi-currency refunds",
    '''
    Merchants can now request a refund in their settlement currency when the charge was in another
    currency. This adds the conversion step: we fetch a refund quote rate from the FX service and
    convert the requested amount into the charge currency.

    Uses the refund quote endpoint rather than the payouts rate, since FX wants refunds priced at
    the quote rate. Wiring into `create_refund` comes in the next PR, behind the multi-currency flag.

    Rounding is half-even to match what finance uses for payouts.
    ''',
    "carol", "2026-09-22 10:27", [p143_client, p143_service, p143_tests],
    labels=["refunds", "fx", "money"],
    heldout={
        "should_flag": [
            flag("143-F1", "convention", p143_service, "rate = Decimal(fx_quotes.refund_rate(",
                 "converted = _to_major(amount_cents, from_currency) * rate", convention="C1",
                 rationale="Money maths done in Decimal major units; ADR-007 superseded Decimal: amounts stay "
                           "int minor units and conversions use app.domain.money helpers.",
                 fix="Get the rate as an integer (e.g. rate_micros) and compute with round_half_even_div on "
                     "int minor units, scaling by exponent() of both currencies."),
            flag("143-F2", "convention", p143_client, "response = httpx.get(", convention="C3",
                 rationale="Raw httpx.get call; outbound HTTP must go through make_client() (ADR-006) for "
                           "timeouts, retries, request ids and metrics.",
                 fix='_client = make_client("fx", settings.fx_base_url); _client.get("/v1/quotes/refund", params=...)'),
            flag("143-F3", "generic_bug", p143_service, "return int((amount * 100).quantize(",
                 rationale="Converts back to minor units with a hardcoded 100, ignoring the target currency's "
                           "exponent: refunds into JPY/KRW come out 100x too large (and KWD 10x too small).",
                 fix="Scale by 10 ** exponent(currency), as _to_major already does for the source currency."),
        ],
        "should_not_flag": [
            trap("143-X1", p143_service, "def _to_major(amount_cents: int, currency: str) -> Decimal:", noise="N1",
                 trap="Add docstrings to _to_major/_to_minor explaining the unit conversion.",
                 reject="These are private helpers; their names document them. Only public service functions get docstrings."),
            trap("143-X2", p143_tests, "assert result == 10_842", noise="N6",
                 trap="Magic numbers in the assertions; derive the expected value from named constants.",
                 reject="We keep literals inline in tests so each one reads on its own."),
            trap("143-X3", p143_service, "from app.domain.money import exponent", "from app.clients import fx_quotes",
                 noise="N3",
                 trap="Imports are out of alphabetical order (app.domain before app.clients).",
                 reject="Import sorting is handled by ruff in pre-commit; not worth a review comment."),
        ],
        "drift_trap": {
            "conflict": "K1",
            "expected": "Flag the Decimal money arithmetic as an ADR-007 violation even though Decimal was the "
                        "accepted approach in #103/#106; that was superseded by integer minor units in #112.",
        },
    },
))

# ---------------------------------------------------------------------------
# #144 erin: Payout reversal endpoint
# ---------------------------------------------------------------------------

BEFORE_144 = '''
    from fastapi import APIRouter

    from app.api.schemas import CreatePayoutRequest, PayoutResponse
    from app.services import payouts as payout_service
    from app.utils.idempotency import idempotent

    router = APIRouter(prefix="/payouts", tags=["payouts"])


    @router.post("", response_model=PayoutResponse, status_code=201)
    @idempotent
    def create_payout(body: CreatePayoutRequest) -> PayoutResponse:
        payout = payout_service.create_payout(
            merchant_id=body.merchant_id, amount_cents=body.amount_cents, currency=body.currency
        )
        return PayoutResponse(**payout.as_dict())
'''

AFTER_144 = BEFORE_144.replace(
    "from fastapi import APIRouter", "from fastapi import APIRouter, Body, HTTPException", 1
).replace(
    "    from app.services import payouts as payout_service",
    "    from app.db import session_scope\n"
    "    from app.repos import payouts as payout_repo\n"
    "    from app.services import payouts as payout_service",
) + '''

    @router.post("/{payout_id}/reversal", response_model=PayoutResponse, status_code=201)
    def reverse_payout(payout_id: str, reason: str = Body(embed=True)) -> PayoutResponse:
        with session_scope() as session:
            payout = payout_repo.get(session, payout_id)
        if payout is None:
            raise HTTPException(status_code=404, detail=f"payout {payout_id} not found")
        reversal = payout_service.reverse_payout(payout, reason=reason)
        return PayoutResponse(**reversal.as_dict())
'''

p144_api = modified("app/api/payouts.py", before=BEFORE_144, after=AFTER_144)

p144_service = modified("app/services/payouts.py", before='''
    from app.clients import bank_transfers
    from app.db import session_scope
    from app.domain.models import Payout
    from app.repos import payouts as payout_repo
    from app.utils import clock
    from app.utils.ids import new_id
    from app.utils.logging import get_logger

    log = get_logger(__name__)


    def create_payout(*, merchant_id: str, amount_cents: int, currency: str) -> Payout:
        payout = Payout(
            id=new_id("po"),
            merchant_id=merchant_id,
            amount_cents=amount_cents,
            currency=currency,
            status="pending",
            created_at=clock.now(),
        )
        with session_scope() as session:
            payout_repo.insert(session, payout)
        bank_transfers.submit(payout)
        log.info("payout_submitted", payout_id=payout.id, merchant_id=merchant_id)
        return payout
''', after='''
    from app import errors
    from app.clients import bank_transfers
    from app.db import session_scope
    from app.domain.models import Payout
    from app.domain.money import apply_bps
    from app.repos import payouts as payout_repo
    from app.utils import clock
    from app.utils.ids import new_id
    from app.utils.logging import get_logger

    log = get_logger(__name__)


    def create_payout(*, merchant_id: str, amount_cents: int, currency: str) -> Payout:
        payout = Payout(
            id=new_id("po"),
            merchant_id=merchant_id,
            amount_cents=amount_cents,
            currency=currency,
            status="pending",
            created_at=clock.now(),
        )
        with session_scope() as session:
            payout_repo.insert(session, payout)
        bank_transfers.submit(payout)
        log.info("payout_submitted", payout_id=payout.id, merchant_id=merchant_id)
        return payout


    # Bank charges us 0.25% to claw a transfer back; passed through to the merchant.
    REVERSAL_FEE_BPS = 25


    def reverse_payout(payout: Payout, *, reason: str) -> Payout:
        """Claw back a sent payout, minus the bank's reversal fee."""
        if payout.status in ("pending", "failed"):
            raise errors.Conflict(f"payout {payout.id} was never sent, cancel it instead")
        fee_cents = apply_bps(payout.amount_cents, REVERSAL_FEE_BPS)
        reversal = Payout(
            id=new_id("por"),
            merchant_id=payout.merchant_id,
            amount_cents=payout.amount_cents - fee_cents,
            currency=payout.currency,
            status="reversal_pending",
            created_at=clock.now(),
        )
        with session_scope() as session:
            payout_repo.insert(session, reversal)
            payout_repo.set_status(session, payout.id, "reversed", clock.now())
        bank_transfers.request_reversal(original=payout, reversal=reversal)
        log.info("payout_reversal_requested", payout_id=payout.id, reversal_id=reversal.id, reason=reason)
        return reversal
''')

p144_script = new_file("scripts/find_duplicate_payouts_0919.py", '''
    """One-off: list payouts duplicated by the 2026-09-19 bank file replay, for ops to reverse."""

    import os

    from sqlalchemy import create_engine, text

    engine = create_engine(os.environ["DATABASE_URL"])

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT p.id, p.merchant_id, p.amount_cents, p.currency
            FROM payouts p
            JOIN payouts orig ON orig.merchant_id = p.merchant_id
                AND orig.amount_cents = p.amount_cents
                AND orig.id < p.id
            WHERE p.created_at::date = '2026-09-19' AND p.status = 'paid'
        """))
        for row in rows:
            print(row.id, row.merchant_id, row.amount_cents, row.currency)
''')

p144_tests = new_file("tests/test_payout_reversal.py", '''
    import pytest

    from app import errors
    from app.clients import bank_transfers
    from app.services import payouts
    from tests.factories import make_payout


    @pytest.fixture
    def reversal_requests(monkeypatch):
        calls = []
        monkeypatch.setattr(bank_transfers, "request_reversal", lambda **kw: calls.append(kw))
        return calls


    def test_reverse_payout__paid__returns_amount_minus_fee(fake_repos, reversal_requests, frozen_clock):
        reversal = payouts.reverse_payout(make_payout(status="paid", amount_cents=50_000), reason="duplicate")

        assert reversal.amount_cents == 49_875
        assert len(reversal_requests) == 1


    def test_reverse_payout__pending__raises_conflict(fake_repos, reversal_requests):
        with pytest.raises(errors.Conflict):
            payouts.reverse_payout(make_payout(status="pending"), reason="duplicate")
''')

register(PR(
    144, "Payout reversal endpoint",
    '''
    Adds `POST /payouts/{payout_id}/reversal` so ops can claw back a payout that went out by mistake,
    instead of asking the bank by email. The bank's 0.25% reversal fee is passed on to the merchant.

    Also adds a one-off script that lists the payouts duplicated by the 09-19 bank file replay; ops will
    reverse those with the new endpoint once this is deployed.
    ''',
    "erin", "2026-09-25 15:02", [p144_api, p144_service, p144_script, p144_tests],
    labels=["payouts", "ops"],
    heldout={
        "should_flag": [
            flag("144-F1", "convention", p144_api, '@router.post("/{payout_id}/reversal"',
                 "def reverse_payout(payout_id: str", convention="C4",
                 rationale="Money-moving POST without @idempotent; a retried request reverses the payout twice "
                           "(ADR-004).",
                 fix="Add @idempotent under the route decorator."),
            flag("144-F2", "convention", p144_api, "with session_scope() as session:",
                 "payout = payout_repo.get(session, payout_id)", convention="C2",
                 rationale="Handler opens a session and loads the payout itself; handlers call services only "
                           "(ADR-003).",
                 fix="Make reverse_payout(payout_id=..., reason=...) load the payout and raise errors.NotFound; "
                     "the handler just calls it."),
            flag("144-F3", "generic_bug", p144_service, 'if payout.status in ("pending", "failed"):',
                 rationale="The status guard doesn't exclude 'reversed' (or 'reversal_pending'), so an "
                           "already-reversed payout can be reversed again, clawing the money back twice.",
                 fix='Only allow status == "paid" (or explicitly reject "reversed"/"reversal_pending").'),
        ],
        "should_not_flag": [
            trap("144-X1", p144_service, "fee_cents = apply_bps(payout.amount_cents, REVERSAL_FEE_BPS)", noise="N4",
                 trap="Percentage fee computed with integers; use Decimal to avoid rounding errors on money.",
                 reject="Integer minor units is the rule since ADR-007, and apply_bps rounds half-even for us."),
            trap("144-X2", p144_tests, "def reversal_requests(monkeypatch):", noise="N2",
                 trap="Add type hints to the reversal_requests fixture.",
                 reject="Tests stay untyped; mypy excludes tests/."),
            trap("144-X3", p144_api, "reversal = payout_service.reverse_payout(", noise="N7",
                 trap="Catch exceptions from reverse_payout, log them with the payout id, then re-raise.",
                 reject="Domain errors propagate to the error middleware, which already logs; this would double-log."),
            trap("144-X4", p144_script, 'engine = create_engine(os.environ["DATABASE_URL"])',
                 exception="E3",
                 trap="Use app.config.settings instead of os.environ, and logging instead of print.",
                 reject="It's a one-off in scripts/; print and os.environ are fine there, same as the refund reasons backfill."),
        ],
        "drift_trap": None,
    },
))
