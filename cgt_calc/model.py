"""Model classes."""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime
from decimal import Decimal
from enum import Enum
import re
from typing import TYPE_CHECKING, Final, NamedTuple, Self, override

from .exceptions import CalculationError
from .util import (
    approx_equal,
    display_str,
    luhn_check_digit,
    normalize_amount,
    round_decimal,
    strip_zeros,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from .stock_splits import StockSplitDetail


_ISIN_REGEX: Final = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_ISIN_CHARS: Final = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_CURRENCY_CODE_REGEX: Final = re.compile(r"^[A-Z]{3}$")


class Isin(str):
    """ISIN (ISO 6166) security identifier.

    Validated and normalised on construction, so every instance is a
    well-formed identifier with a correct check digit.
    """

    __slots__ = ()

    def __new__(cls, value: str) -> Self:
        """Normalise and validate the identifier."""
        normalised = value.strip().upper()
        if not _ISIN_REGEX.match(normalised):
            raise ValueError(f"Invalid ISIN: {value!r}")
        payload = "".join(str(_ISIN_CHARS.index(char)) for char in normalised[:11])
        if luhn_check_digit(payload) != int(normalised[11]):
            raise ValueError(f"Invalid ISIN checksum: {value!r}")
        return super().__new__(cls, normalised)

    @classmethod
    def parse(cls, value: str) -> Self | None:
        """Return the identifier, or None when the value is not an ISIN."""
        try:
            return cls(value)
        except ValueError:
            return None


class CurrencyCode(str):
    """ISO 4217 alpha-3 currency code.

    Only the shape is validated here: whether a code is a real, current
    currency is decided by the HMRC exchange rate table when it is used.
    Case is not folded because it can carry meaning: "GBp" is pence, not
    pounds, and quietly upper-casing it would misstate amounts a hundredfold.
    """

    __slots__ = ()

    def __new__(cls, value: str) -> Self:
        """Validate the code, tolerating surrounding whitespace."""
        normalised = value.strip()
        if not _CURRENCY_CODE_REGEX.match(normalised):
            raise ValueError(f"Invalid currency code: {value!r}")
        return super().__new__(cls, normalised)

    @classmethod
    def parse(cls, value: str) -> Self | None:
        """Return the code, or None when the value is not a currency code."""
        try:
            return cls(value)
        except ValueError:
            return None


@dataclass
class SpinOff:
    """Class representing spin-off event on a share."""

    # Source of the Spin-off, e.g. MMM for SOLV
    source: str
    # Destination ticker to which SpinOff happened, e.g. SOLV for MMM
    dest: str
    # When the spin-off happened
    date: datetime.date
    # Shares received under this row, and the closing prices on the day. A
    # holding split across brokers arrives as one row per broker; the split
    # of value is worked out across all of them together.
    quantity: Decimal = Decimal(0)
    source_price: Decimal | None = None
    dest_price: Decimal | None = None


@dataclass
class TaxTreaty:
    """Class representing a treaty between UK and different countries."""

    country: str
    country_rate: Decimal
    treaty_rate: Decimal


@dataclass
class ExcessReportedIncome:
    """Class representing Excess Reported Income on a fund.

    The income is reported on a fund at the end of its reporting period.
    The income represent an increase of the cost basis at that date and a
    taxable event at the distribution date.
    """

    price: Decimal
    symbol: str
    date: datetime.date
    distribution_date: datetime.date
    is_interest: bool


@dataclass
class ExcessReportedIncomeDistribution:
    """Class representing Excess Reported Income distribution event on a fund.

    This is when the income is distributed to you for tax purposes.
    """

    price: Decimal = Decimal(0)
    amount: Decimal = Decimal(0)
    quantity: Decimal = Decimal(0)

    def __add__(
        self, transaction: ExcessReportedIncomeDistribution
    ) -> ExcessReportedIncomeDistribution:
        """Add two tax transactions."""
        return self.__class__(
            price=transaction.price,
            amount=self.amount + transaction.amount,
            quantity=self.quantity + transaction.quantity,
        )


@dataclass
class HmrcTransactionData:
    """HMRC transaction figures."""

    quantity: Decimal = Decimal(0)
    amount: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    # This is a list to support Bed and Breakfast acquisitions that can cross multiple
    # ERI reports for the same fund. This can happen for example when a fund is
    # liquidated shortly after its usual reporting date, requiring a new final report.
    eris: list[ExcessReportedIncome] = field(default_factory=list)

    def __add__(self, transaction: HmrcTransactionData) -> HmrcTransactionData:
        """Add two transactions."""
        return self.__class__(
            self.quantity + transaction.quantity,
            self.amount + transaction.amount,
            self.fees + transaction.fees,
            self.eris + transaction.eris,
        )


@dataclass
class DayAcquisitions:
    """What a day put into one holding, kept apart by how it got there.

    `purchased` is what the same-day and 30-day rules may identify a disposal
    against: a purchase, a vest, or shares arriving from a spouse.

    `reorganised` is what a spin-off created. That is not an acquisition
    (TCGA 1992 s127, CG51805), and its amount is only the first pass's
    estimate of the share of the source pool that carries across, which the
    walk replaces with the real figure once it reaches the day. A
    reorganisation an export states as a share count leaves nothing here at
    all: it restates a holding rather than adding to one.

    `cost_only` is pooled cost with no shares behind it, which is a management
    fee.
    """

    purchased: HmrcTransactionData = field(default_factory=HmrcTransactionData)
    reorganised: HmrcTransactionData = field(default_factory=HmrcTransactionData)
    cost_only: HmrcTransactionData = field(default_factory=HmrcTransactionData)

    @property
    def total(self) -> HmrcTransactionData:
        """Everything the day put in, which is what the pool moves by."""
        return self.purchased + self.reorganised + self.cost_only


@dataclass
class ForeignCurrencyAmount:
    """Represent a decimal amount in foreign currency."""

    amount: Decimal = Decimal(0)
    currency: CurrencyCode | None = None

    def __add__(self, amount: ForeignCurrencyAmount) -> ForeignCurrencyAmount:
        """Add two amounts."""
        if self.currency is None and self.amount:
            raise CalculationError(f"Currency missing for amount {self.amount}")
        if amount.currency is None and amount.amount:
            raise CalculationError(f"Currency missing for amount {amount.amount}")
        if (
            self.currency is not None
            and amount.currency is not None
            and self.currency != amount.currency
        ):
            raise CalculationError(
                "Cannot combine amounts in different currencies: "
                f"{self.currency} and {amount.currency}"
            )
        result = ForeignCurrencyAmount(
            amount=self.amount + amount.amount,
            currency=self.currency or amount.currency,
        )
        assert result.currency or not result.amount, (
            f"Invalid foreign currency result {result}"
        )
        return result


class OptionType(Enum):
    """Whether an option gives the holder the right to buy or sell."""

    CALL = "call"
    PUT = "put"


@dataclass(frozen=True)
class OptionContract:
    """Identity and delivery terms of a standard equity option contract."""

    underlying: str
    expiry: datetime.date
    strike: Decimal
    option_type: OptionType
    multiplier: Decimal = Decimal(100)

    @override
    def __str__(self) -> str:
        """Name the contract the way a person would say it.

        This is what the report calls the asset, so it says in words what
        kind of thing it is: a bare "META 2024-05-17 500 call" reads like a
        share of something.
        """
        return (
            f"{self.underlying} {self.option_type.value} option expiring "
            f"{self.expiry.isoformat()} at a strike of {strip_zeros(self.strike)}"
        )


@dataclass(frozen=True)
class DatedAmount:
    """An amount converted for tax at the exchange rate on its own date."""

    date: datetime.date
    amount: Decimal
    currency: CurrencyCode


@dataclass
class WrittenOptionTaxData:
    """Tax details attached to the grant of a written option.

    ``taxable_quantity`` excludes contracts which were assigned, since their
    premium is folded into the underlying share transaction instead. Closing
    costs are attached to the original grant under TCGA 1992 s148.
    """

    taxable_quantity: Decimal
    close_costs: list[DatedAmount] = field(default_factory=list)


@dataclass
class OptionDisposalData:
    """Aggregated figures for grants of one option series on one date."""

    quantity: Decimal = Decimal(0)
    proceeds: Decimal = Decimal(0)
    allowable_cost: Decimal = Decimal(0)

    def __add__(self, other: OptionDisposalData) -> OptionDisposalData:
        """Combine grants which share a date and option series."""
        return self.__class__(
            quantity=self.quantity + other.quantity,
            proceeds=self.proceeds + other.proceeds,
            allowable_cost=self.allowable_cost + other.allowable_cost,
        )


@dataclass(frozen=True)
class CapitalAdjustment:
    """An option-related adjustment to an underlying share transaction.

    ``net_amount`` follows the broker transaction's sign convention: a
    positive value increases disposal proceeds or reduces acquisition cost.
    ``fees`` records the related incidental cost separately for reporting.
    """

    date: datetime.date
    net_amount: Decimal
    fees: Decimal
    currency: CurrencyCode


HmrcTransactionLog = dict[datetime.date, dict[str, HmrcTransactionData]]
AcquisitionLog = dict[datetime.date, dict[str, DayAcquisitions]]
ForeignAmountLog = dict[tuple[str, datetime.date], ForeignCurrencyAmount]
ExcessReportedIncomeLog = dict[datetime.date, dict[str, ExcessReportedIncome]]
ExcessReportedIncomeDistributionLog = dict[
    datetime.date, dict[str, ExcessReportedIncomeDistribution]
]


class DividendTaxAttribution(NamedTuple):
    """Withheld tax sorted by whether a dividend was found to carry it."""

    matched: ForeignAmountLog
    """Keyed by the symbol and date of the dividend the tax was taken from."""

    unmatched: ForeignAmountLog
    """Keyed by the symbol and date of the withholding itself.

    No dividend row can carry this tax, but the broker took it all the same,
    so the summary reports it against the year it was taken in.
    """


class ActionType(Enum):
    """Type of transaction action."""

    BUY = 1
    SELL = 2
    TRANSFER = 3
    STOCK_ACTIVITY = 4
    DIVIDEND = 5
    DIVIDEND_TAX = 6
    FEE = 7
    ADJUSTMENT = 8
    CAPITAL_GAIN = 9
    SPIN_OFF = 10
    INTEREST = 11
    REINVEST_SHARES = 12
    REINVEST_DIVIDENDS = 13
    WIRE_FUNDS_RECEIVED = 14
    STOCK_SPLIT = 15
    CASH_MERGER = 16
    EXCESS_REPORTED_INCOME = 17
    FULL_REDEMPTION = 18
    RENAME = 19
    INTEREST_TAX = 20
    TRANSFER_TO_SPOUSE = 21
    # A broker reported shares leaving as a gift without saying who received
    # them. The user classifies it with a TRANSFER_TO_SPOUSE or GIFT row.
    UNCLASSIFIED_GIFT = 22
    TRANSFER_FROM_SPOUSE = 23
    # Shares given to a connected person other than a spouse: a disposal at
    # market value, with a loss clogged under TCGA 1992 s18(3).
    GIFT = 24
    # The same, to someone who is not a connected person: a loss is ordinary.
    GIFT_UNCONNECTED = 25
    # A purchase the broker reversed. Parsers are expected to drop it along
    # with the purchase it reverses, so it never reaches the calculator; it
    # exists so that a row which slips through is refused rather than booked
    # as an acquisition.
    CANCEL_BUY = 26
    # Grant and closing cash flows for written exchange-traded options. The
    # grant is a chargeable disposal unless assignment folds it into the
    # underlying share transaction; a closing purchase is an allowable cost
    # of that original grant (TCGA 1992 sections 144 and 148).
    OPTION_GRANT = 27
    OPTION_CLOSE = 28
    OPTION_EXPIRY = 29
    OPTION_ASSIGNMENT = 30

    @override
    def __str__(self) -> str:
        """Name the action the way a message to the user should say it.

        An enum's default string is its Python name, ``ActionType.BUY``,
        and several messages interpolate an action straight into text a
        user reads. Naming it here fixes all of them at once.
        """
        return self.name.replace("_", " ").capitalize()


class CalculationType(Enum):
    """Calculation type enumeration."""

    ACQUISITION = 1
    DISPOSAL = 2


@dataclass(frozen=True)
class TransactionSource:
    """Where a transaction was read from.

    ``index`` is the position the parser read this row at within one source
    file, and it is what says which of two rows on one day came first. It is
    not ``row``: a parser is free to emit rows in an order the file does not
    use, and Freetrade does exactly that because its export is newest first.
    ``row`` is the line the row came from, for pointing an error at it.

    ``account`` is an opaque token for the boundary the user declared by
    passing one file or one directory. It is calculation-local: it says that
    two transactions were configured as one account, never which account, and
    nothing about it may be read as a broker account identifier.
    """

    parser: str | None = None
    account: str | None = None
    file: Path | None = None
    row: int | None = None
    index: int | None = None
    # The instant the export stated, where it states one at all.
    timestamp: datetime.datetime | None = None
    # Whether this source documents that rows sharing a date are in the order
    # they happened. A hand-written RAW history says so; a broker export does
    # not, and several parsers reorder their rows for the calculation's sake,
    # so `index` says which row was read first and nothing more.
    rows_in_time_order: bool = False


@dataclass
class BrokerTransaction:
    """Broker transaction data."""

    date: datetime.date
    action: ActionType
    symbol: str | None
    description: str
    quantity: Decimal | None
    price: Decimal | None
    fees: Decimal
    amount: Decimal | None
    currency: CurrencyCode
    broker: str
    isin: Isin | None = None
    # Fees paid in currencies other than `currency`, keyed by their own
    # currency. Converted and folded into `fees` by the calculation engine.
    foreign_fees: dict[CurrencyCode, Decimal] = field(default_factory=dict)
    # The other count this row could be stating, where an export does not say
    # whether a share count predating a split was restated for it. Set only
    # when the two cannot be told apart, so that whichever the user confirms
    # can settle it.
    ambiguous_quantity: Decimal | None = None
    # Where this row was read from. Excluded from equality and from any
    # __hash__: parsers deduplicate overlapping exports by comparing
    # transactions, and a field that varies with the file would keep both
    # copies of every row two exports share. Out of the repr, which is a
    # field dump for debugging. Its file and row are in __str__, which is
    # what the errors print, because they are what let a reader find the row
    # in their own export.
    source: TransactionSource | None = field(default=None, compare=False, repr=False)
    # This row's count restated into the units in force after a share
    # reorganisation later the same day. The exported quantity, price and
    # amount are left alone: they are what validation, deduplication and
    # diagnostics read, and a restated count paired with the exported price
    # would look like a discrepancy. Set by the calculator, so it takes no
    # part in equality either, nor in anything printed to the user.
    calculation_quantity: Decimal | None = field(
        default=None, compare=False, repr=False
    )
    # Present on broker rows for exchange-traded equity options.
    option_contract: OptionContract | None = None
    # Present only on a written option's opening row after its lifecycle has
    # been reconciled by the broker parser.
    written_option_tax: WrittenOptionTaxData | None = None
    # Premiums from assigned options are converted on their original dates and
    # folded into the tax basis/proceeds of the underlying share transaction.
    capital_adjustments: list[CapitalAdjustment] = field(default_factory=list)
    # False on a row whose tax event is dated apart from the cash it involves,
    # which another row carries on the date the money really moved. The row
    # still acquires or disposes of the shares; it leaves the cash balance,
    # and the balance check, to that other row. Out of the repr and out of
    # __str__, so no error prints it: the balance error lists only rows that
    # did move cash, so the flag reads True on every one of them.
    # `--dump-transactions` does export it.
    affects_cash_balance: bool = field(default=True, repr=False)

    @property
    def pool_quantity(self) -> Decimal | None:
        """The count to pool, in the units in force at the end of the day."""
        if self.calculation_quantity is not None:
            return self.calculation_quantity
        return self.quantity

    def __post_init__(self) -> None:
        """Validate BrokerTransaction data."""
        # Callers that bypass the type checker still get validated values;
        # to mypy the annotations already rule this out.
        if not isinstance(self.currency, CurrencyCode):
            self.currency = CurrencyCode(self.currency)  # type: ignore[unreachable]
        if self.isin is not None and not isinstance(self.isin, Isin):
            self.isin = Isin(self.isin)  # type: ignore[unreachable]
        if any(not isinstance(key, CurrencyCode) for key in self.foreign_fees):
            # Keys that normalise to the same code are fees in one currency,
            # so they add up rather than overwrite each other.
            coerced: dict[CurrencyCode, Decimal] = {}
            for key, fee in self.foreign_fees.items():
                code = CurrencyCode(key)
                coerced[code] = coerced.get(code, Decimal(0)) + fee
            self.foreign_fees = coerced

    @override
    def __str__(self) -> str:
        """Describe this row for someone reading an error about it.

        Errors print a whole transaction, and the generated ``__repr__`` is
        Python syntax carrying every internal field, including several the
        user never supplied. This says what the export stated and where it
        was read from, and leaves out what it did not state. ``__repr__``
        is unchanged, so debugging still has the full dump.

        Figures are shortened only where that is marked, so a figure the
        export states is printed as it states it.

        Two lines: the row, then the context it was read in. An error can
        list a row for every holding, and each line spent on one row is a
        line the next one is pushed down by.
        """
        what = [self.date.isoformat(), str(self.action)]
        if self.quantity is not None:
            what.append(display_str(self.quantity))
        if self.ambiguous_quantity is not None:
            what.append(f"(or {display_str(self.ambiguous_quantity)})")
        if self.symbol is not None:
            what.append(self.symbol)

        # The price is labelled and grouped with the other figures rather
        # than trailing the symbol: an option's symbol already ends in "at a
        # strike of N", and "at a strike of 50 at 1 USD" reads as one clause.
        money = []
        if self.price is not None:
            money.append(f"price {display_str(self.price)}")
        if self.amount is not None:
            money.append(f"amount {display_str(self.amount)}")
        if self.fees:
            money.append(f"fees {display_str(self.fees)}")
        # The transaction's own currency is stated once, against the first
        # figure in it; a fee paid in another currency carries its own.
        if money:
            money[0] = f"{money[0]} {self.currency}"
        money += [
            f"fees {display_str(fee)} {code}"
            for code, fee in sorted(self.foreign_fees.items())
        ]

        headline = " ".join(what)
        if money:
            headline += ", " + ", ".join(money)

        # Never the source's `account`: it is a calculation-local token for
        # the boundary the user declared, and reads as an account number it
        # is not.
        # Broker, description and ISIN are one thing said three ways, so
        # they read as one phrase. The one comma left is the break that
        # matters, from what the row is to where it was read from.
        what_it_is = self.broker
        if self.description and self.description != self.symbol:
            what_it_is += f' "{self.description}"'
        if self.isin is not None:
            what_it_is += f" (ISIN {self.isin})"

        where = [what_it_is]
        if self.source is not None and self.source.file is not None:
            # "row N of the file", not "the file, row N": this is one item in
            # a comma-separated line, and a trailing ", row 8" reads as
            # another item rather than as part of the file it belongs to.
            # Last, so that a long path wraps with nothing after it.
            place = f"row {self.source.row} of " if self.source.row is not None else ""
            where.append(f"read from {place}{self.source.file}")

        return f"{headline}\n  {', '.join(where)}"


class RuleType(Enum):
    """HMRC rule type."""

    SECTION_104 = 1
    SAME_DAY = 2
    BED_AND_BREAKFAST = 3
    SPIN_OFF = 4
    DIVIDEND = 5
    INTEREST = 6
    EXCESS_REPORTED_INCOME = 7
    EXCESS_REPORTED_INCOME_DISTRIBUTION = 8
    RENAME = 9
    INTEREST_TAX = 10
    TRANSFER_TO_SPOUSE = 11
    STOCK_SPLIT = 12
    OPTION = 13


@dataclass
class Dividend:
    """Class representing a dividend event."""

    date: datetime.date
    symbol: str
    amount: Decimal
    tax_at_source: Decimal
    is_interest: bool
    tax_treaty: TaxTreaty | None

    @property
    def tax_treaty_amount(self) -> Decimal:
        """Dividend amount reclaimable under the tax treaty (0 if none)."""
        if self.tax_treaty is None:
            return Decimal(0)
        return self.amount * self.tax_treaty.treaty_rate


class CalculationEntry:
    """Calculation entry for final report."""

    def __init__(
        self,
        rule_type: RuleType,
        quantity: Decimal,
        amount: Decimal,
        fees: Decimal,
        new_quantity: Decimal,
        new_pool_cost: Decimal,
        gain: Decimal | None = None,
        allowable_cost: Decimal | None = None,
        bed_and_breakfast_date_index: datetime.date | None = None,
        spin_off: SpinOff | None = None,
        dividend: Dividend | None = None,
        eris: list[ExcessReportedIncome] | None = None,
        renamed_to: str | None = None,
        stock_split: StockSplitDetail | None = None,
    ):
        """Create calculation entry."""
        self.rule_type = rule_type
        self.quantity = quantity
        self.amount = amount
        self.allowable_cost = (
            allowable_cost if allowable_cost is not None else Decimal(0)
        )
        self.fees = fees
        self.gain = gain if gain is not None else Decimal(0)
        self.new_quantity = new_quantity
        self.new_pool_cost = new_pool_cost
        self.bed_and_breakfast_date_index = bed_and_breakfast_date_index
        self.spin_off = spin_off
        self.dividend = dividend
        self.eris = eris or []
        self.renamed_to = renamed_to
        self.stock_split = stock_split
        if self.rule_type == RuleType.EXCESS_REPORTED_INCOME:
            assert self.allowable_cost > 0, str(self)
            assert approx_equal(
                self.allowable_cost, self.amount + self.new_pool_cost
            ), (
                f"Mismatch: {self.allowable_cost} != "
                f"{self.amount} + {self.new_pool_cost} (for {self})"
            )
        elif self.amount >= 0 and self.rule_type not in {
            RuleType.SPIN_OFF,
            RuleType.DIVIDEND,
            RuleType.INTEREST,
            RuleType.INTEREST_TAX,
            RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
            RuleType.RENAME,
        }:
            # An entry that states a gain has to add up. One that states none
            # is an acquisition, whose amount is the cost it took on, written
            # negative. A cost of nothing is written as zero, which the sign
            # alone reads as proceeds of nothing.
            if gain is not None:
                assert self.gain == self.amount + self.fees - self.allowable_cost, (
                    f"Mismatch: {self.gain} != "
                    f"{self.amount} + {self.fees} - {self.allowable_cost} (for {self})"
                )

    @override
    def __repr__(self) -> str:
        """Return print representation."""
        return f"<CalculationEntry {self!s}>"

    @override
    def __str__(self) -> str:
        """Return string representation."""
        return (
            f"{self.rule_type.name.replace('_', ' ')}, "
            f"quantity: {self.quantity}, "
            f"amount: {self.amount}, "
            f"allowable cost: {self.allowable_cost}, "
            f"fees: {self.fees}, "
            f"gain: {self.gain}, "
            f"new pool cost: {self.new_pool_cost}"
        )


CalculationLog = dict[datetime.date, dict[str, list[CalculationEntry]]]


@dataclass
class Position:
    """A single position in the portfolio."""

    quantity: Decimal = Decimal(0)
    amount: Decimal = Decimal(0)

    def __add__(self, other: Position) -> Position:
        """Add two positions."""
        return Position(
            self.quantity + other.quantity,
            normalize_amount(self.amount + other.amount),
        )

    def __sub__(self, other: Position) -> Position:
        """Subtract two positions."""
        return Position(
            self.quantity - other.quantity,
            normalize_amount(self.amount - other.amount),
        )

    @override
    def __str__(self) -> str:
        """Return string representation."""
        return str(round_decimal(self.quantity, 2))


class PortfolioEntry:
    """A single symbol entry for the portfolio in the final report."""

    def __init__(
        self,
        symbol: str,
        quantity: Decimal,
        amount: Decimal,
        unrealized_gains: Decimal | None,
    ):
        """Create portfolio entry."""
        self.symbol = symbol
        self.quantity = quantity
        self.amount = amount
        self.unrealized_gains = unrealized_gains

    def unrealized_gains_str(self) -> str:
        """Format the unrealized gains to show in the report."""
        if self.unrealized_gains is None:
            str_val = "unknown"
        else:
            str_val = f"£{round_decimal(self.unrealized_gains, 2):,}"

        return f" (unrealized gains: {str_val})"

    @override
    def __repr__(self) -> str:
        """Return print representation."""
        return f"<PortfolioEntry {self!s}>"

    @override
    def __str__(self) -> str:
        """Return string representation."""
        return (
            f"{self.symbol}: {round_decimal(self.quantity, 2):,}, "
            f"£{round_decimal(self.amount, 2):,}"
        )


@dataclass
class CapitalGainsReport:
    """Store calculated report."""

    tax_year: int
    portfolio: list[PortfolioEntry]
    disposal_count: int
    disposal_proceeds: Decimal
    allowable_costs: Decimal
    capital_gain: Decimal
    capital_loss: Decimal
    capital_gain_allowance: Decimal | None
    dividend_allowance: Decimal | None
    calculation_log: CalculationLog
    calculation_log_yields: CalculationLog
    total_uk_interest: Decimal
    total_foreign_interest: Decimal
    total_interest_tax: Decimal
    show_unrealized_gains: bool
    # Custom reporting period within the tax year, if one was requested.
    period_start: datetime.date | None = None
    period_end: datetime.date | None = None
    # Losses on gifts, kept out of capital_loss: a loss on a disposal to a
    # connected person is a clogged loss (TCGA 1992 s18(3)). Negative or zero.
    gift_loss: Decimal = Decimal(0)
    # Disposals of instruments the user classified as CGT-exempt, kept out of
    # disposal_count and disposal_proceeds: an exempt asset is not a chargeable
    # asset, so neither its gain nor its loss reaches the calculation.
    exempt_disposal_count: int = 0
    exempt_disposal_proceeds: Decimal = Decimal(0)

    def period_label(self) -> str | None:
        """Label for a custom reporting period, None for a full tax year."""
        if self.period_start is None or self.period_end is None:
            return None
        return f"period {self.period_start} to {self.period_end}"

    @property
    def title_period(self) -> str:
        """Reporting period rendered in the PDF report title."""
        if self.period_start is not None and self.period_end is not None:
            return f"{self.period_start} to {self.period_end}"
        return f"{self.tax_year}-{(self.tax_year + 1) % 100:02d}"

    def _filter_calculation_log(
        self, calculation_log: CalculationLog, rule_type: RuleType
    ) -> Generator[CalculationEntry]:
        for data in calculation_log.values():
            for entry_list in data.values():
                for entry in entry_list:
                    if entry.rule_type == rule_type:
                        yield entry

    def total_unrealized_gains(self) -> Decimal:
        """Total unrealized gains across portfolio."""
        return sum(
            (
                h.unrealized_gains
                for h in self.portfolio
                if h.unrealized_gains is not None
            ),
            Decimal(0),
        )

    def total_gain(self) -> Decimal:
        """Total capital gain."""
        return self.capital_gain + self.capital_loss

    def taxable_gain(self) -> Decimal:
        """Taxable gain with current allowance."""
        if self.capital_gain_allowance is None:
            raise CalculationError(
                f"Taxable gain cannot be calculated because the capital gains "
                f"allowance for {self.tax_year}/{self.tax_year + 1} is unavailable"
            )
        return max(Decimal(0), self.total_gain() - self.capital_gain_allowance)

    def total_eri_amount(self, *, is_interest: bool) -> Decimal:
        """Return the total ERI distribution amount.

        Covers interest funds if is_interest is True, otherwise dividend funds.
        """
        total = Decimal(0)
        for item in self._filter_calculation_log(
            self.calculation_log_yields, RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION
        ):
            assert item.eris
            assert len(item.eris) == 1
            if item.eris[0].is_interest == is_interest:
                total += item.amount
        return total

    def total_dividends_amount(self) -> Decimal:
        """Total dividends amount."""
        total = Decimal(0)
        for item in self._filter_calculation_log(
            self.calculation_log_yields, RuleType.DIVIDEND
        ):
            assert item.dividend is not None
            if not item.dividend.is_interest:
                total += item.amount

        total += self.total_eri_amount(is_interest=False)

        return total

    def total_dividend_taxes_in_tax_treaties_amount(self) -> Decimal:
        """Total taxes to be reclaimed due to tax treaties."""
        total = Decimal(0)
        for item in self._filter_calculation_log(
            self.calculation_log_yields, RuleType.DIVIDEND
        ):
            assert item.dividend is not None
            if not item.dividend.is_interest:
                total += item.dividend.tax_treaty_amount
        return total

    def total_dividend_taxable_gain(self) -> Decimal:
        """Total taxable gain after all allowances."""
        return max(
            Decimal(0),
            self.total_dividends_amount()
            - (self.dividend_allowance or Decimal(0))
            - self.total_dividend_taxes_in_tax_treaties_amount(),
        )

    @override
    def __repr__(self) -> str:
        """Return print representation."""
        return f"<CapitalGainsReport: {self!s}>"

    @override
    def __str__(self) -> str:
        """Return string representation."""
        from .render_text import render_text  # noqa: PLC0415

        return render_text(self)
