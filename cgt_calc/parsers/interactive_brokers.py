"""Interactive brokers transaction parser."""

from __future__ import annotations

import datetime
from decimal import Decimal
from enum import StrEnum
from itertools import chain
import re
from typing import TYPE_CHECKING, ClassVar, Final, override

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode, Isin
from cgt_calc.util import parse_decimal

from .base_parsers import StandardCSVParser

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


EXPECTED_COLS_IN_SUMMARY_SECTION: Final[int] = 4

# Dividend and withholding rows name the security as "VT(US9220427424) Cash
# Dividend ...". Trade rows carry a plain description, so this is best effort.
_ISIN_IN_DESCRIPTION_RE: Final = re.compile(r"^[^\s(]+\((?P<isin>[A-Z0-9]{12})\)")


def _isin_from_description(description: str) -> Isin | None:
    """Return the ISIN the description is prefixed with, if it has one."""
    match = _ISIN_IN_DESCRIPTION_RE.match(description)
    if match is None:
        return None
    return Isin.parse(match.group("isin"))


class InteractiveBrokersColumn(StrEnum):
    """Column names for the interactive brokers format."""

    TRANSACTION_HISTORY = "Transaction History"
    HEADER = "Header"
    DATE = "Date"
    ACCOUNT = "Account"
    DESCRIPTION = "Description"
    TRANSACTION_TYPE = "Transaction Type"
    SYMBOL = "Symbol"
    QUANTITY = "Quantity"
    PRICE = "Price"
    PRICE_CURRENCY = "Price Currency"
    GROSS_AMOUNT = "Gross Amount "
    COMMISSION = "Commission"
    NET_AMOUNT = "Net Amount"
    EXCHANGE_RATE = "Exchange Rate"


_IBKR_OPTIONAL_COLUMNS: Final[set[str]] = {
    InteractiveBrokersColumn.PRICE_CURRENCY,
    InteractiveBrokersColumn.EXCHANGE_RATE,
}


def _action_from_str(action_type: str, file_path: Path) -> ActionType:
    """Infer action type."""
    if action_type == "Credit Interest":
        return ActionType.INTEREST
    # A payment in lieu arrives instead of a dividend when the holding is out on
    # loan. It is the rarer of the two, so a parser that knows it and not the
    # ordinary dividend fails for most holders on their first real statement.
    if action_type in {"Dividend", "Payment in Lieu"}:
        return ActionType.DIVIDEND
    if action_type in {"Deposit", "Withdrawal"}:
        return ActionType.TRANSFER
    if action_type == "Buy":
        return ActionType.BUY
    if action_type == "Sell":
        return ActionType.SELL
    if action_type == "Foreign Tax Withholding":
        return ActionType.DIVIDEND_TAX
    if action_type == "Other Fee":
        return ActionType.FEE
    # "FX Translations P&L" arrives as an Adjustment: the revaluation of a
    # foreign currency balance. A "Forex Trade Component" is the base-currency
    # net of a conversion, filed under the currency pair. Neither is a trade in
    # a security: both move the cash balance and create no taxable event, which
    # is what ADJUSTMENT means here and how the Schwab parser treats its own.
    # Read as a fee instead, a component that brings base currency in is
    # refused outright, and one that takes some out opens a pooled cost under
    # the currency pair.
    if action_type in {"Adjustment", "Forex Trade Component"}:
        return ActionType.ADJUSTMENT

    raise ParsingError(file_path, f"Unknown type: '{action_type}'")


def _parse_str(row: dict[str, str], column: str) -> str | None:
    """Read a text column, treating IBKR's "-" placeholder as absent.

    IBKR writes "-" wherever a column does not apply to a row, in text columns
    as well as numeric ones. Taking it at face value puts a currency called "-"
    on the transaction.
    """
    value = row.get(column)
    if not value or value == "-":
        return None
    return value


def _parse_decimal(row: dict[str, str], column: str) -> Decimal | None:
    """Parse decimal value from the row, raising ValueError with context on failure."""

    value = row[column]
    if value == "-":
        return None

    return parse_decimal(value, f"column '{column}'", strip=",")


class InteractiveBrokersTransaction(BrokerTransaction):
    """Represents a single Interactive Brokers transaction."""

    def __init__(
        self,
        row: dict[str, str],
        file_path: Path,
    ):
        """Create transaction from CSV row."""
        date_str = row[InteractiveBrokersColumn.DATE]
        date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()

        action = _action_from_str(
            row[InteractiveBrokersColumn.TRANSACTION_TYPE], file_path
        )
        symbol = _parse_str(row, InteractiveBrokersColumn.SYMBOL)

        if symbol is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
        quantity = _parse_decimal(row, InteractiveBrokersColumn.QUANTITY)
        price = _parse_decimal(row, InteractiveBrokersColumn.PRICE)
        amount = _parse_decimal(row, InteractiveBrokersColumn.NET_AMOUNT)
        fees = _parse_decimal(row, InteractiveBrokersColumn.COMMISSION) or Decimal(0)
        price_currency = CurrencyCode(
            _parse_str(row, InteractiveBrokersColumn.PRICE_CURRENCY) or "GBP"
        )
        exchange_rate = (
            _parse_decimal(row, InteractiveBrokersColumn.EXCHANGE_RATE)
            if InteractiveBrokersColumn.EXCHANGE_RATE in row
            else None
        )

        # Withholding with no security behind it is tax on the account's cash
        # interest, not on a dividend. Read as dividend tax it was filed
        # against the literal "-", a placeholder holding that pooled and
        # priced as though it were real; INTEREST_TAX reports it against the
        # interest it was taken from. The Schwab parser makes the same switch.
        if action is ActionType.DIVIDEND_TAX and symbol is None:
            action = ActionType.INTEREST_TAX

        # "Other Fee" covers both a charge against a holding, such as an ADR
        # fee, and an account-level charge with no security behind it, such as
        # a market-data subscription. Only the first can be a FEE, which adds
        # to a named holding's pooled cost; the second has nowhere to attach
        # and moves the cash balance alone.
        if action is ActionType.FEE and symbol is None:
            action = ActionType.ADJUSTMENT

        # An adjustment moves the cash balance and nothing else: the calculator
        # reads its amount and never its symbol, quantity or price. IBKR files a
        # Forex Trade Component under the currency pair and gives it both a
        # quantity and a price, so clear the columns that describe a security
        # rather than leave a currency pair looking like a holding. Dropping the
        # price before it is converted also keeps a pair priced in a currency
        # the row states no rate for from being refused over a price nothing
        # reads.
        if action is ActionType.ADJUSTMENT:
            symbol = None
            quantity = None
            price = None
            fees = Decimal(0)

        # The Gross/Net Amount and Commission columns are always in the account's
        # base currency (GBP), so that is the transaction's currency whatever
        # priced the leg. Price Currency describes the price alone: convert a
        # foreign price to the base currency where a rate is given. Only a Buy
        # or Sell reads the price back, to check it against the amount
        # (quantity x price + fees ≈ |amount|), so only those two are refused
        # over a price nothing can convert; every other row's price is left
        # foreign rather than blocking rows the calculator never checks it on.
        if price is not None and price_currency != "GBP":
            if exchange_rate is not None:
                price = price * exchange_rate
            elif action in {ActionType.BUY, ActionType.SELL}:
                raise ParsingError(
                    file_path,
                    f"Price is in {price_currency} but the Exchange Rate column is "
                    "missing or empty, so the price cannot be converted to the "
                    "account's base currency (GBP), which Gross Amount, Commission "
                    "and Net Amount are in. Re-export the statement with the "
                    "Exchange Rate filled in for this row: a Buy or Sell always "
                    "needs a price, so clearing it only trades this error for a "
                    "missing-price one.",
                )

        super().__init__(
            date=date,
            action=action,
            symbol=symbol,
            description=row[InteractiveBrokersColumn.DESCRIPTION],
            quantity=abs(quantity) if quantity is not None else None,
            price=price,
            fees=-fees,
            amount=amount,
            currency=CurrencyCode("GBP"),
            broker="Interactive Brokers",
            isin=_isin_from_description(row[InteractiveBrokersColumn.DESCRIPTION]),
        )


class InteractiveBrokersParser(StandardCSVParser[InteractiveBrokersTransaction]):
    """Parser for Interactive Brokers format transaction files."""

    arg_name = "interactive-brokers"
    pretty_name = "Interactive Brokers"
    format_name = "CSV"
    optional_columns: ClassVar[set[str]] = _IBKR_OPTIONAL_COLUMNS
    columns: ClassVar[set[str]] = {
        column.value
        for column in InteractiveBrokersColumn
        if column.value not in _IBKR_OPTIONAL_COLUMNS
    }

    @classmethod
    @override
    def read_row(
        cls, row: dict[str, str], file_path: Path
    ) -> InteractiveBrokersTransaction | None:
        """Read a single transaction from a row in the CSV."""
        return InteractiveBrokersTransaction(row, file_path)

    @classmethod
    @override
    def pre_reading(
        cls, file: Iterable[str], file_path: Path
    ) -> tuple[Iterable[str], int]:
        """Skip Statement and Summary sections. Transaction History is the important one."""
        for row, line in enumerate(file, start=1):
            # Validate we're dealing with a GBP account
            if line.startswith("Summary,Data,Base Currency,"):
                rows = line.split(",")
                if (
                    len(rows) < EXPECTED_COLS_IN_SUMMARY_SECTION
                    or rows[3].strip() != "GBP"
                ):
                    raise ParsingError(
                        file_path,
                        f"Unexpected base currency: '{rows[3]}', only GBP is supported",
                    )
            if line.startswith("Transaction History"):
                return chain([line], file), row
        raise ParsingError(
            file_path,
            "Couldn't find Transaction History header, is this the right file?",
        )

    @staticmethod
    def _by_date_and_action(
        transaction: BrokerTransaction,
    ) -> tuple[datetime.date, bool]:
        """Sort by date and action type."""

        # If there's a deposit in the same second as a buy
        # (happens with the referral award at least)
        # we want to put the buy last to avoid negative balance errors.
        # Tax withheld at source goes last for the same reason: IBKR lists it
        # before the dividend it was taken from.
        return (
            transaction.date,
            transaction.action in {ActionType.BUY, ActionType.DIVIDEND_TAX},
        )

    @classmethod
    @override
    def post_process_transactions(
        cls, transactions: list[InteractiveBrokersTransaction]
    ) -> list[InteractiveBrokersTransaction]:
        """Sort transactions by date, buys and withheld tax last."""
        transactions.sort(key=cls._by_date_and_action)
        return transactions
