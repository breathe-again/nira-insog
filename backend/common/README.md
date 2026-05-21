# backend/common/

Shared building blocks used by every backend module.

Contents:

- `models/` — SQLAlchemy ORM models (one file per entity in `docs/03-data-model.md`).
- `schemas/` — Pydantic schemas for API and inter-module contracts.
- `db.py` — engine, session, transaction helpers.
- `queue.py` — Celery app, task helpers.
- `storage.py` — S3 wrapper.
- `logging.py` — structured logging setup.
- `errors.py` — application-wide exception types.
