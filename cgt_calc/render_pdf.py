"""Render the PDF report with Typst."""

from __future__ import annotations

from importlib import resources
import logging
import sys
from typing import TYPE_CHECKING, Final
import unicodedata

from colorama import Fore
import jinja2
import typst

from .const import PACKAGE_NAME, TYPST_TEMPLATE_RESOURCE
from .exceptions import CgtError, saving
from .logging import style_text
from .report_view import build_report_view
from .resources import RESOURCES_PACKAGE
from .util import strip_zeros

if TYPE_CHECKING:
    from pathlib import Path

    from .model import CapitalGainsReport

LOGGER = logging.getLogger(__name__)

# The characters the report prints, each checked to render and extract as
# itself with the fonts Typst embeds (tests/general/test_report_rendering.py
# renders them all). The binding gives no sign of a glyph a font lacks, so
# anything else is refused, even where a font might draw it.
_VERIFIED_RANGES: Final = (
    (0x0020, 0x007E),  # Basic Latin
    (0x00A0, 0x02FF),  # Latin-1, Latin Extended-A and -B, IPA, modifier letters
    (0x0370, 0x03FF),  # Greek
    (0x0400, 0x04FF),  # Cyrillic
    (0x1E00, 0x1EFF),  # Latin Extended Additional
    (0x2010, 0x2027),  # dashes, quotation marks, bullets, ellipsis
    (0x2030, 0x203A),  # per mille, primes, single guillemets
    (0x20A0, 0x20C0),  # currency signs
)
# Symbols outside those ranges that the LaTeX report printed.
_VERIFIED_SYMBOLS: Final = frozenset(
    (
        0x0E3F,  # baht sign
        0x203B,  # reference mark
        0x203D,  # interrobang
        0x2044,  # fraction slash
        0x204E,  # low asterisk
        0x2052,  # commercial minus sign
        0x2103,  # degree Celsius
        0x2116,  # numero sign
        0x2117,  # sound recording copyright
        0x211E,  # prescription take
        0x2120,  # service mark
        0x2122,  # trade mark sign
        0x2127,  # inverted ohm sign
        0x212E,  # estimated symbol
        0x2190,  # leftwards arrow
        0x2191,  # upwards arrow
        0x2192,  # rightwards arrow
        0x2193,  # downwards arrow
        0x2422,  # blank symbol
        0x2423,  # open box
        0x25E6,  # white bullet
        0x25EF,  # large circle
        0x266A,  # eighth note
        0x27E8,  # mathematical left angle bracket
        0x27E9,  # mathematical right angle bracket
        *range(0xFB00, 0xFB07),  # the seven Latin ligatures
    )
)


def _is_verified(char: str) -> bool:
    """Say whether the report can print `char`, a character of a printed value."""
    if not char.isprintable() or unicodedata.category(char) in {"Mn", "Me"}:
        return False
    code = ord(char)
    return code in _VERIFIED_SYMBOLS or any(
        low <= code <= high for low, high in _VERIFIED_RANGES
    )


def _typst_text(value: object) -> str:
    """Print a value the template prints, whatever its type, as Typst text.

    The value is composed (NFC) and put on one line, each run of whitespace
    becoming one space, so a tab or line break in a name reads as the space
    LaTeX printed. It is written as a Typst string literal, whose content is
    never read as markup: "*", "#", "$", "@", "//" or "--" in a broker name
    prints as itself. The closing semicolon ends the expression, so what the
    template puts after the value is text: without it Typst would swallow a
    following ";" and read "." with a letter, "(" or "[" as code.
    """
    text = str(value)
    shown = " ".join(unicodedata.normalize("NFC", text).split())
    rejected = dict.fromkeys(char for char in shown if not _is_verified(char))
    if rejected:
        codes = ", ".join(f"U+{ord(char):04X}" for char in rejected)
        if unicodedata.normalize("NFC", text) != text:
            codes += ", after Unicode normalisation"
        raise CgtError(
            f"Cannot create the PDF report: the name '{text}' contains characters "
            f"outside the report's verified character set ({codes}). The figures "
            "printed above are complete. To get a PDF, write this holding's name "
            "using Latin, Greek or Cyrillic letters, digits and common punctuation "
            "in your input file, or pass --no-report to skip the PDF."
        )
    return '#"' + shown.replace("\\", "\\\\").replace('"', '\\"') + '";'


def render_pdf(report: CapitalGainsReport, output_path: Path) -> None:
    """Render the report as a PDF named after `output_path`'s stem."""
    # The PDF takes the output's stem whatever its suffix, as it did when
    # pdflatex named it.
    pdf_path = output_path.parent / f"{output_path.stem}.pdf"
    LOGGER.info(
        "\n%s\n",
        style_text(
            f"Writing PDF report to {pdf_path}...",
            colour=Fore.CYAN,
            emoji="💾",
            stream=sys.stderr,
        ),
    )
    environment = jinja2.Environment(
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
        # Applied to every value the template prints, so a line added to it
        # later is covered too.
        finalize=_typst_text,
        loader=jinja2.PackageLoader(PACKAGE_NAME, "resources"),
    )
    environment.filters["money"] = "{:,}".format
    markup = environment.get_template(TYPST_TEMPLATE_RESOURCE).render(
        view=build_report_view(report),
        strip_zeros=strip_zeros,
    )
    # A str input would be read as a file path. Fonts come only from the
    # binding, so the PDF is the same whatever the machine has installed, and
    # the root is fixed so the working directory plays no part.
    with resources.as_file(resources.files(RESOURCES_PACKAGE)) as root:
        pdf = typst.Compiler(root=root, ignore_system_fonts=True).compile(
            input=markup.encode("utf-8"), format="pdf"
        )
    assert isinstance(pdf, bytes)

    with saving("the report", pdf_path, "To save it somewhere else, pass --output."):
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(pdf)
