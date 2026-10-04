"""Describe the command line options as web form fields.

The parser built by ``create_parser`` is the single source of truth: adding an
option there adds it to the form. The kind of input is decided from the
validator an option uses, the same way ``reject_duplicate_stdin`` recognises
the options that read stdin.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import StrEnum
import logging
from typing import TYPE_CHECKING

from cgt_calc.args_parser import create_parser
from cgt_calc.args_validators import (
    date_type,
    existing_directory_type,
    existing_file_or_stdin_type,
    existing_file_type,
    optional_cache_file_type,
    year_type,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


LOGGER = logging.getLogger(__name__)


class FieldKind(StrEnum):
    """How an option is presented in the form."""

    FILE = "file"
    FILES = "files"
    TEXT = "text"
    YEAR = "year"
    DATE = "date"
    FLAG = "flag"
    OUTPUT_FILE = "output_file"


@dataclass(frozen=True, slots=True)
class Field:
    """One option of the command line tool."""

    dest: str
    flag: str
    kind: FieldKind
    help: str
    metavar: str | None = None

    @property
    def directory_name(self) -> str:
        """Name of the folder holding the uploads of a directory option.

        ``--trading212-dir`` uploads land in ``trading212/``, so the command
        reads like one typed in a folder of exports.
        """
        return self.flag.removeprefix("--").removesuffix("-dir")

    @property
    def output_path(self) -> str:
        """Path given to an option that writes a file, relative to the run."""
        return f"out/{self.flag.removeprefix('--')}.csv"


@dataclass(frozen=True, slots=True)
class Section:
    """A titled group of fields, mirroring an argparse argument group."""

    title: str
    fields: tuple[Field, ...]


class UnsupportedOptionError(Exception):
    """Raised when an option has a validator this form does not know."""


# Options the web interface decides itself or that make no sense in a form. The
# report path is fixed so the result can be found; the CLI default already puts
# it under out/. --print-completion is added by shtab and prints a shell script.
MANAGED_DESTS = frozenset({"output", "print_completion"})

# Options that write a new file. The form only offers a switch and picks the
# path, because a path typed into a form would point outside the run.
OUTPUT_FILE_DESTS = frozenset({"dump_transactions"})

_FILE_TYPES: frozenset[object] = frozenset(
    {existing_file_or_stdin_type, existing_file_type, optional_cache_file_type}
)


def _help_text(action: argparse.Action) -> str:
    """Expand placeholders such as ``%(default)s`` the way ``--help`` does."""
    return (action.help or "") % dict(vars(action))


def _long_flag(action: argparse.Action) -> str:
    """Return the long spelling of an option, e.g. ``--verbose`` for ``-v``."""
    return next(option for option in action.option_strings if option.startswith("--"))


def _kind(action: argparse.Action) -> FieldKind | None:
    """Return the input kind for an option, or None when it is not offered."""
    if action.help == argparse.SUPPRESS or action.dest in MANAGED_DESTS:
        return None
    if action.default == argparse.SUPPRESS:
        # Stores no value: --help, --version and shtab's --print-completion act
        # and exit, which makes no sense in a form.
        return None
    if action.nargs == 0:
        return FieldKind.FLAG if isinstance(action.const, bool) else None
    if action.dest in OUTPUT_FILE_DESTS:
        return FieldKind.OUTPUT_FILE
    if action.type in _FILE_TYPES:
        return FieldKind.FILE
    if action.type is existing_directory_type:
        return FieldKind.FILES
    if action.type is year_type:
        return FieldKind.YEAR
    if action.type is date_type:
        return FieldKind.DATE
    if action.type is not None and action.nargs is None:
        # Any other validator (tickers) takes typed text, checked by the CLI.
        return FieldKind.TEXT
    raise UnsupportedOptionError(
        f"Option {action.option_strings[0]} has no web form equivalent"
    )


def build_sections(
    parser: argparse.ArgumentParser | None = None, *, strict: bool = False
) -> list[Section]:
    """Build the form description from the command line parser.

    An option the form cannot express is left out with a warning, so one new
    option never takes the whole interface down. Tests build with ``strict``
    to fail instead, which is how a new option gets noticed.
    """
    parser = parser or create_parser()
    sections: list[Section] = []
    seen: set[str] = set()
    for group in parser._action_groups:  # noqa: SLF001
        fields: list[Field] = []
        for action in group._group_actions:  # noqa: SLF001
            try:
                kind = _kind(action)
            except UnsupportedOptionError as err:
                if strict:
                    raise
                LOGGER.warning("%s; use the command line for it", err)
                continue
            # Deprecated aliases share a dest with the option they replace.
            if kind is None or action.dest in seen:
                continue
            seen.add(action.dest)
            fields.append(
                Field(
                    dest=action.dest,
                    flag=_long_flag(action),
                    kind=kind,
                    help=_help_text(action),
                    metavar=action.metavar if isinstance(action.metavar, str) else None,
                )
            )
        if fields:
            sections.append(Section(group.title or "", tuple(fields)))
    return sections


def flatten(sections: Sequence[Section]) -> list[Field]:
    """Return every field in form order."""
    return [field for section in sections for field in section.fields]
