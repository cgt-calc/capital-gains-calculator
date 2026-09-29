"""Tests for Schwab interest withholding tax handling."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import ActionType, CurrencyCode
from cgt_calc.parsers.schwab import SchwabParser
from cgt_calc.spin_off_handler import SpinOffHandler


def test_schwab_interest_tax_without_symbol_is_account_level() -> None:
    """Ensure Schwab NRA tax adjustments without symbols are treated as interest tax."""
    transactions = SchwabParser.load_from_file(
        Path("tests/schwab/data/interest_tax/transactions.csv")
    )
    assert any(t.action is ActionType.INTEREST for t in transactions)
    assert any(t.action is ActionType.INTEREST_TAX for t in transactions)

    date = datetime.date(2024, 6, 27)
    currency_converter = CurrencyConverter(
        None, {date: {CurrencyCode("USD"): Decimal(1)}}
    )
    calculator = CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )
    calculator.prepare_history(list(transactions))
    report = calculator.calculate_capital_gain()

    assert report.total_foreign_interest == Decimal("2.94")
    assert report.total_interest_tax == Decimal("0.88")


def test_schwab_nra_tax_with_symbol_stays_dividend_tax(tmp_path: Path) -> None:
    """NRA Tax Adj rows tied to a security are still treated as dividend tax.

    The description is the one account-level interest withholding carries, so
    only the symbol keeps this row dividend tax.
    """
    csv_path = tmp_path / "transactions.csv"
    csv_path.write_text(
        "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
        "06/27/2024,NRA Tax Adj,FOO,SCHWAB1 INT 05/30-06/26,,,,$-1.50\n",
        encoding="utf-8",
    )

    transactions = SchwabParser.load_from_file(csv_path)

    assert len(transactions) == 1
    assert transactions[0].action is ActionType.DIVIDEND_TAX
