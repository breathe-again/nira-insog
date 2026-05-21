"""SQLAlchemy engine, session factory, and declarative base.

Usage pattern:

    from common.db import SessionLocal

    with SessionLocal() as session:
        ...

In FastAPI routes, use `Depends(get_db)` to get a scoped session per request.
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from api.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency — yields a session, ensures cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
