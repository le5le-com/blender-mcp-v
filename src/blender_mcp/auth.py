"""le5le account login, owned by the MCP server.

Flow:
1. The Blender addon opens the login page (BLENDERMCP_LOGIN_URL) in the user's
   browser, passing the callback URL as the `cb` query parameter (the login
   page reads `route.query.cb`).
2. After login the page redirects back to the callback URL with the JWT as
   `?token=<jwt>` (or `&token=<jwt>` when the callback already has a query).
3. The /auth/callback endpoint here stores the token and fetches the user
   profile from BLENDERMCP_PROFILE_URL (`?token=<jwt>`).
4. The Blender addon polls /auth/status to display the logged-in username.

The endpoints are served on the same HTTP server as the SSE transport when
MCP_TRANSPORT=sse; with stdio transport a small standalone HTTP server is
started on the auth port instead.
"""

import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

import anyio.to_thread
import httpx

logger = logging.getLogger("BlenderMCPServer")

LOGIN_URL_ENV = "BLENDERMCP_LOGIN_URL"
PROFILE_URL_ENV = "BLENDERMCP_PROFILE_URL"
CALLBACK_URL_ENV = "BLENDERMCP_AUTH_CALLBACK_URL"
AUTH_PORT_ENV = "BLENDERMCP_AUTH_PORT"

DEFAULT_LOGIN_URL = "https://account.le5le.com/login"
DEFAULT_PROFILE_URL = "https://v.le5le.com/api/account/profile"
DEFAULT_AUTH_PORT = 8080

PROFILE_TIMEOUT = 15.0


def _state_path() -> Path:
    """Where the login token is persisted. Next to the consent state."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "blender-mcp" / "auth.json"


def get_login_url() -> str:
    return os.getenv(LOGIN_URL_ENV, DEFAULT_LOGIN_URL)


def get_profile_url() -> str:
    return os.getenv(PROFILE_URL_ENV, DEFAULT_PROFILE_URL)


def get_auth_port() -> int:
    return int(os.getenv(AUTH_PORT_ENV) or os.getenv("MCP_PORT") or DEFAULT_AUTH_PORT)


def get_callback_url() -> str:
    explicit = os.getenv(CALLBACK_URL_ENV)
    if explicit:
        return explicit
    # localhost, not MCP_HOST: the URL is opened in the user's own browser, and
    # 0.0.0.0 is not a browsable address.
    return f"http://localhost:{get_auth_port()}/auth/callback"


def build_login_url() -> str:
    """Login page URL with the callback attached as `cb`.

    The le5le login page reads the redirect target from `route.query.cb` and
    after login redirects to it with `?token=<jwt>` appended.
    """
    login_url = get_login_url()
    quoted_callback = quote(get_callback_url(), safe="")
    sep = "&" if "?" in login_url else "?"
    print(f"{login_url}{sep}cb={quoted_callback}")
    return f"{login_url}{sep}cb={quoted_callback}"


def extract_username(payload: Any) -> Optional[str]:
    """Pull a display name out of the profile response, tolerating wrappers.

    Handles both a bare user object ({\"username\": ...}) and the common
    {\"code\": ..., \"data\": {...}} envelope.
    """
    if not isinstance(payload, dict):
        return None
    candidates = [payload]
    nested = payload.get("data")
    if isinstance(nested, dict):
        candidates.append(nested)
    for candidate in candidates:
        for key in ("username", "nickname", "name"):
            value = candidate.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def extract_avatar(payload: Any) -> Optional[str]:
    """Pull the avatar URL out of the profile response, tolerating wrappers.

    Only http(s) URLs are accepted - the value ends up in an HTML attribute.
    """
    if not isinstance(payload, dict):
        return None
    candidates = [payload]
    nested = payload.get("data")
    if isinstance(nested, dict):
        candidates.append(nested)
    for candidate in candidates:
        for key in ("avatarUrl", "avatar_url", "avatar"):
            value = candidate.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
    return None


def fetch_profile(token: str) -> Dict[str, Any]:
    """Fetch the user profile for a JWT. Raises on HTTP/JSON errors."""
    url = get_profile_url()
    sep = "&" if "?" in url else "?"
    response = httpx.get(f"{url}{sep}token={quote(token, safe='')}", timeout=PROFILE_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Profile endpoint did not return a JSON object")
    return payload


class AuthManager:
    """Thread-safe holder for the JWT and the fetched user profile."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self._user_info: Optional[Dict[str, Any]] = None
        self._error: Optional[str] = None

    def login(self, token: str) -> None:
        """Store the token and fetch the profile.

        The token stays stored even when the profile fetch fails: the user is
        authenticated, we just cannot show who they are. State is persisted to
        disk so a server restart does not log the user out.
        """
        try:
            user_info = fetch_profile(token)
            error = None
        except Exception as e:
            logger.warning(f"Profile fetch failed after login: {e}")
            user_info = None
            error = str(e)
        with self._lock:
            self._token = token
            self._user_info = user_info
            self._error = error
        self._save_state()
        logger.info(
            f"le5le login: token stored, user={self.username or 'unknown'}"
            + (f" (profile error: {error})" if error else "")
        )
        _notify_status()

    def logout(self) -> None:
        with self._lock:
            self._token = None
            self._user_info = None
            self._error = None
        try:
            _state_path().unlink(missing_ok=True)
        except OSError as e:
            logger.debug(f"Could not remove auth state file: {e}")
        _notify_status()

    def _save_state(self) -> None:
        with self._lock:
            state = {"token": self._token, "user_info": self._user_info}
        try:
            path = _state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            # The file holds a JWT: create it readable only by the user.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                import json

                json.dump(state, f)
        except OSError as e:
            logger.warning(f"Could not persist auth state: {e}")

    def load_persisted_state(self) -> bool:
        """Restore the token saved by a previous run. Returns True if restored.

        The profile is refreshed from the account server; when that fails the
        cached profile is kept so the user still shows as logged in.
        """
        try:
            import json

            state = json.loads(_state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        token = state.get("token")
        if not token:
            return False
        with self._lock:
            self._token = token
            self._user_info = state.get("user_info")
            self._error = None
        try:
            refreshed = fetch_profile(token)
            with self._lock:
                self._user_info = refreshed
        except Exception as e:
            logger.info(f"Profile refresh on startup failed (keeping cached): {e}")
        logger.info(f"le5le login restored from disk, user={self.username or 'unknown'}")
        return True

    @property
    def token(self) -> Optional[str]:
        with self._lock:
            return self._token

    @property
    def username(self) -> Optional[str]:
        with self._lock:
            return extract_username(self._user_info)

    @property
    def avatar_url(self) -> Optional[str]:
        with self._lock:
            return extract_avatar(self._user_info)

    @property
    def error(self) -> Optional[str]:
        with self._lock:
            return self._error

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "logged_in": self._token is not None,
                "username": extract_username(self._user_info),
                "avatar_url": extract_avatar(self._user_info),
                "user_info": self._user_info,
                "error": self._error,
            }


_auth_manager = AuthManager()


def get_auth_manager() -> AuthManager:
    return _auth_manager


# Called with the status dict after every login/logout. server.py registers a
# pusher here that forwards the state to the Blender addon over its socket, so
# the addon never has to poll.
_status_listener = None


def set_status_listener(listener) -> None:
    global _status_listener
    _status_listener = listener


def _notify_status() -> None:
    if _status_listener is None:
        return
    try:
        _status_listener(_auth_manager.status())
    except Exception as e:
        logger.debug(f"Auth status listener failed: {e}")


#region HTTP endpoints (starlette)

_PAGE_STYLE = """
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC',
                 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
    min-height: 100vh; display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #d946ef 100%);
  }
  .card {
    background: rgba(255, 255, 255, 0.96); backdrop-filter: blur(12px);
    border-radius: 24px; padding: 48px 56px; text-align: center;
    box-shadow: 0 24px 64px rgba(30, 27, 75, 0.35);
    animation: pop .5s cubic-bezier(.2, .9, .3, 1.15); max-width: 420px;
  }
  @keyframes pop {
    from { opacity: 0; transform: translateY(24px) scale(.94); }
    to   { opacity: 1; transform: none; }
  }
  .avatar-wrap { position: relative; width: 104px; height: 104px; margin: 0 auto; }
  .avatar {
    width: 104px; height: 104px; border-radius: 50%; object-fit: cover;
    border: 4px solid #fff; box-shadow: 0 8px 24px rgba(0, 0, 0, .18);
  }
  .avatar-fallback {
    width: 104px; height: 104px; border-radius: 50%; display: flex;
    align-items: center; justify-content: center; margin: 0 auto;
    font-size: 44px; color: #fff;
    background: linear-gradient(135deg, #34d399, #10b981);
    box-shadow: 0 12px 28px rgba(16, 185, 129, .45);
  }
  .avatar-fallback.error {
    background: linear-gradient(135deg, #f87171, #ef4444);
    box-shadow: 0 12px 28px rgba(239, 68, 68, .45);
  }
  .check {
    position: absolute; right: -2px; bottom: -2px; width: 32px; height: 32px;
    border-radius: 50%; background: #10b981; color: #fff; font-size: 18px;
    display: flex; align-items: center; justify-content: center;
    border: 3px solid #fff; box-shadow: 0 4px 10px rgba(16, 185, 129, .5);
  }
  h1 { font-size: 24px; color: #111827; margin: 20px 0 6px; font-weight: 700; }
  .username { font-size: 17px; color: #6366f1; font-weight: 600; margin-bottom: 8px; }
  p { color: #6b7280; font-size: 14px; line-height: 1.7; }
  .footer { margin-top: 28px; font-size: 12px; color: #9ca3af; letter-spacing: 1px; }
"""


def _html_page(title: str, message: str, ok: bool = True,
               username: Optional[str] = None, avatar_url: Optional[str] = None) -> str:
    from html import escape

    title = escape(title)
    message = escape(message)

    if ok and avatar_url:
        visual = (
            f'<div class="avatar-wrap">'
            f'<img class="avatar" src="{escape(avatar_url, quote=True)}" alt="avatar">'
            f'<div class="check">&#10003;</div>'
            f'</div>'
        )
    elif ok:
        visual = '<div class="avatar-fallback">&#10003;</div>'
    else:
        visual = '<div class="avatar-fallback error">&#10007;</div>'

    username_html = f'<div class="username">{escape(username)}</div>' if username else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{_PAGE_STYLE}</style>
</head>
<body>
  <div class="card">
    {visual}
    <h1>{title}</h1>
    {username_html}
    <p>{message}</p>
    <div class="footer">BLENDER MCP &middot; LE5LE</div>
  </div>
</body>
</html>"""


async def _auth_callback(request):
    from starlette.responses import HTMLResponse
    print("auto_callback")
    token = request.query_params.get("token")
    if not token:
        return HTMLResponse(
            _html_page("登录失败", "缺少 token 参数 / Missing token parameter", ok=False),
            status_code=400,
        )

    manager = get_auth_manager()
    await anyio.to_thread.run_sync(manager.login, token)

    if manager.error:
        return HTMLResponse(
            _html_page(
                "登录成功，但获取用户信息失败",
                f"Token 已保存，可以回到 Blender。/ Token stored. ({manager.error})",
                ok=False,
            ),
            status_code=502,
        )
    return HTMLResponse(
        _html_page(
            "登录成功",
            "可以关闭此页面并回到 Blender。/ You can close this page and return to Blender.",
            username=manager.username,
            avatar_url=manager.avatar_url,
        )
    )


async def _auth_status(request):
    from starlette.responses import JSONResponse

    return JSONResponse(get_auth_manager().status())


async def _auth_logout(request):
    from starlette.responses import JSONResponse

    # logout() also pushes the cleared state to Blender over a blocking socket;
    # keep that off the event loop.
    await anyio.to_thread.run_sync(get_auth_manager().logout)
    return JSONResponse({"logged_in": False})


async def _auth_login(request):
    from starlette.responses import RedirectResponse

    return RedirectResponse(build_login_url())


def auth_routes():
    """Starlette routes for the auth endpoints."""
    from starlette.routing import Route

    return [
        Route("/auth/callback", _auth_callback, methods=["GET"]),
        Route("/auth/status", _auth_status, methods=["GET"]),
        Route("/auth/logout", _auth_logout, methods=["GET", "POST"]),
        Route("/auth/login", _auth_login, methods=["GET"]),
    ]


def register_auth_routes(mcp) -> bool:
    """Mount the auth endpoints on the FastMCP SSE server, when supported.

    Returns False when the installed mcp version has no custom_route support.
    """
    custom_route = getattr(mcp, "custom_route", None)
    if custom_route is None:
        return False
    for route in auth_routes():
        custom_route(route.path, methods=sorted(route.methods or {"GET"}))(route.endpoint)
    return True


def start_auth_server(host: Optional[str] = None, port: Optional[int] = None):
    """Run the auth endpoints as a standalone HTTP server (stdio transport).

    Returns the daemon thread running uvicorn.
    """
    import uvicorn
    from starlette.applications import Starlette

    host = host or os.getenv("MCP_HOST", "0.0.0.0")
    port = port or get_auth_port()
    app = Starlette(routes=auth_routes())
    config = uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="blendermcp-auth", daemon=True)
    thread.start()
    logger.info(
        f"Auth endpoints listening on http://{host}:{port} "
        f"(login callback: {get_callback_url()})"
    )
    return thread

#endregion
