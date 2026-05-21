"""FastAPI entry point for Nira Insig.

Endpoints:
- GET  /                              — service info
- GET  /health                        — liveness (no deps)
- GET  /api/health                    — readiness (checks Postgres + Redis)
- POST /api/documents                 — upload a document
- GET  /api/documents                 — list documents
- GET  /api/documents/{id}            — fetch one document
- GET  /api/vendors                   — list vendors with spend stats
- GET  /api/vendors/{id}/transactions — bank txns + receipts for a vendor
- GET  /api/insights                  — list insights (filterable)
- POST /api/insights/{id}/dismiss     — dismiss an insight
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .config import get_settings
from .deps import ensure_demo_org
from .routes.dashboard import router as dashboard_router
from .routes.documents import router as documents_router
from .routes.insights import router as insights_router
from .routes.vendors import router as vendors_router
from common.db import SessionLocal
from common.storage import ensure_upload_root

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("Starting %s in %s mode", settings.app_name, settings.app_env)

    engine: Engine = create_engine(settings.database_url, pool_pre_ping=True)
    app.state.engine = engine
    app.state.redis = redis.from_url(settings.redis_url, decode_responses=True)

    # Make sure the uploads dir exists and the demo org is seeded.
    ensure_upload_root()
    try:
        with SessionLocal() as session:
            ensure_demo_org(session)
    except Exception as e:  # noqa: BLE001
        # If the DB isn't ready yet (race with migrations), we'll seed lazily.
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
        version="0.1.0",
        description="Financial insight engine — Phase 1 (accounting).",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(documents_router)
    app.include_router(vendors_router)
    app.include_router(insights_router)
    app.include_router(dashboard_router)

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

        # Use a SYNC Redis client for the health check. The async client
        # (redis.asyncio) has a known TLS handshake quirk with Upstash's
        # `rediss://` endpoint that's unrelated to the broker actually working
        # — Celery's sync client connects fine. Probing with the same client
        # type Celery uses keeps the readiness signal honest.
        try:
            import redis as redis_sync  # local import — only needed here

            sync_client = redis_sync.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
            pong = sync_client.ping()
            checks["redis"] = "ok" if pong else "error: no pong"
            sync_client.close()
        except Exception as e:  # noqa: BLE001
            checks["redis"] = f"error: {e.__class__.__name__}"

        overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
        return {"status": overall, "checks": checks}

    return app


app = create_app()
