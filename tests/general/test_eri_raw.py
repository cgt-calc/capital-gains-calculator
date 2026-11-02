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
    ("bought", "sold", "year", "eri_lines", "allowable_costs", "outcome"),
    [
        # Held at 31/10/2020 only, a period KPMG states in pounds: 10 x 2.3578
        # is £23.58, paid on 30/04/2021, in 2021/22, with no conversion.
        pytest.param(
            "2020-10-01",
            "2021-06-01",
            "2021",
            ["CSH2: £23.58 (included as dividend)"],
            "£11,833.58",
            ("Gain:", "£266.42"),
            id="2021-22-pounds",
        ),
        # Held at 31/10/2023 only: 10 x 34.5541 EUR at HMRC's rate of 1.1707
        # for 30/04/2024, its distribution date, in 2024/25, is £295.16.
        pytest.param(
            "2023-10-02",
            "2024-06-03",
            "2024",
            ["CSH2: £295.16 (included as dividend)"],
            "£12,105.16",
            ("Loss:", "£5.16"),
            id="2024-25",
        ),
        # Held at 31/10/2024, 07/03/2025 and 30/09/2025, all paid in 2025/26:
        # 10 x 60.7270 / 1.1897 = £510.44, 10 x 25.0609 / 1.1573 = £216.55
        # and 10 x 32.5745 / 1.1469 = £284.02, at HMRC's rates for their
        # distribution dates, 30/04/2025, 07/09/2025 and 30/03/2026; £1,011.01
        # in all.
        pytest.param(
            "2024-10-01",
            "2025-11-03",
            "2025",
            [
                "CSH2: £510.44 (included as dividend)",
                "CSH2: £216.55 (included as dividend)",
                "CSH2: £284.02 (included as dividend)",
            ],
            "£12,821.01",
            ("Loss:", "£721.01"),
            id="2025-26",
        ),
    ],
)
def test_bundled_csh2_eri_is_read_in_the_currency_kpmg_states(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    bought: str,
    sold: str,
    year: str,
    eri_lines: list[str],
    allowable_costs: str,
    outcome: tuple[str, str],
) -> None:
    """Amundi CSH2's bundled ERI is in pounds to 2021 and in euros from 2022.

    KPMG Reporting Funds gives LU1230136894's excess reportable income in GBP
    for the periods to 31 October 2021 and in EUR from the period ending
    31 October 2022 (#1332). Ten units are bought at 1,180.00 and sold at
    1,210.00, each with a 5.00 fee, so the costs are 11,810.00 plus the ERI,
    against proceeds of 12,100.00. Read as pounds, the four euro amounts
    would be between 15% and 19% higher; read as euros, the pound amounts
    would be lower.
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
    label, amount = outcome
    assert any(line.startswith(label) and line.endswith(f" {amount}") for line in lines)


@pytest.mark.parametrize(
    ("periods", "eri_lines", "allowable_costs", "outcome"),
    [
        # Resident throughout, both periods count: £65.82 is income in
        # 2020/21, and the £23.58 distributed in 2021/22 is still added to cost.
        pytest.param(
            None,
            ["CSH2: £65.82 (included as dividend)"],
            "£11,899.40",
            ("Gain:", "£200.60"),
            id="resident",
        ),
        # Abroad from 30/04/2020, the first distribution date, and not back:
        # both periods are left out and the costs are the purchase alone.
        pytest.param(
            "2020-04-30:", [], "£11,810.00", ("Gain:", "£290.00"), id="left-for-good"
        ),
        # Abroad from a day later: the first period stays, the second goes.
        pytest.param(
            "2020-05-01:",
            ["CSH2: £65.82 (included as dividend)"],
            "£11,875.82",
            ("Gain:", "£224.18"),
            id="left-after-first",
        ),
        # Abroad for 2020 only: the first period goes and the second, paid
        # after coming back, stays.
        pytest.param(
            "2020-01-01:2020-12-31",
            [],
            "£11,833.58",
            ("Gain:", "£266.42"),
            id="came-back",
        ),
        # Abroad over the first period end, 31/10/2019, but back before its
        # distribution date, 30/04/2020. Only the distribution date counts
        # (reg 94(3)), so both periods stay, as for a resident.
        pytest.param(
            "2019-10-01:2020-03-31",
            ["CSH2: £65.82 (included as dividend)"],
            "£11,899.40",
            ("Gain:", "£200.60"),
            id="abroad-at-period-end-only",
        ),
    ],
)
def test_non_uk_resident_periods_drop_eri_income_and_cost_uplift(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    periods: str | None,
    eri_lines: list[str],
    allowable_costs: str,
    outcome: tuple[str, str],
) -> None:
    """ERI distributed while not UK resident is neither income nor cost.

    Ten CSH2 units are held over the periods ending 31/10/2019 and
    31/10/2020, whose ERI KPMG states in pounds: £6.5820 and £2.3578 a unit,
    distributed on 30/04/2020 and 30/04/2021. They are sold on 16/11/2020,
    before the second distribution date. Only ERI charged to UK tax is added
    to cost (Offshore Funds (Tax) Regulations 2009, reg 99(3)), so a period
    left out drops from the allowable costs as well.
    """
    raw = tmp_path / "raw.csv"
    raw.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2019-10-01,TRANSFER,,1,11805.00,0.00,GBP\n"
        "2019-10-01,BUY,CSH2,10,1180.00,5.00,GBP\n"
        "2020-11-16,SELL,CSH2,10,1210.00,5.00,GBP\n",
        encoding="utf-8",
    )
    option = [] if periods is None else ["--non-uk-resident-periods", periods]

    result = run_cli(
        build_cmd(
            "--year",
            "2020",
            "--raw-file",
            str(raw),
            *option,
            "--output",
            report_path(request),
        )
    )

    lines = [line.strip() for line in result.stdout.splitlines()]
    assert [line for line in lines if line.startswith("CSH2:")] == eri_lines
    assert f"Allowable costs:    {allowable_costs}" in lines
    label, amount = outcome
    assert any(line.startswith(label) and line.endswith(f" {amount}") for line in lines)


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
