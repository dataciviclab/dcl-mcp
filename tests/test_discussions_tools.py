"""Contratto del layer tool discussions: default repo e envelope ``ok``."""

from __future__ import annotations

from typing import Any

import pytest

import dcl_mcp.discussions.tools as disc_tools

pytestmark = pytest.mark.contract

HUB = "dataciviclab/dataciviclab"

_CLIENT_FUNCS = (
    "list_discussions",
    "list_discussion_categories",
    "get_discussion",
    "create_discussion",
    "add_discussion_comment",
)


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen["args"] = args
        return {"echo": True}

    for name in _CLIENT_FUNCS:
        monkeypatch.setattr(disc_tools, name, fake)
    return seen


def test_list_uses_hub_default(monkeypatch):
    seen = _capture(monkeypatch)
    result = disc_tools.github_list_discussions()
    assert seen["args"][0] == HUB
    assert result["ok"] is True


def test_get_uses_hub_default_and_number_first(monkeypatch):
    seen = _capture(monkeypatch)
    disc_tools.github_get_discussion(number=42)
    assert seen["args"][:2] == (HUB, 42)


def test_create_uses_hub_default(monkeypatch):
    seen = _capture(monkeypatch)
    disc_tools.github_create_discussion(category_name="Domanda", title="T", body="B")
    assert seen["args"][0] == HUB


def test_add_comment_uses_hub_default(monkeypatch):
    seen = _capture(monkeypatch)
    disc_tools.github_add_discussion_comment(number=42, body="B")
    assert seen["args"][0] == HUB


def test_categories_uses_hub_default(monkeypatch):
    seen = _capture(monkeypatch)
    disc_tools.github_list_discussion_categories()
    assert seen["args"][0] == HUB


def test_error_has_ok_false(monkeypatch):
    def boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(disc_tools, "list_discussions", boom)
    result = disc_tools.github_list_discussions()
    assert result["ok"] is False
    assert result["error"] == "unexpected_error"
