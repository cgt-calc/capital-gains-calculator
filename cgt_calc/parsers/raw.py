"""Raw transaction parser."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal
from enum import StrEnum
import logging
from typing import TYPE_CHECKING, ClassVar, Final, TextIO, overload, override

from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    Isin,
    TransactionSource,
)
from cgt_calc.util import parse_decimal

from .base_parsers import BaseSingleFileParser

if TYPE_CHECKING:
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


COLUMNS: Final[list[str]] = [column.value for column in RawColumn]
CSV_COLUMNS_NUM: Final = len(COLUMNS)
# An optional eighth column, which a file has when its header names it.
ISIN_COLUMN: Final = "isin"
LOGGER = logging.getLogger(__name__)


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
    2023-02-09,DIVIDEND,OPRA,4200,0.80,0.0,USD
    2022-11-14,SELL,META,19,116.00,0.05,USD
    2022-08-15,BUY,META,105,180.50,0.00,USD
    2022-07-26,DIVIDEND,OTGLY,305,0.031737,0.0,USD
    2022-06-06,STOCK_SPLIT,AMZN,209,0.00,0.00,USD
    2023-03-16,TRANSFER_TO_SPOUSE,META,21.5,0.00,0.00,USD
    2023-03-16,TRANSFER_FROM_SPOUSE,META,21.5,95.60,0.00,GBP

    See tests/raw/data/test_data.csv for a sample file showing the expected format.
    """

    def __init__(
        self,
        row: list[str],
        file: Path,
        *,
        with_isin: bool = False,
    ):
        """Create transaction from CSV row."""
        columns_num = CSV_COLUMNS_NUM + with_isin
        if len(row) != columns_num:
            raise UnexpectedColumnCountError(row, columns_num, file)

        row_values: dict[RawColumn, str] = {
            column: row[i] for i, column in enumerate(RawColumn)
        }

        date_str = row_values[RawColumn.DATE]
        date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

        action = _action_from_str(row_values[RawColumn.ACTION], file)
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

        currency = CurrencyCode(row_values[RawColumn.CURRENCY])
        isin_raw = row[CSV_COLUMNS_NUM].strip() if with_isin else ""
        # An ISIN names a security, so beside a blank symbol it is a value
        # typed in the wrong row or column rather than something to ignore.
        if isin_raw and symbol is None:
            raise ValueError(
                "A row with an ISIN needs a symbol. Fill in the symbol or leave "
                "the ISIN blank."
            )
        isin = Isin(isin_raw) if isin_raw else None
        broker = "Unknown"
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
            isin,
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

    @staticmethod
    def _validate_header(header: list[str], file: Path) -> bool:
        """Validate the header row and say whether it names the ISIN column."""

        with_isin = len(header) > CSV_COLUMNS_NUM
        expected = [*COLUMNS, ISIN_COLUMN] if with_isin else COLUMNS
        if len(header) != len(expected):
            raise UnexpectedColumnCountError(header, len(expected), file, row_index=1)

        normalized = [value.strip().lower() for value in header]
        for index, (exp, act) in enumerate(
            zip(expected, normalized, strict=True), start=1
        ):
            if exp != act:
                raise ParsingError(
                    file,
                    f"Expected column {index} to be '{exp}' but found '{header[index - 1]}'",
                    row_index=1,
                )
        return with_isin

    @staticmethod
    def _has_header(first_row: list[str]) -> bool:
        """Return True if the first row is likely a RAW header."""

        if not first_row:
            return False
        return all(
            value.strip() != "" and value.strip().isalpha() for value in first_row
        )

    @classmethod
    @override
    def read_transactions(cls, file: TextIO, file_path: Path) -> list[RawTransaction]:
        """Read Raw transactions from file."""
        lines = list(csv.reader(file))

        if not lines:
            raise ParsingError(file_path, "RAW CSV file is empty")

        data_rows = lines
        start_index = 1
        with_isin = False
        if cls._has_header(lines[0]):
            with_isin = cls._validate_header(lines[0], file_path)
            data_rows = lines[1:]
            start_index = 2
        else:
            LOGGER.warning(
                "RAW CSV file %s is missing header row. The header is required but will be inferred for now.",
                file_path,
            )

        transactions: list[RawTransaction] = []
        for index, row in enumerate(data_rows, start=start_index):
            try:
                transaction = RawTransaction(row, file_path, with_isin=with_isin)
            except ParsingError as err:
                err.add_row_context(index)
                raise
            except ValueError as err:
                raise ParsingError(file_path, str(err), row_index=index) from err
            transaction.source = TransactionSource(row=index)
            transactions.append(transaction)
        return transactions
