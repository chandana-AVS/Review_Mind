from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    hindsight_base_url: str = "http://localhost:8888"
    hindsight_api_key: SecretStr | None = None
    hindsight_timeout_s: float = 900.0
    hindsight_max_attempts: int = 3
    hindsight_bank_prefix: str = "reviewmind"
    reviewmind_team: str = "acme-payments"

    @property
    def team_bank_id(self) -> str:
        return f"{self.hindsight_bank_prefix}-{self.reviewmind_team}"

    @property
    def demo_bank_id(self) -> str:
        """Clone of the historical bank that receives feedback (the baseline stays pristine)."""
        return f"{self.team_bank_id}-demo"

    @property
    def hindsight_mode(self) -> str:
        return "cloud" if "hindsight.vectorize.io" in self.hindsight_base_url else "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
