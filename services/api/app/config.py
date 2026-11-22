from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

NEXCONN_HOSTS: dict[str, tuple[str, str]] = {
    "SG": ("https://api.sg-light-api.com", "https://api-b.sg-light-api.com"),
    "NA": ("https://api.us-light-api.com", "https://api-b.us-light-api.com"),
    "SA": ("https://api.sau-light-api.com", "https://api-b.sau-light-api.com"),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    app_env: str = "development"
    web_base_url: str = ""
    api_base_url: str = ""
    vercel_project_production_url: str = ""
    migrate_on_start: bool = False
    cors_origins: str = "http://localhost:5173"
    demo_mode: bool = False
    cron_secret: str = ""

    jwt_secret: str = ""
    guest_link_secret: str = ""
    otp_pepper: str = ""
    field_encryption_keys: str = ""

    database_url: str = "postgresql+psycopg://pakkaa:pakkaa@localhost:5432/pakkaa"
    database_url_unpooled: str = ""

    nexconn_app_key: str = ""
    nexconn_app_secret: str = ""
    nexconn_area: str = "SG"
    nexconn_bot_user_id: str = "pakkaabot"
    nexconn_bot_name: str = "Pakkaa"

    openai_api_key: str = ""
    llm_model_fast: str = "gpt-6-luna"
    llm_model_live: str = "gpt-6.1-terra"
    llm_model_smart: str = "gpt-6.2-sol"
    llm_model_deep: str = "gpt-6.2-astra"
    llm_embedding_model: str = "text-embedding-3-small"
    llm_transcribe_model: str = "gpt-transcribe"

    webhook_max_skew_seconds: int = Field(default=300)
    serverless: bool = Field(default=False, validation_alias="VERCEL")

    @field_validator("database_url", "database_url_unpooled")
    @classmethod
    def _psycopg_driver(cls, v: str) -> str:
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @property
    def public_url(self) -> str:
        """Where people open the app. Vercel provides its production domain; locally it is the Vite server."""
        if self.web_base_url:
            return self.web_base_url.rstrip("/")
        if self.vercel_project_production_url:
            return f"https://{self.vercel_project_production_url}"
        return "http://localhost:5173"

    @property
    def secure_cookies(self) -> bool:
        return self.serverless or self.public_url.startswith("https")

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def nexconn_hosts(self) -> tuple[str, str]:
        try:
            return NEXCONN_HOSTS[self.nexconn_area.upper()]
        except KeyError as exc:
            raise ValueError(f"Unsupported NEXCONN_AREA {self.nexconn_area!r}") from exc


@lru_cache
def get_settings() -> Settings:
    return Settings()
