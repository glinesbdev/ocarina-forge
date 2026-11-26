"""Loopback, origin, and launch-token checks for the local editor API.

The editor page is served on port 47231 and the API on 47232. A website open
in the same browser must not be able to create projects, retarget folders, or
delete files by calling those ports. DNS rebinding is blocked by the Host
check: the header has to name localhost or a loopback address, not a public
name that happens to resolve here.
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

LAUNCH_TOKEN = secrets.token_urlsafe(32)
TOKEN_HEADER = "x-ocarina-forge-token"
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_VITE_PORT = re.compile(r"\bport:\s*(\d+)\b")
_vite_ports: set[int] | None = None


def launch_token() -> str:
    return LAUNCH_TOKEN


def _vite_config_ports() -> set[int]:
    """Ports written in ``frontend/vite.config.js`` (dev and preview)."""
    global _vite_ports
    if _vite_ports is not None:
        return _vite_ports
    path = os.path.join(os.path.dirname(__file__), "..", "frontend", "vite.config.js")
    found: set[int] = set()
    try:
        with open(os.path.normpath(path), encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        text = ""
    for match in _VITE_PORT.finditer(text):
        port = int(match.group(1))
        if 1 <= port <= 65535:
            found.add(port)
    _vite_ports = found
    return found


def allowed_ports() -> set[int]:
    """Editor, API, and any Vite port in config or the dev-port env vars."""
    ports = {47231, 47232}
    ports |= _vite_config_ports()
    for name in ("OCARINA_FORGE_PORT", "BANCWRIGHT_PORT", "OCARINA_FORGE_DEV_PORT"):
        raw = os.environ.get(name, "").strip()
        if raw.isdigit():
            ports.add(int(raw))
    extra = os.environ.get("OCARINA_FORGE_DEV_PORTS", "")
    for piece in extra.replace(";", ",").split(","):
        piece = piece.strip()
        if piece.isdigit():
            ports.add(int(piece))
    return {port for port in ports if 1 <= port <= 65535}


def allowed_origins() -> set[str]:
    origins = set()
    for port in allowed_ports():
        origins.add(f"http://127.0.0.1:{port}")
        origins.add(f"http://localhost:{port}")
        origins.add(f"http://[::1]:{port}")
    return origins


def _hostname(host_header: str) -> str | None:
    value = (host_header or "").strip().lower()
    if not value or "@" in value or " " in value:
        return None
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return None
        return value[1:end].rstrip(".")
    if value.count(":") > 1:
        return None
    if ":" in value:
        host, _, port = value.rpartition(":")
        if not port.isdigit():
            return None
        return host.rstrip(".")
    return value.rstrip(".")


def host_is_loopback(host_header: str) -> bool:
    return _hostname(host_header) in _LOOPBACK


def normalize_origin(value: str) -> str | None:
    """``http://127.0.0.1:47231`` from an Origin or a Referer URL. Nothing else."""
    parsed = urlsplit((value or "").strip())
    if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if host not in _LOOPBACK or parsed.port is None:
        return None
    display = f"[{host}]" if ":" in host else host
    return f"http://{display}:{parsed.port}"


def presented_origin(headers) -> tuple[str | None, bool]:
    """The browser origin, and whether this API may answer it.

    Origin wins. When it is missing, Referer is used. ``Origin: null`` is a
    browser's opaque origin and is not this app.
    """
    if "origin" in headers:
        raw = (headers.get("origin") or "").strip()
        if raw.lower() == "null":
            return None, False
        if raw:
            origin = normalize_origin(raw)
            return origin, bool(origin and origin in allowed_origins())
    referer = (headers.get("referer") or "").strip()
    if not referer:
        return None, False
    origin = normalize_origin(referer)
    return origin, bool(origin and origin in allowed_origins())


def token_matches(value: str | None) -> bool:
    supplied = value or ""
    if len(supplied) != len(LAUNCH_TOKEN):
        return False
    return hmac.compare_digest(supplied, LAUNCH_TOKEN)


def _forbid(message: str, origin: str | None = None) -> JSONResponse:
    response = JSONResponse({"error": "forbidden", "message": message}, status_code=403)
    if origin:
        _apply_cors(response, origin)
    return response


def _apply_cors(response, origin: str) -> None:
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Methods"] = "GET, HEAD, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Ocarina-Forge-Token"
    vary = response.headers.get("vary")
    response.headers["Vary"] = "Origin" if not vary else (
        vary if "origin" in vary.lower() else f"{vary}, Origin"
    )


def _with_cors_header(message: dict, origin: str) -> dict:
    headers = list(message.get("headers") or [])

    def upsert(name: bytes, value: str) -> None:
        headers[:] = [(key, item) for key, item in headers if key.lower() != name]
        headers.append((name, value.encode("latin-1")))

    upsert(b"access-control-allow-origin", origin)
    upsert(b"access-control-allow-methods", "GET, HEAD, POST, PUT, DELETE, OPTIONS")
    upsert(b"access-control-allow-headers", "Content-Type, X-Ocarina-Forge-Token")
    current = next((value.decode("latin-1") for key, value in headers if key.lower() == b"vary"), "")
    if not current:
        upsert(b"vary", "Origin")
    elif "origin" not in current.lower():
        upsert(b"vary", f"{current}, Origin")
    return {**message, "headers": headers}


class LocalApiMiddleware:
    """Reject a non-loopback Host, and a write whose origin is not this app."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        if not host_is_loopback(request.headers.get("host", "")):
            response = _forbid("The API only accepts requests to localhost.")
            await response(scope, receive, send)
            return
        origin, allowed = presented_origin(request.headers)
        method = scope.get("method", "GET").upper()
        if method == "OPTIONS":
            if not allowed or not origin:
                response = _forbid("This API only accepts the Ocarina Forge editor.")
                await response(scope, receive, send)
                return
            response = Response(status_code=204)
            _apply_cors(response, origin)
            await response(scope, receive, send)
            return
        if method not in {"GET", "HEAD"}:
            if not allowed or not origin:
                response = _forbid("This API only accepts writes from the Ocarina Forge editor.")
                await response(scope, receive, send)
                return
            if not token_matches(request.headers.get(TOKEN_HEADER)):
                response = _forbid("This write needs the editor's launch token.", origin)
                await response(scope, receive, send)
                return

        async def send_cors(message):
            if message["type"] == "http.response.start" and allowed and origin:
                message = _with_cors_header(message, origin)
            await send(message)

        await self.app(scope, receive, send_cors)


def editor_client(app, **kwargs):
    """A TestClient whose Host, Origin, and launch token match the editor.

    Existing API tests speak as the local page. A security test overrides
    ``headers`` to send a foreign Origin or Host.
    """
    from fastapi.testclient import TestClient

    headers = {
        "Origin": "http://127.0.0.1:47232",
        "X-Ocarina-Forge-Token": LAUNCH_TOKEN,
    }
    extra = kwargs.pop("headers", None)
    if extra:
        headers.update(extra)
    return TestClient(app, base_url="http://127.0.0.1", headers=headers, **kwargs)
