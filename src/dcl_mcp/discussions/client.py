"""Client GraphQL per GitHub Discussions.

Fornisce funzioni pubbliche per list, search, get, create discussion e commenti.
Usa httpx per chiamate autenticate all'API GitHub GraphQL.

Migrato da ``lab-connectors/mcp_servers/github-discussions/`` con la
creazione di dcl-mcp (il MCP di dominio non è un connector infrastrutturale).
"""

from __future__ import annotations

import os
from typing import Any

import httpx

API_URL = "https://api.github.com/graphql"
TIMEOUT_SECONDS = 20

# Ordine di fallback per il token GitHub — allineato a _local/mcp/github/server.sh
_GITHUB_TOKEN_ENV_VARS = [
    "GITHUB_PERSONAL_ACCESS_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
]


class GitHubDiscussionsClientError(RuntimeError):
    """Errore sollevato dal client per input non validi o risposte API errate."""


def _get_token() -> str:
    for var in _GITHUB_TOKEN_ENV_VARS:
        token = os.environ.get(var, "").strip()
        if token:
            return token
    raise GitHubDiscussionsClientError(
        "Nessun token GitHub trovato nell'env del server MCP. "
        f"Cercati: {', '.join(_GITHUB_TOKEN_ENV_VARS)}."
    )


def _split_repo(repo_full_name: str) -> tuple[str, str]:
    text = repo_full_name.strip()
    parts = text.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise GitHubDiscussionsClientError("repo_full_name deve avere formato owner/repo")
    return parts[0], parts[1]


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT_SECONDS,
        headers={
            "Authorization": f"Bearer {_get_token()}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "DataCivicLab GitHub Discussions MCP",
        },
    )


def _graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    payload = {"query": query, "variables": variables}
    try:
        with _client() as client:
            resp = client.post(API_URL, json=payload)
    except httpx.TimeoutException as exc:
        raise GitHubDiscussionsClientError(
            "Timeout durante chiamata GraphQL GitHub Discussions"
        ) from exc
    except httpx.HTTPError as exc:
        raise GitHubDiscussionsClientError(f"Errore HTTP verso GitHub GraphQL: {exc}") from exc

    try:
        data = resp.json()
    except Exception as exc:
        raise GitHubDiscussionsClientError(
            f"GitHub GraphQL ha risposto con JSON non valido (HTTP {resp.status_code})"
        ) from exc

    if resp.status_code >= 400:
        message = data.get("message") if isinstance(data, dict) else None
        if message:
            raise GitHubDiscussionsClientError(f"GitHub GraphQL HTTP {resp.status_code}: {message}")
        raise GitHubDiscussionsClientError(f"GitHub GraphQL HTTP {resp.status_code}")

    errors = data.get("errors")
    if errors:
        first = errors[0] if isinstance(errors, list) and errors else {}
        message = first.get("message") if isinstance(first, dict) else None
        err_type = first.get("type") if isinstance(first, dict) else None
        err_path = first.get("path") if isinstance(first, dict) else None
        details = message or ""
        if err_type:
            details += f" [type: {err_type}]"
        if err_path:
            details += f" [path: {err_path}]"
        raise GitHubDiscussionsClientError(
            f"GitHub GraphQL error: {details}" if details else "GitHub GraphQL ha restituito errors"
        )

    payload_data = data.get("data")
    if payload_data is None:
        raise GitHubDiscussionsClientError("GitHub GraphQL ha restituito data vuoto")
    return payload_data


def _discussion_node_to_summary(node: dict[str, Any]) -> dict[str, Any]:
    category = node.get("category") or {}
    labels = ((node.get("labels") or {}).get("nodes")) or []
    return {
        "number": node.get("number"),
        "title": node.get("title"),
        "url": node.get("url"),
        "category": category.get("name"),
        "author": ((node.get("author") or {}).get("login")),
        "created_at": node.get("createdAt"),
        "updated_at": node.get("updatedAt"),
        "answer_chosen": bool(node.get("answerChosenAt")),
        "closed": bool(node.get("closed")),
        "closed_at": node.get("closedAt"),
        "labels": [{"name": lbl.get("name"), "color": lbl.get("color")} for lbl in labels],
    }


def _comment_node_to_dict(
    node: dict[str, Any], max_body_chars: int | None = None
) -> dict[str, Any]:
    replies = ((node.get("replies") or {}).get("nodes")) or []
    body = node.get("body", "")
    return {
        "comment_id": node.get("id"),
        "author": ((node.get("author") or {}).get("login")),
        "body": body if max_body_chars is None else _excerpt(body, max_body_chars),
        "created_at": node.get("createdAt"),
        "reply_count": ((node.get("replies") or {}).get("totalCount")) or 0,
        "replies": [
            {
                "reply_id": reply.get("id"),
                "author": ((reply.get("author") or {}).get("login")),
                "body": reply.get("body", "")
                if max_body_chars is None
                else _excerpt(reply.get("body", ""), max_body_chars),
                "created_at": reply.get("createdAt"),
            }
            for reply in replies
        ],
    }


def _clamp_excerpt(value: int, minimum: int = 80, maximum: int = 2000) -> int:
    """Clamp unico per gli excerpt di body e commenti."""
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = minimum
    return max(minimum, min(requested, maximum))


def _excerpt(text: str, max_chars: int = 280) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1].rstrip() + "..."


def _get_repository_and_categories(
    repo_full_name: str,
) -> tuple[str, list[dict[str, Any]]]:
    owner, name = _split_repo(repo_full_name)
    query = """
    query($owner:String!, $name:String!) {
      repository(owner:$owner, name:$name) {
        id
        discussionCategories(first:50) {
          nodes {
            id
            name
            slug
            isAnswerable
          }
        }
      }
    }
    """
    data = _graphql(query, {"owner": owner, "name": name})
    repo = data.get("repository")
    if not repo:
        raise GitHubDiscussionsClientError(
            f"Repository non trovato o Discussions non disponibili: {repo_full_name}"
        )
    categories = (repo.get("discussionCategories") or {}).get("nodes") or []
    return str(repo.get("id")), categories


def _get_discussion_id(repo_full_name: str, number: int) -> str:
    owner, name = _split_repo(repo_full_name)
    query = """
    query($owner:String!, $name:String!, $number:Int!) {
      repository(owner:$owner, name:$name) {
        discussion(number:$number) {
          id
        }
      }
    }
    """
    data = _graphql(query, {"owner": owner, "name": name, "number": number})
    discussion = ((data.get("repository") or {}).get("discussion")) or None
    if not discussion:
        raise GitHubDiscussionsClientError(f"Discussion #{number} non trovata in {repo_full_name}")
    return str(discussion.get("id"))


def list_discussion_categories(repo_full_name: str) -> dict[str, Any]:
    """Elenca le categorie discussioni valide per la creazione.

    Utile prima di github_create_discussion: evita errori da
    category_name inventato o storico (audit giro 3).
    """
    repo_id, categories = _get_repository_and_categories(repo_full_name)
    return {
        "repo_full_name": repo_full_name,
        "repo_id": repo_id,
        "categories": [
            {
                "name": c.get("name"),
                "slug": c.get("slug"),
                "is_answerable": bool(c.get("isAnswerable")),
            }
            for c in categories
        ],
    }


_SEARCH_DISCUSSIONS_QUERY = """
query($query:String!, $limit:Int!) {
  search(query:$query, type:DISCUSSION, first:$limit) {
    discussionCount
    nodes {
      ... on Discussion {
        number
        title
        url
        createdAt
        updatedAt
        answerChosenAt
        closed
        closedAt
        category { name }
        author { login }
        labels(first:10) { nodes { name color } }
      }
    }
  }
}
"""


def _search_nodes(
    repo_full_name: str, query: str, limit: int
) -> tuple[int, list[dict[str, Any]]]:
    """Search GraphQL repo-scoped: un solo path per search e list(query=)."""
    if not query.strip():
        raise GitHubDiscussionsClientError("query di ricerca vuota")
    owner, name = _split_repo(repo_full_name)
    safe_limit = max(1, min(limit, 50))
    data = _graphql(
        _SEARCH_DISCUSSIONS_QUERY,
        {"query": f"repo:{owner}/{name} {query.strip()}", "limit": safe_limit},
    )
    payload = data.get("search") or {}
    nodes = payload.get("nodes") or []
    results = [_discussion_node_to_summary(n) for n in nodes if isinstance(n, dict)]
    return payload.get("discussionCount") or len(results), results


def list_discussions(
    repo_full_name: str,
    category_name: str | None = None,
    limit: int = 10,
    after: str | None = None,
    before: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """Elenca discussions, oppure cerca se ``query`` è presente.

    Supporta cursor pagination con i parametri ``after`` e ``before``
    (passa il ``cursor`` dalla risposta precedente).

    Nota: il filtro ``category_name`` viene applicato lato client dopo la
    paginazione GitHub. Con paginazione attiva, una pagina può restituire
    ``discussion_count=0`` anche se discussioni della categoria esistono
    più avanti. ``total_count`` è il conteggio globale non filtrato.
    """
    owner, name = _split_repo(repo_full_name)
    safe_limit = max(1, min(limit, 50))

    if query and query.strip():
        count, results = _search_nodes(repo_full_name, query, safe_limit)
        if category_name and category_name.strip():
            wanted = category_name.strip().casefold()
            results = [
                r for r in results if (r.get("category") or "").casefold() == wanted
            ]
        return {
            "repo_full_name": repo_full_name,
            "query": query.strip(),
            "mode": "search",
            "category_name": category_name,
            "limit": safe_limit,
            "total_count": count,
            "discussion_count": len(results),
            "discussions": results,
        }

    if before:
        # backward pagination — usa last + before
        query = """
        query($owner:String!, $name:String!, $limit:Int!, $before:String!) {
          repository(owner:$owner, name:$name) {
            discussions(last:$limit, before:$before, orderBy:{field:UPDATED_AT, direction:DESC}) {
              totalCount
              pageInfo { hasNextPage hasPreviousPage startCursor endCursor }
              nodes {
                number title url createdAt updatedAt answerChosenAt closed closedAt
                category { name }
                author { login }
                labels(first:10) { nodes { name color } }
              }
            }
          }
        }
        """
        variables: dict[str, Any] = {
            "owner": owner,
            "name": name,
            "limit": safe_limit,
            "before": before,
        }
    else:
        # forward pagination (default) — usa first + after (after opzionale, null = prima pagina)
        query = """
        query($owner:String!, $name:String!, $limit:Int!, $after:String) {
          repository(owner:$owner, name:$name) {
            discussions(first:$limit, after:$after, orderBy:{field:UPDATED_AT, direction:DESC}) {
              totalCount
              pageInfo { hasNextPage hasPreviousPage startCursor endCursor }
              nodes {
                number title url createdAt updatedAt answerChosenAt closed closedAt
                category { name }
                author { login }
                labels(first:10) { nodes { name color } }
              }
            }
          }
        }
        """
        variables = {
            "owner": owner,
            "name": name,
            "limit": safe_limit,
            "after": after,
        }

    data = _graphql(query, variables)
    discussions_payload = ((data.get("repository") or {}).get("discussions")) or {}
    nodes = discussions_payload.get("nodes") or []
    page_info = discussions_payload.get("pageInfo") or {}
    discussions = [_discussion_node_to_summary(node) for node in nodes]

    if category_name and category_name.strip():
        wanted = category_name.strip().casefold()
        discussions = [
            item for item in discussions if (item.get("category") or "").casefold() == wanted
        ]

    payload = {
        "repo_full_name": repo_full_name,
        "mode": "list",
        "category_name": category_name,
        "limit": safe_limit,
        "total_count": discussions_payload.get("totalCount") or len(discussions),
        "discussion_count": len(discussions),
        "page_info": {
            "has_next": page_info.get("hasNextPage", False),
            "has_previous": page_info.get("hasPreviousPage", False),
            "start_cursor": page_info.get("startCursor"),
            "end_cursor": page_info.get("endCursor"),
        },
        "discussions": discussions,
    }
    if category_name and category_name.strip():
        # Il filtro è client-side dopo la paginazione: total_count resta
        # globale e inganna (audit giro 3). Chiarire nel payload.
        payload["note"] = (
            "category_name è filtrato lato client su questa pagina: "
            "discussion_count = matching nella pagina, total_count = tutte "
            "le discussioni del repo (non solo di questa categoria)."
        )
    return payload


def get_discussion(
    repo_full_name: str,
    number: int,
    mode: str = "full",
    excerpt_chars: int = 500,
    include_comments: bool = False,
    comments_limit: int = 20,
    comments_mode: str = "full",
    comments_excerpt_chars: int = 200,
) -> dict[str, Any]:
    """Recupera una discussion, opzionalmente con i commenti.

    ``include_comments`` evita una seconda chiamata per i commenti: le
    skill leggevano sempre body e thread insieme.
    """
    owner, name = _split_repo(repo_full_name)
    safe_mode = mode if mode in ("full", "summary") else "full"
    safe_excerpt = _clamp_excerpt(excerpt_chars)
    safe_comments_limit = max(1, min(comments_limit, 50))
    safe_comments_mode = comments_mode if comments_mode in ("full", "summary") else "full"
    safe_comments_excerpt = (
        _clamp_excerpt(comments_excerpt_chars, maximum=1000)
        if safe_comments_mode == "summary"
        else None
    )

    comments_block = (
        """
          comments(first:$commentsLimit) {
            totalCount
            nodes {
              id
              body
              createdAt
              author { login }
              replies(first:10) {
                totalCount
                nodes {
                  id
                  body
                  createdAt
                  author { login }
                }
              }
            }
          }"""
        if include_comments
        else ""
    )
    variables: dict[str, Any] = {"owner": owner, "name": name, "number": number}
    if include_comments:
        variables["commentsLimit"] = safe_comments_limit
    comments_var = ", $commentsLimit:Int!" if include_comments else ""
    query = f"""
    query($owner:String!, $name:String!, $number:Int!{comments_var}) {{
      repository(owner:$owner, name:$name) {{
        discussion(number:$number) {{
          id
          number
          title
          url
          body
          createdAt
          updatedAt
          answerChosenAt
          closed
          closedAt
          category {{ name }}{comments_block}
        }}
      }}
    }}
    """
    data = _graphql(query, variables)
    discussion = ((data.get("repository") or {}).get("discussion")) or None
    if not discussion:
        raise GitHubDiscussionsClientError(f"Discussion #{number} non trovata in {repo_full_name}")

    category = discussion.get("category") or {}
    base = {
        "repo_full_name": repo_full_name,
        "discussion_id": discussion.get("id"),
        "number": discussion.get("number"),
        "title": discussion.get("title"),
        "url": discussion.get("url"),
        "category": category.get("name"),
        "created_at": discussion.get("createdAt"),
        "updated_at": discussion.get("updatedAt"),
        "answer_chosen": bool(discussion.get("answerChosenAt")),
        "closed": bool(discussion.get("closed")),
        "closed_at": discussion.get("closedAt"),
        "mode": safe_mode,
    }

    if safe_mode == "summary":
        body_text = discussion.get("body", "")
        base["body_excerpt"] = _excerpt(body_text, safe_excerpt)
        base["body_chars"] = len(body_text) if body_text else 0
        base["excerpt_chars"] = safe_excerpt
    else:
        base["body"] = discussion.get("body", "")

    if include_comments:
        payload_comments = discussion.get("comments") or {}
        nodes = payload_comments.get("nodes") or []
        comments = [
            _comment_node_to_dict(n, max_body_chars=safe_comments_excerpt) for n in nodes
        ]
        base["comments"] = comments
        base["comment_count"] = len(comments)
        base["total_comment_count"] = payload_comments.get("totalCount") or len(comments)
        base["comments_mode"] = safe_comments_mode

    return base


def create_discussion(
    repo_full_name: str, category_name: str, title: str, body: str
) -> dict[str, Any]:
    """Crea una nuova discussion in una categoria del repository."""
    if not category_name.strip():
        raise GitHubDiscussionsClientError("category_name vuoto")
    if not title.strip():
        raise GitHubDiscussionsClientError("title vuoto")
    if not body.strip():
        raise GitHubDiscussionsClientError("body vuoto")

    repo_id, categories = _get_repository_and_categories(repo_full_name)
    wanted = category_name.strip().casefold()
    # Match su nome OR slug: "Domanda"/"domanda" e "Q&A"/"q-a" funzionano
    # tutti (audit: dubbio name-vs-slug sul write path).
    category = next(
        (
            item
            for item in categories
            if (item.get("name") or "").casefold() == wanted
            or (item.get("slug") or "").casefold() == wanted
        ),
        None,
    )
    if not category:
        available = ", ".join(
            sorted(item.get("name", "") for item in categories if item.get("name"))
        )
        raise GitHubDiscussionsClientError(
            f"Categoria '{category_name}' non trovata in {repo_full_name}. "
            f"Categorie disponibili: {available}"
        )

    mutation = """
    mutation($repositoryId:ID!, $categoryId:ID!, $title:String!, $body:String!) {
      createDiscussion(input:{
        repositoryId:$repositoryId,
        categoryId:$categoryId,
        title:$title,
        body:$body
      }) {
        discussion {
          id
          number
          url
        }
      }
    }
    """
    data = _graphql(
        mutation,
        {
            "repositoryId": repo_id,
            "categoryId": str(category.get("id")),
            "title": title.strip(),
            "body": body.strip(),
        },
    )
    discussion = ((data.get("createDiscussion") or {}).get("discussion")) or None
    if not discussion:
        raise GitHubDiscussionsClientError("createDiscussion non ha restituito discussion")
    return {
        "repo_full_name": repo_full_name,
        "number": discussion.get("number"),
        "url": discussion.get("url"),
    }


def add_discussion_comment(repo_full_name: str, number: int, body: str) -> dict[str, Any]:
    """Aggiunge un commento top-level a una discussion."""
    if not body.strip():
        raise GitHubDiscussionsClientError("body vuoto")
    discussion_id = _get_discussion_id(repo_full_name, number)
    mutation = """
    mutation($discussionId:ID!, $body:String!) {
      addDiscussionComment(input:{discussionId:$discussionId, body:$body}) {
        comment {
          id
          url
        }
      }
    }
    """
    data = _graphql(mutation, {"discussionId": discussion_id, "body": body.strip()})
    comment = ((data.get("addDiscussionComment") or {}).get("comment")) or None
    if not comment:
        raise GitHubDiscussionsClientError("addDiscussionComment non ha restituito comment")
    return {
        "repo_full_name": repo_full_name,
        "number": number,
        "comment_id": comment.get("id"),
        "url": comment.get("url"),
    }
