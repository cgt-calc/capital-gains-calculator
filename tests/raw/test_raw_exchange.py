"""Tests for EXCHANGE rows in the RAW format and the legs they are read as."""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import InvalidTransactionError, ParsingError
from cgt_calc.exchanges import exchange_legs, expand_exchanges
from cgt_calc.model import ActionType
from cgt_calc.parsers.raw import RawParser, RawTransaction
from tests.general.test_calc import create_calculator, get_report
from tests.utils import (
    assert_stdout_matches,
    build_cmd,
    report_path,
    run_cli,
    stderr_alerts,
)

if TYPE_CHECKING:
    from cgt_calc.model import CapitalGainsReport

HEADER = [
    "date",
    "action",
    "symbol",
    "quantity",
    "price",
    "fees",
    "currency",
    "to_symbol",
    "to_quantity",
    "fee_symbol",
    "fee_quantity",
]


def _read(tmp_path: Path, rows: list[list[str]]) -> list[RawTransaction]:
    raw_file = tmp_path / "raw.csv"
    with raw_file.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows([HEADER, *rows])
    return RawParser.load_from_file(raw_file, show_parsing_msg=False)


def _report(tmp_path: Path, rows: list[list[str]]) -> CapitalGainsReport:
    return get_report(create_calculator(tax_year=2024), list(_read(tmp_path, rows)))


FUNDING = [
    ["2024-05-01", "TRANSFER", "", "1", "20000", "0", "GBP", "", "", "", ""],
    ["2024-05-01", "BUY", "BTC", "0.5", "30000", "0", "GBP", "", "", "", ""],
    ["2024-05-01", "BUY", "BNB", "1", "400", "0", "GBP", "", "", "", ""],
]


def test_an_exchange_row_reads_both_sides_and_the_fee_asset(tmp_path: Path) -> None:
    """The asset received and the fee's asset are kept on the row."""
    (row,) = _read(
        tmp_path,
        [
            [
                "2024-06-03",
                "EXCHANGE",
                "BTC",
                "0.5",
                "40000",
                "4.00",
                "GBP",
                "ETH",
                "10",
                "BNB",
                "0.01",
            ]
        ],
    )

    assert row.action is ActionType.EXCHANGE
    assert row.symbol == "BTC"
    assert row.fees == Decimal("4.00")
    assert row.amount == Decimal("19996.00")
    assert (row.to_symbol, row.to_quantity, row.fee_symbol, row.fee_quantity) == (
        "ETH",
        Decimal(10),
        "BNB",
        Decimal("0.01"),
    )


def test_a_row_without_the_exchange_columns_has_no_exchange(tmp_path: Path) -> None:
    """A RAW file without the new columns reads as it did."""
    raw_file = tmp_path / "raw.csv"
    raw_file.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2024-05-01,BUY,BTC,0.5,30000,0,GBP\n",
        encoding="utf-8",
    )

    (row,) = RawParser.load_from_file(raw_file, show_parsing_msg=False)

    assert (row.to_symbol, row.to_quantity, row.fee_symbol, row.fee_quantity) == (
        None,
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (
            ["2024-05-01", "BUY", "BTC", "1", "10", "0", "GBP", "ETH", "", "", ""],
            "read only on an EXCHANGE row",
        ),
        (
            ["2024-06-03", "EXCHANGE", "", "1", "10", "0", "GBP", "ETH", "1", "", ""],
            "names the asset given up as its symbol",
        ),
        (
            ["2024-06-03", "EXCHANGE", "BTC", "1", "10", "0", "GBP", "", "", "", ""],
            "'to_symbol' column",
        ),
        (
            [
                "2024-06-03",
                "EXCHANGE",
                "BTC",
                "1",
                "10",
                "0",
                "GBP",
                "BTC",
                "1",
                "",
                "",
            ],
            "gives up and receives the same asset",
        ),
        (
            [
                "2024-06-03",
                "EXCHANGE",
                "BTC",
                "1",
                "10",
                "0",
                "GBP",
                "GBP",
                "1",
                "",
                "",
            ],
            "write a SELL row",
        ),
        (
            [
                "2024-06-03",
                "EXCHANGE",
                "BTC",
                "1",
                "10",
                "0",
                "GBP",
                "ETH",
                "0",
                "",
                "",
            ],
            "positive 'to_quantity'",
        ),
        (
            [
                "2024-06-03",
                "EXCHANGE",
                "BTC",
                "0",
                "10",
                "0",
                "GBP",
                "ETH",
                "1",
                "",
                "",
            ],
            "positive quantity",
        ),
        (
            ["2024-06-03", "EXCHANGE", "BTC", "1", "", "0", "GBP", "ETH", "1", "", ""],
            "market value of one unit",
        ),
        (
            [
                "2024-06-03",
                "EXCHANGE",
                "BTC",
                "1",
                "10",
                "1",
                "GBP",
                "ETH",
                "1",
                "BNB",
                "",
            ],
            "positive 'fee_quantity'",
        ),
        (
            [
                "2024-06-03",
                "EXCHANGE",
                "BTC",
                "1",
                "10",
                "",
                "GBP",
                "ETH",
                "1",
                "BNB",
                "1",
            ],
            "fee's market value",
        ),
        (
            [
                "2024-06-03",
                "EXCHANGE",
                "BTC",
                "1",
                "10",
                "1",
                "GBP",
                "ETH",
                "1",
                "",
                "1",
            ],
            "needs the asset the fee was paid in",
        ),
        (
            [
                "2024-06-03",
                "EXCHANGE",
                "BTC",
                "1",
                "10",
                "1",
                "GBP",
                "ETH",
                "1",
                "GBP",
                "1",
            ],
            "leave 'fee_symbol' and 'fee_quantity' blank",
        ),
    ],
    ids=[
        "exchange column on a buy",
        "nothing given up named",
        "no asset received",
        "same asset both sides",
        "cash received",
        "nothing received",
        "nothing given up",
        "no price",
        "fee asset without its units",
        "fee asset without its value",
        "fee units without their asset",
        "fee in the row's own cash",
    ],
)
def test_an_invalid_exchange_row_is_refused(
    tmp_path: Path, row: list[str], expected: str
) -> None:
    """Each mistake is named, against its row."""
    with pytest.raises(ParsingError) as exc:
        _read(tmp_path, [row])

    assert "row 2" in str(exc.value)
    assert expected in str(exc.value)


def test_an_asset_received_in_another_letter_case_is_refused(tmp_path: Path) -> None:
    """`to_symbol` is held to one spelling, like `symbol`."""
    with pytest.raises(ParsingError, match="differ only by letter case"):
        _read(
            tmp_path,
            [
                ["2024-05-01", "BUY", "BTC", "1", "10", "0", "GBP", "", "", "", ""],
                [
                    "2024-06-03",
                    "EXCHANGE",
                    "btc",
                    "1",
                    "10",
                    "0",
                    "GBP",
                    "ETH",
                    "1",
                    "",
                    "",
                ],
            ],
        )


def test_an_exchange_is_read_as_a_sale_a_purchase_and_a_fee_sale(
    tmp_path: Path,
) -> None:
    """The legs stand where the row stood, priced from its value and fee."""
    rows = _read(
        tmp_path,
        [
            ["2024-05-01", "BUY", "BTC", "0.5", "30000", "0", "GBP", "", "", "", ""],
            [
                "2024-06-03",
                "EXCHANGE",
                "BTC",
                "0.5",
                "40000",
                "4",
                "GBP",
                "ETH",
                "8",
                "BNB",
                "0.01",
            ],
        ],
    )

    expanded = expand_exchanges(list(rows))

    assert expanded[0] is rows[0]
    assert [
        (leg.action, leg.symbol, leg.quantity, leg.price, leg.fees, leg.amount)
        for leg in expanded[1:]
    ] == [
        (
            ActionType.SELL,
            "BTC",
            Decimal("0.5"),
            Decimal(40000),
            Decimal(4),
            Decimal(19996),
        ),
        (ActionType.BUY, "ETH", Decimal(8), Decimal(2500), Decimal(0), Decimal(-20000)),
        (
            ActionType.SELL,
            "BNB",
            Decimal("0.01"),
            Decimal(400),
            Decimal(0),
            Decimal(4),
        ),
    ]
    for leg in expanded[1:]:
        assert isinstance(leg, RawTransaction)
        assert leg.source == rows[1].source
        assert leg.to_symbol is None
        assert leg.fee_symbol is None
        assert not leg.affects_cash_balance
    # The row read from the file is left as it was.
    assert rows[1].action is ActionType.EXCHANGE


def test_an_exchange_with_a_cash_fee_has_no_fee_sale(tmp_path: Path) -> None:
    """A fee paid in cash is a cost of the sale and nothing more."""
    (row,) = _read(
        tmp_path,
        [["2024-06-03", "EXCHANGE", "BTC", "1", "100", "2", "GBP", "ETH", "4", "", ""]],
    )

    assert [(leg.action, leg.symbol, leg.fees) for leg in exchange_legs(row)] == [
        (ActionType.SELL, "BTC", Decimal(2)),
        (ActionType.BUY, "ETH", Decimal(0)),
    ]


def test_an_exchange_disposes_of_one_asset_and_pools_the_other(
    tmp_path: Path,
) -> None:
    """BTC is sold at the value of the swap less the fee, ETH costs that value.

    - BTC: 20,000 - 4 fee - 15,000 cost = 4,996;
    - BNB fee: 0.01 at 400, its cost, so no gain;
    - ETH: sold for 21,000 against its 20,000 cost = 1,000.

    The exchange moves no cash, so the balance check passes on the deposit.
    """
    report = _report(
        tmp_path,
        [
            *FUNDING,
            [
                "2024-06-03",
                "EXCHANGE",
                "BTC",
                "0.5",
                "40000",
                "4.00",
                "GBP",
                "ETH",
                "10",
                "BNB",
                "0.01",
            ],
            ["2024-07-01", "SELL", "ETH", "10", "2100", "0", "GBP", "", "", "", ""],
        ],
    )

    exchange_day = dt.date(2024, 6, 3)
    (btc,) = report.calculation_log[exchange_day]["sell$BTC"]
    (bnb,) = report.calculation_log[exchange_day]["sell$BNB"]
    (eth,) = report.calculation_log[dt.date(2024, 7, 1)]["sell$ETH"]
    assert btc.gain == Decimal(4996)
    assert bnb.gain == Decimal(0)
    assert eth.allowable_cost == Decimal(20000)
    assert eth.gain == Decimal(1000)
    assert report.capital_gain == Decimal(5996)
    assert [
        (entry.symbol, entry.quantity) for entry in report.portfolio if entry.quantity
    ] == [("BNB", Decimal("0.99"))]


def test_a_fee_taken_from_the_asset_received_is_matched_the_same_day(
    tmp_path: Path,
) -> None:
    """The fee leaves units bought that day, so it is matched at their cost."""
    report = _report(
        tmp_path,
        [
            *FUNDING,
            [
                "2024-06-03",
                "EXCHANGE",
                "BTC",
                "0.5",
                "40000",
                "20",
                "GBP",
                "ETH",
                "10",
                "ETH",
                "0.01",
            ],
        ],
    )

    exchange_day = dt.date(2024, 6, 3)
    (fee,) = report.calculation_log[exchange_day]["sell$ETH"]
    # 0.01 ETH costs 20 of the 20,000 paid for 10, and is worth the 20 fee.
    assert fee.allowable_cost == Decimal(20)
    assert fee.gain == Decimal(0)
    (eth,) = (entry for entry in report.portfolio if entry.symbol == "ETH")
    assert eth.quantity == Decimal("9.99")


def test_a_fee_paid_in_the_asset_given_up_is_a_second_sale_of_it(
    tmp_path: Path,
) -> None:
    """Both disposals of BTC that day are matched against its pool."""
    report = _report(
        tmp_path,
        [
            *FUNDING,
            [
                "2024-06-03",
                "EXCHANGE",
                "BTC",
                "0.4",
                "40000",
                "400",
                "GBP",
                "ETH",
                "8",
                "BTC",
                "0.01",
            ],
        ],
    )

    entries = report.calculation_log[dt.date(2024, 6, 3)]["sell$BTC"]
    # 0.41 BTC leaves the pool at 30,000 each: 12,300, and the 400 fee is a
    # cost too. Proceeds are the 16,000 swap and the 400 the fee BTC was worth.
    assert sum(entry.quantity for entry in entries) == Decimal("0.41")
    assert sum(entry.allowable_cost for entry in entries) == Decimal(12700)
    assert report.capital_gain == Decimal(16400 - 12700)


def test_exchanging_an_asset_not_held_is_refused(tmp_path: Path) -> None:
    """The sale leg is checked like any sale, and names the RAW row."""
    with pytest.raises(InvalidTransactionError) as exc:
        _report(
            tmp_path,
            [
                [
                    "2024-06-03",
                    "EXCHANGE",
                    "SOL",
                    "1",
                    "100",
                    "0",
                    "GBP",
                    "ETH",
                    "1",
                    "",
                    "",
                ]
            ],
        )

    assert "SOL" in str(exc.value)
    assert "row 2" in str(exc.value)


def test_run_with_an_exchange_raw_file(request: pytest.FixtureRequest) -> None:
    """A RAW file with exchanges runs from end to end and writes the report."""
    cmd = build_cmd(
        "--year",
        "2024",
        "--raw-file",
        "tests/raw/data/test_data_exchange.csv",
        "--output",
        report_path(request),
    )
    result = run_cli(cmd)
    assert stderr_alerts(result.stderr) == [], "Unexpected stderr message"
    expected_file = Path("tests") / "raw" / "data" / "expected_output_exchange.txt"
    assert_stdout_matches(result, cmd, expected_file)


def test_an_exchange_column_the_header_leaves_out_reads_as_blank(
    tmp_path: Path,
) -> None:
    """A header naming only some exchange columns reads the rest as blank."""
    raw_file = tmp_path / "raw.csv"
    raw_file.write_text(
        "date,action,symbol,quantity,price,fees,currency,to_symbol\n"
        "2024-06-03,EXCHANGE,BTC,1,10,0,GBP,ETH\n",
        encoding="utf-8",
    )

    with pytest.raises(ParsingError, match="positive 'to_quantity'"):
        RawParser.load_from_file(raw_file, show_parsing_msg=False)
