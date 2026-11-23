"""Model classes for ERI Importer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

import dateutil.parser as date_parser

if TYPE_CHECKING:
    from collections.abc import Callable
    import datetime as dt
    from pathlib import Path

    from pdfplumber.page import Page

    from cgt_calc.parsers.eri.model import ERITransaction


@dataclass
class ERIImporterOutput:
    """Output of an ERI Importer."""

    transactions: list[ERITransaction]
    output_file_name: str


class ERIImporter(ABC):
    """Base class for all ERI importers."""

    def __init__(self, name: str):
        """Create a new instance with the given name."""
        self.name = name

    @abstractmethod
    def parse(self, file: Path) -> ERIImporterOutput | None:
        """Parse the input file.

        Return None when the file is not accepted by the parser.
        """


def extract_report_end_date(
    page: Page,
    prefix: str,
    normalize: Callable[[str], str] | None = None,
) -> dt.date | None:
    """Read the reporting period end date printed after `prefix` on `page`.

    An ERI report states this date as some wording followed by the date itself;
    `prefix` is that wording, matched without regard to case, and the text after
    it on the same line is read as the date. `normalize`, when given, tidies that
    text for a report whose wording needs it (a misspelling, or a few stray
    characters left by the prefix).
    """
    matches = [
        line["text"]
        for line in page.extract_text_lines()
        if prefix.lower() in line["text"].lower()
    ]
    if not matches:
        return None
    date_str = matches[0][len(prefix) :].strip()
    if normalize is not None:
        date_str = normalize(date_str)
    try:
        date = date_parser.parse(date_str, fuzzy=True, dayfirst=True)
    except date_parser.ParserError:
        return None
    return date.date()
