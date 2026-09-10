"""Tests for the L&G ERI importer."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

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
from cgt_calc.parsers.eri.importer.legal_and_general import LegalAndGeneralImporter
from tests.eri_importers.helpers import check_transaction

if TYPE_CHECKING:
    from pathlib import Path

    from cgt_calc.parsers.eri.model import ERITransaction

# Wide page so every cell stays on a single line and the drawn grid
# is easy for pdfplumber to detect.
PAGESIZE = (1800, 700)

REPORT_FILE_NAME = "legal-general-ucits-etf-plc-ukrfs-report-to-investors-2025.pdf"

# L&G splits its header over two rows: the reporting period start and end
# dates sit under a shared "Reporting Period" heading. The distribution
# columns follow the excess reportable income column and hold amounts of the
# same shape, so the parser has to pick its column by header, not position.
HEADER_ROWS = [
    [
        "",
        "Sub Fund",
        "Currency of calculation",
        "ISIN/SEDOL",
        "Reporting Period",
        "",
        "Excess of reportable income per unit",
        "Distribution (ex-date 12/09/2024, pay date 20/09/2024)",
    ],
    ["", "", "", "", "Reporting from", "Reporting to", "", ""],
]

DATA_ROWS: list[list[str]] = [
    [
        "1",
        "L&G Fund A",
        "USD",
        "IE0000000004",
        "01/07/2024",
        "30/06/2025",
        "0.19800",
        "1.50000",
    ],
    # JPN is a known data bug in the reports and is read as JPY.
    [
        "2",
        "L&G Fund B",
        "JPN",
        "LU0000000009",
        "01/07/2024",
        "30/06/2025",
        "14.80240",
        "2.25000",
    ],
    # Zero ERI rows are kept, consistent with the other importers.
    [
        "3",
        "L&G Fund C",
        "EUR",
        "LU0000000124",
        "01/07/2024",
        "30/06/2025",
        "0.00000",
        "0.75000",
    ],
]

PERIOD_END = datetime.date(2025, 6, 30)


def _build_pdf(
    path: Path,
    with_table: bool = True,
    data_rows: list[list[str]] | None = None,
    header_rows: list[list[str]] | None = None,
) -> Path:
    """Build a report PDF, optionally with a data table."""
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=PAGESIZE)
    story: list[Flowable] = [
        Paragraph("Legal & General UCITS ETF Plc", styles["Normal"]),
        Paragraph("UK Reporting Fund Status report to investors", styles["Normal"]),
    ]
    if with_table:
        header_rows = header_rows if header_rows is not None else HEADER_ROWS
        data_rows = data_rows if data_rows is not None else DATA_ROWS
        story.append(Spacer(1, 20))
        col_widths = [200.0] * len(header_rows[0])
        table = Table([*header_rows, *data_rows], colWidths=col_widths)
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 1, colors.black),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    # Keep text within pdfplumber's intersection tolerance of
                    # the column edges, so the outermost columns stay part of
                    # the detected table.
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story.append(table)
    doc.build(story)
    return path


def _check_result_transactions(transactions: list[ERITransaction]) -> None:
    """Assert the transactions parsed from DATA_ROWS."""
    assert len(transactions) == 3
    check_transaction(
        transactions[0], "IE0000000004", PERIOD_END, Decimal("0.19800"), "USD"
    )
    check_transaction(
        transactions[1], "LU0000000009", PERIOD_END, Decimal("14.80240"), "JPY"
    )
    check_transaction(transactions[2], "LU0000000124", PERIOD_END, Decimal(0), "EUR")


def test_parse_report(tmp_path: Path) -> None:
    """Parse the UCITS ETF report format."""
    file = _build_pdf(tmp_path / REPORT_FILE_NAME)

    result = LegalAndGeneralImporter().parse(file)

    assert result is not None
    assert result.output_file_name == "legal_and_general_eri.csv"
    _check_result_transactions(result.transactions)


@pytest.mark.parametrize(
    "name",
    [
        # The official download names, which differ by year.
        "legal-general-ucits-etf-plc-ukrfs-report-to-investors-2025.pdf",
        "legal-general-ucits-etf-plc-ukrfs-report-to-investors-2024.pdf",
        "lg-etf-30.06.23---report-to-investors_final-web.pdf",
        # A manually renamed report.
        "legal-and-general-reportable-income-2025.pdf",
    ],
)
def test_report_file_names_are_recognised(tmp_path: Path, name: str) -> None:
    """Both the official download names and a manual rename are accepted."""
    file = _build_pdf(tmp_path / name)

    result = LegalAndGeneralImporter().parse(file)

    assert result is not None
    _check_result_transactions(result.transactions)


@pytest.mark.parametrize(
    "name",
    [
        # The other L&G reports use a different layout and are not supported.
        "legal--general-icav--investor-report-2025.pdf",
        "report-to-investors-legal--general-sicav-pe-2025.pdf",
        "lgim-liquidity-funds-plc---investor-report---2025.pdf",
        "Reporting-Funds-Xtrackers-II-2024.pdf",
    ],
)
def test_other_report_file_names_are_skipped(tmp_path: Path, name: str) -> None:
    """A file this importer does not own is left for the other importers."""
    file = _build_pdf(tmp_path / name)

    assert LegalAndGeneralImporter().parse(file) is None


def test_amount_is_read_from_the_header_not_the_column_position(
    tmp_path: Path,
) -> None:
    """Moving the columns moves the values read, it does not shift them.

    The distribution columns hold amounts of the same shape as excess
    reportable income, so a layout change must not silently report one as
    the other.
    """
    header_rows = [
        [
            "",
            "Sub Fund",
            "Currency of calculation",
            "ISIN/SEDOL",
            "Reporting Period",
            "",
            "Distribution (ex-date 12/09/2024, pay date 20/09/2024)",
            "Excess of reportable income per unit",
        ],
        ["", "", "", "", "Reporting from", "Reporting to", "", ""],
    ]
    file = _build_pdf(
        tmp_path / REPORT_FILE_NAME,
        header_rows=header_rows,
        data_rows=[
            [
                "1",
                "L&G Fund A",
                "USD",
                "IE0000000004",
                "01/07/2024",
                "30/06/2025",
                "1.50000",
                "0.19800",
            ]
        ],
    )

    result = LegalAndGeneralImporter().parse(file)

    assert result is not None
    check_transaction(
        result.transactions[0], "IE0000000004", PERIOD_END, Decimal("0.19800"), "USD"
    )


@pytest.mark.parametrize(
    ("row", "error_match"),
    [
        (
            [
                "1",
                "A",
                "USD",
                "not-an-isin",
                "01/07/2024",
                "30/06/2025",
                "0.198",
                "1.5",
            ],
            "Invalid ISIN",
        ),
        (
            [
                "1",
                "A",
                "not-a-currency",
                "IE0000000004",
                "01/07/2024",
                "30/06/2025",
                "0.198",
                "1.5",
            ],
            "Invalid currency",
        ),
        (
            [
                "1",
                "A",
                "USD",
                "IE0000000004",
                "01/07/2024",
                "not-a-date",
                "0.198",
                "1.5",
            ],
            "Invalid reporting period end",
        ),
        (
            [
                "1",
                "A",
                "USD",
                "IE0000000004",
                "01/07/2024",
                "30/06/2025",
                "not-a-number",
                "1.5",
            ],
            "Invalid amount",
        ),
    ],
)
def test_bad_data_row_fails_loudly(
    tmp_path: Path, row: list[str], error_match: str
) -> None:
    """Raise on rows with unparsable content instead of skipping them."""
    file = _build_pdf(tmp_path / REPORT_FILE_NAME, data_rows=[row])

    with pytest.raises(ParsingError, match=error_match) as exc_info:
        LegalAndGeneralImporter().parse(file)

    assert str(file) in str(exc_info.value)
    assert "page 1" in str(exc_info.value)


def test_missing_column_fails_loudly(tmp_path: Path) -> None:
    """A report without the excess reportable income column is invalid input."""
    header_rows = [
        [
            "",
            "Sub Fund",
            "Currency of calculation",
            "ISIN/SEDOL",
            "Reporting Period",
            "",
            "Distribution (ex-date 12/09/2024, pay date 20/09/2024)",
            "Total distributions",
        ],
        ["", "", "", "", "Reporting from", "Reporting to", "", ""],
    ]

    file = _build_pdf(tmp_path / REPORT_FILE_NAME, header_rows=header_rows)

    with pytest.raises(
        ParsingError, match="No column matching excess reportable income on page 1"
    ):
        LegalAndGeneralImporter().parse(file)


def test_repeated_column_fails_loudly(tmp_path: Path) -> None:
    """A report naming the same column twice is ambiguous, not a best guess."""
    header_rows = [
        [
            "",
            "ISIN/SEDOL",
            "Currency of calculation",
            "ISIN/SEDOL",
            "Reporting Period",
            "",
            "Excess of reportable income per unit",
            "Distribution (ex-date 12/09/2024, pay date 20/09/2024)",
        ],
        ["", "", "", "", "Reporting from", "Reporting to", "", ""],
    ]

    file = _build_pdf(tmp_path / REPORT_FILE_NAME, header_rows=header_rows)

    with pytest.raises(ParsingError, match="Multiple columns match"):
        LegalAndGeneralImporter().parse(file)


def test_report_without_a_table_fails_loudly(tmp_path: Path) -> None:
    """A recognised report page with no table at all is invalid input."""
    file = _build_pdf(tmp_path / REPORT_FILE_NAME, with_table=False)

    with pytest.raises(ParsingError, match="Found 0 tables on page 1"):
        LegalAndGeneralImporter().parse(file)


def test_header_only_table_fails_loudly(tmp_path: Path) -> None:
    """A table with headers but no data rows is invalid input."""
    file = _build_pdf(tmp_path / REPORT_FILE_NAME, data_rows=[])

    with pytest.raises(ParsingError, match="Could not extract any ERI data"):
        LegalAndGeneralImporter().parse(file)
