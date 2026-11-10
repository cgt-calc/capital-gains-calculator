"""Render the PDF report with Typst."""

from __future__ import annotations

from importlib import resources
import json
import logging
import sys
from typing import TYPE_CHECKING, Final
import unicodedata

from colorama import Fore
import typst

from .const import TYPST_TEMPLATE_RESOURCE
from .exceptions import CgtError, saving
from .logging import style_text
from .report_view import build_report_view
from .resources import RESOURCES_PACKAGE
from .util import strip_zeros

if TYPE_CHECKING:
    import datetime as dt
    from decimal import Decimal
    from pathlib import Path

    from .model import CapitalGainsReport
    from .report_view import (
        EventView,
        IncomeEventView,
        IncomeSummaryView,
        LineView,
        ReportView,
        SummaryView,
    )

LOGGER = logging.getLogger(__name__)

# The characters the report prints, each checked to render and extract as
# itself with the bundled Noto Sans and the fonts Typst embeds for what it
# lacks (tests/general/test_report_rendering.py renders them all). The binding
# gives no sign of a glyph a font lacks, so anything else is refused, even
# where a font might draw it.
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


def _shown(text: str) -> str:
    """Return a string the template prints, as the report shows it.

    The string is composed (NFC) and put on one line, each run of whitespace
    becoming one space, so a tab or line break in a name reads as the space
    LaTeX printed.
    """
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
    return shown


def _all_shown(data: object) -> object:
    """Apply `_shown` to every string in the report's data, however deep.

    Applied to the whole tree, so a value added to the data later is covered
    too.
    """
    if isinstance(data, str):
        return _shown(data)
    if isinstance(data, dict):
        return {key: _all_shown(value) for key, value in data.items()}
    if isinstance(data, list | tuple):
        return [_all_shown(item) for item in data]
    return data


def _money(value: Decimal | None) -> str | None:
    """Print pounds as the view rounded them, with thousands separators."""
    return None if value is None else f"{value:,}"


def _loss(gain: Decimal | None) -> str | None:
    """Print a loss, given as a negative gain, as the pounds lost."""
    return None if gain is None else _money(-gain)


def _short_date(value: dt.date | None) -> str | None:
    """Print a date within a line, such as "03 Jun 2024"."""
    return None if value is None else value.strftime("%d %b %Y")


def _line_data(line: LineView) -> dict[str, object]:
    """Give the figures of one matching rule, as the template prints them."""
    return {
        "rule": line.rule,
        "quantity": strip_zeros(line.quantity),
        "new_quantity": strip_zeros(line.new_quantity),
        "has_pool_units": line.new_quantity > 0,
        "is_gain": line.is_gain,
        "proceeds": _money(line.proceeds),
        "cost": _money(line.cost),
        "gain": _money(line.gain),
        "loss": _loss(line.gain),
        "bnb_date": _short_date(line.bnb_date),
        "amount": _money(line.amount),
        "new_pool_cost": _money(line.new_pool_cost),
        "pool_per_unit": _money(line.pool_per_unit),
        "spin_off_source": line.spin_off_source,
        "eris": [
            {
                "cost": _money(eri.cost),
                "unit_price": _money(eri.unit_price),
                "date": _short_date(eri.date),
            }
            for eri in line.eris
        ],
    }


def _event_data(event: EventView) -> dict[str, object]:
    """Give one capital gains event, as the template prints it.

    The template's tests of a figure (`has_fees`, `has_units` and the like)
    are answered here, from the exact Decimal rather than the printed text.
    """
    split = event.split
    pool_quantity = event.pool_quantity
    return {
        "kind": event.kind,
        "symbol": event.symbol,
        "renamed_to": event.renamed_to,
        "spin_off_source": event.spin_off_source,
        "spin_off_dest": event.spin_off_dest,
        "number": event.number,
        "is_disposal": event.is_disposal,
        "is_acquisition": event.is_acquisition,
        "is_gift": event.is_gift,
        "pool_units_left": event.pool_units_left,
        "quantity": strip_zeros(event.quantity),
        "has_units": event.quantity > 0,
        "is_one_unit": event.quantity == 1,
        "fees": _money(event.fees),
        "has_fees": event.fees > 0,
        "proceeds": _money(event.proceeds),
        "per_unit": _money(event.per_unit),
        "cost": _money(event.cost),
        "outcome": event.outcome,
        "gain": _money(event.gain),
        "loss": _loss(event.gain),
        "gain_to_date": _money(event.gain_to_date),
        "loss_to_date": _money(event.loss_to_date),
        "gift_loss_to_date": _money(event.gift_loss_to_date),
        "base_cost_passed": _money(event.base_cost_passed),
        "eri_unit_price": _money(event.eri_unit_price),
        "split": None
        if split is None
        else {
            "ratio_description": split.ratio_description,
            "day_open_quantity": strip_zeros(split.day_open_quantity),
            "scaled_day_open_quantity": strip_zeros(split.scaled_day_open_quantity),
            "broker_before_quantity": strip_zeros(split.broker_before_quantity),
            "broker_after_quantity": strip_zeros(split.broker_after_quantity),
            "reconciliation_delta": strip_zeros(split.reconciliation_delta),
            "is_reconciled": split.reconciliation_delta != 0,
            "quantity_before_reconciliation": strip_zeros(
                split.quantity_before_reconciliation
            ),
            "quantity_after_reconciliation": strip_zeros(
                split.quantity_after_reconciliation
            ),
        },
        "pool_quantity": None if pool_quantity is None else strip_zeros(pool_quantity),
        "pool_has_units": pool_quantity is not None and pool_quantity > 0,
        "pool_cost": _money(event.pool_cost),
        "pool_per_unit": _money(event.pool_per_unit),
        "lines": [_line_data(line) for line in event.lines],
    }


def _income_event_data(event: IncomeEventView) -> dict[str, object]:
    """Give one dividend, interest or distribution, as the template prints it."""
    return {
        "kind": event.kind,
        "number": event.number,
        "origin": event.origin,
        "symbol": event.symbol,
        "is_interest": event.is_interest,
        "amount": _money(event.amount),
        "tax_at_source": _money(event.tax_at_source),
        "treaty_country": event.treaty_country,
        "treaty_amount": _money(event.treaty_amount),
        "treaty_rate": None if event.treaty_rate is None else str(event.treaty_rate),
        "unit_price": _money(event.unit_price),
    }


def _summary_data(summary: SummaryView) -> dict[str, object]:
    """Give the capital gains totals, as the template prints them."""
    return {
        "acquisition_count": summary.acquisition_count,
        "disposal_count": summary.disposal_count,
        "disposal_proceeds": _money(summary.disposal_proceeds),
        "allowable_costs": _money(summary.allowable_costs),
        "total_gain": _money(summary.total_gain),
        "total_loss": _money(summary.total_loss),
        "gift_loss": _money(summary.gift_loss),
        "has_gift_loss": summary.gift_loss > 0,
        "exempt_disposal_count": summary.exempt_disposal_count,
        "exempt_disposal_proceeds": _money(summary.exempt_disposal_proceeds),
        "net_gain": _money(summary.net_gain),
    }


def _income_summary_data(summary: IncomeSummaryView) -> dict[str, object]:
    """Give the dividend and interest totals, as the template prints them."""
    return {
        "dividend_count": summary.dividend_count,
        "interest_count": summary.interest_count,
        "interest_tax_count": summary.interest_tax_count,
        "eri_count": summary.eri_count,
        "total_dividends": _money(summary.total_dividends),
        "eri_dividends": _money(summary.eri_dividends),
        "treaty_allowance": _money(summary.treaty_allowance),
        "dividend_allowance": _money(summary.dividend_allowance),
        "taxable_dividends": _money(summary.taxable_dividends),
        "explains_dividend_tax_credit": summary.explains_dividend_tax_credit,
        "uk_interest": _money(summary.uk_interest),
        "foreign_interest": _money(summary.foreign_interest),
        "eri_interest": _money(summary.eri_interest),
        "interest_tax": _money(summary.interest_tax),
    }


def _report_data(view: ReportView) -> str:
    """Write the report as JSON for the template, each value as it prints.

    Counts and event numbers stay integers, which Typst prints as Python
    does. Every string is then checked by `_shown`. The template inserts each
    value as text, never as markup: "*", "#", "$", "@", "//" or "--" in a
    broker name prints as itself.
    """
    return json.dumps(
        _all_shown(
            {
                "title_period": view.title_period,
                "summary": _summary_data(view.summary),
                "income_summary": _income_summary_data(view.income_summary),
                "days": [
                    {
                        "date": day.date.strftime("%d %B %Y"),
                        "events": [_event_data(event) for event in day.events],
                    }
                    for day in view.days
                ],
                "income_days": [
                    {
                        "date": day.date.strftime("%d %B %Y"),
                        "events": [_income_event_data(event) for event in day.events],
                    }
                    for day in view.income_days
                ],
            }
        )
    )


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
    data = _report_data(build_report_view(report))
    # Fonts come only from the package and the binding, so the PDF is the
    # same whatever the machine has installed, and the root is fixed so the
    # working directory plays no part.
    with resources.as_file(resources.files(RESOURCES_PACKAGE)) as root:
        pdf = typst.Compiler(
            root=root,
            font_paths=[root / "fonts"],
            ignore_system_fonts=True,
            sys_inputs={"view": data},
        ).compile(input=root / TYPST_TEMPLATE_RESOURCE, format="pdf")
    assert isinstance(pdf, bytes)

    with saving("the report", pdf_path, "To save it somewhere else, pass --output."):
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(pdf)
