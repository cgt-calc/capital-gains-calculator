"""Typed view of a capital gains report, ready for a template to print.

The calculation log names each event with a string key (``sell$FOO``,
``excess-reported-income-distribution$FOO``), and the report's figures need
rounding, counting and adding up before they can be printed. This module makes
every one of those decisions for the detailed report: it decodes the keys,
counts the events, keeps the running totals and rounds every figure exactly
where the report shows it rounded. (The terminal summary in ``render_text``
still reads the gift and spouse-transfer keys for itself.)

A template over the result is loops and substitutions. Every number here is a
``Decimal`` already rounded the way the report prints it, so formatting
(thousands separators, date formats) is all a renderer has left to decide.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from .exceptions import CalculationError
from .model import RuleType
from .util import round_decimal

if TYPE_CHECKING:
    import datetime

    from .model import CalculationEntry, CapitalGainsReport
    from .stock_splits import StockSplitDetail


class EventKind(StrEnum):
    """Kind of capital gains event, decoded from its calculation log key."""

    DISPOSAL = "disposal"
    OPTION_GRANT = "option_grant"
    GIFT_CONNECTED = "gift_connected"
    GIFT_UNCONNECTED = "gift_unconnected"
    ACQUISITION = "acquisition"
    MANAGEMENT_FEE = "management_fee"
    SPIN_OFF = "spin_off"
    ERI = "eri"
    RENAME = "rename"
    SPLIT = "split"
    TRANSFER_TO_SPOUSE = "transfer_to_spouse"
    EXEMPT = "exempt"


class Outcome(StrEnum):
    """What a disposal came to, once its gain is rounded to the penny."""

    GAIN = "gain"
    NIL = "nil"
    LOSS = "loss"
    CLOGGED_LOSS = "clogged_loss"


class IncomeKind(StrEnum):
    """Kind of income event, decoded from its calculation log key."""

    DIVIDEND = "dividend"
    INTEREST = "interest"
    INTEREST_TAX = "interest_tax"
    ERI_DISTRIBUTION = "eri_distribution"


@dataclass(frozen=True)
class EriLineView:
    """Excess reported income added to the cost of one acquisition."""

    cost: Decimal
    unit_price: Decimal
    date: datetime.date


@dataclass(frozen=True)
class LineView:
    """One matching rule applied within an event."""

    rule: str
    quantity: Decimal
    new_quantity: Decimal
    shows_proceeds: bool
    is_gain: bool
    proceeds: Decimal | None = None
    cost: Decimal | None = None
    bnb_date: datetime.date | None = None
    gain: Decimal | None = None
    amount: Decimal | None = None
    new_pool_cost: Decimal | None = None
    pool_per_unit: Decimal | None = None
    spin_off_source: str | None = None
    eris: tuple[EriLineView, ...] = ()


@dataclass(frozen=True)
class EventView:
    """One symbol's events on one day, as the report presents them."""

    kind: EventKind
    symbol: str
    is_disposal: bool
    is_acquisition: bool
    is_gift: bool
    pool_units_left: bool
    quantity: Decimal
    fees: Decimal
    lines: tuple[LineView, ...]
    number: int | None = None
    proceeds: Decimal | None = None
    per_unit: Decimal | None = None
    cost: Decimal | None = None
    gain: Decimal | None = None
    outcome: Outcome | None = None
    gain_to_date: Decimal | None = None
    loss_to_date: Decimal | None = None
    gift_loss_to_date: Decimal | None = None
    base_cost_passed: Decimal | None = None
    eri_unit_price: Decimal | None = None
    renamed_to: str | None = None
    split: StockSplitDetail | None = None
    spin_off_source: str | None = None
    spin_off_dest: str | None = None
    pool_quantity: Decimal | None = None
    pool_cost: Decimal | None = None
    pool_per_unit: Decimal | None = None


@dataclass(frozen=True)
class DayView:
    """Capital gains events recorded on one day."""

    date: datetime.date
    events: tuple[EventView, ...]


@dataclass(frozen=True)
class IncomeEventView:
    """One dividend, interest, interest tax or ERI distribution event."""

    kind: IncomeKind
    number: int
    symbol: str
    amount: Decimal
    is_interest: bool
    origin: str | None = None
    tax_at_source: Decimal | None = None
    treaty_country: str | None = None
    treaty_rate: Decimal | None = None
    treaty_amount: Decimal | None = None
    unit_price: Decimal | None = None


@dataclass(frozen=True)
class IncomeDayView:
    """Income events recorded on one day."""

    date: datetime.date
    events: tuple[IncomeEventView, ...]


@dataclass(frozen=True)
class SummaryView:
    """Capital gains totals for the whole report."""

    acquisition_count: int
    disposal_count: int
    disposal_proceeds: Decimal
    total_gain: Decimal
    total_loss: Decimal
    gift_loss: Decimal
    net_gain: Decimal
    exempt_disposal_count: int
    exempt_disposal_proceeds: Decimal


@dataclass(frozen=True)
class IncomeSummaryView:
    """Dividend and interest totals for the whole report."""

    dividend_count: int
    interest_count: int
    interest_tax_count: int
    eri_count: int
    total_dividends: Decimal
    treaty_allowance: Decimal
    dividend_allowance: Decimal | None
    taxable_dividends: Decimal | None
    dividends_carry_tax_credit: bool
    uk_interest: Decimal
    foreign_interest: Decimal
    interest_tax: Decimal
    eri_dividends: Decimal | None = None
    eri_interest: Decimal | None = None


@dataclass(frozen=True)
class ReportView:
    """The whole report, ready to print."""

    title_period: str
    days: tuple[DayView, ...]
    income_days: tuple[IncomeDayView, ...]
    summary: SummaryView
    income_summary: IncomeSummaryView


# Event kind by log key prefix, in the order the report has always tested
# them: the first match wins.
_EVENT_PREFIXES: Final = (
    ("sell$", EventKind.DISPOSAL),
    ("buy$", EventKind.ACQUISITION),
    ("spin-off$", EventKind.SPIN_OFF),
    ("excess-reported-income$", EventKind.ERI),
    ("rename$", EventKind.RENAME),
    ("split$", EventKind.SPLIT),
    ("transfer-to-spouse$", EventKind.TRANSFER_TO_SPOUSE),
    ("gift$", EventKind.GIFT_CONNECTED),
    ("gift-unconnected$", EventKind.GIFT_UNCONNECTED),
    ("exempt$", EventKind.EXEMPT),
    ("option$", EventKind.OPTION_GRANT),
)

_GIFT_KINDS: Final = frozenset({EventKind.GIFT_CONNECTED, EventKind.GIFT_UNCONNECTED})
_DISPOSAL_KINDS: Final = frozenset(
    {EventKind.DISPOSAL, EventKind.OPTION_GRANT, *_GIFT_KINDS}
)
_ACQUISITION_KINDS: Final = frozenset({EventKind.ACQUISITION, EventKind.MANAGEMENT_FEE})
# Events the report states in prose alone, with no list of matching rules.
_WITHOUT_LINES: Final = frozenset(
    {EventKind.RENAME, EventKind.SPLIT, EventKind.TRANSFER_TO_SPOUSE}
)

_UK_CURRENCY_CODE: Final = "GBP"


def _total(entries: list[CalculationEntry], attribute: str) -> Decimal:
    """Sum one attribute over the entries, in log order."""
    return sum((getattr(entry, attribute) for entry in entries), Decimal(0))


def _decode_event(key: str) -> tuple[EventKind, str]:
    """Return the event kind a log key names, and the symbol it carries."""
    for prefix, kind in _EVENT_PREFIXES:
        if key.startswith(prefix):
            return kind, key.split("$", maxsplit=1)[1]
    raise CalculationError(f"Unknown calculation log key: {key}")


def _decode_income(key: str) -> tuple[IncomeKind, str, str | None]:
    """Return the income kind a log key names, its symbol and its origin.

    Interest and interest tax are named after the currency they were paid in
    rather than the broker, which is why they read the part of the key before
    the ``$`` while the others read the part after it.
    """
    if key.startswith("dividend$"):
        return IncomeKind.DIVIDEND, key.split("$", maxsplit=1)[1], None
    if key.startswith("interestTax"):
        currency = key.split("$", maxsplit=1)[0].removeprefix("interestTax")
        origin = "UK" if currency == _UK_CURRENCY_CODE else "Foreign"
        return IncomeKind.INTEREST_TAX, currency, origin
    if key.startswith("interestUK$"):
        return IncomeKind.INTEREST, key.split("$", maxsplit=1)[1], "UK"
    if key.startswith("interest"):
        return (
            IncomeKind.INTEREST,
            key.split("$", maxsplit=1)[0].removeprefix("interest"),
            "Foreign",
        )
    if key.startswith("excess-reported-income-distribution$"):
        return IncomeKind.ERI_DISTRIBUTION, key.split("$", maxsplit=1)[1], None
    raise CalculationError(f"Unknown income log key: {key}")


def _per_unit(amount: Decimal, quantity: Decimal) -> Decimal | None:
    """Price per unit, or None when there are no units to divide by."""
    if quantity == 0:
        return None
    return round_decimal(amount / quantity, 2)


def _build_line(
    entry: CalculationEntry, kind: EventKind, overall_quantity: Decimal
) -> LineView:
    """Build the view of one matching rule within an event."""
    is_acquisition = kind in _ACQUISITION_KINDS
    spin_off = entry.spin_off
    shows_spin_off = spin_off is not None and (
        is_acquisition or kind is EventKind.SPIN_OFF
    )
    return LineView(
        rule=entry.rule_type.name.replace("_", " "),
        quantity=entry.quantity,
        new_quantity=entry.new_quantity,
        shows_proceeds=entry.quantity < overall_quantity,
        is_gain=entry.gain > 0,
        proceeds=round_decimal(entry.amount + entry.fees, 2),
        cost=round_decimal(entry.allowable_cost, 2),
        bnb_date=(
            entry.bed_and_breakfast_date_index
            if entry.rule_type is RuleType.BED_AND_BREAKFAST
            else None
        ),
        gain=round_decimal(entry.gain, 2),
        amount=round_decimal(-entry.amount, 2),
        new_pool_cost=round_decimal(entry.new_pool_cost, 2),
        pool_per_unit=_per_unit(entry.new_pool_cost, entry.new_quantity),
        spin_off_source=spin_off.source if shows_spin_off and spin_off else None,
        eris=tuple(
            EriLineView(
                cost=round_decimal(eri.price * entry.quantity, 2),
                unit_price=round_decimal(eri.price, 4),
                date=eri.date,
            )
            for eri in entry.eris
        )
        if is_acquisition
        else (),
    )


@dataclass
class _Totals:
    """Running counts and totals kept while walking the calculation log."""

    total_gain: Decimal = Decimal(0)
    total_loss: Decimal = Decimal(0)
    gift_loss: Decimal = Decimal(0)
    acquisitions: int = 0
    disposals: int = 0
    dividends: int = 0
    interests: int = 0
    interest_taxes: int = 0
    eris: int = 0


def _with_disposal(
    event: EventView, entries: list[CalculationEntry], totals: _Totals
) -> EventView:
    """Add the proceeds, gain, outcome and running total of a disposal.

    Exempt disposals share the arithmetic but count towards nothing: an exempt
    asset is neither a chargeable disposal nor part of any total.
    """
    proceeds = round_decimal(_total(entries, "amount") + _total(entries, "fees"), 2)
    gain = round_decimal(_total(entries, "gain"), 2)
    event = replace(
        event,
        proceeds=proceeds,
        gain=gain,
        per_unit=(
            None
            if event.kind is EventKind.OPTION_GRANT
            else _per_unit(proceeds, event.quantity)
        ),
    )
    if event.is_disposal:
        totals.disposals += 1
        event = replace(event, number=totals.disposals)
    if gain > 0:
        if not event.is_disposal:
            return replace(event, outcome=Outcome.GAIN)
        totals.total_gain += gain
        return replace(event, outcome=Outcome.GAIN, gain_to_date=totals.total_gain)
    if gain == 0:
        return replace(event, outcome=Outcome.NIL)
    if event.kind is EventKind.GIFT_CONNECTED:
        totals.gift_loss -= gain
        return replace(
            event, outcome=Outcome.CLOGGED_LOSS, gift_loss_to_date=totals.gift_loss
        )
    if not event.is_disposal:
        return replace(event, outcome=Outcome.LOSS)
    totals.total_loss -= gain
    return replace(event, outcome=Outcome.LOSS, loss_to_date=totals.total_loss)


def _with_pool_after(event: EventView, entry: CalculationEntry) -> EventView:
    """Add the pool the event leaves behind, as the report states it."""
    return replace(
        event,
        pool_quantity=entry.new_quantity,
        pool_cost=round_decimal(entry.new_pool_cost, 2),
        pool_per_unit=_per_unit(entry.new_pool_cost, entry.new_quantity),
    )


def _with_kind_fields(
    event: EventView, entries: list[CalculationEntry], totals: _Totals
) -> EventView:
    """Add the fields only this kind of event carries."""
    kind = event.kind
    if event.is_disposal or kind is EventKind.EXEMPT:
        return _with_disposal(event, entries, totals)
    if kind is EventKind.RENAME:
        return replace(
            event,
            renamed_to=entries[0].renamed_to,
            cost=round_decimal(entries[0].allowable_cost, 2),
        )
    if kind is EventKind.SPLIT:
        return _with_pool_after(
            replace(event, split=entries[0].stock_split), entries[0]
        )
    if kind is EventKind.TRANSFER_TO_SPOUSE:
        return _with_pool_after(
            replace(
                event,
                base_cost_passed=round_decimal(_total(entries, "allowable_cost"), 2),
            ),
            entries[-1],
        )
    # What is left states the cost the event added to the pool.
    cost = round_decimal(entries[0].allowable_cost, 2)
    if kind is EventKind.MANAGEMENT_FEE:
        return replace(event, cost=cost)
    if kind is EventKind.ACQUISITION:
        totals.acquisitions += 1
        return replace(
            event,
            cost=cost,
            number=totals.acquisitions,
            per_unit=_per_unit(cost, event.quantity),
        )
    if kind is EventKind.SPIN_OFF:
        spin_off = entries[0].spin_off
        assert spin_off is not None
        number = None
        if event.quantity > 0:
            totals.acquisitions += 1
            number = totals.acquisitions
        return replace(
            event,
            cost=cost,
            number=number,
            spin_off_source=spin_off.source,
            spin_off_dest=spin_off.dest,
        )
    return replace(
        event, cost=cost, eri_unit_price=round_decimal(entries[0].eris[0].price, 4)
    )


def _build_event(
    key: str, entries: list[CalculationEntry], totals: _Totals
) -> EventView:
    """Build the view of every entry logged for one symbol on one day."""
    kind, symbol = _decode_event(key)
    quantity = _total(entries, "quantity")
    if kind is EventKind.ACQUISITION and quantity <= 0:
        kind = EventKind.MANAGEMENT_FEE
    is_disposal = kind in _DISPOSAL_KINDS
    event = EventView(
        kind=kind,
        symbol=symbol,
        is_disposal=is_disposal,
        is_acquisition=kind in _ACQUISITION_KINDS,
        is_gift=kind in _GIFT_KINDS,
        pool_units_left=is_disposal or kind is EventKind.EXEMPT,
        quantity=quantity,
        fees=round_decimal(_total(entries, "fees"), 2),
        lines=()
        if kind in _WITHOUT_LINES
        else tuple(_build_line(entry, kind, quantity) for entry in entries),
    )
    return _with_kind_fields(event, entries, totals)


def _build_income_event(
    key: str, entries: list[CalculationEntry], totals: _Totals
) -> IncomeEventView:
    """Build the view of one income event."""
    kind, symbol, origin = _decode_income(key)
    entry = entries[0]
    amount = round_decimal(entry.amount, 2)

    if kind is IncomeKind.DIVIDEND:
        dividend = entry.dividend
        assert dividend is not None
        totals.dividends += 1
        treaty = dividend.tax_treaty
        return IncomeEventView(
            kind=kind,
            number=totals.dividends,
            symbol=symbol,
            amount=amount,
            is_interest=dividend.is_interest,
            tax_at_source=round_decimal(-dividend.tax_at_source, 2),
            treaty_country=treaty.country if treaty is not None else None,
            treaty_rate=(
                round_decimal(100 * treaty.treaty_rate, 2)
                if treaty is not None
                else None
            ),
            treaty_amount=(
                round_decimal(dividend.tax_treaty_amount, 2)
                if treaty is not None
                else None
            ),
        )
    if kind is IncomeKind.ERI_DISTRIBUTION:
        eri = entry.eris[0]
        totals.eris += 1
        return IncomeEventView(
            kind=kind,
            number=totals.eris,
            symbol=symbol,
            amount=amount,
            is_interest=eri.is_interest,
            unit_price=round_decimal(eri.price, 4),
        )
    if kind is IncomeKind.INTEREST:
        totals.interests += 1
        number = totals.interests
    else:
        totals.interest_taxes += 1
        number = totals.interest_taxes
    return IncomeEventView(
        kind=kind,
        number=number,
        symbol=symbol,
        amount=amount,
        is_interest=False,
        origin=origin,
    )


def build_report_view(report: CapitalGainsReport) -> ReportView:
    """Build the printable view of a finished report."""
    totals = _Totals()
    days = tuple(
        DayView(
            date=date,
            events=tuple(
                _build_event(key, entries, totals) for key, entries in symbols.items()
            ),
        )
        for date, symbols in report.calculation_log.items()
    )
    income_days = tuple(
        IncomeDayView(
            date=date,
            events=tuple(
                _build_income_event(key, entries, totals)
                for key, entries in symbols.items()
            ),
        )
        for date, symbols in report.calculation_log_yields.items()
    )
    eri_dividends = report.total_eri_amount(is_interest=False)
    eri_interest = report.total_eri_amount(is_interest=True)
    taxable_dividends = report.taxable_dividends()
    return ReportView(
        title_period=report.title_period,
        days=days,
        income_days=income_days,
        summary=SummaryView(
            acquisition_count=totals.acquisitions,
            disposal_count=totals.disposals,
            disposal_proceeds=report.disposal_proceeds,
            total_gain=totals.total_gain,
            total_loss=totals.total_loss,
            gift_loss=totals.gift_loss,
            net_gain=totals.total_gain - totals.total_loss,
            exempt_disposal_count=report.exempt_disposal_count,
            exempt_disposal_proceeds=report.exempt_disposal_proceeds,
        ),
        income_summary=IncomeSummaryView(
            dividend_count=totals.dividends,
            interest_count=totals.interests,
            interest_tax_count=totals.interest_taxes,
            eri_count=totals.eris,
            total_dividends=round_decimal(report.total_dividends_amount(), 2),
            eri_dividends=(
                round_decimal(eri_dividends, 2) if eri_dividends > 0 else None
            ),
            treaty_allowance=round_decimal(
                report.total_dividend_taxes_in_tax_treaties_amount(), 2
            ),
            dividend_allowance=(
                round_decimal(report.dividend_allowance, 2)
                if report.dividend_allowance is not None
                else None
            ),
            taxable_dividends=(
                round_decimal(taxable_dividends, 2)
                if taxable_dividends is not None
                else None
            ),
            dividends_carry_tax_credit=report.dividends_carry_tax_credit,
            uk_interest=report.total_uk_interest,
            foreign_interest=report.total_foreign_interest,
            eri_interest=(round_decimal(eri_interest, 2) if eri_interest > 0 else None),
            interest_tax=report.total_interest_tax,
        ),
    )
