"""Tests for the Blackrock/iShares ERI importer."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.eri.importer.blackrock import BlackrockImporter
from tests.eri_importers.helpers import check_transaction, write_xlsx

if TYPE_CHECKING:
    from pathlib import Path

HEADER: list[object] = [
    "ISIN",
    "Reporting Period",
    "Currency",
    "Excess of Reported Income per Unit",
]
PERIOD = "01 January 2023 to 31 December 2023"
PERIOD_END = dt.date(2023, 12, 31)


def test_parse_header_in_first_row(tmp_path: Path) -> None:
    """Parse a report with the header in the first row."""
    file = write_xlsx(
        tmp_path / "blackrock-eri-2023.xlsx",
        [
            HEADER,
            ["IE00B3RBWM25", PERIOD, "USD", "1.23456"],
            ["IE00BK5BQV03", "01/01/2023 - 31/12/2023", "JPN", "Nil"],
        ],
    )

    result = BlackrockImporter().parse(file)

    assert result is not None
    assert result.output_file_name == "blackrock_eri.csv"
    assert len(result.transactions) == 2
    check_transaction(
        result.transactions[0], "IE00B3RBWM25", PERIOD_END, Decimal("1.23456"), "USD"
    )
    # JPN is a known data bug in the reports and is read as JPY,
    # Nil is read as zero.
    check_transaction(
        result.transactions[1], "IE00BK5BQV03", PERIOD_END, Decimal(0), "JPY"
    )


def test_parse_header_after_preamble_with_trailing_table(tmp_path: Path) -> None:
    """Parse a report with preamble rows and a trailing retired funds table."""
    file = write_xlsx(
        tmp_path / "ishares-eri-2023.xlsx",
        [
            ["Excess Reportable Income Report", None, None, None],
            [None, None, None, None],
            HEADER,
            ["IE00B3RBWM25", PERIOD, "USD", "1.23456"],
            [None, None, None, None],
            HEADER,
            ["IE00BK5BQV03", PERIOD, "USD", "9.99999"],
        ],
    )

    result = BlackrockImporter().parse(file)

    assert result is not None
    assert result.output_file_name == "ishares_eri.csv"
    # The trailing table is for retired funds and is skipped.
    assert len(result.transactions) == 1
    check_transaction(
        result.transactions[0], "IE00B3RBWM25", PERIOD_END, Decimal("1.23456"), "USD"
    )


def test_unmatched_file_name_is_not_accepted(tmp_path: Path) -> None:
    """Return None for files from other providers."""
    file = write_xlsx(tmp_path / "fidelity-eri-2023.xlsx", [HEADER])

    assert BlackrockImporter().parse(file) is None


def test_no_isin_column(tmp_path: Path) -> None:
    """Raise when no ISIN column can be found."""
    file = write_xlsx(
        tmp_path / "blackrock-eri-2023.xlsx",
        [
            ["Fund", "Period", "Ccy", "Amount"],
            ["Some Fund", PERIOD, "USD", "1.0"],
        ],
    )

    with pytest.raises(ParsingError, match="No ISIN column"):
        BlackrockImporter().parse(file)


def test_too_many_isin_columns(tmp_path: Path) -> None:
    """Raise when more tables are found than expected."""
    file = write_xlsx(
        tmp_path / "blackrock-eri-2023.xlsx",
        [
            ["Excess Reportable Income Report", None, None, None],
            HEADER,
            ["IE00B3RBWM25", PERIOD, "USD", "1.23456"],
            HEADER,
            ["IE00BK5BQV03", PERIOD, "USD", "2.34567"],
            HEADER,
            ["IE00B4L5Y983", PERIOD, "USD", "3.45678"],
        ],
    )

    with pytest.raises(ParsingError, match="Too many ISIN columns"):
        BlackrockImporter().parse(file)


def test_missing_columns(tmp_path: Path) -> None:
    """Raise when expected columns are missing."""
    file = write_xlsx(
        tmp_path / "blackrock-eri-2023.xlsx",
        [
            ["ISIN", "Currency"],
            ["IE00B3RBWM25", "USD"],
        ],
    )

    with pytest.raises(ParsingError, match="Cannot process ERI columns"):
        BlackrockImporter().parse(file)


def test_invalid_isin(tmp_path: Path) -> None:
    """Raise on rows with an invalid ISIN."""
    file = write_xlsx(
        tmp_path / "blackrock-eri-2023.xlsx",
        [HEADER, ["NOTANISIN", PERIOD, "USD", "1.23456"]],
    )

    with pytest.raises(ParsingError, match="Not valid ISIN"):
        BlackrockImporter().parse(file)


def test_invalid_currency(tmp_path: Path) -> None:
    """Raise on rows with a malformed currency code."""
    file = write_xlsx(
        tmp_path / "blackrock-eri-2023.xlsx",
        [HEADER, ["IE00B3RBWM25", PERIOD, "QQ", "1.23456"]],
    )

    with pytest.raises(ParsingError, match="Not valid Currency"):
        BlackrockImporter().parse(file)


def test_pence_currency_code_is_not_read_as_pounds(tmp_path: Path) -> None:
    """A pence code such as GBp is refused, not upper-cased into pounds."""
    file = write_xlsx(
        tmp_path / "blackrock-eri-2023.xlsx",
        [HEADER, ["IE00B3RBWM25", PERIOD, "GBp", "1.23456"]],
    )

    with pytest.raises(ParsingError, match="Not valid Currency"):
        BlackrockImporter().parse(file)


def test_non_string_currency(tmp_path: Path) -> None:
    """Raise on rows where the currency is not text."""
    file = write_xlsx(
        tmp_path / "blackrock-eri-2023.xlsx",
        [HEADER, ["IE00B3RBWM25", PERIOD, 123, "1.23456"]],
    )

    with pytest.raises(ParsingError, match="Not valid Currency"):
        BlackrockImporter().parse(file)


def test_invalid_reporting_period(tmp_path: Path) -> None:
    """Raise on rows with an unparsable reporting period."""
    file = write_xlsx(
        tmp_path / "blackrock-eri-2023.xlsx",
        [HEADER, ["IE00B3RBWM25", "unknown", "USD", "1.23456"]],
    )

    with pytest.raises(ParsingError, match="Not valid Reporting period"):
        BlackrockImporter().parse(file)


def test_invalid_amount(tmp_path: Path) -> None:
    """Raise on rows with an unparsable ERI amount."""
    file = write_xlsx(
        tmp_path / "blackrock-eri-2023.xlsx",
        [HEADER, ["IE00B3RBWM25", PERIOD, "USD", "abc"]],
    )

    with pytest.raises(ParsingError, match="Not valid ERI amount"):
        BlackrockImporter().parse(file)
