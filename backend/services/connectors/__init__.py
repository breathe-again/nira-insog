"""Source connector framework.

A "connector" is the pluggable adapter between an external source system
(Tally, Zoho, Setu AA, GSTN, bank CSV, ...) and Nira's canonical ledger.
The contract is intentionally small so adding a new source is "implement
the interface", not "rewrite the dashboard".

Three call paths every connector supports:

  1. **Polling** — Nira initiates: `poll(since=cursor)` pulls everything
     new since the last cursor. Used by Tally HTTP, Zoho OAuth, AA, GSTN.

  2. **Webhook / push** — source initiates: `ingest_payload(payload)`
     accepts a chunk from outside (the Tally-on-AWS push agent, a
     webhook from Setu, an inbound email). Used when we can't poll.

  3. **Manual upload** — user initiates: `ingest_file(path, mime)`
     handles file drops (bank CSV, Trial Balance XLSX, GSTR-2B JSON).
     The existing bank-CSV pipeline plugs into this.

Each connector reads its per-tenant config + secrets from a
`source_systems` row and writes back the sync cursor + status. The
canonical posting helpers in `services.canonical.ledger` are the only
sanctioned write path — no connector touches `ledger_entries` directly.

Registration. Connector classes register themselves at import time:

    from services.connectors import register, BaseConnector

    @register
    class TallyTrialBalanceConnector(BaseConnector):
        system_type = "tally_trial_balance"
        display_name = "Tally — Trial Balance (XLSX upload)"
        ...

`get_connector(system_type)` then returns the class, and
`run_connector(db, source_system_id, ...)` instantiates and invokes it.
"""
from services.connectors.base import (
    BaseConnector,
    ConnectorContext,
    HealthResult,
    SyncResult,
    UnknownConnectorError,
)
from services.connectors.registry import (
    get_connector,
    list_connectors,
    register,
    register_class,
)

__all__ = [
    "BaseConnector",
    "ConnectorContext",
    "HealthResult",
    "SyncResult",
    "UnknownConnectorError",
    "get_connector",
    "list_connectors",
    "register",
    "register_class",
]
