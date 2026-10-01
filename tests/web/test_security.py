"""Tests for the middleware that protects the local server."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from cgt_calc.web.config import AUTH_COOKIE
from cgt_calc.web.security import (
    HostCheckMiddleware,
    MaxBodySizeMiddleware,
    OriginCheckMiddleware,
    SecurityHeadersMiddleware,
    TokenAuthMiddleware,
    hostname,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from starlette.requests import Request

TOKEN = "secret"
BASE_URL = "http://127.0.0.1:8765"


async def _echo(request: Request) -> Response:
    body = await request.body()
    return PlainTextResponse(f"{request.method} {len(body)}")


def _client(*middleware: Middleware) -> TestClient:
    app = Starlette(
        routes=[Route("/", _echo, methods=["GET", "POST"])],
        middleware=list(middleware),
    )
    return TestClient(app, base_url=BASE_URL)


@pytest.mark.parametrize(
    ("header", "name"),
    [
        ("127.0.0.1:8765", "127.0.0.1"),
        ("localhost", "localhost"),
        ("[::1]:8765", "[::1]"),
        ("example.com:80", "example.com"),
    ],
)
def test_hostname(header: str, name: str) -> None:
    """The port is not part of the name."""
    assert hostname(header) == name


@pytest.mark.parametrize(
    ("host", "status"),
    [
        ("127.0.0.1:8765", 200),
        ("localhost:1234", 200),
        ("LOCALHOST", 200),
        ("evil.example", 400),
        ("127.0.0.1.evil.example", 400),
        ("", 400),
    ],
)
def test_host_check(host: str, status: int) -> None:
    """A rebinding page reaches the server under its own name and is refused."""
    client = _client(
        Middleware(HostCheckMiddleware, allowed_hosts={"127.0.0.1", "localhost"})
    )
    assert client.get("/", headers={"host": host}).status_code == status


def test_extra_allowed_host() -> None:
    """Names added at start-up are accepted."""
    client = _client(Middleware(HostCheckMiddleware, allowed_hosts={"nas.local"}))
    assert client.get("/", headers={"host": "nas.local:8765"}).status_code == 200


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 200),
        ({"origin": BASE_URL}, 200),
        ({"origin": "https://evil.example"}, 403),
        ({"origin": "null"}, 403),
        ({"sec-fetch-site": "cross-site"}, 403),
        ({"sec-fetch-site": "same-origin"}, 200),
    ],
)
def test_origin_check_on_post(headers: dict[str, str], status: int) -> None:
    """A form on another site cannot post to the server."""
    client = _client(Middleware(OriginCheckMiddleware))
    assert client.post("/", headers=headers).status_code == status


def test_origin_is_not_checked_on_get() -> None:
    """Reading is safe, so links from other sites still work."""
    client = _client(Middleware(OriginCheckMiddleware))
    response = client.get("/", headers={"origin": "https://evil.example"})
    assert response.status_code == 200


def test_token_flow() -> None:
    """The link sets a cookie once; without it nothing is served."""
    client = _client(Middleware(TokenAuthMiddleware, token=TOKEN))
    assert client.get("/").status_code == 401
    assert client.get("/auth", params={"token": "wrong"}).status_code == 403
    assert AUTH_COOKIE not in client.cookies

    response = client.get("/auth", params={"token": TOKEN}, follow_redirects=False)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert client.get("/").status_code == 200


def test_cookie_is_secure_over_https() -> None:
    """Behind an HTTPS proxy the cookie is never sent in the clear."""
    client = _client(Middleware(TokenAuthMiddleware, token=TOKEN, secure=True))
    response = client.get("/auth", params={"token": TOKEN}, follow_redirects=False)
    assert "secure" in response.headers["set-cookie"].lower()


def test_wrong_cookie_is_refused() -> None:
    """A cookie from another launch does not open this one."""
    client = _client(Middleware(TokenAuthMiddleware, token=TOKEN))
    client.cookies.set(AUTH_COOKIE, "old")
    assert client.get("/").status_code == 401


def test_body_size_limit_by_header() -> None:
    """A body announced as too large is refused before it is read."""
    client = _client(Middleware(MaxBodySizeMiddleware, max_bytes=10))
    assert client.post("/", content=b"x" * 11).status_code == 413


def test_body_size_limit_without_header() -> None:
    """A stream that never announces its size is cut off at the limit."""
    client = _client(Middleware(MaxBodySizeMiddleware, max_bytes=10))

    def chunks() -> Iterator[bytes]:
        yield b"x" * 6
        yield b"x" * 6

    response = client.post("/", content=chunks())
    assert response.status_code == 413
    assert client.post("/", content=b"x" * 10).status_code == 200


def test_security_headers() -> None:
    """Every response forbids caching, sniffing and embedding."""
    client = _client(Middleware(SecurityHeadersMiddleware))
    headers = client.get("/").headers
    assert headers["cache-control"] == "no-store"
    # Fixed assets are revalidated rather than refetched.
    assert client.get("/static/x").headers["cache-control"] == "no-cache"
    assert headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "script-src 'self'" in headers["content-security-policy"]
    # The page icon is one of our own files.
    assert "img-src 'self'" in headers["content-security-policy"]
    # "no-referrer" would make browsers send "Origin: null" with our own forms.
    assert headers["referrer-policy"] == "same-origin"
