"""Resolver GCS (clean/mart) e tool gcs_query."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dcl_mcp.gcs import resolver
from dcl_mcp.gcs.resolver import (
    GcsResolveError,
    resolve,
    resolve_clean,
    resolve_mart,
    to_read_paths,
    validate_sql,
)

pytestmark = pytest.mark.pure_unit


@pytest.fixture(autouse=True)
def _clear_registry_cache():
    yield
    resolver._registry_cache.clear()


# ── to_read_paths / bucket allowlist ──────────────────────────────────────────


def test_single_file_becomes_https():
    paths = to_read_paths("gs://dataciviclab-clean/rifiuti/x/2024/x_clean.parquet")
    assert paths == ["https://storage.googleapis.com/dataciviclab-clean/rifiuti/x/2024/x_clean.parquet"]


def test_glob_stays_gs_for_httpfs():
    glob_path = "gs://dataciviclab-clean/appalti_pubblici/anac_bandi_gara/*/anac_*_clean.parquet"
    assert to_read_paths(glob_path) == [glob_path]


def test_foreign_bucket_rejected():
    with pytest.raises(GcsResolveError, match="Bucket non permesso"):
        to_read_paths("gs://other-bucket/file.parquet")


# ── resolve_clean ─────────────────────────────────────────────────────────────


def test_resolve_clean_single_file(sample_index):
    resolved = resolve_clean(sample_index, "ispra_ru_base")
    assert resolved["layer"] == "clean"
    assert resolved["read_paths"][0].startswith("https://storage.googleapis.com/")
    assert resolved["gs_path"].startswith("gs://dataciviclab-clean/")


def test_resolve_clean_glob(sample_index):
    resolved = resolve_clean(sample_index, "anac_bandi_gara")
    assert "*" in resolved["read_paths"][0]
    assert resolved["read_paths"][0].startswith("gs://")


def test_resolve_clean_unknown_slug(sample_index):
    with pytest.raises(GcsResolveError, match="non trovato"):
        resolve_clean(sample_index, "nope")


def test_resolve_clean_without_location(sample_index):
    with pytest.raises(GcsResolveError, match="senza location"):
        resolve_clean(sample_index, "bdap_entrate_stato")


# ── resolve_mart ──────────────────────────────────────────────────────────────


def _fake_registry(marts):
    return SimpleNamespace(marts=[SimpleNamespace(**m) for m in marts])


_MARTS = [
    {
        "slug": "anzianita__anzianita_comparti",
        "dataset": "anzianita",
        "table": "anzianita_comparti",
        "name": "",
        "location": SimpleNamespace(
            path="gs://dataciviclab-mart/conto-annuale/anzianita/2024/anzianita_comparti.parquet"
        ),
    },
    {
        "slug": "anzianita__altra_tabella",
        "dataset": "anzianita",
        "table": "altra_tabella",
        "name": "",
        "location": SimpleNamespace(
            path="gs://dataciviclab-mart/conto-annuale/anzianita/2024/altra_tabella.parquet"
        ),
    },
]


@pytest.fixture
def mart_index(sample_index):
    """Indice con un dataset 'anzianita' il cui registry ha 2 mart."""
    entry = {
        "slug": "anzianita",
        "name": "Anzianita",
        "stage": "published",
        "source_id": "openbdap",
        "registry_source": "open-conto-annuale",
        "category": "pa",
    }
    sample_index["datasets"]["openbdap"] = [entry]
    sample_index["by_slug"]["anzianita"] = {
        "name": "Anzianita",
        "stage": "published",
        "source_id": "openbdap",
        "registry_source": "open-conto-annuale",
        "domain": None,
        "category": "pa",
    }
    return sample_index


def test_resolve_mart_exact_slug(monkeypatch, mart_index):
    monkeypatch.setattr(
        resolver, "load_registry_github", lambda repo: _fake_registry(_MARTS)
    )
    resolved = resolve_mart(mart_index, "anzianita__anzianita_comparti")
    assert resolved["layer"] == "mart"
    assert resolved["table"] == "anzianita_comparti"
    assert resolved["read_paths"][0].startswith("https://storage.googleapis.com/dataciviclab-mart/")


def test_resolve_mart_dataset_slug_ambiguous(monkeypatch, mart_index):
    monkeypatch.setattr(
        resolver, "load_registry_github", lambda repo: _fake_registry(_MARTS)
    )
    with pytest.raises(GcsResolveError, match="dataset__table"):
        resolve_mart(mart_index, "anzianita")


def test_resolve_mart_dataset_slug_single(monkeypatch, mart_index):
    only_one = [_MARTS[0]]
    monkeypatch.setattr(resolver, "load_registry_github", lambda repo: _fake_registry(only_one))
    resolved = resolve_mart(mart_index, "anzianita")
    assert resolved["slug"] == "anzianita__anzianita_comparti"


def test_resolve_mart_unknown(monkeypatch, mart_index):
    monkeypatch.setattr(resolver, "load_registry_github", lambda repo: _fake_registry([]))
    # dataset presente nell'indice ma senza mart nel registry
    with pytest.raises(GcsResolveError, match="Nessun mart"):
        resolve_mart(mart_index, "ispra_ru_base")
    # dataset completely unknown → fallisce prima la risoluzione del repo
    with pytest.raises(GcsResolveError, match="repo produttore"):
        resolve_mart(mart_index, "unknown_ds")


def test_resolve_dispatch_and_bad_layer(sample_index):
    assert resolve(sample_index, "ispra_ru_base", layer="clean")["layer"] == "clean"
    with pytest.raises(GcsResolveError, match="Layer"):
        resolve(sample_index, "x", layer="raw")


# ── validate_sql ──────────────────────────────────────────────────────────────


def test_validate_sql_accepts_select_and_cte():
    assert validate_sql("SELECT * FROM _data") == "SELECT * FROM _data"
    assert validate_sql("select count(*) from _data;") == "select count(*) from _data"
    # CTE ammesse (audit giro 3: le query di analysis reali le usano)
    cte = "WITH t AS (SELECT anno FROM _data) SELECT anno, count(*) FROM t GROUP BY 1"
    assert validate_sql(cte) == cte


def test_validate_sql_rejects_non_select_and_multi():
    with pytest.raises(GcsResolveError, match="SELECT"):
        validate_sql("UPDATE _data SET x = 1")
    with pytest.raises(GcsResolveError, match="sola"):
        validate_sql("SELECT * FROM _data; DROP TABLE _data")
    with pytest.raises(GcsResolveError, match="vuota"):
        validate_sql("   ")
    with pytest.raises(GcsResolveError, match="_data"):
        validate_sql("SELECT 1")


def test_validate_sql_rejects_forbidden_keywords():
    # Il blocco è defense-in-depth dopo i guard SELECT + no-';':
    # raggiungibile da una SELECT che contiene una parola chiave (es. colonna).
    for bad in (
        "SELECT copy FROM _data",
        "SELECT * FROM _data WHERE drop = 1",
        "SELECT attach FROM _data",
    ):
        with pytest.raises(GcsResolveError, match="non permessa"):
            validate_sql(bad)


def test_validate_sql_quoted_column_not_false_positive():
    """Colonne legittime con nome keyword: "export" nel mart eurostat (audit giro 4)."""
    assert validate_sql('SELECT "export" FROM _data LIMIT 5') == 'SELECT "export" FROM _data LIMIT 5'
    assert validate_sql(
        'SELECT anno, "export", count(*) FROM _data GROUP BY 1, 2'
    ) == 'SELECT anno, "export", count(*) FROM _data GROUP BY 1, 2'
    # Le stringhe letterali non devono triggerare la guardia
    assert validate_sql("SELECT * FROM _data WHERE x = 'copy'") == (
        "SELECT * FROM _data WHERE x = 'copy'"
    )


# ── gcs_query tool ────────────────────────────────────────────────────────────


@pytest.mark.contract
def test_gcs_query_end_to_end_local_parquet(monkeypatch, tmp_path, sample_index):
    """Tool + DuckDB su un parquet locale (resolver monkeypatchato)."""
    import duckdb

    import dcl_mcp.gcs.tools as gcs_tools

    parquet = tmp_path / "demo_clean.parquet"
    duckdb.execute(
        f"COPY (SELECT 1 AS anno, 'Roma' AS comune UNION ALL SELECT 2, 'Milano') "
        f"TO '{parquet}' (FORMAT PARQUET)"
    )

    fake_resolved = {
        "slug": "demo_ds",
        "layer": "clean",
        "gs_path": f"gs://dataciviclab-clean/demo/{parquet.name}",
        "read_paths": [str(parquet)],
    }
    monkeypatch.setattr(gcs_tools, "load_topic_index", lambda: sample_index)
    monkeypatch.setattr(gcs_tools, "resolve", lambda *_a, **_k: fake_resolved)

    result = gcs_tools.gcs_query(
        slug="demo_ds", sql="SELECT anno, comune FROM _data ORDER BY anno", limit=10
    )

    assert result["ok"] is True
    assert result["columns"] == ["anno", "comune"]
    assert result["n_rows"] == 2
    assert result["rows"][0] == [1, "Roma"]
    assert result["truncated"] is False


@pytest.mark.contract
def test_gcs_query_slug_error_before_sql_error(monkeypatch, sample_index):
    """Lo slug inesistente non è mascherato dall'errore di convenzione _data."""
    import dcl_mcp.gcs.tools as gcs_tools

    monkeypatch.setattr(gcs_tools, "load_topic_index", lambda: sample_index)

    # slug fantasma + SQL senza _data → errore sullo slug (lookup prima)
    missing = gcs_tools.gcs_query(slug="nope", sql="SELECT 1")
    assert missing["ok"] is False
    assert "non trovato" in missing["error"]

    # slug valido + SQL senza _data → errore sulla convenzione
    bad_sql = gcs_tools.gcs_query(slug="ispra_ru_base", sql="SELECT 1")
    assert bad_sql["ok"] is False
    assert "_data" in bad_sql["error"]


@pytest.mark.contract
def test_gcs_query_limit_and_truncation(monkeypatch, tmp_path, sample_index):
    import duckdb

    import dcl_mcp.gcs.tools as gcs_tools

    parquet = tmp_path / "big.parquet"
    duckdb.execute(
        f"COPY (SELECT i AS n FROM range(20) t(i)) TO '{parquet}' (FORMAT PARQUET)"
    )
    fake_resolved = {
        "slug": "demo_ds",
        "layer": "clean",
        "gs_path": "gs://dataciviclab-clean/demo/big.parquet",
        "read_paths": [str(parquet)],
    }
    monkeypatch.setattr(gcs_tools, "load_topic_index", lambda: sample_index)
    monkeypatch.setattr(gcs_tools, "resolve", lambda *_a, **_k: fake_resolved)

    result = gcs_tools.gcs_query(slug="demo_ds", sql="SELECT n FROM _data", limit=5)
    assert result["ok"] is True
    assert result["n_rows"] == 5
    assert result["truncated"] is True

    capped = gcs_tools.gcs_query(slug="demo_ds", sql="SELECT n FROM _data", limit=9999)
    assert capped["limit"] == 500
    assert capped["limit_clamped"] == "9999 → 500 (max consentito)"
