"""Render the report for the terminal."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import sys
from typing import TYPE_CHECKING

from colorama import Style

from .const import BASIC_RATE_LIMITS, CAPITAL_GAINS_TAX_RATES, PERSONAL_ALLOWANCES
from .dates import get_tax_year_end, get_tax_year_start
from .logging import bullet, style_text
from .model import RuleType
from .util import exact_str, round_decimal, strip_zeros

if TYPE_CHECKING:
    import datetime

    from .model import CapitalGainsReport


def render_text(report: CapitalGainsReport, income: Decimal | None = None) -> str:
    """Return the report as printed to the terminal.

    `income` is the year's income before the Personal Allowance, without the
    dividends and interest in the report, when the user gave it.
    """
    return (
        _render_portfolio(report)
        + "\n"
        + _render_tax_summary(report, income)
        + _render_excess_reported_income(report)
        + _render_spouse_transfers(report)
        + _render_gifts(report)
    )


def _render_portfolio(report: CapitalGainsReport) -> str:
    """Render the holdings still open at the end of the reporting period."""
    bul = bullet(sys.stdout)
    portfolio_label = (
        report.period_label() or f"{report.tax_year}/{report.tax_year + 1} tax year"
    )
    out = (
        style_text(
            f"Portfolio at the end of {portfolio_label}",
            colour=Style.BRIGHT,
            emoji="📈",
        )
        + "\n"
    )
    held = [entry for entry in report.portfolio if entry.quantity > 0]
    if not held:
        out += f"{bul}(none)\n"
    for entry in sorted(held, key=lambda entry: entry.symbol):
        unrealized_gains_str = (
            entry.unrealized_gains_str() if report.show_unrealized_gains else ""
        )
        out += f"{bul}{entry!s}{unrealized_gains_str}\n"
    return out


def _render_tax_summary(report: CapitalGainsReport, income: Decimal | None) -> str:
    """Render the capital gains, dividends and interest summary."""
    out = (
        style_text(
            "Tax summary for "
            f"{report.period_label() or f'{report.tax_year}/{report.tax_year + 1}'}",
            colour=Style.BRIGHT,
            emoji="🧮",
        )
        + "\n"
    )
    groups = _summary_rows(report, income)
    # Right-align values on one shared column across the whole summary so
    # the decimal points line up.
    label_width = max(len(label) for _, rows, _ in groups for label, _ in rows) + 1
    value_width = max(len(value) for _, rows, _ in groups for _, value in rows)
    for title, rows, notes in groups:
        out += f"\n{style_text(title, colour=Style.BRIGHT)}\n"
        for label, value in rows:
            line = f"  {label + ':':<{label_width}} {value:>{value_width}}"
            if label in {"Total gain", "Taxable gain"}:
                # The headline figures of the whole report.
                line = style_text(line, colour=Style.BRIGHT)
            out += f"{line}\n"
        for note in notes:
            out += f"{note}\n"
    return out


def _summary_rows(
    report: CapitalGainsReport, income: Decimal | None
) -> list[tuple[str, list[tuple[str, str]], list[str]]]:
    """Return the tax summary as (title, label/value rows, notes) groups."""
    capital: list[tuple[str, str]] = [
        ("Disposals", str(report.disposal_count)),
        ("Disposal proceeds", _pounds(report.disposal_proceeds)),
        ("Allowable costs", _pounds(report.allowable_costs)),
        ("Gain", _pounds(report.capital_gain)),
        ("Loss", _pounds(-report.capital_loss)),
        *(
            [("Losses on gifts", _pounds(-report.gift_loss))]
            if report.gift_loss
            else []
        ),
        *(
            [
                ("Exempt disposals", str(report.exempt_disposal_count)),
                (
                    "Exempt disposal proceeds",
                    _pounds(report.exempt_disposal_proceeds),
                ),
            ]
            if report.exempt_disposal_count
            else []
        ),
        ("Total gain", _pounds(report.total_gain())),
    ]
    capital_notes: list[str] = []
    if report.capital_gain_allowance is not None:
        capital.append(("Taxable gain", _pounds(report.taxable_gain())))
        tax_rows, tax_notes = _capital_gains_tax(
            report, report.capital_gain_allowance, income
        )
        capital += tax_rows
        capital_notes += tax_notes
    else:
        capital_notes.append("WARNING: Missing allowance for this tax year")
    if report.show_unrealized_gains:
        capital.append(("Unrealized gains", _pounds(report.total_unrealized_gains())))
        if any(h.unrealized_gains is None for h in report.portfolio):
            capital_notes.append(
                "WARNING: Some unrealized gains couldn't be calculated."
                " Take a look at the symbols with unknown unrealized gains"
                " above and factor in their prices."
            )

    dividends: list[tuple[str, str]] = [
        ("Proceeds", _pounds(report.total_dividends_amount())),
    ]
    if report.dividend_allowance is not None:
        dividends.append(("Tax-free allowance", _pounds(report.dividend_allowance)))
    taxable_dividends = report.taxable_dividends()
    if taxable_dividends is not None:
        dividends.append(("Taxable proceeds", _pounds(taxable_dividends)))
    dividend_notes: list[str] = []
    if report.explains_dividend_tax_credit():
        dividend_notes.append(
            "Most dividends before 6 April 2016 carried a tax credit, so cgt-calc "
            "does not work out the taxable amount. Use your dividend vouchers and "
            "HMRC's guidance on tax credits."
        )

    interest: list[tuple[str, str]] = [
        ("UK proceeds", _pounds(report.total_uk_interest)),
        ("Foreign proceeds", _pounds(report.total_foreign_interest)),
        ("Tax paid", _pounds(report.total_interest_tax)),
    ]

    return [
        ("Capital gains", capital, capital_notes),
        ("Dividends", dividends, dividend_notes),
        ("Interest", interest, []),
    ]


def _rates_on(date: datetime.date) -> tuple[int, int]:
    """Return the basic and higher Capital Gains Tax rates on a day's disposals."""
    return next(
        (basic, higher)
        for start, basic, higher in reversed(CAPITAL_GAINS_TAX_RATES)
        if start <= date
    )


_HIGHEST_RATE_FIRST = (
    "Losses and the annual exempt amount are deducted from the gains taxed at the "
    "highest rate first."
)


def _pounds(amount: Decimal) -> str:
    """Return an amount as the terminal report prints it."""
    return f"£{round_decimal(amount, 2):,}"


def _charged_by_rate(
    taxable: dict[tuple[int, int], Decimal], band: Decimal
) -> dict[int, Decimal]:
    """Return how much of what is left to tax is charged at each rate.

    `band` is the unused part of the basic rate band. The individual chooses
    which gains it goes to (TCGA 1992 s1I(7)), so it goes where the two rates
    are furthest apart. Gains taxed at one rate whatever the income come last
    and so leave it to the others (F(No. 2)A 2010 Sch 1 para 18).
    """
    charged: dict[int, Decimal] = defaultdict(Decimal)
    for (basic, higher), amount in sorted(
        taxable.items(), key=lambda item: item[0][1] - item[0][0], reverse=True
    ):
        in_band = min(amount, band)
        band -= in_band
        charged[basic] += in_band
        charged[higher] += amount - in_band
    return {rate: amount for rate, amount in charged.items() if amount}


def _tax(charged: dict[int, Decimal]) -> Decimal:
    """Return the tax on amounts charged at each rate."""
    return sum((amount * rate for rate, amount in charged.items()), Decimal(0)) / 100


def _taxable_by_rates(
    gains: dict[tuple[int, int], Decimal], deductions: Decimal
) -> dict[tuple[int, int], Decimal]:
    """Return what is left to tax of the gains under each pair of rates.

    Losses and the annual exempt amount may be deducted in whichever way is
    most beneficial (TCGA 1992 s1F and s1K(5), s4B before 2019/20). While one
    pair of rates is at least the other in both rates, which a test holds the
    rate table to, that is from the gains with the highest higher rate, then
    the highest basic rate, first, whatever the unused basic rate band.
    """
    taxable = {}
    for rates, gain in sorted(
        gains.items(), key=lambda item: (item[0][1], item[0][0]), reverse=True
    ):
        deducted = min(gain, deductions)
        deductions -= deducted
        taxable[rates] = gain - deducted
    return taxable


def _estimated_tax(
    report: CapitalGainsReport,
    taxable: dict[tuple[int, int], Decimal],
    income: Decimal,
    *,
    rates_changed: bool,
) -> tuple[str, list[str]]:
    """Return the tax for the user's income, and the notes on how it was reached."""
    allowance = PERSONAL_ALLOWANCES[report.tax_year]
    limit = BASIC_RATE_LIMITS[report.tax_year]
    # Dividends that carried a tax credit are not taxed as the amount received.
    # A total below zero is a reversal of an earlier year's payment.
    dividends = (
        Decimal(0)
        if report.dividends_carry_tax_credit
        else max(Decimal(0), round_decimal(report.total_dividends_amount(), 2))
    )
    interest = max(Decimal(0), report.total_uk_interest + report.total_foreign_interest)
    taxable_income = max(Decimal(0), income + dividends + interest - allowance)
    band = max(Decimal(0), limit - taxable_income)
    charged = _charged_by_rate(taxable, band)

    added = [
        f"{_pounds(amount)} {name}"
        for name, amount in (("dividends", dividends), ("interest", interest))
        if amount
    ]
    source = f"Income {_pounds(income)}"
    if added:
        source += f", plus {' and '.join(added)} from these files,"
    if band:
        notes = [
            (
                f"{source.removesuffix(',')}, less the £{allowance:,} Personal "
                f"Allowance: taxable income {_pounds(taxable_income)}, which leaves "
                f"{_pounds(band)} of the £{limit:,} basic rate limit unused."
            )
        ]
    else:
        # The allowance shrinks above £100,000, so only its most is stated.
        notes = [
            (
                f"{source} less the Personal Allowance (£{allowance:,} at most) is "
                f"£{limit:,} or more, so none of the basic rate limit is unused."
            )
        ]
    if report.explains_dividend_tax_credit():
        notes.append(
            "Dividends before 6 April 2016 are not added: include their taxable "
            "amount in --income."
        )
    (rate, amount), *rest = sorted(charged.items())
    parts = [
        f"{_pounds(amount)} is taxed at {rate}%",
        *(f"{_pounds(amount)} at {rate}%" for rate, amount in rest),
    ]
    listed = f"{', '.join(parts[:-1])} and {parts[-1]}" if rest else parts[0]
    notes.append(f"Of the taxable gain, {listed}.")
    if rates_changed:
        notes.append(
            "Losses, the annual exempt amount and the unused part of the limit are "
            "set against the gains where they save the most tax."
            if band
            else _HIGHEST_RATE_FIRST
        )
    return _pounds(_tax(charged)), notes


def _capital_gains_tax(
    report: CapitalGainsReport, allowance: Decimal, income: Decimal | None
) -> tuple[list[tuple[str, str]], list[str]]:
    """Return the rows and notes for the tax on a full year's taxable gain.

    The rate depends on taxable income. Without the user's income the tax is
    given at the basic and at the higher rate, with who pays each; the tax due
    lies between the two. With it, one figure is given.
    """
    if report.period_start is not None or report.taxable_gain() == 0:
        return [], []
    gains: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    for date, gain in report.gains_by_date.items():
        gains[_rates_on(date)] += gain
    taxable = _taxable_by_rates(gains, allowance - report.capital_loss)
    # At the higher rates none of the basic rate band is unused.
    higher_tax = _pounds(_tax(_charged_by_rate(taxable, Decimal(0))))
    left_out = (
        "gains that are not in the files you supplied, losses brought forward and "
        "reliefs. See https://cgt-calc.uk/usage/"
    )
    estimate = (
        f"The tax is an estimate: it leaves out {left_out}"
        "#tax-at-the-basic-and-higher-rate"
    )
    # The part of the taxable gain whose rate depends on income.
    banded = sum(
        (left for (basic, higher), left in taxable.items() if basic != higher),
        Decimal(0),
    )
    if not banded:
        # All of it is taxed at one rate whatever the income.
        rate = next(basic for (basic, _), left in taxable.items() if left)
        return [(f"Tax at {rate}%", higher_tax)], [estimate]

    year_end = get_tax_year_end(report.tax_year)
    opening = _rates_on(get_tax_year_start(report.tax_year))
    closing = _rates_on(year_end)
    change = next(
        f"{start.day} {start:%B %Y}"
        for start, _, _ in reversed(CAPITAL_GAINS_TAX_RATES)
        if start <= year_end
    )
    limit = f"£{BASIC_RATE_LIMITS[report.tax_year]:,}"
    # Gains taxed at one rate whatever the income do not use the limit
    # (F(No. 2)A 2010 Sch 1 para 18), so only the rest is set against it.
    single_rate = any(
        left for (basic, higher), left in taxable.items() if basic == higher
    )
    before_change = (
        f"Gains before {change} are taxed at {opening[0]}% whatever your income "
        f"and do not count towards the {limit}."
    )
    if income is not None:
        tax, notes = _estimated_tax(
            report, taxable, income, rates_changed=opening != closing
        )
        if single_rate:
            notes.append(before_change)
        notes.append(f"The tax leaves out {left_out}#estimate-the-tax-from-your-income")
        return [("Estimated tax", tax)], notes

    # At the basic rates all of the taxable gain fits within the band.
    basic_tax = _pounds(_tax(_charged_by_rate(taxable, report.taxable_gain())))
    basic, higher = (
        f"{before}%" if before == after else f"{before}%, or {after}% from {change}"
        for before, after in zip(opening, closing, strict=True)
    )
    tax_year = f"{report.tax_year}/{report.tax_year + 1}"
    counted = that = "the taxable gain"
    if single_rate:
        that = f"that {_pounds(banded)}"
        counted = f"the {_pounds(banded)} of taxable gain made from {change}"
    notes = [
        (
            f"Basic rate ({basic}): your taxable income for {tax_year} plus {counted} "
            f"is {limit} or less."
        ),
        f"Higher rate ({higher}): your taxable income for {tax_year} is {limit} or more.",
        (
            f"If your taxable income is under {limit} but {that} takes you over it, "
            f"you pay the basic rate on the part of it that fits under {limit} and "
            "the higher rate on the rest. Your tax is then between the two figures."
        ),
        (
            "Taxable income is your income after the Personal Allowance and other "
            "Income Tax reliefs."
        ),
    ]
    if opening != closing:
        notes.append(_HIGHEST_RATE_FIRST)
    if single_rate:
        notes.append(before_change)
    added = (
        "the interest"
        if report.dividends_carry_tax_credit
        else "the dividends and interest"
    )
    notes += [
        (
            f"To get a single figure, add --income with your income for {tax_year} "
            "before the Personal Allowance, such as the pay on your P60. cgt-calc "
            f"adds {added} in these files."
        ),
        estimate,
    ]
    return [("Tax at basic rate", basic_tax), ("Tax at higher rate", higher_tax)], notes


def _render_excess_reported_income(report: CapitalGainsReport) -> str:
    """Render the excess reported income section, empty when there is none."""
    eris = list(
        report._filter_calculation_log(  # noqa: SLF001
            report.calculation_log_yields,
            RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
        )
    )
    if not eris:
        return ""
    bul = bullet(sys.stdout)
    out = "\n" + style_text("Excess Reported Income", colour=Style.BRIGHT) + "\n"
    for item in eris:
        assert item.eris
        assert len(item.eris) == 1
        dist_type = "interest" if item.eris[0].is_interest else "dividend"
        out += f"{bul}{item.eris[0].symbol}: {_pounds(item.amount)} "
        out += f"(included as {dist_type})\n"
    return out


def _render_spouse_transfers(report: CapitalGainsReport) -> str:
    """Render the no gain/no loss transfers, empty when there are none."""
    transfer_prefix = "transfer-to-spouse$"
    transfers = sorted(
        (
            (date_index, key, entry_list)
            for date_index, symbol_dict in report.calculation_log.items()
            for key, entry_list in symbol_dict.items()
            if key.startswith(transfer_prefix)
        ),
        key=lambda transfer: (transfer[0], transfer[1]),
    )
    if not transfers:
        return ""
    bul = bullet(sys.stdout)
    out = "\n" + style_text("Transferred to spouse", colour=Style.BRIGHT) + "\n"
    out += (
        "  No gain/no loss; the base cost below passes to the recipient"
        " (TCGA 1992 s58).\n"
        "  Give them the RAW row shown under each transfer: it records the"
        " shares arriving at that cost in their own report.\n"
    )
    for date_index, key, entry_list in transfers:
        symbol = key[len(transfer_prefix) :]
        quantity = sum((e.quantity for e in entry_list), Decimal(0))
        base_cost = sum((e.allowable_cost for e in entry_list), Decimal(0))
        out += (
            f"{bul}{date_index}: {symbol} "
            f"{strip_zeros(quantity)} units, base cost {_pounds(base_cost)}\n"
            f"    {date_index},TRANSFER_FROM_SPOUSE,{symbol},"
            f"{exact_str(quantity)},{exact_str(base_cost / quantity)},"
            "0.00,GBP\n"
        )
    return out


def _render_gifts(report: CapitalGainsReport) -> str:
    """Render the disposals made as gifts, empty when there are none."""
    gift_prefixes = ("gift$", "gift-unconnected$")
    gifts = sorted(
        (
            (date_index, key, entry_list)
            for date_index, symbol_dict in report.calculation_log.items()
            for key, entry_list in symbol_dict.items()
            if key.startswith(gift_prefixes)
        ),
        key=lambda gift: (gift[0], gift[1]),
    )
    if not gifts:
        return ""
    bul = bullet(sys.stdout)
    out = "\n" + style_text("Gifts at market value", colour=Style.BRIGHT) + "\n"
    out += (
        "  Disposals at market value (TCGA 1992 s17), before any relief."
        " A loss on a gift to a connected\n"
        "  person (GIFT) is clogged: kept out of Loss and Total gain, usable"
        " only against gains on disposals\n"
        "  to the same person while still connected (s18(3)). Keep a"
        " separate record of it. A loss on a gift\n"
        "  to anyone else (GIFT_UNCONNECTED) counts in Loss.\n"
    )
    for date_index, key, entry_list in gifts:
        clogged = key.startswith("gift$")
        symbol = key.split("$", 1)[1]
        quantity = sum((e.quantity for e in entry_list), Decimal(0))
        market_value = sum((e.amount + e.fees for e in entry_list), Decimal(0))
        gain = sum((e.gain for e in entry_list), Decimal(0))
        if gain < 0:
            outcome = f"loss {_pounds(-gain)}"
            if clogged:
                outcome += " (clogged)"
        else:
            outcome = f"gain {_pounds(gain)}"
        out += (
            f"{bul}{date_index}: {symbol} {strip_zeros(quantity)} units, "
            f"market value {_pounds(market_value)}, {outcome}\n"
        )
    return out
