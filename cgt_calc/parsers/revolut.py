"""Revolut transaction parser."""

from __future__ import annotations

from collections import defaultdict
import csv
import datetime
from decimal import Decimal
from enum import StrEnum
from itertools import chain
import logging
import re
from typing import TYPE_CHECKING, ClassVar, Final, Literal, TextIO, overload, override

from cgt_calc.const import UK_TIMEZONE
from cgt_calc.exceptions import ParsingError
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    TransactionSource,
)
from cgt_calc.util import parse_decimal

from .base_parsers import StandardCSVParser

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class RevolutColumn(StrEnum):
    """Column names for the Revolut format."""

    DATE = "Date"
    TICKER = "Ticker"
    ACTION = "Type"
    QUANTITY = "Quantity"
    PRICE_PER_SHARE = "Price per share"
    TOTAL_AMOUNT = "Total Amount"
    CURRENCY = "Currency"
    FX_RATE = "FX Rate"


COLUMNS: Final[list[str]] = [column.value for column in RevolutColumn]
HEADER_LINE: Final = ",".join(COLUMNS) + "\n"
DIVIDEND_DOCS: Final = (
    "https://cgt-calc.uk/brokers/revolut/#dividends-and-withholding-tax"
)
LOGGER = logging.getLogger(__name__)


def _parse_timestamp(value: str) -> datetime.datetime:
    """Convert a Revolut timestamp to the instant it names, in UTC.

    The export states its times in UTC, so a value without a zone is read
    as UTC too rather than as the machine's local time. The instant is kept
    in UTC rather than in UK time because two aware datetimes sharing one
    `tzinfo` are compared by their wall clocks, ignoring `fold`: stated in
    London, 00:30 UTC on the morning the clocks go back reads as 01:30 BST
    and compares as later than 01:15 GMT, which is the instant 45 minutes
    after it. The date is taken in UK time, where the tax year and the
    matching rules are counted.
    """

    try:
        timestamp = datetime.datetime.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"Invalid timestamp: {value!r}") from err
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=datetime.UTC)
    return timestamp.astimezone(datetime.UTC)


def _action_from_str(label: str, file: Path) -> ActionType:
    """Convert string label to ActionType."""

    action = {
        "BUY - MARKET": ActionType.BUY,
        "BUY - LIMIT": ActionType.BUY,
        "SELL - MARKET": ActionType.SELL,
        "SELL - LIMIT": ActionType.SELL,
        "TRANSFER FROM REVOLUT BANK UAB TO REVOLUT SECURITIES EUROPE UAB": ActionType.TRANSFER,
        "TRANSFER FROM REVOLUT TRADING LTD TO REVOLUT SECURITIES EUROPE UAB": ActionType.TRANSFER,
        "STOCK SPLIT": ActionType.STOCK_SPLIT,
        "DIVIDEND": ActionType.DIVIDEND,
        "DIVIDEND TAX (CORRECTION)": ActionType.DIVIDEND_TAX,
        "CUSTODY FEE": ActionType.ADJUSTMENT,
        "CASH TOP-UP": ActionType.TRANSFER,
        "CASH WITHDRAWAL": ActionType.TRANSFER,
    }.get(label)
    if action is None:
        raise ParsingError(file, f"Unknown action: {label}")
    return action


@overload
def _parse_decimal(
    row: dict[str, str],
    column: RevolutColumn,
    *,
    allow_empty: Literal[True],
    default: Decimal,
    strip_currency: CurrencyCode | None = ...,
) -> Decimal: ...


@overload
def _parse_decimal(
    row: dict[str, str],
    column: RevolutColumn,
    *,
    allow_empty: Literal[True],
    default: None = ...,
    strip_currency: CurrencyCode | None = ...,
) -> Decimal | None: ...


@overload
def _parse_decimal(
    row: dict[str, str],
    column: RevolutColumn,
    *,
    allow_empty: Literal[False],
    default: None = ...,
    strip_currency: CurrencyCode | None = ...,
) -> Decimal: ...


def _parse_decimal(
    row: dict[str, str],
    column: RevolutColumn,
    *,
    allow_empty: bool,
    default: Decimal | None = None,
    strip_currency: CurrencyCode | None = None,
) -> Decimal | None:
    """Parse decimal value from the row, raising ValueError with context on failure."""

    value = row[column]
    if value == "":
        if allow_empty:
            return default
        raise ValueError(f"Missing value in column '{column.value}'")
    if strip_currency is not None:
        prefix = f"{strip_currency} "
        if not value.startswith(prefix):
            raise ValueError(
                f"Expected format '{strip_currency} value' in column "
                f"'{column.value}' but found: {value!r}"
            )
        value = value.removeprefix(prefix).strip()
    return parse_decimal(value, f"column '{column.value}'", strip=",")


class RevolutTransaction(BrokerTransaction):
    """Represents a single Revolut transaction."""

    def __init__(
        self,
        row: dict[str, str],
        file: Path,
    ):
        """Create transaction from a CSV row keyed by column name."""
        currency = CurrencyCode(row[RevolutColumn.CURRENCY])
        timestamp = _parse_timestamp(row[RevolutColumn.DATE])
        description = row[RevolutColumn.ACTION]
        action = _action_from_str(description, file)
        symbol = row[RevolutColumn.TICKER] or None
        quantity = _parse_decimal(row, RevolutColumn.QUANTITY, allow_empty=True)
        price = _parse_decimal(
            row,
            RevolutColumn.PRICE_PER_SHARE,
            allow_empty=True,
            default=Decimal(0),
            strip_currency=currency,
        )
        total = _parse_decimal(
            row, RevolutColumn.TOTAL_AMOUNT, allow_empty=False, strip_currency=currency
        )
        if quantity:
            price = total / quantity  # override CSV's rounding
        amount = -total if action is ActionType.BUY else total
        super().__init__(
            timestamp.astimezone(UK_TIMEZONE).date(),
            action,
            symbol,
            description,
            quantity,
            price,
            Decimal(0),
            amount,
            currency,
            "Revolut",
        )
        # The export times every row, which is what tells the calculation
        # whether a row sharing a date with a share reorganisation happened
        # before or after it.
        self.source = TransactionSource(timestamp=timestamp)


class RevolutParser(StandardCSVParser[RevolutTransaction]):
    """Parser for Revolut format transaction files."""

    arg_name = "revolut"
    pretty_name = "Revolut format"
    format_name = "CSV"
    argument_help = "Revolut Invest account statement in CSV format"

    columns: ClassVar[set[str]] = set(COLUMNS)

    @staticmethod
    def _has_header(line: str) -> bool:
        """Return True if the first line is likely a Revolut header."""
        first_row = next(csv.reader([line]), [])
        if not first_row:
            return False
        return all(re.sub("\\s+", "", value).isalpha() for value in first_row)

    @classmethod
    @override
    def pre_reading(cls, file: TextIO, file_path: Path) -> tuple[Iterable[str], int]:
        """Supply the header row for an export that omits it.

        The columns are then assumed to be in the standard order. A supplied
        header is not part of the file, so it sits before its first line and
        the rows keep the physical line numbers they have in the export.
        """
        lines = iter(file)
        first_line = next(lines, None)
        if first_line is None:
            raise ParsingError(file_path, "Revolut CSV file is empty")
        if cls._has_header(first_line):
            return chain([first_line], lines), 1
        LOGGER.warning(
            "Revolut CSV file %s is missing header row. "
            "The header is required but will be inferred for now.",
            file_path,
        )
        return chain([HEADER_LINE, first_line], lines), 0

    @classmethod
    @override
    def read_row(
        cls, row: dict[str, str], file_path: Path
    ) -> RevolutTransaction | None:
        """Read a single Revolut transaction from a CSV row."""
        transaction = RevolutTransaction(row, file_path)
        if (
            transaction.action == ActionType.TRANSFER
            and transaction.description.startswith("TRANSFER FROM REVOLUT")
        ):
            LOGGER.debug("Skipping %s", transaction.description)
            return None
        return transaction

    @classmethod
    @override
    def finalize_transactions(
        cls, transactions: list[RevolutTransaction]
    ) -> list[RevolutTransaction]:
        """Warn about the dividend tax the export leaves out.

        A `DIVIDEND` row is the cash received after foreign tax, and no row or
        column gives that tax (#1110). The dividend before tax cannot be
        worked out here: the rate depends on the holder and on the company,
        which the export names only by ticker.

        A `DIVIDEND TAX (CORRECTION)` row nearly always comes with its
        opposite within a second, and the two change nothing. The one seen on
        its own was the tax on a dividend paid in full, so the dividend a lone
        correction follows is not short of tax in the way the others are.
        """
        if any(t.action is ActionType.DIVIDEND for t in transactions):
            LOGGER.warning(
                "Revolut dividends are recorded as received: the export gives "
                "each one after foreign tax and does not give that tax. Unless "
                "you have added it in a RAW file, the report's dividend income "
                "is too low by any tax taken from the tax year's dividends, and "
                "that tax is not shown as tax at source. See %s",
                DIVIDEND_DOCS,
            )
        corrections: defaultdict[
            tuple[str | None, datetime.date, CurrencyCode], Decimal
        ] = defaultdict(Decimal)
        for transaction in transactions:
            if transaction.action is ActionType.DIVIDEND_TAX:
                assert transaction.amount is not None, "a Revolut row has a total"
                key = (transaction.symbol, transaction.date, transaction.currency)
                corrections[key] += transaction.amount
        for (symbol, date, currency), amount in corrections.items():
            if amount:
                LOGGER.warning(
                    "The %s dividend tax correction of %s %s on %s is not "
                    "cancelled by an opposite one that day, so it is counted as "
                    "a change to the tax at source of the dividend it follows. "
                    "Check that dividend's figures: see %s",
                    symbol,
                    amount,
                    currency,
                    date,
                    DIVIDEND_DOCS,
                )
        return transactions
