"""Unit tests on schwab_equity_award_json.py."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal
import io
import json
import logging
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING

import pytest

from cgt_calc.args_parser import create_parser
from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers import schwab_equity_award_json
from tests.utils import build_cmd, report_path, stderr_alerts, tax_fields

if TYPE_CHECKING:
    from cgt_calc.parsers.schwab_equity_award_json import SchwabAwardTransaction

# ruff: noqa: SLF001 "Private member accessed"


def test_decimal_from_str() -> None:
    """Test _decimal_from_str()."""
    assert schwab_equity_award_json._decimal_from_str("$123,456.23") == Decimal(
        "123456.23"
    )


def test_decimal_from_number_or_str_both() -> None:
    """Test _decimal_from_number_or_str() on float."""
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"key": "123.45", "keySortValue": Decimal("67.89")}, "key"
    ) == Decimal("67.89")


def test_decimal_from_number_or_str_float_null() -> None:
    """Test _decimal_from_number_or_str() on None float."""
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"key": "67.89", "keySortValue": None}, "key"
    ) == Decimal("67.89")


def test_decimal_from_number_or_str_none() -> None:
    """Test _decimal_from_number_or_str() with a value of None."""
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"key": None}, "key"
    ) == Decimal(0)


def test_decimal_from_number_or_str_empty_string() -> None:
    """Test _decimal_from_number_or_str() with an empty string value."""
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"key": ""}, "key"
    ) == Decimal(0)


def test_decimal_from_number_or_str_float_custom_suffix() -> None:
    """Test _decimal_from_number_or_str() on float.

    With a custom suffix.
    """
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"keyMySuffix": Decimal("67.89")}, "key", "MySuffix"
    ) == Decimal("67.89")


def test_decimal_from_number_or_str_default() -> None:
    """Test _decimal_from_number_or_str() with absent keys."""
    assert schwab_equity_award_json._decimal_from_number_or_str(
        {"key": "123.45", "keySortValue": 67.89}, "otherkey"
    ) == Decimal(0)


def test_schwab_transaction_v1() -> None:
    """Test read_schwab_equity_award_json_transactions() on v1 data."""
    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            Path("tests/schwab/data/equity_award/schwab_equity_award_v1.json")
        )
    )

    assert transactions[0].date == datetime.date(2022, 4, 25)
    assert transactions[0].action == ActionType.STOCK_ACTIVITY
    assert transactions[0].symbol == "GOOG"
    assert transactions[0].quantity == Decimal("67.2")
    assert transactions[0].price == Decimal("125.6445")
    assert transactions[0].fees == Decimal(0)
    assert transactions[0].currency == "USD"
    assert transactions[0].broker == "Charles Schwab"

    assert transactions[1].date == datetime.date(2022, 6, 14)
    assert transactions[1].action == ActionType.SELL
    assert transactions[1].quantity == Decimal("62.601495")
    assert transactions[1].price == Decimal("113.75")
    assert transactions[1].fees == Decimal("0.17")

    assert transactions[2].date == datetime.date(2022, 10, 25)
    assert transactions[2].action == ActionType.STOCK_ACTIVITY
    assert transactions[2].quantity == Decimal("10.45")
    assert transactions[2].price == Decimal("112.42")
    assert transactions[2].fees == Decimal(0)

    assert transactions[3].date == datetime.date(2022, 11, 16)
    assert transactions[3].action == ActionType.SELL
    assert transactions[3].quantity == Decimal("12.549")
    assert transactions[3].price is not None
    assert transactions[3].price.quantize(Decimal(".000001")).normalize() == Decimal(
        "2051.597737"
    )
    assert transactions[3].amount == Decimal(25745)
    assert transactions[3].fees == Decimal("0.50")


def test_schwab_transaction_v2() -> None:
    """Test read_schwab_equity_award_json_transactions() on v2 data."""
    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            Path("tests/schwab/data/equity_award/schwab_equity_award_v2.json")
        )
    )

    i = 0
    assert transactions[i].date == datetime.date(2023, 4, 25)
    assert transactions[i].action == ActionType.STOCK_ACTIVITY
    assert transactions[i].quantity == Decimal("4.911")
    assert transactions[i].price == Decimal("106.78")
    assert transactions[i].fees == Decimal(0)

    i += 1
    assert transactions[i].date == datetime.date(2023, 4, 25)
    assert transactions[i].action == ActionType.STOCK_ACTIVITY
    assert transactions[i].quantity == Decimal("13.6")
    assert transactions[i].price == Decimal("106.78")
    assert transactions[i].fees == Decimal(0)

    i += 1
    assert transactions[i].date == datetime.date(2023, 8, 31)
    assert transactions[i].action == ActionType.SELL
    assert transactions[i].quantity == Decimal("14.40")
    assert transactions[i].price == Decimal("137.90")
    assert transactions[i].fees == Decimal("0.02")

    i += 1
    assert transactions[i].date == datetime.date(2023, 9, 25)
    assert transactions[i].action == ActionType.STOCK_ACTIVITY
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].quantity == Decimal("4.911")
    assert transactions[i].price == Decimal("131.25")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"

    i += 1
    assert transactions[i].date == datetime.date(2023, 9, 25)
    assert transactions[i].action == ActionType.STOCK_ACTIVITY
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].quantity == Decimal("13.6")
    assert transactions[i].price == Decimal("131.25")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"

    i += 1
    assert transactions[i].date == datetime.date(2024, 6, 17)
    assert transactions[i].action == ActionType.DIVIDEND
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].amount == Decimal("74.62")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"

    i += 1
    assert transactions[i].date == datetime.date(2024, 6, 17)
    assert transactions[i].action == ActionType.DIVIDEND_TAX
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].amount == Decimal("-22.39")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"

    i += 1
    assert transactions[i].date == datetime.date(2024, 7, 12)
    assert transactions[i].action == ActionType.DIVIDEND_TAX
    assert transactions[i].symbol == "GOOG"
    assert transactions[i].amount == Decimal("11.20")
    assert transactions[i].fees == Decimal(0)
    assert transactions[i].currency == "USD"
    assert transactions[i].broker == "Charles Schwab"


def test_schwab_transaction_v2_rounding() -> None:
    """Test read_schwab_equity_award_json_transactions() on v2_rounding data.

    This tests 13 vesting events with 7 shares each, which are then sold.
    """
    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            Path("tests/schwab/data/equity_award/schwab_equity_award_v2_rounding.json")
        )
    )

    assert transactions[0].date == datetime.date(2020, 4, 24)
    assert transactions[0].action == ActionType.SELL
    assert transactions[0].symbol == "GOOG"
    assert transactions[0].quantity == Decimal(91)
    assert transactions[0].price == Decimal("102.4935")
    assert transactions[0].fees == Decimal("0.49")
    assert transactions[0].currency == "USD"
    assert transactions[0].broker == "Charles Schwab"

    num_vested = Decimal(0)
    for transaction_id in range(1, len(transactions)):
        assert transactions[transaction_id].date == (
            datetime.date(2020, 1, 1) + datetime.timedelta(days=transaction_id)
        )
        assert transactions[transaction_id].action == ActionType.STOCK_ACTIVITY
        assert transactions[transaction_id].quantity == Decimal(7)
        num_vested += transactions[transaction_id].quantity or Decimal(0)
        assert transactions[transaction_id].price == Decimal("123.45")
        assert transactions[transaction_id].fees == Decimal(0)
    assert num_vested == transactions[0].quantity


def test_split_multiplier_google_boundary() -> None:
    """GOOG counts are in current units from the day after the split."""
    assert (
        schwab_equity_award_json.split_multiplier("GOOG", datetime.date(2022, 7, 15))
        == 20
    )
    assert (
        schwab_equity_award_json.split_multiplier("GOOG", datetime.date(2022, 7, 16))
        == 1
    )


def test_split_multiplier_ignores_unknown_symbol() -> None:
    """A symbol with no split history is left alone rather than guessed at."""
    assert (
        schwab_equity_award_json.split_multiplier("AAPL", datetime.date(2014, 6, 8))
        == 1
    )
    assert (
        schwab_equity_award_json.split_multiplier(None, datetime.date(2022, 1, 1)) == 1
    )


def test_split_history_demands_a_price_floor_it_can_use() -> None:
    """An UNRELIABLE export is unusable without the price that gives units away."""
    with pytest.raises(ValueError, match="presplit_price_floor"):
        schwab_equity_award_json.SplitHistory(
            ((datetime.date(2022, 7, 16), 20),),
            schwab_equity_award_json.Restatement.UNRELIABLE,
        )

    with pytest.raises(ValueError, match="presplit_price_floor"):
        schwab_equity_award_json.SplitHistory(
            ((datetime.date(2021, 7, 20), 4),),
            schwab_equity_award_json.Restatement.ACQUISITIONS_ONLY,
            presplit_price_floor=Decimal(175),
        )


@pytest.mark.parametrize(
    ("label", "action"),
    [
        ("Buy", ActionType.BUY),
        ("Sale", ActionType.SELL),
        ("Sell", ActionType.SELL),
        ("Forced Quick Sell", ActionType.SELL),
        ("Forced Disbursement", ActionType.TRANSFER),
        ("Deposit", ActionType.STOCK_ACTIVITY),
        ("Qualified Dividend", ActionType.DIVIDEND),
        ("Cash Dividend", ActionType.DIVIDEND),
        ("Dividend", ActionType.DIVIDEND),
        ("MoneyLink Transfer", ActionType.TRANSFER),
        ("Cash In Lieu", ActionType.TRANSFER),
        ("Stock Plan Activity", ActionType.STOCK_ACTIVITY),
        ("NRA Tax Adj", ActionType.DIVIDEND_TAX),
        ("NRA Withholding", ActionType.DIVIDEND_TAX),
        ("NRA Withhold", ActionType.DIVIDEND_TAX),
        ("Foreign Tax Paid", ActionType.DIVIDEND_TAX),
        ("Tax Reversal", ActionType.DIVIDEND_TAX),
        ("Tax Withholding", ActionType.DIVIDEND_TAX),
        ("ADR Mgmt Fee", ActionType.FEE),
        ("Adjustment", ActionType.ADJUSTMENT),
        ("IRS Withhold Adj", ActionType.ADJUSTMENT),
        ("Short Term Cap Gain", ActionType.CAPITAL_GAIN),
        ("Long Term Cap Gain", ActionType.CAPITAL_GAIN),
        ("Spin-off", ActionType.SPIN_OFF),
        ("Credit Interest", ActionType.INTEREST),
        ("Reinvest Shares", ActionType.REINVEST_SHARES),
        ("Reinvest Dividend", ActionType.REINVEST_DIVIDENDS),
        ("Wire Funds Received", ActionType.WIRE_FUNDS_RECEIVED),
        ("Gift", ActionType.UNCLASSIFIED_GIFT),
    ],
)
def test_action_from_str(label: str, action: ActionType) -> None:
    """Map every known Schwab equity award action label."""
    assert (
        schwab_equity_award_json.action_from_str(label, Path("awards.json")) == action
    )


def test_action_from_str_unknown() -> None:
    """Raise on unknown action labels."""
    with pytest.raises(ParsingError, match="Unknown action"):
        schwab_equity_award_json.action_from_str("Dance", Path("awards.json"))


def _read_json(
    content: str,
) -> list[SchwabAwardTransaction]:
    """Parse Schwab equity award JSON from a string."""
    parser = schwab_equity_award_json.SchwabEquityAwardsJSONParser
    return parser.read_transactions(io.StringIO(content), Path("awards.json"))


def test_read_transactions_invalid_json() -> None:
    """Raise on malformed JSON."""
    with pytest.raises(ParsingError, match="Could not parse content as JSON"):
        _read_json("{not json")


def test_read_transactions_unknown_top_level_field() -> None:
    """Raise when no known top level field is present."""
    with pytest.raises(ParsingError, match="Expected top level field"):
        _read_json('{"Unknown": []}')


def test_read_transactions_transactions_not_a_list() -> None:
    """Raise when the transactions field is not a list."""
    with pytest.raises(ParsingError, match="is not a list"):
        _read_json('{"Transactions": {}}')


def test_unknown_symbol_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Warn about symbols without known split history."""
    content = (
        '{"Transactions": [{"Date": "01/15/2023", "Action": "Dividend",'
        ' "Symbol": "ZZZ", "Description": "Div", "Quantity": null,'
        ' "Amount": "$10.00", "FeesAndCommissions": null,'
        ' "TransactionDetails": []}]}'
    )

    with caplog.at_level(logging.WARNING):
        transactions = _read_json(content)

    assert "check the share counts" in caplog.text
    assert transactions[0].action == ActionType.DIVIDEND
    assert transactions[0].amount == Decimal(10)


def test_forced_disbursement_parses_as_transfer() -> None:
    """Parse the Forced Disbursement sample from issue #488."""
    content = (
        '{"Transactions": [{"Date": "03/31/2015", "Action": "Forced Disbursement",'
        ' "Symbol": "GOOG", "Quantity": null, "Description": "Debit",'
        ' "FeesAndCommissions": null, "DisbursementElection": null,'
        ' "Amount": "-$2,490.13", "TransactionDetails": []}]}'
    )

    transactions = _read_json(content)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.TRANSFER
    assert transaction.amount == Decimal("-2490.13")
    assert transaction.date == datetime.date(2015, 3, 31)
    assert transaction.quantity is None or transaction.quantity == 0


def test_gift_parses_as_gift() -> None:
    """Parse a gift of shares: quantity moves, no money does."""
    content = (
        '{"Transactions": [{"Date": "11/20/2022", "Action": "Gift",'
        ' "Symbol": "GOOG", "Quantity": "2", "Description": "Share Transfer",'
        ' "FeesAndCommissions": null, "Amount": null,'
        ' "TransactionDetails": []}]}'
    )

    transactions = _read_json(content)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.action == ActionType.UNCLASSIFIED_GIFT
    assert transaction.symbol == "GOOG"
    assert transaction.quantity == Decimal(2)
    assert transaction.date == datetime.date(2022, 11, 20)
    assert transaction.price is None


def test_gift_before_a_split_is_restated_like_a_sale() -> None:
    """Shares leave in the units of their own day, gift or sale alike.

    NVDA exports restate acquisitions and nothing else, so a 2022 gift is
    printed in pre-split units and has to be converted like a disposal.
    """
    content = (
        '{"Transactions": [{"Date": "11/20/2022", "Action": "Gift",'
        ' "Symbol": "NVDA", "Quantity": "2", "Description": "Share Transfer",'
        ' "FeesAndCommissions": null, "Amount": null,'
        ' "TransactionDetails": []}]}'
    )

    transactions = _read_json(content)

    # 4:1 in 2021 is already reflected; the 10:1 of June 2024 is not.
    assert transactions[0].quantity == Decimal(20)


def test_ambiguous_gift_before_a_split_records_both_readings() -> None:
    """Where only the price can date the units, a gift has nothing to go on.

    GOOG exports restate some records and not others, and a gift carries no
    price, so the count could be either and guessing is wrong by the whole
    multiplier. Both readings are kept for the calculator to put to the user.
    """
    content = (
        '{"Transactions": [{"Date": "05/02/2022", "Action": "Gift",'
        ' "Symbol": "GOOG", "Quantity": "2", "Description": "Share Transfer",'
        ' "FeesAndCommissions": null, "Amount": null,'
        ' "TransactionDetails": []}]}'
    )

    transaction = _read_json(content)[0]

    assert transaction.quantity == Decimal(2)
    assert transaction.ambiguous_quantity == Decimal(40)


def test_gift_after_every_split_is_left_alone() -> None:
    """Nothing to convert once the counts are already current."""
    content = (
        '{"Transactions": [{"Date": "11/20/2024", "Action": "Gift",'
        ' "Symbol": "GOOG", "Quantity": "2", "Description": "Share Transfer",'
        ' "FeesAndCommissions": null, "Amount": null,'
        ' "TransactionDetails": []}]}'
    )

    transaction = _read_json(content)[0]

    assert transaction.quantity == Decimal(2)
    # Nothing to be unsure about once the counts are current.
    assert transaction.ambiguous_quantity is None


def test_gift_with_money_raises() -> None:
    """A gift row carrying money is not the row this parser assumes."""
    content = (
        '{"Transactions": [{"Date": "11/20/2022", "Action": "Gift",'
        ' "Symbol": "GOOG", "Quantity": "2", "Description": "Share Transfer",'
        ' "FeesAndCommissions": null, "Amount": "$100.00",'
        ' "TransactionDetails": []}]}'
    )

    with pytest.raises(ParsingError, match="Unexpected amount or fees"):
        _read_json(content)


def test_v2_sale_quantity_from_lot_shares() -> None:
    """Sum fractional lot Shares for a v2 sale whose lots have different prices.

    Regression test: the lot membership check used the lowercase v1 key
    "shares", so v2 sales never took the share-sum path and fell through to
    the sale-price path, which raises when lots disagree on price.
    """
    content = (
        '{"Transactions": [{"Date": "08/31/2023", "Action": "Sale",'
        ' "Symbol": "GOOG", "Quantity": "10", "Description": "Share Sale",'
        ' "FeesAndCommissions": "$0.00", "Amount": "$1,045.00",'
        ' "TransactionDetails": ['
        '{"Details": {"Shares": "5.5", "SalePrice": "$100.00"}},'
        '{"Details": {"Shares": "4.5", "SalePrice": "$110.00"}}'
        "]}]}"
    )

    transactions = _read_json(content)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction.quantity == Decimal(10)
    assert transaction.price == Decimal("104.5")


def test_unimplemented_action_raises() -> None:
    """Raise on actions the parser does not implement."""
    content = (
        '{"Transactions": [{"Date": "01/15/2023", "Action": "Buy",'
        ' "Symbol": "GOOG", "Description": "Buy", "Quantity": "1",'
        ' "Amount": "$10.00", "FeesAndCommissions": null,'
        ' "TransactionDetails": []}]}'
    )

    with pytest.raises(ParsingError, match="is not implemented"):
        _read_json(content)


def test_detail_counts_multiplier() -> None:
    """Compose the ratios of every split after the given date."""
    multiplier = schwab_equity_award_json._detail_counts_multiplier

    # NVDA restates acquisitions only and split 4:1 on 2021-07-20
    # and 10:1 on 2024-06-10.
    assert multiplier("NVDA", datetime.date(2021, 7, 19)) == 40
    assert multiplier("NVDA", datetime.date(2021, 7, 20)) == 10
    assert multiplier("NVDA", datetime.date(2024, 6, 10)) == 1
    # GOOG's export does not restate uniformly, so counts inside the
    # details are already comparable and need no multiplier.
    assert multiplier("GOOG", datetime.date(2020, 1, 1)) == 1


def test_lot_shares_and_price_helpers() -> None:
    """Lot helpers return None when lots are incomplete or disagree."""
    names = schwab_equity_award_json.FieldNames(2)

    no_shares = {"TransactionDetails": [{"Details": {"SalePrice": "$10.00"}}]}
    assert schwab_equity_award_json._lot_share_sum(no_shares, names) is None

    no_price = {"TransactionDetails": [{"Details": {"Shares": "1.5"}}]}
    assert schwab_equity_award_json._lot_price(no_price, names) is None

    different_prices = {
        "TransactionDetails": [
            {"Details": {"SalePrice": "$10.00"}},
            {"Details": {"SalePrice": "$11.00"}},
        ]
    }
    assert schwab_equity_award_json._lot_price(different_prices, names) is None


def test_schwab_json_invalid_json(tmp_path: Path) -> None:
    """Ensure malformed JSON raises ParsingError."""
    json_path = tmp_path / "invalid.json"
    json_path.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(ParsingError, match="Could not parse content as JSON"):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_missing_top_level_key(tmp_path: Path) -> None:
    """Ensure JSON missing expected top-level keys raises ParsingError."""
    json_path = tmp_path / "missing_keys.json"
    json_path.write_text(json.dumps({"someOtherKey": []}), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=r"Expected top level field \(transactions, Transactions\) not found",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_transactions_not_a_list(tmp_path: Path) -> None:
    """Ensure non-list Transactions field raises ParsingError."""
    json_path = tmp_path / "not_a_list.json"
    json_path.write_text(json.dumps({"Transactions": "not-a-list"}), encoding="utf-8")

    with pytest.raises(ParsingError, match=r"'Transactions' is not a list"):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_buy_transaction_fails(tmp_path: Path) -> None:
    """Ensure loading a json with a buy transaction fails as unsupported (we expect Deposit)."""
    json_path = tmp_path / "buy_transaction.json"
    data = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "Buy",
                "Symbol": "GOOG",
                "Description": "Bought shares",
                "Quantity": "10",
                "Amount": "",
                "FeesAndCommissions": "",
                "TransactionDetails": [],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError, match=r"Parsing for action Buy is not implemented!"
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_unknown_action_fails(tmp_path: Path) -> None:
    """Ensure unknown actions in JSON raise ParsingError."""
    json_path = tmp_path / "unknown_action.json"
    data: dict[str, object] = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "UnknownAction",
                "Symbol": "GOOG",
                "Description": "Unknown",
                "Quantity": "10",
                "Amount": "$100.00",
                "FeesAndCommissions": None,
                "TransactionDetails": [],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ParsingError, match=r"Unknown action: UnknownAction"):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_deposit_missing_details_fails(tmp_path: Path) -> None:
    """Deposit transaction with missing TransactionDetails raises ParsingError."""
    json_path = tmp_path / "deposit_no_details.json"
    data: dict[str, object] = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "10",
                "Amount": None,
                "FeesAndCommissions": "",
                "TransactionDetails": [],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=r"Expected a single Transaction Details for a Deposit, but found 0",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_deposit_multiple_details_fails(tmp_path: Path) -> None:
    """Deposit transaction with multiple TransactionDetails raises ParsingError."""
    json_path = tmp_path / "deposit_multi_details.json"
    detail = {
        "Details": {
            "AwardDate": "01/01/2020",
            "AwardId": "C123",
            "VestDate": "01/01/2023",
            "VestFairMarketValue": "$100.00",
        }
    }
    data = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "10",
                "Amount": None,
                "FeesAndCommissions": None,
                "TransactionDetails": [detail, detail],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=r"Expected a single Transaction Details for a Deposit, but found 2",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_deposit_empty_and_null_amount(tmp_path: Path) -> None:
    """Deposit transaction amount is calculated when Amount is empty string or null."""
    json_path = tmp_path / "deposit_empty_amount.json"
    data = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "10",
                "Amount": "",
                "FeesAndCommissions": "",
                "TransactionDetails": [
                    {
                        "Details": {
                            "AwardDate": "01/01/2020",
                            "AwardId": "C123",
                            "VestDate": "01/01/2023",
                            "VestFairMarketValue": "$150.00",
                        }
                    }
                ],
            },
            {
                "Date": "02/01/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "5",
                "Amount": None,
                "FeesAndCommissions": None,
                "TransactionDetails": [
                    {
                        "Details": {
                            "AwardDate": "01/01/2020",
                            "AwardId": "C124",
                            "VestDate": "02/01/2023",
                            "VestFairMarketValue": "$200.00",
                        }
                    }
                ],
            },
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )
    )

    assert len(transactions) == 2
    # Note: load_from_file reverses the transactions list
    assert transactions[0].date == datetime.date(2023, 2, 1)
    assert transactions[0].amount == Decimal(1000)
    assert transactions[0].fees == Decimal(0)
    assert transactions[1].date == datetime.date(2023, 1, 1)
    assert transactions[1].amount == Decimal(1500)
    assert transactions[1].fees == Decimal(0)


@pytest.mark.parametrize(
    (
        "quantity_str",
        "amount_str",
        "fees_str",
        "transac_details",
        "expected_quantity",
        "expected_price",
    ),
    [
        pytest.param(
            "12.549",  # quantity_str
            "$1,254.40",  # amount_str
            "$0.50",  # fees_str
            [{"Details": {"SalePrice": "$100.00"}}],
            Decimal("12.549"),  # expected_quantity
            Decimal(100),  # expected_price
            id="non_integer_quantity",
        ),
        pytest.param(
            "12",  # quantity_str
            "$1,254.40",  # amount_str
            "$0.50",  # fees_str
            [
                {"Details": {"Shares": "3.549", "SalePrice": "$100.00"}},
                {"Details": {"Shares": "9", "SalePrice": "$100.00"}},
            ],
            Decimal("12.549"),  # expected_quantity
            Decimal(100),  # expected_price
            id="integer_quantity_with_fractional_subtransaction_shares",
        ),
        pytest.param(
            "12",  # quantity_str
            "$1,200.00",  # amount_str
            "",  # fees_str
            [
                {"Details": {"Shares": "3", "SalePrice": "$100.00"}},
                {"Details": {"Shares": "9", "SalePrice": "$100.00"}},
            ],
            Decimal(12),  # expected_quantity
            Decimal(100),  # expected_price
            id="integer_quantity_with_integer_subtransaction_shares_exact",
        ),
        pytest.param(
            "12",  # quantity_str
            "$1,254.90",  # amount_str
            "",  # fees_str
            [
                {"Details": {"Shares": "3", "SalePrice": "$100.00"}},
                {"Details": {"Shares": "9", "SalePrice": "$100.00"}},
            ],
            Decimal("12.549"),  # expected_quantity
            Decimal(100),  # expected_price
            id="integer_quantity_with_integer_subtransaction_shares_truncated",
        ),
        pytest.param(
            "10",  # quantity_str
            "$1,000.00",  # amount_str
            "",  # fees_str
            [{"Details": {"SalePrice": "$100.00"}}],
            Decimal(10),  # expected_quantity
            Decimal(100),  # expected_price
            id="integer_quantity_without_subtransaction_shares_exact",
        ),
        pytest.param(
            "10",  # quantity_str
            "$1,054.90",  # amount_str
            "",  # fees_str
            [{"Details": {"SalePrice": "$100.00"}}],
            Decimal("10.549"),  # expected_quantity
            Decimal(100),  # expected_price
            id="integer_quantity_without_subtransaction_shares_truncated",
        ),
    ],
)
def test_schwab_json_sale_variations(
    tmp_path: Path,
    quantity_str: str,
    amount_str: str,
    fees_str: str,
    transac_details: list[dict[str, object]],
    expected_quantity: Decimal,
    expected_price: Decimal,
) -> None:
    """Test Sale parsing variations across integer/non-integer quantities and subtransaction shares."""
    json_path = tmp_path / "sale_variation.json"
    data: dict[str, object] = {
        "Transactions": [
            {
                "Date": "08/31/2023",
                "Action": "Sale",
                "Symbol": "GOOG",
                "Quantity": quantity_str,
                "Description": "Share Sale",
                "FeesAndCommissions": fees_str,
                "Amount": amount_str,
                "TransactionDetails": transac_details,
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )
    )

    assert len(transactions) == 1
    assert transactions[0].action == ActionType.SELL
    assert transactions[0].symbol == "GOOG"
    assert transactions[0].quantity == expected_quantity
    assert transactions[0].price == expected_price


def test_schwab_json_sale_missing_details_fails(tmp_path: Path) -> None:
    """Sale transaction with missing TransactionDetails raises ParsingError."""
    json_path = tmp_path / "sale_no_details.json"
    data: dict[str, object] = {
        "Transactions": [
            {
                "Date": "08/31/2023",
                "Action": "Sale",
                "Symbol": "GOOG",
                "Description": "Share Sale",
                "Quantity": "10",
                "Amount": "$1,000.00",
                "FeesAndCommissions": "",
                "TransactionDetails": [],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ParsingError, match=r"Expected transaction details for Sale"):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_deposit_empty_vest_price_raises(tmp_path: Path) -> None:
    """Deposit with empty amount and empty VestFairMarketValue raises ParsingError."""
    json_path = tmp_path / "deposit_empty_price.json"
    data = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "10",
                "Amount": "",
                "FeesAndCommissions": "",
                "TransactionDetails": [
                    {
                        "Details": {
                            "AwardDate": "01/01/2020",
                            "AwardId": "C123",
                            "VestDate": "01/01/2023",
                            "VestFairMarketValue": "",
                        }
                    }
                ],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=r"Missing or empty VestFairMarketValue for Deposit on 2023-01-01",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_sale_differing_subtransac_prices_fails(
    tmp_path: Path,
) -> None:
    """Sale with differing sub-transaction prices raises ParsingError."""
    json_path = tmp_path / "sale_differing_prices.json"
    data = {
        "Transactions": [
            {
                "Date": "08/31/2023",
                "Action": "Sale",
                "Symbol": "GOOG",
                "Quantity": "20",
                "Description": "Share Sale",
                "FeesAndCommissions": "$0.02",
                "Amount": "$2,000.00",
                "TransactionDetails": [
                    {
                        "Details": {
                            "SalePrice": "$100.00",
                        }
                    },
                    {
                        "Details": {
                            "SalePrice": "$105.00",
                        }
                    },
                ],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=r"lots sold at different prices \(\$100\.00, \$105\.00\)",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_sale_differing_prices_with_whole_lot_shares_fails(
    tmp_path: Path,
) -> None:
    """Whole share counts on the lots do not rescue a multi-price sale.

    Schwab rounds the per-lot counts the same way it rounds the row's own
    Quantity, so 3 + 9 there is as consistent with 12.549 shares as the row's
    own "12" is. Neither the counts nor the money can settle it, so the sale
    has to be refused rather than pooled at a guess.
    """
    json_path = tmp_path / "sale_differing_prices_with_shares.json"
    data = {
        "Transactions": [
            {
                "Date": "08/31/2023",
                "Action": "Sale",
                "Symbol": "GOOG",
                "Quantity": "12",
                "Description": "Share Sale",
                "FeesAndCommissions": "",
                "Amount": "$1,245.00",
                "TransactionDetails": [
                    {"Details": {"Shares": "3", "SalePrice": "$100.00"}},
                    {"Details": {"Shares": "9", "SalePrice": "$105.00"}},
                ],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=r"lots sold at different prices \(\$100\.00, \$105\.00\)",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_sale_lot_without_sale_price_fails(tmp_path: Path) -> None:
    """A lot that states no SalePrice is reported as that, not as a price clash."""
    json_path = tmp_path / "sale_lot_without_price.json"
    data = {
        "Transactions": [
            {
                "Date": "08/31/2023",
                "Action": "Sale",
                "Symbol": "GOOG",
                "Quantity": "20",
                "Description": "Share Sale",
                "FeesAndCommissions": "",
                "Amount": "$2,000.00",
                "TransactionDetails": [
                    {"Details": {"SalePrice": "$100.00"}},
                    {"Details": {}},
                ],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError, match=r"Missing or empty SalePrice for Sale on 2023-08-31"
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_sale_zero_price_fails(tmp_path: Path) -> None:
    """A zero SalePrice is refused rather than dividing by it."""
    json_path = tmp_path / "sale_zero_price.json"
    data = {
        "Transactions": [
            {
                "Date": "08/31/2023",
                "Action": "Sale",
                "Symbol": "GOOG",
                "Quantity": "20",
                "Description": "Share Sale",
                "FeesAndCommissions": "",
                "Amount": "$2,000.00",
                "TransactionDetails": [{"Details": {"SalePrice": "$0.00"}}],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ParsingError, match=r"states a SalePrice of '\$0\.00'"):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


@pytest.mark.parametrize(
    ("qty", "saleprice", "fees_and_commissions", "amount", "expected_fees"),
    [
        ("10", "$100.00", "", "$1,000.00", Decimal(0)),
        ("10", "$100.00", None, "$1,000.00", Decimal(0)),
        ("10", "$100.00", "$1.50", "$998.50", Decimal("1.50")),
    ],
)
def test_schwab_json_sale_fees_variations(
    tmp_path: Path,
    qty: str,
    saleprice: str,
    fees_and_commissions: str | None,
    amount: str,
    expected_fees: Decimal,
) -> None:
    """Test sale parsing with empty string, null, and populated fees."""
    json_path = tmp_path / "sale_fees.json"
    data = {
        "Transactions": [
            {
                "Date": "08/31/2023",
                "Action": "Sale",
                "Symbol": "GOOG",
                "Quantity": qty,
                "Description": "Share Sale",
                "FeesAndCommissions": fees_and_commissions,
                "Amount": amount,
                "TransactionDetails": [
                    {
                        "Details": {
                            "SalePrice": saleprice,
                        }
                    }
                ],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )
    )

    assert len(transactions) == 1
    assert transactions[0].action == ActionType.SELL
    assert transactions[0].fees == expected_fees


def test_schwab_json_dividend_empty_amount_fails(tmp_path: Path) -> None:
    """Dividend transaction with empty Amount raises ParsingError."""
    json_path = tmp_path / "dividend_empty_amount.json"
    data = {
        "Transactions": [
            {
                "Date": "06/17/2024",
                "Action": "Dividend",
                "Symbol": "GOOG",
                "Description": "Credit",
                "FeesAndCommissions": "",
                "Amount": "",
                "TransactionDetails": [],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=r"Missing or empty Amount for Dividend on 2024-06-17",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def _fractional_sale(details: list[dict[str, object]]) -> dict[str, object]:
    """Build a Sale whose own Quantity already carries the decimals."""
    return {
        "Transactions": [
            {
                "Date": "08/31/2023",
                "Action": "Sale",
                "Symbol": "GOOG",
                "Quantity": "12.549",
                "Description": "Share Sale",
                "FeesAndCommissions": "$0.50",
                "Amount": "$1,254.40",
                "TransactionDetails": details,
            }
        ]
    }


def test_schwab_json_fractional_sale_without_details_fails(tmp_path: Path) -> None:
    """A fractional Quantity does not excuse a Sale from stating its lots."""
    json_path = tmp_path / "fractional_sale_no_details.json"
    json_path.write_text(json.dumps(_fractional_sale([])), encoding="utf-8")

    with pytest.raises(ParsingError, match=r"Expected transaction details for Sale"):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_fractional_sale_zero_lot_price_fails(tmp_path: Path) -> None:
    """A $0.00 lot price is refused even where the price comes from the money."""
    json_path = tmp_path / "fractional_sale_zero_price.json"
    json_path.write_text(
        json.dumps(_fractional_sale([{"Details": {"SalePrice": "$0.00"}}])),
        encoding="utf-8",
    )

    with pytest.raises(ParsingError, match=r"states a SalePrice of '\$0\.00'"):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_fractional_sale_negative_lot_shares_fails(
    tmp_path: Path,
) -> None:
    """A negative lot count is refused even where the row's own count is used."""
    json_path = tmp_path / "fractional_sale_negative_shares.json"
    json_path.write_text(
        json.dumps(
            _fractional_sale(
                [
                    {"Details": {"Shares": "13.549", "SalePrice": "$100.00"}},
                    {"Details": {"Shares": "-1", "SalePrice": "$100.00"}},
                ]
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ParsingError, match=r"cannot dispose of a negative number of shares"
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_fractional_sale_keeps_differing_lot_prices(
    tmp_path: Path,
) -> None:
    """Lots at different prices are fine once the row states the count itself.

    Real Schwab exports do this — the v1 sample's 12.549-share sale spans
    $2,051.60 and $2,051.54 lots — so the refusal that the inference branch
    needs must not reach this one.
    """
    json_path = tmp_path / "fractional_sale_differing_prices.json"
    json_path.write_text(
        json.dumps(
            _fractional_sale(
                [
                    {"Details": {"Shares": "3.549", "SalePrice": "$100.00"}},
                    {"Details": {"Shares": "9", "SalePrice": "$105.00"}},
                ]
            )
        ),
        encoding="utf-8",
    )

    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )
    )

    assert len(transactions) == 1
    assert transactions[0].quantity == Decimal("12.549")
    assert transactions[0].price == Decimal(100)


def test_schwab_json_sale_zero_lot_shares_are_allowed(tmp_path: Path) -> None:
    """A lot stating no shares at all is ordinary, and keeps the sale parsing."""
    json_path = tmp_path / "sale_zero_lot_shares.json"
    data = {
        "Transactions": [
            {
                "Date": "08/31/2023",
                "Action": "Sale",
                "Symbol": "GOOG",
                "Quantity": "12",
                "Description": "Share Sale",
                "FeesAndCommissions": "",
                "Amount": "$1,254.90",
                "TransactionDetails": [
                    {"Details": {"Shares": "0", "SalePrice": "$100.00"}},
                    {"Details": {"Shares": "12.549", "SalePrice": "$100.00"}},
                ],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )
    )

    assert len(transactions) == 1
    assert transactions[0].quantity == Decimal("12.549")


@pytest.mark.parametrize("printed", ["NaN", "Infinity", "-Infinity"])
def test_schwab_json_sale_non_finite_lot_price_fails(
    tmp_path: Path, printed: str
) -> None:
    """A NaN or an Infinity in a lot price is refused like any other nonsense.

    Decimal accepts both. A NaN would raise out of the positivity check that
    follows, and an Infinity would pass it and reach the arithmetic.
    """
    json_path = tmp_path / "sale_non_finite_price.json"
    json_path.write_text(
        json.dumps(_fractional_sale([{"Details": {"SalePrice": printed}}])),
        encoding="utf-8",
    )

    with pytest.raises(
        ParsingError, match=rf"Invalid SalePrice '{printed}' for Sale on 2023-08-31"
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_sale_non_finite_lot_shares_fails(tmp_path: Path) -> None:
    """A NaN share count is refused rather than poisoning the sum."""
    json_path = tmp_path / "sale_non_finite_shares.json"
    json_path.write_text(
        json.dumps(
            _fractional_sale([{"Details": {"Shares": "NaN", "SalePrice": "$100.00"}}])
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ParsingError, match=r"Invalid Shares 'NaN' for Sale on 2023-08-31"
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


@pytest.mark.parametrize("printed", ["NaN", "Infinity"])
def test_schwab_json_non_finite_vest_price_fails(tmp_path: Path, printed: str) -> None:
    """A NaN or an Infinity vest market value is refused."""
    json_path = tmp_path / "deposit_non_finite_vest_price.json"
    data = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "10",
                "Amount": "",
                "FeesAndCommissions": "",
                "TransactionDetails": [
                    {
                        "Details": {
                            "AwardDate": "01/01/2020",
                            "AwardId": "C123",
                            "VestDate": "01/01/2023",
                            "VestFairMarketValue": printed,
                        }
                    }
                ],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=rf"Invalid VestFairMarketValue '{printed}' for Deposit on 2023-01-01",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_lot_field_that_is_not_a_number_fails(tmp_path: Path) -> None:
    """A lot field holding something that is not a number at all is refused."""
    json_path = tmp_path / "sale_lot_not_a_number.json"
    json_path.write_text(
        json.dumps(
            _fractional_sale([{"Details": {"Shares": "abc", "SalePrice": "$100.00"}}])
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ParsingError, match=r"Invalid Shares 'abc' for Sale on 2023-08-31"
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_row_field_that_is_not_a_number_names_the_string(
    tmp_path: Path,
) -> None:
    """A row's own field is named from the string where it has no number field."""
    json_path = tmp_path / "sale_quantity_not_a_number.json"
    json_path.write_text(
        json.dumps(_fractional_sale([{"Details": {"SalePrice": "$100.00"}}])).replace(
            '"Quantity": "12.549"', '"Quantity": "abc"'
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ParsingError, match=r"Invalid Quantity 'abc' for Sale on 08/31/2023"
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_non_finite_number_field_fails(tmp_path: Path) -> None:
    """The NaN literal Python's JSON reader allows is refused in a number field."""
    json_path = tmp_path / "sale_non_finite_number_field.json"
    json_path.write_text(
        json.dumps(_fractional_sale([{"Details": {"SalePrice": "$100.00"}}])).replace(
            '"Quantity": "12.549"', '"QuantitySortValue": NaN'
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ParsingError, match=r"Invalid Quantity 'nan' for Sale on 08/31/2023"
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_deposit_zero_vest_price_fails(tmp_path: Path) -> None:
    """A vest at $0.00 is refused rather than pooled at no cost."""
    json_path = tmp_path / "deposit_zero_vest_price.json"
    data = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "10",
                "Amount": "",
                "FeesAndCommissions": "",
                "TransactionDetails": [
                    {
                        "Details": {
                            "AwardDate": "01/01/2020",
                            "AwardId": "C123",
                            "VestDate": "01/01/2023",
                            "VestFairMarketValue": "$0.00",
                        }
                    }
                ],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=r"states a VestFairMarketValue of '\$0\.00'",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_deposit_negative_vest_price_fails(tmp_path: Path) -> None:
    """A negative vest market value is refused too."""
    json_path = tmp_path / "deposit_negative_vest_price.json"
    data = {
        "Transactions": [
            {
                "Date": "01/01/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "10",
                "Amount": "",
                "FeesAndCommissions": "",
                "TransactionDetails": [
                    {
                        "Details": {
                            "AwardDate": "01/01/2020",
                            "AwardId": "C123",
                            "VestDate": "01/01/2023",
                            "VestFairMarketValue": "-$131.25",
                        }
                    }
                ],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ParsingError,
        match=r"states a VestFairMarketValue of '-\$131\.25'",
    ):
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )


def test_schwab_json_dividend_amount_from_number_field(tmp_path: Path) -> None:
    """A dividend priced only in the number field parses from it."""
    json_path = tmp_path / "dividend_sort_value.json"
    data = {
        "Transactions": [
            {
                "Date": "06/17/2024",
                "Action": "Dividend",
                "Symbol": "GOOG",
                "Description": "Credit",
                "FeesAndCommissions": "",
                "Amount": "",
                "AmountSortValue": 74.62,
                "TransactionDetails": [],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )
    )

    assert len(transactions) == 1
    assert transactions[0].amount == Decimal("74.62")


def test_schwab_json_skipped_actions(tmp_path: Path) -> None:
    """Journal and Wire Transfer transactions are skipped during parsing."""
    json_path = tmp_path / "skipped_actions.json"
    data = {
        "Transactions": [
            {
                "Date": "09/01/2023",
                "Action": "Journal",
                "Symbol": "GOOG",
                "Quantity": None,
                "Description": "Journal To Account",
                "FeesAndCommissions": "",
                "Amount": "-$1,000.00",
                "TransactionDetails": [],
            },
            {
                "Date": "09/02/2023",
                "Action": "Wire Transfer",
                "Symbol": "GOOG",
                "Quantity": None,
                "Description": "Wire Transfer",
                "FeesAndCommissions": None,
                "Amount": "-$500.00",
                "TransactionDetails": [],
            },
            {
                "Date": "09/25/2023",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Quantity": "10",
                "Description": "RS",
                "FeesAndCommissions": "",
                "Amount": None,
                "TransactionDetails": [
                    {
                        "Details": {
                            "AwardDate": "01/01/2020",
                            "AwardId": "C123",
                            "VestDate": "09/25/2023",
                            "VestFairMarketValue": "$130.00",
                        }
                    }
                ],
            },
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )
    )

    assert len(transactions) == 1
    assert transactions[0].action == ActionType.STOCK_ACTIVITY


def test_schwab_json_v1_empty_fees(tmp_path: Path) -> None:
    """Test schema v1 with empty string and null fees."""
    json_path = tmp_path / "v1_empty_fees.json"
    data = {
        "transactions": [
            {
                "eventDate": "06/14/2022",
                "action": "Sale",
                "symbol": "GOOG",
                "quantity": "10",
                "description": "Sale",
                "totalCommissionsAndFees": "",
                "amount": "$1,000.00",
                "transactionDetails": [
                    {
                        "salePrice": "$100.00",
                    }
                ],
            }
        ]
    }
    json_path.write_text(json.dumps(data), encoding="utf-8")

    transactions = (
        schwab_equity_award_json.SchwabEquityAwardsJSONParser().load_from_file(
            json_path
        )
    )

    assert len(transactions) == 1
    assert transactions[0].fees == Decimal(0)
    assert transactions[0].action == ActionType.SELL


@pytest.mark.parametrize("extension", ["json", "csv"])
def test_run_with_schwab_equity_award_json(
    request: pytest.FixtureRequest, extension: str
) -> None:
    """Run cgt-calc end-to-end on both layouts of one Equity Awards history."""
    input_file_base = "schwab_equity_award_v2"
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-equity-award-json",
        f"tests/schwab/data/equity_award/{input_file_base}.{extension}",
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    if result.returncode:
        pytest.fail(
            "Integration test failed\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    assert stderr_alerts(result.stderr) == [], (
        f"Run with example file {input_file_base}.{extension} generated errors"
    )
    expected_file = (
        Path("tests")
        / "schwab"
        / "data"
        / "equity_award"
        / f"{input_file_base}-expected_output.txt"
    )
    expected = expected_file.read_text(encoding="utf-8")
    cmd_str = " ".join([param or "''" for param in cmd])
    assert result.stdout == expected, (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{cmd_str} > {expected_file}"
    )


COMPLETE_CSV_HEADER = [
    "Date",
    "Action",
    "Symbol",
    "Description",
    "Quantity",
    "FeesAndCommissions",
    "DisbursementElection",
    "Amount",
    "Type",
    "Shares",
    "SalePrice",
    "SubscriptionDate",
    "SubscriptionFairMarketValue",
    "PurchaseDate",
    "PurchasePrice",
    "PurchaseFairMarketValue",
    "DispositionType",
    "GrantId",
    "VestDate",
    "VestFairMarketValue",
    "GrossProceeds",
    "AwardDate",
    "AwardId",
    "SharesSoldWithheldForTaxes",
    "NetSharesDeposited",
]

EQUITY_AWARD_DATA = Path("tests/schwab/data/equity_award")


def _csv_text(rows: list[dict[str, str]]) -> str:
    """Return a complete Equity Awards CSV holding the given rows."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=COMPLETE_CSV_HEADER, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _parse(
    content: str, file_path: Path = Path("awards.csv")
) -> list[SchwabAwardTransaction]:
    """Read transactions from export content of either layout."""
    return schwab_equity_award_json.SchwabEquityAwardsParser.read_transactions(
        io.StringIO(content), file_path
    )


A_SALE = {
    "Date": "01/02/2026",
    "Action": "Sale",
    "Symbol": "GOOG",
    "Description": "Share Sale",
    "Quantity": "1",
    "Amount": "$10.00",
}
ITS_LOT = {"Shares": "1", "SalePrice": "$10.00"}


@pytest.mark.parametrize(
    ("basename", "suffix"),
    [("schwab_equity_award_v2.csv", ".json"), ("schwab_equity_award_v2.json", ".csv")],
)
def test_read_transactions_ignores_the_path_suffix(basename: str, suffix: str) -> None:
    """The contents choose the parser, so a misnamed file still reads."""
    content = (EQUITY_AWARD_DATA / basename).read_text(encoding="utf-8")

    assert _parse(content, Path(f"awards{suffix}"))


@pytest.mark.parametrize(
    "basename",
    ["schwab_equity_award_v2", "schwab_equity_award_v2_rounding"],
)
def test_complete_csv_matches_json(basename: str) -> None:
    """The two layouts of one history produce the same tax figures."""
    parser = schwab_equity_award_json.SchwabEquityAwardsParser

    json_rows = parser.load_from_file(EQUITY_AWARD_DATA / f"{basename}.json")
    csv_rows = parser.load_from_file(EQUITY_AWARD_DATA / f"{basename}.csv")

    assert [tax_fields(row) for row in csv_rows] == [
        tax_fields(row) for row in json_rows
    ]


def test_legacy_price_csv_is_refused() -> None:
    """The price-only CSV supplements a main history and imports nothing."""
    content = Path("tests/schwab/data/rsu_settlement/awards.csv").read_text(
        encoding="utf-8"
    )

    with pytest.raises(ParsingError, match="award-price file"):
        _parse(content)


def test_csv_detail_row_before_any_transaction_is_rejected() -> None:
    """A detail row has nothing to attach to until a parent row opens one."""
    content = _csv_text([ITS_LOT])

    with pytest.raises(ParsingError, match="detail row before") as exc_info:
        _parse(content)

    assert exc_info.value.row_index == 2


def test_csv_parent_row_holding_details_is_rejected() -> None:
    """One row cannot be both a transaction and one of its own lots."""
    content = _csv_text([A_SALE | ITS_LOT])

    with pytest.raises(ParsingError, match="also holds detail") as exc_info:
        _parse(content)

    assert exc_info.value.row_index == 2


def test_csv_parent_row_without_an_action_is_rejected() -> None:
    """A row with some parent fields and no Action is malformed, not a detail."""
    content = _csv_text([{"Symbol": "GOOG", "Quantity": "1"}])

    with pytest.raises(ParsingError, match="Date and Action") as exc_info:
        _parse(content)

    assert exc_info.value.row_index == 2


def test_csv_row_of_the_wrong_width_is_rejected() -> None:
    """A short row cannot be zipped onto the header without inventing values."""
    content = _csv_text([A_SALE, ITS_LOT]).splitlines()
    content[2] = content[2].rsplit(",", 1)[0]

    with pytest.raises(ParsingError, match="columns") as exc_info:
        _parse("\n".join(content) + "\n")

    assert exc_info.value.row_index == 3


def test_csv_blank_shares_leaves_the_parent_quantity_alone() -> None:
    """A blank detail field is absent, not a zero that eats the disposal."""
    content = _csv_text(
        [
            A_SALE | {"Quantity": "10", "Amount": "$100.00"},
            {"SalePrice": "$10.00"},
        ]
    )

    assert _parse(content)[0].quantity == Decimal(10)


def test_csv_identical_transactions_stay_distinct() -> None:
    """Two rows describing the same trade twice are two trades."""
    content = _csv_text([A_SALE, ITS_LOT, dict(A_SALE), dict(ITS_LOT)])

    assert len(_parse(content)) == 2


def test_csv_malformed_detail_names_its_parent_row() -> None:
    """A bad detail is reported against the transaction it belongs to."""
    content = _csv_text([A_SALE, ITS_LOT | {"SalePrice": "not a price"}])

    with pytest.raises(ParsingError) as exc_info:
        _parse(content)

    assert exc_info.value.row_index == 2
    assert "awards.csv" in str(exc_info.value)


def test_csv_example_export_reads_its_dividend_tax_deposits_and_sale() -> None:
    """The sanitised Equity Award Center export reads end to end."""
    transactions = schwab_equity_award_json.SchwabEquityAwardsParser.load_from_file(
        EQUITY_AWARD_DATA / "schwab_equity_award_v2.example.csv"
    )

    # Oldest first, and Journal and Wire Transfer are dropped as internal.
    assert [row.action for row in transactions] == [
        ActionType.DIVIDEND,
        ActionType.DIVIDEND_TAX,
        ActionType.STOCK_ACTIVITY,
        ActionType.STOCK_ACTIVITY,
        ActionType.SELL,
    ]
    assert transactions[0].amount == Decimal("3.25")
    assert transactions[2].date == datetime.date(2025, 12, 25)
    assert transactions[2].price == Decimal("315.67")
    assert transactions[4].quantity == Decimal("13.302")


def test_csv_deposit_missing_its_vest_date_names_the_row() -> None:
    """A required detail that is absent is reported, not raised as a KeyError."""
    content = _csv_text(
        [
            {
                "Date": "12/30/2025",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "10",
            },
            {"VestFairMarketValue": "$315.67", "AwardDate": "03/05/2025"},
        ]
    )

    with pytest.raises(ParsingError) as exc_info:
        _parse(content)

    assert exc_info.value.row_index == 2
    assert "The Deposit of GOOG on 12/30/2025 states no VestDate" in str(exc_info.value)


def test_csv_deposit_missing_its_award_id_names_the_row() -> None:
    """A detail with no guard of its own is still answered with its row."""
    content = _csv_text(
        [
            {
                "Date": "12/30/2025",
                "Action": "Deposit",
                "Symbol": "GOOG",
                "Description": "RS",
                "Quantity": "10",
            },
            {
                "VestDate": "12/25/2025",
                "VestFairMarketValue": "$315.67",
                "AwardDate": "03/05/2025",
            },
        ]
    )

    with pytest.raises(ParsingError) as exc_info:
        _parse(content)

    assert exc_info.value.row_index == 2
    assert "AwardId is missing from this transaction" in str(exc_info.value)


def test_csv_blank_lines_between_transactions_are_ignored() -> None:
    """Exports padded with empty rows still read."""
    content = _csv_text([A_SALE, ITS_LOT]).splitlines()
    padded = [content[0], "", content[1], "," * 22, content[2], ""]

    assert len(_parse("\n".join(padded) + "\n")) == 1


NVDA_LAPSE = {
    "Date": "09/15/2021",
    "Action": "Lapse",
    "Symbol": "NVDA",
    "Description": "Restricted Stock Lapse",
    "Quantity": "600",
}
# 20 withheld plus 40 deposited, at the 10:1 split still to come on
# 2024-06-10, is the 600 the lapse states. The check only bites once that
# identity is broken.
ITS_COUNTS = {
    "SharesSoldWithheldForTaxes": "20",
    "NetSharesDeposited": "40",
    "AwardDate": "08/10/2020",
    "AwardId": "000001",
}


def test_csv_lapse_agreeing_with_the_split_table_is_accepted() -> None:
    """The baseline the next test mutates has to reconcile to begin with."""
    assert _parse(_csv_text([NVDA_LAPSE, ITS_COUNTS])) == []


def test_csv_lapse_disagreeing_with_the_split_table_names_its_row() -> None:
    """The split-table check runs before conversion and still names the row."""
    content = _csv_text([NVDA_LAPSE | {"Quantity": "599"}, ITS_COUNTS])

    with pytest.raises(ParsingError, match="split table") as exc_info:
        _parse(content)

    assert exc_info.value.row_index == 2


def test_csv_lapse_with_an_unreadable_date_names_its_row() -> None:
    """A date the split check cannot read is reported, not raised raw."""
    content = _csv_text([NVDA_LAPSE | {"Date": "31/31/2021"}, ITS_COUNTS])

    with pytest.raises(ParsingError) as exc_info:
        _parse(content)

    assert exc_info.value.row_index == 2
    assert "31/31/2021" in str(exc_info.value)


def test_csv_transactions_keep_the_row_they_were_stated_on() -> None:
    """Provenance survives a successful parse, not only a failing one."""
    transactions = schwab_equity_award_json.SchwabEquityAwardsParser.load_from_file(
        EQUITY_AWARD_DATA / "schwab_equity_award_v2.example.csv"
    )

    # Oldest first, so the rows count down the file: Dividend is its row 11.
    assert [row.source.row for row in transactions if row.source] == [11, 10, 8, 6, 3]


def test_csv_with_a_byte_order_mark_still_reads() -> None:
    """Excel writes a BOM, and it must not hide the layout."""
    content = "\ufeff\n  " + _csv_text([A_SALE, ITS_LOT])

    assert len(_parse(content)) == 1


def test_the_option_help_names_both_layouts() -> None:
    """The option is still --schwab-equity-award-json and says it takes CSV."""
    help_text = create_parser().format_help()

    assert "--schwab-equity-award-json" in help_text
    assert "--schwab-equity-award-file" not in help_text
    assert "JSON or complete CSV" in " ".join(help_text.split())


def test_the_option_help_points_at_the_canonical_one() -> None:
    """It stays registered, and its help says which option to prefer."""
    help_text = " ".join(create_parser().format_help().split())

    assert "Prefer --schwab-award-file" in help_text
    # And why it is still here: the combination the canonical option refuses.
    assert "combine the history with --schwab-file or --schwab-dir" in help_text
