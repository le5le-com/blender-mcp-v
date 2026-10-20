"""Tests for blender_mcp.env.load_dotenv."""

from __future__ import annotations

import os
import sys

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


def _freeze(monkeypatch, exe_dir):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "blender-mcp"))


def test_frozen_loads_env_next_to_executable(tmp_path, monkeypatch):
    exe_dir = tmp_path / "dist"
    exe_dir.mkdir()
    env_file = exe_dir / ".env"
    env_file.write_text("BLENDERMCP_FROZEN_MARKER=frozen\n", encoding="utf-8")
    _freeze(monkeypatch, exe_dir)
    monkeypatch.delenv(ENV_FILE_ENV, raising=False)
    monkeypatch.delenv("BLENDERMCP_FROZEN_MARKER", raising=False)
    monkeypatch.chdir(tmp_path)  # cwd has no .env, like a double-clicked app

    assert load_dotenv() == env_file
    assert os.environ["BLENDERMCP_FROZEN_MARKER"] == "frozen"


def test_frozen_executable_env_wins_over_cwd(tmp_path, monkeypatch):
    exe_dir = tmp_path / "dist"
    exe_dir.mkdir()
    exe_env = exe_dir / ".env"
    exe_env.write_text("BLENDERMCP_MARKER=exe\n", encoding="utf-8")
    (tmp_path / ".env").write_text("BLENDERMCP_MARKER=cwd\n", encoding="utf-8")
    _freeze(monkeypatch, exe_dir)
    monkeypatch.delenv(ENV_FILE_ENV, raising=False)
    monkeypatch.delenv("BLENDERMCP_MARKER", raising=False)
    monkeypatch.chdir(tmp_path)

    assert load_dotenv() == exe_env
    assert os.environ["BLENDERMCP_MARKER"] == "exe"


def test_not_frozen_ignores_executable_dir(tmp_path, monkeypatch):
    exe_dir = tmp_path / "dist"
    exe_dir.mkdir()
    (exe_dir / ".env").write_text("BLENDERMCP_MARKER=exe\n", encoding="utf-8")
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "blender-mcp"))
    monkeypatch.delenv(ENV_FILE_ENV, raising=False)
    monkeypatch.delenv("BLENDERMCP_MARKER", raising=False)
    monkeypatch.chdir(tmp_path)  # no cwd .env either

    assert load_dotenv() is None
    assert "BLENDERMCP_MARKER" not in os.environ
