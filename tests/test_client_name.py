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


@pytest.mark.parametrize("bad", ["", "   ", "con spazi", "quote'inside", "a;drop", "x" * 200])
def test_env_non_conforme_cade_sul_default(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """Un env malevolo non deve finire nell'header né rompere la connessione."""
    from dcl_mcp.gcs.tools import _client_name

    monkeypatch.setenv("DCL_CLIENT_NAME", bad)

    assert _client_name() == "dcl-unknown/0"
