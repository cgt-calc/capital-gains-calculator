"""Unit tests for the Schwab CSV parser."""

from __future__ import annotations

from collections import OrderedDict
import csv
from decimal import Decimal
from pathlib import Path

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers.schwab import (
    AwardPrices,
    RequiredTransactionsColumn,
    SchwabParser,
    SchwabTransaction,
)


def _build_row() -> OrderedDict[str, str]:
    """Create a valid Schwab CSV row."""

    return OrderedDict(
        (
            (RequiredTransactionsColumn.DATE.value, "01/02/2024"),
            (RequiredTransactionsColumn.ACTION.value, "Buy"),
            (RequiredTransactionsColumn.SYMBOL.value, "ABC"),
            (RequiredTransactionsColumn.DESCRIPTION.value, "Sample description"),
            (RequiredTransactionsColumn.PRICE.value, "$10.00"),
            (RequiredTransactionsColumn.QUANTITY.value, "5"),
            (RequiredTransactionsColumn.FEES_AND_COMM.value, "$1.00"),
            (RequiredTransactionsColumn.AMOUNT.value, "$49.00"),
        )
    )


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    """Persist CSV rows to disk for a temporary Schwab fixture."""

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def test_parse_valid_row() -> None:
    """SchwabTransaction.create parses expected Decimal columns."""

    row = _build_row()
    transaction = SchwabTransaction.create(
        row, Path("input.csv"), AwardPrices(award_prices={})
    )

    assert transaction.date.isoformat() == "2024-01-02"
    assert transaction.action is ActionType.BUY
    assert transaction.symbol == "ABC"
    assert transaction.price == Decimal("10.00")
    assert transaction.quantity == Decimal(5)
    assert transaction.fees == Decimal("1.00")
    assert transaction.amount == Decimal(49)


@pytest.mark.parametrize(
    ("column", "override"),
    [
        (RequiredTransactionsColumn.PRICE, "$bad"),
        (RequiredTransactionsColumn.QUANTITY, "not-a-number"),
        (RequiredTransactionsColumn.FEES_AND_COMM, "$bogus"),
        (RequiredTransactionsColumn.AMOUNT, "$invalid"),
        # "NaN" and "Infinity" parse as decimals but are not amounts.
        (RequiredTransactionsColumn.PRICE, "NaN"),
        (RequiredTransactionsColumn.QUANTITY, "Infinity"),
        (RequiredTransactionsColumn.FEES_AND_COMM, "NaN"),
        (RequiredTransactionsColumn.AMOUNT, "-Infinity"),
    ],
)
def test_parse_row_invalid_decimal_raises(
    tmp_path: Path, column: RequiredTransactionsColumn, override: str
) -> None:
    """Invalid decimal values are surfaced as ParsingError with row context."""

    header = [col.value for col in RequiredTransactionsColumn]
    csv_path = tmp_path / f"invalid_{column.value.replace(' ', '_')}.csv"
    row = list(_build_row().values())
    row[header.index(column.value)] = override

    _write_csv(csv_path, header, [row])

    with pytest.raises(ParsingError) as exc:
        SchwabParser.load_from_file(csv_path)

    message = str(exc.value)
    assert f"Invalid decimal in column '{column.value}'" in message
    assert "row 2" in message


def test_parse_row_tolerates_formatting(tmp_path: Path) -> None:
    """Surrounding whitespace and thousands separators are accepted.

    Schwab tweaks its CSV formatting from time to time, so parsing stays
    relaxed about anything that is still an unambiguous number.
    """

    header = [col.value for col in RequiredTransactionsColumn]
    csv_path = tmp_path / "formatted.csv"
    row = list(_build_row().values())
    row[header.index(RequiredTransactionsColumn.FEES_AND_COMM.value)] = (
        "$\N{NARROW NO-BREAK SPACE}1.23"
    )
    row[header.index(RequiredTransactionsColumn.AMOUNT.value)] = "$1,234.56"

    _write_csv(csv_path, header, [row])

    transactions = SchwabParser.load_from_file(csv_path)

    assert len(transactions) == 1
    assert transactions[0].fees == Decimal("1.23")
    assert transactions[0].amount == Decimal("1234.56")


def test_missing_column_raises_parsing_error(tmp_path: Path) -> None:
    """Missing required columns are named, in alphabetical order, at the header."""

    header = [col.value for col in RequiredTransactionsColumn]
    row = list(_build_row().values())
    csv_path = tmp_path / "missing_column.csv"
    _write_csv(csv_path, header[:2], [row[:2]])

    with pytest.raises(ParsingError) as exc:
        SchwabParser.load_from_file(csv_path)

    message = str(exc.value)
    assert (
        "Missing columns in Schwab transaction file: Amount, Description, "
        "Fees & Comm, Price, Quantity, Symbol." in message
    )
    assert "row 1" in message
