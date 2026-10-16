"""Tool MCP GitHub Discussions — lettura, ricerca e scrittura.

Nomi invariati rispetto al vecchio server ``github-discussions``
(lab-connectors): contratto pubblico usato da skill e config utente.
Richiede un token GitHub (GraphQL è autenticato anche in lettura).
"""

from __future__ import annotations

from typing import Any

from lab_connectors.mcp import guard_timed

from dcl_mcp.app import SERVER_NAME, mcp
from dcl_mcp.discussions.client import (
    add_discussion_comment,
    create_discussion,
    get_discussion,
    get_discussion_comments,
    get_discussion_summary,
    list_discussion_categories,
    list_discussions,
    search_discussions,
)


@mcp.tool(
    description=(
        "Elenca le GitHub Discussions di un repository, ordinate per UPDATED_AT discendente. "
        "Repo hub del Lab: dataciviclab/dataciviclab (intake pubblico, domande, proposte). "
        "Opzionalmente filtra per categoria (applicato lato client dopo paginazione — "
        "con paginazione attiva, discussion_count puo' essere 0 anche se discussioni "
        "della categoria esistono piu' avanti). "
        "Supporta cursor pagination: passa il valore 'end_cursor' della risposta "
        "precedente come 'after' per la pagina successiva."
    ),
    structured_output=True,
)
def github_list_discussions(
    repo_full_name: str,
    category_name: str | None = None,
    limit: int = 10,
    after: str | None = None,
    before: str | None = None,
) -> dict[str, Any]:
    """Elenca le discussioni di un repo con paginazione cursor."""
    return guard_timed(
        list_discussions,
        "github_list_discussions",
        repo_full_name,
        category_name,
        limit,
        after,
        before,
        logger_name=SERVER_NAME,
    )


@mcp.tool(
    description=(
        "Elenca le categorie discussioni valide di un repository "
        "(nome, slug, is_answerable) — usala prima di "
        "github_create_discussion per non sbagliare category_name."
    ),
    structured_output=True,
)
def github_list_discussion_categories(repo_full_name: str) -> dict[str, Any]:
    """Elenca le categorie utili per la creazione di discussioni."""
    return guard_timed(
        list_discussion_categories,
        "github_list_discussion_categories",
        repo_full_name,
        logger_name=SERVER_NAME,
    )


@mcp.tool(
    description=(
        "Restituisce una GitHub Discussion. "
        "mode=summary restituisce solo un estratto del body (default excerpt_chars=500). "
        "mode=full restituisce il body intero (default, backward compat)."
    ),
    structured_output=True,
)
def github_get_discussion(
    repo_full_name: str, number: int, mode: str = "full", excerpt_chars: int = 500
) -> dict[str, Any]:
    """Restituisce una discussion con body intero o riepilogo."""
    return guard_timed(
        get_discussion,
        "github_get_discussion",
        repo_full_name,
        number,
        mode,
        excerpt_chars,
        logger_name=SERVER_NAME,
    )


@mcp.tool(
    description="Restituisce un riepilogo corto di una GitHub Discussion.",
    structured_output=True,
)
def github_get_discussion_summary(
    repo_full_name: str, number: int, excerpt_chars: int = 280
) -> dict[str, Any]:
    """Restituisce un estratto corto di una discussion."""
    return guard_timed(
        get_discussion_summary,
        "github_get_discussion_summary",
        repo_full_name,
        number,
        excerpt_chars,
        logger_name=SERVER_NAME,
    )


@mcp.tool(
    description=(
        "Restituisce commenti top-level e reply annidate di una GitHub Discussion. "
        "mode=summary tronca body a excerpt_chars (default 200). "
        "mode=full restituisce body interi (default, backward compat)."
    ),
    structured_output=True,
)
def github_get_discussion_comments(
    repo_full_name: str,
    number: int,
    limit: int = 20,
    mode: str = "full",
    excerpt_chars: int = 200,
) -> dict[str, Any]:
    """Restituisce commenti e reply annidate di una discussion."""
    return guard_timed(
        get_discussion_comments,
        "github_get_discussion_comments",
        repo_full_name,
        number,
        limit,
        mode,
        excerpt_chars,
        logger_name=SERVER_NAME,
    )


@mcp.tool(
    description=(
        "Cerca discussions in un repository per testo libero (titolo, body, commenti). "
        "Usa la GitHub Search API — i risultati sono limitati al repository specificato."
    ),
    structured_output=True,
)
def github_search_discussions(repo_full_name: str, query: str, limit: int = 10) -> dict[str, Any]:
    """Cerca discussion per testo libero su titolo, body e commenti."""
    return guard_timed(
        search_discussions,
        "github_search_discussions",
        repo_full_name,
        query,
        limit,
        logger_name=SERVER_NAME,
    )


@mcp.tool(
    description=(
        "Crea una GitHub Discussion in una categoria del repository. "
        "Per il Lab usa repo_full_name=dataciviclab/dataciviclab. "
        "category_name accetta nome o slug della categoria (case-insensitive, "
        "es. 'Domanda' o 'domanda') — elenca le opzioni con "
        "github_list_discussion_categories."
    ),
    structured_output=True,
)
def github_create_discussion(
    repo_full_name: str, category_name: str, title: str, body: str
) -> dict[str, Any]:
    """Crea una nuova discussion in una categoria."""
    return guard_timed(
        create_discussion,
        "github_create_discussion",
        repo_full_name,
        category_name,
        title,
        body,
        logger_name=SERVER_NAME,
    )


@mcp.tool(
    description="Aggiunge un commento top-level a una GitHub Discussion.",
    structured_output=True,
)
def github_add_discussion_comment(repo_full_name: str, number: int, body: str) -> dict[str, Any]:
    """Aggiunge un commento top-level a una discussion."""
    return guard_timed(
        add_discussion_comment,
        "github_add_discussion_comment",
        repo_full_name,
        number,
        body,
        logger_name=SERVER_NAME,
    )
