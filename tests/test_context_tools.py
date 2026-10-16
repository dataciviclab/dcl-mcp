"""Tool context — fetch artefatti (FakeHttpClient) e navigazione."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from lab_connectors.http import HttpResult
from lab_connectors.testing import FakeHttpClient, fake_response

import dcl_mcp.app as app
import dcl_mcp.context.fetch as ctx_fetch
import dcl_mcp.context.tools as context_tools
from tests.conftest import SAMPLE_INDEX


def _patch_fetch(fake: FakeHttpClient, path: str, text: str = "", status: int = 200) -> None:
    """Register a response for one of the context-branch artifact URLs."""
    url = f"{ctx_fetch.RAW_BASE}/{path}"
    resp = fake_response(status, text=text)
    err = Exception(f"HTTP {status}") if status >= 400 else None
    fake.responses[url] = HttpResult(response=resp, err=err)
    ctx_fetch.artifact_cache.invalidate(path)


def _use_fake(fake: FakeHttpClient) -> None:
    ctx_fetch._http = fake


def _clear_fake() -> None:
    ctx_fetch._http = None
    ctx_fetch.artifact_cache.clear()


@pytest.fixture(autouse=True)
def _reset_state():
    """Isola lo stato globale del server tra i test."""
    yield
    _clear_fake()
    context_tools._last_refresh_attempt = None


# ── fetch-based tools ─────────────────────────────────────────────────────────


@pytest.mark.contract
def test_session_bootstrap():
    fake = FakeHttpClient()
    _patch_fetch(fake, "session_bootstrap.md", text="# Session Bootstrap\n")
    _use_fake(fake)
    result = context_tools.session_bootstrap()

    assert result["format"] == "markdown"
    assert "Session Bootstrap" in result["content"]


@pytest.mark.contract
def test_workspace_triage_compact():
    fake = FakeHttpClient()
    _patch_fetch(
        fake,
        "workspace_triage.json",
        text='{"open_prs": 2, "radar": {"green": 3, "yellow": 1}, "registry_summary": []}',
    )
    _use_fake(fake)
    result = context_tools.workspace_triage()

    assert result["ok"] is True
    assert result["prs"] == 2
    assert result["radar"]["green"] == 3


@pytest.mark.contract
def test_workspace_triage_section_and_invalid():
    fake = FakeHttpClient()
    _patch_fetch(fake, "workspace_triage.json", text='{"warnings": ["w1"], "radar": {}}')
    _use_fake(fake)

    section = context_tools.workspace_triage(section="warnings")
    assert section["content"] == {"warnings": ["w1"]}

    invalid = context_tools.workspace_triage(section="nope")
    assert invalid["ok"] is False


@pytest.mark.contract
def test_fetch_http_errors_return_error_dict():
    fake = FakeHttpClient()
    _patch_fetch(fake, "session_bootstrap.md", status=403)
    _use_fake(fake)
    result = context_tools.session_bootstrap()
    assert "error" in result

    fake2 = FakeHttpClient()
    _patch_fetch(fake2, "workspace_triage.json", status=404)
    _use_fake(fake2)
    assert "error" in context_tools.workspace_triage()


# ── lab_map / find / explore ──────────────────────────────────────────────────


@pytest.mark.contract
def test_lab_map_tree():
    fake = FakeHttpClient()
    _patch_fetch(fake, "topic_index.json", text=json.dumps(SAMPLE_INDEX))
    _use_fake(fake)
    content = context_tools.lab_map()["content"]

    assert content["ok"] is True
    assert content["totals"]["datasets"] == 3
    assert any(d["slug"] == "territorio-ambiente" for d in content["domains"])
    assert content["sources"]["ispra"] == 1


@pytest.mark.contract
def test_lab_map_domain_filter():
    fake = FakeHttpClient()
    _patch_fetch(fake, "topic_index.json", text=json.dumps(SAMPLE_INDEX))
    _use_fake(fake)
    content = context_tools.lab_map(domain="territorio-ambiente")["content"]
    assert [d["slug"] for d in content["domains"]] == ["territorio-ambiente"]


@pytest.mark.contract
def test_find_ranks_dataset_with_parents():
    fake = FakeHttpClient()
    _patch_fetch(fake, "topic_index.json", text=json.dumps(SAMPLE_INDEX))
    _use_fake(fake)
    content = context_tools.find(query="ispra_ru_base", limit=5)["content"]

    assert content["ok"] is True
    top = content["results"][0]
    assert top["type"] == "dataset"
    assert top["parent_repo"] == "rifiuti-urbani"
    assert top["parent_domain"] == "territorio-ambiente"


@pytest.mark.contract
def test_find_type_filter_repo_and_no_github_by_default():
    fake = FakeHttpClient()
    _patch_fetch(fake, "topic_index.json", text=json.dumps(SAMPLE_INDEX))
    _use_fake(fake)

    repo = context_tools.find(query="toolkit", type="repo")["content"]
    assert repo["results"][0]["role"] == "infra"

    plain = context_tools.find(query="rifiuti")["content"]
    assert "issues" not in plain


@pytest.mark.contract
def test_explore_dataset_repo_domain_not_found():
    fake = FakeHttpClient()
    _patch_fetch(fake, "topic_index.json", text=json.dumps(SAMPLE_INDEX))
    _use_fake(fake)

    card = context_tools.explore(ref="ispra_ru_base")["content"]
    assert card["found"] is True and card["type"] == "dataset"
    assert card["repo"]["role"] == "dati"

    repo = context_tools.explore(ref="rifiuti-urbani")["content"]
    assert repo["type"] == "repo" and repo["n_published"] == 1

    dom = context_tools.explore(ref="territorio-ambiente")["content"]
    assert dom["type"] == "domain" and "ispra_ru_base" in dom["datasets"]

    assert context_tools.explore(ref="does-not-exist")["content"]["found"] is False


# ── refresh_context ───────────────────────────────────────────────────────────


def _reset_refresh_state(monkeypatch, env_loaded: bool = True) -> None:
    monkeypatch.setattr(context_tools, "_last_refresh_attempt", None)
    monkeypatch.setattr(app, "_ENV_LOADED", env_loaded)


def _mock_http_result(status: int = 204) -> HttpResult:
    resp = MagicMock()
    resp.status_code = status
    resp.text = "mock response"
    return HttpResult(response=resp, err=None, ssl_fallback_used=None)


@pytest.mark.adapter
def test_refresh_context_no_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("DCL_MCP_ENV_FILE", raising=False)
    monkeypatch.delenv("ACB_ENV_FILE", raising=False)
    monkeypatch.setattr(app, "_ENV_LOADED", True)
    result = context_tools.refresh_context()

    assert result["ok"] is False
    assert "GITHUB_TOKEN" in result["error"]


@pytest.mark.adapter
def test_refresh_context_loads_token_from_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GITHUB_TOKEN=file-token\n", encoding="utf-8")

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("DCL_MCP_ENV_FILE", str(env_file))
    _reset_refresh_state(monkeypatch, env_loaded=False)

    with patch("dcl_mcp.context.fetch.HttpClient.post") as mock_post:
        mock_post.return_value = _mock_http_result(204)
        result = context_tools.refresh_context()

    assert result["ok"] is True
    assert mock_post.call_args.kwargs["headers"]["Authorization"] == "token file-token"


@pytest.mark.adapter
def test_refresh_context_supports_legacy_acb_env_file(monkeypatch, tmp_path):
    """Backward-compat: ACB_ENV_FILE continua a funzionare come DCL_MCP_ENV_FILE."""
    env_file = tmp_path / ".env"
    env_file.write_text("GITHUB_TOKEN=legacy-token\n", encoding="utf-8")

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("DCL_MCP_ENV_FILE", raising=False)
    monkeypatch.setenv("ACB_ENV_FILE", str(env_file))
    _reset_refresh_state(monkeypatch, env_loaded=False)

    with patch("dcl_mcp.context.fetch.HttpClient.post") as mock_post:
        mock_post.return_value = _mock_http_result(204)
        result = context_tools.refresh_context()

    assert result["ok"] is True
    assert mock_post.call_args.kwargs["headers"]["Authorization"] == "token legacy-token"


@pytest.mark.adapter
def test_refresh_context_success_and_api_error(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    _reset_refresh_state(monkeypatch)

    with patch("dcl_mcp.context.fetch.HttpClient.post") as mock_post:
        mock_post.return_value = _mock_http_result(204)
        assert context_tools.refresh_context()["ok"] is True

    _reset_refresh_state(monkeypatch)
    with patch("dcl_mcp.context.fetch.HttpClient.post") as mock_post:
        mock_post.return_value = _mock_http_result(403)
        result = context_tools.refresh_context()
    assert result["ok"] is False and "error" in result


@pytest.mark.adapter
def test_refresh_context_rate_limit(monkeypatch):
    import time

    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    _reset_refresh_state(monkeypatch)
    context_tools._last_refresh_attempt = time.monotonic()
    result = context_tools.refresh_context()
    assert result["ok"] is False
    assert result["retry_after"] > 0


# ── _search_github_issues ─────────────────────────────────────────────────────


@pytest.mark.adapter
def test_search_github_issues_error_and_malformed():
    fake = FakeHttpClient()
    fake.responses["https://api.github.com/search/issues"] = HttpResult(
        response=None, err=RuntimeError("403 rate limit exceeded")
    )
    _use_fake(fake)
    assert context_tools._search_github_issues("test", "tok", limit=5) == []

    fake2 = FakeHttpClient()
    fake2.responses["https://api.github.com/search/issues"] = HttpResult(
        response=fake_response(200, text="not json"), err=None
    )
    _use_fake(fake2)
    assert context_tools._search_github_issues("test", "tok", limit=5) == []


@pytest.mark.adapter
def test_search_github_issues_parses_items_and_pr():
    fake = FakeHttpClient()
    fake.responses["https://api.github.com/search/issues"] = HttpResult(
        response=fake_response(
            200,
            json_data={
                "items": [
                    {
                        "number": 42,
                        "title": "Test issue about rifiuti",
                        "state": "open",
                        "html_url": "https://github.com/dataciviclab/test-repo/issues/42",
                        "repository_url": "https://api.github.com/repos/dataciviclab/test-repo",
                    },
                    {
                        "number": 99,
                        "title": "feat: add dataset",
                        "state": "open",
                        "html_url": "https://github.com/dataciviclab/repo/pull/99",
                        "repository_url": "https://api.github.com/repos/dataciviclab/repo",
                        "pull_request": {"url": "..."},
                    },
                ]
            },
        ),
        err=None,
    )
    _use_fake(fake)
    result = context_tools._search_github_issues("rifiuti", "tok", limit=5)

    assert len(result) == 2
    assert result[0]["repo"] == "dataciviclab/test-repo"
    assert result[0]["type"] == "issue"
    assert result[1]["type"] == "pr"


# ── artifact TTL cache ────────────────────────────────────────────────────────


@pytest.mark.policy
def test_artifact_cache_reuses_topic_index():
    fake = FakeHttpClient()
    _patch_fetch(fake, "topic_index.json", text=json.dumps(SAMPLE_INDEX))
    _use_fake(fake)
    try:
        context_tools.lab_map()
        context_tools.lab_map()
        context_tools.find(query="ispra_ru_base", limit=1)
        context_tools.explore(ref="toolkit")
        assert ctx_fetch.artifact_cache.stats.entries >= 1
    finally:
        _clear_fake()


@pytest.mark.policy
def test_artifact_cache_cleared_on_refresh_success(monkeypatch):
    fake = FakeHttpClient()
    _patch_fetch(fake, "topic_index.json", text=json.dumps(SAMPLE_INDEX))
    _use_fake(fake)
    context_tools.lab_map()
    assert ctx_fetch.artifact_cache.stats.entries >= 1

    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(app, "_ENV_LOADED", True)
    monkeypatch.setattr(context_tools, "_last_refresh_attempt", None)

    dispatch_url = f"{ctx_fetch.API_BASE}/actions/workflows/build-context.yml/dispatches"
    fake.responses[dispatch_url] = HttpResult(response=fake_response(204, text=""), err=None)
    result = context_tools.refresh_context()

    assert result["ok"] is True
    assert ctx_fetch.artifact_cache.stats.entries == 0
