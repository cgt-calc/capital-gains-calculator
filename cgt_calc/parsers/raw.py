"""Raw transaction parser."""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
from enum import StrEnum
import io
from typing import TYPE_CHECKING, ClassVar, Final, TextIO, overload, override

from cgt_calc.csv_columns import CsvColumns
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    TransactionSource,
)
from cgt_calc.util import is_blank_row, parse_decimal

from .base_parsers import BaseSingleFileParser, read_csv_rows

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path


class RawColumn(StrEnum):
    """Column names for the RAW format."""

    DATE = "date"
    ACTION = "action"
    SYMBOL = "symbol"
    QUANTITY = "quantity"
    PRICE = "price"
    FEES = "fees"
    CURRENCY = "currency"
    # Optional: read only when the header names them.
    BROKER = "broker"
    REPLACES = "replaces"


_OPTIONAL_COLUMNS: Final = (RawColumn.BROKER, RawColumn.REPLACES)
# The seven every header names, in the order the guide gives them.
COLUMNS: Final[list[str]] = [
    column.value for column in RawColumn if column not in _OPTIONAL_COLUMNS
]
RAW_COLUMNS: Final = CsvColumns(
    tuple(COLUMNS), tuple(column.value for column in _OPTIONAL_COLUMNS)
)
# The ledger of a row that names no broker.
UNKNOWN_BROKER: Final = "Unknown"
# The rows that can stand in for one in a broker export: nothing their export
# rows carry is beyond the RAW columns, and replacing one moves no more money.
REPLACEABLE_ACTIONS: Final = frozenset(
    {ActionType.DIVIDEND_TAX, ActionType.STOCK_ACTIVITY}
)


def _parse_date(value: str, column: RawColumn, file: Path) -> dt.date:
    """Read a `YYYY-MM-DD` date, naming the column when it is not `date`."""
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as err:
        # "date", not "date format": a typed 2023-06-31 has the right form.
        name = "date" if column is RawColumn.DATE else f"{column.value} date"
        raise ParsingError(
            file,
            f"Invalid {name}: {value!r}, expected YYYY-MM-DD, e.g. '2023-08-15'",
        ) from err


def _action_from_str(label: str, file: Path) -> ActionType:
    """Convert string label to ActionType."""
    try:
        return ActionType[label.upper()]
    except KeyError as err:
        raise ParsingError(file, f"Unknown action: {label}") from err


@overload
def _parse_decimal(
    row: dict[RawColumn, str],
    column: RawColumn,
    *,
    default: Decimal,
) -> Decimal: ...


@overload
def _parse_decimal(
    row: dict[RawColumn, str],
    column: RawColumn,
    *,
    default: None = ...,
) -> Decimal | None: ...


def _parse_decimal(
    row: dict[RawColumn, str],
    column: RawColumn,
    *,
    default: Decimal | None = None,
) -> Decimal | None:
    """Parse decimal value from the row, raising ValueError with context on failure."""

    value = row[column]
    if value == "":
        return default

    return parse_decimal(value, f"column '{column.value}'", strip=",")


class RawTransaction(BrokerTransaction):
    """Represents a single raw transaction.

    Example format:
    date,action,symbol,quantity,price,fees,currency
    2023-02-09,DIVIDEND,OPRA,4200,0.80,0.0,USD
    2022-11-14,SELL,META,19,116.00,0.05,USD
    2022-08-15,BUY,META,105,180.50,0.00,USD
    2022-07-26,DIVIDEND,OTGLY,305,0.031737,0.0,USD
    2022-06-06,STOCK_SPLIT,AMZN,209,0.00,0.00,USD
    2023-03-16,TRANSFER_TO_SPOUSE,META,21.5,0.00,0.00,USD
    2023-03-16,TRANSFER_FROM_SPOUSE,META,21.5,95.60,0.00,GBP

    See tests/raw/data/test_data.csv for a sample file showing the expected format.
    """

    # The date cgt-calc reads for the export row this one takes the place of.
    replaces: dt.date | None

    def __init__(
        self,
        row: list[str],
        file: Path,
        columns: Mapping[str, int],
    ):
        """Create transaction from CSV row, read by its header's `columns`.

        `columns` maps each column the header names to its index, as
        `RAW_COLUMNS.positions` gives it.
        """
        if len(row) != len(columns):
            raise UnexpectedColumnCountError(row, len(columns), file)

        # A space typed beside a comma is not part of the value: kept on a
        # symbol, it would make a holding of its own.
        row_values: dict[RawColumn, str] = {
            column: row[columns[column]].strip()
            for column in RawColumn
            if column in columns
        }

        date = _parse_date(row_values[RawColumn.DATE], RawColumn.DATE, file)
        action = _action_from_str(row_values[RawColumn.ACTION], file)
        broker = row_values.get(RawColumn.BROKER) or UNKNOWN_BROKER
        replaces_str = row_values.get(RawColumn.REPLACES)
        self.replaces = (
            _parse_date(replaces_str, RawColumn.REPLACES, file)
            if replaces_str
            else None
        )
        if self.replaces is not None:
            if broker == UNKNOWN_BROKER:
                raise ValueError(
                    "A row that replaces a row of a broker export names that "
                    "broker in the 'broker' column."
                )
            if action not in REPLACEABLE_ACTIONS:
                raise ValueError(
                    f"A {action.name} row cannot replace a row of a broker "
                    "export: only DIVIDEND_TAX and STOCK_ACTIVITY rows can. "
                    "Edit a working copy of the export instead."
                )
        symbol = row_values[RawColumn.SYMBOL] or None
        quantity = _parse_decimal(row_values, RawColumn.QUANTITY)
        price = _parse_decimal(row_values, RawColumn.PRICE)
        fees = _parse_decimal(row_values, RawColumn.FEES, default=Decimal(0))
        # No action takes negative fees: deductions and withdrawals go in the
        # price. Other parsers can produce them legitimately (a commission
        # rebate, or rounding in a derived fee), so this is checked only where
        # a person types the value.
        if fees < 0:
            raise ValueError(
                "Fees cannot be negative. Enter the fees you paid as a positive number."
            )

        if price is not None and quantity is not None:
            amount = price * quantity

            if action is ActionType.BUY:
                amount = -abs(amount)
            amount -= fees
        else:
            amount = None
        # Tax deducted is money paid, so a positive tax row would be reported
        # as negative tax paid. A broker export can hold one, reversing an
        # earlier deduction, so like the fees above this is checked only where
        # a person types the value. The amount is checked rather than the
        # price, as a negative quantity reverses the sign of the price. A row
        # replacing an export row has that row's amount, so its sign is the
        # broker's: one that differs matches nothing and is refused there.
        if (
            action in {ActionType.DIVIDEND_TAX, ActionType.INTEREST_TAX}
            and amount is not None
            and amount > 0
            and self.replaces is None
        ):
            raise ValueError(
                f"{action.name} cannot be positive. Enter the tax deducted as a "
                "negative price; if some of it was refunded, enter only the part "
                "that was not."
            )

        currency = CurrencyCode(row_values[RawColumn.CURRENCY])
        super().__init__(
            date,
            action,
            symbol,
            "",
            quantity,
            price,
            fees,
            amount,
            currency,
            broker,
        )


def raw_file_of(rows: Iterable[BrokerTransaction]) -> Path | None:
    """Return the RAW file the run reads, if any of `rows` came from one.

    A RAW file with no rows leaves nothing to find, so it reads as none.
    """
    return next(
        (
            row.source.file
            for row in rows
            if isinstance(row, RawTransaction) and row.source
        ),
        None,
    )


class RawParser(BaseSingleFileParser[RawTransaction]):
    """Parser for RAW format transaction files."""

    arg_name = "raw"
    pretty_name = "RAW format"
    # The format documents that rows for one date are written in the order
    # they happened, each in the units in force at that point: see "The order
    # of rows on one date" in docs/brokers/raw.md, which is what a user writing
    # the file by hand is told to do. No broker export makes that promise.
    rows_in_time_order: ClassVar[bool] = True
    format_name = "CSV"
    argument_help = (
        "transactions in cgt-calc's own RAW CSV format, for other brokers and for "
        "events added by hand"
    )
    deprecated_flags: ClassVar[list[str]] = ["--raw"]

    @classmethod
    @override
    def recognise(cls, text: str, file_path: Path) -> list[str]:
        """Name the file when its first row passes the reader's header check.

        Read as `read_transactions` reads it. A file without its header row is
        refused by both: no row of data passes the header check.
        """
        first_row = next(csv.reader(io.StringIO(text), skipinitialspace=True), [])
        return ["a RAW format file"] if RAW_COLUMNS.matches(first_row) else []

    @classmethod
    @override
    def read_transactions(cls, file: TextIO, file_path: Path) -> list[RawTransaction]:
        """Read Raw transactions from file."""
        # A space after a comma is skipped here, not left to the trim in
        # `RawTransaction`: a value in quotes is only read as quoted when the
        # quote is the first thing in its cell.
        lines = read_csv_rows(file, file_path, skipinitialspace=True)

        if all(is_blank_row(row) for row in lines):
            raise ParsingError(file_path, "RAW CSV file is empty")

        columns = RAW_COLUMNS.positions(lines[0], file_path)
        transactions: list[RawTransaction] = []
        # Symbols are matched exactly, so `foo` and `FOO` would be two holdings
        # and a sale of one would not be matched with the other's purchases.
        # A symbol written in two letter cases is refused, not folded into one.
        # Maps each case-folded symbol to its first spelling and row.
        spellings: dict[str, tuple[str, int]] = {}
        for index, row in enumerate(lines[1:], start=2):
            # Skip harmless blank rows left by editors or tooling.
            if is_blank_row(row):
                continue
            try:
                transaction = RawTransaction(row, file_path, columns)
            except ParsingError as err:
                err.add_row_context(index)
                raise
            except ValueError as err:
                raise ParsingError(file_path, str(err), row_index=index) from err
            if (symbol := transaction.symbol) is not None:
                first, first_index = spellings.setdefault(
                    symbol.casefold(), (symbol, index)
                )
                if first != symbol:
                    raise ParsingError(
                        file_path,
                        f"RAW symbols '{first}' (row {first_index}) and '{symbol}' "
                        f"(row {index}) differ only by letter case. "
                        "Use one spelling consistently.",
                    )
            transaction.source = TransactionSource(row=index)
            transactions.append(transaction)
        return transactions
