"""Tests for the le5le login flow in blender_mcp.auth (no Blender required)."""

from __future__ import annotations

import os

import pytest

from blender_mcp import auth


@pytest.fixture()
def manager(monkeypatch, tmp_path):
    """A fresh global auth manager with profile fetching stubbed out."""
    monkeypatch.setattr(auth, "_state_path", lambda: tmp_path / "auth.json")
    auth.get_auth_manager().logout()
    monkeypatch.setattr(
        auth, "fetch_profile", lambda token: {"code": 200, "data": {"username": f"user-{token}"}}
    )
    yield auth.get_auth_manager()
    auth.get_auth_manager().logout()


class TestBuildLoginUrl:
    def test_default(self, monkeypatch):
        for var in (auth.LOGIN_URL_ENV, auth.CALLBACK_URL_ENV, auth.AUTH_PORT_ENV, "MCP_PORT"):
            monkeypatch.delenv(var, raising=False)
        url = auth.build_login_url()
        assert url.startswith(auth.DEFAULT_LOGIN_URL + "?")
        assert "cb=http%3A%2F%2Flocalhost%3A8080%2Fauth%2Fcallback" in url

    def test_login_url_env_override_with_existing_query(self, monkeypatch):
        monkeypatch.setenv(auth.LOGIN_URL_ENV, "https://example.com/login?lang=zh")
        monkeypatch.setenv(auth.CALLBACK_URL_ENV, "http://localhost:9999/auth/callback")
        url = auth.build_login_url()
        assert url.startswith("https://example.com/login?lang=zh&")
        assert "cb=http%3A%2F%2Flocalhost%3A9999%2Fauth%2Fcallback" in url

    def test_auth_port_env_override(self, monkeypatch):
        monkeypatch.delenv(auth.CALLBACK_URL_ENV, raising=False)
        monkeypatch.setenv(auth.AUTH_PORT_ENV, "9090")
        assert auth.get_callback_url() == "http://localhost:9090/auth/callback"

    def test_mcp_port_used_as_fallback(self, monkeypatch):
        monkeypatch.delenv(auth.CALLBACK_URL_ENV, raising=False)
        monkeypatch.delenv(auth.AUTH_PORT_ENV, raising=False)
        monkeypatch.setenv("MCP_PORT", "18080")
        assert auth.get_callback_url() == "http://localhost:18080/auth/callback"


class TestExtractUsername:
    def test_bare_object(self):
        assert auth.extract_username({"username": "alice"}) == "alice"

    def test_data_envelope(self):
        assert auth.extract_username({"code": 200, "data": {"username": "bob"}}) == "bob"

    def test_fallback_keys(self):
        assert auth.extract_username({"data": {"nickname": "carol"}}) == "carol"

    def test_missing(self):
        assert auth.extract_username({"code": 200, "data": {}}) is None
        assert auth.extract_username("not a dict") is None
        assert auth.extract_username(None) is None


class TestExtractAvatar:
    def test_bare_object(self):
        assert auth.extract_avatar({"avatarUrl": "https://cdn.example.com/a.png"}) == "https://cdn.example.com/a.png"

    def test_data_envelope(self):
        payload = {"code": 200, "data": {"avatarUrl": "https://cdn.example.com/b.png"}}
        assert auth.extract_avatar(payload) == "https://cdn.example.com/b.png"

    def test_fallback_keys(self):
        assert auth.extract_avatar({"avatar": "https://cdn.example.com/c.png"}) == "https://cdn.example.com/c.png"

    def test_rejects_non_http(self):
        assert auth.extract_avatar({"avatarUrl": "javascript:alert(1)"}) is None
        assert auth.extract_avatar({"avatarUrl": "/relative/path.png"}) is None

    def test_missing(self):
        assert auth.extract_avatar({"data": {}}) is None
        assert auth.extract_avatar(None) is None


class TestAuthManager:
    def test_login_stores_token_and_profile(self, manager):
        manager.login("jwt123")
        assert manager.token == "jwt123"
        assert manager.username == "user-jwt123"
        status = manager.status()
        assert status["logged_in"] is True
        assert status["error"] is None

    def test_profile_failure_keeps_token(self, monkeypatch):
        manager = auth.get_auth_manager()
        manager.logout()
        monkeypatch.setattr(
            auth, "fetch_profile", lambda token: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        try:
            manager.login("jwt-bad")
            assert manager.token == "jwt-bad"
            assert manager.status()["logged_in"] is True
            assert manager.username is None
            assert "boom" in manager.status()["error"]
        finally:
            manager.logout()

    def test_logout_clears_state(self, manager):
        manager.login("jwt123")
        manager.logout()
        status = manager.status()
        assert status["logged_in"] is False
        assert status["username"] is None


class TestPersistence:
    def test_login_persists_and_restores(self, manager, tmp_path):
        manager.login("jwt-persist")
        state_file = tmp_path / "auth.json"
        assert state_file.is_file()
        if os.name != "nt":
            assert (state_file.stat().st_mode & 0o777) == 0o600

        # Simulate a server restart: in-memory state gone, file still there.
        with manager._lock:
            manager._token = None
            manager._user_info = None

        assert manager.load_persisted_state() is True
        assert manager.token == "jwt-persist"
        assert manager.username == "user-jwt-persist"

    def test_logout_removes_state_file(self, manager, tmp_path):
        manager.login("jwt-persist")
        assert (tmp_path / "auth.json").is_file()
        manager.logout()
        assert not (tmp_path / "auth.json").exists()

    def test_load_without_file_returns_false(self, manager):
        assert manager.load_persisted_state() is False
        assert manager.token is None

    def test_restore_keeps_cached_profile_when_refresh_fails(self, manager, monkeypatch, tmp_path):
        manager.login("jwt-persist")
        with manager._lock:
            manager._token = None
            manager._user_info = None
        monkeypatch.setattr(
            auth, "fetch_profile", lambda token: (_ for _ in ()).throw(RuntimeError("offline"))
        )
        assert manager.load_persisted_state() is True
        assert manager.username == "user-jwt-persist"


class TestHttpEndpoints:
    @pytest.fixture()
    def client(self, manager):
        from starlette.applications import Starlette
        from starlette.testclient import TestClient

        app = Starlette(routes=auth.auth_routes())
        return TestClient(app)

    def test_callback_requires_token(self, client):
        response = client.get("/auth/callback")
        assert response.status_code == 400

    def test_callback_stores_token_and_profile(self, client, manager):
        response = client.get("/auth/callback", params={"token": "abc"})
        assert response.status_code == 200
        assert "user-abc" in response.text
        assert manager.token == "abc"

    def test_callback_page_shows_avatar(self, client, manager, monkeypatch):
        monkeypatch.setattr(
            auth, "fetch_profile",
            lambda token: {"code": 200, "data": {
                "username": "alice",
                "avatarUrl": "https://cdn.example.com/alice.png",
            }},
        )
        response = client.get("/auth/callback", params={"token": "abc"})
        assert response.status_code == 200
        assert 'class="avatar"' in response.text
        assert "https://cdn.example.com/alice.png" in response.text
        assert manager.avatar_url == "https://cdn.example.com/alice.png"
        assert manager.status()["avatar_url"] == "https://cdn.example.com/alice.png"

    def test_callback_page_escapes_html(self, client, manager, monkeypatch):
        monkeypatch.setattr(
            auth, "fetch_profile",
            lambda token: {"username": "<script>alert(1)</script>"},
        )
        response = client.get("/auth/callback", params={"token": "abc"})
        assert response.status_code == 200
        assert "<script>alert(1)</script>" not in response.text
        assert "&lt;script&gt;" in response.text

    def test_status_reports_login_state(self, client, manager):
        response = client.get("/auth/status")
        assert response.status_code == 200
        assert response.json()["logged_in"] is False

        manager.login("xyz")
        payload = client.get("/auth/status").json()
        assert payload["logged_in"] is True
        assert payload["username"] == "user-xyz"

    def test_logout(self, client, manager):
        manager.login("xyz")
        response = client.get("/auth/logout")
        assert response.status_code == 200
        assert response.json()["logged_in"] is False
        assert manager.token is None

    def test_login_redirects_to_login_page(self, client, monkeypatch):
        monkeypatch.setenv(auth.LOGIN_URL_ENV, "https://example.com/login")
        response = client.get("/auth/login", follow_redirects=False)
        assert response.status_code in (302, 307)
        assert response.headers["location"].startswith("https://example.com/login?")

    def test_callback_with_profile_error_returns_502(self, client, monkeypatch):
        monkeypatch.setattr(
            auth, "fetch_profile", lambda token: (_ for _ in ()).throw(RuntimeError("down"))
        )
        response = client.get("/auth/callback", params={"token": "abc"})
        assert response.status_code == 502
        # The token is still stored - the user is authenticated.
        assert auth.get_auth_manager().token == "abc"


class TestStatusListener:
    def test_login_and_logout_notify(self, manager, monkeypatch):
        calls = []
        monkeypatch.setattr(auth, "_status_listener", lambda status: calls.append(dict(status)))
        manager.login("abc")
        manager.logout()
        assert len(calls) == 2
        assert calls[0]["logged_in"] is True
        assert calls[0]["username"] == "user-abc"
        assert calls[1]["logged_in"] is False

    def test_listener_errors_are_swallowed(self, manager, monkeypatch):
        def boom(_status):
            raise RuntimeError("no blender")
        monkeypatch.setattr(auth, "_status_listener", boom)
        manager.login("abc")  # must not raise
        assert manager.token == "abc"


def test_push_auth_state_sends_socket_command():
    from unittest.mock import MagicMock

    from blender_mcp import server

    blender = MagicMock()
    server._push_auth_state(
        status={"logged_in": True, "username": "alice"}, blender=blender
    )
    blender.send_command.assert_called_once_with(
        "set_auth_info", {"logged_in": True, "username": "alice"}
    )


def test_push_auth_state_survives_blender_errors():
    from unittest.mock import MagicMock

    from blender_mcp import server

    blender = MagicMock()
    blender.send_command.side_effect = Exception("not connected")
    server._push_auth_state(
        status={"logged_in": False, "username": ""}, blender=blender
    )  # must not raise


def test_register_auth_routes_with_fastmcp():
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test-auth")
    assert auth.register_auth_routes(mcp) is True


def test_sse_app_serves_auth_routes(manager):
    """The SSE starlette app must expose the auth endpoints alongside /sse."""
    from mcp.server.fastmcp import FastMCP
    from starlette.testclient import TestClient

    mcp = FastMCP("test-auth-sse")
    assert auth.register_auth_routes(mcp) is True
    client = TestClient(mcp.sse_app())

    assert client.get("/auth/status").json()["logged_in"] is False

    response = client.get("/auth/callback", params={"token": "tok"})
    assert response.status_code == 200
    assert manager.username == "user-tok"
