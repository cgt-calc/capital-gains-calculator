"""Charles Schwab parser."""

from __future__ import annotations

import argparse
from collections import OrderedDict, defaultdict
import csv
from dataclasses import dataclass, field, replace
import datetime
from decimal import Decimal
from enum import StrEnum
import io
import itertools
import logging
from typing import TYPE_CHECKING, ClassVar, Final, TextIO, override

import shtab

from cgt_calc.args_validators import (
    STDIN_PATH,
    DeprecatedAction,
    existing_directory_type,
    existing_file_or_stdin_type,
    set_completer,
)
from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import (
    CgtError,
    ParsingError,
    SymbolMissingError,
    UnexpectedColumnCountError,
    UnexpectedRowCountError,
)
from cgt_calc.logging import parsing_msg
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    TransactionSource,
)
from cgt_calc.parsers.schwab_cusip_bonds import adjust_cusip_bond_price
from cgt_calc.parsers.schwab_equity_award_json import (
    COMPLETE_CSV_COLUMNS,
    COMPLETE_EXPORT_CAVEAT,
    SchwabEquityAwardsParser,
    lapse_award_prices,
    lapse_only_rows,
    price_only_alone_message,
)
from cgt_calc.parsers.schwab_options import (
    parse_option_contract,
    reconcile_written_options,
)
from cgt_calc.util import parse_decimal

from .base_parsers import BaseSingleFileParser, next_account_token, read_input

if TYPE_CHECKING:
    from pathlib import Path

OLD_COLUMNS_NUM: Final = 9
NEW_COLUMNS_NUM: Final = 8
LOGGER = logging.getLogger(__name__)

# Cancel Buy search window: Arbitrary time window chosen as a sensible limit for
# how far to search backward from a Cancel Buy to find the original Buy transaction.
# This is not based on any documented Schwab settlement period - just a practical limit.
CANCEL_BUY_SEARCH_DAYS: Final = 5


class RequiredTransactionsColumn(StrEnum):
    """Column names for Schwab transactions file."""

    DATE = "Date"
    ACTION = "Action"
    SYMBOL = "Symbol"
    DESCRIPTION = "Description"
    PRICE = "Price"
    QUANTITY = "Quantity"
    FEES_AND_COMM = "Fees & Comm"
    AMOUNT = "Amount"


class RequiredAwardColumn(StrEnum):
    """Column names for Awards transactions file."""

    DATE = "Date"
    SYMBOL = "Symbol"
    FAIR_MARKET_VALUE_PRICE = "FairMarketValuePrice"


@dataclass
class AwardPrices:
    """Class to store initial stock prices."""

    award_prices: dict[datetime.date, dict[str, Decimal]]

    lapse_priced_from: dict[str, datetime.date] = field(default_factory=dict)
    """Earliest date each symbol is priced at, for prices read from Lapse rows.

    Those prices are stated in one file and the share counts they cost in
    another, so a split on or after one of these dates has to be refused
    rather than guessed at. Empty for the award-price CSV, which is not read
    that way.
    """

    def __bool__(self) -> bool:
        """Return True if not empty."""
        return bool(self.award_prices)

    def get(self, date: datetime.date, symbol: str) -> tuple[datetime.date, Decimal]:
        """Get initial stock price at given date."""
        # Award dates may go back a few days, depending on
        # holidays or weekends, so we do a linear search
        # in the past to find the award price
        symbol = TICKER_RENAMES.get(symbol, symbol)
        for i in range(7):
            to_search = date - datetime.timedelta(days=i)

            if (
                to_search in self.award_prices
                and symbol in self.award_prices[to_search]
            ):
                return (to_search, self.award_prices[to_search][symbol])
        raise KeyError(f"Award price is not found for symbol {symbol} for date {date}")


def action_from_str(label: str, file: Path) -> ActionType:
    """Convert string label to ActionType."""
    if label == "Buy":
        return ActionType.BUY

    if label == "Cancel Buy":
        # Not a purchase: it is dropped along with the Buy it reverses, and
        # a cancellation with no matching Buy fails the run.
        return ActionType.CANCEL_BUY

    if label == "Sell":
        return ActionType.SELL

    if label == "Sell to Open":
        return ActionType.OPTION_GRANT

    if label == "Buy to Close":
        return ActionType.OPTION_CLOSE

    if label == "Expired":
        return ActionType.OPTION_EXPIRY

    if label == "Assigned":
        return ActionType.OPTION_ASSIGNMENT

    if label in {"Buy to Open", "Sell to Close", "Exercised"}:
        raise ParsingError(
            file,
            f"Schwab action '{label}' is a purchased-option transaction. "
            "Only written equity options (Sell to Open followed by Buy to "
            "Close, Expired or Assigned) are currently supported.",
        )

    if label in {
        "MoneyLink Transfer",
        "Misc Cash Entry",
        "Service Fee",
        "Wire Funds",
        "Wire Sent",
        "Funds Received",
        "Journal",
        "Cash In Lieu",
        "Visa Purchase",
        "MoneyLink Deposit",
        "MoneyLink Adj",  # likely a returned transfer
        "Security Transfer",
    }:
        return ActionType.TRANSFER

    if label == "Stock Plan Activity":
        return ActionType.STOCK_ACTIVITY

    if label in {
        "Qualified Dividend",
        "Cash Dividend",
        "Qual Div Reinvest",
        "Div Adjustment",
        "Special Qual Div",
        "Non-Qualified Div",
    }:
        return ActionType.DIVIDEND

    if label in {"NRA Tax Adj", "NRA Withholding", "Foreign Tax Paid"}:
        return ActionType.DIVIDEND_TAX

    if label == "ADR Mgmt Fee":
        return ActionType.FEE

    if label in {"Adjustment", "IRS Withhold Adj", "Wire Funds Adj"}:
        return ActionType.ADJUSTMENT

    if label in {"Short Term Cap Gain", "Long Term Cap Gain"}:
        return ActionType.CAPITAL_GAIN

    if label == "Spin-off":
        return ActionType.SPIN_OFF

    if label in {"Credit Interest", "Bond Interest"}:
        return ActionType.INTEREST

    if label == "Reinvest Shares":
        return ActionType.REINVEST_SHARES

    if label == "Reinvest Dividend":
        return ActionType.REINVEST_DIVIDENDS

    if label == "Wire Funds Received":
        return ActionType.WIRE_FUNDS_RECEIVED

    if label == "Stock Split":
        return ActionType.STOCK_SPLIT

    if label in {"Cash Merger", "Cash Merger Adj"}:
        return ActionType.CASH_MERGER

    if label in {"Full Redemption", "Full Redemption Adj"}:
        return ActionType.FULL_REDEMPTION

    raise ParsingError(file, f"Unknown action: '{label}'")


def _parse_decimal(
    row: OrderedDict[str, str],
    column: RequiredTransactionsColumn,
) -> Decimal | None:
    """Convert Schwab CSV column into Decimal, allowing optional blanks."""

    raw_value = row[column]
    if raw_value.strip(" $,") == "":
        return None

    # An option debit is written in accounting notation: (100.65) is -100.65.
    value = raw_value.strip()
    if value.startswith("(") and value.endswith(")"):
        value = f"-{value[1:-1]}"

    return parse_decimal(value, f"column '{column.value}'", strip="$,")


class SchwabTransaction(BrokerTransaction):
    """Represent single Schwab transaction."""

    def __init__(
        self,
        row_dict: OrderedDict[str, str],
        file: Path,
    ):
        """Create transaction from CSV row."""
        if len(row_dict) < NEW_COLUMNS_NUM or len(row_dict) > OLD_COLUMNS_NUM:
            # Old transactions had empty 9th column.
            raise UnexpectedColumnCountError(
                list(row_dict.values()), NEW_COLUMNS_NUM, file
            )
        if len(row_dict) == OLD_COLUMNS_NUM and list(row_dict.values())[-1] != "":
            raise ParsingError(file, f"Column {OLD_COLUMNS_NUM} should be empty")
        as_of_str = " as of "
        date_header = RequiredTransactionsColumn.DATE.value
        if as_of_str in row_dict[date_header]:
            index = row_dict[date_header].find(as_of_str)
            date_str = row_dict[date_header][:index]
        else:
            date_str = row_dict[date_header]
        try:
            date = datetime.datetime.strptime(date_str, "%m/%d/%Y").date()
        except ValueError as exc:
            raise ParsingError(
                file, f"Invalid date format: {date_str} from row: {row_dict}"
            ) from exc
        action_header = RequiredTransactionsColumn.ACTION.value
        self.raw_action = row_dict[action_header]
        action = action_from_str(self.raw_action, file)
        symbol_header = RequiredTransactionsColumn.SYMBOL.value
        symbol = row_dict[symbol_header] if row_dict[symbol_header] != "" else None
        option_contract = None
        if action in {
            ActionType.OPTION_GRANT,
            ActionType.OPTION_CLOSE,
            ActionType.OPTION_EXPIRY,
            ActionType.OPTION_ASSIGNMENT,
        }:
            if (
                symbol is None
                or (option_contract := parse_option_contract(symbol)) is None
            ):
                raise ParsingError(
                    file,
                    f"Cannot parse the option contract symbol {symbol!r} for "
                    f"Schwab action '{self.raw_action}'",
                )
            symbol = str(option_contract)
        elif symbol is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
        description = row_dict[RequiredTransactionsColumn.DESCRIPTION.value]
        if (
            action == ActionType.DIVIDEND_TAX
            and symbol is None
            and "SCHWAB1 INT" in description
        ):
            # Withholding on account-level cash interest, not tied to a security.
            action = ActionType.INTEREST_TAX
        price = _parse_decimal(row_dict, RequiredTransactionsColumn.PRICE)
        quantity = _parse_decimal(row_dict, RequiredTransactionsColumn.QUANTITY)
        fees = _parse_decimal(
            row_dict, RequiredTransactionsColumn.FEES_AND_COMM
        ) or Decimal(0)
        amount = _parse_decimal(row_dict, RequiredTransactionsColumn.AMOUNT)

        # Handle bonds/notes: CUSIP symbols have price per $100 face value
        price, fees = adjust_cusip_bond_price(symbol, price, quantity, amount, fees)

        currency = CurrencyCode("USD")
        broker = "Charles Schwab"
        super().__init__(
            date,
            action,
            symbol,
            description,
            quantity,
            price,
            fees,
            amount,
            currency,
            broker,
            option_contract=option_contract,
        )

    @staticmethod
    def create(
        row_dict: OrderedDict[str, str], file: Path, awards_prices: AwardPrices
    ) -> SchwabTransaction:
        """Create and post process a SchwabTransaction."""
        transaction = SchwabTransaction(row_dict, file)
        if (
            transaction.price is None
            and transaction.action == ActionType.STOCK_ACTIVITY
        ):
            symbol = transaction.symbol
            if symbol is None:
                raise SymbolMissingError(transaction)
            # The Schwab transaction list sometimes contains an incorrect date
            # for awards which doesn't match the PDF statements.
            # We want to make sure to match date and price from the awards
            # spreadsheet.
            try:
                _vest_date, transaction.price = awards_prices.get(
                    transaction.date, symbol
                )
            except KeyError as err:
                context = (
                    f"the {symbol} stock activity of {transaction.quantity} "
                    f"on {transaction.date} has no price of its own"
                )
                reason = (
                    "no Schwab Award file was provided"
                    if not awards_prices
                    else "the Schwab Award file has no award price for it"
                )
                raise ParsingError(
                    file,
                    f"Cannot price a vest: {context}, and {reason}. "
                    "Pass the Equity Awards transaction history with "
                    "--schwab-award-file.",
                ) from err
        return transaction


def _combine_cash_merger_pair(
    cash_merger: SchwabTransaction,
    cash_merger_adj: SchwabTransaction,
    transactions_file: Path,
) -> SchwabTransaction:
    """Combine Cash Merger + Cash Merger Adj into single transaction.

    Cash Merger: Has Amount (proceeds), No Quantity/Price
    Cash Merger Adj: Has Quantity (shares), No Amount

    Returns: Unified transaction with calculated price
    """
    if (
        cash_merger.description != cash_merger_adj.description
        or cash_merger.symbol != cash_merger_adj.symbol
        or cash_merger.date != cash_merger_adj.date
    ):
        raise ParsingError(
            transactions_file,
            f"Invalid Cash Merger format for {cash_merger.symbol} on "
            f"{cash_merger.date}: the Cash Merger and adjustment rows must "
            "have the same date, symbol and description",
        )

    amount = cash_merger.amount
    adjustment_quantity = cash_merger_adj.quantity
    if (
        amount is None
        or amount < 0
        or cash_merger.quantity is not None
        or cash_merger.price is not None
        or adjustment_quantity is None
        or adjustment_quantity >= 0
        or cash_merger_adj.amount is not None
        or cash_merger_adj.price is not None
    ):
        raise ParsingError(
            transactions_file,
            f"Invalid Cash Merger format for {cash_merger.symbol} on "
            f"{cash_merger.date}: expected non-negative proceeds only on the "
            "Cash Merger row and a negative share quantity only on the "
            "adjustment row",
        )

    # Create unified transaction
    unified = cash_merger
    # Quantity is negative (shares leaving), convert to positive for SELL
    unified.quantity = -adjustment_quantity
    unified.price = amount / unified.quantity
    unified.fees += cash_merger_adj.fees

    return unified


def _combine_full_redemption_pair(
    full_redemption_adj: SchwabTransaction,
    full_redemption: SchwabTransaction,
    transactions_file: Path,
) -> SchwabTransaction:
    """Combine Full Redemption Adj + Full Redemption into single transaction.

    Actual Schwab CSV format:
    Full Redemption Adj: Has Amount (proceeds), No Price/Quantity
    Full Redemption: Has Quantity (shares), No Price/Amount

    Returns: Unified transaction with calculated price
    """
    if (
        full_redemption_adj.description != full_redemption.description
        or full_redemption_adj.symbol != full_redemption.symbol
        or full_redemption_adj.date != full_redemption.date
    ):
        raise ParsingError(
            transactions_file,
            f"Invalid Full Redemption format for {full_redemption.symbol} on "
            f"{full_redemption.date}: the adjustment and redemption rows must "
            "have the same date, symbol and description",
        )

    amount = full_redemption_adj.amount
    redemption_quantity = full_redemption.quantity
    if (
        amount is None
        or amount < 0
        or full_redemption_adj.quantity is not None
        or full_redemption_adj.price is not None
        or redemption_quantity is None
        or redemption_quantity >= 0
        or full_redemption.price is not None
        or full_redemption.amount is not None
    ):
        raise ParsingError(
            transactions_file,
            f"Invalid Full Redemption format for {full_redemption.symbol} on "
            f"{full_redemption.date}: expected non-negative proceeds only on "
            "the adjustment row and a negative share quantity only on the "
            "redemption row",
        )

    # Create unified transaction (use Full Redemption as base for action type)
    unified = full_redemption
    # Quantity is negative (shares leaving), convert to positive
    unified.quantity = -redemption_quantity
    unified.amount = amount
    unified.price = amount / unified.quantity
    unified.fees += full_redemption_adj.fees

    return unified


def _unify_schwab_paired_transactions(
    transactions: list[SchwabTransaction],
    transactions_file: Path,
) -> list[SchwabTransaction]:
    """Unify paired transactions (Cash Merger and Full Redemption).

    Both follow a similar pattern where transactions are split into two rows:
    1. One row has the amount (proceeds)
    2. Other row has the quantity (shares)
    We combine them to calculate the price per share.

    Cash Merger pattern:
        Row 1: "Cash Merger" - Has Amount ($1000), No Quantity/Price
        Row 2: "Cash Merger Adj" - Has Quantity (-100 shares), No Amount
        Result: Sell 100 shares at $10/share

    Full Redemption pattern:
        Row 1: "Full Redemption Adj" - Has Quantity (-100 shares), No Amount
        Row 2: "Full Redemption" - Has Amount ($1000), No Quantity/Price
        Result: Sell 100 shares at $10/share
    """
    filtered: list[SchwabTransaction] = []
    i = 0
    while i < len(transactions):
        transaction = transactions[i]

        if transaction.raw_action == "Cash Merger Adj":
            # Cash Merger Adj comes AFTER Cash Merger
            if len(filtered) == 0:
                raise ParsingError(
                    transactions_file,
                    "Cash Merger Adj must be preceded by a Cash Merger transaction",
                )
            main_transaction = filtered[-1]
            adj_transaction = transaction

            if main_transaction.raw_action != "Cash Merger":
                raise ParsingError(
                    transactions_file,
                    "Cash Merger Adj must immediately follow a Cash Merger transaction",
                )

            unified = _combine_cash_merger_pair(
                main_transaction, adj_transaction, transactions_file
            )

            # Replace the previous Cash Merger with unified transaction
            filtered[-1] = unified
            LOGGER.warning(
                "Cash Merger support is not complete and doesn't cover the "
                "cases when shares are received aside from cash, "
                "please review this transaction carefully:\n"
                "    %s: %s units on %s for %s %s (%s)",
                unified.symbol,
                unified.quantity,
                unified.date,
                unified.amount,
                unified.currency,
                unified.broker,
            )

        elif transaction.raw_action == "Full Redemption Adj":
            # Full Redemption Adj comes BEFORE Full Redemption
            if i + 1 >= len(transactions):
                raise ParsingError(
                    transactions_file,
                    "Full Redemption Adj must be followed by a Full Redemption "
                    "transaction",
                )
            adj_transaction = transaction
            main_transaction = transactions[i + 1]

            if main_transaction.raw_action != "Full Redemption":
                raise ParsingError(
                    transactions_file,
                    "Full Redemption Adj must be followed by a Full Redemption "
                    "transaction",
                )

            unified = _combine_full_redemption_pair(
                adj_transaction, main_transaction, transactions_file
            )

            # Add unified transaction and skip next (Full Redemption)
            filtered.append(unified)
            i += 1  # Skip the Full Redemption transaction
            LOGGER.warning(
                "Full Redemption combined with adjustment: %s",
                unified,
            )

        else:
            filtered.append(transaction)

        i += 1

    return filtered


def _is_matching_buy(buy_txn: SchwabTransaction, cancel_txn: SchwabTransaction) -> bool:
    """Whether buy_txn is the purchase that cancel_txn reverses.

    The raw action is compared rather than the action type, so a cancellation
    can never pair with another cancellation.
    """
    return (
        buy_txn.raw_action == "Buy"
        and buy_txn.symbol == cancel_txn.symbol
        and buy_txn.quantity == cancel_txn.quantity
        and buy_txn.price == cancel_txn.price
    )


def _find_matching_buy(
    transactions: list[SchwabTransaction],
    cancel_idx: int,
    consumed: set[int],
) -> int | None:
    """Find the index of the Buy that the Cancel Buy at cancel_idx reverses.

    The list is newest-first, so the purchase being reversed normally sits at
    a higher index than the cancellation. When the two share a date the export
    orders them arbitrarily, so the purchase can equally sit just above it;
    those rows are searched as well. A purchase on a date later than the
    cancellation is never a match, because it had not happened yet when the
    cancellation was recorded.

    Args:
        transactions: Parsed Schwab transactions, newest-first
        cancel_idx: Index of the Cancel Buy to reconcile
        consumed: Indices already claimed by an earlier pair

    Returns:
        Index of the matching Buy, or None when there is none

    """
    cancel_txn = transactions[cancel_idx]

    # Older rows, out to the edge of the search window.
    for buy_idx in range(cancel_idx + 1, len(transactions)):
        buy_txn = transactions[buy_idx]
        if abs((buy_txn.date - cancel_txn.date).days) > CANCEL_BUY_SEARCH_DAYS:
            break
        if buy_idx not in consumed and _is_matching_buy(buy_txn, cancel_txn):
            return buy_idx

    # Newer rows, but only the ones sharing the cancellation's own date.
    for buy_idx in range(cancel_idx - 1, -1, -1):
        buy_txn = transactions[buy_idx]
        if buy_txn.date != cancel_txn.date:
            break
        if buy_idx not in consumed and _is_matching_buy(buy_txn, cancel_txn):
            return buy_idx

    return None


def _filter_cancelled_buy_transactions(
    transactions: list[SchwabTransaction],
    file: Path,
) -> list[SchwabTransaction]:
    """Filter out Cancel Buy transactions and their matching Buy transactions.

    Schwab reports both the original Buy and a "Cancel Buy" transaction when a
    purchase is cancelled. Both need to be removed to avoid incorrect capital
    gains calculations.

    This is a Schwab-specific quirk - other brokers may not report cancellations
    at all or may handle them differently.

    Note: this runs on the raw, newest-first ordered list of transactions as
    read from the Schwab export (before ``transactions.reverse()`` is applied
    by the caller). A Cancel Buy row is chronologically *after* the Buy it
    cancels, so in a newest-first list the Cancel Buy normally has a *lower*
    index than its Buy. Same-date rows are the exception, since the export
    orders those arbitrarily; see ``_find_matching_buy`` for how both are
    searched.

    Args:
        transactions: List of parsed Schwab transactions, newest-first
        file: Source file, for error reporting

    Returns:
        Filtered list with Cancel Buy pairs removed

    Raises:
        ParsingError: If a Cancel Buy has no matching Buy

    """
    indices_to_remove: set[int] = set()

    # Find all Cancel Buy transactions
    for cancel_idx, transaction in enumerate(transactions):
        if transaction.raw_action != "Cancel Buy":
            continue

        # Already marked for removal
        if cancel_idx in indices_to_remove:
            continue

        buy_idx = _find_matching_buy(transactions, cancel_idx, indices_to_remove)

        if buy_idx is not None:
            # Found matching pair - mark both for removal
            indices_to_remove.add(cancel_idx)
            indices_to_remove.add(buy_idx)
            buy_txn = transactions[buy_idx]
            LOGGER.debug(
                "Matched Cancel Buy with original Buy: symbol=%s, qty=%s, "
                "price=%s, buy_date=%s, cancel_date=%s",
                buy_txn.symbol,
                buy_txn.quantity,
                buy_txn.price,
                buy_txn.date,
                transaction.date,
            )
        else:
            # A cancellation with nothing to reverse means the export does not
            # reach back to the purchase, or that purchase settled outside the
            # window above. Either way the pair cannot be reconciled and the
            # row cannot be processed, so refuse here, where the row's meaning
            # is still known. Left in place it would reach the calculator,
            # which refuses any action it does not handle but can only report
            # an unprocessed action type, naming neither the cancellation nor
            # what to do about it.
            # A directory load passes the directory, so name the file and line
            # the row records: "remove this row" needs to say which row.
            source = transaction.source
            raise ParsingError(
                source.file if source and source.file else file,
                f"Found a {transaction.raw_action} for {transaction.symbol} on "
                f"{transaction.date} with no Buy to match it in the "
                f"{CANCEL_BUY_SEARCH_DAYS} days before. The purchase may be "
                "missing from the export, older than that window, recorded "
                "with a different quantity or price, or already reversed by "
                "another cancellation. Remove this row; remove the purchase "
                "with it only if the purchase is still in the export and no "
                "other cancellation reverses it.",
                row_index=source.row if source else None,
            )

    if indices_to_remove:
        LOGGER.info(
            "Removed %d cancelled transaction(s) and their originals",
            len(indices_to_remove),
        )

    return [txn for i, txn in enumerate(transactions) if i not in indices_to_remove]


@dataclass(frozen=True)
class _Export:
    """One Schwab CSV from a directory, with the dates it actually covers."""

    rows: list[SchwabTransaction]
    file_path: Path

    @property
    def oldest(self) -> datetime.date:
        """Earliest transaction date in the file."""
        return min(row.date for row in self.rows)

    @property
    def newest(self) -> datetime.date:
        """Latest transaction date in the file."""
        return max(row.date for row in self.rows)


def _reject_overlapping_exports(exports: list[_Export]) -> None:
    """Refuse two exports whose transaction dates overlap.

    A Schwab CSV carries no transaction id, so a row repeated by an overlap is
    indistinguishable from a genuine repeat of the same trade: two identical
    buys of the same size and price on one day are a real thing. Continuing
    would report a disposal or acquisition that never happened, and would leave
    the order of a shared date decided by the filename.

    The comparison is over the dates present in each file rather than the range
    the user asked Schwab for. That is the stricter reading of what matters: if
    two requested ranges overlap but no transaction falls in the shared window,
    nothing is duplicated and there is nothing to refuse.
    """
    ordered = sorted(exports, key=lambda export: (export.oldest, export.newest))
    for earlier, later in itertools.pairwise(ordered):
        if later.oldest <= earlier.newest:
            raise ParsingError(
                later.file_path,
                f"has transactions from {later.oldest} to {later.newest}, "
                f"overlapping {earlier.file_path.name} ({earlier.oldest} to "
                f"{earlier.newest}). A Schwab CSV has no transaction id, so "
                "cgt-calc cannot tell a row repeated by the overlap from a "
                "genuine repeat of the same trade, and would count both. "
                "Re-export the ranges so they do not overlap, or remove the "
                "redundant file. If these are exports from two different "
                "Schwab accounts, cgt-calc cannot combine them: the CSV does "
                "not say which account a row belongs to.",
            )


def _read_schwab_awards(
    schwab_award_transactions_file: Path | None,
) -> AwardPrices:
    """Read initial stock prices from CSV file."""
    if schwab_award_transactions_file is None:
        return AwardPrices(award_prices={})

    with schwab_award_transactions_file.open(encoding="utf-8") as csv_file:
        parsing_msg(schwab_award_transactions_file)
        lines = list(csv.reader(csv_file))
    return _award_prices_from_lines(lines, schwab_award_transactions_file)


def _award_prices_from_lines(
    lines: list[list[str]],
    schwab_award_transactions_file: Path,
) -> AwardPrices:
    """Read initial stock prices from the rows of an award-price CSV.

    The guards below stay with the rows they protect rather than with the
    reading: ``_read_schwab_awards`` is called directly with a path as well as
    through the CLI, and an empty file or a wrong header has to name itself
    either way instead of surfacing as IndexError or KeyError.
    """
    initial_prices: dict[datetime.date, dict[str, Decimal]] = defaultdict(dict)

    if not lines:
        raise ParsingError(
            schwab_award_transactions_file, "Charles Schwab Award CSV file is empty"
        )
    header = lines[0]
    required_columns = {column.value for column in RequiredAwardColumn}
    if not required_columns.issubset(header):
        raise ParsingError(
            schwab_award_transactions_file,
            "Missing columns in Schwab Award file: "
            f"{required_columns.difference(header)}",
            row_index=1,
        )

    # Remove header
    lines = lines[1:]

    modulo = len(lines) % 2
    if modulo != 0:
        raise UnexpectedRowCountError(
            len(lines) - modulo + 2, schwab_award_transactions_file
        )

    for offset in range(0, len(lines), 2):
        upper_row = lines[offset]
        lower_row = lines[offset + 1]
        row_index = offset + 2

        if len(upper_row) != len(header):
            raise UnexpectedColumnCountError(
                upper_row,
                len(header),
                schwab_award_transactions_file,
                row_index=row_index,
            )
        # Some exports append an extra empty column to the lower row.
        if len(lower_row) == len(header) + 1 and lower_row[-1] == "":
            lower_row.pop()
        if len(lower_row) != len(header):
            raise UnexpectedColumnCountError(
                lower_row,
                len(header),
                schwab_award_transactions_file,
                row_index=row_index + 1,
            )

        # in this format each logical row is split into two rows,
        # so we combine them safely below
        row = []
        for column_index, (upper_col, lower_col) in enumerate(
            zip(upper_row, lower_row, strict=True), start=1
        ):
            if upper_col and lower_col:
                raise ParsingError(
                    schwab_award_transactions_file,
                    f"Both halves of the split award row (rows {row_index} and "
                    f"{row_index + 1}) contain data in column {column_index}",
                    row_index=row_index,
                )
            row.append(upper_col + lower_col)

        row_dict = OrderedDict(zip(header, row, strict=True))
        date_str = row_dict[RequiredAwardColumn.DATE.value]
        try:
            date = datetime.datetime.strptime(date_str, "%Y/%m/%d").date()
        except ValueError:
            date = datetime.datetime.strptime(date_str, "%m/%d/%Y").date()
        symbol_header = RequiredAwardColumn.SYMBOL.value
        symbol = row_dict[symbol_header] if row_dict[symbol_header] != "" else None
        fair_market_value_price_header = (
            RequiredAwardColumn.FAIR_MARKET_VALUE_PRICE.value
        )
        price_str = row_dict[fair_market_value_price_header]
        try:
            price = (
                parse_decimal(
                    price_str, f"column '{fair_market_value_price_header}'", strip="$,"
                )
                if price_str != ""
                else None
            )
        except ValueError as err:
            # This file is read outside StandardCSVParser, so nothing else
            # turns the helper's ValueError into an error naming the row.
            # The format splits each award over two rows and states the price
            # on the lower one, which is the row the reader has to correct.
            raise ParsingError(
                schwab_award_transactions_file, str(err), row_index=row_index + 1
            ) from err
        if symbol is not None and price is not None:
            symbol = TICKER_RENAMES.get(symbol, symbol)
            initial_prices[date][symbol] = price
    return AwardPrices(award_prices=dict(initial_prices))


def _reject_split_of_a_lapse_priced_symbol(
    transactions: list[BrokerTransaction], awards_prices: AwardPrices
) -> None:
    """Refuse a main history that splits a symbol priced from Lapse rows.

    The award export states the price and the main history states the number
    of shares. A split restates the records before it, and neither file says
    whether it restated both, so a cost out by the split factor would pass
    every check the calculator makes. Checked over the whole history rather
    than per row because the split can be exported after the vest it affects.

    A split strictly before every price the award file states for the symbol
    is not a problem, and is the same boundary the split table gets: there is
    no earlier award record for it to have restated. Someone who held the
    share before joining the plan is the ordinary case.

    Only the JSON path fills in `lapse_priced_from`. The award-price CSV is
    untouched: it has always been read this way, and narrowing it now would
    reject histories that work today.
    """
    for transaction in transactions:
        if transaction.action is not ActionType.STOCK_SPLIT or not transaction.symbol:
            continue
        symbol = TICKER_RENAMES.get(transaction.symbol, transaction.symbol)
        first_priced = awards_prices.lapse_priced_from.get(symbol)
        if first_priced is None or transaction.date < first_priced:
            continue
        message = (
            f"This history records a {symbol} stock split on "
            f"{transaction.date}, and the Equity Awards export prices a "
            f"{symbol} vest on {first_priced}, on or before it. A split "
            "restates the share counts before it, and neither file says "
            "whether the price and the count were restated together, so the "
            "acquisition cost could be out by the split factor with nothing "
            "to show for it. " + COMPLETE_EXPORT_CAVEAT
        )
        source = transaction.source
        if source is None or source.file is None:
            raise CgtError(message)
        raise ParsingError(source.file, message, row_index=source.row)


def _is_complete_award_export(content: str, file_path: Path) -> bool:
    """Say which Equity Awards export the award input holds.

    The contents decide, not the filename: users rename these exports, and
    ``--schwab-award-file`` takes either kind. The header alone decides, so a
    file holding nothing but a header is still routed to the reader that owns
    its layout and reports the shortage in that layout's own terms.
    """
    if content.startswith("{"):
        return True
    header = next(csv.reader(io.StringIO(content)), [])
    # The complete layout is tested first because a real one also carries
    # FairMarketValuePrice; only the last two columns are unique to it.
    if COMPLETE_CSV_COLUMNS.issubset(header):
        return True
    if {column.value for column in RequiredAwardColumn}.issubset(header):
        return False
    raise ParsingError(
        file_path,
        "This is not a Schwab Equity Awards export cgt-calc reads. The "
        "award-price CSV holds Date, Symbol and FairMarketValuePrice; the "
        "complete CSV holds VestFairMarketValue and PurchaseFairMarketValue; "
        "the JSON export starts with '{'.",
        row_index=1,
    )


class SchwabParser(BaseSingleFileParser[BrokerTransaction]):
    """Parser for Charles Schwab transaction files."""

    arg_name = "schwab"
    pretty_name = "Charles Schwab"
    format_name = "CSV"
    deprecated_flags: ClassVar[list[str]] = ["--schwab"]
    # Schwab cannot inherit BaseDirParser, which would rename --schwab-file to
    # --schwab-dir, so it declares the same directory conventions by hand and
    # reads them through cls in load_from_dir.
    glob_dir: ClassVar[str] = "*.csv"

    awards_prices: AwardPrices = _read_schwab_awards(None)

    @classmethod
    @override
    def register_arguments(cls, arg_group: argparse._ArgumentGroup) -> None:
        """Register argparse arguments for this broker."""
        set_completer(
            arg_group.add_argument(
                "--schwab-award-file",
                type=existing_file_or_stdin_type,
                default=None,
                metavar="PATH",
                help="Charles Schwab Equity Awards export: the price-only "
                "export that prices vests in the main history, as the "
                "award-price CSV or as JSON holding only Lapse rows, or the "
                "complete transaction history as JSON or CSV",
            ),
            shtab.FILE,
        )
        arg_group.add_argument(
            "--schwab-award",
            action=DeprecatedAction,
            dest="schwab_award_file",
            type=existing_file_or_stdin_type,
            help=argparse.SUPPRESS,
        )
        # Schwab keeps both forms: `--schwab-file` predates the directory
        # support, so removing it would break every existing command. The
        # other directory brokers replaced their file flag instead.
        set_completer(
            arg_group.add_argument(
                "--schwab-dir",
                type=existing_directory_type,
                default=None,
                metavar="DIR",
                help="directory with Charles Schwab transaction history in CSV format",
            ),
            shtab.DIRECTORY,
        )
        super().register_arguments(arg_group)

    @classmethod
    @override
    def load_from_args(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load broker data from parsed arguments."""
        if args.schwab_dir and args.schwab_file:
            # main() rejects this through argparse, for the usage message and
            # exit code 2. Repeated here because this is the layer that knows
            # both flags: taking the directory branch silently would drop
            # every transaction in the file. Checked before any I/O so a bad
            # award file can't mask the real problem.
            raise CgtError(
                "--schwab-file and --schwab-dir cannot be used together. Pass "
                "the directory holding every export, or the single file."
            )
        award_transactions = cls._load_award_file(args)
        if args.schwab_dir:
            # list is invariant, so widen explicitly rather than return list[T].
            transactions: list[BrokerTransaction] = list(
                cls.load_from_dir(args.schwab_dir)
            )
            return transactions
        # A complete award export cannot be combined with a main history, so
        # at most one of these two holds anything. The registry then reports
        # them under this parser's name, so a canonical complete import says
        # "Charles Schwab" where the old option said "Charles Schwab Equity
        # Awards". Only that progress line: each transaction still records the
        # parser that read it.
        return award_transactions + super().load_from_args(args)

    @classmethod
    def _load_award_file(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Read --schwab-award-file and route it by what it holds.

        An award-price CSV supplies `awards_prices` and imports nothing, and
        so does the JSON export whose every row is a Lapse; a complete export
        imports its own history and prices no vests. Either way
        `awards_prices` is assigned, because it is class-level state and
        leaving it would price this run's vests from the last run's file.
        """
        cls.awards_prices = AwardPrices(award_prices={})
        award_path = args.schwab_award_file
        if award_path is None:
            return []
        # Read once and classify the text: stdin cannot be reopened, and the
        # reader that owns the layout is handed this same content.
        parsing_msg("stdin" if award_path == STDIN_PATH else award_path)
        content = read_input(award_path, cls.encoding)
        # The same leading noise a complete export already tolerates. It is
        # applied before classification, so a price CSV led by a byte-order
        # mark or a blank line is now read too.
        content = content.lstrip("\ufeff \t\r\n")
        if not _is_complete_award_export(content, award_path):
            cls.awards_prices = _award_prices_from_lines(
                list(csv.reader(io.StringIO(content))), award_path
            )
            return []
        # Before the checks below, and for the same reason the price CSV is
        # exempt from them: an export whose every row is a Lapse states vest
        # prices and no transactions, so it can neither duplicate a history
        # nor need reconciling with one.
        #
        # Classified, then checked against the other inputs, and only then
        # read. A file passed without the main history it exists to price has
        # to be answered with the input it is missing, not with the first of
        # its rows cgt-calc declines to use.
        price_only = lapse_only_rows(content, award_path)
        if price_only is not None:
            if not (args.schwab_file or args.schwab_dir):
                raise CgtError(price_only_alone_message(canonical_option=True))
            rows, names = price_only
            lapse_prices = lapse_award_prices(rows, award_path, names)
            first_priced: dict[str, datetime.date] = {}
            for priced_date, prices in sorted(lapse_prices.items()):
                for symbol in prices:
                    first_priced.setdefault(symbol, priced_date)
            cls.awards_prices = AwardPrices(
                award_prices=lapse_prices, lapse_priced_from=first_priced
            )
            return []
        if args.schwab_equity_award_json:
            raise CgtError(
                "A complete Equity Awards export was given to both "
                "--schwab-award-file and --schwab-equity-award-json. Accepting "
                "both could duplicate transactions: cgt-calc cannot tell two "
                "exports of one history from two exports of different periods. "
                "Pass the history once, through --schwab-award-file."
            )
        if args.schwab_file or args.schwab_dir:
            raise CgtError(
                "--schwab-award-file holds a complete Equity Awards history, "
                "and combining one with a main history is not supported yet. "
                "Pass the complete export on its own, or pass it with "
                "--schwab-equity-award-json and make sure no transaction "
                "appears in both files. That option only adds transactions, it "
                "does not price vests, so the main history has to import on "
                "its own: if it holds a vest cgt-calc cannot price, pass the "
                "award-price CSV with --schwab-award-file as well."
            )
        return list(
            SchwabEquityAwardsParser.load_from_stream(
                io.StringIO(content), award_path, show_parsing_msg=False
            )
        )

    @classmethod
    @override
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[BrokerTransaction]:
        """Read Schwab transactions from file."""
        transactions = cls._read_rows(file, file_path)
        transactions = _unify_schwab_paired_transactions(transactions, file_path)
        transactions = _filter_cancelled_buy_transactions(transactions, file_path)
        transactions.reverse()
        return list(transactions)

    @classmethod
    @override
    def finalize_transactions(
        cls, transactions: list[BrokerTransaction]
    ) -> list[BrokerTransaction]:
        """Check and reconcile what only the whole account can answer.

        An opening option row and its outcome can sit in different files of
        one `--schwab-dir` export, so that pairing cannot run per file. A
        split can likewise be exported after the vest whose units it puts in
        doubt. The boundary hook runs exactly once, after every file has been
        read and stamped.
        """
        _reject_split_of_a_lapse_priced_symbol(transactions, cls.awards_prices)
        return reconcile_written_options(
            [row for row in transactions if isinstance(row, SchwabTransaction)]
        )

    @classmethod
    def load_from_dir(cls, dir_path: Path) -> list[BrokerTransaction]:
        """Read every CSV in the directory as one export.

        Schwab limits how much history one export can cover, so a long history
        arrives as several date-range files. They are parsed separately and
        then treated as a single export.

        Corporate-action pairing stays per file: both shapes it recognises are
        same-date by construction, and refusing overlapping exports first
        guarantees one date lives in exactly one file, so a pair can never be
        split. Cancellation matching is the opposite: the Buy a Cancel Buy
        reverses is up to CANCEL_BUY_SEARCH_DAYS away, so it routinely sits in
        the neighbouring file and can only be found once everything is merged.
        """
        # One directory is one declared account boundary, as it is for the
        # other directory brokers, so every file in it shares a token.
        account = next_account_token(cls.pretty_name)
        exports: list[_Export] = []
        for file_path in sorted(dir_path.glob(cls.glob_dir, case_sensitive=False)):
            if not cls.file_path_filter(file_path):
                continue
            try:
                with file_path.open(encoding=cls.encoding) as file:
                    parsing_msg(file_path)
                    rows = cls._read_rows(file, file_path, from_directory=True)
            except OSError as exc:
                # Branch on what the path is, not on the exception type:
                # opening a directory raises IsADirectoryError on POSIX but
                # PermissionError on Windows.
                if file_path.is_dir():
                    raise ParsingError(
                        file_path,
                        "is not a regular file. Remove or move it out of the "
                        "Schwab directory.",
                    ) from exc
                raise ParsingError(
                    file_path,
                    f"could not be read ({exc.strerror or exc}). Remove or move "
                    "it out of the Schwab directory.",
                ) from exc
            if rows:
                exports.append(_Export(rows=rows, file_path=file_path))

        # Before pairing, not after: an overlap is exactly what can put the two
        # halves of a corporate action in different files, and pairing would
        # then report a missing half rather than the overlap behind it. Pairing
        # only ever merges rows sharing a date, so it cannot change the span a
        # file covers and the answer here is the same either way.
        _reject_overlapping_exports(exports)

        exports = [
            replace(
                export,
                rows=_unify_schwab_paired_transactions(export.rows, export.file_path),
            )
            for export in exports
        ]
        for export in exports:
            # Rows are still newest-first here, so stamp a reversed view: index
            # has to rise with time, as it does on the single-file path where
            # stamping happens after the reverse.
            cls.stamp_source(list(reversed(export.rows)), export.file_path, account)

        # Every export is newest-first and no date spans two of them, so
        # ordering whole exports by their newest date makes the concatenation
        # newest-first without disturbing row order inside a file. Sorting the
        # rows themselves would have to break ties between files, and the only
        # tiebreak available is the filename.
        exports.sort(key=lambda export: export.newest, reverse=True)

        transactions = [row for export in exports for row in export.rows]
        transactions = _filter_cancelled_buy_transactions(transactions, dir_path)
        transactions.reverse()

        # Checked after filtering, not before: a directory whose only rows are
        # a Buy and the Cancel Buy that reverses it ends up empty, and has to
        # warn like any other empty result.
        if not transactions:
            LOGGER.warning(
                "No transactions detected in directory %s for broker %s",
                dir_path,
                cls.pretty_name,
            )
        # A directory is one account boundary and does not go through
        # load_from_file, so it finalizes here instead.
        parsed_transactions: list[BrokerTransaction] = list(transactions)
        return cls.finalize_transactions(
            cls.post_process_transactions(parsed_transactions)
        )

    @classmethod
    def _directory_help(cls, file_path: Path) -> str:
        return (
            f" Every {cls.glob_dir} in the directory is parsed as Schwab "
            f"transaction history; remove or move this file ({file_path.name}) "
            "out of the directory."
        )

    @classmethod
    def _read_rows(
        cls, file: TextIO, file_path: Path, *, from_directory: bool = False
    ) -> list[SchwabTransaction]:
        """Parse the rows of one Schwab CSV, without any cross-row pass.

        A ``classmethod`` because pricing a vest needs ``cls.awards_prices``,
        which ``load_from_args`` fills in from ``--schwab-award-file``.
        """
        lines = list(csv.reader(file))
        if not lines:
            message = "Charles Schwab transactions CSV file is empty."
            if from_directory:
                message += cls._directory_help(file_path)
            raise ParsingError(file_path, message)
        header = lines[0]

        required_headers = {column.value for column in RequiredTransactionsColumn}
        if not required_headers.issubset(header):
            directory_help = ""
            if from_directory:
                directory_help = cls._directory_help(file_path)
                award_headers = {column.value for column in RequiredAwardColumn}
                if award_headers.issubset(header):
                    directory_help += " Pass it with --schwab-award-file instead."
            raise ParsingError(
                file_path,
                "Missing columns in Schwab transaction file: "
                f"{required_headers.difference(header)}.{directory_help}",
                row_index=1,
            )

        transactions: list[SchwabTransaction] = []
        for index, row in enumerate(lines[1:], start=2):
            if not any(row):
                continue

            if len(row) != len(header):
                raise UnexpectedColumnCountError(
                    row, len(header), file_path, row_index=index
                )

            try:
                transaction = SchwabTransaction.create(
                    OrderedDict(zip(header, row, strict=True)),
                    file_path,
                    cls.awards_prices,
                )
            except ParsingError as err:
                err.add_row_context(index)
                raise
            except ValueError as err:
                raise ParsingError(file_path, str(err), row_index=index) from err

            transaction.source = TransactionSource(row=index)
            transactions.append(transaction)
        return transactions
