"""Contratto guard: ogni risposta ha ``ok``, anche gli errori."""

from __future__ import annotations

import pytest
from lab_connectors.mcp import ErrorCode, McpError

from dcl_mcp.guards import guard_ok

pytestmark = pytest.mark.contract


def test_success_gets_ok_true():
    assert guard_ok(lambda: {"value": 1}, "t") == {"value": 1, "ok": True}


def test_success_keeps_existing_ok():
    assert guard_ok(lambda: {"ok": False, "error": "x"}, "t")["ok"] is False


def test_mcp_error_gets_ok_false():
    def boom() -> dict:
        raise McpError(ErrorCode.INVALID_PARAMS, "male")

    result = guard_ok(boom, "t")
    assert result["ok"] is False
    assert result["error"] == "invalid_params"
    assert result["message"] == "male"


def test_unexpected_error_gets_ok_false():
    def boom() -> dict:
        raise RuntimeError("kaboom")

    result = guard_ok(boom, "t")
    assert result["ok"] is False
    assert result["error"] == "unexpected_error"
