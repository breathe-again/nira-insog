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

    # CORS — comma-separated list of allowed origins.
    # IMPORTANT: in prod this must be a finite list (never "*") because we
    # rely on credentialed cookies and the CORS spec forbids "*" + credentials.
    cors_origins: str = Field(default="http://localhost:5173,http://127.0.0.1:5173")

    # Database
    database_url: str = Field(
        default="postgresql+psycopg://nira:nira@localhost:5432/nira_insig",
        description="SQLAlchemy URL",
    )

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0")

    # ---------- Auth / security ----------
    # JWT signing secret. MUST be set to something long & random in prod.
    # The default below is deliberately obvious so a misconfigured prod boot
    # fails the strong-secret check below.
    jwt_secret: str = Field(default="dev-only-do-not-use-in-prod-CHANGE-ME")
    jwt_algorithm: str = Field(default="HS256")
    access_token_ttl_minutes: int = Field(default=30)
    refresh_token_ttl_days: int = Field(default=30)

    # Cookie config. In prod we set cookie_secure=True so the browser only
    # sends the cookie over HTTPS.
    cookie_secure: bool = Field(default=False)
    cookie_samesite: str = Field(default="lax")  # lax | strict | none
    cookie_domain: str | None = Field(default=None)

    # File encryption at rest. Fernet key (urlsafe base64 of 32 bytes).
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # If unset, files are stored plaintext (with a warning at startup).
    file_encryption_key: str | None = Field(default=None)

    # Demo mode. When True, the API short-circuits auth and uses DEMO_ORG_ID
    # like the original Phase-1 build. NEVER enable in prod.
    demo_mode: bool = Field(default=False)

    # Rate limit defaults (requests / window). Tuned per-route in code too.
    rate_limit_default: str = Field(default="120/minute")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_prod(self) -> bool:
        return self.app_env.lower() in ("prod", "production")

    def validate_for_prod(self) -> list[str]:
        """Return a list of problems if running in prod with unsafe defaults.

        Called once at app startup — the API REFUSES to boot in prod when the
        list is non-empty. This is intentional: we never want a silent default
        secret in production.
        """
        problems: list[str] = []
        if not self.is_prod:
            return problems
        if self.demo_mode:
            problems.append("DEMO_MODE=1 is not allowed in prod")
        if len(self.jwt_secret) < 32 or "dev-only" in self.jwt_secret:
            problems.append(
                "JWT_SECRET must be set to a 32+ char random value in prod"
            )
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be True in prod")
        if not self.file_encryption_key:
            problems.append(
                "FILE_ENCRYPTION_KEY must be set in prod "
                "(generate with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`)"
            )
        if any(o == "*" for o in self.cors_origins_list):
            problems.append("CORS_ORIGINS='*' is not allowed in prod (cookies)")
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
