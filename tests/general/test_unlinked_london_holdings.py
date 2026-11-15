"""Which London holdings a fund's reports already reach through a rename.

The Sharesight CLI tests own the warning's message and its other conditions.
No input file can rename a holding to an `LSE:` name, so a rename reaching
one is built here from calculator rows: the warning is decided after
ingestion, by whether the fund's reports actually reach the holding.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
import logging
import re
from typing import TYPE_CHECKING

import pytest

from cgt_calc.model import ActionType, Isin

from .calc_test_data import GBP, transaction
from .test_calc import create_calculator, get_report
from .test_rename_spin_off import rename_row

if TYPE_CHECKING:
    from cgt_calc.model import BrokerTransaction

# The bundled ISIN translation names this fund VAGP.
VAGP_FUND = Isin("IE00BG47KG48")
# The bundled ISIN translation names this fund CSP1 and CSPX, and a row
# exporting it as SXR8 is read as CSP1.
CSP1_FUND = Isin("IE00B5BMR087")
BUY_DAY = dt.date(2024, 6, 1)


def buy(symbol: str, isin: Isin | None = None) -> BrokerTransaction:
    """Buy 100 units for £2,000."""
    return transaction(
        BUY_DAY, ActionType.BUY, symbol, 100, 20, 0, -2000, GBP, isin=isin
    )


def eri(isin: Isin) -> BrokerTransaction:
    """Report £0.05 a unit for the year to 31 July 2024.

    After rename_row's day, 5 July 2024. Distributed on 31 January 2025, in
    2024/25: a holding the report reaches costs 100 x £0.05 = £5.00 more.
    """
    return transaction(
        dt.date(2024, 7, 31),
        ActionType.EXCESS_REPORTED_INCOME,
        price=0.05,
        isin=isin,
        currency=GBP,
    )


@pytest.mark.parametrize(
    ("rows", "fund", "costs", "warned"),
    [
        # The rename joins LSE:VAGP to VAGP, so VAGP's report reaches it.
        pytest.param(
            [buy("VAGP"), rename_row("VAGP", "LSE:VAGP"), eri(VAGP_FUND)],
            VAGP_FUND,
            {"LSE:VAGP": Decimal(2005)},
            [],
            id="renamed to the London name",
        ),
        # ZZQX has no ISIN, so the report reaches VAGP only, not the name
        # ZZQX is renamed to: LSE:VAGP keeps its £2,000 and is warned about.
        pytest.param(
            [buy("ZZQX"), rename_row("ZZQX", "LSE:VAGP"), eri(VAGP_FUND)],
            VAGP_FUND,
            {"LSE:VAGP": Decimal(2000)},
            ["LSE:VAGP"],
            id="held only under the name it is renamed to",
        ),
        # SXR8 is read as CSP1 before the rename is applied, so the rename's
        # old name SXR8 joins LSE:CSP1 to nothing the report reaches.
        pytest.param(
            [
                buy("SXR8", CSP1_FUND),
                buy("LSE:CSP1"),
                rename_row("SXR8", "LSE:CSP1"),
                eri(CSP1_FUND),
            ],
            CSP1_FUND,
            {"CSP1": Decimal(2005), "LSE:CSP1": Decimal(2000)},
            ["LSE:CSP1"],
            id="renamed from a name read as another",
        ),
    ],
)
def test_a_rename_that_carries_the_report_leaves_the_holding_alone(
    rows: list[BrokerTransaction],
    fund: Isin,
    costs: dict[str, Decimal],
    warned: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Warn only for a London holding the fund's report does not reach."""
    calculator = create_calculator(tax_year=2024, balance_check=False)
    # The CLI records every fund its ERI data has a row for.
    calculator.isin_converter.isins_with_eri = {fund}
    with caplog.at_level(logging.WARNING, logger="cgt_calc.main"):
        report = get_report(calculator, rows)

    assert {
        entry.symbol: entry.amount for entry in report.portfolio if entry.quantity
    } == costs
    assert [
        match[1]
        for record in caplog.records
        if (match := re.search(r"links them to your holding (\S+)\.", record.message))
    ] == warned
