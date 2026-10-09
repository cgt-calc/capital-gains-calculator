"""Validators used for parsing arguments."""

from __future__ import annotations

import argparse
import datetime
from decimal import Decimal
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, override

from .const import EARLIEST_TAX_YEAR
from .version import DISTRIBUTION_NAME, get_version

STDIN_PATH = Path("-")

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

LOGGER = logging.getLogger(__name__)


def year_type(value: str) -> int:
    """Validate and convert year argument."""
    try:
        year = int(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"invalid int value: '{value}'") from err

    min_year = EARLIEST_TAX_YEAR
    max_year = datetime.datetime.now().year

    if year < min_year or year > max_year:
        raise argparse.ArgumentTypeError(
            f"year must be between {min_year} and {max_year}, got {year}"
        )

    return year


def income_type(value: str) -> Decimal:
    """Validate and convert an income in whole pounds.

    Only digits: a comma or a dot could separate thousands or pence, and a
    wrong guess changes the income a thousandfold. Twelve digits are more than
    any income needs.
    """
    if not re.fullmatch(r"\d{1,12}", value):
        raise argparse.ArgumentTypeError(
            f"invalid income: '{value}', expected whole pounds such as 45000"
        )
    return Decimal(value)


def date_type(value: str) -> datetime.date:
    """Validate and convert ISO format date argument."""
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError(
            f"invalid date value: '{value}', expected YYYY-MM-DD"
        ) from err


def ticker_list_type(value: str) -> list[str]:
    """Split comma-separated tickers and convert to uppercase list."""
    return [ticker.strip().upper() for ticker in value.split(",") if ticker.strip()]


def output_path_type(value: str) -> Path:
    """Validate non-empty output path and convert to Path.

    '-' is rejected: neither the report nor the dump is written to stdout, so
    it would create a file named '-', '-.pdf' or '-.tex'.
    """
    if not value.strip():
        raise argparse.ArgumentTypeError("path must not be empty")
    if value == "-":
        raise argparse.ArgumentTypeError(
            "expected file path, got '-': this file cannot be written to "
            "standard output"
        )
    return Path(value)


def _expand_user(value: str) -> Path:
    """Convert to Path, expanding a leading '~'.

    Path.expanduser raises RuntimeError when the home directory cannot be
    resolved, e.g. for an unknown '~user'. Argument parsing runs outside the
    exception handling in main(), so that would surface as a traceback.
    """
    try:
        return Path(value).expanduser()
    except RuntimeError as err:
        raise argparse.ArgumentTypeError(
            f"unable to expand user path: '{value}': {err}"
        ) from err


def _ensure_readable_file(path: Path, value: str) -> None:
    """Raise ArgumentTypeError when file cannot be read."""
    try:
        with path.open("rb"):
            pass
    except OSError as err:
        raise argparse.ArgumentTypeError(
            f"unable to read file path: '{value}': {err}"
        ) from err


def _ensure_readable_directory(path: Path, value: str) -> None:
    """Raise ArgumentTypeError when directory contents cannot be listed."""
    try:
        next(path.iterdir(), None)
    except OSError as err:
        raise argparse.ArgumentTypeError(
            f"unable to read directory path: '{value}': {err}"
        ) from err


def optional_cache_file_type(value: str) -> Path | None:
    """Convert non-empty value to a cache Path and ensure file semantics.

    An empty value disables the cache. The stdin marker is rejected: a cache is
    read and written, so '-' would create a file literally named '-' and later
    be read back as the cache. Anything that is not a regular file is rejected
    for the same reason.
    """
    if value.strip() == "":
        return None
    if value == "-":
        raise argparse.ArgumentTypeError("expected file path, got stdin marker: '-'")
    path = _expand_user(value)
    if path.exists():
        if path.is_dir():
            raise argparse.ArgumentTypeError(
                f"expected file path, got directory: '{value}'"
            )
        if not path.is_file():
            # Every cache is read back through Path.is_file(), so a pipe,
            # socket or device would be written to and then never read again.
            raise argparse.ArgumentTypeError(
                f"expected regular file path, got: '{value}'"
            )
        _ensure_readable_file(path, value)
    return path


def _existing_path_type(value: str, *, require_dir: bool) -> Path:
    """Ensure provided path exists and matches expected type."""
    path = _expand_user(value)
    if not path.exists():
        raise argparse.ArgumentTypeError(f"path does not exist: '{value}'")
    if require_dir and not path.is_dir():
        raise argparse.ArgumentTypeError(f"expected directory path, got: '{value}'")
    if not require_dir and not (path.is_file() or path.is_fifo()):
        raise argparse.ArgumentTypeError(f"expected file path, got: '{value}'")
    if require_dir:
        _ensure_readable_directory(path, value)
    elif not path.is_fifo():
        # Opening a pipe blocks until a writer attaches, and the probe would
        # consume from the stream the real read needs.
        _ensure_readable_file(path, value)
    return path


def existing_file_or_stdin_type(value: str) -> Path:
    """Validate that provided value points to an existing file, or '-' for stdin."""
    if value == "-":
        return STDIN_PATH
    return _existing_path_type(value, require_dir=False)


def existing_file_type(value: str) -> Path:
    """Validate that provided value points to an existing file.

    The stdin marker is rejected: options using this validator open the path
    directly instead of going through BaseSingleFileParser, so '-' would raise
    FileNotFoundError mid-run, or silently read a file literally named '-'.
    """
    if value == "-":
        raise argparse.ArgumentTypeError("expected file path, got stdin marker: '-'")
    return _existing_path_type(value, require_dir=False)


def existing_directory_type(value: str) -> Path:
    """Validate that provided value points to an existing directory."""
    return _existing_path_type(value, require_dir=True)


def set_completer(
    action: argparse.Action, completer: dict[str, str | dict[str, str]]
) -> None:
    """Attach shtab completion hint (e.g. shtab.FILE) to an argparse action."""
    action.complete = completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]


class StoreOnceAction(argparse.Action):
    """Store an option's value, and refuse a second value for it.

    argparse keeps the last value of a repeated option and drops the others
    without a word. For an input that means a file the user passed is never
    read, and the figures are worked out from the rest.
    """

    @override
    def __call__(  # type: ignore[explicit-any]
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        """Store the value unless the option already has one."""
        # Until an option is given, the namespace holds the parser's own
        # default object for its destination. A deprecated alias shares the
        # destination, so it counts as the same option.
        if getattr(namespace, self.dest) is not parser.get_default(self.dest):
            parser.error(
                f"{option_string} gives a second value for an option that takes "
                "one. cgt-calc would have used only the last and ignored the "
                "other: give the option once."
            )
        setattr(namespace, self.dest, values)


class DeprecatedAction(StoreOnceAction):
    """Print warning when deprecated argument is used."""

    @override
    def __call__(  # type: ignore[explicit-any]
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        """Check if argument is deprecated."""
        assert isinstance(option_string, str), "Positional arguments are not supported"
        replacements: dict[str, str] = {
            "--freetrade": "--freetrade-file",
            "--initial-prices": "--prices-file",
            "--initial-prices-file": "--prices-file",
            "--mssb": "--mssb-dir",
            "--raw": "--raw-file",
            "--report": "--output",
            "--schwab": "--schwab-file",
            "--schwab-award": "--schwab-award-file",
            "--schwab-equity-award-json": "--schwab-award-file",
            "--schwab_equity_award_json": "--schwab-award-file",
            "--sharesight": "--sharesight-dir",
            "--trading212": "--trading212-dir",
            "--vanguard": "--vanguard-file",
        }
        LOGGER.warning(
            "Option '%s' is deprecated; use '%s' instead.",
            option_string,
            replacements[option_string],
        )
        super().__call__(parser, namespace, values, option_string)


class VersionAction(argparse.Action):
    """Print the version and exit.

    Unlike argparse's built-in version action, which needs the string upfront,
    this resolves the version only when the option is used, keeping the lookup
    off the startup path of every other run.
    """

    @override
    def __call__(  # type: ignore[explicit-any]
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | Sequence[Any] | None,
        option_string: str | None = None,
    ) -> None:
        """Print the version and exit."""
        print(f"{DISTRIBUTION_NAME} {get_version()}")
        parser.exit()
