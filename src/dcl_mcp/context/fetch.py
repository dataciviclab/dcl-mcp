"""Fetch degli artefatti contesto dal branch ``context`` di ACB.

Client HTTP-only: gli artefatti vivono su
``raw.githubusercontent.com/{ACB_REPO}/{ACB_BRANCH}`` (default:
``dataciviclab/agent-context-builder`` branch ``context``), prodotti
dalla CI di ACB ogni ~6h. TTL cache per le sequenze
lab_map → find → explore sullo stesso ``topic_index.json``.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from lab_connectors.mcp import TtlCache, get_mcp_logger

from dcl_mcp.app import get_http

REPO = os.environ.get("ACB_REPO", "dataciviclab/agent-context-builder")
BRANCH = os.environ.get("ACB_BRANCH", "context")
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"

ARTIFACT_TTL_SECONDS = int(os.environ.get("ACB_ARTIFACT_TTL_SECONDS", "120"))
artifact_cache: TtlCache[str, str] = TtlCache(ttl_seconds=ARTIFACT_TTL_SECONDS)

_log = get_mcp_logger("dcl-mcp-context", level=os.environ.get("ACB_LOG_LEVEL", "INFO"))


def fetch(path: str, retries: int = 1, backoff: float = 1.0, use_cache: bool = True) -> str:
    """Scarica un artefatto dal branch context, con TTL cache opzionale."""
    if use_cache:
        cached = artifact_cache.get(path)
        if cached is not None:
            return cached

    url = f"{RAW_BASE}/{path}"
    client = get_http()
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            result = client.get(url)
        except Exception as exc:
            last_err = exc
            _log.warning("fetch", "exception", path=path, attempt=attempt, error=str(exc))
            if attempt < retries:
                time.sleep(backoff * (2**attempt))
            continue
        # `is_ok` di HttpResult significa "nessuna eccezione", non "2xx": un
        # 404 di raw.githubusercontent è un response valido con err=None.
        # Senza il check sullo status il corpo d'errore finiva in cache per
        # il TTL e riemergeva come "JSON invalido".
        response = result.response
        if response is not None and response.status_code < 400:
            text = response.text
            if use_cache:
                artifact_cache.set(path, text)
            return text
        status = response.status_code if response is not None else "N/A"
        last_err = Exception(f"HTTP {status}: {result.err or 'risposta non valida'}")
        _log.warning("fetch", "http_error", path=path, status=status, error=result.err)
        if attempt < retries:
            time.sleep(backoff * (2**attempt))
    raise last_err or Exception(f"Failed to fetch {path}")


def load_topic_index() -> dict[str, Any]:
    """Fetch + parse di topic_index.json (dict plain, vuoto se non dict)."""
    raw = fetch("topic_index.json")
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}
