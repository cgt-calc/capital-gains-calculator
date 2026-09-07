"""Tests for the Schwab `NRA Withhold` action.

Schwab writes non-resident-alien withholding on a dividend under several
labels. `NRA Withhold` is one of them, and until it was recognised a single
such row stopped the whole import.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.exceptions import CalculationError
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import ActionType, CurrencyCode
from cgt_calc.parsers.schwab import SchwabParser
from cgt_calc.spin_off_handler import SpinOffHandler

if TYPE_CHECKING:
    from pathlib import Path

HEADER = "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
DIVIDEND_DATE = datetime.date(2024, 6, 27)
REVERSAL_DATE = datetime.date(2024, 7, 5)


def build_calculator(*, balance_check: bool) -> CapitalGainsCalculator:
    """Build a calculator whose rates need no exchange-rate lookup."""
    currency_converter = CurrencyConverter(
        None,
        {
            DIVIDEND_DATE: {CurrencyCode("USD"): Decimal(1)},
            REVERSAL_DATE: {CurrencyCode("USD"): Decimal(1)},
        },
    )
    return CapitalGainsCalculator(
        2024,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=balance_check,
    )


def write_transactions(tmp_path: Path, *rows: str) -> Path:
    """Write a main transaction CSV holding the given rows."""
    csv_path = tmp_path / "transactions.csv"
    csv_path.write_text(HEADER + "".join(rows), encoding="utf-8")
    return csv_path


def tax_at_source(tmp_path: Path, *rows: str) -> Decimal:
    """Return the tax attributed to the single dividend in these rows."""
    calculator = build_calculator(balance_check=False)
    calculator.prepare_history(
        list(SchwabParser.load_from_file(write_transactions(tmp_path, *rows)))
    )
    report = calculator.calculate_capital_gain()
    dividends = [
        entry.dividend
        for day in report.calculation_log_yields.values()
        for entries in day.values()
        for entry in entries
        if entry.dividend is not None
    ]
    assert len(dividends) == 1
    return dividends[0].tax_at_source


def test_nra_withhold_is_dividend_tax(tmp_path: Path) -> None:
    """A row Schwab labels `NRA Withhold` is withholding on a dividend."""
    transactions = SchwabParser.load_from_file(
        write_transactions(tmp_path, "06/27/2024,NRA Withhold,FOO,FOO INC,,,,$-1.50\n")
    )

    assert len(transactions) == 1
    assert transactions[0].action is ActionType.DIVIDEND_TAX
    assert transactions[0].amount == Decimal("-1.50")


def test_nra_withhold_is_deducted_from_the_dividend(tmp_path: Path) -> None:
    """The withheld amount is attributed to the payment it was taken from."""
    assert tax_at_source(
        tmp_path,
        "06/27/2024,Cash Dividend,FOO,FOO INC,,,,$10.00\n",
        "06/27/2024,NRA Withhold,FOO,FOO INC,,,,$-1.50\n",
    ) == Decimal("-1.50")


def test_positive_nra_withhold_nets_against_the_earlier_deduction(
    tmp_path: Path,
) -> None:
    """A later positive row returns part of the tax, as `NRA Tax Adj` does.

    The sign carries the meaning for every label in this set, so a reversal
    needs no special handling: it leaves the difference as the tax paid.

    The reversal is dated well inside `DIVIDEND_TAX_MATCH_DAYS` of the
    payment. Outside that window nothing nets, because no single payment can
    be shown to be the one the tax came from.
    """
    assert tax_at_source(
        tmp_path,
        "06/27/2024,Cash Dividend,FOO,FOO INC,,,,$10.00\n",
        "06/27/2024,NRA Withhold,FOO,FOO INC,,,,$-1.50\n",
        "07/05/2024,NRA Withhold,FOO,FOO INC,,,,$0.50\n",
    ) == Decimal("-1.00")


def test_nra_withhold_reduces_the_cash_balance(tmp_path: Path) -> None:
    """Withholding takes cash out of the account, so an unfunded one overdraws."""
    calculator = build_calculator(balance_check=True)

    with pytest.raises(CalculationError, match="Reached a negative balance"):
        calculator.prepare_history(
            list(
                SchwabParser.load_from_file(
                    write_transactions(
                        tmp_path, "06/27/2024,NRA Withhold,FOO,FOO INC,,,,$-1.50\n"
                    )
                )
            )
        )


def test_account_interest_withholding_still_bypasses_dividends(
    tmp_path: Path,
) -> None:
    """The `SCHWAB1 INT` reroute covers this label like the other three.

    Withholding on account cash interest states no symbol, so it is interest
    tax and never looks for a dividend to attach to.
    """
    transactions = SchwabParser.load_from_file(
        write_transactions(
            tmp_path, "06/27/2024,NRA Withhold,,SCHWAB1 INT 05/30-06/26,,,,$-0.88\n"
        )
    )

    assert len(transactions) == 1
    assert transactions[0].action is ActionType.INTEREST_TAX
