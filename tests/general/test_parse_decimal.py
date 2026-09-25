"""Every parser refuses a field that is not a finite number.

"NaN" and "Infinity" parse as decimals, so a corrupt export used to reach the
calculation and fail there as a bare decimal exception naming neither the file
nor the row. Each parser's own helper now routes through
`cgt_calc.util.parse_decimal`, which refuses them where the column is known.
"""

from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers import (
    freetrade,
    interactive_brokers,
    mssb,
    raw,
    revolut,
    schwab,
    trading212,
    vanguard,
)
from cgt_calc.parsers.sharesight import SharesightParser, TradeColumn
from cgt_calc.util import parse_decimal

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

# Every spelling `Decimal` accepts but no export can mean as an amount.
NON_FINITE = ["NaN", "-NaN", "sNaN", "Infinity", "-Infinity", "inf", "-INF"]

# One decimal field per parser, called the way that parser calls its own helper.
PARSER_FIELDS: list[tuple[str, Callable[[str], Decimal | None], str]] = [
    (
        "raw",
        lambda value: raw._parse_decimal(  # noqa: SLF001
            {raw.RawColumn.QUANTITY: value}, raw.RawColumn.QUANTITY
        ),
        "quantity",
    ),
    (
        "revolut",
        lambda value: revolut._parse_decimal(  # noqa: SLF001
            {revolut.RevolutColumn.QUANTITY: value},
            revolut.RevolutColumn.QUANTITY,
            allow_empty=False,
        ),
        "Quantity",
    ),
    (
        "mssb",
        lambda value: mssb._parse_decimal(  # noqa: SLF001
            {mssb.ReleaseColumn.QUANTITY: value}, mssb.ReleaseColumn.QUANTITY
        ),
        "Quantity",
    ),
    (
        "interactive_brokers",
        lambda value: interactive_brokers._parse_decimal(  # noqa: SLF001
            {interactive_brokers.InteractiveBrokersColumn.QUANTITY: value},
            interactive_brokers.InteractiveBrokersColumn.QUANTITY,
        ),
        "Quantity",
    ),
    (
        "schwab",
        lambda value: schwab._parse_decimal(  # noqa: SLF001
            OrderedDict({schwab.RequiredTransactionsColumn.QUANTITY: value}),
            schwab.RequiredTransactionsColumn.QUANTITY,
        ),
        "Quantity",
    ),
    (
        "sharesight",
        lambda value: SharesightParser._parse_decimal(  # noqa: SLF001
            {TradeColumn.QUANTITY: value}, TradeColumn.QUANTITY
        ),
        "Quantity",
    ),
    (
        "freetrade",
        lambda value: freetrade._parse_decimal(  # noqa: SLF001
            {freetrade.FreetradeColumn.QUANTITY: value},
            freetrade.FreetradeColumn.QUANTITY,
        ),
        "Quantity",
    ),
    (
        "vanguard",
        lambda value: vanguard._parse_decimal(value, "Quantity"),  # noqa: SLF001
        "Quantity",
    ),
    (
        "trading212",
        lambda value: trading212.decimal_or_none(
            {trading212.Trading212Column.NO_OF_SHARES: value},
            trading212.Trading212Column.NO_OF_SHARES,
        ),
        "No. of shares",
    ),
]


@pytest.mark.parametrize("value", NON_FINITE)
@pytest.mark.parametrize(
    ("parse", "column"),
    [pytest.param(parse, column, id=name) for name, parse, column in PARSER_FIELDS],
)
def test_non_finite_field_is_refused(
    parse: Callable[[str], Decimal | None], column: str, value: str
) -> None:
    """A non-finite field raises, naming the column and quoting the value."""
    with pytest.raises(ValueError, match=f"Invalid decimal in .*{column}"):
        parse(value)


@pytest.mark.parametrize("value", ["1", "-2.5", "0"])
def test_finite_field_still_parses(value: str) -> None:
    """The guard leaves ordinary amounts alone."""
    for _, parse, _ in PARSER_FIELDS:
        assert parse(value) == Decimal(value)


def test_non_finite_field_reports_the_file_and_row(tmp_path: Path) -> None:
    """The refusal reaches the user as a parse error, not a traceback.

    Revolut states its amounts with a currency prefix, so this also covers a
    value the parser cleans before it is read as a number.
    """
    header = "Date,Ticker,Type,Quantity,Price per share,Total Amount,Currency,FX Rate\n"
    row = (
        "2021-07-20T09:00:00.000000Z,NVDA,BUY - MARKET,10,USD 100.00,USD NaN,USD,0.72\n"
    )
    revolut_file = tmp_path / "revolut.csv"
    revolut_file.write_text(header + row, encoding="utf-8")

    with pytest.raises(
        ParsingError, match="row 2: Invalid decimal in column 'Total Amount': 'NaN'"
    ):
        revolut.RevolutParser().load_from_file(revolut_file)


def test_parse_decimal_strips_only_what_it_is_told_to() -> None:
    """`strip` removes the separators a broker writes, and nothing else."""
    assert parse_decimal("1,250.00", "column 'Amount'", strip=",") == Decimal("1250.00")
    assert parse_decimal("$1,250.00", "column 'Amount'", strip=",$") == Decimal(
        "1250.00"
    )
    with pytest.raises(
        ValueError, match=r"Invalid decimal in column 'Amount': '1,250'"
    ):
        parse_decimal("1,250", "column 'Amount'")
