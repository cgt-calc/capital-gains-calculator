"""Tests for the Xtrackers ERI importer."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING, cast

import pytest
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.eri.importer.xtrackers import XtrackersImporter
from tests.eri_importers.helpers import check_transaction

if TYPE_CHECKING:
    from pathlib import Path

    from pdfplumber.page import Page

    from cgt_calc.parsers.eri.model import ERITransaction

# Wide page so every cell stays on a single line and the drawn grid
# is easy for pdfplumber to detect.
PAGESIZE = (1800, 700)

HEADER = [
    "ISIN",
    "Share class currency",
    "Excess reported income per share",
    "Fund name",
    "Sub fund",
]

DATA_ROWS: list[list[str]] = [
    ["LU0000000009", "USD", "1.23456", "Fund A", "Acc"],
    # JPN is a known data bug in the reports and is read as JPY.
    ["IE0000000004", "JPN", "0.55555", "Fund B", "Dist"],
    # Zero ERI rows are kept, consistent with the other importers.
    ["LU0000000124", "EUR", "0.0000", "Fund C", "Acc"],
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
    assert len(transactions) == 3
    check_transaction(
        transactions[0], "LU0000000009", period_end, Decimal("1.23456"), "USD"
    )
    check_transaction(
        transactions[1], "IE0000000004", period_end, Decimal("0.55555"), "JPY"
    )
    check_transaction(transactions[2], "LU0000000124", period_end, Decimal(0), "EUR")


def test_parse_summary_format(tmp_path: Path) -> None:
    """Parse the summary format used by the main Xtrackers umbrella."""
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2024.pdf",
        [
            "Summary of reportable income calculations",
            "Period ended 31 December 2024",
        ],
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    assert result.output_file_name == "xtrackers_eri.csv"
    _check_result_transactions(result.transactions, dt.date(2024, 12, 31))


def test_parse_investor_report_format(tmp_path: Path) -> None:
    """Parse the investor report format used by Xtrackers II and (IE) Plc.

    Also covers the official DWS download file name.
    """
    file = _build_pdf(
        tmp_path / "Reporting-Funds-Xtrackers-II-2024.pdf",
        [
            "Xtrackers II",
            "UK reporting fund status report to investors",
            "Period of account ended 31 December 2024",
        ],
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    _check_result_transactions(result.transactions, dt.date(2024, 12, 31))


def test_old_report_is_ignored(tmp_path: Path) -> None:
    """Reports before the minimum valid year produce no transactions."""
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2015.pdf",
        [
            "Summary of reportable income calculations",
            "Period ended 31 December 2015",
        ],
        with_table=False,
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    assert result.transactions == []


def test_missing_reporting_date(tmp_path: Path) -> None:
    """Produce no transactions when the reporting date line is missing."""
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2024.pdf",
        ["Summary of reportable income calculations"],
        with_table=False,
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    assert result.transactions == []


SUMMARY_PREAMBLE = [
    "Summary of reportable income calculations",
    "Period ended 31 December 2024",
]


@pytest.mark.parametrize(
    ("row", "error_match"),
    [
        (["not-an-isin", "USD", "1.23456", "Fund A", "Acc"], "Invalid ISIN"),
        (
            ["LU0000000009", "not-a-currency", "1.23456", "Fund A", "Acc"],
            "Invalid currency",
        ),
        (["LU0000000009", "USD", "not-a-number", "Fund A", "Acc"], "Invalid amount"),
    ],
)
def test_bad_data_row_fails_loudly(
    tmp_path: Path, row: list[str], error_match: str
) -> None:
    """Raise on rows with unparsable content instead of skipping them."""
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2024.pdf",
        SUMMARY_PREAMBLE,
        data_rows=[row],
    )

    with pytest.raises(ParsingError, match=error_match) as exc_info:
        XtrackersImporter().parse(file)

    assert str(file) in str(exc_info.value)
    assert "page 1" in str(exc_info.value)


def test_report_without_a_table_fails_loudly(tmp_path: Path) -> None:
    """A recognised report page with no table at all is invalid input."""
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2024.pdf",
        SUMMARY_PREAMBLE,
        with_table=False,
    )

    with pytest.raises(ParsingError, match="Could not find table headers on page 1"):
        XtrackersImporter().parse(file)


def test_header_only_table_fails_loudly(tmp_path: Path) -> None:
    """A table with headers but no data rows is invalid input."""
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2024.pdf",
        SUMMARY_PREAMBLE,
        data_rows=[],
    )

    with pytest.raises(ParsingError, match="Could not extract data table on page 1"):
        XtrackersImporter().parse(file)


def test_duplicate_header_columns_fail_loudly(tmp_path: Path) -> None:
    """Two columns claiming the same field make the table ambiguous."""
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2024.pdf",
        SUMMARY_PREAMBLE,
        header=[
            "ISIN",
            "Share class currency",
            "Excess reported income per share",
            "ISIN again",
            "Sub fund",
        ],
        data_rows=[],
    )

    with pytest.raises(ParsingError, match=r"Multiple columns match .* on page 1"):
        XtrackersImporter().parse(file)


def test_missing_header_column_fails_loudly(tmp_path: Path) -> None:
    """A table without one of the required columns is invalid input."""
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2024.pdf",
        SUMMARY_PREAMBLE,
        header=[
            "ISIN",
            "Currency",
            "Excess reported income per share",
            "Fund name",
            "Sub fund",
        ],
        data_rows=[],
    )

    with pytest.raises(ParsingError, match=r"No column matching .* on page 1"):
        XtrackersImporter().parse(file)


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
        XtrackersImporter._extract_header(  # noqa: SLF001
            page, tmp_path / "report.pdf", 1
        )


# The participants report used from 2025 by all three umbrellas. Its
# columns are cut down to those the importer reads plus a distribution
# column, which must never be taken for ERI.
PARTICIPANTS_FILE = "Xtrackers SICAV - Investor Report_31.12.2025.pdf"
PARTICIPANTS_TITLE = "UK Reporting Fund Status (UKRFS) Report to Participants"
PARTICIPANTS_PERIOD_LINE = "Xtrackers Period ended: 31/12/2025"
PARTICIPANTS_HEADER = [
    "HMRC share class\nreference",
    "ISIN CODE",
    "SUB FUND",
    "REPORTING PERIOD",
    "CLASS CURRENCY",
    "DISTRIBUTION(S) PER UNIT IN\nRESPECT OF THE REPORTING PERIOD",
    (
        "EXCESS OF REPORTED INCOME PER UNIT OVER\nDISTRIBUTIONS IN RESPECT OF THE\n"
        "REPORTING PERIOD"
    ),
]
SECTION_ROW = ["Accumulating shares:", "", "", "", "", "", ""]
YEAR_2025 = "01.01.2025 - 31.12.2025"
YEAR_END_2025 = dt.date(2025, 12, 31)


def _class_row(
    isin: str,
    currency: str,
    eri: str,
    period: str = YEAR_2025,
    distribution: str = "N/A",
) -> list[str]:
    """Return a share class row in PARTICIPANTS_HEADER's column order."""
    return ["X0004-0001", isin, "MSCI World", period, currency, distribution, eri]


def _header_with(index: int, heading: str) -> list[str]:
    """Return PARTICIPANTS_HEADER with one heading replaced."""
    header = list(PARTICIPANTS_HEADER)
    header[index] = heading
    return header


def _build_participants_pdf(
    path: Path,
    pages: list[list[list[str]] | None],
    title: str = PARTICIPANTS_TITLE,
    period_line: str = PARTICIPANTS_PERIOD_LINE,
    second_table: list[list[str]] | None = None,
) -> Path:
    """Build a participants report with one drawn table per page.

    As in the published reports, the first page's table starts with a
    title row and a period row spanning the table. A page given as None
    has a line of text and no table. second_table adds a separate table
    below the first page's one.
    """
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=PAGESIZE)
    story: list[Flowable] = []
    for page_num, rows in enumerate(pages):
        if page_num:
            story.append(PageBreak())
        if rows is None:
            story.append(Paragraph("Notes", styles["Normal"]))
            continue
        width = len(rows[0])
        if page_num == 0:
            rows = [
                [title, *[""] * (width - 1)],
                [period_line, *[""] * (width - 1)],
                *rows,
            ]
        table = Table(rows, colWidths=[170.0] * width)
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 1, colors.black),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        if page_num == 0:
            table.setStyle(
                TableStyle([("SPAN", (0, 0), (-1, 0)), ("SPAN", (0, 1), (-1, 1))])
            )
        story.append(table)
        if page_num == 0 and second_table is not None:
            extra = Table(second_table, colWidths=[170.0] * len(second_table[0]))
            extra.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
            story += [Spacer(1, 40), extra]
    doc.build(story)
    return path


def _summary(transactions: list[ERITransaction]) -> list[tuple[object, ...]]:
    """Return each transaction's ISIN, date, price and currency."""
    return [(t.isin, t.date, t.price, t.currency) for t in transactions]


def test_parse_participants_report(tmp_path: Path) -> None:
    """Read a two-page participants report with the header on page 1 only.

    Page 1's table has the title and period rows, the header and a section
    label before its classes, and page 2 continues without a header. The
    expected values are the fixture's own cells: JPN is read as JPY, a zero
    ERI is kept, and the distributing class's ERI is 0.3004, not its
    distributions of 0.1004 and 0.0553.
    """
    file = _build_participants_pdf(
        tmp_path / PARTICIPANTS_FILE,
        [
            [
                PARTICIPANTS_HEADER,
                SECTION_ROW,
                _class_row("LU0000000017", "USD", "1.8345"),
                _class_row("IE0000000020", "JPN", "0.5555"),
                _class_row("LU0000000033", "EUR", "0.0000"),
                _class_row("IE0000000046", "USD", "0.3004", distribution="0.1004"),
                ["", "", "", "", "", "0.0553", ""],
            ],
            [
                _class_row("LU0000000058", "EUR", "2.9484"),
                _class_row("IE0000000061", "GBP", "0.0635"),
            ],
        ],
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    assert result.output_file_name == "xtrackers_eri.csv"
    assert _summary(result.transactions) == [
        ("LU0000000017", YEAR_END_2025, Decimal("1.8345"), "USD"),
        ("IE0000000020", YEAR_END_2025, Decimal("0.5555"), "JPY"),
        ("LU0000000033", YEAR_END_2025, Decimal(0), "EUR"),
        ("IE0000000046", YEAR_END_2025, Decimal("0.3004"), "USD"),
        ("LU0000000058", YEAR_END_2025, Decimal("2.9484"), "EUR"),
        ("IE0000000061", YEAR_END_2025, Decimal("0.0635"), "GBP"),
    ]


def test_participants_header_repeated_on_a_later_page(tmp_path: Path) -> None:
    """A page that repeats the header is read by its own header.

    Page 2's header puts the currency before the sub fund and the ERI
    before the distribution, so reading it with page 1's columns would
    give the wrong values.
    """
    page_2_header = [
        PARTICIPANTS_HEADER[0],
        PARTICIPANTS_HEADER[1],
        PARTICIPANTS_HEADER[4],
        PARTICIPANTS_HEADER[2],
        PARTICIPANTS_HEADER[3],
        PARTICIPANTS_HEADER[6],
        PARTICIPANTS_HEADER[5],
    ]
    file = _build_participants_pdf(
        tmp_path / PARTICIPANTS_FILE,
        [
            [PARTICIPANTS_HEADER, _class_row("LU0000000017", "USD", "1.8345")],
            [
                page_2_header,
                [
                    "X0004-0002",
                    "IE0000000020",
                    "CHF",
                    "MSCI Europe",
                    YEAR_2025,
                    "1.2000",
                    "0.4000",
                ],
            ],
        ],
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    assert _summary(result.transactions) == [
        ("LU0000000017", YEAR_END_2025, Decimal("1.8345"), "USD"),
        ("IE0000000020", YEAR_END_2025, Decimal("1.2000"), "CHF"),
    ]


@pytest.mark.parametrize(
    ("eris", "expected"),
    [
        pytest.param(
            ["1.8345", "", "0.0635"],
            [
                ("LU0000000017", YEAR_END_2025, Decimal("1.8345"), "USD"),
                ("LU0000000033", YEAR_END_2025, Decimal("0.0635"), "USD"),
            ],
            id="one-blank",
        ),
        pytest.param(["", "", ""], [], id="all-blank"),
    ],
)
def test_participants_blank_eri_is_left_out_with_a_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    eris: list[str],
    expected: list[tuple[object, ...]],
) -> None:
    """A class with an empty ERI cell gets no row and a warning naming it.

    The report gives no figure, so it is not read as zero and its
    distribution of 0.0809 is not used instead. A report whose classes all
    lack ERI gives no rows, not a refusal.
    """
    isins = ["LU0000000017", "IE0000000020", "LU0000000033"]
    file = _build_participants_pdf(
        tmp_path / PARTICIPANTS_FILE,
        [
            [
                PARTICIPANTS_HEADER,
                *(
                    _class_row(isin, "USD", eri, distribution="0.0809")
                    for isin, eri in zip(isins, eris, strict=True)
                ),
            ]
        ],
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    assert _summary(result.transactions) == expected
    warned = [isin for isin, eri in zip(isins, eris, strict=True) if not eri]
    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == [
        f"Xtrackers report gives no excess reportable income for {isin} on page 1; "
        "no row added"
        for isin in warned
    ]


VALID_CLASS = _class_row("LU0000000017", "USD", "1.8345")


@pytest.mark.parametrize(
    ("pages", "error_match"),
    [
        pytest.param(
            [[_header_with(1, "ISIN"), VALID_CLASS]],
            "No column matching 'ISIN CODE' on page 1",
            id="no-isin-heading",
        ),
        pytest.param(
            [[_header_with(3, "PERIOD"), VALID_CLASS]],
            "No column matching 'REPORTING PERIOD' on page 1",
            id="no-period-heading",
        ),
        pytest.param(
            [[_header_with(4, "CURRENCY"), VALID_CLASS]],
            "No column matching 'CLASS CURRENCY' on page 1",
            id="no-currency-heading",
        ),
        pytest.param(
            [[_header_with(6, "EXCESS INCOME"), VALID_CLASS]],
            "No column matching 'EXCESS OF REPORTED INCOME PER UNIT OVER "
            "DISTRIBUTIONS' on page 1",
            id="no-eri-heading",
        ),
        pytest.param(
            [[_header_with(2, "CLASS CURRENCY"), VALID_CLASS]],
            r"Multiple columns match 'CLASS CURRENCY' on page 1: \[2, 4\]",
            id="two-currency-headings",
        ),
        pytest.param(
            [[VALID_CLASS]],
            "No header row on page 1",
            id="no-header-row",
        ),
        pytest.param(
            [[VALID_CLASS, PARTICIPANTS_HEADER, VALID_CLASS]],
            "Share class row before the header on page 1",
            id="class-before-header",
        ),
        pytest.param(
            [[PARTICIPANTS_HEADER, SECTION_ROW]],
            "No share class rows found",
            id="no-class-rows",
        ),
        pytest.param(
            [[PARTICIPANTS_HEADER, VALID_CLASS], [VALID_CLASS[:6]]],
            "Table on page 2 has 6 columns; the header on page 1 has 7",
            id="continuation-width",
        ),
        pytest.param(
            [[PARTICIPANTS_HEADER, VALID_CLASS, ["", "", "", "", "", "", "1.0000"]]],
            "Value without an ISIN on page 1, row 5",
            id="eri-without-isin",
        ),
        pytest.param(
            [[PARTICIPANTS_HEADER, VALID_CLASS, ["", "", "", "", "USD", "", ""]]],
            "Value without an ISIN on page 1, row 5",
            id="currency-without-isin",
        ),
        pytest.param(
            [[PARTICIPANTS_HEADER, VALID_CLASS, ["", "", "", YEAR_2025, "", "", ""]]],
            "Value without an ISIN on page 1, row 5",
            id="period-without-isin",
        ),
        pytest.param(
            [[PARTICIPANTS_HEADER, _class_row("LU0000000000", "USD", "1.8345")]],
            "Invalid ISIN on page 1, row 4: 'LU0000000000'",
            id="bad-isin",
        ),
        pytest.param(
            [
                [
                    PARTICIPANTS_HEADER,
                    _class_row("LU0000000017", "not-a-currency", "1.8345"),
                ]
            ],
            "Invalid currency on page 1, row 4: 'not-a-currency'",
            id="bad-currency",
        ),
        pytest.param(
            [[PARTICIPANTS_HEADER, _class_row("LU0000000017", "USD", "N/A")]],
            "Invalid amount on page 1, row 4: 'N/A'",
            id="eri-not-applicable",
        ),
        pytest.param(
            [
                [
                    PARTICIPANTS_HEADER,
                    _class_row(
                        "LU0000000017",
                        "USD",
                        "1.8345",
                        period="01.01.2025 - 31.13.2025",
                    ),
                ]
            ],
            "Invalid reporting period on page 1, row 4: '01.01.2025 - 31.13.2025'",
            id="bad-period",
        ),
        pytest.param(
            [
                [
                    PARTICIPANTS_HEADER,
                    _class_row("LU0000000017", "USD", "1.8345", period="31.12.2025"),
                ]
            ],
            "Invalid reporting period on page 1, row 4: '31.12.2025'",
            id="period-not-a-range",
        ),
        pytest.param(
            [
                [
                    PARTICIPANTS_HEADER,
                    _class_row(
                        "LU0000000017",
                        "USD",
                        "1.8345",
                        period="01.02.2025 - 31.01.2025",
                    ),
                ]
            ],
            "Invalid reporting period on page 1, row 4: '01.02.2025 - 31.01.2025'",
            id="period-ends-before-start",
        ),
        pytest.param(
            [[PARTICIPANTS_HEADER, VALID_CLASS], None],
            "Expected one table on page 2, found 0",
            id="page-without-table",
        ),
    ],
)
def test_participants_report_refusals(
    tmp_path: Path, pages: list[list[list[str]] | None], error_match: str
) -> None:
    """Refuse a participants report the importer cannot read with certainty."""
    file = _build_participants_pdf(tmp_path / PARTICIPANTS_FILE, pages)

    with pytest.raises(ParsingError, match=error_match):
        XtrackersImporter().parse(file)


def test_participants_class_period_end_is_the_row_date(tmp_path: Path) -> None:
    """Each class is dated by its own reporting period, not the report's."""
    file = _build_participants_pdf(
        tmp_path / PARTICIPANTS_FILE,
        [
            [
                PARTICIPANTS_HEADER,
                _class_row(
                    "LU0000000017", "USD", "1.0000", period="01.01.2025 - 30.09.2025"
                ),
                _class_row("IE0000000020", "USD", "2.0000"),
            ]
        ],
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    assert _summary(result.transactions) == [
        ("LU0000000017", dt.date(2025, 9, 30), Decimal("1.0000"), "USD"),
        ("IE0000000020", YEAR_END_2025, Decimal("2.0000"), "USD"),
    ]


@pytest.mark.parametrize(
    ("periods", "expected"),
    [
        pytest.param(
            ["01.01.2017 - 31.12.2017", "01.01.2018 - 31.12.2018"],
            [("IE0000000020", dt.date(2018, 12, 31), Decimal("1.0000"), "USD")],
            id="2017-left-out-2018-kept",
        ),
        pytest.param(
            ["01.01.2015 - 31.12.2015", "01.01.2015 - 31.12.2015"],
            [],
            id="old-report-gives-no-rows",
        ),
    ],
)
def test_participants_classes_before_minimum_year_are_ignored(
    tmp_path: Path, periods: list[str], expected: list[tuple[object, ...]]
) -> None:
    """A class whose period ends before 2018 gives no row and no refusal."""
    file = _build_participants_pdf(
        tmp_path / PARTICIPANTS_FILE,
        [
            [
                PARTICIPANTS_HEADER,
                _class_row("LU0000000017", "USD", "1.0000", period=periods[0]),
                _class_row("IE0000000020", "USD", "1.0000", period=periods[1]),
            ]
        ],
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    assert _summary(result.transactions) == expected


def test_participants_page_with_two_tables_is_refused(tmp_path: Path) -> None:
    """A page with a second table is refused, not read from its first."""
    file = _build_participants_pdf(
        tmp_path / PARTICIPANTS_FILE,
        [[PARTICIPANTS_HEADER, VALID_CLASS]],
        second_table=[_class_row("IE0000000020", "USD", "2.0000")],
    )

    with pytest.raises(ParsingError, match="Expected one table on page 1, found 2"):
        XtrackersImporter().parse(file)


@pytest.mark.parametrize(
    ("title", "period_line"),
    [
        pytest.param(
            "UK Reporting Fund Status (UKRFS)",
            PARTICIPANTS_PERIOD_LINE,
            id="no-participants-marker",
        ),
        pytest.param(
            PARTICIPANTS_TITLE, "Period ended: 31/12/2025", id="no-xtrackers-marker"
        ),
    ],
)
def test_participants_report_needs_both_markers(
    tmp_path: Path, title: str, period_line: str
) -> None:
    """A readable grid is not taken for an Xtrackers report without both markers."""
    file = _build_participants_pdf(
        tmp_path / PARTICIPANTS_FILE,
        [[PARTICIPANTS_HEADER, VALID_CLASS]],
        title=title,
        period_line=period_line,
    )

    assert XtrackersImporter().parse(file) is None


def test_unknown_content_is_not_accepted(tmp_path: Path) -> None:
    """Return None when the content matches neither format."""
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2024.pdf",
        ["Some other document"],
        with_table=False,
    )

    assert XtrackersImporter().parse(file) is None


def test_unmatched_file_name_is_not_accepted(tmp_path: Path) -> None:
    """Return None for files with other names."""
    file = _build_pdf(tmp_path / "vanguard.pdf", ["Anything"], with_table=False)

    assert XtrackersImporter().parse(file) is None
