"""Test Schwab bond pricing with CUSIP symbols.

Tests the handling of CUSIP bond symbols where prices are quoted per $100 face value
and need to be divided by 100 for calculation purposes.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from cgt_calc.parsers.schwab import SchwabParser
from cgt_calc.parsers.schwab_cusip_bonds import adjust_cusip_bond_price


class TestBondPricing:
    """Test bond pricing with CUSIP symbols."""

    def test_bond_buy_price_divided_by_100(self, tmp_path: Path) -> None:
        """Test that bond buy price is divided by 100 for CUSIP symbols."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/01/2024,Buy,91282CMF5,US TREASURY NOTE,$9917.27,40000,$0.00,-$3966908.00\n"
        )

        transactions = SchwabParser().load_from_file(csv_file)

        # Price should be divided by 100
        assert len(transactions) == 1
        assert transactions[0].price == Decimal("99.1727")

    def test_bond_sell_price_divided_by_100(self, tmp_path: Path) -> None:
        """Test that bond sell price is divided by 100 for CUSIP symbols."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/01/2024,Sell,91282CKS9,US TREASURY NOTE,$10076.95,50000,$50.00,$5038425.00\n"
        )

        transactions = SchwabParser().load_from_file(csv_file)

        # Price should be divided by 100
        assert len(transactions) == 1
        assert transactions[0].price == Decimal("100.7695")

    def test_regular_stock_price_not_divided(self, tmp_path: Path) -> None:
        """Test that regular stock price is not divided by 100."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/01/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        )

        transactions = SchwabParser().load_from_file(csv_file)

        # Price should remain as-is
        assert len(transactions) == 1
        assert transactions[0].price == Decimal("150.00")

    @pytest.mark.parametrize(
        ("symbol", "expected_is_cusip"),
        [
            ("91282CMF5", True),  # Valid CUSIP (US Treasury Note)
            ("912797QL4", True),  # Valid CUSIP (US Treasury Bill)
            ("91282CKE0", True),  # Valid CUSIP (US Treasury Note)
            ("AAPL", False),  # Too short (4 chars)
            ("12345678", False),  # Too short (8 chars)
            ("1234567890", False),  # Too long (10 chars)
            ("123456789", False),  # Invalid check digit
            ("12345678A", False),  # Invalid check digit
            (None, False),  # None
        ],
    )
    def test_cusip_detection(self, symbol: str | None, expected_is_cusip: bool) -> None:
        """Test CUSIP symbol detection edge cases."""
        # If it's a CUSIP, the price should be divided by 100
        # If not, price should remain unchanged
        price = Decimal("10000.00")
        quantity = Decimal(100)
        fees = Decimal(0)

        # Every case states the amount a bond at this price would have, so the
        # amount check accepts the division and only detection decides it.
        amount = -(quantity * (price / 100))  # Buy transaction

        adjusted_price, adjusted_fees = adjust_cusip_bond_price(
            symbol, price, quantity, amount, fees
        )

        if expected_is_cusip:
            assert adjusted_price == price / 100
        else:
            assert adjusted_price == price
        assert adjusted_fees == fees

    @pytest.mark.parametrize(
        ("price", "quantity", "amount", "fees", "should_adjust"),
        [
            # Valid bond buy with no accrued interest
            (
                Decimal("9917.27"),
                Decimal(40000),
                Decimal("-3966908.00"),
                Decimal(0),
                True,
            ),
            # Valid bond sell with fees and no accrued interest
            (
                Decimal("10076.95"),
                Decimal(50000),
                Decimal("5038425.00"),
                Decimal("50.00"),
                True,
            ),
            # Invalid: amount too far off (should not apply adjustment)
            (
                Decimal("10000.00"),
                Decimal(10000),
                Decimal("-50000000.00"),
                Decimal(0),
                False,
            ),
            # Fractional quantity edge case
            (
                Decimal("10000.00"),
                Decimal("100.5"),
                Decimal("-10050.00"),
                Decimal(0),
                True,
            ),
            # Zero fees
            (
                Decimal("10000.00"),
                Decimal(100),
                Decimal("-10000.00"),
                Decimal(0),
                True,
            ),
            # With fees
            (
                Decimal("10000.00"),
                Decimal(100),
                Decimal("-10050.00"),
                Decimal("50.00"),
                True,
            ),
        ],
    )
    def test_bond_price_adjustment_validation(
        self,
        price: Decimal,
        quantity: Decimal,
        amount: Decimal,
        fees: Decimal,
        should_adjust: bool,
    ) -> None:
        """Test bond price validation logic with various edge cases."""
        symbol = "91282CMF5"  # Valid CUSIP

        adjusted_price, adjusted_fees = adjust_cusip_bond_price(
            symbol, price, quantity, amount, fees
        )

        if should_adjust:
            # Should apply /100 adjustment
            assert adjusted_price == price / 100
        else:
            # Should NOT apply adjustment (validation failed)
            assert adjusted_price == price
            assert adjusted_fees == fees

    def test_bond_accrued_interest_added_to_fees(self) -> None:
        """Test that accrued interest is correctly added to fees for bonds."""
        symbol = "91282CMF5"
        price = Decimal("9917.27")
        quantity = Decimal(40000)
        fees = Decimal(0)

        # Amount includes $500 accrued interest
        # Expected: 40000 * (9917.27/100) = 3,966,908
        # Actual: 3,966,908 + 500 = 3,967,408
        amount = Decimal("-3967408.00")

        adjusted_price, adjusted_fees = adjust_cusip_bond_price(
            symbol, price, quantity, amount, fees
        )

        # Price should be divided by 100
        assert adjusted_price == Decimal("99.1727")

        # Accrued interest should be added to fees
        # accrued_interest = |amount| - |expected_amount| - |fees|
        # accrued_interest = 3967408 - 3966908 - 0 = 500
        assert adjusted_fees == Decimal("500.00")

    def test_bond_no_accrued_interest_fees_unchanged(self) -> None:
        """Test that fees stay zero when the amount implies no accrued interest."""
        symbol = "91282CMF5"
        price = Decimal("10000.00")
        quantity = Decimal(100)
        fees = Decimal(0)

        amount = Decimal("-10000.00")  # Exactly quantity * (price/100), no accrued

        adjusted_price, adjusted_fees = adjust_cusip_bond_price(
            symbol, price, quantity, amount, fees
        )

        # Price adjusted but fees unchanged
        assert adjusted_price == Decimal("100.00")
        assert adjusted_fees == Decimal(0)
