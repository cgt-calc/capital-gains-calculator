"""Test Schwab parser."""

import datetime
from decimal import Decimal
import io
from pathlib import Path
import subprocess

import pytest

from cgt_calc.exceptions import ParsingError, SymbolMissingError
from cgt_calc.model import ActionType, BrokerTransaction
from cgt_calc.parsers.schwab import (
    AwardPrices,
    SchwabParser,
    _read_schwab_awards,
    action_from_str,
)
from tests.utils import build_cmd, report_path, stderr_alerts


@pytest.fixture(autouse=True)
def _reset_awards_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep changes to the class-level award prices out of other test modules."""
    monkeypatch.setattr(SchwabParser, "awards_prices", AwardPrices(award_prices={}))


def test_missing_award_file_reports_the_vest_it_cannot_price() -> None:
    """A vest without a price is the only thing the award file is needed for.

    The runs below parse files that never need it and stay silent; this one
    reaches a Stock Plan Activity row and has nothing to price it with.
    """
    path = Path("tests") / "schwab" / "data" / "rsu_settlement" / "transactions.csv"
    SchwabParser.awards_prices = AwardPrices(award_prices={})

    with (
        path.open(encoding="utf-8") as csv_file,
        pytest.raises(ParsingError) as exc_info,
    ):
        SchwabParser.read_transactions(csv_file, path)

    message = str(exc_info.value)
    assert "no Schwab Award file was provided" in message
    assert "--schwab-award-file" in message
    # The row that needs the file is named, so it can be found in the statement.
    assert "BAR" in message
    assert "2023-08-18" in message


def test_award_rows_overlapping_in_a_column_are_reported(tmp_path: Path) -> None:
    """A split award row whose halves both fill a column is malformed input."""
    award_file = tmp_path / "awards.csv"
    award_file.write_text(
        "Date,Action,Symbol,Description,Quantity,FeesAndCommissions,"
        "DisbursementElection,Amount,AwardDate,AwardId,FairMarketValuePrice,"
        "SalePrice,SharesSoldWithheldForTaxes,NetSharesDeposited,Taxes\n"
        "08/15/2023,Lapse,BAR,Restricted Stock Lapse,400,,,,,,,,,,\n"
        ',,,,400,,,,03/21/2022,101883189,$140.35,,200,200,"$13,192.90"\n'
    )

    with pytest.raises(ParsingError, match="contain data in column 5") as exc_info:
        _read_schwab_awards(award_file)

    assert exc_info.value.row_index == 2


def test_an_empty_award_file_is_reported(tmp_path: Path) -> None:
    """Reading a path directly still names the empty file.

    The CLI classifies the contents first and never reaches this, but
    _read_schwab_awards is called with a path too, and the guard is what keeps
    an empty file from surfacing as IndexError on the header lookup.
    """
    award_file = tmp_path / "awards.csv"
    award_file.write_text("")

    with pytest.raises(ParsingError, match="Award CSV file is empty"):
        _read_schwab_awards(award_file)


def test_an_award_file_without_the_price_column_is_reported(tmp_path: Path) -> None:
    """And a header lacking FairMarketValuePrice, rather than a KeyError."""
    award_file = tmp_path / "awards.csv"
    award_file.write_text("Date,Symbol,Quantity\n08/15/2023,BAR,400\n")

    with pytest.raises(ParsingError, match="Missing columns in Schwab Award file"):
        _read_schwab_awards(award_file)


@pytest.mark.parametrize("price", ["NaN", "$Infinity", "$not-a-number"])
def test_award_price_that_is_not_a_finite_amount_is_reported(
    tmp_path: Path, price: str
) -> None:
    """An unreadable award price names the file and row, not a late traceback.

    The award file is read outside the CSV parser, so a bad price used to
    reach the matching engine and fail there with nothing to point at.
    """
    award_file = tmp_path / "awards.csv"
    award_file.write_text(
        "Date,Action,Symbol,Description,Quantity,FeesAndCommissions,"
        "DisbursementElection,Amount,AwardDate,AwardId,FairMarketValuePrice,"
        "SalePrice,SharesSoldWithheldForTaxes,NetSharesDeposited,Taxes\n"
        "08/15/2023,Lapse,BAR,Restricted Stock Lapse,400,,,,,,,,,,\n"
        f',,,,,,,,03/21/2022,101883189,{price},,200,200,"$13,192.90"\n'
    )

    with pytest.raises(
        ParsingError, match="Invalid decimal in column 'FairMarketValuePrice'"
    ) as exc_info:
        _read_schwab_awards(award_file)

    # The price is stated on the lower half of the split award row, so the
    # row to correct is 3: row 1 is the header and row 2 holds the upper half.
    assert exc_info.value.row_index == 3


def test_award_price_with_thousands_separators_is_parsed(tmp_path: Path) -> None:
    """Schwab groups prices over 999 with commas, such as $1,250.00."""
    award_file = tmp_path / "awards.csv"
    award_file.write_text(
        "Date,Action,Symbol,Description,Quantity,FeesAndCommissions,"
        "DisbursementElection,Amount,AwardDate,AwardId,FairMarketValuePrice,"
        "SalePrice,SharesSoldWithheldForTaxes,NetSharesDeposited,Taxes\n"
        "08/15/2023,Lapse,BAR,Restricted Stock Lapse,400,,,,,,,,,,\n"
        ',,,,,,,,03/21/2022,101883189,"$1,250.00",,200,200,"$13,192.90"\n'
    )

    awards = _read_schwab_awards(award_file)

    assert awards.award_prices[datetime.date(2023, 8, 15)]["BAR"] == Decimal("1250.00")


def test_award_file_without_the_award_says_so() -> None:
    """A file that lacks this particular award is a different mistake."""
    path = Path("tests") / "schwab" / "data" / "rsu_settlement" / "transactions.csv"
    SchwabParser.awards_prices = AwardPrices(
        award_prices={datetime.date(2020, 1, 1): {"FOO": Decimal(1)}}
    )

    with (
        path.open(encoding="utf-8") as csv_file,
        pytest.raises(ParsingError) as exc_info,
    ):
        SchwabParser.read_transactions(csv_file, path)

    assert "has no award price for it" in str(exc_info.value)


def test_run_with_schwab_example_2023_files(request: pytest.FixtureRequest) -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-file",
        "tests/schwab/data/2023/transactions.csv",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert stderr_alerts(result.stderr) == [], "Unexpected stderr message"
    expected_file = Path("tests") / "schwab" / "data" / "2023" / "expected_output.txt"
    expected = expected_file.read_text(encoding="utf-8")
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_run_with_schwab_cash_merger_files(request: pytest.FixtureRequest) -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2020",
        "--schwab-file",
        "tests/schwab/data/cash_merger/transactions.csv",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    alerts = stderr_alerts(result.stderr)
    assert len(alerts) == 1
    assert alerts[0].startswith("WARNING: Cash Merger support is not complete")
    assert "FOO: 100 units on 2021-03-02 for 1000 USD (Charles Schwab)" in result.stderr
    expected_file = (
        Path("tests") / "schwab" / "data" / "cash_merger" / "expected_output.txt"
    )
    expected = expected_file.read_text(encoding="utf-8")
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_run_with_schwab_rsu_settlement_files(request: pytest.FixtureRequest) -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-file",
        "tests/schwab/data/rsu_settlement/transactions.csv",
        "--schwab-award-file",
        "tests/schwab/data/rsu_settlement/awards.csv",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert stderr_alerts(result.stderr) == []
    expected_file = (
        Path("tests") / "schwab" / "data" / "rsu_settlement" / "expected_output.txt"
    )
    expected = expected_file.read_text(encoding="utf-8")
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_run_with_schwab_bond_interest_files(request: pytest.FixtureRequest) -> None:
    """Test that Bond Interest and Credit Interest are classified as INTEREST."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-file",
        "tests/schwab/data/bond_interest/transactions.csv",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert stderr_alerts(result.stderr) == [], "Unexpected stderr message"
    expected_file = (
        Path("tests") / "schwab" / "data" / "bond_interest" / "expected_output.txt"
    )
    expected = expected_file.read_text(encoding="utf-8")
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_run_with_schwab_interest_tax_files(request: pytest.FixtureRequest) -> None:
    """Runs the script on interest withholding data and checks the output."""
    cmd = build_cmd(
        "--year",
        "2024",
        "--schwab-file",
        "tests/schwab/data/interest_tax/transactions.csv",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert stderr_alerts(result.stderr) == []
    expected_file = (
        Path("tests") / "schwab" / "data" / "interest_tax" / "expected_output.txt"
    )
    expected = expected_file.read_text(encoding="utf-8")
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


SCHWAB_HEADER = "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"


def _read(content: str) -> list[BrokerTransaction]:
    """Parse a Schwab CSV from a string."""
    return SchwabParser.read_transactions(
        io.StringIO(content), Path("transactions.csv")
    )


@pytest.mark.parametrize(
    ("label", "action"),
    [
        ("Buy", ActionType.BUY),
        ("Sell", ActionType.SELL),
        ("Sell to Open", ActionType.OPTION_GRANT),
        ("Buy to Close", ActionType.OPTION_CLOSE),
        ("Expired", ActionType.OPTION_EXPIRY),
        ("Assigned", ActionType.OPTION_ASSIGNMENT),
        ("Qualified Dividend", ActionType.DIVIDEND),
        ("Cash Dividend", ActionType.DIVIDEND),
        ("Qual Div Reinvest", ActionType.DIVIDEND),
        ("Div Adjustment", ActionType.DIVIDEND),
        ("Special Qual Div", ActionType.DIVIDEND),
        ("Non-Qualified Div", ActionType.DIVIDEND),
        ("NRA Tax Adj", ActionType.DIVIDEND_TAX),
        ("NRA Withholding", ActionType.DIVIDEND_TAX),
        ("NRA Withhold", ActionType.DIVIDEND_TAX),
        ("Foreign Tax Paid", ActionType.DIVIDEND_TAX),
        ("ADR Mgmt Fee", ActionType.FEE),
        ("Adjustment", ActionType.ADJUSTMENT),
        ("IRS Withhold Adj", ActionType.ADJUSTMENT),
        ("Wire Funds Adj", ActionType.ADJUSTMENT),
        ("Short Term Cap Gain", ActionType.CAPITAL_GAIN),
        ("Long Term Cap Gain", ActionType.CAPITAL_GAIN),
        ("Spin-off", ActionType.SPIN_OFF),
        ("Credit Interest", ActionType.INTEREST),
        ("Bond Interest", ActionType.INTEREST),
        ("Reinvest Shares", ActionType.REINVEST_SHARES),
        ("Reinvest Dividend", ActionType.REINVEST_DIVIDENDS),
        ("Wire Funds Received", ActionType.WIRE_FUNDS_RECEIVED),
        ("Stock Split", ActionType.STOCK_SPLIT),
        ("Cash Merger", ActionType.CASH_MERGER),
        ("Cash Merger Adj", ActionType.CASH_MERGER),
        ("Full Redemption", ActionType.FULL_REDEMPTION),
        ("Full Redemption Adj", ActionType.FULL_REDEMPTION),
    ],
)
def test_action_from_str(label: str, action: ActionType) -> None:
    """Map every known Schwab action label."""
    assert action_from_str(label, Path("transactions.csv")) == action


def test_action_from_str_unknown() -> None:
    """Raise on unknown action labels."""
    with pytest.raises(ParsingError, match="Unknown action"):
        action_from_str("Dance", Path("transactions.csv"))


def test_read_transactions_empty_file() -> None:
    """Raise on empty transaction files."""
    with pytest.raises(ParsingError, match="file is empty"):
        _read("")


def test_read_transactions_missing_columns() -> None:
    """Raise when required columns are missing."""
    with pytest.raises(ParsingError, match="Missing columns"):
        _read("Date,Action\n01/15/2023,Sell\n")


def test_read_transactions_skips_blank_lines() -> None:
    """Skip blank lines in the file."""
    content = SCHWAB_HEADER + ",,,,,,,\n"
    assert _read(content) == []


def test_read_transactions_wrong_column_count() -> None:
    """Raise when a row has a different number of columns."""
    content = SCHWAB_HEADER + "01/15/2023,Sell\n"
    with pytest.raises(ParsingError) as excinfo:
        _read(content)
    assert excinfo.value.row_index == 2


def test_read_transactions_as_of_date() -> None:
    """Parse dates with an 'as of' suffix."""
    content = (
        SCHWAB_HEADER
        + '01/15/2023 as of 01/12/2023,Credit Interest,,Interest,,,,"$1.00"\n'
    )
    transactions = _read(content)
    assert transactions[0].date == datetime.date(2023, 1, 15)


def test_read_transactions_invalid_date() -> None:
    """Raise on unparsable dates."""
    content = SCHWAB_HEADER + "2023-01-15,Credit Interest,,Interest,,,,$1.00\n"
    with pytest.raises(ParsingError, match="Invalid date format"):
        _read(content)


def test_read_transactions_ninth_column_must_be_empty() -> None:
    """Raise when the legacy ninth column contains data."""
    header = SCHWAB_HEADER.rstrip("\n") + ",Extra\n"
    content = header + "01/15/2023,Credit Interest,,Interest,,,,$1.00,oops\n"
    with pytest.raises(ParsingError, match="should be empty"):
        _read(content)


def test_stock_activity_without_symbol() -> None:
    """Raise when a stock activity row has no symbol to price."""
    content = SCHWAB_HEADER + "01/15/2023,Stock Plan Activity,,Vest,,10,,\n"
    with pytest.raises(SymbolMissingError):
        _read(content)


def test_cash_merger_adj_without_cash_merger() -> None:
    """Raise when a Cash Merger Adj has no preceding Cash Merger."""
    content = SCHWAB_HEADER + "01/15/2023,Cash Merger Adj,FOO,Merger,,-10,,\n"
    with pytest.raises(ParsingError, match="must be preceded"):
        _read(content)


def test_cash_merger_adj_after_another_action() -> None:
    """Report a malformed pair as input data, not an internal assertion."""
    content = (
        SCHWAB_HEADER
        + "01/15/2023,Credit Interest,,Interest,,,,$1.00\n"
        + "01/15/2023,Cash Merger Adj,FOO,Merger,,-10,,\n"
    )

    with pytest.raises(ParsingError, match="must immediately follow"):
        _read(content)


def test_full_redemption_adj_before_another_action() -> None:
    """Report an unexpected second row as malformed broker data."""
    content = (
        SCHWAB_HEADER
        + "01/15/2023,Full Redemption Adj,FOO,Redemption,,,,$100.00\n"
        + "01/15/2023,Credit Interest,,Interest,,,,$1.00\n"
    )

    with pytest.raises(ParsingError, match="must be followed"):
        _read(content)


def test_invalid_cash_merger_pair() -> None:
    """Raise when a Cash Merger pair fails validation."""
    content = (
        SCHWAB_HEADER
        + '01/16/2023,Cash Merger,FOO,Merger,,,,"$100.00"\n'
        + '01/15/2023,Cash Merger Adj,FOO,Merger,,-10,,"$5.00"\n'
    )
    with pytest.raises(ParsingError, match="Invalid Cash Merger format") as exc_info:
        _read(content)

    assert "FOO on 2023-01-16" in str(exc_info.value)


@pytest.mark.parametrize(
    ("amount", "quantity", "adj_price", "adj_fees"),
    [
        # Negative proceeds would make a disposal with a negative price.
        ('"-$100.00"', "-10", "", ""),
        # A positive quantity is not shares leaving the account.
        ('"$100.00"', "10", "", ""),
        # A price on the adjustment row is not part of the format.
        ('"$100.00"', "-10", "5", ""),
    ],
)
def test_cash_merger_pair_with_invalid_values(
    amount: str, quantity: str, adj_price: str, adj_fees: str
) -> None:
    """Reject values that would corrupt the derived disposal."""
    content = (
        SCHWAB_HEADER
        + f"01/15/2023,Cash Merger,FOO,Merger,,,,{amount}\n"
        + f"01/15/2023,Cash Merger Adj,FOO,Merger,{adj_price},{quantity},{adj_fees},\n"
    )

    with pytest.raises(ParsingError, match="Invalid Cash Merger format"):
        _read(content)


@pytest.mark.parametrize(
    ("amount", "quantity", "redemption_fees"),
    [
        ('"-$100.00"', "-10", ""),
        ('"$100.00"', "10", ""),
    ],
)
def test_full_redemption_pair_with_invalid_values(
    amount: str, quantity: str, redemption_fees: str
) -> None:
    """Reject values that would corrupt the derived disposal."""
    content = (
        SCHWAB_HEADER
        + f"01/15/2023,Full Redemption Adj,FOO,Redemption,,,,{amount}\n"
        + f"01/15/2023,Full Redemption,FOO,Redemption,,{quantity},{redemption_fees},\n"
    )

    with pytest.raises(ParsingError, match="Invalid Full Redemption format"):
        _read(content)


def test_invalid_full_redemption_pair() -> None:
    """Raise when a Full Redemption pair fails validation."""
    content = (
        SCHWAB_HEADER
        + '01/16/2023,Full Redemption Adj,FOO,Redemption,,,,"$100.00"\n'
        + '01/15/2023,Full Redemption,FOO,Redemption,"$1.00",-10,,\n'
    )
    with pytest.raises(
        ParsingError, match="Invalid Full Redemption format"
    ) as exc_info:
        _read(content)

    assert "FOO on 2023-01-15" in str(exc_info.value)


def test_cash_merger_rejects_zero_adjustment_quantity() -> None:
    """A zero quantity identifies the malformed Cash Merger pair."""
    content = (
        SCHWAB_HEADER
        + "01/15/2023,Cash Merger,FOO,Merger,,,,$100.00\n"
        + "01/15/2023,Cash Merger Adj,FOO,Merger,,0,,\n"
    )

    with pytest.raises(ParsingError, match="negative share quantity") as exc_info:
        _read(content)

    assert "FOO on 2023-01-15" in str(exc_info.value)


def test_full_redemption_rejects_zero_quantity() -> None:
    """A zero quantity identifies the malformed Full Redemption pair."""
    content = (
        SCHWAB_HEADER
        + "01/15/2023,Full Redemption Adj,FOO,Redemption,,,,$100.00\n"
        + "01/15/2023,Full Redemption,FOO,Redemption,,0,,\n"
    )

    with pytest.raises(ParsingError, match="negative share quantity") as exc_info:
        _read(content)

    assert "FOO on 2023-01-15" in str(exc_info.value)
