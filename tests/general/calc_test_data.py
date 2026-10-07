"""Test data for calc tests."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CalculationEntry,
    CurrencyCode,
    Isin,
    RuleType,
)
from cgt_calc.util import round_decimal

USD = CurrencyCode("USD")
GBP = CurrencyCode("GBP")


def interest_transaction(
    date: datetime.date,
    amount: float,
    currency: CurrencyCode = USD,
) -> BrokerTransaction:
    """Create interest transaction."""
    return transaction(date, ActionType.INTEREST, None, None, None, 0, amount, currency)


def dividend_transaction(
    date: datetime.date,
    symbol: str,
    amount: float,
) -> BrokerTransaction:
    """Create dividend transaction."""
    return transaction(
        date,
        ActionType.DIVIDEND,
        symbol,
        None,
        None,
        0,
        amount,
    )


def dividend_tax_transaction(
    date: datetime.date,
    symbol: str,
    amount: float,
) -> BrokerTransaction:
    """Create dividend tax transaction."""
    return transaction(
        date,
        ActionType.DIVIDEND_TAX,
        symbol,
        None,
        None,
        0,
        -amount,
    )


def eri_transaction(
    date: datetime.date,
    isin: Isin,
    price: float,
) -> BrokerTransaction:
    """Create excess reported income transaction."""
    return transaction(date, ActionType.EXCESS_REPORTED_INCOME, price=price, isin=isin)


def buy_transaction(
    date: datetime.date,
    symbol: str,
    quantity: float,
    price: float,
    fees: float,
    amount: float,
    isin: Isin | None = None,
) -> BrokerTransaction:
    """Create buy transaction."""
    return transaction(
        date,
        ActionType.BUY,
        symbol,
        quantity,
        price,
        fees,
        amount,
        isin=isin,
    )


def sell_transaction(
    date: datetime.date,
    symbol: str,
    quantity: float,
    price: float,
    fees: float,
    amount: float,
    isin: Isin | None = None,
) -> BrokerTransaction:
    """Create sell transaction."""
    return transaction(
        date,
        ActionType.SELL,
        symbol,
        quantity,
        price,
        fees,
        amount,
        isin=isin,
    )


def transfer_transaction(
    date: datetime.date,
    amount: float,
    fees: float = 0,
) -> BrokerTransaction:
    """Create transfer transaction."""
    return transaction(
        date,
        ActionType.TRANSFER,
        fees=fees,
        amount=amount,
    )


def transfer_to_spouse_transaction(
    date: datetime.date,
    symbol: str,
    quantity: float,
    currency: CurrencyCode = GBP,
) -> BrokerTransaction:
    """Create a no gain/no loss transfer to spouse transaction."""
    return transaction(
        date,
        ActionType.TRANSFER_TO_SPOUSE,
        symbol,
        quantity,
        currency=currency,
    )


def transaction(
    date: datetime.date,
    action_type: ActionType,
    symbol: str | None = None,
    quantity: float | None = None,
    price: float | None = None,
    fees: float = 0.0,
    amount: float | None = None,
    currency: CurrencyCode = USD,
    isin: Isin | None = None,
    broker: str = "Testing",
) -> BrokerTransaction:
    """Create transaction."""
    return BrokerTransaction(
        date,
        action_type,
        symbol=symbol,
        description=f"Description for symbol {symbol}",
        quantity=round_decimal(Decimal(quantity), 6) if quantity else None,
        price=round_decimal(Decimal(price), 6) if price else None,
        fees=round_decimal(Decimal(fees), 6),
        amount=round_decimal(Decimal(amount), 6) if amount else None,
        currency=currency,
        broker=broker,
        isin=isin,
    )


def split_transaction(
    date: datetime.date,
    symbol: str,
    quantity: float,
) -> BrokerTransaction:
    """Create stock split transaction."""
    return BrokerTransaction(
        date,
        ActionType.STOCK_SPLIT,
        symbol,
        f"Split of {symbol}",
        round_decimal(Decimal(quantity), 6),
        price=Decimal(0),
        fees=Decimal(0),
        amount=Decimal(0),
        currency=CurrencyCode("USD"),
        broker="Testing",
    )


# HMRC's Example 3 spans a tax year end: the disposal of 31 March is matched
# against acquisitions made in April, which fall in the next year. The same
# transactions are therefore checked twice, once per year.
CRYPTO22253_TRANSACTIONS = [
    transfer_transaction(datetime.date(day=1, month=1, year=2021), 2000),
    # Opening pool: 2,000 tokens for £1,000.
    buy_transaction(
        date=datetime.date(day=1, month=1, year=2021),
        symbol="TOK",
        quantity=2000,
        price=0.5,
        fees=0,
        amount=-1000,
    ),
    sell_transaction(
        date=datetime.date(day=31, month=3, year=2021),
        symbol="TOK",
        quantity=1000,
        price=0.4,
        fees=0,
        amount=400,
    ),
    sell_transaction(
        date=datetime.date(day=20, month=4, year=2021),
        symbol="TOK",
        quantity=500,
        price=0.3,
        fees=0,
        amount=150,
    ),
    buy_transaction(
        date=datetime.date(day=21, month=4, year=2021),
        symbol="TOK",
        quantity=700,
        price=0.25,
        fees=0,
        amount=-175,
    ),
    buy_transaction(
        date=datetime.date(day=28, month=4, year=2021),
        symbol="TOK",
        quantity=500,
        price=0.2,
        fees=0,
        amount=-100,
    ),
    buy_transaction(
        date=datetime.date(day=1, month=5, year=2021),
        symbol="TOK",
        quantity=500,
        price=0.3,
        fees=0,
        amount=-150,
    ),
]

calc_basic_data = [
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=5, year=2020), 5000),
            buy_transaction(
                date=datetime.date(day=1, month=5, year=2020),
                symbol="FOO",
                quantity=3,
                price=5,
                fees=1,
                amount=-16,
            ),
            sell_transaction(
                date=datetime.date(day=1, month=5, year=2020),
                symbol="FOO",
                quantity=3,
                price=6,
                fees=1,
                amount=17,
            ),
        ],
        1.00,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            datetime.date(day=1, month=5, year=2020): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(3),
                        amount=Decimal(-16),
                        allowable_cost=Decimal(16),
                        fees=Decimal(1),
                        new_quantity=Decimal(3),
                        new_pool_cost=Decimal(16),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal(3),
                        amount=Decimal(17),
                        gain=Decimal(1),
                        allowable_cost=Decimal(16) + Decimal(1),
                        fees=Decimal(1),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
        id="same_day_gain",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=4, year=2014), 6280),
            buy_transaction(
                date=datetime.date(day=1, month=4, year=2014),
                symbol="LOB",
                quantity=1000,
                price=4,
                fees=150,
                amount=-4150,
            ),
            buy_transaction(
                date=datetime.date(day=1, month=9, year=2017),
                symbol="LOB",
                quantity=500,
                price=4.1,
                fees=80,
                amount=-2130,
            ),
            sell_transaction(
                date=datetime.date(day=1, month=5, year=2020),
                symbol="LOB",
                quantity=700,
                price=4.8,
                fees=100,
                amount=3260,
            ),
            sell_transaction(
                date=datetime.date(day=1, month=2, year=2021),
                symbol="LOB",
                quantity=400,
                price=5.2,
                fees=105,
                amount=1975,
            ),
        ],
        # exact amount would be £629+2/3
        629.66,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            datetime.date(day=1, month=5, year=2020): {
                "sell$LOB": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(700),
                        amount=Decimal(3260),
                        gain=Decimal("329.3333"),
                        allowable_cost=Decimal("2930.6667") + Decimal(100),
                        fees=Decimal(100),
                        new_quantity=Decimal(800),
                        new_pool_cost=Decimal("3349.3333"),
                    ),
                ],
            },
            datetime.date(day=1, month=2, year=2021): {
                "sell$LOB": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(400),
                        amount=Decimal(1975),
                        gain=Decimal("300.3333"),
                        allowable_cost=Decimal("1674.6667") + Decimal(105),
                        fees=Decimal(105),
                        new_quantity=Decimal(400),
                        new_pool_cost=Decimal("1674.6667"),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
        # https://assets.publishing.service.gov.uk/government/uploads/system/uploads/attachment_data/file/972646/HS284_Example_3_2021.pdf
        id="HS284_Example_3_2021",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=1, year=2019), 15100),
            buy_transaction(
                date=datetime.date(day=1, month=1, year=2019),
                symbol="MSP",
                quantity=9500,
                price=1.5,
                fees=0,
                amount=-14250,
            ),
            sell_transaction(
                date=datetime.date(day=30, month=8, year=2020),
                symbol="MSP",
                quantity=4000,
                price=1.5,
                fees=0,
                amount=6000,
            ),
            buy_transaction(
                date=datetime.date(day=11, month=9, year=2020),
                symbol="MSP",
                quantity=500,
                price=1.7,
                fees=0,
                amount=-850,
            ),
        ],
        -100,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            datetime.date(day=30, month=8, year=2020): {
                "sell$MSP": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(500),
                        amount=Decimal(750),
                        gain=Decimal(-100),
                        allowable_cost=Decimal(850),
                        fees=Decimal(0),
                        new_quantity=Decimal(9000),
                        new_pool_cost=Decimal(13500),
                        bed_and_breakfast_date_index=(
                            datetime.date(day=11, month=9, year=2020)
                        ),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(3500),
                        amount=Decimal(5250),
                        gain=Decimal(0),
                        allowable_cost=Decimal(5250),
                        fees=Decimal(0),
                        new_quantity=Decimal(5500),
                        new_pool_cost=Decimal(8250),
                    ),
                ],
            },
            datetime.date(day=11, month=9, year=2020): {
                "buy$MSP": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(500),
                        amount=Decimal(-750),
                        allowable_cost=Decimal(850),
                        fees=Decimal(0),
                        new_quantity=Decimal(6000),
                        new_pool_cost=Decimal(9000),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
        # https://www.gov.uk/government/publications/shares-and-capital-gains-tax-hs284-self-assessment-helpsheet/
        id="HS284_Example_2_2021",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=3, year=2021), 6782),
            buy_transaction(
                date=datetime.date(day=2, month=3, year=2021),
                symbol="FOO",
                quantity=100,
                price=25,
                fees=6,
                amount=-2506,
            ),
            buy_transaction(
                date=datetime.date(day=3, month=3, year=2021),
                symbol="FOO",
                quantity=154,
                price=27.7,
                fees=10,
                amount=-4275.8,
            ),
            sell_transaction(
                date=datetime.date(day=3, month=3, year=2021),
                symbol="FOO",
                quantity=254,
                price=28.03,
                fees=15,
                amount=7104.62,
            ),
            buy_transaction(
                date=datetime.date(day=6, month=3, year=2021),
                symbol="FOO",
                quantity=90,
                price=28,
                fees=5,
                amount=-2525,
            ),
            sell_transaction(
                date=datetime.date(day=6, month=3, year=2021),
                symbol="FOO",
                quantity=90,
                price=27,
                fees=5,
                amount=2425,
            ),
        ],
        222.82,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            datetime.date(day=2, month=3, year=2021): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(100),
                        amount=Decimal(-2506),
                        allowable_cost=Decimal(2506),
                        fees=Decimal(6),
                        new_quantity=Decimal(100),
                        new_pool_cost=Decimal(2506),
                    ),
                ],
            },
            datetime.date(day=3, month=3, year=2021): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(154),
                        amount=Decimal("-4275.8"),
                        allowable_cost=Decimal("4275.8"),
                        fees=Decimal(10),
                        new_quantity=Decimal(254),
                        new_pool_cost=Decimal("6781.8"),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal(154),
                        amount=Decimal("4307.5255"),
                        gain=Decimal("31.7255"),
                        allowable_cost=Decimal("4275.8") + Decimal("9.0945"),
                        fees=Decimal("9.0945"),
                        new_quantity=Decimal(100),
                        new_pool_cost=Decimal(2506),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(100),
                        amount=Decimal("2797.0945"),
                        gain=Decimal("291.0945"),
                        allowable_cost=Decimal(2506) + Decimal("5.9055"),
                        fees=Decimal("5.9055"),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                    ),
                ],
            },
            datetime.date(day=6, month=3, year=2021): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(90),
                        amount=Decimal(-2525),
                        allowable_cost=Decimal(2525),
                        fees=Decimal(5),
                        new_quantity=Decimal(90),
                        new_pool_cost=Decimal(2525),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal(90),
                        amount=Decimal(2425),
                        gain=Decimal(-100),
                        allowable_cost=Decimal(2525) + Decimal(5),
                        fees=Decimal(5),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
        # Complex case when same day rule should be applied before bed & breakfast.
        id="bed_and_breakfast_vs_same_day",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=3, year=2021), 6782),
            buy_transaction(
                date=datetime.date(day=2, month=3, year=2021),
                symbol="FOO",
                quantity=100,
                price=25,
                fees=6,
                amount=-2506,
            ),
            buy_transaction(
                date=datetime.date(day=3, month=3, year=2021),
                symbol="FOO",
                quantity=154,
                price=27.7,
                fees=10,
                amount=-4275.8,
            ),
            sell_transaction(
                date=datetime.date(day=3, month=3, year=2021),
                symbol="FOO",
                quantity=254,
                price=28.03,
                fees=15,
                amount=7104.62,
            ),
            buy_transaction(
                date=datetime.date(day=6, month=3, year=2021),
                symbol="FOO",
                quantity=90,
                price=28,
                fees=5,
                amount=-2525,
            ),
            sell_transaction(
                date=datetime.date(day=6, month=3, year=2021),
                symbol="FOO",
                quantity=90,
                price=27,
                fees=5,
                amount=2425,
            ),
            buy_transaction(
                date=datetime.date(day=2, month=4, year=2021),
                symbol="FOO",
                quantity=30.5,
                price=30.2,
                fees=4,
                amount=-925.1,
            ),
        ],
        62.05,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            datetime.date(day=2, month=3, year=2021): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(100),
                        amount=Decimal(-2506),
                        allowable_cost=Decimal(2506),
                        fees=Decimal(6),
                        new_quantity=Decimal(100),
                        new_pool_cost=Decimal(2506),
                    ),
                ],
            },
            datetime.date(day=3, month=3, year=2021): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(154),
                        amount=Decimal("-4275.8"),
                        allowable_cost=Decimal("4275.8"),
                        fees=Decimal(10),
                        new_quantity=Decimal(254),
                        new_pool_cost=Decimal("6781.8"),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal(154),
                        amount=Decimal("4307.5255"),
                        gain=Decimal("31.7255"),
                        allowable_cost=Decimal("4275.8") + Decimal("9.0945"),
                        fees=Decimal("9.0945"),
                        new_quantity=Decimal(100),
                        new_pool_cost=Decimal(2506),
                    ),
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("30.5"),
                        amount=Decimal("853.1138"),
                        gain=Decimal("-71.9862"),
                        allowable_cost=Decimal("925.1") + Decimal("1.8012"),
                        fees=Decimal("1.8012"),
                        new_quantity=Decimal("69.5"),
                        new_pool_cost=Decimal("1741.67"),
                        bed_and_breakfast_date_index=(
                            datetime.date(day=2, month=4, year=2021)
                        ),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("69.5"),
                        amount=Decimal("1943.9807"),
                        gain=Decimal("202.3107"),
                        allowable_cost=Decimal("1741.67") + Decimal("4.1043"),
                        fees=Decimal("4.1043"),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                    ),
                ],
            },
            datetime.date(day=6, month=3, year=2021): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(90),
                        amount=Decimal(-2525),
                        allowable_cost=Decimal(2525),
                        fees=Decimal(5),
                        new_quantity=Decimal(90),
                        new_pool_cost=Decimal(2525),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal(90),
                        amount=Decimal(2425),
                        gain=Decimal(-100),
                        allowable_cost=Decimal(2525) + Decimal(5),
                        fees=Decimal(5),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                    ),
                ],
            },
            datetime.date(day=2, month=4, year=2021): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("30.5"),
                        amount=Decimal("-764.33"),
                        allowable_cost=Decimal("925.1"),
                        fees=Decimal(4),
                        new_quantity=Decimal("30.5"),
                        new_pool_cost=Decimal("764.33"),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
        # Add real bed & breakfast entries.
        id="with_bed_and_breakfast",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=3, year=2021), 6782),
            buy_transaction(
                date=datetime.date(day=2, month=3, year=2021),
                symbol="FOO",
                quantity=100,
                price=25,
                fees=6,
                amount=-2506,
            ),
            buy_transaction(
                date=datetime.date(day=3, month=3, year=2021),
                symbol="FOO",
                quantity=154,
                price=27.7,
                fees=10,
                amount=-4275.8,
            ),
            sell_transaction(
                date=datetime.date(day=3, month=3, year=2021),
                symbol="FOO",
                quantity=254,
                price=28.03,
                fees=15,
                amount=7104.62,
            ),
            buy_transaction(
                date=datetime.date(day=5, month=3, year=2021),
                symbol="FOO",
                quantity=90,
                price=28,
                fees=5,
                amount=-2525,
            ),
            sell_transaction(
                date=datetime.date(day=6, month=3, year=2021),
                symbol="FOO",
                quantity=90,
                price=27,
                fees=5,
                amount=2425,
            ),
            buy_transaction(
                date=datetime.date(day=2, month=4, year=2021),
                symbol="FOO",
                quantity=30.5,
                price=30.2,
                fees=4,
                amount=-925.1,
            ),
        ],
        62.05,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            datetime.date(day=2, month=3, year=2021): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(100),
                        amount=Decimal(-2506),
                        allowable_cost=Decimal(2506),
                        fees=Decimal(6),
                        new_quantity=Decimal(100),
                        new_pool_cost=Decimal(2506),
                    ),
                ],
            },
            datetime.date(day=3, month=3, year=2021): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(154),
                        amount=Decimal("-4275.8"),
                        allowable_cost=Decimal("4275.8"),
                        fees=Decimal(10),
                        new_quantity=Decimal(254),
                        new_pool_cost=Decimal("6781.8"),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal(154),
                        amount=Decimal("4307.5255"),
                        gain=Decimal("31.7255"),
                        allowable_cost=Decimal("4275.8") + Decimal("9.0945"),
                        fees=Decimal("9.0945"),
                        new_quantity=Decimal(100),
                        new_pool_cost=Decimal(2506),
                    ),
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(90),
                        amount=Decimal("2517.3850"),
                        gain=Decimal("-7.6150"),
                        allowable_cost=Decimal("2525.0") + Decimal("5.3150"),
                        fees=Decimal("5.3150"),
                        new_quantity=Decimal(10),
                        new_pool_cost=Decimal("250.60"),
                        bed_and_breakfast_date_index=(
                            datetime.date(day=5, month=3, year=2021)
                        ),
                    ),
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("10.0"),
                        amount=Decimal("279.7094488188976377952755906"),
                        gain=Decimal("-23.6020265909384277784949012"),
                        allowable_cost=Decimal("303.3114754098360655737704918")
                        + Decimal("0.5905511811023622047244094488"),
                        fees=Decimal("0.5905511811023622047244094488"),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                        bed_and_breakfast_date_index=(
                            datetime.date(day=2, month=4, year=2021)
                        ),
                    ),
                ],
            },
            datetime.date(day=5, month=3, year=2021): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(90),
                        amount=Decimal("-2255.4000"),
                        allowable_cost=Decimal("2525.0000"),
                        fees=Decimal(5),
                        new_quantity=Decimal(90),
                        new_pool_cost=Decimal("2255.4000"),
                    ),
                ],
            },
            datetime.date(day=6, month=3, year=2021): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("20.5"),
                        amount=Decimal("552.3611"),
                        gain=Decimal("-69.4274"),
                        allowable_cost=Decimal("621.7885") + Decimal("1.13888"),
                        fees=Decimal("1.13888"),
                        new_quantity=Decimal("69.5"),
                        new_pool_cost=Decimal("1741.6700"),
                        bed_and_breakfast_date_index=(
                            datetime.date(day=2, month=4, year=2021)
                        ),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("69.5"),
                        amount=Decimal("1872.6389"),
                        gain=Decimal("130.9689"),
                        allowable_cost=Decimal("1741.67") + Decimal("3.8611"),
                        fees=Decimal("3.8611"),
                        new_quantity=Decimal(0),
                        new_pool_cost=Decimal(0),
                    ),
                ],
            },
            datetime.date(day=2, month=4, year=2021): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("30.5"),
                        amount=Decimal("-764.33"),
                        allowable_cost=Decimal("925.1"),
                        fees=Decimal(4),
                        new_quantity=Decimal("30.5"),
                        new_pool_cost=Decimal("764.33"),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
        # Add real bed & breakfast entries.
        id="with_bed_and_breakfast_2",
    ),
    pytest.param(
        2023,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=1, year=2023), 52503),
            buy_transaction(
                date=datetime.date(day=1, month=1, year=2023),
                symbol="FOO",
                quantity=500,
                price=101.1,
                fees=1,
                amount=-50551,
            ),
            sell_transaction(
                date=datetime.date(day=25, month=6, year=2023),
                symbol="FOO",
                quantity=30,
                price=100,
                fees=1,
                amount=2999,
            ),
            buy_transaction(
                date=datetime.date(day=30, month=6, year=2023),
                symbol="FOO",
                quantity=50,
                price=99,
                fees=1,
                amount=-4951,
            ),
            sell_transaction(
                date=datetime.date(day=30, month=6, year=2023),
                symbol="FOO",
                quantity=100,
                price=100,
                fees=1,
                amount=9999,
            ),
        ],
        -41.16,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            datetime.date(day=25, month=6, year=2023): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("30.0"),
                        amount=Decimal(2999),
                        gain=Decimal("-34.06"),
                        allowable_cost=Decimal("3033.06") + Decimal("1.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal("47517.94"),
                    ),
                ],
            },
            datetime.date(day=30, month=6, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("50.0"),
                        amount=Decimal(-4951),
                        gain=Decimal(0),
                        allowable_cost=Decimal("4951.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(520),
                        new_pool_cost=Decimal("52468.94"),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal("50.0"),
                        amount=Decimal("4999.50"),
                        gain=Decimal("48.5"),
                        allowable_cost=Decimal(4951) + Decimal("0.5"),
                        fees=Decimal("0.5"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal("47517.94"),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("50.0"),
                        amount=Decimal("4999.5"),
                        gain=Decimal("-55.6"),
                        allowable_cost=Decimal("5055.1") + Decimal("0.5"),
                        fees=Decimal("0.5"),
                        new_quantity=Decimal(420),
                        new_pool_cost=Decimal("42462.84"),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
        id="issue_460.sell_on_30/6_is_split_into_104+same_day",
    ),
    pytest.param(
        2023,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=1, year=2023), 52503),
            buy_transaction(
                date=datetime.date(day=1, month=1, year=2023),
                symbol="FOO",
                quantity=500,
                price=101.1,
                fees=1,
                amount=-50551,
            ),
            sell_transaction(
                date=datetime.date(day=25, month=6, year=2023),
                symbol="FOO",
                quantity=30,
                price=100,
                fees=1,
                amount=2999,
            ),
            buy_transaction(
                date=datetime.date(day=30, month=6, year=2023),
                symbol="FOO",
                quantity=50,
                price=99,
                fees=1,
                amount=-4951,
            ),
            sell_transaction(
                date=datetime.date(day=30, month=6, year=2023),
                symbol="FOO",
                quantity=100,
                price=100,
                fees=1,
                amount=9999,
            ),
            buy_transaction(
                date=datetime.date(day=1, month=7, year=2023),
                symbol="FOO",
                quantity=50,
                price=99,
                fees=1,
                amount=-4951,
            ),
        ],
        62.94,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            datetime.date(day=25, month=6, year=2023): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("30.0"),
                        amount=Decimal(2999),
                        gain=Decimal("28.4"),
                        allowable_cost=Decimal("2970.6") + Decimal("1.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal("47517.94"),
                        bed_and_breakfast_date_index=datetime.date(
                            day=1, month=7, year=2023
                        ),
                    ),
                ],
            },
            datetime.date(day=30, month=6, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("50.0"),
                        amount=Decimal(-4951),
                        gain=Decimal(0),
                        allowable_cost=Decimal("4951.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(520),
                        new_pool_cost=Decimal("52468.94"),
                    ),
                ],
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SAME_DAY,
                        quantity=Decimal("50.0"),
                        amount=Decimal("4999.50"),
                        gain=Decimal("48.5"),
                        allowable_cost=Decimal(4951) + Decimal("0.5"),
                        fees=Decimal("0.5"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal("47517.94"),
                    ),
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("20.0"),
                        amount=Decimal("1999.8"),
                        gain=Decimal("19.4"),
                        allowable_cost=Decimal("1980.4") + Decimal("0.2"),
                        fees=Decimal("0.2"),
                        new_quantity=Decimal(450),
                        new_pool_cost=Decimal("45495.9"),
                        bed_and_breakfast_date_index=datetime.date(
                            day=1, month=7, year=2023
                        ),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("30.0"),
                        amount=Decimal("2999.7"),
                        gain=Decimal("-33.36"),
                        allowable_cost=Decimal("3033.06") + Decimal("0.3"),
                        fees=Decimal("0.3"),
                        new_quantity=Decimal(420),
                        new_pool_cost=Decimal("42462.84"),
                    ),
                ],
            },
            datetime.date(day=1, month=7, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("50.0"),
                        amount=Decimal("-5055.1"),
                        gain=Decimal(0),
                        allowable_cost=Decimal("4951.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal("47517.94"),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
        id="sell_on_30/6_is_split_into_104+same_day+b&d",
    ),
    pytest.param(
        2023,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=5, year=2020), 100),
            buy_transaction(
                date=datetime.date(day=2, month=5, year=2023),
                symbol="FOO",
                quantity=12,
                price=5,
                amount=-60,
                fees=0,
            ),
            sell_transaction(
                date=datetime.date(day=10, month=5, year=2023),
                symbol="FOO",
                quantity=2,
                price=6,
                amount=12,  # 2.00 gain
                fees=0,
            ),
            split_transaction(
                date=datetime.date(day=15, month=5, year=2023),
                symbol="FOO",
                quantity=10,  # 2x split
            ),
            sell_transaction(
                date=datetime.date(day=10, month=6, year=2023),
                symbol="FOO",
                quantity=2,
                price=5,
                amount=10,  # 5.00 gain
                fees=0,
            ),
        ],
        7.00,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            datetime.date(day=2, month=5, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(12),
                        amount=Decimal(-60),
                        allowable_cost=Decimal(60),
                        new_quantity=Decimal(12),
                        fees=Decimal(0),
                        new_pool_cost=Decimal(60),
                    ),
                ],
            },
            datetime.date(day=10, month=5, year=2023): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(2),
                        amount=Decimal(12),
                        gain=Decimal(2),
                        allowable_cost=Decimal(10),
                        fees=Decimal(0),
                        new_quantity=Decimal(10),
                        new_pool_cost=Decimal(50),
                    ),
                ],
            },
            datetime.date(day=15, month=5, year=2023): {
                # A reorganisation, not a free acquisition: nothing is bought
                # or sold, the pool cost is untouched, and only the number of
                # units it is spread over changes (TCGA 1992 s127).
                "split$FOO": [
                    CalculationEntry(
                        RuleType.STOCK_SPLIT,
                        quantity=Decimal(0),
                        amount=Decimal(0),
                        allowable_cost=Decimal(0),
                        new_quantity=Decimal(20),
                        fees=Decimal(0),
                        new_pool_cost=Decimal(50),
                    ),
                ],
            },
            datetime.date(day=10, month=6, year=2023): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(2),
                        amount=Decimal(10),
                        gain=Decimal(5),
                        fees=Decimal(0),
                        allowable_cost=Decimal(5),
                        new_quantity=Decimal(18),
                        new_pool_cost=Decimal(45),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
        id="split_then_sell",
    ),
    pytest.param(
        2023,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=5, year=2020), 100),
            buy_transaction(
                date=datetime.date(day=7, month=4, year=2023),
                symbol="FOO",
                quantity=12,
                price=5,
                amount=-60,
                fees=0,
            ),
            sell_transaction(
                date=datetime.date(day=10, month=5, year=2023),
                symbol="FOO",
                quantity=2,
                price=6,
                amount=12,  # 2.00 gain before B&B matching; realised result is -3.00
                fees=0,
            ),
            split_transaction(
                date=datetime.date(day=15, month=5, year=2023),
                symbol="FOO",
                quantity=10,  # 2x split
            ),
            buy_transaction(
                date=datetime.date(day=8, month=6, year=2023),
                symbol="FOO",
                quantity=2,
                price=5,
                amount=-10,  # B&B matched against the 10 May sell
                fees=0,
            ),
        ],
        -3.00,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            datetime.date(day=7, month=4, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(12),
                        amount=Decimal(-60),
                        allowable_cost=Decimal(60),
                        new_quantity=Decimal(12),
                        fees=Decimal(0),
                        new_pool_cost=Decimal(60),
                    ),
                ],
            },
            datetime.date(day=10, month=5, year=2023): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(1),
                        amount=Decimal(6),
                        gain=Decimal(-4),
                        allowable_cost=Decimal(10),
                        fees=Decimal(0),
                        new_quantity=Decimal(11),
                        new_pool_cost=Decimal(55),
                        bed_and_breakfast_date_index=(
                            datetime.date(day=8, month=6, year=2023)
                        ),
                    ),
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(1),
                        amount=Decimal(6),
                        gain=Decimal(1),
                        allowable_cost=Decimal(5),
                        fees=Decimal(0),
                        new_quantity=Decimal(10),
                        new_pool_cost=Decimal(50),
                    ),
                ],
            },
            datetime.date(day=15, month=5, year=2023): {
                # A reorganisation, not a free acquisition: nothing is bought
                # or sold, the pool cost is untouched, and only the number of
                # units it is spread over changes (TCGA 1992 s127).
                "split$FOO": [
                    CalculationEntry(
                        RuleType.STOCK_SPLIT,
                        quantity=Decimal(0),
                        amount=Decimal(0),
                        allowable_cost=Decimal(0),
                        new_quantity=Decimal(20),
                        fees=Decimal(0),
                        new_pool_cost=Decimal(50),
                    ),
                ],
            },
            datetime.date(day=8, month=6, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(2),
                        amount=Decimal(-5),
                        fees=Decimal(0),
                        allowable_cost=Decimal(10),
                        new_quantity=Decimal(22),
                        new_pool_cost=Decimal(55),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
        id="split_then_bnb",
    ),
    pytest.param(
        2019,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=1, year=2019), 1000),
            # The pool as HMRC's example starts it: 8,000 tokens costing £1,000.
            buy_transaction(
                date=datetime.date(day=1, month=1, year=2019),
                symbol="TOK",
                quantity=8000,
                price=0.125,
                fees=0,
                amount=-1000,
            ),
            # Everything below happens on one day, so the same day rule applies
            # to all of it before the pool is touched at all.
            sell_transaction(
                date=datetime.date(day=31, month=1, year=2020),
                symbol="TOK",
                quantity=5000,
                price=0.1,
                fees=0,
                amount=500,
            ),
            buy_transaction(
                date=datetime.date(day=31, month=1, year=2020),
                symbol="TOK",
                quantity=4000,
                price=0.08,
                fees=0,
                amount=-320,
            ),
            buy_transaction(
                date=datetime.date(day=31, month=1, year=2020),
                symbol="TOK",
                quantity=1000,
                price=0.075,
                fees=0,
                amount=-75,
            ),
            buy_transaction(
                date=datetime.date(day=31, month=1, year=2020),
                symbol="TOK",
                quantity=1000,
                price=0.07,
                fees=0,
                amount=-70,
            ),
            sell_transaction(
                date=datetime.date(day=31, month=1, year=2020),
                symbol="TOK",
                quantity=2000,
                price=0.071,
                fees=0,
                amount=142,
            ),
            buy_transaction(
                date=datetime.date(day=31, month=1, year=2020),
                symbol="TOK",
                quantity=500,
                price=0.07,
                fees=0,
                amount=-35,
            ),
        ],
        # Consideration £642 less same-day costs £500 less the pool's share of
        # the remaining 500 tokens, £1,000 x 500/8,000 = £62.50.
        # HMRC rounds that to £63 and publishes the gain as £79.
        79.50,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        None,  # Calculation Log
        {},  # Calculation Log Other
        # https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22254
        id="CRYPTO22254_Example_4",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=1, year=2020), 345000),
            # Opening pool: 100,000 tokens for £300,000.
            buy_transaction(
                date=datetime.date(day=1, month=1, year=2020),
                symbol="TOK",
                quantity=100000,
                price=3.0,
                fees=0,
                amount=-300000,
            ),
            buy_transaction(
                date=datetime.date(day=31, month=7, year=2020),
                symbol="TOK",
                quantity=10000,
                price=4.5,
                fees=0,
                amount=-45000,
            ),
            sell_transaction(
                date=datetime.date(day=31, month=7, year=2020),
                symbol="TOK",
                quantity=30000,
                price=5.0,
                fees=0,
                amount=150000,
            ),
            sell_transaction(
                date=datetime.date(day=5, month=8, year=2020),
                symbol="TOK",
                quantity=20000,
                price=5.0,
                fees=0,
                amount=100000,
            ),
            # One acquisition feeding the 30 day rule for two earlier
            # disposals, with what is left over going to the pool.
            buy_transaction(
                date=datetime.date(day=6, month=8, year=2020),
                symbol="TOK",
                quantity=50000,
                price=4.5,
                fees=0,
                amount=-225000,
            ),
            sell_transaction(
                date=datetime.date(day=7, month=8, year=2020),
                symbol="TOK",
                quantity=100000,
                price=1.5,
                fees=0,
                amount=150000,
            ),
        ],
        # 31 July: £150,000 less £45,000 same day less £90,000 under the 30 day
        #          rule = £15,000
        # 5 August: £100,000 less £90,000 under the 30 day rule = £10,000
        # 7 August: £150,000 less the pool's £313,636 = a loss of £163,636
        # HMRC rounds the pool cost to £313,637 and states the loss as £163,637.
        -138636.36,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        None,  # Calculation Log
        {},  # Calculation Log Other
        # https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22256
        id="CRYPTO22256_Example_6",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=1, year=2020), 126000),
            # Opening pool: 100 tokens for £1,000.
            buy_transaction(
                date=datetime.date(day=1, month=1, year=2020),
                symbol="TOK",
                quantity=100,
                price=10.0,
                fees=0,
                amount=-1000,
            ),
            buy_transaction(
                date=datetime.date(day=18, month=9, year=2020),
                symbol="TOK",
                quantity=50,
                price=2500.0,
                fees=0,
                amount=-125000,
            ),
            sell_transaction(
                date=datetime.date(day=1, month=12, year=2020),
                symbol="TOK",
                quantity=50,
                price=6000.0,
                fees=0,
                amount=300000,
            ),
        ],
        # £300,000 less the pool's share, £126,000 x 50/150 = £42,000.
        258000.00,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        None,  # Calculation Log
        {},  # Calculation Log Other
        # https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22251
        id="CRYPTO22251_Example_1",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=1, year=2020), 1500),
            # Opening pool: 5,000 tokens for £500.
            buy_transaction(
                date=datetime.date(day=1, month=1, year=2020),
                symbol="TOK",
                quantity=5000,
                price=0.1,
                fees=0,
                amount=-500,
            ),
            # Two disposals and an acquisition on one day. The disposals are
            # treated as one, and the acquisition is matched against it.
            sell_transaction(
                date=datetime.date(day=23, month=6, year=2020),
                symbol="TOK",
                quantity=1000,
                price=0.8,
                fees=0,
                amount=800,
            ),
            buy_transaction(
                date=datetime.date(day=23, month=6, year=2020),
                symbol="TOK",
                quantity=1600,
                price=0.625,
                fees=0,
                amount=-1000,
            ),
            sell_transaction(
                date=datetime.date(day=23, month=6, year=2020),
                symbol="TOK",
                quantity=500,
                price=1.2,
                fees=0,
                amount=600,
            ),
        ],
        # £1,400 less £1,000 x 1,500/1,600 = £937.50. The 100 tokens left
        # over join the pool at £62.50.
        # HMRC rounds the cost to £938 and publishes the gain as £462.
        462.50,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        None,  # Calculation Log
        {},  # Calculation Log Other
        # https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22252
        id="CRYPTO22252_Example_2",
    ),
    pytest.param(
        2020,  # tax year
        CRYPTO22253_TRANSACTIONS,
        # The 31 March disposal falls in this year: £400 less 700 tokens at
        # £175 and 300 at £60, both acquired inside the next 30 days but in
        # the *following* tax year. The rule reaches across the year end.
        165.00,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        None,  # Calculation Log
        {},  # Calculation Log Other
        # https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22253
        id="CRYPTO22253_Example_3_first_year",
    ),
    pytest.param(
        2021,  # tax year
        CRYPTO22253_TRANSACTIONS,
        # The 20 April disposal falls in the next year: £150 less 200 tokens
        # at £40 and 300 at £90.
        20.00,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        None,  # Calculation Log
        {},  # Calculation Log Other
        # https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22253
        id="CRYPTO22253_Example_3_second_year",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(datetime.date(day=1, month=1, year=2020), 200000),
            # Opening pool: 14,000 tokens for £200,000.
            buy_transaction(
                date=datetime.date(day=1, month=1, year=2020),
                symbol="TOK",
                quantity=14000,
                price=14.285714,
                fees=0,
                amount=-200000,
            ),
            sell_transaction(
                date=datetime.date(day=30, month=8, year=2020),
                symbol="TOK",
                quantity=4000,
                price=40.0,
                fees=0,
                amount=160000,
            ),
            # Bought inside 30 days, so 500 of the 4,000 sold match here rather
            # than against the pool.
            buy_transaction(
                date=datetime.date(day=11, month=9, year=2020),
                symbol="TOK",
                quantity=500,
                price=35.0,
                fees=0,
                amount=-17500,
            ),
        ],
        # £160,000 less £17,500 for the 500 matched under the 30 day rule,
        # less £50,000 for the 3,500 taken from the pool.
        92500.00,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        None,  # Calculation Log
        {},  # Calculation Log Other
        # https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22255
        id="CRYPTO22255_Example_5",
    ),
]
