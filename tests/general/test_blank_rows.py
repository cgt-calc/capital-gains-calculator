"""Tests for blank rows in broker files read row by row against a header.

A row cleared in a spreadsheet is saved as a line of commas, and a text editor
can leave an empty line or a line of spaces. Such a row holds no transaction,
so it is skipped whatever its width, and the rows after it keep their own line
numbers. A row with anything in it is still checked as before.

The four parsers built on the shared CSV reader share one loop, and Trading 212
has its own. Each is read through ``load_from_file``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
import logging
import re
from typing import TYPE_CHECKING, Protocol

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.hl import HargreavesLansdownParser
from cgt_calc.parsers.interactive_brokers import InteractiveBrokersParser
from cgt_calc.parsers.mssb import MSSBParser
from cgt_calc.parsers.revolut import RevolutParser
from cgt_calc.parsers.trading212 import Trading212Parser

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from cgt_calc.model import BrokerTransaction


class _Load(Protocol):
    """A parser's ``load_from_file``, as these tests call it."""

    def __call__(
        self, file_path: Path, *, show_parsing_msg: bool = ...
    ) -> Sequence[BrokerTransaction]: ...


@dataclass(frozen=True)
class Export:
    """A minimal export: a header and two rows, each with its stated amount."""

    load: _Load
    # The broker's name, as the parser prints it.
    name: str
    file_name: str
    header: str
    rows: tuple[str, str]
    # The amount each row states, with the sign the parser gives it.
    amounts: tuple[Decimal, Decimal]
    encoding: str = "utf-8"

    @property
    def width(self) -> int:
        """Return the number of cells in the header."""
        return len(next(csv.reader([self.header])))

    def write(self, tmp_path: Path, lines: list[str]) -> Path:
        """Write the header and the given lines, one per line, as the broker would."""
        path = tmp_path / self.file_name
        path.write_text(
            "".join(f"{line}\n" for line in [self.header, *lines]),
            encoding=self.encoding,
        )
        return path


REVOLUT = Export(
    RevolutParser.load_from_file,
    "Revolut",
    "revolut.csv",
    "Date,Ticker,Type,Quantity,Price per share,Total Amount,Currency,FX Rate",
    (
        "2021-01-04T10:00:00Z,,CASH TOP-UP,,,USD 20000,USD,1.38",
        "2021-01-05T14:00:00Z,MSFT,BUY - MARKET,4,USD 250,USD 1000,USD,1.36",
    ),
    # A top-up adds its total; a purchase spends it.
    (Decimal(20000), Decimal(-1000)),
)
MORGAN_STANLEY = Export(
    MSSBParser.load_from_file,
    "Morgan Stanley",
    "Withdrawals Report.csv",
    "Execution Date,Order Number,Plan,Type,Order Status,Price,Quantity,"
    "Net Amount,Net Share Proceeds,Tax Payment Method",
    (
        "07-Feb-2023,X,GSU Class C,Sale,Complete,$105.70,-30.00,$3170.93,0,N/A",
        "09-Feb-2023,X,Cash,Sale,Complete,$1.00,-3170.93,$3170.93,0,N/A",
    ),
    # A share sale brings in its net amount; a sale of cash pays it out.
    (Decimal("3170.93"), Decimal("-3170.93")),
)
HARGREAVES_LANSDOWN = Export(
    HargreavesLansdownParser.load_from_file,
    "Hargreaves Lansdown",
    "hl-transaction-summary.csv",
    # HL ends its header, and not its rows, with a comma.
    "Trade date,Settle date,Reference,Description,Unit cost (p),Quantity,Value (£),",
    (
        '"09/03/2026","09/03/2026","INTEREST","Interest","n/a","n/a","3.87"',
        '"04/04/2026","04/04/2026","MANAGE FEE","Fee","n/a","n/a","-4.05"',
    ),
    (Decimal("3.87"), Decimal("-4.05")),
    encoding="windows-1252",
)
INTERACTIVE_BROKERS = Export(
    InteractiveBrokersParser.load_from_file,
    "Interactive Brokers",
    "ibkr.csv",
    "Transaction History,Header,Date,Account,Description,Transaction Type,Symbol,"
    "Quantity,Price,Gross Amount ,Commission,Net Amount",
    (
        (
            "Transaction History,Data,2025-01-01,U***00000,Electronic Fund Transfer,"
            "Deposit,-,-,-,5000.0,-,5000.0"
        ),
        (
            "Transaction History,Data,2025-02-01,U***00000,"
            "GBP Credit Interest for Feb-2025,Credit Interest,-,-,-,13.0,-,13.0"
        ),
    ),
    (Decimal("5000.0"), Decimal("13.0")),
)
TRADING_212 = Export(
    Trading212Parser.load_from_file,
    "Trading 212",
    "trading212.csv",
    "Action,Time,ISIN,Ticker,Name,No. of shares,Price / share,"
    "Currency (Price / share),Exchange rate,Result,Currency (Result),Total,"
    "Currency (Total),Withholding tax,Currency (Withholding tax),Transaction fee,"
    "Notes,ID,Currency conversion fee,Currency (Currency conversion fee),"
    "Currency (Transaction fee)",
    (
        'Deposit,2024-01-01 00:15:20.149,,,,,,,,,,3000.00,"GBP",,,,,ID01,,,',
        'Deposit,2024-01-02 09:00:00.000,,,,,,,,,,500.00,"GBP",,,,,ID02,,,',
    ),
    (Decimal("3000.00"), Decimal("500.00")),
)
SHARED_READER = [REVOLUT, MORGAN_STANLEY, HARGREAVES_LANSDOWN, INTERACTIVE_BROKERS]
EXPORTS = [*SHARED_READER, TRADING_212]


def _export_id(export: Export) -> str:
    """Name each test case after the broker it reads."""
    return export.name


def _blank_rows(width: int) -> dict[str, str]:
    """Return each shape of blank row, for a header of ``width`` cells."""
    return {
        "empty line": "",
        "only commas": "," * (width - 1),
        "fewer commas than the header": "," * (width // 2),
        "more commas than the header": "," * (width + 1),
        "spaces and a tab": " \t ",
        "cells of spaces": ", " * (width - 1) + " ",
        "quoted empty cells": ",".join(['""'] * width),
    }


# The names of the shapes, which are the same at any width.
BLANK_SHAPES = list(_blank_rows(1))


def _read(export: Export, path: Path) -> list[tuple[int | None, Decimal | None]]:
    """Load the file and return each transaction's line and amount."""
    transactions = export.load(path, show_parsing_msg=False)
    return [(t.source.row if t.source else None, t.amount) for t in transactions]


@pytest.mark.parametrize("shape", BLANK_SHAPES)
@pytest.mark.parametrize("export", EXPORTS, ids=_export_id)
def test_a_blank_row_between_two_rows_is_skipped(
    tmp_path: Path, export: Export, shape: str
) -> None:
    """Both rows are read as stated, the second still numbered by its own line.

    The header is line 1, the rows lines 2 and 4, and the blank row line 3.
    """
    blank = _blank_rows(export.width)[shape]
    path = export.write(tmp_path, [export.rows[0], blank, export.rows[1]])

    assert _read(export, path) == [(2, export.amounts[0]), (4, export.amounts[1])]


@pytest.mark.parametrize("export", EXPORTS, ids=_export_id)
def test_blank_rows_at_the_end_of_the_file_are_skipped(
    tmp_path: Path, export: Export
) -> None:
    """Rows a spreadsheet leaves below the last transaction are not read as one."""
    blanks = _blank_rows(export.width)
    path = export.write(
        tmp_path,
        [
            *export.rows,
            blanks["only commas"],
            blanks["spaces and a tab"],
            blanks["empty line"],
        ],
    )

    assert _read(export, path) == [(2, export.amounts[0]), (3, export.amounts[1])]


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(lambda width: "x" + "," * width, id="a value, then commas"),
        # Past the header the cells are kept apart from the rest of the row,
        # so the value there has to be looked at too.
        pytest.param(lambda width: "," * width + "x", id="commas, then a value"),
    ],
)
@pytest.mark.parametrize("export", EXPORTS, ids=_export_id)
def test_a_row_too_wide_with_a_value_in_it_is_still_refused(
    tmp_path: Path, export: Export, row: Callable[[int], str]
) -> None:
    """Only a row with nothing in it is skipped; this one is refused for its width."""
    path = export.write(tmp_path, [export.rows[0], row(export.width)])

    with pytest.raises(
        ParsingError,
        match=re.escape(
            f"This row has {export.width + 1} columns, not {export.width}:"
        ),
    ) as excinfo:
        export.load(path, show_parsing_msg=False)

    assert excinfo.value.row_index == 3


@pytest.mark.parametrize("export", SHARED_READER, ids=_export_id)
def test_a_header_over_only_a_row_of_commas_loads_no_transactions(
    tmp_path: Path, export: Export, caplog: pytest.LogCaptureFixture
) -> None:
    """A cleared row is still a row, so the file is not refused as empty.

    An empty file stops a whole directory of HL or Morgan Stanley reports. An
    HL report whose one row is cleared loads, as HL's blank-reference rule
    already let it, rather than stopping the directory.
    """
    path = export.write(tmp_path, ["," * (export.width - 1)])

    with caplog.at_level(logging.WARNING):
        assert export.load(path, show_parsing_msg=False) == []

    assert f"No transactions detected in file {path}" in caplog.text


@pytest.mark.parametrize("export", SHARED_READER, ids=_export_id)
def test_a_header_over_only_empty_lines_is_an_empty_file(
    tmp_path: Path, export: Export
) -> None:
    """The CSV reader never yields an empty line, so the file has no rows at all."""
    path = export.write(tmp_path, ["", ""])

    with pytest.raises(ParsingError, match=f"{export.name} CSV file is empty"):
        export.load(path, show_parsing_msg=False)
