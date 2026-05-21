# frontend/

React + TypeScript + Vite + Tailwind frontend.

Surfaces:

- `dashboard/` — the unified "Altogether" view (cash, receivables, payables, insights, forecast).
- `inbox/` — document inbox: drag-drop, list, per-document detail and editing.

Shared concerns live in a `shared/` folder created during build:

- Auth flows (login, signup, Google SSO).
- API client (generated from the FastAPI OpenAPI schema).
- WebSocket client for live updates.
- Design system components (built on shadcn/ui + Tailwind).

State management: React Query for server state, Zustand for UI state. Form handling: React Hook Form + Zod.
