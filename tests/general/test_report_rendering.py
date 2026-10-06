"""The rendered report shows each disposal's figures on one basis.

Every figure in the PDF is the exact amount rounded to the penny on its own,
so a line can be out by a penny against its own components; that bound is
arithmetic, not a defect. What must not happen is a line out by the fee: the
proceeds shown net of the sale fee beside an allowable cost that already
includes it. The figures are parsed back out of the LaTeX source, so the
check covers what the reader sees rather than the values behind it.

A name from the user's files is printed as text: the characters LaTeX acts on
are escaped, in the source and, where pdflatex is available, in a compiled PDF.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import os
import re
from typing import TYPE_CHECKING

import pytest

from cgt_calc.model import ActionType
from cgt_calc.render_latex import render_pdf

from .calc_test_data import GBP, transaction
from .test_calc import create_calculator, get_report

if TYPE_CHECKING:
    from pathlib import Path

PENNY = Decimal("0.01")

POOL_DAY = datetime.date(2024, 6, 1)
SPLIT_DAY = datetime.date(2024, 6, 10)
BAR_BUY_DAY = datetime.date(2024, 7, 1)
BAR_SALE_DAY = datetime.date(2024, 8, 1)

# FOO: a pool of 100, then on one day a purchase of 20 and a sale of 120, so the
# sale is matched same day for 20 and against the pool for 100 and the sale fee
# is apportioned between the two lines. BAR: one purchase sold at a loss, one
# matching line, so only the heading carries its proceeds. A fee on every leg.
TRANSACTIONS = [
    transaction(POOL_DAY, ActionType.BUY, "FOO", 100, 10, 1, -1001, GBP),
    transaction(SPLIT_DAY, ActionType.BUY, "FOO", 20, 12, 1, -241, GBP),
    transaction(SPLIT_DAY, ActionType.SELL, "FOO", 120, 13, 1, 1559, GBP),
    transaction(BAR_BUY_DAY, ActionType.BUY, "BAR", 50, 20, 1, -1001, GBP),
    transaction(BAR_SALE_DAY, ActionType.SELL, "BAR", 50, 18, 1, 899, GBP),
]

HEADING = re.compile(r"^Disposal \d+: \S+ units of (\w+) for £([\d,.]+)")
ENTRY = re.compile(
    r"\\item \\textbf\{[A-Z0-9 ]+\}\. Quantity: [\d.]+, "
    r"(?:disposal proceeds: £([\d,.]+), )?"
    r"allowable cost(?: on [^:]+)?: £([\d,.]+), (gain|loss): £([\d,.]+)\."
)
TOTAL = re.compile(r"Total disposal proceeds: £([\d,.]+)")


def money(text: str) -> Decimal:
    """Parse a figure as the report prints it."""
    return Decimal(text.replace(",", ""))


def render(tmp_path: Path) -> str:
    """Render the scenario to LaTeX source, flattened to one line per block."""
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False), TRANSACTIONS
    )
    render_pdf(report, tmp_path / "report.pdf", skip_pdflatex=True)
    source = (tmp_path / "report.tex").read_text(encoding="utf-8")
    # Line breaks and \mbox{} only shape the typesetting.
    source = re.sub(r"\\mbox\{([^}]*)\}", r"\1", source)
    return re.sub(r"\s+", " ", source)


def disposals(
    source: str,
) -> list[tuple[str, Decimal, list[tuple[Decimal | None, Decimal, Decimal]]]]:
    """Each disposal's symbol and proceeds with its (proceeds, cost, gain) lines."""
    found = []
    for block in source.split("\\subsubsection*{ ")[1:]:
        heading = HEADING.match(block)
        if heading is None:
            continue
        lines = [
            (
                money(proceeds) if proceeds else None,
                money(cost),
                money(gain) if sign == "gain" else -money(gain),
            )
            for proceeds, cost, sign, gain in ENTRY.findall(block)
        ]
        found.append((heading[1], money(heading[2]), lines))
    return found


def test_proceeds_are_shown_gross(tmp_path: Path) -> None:
    """The heading shows what the shares sold for; the fee is in the cost."""
    by_symbol = {
        symbol: proceeds for symbol, proceeds, _ in disposals(render(tmp_path))
    }
    assert by_symbol == {"FOO": Decimal("1560.00"), "BAR": Decimal("900.00")}


def test_each_line_reconciles_to_the_penny(tmp_path: Path) -> None:
    """Proceeds less allowable cost is the gain on every line, give or take a penny."""
    found = disposals(render(tmp_path))
    assert [len(lines) for _, _, lines in found] == [2, 1]
    for _, heading_proceeds, lines in found:
        for proceeds, cost, gain in lines:
            shown = heading_proceeds if proceeds is None else proceeds
            assert abs(shown - cost - gain) <= PENNY, (shown, cost, gain)


def test_lines_add_up_to_their_heading_and_the_total(tmp_path: Path) -> None:
    """Each rounded figure may be half a penny out, so sums drift one penny per term."""
    source = render(tmp_path)
    found = disposals(source)
    for _, heading_proceeds, lines in found:
        if len(lines) > 1:
            parts = [proceeds for proceeds, _, _ in lines if proceeds is not None]
            assert len(parts) == len(lines)
            assert abs(sum(parts) - heading_proceeds) <= PENNY * len(parts)
    total = TOTAL.search(source)
    assert total is not None
    headings = [proceeds for _, proceeds, _ in found]
    assert abs(sum(headings) - money(total[1])) <= PENNY * len(headings)


@pytest.mark.parametrize(
    ("character", "escaped"),
    [
        ("%", r"\%"),
        ("&", r"\&"),
        ("_", r"\_"),
        ("#", r"\#"),
        ("$", r"\$"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("^", r"\textasciicircum{}"),
        ("~", r"\textasciitilde{}"),
        ("\\", r"\textbackslash{}"),
        ("<", r"\textless{}"),
        (">", r"\textgreater{}"),
        ("|", r"\textbar{}"),
    ],
)
def test_a_holding_s_name_is_escaped_for_latex(
    tmp_path: Path, character: str, escaped: str
) -> None:
    """A character LaTeX acts on is printed as text, and the line keeps its cost.

    Left as it is, `%` starts a comment that drops the rest of the line, cost
    included, and most of the others stop pdflatex.
    """
    name = f"A{character}B"
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(POOL_DAY, ActionType.BUY, name, 10, 100, 0, -1000, GBP),
            transaction(BAR_SALE_DAY, ActionType.SELL, name, 10, 120, 0, 1200, GBP),
            # A line from the income half of the report, read by the last check.
            transaction(BAR_SALE_DAY, ActionType.DIVIDEND, name, None, None, 0, 5, GBP),
        ],
    )

    render_pdf(report, tmp_path / "report.pdf", skip_pdflatex=True)
    source = (tmp_path / "report.tex").read_text(encoding="utf-8")

    assert f"units of A{escaped}B for £1,000.00" in source
    assert name not in source


@pytest.mark.skipif(not os.getenv("ENABLE_PDFLATEX"), reason="needs pdflatex")
def test_a_name_with_every_escaped_character_compiles(tmp_path: Path) -> None:
    """The escaped spellings compile with pdflatex, in a heading and in running text.

    The name starts with an ampersand that stops pdflatex when it is not
    escaped. Led by a backslash it would compile without the fix: the percent
    sign would hide the rest.
    """
    name = "S&P 80% Fund_A #1 {x} ~ ^ < > | \\ $"
    report = get_report(
        create_calculator(tax_year=2024, balance_check=False),
        [
            transaction(POOL_DAY, ActionType.BUY, name, 10, 100, 0, -1000, GBP),
            transaction(BAR_SALE_DAY, ActionType.SELL, name, 10, 120, 0, 1200, GBP),
            transaction(BAR_SALE_DAY, ActionType.DIVIDEND, name, None, None, 0, 5, GBP),
        ],
    )

    render_pdf(report, tmp_path / "report.pdf")

    assert (tmp_path / "report.pdf").stat().st_size > 0
