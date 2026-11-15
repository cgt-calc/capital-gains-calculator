"""The warning for an --interest-fund-tickers value that matches no income.

The Sharesight CLI test owns the dividend case and the message. These cover
excess reported income, which matches the holding a report reaches rather
than a dividend row's symbol.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
import logging
from typing import TYPE_CHECKING

import pytest

from cgt_calc.model import ActionType, Isin

from .calc_test_data import GBP, transaction
from .test_calc import create_calculator, get_report
from .test_rename_spin_off import rename_row

if TYPE_CHECKING:
    from cgt_calc.model import BrokerTransaction

# The bundled ISIN translation names this fund VAGP.
FUND = Isin("IE00BG47KG48")
BUY_DAY = dt.date(2024, 6, 1)
# After rename_row's day, 5 July 2024. The report's income is distributed six
# months on, on 31 January 2025, in the 2024/25 tax year: 100 units at £0.05.
ERI = transaction(
    dt.date(2024, 7, 31),
    ActionType.EXCESS_REPORTED_INCOME,
    price=0.05,
    isin=FUND,
    currency=GBP,
)


def buy(symbol: str) -> BrokerTransaction:
    """Buy 100 units for £2,000."""
    return transaction(BUY_DAY, ActionType.BUY, symbol, 100, 20, 0, -2000, GBP)


@pytest.mark.parametrize(
    ("rows", "ticker", "interest", "warnings"),
    [
        pytest.param([buy("VAGP"), ERI], "VAGP", Decimal(5), [], id="held"),
        pytest.param(
            [buy("VAGP"), rename_row("VAGP", "VAGPNEW"), ERI],
            "VAGPNEW",
            Decimal(5),
            [],
            id="held under its new name",
        ),
        # A Sharesight holding: the report reaches VAGP, which is not held, so
        # it is not income, and the dividend stays a dividend.
        pytest.param(
            [
                buy("LSE:VAGP"),
                transaction(
                    dt.date(2024, 9, 30),
                    ActionType.DIVIDEND,
                    "VAGP.LSE",
                    amount=10,
                    currency=GBP,
                ),
                ERI,
            ],
            "VAGP",
            Decimal(0),
            [
                (
                    "Interest fund ticker 'VAGP' matched no dividend or excess "
                    "reported income, so it changed nothing. Give each fund's "
                    "symbol exactly as its income shows it. Your files have "
                    "income under VAGP.LSE."
                )
            ],
            id="named only by the report",
        ),
    ],
)
def test_excess_reported_income_matches_the_holding_it_reaches(
    rows: list[BrokerTransaction],
    ticker: str,
    interest: Decimal,
    warnings: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A fund whose only income is excess reported income is matched.

    The interest is the warning's evidence: where it says the ticker changed
    nothing, no income is reported as interest.
    """
    calculator = create_calculator(
        tax_year=2024, balance_check=False, interest_fund_tickers=[ticker]
    )
    with caplog.at_level(logging.WARNING, logger="cgt_calc.main"):
        report = get_report(calculator, rows)

    assert report.total_foreign_interest == interest
    assert [
        record.message
        for record in caplog.records
        if record.message.startswith("Interest fund ticker")
    ] == warnings
