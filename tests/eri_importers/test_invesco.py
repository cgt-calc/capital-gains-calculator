"""Tests for the Invesco ERI importer."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from itertools import accumulate, pairwise
from typing import TYPE_CHECKING, cast

import pytest
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Flowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.eri.importer.invesco import (
    CURRENCY_COLUMN,
    ERI_COLUMN,
    ISIN_COLUMN,
    InvescoImporter,
)
from tests.eri_importers.helpers import check_transaction

if TYPE_CHECKING:
    from pathlib import Path

    from pdfplumber.page import Page

    from cgt_calc.parsers.eri.model import ERITransaction

# Wide page so every cell stays on a single line and the drawn grid
# is easy for pdfplumber to detect.
PAGESIZE = (2400, 700)

HEADER = [
    "ISIN / Identifier",
    "CURRENCY OF SHARE CLASS",
    "PER UNIT EXCESS REPORTABLE INCOME OVER DISTRIBUTIONS",
    "FUND NAME",
    "SHARE CLASS",
    "SEDOL",
    "DISTRIBUTION PER UNIT",
    "REPORTING PERIOD START",
    "REPORTING PERIOD END",
    "EQUALISATION PER UNIT",
    "NOTES",
]

DATA_ROWS: list[list[str]] = [
    [
        "IE00B3RBWM25",
        "USD",
        "1.23456",
        "Fund A",
        "Acc",
        "B3RBWM2",
        "0.1",
        "a",
        "b",
        "0.2",
        "n1",
    ],
    # Sub-row without an ISIN, e.g. a share class detail line.
    ["", "", "", "Fund A sub-class", "Hdg", "AAAAAAA", "0.1", "a", "b", "0.2", "n2"],
    # JPN is a known data bug in the reports and is read as JPY.
    [
        "IE00BK5BQV03",
        "JPN",
        "0.55555",
        "Fund B",
        "Dist",
        "BK5BQV0",
        "0.1",
        "a",
        "b",
        "0.2",
        "n3",
    ],
    # Footnote row that is not part of the data table.
    ["** Distribution reinvested", "x", "x", "x", "x", "x", "x", "x", "x", "x", "x"],
]


def _build_pdf(
    path: Path,
    preamble: list[str],
    with_table: bool = True,
    data_rows: list[list[str]] | None = None,
    header: list[str] | None = None,
) -> Path:
    """Build a PDF with preamble text lines and optionally a data table."""
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=PAGESIZE)
    story: list[Flowable] = [Paragraph(line, styles["Normal"]) for line in preamble]
    if with_table:
        header = header if header is not None else HEADER
        data_rows = data_rows if data_rows is not None else DATA_ROWS
        story.append(Spacer(1, 20))
        # The third column is wide enough for its long header to stay
        # within the cell boundaries.
        col_widths = [180.0] * len(header)
        col_widths[2] = 340.0
        table = Table([header, *data_rows], colWidths=col_widths)
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 1, colors.black),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    # Keep text within pdfplumber's intersection
                    # tolerance of the column edges, so the outermost
                    # columns stay part of the detected table.
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
                ]
            )
        )
        story.append(table)
    doc.build(story)
    return path


def _check_result_transactions(
    transactions: list[ERITransaction], period_end: dt.date
) -> None:
    """Assert the transactions parsed from DATA_ROWS."""
    assert len(transactions) == 2
    check_transaction(
        transactions[0], "IE00B3RBWM25", period_end, Decimal("1.23456"), "USD"
    )
    check_transaction(
        transactions[1], "IE00BK5BQV03", period_end, Decimal("0.55555"), "JPY"
    )


def test_parse_v1_report(tmp_path: Path) -> None:
    """Parse a v1 report with the statement header on the first page."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2022.pdf",
        [
            (
                "STATEMENT OF REPORTABLE INCOME FOR INVESCO MARKETS II PLC "
                "FOR THE PERIOD ENDED 30 June 2022"
            ),
        ],
    )

    result = InvescoImporter().parse(file)

    assert result is not None
    assert result.output_file_name == "invesco_eri.csv"
    _check_result_transactions(result.transactions, dt.date(2022, 6, 30))


def test_parse_v2_report(tmp_path: Path) -> None:
    """Parse a v2 report with per-page headers."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2023.pdf",
        [
            "Invesco Markets II plc",
            "UK reporting fund status report to investors",
            "Reporting Period End 31 December 2023",
        ],
    )

    result = InvescoImporter().parse(file)

    assert result is not None
    _check_result_transactions(result.transactions, dt.date(2023, 12, 31))


UNRULED_HEADINGS = [
    "UMBRELLA FUND\nNAME",
    "SUB FUND NAME",
    "SHARE CLASS / SERIES NAME",
    "ISIN / Identifier",
    "HMRC REFERENCE",
    "CURRENCY OF\nSHARE CLASS",
    (
        "PER UNIT EXCESS REPORTABLE\nINCOME OVER DISTRIBUTIONS IN\n"
        "RESPECT OF THE REPORTING\nPERIOD (1) (3) (4)"
    ),
    "FUND DISTRIBUTION\nDATE (5)",
    (
        "DOES THE FUND REMAIN A\nREPORTING FUND AT THE\n"
        "DATE THIS REPORT IS MADE\nAVAILABLE?"
    ),
    "DISTRIBUTION PER\nUNIT IN RESPECT OF\nTHE REPORTING\nPERIOD (2)",
    "DATE OF\nDISTRIBUTION",
]

# The ISIN column is far wider than its heading and the share class column
# is not, so the ISINs start well left of their heading and left of the point
# halfway between the two headings.
UNRULED_WIDTHS = [90.0, 80.0, 122.0, 100.0, 84.0, 70.0, 140.0, 90.0, 120.0, 90.0, 80.0]

UNRULED_PREAMBLE = [
    "Invesco Markets II plc",
    "UK reporting fund status Report to Investors",
    "Reporting Period Ended 31 December 2025",
]


def _build_unruled_pdf(
    path: Path, data_rows: list[list[str]], heading_shift: float = 0.0
) -> Path:
    """Draw a report laid out like Invesco's 2025 one.

    A shaded box holds the headings, each line centred in its column and
    followed by a space, as in that report. Thin filled rectangles rule the
    rows, and nothing marks the columns. Cell text starts 2 points inside its
    column. ``heading_shift`` moves every heading that far right of centre.

    As in that report, the rules are a little wider than the box, so the box
    is the tallest rectangle but not the widest. The box also ends half a point
    right of the last column, where the report's is a fifth of a point off.
    """
    font = "Helvetica"
    canvas = Canvas(str(path), pagesize=PAGESIZE)
    canvas.setFont(font, 8)
    for i, line in enumerate(UNRULED_PREAMBLE):
        canvas.drawString(40, 680 - 12 * i, line)
    edges = list(accumulate(UNRULED_WIDTHS, initial=40.0))
    width = edges[-1] - edges[0] + 0.5
    canvas.setFillColor(colors.lightgrey)
    canvas.rect(edges[0], 560, width, 44, stroke=0, fill=1)
    canvas.setFillColor(colors.black)
    for (left, right), heading in zip(pairwise(edges), UNRULED_HEADINGS, strict=True):
        for i, line in enumerate(heading.split("\n")):
            ink = stringWidth(line, font, 8)
            x = (left + right - ink) / 2 + heading_shift
            canvas.drawString(x, 594 - 9 * i, line + " ")
    top = 560.0
    for row in data_rows:
        canvas.rect(edges[0] - 0.4, top, width + 0.4, 0.6, stroke=0, fill=1)
        top -= 12
        for left, cell in zip(edges, row, strict=False):
            canvas.drawString(left + 2, top + 3, cell)
    canvas.save()
    return path


UNRULED_ROWS = [
    [
        "Invesco Markets II plc",
        "Fund A",
        "Class A Acc",
        "IE00B3RBWM25",
        "I0103-0001",
        "USD",
        "1.2345",
        "30 June 2026",
        "Yes",
        "N/A",
        "N/A",
    ],
    # A distribution paid in the period, on a line of its own with no ISIN.
    ["", "", "", "", "", "", "", "", "", "0.1100", "11 December 2025"],
    [
        "Invesco Markets II plc",
        "Fund B",
        "Class B Dist",
        "IE00BK5BQV03",
        "I0103-0002",
        "EUR",
        "0.0000",
        "30 June 2026",
        "Yes",
        "0.2926",
        "11 September 2025",
    ],
    [
        "Invesco Markets II plc",
        "Fund C",
        "Class C Acc",
        "IE00B42WWV65",
        "I0103-0003",
        "GBP",
        "12.0456",
        "30 June 2026",
        "Yes",
        "N/A",
        "N/A",
    ],
]


def test_parse_report_with_no_column_lines(tmp_path: Path) -> None:
    """Read the 2025 layout, whose table rules its rows but not its columns.

    Each share class's ISIN, currency and excess per unit come from its own
    line; the distribution line between them has no ISIN and adds nothing.
    The period end is the title's 31 December 2025.
    """
    file = _build_unruled_pdf(
        tmp_path / "invesco-markets-ii-reportable-income-2025.pdf", UNRULED_ROWS
    )

    result = InvescoImporter().parse(file)

    assert result is not None
    period_end = dt.date(2025, 12, 31)
    assert len(result.transactions) == 3
    check_transaction(
        result.transactions[0], "IE00B3RBWM25", period_end, Decimal("1.2345"), "USD"
    )
    check_transaction(
        result.transactions[1], "IE00BK5BQV03", period_end, Decimal(0), "EUR"
    )
    check_transaction(
        result.transactions[2], "IE00B42WWV65", period_end, Decimal("12.0456"), "GBP"
    )


@pytest.mark.parametrize("heading_shift", [3.0, -3.0, 0.8, -0.3])
def test_header_with_no_column_lines_and_uncentred_headings_fails_loudly(
    tmp_path: Path, heading_shift: float
) -> None:
    """Without centred headings the columns cannot be placed, so refuse.

    Headings moved off centre move the right edges of the 1st, 3rd, ..., 11th
    columns twice as far the same way. The box ends 0.5 points right of the
    centred columns, so 3-point shifts put the last edge 5.5 points past it or
    6.5 points short. Shifts of 0.8 and -0.3 points put it 1.1 points past or
    short, just outside the 1-point tolerance.
    """
    file = _build_unruled_pdf(
        tmp_path / "invesco-markets-ii-reportable-income-2025.pdf",
        UNRULED_ROWS,
        heading_shift=heading_shift,
    )

    with pytest.raises(
        ParsingError,
        match=(
            "The table header on page 1 has no column lines, and its columns "
            "cannot be placed from the centred headings"
        ),
    ):
        InvescoImporter().parse(file)


def test_old_report_is_ignored(tmp_path: Path) -> None:
    """Reports before the minimum valid year produce no transactions."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2015.pdf",
        [
            (
                "STATEMENT OF REPORTABLE INCOME FOR INVESCO MARKETS II PLC "
                "FOR THE PERIOD ENDED 30 June 2015"
            ),
        ],
        with_table=False,
    )

    result = InvescoImporter().parse(file)

    assert result is not None
    assert result.transactions == []


def test_v2_alternative_title_and_old_date(tmp_path: Path) -> None:
    """Accept the alternative v2 title and skip old reporting periods."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2015.pdf",
        [
            "Invesco Markets II plc",
            "UK reporting fund status Report to Investor",
            "Reporting Period Ended 31 December 2015",
        ],
        with_table=False,
    )

    result = InvescoImporter().parse(file)

    assert result is not None
    assert result.transactions == []


def test_v2_missing_reporting_date(tmp_path: Path) -> None:
    """Produce no transactions when the reporting date line is missing."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2023.pdf",
        [
            "Invesco Markets II plc",
            "UK reporting fund status report to investors",
        ],
        with_table=False,
    )

    result = InvescoImporter().parse(file)

    assert result is not None
    assert result.transactions == []


def test_v2_unparseable_reporting_date(tmp_path: Path) -> None:
    """Produce no transactions when the reporting date cannot be parsed."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2023.pdf",
        [
            "Invesco Markets II plc",
            "UK reporting fund status report to investors",
            "Reporting Period End soon",
        ],
        with_table=False,
    )

    result = InvescoImporter().parse(file)

    assert result is not None
    assert result.transactions == []


V1_PREAMBLE = [
    (
        "STATEMENT OF REPORTABLE INCOME FOR INVESCO MARKETS II PLC "
        "FOR THE PERIOD ENDED 30 June 2022"
    ),
]

_ROW_TAIL = ["Fund A", "Acc", "B3RBWM2", "0.1", "a", "b", "0.2", "n1"]


@pytest.mark.parametrize(
    ("row_head", "error_match"),
    [
        (["not-an-isin", "USD", "1.23456"], "Invalid ISIN"),
        (["IE00B3RBWM25", "not-a-currency", "1.23456"], "Invalid currency"),
        (["IE00B3RBWM25", "USD", "not-a-number"], "Invalid amount"),
    ],
)
def test_bad_data_row_fails_loudly(
    tmp_path: Path, row_head: list[str], error_match: str
) -> None:
    """Raise on rows with unparsable content instead of skipping them."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2022.pdf",
        V1_PREAMBLE,
        data_rows=[row_head + _ROW_TAIL],
    )

    with pytest.raises(ParsingError, match=error_match) as exc_info:
        InvescoImporter().parse(file)

    assert str(file) in str(exc_info.value)
    assert "page 1" in str(exc_info.value)


def test_report_without_a_table_fails_loudly(tmp_path: Path) -> None:
    """A recognised report page with no table at all is invalid input."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2022.pdf",
        V1_PREAMBLE,
        with_table=False,
    )

    with pytest.raises(ParsingError, match="Could not find table headers on page 1"):
        InvescoImporter().parse(file)


def test_header_only_table_fails_loudly(tmp_path: Path) -> None:
    """A table with headers but no data rows is invalid input."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2022.pdf",
        V1_PREAMBLE,
        data_rows=[],
    )

    with pytest.raises(ParsingError, match="Could not extract data table on page 1"):
        InvescoImporter().parse(file)


def test_duplicate_header_columns_fail_loudly(tmp_path: Path) -> None:
    """Two columns claiming the same field make the table ambiguous."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2022.pdf",
        V1_PREAMBLE,
        header=[*HEADER[:-1], "ISIN again"],
        data_rows=[],
    )

    with pytest.raises(ParsingError, match=r"Multiple columns match .* on page 1"):
        InvescoImporter().parse(file)


def test_missing_header_column_fails_loudly(tmp_path: Path) -> None:
    """A table without one of the required columns is invalid input."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2022.pdf",
        V1_PREAMBLE,
        header=["Currency" if h == "CURRENCY OF SHARE CLASS" else h for h in HEADER],
        data_rows=[],
    )

    with pytest.raises(ParsingError, match=r"No column matching .* on page 1"):
        InvescoImporter().parse(file)


def test_too_few_header_columns_fail_loudly(tmp_path: Path) -> None:
    """A table narrower than the known report layout is invalid input."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2022.pdf",
        V1_PREAMBLE,
        header=HEADER[:3],
        data_rows=[],
    )

    with pytest.raises(
        ParsingError, match="Expected at least 11 columns on page 1, found 3"
    ):
        InvescoImporter().parse(file)


class _HeaderlessTable:
    """Table stub whose extraction yields no rows."""

    @staticmethod
    def extract() -> list[list[str]]:
        return []


class _HeaderlessPage:
    """Page stub with a detectable but empty table."""

    @staticmethod
    def find_table() -> _HeaderlessTable:
        return _HeaderlessTable()


def test_empty_extracted_header_fails_loudly(tmp_path: Path) -> None:
    """A detected table that extracts to nothing is reported, not indexed."""
    page = cast("Page", _HeaderlessPage())

    with pytest.raises(ParsingError, match="Table header is empty on page 1"):
        InvescoImporter._extract_header(  # noqa: SLF001
            page, tmp_path / "report.pdf", 1
        )


def test_short_data_row_fails_loudly(tmp_path: Path) -> None:
    """A row with too few cells is an error, not a silently skipped subrow."""
    colmap = {ISIN_COLUMN: 0, CURRENCY_COLUMN: 1, ERI_COLUMN: 2}
    data_table: list[list[str | None]] = [["IE00B3RBWM25", "USD"]]

    with pytest.raises(ParsingError, match="has 2 cells, expected at least 3"):
        InvescoImporter._process_data_table(  # noqa: SLF001
            1, data_table, dt.date(2024, 12, 31), colmap, tmp_path / "x.pdf"
        )


def test_unknown_content_is_not_accepted(tmp_path: Path) -> None:
    """Return None when the content matches neither format."""
    file = _build_pdf(
        tmp_path / "invesco-excess-reportable-income-2023.pdf",
        ["Some other document"],
        with_table=False,
    )

    assert InvescoImporter().parse(file) is None


def test_unmatched_file_name_is_not_accepted(tmp_path: Path) -> None:
    """Return None for files with other names."""
    file = _build_pdf(tmp_path / "vanguard.pdf", ["Anything"], with_table=False)

    assert InvescoImporter().parse(file) is None
