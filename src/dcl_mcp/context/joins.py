"""Suggerimenti di join tra dataset pubblicati, derivati dai semantic types.

Nessuna relazione scritta a mano: si confrontano i ``semantic_type`` già
risolti dal registry build e presenti in ``topic_index``. L'unica fonte di
verità è il vocabolario (alias → tipo), applicato a monte dal producer.

Regole:
- ``year``/``month``/``country_code`` sono esclusi: non sono join utili.
- le chiavi testuali (``municipality_name``) sono fragili (accenti, case):
  incluse solo con ``include_names=True``.
- ranking: chiavi condivise → anni in comune → stessa category.
"""

from __future__ import annotations

from typing import Any

from dcl_mcp.context.search import flatten_datasets

# Dimensioni troppo comuni per identificare un join.
NOISE_TYPES = frozenset({"year", "month", "country_code"})

# Chiavi testuali: stesse entità, confronto fragile.
NAME_TYPES = frozenset({"municipality_name"})

# Chiavi geografiche: join quasi sempre possibile, quindi poco informative.
GEO_TYPES = frozenset(
    {
        "municipality_code",
        "municipality_name",
        "cadastral_code",
        "province_code",
        "region_code",
        "nuts_code",
    }
)


def _keys(entry: dict[str, Any], include_names: bool) -> dict[str, str]:
    """Mappa semantic_type → nome colonna per le chiavi join della entry."""
    out: dict[str, str] = {}
    for col in entry.get("columns") or []:
        stype = col.get("semantic_type") or ""
        if not stype or stype in NOISE_TYPES:
            continue
        if stype in NAME_TYPES and not include_names:
            continue
        out.setdefault(stype, col.get("name") or "")
    return out


def _column_type(entry: dict[str, Any], name: str) -> str:
    """Tipo dichiarato della colonna (VARCHAR/BIGINT/...), vuoto se assente."""
    for col in entry.get("columns") or []:
        if col.get("name") == name:
            return str(col.get("type") or "")
    return ""


def _year_overlap(a: dict[str, Any], b: dict[str, Any]) -> int:
    """Anni coperti da entrambi i dataset, 0 se non determinabile."""
    pa = a.get("period") or {}
    pb = b.get("period") or {}
    try:
        start = max(int(pa.get("start") or 0), int(pb.get("start") or 0))
        end = min(int(pa.get("end") or 0), int(pb.get("end") or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, end - start + 1)


def join_suggestions(
    topic_index: dict[str, Any],
    slug: str,
    *,
    limit: int = 5,
    min_years: int = 1,
    include_names: bool = False,
) -> dict[str, Any]:
    """Suggerisce dataset joinabili con ``slug`` sulle chiavi semantiche comuni."""
    entries = flatten_datasets(topic_index.get("datasets") or {})
    by_slug: dict[str, dict[str, Any]] = {
        e["slug"]: e for e in entries if isinstance(e, dict) and e.get("slug")
    }
    meta: dict[str, dict[str, Any]] = topic_index.get("by_slug") or {}

    target = by_slug.get(slug)
    if target is None:
        return {
            "slug": slug,
            "found": False,
            "suggestions": [],
            "message": f"dataset '{slug}' non presente nel topic_index",
        }

    mine = _keys(target, include_names)
    if not mine:
        return {
            "slug": slug,
            "found": True,
            "keys": [],
            "suggestions": [],
            "total_candidates": 0,
            "message": "nessuna chiave semantica: join non suggeribile",
        }

    candidates: list[dict[str, Any]] = []
    for other in by_slug.values():
        if other.get("slug") == slug:
            continue
        theirs = _keys(other, include_names)
        shared = sorted(set(mine) & set(theirs))
        if not shared:
            continue
        years = _year_overlap(target, other)
        if years < min_years:
            continue
        on = []
        for t in shared:
            mine_type = _column_type(target, mine[t])
            theirs_type = _column_type(other, theirs[t])
            on.append(
                {
                    "type": t,
                    "mine": mine[t],
                    "theirs": theirs[t],
                    "mine_type": mine_type,
                    "theirs_type": theirs_type,
                    "type_mismatch": bool(mine_type and theirs_type and mine_type != theirs_type),
                }
            )
        candidates.append(
            {
                "slug": other["slug"],
                "name": other.get("name") or other["slug"],
                "repo": (meta.get(other["slug"]) or {}).get("registry_source") or "",
                "category": other.get("category") or "",
                "shared": len(shared),
                "specific": sum(1 for t in shared if t not in GEO_TYPES),
                "years_overlap": years,
                "same_category": (target.get("category") or "") == (other.get("category") or ""),
                "on": on,
                "confidence": "alias-match",
            }
        )

    candidates.sort(
        key=lambda c: (
            -c["specific"],
            -c["shared"],
            -c["years_overlap"],
            not c["same_category"],
            c["slug"],
        )
    )

    return {
        "slug": slug,
        "found": True,
        "keys": [{"type": t, "column": c} for t, c in sorted(mine.items())],
        "suggestions": candidates[:limit],
        "total_candidates": len(candidates),
        "truncated": len(candidates) > limit,
    }
