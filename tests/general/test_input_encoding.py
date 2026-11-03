"""Tests for input files that are not in the encoding their reader expects."""

from __future__ import annotations

import io
from pathlib import Path
import sys
from typing import TYPE_CHECKING

import pytest

from cgt_calc.args_validators import STDIN_PATH
from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.exceptions import ParsingError
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.logging import force_utf8_stdio
from cgt_calc.parsers.hl import HargreavesLansdownParser
from cgt_calc.parsers.raw import RawParser
from cgt_calc.parsers.schwab import SchwabParser
from cgt_calc.parsers.sharesight import SharesightParser
from cgt_calc.parsers.vanguard import VanguardParser
from cgt_calc.share_prices import SharePrices
from cgt_calc.spin_off_handler import SpinOffHandler
from tests.schwab.helpers import load_via_cli
from tests.utils import tax_fields

if TYPE_CHECKING:
    from collections.abc import Callable

CSV_TEXT = "date,action,symbol\n2024-05-01,BUY,ACME\n"
# With a byte-order mark, which is not valid UTF-8.
UTF16_CSV = CSV_TEXT.encode("utf-16")
# Without one, UTF-16 whose characters are ASCII is valid UTF-8 too: each
# character reads as itself and a NUL, after it in little-endian and before
# it in big-endian.
UTF16_ORDERS = ["utf-16-le", "utf-16-be"]


@pytest.mark.parametrize(
    "read",
    [
        lambda path: RawParser.load_from_file(path, show_parsing_msg=False),
        lambda path: load_via_cli(schwab_award_file=str(path)),
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


@pytest.mark.parametrize("encoding", UTF16_ORDERS)
@pytest.mark.parametrize(
    ("name", "read"),
    [
        (
            "input.csv",
            lambda path: RawParser.load_from_file(path, show_parsing_msg=False),
        ),
        ("input.csv", lambda path: load_via_cli(schwab_award_file=str(path))),
        ("input.csv", lambda path: SchwabParser.load_from_dir(path.parent)),
        # Sharesight found no rows in such a report and went on without it.
        (
            "All Trades Report.csv",
            lambda path: SharesightParser.load_from_dir(path.parent),
        ),
    ],
    ids=["broker file", "Schwab award file", "Schwab directory", "Sharesight report"],
)
def test_utf16_without_a_byte_order_mark_is_reported_by_name(
    tmp_path: Path, name: str, read: Callable[[Path], object], encoding: str
) -> None:
    """The NULs in its first line give it away, not a header the parser lacks."""
    path = tmp_path / name
    path.write_bytes(CSV_TEXT.encode(encoding))

    with pytest.raises(ParsingError, match="this file is not UTF-8 text") as excinfo:
        read(path)

    assert excinfo.value.file == path


def test_nuls_after_the_first_line_are_left_to_the_parser(tmp_path: Path) -> None:
    """A file padded with NULs after its last row still loads, as it did before.

    An interrupted download or sync can leave a file that way. Vanguard reads
    past the padding, so refusing every NUL would stop a file that loads.
    """
    export = Path("tests/vanguard/data/cash_investment_report.csv")
    padded = tmp_path / export.name
    padded.write_bytes(export.read_bytes() + b"\x00" * 32)

    loaded = VanguardParser.load_from_file(padded, show_parsing_msg=False)

    assert loaded
    assert [tax_fields(row) for row in loaded] == [
        tax_fields(row)
        for row in VanguardParser.load_from_file(export, show_parsing_msg=False)
    ]


@pytest.mark.parametrize(
    "piped",
    [UTF16_CSV, *(CSV_TEXT.encode(order) for order in UTF16_ORDERS)],
    ids=["with a byte-order mark", *UTF16_ORDERS],
)
@pytest.mark.parametrize(
    "read",
    [
        lambda: RawParser.load_from_file(STDIN_PATH, show_parsing_msg=False),
        lambda: load_via_cli(schwab_award_file=str(STDIN_PATH)),
    ],
    ids=["broker file", "Schwab award file"],
)
def test_piped_input_that_is_not_utf8_is_reported(
    monkeypatch: pytest.MonkeyPatch, piped: bytes, read: Callable[[], object]
) -> None:
    """Standard input is checked too, with advice that fits a pipe or redirect."""
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(piped), encoding="utf-8")
    )
    # As the CLI sets it up: stdin must stay strict for the check to fire.
    force_utf8_stdio()

    with pytest.raises(ParsingError, match="standard input is not UTF-8 text"):
        read()


def test_a_hargreaves_lansdown_file_names_its_own_encoding(tmp_path: Path) -> None:
    """The advice names Windows-1252, which is what this reader can open."""
    # 0x81 is one of the few bytes Windows-1252 leaves undefined.
    (tmp_path / "summary.csv").write_bytes(b"Trade date,Details\n\x81\n")

    with pytest.raises(ParsingError, match="Save it as Windows-1252"):
        HargreavesLansdownParser.load_from_dir(tmp_path)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le"])
def test_a_utf16_hargreaves_lansdown_file_names_its_own_encoding(
    tmp_path: Path, encoding: str
) -> None:
    """UTF-16 is refused under the name of the encoding HL writes.

    With a byte-order mark the file is not UTF-8, and Windows-1252, which HL
    falls back to, reads it without an error. Without one it is read as UTF-8.
    Either way its first line holds NULs.
    """
    path = tmp_path / "summary.csv"
    path.write_bytes("Trade date,Details\n".encode(encoding))

    with pytest.raises(ParsingError, match="this file is not Windows-1252 text"):
        HargreavesLansdownParser.load_from_dir(tmp_path)
