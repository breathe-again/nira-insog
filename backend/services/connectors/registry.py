"""Connector registry.

Connectors register themselves at import time. The orchestrator looks
up the right class by `system_type` when a SourceSystem row is processed.

Usage::

    from services.connectors import register, BaseConnector

    @register
    class TallyTrialBalanceConnector(BaseConnector):
        system_type = "tally_trial_balance"
        display_name = "Tally — Trial Balance (XLSX upload)"
        category = "ledger"
        supports_file_upload = True
        ...

Importing `services.connectors.tally` (etc.) triggers the @register
decorator. The bootstrap helper `import_all()` imports every known
connector module so callers don't have to remember to import them
individually.
"""
from __future__ import annotations

import importlib
import logging
from typing import Type

from services.connectors.base import BaseConnector, UnknownConnectorError

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Type[BaseConnector]] = {}


def register(cls: Type[BaseConnector]) -> Type[BaseConnector]:
    """Class decorator that registers a connector by its `system_type`."""
    if not cls.system_type:
        raise ValueError(
            f"Connector {cls.__name__} must set a non-empty `system_type`"
        )
    if cls.system_type in _REGISTRY and _REGISTRY[cls.system_type] is not cls:
        existing = _REGISTRY[cls.system_type]
        # Allow re-registration during dev hot-reload but log it loudly
        logger.warning(
            "Re-registering connector %s: %s replaces %s",
            cls.system_type,
            cls.__name__,
            existing.__name__,
        )
    _REGISTRY[cls.system_type] = cls
    return cls


def register_class(system_type: str, cls: Type[BaseConnector]) -> None:
    """Imperative form for tests / dynamic registration."""
    cls.system_type = system_type
    register(cls)


def get_connector(system_type: str) -> Type[BaseConnector]:
    """Look up a connector class. Raises UnknownConnectorError if missing.

    Triggers a lazy import of `services.connectors.<system_type>` on
    miss, so connectors that haven't been imported yet still resolve.
    """
    if system_type not in _REGISTRY:
        # Try lazy import. Module name = system_type with non-letter
        # characters stripped.
        candidate = system_type.replace("-", "_").replace(".", "_")
        try:
            importlib.import_module(f"services.connectors.{candidate}")
        except ImportError:
            pass
    if system_type not in _REGISTRY:
        raise UnknownConnectorError(
            f"No connector registered for system_type={system_type!r}. "
            f"Known: {sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[system_type]


def list_connectors() -> list[Type[BaseConnector]]:
    return list(_REGISTRY.values())


# ---------------------------------------------------------------------------
# Bootstrap — eagerly import every known connector so registrations happen
# at startup. New connectors should be added to this list.
# ---------------------------------------------------------------------------

_BUILTIN_MODULES = [
    "services.connectors.tally_trial_balance",
    "services.connectors.tally_day_book",
    "services.connectors.bank_csv",
]


def import_all() -> None:
    """Import every built-in connector module so they register
    themselves. Safe to call multiple times.
    """
    for mod in _BUILTIN_MODULES:
        try:
            importlib.import_module(mod)
        except ImportError as exc:
            # During incremental development not all modules exist yet —
            # don't crash; log and continue.
            logger.debug("Connector module %s not yet importable: %s", mod, exc)
