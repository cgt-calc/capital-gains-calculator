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
