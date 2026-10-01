"""Protect a server that handles tax data on someone's own computer.

The browser is the attacker's route in: any web page the person has open can
send requests to ``127.0.0.1``. Four checks close it. The Host header must be
a known name (DNS rebinding), state-changing requests must come from this
origin (cross-site forms), every request must carry the token printed at
start-up (anything else that can reach the port), and responses tell the
browser not to keep or embed anything.

These are plain ASGI middleware. Starlette's ``BaseHTTPMiddleware`` buffers
responses, which would hold back the live log.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import HTTPConnection
from starlette.responses import PlainTextResponse, RedirectResponse

from .config import AUTH_COOKIE

if TYPE_CHECKING:
    from collections.abc import Collection

    from starlette.types import ASGIApp, Message, Receive, Scope, Send

AUTH_PATH = "/auth"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Nothing is loaded from outside this server, and nothing inline runs.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
    "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)

RESPONSE_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # Not "no-referrer": browsers then send "Origin: null" with same-site form
    # posts, which the origin check has to refuse.
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def new_token() -> str:
    """Create the secret that identifies this launch."""
    return secrets.token_urlsafe(24)


def hostname(host_header: str) -> str:
    """Return the host name of a Host header, without any port."""
    if host_header.startswith("["):
        return host_header[: host_header.find("]") + 1]
    return host_header.partition(":")[0]


class HostCheckMiddleware:
    """Refuse requests addressed to a name this server was not started for."""

    def __init__(self, app: ASGIApp, *, allowed_hosts: Collection[str]) -> None:
        """Wrap ``app``."""
        self.app = app
        self.allowed = {host.lower() for host in allowed_hosts}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer 400 for an unknown host."""
        if scope["type"] == "http":
            host = HTTPConnection(scope).headers.get("host", "")
            if hostname(host).lower() not in self.allowed:
                await PlainTextResponse("Invalid host header", 400)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """Add the headers that stop the browser keeping or embedding responses."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Set the headers on every response."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                # Reports hold the person's whole trading history, so pages are
                # never stored. The fixed assets may be, but are rechecked
                # each time; the ETag makes that a cheap 304.
                is_asset = scope["path"].startswith("/static/")
                headers["Cache-Control"] = "no-cache" if is_asset else "no-store"
                for name, value in RESPONSE_HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class MaxBodySizeMiddleware:
    """Refuse request bodies over a limit, however they announce their size."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        """Wrap ``app``."""
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer 413 as soon as the limit is passed."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = HTTPConnection(scope).headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_bytes:
            await PlainTextResponse("Upload too large", 413)(scope, receive, send)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(status_code=413, detail="Upload too large")
            return message

        await self.app(scope, counting_receive, send)


class OriginCheckMiddleware:
    """Refuse state-changing requests that a page on another site sent."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer 403 when the Origin is not this server."""
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            headers = HTTPConnection(scope).headers
            origin = headers.get("origin")
            fetch_site = headers.get("sec-fetch-site")
            cross_site = origin is not None and (
                urlsplit(origin).netloc != headers.get("host")
            )
            if cross_site or fetch_site not in {None, "same-origin", "none"}:
                await PlainTextResponse("Cross-site request refused", 403)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


class TokenAuthMiddleware:
    """Require the launch token, handed over once through ``/auth``.

    The token is printed in the terminal that started the server. Opening
    ``/auth?token=...`` stores it in an HttpOnly cookie, so it never appears in
    later URLs, and SameSite=Strict keeps other sites from using it.
    """

    def __init__(self, app: ASGIApp, *, token: str, secure: bool = False) -> None:
        """Wrap ``app``; ``secure`` limits the cookie to HTTPS."""
        self.app = app
        self.token = token
        self.secure = secure

    def _matches(self, candidate: str | None) -> bool:
        """Compare in constant time."""
        return candidate is not None and secrets.compare_digest(
            candidate.encode(), self.token.encode()
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Let authenticated requests through and answer the rest."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        connection = HTTPConnection(scope)
        if scope["path"] == AUTH_PATH:
            if self._matches(connection.query_params.get("token")):
                response = RedirectResponse("/", status_code=303)
                response.set_cookie(
                    AUTH_COOKIE,
                    self.token,
                    httponly=True,
                    samesite="strict",
                    secure=self.secure,
                    path="/",
                )
            else:
                response = PlainTextResponse("Invalid token", 403)
            await response(scope, receive, send)
            return
        if not self._matches(connection.cookies.get(AUTH_COOKIE)):
            await PlainTextResponse(
                "Not signed in. Open the address printed when cgt-calc-web started.",
                401,
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)
