"""Tool MCP GitHub Discussions — lettura, ricerca e scrittura.

Nomi invariati rispetto al vecchio server ``github-discussions``
(lab-connectors): contratto pubblico usato da skill e config utente.
Richiede un token GitHub (GraphQL è autenticato anche in lettura).
"""

from __future__ import annotations

from typing import Any

from dcl_mcp.app import SERVER_NAME, mcp
from dcl_mcp.discussions.client import (
    add_discussion_comment,
    create_discussion,
    get_discussion,
    list_discussion_categories,
    list_discussions,
)
from dcl_mcp.guards import guard_ok

HUB_REPO = "dataciviclab/dataciviclab"


@mcp.tool(
    description=(
        "Elenca le GitHub Discussions di un repository, ordinate per UPDATED_AT discendente. "
        "Default: repo hub dataciviclab/dataciviclab. "
        "Opzionalmente filtra per categoria (applicato lato client dopo paginazione — "
        "con paginazione attiva, discussion_count puo' essere 0 anche se discussioni "
        "della categoria esistono piu' avanti). "
        "Supporta cursor pagination: passa il valore 'end_cursor' della risposta "
        "precedente come 'after' per la pagina successiva. "
        "Con query=: full-text search repo-scoped (mode='search', cursori ignorati)."
    ),
    structured_output=True,
)
def github_list_discussions(
    repo_full_name: str = HUB_REPO,
    category_name: str | None = None,
    limit: int = 10,
    after: str | None = None,
    before: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """Elenca le discussioni di un repo, o le cerca se query= è presente."""
    return guard_ok(
        list_discussions,
        "github_list_discussions",
        repo_full_name,
        category_name,
        limit,
        after,
        before,
        query,
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
def github_list_discussion_categories(repo_full_name: str = HUB_REPO) -> dict[str, Any]:
    """Elenca le categorie utili per la creazione di discussioni."""
    return guard_ok(
        list_discussion_categories,
        "github_list_discussion_categories",
        repo_full_name,
        logger_name=SERVER_NAME,
    )


@mcp.tool(
    description=(
        "Restituisce una GitHub Discussion. "
        "mode=summary restituisce solo un estratto del body (default excerpt_chars=500). "
        "mode=full restituisce il body intero (default, backward compat). "
        "include_comments=true aggiunge commenti e reply annidate nello stesso "
        "round-trip (comments_mode=full|summary, comments_limit, comments_excerpt_chars)."
    ),
    structured_output=True,
)
def github_get_discussion(
    number: int,
    repo_full_name: str = HUB_REPO,
    mode: str = "full",
    excerpt_chars: int = 500,
    include_comments: bool = False,
    comments_limit: int = 20,
    comments_mode: str = "full",
    comments_excerpt_chars: int = 200,
) -> dict[str, Any]:
    """Restituisce una discussion, opzionalmente con i commenti."""
    return guard_ok(
        get_discussion,
        "github_get_discussion",
        repo_full_name,
        number,
        mode,
        excerpt_chars,
        include_comments,
        comments_limit,
        comments_mode,
        comments_excerpt_chars,
        logger_name=SERVER_NAME,
    )


@mcp.tool(
    description=(
        "Crea una GitHub Discussion in una categoria del repository. "
        "Default: repo hub dataciviclab/dataciviclab. "
        "category_name accetta nome o slug della categoria (case-insensitive, "
        "es. 'Domanda' o 'domanda') — elenca le opzioni con "
        "github_list_discussion_categories."
    ),
    structured_output=True,
)
def github_create_discussion(
    category_name: str, title: str, body: str, repo_full_name: str = HUB_REPO
) -> dict[str, Any]:
    """Crea una nuova discussion in una categoria."""
    return guard_ok(
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
def github_add_discussion_comment(
    number: int, body: str, repo_full_name: str = HUB_REPO
) -> dict[str, Any]:
    """Aggiunge un commento top-level a una discussion."""
    return guard_ok(
        add_discussion_comment,
        "github_add_discussion_comment",
        repo_full_name,
        number,
        body,
        logger_name=SERVER_NAME,
    )
