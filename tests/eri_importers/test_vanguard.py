"""Tests for the Vanguard ERI importer."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.eri.importer.vanguard import VanguardImporter
from tests.eri_importers.helpers import check_transaction, write_xlsx, write_xlsx_sheets

if TYPE_CHECKING:
    from pathlib import Path

HEADER: list[object] = [
    "ISIN",
    "Reporting Period",
    "Currency",
    "Excess of reporting income",
]
PERIOD = "01 January 2024 to 31 December 2024"
PERIOD_END = dt.date(2024, 12, 31)


def test_parse_header_in_first_row(tmp_path: Path) -> None:
    """Parse a report with the header in the first row."""
    # The file name check is case-insensitive.
    file = write_xlsx(
        tmp_path / "Vanguard-ERI-2024.xlsx",
        [
            HEADER,
            ["IE00B3RBWM25", PERIOD, "USD", "1.23456"],
            ["IE00BK5BQV03", PERIOD, "GBP", "Nil"],
        ],
    )

    result = VanguardImporter().parse(file)

    assert result is not None
    assert result.output_file_name == "vanguard_eri.csv"
    assert len(result.transactions) == 2
    check_transaction(
        result.transactions[0], "IE00B3RBWM25", PERIOD_END, Decimal("1.23456"), "USD"
    )
    check_transaction(
        result.transactions[1], "IE00BK5BQV03", PERIOD_END, Decimal(0), "GBP"
    )


def test_parse_header_after_preamble_with_trailing_table(tmp_path: Path) -> None:
    """Parse a report with preamble rows and a trailing retired funds table."""
    file = write_xlsx(
        tmp_path / "vanguard-eri-2024.xlsx",
        [
            ["Vanguard Excess Reportable Income", None, None, None],
            [None, None, None, None],
            HEADER,
            ["IE00B3RBWM25", PERIOD, "USD", "1.23456"],
            [None, None, None, None],
            HEADER,
            ["IE00BK5BQV03", PERIOD, "USD", "9.99999"],
        ],
    )

    result = VanguardImporter().parse(file)

    assert result is not None
    # The trailing table is for retired funds and is skipped.
    assert len(result.transactions) == 1
    check_transaction(
        result.transactions[0], "IE00B3RBWM25", PERIOD_END, Decimal("1.23456"), "USD"
    )


def test_period_end_is_read_day_first(tmp_path: Path) -> None:
    """Read a period end printed day first, as UK reports print them.

    Both periods end on "01/04/2019", 1 April 2019. Read month first it
    would be 4 January 2019.
    """
    file = write_xlsx(
        tmp_path / "vanguard-eri-2019.xlsx",
        [
            HEADER,
            ["IE00B3RBWM25", "02/04/2018 - 01/04/2019", "USD", "0.03590"],
            ["IE00BK5BQV03", "02/04/2018 to 01/04/2019", "GBP", "0.10000"],
        ],
    )

    result = VanguardImporter().parse(file)

    assert result is not None
    assert len(result.transactions) == 2
    check_transaction(
        result.transactions[0],
        "IE00B3RBWM25",
        dt.date(2019, 4, 1),
        Decimal("0.03590"),
        "USD",
    )
    check_transaction(
        result.transactions[1],
        "IE00BK5BQV03",
        dt.date(2019, 4, 1),
        Decimal("0.10000"),
        "GBP",
    )


def test_investment_series_file_name_is_accepted(tmp_path: Path) -> None:
    """Read an Investment Series report under the name Vanguard publishes it.

    Those names start ``vis-plc-`` and do not contain "vanguard".
    """
    file = write_xlsx(
        tmp_path / "vis-plc-excess-reportable-income-31-december-2024.xlsx",
        [HEADER, ["IE00B3RBWM25", PERIOD, "USD", "1.23456"]],
    )

    result = VanguardImporter().parse(file)

    assert result is not None
    assert result.output_file_name == "vanguard_eri.csv"
    assert len(result.transactions) == 1


def test_parse_investment_series_2022_layout(tmp_path: Path) -> None:
    """Read the columns of the 2022 Investment Series report.

    It heads the ERI column "reportable" where the other years' spreadsheets
    say "reporting"; the report's own note defines both as the income reported
    per share in excess of the distributions paid. Columns on both sides hold
    other per-share figures, the amounts are numbers to be rounded, and the
    second class stopped reporting in the middle of the year.
    """
    header: list[object] = [
        "ISIN₁",
        "Umbrella Name",
        "Fund Name",
        "Share Class Name",
        "Reporting Period",
        "Distributing / Accumulating",
        "Share Class Currency",
        "Income distributed per share in respect of the reporting period2,8,9",
        "Ex-date on which distribution was declared₃ DD/MM/YYYY",
        "Excess of reportable income over distribution₄",
        "Fund distribution date₅",
        (
            "Equalisation amount per unit in relation to any interest acquired in "
            "the fund in the reporting period 7"
        ),
        "Fund classification10",
        (
            "Did the share class remain a reporting fund at the date this report "
            "was made available?"
        ),
    ]

    def class_row(
        isin: str, period: str, currency: str, income: float, eri: float
    ) -> list[object]:
        return [
            isin,
            "Vanguard Investment Series plc",
            "Foo Index Fund",
            f"{currency} Dist",
            period,
            "Distributing",
            currency,
            income,
            "30/12/2022",
            eri,
            "30 June 2023",
            0.25,
            "Equity Fund",
            "Yes",
        ]

    # The report's title is in a text box, which leaves the rows above the
    # header empty.
    blank: list[object] = [None] * len(header)
    file = write_xlsx(
        tmp_path / "vanguard-eri-2022.xlsx",
        [
            blank,
            blank,
            header,
            class_row(
                "IE00B3RBWM25",
                "01 January 2022 - 31 December 2022",
                "GBP",
                0.5,
                1.234564,
            ),
            class_row(
                "IE00BK5BQV03", "01 January 2022 - 14 June 2022", "USD", 0.75, 2.345678
            ),
        ],
    )

    result = VanguardImporter().parse(file)

    assert result is not None
    assert len(result.transactions) == 2
    # Amounts are kept to five decimal places: 1.234564 rounds down and
    # 2.345678 rounds up.
    check_transaction(
        result.transactions[0],
        "IE00B3RBWM25",
        dt.date(2022, 12, 31),
        Decimal("1.23456"),
        "GBP",
    )
    check_transaction(
        result.transactions[1],
        "IE00BK5BQV03",
        dt.date(2022, 6, 14),
        Decimal("2.34568"),
        "USD",
    )


def test_unmatched_file_name_is_not_accepted(tmp_path: Path) -> None:
    """Return None for files from other providers."""
    file = write_xlsx(tmp_path / "blackrock-eri-2024.xlsx", [HEADER])

    assert VanguardImporter().parse(file) is None


def test_no_isin_column(tmp_path: Path) -> None:
    """Raise when no sheet of the workbook has an ISIN column."""
    file = write_xlsx_sheets(
        tmp_path / "vanguard-eri-2024.xlsx",
        {
            "Report information": [["Excess reportable income report"]],
            "31 Dec 2024": [
                ["Fund", "Period", "Ccy", "Amount"],
                ["Some Fund", PERIOD, "USD", "1.0"],
            ],
        },
    )

    with pytest.raises(ParsingError, match="No ISIN column"):
        VanguardImporter().parse(file)


# The US-domiciled ETFs' workbooks have one sheet per year end, with the
# header below a few lines of title; some open with an information sheet,
# and the latest has header-only sheets for year ends still to come.
TITLE: list[list[object]] = [
    ["Vanguard US ETFs", None, None, None],
    ["UK reporting fund status", None, None, None],
    [None, None, None, None],
]
JAN_PERIOD = "01 February 2024 to 31 January 2025"
DEC_PERIOD = "01 January 2025 to 31 December 2025"


def test_rows_are_read_from_every_data_sheet(tmp_path: Path) -> None:
    """Read the classes on each year-end sheet, and nothing from a placeholder."""
    file = write_xlsx_sheets(
        tmp_path / "vanguard-Excess_Reportable_Income_2025.xlsx",
        {
            "31 Jan 2025": [
                *TITLE,
                HEADER,
                ["US9220426764", JAN_PERIOD, "USD", "1.23456"],
            ],
            "31 Dec 2025": [
                *TITLE,
                HEADER,
                ["US9229087690", DEC_PERIOD, "USD", "0"],
                ["US9220427424", DEC_PERIOD, "USD", "0.25000"],
            ],
            "31 Aug 2026": [*TITLE, HEADER],
        },
    )

    result = VanguardImporter().parse(file)

    assert result is not None
    assert len(result.transactions) == 3
    check_transaction(
        result.transactions[0],
        "US9220426764",
        dt.date(2025, 1, 31),
        Decimal("1.23456"),
        "USD",
    )
    check_transaction(
        result.transactions[1],
        "US9229087690",
        dt.date(2025, 12, 31),
        Decimal(0),
        "USD",
    )
    check_transaction(
        result.transactions[2],
        "US9220427424",
        dt.date(2025, 12, 31),
        Decimal("0.25000"),
        "USD",
    )


def test_information_and_empty_sheets_are_skipped(tmp_path: Path) -> None:
    """A sheet without an ISIN header, or with nothing on it, adds no rows."""
    file = write_xlsx_sheets(
        tmp_path / "vanguard-Excess_Reportable_Income_2025.xlsx",
        {
            "Report information": [
                ["Report information", None],
                ["Fund", "Vanguard US ETFs"],
                ["Period ended", "31 January 2025"],
            ],
            "31 Jan 2025": [*TITLE, HEADER, ["US9220426764", JAN_PERIOD, "USD", "0.5"]],
            "Sheet3": [],
        },
    )

    result = VanguardImporter().parse(file)

    assert result is not None
    assert len(result.transactions) == 1
    check_transaction(
        result.transactions[0],
        "US9220426764",
        dt.date(2025, 1, 31),
        Decimal("0.50000"),
        "USD",
    )


def test_malformed_data_sheet_after_a_valid_one_is_refused(tmp_path: Path) -> None:
    """A bad row on a later data sheet still fails the whole workbook."""
    file = write_xlsx_sheets(
        tmp_path / "vanguard-Excess_Reportable_Income_2025.xlsx",
        {
            "31 Jan 2025": [*TITLE, HEADER, ["US9220426764", JAN_PERIOD, "USD", "1"]],
            "31 Dec 2025": [*TITLE, HEADER, ["NOTANISIN", DEC_PERIOD, "USD", "1"]],
        },
    )

    with pytest.raises(ParsingError, match="Not valid ISIN NOTANISIN"):
        VanguardImporter().parse(file)


def test_too_many_isin_columns(tmp_path: Path) -> None:
    """Raise when more tables are found than expected."""
    file = write_xlsx(
        tmp_path / "vanguard-eri-2024.xlsx",
        [
            ["Vanguard Excess Reportable Income", None, None, None],
            HEADER,
            ["IE00B3RBWM25", PERIOD, "USD", "1.23456"],
            HEADER,
            ["IE00BK5BQV03", PERIOD, "USD", "2.34567"],
            HEADER,
            ["IE00B4L5Y983", PERIOD, "USD", "3.45678"],
        ],
    )

    with pytest.raises(ParsingError, match="Too many ISIN columns"):
        VanguardImporter().parse(file)


def test_non_string_currency(tmp_path: Path) -> None:
    """Raise on rows where the currency is not text."""
    file = write_xlsx(
        tmp_path / "vanguard-eri-2024.xlsx",
        [HEADER, ["IE00B3RBWM25", PERIOD, 123, "1.23456"]],
    )

    with pytest.raises(ParsingError, match="Not valid Currency"):
        VanguardImporter().parse(file)


def test_missing_columns(tmp_path: Path) -> None:
    """Raise when expected columns are missing."""
    file = write_xlsx(
        tmp_path / "vanguard-eri-2024.xlsx",
        [
            ["ISIN", "Currency"],
            ["IE00B3RBWM25", "USD"],
        ],
    )

    with pytest.raises(ParsingError, match="Cannot process ERI columns"):
        VanguardImporter().parse(file)


def test_invalid_isin(tmp_path: Path) -> None:
    """Raise on rows with an invalid ISIN."""
    file = write_xlsx(
        tmp_path / "vanguard-eri-2024.xlsx",
        [HEADER, ["NOTANISIN", PERIOD, "USD", "1.23456"]],
    )

    with pytest.raises(ParsingError, match="Not valid ISIN"):
        VanguardImporter().parse(file)


def test_invalid_currency(tmp_path: Path) -> None:
    """Raise on rows with a malformed currency code."""
    file = write_xlsx(
        tmp_path / "vanguard-eri-2024.xlsx",
        [HEADER, ["IE00B3RBWM25", PERIOD, "QQ", "1.23456"]],
    )

    with pytest.raises(ParsingError, match="Not valid Currency"):
        VanguardImporter().parse(file)


def test_pence_currency_code_is_not_read_as_pounds(tmp_path: Path) -> None:
    """A pence code such as GBp is refused, not upper-cased into pounds."""
    file = write_xlsx(
        tmp_path / "vanguard-eri-2024.xlsx",
        [HEADER, ["IE00B3RBWM25", PERIOD, "GBp", "1.23456"]],
    )

    with pytest.raises(ParsingError, match="Not valid Currency"):
        VanguardImporter().parse(file)


def test_invalid_reporting_period(tmp_path: Path) -> None:
    """Raise on rows with an unparsable reporting period."""
    file = write_xlsx(
        tmp_path / "vanguard-eri-2024.xlsx",
        [HEADER, ["IE00B3RBWM25", "unknown", "USD", "1.23456"]],
    )

    with pytest.raises(ParsingError, match="Not valid Reporting period"):
        VanguardImporter().parse(file)


def test_invalid_amount(tmp_path: Path) -> None:
    """Raise on rows with an unparsable ERI amount."""
    file = write_xlsx(
        tmp_path / "vanguard-eri-2024.xlsx",
        [HEADER, ["IE00B3RBWM25", PERIOD, "USD", "abc"]],
    )

    with pytest.raises(ParsingError, match="Not valid ERI amount"):
        VanguardImporter().parse(file)
