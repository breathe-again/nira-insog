"""FastAPI entry point for Nira Insig.

Endpoints:
- GET  /                              — service info
- GET  /health                        — liveness (no deps)
- GET  /api/health                    — readiness (checks Postgres + Redis)
- POST /api/auth/signup               — create org + first user
- POST /api/auth/login                — email+password
- POST /api/auth/logout               — revoke refresh session
- POST /api/auth/refresh              — rotate refresh + mint new access
- GET  /api/auth/me                   — current user/org
- POST /api/auth/change-password
- POST /api/documents                 — upload a document
- GET  /api/documents                 — list documents
- GET  /api/documents/{id}            — fetch one document
- PATCH /api/documents/{id}           — edit fields (feedback)
- GET  /api/vendors                   — list vendors with spend stats
- PATCH /api/vendors/{id}             — rename / set category / add alias
- POST  /api/vendors/{id}/merge       — merge two vendors
- GET  /api/vendors/{id}/transactions — bank txns + receipts for a vendor
- GET  /api/insights                  — list insights (filterable)
- POST /api/insights/{id}/dismiss     — dismiss an insight
- PATCH /api/insights/{id}            — change severity / mute vendor
- GET  /api/dashboard/summary         — dashboard view-model

Every protected endpoint requires a valid JWT (Bearer or cookie). The DEMO_MODE
env flag bypasses auth for local dev only.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import get_settings
from .deps import ensure_demo_org
from .middleware import install_middleware
from .routes.auth import router as auth_router
from .routes.dashboard import router as dashboard_router
from .routes.documents import router as documents_router
from .routes.feedback import router as feedback_router
from .routes.insights import router as insights_router
from .routes.vendors import router as vendors_router
from common.db import SessionLocal
from common.storage import ensure_upload_root
from services.encryption import warn_if_disabled as warn_encryption_disabled

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("Starting %s in %s mode", settings.app_name, settings.app_env)

    # Refuse to boot in prod with unsafe defaults.
    problems = settings.validate_for_prod()
    if problems:
        for p in problems:
            logger.error("PROD CONFIG ERROR: %s", p)
        # In prod, this is fatal. In dev / staging we just warn.
        if settings.is_prod:
            print("FATAL: prod config errors — refusing to start.", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            raise SystemExit(2)

    warn_encryption_disabled()

    engine: Engine = create_engine(settings.database_url, pool_pre_ping=True)
    app.state.engine = engine
    app.state.redis = redis.from_url(settings.redis_url, decode_responses=True)

    ensure_upload_root()

    # Seed the demo org only when running in demo mode (single-tenant dev).
    if settings.demo_mode and not settings.is_prod:
        try:
            with SessionLocal() as session:
                ensure_demo_org(session)
        except Exception as e:  # noqa: BLE001
            logger.warning("Demo org bootstrap skipped at startup: %s", e)

    try:
        yield
    finally:
        logger.info("Shutting down")
        engine.dispose()
        await app.state.redis.aclose()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Nira Insig API",
        version="0.2.0",
        description="Financial insight engine — Phase 1 + auth.",
        lifespan=lifespan,
    )

    # CORS: locked down to the configured origin list (never "*" + credentials).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
        max_age=600,
    )

    # Security headers + rate limiting.
    install_middleware(app)

    # Routes.
    app.include_router(auth_router)
    app.include_router(documents_router)
    app.include_router(vendors_router)
    app.include_router(insights_router)
    app.include_router(dashboard_router)
    app.include_router(feedback_router)

    @app.get("/")
    def root() -> dict[str, Any]:
        return {
            "service": settings.app_name,
            "env": settings.app_env,
            "version": app.version,
            "message": "Nira Insig API is running.",
        }

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness — process is up. No dependency checks."""
        return {"status": "ok"}

    @app.get("/api/health")
    async def api_health() -> dict[str, Any]:
        """Readiness — checks Postgres + Redis."""
        checks: dict[str, str] = {}
        try:
            with app.state.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["postgres"] = "ok"
        except Exception as e:  # noqa: BLE001
            checks["postgres"] = f"error: {e.__class__.__name__}"

        # Sync Redis client + pre-parse `ssl_cert_reqs` (Celery vs redis-py
        # mismatch on the URL form — see deploy notes).
        try:
            import ssl as _ssl
            from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
            import redis as redis_sync

            parsed = urlparse(settings.redis_url)
            qs = dict(parse_qsl(parsed.query))
            cert_req_str = qs.pop("ssl_cert_reqs", None)
            cert_req_const = None
            if cert_req_str:
                upper = cert_req_str.upper()
                if not upper.startswith("CERT_"):
                    upper = "CERT_" + upper
                cert_req_const = getattr(_ssl, upper, _ssl.CERT_REQUIRED)
            cleaned = urlunparse(parsed._replace(query=urlencode(qs)))

            kwargs = {
                "decode_responses": True,
                "socket_connect_timeout": 3,
                "socket_timeout": 3,
            }
            if cert_req_const is not None:
                kwargs["ssl_cert_reqs"] = cert_req_const

            sync_client = redis_sync.from_url(cleaned, **kwargs)
            pong = sync_client.ping()
            checks["redis"] = "ok" if pong else "error: no pong"
            sync_client.close()
        except Exception as e:  # noqa: BLE001
            checks["redis"] = f"error: {e.__class__.__name__}: {e}"[:200]

        overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
        return {"status": overall, "checks": checks}

    return app


app = create_app()
