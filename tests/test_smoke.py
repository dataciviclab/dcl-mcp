"""Smoke test end-to-end sul server reale (rete richiesta).

Esclusi dalla CI (`-m "not smoke"`): verificano il golden path di
onboarding sugli artefatti e sui parquet pubblicati, non la logica.
Da lanciare a mano prima di un rilascio o dopo un cambio di contratto:

    pytest tests/test_smoke.py -q -m smoke
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.smoke


def test_golden_path_onboarding():
    """session_bootstrap → find → explore → gcs_query sullo stesso dataset."""
    from dcl_mcp.context import tools as ctx
    from dcl_mcp.gcs import tools as gcs

    boot = ctx.session_bootstrap()
    assert boot["ok"] is True
    assert "Session Bootstrap" in boot["content"]

    found = ctx.find(query="rifiuti urbani", type="dataset", limit=10)["content"]
    assert found["ok"] is True
    assert found["returned"] >= 1
    # `total_catalog` è un totale, non il numero di risultati tagliati.
    assert found["total_catalog"] >= found["returned"]

    # Il criterio per `gcs_query` è la location GCS nella card, non lo
    # `stage` del catalogo (molti dataset "incubating" hanno già il parquet
    # clean pubblicato).
    card = None
    for candidate in found["results"][:5]:
        explored = ctx.explore(ref=candidate["slug"])["content"]
        if explored.get("found") and explored.get("gcs_path"):
            card = explored
            break
    if card is None:
        pytest.skip("nessun dataset con parquet pubblicato per la query di smoke")
    assert card["type"] == "dataset"

    result = gcs.gcs_query(
        slug=card["slug"], sql="SELECT count(*) AS n FROM _data", limit=1
    )
    assert result["ok"] is True, result.get("error")
    assert result["n_rows"] == 1
    assert result["rows"][0][0] > 0
    # Stesso nome e stesso path della card: il contratto regge end-to-end.
    assert result["gcs_path"] == card["gcs_path"]
