"""Trading 212 exports a share reorganisation as two rows.

`Stock split close` states the complete position before the event and
`Stock split open` the complete position after it, in either order and
sometimes in different files. Neither row on its own says what happened, and
neither is a purchase or a sale: the totals either side are the same money,
which is what makes the pair checkable.
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
import re
import subprocess
from typing import TYPE_CHECKING, TypedDict, Unpack

import pytest

from cgt_calc.exceptions import CalculationError, ParsingError
from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode
from cgt_calc.parsers.freetrade import FreetradeColumn
from cgt_calc.parsers.trading212 import (
    SPLIT_CLOSE_ACTION,
    SPLIT_OPEN_ACTION,
    Trading212Column,
    Trading212Parser,
    Trading212Transaction,
)
from cgt_calc.stock_splits import StockSplitTransaction, UnresolvedRatio
from tests.freetrade.test_freetrade import NEW_COLUMNS, _default_new_row
from tests.general.test_calc import create_calculator
from tests.utils import (
    assert_stdout_matches,
    build_cmd,
    report_path,
    run_cli,
    stderr_alerts,
)

from .test_trading212 import (
    HEADER_2026,
    HEADER_2026_UTC,
    _make_conversion_row,
    _make_row,
    _write_csv,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

# A one-for-nine consolidation of a whole share, priced so that the two rows
# state the same money. The counts are as Trading 212 prints them: ten
# decimal places, but granular to 1e-8, so their quotient is not 1/9.
CONSOLIDATION_BEFORE = "1.0000000000"
CONSOLIDATION_AFTER = "0.1111111100"
PRICE_BEFORE = "90.00"
PRICE_AFTER = "810.00"
TOTAL = "90.00"

CLOSED_AT = "2026-02-02 07:44:13"
OPENED_AT = "2026-02-02 07:44:13"
EVENT_DAY = date(2026, 2, 2)


class HalfOptions(TypedDict, total=False):
    """What a caller may change about one reorganisation row."""

    time: str
    shares: str
    price: str
    total: str
    ticker: str
    isin: str
    price_currency: str
    rate: str
    currency: str
    transaction_id: str
    time_column: Trading212Column
    extra: Mapping[str | Trading212Column, str]


def split_row(
    header: list[str],
    action: str,
    *,
    time: str = CLOSED_AT,
    shares: str = CONSOLIDATION_BEFORE,
    price: str = PRICE_BEFORE,
    total: str = TOTAL,
    ticker: str = "FOO",
    isin: str = "US0000000002",
    price_currency: str = "GBP",
    rate: str = "",
    currency: str = "GBP",
    transaction_id: str | None = None,
    time_column: Trading212Column = Trading212Column.TIME,
    extra: Mapping[str | Trading212Column, str] | None = None,
) -> list[str]:
    """Build one half of a reorganisation as the export writes it."""
    overrides: dict[str | Trading212Column, str] = {
        Trading212Column.ACTION: action,
        time_column: time,
        Trading212Column.ISIN: isin,
        Trading212Column.TICKER: ticker,
        Trading212Column.NAME: "Foo Inc",
        Trading212Column.NO_OF_SHARES: shares,
        Trading212Column.PRICE_PER_SHARE: price,
        Trading212Column.CURRENCY_PRICE_PER_SHARE: price_currency,
        Trading212Column.EXCHANGE_RATE: rate,
        Trading212Column.TOTAL: total,
        Trading212Column.CURRENCY_TOTAL: currency,
        Trading212Column.TRANSACTION_ID: (
            transaction_id if transaction_id is not None else f"{action}-1"
        ),
    }
    overrides.update(extra or {})
    return _make_row(header, overrides)


def close_row(
    header: list[str] = HEADER_2026, **options: Unpack[HalfOptions]
) -> list[str]:
    """Build the row stating the position before the event."""
    options.setdefault("time", CLOSED_AT)
    options.setdefault("shares", CONSOLIDATION_BEFORE)
    options.setdefault("price", PRICE_BEFORE)
    return split_row(header, SPLIT_CLOSE_ACTION, **options)


def open_row(
    header: list[str] = HEADER_2026, **options: Unpack[HalfOptions]
) -> list[str]:
    """Build the row stating the position after the event."""
    options.setdefault("time", OPENED_AT)
    options.setdefault("shares", CONSOLIDATION_AFTER)
    options.setdefault("price", PRICE_AFTER)
    return split_row(header, SPLIT_OPEN_ACTION, **options)


def trade_row(
    header: list[str] = HEADER_2026,
    *,
    action: str = "Market buy",
    time: str = "2026-02-02 06:00:00",
    shares: str = "1.0000000000",
    price: str = "90.00",
    total: str = "90.00",
    ticker: str = "FOO",
    transaction_id: str = "trade-1",
    extra: Mapping[str | Trading212Column, str] | None = None,
) -> list[str]:
    """Build an ordinary trade in the same export."""
    return _make_row(
        header,
        {
            **(extra or {}),
            Trading212Column.ACTION: action,
            Trading212Column.TIME: time,
            Trading212Column.ISIN: "US0000000002",
            Trading212Column.TICKER: ticker,
            Trading212Column.NAME: "Foo Inc",
            Trading212Column.NO_OF_SHARES: shares,
            Trading212Column.PRICE_PER_SHARE: price,
            Trading212Column.CURRENCY_PRICE_PER_SHARE: "GBP",
            Trading212Column.TOTAL: total,
            Trading212Column.CURRENCY_TOTAL: "GBP",
            Trading212Column.TRANSACTION_ID: transaction_id,
        },
    )


def legacy_split_row(
    header: list[str] = HEADER_2026,
    *,
    time: str,
    ticker: str = "FOO",
    transaction_id: str = "legacy-split-1",
    extra: Mapping[str | Trading212Column, str] | None = None,
) -> list[str]:
    """Build Trading 212's older one-row reorganisation representation."""
    overrides: dict[str | Trading212Column, str] = {
        Trading212Column.ACTION: "Stock Split",
        Trading212Column.TIME: time,
        Trading212Column.TICKER: ticker,
        Trading212Column.NAME: "Foo Inc",
        Trading212Column.NO_OF_SHARES: "-0.8888888900",
        Trading212Column.CURRENCY_TOTAL: "GBP",
        Trading212Column.TRANSACTION_ID: transaction_id,
    }
    overrides.update(extra or {})
    return _make_row(
        header,
        overrides,
    )


def load(
    tmp_path: Path,
    files: Mapping[str, list[list[str]]],
) -> list[BrokerTransaction]:
    """Load one directory of exports, which is one declared account."""
    folder = tmp_path / "inputs"
    folder.mkdir(exist_ok=True)
    for name, rows in files.items():
        _write_csv(folder / name, rows)
    return Trading212Parser().load_from_dir(folder)


def load_pair(
    tmp_path: Path,
    rows: list[list[str]],
    header: list[str] = HEADER_2026,
) -> StockSplitTransaction:
    """Load one export and return the single reorganisation it states."""
    (event,) = load(tmp_path, {"export.csv": [header, *rows]})
    assert isinstance(event, StockSplitTransaction)
    return event


# ===== the pair, in either order =====


@pytest.mark.parametrize("order", ["close first", "open first"])
def test_the_two_rows_combine_whichever_order_they_are_written_in(
    tmp_path: Path, order: str
) -> None:
    """Trading 212 writes them both ways, and the result is the same event."""
    rows = [close_row(), open_row()]
    if order == "open first":
        rows.reverse()

    event = load_pair(tmp_path, rows)

    assert event.action is ActionType.STOCK_SPLIT
    assert event.symbol == "FOO"
    assert event.date == EVENT_DAY
    assert event.event.before_quantity == Decimal(CONSOLIDATION_BEFORE)
    assert event.event.after_quantity == Decimal(CONSOLIDATION_AFTER)
    assert event.event.ratio == Fraction(1, 9)
    # Nothing was bought, sold or paid for.
    assert event.amount == Decimal(0)
    assert event.fees == Decimal(0)


def test_a_currency_conversion_in_the_same_export_is_read(tmp_path: Path) -> None:
    """The cash of a conversion is added once the two halves are paired.

    Pairing sorts by a time only a row read from the export has, which the two
    cash rows are not.
    """
    conversion = _make_conversion_row(
        HEADER_2026, "-0.10", overrides={Trading212Column.CURRENCY_TOTAL: "EUR"}
    )

    transactions = load(
        tmp_path, {"a.csv": [HEADER_2026, close_row(), open_row(), conversion]}
    )

    # The conversion of April 2025 comes before the reorganisation of February 2026.
    assert [transaction.action for transaction in transactions] == [
        ActionType.ADJUSTMENT,
        ActionType.ADJUSTMENT,
        ActionType.ADJUSTMENT,
        ActionType.STOCK_SPLIT,
    ]


def test_a_close_row_can_be_the_first_row_in_the_file(tmp_path: Path) -> None:
    """Pairing does not lean on the row before it.

    Reading the close row's partner off the previous transaction breaks on
    the first row of a file, and quietly picks the wrong row everywhere else.
    """
    transactions = load(
        tmp_path,
        {"export.csv": [HEADER_2026, close_row(), trade_row(), open_row()]},
    )
    (event,) = (t for t in transactions if isinstance(t, StockSplitTransaction))
    assert event.event.ratio == Fraction(1, 9)


def test_a_forward_split_multiplies_the_count(tmp_path: Path) -> None:
    """A ratio above one is the same shape, read the other way."""
    event = load_pair(
        tmp_path,
        [
            close_row(shares="0.0638781600", price="1000.00", total="63.88"),
            open_row(shares="0.6387816000", price="100.00", total="63.88"),
        ],
    )
    assert event.event.ratio == Fraction(10)
    assert event.quantity == Decimal("0.5749034400")


def test_a_ratio_that_is_not_a_whole_number_is_recovered(tmp_path: Path) -> None:
    """Unilever's nine-for-eight consolidation, as exported."""
    event = load_pair(
        tmp_path,
        [
            close_row(shares="4.2874920800", price="10.00", total="42.87"),
            open_row(shares="3.8111040700", price="11.25", total="42.87"),
        ],
    )
    assert event.event.ratio == Fraction(8, 9)


# ===== when the rows were stamped =====


@pytest.mark.parametrize(
    ("close_time", "open_time"),
    [
        ("2026-02-02 07:44:13", "2026-02-02 07:44:13"),
        ("2026-02-02 07:44:13.000", "2026-02-02 07:44:13.009"),
        ("2026-02-02 07:44:13", "2026-02-02 07:44:14"),
    ],
    ids=["identical", "milliseconds apart", "at the bound"],
)
def test_halves_stamped_close_enough_together_pair(
    tmp_path: Path, close_time: str, open_time: str
) -> None:
    """The halves are stamped at one instant, or all but."""
    event = load_pair(tmp_path, [close_row(time=close_time), open_row(time=open_time)])
    assert event.event.ratio == Fraction(1, 9)


@pytest.mark.parametrize(
    ("close_time", "open_time"),
    [
        ("2026-02-02 07:44:13", "2026-02-02 07:44:14.001"),
        ("2026-02-02 07:44:14", "2026-02-02 07:44:13"),
    ],
    ids=["past the bound", "open before close"],
)
def test_halves_stamped_too_far_apart_do_not_pair(
    tmp_path: Path, close_time: str, open_time: str
) -> None:
    """A position cannot reopen before it closed, or a second later."""
    with pytest.raises(ParsingError) as error:
        load_pair(tmp_path, [close_row(time=close_time), open_row(time=open_time)])
    message = str(error.value)
    assert "Time:" in message
    assert f"Stock split close={close_time}" in message
    assert f"Stock split open={open_time}" in message


@pytest.mark.parametrize(
    ("trade_time", "open_time"),
    [
        ("2026-02-02 07:44:13", "2026-02-02 07:44:14"),
        ("2026-02-02 07:44:13.500", "2026-02-02 07:44:14"),
        ("2026-02-02 07:44:14", "2026-02-02 07:44:14"),
        ("2026-02-02 07:44:13", "2026-02-02 07:44:13"),
    ],
    ids=[
        "as the position closes",
        "between",
        "as it reopens",
        "as it closes and reopens at once",
    ],
)
def test_a_trade_at_or_between_the_two_halves_is_refused(
    tmp_path: Path, trade_time: str, open_time: str
) -> None:
    """Its count is in neither unit system, and nothing says which.

    The close is stamped 07:44:13 in every row. When the reopening carries the
    same stamp, a trade at that instant is the only one there is to refuse.
    """
    with pytest.raises(ParsingError, match="stamped at or between the two halves"):
        load(
            tmp_path,
            {
                "export.csv": [
                    HEADER_2026,
                    close_row(time="2026-02-02 07:44:13"),
                    trade_row(time=trade_time),
                    open_row(time=open_time),
                ]
            },
        )


def test_a_trade_outside_the_two_halves_is_left_alone(tmp_path: Path) -> None:
    """Only a row stamped at or between them is ambiguous.

    One trade is a millisecond before the position closes and one a millisecond
    after it reopens. Both load, beside the reorganisation itself.
    """
    transactions = load(
        tmp_path,
        {
            "export.csv": [
                HEADER_2026,
                trade_row(time="2026-02-02 07:44:12.999"),
                close_row(time="2026-02-02 07:44:13"),
                open_row(time="2026-02-02 07:44:14"),
                trade_row(time="2026-02-02 07:44:14.001", transaction_id="trade-2"),
            ]
        },
    )
    assert len(transactions) == 3


def test_the_current_header_and_an_offset_timestamp_pair(tmp_path: Path) -> None:
    """Trading 212 renamed the column and spelled the zone out."""
    event = load_pair(
        tmp_path,
        [
            close_row(
                HEADER_2026_UTC,
                time="2026-02-02T07:44:13+00:00",
                time_column=Trading212Column.TIME_UTC,
            ),
            open_row(
                HEADER_2026_UTC,
                time="2026-02-02T07:44:13+00:00",
                time_column=Trading212Column.TIME_UTC,
            ),
        ],
        HEADER_2026_UTC,
    )
    assert event.date == EVENT_DAY
    assert event.event.instants == (
        datetime(2026, 2, 2, 7, 44, 13, tzinfo=UTC),
        datetime(2026, 2, 2, 7, 44, 13, tzinfo=UTC),
    )


def test_a_late_evening_event_lands_on_the_uk_date(tmp_path: Path) -> None:
    """British Summer Time is an hour ahead, so 23:30 UTC is the next day."""
    event = load_pair(
        tmp_path,
        [
            close_row(time="2026-06-01 23:30:00"),
            open_row(time="2026-06-01 23:30:00"),
        ],
    )
    assert event.date == date(2026, 6, 2)


def test_an_event_at_the_clocks_going_forward_keeps_its_uk_date(
    tmp_path: Path,
) -> None:
    """01:00 UTC on the last Sunday in March is 02:00 in London, same day."""
    event = load_pair(
        tmp_path,
        [
            close_row(time="2026-03-29 01:00:00"),
            open_row(time="2026-03-29 01:00:00"),
        ],
    )
    assert event.date == date(2026, 3, 29)


# ===== more than one file, and more than one account =====


def test_the_two_halves_may_be_in_different_files(tmp_path: Path) -> None:
    """Exports are cut by date range, and an event can fall on the seam.

    Pairing has to see the whole directory: the per-file pass would reject a
    half whose partner is in the next file.
    """
    transactions = load(
        tmp_path,
        {
            "2026-01.csv": [HEADER_2026, close_row()],
            "2026-02.csv": [HEADER_2026, open_row()],
        },
    )
    (event,) = transactions
    assert isinstance(event, StockSplitTransaction)
    assert event.event.ratio == Fraction(1, 9)


def test_halves_in_two_declared_accounts_do_not_pair(tmp_path: Path) -> None:
    """Passing a directory is the user's declaration of one account.

    Nothing in the CSV identifies the account, so two separately configured
    inputs are two accounts and their rows are not two halves of one event.
    """
    first = tmp_path / "first"
    first.mkdir()
    _write_csv(first / "export.csv", [HEADER_2026, close_row()])
    second = tmp_path / "second"
    second.mkdir()
    _write_csv(second / "export.csv", [HEADER_2026, open_row()])

    with pytest.raises(ParsingError, match="once each"):
        Trading212Parser().load_from_dir(first)


def test_a_pair_reported_by_two_overlapping_exports_is_one_event(
    tmp_path: Path,
) -> None:
    """Consecutive downloads cover the same days and repeat the same rows."""
    transactions = load(
        tmp_path,
        {
            "2026-01.csv": [HEADER_2026, close_row(), open_row()],
            "2026-02.csv": [HEADER_2026, close_row(), open_row()],
        },
    )
    (event,) = transactions
    assert isinstance(event, StockSplitTransaction)
    assert event.event.ratio == Fraction(1, 9)


def test_one_id_reused_by_distinct_transactions_keeps_both(tmp_path: Path) -> None:
    """Trading 212 IDs are not globally unique transaction identifiers."""
    transactions = load(
        tmp_path,
        {
            "export.csv": [
                HEADER_2026,
                trade_row(transaction_id="reused"),
                trade_row(
                    action="Market sell",
                    time="2026-02-02 10:00:00",
                    transaction_id="reused",
                ),
            ]
        },
    )

    assert [transaction.action for transaction in transactions] == [
        ActionType.BUY,
        ActionType.SELL,
    ]


def test_repeated_rows_in_one_export_keep_their_multiplicity(tmp_path: Path) -> None:
    """One export is evidence that both otherwise-identical fills occurred."""
    row = trade_row(transaction_id="same")
    transactions = load(tmp_path, {"export.csv": [HEADER_2026, row, row]})

    assert len(transactions) == 2


def test_distinct_ids_keep_otherwise_identical_fills(tmp_path: Path) -> None:
    """Within matching recorded content, different IDs prove distinct fills."""
    transactions = load(
        tmp_path,
        {
            "first.csv": [HEADER_2026, trade_row(transaction_id="fill-1")],
            "second.csv": [HEADER_2026, trade_row(transaction_id="fill-2")],
        },
    )

    assert len(transactions) == 2


def test_rows_without_an_id_are_all_kept(tmp_path: Path) -> None:
    """A blank id identifies nothing, so two events can look identical.

    Collapsing them on whole-row equality would lose a real transaction.
    """
    transactions = load(
        tmp_path,
        {
            "export.csv": [
                HEADER_2026,
                trade_row(transaction_id="", time="2026-01-05 09:00:00"),
                trade_row(transaction_id="", time="2026-01-05 09:00:00"),
            ]
        },
    )
    assert len(transactions) == 2


def test_blank_id_overlap_keeps_the_largest_export_multiplicity(
    tmp_path: Path,
) -> None:
    """Copies across files collapse, while repeated fills in one file remain."""
    row = trade_row(transaction_id="", time="2026-01-05 09:00:00")
    transactions = load(
        tmp_path,
        {
            "short.csv": [HEADER_2026, row],
            "complete.csv": [HEADER_2026, row, row],
        },
    )

    assert len(transactions) == 2


def test_blank_id_split_pair_in_overlapping_exports_is_one_event(
    tmp_path: Path,
) -> None:
    """Overlap merging applies before paired reorganisation rows are counted."""
    rows = [
        close_row(transaction_id=""),
        open_row(transaction_id=""),
    ]
    (event,) = load(
        tmp_path,
        {
            "first.csv": [HEADER_2026, *rows],
            "second.csv": [HEADER_2026, *rows],
        },
    )

    assert isinstance(event, StockSplitTransaction)
    assert event.event.ratio == Fraction(1, 9)


def test_more_than_one_reorganisation_in_a_day_is_refused(
    tmp_path: Path,
) -> None:
    """One security is restated once a day, or nothing here can be proved."""
    with pytest.raises(ParsingError, match="has 2 Stock split close") as error:
        load(
            tmp_path,
            {
                "export.csv": [
                    HEADER_2026,
                    close_row(transaction_id=""),
                    close_row(transaction_id=""),
                    open_row(transaction_id=""),
                    open_row(transaction_id=""),
                ]
            },
        )
    message = str(error.value)
    assert all(f"export.csv, row {row}" in message for row in range(2, 6))
    assert "replace these files with one complete export covering 2026-02-02" in message


def test_three_by_three_pairing_ambiguity_is_refused(tmp_path: Path) -> None:
    """A third candidate must not make the matcher settle on its first answer."""
    rows = [HEADER_2026]
    rows.extend(close_row(transaction_id=f"close-{index}") for index in range(3))
    rows.extend(open_row(transaction_id=f"open-{index}") for index in range(3))

    with pytest.raises(ParsingError) as error:
        load(tmp_path, {"export.csv": rows})

    message = str(error.value)
    assert "has 3 Stock split close and 3 Stock split open rows" in message
    assert all(f"export.csv, row {row}" in message for row in range(2, 8))
    assert "work the day out by hand" in message


def test_genuine_cross_midnight_ambiguity_names_both_dates(tmp_path: Path) -> None:
    """A real many-to-many choice refuses with the full export range."""
    with pytest.raises(ParsingError) as error:
        load(
            tmp_path,
            {
                "export.csv": [
                    HEADER_2026,
                    close_row(time="2026-06-01 22:59:59.500", transaction_id="close-1"),
                    close_row(time="2026-06-01 22:59:59.500", transaction_id="close-2"),
                    open_row(time="2026-06-01 23:00:00.100", transaction_id="open-1"),
                    open_row(time="2026-06-01 23:00:00.100", transaction_id="open-2"),
                ]
            },
        )

    message = str(error.value)
    assert all(f"export.csv, row {row}" in message for row in range(2, 6))
    assert "has 2 Stock split close and 2 Stock split open rows" in message
    assert "one complete export covering 2026-06-01 through 2026-06-02" in message


# ===== a half that does not pair =====


@pytest.mark.parametrize("keep", ["close", "open"])
def test_a_half_on_its_own_is_refused(tmp_path: Path, keep: str) -> None:
    """One row says nothing about what happened to the holding."""
    rows = [close_row()] if keep == "close" else [open_row()]
    with pytest.raises(ParsingError, match="once each"):
        load_pair(tmp_path, rows)


@pytest.mark.parametrize(
    ("other_half", "message"),
    [
        # A different security is a different holding, so the two rows are
        # never brought together to be compared at all.
        (
            open_row(ticker="BAR"),
            "Ticker: Stock split close='FOO'; Stock split open='BAR'",
        ),
        (
            open_row(price_currency="USD", rate="1.25"),
            (
                "Currency (Price / share): Stock split close='GBP'; "
                "Stock split open='USD'"
            ),
        ),
        (
            open_row(currency="USD", rate="1.25"),
            "Currency (Total): Stock split close='GBP'; Stock split open='USD'",
        ),
        # One ticker under two identifiers is a reorganisation that changes
        # the security as well, which needs relationship data the export does
        # not carry.
        (open_row(isin="US0000000010"), "changes the ISIN"),
    ],
    ids=["ticker", "price currency", "account currency", "isin"],
)
def test_halves_that_describe_different_things_do_not_pair(
    tmp_path: Path, other_half: list[str], message: str
) -> None:
    """A reorganisation restates one holding, in one currency."""
    with pytest.raises(ParsingError) as error:
        load_pair(tmp_path, [close_row(), other_half])
    assert message in str(error.value)


def test_halves_whose_totals_disagree_do_not_pair(tmp_path: Path) -> None:
    """The two rows are the same money; that is what makes them a pair."""
    with pytest.raises(ParsingError) as error:
        load_pair(tmp_path, [close_row(), open_row(total="95.00")])
    message = str(error.value)
    assert "Total: Stock split close=90.00; Stock split open=95.00" in message
    assert "export.csv, row 2" in message
    assert "export.csv, row 3" in message
    assert "replace these files with one complete export covering 2026-02-02" in message
    # The refusal comes while the CSV is read, before any RAW row is seen, so
    # a RAW STOCK_SPLIT row cannot settle it. An export that is already
    # complete needs another remedy than a fuller export.
    assert "a RAW STOCK_SPLIT row cannot override it" in message
    assert "work the day out by hand" in message


def test_halves_whose_positions_are_worth_different_amounts_do_not_pair(
    tmp_path: Path,
) -> None:
    """A reorganisation preserves the value of the position."""
    with pytest.raises(ParsingError) as error:
        load_pair(tmp_path, [close_row(), open_row(price="900.00")])
    assert (
        "Stock split open calculated position value=99.999999000000 GBP; "
        "Total=90.00 GBP"
    ) in str(error.value)


# ===== the fields a reorganisation row must and must not carry =====


@pytest.mark.parametrize("shares", ["", "0.0000000000", "-1.0000000000"])
def test_a_half_without_a_positive_share_count_is_refused(
    tmp_path: Path, shares: str
) -> None:
    """The row states a complete position, so it states a real count."""
    with pytest.raises(ParsingError, match="needs a positive share count"):
        load_pair(tmp_path, [close_row(shares=shares), open_row()])


def test_a_half_with_an_unreadable_share_count_is_refused(tmp_path: Path) -> None:
    """A value that is not a number is a parsing failure with its row."""
    with pytest.raises(ParsingError, match="row 2") as error:
        load_pair(tmp_path, [close_row(shares="lots"), open_row()])
    assert "No. of shares" in str(error.value)


@pytest.mark.parametrize(
    ("half", "column"),
    [
        (close_row(shares="NaN"), "No. of shares"),
        (close_row(shares="Infinity"), "No. of shares"),
        (close_row(price="NaN"), "Price / share"),
        (close_row(price="Infinity"), "Price / share"),
        (close_row(total="NaN"), "Total"),
        (close_row(total="Infinity"), "Total"),
        (close_row(rate="NaN"), "Exchange rate"),
        (close_row(rate="Infinity"), "Exchange rate"),
    ],
)
def test_a_half_with_a_non_finite_field_is_refused(
    tmp_path: Path, half: list[str], column: str
) -> None:
    """Refuse a cell that parses as a decimal but is not an amount.

    "NaN" and "Infinity" are refused with the row they came from rather than
    at the first arithmetic that touches them, which raises a bare decimal
    exception carrying no context at all.
    """
    with pytest.raises(ParsingError, match=re.escape(f"Invalid decimal in {column}")):
        load_pair(tmp_path, [half, open_row()])


@pytest.mark.parametrize("price", ["", "0.00", "-1.00"])
def test_a_half_without_a_positive_price_is_refused(tmp_path: Path, price: str) -> None:
    """The price is what makes the stated value checkable."""
    with pytest.raises(ParsingError, match="needs a positive price per share"):
        load_pair(tmp_path, [close_row(price=price), open_row()])


@pytest.mark.parametrize("total", ["", "-1.00"])
def test_a_half_without_a_usable_total_is_refused(tmp_path: Path, total: str) -> None:
    """A position has a value, even if it rounds to nothing."""
    with pytest.raises(ParsingError, match="needs a total of zero or more"):
        load_pair(tmp_path, [close_row(total=total), open_row(total=total)])


def test_a_position_worth_less_than_a_penny_is_allowed(tmp_path: Path) -> None:
    """A small enough holding rounds to zero in the account currency."""
    event = load_pair(
        tmp_path,
        [
            close_row(shares="0.0000090000", price="0.90", total="0.00"),
            open_row(shares="0.0000010000", price="8.10", total="0.00"),
        ],
    )
    assert event.event.before_quantity == Decimal("0.0000090000")
    assert event.event.after_quantity == Decimal("0.0000010000")


def test_a_blank_exchange_rate_is_a_rate_of_one_in_one_currency(
    tmp_path: Path,
) -> None:
    """Nothing to convert when the price and the total are in one currency."""
    event = load_pair(tmp_path, [close_row(rate=""), open_row(rate="")])
    assert event.event.ratio == Fraction(1, 9)


def test_a_blank_exchange_rate_across_currencies_is_refused(
    tmp_path: Path,
) -> None:
    """Without a rate the stated value cannot be checked at all."""
    with pytest.raises(ParsingError, match="states no exchange rate"):
        load_pair(
            tmp_path,
            [
                close_row(price_currency="USD", rate=""),
                open_row(price_currency="USD", rate=""),
            ],
        )


@pytest.mark.parametrize("rate", ["0", "-1.25"])
def test_a_half_with_an_impossible_exchange_rate_is_refused(
    tmp_path: Path, rate: str
) -> None:
    """A rate divides the price, so it has to be a positive number."""
    with pytest.raises(ParsingError, match="needs a positive exchange rate"):
        load_pair(tmp_path, [close_row(rate=rate), open_row(rate=rate)])


def test_each_half_is_converted_at_its_own_rate(tmp_path: Path) -> None:
    """The two halves can carry different rates, and both are needed.

    Trading 212 derives both rows from one account-currency total, so the
    per-row rates are exactly what makes the two values agree. Swapping them
    inflates the disagreement a thousandfold.
    """
    event = load_pair(
        tmp_path,
        [
            close_row(price_currency="USD", price="112.50", rate="1.25"),
            open_row(price_currency="USD", price="1215.00", rate="1.50"),
        ],
    )
    assert event.event.ratio == Fraction(1, 9)


@pytest.mark.parametrize(
    ("column", "currency_column"),
    [
        (Trading212Column.RESULT, Trading212Column.CURRENCY_RESULT),
        (Trading212Column.WITHHOLDING_TAX, Trading212Column.CURRENCY_WITHHOLDING_TAX),
        (Trading212Column.TRANSACTION_FEE, Trading212Column.CURRENCY_TRANSACTION_FEE),
        (
            Trading212Column.STAMP_DUTY_RESERVE_TAX,
            Trading212Column.CURRENCY_STAMP_DUTY_RESERVE_TAX,
        ),
        (
            Trading212Column.CURRENCY_CONVERSION_FEE,
            Trading212Column.CURRENCY_CURRENCY_CONVERSION_FEE,
        ),
        (
            Trading212Column.CURRENCY_CONVERSION_FROM_AMOUNT,
            Trading212Column.CURRENCY_CURRENCY_CONVERSION_FROM_AMOUNT,
        ),
    ],
)
def test_a_half_that_moves_money_is_refused(
    tmp_path: Path,
    column: Trading212Column,
    currency_column: Trading212Column,
) -> None:
    """A reorganisation is not a disposal, so nothing is realised or paid.

    A cash leg alongside a consolidation can be a capital distribution, and
    that is a part disposal rather than a no-disposal reorganisation.
    """
    with pytest.raises(ParsingError, match="moves no money"):
        load_pair(
            tmp_path,
            [close_row(extra={column: "1.23", currency_column: "GBP"}), open_row()],
        )


def test_the_zeroes_a_real_export_writes_are_accepted(tmp_path: Path) -> None:
    """The close row states a result of 0.00 and the open row leaves it blank."""
    event = load_pair(
        tmp_path,
        [
            close_row(
                extra={
                    Trading212Column.RESULT: "0.00",
                    Trading212Column.CURRENCY_RESULT: "GBP",
                }
            ),
            open_row(),
        ],
    )
    assert event.event.ratio == Fraction(1, 9)


# ===== a ratio the counts do not single out =====


def test_a_ratio_the_counts_leave_ambiguous_still_parses(tmp_path: Path) -> None:
    """Whether the ratio is needed is not knowable while reading the file.

    A holding the export states in full is replaced outright and needs no
    ratio, so refusing here would reject a whole export for a number nothing
    asked for. The calculator refuses where the ratio is used.
    """
    event = load_pair(
        tmp_path,
        [
            close_row(shares="0.0001000000", price="900000.00", total="90.00"),
            open_row(shares="0.0000066700", price="13493253.37", total="90.00"),
        ],
    )
    assert isinstance(event.event.ratio, UnresolvedRatio)
    assert event.event.ratio.reason == "multiple"
    assert len(event.event.ratio.samples) == 2


def test_a_ratio_outside_the_supported_bound_still_parses(tmp_path: Path) -> None:
    """A ratio nothing supports is carried as unresolved, not guessed."""
    event = load_pair(
        tmp_path,
        [
            close_row(shares="5000.0000000000", price="0.018", total="90.00"),
            open_row(shares="1.0000000000", price="90.00", total="90.00"),
        ],
    )
    assert isinstance(event.event.ratio, UnresolvedRatio)
    assert event.event.ratio.reason == "none"


def test_a_full_run_over_a_split_and_a_consolidation(
    request: pytest.FixtureRequest,
) -> None:
    """Run the tool over an export holding both kinds of reorganisation.

    FOO is split ten for one and BAR consolidated nine for one, with the two
    halves of the FOO event in different exports. Both are in the current
    header with offset-aware timestamps, and the BAR halves are stamped nine
    milliseconds apart. The figures pin the whole path: the counts either
    side, the pooled cost carried through unchanged, the gain on a later
    disposal, and a cash balance the reorganisations leave alone even though
    their rows state a total.
    """
    cmd = build_cmd(
        "--year",
        "2024",
        "--trading212-dir",
        "tests/trading212/data/splits/inputs/",
        "--output",
        report_path(request),
    )
    result = run_cli(cmd)
    assert stderr_alerts(result.stderr) == [], "Run with example files generated errors"
    expected_file = (
        Path("tests") / "trading212" / "data" / "splits" / "expected_output.txt"
    )
    assert_stdout_matches(result, cmd, expected_file)


def test_a_full_run_renders_each_reorganisation(tmp_path: Path) -> None:
    """The report shows the event itself, not a purchase of free shares."""
    cmd = build_cmd(
        "--year",
        "2024",
        "--trading212-dir",
        "tests/trading212/data/splits/inputs/",
        "--output",
        str(tmp_path / "report"),
        keep_tex=True,
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr
    source = (tmp_path / "report.tex").read_text(encoding="utf-8")

    assert (
        "Share reorganisation: 100 units of FOO restated as 1000 (ratio 10)" in source
    )
    assert "Share reorganisation: 90 units of BAR restated as 10 (ratio 1/9)" in source
    assert "Neither a disposal nor an acquisition" in source
    # The pooled cost is carried across the event untouched.
    assert "Stated as 100 units before the event and 1000 after." in source
    # A reorganisation is never rendered as an acquisition.
    assert "Acquisition 3" not in source


def test_a_file_loaded_on_its_own_is_a_boundary_that_pairs(tmp_path: Path) -> None:
    """One file passed on its own is one declared account, so it finalizes.

    Pairing runs once per boundary. A directory is one, and so is a single
    file, which would otherwise hand the calculator two raw halves.
    """
    export = tmp_path / "export.csv"
    _write_csv(export, [HEADER_2026, close_row(), open_row()])

    (event,) = Trading212Parser().load_from_file(export)

    assert isinstance(event, StockSplitTransaction)
    assert event.event.ratio == Fraction(1, 9)


def test_an_unread_nonfinancial_column_does_not_make_a_second_transaction(
    tmp_path: Path,
) -> None:
    """Overlap identity rests on recorded tax data, not an unrelated card field.

    Merchant metadata cannot distinguish two otherwise identical security
    transactions, and a reused ID cannot make it authoritative either.
    """
    transactions = load(
        tmp_path,
        {
            "1.csv": [HEADER_2026, trade_row(transaction_id="same")],
            "2.csv": [
                HEADER_2026,
                trade_row(
                    transaction_id="same",
                    extra={Trading212Column.MERCHANT_NAME: "Somewhere else"},
                ),
            ],
        },
    )
    assert len(transactions) == 1


def test_a_column_only_one_export_carries_is_not_a_disagreement(
    tmp_path: Path,
) -> None:
    """Trading 212 has changed its columns more than once.

    A column one export has and the other does not says nothing about
    whether the two rows are the same transaction.
    """
    older = [column for column in HEADER_2026 if column != "Merchant name"]
    transactions = load(
        tmp_path,
        {
            "1.csv": [HEADER_2026, trade_row(transaction_id="same")],
            "2.csv": [older, trade_row(older, transaction_id="same")],
        },
    )
    assert len(transactions) == 1


def test_split_evidence_in_only_the_richer_schema_is_not_discarded(
    tmp_path: Path,
) -> None:
    """Every original split row is checked before overlap copies collapse."""
    older = [
        column
        for column in HEADER_2026
        if column not in {"Result", "Currency (Result)"}
    ]

    with pytest.raises(ParsingError) as error:
        load(
            tmp_path,
            {
                "1-old.csv": [
                    older,
                    close_row(older, transaction_id="close"),
                    open_row(older, transaction_id="open"),
                ],
                "2-new.csv": [
                    HEADER_2026,
                    close_row(
                        transaction_id="close",
                        extra={
                            Trading212Column.RESULT: "5.00",
                            Trading212Column.CURRENCY_RESULT: "GBP",
                        },
                    ),
                ],
            },
        )

    message = str(error.value)
    assert "2-new.csv, row 2" in message
    assert "Stock split close states Result of 5.00" in message


def test_a_legacy_split_with_financial_evidence_is_refused(tmp_path: Path) -> None:
    """A one-row split with a realised result is not a pure reorganisation."""
    with pytest.raises(ParsingError) as error:
        load(
            tmp_path,
            {
                "legacy.csv": [
                    HEADER_2026,
                    legacy_split_row(
                        time=CLOSED_AT,
                        extra={
                            Trading212Column.RESULT: "5.00",
                            Trading212Column.CURRENCY_RESULT: "GBP",
                        },
                    ),
                ]
            },
        )

    message = str(error.value)
    assert "legacy.csv, row 2" in message
    assert "Stock Split states Result of 5.00" in message
    assert "share reorganisation moves no money and realises nothing" in message
    assert "Check this row against Trading 212" in message
    assert "complete one covering 2026-02-02" in message


def test_legacy_split_evidence_in_only_the_richer_schema_is_not_discarded(
    tmp_path: Path,
) -> None:
    """An overlapping older export cannot hide a legacy split's cash leg."""
    older = [
        column
        for column in HEADER_2026
        if column not in {"Result", "Currency (Result)"}
    ]

    with pytest.raises(ParsingError) as error:
        load(
            tmp_path,
            {
                "1-old.csv": [
                    older,
                    legacy_split_row(older, time=CLOSED_AT),
                ],
                "2-new.csv": [
                    HEADER_2026,
                    legacy_split_row(
                        time=CLOSED_AT,
                        extra={
                            Trading212Column.RESULT: "5.00",
                            Trading212Column.CURRENCY_RESULT: "GBP",
                        },
                    ),
                ],
            },
        )

    message = str(error.value)
    assert "2-new.csv, row 2" in message
    assert "Stock Split states Result of 5.00" in message
    assert "share reorganisation moves no money and realises nothing" in message
    assert "Check this row against Trading 212" in message
    assert "complete one covering 2026-02-02" in message


def test_clean_split_overlap_across_schema_versions_is_one_event(
    tmp_path: Path,
) -> None:
    """A missing empty column is not evidence of a second reorganisation."""
    older = [
        column
        for column in HEADER_2026
        if column not in {"Result", "Currency (Result)"}
    ]

    (event,) = load(
        tmp_path,
        {
            "1-old.csv": [older, close_row(older), open_row(older)],
            "2-new.csv": [HEADER_2026, close_row(), open_row()],
        },
    )

    assert isinstance(event, StockSplitTransaction)
    assert event.event.ratio == Fraction(1, 9)


def test_the_first_copy_read_is_the_one_kept(tmp_path: Path) -> None:
    """Deterministic: globbed in sorted order, read in row order."""
    transactions = load(
        tmp_path,
        {
            "1.csv": [HEADER_2026, trade_row(transaction_id="same")],
            "2.csv": [HEADER_2026, trade_row(transaction_id="same")],
            "3.csv": [HEADER_2026, trade_row(transaction_id="same")],
        },
    )
    (kept,) = transactions
    assert isinstance(kept, Trading212Transaction)
    assert kept.source is not None
    assert kept.source.file is not None
    assert kept.source.file.name == "1.csv"


@pytest.mark.parametrize(
    ("close_time", "open_time"),
    [
        ("2026-02-02 07:44:13.000", "2026-02-02 07:44:13.500"),
        # 23:00 UTC is UK midnight under BST, so the representations date
        # to different UK days even though they are six tenths apart.
        ("2026-06-01 22:59:59.500", "2026-06-01 23:00:00.100"),
    ],
    ids=["same UK day", "across UK midnight"],
)
def test_legacy_and_paired_representations_of_one_split_are_refused(
    tmp_path: Path, close_time: str, open_time: str
) -> None:
    """Overlapping exports cannot apply the old and new shapes twice."""
    with pytest.raises(ParsingError) as error:
        load(
            tmp_path,
            {
                "legacy.csv": [HEADER_2026, legacy_split_row(time=close_time)],
                "paired.csv": [
                    HEADER_2026,
                    close_row(time=close_time),
                    open_row(time=open_time),
                ],
            },
        )

    message = str(error.value)
    assert "legacy.csv, row 2" in message
    assert "paired.csv, row 2" in message
    assert "paired.csv, row 3" in message
    assert "both Stock Split and Stock split close/open" in message
    assert "may describe one corporate action" in message
    assert "one complete export" in message
    assert "work the day out by hand" in message


# ===== the boundary the pairing rule actually uses =====


def test_halves_either_side_of_uk_midnight_pair(tmp_path: Path) -> None:
    """A calendar date is not a boundary the reorganisation knows about.

    23:00 UTC is midnight in London under BST, so a pair stamped six tenths
    of a second apart across it falls on two UK dates. Collecting candidates
    by date reported the close as a half whose partner never arrived, while
    the partner sat in the next row of the same file, and told the reader to
    re-export a range they already had in full.
    """
    event = load_pair(
        tmp_path,
        [
            close_row(time="2026-06-01 22:59:59.500"),
            open_row(time="2026-06-01 23:00:00.100"),
        ],
    )

    assert event.event.ratio == Fraction(1, 9)
    # The event is dated by the position it opens, which is the next UK day.
    assert event.date == date(2026, 6, 2)
    assert event.event.instants == (
        datetime(2026, 6, 1, 22, 59, 59, 500000, tzinfo=UTC),
        datetime(2026, 6, 1, 23, 0, 0, 100000, tzinfo=UTC),
    )


def test_reorganisations_hours_apart_are_separate_events(tmp_path: Path) -> None:
    """Candidacy is proximity, so two distant events are not one bad group.

    Two reorganisations of one security on one day are still refused, but by
    the calculator, which knows the whole pooled holding and says so; the
    parser has no reading to prefer between them and does not pretend to.
    """
    transactions = load(
        tmp_path,
        {
            "export.csv": [
                HEADER_2026,
                close_row(time="2026-02-02 07:44:13"),
                open_row(time="2026-02-02 07:44:13"),
                close_row(time="2026-02-02 15:00:00", transaction_id="close-2"),
                open_row(time="2026-02-02 15:00:00", transaction_id="open-2"),
            ]
        },
    )

    assert [type(transaction) for transaction in transactions] == [
        StockSplitTransaction,
        StockSplitTransaction,
    ]
    calculator = create_calculator(tax_year=2025, balance_check=False)
    with pytest.raises(CalculationError) as error:
        calculator.prepare_history(transactions)
    message = str(error.value)
    assert "different or unknown times" in message
    assert "Add a single RAW STOCK_SPLIT row" not in message
    assert "work this day out by hand" in message


def test_two_tight_pairs_inside_the_diagnostic_window_stay_separate(
    tmp_path: Path,
) -> None:
    """The wider near-miss window does not own otherwise proved events."""
    transactions = load(
        tmp_path,
        {
            "export.csv": [
                HEADER_2026,
                close_row(
                    time="2026-01-01 23:59:45",
                    shares="9",
                    price="10",
                    transaction_id="close-1",
                ),
                open_row(
                    time="2026-01-01 23:59:45",
                    shares="3",
                    price="30",
                    transaction_id="open-1",
                ),
                close_row(
                    time="2026-01-02 00:00:15",
                    shares="3",
                    price="30",
                    transaction_id="close-2",
                ),
                open_row(
                    time="2026-01-02 00:00:15",
                    shares="1",
                    price="90",
                    transaction_id="open-2",
                ),
            ]
        },
    )

    events = [
        transaction
        for transaction in transactions
        if isinstance(transaction, StockSplitTransaction)
    ]
    assert [(event.date, event.event.ratio) for event in events] == [
        (date(2026, 1, 1), Fraction(1, 3)),
        (date(2026, 1, 2), Fraction(1, 3)),
    ]


def test_chained_pairs_sharing_evidence_are_refused(tmp_path: Path) -> None:
    """Rows whose evidence overlaps are refused rather than searched.

    The first close could pair with either open here, and settling that
    requires searching the component for a complete matching, which grows
    factorially on rows deduplication legitimately keeps. Nothing real is
    lost by refusing: both readings are two reorganisations of one security
    on one date, which the calculator refuses anyway.
    """
    with pytest.raises(ParsingError, match="once each"):
        load(
            tmp_path,
            {
                "export.csv": [
                    HEADER_2026,
                    close_row(
                        time="2026-02-02 07:44:13.000",
                        shares="9",
                        price="10",
                        transaction_id="close-1",
                    ),
                    open_row(
                        time="2026-02-02 07:44:13.000",
                        shares="3",
                        price="30",
                        transaction_id="open-1",
                    ),
                    close_row(
                        time="2026-02-02 07:44:13.500",
                        shares="3",
                        price="30",
                        transaction_id="close-2",
                    ),
                    open_row(
                        time="2026-02-02 07:44:13.500",
                        shares="1",
                        price="90",
                        transaction_id="open-2",
                    ),
                ]
            },
        )


def test_an_unrelated_security_cannot_bridge_two_tight_pairs(tmp_path: Path) -> None:
    """A third holding cannot change which AAPL halves belong together."""
    rows = [HEADER_2026]
    for ticker, time, before, after, suffix in [
        ("AAPL", "2026-02-02 10:00:00", "9", "3", "aapl-1"),
        ("MSFT", "2026-02-02 10:01:00", "6", "2", "msft"),
        ("AAPL", "2026-02-02 10:02:00", "3", "1", "aapl-2"),
    ]:
        rows.extend(
            [
                close_row(
                    ticker=ticker,
                    time=time,
                    shares=before,
                    price=str(Decimal(TOTAL) / Decimal(before)),
                    transaction_id=f"close-{suffix}",
                ),
                open_row(
                    ticker=ticker,
                    time=time,
                    shares=after,
                    price=str(Decimal(TOTAL) / Decimal(after)),
                    transaction_id=f"open-{suffix}",
                ),
            ]
        )

    transactions = load(tmp_path, {"export.csv": rows})

    events = [
        transaction
        for transaction in transactions
        if isinstance(transaction, StockSplitTransaction)
    ]
    assert [(event.symbol, event.event.ratio) for event in events] == [
        ("AAPL", Fraction(1, 3)),
        ("MSFT", Fraction(1, 3)),
        ("AAPL", Fraction(1, 3)),
    ]


def test_a_pair_just_past_the_gap_names_the_partner_it_will_not_pair_with(
    tmp_path: Path,
) -> None:
    """The gap is the pairing rule, not the rule for what to show a reader.

    Candidates are collected over a wider window so a near miss reaches the
    check that names the gap. Collecting them over the gap itself puts the
    two rows in different groups and reports each as a lone half.
    """
    with pytest.raises(ParsingError) as error:
        load_pair(
            tmp_path,
            [
                close_row(time="2026-02-02 07:44:13"),
                open_row(time="2026-02-02 07:44:43"),
            ],
        )

    message = str(error.value)
    assert "Time:" in message
    assert "Stock split close=2026-02-02 07:44:13" in message
    assert "Stock split open=2026-02-02 07:44:43" in message
    assert "0 Stock split open rows" not in message


def test_separate_unresolved_events_do_not_share_diagnostics(tmp_path: Path) -> None:
    """A distant near miss cannot be blamed for the first incomplete event."""
    with pytest.raises(ParsingError) as error:
        load(
            tmp_path,
            {
                "export.csv": [
                    HEADER_2026,
                    close_row(time="2026-02-02 07:44:13", transaction_id="close-1"),
                    open_row(time="2026-02-02 07:44:43", transaction_id="open-1"),
                    close_row(time="2026-02-02 07:46:14", transaction_id="close-2"),
                    open_row(time="2026-02-02 07:46:44", transaction_id="open-2"),
                ]
            },
        )

    message = str(error.value)
    assert "Stock split close=2026-02-02 07:44:13" in message
    assert "Stock split open=2026-02-02 07:44:43" in message
    assert "2026-02-02 07:46:14" not in message
    assert "2026-02-02 07:46:44" not in message


# ===== how far the two positions may disagree =====

# Both halves restate one holding worth £100.00, so the tolerance on their
# reconstructed values is 1E-7 x 100 = 1.0e-5. Two shares at 50.0000045 come
# to 100.000009, which is inside it; at 50.0000055 they come to 100.000011,
# which is not. Each half is well within its own Total either way, so this
# pair of cases pins the bound between the two halves and nothing else.


@pytest.mark.parametrize(
    ("open_price", "pairs"),
    [("50.0000045", True), ("50.0000055", False)],
    ids=["just inside the bound", "just outside it"],
)
def test_the_value_tolerance_is_pinned_on_both_sides(
    tmp_path: Path, open_price: str, pairs: bool
) -> None:
    """A hand-fitted bound is only a bound if something fails either side."""
    rows = [
        close_row(shares="1.0000000000", price="100.00", total="100.00"),
        open_row(shares="2.0000000000", price=open_price, total="100.00"),
    ]
    if pairs:
        assert load_pair(tmp_path, rows).event.ratio == Fraction(2)
        return
    with pytest.raises(ParsingError) as error:
        load_pair(tmp_path, rows)
    message = str(error.value)
    assert "calculated position value" in message
    assert "100.0000110000" in message


# ===== refusals that name their row =====


def test_a_half_without_a_ticker_is_refused(tmp_path: Path) -> None:
    """A row that restates a holding has to say which holding."""
    with pytest.raises(ParsingError) as error:
        load_pair(tmp_path, [close_row(ticker=""), open_row()])

    message = str(error.value)
    assert "does not say which holding it restates" in message
    assert "row 2" in message


def test_an_unreadable_money_column_names_its_file_and_row(tmp_path: Path) -> None:
    """`Result` is parsed nowhere else, so this is the first look at it.

    It happens after the per-row wrapper that turns a bad cell into a
    `ParsingError` has gone, so left bare it reaches the user as an
    unexpected error and a traceback carrying no file and no row.
    """
    with pytest.raises(ParsingError) as error:
        load_pair(
            tmp_path,
            [
                close_row(
                    extra={
                        Trading212Column.RESULT: "n/a",
                        Trading212Column.CURRENCY_RESULT: "GBP",
                    }
                ),
                open_row(),
            ],
        )

    message = str(error.value)
    assert "Invalid decimal in Result: 'n/a'" in message
    assert "row 2" in message


def test_a_transaction_from_elsewhere_is_named_rather_than_dereferenced() -> None:
    """`python -O` drops an assert, so the narrowing is a real check.

    What followed the assert was an attribute only this parser's rows carry,
    so under `-O` the broken invariant surfaced as an `AttributeError` from
    somewhere with nothing to say about it.
    """
    stranger = BrokerTransaction(
        date=EVENT_DAY,
        action=ActionType.BUY,
        symbol="FOO",
        description="Foo Inc",
        quantity=Decimal(1),
        price=Decimal(1),
        fees=Decimal(0),
        amount=Decimal(-1),
        currency=CurrencyCode("GBP"),
        broker="Somewhere else",
    )

    with pytest.raises(TypeError, match="not read from a Trading 212 export"):
        Trading212Parser.post_process_transactions([stranger])


def test_ids_top_up_the_count_but_never_uncap_the_group(tmp_path: Path) -> None:
    """The shape where the id floor in `keep_count` is load-bearing.

    Three overlapping exports of one fill, one of which predates the ID
    column. Seeding the kept rows from the ids puts two in hand before the
    per-export cap of one is consulted, so the cap must stop the top-up
    without being read as licence to keep the whole group. The test above for
    distinct ids cannot see this: with an id on every row there is nothing
    left to top up with, so it passes whichever way the cap is written.
    """
    when = "2026-01-05 09:00:00"
    transactions = load(
        tmp_path,
        {
            "1-older.csv": [HEADER_2026, trade_row(transaction_id="", time=when)],
            "2-newer.csv": [HEADER_2026, trade_row(transaction_id="fill-1", time=when)],
            "3-newest.csv": [
                HEADER_2026,
                trade_row(transaction_id="fill-2", time=when),
            ],
        },
    )

    # Two ids, so two fills; the blank-id copy is one of them seen again.
    assert len(transactions) == 2
    assert sum(
        transaction.quantity or Decimal(0) for transaction in transactions
    ) == Decimal(2)


def test_a_row_between_the_halves_says_what_to_do_about_it(tmp_path: Path) -> None:
    """Every other split refusal carries a remedy; this one used to stop short.

    A reader told only that the units cannot be told apart has nowhere to go,
    and the row this names is one they can check against Trading 212.
    """
    with pytest.raises(ParsingError) as error:
        load_pair(
            tmp_path,
            [
                close_row(time="2026-02-02 07:44:13"),
                trade_row(time="2026-02-02 07:44:13.500"),
                open_row(time="2026-02-02 07:44:14"),
            ],
        )

    message = str(error.value)
    assert "stamped at or between the two halves" in message
    # The offending row, named the way every other split error names one.
    assert "Market buy at" in message
    assert "Time=2026-02-02 07:44:13.500" in message
    # And somewhere to go next, if the row is right about when it was dealt.
    assert (
        "the reorganisation at 2026-02-02 07:44:13+00:00 and 2026-02-02 "
        "07:44:14+00:00. Check this row against Trading 212; if it really was "
        "dealt at or between those two times, leave FOO out and work it out by "
        "hand (consider professional advice)."
    ) in message


def test_a_legacy_split_on_the_close_halfs_date_is_refused(tmp_path: Path) -> None:
    """A pair straddling UK midnight files its close on the previous day.

    Matching the legacy row against the event's own date alone missed it, and
    a legacy row more than a window away escaped the proximity test too. The
    calculator plans each day on its own, so the two never met and the same
    consolidation was applied twice, silently.
    """
    with pytest.raises(ParsingError) as error:
        load(
            tmp_path,
            {
                "export.csv": [
                    HEADER_2026,
                    # UK 1 June under BST, two hours before the close half.
                    legacy_split_row(time="2026-06-01 21:00:00"),
                    close_row(time="2026-06-01 22:59:59.500"),
                    open_row(time="2026-06-01 23:00:00.100"),
                ]
            },
        )

    message = str(error.value)
    assert "reported as both Stock Split and Stock split close/open" in message
    # Both dates the pair touches, so the reader can find every row.
    assert "2026-06-01 through 2026-06-02" in message


def test_a_legacy_split_a_day_away_from_the_pair_still_pairs(tmp_path: Path) -> None:
    """The widened window is the pair's own two dates, not a blanket radius.

    A reorganisation on its own day is a separate event, and refusing it
    would reject a history that states two of them perfectly clearly.
    """
    transactions = load(
        tmp_path,
        {
            "export.csv": [
                HEADER_2026,
                legacy_split_row(time="2026-05-31 12:00:00"),
                close_row(time="2026-06-01 22:59:59.500"),
                open_row(time="2026-06-01 23:00:00.100"),
            ]
        },
    )

    (event,) = (
        transaction
        for transaction in transactions
        if isinstance(transaction, StockSplitTransaction)
    )
    assert event.event.ratio == Fraction(1, 9)
    assert event.date == date(2026, 6, 2)


@pytest.mark.parametrize("price_currency", ["", "Not available"])
def test_a_half_whose_price_has_no_currency_is_refused(
    tmp_path: Path, price_currency: str
) -> None:
    """Pairing multiplies the price out and compares the two positions.

    An exchange rate does not supply the missing currency: it says how many
    of one buy the other, not which two those are. A rate of 1 was enough to
    let two halves reconcile on figures neither row stated the currency of.
    """
    with pytest.raises(ParsingError) as error:
        load_pair(
            tmp_path,
            [
                close_row(price_currency=price_currency, rate="1"),
                open_row(price_currency=price_currency, rate="1"),
            ],
        )

    message = str(error.value)
    assert "Currency (Price / share)" in message
    assert "A price with no currency cannot be checked" in message
    assert "row 2" in message


def test_a_legacy_split_a_moment_before_a_next_day_pair_is_refused(
    tmp_path: Path,
) -> None:
    """Only the proximity arm of the legacy guard catches this one.

    Both halves sit at 00:00:00.1 UK on 2 June, so the pair covers one date.
    The legacy row six tenths of a second earlier is on 1 June, which is in
    neither of the pair's dates -- the date arm cannot see it. Without the
    window the two representations both survive and the calculator, planning
    each day on its own, applies the consolidation twice.
    """
    with pytest.raises(ParsingError) as error:
        load(
            tmp_path,
            {
                "export.csv": [
                    HEADER_2026,
                    legacy_split_row(time="2026-06-01 22:59:59.500"),
                    close_row(time="2026-06-01 23:00:00.100"),
                    open_row(time="2026-06-01 23:00:00.100"),
                ]
            },
        )

    message = str(error.value)
    assert "reported as both Stock Split and Stock split close/open" in message
    assert "2026-06-01 through 2026-06-02" in message


def test_two_same_day_events_split_across_files_stay_two(tmp_path: Path) -> None:
    """Only the exported instant tells these four rows apart.

    Two reorganisations of one holding on one day, each half in its own
    export and every id blank, as Trading 212 writes them. The UK date is
    shared, the counts and totals are identical, and one half per file means
    the per-export multiplicity rule sees one of everything. Drop the instant
    from what overlap merging compares and the four rows collapse to one
    close and one open, which pair into a single event and quietly discard a
    real corporate action.
    """
    transactions = load(
        tmp_path,
        {
            "a.csv": [
                HEADER_2026,
                close_row(time="2026-02-02 07:00:00", transaction_id=""),
            ],
            "b.csv": [
                HEADER_2026,
                open_row(time="2026-02-02 07:00:00", transaction_id=""),
            ],
            "c.csv": [
                HEADER_2026,
                close_row(time="2026-02-02 15:00:00", transaction_id=""),
            ],
            "d.csv": [
                HEADER_2026,
                open_row(time="2026-02-02 15:00:00", transaction_id=""),
            ],
        },
    )

    events = [
        transaction
        for transaction in transactions
        if isinstance(transaction, StockSplitTransaction)
    ]
    assert len(events) == 2
    assert {event.event.instants[1] for event in events} == {
        datetime(2026, 2, 2, 7, 0, tzinfo=UTC),
        datetime(2026, 2, 2, 15, 0, tzinfo=UTC),
    }


def test_a_chain_of_near_misses_does_not_grow_one_candidate_group(
    tmp_path: Path,
) -> None:
    """Each diagnostic group is anchored at its first row, not its last.

    Three unpartnered rows a second under the window apart: measuring from
    the previous row instead of the first lets each one drag the group along,
    so an error about the 07:00 rows would name a row two minutes away as a
    candidate for the same event. Anchoring stops the group at the window.
    """
    with pytest.raises(ParsingError) as error:
        load(
            tmp_path,
            {
                "export.csv": [
                    HEADER_2026,
                    close_row(time="2026-02-02 07:00:00", transaction_id="c1"),
                    close_row(time="2026-02-02 07:00:59", transaction_id="c2"),
                    close_row(time="2026-02-02 07:01:58", transaction_id="c3"),
                ]
            },
        )

    message = str(error.value)
    assert "has 2 Stock split close and 0 Stock split open rows" in message
    assert "Time=2026-02-02 07:00:00" in message
    assert "Time=2026-02-02 07:00:59" in message
    # The third row is outside the window from the first, so it is a
    # candidate for some other event, not for this one.
    assert "07:01:58" not in message


# ===== overlapping exports may not synthesize an event =====


def _trade_between_halves(
    header: list[str], *, time: str = "2026-02-02 07:44:13.500"
) -> list[str]:
    """Return a buy of the reorganised holding, stamped at `time`."""
    return _make_row(
        header,
        {
            Trading212Column.ACTION: "Market buy",
            Trading212Column.TIME: time,
            Trading212Column.TICKER: "FOO",
            Trading212Column.NAME: "Foo Company",
            Trading212Column.ISIN: "US0000000002",
            Trading212Column.NO_OF_SHARES: "1.0000000000",
            Trading212Column.PRICE_PER_SHARE: "90.00",
            Trading212Column.CURRENCY_PRICE_PER_SHARE: "GBP",
            Trading212Column.TOTAL: "-90.00",
            Trading212Column.CURRENCY_TOTAL: "GBP",
            Trading212Column.TRANSACTION_ID: "mid",
        },
    )


def test_halves_from_two_exports_do_not_form_one_event(tmp_path: Path) -> None:
    """Two exports stamping the event differently must not be combined.

    Each half is deduplicated in its own group, so nothing stops a close
    from one export pairing with an open from another. The pair that
    results is one no export reported, and the checks that guard a
    reorganisation are read off its instants: a trade the first export
    places between its halves falls outside the synthesized pair and
    stops being refused.

    Refusing the ambiguity is the safe answer. Merging is for rows that
    state the same event, and these do not.
    """
    header = HEADER_2026
    early = [
        split_row(header, SPLIT_CLOSE_ACTION, time="2026-02-02 07:44:13.010"),
        split_row(
            header,
            SPLIT_OPEN_ACTION,
            time="2026-02-02 07:44:13.020",
            shares=CONSOLIDATION_AFTER,
            price=PRICE_AFTER,
        ),
    ]
    straddling = [
        split_row(header, SPLIT_CLOSE_ACTION, time="2026-02-02 07:44:13.100"),
        _trade_between_halves(header),
        split_row(
            header,
            SPLIT_OPEN_ACTION,
            time="2026-02-02 07:44:13.900",
            shares=CONSOLIDATION_AFTER,
            price=PRICE_AFTER,
        ),
    ]

    one = tmp_path / "one"
    both = tmp_path / "both"
    one.mkdir()
    both.mkdir()

    with pytest.raises(ParsingError) as alone:
        load(one, {"a.csv": [header, *straddling]})
    assert "stamped at or between the two halves" in str(alone.value)

    with pytest.raises(ParsingError) as combined:
        load(both, {"a.csv": [header, *straddling], "b.csv": [header, *early]})
    # Refused for the ambiguity rather than silently accepting the trade.
    assert "Stock split close" in str(combined.value)


def test_a_disagreed_trade_timestamp_is_not_erased_by_a_merge(
    tmp_path: Path,
) -> None:
    """Both exports' stamps for one trade have to reach the split check.

    Two exports report the same trade, one placing it between the halves
    and one before them. They record the same content, so the merge keeps
    a single copy, and it picks that copy without knowing a
    reorganisation surrounds it. Keeping only the survivor's stamp would
    let the export that saw nothing wrong overrule the one that did, and
    the trade's share count would be read on the wrong side of the event.

    The export that places it outside is right on its own evidence, so it
    still loads alone. Only the pair refuses.
    """
    header = HEADER_2026
    halves = [
        split_row(header, SPLIT_CLOSE_ACTION, time="2026-02-02 07:44:13.200"),
        split_row(
            header,
            SPLIT_OPEN_ACTION,
            time="2026-02-02 07:44:13.800",
            shares=CONSOLIDATION_AFTER,
            price=PRICE_AFTER,
        ),
    ]

    def export(trade_time: str) -> list[list[str]]:
        return [header, *halves, _trade_between_halves(header, time=trade_time)]

    between = export("2026-02-02 07:44:13.500")
    before = export("2026-02-02 07:44:13.100")

    for name, rows, refuses in (
        ("between", between, True),
        ("before", before, False),
    ):
        folder = tmp_path / name
        folder.mkdir()
        if refuses:
            with pytest.raises(ParsingError):
                load(folder, {"a.csv": rows})
        else:
            assert load(folder, {"a.csv": rows})

    both = tmp_path / "both"
    both.mkdir()
    with pytest.raises(ParsingError) as combined:
        load(both, {"a.csv": between, "b.csv": before})

    message = str(combined.value)
    assert "stamped at or between the two halves" in message
    # The survivor is the .100 copy, so the refusal has to name the .500
    # one it turned on or the error points at a stamp that looks fine.
    assert "also reported as" in message
    assert "07:44:13.500" in message


def test_the_same_event_in_two_exports_still_combines(tmp_path: Path) -> None:
    """The overlap that does state one event is still merged.

    Reorganisation rows compare on the exact instant and the figures
    pairing checks, so two exports reporting the event identically still
    collapse to one, and only a disagreement refuses.
    """
    header = HEADER_2026
    rows = [
        split_row(header, SPLIT_CLOSE_ACTION, time="2026-02-02 07:44:13.100"),
        split_row(
            header,
            SPLIT_OPEN_ACTION,
            time="2026-02-02 07:44:13.900",
            shares=CONSOLIDATION_AFTER,
            price=PRICE_AFTER,
        ),
    ]
    transactions = load(tmp_path, {"a.csv": [header, *rows], "b.csv": [header, *rows]})
    (event,) = transactions
    assert isinstance(event, StockSplitTransaction)


# ===== a known split the history has no row for =====
#
# NVIDIA split 10-for-1 with split-adjusted trading from 10 June 2024, and
# Super Micro Computer 10-for-1 from 1 October 2024; the Trading 212 exports
# examined have no rows for either. Each history here buys 2 shares for
# GBP 800 before the split and sells 1 for GBP 88 after it. With the split
# supplied the 2 shares become 20, so the sale's cost is 800 x 1/20 = 40, its
# gain 88 - 40 = 48, and 19 shares remain at a cost of 800 - 40 = 760.
# Without it the sale takes 800 x 1/2 = 400, a loss of 312 with no error.

NVDA_ISIN = "US67066G1040"
# Super Micro Computer's ISIN changed with its CUSIP at the split.
SMCI_ISIN_BEFORE = "US86800U1043"
SMCI_ISIN_AFTER = "US86800U3023"
NO_ROWS_DOCS = (
    "https://cgt-calc.uk/brokers/trading212/#a-split-with-no-rows-in-the-export"
)
OTHER_ROW_DOCS = "https://cgt-calc.uk/brokers/trading212/#a-split-row-on-another-day-or-with-another-ratio"
RAW_HEADER = "date,action,symbol,quantity,price,fees,currency"


def _trade(
    action: str,
    time: str,
    shares: str,
    price: str,
    *,
    ticker: str = "NVDA",
    isin: str = NVDA_ISIN,
) -> list[str]:
    """Build a sterling trade whose total is its shares times its price."""
    total = Decimal(shares) * Decimal(price)
    return _make_row(
        HEADER_2026,
        {
            Trading212Column.ACTION: action,
            Trading212Column.TIME: time,
            Trading212Column.ISIN: isin,
            Trading212Column.TICKER: ticker,
            Trading212Column.NAME: ticker,
            Trading212Column.NO_OF_SHARES: shares,
            Trading212Column.PRICE_PER_SHARE: price,
            Trading212Column.CURRENCY_PRICE_PER_SHARE: "GBP",
            Trading212Column.TOTAL: f"{total:.2f}",
            Trading212Column.CURRENCY_TOTAL: "GBP",
            Trading212Column.TRANSACTION_ID: f"{action}-{time}",
        },
    )


def _deposit(time: str) -> list[str]:
    return _make_row(
        HEADER_2026,
        {
            Trading212Column.ACTION: "Deposit",
            Trading212Column.TIME: time,
            Trading212Column.TOTAL: "1000.00",
            Trading212Column.CURRENCY_TOTAL: "GBP",
            Trading212Column.TRANSACTION_ID: f"deposit-{time}",
        },
    )


def _bought_then_sold(
    *,
    ticker: str = "NVDA",
    isin: str = NVDA_ISIN,
    bought: str = "2024-06-03 14:00:00",
    sold: str = "2024-06-12 14:00:00",
    sold_as: str | None = None,
) -> list[list[str]]:
    """Buy 2 for GBP 800 and sell 1 for GBP 88, with the cash to pay for it."""
    return [
        _deposit("2024-06-01 09:00:00"),
        _trade("Market buy", bought, "2", "400.00", ticker=ticker, isin=isin),
        _trade("Market sell", sold, "1", "88.00", ticker=sold_as or ticker, isin=isin),
    ]


def _known_split_cmd(
    tmp_path: Path,
    rows: list[list[str]],
    *,
    raw_rows: tuple[str, ...] = (),
    extra: tuple[str, ...] = (),
) -> list[str]:
    """Write a Trading 212 export, and a RAW file when given rows for one."""
    folder = tmp_path / "trading212"
    folder.mkdir()
    _write_csv(folder / "export.csv", [HEADER_2026, *rows])
    args = ["--year", "2024", "--trading212-dir", str(folder)]
    if raw_rows:
        raw_file = tmp_path / "splits.csv"
        raw_file.write_text("\n".join([RAW_HEADER, *raw_rows]) + "\n", encoding="utf-8")
        args += ["--raw-file", str(raw_file)]
    return build_cmd(*args, *extra, "--output", str(tmp_path / "report"))


def _refusal(cmd: list[str]) -> str:
    """Run a history that has to stop, and return what it wrote to stderr."""
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    assert result.returncode != 0, result.stdout
    return result.stderr


def _carried(symbol: str, date: str, held: str) -> str:
    """Return the opening every refusal for a missing row shares."""
    return (
        f"{symbol} traded at a new count from {date} after a 10-for-1 split, "
        f"but no input has a split row for {symbol} dated {date}. The history "
        f"holds {held} {symbol} going into that day, and Trading 212 rows "
        f"change the {symbol} holding on or after it, so without the split "
        "its purchases before that day would be counted in old shares and "
        "its sales after it in new ones. "
    )


def _add_split(symbol: str, date: str) -> str:
    return (
        f"as one STOCK_SPLIT row dated {date} in a file passed with --raw-file, "
        "stating how many shares it added to the whole holding, or, if you "
        f"also traded {symbol} that day, as a Stock Split row in a working copy "
        "of the export."
    )


def _missing(symbol: str, date: str, held: str) -> str:
    """Return the refusal for a split with no row on any date."""
    return (
        _carried(symbol, date, held)
        + f"Add the split {_add_split(symbol, date)} See {NO_ROWS_DOCS}"
    )


RAW_ON_ANOTHER_DATE = (
    "If a STOCK_SPLIT row in your RAW file records this split rather than an "
    "earlier one, change its date to 2024-06-10; if you also traded NVDA that "
    "day, replace it with a Stock Split row in a working copy of the export "
    "instead, because a RAW row has no time to place that day's trades against."
)
TRADING212_ON_ANOTHER_DATE = (
    "A split row in a Trading 212 file has a time that places trades either "
    "side of it, so do not change its date: if it records this split, replace "
    "it in a working copy of the export as the page below describes."
)
OTHER_BROKER_ON_ANOTHER_DATE = (
    "A split row from another broker's export can place that broker's trades "
    "either side of it by its time or its order, so do not change its date or "
    "add a split row beside it: if it records this split, check it against "
    "that broker's records and correct or replace it in that export's own "
    "format, and if its time and order cannot be kept, work the split out by "
    "hand (consider professional advice)."
)
RAW_WRONG_RATIO = (
    "A STOCK_SPLIT row states how many shares the split added to the whole "
    "holding, not the count after it: correct the number in your RAW file."
)
TRADING212_WRONG_RATIO = (
    "These rows are in a Trading 212 file. If you added a Stock Split row, "
    "correct its number of shares: it states how many shares the split added, "
    "not the count after it. If they are the export's own rows, a RAW row "
    "added beside them is refused or loses their times, so replace them in a "
    "working copy of the export as the page below describes."
)
OTHER_BROKER_WRONG_RATIO = (
    "This row is from another broker's export: check it against that broker's "
    "records and correct or replace it in that export's own format, without "
    "adding a split row beside it, and if its time and order cannot be kept, "
    "work the split out by hand (consider professional advice)."
)
ON_ANOTHER_DATE = (
    RAW_ON_ANOTHER_DATE,
    TRADING212_ON_ANOTHER_DATE,
    OTHER_BROKER_ON_ANOTHER_DATE,
)
WRONG_RATIO = (RAW_WRONG_RATIO, TRADING212_WRONG_RATIO, OTHER_BROKER_WRONG_RATIO)


def _assert_listed_elsewhere(stderr: str, held: str, dated: str, advice: str) -> None:
    """Check the refusal names the row on another date and only its advice."""
    assert (
        "ERROR: "
        + _carried("NVDA", "2024-06-10", held)
        + "The inputs have NVDA split rows on other dates:\n"
    ) in stderr
    listing = stderr.split("on other dates:\n", 1)[1].split("\nDo not add", 1)[0]
    # One entry, the row and then where it was read from.
    entries = re.findall(r"^  (\d{4}-\d\d-\d\d) ", listing, flags=re.MULTILINE)
    assert entries == [dated], listing
    assert (
        "\nDo not add a split row for NVDA on 2024-06-10 while one of these may "
        "record this split: two rows would apply it twice. "
        + advice
        + " If none of them records this split, add it "
        + _add_split("NVDA", "2024-06-10")
        + f" See {OTHER_ROW_DOCS}"
    ) in stderr
    for other in ON_ANOTHER_DATE:
        assert (other in stderr) == (other == advice)


def _assert_wrong_ratio(stderr: str, recorded: str, advice: str) -> None:
    """Check the refusal names the ratio the history applied and only its advice."""
    assert (
        "ERROR: The NVDA split on 2024-06-10 does not match NVDA's 10-for-1 "
        f"split that day: the history records {recorded}. The day's split rows "
        "are:\n"
    ) in stderr
    assert f"\n{advice} See {OTHER_ROW_DOCS}" in stderr
    for other in WRONG_RATIO:
        assert (other in stderr) == (other == advice)


def _assert_split_applied(stdout: str, symbol: str) -> None:
    """Check the sale took a twentieth of the cost, as worked out above."""
    assert re.search(r"Allowable costs:\s+£40\.00\n", stdout), stdout
    assert re.search(r"\n  Gain:\s+£48\.00\n", stdout), stdout
    assert f"  {symbol}: 19.00, £760.00\n" in stdout, stdout


def test_a_holding_carried_across_a_known_split_without_a_row_is_refused(
    tmp_path: Path,
) -> None:
    """The NVIDIA history that used to report a loss of 312 now stops."""
    stderr = _refusal(_known_split_cmd(tmp_path, _bought_then_sold()))

    assert "ERROR: " + _missing("NVDA", "2024-06-10", "2") + "\n" in stderr


@pytest.mark.parametrize(
    "isin",
    [
        pytest.param(SMCI_ISIN_BEFORE, id="identifier before the split"),
        pytest.param(SMCI_ISIN_AFTER, id="identifier after the split"),
    ],
)
def test_super_micro_carried_across_its_split_is_refused(
    tmp_path: Path, isin: str
) -> None:
    """Either of Super Micro Computer's identifiers names the common stock."""
    rows = _bought_then_sold(
        ticker="SMCI",
        isin=isin,
        bought="2024-09-24 14:00:00",
        sold="2024-10-08 14:00:00",
    )

    stderr = _refusal(_known_split_cmd(tmp_path, rows))

    assert "ERROR: " + _missing("SMCI", "2024-10-01", "2") + "\n" in stderr


@pytest.mark.parametrize(
    ("symbol", "rows", "raw_rows"),
    [
        pytest.param(
            "NVDA",
            _bought_then_sold(),
            ("2024-06-10,STOCK_SPLIT,NVDA,18,0.00,0.00,USD",),
            id="RAW row on the split day",
        ),
        pytest.param(
            "NVDA",
            [
                *_bought_then_sold(sold="2024-06-10 15:00:00"),
                legacy_split_row(
                    time="2024-06-10 00:00:00",
                    ticker="NVDA",
                    extra={Trading212Column.NO_OF_SHARES: "18"},
                ),
            ],
            (),
            id="timed Stock Split row before a sale that day",
        ),
        pytest.param(
            "SMCI",
            _bought_then_sold(
                ticker="SMCI",
                isin=SMCI_ISIN_BEFORE,
                bought="2024-09-24 14:00:00",
                sold="2024-10-08 14:00:00",
            ),
            ("2024-10-01,STOCK_SPLIT,SMCI,18,0.00,0.00,USD",),
            id="Super Micro with a RAW row",
        ),
    ],
)
def test_a_matching_split_row_lets_the_history_run(
    tmp_path: Path, symbol: str, rows: list[list[str]], raw_rows: tuple[str, ...]
) -> None:
    """A row on the split day adding 18 to the 2 held is the 10-for-1 split."""
    result = run_cli(_known_split_cmd(tmp_path, rows, raw_rows=raw_rows))

    _assert_split_applied(result.stdout, symbol)


def test_a_raw_row_stating_the_count_after_the_split_is_refused(
    tmp_path: Path,
) -> None:
    """Writing the total of 20 rather than the 18 added gives 22 / 2 = 11."""
    cmd = _known_split_cmd(
        tmp_path,
        _bought_then_sold(),
        raw_rows=("2024-06-10,STOCK_SPLIT,NVDA,20,0.00,0.00,USD",),
    )

    stderr = _refusal(cmd)

    _assert_wrong_ratio(
        stderr, "NVDA on 2024-06-10 (2 -> 22 units, ratio 11)", RAW_WRONG_RATIO
    )


def test_an_export_pair_giving_another_ratio_is_refused(tmp_path: Path) -> None:
    """The pair states 2 at GBP 110 and 22 at GBP 10, the same GBP 220, so 11."""
    rows = [
        *_bought_then_sold(),
        close_row(
            time="2024-06-10 05:00:00",
            shares="2.0000000000",
            price="110.00",
            total="220.00",
            ticker="NVDA",
            isin=NVDA_ISIN,
        ),
        open_row(
            time="2024-06-10 05:00:00",
            shares="22.0000000000",
            price="10.00",
            total="220.00",
            ticker="NVDA",
            isin=NVDA_ISIN,
        ),
    ]

    stderr = _refusal(_known_split_cmd(tmp_path, rows))

    _assert_wrong_ratio(
        stderr,
        "NVDA on 2024-06-10 (2 -> 22 units, ratio 11)",
        TRADING212_WRONG_RATIO,
    )


def _schwab_history(tmp_path: Path, split_date: str, added: str) -> str:
    """Schwab holds 2 NVDA bought on 3 June and reports a split of its own."""
    history = tmp_path / "schwab.csv"
    history.write_text(
        '"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"\n'
        f'"{split_date}","Stock Split","NVDA","NVIDIA CORP","{added}","","",""\n'
        '"06/03/2024","Buy","NVDA","NVIDIA CORP","2","$500.00","","-$1000.00"\n'
        '"06/03/2024","Wire Funds Received","","WIRED FUNDS RECEIVED","","","","$1000.00"\n',
        encoding="utf-8",
    )
    return str(history)


# A Trading 212 purchase after the split, which is what brings the Schwab
# holding under the check.
LATER_TRADING212_PURCHASE = [
    _deposit("2024-06-01 09:00:00"),
    _trade("Market buy", "2024-06-12 14:00:00", "1", "100.00"),
]


def test_another_broker_s_row_giving_another_ratio_is_refused(tmp_path: Path) -> None:
    """Schwab's row adds 20 to its 2, so 22 / 2 = 11, and it is Schwab's to fix."""
    cmd = _known_split_cmd(
        tmp_path,
        LATER_TRADING212_PURCHASE,
        extra=("--schwab-file", _schwab_history(tmp_path, "06/10/2024", "20")),
    )

    stderr = _refusal(cmd)

    _assert_wrong_ratio(
        stderr,
        "NVDA on 2024-06-10 (2 -> 22 units, ratio 11)",
        OTHER_BROKER_WRONG_RATIO,
    )


@pytest.mark.parametrize(
    ("raw_row", "held"),
    [
        # Already applied when 7 June opened: 2 + 18 = 20 going into 10 June.
        pytest.param("2024-06-07,STOCK_SPLIT,NVDA,18,0.00,0.00,USD", "20", id="before"),
        # Not applied yet when the first day from 10 June opens.
        pytest.param("2024-06-11,STOCK_SPLIT,NVDA,18,0.00,0.00,USD", "2", id="after"),
    ],
)
def test_a_raw_row_on_another_date_is_named_to_move(
    tmp_path: Path, raw_row: str, held: str
) -> None:
    """A RAW row has no time, so the refusal says to change its date."""
    cmd = _known_split_cmd(tmp_path, _bought_then_sold(), raw_rows=(raw_row,))

    stderr = _refusal(cmd)

    _assert_listed_elsewhere(stderr, held, raw_row[:10], RAW_ON_ANOTHER_DATE)


def test_an_export_pair_on_another_date_is_not_to_be_moved(tmp_path: Path) -> None:
    """The pair booked on 7 June has restated 2 as 20 by 10 June, at its times."""
    rows = [
        *_bought_then_sold(),
        close_row(
            time="2024-06-07 21:00:00",
            shares="2.0000000000",
            price="400.00",
            total="800.00",
            ticker="NVDA",
            isin=NVDA_ISIN,
        ),
        open_row(
            time="2024-06-07 21:00:00",
            shares="20.0000000000",
            price="40.00",
            total="800.00",
            ticker="NVDA",
            isin=NVDA_ISIN,
        ),
    ]

    stderr = _refusal(_known_split_cmd(tmp_path, rows))

    _assert_listed_elsewhere(stderr, "20", "2024-06-07", TRADING212_ON_ANOTHER_DATE)


def test_another_broker_s_row_on_another_date_is_that_broker_s_to_fix(
    tmp_path: Path,
) -> None:
    """Schwab's own row of 7 June has restated its 2 shares as 20 by 10 June."""
    cmd = _known_split_cmd(
        tmp_path,
        LATER_TRADING212_PURCHASE,
        extra=("--schwab-file", _schwab_history(tmp_path, "06/07/2024", "18")),
    )

    stderr = _refusal(cmd)

    _assert_listed_elsewhere(stderr, "20", "2024-06-07", OTHER_BROKER_ON_ANOTHER_DATE)


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param(
            [
                _deposit("2024-06-01 09:00:00"),
                _trade("Market buy", "2024-06-03 14:00:00", "2", "400.00"),
                _trade("Market sell", "2024-06-05 14:00:00", "2", "390.00"),
                _trade("Market buy", "2024-06-11 14:00:00", "1", "40.00"),
                _trade("Market sell", "2024-06-12 14:00:00", "1", "41.00"),
            ],
            id="sold out before the split",
        ),
        pytest.param(
            [
                _deposit("2024-06-01 09:00:00"),
                _trade("Market buy", "2024-06-03 14:00:00", "2", "400.00"),
                _make_row(
                    HEADER_2026,
                    {
                        Trading212Column.ACTION: "Dividend (Ordinary)",
                        Trading212Column.TIME: "2024-06-28 10:00:00",
                        Trading212Column.ISIN: NVDA_ISIN,
                        Trading212Column.TICKER: "NVDA",
                        Trading212Column.NAME: "NVDA",
                        Trading212Column.NO_OF_SHARES: "20",
                        Trading212Column.PRICE_PER_SHARE: "0.01",
                        Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                        Trading212Column.EXCHANGE_RATE: "Not available",
                        Trading212Column.TOTAL: "0.16",
                        Trading212Column.CURRENCY_TOTAL: "GBP",
                        Trading212Column.TRANSACTION_ID: "dividend-1",
                    },
                ),
            ],
            id="only a dividend after the split",
        ),
        pytest.param(
            _bought_then_sold(
                ticker="SMCI",
                isin="XS2779863021",
                bought="2024-09-24 14:00:00",
                sold="2024-10-08 14:00:00",
            ),
            id="another security trading as SMCI",
        ),
        pytest.param(_bought_then_sold(isin=""), id="trades without an ISIN"),
    ],
)
def test_a_history_the_check_cannot_prove_needs_the_split_runs(
    rows: list[list[str]], tmp_path: Path
) -> None:
    """Each history lacks one thing the refusal needs, so nothing is refused.

    Respectively: nothing held going into 10 June; no Trading 212 row
    changing the holding after it; a Leverage Shares ETP whose ISIN is not
    Super Micro Computer's; and rows that cannot show which security they
    are. The figures are not asserted: the last three are calculated as
    they were before, without a split, which is the stated limit.
    """
    result = run_cli(_known_split_cmd(tmp_path, rows))

    assert "split row for" not in result.stderr


def test_a_sale_on_the_split_day_itself_is_refused(tmp_path: Path) -> None:
    """The check runs as 10 June opens, not only on a later day."""
    rows = _bought_then_sold(sold="2024-06-10 15:00:00")

    stderr = _refusal(_known_split_cmd(tmp_path, rows))

    assert "ERROR: " + _missing("NVDA", "2024-06-10", "2") + "\n" in stderr


def test_a_sale_exported_under_an_alias_is_the_same_holding(tmp_path: Path) -> None:
    """NVD under NVIDIA's ISIN is read as NVDA before the check looks."""
    rows = _bought_then_sold(sold_as="NVD")

    stderr = _refusal(_known_split_cmd(tmp_path, rows))

    assert "ERROR: " + _missing("NVDA", "2024-06-10", "2") + "\n" in stderr


def test_another_broker_s_history_is_not_checked(tmp_path: Path) -> None:
    """A Freetrade NVIDIA history crossing the split is outside the check.

    Its rows carry NVIDIA's own ISIN, a holding of 2 and a sale after the
    split, so only their broker keeps them out. The figures are not
    asserted: without a split row they are wrong exactly as before.
    """
    history = tmp_path / "freetrade.csv"
    rows = [
        _default_new_row(
            {
                FreetradeColumn.TITLE.value: "Top up",
                FreetradeColumn.TYPE.value: "TOP_UP",
                FreetradeColumn.TIMESTAMP.value: "2024-06-01T09:00:00.000Z",
                FreetradeColumn.BUY_SELL.value: "",
                FreetradeColumn.TICKER.value: "",
                FreetradeColumn.ISIN.value: "",
                "Total Amount in Account Currency": "1000.00",
            }
        ),
        _default_new_row(
            {
                FreetradeColumn.TITLE.value: "NVIDIA",
                FreetradeColumn.TIMESTAMP.value: "2024-06-03T14:00:00.000Z",
                FreetradeColumn.BUY_SELL.value: "BUY",
                FreetradeColumn.TICKER.value: "NVDA",
                FreetradeColumn.ISIN.value: NVDA_ISIN,
                FreetradeColumn.QUANTITY.value: "2",
                FreetradeColumn.PRICE_PER_SHARE_ACCOUNT.value: "400.00",
                "Total Amount in Account Currency": "800.00",
            }
        ),
        _default_new_row(
            {
                FreetradeColumn.TITLE.value: "NVIDIA",
                FreetradeColumn.TIMESTAMP.value: "2024-06-12T14:00:00.000Z",
                FreetradeColumn.BUY_SELL.value: "SELL",
                FreetradeColumn.TICKER.value: "NVDA",
                FreetradeColumn.ISIN.value: NVDA_ISIN,
                FreetradeColumn.QUANTITY.value: "1",
                FreetradeColumn.PRICE_PER_SHARE_ACCOUNT.value: "88.00",
                "Total Amount in Account Currency": "88.00",
            }
        ),
    ]
    with history.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows([NEW_COLUMNS, *rows])

    result = run_cli(
        build_cmd(
            "--year",
            "2024",
            "--freetrade-file",
            str(history),
            "--output",
            str(tmp_path / "report"),
        )
    )

    assert "split row for" not in result.stderr
