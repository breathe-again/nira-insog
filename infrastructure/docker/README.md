# infrastructure/docker/

Dockerfiles and `docker-compose.yml` for local development and CI.

Planned files:

- `Dockerfile.api` — FastAPI service image.
- `Dockerfile.worker` — Celery workers image (used for extraction, understanding, insights).
- `Dockerfile.frontend-dev` — Vite dev server (optional; most devs run frontend natively).
- `docker-compose.yml` — local stack: api, worker, postgres, redis, mailhog (for testing email-to-inbox), localstack (for S3 emulation).

Goal: `docker compose up` on a fresh laptop boots a working local environment in under 5 minutes.
