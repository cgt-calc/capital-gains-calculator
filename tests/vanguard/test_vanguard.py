"""Test Vanguard parser support."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import re
import subprocess

import pytest

from cgt_calc.const import RENAME_DESCRIPTION_PREFIX
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import ActionType
from cgt_calc.parsers.vanguard import VanguardParser, VanguardTransaction
from tests.utils import build_cmd, report_path, stderr_alerts

# The header of a cash-only export, as Vanguard writes it.
COLUMNS = ["Date", "Details", "Amount", "Balance"]


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    path.write_text("\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8")


def test_run_with_vanguard_files(request: pytest.FixtureRequest) -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2022",
        "--vanguard-file",
        "tests/vanguard/data/cash_investment_report.csv",
        "--interest-fund-tickers",
        "FOO",
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
    assert len(alerts) == 1, f"Unexpected alerts: {alerts}"
    assert "no ISIN mapping was found" in alerts[0]
    expected_file = Path("tests") / "vanguard" / "data" / "expected_output.txt"
    expected = expected_file.read_text(encoding="utf-8")
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


def test_read_vanguard_transactions_buy(tmp_path: Path) -> None:
    """Parse a simple BUY transaction and compute derived fields."""

    vanguard_file = tmp_path / "buy.csv"
    rows = [
        COLUMNS,
        [
            "09/03/2022",
            "Bought 10 Foo Fund (FOO)",
            "-100.00",
            "0",
        ],
    ]
    _write_csv(vanguard_file, rows)

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action is ActionType.BUY
    assert transaction.symbol == "FOO"
    assert transaction.quantity == Decimal(10)
    assert transaction.price == Decimal(10)
    assert transaction.amount == Decimal(-100)
    assert transaction.currency == "GBP"


def test_read_vanguard_missing_symbol(tmp_path: Path) -> None:
    """Use the full string investment name if no symbol within ( ) is matched."""

    vanguard_file = tmp_path / "buy.csv"
    rows = [
        COLUMNS,
        [
            "09/03/2022",
            "Bought 10 Foo Fund",
            "-100.00",
            "0",
        ],
    ]
    _write_csv(vanguard_file, rows)

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action is ActionType.BUY
    assert transaction.symbol == "Foo Fund"
    assert transaction.quantity == Decimal(10)
    assert transaction.price == Decimal(10)
    assert transaction.amount == Decimal(-100)
    assert transaction.currency == "GBP"


def test_read_vanguard_fractional_share(tmp_path: Path) -> None:
    """Make sure it handles fractional share."""

    vanguard_file = tmp_path / "buy.csv"
    rows = [
        COLUMNS,
        [
            "09/03/2022",
            "Bought .2 Foo Fund (GBP) (FOO)",
            "-100.00",
            "0",
        ],
    ]
    _write_csv(vanguard_file, rows)

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action is ActionType.BUY
    assert transaction.symbol == "FOO"
    assert transaction.quantity == Decimal("0.2")
    assert transaction.price == Decimal(500)
    assert transaction.amount == Decimal(-100)
    assert transaction.currency == "GBP"


def test_read_vanguard_etf_dealing_fee_is_transfer(tmp_path: Path) -> None:
    """ETF dealing fees parse as TRANSFER and affect cash without a position."""

    vanguard_file = tmp_path / "etf_dealing_fee.csv"
    rows = [
        COLUMNS,
        [
            "09/03/2022",
            "ETF dealing fee (sell) FTSE 100 UCITS ETF - Distributing (VUKE)",
            "-7.50",
            "0",
        ],
    ]
    _write_csv(vanguard_file, rows)

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action is ActionType.TRANSFER
    assert transaction.symbol is None
    assert transaction.quantity is None
    assert transaction.price is None
    assert transaction.amount == Decimal("-7.50")


def test_read_vanguard_transactions_invalid_decimal(tmp_path: Path) -> None:
    """Raise ParsingError when amount cannot be parsed as Decimal."""

    vanguard_file = tmp_path / "invalid.csv"
    rows = [
        COLUMNS,
        [
            "09/03/2022",
            "Bought 10 Foo Fund (FOO)",
            "not-a-number",
            "0",
        ],
    ]
    _write_csv(vanguard_file, rows)

    with pytest.raises(ParsingError) as exc:
        VanguardParser().load_from_file(vanguard_file)

    message = str(exc.value)
    assert "row 2" in message
    assert "Invalid decimal" in message


def test_read_vanguard_transactions_invalid_header(tmp_path: Path) -> None:
    """Raise ParsingError when header contains unexpected column."""

    vanguard_file = tmp_path / "invalid_header.csv"
    rows = [
        [
            "Date",
            "Details",
            "Unexpected",
            "Balance",
        ],
    ]
    _write_csv(vanguard_file, rows)

    with pytest.raises(ParsingError) as exc:
        VanguardParser().load_from_file(vanguard_file)

    assert "Expected column 3 to be 'Amount' but found 'Unexpected'" in str(exc.value)


def test_read_vanguard_transactions_empty_file(tmp_path: Path) -> None:
    """Raise ParsingError when file has no content."""

    vanguard_file = tmp_path / "empty.csv"
    vanguard_file.write_text("", encoding="utf-8")

    with pytest.raises(ParsingError) as exc:
        VanguardParser().load_from_file(vanguard_file)

    assert "Vanguard CSV file is empty" in str(exc.value)


# --- Tests for investment transaction enrichment ---

INVESTMENT_DATA_DIR = Path("tests/vanguard/data")


def _dual_table_csv(investment_row: str) -> str:
    """Build a minimal two-table export around one Investment Transactions row."""
    return (
        "Cash Transactions\n"
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n"
        "Investment Transactions\n"
        "Date,InvestmentName,TransactionDetails,Quantity,Price,Cost\n"
        f"{investment_row}\n"
    )


# An investment row is validated while it is read, even though only a RENAME
# produces a transaction. These pin that, so removing the read stops the suite.
def test_read_vanguard_investment_row_unknown_action_is_rejected(
    tmp_path: Path,
) -> None:
    """Refuse activity in the investment table that the cash table would refuse."""
    vanguard_file = tmp_path / "unknown_investment_action.csv"
    vanguard_file.write_text(
        _dual_table_csv("01/02/2022,Foo Fund (FOO),Frobnicated 10 Foo Fund,10,1,10"),
        encoding="utf-8",
    )

    with pytest.raises(
        ParsingError, match="row 6: Unknown action: Frobnicated 10 Foo Fund"
    ):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_investment_row_invalid_decimal_is_rejected(
    tmp_path: Path,
) -> None:
    """Refuse an unreadable investment quantity instead of ignoring the row."""
    vanguard_file = tmp_path / "invalid_investment_quantity.csv"
    vanguard_file.write_text(
        _dual_table_csv(
            "01/02/2022,Foo Fund (FOO),Bought 10 Foo Fund (FOO),not-a-number,1,10"
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ParsingError, match="row 6: Invalid decimal in Quantity: 'not-a-number'"
    ):
        VanguardParser().load_from_file(vanguard_file)


def test_dual_table_enriches_buy_with_investment_data() -> None:
    """Cash BUY transactions are enriched with precise quantity/price from investment table."""
    transactions = VanguardParser().load_from_file(
        INVESTMENT_DATA_DIR / "cash_investment_report.csv"
    )

    foo_buys = [
        t for t in transactions if t.symbol == "FOO" and t.action == ActionType.BUY
    ]
    assert len(foo_buys) == 1
    assert foo_buys[0].quantity == Decimal(1550)
    assert foo_buys[0].price == Decimal("14982.06") / Decimal(1550)


def test_dual_table_enriches_sell_with_investment_data() -> None:
    """Cash SELL transactions are enriched with precise price from investment table."""
    transactions = VanguardParser().load_from_file(
        INVESTMENT_DATA_DIR / "cash_investment_report.csv"
    )

    bar_sells = [
        t for t in transactions if t.symbol == "BAR" and t.action == ActionType.SELL
    ]
    assert len(bar_sells) == 1
    assert bar_sells[0].quantity == Decimal(1)
    assert bar_sells[0].price == Decimal("104.06")


def test_dual_table_keeps_cash_table_amount() -> None:
    """The cash-table amount is preserved when a buy is enriched."""
    transactions = VanguardParser().load_from_file(
        INVESTMENT_DATA_DIR / "cash_investment_report.csv"
    )

    bar_buys = [
        t for t in transactions if t.symbol == "BAR" and t.action == ActionType.BUY
    ]
    assert len(bar_buys) == 1
    # amount stays as the cash table value
    assert bar_buys[0].amount == Decimal("-29947.28")


def test_dual_table_enriches_fee_sale_with_positive_price(tmp_path: Path) -> None:
    """A disposal made to fund fees has no quantity in its details text.

    Quantity comes from the investment table and the derived unit price must
    be positive even though the cash amount is an outflow.
    See https://github.com/cgt-calc/capital-gains-calculator/issues/758.
    """
    fee_sale_details = (
        "Selling of account investments for payment of Account Fee "
        "for the period 08-Apr-2022 to 07-Jul-2022"
    )
    content = (
        "Date,Details,Amount,Balance\n"
        f"19/07/2022,{fee_sale_details},-13.91,100\n"
        "\n"
        "Investment Transactions\n"
        "\n"
        "Date,InvestmentName,TransactionDetails,Quantity,Price,Cost\n"
        f"19/07/2022,Foo ETF (FOO),{fee_sale_details},1.391,10,13.91\n"
    )
    vanguard_file = tmp_path / "fee_sale.csv"
    vanguard_file.write_text(content, encoding="utf-8")

    transactions = VanguardParser().load_from_file(vanguard_file)

    enriched = [
        t
        for t in transactions
        if t.action == ActionType.TRANSFER and t.quantity is not None
    ]
    assert len(enriched) == 1
    assert enriched[0].quantity == Decimal("1.391")
    assert enriched[0].price == Decimal(10)
    assert enriched[0].amount == Decimal("-13.91")


def test_dual_table_fractional_shares_from_investment() -> None:
    """Fractional shares from investment table are used over cash regex parsing."""
    transactions = VanguardParser().load_from_file(
        INVESTMENT_DATA_DIR / "cash_investment_report.csv"
    )

    em_buys = [
        t
        for t in transactions
        if t.symbol == "Emerging Markets Stock Index Fund - Accumulation"
        and t.action == ActionType.BUY
    ]
    assert len(em_buys) == 1
    assert em_buys[0].quantity == Decimal("6.7700")
    assert em_buys[0].price == Decimal(1000) / Decimal("6.7700")


def test_dual_table_preserves_non_investment_transactions() -> None:
    """Transfers, interest, and dividends from cash table are preserved."""
    transactions = VanguardParser().load_from_file(
        INVESTMENT_DATA_DIR / "cash_investment_report.csv"
    )

    transfers = [t for t in transactions if t.action == ActionType.TRANSFER]
    assert len(transfers) > 0

    interests = [t for t in transactions if t.action == ActionType.INTEREST]
    assert len(interests) == 1
    assert interests[0].amount == Decimal("0.41")

    dividends = [t for t in transactions if t.action == ActionType.DIVIDEND]
    assert len(dividends) == 4


def test_dual_table_reversal_handling() -> None:
    """Reversal transactions are correctly flagged."""
    transactions = VanguardParser().load_from_file(
        INVESTMENT_DATA_DIR / "cash_investment_report.csv"
    )

    reversals = [t for t in transactions if hasattr(t, "is_reversal") and t.is_reversal]
    assert len(reversals) == 1
    assert reversals[0].action == ActionType.DIVIDEND
    assert reversals[0].amount == Decimal("-170.83")


@pytest.mark.parametrize(
    "details",
    ["Bought 10 Foo Fund (FOO)", "Sold 10 Foo Fund (FOO)"],
)
def test_read_vanguard_reversal_of_a_trade_is_rejected(
    tmp_path: Path, details: str
) -> None:
    """Refuse a reversed trade instead of importing it the wrong way round."""
    vanguard_file = tmp_path / "reversed_trade.csv"
    vanguard_file.write_text(
        f"Date,Details,Amount,Balance\n09/03/2022,Reversal of {details},100.00,0\n",
        encoding="utf-8",
    )

    expected = re.escape(f"Cannot import a reversed trade: Reversal of {details}")
    with pytest.raises(ParsingError, match=expected) as exc:
        VanguardParser().load_from_file(vanguard_file)

    message = str(exc.value)
    # The row is understood; it is the trade it cancels that is missing. Ask
    # for it to be reported, so the format can eventually be read, and do not
    # prescribe an edit: what the row means has not been established, so any
    # deletion could drop activity that turns out to be chargeable.
    assert "Keep the export unchanged" in message
    assert "please report this row" in message
    assert "remove" not in message.lower()
    assert "delete" not in message.lower()
    assert "https://cgt-calc.uk/brokers/vanguard/#a-reversed-purchase-or-disposal" in (
        message
    )


@pytest.mark.parametrize(
    ("details", "action"),
    [
        ("DIV: FOO.XLON.GB @ GBP 0.3106", ActionType.DIVIDEND),
        ("Cash Account Interest", ActionType.INTEREST),
        ("Regular Deposit", ActionType.TRANSFER),
        ("Account Fee", ActionType.TRANSFER),
    ],
)
def test_read_vanguard_reversal_of_income_is_still_read(
    tmp_path: Path, details: str, action: ActionType
) -> None:
    """Keep reading the reversals that only negate one exported amount."""
    vanguard_file = tmp_path / "reversed_income.csv"
    vanguard_file.write_text(
        f"Date,Details,Amount,Balance\n09/03/2022,Reversal of {details},-10.00,0\n",
        encoding="utf-8",
    )

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert isinstance(transaction, VanguardTransaction)
    assert transaction.action is action
    assert transaction.is_reversal is True


def test_read_vanguard_reversal_of_unknown_activity_stays_generic(
    tmp_path: Path,
) -> None:
    """Do not call a reversal a trade when the activity itself is unrecognised."""
    vanguard_file = tmp_path / "reversed_unknown.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        "09/03/2022,Reversal of Corporate Action,100.00,0\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ParsingError, match="Unknown action: Reversal of Corporate Action"
    ):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_reversed_fee_sale_is_enriched(tmp_path: Path) -> None:
    """Enrich a reversal from its own investment row, not only plain rows."""
    fee = "Selling of account investments for payment of Account Fee"
    vanguard_file = tmp_path / "reversed_fee_sale.csv"
    vanguard_file.write_text(
        "Cash Transactions\n"
        "Date,Details,Amount,Balance\n"
        f"19/07/2022,{fee},-13.91,100\n"
        f"20/07/2022,Reversal of {fee},13.91,113.91\n"
        "Investment Transactions\n"
        "Date,InvestmentName,TransactionDetails,Quantity,Price,Cost\n"
        f"19/07/2022,Foo Fund (FOO),{fee},1.391,10,13.91\n"
        f"20/07/2022,Foo Fund (FOO),Reversal of {fee},2.5,20,50\n",
        encoding="utf-8",
    )

    transactions = VanguardParser().load_from_file(vanguard_file)

    by_reversal = {transaction.is_reversal: transaction for transaction in transactions}
    # Each row takes its own quantity, so neither is enriched from the other.
    assert by_reversal[False].quantity == Decimal("1.391")
    assert by_reversal[True].quantity == Decimal("2.5")


def test_dual_table_sorted_by_date() -> None:
    """Transactions are sorted by date."""
    transactions = VanguardParser().load_from_file(
        INVESTMENT_DATA_DIR / "cash_investment_report.csv"
    )

    dates = [t.date for t in transactions]
    assert dates == sorted(dates)


def test_dual_table_blank_transaction_details_does_not_crash(tmp_path: Path) -> None:
    """A blank TransactionDetails cell (seen in real multi-fund batch exports) must not abort parsing."""
    content = (
        "Date,Details,Amount,Balance\n"
        "10/03/2022,Bought 10 Foo ETF (FOO),-95,5\n"
        "\n"
        "Investment Transactions\n"
        "\n"
        "Date,InvestmentName,TransactionDetails,Quantity,Price,Cost\n"
        "10/03/2022,Foo ETF (FOO),,10,9.5,95\n"
    )
    vanguard_file = tmp_path / "blank_transaction_details.csv"
    vanguard_file.write_text(content, encoding="utf-8")

    transactions = VanguardParser().load_from_file(vanguard_file)

    buys = [t for t in transactions if t.action == ActionType.BUY]
    assert len(buys) == 1
    assert buys[0].symbol == "FOO"
    assert buys[0].quantity == Decimal(10)


def test_namechange_emits_rename_transaction(tmp_path: Path) -> None:
    """NameChange in dual-table CSV emits one RENAME; pre-rename BUY stays under OLD."""
    content = (
        "Date,Details,Amount,Balance\n"
        "01/03/2022,Bought 10 Old Fund (VDXX),-100.00,0\n"
        "\n"
        "Investment Transactions\n"
        "\n"
        "Date,InvestmentName,TransactionDetails,Quantity,Price,Cost\n"
        "01/03/2022,Old Fund (VDXX),Bought 10 Old Fund (VDXX),10,10,100\n"
        "15/03/2022,Old Fund (VDXX),NameChange: VDXX,0,0,0\n"
        "15/03/2022,New Fund (VGER),NameChange: VDXX replaced with VGER,0,0,0\n"
    )
    vanguard_file = tmp_path / "namechange.csv"
    vanguard_file.write_text(content, encoding="utf-8")

    transactions = VanguardParser().load_from_file(vanguard_file)

    renames = [t for t in transactions if t.action is ActionType.RENAME]
    assert len(renames) == 1
    rename = renames[0]
    assert rename.symbol == "VGER"
    assert rename.description == f"{RENAME_DESCRIPTION_PREFIX}VDXX"
    assert rename.amount == Decimal(0)

    buys = [t for t in transactions if t.action is ActionType.BUY]
    assert len(buys) == 1
    assert buys[0].symbol == "VDXX"


@pytest.mark.parametrize(
    "transaction_details",
    ["", "Bought 10 Foo ETF (FOO)"],
)
def test_investment_only_table_is_rejected(
    tmp_path: Path, transaction_details: str
) -> None:
    """Reject an investment-only table because it has no signed cash amounts."""
    content = (
        "Investment Transactions\n"
        "\n"
        "Date,InvestmentName,TransactionDetails,Quantity,Price,Cost\n"
        f"10/03/2022,Foo ETF (FOO),{transaction_details},10,9.5,95\n"
    )
    vanguard_file = tmp_path / "inv_only.csv"
    vanguard_file.write_text(content, encoding="utf-8")

    with pytest.raises(ParsingError, match="must include the Cash Transactions table"):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_conflicting_repeated_header_is_rejected(tmp_path: Path) -> None:
    """Reject a repeated header whose column order differs from the active table."""
    vanguard_file = tmp_path / "conflicting_repeated_header.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n"
        "Date,Amount,Details,Balance\n"
        "02/01/2022,Regular Deposit,50,150\n",
        encoding="utf-8",
    )

    with pytest.raises(ParsingError, match="Multiple Cash Transactions table headers"):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_date_shaped_row_is_not_treated_as_footer(
    tmp_path: Path,
) -> None:
    """Report a malformed dated row instead of closing the table before it."""
    vanguard_file = tmp_path / "padded_date.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n"
        "02/01/2022,,50,150\n"
        "03/01/2022,Regular Deposit,50,150\n",
        encoding="utf-8",
    )

    with pytest.raises(ParsingError, match=r"row 3: Missing action text"):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_error_uses_physical_line_after_quoted_newline(
    tmp_path: Path,
) -> None:
    """Count embedded CSV newlines when reporting the later malformed row."""
    vanguard_file = tmp_path / "quoted_newline.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        '01/01/2022,Regular Deposit,100,"100\n'
        'continued"\n'
        "02/01/2022,Regular Deposit,50\n",
        encoding="utf-8",
    )

    with pytest.raises(UnexpectedColumnCountError, match=r"row 4:"):
        VanguardParser().load_from_file(vanguard_file)


@pytest.mark.parametrize("suffix", [".xls", ".xlsx", ".xlsm", ".xlsb"])
def test_read_vanguard_excel_workbook_has_clear_error(
    tmp_path: Path, suffix: str
) -> None:
    """Reject an Excel workbook before attempting to decode it as CSV text."""
    vanguard_file = tmp_path / f"vanguard{suffix}"
    vanguard_file.write_bytes(b"PK\x03\x04\xff")

    with pytest.raises(ParsingError, match="Excel workbooks are not supported"):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_ignores_cosmetic_trailing_empty_cells(tmp_path: Path) -> None:
    """Accept rows with or without the export header's empty padding columns."""
    vanguard_file = tmp_path / "trailing_empty_cells.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance,,\n"
        "01/01/2022,Regular Deposit,100,100\n"
        "02/01/2022,Regular Deposit,50,150,,\n",
        encoding="utf-8",
    )

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert [transaction.amount for transaction in transactions] == [
        Decimal(100),
        Decimal(50),
    ]


def test_read_vanguard_invalid_utf8_has_clear_error(tmp_path: Path) -> None:
    """Wrap decoding failures in the normal parsing error type."""
    vanguard_file = tmp_path / "vanguard.csv"
    vanguard_file.write_bytes(b"Date,Details,Amount,Balance\n\xff")

    with pytest.raises(ParsingError, match="must be a UTF-8 CSV text file"):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_legacy_footer_is_ignored(tmp_path: Path) -> None:
    """Ignore a single-cell footer after a legacy cash-only table."""
    vanguard_file = tmp_path / "legacy_footer.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n"
        "This report is not advice\n",
        encoding="utf-8",
    )

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert len(transactions) == 1
    assert transactions[0].amount == Decimal(100)


def test_read_vanguard_page_footer_between_transactions_is_ignored(
    tmp_path: Path,
) -> None:
    """Do not let a pagination label truncate an active table."""
    vanguard_file = tmp_path / "page_footer.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n"
        "Page 1 of 2\n"
        "02/01/2022,Regular Deposit,50,150\n",
        encoding="utf-8",
    )

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert [transaction.amount for transaction in transactions] == [
        Decimal(100),
        Decimal(50),
    ]


def test_read_vanguard_collapsed_cash_row_is_rejected(tmp_path: Path) -> None:
    """Reject a transaction whose lost separators leave it in a single cell."""
    vanguard_file = tmp_path / "collapsed_cash.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n"
        '"02/01/2022,Regular Deposit,50,150"\n'
        "03/01/2022,Regular Deposit,25,175\n",
        encoding="utf-8",
    )

    with pytest.raises(UnexpectedColumnCountError, match="row 3"):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_collapsed_final_cash_row_is_rejected(tmp_path: Path) -> None:
    """Do not accept a collapsed last row as the trailing footer it resembles."""
    vanguard_file = tmp_path / "collapsed_last.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n"
        '"02/01/2022,Regular Deposit,50,150"\n',
        encoding="utf-8",
    )

    with pytest.raises(UnexpectedColumnCountError, match="row 3"):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_unlabelled_mid_table_text_is_rejected(tmp_path: Path) -> None:
    """Only pagination labels may interrupt a table; other text is not skipped."""
    vanguard_file = tmp_path / "mid_table_text.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n"
        "Continued overleaf\n"
        "02/01/2022,Regular Deposit,50,150\n",
        encoding="utf-8",
    )

    with pytest.raises(UnexpectedColumnCountError, match="row 3"):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_page_label_outside_first_column_is_ignored(
    tmp_path: Path,
) -> None:
    """Recognise a pagination label wherever the export centres it."""
    vanguard_file = tmp_path / "centred_label.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n"
        ",,Page 1 of 2,\n"
        "02/01/2022,Regular Deposit,50,150\n",
        encoding="utf-8",
    )

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert [transaction.amount for transaction in transactions] == [
        Decimal(100),
        Decimal(50),
    ]


def test_read_vanguard_header_under_wrong_section_title_is_rejected(
    tmp_path: Path,
) -> None:
    """Do not read a cash table announced as the Investment Transactions one."""
    vanguard_file = tmp_path / "mismatched_section.csv"
    vanguard_file.write_text(
        "Investment Transactions\n"
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ParsingError,
        match="Cash Transactions table header under the Investment Transactions",
    ):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_unrecognised_header_under_title_is_rejected(
    tmp_path: Path,
) -> None:
    """Reject an announced table whose header lost a column."""
    vanguard_file = tmp_path / "lost_column.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n"
        "Investment Transactions\n"
        "InvestmentName,TransactionDetails,Quantity,Price,Cost\n"
        "Foo Fund (FOO),Sold 1 Foo Fund (FOO),1,10,10\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ParsingError, match="row 4: Found a row outside the Investment Transactions"
    ):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_transaction_above_first_header_is_rejected(
    tmp_path: Path,
) -> None:
    """Reject an export that lost the header above its opening transactions."""
    vanguard_file = tmp_path / "lost_first_header.csv"
    vanguard_file.write_text(
        "01/01/2022,Regular Deposit,100,100\n"
        "Date,Details,Amount,Balance\n"
        "02/01/2022,Regular Deposit,50,150\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ParsingError,
        match="row 1: Found a transaction row above the first table header",
    ):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_collapsed_row_above_first_header_is_rejected(
    tmp_path: Path,
) -> None:
    """Reject a collapsed transaction above the header, which has no fields."""
    vanguard_file = tmp_path / "collapsed_above_header.csv"
    vanguard_file.write_text(
        '"01/01/2022,Regular Deposit,100,100"\n'
        "Date,Details,Amount,Balance\n"
        "02/01/2022,Regular Deposit,50,150\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ParsingError,
        match="row 1: Found a transaction row above the first table header",
    ):
        VanguardParser().load_from_file(vanguard_file)


@pytest.mark.parametrize(
    "metadata",
    ["01/01/2022", "01/01/2022 - 31/12/2022", "Statement produced 01/01/2022"],
)
def test_read_vanguard_dated_metadata_above_header_is_accepted(
    tmp_path: Path, metadata: str
) -> None:
    """Keep accepting the dated preamble an export prints above its table."""
    vanguard_file = tmp_path / "dated_preamble.csv"
    vanguard_file.write_text(
        f"{metadata}\nDate,Details,Amount,Balance\n02/01/2022,Regular Deposit,50,150\n",
        encoding="utf-8",
    )

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert [transaction.amount for transaction in transactions] == [Decimal(50)]


def test_read_vanguard_missing_header_still_reports_the_columns(
    tmp_path: Path,
) -> None:
    """Keep the column error when no table header is recognised at all."""
    vanguard_file = tmp_path / "no_header.csv"
    vanguard_file.write_text(
        "Date,Detail,Amount,Balance\n01/01/2022,Regular Deposit,100,100\n",
        encoding="utf-8",
    )

    with pytest.raises(ParsingError, match="Expected column 2 to be 'Details'"):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_padded_header_cells_are_accepted(tmp_path: Path) -> None:
    """Match column names that the export spaced out for readability."""
    vanguard_file = tmp_path / "padded_header.csv"
    vanguard_file.write_text(
        "Date, Details, Amount, Balance\n01/01/2022,Regular Deposit,100,100\n",
        encoding="utf-8",
    )

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert [transaction.amount for transaction in transactions] == [Decimal(100)]


def test_read_vanguard_repeated_page_header_tolerates_spacing(tmp_path: Path) -> None:
    """Treat a respaced repeat of the same header as pagination, not a conflict."""
    vanguard_file = tmp_path / "respaced_header.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n"
        "Date,Details,Amount,Balance \n"
        "02/01/2022,Regular Deposit,50,150\n",
        encoding="utf-8",
    )

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert [transaction.amount for transaction in transactions] == [
        Decimal(100),
        Decimal(50),
    ]


def test_read_vanguard_ragged_cash_row_is_rejected(tmp_path: Path) -> None:
    """Do not silently omit a cash row with the wrong field count."""
    vanguard_file = tmp_path / "ragged_cash.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n09/03/2022,Bought 10 Foo Fund (FOO),-100.00\n",
        encoding="utf-8",
    )

    with pytest.raises(UnexpectedColumnCountError, match="doesn't have 4 columns"):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_ragged_investment_row_is_rejected(tmp_path: Path) -> None:
    """Do not silently omit an investment row with the wrong field count."""
    vanguard_file = tmp_path / "ragged_investment.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        "09/03/2022,Bought 10 Foo Fund (FOO),-100.00,0\n"
        "\n"
        "Investment Transactions\n"
        "\n"
        "Date,InvestmentName,TransactionDetails,Quantity,Price,Cost\n"
        "10/03/2022,Foo Fund (FOO),Bought 10 Foo Fund (FOO),10,10\n",
        encoding="utf-8",
    )

    with pytest.raises(
        UnexpectedColumnCountError, match=r"row 7:.*doesn't have 6 columns"
    ):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_repeated_page_header_continues_table(tmp_path: Path) -> None:
    """Append rows after repeated pagination titles and unchanged headers."""
    vanguard_file = tmp_path / "repeated_header.csv"
    vanguard_file.write_text(
        "Cash Transactions\n"
        "Date,Details,Amount,Balance,,\n"
        "01/01/2022,Regular Deposit,100,100\n"
        "Page 1 of 2\n"
        "Cash Transactions\n"
        "Date,Details,Amount,Balance\n"
        "02/01/2022,Regular Deposit,50,150\n",
        encoding="utf-8",
    )

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert [transaction.amount for transaction in transactions] == [
        Decimal(100),
        Decimal(50),
    ]


def test_read_vanguard_running_summary_before_page_header_continues_table(
    tmp_path: Path,
) -> None:
    """Treat a summary followed by a repeated page header as pagination."""
    vanguard_file = tmp_path / "running_summary.csv"
    vanguard_file.write_text(
        "Cash Transactions\n"
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n"
        "Balance,100\n"
        "Page 1 of 2\n"
        "Cash Transactions\n"
        "Date,Details,Amount,Balance\n"
        "02/01/2022,Regular Deposit,50,150\n",
        encoding="utf-8",
    )

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert [transaction.amount for transaction in transactions] == [
        Decimal(100),
        Decimal(50),
    ]


def test_read_vanguard_section_title_updates_error_context(tmp_path: Path) -> None:
    """Name the section containing a transaction row that lacks its table header."""
    vanguard_file = tmp_path / "missing_investment_header.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n"
        "Balance,100\n"
        "Investment Transactions\n"
        "02/01/2022,Foo Fund (FOO),Bought 1 Foo Fund (FOO),1,1,1\n",
        encoding="utf-8",
    )

    with pytest.raises(ParsingError, match="outside the Investment Transactions"):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_tab_delimited_full_export(tmp_path: Path) -> None:
    """Detect tabs from a table header even when the title row has one cell."""
    vanguard_file = tmp_path / "vanguard.tsv"
    vanguard_file.write_text(
        "Bob's General investment\n"
        "\n"
        "Cash Transactions\n"
        "\n"
        "Date\tDetails\tAmount\tBalance\n"
        "09/03/2022\tBought 10 Foo Fund (FOO)\t-100.00\t0\n",
        encoding="utf-8",
    )

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert len(transactions) == 1
    assert transactions[0].symbol == "FOO"


def test_read_vanguard_transaction_after_summary_is_rejected(tmp_path: Path) -> None:
    """Do not discard a transaction-shaped row after a premature summary."""
    vanguard_file = tmp_path / "mid_table_summary.csv"
    vanguard_file.write_text(
        "Date,Details,Amount,Balance\n"
        "01/01/2022,Regular Deposit,100,100\n"
        "Balance,100\n"
        "02/01/2022,Regular Deposit,50,150\n",
        encoding="utf-8",
    )

    with pytest.raises(ParsingError, match="transaction row after the Cash"):
        VanguardParser().load_from_file(vanguard_file)


def test_read_vanguard_utf8_bom(tmp_path: Path) -> None:
    """Accept the BOM written by Excel's CSV UTF-8 format."""
    vanguard_file = tmp_path / "bom.csv"
    vanguard_file.write_text(
        "\ufeffDate,Details,Amount,Balance\n"
        "09/03/2022,Bought 10 Foo Fund (FOO),-100.00,0\n",
        encoding="utf-8",
    )

    transactions = VanguardParser().load_from_file(vanguard_file)

    assert len(transactions) == 1
    assert transactions[0].symbol == "FOO"
