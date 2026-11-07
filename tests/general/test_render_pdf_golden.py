"""The text of the PDF report is pinned exactly.

`test_run_with_example_files` pins the text of the example run's PDF. Every
branch of `template.typ.j2` that run does not reach is rendered here from a
small scenario, and the PDF's text, as pdfplumber extracts it, is compared
with a golden file under `data/report_text/`. Together they are the standard
for template changes: one that is not a deliberate wording change must leave
every golden untouched.

The goldens pin words, line breaks and each page's text, not vertical space,
so a lost paragraph break that leaves the line breaks alone needs its own
test (`test_report_rendering.py`).

Regenerate the goldens on purpose, after checking the diff is the one
intended:

    uv run python -m tests.general.test_render_pdf_golden
"""

from __future__ import annotations

import datetime as dt
from fractions import Fraction
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING

import pytest

from cgt_calc.model import ActionType, Isin
from cgt_calc.render_pdf import render_pdf
from tests.schwab.test_schwab_options import _report as schwab_report
from tests.utils import pdf_text

from .calc_test_data import GBP, transaction
from .test_calc import (
    RENAME_DAY,
    _gbp_trade,
    _rename_transaction,
    create_calculator,
    get_report,
)
from .test_spin_off_basis import BUY_DAY, FLAT, SPIN_OFF_DAY, spin_off
from .test_stock_splits import EVENT_DAY, POOL_DAY, paired_split, trade

if TYPE_CHECKING:
    from collections.abc import Callable

    from cgt_calc.model import CapitalGainsReport

GOLDEN_DIR = Path(__file__).parent / "data" / "report_text"
REGENERATE = "uv run python -m tests.general.test_render_pdf_golden"

JUNE_1 = dt.date(2024, 6, 1)
JUNE_3 = dt.date(2024, 6, 3)
JUNE_10 = dt.date(2024, 6, 10)
JUNE_11 = dt.date(2024, 6, 11)
JUNE_12 = dt.date(2024, 6, 12)
JUNE_13 = dt.date(2024, 6, 13)
JULY_2 = dt.date(2024, 7, 2)
JULY_3 = dt.date(2024, 7, 3)
US_ISIN = Isin("US9220427424")


def _gifts() -> CapitalGainsReport:
    """Give shares away at a gain, at a clogged loss, at a loss to anyone else, and at cost."""
    return get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(JUNE_1, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(JUNE_10, ActionType.GIFT, "FOO", 2, 30, 0, None, GBP),
            transaction(JUNE_11, ActionType.GIFT, "FOO", 2, 5, 0, None, GBP),
            transaction(
                JUNE_12, ActionType.GIFT_UNCONNECTED, "FOO", 2, 5, 0, None, GBP
            ),
            transaction(JUNE_13, ActionType.GIFT, "FOO", 2, 10, 0, None, GBP),
        ],
    )


def _exempt() -> CapitalGainsReport:
    """Dispose of a CGT-exempt instrument at a gain, at cost, and at a loss."""
    return get_report(
        create_calculator(
            tax_year=2024, balance_check=False, cgt_exempt_tickers=["T26"]
        ),
        [
            transaction(
                dt.date(2024, 5, 1),
                ActionType.BUY,
                "T26",
                100,
                100,
                0,
                -10000,
                GBP,
            ),
            transaction(
                dt.date(2024, 9, 1), ActionType.SELL, "T26", 50, 120, 0, 6000, GBP
            ),
            transaction(
                dt.date(2024, 9, 2), ActionType.SELL, "T26", 25, 100, 0, 2500, GBP
            ),
            transaction(
                dt.date(2024, 9, 3), ActionType.SELL, "T26", 25, 80, 0, 2000, GBP
            ),
        ],
    )


def _option_grant() -> CapitalGainsReport:
    """Write two option contracts that expire, as Schwab exports them."""
    return schwab_report(
        [
            "05/17/2024,Expired,META 05/17/2024 500.00 C,Expiration,,2,,",
            "05/01/2024,Sell to Open,META 05/17/2024 500.00 C,Open,$1.00,2,$1.30,$198.70",
        ]
    )


def _split() -> CapitalGainsReport:
    """Restate a holding through a reorganisation the export gave as two share counts."""
    return get_report(
        create_calculator(tax_year=2023, balance_check=False),
        [
            trade(POOL_DAY, ActionType.BUY, "FOO", "0.9", "100"),
            trade(EVENT_DAY, ActionType.BUY, "FOO", "0.1", "100"),
            paired_split(
                EVENT_DAY, "FOO", "1.0000000000", "0.1111111100", Fraction(1, 9)
            ),
        ],
    )


def _rename() -> CapitalGainsReport:
    """Rename a holding on the day it is sold, matched to a purchase under the new name."""
    return get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            _gbp_trade(dt.date(2024, 5, 1), ActionType.BUY, "OLD", 100, 1000),
            _gbp_trade(RENAME_DAY, ActionType.SELL, "OLD", 100, 800),
            _rename_transaction(RENAME_DAY, "OLD", "NEW"),
            _gbp_trade(dt.date(2024, 5, 20), ActionType.BUY, "NEW", 100, 900),
        ],
    )


def _spouse_transfer() -> CapitalGainsReport:
    """Pass part of a holding to a spouse at no gain and no loss."""
    return get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(JUNE_1, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(
                JUNE_10, ActionType.TRANSFER_TO_SPOUSE, "FOO", 4, 25, 0, 0, GBP
            ),
        ],
    )


def _spin_off() -> CapitalGainsReport:
    """Spin a tenth of a holding off into a new one."""
    _, report = spin_off(
        [transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP)],
        10,
        {BUY_DAY: {GBP: FLAT}, SPIN_OFF_DAY: {GBP: FLAT}},
    )
    return report


def _fee_and_income() -> CapitalGainsReport:
    """Add a cost with no new shares, pay UK interest and its tax, and a dividend with treaty relief."""
    return get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(JUNE_1, ActionType.BUY, "FOO", 10, 10, 0, -100, GBP),
            transaction(JUNE_3, ActionType.FEE, "FOO", None, None, 0, -5, GBP),
            transaction(
                JUNE_3,
                ActionType.DIVIDEND,
                "BAR",
                None,
                None,
                0,
                100,
                GBP,
                isin=US_ISIN,
            ),
            transaction(
                JUNE_3,
                ActionType.DIVIDEND_TAX,
                "BAR",
                None,
                None,
                0,
                -15,
                GBP,
                isin=US_ISIN,
            ),
            transaction(JULY_2, ActionType.INTEREST, None, None, None, 0, 3, GBP),
            transaction(JULY_3, ActionType.INTEREST_TAX, None, None, None, 0, -2, GBP),
        ],
    )


def _dividend_before_the_allowance() -> CapitalGainsReport:
    """Receive a dividend with treaty relief in 2012/13, before the dividend allowance."""
    day = dt.date(2012, 6, 1)
    return get_report(
        create_calculator(tax_year=2012, balance_check=False),
        [
            transaction(
                day, ActionType.DIVIDEND, "BAR", None, None, 0, 100, GBP, isin=US_ISIN
            ),
            transaction(
                day,
                ActionType.DIVIDEND_TAX,
                "BAR",
                None,
                None,
                0,
                -15,
                GBP,
                isin=US_ISIN,
            ),
        ],
    )


# Each scenario with the template branches it is there to pin: a phrase per
# branch, so a scenario that stops reaching one fails loudly rather than
# pinning a page that no longer says it.
SCENARIOS: dict[str, tuple[Callable[[], CapitalGainsReport], list[str]]] = {
    "gifts": (
        _gifts,
        [
            "given away at a market value of",
            "before any relief",
            "Clogged loss",
            "Losses on gifts to date",
            "Chargeable loss",
            "Neither a gain nor a loss.",
            "Losses on gifts, kept separate",
        ],
    ),
    "exempt": (
        _exempt,
        [
            "Exempt disposal",
            "disregarded gain",
            "Neither a gain nor a loss; the gain or loss is disregarded",
            "disregarded loss",
            "Number of exempt disposals",
        ],
    ),
    "option_grant": (
        _option_grant,
        ["grant of 2", "contracts of the", "does not change the Section 104 holding"],
    ),
    "split": (
        _split,
        ["Share reorganisation", "Reconciled to that count"],
    ),
    "rename": (
        _rename,
        ["was renamed to", "BED AND BREAKFAST"],
    ),
    "spouse_transfer": (
        _spouse_transfer,
        ["Transferred to spouse", "passes to the recipient"],
    ),
    "spin_off": (
        _spin_off,
        ["Spin-off adjustment", "Cost basis calculated based on cost of"],
    ),
    "fee_and_income": (
        _fee_and_income,
        [
            "Cost added to FOO with no new shares",
            "Interest UK 1",
            "Interest tax UK 1",
            "tax treaty amount",
        ],
    ),
    "dividend_before_the_allowance": (
        _dividend_before_the_allowance,
        ["carried a tax credit"],
    ),
}


def _text(report: CapitalGainsReport, directory: Path) -> str:
    """Render the report to a PDF and return its text."""
    render_pdf(report, directory / "report.pdf")
    return pdf_text(directory / "report.pdf")


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_report_text_matches_golden(name: str, tmp_path: Path) -> None:
    """The PDF's text is identical to the golden file."""
    build, phrases = SCENARIOS[name]
    text = _text(build(), tmp_path)
    flat = " ".join(text.split())
    for phrase in phrases:
        assert phrase in flat, f"{name} no longer reaches the branch saying {phrase!r}"
    golden = GOLDEN_DIR / f"{name}.txt"
    assert text == golden.read_text(encoding="utf-8"), (
        f"{golden} differs from the PDF's text; if the change is intended, "
        f"regenerate with: {REGENERATE}"
    )


if __name__ == "__main__":
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        for scenario, (builder, _) in SCENARIOS.items():
            (GOLDEN_DIR / f"{scenario}.txt").write_text(
                _text(builder(), Path(scratch)), encoding="utf-8"
            )
            print(f"wrote {scenario}.txt")
