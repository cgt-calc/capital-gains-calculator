"""Tests for the JPMorgan ETFs (Ireland) ICAV ERI importer.

Each test draws a synthetic Report to Investors laid out like JPMorgan's:
- the reports from 2021 have one row per share class, with the excess column
  between the reporting period and the distribution columns;
- the 2025 report adds an HMRC reference and the fund's currency of
  calculation, and names the share class currency only in the share class;
- the 2020 report lists the share classes as columns under row labels, and its
  reporting periods are cut off after the end's day.

The amounts are the reports' own per-unit figures, so nothing is calculated.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
import logging
import re
from typing import TYPE_CHECKING, NamedTuple, TypedDict

import pytest
from reportlab.pdfgen.canvas import Canvas

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.eri.importer.jpmorgan import JpmorganImporter
from tests.eri_importers.helpers import check_transaction

if TYPE_CHECKING:
    from pathlib import Path

ROW_REPORT = "jpm-etfs-uk-reportable-income-2021-en.pdf"
CCY_REPORT = "jpm-etf-ireland-icav-2025-report-to-investors.pdf"
COLUMN_REPORT = "jpm-etfs-uk-reportable-income-2020-en.pdf"

FONT = "Helvetica"
SIZE = 6
LINE = 8
LEFT = 40
# Valid check digits; IE00000000A3 differs from the first only in its check digit.
ISIN_A, ISIN_B, ISIN_C, ISIN_D = (
    "IE00000000A2",
    "IE00000000B0",
    "IE00000000C8",
    "IE00000000D6",
)
DISTRIBUTION_HEADING = ["Distribution", "(ex-date", "11/02/2021,", "pay date"]
ROW_ACCOUNT = "PERIOD OF ACCOUNT: 01/01/2021 - 31/12/2021"
CCY_ACCOUNT = "Accounting Period 1 January 2025 - 31 December 2025"
COLUMN_ACCOUNT = "Accounting Period 1 January 2020 - 31 December 2020"


class Layout(NamedTuple):
    """Where a row report draws its columns.

    `share_class` is None for the reports up to 2024, which give the currency a
    column at `currency`; the 2025 report draws the share class there instead,
    after the HMRC reference and the currency of calculation.
    """

    isin: float
    currency: float
    start: float
    end: float
    excess: float
    excess_right: float
    distributions: tuple[float, float]
    share_class: bool


ROWS = Layout(200, 260, 300, 335, 380, 405, (420, 470), share_class=False)
CCY = Layout(225, 270, 345, 380, 420, 445, (460, 510), share_class=True)


class Row(NamedTuple):
    """One share class of a row report.

    `currency` is the currency column's text, or the 2025 share class text.
    """

    isin: str
    currency: str
    excess: str
    start: str = "01/01/2021"
    end: str = "31/12/2021"
    distributions: tuple[str, ...] = ()
    name: str = "JPM Example UCITS ETF"


class RowOptions(TypedDict, total=False):
    """Changes to a row report's page, for the refusal cases."""

    account: str | None
    excess_heading: tuple[str, ...]
    distribution_headings: list[str]


def _write_rows(
    path: Path,
    rows: list[Row],
    *,
    layout: Layout = ROWS,
    account: str | None = ROW_ACCOUNT,
    excess_heading: tuple[str, ...] = (
        "Excess of",
        "reportable",
        "income per",
        "unit in",
        "share class",
        "currency",
    ),
    distribution_headings: list[str] = DISTRIBUTION_HEADING,
) -> Path:
    """Draw a one-page report with one row per share class."""
    canvas = Canvas(str(path), pagesize=(842, 595))
    canvas.setFont(FONT, SIZE)
    canvas.drawString(LEFT, 570, "Report to Investors")
    canvas.drawString(LEFT, 562, "JPMorgan ETFs (Ireland) ICAV")
    if account is not None:
        canvas.drawString(LEFT, 554, account)
    canvas.drawString(layout.start, 530, "Reporting Period")
    # The heading is centred, so its wider lines start left of "Excess".
    for offset, text in enumerate(excess_heading):
        canvas.drawCentredString(layout.excess + 15, 520 - offset * LINE, text)
    for x in layout.distributions:
        for offset, text in enumerate(distribution_headings):
            canvas.drawString(x, 520 - offset * LINE, text)
    heading = 520 - len(excess_heading) * LINE
    canvas.drawString(LEFT, heading, "Share class name")
    canvas.drawString(layout.isin, heading, "ISIN/SEDOL")
    canvas.drawString(layout.start, heading, "From")
    canvas.drawString(layout.end, heading, "To")
    y = heading - 2 * LINE
    for row in rows:
        canvas.drawString(LEFT, y, row.name)
        if layout.share_class:
            canvas.drawString(150, y, "J0072-0054")
            canvas.drawString(200, y, "USD")
        canvas.drawString(layout.isin, y, row.isin)
        canvas.drawString(layout.currency, y, row.currency)
        canvas.drawString(layout.start, y, row.start)
        canvas.drawString(layout.end, y, row.end)
        canvas.drawRightString(layout.excess_right, y, row.excess)
        for x, value in zip(layout.distributions, row.distributions, strict=False):
            canvas.drawRightString(x + 30, y, value)
        y -= LINE
    # The reports end with notes below the table.
    canvas.drawString(LEFT, y - LINE, "There is no excess reportable income where the")
    canvas.save()
    return path


class ShareClass(NamedTuple):
    """One column of the 2020 report."""

    isin: str
    share_class: str
    calculation: str
    excess: str
    period: str = "1 January 2020 - 31"


LABELS = (
    "HMRC ref. no.",
    "Currency of calculation",
    "ISIN/SEDOL",
    "Share class",
    "Reporting period",
    "Excess of reportable income per unit",
)


def _write_columns(
    path: Path,
    columns: list[ShareClass],
    *,
    account: str = COLUMN_ACCOUNT,
    labels: tuple[str, ...] = LABELS,
) -> Path:
    """Draw a one-page report with one column per share class."""
    canvas = Canvas(str(path), pagesize=(1191, 842))
    canvas.setFont(FONT, SIZE)
    canvas.drawString(LEFT, 800, "REPORT TO INVESTORS")
    canvas.drawString(LEFT, 790, account)
    # Each column is headed by its fund's name, above the labelled rows.
    for index in range(len(columns)):
        canvas.drawCentredString(400 + 120 * index, 772, "BetaBuilders Fund")
    y = 760
    for label in labels:
        canvas.drawString(LEFT, y, label)
        for index, column in enumerate(columns):
            centre = 400 + 120 * index
            value = {
                "HMRC ref. no.": f"J0072-00{index + 10}",
                "Currency of calculation": column.calculation,
                "ISIN/SEDOL": column.isin,
                "Share class": column.share_class,
                "Reporting period": column.period,
                "Excess of reportable income per unit": column.excess,
            }[label]
            canvas.drawCentredString(centre, y, value)
        y -= 12
    canvas.drawString(LEFT, y, "Cash and other distributions per unit:")
    canvas.drawString(
        LEFT, y - 12, "Distribution (ex-date 09/01/2020, pay date 07/02/2020)"
    )
    canvas.drawCentredString(400, y - 12, "0.0392")
    canvas.drawString(LEFT, y - 36, "There is no excess reportable income where the")
    canvas.save()
    return path


def test_reads_rows_with_a_currency_column(tmp_path: Path) -> None:
    """Each row gives its currency, its period's end and its excess.

    The second class started in September and pays distributions, whose
    amounts in the columns after the excess are not read as the excess.
    """
    report = _write_rows(
        tmp_path / ROW_REPORT,
        [
            Row(ISIN_A, "EUR", "0.0751"),
            Row(
                ISIN_B,
                "GBP",
                "0.0242",
                start="15/09/2021",
                distributions=("0.0371", "0.0321"),
            ),
        ],
    )

    output = JpmorganImporter().parse(report)

    assert output is not None
    assert output.output_file_name == "jpmorgan_eri.csv"
    transactions = sorted(output.transactions, key=lambda t: str(t.isin))
    assert len(transactions) == 2
    year_end = dt.date(2021, 12, 31)
    check_transaction(transactions[0], ISIN_A, year_end, Decimal("0.0751"), "EUR")
    check_transaction(transactions[1], ISIN_B, year_end, Decimal("0.0242"), "GBP")


def test_reads_the_share_class_currency_from_the_share_class(tmp_path: Path) -> None:
    """The 2025 report's excess is in the share class currency, "ccy".

    It also prints the fund's currency of calculation, USD, before the ISIN;
    the CHF hedged class's figure is in CHF, as its share class says.
    """
    report = _write_rows(
        tmp_path / CCY_REPORT,
        [
            Row(
                ISIN_C,
                "Class CHF Hedged (acc)",
                "0.2648",
                start="01/01/2025",
                end="31/12/2025",
            ),
            Row(
                ISIN_D,
                "Class USD (dist)",
                "0.1138",
                start="01/01/2025",
                end="31/12/2025",
                distributions=("0.0491",),
            ),
        ],
        layout=CCY,
        account=CCY_ACCOUNT,
        excess_heading=(
            "Excess of",
            "reportable",
            "income per",
            "unit in",
            "share class",
            "ccy",
        ),
    )

    output = JpmorganImporter().parse(report)

    assert output is not None
    transactions = sorted(output.transactions, key=lambda t: str(t.isin))
    assert len(transactions) == 2
    year_end = dt.date(2025, 12, 31)
    check_transaction(transactions[0], ISIN_C, year_end, Decimal("0.2648"), "CHF")
    check_transaction(transactions[1], ISIN_D, year_end, Decimal("0.1138"), "USD")


def test_reads_columns_and_refuses_a_figure_in_an_unstated_currency(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The 2020 report does not say which currency its figures are in.

    A class in the fund's currency of calculation is read, and so is "Nil",
    which is 0 in any currency. A figure for a class in another currency could
    be in either, so that column is left out with a warning; the rest of the
    report is still read.
    """
    report = _write_columns(
        tmp_path / COLUMN_REPORT,
        [
            ShareClass(ISIN_A, "Class EUR (acc)", "EUR", "Nil"),
            ShareClass(ISIN_B, "Class USD (acc)", "USD", "0.4636"),
            ShareClass(ISIN_C, "Class GBP Hedged (acc)", "USD", "Nil"),
            ShareClass(
                ISIN_D,
                "Class MXN Hedged (acc)",
                "USD",
                "8.6764",
                period="4 February 2020 - 31",
            ),
        ],
    )

    with caplog.at_level(logging.WARNING):
        output = JpmorganImporter().parse(report)

    assert output is not None
    transactions = sorted(output.transactions, key=lambda t: str(t.isin))
    assert len(transactions) == 3
    year_end = dt.date(2020, 12, 31)
    check_transaction(transactions[0], ISIN_A, year_end, Decimal(0), "EUR")
    check_transaction(transactions[1], ISIN_B, year_end, Decimal("0.4636"), "USD")
    check_transaction(transactions[2], ISIN_C, year_end, Decimal(0), "GBP")
    assert (
        f"Skipping {ISIN_D} on page 1: the report does not say whether its excess "
        "of 8.6764 is in the share class currency, MXN, or the currency of "
        "calculation, USD"
    ) in caplog.messages


@pytest.mark.parametrize(
    ("rows", "options", "message"),
    [
        pytest.param(
            [Row(ISIN_A, "EUR", "0.0751")],
            {"excess_heading": ("Excess of", "reportable", "income per", "unit")},
            "The excess heading on page 1 does not say its figures are in the share "
            "class currency: 'Excess of reportable income per unit'",
            id="heading-without-currency",
        ),
        pytest.param(
            [Row(ISIN_A, "EUR", "0.0751")],
            {"distribution_headings": ["Excess"]},
            "Expected one column headed 'Excess' on page 1, found 3",
            id="two-excess-headings",
        ),
        pytest.param(
            [Row(ISIN_A, "", "0.0751")],
            {},
            f"Expected the share class currency after {ISIN_A} on page 1",
            id="no-currency",
        ),
        pytest.param(
            [Row(ISIN_A, "EUR", "0.0751", end="")],
            {},
            f"Expected the reporting period's start and end for {ISIN_A} on page 1",
            id="one-date",
        ),
        pytest.param(
            [Row(ISIN_A, "EUR", "0.0751", end="31/12/2022")],
            {},
            f"The reporting period 2021-01-01 to 2022-12-31 of {ISIN_A} on page 1 "
            "is not within the accounting period 2021-01-01 to 2021-12-31",
            id="period-outside-the-accounting-period",
        ),
        pytest.param(
            [Row(ISIN_A, "EUR", "n/a")],
            {},
            f"Expected one amount under 'Excess' for {ISIN_A} on page 1",
            id="amount-not-a-number",
        ),
        pytest.param(
            [Row(ISIN_A, "EUR", "0.07 0.08")],
            {},
            f"Expected one amount under 'Excess' for {ISIN_A} on page 1",
            id="two-amounts",
        ),
        pytest.param(
            [Row("IE00000000A3", "EUR", "0.0751")],
            {},
            "Invalid ISIN 'IE00000000A3' on page 1",
            id="isin-bad-check-digit",
        ),
        pytest.param(
            [Row(ISIN_A, "EUR", "0.0751", name=f"JPM ETF {ISIN_B}")],
            {},
            "Expected one ISIN on page 1",
            id="two-isins",
        ),
        pytest.param(
            [Row(ISIN_A, "EUR", "0.0751")],
            {"account": None},
            "The report gives no accounting period",
            id="no-accounting-period",
        ),
    ],
)
def test_refuses_a_row_report_it_cannot_read_safely(
    tmp_path: Path, rows: list[Row], options: RowOptions, message: str
) -> None:
    """Each refusal names the page and what it expected."""
    report = _write_rows(tmp_path / ROW_REPORT, rows, **options)

    with pytest.raises(ParsingError, match=re.escape(message)):
        JpmorganImporter().parse(report)


@pytest.mark.parametrize(
    ("columns", "labels", "message"),
    [
        pytest.param(
            [ShareClass(ISIN_A, "Class EUR (acc)", "EUR", "0.2940")],
            tuple(label for label in LABELS if label != "Share class"),
            "No row labelled 'Share class' on page 1",
            id="missing-label",
        ),
        pytest.param(
            [ShareClass(ISIN_A, "EUR (acc)", "EUR", "0.2940")],
            LABELS,
            "Expected a share class currency and a currency of calculation for "
            f"{ISIN_A} on page 1, found 'EUR (acc)' and 'EUR'",
            id="no-share-class-currency",
        ),
        pytest.param(
            [
                ShareClass(
                    ISIN_A, "Class EUR (acc)", "EUR", "0.2940", "1 January 2020 - 19"
                )
            ],
            LABELS,
            f"Expected the reporting period of {ISIN_A} on page 1, ending with the "
            "accounting period on 2020-12-31, found '1 January 2020 - 19'",
            id="period-ends-another-day",
        ),
        pytest.param(
            [ShareClass(ISIN_A, "Class EUR (acc)", "EUR", "-")],
            LABELS,
            f"Expected one amount or 'Nil' for {ISIN_A} on page 1, found '-'",
            id="amount-not-a-number",
        ),
    ],
)
def test_refuses_a_column_report_it_cannot_read_safely(
    tmp_path: Path,
    columns: list[ShareClass],
    labels: tuple[str, ...],
    message: str,
) -> None:
    """Each refusal names the page and what it expected."""
    report = _write_columns(tmp_path / COLUMN_REPORT, columns, labels=labels)

    with pytest.raises(ParsingError, match=re.escape(message)):
        JpmorganImporter().parse(report)


def test_refuses_a_report_without_figures(tmp_path: Path) -> None:
    """A report with no share class rows has nothing to import."""
    report = _write_rows(tmp_path / ROW_REPORT, [])

    with pytest.raises(ParsingError, match="Could not extract any ERI data"):
        JpmorganImporter().parse(report)


@pytest.mark.parametrize(
    "name",
    [
        "jpm56794-etf-ctce-liquidation-en.pdf",
        "ssga-spdr-etfs-europe-i-plc-reportable-income-report-as-at-31-march-2025.pdf",
        "jpm-etfs-uk-reportable-income-2021-en.xlsx",
    ],
)
def test_leaves_other_files_to_other_importers(tmp_path: Path, name: str) -> None:
    """JPMorgan's notices, other providers' reports and other files are not read."""
    report = _write_rows(tmp_path / name, [Row(ISIN_A, "EUR", "0.0751")])

    assert JpmorganImporter().parse(report) is None
