"""Query sul topic_index: search_index, lab_map_summary, explore_ref."""

from __future__ import annotations

import pytest

from dcl_mcp.context.search import (
    _field_words,
    _tokenize_query,
    _word_forms,
    explore_ref,
    lab_map_summary,
    search_index,
)

pytestmark = pytest.mark.pure_unit


# ── tokenize / word helpers ───────────────────────────────────────────────────


def test_tokenize_query_drops_stopwords_and_accents():
    assert _tokenize_query("bandi di gara") == ["bandi", "gara"]
    assert _tokenize_query("appalti pubblici ANAC") == ["appalti", "pubblici", "anac"]
    assert _tokenize_query("spesa più recente") == ["spesa", "recente"]
    assert _tokenize_query("di delle dei") == []


def test_field_words_split_snake_case():
    assert _field_words("anac_bandi_gara") == ["anac", "bandi", "gara"]


def test_word_forms_match_plural_gender():
    assert _word_forms("gara") & _word_forms("gare")
    assert _word_forms("pubblico") & _word_forms("pubblici")
    assert _word_forms("appalto") & _word_forms("appalti")


# ── search_index ──────────────────────────────────────────────────────────────


def test_search_exact_slug(sample_index):
    results = search_index(sample_index, "ispra_ru_base")
    assert results
    assert results[0]["type"] == "dataset"
    assert results[0]["slug"] == "ispra_ru_base"
    assert results[0]["parent_repo"] == "rifiuti-urbani"
    assert results[0]["parent_domain"] == "territorio-ambiente"


def test_search_type_filter_repo(sample_index):
    results = search_index(sample_index, "toolkit", entity_type="repo")
    assert results
    assert results[0]["type"] == "repo"
    assert results[0]["role"] == "infra"


def test_search_domain_filter(sample_index):
    results = search_index(sample_index, "rifiuti", domain="territorio-ambiente")
    assert any(r["slug"] == "ispra_ru_base" for r in results)
    results2 = search_index(sample_index, "entrate", domain="territorio-ambiente")
    assert results2 == []


def test_search_source_and_analysis(sample_index):
    src = search_index(sample_index, "ispra", entity_type="source")
    assert src and src[0]["type"] == "source" and src[0]["n_datasets"] == 1
    ana = search_index(sample_index, "rifiuti", entity_type="analysis")
    assert ana and ana[0]["type"] == "analysis"


def test_search_natural_query_cross_field(sample_index):
    """Regression: 'appalti pubblici anac' trova anac_bandi_gara (token sparsi)."""
    results = search_index(sample_index, "appalti pubblici anac", entity_type="dataset")
    assert results
    assert results[0]["slug"] == "anac_bandi_gara"


def test_search_stopword_query_finds_snake_case_slug(sample_index):
    results = search_index(sample_index, "bandi di gara", entity_type="dataset")
    assert "anac_bandi_gara" in [r["slug"] for r in results]


def test_search_morphology_plural(sample_index):
    results = search_index(sample_index, "bandi gare anac", entity_type="dataset")
    assert results and results[0]["slug"] == "anac_bandi_gara"


def test_search_column_name_and_semantic_type(sample_index):
    assert search_index(sample_index, "importo aggiudicazione")[0]["slug"] == "anac_bandi_gara"
    assert search_index(sample_index, "cig")[0]["slug"] == "anac_bandi_gara"


def test_search_single_stopword_returns_nothing(sample_index):
    assert search_index(sample_index, "e", entity_type="dataset") == []
    assert search_index(sample_index, "di dei delle", entity_type="dataset") == []


def test_search_natural_question(sample_index):
    assert _tokenize_query("quanti bandi di gara ha anac") == ["bandi", "gara", "anac"]
    results = search_index(sample_index, "quanti bandi di gara ha anac", entity_type="dataset")
    assert results and results[0]["slug"] == "anac_bandi_gara"


def test_search_respects_limit(sample_index):
    unlimited = search_index(sample_index, "rifiuti", limit=10)
    limited = search_index(sample_index, "rifiuti", limit=1)
    assert len(unlimited) > 1
    assert len(limited) == 1


# ── lab_map_summary ───────────────────────────────────────────────────────────


def test_lab_map_summary_tree(sample_index):
    summary = lab_map_summary(sample_index)
    assert summary["ok"] is True
    assert summary["totals"]["datasets"] == 3
    assert summary["totals"]["published"] == 2
    assert {d["slug"] for d in summary["domains"]} == {"territorio-ambiente", "finanza-pubblica"}
    assert any(r["repo"] == "toolkit" for r in summary["repos"])
    assert summary["sources"]["ispra"] == 1


def test_lab_map_summary_domain_filter(sample_index):
    summary = lab_map_summary(sample_index, domain="finanza-pubblica")
    assert len(summary["domains"]) == 1
    assert summary["domains"][0]["slug"] == "finanza-pubblica"
    assert all(r["domain"] == "finanza-pubblica" for r in summary["repos"])


# ── explore_ref ───────────────────────────────────────────────────────────────


def test_explore_dataset_repo_domain(sample_index):
    ds = explore_ref(sample_index, "ispra_ru_base")
    assert ds["found"] is True
    assert ds["type"] == "dataset"
    assert ds["registry_source"] == "rifiuti-urbani"
    assert ds["analyses"] == ["rifiuti-urbani"]
    assert ds["repo"]["role"] == "dati"
    # Enrichment explore → gcs_query
    assert ds["description"] == "Rifiuti urbani per comune"
    assert ds["year_min"] == 2010 and ds["year_max"] == 2024
    assert ds["clean_rows"] == 123456
    assert "serie completa" in ds["clean_rows_note"]
    assert ds["marts"] == ["ispra_ru_base__mart_comuni"]
    assert 'layer="mart"' in ds["marts_hint"]
    assert "SELECT count(*)" in ds["marts_hint"]
    assert ds["gcs_path"].startswith("gs://dataciviclab-clean/")
    assert 'slug="ispra_ru_base"' in ds["query_hint"]
    assert "SELECT count(*)" in ds["query_hint"]

    repo = explore_ref(sample_index, "rifiuti-urbani")
    assert repo["found"] is True and repo["type"] == "repo"
    assert repo["datasets"] == ["ispra_ru_base"]
    assert repo["n_published"] == 1

    dom = explore_ref(sample_index, "territorio-ambiente")
    assert dom["found"] is True and dom["type"] == "domain"
    assert "ispra_ru_base" in dom["datasets"]


def test_explore_typed_refs_disambiguate_collision(sample_index):
    """Prefisso analysis: risolve la collisione slug analysis/repo."""
    untyped = explore_ref(sample_index, "rifiuti-urbani")
    assert untyped["type"] == "repo"  # senza prefisso il repo vince

    typed = explore_ref(sample_index, "analysis:rifiuti-urbani")
    assert typed["found"] is True
    assert typed["type"] == "analysis"
    assert typed["datasets"] == ["ispra_ru_base"]

    forced_repo = explore_ref(sample_index, "repo:rifiuti-urbani")
    assert forced_repo["type"] == "repo"

    missing_analysis = explore_ref(sample_index, "analysis:inexistent")
    assert missing_analysis["found"] is False


def test_explore_not_found_suggests_similar(sample_index):
    card = explore_ref(sample_index, "rifiut")
    assert card["found"] is False
    assert card["suggestions"]
    slugs = [s["slug"] for s in card["suggestions"]]
    assert "ispra_ru_base" in slugs


def test_explore_not_found_no_match_has_hint(sample_index):
    card = explore_ref(sample_index, "dataset_fantasma_123")
    assert card["found"] is False
    assert "suggestions" not in card
    assert "find(query=" in card["hint"]


def test_search_analysis_domain_filter_and_parent(sample_index):
    """Il filtro domain= deve valere anche per le analysis (audit giro 3)."""
    # analysis "rifiuti-urbani" collegata a ispra_ru_base → territorio-ambiente
    in_domain = search_index(
        sample_index, "rifiuti", entity_type="analysis", domain="territorio-ambiente"
    )
    assert any(a["slug"] == "rifiuti-urbani" for a in in_domain)
    assert in_domain[0]["parent_domain"] == "territorio-ambiente"

    out_domain = search_index(
        sample_index, "rifiuti", entity_type="analysis", domain="finanza-pubblica"
    )
    assert out_domain == []


def test_explore_dataset_card_includes_compact_columns(sample_index):
    card = explore_ref(sample_index, "anac_bandi_gara")
    assert card["found"] is True
    assert [c["name"] for c in card["columns"]] == ["cig", "importo"]
    # colonna compatta: name/type/role, niente description/semantic_type
    assert set(card["columns"][0]) == {"name", "type", "role"}
    assert "*" in card["gcs_path"]


def test_explore_analysis_and_source(sample_index):
    ana = explore_ref(sample_index, "rifiuti-urbani")
    # repo wins over analysis with the same slug — repo è il tipo primario
    assert ana["type"] == "repo"

    src = explore_ref(sample_index, "ispra")
    assert src["found"] is True and src["type"] == "source"
    assert src["datasets"] == ["ispra_ru_base"]


def test_explore_source_dedupes_slugs(sample_index):
    sample_index["datasets"]["ispra"].append(dict(sample_index["datasets"]["ispra"][0]))
    src = explore_ref(sample_index, "ispra")
    assert src["datasets"] == ["ispra_ru_base"]
    assert src["n_datasets"] == 1


def test_explore_not_found(sample_index):
    assert explore_ref(sample_index, "nope")["found"] is False
