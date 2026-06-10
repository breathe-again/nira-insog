# Nira Insig — Project State & Handover

_Last updated: 2026-05-29 (after canonical-ledger pivot + 13-week cash forecast shipped)_

This document is a self-contained snapshot of where the project is, what's built,
what's pending, and how to continue. It exists so a fresh Claude conversation (or
new dev) can be brought up to speed without reading prior chat history.

---

## 1. What Nira is

A finance intelligence + aggregation product for Indian SMBs and mid-market
companies. The pitch:

> "Nira sits on top of your Tally / Zoho / banks / GST portal / TRACES and gives
> you a single pane of glass with AI-powered anomaly detection, plain-English
> Q&A over your books, tax intelligence, and 13-week cash forecasting."

**Target customer:** Finance manager at ₹50 Cr – ₹500 Cr revenue Indian companies,
50-500 employees, 1-3 legal entities, using Tally or Zoho Books as their primary
ERP. Pricing target: ₹15–50 K/month per customer.

**Current production customer:** Quantta Analytics Private Limited (the
operator's company — single tenant on prod today).

**Competitors (mid-market segment):** Trovata ($40M raised), Drivetrain ($20M),
Cube, Vena. All foreign. India mid-market is underserved.

**Operating entity:** Quantta Analytics Pvt Ltd (operator).

---

## 2. Tech stack

### Backend (Python)

- **FastAPI** — HTTP API surface
- **SQLAlchemy 2.0** with Mapped/`mapped_column` style
- **Alembic** — DB migrations (currently at revision `0009`)
- **psycopg 3** — Postgres driver
- **Celery + Redis (Upstash on prod)** — async worker for document processing
- **Pydantic v2** — request/response schemas + settings
- **argon2id** (`argon2-cffi`) — password hashing
- **Fernet** (`cryptography`) — file & secrets encryption at rest
- **pyjwt** — JWT access + refresh tokens (refresh tokens hashed server-side
  in the `sessions` table for revocation)
- **slowapi** — per-IP rate limiting
- **sentence-transformers** (`all-MiniLM-L6-v2`, 384-dim) — semantic search +
  vendor-merge embeddings (CPU-only torch in the Docker image to keep size sane)
- **pgvector** — Postgres extension for vector similarity
- **rapidfuzz** — fuzzy vendor matching
- **openpyxl** — XLSX parsing (Trial Balance, Tally exports)
- **anthropic** — Claude API client for Q&A + LLM vision extraction
- **pglast** (v7.13) — Postgres' own parser as a Python library; used to
  AST-validate SQL that Claude generates (replaces a regex parser that produced
  false-positive "unknown table" errors)
- **cohere** — optional cross-encoder rerank for hybrid search
- **json-repair** — fixes LLM-produced JSON that's almost-valid

### Frontend (TypeScript)

- **React 18** + **Vite**
- **Tailwind CSS** with a custom design tokens layer
- **recharts** — all charts
- **lucide-react** — icons
- **react-router-dom** — routing
- **date-fns** — date formatting

### Infrastructure

- **Postgres 17** on **Neon** (managed; prod uses pooled connection with
  `sslmode=require`)
- **Redis 7-alpine** for Celery broker + caches
- **EC2 box** at `/opt/nira-insig` running `docker compose -f
  infrastructure/deploy/docker-compose.prod.yml --env-file .env.prod`
- **Caddy** for TLS termination via Let's Encrypt + reverse proxy
- **Public domain:** `https://insig.nirabalance.com`
- **Uploaded files:** persisted at `/var/nira/uploads` on EBS, encrypted at rest

---

## 3. Architecture — the canonical-ledger pivot

### Why the pivot happened

In May 2026 the operator uploaded Quantta's Tally Trial Balance (`TrialBal.xlsx`).
The cash position in Nira's dashboard read **₹3.26 L** (computed from bank-CSV
running balances). The Trial Balance showed **₹79.91 L**. A 96 % gap.

The structural cause: bottom-up reconstruction from bank statements can never see
journal entries, contra vouchers, related-party loans, statutory provisions,
warrants, capital, gratuity, depreciation, etc. Roughly **90 % of the business
lives outside bank statements** for any company with an accounting team.

### The pivot

Stop treating bank statements as the source of truth. Build a **canonical
ledger** in Nira that every source — Tally, Zoho, bank CSVs, AA, GSTN, manual
journals — posts into. Dashboards + intelligence modules read from the canonical
layer. Bank statements become an **enrichment** source attached to ledger
entries, not the primary data.

### Five layers of the target architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                       NIRA CONTROL PLANE                         │
│                                                                  │
│  Sources:    Tally · Zoho · QuickBooks · Setu AA · GSTN ·        │
│              TRACES · e-Invoice · Bank statements (CSV/PDF)      │
│                                ↓                                 │
│  Connectors: BaseConnector with connect/poll/webhook/backfill    │
│                                ↓                                 │
│  Canonical:  entities · accounts · ledger_entries · transactions │
│                                ↓                                 │
│  Intelligence: anomaly detection · recurring patterns ·          │
│                hybrid search · Q&A · tax · cash forecast ·       │
│                reconciliation · audit                            │
│                                ↓                                 │
│  Surface:    Dashboard · Forecast · Search · Tax · Settings      │
│                                                                  │
│  Cross-cutting: multi-tenant (org_id) · multi-entity · multi-    │
│                 currency · per-tenant secrets · audit log        │
└──────────────────────────────────────────────────────────────────┘
```

### Multi-tenant disciplines kept from day one

These are cheap now and expensive to retrofit later when the product becomes
SaaS with multiple paying customers:

- Every table carries `org_id uuid not null` + index. No exceptions.
- Every query goes through SQLAlchemy with `org_id` filter, enforced by
  `current_org_id` middleware in `api/deps.py`. Never trust client-supplied org_id.
- Multi-entity from day one: ledger-bearing tables also carry `entity_id`.
- Multi-currency from day one: `currency_code` + `amount_inr` + `amount_native`
  on every monetary row. Pure-INR tenants ignore the FX columns.
- Per-tenant config in `tenant_settings(org_id, key, value_json, encrypted)` —
  not env vars. Secrets are Fernet-encrypted.
- Per-tenant secrets resolved via `services/tenant_settings.read_tenant_setting()`
  which falls back to env vars (so current single-tenant operation keeps working).
- Audit log keyed by `(org_id, user_id, action)` via `services/audit.py`.

---

## 4. Database schema (migrations 0001 → 0009)

| Rev | What it does |
| --- | --- |
| 0001 | Initial schema: organizations, users, documents, vendors, clients, bank_accounts, bank_transactions, invoices, receipts, insights, feedback_events, filename_hints, vendor_mutes |
| 0002 | Auth + security: sessions table for refresh token registry, lockout fields on users |
| 0003 | Recurring patterns + Tier-1 tenant learning columns (is_recurring, auto_tagged_by) |
| 0004 | pgvector extension + 384-dim `description_embedding` columns on bank_transactions, receipts |
| 0005 | Document `content_sha256` + `deleted_at` for upload-time dedup and soft-delete |
| 0006 | `invites` table for link-based team invitations |
| 0007 | Hybrid search — tsvector + GIN indexes on bank_transactions, invoices, receipts, vendors (vendors uses `vendor_search_text(text, text[])` IMMUTABLE wrapper function to keep PostgreSQL 17 happy) |
| **0008** | **Canonical ledger** — entities, accounts, ledger_entries, transactions, source_systems, tenant_settings, reconciliation_findings, approvals + approval_policies + approval_actions (empty, behind feature flag). Auto-seeds one entity per existing org and backfills `entity_id` on bank_accounts/bank_transactions/invoices/receipts/documents. Adds `source_system_id` to documents. |
| **0009** | **Cash forecast tables** — cash_forecast_runs, cash_forecast_points, forecast_drivers |

---

## 5. Backend services map

Each module under `backend/services/` and what it does:

- **`anomalies.py`** — per-vendor anomaly detection (z-score against the vendor's
  history). Emits Insight rows with severity.
- **`audit.py`** — `record_event()` API for writing audit_events rows.
- **`embeddings.py`** — sentence-transformer wrappers; embed_text(), batch embed,
  cosine similarity. Falls back gracefully if pgvector isn't available.
- **`encryption.py`** — Fernet wrappers used for file at rest + tenant secret blobs.
- **`extractors/llm_vision.py`** — Claude vision API for PDF/image extraction.
  Model selected via `ANTHROPIC_MODEL` env var (default `claude-sonnet-4-6`).
- **`feedback.py`** — feedback_events writes; used for active-learning future work.
- **`forecasting.py`** — older 30-day seasonal forecast (day-of-month-based). Used
  by the dashboard mini-forecast widget. NOT to be confused with `cash_forecast.py`.
- **`cash_forecast.py`** — **NEW**: 13-week forecast engine. Driver discovery
  (recurring patterns + open invoices + Indian tax calendar), three scenarios
  (pessimistic / likely / optimistic), 5-day triangular smoothing on AR/AP,
  runway-zero detection.
- **`parsers/bank_csv.py`** — CSV bank-statement parser; handles HDFC/ICICI-ish
  formats, balance reconciliation, vendor hint extraction. Rejects ICICR/HDFCR
  bank-reference strings as vendor names.
- **`parsers/tally_xml.py`** — Tally Day Book / Voucher XML parser.
- **`parsers/extracted_json.py`** — Parses the JSON shape that LLM vision returns
  into BankStatementDraft / InvoiceDraft / ReceiptDraft / ComplianceDraft.
- **`qa.py`** — Q&A engine. Builds a schema + samples prompt, calls Claude,
  validates the returned SQL via `pglast` AST, executes against Postgres,
  returns rows to the LLM for a natural-language answer. Tables Claude can
  see are whitelisted. Org_id is appended to every WHERE clause if missing.
- **`recurring.py`** — recurring-pattern detector (≥3 same-amount observations,
  monthly cadence, low coefficient-of-variation). Used by anomaly detector to
  ignore expected spend and by `cash_forecast.py` to project future occurrences.
- **`search_hybrid.py`** — BM25 (Postgres FTS) + dense (pgvector) + optional
  Cohere rerank, fused via Reciprocal Rank Fusion (k=60). Indexes bank_transactions,
  invoices, receipts, vendors.
- **`tenant_settings.py`** — **NEW**: `read_tenant_setting(org_id, key)`,
  `write_tenant_setting(...)`, `is_feature_enabled(...)`. Encrypted-at-rest
  secrets via Fernet. Env-var fallback for current single-tenant ops.
- **`tax/gstin.py`** — GSTIN format + checksum + state-code validation. Has the
  Quantta-real-data regression test fixture (Amazon, Google, Facebook, Zoho, ...)
  pinning the 1,2,1,2 factor sequence (vs the broken 2,1,2,1 that bit prod).
- **`tax/advance_tax.py`** — quarterly advance-tax estimator (4 installments,
  15-Jun/Sep/Dec/Mar).
- **`tax/tds.py`** — TDS section auto-detect (194C/I/J/A/H/Q) + draft generator.
- **`vendors.py`** — vendor resolution (fuzzy match + embedding similarity).
- **`canonical/entities.py`** — **NEW**: get_default_entity, list_entities,
  create_entity, resolve_entity.
- **`canonical/accounts.py`** — **NEW**: chart-of-accounts classifier (Tally
  group → category mapping + name keyword fallback + party-name debit/credit
  heuristic). 25/25 Quantta-real fixtures pass.
- **`canonical/ledger.py`** — **NEW**: post_entry, post_journal, post_opening_balance,
  get_balance, get_category_total, get_trial_balance. Suspense-on-imbalance for
  single-leg ingestion.
- **`canonical/dashboard_kpis.py`** — **NEW**: canonical-first reads with bottom-up
  fallback. has_canonical_data(), get_cash_position(), get_receivables(),
  get_payables(), get_investments(), get_fixed_assets(), get_data_freshness().
- **`connectors/base.py`** — **NEW**: BaseConnector abstract class, ConnectorContext,
  HealthResult, SyncResult, run_connector_method, run_health_check.
- **`connectors/registry.py`** — **NEW**: `@register` decorator + `get_connector()`
  + lazy module imports.
- **`connectors/tally_trial_balance.py`** — **NEW**: TallyTrialBalanceConnector.
  Parses Tally TB XLSX, classifies ledgers, posts opening-balance entries to
  canonical with suspense mirror. Smart classification heuristic for party-name
  ledgers (debit balance → receivables, credit balance → payables).

---

## 6. API routes (mounted in `api/main.py`)

| Router | Prefix | Notable endpoints |
| --- | --- | --- |
| auth_router | `/api/auth` | `/login`, `/signup`, `/refresh`, `/me`, `/change-password`, `/sessions`, `/sessions/{id}` (delete), `/sessions/revoke-others`, `/org` (PATCH) |
| documents_router | `/api/documents` | `POST /` (upload — 409 on SHA-256 duplicate), `GET /`, `GET /{id}`, `PATCH /{id}`, `GET /duplicates`, `POST /{id}/delete-as-duplicate`, `POST /backfill-hashes` |
| vendors_router | `/api/vendors` | list, get, patch, merge |
| insights_router | `/api/insights` | list, dismiss |
| dashboard_router | `/api/dashboard` | `/summary` (KPI strip + cash flow chart + expense breakdown + counterparties + receivables aging + forecast + insights — canonical-first reads for cash / receivables / payables), `/investment-activity`, `/category/{slug}` (drill into Other) |
| feedback_router | `/api/feedback` | feedback writes for active-learning |
| learning_router | `/api/learning` | training status + retrain trigger + forecast summary |
| qa_router | `/api/qa` | `POST /ask` (Claude SQL Q&A) |
| search_router | `/api/search` | hybrid search across bank_transactions + invoices + receipts |
| tax_router | `/api/tax` | `/gstin-health`, `/advance-tax`, `/tds-draft` |
| team_router | `/api/team` | members + pending invites + create/revoke invite (org-scoped) |
| team_public_router | `/api/team/invites/check`, `/accept` | public accept-invite flow |
| **forecast_router** | **`/api/forecast`** | **`POST /cash/run`, `GET /cash`, `GET /cash/drivers`** |

---

## 7. Frontend pages (under `frontend/src/pages/`)

| Page | Route | What it shows |
| --- | --- | --- |
| Login.tsx | `/login` | email + password sign-in |
| Signup.tsx | `/signup` | self-serve account creation |
| AcceptInvite.tsx | `/accept-invite/:token` | accept a team invite |
| Dashboard.tsx | `/` | full home — KPIs (cash, receivables, payables, MTD), cash-flow chart, expense breakdown, counterparties, receivables aging, forecast, insights, investment activity |
| Inbox.tsx | `/inbox` | document list with status badges, filters |
| DocumentDetail.tsx | `/inbox/:id` | one document — file viewer + extraction + linked entities |
| Ask.tsx | `/ask` | Claude Q&A chat bar |
| Insights.tsx | `/insights` | anomaly + recurring-missed-payment feed |
| Learning.tsx | `/learning` | training status, retrain button, seasonal forecast |
| Search.tsx | `/search` | hybrid search across all sources |
| Tax.tsx | `/tax` | GSTIN health table, advance-tax 4-installment timeline, TDS draft |
| **Forecast.tsx** | **`/forecast`** | **NEW: 13-week cash forecast with 3 scenarios + driver explainability** |
| Duplicates.tsx | `/duplicates` | cluster-by-hash UI for duplicate review |
| Health.tsx | `/system` | dev/ops view of system health |
| Settings.tsx | `/settings` | workspace, team, sessions, security, integrations |
| NotFound.tsx | `*` | 404 |

---

## 8. Deployment topology

```
    Browser
       │ https
       ▼
    Caddy (TLS) on EC2
       ├─ /  → frontend container (nginx serving Vite build)
       └─ /api/* → api container (uvicorn, 2 workers)
                     ├─ Postgres → Neon (managed)
                     ├─ Redis    → local container
                     └─ Celery   → worker container (same image as api)
```

- **EC2 box**: `/opt/nira-insig` (Ubuntu).
- **Compose file**: `infrastructure/deploy/docker-compose.prod.yml`
- **Env file**: `/opt/nira-insig/.env.prod` (DATABASE_URL, REDIS_URL,
  ANTHROPIC_API_KEY, FILE_ENCRYPTION_KEY, JWT_SECRET, CORS_ORIGINS, etc.)
- **Upload volume**: `/var/nira/uploads` → mounted into api+worker as `/app/uploads`
- **Backups**: `/var/nira/backups/*.sql.gz` from `pg_dump` against the Neon URL
- **Database**: `postgres:17` schema; we use `postgres:17-alpine` for one-shot
  `pg_dump` containers (the api image is `python:3.12-slim` and doesn't ship
  postgres client tools).

### Deploy commands (manual, no CI/CD yet)

```bash
# ── On the developer's laptop ──
git push origin master

# ── SSH to EC2 ──
cd /opt/nira-insig

# Backup first — non-negotiable
TS=$(date +%Y%m%d-%H%M%S)
PG_URL=$(grep '^DATABASE_URL=' .env.prod | cut -d= -f2- | tr -d '"' | tr -d "'" \
    | sed 's|postgresql+psycopg://|postgresql://|')
docker run --rm -e PG_URL="$PG_URL" postgres:17-alpine \
    sh -c 'pg_dump "$PG_URL"' | gzip > /var/nira/backups/prod-$TS.sql.gz

# Pull + rebuild
git pull origin master
docker compose -f infrastructure/deploy/docker-compose.prod.yml --env-file .env.prod \
    build --no-cache api worker frontend

# Apply migrations in a one-shot container (catches errors without taking API down)
docker compose -f infrastructure/deploy/docker-compose.prod.yml --env-file .env.prod \
    run --rm api alembic upgrade head

# Replace running containers
docker compose -f infrastructure/deploy/docker-compose.prod.yml --env-file .env.prod \
    up -d --force-recreate api worker frontend

# Verify
docker compose -f infrastructure/deploy/docker-compose.prod.yml --env-file .env.prod \
    logs api --tail 30
curl -i https://insig.nirabalance.com/health
docker exec nira-api python scripts/verify_canonical_layer.py
```

---

## 9. What's been built (feature checklist)

### Core platform
- ✅ Multi-tenant data model with `org_id` discipline throughout
- ✅ JWT auth (access + refresh) with server-side session table for revocation
- ✅ argon2id password hashing
- ✅ Fernet-encrypted file storage at rest
- ✅ Audit log (table + service; UI is stubbed)
- ✅ Per-tenant settings with encrypted secret values
- ✅ Per-IP rate limiting via slowapi
- ✅ Soft-delete on documents

### Document ingestion
- ✅ CSV bank-statement parser with balance reconciliation + parse-confidence scoring
- ✅ Tally XML Day Book parser
- ✅ Tally Trial Balance XLSX → canonical ledger (new)
- ✅ PDF / image invoice + receipt extraction via Claude vision
- ✅ SHA-256 upload dedup with 409 on duplicates
- ✅ Duplicate review queue UI

### Intelligence
- ✅ Per-vendor anomaly detection (z-score)
- ✅ Recurring-pattern detection (≥3 same-amount monthly)
- ✅ Missed-payment insights
- ✅ Vendor merging via fuzzy + embedding similarity
- ✅ Investment-activity widget (MF / SGB / warrants — by-scheme breakdown)
- ✅ Dynamic category drill-into-Other
- ✅ Hybrid search (BM25 + pgvector + optional Cohere rerank)
- ✅ Q&A over the books (Claude + pglast SQL validation)
- ✅ **13-week cash forecast (new)** — three scenarios, AR/AP smoothing, runway-zero detection

### Tax intelligence
- ✅ GSTIN format + checksum + state validation (with regression tests
  pinned against real Quantta vendors)
- ✅ Advance-tax 4-installment estimator
- ✅ TDS section auto-detect (194C/I/J/A/H/Q) + 24Q/26Q draft

### Canonical layer (the pivot)
- ✅ Migration 0008: entities, accounts, ledger_entries, transactions,
  source_systems, tenant_settings, reconciliation_findings (+ approvals
  tables behind feature flag)
- ✅ Auto-seeded default entity per existing org
- ✅ entity_id backfilled on existing bank_accounts/bank_transactions/invoices/
  receipts/documents
- ✅ Source connector framework (BaseConnector + registry)
- ✅ Tally Trial Balance connector (XLSX upload → canonical, validated against
  ₹28.13 Cr real TB)
- ✅ Dashboard cash / receivables / payables read canonical-first with
  bottom-up fallback (no breaking change for orgs without TB)
- ✅ Tally Day Book → canonical dual-write stub behind
  `features.canonical_day_book` flag (engine work needed)

### Multi-user / settings
- ✅ Multi-user invites with link flow
- ✅ Active sessions list + per-session revoke + revoke-others
- ✅ Org-name editing (founder-only)
- ✅ Settings page with workspace + team + sessions + security + integrations sections
- ✅ Change-password flow

### Tests
- 198 unit tests passing across canonical accounts, GSTIN, Tally TB, connector
  registry, cash forecast, recurring patterns, vendors, anomalies, etc.
- 6 pre-existing failures in `test_bank_csv.py` and `test_extracted_json.py`
  (unrelated to recent work; need attention separately).

---

## 10. What's pending (prioritised)

### Bucket A — Required before first paying customer (~30 days)
1. Onboarding wizard — connect Tally → upload first CSV → invite CA
2. Email notifications (document processed, forecast updated, anomalies)
3. In-app help / tooltips on every screen
4. CSV / Excel export from every screen
5. Bulk-edit + undo + dry-run
6. Mobile-responsive layout
7. Stripe / Razorpay billing
8. Pricing tier picker
9. Customer support channel (Intercom or email)

### Bucket B — Required before customers >50 employees (~30 days)
1. SSO via WorkOS (SAML + OIDC)
2. RBAC UI (founder / CFO / accountant / view-only / auditor)
3. Two-factor authentication (TOTP)
4. Audit log /audit page
5. Per-tenant rate limiting
6. Sentry error tracking
7. Datadog or Uptime-Robot monitoring
8. Public status page
9. OpenAPI documentation polish + lock + publish
10. Webhook events
11. GitHub Actions deploy workflow
12. Daily automated backups (cron on EC2)
13. Staging environment

### Bucket C — Required before customers >200 employees (~4-6 months parallel)
1. SOC 2 Type II audit prep (Vanta or Sprinto)
2. ISO 27001
3. Penetration test report
4. Data residency commitment (already on AWS Mumbai — needs documenting)
5. Customer-managed encryption keys
6. DPA template + privacy policy + ToS (DPDP Act 2023 compliance)
7. DR runbook + RPO/RTO commitments
8. Single-tenant deployment option

### Bucket D — Killer features that close deals (run in parallel)
- Bank-Tally reconciliation engine + UI (tables exist; engine + UI are tasks #66/#67)
- GSTR-2B reconciliation (manual JSON first, GSP later)
- TRACES Form 26AS reconciliation
- Setu Account Aggregator integration (blocked on FIU signup at bridge.setu.co)
- Zoho Books OAuth connector
- Multi-entity consolidation UI
- Approval workflows UI (schema exists, behind `features.approvals_enabled`)
- Slack / WhatsApp daily digest
- AI close-assistant (close checklist + Claude-driven journal suggestions)
- Vendor health scoring (1-100)
- Per-bank CSV parsers (HDFC/ICICI/SBI/Axis/Kotak/YES/IDFC/IndusInd)
- Filing-grade Income Tax computation engine

### Bucket E — Recently completed but worth knowing
- ✅ pglast AST validator for Q&A SQL (replaces buggy regex parser)
- ✅ Hybrid retrieval BM25 + dense + rerank
- ✅ GSTIN checksum bug fix (factor sequence was inverted — 2,1,2,1 instead of 1,2,1,2)
- ✅ 13-week cash forecast with smoothing + scenario fix

---

## 11. Open issues + recent lessons

### Production incidents during this rollout

1. **Local dev DB wiped** — `docker system prune -af --volumes` deleted the
   postgres volume containing Quantta's local-dev data. Prod (Neon) was
   unaffected. **Lesson:** never use `--volumes` flag on prune without
   confirming what's in there. Always `pg_dump` before any destructive op.

2. **Postgres 16 vs 17 mismatch** — `postgres:16-alpine` `pg_dump` can't connect
   to Neon's Postgres 17 server. **Fix:** use `postgres:17-alpine` for one-shot
   backups.

3. **SQLAlchemy URL scheme vs pg_dump** — `postgresql+psycopg://...` doesn't
   parse for vanilla `pg_dump`. Strip the `+psycopg` driver suffix with sed.

4. **GSTIN checksum was inverted** — original code toggled `factor` BEFORE use,
   producing 2,1,2,1 sequence instead of 1,2,1,2. Result: every real GSTIN in
   prod read "checksum mismatch". Fixed; 33 regression tests pin the algorithm
   against real public-record GSTINs.

5. **Migration 0007 failed on Postgres 17** — `vendors.search_tsv` GENERATED
   column rejected because `array_to_string` in concatenation context was
   considered non-immutable. Fixed by wrapping in an explicitly-declared
   IMMUTABLE SQL function (`vendor_search_text`).

6. **Frontend TS build failed during canonical-ledger deploy** — 6 type errors
   (unused import, wrong prop names, formatINR called with string instead of
   number). Fixed; new test pattern: always run `tsc -b && vite build`
   locally before pushing.

7. **API container ran old image after partial deploy** — frontend build
   failure cancelled the api build too, so the api container restarted from
   the cached image without the new forecast routes. Manual rebuild required.

8. **Neon password was leaked in chat** — appeared in a `pg_dump` error
   message that included the URL. Should be rotated; operator was notified.

9. **Cash forecast scenario clustering** — overdue invoices shifted to earlier
   dates in optimistic scenario clustered at day 0, producing a fake ₹1.6 Cr
   spike. Fixed: don't shift overdue invoices further; collection-window
   heuristic already encodes the uncertainty. Also added 5-day triangular
   smoothing to AR/AP so chart doesn't look like step functions.

### Known data state

- **Prod (Neon):** Quantta Analytics with 263 bank_transactions, 93 invoices,
  90 vendors, 199 documents, 2 orgs, 2 users. Real production data with real
  customer/vendor PII. Treat with care.
- **Default entity:** auto-seeded on migration 0008 for both Demo Org and
  Quantta Analytics.
- **Canonical ledger on prod is empty** — operator hasn't uploaded
  `TrialBal.xlsx` on prod yet. Until they do, cash position remains
  ₹3.26 L (bank-CSV reconstruction). After upload, jumps to ₹79.91 L.

---

## 12. Where to find things (file roadmap)

```
nira-insig/
├─ ARCHITECTURE_PLAN.md          ← the original pivot plan + 14-day execution
├─ PROJECT_STATE.md              ← THIS FILE
├─ Makefile                      ← dev shortcuts (make up, make backup-db, …)
├─ docker-compose.yml            ← LOCAL dev compose
├─ infrastructure/
│  ├─ deploy/
│  │  ├─ docker-compose.prod.yml ← PROD compose for EC2
│  │  ├─ Caddyfile               ← TLS + reverse proxy
│  │  └─ README.md
│  └─ terraform/                 ← (planned but not wired)
├─ backend/
│  ├─ Dockerfile                 ← python:3.12-slim base + CPU-only torch
│  ├─ requirements.txt           ← all Python deps
│  ├─ alembic/versions/          ← migrations 0001..0009
│  ├─ api/
│  │  ├─ main.py                 ← FastAPI app + router registration
│  │  ├─ deps.py                 ← current_user / current_org_id middleware
│  │  ├─ middleware.py
│  │  ├─ security.py             ← hash_password / verify_password / JWT
│  │  ├─ schemas.py              ← Pydantic schemas
│  │  └─ routes/
│  │     ├─ auth.py
│  │     ├─ dashboard.py         ← canonical-first KPIs
│  │     ├─ documents.py
│  │     ├─ forecast.py          ← NEW
│  │     ├─ qa.py
│  │     ├─ search.py
│  │     ├─ tax.py
│  │     ├─ team.py
│  │     └─ (etc)
│  ├─ common/
│  │  ├─ db.py                   ← SessionLocal, get_db
│  │  ├─ models.py               ← ALL SQLAlchemy models including canonical
│  │  ├─ enums.py
│  │  └─ storage.py              ← open_document() with decrypt
│  ├─ services/
│  │  ├─ canonical/              ← NEW: ledger, accounts, entities, dashboard_kpis
│  │  ├─ connectors/             ← NEW: base, registry, tally_trial_balance
│  │  ├─ extractors/llm_vision.py
│  │  ├─ parsers/                ← bank_csv, tally_xml, extracted_json
│  │  ├─ tax/                    ← gstin, advance_tax, tds
│  │  ├─ cash_forecast.py        ← NEW
│  │  ├─ tenant_settings.py      ← NEW
│  │  └─ (anomalies, embeddings, encryption, qa, search_hybrid, etc.)
│  ├─ worker/
│  │  ├─ app.py                  ← Celery app
│  │  └─ tasks.py                ← process_document() state machine
│  ├─ scripts/
│  │  ├─ entrypoint.sh           ← runs alembic upgrade then uvicorn
│  │  └─ verify_canonical_layer.py ← post-deploy diagnostic
│  └─ tests/
│     ├─ test_documents.py       ← integration (needs live stack)
│     ├─ test_health.py
│     ├─ test_models.py
│     └─ unit/                   ← pure-Python tests (no stack needed)
│        ├─ test_canonical_accounts.py
│        ├─ test_cash_forecast.py
│        ├─ test_connector_registry.py
│        ├─ test_gstin_checksum.py
│        ├─ test_tally_trial_balance.py
│        └─ (etc)
└─ frontend/
   ├─ Dockerfile.prod            ← builds Vite bundle → nginx static
   ├─ src/
   │  ├─ App.tsx                 ← routes
   │  ├─ api.ts                  ← API client (all backend calls)
   │  ├─ types.ts                ← all TypeScript types
   │  ├─ contexts/AuthContext.tsx
   │  ├─ components/
   │  │  ├─ Layout.tsx
   │  │  ├─ Sidebar.tsx          ← nav with NEW badge support
   │  │  ├─ TopBar.tsx
   │  │  ├─ SectionCard.tsx
   │  │  ├─ EmptyState.tsx
   │  │  └─ (etc)
   │  ├─ pages/
   │  │  ├─ Dashboard.tsx
   │  │  ├─ Forecast.tsx         ← NEW
   │  │  ├─ Tax.tsx
   │  │  ├─ Settings.tsx
   │  │  ├─ Login.tsx / Signup.tsx / AcceptInvite.tsx
   │  │  └─ (etc)
   │  └─ lib/
   │     ├─ format.ts            ← formatINR, formatINRShort, timeAgo
   │     └─ cn.ts                ← className helper
   └─ package.json
```

---

## 13. How to continue this work

If you're a fresh Claude conversation or a new dev picking this up:

1. **Read this file first** + `ARCHITECTURE_PLAN.md` for the strategic picture.
2. **Check the task list** — the operator uses TaskCreate/TaskUpdate in their
   conversation tool. Last we left off at task 69 (forecast smoothing — done).
   Pending: tasks 66 + 67 (reconciliation engine + page).
3. **Verify the build is green** before changes:
   ```bash
   cd backend && python -m pytest tests/unit/ --no-header
   ```
   Should show 198 passed, 6 pre-existing failures.
4. **Verify the code parses** after any backend change:
   ```bash
   python -c "import ast; ast.parse(open('the/changed/file.py').read())"
   ```
5. **Local dev** — `make up` for full stack, `make rebuild` if anything weird.
   Demo Org user is at `founder@quantta.example` / `changeme-quantta-2026`
   (set after the local-dev DB wipe; the seeded email was `founder@demo.local`
   but the newer email-validator rejects `.local` TLDs).
6. **Prod deploy** — see the deploy commands in section 8 above. Always
   `pg_dump` first, always run migrations in a one-shot container before
   recreating live containers.
7. **Anthropic model** controlled by `ANTHROPIC_MODEL` and
   `ANTHROPIC_FALLBACK_MODEL` env vars. Defaults are `claude-sonnet-4-6` and
   `claude-haiku-4-5-20251001` respectively.

### Conventions

- All new tables: `org_id uuid not null references organizations(id)
  on delete cascade` + index.
- All money: `Numeric(20, 2)` with INR + native columns where applicable.
- All datetimes: `DateTime(timezone=True)` with `server_default=now()`.
- All UUID PKs with `server_default=gen_random_uuid()`.
- Pydantic schemas use snake_case (matches DB columns).
- Frontend types use camelCase (transformed at the API client boundary).
- Tests: pure-Python in `tests/unit/`, integration in `tests/`.
- One feature = one Alembic revision = one migration file.

### Don't do

- Don't `docker system prune -af --volumes` without `pg_dump` first.
- Don't introduce SQL strings concatenated with user input (Q&A is the only
  user-driven SQL path, and it goes through `pglast` AST validation).
- Don't bypass `current_org_id` middleware. Every query must filter by org.
- Don't store secrets in env vars meant for SaaS multi-tenancy — use
  `tenant_settings.write_tenant_setting(..., encrypted=True)`.
- Don't change `cash_forecast.py` confidence math without re-running the
  22 regression tests in `tests/unit/test_cash_forecast.py`.
- Don't ship a SOC 2-bearing feature without parallel evidence collection
  for the audit.

---

## 14. Quick stats (as of this snapshot)

- **Backend lines of Python:** ~12,000
- **Frontend lines of TypeScript:** ~8,500
- **Migrations:** 9
- **Backend services modules:** 15+
- **API routes:** 13 routers, ~60 endpoints
- **Frontend pages:** 15
- **Unit tests:** 198 passing
- **Production tenant:** 1 (Quantta Analytics)
- **Production data:** 263 bank_transactions, 93 invoices, 90 vendors,
  199 documents
- **Cash position truth gap (pre-canonical-pivot):** 96 % (₹3.26 L vs
  ₹79.91 L Tally truth)

---

End of state snapshot. For strategic / build-plan context, read
`ARCHITECTURE_PLAN.md` next.
