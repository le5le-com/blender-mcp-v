"""Tests for blender_mcp.env.load_dotenv."""

from __future__ import annotations

import os

from blender_mcp.env import ENV_FILE_ENV, load_dotenv


def test_loads_key_value_pairs(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "\n"
        "MCP_PORT=8080\n"
        'BLENDERMCP_UPLOAD_URL="https://account.le5le.com/api/file/upload"\n'
        "export MCP_TRANSPORT=sse\n"
        "SINGLE_QUOTED='value with = sign'\n"
        "NOT_A_PAIR\n"
        "=orphan\n",
        encoding="utf-8",
    )
    for key in ("MCP_PORT", "BLENDERMCP_UPLOAD_URL", "MCP_TRANSPORT", "SINGLE_QUOTED"):
        monkeypatch.delenv(key, raising=False)

    result = load_dotenv(str(env_file))

    assert result == env_file
    assert os.environ["MCP_PORT"] == "8080"
    assert os.environ["BLENDERMCP_UPLOAD_URL"] == "https://account.le5le.com/api/file/upload"
    assert os.environ["MCP_TRANSPORT"] == "sse"
    assert os.environ["SINGLE_QUOTED"] == "value with = sign"
    assert "NOT_A_PAIR" not in os.environ


def test_existing_env_vars_win(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("MCP_PORT=9999\n", encoding="utf-8")
    monkeypatch.setenv("MCP_PORT", "1234")

    load_dotenv(str(env_file))

    assert os.environ["MCP_PORT"] == "1234"


def test_missing_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_FILE_ENV, raising=False)
    assert load_dotenv(str(tmp_path / "nope.env")) is None


def test_env_file_env_var_points_elsewhere(tmp_path, monkeypatch):
    env_file = tmp_path / "custom.env"
    env_file.write_text("BLENDERMCP_CUSTOM_MARKER=hello\n", encoding="utf-8")
    monkeypatch.setenv(ENV_FILE_ENV, str(env_file))
    monkeypatch.delenv("BLENDERMCP_CUSTOM_MARKER", raising=False)
    monkeypatch.chdir(tmp_path)  # no local .env here; BLENDERMCP_ENV_FILE must win

    assert load_dotenv() == env_file
    assert os.environ["BLENDERMCP_CUSTOM_MARKER"] == "hello"
