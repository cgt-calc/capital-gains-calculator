"""Revolut crypto account statement parser."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Final, override

from cgt_calc.const import UK_TIMEZONE
from cgt_calc.exceptions import UnsupportedActionError
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    TransactionSource,
)
from cgt_calc.util import parse_decimal

from .base_parsers import StandardCSVParser

if TYPE_CHECKING:
    from pathlib import Path


class RevolutCryptoColumn(StrEnum):
    """Column names for the Revolut crypto account statement."""

    SYMBOL = "Symbol"
    TYPE = "Type"
    QUANTITY = "Quantity"
    PRICE = "Price"
    VALUE = "Value"
    FEES = "Fees"
    DATE = "Date"


# A coin's symbol can also be a share's ticker: a US bitcoin fund trades as
# BTC. The prefix keeps the coin out of that share's pool.
SYMBOL_PREFIX: Final = "CRYPTO:"
DATE_FORMAT: Final = "%d %b %Y, %H:%M:%S"
CURRENCY_SYMBOLS: Final[dict[str, CurrencyCode]] = {
    "$": CurrencyCode("USD"),
    "£": CurrencyCode("GBP"),
    "€": CurrencyCode("EUR"),
}
ACTIONS: Final[dict[str, ActionType]] = {
    "Buy": ActionType.BUY,
    "Sell": ActionType.SELL,
}


def _parse_money(
    row: dict[str, str], column: RevolutCryptoColumn
) -> tuple[Decimal, CurrencyCode]:
    """Split a value such as `$2,000.00` into its amount and currency."""
    value = row[column].strip()
    currency = CURRENCY_SYMBOLS.get(value[:1])
    if currency is None:
        raise ValueError(
            f"Expected a $, £ or € amount in column '{column.value}' but found: "
            f"{value!r}"
        )
    return parse_decimal(value[1:], f"column '{column.value}'", strip=","), currency


def _parse_timestamp(value: str) -> dt.datetime:
    """Read a statement time as UK time and return the instant in UTC.

    The statement does not name its time zone. It is read as UK time, the
    zone the tax year and the matching rules are counted in, and kept in UTC
    for the reason the Revolut Invest parser's `_parse_timestamp` gives.
    """
    try:
        local = dt.datetime.strptime(value, DATE_FORMAT)
    except ValueError as err:
        raise ValueError(f"Invalid date: {value!r}") from err
    return local.replace(tzinfo=UK_TIMEZONE).astimezone(dt.UTC)


class RevolutCryptoTransaction(BrokerTransaction):
    """Represents a single Revolut crypto purchase or sale."""

    def __init__(self, row: dict[str, str], file: Path):
        """Create transaction from a CSV row keyed by column name."""
        label = row[RevolutCryptoColumn.TYPE]
        action = ACTIONS.get(label)
        if action is None:
            raise UnsupportedActionError(
                file,
                f"Unsupported action: {label}. Only Buy and Sell rows are read. "
                "See https://cgt-calc.uk/brokers/revolut-crypto/#supported-activity",
            )
        quantity = parse_decimal(
            row[RevolutCryptoColumn.QUANTITY],
            f"column '{RevolutCryptoColumn.QUANTITY.value}'",
        )
        if quantity <= 0:
            raise ValueError(f"Quantity must be positive but found: {quantity}")
        value, currency = _parse_money(row, RevolutCryptoColumn.VALUE)
        fees, fee_currency = (
            _parse_money(row, RevolutCryptoColumn.FEES)
            if row[RevolutCryptoColumn.FEES].strip()
            else (Decimal(0), currency)
        )
        if fee_currency != currency:
            raise ValueError(
                f"Fees are in {fee_currency} but the value is in {currency}"
            )
        timestamp = _parse_timestamp(row[RevolutCryptoColumn.DATE])
        # The value is the coins' worth and the fee is charged on top of it,
        # so a purchase costs both and a sale brings in the value less the fee.
        amount = -(value + fees) if action is ActionType.BUY else value - fees
        super().__init__(
            date=timestamp.astimezone(UK_TIMEZONE).date(),
            action=action,
            symbol=SYMBOL_PREFIX + row[RevolutCryptoColumn.SYMBOL].strip(),
            description=label,
            quantity=quantity,
            # Recalculated rather than read, so the cost or proceeds agree
            # with the rounded price's total exactly.
            price=value / quantity,
            fees=fees,
            amount=amount,
            currency=currency,
            broker="Revolut Crypto",
            # The money is paid from or into the Revolut current account,
            # which this statement does not cover, so no balance is kept.
            affects_cash_balance=False,
        )
        self.source = TransactionSource(timestamp=timestamp)


class RevolutCryptoParser(StandardCSVParser[RevolutCryptoTransaction]):
    """Parser for the Revolut crypto account statement."""

    arg_name = "revolut-crypto"
    pretty_name = "Revolut Crypto"
    format_name = "CSV"
    argument_help = "Revolut crypto account statement in CSV format"

    columns: ClassVar[set[str]] = {column.value for column in RevolutCryptoColumn}

    @classmethod
    @override
    def read_row(
        cls, row: dict[str, str], file_path: Path
    ) -> RevolutCryptoTransaction | None:
        """Read a single Revolut crypto transaction from a CSV row."""
        return RevolutCryptoTransaction(row, file_path)
