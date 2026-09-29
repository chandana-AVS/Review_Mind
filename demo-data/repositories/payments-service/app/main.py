from fastapi import FastAPI

from app.api import errors, health, payouts, refunds, webhooks

app = FastAPI(title="payments-service")
errors.register(app)
for module in (health, refunds, payouts, webhooks):
    app.include_router(module.router)
