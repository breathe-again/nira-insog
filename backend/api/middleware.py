"""HTTP middleware: security headers + rate limiting.

Security headers we set (chosen for an SMB SaaS API serving a SPA via Caddy):

- Strict-Transport-Security: force HTTPS for a year (prod only).
- X-Content-Type-Options: nosniff — stop browsers from guessing MIME types.
- X-Frame-Options: DENY — clickjacking defence.
- Referrer-Policy: strict-origin-when-cross-origin — leak the origin only.
- Permissions-Policy: deny everything the API doesn't need.
- Cross-Origin-Opener-Policy / Resource-Policy: opt into modern isolation.

We DON'T set Content-Security-Policy here — the SPA's CSP is set by Caddy on
the frontend container so it can be tuned without redeploying the API.

Rate limiting uses slowapi (a flask-limiter port for Starlette). It keys on
the client IP and the route name; limits are configured per-endpoint in code.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, Response
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from .config import get_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[get_settings().rate_limit_default],
    # Keep state in-process. For multi-replica deploys, point this at Redis
    # with `storage_uri="redis://..."` — Upstash works fine.
)


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> Response:
    return Response(
        content='{"detail":"rate limit exceeded"}',
        status_code=429,
        media_type="application/json",
        headers={"Retry-After": str(getattr(exc, "retry_after", 60))},
    )


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set a tight default set of security response headers."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        settings = get_settings()
        headers = response.headers

        # HSTS only in prod (over HTTPS). 1 year, include subdomains, preloadable.
        if settings.is_prod:
            headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains; preload",
            )

        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault(
            "Permissions-Policy",
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=()",
        )
        # API never needs to be embedded as a resource by another origin.
        headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")

        # Don't leak server software.
        if "Server" in headers:
            del headers["Server"]

        return response


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def install_middleware(app: FastAPI) -> None:
    """Attach rate-limit + security-headers middleware to the FastAPI app."""
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
