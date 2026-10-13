"""Additional test data for calc tests."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from cgt_calc.model import (
    ActionType,
    CalculationEntry,
    CurrencyCode,
    Isin,
    RuleType,
    SpinOff,
)

from .calc_test_data import (
    buy_transaction,
    dividend_tax_transaction,
    dividend_transaction,
    eri_transaction,
    interest_transaction,
    sell_transaction,
    transaction,
    transfer_transaction,
)

calc_basic_data_2 = [
    pytest.param(
        2023,  # tax year
        [
            transfer_transaction(dt.date(day=1, month=5, year=2023), 5000),
            buy_transaction(
                date=dt.date(day=1, month=5, year=2023),
                symbol="FOO",
                quantity=3,
                price=5,
                fees=1,
                amount=-16,
            ),
        ],
        0.0,  # Expected capital gain/loss
        2.0,  # Expected unrealized gains
        None,  # GBP/USD prices
        {"FOO": 6},  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            dt.date(day=1, month=5, year=2023): {
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
            },
        },
        {},  # Calculation Log Other
        id="unrealized_gains_test",
    ),
    pytest.param(
        2023,  # tax year
        [
            transfer_transaction(dt.date(day=1, month=1, year=2023), 52503),
            buy_transaction(
                date=dt.date(day=1, month=1, year=2023),
                symbol="FOO",
                quantity=500,
                price=101.1,
                fees=1,
                amount=-50551,
            ),
            sell_transaction(
                date=dt.date(day=25, month=6, year=2023),
                symbol="FOO",
                quantity=30,
                price=100,
                fees=1,
                amount=2999,
            ),
            buy_transaction(
                date=dt.date(day=30, month=6, year=2023),
                symbol="FOO",
                quantity=50,
                price=99,
                fees=1,
                amount=-4951,
            ),
            sell_transaction(
                date=dt.date(day=30, month=6, year=2023),
                symbol="FOO",
                quantity=100,
                price=100,
                fees=1,
                amount=9999,
            ),
            transaction(
                date=dt.date(day=5, month=7, year=2023),
                action_type=ActionType.SPIN_OFF,
                symbol="BAR",
                quantity=840,  # 1:2 spin_off
                price=10,
                fees=1,
            ),
            buy_transaction(
                date=dt.date(day=10, month=7, year=2023),
                symbol="FOO",
                quantity=50,
                price=99,
                fees=1,
                amount=-4951,
            ),
            sell_transaction(
                date=dt.date(day=30, month=8, year=2023),
                symbol="BAR",
                quantity=800,
                price=10,
                fees=1,
                amount=7999,
            ),
            sell_transaction(
                date=dt.date(day=30, month=8, year=2023),
                symbol="FOO",
                quantity=100,
                price=110,
                fees=1,
                amount=10999,
            ),
        ],
        2338.91,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            dt.date(day=25, month=6, year=2023): {
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
                        bed_and_breakfast_date_index=dt.date(
                            day=10, month=7, year=2023
                        ),
                    ),
                ],
            },
            dt.date(day=30, month=6, year=2023): {
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
                        quantity=Decimal(50),
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
                        bed_and_breakfast_date_index=dt.date(
                            day=10, month=7, year=2023
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
            dt.date(day=5, month=7, year=2023): {
                "buy$BAR": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(840),
                        amount=Decimal("-8939.55"),
                        gain=Decimal(0),
                        allowable_cost=Decimal("8939.55"),
                        fees=Decimal(1),
                        new_quantity=Decimal(840),
                        new_pool_cost=Decimal("8939.55"),
                        spin_off=SpinOff(
                            source="FOO",
                            dest="BAR",
                            date=dt.date(day=5, month=7, year=2023),
                        ),
                    ),
                ],
                "spin-off$FOO": [
                    CalculationEntry(
                        RuleType.SPIN_OFF,
                        quantity=Decimal(420),
                        amount=Decimal("-42462.84"),
                        gain=Decimal(0),
                        allowable_cost=Decimal("33523.29"),
                        fees=Decimal(0),
                        new_quantity=Decimal(420),
                        new_pool_cost=Decimal("33523.29"),
                        spin_off=SpinOff(
                            source="FOO",
                            dest="BAR",
                            date=dt.date(day=5, month=7, year=2023),
                        ),
                    ),
                ],
            },
            dt.date(day=10, month=7, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal("50.0"),
                        amount=Decimal("-5055.1"),
                        gain=Decimal(0),
                        allowable_cost=Decimal(4951),
                        fees=Decimal(1),
                        new_quantity=Decimal(470),
                        new_pool_cost=Decimal("38578.39"),
                    ),
                ],
            },
            dt.date(day=30, month=8, year=2023): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(100),
                        amount=Decimal(10999),
                        gain=Decimal("2790.8319"),
                        allowable_cost=Decimal("8208.1681") + Decimal(1),
                        fees=Decimal(1),
                        new_quantity=Decimal(370),
                        new_pool_cost=Decimal("30370.2219"),
                    ),
                ],
                "sell$BAR": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal(800),
                        amount=Decimal(7999),
                        gain=Decimal("-514.8571"),
                        allowable_cost=Decimal("8513.8571") + Decimal(1),
                        fees=Decimal(1),
                        new_quantity=Decimal(40),
                        new_pool_cost=Decimal("425.6929"),
                    ),
                ],
            },
        },
        {},  # Calculation Log Other
        id="spin_off",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(dt.date(day=1, month=5, year=2020), 5000),
            buy_transaction(
                date=dt.date(day=1, month=5, year=2020),
                symbol="FOO",
                quantity=3,
                price=5,
                fees=1,
                amount=-16,
            ),
            buy_transaction(
                date=dt.date(day=1, month=5, year=2020),
                symbol="BAR",
                quantity=3,
                price=5,
                fees=1,
                amount=-16,
            ),
            dividend_transaction(
                date=dt.date(day=20, month=5, year=2020),
                symbol="FOO",
                amount=8,
            ),
            dividend_transaction(
                date=dt.date(day=20, month=5, year=2020),
                symbol="BAR",
                amount=10000,
            ),
            dividend_tax_transaction(
                date=dt.date(day=20, month=5, year=2020),
                symbol="BAR",
                amount=1500,
            ),
            # This should never appear in the calcs for 2020/2021 tax year
            interest_transaction(
                date=dt.date(day=1, month=4, year=2020),
                amount=2000,
                currency=CurrencyCode("GBP"),
            ),
            interest_transaction(
                date=dt.date(day=10, month=4, year=2020),
                amount=1500,
                currency=CurrencyCode("GBP"),
            ),
            interest_transaction(
                date=dt.date(day=15, month=7, year=2020),
                amount=250,
            ),
            interest_transaction(
                date=dt.date(day=17, month=7, year=2020),
                amount=250,
            ),
            interest_transaction(
                date=dt.date(day=2, month=4, year=2021),
                amount=500,
            ),
            # This should never appear in the calcs for 2020/2021 tax year
            interest_transaction(
                date=dt.date(day=10, month=4, year=2021),
                amount=1000,
            ),
        ],
        0.00,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        1500.00,  # Expected UK interest
        1008.00,  # Expected foreign interest
        10000.0,  # Expected dividend
        6500.0,  # Expected dividend gain
        {
            dt.date(day=1, month=5, year=2020): {
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
                "buy$BAR": [
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
            }
        },
        {
            dt.date(day=10, month=4, year=2020): {
                "interestUK$Testing": [
                    CalculationEntry(
                        RuleType.INTEREST,
                        quantity=Decimal(1),
                        amount=Decimal(1500),
                        allowable_cost=Decimal(0),
                        new_pool_cost=Decimal(0),
                        fees=Decimal(0),
                        new_quantity=Decimal(1),
                    ),
                ],
            },
            dt.date(day=20, month=5, year=2020): {
                "dividend$FOO": [
                    CalculationEntry(
                        RuleType.DIVIDEND,
                        quantity=Decimal(1),
                        amount=Decimal(8),
                        allowable_cost=Decimal(0),
                        new_pool_cost=Decimal(0),
                        fees=Decimal(0),
                        new_quantity=Decimal(1),
                    ),
                ],
                "dividend$BAR": [
                    CalculationEntry(
                        RuleType.DIVIDEND,
                        quantity=Decimal(1),
                        amount=Decimal(10000),
                        allowable_cost=Decimal(0),
                        new_pool_cost=Decimal(0),
                        fees=Decimal(0),
                        new_quantity=Decimal(1),
                    ),
                ],
            },
            dt.date(day=17, month=7, year=2020): {
                "interestUSD$Testing": [
                    CalculationEntry(
                        RuleType.INTEREST,
                        quantity=Decimal(1),
                        amount=Decimal(500),
                        allowable_cost=Decimal(0),
                        new_pool_cost=Decimal(0),
                        fees=Decimal(0),
                        new_quantity=Decimal(1),
                    ),
                ],
            },
            dt.date(day=2, month=4, year=2021): {
                "interestUSD$Testing": [
                    CalculationEntry(
                        RuleType.INTEREST,
                        quantity=Decimal(1),
                        amount=Decimal(500),
                        allowable_cost=Decimal(0),
                        new_pool_cost=Decimal(0),
                        fees=Decimal(0),
                        new_quantity=Decimal(1),
                    ),
                ],
            },
        },
        id="dividends_and_interests",
    ),
    pytest.param(
        2023,  # tax year
        [
            transfer_transaction(dt.date(day=1, month=5, year=2023), 100000),
            buy_transaction(
                date=dt.date(day=1, month=6, year=2023),
                symbol="FOO",
                quantity=500,
                price=101.1,
                fees=1,
                amount=-50551,
                isin=Isin("USFOO0000006"),
            ),
            buy_transaction(
                date=dt.date(day=2, month=6, year=2023),
                symbol="FOO",
                quantity=100,
                price=102.1,
                fees=1.0,
                amount=-10211.0,
                isin=Isin("USFOO0000006"),
            ),
            eri_transaction(
                date=dt.date(day=1, month=7, year=2023),
                isin=Isin("USFOO0000006"),
                price=2.1,
            ),
            sell_transaction(
                date=dt.date(day=25, month=9, year=2023),
                symbol="FOO",
                quantity=30,
                price=130,
                fees=1,
                amount=3899,
                isin=Isin("USFOO0000006"),
            ),
        ],
        797.90,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        1260.00,  # Expected foreign interest
        0.00,  # Expected dividend
        0.00,  # Expected dividend gain
        {
            dt.date(day=1, month=6, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("500.0"),
                        amount=Decimal("-50551.0"),
                        gain=Decimal(0),
                        allowable_cost=Decimal("50551.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(500),
                        new_pool_cost=Decimal("50551.0"),
                    ),
                ],
            },
            dt.date(day=2, month=6, year=2023): {
                "buy$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("100.0"),
                        amount=Decimal("-10211.0"),
                        gain=Decimal(0),
                        allowable_cost=Decimal("10211.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(600),
                        new_pool_cost=Decimal("60762.0"),
                    ),
                ],
            },
            dt.date(day=1, month=7, year=2023): {
                "excess-reported-income$FOO": [
                    CalculationEntry(
                        RuleType.EXCESS_REPORTED_INCOME,
                        quantity=Decimal("600.0"),
                        amount=Decimal("-60762.0"),
                        gain=None,
                        allowable_cost=Decimal("1260.0"),
                        fees=Decimal("0.0"),
                        new_quantity=Decimal(600),
                        new_pool_cost=Decimal("62022.0"),
                    ),
                ],
            },
            dt.date(day=25, month=9, year=2023): {
                "sell$FOO": [
                    CalculationEntry(
                        RuleType.SECTION_104,
                        quantity=Decimal("30.0"),
                        amount=Decimal("3899.0"),
                        gain=Decimal("797.90"),
                        allowable_cost=Decimal("3101.10") + Decimal("1.0"),
                        fees=Decimal("1.0"),
                        new_quantity=Decimal(570),
                        new_pool_cost=Decimal("58920.90"),
                    ),
                ],
            },
        },
        {
            dt.date(day=1, month=1, year=2024): {
                "excess-reported-income-distribution$FOO": [
                    CalculationEntry(
                        RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
                        quantity=Decimal("600.0"),
                        amount=Decimal("1260.0"),
                        gain=None,
                        allowable_cost=None,
                        fees=Decimal(0),
                        new_quantity=Decimal("600.0"),
                        new_pool_cost=Decimal("1260.0"),
                    ),
                ],
            },
        },
        # https://www.gov.uk/government/publications/offshore-funds-self-assessment-helpsheet-hs265/hs265-offshore-funds
        id="HS265_excess_income_reported",
    ),
    pytest.param(
        2020,  # tax year
        [
            transfer_transaction(dt.date(day=1, month=1, year=2019), 15100),
            buy_transaction(
                date=dt.date(day=1, month=1, year=2019),
                symbol="MSP",
                quantity=9500,
                price=1.5,
                fees=0,
                amount=-14250,
                isin=Isin("USMSP0000000"),
            ),
            sell_transaction(
                date=dt.date(day=30, month=8, year=2020),
                symbol="MSP",
                quantity=4000,
                price=1.5,
                fees=0,
                amount=6000,
                isin=Isin("USMSP0000000"),
            ),
            eri_transaction(
                date=dt.date(day=1, month=9, year=2020),
                isin=Isin("USMSP0000000"),
                price=0.4,
            ),
            eri_transaction(
                date=dt.date(day=10, month=9, year=2020),
                isin=Isin("USMSP0000000"),
                price=0.2,
            ),
            buy_transaction(
                date=dt.date(day=11, month=9, year=2020),
                symbol="MSP",
                quantity=500,
                price=1.7,
                fees=0,
                amount=-850,
                isin=Isin("USMSP0000000"),
            ),
        ],
        -100.0,  # Expected capital gain/loss
        None,  # Expected unrealized gains
        None,  # GBP/USD prices
        None,  # Current prices
        0.00,  # Expected UK interest
        0.00,  # Expected foreign interest
        3600.00,  # Expected dividend
        1600.00,  # Expected dividend gain
        {
            dt.date(day=30, month=8, year=2020): {
                "sell$MSP": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(500),
                        amount=Decimal(750),
                        gain=Decimal("-100.0"),
                        allowable_cost=Decimal(850),
                        fees=Decimal(0),
                        new_quantity=Decimal(9000),
                        new_pool_cost=Decimal(13500),
                        bed_and_breakfast_date_index=(
                            dt.date(day=11, month=9, year=2020)
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
            dt.date(day=1, month=9, year=2020): {
                "excess-reported-income$MSP": [
                    CalculationEntry(
                        RuleType.EXCESS_REPORTED_INCOME,
                        quantity=Decimal("5500.0"),
                        amount=Decimal("-8250.0"),
                        gain=None,
                        allowable_cost=Decimal(2200),
                        fees=Decimal("0.0"),
                        new_quantity=Decimal("5500.0"),
                        new_pool_cost=Decimal("10450.0"),
                    ),
                ],
            },
            dt.date(day=10, month=9, year=2020): {
                "excess-reported-income$MSP": [
                    CalculationEntry(
                        RuleType.EXCESS_REPORTED_INCOME,
                        quantity=Decimal("5500.0"),
                        amount=Decimal("-10450.0"),
                        gain=None,
                        allowable_cost=Decimal(1100),
                        fees=Decimal("0.0"),
                        new_quantity=Decimal("5500.0"),
                        new_pool_cost=Decimal("11550.0"),
                    ),
                ],
            },
            dt.date(day=11, month=9, year=2020): {
                "buy$MSP": [
                    CalculationEntry(
                        RuleType.BED_AND_BREAKFAST,
                        quantity=Decimal(500),
                        amount=Decimal(-1050),
                        allowable_cost=Decimal(850),
                        fees=Decimal(0),
                        new_quantity=Decimal(6000),
                        new_pool_cost=Decimal(12600),
                    ),
                ],
            },
        },
        {
            dt.date(day=1, month=3, year=2021): {
                "excess-reported-income-distribution$MSP": [
                    CalculationEntry(
                        RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
                        quantity=Decimal("6000.0"),
                        amount=Decimal("2400.0"),
                        gain=None,
                        allowable_cost=None,
                        fees=Decimal(0),
                        new_quantity=Decimal("6000.0"),
                        new_pool_cost=Decimal("2400.0"),
                    ),
                ],
            },
            dt.date(day=10, month=3, year=2021): {
                "excess-reported-income-distribution$MSP": [
                    CalculationEntry(
                        RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
                        quantity=Decimal("6000.0"),
                        amount=Decimal("1200.0"),
                        gain=None,
                        allowable_cost=None,
                        fees=Decimal(0),
                        new_quantity=Decimal("6000.0"),
                        new_pool_cost=Decimal("1200.0"),
                    ),
                ],
            },
        },
        # https://www.gov.uk/government/publications/offshore-funds-self-assessment-helpsheet-hs265/hs265-offshore-funds
        id="HS265_excess_income_reported_bnb",
    ),
]
