"""The report states its total allowable costs to the penny.

The terminal and the PDF both print this figure: the terminal rounds it to the
penny again and the PDF prints it as it stands. A change to how the report
rounds it can move what the user sees: a penny or more in the terminal, or a
different number of digits in the PDF.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from cgt_calc.model import ActionType

from .calc_test_data import USD, transaction
from .test_calc import create_calculator, get_report

# Both dates have a rate in tests/exchange_rates_data.csv.
BUY_DAY = dt.date(2024, 5, 1)  # USD 1.2457 per £1
SELL_DAY = dt.date(2024, 6, 3)  # USD 1.2709 per £1


def test_allowable_costs_are_reported_to_the_penny() -> None:
    """A sale converted from dollars reports its cost to the penny.

    Ten shares are bought for $1,200.00 and sold for $1,304.00:

    - Cost: 1,200 / 1.2457 = £963.31380 (1.2457 x 963 = 1,199.6091, and
      0.3909 / 1.2457 = 0.31380).
    - Proceeds: 1,304 / 1.2709 = £1,026.04454 (1.2709 x 1,026 = 1,303.9434,
      and 0.0566 / 1.2709 = 0.04454).
    - Gain: 1,026.04454 - 963.31380 = 62.73074, reported as £62.73.
    - Allowable costs: the proceeds less that gain, 1,026.04454 - 62.73 =
      963.31454, reported as £963.31.

    £963.31 is also the cost itself to the penny, and the reported proceeds
    less the reported gain: £1,026.04 - £62.73. The unrounded total, 963.31454,
    is 0.45p above it, so it comes to £963.31 whether it is rounded to the
    nearest penny or down; this sale does not tell the two apart.

    Rounded first to three places it would be 963.315, which the terminal shows
    as £963.32; to four places 963.3145, to one 963.3 and to none 963. Left
    unrounded, the PDF would print all of 963.3145353686...
    """
    calculator = create_calculator(tax_year=2024, balance_check=False)
    report = get_report(
        calculator,
        [
            transaction(BUY_DAY, ActionType.BUY, "FOO", 10, 120, 0, -1200, USD),
            transaction(SELL_DAY, ActionType.SELL, "FOO", 10, 130.40, 0, 1304, USD),
        ],
    )

    assert report.allowable_costs == Decimal("963.31")
