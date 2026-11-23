"""Tool MCP GCS — SELECT read-only sui parquet pubblici del Lab.

Differenza rispetto a ``toolkit_query`` (MCP del repo toolkit): qui non
c'è workspace locale — solo i dataset/mart pubblicati sui bucket
``dataciviclab-clean`` e ``dataciviclab-mart``, risolti dal catalogo
remoto (topic_index + registry). Orientato al contributor che partecipa
senza clonare il multi-repo.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from dcl_mcp import telemetry
from dcl_mcp.app import mcp
from dcl_mcp.context.fetch import load_topic_index, load_triage
from dcl_mcp.gcs.resolver import GcsResolveError, resolve, validate_sql
from dcl_mcp.guards import guard_ok
from dcl_mcp.provenance import catalog_meta, signal_index, snapshot_year

_MAX_LIMIT = 500
_DEFAULT_LIMIT = 100

# Connessione DuckDB riusata tra le chiamate. La prima query su un glob
# multi-file paga la listing del bucket (~2 s); le successive nella stessa
# connessione costano ~250 ms. Con una connessione per chiamata quel lavoro
# andava perso ogni volta: un agente che interroga lo stesso dataset 27 volte
# ripagava 27 listing.
_CONN_LOCK = threading.Lock()
_CONN: Any = None


def _persistent_conn() -> Any:
    """Connessione DuckDB condivisa, creata alla prima query.

    Riusa la config GCS di lab-connectors (`GCS_S3_CONFIG`); il bootstrap
    (INSTALL/LOAD httpfs) è qui perché `gcs_connect` è un context manager
    pensato per una query sola. Quando lab-connectors esporrà una factory,
    questa funzione diventa un wrapper.
    """
    global _CONN
    if _CONN is None:
        import duckdb
        from lab_connectors.duckdb.core import GCS_S3_CONFIG

        con = duckdb.connect(
            ":memory:",
            config={"memory_limit": os.environ.get("DUCKDB_MEMORY_LIMIT", "2GB")},
        )
        con.execute("PRAGMA disable_progress_bar")
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
        for key, value in GCS_S3_CONFIG.items():
            con.execute(f"SET {key} = '{value}'")
        _CONN = con
    return _CONN


def reset_connection() -> None:
    """Chiude e dimentica la connessione (usato dai test e dopo errori I/O)."""
    global _CONN
    if _CONN is not None:
        try:
            _CONN.close()
        except Exception:
            pass
        _CONN = None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _run_query(read_paths: list[str], sql: str, limit: int) -> tuple[list[str], list[list[Any]]]:
    """Esegue la SELECT dell'utente su una view `_data` del parquet. Restituisce (columns, rows).

    La query arriva già validata da ``validate_sql`` (unica sorgente `_data`):
    qui resta solo il binding su DuckDB.
    """
    path_list = ", ".join(f"'{p}'" for p in read_paths)
    create_view = (
        f"CREATE OR REPLACE TEMP VIEW _data AS SELECT * FROM read_parquet([{path_list}])"
    )
    final = f"SELECT * FROM ({sql}) AS _inner LIMIT {limit}"

    with _CONN_LOCK:
        con = _persistent_conn()
        try:
            con.execute(create_view)
            cur = con.execute(final)
            columns = [d[0] for d in cur.description or []]
            rows = cur.fetchall()
        except Exception as exc:
            # un errore di rete può lasciare la connessione inutilizzabile:
            # la prossima chiamata ne crea una nuova (i binder error no).
            if "IO Error" in str(exc) or "Connection" in str(exc) or "HTTP" in str(exc):
                reset_connection()
            raise
    return columns, [[_json_safe(v) for v in row] for row in rows]


@mcp.tool(
    description=(
        "SELECT read-only su un dataset clean o mart pubblicato sui bucket "
        "publici del Lab (dataciviclab-clean / dataciviclab-mart). "
        "slug= del dataset (layer=clean, default) o mart dataset__table "
        "(layer=mart). La query deve referenziare la tabella `_data` "
        "(es. SELECT anno, count(*) FROM _data GROUP BY anno); sono ammesse "
        "anche CTE (WITH … SELECT). "
        "Una sola istruzione, limit= max righe in output (default 100, max 500). "
        "La risposta riporta snapshot_year (anno del file letto), as_of "
        "(quando è stato costruito il catalogo) e l'aggiornamento della "
        "pipeline (last_run, run_year): servono a datare un numero. "
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
            # Leggi una riga in più del limite richiesto: serve a distinguere
            # "esattamente `limit` righe" (non troncato) da "ce n'erano altre".
            try:
                columns, rows = _run_query(resolved["read_paths"], query, safe_limit + 1)
            except GcsResolveError:
                raise
            except Exception as exc:
                # Errore di binding/execution DuckDB: dillo come errore della
                # query, non come guasto del tool.
                raise GcsResolveError(
                    f"Query fallita: {exc}. Controlla le colonne con "
                    "SELECT * FROM _data LIMIT 1."
                ) from exc
            truncated = len(rows) > safe_limit
            rows = rows[:safe_limit]
            payload: dict[str, Any] = {
                "slug": resolved["slug"],
                "layer": resolved["layer"],
                # Stesso nome della card `explore` (era `gs_path` qui e
                # `gcs_path` là: due nomi per lo stesso dato).
                "gcs_path": resolved["gs_path"],
                # Provenienza: catalogo (as_of) e snapshot del file letto.
                "snapshot_year": snapshot_year(resolved["gs_path"]),
                **catalog_meta(topic),
                "columns": columns,
                "n_rows": len(rows),
                "limit": safe_limit,
                "truncated": truncated,
                "rows": rows,
                "ok": True,
            }
            if requested > _MAX_LIMIT:
                payload["limit_clamped"] = f"{requested} → {_MAX_LIMIT} (max consentito)"
            # Aggiornamento della pipeline per il dataset dietro lo slug.
            signal = signal_index(load_triage()).get(resolved.get("dataset") or resolved["slug"])
            if signal:
                payload.update(signal)
            telemetry.record(
                "gcs_query",
                {"slug": resolved["slug"], "layer": resolved["layer"], "limit": safe_limit},
                {"n_rows": len(rows), "truncated": truncated, "ok": True},
            )
            return payload
        except GcsResolveError as exc:
            telemetry.record(
                "gcs_query", {"slug": slug, "layer": layer}, {"ok": False, "error": str(exc)[:200]}
            )
            return {"ok": False, "error": str(exc)}

    return guard_ok(_exec, "gcs_query")
