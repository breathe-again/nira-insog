"""BaseConnector — the abstract interface every source adapter implements.

Lifecycle of a connector call::

    1.  Caller (Celery task / API route / sync upload) constructs a
        ConnectorContext with the SourceSystem row + a DB session.
    2.  Caller picks the right subclass via `get_connector(system_type)`,
        instantiates it with the context.
    3.  Caller invokes one of:
            - `health_check()`     — quick reachability test
            - `poll(since=...)`    — incremental pull from source
            - `backfill(start,end)`— full re-pull for a window
            - `ingest_payload(...)`— accept pushed data
            - `ingest_file(...)`   — accept uploaded file
    4.  Connector returns a SyncResult; the wrapper writes last_sync_at,
        last_sync_status, cursor_json back to the row.

Connector implementations live in `services.connectors.<source>`. Keep
each module focused; share via helpers in services/canonical, not by
inheriting from sibling connectors.
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, ClassVar, Optional

from sqlalchemy.orm import Session

from common.models import SourceSystem
from services import encryption

logger = logging.getLogger(__name__)


class UnknownConnectorError(Exception):
    """Raised when get_connector() can't find a registered system_type."""


class ConnectorNotApplicable(Exception):
    """Raised by a connector method that isn't supported (e.g. calling
    `poll()` on an upload-only connector). The orchestrator treats this
    as a no-op + skip, not a failure.
    """


@dataclass
class HealthResult:
    """Result of a health check. `ok=False` doesn't fail the call —
    callers decide what to do."""

    ok: bool
    latency_ms: Optional[int] = None
    detail: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SyncResult:
    """Result of a poll / backfill / push ingest.

    Numbers are the canonical-side counts (transactions written, ledger
    entries written, accounts auto-created). Errors is a list of
    user-facing messages; an empty list means full success.
    """

    transactions_written: int = 0
    ledger_entries_written: int = 0
    accounts_upserted: int = 0
    documents_created: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    cursor: Optional[dict] = None  # connector-defined; persisted to source_systems.cursor_json
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def status(self) -> str:
        if not self.errors:
            return "ok"
        # If we wrote anything at all, we're partial; otherwise full error
        if (
            self.transactions_written
            or self.ledger_entries_written
            or self.accounts_upserted
        ):
            return "partial"
        return "error"


@dataclass
class ConnectorContext:
    """Everything a connector needs to do its job. Constructed by the
    orchestrator (run_connector helper below); connectors don't build
    these themselves.
    """

    db: Session
    org_id: uuid.UUID
    entity_id: uuid.UUID
    source_system: SourceSystem

    @property
    def system_type(self) -> str:
        return self.source_system.system_type

    def get_config(self, key: str, default: Any = None) -> Any:
        """Pull a value from source_systems.config_json."""
        cfg = self.source_system.config_json or {}
        return cfg.get(key, default)

    def get_secret(self, key: str, default: Any = None) -> Any:
        """Decrypt and pull a value from source_systems.auth_secrets_enc.
        Returns `default` if no secrets blob is set.

        Secrets blob is Fernet-encrypted JSON; we decrypt lazily so a
        connector that doesn't need secrets pays nothing.
        """
        blob = self.source_system.auth_secrets_enc
        if not blob:
            return default
        try:
            import json
            plain = encryption.decrypt_bytes(
                blob.encode("ascii"), {"scheme": "fernet-v1"}
            )
            obj = json.loads(plain.decode("utf-8"))
            return obj.get(key, default)
        except Exception:
            logger.exception(
                "Failed to decrypt secrets for source %s", self.source_system.id
            )
            return default

    def get_cursor(self, key: str, default: Any = None) -> Any:
        cur = self.source_system.cursor_json or {}
        return cur.get(key, default)

    def set_cursor(self, key: str, value: Any) -> None:
        cur = dict(self.source_system.cursor_json or {})
        cur[key] = value
        self.source_system.cursor_json = cur


class BaseConnector(ABC):
    """Abstract base for all source connectors.

    Subclasses should set the class-level metadata (`system_type`,
    `display_name`, `category`) and implement whichever of the four
    ingestion methods make sense for the source. Methods not overridden
    raise ConnectorNotApplicable, which the orchestrator treats as a
    no-op.
    """

    # ---- Class metadata --------------------------------------------------
    system_type: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    # 'ledger' (Tally/Zoho/QB), 'bank' (CSV/AA), 'tax' (GSTN/TRACES),
    # 'document' (upload/OCR), 'other'
    category: ClassVar[str] = "other"
    # Does this connector poll on a timer? Drives the worker scheduler.
    supports_polling: ClassVar[bool] = False
    supports_push: ClassVar[bool] = False
    supports_file_upload: ClassVar[bool] = False

    def __init__(self, ctx: ConnectorContext) -> None:
        self.ctx = ctx
        # Convenience attrs to reduce ctx.* chatter in subclasses
        self.db = ctx.db
        self.org_id = ctx.org_id
        self.entity_id = ctx.entity_id
        self.source_system = ctx.source_system

    # ---- Connector lifecycle -- override these in subclasses -------------

    def health_check(self) -> HealthResult:
        """Quick connectivity / auth test. Default: green if the
        connector loaded — subclasses that talk to external APIs should
        override.
        """
        return HealthResult(ok=True, detail="No health check implemented")

    def poll(self, since: Optional[datetime] = None) -> SyncResult:
        """Pull new data since the cursor. Subclasses for poll-style
        sources (Tally HTTP, Zoho, AA, GSTN) override.
        """
        raise ConnectorNotApplicable(
            f"{type(self).__name__} does not support polling"
        )

    def backfill(
        self, start: date, end: Optional[date] = None
    ) -> SyncResult:
        """Full re-pull for a date window. Default falls through to
        poll(since=None) for connectors that don't differentiate.
        """
        return self.poll(since=None)

    def ingest_payload(self, payload: dict) -> SyncResult:
        """Accept a pushed payload (webhook, push-agent). Subclass
        responsibility to validate shape.
        """
        raise ConnectorNotApplicable(
            f"{type(self).__name__} does not accept pushed payloads"
        )

    def ingest_file(
        self,
        file_path: str,
        mime_type: Optional[str] = None,
        original_filename: Optional[str] = None,
        document_id: Optional[uuid.UUID] = None,
    ) -> SyncResult:
        """Accept an uploaded file. Subclass responsibility to parse.

        `document_id` is the Document row that already exists for this
        upload (created by the upload route); connectors should attach
        canonical entries to it via `source_document_id`.
        """
        raise ConnectorNotApplicable(
            f"{type(self).__name__} does not accept file uploads"
        )


# ---------------------------------------------------------------------------
# Orchestrator — runs a connector against a SourceSystem row and writes
# back the cursor + status. The connector implementation does the actual
# work; this helper handles bookkeeping.
# ---------------------------------------------------------------------------


def run_connector_method(
    db: Session,
    source_system_id: uuid.UUID,
    method: str,
    *,
    org_id: Optional[uuid.UUID] = None,
    **method_kwargs: Any,
) -> SyncResult:
    """Invoke `method` on the connector registered for the given
    source_system. Writes back cursor + last_sync_status atomically.

    `method` must be one of: 'poll', 'backfill', 'ingest_payload',
    'ingest_file'. health_check goes through a different helper since it
    doesn't update sync state.

    The org_id parameter is a defensive cross-check — if it's provided
    and doesn't match the source_system's org_id, the call is rejected.
    Prevents accidental cross-tenant writes from API routes that fetched
    the wrong source_system.
    """
    from services.canonical import entities as entities_svc
    from services.connectors.registry import get_connector

    src = db.get(SourceSystem, source_system_id)
    if src is None:
        raise ValueError(f"source_system {source_system_id} not found")

    if org_id is not None and src.org_id != org_id:
        raise ValueError(
            f"source_system {source_system_id} belongs to a different org"
        )

    entity_id = src.entity_id
    if entity_id is None:
        # Fall back to the org's default entity
        entity_id = entities_svc.get_default_entity(db, src.org_id).id

    ctx = ConnectorContext(
        db=db, org_id=src.org_id, entity_id=entity_id, source_system=src
    )
    connector_cls = get_connector(src.system_type)
    connector = connector_cls(ctx)

    fn = getattr(connector, method, None)
    if not callable(fn):
        raise ValueError(f"unknown connector method: {method}")

    try:
        result: SyncResult = fn(**method_kwargs)
    except ConnectorNotApplicable as exc:
        logger.info("Connector %s %s: not applicable (%s)", src.system_type, method, exc)
        # Don't update sync state for not-applicable calls
        return SyncResult(skipped=1, errors=[], detail={"not_applicable": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Connector %s %s failed", src.system_type, method)
        src.last_sync_at = datetime.now(timezone.utc)
        src.last_sync_status = "error"
        src.last_sync_error = str(exc)[:500]
        db.commit()
        return SyncResult(errors=[str(exc)], detail={"exception_type": type(exc).__name__})

    # Persist result
    src.last_sync_at = datetime.now(timezone.utc)
    src.last_sync_status = result.status
    src.last_sync_error = "; ".join(result.errors)[:500] if result.errors else None
    if result.cursor is not None:
        # Connector may have already mutated source_system.cursor_json via
        # set_cursor; merge any extras returned in result.cursor.
        merged = dict(src.cursor_json or {})
        merged.update(result.cursor)
        src.cursor_json = merged
    db.commit()
    return result


def run_health_check(
    db: Session, source_system_id: uuid.UUID
) -> HealthResult:
    """Invoke health_check on a connector. Does NOT update sync state."""
    from services.canonical import entities as entities_svc
    from services.connectors.registry import get_connector

    src = db.get(SourceSystem, source_system_id)
    if src is None:
        raise ValueError(f"source_system {source_system_id} not found")

    entity_id = src.entity_id or entities_svc.get_default_entity(db, src.org_id).id
    ctx = ConnectorContext(
        db=db, org_id=src.org_id, entity_id=entity_id, source_system=src
    )
    connector_cls = get_connector(src.system_type)
    return connector_cls(ctx).health_check()
