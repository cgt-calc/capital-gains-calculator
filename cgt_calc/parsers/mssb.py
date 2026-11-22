"""Parse the Morgan Stanley at Work report format used for Alphabet awards.

The importer has only been validated against an Alphabet ``GSU Class C``
export. It is not a general parser for other employers or Morgan Stanley
brokerage accounts.
"""

from __future__ import annotations

import contextlib
import csv
from dataclasses import dataclass
import datetime as dt
from decimal import Decimal
from enum import StrEnum
import io
from typing import TYPE_CHECKING, ClassVar, Final, TextIO, override

from cgt_calc.exceptions import CgtError, ParsingError
from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode
from cgt_calc.util import parse_date, parse_decimal

from .base_parsers import BaseDirParser, StandardCSVParser, missing_columns

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

BROKER_NAME: Final = "Morgan Stanley"
WITHDRAWALS_REPORT_FILENAME: Final = "Withdrawals Report.csv"
RELEASES_REPORT_FILENAME: Final = "Releases Report.csv"


class ReleaseColumn(StrEnum):
    """Column names for release reports."""

    VEST_DATE = "Vest Date"
    ORDER_NUMBER = "Order Number"
    PLAN = "Plan"
    TYPE = "Type"
    STATUS = "Status"
    PRICE = "Price"
    QUANTITY = "Quantity"
    NET_CASH_PROCEEDS = "Net Cash Proceeds"
    NET_SHARE_PROCEEDS = "Net Share Proceeds"
    TAX_PAYMENT_METHOD = "Tax Payment Method"


class WithdrawalColumn(StrEnum):
    """Column names for withdrawal reports."""

    EXECUTION_DATE = "Execution Date"
    ORDER_NUMBER = "Order Number"
    PLAN = "Plan"
    TYPE = "Type"
    ORDER_STATUS = "Order Status"
    PRICE = "Price"
    QUANTITY = "Quantity"
    NET_AMOUNT = "Net Amount"
    NET_SHARE_PROCEEDS = "Net Share Proceeds"
    TAX_PAYMENT_METHOD = "Tax Payment Method"


COLUMNS_RELEASE: Final[list[str]] = [column.value for column in ReleaseColumn]
COLUMNS_WITHDRAWAL: Final[list[str]] = [column.value for column in WithdrawalColumn]

RELEASES_REPORT: Final = "a Morgan Stanley Releases Report"
WITHDRAWALS_REPORT: Final = "a Morgan Stanley Withdrawals Report"
UNREAD_REPORT: Final = "a Morgan Stanley report cgt-calc does not read"
# The headings of the two other reports in Morgan Stanley's download, the
# Releases Net Shares Report and the Withdrawal Wire Report. `--mssb-dir`
# skips them by name; a plain INPUT is skipped by these exact headings.
UNREAD_HEADERS: Final = (
    [
        "Date",
        "Order Number",
        "Plan",
        "Type",
        "Order Status",
        "Price",
        "Quantity",
        "Net Share Proceeds",
        "Net Share Proceeds",
        "Tax Payment Method",
    ],
    [
        "Date",
        "Order Number",
        "Symbol",
        "Type",
        "Order Status",
        "Price",
        "Quantity",
        "Net Cash Proceeds",
        "Net Share Proceeds",
        "Tax Payment Method",
    ],
)

# These can be potentially wired through as a flag
KNOWN_SYMBOL_DICT: Final[dict[str, str]] = {
    "GSU Class C": "GOOG",
    "Cash": "USD",
}


@dataclass
class StockSplit:
    """A split whose earlier sales the withdrawal report leaves in old units."""

    symbol: str
    date: dt.date
    """Last day on which the report prints sales in pre-split units.

    Morgan Stanley's notice puts sales "on or prior to" the split date in
    pre-split values, so this is the split date itself rather than the first
    date treated as post-split by the Schwab parser's ``SPLITS`` table.
    """
    factor: int


STOCK_SPLIT_INFO = [
    StockSplit(symbol="GOOG", date=dt.datetime(2022, 7, 15).date(), factor=20),
]


def _parse_decimal(row: dict[str, str], column: StrEnum) -> Decimal:
    """Parse a decimal from the given row, annotating errors with column context."""

    return parse_decimal(row[column], f"column '{column.value}'", strip=",$")


def _parse_date(row: dict[str, str], column: StrEnum) -> dt.date:
    """Parse a date from the given row, naming the column if it cannot be read."""
    return parse_date(
        row[column],
        column.value,
        "%d-%b-%Y",
        expected="DD-Mon-YYYY",
        example="25-Mar-2021",
    )


class MSSBParser(
    StandardCSVParser[BrokerTransaction], BaseDirParser[BrokerTransaction]
):
    """Morgan Stanley parser."""

    arg_name = "mssb"
    pretty_name = BROKER_NAME
    format_name = "CSV"
    glob_dir = "*.csv"
    deprecated_flags: ClassVar[list[str]] = ["--mssb"]
    header_mismatch_needs_update: ClassVar[bool] = True
    # Morgan Stanley loads as it did before the check for a transaction in
    # two files was added: that change covered Hargreaves Lansdown and
    # Sharesight only. The two reports are read by fixed name and never hold
    # the same transaction. Only two names that differ in nothing but their
    # case, on a file system that tells them apart, are read as two copies
    # of one report, and counted twice.
    refuses_transaction_in_two_files: ClassVar[bool] = False
    skipped_kinds: ClassVar[frozenset[str]] = frozenset({UNREAD_REPORT})

    @classmethod
    @override
    def recognise(cls, text: str, file_path: Path) -> list[str]:
        """Name the report by its headings, as the reader checks them."""
        return cls._kinds_with(next(csv.reader(io.StringIO(text)), []))

    @classmethod
    def _kinds_with(cls, header: list[str]) -> list[str]:
        """Name the report whose heading row this is."""
        kinds = [
            kind
            for kind, columns in (
                (RELEASES_REPORT, COLUMNS_RELEASE),
                (WITHDRAWALS_REPORT, COLUMNS_WITHDRAWAL),
            )
            if not missing_columns(columns, header)
        ]
        if header in UNREAD_HEADERS:
            kinds.append(UNREAD_REPORT)
        return kinds

    @classmethod
    @override
    def takes_several(cls, kind: str) -> bool:
        """Read one report of each kind: the two never hold one transaction."""
        return False

    @classmethod
    def _is_withdrawals_report(cls, file_path: Path) -> bool:
        """Return whether the file is a withdrawals report.

        A plain INPUT is the report it was recognised as. The directory option
        goes by the name, which is all it has ever gone by.
        """
        if kind := cls.recognised_kinds.get(file_path):
            return kind == WITHDRAWALS_REPORT
        return file_path.name.casefold() == WITHDRAWALS_REPORT_FILENAME.casefold()

    @classmethod
    @override
    def file_path_filter(cls, file_path: Path) -> bool:
        """Choose which files to parse."""
        return file_path.name.casefold() in {
            WITHDRAWALS_REPORT_FILENAME.casefold(),
            RELEASES_REPORT_FILENAME.casefold(),
        }

    @classmethod
    @override
    def load_from_dir(cls, dir_path: Path) -> list[BrokerTransaction]:
        """Read the two reports by name, refusing either under another name.

        A second download saved as `Withdrawals Report (1).csv` would otherwise
        be left out without a word, and its sales with it. Every other file in
        the directory is still ignored.
        """
        renamed: list[str] = []
        for file_path in sorted(dir_path.glob(cls.glob_dir, case_sensitive=False)):
            if cls.file_path_filter(file_path):
                continue
            # Only the heading row is read, so an unrelated file that is large, in
            # another encoding, unreadable to the csv module or closed to this
            # user cannot stop the run. Before Python 3.14, `is_file()` too fails
            # on a link into a folder closed to this user.
            with contextlib.suppress(PermissionError, csv.Error):
                # Only files are opened: a named pipe would never end.
                if not file_path.is_file():
                    continue
                with file_path.open(
                    encoding=cls.encoding, errors="replace", newline=""
                ) as file:
                    renamed += [
                        f"  {file_path} is {kind}"
                        for kind in cls._kinds_with(next(csv.reader(file), []))
                        if kind != UNREAD_REPORT
                    ]
        if renamed:
            raise CgtError(
                "--mssb-dir reads Morgan Stanley reports only under the names "
                f"{RELEASES_REPORT_FILENAME} and {WITHDRAWALS_REPORT_FILENAME}, so "
                "it would leave out:\n"
                + "\n".join(renamed)
                + "\nKeep one copy of each report, holding the whole history, and "
                "move the other copies out of the directory. Then either give each "
                "report its name, or pass the directory as INPUT, without "
                "--mssb-dir: that reads a report whatever its name."
            )
        return super().load_from_dir(dir_path)

    @classmethod
    @override
    def pre_reading(cls, file: TextIO, file_path: Path) -> tuple[Iterable[str], int]:
        """Select the expected column set based on the report filename."""
        if cls._is_withdrawals_report(file_path):
            cls.columns = set(COLUMNS_WITHDRAWAL)
        else:
            cls.columns = set(COLUMNS_RELEASE)
        return file, 1

    @classmethod
    @override
    def read_row(cls, row: dict[str, str], file_path: Path) -> BrokerTransaction | None:
        """Read a single transaction from a row in the CSV."""
        if cls._is_withdrawals_report(file_path):
            return cls._init_from_withdrawal_report(row, file_path)
        return cls._init_from_release_report(row, file_path)

    @staticmethod
    def _init_from_release_report(row: dict[str, str], file: Path) -> BrokerTransaction:
        plan = row[ReleaseColumn.PLAN]

        if row[ReleaseColumn.TYPE] != "Release":
            raise ParsingError(
                file, f"Unknown type: {row[ReleaseColumn.TYPE]}", needs_update=True
            )

        if row[ReleaseColumn.STATUS] not in {"Complete", "Staged"}:
            raise ParsingError(file, f"Unknown status: {row[ReleaseColumn.STATUS]}")

        price_raw = row[ReleaseColumn.PRICE]
        if not price_raw or not price_raw.startswith("$"):
            raise ParsingError(file, f"Unknown price currency: {price_raw}")

        if row[ReleaseColumn.NET_CASH_PROCEEDS] != "$0.00":
            raise ParsingError(
                file,
                f"Non-zero Net Cash Proceeds: {row[ReleaseColumn.NET_CASH_PROCEEDS]}",
            )

        if plan not in KNOWN_SYMBOL_DICT:
            raise ParsingError(file, f"Unknown plan: {plan}")

        quantity = _parse_decimal(row, ReleaseColumn.NET_SHARE_PROCEEDS)
        price = _parse_decimal(row, ReleaseColumn.PRICE)
        amount = quantity * price
        symbol = KNOWN_SYMBOL_DICT[plan]

        return BrokerTransaction(
            date=_parse_date(row, ReleaseColumn.VEST_DATE),
            action=ActionType.STOCK_ACTIVITY,
            symbol=symbol,
            description=plan,
            quantity=quantity,
            price=price,
            fees=Decimal(0),
            amount=amount,
            currency=CurrencyCode("USD"),
            broker=BROKER_NAME,
        )

    # Morgan Stanley decided to put a notice at the end of the withdrawal report that looks
    # like this:
    # "Please note that any Alphabet share sales, transfers, or deposits that occurred on
    # or prior to the July 15, 2022 stock split are reflected in pre-split. Any sales,
    # transfers, or deposits that occurred after July 15, 2022 are in post-split values.
    # For GSU vests, your activity is displayed in post-split values."
    # It makes sense, but it totally breaks the CSV parsing
    @staticmethod
    def _is_notice(row: dict[str, str]) -> bool:
        row_str = ",".join([c for c in row.values() if c])
        return row_str.startswith("Please note")

    @staticmethod
    def _init_from_withdrawal_report(
        row: dict[str, str], file: Path
    ) -> BrokerTransaction | None:
        if MSSBParser._is_notice(row):
            return None

        plan = row[WithdrawalColumn.PLAN]

        if row[WithdrawalColumn.TYPE] != "Sale":
            raise ParsingError(
                file, f"Unknown type: {row[WithdrawalColumn.TYPE]}", needs_update=True
            )

        if row[WithdrawalColumn.ORDER_STATUS] != "Complete":
            raise ParsingError(
                file, f"Unknown status: {row[WithdrawalColumn.ORDER_STATUS]}"
            )

        price_raw = row[WithdrawalColumn.PRICE]
        if not price_raw or not price_raw.startswith("$"):
            raise ParsingError(file, f"Unknown price currency: {price_raw}")

        if plan not in KNOWN_SYMBOL_DICT:
            raise ParsingError(file, f"Unknown plan: {plan}")

        quantity = -_parse_decimal(row, WithdrawalColumn.QUANTITY)
        price = _parse_decimal(row, WithdrawalColumn.PRICE)
        amount = _parse_decimal(row, WithdrawalColumn.NET_AMOUNT)
        fees = quantity * price - amount

        if plan == "Cash":
            action = ActionType.TRANSFER
            amount *= -1
        else:
            action = ActionType.SELL

        symbol = KNOWN_SYMBOL_DICT[plan]
        transaction = BrokerTransaction(
            date=_parse_date(row, WithdrawalColumn.EXECUTION_DATE),
            action=action,
            symbol=symbol,
            description=plan,
            quantity=quantity,
            price=price,
            fees=fees,
            amount=amount,
            currency=CurrencyCode("USD"),
            broker=BROKER_NAME,
        )

        return MSSBParser._handle_stock_split(transaction)

    @staticmethod
    def _handle_stock_split(transaction: BrokerTransaction) -> BrokerTransaction:
        """Adjust pre-split sales."""
        for split in STOCK_SPLIT_INFO:
            if (
                transaction.symbol == split.symbol
                and transaction.action == ActionType.SELL
                and transaction.date <= split.date
            ):
                if transaction.quantity:
                    transaction.quantity *= split.factor
                if transaction.price:
                    transaction.price /= split.factor

        return transaction
