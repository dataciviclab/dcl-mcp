"""Contratto pubblico: nomi tool registrati sul server unico."""

from __future__ import annotations

import asyncio

import pytest

from dcl_mcp.server import mcp

pytestmark = pytest.mark.contract

EXPECTED_TOOLS = sorted(
    [
        # context (ex dataciviclab-context) — legacy topic_index/search droppati
        "session_bootstrap",
        "workspace_triage",
        "lab_map",
        "find",
        "explore",
        "refresh_context",
        # discussions (ex github-discussions server)
        "github_list_discussions",
        "github_list_discussion_categories",
        "github_get_discussion",
        "github_get_discussion_summary",
        "github_get_discussion_comments",
        "github_search_discussions",
        "github_create_discussion",
        "github_add_discussion_comment",
        # gcs
        "gcs_query",
    ]
)


def test_all_tools_registered():
    tools = asyncio.run(mcp.list_tools())
    names = sorted(t.name for t in tools)
    assert names == EXPECTED_TOOLS, f"Mismatch: {names} vs {EXPECTED_TOOLS}"


def test_server_name():
    assert mcp.name == "dataciviclab"
