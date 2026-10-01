"""Render the report for the terminal."""

from __future__ import annotations

from decimal import Decimal
import sys
from typing import TYPE_CHECKING

from colorama import Style

from .logging import bullet, style_text
from .model import RuleType
from .util import exact_str, round_decimal, strip_zeros

if TYPE_CHECKING:
    from .model import CapitalGainsReport


def render_text(report: CapitalGainsReport) -> str:
    """Return the report as printed to the terminal."""
    return (
        _render_portfolio(report)
        + "\n"
        + _render_tax_summary(report)
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


def _render_tax_summary(report: CapitalGainsReport) -> str:
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
    groups = _summary_rows(report)
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
    report: CapitalGainsReport,
) -> list[tuple[str, list[tuple[str, str]], list[str]]]:
    """Return the tax summary as (title, label/value rows, notes) groups."""
    capital: list[tuple[str, str]] = [
        ("Disposals", str(report.disposal_count)),
        ("Disposal proceeds", f"£{report.disposal_proceeds:,}"),
        ("Allowable costs", f"£{report.allowable_costs:,}"),
        ("Gain", f"£{report.capital_gain:,}"),
        ("Loss", f"£{-report.capital_loss:,}"),
        *([("Losses on gifts", f"£{-report.gift_loss:,}")] if report.gift_loss else []),
        *(
            [
                ("Exempt disposals", str(report.exempt_disposal_count)),
                (
                    "Exempt disposal proceeds",
                    f"£{report.exempt_disposal_proceeds:,}",
                ),
            ]
            if report.exempt_disposal_count
            else []
        ),
        ("Total gain", f"£{report.total_gain():,}"),
    ]
    capital_notes: list[str] = []
    if report.capital_gain_allowance is not None:
        capital.append(
            ("Taxable gain", f"£{round_decimal(report.taxable_gain(), 2):,}")
        )
    else:
        capital_notes.append("WARNING: Missing allowance for this tax year")
    if report.show_unrealized_gains:
        capital.append(
            (
                "Unrealized gains",
                f"£{round_decimal(report.total_unrealized_gains(), 2):,}",
            )
        )
        if any(h.unrealized_gains is None for h in report.portfolio):
            capital_notes.append(
                "WARNING: Some unrealized gains couldn't be calculated."
                " Take a look at the symbols with unknown unrealized gains"
                " above and factor in their prices."
            )

    dividends: list[tuple[str, str]] = [
        ("Proceeds", f"£{round_decimal(report.total_dividends_amount(), 2):,}"),
    ]
    if report.dividend_allowance is not None:
        dividends.append(
            (
                "Tax-free allowance",
                f"£{round_decimal(report.dividend_allowance, 2):,}",
            )
        )
    taxable_dividends = report.taxable_dividends()
    if taxable_dividends is not None:
        dividends.append(
            ("Taxable proceeds", f"£{round_decimal(taxable_dividends, 2):,}")
        )
    dividend_notes: list[str] = []
    if report.dividends_carry_tax_credit:
        dividend_notes.append(
            "Dividends before 6 April 2016 carried a tax credit, so the taxable "
            "amount is not worked out."
        )

    interest: list[tuple[str, str]] = [
        ("UK proceeds", f"£{report.total_uk_interest:,}"),
        ("Foreign proceeds", f"£{report.total_foreign_interest:,}"),
        ("Tax paid", f"£{report.total_interest_tax:,}"),
    ]

    return [
        ("Capital gains", capital, capital_notes),
        ("Dividends", dividends, dividend_notes),
        ("Interest", interest, []),
    ]


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
        out += f"{bul}{item.eris[0].symbol}: £{round_decimal(item.amount, 2):,} "
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
            f"{strip_zeros(quantity)} units, base cost "
            f"£{round_decimal(base_cost, 2):,}\n"
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
            outcome = f"loss £{round_decimal(-gain, 2):,}"
            if clogged:
                outcome += " (clogged)"
        else:
            outcome = f"gain £{round_decimal(gain, 2):,}"
        out += (
            f"{bul}{date_index}: {symbol} {strip_zeros(quantity)} units, "
            f"market value £{round_decimal(market_value, 2):,}, {outcome}\n"
        )
    return out
