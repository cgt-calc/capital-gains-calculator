"""Mutable working state of the capital gains calculator."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, fields
from decimal import Decimal
from typing import TYPE_CHECKING

from .model import ExcessReportedIncomeDistribution, ForeignCurrencyAmount, Position

if TYPE_CHECKING:
    import datetime

    from .model import (
        AcquisitionLog,
        CalculationEntry,
        CalculationLog,
        CurrencyCode,
        ExcessReportedIncomeDistributionLog,
        ExcessReportedIncomeLog,
        ForeignAmountLog,
        HmrcTransactionLog,
        OptionDisposalData,
        SpinOff,
    )
    from .rename_planning import RenameComponent
    from .stock_splits import SplitTransformation


@dataclass
class PreparedHistory:
    """What the first pass records for the second pass to replay.

    Everything here is written while transactions are being read, and read
    again while they are matched. The four scratch fields
    (split_day_capacity, holding_sources, gift_prices, rename_components) are
    the first pass's own working notes: nothing in the second pass looks at
    them.
    """

    # What each day added to a holding, kept apart by how it got there so
    # that matching can ask for the day's genuine acquisitions without
    # reconstructing them from a total (see `DayAcquisitions`).
    acquisition_list: AcquisitionLog = field(default_factory=dict)
    disposal_list: HmrcTransactionLog = field(default_factory=dict)
    # Grants of written options, by date and contract name. Kept apart from
    # disposal_list: a grant disposes of the option the grant creates, not of
    # units of a holding, so it has no pool to draw a cost from.
    option_disposal_list: dict[datetime.date, dict[str, OptionDisposalData]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    # No gain/no loss transfers to a spouse/civil partner. Kept separate from
    # disposal_list so they never enter the taxable disposal totals.
    transfer_to_spouse_list: HmrcTransactionLog = field(default_factory=dict)
    # What each share reorganisation does to a holding, by date and
    # symbol. Decided once from the chronological source view and
    # replayed by both passes: deciding again in the second pass is how
    # one pass ends up substituting the broker's count while the other
    # scales by the ratio.
    splits: dict[datetime.date, dict[str, SplitTransformation]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    # What a decrease may still take, on a day a reorganisation restated
    # a holding. The recorded replay puts the day's increases and its
    # reconciliation before its decreases, and this pass meets them
    # interleaved, so its running count dips below what the day really
    # holds on one side or the other of the correction.
    split_day_capacity: dict[tuple[str, datetime.date], Decimal] = field(
        default_factory=dict
    )
    # Which declared account boundaries have put units into each holding.
    # A broker's own single-row split states a change to its own account,
    # so it may only be read as a change to the whole pool when the whole
    # pool came from that one source. Only rows that add units count: a
    # sale or a hand-written gift takes units out of the pool and supplies
    # none, and the sources are forgotten again once a day closes with
    # the holding empty, so that a ticker traded at one broker years ago
    # cannot veto a reorganisation of a pool rebuilt entirely at another.
    holding_sources: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )
    # Stores old->new mapping when a symbol changes its name.
    rename_list: dict[datetime.date, dict[str, str]] = field(
        default_factory=lambda: defaultdict(dict)
    )
    # What a day's renames say is one holding, by date and by each of the
    # names that holding is spelled under. Only the days and holdings
    # `plan_renames` reads that way: any other name is absent, and keeps the
    # pool the RENAME row's own position leaves it under until the day closes.
    rename_components: dict[datetime.date, dict[str, RenameComponent]] = field(
        default_factory=dict
    )

    dividend_list: ForeignAmountLog = field(
        default_factory=lambda: defaultdict(ForeignCurrencyAmount)
    )
    # Withholding is matched to a dividend of the same broker as well as
    # the same symbol, so both are keyed by broker. The dividends stay
    # merged across brokers in dividend_list, which is what the report
    # rows are built from. The currency is part of the key so that rows in
    # different ones are combined at the date of the dividend they are
    # attributed to, not the date they happened to post on.
    dividend_tax_list: dict[
        tuple[str, str, datetime.date, CurrencyCode], ForeignCurrencyAmount
    ] = field(default_factory=lambda: defaultdict(ForeignCurrencyAmount))
    dividend_dates: dict[tuple[str, str], set[datetime.date]] = field(
        default_factory=lambda: defaultdict(set)
    )
    interest_list: dict[
        tuple[str, CurrencyCode, datetime.date], ForeignCurrencyAmount
    ] = field(default_factory=lambda: defaultdict(ForeignCurrencyAmount))
    interest_tax_list: dict[
        tuple[str, CurrencyCode, datetime.date], ForeignCurrencyAmount
    ] = field(default_factory=lambda: defaultdict(ForeignCurrencyAmount))

    spin_offs: dict[datetime.date, list[SpinOff]] = field(
        default_factory=lambda: defaultdict(list)
    )
    # Disposals that are gifts at market value rather than sales, and
    # whether the recipient is a connected person. A loss on a gift to a
    # connected person is clogged (TCGA 1992 s18(3)) and reported on its own.
    gift_disposals: dict[tuple[datetime.date, str], bool] = field(default_factory=dict)
    # Days on which a symbol was sold, redeemed or cashed out.
    sale_days: set[tuple[datetime.date, str]] = field(default_factory=set)
    # The value per unit each day's gifts to a connected person stated,
    # to refuse a second value: one holding has one market value on a day.
    gift_prices: dict[tuple[datetime.date, str], tuple[Decimal, CurrencyCode]] = field(
        default_factory=dict
    )
    # Days on which a symbol was charged a management fee. A fee adds to
    # the pool's cost with no shares, so it cannot be told from the pool
    # itself once it is in.
    fee_days: set[tuple[datetime.date, str]] = field(default_factory=set)
    eris: ExcessReportedIncomeLog = field(default_factory=lambda: defaultdict(dict))
    # The date of the earliest transaction, where the walk starts.
    first_date: datetime.date | None = None


@dataclass
class RunState:
    """What the second pass builds, and how far the run has got.

    The walk owns everything here. `portfolio` is the exception the split
    makes visible: the first pass fills it with provisional holdings to
    check its own rows against, and the walk clears it and rebuilds it.
    """

    total_uk_interest: Decimal = Decimal(0)
    total_foreign_interest: Decimal = Decimal(0)
    total_interest_tax: Decimal = Decimal(0)

    bnb_list: HmrcTransactionLog = field(default_factory=dict)

    # Log for the report section related only to interests and dividends
    calculation_log_yields: CalculationLog = field(
        default_factory=lambda: defaultdict(dict)
    )

    portfolio: dict[str, Position] = field(
        default_factory=lambda: defaultdict(Position)
    )
    # Whether the first pass has begun, and whether it finished. Ingestion
    # accumulates into the prepared history, so it may only fill this state
    # once, and a run that failed part way leaves a partial history that must
    # not be calculated on.
    ingestion_started: bool = False
    ingested: bool = False
    # The source side of each spin-off, recorded when it is applied and
    # collected into the calculation log by date.
    spin_off_entries: dict[datetime.date, dict[str, list[CalculationEntry]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(list))
    )
    eris_distribution: ExcessReportedIncomeDistributionLog = field(
        default_factory=lambda: defaultdict(
            lambda: defaultdict(ExcessReportedIncomeDistribution)
        )
    )
    # Acquisition cost of a spun-off holding once the source pool is known,
    # by day and symbol. The first pass could only estimate it; the walk
    # works out the real figure when it reaches the day and keeps it here
    # rather than rewriting the recorded acquisition.
    spin_off_corrected_amounts: dict[tuple[datetime.date, str], Decimal] = field(
        default_factory=dict
    )

    def reset(self) -> None:
        """Forget what the last calculation built, keeping how far ingestion got.

        In place, because the collaborators hold this object, not its fields.
        """
        fresh = RunState()
        for spec in fields(self):
            if spec.name not in {"ingestion_started", "ingested"}:
                setattr(self, spec.name, getattr(fresh, spec.name))


@dataclass
class CalculatorState:
    """What the calculator builds up as it walks the transactions.

    Everything here is written during a run, split by how long each part
    lives: the first pass fills `history`, the second fills `run`. Keeping
    them apart is what makes a write that crosses between the passes
    something you can grep for. Configuration and the injected converters
    and fetchers stay on the calculator itself.
    """

    history: PreparedHistory = field(default_factory=PreparedHistory)
    run: RunState = field(default_factory=RunState)
