"""Raw transaction parser."""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
from enum import StrEnum
import io
import logging
from typing import TYPE_CHECKING, ClassVar, Final, TextIO, overload, override

from cgt_calc.const import LSE_MARKET, US_MARKET
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

LOGGER = logging.getLogger(__name__)


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
    MARKET = "market"
    # Read only on an EXCHANGE row: the asset received and the fee's asset.
    TO_SYMBOL = "to_symbol"
    TO_QUANTITY = "to_quantity"
    FEE_SYMBOL = "fee_symbol"
    FEE_QUANTITY = "fee_quantity"


EXCHANGE_COLUMNS: Final = (
    RawColumn.TO_SYMBOL,
    RawColumn.TO_QUANTITY,
    RawColumn.FEE_SYMBOL,
    RawColumn.FEE_QUANTITY,
)
_OPTIONAL_COLUMNS: Final = (
    RawColumn.BROKER,
    RawColumn.REPLACES,
    RawColumn.MARKET,
    *EXCHANGE_COLUMNS,
)
# The seven every header names, in the order the guide gives them.
COLUMNS: Final[list[str]] = [
    column.value for column in RawColumn if column not in _OPTIONAL_COLUMNS
]
RAW_COLUMNS: Final = CsvColumns(
    tuple(COLUMNS), tuple(column.value for column in _OPTIONAL_COLUMNS)
)
# The rows other than EXCHANGE that can name an asset their fee was paid in.
ASSET_FEE_ACTIONS: Final = (
    ActionType.BUY,
    ActionType.SELL,
    ActionType.TRANSFER,
    ActionType.SPEND,
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


def _article(action: ActionType) -> str:
    """Return "A" or "An" for a row of `action`, as its name is read aloud."""
    return "An" if action.name[0] in "AEIOU" else "A"


def _parse_asset_fee(
    row: dict[RawColumn, str], action: ActionType, fees: Decimal
) -> tuple[str | None, Decimal | None]:
    """Read the asset a fee was paid in and its units, if it was not cash.

    Return `fee_symbol` and `fee_quantity`, both None for a fee paid in cash.
    """
    currency = row[RawColumn.CURRENCY]
    fee_symbol = row[RawColumn.FEE_SYMBOL] or None
    fee_quantity = _parse_decimal(row, RawColumn.FEE_QUANTITY)
    if fee_symbol is None:
        if fee_quantity is not None:
            raise ValueError(
                "A 'fee_quantity' needs the asset the fee was paid in, in the "
                "'fee_symbol' column. For a fee paid in cash, leave both blank "
                "and enter it in 'fees'."
            )
        return None, None
    if fee_symbol == currency:
        raise ValueError(
            f"For a fee paid in {currency} cash, leave 'fee_symbol' and "
            "'fee_quantity' blank and enter it in 'fees'."
        )
    if fee_quantity is None or fee_quantity <= 0:
        raise ValueError(
            f"{_article(action)} {action.name} row whose fee was paid in "
            f"{fee_symbol} gives the units paid as a positive 'fee_quantity'."
        )
    if fees <= 0:
        raise ValueError(
            f"{_article(action)} {action.name} row whose fee was paid in "
            f"{fee_symbol} gives the fee's market value in {currency} in the "
            "'fees' column."
        )
    return fee_symbol, fee_quantity


def _refuse_filled(
    row: dict[RawColumn, str], action: ActionType, columns: tuple[RawColumn, ...]
) -> None:
    """Refuse a value in any of `columns`, which a row of `action` does not read."""
    filled = [column.value for column in columns if row[column]]
    if not filled:
        return
    if RawColumn.TO_SYMBOL in columns:
        readers = "an EXCHANGE row"
    else:
        *others, last = (reader.name for reader in ASSET_FEE_ACTIONS)
        readers = f"an EXCHANGE, {', '.join(others)} or {last} row"
    raise ValueError(
        f"The {', '.join(repr(name) for name in filled)} "
        f"{'column is' if len(filled) == 1 else 'columns are'} read only "
        f"on {readers}. Leave {'it' if len(filled) == 1 else 'them'} "
        f"blank on a {action.name} row."
    )


def _parse_exchange(
    row: dict[RawColumn, str],
    action: ActionType,
    symbol: str | None,
    quantity: Decimal | None,
    price: Decimal | None,
    fees: Decimal,
) -> tuple[str | None, Decimal | None, str | None, Decimal | None]:
    """Read the other side of an EXCHANGE row and any asset its fee was paid in.

    Return `to_symbol`, `to_quantity`, `fee_symbol` and `fee_quantity`. The
    rows in `ASSET_FEE_ACTIONS` can name a fee asset too; any other row has
    none of them.
    """
    # The exchange columns are optional, so a header can leave any of them
    # out: one it does not name reads as blank.
    row = dict.fromkeys(EXCHANGE_COLUMNS, "") | row
    if action is not ActionType.EXCHANGE:
        _refuse_filled(row, action, (RawColumn.TO_SYMBOL, RawColumn.TO_QUANTITY))
        if action not in ASSET_FEE_ACTIONS:
            _refuse_filled(row, action, (RawColumn.FEE_SYMBOL, RawColumn.FEE_QUANTITY))
            return None, None, None, None
        fee_symbol, fee_quantity = _parse_asset_fee(row, action, fees)
        if action is not ActionType.TRANSFER:
            return None, None, fee_symbol, fee_quantity
        # The asset moved stays in the one pool cgt-calc keeps for it, so
        # only the fee changes anything: the row itself moves no cash.
        if fee_symbol is not None and symbol is not None and price:
            raise ValueError(
                f"A TRANSFER of {symbol} moves no cash. Enter 0 as its price; "
                "the fee's value goes in 'fees'."
            )
        return None, None, fee_symbol, fee_quantity
    currency = row[RawColumn.CURRENCY]
    if symbol is None:
        raise ValueError("An EXCHANGE row names the asset given up as its symbol.")
    if quantity is None or quantity <= 0:
        raise ValueError(
            "An EXCHANGE row gives the units given up as a positive quantity."
        )
    if price is None or price <= 0:
        raise ValueError(
            "An EXCHANGE row gives the market value of one unit given up as a "
            "positive price."
        )
    to_symbol = row[RawColumn.TO_SYMBOL]
    if not to_symbol:
        raise ValueError(
            "An EXCHANGE row names the asset received in the 'to_symbol' column."
        )
    if to_symbol == symbol:
        raise ValueError(
            f"An EXCHANGE row gives up and receives the same asset, {symbol}. "
            "Name the asset received in the 'to_symbol' column."
        )
    if currency in {symbol, to_symbol}:
        raise ValueError(
            f"An EXCHANGE row swaps one asset for another, not for {currency} "
            "cash: write a SELL row for cash received, or a BUY row for cash paid."
        )
    to_quantity = _parse_decimal(row, RawColumn.TO_QUANTITY)
    if to_quantity is None or to_quantity <= 0:
        raise ValueError(
            "An EXCHANGE row gives the units received as a positive 'to_quantity'."
        )
    fee_symbol, fee_quantity = _parse_asset_fee(row, action, fees)
    return to_symbol, to_quantity, fee_symbol, fee_quantity


def _check_spend(
    action: ActionType,
    symbol: str | None,
    quantity: Decimal | None,
    price: Decimal | None,
    fees: Decimal,
    fee_symbol: str | None,
    currency: str,
) -> None:
    """Check that a row spending an asset names it, its units and its value."""
    if action is not ActionType.SPEND:
        return
    if symbol is None:
        raise ValueError("A SPEND row names the asset given as its symbol.")
    if symbol == currency:
        raise ValueError(
            f"A SPEND row gives an asset, not {currency} cash: write an "
            "ADJUSTMENT row for cash paid."
        )
    if quantity is None or quantity <= 0:
        raise ValueError("A SPEND row gives the units given as a positive quantity.")
    if price is None or price <= 0:
        raise ValueError(
            "A SPEND row gives the market value of one unit given as a positive price."
        )
    # Nothing on the row moves cash to take a cash fee from.
    if fees and fee_symbol is None:
        raise ValueError(
            "A SPEND row's fee is paid in an asset: name it in 'fee_symbol' and "
            "its units in 'fee_quantity'. Leave 'fees' blank or 0 for no fee."
        )


def _check_reward(
    action: ActionType,
    symbol: str | None,
    quantity: Decimal | None,
    price: Decimal | None,
    fees: Decimal,
    currency: str,
) -> None:
    """Check that a row receiving an asset as income names it, its units and value.

    The value is the income, so it cannot be left for cgt-calc to look up.
    """
    if action is not ActionType.REWARD:
        return
    if symbol is None:
        raise ValueError("A REWARD row names the asset received as its symbol.")
    if symbol == currency:
        raise ValueError(
            f"A REWARD row receives an asset, not {currency} cash: write an "
            "INTEREST row for interest paid in cash."
        )
    if quantity is None or quantity <= 0:
        raise ValueError(
            "A REWARD row gives the units received as a positive quantity."
        )
    if price is None or price <= 0:
        raise ValueError(
            "A REWARD row gives the market value of one unit received as a "
            "positive price."
        )
    # A fee taken from the reward leaves only the units received to record.
    if fees:
        raise ValueError(
            "A REWARD row records the units received after any fee taken from "
            "them. Leave 'fees' blank or 0."
        )


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
    2023-03-17,SPEND,ETH,0.002,2000.00,0.00,GBP
    2023-03-17,REWARD,ETH,0.01,2400.00,0.00,GBP

    An EXCHANGE row needs the optional exchange columns:
    date,action,symbol,quantity,price,fees,currency,to_symbol,to_quantity,fee_symbol,fee_quantity
    2025-06-03,EXCHANGE,BTC,0.5,40000,3.00,GBP,ETH,10,BNB,0.01

    A BUY, SELL, TRANSFER or SPEND row can name the asset its fee was paid
    in, in the same columns, such as a transfer between your own wallets:
    2025-06-04,TRANSFER,ETH,2,0,4.00,GBP,,,ETH,0.002

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
        # A market code has no letter case, unlike a symbol, which is kept
        # as written. Blank is unknown, as a row without the column is.
        market = row_values.get(RawColumn.MARKET, "").upper()
        if market not in {"", US_MARKET, LSE_MARKET}:
            raise ValueError(
                f"Unknown market: {row_values[RawColumn.MARKET]!r}. Write "
                f"{US_MARKET} for a security listed in the United States, on an "
                f"exchange or over the counter, or {LSE_MARKET} for one listed on "
                "the London Stock Exchange. Leave the cell blank for any other "
                "market."
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

        to_symbol, to_quantity, fee_symbol, fee_quantity = _parse_exchange(
            row_values, action, symbol, quantity, price, fees
        )
        _check_spend(
            action,
            symbol,
            quantity,
            price,
            fees,
            fee_symbol,
            row_values[RawColumn.CURRENCY],
        )
        _check_reward(
            action, symbol, quantity, price, fees, row_values[RawColumn.CURRENCY]
        )

        if price is not None and quantity is not None:
            amount = price * quantity

            if action is ActionType.BUY:
                amount = -abs(amount)
            # A fee paid in an asset is taken as if it were cash here: the
            # sale of the fee asset its row is read with returns that cash.
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
            to_symbol=to_symbol,
            to_quantity=to_quantity,
            fee_symbol=fee_symbol,
            fee_quantity=fee_quantity,
        )
        self.market = market or None


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
            for symbol in (
                transaction.symbol,
                transaction.to_symbol,
                transaction.fee_symbol,
            ):
                if symbol is None:
                    continue
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
            # A price of 0 is right for worthless shares but is usually a value
            # left out. A row replacing a vest is refused at 0 once its vest
            # is found, so it is not warned about here.
            if (
                transaction.action is ActionType.STOCK_ACTIVITY
                and transaction.price == 0
                and transaction.replaces is None
            ):
                LOGGER.warning(
                    "%s, row %d: the STOCK_ACTIVITY row acquires %s %s on %s at a "
                    "price of 0, so they cost nothing but any fees, which "
                    "overstates the gain on a later sale. Unless the shares were "
                    "worth nothing that day, enter their market value per share "
                    "as the price.",
                    file_path,
                    index,
                    transaction.quantity,
                    transaction.symbol,
                    transaction.date,
                )
            transactions.append(transaction)
        return transactions
