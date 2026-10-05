"""Tests for colour and emoji aware logging."""

from __future__ import annotations

import io
import logging
import sys
from typing import TYPE_CHECKING, TextIO, cast

import colorama

from cgt_calc.logging import ColourMessageFormatter, ConsoleUI, force_utf8_stdio

if TYPE_CHECKING:
    import pytest


class FakeStream:
    """Stream stub with a configurable encoding and tty flag."""

    def __init__(self, encoding: str = "utf-8", *, atty: bool = False) -> None:
        """Create the stream stub."""
        self.encoding = encoding
        self._atty = atty

    def isatty(self) -> bool:
        """Return the configured tty flag."""
        return self._atty


def _stream(encoding: str = "utf-8", *, atty: bool = False) -> TextIO:
    """Create a stream stub typed as TextIO."""
    return cast("TextIO", FakeStream(encoding, atty=atty))


def _record(level: int, msg: str) -> logging.LogRecord:
    """Create a log record."""
    return logging.LogRecord(
        name="test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )


def test_painted_with_colour_and_emoji(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrap the message in colour codes and prefix the emoji."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("NO_EMOJI", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")

    result = ConsoleUI().painted(
        _stream(), "hello", colour=colorama.Fore.CYAN, emoji="📄"
    )

    assert result == f"📄  {colorama.Fore.CYAN}hello{colorama.Style.RESET_ALL}"


def test_painted_respects_no_emoji(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep colours but drop emoji when NO_EMOJI is set."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.setenv("NO_EMOJI", "1")

    result = ConsoleUI().painted(
        _stream(), "hello", colour=colorama.Fore.CYAN, emoji="📄"
    )

    assert "📄" not in result
    assert colorama.Fore.CYAN in result


def test_painted_plain_without_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return the plain message for non-tty streams."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_EMOJI", raising=False)

    result = ConsoleUI().painted(
        _stream(), "hello", colour=colorama.Fore.CYAN, emoji="📄"
    )

    assert result == "hello"


def test_painted_uses_tty_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Colourise tty streams without FORCE_COLOR."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    assert ConsoleUI().supports_colour(_stream(atty=True)) is True
    assert ConsoleUI().supports_colour(_stream(atty=False)) is False


def test_no_emoji_for_non_unicode_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable emoji when the stream encoding is not unicode."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("NO_EMOJI", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")

    assert ConsoleUI().supports_emoji(_stream(encoding="latin-1")) is False


def test_formatter_colours_and_wraps_alerts() -> None:
    """Colourise warnings, add emoji and pad alerts with blank lines."""
    formatter = ColourMessageFormatter("%(message)s", use_colour=True, use_emoji=True)

    first = formatter.format(_record(logging.WARNING, "danger\nsecond line"))
    second = formatter.format(_record(logging.WARNING, "again"))

    assert "⚠️" in first
    assert colorama.Fore.YELLOW in first
    assert "WARNING: danger" in first
    # Continuation lines are indented.
    assert "\n    " in first
    # The first alert is padded with a leading blank line,
    # consecutive alerts are not.
    assert first.startswith("\n")
    assert not second.startswith("\n")


def test_formatter_plain_info() -> None:
    """Leave info messages untouched without colour and emoji."""
    formatter = ColourMessageFormatter("%(message)s", use_colour=False, use_emoji=False)

    assert formatter.format(_record(logging.INFO, "hello")) == "hello"


def test_force_utf8_stdio_reconfigures_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Piped input decodes as UTF-8 whatever the platform locale reports."""
    stream = io.TextIOWrapper(io.BytesIO("CAFÉ".encode()), encoding="cp1252")
    monkeypatch.setattr(sys, "stdin", stream)

    force_utf8_stdio()

    assert sys.stdin.read() == "CAFÉ"
