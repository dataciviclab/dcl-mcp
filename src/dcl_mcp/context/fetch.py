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

from lab_connectors.http import HttpClient
from lab_connectors.mcp import TtlCache, get_mcp_logger

REPO = os.environ.get("ACB_REPO", "dataciviclab/agent-context-builder")
BRANCH = os.environ.get("ACB_BRANCH", "context")
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
API_BASE = f"https://api.github.com/repos/{REPO}"

ARTIFACT_TTL_SECONDS = int(os.environ.get("ACB_ARTIFACT_TTL_SECONDS", "120"))
artifact_cache: TtlCache[str, str] = TtlCache(ttl_seconds=ARTIFACT_TTL_SECONDS)

_log = get_mcp_logger("dcl-mcp-context", level=os.environ.get("ACB_LOG_LEVEL", "INFO"))

# Iniettabile nei test (FakeHttpClient), stesso pattern del vecchio server ACB.
_http: HttpClient | None = None


def get_http() -> HttpClient:
    global _http
    if _http is None:
        _http = HttpClient(timeout=15)
    return _http


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
        if result.is_ok and result.response is not None:
            text = result.response.text
            if use_cache:
                artifact_cache.set(path, text)
            return text
        last_err = Exception(
            f"HTTP {result.response.status_code if result.response else 'N/A'}: {result.err}"
        )
        _log.warning("fetch", "http_error", path=path, error=result.err)
        if attempt < retries:
            time.sleep(backoff * (2**attempt))
    raise last_err or Exception(f"Failed to fetch {path}")


def load_topic_index() -> dict[str, Any]:
    """Fetch + parse di topic_index.json (dict plain, vuoto se non dict)."""
    raw = fetch("topic_index.json")
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}
