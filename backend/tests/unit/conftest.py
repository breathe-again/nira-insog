"""Unit-test conftest.

Overrides the live-API `wait_for_api` fixture from `tests/conftest.py` so
these pure-Python tests can run without the docker compose stack up.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def wait_for_api() -> None:  # type: ignore[override]
    """No-op override — unit tests don't need the live API."""
    return
