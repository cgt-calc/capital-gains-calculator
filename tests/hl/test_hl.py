"""Tests for Hargreaves Lansdown parser."""

from decimal import Decimal
import io
from pathlib import Path
import shutil
import subprocess

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers.hl import HargreavesLansdownParser
from tests.utils import (
    assert_stdout_matches,
    build_cmd,
    report_path,
    run_cli,
    stderr_alerts,
)

HL_CSV_HEADER = (
    "Transaction Summary, , , ,\n"
    "Client Name:,Anonymous User, , ,\n"
    "Client Number:,1234567, , ,\n"
    "Valuation as at,09-08-2026 16:40, , ,\n"
    "\n"
    "Trade date,Settle date,Reference,Description,Unit cost (p),Quantity,Value (£),\n"
)


def _create_buy_pdf(filename: str) -> None:
    """Generate a mock HL Buy Contract Note PDF."""
    c = canvas.Canvas(filename, pagesize=letter)
    _, height = letter

    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, height - 50, "Hargreaves Lansdown Contract Note")

    c.setFont("Helvetica", 10)
    c.drawString(50, height - 75, "Date 24/02/2026")
    c.drawString(50, height - 90, "Contract Note No. B302087054-02508682")

    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, height - 120, "**BOUGHT**")

    c.setFont("Helvetica", 10)
    c.drawString(50, height - 150, "ISIN: IE00BK5BQV03")
    c.drawString(50, height - 165, "STOCK CODE: VHVG")
    c.drawString(50, height - 180, "Security: Vanguard FTSE Developed World UCITS ETF")

    # Quantity | Price | Consideration
    c.drawString(50, height - 220, "4,563.00 | 3287.199 | 149,994.89")
    c.drawString(50, height - 260, "Dealing charge || 11.95")
    c.drawString(50, height - 290, "Settlement Date: 26/02/2026")

    c.save()


def _create_sell_pdf(filename: str) -> None:
    """Generate a mock HL Sell Contract Note PDF."""
    c = canvas.Canvas(filename, pagesize=letter)
    _, height = letter

    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, height - 50, "Hargreaves Lansdown Contract Note")

    c.setFont("Helvetica", 10)
    c.drawString(50, height - 75, "Date 25/02/2026")
    c.drawString(50, height - 90, "Contract Note No. S302087055-04500122")

    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, height - 120, "**SOLD**")

    c.setFont("Helvetica", 10)
    c.drawString(50, height - 150, "ISIN: IE00BK5BQV03")
    c.drawString(50, height - 165, "STOCK CODE: VHVG")
    c.drawString(50, height - 180, "Security: Vanguard FTSE Developed World UCITS ETF")

    # Quantity | Price | Consideration
    c.drawString(50, height - 220, "1,000.00 | 4500.00 | 45,000.00")
    c.drawString(50, height - 260, "Dealing charge || 11.95")
    c.drawString(50, height - 290, "Settlement Date: 27/02/2026")

    c.save()


def _prepare_pdf_test_data() -> str:
    test_dir = Path(__file__).resolve().parent

    source_file = Path(test_dir / "data" / "inputs" / "hl-transaction-summary.csv")

    # Use a stable repo-relative directory: its path is echoed in the parsing
    # output, so a machine-specific temp dir would break the expected-output
    # fixture on any other machine.
    input_dir = Path("out") / "test-hl-inputs"
    input_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source_file, input_dir)

    _create_buy_pdf(str(input_dir / "B302087054_BOUGHT_Vanguard.pdf"))
    _create_sell_pdf(str(input_dir / "S302087055_SOLD_Vanguard.pdf"))

    return str(input_dir)


def test_hl_parser(request: pytest.FixtureRequest) -> None:
    """Runs the tool and verifies it doesn't fail."""

    test_output_dir = _prepare_pdf_test_data()
    cmd = build_cmd(
        "--year",
        "2025",
        "--hl-dir",
        test_output_dir,
        "--no-balance-check",
        "--output",
        report_path(request),
    )
    result = run_cli(cmd)
    assert stderr_alerts(result.stderr) == [], "Run with example files generated errors"
    expected_file = (
        Path("tests")
        / "hl"
        / "data"
        / "test_run_with_hl_files_no_balance_check_output.txt"
    )
    assert_stdout_matches(result, cmd, expected_file)


def test_hl_parser_missing_pdf(request: pytest.FixtureRequest) -> None:
    """A trade whose contract note PDF is missing stops the run."""
    cmd = build_cmd(
        "--year",
        "2025",
        "--hl-dir",
        "tests/hl/data/inputs/",
        "--no-balance-check",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    assert result.returncode, "Test succeeded but expected failure"
    assert "Cannot find contract note pdf" in result.stderr, (
        "Test failed with unexpected error"
    )


def test_hl_unknown_reference_raises(tmp_path: Path) -> None:
    """Raise ParsingError instead of silently dropping unrecognized rows.

    HL statements routinely contain rows such as dividends or corporate
    actions whose "Reference" code isn't a Buy/Sell reference, INTEREST,
    FPC or MANAGE FEE. Previously these rows were silently skipped, which
    meant real dividend income could vanish from a user's report without
    any indication anything was wrong.
    """
    csv_file = tmp_path / "hl-transaction-summary.csv"
    csv_file.write_text(
        HL_CSV_HEADER
        + '"04/04/2026","04/04/2026","D302087099","Vanguard FTSE Developed World '
        'UCITS ETF Dividend","n/a","n/a","12.34"\n',
        encoding="windows-1252",
    )

    with pytest.raises(ParsingError) as exc:
        HargreavesLansdownParser().load_from_file(csv_file)

    message = str(exc.value)
    assert "Unknown transaction type" in message
    assert "D302087099" in message


def test_hl_blank_reference_skipped(tmp_path: Path) -> None:
    """Rows with a blank reference (e.g. subtotal/summary lines) are skipped."""
    csv_file = tmp_path / "hl-transaction-summary.csv"
    csv_file.write_text(
        HL_CSV_HEADER + '"","","","Balance carried forward","","","0.00"\n',
        encoding="windows-1252",
    )

    transactions = HargreavesLansdownParser().load_from_file(
        csv_file, warn_on_empty=False
    )

    assert transactions == []


def test_load_from_dir_accepts_uppercase_csv_extension(tmp_path: Path) -> None:
    """Discover an HL transaction report whose CSV extension is uppercase."""
    csv_file = tmp_path / "hl-transaction-summary.CSV"
    csv_file.write_text(
        HL_CSV_HEADER
        + '"01/03/2026","01/03/2026","Interest","Gross interest","n/a","n/a","12.34"\n',
        encoding="windows-1252",
    )

    transactions = HargreavesLansdownParser.load_from_dir(tmp_path)

    assert len(transactions) == 1
    assert transactions[0].action == ActionType.INTEREST
    assert transactions[0].amount == Decimal("12.34")


def test_real_preamble_preserves_physical_row_numbers(tmp_path: Path) -> None:
    """Count HL's preamble when recording transaction source lines."""
    csv_file = tmp_path / "hl-transaction-summary.csv"
    shutil.copy2(
        Path("tests/hl/data/inputs/hl-transaction-summary.csv"),
        csv_file,
    )
    _create_buy_pdf(str(tmp_path / "B302087054_BOUGHT_Vanguard.pdf"))
    _create_sell_pdf(str(tmp_path / "S302087055_SOLD_Vanguard.pdf"))

    transactions = HargreavesLansdownParser.load_from_file(csv_file)

    rows = {
        transaction.date.isoformat(): transaction.source.row
        for transaction in transactions
        if transaction.source
    }
    assert rows["2026-04-04"] == 7
    assert rows["2026-03-09"] == 8


# The tests below call the parser directly: the end-to-end tests above
# run in a subprocess, which is invisible to coverage.


def test_read_row_interest() -> None:
    """Read an interest row without a contract note."""
    row = {
        "Reference": "Interest",
        "Description": "Gross interest",
        "Trade date": "01/03/2026",
        "Value (£)": "12.34",
    }

    transaction = HargreavesLansdownParser.read_row(row, Path("statement.csv"))

    assert transaction is not None
    assert transaction.action == ActionType.INTEREST
    assert transaction.amount == Decimal("12.34")
    assert transaction.currency == "GBP"
    assert transaction.fees == Decimal(0)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "not-a-number"])
def test_read_row_unreadable_value(value: str) -> None:
    """Refuse a value column that is not a finite amount, naming the column."""
    row = {
        "Reference": "Interest",
        "Description": "Gross interest",
        "Trade date": "01/03/2026",
        "Value (\u00a3)": value,
    }

    with pytest.raises(ValueError, match=r"Invalid decimal in column 'Value"):
        HargreavesLansdownParser.read_row(row, Path("statement.csv"))


def test_read_row_transfer_with_na_value() -> None:
    """Read a fee row where the value column is n/a."""
    row = {
        "Reference": "MANAGE FEE",
        "Description": "Management fee",
        "Trade date": "01/03/2026",
        "Value (£)": "n/a",
    }

    transaction = HargreavesLansdownParser.read_row(row, Path("statement.csv"))

    assert transaction is not None
    assert transaction.action == ActionType.TRANSFER
    assert transaction.amount == Decimal(0)


def test_read_row_blank_reference_returns_none() -> None:
    """Skip rows without a reference."""
    row = {"Reference": " ", "Description": "x", "Trade date": "01/03/2026"}

    assert HargreavesLansdownParser.read_row(row, Path("statement.csv")) is None


def test_read_row_invalid_date() -> None:
    """Raise on rows with an invalid trade date."""
    row = {
        "Reference": "Interest",
        "Description": "x",
        "Trade date": "garbage",
        "Value (£)": "1",
    }

    with pytest.raises(ParsingError, match="Invalid date format"):
        HargreavesLansdownParser.read_row(row, Path("statement.csv"))


def test_read_row_buy_with_uppercase_contract_note_extension(tmp_path: Path) -> None:
    """Cross-reference a buy row with an uppercase PDF extension."""
    _create_buy_pdf(str(tmp_path / "B123_contract.PDF"))
    row = {
        "Reference": "B123",
        "Description": "Buy VHVG",
        "Trade date": "24/02/2026",
        "Value (£)": "149,994.89",
    }

    transaction = HargreavesLansdownParser.read_row(row, tmp_path / "statement.csv")

    assert transaction is not None
    assert transaction.action == ActionType.BUY
    assert transaction.symbol == "VHVG"
    assert transaction.isin == "IE00BK5BQV03"
    assert transaction.quantity == Decimal("4563.00")
    # Prices in the contract note are in pence.
    assert transaction.price == Decimal("32.87199")
    assert transaction.fees == Decimal("11.95")


def test_read_row_sell_without_contract_note(tmp_path: Path) -> None:
    """Raise when the contract note PDF is missing."""
    row = {
        "Reference": "S99",
        "Description": "Sell VHVG",
        "Trade date": "24/02/2026",
        "Value (£)": "100.00",
    }

    with pytest.raises(ParsingError, match="Cannot find contract note"):
        HargreavesLansdownParser.read_row(row, tmp_path / "statement.csv")


def test_read_transactions_reverses_order() -> None:
    """Return rows in reverse file order."""
    content = HL_CSV_HEADER + (
        "02/03/2026,02/03/2026,Interest,Later interest,n/a,n/a,20.00,\n"
        "01/03/2026,01/03/2026,Interest,Earlier interest,n/a,n/a,10.00,\n"
    )

    transactions = HargreavesLansdownParser.read_transactions(
        io.StringIO(content), Path("statement.csv")
    )

    assert [transaction.description for transaction in transactions] == [
        "Earlier interest",
        "Later interest",
    ]


def test_pre_reading_missing_header() -> None:
    """Raise when the trade date header cannot be found."""
    file = io.StringIO("no,header,here\n1,2,3\n")

    with pytest.raises(ParsingError, match="Could not find the 'Trade date' header"):
        list(HargreavesLansdownParser.pre_reading(file, Path("statement.csv")))
