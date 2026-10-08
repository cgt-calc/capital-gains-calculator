"""Base parser from which all brokers are derived."""

from abc import ABC, abstractmethod
import argparse
from collections.abc import Iterable, Sequence
import csv
import dataclasses
import itertools
import logging
from pathlib import Path
import sys
from typing import TYPE_CHECKING, ClassVar, TextIO, override

import shtab

from cgt_calc.args_validators import (
    STDIN_PATH,
    DeprecatedAction,
    existing_directory_type,
    existing_file_or_stdin_type,
    set_completer,
)
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError, reading_as
from cgt_calc.logging import parsing_msg
from cgt_calc.model import BrokerTransaction, TransactionSource
from cgt_calc.ticker_renames import ambiguity, current_ticker

if TYPE_CHECKING:
    import datetime

LOGGER = logging.getLogger(__name__)

_ACCOUNT_COUNTER = itertools.count(1)


def read_input(file_path: Path, encoding: str) -> str:
    """Read one input whole, so its contents can choose the reader.

    stdin cannot be reopened, so a caller that has to look at the text before
    deciding which parser owns it reads it here and hands the text on rather
    than the path.
    """
    with reading_as(encoding, file_path):
        if file_path == STDIN_PATH:
            return sys.stdin.read()
        with file_path.open(encoding=encoding) as file:
            return file.read()


def use_current_tickers(
    transactions: Sequence[BrokerTransaction], file_path: Path
) -> None:
    """Name each holding by its current ticker, from the built-in rename table.

    The shared loader runs this on every file it reads, so that a holding
    bought under an old ticker and sold under the new one is one holding
    whichever broker exported it, without each parser mapping the ticker
    itself. Schwab also runs it as it reads its rows, because it compares
    tickers before the shared loader gets to them; a second run changes
    nothing. Parsers still call ``current_ticker`` themselves for a ticker
    they need while reading, to key a table of their own, and for one held
    outside ``symbol``, such as an option's underlying.

    A file is refused when only some of its rows under one ticker, without
    an ISIN, fall while that ticker was a renamed security's: nothing says
    which rows are which security's.
    """
    dates: dict[str, list[datetime.date]] = {}
    for transaction in transactions:
        if transaction.symbol is not None and transaction.isin is None:
            dates.setdefault(transaction.symbol, []).append(transaction.date)
    for symbol, symbol_dates in dates.items():
        if (reason := ambiguity(symbol, symbol_dates)) is not None:
            raise ParsingError(file_path, reason)
    for transaction in transactions:
        if transaction.symbol is not None:
            transaction.symbol = current_ticker(
                transaction.symbol, transaction.date, transaction.isin
            )


def next_account_token(name: str) -> str:
    """Return a fresh opaque token for one declared account boundary.

    Every separately configured input is a boundary of its own: one file
    passed on its own, or one directory of exports. The token only says which
    transactions were configured together, so it is deliberately not derived
    from the path, which is a location on this machine rather than an account.
    """
    return f"{name} #{next(_ACCOUNT_COUNTER)}"


class BaseParser(ABC):
    """Base parser from which all brokers are derived."""

    pretty_name: str

    @classmethod
    @abstractmethod
    def register_arguments(cls, arg_group: argparse._ArgumentGroup) -> None:
        """Register argparse arguments for this broker."""

    @classmethod
    @abstractmethod
    def load_from_args(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load broker data from parsed arguments."""


class BaseSingleFileParser[T: BrokerTransaction](BaseParser):
    """Parser for single transaction file."""

    arg_name: str
    format_name: str
    full_arg: str
    deprecated_flags: ClassVar[list[str]] = []
    argument_help: ClassVar[str | None] = None
    # UTF-8 that also drops a leading byte-order mark. Excel's "CSV UTF-8"
    # writes one, and it would otherwise be read as part of the first column
    # name.
    encoding: str = "utf-8-sig"
    # Set only where the format documents that rows sharing a date are in the
    # order they happened. It decides whether a row can be placed either side
    # of a same-day share reorganisation without an exported time.
    rows_in_time_order: ClassVar[bool] = False

    @override
    def __init_subclass__(cls, **kwargs: object) -> None:
        """Compute full arg."""
        super().__init_subclass__(**kwargs)
        # compute full_arg once per subclass at class creation time
        suffix = "json" if getattr(cls, "format_name", None) == "JSON" else "file"
        cls.full_arg = f"{getattr(cls, 'arg_name', None)}-{suffix}"

    @classmethod
    @override
    def register_arguments(cls, arg_group: argparse._ArgumentGroup) -> None:
        """Register argparse arguments for this broker."""
        set_completer(
            arg_group.add_argument(
                f"--{cls.full_arg}",
                type=existing_file_or_stdin_type,
                default=None,
                metavar="PATH",
                help=cls.argument_help
                or f"{cls.pretty_name} transaction history in {cls.format_name} format",
            ),
            shtab.FILE,
        )
        for deprecated_flag in cls.deprecated_flags:
            arg_group.add_argument(
                deprecated_flag,
                action=DeprecatedAction,
                dest=cls.full_arg.replace("-", "_"),
                type=existing_file_or_stdin_type,
                help=argparse.SUPPRESS,
            )

    @classmethod
    @override
    def load_from_args(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load broker data from parsed arguments."""
        file_path = getattr(args, cls.full_arg.replace("-", "_"))
        if file_path:
            # list is invariant, so widen explicitly rather than return list[T].
            transactions: list[BrokerTransaction] = list(cls.load_from_file(file_path))
            return transactions
        return []

    @classmethod
    def load_from_file(
        cls,
        file_path: Path,
        *,
        warn_on_empty: bool = True,
        show_parsing_msg: bool = True,
        account: str | None = None,
    ) -> list[T]:
        """Load broker data from file path.

        ``account`` is the boundary this file was loaded under. A directory
        load passes its own token so every file in it shares one boundary; a
        file loaded on its own is a boundary of its own.
        """
        with reading_as(cls.encoding, file_path):
            if file_path == STDIN_PATH:
                return cls.load_from_stream(
                    sys.stdin,
                    STDIN_PATH,
                    warn_on_empty=warn_on_empty,
                    show_parsing_msg=show_parsing_msg,
                    account=account,
                )
            with file_path.open(encoding=cls.encoding) as file:
                return cls.load_from_stream(
                    file,
                    file_path,
                    warn_on_empty=warn_on_empty,
                    show_parsing_msg=show_parsing_msg,
                    account=account,
                )

    @classmethod
    def load_from_stream(
        cls,
        file: TextIO,
        file_path: Path,
        *,
        warn_on_empty: bool = True,
        show_parsing_msg: bool = True,
        account: str | None = None,
    ) -> list[T]:
        """Load broker data from a stream someone else opened.

        ``file_path`` is what the stream holds, used to report rows and to
        record where each transaction came from. Callers that have already
        read the input, to decide which parser owns it, hand the text back
        here rather than reopening a path or a stdin that is now exhausted.
        """
        # A file passed on its own is a boundary of its own, and nothing
        # follows it, so it finalizes here. A directory load supplies its own
        # token and finalizes once over every file instead.
        standalone = account is None
        boundary = (
            account if account is not None else next_account_token(cls.pretty_name)
        )
        if show_parsing_msg:
            parsing_msg("stdin" if file_path == STDIN_PATH else file_path)
        transactions = cls.read_transactions(file, file_path)
        use_current_tickers(transactions, file_path)
        if not transactions and warn_on_empty:
            LOGGER.warning("No transactions detected in file %s", file_path)
        cls.stamp_source(transactions, file_path, boundary)
        transactions = cls.post_process_transactions(transactions)
        if standalone:
            transactions = cls.finalize_transactions(transactions)
        return transactions

    @classmethod
    def stamp_source(
        cls,
        transactions: list[T],
        file_path: Path,
        account: str,
    ) -> None:
        """Record where each transaction was read from.

        Whatever the parser already put on the transaction is kept: it knows
        the line a row came from and the instant the export stated, and this
        only adds what it cannot know. ``index`` is the order the parser read
        the rows in, which is what says which of two rows on one day came
        first.
        """
        for index, transaction in enumerate(transactions):
            transaction.source = dataclasses.replace(
                transaction.source or TransactionSource(),
                parser=cls.pretty_name,
                account=account,
                file=file_path,
                index=index,
                rows_in_time_order=cls.rows_in_time_order,
            )

    @classmethod
    @abstractmethod
    def read_transactions(cls, file: TextIO, file_path: Path) -> list[T]:
        """Parse broker transactions from open file."""

    @classmethod
    def post_process_transactions(cls, transactions: list[T]) -> list[T]:
        """Do any required post processing after loading the transactions."""
        return transactions

    @classmethod
    def file_path_filter(cls, file_path: Path) -> bool:  # noqa: ARG003
        """Choose which files to parse."""
        return True

    @classmethod
    def finalize_transactions(cls, transactions: list[T]) -> list[T]:
        """Post-process once every file in one declared boundary is read.

        `post_process_transactions` runs per file as well as over the union of
        a directory, so work that needs the whole boundary at once cannot go
        there: it would run on the first file alone and reject what the
        second file completes. This runs exactly once per boundary.
        """
        return transactions


def _column_names(columns: set[str]) -> str:
    """List column names for an error, in an order that is the same on every run."""
    return ", ".join(sorted(name or "(no name)" for name in columns)) or "none"


class StandardCSVParser[T: BrokerTransaction](BaseSingleFileParser[T]):
    """Base parser for CSV files with a fixed set of expected columns."""

    columns: ClassVar[set[str]]
    optional_columns: ClassVar[set[str]] = set()

    @classmethod
    def _validate_header(cls, header: Sequence[str], file_path: Path) -> None:
        """Validate the header has required columns and no unexpected ones."""
        header_set = set(header)
        missing = cls.columns - header_set
        extra = header_set - cls.columns - cls.optional_columns
        if missing or extra:
            raise ParsingError(
                file_path,
                f"CSV header mismatch. Missing: {_column_names(missing)}. "
                f"Extra: {_column_names(extra)}.",
            )

    @classmethod
    @abstractmethod
    def read_row(cls, row: dict[str, str], file_path: Path) -> T | None:
        """Read a single transaction from a row in the CSV."""

    @classmethod
    def pre_reading(
        cls,
        file: TextIO,
        file_path: Path,  # noqa: ARG003
    ) -> tuple[Iterable[str], int]:
        """Return preprocessed lines and the physical line of their header."""
        return file, 1

    @classmethod
    @override
    def read_transactions(cls, file: TextIO, file_path: Path) -> list[T]:
        """Read transactions from a CSV file."""
        lines, header_row = cls.pre_reading(file, file_path)
        reader = csv.DictReader(lines)
        if reader.fieldnames is None:
            raise ParsingError(
                file_path, f"{cls.pretty_name} CSV doesn't have a header"
            )
        cls._validate_header(reader.fieldnames, file_path)
        expected_col_count = len(reader.fieldnames)

        transactions: list[T] = []
        saw_row = False
        for row in reader:
            saw_row = True
            # Counted from the lines the reader has taken, not from the rows
            # it has given: it skips an empty line without giving a row.
            index = header_row + reader.line_num - 1
            try:
                # A row with MORE fields than the header collects the extras
                # under DictReader's `None` key (`restkey`), which changes
                # `len(row)`. A row with FEWER fields is instead padded with
                # `None` values for the missing trailing keys (`restval`), so
                # `len(row)` alone can't detect it here. We deliberately don't
                # reject those on `None` values alone: some parsers (e.g.
                # Morgan Stanley's withdrawal report) legitimately rely on
                # DictReader padding a short trailing row to recognise and
                # skip it in `read_row`. Instead, a short/malformed row that
                # a parser doesn't expect is caught below when it blows up
                # trying to parse a `None` field, and reported with row
                # context rather than as an unhandled traceback.
                if len(row) != expected_col_count:
                    # The fields past the header arrive as one list under the
                    # restkey. Spread them back out so the row is counted and
                    # printed as the file states it.
                    stated = [value for key, value in row.items() if key is not None]
                    raise UnexpectedColumnCountError(
                        [*stated, *row.get(None, [])], expected_col_count, file_path
                    )
                transaction = cls.read_row(row, file_path)
                if transaction:
                    # Whatever `read_row` recorded is kept, because it knows
                    # what the row itself stated and this only adds the line
                    # it came from. Replacing the source outright would drop
                    # an exported instant, which is the only evidence that
                    # can order two rows sharing a date.
                    transaction.source = dataclasses.replace(
                        transaction.source or TransactionSource(), row=index
                    )
                    transactions.append(transaction)
            except ParsingError as err:
                err.add_row_context(index)
                raise
            except (ValueError, TypeError, AttributeError) as err:
                raise ParsingError(file_path, str(err), row_index=index) from err

        if not saw_row:
            raise ParsingError(file_path, f"{cls.pretty_name} CSV file is empty")
        return transactions


class BaseDirParser[T: BrokerTransaction](BaseSingleFileParser[T]):
    """Parser for loading all files within a directory."""

    glob_dir: str

    @override
    def __init_subclass__(cls, **kwargs: object) -> None:
        """Compute full arg."""
        super().__init_subclass__(**kwargs)
        cls.full_arg = f"{getattr(cls, 'arg_name', None)}-dir"

    @classmethod
    @override
    def register_arguments(cls, arg_group: argparse._ArgumentGroup) -> None:
        """Register argparse arguments for this broker."""
        set_completer(
            arg_group.add_argument(
                f"--{cls.full_arg}",
                type=existing_directory_type,
                default=None,
                metavar="DIR",
                help=f"directory with {cls.pretty_name} reports "
                f"in {cls.format_name} format",
            ),
            shtab.DIRECTORY,
        )
        for deprecated_flag in cls.deprecated_flags:
            arg_group.add_argument(
                deprecated_flag,
                action=DeprecatedAction,
                dest=cls.full_arg.replace("-", "_"),
                type=existing_directory_type,
                help=argparse.SUPPRESS,
            )

    @classmethod
    @override
    def load_from_args(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load broker data from parsed arguments."""
        dir_path = getattr(args, cls.full_arg.replace("-", "_"))
        if dir_path:
            # list is invariant, so widen explicitly rather than return list[T].
            transactions: list[BrokerTransaction] = list(cls.load_from_dir(dir_path))
            return transactions
        return []

    @classmethod
    def load_from_dir(cls, dir_path: Path) -> list[T]:
        """Load broker data from dir path.

        One directory is one declared account boundary, so every file in it
        is loaded under the same token.
        """
        account = next_account_token(cls.pretty_name)
        transactions: list[T] = []
        for file_path in sorted(dir_path.glob(cls.glob_dir, case_sensitive=False)):
            if cls.file_path_filter(file_path):
                transactions += cls.load_from_file(
                    file_path, warn_on_empty=False, account=account
                )
        if not transactions:
            LOGGER.warning(
                "No transactions detected in directory %s for broker %s",
                dir_path,
                cls.pretty_name,
            )
        return cls.finalize_transactions(cls.post_process_transactions(transactions))
