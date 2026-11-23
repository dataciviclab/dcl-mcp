"""End-to-end sul server reale via **stdio** (rete richiesta, zero LLM).

Differenza rispetto a `test_smoke.py`: qui si avvia il processo `dcl-mcp`, si
parla il protocollo MCP (initialize → list_tools → call_tool) e si verifica il
contratto che il client vede davvero: registrazione dei tool, schema di input,
serializzazione JSON, campi di provenienza, assenza di `stage`.

Escluso dalla CI (`-m "not smoke"`). Da lanciare dopo un cambio di contratto:

    pytest tests/test_e2e_stdio.py -q -m smoke
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.smoke

EXPECTED_TOOLS = {
    "session_bootstrap",
    "workspace_triage",
    "lab_map",
    "join_suggestions",
    "find",
    "explore",
    "github_list_discussions",
    "github_list_discussion_categories",
    "github_get_discussion",
    "github_create_discussion",
    "github_add_discussion_comment",
    "gcs_query",
}


def _json(result: Any) -> dict[str, Any]:
    """Payload di un call_tool: structuredContent o primo blocco testo."""
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    for block in result.content:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError("risposta senza contenuto")


async def _call(session: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return _json(await session.call_tool(name, args))


async def _check() -> dict[str, Any]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env_file = Path(env.get("DCL_MCP_ENV_FILE", ""))
    if not env_file.is_file():
        fallback = Path(__file__).resolve().parents[2] / ".env"
        if fallback.is_file():
            env["DCL_MCP_ENV_FILE"] = str(fallback)

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "dcl_mcp.server"], env=env
    )
    report: dict[str, Any] = {}

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            report["tools"] = sorted(names)
            assert names == EXPECTED_TOOLS, f"tool mancanti: {EXPECTED_TOOLS - names}"

            # 1. Orientamento: narrazione con data strutturata.
            boot = await _call(session, "session_bootstrap", {})
            assert boot["ok"] is True
            assert "Session Bootstrap" in boot["content"]
            assert boot["as_of"]
            report["as_of"] = boot["as_of"]

            # 2. Enumerazione: la vista bulk esiste e non porta `stage`.
            listing = await _call(session, "lab_map", {"view": "datasets"})
            rows = listing["content"]["datasets"]
            assert listing["ok"] is True and listing["content"]["count"] == len(rows)
            assert len(rows) > 400, f"solo {len(rows)} dataset"
            row = rows[0]
            assert {"slug", "years", "gcs_available", "n_columns"} <= set(row)
            assert "stage" not in row
            report["datasets"] = len(rows)
            report["listing_kb"] = len(json.dumps(listing)) // 1024

            # 3. Ricerca: risultati dataset ricchi, senza `stage`.
            found = await _call(session, "find", {"query": "appalti", "limit": 5})
            assert found["ok"] is True
            items = [r for r in found["content"]["results"] if r["type"] == "dataset"]
            assert items, "nessun dataset per 'appalti'"
            assert "gcs_available" in items[0]
            assert "stage" not in items[0]
            assert found["as_of"]

            # 4. Card: provenienza, accessibilità, join come assaggio.
            card = (await _call(session, "explore", {"ref": items[0]["slug"]}))["content"]
            assert card["found"] is True and card["type"] == "dataset"
            assert "stage" not in card
            assert isinstance(card["gcs_available"], bool)
            if "joins" in card:
                assert card["joins_total"] >= len(card["joins"])

            # 5. Registry compatto: il dettaglio per dataset non viaggia più di
            #    default (era ~186 KB).
            registry = await _call(session, "workspace_triage", {"section": "registry"})
            assert registry["content"]["detail"] is False
            repos = registry["content"]["registry_summary"]
            assert repos and "signals_detail" not in repos[0]
            report["registry_kb"] = len(json.dumps(registry)) // 1024
            assert report["registry_kb"] < 50

            # 6. Dati: numero verificabile, con snapshot dichiarato.
            slug = card["slug"]
            query = await _call(
                session,
                "gcs_query",
                {"slug": slug, "sql": "SELECT count(*) AS n FROM _data"},
            )
            assert query["ok"] is True, query.get("error")
            assert query["n_rows"] == 1 and query["rows"][0][0] > 0
            assert query["gcs_path"] == card["gcs_path"]
            assert query["as_of"] and "snapshot_year" in query
            report["count"] = query["rows"][0][0]

            # 7. Errori onesti: view invalida con le opzioni valide.
            bad_view = await _call(session, "lab_map", {"view": "nope"})
            assert bad_view["ok"] is False
            assert bad_view["valid_views"] == ["tree", "datasets"]

            # 8. Guardrail SQL: DML rifiutato.
            dml = await _call(
                session, "gcs_query", {"slug": slug, "sql": "DELETE FROM _data"}
            )
            assert dml["ok"] is False and "SELECT" in dml["error"]

    return report


async def _check_mart() -> dict[str, Any]:
    """Percorso mart: dataset → mart slug → gcs_query(layer=mart).

    È il ramo che passa dal registry del producer, non dal topic_index, e
    perde role/semantic_type (DESCRIBE): deve dichiararlo.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    if not Path(env.get("DCL_MCP_ENV_FILE", "")).is_file():
        fallback = Path(__file__).resolve().parents[2] / ".env"
        if fallback.is_file():
            env["DCL_MCP_ENV_FILE"] = str(fallback)

    params = StdioServerParameters(
        command=sys.executable, args=["-m", "dcl_mcp.server"], env=env
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            found = await _call(session, "find", {"query": "appalti", "limit": 10})
            candidates = [
                r["slug"]
                for r in found["content"]["results"]
                if r["type"] == "dataset" and r.get("gcs_available")
            ]
            assert candidates
            mart_slug = None
            for slug in candidates:
                card = (await _call(session, "explore", {"ref": slug}))["content"]
                if card.get("marts"):
                    mart_slug = card["marts"][0]
                    break
            assert mart_slug, "nessun dataset con mart tra i candidati"

            mart = (await _call(session, "explore", {"ref": mart_slug}))["content"]
            assert mart["type"] == "mart" and mart["found"] is True
            assert mart["gcs_path"].startswith("gs://dataciviclab-mart/")
            assert mart["n_columns"] > 0
            # Il DESCRIBE non dà role/semantic_type: la card lo deve dire.
            assert "columns_note" in mart

            query = await _call(
                session,
                "gcs_query",
                {
                    "slug": mart_slug,
                    "layer": "mart",
                    "sql": "SELECT count(*) AS n FROM _data",
                },
            )
            assert query["ok"] is True, query.get("error")
            assert query["layer"] == "mart"
            assert query["gcs_path"] == mart["gcs_path"]
            return {"mart": mart_slug, "count": query["rows"][0][0]}


def test_mcp_stdio_end_to_end():
    """Golden path completo passando dal protocollo MCP, non dalle funzioni."""
    report = asyncio.run(asyncio.wait_for(_check(), timeout=180))
    print(json.dumps(report, ensure_ascii=False))


def test_mcp_stdio_mart_path():
    """Ramo mart (registry del producer + layer=mart) via protocollo."""
    report = asyncio.run(asyncio.wait_for(_check_mart(), timeout=180))
    print(json.dumps(report, ensure_ascii=False))
