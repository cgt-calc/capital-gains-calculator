"""Test Revolut crypto support."""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from cgt_calc.exceptions import ParsingError, UnsupportedActionError
from cgt_calc.model import ActionType
from cgt_calc.parsers.base_parsers import skipping_unsupported_rows
from cgt_calc.parsers.revolut_crypto import RevolutCryptoColumn, RevolutCryptoParser
from tests.utils import build_cmd, run_cli

STATEMENT = Path("tests") / "revolut_crypto" / "data" / "statement.csv"

BASE_ROW = {
    RevolutCryptoColumn.SYMBOL: "BTC",
    RevolutCryptoColumn.TYPE: "Buy",
    RevolutCryptoColumn.QUANTITY: "0.08",
    RevolutCryptoColumn.PRICE: "$25,000.00",
    RevolutCryptoColumn.VALUE: "$2,000.00",
    RevolutCryptoColumn.FEES: "$25.00",
    RevolutCryptoColumn.DATE: "3 Mar 2023, 10:00:00",
}


def _write_csv(tmp_path: Path, *overrides: dict[RevolutCryptoColumn, str]) -> Path:
    """Write a statement with one row per override of the base row."""
    target = tmp_path / "crypto.csv"
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([column.value for column in RevolutCryptoColumn])
        for override in overrides or ({},):
            row = BASE_ROW | override
            writer.writerow([row[column] for column in RevolutCryptoColumn])
    return target


def test_a_purchase_costs_the_value_and_the_fee(tmp_path: Path) -> None:
    """The fee is charged on top of the coins' value, and adds to their cost."""
    (transaction,) = RevolutCryptoParser().load_from_file(_write_csv(tmp_path))

    assert transaction.action is ActionType.BUY
    assert transaction.symbol == "CRYPTO:BTC"
    assert transaction.currency == "USD"
    assert transaction.quantity == Decimal("0.08")
    assert transaction.price == Decimal(25000)
    assert transaction.fees == Decimal("25.00")
    assert transaction.amount == Decimal("-2025.00")
    assert transaction.broker == "Revolut Crypto"


def test_a_sale_brings_in_the_value_less_the_fee(tmp_path: Path) -> None:
    """A row in pounds keeps its own currency."""
    path = _write_csv(
        tmp_path,
        {
            RevolutCryptoColumn.TYPE: "Sell",
            RevolutCryptoColumn.QUANTITY: "0.2",
            RevolutCryptoColumn.VALUE: "£6,000.00",
            RevolutCryptoColumn.FEES: "£60.00",
        },
    )

    (transaction,) = RevolutCryptoParser().load_from_file(path)

    assert transaction.action is ActionType.SELL
    assert transaction.currency == "GBP"
    assert transaction.amount == Decimal("5940.00")


def test_no_cash_balance_is_kept(tmp_path: Path) -> None:
    """The statement has no top-ups: the money moves in the current account."""
    (transaction,) = RevolutCryptoParser().load_from_file(_write_csv(tmp_path))

    assert transaction.affects_cash_balance is False


def test_the_time_is_read_as_uk_time(tmp_path: Path) -> None:
    """Half past eleven at night in summer is the same UK day, 22:30 in UTC."""
    path = _write_csv(tmp_path, {RevolutCryptoColumn.DATE: "5 Apr 2024, 23:30:00"})

    (transaction,) = RevolutCryptoParser().load_from_file(path)

    assert transaction.date == date(2024, 4, 5)
    assert transaction.source is not None
    assert transaction.source.timestamp == datetime(2024, 4, 5, 22, 30, tzinfo=UTC)


def test_a_send_is_refused_with_its_row(tmp_path: Path) -> None:
    """A Send may be a disposal or a move to the user's own wallet."""
    path = _write_csv(tmp_path, {}, {RevolutCryptoColumn.TYPE: "Send"})

    with pytest.raises(UnsupportedActionError, match="row 3: Unsupported action: Send"):
        RevolutCryptoParser().load_from_file(path)


def test_a_send_can_be_skipped(tmp_path: Path) -> None:
    """--skip-unsupported-rows leaves the Send out and keeps the purchase."""
    path = _write_csv(tmp_path, {}, {RevolutCryptoColumn.TYPE: "Send"})

    with skipping_unsupported_rows(enabled=True):
        transactions = RevolutCryptoParser().load_from_file(path)

    assert [t.action for t in transactions] == [ActionType.BUY]


@pytest.mark.parametrize(
    ("override", "message"),
    [
        pytest.param(
            {RevolutCryptoColumn.VALUE: "2000"},
            "Expected a \\$, £ or € amount in column 'Value'",
            id="no currency",
        ),
        pytest.param(
            {RevolutCryptoColumn.FEES: "£25.00"},
            "Fees are in GBP but the value is in USD",
            id="fee in another currency",
        ),
        pytest.param(
            {RevolutCryptoColumn.QUANTITY: "0"},
            "Quantity must be positive",
            id="zero quantity",
        ),
        pytest.param(
            {RevolutCryptoColumn.DATE: "2022-10-11"},
            "Invalid date",
            id="other date format",
        ),
    ],
)
def test_a_malformed_row_is_refused(
    tmp_path: Path, override: dict[RevolutCryptoColumn, str], message: str
) -> None:
    """A row that cannot be read stops the run with its row number."""
    path = _write_csv(tmp_path, override)

    with pytest.raises(ParsingError, match=f"row 2: {message}"):
        RevolutCryptoParser().load_from_file(path)


def test_a_missing_fee_is_zero(tmp_path: Path) -> None:
    """An empty Fees cell is a row with no fee, not a malformed one."""
    path = _write_csv(tmp_path, {RevolutCryptoColumn.FEES: ""})

    (transaction,) = RevolutCryptoParser().load_from_file(path)

    assert transaction.fees == 0
    assert transaction.amount == Decimal(-2000)


def test_a_full_run_pools_each_coin(tmp_path: Path) -> None:
    """Half the pooled bitcoin is sold, so half its pooled cost is allowed.

    The two purchases cost £22,200 with their fees, so the half sold costs
    £11,100, and the £150 fee on the sale makes the allowable costs £11,250.
    No balance check is turned off: the statement keeps no cash balance.
    """
    result = run_cli(
        build_cmd(
            "--year",
            "2023",
            "--revolut-crypto-file",
            str(STATEMENT),
            "--skip-unsupported-rows",
            "--no-report",
        )
    )

    assert "Disposal proceeds:  £15,000.00" in result.stdout
    assert "Allowable costs:    £11,250.00" in result.stdout
    assert "Gain:                £3,750.00" in result.stdout
    assert "CRYPTO:ETH: 2.00" in result.stdout
    assert "row 5: Unsupported action: Send" in result.stderr
