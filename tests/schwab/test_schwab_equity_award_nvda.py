"""NVIDIA-specific parsing of Schwab Equity Award JSON exports.

Schwab reports NVDA history in mixed units. Acquisitions are restated for later
splits, disposals are not, and one record type mixes both inside itself. Each
test here pins one of those rules.

The fixture is a synthetic portfolio: real dates and real prices, invented
share counts. Real dates and prices because they are what is under test — the
split boundaries of 20 July 2021 and 10 June 2024, and the tax year end.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction
from cgt_calc.parsers import schwab_equity_award_json
from cgt_calc.parsers.schwab_equity_award_json import (
    JsonRowType,
    SchwabAwardTransaction,
    split_multiplier,
)
from tests.utils import tax_fields

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

FIXTURE = Path("tests/schwab/data/equity_award/nvda_synthetic.json")


@pytest.fixture(name="transactions")
def transactions_fixture() -> list[SchwabAwardTransaction]:
    """Parse the synthetic NVDA portfolio."""
    return schwab_equity_award_json.SchwabEquityAwardsParser().load_from_file(FIXTURE)


def on(
    transactions: Sequence[BrokerTransaction],
    date: datetime.date,
    action: ActionType,
) -> BrokerTransaction:
    """Return the single transaction of that kind on that date."""
    found = [t for t in transactions if t.date == date and t.action == action]
    assert len(found) == 1, f"expected one {action} on {date}, got {len(found)}"
    return found[0]


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        # Before both splits: 4:1 in 2021 then 10:1 in 2024.
        (datetime.date(2021, 2, 26), 40),
        (datetime.date(2021, 7, 19), 40),
        # The new shares trade from the 20th, so that day is already post-split.
        (datetime.date(2021, 7, 20), 10),
        (datetime.date(2024, 6, 7), 10),
        (datetime.date(2024, 6, 10), 1),
        (datetime.date(2026, 4, 2), 1),
    ],
)
def test_split_multiplier_boundaries(day: datetime.date, expected: int) -> None:
    """The multiplier changes on the day the new shares trade."""
    assert split_multiplier("NVDA", day) == expected


def test_lapse_is_not_an_acquisition(transactions: list[BrokerTransaction]) -> None:
    """A Lapse duplicates its Deposit and must not reach the pool.

    Its NetSharesDeposited is in the units of the day while its price is
    restated, so pairing them puts a tenth of the shares in at the full price.
    """
    assert len(transactions) == 12  # 6 acquisitions, 4 disposals, 2 cash

    vests = [
        t
        for t in transactions
        if t.action == ActionType.STOCK_ACTIVITY
        and t.date == datetime.date(2021, 9, 15)
    ]
    assert len(vests) == 1
    # The paired Lapse says 40; the Deposit says 400 and is the one that counts.
    assert vests[0].quantity == Decimal(400)


def test_vest_is_read_in_restated_units(transactions: list[BrokerTransaction]) -> None:
    """Both the count and the price of a vest are already restated."""
    vest = on(transactions, datetime.date(2021, 9, 15), ActionType.STOCK_ACTIVITY)

    assert vest.quantity == Decimal(400)
    assert vest.price == Decimal("22.341")


def test_sell_to_cover_leaves_net_shares(transactions: list[BrokerTransaction]) -> None:
    """1000 vested, 300 went to tax, and the Deposit is already net."""
    vest = on(transactions, datetime.date(2024, 9, 18), ActionType.STOCK_ACTIVITY)

    assert vest.quantity == Decimal(700)


def test_espp_uses_market_value_not_price_paid(
    transactions: list[BrokerTransaction],
) -> None:
    """The discount is taxed through payroll, so the basis is market value.

    Using PurchasePrice of $9.3588 instead of the $13.7145 the shares were
    worth understates the basis by a third on this purchase, and roughly
    tenfold on the ones where the discount is larger.
    """
    espp = on(transactions, datetime.date(2021, 2, 26), ActionType.STOCK_ACTIVITY)

    assert espp.quantity == Decimal(80)
    assert espp.price == Decimal("13.7145")
    assert espp.amount == Decimal(80) * Decimal("13.7145")


def test_espp_taxed_in_shares_pools_the_net(
    transactions: list[BrokerTransaction],
) -> None:
    """Since August 2025 the tax is settled in shares: 200 bought, 150 kept."""
    espp = on(transactions, datetime.date(2026, 2, 27), ActionType.STOCK_ACTIVITY)

    assert espp.quantity == Decimal(150)
    assert espp.price == Decimal("177.19")


def test_espp_before_a_split_pools_the_net_in_current_units(tmp_path: Path) -> None:
    """The counts inside the row are in the units of their own day.

    Its Quantity is restated, so a purchase predating a split only adds up
    through the multiplier — and the shares it pools have to go through it too,
    or they land in a pool that is counting in something else.
    """

    def moved_before_the_splits(rows: list[JsonRowType]) -> None:
        for row in rows:
            if row.get("Description") == "ESPP" and row["Date"] == "02/27/2026":
                row["Date"] = "05/17/2022"
                row["Quantity"] = "2000"  # (150 + 50) restated by 10
                details_of(row)["PurchaseDate"] = "05/17/2022"

    espp = on(
        parse(mutated(tmp_path, moved_before_the_splits)),
        datetime.date(2022, 5, 17),
        ActionType.STOCK_ACTIVITY,
    )

    assert espp.quantity == Decimal(1500)  # 150 restated by 10


def test_disposal_before_the_split_is_converted(
    transactions: list[BrokerTransaction],
) -> None:
    """Disposals are the one record left in the units of their own day.

    Two shares at $191.93 as recorded is twenty at $19.193 in current units,
    and the money is the same either way. Missing this costs 54 shares across
    the two 2023 disposals, and the pool stays wrong by that much for every
    year afterwards.
    """
    sale = on(transactions, datetime.date(2023, 1, 23), ActionType.SELL)
    quantity = sale.quantity
    price = sale.price

    assert quantity is not None
    assert price is not None
    assert quantity == Decimal(20)
    assert price == Decimal("19.193")
    assert quantity * price == Decimal("383.86")


def test_disposal_after_the_split_is_left_alone(
    transactions: list[BrokerTransaction],
) -> None:
    """A disposal already in current units needs no conversion."""
    sale = on(transactions, datetime.date(2026, 4, 2), ActionType.SELL)

    assert sale.quantity == Decimal(120)
    assert sale.price == Decimal("176.725")


def test_disposal_price_comes_from_the_money(
    transactions: list[BrokerTransaction],
) -> None:
    """Amount is net of fees, so the gross is rebuilt before dividing.

    Deriving the price this way rather than from the per-lot SalePrice is what
    lets a disposal span lots sold at different prices.
    """
    sale = on(transactions, datetime.date(2025, 5, 22), ActionType.SELL)

    assert sale.fees == Decimal("0.10")
    assert sale.price == Decimal("132.83")
    assert sale.quantity == Decimal(500)


def test_disposal_fees_are_preserved(
    transactions: list[BrokerTransaction],
) -> None:
    """A populated Schwab fee like "$0.02" is parsed and kept on the disposal."""
    sale = on(transactions, datetime.date(2023, 1, 23), ActionType.SELL)

    assert sale.fees == Decimal("0.02")


def test_withholding_on_dividends_is_kept(
    transactions: list[BrokerTransaction],
) -> None:
    """US tax at source, needed for SA106 and foreign tax credit relief.

    Skipping it alongside Lapse would lose the figure entirely.
    """
    withheld = [t for t in transactions if t.action == ActionType.DIVIDEND_TAX]

    assert len(withheld) == 1
    assert withheld[0].amount == Decimal("-2.22")


def test_the_position_matches_the_transactions(
    transactions: list[BrokerTransaction],
) -> None:
    """Acquisitions less disposals, in current units throughout."""
    acquired = sum(
        t.quantity or Decimal(0)
        for t in transactions
        if t.action == ActionType.STOCK_ACTIVITY
    )
    disposed = sum(
        t.quantity or Decimal(0) for t in transactions if t.action == ActionType.SELL
    )

    assert acquired == Decimal(1730)
    assert disposed == Decimal(690)
    assert acquired - disposed == Decimal(1040)


def mutated(tmp_path: Path, change: Callable[[list[JsonRowType]], None]) -> Path:
    """Write a copy of the fixture with `change` applied to its transactions."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    change(data["Transactions"])
    path = tmp_path / "mutated.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def parse(path: Path) -> list[SchwabAwardTransaction]:
    """Parse an export, whatever it contains."""
    return schwab_equity_award_json.SchwabEquityAwardsParser().load_from_file(path)


def details_of(row: JsonRowType) -> JsonRowType:
    """Return a transaction's detail block, whichever nesting it uses."""
    first: JsonRowType = row["TransactionDetails"][0]
    return first.get("Details", first)


def test_a_sale_rounded_in_the_row_takes_its_count_from_the_lots(
    tmp_path: Path,
) -> None:
    """The row rounds the quantity away; the lots still hold the fraction.

    Deriving the price from the money hides it — the proceeds come out right
    whichever count is used — so the pool would quietly lose the fraction and
    never say so.
    """

    def fractional_lots(rows: list[JsonRowType]) -> None:
        for row in rows:
            if row.get("Action") == "Sale" and row["Date"] == "05/22/2025":
                row["TransactionDetails"][0]["Details"]["Shares"] = "200.5"

    sale = on(
        parse(mutated(tmp_path, fractional_lots)),
        datetime.date(2025, 5, 22),
        ActionType.SELL,
    )

    assert sale.quantity == Decimal("500.5")
    assert sale.amount == Decimal("66414.90")


def test_a_vest_with_no_quantity_is_left_for_the_calculator_to_refuse(
    tmp_path: Path,
) -> None:
    """Only a purchase that pooled nothing may be dropped, not any zero.

    A blank Quantity reads as zero like any other missing number. Dropping it
    would turn a malformed row into a smaller holding and a confident wrong
    answer, where refusing it says so.
    """

    def blank_the_vest(rows: list[JsonRowType]) -> None:
        for row in rows:
            if row.get("Description") == "RS" and row["Date"] == "09/18/2024":
                row["Quantity"] = ""
                row.pop("QuantitySortValue", None)

    vest = on(
        parse(mutated(tmp_path, blank_the_vest)),
        datetime.date(2024, 9, 18),
        ActionType.STOCK_ACTIVITY,
    )

    assert vest.quantity == Decimal(0)


def test_a_sale_hiding_its_fraction_in_a_zero_lot_is_recovered(
    tmp_path: Path,
) -> None:
    """Both share-count fields can round the same fraction away.

    A lot stating zero shares leaves the row's count and the lot sum agreeing
    on a whole number while the money says otherwise. Schwab writes this shape:
    the v1 fixture's 2022-06-14 sale reads 3 in both and 3.13007 in cash. The
    price is the only witness, and only when every lot sold at one.
    """

    def fraction_hidden_in_a_zero_lot(rows: list[JsonRowType]) -> None:
        for row in rows:
            if row.get("Action") == "Sale" and row["Date"] == "04/02/2026":
                row["Amount"] = "$12,050.00"
                row["FeesAndCommissions"] = None
                lot = details_of(row)
                lot["SalePrice"] = "$100.00"
                row["TransactionDetails"].append({"Details": dict(lot, Shares="0")})

    sale = on(
        parse(mutated(tmp_path, fraction_hidden_in_a_zero_lot)),
        datetime.date(2026, 4, 2),
        ActionType.SELL,
    )

    assert sale.quantity == Decimal("120.5")
    assert sale.price == Decimal(100)


def test_a_sale_the_row_already_states_exactly_is_left_alone(
    tmp_path: Path,
) -> None:
    """The price is rounded for display, so the money is not the better source.

    Recomputing from it unconditionally turns an exact 50 into 49.99997 and the
    pool starts drifting on transactions that were never wrong.
    """

    def nothing(rows: list[JsonRowType]) -> None:
        pass

    sale = on(
        parse(mutated(tmp_path, nothing)),
        datetime.date(2025, 5, 19),
        ActionType.SELL,
    )

    assert sale.quantity == Decimal(50)


def test_a_disposal_schwab_already_restated_is_flagged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The failure this check exists for, and why it only warns.

    Converting a disposal Schwab had already converted leaves the proceeds
    right and the share count ten times too high. Nothing fails to balance, so
    without this the run finishes cleanly on a different answer. It warns
    rather than raises because a share that fell far enough between the sale
    and the nearest vest looks exactly the same.
    """

    def already_restated(rows: list[JsonRowType]) -> None:
        for row in rows:
            if row.get("Action") == "Sale" and row["Date"] == "01/23/2023":
                row["Quantity"] = "20"
                details_of(row)["SalePrice"] = "$19.192"

    with caplog.at_level(logging.WARNING):
        assert parse(mutated(tmp_path, already_restated))
    assert "already restated" in caplog.text


def test_a_disposal_in_its_own_units_is_left_alone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The check has to stay quiet on the units Schwab actually writes.

    A guard that fires on correct data is worse than no guard, because the way
    round it is to switch it off.
    """

    def nothing(rows: list[JsonRowType]) -> None:
        pass

    with caplog.at_level(logging.WARNING):
        assert parse(mutated(tmp_path, nothing))
    assert "already restated" not in caplog.text


@pytest.mark.parametrize("printed", ["NaN", "Infinity"])
def test_a_lot_that_is_not_a_number_names_its_row(tmp_path: Path, printed: str) -> None:
    """A restated disposal has its lots checked like any other sale.

    The helpers that recover its count return None where a field is missing,
    so they refuse nothing that is present and unusable, and Decimal takes
    NaN and Infinity without complaint.
    """

    def unparseable_lot(rows: list[JsonRowType]) -> None:
        for row in rows:
            if row.get("Action") == "Sale" and row["Date"] == "05/22/2025":
                details_of(row)["Shares"] = printed

    with pytest.raises(
        ParsingError, match=rf"Invalid Shares '{printed}' for Sale on 2025-05-22"
    ):
        parse(mutated(tmp_path, unparseable_lot))


def test_a_negative_lot_count_is_refused_on_the_restated_path(
    tmp_path: Path,
) -> None:
    """A negative lot count would take shares out of the pool silently.

    The count is recovered by summing the lots, so a negative one is simply
    added in: the 500-share disposal of 22 May 2025 reads as 99.5 while the
    proceeds stay right, and nothing about the output looks wrong.
    """

    def negative_lot(rows: list[JsonRowType]) -> None:
        for row in rows:
            if row.get("Action") == "Sale" and row["Date"] == "05/22/2025":
                details_of(row)["Shares"] = "-200.5"

    with pytest.raises(
        ParsingError, match=r"cannot dispose of a negative number of shares"
    ):
        parse(mutated(tmp_path, negative_lot))


def test_a_zero_lot_price_is_refused_on_the_restated_path(tmp_path: Path) -> None:
    """The price comes from the money here, but a zero lot price is malformed."""

    def zero_price(rows: list[JsonRowType]) -> None:
        for row in rows:
            if row.get("Action") == "Sale" and row["Date"] == "04/02/2026":
                details_of(row)["SalePrice"] = "$0.00"

    with pytest.raises(ParsingError, match=r"states a SalePrice of '\$0\.00'"):
        parse(mutated(tmp_path, zero_price))


def test_a_lot_price_that_is_not_a_number_names_its_row(tmp_path: Path) -> None:
    """The per-lot price on a restated disposal is read the same way."""

    def unparseable_price(rows: list[JsonRowType]) -> None:
        for row in rows:
            if row.get("Action") == "Sale" and row["Date"] == "05/22/2025":
                details_of(row)["SalePrice"] = "Infinity"

    with pytest.raises(
        ParsingError, match=r"Invalid SalePrice 'Infinity' for Sale on 2025-05-22"
    ):
        parse(mutated(tmp_path, unparseable_price))


def test_an_espp_market_value_that_is_not_a_number_names_its_row(
    tmp_path: Path,
) -> None:
    """An Infinity basis would price the whole purchase and never say so."""

    def unparseable_market_value(rows: list[JsonRowType]) -> None:
        for row in rows:
            if row.get("Description") == "ESPP":
                details_of(row)["PurchaseFairMarketValue"] = "Infinity"

    with pytest.raises(
        ParsingError,
        match=r"Invalid PurchaseFairMarketValue 'Infinity' for Deposit on ",
    ):
        parse(mutated(tmp_path, unparseable_market_value))


def test_a_lapse_count_that_is_not_a_number_names_its_row(tmp_path: Path) -> None:
    """The split-table check reads three fields before any row is built."""

    def unparseable_lapse(rows: list[JsonRowType]) -> None:
        for row in rows:
            if row.get("Action") == "Lapse":
                details_of(row)["NetSharesDeposited"] = "NaN"

    with pytest.raises(
        ParsingError, match=r"Invalid NetSharesDeposited 'NaN' for Lapse on "
    ):
        parse(mutated(tmp_path, unparseable_lapse))


def test_a_split_missing_from_the_table_is_caught(tmp_path: Path) -> None:
    """A Lapse states both units at once, so it can contradict the table.

    Here the counts are consistent with a split the table does not have. The
    point is that this is caught from the file alone, with no market data and
    no broker statement.
    """

    def as_if_another_split(rows: list[JsonRowType]) -> None:
        for row in rows:
            if row.get("Action") == "Lapse" and row["Date"] == "09/18/2024":
                row["Quantity"] = "2000"

    with pytest.raises(ParsingError, match="split is missing"):
        parse(mutated(tmp_path, as_if_another_split))


def test_espp_counts_must_account_for_every_share(tmp_path: Path) -> None:
    """A "0" is a count, not a blank.

    Reading it as one silently dropped the whole purchase from the pool: 200
    shares in, nothing out, no error.
    """

    def zero_deposited(rows: list[JsonRowType]) -> None:
        for row in rows:
            if row.get("Description") == "ESPP" and row["Date"] == "02/27/2026":
                details_of(row)["NetSharesDeposited"] = "0"

    with pytest.raises(ParsingError, match="accounts for"):
        parse(mutated(tmp_path, zero_deposited))


def test_espp_with_everything_withheld_pools_nothing(tmp_path: Path) -> None:
    """And when the counts do add up, zero deposited means nothing was acquired.

    The same "0" as above, now corroborated. Distinguishing the two is the
    whole point of asking the counts rather than the field's emptiness.
    """

    def all_withheld(rows: list[JsonRowType]) -> None:
        for row in rows:
            if row.get("Description") == "ESPP" and row["Date"] == "02/27/2026":
                details = details_of(row)
                details["NetSharesDeposited"] = "0"
                details["SharesWithheld"] = "200"

    espp = [
        t
        for t in parse(mutated(tmp_path, all_withheld))
        if t.action == ActionType.STOCK_ACTIVITY
        and t.date == datetime.date(2026, 2, 27)
    ]

    # Not an acquisition of zero shares, which the calculator refuses outright,
    # but no acquisition at all.
    assert espp == []


def test_the_csv_export_of_this_history_reads_the_same() -> None:
    """The CSV layout states the same portfolio, splits and all.

    NVDA is the ticker whose acquisitions Schwab restates and whose disposals
    it does not, so this is where a layout difference would show up as a
    different share count rather than a parse error.
    """
    parser = schwab_equity_award_json.SchwabEquityAwardsParser

    json_rows = parser.load_from_file(FIXTURE)
    csv_rows = parser.load_from_file(FIXTURE.with_suffix(".csv"))

    assert [tax_fields(row) for row in csv_rows] == [
        tax_fields(row) for row in json_rows
    ]
