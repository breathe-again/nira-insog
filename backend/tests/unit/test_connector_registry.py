"""Unit tests for the connector registry + base contract.

The framework is pure-Python — these run without a DB.
"""
from __future__ import annotations

import pytest

from services.connectors import (
    BaseConnector,
    HealthResult,
    SyncResult,
    UnknownConnectorError,
    get_connector,
    list_connectors,
    register_class,
)
from services.connectors.base import ConnectorNotApplicable


class _DummyOK(BaseConnector):
    system_type = "_test_dummy_ok"
    display_name = "Dummy OK"
    category = "other"
    supports_polling = True

    def health_check(self) -> HealthResult:
        return HealthResult(ok=True, detail="all clear")

    def poll(self, since=None) -> SyncResult:
        return SyncResult(transactions_written=2, ledger_entries_written=4)


class _DummyUploadOnly(BaseConnector):
    system_type = "_test_dummy_upload"
    display_name = "Dummy Upload Only"
    category = "document"
    supports_file_upload = True

    def ingest_file(self, file_path, mime_type=None, original_filename=None, document_id=None):
        return SyncResult(documents_created=1, ledger_entries_written=10)


@pytest.fixture(autouse=True)
def _register_dummies():
    register_class("_test_dummy_ok", _DummyOK)
    register_class("_test_dummy_upload", _DummyUploadOnly)
    yield


def test_register_and_get():
    cls = get_connector("_test_dummy_ok")
    assert cls is _DummyOK


def test_unknown_connector_raises():
    with pytest.raises(UnknownConnectorError):
        get_connector("___does_not_exist___")


def test_list_includes_registered():
    classes = list_connectors()
    types_ = {c.system_type for c in classes}
    assert "_test_dummy_ok" in types_
    assert "_test_dummy_upload" in types_


def test_default_methods_raise_not_applicable():
    """A connector that doesn't override poll() must raise
    ConnectorNotApplicable — not return an empty SyncResult silently.
    """

    class _NoOverrides(BaseConnector):
        system_type = "_test_no_overrides"
        display_name = "No Overrides"

    # We instantiate manually since ConnectorContext needs a DB.
    inst = _NoOverrides.__new__(_NoOverrides)
    inst.ctx = None  # not used for this test
    inst.db = None
    inst.org_id = None
    inst.entity_id = None
    inst.source_system = None
    with pytest.raises(ConnectorNotApplicable):
        inst.poll()
    with pytest.raises(ConnectorNotApplicable):
        inst.ingest_payload({})
    with pytest.raises(ConnectorNotApplicable):
        inst.ingest_file("/dev/null")


def test_sync_result_status_ok():
    r = SyncResult(transactions_written=1, ledger_entries_written=2)
    assert r.ok is True
    assert r.status == "ok"


def test_sync_result_status_partial():
    r = SyncResult(
        transactions_written=5, ledger_entries_written=10,
        errors=["one row had bad date"],
    )
    assert r.ok is False
    assert r.status == "partial"


def test_sync_result_status_error():
    r = SyncResult(errors=["totally broken"])
    assert r.ok is False
    assert r.status == "error"


def test_register_requires_system_type():
    class _Empty(BaseConnector):
        system_type = ""
        display_name = "Empty"

    with pytest.raises(ValueError):
        register_class("", _Empty)
