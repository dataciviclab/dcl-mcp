"""Client GitHub Discussions — token, split repo e operazioni con _graphql mockato."""

from __future__ import annotations

from typing import Any

import pytest

from dcl_mcp.discussions.client import (
    _GITHUB_TOKEN_ENV_VARS,
    GitHubDiscussionsClientError,
    _get_token,
    _split_repo,
    add_discussion_comment,
    create_discussion,
    get_discussion,
    list_discussion_categories,
    list_discussions,
)

# ── fixtures ──────────────────────────────────────────────────────────────────

_FAKE_DISCUSSION_NODE = {
    "number": 42,
    "title": "Test Discussion",
    "url": "https://github.com/owner/repo/discussions/42",
    "createdAt": "2026-01-15T10:00:00Z",
    "updatedAt": "2026-06-01T12:00:00Z",
    "answerChosenAt": None,
    "closed": True,
    "closedAt": "2026-06-02T15:55:00Z",
    "category": {"name": "Ideas"},
}

_FAKE_DISCUSSION_NODE_OPEN = {**_FAKE_DISCUSSION_NODE, "closed": False, "closedAt": None}

_FAKE_LIST_RESPONSE = {
    "repository": {
        "discussions": {
            "totalCount": 1,
            "pageInfo": {
                "hasNextPage": False,
                "hasPreviousPage": False,
                "startCursor": "Y3Vyc29yOnYyOpHOA1",
                "endCursor": "Y3Vyc29yOnYyOpHOA2",
            },
            "nodes": [_FAKE_DISCUSSION_NODE],
        }
    }
}

_FAKE_SEARCH_RESPONSE = {
    "search": {
        "discussionCount": 1,
        "nodes": [
            {
                "number": 42,
                "title": "Test Discussion",
                "url": "https://github.com/owner/repo/discussions/42",
                "createdAt": "2026-01-15T10:00:00Z",
                "updatedAt": "2026-06-01T12:00:00Z",
                "answerChosenAt": None,
                "closed": False,
                "closedAt": None,
                "category": {"name": "Ideas"},
                "author": {"login": "testuser"},
                "labels": {"nodes": [{"name": "bug", "color": "d73a4a"}]},
            }
        ],
    }
}


@pytest.fixture
def mock_graphql(monkeypatch):
    """Sostituisce _graphql con una funzione controllata (calls + response)."""
    calls: list[dict[str, Any]] = []
    current_response: dict[str, Any] = {}

    def fake_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        calls.append({"query": query, "variables": dict(variables)})
        if "error" in current_response:
            raise GitHubDiscussionsClientError(current_response["error"])
        return dict(current_response.get("data", {}))

    monkeypatch.setattr("dcl_mcp.discussions.client._graphql", fake_graphql)
    return calls, current_response


# ── _get_token ────────────────────────────────────────────────────────────────


@pytest.mark.pure_unit
class TestGetToken:
    def test_first_var_used(self, monkeypatch):
        for var in _GITHUB_TOKEN_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv(_GITHUB_TOKEN_ENV_VARS[0], "token-a")
        assert _get_token() == "token-a"

    def test_second_var_fallback(self, monkeypatch):
        for var in _GITHUB_TOKEN_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv(_GITHUB_TOKEN_ENV_VARS[1], "token-b")
        assert _get_token() == "token-b"

    def test_third_var_fallback(self, monkeypatch):
        for var in _GITHUB_TOKEN_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv(_GITHUB_TOKEN_ENV_VARS[2], "token-c")
        assert _get_token() == "token-c"

    def test_raises_when_missing(self, monkeypatch):
        for var in _GITHUB_TOKEN_ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(GitHubDiscussionsClientError, match="Nessun token"):
            _get_token()

    def test_precedence_order(self, monkeypatch):
        for var in _GITHUB_TOKEN_ENV_VARS:
            monkeypatch.setenv(var, f"value-{var}")
        assert _get_token() == f"value-{_GITHUB_TOKEN_ENV_VARS[0]}"


# ── _split_repo ───────────────────────────────────────────────────────────────


@pytest.mark.pure_unit
class TestSplitRepo:
    def test_valid(self):
        assert _split_repo("owner/repo") == ("owner", "repo")

    def test_valid_with_spaces(self):
        assert _split_repo("  owner/repo  ") == ("owner", "repo")

    def test_invalid(self):
        with pytest.raises(GitHubDiscussionsClientError, match="owner/repo"):
            _split_repo("justrepo")
        with pytest.raises(GitHubDiscussionsClientError, match="owner/repo"):
            _split_repo("/repo")
        with pytest.raises(GitHubDiscussionsClientError, match="owner/repo"):
            _split_repo("owner/")


# ── list_discussions ──────────────────────────────────────────────────────────


@pytest.mark.adapter
class TestListDiscussions:
    def test_forward_no_cursor(self, mock_graphql):
        calls, resp = mock_graphql
        resp["data"] = _FAKE_LIST_RESPONSE

        result = list_discussions("owner/repo")

        assert result["repo_full_name"] == "owner/repo"
        assert result["mode"] == "list"
        assert result["discussion_count"] == 1
        disc = result["discussions"][0]
        assert disc["number"] == 42
        assert disc["closed"] is True
        assert result["page_info"]["has_next"] is False
        assert calls[0]["variables"]["after"] is None

    def test_forward_with_after_cursor(self, mock_graphql):
        calls, resp = mock_graphql
        resp["data"] = _FAKE_LIST_RESPONSE

        list_discussions("owner/repo", after="Y3Vyc29yOnYyOpHOA2")

        assert calls[0]["variables"]["after"] == "Y3Vyc29yOnYyOpHOA2"
        assert 'after:"Y3Vyc29yOnYyOpHOA2"' not in calls[0]["query"]

    def test_backward_with_before_cursor(self, mock_graphql):
        calls, resp = mock_graphql
        resp["data"] = _FAKE_LIST_RESPONSE

        list_discussions("owner/repo", before="Y3Vyc29yOnYyOpHOA3")

        assert calls[0]["variables"]["before"] == "Y3Vyc29yOnYyOpHOA3"
        assert "last:" in calls[0]["query"]
        assert "discussions(first:" not in calls[0]["query"]

    def test_category_filter_is_server_side(self, mock_graphql):
        """category_name usa il qualifier GitHub, non un filtro lato client."""
        calls, resp = mock_graphql
        resp["data"] = _FAKE_SEARCH_RESPONSE

        result = list_discussions("owner/repo", category_name="Domanda")

        assert result["mode"] == "search"
        assert result["category_name"] == "Domanda"
        assert "note" not in result
        q = calls[0]["variables"]["query"]
        assert 'category:"Domanda"' in q
        # solo categoria → ordine per updated recente
        assert "sort:updated-desc" in q
        assert result["discussion_count"] == 1

    def test_category_and_query_combined(self, mock_graphql):
        calls, resp = mock_graphql
        resp["data"] = _FAKE_SEARCH_RESPONSE

        list_discussions("owner/repo", category_name="Domanda", query="rifiuti")

        q = calls[0]["variables"]["query"]
        assert 'category:"Domanda"' in q
        assert q.endswith("rifiuti")
        assert "sort:updated-desc" not in q

    def test_empty_response(self, mock_graphql):
        _calls, resp = mock_graphql
        resp["data"] = {
            "repository": {"discussions": {"totalCount": 0, "pageInfo": {}, "nodes": []}}
        }
        result = list_discussions("owner/repo")
        assert result["discussion_count"] == 0

    def test_error_from_graphql(self, mock_graphql):
        _calls, resp = mock_graphql
        resp["error"] = "NOT_FOUND"
        with pytest.raises(GitHubDiscussionsClientError, match="NOT_FOUND"):
            list_discussions("owner/repo")


# ── get_discussion ─────────────────────────────────────────────────


@pytest.mark.adapter
class TestGetDiscussion:
    def test_full_mode_returns_body(self, mock_graphql):
        _calls, resp = mock_graphql
        resp["data"] = {
            "repository": {
                "discussion": {
                    **_FAKE_DISCUSSION_NODE,
                    "id": "D_kw123",
                    "body": "Full body text here",
                }
            }
        }
        result = get_discussion("owner/repo", 42, mode="full")
        assert result["body"] == "Full body text here"
        assert result["closed"] is True

    def test_summary_truncates_body(self, mock_graphql):
        _calls, resp = mock_graphql
        resp["data"] = {
            "repository": {
                "discussion": {**_FAKE_DISCUSSION_NODE, "id": "D_kw123", "body": "A" * 1000}
            }
        }
        result = get_discussion("owner/repo", 42, mode="summary", excerpt_chars=100)
        assert "body" not in result
        assert len(result["body_excerpt"]) <= 100 + 3


@pytest.mark.adapter
class TestGetDiscussionWithComments:
    def test_include_comments_single_round_trip(self, mock_graphql):
        calls, resp = mock_graphql
        resp["data"] = {
            "repository": {
                "discussion": {
                    "id": "D_kw123",
                    "body": "Body",
                    "comments": {
                        "totalCount": 1,
                        "nodes": [
                            {
                                "id": "DC_1",
                                "body": "Ciao",
                                "createdAt": "2026-01-01T00:00:00Z",
                                "author": {"login": "u1"},
                                "replies": {"totalCount": 0, "nodes": []},
                            }
                        ],
                    },
                }
            }
        }
        result = get_discussion("owner/repo", 42, include_comments=True)
        assert len(calls) == 1
        assert result["body"] == "Body"
        assert result["comment_count"] == 1
        assert result["comments"][0]["author"] == "u1"
        assert "$commentsLimit" in calls[0]["query"]

    def test_without_comments_has_no_comments_key(self, mock_graphql):
        _calls, resp = mock_graphql
        resp["data"] = {"repository": {"discussion": {"id": "D", "body": "B"}}}
        assert "comments" not in get_discussion("owner/repo", 42)

    def test_summary_clamp_unified(self, mock_graphql):
        _calls, resp = mock_graphql
        resp["data"] = {"repository": {"discussion": {"id": "D", "body": "A" * 5000}}}
        result = get_discussion("owner/repo", 42, mode="summary", excerpt_chars=99999)
        assert result["excerpt_chars"] == 2000


@pytest.mark.adapter
class TestListDiscussionsSearch:
    def test_query_uses_search_path_same_shape(self, mock_graphql):
        calls, resp = mock_graphql
        resp["data"] = _FAKE_SEARCH_RESPONSE
        result = list_discussions("owner/repo", query="test query")

        assert result["mode"] == "search"
        assert result["discussion_count"] == 1
        assert result["discussions"][0]["number"] == 42
        assert "repo:owner/repo" in calls[0]["variables"]["query"]


@pytest.mark.adapter
class TestListCategories:
    def test_lists_categories_with_slug(self, mock_graphql):
        _calls, resp = mock_graphql
        resp["data"] = {
            "repository": {
                "id": "R_1",
                "discussionCategories": {
                    "nodes": [
                        {"id": "C_1", "name": "Domanda", "slug": "domanda", "isAnswerable": True},
                        {"id": "C_2", "name": "Proposte", "slug": "proposte", "isAnswerable": False},
                    ]
                },
            }
        }
        result = list_discussion_categories("owner/repo")
        assert [c["name"] for c in result["categories"]] == ["Domanda", "Proposte"]
        assert result["categories"][0]["slug"] == "domanda"
        assert result["categories"][0]["is_answerable"] is True


@pytest.mark.adapter
class TestMutations:
    def test_create_matches_name_or_slug(self, mock_graphql):
        """category_name accetta nome o slug (case-insensitive)."""
        _calls, resp = mock_graphql
        resp["data"] = {
            "repository": {
                "id": "R_1",
                "discussionCategories": {
                    "nodes": [
                        {"id": "C_1", "name": "Q&A", "slug": "q-a", "isAnswerable": True},
                    ]
                },
            }
        }
        # Lookup su slug riuscito → la mutation parte (il fake restituisce lo
        # stesso payload, quindi fallisce dopo la lookup). Se lo slug non
        # fosse accettato, l'errore sarebbe "Categoria 'q-a' non trovata".
        with pytest.raises(GitHubDiscussionsClientError, match="createDiscussion"):
            create_discussion("owner/repo", "q-a", "Titolo", "Body")

    def test_create_rejects_unknown_category(self, mock_graphql):
        _calls, resp = mock_graphql
        resp["data"] = {
            "repository": {
                "id": "R_1",
                "discussionCategories": {
                    "nodes": [{"id": "C_1", "name": "Domanda", "slug": "domanda"}]
                },
            }
        }
        with pytest.raises(GitHubDiscussionsClientError, match="non trovata"):
            create_discussion("owner/repo", "inesistente", "Titolo", "Body")

    def test_create_requires_non_empty_fields(self, mock_graphql):
        with pytest.raises(GitHubDiscussionsClientError, match="vuoto"):
            create_discussion("owner/repo", "", "title", "body")
        with pytest.raises(GitHubDiscussionsClientError, match="vuoto"):
            create_discussion("owner/repo", "cat", "", "body")
        with pytest.raises(GitHubDiscussionsClientError, match="vuoto"):
            create_discussion("owner/repo", "cat", "title", "")

    def test_add_comment_requires_non_empty_body(self, mock_graphql):
        with pytest.raises(GitHubDiscussionsClientError, match="vuoto"):
            add_discussion_comment("owner/repo", 42, "")

    def test_add_comment_requires_valid_repo(self, mock_graphql):
        with pytest.raises(GitHubDiscussionsClientError, match="owner/repo"):
            add_discussion_comment("invalid", 42, "body")
