"""Tool MCP context — orientamento, navigazione e refresh degli artefatti.

Nomi invariati rispetto al vecchio server ``dataciviclab-context`` (ACB):
contratto pubblico usato da skill, config utente e docs del Lab.
"""

from __future__ import annotations

import json
import time
from typing import Any

from lab_connectors.mcp import guard_timed

from dcl_mcp.app import get_env, mcp
from dcl_mcp.context import fetch as ctx_fetch
from dcl_mcp.context.search import explore_ref, lab_map_summary, search_index

_REFRESH_MIN_INTERVAL = 60
_last_refresh_attempt: float | None = None


@mcp.tool(
    description="Quick orientation — compact markdown overview of Lab state.",
    structured_output=True,
)
def session_bootstrap() -> dict[str, object]:
    def _exec() -> dict[str, object]:
        content = ctx_fetch.fetch("session_bootstrap.md")
        return {"content": content, "format": "markdown", "ok": True}

    return guard_timed(_exec, "session_bootstrap")


@mcp.tool(
    description=(
        "Machine-readable Lab state. Default: compact summary. "
        "Use section= to get a specific part: radar, prs, issues, "
        "discussions, registry, git, warnings."
    ),
    structured_output=True,
)
def workspace_triage(section: str | None = None) -> dict[str, object]:
    def _exec() -> dict[str, object]:
        raw = ctx_fetch.fetch("workspace_triage.json")
        data: dict = json.loads(raw)

        # Default: compact summary
        if section is None:
            radar = data.get("radar", {})
            registry = data.get("registry_summary", [])
            total_ds = sum(r.get("datasets", 0) for r in registry if r.get("available"))
            total_signals = sum(r.get("signals", 0) for r in registry if r.get("available"))
            return {
                "radar": {
                    "green": radar.get("green", 0),
                    "yellow": radar.get("yellow", 0),
                    "red": radar.get("red", 0),
                    "persistent_red": radar.get("persistent_red", 0),
                },
                "datasets": total_ds,
                "signals": total_signals,
                "repos_active": sum(1 for r in registry if r.get("available")),
                "prs": data.get("open_prs") or len(data.get("prs", [])),
                "issues": data.get("open_issues") or len(data.get("issues", [])),
                "discussions": data.get("open_discussions") or len(data.get("discussions", [])),
                "warnings": data.get("warnings", []),
                "ok": True,
            }

        # Section filter
        sections = {
            "radar": lambda d: d.get("radar", {}),
            "prs": lambda d: {"prs": d.get("prs", []), "count": len(d.get("prs", []))},
            "issues": lambda d: {"issues": d.get("issues", []), "count": len(d.get("issues", []))},
            "discussions": lambda d: {
                "discussions": d.get("discussions", []),
                "count": len(d.get("discussions", [])),
            },
            "registry": lambda d: {"registry_summary": d.get("registry_summary", [])},
            "git": lambda d: {"git_state": d.get("git_state", {})},
            "warnings": lambda d: {"warnings": d.get("warnings", [])},
            "pipeline": lambda d: {"pipeline_state": d.get("pipeline_state", {})},
            "source_health": lambda d: {"source_health": d.get("source_health", {})},
        }

        extractor = sections.get(section)
        if extractor is None:
            return {
                "ok": False,
                "error": f"Sezione '{section}' non valida. Opzioni: {list(sections.keys())}",
            }

        return {"content": extractor(data), "ok": True}

    return guard_timed(_exec, "workspace_triage")


@mcp.tool(
    description=(
        "Lab navigation map — domains → repos → sources. "
        "Use domain= to focus on one editorial theme."
    ),
    structured_output=True,
)
def lab_map(domain: str | None = None) -> dict[str, object]:
    def _exec() -> dict[str, object]:
        data = ctx_fetch.load_topic_index()
        summary = lab_map_summary(data, domain=domain)
        return {"content": summary, "ok": True}

    return guard_timed(_exec, "lab_map")


@mcp.tool(
    description=(
        "Ranked search across datasets, repos, domains, sources, and analyses. "
        "Filters: type= (dataset|repo|domain|source|analysis), domain=, limit=. "
        "Returns type + parent pointers. Use explore(ref) for details."
    ),
    structured_output=True,
)
def find(
    query: str,
    type: str | None = None,
    domain: str | None = None,
    limit: int = 10,
    include_github: bool = False,
) -> dict[str, object]:
    def _exec() -> dict[str, object]:
        data = ctx_fetch.load_topic_index()
        results = search_index(data, query, entity_type=type, domain=domain, limit=limit)
        payload: dict[str, Any] = {
            "query": query,
            "total": len(results),
            "results": results,
            "ok": True,
        }
        if include_github:
            token = get_env("GITHUB_TOKEN")
            payload["issues"] = _search_github_issues(query, token, limit)
            payload["total"] += len(payload["issues"])
        return {"content": payload, "ok": True}

    return guard_timed(_exec, "find")


@mcp.tool(
    description=(
        "Deep-dive any Lab entity by ref: dataset slug, repo name, domain slug, "
        "source id, or analysis slug. Returns card + children + routing hints."
    ),
    structured_output=True,
)
def explore(ref: str) -> dict[str, object]:
    def _exec() -> dict[str, object]:
        data = ctx_fetch.load_topic_index()
        return {"content": explore_ref(data, ref), "ok": True}

    return guard_timed(_exec, "explore")


@mcp.tool(
    description=(
        "Trigger a new context build on CI. "
        "Requires GITHUB_TOKEN with workflow scope. "
        "Artifacts updated within ~1 minute. Rate limit: 2 dispatches/hour."
    ),
    structured_output=True,
)
def refresh_context() -> dict[str, object]:
    def _exec() -> dict[str, Any]:
        global _last_refresh_attempt

        token = get_env("GITHUB_TOKEN")
        if not token:
            return {"ok": False, "error": "GITHUB_TOKEN non impostato."}

        now = time.monotonic()
        if _last_refresh_attempt is not None:
            elapsed = now - _last_refresh_attempt
            if elapsed < _REFRESH_MIN_INTERVAL:
                wait = _REFRESH_MIN_INTERVAL - elapsed
                return {
                    "ok": False,
                    "error": f"Rate limit. Aspetta ~{int(wait)}s.",
                    "retry_after": int(wait),
                }

        _last_refresh_attempt = now
        client = ctx_fetch.get_http()
        result = client.post(
            f"{ctx_fetch.API_BASE}/actions/workflows/build-context.yml/dispatches",
            json={"ref": "main"},
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
        )

        if not result.is_ok or result.response is None:
            return {"ok": False, "error": f"Errore di rete: {result.err}"}

        if result.response.status_code == 204:
            ctx_fetch.artifact_cache.clear()
            return {"ok": True, "message": "Build triggerato. Aggiornamento entro ~1 minuto."}
        elif result.response.status_code == 422:
            return {"ok": False, "error": "Build rifiutato (422). Verifica workflow su main."}
        else:
            return {"ok": False, "error": f"Errore {result.response.status_code}"}

    return guard_timed(_exec, "refresh_context")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _search_github_issues(
    query: str, token: str | None, limit: int = 10
) -> list[dict[str, object]]:
    url = "https://api.github.com/search/issues"
    headers = (
        {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
        if token
        else {}
    )
    params = {
        "q": f"{query} org:dataciviclab",
        "sort": "updated",
        "order": "desc",
        "per_page": min(limit, 50),
    }
    client = ctx_fetch.get_http()
    try:
        result = client.get(url, params=params, headers=headers)
    except Exception:
        return []
    if not result.is_ok or result.response is None:
        return []
    try:
        items = result.response.json().get("items", [])
    except Exception:
        return []
    return [
        {
            "repo": item.get("repository_url", "").replace("https://api.github.com/repos/", ""),
            "number": item.get("number"),
            "title": item.get("title", ""),
            "state": item.get("state", ""),
            "url": item.get("html_url", ""),
            "type": "pr" if "pull_request" in item else "issue",
        }
        for item in items
    ]
