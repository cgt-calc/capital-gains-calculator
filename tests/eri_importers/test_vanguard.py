"""Tests for the Vanguard ERI importer."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.eri.importer.vanguard import VanguardImporter
from tests.eri_importers.helpers import check_transaction, write_xlsx

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


def test_unmatched_file_name_is_not_accepted(tmp_path: Path) -> None:
    """Return None for files from other providers."""
    file = write_xlsx(tmp_path / "blackrock-eri-2024.xlsx", [HEADER])

    assert VanguardImporter().parse(file) is None


def test_no_isin_column(tmp_path: Path) -> None:
    """Raise when no ISIN column can be found."""
    file = write_xlsx(
        tmp_path / "vanguard-eri-2024.xlsx",
        [
            ["Fund", "Period", "Ccy", "Amount"],
            ["Some Fund", PERIOD, "USD", "1.0"],
        ],
    )

    with pytest.raises(ParsingError, match="No ISIN column"):
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
