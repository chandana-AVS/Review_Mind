"""Reference PR: #113 fraud-check client. Copy this style."""

from lib import PR, c, label, modified, new_file, register, thread

fraud = new_file("app/clients/fraud.py", '''
    import httpx

    from app.config import settings


    def score_charge(charge_id: str, amount_cents: int, currency: str, card_fingerprint: str) -> int:
        response = httpx.post(
            f"{settings.fraud_base_url}/v2/score",
            json={
                "charge_id": charge_id,
                "amount_cents": amount_cents,
                "currency": currency,
                "card_fingerprint": card_fingerprint,
            },
            timeout=5.0,
        )
        response.raise_for_status()
        return _risk_score(response.json())


    def _risk_score(payload: dict[str, object]) -> int:
        return int(payload["score"])
''')

charges = modified("app/services/charges.py", before='''
    from app import errors
    from app.db import session_scope
    from app.domain.models import Charge
    from app.repos import charges as charge_repo
    from app.utils import clock


    def capture_charge(*, charge_id: str, amount_cents: int) -> Charge:
        with session_scope() as session:
            charge = charge_repo.get(session, charge_id)
            if charge is None:
                raise errors.NotFound(f"charge {charge_id} not found")
            charge_repo.mark_captured(session, charge.id, amount_cents, clock.now())
        return charge


    def authorize_charge(*, charge_id: str, card_fingerprint: str) -> Charge:
        with session_scope() as session:
            charge = charge_repo.get(session, charge_id)
            charge_repo.mark_authorized(session, charge.id)
        return charge
''', after='''
    from app import errors
    from app.clients import fraud
    from app.db import session_scope
    from app.domain.models import Charge
    from app.repos import charges as charge_repo
    from app.utils import clock

    FRAUD_BLOCK_THRESHOLD = 80


    def capture_charge(*, charge_id: str, amount_cents: int) -> Charge:
        with session_scope() as session:
            charge = charge_repo.get(session, charge_id)
            if charge is None:
                raise errors.NotFound(f"charge {charge_id} not found")
            charge_repo.mark_captured(session, charge.id, amount_cents, clock.now())
        return charge


    def authorize_charge(*, charge_id: str, card_fingerprint: str) -> Charge:
        with session_scope() as session:
            charge = charge_repo.get(session, charge_id)
            if charge is None:
                raise errors.NotFound(f"charge {charge_id} not found")
            score = fraud.score_charge(charge.id, charge.amount_cents, charge.currency, card_fingerprint)
            if score >= FRAUD_BLOCK_THRESHOLD:
                raise errors.PaymentDeclined("fraud_score_too_high")
            charge_repo.mark_authorized(session, charge.id)
        return charge
''')

register(PR(
    113, "Score charges with the fraud service before authorization",
    '''
    Adds a small client for the internal fraud-scoring service (`/v2/score`) and calls it from
    `authorize_charge`. Charges scoring 80+ are declined with `PaymentDeclined("fraud_score_too_high")`.

    Threshold agreed with the risk team (#risk-payments thread from 03-12). Next step is making it per-merchant.

    Testing: unit tests with the fraud client mocked; ran against fraud staging manually.
    ''',
    "dan", "2026-03-16 09:41", [fraud, charges], merged="2026-03-18 11:05",
    labels=["integrations", "risk"],
    reviews=[
        ("frank", "commented", "2026-03-16 13:20", "Fraud service is flaky under load, see my inline note."),
        ("alice", "changes_requested", "2026-03-16 15:02", "Logic looks right. One blocker: this needs to go through the shared client."),
        ("alice", "approved", "2026-03-17 16:48", "Thanks Dan, good to go."),
    ],
    threads=[
        thread(fraud, "httpx.post(", [
            c("alice", "2026-03-16 14:55", """
                Please build this on `make_client("fraud", settings.fraud_base_url)` instead of calling
                `httpx.post` directly. Module-level httpx calls skip our timeouts, request-id propagation and
                the http_call metrics, which is exactly what bit us on 02-11. See ADR-006."""),
            c("dan", "2026-03-16 16:10", "Ah, I copied this from an old spike. I thought an explicit `timeout=5.0` was enough. Switching now."),
            c("alice", "2026-03-16 16:31", "Timeout is only one of the things the shared client does. Rule of thumb: nothing in app/ constructs its own httpx client or imports requests."),
        ], "applied", "alice", **label("C3", mistake="M1")),
        thread(charges, "score = fraud.score_charge", [
            c("frank", "2026-03-16 13:18", "Wrap this in a tenacity `@retry(stop=stop_after_attempt(5), wait=wait_exponential())`. Every call to an internal service should have retries with backoff, it's basic resilience."),
            c("bob", "2026-03-16 17:02", """
                Please no. Once this moves to make_client it already retries connect errors and 502/503/504 twice
                with jitter. Adding tenacity on top multiplies it: 3 x 5 = 15 attempts per charge. That's exactly
                how we got INC-12, the retry storm against the card network in February. One retry layer, and it
                lives in the client."""),
            c("frank", "2026-03-17 09:12", "Fair, I didn't know the shared client retried. Withdrawing."),
        ], "rejected", "bob",
            "make_client() already retries; stacking another retry layer caused the INC-12 retry storm.",
            **label(noise="N5")),
        thread(None, None, [
            c("ci-bot", "2026-03-16 09:52", "Coverage report: 91.4% (+0.2%). No new uncovered lines in app/services."),
        ], "noise"),
    ],
))
