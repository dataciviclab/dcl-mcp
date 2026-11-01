"""Risoluzione slug → parquet leggibili (clean/mart) sui bucket pubblici.

Due sorgenti, entrambe remote-first:

- **clean**: ``location`` già presente nelle voci dataset del
  ``topic_index.json`` (artefatto ACB, cachato dal server).
- **mart**: ``registry/registry.json`` del repo produttore, caricato da
  raw.githubusercontent (``lab_connectors.registry.load_registry_github``).
  Gli slug mart sono ``dataset__table``.

I path ``gs://`` vengono convertiti in ``https://storage.googleapis.com/``
per la lettura DuckDB nativa; i path con glob (``*``) restano ``gs://`` e
usano httpfs (l'HTTP generico non supporta i glob).
"""

from __future__ import annotations

import json
import re
from typing import Any

from lab_connectors.gcs.paths import CLEAN_BUCKET, MART_BUCKET, parse_gs_url, public_url
from lab_connectors.mcp import TtlCache, get_mcp_logger
from lab_connectors.registry import load_registry_github

from dcl_mcp.context.search import flatten_datasets

ALLOWED_BUCKETS = frozenset({CLEAN_BUCKET, MART_BUCKET})

_log = get_mcp_logger("dcl-mcp-gcs", level="INFO")

# Registry remoto: cambiano raramente (push nei repo produttori) — 10 minuti
# bastano tra un refresh e l'altro della sessione.
_registry_cache: TtlCache[str, Any] = TtlCache(ttl_seconds=600)
# Schema (colonne) dei parquet mart: il DESCRIBE è un round-trip GCS, lo
# schema cambia solo a publish — stessa TTL del registry.
_schema_cache: TtlCache[str, Any] = TtlCache(ttl_seconds=600)


class GcsResolveError(ValueError):
    """Slug sconosciuto, registry irraggiungibile o path fuori allowlist."""


def _check_bucket(gs_path: str) -> None:
    bucket, _key = parse_gs_url(gs_path)
    if bucket not in ALLOWED_BUCKETS:
        raise GcsResolveError(
            f"Bucket non permesso: '{bucket}'. Ammessi: {sorted(ALLOWED_BUCKETS)}."
        )


def to_read_paths(gs_path: str) -> list[str]:
    """Converte un location.path GCS in path leggibili da DuckDB."""
    _check_bucket(gs_path)
    if "*" in gs_path:
        # Glob multi-file: unica via stabile è httpfs su gs:// (gcs_connect).
        return [gs_path]
    return [public_url(gs_path)]


def find_dataset_entry(topic_index: dict[str, Any], slug: str) -> dict[str, Any] | None:
    """Cerca la voce dataset completa per slug nel topic_index."""
    for ds in flatten_datasets(topic_index.get("datasets") or {}):
        if ds.get("slug") == slug:
            return ds
    return None


def resolve_clean(topic_index: dict[str, Any], slug: str) -> dict[str, Any]:
    """Risolvi un dataset clean: location dal topic_index (artefatto ACB)."""
    ds = find_dataset_entry(topic_index, slug)
    if ds is None:
        raise GcsResolveError(
            f"Dataset '{slug}' non trovato nel catalogo. Usa find() per scoprire gli slug."
        )
    location = ds.get("location") or {}
    path = location.get("path") or ""
    if not path:
        raise GcsResolveError(
            f"Dataset '{slug}' senza location GCS (stage={ds.get('stage')}, "
            "forse non ancora pubblicato)."
        )
    return {
        "slug": slug,
        "layer": "clean",
        "name": ds.get("name") or slug,
        "stage": ds.get("stage"),
        "registry_source": ds.get("registry_source") or "",
        "gs_path": path,
        "read_paths": to_read_paths(path),
    }


def _load_registry_cached(repo: str) -> Any:
    cached = _registry_cache.get(repo)
    if cached is not None:
        return cached
    reg = load_registry_github(repo)
    _registry_cache.set(repo, reg)
    return reg


def mart_card_detail(repo: str, mart_slug: str) -> dict[str, Any] | None:
    """Schema di un mart dal registry del producer (best-effort, futuro).

    Oggi i produttori NON committano le colonne dei mart nel registry
    (solo slug/dataset/table/location) — questo path è future-proofing.
    La via reale è ``describe_parquet_columns`` sul parquet pubblicato.
    """
    try:
        reg = _load_registry_cached(repo)
    except Exception:
        return None
    match = next((m for m in (reg.marts or []) if m.slug == mart_slug), None)
    if match is None:
        return None
    columns = [
        {"name": c.name, "type": c.type, "role": c.role}
        for c in (match.columns or [])
        if getattr(c, "name", None)
    ]
    detail: dict[str, Any] = {"table": match.table}
    if match.name:
        detail["name"] = match.name
    if columns:
        detail["columns"] = columns
    if match.primary_key:
        detail["primary_key"] = match.primary_key
    return detail


def describe_parquet_columns(read_paths: list[str]) -> list[dict[str, Any]] | None:
    """Schema reale di un parquet via DuckDB DESCRIBE (footer-only).

    Usato per le card mart: le colonne wide (la dimensione diventa nome
    colonna) esistono solo nel parquet pubblicato, non nel topic_index
    né nel registry. Best-effort: None su qualunque errore di rete/DuckDB.
    """
    if not read_paths:
        return None
    cache_key = "|".join(read_paths)
    cached = _schema_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        from lab_connectors.duckdb import gcs_connect

        path_list = ", ".join(f"'{p}'" for p in read_paths)
        with gcs_connect(read_paths[0]) as con:
            cur = con.execute(f"DESCRIBE SELECT * FROM read_parquet([{path_list}])")
            rows = cur.fetchall() or []
        columns = [{"name": r[0], "type": r[1]} for r in rows if r and r[0]]
        if columns:
            _schema_cache.set(cache_key, columns)
            return columns
        return None
    except Exception:
        return None


def resolve_mart(topic_index: dict[str, Any], slug: str) -> dict[str, Any]:
    """Risolvi un mart: slug ``dataset__table`` (o dataset con un solo mart).

    Gli slug mart non esistono nel topic_index: il repo produttore si
    ricava dalla parte ``dataset`` dello slug (o dallo slug dataset intero).
    """
    dataset_part = slug.split("__", 1)[0]
    ds = find_dataset_entry(topic_index, dataset_part)
    meta = (topic_index.get("by_slug") or {}).get(dataset_part) or {}
    repo = (ds or {}).get("registry_source") or meta.get("registry_source") or ""
    if not repo:
        raise GcsResolveError(
            f"Nessun repo produttore associato a '{dataset_part}' — "
            "impossibile caricare il registry."
        )

    try:
        reg = _load_registry_cached(repo)
    except Exception as exc:
        raise GcsResolveError(f"Registry di '{repo}' irraggiungibile: {exc}") from exc

    marts = list(reg.marts or [])

    # Slug mart completo (dataset__table)
    exact = [m for m in marts if m.slug == slug]
    if exact:
        matches = exact
    else:
        # Slug di dataset: tutti i suoi mart
        matches = [m for m in marts if m.dataset == slug]
        if not matches:
            available = sorted(m.slug for m in marts)[:20]
            raise GcsResolveError(
                f"Nessun mart per '{slug}' in '{repo}'. "
                f"Esempi disponibili (dataset__table): {available}"
            )
        if len(matches) > 1:
            raise GcsResolveError(
                f"'{slug}' ha {len(matches)} mart — usa lo slug completo dataset__table: "
                f"{sorted(m.slug for m in matches)}"
            )

    mart = matches[0]
    path = mart.location.path if mart.location else ""
    if not path:
        raise GcsResolveError(f"Mart '{mart.slug}' senza location GCS nel registry di '{repo}'.")
    return {
        "slug": mart.slug,
        "layer": "mart",
        "name": mart.name or mart.slug,
        "dataset": mart.dataset,
        "table": mart.table,
        "registry_source": repo,
        "gs_path": path,
        "read_paths": to_read_paths(path),
    }


def resolve(
    topic_index: dict[str, Any], slug: str, layer: str = "clean"
) -> dict[str, Any]:
    """Dispatch per layer: ``clean`` (topic_index) o ``mart`` (registry)."""
    layer_l = (layer or "clean").strip().lower()
    if layer_l == "clean":
        return resolve_clean(topic_index, slug)
    if layer_l == "mart":
        return resolve_mart(topic_index, slug)
    raise GcsResolveError(f"Layer '{layer}' non valido — usa 'clean' o 'mart'.")


# ── SQL guardrail ──────────────────────────────────────────────────────────────

_SQL_RULES = (
    "Regole: una sola istruzione (no ';'), SELECT o WITH…SELECT su `_data`, "
    "niente COPY/INSERT/UPDATE/DELETE/PRAGMA e simili."
)

_SOURCE_TABLE = "_data"
# Table function che generano righe in memoria: le uniche ammesse in FROM.
# `range`/`generate_series` servono alle spine temporali delle analisi.
_ALLOWED_TABLE_FUNCTIONS = frozenset({"unnest", "range", "generate_series"})
# Famiglie di funzioni che leggono filesystem o database esterni, ovunque
# compaiano nella query (in FROM o come scalari in WHERE).
_DANGEROUS_FUNCTION = re.compile(
    r"(?i)^(read_[a-z0-9_]+|parquet_[a-z0-9_]+|[a-z0-9_]+_(scan|query|attach)"
    r"|glob|sniff_csv|st_read)$"
)
# Messaggio con cui DuckDB rifiuta tutto ciò che non è una SELECT.
_SELECT_ONLY = "Only SELECT statements can be serialized to json!"


def _parse_sql(sql: str) -> dict[str, Any]:
    """AST della query via DuckDB (``json_serialize_sql``), zero regex.

    Il serializzatore rifiuta da sé ogni istruzione che non sia una SELECT:
    è il controllo più solido disponibile senza un parser nostro.
    """
    import duckdb

    with duckdb.connect() as con:
        row = con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()
    if row is None:
        raise GcsResolveError("SQL non valida: il parser non ha risposto. " + _SQL_RULES)
    return json.loads(row[0])


def _scan_ast(node: Any, out: dict[str, Any]) -> None:
    """Raccoglie sorgenti, funzioni e CTE dall'AST (walk generico)."""
    if isinstance(node, dict):
        node_type = node.get("type")
        if node_type == "BASE_TABLE":
            out["tables"].append(str(node.get("table_name") or ""))
        if node_type == "TABLE_FUNCTION":
            function = node.get("function") or {}
            out["table_functions"].append(str(function.get("function_name") or ""))
        elif node.get("class") == "FUNCTION":
            out["functions"].append(str(node.get("function_name") or ""))
        cte_map = node.get("cte_map")
        if isinstance(cte_map, dict):
            for entry in cte_map.get("map") or []:
                if isinstance(entry, dict) and entry.get("key"):
                    out["ctes"].add(str(entry["key"]).lower())
        for value in node.values():
            _scan_ast(value, out)
    elif isinstance(node, list):
        for value in node:
            _scan_ast(value, out)


def validate_sql(sql: str) -> str:
    """Valida una singola SELECT read-only su `_data`, sull'AST di DuckDB.

    Regole, tutte strutturali (nessuna euristica su stringhe):
      - una sola istruzione, e deve essere una SELECT (o WITH…SELECT);
      - ogni sorgente di righe (`BASE_TABLE`) è `_data` o una CTE della query;
      - le table function in FROM sono solo quelle in memoria
        (`unnest`/`range`/`generate_series`);
      - nessuna funzione di lettura (`read_*`, `*_scan`, `glob`, …) ovunque.

    Prima il check era una substring su `_data` e poi un regex sulle
    sorgenti: `SELECT * FROM '/tmp/seg.csv'` è sintassi DuckDB valida e
    passava (la tabella era una stringa, invisibile al regex).

    Resta un guardrail, non un sandbox: il limite è dichiarato nel README.
    """
    text = (sql or "").strip()
    # Il ';' finale è tollerato, ma va tolto: la query viene incapsulata in
    # una subquery (`SELECT * FROM (…) AS _inner`).
    text = text.rstrip(";").strip()
    if not text:
        raise GcsResolveError("SQL vuota. " + _SQL_RULES)

    parsed = _parse_sql(text)
    if parsed.get("error"):
        message = str(parsed.get("error_message") or "errore di parsing")
        if message == _SELECT_ONLY:
            raise GcsResolveError("Ammesse solo query SELECT (o WITH…SELECT). " + _SQL_RULES)
        raise GcsResolveError(f"SQL non valida: {message}. " + _SQL_RULES)

    statements = parsed.get("statements") or []
    if len(statements) != 1:
        raise GcsResolveError("Ammessa una sola istruzione SQL (niente ';'). " + _SQL_RULES)
    statement = (statements[0] or {}).get("node") or {}
    if statement.get("type") != "SELECT_NODE":
        raise GcsResolveError(
            f"Ammesse solo query SELECT (o WITH…SELECT), non {statement.get('type')}. "
            + _SQL_RULES
        )

    scan: dict[str, Any] = {"tables": [], "table_functions": [], "functions": [], "ctes": set()}
    _scan_ast(parsed, scan)

    for name in scan["table_functions"]:
        if name.lower() not in _ALLOWED_TABLE_FUNCTIONS:
            raise GcsResolveError(
                f"Funzione non permessa in modalità read-only: {name} "
                "(l'unica sorgente è `_data`). " + _SQL_RULES
            )
    for name in scan["functions"]:
        if _DANGEROUS_FUNCTION.match(name):
            raise GcsResolveError(
                f"Funzione non permessa in modalità read-only: {name} "
                "(legge file o database esterni). " + _SQL_RULES
            )

    allowed = scan["ctes"] | {_SOURCE_TABLE}
    unknown = [table for table in scan["tables"] if table.lower() not in allowed]
    if unknown:
        raise GcsResolveError(
            f"Sorgente non permessa: '{unknown[0]}'. L'unica sorgente è `_data` "
            "(o una CTE che la legge). " + _SQL_RULES
        )
    if not any(table.lower() == _SOURCE_TABLE for table in scan["tables"]):
        raise GcsResolveError(
            "La query deve leggere `_data` (es. SELECT count(*) FROM _data). " + _SQL_RULES
        )
    return text
