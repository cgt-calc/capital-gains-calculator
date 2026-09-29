"""Tests for attributing withheld tax to the dividend it was taken from."""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging

import pytest

from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.exceptions import CalculationError
from cgt_calc.initial_prices import InitialPrices
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode
from cgt_calc.spin_off_handler import SpinOffHandler

from .calc_test_data import dividend_tax_transaction, dividend_transaction

GBP = CurrencyCode("GBP")
USD = CurrencyCode("USD")
PLN = CurrencyCode("PLN")

# The tax year under test, a dividend inside it, and withholding placed at
# various distances from that dividend: two days away, exactly at the
# thirty-day limit, one day past it, and far outside.
TAX_YEAR = 2024
DIVIDEND_DATE = datetime.date(2024, 6, 3)
LATER_DATE = DIVIDEND_DATE + datetime.timedelta(days=2)
LIMIT_DATE = DIVIDEND_DATE + datetime.timedelta(days=30)
BEYOND_LIMIT_DATE = DIVIDEND_DATE + datetime.timedelta(days=31)

# A payment at the very end of 2024/25 whose withholding posts in 2025/26.
BORDER_DIVIDEND_DATE = datetime.date(2025, 4, 2)
BORDER_TAX_DATE = datetime.date(2025, 4, 8)


def _calculator(
    transactions: list[BrokerTransaction],
    tax_year: int = TAX_YEAR,
    *,
    autoconvert_currency: bool = False,
    gbp_prices: dict[datetime.date, dict[CurrencyCode, Decimal]] | None = None,
) -> CapitalGainsCalculator:
    """Create a calculator with supplied rates or flat 1:1 USD rates."""
    rates = (
        gbp_prices
        if gbp_prices is not None
        else {t.date: {USD: Decimal(1)} for t in transactions}
    )
    currency_converter = CurrencyConverter(None, rates)
    return CapitalGainsCalculator(
        tax_year,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=[],
        balance_check=False,
        autoconvert_currency=autoconvert_currency,
    )


def _reported(
    transactions: list[BrokerTransaction],
    tax_year: int = TAX_YEAR,
    *,
    autoconvert_currency: bool = False,
    gbp_prices: dict[datetime.date, dict[CurrencyCode, Decimal]] | None = None,
) -> list[tuple[datetime.date, Decimal, Decimal, bool]]:
    """Return date, amount, tax and whether a treaty applied, per reported row."""
    calculator = _calculator(
        transactions,
        tax_year,
        autoconvert_currency=autoconvert_currency,
        gbp_prices=gbp_prices,
    )
    report = calculator.calculate(transactions)
    rows = []
    for entries in report.calculation_log_yields.values():
        for key, log in entries.items():
            if not key.startswith("dividend$"):
                continue
            for entry in log:
                dividend = entry.dividend
                assert dividend is not None
                rows.append(
                    (
                        dividend.date,
                        dividend.amount,
                        dividend.tax_at_source,
                        dividend.tax_treaty is not None,
                    )
                )
    return sorted(rows)


def _unattributed_warnings(
    transactions: list[BrokerTransaction],
    caplog: pytest.LogCaptureFixture,
    tax_year: int = TAX_YEAR,
) -> list[str]:
    """Run the calculation and return the unattributed tax warnings."""
    # A test that also reports the run has already made the calculator warn
    # once, and those records must not be counted again here.
    caplog.clear()
    calculator = _calculator(transactions, tax_year)
    with caplog.at_level(logging.WARNING, logger="cgt_calc.income"):
        calculator.calculate(transactions)
    return [
        record.message
        for record in caplog.records
        if "left out of the report" in record.message
    ]


def _summary_dividend_lines(
    transactions: list[BrokerTransaction],
    capsys: pytest.CaptureFixture[str],
    tax_year: int = TAX_YEAR,
) -> list[str]:
    """Return the dividend lines of the first pass summary."""
    # Discard whatever an earlier run in the same test printed.
    capsys.readouterr()
    calculator = _calculator(transactions, tax_year)
    calculator.prepare_history(transactions)
    lines = capsys.readouterr().out.splitlines()
    header = next((i for i, line in enumerate(lines) if "Dividends" in line), None)
    if header is None:
        return []
    section = lines[header + 1 :]
    return section[: section.index("")] if "" in section else section


def _dividend_at(
    date: datetime.date,
    amount: float,
    broker: str,
    currency: CurrencyCode = USD,
) -> BrokerTransaction:
    """Create a dividend held at a named broker."""
    return BrokerTransaction(
        date,
        ActionType.DIVIDEND,
        symbol="FOO",
        description="Dividend",
        quantity=None,
        price=None,
        fees=Decimal(0),
        amount=Decimal(str(amount)),
        currency=currency,
        broker=broker,
    )


def _dividend_tax_at(
    date: datetime.date, amount: float, broker: str
) -> BrokerTransaction:
    """Create withholding taken by a named broker."""
    return BrokerTransaction(
        date,
        ActionType.DIVIDEND_TAX,
        symbol="FOO",
        description="Dividend tax",
        quantity=None,
        price=None,
        fees=Decimal(0),
        amount=Decimal(str(-amount)),
        currency=USD,
        broker=broker,
    )


def test_late_correction_belongs_to_the_payment_it_corrects(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A correction goes to the payment before it, not the nearer one after.

    A monthly payer leaves a late correction closer to the following payment
    than to the one it corrects, so matching on distance alone moved it, and
    both payments then failed the treaty check.
    """
    first = datetime.date(2024, 6, 3)
    correction = datetime.date(2024, 6, 22)  # 19 days after, 11 days before
    second = datetime.date(2024, 7, 3)
    transactions = [
        dividend_transaction(first, "FOO", 100),
        dividend_tax_transaction(first, "FOO", 30),
        dividend_tax_transaction(correction, "FOO", -15),
        dividend_transaction(second, "FOO", 200),
        dividend_tax_transaction(second, "FOO", 30),
    ]

    assert _reported(transactions) == [
        (first, Decimal(100), Decimal(-15), True),
        (second, Decimal(200), Decimal(-30), True),
    ]
    assert _unattributed_warnings(transactions, caplog) == []


def test_tax_on_a_payment_day_is_never_in_doubt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax dated on a payment belongs to it, whatever else lies nearby.

    A monthly payer always has a second payment inside the window, and that
    must not make the ordinary case ambiguous.
    """
    first = datetime.date(2024, 6, 3)
    second = first + datetime.timedelta(days=30)
    transactions = [
        dividend_transaction(first, "FOO", 100),
        dividend_tax_transaction(first, "FOO", 15),
        dividend_transaction(second, "FOO", 200),
        dividend_tax_transaction(second, "FOO", 30),
    ]

    assert _reported(transactions) == [
        (first, Decimal(100), Decimal(-15), True),
        (second, Decimal(200), Decimal(-30), True),
    ]
    assert _unattributed_warnings(transactions, caplog) == []


def _tax_in_currency(
    date: datetime.date,
    amount: float,
    currency: CurrencyCode,
    broker: str = "A",
) -> BrokerTransaction:
    """Create withholding denominated in a given currency."""
    return BrokerTransaction(
        date,
        ActionType.DIVIDEND_TAX,
        symbol="FOO",
        description="Dividend tax",
        quantity=None,
        price=None,
        fees=Decimal(0),
        amount=Decimal(str(amount)),
        currency=currency,
        broker=broker,
    )


@pytest.mark.parametrize(
    ("amount", "tickers"),
    [
        pytest.param(15, [], id="refund"),
        pytest.param(-15, [], id="withholding"),
        pytest.param(-15, ["FOO"], id="withholding-on-a-bond-fund"),
    ],
)
def test_tax_in_another_currency_than_its_dividend_is_refused(
    amount: int, tickers: list[str]
) -> None:
    """Tax is converted at the dividend's rate, so the two must agree.

    The check once sat inside the branch for tax deducted from an ordinary
    holding, so a refund, or any tax on a bond fund, was converted as though
    it were in the dividend's currency and came out at the wrong figure.
    """
    transactions = [
        _dividend_at(DIVIDEND_DATE, 100, broker="A"),
        _tax_in_currency(LATER_DATE, amount, CurrencyCode("GBP")),
    ]
    gbp_prices = {t.date: {USD: Decimal(2)} for t in transactions}
    currency_converter = CurrencyConverter(None, gbp_prices)
    calculator = CapitalGainsCalculator(
        TAX_YEAR,
        currency_converter,
        IsinConverter(),
        CurrentPriceFetcher(currency_converter, {}, {}),
        SpinOffHandler(),
        InitialPrices(),
        interest_fund_tickers=tickers,
        balance_check=False,
    )
    calculator.prepare_history(transactions)

    with pytest.raises(CalculationError, match="currencies do not match"):
        calculator.calculate_capital_gain()


def test_ambiguous_tax_warns_in_the_year_of_each_payment(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A payment left short hears about it, in either year it falls in.

    A withholding just after 5 April can belong to a payment on either side
    of the boundary. Scoping the warning to the year the tax was taken in
    left the earlier year reporting its dividend with no tax and saying
    nothing about why.
    """
    transactions = [
        dividend_transaction(datetime.date(2025, 4, 2), "FOO", 100),
        dividend_tax_transaction(datetime.date(2025, 4, 6), "FOO", 15),
        dividend_transaction(datetime.date(2025, 4, 10), "FOO", 200),
    ]

    assert _reported(transactions, tax_year=2024) == [
        (datetime.date(2025, 4, 2), Decimal(100), Decimal(0), False)
    ]
    assert len(_unattributed_warnings(transactions, caplog, tax_year=2024)) == 1
    assert len(_unattributed_warnings(transactions, caplog, tax_year=2025)) == 1


def test_tax_two_payments_could_claim_is_left_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A correction near two payments is refused rather than guessed at.

    A credit four weeks after one payment and two days before the next could
    be a late correction of the first or ordinary tax on the second. The
    export does not say which, so neither payment is given it.
    """
    first = datetime.date(2024, 6, 3)
    correction = datetime.date(2024, 7, 1)
    second = datetime.date(2024, 7, 3)
    transactions = [
        dividend_transaction(first, "FOO", 100),
        dividend_tax_transaction(first, "FOO", 30),
        dividend_tax_transaction(correction, "FOO", -15),
        dividend_transaction(second, "FOO", 200),
        dividend_tax_transaction(second, "FOO", 30),
    ]

    assert _reported(transactions) == [
        (first, Decimal(100), Decimal(-30), False),
        (second, Decimal(200), Decimal(-30), True),
    ]

    warnings = _unattributed_warnings(transactions, caplog)

    assert len(warnings) == 1
    assert "the dividend on 2024-06-03 or 2024-07-03" in warnings[0]
    assert "Date it in the export on the one it belongs to" in warnings[0]


def test_further_withholding_two_payments_could_claim_is_left_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A second debit near two payments is refused, as a credit would be.

    Brokers map an adjustment and an ordinary withholding to one action, so
    a further debit cannot be told from tax on the payment that follows it.
    """
    first = datetime.date(2024, 6, 3)
    adjustment = datetime.date(2024, 7, 1)
    second = datetime.date(2024, 7, 3)
    transactions = [
        dividend_transaction(first, "FOO", 100),
        dividend_tax_transaction(first, "FOO", 10),
        dividend_tax_transaction(adjustment, "FOO", 5),
        dividend_transaction(second, "FOO", 200),
        dividend_tax_transaction(second, "FOO", 30),
    ]

    assert _reported(transactions) == [
        (first, Decimal(100), Decimal(-10), False),
        (second, Decimal(200), Decimal(-30), True),
    ]
    assert len(_unattributed_warnings(transactions, caplog)) == 1


def test_tax_left_out_is_still_summarised(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tax no payment can claim still shows the broker took it."""
    first = datetime.date(2024, 6, 3)
    correction = datetime.date(2024, 7, 1)
    second = datetime.date(2024, 7, 3)
    transactions = [
        dividend_transaction(first, "FOO", 100),
        dividend_tax_transaction(first, "FOO", 10),
        dividend_tax_transaction(correction, "FOO", 5),
        dividend_transaction(second, "FOO", 200),
        dividend_tax_transaction(second, "FOO", 30),
    ]

    assert _summary_dividend_lines(transactions, capsys) == [
        "  FOO: 300.00, excluding 45.00 taxed at source (USD)"
    ]


def test_withholding_between_two_payments_is_left_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax a day before one payment and weeks after another is refused."""
    first = datetime.date(2024, 6, 3)
    tax = datetime.date(2024, 7, 2)
    second = datetime.date(2024, 7, 3)
    transactions = [
        dividend_transaction(first, "FOO", 100),
        dividend_tax_transaction(first, "FOO", 15),
        dividend_tax_transaction(tax, "FOO", 30),
        dividend_transaction(second, "FOO", 200),
    ]

    assert _reported(transactions) == [
        (first, Decimal(100), Decimal(-15), True),
        (second, Decimal(200), Decimal(0), False),
    ]
    assert len(_unattributed_warnings(transactions, caplog)) == 1


def test_withholding_too_far_before_its_dividend_is_not_claimed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A payment further ahead than the lead window cannot claim the tax."""
    dividend = DIVIDEND_DATE + datetime.timedelta(days=6)
    transactions = [
        dividend_transaction(dividend, "FOO", 100),
        dividend_tax_transaction(DIVIDEND_DATE, "FOO", 15),
    ]

    assert _reported(transactions) == [(dividend, Decimal(100), Decimal(0), False)]
    assert len(_unattributed_warnings(transactions, caplog)) == 1


def test_withholding_stays_with_its_own_broker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One symbol at two brokers keeps each broker's tax with its payments.

    The other broker's payment is nearer in time than the one the tax was
    taken from, so matching on symbol alone claimed it.
    """
    first = datetime.date(2024, 6, 3)
    late_tax = datetime.date(2024, 6, 20)
    other = datetime.date(2024, 6, 25)
    transactions = [
        _dividend_at(first, 100, broker="A"),
        _dividend_tax_at(late_tax, 15, broker="A"),
        _dividend_at(other, 200, broker="B"),
        _dividend_tax_at(other, 30, broker="B"),
    ]

    assert _reported(transactions) == [
        (first, Decimal(100), Decimal(-15), True),
        (other, Decimal(200), Decimal(-30), True),
    ]
    assert _unattributed_warnings(transactions, caplog) == []


def test_summary_and_report_agree_across_the_tax_year_end(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The summary puts withholding in the year the report puts it in.

    The summary used to count withholding in the year it was posted, so a
    payment and its tax either side of 5 April were reported in different
    years and contradicted the report.
    """
    transactions = [
        dividend_transaction(BORDER_DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(BORDER_TAX_DATE, "FOO", 15),
    ]

    assert _summary_dividend_lines(transactions, capsys, tax_year=2024) == [
        "  FOO: 100.00, excluding 15.00 taxed at source (USD)"
    ]
    assert _reported(transactions, tax_year=2024) == [
        (BORDER_DIVIDEND_DATE, Decimal(100), Decimal(-15), True)
    ]

    assert _summary_dividend_lines(transactions, capsys, tax_year=2025) == []
    assert _reported(transactions, tax_year=2025) == []


def test_summary_counts_withholding_dated_apart_from_its_dividend(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tax posted days after its dividend still reaches the summary."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(LATER_DATE, "FOO", 15),
    ]

    assert _summary_dividend_lines(transactions, capsys) == [
        "  FOO: 100.00, excluding 15.00 taxed at source (USD)"
    ]


def test_summary_reports_withholding_with_no_dividend(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tax the broker took is summarised even when no dividend can carry it."""
    transactions = [dividend_tax_transaction(DIVIDEND_DATE, "FOO", 15)]

    assert _summary_dividend_lines(transactions, capsys) == [
        "  FOO: 0.00, excluding 15.00 taxed at source (USD)"
    ]
    assert _reported(transactions) == []


def test_summary_reports_withholding_beyond_the_limit(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tax too far from a dividend is summarised but carried by no row.

    The two views differ here on purpose. The broker took the tax, so the
    summary says so, while the report cannot say which payment it belongs
    to and leaves it out. The warning raised for it reconciles the two.
    """
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(BEYOND_LIMIT_DATE, "FOO", 15),
    ]

    assert _summary_dividend_lines(transactions, capsys) == [
        "  FOO: 100.00, excluding 15.00 taxed at source (USD)"
    ]
    assert _reported(transactions) == [(DIVIDEND_DATE, Decimal(100), Decimal(0), False)]


def test_withholding_on_its_dividend_date_is_attributed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax taken on the day of its dividend belongs to it, as it always did."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(DIVIDEND_DATE, "FOO", 15),
    ]

    assert _reported(transactions) == [
        (DIVIDEND_DATE, Decimal(100), Decimal(-15), True)
    ]
    assert _unattributed_warnings(transactions, caplog) == []


def test_withholding_dated_apart_from_its_dividend_is_attributed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax posted a couple of days late still belongs to its dividend."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(LATER_DATE, "FOO", 15),
    ]

    assert _reported(transactions) == [
        (DIVIDEND_DATE, Decimal(100), Decimal(-15), True)
    ]
    assert _unattributed_warnings(transactions, caplog) == []


def test_correction_of_an_over_withholding_is_attributed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Withholding at 30% later corrected to the treaty rate reports as 15%."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(DIVIDEND_DATE, "FOO", 30),
        dividend_tax_transaction(LATER_DATE, "FOO", -15),
    ]

    assert _reported(transactions) == [
        (DIVIDEND_DATE, Decimal(100), Decimal(-15), True)
    ]
    assert _unattributed_warnings(transactions, caplog) == []


def test_each_dividend_keeps_its_own_withholding(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax goes to the nearer payment when two could claim it."""
    second_date = datetime.date(2024, 9, 5)
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(DIVIDEND_DATE, "FOO", 15),
        dividend_transaction(second_date, "FOO", 200),
        dividend_tax_transaction(second_date + datetime.timedelta(days=4), "FOO", 30),
    ]

    assert _reported(transactions) == [
        (DIVIDEND_DATE, Decimal(100), Decimal(-15), True),
        (second_date, Decimal(200), Decimal(-30), True),
    ]
    assert _unattributed_warnings(transactions, caplog) == []


def test_withholding_at_the_limit_is_attributed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Thirty days away is still near enough to belong to the dividend."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(LIMIT_DATE, "FOO", 15),
    ]

    assert _reported(transactions) == [
        (DIVIDEND_DATE, Decimal(100), Decimal(-15), True)
    ]
    assert _unattributed_warnings(transactions, caplog) == []


def test_withholding_beyond_the_limit_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A day past the limit is too far to attribute to the dividend."""
    transactions = [
        dividend_transaction(DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(BEYOND_LIMIT_DATE, "FOO", 15),
    ]

    assert _reported(transactions) == [(DIVIDEND_DATE, Decimal(100), Decimal(0), False)]

    warnings = _unattributed_warnings(transactions, caplog)

    assert len(warnings) == 1
    assert "-15.00 USD for FOO on 2024-07-04" in warnings[0]
    assert "has no dividend in the 30 days before it or the 5 days after" in warnings[0]


def test_withholding_without_any_dividend_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax for a symbol that paid no dividend at all is reported."""
    transactions = [dividend_tax_transaction(DIVIDEND_DATE, "FOO", 15)]

    warnings = _unattributed_warnings(transactions, caplog)

    assert len(warnings) == 1
    assert "-15.00 USD for FOO on 2024-06-03" in warnings[0]


def test_withholding_across_the_tax_year_end_stays_with_its_dividend() -> None:
    """A payment on 2 April keeps tax posted on 8 April, in the earlier year."""
    transactions = [
        dividend_transaction(BORDER_DIVIDEND_DATE, "FOO", 100),
        dividend_tax_transaction(BORDER_TAX_DATE, "FOO", 15),
    ]

    assert _reported(transactions, tax_year=2024) == [
        (BORDER_DIVIDEND_DATE, Decimal(100), Decimal(-15), True)
    ]
    # The later year must not claim the same withholding a second time.
    assert _reported(transactions, tax_year=2025) == []


def test_unattributed_withholding_outside_tax_year_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only withholding of the computed year may warn, as for the treaty."""
    transactions = [dividend_tax_transaction(datetime.date(2022, 6, 3), "FOO", 15)]

    assert _unattributed_warnings(transactions, caplog) == []


def test_unattributed_withholding_below_a_penny_shows_its_own_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Tax too small to round to a penny is shown as it stands, not as zero.

    Whether such an amount is material depends on its value in GBP, which
    this warning deliberately does not work out, so it reports the loss and
    leaves that judgement to the reader.
    """
    transactions = [dividend_tax_transaction(DIVIDEND_DATE, "FOO", 0.004)]

    warnings = _unattributed_warnings(transactions, caplog)

    assert len(warnings) == 1
    assert "of -0.00 USD" not in warnings[0]
    assert "-0.004" in warnings[0]


def test_unattributed_withholding_of_half_a_penny_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Half a penny rounds up to one, which the report can show."""
    transactions = [dividend_tax_transaction(DIVIDEND_DATE, "FOO", 0.005)]

    warnings = _unattributed_warnings(transactions, caplog)

    assert len(warnings) == 1
    assert "-0.01 USD for FOO" in warnings[0]


def test_dividend_without_withholding_leaves_no_entry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dividend taxed nowhere leaves no zero entry to warn about."""
    transactions = [dividend_transaction(DIVIDEND_DATE, "FOO", 100)]

    assert _reported(transactions) == [(DIVIDEND_DATE, Decimal(100), Decimal(0), False)]
    assert _unattributed_warnings(transactions, caplog) == []


def test_autoconvert_combines_gbp_and_foreign_dividends_across_brokers() -> None:
    """The intended multi-broker case reports one correct GBP total."""
    transactions = [
        _dividend_at(DIVIDEND_DATE, 100, "A", USD),
        _tax_in_currency(DIVIDEND_DATE, -15, USD, "A"),
        _dividend_at(DIVIDEND_DATE, 80, "B", GBP),
        _tax_in_currency(DIVIDEND_DATE, -12, GBP, "B"),
    ]

    assert _reported(
        transactions,
        autoconvert_currency=True,
        gbp_prices={DIVIDEND_DATE: {USD: Decimal("1.25")}},
    ) == [(DIVIDEND_DATE, Decimal(160), Decimal(-24), True)]


def test_autoconvert_handles_gbp_tax_on_a_foreign_dividend() -> None:
    """Dividend and withholding use their own rates before comparison."""
    transactions = [
        _dividend_at(DIVIDEND_DATE, 100, "A", USD),
        _tax_in_currency(DIVIDEND_DATE, -12, GBP, "A"),
    ]

    assert _reported(
        transactions,
        autoconvert_currency=True,
        gbp_prices={DIVIDEND_DATE: {USD: Decimal("1.25")}},
    ) == [(DIVIDEND_DATE, Decimal(80), Decimal(-12), True)]


def test_autoconvert_refuses_dividend_and_tax_in_two_foreign_currencies() -> None:
    """A second foreign currency cannot silently choose treaty treatment."""
    transactions = [
        _dividend_at(DIVIDEND_DATE, 100, "A", USD),
        _tax_in_currency(DIVIDEND_DATE, -60, PLN, "A"),
    ]

    with pytest.raises(CalculationError, match="currencies do not match"):
        _reported(
            transactions,
            autoconvert_currency=True,
            gbp_prices={DIVIDEND_DATE: {USD: Decimal("1.25"), PLN: Decimal(5)}},
        )


def test_autoconvert_refuses_two_foreign_currencies_for_one_dividend() -> None:
    """Two brokers paying the same dividend in different foreign currencies."""
    transactions = [
        _dividend_at(DIVIDEND_DATE, 100, "A", USD),
        _dividend_at(DIVIDEND_DATE, 400, "B", PLN),
    ]

    with pytest.raises(
        CalculationError, match="does not convert between foreign currencies"
    ):
        _reported(
            transactions,
            autoconvert_currency=True,
            gbp_prices={DIVIDEND_DATE: {USD: Decimal("1.25"), PLN: Decimal(5)}},
        )


def test_autoconvert_gbp_dividend_with_foreign_tax_reports_no_treaty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The source-country fallback reads the dividend's currency, not the tax's.

    A broker that reports the payment already converted to GBP leaves nothing
    to name the source country with. The tax is still converted at its own
    rate, but the relief is left out and said to be missing rather than
    guessed from the currency the withholding happened to be taken in.
    """
    transactions = [
        _dividend_at(DIVIDEND_DATE, 80, "A", GBP),
        _tax_in_currency(DIVIDEND_DATE, -15, USD, "A"),
    ]

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="cgt_calc.income"):
        reported = _reported(
            transactions,
            autoconvert_currency=True,
            gbp_prices={DIVIDEND_DATE: {USD: Decimal("1.25")}},
        )

    assert reported == [(DIVIDEND_DATE, Decimal(80), Decimal(-12), False)]
    assert any(
        "Source country of the GBP dividend is unknown" in record.message
        for record in caplog.records
    )


def test_autoconvert_converts_withholding_at_its_dividend_date() -> None:
    """Withholding posted in a later rate month still converts at the dividend.

    The report states the tax at the rate of the dividend it was taken from,
    so combining the rows at the date they happened to post would convert the
    GBP part at one rate and read the total back at another.
    """
    tax_date = datetime.date(2024, 7, 1)
    transactions = [
        _dividend_at(DIVIDEND_DATE, 100, "A", USD),
        _tax_in_currency(tax_date, -10, USD, "A"),
        _tax_in_currency(tax_date, -4, GBP, "A"),
    ]

    assert _reported(
        transactions,
        autoconvert_currency=True,
        gbp_prices={
            DIVIDEND_DATE: {USD: Decimal("1.25")},
            tax_date: {USD: Decimal(2)},
        },
    ) == [(DIVIDEND_DATE, Decimal(80), Decimal(-12), True)]
