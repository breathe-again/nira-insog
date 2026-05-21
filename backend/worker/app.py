"""Celery application.

The worker is a separate container that runs alongside the API. It shares
the same Postgres database and reads its broker URL from REDIS_URL.

Tasks live in worker/tasks.py and are auto-discovered via the `include` list.
"""

from __future__ import annotations

from celery import Celery

from api.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "nira_insig",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
    include=["worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Isolate from other apps that may be sharing the same Upstash Redis
    # instance. Without this, ANY publisher hitting the default `celery`
    # queue can land tasks our worker doesn't know how to handle, and our
    # tasks could be eaten by other workers. Custom queue name = strict
    # tenant separation across apps even when the broker is shared.
    task_default_queue="nira_insig",
    task_default_exchange="nira_insig",
    task_default_routing_key="nira_insig",
)
