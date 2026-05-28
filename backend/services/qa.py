"""Q&A service — "ask anything" over a tenant's books.

Architecture:

  1. Caller passes a natural-language question + the calling org's id.
  2. We call Claude with a SCHEMA_PROMPT describing the relevant tables
     and ask for ONE read-only SQL statement.
  3. We validate the proposed SQL against a strict allowlist:
     - must start with SELECT (or WITH ... SELECT)
     - no semicolons (no statement chaining)
     - no DDL / DML keywords (DROP/INSERT/UPDATE/DELETE/ALTER/CREATE/...)
     - must reference only whitelisted tables
     - must include `org_id = :org_id` in the WHERE clause somewhere
  4. We execute it with a parameter binding for :org_id and fetch up to
     QA_MAX_ROWS rows.
  5. We call Claude again with (question, sql, sample of rows) and ask for
     a one-paragraph plain-English answer.
  6. Return { question, sql, row_count, sample, answer }.

Why this design (vs. handing Claude a SQL tool and letting it loop):

  - Predictable token cost: exactly 2 LLM calls per question.
  - Single audit point for SQL safety.
  - Easier to test — no agent-loop state.

When `ANTHROPIC_API_KEY` is unset (dev mode), we fall back to a stub answer.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
# Fallback model — used when the primary is overloaded. Haiku is faster
# and rarely rate-limited; quality is lower for complex SQL but acceptable.
FALLBACK_MODEL = os.environ.get("ANTHROPIC_FALLBACK_MODEL", "claude-haiku-4-5-20251001")
QA_MAX_ROWS = 200          # cap rows we send back to Claude / the frontend
QA_MAX_TOKENS = 1500
QA_TIMEOUT_S = 30

# Retry config for transient Anthropic 529/503/429 errors.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_S = (1.0, 3.0, 6.0)   # delay before each retry


# Tables Claude is allowed to query. EVERY one of these has an `org_id`
# column we'll enforce. If you add new tenant-scoped tables later, add them
# here too — leaving them off means Claude can't see them in answers.
_ALLOWED_TABLES = {
    "bank_transactions",
    "invoices",
    "receipts",
    "vendors",
    "clients",
    "documents",
    "insights",
    "recurring_patterns",
}

# Postgres set-returning functions Claude may use inside FROM / JOIN. They
# look like tables to a naive regex (`FROM unnest(...)`) but they're built-in
# functions — they don't read any table and are always safe. Add to this list
# only functions that can't reach into the DB.
_SAFE_FROM_FUNCTIONS = {
    "unnest",
    "generate_series",
    "regexp_split_to_table",
    "string_to_table",
    "jsonb_array_elements",
    "jsonb_array_elements_text",
    "jsonb_each",
    "jsonb_each_text",
    "jsonb_object_keys",
    "json_array_elements",
    "json_array_elements_text",
    "json_each",
    "json_each_text",
    "json_object_keys",
    # values() lists like FROM (VALUES (1),(2)) AS t(x) — alias is matched
    # before the parens so they don't end up in `referenced` anyway, but
    # adding `values` here makes the intent explicit.
    "values",
}


# NOTE: previously we maintained _SQL_TOKENS_NOT_TABLES — a manual exclusion
# list of SQL keywords (case, lateral, ...) and column names (txn_date,
# amount, ...) that the old regex-based parser would mis-identify as
# tables. With pglast doing AST-level parsing we no longer need that
# defence — the parser never confuses a CASE expression for a table.
# Kept here as a comment so future readers don't reintroduce the regex
# approach without realising what it was working around.

# Hard-banned tokens — any of these in the proposed SQL → reject.
_BANNED_PATTERNS = [
    re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|vacuum)\b", re.IGNORECASE),
    re.compile(r";"),
    re.compile(r"--"),
    re.compile(r"/\*"),
    re.compile(r"\bpg_\w+", re.IGNORECASE),   # block pg_catalog / pg_settings / etc.
    re.compile(r"\binformation_schema\b", re.IGNORECASE),
]


SCHEMA_PROMPT = """\
You are a senior data analyst with SQL skills. The user is a founder of an
Indian SMB asking questions about their own financial data. Your job is to
write a single Postgres SELECT statement that answers their question.

DATABASE SCHEMA (only these tables are visible — never reference others):

  bank_transactions:
    id (uuid), org_id (uuid), document_id (uuid),
    txn_date (date), description (text), amount (numeric),
    direction ('credit' | 'debit'), running_balance (numeric, nullable),
    matched_vendor_id (uuid, nullable), matched_client_id (uuid, nullable),
    category (text, nullable), is_recurring (bool, nullable),
    auto_tagged_by (text, nullable)

  invoices:
    id (uuid), org_id (uuid), document_id (uuid),
    type ('sales' | 'purchase'), invoice_number (text),
    vendor_id (uuid, nullable), client_id (uuid, nullable),
    issue_date (date), due_date (date, nullable),
    subtotal, tax, total (numeric), currency (text), status (text)

  receipts:
    id, org_id, document_id, vendor_id (nullable),
    date, amount, tax (nullable), category (nullable),
    payment_mode, notes

  vendors:
    id (uuid), org_id (uuid), name (text), aliases (text[]),
    gstin (text, nullable), default_expense_category (text, nullable),
    created_at

  clients:
    id, org_id, name, aliases, gstin, created_at

  documents:
    id, org_id, original_filename, file_type, document_type, status,
    created_at, processed_at

  insights:
    id, org_id, type (e.g. 'vendor_amount_anomaly'),
    severity ('info'|'attention'|'urgent'),
    title, body, supporting_data (jsonb), created_at, dismissed_at

  recurring_patterns:
    id, org_id, vendor_id, label, cadence,
    expected_day_of_month, median_amount, observed_count,
    first_seen_on, last_seen_on

CRITICAL RULES (must follow without exception):
  1. Output EXACTLY ONE SELECT statement. No semicolons, no comments.
  2. Every table reference MUST be filtered by `org_id = :org_id`. The
     server binds :org_id at execution time — do NOT inline the UUID.
  3. NEVER use INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT,
     COPY, VACUUM, or any DDL. NEVER reference pg_* or information_schema.
  4. Always include LIMIT 200 at the end unless the question explicitly asks
     for fewer rows.
  5. Use Postgres date functions (date_trunc, EXTRACT, NOW(), CURRENT_DATE).
  6. Indian financial year runs Apr 1 → Mar 31.
  7. Cast money columns to numeric/float as needed for clean arithmetic.
  8. Use vendor / client names (joined from vendors / clients tables) in the
     output, NOT the UUIDs.

OUTPUT FORMAT:
  Respond with ONLY the SQL — no prose, no markdown, no ```sql fences,
  no explanation. Just the raw SQL statement.
"""


ANSWER_PROMPT = """\
You are a financial co-pilot for an Indian SMB. The user asked a question
about their books; we ran a SQL query and got the rows below. Write a SHORT
plain-English answer (2-4 sentences max) that directly addresses their
question using the data.

Rules:
  - Format amounts in Indian style — ₹X.XX Cr, ₹X.X L, ₹X,XXX, etc.
  - Format dates naturally — "April 2025", "last quarter", "12 April".
  - If the row count is zero, say so clearly and suggest what data they might
    need to upload.
  - If the answer is a single number / total, lead with that number.
  - Don't say "according to the data" or "based on the query" — just answer
    like a knowledgeable friend.
  - Don't show SQL or technical jargon to the user.
"""


def is_enabled() -> bool:
    """Q&A requires the Anthropic key, same as the OCR extractor."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


class QAError(RuntimeError):
    """Raised when we can't safely answer a question."""


class QAOverloadedError(QAError):
    """Raised specifically when Anthropic is rate-limiting / overloaded after
    all retries. The route maps this to a friendly user-facing 503 message."""


def _is_overloaded(exc: Exception) -> bool:
    """Return True if `exc` looks like a transient Anthropic capacity error.

    Anthropic's SDK raises a typed APIError with status_code; we also fall
    back to substring sniffing in case the SDK wraps it differently."""
    status = getattr(exc, "status_code", None)
    if status in (429, 503, 529):
        return True
    msg = str(exc).lower()
    return any(
        s in msg
        for s in ("overloaded", "rate limit", "rate_limit", "too many requests", "529", "503", "service unavailable")
    )


def _call_with_retry(client, *, model: str, messages: list, system: str, max_tokens: int):
    """Wrap client.messages.create with retry on overload errors. After
    RETRY_ATTEMPTS exhausted on the primary model, tries the fallback model
    once. Raises QAOverloadedError if everything fails with overload."""
    last_exc: Optional[Exception] = None
    # Primary model: retry with backoff.
    for attempt, backoff in enumerate(RETRY_BACKOFF_S, start=1):
        try:
            return client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
        except Exception as e:  # noqa: BLE001
            last_exc = e
            if not _is_overloaded(e):
                # Hard error (auth, malformed request, etc.) — don't retry.
                raise
            logger.warning(
                "Anthropic overloaded (attempt %d/%d on %s): %s — backing off %.1fs",
                attempt, RETRY_ATTEMPTS, model, e, backoff,
            )
            if attempt < RETRY_ATTEMPTS:
                time.sleep(backoff)

    # Last-ditch attempt on the fallback model (smaller / less contended).
    if FALLBACK_MODEL and FALLBACK_MODEL != model:
        logger.warning(
            "Primary model %s overloaded — trying fallback %s",
            model, FALLBACK_MODEL,
        )
        try:
            return client.messages.create(
                model=FALLBACK_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
        except Exception as e:  # noqa: BLE001
            last_exc = e

    raise QAOverloadedError(
        "Claude is overloaded right now — please try again in 30 seconds."
    ) from last_exc


def validate_sql(sql: str) -> str:
    """Validate Claude-proposed SQL via pglast (libpg_query — PostgreSQL's
    own parser) and the layered safety policy.

    Raises QAError if the proposed SQL violates any rule. Returns the
    cleaned SQL (fenced backticks stripped) on success.

    Layers, in order:
      1. Strip ``` fences if the model wrapped the SQL.
      2. Parse via pglast.  If the parser rejects it, surface the syntax
         error — Claude can retry.
      3. Walk the AST:
           - Top statement must be a SelectStmt (single statement only).
           - Every relation reference (FROM, JOIN, subquery FROM) must
             resolve to either _ALLOWED_TABLES or a CTE defined within.
           - Every function call in a FROM/JOIN position must be in
             _SAFE_FROM_FUNCTIONS (no pg_*, no information_schema, etc.).
           - Banned tokens (DDL keywords, pg_catalog, semicolons) are
             impossible to parse as a SELECT, so the syntax check
             handles them, but we double-check via _BANNED_PATTERNS for
             defence-in-depth.
      4. The :org_id bind parameter must appear textually — every
         tenant-scoped table must filter by it.

    pglast is the same parser PostgreSQL uses internally, so any SQL that
    passes this validator behaves identically to what the database would
    accept. No more "unknown table 'case'" false positives.
    """
    cleaned = sql.strip()

    # Strip ```sql ... ``` fence if the model added one despite instructions.
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```\w*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
        cleaned = cleaned.strip()

    # Defence-in-depth — banned pattern scan BEFORE we let pglast loose.
    for pat in _BANNED_PATTERNS:
        m = pat.search(cleaned)
        if m:
            raise QAError(f"banned token in SQL: {m.group(0)!r}")

    # Parse with pglast.
    try:
        import pglast
        from pglast.ast import (
            RangeVar, RangeFunction, RangeSubselect, FuncCall, SelectStmt,
            CommonTableExpr,
        )
    except ImportError as e:
        raise QAError(
            "pglast is not installed on the server — SQL validation cannot run"
        ) from e

    # pglast uses PostgreSQL-native parameter syntax ($1, $2, ...) but
    # SQLAlchemy + our query templates use named binds (:org_id, :start_date).
    # Translate for the parse only — we'll execute the *original* `cleaned`
    # text via SQLAlchemy so the named binds remain intact.
    parse_text = re.sub(r":([a-zA-Z_][a-zA-Z0-9_]*)", lambda m: "$1", cleaned)

    try:
        parsed = pglast.parse_sql(parse_text)
    except Exception as e:  # pglast.parser.ParseError + others
        raise QAError(f"SQL syntax error: {e}")

    if len(parsed) != 1:
        raise QAError("only a single SELECT statement is allowed")

    stmt = parsed[0].stmt
    if not isinstance(stmt, SelectStmt):
        raise QAError(
            f"only SELECT / WITH-SELECT statements are allowed "
            f"(got {type(stmt).__name__})"
        )

    # pglast AST nodes use __slots__ and are themselves callable (they
    # implement __call__ for serialization), so we walk via a generic
    # helper that filters by class module — `pglast.ast.*` for AST nodes,
    # `pglast.enums.*` for enum values (which we skip; they're terminals).
    def _iter_children(node):
        for name in dir(node):
            if name.startswith("_"):
                continue
            if name in ("ancestors", "all"):
                continue
            try:
                val = getattr(node, name)
            except Exception:
                continue
            if val is None:
                continue
            if isinstance(val, (list, tuple)):
                for item in val:
                    mod = getattr(type(item), "__module__", "")
                    if mod.startswith("pglast.ast"):
                        yield item
                continue
            mod = getattr(type(val), "__module__", "")
            if mod.startswith("pglast.ast"):
                yield val

    # Collect every CTE alias defined inside this query.  CTEs can nest.
    cte_aliases: set[str] = set()

    def _gather_ctes(node) -> None:
        if isinstance(node, CommonTableExpr) and node.ctename:
            cte_aliases.add(node.ctename.lower())
        for child in _iter_children(node):
            _gather_ctes(child)

    _gather_ctes(stmt)

    # Walk the tree gathering every RangeVar (table reference) and every
    # RangeFunction (FROM funcname(...)).  Subselects are fine — they're
    # just nested SELECTs that we'll recursively validate the same way.
    bad_tables: set[str] = set()
    unsafe_funcs: set[str] = set()

    def _check(node) -> None:
        if node is None:
            return
        if isinstance(node, RangeVar):
            tname = (node.relname or "").lower()
            if not tname:
                return
            if tname in _ALLOWED_TABLES or tname in cte_aliases:
                return
            bad_tables.add(tname)
            return
        if isinstance(node, RangeFunction):
            # node.functions is a tuple of (FuncCall, ...) pairs; first
            # element is the function call itself.
            funcs = node.functions or ()
            for entry in funcs:
                # Each entry is itself a tuple (FuncCall, coldef_list).
                if isinstance(entry, (list, tuple)) and entry:
                    fc = entry[0]
                else:
                    fc = entry
                if isinstance(fc, FuncCall):
                    # funcname is a tuple of String AST nodes
                    name_parts = []
                    for n in (fc.funcname or ()):
                        # pglast 6.x: name nodes have `sval`
                        sval = getattr(n, "sval", None) or getattr(n, "val", None)
                        if sval:
                            name_parts.append(str(sval).lower())
                    full = ".".join(name_parts)
                    if full and full not in _SAFE_FROM_FUNCTIONS:
                        # Allow schema-qualified safe funcs too — e.g.
                        # pg_catalog.generate_series is NOT safe but
                        # generate_series IS.
                        last = name_parts[-1] if name_parts else ""
                        if last not in _SAFE_FROM_FUNCTIONS:
                            unsafe_funcs.add(full)
            return
        if isinstance(node, RangeSubselect):
            # Recurse into the inner SELECT.
            if node.subquery is not None:
                _check(node.subquery)
            return

        # Generic recursion — visit every child node.
        for child in _iter_children(node):
            _check(child)

    _check(stmt)

    if unsafe_funcs:
        raise QAError(f"disallowed function in FROM/JOIN: {sorted(unsafe_funcs)}")
    if bad_tables:
        snippet = cleaned[:400].replace("\n", " ").strip()
        raise QAError(
            f"unknown table(s) referenced: {sorted(bad_tables)} — "
            f"in SQL: {snippet}{'...' if len(cleaned) > 400 else ''}"
        )

    # Must include the org_id binding. The server provides :org_id at exec.
    if ":org_id" not in cleaned:
        raise QAError("SQL must include the :org_id parameter — refusing to run")

    return cleaned


def ask(question: str, *, org_id: uuid.UUID, db: Session) -> dict:
    """Run a single Q&A round. Returns a dict suitable for the API response."""
    if not question or not question.strip():
        raise QAError("question is empty")
    if len(question) > 1000:
        raise QAError("question too long (max 1000 characters)")

    if not is_enabled():
        return {
            "question": question,
            "sql": None,
            "row_count": 0,
            "sample": [],
            "answer": (
                "Q&A is disabled because ANTHROPIC_API_KEY isn't set on this "
                "server. Once it's configured, ask me anything about your "
                "bank transactions, invoices, receipts, vendors, or insights."
            ),
        }

    try:
        import anthropic  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise QAError("anthropic package not installed") from e

    client = anthropic.Anthropic()

    # --- Step 1: ask Claude for SQL ---
    try:
        sql_resp = _call_with_retry(
            client,
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": question}],
            system=SCHEMA_PROMPT,
            max_tokens=QA_MAX_TOKENS,
        )
    except QAOverloadedError:
        raise
    except Exception as e:  # noqa: BLE001
        raise QAError(f"LLM call (SQL synthesis) failed: {e}") from e

    proposed_sql = _flatten_text(sql_resp)
    sql = validate_sql(proposed_sql)

    # --- Step 2: execute the SQL with org_id bound ---
    try:
        rows = db.execute(text(sql), {"org_id": str(org_id)}).mappings().all()
    except Exception as e:  # noqa: BLE001
        raise QAError(f"SQL execution failed: {e}") from e

    truncated = rows[:QA_MAX_ROWS]
    sample = [dict(r) for r in truncated]

    # --- Step 3: ask Claude to summarize ---
    try:
        ans_resp = _call_with_retry(
            client,
            model=DEFAULT_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n\n"
                        f"Rows returned ({len(rows)} total, showing up to {QA_MAX_ROWS}):\n"
                        f"{_safe_json(sample)}\n\n"
                        f"Write the plain-English answer now."
                    ),
                }
            ],
            system=ANSWER_PROMPT,
            max_tokens=600,
        )
    except QAOverloadedError as e:
        # SQL ran fine — just couldn't summarize. Return the rows with a
        # friendly note instead of failing the whole request.
        logger.warning("Summary LLM overloaded — returning raw rows: %s", e)
        return {
            "question": question,
            "sql": sql,
            "row_count": len(rows),
            "sample": _jsonable_rows(sample),
            "answer": (
                f"Found {len(rows)} matching row{'s' if len(rows) != 1 else ''}. "
                f"Claude is overloaded so I can't summarize this in plain English "
                f"right now — try again in 30 seconds, or click ‘Show data’ below "
                f"to see the result directly."
            ),
        }
    except Exception as e:  # noqa: BLE001
        raise QAError(f"LLM call (answer) failed: {e}") from e

    answer = _flatten_text(ans_resp).strip()

    return {
        "question": question,
        "sql": sql,
        "row_count": len(rows),
        "sample": _jsonable_rows(sample),
        "answer": answer,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flatten_text(response) -> str:  # type: ignore[no-untyped-def]
    """Extract concatenated text from a Claude Messages-API response."""
    parts: list[str] = []
    for block in response.content:
        text_val = getattr(block, "text", None)
        if text_val:
            parts.append(text_val)
    return "".join(parts).strip()


def _safe_json(obj: Any) -> str:
    """Best-effort JSON serialization for the Claude prompt. Truncates strings
    so a runaway description doesn't blow our token budget."""
    import json
    from decimal import Decimal
    from datetime import date as _date, datetime as _dt

    def _default(v):  # type: ignore[no-untyped-def]
        if isinstance(v, (Decimal, _date, _dt, uuid.UUID)):
            return str(v)
        if isinstance(v, str) and len(v) > 200:
            return v[:200] + "…"
        return str(v)

    try:
        return json.dumps(obj, default=_default, indent=2)[:30000]
    except Exception:  # noqa: BLE001
        return str(obj)[:30000]


def _jsonable_rows(rows: list[dict]) -> list[dict]:
    """Convert Decimal/UUID/date/datetime to strings so the API response
    serializes cleanly."""
    from decimal import Decimal
    from datetime import date as _date, datetime as _dt

    out: list[dict] = []
    for r in rows:
        clean: dict = {}
        for k, v in r.items():
            if isinstance(v, (Decimal, _date, _dt, uuid.UUID)):
                clean[k] = str(v)
            else:
                clean[k] = v
        out.append(clean)
    return out
