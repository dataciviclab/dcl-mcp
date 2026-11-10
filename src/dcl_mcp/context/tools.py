"""Tool MCP context — orientamento e navigazione degli artefatti.

Nomi invariati rispetto al vecchio server ``dataciviclab-context`` (ACB):
contratto pubblico usato da skill, config utente e docs del Lab.
``refresh_context`` è stato rimosso: il rebuild degli artefatti è un'azione
operativa che si lancia dalla CLI/CI di ACB, non da un MCP per esterni.
"""

from __future__ import annotations

import json
from typing import Any

from dcl_mcp.app import GITHUB_TOKEN_MISSING, get_http, github_token, mcp
from dcl_mcp.context import fetch as ctx_fetch
from dcl_mcp.context.search import (
    ENTITY_TYPES,
    explore_ref,
    lab_map_summary,
    normalize_entity_type,
    search_index,
)
from dcl_mcp.discussions.client import search_org_discussions
from dcl_mcp.guards import guard_ok


@mcp.tool(
    description="Quick orientation — compact markdown overview of Lab state.",
    structured_output=True,
)
def session_bootstrap() -> dict[str, object]:
    def _exec() -> dict[str, object]:
        content = ctx_fetch.fetch("session_bootstrap.md")
        return {"content": content, "format": "markdown", "ok": True}

    return guard_ok(_exec, "session_bootstrap")


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

    return guard_ok(_exec, "workspace_triage")


@mcp.tool(
    description=(
        "Lab navigation map — domains → repos → sources. "
        "Use domain= to focus on one editorial theme: an unknown domain "
        "returns ok=false with the list of valid domain slugs."
    ),
    structured_output=True,
)
def lab_map(domain: str | None = None) -> dict[str, object]:
    def _exec() -> dict[str, object]:
        data = ctx_fetch.load_topic_index()
        if domain:
            valid = [
                d.get("slug")
                for d in (data.get("domains") or [])
                if isinstance(d, dict) and d.get("slug")
            ]
            if domain not in valid:
                return {
                    "ok": False,
                    "error": f"Dominio '{domain}' non trovato. Domini validi: {valid}",
                    "valid_domains": valid,
                }
        summary = lab_map_summary(data, domain=domain)
        return {"content": summary, "ok": True}

    return guard_ok(_exec, "lab_map")


@mcp.tool(
    description=(
        "Suggerisce dataset joinabili con `slug`, derivati dai semantic_type "
        "dell'artifact (nessuna relazione scritta a mano). Restituisce, per ogni "
        "candidato, le coppie di colonne (`mine`/`theirs`), il tipo semantico, "
        "l'overlap anni e `type_mismatch`. Chiavi temporali/geografiche generiche "
        "(anno, mese, paese) sono escluse; `specific` conta le chiavi non "
        "territoriali (CIG, CUP, CF...), usate per il ranking. "
        "I nomi comune sono esclusi salvo `include_names=True` (confronto fragile)."
    ),
    structured_output=True,
)
def join_suggestions(
    slug: str,
    limit: int = 5,
    min_years: int = 1,
    include_names: bool = False,
) -> dict[str, object]:
    from dcl_mcp.context.joins import join_suggestions as _suggest

    def _exec() -> dict[str, object]:
        data = ctx_fetch.load_topic_index()
        return _suggest(
            data,
            slug,
            limit=limit,
            min_years=min_years,
            include_names=include_names,
        )

    return guard_ok(_exec, "join_suggestions")


@mcp.tool(
    description=(
        "Ranked search across datasets, repos, domains, sources, and analyses. "
        "All query tokens must match (cross-field AND): words may be spread "
        "across slug, name, tags, description, source_id and column schema "
        "(names/descriptions/semantic types). "
        "Filters: type= (dataset|repo|domain|source|analysis), domain=, limit=. "
        "Returns type + parent pointers + score; total_catalog is the number "
        "of matches in the catalog, returned is how many you actually see "
        "(<= limit). Use explore(ref) for details. "
        "include_github=true (needs GITHUB_TOKEN) adds open issues/PRs whose "
        "title contains all query tokens, and top open Discussions of the org "
        "on the same query."
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
        # Un type= sconosciuto restituiva ok:true con zero risultati: sembrava
        # "non esiste niente" invece di "filtro sbagliato" (lab_map(domain=)
        # era già onesto).
        if type is not None and normalize_entity_type(type) is None:
            return {
                "ok": False,
                "error": f"Tipo '{type}' non valido. Tipi: {list(ENTITY_TYPES)}",
                "valid_types": list(ENTITY_TYPES),
            }
        data = ctx_fetch.load_topic_index()
        # Tutti i match prima del taglio: `total_catalog` deve essere un
        # totale, non `len(results)` (era al massimo `limit`).
        matched = search_index(data, query, entity_type=type, domain=domain, limit=None)
        results = matched[: max(limit, 1)]
        payload: dict[str, Any] = {
            "query": query,
            "total_catalog": len(matched),
            "returned": len(results),
            "results": results,
            "ok": True,
        }
        if include_github:
            # Stessa risoluzione del token dei tool github_* (3 env var, .env
            # lazy): prima qui si guardava solo GITHUB_TOKEN.
            token = github_token()
            issues, issues_err = _search_github_issues(query, token, limit)
            discussions, discussions_err = _search_github_discussions(
                query, token, min(limit, 5)
            )
            payload["issues"] = issues
            payload["discussions"] = discussions
            payload["total_external"] = len(issues) + len(discussions)
            # Un token assente/scaduto non deve sembrare "nessun risultato".
            errors = list(dict.fromkeys(e for e in (issues_err, discussions_err) if e))
            payload["github_ok"] = not errors
            if errors:
                payload["github_errors"] = errors
        # total resta la somma esplicita: prima veniva gonfiato in-place e
        # non significava più "match nel catalogo".
        payload["total"] = payload["total_catalog"] + payload.get("total_external", 0)
        return {"content": payload, "ok": True}

    return guard_ok(_exec, "find")


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
        content = explore_ref(data, ref)
        # Mart: lo schema reale (wide) esiste solo nel parquet — il
        # clean parent indurrebbe a scrivere colonne sbagliate (audit 4).
        if isinstance(content, dict) and content.get("type") == "mart":
            from dcl_mcp.gcs.resolver import (
                GcsResolveError,
                describe_parquet_columns,
                mart_card_detail,
                resolve,
            )

            slug = content.get("slug") or ""
            repo = content.get("registry_source") or ""
            # Path del mart (per gcs_path/query_hint coerenti col layer)
            try:
                resolved = resolve(data, slug, layer="mart")
            except GcsResolveError:
                resolved = None
            # Colonne: registry se presenti (future-proof), altrimenti
            # DESCRIBE sul parquet pubblicato (footer-only)
            detail = mart_card_detail(repo, slug) if repo else None
            columns = (detail or {}).get("columns")
            if not columns and resolved is not None:
                columns = describe_parquet_columns(resolved["read_paths"])
            if columns:
                content["columns"] = columns
                if detail and detail.get("primary_key"):
                    content["primary_key"] = detail["primary_key"]
                content.pop("note", None)
            else:
                # Non indurre a usare le colonne del clean: rimuovile e dichiara.
                content.pop("columns", None)
                content["note"] = (
                    "Schema del mart non disponibile — "
                    "apri con SELECT * FROM _data LIMIT 1 (gcs_query)."
                )
            # La card è del mart: via i campi che descrivono il clean parent
            # (righe dello snapshot, lista dei mart fratelli) — restavano
            # accanto a quelli del mart e si contraddicevano.
            for stale in ("clean_rows", "clean_rows_note", "marts"):
                content.pop(stale, None)
            if resolved is not None:
                # Un solo nome per il path (`gcs_path`, come la card dataset)
                # e un solo hint, coerente col layer.
                content["gcs_path"] = resolved["gs_path"]
                # Il nome è quello del mart: senza, i tre mart dello stesso
                # dataset mostravano tutti il nome del dataset padre.
                content["name"] = resolved["name"]
                content["query_hint"] = (
                    f'gcs_query(slug="{slug}", layer="mart", '
                    'sql="SELECT count(*) AS n FROM _data")'
                )
            else:
                # gcs_path/query_hint ereditati dal clean sarebbero sbagliati
                content.pop("gcs_path", None)
                content.pop("query_hint", None)
        return {"content": content, "ok": True}

    return guard_ok(_exec, "explore")


# ── Helpers ────────────────────────────────────────────────────────────────────


def _search_github_issues(
    query: str, token: str | None, limit: int = 10
) -> tuple[list[dict[str, object]], str | None]:
    """Issue/PR aperte dell'org, filtrate sui token del titolo.

    Restituisce ``(items, errore)``: l'errore è esplicito perché un
    fallimento GitHub non deve sembrare "nessun risultato".
    """
    if not token:
        return [], GITHUB_TOKEN_MISSING
    url = "https://api.github.com/search/issues"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}
    # Default: solo issue/PR aperte — le chiuse sporcano il segnale per un
    # newcomer (friction audit giro 2: "15 item misti, issue chiuse").
    params = {
        "q": f"{query} org:dataciviclab is:open",
        "sort": "updated",
        "order": "desc",
        "per_page": min(limit, 50),
    }
    try:
        result = get_http().get(url, params=params, headers=headers)
    except Exception as exc:
        return [], f"GitHub REST search: {exc}"
    if not result.is_ok or result.response is None:
        return [], f"GitHub REST search: {result.err}"
    if result.response.status_code >= 400:
        return [], f"GitHub REST search: HTTP {result.response.status_code}"
    try:
        items = result.response.json().get("items", [])
    except Exception as exc:
        return [], f"GitHub REST search: risposta non JSON ({exc})"
    # Rilevanza: la REST search è OR-ish e restituisce rumore (audit giro 4:
    # "pnrr" → post LinkedIn, riordino script…). Teniamo solo le issue il cui
    # titolo contiene tutti i token significativi della query.
    tokens = [t for t in query.lower().split() if len(t) >= 3]
    relevant = [
        item for item in items if all(t in (item.get("title") or "").lower() for t in tokens)
    ]
    return [
        {
            "repo": item.get("repository_url", "").replace("https://api.github.com/repos/", ""),
            "number": item.get("number"),
            "title": item.get("title", ""),
            "state": item.get("state", ""),
            "url": item.get("html_url", ""),
            "type": "pr" if "pull_request" in item else "issue",
        }
        for item in relevant
    ], None


def _search_github_discussions(
    query: str, token: str | None, limit: int = 5
) -> tuple[list[dict[str, object]], str | None]:
    """Top discussioni org-wide sulla query (GraphQL, richiede token).

    Le discussioni non esistono nella REST search/issues — vanno cercate
    via GraphQL type:DISCUSSION. La query vive in ``discussions.client``
    (una sola copia, condivisa con ``github_list_discussions``).
    """
    if not token:
        return [], GITHUB_TOKEN_MISSING
    try:
        results = search_org_discussions(query, limit)
    except Exception as exc:
        return [], f"GitHub GraphQL discussions: {exc}"
    return [dict(item, type="discussion") for item in results], None
