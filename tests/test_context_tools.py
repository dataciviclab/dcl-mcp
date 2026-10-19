"""Tool context — fetch artefatti (FakeHttpClient) e navigazione."""

from __future__ import annotations

import json

import pytest
from lab_connectors.http import HttpResult
from lab_connectors.testing import FakeHttpClient, fake_response

import dcl_mcp.app as app
import dcl_mcp.context.fetch as ctx_fetch
import dcl_mcp.context.tools as context_tools
from dcl_mcp.gcs.resolver import GcsResolveError
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
def test_lab_map_invalid_domain_is_honest():
    """Dominio sconosciuto → ok:false + elenco domini validi (niente totali globali)."""
    fake = FakeHttpClient()
    _patch_fetch(fake, "topic_index.json", text=json.dumps(SAMPLE_INDEX))
    _use_fake(fake)
    result = context_tools.lab_map(domain="inesistente")

    assert result["ok"] is False
    assert " Domini validi:" in result["error"]
    assert "territorio-ambiente" in result["valid_domains"]


@pytest.mark.contract
def test_find_ranks_dataset_with_parents():
    fake = FakeHttpClient()
    _patch_fetch(fake, "topic_index.json", text=json.dumps(SAMPLE_INDEX))
    _use_fake(fake)
    content = context_tools.find(query="ispra_ru_base", limit=5)["content"]

    assert content["ok"] is True
    assert content["total_catalog"] == len(content["results"])
    assert content["total"] == content["total_catalog"]
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
    assert "total_external" not in plain


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


@pytest.mark.contract
def test_explore_mart_uses_registry_columns_when_present(monkeypatch):
    """Se il registry ha le colonne del mart, quelle vincono."""
    fake = FakeHttpClient()
    idx = json.loads(json.dumps(SAMPLE_INDEX))
    idx["datasets"]["ispra"][0]["mart_refs"] = ["ispra_ru_base__mart_comuni"]
    _patch_fetch(fake, "topic_index.json", text=json.dumps(idx))
    _use_fake(fake)

    def fake_detail(repo, mart_slug):
        assert repo == "rifiuti-urbani"
        assert mart_slug == "ispra_ru_base__mart_comuni"
        return {
            "table": "mart_comuni",
            "columns": [{"name": "anno", "type": "INTEGER", "role": "dimension"}],
            "primary_key": ["anno"],
        }

    monkeypatch.setattr("dcl_mcp.gcs.resolver.mart_card_detail", fake_detail)
    monkeypatch.setattr(
        "dcl_mcp.gcs.resolver.resolve",
        lambda topic, slug, layer="clean": {
            "read_paths": ["gs://dataciviclab-mart/x.parquet"],
            "gs_path": "gs://dataciviclab-mart/rifiuti/mart_comuni.parquet",
        },
    )
    content = context_tools.explore(ref="ispra_ru_base__mart_comuni")["content"]

    assert content["type"] == "mart"
    assert [c["name"] for c in content["columns"]] == ["anno"]
    assert content["primary_key"] == ["anno"]
    assert content["gs_path"] == "gs://dataciviclab-mart/rifiuti/mart_comuni.parquet"
    assert 'layer="mart"' in content["query_hint"]


@pytest.mark.contract
def test_explore_mart_describes_parquet_when_registry_empty(monkeypatch):
    """I registry non committano colonne mart: DESCRIBE sul parquet (audit 4)."""
    fake = FakeHttpClient()
    idx = json.loads(json.dumps(SAMPLE_INDEX))
    idx["datasets"]["ispra"][0]["mart_refs"] = ["ispra_ru_base__mart_comuni"]
    _patch_fetch(fake, "topic_index.json", text=json.dumps(idx))
    _use_fake(fake)

    monkeypatch.setattr("dcl_mcp.gcs.resolver.mart_card_detail", lambda repo, slug: None)
    monkeypatch.setattr(
        "dcl_mcp.gcs.resolver.resolve",
        lambda topic, slug, layer="clean": {
            "read_paths": ["gs://dataciviclab-mart/x.parquet"],
            "gs_path": "gs://dataciviclab-mart/eurostat/x/mart_expenditure.parquet",
        },
    )
    monkeypatch.setattr(
        "dcl_mcp.gcs.resolver.describe_parquet_columns",
        lambda paths: [{"name": "pil", "type": "DOUBLE"}, {"name": "export", "type": "DOUBLE"}],
    )
    content = context_tools.explore(ref="ispra_ru_base__mart_comuni")["content"]

    assert content["type"] == "mart"
    assert [c["name"] for c in content["columns"]] == ["pil", "export"]
    assert "note" not in content
    # gcs_path e query_hint devono puntare al mart, non al clean parent
    assert content["gs_path"] == "gs://dataciviclab-mart/eurostat/x/mart_expenditure.parquet"
    assert 'layer="mart"' in content["query_hint"]


@pytest.mark.contract
def test_explore_mart_without_schema_drops_parent_columns(monkeypatch):
    """Né registry né DESCRIBE: niente colonne/path del clean al posto di quelli del mart."""
    fake = FakeHttpClient()
    _patch_fetch(fake, "topic_index.json", text=json.dumps(SAMPLE_INDEX))
    _use_fake(fake)
    monkeypatch.setattr("dcl_mcp.gcs.resolver.mart_card_detail", lambda repo, slug: None)
    monkeypatch.setattr(
        "dcl_mcp.gcs.resolver.resolve",
        lambda topic, slug, layer="clean": (_ for _ in ()).throw(GcsResolveError("nope")),
    )
    content = context_tools.explore(ref="ispra_ru_base__mart_comuni")["content"]

    assert content["type"] == "mart"
    assert "columns" not in content
    assert "gs_path" not in content
    assert "query_hint" not in content
    assert "SELECT * FROM _data LIMIT 1" in content["note"]


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
                        "title": "feat: add rifiuti dataset",
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
    # Default: solo aperte (is:open) — le chiuse sporcano il segnale
    _, url, kwargs = fake.requests[0]
    assert "is:open" in kwargs["params"]["q"]


@pytest.mark.adapter
def test_find_include_github_merges_discussions(monkeypatch):
    """include_github=true aggiunge le top discussioni org-wide (GraphQL)."""
    fake = FakeHttpClient()
    _patch_fetch(fake, "topic_index.json", text=json.dumps(SAMPLE_INDEX))
    search_url = "https://api.github.com/search/issues"
    fake.responses[search_url] = HttpResult(
        response=fake_response(200, json_data={"items": []}), err=None
    )
    _use_fake(fake)

    def fake_graphql(query, variables):
        return {
            "search": {
                "discussionCount": 1,
                "nodes": [
                    {
                        "number": 443,
                        "title": "Rifiuti urbani",
                        "url": "https://github.com/orgs/dataciviclab/discussions/443",
                        "updatedAt": "2026-10-01T00:00:00Z",
                        "answerChosenAt": None,
                        "closed": False,
                        "category": {"name": "Domanda"},
                        "repository": {"nameWithOwner": "dataciviclab/rifiuti-urbani"},
                    }
                ],
            }
        }

    seen: dict = {}

    def fake_graphql_capture(query, variables):
        seen.update(variables)
        return fake_graphql(query, variables)

    monkeypatch.setattr(context_tools, "discussions_graphql", fake_graphql_capture)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(app, "_ENV_LOADED", True)

    content = context_tools.find(query="rifiuti", include_github=True)["content"]

    assert content["discussions"][0]["number"] == 443
    assert content["discussions"][0]["type"] == "discussion"
    assert content["discussions"][0]["repo"] == "dataciviclab/rifiuti-urbani"
    # Discussion search di default solo aperte
    assert "is:open" in seen["query"]


@pytest.mark.adapter
def test_search_github_issues_filters_by_title_tokens():
    """Solo issue il cui titolo contiene tutti i token (audit giro 4)."""
    fake = FakeHttpClient()
    url = "https://api.github.com/search/issues"
    fake.responses[url] = HttpResult(
        response=fake_response(
            200,
            json_data={
                "items": [
                    {
                        "number": 1,
                        "title": "PNRR: report spesa farmaceutica 2024",
                        "state": "open",
                        "html_url": "https://github.com/dataciviclab/r/pull/1",
                        "repository_url": "https://api.github.com/repos/dataciviclab/r",
                        "pull_request": {},
                    },
                    {
                        "number": 2,
                        "title": "Riordino script italia-corpus",
                        "state": "open",
                        "html_url": "https://github.com/dataciviclab/r/issues/2",
                        "repository_url": "https://api.github.com/repos/dataciviclab/r",
                    },
                ]
            },
        ),
        err=None,
    )
    _use_fake(fake)
    # "pnrr" c'è solo nel titolo #1; "script" solo nel #2 → nessuna hit con entrambi
    assert context_tools._search_github_issues("pnrr script", "tok") == []
    # query con un solo token → solo la issue pertinente
    fake2 = FakeHttpClient()
    fake2.responses[url] = fake.responses[url]
    _use_fake(fake2)
    result = context_tools._search_github_issues("pnrr", "tok")
    assert [i["number"] for i in result] == [1]


@pytest.mark.adapter
def test_search_github_discussions_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(app, "_ENV_LOADED", True)
    assert context_tools._search_github_discussions("x", None) == []


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
