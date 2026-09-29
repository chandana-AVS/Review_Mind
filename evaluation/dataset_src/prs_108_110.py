"""History PRs #108-#110: Pydantic in services (K3), ADR-005, ADR-006 shared HTTP client."""

from lib import FILLER, PR, c, label, modified, new_file, register, thread

# --- #108 Customer lookup service -------------------------------------------------------------

customers_108 = new_file("app/services/customers.py", '''
    import logging
    from decimal import Decimal

    from pydantic import BaseModel

    from app import errors
    from app.db import session_scope
    from app.repos import charges as charge_repo
    from app.repos import customers as customer_repo

    logger = logging.getLogger(__name__)


    class CustomerView(BaseModel):
        id: str
        email: str
        country: str
        lifetime_volume: Decimal
        open_disputes: int


    def get_customer_view(customer_id: str) -> CustomerView:
        with session_scope() as session:
            customer = customer_repo.get(session, customer_id)
            if customer is None:
                raise errors.NotFound(f"customer {customer_id} not found")
            volume = charge_repo.lifetime_volume(session, customer_id)
            disputes = charge_repo.count_open_disputes(session, customer_id)
        return CustomerView(
            id=customer.id,
            email=customer.email,
            country=customer.country,
            lifetime_volume=volume,
            open_disputes=disputes,
        )
''')

api_108 = new_file("app/api/customers.py", '''
    from fastapi import APIRouter

    from app.services import customers as customer_service

    router = APIRouter(prefix="/customers", tags=["customers"])


    @router.get("/{customer_id}")
    def get_customer(customer_id: str) -> dict[str, object]:
        view = customer_service.get_customer_view(customer_id)
        return view.model_dump(mode="json")
''')

register(PR(
    108, "Customer lookup endpoint for the support console",
    '''
    Support needs a single call that shows a customer's lifetime volume and open disputes
    (SUP-212). Adds `GET /customers/{id}` backed by `customer_service.get_customer_view`.

    `lifetime_volume` and `count_open_disputes` are new repo queries (already merged in #106's
    follow-up).
    ''',
    "dan", "2026-02-10 10:05", [customers_108, api_108], merged="2026-02-11 17:20",
    labels=["support"],
    reviews=[
        ("frank", "commented", "2026-02-10 14:40", "Suggestion inline, otherwise fine."),
        ("carol", "approved", "2026-02-11 09:15", "LGTM"),
    ],
    threads=[
        thread(customers_108, "class CustomerView(BaseModel):", [
            c("frank", "2026-02-10 14:32", """
                Returning a bare dict from the service loses all structure. Make `CustomerView` a Pydantic
                model: you get validation, `model_dump(mode="json")` handles the Decimal for you, and the
                handler becomes a one-liner. This is what we do in the platform services."""),
            c("dan", "2026-02-10 16:02", "Makes sense, the Decimal serialisation was annoying me anyway. Changed."),
            c("carol", "2026-02-11 09:12", "Fine by me for now. We don't really have a rule about where Pydantic lives yet, might be worth one."),
        ], "applied", "frank", **label(conflict="K3", strength="contested")),
        thread(api_108, "return view.model_dump", [
            c("carol", "2026-02-11 09:14", "nit: `mode=\"json\"` is doing the heavy lifting here, maybe a comment? Not blocking."),
            c("dan", "2026-02-11 10:30", "I think it's clear enough, leaving it."),
        ], "noise"),
    ],
))

# --- #109 ADR-005 domain dataclasses -------------------------------------------------------------

adr_005 = new_file("docs/adr/ADR-005-domain-dataclasses.md", '''
    # ADR-005: Dataclasses for domain types, Pydantic only at the edges

    - Status: Accepted
    - Date: 2026-02-17
    - Deciders: alice, bob

    ## Context
    We mix Pydantic models and dataclasses in `app/services/`. Validation runs several times per
    request (API schema -> service model -> repo) and Pydantic v2 coercion silently turned "10.5"
    into a number in one service path.

    ## Decision
    - Pydantic models live in `app/api/schemas.py` (request/response) and `app/config.py` only.
    - Domain and service types are `@dataclass(frozen=True, slots=True)` in `app/domain/`.
    - Conversion happens once, in the handler.

    ## Consequences
    - Services cannot rely on Pydantic validation; validate at the API edge.
    - Supersedes the Pydantic `CustomerView` introduced in #108.
''')

models_109 = new_file("app/domain/models.py", '''
    from dataclasses import asdict, dataclass
    from decimal import Decimal


    @dataclass(frozen=True, slots=True)
    class CustomerView:
        id: str
        email: str
        country: str
        lifetime_volume: Decimal
        open_disputes: int

        def as_dict(self) -> dict[str, object]:
            return asdict(self)
''')

customers_109 = modified("app/services/customers.py", before='''
    import logging
    from decimal import Decimal

    from pydantic import BaseModel

    from app import errors
    from app.db import session_scope
    from app.repos import charges as charge_repo
    from app.repos import customers as customer_repo

    logger = logging.getLogger(__name__)


    class CustomerView(BaseModel):
        id: str
        email: str
        country: str
        lifetime_volume: Decimal
        open_disputes: int


    def get_customer_view(customer_id: str) -> CustomerView:
''', after='''
    import logging

    from app import errors
    from app.db import session_scope
    from app.domain.models import CustomerView
    from app.repos import charges as charge_repo
    from app.repos import customers as customer_repo

    logger = logging.getLogger(__name__)


    def get_customer_view(customer_id: str) -> CustomerView:
''')

schemas_109 = new_file("app/api/schemas.py", '''
    from decimal import Decimal

    from pydantic import BaseModel


    class CustomerResponse(BaseModel):
        id: str
        email: str
        country: str
        lifetime_volume: Decimal
        open_disputes: int
''')

api_109 = modified("app/api/customers.py", before='''
    from fastapi import APIRouter

    from app.services import customers as customer_service

    router = APIRouter(prefix="/customers", tags=["customers"])


    @router.get("/{customer_id}")
    def get_customer(customer_id: str) -> dict[str, object]:
        view = customer_service.get_customer_view(customer_id)
        return view.model_dump(mode="json")
''', after='''
    from fastapi import APIRouter

    from app.api.schemas import CustomerResponse
    from app.services import customers as customer_service

    router = APIRouter(prefix="/customers", tags=["customers"])


    @router.get("/{customer_id}", response_model=CustomerResponse)
    def get_customer(customer_id: str) -> CustomerResponse:
        view = customer_service.get_customer_view(customer_id)
        return CustomerResponse(**view.as_dict())
''')

register(PR(
    109, "ADR-005: dataclasses for domain types, Pydantic only at the edges",
    '''
    Writes down what we discussed at Tuesday's arch sync. Pydantic stays at the API boundary
    (`app/api/schemas.py`) and in config; everything inside services uses frozen dataclasses.

    Converts `CustomerView` from #108 as the first example. The remaining Pydantic models in
    services will move over as we touch them.

    Why now: last week's "10.5" coercion bug in the capture path (validation ran in the service
    model, not at the edge) plus ~15% of request time spent re-validating in the refund path.
    ''',
    "bob", "2026-02-16 11:30", [adr_005, models_109, customers_109, schemas_109, api_109],
    merged="2026-02-18 10:10", labels=["architecture"], adr="ADR-005",
    reviews=[
        ("frank", "changes_requested", "2026-02-16 15:10", "I disagree with the direction here, see thread."),
        ("alice", "approved", "2026-02-17 11:45", "Approving. Decision recorded in the ADR."),
        ("carol", "approved", "2026-02-17 12:02", "👍"),
    ],
    threads=[
        thread(adr_005, "Domain and service types are `@dataclass", [
            c("frank", "2026-02-16 15:05", """
                This is a step backwards. Pydantic gives you validation, serialisation and schema generation for
                free, and every platform service uses it throughout. Dataclasses mean we will hand-roll
                validation in services and drift from the rest of the org."""),
            c("bob", "2026-02-16 16:40", """
                We don't want validation in services, that's the point. Input is validated once at the edge. The
                coercion bug last week happened precisely because a service model "helpfully" converted a string.
                Frozen dataclasses are also cheap and immutable, which matters in the refund path."""),
            c("frank", "2026-02-17 09:20", "Other teams manage fine with Pydantic everywhere. I'd rather not diverge."),
            c("alice", "2026-02-17 11:40", """
                I hear you, Frank, but this is a payments-service decision and we've felt the pain directly. Going
                with the ADR: Pydantic at the edges, dataclasses inside. Happy to revisit if it hurts in six months."""),
        ], "rejected", "alice",
            "Team decided (ADR-005) Pydantic stays at the API edge; domain/service types are frozen dataclasses.",
            **label(conflict="K3", strength="enforced")),
        thread(None, None, [
            c("ci-bot", "2026-02-16 11:41", "Coverage report: 89.9% (+0.1%)."),
        ], "noise"),
    ],
))

# --- #110 ADR-006 shared HTTP client ---------------------------------------------------------------

adr_006 = new_file("docs/adr/ADR-006-shared-http-client.md", '''
    # ADR-006: One shared HTTP client for all outbound calls

    - Status: Accepted
    - Date: 2026-02-23
    - Deciders: alice, bob

    ## Context
    Outbound calls use a mix of `requests` and `httpx` with inconsistent, often missing, timeouts.
    The card-network outage on 2026-02-11 hung worker threads for 9 minutes because a
    `requests.get` had no timeout.

    ## Decision
    - All outbound HTTP goes through `app.clients.http.make_client(service_name, base_url)`:
      3 s connect / 10 s read timeouts, 2 retries with jittered backoff on connect errors and
      502/503/504, request-id propagation and metrics.
    - Do not import `requests` or construct `httpx.Client` / call `httpx.get|post` anywhere else.
    - Do not add extra retry loops around client calls.

    ## Consequences
    - `requests` removed from dependencies.
    - Provider SDKs with their own transport (Stripe) are exempt.
''')

http_110 = new_file("app/clients/http.py", '''
    """The only way to make outbound HTTP calls (ADR-006)."""

    import logging

    import httpx

    from app.utils.request_context import current_request_id

    logger = logging.getLogger(__name__)

    _RETRY_STATUSES = {502, 503, 504}


    class _RetryTransport(httpx.HTTPTransport):
        """Connect-level retries plus up to `retries` retries on 502/503/504."""

        def __init__(self, retries: int = 2) -> None:
            super().__init__(retries=retries)
            self._retries = retries

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            response = super().handle_request(request)
            attempt = 0
            while response.status_code in _RETRY_STATUSES and attempt < self._retries:
                attempt += 1
                response.close()
                response = super().handle_request(request)
            return response


    def make_client(service_name: str, base_url: str) -> httpx.Client:
        return httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(10.0, connect=3.0),
            transport=_RetryTransport(),
            headers={"X-Request-Id": current_request_id(), "User-Agent": f"payments/{service_name}"},
        )
''')

card_110 = modified("app/clients/card_network.py", before='''
    import requests

    from app.config import settings


    def network_status(network: str) -> str:
        response = requests.get(f"{settings.card_network_base_url}/v1/networks/{network}/status")
        response.raise_for_status()
        return _parse_status(response.json())
''', after='''
    from app.clients.http import make_client
    from app.config import settings

    _client = make_client("card_network", settings.card_network_base_url)


    def network_status(network: str) -> str:
        response = _client.get(f"/v1/networks/{network}/status")
        response.raise_for_status()
        return _parse_status(response.json())
''')

pyproject_110 = modified("pyproject.toml", before='''
    dependencies = [
      "fastapi>=0.115", "sqlalchemy>=2.0", "alembic>=1.13", "httpx>=0.27",
      "requests>=2.31", "pydantic-settings>=2.4",
    ]
''', after='''
    dependencies = [
      "fastapi>=0.115", "sqlalchemy>=2.0", "alembic>=1.13", "httpx>=0.27",
      "pydantic-settings>=2.4",
    ]
''', start=5)

register(PR(
    110, "ADR-006: shared HTTP client for all outbound calls",
    '''
    Follow-up to the 02-11 card-network incident (worker threads hung for 9 min on a `requests.get`
    with no timeout).

    - adds `app/clients/http.py` with `make_client(service_name, base_url)`: timeouts, 2 retries on
      connect errors / 502 / 503 / 504, request-id header
    - migrates `card_network` off `requests` and drops `requests` from dependencies
    - ADR-006 documents the rule

    Fraud and FX clients don't exist yet; they should be built on this from day one.
    ''',
    "alice", "2026-02-23 09:15", [adr_006, http_110, card_110, pyproject_110],
    merged="2026-02-24 15:30", labels=["architecture", "integrations"], adr="ADR-006",
    reviews=[
        ("bob", "approved", "2026-02-23 13:10", "Yes please. This would have saved us on the 11th."),
        ("dan", "commented", "2026-02-23 14:25", "One question inline."),
        ("carol", "approved", "2026-02-24 10:02", "LGTM"),
    ],
    threads=[
        thread(adr_006, "Do not import `requests`", [
            c("bob", "2026-02-23 13:05", """
                +1 on making this a hard rule rather than a suggestion. In #102 I said requests was fine
                "for now"; that was the wrong call in hindsight. Anything under app/ that talks HTTP should go
                through make_client, no exceptions except SDKs with their own transport."""),
            c("alice", "2026-02-23 13:40", "Agreed. I'll call it out in review from now on."),
        ], "discussed", "alice", **label("C3", conflict="K2")),
        thread(http_110, "while response.status_code in _RETRY_STATUSES", [
            c("dan", "2026-02-23 14:20", "Should callers still add their own retry around calls to, e.g., the card network? I was going to do that for the status poller."),
            c("alice", "2026-02-23 15:02", """
                No. Retries live here and only here. If a caller also retries, you multiply attempts and a flaky
                downstream turns into a retry storm. If some call needs a different policy we'll add a parameter
                to make_client, not a loop at the call site."""),
            c("dan", "2026-02-23 15:10", "Got it, thanks."),
        ], "discussed", "alice", **label("C3")),
    ],
))
