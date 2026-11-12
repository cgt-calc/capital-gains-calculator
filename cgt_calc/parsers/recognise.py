"""Recognise what each plain INPUT holds, from its contents alone.

Every check here is the one the file's own reader makes when the file comes
with its option: each parser answers through `recognise`, and each file read
with another option through the header check its reader uses. A file given
with an option is never looked at here, so a run with options alone is as it
was.
"""

from __future__ import annotations

from collections import defaultdict
import contextlib
import csv
import io
import logging
import os
import sys
from typing import TYPE_CHECKING

from colorama import Fore

from cgt_calc.args_validators import STDIN_PATH
from cgt_calc.currency_converter import is_comment, is_exchange_rates_header
from cgt_calc.exceptions import CgtError, ParsingError, not_text_in
from cgt_calc.isin_converter import is_isin_translation_header
from cgt_calc.logging import style_text
from cgt_calc.share_prices import is_share_prices_header
from cgt_calc.spin_off_handler import is_spin_offs_header
from cgt_calc.transaction_dumper import DUMP_FIELDS

from .base_parsers import BaseDirParser, PlainInput, read_input, read_text
from .eri.raw import ERIRawParser
from .hl import HargreavesLansdownParser

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from .base_parsers import BaseParser

LOGGER = logging.getLogger(__name__)

# What a spreadsheet workbook starts with: a ZIP archive (.xlsx) or an OLE2
# compound file (.xls).
WORKBOOK_SIGNATURES = (b"PK\x03\x04", b"\xd0\xcf\x11\xe0")
PDF_SIGNATURE = b"%PDF-"
ENCODING = "utf-8-sig"

NOT_RECOGNISED_HELP = (
    "cgt-calc recognises an export by its column headings, so a file from a "
    "report or broker it does not read, or one without its heading row, is not "
    "recognised. If a file is from a supported broker, pass it with that "
    "broker's option (see cgt-calc --help): its reader then says what it "
    "expected. See https://cgt-calc.uk/brokers/"
)
SEVERAL_HELP = (
    "cgt-calc does not guess: pass such a file with the option for the broker it "
    "came from, or keep one export per file."
)


def _first_row(text: str, *, skipinitialspace: bool = False) -> list[str]:
    return next(csv.reader(io.StringIO(text), skipinitialspace=skipinitialspace), [])


def _is_exchange_rates(text: str) -> bool:
    lines = [line for line in io.StringIO(text) if not is_comment(line)]
    header = next(csv.reader(lines), None)
    return header is not None and is_exchange_rates_header(header)


# Files cgt-calc reads with another option, or writes: each is named, with the
# option that reads it, rather than left "not recognised". Each check is the
# one its reader makes, reading the first row as that reader does.
OTHER_FILES: tuple[tuple[str, str | None, Callable[[str, Path], bool]], ...] = (
    (
        "ERI data",
        "--eri-raw-file",
        lambda text, path: bool(ERIRawParser.recognise(text, path)),
    ),
    (
        "an ISIN translation file",
        "--isin-translation-file",
        lambda text, _: is_isin_translation_header(
            _first_row(text, skipinitialspace=True)
        ),
    ),
    (
        "a share prices file",
        "--prices-file",
        lambda text, _: is_share_prices_header(_first_row(text)),
    ),
    (
        "an exchange rates cache",
        "--exchange-rates-file",
        lambda text, _: _is_exchange_rates(text),
    ),
    (
        "a spin-offs file",
        "--spin-offs-file",
        lambda text, _: is_spin_offs_header(_first_row(text)),
    ),
    (
        "the --dump-transactions output of cgt-calc",
        None,
        lambda text, _: tuple(_first_row(text)) == DUMP_FIELDS,
    ),
)


def shown(path: Path) -> str:
    """Show a path as the progress lines do, or name standard input."""
    if path == STDIN_PATH:
        return "standard input"
    return str(path).replace(os.path.sep, "/")


def _source(path: Path) -> str:
    """Name an input at the start of a sentence."""
    return "Standard input" if path == STDIN_PATH else shown(path)


def _files_of(argument: Path, problems: list[str]) -> list[Path]:
    """Return the files an INPUT stands for: itself, or a directory's exports."""
    if argument == STDIN_PATH or not argument.is_dir():
        return [argument]
    # Not recursive, like every directory option.
    files = sorted(
        path
        for pattern in ("*.csv", "*.json")
        for path in argument.glob(pattern, case_sensitive=False)
        if path.is_file()
    )
    if not files:
        problems.append(
            f"{shown(argument)} holds no CSV or JSON file, so no broker can be "
            "named for it."
        )
    return files


def _identity(path: Path) -> tuple[int, int]:
    """Return what makes two paths one file, hard and symbolic links included."""
    stat = path.stat()
    return stat.st_dev, stat.st_ino


class _Recogniser:
    """Recognise every plain INPUT, collecting each problem rather than stopping."""

    def __init__(
        self, args: argparse.Namespace, brokers: Sequence[type[BaseParser]]
    ) -> None:
        self.args = args
        self.brokers = brokers
        self.problems: list[str] = []
        self.unrecognised = False
        self.several = False
        self.lines: list[str] = []
        self.groups: dict[type[BaseParser], list[PlainInput]] = defaultdict(list)
        self.pdfs: list[tuple[Path, int]] = []
        # Each file an option or an INPUT reaches, and the argument that did.
        self.seen: dict[tuple[int, int], str] = {}
        given: list[tuple[str, Path | None]] = [
            pair for broker in brokers for pair in broker.given_options(args)
        ]
        given += [
            (option, getattr(args, option[2:].replace("-", "_"), None))
            for _, option, _ in OTHER_FILES
            if option
        ]
        for option, path in given:
            if path and path != STDIN_PATH and path.is_file():
                self.seen.setdefault(_identity(path), option)

    def add(self, argument: Path) -> None:
        """Recognise the files one INPUT stands for."""
        for file in _files_of(argument, self.problems):
            if file != STDIN_PATH:
                identity = _identity(file)
                if identity in self.seen:
                    self.problems.append(
                        f"{shown(file)} is given twice, by {self.seen[identity]} "
                        f"and by {shown(argument)}. Give each file once."
                    )
                    continue
                self.seen[identity] = shown(argument)
            self._recognise(file, argument)

    def _read(self, file: Path) -> tuple[str, Sequence[type[BaseParser]]] | None:
        """Read the file as its readers would, and say which of them can read it."""
        if file == STDIN_PATH:
            # Read as the options read it, and only once: it cannot be reread.
            return read_input(file, ENCODING), self.brokers
        data = file.read_bytes()
        if data.startswith(WORKBOOK_SIGNATURES):
            self.problems.append(
                f"{shown(file)} is a spreadsheet workbook. Save the worksheet "
                "as a CSV file and pass that."
            )
            return None
        if data.startswith(PDF_SIGNATURE):
            # Its line waits for every HL file to be known, in its place.
            self.pdfs.append((file, len(self.lines)))
            self.lines.append("")
            return None
        readers = self.brokers
        try:
            text = data.decode(ENCODING)
        except UnicodeDecodeError:
            # Only Hargreaves Lansdown reads a file that is not UTF-8.
            readers = [HargreavesLansdownParser]
            try:
                text = data.decode(HargreavesLansdownParser.encoding)
            except UnicodeDecodeError:
                raise not_text_in(ENCODING, file) from None
        # The readers' own refusal of UTF-16 text without a byte-order mark, on
        # text whose line ends read as their text files give them: a lone
        # carriage return, or one before a newline, ends a line as "\n" does.
        return read_text(io.StringIO(text, newline=None), file, ENCODING), readers

    def _recognise(self, file: Path, argument: Path) -> None:
        try:
            read = self._read(file)
        except ParsingError as err:
            self.problems.append(str(err))
            return
        if read is None:
            return
        text, readers = read
        if not text.strip():
            self.problems.append(
                f"{shown(file)} is empty, so cgt-calc cannot tell which broker "
                "it is from."
            )
            return
        # A CSV the csv module cannot read, such as one with a quote left
        # open, is no format's: its reader, given the option, says where.
        matches: list[tuple[type[BaseParser], str]] = []
        for broker in readers:
            with contextlib.suppress(csv.Error):
                matches += [(broker, kind) for kind in broker.recognise(text, file)]
        others = []
        # Every other file is read as UTF-8 only.
        for name, option, check in OTHER_FILES if readers is self.brokers else ():
            with contextlib.suppress(csv.Error):
                if check(text, file):
                    others.append((name, option))
        names = [kind for _, kind in matches] + [name for name, _ in others]
        if len(names) > 1:
            self.several = True
            self.problems.append(
                f"{shown(file)} matches more than one format: {', '.join(names)}."
            )
        elif others:
            name, option = others[0]
            use = (
                f"pass it with {option}"
                if option
                else "pass the exports it was written from, as cgt-calc does "
                "not read it back"
            )
            if file != argument:
                # Given with its option too, it would then be given twice.
                use = f"move it out of {shown(argument)} and {use}"
            self.problems.append(
                f"{shown(file)} is {name}, not a broker export: {use}."
            )
        elif matches:
            self._take(file, text, *matches[0])
        elif readers is not self.brokers:
            # Not UTF-8, and not the export that need not be.
            self.problems.append(str(not_text_in(ENCODING, file)))
        else:
            self.unrecognised = True
            self.problems.append(f"{shown(file)} is not recognised.")

    def _take(self, file: Path, text: str, broker: type[BaseParser], kind: str) -> None:
        """Give the recognised file to its broker, unless something rules it out."""
        if kind in broker.skipped_kinds:
            self.lines.append(f"Skipped {shown(file)}: {kind}")
            return
        # Standard input, or a pipe such as `<(convert.py)`, can be read only
        # once, so what was read here is what its reader is given.
        once = file == STDIN_PATH or not file.is_file()
        if once and issubclass(broker, BaseDirParser):
            self.problems.append(
                f"{_source(file)} holds {kind}, which cgt-calc reads only from "
                "files: save it to a file and pass that."
            )
            return
        for option, path in broker.given_options(self.args):
            if broker.option_fills(option, kind):
                if not broker.takes_several(kind):
                    advice = (
                        "cgt-calc reads one per run: pass the export that holds "
                        "the whole history, with the option or as INPUT."
                    )
                elif path.is_dir():
                    advice = (
                        "Give it one way: every such file with the option, or "
                        "every one as INPUT."
                    )
                else:
                    advice = (
                        "That option reads one file: pass every such file as INPUT."
                    )
                self.problems.append(
                    f"{shown(file)} is {kind}, but {option} already gives that "
                    f"input. {advice}"
                )
                return
        self.lines.append(f"Recognised {shown(file)} as {kind}")
        self.groups[broker].append(PlainInput(file, kind, text if once else None))

    def finish(self) -> dict[type[BaseParser], list[PlainInput]]:
        """Check the files together, print a line for each, and stop on a problem."""
        for broker, plain in self.groups.items():
            kinds = {item.kind for item in plain}
            for kind in sorted(kinds):
                items = [item for item in plain if item.kind == kind]
                if len(items) > 1 and not broker.takes_several(kind):
                    self.problems.append(
                        f"{' and '.join(shown(item.path) for item in items)} are "
                        f"each {kind}, and cgt-calc reads one per run. Pass the "
                        "one that holds the whole history."
                    )
                elif len(items) > 1:
                    self.problems.extend(
                        f"{_source(item.path)} holds {kind}, which cgt-calc reads "
                        "together with the other files of that kind only from "
                        "files: save it to a file and pass that."
                        for item in items
                        if item.text is not None
                    )
        hl_folders = {
            item.path.parent for item in self.groups.get(HargreavesLansdownParser, [])
        }
        for pdf, place in self.pdfs:
            if pdf.parent in hl_folders:
                # Nothing in the PDF is checked: the summary's reader opens it
                # only if a row names it.
                self.lines[place] = (
                    f"Accepted {shown(pdf)}, in the folder of a Hargreaves "
                    "Lansdown Transaction Summary: read as a contract note if a "
                    "row of the summary names it"
                )
            else:
                self.problems.append(
                    f"{shown(pdf)} is a PDF. cgt-calc reads a PDF only as a "
                    "Hargreaves Lansdown contract note, in the folder of the "
                    "Transaction Summary passed with it."
                )
        for line in filter(None, self.lines):
            LOGGER.info(style_text(line, colour=Fore.CYAN, stream=sys.stderr))
        if self.problems:
            details = [
                *self.problems,
                *([NOT_RECOGNISED_HELP] if self.unrecognised else []),
                *([SEVERAL_HELP] if self.several else []),
            ]
            raise CgtError(
                "cgt-calc cannot use every INPUT:\n"
                + "\n".join(f"  {detail}" for detail in details)
            )
        return dict(self.groups)


def recognise_inputs(
    args: argparse.Namespace, brokers: Sequence[type[BaseParser]]
) -> dict[type[BaseParser], list[PlainInput]]:
    """Recognise each plain INPUT, and group the files by the broker that reads them."""
    recogniser = _Recogniser(args, brokers)
    for argument in getattr(args, "inputs", None) or []:
        recogniser.add(argument)
    return recogniser.finish()
