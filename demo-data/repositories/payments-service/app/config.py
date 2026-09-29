from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PAYMENTS_", env_file=".env")

    database_url: str = "postgresql+psycopg://localhost/payments"
    redis_url: str = "redis://localhost:6379/0"
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""
    card_network_base_url: str = "https://status.cardnet.example"
    fraud_base_url: str = "https://fraud.internal.example"
    fx_base_url: str = "https://fx.internal.example"
    bank_transfers_base_url: str = "https://transfers.bank.example"
    idempotency_ttl_seconds: int = 86_400


settings = Settings()
