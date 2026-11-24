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

# Il sorgente degli artifact è configurabile (ACB_REPO/ACB_BRANCH): la
# provenienza dichiarata deve seguire l'env, altrimenti mente proprio dove
# serve. Import lazy per non creare un ciclo a import-time.
_FALLBACK_SOURCE = "agent-context-builder@context"

# `/2026/` in `gs://bucket/repo/slug/2026/slug_2026_clean.parquet`.
_YEAR_SEGMENT = re.compile(r"/((?:19|20)\d{2})/")


def artifact_source() -> str:
    """`repo@branch` da cui arrivano gli artifact (configurabile via env)."""
    try:
        from dcl_mcp.context.fetch import BRANCH, REPO

        return f"{REPO}@{BRANCH}"
    except Exception:  # import parziale nei test o modulo non ancora caricato
        return _FALLBACK_SOURCE


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
    meta: dict[str, Any] = {"as_of": index.get("generated_at"), "source": artifact_source()}
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


def signal_index(topic_index: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Indice slug → aggiornamento reale, da `topic_index.datasets[]`.

    Fonte unica: il registry v2 espone per dataset `last_run`
    (``{at, year, status}``, l'esecuzione più recente su tutte le partizioni).
    ACB li riporta nel topic_index (schema v8), quindi non serve più
    `workspace_triage.registry_summary.signals_detail` (~190 KB che duplicava
    gli stessi fatti).

    Compat: finché il branch `context` pubblica topic_index v7 (senza
    `last_run`) si ricade su `signals_detail`. Rimuovere `_legacy_index`
    quando `schema_version` del topic_index live è >= 8.
    """
    if topic_index and topic_index.get("schema_version", 0) < 8:
        return _legacy_index()

    out: dict[str, dict[str, Any]] = {}
    datasets = (topic_index or {}).get("datasets") or {}
    repos = (topic_index or {}).get("repos") or {}
    if not isinstance(datasets, dict):
        return out
    for entries in datasets.values():
        for ds in entries or []:
            if not isinstance(ds, dict):
                continue
            slug = str(ds.get("slug") or "")
            if not slug:
                continue
            run = ds.get("last_run") or {}
            repo = ds.get("registry_source") or ""
            repo_updated = (repos.get(repo) or {}).get("updated_at") if repo else None
            out[slug] = {
                "last_run": run.get("at"),
                "run_year": run.get("year"),
                "run_status": run.get("status"),
                "repo_updated_at": repo_updated,
            }
    return out


def _legacy_index() -> dict[str, dict[str, Any]]:
    """Transizione: freschezza da `workspace_triage.registry_summary` (ACB < v8)."""
    from dcl_mcp.context.fetch import load_triage

    triage = load_triage()
    out: dict[str, dict[str, Any]] = {}
    for repo in (triage or {}).get("registry_summary") or []:
        if not isinstance(repo, dict):
            continue
        repo_updated = repo.get("updated_at") or None
        for signal in repo.get("signals_detail") or []:
            if not isinstance(signal, dict):
                continue
            # `label` è lo slug del dataset; `id` può essere prefissato per
            # famiglia (es. `compose:anac_cross`).
            slug = signal.get("label") or signal.get("id")
            if not slug:
                continue
            slug = str(slug).rsplit(":", 1)[-1]
            run = signal.get("run") or {}
            out[slug] = {
                "last_run": run.get("started_at"),
                "run_year": run.get("year"),
                "run_status": run.get("status"),
                "repo_updated_at": repo_updated,
            }
    return out
