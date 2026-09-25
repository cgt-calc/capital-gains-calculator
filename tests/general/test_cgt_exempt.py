"""Tests for CGT-exempt assets (UK gilts, QCBs)."""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING

from cgt_calc.model import ActionType, RuleType
from cgt_calc.render_latex import render_pdf
from tests.utils import build_cmd, report_path, run_cli, stderr_alerts

from .calc_test_data import GBP, transaction
from .test_calc import create_calculator, get_report

if TYPE_CHECKING:
    import pytest

TAX_YEAR = 2024
BUY_DATE = datetime.date(2024, 5, 1)
SELL_DATE = datetime.date(2024, 9, 1)


def test_exempt_disposal_at_gain_excluded_from_capital_gain() -> None:
    """Exempt disposal with a gain does not increase chargeable gain or proceeds."""
    transactions = [
        transaction(BUY_DATE, ActionType.BUY, "T26", 100, 100, 0, -10000, GBP),
        transaction(SELL_DATE, ActionType.SELL, "T26", 100, 120, 0, 12000, GBP),
    ]
    calc = create_calculator(
        tax_year=TAX_YEAR, balance_check=False, cgt_exempt_tickers=["T26"]
    )
    report = get_report(calc, transactions)

    assert report.disposal_count == 0
    assert report.disposal_proceeds == Decimal(0)
    assert report.allowable_costs == Decimal(0)
    assert report.capital_gain == Decimal(0)
    assert report.capital_loss == Decimal(0)
    assert report.total_gain() == Decimal(0)
    assert report.taxable_gain() == Decimal(0)
    assert report.exempt_disposal_count == 1
    assert report.exempt_disposal_proceeds == Decimal(12000)

    assert "exempt$T26" in report.calculation_log[SELL_DATE]
    assert "sell$T26" not in report.calculation_log[SELL_DATE]

    report_str = str(report)
    assert "Exempt disposals:                  1" in report_str
    assert "Exempt disposal proceeds: £12,000.00" in report_str
    assert "Gain:                          £0.00" in report_str
    assert "Total gain:                    £0.00" in report_str


def test_exempt_disposal_at_loss_excluded_from_capital_loss() -> None:
    """Exempt disposal with a loss is excluded from capital_loss and not carried forward."""
    transactions_year1 = [
        transaction(BUY_DATE, ActionType.BUY, "T26", 100, 100, 0, -10000, GBP),
        transaction(SELL_DATE, ActionType.SELL, "T26", 100, 80, 0, 8000, GBP),
    ]
    calc1 = create_calculator(
        tax_year=TAX_YEAR, balance_check=False, cgt_exempt_tickers=["T26"]
    )
    report1 = get_report(calc1, transactions_year1)

    assert report1.capital_gain == Decimal(0)
    assert report1.capital_loss == Decimal(0)
    assert report1.total_gain() == Decimal(0)
    assert report1.taxable_gain() == Decimal(0)
    assert report1.exempt_disposal_count == 1
    assert report1.exempt_disposal_proceeds == Decimal(8000)

    report_str = str(report1)
    assert "Loss:                         £0.00" in report_str
    assert "Exempt disposals:                 1" in report_str
    assert "Exempt disposal proceeds: £8,000.00" in report_str

    # In a subsequent tax year with a chargeable gain, verify no allowable loss carried forward
    next_year_buy = datetime.date(2025, 5, 1)
    next_year_sell = datetime.date(2025, 9, 1)
    transactions_year2 = [
        *transactions_year1,
        transaction(next_year_buy, ActionType.BUY, "FOO", 50, 10, 0, -500, GBP),
        transaction(next_year_sell, ActionType.SELL, "FOO", 50, 20, 0, 1000, GBP),
    ]
    calc2 = create_calculator(
        tax_year=2025, balance_check=False, cgt_exempt_tickers=["T26"]
    )
    report2 = get_report(calc2, transactions_year2)

    assert report2.capital_gain == Decimal(500)
    assert report2.capital_loss == Decimal(0)
    assert report2.total_gain() == Decimal(500)


def test_mixed_portfolio_chargeable_and_exempt() -> None:
    """Chargeable disposal alongside an exempt disposal is completely unaffected."""
    transactions = [
        transaction(BUY_DATE, ActionType.BUY, "T26", 100, 100, 0, -10000, GBP),
        transaction(BUY_DATE, ActionType.BUY, "FOO", 50, 20, 0, -1000, GBP),
        transaction(SELL_DATE, ActionType.SELL, "T26", 100, 120, 0, 12000, GBP),
        transaction(SELL_DATE, ActionType.SELL, "FOO", 50, 30, 0, 1500, GBP),
    ]
    calc = create_calculator(
        tax_year=TAX_YEAR, balance_check=False, cgt_exempt_tickers=["T26"]
    )
    report = get_report(calc, transactions)

    # Only the FOO disposal should be in chargeable totals
    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal(1500)
    assert report.allowable_costs == Decimal(1000)
    assert report.capital_gain == Decimal(500)
    assert report.capital_loss == Decimal(0)
    assert report.total_gain() == Decimal(500)

    # T26 disposal should be in exempt totals
    assert report.exempt_disposal_count == 1
    assert report.exempt_disposal_proceeds == Decimal(12000)

    report_str = str(report)
    assert "Disposals:                         1" in report_str
    assert "Disposal proceeds:         £1,500.00" in report_str
    assert "Allowable costs:           £1,000.00" in report_str
    assert "Gain:                        £500.00" in report_str
    assert "Exempt disposal proceeds: £12,000.00" in report_str
    assert "Total gain:                  £500.00" in report_str


def test_exempt_holding_still_present_in_year_end_portfolio() -> None:
    """Remaining exempt holding is still pooled and present in the year-end portfolio."""
    transactions = [
        transaction(BUY_DATE, ActionType.BUY, "T26", 100, 100, 0, -10000, GBP),
        transaction(SELL_DATE, ActionType.SELL, "T26", 40, 110, 0, 4400, GBP),
    ]
    calc = create_calculator(
        tax_year=TAX_YEAR, balance_check=False, cgt_exempt_tickers=["T26"]
    )
    report = get_report(calc, transactions)

    assert report.exempt_disposal_count == 1
    assert report.exempt_disposal_proceeds == Decimal(4400)

    positions = {entry.symbol: entry for entry in report.portfolio}
    assert "T26" in positions
    assert positions["T26"].quantity == Decimal(60)
    assert positions["T26"].amount == Decimal(6000)

    report_str = str(report)
    assert "T26: 60.00, £6,000.00" in report_str


def test_exempt_disposal_uses_same_day_matching() -> None:
    """Same-day matching still runs, but its gain is disregarded."""
    transactions = [
        transaction(BUY_DATE, ActionType.BUY, "T26", 100, 100, 0, -10000, GBP),
        transaction(BUY_DATE, ActionType.SELL, "T26", 100, 120, 0, 12000, GBP),
    ]
    report = get_report(
        create_calculator(
            tax_year=TAX_YEAR, balance_check=False, cgt_exempt_tickers=["T26"]
        ),
        transactions,
    )

    assert [
        entry.rule_type for entry in report.calculation_log[BUY_DATE]["exempt$T26"]
    ] == [RuleType.SAME_DAY]
    assert report.capital_gain == report.capital_loss == Decimal(0)
    assert report.exempt_disposal_proceeds == Decimal(12000)


def test_exempt_disposal_uses_bed_and_breakfast_matching() -> None:
    """Thirty-day matching still runs, but its gain is disregarded."""
    repurchase_date = SELL_DATE + datetime.timedelta(days=10)
    transactions = [
        transaction(BUY_DATE, ActionType.BUY, "T26", 100, 100, 0, -10000, GBP),
        transaction(SELL_DATE, ActionType.SELL, "T26", 100, 120, 0, 12000, GBP),
        transaction(repurchase_date, ActionType.BUY, "T26", 100, 110, 0, -11000, GBP),
    ]
    report = get_report(
        create_calculator(
            tax_year=TAX_YEAR, balance_check=False, cgt_exempt_tickers=["T26"]
        ),
        transactions,
    )

    assert [
        entry.rule_type for entry in report.calculation_log[SELL_DATE]["exempt$T26"]
    ] == [RuleType.BED_AND_BREAKFAST]
    assert report.capital_gain == report.capital_loss == Decimal(0)
    assert report.exempt_disposal_proceeds == Decimal(12000)


def test_exempt_ticker_income_is_retained() -> None:
    """The override disregards CGT only, leaving taxable income in the report."""
    income_date = datetime.date(2024, 7, 1)
    transactions = [
        transaction(BUY_DATE, ActionType.BUY, "T26", 100, 100, 0, -10000, GBP),
        transaction(income_date, ActionType.DIVIDEND, "T26", amount=250, currency=GBP),
        transaction(income_date, ActionType.INTEREST, "T26", amount=40, currency=GBP),
        transaction(SELL_DATE, ActionType.SELL, "T26", 100, 120, 0, 12000, GBP),
    ]
    report = get_report(
        create_calculator(
            tax_year=TAX_YEAR, balance_check=False, cgt_exempt_tickers=["T26"]
        ),
        transactions,
    )

    assert report.capital_gain == report.capital_loss == Decimal(0)
    assert report.total_dividends_amount() == Decimal(250)
    assert report.total_uk_interest == Decimal(40)
    assert "dividend$T26" in report.calculation_log_yields[income_date]
    assert "interestUK$Testing" in report.calculation_log_yields[income_date]


def test_unmatched_ticker_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A ticker passed to --cgt-exempt-tickers with no transactions produces a warning."""
    transactions = [
        transaction(BUY_DATE, ActionType.BUY, "T26", 100, 100, 0, -10000, GBP),
        transaction(SELL_DATE, ActionType.SELL, "T26", 100, 110, 0, 11000, GBP),
    ]
    calc = create_calculator(
        tax_year=TAX_YEAR,
        balance_check=False,
        cgt_exempt_tickers=["MISSING", "T26"],
    )
    with caplog.at_level(logging.WARNING, logger="cgt_calc.main"):
        get_report(calc, transactions)

    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "MISSING" in msg and "not found in acquisitions or disposals" in msg
        for msg in warnings
    )
    assert not any("T26" in msg for msg in warnings)


def test_case_insensitive_matching() -> None:
    """Matching is case-insensitive for symbols and command-line tickers."""
    transactions = [
        transaction(BUY_DATE, ActionType.BUY, "t26", 100, 100, 0, -10000, GBP),
        transaction(SELL_DATE, ActionType.SELL, "t26", 100, 120, 0, 12000, GBP),
    ]
    calc = create_calculator(
        tax_year=TAX_YEAR, balance_check=False, cgt_exempt_tickers=["T26"]
    )
    report = get_report(calc, transactions)

    assert report.disposal_count == 0
    assert report.capital_gain == Decimal(0)
    assert report.exempt_disposal_count == 1
    assert report.exempt_disposal_proceeds == Decimal(12000)


def test_zero_exempt_disposals_hidden_in_terminal_summary() -> None:
    """When there are no exempt disposals, the Exempt disposals line is omitted."""
    transactions = [
        transaction(BUY_DATE, ActionType.BUY, "FOO", 50, 20, 0, -1000, GBP),
        transaction(SELL_DATE, ActionType.SELL, "FOO", 50, 30, 0, 1500, GBP),
    ]
    calc_no_exempt = create_calculator(
        tax_year=TAX_YEAR, balance_check=False, cgt_exempt_tickers=[]
    )
    report = get_report(calc_no_exempt, transactions)

    assert report.exempt_disposal_proceeds == Decimal(0)
    assert "Exempt disposals" not in str(report)


def test_pdf_report_rendering_exempt_disposal(tmp_path: Path) -> None:
    """PDF report contains per-disposal subsection stating amount and that gain/loss is disregarded."""
    transactions = [
        transaction(BUY_DATE, ActionType.BUY, "T26", 100, 100, 0, -10000, GBP),
        transaction(SELL_DATE, ActionType.SELL, "T26", 100, 120, 0, 12000, GBP),
    ]
    calc = create_calculator(
        tax_year=TAX_YEAR, balance_check=False, cgt_exempt_tickers=["T26"]
    )
    report = get_report(calc, transactions)

    render_pdf(report, tmp_path / "report.pdf", skip_pdflatex=True)
    source = (tmp_path / "report.tex").read_text(encoding="utf-8")
    source_flat = re.sub(r"\s+", " ", source)

    # Check per-disposal subsection
    assert "Exempt disposal: 100 units of T26 valued at £12,000" in source_flat
    assert "the gain or loss is disregarded" in source_flat
    # The cost label carries its colon like the chargeable branch does: no
    # stray space in front of it.
    assert "cost used for this illustrative calculation: £10,000" in source_flat
    assert "not allowable deductions against other gains" in source_flat

    # Check Overall section line
    assert "Number of exempt disposals: 1" in source_flat
    assert "Total exempt disposal proceeds: £12,000" in source_flat
    assert "Number of disposals: 0" in source_flat


def test_pdf_report_rendering_exempt_bed_and_breakfast(tmp_path: Path) -> None:
    """A bed-and-breakfast exempt line keeps its match date and colon together."""
    repurchase_date = SELL_DATE + datetime.timedelta(days=10)
    transactions = [
        transaction(BUY_DATE, ActionType.BUY, "T26", 100, 100, 0, -10000, GBP),
        transaction(SELL_DATE, ActionType.SELL, "T26", 100, 120, 0, 12000, GBP),
        transaction(repurchase_date, ActionType.BUY, "T26", 100, 110, 0, -11000, GBP),
    ]
    calc = create_calculator(
        tax_year=TAX_YEAR, balance_check=False, cgt_exempt_tickers=["T26"]
    )
    report = get_report(calc, transactions)

    render_pdf(report, tmp_path / "report.pdf", skip_pdflatex=True)
    source_flat = re.sub(
        r"\s+", " ", (tmp_path / "report.tex").read_text(encoding="utf-8")
    )

    assert (
        "cost used for this illustrative calculation on "
        "\\mbox{11 Sep 2024:} £11,000" in source_flat
    )


def test_pdf_reports_exempt_gift_with_neutral_valuation_wording(
    tmp_path: Path,
) -> None:
    """An exempt gift is not described as receiving sale proceeds."""
    gift_date = datetime.date(2024, 9, 1)
    transactions = [
        transaction(BUY_DATE, ActionType.BUY, "T26", 100, 100, 0, -10000, GBP),
        transaction(gift_date, ActionType.GIFT, "T26", 100, 80, 0, None, GBP),
    ]
    report = get_report(
        create_calculator(
            tax_year=TAX_YEAR, balance_check=False, cgt_exempt_tickers=["T26"]
        ),
        transactions,
    )

    assert "exempt$T26" in report.calculation_log[gift_date]
    assert "Gifts at market value" not in str(report)
    render_pdf(report, tmp_path / "report.pdf", skip_pdflatex=True)
    source_flat = re.sub(
        r"\s+", " ", (tmp_path / "report.tex").read_text(encoding="utf-8")
    )
    assert "Exempt disposal: 100 units of T26 valued at £8,000.00" in source_flat


def test_run_with_exempt_tickers(
    request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    """Run with exempt tickers generates expected terminal report and PDF."""
    raw_file = tmp_path / "raw_transactions.csv"
    raw_file.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2024-05-01,BUY,T26,100,100,0,GBP\n"
        "2024-09-01,SELL,T26,100,120,0,GBP\n",
        encoding="utf-8",
    )
    out_dir = report_path(request)
    cmd = build_cmd(
        "--year",
        "2024",
        "--raw-file",
        str(raw_file),
        "--cgt-exempt-tickers",
        "T26",
        "--no-balance-check",
        "--output",
        out_dir,
    )
    result = run_cli(cmd)
    assert stderr_alerts(result.stderr) == [], "Unexpected stderr message"
    assert "Exempt disposal proceeds: £12,000.00" in result.stdout
    if os.getenv("ENABLE_PDFLATEX"):
        pdf_file = Path(out_dir.rstrip("/")).with_suffix(".pdf")
        assert pdf_file.exists()
        assert pdf_file.stat().st_size > 0
