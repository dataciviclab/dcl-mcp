"""Provenienza delle risposte: da dove viene un dato e a quando risale.

Perché esiste: senza un campo strutturato di temporalità l'agente non può
distinguere un catalogo di oggi da uno di dieci giorni fa, né dire se un
dataset è aggiornato. Le risposte che citano numeri devono essere databili
(vedi `_local/audit/dcl-mcp-usability-2026-11-23/`, finding F8/F10).

Due livelli distinti, da non confondere:

- **catalogo** (`catalog_meta`): quando è stato costruito l'artifact ACB —
  vale per la scoperta, non per i dati.
- **snapshot** (`snapshot_year`): l'anno del file pubblicato su GCS, ricavato
  dalla `location` (`.../<dataset>/<anno>/<file>.parquet`). È il dato che dice
  se un dataset è vecchio; per i glob multi-file è l'anno più recente noto dal
  path, non la copertura della serie.
"""

from __future__ import annotations

import re
from typing import Any

SOURCE = "agent-context-builder@context"

# `/2026/` in `gs://bucket/repo/slug/2026/slug_2026_clean.parquet`.
_YEAR_SEGMENT = re.compile(r"/((?:19|20)\d{2})/")


def snapshot_year(gs_path: str | None) -> int | None:
    """Anno dello snapshot dal path GCS, o None se non deducibile.

    Per i path con glob (`.../slug/*/file.parquet`) non c'è un anno nel path:
    resta None invece di indovinare l'ultimo anno della serie.
    """
    if not gs_path:
        return None
    match = _YEAR_SEGMENT.search(gs_path)
    return int(match.group(1)) if match else None


def catalog_meta(topic_index: dict[str, Any] | None) -> dict[str, Any]:
    """Metadati di provenienza del catalogo, da fondere nel payload del tool."""
    index = topic_index or {}
    meta: dict[str, Any] = {"as_of": index.get("generated_at"), "source": SOURCE}
    if index.get("schema_version") is not None:
        meta["schema_version"] = index.get("schema_version")
    return meta


def dataset_snapshot(entry: dict[str, Any] | None) -> dict[str, Any]:
    """Snapshot pubblicato di una voce dataset del topic_index."""
    location = (entry or {}).get("location") or {}
    path = location.get("path") or ""
    return {
        "snapshot_year": snapshot_year(path),
        "multi_file": bool(location.get("multi_file")),
    }
