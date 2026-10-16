"""Query sul topic_index (schema v7) — search, lab_map, explore.

Funzioni pure su dict plain: zero I/O, zero tipi del builder ACB.
Spostate da ``agent_context_builder.navigation`` con la migrazione
del MCP nel repo dcl-mcp (ACB tiene solo i build_*).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Function words that pollute natural-language queries ("bandi di gara",
# "appalti pubblici anac"). Matched after de-accenting, so "piu" covers "più".
# Curated minimal set — not a full Italian stopword list.
_STOPWORDS = frozenset(
    {
        "a",
        "ad",
        "agli",
        "ai",
        "al",
        "alle",
        "allo",
        "anche",
        "chi",
        "ci",
        "come",
        "cui",
        "da",
        "dagli",
        "dai",
        "dal",
        "dalle",
        "del",
        "dello",
        "delle",
        "dei",
        "degli",
        "di",
        "dove",
        "ed",
        "e",
        "fra",
        "gli",
        "ha",
        "hanno",
        "i",
        "il",
        "in",
        "la",
        "le",
        "lo",
        "ma",
        "nei",
        "negli",
        "nel",
        "nello",
        "non",
        "o",
        "oppure",
        "per",
        "piu",
        "quale",
        "quali",
        "quanti",
        "quando",
        "si",
        "sono",
        "sugli",
        "sulle",
        "sullo",
        "sul",
        "su",
        "sui",
        "tra",
        "un",
        "una",
        "uno",
    }
)

# Field weights for the token tier (all tokens matched across the entity).
_FIELD_TOKEN_WEIGHTS = {"slug": 50, "name": 55, "text": 35, "tag": 28}
# Tier weights for exact whole-query and substring matches.
_EXACT_WEIGHTS = {"slug": 100, "name": 80, "text": 40, "tag": 30}
_SUBSTR_WEIGHTS = {"slug": 80, "name": 60, "text": 30, "tag": 25}


def flatten_datasets(datasets_by_source: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Flatten topic_index ``datasets`` dict into a list of dataset entries."""
    flat: list[dict[str, Any]] = []
    for _source, items in (datasets_by_source or {}).items():
        for item in items or []:
            if isinstance(item, dict) and item.get("slug"):
                flat.append(item)
    return flat


def _norm(text: str | None) -> str:
    return (text or "").strip().lower()


def _deaccent(text: str) -> str:
    # Fast path: most Lab fields (slugs, tags, semantic_types, ASCII names)
    # have no accented characters, so skip normalize entirely.
    if text.isascii():
        return text
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


# Per-search cache: field value → word list. Passed down through
# search_index → _score_entity → _token_in_words so each distinct field
# string is tokenized once per query, not once per (token × entity).
_WordCache = dict[str, list[str]]


def _field_words(value: str | None, cache: _WordCache | None = None) -> list[str]:
    """Lowercase alphanumeric words; underscores/punctuation are separators.

    Makes snake_case slugs word-matchable: "anac_bandi_gara" →
    ["anac", "bandi", "gara"]. When a cache dict is supplied, repeated
    values reuse the precomputed word list.
    """
    if value is None:
        return []
    if cache is not None and value in cache:
        return cache[value]
    v = _deaccent(_norm(value))
    words = [w for w in re.split(r"[^a-z0-9]+", v) if w]
    if cache is not None:
        cache[value] = words
    return words


def _tokenize_query(query: str) -> list[str]:
    """Query tokens minus Italian stopwords (order preserved, deduped)."""
    tokens: list[str] = []
    for word in _field_words(query):
        if len(word) < 2 or word in _STOPWORDS:
            continue
        if word not in tokens:
            tokens.append(word)
    return tokens


def _word_forms(word: str) -> set[str]:
    """Word plus a light morphological variant (final-vowel strip).

    Covers common Italian singular/plural and gender pairs without a full
    stemmer: gara≈gare, pubblico≈pubblici, appalto≈appalti.
    """
    forms = {word}
    if len(word) >= 4 and word[-1] in "aeiou":
        forms.add(word[:-1])
    return forms


def _token_in_words(token: str, words: list[str], token_forms: set[str]) -> bool:
    """Match a query token against a pre-tokenized field (word list).

    Uses a token_forms set computed once per query — the common case, since
    the same token is tested against every field of every entity.
    """
    for field_word in words:
        if _word_forms(field_word) & token_forms:
            return True
        if len(token) >= 3 and field_word.startswith(token):
            return True
    return False


def _score_entity(
    query: str,
    fields: list[tuple[str | None, str]],
    word_cache: _WordCache | None = None,
) -> int:
    """Score an entity against a query. fields: (value, weight_name).

    Tiers:
      1. exact whole-query match on a field
      2. whole-query substring on a field
      3. all non-stopword tokens match across the entity's fields (cross-field
         AND) — natural-language queries like "appalti pubblici anac"
    """
    q = _norm(query)
    if not q:
        return 0
    score = 0
    # Length guards: a 1-char query ("e") is a substring of almost everything;
    # exact match needs at least 2 chars to be meaningful.
    for value, kind in fields:
        v = _norm(value)
        if not v:
            continue
        if len(q) >= 2 and v == q:
            score = max(score, _EXACT_WEIGHTS.get(kind, 20))
        elif len(q) >= 3 and q in v:
            score = max(score, _SUBSTR_WEIGHTS.get(kind, 15))

    tokens = _tokenize_query(query)
    if not tokens:
        return score
    token_forms_list = [_word_forms(t) for t in tokens]
    # Tokenize each field once per entity (not once per token×field). Empty
    # fields are dropped so they are never re-scanned. The shared word_cache
    # dedupes identical field values across entities in one search.
    prepared = []
    for value, kind in fields:
        if not value:
            continue
        words = _field_words(value, word_cache)
        if words:
            prepared.append((kind, words))
    matched_kinds: set[str] = set()
    all_matched = True
    for tok, tok_forms in zip(tokens, token_forms_list):
        token_hit = False
        for kind, words in prepared:
            if _token_in_words(tok, words, tok_forms):
                matched_kinds.add(kind)
                token_hit = True
        if not token_hit:
            all_matched = False
            break
    if all_matched and matched_kinds:
        best = max(_FIELD_TOKEN_WEIGHTS.get(kind, 18) for kind in matched_kinds)
        score = max(score, best)
    return score


def _columns_index_text(columns: list[dict[str, Any]] | None) -> tuple[str, str]:
    """Join column names+descriptions and semantic types for search indexing.

    The topic_index already carries full column defs (~6k across the Lab);
    indexing them lets find() answer "which dataset has a CIG column?"
    without a separate pipeline.
    """
    if not columns:
        return "", ""
    names = " ".join(
        filter(
            None,
            (str(c.get("name") or "") for c in columns),
        )
    )
    descs = " ".join(
        filter(
            None,
            (str(c.get("description") or "") for c in columns),
        )
    )
    sem = " ".join(
        filter(
            None,
            (str(c.get("semantic_type") or "") for c in columns),
        )
    )
    return f"{names} {descs}".strip(), sem


def search_index(
    topic_index: dict[str, Any],
    query: str,
    entity_type: str | None = None,
    domain: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Ranked cross-entity search over a topic_index dict.

    entity_type: dataset | repo | domain | source | analysis | None (all)
    Returns items: {type, slug, name, parent_repo, parent_domain, score, stage?}
    """
    results: list[dict[str, Any]] = []
    type_filter = entity_type.lower() if entity_type else None
    # Shared word cache for the whole query: identical field values (e.g.
    # source_id "anac" repeated across 9 datasets) tokenize once.
    word_cache: _WordCache = {}

    by_slug = topic_index.get("by_slug") or {}
    datasets_by_source = topic_index.get("datasets") or {}

    # Datasets — full entries carry tags/description (the main discovery
    # fields); by_slug meta enriches with registry_source and domain. Both
    # are built from the same flat list in render_topic_index, so by_slug
    # never holds slugs missing from datasets.
    dataset_entries: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    for ds in flatten_datasets(datasets_by_source):
        slug = ds.get("slug")
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        meta = by_slug.get(slug) or {}
        dataset_entries.append(
            {
                "slug": slug,
                "name": ds.get("name") or slug,
                "stage": ds.get("stage") or meta.get("stage"),
                "source_id": ds.get("source_id") or meta.get("source_id"),
                "registry_source": ds.get("registry_source") or meta.get("registry_source") or "",
                "domain": meta.get("domain"),
                "category": ds.get("category") or meta.get("category"),
                "tags": ds.get("tags") or meta.get("tags"),
                "description": ds.get("description") or meta.get("description"),
                "columns": ds.get("columns") or meta.get("columns"),
            }
        )

    if type_filter in (None, "dataset", "datasets"):
        for ds in dataset_entries:
            col_text, col_sem = _columns_index_text(ds.get("columns"))
            score = _score_entity(
                query,
                [
                    (ds.get("slug"), "slug"),
                    (ds.get("name"), "name"),
                    (ds.get("source_id"), "tag"),
                    (ds.get("category"), "tag"),
                    (ds.get("registry_source"), "tag"),
                    (ds.get("description"), "text"),
                    (" ".join(ds.get("tags") or []), "tag"),
                    # Column schema already lives in the artifact — names +
                    # descriptions as discovery text, semantic_type as a
                    # structured tag ("cig_code" → query "cig").
                    (col_text, "text"),
                    (col_sem, "tag"),
                ],
                word_cache,
            )
            if score <= 0:
                continue
            d_domain = ds.get("domain")
            if domain and d_domain != domain:
                continue
            results.append(
                {
                    "type": "dataset",
                    "slug": ds.get("slug"),
                    "name": ds.get("name"),
                    "parent_repo": ds.get("registry_source") or "",
                    "parent_domain": d_domain,
                    "stage": ds.get("stage"),
                    "score": score,
                }
            )

    if type_filter in (None, "repo", "repos"):
        for repo, card in (topic_index.get("repos") or {}).items():
            if not isinstance(card, dict):
                continue
            score = _score_entity(
                query,
                [
                    (repo, "slug"),
                    (card.get("description"), "text"),
                    (card.get("role"), "tag"),
                    (card.get("domain"), "tag"),
                ],
                word_cache,
            )
            if score <= 0:
                continue
            if domain and card.get("domain") != domain:
                continue
            results.append(
                {
                    "type": "repo",
                    "slug": repo,
                    "name": repo,
                    "parent_repo": repo,
                    "parent_domain": card.get("domain"),
                    "role": card.get("role"),
                    "n_datasets": card.get("n_datasets"),
                    "score": score,
                }
            )

    if type_filter in (None, "domain", "domains", "theme", "themes"):
        domains = topic_index.get("domains") or topic_index.get("explorer_themes") or []
        for dom in domains:
            if not isinstance(dom, dict):
                continue
            score = _score_entity(
                query,
                [
                    (dom.get("slug"), "slug"),
                    (dom.get("name"), "name"),
                    (dom.get("description"), "text"),
                ],
                word_cache,
            )
            if score <= 0:
                continue
            if domain and dom.get("slug") != domain:
                continue
            results.append(
                {
                    "type": "domain",
                    "slug": dom.get("slug"),
                    "name": dom.get("name"),
                    "parent_repo": "",
                    "parent_domain": dom.get("slug"),
                    "dataset_count": dom.get("dataset_count") or len(dom.get("datasets") or []),
                    "score": score,
                }
            )

    if type_filter in (None, "analysis", "analyses"):
        for a in topic_index.get("analyses") or []:
            score = _score_entity(
                query,
                [
                    (a.get("slug"), "slug"),
                    (a.get("name"), "name"),
                    (a.get("description"), "text"),
                    (a.get("source"), "tag"),
                ],
                word_cache,
            )
            if score <= 0:
                continue
            results.append(
                {
                    "type": "analysis",
                    "slug": a.get("slug"),
                    "name": a.get("name"),
                    "parent_repo": "",
                    "parent_domain": None,
                    "datasets": a.get("datasets") or [],
                    "score": score,
                }
            )

    if type_filter in (None, "source", "sources"):
        source_counts: dict[str, int] = {}
        for ds in dataset_entries:
            sid = ds.get("source_id") or ""
            if sid:
                source_counts[sid] = source_counts.get(sid, 0) + 1
        for sid, count in source_counts.items():
            score = _score_entity(query, [(sid, "slug")], word_cache)
            if score <= 0:
                continue
            results.append(
                {
                    "type": "source",
                    "slug": sid,
                    "name": sid,
                    "parent_repo": "",
                    "parent_domain": None,
                    "n_datasets": count,
                    "score": score,
                }
            )

    results.sort(key=lambda r: (-r.get("score", 0), r.get("slug") or ""))
    return results[: max(limit, 1)]


def lab_map_summary(topic_index: dict[str, Any], domain: str | None = None) -> dict[str, Any]:
    """Compact navigation tree: domains → repos → sources."""
    repos = topic_index.get("repos") or {}
    domains = topic_index.get("domains") or []
    by_domain = topic_index.get("by_domain") or {}
    by_repo = topic_index.get("by_repo") or {}
    by_slug = topic_index.get("by_slug") or {}

    total_datasets = (
        sum(len(v) for v in by_repo.values()) if by_repo else sum(len(v) for v in by_slug.values())
    )
    published = sum(1 for m in by_slug.values() if m.get("stage") == "published")

    domain_nodes: list[dict[str, Any]] = []
    selected = [d for d in domains if not domain or d.get("slug") == domain]
    for d in selected:
        domain_nodes.append(
            {
                "slug": d.get("slug"),
                "name": d.get("name"),
                "description": d.get("description", ""),
                "dataset_count": d.get("dataset_count") or len(d.get("datasets") or []),
                "repos": d.get("repos") or [],
            }
        )

    repo_nodes: list[dict[str, Any]] = []
    for name, card in sorted(repos.items()):
        if not isinstance(card, dict):
            continue
        if domain and card.get("domain") != domain:
            continue
        repo_nodes.append(
            {
                "repo": name,
                "role": card.get("role"),
                "domain": card.get("domain"),
                "n_datasets": card.get("n_datasets") or len(by_repo.get(name) or []),
                "n_published": card.get("n_published", 0),
                "sources": card.get("sources") or [],
            }
        )

    sources: dict[str, int] = {}
    if domain:
        ds_slugs = by_domain.get(domain) or []
        for slug in ds_slugs:
            sid = (by_slug.get(slug) or {}).get("source_id") or ""
            if sid:
                sources[sid] = sources.get(sid, 0) + 1
    else:
        for meta in by_slug.values():
            sid = meta.get("source_id") or ""
            if sid:
                sources[sid] = sources.get(sid, 0) + 1

    return {
        "domains": domain_nodes,
        "repos": repo_nodes,
        "sources": dict(sorted(sources.items(), key=lambda x: (-x[1], x[0]))),
        "totals": {
            "repos": len(repo_nodes),
            "domains": len(domain_nodes),
            "datasets": total_datasets,
            "published": published,
            "analyses": len(topic_index.get("analyses") or []),
        },
        "ok": True,
    }


def explore_ref(topic_index: dict[str, Any], ref: str) -> dict[str, Any]:
    """Resolve any Lab entity (repo, dataset, domain, source, analysis)."""
    ref_l = (ref or "").strip().lower()
    result: dict[str, Any] = {"ref": ref, "found": False}

    by_slug = topic_index.get("by_slug") or {}
    datasets_by_source = topic_index.get("datasets") or {}
    repos = topic_index.get("repos") or {}
    domains = topic_index.get("domains") or topic_index.get("explorer_themes") or []
    analyses = topic_index.get("analyses") or []
    analyses_by_dataset = topic_index.get("analyses_by_dataset") or {}
    by_repo = topic_index.get("by_repo") or {}
    by_domain = topic_index.get("by_domain") or {}

    def _full_entry(slug: str) -> dict[str, Any] | None:
        # by_slug ha la anagrafica; columns/location vivono solo nella voce
        # completa di datasets (una scan per explore è accettabile: ~400 voci).
        for ds in flatten_datasets(datasets_by_source):
            if ds.get("slug") == slug:
                return ds
        return None

    def _dataset_card(slug: str) -> dict[str, Any] | None:
        # by_slug is always built from the same flat list as datasets
        # (render_topic_index) — no scan fallback needed.
        meta = by_slug.get(slug)
        if meta is None:
            return None
        card: dict[str, Any] = {
            "type": "dataset",
            "slug": slug,
            "name": meta.get("name") or slug,
            "stage": meta.get("stage"),
            "source_id": meta.get("source_id"),
            "registry_source": meta.get("registry_source") or "",
            "domain": meta.get("domain"),
            "category": meta.get("category"),
            "analyses": analyses_by_dataset.get(slug) or [],
        }
        # Enrichment per il passaggio explore → gcs_query: schema compatto e
        # path pubblico + hint d'uso (backward-compatible, campi additivi).
        full = _full_entry(slug) or {}
        columns = full.get("columns") or []
        if columns:
            card["columns"] = [
                {"name": c.get("name"), "type": c.get("type"), "role": c.get("role")}
                for c in columns
                if isinstance(c, dict)
            ]
        gcs_path = (full.get("location") or {}).get("path") or ""
        if gcs_path:
            card["gcs_path"] = gcs_path
            card["query_hint"] = (
                f'gcs_query(slug="{slug}", sql="SELECT * FROM _data LIMIT 10")'
            )
        return card

    # Dataset
    ds_card = _dataset_card(ref)
    if ds_card is not None:
        result.update(ds_card)
        result["found"] = True
        repo = ds_card.get("registry_source") or ""
        if repo and repo in repos:
            card = repos[repo]
            result["repo"] = {
                "name": repo,
                "role": card.get("role") if isinstance(card, dict) else None,
                "domain": card.get("domain") if isinstance(card, dict) else None,
                "url": card.get("url") if isinstance(card, dict) else "",
            }
        return result

    # Repo
    repo_key = ref if ref in repos else None
    if repo_key is None:
        for name in repos:
            if name.lower() == ref_l:
                repo_key = name
                break
    if repo_key is not None:
        card = repos[repo_key] if isinstance(repos[repo_key], dict) else {}
        ds_slugs = by_repo.get(repo_key) or []
        result.update(
            {
                "type": "repo",
                "slug": repo_key,
                "name": repo_key,
                "role": card.get("role"),
                "domain": card.get("domain"),
                "description": card.get("description", ""),
                "url": card.get("url", ""),
                "n_datasets": card.get("n_datasets") or len(ds_slugs),
                "n_published": card.get("n_published", 0),
                "sources": card.get("sources") or [],
                "datasets": ds_slugs[:50],
                "found": True,
            }
        )
        return result

    # Domain
    domain_key = None
    for d in domains:
        slug = d.get("slug") if isinstance(d, dict) else None
        if slug and slug.lower() == ref_l:
            domain_key = d
            break
    if domain_key is not None:
        slug = domain_key.get("slug")
        ds_slugs = domain_key.get("datasets") or by_domain.get(slug) or []
        result.update(
            {
                "type": "domain",
                "slug": slug,
                "name": domain_key.get("name"),
                "description": domain_key.get("description", ""),
                "repos": domain_key.get("repos") or [],
                "datasets": ds_slugs[:50],
                "dataset_count": domain_key.get("dataset_count") or len(ds_slugs),
                "found": True,
            }
        )
        return result

    # Source (source_id)
    source_ds: list[str] = []
    seen_source_ds: set[str] = set()
    for source_key, items in datasets_by_source.items():
        if source_key.lower() == ref_l:
            for item in items or []:
                slug = item.get("slug")
                if slug and slug not in seen_source_ds:
                    seen_source_ds.add(slug)
                    source_ds.append(slug)
            result.update(
                {
                    "type": "source",
                    "slug": source_key,
                    "name": source_key,
                    "n_datasets": len(source_ds),
                    "datasets": sorted(source_ds)[:50],
                    "found": True,
                }
            )
            return result
    for slug, meta in by_slug.items():
        sid = meta.get("source_id") or ""
        if sid and sid.lower() == ref_l and slug not in seen_source_ds:
            seen_source_ds.add(slug)
            source_ds.append(slug)
    if source_ds:
        result.update(
            {
                "type": "source",
                "slug": ref,
                "name": ref,
                "n_datasets": len(source_ds),
                "datasets": sorted(source_ds)[:50],
                "found": True,
            }
        )
        return result

    # Analysis
    for a in analyses:
        if (a.get("slug") or "").lower() == ref_l:
            result.update(
                {
                    "type": "analysis",
                    "slug": a.get("slug"),
                    "name": a.get("name"),
                    "status": a.get("status"),
                    "description": a.get("description", ""),
                    "source": a.get("source", ""),
                    "period": a.get("period"),
                    "datasets": a.get("datasets") or [],
                    "found": True,
                }
            )
            return result

    return result
