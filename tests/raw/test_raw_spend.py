"""Tests for payments in an asset in the RAW format: SPEND rows and transfer fees."""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import CalculationError, InvalidTransactionError, ParsingError
from cgt_calc.exchanges import expand_to_legs, transfer_legs
from cgt_calc.model import ActionType
from cgt_calc.parsers.raw import RawParser, RawTransaction
from tests.general.test_calc import create_calculator, get_report

if TYPE_CHECKING:
    from pathlib import Path

    from cgt_calc.model import CapitalGainsReport

HEADER = ["date", "action", "symbol", "quantity", "price", "fees", "currency"]
# The fee columns a TRANSFER reads, without the other exchange columns.
FEE_HEADER = [*HEADER, "fee_symbol", "fee_quantity"]

FUNDING = [
    ["2024-05-01", "TRANSFER", "", "1", "5000", "0", "GBP"],
    ["2024-05-01", "BUY", "ETH", "2", "2500", "0", "GBP"],
]
NETWORK_FEE = ["2024-07-10", "SPEND", "ETH", "0.002", "2600", "0", "GBP"]
# 1.5 ETH moved between wallets for a fee of 0.002 ETH worth 5.20.
TRANSFER_FEE = [
    "2024-07-10",
    "TRANSFER",
    "ETH",
    "1.5",
    "0",
    "5.20",
    "GBP",
    "ETH",
    "0.002",
]


def _read(
    tmp_path: Path, rows: list[list[str]], header: list[str] = HEADER
) -> list[RawTransaction]:
    raw_file = tmp_path / "raw.csv"
    with raw_file.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows([header, *rows])
    return RawParser.load_from_file(raw_file, show_parsing_msg=False)


def _report(
    tmp_path: Path, rows: list[list[str]], header: list[str] = HEADER
) -> CapitalGainsReport:
    return get_report(
        create_calculator(tax_year=2024), list(_read(tmp_path, rows, header))
    )


def _with_fee_columns(rows: list[list[str]]) -> list[list[str]]:
    return [row if len(row) == len(FEE_HEADER) else [*row, "", ""] for row in rows]


def test_a_spend_row_is_worth_its_market_value(tmp_path: Path) -> None:
    """The amount is the value given, as on a sale without fees."""
    (row,) = _read(tmp_path, [NETWORK_FEE])

    assert row.action is ActionType.SPEND
    assert row.symbol == "ETH"
    assert row.fees == Decimal(0)
    assert row.amount == Decimal("5.200")


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        pytest.param(
            ["2024-07-10", "SPEND", "", "1", "10", "0", "GBP"],
            "names the asset given",
            id="no symbol",
        ),
        pytest.param(
            ["2024-07-10", "SPEND", "GBP", "1", "10", "0", "GBP"],
            "write an ADJUSTMENT row",
            id="cash",
        ),
        pytest.param(
            ["2024-07-10", "SPEND", "ETH", "0", "10", "0", "GBP"],
            "positive quantity",
            id="no units",
        ),
        pytest.param(
            ["2024-07-10", "SPEND", "ETH", "1", "0", "0", "GBP"],
            "positive price",
            id="price of zero",
        ),
        pytest.param(
            ["2024-07-10", "SPEND", "ETH", "1", "10", "1", "GBP"],
            "has no fees",
            id="fees",
        ),
    ],
)
def test_an_invalid_spend_row_is_refused(
    tmp_path: Path, row: list[str], expected: str
) -> None:
    """Each mistake is named, against its row."""
    with pytest.raises(ParsingError) as exc:
        _read(tmp_path, [row])

    assert "row 2" in str(exc.value)
    assert expected in str(exc.value)


def test_a_spend_is_read_as_a_sale_that_moves_no_cash(tmp_path: Path) -> None:
    """The sale stands where the row stood, with the row's source."""
    rows = _read(tmp_path, [*FUNDING, NETWORK_FEE])

    expanded = expand_to_legs(list(rows))

    assert expanded[:2] == rows[:2]
    (leg,) = expanded[2:]
    assert isinstance(leg, RawTransaction)
    assert (leg.action, leg.symbol, leg.quantity, leg.price, leg.fees, leg.amount) == (
        ActionType.SELL,
        "ETH",
        Decimal("0.002"),
        Decimal(2600),
        Decimal(0),
        Decimal("5.200"),
    )
    assert not leg.affects_cash_balance
    assert leg.source == rows[2].source
    # The row read from the file is left as it was.
    assert rows[2].action is ActionType.SPEND


def test_a_spend_is_a_disposal_at_market_value(tmp_path: Path) -> None:
    """0.002 ETH worth 5.20 leaves the pool at its 5.00 cost."""
    report = _report(tmp_path, [*FUNDING, NETWORK_FEE])

    (fee,) = report.calculation_log[dt.date(2024, 7, 10)]["sell$ETH"]
    assert fee.amount == Decimal("5.2")
    assert fee.allowable_cost == Decimal(5)
    assert report.capital_gain == Decimal("0.2")
    (eth,) = (entry for entry in report.portfolio if entry.symbol == "ETH")
    assert eth.quantity == Decimal("1.998")


def test_a_spend_adds_no_cash_to_the_balance(tmp_path: Path) -> None:
    """Nothing is left after buying, so paying for more ETH goes negative."""
    with pytest.raises(CalculationError, match=r"negative balance of -5\.2"):
        _report(
            tmp_path,
            [
                *FUNDING,
                NETWORK_FEE,
                ["2024-07-10", "BUY", "ETH", "0.002", "2600", "0", "GBP"],
            ],
        )


def test_spending_an_asset_not_held_is_refused(tmp_path: Path) -> None:
    """The sale is checked like any sale, and names the RAW row."""
    with pytest.raises(InvalidTransactionError) as exc:
        _report(tmp_path, [NETWORK_FEE])

    assert "ETH" in str(exc.value)
    assert "row 2" in str(exc.value)


def test_a_transfer_fee_paid_in_an_asset_is_not_cash(tmp_path: Path) -> None:
    """The fee's value is kept, but the amount is the cash moved: none."""
    (row,) = _read(tmp_path, [TRANSFER_FEE], FEE_HEADER)

    assert row.action is ActionType.TRANSFER
    assert (row.fee_symbol, row.fee_quantity) == ("ETH", Decimal("0.002"))
    assert row.fees == Decimal("5.20")
    assert row.amount == Decimal(0)


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        pytest.param(
            ["2024-07-10", "TRANSFER", "ETH", "1", "0", "5", "GBP", "ETH", ""],
            "positive 'fee_quantity'",
            id="fee asset without its units",
        ),
        pytest.param(
            ["2024-07-10", "TRANSFER", "ETH", "1", "0", "0", "GBP", "ETH", "0.002"],
            "fee's market value",
            id="fee asset without its value",
        ),
        pytest.param(
            ["2024-07-10", "TRANSFER", "ETH", "1", "2600", "5", "GBP", "ETH", "0.002"],
            "moves no cash",
            id="asset moved with a price",
        ),
        pytest.param(
            ["2024-07-10", "BUY", "ETH", "1", "2600", "0", "GBP", "ETH", "0.002"],
            "read only on an EXCHANGE row",
            id="fee asset on a buy",
        ),
    ],
)
def test_an_invalid_transfer_fee_is_refused(
    tmp_path: Path, row: list[str], expected: str
) -> None:
    """Each mistake is named, against its row."""
    with pytest.raises(ParsingError) as exc:
        _read(tmp_path, [row], FEE_HEADER)

    assert "row 2" in str(exc.value)
    assert expected in str(exc.value)


def test_a_transfer_with_an_asset_fee_is_read_as_the_transfer_and_a_sale(
    tmp_path: Path,
) -> None:
    """The transfer keeps its cash amount; the fee is a sale moving no cash."""
    (row,) = _read(tmp_path, [TRANSFER_FEE], FEE_HEADER)

    transfer, fee = transfer_legs(row)

    assert (transfer.action, transfer.symbol, transfer.amount, transfer.fees) == (
        ActionType.TRANSFER,
        "ETH",
        Decimal(0),
        Decimal(0),
    )
    assert transfer.fee_symbol is None
    assert (fee.action, fee.symbol, fee.quantity, fee.price, fee.amount) == (
        ActionType.SELL,
        "ETH",
        Decimal("0.002"),
        Decimal(2600),
        Decimal("5.20"),
    )
    assert not fee.affects_cash_balance
    assert fee.source == row.source
    # The row read from the file is left as it was.
    assert row.fee_symbol == "ETH"


def test_a_transfer_fee_paid_in_an_asset_is_a_disposal(tmp_path: Path) -> None:
    """Only the 0.002 ETH fee leaves the pool; the 1.5 ETH moved stays."""
    report = _report(tmp_path, _with_fee_columns([*FUNDING, TRANSFER_FEE]), FEE_HEADER)

    (fee,) = report.calculation_log[dt.date(2024, 7, 10)]["sell$ETH"]
    assert fee.amount == Decimal("5.2")
    assert fee.allowable_cost == Decimal(5)
    assert report.capital_gain == Decimal("0.2")
    (eth,) = (entry for entry in report.portfolio if entry.symbol == "ETH")
    assert eth.quantity == Decimal("1.998")


def test_a_transfer_fee_paid_in_an_asset_adds_no_cash(tmp_path: Path) -> None:
    """Nothing is left after buying, so paying for more ETH goes negative."""
    with pytest.raises(CalculationError, match=r"negative balance of -5\.2"):
        _report(
            tmp_path,
            _with_fee_columns(
                [
                    *FUNDING,
                    TRANSFER_FEE,
                    ["2024-07-10", "BUY", "ETH", "0.002", "2600", "0", "GBP"],
                ]
            ),
            FEE_HEADER,
        )


def test_a_cash_transfer_keeps_its_cash_fee(tmp_path: Path) -> None:
    """Without a fee asset, a TRANSFER's fees still come out of its amount."""
    (row,) = _read(
        tmp_path,
        [["2024-05-01", "TRANSFER", "", "1", "100", "2", "GBP", "", ""]],
        FEE_HEADER,
    )

    assert row.amount == Decimal(98)
    assert expand_to_legs([row]) == [row]
