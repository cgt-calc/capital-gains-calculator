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


# SICAV 2025 investor format (v11): the table pdfplumber finds is not
# header-only. It starts with a title row and a period row, then the
# column-name row, then a section label row, then the data rows.
# The header phrases are shortened for this synthetic PDF (the real
# headers are longer) but contain exactly the phrases the parser
# matches: ISIN CODE, CLASS CURRENCY and EXCESS OF REPORTED INCOME.
SICAV_HEADER = [
    "HMRC share class reference",
    "ISIN CODE",
    "UMBRELLA FUND",
    "SUB FUND",
    "CLASS NAME",
    "REPORTING PERIOD",
    "CLASS CURRENCY",
    "DISTRIBUTION(S) PER UNIT",
    "DISTRIBUTION DATE(S)",
    "EXCESS OF REPORTED INCOME PER UNIT",
    "FUND DISTRIBUTION DATE",
    "DID THE SHARE CLASS REMAIN A REPORTING FUND?",
]

SICAV_DATA_ROWS: list[list[str]] = [
    [
        "X0004-0064",
        "LU0274208692",
        "Xtrackers",
        "MSCI WORLD SWAP",
        "1C",
        "01.01.2025 - 31.12.2025",
        "USD",
        "N/A",
        "N/A",
        "1.8345",
        "30/06/2026",
        "Yes",
    ],
    [
        "X0004-0022",
        "LU0274209237",
        "Xtrackers",
        "MSCI EUROPE",
        "1C",
        "01.01.2025 - 31.12.2025",
        "USD",
        "N/A",
        "N/A",
        "2.9484",
        "30/06/2026",
        "Yes",
    ],
    [
        "X0004-0068",
        "LU0274209740",
        "Xtrackers",
        "MSCI JAPAN",
        "1C",
        "01.01.2025 - 31.12.2025",
        "USD",
        "N/A",
        "N/A",
        "1.7031",
        "30/06/2026",
        "Yes",
    ],
    # Zero ERI row, kept in the SICAV format as well.
    [
        "X0004-0011",
        "LU0292106167",
        "Xtrackers",
        "BLOOMBERG COMMODITY",
        "1C - EUR HEDGED",
        "01.01.2025 - 31.12.2025",
        "EUR",
        "N/A",
        "N/A",
        "0.0000",
        "30/06/2026",
        "Yes",
    ],
]


def _build_sicav_pdf(
    path: Path, preamble: list[str], data_rows: list[list[str]] | None = None
) -> Path:
    """Build a SICAV 2025 PDF: title, period, header, section, data.

    All of those rows are inside one table, as pdfplumber finds them
    in the real report, so the parser must find the header at row 2,
    the section label at row 3, and the first data row at row 4.
    """
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(path),
        pagesize=PAGESIZE,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    story: list[Flowable] = [Paragraph(line, styles["Normal"]) for line in preamble]
    story.append(Spacer(1, 20))
    rows = [
        ["UK Reporting Fund Status (UKRFS) Report to Participants", *[""] * 11],
        ["Xtrackers Period ended: 31/12/2025 Date of Report: 25/09/2026", *[""] * 11],
        SICAV_HEADER,
        ["Accumulating shares:", *[""] * 11],
        *(data_rows if data_rows is not None else SICAV_DATA_ROWS),
    ]
    table = Table(rows, colWidths=[140.0] * len(SICAV_HEADER))
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                # Title and period rows span the whole table, as in
                # the real report, where their text is in the first
                # cell and the remaining cells are empty.
                ("SPAN", (0, 0), (-1, 0)),
                ("SPAN", (0, 1), (-1, 1)),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    return path


def test_parse_sicav_2025_investor_format(tmp_path: Path) -> None:
    """Parse the SICAV 2025 investor format, including its first row.

    Regression test for v11: the parser used to crop after
    start_row + 2, which on this layout is the first data row
    itself, so LU0274208692 was lost whatever the header crop
    epsilon. It must now be the first transaction returned.
    Also covers the numeric reporting date (31/12/2025) and the
    section label row, which must be skipped, not raised on.
    """
    file = _build_sicav_pdf(
        tmp_path / "Xtrackers SICAV - Investor Report_31.12.2025.pdf",
        [
            "Xtrackers SICAV",
            "UK reporting fund status report to participants",
            "Period ended: 31/12/2025",
        ],
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    assert result.output_file_name == "xtrackers_eri.csv"
    assert len(result.transactions) == 4
    check_transaction(
        result.transactions[0],
        "LU0274208692",
        dt.date(2025, 12, 31),
        Decimal("1.8345"),
        "USD",
    )
    check_transaction(
        result.transactions[1],
        "LU0274209237",
        dt.date(2025, 12, 31),
        Decimal("2.9484"),
        "USD",
    )
    check_transaction(
        result.transactions[2],
        "LU0274209740",
        dt.date(2025, 12, 31),
        Decimal("1.7031"),
        "USD",
    )
    check_transaction(
        result.transactions[3],
        "LU0292106167",
        dt.date(2025, 12, 31),
        Decimal(0),
        "EUR",
    )


def test_reporting_date_on_next_line(tmp_path: Path) -> None:
    """A reporting date on the line after its prefix is still found.

    Covers _extract_report_end_date's next-line branch: the prefix
    line alone has no date, so the following line is tried.
    """
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2024.pdf",
        [
            "Summary of reportable income calculations",
            "Period ended:",
            "31 December 2024",
        ],
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    _check_result_transactions(result.transactions, dt.date(2024, 12, 31))


def test_prefix_without_date_returns_no_transactions(tmp_path: Path) -> None:
    """A prefix line with no date after it produces no transactions.

    Covers the other side of the next-line branch (no following
    line) and the empty-date continue: the date extraction returns
    None, so the summary parser returns an empty result, not a
    guessed date.
    """
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2024.pdf",
        [
            "Summary of reportable income calculations",
            "Period ended:",
        ],
        with_table=False,
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    assert result.transactions == []


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


def test_partial_data_rows_are_skipped_not_raised(tmp_path: Path) -> None:
    """Rows missing either ISIN or ERI are skipped, not raised on.

    Since v7, no_isin_no_eri matches if EITHER column is missing,
    and it runs before the invalid* checks, so a partial row must
    never reach the Invalid ISIN / Invalid amount raises, and the
    complete rows around it must still parse.
    """
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2024.pdf",
        SUMMARY_PREAMBLE,
        data_rows=[
            ["LU0000000009", "USD", "1.23456", "Fund A", "Acc"],
            ["LU0000000009", "USD", "", "Fund A", "Acc"],
            ["", "USD", "1.23456", "Fund A", "Acc"],
            ["LU0000000124", "EUR", "0.0000", "Fund C", "Acc"],
        ],
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    assert len(result.transactions) == 2
    check_transaction(
        result.transactions[0],
        "LU0000000009",
        dt.date(2024, 12, 31),
        Decimal("1.23456"),
        "USD",
    )
    check_transaction(
        result.transactions[1], "LU0000000124", dt.date(2024, 12, 31), Decimal(0), "EUR"
    )


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


def test_duplicate_header_columns_use_first_match(tmp_path: Path) -> None:
    """Duplicate header columns no longer fail loudly.

    Since v3, _extract_header does not raise on a pattern matching
    several columns; it takes the first match and lets the data
    rows disambiguate. This replaces the old test that expected
    "Multiple columns match", which the current parser no longer
    raises: with a data row present, the first ISIN column wins
    and the row parses normally.
    """
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
        data_rows=[["LU0000000009", "USD", "1.23456", "Fund A", "Acc"]],
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    assert len(result.transactions) == 1
    check_transaction(
        result.transactions[0],
        "LU0000000009",
        dt.date(2024, 12, 31),
        Decimal("1.23456"),
        "USD",
    )


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


def test_process_data_table_realigns_shifted_columns(tmp_path: Path) -> None:
    """Data rows shifted by one against the header map still parse.

    In the real SICAV 2025 cropped extraction the data rows have
    ISIN at column 0, currency at 5 and ERI at 8, while the header
    map says 1, 6 and 9. _process_data_table must shift every
    column by the ISIN discrepancy, not read ISIN as 'Xtrackers'
    and raise Invalid ISIN.
    """
    data_rows: list[list[str | None]] = [
        [
            "LU0274208692",
            "Xtrackers",
            "MSCI WORLD SWAP",
            "1C",
            "01.01.2025 - 31.12.2025",
            "USD",
            "N/A",
            "N/A",
            "1.8345",
            "30/06/2026",
        ],
        [
            "LU0274209237",
            "Xtrackers",
            "MSCI EUROPE",
            "1C",
            "01.01.2025 - 31.12.2025",
            "USD",
            "N/A",
            "N/A",
            "2.9484",
            "30/06/2026",
        ],
    ]
    transactions = XtrackersImporter._process_data_table(  # noqa: SLF001
        1,
        data_rows,
        dt.date(2025, 12, 31),
        {0: 1, 1: 6, 2: 9},
        tmp_path / "report.pdf",
    )

    assert len(transactions) == 2
    check_transaction(
        transactions[0], "LU0274208692", dt.date(2025, 12, 31), Decimal("1.8345"), "USD"
    )
    check_transaction(
        transactions[1], "LU0274209237", dt.date(2025, 12, 31), Decimal("2.9484"), "USD"
    )


def test_first_data_row_finds_first_isin_row() -> None:
    """_first_data_row stops at the first row with a real ISIN.

    On SICAV 2025 page 1 that is row 4, after the header row (2)
    and the section label row (3), which has no ISIN. A row whose
    ISIN merely looks like one (format only, checksum not checked
    here) still counts as data, so a corrupted first row is not
    mistaken for header. With no data row at all it returns None,
    and _extract_header falls back to the 3-row merge.
    """
    rows: list[list[str | None]] = [
        ["UK Reporting Fund Status", *[""] * 11],
        ["Xtrackers Period ended: 31/12/2025", *[""] * 11],
        ["HMRC share class reference", "ISIN CODE", *[""] * 10],
        ["Accumulating shares:", *[""] * 11],
        ["X0004-0064", "LU0274208692", *[""] * 10],
    ]
    assert XtrackersImporter._first_data_row(rows, 2) == 4  # noqa: SLF001
    assert XtrackersImporter._first_data_row(rows[:4], 2) is None  # noqa: SLF001
    # The appended row needs its own list[str | None] annotation:
    # an inline list[str] literal would not be assignable to
    # list[str | None] either, even inside the unpacking.
    corrupt_row: list[str | None] = ["X0004-0000", "LU0000000000", *[""] * 10]
    assert (
        XtrackersImporter._first_data_row(  # noqa: SLF001
            [*rows[:4], corrupt_row], 2
        )
        == 4
    )


class _TextPage:
    """Page stub returning fixed text lines for date extraction."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def extract_text_lines(self) -> list[dict[str, str]]:
        return [{"text": line} for line in self._lines]


def test_extract_report_end_date_next_line() -> None:
    """Date extraction: prefix alone, date on the next line."""
    page = cast("Page", _TextPage(["Period ended:", "31 December 2024"]))

    assert XtrackersImporter._extract_report_end_date(  # noqa: SLF001
        page, "Period ended:"
    ) == dt.date(2024, 12, 31)


def test_extract_report_end_date_unparseable_returns_none() -> None:
    """Date extraction: first parse fails, fallback finds no date.

    Covers the fallback's no-match branch: the remainder contains
    no date pattern at all, so extraction returns None.
    """
    page = cast("Page", _TextPage(["Period ended: not a date at all"]))

    assert (
        XtrackersImporter._extract_report_end_date(  # noqa: SLF001
            page, "Period ended:"
        )
        is None
    )


def test_extract_report_end_date_invalid_day_returns_none() -> None:
    """Date extraction: first parse fails, fallback match also fails.

    31 February is a date pattern, so the fallback finds it, but
    parsing it fails again, so the fallback's except-pass branch
    is taken and extraction returns None.
    """
    page = cast("Page", _TextPage(["Period ended: 31 February 2025"]))

    assert (
        XtrackersImporter._extract_report_end_date(  # noqa: SLF001
            page, "Period ended:"
        )
        is None
    )


def test_unknown_content_is_not_accepted(tmp_path: Path) -> None:
    """Return None when the content matches neither format."""
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2024.pdf",
        ["Some other document"],
        with_table=False,
    )

    assert XtrackersImporter().parse(file) is None


def test_shift_isin_column_rejects_out_of_range_shift() -> None:
    """An ISIN shift that would leave the table is rejected.

    Covers _shift_isin_column's out-of-range return, and its
    empty-row continue: the data has an empty row, then a row
    whose ISIN is at column 9 while the header map says 1, so
    shifting by +8 would put currency and ERI outside the table.
    The original map is returned unchanged.
    """
    data_table: list[list[str | None]] = [
        [],
        ["", "", "", "", "", "", "", "", "", "LU0274208692"],
    ]
    result = XtrackersImporter._shift_isin_column(  # noqa: SLF001
        data_table, {0: 1, 1: 6, 2: 9}
    )

    assert result == {0: 1, 1: 6, 2: 9}


def test_disambiguate_currency_and_eri_from_data(tmp_path: Path) -> None:
    """Currency and ERI are found from data when the header is wrong.

    Covers _disambiguate_currency_eri's assign branches, which
    the shift-only SICAV test does not reach: the header map says
    currency 8 and ERI 9, but the data has a junk column at 8,
    currency at 9 and amounts at 10. The third row's ERI is N/A,
    so it is skipped as eri_na_blank, which also covers the
    skipped-ISINs logging path in _process_data_table.
    """
    data_rows: list[list[str | None]] = [
        [
            "LU0274208692",
            "Fund A",
            "x",
            "x",
            "x",
            "x",
            "x",
            "x",
            "SHARE CLASS",
            "EUR",
            "2.5000",
        ],
        [
            "LU0274209237",
            "Fund B",
            "x",
            "x",
            "x",
            "x",
            "x",
            "x",
            "SHARE CLASS",
            "USD",
            "0.7500",
        ],
        [
            "LU0274209740",
            "Fund C",
            "x",
            "x",
            "x",
            "x",
            "x",
            "x",
            "SHARE CLASS",
            "GBP",
            "N/A",
        ],
    ]
    transactions = XtrackersImporter._process_data_table(  # noqa: SLF001
        1,
        data_rows,
        dt.date(2025, 12, 31),
        {0: 0, 1: 8, 2: 9},
        tmp_path / "report.pdf",
    )

    assert len(transactions) == 2
    check_transaction(
        transactions[0], "LU0274208692", dt.date(2025, 12, 31), Decimal("2.5000"), "EUR"
    )
    check_transaction(
        transactions[1], "LU0274209237", dt.date(2025, 12, 31), Decimal("0.7500"), "USD"
    )


def test_disambiguate_with_no_qualifying_column_keeps_header_map() -> None:
    """No column reaching the threshold leaves the map unchanged.

    Covers the other sides of the currency and amount
    realignment branches: currency and amount codes are scattered
    one row per column, so no column reaches 50% and there are
    no amount candidates at all.
    """
    data_table: list[list[str | None]] = [
        ["LU0274208692", "x", "x", "x", "x", "EUR", "x", "x", "2.5000", "x", "x"],
        ["LU0274209237", "x", "x", "x", "x", "x", "USD", "x", "x", "0.7500", "x"],
        ["LU0274209740", "x", "x", "x", "x", "x", "x", "GBP", "x", "x", "1.0000"],
    ]
    result = XtrackersImporter._disambiguate_currency_eri(  # noqa: SLF001
        data_table, {0: 0, 1: 5, 2: 8}
    )

    assert result == {0: 0, 1: 5, 2: 8}


def test_process_data_table_empty_returns_no_transactions(tmp_path: Path) -> None:
    """An empty data table returns no transactions, without error."""
    transactions = XtrackersImporter._process_data_table(  # noqa: SLF001
        1,
        [],
        dt.date(2025, 12, 31),
        {0: 0, 1: 1, 2: 2},
        tmp_path / "report.pdf",
    )

    assert transactions == []


def test_eri_na_blank_rows_are_skipped(tmp_path: Path) -> None:
    """N/A, Nil and blank ERI rows are skipped, not raised on.

    Covers _process_row's eri_na_blank return and
    _process_data_table's skipped-ISINs path, which no earlier
    test reached (earlier data had amounts, never N/A ERI).
    A fully blank row is skipped as blank, not as eri_na_blank.
    """
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2024.pdf",
        SUMMARY_PREAMBLE,
        data_rows=[
            ["LU0000000009", "USD", "1.23456", "Fund A", "Acc"],
            ["LU0000000009", "USD", "N/A", "Fund A", "Acc"],
            ["LU0000000009", "USD", "Nil", "Fund A", "Acc"],
            ["", "", "", "", ""],
            ["LU0000000124", "EUR", "0.0000", "Fund C", "Acc"],
        ],
    )

    result = XtrackersImporter().parse(file)

    assert result is not None
    assert len(result.transactions) == 2
    check_transaction(
        result.transactions[0],
        "LU0000000009",
        dt.date(2024, 12, 31),
        Decimal("1.23456"),
        "USD",
    )
    check_transaction(
        result.transactions[1], "LU0000000124", dt.date(2024, 12, 31), Decimal(0), "EUR"
    )


def test_missing_isin_column_fails_loudly(tmp_path: Path) -> None:
    """A table with no ISIN column at all is invalid input.

    Covers the start-row search completing without finding an
    ISIN row, and the last-resort raise for the ISIN column.
    Requires the v13 _parse_pages behaviour: a header error
    with no header ever found is raised, not silently returned
    as zero transactions.
    """
    file = _build_pdf(
        tmp_path / "Xtrackers-Report-2024.pdf",
        SUMMARY_PREAMBLE,
        header=[
            "Fund",
            "Share class currency",
            "Excess reported income per share",
            "Fund name",
            "Sub fund",
        ],
        data_rows=[],
    )

    with pytest.raises(ParsingError, match=r"No column matching .* on page 1"):
        XtrackersImporter().parse(file)


def test_header_only_sicav_table_fails_loudly(tmp_path: Path) -> None:
    """A SICAV table with headers but no data rows is invalid input.

    Covers _extract_header's fallback when the header table
    contains no data row (first_data_row is None, so the header
    ends at start_row + 2): the header is found, the crop is
    after the section row, and data extraction then fails.
    """
    file = _build_sicav_pdf(
        tmp_path / "Xtrackers SICAV - Investor Report_31.12.2025.pdf",
        [
            "Xtrackers SICAV",
            "UK reporting fund status report to participants",
            "Period ended: 31/12/2025",
        ],
        data_rows=[],
    )

    with pytest.raises(ParsingError, match="Could not extract data table on page 1"):
        XtrackersImporter().parse(file)


def test_unmatched_file_name_is_not_accepted(tmp_path: Path) -> None:
    """Return None for files with other names."""
    file = _build_pdf(tmp_path / "vanguard.pdf", ["Anything"], with_table=False)

    assert XtrackersImporter().parse(file) is None
