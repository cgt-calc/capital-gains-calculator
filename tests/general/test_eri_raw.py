"""Tests for ERI raw parser error handling."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.eri.raw import ERIRawParser

if TYPE_CHECKING:
    from pathlib import Path

HEADER = (
    "ISIN,Fund Reporting Period End Date,Currency,"
    "Excess of reporting income over distribution\n"
)
VALID_ISIN = "US5949181045"
INVALID_ISIN = "US1234567890"


def test_read_eri_raw_raises_on_invalid_date(tmp_path: Path) -> None:
    """Raise ParsingError when ERI date field is invalid."""
    file_path = tmp_path / "eri.csv"
    file_path.write_text(
        HEADER + f"{VALID_ISIN},32/13/2024,USD,1.23\n",
        encoding="utf8",
    )

    with pytest.raises(ParsingError, match="Invalid date '32/13/2024'"):
        ERIRawParser.load_from_file(file_path)


@pytest.mark.parametrize("value", ["not-a-number", "NaN", "Infinity"])
def test_read_eri_raw_raises_on_invalid_decimal(tmp_path: Path, value: str) -> None:
    """Raise ParsingError when ERI decimal field is not a finite amount."""
    file_path = tmp_path / "eri.csv"
    file_path.write_text(
        HEADER + f"{VALID_ISIN},01/02/2024,USD,{value}\n",
        encoding="utf8",
    )

    with pytest.raises(ParsingError, match=f"Invalid decimal '{value}'"):
        ERIRawParser.load_from_file(file_path)


@pytest.mark.parametrize(
    "content", ["", "\n   \n,,,\n"], ids=["no bytes", "only blank rows"]
)
def test_read_eri_raw_raises_on_empty_file(tmp_path: Path, content: str) -> None:
    """Raise ParsingError when ERI file has no header row."""
    file_path = tmp_path / "eri.csv"
    file_path.write_text(content, encoding="utf8")

    with pytest.raises(ParsingError, match="ERI data file is empty"):
        ERIRawParser.load_from_file(file_path)


@pytest.mark.parametrize(
    "blank", ["", "   ", ",,,"], ids=["empty line", "only spaces", "only commas"]
)
def test_read_eri_raw_skips_a_blank_row(tmp_path: Path, blank: str) -> None:
    """A row with nothing in it is ignored, and the rows after it keep their lines."""
    file_path = tmp_path / "eri.csv"
    file_path.write_text(
        HEADER
        + f"{VALID_ISIN},01/02/2023,USD,1.23\n"
        + f"{blank}\n"
        + f"{VALID_ISIN},01/02/2024,USD,2.34\n",
        encoding="utf8",
    )

    transactions = ERIRawParser.load_from_file(file_path)

    assert [t.source.row for t in transactions if t.source] == [2, 4]


def test_read_eri_raw_reports_a_half_filled_row(tmp_path: Path) -> None:
    """A row with any cell filled is not blank: it is reported, at its own line."""
    file_path = tmp_path / "eri.csv"
    file_path.write_text(HEADER + "\n,01/02/2024,USD,1.23\n", encoding="utf8")

    with pytest.raises(ParsingError, match="Invalid ISIN value ''") as excinfo:
        ERIRawParser.load_from_file(file_path)

    assert excinfo.value.row_index == 3


@pytest.mark.parametrize(
    ("header", "row"),
    [
        (HEADER, f"{VALID_ISIN},01/02/2024,USD,1.23\n"),
        (
            (
                "Currency,Excess of reporting income over distribution,ISIN,"
                "Fund Reporting Period End Date\n"
            ),
            f"USD,1.23,{VALID_ISIN},01/02/2024\n",
        ),
    ],
    ids=["documented column order", "another column order"],
)
def test_read_eri_raw_parses_valid_row(tmp_path: Path, header: str, row: str) -> None:
    """Parse a well-formed ERI CSV row, reading each value by its column name."""
    file_path = tmp_path / "eri.csv"
    file_path.write_text(header + row, encoding="utf8")

    transactions = ERIRawParser.load_from_file(file_path)

    assert len(transactions) == 1
    entry = transactions[0]
    assert entry.isin == VALID_ISIN
    assert entry.date.isoformat() == "2024-02-01"
    assert entry.currency == "USD"
    assert entry.price == Decimal("1.23")


def test_read_eri_raw_raises_on_invalid_isin(tmp_path: Path) -> None:
    """Raise ParsingError when ISIN fails validation."""
    file_path = tmp_path / "eri.csv"
    file_path.write_text(
        HEADER + f"{INVALID_ISIN},01/02/2024,USD,1.23\n",
        encoding="utf8",
    )

    with pytest.raises(ParsingError, match=f"Invalid ISIN value '{INVALID_ISIN}'"):
        ERIRawParser.load_from_file(file_path)


@pytest.mark.parametrize(
    ("header", "match"),
    [
        pytest.param(
            "Foo,ISIN,Bar,Fund Reporting Period End Date,Currency,"
            "Excess of reporting income over distribution",
            "Unknown columns: Bar, Foo",
            id="unknown columns, in alphabetical order",
        ),
        pytest.param(
            "ISIN,Fund Reporting Period End Date,Currency",
            "Missing columns: Excess of reporting income over distribution",
            id="a column missing",
        ),
        pytest.param(
            HEADER.strip() + ",Currency",
            "Repeated columns: Currency",
            id="a column named twice",
        ),
        pytest.param(
            "ISIN,Currency,Currency,Excess of reporting income over distribution",
            "Missing columns: Fund Reporting Period End Date",
            id="one column named twice in place of another",
        ),
        # The blank line is taken for the header, so every column is missing.
        pytest.param(
            "\n" + HEADER.strip(),
            "Missing columns: ISIN, Fund Reporting Period End Date, Currency, "
            "Excess of reporting income over distribution",
            id="a blank line above the header",
        ),
    ],
)
def test_read_eri_raw_refuses_a_wrong_header(
    tmp_path: Path, header: str, match: str
) -> None:
    """A header that is not the four columns is reported, not left to its rows."""
    file_path = tmp_path / "eri.csv"
    file_path.write_text(
        f"{header}\n{VALID_ISIN},01/02/2024,USD,1.23\n", encoding="utf8"
    )

    with pytest.raises(ParsingError, match=match) as excinfo:
        ERIRawParser.load_from_file(file_path)

    assert excinfo.value.row_index == 1
