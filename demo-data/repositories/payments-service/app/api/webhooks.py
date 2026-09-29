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
