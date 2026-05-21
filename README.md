# Nira Insig — Financial Insight Engine

An AI-powered system that ingests financial documents (bank statements, invoices, receipts), extracts and understands the data, and produces real-time + predictive business insights for founders, CEOs, and finance teams.

**Phase 1 focus:** Accounting (bank transactions, sales invoices, purchase invoices, receipts).
**Future phases:** Sales, HR, Operations, Legal — same engine, new document types.

---

## Quick start (local dev)

### Prerequisites

- **Docker Desktop** — installed and running. ([download](https://www.docker.com/products/docker-desktop/))
- **Make** — comes preinstalled on macOS / Linux.
- That's it. No need to install Python, Node, Postgres, or Redis locally — everything runs in containers.

### Run it

From the repo root:

```bash
make up
```

(Or, equivalently: `docker compose up --build`.)

First boot will take a few minutes (downloading images, installing dependencies). Subsequent starts are fast.

### Open it

Once everything is up, open these in your browser:

| URL | What you see |
|---|---|
| http://localhost:5173 | **App** — sidebar with Dashboard, Inbox, System, Settings. |
| http://localhost:5173 (`Dashboard`) | KPIs, cash flow chart, expense donut, receivables aging, forecast, insights feed, top vendors/clients, compliance. Toggle "Demo data on/off" in the top-right. |
| http://localhost:5173/inbox | Drop files, search/filter, click any row to see the document detail. |
| http://localhost:5173/inbox/:id | Pipeline timeline + extracted JSON for one document. |
| http://localhost:5173/system | Service health dashboard. |
| http://localhost:5173/settings | Workspace, team, integrations (placeholders). |
| http://localhost:8000/docs | Auto-generated Swagger UI. |

**Try it end-to-end:** open the Inbox, drag any PDF/image/CSV onto the dropzone. The status walks `received → extracting → extracted → understood → indexed` over about 3 seconds. Click the row to open the detail view and watch the pipeline timeline animate.

### Stop it

```bash
make down
```

To wipe the Postgres data volume too (full reset):

```bash
make clean
```

### Other handy commands

```bash
make logs           # tail logs from all services
make logs-api       # tail just the API logs
make logs-frontend  # tail just the frontend logs
make ps             # show running containers
make sh-api         # bash into the api container
make sh-db          # psql into the postgres container
make health         # curl /health and /api/health from your shell
make rebuild        # rebuild images from scratch (use after dependency changes)
```

---

## What this repo contains

This repository is the foundation for the Nira Insig product. Right now it holds:

- **Planning documents** in `docs/` — vision, architecture, requirements, roadmap, etc.
- **A runnable local stack** — FastAPI backend, React frontend, Postgres, Redis — wired together via Docker Compose.
- **Skeleton code structure** for the modules we will fill in during Phase 1.

```
nira-insig/
├── docker-compose.yml         ← the local stack definition
├── Makefile                   ← run shortcuts
├── .env.example               ← env vars (copy to .env if overriding)
├── docs/                      ← all planning documents (start here for context)
├── backend/                   ← FastAPI app
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pyproject.toml
│   └── api/                   ← FastAPI routes, config
├── frontend/                  ← React + Vite + Tailwind
│   ├── Dockerfile.dev
│   ├── package.json
│   └── src/                   ← App.tsx, main.tsx
├── ml-models/                 ← (placeholder) extraction, classification, forecasting
├── data/                      ← (placeholder) samples + JSON schemas
└── infrastructure/            ← deploy + docker config (production)
```

## How to read the docs

In order, for full context:

1. `docs/01-vision-and-scope.md` — the *why* and the *what*. CEO-readable.
2. `docs/02-system-architecture.md` — the *how*. Components and data flow.
3. `docs/03-data-model.md` — entities, fields, relationships.
4. `docs/04-functional-requirements.md` — what the system must do.
5. `docs/05-tech-stack.md` — concrete technology choices.
6. `docs/06-roadmap.md` — 12-week Phase 1 plan + future phases.
7. `docs/07-team-and-resources.md` — team composition, tooling, budget.
8. `docs/08-insights-catalog.md` — every insight the system will produce.

## Project status

🟢 **Planning** — architecture and scope locked.
🟢 **Local dev stack** — runnable boot check; API ↔ Postgres ↔ Redis wired up.
🟢 **Data model + migrations** — all 11 Phase 1 tables created via Alembic on first boot.
🟢 **Upload + worker** — `POST /api/documents` stores files, queues Celery job, worker walks status through the state machine.
🟢 **App UI** — sidebar layout with Dashboard (KPIs · charts · forecast · insights · compliance), Inbox (upload · filter · search), Document Detail (pipeline timeline · extracted JSON), System (service health), Settings.
🟢 **Understanding layer (Level 3)** — bank-CSV parser, LLM-JSON parser, rapidfuzz vendor resolution (≥85 token-set ratio), per-vendor anomaly detection (>2σ ⇒ Insight row). Wired into the Celery task. `/api/vendors` and `/api/insights` endpoints live. See "Understanding pipeline" below.
⚪ **Real OCR + LLM extraction** — Tesseract deliberately deferred to keep image size small. The worker calls the parsers as soon as `raw_extraction_json` is set.
⚪ **Phase 1 launch** — target ~12 weeks from build start.

## Understanding pipeline (Level 3)

`backend/services/` holds the pure-Python understanding layer. The Celery worker
(`backend/worker/tasks.py`) walks each Document through this pipeline:

```
received → extracting → extracted → understood → indexed
              │             │            │
              │             │            └─► persist typed rows (BankTransaction
              │             │                / Invoice / Receipt), resolve the
              │             │                vendor (fuzzy match against existing
              │             │                names + aliases), run anomaly check
              │             │                against the vendor's prior history,
              │             │                emit Insight rows when amount > 2σ.
              │             └─► raw_extraction_json populated
              │                 (real parse for CSVs, stub for PDF/image until OCR lands)
              └─► extractor invoked
```

**What's in services/:**

```
backend/services/
├── parsers/
│   ├── bank_csv.py         CSV → BankTxnDraft (tolerates HDFC/SBI/generic headers)
│   └── extracted_json.py   LLM-JSON → InvoiceDraft / ReceiptDraft
├── vendors.py              resolve_vendor / resolve_client (rapidfuzz, ≥85 token-set ratio)
└── anomalies.py            per-vendor µ + σ → Insight rows (>2σ ⇒ attention, >4σ ⇒ urgent)
```

**Try it end-to-end** with the stack running:

```bash
# Upload the sample bank statement — the worker parses it, resolves vendors, runs anomalies.
curl -F "file=@data/samples/sample_bank_statement.csv" http://localhost:8000/api/documents

# Watch what the pipeline produced:
curl http://localhost:8000/api/vendors  | python3 -m json.tool
curl http://localhost:8000/api/insights | python3 -m json.tool
```

**New endpoints:**

| Method | URL | Returns |
|---|---|---|
| GET | `/api/vendors` | Vendors with `txn_count` / `txn_total` / `txn_mean` / receipt rollups |
| GET | `/api/vendors/{id}/transactions` | Recent bank txns + receipts for one vendor |
| GET | `/api/insights` | Insights (filter by `severity`, `type`, `include_dismissed`) |
| POST | `/api/insights/{id}/dismiss` | Dismiss one insight |

## How to test

There are three layers, from fastest to most thorough.

### 0. Unit tests (no stack required, runs in <1s)

```bash
cd backend && PYTHONPATH=. python3 -m pytest tests/unit -q
```

Covers the bank CSV parser, extracted-JSON parser, vendor-name normalization,
and the anomaly stats rule — 60+ tests, all pure-Python, no DB.

### 1. Manual smoke (60 seconds)

With the stack running (`make up`), open http://localhost:5173:

1. **Dashboard loads** — sidebar visible, demo data toggle on the top right works.
2. **Inbox loads** — empty list with a drag-drop zone.
3. **Drop a file** — try `data/samples/sample_bank_statement.csv` from this repo.
4. **Status walks through the pipeline** — row appears in `received`, then progresses through `extracting → extracted → understood → indexed` in ~3 seconds. Status badge turns green.
5. **Click the row** — detail page opens, pipeline timeline shows all steps complete, extracted JSON visible on the right.
6. **System tab** — both Postgres and Redis show `Online`.

If all six pass, the system is healthy end-to-end.

### 2. API smoke from the command line

```bash
make smoke
```

That single command hits `/health`, `/api/health`, and `POST /api/documents` (with a tiny CSV) and prints the response. Useful when you don't want to open a browser.

Or do it by hand with curl:

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
curl -s http://localhost:8000/api/health | python3 -m json.tool
curl -s http://localhost:8000/api/documents | python3 -m json.tool

# Upload a real sample
curl -F "file=@data/samples/sample_bank_statement.csv" \
     http://localhost:8000/api/documents | python3 -m json.tool
```

You can also point your browser at http://localhost:8000/docs (Swagger UI) and click "Try it out" on any endpoint.

### 3. Automated tests (pytest)

A test suite lives under `backend/tests/`:

| File | What it covers |
|---|---|
| `test_health.py` | `/`, `/health`, `/api/health` — readiness, liveness |
| `test_documents.py` | Upload (small + too-large), list, detail, worker round-trip, classification |
| `test_models.py` | Unit-level checks on the schema + storage helpers (no stack needed) |

Run everything:

```bash
make test          # full suite inside the api container, against the running stack
make test-unit     # just the no-stack-needed unit tests (fast)
```

What `make test` expects:
- `make up` is already running (Postgres + Redis + API + worker all live)
- Tests will wait up to 60s for the API to come online before running

Sample output of a passing run:

```
tests/test_health.py::test_root PASSED
tests/test_health.py::test_liveness PASSED
tests/test_health.py::test_readiness_checks_dependencies PASSED
tests/test_documents.py::test_list_documents_returns_envelope PASSED
tests/test_documents.py::test_get_unknown_document_is_404 PASSED
tests/test_documents.py::test_upload_creates_document PASSED
tests/test_documents.py::test_upload_too_large_is_rejected PASSED
tests/test_documents.py::test_worker_processes_document_to_indexed PASSED
tests/test_documents.py::test_document_type_is_inferred_from_filename PASSED
tests/test_models.py::test_all_expected_tables_are_registered PASSED
…
```

### Database / worker introspection (when something's off)

If a test fails or a document stays stuck:

```bash
# Look at the database directly
make sh-db        # opens psql
# Then in psql:
SELECT id, original_filename, status, error_message, created_at, processed_at
FROM documents ORDER BY created_at DESC LIMIT 10;

# Watch the worker logs
make logs         # all services
docker compose logs -f worker     # just the worker

# Get a shell in the api container
make sh-api
```

## Troubleshooting

**`docker compose` says "command not found"**
You have an older Docker. Try `docker-compose up` (with a hyphen) or upgrade Docker Desktop.

**Port already in use (5432, 6379, 8000, or 5173)**
Something else is using that port. Either stop the other process or change the port in `docker-compose.yml`.

**Frontend card shows red error "Could not reach API"**
The API container may still be starting. Wait 30 seconds and the card auto-refreshes every 5s. If it stays red, run `make logs-api` to see why.

**Changed a Python dependency in `requirements.txt`, the API didn't pick it up**
You need to rebuild the image: `make rebuild`.

**Vite says "Failed to resolve import 'foo' from ..."**
The frontend `node_modules` volume is stale (a dep was added in `package.json` but the running container has the old install). Fix:

```bash
make refresh-frontend
```

This stops the frontend service, removes the cached `node_modules` volume, rebuilds the image with the new `package.json`, and brings the frontend back up. Postgres data is preserved.

**Need a clean slate**
`make clean` — stops everything and deletes the Postgres data volume.
