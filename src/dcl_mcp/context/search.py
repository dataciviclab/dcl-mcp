"""Query sul topic_index (schema v7) — search, lab_map, explore.

Funzioni pure su dict plain: zero I/O, zero tipi del builder ACB.
Spostate da ``agent_context_builder.navigation`` con la migrazione
del MCP nel repo dcl-mcp (ACB tiene solo i build_*).
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from dcl_mcp.provenance import dataset_snapshot

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
        # forme articolate mancanti: "personale della scuola" tornava 0 perché
        # "della" non era una stopword (audit search log, 2026-11-23).
        "dalla",
        "dallo",
        "della",
        "alla",
        "nella",
        "nelle",
        "sulla",
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

# Sotto questa soglia il match è rumore (es. un solo token su tre, trovato in
# una descrizione di colonna). Meglio zero risultati con un hint che dieci
# risultati casuali: l'agente non può distinguerli.
MIN_SCORE = 12

# Sinonimi/acronimi del vocabolario del Lab. Curati a mano, non un dizionario:
# servono a far combaciare le parole che un agente usa con quelle del catalogo
# ("disabilità" ↔ "disabili", "fondi europei" ↔ "eu").
_ALIASES: dict[str, tuple[str, ...]] = {
    "disabilita": ("disabili", "invalidi"),
    "disabile": ("disabili",),
    "invalidi": ("disabili",),
    "europei": ("eu", "ue"),
    "europee": ("eu", "ue"),
    "europeo": ("eu", "ue"),
    "europa": ("ue", "eu"),
    "fondi": ("finanziamenti", "grant"),
    "finanziamenti": ("fondi", "grant"),
    "contributi": ("aiuti", "grant"),
    "aiuti": ("contributi",),
    "imprese": ("aziende", "societa"),
    "aziende": ("imprese",),
    "scuole": ("scuola",),
    "lavoratori": ("lavoro", "dipendenti"),
    "occupati": ("occupazione",),
    "disoccupati": ("disoccupazione",),
    "stipendi": ("retribuzioni", "salari"),
    "salari": ("retribuzioni",),
    "sanita": ("sanitario", "ssn"),
    "ssn": ("sanitario",),
    "concorsi": ("reclutamento", "inpa"),
    "benzina": ("carburanti",),
    "auto": ("veicoli", "autovetture"),
}

# Tipi di entità ricercabili: unica fonte per search_index e per la
# validazione di find(type=). I plurali e "theme" sono alias storici.
ENTITY_TYPES: tuple[str, ...] = ("dataset", "repo", "domain", "source", "analysis")
_TYPE_ALIASES = {
    "datasets": "dataset",
    "repos": "repo",
    "domains": "domain",
    "sources": "source",
    "analyses": "analysis",
    "theme": "domain",
    "themes": "domain",
}


def normalize_entity_type(value: str | None) -> str | None:
    """Normalizza ``type=`` in uno di ``ENTITY_TYPES`` (None se assente o ignoto)."""
    if not value:
        return None
    candidate = value.strip().lower()
    candidate = _TYPE_ALIASES.get(candidate, candidate)
    return candidate if candidate in ENTITY_TYPES else None


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


# Field value → word list. **Persistente tra query**: le stringhe dei campi
# (slug, tag, descrizioni, nomi colonna) sono content-addressed, quindi la
# stessa stringa dà sempre le stesse parole. Prima era per-query e ogni
# chiamata ri-tokenizzava 547 dataset × 9 campi (~6.000 colonne): 150 ms.
_WordCache = dict[str, list[str]]
_WORD_CACHE: _WordCache = {}
_WORD_CACHE_MAX = 200_000
# value → (words, forme plurali/singolari). Evita di ricreare le forme per ogni
# (token × parola × campo): era il costo residuo di `find` (~100 ms).
_FIELD_FORMS: dict[str, tuple[list[str], frozenset[str]]] = {}


def _field_index(value: str, cache: _WordCache | None = None) -> tuple[list[str], frozenset[str]]:
    """Parole del campo + unione delle loro forme, calcolate una volta sola."""
    hit = _FIELD_FORMS.get(value)
    if hit is not None:
        return hit
    words = _field_words(value, cache)
    forms = frozenset(form for word in words for form in _word_forms(word))
    if len(_FIELD_FORMS) > _WORD_CACHE_MAX:
        _FIELD_FORMS.clear()
    entry = (words, forms)
    _FIELD_FORMS[value] = entry
    return entry


def _value_matches(
    token: str, token_forms: set[str], value: str, cache: _WordCache | None = None
) -> bool:
    """Il token compare nel valore? Forme complete, poi prefisso di parola."""
    words, forms = _field_index(value, cache)
    if token_forms & forms:
        return True
    return len(token) >= 3 and any(word.startswith(token) for word in words)


def _field_words(
    value: str | None, cache: _WordCache | None = None
) -> list[str]:
    """Lowercase alphanumeric words; underscores/punctuation are separators.

    Makes snake_case slugs word-matchable: "anac_bandi_gara" →
    ["anac", "bandi", "gara"]. When a cache dict is supplied, repeated
    values reuse the precomputed word list.
    """
    if value is None:
        return []
    if cache is None:
        cache = _WORD_CACHE
        if len(cache) > _WORD_CACHE_MAX:  # guardia: non cresce senza limite
            cache.clear()
    if value in cache:
        return cache[value]
    v = _deaccent(_norm(value))
    words = [w for w in re.split(r"[^a-z0-9]+", v) if w]
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


def _query_forms(token: str) -> set[str]:
    """Forme morfologiche del token + alias curati del vocabolario del Lab."""
    forms = _word_forms(token)
    for alias in _ALIASES.get(token, ()):
        forms |= _word_forms(alias)
    return forms


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


def _phrase_hit(phrase: str, value: str) -> bool:
    """Substring ai confini di parola: "cup" non matcha "occupazione"."""
    start = value.find(phrase)
    while start != -1:
        before = value[start - 1] if start else " "
        after_at = start + len(phrase)
        after = value[after_at] if after_at < len(value) else " "
        if not before.isalnum() and not after.isalnum():
            return True
        start = value.find(phrase, start + 1)
    return False


def _idf_weights(
    entries: list[dict[str, Any]],
    tokens: list[str],
    word_cache: _WordCache,
) -> dict[str, float]:
    """Peso dei token dalla frequenza nel catalogo (IDF, lisciato).

    Senza questo, "italiano" (3 dataset) obbligava la ricerca quanto "cup" (16).
    Ritorna un peso in [1, ~7]: parole rare pesano di più.
    """
    if not tokens or not entries:
        return {token: 1.0 for token in tokens}
    doc_freq = dict.fromkeys(tokens, 0)
    forms_by_token = {token: _word_forms(token) for token in tokens}
    for entry in entries:
        values = [value for value, _kind in dataset_fields(entry) if value]
        for token in tokens:
            if any(_value_matches(token, forms_by_token[token], value, word_cache) for value in values):
                doc_freq[token] += 1
    n = len(entries)
    return {
        token: 1.0 + math.log((n + 1) / (doc_freq[token] + 1)) for token in tokens
    }


def _score_entity(
    query: str,
    fields: list[tuple[str | None, str]],
    word_cache: _WordCache | None = None,
    idf: dict[str, float] | None = None,
) -> int:
    """Score an entity against a query. fields: (value, weight_name).

    Tre componenti **sommate** (prima si prendeva il massimo, e un substring
    accidentale pareggiava un match coerente):

      1. match esatto dell'intera query su un campo;
      2. occorrenza dell'intera query ai confini di parola;
      3. copertura dei token, con credito parziale e peso IDF: 2 token su 3
         valgono ~0,44 del pieno, non zero.
    """
    q = _norm(query)
    if not q:
        return 0

    exact = 0
    phrase = 0
    for value, kind in fields:
        v = _norm(value)
        if not v:
            continue
        if len(q) >= 2 and v == q:
            exact = max(exact, _EXACT_WEIGHTS.get(kind, 20))
        elif len(q) >= 3 and _phrase_hit(q, v):
            phrase = max(phrase, _SUBSTR_WEIGHTS.get(kind, 15))

    token_score = 0.0
    tokens = _tokenize_query(query)
    if tokens:
        matched = 0
        weight_sum = 0.0
        kinds: dict[str, int] = {}
        for token in tokens:
            forms = _query_forms(token)
            hit_kinds = {
                kind
                for value, kind in fields
                if value and _value_matches(token, forms, value, word_cache)
            }
            if hit_kinds:
                matched += 1
                weight_sum += (idf or {}).get(token, 1.0)
                for kind in hit_kinds:
                    kinds[kind] = kinds.get(kind, 0) + 1
        if matched:
            coverage = matched / len(tokens)
            base = max(_FIELD_TOKEN_WEIGHTS.get(kind, 18) for kind in kinds)
            # Peso medio su TUTTI i token (non solo quelli trovati): un match
            # parziale non deve valere quanto uno completo. Esponente 3:
            # 2/3 → ~0,20 del pieno.
            avg_idf = weight_sum / len(tokens)
            token_score = base * (coverage**3) * avg_idf
            if matched == len(tokens) and len(tokens) > 1:
                # copertura piena su più token: premia la coerenza (non su una
                # query di un solo token, dove il bonus appiattisce tutto)
                token_score += 20.0

    return int(round(min(exact + 0.6 * phrase + token_score, 400)))


def _matched_fields(query: str, fields: list[tuple[str | None, str]]) -> list[str]:
    """Quali campi hanno fatto match (per rendere la risposta spiegabile)."""
    tokens = _tokenize_query(query)
    if not tokens:
        return []
    found: list[str] = []
    for value, kind in fields:
        if not value:
            continue
        if any(_value_matches(token, _query_forms(token), value) for token in tokens):
            if kind not in found:
                found.append(kind)
    return found


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


def _period_years(period: Any) -> tuple[int | None, int | None]:
    """Anni da un `period` dell'artifact (dict o stringa "2018–2025")."""
    if isinstance(period, dict):
        return period.get("start"), period.get("end")
    text = str(period or "")
    match = re.search(r"((?:19|20)\d{2})\D+((?:19|20)\d{2})", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    single = re.search(r"((?:19|20)\d{2})", text)
    if single:
        year = int(single.group(1))
        return year, year
    return None, None


def _column_card(column: dict[str, Any]) -> dict[str, Any]:
    """Colonna come la vede l'agente: nome, tipo, ruolo e — se c'è — semantica.

    `semantic_type` esiste nell'artifact (`municipality_name`, `cup_code`, …) e
    dice a cosa serve la colonna: senza, un agente deve interrogare il dataset
    per scoprire che `fin_comune` è un codice DOUBLE e `comune` è il nome
    (misurato: 83 `gcs_query` e timeout su una domanda PNRR).
    """
    card: dict[str, Any] = {
        "name": column.get("name"),
        "type": column.get("type"),
        "role": column.get("role"),
    }
    if column.get("semantic_type"):
        card["semantic_type"] = column["semantic_type"]
    description = (column.get("description") or "").strip()
    if description:
        card["description"] = description if len(description) <= 80 else description[:77] + "..."
    return card


def dataset_fields(ds: dict[str, Any]) -> list[tuple[str | None, str]]:
    """Campi su cui `find` cerca un dataset (unica definizione)."""
    col_text, col_sem = _columns_index_text(ds.get("columns"))
    return [
        (ds.get("slug"), "slug"),
        (ds.get("name"), "name"),
        (ds.get("source_id"), "tag"),
        (ds.get("category"), "tag"),
        (ds.get("registry_source"), "tag"),
        (ds.get("description"), "text"),
        (" ".join(ds.get("tags") or []), "tag"),
        (col_text, "text"),
        (col_sem, "tag"),
    ]


def dataset_matched_fields(entry: dict[str, Any], query: str) -> list[str]:
    """Quali campi hanno fatto match, per rendere il risultato spiegabile."""
    return _matched_fields(query, dataset_fields(entry))[:4]


def _dataset_decision_fields(ds: dict[str, Any]) -> dict[str, Any]:
    """Campi che servono a scegliere tra candidati senza aprire la card.

    `find` restituiva solo slug/nome/score: per distinguere due dataset serviva
    un `explore` per candidato (audit: 34–74 explore per task). Qui passano le
    informazioni che l'agente usa davvero per decidere.
    """
    description = (ds.get("description") or "").strip()
    period = ds.get("period") or {}
    fields: dict[str, Any] = {
        "n_columns": len(ds.get("columns") or []),
        "n_marts": len(ds.get("mart_refs") or []),
        "snapshot_year": dataset_snapshot(ds)["snapshot_year"],
    }
    if description:
        fields["snippet"] = description if len(description) <= 160 else description[:157] + "..."
    if period.get("start") or period.get("end"):
        fields["years"] = {"start": period.get("start"), "end": period.get("end")}
    return fields


def search_index(
    topic_index: dict[str, Any],
    query: str,
    entity_type: str | None = None,
    domain: str | None = None,
    limit: int | None = 10,
) -> list[dict[str, Any]]:
    """Ranked cross-entity search over a topic_index dict.

    entity_type: dataset | repo | domain | source | analysis | None (all)
    Returns items: {type, slug, name, parent_repo, parent_domain, score, gcs_available?}

    ``limit=None`` restituisce **tutti** i match: serve a ``find`` per
    riportare il totale reale prima di tagliare (prima ``total_catalog``
    era ``len(results)``, quindi al massimo ``limit``).
    """
    results: list[dict[str, Any]] = []
    type_filter = normalize_entity_type(entity_type)
    # Cache persistente delle parole (vedi _WORD_CACHE): le stringhe dei campi
    # sono le stesse a ogni query finché l'artifact non cambia.
    word_cache: _WordCache = _WORD_CACHE

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
                "source_id": ds.get("source_id") or meta.get("source_id"),
                "registry_source": ds.get("registry_source") or meta.get("registry_source") or "",
                "domain": meta.get("domain"),
                "category": ds.get("category") or meta.get("category"),
                "tags": ds.get("tags") or meta.get("tags"),
                "description": ds.get("description") or meta.get("description"),
                "columns": ds.get("columns") or meta.get("columns"),
                # Campi per far decidere l'agente senza un `explore` per
                # candidato (audit: 74 explore su 128 chiamate in D01).
                "period": ds.get("period"),
                "mart_refs": ds.get("mart_refs"),
                "location": ds.get("location"),
            }
        )

    tokens = _tokenize_query(query)
    idf = _idf_weights(dataset_entries, tokens, word_cache)

    if type_filter in (None, "dataset"):
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
                idf,
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
                    "gcs_available": bool((ds.get("location") or {}).get("path")),
                    "score": score,
                    **_dataset_decision_fields(ds),
                }
            )

    if type_filter in (None, "repo"):
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
                idf,
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

    if type_filter in (None, "domain"):
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
                idf,
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

    if type_filter in (None, "analysis"):
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
                idf,
            )
            if score <= 0:
                continue
            # Il dominio di un'analisi è quello dei suoi dataset collegati
            # (by_slug). Senza questo, il filtro domain= risultava inutile
            # sulle analysis — falso senso di sicurezza (audit giro 3).
            a_domains = {
                (by_slug.get(ds) or {}).get("domain")
                for ds in (a.get("datasets") or [])
                if (by_slug.get(ds) or {}).get("domain")
            }
            if domain and domain not in a_domains:
                continue
            results.append(
                {
                    "type": "analysis",
                    "slug": a.get("slug"),
                    "name": a.get("name"),
                    "parent_repo": "",
                    # Dominio primario dai dataset collegati; None se
                    # l'analisi attraversa più domini.
                    "parent_domain": next(iter(a_domains)) if len(a_domains) == 1 else None,
                    "datasets": a.get("datasets") or [],
                    "score": score,
                }
            )

    if type_filter in (None, "source"):
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

    results = [r for r in results if r.get("score", 0) >= MIN_SCORE]
    results.sort(key=lambda r: (-r.get("score", 0), r.get("slug") or ""))
    if limit is None:
        return results
    return results[: max(limit, 1)]


def dataset_listing(
    topic_index: dict[str, Any],
    domain: str | None = None,
    signals: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Una riga per dataset: quanto basta a rispondere a domande di catalogo.

    Esiste per l'enumerazione. Senza, l'unico modo per vedere tutti i dataset è
    un `explore` per ciascuno: nell'audit la domanda "quali dataset sono
    obsoleti" è costata 1.516–2.606 chiamate con esito non deterministico.
    547 righe costano ~70 KB: una chiamata sola.
    """
    rows: list[dict[str, Any]] = []
    for ds in flatten_datasets(topic_index.get("datasets") or {}):
        meta = (topic_index.get("by_slug") or {}).get(ds["slug"]) or {}
        if domain and (meta.get("domain") or ds.get("domain")) != domain:
            continue
        period = ds.get("period") or {}
        row: dict[str, Any] = {
            "slug": ds["slug"],
            "name": ds.get("name") or ds["slug"],
            "years": {"start": period.get("start"), "end": period.get("end")},
            "gcs_available": bool((ds.get("location") or {}).get("path")),
            "snapshot_year": dataset_snapshot(ds)["snapshot_year"],
            "n_columns": len(ds.get("columns") or []),
        }
        update = (signals or {}).get(ds["slug"])
        if update:
            row["last_run"] = update.get("last_run")
            row["run_year"] = update.get("run_year")
        rows.append(row)
    rows.sort(key=lambda row: row["slug"])
    return rows


def lab_map_summary(topic_index: dict[str, Any], domain: str | None = None) -> dict[str, Any]:
    """Compact navigation tree: domains → repos → sources."""
    repos = topic_index.get("repos") or {}
    domains = topic_index.get("domains") or []
    by_domain = topic_index.get("by_domain") or {}
    by_repo = topic_index.get("by_repo") or {}
    by_slug = topic_index.get("by_slug") or {}

    # Accessibilità reale: `stage` era editoriale e stale (33 "published" su 547,
    # mentre 536 hanno un parquet leggibile). Qui conta il parquet.
    with_gcs = {
        ds["slug"]
        for ds in flatten_datasets(topic_index.get("datasets") or {})
        if ds.get("slug") and (ds.get("location") or {}).get("path")
    }

    # Con domain= i totali sono quelli del dominio: prima restavano globali
    # (1 dominio, 6 repo, ma "datasets: 468") e si contraddicevano con la
    # vista filtrata.
    domain_slugs = (by_domain.get(domain) or []) if domain else []
    if domain:
        total_datasets = len(domain_slugs)
        accessible = sum(1 for s in domain_slugs if s in with_gcs)
        analyses_by_dataset = topic_index.get("analyses_by_dataset") or {}
        n_analyses = len(
            {a for s in domain_slugs for a in (analyses_by_dataset.get(s) or [])}
        )
    else:
        total_datasets = (
            sum(len(v) for v in by_repo.values())
            if by_repo
            else sum(len(v) for v in by_slug.values())
        )
        accessible = sum(1 for s in by_slug if s in with_gcs)
        n_analyses = len(topic_index.get("analyses") or [])

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
                "n_with_gcs": sum(
                    1 for s in (by_repo.get(name) or []) if s in with_gcs
                ),
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
            "with_gcs": accessible,
            "analyses": n_analyses,
        },
        "ok": True,
    }


# Prefissi tipizzati per explore: "analysis:rifiuti-urbani" forza il tipo
# quando slug di repo/analysis collidono (contratto ref, backward-compatible).
_REF_TYPE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("dataset:", "dataset"),
    ("repo:", "repo"),
    ("domain:", "domain"),
    ("source:", "source"),
    ("analysis:", "analysis"),
)


def explore_ref(topic_index: dict[str, Any], ref: str) -> dict[str, Any]:
    """Resolve any Lab entity (repo, dataset, domain, source, analysis).

    I ref possono essere tipizzati con prefisso (es. ``analysis:slug``);
    senza prefisso l'ordine di risoluzione è dataset → repo → domain →
    source → analysis (i repo vincono sulle analysis con stesso slug).
    Se non trovato, restituisce ``suggestions`` da search_index.
    """
    clean_ref = (ref or "").strip()
    ref_l = clean_ref.lower()
    forced: str | None = None
    for prefix, ftype in _REF_TYPE_PREFIXES:
        if ref_l.startswith(prefix):
            forced = ftype
            clean_ref = clean_ref[len(prefix) :].strip()
            ref_l = clean_ref.lower()
            break
    ref = clean_ref
    result: dict[str, Any] = {"ref": ref, "found": False}

    by_slug = topic_index.get("by_slug") or {}
    datasets_by_source = topic_index.get("datasets") or {}
    repos = topic_index.get("repos") or {}
    domains = topic_index.get("domains") or topic_index.get("explorer_themes") or []
    analyses = topic_index.get("analyses") or []
    analyses_by_dataset = topic_index.get("analyses_by_dataset") or {}
    by_repo = topic_index.get("by_repo") or {}
    by_domain = topic_index.get("by_domain") or {}

    # Indice delle voci complete + set dei dataset con parquet leggibile:
    # una scan per chiamata, riusata da card, conteggi e join.
    entry_by_slug = {
        ds["slug"]: ds for ds in flatten_datasets(datasets_by_source) if ds.get("slug")
    }
    with_gcs = {
        slug
        for slug, entry in entry_by_slug.items()
        if (entry.get("location") or {}).get("path")
    }

    def _list_block(slugs: list[str]) -> dict[str, Any]:
        """Lista con taglio dichiarato: prima 50 su 169 sembravano 50 in tutto."""
        return {
            "datasets": slugs[:50],
            "datasets_total": len(slugs),
            "truncated": len(slugs) > 50,
        }

    def _n_with_gcs(slugs: list[str]) -> int:
        return sum(1 for slug in slugs if slug in with_gcs)

    def _full_entry(slug: str) -> dict[str, Any] | None:
        # Indice costruito una volta per chiamata (prima era una scan per
        # ogni _full_entry: explore ne fa più di una).
        return entry_by_slug.get(slug)

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
            "source_id": meta.get("source_id"),
            "registry_source": meta.get("registry_source") or "",
            "domain": meta.get("domain"),
            "category": meta.get("category"),
            "analyses": analyses_by_dataset.get(slug) or [],
        }
        # Enrichment per il passaggio explore → gcs_query: campi che servono
        # "prima di scrivere SQL" (backward-compatible, additivi).
        full = _full_entry(slug) or {}
        # Provenienza: anno dello snapshot pubblicato, dal path della location.
        # Senza, l'agente non può dire se un dataset è aggiornato (R02: 1.772
        # chiamate per una risposta che il sistema non permetteva).
        card.update(dataset_snapshot(full))
        if full.get("description"):
            card["description"] = full["description"]
        # Provenienza della fonte (il campo `source` è nell'artifact ma non
        # veniva esposto) e pagina pubblica del dataset (`url_slug`).
        if full.get("source"):
            card["source"] = full["source"]
        if full.get("url_slug"):
            card["url"] = f"https://dataciviclab.github.io/data-explorer/dataset/{full['url_slug']}"
        # Accessibilità reale: `stage` non dice se il parquet si può leggere
        # (503 dataset `incubating` sono interrogabili). Questo sì.
        card["gcs_available"] = bool((full.get("location") or {}).get("path"))
        period = full.get("period") or {}
        if period.get("start") or period.get("end"):
            card["period"] = period
            card["year_min"] = period.get("start")
            card["year_max"] = period.get("end")
        # clean_rows è il campo del catalogo (righe dell'ultimo snapshot
        # clean pubblicato) — esposto col nome onesto, con disclaimer:
        # non è garantito sia la serie completa (audit: 7762 vs 114k di
        # #443). Per numeri citabili usare count(*) via gcs_query.
        if full.get("clean_rows"):
            card["clean_rows"] = full["clean_rows"]
            # Cosa significa il numero. Con topic_index v8 `clean_rows` è la
            # somma delle partizioni dichiarate (`partitions`): è il totale
            # pubblicato, non un singolo file. Con v7 era l'ultimo snapshot
            # (su un glob, l'ultimo anno: fino a 12× di scarto, audit F10).
            partitions = full.get("partitions") or {}
            if partitions:
                card["clean_rows_scope"] = "sum_of_partitions"
                card["clean_rows_note"] = (
                    f"Somma delle righe clean delle {len(partitions)} partizioni "
                    "pubblicate nel registry (campo `partitions`). Per un numero "
                    "verificabile usa il query_hint (count)."
                )
            else:
                card["clean_rows_scope"] = (
                    "last_year_of_multi_file" if card.get("multi_file") else "single_file"
                )
                card["clean_rows_note"] = (
                    "Righe dell'ultimo snapshot clean pubblicato dal producer "
                    "(registry senza `partitions`): non è garantito sia la serie "
                    "completa — per un numero verificabile usa il query_hint (count)."
                )
        marts = full.get("mart_refs") or []
        if marts:
            # Solo la lista: l'hint lo dà `explore(<mart>)`, con il layer
            # giusto. Un `marts_hint` qui duplicava `query_hint` puntando a
            # un'altra tabella.
            card["marts"] = marts
        columns = full.get("columns") or []
        if columns:
            card["columns"] = [_column_card(c) for c in columns if isinstance(c, dict)]
            card["n_columns"] = len(card["columns"])
        gcs_path = (full.get("location") or {}).get("path") or ""
        if gcs_path:
            card["gcs_path"] = gcs_path
            card["query_hint"] = (
                f'gcs_query(slug="{slug}", sql="SELECT count(*) AS n FROM _data")'
            )
        # Collegamenti: i due dataset più joinabili sono un assaggio; la lista
        # completa (con type_mismatch) è di join_suggestions, e `joins_total`
        # dice quanti candidati restano.
        try:
            from dcl_mcp.context.joins import join_suggestions as _join

            join_result = _join(topic_index, slug, limit=2)
        except Exception:  # un join mancante non deve rompere la card
            join_result = {}
        pairs = join_result.get("suggestions") or []
        if pairs:
            card["joins"] = [
                {
                    "slug": pair.get("slug"),
                    "keys": [key.get("type") for key in pair.get("on") or []],
                    "years_overlap": pair.get("years_overlap"),
                }
                for pair in pairs
            ]
            card["joins_total"] = join_result.get("total_candidates", len(pairs))
        return card

    # Dataset
    ds_card = _dataset_card(ref) if forced in (None, "dataset") else None
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

    # Mart (slug dataset__table, da mart_refs): collega al dataset padre.
    # L'audit giro 4 notava che marts_hint suggeriva uno slug che explore
    # stesso non risolvava — incoerenza chiusa qui.
    if forced in (None, "dataset") and "__" in ref:
        ds_part = ref.split("__", 1)[0]
        if ds_part in by_slug:
            base = _dataset_card(ds_part)
            if base is not None:
                full = _full_entry(ds_part) or {}
                known_marts = full.get("mart_refs") or []
                result.update(base)
                result.update(
                    {
                        "type": "mart",
                        "slug": ref,
                        "dataset": ds_part,
                        "found": True,
                    }
                )
                if ref not in known_marts:
                    result["note"] = (
                        f"'{ref}' non è nei mart noti di '{ds_part}' {known_marts} "
                        "— gcs_query potrebbe fallire."
                    )
                return result

    # Repo
    repo_key = None
    if forced in (None, "repo"):
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
                # `n_published` del builder diceva 0 su repo con 15 dataset su
                # GCS: sostituito dal conteggio reale dei parquet leggibili.
                "n_with_gcs": _n_with_gcs(ds_slugs),
                "sources": card.get("sources") or [],
                **_list_block(ds_slugs),
                "found": True,
            }
        )
        return result

    # Domain
    domain_key = None
    if forced in (None, "domain"):
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
                # `dataset_count` veniva dal builder; il numero di elementi
                # restituiti era 50 senza dirlo.
                "dataset_count": domain_key.get("dataset_count") or len(ds_slugs),
                "n_with_gcs": _n_with_gcs(ds_slugs),
                **_list_block(ds_slugs),
                "found": True,
            }
        )
        return result

    # Source (source_id)
    source_ds: list[str] = []
    seen_source_ds: set[str] = set()
    if forced in (None, "source"):
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
                        **_list_block(sorted(source_ds)),
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
                    **_list_block(sorted(source_ds)),
                    "found": True,
                }
            )
            return result

    # Analysis
    if forced in (None, "analysis"):
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
                        "year_min": _period_years(a.get("period"))[0],
                        "year_max": _period_years(a.get("period"))[1],
                        "datasets": a.get("datasets") or [],
                        "found": True,
                    }
                )
                return result

    if not result.get("found"):
        hints = search_index(topic_index, ref, limit=3)
        if hints:
            result["suggestions"] = [
                {
                    "type": h.get("type"),
                    "slug": h.get("slug"),
                    "name": h.get("name"),
                    "score": h.get("score"),
                }
                for h in hints
            ]
        else:
            result["hint"] = (
                "Nessun ref simile. Prova find(query=...) per cercare "
                "liberamente, oppure un ref parziale."
            )
    return result
