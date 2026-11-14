"""Tests for the SSGA SPDR ETFs Europe I and II plc ERI importer.

Each test draws a synthetic report laid out like SSGA's: a letter page, then
HMRC's template table with its title and notes above it, headings stacked over
several lines, fund names wrapped over the lines above and below a row, and
distributions on lines of their own. The reports up to 2017 print their text
with no spaces between words; `run_together` draws that kind.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple, NotRequired, TypedDict

import pytest
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.eri.importer.spdr import SpdrImporter
from tests.eri_importers.helpers import check_transaction

if TYPE_CHECKING:
    from pathlib import Path

REPORT_NAME = (
    "ssga-spdr-etfs-europe-i-plc-reportable-income-report-as-at-31-march-2025.pdf"
)

FONT = "Helvetica"
SIZE = 6
PAGE_SIZE = (1191, 842)
LEFT = 40
SUB_FUND_LEFT = 130
# The baseline of the line holding "UMBRELLA FUND NAME".
HEADING_BASELINE = 640
LINE = 7

# Valid check digits; IE00000000A3 differs from the first only in its check digit.
ISIN_A, ISIN_B, ISIN_C, ISIN_D = (
    "IE00000000A2",
    "IE00000000B0",
    "IE00000000C8",
    "IE00000000D6",
)


class Column(NamedTuple):
    """A column's centre and heading lines, by line offset from the UMBRELLA line."""

    key: str
    centre: float
    heading: list[tuple[int, str]]


COLUMNS = [
    Column("isin", 300, [(0, "ISIN / Identifier")]),
    Column("ref", 380, [(0, "HMRC REFERENCE")]),
    Column("ccy", 460, [(-1, "CURRENCY OF"), (1, "SHARE CLASS")]),
    Column(
        "eri",
        570,
        [
            (-2, "PER UNIT EXCESS REPORTABLE"),
            (-1, "INCOME OVER DISTRIBUTIONS IN"),
            (0, "RESPECT OF THE REPORTING"),
            (1, "PERIOD (1) (3) (4)"),
        ],
    ),
    Column("fdd", 770, [(-1, "FUND DISTRIBUTION"), (0, "DATE (5)")]),
    Column(
        "remain",
        870,
        [
            (-2, "DOES THE FUND REMAIN A"),
            (-1, "REPORTING FUND AT THE DATE THIS"),
            (0, "REPORT IS MADE AVAILABLE?"),
        ],
    ),
    Column(
        "dist",
        980,
        [(-1, "DISTRIBUTION PER UNIT"), (0, "IN RESPECT OF THE PERIOD (2)")],
    ),
    Column("date", 1090, [(0, "DATE OF DISTRIBUTION")]),
]
# The reissued reports for 2020 to 2023 add whether a figure was revised.
REVISED_COLUMN = Column(
    "revised",
    670,
    [
        (-2, "HAS THE PER UNIT EXCESS"),
        (-1, "REPORTABLE INCOME"),
        (1, "BEEN REVISED (6)?"),
    ],
)


class Row(NamedTuple):
    """One share class: the values printed on its line, and lines around it."""

    isin: str
    ccy: str
    eri: str
    name: str = "SPDR Example UCITS ETF"
    # Names too long for the column continue on the lines above and below.
    wrapped: bool = False
    # Each distribution is printed on its own line, without the ISIN.
    distributions: tuple[tuple[str, str], ...] = ()
    # Points by which a long name runs on into the ISIN's first letters, drawn
    # before the ISIN as in SSGA's reports ("(AccIE)00BFWFPY67" read left to
    # right).
    overlap: float = 0


class Table(TypedDict):
    """The options of one table page, as `_draw_table` takes them."""

    rows: list[Row]
    title: NotRequired[str | None]
    columns: NotRequired[list[Column]]
    run_together: NotRequired[bool]
    footnotes: NotRequired[tuple[str, ...]]


def _text(text: str, run_together: bool) -> str:
    return text.replace(" ", "") if run_together else text


def _draw_table(
    canvas: Canvas,
    rows: list[Row],
    *,
    title: str | None = "Reporting Period Ended 31 March 2025",
    columns: list[Column] | None = None,
    run_together: bool = False,
    footnotes: tuple[str, ...] = (),
) -> None:
    """Draw one table page, with its title and notes above the headings."""
    columns = columns if columns is not None else COLUMNS
    canvas.setFont(FONT, SIZE)

    def at(x: float, y: float, text: str, *, centred: bool = False) -> None:
        draw = canvas.drawCentredString if centred else canvas.drawString
        draw(x, y, _text(text, run_together))

    at(LEFT, 800, "SSGA SPDR ETFs Europe I plc")
    at(LEFT, 790, "UK reporting fund status Report to Investors")
    if title is not None:
        at(LEFT, 780, title)
    # Notes over the table start at the left margin, so they are not headings,
    # even when they name the excess column as the reissued reports' note does.
    at(LEFT, 700, "Notes")
    at(LEFT, 693, "1 All per-share figures to 4 decimal places of relevant currency")
    at(
        LEFT,
        686,
        "PLEASE NOTE THAT ANY PER UNIT EXCESS REPORTABLE INCOME OVER DISTRIBUTIONS "
        "IN RESPECT OF THE REPORTING PERIOD HIGHLIGHTED IS A REVISED NUMBER",
    )
    at(LEFT, HEADING_BASELINE, "UMBRELLA FUND NAME")
    at(SUB_FUND_LEFT, HEADING_BASELINE, "SUB FUND NAME")
    for column in columns:
        for offset, text in column.heading:
            at(column.centre, HEADING_BASELINE - offset * LINE, text, centred=True)

    centre = {column.key: column.centre for column in columns}
    baseline = HEADING_BASELINE - 4 * LINE
    for row in rows:
        baseline -= LINE
        for amount, day in row.distributions[:1]:
            at(centre["dist"], baseline, amount, centred=True)
            at(centre["date"], baseline, day, centred=True)
        if row.wrapped:
            at(SUB_FUND_LEFT, baseline, row.name.split()[0])
        baseline -= LINE
        at(LEFT, baseline, "SSGA SPDR ETFs Europe I plc")
        if row.overlap:
            isin_left = centre["isin"] - stringWidth(row.isin, FONT, SIZE) / 2
            canvas.drawRightString(isin_left + row.overlap, baseline, row.name)
        else:
            at(
                SUB_FUND_LEFT,
                baseline,
                " ".join(row.name.split()[1:-1]) if row.wrapped else row.name,
            )
        values = {"isin": row.isin, "ref": "S0114-0001", "ccy": row.ccy, "eri": row.eri}
        values |= {"fdd": "30 September 2025", "remain": "Yes", "revised": "No"}
        for key, value in values.items():
            if key in centre and value:
                at(centre[key], baseline, value, centred=True)
        baseline -= LINE
        if row.wrapped:
            at(SUB_FUND_LEFT, baseline, row.name.split()[-1])
        for amount, day in row.distributions[1:]:
            at(centre["dist"], baseline, amount, centred=True)
            at(centre["date"], baseline, day, centred=True)
    for footnote in footnotes:
        baseline -= LINE
        at(LEFT, baseline, footnote)
    # A long note under the table, run together like the 2015 letters': upper
    # case, "ed5April2016" would pass as an ISIN.
    at(
        LEFT,
        100,
        "The excess should be included in the tax return for the year ended 5 April "
        "2016 if applicable",
    )
    canvas.drawString(PAGE_SIZE[0] / 2, 40, "2")
    canvas.showPage()


def _write_report(path: Path, *tables: Table) -> Path:
    """Write a letter page, then one table page for each keyword set."""
    canvas = Canvas(str(path), pagesize=PAGE_SIZE)
    canvas.setFont(FONT, SIZE)
    canvas.drawString(LEFT, 800, "Dear Shareholder")
    canvas.drawString(
        LEFT, 790, "Reportable income calculation - Period ended 31 March 2025"
    )
    canvas.showPage()
    for table in tables:
        _draw_table(canvas, **table)
    canvas.save()
    return path


ROWS = [
    Row(ISIN_A, "GBP", "0.0150"),
    # The distributions sit on their own lines, where an excess could be read
    # if the importer took the column from the wrong heading.
    Row(
        ISIN_B,
        "USD",
        "0.3113",
        name="SPDR Bloomberg Long Name UCITS ETF",
        wrapped=True,
        distributions=(("0.4253", "16 August 2024"), ("0.4568", "18 February 2025")),
    ),
]


def test_reads_excess_income_from_each_row(tmp_path: Path) -> None:
    """Each row gives its currency and per-share excess; the title gives the date.

    The table continues on a second page without a title of its own, and its
    row's fund name runs into the ISIN. The amounts are the report's own
    figures per share, so nothing is calculated.
    """
    overlapping = Row(
        ISIN_D,
        "USD",
        "1.3988",
        name="SPDR Emerging Markets Local Bond UCITS ETF (Acc)",
        overlap=6,
    )
    report = _write_report(
        tmp_path / REPORT_NAME,
        {"rows": ROWS},
        {"rows": [Row(ISIN_C, "EUR", "0.0000"), overlapping], "title": None},
    )

    output = SpdrImporter().parse(report)

    assert output is not None
    assert output.output_file_name == "spdr_eri.csv"
    transactions = sorted(output.transactions, key=lambda t: str(t.isin))
    assert len(transactions) == 4
    year_end = dt.date(2025, 3, 31)
    check_transaction(transactions[0], ISIN_A, year_end, Decimal("0.0150"), "GBP")
    check_transaction(transactions[1], ISIN_B, year_end, Decimal("0.3113"), "USD")
    check_transaction(transactions[2], ISIN_C, year_end, Decimal(0), "EUR")
    check_transaction(transactions[3], ISIN_D, year_end, Decimal("1.3988"), "USD")


def test_reads_run_together_report_and_dates_a_terminated_fund(
    tmp_path: Path,
) -> None:
    """The 2015 to 2017 reports print no spaces between words.

    A fund that closed during the year is footnoted, and its period ended on
    the day it closed, not on the title's 31 March.
    """
    report = _write_report(
        tmp_path / "ssga-spdr-i-investor-letter-authorised.pdf",
        {
            "rows": [
                Row(ISIN_A, "USD", "0.0029"),
                Row(
                    ISIN_D,
                    "GBP",
                    "0.0000",
                    name="SPDR Sterling Aggregate Bond UCITS ETF",
                ),
            ],
            "title": "Reporting period ended 31 March 2015",
            "run_together": True,
            "footnotes": (
                "SPDR Sterling Aggregate Bond UCITS ETF terminated 8 December 2014.",
            ),
        },
    )

    output = SpdrImporter().parse(report)

    assert output is not None
    transactions = sorted(output.transactions, key=lambda t: str(t.isin))
    assert len(transactions) == 2
    check_transaction(
        transactions[0], ISIN_A, dt.date(2015, 3, 31), Decimal("0.0029"), "USD"
    )
    check_transaction(transactions[1], ISIN_D, dt.date(2014, 12, 8), Decimal(0), "GBP")


def test_reads_revised_report(tmp_path: Path) -> None:
    """The reissued reports add a column saying whether each figure was revised."""
    report = _write_report(
        tmp_path
        / "revised-ssga-spdr-i-excess-reportable-income-report-as-at-31-march-2021.pdf",
        {
            "rows": [Row(ISIN_A, "EUR", "0.3132")],
            "title": "Reporting Period Ended 31 March 2021 (Revised)",
            "columns": [*COLUMNS[:4], REVISED_COLUMN, *COLUMNS[4:]],
        },
    )

    output = SpdrImporter().parse(report)

    assert output is not None
    assert len(output.transactions) == 1
    check_transaction(
        output.transactions[0], ISIN_A, dt.date(2021, 3, 31), Decimal("0.3132"), "EUR"
    )


def _renamed(key: str, heading: list[tuple[int, str]]) -> list[Column]:
    """Return the columns with one column's heading replaced."""
    return [
        column._replace(heading=heading) if column.key == key else column
        for column in COLUMNS
    ]


@pytest.mark.parametrize(
    ("table", "message"),
    [
        pytest.param(
            {
                "rows": [Row(ISIN_A, "EUR", "0.3132")],
                "title": "Reporting Period Ended 31 March 2021",
            },
            "This is SSGA's original report for the period ending 2021-03-31. SSGA "
            "reissued its reports for the periods ending 31 March 2020 to 2023; "
            "import the revised report instead",
            id="original-not-revised",
        ),
        pytest.param(
            {"rows": ROWS, "title": None},
            "No reporting period end above the table on page 2",
            id="no-title",
        ),
        pytest.param(
            {"rows": ROWS, "columns": _renamed("ccy", [(0, "CCY")])},
            "Expected one column headed 'CURRENCYOF' on page 2, found 0",
            id="no-currency-heading",
        ),
        pytest.param(
            {
                "rows": ROWS,
                # A second column under the full excess heading.
                "columns": _renamed("remain", COLUMNS[3].heading),
            },
            "Expected one column headed 'PERUNITEXCESS' on page 2, found 2",
            id="two-excess-headings",
        ),
        pytest.param(
            {"rows": [Row(ISIN_A, "GBP", "N/A")]},
            f"Expected one amount for {ISIN_A} on page 2, found 'N/A'",
            id="amount-not-a-number",
        ),
        pytest.param(
            {"rows": [Row(ISIN_A, "GBP", "0.0150 0.0200")]},
            f"Expected one amount for {ISIN_A} on page 2, found '0.0150 0.0200'",
            id="two-amounts",
        ),
        pytest.param(
            {"rows": [Row(ISIN_A, "US$", "0.0150")]},
            f"Expected one currency for {ISIN_A} on page 2, found 'US$'",
            id="currency-not-a-code",
        ),
        pytest.param(
            {"rows": [Row(ISIN_A, "", "0.0150")]},
            f"Expected one currency for {ISIN_A} on page 2, found ''",
            id="currency-empty",
        ),
        pytest.param(
            {"rows": [Row(ISIN_A, "GBP EUR", "0.0150")]},
            f"Expected one currency for {ISIN_A} on page 2, found 'GBP EUR'",
            id="two-currencies",
        ),
        pytest.param(
            {"rows": [Row(f"{ISIN_A} {ISIN_B}", "GBP", "0.0150")]},
            "Expected one ISIN on page 2, found",
            id="two-isins",
        ),
        pytest.param(
            {"rows": [Row("IE00000000A3", "GBP", "0.0150")]},
            "An amount on page 2 has no ISIN",
            id="isin-bad-check-digit",
        ),
        pytest.param(
            {
                "rows": ROWS,
                "footnotes": ("SPDR Other Fund UCITS ETF terminated 8 December 2014.",),
            },
            "No row of the table on page 2 names the fund in the footnote 'SPDR Other "
            "Fund UCITS ETF terminated 8 December 2014.'",
            id="unmatched-termination",
        ),
        pytest.param({"rows": []}, "Could not extract any ERI data", id="no-rows"),
    ],
)
def test_refuses_a_report_it_cannot_read_safely(
    tmp_path: Path, table: Table, message: str
) -> None:
    """Each refusal names what is wrong, and nothing is imported."""
    report = _write_report(tmp_path / REPORT_NAME, table)

    with pytest.raises(ParsingError) as error:
        SpdrImporter().parse(report)

    assert message in str(error.value)


def test_refuses_two_titles_that_disagree(tmp_path: Path) -> None:
    """A report covers one period; two titles for different ones are refused."""
    report = _write_report(
        tmp_path / REPORT_NAME,
        {"rows": ROWS},
        {
            "rows": [Row(ISIN_C, "EUR", "0.0000")],
            "title": "Reporting Period Ended 31 March 2024",
        },
    )

    with pytest.raises(
        ParsingError,
        match="The table on page 3 is for the period ending 2024-03-31, the one before it for 2025-03-31",
    ):
        SpdrImporter().parse(report)


def test_refuses_a_scanned_report(tmp_path: Path) -> None:
    """A scanned copy has no text to read; SSGA also publishes it as text."""
    path = tmp_path / "ssga-spdr-i-investor-letter-authorised-2015.pdf"
    canvas = Canvas(str(path), pagesize=PAGE_SIZE)
    canvas.rect(LEFT, 400, 500, 300)
    canvas.showPage()
    canvas.save()

    with pytest.raises(ParsingError, match="The file has no text to read"):
        SpdrImporter().parse(path)


@pytest.mark.parametrize(
    "name",
    [
        # SSGA's notices about the reissued reports hold no figures.
        "important-ssga-spdr-i-shareholder-notice-revised-excess-reportable-income-reports.pdf",
        "reportable income 2025-gb-en-hetf.pdf",
        "ssga-spdr-etfs-europe-i-plc-reportable-income-report-as-at-31-march-2025.xlsx",
    ],
)
def test_leaves_other_files_to_other_importers(tmp_path: Path, name: str) -> None:
    """Only SSGA's SPDR reports are read, whatever the file holds."""
    report = _write_report(tmp_path / name, {"rows": ROWS})

    assert SpdrImporter().parse(report) is None
