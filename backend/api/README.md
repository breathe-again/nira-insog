# backend/api/

FastAPI application. Owns:

- HTTP entry points (REST endpoints).
- Authentication and authorization (JWT sessions, role checks).
- Multi-tenant scoping (`org_id` filter on every query).
- WebSocket channel for live updates.
- OpenAPI schema generation (frontend TS types are generated from this).
