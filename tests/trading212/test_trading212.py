"""Tests for the Trading 212 parser."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import ActionType, BrokerTransaction
from cgt_calc.parsers.trading212 import (
    Trading212Column,
    Trading212Parser,
    Trading212Transaction,
    datetime_from_str,
)
from tests.general.test_calc import create_calculator
from tests.utils import (
    assert_stdout_matches,
    build_cmd,
    report_path,
    run_cli,
    stderr_alerts,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


HEADER_2020 = [
    "Action",
    "Time",
    "ISIN",
    "Ticker",
    "Name",
    "No. of shares",
    "Price / share",
    "Currency (Price / share)",
    "Exchange rate",
    "Result (GBP)",
    "Total (GBP)",
    "Withholding tax",
    "Currency (Withholding tax)",
    "Charge amount (GBP)",
    "Transaction fee (GBP)",
    "Finra fee",
    "Currency (Finra fee)",
    "Notes",
    "ID",
]


HEADER_2024 = [
    "Action",
    "Time",
    "ISIN",
    "Ticker",
    "Name",
    "No. of shares",
    "Price / share",
    "Currency (Price / share)",
    "Exchange rate",
    "Result",
    "Currency (Result)",
    "Total",
    "Currency (Total)",
    "Withholding tax",
    "Currency (Withholding tax)",
    "Transaction fee",
    "Notes",
    "ID",
    "Currency conversion fee",
    "Currency (Currency conversion fee)",
    "Currency (Transaction fee)",
]

HEADER_2026 = [
    "Action",
    "Time",
    "ISIN",
    "Ticker",
    "Name",
    "Notes",
    "ID",
    "No. of shares",
    "Price / share",
    "Currency (Price / share)",
    "Exchange rate",
    "Result",
    "Currency (Result)",
    "Total",
    "Currency (Total)",
    "Withholding tax",
    "Currency (Withholding tax)",
    "Stamp duty reserve tax",
    "Currency (Stamp duty reserve tax)",
    "Transaction fee",
    "Finra fee",
    "Currency conversion from amount",
    "Currency (Currency conversion from amount)",
    "Currency conversion to amount",
    "Currency (Currency conversion to amount)",
    "Currency conversion fee",
    "Currency (Currency conversion fee)",
    "Currency (Transaction fee)",
    "Currency (Finra fee)",
    "Merchant name",
    "Merchant category",
]

# Trading 212 renamed the Time column to spell out the zone.
HEADER_2026_UTC = [
    "Time (UTC)" if column == "Time" else column for column in HEADER_2026
]


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    path.write_text("\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8")


def _make_row(
    header: list[str], overrides: Mapping[str | Trading212Column, str]
) -> list[str]:
    row = dict.fromkeys(header, "")
    for column, value in overrides.items():
        column_name = column.value if isinstance(column, Trading212Column) else column
        row[column_name] = value
    return [row[column_name] for column_name in header]


def _prepare_file(tmp_path: Path, rows: list[list[str]]) -> Path:
    folder = tmp_path / "inputs"
    folder.mkdir()
    csv_file = folder / "trading212.csv"
    _write_csv(csv_file, rows)
    return folder


def test_load_from_dir_accepts_uppercase_csv_extension(tmp_path: Path) -> None:
    """Discover a Trading 212 export whose CSV extension is uppercase."""
    rows = [
        HEADER_2024,
        _make_row(
            HEADER_2024,
            {
                Trading212Column.ACTION: "Deposit",
                Trading212Column.TIME: "2024-01-01 00:15:20.149",
                Trading212Column.TOTAL: "100.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "uppercase-extension",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)
    (folder / "trading212.csv").rename(folder / "trading212.CSV")

    transactions = Trading212Parser.load_from_dir(folder)

    assert len(transactions) == 1
    assert transactions[0].amount == Decimal("100.00")


def test_read_trading212_transactions_supports_2020_export(tmp_path: Path) -> None:
    """Parse transactions using the legacy 2020 column set."""

    rows = [
        HEADER_2020,
        _make_row(
            HEADER_2020,
            {
                Trading212Column.ACTION: "Deposit",
                Trading212Column.TIME: "2020-06-24 04:06:06",
                Trading212Column.TOTAL_GBP: "5000.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "deposit-1",
            },
        ),
        _make_row(
            HEADER_2020,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2020-06-24 14:33:50",
                Trading212Column.ISIN: "US0000000010",
                Trading212Column.TICKER: "FOO",
                Trading212Column.NAME: "Foo Inc",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "10.50",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL_GBP: "16.60",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "buy-1",
            },
        ),
        _make_row(
            HEADER_2020,
            {
                Trading212Column.ACTION: "Dividend (Ordinary)",
                Trading212Column.TIME: "2021-04-02 10:15:07",
                Trading212Column.ISIN: "US0000000002",
                Trading212Column.TICKER: "BAR",
                Trading212Column.NAME: "Bar Inc",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "0.20",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "Not available",
                Trading212Column.TOTAL_GBP: "0.40",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "dividend-1",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    assert [transaction.action for transaction in transactions] == [
        ActionType.TRANSFER,
        ActionType.BUY,
        ActionType.DIVIDEND,
    ]

    deposit = transactions[0]
    assert deposit.amount == Decimal("5000.00")
    assert deposit.currency == "GBP"

    buy = transactions[1]
    assert isinstance(buy, Trading212Transaction)
    assert buy.symbol == "FOO"
    assert buy.quantity == Decimal(2)
    assert buy.amount == Decimal("-16.60")
    assert buy.currency == "GBP"
    assert buy.price == Decimal("8.30")
    assert buy.price_foreign == Decimal("10.50")
    assert buy.exchange_rate == Decimal("1.25")

    dividend = transactions[2]
    assert isinstance(dividend, Trading212Transaction)
    assert dividend.amount == Decimal("0.40")
    assert dividend.currency == "GBP"


def test_read_trading212_transactions_supports_2024_export(tmp_path: Path) -> None:
    """Parse transactions using the modern 2024 column set."""

    rows = [
        HEADER_2024,
        _make_row(
            HEADER_2024,
            {
                Trading212Column.ACTION: "Deposit",
                Trading212Column.TIME: "2024-01-01 00:15:20.149",
                Trading212Column.TOTAL: "3000.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "deposit-2",
            },
        ),
        _make_row(
            HEADER_2024,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2024-01-01 16:10:05.175",
                Trading212Column.ISIN: "US0000000036",
                Trading212Column.TICKER: "BAZ",
                Trading212Column.NAME: "Baz Corp",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "10.50",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "16.60",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_FEE: "0.20",
                Trading212Column.CURRENCY_TRANSACTION_FEE: "GBP",
                Trading212Column.TRANSACTION_ID: "buy-2",
            },
        ),
        _make_row(
            HEADER_2024,
            {
                Trading212Column.ACTION: "Dividend (Dividend)",
                Trading212Column.TIME: "2024-03-23 18:35:26",
                Trading212Column.ISIN: "US0000000036",
                Trading212Column.TICKER: "BAZ",
                Trading212Column.NAME: "Baz Corp",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "0.05",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "Not available",
                Trading212Column.TOTAL: "0.40",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.WITHHOLDING_TAX: "0.05",
                Trading212Column.CURRENCY_WITHHOLDING_TAX: "USD",
                Trading212Column.TRANSACTION_ID: "dividend-2",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    assert [transaction.action for transaction in transactions] == [
        ActionType.TRANSFER,
        ActionType.BUY,
        ActionType.DIVIDEND,
    ]

    deposit = transactions[0]
    assert deposit.amount == Decimal("3000.00")
    assert deposit.currency == "GBP"

    buy = transactions[1]
    assert isinstance(buy, Trading212Transaction)
    assert buy.amount == Decimal("-16.60")
    assert buy.transaction_fee == Decimal("0.20")
    assert buy.currency == "GBP"
    assert buy.price_foreign == Decimal("10.50")
    assert buy.exchange_rate == Decimal("1.25")

    dividend = transactions[2]
    assert isinstance(dividend, Trading212Transaction)
    assert dividend.amount == Decimal("0.40")
    assert dividend.currency == "GBP"
    assert dividend.transaction_fee == Decimal(0)


def test_read_trading212_transactions_supports_2026_export(tmp_path: Path) -> None:
    """Parse transactions using the newer 2026 column set."""

    rows = [
        HEADER_2026,
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Deposit",
                Trading212Column.TIME: "2025-04-07 09:15:20.149",
                Trading212Column.TOTAL: "1000.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "deposit-3",
            },
        ),
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2025-04-08 10:10:05.175",
                Trading212Column.ISIN: "US0000000044",
                Trading212Column.TICKER: "QUX",
                Trading212Column.NAME: "Qux Ltd",
                Trading212Column.NO_OF_SHARES: "4",
                Trading212Column.PRICE_PER_SHARE: "12.50",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "41.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.STAMP_DUTY_RESERVE_TAX: "0.50",
                Trading212Column.CURRENCY_STAMP_DUTY_RESERVE_TAX: "GBP",
                Trading212Column.CURRENCY_CONVERSION_FROM_AMOUNT: "50.00",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_FROM_AMOUNT: "USD",
                Trading212Column.CURRENCY_CONVERSION_TO_AMOUNT: "41.00",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_TO_AMOUNT: "GBP",
                Trading212Column.CURRENCY_CONVERSION_FEE: "1.00",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_FEE: "GBP",
                Trading212Column.TRANSACTION_ID: "buy-3",
            },
        ),
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Market sell",
                Trading212Column.TIME: "2025-04-08 15:05:00",
                Trading212Column.ISIN: "US0000000044",
                Trading212Column.TICKER: "QUX",
                Trading212Column.NAME: "Qux Ltd",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "10.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "16.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "sell-1",
            },
        ),
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Card debit",
                Trading212Column.TIME: "2025-04-09 12:00:00",
                Trading212Column.TOTAL: "20.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.NOTES: "Latte",
                Trading212Column.MERCHANT_NAME: "Cafe 212",
                Trading212Column.MERCHANT_CATEGORY: "Food & Drink",
                Trading212Column.TRANSACTION_ID: "debit-1",
            },
        ),
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Currency conversion",
                Trading212Column.TIME: "2025-04-09 14:00:00",
                Trading212Column.TOTAL: "-0.10",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.CURRENCY_CONVERSION_FROM_AMOUNT: "10.00",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_FROM_AMOUNT: "USD",
                Trading212Column.CURRENCY_CONVERSION_TO_AMOUNT: "8.00",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_TO_AMOUNT: "GBP",
                Trading212Column.CURRENCY_CONVERSION_FEE: "0.10",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_FEE: "GBP",
                Trading212Column.TRANSACTION_ID: "conversion-1",
            },
        ),
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Card refund",
                Trading212Column.TIME: "2025-04-10 09:30:00",
                Trading212Column.TOTAL: "5.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.NOTES: "Latte refund",
                Trading212Column.MERCHANT_NAME: "Cafe 212",
                Trading212Column.MERCHANT_CATEGORY: "Food & Drink",
                Trading212Column.TRANSACTION_ID: "refund-1",
            },
        ),
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Spending cashback",
                Trading212Column.TIME: "2025-04-11 08:00:00",
                Trading212Column.TOTAL: "0.50",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.NOTES: "Cashback",
                Trading212Column.TRANSACTION_ID: "cashback-1",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    assert [transaction.action for transaction in transactions] == [
        ActionType.TRANSFER,
        ActionType.BUY,
        ActionType.SELL,
        ActionType.TRANSFER,
        ActionType.ADJUSTMENT,
        ActionType.TRANSFER,
        ActionType.ADJUSTMENT,
    ]

    deposit = transactions[0]
    assert deposit.action == ActionType.TRANSFER
    assert deposit.amount == Decimal("1000.00")
    assert deposit.currency == "GBP"

    buy = transactions[1]
    assert isinstance(buy, Trading212Transaction)
    assert buy.action == ActionType.BUY
    assert buy.amount == Decimal("-41.00")
    assert buy.currency == "GBP"
    assert buy.conversion_fee == Decimal("1.00")
    assert buy.price_foreign == Decimal("12.50")
    assert buy.exchange_rate == Decimal("1.25")
    assert buy.stamp_duty == Decimal("0.50")

    sell = transactions[2]
    assert isinstance(sell, Trading212Transaction)
    assert sell.action == ActionType.SELL
    assert sell.amount == Decimal("16.00")
    assert sell.currency == "GBP"
    assert sell.price_foreign == Decimal("10.00")
    assert sell.exchange_rate == Decimal("1.25")

    debit = transactions[3]
    assert isinstance(debit, Trading212Transaction)
    assert debit.action == ActionType.TRANSFER
    assert debit.amount == Decimal("20.00")
    assert debit.currency == "GBP"
    assert debit.notes == "Latte"

    conversion = transactions[4]
    assert isinstance(conversion, Trading212Transaction)
    assert conversion.action == ActionType.ADJUSTMENT
    assert conversion.amount == Decimal("-0.10")
    assert conversion.currency == "GBP"
    assert conversion.conversion_fee == Decimal("0.10")

    refund = transactions[5]
    assert isinstance(refund, Trading212Transaction)
    assert refund.action == ActionType.TRANSFER
    assert refund.amount == Decimal("5.00")
    assert refund.currency == "GBP"
    assert refund.notes == "Latte refund"

    cashback = transactions[6]
    assert isinstance(cashback, Trading212Transaction)
    assert cashback.action == ActionType.ADJUSTMENT
    assert cashback.amount == Decimal("0.50")
    assert cashback.currency == "GBP"
    assert cashback.notes == "Cashback"


HEADER_2025_BARE = [
    "Action",
    "Time",
    "ISIN",
    "Ticker",
    "Name",
    "Notes",
    "ID",
    "No. of shares",
    "Price / share",
    "Currency (Price / share)",
    "Exchange rate",
    "Result",
    "Currency (Result)",
    "Total",
    "Currency (Total)",
    "Withholding tax",
    "Currency (Withholding tax)",
    "Charge amount",
    "Currency (Charge amount)",
    "Deposit fee",
    "Currency (Deposit fee)",
    "Stamp duty",
    "Currency (Stamp duty)",
    "Stamp duty reserve tax",
    "Currency (Stamp duty reserve tax)",
    "Currency conversion fee",
    "Currency (Currency conversion fee)",
    "French transaction tax",
    "Currency (French transaction tax)",
]


def test_read_trading212_transactions_supports_2025_bare_columns(
    tmp_path: Path,
) -> None:
    """Parse exports with bare tax columns and 2025 action labels."""

    rows = [
        HEADER_2025_BARE,
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Deposit",
                Trading212Column.TIME: "2025-04-01 08:00:00",
                Trading212Column.TOTAL: "1000.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.DEPOSIT_FEE: "0.70",
                Trading212Column.CURRENCY_DEPOSIT_FEE: "GBP",
                Trading212Column.TRANSACTION_ID: "deposit-1",
            },
        ),
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2025-04-02 10:30:00",
                Trading212Column.ISIN: "GB0000000017",
                Trading212Column.TICKER: "WID",
                Trading212Column.NAME: "Widget plc",
                Trading212Column.NO_OF_SHARES: "50",
                Trading212Column.PRICE_PER_SHARE: "6.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "GBP",
                Trading212Column.TOTAL: "301.50",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.STAMP_DUTY: "1.50",
                Trading212Column.CURRENCY_STAMP_DUTY: "GBP",
                Trading212Column.TRANSACTION_ID: "buy-1",
            },
        ),
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2025-04-03 11:00:00",
                Trading212Column.ISIN: "FR0000000002",
                Trading212Column.TICKER: "FRA",
                Trading212Column.NAME: "Frenchie SA",
                Trading212Column.NO_OF_SHARES: "10",
                Trading212Column.PRICE_PER_SHARE: "20.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "EUR",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "161.36",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.FRENCH_TRANSACTION_TAX: "0.51",
                Trading212Column.CURRENCY_FRENCH_TRANSACTION_TAX: "GBP",
                Trading212Column.CURRENCY_CONVERSION_FEE: "0.85",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_FEE: "GBP",
                Trading212Column.TRANSACTION_ID: "buy-2",
            },
        ),
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Card credit",
                Trading212Column.TIME: "2025-04-04 09:00:00",
                Trading212Column.TOTAL: "25.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "card-credit-1",
            },
        ),
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Dividend (Interest)",
                Trading212Column.TIME: "2025-04-05 12:00:00",
                Trading212Column.ISIN: "IE0000000038",
                Trading212Column.TICKER: "BOND",
                Trading212Column.NAME: "Bond Fund",
                Trading212Column.TOTAL: "0.08",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "interest-1",
            },
        ),
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Dividend (Property income distribution)",
                Trading212Column.TIME: "2025-04-06 12:00:00",
                Trading212Column.ISIN: "GB0000000041",
                Trading212Column.TICKER: "REIT",
                Trading212Column.NAME: "Property Trust",
                Trading212Column.TOTAL: "3.20",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "pid-1",
            },
        ),
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Dividend adjustment",
                Trading212Column.TIME: "2025-04-07 12:00:00",
                Trading212Column.ISIN: "GB0000000041",
                Trading212Column.TICKER: "REIT",
                Trading212Column.NAME: "Property Trust",
                Trading212Column.TOTAL: "-0.02",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "div-adj-1",
            },
        ),
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Spin off",
                Trading212Column.TIME: "2025-04-08 06:00:00",
                Trading212Column.ISIN: "US0000000051",
                Trading212Column.TICKER: "NEWCO",
                Trading212Column.NAME: "NewCo Inc",
                Trading212Column.NO_OF_SHARES: "5",
                Trading212Column.TOTAL: "0.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "spin-off-1",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    assert [transaction.action for transaction in transactions] == [
        ActionType.TRANSFER,
        ActionType.BUY,
        ActionType.BUY,
        ActionType.TRANSFER,
        ActionType.INTEREST,
        ActionType.DIVIDEND,
        ActionType.DIVIDEND,
        ActionType.SPIN_OFF,
    ]

    deposit = transactions[0]
    assert deposit.action == ActionType.TRANSFER
    assert deposit.amount == Decimal("1000.00")

    buy = transactions[1]
    assert isinstance(buy, Trading212Transaction)
    assert buy.amount == Decimal("-301.50")
    assert buy.stamp_duty == Decimal("1.50")
    assert buy.fees == Decimal("1.50")
    assert buy.price == Decimal("6.00")

    buy_french = transactions[2]
    assert isinstance(buy_french, Trading212Transaction)
    assert buy_french.amount == Decimal("-161.36")
    assert buy_french.stamp_duty == Decimal("0.51")
    assert buy_french.conversion_fee == Decimal("0.85")
    assert buy_french.fees == Decimal("1.36")
    assert buy_french.price == Decimal("16.00")

    card_credit = transactions[3]
    assert card_credit.action == ActionType.TRANSFER
    assert card_credit.amount == Decimal("25.00")

    interest = transactions[4]
    assert interest.action == ActionType.INTEREST
    assert interest.amount == Decimal("0.08")
    assert interest.symbol == "BOND"

    pid = transactions[5]
    assert pid.action == ActionType.DIVIDEND
    assert pid.amount == Decimal("3.20")
    assert pid.symbol == "REIT"

    dividend_adjustment = transactions[6]
    assert dividend_adjustment.action == ActionType.DIVIDEND
    assert dividend_adjustment.amount == Decimal("-0.02")

    spin_off = transactions[7]
    assert spin_off.action == ActionType.SPIN_OFF
    assert spin_off.symbol == "NEWCO"
    assert spin_off.quantity == Decimal(5)


def test_read_trading212_transactions_non_gbp_french_tax(tmp_path: Path) -> None:
    """Store non-GBP French transaction tax in foreign_fees."""

    rows = [
        HEADER_2025_BARE,
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2025-04-03 11:00:00",
                Trading212Column.ISIN: "FR0000000002",
                Trading212Column.TICKER: "FRA",
                Trading212Column.NAME: "Frenchie SA",
                Trading212Column.NO_OF_SHARES: "10",
                Trading212Column.PRICE_PER_SHARE: "20.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "EUR",
                Trading212Column.TOTAL: "170.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.FRENCH_TRANSACTION_TAX: "0.60",
                Trading212Column.CURRENCY_FRENCH_TRANSACTION_TAX: "EUR",
                Trading212Column.TRANSACTION_ID: "buy-fr-tax",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    buy = transactions[0]
    assert isinstance(buy, Trading212Transaction)
    assert buy.foreign_fees == {"EUR": Decimal("0.60")}
    assert buy.stamp_duty == Decimal(0)
    assert buy.fees == Decimal(0)
    assert buy.amount == Decimal("-170.00")


def test_read_trading212_transactions_non_gbp_stamp_duty(tmp_path: Path) -> None:
    """Store non-GBP stamp duty in foreign_fees."""

    rows = [
        HEADER_2025_BARE,
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2025-04-02 10:30:00",
                Trading212Column.ISIN: "GB0000000017",
                Trading212Column.TICKER: "WID",
                Trading212Column.NAME: "Widget plc",
                Trading212Column.NO_OF_SHARES: "50",
                Trading212Column.PRICE_PER_SHARE: "6.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "GBP",
                Trading212Column.TOTAL: "301.50",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.STAMP_DUTY: "1.50",
                Trading212Column.CURRENCY_STAMP_DUTY: "EUR",
                Trading212Column.TRANSACTION_ID: "buy-1",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    buy = transactions[0]
    assert isinstance(buy, Trading212Transaction)
    assert buy.foreign_fees == {"EUR": Decimal("1.50")}
    assert buy.stamp_duty == Decimal(0)
    assert buy.fees == Decimal(0)


def test_read_trading212_transactions_mixed_fee_currencies(tmp_path: Path) -> None:
    """Split fees between the account currency and foreign_fees."""

    rows = [
        HEADER_2024,
        _make_row(
            HEADER_2024,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2024-01-01 16:10:05",
                Trading212Column.ISIN: "US0000000036",
                Trading212Column.TICKER: "BAZ",
                Trading212Column.NAME: "Baz Corp",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "10.50",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "17.11",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_FEE: "0.20",
                Trading212Column.CURRENCY_TRANSACTION_FEE: "USD",
                Trading212Column.CURRENCY_CONVERSION_FEE: "0.15",
                Trading212Column.CURRENCY_CURRENCY_CONVERSION_FEE: "GBP",
                Trading212Column.TRANSACTION_ID: "buy-mixed",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    buy = transactions[0]
    assert isinstance(buy, Trading212Transaction)
    assert buy.foreign_fees == {"USD": Decimal("0.20")}
    assert buy.transaction_fee == Decimal(0)
    assert buy.conversion_fee == Decimal("0.15")
    assert buy.fees == Decimal("0.15")


def test_read_trading212_transactions_missing_fee_currency(tmp_path: Path) -> None:
    """Raise ParsingError when a broker fee has no currency."""

    rows = [
        HEADER_2024,
        _make_row(
            HEADER_2024,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2024-01-01 16:10:05",
                Trading212Column.ISIN: "US0000000036",
                Trading212Column.TICKER: "BAZ",
                Trading212Column.NAME: "Baz Corp",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "10.50",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "16.96",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_FEE: "0.20",
                Trading212Column.TRANSACTION_ID: "buy-no-fee-currency",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    with pytest.raises(ParsingError) as exc:
        Trading212Parser().load_from_dir(folder)

    message = str(exc.value)
    assert "row 2" in message
    assert "Missing currency for Transaction fee" in message


def test_read_trading212_transactions_blank_tax_currency(tmp_path: Path) -> None:
    """Fold a tax with a blank currency into the transaction currency."""

    rows = [
        HEADER_2025_BARE,
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2025-04-02 10:30:00",
                Trading212Column.ISIN: "IE0000000095",
                Trading212Column.TICKER: "EURX",
                Trading212Column.NAME: "Euro plc",
                Trading212Column.NO_OF_SHARES: "50",
                Trading212Column.PRICE_PER_SHARE: "6.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "EUR",
                Trading212Column.TOTAL: "301.50",
                Trading212Column.CURRENCY_TOTAL: "EUR",
                Trading212Column.STAMP_DUTY: "1.50",
                Trading212Column.TRANSACTION_ID: "buy-blank-tax-currency",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    buy = transactions[0]
    assert isinstance(buy, Trading212Transaction)
    assert buy.stamp_duty == Decimal("1.50")
    assert buy.fees == Decimal("1.50")
    assert buy.foreign_fees == {}
    assert buy.price == Decimal("6.00")


def _make_usd_fee_buy_row(total: str) -> list[str]:
    """Build a GBP-account buy of a USD stock with a USD transaction fee."""
    return _make_row(
        HEADER_2024,
        {
            Trading212Column.ACTION: "Market buy",
            Trading212Column.TIME: "2024-01-01 16:10:05",
            Trading212Column.ISIN: "US0000000036",
            Trading212Column.TICKER: "BAZ",
            Trading212Column.NAME: "Baz Corp",
            Trading212Column.NO_OF_SHARES: "2",
            Trading212Column.PRICE_PER_SHARE: "10.50",
            Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
            Trading212Column.EXCHANGE_RATE: "1.25",
            Trading212Column.TOTAL: total,
            Trading212Column.CURRENCY_TOTAL: "GBP",
            Trading212Column.TRANSACTION_FEE: "0.20",
            Trading212Column.CURRENCY_TRANSACTION_FEE: "USD",
            Trading212Column.TRANSACTION_ID: "buy-usd-fee",
        },
    )


def test_read_trading212_transactions_foreign_fee_price_discrepancy(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Warn when the price does not add up for a foreign-fee row."""

    folder = _prepare_file(tmp_path, [HEADER_2024, _make_usd_fee_buy_row("17.50")])

    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        Trading212Parser().load_from_dir(folder)

    assert "does not add up" in caplog.text


def test_read_trading212_transactions_foreign_fee_price_consistent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Stay quiet when a foreign-fee row's price adds up."""

    folder = _prepare_file(tmp_path, [HEADER_2024, _make_usd_fee_buy_row("16.96")])

    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        Trading212Parser().load_from_dir(folder)

    assert "does not add up" not in caplog.text


def test_read_trading212_transactions_unconvertible_fee_skips_price_check(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Skip the price check when a fee cannot be converted from the export."""

    rows = [
        HEADER_2025_BARE,
        _make_row(
            HEADER_2025_BARE,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2025-04-03 11:00:00",
                Trading212Column.ISIN: "US0000000036",
                Trading212Column.TICKER: "BAZ",
                Trading212Column.NAME: "Baz Corp",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "10.50",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "17.50",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.FRENCH_TRANSACTION_TAX: "0.60",
                Trading212Column.CURRENCY_FRENCH_TRANSACTION_TAX: "EUR",
                Trading212Column.TRANSACTION_ID: "buy-eur-tax",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        Trading212Parser().load_from_dir(folder)

    assert "does not add up" not in caplog.text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024-01-01 16:10:05", datetime(2024, 1, 1, 16, 10, 5, tzinfo=UTC)),
        (
            "2024-01-01 16:10:05.175",
            datetime(2024, 1, 1, 16, 10, 5, 175000, tzinfo=UTC),
        ),
        ("2024-01-01T16:10:05Z", datetime(2024, 1, 1, 16, 10, 5, tzinfo=UTC)),
        (
            "2024-01-01 16:10:05.175+00:00",
            datetime(2024, 1, 1, 16, 10, 5, 175000, tzinfo=UTC),
        ),
        ("2024-01-01 17:10:05+01:00", datetime(2024, 1, 1, 16, 10, 5, tzinfo=UTC)),
    ],
)
def test_datetime_from_str(value: str, expected: datetime) -> None:
    """Parse every timestamp shape the exports use, normalised to UTC."""

    assert datetime_from_str(value) == expected


@pytest.mark.parametrize(
    ("time_str", "expected"),
    [
        # The tax year boundary always falls inside BST, so the last hour
        # of 5 April in UTC already belongs to the next tax year.
        ("2025-04-05 22:59:59+00:00", date(2025, 4, 5)),
        ("2025-04-05 23:00:00+00:00", date(2025, 4, 6)),
        ("2025-04-05 23:30:00", date(2025, 4, 6)),
        # Outside summer time UK dates and UTC dates agree.
        ("2026-01-15 23:30:00+00:00", date(2026, 1, 15)),
        ("2026-11-01 23:30:00+00:00", date(2026, 11, 1)),
    ],
)
def test_read_trading212_transactions_uses_uk_dates(
    tmp_path: Path, time_str: str, expected: date
) -> None:
    """Take the tax date from the UK calendar, not the UTC one."""

    rows = [
        HEADER_2024,
        _make_row(
            HEADER_2024,
            {
                Trading212Column.ACTION: "Deposit",
                Trading212Column.TIME: time_str,
                Trading212Column.TOTAL: "100.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "deposit-boundary",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    assert transactions[0].date == expected


def test_read_trading212_transactions_supports_time_utc_column(
    tmp_path: Path,
) -> None:
    """Parse an export whose Time column spells out the zone."""

    rows = [
        HEADER_2026_UTC,
        _make_row(
            HEADER_2026_UTC,
            {
                Trading212Column.TIME_UTC: "2026-05-02 14:30:05.123",
                Trading212Column.ACTION: "Market buy",
                Trading212Column.ISIN: "US0000000200",
                Trading212Column.TICKER: "ACME",
                Trading212Column.NAME: "Acme Corp",
                Trading212Column.NO_OF_SHARES: "10",
                Trading212Column.PRICE_PER_SHARE: "150.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "1200.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "buy-utc",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    transactions = Trading212Parser().load_from_dir(folder)

    assert len(transactions) == 1
    transaction = transactions[0]
    assert isinstance(transaction, Trading212Transaction)
    assert transaction.datetime == datetime(2026, 5, 2, 14, 30, 5, 123000, tzinfo=UTC)
    assert transaction.amount == Decimal("-1200.00")


def test_read_trading212_transactions_mixes_time_column_spellings(
    tmp_path: Path,
) -> None:
    """Read old and new exports from the same directory."""

    folder = tmp_path / "inputs"
    folder.mkdir()
    _write_csv(
        folder / "old.csv",
        [
            HEADER_2026,
            _make_row(
                HEADER_2026,
                {
                    Trading212Column.ACTION: "Deposit",
                    Trading212Column.TIME: "2026-05-01 00:10:00.000",
                    Trading212Column.TOTAL: "100.00",
                    Trading212Column.CURRENCY_TOTAL: "GBP",
                    Trading212Column.TRANSACTION_ID: "deposit-old",
                },
            ),
        ],
    )
    _write_csv(
        folder / "new.csv",
        [
            HEADER_2026_UTC,
            _make_row(
                HEADER_2026_UTC,
                {
                    Trading212Column.ACTION: "Deposit",
                    Trading212Column.TIME_UTC: "2026-05-02 00:10:00.000",
                    Trading212Column.TOTAL: "200.00",
                    Trading212Column.CURRENCY_TOTAL: "GBP",
                    Trading212Column.TRANSACTION_ID: "deposit-new",
                },
            ),
        ],
    )

    transactions = Trading212Parser().load_from_dir(folder)

    assert [transaction.amount for transaction in transactions] == [
        Decimal("100.00"),
        Decimal("200.00"),
    ]


def test_load_from_dir_deduplicates_overlapping_exports(tmp_path: Path) -> None:
    """Collapse a transaction that two overlapping exports both report.

    Exports are requested by date range, so consecutive downloads routinely
    cover the same days. Rows are merged on the content the export recorded
    and the number of copies each file carries, so any field that varies with
    the source file rather than with the transaction would keep both copies
    and double the holding.
    """

    folder = tmp_path / "inputs"
    folder.mkdir()

    def buy(transaction_id: str, day: str, shares: str, total: str) -> list[str]:
        return _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: f"{day} 09:30:00",
                Trading212Column.ISIN: "US0000000002",
                Trading212Column.TICKER: "FOO",
                Trading212Column.NAME: "Foo Inc",
                Trading212Column.NO_OF_SHARES: shares,
                Trading212Column.PRICE_PER_SHARE: "100.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "GBP",
                Trading212Column.TOTAL: total,
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: transaction_id,
            },
        )

    overlapping = buy("buy-overlapping", "2026-02-10", "2", "200.00")
    _write_csv(
        folder / "2026-01.csv",
        [HEADER_2026, buy("buy-january", "2026-01-05", "1", "100.00"), overlapping],
    )
    _write_csv(
        folder / "2026-02.csv",
        [HEADER_2026, overlapping, buy("buy-february", "2026-02-20", "3", "300.00")],
    )

    transactions = Trading212Parser().load_from_dir(folder)

    assert [transaction.date for transaction in transactions] == [
        date(2026, 1, 5),
        date(2026, 2, 10),
        date(2026, 2, 20),
    ]
    assert sum(
        transaction.quantity or Decimal(0) for transaction in transactions
    ) == Decimal(6)


def test_load_from_dir_records_where_each_row_came_from(tmp_path: Path) -> None:
    """Every row keeps its file, line, read order and exported instant.

    The line is what an error points at; the read order is what says which
    of two rows on one day came first, and the export's own instant settles
    it here without needing either.
    """
    folder = tmp_path / "inputs"
    folder.mkdir()

    def buy(transaction_id: str, day: str) -> list[str]:
        return _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: f"{day} 09:30:00",
                Trading212Column.ISIN: "US0000000002",
                Trading212Column.TICKER: "FOO",
                Trading212Column.NAME: "Foo Inc",
                Trading212Column.NO_OF_SHARES: "1",
                Trading212Column.PRICE_PER_SHARE: "100.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "GBP",
                Trading212Column.TOTAL: "100.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: transaction_id,
            },
        )

    _write_csv(
        folder / "2026-01.csv",
        [HEADER_2026, buy("buy-a", "2026-01-05"), buy("buy-b", "2026-01-06")],
    )
    _write_csv(folder / "2026-02.csv", [HEADER_2026, buy("buy-c", "2026-02-10")])

    transactions = Trading212Parser().load_from_dir(folder)

    sources = [transaction.source for transaction in transactions]
    assert [source.file.name for source in sources if source and source.file] == [
        "2026-01.csv",
        "2026-01.csv",
        "2026-02.csv",
    ]
    assert [source.row for source in sources if source] == [2, 3, 2]
    assert [source.index for source in sources if source] == [0, 1, 0]
    assert [source.parser for source in sources if source] == ["Trading 212"] * 3
    assert [source.timestamp for source in sources if source] == [
        datetime(2026, 1, 5, 9, 30, tzinfo=UTC),
        datetime(2026, 1, 6, 9, 30, tzinfo=UTC),
        datetime(2026, 2, 10, 9, 30, tzinfo=UTC),
    ]


def test_one_directory_is_one_account_boundary(tmp_path: Path) -> None:
    """Files in one directory share a boundary; two loads never do.

    Passing a directory is the user's declaration that its exports are one
    account. Two separately configured inputs are two accounts, whatever the
    export says, because nothing in the CSV identifies the account.
    """
    rows = [
        HEADER_2026,
        _make_row(
            HEADER_2026,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2026-01-05 09:30:00",
                Trading212Column.ISIN: "US0000000002",
                Trading212Column.TICKER: "FOO",
                Trading212Column.NAME: "Foo Inc",
                Trading212Column.NO_OF_SHARES: "1",
                Trading212Column.PRICE_PER_SHARE: "100.00",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "GBP",
                Trading212Column.TOTAL: "100.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "buy-a",
            },
        ),
    ]
    first = tmp_path / "first"
    first.mkdir()
    _write_csv(first / "a.csv", rows)
    _write_csv(first / "b.csv", rows)
    second = tmp_path / "second"
    second.mkdir()
    _write_csv(second / "a.csv", rows)

    together = Trading212Parser().load_from_dir(first)
    apart = Trading212Parser().load_from_dir(second)

    accounts = {
        transaction.source.account
        for transaction in together
        if transaction.source is not None
    }
    assert len(accounts) == 1
    assert apart[0].source is not None
    assert apart[0].source.account not in accounts


def test_read_trading212_transactions_missing_time_column(tmp_path: Path) -> None:
    """Raise ParsingError when neither Time column is present."""

    header = ["Action", "Total", "Currency (Total)", "ID"]
    rows = [header, ["Deposit", "100.00", "GBP", "deposit-no-time"]]
    folder = _prepare_file(tmp_path, rows)

    with pytest.raises(ParsingError) as exc:
        Trading212Parser().load_from_dir(folder)

    message = str(exc.value)
    assert "row 2" in message
    assert "Missing Time or Time (UTC)" in message


def test_read_trading212_transactions_invalid_time(tmp_path: Path) -> None:
    """Raise ParsingError when the timestamp cannot be parsed."""

    rows = [
        HEADER_2024,
        _make_row(
            HEADER_2024,
            {
                Trading212Column.ACTION: "Deposit",
                Trading212Column.TIME: "01/01/2024 10:00",
                Trading212Column.TOTAL: "100.00",
                Trading212Column.CURRENCY_TOTAL: "GBP",
                Trading212Column.TRANSACTION_ID: "deposit-bad-time",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    with pytest.raises(ParsingError) as exc:
        Trading212Parser().load_from_dir(folder)

    message = str(exc.value)
    assert "row 2" in message
    assert "Invalid timestamp" in message


def _make_dividend_row(
    total: str, overrides: Mapping[str | Trading212Column, str] | None = None
) -> list[str]:
    """Build a GBP-account dividend of a USD stock with USD withholding tax."""
    row: dict[str | Trading212Column, str] = {
        Trading212Column.ACTION: "Dividend (Ordinary)",
        Trading212Column.TIME: "2024-06-01 12:00:00",
        Trading212Column.ISIN: "US0000000036",
        Trading212Column.TICKER: "BAZ",
        Trading212Column.NAME: "Baz Corp",
        Trading212Column.NO_OF_SHARES: "10",
        Trading212Column.PRICE_PER_SHARE: "1.00",
        Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
        Trading212Column.EXCHANGE_RATE: "1.25",
        Trading212Column.TOTAL: total,
        Trading212Column.CURRENCY_TOTAL: "GBP",
        Trading212Column.WITHHOLDING_TAX: "1.50",
        Trading212Column.CURRENCY_WITHHOLDING_TAX: "USD",
        Trading212Column.TRANSACTION_ID: "dividend-withholding",
    }
    row.update(overrides or {})
    return _make_row(HEADER_2024, row)


def test_read_trading212_transactions_withholding_tax_price_consistent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Stay quiet when only withholding tax separates the total from the price.

    Trading 212 reports the gross Price per Share but a Total net of
    withholding tax, so the tax has to be added back before comparing them.
    """

    # 10 shares at $1.00 less $1.50 tax is $8.50, or GBP 6.80 at 1.25.
    folder = _prepare_file(tmp_path, [HEADER_2024, _make_dividend_row("6.80")])

    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        transactions = Trading212Parser().load_from_dir(folder)

    assert "does not add up" not in caplog.text
    assert transactions[0].amount == Decimal("6.80")
    # Withholding tax is not a dealing cost.
    assert transactions[0].fees == Decimal(0)


def test_read_trading212_transactions_withholding_tax_price_discrepancy(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Still warn when a dividend does not add up once tax is accounted for."""

    folder = _prepare_file(tmp_path, [HEADER_2024, _make_dividend_row("6.00")])

    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        Trading212Parser().load_from_dir(folder)

    assert "does not add up" in caplog.text


@pytest.mark.parametrize("tax_currency", ["GBP", ""])
def test_read_trading212_transactions_account_currency_withholding_tax(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, tax_currency: str
) -> None:
    """Add back withholding tax charged in the account currency.

    Some exports leave the tax currency blank, which means the currency of
    the transaction.
    """

    row = _make_dividend_row(
        "8.50",
        {
            Trading212Column.CURRENCY_PRICE_PER_SHARE: "GBP",
            Trading212Column.EXCHANGE_RATE: "",
            Trading212Column.CURRENCY_WITHHOLDING_TAX: tax_currency,
        },
    )
    folder = _prepare_file(tmp_path, [HEADER_2024, row])

    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        Trading212Parser().load_from_dir(folder)

    assert "does not add up" not in caplog.text


def test_read_trading212_transactions_invalid_decimal(tmp_path: Path) -> None:
    """Raise ParsingError when a decimal value is invalid."""

    rows = [
        HEADER_2024,
        _make_row(
            HEADER_2024,
            {
                Trading212Column.ACTION: "Market buy",
                Trading212Column.TIME: "2024-01-01 10:00:00",
                Trading212Column.ISIN: "US0000000010",
                Trading212Column.TICKER: "FOO",
                Trading212Column.NAME: "Foo Inc",
                Trading212Column.NO_OF_SHARES: "2",
                Trading212Column.PRICE_PER_SHARE: "10.50",
                Trading212Column.CURRENCY_PRICE_PER_SHARE: "USD",
                Trading212Column.EXCHANGE_RATE: "1.25",
                Trading212Column.TOTAL: "invalid",
                Trading212Column.CURRENCY_TOTAL: "GBP",
            },
        ),
    ]
    folder = _prepare_file(tmp_path, rows)

    with pytest.raises(ParsingError) as exc:
        Trading212Parser().load_from_dir(folder)

    message = str(exc.value)
    assert "row 2" in message
    assert "Invalid decimal in Total" in message


def test_run_with_trading212_2026_files(request: pytest.FixtureRequest) -> None:
    """Run the script on a 2026-format export with foreign fees.

    Covers a US sell with a USD Finra fee and a French buy with an EUR
    French transaction tax, so the report numbers pin the engine's
    foreign fee conversion end to end.
    """
    cmd = build_cmd(
        "--year",
        "2024",
        "--trading212-dir",
        "tests/trading212/data/2026/inputs/",
        "--output",
        report_path(request),
    )
    result = run_cli(cmd)
    assert stderr_alerts(result.stderr) == [], "Run with example files generated errors"
    expected_file = (
        Path("tests") / "trading212" / "data" / "2026" / "expected_output.txt"
    )
    assert_stdout_matches(result, cmd, expected_file)


def test_run_with_trading212_2024_files(request: pytest.FixtureRequest) -> None:
    """Runs the script and verifies it doesn't fail."""
    cmd = build_cmd(
        "--year",
        "2024",
        "--trading212-dir",
        "tests/trading212/data/2024/inputs/",
        "--output",
        report_path(request),
    )
    result = run_cli(cmd)
    assert stderr_alerts(result.stderr) == [], "Run with example files generated errors"
    expected_file = (
        Path("tests") / "trading212" / "data" / "2024" / "expected_output.txt"
    )
    assert_stdout_matches(result, cmd, expected_file)


def test_the_legacy_single_row_stock_split_still_reaches_the_calculator(
    tmp_path: Path,
) -> None:
    """Trading 212 also exports a reorganisation as one `Stock Split` row.

    That row states the net change to the holding at this account rather than
    the two counts either side of the event, so it takes the calculator's
    legacy-delta path. Pairing must leave it alone: it is not half of
    anything, and there is no partner for it to go looking for.
    """
    folder = _prepare_file(
        tmp_path,
        [
            HEADER_2026,
            _make_row(
                HEADER_2026,
                {
                    Trading212Column.ACTION: "Market buy",
                    Trading212Column.TIME: "2026-01-05 10:00:00",
                    Trading212Column.TICKER: "FOO",
                    Trading212Column.NO_OF_SHARES: "10",
                    Trading212Column.PRICE_PER_SHARE: "10.00",
                    Trading212Column.CURRENCY_PRICE_PER_SHARE: "GBP",
                    Trading212Column.TOTAL: "100.00",
                    Trading212Column.CURRENCY_TOTAL: "GBP",
                    Trading212Column.TRANSACTION_ID: "buy-1",
                },
            ),
            _make_row(
                HEADER_2026,
                {
                    Trading212Column.ACTION: "Stock Split",
                    Trading212Column.TIME: "2026-02-02 07:44:13",
                    Trading212Column.TICKER: "FOO",
                    Trading212Column.NO_OF_SHARES: "10",
                    Trading212Column.CURRENCY_TOTAL: "GBP",
                    Trading212Column.TRANSACTION_ID: "split-1",
                },
            ),
        ],
    )
    buy, split = Trading212Parser().load_from_dir(folder)
    assert buy.action is ActionType.BUY
    # Still the parser's own row, not combined into a paired event.
    assert isinstance(split, Trading212Transaction)
    assert split.action is ActionType.STOCK_SPLIT
    assert split.quantity == Decimal(10)

    calculator = create_calculator(tax_year=2025, balance_check=False)
    calculator.prepare_history([buy, split])
    # A reorganisation, so the pool doubles in units and keeps its cost.
    assert calculator.portfolio["FOO"].quantity == Decimal(20)
    assert calculator.portfolio["FOO"].amount == Decimal(100)


# ===== Overlapping export de-duplication =====
#
# A single export may repeat a row, because Trading 212 can fill one
# order more than once in a second. Two exports covering overlapping
# ranges repeat every row in the overlap. Only the second kind may be
# removed, so these tests always say which file each row came from.


def _trade_row(
    time: str,
    transaction_id: str = "",
    *,
    action: str = "Market buy",
    total: str = "-10.00",
    header: list[str] | None = None,
) -> list[str]:
    """One buy of FOO, identical in every field the merge compares."""
    columns = header if header is not None else HEADER_2024
    # The parser warns when price * quantity does not reach the total, so
    # a row that differs by value has to move both together.
    price = total.lstrip("-")
    time_column = (
        Trading212Column.TIME_UTC
        if Trading212Column.TIME_UTC.value in columns
        else Trading212Column.TIME
    )
    return _make_row(
        columns,
        {
            Trading212Column.ACTION: action,
            time_column: time,
            Trading212Column.TICKER: "FOO",
            Trading212Column.NAME: "Foo Inc",
            Trading212Column.NO_OF_SHARES: "1.0000000000",
            Trading212Column.PRICE_PER_SHARE: price,
            Trading212Column.CURRENCY_PRICE_PER_SHARE: "GBP",
            Trading212Column.TOTAL: total,
            Trading212Column.CURRENCY_TOTAL: "GBP",
            Trading212Column.TRANSACTION_ID: transaction_id,
        },
    )


def _prepare_files(tmp_path: Path, files: Mapping[str, list[list[str]]]) -> Path:
    folder = tmp_path / "inputs"
    folder.mkdir(parents=True)
    for name, rows in files.items():
        _write_csv(folder / name, rows)
    return folder


def _export(*rows: list[str], header: list[str] | None = None) -> list[list[str]]:
    return [header if header is not None else HEADER_2024, *rows]


def _fractions(transactions: Sequence[BrokerTransaction]) -> list[int]:
    """Sub-second component of each row, which is what the merge picks between."""
    return [_datetime_of(transaction).microsecond for transaction in transactions]


def _datetime_of(transaction: BrokerTransaction) -> datetime:
    assert isinstance(transaction, Trading212Transaction)
    return transaction.datetime


def _identity(
    transactions: Sequence[BrokerTransaction],
) -> list[tuple[object, ...]]:
    """Everything about a row that distinguishes it from its neighbours.

    `BrokerTransaction` is a data class whose equality covers none of the
    time below the day, the ID, or the file a row came from, so comparing
    transactions directly passes even when the merge picked the wrong
    representative or ordered the result backwards.
    """
    return [
        (
            _datetime_of(transaction),
            _transaction_id_of(transaction),
            transaction.action,
            transaction.amount,
        )
        for transaction in transactions
    ]


def _transaction_id_of(transaction: BrokerTransaction) -> str | None:
    assert isinstance(transaction, Trading212Transaction)
    return transaction.transaction_id


def _source_of(transaction: BrokerTransaction) -> Path:
    assert transaction.source is not None
    assert transaction.source.file is not None
    return transaction.source.file


# An export predating the ID column entirely, as opposed to one that has
# the column and leaves it blank.
HEADER_2024_NO_ID = [column for column in HEADER_2024 if column != "ID"]


# ----- one export is never de-duplicated -----


def test_single_file_keeps_one_row(tmp_path: Path) -> None:
    """One row is one transaction."""
    folder = _prepare_files(
        tmp_path, {"a.csv": _export(_trade_row("2024-01-01 10:00:00"))}
    )
    assert len(Trading212Parser.load_from_dir(folder)) == 1


def test_single_file_keeps_repeated_anonymous_rows(tmp_path: Path) -> None:
    """Two identical rows in one export are two fills, not a duplicate."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00"),
                _trade_row("2024-01-01 10:00:00"),
            )
        },
    )
    assert len(Trading212Parser.load_from_dir(folder)) == 2


def test_single_file_keeps_repeated_rows_sharing_an_id(tmp_path: Path) -> None:
    """A repeated ID within one export still means two fills.

    This is one of the two cases the superseded prototype got wrong: it
    hashed the ID, so the second row collided with the first and was
    dropped.
    """
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00", "X"),
                _trade_row("2024-01-01 10:00:00", "X"),
            )
        },
    )
    assert len(Trading212Parser.load_from_dir(folder)) == 2


def test_single_file_keeps_rows_at_different_seconds(tmp_path: Path) -> None:
    """Equal tax fields an hour apart are two transactions."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00"),
                _trade_row("2024-01-01 11:00:00"),
            )
        },
    )
    assert len(Trading212Parser.load_from_dir(folder)) == 2


def test_single_file_keeps_distinct_ids_in_one_second(tmp_path: Path) -> None:
    """Two IDs in one second are two transactions."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00", "X"),
                _trade_row("2024-01-01 10:00:00", "Y"),
            )
        },
    )
    assert len(Trading212Parser.load_from_dir(folder)) == 2


def test_one_file_dir_matches_load_from_file(tmp_path: Path) -> None:
    """The directory phase is a no-op when there is nothing to overlap."""
    rows = _export(
        _trade_row("2024-01-01 10:00:00", "X"),
        _trade_row("2024-01-01 10:00:00", "X"),
        _trade_row("2024-01-01 10:00:00"),
        _trade_row("2024-01-01 10:00:01"),
    )
    folder = _prepare_files(tmp_path, {"a.csv": rows})

    from_dir = Trading212Parser.load_from_dir(folder)
    from_file = Trading212Parser.load_from_file(folder / "a.csv")

    assert len(from_dir) == len(from_file) == 4
    assert _identity(from_dir) == _identity(from_file)
    # The rows differ only below the day and by ID, which data class
    # equality ignores, so this has to be checked on the identities.
    assert _identity(from_dir) != _identity(from_file)[::-1]


def test_one_file_dir_does_not_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A single export can never be a conflict between exports."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00", "X"),
                _trade_row("2024-01-01 10:00:00", "X", total="-11.00"),
            )
        },
    )
    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        transactions = Trading212Parser.load_from_dir(folder)

    assert len(transactions) == 2
    assert caplog.text == ""


# ----- overlapping exports are de-duplicated -----


def test_identified_transaction_in_two_files_kept_once(tmp_path: Path) -> None:
    """The plain overlap: one transaction, two exports, one row."""
    row = _trade_row("2024-01-01 10:00:00", "X")
    folder = _prepare_files(tmp_path, {"a.csv": _export(row), "b.csv": _export(row)})
    assert len(Trading212Parser.load_from_dir(folder)) == 1


def test_identified_and_anonymous_representation_kept_once(tmp_path: Path) -> None:
    """An older export without the ID column matches a newer one that has it.

    The older file has no `ID` header at all, which is a different thing
    from a file that carries the column and leaves it empty; both must
    read as "no identity" and match the identified row.
    """
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(_trade_row("2024-01-01 10:00:00", "X")),
            "b.csv": _export(
                _trade_row("2024-01-01 10:00:00", header=HEADER_2024_NO_ID),
                header=HEADER_2024_NO_ID,
            ),
        },
    )
    transactions = Trading212Parser.load_from_dir(folder)
    assert _identity(transactions) == [
        (
            datetime(2024, 1, 1, 10, 0, tzinfo=UTC),
            "X",
            ActionType.BUY,
            Decimal("-10.00"),
        )
    ]


def test_blank_id_column_matches_a_missing_one(tmp_path: Path) -> None:
    """A blank ID and no ID column are both "no identity"."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(_trade_row("2024-01-01 10:00:00")),
            "b.csv": _export(
                _trade_row("2024-01-01 10:00:00", header=HEADER_2024_NO_ID),
                header=HEADER_2024_NO_ID,
            ),
        },
    )
    assert len(Trading212Parser.load_from_dir(folder)) == 1


def test_anonymous_transaction_in_two_files_kept_once(tmp_path: Path) -> None:
    """Two ID-less exports still de-duplicate on the transaction details."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(_trade_row("2024-01-01 10:00:00")),
            "b.csv": _export(_trade_row("2024-01-01 10:00:00")),
        },
    )
    assert len(Trading212Parser.load_from_dir(folder)) == 1


def test_anonymous_multiplicity_takes_the_widest_export(tmp_path: Path) -> None:
    """One export seeing two fills settles it, even if another saw one."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(_trade_row("2024-01-01 10:00:00")),
            "b.csv": _export(
                _trade_row("2024-01-01 10:00:00"),
                _trade_row("2024-01-01 10:00:00"),
            ),
        },
    )
    assert len(Trading212Parser.load_from_dir(folder)) == 2


def test_distinct_ids_on_one_fingerprint_both_kept(tmp_path: Path) -> None:
    """Two IDs are two transactions however the exports are split."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(_trade_row("2024-01-01 10:00:00", "X")),
            "b.csv": _export(_trade_row("2024-01-01 10:00:00", "Y")),
        },
    )
    assert len(Trading212Parser.load_from_dir(folder)) == 2


def test_repeated_id_uses_maximum_per_file_multiplicity(tmp_path: Path) -> None:
    """Not one, and not the sum across files."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00", "X"),
                _trade_row("2024-01-01 10:00:00", "X"),
            ),
            "b.csv": _export(_trade_row("2024-01-01 10:00:00", "X")),
        },
    )
    assert len(Trading212Parser.load_from_dir(folder)) == 2


def test_buy_and_sell_reusing_one_id_both_kept(tmp_path: Path) -> None:
    """Trading 212 reuses an ID across a buy and the sell that closes it."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00", "X"),
                _trade_row(
                    "2024-04-29 13:20:03", "X", action="Market sell", total="12.00"
                ),
            ),
        },
    )
    assert len(Trading212Parser.load_from_dir(folder)) == 2


def test_anonymous_rows_at_different_seconds_across_files(tmp_path: Path) -> None:
    """Equal tax fields an hour apart are never one transaction.

    The other case the superseded prototype got wrong: data class
    equality ignored the time, so these two collapsed into one.
    """
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(_trade_row("2024-01-01 10:00:00")),
            "b.csv": _export(_trade_row("2024-01-01 11:00:00")),
        },
    )
    assert len(Trading212Parser.load_from_dir(folder)) == 2


def test_mixed_precision_merges_and_keeps_the_fraction(tmp_path: Path) -> None:
    """An old whole-second export and a new millisecond one agree."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(_trade_row("2024-01-01 10:00:00")),
            "b.csv": _export(_trade_row("2024-01-01 10:00:00.123")),
        },
    )
    transactions = Trading212Parser.load_from_dir(folder)
    assert _fractions(transactions) == [123000]


def test_time_and_time_utc_columns_merge(tmp_path: Path) -> None:
    """The renamed column describes the same instant, so it must match."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(_trade_row("2024-01-01 10:00:00")),
            "b.csv": _export(
                _trade_row("2024-01-01 11:00:00+01:00", header=HEADER_2026_UTC),
                header=HEADER_2026_UTC,
            ),
        },
    )
    assert len(Trading212Parser.load_from_dir(folder)) == 1


def test_deposit_and_buy_keep_the_buy_last_across_precisions(tmp_path: Path) -> None:
    """The merge must not disturb the same-second deposit/buy ordering."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _make_row(
                    HEADER_2024,
                    {
                        Trading212Column.ACTION: "Deposit",
                        Trading212Column.TIME: "2024-01-01 10:00:00",
                        Trading212Column.TOTAL: "100.00",
                        Trading212Column.CURRENCY_TOTAL: "GBP",
                    },
                ),
                _trade_row("2024-01-01 10:00:00"),
            ),
            "b.csv": _export(
                _make_row(
                    HEADER_2024,
                    {
                        Trading212Column.ACTION: "Deposit",
                        Trading212Column.TIME: "2024-01-01 10:00:00.123",
                        Trading212Column.TOTAL: "100.00",
                        Trading212Column.CURRENCY_TOTAL: "GBP",
                    },
                ),
                _trade_row("2024-01-01 10:00:00.123"),
            ),
        },
    )
    transactions = Trading212Parser.load_from_dir(folder)

    assert _fractions(transactions) == [123000, 123000]
    assert [t.action for t in transactions] == [ActionType.TRANSFER, ActionType.BUY]


def test_three_overlapping_exports_use_the_widest(tmp_path: Path) -> None:
    """More exports do not add multiplicity; the widest single one decides."""
    one = _export(_trade_row("2024-01-01 10:00:00"))
    two = _export(_trade_row("2024-01-01 10:00:00"), _trade_row("2024-01-01 10:00:00"))
    folder = _prepare_files(tmp_path, {"a.csv": one, "b.csv": two, "c.csv": one})
    assert len(Trading212Parser.load_from_dir(folder)) == 2


# ----- which representative survives -----


def test_wider_id_bucket_wins_whole(tmp_path: Path) -> None:
    """Two fills under one ID come from the export that saw both.

    Assembling the two from the union would take `.100` from each file
    and lose the `.500` fill entirely.
    """
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00.100", "X"),
                _trade_row("2024-01-01 10:00:00.500", "X"),
            ),
            "b.csv": _export(_trade_row("2024-01-01 10:00:00.100", "X")),
        },
    )
    assert _fractions(Trading212Parser.load_from_dir(folder)) == [100000, 500000]


def test_tied_id_buckets_prefer_distinct_fractions(tmp_path: Path) -> None:
    """A bucket that repeats one fraction loses to one that tells them apart.

    Both exports report the ID twice, so multiplicity cannot separate
    them. Comparing the fractions alone would pick `(.100, .100)`, which
    sorts below `(.100, .500)`, and discard the distinct occurrence.
    """
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00.100", "X"),
                _trade_row("2024-01-01 10:00:00.500", "X"),
            ),
            "b.csv": _export(
                _trade_row("2024-01-01 10:00:00.100", "X"),
                _trade_row("2024-01-01 10:00:00.100", "X"),
            ),
        },
    )
    assert _fractions(Trading212Parser.load_from_dir(folder)) == [100000, 500000]


def test_wider_anonymous_bucket_wins_whole(tmp_path: Path) -> None:
    """The same coherence rule applies with no IDs to help."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00.100"),
                _trade_row("2024-01-01 10:00:00.500"),
            ),
            "b.csv": _export(_trade_row("2024-01-01 10:00:00.100")),
        },
    )
    assert _fractions(Trading212Parser.load_from_dir(folder)) == [100000, 500000]


def test_anonymous_completion_ranks_with_the_identified_rows(tmp_path: Path) -> None:
    """The anonymous slot is filled against what is already selected.

    Ranking the anonymous rows on their own would prefer the export
    holding two of them, then take its `.100` — duplicating the
    timestamp the identified row already carries and dropping `.500`.
    """
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00.100", "X"),
                _trade_row("2024-01-01 10:00:00.500"),
            ),
            "b.csv": _export(
                _trade_row("2024-01-01 10:00:00.100"),
                _trade_row("2024-01-01 10:00:00.500"),
            ),
        },
    )
    assert _fractions(Trading212Parser.load_from_dir(folder)) == [100000, 500000]


def test_anonymous_subset_is_chosen_against_the_identified_rows(
    tmp_path: Path,
) -> None:
    """The subset is picked knowing which timestamps are already taken.

    One export is the widest, so there is no choice between sources to
    mask the decision, and it offers three anonymous rows for two slots.
    Judged alone the cheapest pair is `.100` and `.500`; judged with the
    identified `.100` already selected, `.100` adds nothing and the pair
    `.500`/`.700` is what keeps four distinct timestamps.
    """
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00.100", "X"),
                _trade_row("2024-01-01 10:00:00.100"),
                _trade_row("2024-01-01 10:00:00.500"),
                _trade_row("2024-01-01 10:00:00.700"),
            ),
            "b.csv": _export(_trade_row("2024-01-01 10:00:00.900", "Y")),
        },
    )
    assert _fractions(Trading212Parser.load_from_dir(folder)) == [
        100000,
        500000,
        700000,
        900000,
    ]


def test_each_id_chooses_its_bucket_independently(tmp_path: Path) -> None:
    """Two IDs may end up at a pairing neither export reports.

    The exports disagree about each ID's fraction and nothing says which
    is right, so each ID independently takes the earliest. IDs already
    establish that these are two occurrences, so nothing is lost.
    """
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00.100", "X"),
                _trade_row("2024-01-01 10:00:00.500", "Y"),
            ),
            "b.csv": _export(
                _trade_row("2024-01-01 10:00:00.500", "X"),
                _trade_row("2024-01-01 10:00:00.100", "Y"),
            ),
        },
    )
    assert _fractions(Trading212Parser.load_from_dir(folder)) == [100000, 100000]


def test_conflicting_fractions_take_the_earliest(tmp_path: Path) -> None:
    """An arbitrary but deterministic choice, not a claim about accuracy."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(_trade_row("2024-01-01 10:00:00.500")),
            "b.csv": _export(_trade_row("2024-01-01 10:00:00.100")),
        },
    )
    assert _fractions(Trading212Parser.load_from_dir(folder)) == [100000]
    # The same pair with the file names swapped resolves the same way.
    other = _prepare_files(
        tmp_path / "swapped",
        {
            "a.csv": _export(_trade_row("2024-01-01 10:00:00.100")),
            "b.csv": _export(_trade_row("2024-01-01 10:00:00.500")),
        },
    )
    assert _fractions(Trading212Parser.load_from_dir(other)) == [100000]


# ----- conflicting descriptions of one ID -----


def test_restated_row_keeps_both_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One ID, one instant, one action, two different amounts.

    That is an export restating a row another still reports the old way.
    Neither can be preferred, so both are kept and the double count is
    announced rather than left to be found in the totals.
    """
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(_trade_row("2024-01-01 10:00:00", "X")),
            "b.csv": _export(_trade_row("2024-01-01 10:00:00", "X", total="-11.00")),
        },
    )
    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        transactions = Trading212Parser.load_from_dir(folder)

    assert len(transactions) == 2
    assert "Transaction ID X is described differently" in caplog.text
    assert "a.csv" in caplog.text
    assert "b.csv" in caplog.text


def test_reused_id_across_buy_and_sell_does_not_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The ordinary reuse must stay silent or the warning is useless."""
    rows = _export(
        _trade_row("2024-01-01 10:00:00", "X"),
        _trade_row("2024-04-29 13:20:03", "X", action="Market sell", total="12.00"),
    )
    folder = _prepare_files(tmp_path, {"a.csv": rows, "b.csv": rows})

    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        transactions = Trading212Parser.load_from_dir(folder)

    assert len(transactions) == 2
    assert caplog.text == ""


def test_one_file_holding_both_descriptions_does_not_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An export listing both proves they are separate rows."""
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00", "X"),
                _trade_row("2024-01-01 10:00:00", "X", total="-11.00"),
            ),
            "b.csv": _export(_trade_row("2024-01-01 10:00:00", "X")),
        },
    )
    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        transactions = Trading212Parser.load_from_dir(folder)

    assert len(transactions) == 2
    assert caplog.text == ""


# ----- how hard the merge is allowed to work -----


def _anonymous_export(fractions: list[str]) -> list[list[str]]:
    return _export(
        *(_trade_row(f"2024-01-01 10:00:00.{f}") for f in fractions),
    )


def _identified_export(count: int) -> list[list[str]]:
    return _export(
        *(
            _trade_row("2024-01-01 10:00:00.100", f"ID{index:03d}")
            for index in range(count)
        )
    )


def test_large_bucket_falls_back_to_timestamp_order_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Forty rows needing twenty is 137,846,528,820 candidates.

    Ranking them is out of the question, so the merge takes them by
    timestamp alone and says so. The count and the tax figures are
    unchanged; only the choice of which rows carry which sub-second time
    is.

    The export lists `.400` first and `.000` last, and the result is the
    same as if it had listed them in order, because the single-file phase
    sorts before the merge ever sees the rows.

    The whole-second rows are what show the ordering is not simply
    "earliest". `10:00:00` is the earliest instant present, yet every one
    of those rows is dropped: a row stating a fraction is preferred to
    one stating a whole second, because the two can be the same
    transaction reported at different precision.
    """
    fractions = [f"{value:03d}" for value in (400, 0, 300, 200) for _ in range(10)]
    folder = _prepare_files(
        tmp_path,
        {"a.csv": _anonymous_export(fractions), "b.csv": _identified_export(20)},
    )
    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        transactions = Trading212Parser.load_from_dir(folder)

    assert len(transactions) == 40
    assert "taken by timestamp alone" in caplog.text
    # Twenty identified rows at .100, then the twenty anonymous rows the
    # ordering picks: the .200s and .300s. Every .000 row loses despite
    # 10:00:00 being the earliest instant in the group.
    assert sorted(_fractions(transactions)) == (
        [100000] * 20 + [200000] * 10 + [300000] * 10
    )
    assert 0 not in _fractions(transactions)


def test_one_warning_names_every_approximated_source(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Two exports are approximated, and one warning covers both.

    Both are over the gate, so neither was ranked exactly and either
    could have won a contest it lost. Reporting only the winner, or
    reporting each source as it is tried, would misstate which
    comparisons were approximate.
    """
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _anonymous_export([f"{100 + i:03d}" for i in range(20)] * 2),
            "b.csv": _anonymous_export([f"{500 + i:03d}" for i in range(20)] * 2),
            "c.csv": _identified_export(20),
        },
    )
    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        Trading212Parser.load_from_dir(folder)

    approximated = [
        record for record in caplog.messages if "exact comparison" in record
    ]
    assert len(approximated) == 1
    assert "a.csv" in approximated[0]
    assert "b.csv" in approximated[0]


def test_losing_fallback_source_still_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An approximated source that loses still taints the comparison.

    `a.csv` offers `C(20, 16) = 4,845` candidates, so its subset is the
    prefix rather than its best. Its ten lowest fractions are identical,
    which makes that prefix duplicate-heavy, and `b.csv` wins on distinct
    timestamps. Ranked exactly, `a.csv` would have won instead: it holds
    ten distinct fractions that the prefix never reaches.

    So the gate decided which export supplied the rows. Warning only when
    the winner fell back would have said nothing here, and raising the
    limit would silently change the result.
    """
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _anonymous_export(
                ["001"] * 10 + [f"{100 + index:03d}" for index in range(10)]
            ),
            "b.csv": _export(
                *(
                    _trade_row("2024-01-01 10:00:00.900", f"ID{index:03d}")
                    for index in range(4)
                ),
                *(_trade_row("2024-01-01 10:00:00.200") for _ in range(8)),
                *(
                    _trade_row(f"2024-01-01 10:00:00.{201 + index:03d}")
                    for index in range(8)
                ),
            ),
        },
    )
    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        transactions = Trading212Parser.load_from_dir(folder)

    # b.csv supplies the anonymous rows, yet a.csv is what was approximated.
    assert {
        _source_of(transaction).name
        for transaction in transactions
        if _transaction_id_of(transaction) is None
    } == {"b.csv"}
    assert "may differ from an exact comparison" in caplog.text
    assert "a.csv" in caplog.text


def test_candidate_limit_boundary(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """C(12, 6) is 924 and stays exact; C(13, 6) is 1,716 and does not."""
    at_limit = _prepare_files(
        tmp_path / "at",
        {
            "a.csv": _anonymous_export([f"{100 + i:03d}" for i in range(12)]),
            "b.csv": _identified_export(6),
        },
    )
    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        assert len(Trading212Parser.load_from_dir(at_limit)) == 12
    assert caplog.text == ""

    over_limit = _prepare_files(
        tmp_path / "over",
        {
            "a.csv": _anonymous_export([f"{100 + i:03d}" for i in range(13)]),
            "b.csv": _identified_export(7),
        },
    )
    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        assert len(Trading212Parser.load_from_dir(over_limit)) == 13
    assert "taken by timestamp alone" in caplog.text


def test_large_but_cheap_buckets_stay_exact(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The gate counts candidates, not rows.

    Thirteen rows all needed is one candidate, and a hundred rows needing
    one is a hundred. A gate on bucket size would have sent both to the
    fallback, the first of them being how a single export arrives here.
    """
    all_needed = _prepare_files(
        tmp_path / "all",
        {
            "a.csv": _anonymous_export([f"{100 + i:03d}" for i in range(13)]),
            "b.csv": _anonymous_export(["100"]),
        },
    )
    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        assert len(Trading212Parser.load_from_dir(all_needed)) == 13
    assert caplog.text == ""

    one_needed = _prepare_files(
        tmp_path / "one",
        {
            "a.csv": _anonymous_export([f"{i:03d}" for i in range(100, 200)]),
            "b.csv": _identified_export(99),
        },
    )
    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.trading212"):
        assert len(Trading212Parser.load_from_dir(one_needed)) == 100
    assert caplog.text == ""


# ----- the whole pipeline -----


def test_duplicated_export_matches_reading_it_once(tmp_path: Path) -> None:
    """The likeliest real overlap: the same range exported twice.

    Goes through a real fixture rather than a constructed one, so it
    exercises grouping, bucket choice and the final sort together.
    """
    fixture = Path(__file__).parent / "data" / "2024" / "inputs" / "transactions.csv"
    folder = tmp_path / "inputs"
    folder.mkdir()
    for name in ("first.csv", "second.csv"):
        (folder / name).write_text(fixture.read_text(encoding="utf-8"), "utf-8")

    once = Trading212Parser.load_from_file(fixture)
    twice = Trading212Parser.load_from_dir(folder)

    assert _identity(twice) == _identity(once)


# Prints what the merge can actually vary: which representative was kept
# and in what order. Printing only the data class fields would compare
# equal however the rows were chosen or ordered.
_DETERMINISM_SCRIPT = """
import sys
from pathlib import Path

from cgt_calc.parsers.trading212 import Trading212Parser

for transaction in Trading212Parser.load_from_dir(Path(sys.argv[1])):
    print(
        transaction.datetime.isoformat(),
        transaction.transaction_id,
        transaction.source.file.name,
        transaction.source.index,
        transaction.action,
        transaction.amount,
        sep="|",
    )
"""


def test_result_does_not_depend_on_hash_randomisation(tmp_path: Path) -> None:
    """No set of transactions may decide the output order.

    Once the invalid hash is gone this holds by construction, since the
    groups are dict-ordered and only provenance keys reach a set. The
    test guards against a `set` creeping back in.
    """
    folder = _prepare_files(
        tmp_path,
        {
            "a.csv": _export(
                _trade_row("2024-01-01 10:00:00", "X"),
                _trade_row("2024-01-01 10:00:00.500"),
            ),
            "b.csv": _export(
                _trade_row("2024-01-01 10:00:00", "Y"),
                _trade_row("2024-01-01 10:00:00.100"),
            ),
        },
    )
    runs = {
        subprocess.run(
            [sys.executable, "-c", _DETERMINISM_SCRIPT, str(folder)],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout
        for seed in ("0", "1", "12345")
    }
    assert len(runs) == 1
    # Two IDs fill the two slots the widest export establishes, so the
    # anonymous rows are absorbed as their other representation.
    lines = next(iter(runs)).splitlines()
    assert len(lines) == 2
    # The two rows differ only in ID and provenance, so the probe has to
    # print those for a reordering to be visible at all.
    assert [line.split("|")[1] for line in lines] == ["X", "Y"]


# ----- empty inputs -----


def test_empty_directory_warns_and_returns_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A directory with no exports is reported, not treated as no activity.

    The merge runs on the combined list either way, so this also covers
    it being handed nothing.
    """
    folder = tmp_path / "inputs"
    folder.mkdir()

    with caplog.at_level(logging.WARNING, logger="cgt_calc.parsers.base_parsers"):
        transactions = Trading212Parser.load_from_dir(folder)

    assert transactions == []
    assert "No transactions detected in directory" in caplog.text
    assert "Trading 212" in caplog.text


def test_header_only_export_warns_for_the_directory(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A file with a header and no rows leaves the directory empty.

    `load_from_dir` suppresses the per-file warning and reports once for
    the directory, which is what stops a multi-file export warning for
    every file that happens to cover a quiet period.
    """
    folder = _prepare_files(tmp_path, {"a.csv": [HEADER_2024]})

    with caplog.at_level(logging.WARNING):
        transactions = Trading212Parser.load_from_dir(folder)

    assert transactions == []
    assert caplog.text.count("No transactions detected") == 1
    assert "in directory" in caplog.text


def test_completely_empty_file_is_rejected(tmp_path: Path) -> None:
    """A file with no header at all cannot be parsed, so it raises."""
    folder = tmp_path / "inputs"
    folder.mkdir()
    (folder / "a.csv").write_text("", encoding="utf-8")

    with pytest.raises(ParsingError) as exc:
        Trading212Parser.load_from_dir(folder)

    assert "Trading 212 CSV file is empty" in str(exc.value)


def test_empty_file_alongside_a_real_one_is_rejected(tmp_path: Path) -> None:
    """The empty file is not quietly skipped because another file parsed."""
    folder = _prepare_files(
        tmp_path, {"b.csv": _export(_trade_row("2024-01-01 10:00:00"))}
    )
    (folder / "a.csv").write_text("", encoding="utf-8")

    with pytest.raises(ParsingError) as exc:
        Trading212Parser.load_from_dir(folder)

    assert "Trading 212 CSV file is empty" in str(exc.value)
