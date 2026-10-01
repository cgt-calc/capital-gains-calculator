"""Tests for the server's command line and environment settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cgt_calc.web.server import build_settings, create_parser, login_url

if TYPE_CHECKING:
    import argparse

    from cgt_calc.web.config import Settings


def _settings(
    argv: list[str], environ: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> tuple[argparse.Namespace, Settings]:
    """Parse ``argv`` with ``environ`` as the process environment."""
    for name in list(environ):
        monkeypatch.setenv(name, environ[name])
    parser = create_parser()
    args = parser.parse_args(argv)
    return args, build_settings(parser, args, environ)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without options the server is loopback only, on the documented port."""
    args, settings = _settings([], {}, monkeypatch)
    assert (args.host, args.port) == ("127.0.0.1", 8765)
    assert settings.allowed_hosts == {"127.0.0.1", "localhost", "[::1]"}
    assert len(settings.token) >= 16
    assert not settings.secure_cookie
    assert login_url(args, settings).startswith("http://127.0.0.1:8765/auth?token=")


def test_environment_configures_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every setting can come from the environment, as in a container."""
    environ = {
        "CGT_WEB_HOST": "0.0.0.0",
        "CGT_WEB_PORT": "9000",
        "CGT_WEB_NO_BROWSER": "1",
        "CGT_WEB_MAX_UPLOAD_MB": "5",
        "CGT_WEB_RUN_TTL_MINUTES": "10",
        "CGT_WEB_RUN_TIMEOUT_MINUTES": "2",
        "CGT_WEB_ALLOWED_HOSTS": "nas.local, Cgt.Example",
        "CGT_WEB_TOKEN": "a" * 20,
    }
    args, settings = _settings([], environ, monkeypatch)
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.no_browser
    assert settings.max_upload_bytes == 5 * 1024 * 1024
    assert settings.run_ttl_seconds == 600
    assert settings.run_timeout_seconds == 120
    assert settings.allowed_hosts >= {"nas.local", "cgt.example"}
    assert settings.token == "a" * 20


def test_options_beat_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A command line option overrides the variable."""
    args, _ = _settings(["--port", "1234"], {"CGT_WEB_PORT": "9000"}, monkeypatch)
    assert args.port == 1234


def test_empty_variable_means_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose passes unset variables as empty strings."""
    args, _ = _settings([], {"CGT_WEB_PORT": ""}, monkeypatch)
    assert args.port == 8765


def test_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """The address you open is allowed and used in the link."""
    args, settings = _settings(
        ["--public-url", "https://cgt.home.example/", "--allow-host", "other"],
        {"CGT_WEB_TOKEN": "b" * 16},
        monkeypatch,
    )
    assert {"cgt.home.example", "other"} <= settings.allowed_hosts
    assert settings.secure_cookie
    assert (
        login_url(args, settings) == f"https://cgt.home.example/auth?token={'b' * 16}"
    )


def test_public_url_with_ipv6_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Host headers keep the brackets around an IPv6 address."""
    _, settings = _settings(["--public-url", "http://[fd00::1]:8765"], {}, monkeypatch)
    assert "[fd00::1]" in settings.allowed_hosts


def test_short_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A guessable secret would defeat the login check."""
    with pytest.raises(SystemExit):
        _settings([], {"CGT_WEB_TOKEN": "short"}, monkeypatch)


def test_public_url_needs_a_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A URL without a host cannot be allowed."""
    with pytest.raises(SystemExit):
        _settings(["--public-url", "192.168.1.5:8765"], {}, monkeypatch)
