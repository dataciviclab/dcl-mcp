"""Fixture condivise per i test di dcl-mcp."""

from __future__ import annotations

import json
from typing import Any

import pytest

# Indice sintetico v7 — costruito a mano (i build_* restano in ACB):
# copre dataset con/ senza tags, colonne, due domain, repo infra, analisi.
SAMPLE_INDEX: dict[str, Any] = {
    "schema_version": 7,
    "repos": {
        "rifiuti-urbani": {
            "description": "Rifiuti urbani ISPRA",
            "url": "https://github.com/dataciviclab/rifiuti-urbani",
            "role": "dati",
            "domain": "territorio-ambiente",
            "n_datasets": 1,
            "n_published": 1,
            "sources": ["ispra"],
        },
        "open-conto-annuale": {
            "description": "Conto annuale PA",
            "url": "https://github.com/dataciviclab/open-conto-annuale",
            "role": "dati",
            "domain": "finanza-pubblica",
            "n_datasets": 1,
            "n_published": 0,
            "sources": ["bdap"],
        },
        "appalti-pubblici": {
            "description": "Appalti pubblici ANAC",
            "url": "https://github.com/dataciviclab/appalti-pubblici",
            "role": "dati",
            "domain": None,
            "n_datasets": 1,
            "n_published": 1,
            "sources": ["anac"],
        },
        "toolkit": {
            "description": "Pipeline engine",
            "url": "https://github.com/dataciviclab/toolkit",
            "role": "infra",
            "domain": None,
            "n_datasets": 0,
            "n_published": 0,
            "sources": [],
        },
    },
    "datasets": {
        "ispra": [
            {
                "slug": "ispra_ru_base",
                "name": "Rifiuti Urbani",
                "stage": "published",
                "source_id": "ispra",
                "registry_source": "rifiuti-urbani",
                "category": "ambiente",
                "tags": ["rifiuti", "ispra"],
                "description": "Rifiuti urbani per comune",
                "location": {
                    "type": "gcs",
                    "path": "gs://dataciviclab-clean/rifiuti/ispra_ru_base/2024/ispra_ru_base_2024_clean.parquet",
                    "multi_file": False,
                },
            }
        ],
        "bdap": [
            {
                "slug": "bdap_entrate_stato",
                "name": "Entrate Stato",
                "stage": "incubating",
                "source_id": "bdap",
                "registry_source": "open-conto-annuale",
                "category": "finanza-pubblica",
                "tags": ["finanza-pubblica", "entrate"],
                "description": "Entrate dello Stato per titolo",
                "location": {},
            }
        ],
        "anac": [
            {
                "slug": "anac_bandi_gara",
                "name": "Anac Bandi Gara",
                "stage": "published",
                "source_id": "anac",
                "registry_source": "appalti-pubblici",
                "category": "appalti",
                "tags": ["appalti", "anac", "bandi", "gara"],
                "description": "Bandi di gara pubblici italiani (CIG): oggetto, importi, stazione appaltante",
                "columns": [
                    {
                        "name": "cig",
                        "type": "VARCHAR",
                        "role": "dimension",
                        "semantic_type": "cig_code",
                        "description": "Codice identificativo gara",
                    },
                    {
                        "name": "importo",
                        "type": "DOUBLE",
                        "role": "metric",
                        "semantic_type": "",
                        "description": "Importo aggiudicazione",
                    },
                ],
                "location": {
                    "type": "gcs",
                    "path": "gs://dataciviclab-clean/appalti_pubblici/anac_bandi_gara/*/anac_bandi_gara_*_clean.parquet",
                    "multi_file": True,
                },
            }
        ],
    },
    "domains": [
        {
            "slug": "territorio-ambiente",
            "name": "Territorio e ambiente",
            "description": "Ambiente ed energia",
            "repos": ["rifiuti-urbani"],
            "datasets": ["ispra_ru_base"],
            "dataset_count": 1,
        },
        {
            "slug": "finanza-pubblica",
            "name": "Finanza pubblica",
            "description": "Conti pubblici",
            "repos": ["open-conto-annuale"],
            "datasets": ["bdap_entrate_stato"],
            "dataset_count": 1,
        },
    ],
    "explorer_themes": [
        {"slug": "territorio-ambiente", "name": "Territorio e ambiente", "datasets": ["ispra_ru_base"]},
        {"slug": "finanza-pubblica", "name": "Finanza pubblica", "datasets": ["bdap_entrate_stato"]},
    ],
    "analyses": [
        {
            "slug": "rifiuti-urbani",
            "name": "Rifiuti urbani",
            "datasets": ["ispra_ru_base"],
            "status": "published",
        }
    ],
    "analyses_by_dataset": {"ispra_ru_base": ["rifiuti-urbani"]},
    "by_repo": {
        "appalti-pubblici": ["anac_bandi_gara"],
        "open-conto-annuale": ["bdap_entrate_stato"],
        "rifiuti-urbani": ["ispra_ru_base"],
    },
    "by_domain": {
        "finanza-pubblica": ["bdap_entrate_stato"],
        "territorio-ambiente": ["ispra_ru_base"],
    },
    "by_slug": {
        "ispra_ru_base": {
            "name": "Rifiuti Urbani",
            "stage": "published",
            "source_id": "ispra",
            "registry_source": "rifiuti-urbani",
            "domain": "territorio-ambiente",
            "category": "ambiente",
            "tags": ["rifiuti", "ispra"],
            "description": "Rifiuti urbani per comune",
        },
        "bdap_entrate_stato": {
            "name": "Entrate Stato",
            "stage": "incubating",
            "source_id": "bdap",
            "registry_source": "open-conto-annuale",
            "domain": "finanza-pubblica",
            "category": "finanza-pubblica",
            "tags": ["finanza-pubblica", "entrate"],
            "description": "Entrate dello Stato per titolo",
        },
        "anac_bandi_gara": {
            "name": "Anac Bandi Gara",
            "stage": "published",
            "source_id": "anac",
            "registry_source": "appalti-pubblici",
            "domain": None,
            "category": "appalti",
            "tags": ["appalti", "anac", "bandi", "gara"],
            "description": "Bandi di gara pubblici italiani (CIG): oggetto, importi, stazione appaltante",
        },
    },
}


@pytest.fixture
def sample_index() -> dict[str, Any]:
    """Copia profonda dell'indice sintetico — i test non devono cross-pollinarlo."""
    import copy

    return copy.deepcopy(SAMPLE_INDEX)


@pytest.fixture
def sample_index_json(sample_index) -> str:
    return json.dumps(sample_index)
