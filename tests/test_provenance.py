"""Contratto di provenienza: `as_of`, `snapshot_year`, campi di decisione.

Protegge il comportamento introdotto dall'audit usabilità 2026-11-23:
senza questi campi l'agente non può datare una risposta (F8) né distinguere un
dataset aggiornato da uno vecchio senza aprirlo (F5, R02).
"""

from __future__ import annotations

import pytest

from dcl_mcp.context.search import explore_ref, search_index
from dcl_mcp.provenance import catalog_meta, dataset_snapshot, signal_index, snapshot_year

pytestmark = pytest.mark.pure_unit


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("gs://dataciviclab-clean/a/slug/2026/slug_2026_clean.parquet", 2026),
        ("gs://dataciviclab-mart/a/slug/2025/mart_x.parquet", 2025),
        # glob: nessun anno nel path → non si indovina
        ("gs://dataciviclab-clean/a/slug/*/slug_*_clean.parquet", None),
        ("", None),
        (None, None),
        # anno non plausibile non deve matchare (es. un codice)
        ("gs://b/a/slug/9999/x.parquet", None),
    ],
)
def test_snapshot_year(path, expected):
    assert snapshot_year(path) == expected


def test_catalog_meta_carries_as_of_and_schema():
    from dcl_mcp.context import fetch as ctx_fetch

    meta = catalog_meta(
        {"generated_at": "2026-11-23T06:02:34", "schema_version": 7}
    )
    assert meta["as_of"] == "2026-11-23T06:02:34"
    assert meta["schema_version"] == 7
    # La fonte dichiarata segue l'env (ACB_REPO/ACB_BRANCH), non è costante.
    assert meta["source"] == f"{ctx_fetch.REPO}@{ctx_fetch.BRANCH}"


def test_catalog_meta_tolerates_empty_index():
    meta = catalog_meta(None)
    assert meta["as_of"] is None
    assert "schema_version" not in meta


def test_signal_index_uses_label_not_prefixed_id():
    """`id` può essere prefissato (`compose:slug`); la chiave è `label`.

    Regressione: con `id` prima, i 42 dataset compose restavano senza
    last_run/run_year, proprio quelli la cui freschezza dipende da altri.
    """
    triage = {
        "registry_summary": [
            {
                "repo": "appalti-pubblici",
                "updated_at": "2026-11-22",
                "signals_detail": [
                    {
                        "id": "compose:anac_cross",
                        "label": "anac_cross",
                        "run": {"year": 2026, "started_at": "2026-11-16T10:00:00Z"},
                    },
                    # senza label: si recupera togliendo il prefisso
                    {"id": "compose:sentenze_complete", "run": {"year": 2025}},
                ],
            }
        ]
    }
    index = signal_index(triage)
    assert index["anac_cross"]["run_year"] == 2026
    assert index["sentenze_complete"]["run_year"] == 2025
    assert "compose:anac_cross" not in index


def test_signal_index_from_registry_summary():
    """L'aggiornamento per dataset arriva da registry_summary.signals_detail."""
    triage = {
        "registry_summary": [
            {
                "repo": "dataset-incubator",
                "updated_at": "2026-11-22",
                "signals_detail": [
                    {
                        "id": "personale_ssn",
                        "run": {
                            "run_id": "x",
                            "year": 2021,
                            "status": "SUCCESS",
                            "started_at": "2026-11-06T22:12:55+00:00",
                        },
                    },
                    {"id": "pensioni_pa_dag"},  # nessuna run
                ],
            },
            {"repo": "rotto", "signals_detail": "non-una-lista"},
            "non-un-dict",
        ]
    }
    index = signal_index(triage)
    assert index["personale_ssn"] == {
        "last_run": "2026-11-06T22:12:55+00:00",
        "run_year": 2021,
        "run_status": "SUCCESS",
        "repo_updated_at": "2026-11-22",
    }
    assert index["pensioni_pa_dag"]["last_run"] is None
    assert signal_index(None) == {}


def test_dataset_card_joins_is_a_teaser():
    """La card dà i primi 2 join e dice quanti sono in totale."""
    index = {
        "by_slug": {"a": {}, "b": {}, "c": {}},
        "datasets": {
            "s1": [
                {
                    "slug": "a",
                    "name": "A",
                    "registry_source": "r",
                    "period": {"start": 2020, "end": 2024},
                    "columns": [
                        {"name": "cig", "type": "VARCHAR", "semantic_type": "cig_code"}
                    ],
                }
            ],
            "s2": [
                {
                    "slug": "b",
                    "name": "B",
                    "registry_source": "r",
                    "period": {"start": 2020, "end": 2024},
                    "columns": [
                        {"name": "cig", "type": "VARCHAR", "semantic_type": "cig_code"}
                    ],
                }
            ],
            "s3": [
                {
                    "slug": "c",
                    "name": "C",
                    "registry_source": "r",
                    "period": {"start": 2021, "end": 2024},
                    "columns": [
                        {"name": "cig", "type": "VARCHAR", "semantic_type": "cig_code"}
                    ],
                }
            ],
        },
        "repos": {},
        "domains": [],
    }
    card = explore_ref(index, "a")
    assert [pair["slug"] for pair in card["joins"]] == ["b", "c"]
    assert card["joins_total"] == 2


def test_dataset_snapshot_from_location():
    snap = dataset_snapshot(
        {"location": {"path": "gs://b/a/s/2024/f.parquet", "multi_file": True}}
    )
    assert snap == {"snapshot_year": 2024, "multi_file": True}
    assert dataset_snapshot(None) == {"snapshot_year": None, "multi_file": False}


def test_find_results_carry_decision_fields(sample_index):
    """Il risultato dataset deve bastare a scegliere senza `explore`."""
    results = search_index(sample_index, "anac bandi", entity_type="dataset")
    assert results, "la query deve trovare anac_bandi_gara"
    item = results[0]
    assert item["slug"] == "anac_bandi_gara"
    assert item["n_columns"] == 2
    assert item["snippet"].startswith("Bandi di gara")
    assert item["snapshot_year"] is None  # glob: anno non deducibile


def test_find_results_snapshot_year_when_path_has_year(sample_index):
    results = search_index(sample_index, "rifiuti urbani", entity_type="dataset")
    item = next(r for r in results if r["slug"] == "ispra_ru_base")
    assert item["snapshot_year"] == 2024
    assert item["n_marts"] == 1
    assert item["years"] == {"start": 2010, "end": 2024}
