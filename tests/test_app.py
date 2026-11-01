"""app.py — caricamento `.env`, token GitHub e client HTTP condiviso."""

from __future__ import annotations

import pytest

import dcl_mcp.app as app


@pytest.fixture(autouse=True)
def _reset_app(monkeypatch):
    """Stato globale isolato: niente .env reale del workspace, niente client."""
    monkeypatch.setattr(app, "_ENV_LOADED", False)
    monkeypatch.setattr(app, "_http", None)
    for var in ("DCL_MCP_ENV_FILE", "ACB_ENV_FILE", *app.GITHUB_TOKEN_ENV_VARS):
        monkeypatch.delenv(var, raising=False)
    yield


def _only_path(monkeypatch, path):
    """Isola la ricerca del .env: solo il file passato."""
    monkeypatch.setattr(app, "_candidate_env_paths", lambda: [path])


# ── _parse_env_line ───────────────────────────────────────────────────────────


@pytest.mark.pure_unit
def test_parse_env_line_cases():
    assert app._parse_env_line("FOO=bar") == ("FOO", "bar")
    assert app._parse_env_line('  FOO="bar baz"  ') == ("FOO", "bar baz")
    assert app._parse_env_line("FOO='bar'") == ("FOO", "bar")
    assert app._parse_env_line("FOO=bar=baz") == ("FOO", "bar=baz")
    assert app._parse_env_line("# comment") is None
    assert app._parse_env_line("   ") is None
    assert app._parse_env_line("senza_uguale") is None
    assert app._parse_env_line("=valore") is None


# ── load_env ──────────────────────────────────────────────────────────────────


@pytest.mark.policy
def test_load_env_reads_file_without_overriding(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DA_FILE=uno\nGIÀ_IMPOSTATA=dal-file\n", encoding="utf-8")
    _only_path(monkeypatch, env_file)
    monkeypatch.setenv("GIÀ_IMPOSTATA", "dall-ambiente")

    assert app.load_env() is True
    assert app.get_env("DA_FILE") == "uno"
    # L'ambiente vince sul file.
    assert app.get_env("GIÀ_IMPOSTATA") == "dall-ambiente"


@pytest.mark.policy
def test_load_env_is_idempotent(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("PRIMO=1\n", encoding="utf-8")
    _only_path(monkeypatch, env_file)

    assert app.load_env() is True
    assert app.get_env("PRIMO") == "1"

    # Il file cambia, ma il caricamento non viene ripetuto.
    env_file.write_text("PRIMO=2\nSECONDO=2\n", encoding="utf-8")
    assert app.load_env() is True
    assert app.get_env("PRIMO") == "1"
    assert app.get_env("SECONDO") is None


@pytest.mark.policy
def test_load_env_missing_file_is_not_an_error(monkeypatch, tmp_path):
    _only_path(monkeypatch, tmp_path / "assente.env")
    assert app.load_env() is False
    assert app.get_env("QUALSIASI") is None


@pytest.mark.policy
def test_explicit_env_file_comes_first(monkeypatch, tmp_path):
    explicit = tmp_path / "esplicito.env"
    monkeypatch.setenv("DCL_MCP_ENV_FILE", str(explicit))

    paths = app._candidate_env_paths()

    assert paths[0] == explicit.resolve()


@pytest.mark.policy
def test_get_env_loads_lazily(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LAZY=ok\n", encoding="utf-8")
    _only_path(monkeypatch, env_file)

    # Nessuna chiamata esplicita a load_env(): get_env la fa per noi.
    assert app.get_env("LAZY") == "ok"


# ── github_token ──────────────────────────────────────────────────────────────


@pytest.mark.contract
def test_github_token_fallback_order(monkeypatch, tmp_path):
    _only_path(monkeypatch, tmp_path / "assente.env")
    assert app.github_token() is None

    monkeypatch.setenv("GH_TOKEN", "terzo")
    assert app.github_token() == "terzo"

    monkeypatch.setenv("GITHUB_TOKEN", "secondo")
    assert app.github_token() == "secondo"

    monkeypatch.setenv("GITHUB_PERSONAL_ACCESS_TOKEN", "primo")
    assert app.github_token() == "primo"


@pytest.mark.contract
def test_github_token_from_env_file(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("GITHUB_TOKEN=dal-file\n", encoding="utf-8")
    _only_path(monkeypatch, env_file)

    assert app.github_token() == "dal-file"


# ── get_http ──────────────────────────────────────────────────────────────────


@pytest.mark.policy
def test_get_http_is_a_singleton():
    first = app.get_http()
    assert app.get_http() is first
