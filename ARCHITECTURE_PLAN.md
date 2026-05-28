# Nira — Refined Architecture & Build Plan

_Last updated: 2026-05-27 — Phase 1 + Phase 2a shipped_

## What ships in this commit

**Foundation (F1, F1b, F2):** Migration `0008_canonical_ledger` introduces
the canonical layer — `entities`, `accounts`, `ledger_entries`,
`transactions`, `source_systems`, `tenant_settings`,
`reconciliation_findings`, plus empty `approvals*` tables behind a per-tenant
feature flag. SQLAlchemy models live in `common/models.py`. Service helpers
live in `services/canonical/{entities,accounts,ledger,dashboard_kpis}.py`
and `services/tenant_settings.py`. Multi-entity + multi-currency columns
present on every monetary table from day one. Every existing org gets an
auto-seeded default entity; existing rows backfill `entity_id`.

**Source connector framework (F2):** `services/connectors/base.py` and
`services/connectors/registry.py`. `BaseConnector` exposes
`connect/poll/backfill/health_check/ingest_payload/ingest_file`. Per-tenant
config + Fernet-encrypted secrets resolved via `ConnectorContext`.

**First connector (C1):** `services/connectors/tally_trial_balance.py`.
TB XLSX upload → canonical ledger. Smart classifier handles party-name
debit/credit inference. Validated against Quantta's real TrialBal.xlsx
(₹28.13 Cr, 117 ledgers — 100% parse, 100% balance check).

**Worker wired (C1 integration):** `worker/tasks.py` detects Tally Trial
Balance XLSX uploads (filename hint + content sniff) and routes them
through the new connector. Other XLSX paths continue to use the legacy
LLM extractor.

**Dashboard rewired (I1a):** `api/routes/dashboard.py` uses
`services.canonical.dashboard_kpis` for cash position, receivables,
payables. Dual-read fallback keeps everything working for orgs without
canonical data. For Quantta, once `TrialBal.xlsx` is uploaded the cash
KPI jumps ~₹3.26L → ₹79.91L.

**Verification script:** `backend/scripts/verify_canonical_layer.py`
prints schema status + per-org KPI breakdown + trial-balance integrity
check. Run inside the api container after applying the migration.

**Tests:** 79 new unit tests across classifier, parser, connector
registry. The integration test parses the actual `TrialBal.xlsx` and
asserts debits = credits = ₹28.13 Cr.

## How to deploy

1. Apply migration: `docker compose exec api alembic upgrade head`
2. Restart worker: `docker compose restart worker`
3. Upload `TrialBal.xlsx` via the existing `/api/documents` upload route
   (filename triggers the new path automatically).
4. Run verification: `docker compose exec api python scripts/verify_canonical_layer.py`
5. Open the dashboard — cash position should now reflect Tally truth.

## What's still pending

- **C1b (Tally Day Book → canonical):** code path stubbed behind
  `features.canonical_day_book` flag; needs period-coordination logic
  to avoid double-counting against the TB opening balances.
- **I1b extension:** Investment / Fixed Asset / Loans / P&L breakdown
  widgets — helpers exist; dashboard route doesn't surface them yet.
- **C2 / C3 / C4 / C5 / I2:** Setu AA, Zoho, GSTR-2B, TRACES, cross-
  source reconciliation.

---

---

## 1. The pivot in one sentence

**Stop reconstructing the books from bank statements. Make ledgers (Tally / Zoho / QuickBooks) the source of truth; Nira becomes the intelligence + reconciliation layer over multiple sources.**

---

## 2. Why we're pivoting

The Quantta `TrialBal.xlsx` upload exposed a structural ceiling, not a bug:

| Source | Cash position |
| --- | --- |
| Nira dashboard today (from bank CSVs) | **₹3.26 L** |
| Tally Trial Balance (truth) | **₹79.91 L** |

The 96% gap lives in journal entries, contra vouchers, cash receipts, other bank accounts, related-party loans, ₹10 Cr warrants, ₹4.58 Cr SGB, ₹18.18 L gratuity provision, ₹9.37 Cr Lichee payable — places a bank statement never sees. Uploading more statements can't close this gap. The architecture has to change.

---

## 3. Three patterns considered

| Pattern | What it means | Verdict |
| --- | --- | --- |
| **A. Replace Tally** | Build chart-of-accounts, double-entry, GSTR-1/3B filing, e-invoice | ❌ 12-18 months pure engineering vs incumbents with 25-year head start |
| **B. Intelligence over Tally** | Tally stays the daily workflow; Nira pulls and adds AI/reconciliation/tax | ✅ Where we have natural fit |
| **C. OCR / document intake** | Drop invoices, push to Tally | ⚠️ Useful workflow but not a moat alone |

**Decision: Pattern B with Pattern C as a feeder.**

---

## 4. Target customer

Finance manager at a mid-sized Indian company:

- Revenue: ₹50 Cr – ₹500 Cr
- Headcount: 50 – 500
- Entities: 1 – 3 (operating + investment + family LLP common)
- Primary ERP: Tally or Zoho Books
- Additional systems: 5-8 disconnected (banks, GSTN, TRACES, Razorpay, payroll, spreadsheets)

Comparable companies in US: Trovata ($40M raised), Drivetrain ($20M), Cube, Vena. India mid-market is underserved.

---

## 5. Target architecture

```
   ┌────────────────────────────────────────────────────────────┐
   │                    NIRA CONTROL PLANE                      │
   │                                                            │
   │   ┌──────────────────────────────────────────────────────┐ │
   │   │  Canonical Ledger (single source of truth in Nira)  │ │
   │   │  ledger_entries · transactions · entities · docs    │ │
   │   │  every row tagged (source_system, source_id,        │ │
   │   │  confidence, last_seen_at)                          │ │
   │   └────────────────────────┬─────────────────────────────┘ │
   │                            │ normalize + dedupe            │
   │   ┌────────────────────────┴─────────────────────────────┐ │
   │   │           Source Connector Framework                 │ │
   │   │                                                      │ │
   │   │  Tally · Zoho · Setu AA · GSTN · TRACES · QB ·       │ │
   │   │  e-Invoice IRN · Bank statements (CSV/PDF/OCR)       │ │
   │   └──────────────────────────────────────────────────────┘ │
   │                                                            │
   │   ┌──────────────────────────────────────────────────────┐ │
   │   │           Intelligence + Workflow Layer              │ │
   │   │                                                      │ │
   │   │  • Cross-source reconciliation                       │ │
   │   │  • Cash forecast (multi-entity consolidated)         │ │
   │   │  • Anomaly detection                                 │ │
   │   │  • Tax intelligence (GSTIN, TDS, advance)            │ │
   │   │  • Approval workflows                                │ │
   │   │  • Semantic search + Q&A                             │ │
   │   │  • Immutable audit log                               │ │
   │   └──────────────────────────────────────────────────────┘ │
   │                                                            │
   │   ┌──────────────────────────────────────────────────────┐ │
   │   │   Multi-entity · RBAC · SSO · SOC 2 · API surface    │ │
   │   └──────────────────────────────────────────────────────┘ │
   └────────────────────────────────────────────────────────────┘
```

Three new architectural concepts:

1. **Canonical ledger** — Nira's normalized representation of every financial event. Every row tagged with origin source + confidence. Multiple sources can claim the same logical entry; mismatches become reconciliation findings.
2. **Source connector framework** — Each integration is a plugin implementing `connect / poll / webhook / backfill / health`. Adding an ERP = implement the interface, not rewrite the dashboard.
3. **Enterprise control plane** — Multi-entity, RBAC, approval workflows, audit log, SSO. Required for finance managers (not founders).

---

## 6. What survives from today

Almost everything. Concrete mapping:

| What you have now | Where it goes |
| --- | --- |
| Bank CSV parser | Connector: "Bank statement (manual upload)" |
| Tally XML parser | Connector: "Tally — Day Book + Trial Balance" |
| PDF / image extraction (Claude vision) | Connector: "Document OCR" |
| Anomaly detection | Intelligence module — reads canonical |
| Recurring patterns | Intelligence module — reads canonical |
| Q&A (pglast + Claude) | Intelligence module — queries canonical |
| Hybrid search (BM25 + pgvector) | Intelligence module — indexes canonical |
| Tax intelligence page | Intelligence module — consumes canonical |
| Duplicate review | Intelligence module — works across connectors |
| Settings / invites / sessions | Control plane — needs RBAC layer |

Dashboard widgets keep their shape; their **data path** changes from `aggregate(bank_transactions)` to `aggregate(ledger_entries)`. SQL-level rewrite, not UI rewrite.

---

## 7. Multi-tenant discipline from day one

Cheap now, expensive to retrofit. We keep these even though Quantta is the only tenant today:

- Every new table has `org_id uuid not null` + index. No exceptions.
- All queries go through SQLAlchemy with `current_org_id` middleware. Never trust client-supplied org_ids.
- Per-tenant config in `tenant_settings(org_id, key, value_json, encrypted bool)` — not env vars. Setu key, Tally URL, GSP creds, feature flags all per-tenant.
- Per-tenant secrets encrypted with existing Fernet key.
- Source connectors are stateless code; cursor + creds live in `source_systems`.
- Audit log keyed by (org_id, user_id, action) via existing `services/audit.py`.
- `read_tenant_setting(org_id, key)` helper falls back to env vars for current single-org operation — no need to seed rows immediately.

---

## 8. Build sequence — six phases, ~50 days engineering

### Phase 1 — Foundation (Week 1-2, ~10 days)

| ID | Item | Effort | Why |
| --- | --- | --- | --- |
| F1 | Canonical ledger schema migration `0008` | 2d | Without this, every other piece is built on sand |
| F2 | Source connector framework (BaseConnector) | 3d | One interface to implement per source |
| F3 | Multi-entity support (entities table + entity_id everywhere) | 3d | Mid-sized cos have subsidiaries from day one |
| F4 | RBAC per-resource role grants | 2d | Required for any finance team |
| F5 | Immutable audit log (already partially exists) | 2d | Required for SOC 2 + GST audit |

### Phase 2 — First Real Source (Week 3, ~5 days)

| ID | Item | Effort | Why |
| --- | --- | --- | --- |
| C1 | Tally connector — TrialBal XLSX + Day Book XML + HTTP auto-sync from AWS | 2d | You already have real Tally on AWS + a working TB file |
| I1a | Rewire Cash Position widget to canonical | 1d | First proof: dashboard jumps ₹3.26 L → ₹79.91 L |
| I1b | Rewire P&L + Balance Sheet KPIs | 3d | Full dashboard now matches Tally |

### Phase 3 — Reconciliation (Week 4, ~6 days)

| ID | Item | Effort | Why |
| --- | --- | --- | --- |
| I2 | Cross-source reconciliation views | 3d | The "Tally vs bank vs GSTN" widget — your moat |
| C2 | Setu AA connector | 3d | Blocked on your Setu FIU signup at bridge.setu.co |

### Phase 4 — More Sources (Week 5-6, ~7 days)

| ID | Item | Effort | Why |
| --- | --- | --- | --- |
| C3 | Zoho Books OAuth connector | 2d | Standard for mid-market SMBs |
| C4 | GSTR-2B connector — manual JSON upload first, GSP later | 2d | Highest tax value |
| C5 | TRACES 26AS via ClearTDS API | 3d | Closes TDS reconciliation |

### Phase 5 — Workflows (OPTIONAL, build only when a customer asks)

Status: **deferred / per-tenant feature flag**. Not part of the default build. Quantta doesn't need it (payments today go through Tally + bank directly). Build when the first paying customer requires it.

| ID | Item | Effort | Why |
| --- | --- | --- | --- |
| W1 | Invoice approval workflow | 3d | Single biggest CFO ask — but only matters for customers who route invoices through Nira before payment |
| W2 | Payment authorization dual-control | 2d | Required for spend approvals — only matters once Nira initiates payments (not while it's read-only) |

**Implementation note.** When built, gated by `tenant_settings.approvals_enabled = true`. Tables (`approvals`, `approval_actions`, `approval_policies`) ship with the F1 migration as empty/unused; no schema change needed when a customer flips the flag.

### Phase 6 — Enterprise Readiness (Week 8-10, ~8 days + ongoing)

| ID | Item | Effort | Why |
| --- | --- | --- | --- |
| E1 | SSO via WorkOS (SAML + OIDC) | 2d | Required to sell to >50-person customers |
| E2 | Public API surface for ERP / DW pulls | 3d | Finance teams want data out, not just dashboards |
| I3 | Multi-entity consolidation (eliminations + intra-group) | 3d | Group customers need this |
| E3 | SOC 2 Type II readiness | ongoing | Required past ~200 employees |

**Default build: ~40-45 engineering days (Phases 1-4 + 6).** Phase 5 (approval workflows) adds ~5 days only if/when a customer requires it. Realistic solo timeline 4-6 months with sales + ops overhead.

---

## 9. Decisions needed before Day 1

Four decisions that are cheap to make now and expensive to retrofit later. My recommendations are starred.

**D1. Multi-entity from day one?**
- ★ Yes — add `entity_id` to every ledger table. One extra column. Future multi-entity clients work without migration.
- No — single entity per org. Painful backfill when first group-company customer signs up.

**D2. Multi-currency from day one?**
- ★ Yes — every ledger entry carries `currency_code` + `amount_inr` + `amount_native`. Pure-INR clients ignore it.
- No — INR only. Painful if SaaS hits an export-heavy IT services / consulting client.

**D3. Tally cutover strategy?**
- ★ Dual-read for 2 weeks, then deprecate. Dashboard merges old `bank_transactions` and new `ledger_entries`. Safer; can roll back instantly.
- Hard cutover after Phase 2. Faster; lose a day if something breaks.

**D4. Tally auto-sync network architecture?**
- ★ Push agent on your AWS Tally box → Nira webhook. Tiny Python script polls Tally locally + POSTs to Nira. Scales to SaaS: every future customer runs the same agent.
- Static-IP Nira backend pulls from Tally (~$20/mo + you open Tally port 9000 to it)
- Wireguard tunnel (overkill for one tenant)

---

## 10. Eyes-open trade-offs

- **Sales cycle: 3-6 months per deal.** Finance managers at 100-500 person companies don't sign up on a website. Outbound + demos + security questionnaires + procurement.
- **Compliance is non-optional.** SOC 2 Type II audit ≈ $15-40K/year + 3-6 months prep. Customers above ~200 employees won't even pilot without it.
- **Pricing is per-seat or per-entity.** Expect ₹15-50K/month per customer. 20-30 customers for sustainable revenue.
- **Single-tenant hardening eventually needed.** Big customers want data residency + customer-managed encryption keys + single-tenant deployments.
- **Competitors are well-funded** but India mid-market is underserved and you have native context they don't.

---

## 11. Week 1 concrete deliverables (5 days work)

Start with the minimum viable proof of the new architecture:

**Day 1-2 — F1: Canonical ledger schema**
- Migration `0008_canonical_ledger.py`:
  - `entities` (id, org_id, legal_name, gstin, pan, currency, parent_entity_id)
  - `accounts` (id, org_id, entity_id, name, normalized_name, type, parent_account_id)
  - `ledger_entries` (id, org_id, entity_id, account_id, period_start, period_end, debit, credit, source_system, source_record_id, source_document_id, confidence, fy)
  - `tenant_settings` (org_id, key, value_json, encrypted bool)
  - `source_systems` (id, org_id, system_type, config_json, cursor_json, status, last_sync_at)
  - `reconciliation_findings` (id, org_id, finding_type, severity, source_a, source_b, delta, status)
- SQLAlchemy models, Pydantic schemas
- `services/canonical/ledger.py`: `post_entry`, `post_journal`, `get_balance`, `get_trial_balance`
- `read_tenant_setting(org_id, key)` helper with env-var fallback
- Unit tests against in-memory ledger

**Day 3 — F2 (lightweight): Connector framework**
- Abstract `BaseConnector` class with `connect / poll / backfill / health`
- Connector registry
- Refactor existing bank-CSV upload to implement this interface (no behavior change)

**Day 4 — C1: Tally Trial Balance ingestion**
- XLSX import builds chart of accounts in `accounts`
- Map Tally ledger groups → canonical account categories
- TrialBal.xlsx upload posts to `ledger_entries`
- Sanity: total debit = total credit = ₹28.13 Cr

**Day 5 — I1a: Cash Position widget rewired**
- Dashboard reads from `ledger_entries` joined to `accounts` where category in (Cash, Bank)
- Cash KPI jumps from ₹3.26 L → ₹79.91 L
- This is the proof the architecture works

After Week 1, you have a working v1 of the new architecture running alongside the old code with real Quantta data. Everything in Phase 2-6 is layered on top of this foundation.

---

## 12. What we are NOT doing (yet)

To stay focused, these are explicitly deferred:

- ❌ Building Tally features Tally already does (chart of accounts UI, journal posting UI, GSTR-1 filing)
- ❌ Mobile app (mid-market finance happens on desktop)
- ❌ E-invoice IRN generation (read-only consumption only for now)
- ❌ Bank payment initiation (read-only)
- ❌ Replacing the user's existing accounting workflow
- ❌ Marketplace / plugin ecosystem (premature)

---

## 13. Strategic positioning

**For now (Phase 1 — Quantta):** Build for self with multi-tenant discipline. Prove the architecture end-to-end.

**Phase 2 (first external customers, ~Month 3-4):** Add 2-3 friendly mid-market customers on Tally. Pricing ₹25-50K/month. Get reference logos.

**Phase 3 (productize, ~Month 6+):** When CEO wants SaaS, the foundation is already there. What's added: WorkOS SSO, billing (Stripe / Razorpay), SOC 2 audit, public landing page, sales outbound.
