# Nira Insig — Pending Work Plan (post Phase-1 demo)

_Last updated: 2026-05-21_

This is the forward plan from where we are today (Phase 1 working: ingest → understand → dashboard, on a single demo org) to a multi-tenant, learning, distribution-ready product.

The plan is split into phases A → F. Each phase is sized to be shippable on its own — we should never have a half-done auth that breaks the demo. Phases are mostly independent except where flagged.

---

## Where we are today

**Working:**
- Upload → extract → understand pipeline for CSV / PDF / image / XLSX / HTML (just added).
- Anthropic vision API extracts bank statements, invoices, receipts.
- Fuzzy vendor resolution (rapidfuzz) + per-vendor anomaly detection (z-score >2σ).
- Dashboard with KPIs, cash flow, expense donut, top-vendors, AR/AP, recent docs — all anchored on real data.
- Production deployment on AWS Mumbai (EC2 + Caddy TLS + Neon Postgres + Upstash Redis) at https://insig.nirabalance.com.
- `FeedbackEvent` table exists in schema — **but no code writes to it yet**.

**Hardcoded shortcuts:**
- `DEMO_ORG_ID` / `DEMO_USER_ID` in `backend/api/deps.py` — every request operates on one tenant.
- No `/login`, `/signup`, password column, or JWT.
- No UI for vendor merge / split, insight tuning, or extraction correction.

---

## Phase A — Auth + multi-tenant   (~5–7 days)

**Goal:** kill `DEMO_ORG_ID`. Real users sign up, log in, and see only their org's data.

**Backend**
- Add `password_hash` (argon2id) + `email_verified_at` columns to `users`. Migration 0002.
- New `auth` route group:
  - `POST /api/auth/signup` → creates Org + first User (role=founder), returns JWT.
  - `POST /api/auth/login` → email+password, returns JWT (httpOnly cookie in browser, Bearer for API).
  - `POST /api/auth/logout` → clear cookie.
  - `GET  /api/auth/me` → current user + org.
- JWT: HS256, 7-day expiry, signed with `JWT_SECRET` env. Refresh on each `me` call.
- Rewrite `current_org_id` / `current_user_id` in `deps.py` to read the JWT.
- Keep a `DEMO_MODE=1` env flag that re-enables the demo org bypass for the founder's own laptop testing — gated, off in prod.
- Rate-limit signup + login (10/min/IP) via slowapi.

**Frontend**
- Two new pages: `Login.tsx`, `Signup.tsx`.
- `AuthProvider` context — checks `/api/auth/me` on mount, redirects to `/login` if 401.
- `Logout` item in TopBar dropdown.
- The existing pages don't change — they still hit the same routes, just with a real JWT instead of demo defaults.

**Tests + acceptance**
- Two orgs A and B, each uploads a doc. A's `/api/documents` never returns B's docs.
- Wrong password 5× in a row → throttled.
- JWT expires → frontend redirects to login.

**Risks**
- The `documents` table is FK'd to `users.id` on `uploaded_by`. We'll mark legacy demo-doc rows as `uploaded_by=NULL` if needed.
- Don't forget to set `secure=true` on the cookie behind Caddy.

---

## Phase B — Feedback loop & "edit & correct" UX   (~4–5 days)

**Goal:** every correction the user makes flows into a `FeedbackEvent` row. The understanding layer reads those events to improve future runs.

This is the biggest moat: real-world Indian SMB documents are messy and the LLM **will** miss things. Capturing the user's fix is what makes us better over time.

**Backend**
- `PATCH /api/documents/{id}` — edit `document_type`, `vendor_id`, `category` on the linked entity (Invoice/Receipt/BankTxn). Writes a `FeedbackEvent` row capturing `field`, `old_value`, `new_value`.
- `PATCH /api/vendors/{id}` — rename, merge, set default category. Merge: re-points all `matched_vendor_id` FKs to the surviving vendor.
- `PATCH /api/insights/{id}` — mark not-a-bug / mute-this-vendor / change-severity. Mute writes a `vendor_anomaly_mute` row consulted by the anomaly checker.
- New service `services/learning.py`:
  - When a user re-categorizes a receipt, write/update `Vendor.default_expense_category` so future receipts from that vendor auto-tag.
  - When a user merges two vendors, save the loser's name as an alias of the winner.
  - When a user changes a doc's `document_type`, store the filename pattern → suggest the same type next time (`FilenameHint` table).

**Frontend**
- Inline edit on `DocumentDetail.tsx`: doc type dropdown, vendor combobox, category dropdown — saves write FeedbackEvent.
- New `Vendors.tsx` page: list, search, click → vendor detail with merge / rename / alias-add.
- "Why this insight?" panel on each insight card → user can mute or correct.

**Tests + acceptance**
- Upload same vendor under three spellings → merge in UI → all three transactions show one canonical name.
- Re-categorize a receipt → next upload from same vendor auto-picks that category.

**Depends on:** Phase A (need a real `user_id` to attribute feedback).

---

## Phase C — Coverage + insight depth   (~5–7 days)

**Goal:** the dashboard answers more questions; more file types & vendors land in the right buckets without intervention.

**Insights to add** (`docs/08-insights-catalog.md` is the source of truth)
- Cash-runway insight: "At current burn you have ~X weeks of runway."
- Recurring-expense detection: flag when a vendor's monthly amount jumps >25%.
- GST mismatch: invoice total ≠ subtotal + tax (commonly wrong on auto-generated bills).
- Late-payer detection: client invoice unpaid >30 days past due.
- Duplicate-invoice detection: same vendor + same `invoice_number` within 90 days.
- Round-number anomaly: txn at exactly ₹50,000 / ₹1,00,000 — often manual entries hiding mistakes.

**Extraction coverage**
- Add `.eml` / forwarded-email body parsing (treat the body like HTML; attachments processed normally).
- Add HEIC support end-to-end (already in dropzone accept; needs server-side Pillow + pillow-heif).
- For PDFs >25 MB (currently rejected): chunk-by-page upload to S3 + per-page extraction.

**UI**
- Dedicated `/insights` page (today they only appear on the dashboard widget).
- Date-range picker on dashboard (today it's a fixed 30-day window).
- Vendor drill-through: clicking a slice of the expense donut opens that vendor's txn list.

**Tests**
- Each insight rule has a unit test with a synthetic txn set that triggers it.
- A regression set of 10 messy real-world docs (anonymized) — extraction success rate ≥ 90%.

---

## Phase D — Distribution + ingestion   (~4–5 days)

**Goal:** the data gets in without anyone uploading, and insights get out without anyone logging in.

**Email-to-inbox ingestion**
- Each org gets a unique address `inbox+<slug>@nirabalance.com`.
- Postmark / Resend inbound webhook → `POST /api/inbound/email` → parses attachments, creates Documents.
- Body of the email becomes a comment on the doc ("Forwarded from accounts@vendor.com on 2026-05-21").

**Slack alerts**
- OAuth Slack connection per org (`SlackInstallation` table).
- Insight created with severity≥attention → Slack message in the user-chosen channel, with Approve / Mute buttons.
- Slash command `/insig last week` → returns the dashboard summary card.

**Scheduled exports**
- Weekly PDF digest email Monday 8am IST: "Last week in 5 numbers."
- "Export to Tally / Zoho Books" — CSV of invoices + receipts in the format those apps import.

**Risks**
- Inbound email needs SPF/DKIM and an MX record we control. Vendor lock-in is real; pick Postmark.

---

## Phase E — Polish + mobile   (~3–4 days)

- Mobile-responsive dashboard. Today the donut + sidebar break <768px.
- React Native or PWA "snap a receipt" screen → drops into the same upload pipeline.
- Skeleton loaders on every page (no more layout shift when data loads).
- Empty states with real CTAs ("No vendors yet — upload your first bill").
- Toasts on every mutation, not just upload.
- A11y: keyboard nav through the inbox, focus rings, semantic landmarks.

---

## Phase F — Compliance-heavy   (~10–15 days, defer until paid users)

**Bank Account Aggregator integration**
- Pick AA provider (Setu, Finvu, OneMoney). Setu has the best devex.
- New `BankConnection` table; OAuth-like consent flow.
- Replace bank-statement uploads with daily polled txn pulls.
- This is the biggest engineering lift — it needs RBI-licensed compliance, encrypted at-rest, audit trail, consent revocation flow.

**Adjacent compliance work**
- Audit log: every PATCH on accounting data → `audit_events` row with actor + diff + ip.
- Per-org S3 prefix with KMS encryption (today: local disk via volume mount).
- Move Neon to `ap-south-1` (currently Singapore — adds ~40ms per query).
- SOC 2 Type 1 readiness checklist (just internal docs at this stage).

---

## Suggested ordering

```
A  (auth)            ─────►  B  (feedback loop)  ─────►  C  (coverage + insights)
                                                          │
                                                          ▼
                                                          D  (Slack + email)
                                                          │
                                                          ▼
                                                          E  (polish + mobile)
                                                          │
                                                          ▼
                                                          F  (AA + compliance)
```

A blocks B (need user_id). B doesn't block C, but doing B first means C's insight rules can be tuned by real feedback. D and E are largely independent and can run in parallel if there are two people on it. F should wait for paying customers — it's expensive to build and harder to undo.

---

## What is **not** in scope here

- Mobile native apps (PWA only in Phase E).
- Multi-currency (everything is INR for now).
- Accounting GL / journal entries — we are an **insight engine**, not a books-of-account tool.
- AI chat ("ask your books a question") — tempting but deferred until the structured data is rock-solid.
