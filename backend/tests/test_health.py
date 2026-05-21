"""Smoke tests for the health endpoints.

These confirm the API itself is up and that its dependencies (Postgres, Redis)
are reachable from inside the container.
"""

from __future__ import annotations

import httpx


def test_root(api: httpx.Client) -> None:
    res = api.get("/")
    assert res.status_code == 200
    data = res.json()
    assert data["service"] == "nira-insig-api"
    assert "version" in data


def test_liveness(api: httpx.Client) -> None:
    res = api.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_readiness_checks_dependencies(api: httpx.Client) -> None:
    res = api.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    # Status should be "ok" — if a dep is down, the test (rightly) fails.
    assert data["status"] == "ok", f"Readiness degraded: {data}"
    assert data["checks"]["postgres"] == "ok"
    assert data["checks"]["redis"] == "ok"
