"""Istanza FastMCP condivisa + caricamento env del server.

Modulo senza dipendenze dai package tools: evita import circolari
(server → tools → mcp). Ogni modulo tools importa ``mcp`` da qui e
registra i propri tool a import-time.
"""

from __future__ import annotations

import os
from pathlib import Path

from lab_connectors.mcp import create_mcp_server

SERVER_NAME = "dataciviclab"

mcp = create_mcp_server(
    name=SERVER_NAME,
    instructions=(
        "DataCivicLab — contesto, discussioni e dati pubblici del Lab. "
        "Orientamento: session_bootstrap. Navigazione: lab_map, find, explore. "
        "Stato: workspace_triage. "
        "Partecipazione: github_* su GitHub Discussions — repo hub del Lab: "
        "dataciviclab/dataciviclab (usa repo_full_name owner/repo). "
        "Dati: gcs_query (SELECT read-only su clean/mart pubblici; slug da "
        "find/explore, layer=mart per le tabelle mart). "
        "refresh_context richiede GITHUB_TOKEN."
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
