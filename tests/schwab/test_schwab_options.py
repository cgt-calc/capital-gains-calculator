"""Tests for UK capital-gains treatment of Schwab written options."""

from __future__ import annotations

import datetime
from decimal import Decimal
import os
from pathlib import Path
import re
import subprocess
import tempfile

import pytest

from cgt_calc import render_latex
from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.exceptions import CalculationError, ParsingError
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CapitalGainsReport,
    CurrencyCode,
    OptionType,
    RuleType,
)
from cgt_calc.parsers.schwab import SchwabParser
from cgt_calc.parsers.schwab_options import parse_option_contract
from cgt_calc.spin_off_handler import SpinOffHandler
from tests.utils import build_cmd, stderr_alerts

HEADER = "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
USD = CurrencyCode("USD")


def _read(*rows: str) -> list[BrokerTransaction]:
    """Read newest-first Schwab rows and return chronological transactions.

    Loaded through the public entry point: option reconciliation runs once per
    declared account, after every row has been stamped with where it was read
    from, and that is what stamping and reconciliation both need.
    """
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "options.csv"
        path.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
        return SchwabParser.load_from_file(path)


def _report(rows: list[str], *, balance_check: bool = False) -> CapitalGainsReport:
    """Calculate a report with one USD equal to one GBP."""
    transactions = _read(*rows)
    dates = {transaction.date for transaction in transactions}
    converter = CurrencyConverter(None, {date: {USD: Decimal(1)} for date in dates})
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=balance_check,
    )
    calculator.prepare_history(transactions)
    return calculator.calculate_capital_gain()


@pytest.mark.parametrize(
    ("symbol", "underlying", "expiry", "strike", "option_type"),
    [
        (
            "META 05/17/2024 500.00 C",
            "META",
            datetime.date(2024, 5, 17),
            Decimal(500),
            OptionType.CALL,
        ),
        (
            "META  240517P00475500",
            "META",
            datetime.date(2024, 5, 17),
            Decimal("475.5"),
            OptionType.PUT,
        ),
    ],
)
def test_parse_option_contract_formats(
    symbol: str,
    underlying: str,
    expiry: datetime.date,
    strike: Decimal,
    option_type: OptionType,
) -> None:
    """Parse both Schwab's readable and canonical OCC option symbols."""
    contract = parse_option_contract(symbol)

    assert contract is not None
    assert contract.underlying == underlying
    assert contract.expiry == expiry
    assert contract.strike == strike
    assert contract.option_type is option_type
    assert contract.multiplier == Decimal(100)


def test_written_call_bought_to_close_reduces_grant_gain() -> None:
    """A closing purchase is an allowable cost of the original grant."""
    report = _report(
        [
            "05/10/2024,Buy to Close,META 05/17/2024 500.00 C,Close,$0.10,1,$0.65,(10.65)",
            "05/01/2024,Sell to Open,META 05/17/2024 500.00 C,Open,$0.30,1,$0.65,$29.35",
        ]
    )

    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal("30.00")
    assert report.allowable_costs == Decimal("11.30")
    assert report.capital_gain == Decimal("18.70")
    entries = report.calculation_log[datetime.date(2024, 5, 1)][
        "option$META call option expiring 2024-05-17 at a strike of 500"
    ]
    assert entries[0].rule_type is RuleType.OPTION
    assert entries[0].quantity == Decimal(1)


def test_written_call_closed_above_the_premium_is_a_loss() -> None:
    """Closing for more than the option was written for is a capital loss."""
    report = _report(
        [
            "05/10/2024,Buy to Close,META 05/17/2024 500.00 C,Close,$3.00,1,$0.65,(300.65)",
            "05/01/2024,Sell to Open,META 05/17/2024 500.00 C,Open,$1.00,1,$0.65,$99.35",
        ]
    )

    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal("100.00")
    assert report.allowable_costs == Decimal("301.30")
    assert report.capital_gain == Decimal(0)
    assert report.capital_loss == Decimal("-201.30")


def test_closing_cost_uses_the_close_date_exchange_rate() -> None:
    """A later closing debit is not converted at the original grant rate."""
    transactions = _read(
        "05/10/2024,Buy to Close,META 05/17/2024 500.00 C,Close,$0.10,1,$0.65,(10.65)",
        "05/01/2024,Sell to Open,META 05/17/2024 500.00 C,Open,$0.30,1,$0.65,$29.35",
    )
    converter = CurrencyConverter(
        None,
        {
            datetime.date(2024, 5, 1): {USD: Decimal(2)},
            datetime.date(2024, 5, 10): {USD: Decimal(1)},
        },
    )
    calculator = CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(),
        CurrentPriceFetcher(converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
    )

    calculator.prepare_history(transactions)
    report = calculator.calculate_capital_gain()

    assert report.disposal_proceeds == Decimal("15.00")
    assert report.capital_gain == Decimal("4.03")


def test_expired_written_call_keeps_grant_gain() -> None:
    """Expiry has no further tax effect for the option writer."""
    report = _report(
        [
            "05/17/2024,Expired,META 05/17/2024 500.00 C,Expiration,,1,,",
            "05/01/2024,Sell to Open,META 05/17/2024 500.00 C,Open,$1.00,1,$0.65,$99.35",
        ]
    )

    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal("100.00")
    assert report.allowable_costs == Decimal("0.65")
    assert report.capital_gain == Decimal("99.35")
    assert report.portfolio == []


def test_assigned_written_call_premium_joins_share_disposal() -> None:
    """A covered-call assignment adds its premium to the share proceeds."""
    report = _report(
        [
            "05/17/2024,Assigned,META 05/17/2024 350.00 C,Assignment,,1,,",
            "05/17/2024,Sell,META,META PLATFORMS INC,$350.00,100,,$35000.00",
            "05/02/2024,Sell to Open,META 05/17/2024 350.00 C,Open,$2.00,1,$0.65,$199.35",
            "05/01/2024,Buy,META,META PLATFORMS INC,$300.00,100,,-$30000.00",
        ]
    )

    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal("35200.00")
    assert report.allowable_costs == Decimal("30000.65")
    assert report.capital_gain == Decimal("5199.35")
    assert all(entry.quantity == 0 for entry in report.portfolio)


def test_assigned_written_put_reduces_share_acquisition_cost() -> None:
    """A written-put premium reduces the basis of assigned shares."""
    report = _report(
        [
            "05/17/2024,Buy,META,META PLATFORMS INC,$50.00,100,,-$5000.00",
            "05/17/2024,Assigned,META 05/17/2024 50.00 P,Assignment,,1,,",
            "05/02/2024,Sell to Open,META 05/17/2024 50.00 P,Open,$1.00,1,$0.65,$99.35",
        ]
    )

    assert report.disposal_count == 0
    assert report.capital_gain == Decimal(0)
    assert len(report.portfolio) == 1
    assert report.portfolio[0].symbol == "META"
    assert report.portfolio[0].quantity == Decimal(100)
    assert report.portfolio[0].amount == Decimal("4900.65")


# Newest first, the way Schwab exports: the put is written on 2 May, assigned
# on 17 May, and its shares settle on 21 May, the day the account is funded.
_FUNDED_ASSIGNED_PUT = [
    "05/21/2024,Buy,META,META PLATFORMS INC,$50.00,100,,-$5000.00",
    "05/21/2024,MoneyLink Deposit,,,,,,$5000.00",
    "05/17/2024,Assigned,META 05/17/2024 50.00 P,Assignment,,1,,",
    "05/02/2024,Sell to Open,META 05/17/2024 50.00 P,Open,$1.00,1,$0.65,$99.35",
]

_PLAIN_FUNDED_PURCHASE = [
    "05/21/2024,Buy,META,META PLATFORMS INC,$50.00,100,,-$5000.00",
    "05/21/2024,MoneyLink Deposit,,,,,,$5000.00",
]


def _passes_balance_check(rows: list[str]) -> bool:
    """Whether the balance check accepts this history."""
    try:
        _report(rows, balance_check=True)
    except CalculationError:
        return False
    return True


def test_assignment_leaves_its_settlement_cash_on_the_settlement_date() -> None:
    """The taxable purchase moves to the assignment date; its cash does not."""
    transactions = _read(*_FUNDED_ASSIGNED_PUT)
    purchase = next(row for row in transactions if row.action is ActionType.BUY)
    settlement = next(
        row
        for row in transactions
        if row.action is ActionType.TRANSFER and row.amount == Decimal("-5000.00")
    )

    assert purchase.date == datetime.date(2024, 5, 17)
    assert purchase.affects_cash_balance is False
    assert settlement.date == datetime.date(2024, 5, 21)
    assert settlement.affects_cash_balance is True
    # Quoted verbatim in docs/brokers/schwab.md, so pin the whole string.
    assert settlement.description == (
        "Settlement of the META put option expiring 2024-05-17 "
        "at a strike of 50 assigned on 2024-05-17"
    )


def test_funded_assigned_put_passes_the_balance_check() -> None:
    """Cash leaves on 21 May, and the account is funded on 21 May."""
    report = _report(_FUNDED_ASSIGNED_PUT, balance_check=True)

    assert len(report.portfolio) == 1
    assert report.portfolio[0].quantity == Decimal(100)
    assert report.portfolio[0].amount == Decimal("4900.65")


def test_assigned_put_without_its_funding_deposit_still_errors() -> None:
    """An account that really cannot pay is still reported, on the pay date."""
    rows = [row for row in _FUNDED_ASSIGNED_PUT if "MoneyLink" not in row]

    with pytest.raises(
        CalculationError, match=r"negative balance\(-4900.65\)"
    ) as error:
        _report(rows, balance_check=True)

    message = str(error.value)
    assert "2024-05-21 Transfer" in message
    # The taxable purchase moves no cash, so listing it under a running
    # balance it did not change would only mislead.
    assert "Buy 100 META" not in message


@pytest.mark.parametrize("deposit_first", [True, False])
def test_assigned_put_passes_the_balance_check_in_either_export_order(
    deposit_first: bool,
) -> None:
    """An assignment must not be judged more harshly than an ordinary buy.

    Each pool is judged after its last row of the day, so where the funding
    deposit sits in the export no longer decides the outcome. Both orders
    pass, for the assignment and the plain purchase alike.
    """

    def ordered(rows: list[str]) -> list[str]:
        return rows if deposit_first else [rows[1], rows[0], *rows[2:]]

    assert _passes_balance_check(ordered(_FUNDED_ASSIGNED_PUT))
    assert _passes_balance_check(ordered(_PLAIN_FUNDED_PURCHASE))


def test_assigned_call_leaves_its_proceeds_on_the_settlement_date() -> None:
    """Proceeds of an assigned call arrive when Schwab actually paid them."""
    transactions = _read(
        "05/21/2024,Sell,META,META PLATFORMS INC,$350.00,100,,$35000.00",
        "05/17/2024,Assigned,META 05/17/2024 350.00 C,Assignment,,1,,",
        "05/02/2024,Sell to Open,META 05/17/2024 350.00 C,Open,$2.00,1,$0.65,$199.35",
        "05/01/2024,Buy,META,META PLATFORMS INC,$300.00,100,,-$30000.00",
    )
    sale = next(row for row in transactions if row.action is ActionType.SELL)
    settlement = next(row for row in transactions if row.action is ActionType.TRANSFER)

    assert sale.date == datetime.date(2024, 5, 17)
    assert sale.affects_cash_balance is False
    assert settlement.date == datetime.date(2024, 5, 21)
    assert settlement.amount == Decimal("35000.00")


def test_partial_close_and_assignment_split_the_opening_premium() -> None:
    """Only the non-assigned part remains a disposal of the option itself."""
    transactions = _read(
        "05/17/2024,Assigned,META 05/17/2024 350.00 C,Assignment,,1,,",
        "05/17/2024,Sell,META,META PLATFORMS INC,$350.00,100,,$35000.00",
        "05/10/2024,Buy to Close,META 05/17/2024 350.00 C,Close,$0.25,1,$0.65,(25.65)",
        "05/02/2024,Sell to Open,META 05/17/2024 350.00 C,Open,$2.00,2,$1.30,$398.70",
        "05/01/2024,Buy,META,META PLATFORMS INC,$300.00,100,,-$30000.00",
    )

    grant = next(
        transaction
        for transaction in transactions
        if transaction.action is ActionType.OPTION_GRANT
    )
    sale = next(
        transaction
        for transaction in transactions
        if transaction.action is ActionType.SELL
    )

    assert grant.written_option_tax is not None
    assert grant.written_option_tax.taxable_quantity == Decimal(1)
    assert grant.written_option_tax.close_costs[0].amount == Decimal("25.65")
    assert sale.capital_adjustments[0].net_amount == Decimal("199.35")
    assert sale.capital_adjustments[0].fees == Decimal("0.65")


def test_unmatched_close_requests_the_missing_opening_rows() -> None:
    """Do not silently put a closing debit in the wrong tax year."""
    with pytest.raises(ParsingError, match="missing Sell to Open rows"):
        _read(
            "05/10/2024,Buy to Close,META 05/17/2024 500.00 C,Close,$0.10,1,$0.65,(10.65)"
        )


def test_purchased_options_are_rejected_explicitly() -> None:
    """Long-option rows should not be mistaken for ordinary share trades."""
    with pytest.raises(ParsingError, match="purchased-option transaction"):
        _read(
            "05/01/2024,Buy to Open,META 05/17/2024 500.00 C,Open,$1.00,1,$0.65,(100.65)"
        )


def test_written_option_is_rendered_as_an_option_grant(tmp_path: Path) -> None:
    """The detailed report should not describe option contracts as shares."""
    report = _report(
        [
            "05/17/2024,Expired,META 05/17/2024 500.00 C,Expiration,,1,,",
            "05/01/2024,Sell to Open,META 05/17/2024 500.00 C,Open,$1.00,1,$0.65,$99.35",
        ]
    )
    output = tmp_path / "option-report.pdf"

    render_latex.render_pdf(report, output, skip_pdflatex=True)

    rendered = output.with_suffix(".tex").read_text(encoding="utf-8")
    assert "Disposal 1: grant of 1" in rendered
    assert "contract of the META call option" in rendered
    assert "expiring 2024-05-17 at a strike of 500" in rendered
    assert "does not change the Section 104 holding" in rendered


def test_later_posted_assignment_share_row_keeps_the_assignment_date() -> None:
    """Schwab settles the shares days later, in a possibly later tax year."""
    transactions = _read(
        "04/07/2025,Sell,META,META PLATFORMS INC,$100.00,100,,$10000.00",
        "04/04/2025,Assigned,META 04/04/2025 100.00 C,Assignment,,1,,",
        "04/01/2025,Sell to Open,META 04/04/2025 100.00 C,Open,$1.00,1,$0.65,$99.35",
        "03/01/2025,Buy,META,META PLATFORMS INC,$90.00,100,,-$9000.00",
    )
    sales = [row for row in transactions if row.action is ActionType.SELL]

    assert len(sales) == 1
    assert sales[0].date == datetime.date(2025, 4, 4)
    assert sales[0].amount == Decimal("10000.00")


def test_assignment_settled_after_5_april_stays_in_its_own_tax_year() -> None:
    """The disposal belongs to the year the option was assigned in."""
    report = _report(
        [
            "04/07/2025,Sell,META,META PLATFORMS INC,$100.00,100,,$10000.00",
            "04/04/2025,Assigned,META 04/04/2025 100.00 C,Assignment,,1,,",
            "04/01/2025,Sell to Open,META 04/04/2025 100.00 C,Open,$1.00,1,$0.65,$99.35",
            "03/01/2025,Buy,META,META PLATFORMS INC,$90.00,100,,-$9000.00",
        ]
    )

    assert report.disposal_count == 1
    assert report.disposal_proceeds == Decimal("10100.00")
    assert report.allowable_costs == Decimal("9000.65")
    assert all(entry.quantity == 0 for entry in report.portfolio)


def test_two_candidate_share_rows_are_refused() -> None:
    """Two rows could be the settlement, and guessing would pick one."""
    with pytest.raises(ParsingError, match="Cannot identify which META"):
        _read(
            "05/17/2024,Sell,META,META PLATFORMS INC,$350.00,100,,$35000.00",
            "05/17/2024,Sell,META,META PLATFORMS INC,$350.00,100,,$35000.00",
            "05/17/2024,Assigned,META 05/17/2024 350.00 C,Assignment,,1,,",
            "05/02/2024,Sell to Open,META 05/17/2024 350.00 C,Open,$2.00,1,$0.65,$199.35",
        )


def test_plausible_but_unreconciled_share_row_is_refused() -> None:
    """A near-match must stop, not be replaced by an invented second row.

    Same symbol, action, quantity and settlement window, but the amount does
    not add up to the strike. Treating it as an unrelated trade leaves it in
    the history beside the shares invented for the assignment, disposing of
    the same 100 shares twice.
    """
    with pytest.raises(ParsingError, match="do not reconcile"):
        _read(
            "05/17/2024,Sell,META,META PLATFORMS INC,$350.00,100,,$34000.00",
            "05/17/2024,Assigned,META 05/17/2024 350.00 C,Assignment,,1,,",
            "05/02/2024,Sell to Open,META 05/17/2024 350.00 C,Open,$2.00,1,$0.65,$199.35",
        )


def test_option_opened_and_closed_in_different_export_files() -> None:
    """A long history arrives as several files; the lifecycle spans them."""
    with tempfile.TemporaryDirectory() as directory:
        export = Path(directory)
        (export / "2024-05.csv").write_text(
            HEADER
            + "05/01/2024,Sell to Open,META 05/17/2024 500.00 C,Open,$1.00,1,$0.65,$99.35\n",
            encoding="utf-8",
        )
        (export / "2024-06.csv").write_text(
            HEADER
            + "06/10/2024,Buy to Close,META 05/17/2024 500.00 C,Close,$0.10,1,$0.65,(10.65)\n",
            encoding="utf-8",
        )
        transactions = SchwabParser.load_from_dir(export)

    grant = next(row for row in transactions if row.action is ActionType.OPTION_GRANT)
    accounts = {row.source.account for row in transactions if row.source is not None}

    assert grant.written_option_tax is not None
    assert grant.written_option_tax.taxable_quantity == Decimal(1)
    assert [cost.amount for cost in grant.written_option_tax.close_costs] == [
        Decimal("10.65")
    ]
    assert len(accounts) == 1
    assert None not in accounts


@pytest.mark.parametrize(
    "symbol",
    [
        "AAPL1 05/17/2024 100.00 C",
        "META1  240517C00500000",
        "SPX 05/17/2024 5000.00 C",
        "SPXW  240517C05000000",
        "XND 05/17/2024 180.00 C",
        "NQX  240517C05000000",
        "NDXP  240517C18000000",
        "RUTW 05/17/2024 2000.00 P",
        "MRUT 05/17/2024 200.00 P",
        "VIXW  240517C00020000",
    ],
)
def test_contract_not_delivering_100_shares_is_refused(symbol: str) -> None:
    """An adjusted or cash-settled contract is not 100 shares of anything."""
    assert parse_option_contract(symbol) is None

    with pytest.raises(ParsingError, match="Cannot parse the option contract"):
        _read(f"05/01/2024,Sell to Open,{symbol},Open,$1.00,1,$0.65,$99.35")


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (
            "05/01/2024,Sell to Open,META 05/17/2024 500.00 C,Open,$1.00,1.5,$0.65,$149.35",
            "Invalid contract quantity",
        ),
        (
            "05/01/2024,Sell to Open,META 05/17/2024 500.00 C,Open,$1.00,1,-$0.65,$100.65",
            "Invalid fees",
        ),
        (
            "05/01/2024,Sell to Open,META 05/17/2024 500.00 C,Open,$1.00,1,$0.65,(99.35)",
            "expected non-negative premium proceeds",
        ),
        (
            "05/17/2024,Expired,META 05/17/2024 500.00 C,Expiration,,1,,$5.00",
            "expected no price, fees or cash amount",
        ),
        (
            "05/01/2024,Sell to Open,META 05/17/2024 500.00 C,Open,$1.00,1,$0.65,$50.00",
            "Option premium does not match",
        ),
    ],
)
def test_malformed_option_row_is_refused(row: str, message: str) -> None:
    """A row whose cash does not describe the contract stops the import."""
    with pytest.raises(ParsingError, match=message):
        _read(row)


def test_schwab_options_run_from_csv_to_report(tmp_path: Path) -> None:
    """The whole path: a real export, the CLI, and the rendered report."""
    output = tmp_path / "schwab-options.pdf"
    pdf_enabled = bool(os.getenv("ENABLE_PDFLATEX"))
    cmd = build_cmd(
        "--year",
        "2024",
        "--schwab-file",
        "tests/schwab/data/options/transactions.csv",
        "--output",
        str(output),
        keep_tex=not pdf_enabled,
    )

    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)

    assert result.returncode == 0, result.stderr
    assert stderr_alerts(result.stderr) == []
    # The lapsed grant, and the assigned call folded into the share disposal.
    assert re.search(r"^\s+Disposals:\s+2$", result.stdout, re.MULTILINE) is not None
    if pdf_enabled:
        assert output.is_file()
    else:
        source = output.with_suffix(".tex").read_text(encoding="utf-8")
        assert "META call option expiring 2024-05-17" in source
        assert "does not change the Section 104 holding" in source


def test_assignment_without_a_settlement_row_is_refused() -> None:
    """Inventing the shares would double the disposal if the row turns up."""
    with pytest.raises(ParsingError, match="no META transaction that settles"):
        _read(
            "05/17/2024,Assigned,META 05/17/2024 350.00 C,Assignment,,1,,",
            "05/02/2024,Sell to Open,META 05/17/2024 350.00 C,Open,$2.00,1,$0.65,$199.35",
            "05/01/2024,Buy,META,META PLATFORMS INC,$300.00,100,,-$30000.00",
        )


def test_settlement_posted_after_a_long_weekend_is_matched() -> None:
    """Settlement runs in business days, so it can be five calendar days out.

    Assigned on Friday 24 May 2024 and settled on Wednesday 29 May: Memorial
    Day fell on the Monday, and the trade predates the move to T+1 on 28 May,
    so this is an ordinary T+2 of its time.
    """
    transactions = _read(
        "05/29/2024,Sell,META,META PLATFORMS INC,$350.00,100,,$35000.00",
        "05/24/2024,Assigned,META 05/24/2024 350.00 C,Assignment,,1,,",
        "05/02/2024,Sell to Open,META 05/24/2024 350.00 C,Open,$2.00,1,$0.65,$199.35",
        "05/01/2024,Buy,META,META PLATFORMS INC,$300.00,100,,-$30000.00",
    )
    sales = [row for row in transactions if row.action is ActionType.SELL]

    assert len(sales) == 1
    assert sales[0].date == datetime.date(2024, 5, 24)
    assert sales[0].quantity == Decimal(100)


def test_two_assignments_of_one_contract_on_one_day_are_refused() -> None:
    """Each assignment settles separately, and pairing them up is guesswork."""
    with pytest.raises(ParsingError, match="more than one assignment"):
        _read(
            "05/17/2024,Sell,META,META PLATFORMS INC,$350.00,100,,$35000.00",
            "05/17/2024,Sell,META,META PLATFORMS INC,$350.00,100,,$35000.00",
            "05/17/2024,Assigned,META 05/17/2024 350.00 C,Assignment,,1,,",
            "05/17/2024,Assigned,META 05/17/2024 350.00 C,Assignment,,1,,",
            "05/02/2024,Sell to Open,META 05/17/2024 350.00 C,Open,$2.00,2,$1.30,$398.70",
            "05/01/2024,Buy,META,META PLATFORMS INC,$300.00,200,,-$60000.00",
        )
