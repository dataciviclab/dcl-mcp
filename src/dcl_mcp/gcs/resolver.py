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
    try:
        from lab_connectors.duckdb import gcs_connect

        path_list = ", ".join(f"'{p}'" for p in read_paths)
        with gcs_connect(read_paths[0]) as con:
            cur = con.execute(f"DESCRIBE SELECT * FROM read_parquet([{path_list}])")
            rows = cur.fetchall() or []
        columns = [{"name": r[0], "type": r[1]} for r in rows if r and r[0]]
        return columns or None
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

# Parole chiave che non appartengono a una query read-only. Word-boundary per
# non falsare positivi su colonne che contengono la sottostringa.
_FORBIDDEN_SQL = re.compile(
    r"\b(COPY|ATTACH|DETACH|INSTALL|LOAD|PRAGMA|CREATE|INSERT|UPDATE|DELETE|DROP|"
    r"EXPORT|IMPORT|CALL|SET|RESET|CHECKPOINT|FORCE)\b",
    re.IGNORECASE,
)

_SQL_RULES = (
    "Regole: una sola istruzione (no ';'), SELECT o WITH…SELECT su `_data`, "
    "niente COPY/INSERT/UPDATE/DELETE/PRAGMA e simili."
)


def _strip_quoted(text: str) -> str:
    """Rimuove stringhe e identificatori quotati prima del check keyword.

    Le colonne legittime non devono triggerare la guardia: il mart
    eurostat ha una colonna `export`, quotata in DuckDB come "export"
    (audit giro 4: falso positivo che rendeva la colonna inqueryabile).
    """
    text = re.sub(r"'([^']|'')*'", "''", text)
    text = re.sub(r'"[^"]*"', '""', text)
    text = re.sub(r"`[^`]*`", "``", text)
    return text


def validate_sql(sql: str) -> str:
    """Valida una singola istruzione read-only su `_data` (SELECT o CTE).

    Restituisce la sql normalizzata. Le CTE (`WITH … SELECT`) sono ammesse.
    Il check keyword gira su testo con stringhe/identificatori quotati
    rimossi: `"export"` è una colonna, `COPY` a statement-level resta
    bloccato.
    """
    text = (sql or "").strip()
    if not text:
        raise GcsResolveError("SQL vuota. " + _SQL_RULES)
    if ";" in text.rstrip(";"):
        raise GcsResolveError("Ammessa una sola istruzione SQL (niente ';'). " + _SQL_RULES)
    text = text.rstrip(";").strip()
    if not re.match(r"(?is)^\s*(SELECT|WITH)\b", text):
        raise GcsResolveError("Ammesse solo query SELECT (o WITH…SELECT). " + _SQL_RULES)
    if "_data" not in text:
        raise GcsResolveError(
            "La query deve referenziare la tabella `_data` "
            "(es. SELECT count(*) FROM _data). " + _SQL_RULES
        )
    match = _FORBIDDEN_SQL.search(_strip_quoted(text))
    if match:
        raise GcsResolveError(
            f"Parola chiave non permessa in modalità read-only: {match.group(1)}. "
            + _SQL_RULES
        )
    return text
