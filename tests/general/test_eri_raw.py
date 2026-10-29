"""Tests for ERI raw parser error handling."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.args_parser import create_parser
from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.eri.raw import ERIRawParser
from tests.utils import build_cmd, report_path, run_cli

if TYPE_CHECKING:
    from pathlib import Path

HEADER = (
    "ISIN,Fund Reporting Period End Date,Currency,"
    "Excess of reporting income over distribution\n"
)
VALID_ISIN = "US5949181045"
INVALID_ISIN = "US1234567890"


def test_eri_raw_file_option_adds_its_rows_to_the_bundled_ones(tmp_path: Path) -> None:
    """--eri-raw-file is read as well as the fund data cgt-calc ships with."""
    file_path = tmp_path / "eri.csv"
    file_path.write_text(
        HEADER + f"{VALID_ISIN},01/02/2023,USD,1.23\n", encoding="utf8"
    )
    args = create_parser().parse_args(["--eri-raw-file", str(file_path)])

    transactions = ERIRawParser.load_from_args(args)

    given = [t for t in transactions if t.source and t.source.file == file_path]
    assert [(t.isin, t.date, t.price) for t in given] == [
        (VALID_ISIN, dt.date(2023, 2, 1), Decimal("1.23"))
    ]
    assert len(transactions) > len(given)


@pytest.mark.parametrize(
    ("bought", "sold", "year", "eri_lines", "allowable_costs", "loss"),
    [
        # Held at 31/10/2023 only: 10 x 34.5541 EUR at HMRC's October 2023
        # rate of 1.1555 is £299.04, paid on 30/04/2024, in 2024/25.
        pytest.param(
            "2023-10-02",
            "2024-06-03",
            "2024",
            ["CSH2: £299.04 (included as dividend)"],
            "£12,109.04",
            "£9.04",
            id="2024-25",
        ),
        # Held at 31/10/2024, 07/03/2025 and 30/09/2025, all paid in 2025/26:
        # 10 x 60.7270 / 1.1876 = £511.34, 10 x 25.0609 / 1.2073 = £207.58
        # and 10 x 32.5745 / 1.1573 = £281.47, at HMRC's rates for October
        # 2024, March 2025 and September 2025; £1,000.39 in all.
        pytest.param(
            "2024-10-01",
            "2025-11-03",
            "2025",
            [
                "CSH2: £511.34 (included as dividend)",
                "CSH2: £207.58 (included as dividend)",
                "CSH2: £281.47 (included as dividend)",
            ],
            "£12,810.39",
            "£710.39",
            id="2025-26",
        ),
    ],
)
def test_bundled_csh2_eri_is_converted_from_euros(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    bought: str,
    sold: str,
    year: str,
    eri_lines: list[str],
    allowable_costs: str,
    loss: str,
) -> None:
    """Amundi CSH2's bundled ERI from 2022 is read as euros, as KPMG states it.

    KPMG Reporting Funds gives LU1230136894's excess reportable income in EUR
    from the period ending 31 October 2022 (#1332). Ten units are bought at
    1,180.00 and sold at 1,210.00, each with a 5.00 fee, so the costs are
    11,810.00 plus the ERI, against proceeds of 12,100.00. Read as pounds,
    the four euro amounts would be between 16% and 21% higher.
    """
    raw = tmp_path / "raw.csv"
    raw.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        f"{bought},TRANSFER,,1,11805.00,0.00,GBP\n"
        f"{bought},BUY,CSH2,10,1180.00,5.00,GBP\n"
        f"{sold},SELL,CSH2,10,1210.00,5.00,GBP\n",
        encoding="utf-8",
    )

    result = run_cli(
        build_cmd(
            "--year", year, "--raw-file", str(raw), "--output", report_path(request)
        )
    )

    lines = [line.strip() for line in result.stdout.splitlines()]
    assert [line for line in lines if line.startswith("CSH2:")] == eri_lines
    assert f"Allowable costs:    {allowable_costs}" in lines
    assert any(line.startswith("Loss:") and line.endswith(f" {loss}") for line in lines)


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
