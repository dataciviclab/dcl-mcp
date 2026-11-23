"""Tool MCP context — orientamento e navigazione degli artefatti.

Nomi invariati rispetto al vecchio server ``dataciviclab-context`` (ACB):
contratto pubblico usato da skill, config utente e docs del Lab.
``refresh_context`` è stato rimosso: il rebuild degli artefatti è un'azione
operativa che si lancia dalla CLI/CI di ACB, non da un MCP per esterni.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from dcl_mcp import telemetry
from dcl_mcp.app import GITHUB_TOKEN_MISSING, get_http, github_token, mcp
from dcl_mcp.context import fetch as ctx_fetch
from dcl_mcp.context.search import (
    ENTITY_TYPES,
    dataset_listing,
    dataset_matched_fields,
    explore_ref,
    lab_map_summary,
    normalize_entity_type,
    search_index,
)
from dcl_mcp.discussions.client import search_org_discussions
from dcl_mcp.guards import guard_ok
from dcl_mcp.provenance import catalog_meta, signal_index


@mcp.tool(
    description=(
        "Quick orientation — compact markdown overview of Lab state. "
        "Narrative entry point: for numbers and structure use workspace_triage "
        "and lab_map."
    ),
    structured_output=True,
)
def session_bootstrap() -> dict[str, object]:
    def _exec() -> dict[str, object]:
        content = ctx_fetch.fetch("session_bootstrap.md")
        # `as_of` strutturato: la data c'è già nel markdown, ma un agente che
        # non lo parsa non può datare la risposta (audit F8).
        match = re.search(r"\*\*Generated\*\*:\s*(\S+)", content)
        return {
            "content": content,
            "format": "markdown",
            "as_of": match.group(1) if match else None,
            "ok": True,
        }

    return guard_ok(_exec, "session_bootstrap")


@mcp.tool(
    description=(
        "Machine-readable Lab state (numbers and lists). Default: compact "
        "summary. For the narrative overview use session_bootstrap. "
        "Use section= to get a specific part: radar, prs, issues, "
        "discussions, registry, git, warnings. "
        "registry is compact by default (per-repo counts + updated_at); "
        "detail=true adds per-dataset pipeline runs (signals_detail, ~190 KB)."
    ),
    structured_output=True,
)
def workspace_triage(
    section: str | None = None, detail: bool = False
) -> dict[str, object]:
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
                "as_of": data.get("generated_at"),
                "ok": True,
            }

        # Section filter
        def _registry(d: dict) -> dict:
            # `signals_detail` (~190 KB, una voce per dataset) duplica ciò che
            # `find`/`explore`/`lab_map(view="datasets")` già espongono.
            # Resta disponibile con detail=true.
            repos = d.get("registry_summary", [])
            if detail:
                return {"registry_summary": repos, "detail": True}
            compact = [
                {k: v for k, v in repo.items() if k != "signals_detail"}
                if isinstance(repo, dict)
                else repo
                for repo in repos
            ]
            return {"registry_summary": compact, "detail": False}

        sections = {
            "radar": lambda d: d.get("radar", {}),
            "prs": lambda d: {"prs": d.get("prs", []), "count": len(d.get("prs", []))},
            "issues": lambda d: {"issues": d.get("issues", []), "count": len(d.get("issues", []))},
            "discussions": lambda d: {
                "discussions": d.get("discussions", []),
                "count": len(d.get("discussions", [])),
            },
            "registry": _registry,
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

        return {"content": extractor(data), "as_of": data.get("generated_at"), "ok": True}

    return guard_ok(_exec, "workspace_triage")


@mcp.tool(
    description=(
        "Lab navigation map — domains → repos → sources. "
        "Use domain= to focus on one editorial theme: an unknown domain "
        "returns ok=false with the list of valid domain slugs. "
        "view='datasets' returns one row per dataset (slug, name, years, "
        "gcs_available, snapshot_year, n_columns, last_run, run_year) — "
        "~120 KB for all 547; use domain= to shrink it (2-38 KB): use it to enumerate "
        "the catalog instead of calling explore on every dataset."
    ),
    structured_output=True,
)
def lab_map(domain: str | None = None, view: str | None = None) -> dict[str, object]:
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
        if view == "datasets":
            rows = dataset_listing(
                data, domain=domain, signals=signal_index(ctx_fetch.load_triage())
            )
            telemetry.record(
                "lab_map", {"view": "datasets", "domain": domain}, {"count": len(rows)}
            )
            return {
                "content": {"datasets": rows, "count": len(rows), "domain": domain},
                **catalog_meta(data),
                "ok": True,
            }
        if view not in (None, "tree"):
            return {
                "ok": False,
                "error": f"view '{view}' non valida. Opzioni: tree, datasets.",
                "valid_views": ["tree", "datasets"],
            }
        summary = lab_map_summary(data, domain=domain)
        telemetry.record(
            "lab_map",
            {"view": view or "tree", "domain": domain},
            {"count": len(summary.get("repos") or [])},
        )
        return {"content": summary, **catalog_meta(data), "ok": True}

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
        result = _suggest(
            data,
            slug,
            limit=limit,
            min_years=min_years,
            include_names=include_names,
        )
        result.update(catalog_meta(data))
        return result

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
        "(<= limit). Dataset results also carry snippet, years, n_columns, "
        "n_marts, snapshot_year (year of the published GCS file) and the "
        "pipeline freshness (last_run, run_year, repo_updated_at): enough "
        "to choose and to judge staleness without explore(). The payload has "
        "as_of (catalog build time, not data freshness). "
        "Repo/domain/source results are compact: use explore(ref) for their "
        "card. Use explore(ref) for details. "
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
        started = time.perf_counter()
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
        # Aggiornamento reale per dataset (ultima run di pipeline + updated_at
        # del repo), agganciato qui per non obbligare l'agente a incrociare
        # `workspace_triage(section="registry")` con 547 `explore`.
        signals = signal_index(ctx_fetch.load_triage())
        entries = {
            d["slug"]: d
            for d in (ds for v in (data.get("datasets") or {}).values() for ds in v)
            if d.get("slug")
        }
        for item in results:
            if item.get("type") == "dataset" and item["slug"] in entries:
                item["matched"] = dataset_matched_fields(entries[item["slug"]], query)
            if item.get("type") == "dataset":
                update = signals.get(str(item.get("slug")))
                if update:
                    item.update(update)
        if not results:
            payload_hint = (
                "Nessun match sopra la soglia. Prova un acronimo del Lab "
                "(CIG, CUP, NASpI, PNRR) o un termine del catalogo "
                "(es. 'bandi di gara', 'rifiuti urbani')."
            )
        else:
            payload_hint = None
        payload: dict[str, Any] = {
            "query": query,
            "total_catalog": len(matched),
            "returned": len(results),
            "results": results,
            "ok": True,
        }
        if payload_hint:
            payload["hint"] = payload_hint
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
        telemetry.record(
            "find",
            {"query": query, "type": type, "domain": domain, "limit": limit,
             "github": include_github},
            {"total": len(matched), "returned": len(results),
             "top": [r.get("slug") for r in results[:5]],
             "top_types": [r.get("type") for r in results[:5]]},
            int((time.perf_counter() - started) * 1000),
        )
        return {"content": payload, **catalog_meta(data), "ok": True}

    return guard_ok(_exec, "find")


@mcp.tool(
    description=(
        "Deep-dive any Lab entity by ref: dataset slug, repo name, domain slug, "
        "source id, or analysis slug. Returns card + children + routing hints. "
        "The payload has as_of (catalog build time); a dataset/mart card has "
        "snapshot_year (year of the published GCS file, if in the path) and "
        "the pipeline freshness (last_run, run_year, repo_updated_at) and a "
        "`joins` teaser (top 2, with joins_total): the full ranked list with "
        "type_mismatch comes from join_suggestions(slug)."
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
                content["n_columns"] = len(columns)
                # Onestà: le colonne del mart vengono dal DESCRIBE del parquet,
                # che non porta role/semantic_type. Senza dirlo, l'agente
                # presume che valgano come quelle del clean.
                if not any("role" in column for column in columns):
                    content["columns_note"] = (
                        "Colonne dal parquet (DESCRIBE): solo nome e tipo. "
                        "role e semantic_type non sono disponibili per i mart."
                    )
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
        # Aggiornamento reale della pipeline (una card databile); per i mart si
        # usa lo slug del dataset padre, che è la chiave dei segnali.
        if isinstance(content, dict) and content.get("type") in ("dataset", "mart"):
            signal_slug = content.get("dataset") or content.get("slug")
            update = signal_index(ctx_fetch.load_triage()).get(str(signal_slug))
            if update:
                content.update(update)
        telemetry.record(
            "explore",
            {"ref": ref},
            {"found": bool(content.get("found")) if isinstance(content, dict) else None,
             "entity": content.get("type") if isinstance(content, dict) else None},
        )
        return {"content": content, **catalog_meta(data), "ok": True}

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
