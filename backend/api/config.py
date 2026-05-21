"""Application configuration loaded from environment variables.

We keep everything in one Settings object so it is easy to test (override) and
easy to audit (one place to see every env var the app reads).
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings for the Nira Insig API service."""

    # App
    app_name: str = "nira-insig-api"
    app_env: str = Field(default="dev", description="dev | staging | prod")
    log_level: str = Field(default="INFO")

    # CORS — comma-separated list of allowed origins
    cors_origins: str = Field(default="http://localhost:5173,http://127.0.0.1:5173")

    # Database
    database_url: str = Field(
        default="postgresql+psycopg://nira:nira@localhost:5432/nira_insig",
        description="SQLAlchemy URL",
    )

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
