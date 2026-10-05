"""Tests for input files that are not in the encoding their reader expects."""

from __future__ import annotations

import argparse
import io
import sys
from typing import TYPE_CHECKING

import pytest

from cgt_calc.args_validators import STDIN_PATH
from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.exceptions import ParsingError
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.parsers.hl import HargreavesLansdownParser
from cgt_calc.parsers.raw import RawParser
from cgt_calc.parsers.schwab import SchwabParser
from cgt_calc.share_prices import SharePrices
from cgt_calc.spin_off_handler import SpinOffHandler

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

UTF16_CSV = "date,action,symbol\n2024-05-01,BUY,ACME\n".encode("utf-16")


def _schwab_award_file(path: Path) -> object:
    return SchwabParser.load_from_args(
        argparse.Namespace(
            schwab_file=None,
            schwab_dir=None,
            schwab_award_file=path,
            schwab_equity_award_json=None,
        )
    )


@pytest.mark.parametrize(
    "read",
    [
        lambda path: RawParser.load_from_file(path, show_parsing_msg=False),
        _schwab_award_file,
        lambda path: SchwabParser.load_from_dir(path.parent),
        SharePrices,
        lambda path: CurrencyConverter(exchange_rates_file=path),
        lambda path: IsinConverter(isin_translation_file=path),
        SpinOffHandler,
    ],
    ids=[
        "broker file",
        "Schwab award file",
        "Schwab directory",
        "prices file",
        "exchange rates file",
        "ISIN translation file",
        "spin-offs file",
    ],
)
def test_a_file_that_is_not_utf8_is_reported_by_name(
    tmp_path: Path, read: Callable[[Path], object]
) -> None:
    """A wrongly encoded file is named in a parsing error, not a traceback."""
    path = tmp_path / "input.csv"
    path.write_bytes(UTF16_CSV)

    with pytest.raises(ParsingError, match="this file is not UTF-8 text") as excinfo:
        read(path)

    assert excinfo.value.file == path


def test_piped_input_that_is_not_utf8_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Standard input is checked too, with advice that fits a pipe."""
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(UTF16_CSV), encoding="utf-8")
    )

    with pytest.raises(ParsingError, match="the piped input is not UTF-8 text"):
        RawParser.load_from_file(STDIN_PATH, show_parsing_msg=False)


def test_a_hargreaves_lansdown_file_names_its_own_encoding(tmp_path: Path) -> None:
    """The advice names Windows-1252, which is what this reader can open."""
    # 0x81 is one of the few bytes Windows-1252 leaves undefined.
    (tmp_path / "summary.csv").write_bytes(b"Trade date,Details\n\x81\n")

    with pytest.raises(ParsingError, match="Save it as Windows-1252"):
        HargreavesLansdownParser.load_from_dir(tmp_path)
