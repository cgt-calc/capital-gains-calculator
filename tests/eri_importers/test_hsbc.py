"""Tests for the HSBC ETFs plc ERI importer.

Each test draws a synthetic report laid out like HSBC's: a cover page, the
table of excess reported income, a table of distributions and a notes page.
The tables have no ruled lines; every heading and value is centred on its
column, and fund names start at the left margin.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.eri.importer.hsbc import HsbcImporter
from tests.eri_importers.helpers import check_transaction

if TYPE_CHECKING:
    from pathlib import Path

REPORT_NAME = "reportable income 2025-gb-en-hetf.pdf"

FONT = "Helvetica"
SIZE = 7
PAGE_SIZE = (792, 612)
LEFT = 40
# The baseline of the line holding "Sub fund" and the last line of each heading.
HEADING_BASELINE = 480
LINE = 9

# Each column's heading, top line first, and the centre it shares with its
# values: the columns HSBC prints from its 2018 report on.
COLUMNS: list[tuple[list[str], float]] = [
    (["ISIN"], 250),
    (["HMRC", "Reference"], 320),
    (["Reporting", "period end date"], 390),
    (["Currency"], 450),
    (["Excess reported", "income per", "share"], 515),
    (["Fund", "distribution", "date"], 585),
    (["Reporting fund", "at date report", "made available"], 655),
    (["The fund meets", "the definition of", "a bond fund for", "the period"], 730),
]

# The 2015 to 2017 reports add the period's start date, and the 2015 one ends
# with the total dividend instead of the bond fund test.
COLUMNS_2015: list[tuple[list[str], float]] = [
    (["ISIN"], 250),
    (["HMRC", "Reference"], 310),
    (["Reporting", "period start date"], 370),
    (["Reporting", "period end date"], 430),
    (["Currency"], 485),
    (["Excess reported", "income per", "share"], 540),
    (["Fund", "distribution date"], 600),
    (["Reporting fund", "at date report", "made available"], 665),
    (["Total dividend", "per share for", "the reporting", "period"], 735),
]

DISTRIBUTION_COLUMNS: list[tuple[list[str], float]] = [
    (["ISIN"], 250),
    (["HMRC", "Reference"], 320),
    (["Distribution", "per share"], 400),
    (["Date of", "distribution"], 470),
]

# Valid check digits; IE00000000A3 differs from the first only in its check digit.
ISIN_A, ISIN_B, ISIN_C, ISIN_D, ISIN_E = (
    "IE00000000A2",
    "IE00000000B0",
    "IE00000000C8",
    "IE00000000D6",
    "IE00000000E4",
)

# A row is the fund name, then one value per column ("" leaves the cell empty).
Row = tuple[str, list[str]]


def _row(isin: str, end: str, currency: str, amount: str) -> Row:
    """Return a row of the current layout."""
    return (
        "HSBC Example UCITS ETF",
        [isin, "H0011-0001", end, currency, amount, "30.06.2026", "Yes", "No"],
    )


def _draw_table(
    canvas: Canvas,
    columns: list[tuple[list[str], float]],
    rows: list[Row],
    sub_fund_heading: str = "Sub fund",
) -> None:
    """Draw one table page, its headings bottom-aligned on the "Sub fund" line."""
    canvas.setFont(FONT, SIZE)
    canvas.drawString(LEFT, 560, "HSBC ETF plc")
    canvas.drawString(LEFT, 550, "UK reporting fund status report to investors")
    canvas.drawString(LEFT, 540, "Reporting Period End 31 December 2025")
    canvas.drawString(LEFT, HEADING_BASELINE, sub_fund_heading)
    for heading, centre in columns:
        for above, text in enumerate(reversed(heading)):
            canvas.drawCentredString(centre, HEADING_BASELINE + above * LINE, text)
    for number, (name, values) in enumerate(rows, 1):
        baseline = HEADING_BASELINE - number * LINE
        canvas.drawString(LEFT, baseline, name)
        for (_, centre), value in zip(columns, values, strict=True):
            if value:
                canvas.drawCentredString(centre, baseline, value)
    # The page number under the table is not a row.
    canvas.drawString(PAGE_SIZE[0] / 2, 40, "2")
    canvas.showPage()


def _draw_overflowing_rows(canvas: Canvas) -> None:
    """Draw three rows whose ISIN shares its column with other text.

    The name is drawn before the ISIN, as HSBC's reports draw them.
    """
    canvas.setFont(FONT, SIZE)
    isin_centre = COLUMNS[0][1]

    def isin_left(isin: str) -> float:
        return isin_centre - stringWidth(isin, FONT, SIZE) / 2

    rows: list[tuple[str, str, float, str, str]] = [
        # The name ends where the ISIN starts, so the two read as one word,
        # "...ETFIE00000000C8" (HSBC's 2024 and 2025 reports).
        (ISIN_C, "HSBC Joined Name UCITS ETF", 0, "EUR", "1.8063"),
        # The name runs on over the ISIN, so its last word "ETF" shares the
        # column with it and their letters overlap (2020 and 2024).
        (ISIN_D, "HSBC Overlapping Name UCITS ETF", 25, "CHF", "0.0000"),
        # The HMRC reference follows the ISIN 2 points after it (2023): it is
        # drawn below in place of the centred one.
        (ISIN_E, "HSBC Close Reference UCITS ETF", -60, "HKD", "3.2500"),
    ]
    for number, (isin, name, into_isin, currency, amount) in enumerate(rows, 3):
        baseline = HEADING_BASELINE - number * LINE
        name_end = isin_left(isin) + into_isin
        canvas.drawString(name_end - stringWidth(name, FONT, SIZE), baseline, name)
        canvas.drawCentredString(isin_centre, baseline, isin)
        reference = "H0011-0003"
        if isin == ISIN_E:
            isin_right = isin_left(isin) + stringWidth(isin, FONT, SIZE)
            canvas.drawString(isin_right + 2, baseline, reference)
        else:
            canvas.drawCentredString(COLUMNS[1][1], baseline, reference)
        for (_, centre), value in zip(
            COLUMNS[2:], ["31/12/2025", currency, amount, "30.06.2026"], strict=False
        ):
            canvas.drawCentredString(centre, baseline, value)


def _write_report(
    path: Path,
    columns: list[tuple[list[str], float]] | None = None,
    rows: list[Row] | None = None,
    sub_fund_heading: str = "Sub fund",
    *,
    overflowing_rows: bool = False,
) -> Path:
    """Write a four-page report: cover, excess income, distributions, notes."""
    canvas = Canvas(str(path), pagesize=PAGE_SIZE)
    canvas.setFont(FONT, SIZE)
    canvas.drawString(LEFT, 560, "HSBC ETFs plc")
    canvas.drawString(LEFT, 550, "UK Reportable Income information")
    canvas.showPage()
    if overflowing_rows:
        _draw_overflowing_rows(canvas)
    _draw_table(
        canvas,
        columns if columns is not None else COLUMNS,
        rows if rows is not None else [_row(ISIN_A, "31/12/2025", "GBP", "0.0150")],
        sub_fund_heading,
    )
    # Distribution amounts sit where an excess column could be, and must not
    # be read as excess reported income.
    _draw_table(
        canvas,
        DISTRIBUTION_COLUMNS,
        [("HSBC Example UCITS ETF", [ISIN_A, "H0011-0001", "0.9999", "15/01/2026"])],
    )
    canvas.drawString(LEFT, 560, "Notes")
    canvas.drawString(
        LEFT, 550, "Excess of reported income per share: treated as a foreign dividend."
    )
    canvas.showPage()
    canvas.save()
    return path


def test_reads_excess_income_from_each_row(tmp_path: Path) -> None:
    """Each row gives its own period end, currency and per-share excess.

    The second fund closed on 19 February 2025, so its period ends then and
    not on the report's 31 December. The amounts are the report's own figures
    per share, so nothing is calculated.
    """
    rows = [
        _row(ISIN_A, "31/12/2025", "GBP", "0.0150"),
        _row(ISIN_B, "19/02/2025", "USD", "0.2095"),
    ]
    report = _write_report(tmp_path / REPORT_NAME, rows=rows, overflowing_rows=True)

    output = HsbcImporter().parse(report)

    assert output is not None
    assert output.output_file_name == "hsbc_eri.csv"
    transactions = sorted(output.transactions, key=lambda t: str(t.isin))
    assert len(transactions) == 5
    year_end = dt.date(2025, 12, 31)
    check_transaction(transactions[0], ISIN_A, year_end, Decimal("0.0150"), "GBP")
    check_transaction(
        transactions[1], ISIN_B, dt.date(2025, 2, 19), Decimal("0.2095"), "USD"
    )
    check_transaction(transactions[2], ISIN_C, year_end, Decimal("1.8063"), "EUR")
    check_transaction(transactions[3], ISIN_D, year_end, Decimal(0), "CHF")
    check_transaction(transactions[4], ISIN_E, year_end, Decimal("3.2500"), "HKD")


def test_reads_the_end_date_where_reports_give_start_and_end(tmp_path: Path) -> None:
    """The 2015 to 2017 reports print the period's start before its end."""
    # The last column is the total dividend for the year, also an amount.
    values = [ISIN_A, "H0011-0013", "01/01/2015", "31/12/2015", "GBP", "0.0150"]
    row = ("HSBC FTSE 100 UCITS ETF", [*values, "30/06/2016", "Yes", "2.4081"])
    report = _write_report(tmp_path / REPORT_NAME, COLUMNS_2015, [row])

    output = HsbcImporter().parse(report)

    assert output is not None
    assert len(output.transactions) == 1
    check_transaction(
        output.transactions[0], ISIN_A, dt.date(2015, 12, 31), Decimal("0.0150"), "GBP"
    )


def _renamed(old: str, new: list[str]) -> list[tuple[list[str], float]]:
    """Return the current columns with one heading replaced."""
    return [
        (new if " ".join(heading) == old else heading, centre)
        for heading, centre in COLUMNS
    ]


@pytest.mark.parametrize(
    ("columns", "rows", "sub_fund_heading", "message"),
    [
        pytest.param(
            _renamed("Excess reported income per share", ["Excess", "income"]),
            None,
            "Sub fund",
            "No column headed 'Excess reported income per share' on page 2, and it "
            "is not a table of distributions",
            id="no-excess-heading",
        ),
        pytest.param(
            _renamed("Currency", ["Ccy"]),
            None,
            "Sub fund",
            "No column headed 'Currency' on page 2",
            id="no-currency-heading",
        ),
        pytest.param(
            [*COLUMNS[:-1], (["Currency"], 730)],
            None,
            "Sub fund",
            "Several columns headed 'Currency' on page 2",
            id="two-currency-headings",
        ),
        pytest.param(
            None, None, "Fund name", "No 'Sub fund' heading on page 2", id="no-sub-fund"
        ),
        pytest.param(
            None,
            [_row(ISIN_A, "31/12/2025", "GBP", "N/A")],
            "Sub fund",
            f"Invalid amount for {ISIN_A} on page 2: 'N/A'",
            id="amount-not-a-number",
        ),
        pytest.param(
            None,
            [_row(ISIN_A, "2025-12-31", "GBP", "0.0150")],
            "Sub fund",
            f"Invalid reporting period end for {ISIN_A} on page 2: '2025-12-31'",
            id="end-not-dd-mm-yyyy",
        ),
        pytest.param(
            None,
            [_row(ISIN_A, "31/12/2025", "US$", "0.0150")],
            "Sub fund",
            f"Invalid currency for {ISIN_A} on page 2: 'US$'",
            id="currency-not-a-code",
        ),
        pytest.param(
            None,
            [_row(ISIN_A, "31/12/2025", "", "0.0150")],
            "Sub fund",
            f"Expected one value under 'Currency' for {ISIN_A} on page 2, found ''",
            id="currency-empty",
        ),
        pytest.param(
            None,
            [_row("IE00000000A3", "31/12/2025", "GBP", "0.0150")],
            "Sub fund",
            "Expected one ISIN on page 2, found 'IE00000000A3'",
            id="isin-bad-check-digit",
        ),
        pytest.param(
            None,
            [_row(f"{ISIN_A} {ISIN_B}", "31/12/2025", "GBP", "0.0150")],
            "Sub fund",
            f"Expected one ISIN on page 2, found '{ISIN_A} {ISIN_B}'",
            id="two-isins",
        ),
        pytest.param(
            None,
            [_row("", "31/12/2025", "GBP", "0.0150")],
            "Sub fund",
            "An amount on page 2 has no ISIN: 'HSBC Example UCITS ETF H0011-0001 "
            "31/12/2025 GBP 0.0150 30.06.2026 Yes No'",
            id="amount-without-isin",
        ),
        pytest.param(
            None, [], "Sub fund", "Could not extract any ERI data", id="no-rows"
        ),
    ],
)
def test_refuses_a_report_it_cannot_read_safely(
    tmp_path: Path,
    columns: list[tuple[list[str], float]] | None,
    rows: list[Row] | None,
    sub_fund_heading: str,
    message: str,
) -> None:
    """Each refusal names the page and what is wrong, and imports nothing."""
    report = _write_report(tmp_path / REPORT_NAME, columns, rows, sub_fund_heading)

    with pytest.raises(ParsingError) as error:
        HsbcImporter().parse(report)

    assert message in str(error.value)


@pytest.mark.parametrize(
    "name",
    [
        # HSBC Global Funds ICAV's report, laid out differently.
        "reportable income 2025-gb-en-hgfi.pdf",
        "legal-general-ucits-etf-plc-ukrfs-report-to-investors-2025.pdf",
        "reportable income 2025-gb-en-hetf.xlsx",
    ],
)
def test_leaves_other_files_to_other_importers(tmp_path: Path, name: str) -> None:
    """Only HSBC ETFs plc's report is read, whatever the file holds."""
    report = _write_report(tmp_path / name)

    assert HsbcImporter().parse(report) is None
