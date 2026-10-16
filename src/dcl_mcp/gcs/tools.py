"""Tool MCP GCS — SELECT read-only sui parquet pubblici del Lab.

Differenza rispetto a ``toolkit_query`` (MCP del repo toolkit): qui non
c'è workspace locale — solo i dataset/mart pubblicati sui bucket
``dataciviclab-clean`` e ``dataciviclab-mart``, risolti dal catalogo
remoto (topic_index + registry). Orientato al contributor che partecipa
senza clonare il multi-repo.
"""

from __future__ import annotations

from typing import Any

from lab_connectors.duckdb import gcs_connect
from lab_connectors.mcp import guard_timed

from dcl_mcp.app import mcp
from dcl_mcp.context.fetch import load_topic_index
from dcl_mcp.gcs.resolver import GcsResolveError, resolve, validate_sql

_MAX_LIMIT = 500
_DEFAULT_LIMIT = 100


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _run_query(read_paths: list[str], sql: str, limit: int) -> tuple[list[str], list[list[Any]]]:
    """Esegue la SELECT dell'utente su una view `_data` del parquet. Restituisce (columns, rows)."""
    if "_data" not in sql:
        raise GcsResolveError(
            "La query deve referenziare la tabella `_data` "
            "(es. SELECT * FROM _data LIMIT 10)."
        )
    path_list = ", ".join(f"'{p}'" for p in read_paths)
    create_view = (
        f"CREATE OR REPLACE TEMP VIEW _data AS SELECT * FROM read_parquet([{path_list}])"
    )
    final = f"SELECT * FROM ({sql}) AS _inner LIMIT {limit}"

    with gcs_connect(read_paths[0]) as con:
        con.execute(create_view)
        cur = con.execute(final)
        columns = [d[0] for d in cur.description or []]
        rows = cur.fetchall()
    return columns, [[_json_safe(v) for v in row] for row in rows]


@mcp.tool(
    description=(
        "SELECT read-only su un dataset clean o mart pubblicato sui bucket "
        "publici del Lab (dataciviclab-clean / dataciviclab-mart). "
        "slug= del dataset (layer=clean, default) o mart dataset__table "
        "(layer=mart). La query deve referenziare la tabella `_data` "
        "(es. SELECT anno, count(*) FROM _data GROUP BY anno). "
        "Una sola istruzione SELECT, limit= max righe in output (default 100, max 500). "
        "Scopri gli slug con find()/explore() — explore restituisce anche "
        "columns e gcs_path. Serve solo per i dati pubblicati: per "
        "raw/contratti/pipeline usa il MCP toolkit."
    ),
    structured_output=True,
)
def gcs_query(slug: str, sql: str, layer: str = "clean", limit: int = 100) -> dict[str, Any]:
    """Esegue una SELECT read-only su parquet GCS pubblici."""

    def _exec() -> dict[str, Any]:
        try:
            requested = int(limit) if limit else _DEFAULT_LIMIT
            safe_limit = max(1, min(requested, _MAX_LIMIT))
            # Ordine: lookup slug PRIMA della validazione SQL — altrimenti un
            # errore di convenzione (_data) maschera uno slug inesistente
            # (friction audit giro 2).
            topic = load_topic_index()
            resolved = resolve(topic, slug, layer=layer)
            query = validate_sql(sql)
            columns, rows = _run_query(resolved["read_paths"], query, safe_limit)
            payload: dict[str, Any] = {
                "slug": resolved["slug"],
                "layer": resolved["layer"],
                "gs_path": resolved["gs_path"],
                "columns": columns,
                "n_rows": len(rows),
                "limit": safe_limit,
                "truncated": len(rows) >= safe_limit,
                "rows": rows,
                "ok": True,
            }
            if requested > _MAX_LIMIT:
                payload["limit_clamped"] = f"{requested} → {_MAX_LIMIT} (max consentito)"
            return payload
        except GcsResolveError as exc:
            return {"ok": False, "error": str(exc)}

    return guard_timed(_exec, "gcs_query")
