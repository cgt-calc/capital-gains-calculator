"""Tests for assets received as income in the RAW format: REWARD rows."""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import CalculationError, ParsingError
from cgt_calc.model import ActionType, RuleType
from cgt_calc.parsers.raw import RawParser, RawTransaction
from cgt_calc.render_text import render_text
from tests.general.test_calc import create_calculator, get_report
from tests.utils import report_text

if TYPE_CHECKING:
    from pathlib import Path

    from cgt_calc.model import CapitalGainsReport

HEADER = ["date", "action", "symbol", "quantity", "price", "fees", "currency"]

ALLOWANCE_NOTE = (
    "The £1,000 trading allowance covers trading and miscellaneous income "
    "together. If that income is £1,000 or less in all, it does not need to be "
    "reported."
)

# 100 units worth 10.00 when received.
REWARD = ["2024-07-08", "REWARD", "CRO", "100", "0.10", "0", "GBP"]


def _read(tmp_path: Path, rows: list[list[str]]) -> list[RawTransaction]:
    raw_file = tmp_path / "raw.csv"
    with raw_file.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows([HEADER, *rows])
    return RawParser.load_from_file(raw_file, show_parsing_msg=False)


def _report(tmp_path: Path, rows: list[list[str]]) -> CapitalGainsReport:
    return get_report(create_calculator(tax_year=2024), list(_read(tmp_path, rows)))


def test_a_reward_row_is_worth_its_market_value(tmp_path: Path) -> None:
    """The amount is the value received, which is the income."""
    (row,) = _read(tmp_path, [REWARD])

    assert row.action is ActionType.REWARD
    assert row.symbol == "CRO"
    assert row.amount == Decimal("10.00")


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        pytest.param(
            ["2024-07-08", "REWARD", "", "1", "10", "0", "GBP"],
            "names the asset received",
            id="no symbol",
        ),
        pytest.param(
            ["2024-07-08", "REWARD", "GBP", "1", "10", "0", "GBP"],
            "write an INTEREST row",
            id="cash",
        ),
        pytest.param(
            ["2024-07-08", "REWARD", "CRO", "0", "10", "0", "GBP"],
            "positive quantity",
            id="no units",
        ),
        pytest.param(
            ["2024-07-08", "REWARD", "CRO", "1", "0", "0", "GBP"],
            "positive price",
            id="price of zero",
        ),
        pytest.param(
            ["2024-07-08", "REWARD", "CRO", "1", "", "0", "GBP"],
            "positive price",
            id="no price",
        ),
        pytest.param(
            ["2024-07-08", "REWARD", "CRO", "1", "10", "1", "GBP"],
            "after any fee",
            id="fee",
        ),
    ],
)
def test_an_invalid_reward_row_is_refused(
    tmp_path: Path, row: list[str], expected: str
) -> None:
    """Each mistake is named, against its row."""
    with pytest.raises(ParsingError) as exc:
        _read(tmp_path, [row])

    assert "row 2" in str(exc.value)
    assert expected in str(exc.value)


def test_a_reward_costs_its_value_when_sold(tmp_path: Path) -> None:
    """100 units received for 10.00 and sold for 15.00 gain 5.00."""
    report = _report(
        tmp_path,
        [REWARD, ["2024-08-01", "SELL", "CRO", "100", "0.15", "0", "GBP"]],
    )

    assert report.capital_gain == Decimal(5)
    assert report.total_misc_income == Decimal(10)


def test_a_reward_is_matched_with_a_sale_before_it(tmp_path: Path) -> None:
    """A reward within 30 days of a sale is the units the sale is matched with.

    The sale of 100 units for 12.00 takes the reward's 10.00 as its cost, not
    the 20.00 of the pool, which keeps the units bought first.
    """
    report = _report(
        tmp_path,
        [
            ["2024-05-01", "TRANSFER", "", "1", "20", "0", "GBP"],
            ["2024-05-01", "BUY", "CRO", "100", "0.20", "0", "GBP"],
            ["2024-07-01", "SELL", "CRO", "100", "0.12", "0", "GBP"],
            REWARD,
        ],
    )

    (sale,) = report.calculation_log[dt.date(2024, 7, 1)]["sell$CRO"]
    assert sale.rule_type is RuleType.BED_AND_BREAKFAST
    assert sale.allowable_cost == Decimal(10)
    assert report.capital_gain == Decimal(2)


def test_a_reward_adds_no_cash_to_the_balance(tmp_path: Path) -> None:
    """Nothing was paid in, so buying for 0.10 goes negative."""
    with pytest.raises(CalculationError, match=r"negative balance of -0\.1"):
        _report(
            tmp_path,
            [REWARD, ["2024-07-08", "BUY", "CRO", "1", "0.10", "0", "GBP"]],
        )


def test_only_the_rewards_of_the_tax_year_are_its_income(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two rewards in the year add up; one after it is the next year's.

    The first pass totals the year's income by asset, as it does dividends.
    """
    report = _report(
        tmp_path,
        [
            REWARD,
            ["2024-07-15", "REWARD", "CRO", "50", "0.12", "0", "GBP"],
            ["2025-05-01", "REWARD", "CRO", "100", "0.20", "0", "GBP"],
        ],
    )

    assert report.total_misc_income == Decimal(16)
    summary = capsys.readouterr().out.split("Miscellaneous income\n")[1]
    assert summary.splitlines()[0].strip() == "CRO: 16.00 (GBP)"


@pytest.mark.parametrize(
    ("tax_year", "price", "income", "income_line", "allowance_note"),
    [
        pytest.param(
            2024,
            "10",
            45000,
            "Income £45,000.00, less the £12,570 Personal Allowance: taxable "
            "income £32,430.00",
            "£1,000.00 of miscellaneous income is not added",
            id="covered by the trading allowance",
        ),
        pytest.param(
            2024,
            "15",
            45000,
            "Income £45,000.00, plus £500.00 miscellaneous income from these "
            "files, less the £12,570 Personal Allowance: taxable income "
            "£32,930.00",
            "£1,000.00 of miscellaneous income is not added",
            id="above the trading allowance",
        ),
        pytest.param(
            2016,
            "15",
            # The 2016/17 basic rate limit is £32,000, which £45,000 uses up.
            30000,
            "Income £30,000.00, plus £1,500.00 miscellaneous income from these "
            "files, less the £11,000 Personal Allowance: taxable income "
            "£20,500.00",
            None,
            id="before the trading allowance",
        ),
    ],
)
def test_the_tax_estimate_adds_the_income_above_the_trading_allowance(
    tmp_path: Path,
    tax_year: int,
    price: str,
    income: int,
    income_line: str,
    allowance_note: str | None,
) -> None:
    """The income uses up the basic rate band, less the trading allowance.

    The gain is over the annual exempt amount, so the tax is estimated.
    """
    rows = [
        [f"{tax_year}-07-08", "REWARD", "CRO", "100", price, "0", "GBP"],
        [f"{tax_year}-08-01", "SELL", "CRO", "100", "300", "0", "GBP"],
    ]
    report = get_report(
        create_calculator(tax_year=tax_year), list(_read(tmp_path, rows))
    )

    text = " ".join(render_text(report, Decimal(income)).split())

    assert income_line in text
    if allowance_note is None:
        assert "trading allowance" not in text
    else:
        assert allowance_note in text
        assert "add it to --income" in text
        assert ALLOWANCE_NOTE in text


def test_a_report_without_rewards_shows_no_miscellaneous_income(
    tmp_path: Path,
) -> None:
    """The section is left out, rather than shown as £0.00."""
    report = _report(
        tmp_path,
        [
            ["2024-05-01", "TRANSFER", "", "1", "20", "0", "GBP"],
            ["2024-05-01", "BUY", "CRO", "100", "0.20", "0", "GBP"],
        ],
    )

    assert "Miscellaneous income" not in render_text(report)
    assert "miscellaneous income" not in report_text(report, tmp_path)


def test_the_pdf_shows_the_income(tmp_path: Path) -> None:
    """The summary gives the total, and the events each asset's income by month."""
    report = _report(
        tmp_path,
        [
            REWARD,
            ["2024-07-15", "REWARD", "CRO", "50", "0.12", "0", "GBP"],
            ["2024-07-15", "REWARD", "ETH", "0.01", "2400", "0", "GBP"],
        ],
    )

    pdf = report_text(report, tmp_path)

    assert "Number of miscellaneous income transactions 2" in pdf
    assert "Total amount of miscellaneous income £40.00" in pdf
    assert "Miscellaneous income 1: CRO for £16.00" in pdf
    assert "Miscellaneous income 2: ETH for £24.00" in pdf
    assert ALLOWANCE_NOTE in pdf


def test_a_reward_in_another_currency_is_income_in_pounds(tmp_path: Path) -> None:
    """USD 2,491.40 at the May 2024 rate of 1.2457 is £2,000.00, income and cost."""
    report = _report(
        tmp_path, [["2024-05-01", "REWARD", "ETH", "1", "2491.40", "0", "USD"]]
    )

    assert report.total_misc_income == Decimal("2000.00")
    (entry,) = report.calculation_log[dt.date(2024, 5, 1)]["buy$ETH"]
    assert entry.allowable_cost == Decimal("2000.00")
