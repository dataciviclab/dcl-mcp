"""Search log: riga JSONL quando `DCL_MCP_SEARCH_LOG` è impostata.

Protegge il contratto che serve per diagnosticare la ricerca: cosa è stato
chiesto, quante volte è tornato zero, cosa è finito in cima. Spento di default:
nessun file, nessun effetto sui tool.
"""

from __future__ import annotations

import json

import pytest
from lab_connectors.http import HttpResult
from lab_connectors.testing import FakeHttpClient, fake_response

import dcl_mcp.app as app
import dcl_mcp.context.fetch as ctx_fetch
import dcl_mcp.context.tools as context_tools
from dcl_mcp import telemetry


@pytest.mark.pure_unit
def test_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv(telemetry.PATH_ENV, raising=False)
    assert telemetry.enabled() is False
    telemetry.record("find", {"query": "x"}, {"total": 0})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.contract
def test_record_writes_one_line(monkeypatch, tmp_path):
    target = tmp_path / "calls.jsonl"
    monkeypatch.setenv(telemetry.PATH_ENV, str(target))
    assert telemetry.enabled() is True

    telemetry.record(
        "find",
        {"query": "bandi di gara", "limit": 10},
        {"total": 3, "returned": 3, "top": ["anac_bandi_gara"]},
        elapsed_ms=12,
    )
    line = json.loads(target.read_text(encoding="utf-8").strip())
    assert line["tool"] == "find"
    assert line["query"] == "bandi di gara"
    assert line["total"] == 3
    assert line["duration_ms"] == 12
    assert line["pid"] > 0
    # i None non vengono scritti: righe compatte
    assert "domain" not in line


@pytest.mark.policy
def test_record_never_raises_on_unwritable_path(monkeypatch, tmp_path):
    blocker = tmp_path / "file.txt"
    blocker.write_text("x", encoding="utf-8")
    # il parent è un file: mkdir/append falliscono, record non deve sollevare
    monkeypatch.setenv(telemetry.PATH_ENV, str(blocker / "x.jsonl"))
    telemetry.record("find", {"query": "x"}, {"total": 0})


@pytest.mark.contract
def test_find_emits_search_log(monkeypatch, tmp_path, sample_index):
    """Aggancio reale: una `find` scrive una riga con query ed esito."""
    target = tmp_path / "search.jsonl"
    monkeypatch.setenv(telemetry.PATH_ENV, str(target))

    fake = FakeHttpClient()
    fake.responses[f"{ctx_fetch.RAW_BASE}/topic_index.json"] = HttpResult(
        response=fake_response(200, text=json.dumps(sample_index)), err=None
    )
    app._http = fake
    ctx_fetch.artifact_cache.clear()
    try:
        context_tools.find(query="appalti", limit=2)
    finally:
        app._http = None
        ctx_fetch.artifact_cache.clear()

    line = json.loads(target.read_text(encoding="utf-8").strip())
    assert line["tool"] == "find"
    assert line["query"] == "appalti"
    assert line["returned"] >= 1
    assert line["top"]
