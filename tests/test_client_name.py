"""L'MCP dichiara la propria identità nei usage log GCS.

Senza `DCL_CLIENT_NAME` le query di `gcs_query` risultano
`duckdb/vX python/3.12`, indistinguibili da build, dashboard o script: è la
ragione per cui il 25 nov 1,7 GB di letture locali non erano attribuibili.
"""

from __future__ import annotations

import importlib
import os

import pytest

pytestmark = pytest.mark.contract


def test_app_imposta_client_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DCL_CLIENT_NAME", raising=False)
    import dcl_mcp.app as app

    try:
        importlib.reload(app)
        value = os.environ.get("DCL_CLIENT_NAME", "")
        assert value.startswith("dcl-mcp/")
    finally:
        # `setdefault` gira all'import: senza cleanup resta nel processo.
        os.environ.pop("DCL_CLIENT_NAME", None)


def test_conn_persistente_usa_client_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """La UA deve finire nella config di `duckdb.connect`, non solo nell'env.

    Regressione storica: impostare il valore e non passarlo alla connessione
    (DuckDB non accetta `SET custom_user_agent` dopo l'apertura).
    """
    import duckdb

    from dcl_mcp.gcs import tools

    catturato: dict[str, object] = {}

    class _FakeCon:
        def execute(self, *a: object, **k: object) -> None:
            return None

        def close(self) -> None:
            return None

    def fake_connect(*a: object, **kw: object) -> _FakeCon:
        catturato.update(kw.get("config") or {})
        return _FakeCon()

    monkeypatch.setenv("DCL_CLIENT_NAME", "dcl-mcp-test/9.9")
    monkeypatch.setattr(duckdb, "connect", fake_connect)
    tools.reset_connection()
    try:
        tools._persistent_conn()
    finally:
        tools.reset_connection()

    assert catturato.get("custom_user_agent") == "dcl-mcp-test/9.9"
