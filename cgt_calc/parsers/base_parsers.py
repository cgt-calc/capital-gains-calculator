"""Base parser from which all brokers are derived."""

from abc import ABC, abstractmethod
import argparse
from collections.abc import Iterable, Sequence
import csv
import dataclasses
import io
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
from cgt_calc.exceptions import (
    ParsingError,
    UnexpectedColumnCountError,
    not_text_in,
    reading_as,
)
from cgt_calc.logging import parsing_msg
from cgt_calc.model import BrokerTransaction, TransactionSource
from cgt_calc.ticker_renames import ambiguity, current_ticker
from cgt_calc.util import is_blank_row

if TYPE_CHECKING:
    import datetime as dt

LOGGER = logging.getLogger(__name__)

_ACCOUNT_COUNTER = itertools.count(1)


def read_text(file: TextIO, file_path: Path, encoding: str) -> str:
    """Read all of a broker input, refusing UTF-16 text without a byte-order mark.

    UTF-8 and Windows-1252 read each ASCII character of such text as that
    character and a NUL, so a file whose characters are all ASCII, as nearly
    every character of an export is, decodes without an error. The parser
    would then report a header or a row it cannot read, or find no rows at
    all, instead of the encoding. Only the first line is checked: a NUL
    further down is left to the parser as before, so a file that loaded with
    NULs after its last row, as a Vanguard export can, still loads.
    ``encoding`` is the one the refusal names.
    """
    text = file.read()
    if "\x00" in text.partition("\n")[0]:
        raise not_text_in(encoding, file_path)
    return text


def read_input(file_path: Path, encoding: str) -> str:
    """Read one input whole, so its contents can choose the reader.

    stdin cannot be reopened, so a caller that has to look at the text before
    deciding which parser owns it reads it here and hands the text on rather
    than the path.
    """
    with reading_as(encoding, file_path):
        if file_path == STDIN_PATH:
            return read_text(sys.stdin, file_path, encoding)
        with file_path.open(encoding=encoding) as file:
            return read_text(file, file_path, encoding)


def read_csv_rows(
    lines: Iterable[str],
    file_path: Path,
    *,
    first_row: int = 1,
    delimiter: str = ",",
    skipinitialspace: bool = False,
) -> list[list[str]]:
    """Read the rows of a CSV file, refusing one that runs over several lines.

    A row does that when a cell holds a line break. In a file whose cells
    never hold one, that is a double quote left open: it takes in every row
    down to the next double quote, or to the end of the file, and those rows
    would be left out without a word. ``first_row`` is the line of the file
    that the first of ``lines`` is on. An error the CSV reader itself raises
    is reported in the same way, with the file and the row it was reading.
    """
    reader = csv.reader(lines, delimiter=delimiter, skipinitialspace=skipinitialspace)
    rows: list[list[str]] = []
    try:
        for row in reader:
            # Every row before this one took one line, or it was refused.
            start = first_row + len(rows)
            end = first_row + reader.line_num - 1
            if end > start:
                raise ParsingError(
                    file_path,
                    f"This row runs on to row {end}, because one of its cells "
                    'holds a line break. If a double quote (") is opened on it '
                    "and not closed, every row down to there was read as part of "
                    "that cell: close or remove the double quote. Otherwise take "
                    "the line break out of the cell.",
                    row_index=start,
                )
            rows.append(row)
    except csv.Error as err:
        # The reader raises without giving the row it was reading, which is
        # the one after the rows read so far. A cell past the reader's limit
        # is nearly always a double quote left open far above the end of the
        # file. The reader's text for that error, matched here, is the same
        # in Python 3.12 to 3.14; a changed text would get the general message.
        if "field larger than field limit" in str(err):
            detail = (
                "A cell that starts on this row is longer than a CSV cell can be "
                f'({csv.field_size_limit():,} characters). A double quote (") is '
                "probably opened on it and not closed: close or remove it."
            )
        else:
            detail = f"The CSV reader could not read this row: {err}"
        raise ParsingError(file_path, detail, row_index=first_row + len(rows)) from err
    return rows


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
    dates: dict[str, list[dt.date]] = {}
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


@dataclasses.dataclass(frozen=True)
class PlainInput:
    """A file given as a plain INPUT, and what recognition found it to be."""

    path: Path
    # One of the descriptions the parser's `recognise` returns.
    kind: str
    # Standard input cannot be read twice, so what recognition read is kept.
    text: str | None = None


class BaseParser(ABC):
    """Base parser from which all brokers are derived."""

    pretty_name: str
    full_arg: str
    # Kinds `recognise` names that this broker exports and cgt-calc does not
    # read: a plain INPUT holding one is skipped, as its directory option skips it.
    skipped_kinds: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    @abstractmethod
    def register_arguments(cls, arg_group: argparse._ArgumentGroup) -> None:
        """Register argparse arguments for this broker."""

    @classmethod
    @abstractmethod
    def load_from_args(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load broker data from parsed arguments."""

    @classmethod
    def recognise(cls, text: str, file_path: Path) -> list[str]:  # noqa: ARG003
        """Name each kind of this broker's export that `text` is.

        A parser answers with the checks it makes itself when the file comes
        through its option, so that recognising a plain INPUT and reading an
        option's file cannot disagree on what the file is. Every parser in the
        registry answers; the award reader is reached through Schwab's.
        """
        raise AssertionError(f"{cls.__name__} does not say which files are its own")

    @classmethod
    def given_options(cls, args: argparse.Namespace) -> list[tuple[str, Path]]:
        """List each of this broker's options the run gives, with its path."""
        path = getattr(args, cls.full_arg.replace("-", "_"), None)
        return [(f"--{cls.full_arg}", path)] if path else []

    @classmethod
    def option_fills(cls, option: str, kind: str) -> bool:  # noqa: ARG003
        """Say whether `option` gives the input a `kind` file would fill."""
        return True

    @classmethod
    def takes_several(cls, kind: str) -> bool:  # noqa: ARG003
        """Say whether several `kind` files are read together as one account."""
        return False

    @classmethod
    def plain_inputs(cls, args: argparse.Namespace) -> list[PlainInput]:
        """Return the plain INPUTs recognised as this broker's."""
        plain: dict[type[BaseParser], list[PlainInput]] = getattr(
            args, "plain_inputs", {}
        )
        return plain.get(cls, [])


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
        # list is invariant, so widen explicitly rather than return list[T].
        transactions: list[BrokerTransaction] = []
        if file_path:
            transactions += cls.load_from_file(file_path)
        # Recognition refuses a second file, and a file next to the option.
        for plain in cls.plain_inputs(args):
            transactions += cls.load_plain(plain)
        return transactions

    @classmethod
    def load_plain(cls, plain: PlainInput) -> list[T]:
        """Load a plain INPUT as a file given with the option is loaded."""
        if plain.text is None:
            return cls.load_from_file(plain.path)
        return cls.load_from_stream(io.StringIO(plain.text), plain.path)

    @classmethod
    def encoding_of(cls, file_path: Path) -> str:  # noqa: ARG003
        """Return the encoding to open this file in.

        Overridden where a broker's file can arrive in more than one encoding.
        A file that fails to decode is reported as not being in ``encoding``,
        so an override returns either that or an encoding it has checked the
        file is in.
        """
        return cls.encoding

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
            with file_path.open(encoding=cls.encoding_of(file_path)) as file:
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
        text = read_text(file, file_path, cls.encoding)
        transactions = cls.read_transactions(io.StringIO(text), file_path)
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


def missing_columns(required: Iterable[str], header: Iterable[str]) -> set[str]:
    """Return the required columns a header lacks.

    None missing is what makes a file a broker's, whether it comes as a plain
    INPUT or with the broker's option, so both ask this one question.
    """
    return set(required) - set(header)


def _column_names(columns: set[str]) -> str:
    """List column names for an error, in an order that is the same on every run.

    Each name is shown as Python quotes a string, so that a space at either
    end of it, or a character that cannot be seen, shows up: Interactive
    Brokers names a column "Gross Amount ".
    """
    names = (repr(name) if name else "(no name)" for name in sorted(columns))
    return ", ".join(names) or "none"


class StandardCSVParser[T: BrokerTransaction](BaseSingleFileParser[T]):
    """Base parser for CSV files with a fixed set of expected columns."""

    columns: ClassVar[set[str]]
    optional_columns: ClassVar[set[str]] = set()
    # Whether an unchanged export with another header means the broker changed
    # its columns, which takes a cgt-calc update, rather than an edited file.
    header_mismatch_needs_update: ClassVar[bool] = False

    # What a plain INPUT this parser reads is recognised as.
    kind: ClassVar[str]

    @classmethod
    def header_line(cls, text: str) -> str | None:
        """Return the line the header is on, if the text has one.

        The first line, unless the export puts something above its table.
        """
        return next(iter(io.StringIO(text)), None)

    @classmethod
    @override
    def recognise(cls, text: str, file_path: Path) -> list[str]:
        """Name the export when its header holds every required column."""
        line = cls.header_line(text)
        if line is None or missing_columns(cls.columns, next(csv.reader([line]))):
            return []
        return [cls.kind]

    @classmethod
    def _validate_header(cls, header: Sequence[str], file_path: Path) -> None:
        """Validate the header has required columns and no unexpected ones."""
        header_set = set(header)
        missing = missing_columns(cls.columns, header)
        extra = header_set - cls.columns - cls.optional_columns
        if missing or extra:
            raise ParsingError(
                file_path,
                f"CSV header mismatch. Missing: {_column_names(missing)}. "
                f"Extra: {_column_names(extra)}.",
                needs_update=cls.header_mismatch_needs_update,
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
        # Read through once, only to refuse a row that runs over several
        # lines. The reader below skips an empty line without saying so, so it
        # cannot tell on which line such a row began.
        lines = list(lines)
        read_csv_rows(lines, file_path, first_row=header_row)
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
            # Set before the blank check, so a header over rows of only commas
            # or spaces loads no transactions, with the warning for that,
            # rather than being refused as an empty file.
            saw_row = True
            # Counted from the lines the reader has taken, not from the rows
            # it has given: it skips an empty line without giving a row.
            index = header_row + reader.line_num - 1
            # The fields past the header arrive as one list under the restkey.
            # Spread them back out so the row is checked, counted and printed
            # as the file states it.
            stated = [value for key, value in row.items() if key is not None]
            cells = [*stated, *row.get(None, [])]
            # A row with nothing in any cell is a cleared spreadsheet row or a
            # stray line, not a transaction, however many commas it has.
            if is_blank_row(cells):
                continue
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
                    raise UnexpectedColumnCountError(
                        cells, expected_col_count, file_path
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
    # Whether a transaction that two files of the directory both hold stops
    # the load. A parser that clears it says why.
    refuses_transaction_in_two_files: ClassVar[bool] = True
    # While plain INPUTs load, the kind each was recognised as, for a parser
    # that otherwise tells its reports apart by their names.
    recognised_kinds: ClassVar[dict[Path, str]] = {}

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
        # list is invariant, so widen explicitly rather than return list[T].
        transactions: list[BrokerTransaction] = []
        if dir_path:
            transactions += cls.load_from_dir(dir_path)
        # Recognition refuses plain INPUTs next to the option. Every plain
        # file of this broker is one account, as a directory is.
        if plain := cls.plain_inputs(args):
            transactions += cls.load_from_files(
                [item.path for item in plain],
                ", ".join(str(item.path) for item in plain),
                kinds={item.path: item.kind for item in plain},
            )
        return transactions

    @classmethod
    @override
    def takes_several(cls, kind: str) -> bool:
        """Read several files together, as the directory option does."""
        return True

    @classmethod
    def load_from_dir(cls, dir_path: Path) -> list[T]:
        """Load broker data from dir path.

        One directory is one declared account boundary, so every file in it
        is loaded under the same token.
        """
        files = sorted(dir_path.glob(cls.glob_dir, case_sensitive=False))
        return cls.load_from_files(
            [file_path for file_path in files if cls.file_path_filter(file_path)],
            f"directory {dir_path}",
        )

    @classmethod
    def load_from_files(
        cls,
        files: Sequence[Path],
        where: str,
        *,
        kinds: dict[Path, str] | None = None,
    ) -> list[T]:
        """Load files as one declared account boundary.

        A directory option and a broker's plain INPUTs both come here, so the
        checks across its files apply to both. `where` names the files in the
        warning that none holds a transaction. `kinds` gives the export each
        plain INPUT was recognised as; a file from the directory option has
        none, and its parser goes by its name, as it always has.
        """
        cls.recognised_kinds = kinds or {}
        try:
            account = next_account_token(cls.pretty_name)
            transactions: list[T] = []
            for file_path in files:
                loaded = cls.load_from_file(
                    file_path, warn_on_empty=False, account=account
                )
                if cls.refuses_transaction_in_two_files:
                    cls._refuse_transaction_in_two_files(
                        loaded, transactions, file_path
                    )
                transactions += loaded
        finally:
            cls.recognised_kinds = {}
        if not transactions:
            LOGGER.warning(
                "No transactions detected in %s for broker %s",
                where,
                cls.pretty_name,
            )
        return cls.finalize_transactions(cls.post_process_transactions(transactions))

    @classmethod
    def _refuse_transaction_in_two_files(
        cls, loaded: list[T], earlier: list[T], file_path: Path
    ) -> None:
        """Refuse a file holding a transaction that an earlier file holds too.

        Two files hold the same transaction when one is a copy of the other,
        or when the periods they cover overlap, and reading both counts it
        twice. Transactions are compared as they were read, so a copy saved
        in another encoding is one too. Rows that match within one file are
        left alone: one export listing a transaction twice is saying that it
        happened twice.
        """
        on_date: dict[dt.date, list[T]] = {}
        for transaction in earlier:
            on_date.setdefault(transaction.date, []).append(transaction)
        for transaction in loaded:
            for other in on_date.get(transaction.date, ()):
                if other == transaction:
                    raise ParsingError(
                        file_path,
                        "Two files in the directory hold this transaction, and "
                        "reading both would count it twice:\n"
                        f"{other}\n{transaction}\n"
                        "If the two rows are one transaction that both files "
                        "report, because one file is a copy of the other or the "
                        "periods they cover overlap, leave it in one file only: "
                        "take out of the directory a file whose transactions are "
                        "all in the other one, or export the periods again so "
                        "that they do not overlap.\n"
                        "If they are two separate transactions that only look the "
                        "same, keep both in one file. Check first that the two "
                        "files have the same column headings in the same order: "
                        "if they do not, export both reports again in one "
                        "layout, or put each value under the matching heading of "
                        "the file it moves to. Then insert one of the two rows "
                        "as a new line directly below the other, without pasting "
                        "over the row already there, and remove it from the file "
                        "it came from. Rows that match within one file are each "
                        "counted.",
                    )
