"""Shared pytest fixtures.

Tests run against a live API stack (Postgres + Redis + API + worker via
docker compose). The `api` fixture provides an httpx client pointed at it.

How to run:
    make test                  # runs inside the api container against the live stack
    pytest backend/tests       # locally, if you have the venv set up + stack running
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest


def _api_base() -> str:
    return os.environ.get("API_BASE", "http://localhost:8000")


@pytest.fixture(scope="session")
def api_base() -> str:
    return _api_base()


@pytest.fixture()
def api(api_base: str) -> Iterator[httpx.Client]:
    """An httpx client with a sane timeout and the right base URL."""
    with httpx.Client(base_url=api_base, timeout=30.0) as client:
        yield client


@pytest.fixture(scope="session", autouse=True)
def wait_for_api(api_base: str) -> None:
    """Block until the API responds — useful when tests start before the
    container is fully warm."""
    import time

    deadline = time.time() + 60
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=2.0) as client:
                r = client.get(f"{api_base}/health")
                if r.status_code == 200:
                    return
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(1)
    raise RuntimeError(f"API at {api_base} did not respond within 60s: {last_err}")
