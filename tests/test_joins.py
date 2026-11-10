"""join_suggestions: join derivati dai semantic_type dell'artifact ACB."""

from __future__ import annotations

import pytest

from dcl_mcp.context.joins import join_suggestions

pytestmark = pytest.mark.pure_unit


def _col(name: str, stype: str = "", ctype: str = "VARCHAR", role: str = "dimension") -> dict:
    return {"name": name, "type": ctype, "role": role, "semantic_type": stype, "description": ""}


def _entry(slug: str, columns: list[dict], *, start: int = 2020, end: int = 2023, category: str = "pa") -> dict:
    return {
        "slug": slug,
        "name": slug.replace("_", " ").title(),
        "category": category,
        "stage": "published",
        "period": {"start": start, "end": end},
        "columns": columns,
    }


def _index(*entries: dict) -> dict:
    return {"datasets": {"fonte": list(entries)}}


def test_suggerisce_dataset_con_chiavi_condivise():
    idx = _index(
        _entry("spesa_comuni", [_col("codice_istat", "municipality_code"), _col("anno", "year")]),
        _entry("popolazione_comuni", [_col("cod_istat", "municipality_code"), _col("anno", "year")]),
        _entry("bandi_gara", [_col("cig", "cig_code")]),
    )
    res = join_suggestions(idx, "spesa_comuni")

    assert res["found"] is True
    assert res["keys"] == [{"type": "municipality_code", "column": "codice_istat"}]
    assert [s["slug"] for s in res["suggestions"]] == ["popolazione_comuni"]
    assert res["suggestions"][0]["on"][0] == {
        "type": "municipality_code",
        "mine": "codice_istat",
        "theirs": "cod_istat",
        "mine_type": "VARCHAR",
        "theirs_type": "VARCHAR",
        "type_mismatch": False,
    }


def test_esclude_year_month_country_dal_join():
    idx = _index(
        _entry("a", [_col("anno", "year"), _col("mese", "month"), _col("paese", "country_code")]),
        _entry("b", [_col("anno", "year"), _col("mese", "month"), _col("paese", "country_code")]),
    )
    res = join_suggestions(idx, "a")

    assert res["keys"] == []
    assert res["suggestions"] == []
    assert "nessuna chiave semantica" in res["message"]


def test_nomi_esclusi_salvo_include_names():
    idx = _index(
        _entry("a", [_col("comune", "municipality_name")]),
        _entry("b", [_col("comune", "municipality_name")]),
    )
    assert join_suggestions(idx, "a")["suggestions"] == []

    res = join_suggestions(idx, "a", include_names=True)
    assert [s["slug"] for s in res["suggestions"]] == ["b"]


def test_min_years_filtra_chi_non_si_sovrappone():
    idx = _index(
        _entry("a", [_col("codice_istat", "municipality_code")], start=2020, end=2021),
        _entry("b", [_col("codice_istat", "municipality_code")], start=1990, end=1995),
    )
    assert join_suggestions(idx, "a")["suggestions"] == []


def test_rank_chiavi_poi_anni_poi_categoria():
    idx = _index(
        _entry("target", [_col("c1", "municipality_code"), _col("c2", "region_code")], category="pa"),
        _entry("una_chiave", [_col("c1", "municipality_code")], start=2020, end=2023, category="pa"),
        _entry("due_chiavi", [_col("c1", "municipality_code"), _col("c2", "region_code")], start=2019, end=2023, category="sanita"),
        _entry("due_chiavi_stessa_cat", [_col("c1", "municipality_code"), _col("c2", "region_code")], start=2019, end=2023, category="pa"),
    )
    slugs = [s["slug"] for s in join_suggestions(idx, "target")["suggestions"]]

    assert slugs[:2] == ["due_chiavi_stessa_cat", "due_chiavi"]
    assert slugs[-1] == "una_chiave"


def test_flag_type_mismatch():
    idx = _index(
        _entry("a", [_col("id_ddl", "ddl_id", "VARCHAR")]),
        _entry("b", [_col("id_ddl", "ddl_id", "BIGINT")]),
    )
    on = join_suggestions(idx, "a")["suggestions"][0]["on"][0]

    assert on["type_mismatch"] is True


def test_slug_sconosciuto():
    res = join_suggestions(_index(), "non_esiste")

    assert res["found"] is False
    assert res["suggestions"] == []
    assert "non presente" in res["message"]


def test_limite_e_truncated():
    idx = _index(
        _entry("target", [_col("c1", "municipality_code")]),
        *[_entry(f"altro_{i}", [_col("c1", "municipality_code")]) for i in range(4)],
    )
    res = join_suggestions(idx, "target", limit=2)

    assert len(res["suggestions"]) == 2
    assert res["total_candidates"] == 4
    assert res["truncated"] is True
