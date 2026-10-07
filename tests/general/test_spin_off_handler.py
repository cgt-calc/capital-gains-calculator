"""Tests for SpinOffHandler."""

from __future__ import annotations

import datetime
import re
import sys
from typing import TYPE_CHECKING

import pytest

from cgt_calc.const import DEFAULT_SPIN_OFF_FILE
from cgt_calc.exceptions import InteractiveInputRequiredError, ParsingError
from cgt_calc.model import Position
from cgt_calc.spin_off_handler import SpinOffHandler

if TYPE_CHECKING:
    from pathlib import Path

SPIN_OFF_DATE = datetime.date(2021, 5, 10)


def test_cached_source_needs_no_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spin-off already recorded in the file is returned without prompting."""
    spin_offs_file = tmp_path / "spin_offs.csv"
    spin_offs_file.write_text("dst,src\nNEW,OLD\n", encoding="utf8")
    handler = SpinOffHandler(spin_offs_file)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    assert handler.get_spin_off_source("NEW", SPIN_OFF_DATE, {}) == "OLD"


def test_spin_offs_file_with_a_byte_order_mark_is_read(tmp_path: Path) -> None:
    """A mapping file saved as Excel's "CSV UTF-8" keeps its dst column."""
    spin_offs_file = tmp_path / "spin_offs.csv"
    spin_offs_file.write_text("\ufeffdst,src\nNEW,OLD\n", encoding="utf8")

    assert SpinOffHandler(spin_offs_file).cache == {"NEW": "OLD"}


@pytest.mark.parametrize(
    ("row", "match"),
    [
        pytest.param(
            "NEW,OLD,",
            "This row has 3 columns, not 2",
            id="value beyond the last column",
        ),
        pytest.param("NEW", "needs both tickers", id="no source column"),
        pytest.param("NEW,", "needs both tickers", id="empty source"),
        pytest.param(",OLD", "needs both tickers", id="empty new ticker"),
        pytest.param("NEW,   ", "needs both tickers", id="source of only spaces"),
    ],
)
def test_spin_offs_file_refuses_a_malformed_row(
    tmp_path: Path, row: str, match: str
) -> None:
    """A row that is not a new ticker and its source is refused by row number."""
    spin_offs_file = tmp_path / "spin_offs.csv"
    spin_offs_file.write_text(f"dst,src\n{row}\n", encoding="utf8")

    with pytest.raises(ParsingError, match=match) as excinfo:
        SpinOffHandler(spin_offs_file)

    assert excinfo.value.row_index == 2


@pytest.mark.parametrize("blank", [",", "   "], ids=["only a comma", "only spaces"])
def test_spin_offs_file_skips_a_blank_row(tmp_path: Path, blank: str) -> None:
    """A row with nothing in it is ignored, not read as a mapping."""
    spin_offs_file = tmp_path / "spin_offs.csv"
    spin_offs_file.write_text(f"dst,src\nNEW,OLD\n{blank}\n", encoding="utf8")

    assert SpinOffHandler(spin_offs_file).cache == {"NEW": "OLD"}


@pytest.mark.parametrize(
    ("content", "found"),
    [
        # Not read as a header with no rows under it, which is an empty file.
        pytest.param("NEW,OLD\n", "NEW,OLD", id="one mapping and no header"),
        # The header is the error, not the rows it makes too long.
        pytest.param("dst\nNEW,OLD\n", "dst", id="header with a column missing"),
        # Not accepted with the last column of that name deciding the source.
        pytest.param(
            "dst,src,src\nNEW,OLD,OTHER\n", "dst,src,src", id="column named twice"
        ),
        # Quoted, or the line shown would look like the header it must be.
        pytest.param("dst,src \nNEW,OLD\n", "dst,src ", id="space after a name"),
        # Escaped, for the same reason: a zero-width space cannot be seen.
        pytest.param(
            "dst,src\u200b\nNEW,OLD\n", "dst,src\\u200b", id="unseen character"
        ),
    ],
)
def test_spin_offs_file_reports_a_wrong_first_line(
    tmp_path: Path, content: str, found: str
) -> None:
    """The first line is checked as the header before any row is read."""
    spin_offs_file = tmp_path / "spin_offs.csv"
    spin_offs_file.write_text(content, encoding="utf8")

    with pytest.raises(
        ParsingError,
        match=re.escape(f"must be the header 'dst,src', but it is:\n  '{found}'"),
    ) as excinfo:
        SpinOffHandler(spin_offs_file)

    assert excinfo.value.row_index == 1


def test_an_empty_spin_offs_file_holds_no_mappings(tmp_path: Path) -> None:
    """A file with nothing in it has no header to check, and is not an error."""
    spin_offs_file = tmp_path / "spin_offs.csv"
    spin_offs_file.touch()

    assert SpinOffHandler(spin_offs_file).cache == {}


def test_spin_offs_file_refuses_two_sources_for_one_ticker(tmp_path: Path) -> None:
    """The first row's source is not dropped for the second's without a word."""
    spin_offs_file = tmp_path / "spin_offs.csv"
    spin_offs_file.write_text("dst,src\nNEW,OLD\nKID,MUM\nNEW,OTHER\n", encoding="utf8")

    with pytest.raises(
        ParsingError, match="NEW is also on row 2, spun off from OLD there"
    ) as excinfo:
        SpinOffHandler(spin_offs_file)

    assert excinfo.value.row_index == 4


def test_spin_offs_file_accepts_a_row_repeated_as_it_is(tmp_path: Path) -> None:
    """A second row that says what the first did is not an error."""
    spin_offs_file = tmp_path / "spin_offs.csv"
    spin_offs_file.write_text("dst,src\nNEW,OLD\nNEW, OLD\n", encoding="utf8")

    assert SpinOffHandler(spin_offs_file).cache == {"NEW": "OLD"}


def test_spin_offs_file_ignores_spaces_around_a_ticker(tmp_path: Path) -> None:
    """Spaces typed beside the comma are not part of either ticker."""
    spin_offs_file = tmp_path / "spin_offs.csv"
    spin_offs_file.write_text("dst,src\n NEW , OLD \n", encoding="utf8")

    assert SpinOffHandler(spin_offs_file).cache == {"NEW": "OLD"}


def test_non_interactive_run_raises_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a terminal the prompt is replaced by an actionable error."""
    spin_offs_file = tmp_path / "spin_offs.csv"
    handler = SpinOffHandler(spin_offs_file)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    with pytest.raises(InteractiveInputRequiredError) as excinfo:
        handler.get_spin_off_source("NEW", SPIN_OFF_DATE, {})

    message = str(excinfo.value)
    assert "NEW" in message
    assert str(spin_offs_file) in message
    assert "dst,src" in message


def test_eof_during_prompt_raises_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EOF while reading the answer is reported like a missing terminal."""

    def read_eof(prompt: str) -> str:
        raise EOFError

    handler = SpinOffHandler(tmp_path / "spin_offs.csv")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", read_eof)

    with pytest.raises(InteractiveInputRequiredError):
        handler.get_spin_off_source("NEW", SPIN_OFF_DATE, {})


def test_interactive_prompt_uses_clear_spin_off_wording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prompt identifies the new and original tickers in clear English."""
    prompts: list[str] = []

    def enter_source(prompt: str) -> str:
        prompts.append(prompt)
        return "OLD"

    handler = SpinOffHandler(tmp_path / "spin_offs.csv")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", enter_source)

    handler.get_spin_off_source("NEW", SPIN_OFF_DATE, {"OLD": Position()})

    assert prompts == [
        (
            "For a spin-off, please enter the original ticker from which the new stock "
            "(symbol: NEW) was spun off on 2021-05-10: "
        )
    ]


def test_recording_spin_off_creates_missing_parent_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Saving a newly entered spin-off creates the file's parent directories."""
    spin_offs_file = tmp_path / "missing" / "nested" / "spin_offs.csv"
    handler = SpinOffHandler(spin_offs_file)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "OLD")

    source = handler.get_spin_off_source("NEW", SPIN_OFF_DATE, {"OLD": Position()})

    assert source == "OLD"
    content = spin_offs_file.read_text(encoding="utf8")
    assert content.splitlines() == ["dst,src", "NEW,OLD"]


def test_disabled_cache_error_asks_for_a_non_empty_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the cache disabled the error asks for a path to put the row in."""
    handler = SpinOffHandler(spin_offs_file=None)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    with pytest.raises(InteractiveInputRequiredError) as excinfo:
        handler.get_spin_off_source("NEW", SPIN_OFF_DATE, {})

    message = str(excinfo.value)
    assert "non-empty --spin-offs-file" in message
    assert "'NEW,<source>' row" in message
    # The disabled cache is never read, so its default path must not be named.
    assert str(DEFAULT_SPIN_OFF_FILE) not in message
