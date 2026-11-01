"""Istanza FastMCP condivisa + caricamento env del server.

Modulo senza dipendenze dai package tools: evita import circolari
(server → tools → mcp). Ogni modulo tools importa ``mcp`` da qui e
registra i propri tool a import-time.
"""

from __future__ import annotations

import os
from pathlib import Path

from lab_connectors.http import HttpClient
from lab_connectors.mcp import create_mcp_server

SERVER_NAME = "dataciviclab"
# Organizzazione GitHub del Lab: hub delle Discussions e perimetro della
# ricerca org-wide di `find(include_github=True)`.
DCL_ORG = "dataciviclab"

# Ordine di fallback per il token GitHub — unica regola per context e
# discussions (prima `find` guardava solo GITHUB_TOKEN e i tool github_*
# accettavano anche GITHUB_PERSONAL_ACCESS_TOKEN/GH_TOKEN).
GITHUB_TOKEN_ENV_VARS = ("GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")
GITHUB_TOKEN_MISSING = (
    "Nessun token GitHub: imposta GITHUB_TOKEN "
    "(o GITHUB_PERSONAL_ACCESS_TOKEN/GH_TOKEN)"
)

# Client HTTP condiviso da context e discussions: una sola sessione/pool.
_http: HttpClient | None = None

mcp = create_mcp_server(
    name=SERVER_NAME,
    instructions=(
        "DataCivicLab — contesto, discussioni e dati pubblici del Lab. "
        "Orientamento: session_bootstrap. Navigazione: lab_map, find, explore. "
        "Stato: workspace_triage. "
        "Partecipazione: github_* su GitHub Discussions — repo hub del Lab "
        "dataciviclab/dataciviclab (default di repo_full_name). "
        "Leggi una discussion con github_get_discussion(include_comments=True). "
        "Dati: gcs_query (SELECT read-only su clean/mart pubblici; slug da "
        "find/explore, layer=mart per le tabelle mart). "
        "Ogni risposta ha ok: true/false (in errore error/message). "
    ),
)

_ENV_LOADED = False


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    value = value.strip().strip('"').strip("'")
    return key, value


def _candidate_env_paths() -> list[Path]:
    explicit_vars = ("DCL_MCP_ENV_FILE", "ACB_ENV_FILE")
    paths: list[Path] = []
    for var in explicit_vars:
        value = os.environ.get(var, "").strip()
        if value:
            paths.append(Path(value).expanduser())
    starts = [Path.cwd(), Path(__file__).resolve()]
    for start in starts:
        current = start if start.is_dir() else start.parent
        paths.extend(parent / ".env" for parent in [current] + list(current.parents))
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def load_env() -> bool:
    """Carica ``.env`` (mai sovrascrivendo env già impostate). Idempotente."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return True
    _ENV_LOADED = True
    loaded = False
    for path in _candidate_env_paths():
        if not path.is_file():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                parsed = _parse_env_line(line)
                if parsed is None:
                    continue
                key, value = parsed
                if key not in os.environ or not os.environ[key]:
                    os.environ[key] = value
                    loaded = True
        except OSError:
            continue
    return loaded


def get_env(name: str) -> str | None:
    """Legge una env var, caricando i .env candidati la prima volta."""
    load_env()
    return os.environ.get(name) or None


def get_http() -> HttpClient:
    """Client HTTP condiviso del server (timeout 20s, retry/backoff inclusi)."""
    global _http
    if _http is None:
        _http = HttpClient(timeout=20)
    return _http


def github_token() -> str | None:
    """Token GitHub dall'ambiente (o dal `.env`), o None se assente."""
    for var in GITHUB_TOKEN_ENV_VARS:
        value = get_env(var)
        if value and value.strip():
            return value.strip()
    return None
