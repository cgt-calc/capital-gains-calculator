"""Test Schwab Cancel Buy filtering logic.

Tests the filtering of Cancel Buy transactions and their matching with original
Buy transactions within the search window.

Cancel Buy is a Schwab-specific transaction type that indicates a purchase was
cancelled. Both rows have to be filtered out to avoid counting a purchase that
never stood. The cancellation carries ActionType.CANCEL_BUY rather than the
type of a purchase, so a row that escapes the filter is refused downstream
instead of being booked as an acquisition.

Real Schwab exports are ordered newest-first (see
tests/schwab/data/schwab_transactions.csv, where 2021 rows appear before 2020
rows). Since a Cancel Buy is chronologically *after* the Buy it cancels, in a
newest-first CSV the Cancel Buy row appears *above* (before) its matching Buy
row. All CSVs below follow that realistic ordering.
"""

from collections import OrderedDict
import csv
import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers.schwab import AwardPrices, SchwabParser, SchwabTransaction


class TestCancelBuyFiltering:
    """Test Cancel Buy filtering logic."""

    def test_cancel_buy_matches_exact_symbol_quantity_price(
        self, tmp_path: Path
    ) -> None:
        """Test that Cancel Buy matches exact symbol, quantity, and price."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/12/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$151.00,10,$0.00,-$1510.00\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        )

        transactions = SchwabParser().load_from_file(csv_file)

        # Only the matching-price Buy should be removed (exact price match)
        assert len(transactions) == 1
        assert transactions[0].price == Decimal("151.00")

    def test_cancel_buy_with_different_symbol_no_match(self, tmp_path: Path) -> None:
        """Test that Cancel Buy doesn't match different symbol."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/12/2024,Cancel Buy,MSFT,MICROSOFT CORP,$150.00,10,$0.00,$0.00\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        )

        # Different symbols, so nothing matches the cancellation.
        with pytest.raises(ParsingError, match="no Buy to match it"):
            SchwabParser().load_from_file(csv_file)

    def test_cancel_buy_with_fractional_shares(self, tmp_path: Path) -> None:
        """Test that Cancel Buy works with fractional shares."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/12/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10.5,$0.00,$0.00\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10.5,$0.00,-$1575.00\n"
        )

        transactions = SchwabParser().load_from_file(csv_file)

        # Both should be removed (exact fractional quantity match)
        assert len(transactions) == 0

    def test_multiple_cancel_buys_same_symbol(self, tmp_path: Path) -> None:
        """Test multiple Cancel Buy transactions for the same symbol."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/13/2024,Cancel Buy,AAPL,APPLE INC,$151.00,20,$0.00,$0.00\n"
            "01/12/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
            "01/11/2024,Buy,AAPL,APPLE INC,$151.00,20,$0.00,-$3020.00\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        )

        transactions = SchwabParser().load_from_file(csv_file)

        # All should be removed
        assert len(transactions) == 0

    def test_cancel_buy_with_other_transactions(self, tmp_path: Path) -> None:
        """Test Cancel Buy doesn't affect unrelated transactions."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/12/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
            "01/11/2024,Sell,MSFT,MICROSOFT CORP,$205.00,5,$0.00,$1025.00\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
            "01/09/2024,Buy,MSFT,MICROSOFT CORP,$200.00,5,$0.00,-$1000.00\n"
        )

        transactions = SchwabParser().load_from_file(csv_file)

        # Only AAPL Buy and Cancel Buy removed, MSFT transactions remain
        assert len(transactions) == 2
        assert all(txn.symbol == "MSFT" for txn in transactions)

    @pytest.mark.parametrize(
        "days_apart",
        [0, 1, 2, 3, 4, 5],
    )
    def test_cancel_buy_5_day_boundary(self, tmp_path: Path, days_apart: int) -> None:
        """Test that Cancel Buy matches within 5-day boundary."""
        buy_date = datetime.date(2024, 1, 10)
        cancel_date = buy_date + datetime.timedelta(days=days_apart)

        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            f"Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            f"{cancel_date.strftime('%m/%d/%Y')},Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
            f"{buy_date.strftime('%m/%d/%Y')},Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        )

        transactions = SchwabParser().load_from_file(csv_file)

        # Within 5 days - should match and remove both
        assert len(transactions) == 0

    def test_cancel_buy_6_days_no_match(self, tmp_path: Path) -> None:
        """Test that Cancel Buy doesn't match beyond 5-day window."""
        buy_date = datetime.date(2024, 1, 10)
        cancel_date = buy_date + datetime.timedelta(days=6)

        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            f"Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            f"{cancel_date.strftime('%m/%d/%Y')},Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
            f"{buy_date.strftime('%m/%d/%Y')},Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        )

        # 6 days apart, one day outside the window.
        with pytest.raises(ParsingError, match="no Buy to match it"):
            SchwabParser().load_from_file(csv_file)

    def test_no_cancel_buy_transactions(self, tmp_path: Path) -> None:
        """Test that normal transactions are unaffected when no Cancel Buy present."""
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/15/2024,Sell,AAPL,APPLE INC,$155.00,10,$0.00,$1550.00\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        )

        transactions = SchwabParser().load_from_file(csv_file)

        # No Cancel Buy - both transactions remain
        assert len(transactions) == 2
        assert transactions[0].action.name == "BUY"
        assert transactions[1].action.name == "SELL"

    def test_cancel_buy_after_original_buy_in_newest_first_order(
        self, tmp_path: Path
    ) -> None:
        """Regression test: Cancel Buy must match when list is newest-first.

        This reproduces the real-world Schwab export ordering (see
        tests/schwab/data/schwab_transactions.csv), where the Cancel Buy row
        (more recent) appears *before* its original Buy row (older) in the raw
        CSV, with an unrelated, older transaction below both. A backward-only
        search (searching only at indices lower than the Cancel Buy's index)
        can never find the Buy in this ordering, since the Buy is always at a
        higher index than the Cancel Buy for a real, newest-first file.
        """
        csv_file = tmp_path / "transactions.csv"
        csv_file.write_text(
            "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
            "01/12/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$0.00\n"
            "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
            "01/05/2024,Buy,MSFT,MICROSOFT CORP,$200.00,5,$0.00,-$1000.00\n"
        )

        transactions = SchwabParser().load_from_file(csv_file)

        # AAPL Buy and Cancel Buy should be matched and removed; MSFT remains.
        assert len(transactions) == 1
        assert transactions[0].symbol == "MSFT"


def test_unmatched_cancel_buy_is_refused(tmp_path: Path) -> None:
    """An unmatched Cancel Buy stops the run, naming the row at fault.

    The pair cannot be reconciled: either the export does not reach the
    purchase being reversed, or it settled outside the search window. Refusing
    in the parser names the offending row and what to check, which the
    calculator cannot do once the row is just an action it does not handle.
    """
    csv_file = tmp_path / "transactions.csv"
    csv_file.write_text(
        "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
        "01/20/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        "01/01/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        "01/05/2020,Buy,MSFT,MICROSOFT CORP,$200.00,5,$0.00,-$1000.00\n"
    )

    with pytest.raises(ParsingError, match="no Buy to match it") as exc_info:
        SchwabParser().load_from_file(csv_file)

    # The row is what the user has to remove, so the error has to point at it.
    assert "row 2" in str(exc_info.value)


def test_cancel_buy_is_not_typed_as_a_purchase(tmp_path: Path) -> None:
    """A Cancel Buy row does not carry the action type of a purchase.

    Nothing between parsing and filtering reads the action type, and the
    filter drops or refuses every cancellation, so typing one as a purchase
    buys nothing and costs the guarantee: a row reaching the calculator by
    any other route would be booked as real cost basis. Typed as itself, the
    calculator refuses it.
    """
    csv_file = tmp_path / "transactions.csv"
    csv_file.write_text(
        "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
        "01/12/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$1500.00\n"
        "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
    )

    # Reach past the filter to see how the row itself is typed.
    with csv_file.open(encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    header = rows[0]
    cancel = SchwabTransaction.create(
        OrderedDict(zip(header, rows[1], strict=True)), csv_file, AwardPrices({})
    )

    assert cancel.raw_action == "Cancel Buy"
    assert cancel.action is ActionType.CANCEL_BUY


def test_identical_cancellations_pair_with_the_buys(tmp_path: Path) -> None:
    """Two identical cancellations reverse the two purchases, not each other.

    Matching on the action type paired the cancellations with one another,
    because a Cancel Buy was itself typed as a purchase. Both cancellations
    dropped out and both purchases stayed, adding cost basis for shares that
    were never bought. The rows here are deliberately identical in symbol,
    quantity and price, which is what it takes to reproduce that: the
    committed multiple-cancellation test varies both, so the pairs can only
    match the way they are meant to.
    """
    csv_file = tmp_path / "transactions.csv"
    csv_file.write_text(
        "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
        "01/12/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$1500.00\n"
        "01/11/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$1500.00\n"
        "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        "01/09/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
    )

    assert SchwabParser().load_from_file(csv_file) == []


def test_cancel_buy_matches_a_same_date_buy_listed_above_it(tmp_path: Path) -> None:
    """A same-date purchase is found whichever side of the cancellation it sits.

    The export gives no order to rows sharing a date, so the purchase can be
    listed either side of the cancellation that reverses it. Searching only
    older rows refused a file that held the pair all along.
    """
    csv_file = tmp_path / "transactions.csv"
    csv_file.write_text(
        "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
        "01/10/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        "01/10/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$1500.00\n"
    )

    assert SchwabParser().load_from_file(csv_file) == []


def test_cancel_buy_does_not_match_a_later_buy(tmp_path: Path) -> None:
    """A purchase made after the cancellation is a different purchase.

    Only same-date rows above the cancellation are searched. A Buy on a later
    date had not happened when the cancellation was recorded, so consuming it
    would delete a real acquisition.
    """
    csv_file = tmp_path / "transactions.csv"
    csv_file.write_text(
        "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
        "01/12/2024,Buy,AAPL,APPLE INC,$150.00,10,$0.00,-$1500.00\n"
        "01/10/2024,Cancel Buy,AAPL,APPLE INC,$150.00,10,$0.00,$1500.00\n"
    )

    with pytest.raises(ParsingError, match="no Buy to match it"):
        SchwabParser().load_from_file(csv_file)
