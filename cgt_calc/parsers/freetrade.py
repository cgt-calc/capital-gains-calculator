"""Freetrade parser."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
import logging
from typing import TYPE_CHECKING, ClassVar, Final, TextIO, override

from cgt_calc.const import UK_TIMEZONE
from cgt_calc.exceptions import ParsingError, UnsupportedBrokerCurrencyError
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

BROKER_NAME: Final = "Freetrade"
LOGGER = logging.getLogger(__name__)


class FreetradeColumn(StrEnum):
    """Column names expected in Freetrade CSV exports."""

    TITLE = "Title"
    TYPE = "Type"
    TIMESTAMP = "Timestamp"
    ACCOUNT_CURRENCY = "Account Currency"
    TOTAL_AMOUNT = "Total Amount"
    BUY_SELL = "Buy / Sell"
    TICKER = "Ticker"
    ISIN = "ISIN"
    PRICE_PER_SHARE_ACCOUNT = "Price per Share in Account Currency"
    STAMP_DUTY = "Stamp Duty"
    QUANTITY = "Quantity"
    VENUE = "Venue"
    ORDER_ID = "Order ID"
    ORDER_TYPE = "Order Type"
    INSTRUMENT_CURRENCY = "Instrument Currency"
    TOTAL_SHARES_AMOUNT = "Total Shares Amount"
    PRICE_PER_SHARE = "Price per Share"
    FX_RATE = "FX Rate"
    BASE_FX_RATE = "Base FX Rate"
    FX_FEE_BPS = "FX Fee (BPS)"
    FX_FEE_AMOUNT = "FX Fee Amount"
    DIVIDEND_EX_DATE = "Dividend Ex Date"
    DIVIDEND_PAY_DATE = "Dividend Pay Date"
    DIVIDEND_ELIGIBLE_QUANTITY = "Dividend Eligible Quantity"
    DIVIDEND_AMOUNT_PER_SHARE = "Dividend Amount Per Share"
    DIVIDEND_GROSS_AMOUNT = "Dividend Gross Distribution Amount"
    DIVIDEND_NET_AMOUNT = "Dividend Net Distribution Amount"
    DIVIDEND_WITHHELD_PERCENTAGE = "Dividend Withheld Tax Percentage"
    DIVIDEND_WITHHELD_AMOUNT = "Dividend Withheld Tax Amount"


COLUMNS: Final[list[str]] = [column.value for column in FreetradeColumn]
REQUIRED_COLUMNS: Final[set[str]] = set(COLUMNS)

# Freetrade renamed these columns, older exports still use the names above.
COLUMN_ALIASES: Final[dict[str, str]] = {
    "Total Amount in Account Currency": FreetradeColumn.TOTAL_AMOUNT.value,
    "Total Amount in Instrument Currency": FreetradeColumn.TOTAL_SHARES_AMOUNT.value,
}

# Newer exports add a block of stock split columns. They are tolerated so that
# the header validates, their values are not read yet: a stock split row still
# fails as an unknown type.
STOCK_SPLIT_PREFIX: Final = "Stock Split "

# These Activity Feed rows point to documents rather than cash or security
# transactions. Freetrade includes them in an All Activity export.
IGNORED_TYPES: Final[frozenset[str]] = frozenset(
    {"MONTHLY_STATEMENT", "TAX_CERTIFICATE"}
)


def _parse_decimal(row: dict[str, str], column: FreetradeColumn) -> Decimal:
    """Parse Decimal value for column, raising ValueError with context on failure."""
    return parse_decimal(row[column], f"column '{column.value}'")


def _parse_optional_decimal(row: dict[str, str], column: FreetradeColumn) -> Decimal:
    """Parse a decimal that Freetrade leaves blank when it does not apply."""
    if row[column] == "":
        return Decimal(0)
    return _parse_decimal(row, column)


def _dividend_amount_in_gbp(row: dict[str, str], column: FreetradeColumn) -> Decimal:
    """Convert a dividend field from instrument currency to account currency."""
    amount = _parse_decimal(row, column)
    if amount != 0:
        instrument_currency = CurrencyCode(row[FreetradeColumn.INSTRUMENT_CURRENCY])
        if instrument_currency != "GBP":
            # Dividend exports express Base FX Rate as GBP per unit of the
            # instrument currency (unlike the trade-only FX Rate column).
            amount *= _parse_decimal(row, FreetradeColumn.BASE_FX_RATE)
    return amount


def _action_from_str(action_type: str, buy_sell: str, file: Path) -> ActionType:
    """Infer action type."""
    if action_type == "INTEREST_FROM_CASH":
        return ActionType.INTEREST
    if action_type == "DIVIDEND":
        return ActionType.DIVIDEND
    if action_type in {"TOP_UP", "WITHDRAWAL"}:
        return ActionType.TRANSFER
    if action_type in {"ORDER", "FREESHARE_ORDER"}:
        if buy_sell == "BUY":
            return ActionType.BUY
        if buy_sell == "SELL":
            return ActionType.SELL

        raise ParsingError(file, f"Unknown buy_sell: '{buy_sell}'")

    raise ParsingError(file, f"Unknown type: '{action_type}'")


class FreetradeTransaction(BrokerTransaction):
    """Represents a single Freetrade transaction."""

    def __init__(self, row: dict[str, str], file: Path) -> None:
        """Create transaction from a CSV row keyed by column name."""
        action = _action_from_str(
            row[FreetradeColumn.TYPE], row[FreetradeColumn.BUY_SELL], file
        )

        symbol = (
            row[FreetradeColumn.TICKER] if row[FreetradeColumn.TICKER] != "" else None
        )
        if symbol is None and action not in {ActionType.TRANSFER, ActionType.INTEREST}:
            raise ParsingError(file, f"No symbol for action: {action}")

        # The importer and calculation path below use the exported GBP account
        # currency fields. Reject another account currency rather than
        # silently treating it as sterling.
        if row[FreetradeColumn.ACCOUNT_CURRENCY] != "GBP":
            raise UnsupportedBrokerCurrencyError(
                file, BROKER_NAME, row[FreetradeColumn.ACCOUNT_CURRENCY]
            )

        fees = Decimal(0)
        if action in {ActionType.SELL, ActionType.BUY}:
            quantity = _parse_decimal(row, FreetradeColumn.QUANTITY)
            # These two fields are already in account currency. Total Amount
            # is the cash movement after fees, while the account-currency unit
            # price excludes them, so retaining the exported fee fields keeps
            # all three values mutually consistent.
            price = _parse_decimal(row, FreetradeColumn.PRICE_PER_SHARE_ACCOUNT)
            amount = _parse_decimal(row, FreetradeColumn.TOTAL_AMOUNT)
            fees = _parse_optional_decimal(
                row, FreetradeColumn.STAMP_DUTY
            ) + _parse_optional_decimal(row, FreetradeColumn.FX_FEE_AMOUNT)
            currency = CurrencyCode("GBP")
        elif action == ActionType.DIVIDEND:
            amount = _dividend_amount_in_gbp(row, FreetradeColumn.DIVIDEND_GROSS_AMOUNT)
            quantity, price = None, None
            currency = CurrencyCode("GBP")
        elif action in {ActionType.TRANSFER, ActionType.INTEREST}:
            amount = _parse_decimal(row, FreetradeColumn.TOTAL_AMOUNT)
            quantity, price = None, None
            currency = CurrencyCode("GBP")
        else:
            # _action_from_str refuses every other type, so only an action it
            # learns to return without a branch here can reach this.
            raise AssertionError(f"no branch for {action}")

        if row[FreetradeColumn.TYPE] == "FREESHARE_ORDER":
            price = Decimal(0)
            amount = Decimal(0)
            fees = Decimal(0)

        amount_negative = (
            action == ActionType.BUY or row[FreetradeColumn.TYPE] == "WITHDRAWAL"
        )
        if amount_negative:
            amount *= -1

        isin_raw = row.get(FreetradeColumn.ISIN)
        isin = Isin(isin_raw) if isin_raw else None

        # The timestamp is in UTC, but the date that drives the tax year and
        # the matching rules is the UK one.
        timestamp = datetime.fromisoformat(row[FreetradeColumn.TIMESTAMP])
        if timestamp.tzinfo is None:
            # The export states its times in UTC, so a value without a zone
            # is read as UTC too rather than as the machine's local time.
            timestamp = timestamp.replace(tzinfo=UTC)
        self.datetime = timestamp

        super().__init__(
            date=timestamp.astimezone(UK_TIMEZONE).date(),
            action=action,
            symbol=symbol,
            description=f"{row[FreetradeColumn.TITLE]} {action}",
            quantity=quantity,
            price=price,
            fees=fees,
            amount=amount,
            currency=currency,
            broker=BROKER_NAME,
            isin=isin,
        )


def _dividend_tax_transaction(
    transaction: FreetradeTransaction, row: dict[str, str]
) -> BrokerTransaction | None:
    """Create the tax-at-source cash movement carried on a dividend row."""
    if transaction.action is not ActionType.DIVIDEND:
        return None

    if row[FreetradeColumn.DIVIDEND_WITHHELD_AMOUNT] == "":
        return None
    amount = _dividend_amount_in_gbp(row, FreetradeColumn.DIVIDEND_WITHHELD_AMOUNT)
    if amount == 0:
        return None

    return BrokerTransaction(
        date=transaction.date,
        action=ActionType.DIVIDEND_TAX,
        symbol=transaction.symbol,
        description=f"{row[FreetradeColumn.TITLE]} {ActionType.DIVIDEND_TAX}",
        quantity=None,
        price=None,
        fees=Decimal(0),
        amount=-amount,
        currency=CurrencyCode("GBP"),
        broker=BROKER_NAME,
        isin=transaction.isin,
    )


class FreetradeParser(BaseSingleFileParser[BrokerTransaction]):
    """Parser for Freetrade transaction files."""

    arg_name = "freetrade"
    pretty_name = "Freetrade"
    format_name = "CSV"
    deprecated_flags: ClassVar[list[str]] = ["--freetrade"]

    @classmethod
    @override
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[BrokerTransaction]:
        """Parse Freetrade transactions from a CSV file."""
        lines = list(csv.reader(file))
        if not lines:
            raise ParsingError(file_path, "Freetrade CSV file is empty")
        header = [COLUMN_ALIASES.get(column, column) for column in lines[0]]
        cls._validate_header(header, file_path)
        lines = lines[1:]
        indexed_rows = list(enumerate(lines, start=2))
        # HACK: reverse transactions to avoid negative balance issues
        # the proper fix would be to use datetime in BrokerTransaction
        indexed_rows.reverse()
        transactions: list[BrokerTransaction] = []
        for index, row_raw in indexed_rows:
            # Exports may end with a blank line, which csv reads as an
            # empty row rather than a transaction.
            if not any(row_raw):
                continue
            row = dict(zip(header, row_raw, strict=False))
            action_type = row.get(FreetradeColumn.TYPE)
            if action_type in IGNORED_TYPES:
                LOGGER.debug(
                    "Skipping non-transaction Freetrade row %d (%s)",
                    index,
                    action_type,
                )
                continue
            try:
                transaction = FreetradeTransaction(row, file_path)
                transaction.source = TransactionSource(
                    row=index, timestamp=transaction.datetime
                )
                transactions.append(transaction)
                dividend_tax = _dividend_tax_transaction(transaction, row)
                if dividend_tax is not None:
                    dividend_tax.source = TransactionSource(
                        row=index, timestamp=transaction.datetime
                    )
                    transactions.append(dividend_tax)
            except ParsingError as err:
                err.add_row_context(index)
                raise
            except ValueError as err:
                raise ParsingError(file_path, str(err), row_index=index) from err
        return transactions

    @staticmethod
    def _validate_header(header: list[str], file: Path) -> None:
        """Check if header is valid."""
        provided = set(header)
        missing = REQUIRED_COLUMNS - provided
        if missing:
            missing_columns = ", ".join(sorted(missing))
            raise ParsingError(file, f"Missing columns: {missing_columns}", row_index=1)

        unknown = {
            column
            for column in provided - REQUIRED_COLUMNS
            if not column.startswith(STOCK_SPLIT_PREFIX)
        }
        if unknown:
            unknown_columns = ", ".join(sorted(unknown))
            raise ParsingError(file, f"Unknown columns: {unknown_columns}", row_index=1)
