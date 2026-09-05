"""Second pass: matching disposals against acquisitions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
import datetime
from decimal import Decimal
from fractions import Fraction
import logging
from typing import TYPE_CHECKING, NamedTuple

from .const import (
    BED_AND_BREAKFAST_DAYS,
    ERI_TAX_DATE_DELTA,
    INTERNAL_START_DATE,
    MAX_CONTENDED_DATES_SHOWN,
)
from .exceptions import CalculationError
from .model import (
    CalculationEntry,
    CalculationLog,
    ExcessReportedIncome,
    ExcessReportedIncomeDistribution,
    HmrcTransactionData,
    Position,
    RuleType,
    SpinOff,
)
from .stock_splits import (
    StockSplitDetail,
    StockSplitEvent,
    UnresolvedRatio,
    scale_quantity,
    unscale_quantity,
)
from .transaction_log import add_to_list, has_key
from .util import normalize_amount, round_decimal, strip_zeros

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from .calculator_state import CalculatorState

LOGGER = logging.getLogger(__name__)


class WalkResult(NamedTuple):
    """What one walk over the days accumulated."""

    disposal_count: int
    disposal_proceeds: Decimal
    allowable_costs: Decimal
    capital_gain: Decimal
    capital_loss: Decimal
    gift_loss: Decimal
    exempt_disposal_count: int
    exempt_disposal_proceeds: Decimal
    calculation_log: CalculationLog


class DayIdentity(NamedTuple):
    """What one holding is called on the day a disposal of it is identified."""

    end_name: str
    """The name the day's renames leave the holding under."""

    acquired_under: str
    """The name today's purchases of the holding were recorded under."""

    pool_name: str
    """The name the holding's Section 104 pool is kept under today."""

    names: frozenset[str]
    """Every name today's renames give the holding, this one included."""


@dataclass
class DisposalContext:
    """The running state of one disposal as the identification rules consume it."""

    symbol: str
    identity: DayIdentity
    cost_holder: str
    date_index: datetime.date
    no_gain_no_loss: bool
    disposal: HmrcTransactionData
    disposal_quantity: Decimal
    disposal_price: Decimal
    current_quantity: Decimal
    current_amount: Decimal
    chargeable_gain: Decimal
    calculation_entries: list[CalculationEntry]


class Matcher:
    """Matches disposals against acquisitions across the whole history."""

    def __init__(
        self,
        state: CalculatorState,
        tax_year_start_date: datetime.date,
        tax_year_end_date: datetime.date,
        interest_fund_tickers: list[str],
        date_in_tax_year: Callable[[datetime.date], bool],
        is_cgt_exempt: Callable[[str], bool],
    ):
        """Create matcher object."""
        self.history = state.history
        self.run = state.run
        self.tax_year_start_date = tax_year_start_date
        self.tax_year_end_date = tax_year_end_date
        self.interest_fund_tickers = interest_fund_tickers
        self.date_in_tax_year = date_in_tax_year
        self.is_cgt_exempt = is_cgt_exempt
        # What each name holds as the day being walked opens, before any of
        # the day's rows. Only the days that record a rename need it, and only
        # to tell a holding of a name's own from what the day put there.
        self._day_open_pools: dict[str, Position] = {}

    def get_eri(self, symbol: str, date: datetime.date) -> ExcessReportedIncome | None:
        """Return Excess Reported Income at specific date for the input symbol."""
        return self.history.eris.get(date, {}).get(symbol)

    def _disposal_log_prefix(self, date_index: datetime.date, symbol: str) -> str:
        """Name the kind of disposal recorded for a symbol on a day.

        A sale with a gift to someone unconnected is one ordinary disposal,
        reported as a sale.

        A disposal of an instrument the user has classified as CGT-exempt is
        named "exempt" whichever kind it is, so a gift of one is reported as an
        exempt disposal rather than as a gift.
        """
        if self.is_cgt_exempt(symbol):
            return "exempt"
        key = (date_index, symbol)
        connected = self.history.gift_disposals.get(key)
        if connected:
            return "gift"
        if connected is None or key in self.history.sale_days:
            return "sell"
        return "gift-unconnected"

    def process_acquisition(
        self,
        symbol: str,
        date_index: datetime.date,
    ) -> list[CalculationEntry]:
        """Process single acquisition."""
        record = self.history.acquisition_list[date_index][symbol]
        acquisition = record.total
        acquisition_amount = acquisition.amount
        spin_offs_here = [
            spin_off
            for spin_off in self.history.spin_offs.get(date_index, [])
            if spin_off.dest == symbol
        ]
        spin_off = spin_offs_here[0] if spin_offs_here else None
        if spin_offs_here:
            # A spin-off carries a share of the source's cost across, and the
            # original cost is apportioned between the two holdings at the
            # reorganisation itself (CG51976). The first pass could only
            # estimate the source pool, so it recorded an estimate for this
            # holding; here the pool is authoritative and in GBP. Record the
            # corrected figure on the run and take the source's side at the
            # same moment, so that what one gives up is what the other
            # receives. The recorded acquisition is left alone: it may also
            # hold ordinary purchases of the same symbol, and the first pass's
            # history stays as written.
            carried = Decimal(0)
            # Several rows for one spin-off, as when the holding is split
            # across brokers, are one reorganisation: the split of value is by
            # the whole of what was received, and the source gives up its
            # share once. Rows from different sources stay separate events,
            # taken in the order they happened.
            events: dict[str, list[SpinOff]] = {}
            for row in spin_offs_here:
                events.setdefault(row.source, []).append(row)
            for source_symbol, rows in events.items():
                first = rows[0]
                source = self.run.portfolio[
                    self._spin_off_pool(date_index, source_symbol, first.dest)
                ]
                assert first.source_price is not None
                assert first.dest_price is not None
                received = sum((row.quantity for row in rows), Decimal(0))
                source_value = source.quantity * first.source_price
                total_value = source_value + received * first.dest_price
                proportion = source_value / total_value if total_value else Decimal(1)
                share = round_decimal((1 - proportion) * source.amount, 2)
                carried += share
                self.run.spin_off_entries[date_index][source_symbol].append(
                    CalculationEntry(
                        RuleType.SPIN_OFF,
                        quantity=source.quantity,
                        amount=-source.amount,
                        new_quantity=source.quantity,
                        gain=None,
                        # Fees, if any, are already accounted for on the
                        # acquisition of spun-off shares
                        fees=Decimal(0),
                        new_pool_cost=source.amount - share,
                        allowable_cost=source.amount - share,
                        spin_off=first,
                    )
                )
                source.amount -= share
            # The estimate is not used at all: the real share of the source
            # pool goes in beside what the day genuinely put into the holding.
            acquisition_amount = (
                record.purchased.amount + record.cost_only.amount + carried
            )
            self.run.spin_off_corrected_amounts[date_index, symbol] = acquisition_amount
        modified_amount = acquisition_amount
        position = self.run.portfolio[symbol]
        calculation_entries = []
        # A management fee is cost with no shares, and a fee of nothing is
        # neither, so a day can record either without the other.
        assert acquisition.quantity >= 0
        assert acquisition_amount >= 0

        bnb_acquisition = HmrcTransactionData()
        bed_and_breakfast_fees = Decimal(0)

        # What an earlier disposal took under the 30-day rule has to leave the
        # pool at the price it was given, and it was identified against the
        # day's genuine acquisitions alone. Priced over everything the day
        # brought in, a day that also received shares by reorganisation would
        # hand the disposal one figure and take a smaller one out of the pool,
        # leaving the difference to be relieved a second time.
        identifiable = self._identifiable_acquisition(date_index, symbol)
        if identifiable.quantity > 0 and has_key(self.run.bnb_list, date_index, symbol):
            bnb_acquisition = self.run.bnb_list[date_index][symbol]
            assert bnb_acquisition.quantity <= identifiable.quantity
            # Multiply by the B&B quantity before dividing to avoid rounding errors from division
            bnb_cost_basis = normalize_amount(
                (bnb_acquisition.quantity * identifiable.amount) / identifiable.quantity
            )
            modified_amount -= bnb_cost_basis
            modified_amount += bnb_acquisition.amount
            assert modified_amount > 0
            bed_and_breakfast_fees = (
                identifiable.fees * bnb_acquisition.quantity / identifiable.quantity
            )
            calculation_entries.append(
                CalculationEntry(
                    rule_type=RuleType.BED_AND_BREAKFAST,
                    quantity=bnb_acquisition.quantity,
                    amount=-bnb_acquisition.amount,
                    new_quantity=position.quantity + bnb_acquisition.quantity,
                    new_pool_cost=position.amount + bnb_acquisition.amount,
                    fees=bed_and_breakfast_fees,
                    allowable_cost=acquisition_amount,
                    eris=bnb_acquisition.eris,
                )
            )
        self.run.portfolio[symbol] += Position(
            acquisition.quantity,
            modified_amount,
        )
        if (
            acquisition.quantity - bnb_acquisition.quantity > 0
            or bnb_acquisition.quantity == 0
        ):
            calculation_entries.append(
                CalculationEntry(
                    rule_type=RuleType.SECTION_104,
                    quantity=acquisition.quantity - bnb_acquisition.quantity,
                    amount=-(modified_amount - bnb_acquisition.amount),
                    new_quantity=position.quantity + acquisition.quantity,
                    new_pool_cost=position.amount + modified_amount,
                    fees=acquisition.fees - bed_and_breakfast_fees,
                    allowable_cost=acquisition_amount,
                    spin_off=spin_off,
                )
            )
        return calculation_entries

    def process_disposal(
        self,
        symbol: str,
        date_index: datetime.date,
        *,
        no_gain_no_loss: bool = False,
    ) -> tuple[Decimal, list[CalculationEntry]]:
        """Process a single disposal.

        With ``no_gain_no_loss`` the event is a transfer to a spouse/civil
        partner: it uses the same same-day/30-day/Section 104 identification as a
        disposal, but each matched tranche's deemed proceeds equal its allowable
        cost so the gain is nil (TCGA 1992 s58), and the source rows come from
        ``transfer_to_spouse_list`` (which carry no fees and no proceeds).
        """
        disposal = (
            self.history.transfer_to_spouse_list
            if no_gain_no_loss
            else self.history.disposal_list
        )[date_index][symbol]
        # A transfer to a spouse is recorded after the day's renames have been
        # applied, so the pool is read under the name the day leaves the
        # holding under rather than the name that opened the day holding it.
        # Which acquisition the day's purchases make up is the same question
        # either way.
        identity = self._day_identity(symbol, date_index, after_renames=no_gain_no_loss)
        # Where the day's purchases of the holding sit. That is the name they
        # were recorded under, unless the day's renames have already merged
        # that pool into the one the holding ends the day under, which they
        # have by the time a transfer to a spouse is recorded.
        cost_holder = identity.pool_name if no_gain_no_loss else identity.acquired_under
        pool = self.run.portfolio[identity.pool_name]
        ctx = DisposalContext(
            symbol=symbol,
            identity=identity,
            cost_holder=cost_holder,
            date_index=date_index,
            no_gain_no_loss=no_gain_no_loss,
            disposal=disposal,
            disposal_quantity=disposal.quantity,
            disposal_price=disposal.amount / disposal.quantity,
            current_quantity=pool.quantity,
            current_amount=pool.amount,
            chargeable_gain=Decimal(0),
            calculation_entries=[],
        )
        # What there is to identify the disposal against: the Section 104 pool
        # the holding keeps under one name, and the day's purchases of it,
        # which a rename today may have put under another.
        available = ctx.current_quantity
        if cost_holder != identity.pool_name:
            available += self.run.portfolio.get(cost_holder, Position()).quantity
        assert ctx.disposal_quantity <= available
        self._match_same_day(ctx)
        self._match_bed_and_breakfast(ctx)
        self._match_section_104(ctx)

        assert round_decimal(ctx.disposal_quantity, 23) == 0, (
            f"disposal quantity {ctx.disposal_quantity}"
        )
        self.run.portfolio[ctx.identity.pool_name] = Position(
            ctx.current_quantity, normalize_amount(ctx.current_amount)
        )
        ctx.chargeable_gain = round_decimal(ctx.chargeable_gain, 2)
        return ctx.chargeable_gain, ctx.calculation_entries

    def _match_same_day(self, ctx: DisposalContext) -> None:
        """Identify the disposal against the same day's acquisitions."""
        # Same day rule is first, against the day's real purchases only. What a
        # reorganisation brought in is not an acquisition (TCGA 1992 s127,
        # CG51805), so matching it would spread the day's purchase cost over
        # shares that were never bought, and price the sale at the average of
        # the two. What those shares cost reaches a disposal through the
        # Section 104 pool instead (see _identifiable_acquisition).
        # The day's purchases are one acquisition of this holding (TCGA 1992
        # s105(1)(a)) whichever of its names today's rows spell them under.
        acquired_under = ctx.identity.acquired_under
        same_day_acquisition = self._identifiable_acquisition(
            ctx.date_index, acquired_under
        )
        if same_day_acquisition.quantity > 0:
            available_quantity = min(
                ctx.disposal_quantity, same_day_acquisition.quantity
            )
            if available_quantity > 0:
                fees = ctx.disposal.fees * available_quantity / ctx.disposal.quantity

                # Multiply by available_quantity before divide to avoid rounding errors from division
                acquisition_cost = normalize_amount(
                    (available_quantity * same_day_acquisition.amount)
                    / same_day_acquisition.quantity
                )

                acquisition_price = acquisition_cost / available_quantity
                # No gain/no loss: deemed proceeds equal the allowable cost.
                same_day_amount = (
                    acquisition_cost
                    if ctx.no_gain_no_loss
                    else available_quantity * ctx.disposal_price
                )
                same_day_proceeds = same_day_amount + fees
                same_day_allowable_cost = acquisition_cost + fees
                same_day_gain = same_day_proceeds - same_day_allowable_cost
                ctx.chargeable_gain += same_day_gain
                LOGGER.debug(
                    "SAME DAY, quantity %s, gain %s, disposal price %s, "
                    "acquisition price %s",
                    available_quantity,
                    same_day_gain,
                    ctx.disposal_price,
                    acquisition_price,
                )
                ctx.disposal_quantity -= available_quantity
                # These shares shouldn't be added to Section 104 holding. They
                # went into the pool of the name they were bought under, which
                # a rename today need not have been the name being disposed of,
                # so they come back out of that same pool: the two are one
                # holding by the end of the day either way, and taking the cost
                # out of the wrong one leaves both wrong until then.
                if ctx.cost_holder == ctx.identity.pool_name:
                    ctx.current_quantity -= available_quantity
                    ctx.current_amount -= acquisition_cost
                    if ctx.current_quantity == 0:
                        assert round_decimal(ctx.current_amount, 23) == 0, (
                            f"current amount {ctx.current_amount}"
                        )
                else:
                    self.run.portfolio[ctx.cost_holder] -= Position(
                        available_quantity, acquisition_cost
                    )
                ctx.calculation_entries.append(
                    CalculationEntry(
                        rule_type=(
                            RuleType.TRANSFER_TO_SPOUSE
                            if ctx.no_gain_no_loss
                            else RuleType.SAME_DAY
                        ),
                        quantity=available_quantity,
                        amount=same_day_amount,
                        gain=same_day_gain,
                        allowable_cost=same_day_allowable_cost,
                        fees=fees,
                        new_quantity=ctx.current_quantity,
                        new_pool_cost=ctx.current_amount,
                    )
                )

    def _match_bed_and_breakfast(self, ctx: DisposalContext) -> None:
        """Identify the disposal against the next 30 days' acquisitions."""
        # Bed and breakfast rule next
        if ctx.disposal_quantity > 0:
            eris = []
            eri = self.get_eri(ctx.symbol, ctx.date_index)
            if eri:
                eris.append(eri)

            # A reorganisation between the disposal and the repurchase
            # leaves the two counted in different units. The ratios compose
            # exactly, as one fraction, and are divided by once at the end:
            # multiplying rounded decimal multipliers day by day compounds the
            # very error the exact ratio removed.
            cumulative_ratio = Fraction(1)
            unresolved_splits: list[tuple[datetime.date, StockSplitEvent]] = []
            # A rename recorded on the disposal date itself has taken effect by
            # the time the following days start, so the walk begins under the
            # name it leaves the holding under.
            effective_symbol = ctx.identity.end_name

            for i in range(BED_AND_BREAKFAST_DAYS):
                search_index = ctx.date_index + datetime.timedelta(days=i + 1)
                # HMRC treats renames as the same security for B&B purposes.
                # A reorganisation is recorded under the name the holding
                # carried when it happened, which on a day the holding is also
                # renamed is the name it entered the day under. Look there
                # first and under the day's new name second, then carry on
                # under the name the day ends with. Only one can be there:
                # a day whose rename pools two holdings is refused before
                # either pass reaches it (see apply_split_openings). A
                # reorganisation of a holding renamed *into* this name is
                # deliberately not picked up: the shares disposed of here left
                # before that one arrived, so they were never its shares.
                renames = self.history.rename_list.get(search_index, {})
                day_splits = self.history.splits.get(search_index, {})
                renamed_symbol = renames.get(effective_symbol, effective_symbol)
                transformation = day_splits.get(effective_symbol) or day_splits.get(
                    renamed_symbol
                )
                effective_symbol = renamed_symbol
                if transformation is not None:
                    if transformation.ratio is None:
                        unresolved_splits.append((search_index, transformation.event))
                    else:
                        cumulative_ratio *= transformation.ratio
                    # A reorganisation this close to a disposal is worth a
                    # second look, whether or not a match follows.
                    LOGGER.warning(
                        "A split happened shortly after a disposal of %s, double check these transactions."
                        "Disposed on %s and split happened on %s",
                        ctx.symbol,
                        ctx.date_index,
                        search_index,
                    )

                # ERI is distributed annually, but when a fund closes we might have
                # multiple ERI distributions in close succession
                eri = self.get_eri(effective_symbol, search_index)
                if eri:
                    eris.append(eri)
                acquisition = self._identifiable_acquisition(
                    search_index, effective_symbol
                )
                if acquisition.quantity > 0:
                    bnb_acquisition = (
                        self.run.bnb_list[search_index][effective_symbol]
                        if has_key(self.run.bnb_list, search_index, effective_symbol)
                        else HmrcTransactionData()
                    )
                    assert bnb_acquisition.quantity <= acquisition.quantity

                    same_day_disposal = self._same_day_claims(
                        search_index, effective_symbol
                    )
                    if same_day_disposal.quantity > acquisition.quantity:
                        # If the number of shares disposed of exceeds the number
                        # acquired on the same day the excess shares will be identified
                        # in the normal way.
                        continue

                    # This can be some management fee entry or already used
                    # by bed and breakfast rule
                    if (
                        acquisition.quantity
                        - same_day_disposal.quantity
                        - bnb_acquisition.quantity
                        == 0
                    ):
                        continue
                    # Bed and breakfasting is a record of how the disposal was
                    # matched rather than a problem. Surface it for the computed
                    # tax year; the rest of the history walk logs it at DEBUG.
                    LOGGER.log(
                        logging.INFO
                        if self.date_in_tax_year(ctx.date_index)
                        else logging.DEBUG,
                        "Bed & breakfast match: %s %s %s, re-acquired %s",
                        ctx.symbol,
                        "transferred to spouse" if ctx.no_gain_no_loss else "disposed",
                        ctx.date_index,
                        search_index,
                    )
                    # The conversion needs the ratio, so this is where an
                    # unrecoverable one is refused. Not merely for happening
                    # inside the window: an acquisition before it, or a
                    # disposal already matched, needs no conversion at all.
                    if unresolved_splits:
                        split_date, unresolved_event = unresolved_splits[0]
                        raise CalculationError(
                            self._unresolved_bnb_message(
                                unresolved_event, ctx.symbol, ctx.date_index, split_date
                            )
                        )
                    if cumulative_ratio != 1:
                        LOGGER.warning(
                            "Bed & breakfast for %s is taking into account a %sx split "
                            "that happened shortly before the repurchase of shares",
                            ctx.symbol,
                            cumulative_ratio,
                        )
                    # Everything reserved here is counted in the acquisition's
                    # own units, so the whole remainder converts to the
                    # disposal's units once. Converting only the acquisition
                    # would subtract post-split counts from a pre-split one and
                    # can go negative.
                    available_acquisition_units = (
                        acquisition.quantity
                        - same_day_disposal.quantity
                        - bnb_acquisition.quantity
                    )
                    available_quantity, consumed_acquisition_units = (
                        self._match_across_splits(
                            ctx.disposal_quantity,
                            available_acquisition_units,
                            cumulative_ratio,
                            symbol=ctx.symbol,
                            date_index=ctx.date_index,
                            search_index=search_index,
                        )
                    )
                    fees = (
                        ctx.disposal.fees * available_quantity / ctx.disposal.quantity
                    )
                    # Multiply by the consumed quantity before dividing to
                    # avoid rounding errors from division. Both counts here
                    # are in the acquisition's own units.
                    bnb_acquisition_cost = normalize_amount(
                        (consumed_acquisition_units * acquisition.amount)
                        / acquisition.quantity
                    )
                    acquisition_price = bnb_acquisition_cost / available_quantity
                    # No gain/no loss: deemed proceeds equal the allowable cost.
                    bed_and_breakfast_amount = (
                        bnb_acquisition_cost
                        if ctx.no_gain_no_loss
                        else available_quantity * ctx.disposal_price
                    )
                    bed_and_breakfast_proceeds = bed_and_breakfast_amount + fees
                    bed_and_breakfast_allowable_cost = bnb_acquisition_cost + fees
                    # ERI needs to be reported when doing bed and breakfast as if you
                    # held the stocks at the reporting end date.
                    # https://www.rawknowledge.ltd/eri-explained-four-tricky-questions-answered/
                    total_dist_amount = Decimal(0)
                    for eri in eris:
                        eri_distribution = ExcessReportedIncomeDistribution(
                            price=eri.price,
                            amount=available_quantity * eri.price,
                            quantity=available_quantity,
                        )
                        total_dist_amount += eri_distribution.amount
                        if self.date_in_tax_year(eri.distribution_date):
                            self.run.eris_distribution[eri.distribution_date][
                                ctx.symbol
                            ] += eri_distribution

                    bed_and_breakfast_gain = (
                        bed_and_breakfast_proceeds - bed_and_breakfast_allowable_cost
                    )
                    ctx.chargeable_gain += bed_and_breakfast_gain
                    LOGGER.debug(
                        "BED & BREAKFAST, quantity %s, gain %s, disposal price %s, "
                        "acquisition price %s%s",
                        available_quantity,
                        bed_and_breakfast_gain,
                        ctx.disposal_price,
                        acquisition_price,
                        f", added_excess_income: {total_dist_amount}"
                        if total_dist_amount > 0
                        else "",
                    )
                    ctx.disposal_quantity -= available_quantity

                    # Multiply by available_quantity before divide to avoid rounding errors from division
                    amount_delta = normalize_amount(
                        (available_quantity * ctx.current_amount) / ctx.current_quantity
                    )

                    ctx.current_quantity -= available_quantity
                    ctx.current_amount -= amount_delta
                    if ctx.current_quantity == 0:
                        assert round_decimal(ctx.current_amount, 23) == 0, (
                            f"current amount {ctx.current_amount}"
                        )
                    add_to_list(
                        self.run.bnb_list,
                        search_index,
                        effective_symbol,
                        consumed_acquisition_units,
                        amount_delta + total_dist_amount,
                        Decimal(0),
                        eris,
                    )
                    ctx.calculation_entries.append(
                        CalculationEntry(
                            rule_type=(
                                RuleType.TRANSFER_TO_SPOUSE
                                if ctx.no_gain_no_loss
                                else RuleType.BED_AND_BREAKFAST
                            ),
                            quantity=available_quantity,
                            amount=bed_and_breakfast_amount,
                            gain=bed_and_breakfast_gain,
                            allowable_cost=bed_and_breakfast_allowable_cost,
                            fees=fees,
                            bed_and_breakfast_date_index=search_index,
                            new_quantity=ctx.current_quantity,
                            new_pool_cost=ctx.current_amount,
                        )
                    )
                    # If we completely matched the current disposal,
                    # there's no need to keep looking for more B&B days
                    if ctx.disposal_quantity <= 0:
                        break

    def _match_section_104(self, ctx: DisposalContext) -> None:
        """Identify what is left of the disposal against the Section 104 pool."""
        if ctx.disposal_quantity > 0:
            available_quantity = ctx.disposal_quantity
            fees = ctx.disposal.fees * available_quantity / ctx.disposal.quantity

            # Multiply by available_quantity before divide to avoid rounding errors from division
            amount_delta = normalize_amount(
                (available_quantity * ctx.current_amount) / ctx.current_quantity
            )

            # No gain/no loss: deemed proceeds equal the allowable cost.
            r104_amount = (
                amount_delta
                if ctx.no_gain_no_loss
                else available_quantity * ctx.disposal_price
            )
            r104_proceeds = r104_amount + fees
            r104_allowable_cost = amount_delta + fees
            r104_gain = r104_proceeds - r104_allowable_cost
            ctx.chargeable_gain += r104_gain
            LOGGER.debug(
                "SECTION 104, quantity %s, gain %s, proceeds amount %s, "
                "allowable cost %s",
                available_quantity,
                r104_gain,
                r104_proceeds,
                r104_allowable_cost,
            )
            ctx.disposal_quantity -= available_quantity
            ctx.current_quantity -= available_quantity
            ctx.current_amount -= amount_delta
            if ctx.current_quantity == 0:
                assert round_decimal(ctx.current_amount, 10) == 0, (
                    f"current amount {ctx.current_amount}"
                )
            ctx.calculation_entries.append(
                CalculationEntry(
                    rule_type=(
                        RuleType.TRANSFER_TO_SPOUSE
                        if ctx.no_gain_no_loss
                        else RuleType.SECTION_104
                    ),
                    quantity=available_quantity,
                    amount=r104_amount,
                    gain=r104_gain,
                    allowable_cost=r104_allowable_cost,
                    fees=fees,
                    new_quantity=ctx.current_quantity,
                    new_pool_cost=ctx.current_amount,
                )
            )
            ctx.disposal_quantity = Decimal(0)

    def process_rename(self, old: str, new: str) -> CalculationEntry:
        """Transfer pool from old ticker to new ticker (no disposal)."""
        pos = self.run.portfolio.pop(old, Position())
        self.run.portfolio[new] += pos
        return CalculationEntry(
            rule_type=RuleType.RENAME,
            quantity=pos.quantity,
            amount=Decimal(0),
            fees=Decimal(0),
            new_quantity=self.run.portfolio[new].quantity,
            new_pool_cost=self.run.portfolio[new].amount,
            allowable_cost=pos.amount,
            renamed_to=new,
        )

    def apply_split_openings(self, date_index: datetime.date) -> None:
        """Restate every holding a reorganisation touches today.

        A reorganisation restates the day's opening pool before any of the
        day's activity: the rows that preceded it are already in the units it
        leaves behind, so scaling after any of them applies the ratio twice.

        Replays what the first pass recorded rather than deciding again.
        Substituting the broker's count in one pass while scaling by the ratio
        in the other differs by a billionth of a share on the Trading 212
        exports this was built from, which is enough to leave a disposal of
        the whole holding short of zero.
        """
        renames = self.history.rename_list.get(date_index, {})
        for symbol, transformation in sorted(
            self.history.splits.get(date_index, {}).items()
        ):
            chained = self._rename_chain_at(symbol, renames)
            if chained is not None:
                raise CalculationError(
                    self._rename_chain_message(
                        "apply the reorganisation of", symbol, date_index, *chained
                    )
                )
            pooled = self._rename_pooling_with(symbol, date_index, renames)
            if pooled is not None:
                raise CalculationError(
                    self._rename_and_split_message(symbol, date_index, *pooled)
                )
            position = self.run.portfolio[symbol]
            if position.quantity != transformation.day_open_quantity:
                raise CalculationError(
                    f"Cannot apply the reorganisation of {symbol} on "
                    f"{date_index}: the holding opens at "
                    f"{strip_zeros(position.quantity)} units here and at "
                    f"{strip_zeros(transformation.day_open_quantity)} where "
                    "the reorganisation was worked out. Please report this: "
                    "the two passes over the history disagree."
                )
            self.run.portfolio[symbol] = Position(
                transformation.scaled_day_open_quantity, position.amount
            )

    def _holds_units_today(self, symbol: str, date_index: datetime.date) -> bool:
        """Whether a rename today could move units of ``symbol`` anywhere.

        Called from ``apply_split_openings``, before any of the day's rows, so
        the portfolio still holds the day-opening pool; an acquisition later
        today lands before the rename, which is applied at the end of the day.
        """
        if symbol in self.run.portfolio and self.run.portfolio[symbol].quantity != 0:
            return True
        return has_key(self.history.acquisition_list, date_index, symbol)

    @staticmethod
    def _rename_chain_at(
        symbol: str, renames: dict[str, str]
    ) -> tuple[tuple[str, str], tuple[str, str]] | None:
        """Return the two renames that move this holding twice in one day.

        A day's renames are applied in the order the input lists them, so a
        holding renamed onward from the name it was just renamed to ends
        wherever that order puts it, and swapping the two rows moves it
        somewhere else. A date carries no order, so neither reading is
        established and there is nothing to pick between them. A cycle is the
        same thing joined up, and is caught by the same test.
        """
        next_name = renames.get(symbol)
        if next_name is None or next_name not in renames:
            return None
        return (symbol, next_name), (next_name, renames[next_name])

    @staticmethod
    def _renames_reaching(
        symbol: str, renames: dict[str, str]
    ) -> Iterator[tuple[str, tuple[str, str]]]:
        """Yield every other name the day's renames connect this holding to.

        A rename says the two tickers are one security, so what a holding is
        pooled with today is not only the name at the other end of a rename it
        is itself named in: a holding renamed into the name this one is renamed
        to, or renamed along to it through names it never held, arrives just
        the same. So the whole of the day's rename graph connected to this
        holding is walked, in both directions. The ordinary ticker change
        reaches one empty name and stops.

        Each name comes with the rename that reaches it, taking the renames in
        the order the input lists them.
        """
        reached = {symbol}
        frontier = [symbol]
        while frontier:
            name = frontier.pop(0)
            for old_name, new_name in renames.items():
                other = (
                    new_name
                    if name == old_name
                    else old_name
                    if name == new_name
                    else None
                )
                if other is None or other in reached:
                    continue
                reached.add(other)
                frontier.append(other)
                yield other, (old_name, new_name)

    def _rename_pooling_with(
        self,
        symbol: str,
        date_index: datetime.date,
        renames: dict[str, str],
    ) -> tuple[str, tuple[str, str]] | None:
        """Return a second holding the day's renames would pool this one with.

        A rename says the two tickers are one security, so a reorganisation of
        either restates both. The calculator restates one, and the renames
        bring whatever else they reach in afterwards, untouched, so any name
        the renames reach that holds something of its own is the answer.

        Returns the holding it would be pooled with and the rename that
        reaches it.
        """
        for other, rename in self._renames_reaching(symbol, renames):
            if self._holds_units_today(other, date_index):
                return other, rename
        return None

    def _opened_with(self, symbol: str) -> Position:
        """Return what this name held as the day opened, before its own rows."""
        return self._day_open_pools.get(symbol, Position())

    def _shares_already_moved(self, symbol: str, date_index: datetime.date) -> bool:
        """Whether the day's own earlier rows have moved shares from this name.

        What the name opened the day with, plus what the day bought under it,
        is what it would still hold if nothing had left it yet.

        A reorganisation recorded today is neither an acquisition nor a
        disposal (TCGA 1992 s127): it restates the opening holding, and the
        day's passes then reconcile that to the broker's own count. Both
        change the count without anything having left, so the day opens on
        the figure they leave rather than the one the snapshot took.
        """
        transformation = self.history.splits.get(date_index, {}).get(symbol)
        opened_with = (
            self._opened_with(symbol).quantity
            if transformation is None
            else transformation.scaled_day_open_quantity
            + transformation.reconciliation_delta
        )
        return (
            self.run.portfolio.get(symbol, Position()).quantity
            != opened_with + self._matchable_acquisition(date_index, symbol).quantity
        )

    def _holds_cost_without_shares(
        self, symbol: str, date_index: datetime.date
    ) -> bool:
        """Whether this name holds pooled cost that no shares of its own carry.

        A management fee is recorded as cost with no shares, so a name can
        hold one without holding a holding, and there is nothing under that
        name for the same-day rule to identify a disposal against. The cost
        reaches the shares it belongs to only when the day's renames are
        applied, which is after the day's disposals have priced themselves.

        Asked only of the names that are not holding the pool, and those
        opened the day with no shares at all. Such a name holds cost with
        nothing behind it if it brought cost in - which a purchase today
        masks, by making its quantity positive while pricing only its own
        shares - or if the day recorded cost but no shares under it.
        """
        acquired = self._matchable_acquisition(date_index, symbol)
        return self._opened_with(symbol).amount != 0 or (
            acquired.quantity == 0 and acquired.amount != 0
        )

    def _same_day_claims(
        self, date_index: datetime.date, symbol: str
    ) -> HmrcTransactionData:
        """Return what a day's own disposals take out of its acquisition first.

        The same-day rule comes before the 30-day rule (CG51560), so shares
        the day's own disposals are identified against are not there for an
        earlier disposal to claim. A rename recorded that day makes the day's
        rows one holding whichever of its names they state, so a disposal
        under either name claims the same purchase and has to be reserved
        here too. A transfer to a spouse competes for it like a sale.
        """
        names = {symbol}
        names.update(
            other
            for other, _ in self._renames_reaching(
                symbol, self.history.rename_list.get(date_index, {})
            )
        )
        claimed = HmrcTransactionData()
        for name in sorted(names):
            for claims in (
                self.history.disposal_list,
                self.history.transfer_to_spouse_list,
            ):
                if has_key(claims, date_index, name):
                    claimed = claimed + claims[date_index][name]
        return claimed

    def _day_identity(
        self, symbol: str, date_index: datetime.date, *, after_renames: bool = False
    ) -> DayIdentity:
        """Resolve which names are this holding on the day it is disposed of.

        A rename is neither a disposal nor an acquisition (TCGA 1992 s127,
        CG51700): the same shares carry on under another name, and the name
        changes at the end of the day the rename is recorded on. So the day's
        rows may state either name and mean the same holding, in whatever
        order they sit, and identification has to read them as one - otherwise
        the same facts give a different answer depending on which spelling a
        row happened to use.

        Refuses the days whose renames leave that identity unsettled, rather
        than identifying the disposal against the wrong holding or falling
        back to the Section 104 pool of one name as if the other were a
        different security.

        ``after_renames`` is for a transfer to a spouse, which a day records
        once its renames have been applied: the pool is already under the
        name the day leaves the holding under, so that is the name it is
        priced from. What the day opened holding still settles the rest, so
        the refusals that read it are asked here too: the day merges before
        it transfers whatever order the rows are in, and letting a merge
        through would decide what the transfer costs, and with it what the
        recipient is treated as having paid (CG22200). Only which rows have
        already moved shares out of which name goes unasked. That reads a
        portfolio the renames have reshuffled, and a transfer contending
        with another disposal row is settled where the day's transfers are
        recorded.
        """
        renames = self.history.rename_list.get(date_index, {})
        names = {symbol}
        names.update(other for other, _ in self._renames_reaching(symbol, renames))
        end_name = renames.get(symbol, symbol)
        identity_names = frozenset(names)
        if len(names) == 1:
            return DayIdentity(end_name, symbol, symbol, identity_names)
        for name in sorted(names):
            chained = self._rename_chain_at(name, renames)
            if chained is not None:
                raise CalculationError(
                    self._rename_chain_message(
                        "compute the disposal of", symbol, date_index, *chained
                    )
                )
        pooled = sorted(n for n in names if self._opened_with(n).quantity != 0)
        if len(pooled) > 1:
            raise CalculationError(
                f"Cannot compute the disposal of {symbol} on {date_index}: the "
                f"day's renames pool {' and '.join(pooled)} together, and each "
                "of them holds shares of its own. A rename says the tickers "
                "are one security, so the disposal is identified against one "
                "holding, but the pools only meet at the end of the day and "
                "the input does not say whether these shares were the same "
                "holding before that. Which pool the disposal comes out of, "
                "and at what cost, cannot be established. Work this day out by "
                "hand (consider professional advice)."
            )
        # The holding opens the day with its Section 104 pool under whichever
        # name held shares, and the refusal above leaves at most one.
        opening_pool = pooled[0] if pooled else symbol
        # Only the names the renames reach. Cost under the name that opened
        # the day holding the pool is cost those shares carry whichever side
        # of the rename the row sits, so there is nothing there to strand.
        cost_only = sorted(
            n
            for n in names
            if n != opening_pool and self._holds_cost_without_shares(n, date_index)
        )
        if cost_only:
            raise CalculationError(
                f"Cannot compute the disposal of {symbol} on {date_index}: the "
                f"day's renames make {' and '.join(sorted(names))} one "
                "holding, and pooled cost with no shares of its own is "
                f"recorded under {' and '.join(cost_only)} - a management fee, "
                "most likely. That cost belongs to the shares being disposed "
                "of here, but it only joins their pool when the day's renames "
                "are applied, so whether this disposal's Section 104 cost "
                "includes it turns on an order the input does not give. "
                "Record the fee under the name the shares are held under, or "
                "work this day out by hand (consider professional advice)."
            )
        if after_renames:
            # A transfer to a spouse is written once the day's renames have
            # moved the pool, so it prices itself from the name the day ends
            # with. Which of the day's rows has already moved shares out of
            # which name is a question about a portfolio that no longer
            # exists; a transfer contending with another disposal row is
            # settled where the day's transfers are recorded.
            return self._acquisition_identity(
                symbol, date_index, end_name, end_name, identity_names
            )
        if any(self._shares_already_moved(n, date_index) for n in names):
            raise CalculationError(
                f"Cannot compute the disposal of {symbol} on {date_index}: the "
                f"day's renames make {' and '.join(sorted(names))} one "
                "holding, and a disposal recorded under another of those "
                "names has already been identified against the day's "
                "purchase. The day's purchases are one acquisition (TCGA 1992 "
                "s105(1)(a)) and every disposal of the holding that day is "
                "identified against it, but this tool takes each disposal row "
                "against the purchase under a single name and cannot share "
                "one acquisition between rows written under different names "
                "of the holding. Record the day's sales under one name, or "
                "work this day out by hand (consider professional advice)."
            )
        return self._acquisition_identity(
            symbol, date_index, end_name, opening_pool, identity_names
        )

    def _acquisition_identity(
        self,
        symbol: str,
        date_index: datetime.date,
        end_name: str,
        pool_name: str,
        names: frozenset[str],
    ) -> DayIdentity:
        """Name the acquisition the day's purchases of this holding make up."""
        acquired = sorted(
            name
            for name in names
            if self._matchable_acquisition(date_index, name).quantity > 0
        )
        if len(acquired) > 1:
            raise CalculationError(
                f"Cannot compute the disposal of {symbol} on {date_index}: the "
                f"day's renames make {' and '.join(acquired)} one holding, and "
                "shares were bought under each of them today. The day's "
                "purchases are one acquisition at one blended cost (TCGA 1992 "
                "s105(1)(a)), and this tool cannot split that cost back across "
                "the names the shares were bought under. Record the day's "
                "purchases under one name, or work this day out by hand "
                "(consider professional advice)."
            )
        return DayIdentity(
            end_name, acquired[0] if acquired else pool_name, pool_name, names
        )

    def record_split_reconciliations(
        self,
        date_index: datetime.date,
        tax_year_start_index: datetime.date,
        calculation_log: CalculationLog,
    ) -> None:
        """Reconcile every reorganisation today to the broker's own count.

        Runs after the day's genuine acquisitions, so the correction cannot
        make a holding acquired earlier today look momentarily negative, and
        before its disposals, so a sale of the whole holding sees the count
        the broker stated.

        The delta is quantity only. It absorbs the broker's own rounding of
        the count it reported, and the difference left by restating the
        opening pool and the day's earlier rows separately rather than as one
        sum. It buys nothing and sells nothing, so it changes no cost and
        takes no part in same-day or 30-day identification.
        """
        for symbol, transformation in sorted(
            self.history.splits.get(date_index, {}).items()
        ):
            position = self.run.portfolio[symbol]
            quantity = position.quantity + transformation.reconciliation_delta
            if quantity < 0:
                raise CalculationError(
                    f"Cannot apply the reorganisation of {symbol} on "
                    f"{date_index}: reconciling to the broker's count leaves "
                    f"{strip_zeros(quantity)} units."
                )
            self.run.portfolio[symbol] = Position(quantity, position.amount)
            if date_index < tax_year_start_index:
                continue
            event = transformation.event
            calculation_log[date_index][f"split${symbol}"] = [
                CalculationEntry(
                    rule_type=RuleType.STOCK_SPLIT,
                    # A reorganisation acquires and disposes of nothing; the
                    # counts it moves between two unit systems are in the
                    # detail below.
                    quantity=Decimal(0),
                    amount=Decimal(0),
                    fees=Decimal(0),
                    gain=Decimal(0),
                    allowable_cost=Decimal(0),
                    new_quantity=quantity,
                    new_pool_cost=position.amount,
                    stock_split=StockSplitDetail(
                        ratio_description=transformation.ratio_description,
                        broker_before_quantity=event.before_quantity,
                        broker_after_quantity=event.after_quantity,
                        day_open_quantity=transformation.day_open_quantity,
                        scaled_day_open_quantity=(
                            transformation.scaled_day_open_quantity
                        ),
                        quantity_before_reconciliation=position.quantity,
                        quantity_after_reconciliation=quantity,
                        reconciliation_delta=transformation.reconciliation_delta,
                    ),
                )
            ]

    def verify_split_day_closes(self, date_index: datetime.date) -> None:
        """Check every reorganisation today left the holding where it said.

        A holding renamed today is checked under the name it was renamed to:
        the rename has moved the pool by now. That single hop is the whole
        journey, and the pool arrived alone, because a day that renames the
        holding onward again or pools it with a second one is refused before
        any of this (see ``apply_split_openings``).
        """
        renames = self.history.rename_list.get(date_index, {})
        for symbol, transformation in sorted(
            self.history.splits.get(date_index, {}).items()
        ):
            closing = self.run.portfolio[renames.get(symbol, symbol)].quantity
            if closing != transformation.expected_day_close:
                raise CalculationError(
                    f"Cannot apply the reorganisation of {symbol} on "
                    f"{date_index}: the day closes at {strip_zeros(closing)} "
                    "units where the reorganisation and the day's activity "
                    f"come to {strip_zeros(transformation.expected_day_close)}."
                    " Please report this: the two passes over the history "
                    "disagree."
                )

    @staticmethod
    def _rename_chain_message(
        action: str,
        symbol: str,
        date_index: datetime.date,
        first: tuple[str, str],
        second: tuple[str, str],
    ) -> str:
        """Explain why a holding renamed twice in a day cannot be worked out."""
        return (
            f"Cannot {action} {symbol} on {date_index}: it "
            f"is renamed to {first[1]}, and {second[0]} is renamed to "
            f"{second[1]}, the same day. The renames are applied in the order "
            "the input lists them, so where the holding ends up, and what it "
            "is pooled with on the way, depends on which of the two rows "
            "comes first. A date carries no order, so neither reading is "
            "established. Put the renames on the days they happened, or work "
            "this day out by hand (consider professional advice)."
        )

    @staticmethod
    def _rename_and_split_message(
        symbol: str,
        date_index: datetime.date,
        other: str,
        rename: tuple[str, str],
    ) -> str:
        """Explain why a rename that pools two holdings cannot be applied."""
        old_name, new_name = rename
        return (
            f"Cannot apply the reorganisation of {symbol} on {date_index}: "
            f"the day's renames pool it with {other}, which holds shares of "
            f"its own, by way of the rename of {old_name} to {new_name}. A "
            "rename says the two tickers are one security, so the "
            "reorganisation restates both, but it is applied under one name "
            "and the renames bring the other holding in afterwards, "
            "untouched. Whether those shares were part of the event depends on "
            "whether the renames took effect before it or after, and the input "
            "carries dates but not that order. Work this day out by hand "
            "(consider professional advice)."
        )

    @staticmethod
    def _unresolved_bnb_message(
        event: StockSplitEvent,
        symbol: str,
        date_index: datetime.date,
        search_index: datetime.date,
    ) -> str:
        """Explain why an unrecoverable ratio blocks a bed and breakfast match."""
        assert isinstance(event.ratio, UnresolvedRatio)
        return (
            f"Cannot compute the disposal of {symbol} on {date_index}: it is "
            f"identified under the 30-day rule against the acquisition on "
            f"{search_index}, and {event.describe()} lies between them, so the "
            "two counts are in different units. Converting one to the other "
            "needs the corporate ratio, and that cannot be recovered from the "
            f"export: {event.ratio.describe()}. Work this disposal out by hand "
            "(consider professional advice)."
        )

    def _spin_off_pool(self, date_index: datetime.date, source: str, dest: str) -> str:
        """Return the symbol whose pool a spin-off from `source` draws on today.

        A rename on the day is applied after the day's acquisitions, so a
        source renamed that morning still has its pool under the old name
        here.

        Refuses when the source was also traded on the day. Whether a trade
        came before or after the reorganisation decides which shares were
        reorganised and what they cost, and the input carries dates but not
        times, so there is no right answer to give. Shares the source itself
        received by spin-off that day are not a trade; dependency order has
        already put them in its pool.
        """
        renames = self.history.rename_list.get(date_index, {})
        # Follow the day's renames back from the source, a holding renamed
        # twice in a morning being two hops from its pool. Only renames on
        # this chain matter; what happened to other symbols that day does not.
        names = [source]
        frontier = [source]
        while frontier:
            current = frontier.pop()
            for old, new in renames.items():
                if new != current:
                    continue
                if old in names:
                    raise CalculationError(
                        f"Cannot compute the spin-off of {dest} from {source} on "
                        f"{date_index}: the day's renames go round in a circle "
                        f"({' -> '.join([*names, old])}), so there is no telling "
                        f"where the pool is. Work {source} and {dest} out by "
                        "hand (consider professional advice)."
                    )
                names.append(old)
                frontier.append(old)
        # The pool sits under whichever of these names holds anything here,
        # since the day's renames are applied after its acquisitions. Cost
        # with no shares counts: a fee charged after a holding was sold out
        # leaves exactly that, and the rename merges it in all the same. Two
        # of them means two holdings became one that day, and whether the
        # spin-off applied to both or to one cannot be told from the input.
        holding = sorted(
            name
            for name in names
            if name in self.run.portfolio
            and (
                self.run.portfolio[name].quantity > 0
                or self.run.portfolio[name].amount != 0
            )
        )
        if len(holding) > 1:
            raise CalculationError(
                f"Cannot compute the spin-off of {dest} from {source} on "
                f"{date_index}: {' and '.join(holding)} were separate holdings "
                "until renamed into one that day, and whether the spin-off "
                "applied to both or just one cannot be told from the input. "
                f"Work {source} and {dest} out by hand (consider professional "
                "advice)."
            )
        pool = holding[0] if holding else source
        activity = []
        for name in names:
            if (
                has_key(self.history.acquisition_list, date_index, name)
                and self.history.acquisition_list[date_index][name].purchased.quantity
                > 0
            ):
                activity.append("bought")
            if has_key(self.history.disposal_list, date_index, name):
                activity.append("sold")
            if has_key(self.history.transfer_to_spouse_list, date_index, name):
                activity.append("transferred to a spouse")
            if (date_index, name) in self.history.fee_days:
                activity.append("charged a fee")
        if activity:
            raise CalculationError(
                f"Cannot compute the spin-off of {dest} from {source} on "
                f"{date_index}: {source} was also {' and '.join(activity)} that "
                "day. Whether that came before or after the reorganisation "
                "decides which shares were reorganised and what they cost, and "
                "the input has dates but not times, so this tool cannot tell. "
                f"Work {source} and {dest} out by hand for this period (consider "
                "professional advice). Do not change the dates."
            )
        return pool

    def _acquisition_order(self, date_index: datetime.date) -> list[str]:
        """Return the day's acquired symbols, each spun-off holding after its source.

        A spin-off takes its share of the source's pool as it stands on the
        day, and the day's purchases of the source are part of that, since
        same-day acquisitions form a single acquisition (TCGA 1992 s105). So
        every other symbol is pooled first, and a spun-off holding only after
        whatever it was spun off from, so that a holding spun off and spun off
        from on the same day is complete before it is apportioned. Holdings
        spun off from the same source take their shares one after another, so
        among themselves they keep the order the spin-offs happened in, which
        is the order the first pass worked them out in.
        """
        sources: dict[str, list[str]] = defaultdict(list)
        first_event: dict[str, int] = {}
        for index, spin_off in enumerate(self.history.spin_offs.get(date_index, [])):
            sources[spin_off.dest].append(spin_off.source)
            first_event.setdefault(spin_off.dest, index)

        def depth(symbol: str) -> int:
            # A cycle cannot get here: the first pass refuses a chain that is
            # not in order, and a cycle is never in order.
            return 1 + max((depth(source) for source in sources[symbol]), default=-1)

        return sorted(
            self.history.acquisition_list[date_index],
            key=lambda symbol: (depth(symbol), first_event.get(symbol, -1)),
        )

    def _match_across_splits(
        self,
        disposal_quantity: Decimal,
        available_acquisition_units: Decimal,
        cumulative_ratio: Fraction,
        *,
        symbol: str,
        date_index: datetime.date,
        search_index: datetime.date,
    ) -> tuple[Decimal, Decimal]:
        """Match a disposal against an acquisition counted in other units.

        Returns what the disposal takes, in its own units, and what that
        consumes of the acquisition, in the acquisition's. Everything
        reserved is counted in the acquisition's own units, so the whole
        remainder converts once: converting only the acquisition would
        subtract post-split counts from a pre-split one and can go negative.

        Both counts are kept to ten decimal places, so a large enough ratio
        can leave a real quantity with nothing to say for itself on the other
        side. That is refused rather than matched at nil cost against an
        acquisition it then fails to reserve.
        """
        available_disposal_units = unscale_quantity(
            available_acquisition_units, cumulative_ratio
        )
        if available_disposal_units <= 0:
            raise CalculationError(
                self._unrepresentable_match_message(
                    symbol, date_index, search_index, cumulative_ratio
                )
            )
        matched = min(disposal_quantity, available_disposal_units)
        # Taking the whole remainder outright, rather than converting it back,
        # keeps a ten-decimal round trip from leaving or over-consuming a
        # residual share.
        if matched == available_disposal_units:
            consumed = available_acquisition_units
        else:
            consumed = min(
                available_acquisition_units,
                scale_quantity(matched, cumulative_ratio),
            )
        if consumed <= 0:
            raise CalculationError(
                self._unrepresentable_match_message(
                    symbol, date_index, search_index, cumulative_ratio
                )
            )
        assert consumed <= available_acquisition_units
        return matched, consumed

    @staticmethod
    def _unrepresentable_match_message(
        symbol: str,
        date_index: datetime.date,
        search_index: datetime.date,
        cumulative_ratio: Fraction,
    ) -> str:
        """Explain a match the two unit systems cannot both express."""
        return (
            f"Cannot compute the disposal of {symbol} on {date_index}: it is "
            f"identified under the 30-day rule against the acquisition on "
            f"{search_index}, and the reorganisations in between restate the "
            f"count by {cumulative_ratio}. One of the two quantities comes to "
            "less than the ten decimal places this keeps, so the match cannot "
            "be expressed in both unit systems at once. Work this disposal out "
            "by hand (consider professional advice)."
        )

    def _matchable_acquisition(
        self,
        date_index: datetime.date,
        symbol: str,
    ) -> HmrcTransactionData:
        """Return all of a day's additions to a holding, spin-off cost corrected.

        Units a reorganisation restated in place are not here to be taken out:
        they were never acquired (TCGA 1992 s127, CG51805), so they never
        enter ``acquisition_list`` in the first place. Shares a spin-off
        created do enter it, and still answer here along with everything else
        the day put in; which of them a disposal may be identified against is
        a separate question this does not yet ask. A spun-off holding's cost
        is the corrected figure once its day has been walked, in place of the
        estimate the first pass recorded.
        """
        if not has_key(self.history.acquisition_list, date_index, symbol):
            return HmrcTransactionData()
        acquisition = self.history.acquisition_list[date_index][symbol].total
        corrected = self.run.spin_off_corrected_amounts.get((date_index, symbol))
        if corrected is not None:
            return replace(acquisition, amount=corrected)
        return acquisition

    def _identifiable_acquisition(
        self,
        date_index: datetime.date,
        symbol: str,
    ) -> HmrcTransactionData:
        """Return the part of a day a disposal may be identified against.

        The same-day rule reads a day's acquisitions as one transaction
        (TCGA 1992 s105(1)(a)), and the 30-day rule reaches acquisitions made
        in the days after a disposal. Shares a reorganisation brings in are
        neither: it is "not to be treated as involving ... any acquisition of
        the new holding or any part of it" (TCGA 1992 s127), and CG51560 says
        of a disposal followed by such a reorganisation that "the additional
        shares are not treated as acquired within the 30 days following the
        disposal". So they are left out here, and reach a later disposal
        through the Section 104 pool instead, which is where s127 puts them.

        A management fee is not an acquisition either, and is still counted
        here. Leaving it out would change what an unrelated purchase costs on
        a day it was charged, which is its own fix (see issue 1121).
        """
        if not has_key(self.history.acquisition_list, date_index, symbol):
            return HmrcTransactionData()
        record = self.history.acquisition_list[date_index][symbol]
        # No spin-off correction is wanted: what a purchase cost is what its
        # own row said, settled when it was read.
        return record.purchased + record.cost_only

    def _contending_acquisitions(
        self,
        identity: DayIdentity,
        date_index: datetime.date,
        bnb_claimed_before_today: dict[tuple[datetime.date, str], Decimal],
    ) -> list[datetime.date]:
        """Dates a same-day sale and transfer of one holding would both claim.

        Left to the Section 104 pool alone the two draw at the same average
        cost, so the order between them cannot change either result. They only
        contend over acquisitions both could be identified against under the
        same-day or 30-day rules, and then HMRC does not say which one gets
        them. Returns those acquisition dates, earliest first, or an empty list
        when there is nothing to argue over.
        """
        dates = []
        effective_symbol = identity.end_name

        def has_shares_going_spare(
            day: datetime.date, ticker: str, *, is_disposal_day: bool
        ) -> bool:
            # Only what is left over is worth arguing about, and only what
            # either of them could be identified against at all: shares a
            # reorganisation brought in are not there for either to claim, and
            # a management fee brings in no shares. Shares that cost nothing are
            # still worth arguing over: whichever disposal is identified
            # against them takes a nil allowable cost, and the other one takes
            # a share of the pool instead.
            acquisition = self._identifiable_acquisition(day, ticker)
            if acquisition.quantity <= 0:
                return False
            # Claims an earlier disposal already made are settled and leave
            # that much less to argue over. What the sale we are contending
            # with took today is deliberately not deducted: those are the very
            # shares in dispute, and it only got them by going first.
            taken = bnb_claimed_before_today.get((day, ticker), Decimal(0))
            if not is_disposal_day:
                # Whatever that later day disposes of under the same-day rule
                # is spoken for before either of ours can reach it, under
                # whichever of the holding's names that day's rows spell it -
                # the same claim the 30-day walk reserves. On the disposal day
                # itself the sale and the transfer are the two laying claim, so
                # subtracting them would hide the clash.
                taken += self._same_day_claims(day, ticker).quantity
            return acquisition.quantity - taken > 0

        if has_shares_going_spare(
            date_index, identity.acquired_under, is_disposal_day=True
        ):
            dates.append(date_index)
        for i in range(BED_AND_BREAKFAST_DAYS):
            search_index = date_index + datetime.timedelta(days=i + 1)
            # HMRC treats renames as the same security for B&B purposes.
            effective_symbol = self.history.rename_list.get(search_index, {}).get(
                effective_symbol, effective_symbol
            )
            if has_shares_going_spare(
                search_index, effective_symbol, is_disposal_day=False
            ):
                dates.append(search_index)
        return dates

    @staticmethod
    def _show_dates(dates: list[datetime.date]) -> str:
        """Name a few of the dates, and say how many more there are."""
        shown = ", ".join(str(date) for date in dates[:MAX_CONTENDED_DATES_SHOWN])
        if len(dates) > MAX_CONTENDED_DATES_SHOWN:
            shown += f" and {len(dates) - MAX_CONTENDED_DATES_SHOWN} more"
        return shown

    @staticmethod
    def _day_rows(
        log: dict[datetime.date, dict[str, HmrcTransactionData]],
        date_index: datetime.date,
        identity: DayIdentity,
        *,
        skip: str | None = None,
    ) -> dict[str, Decimal]:
        """Return what each of the holding's names records in a day's log."""
        return {
            name: log[date_index][name].quantity
            for name in sorted(identity.names)
            if name != skip and has_key(log, date_index, name)
        }

    def _transfers_under_two_names_message(
        self,
        identity: DayIdentity,
        symbol: str,
        date_index: datetime.date,
        contended: list[datetime.date],
    ) -> str:
        """Explain why two transfer rows cannot share the day's purchase."""
        elsewhere = self._day_rows(
            self.history.transfer_to_spouse_list, date_index, identity, skip=symbol
        )
        others = ", ".join(
            f"{strip_zeros(quantity)} of {name}" for name, quantity in elsewhere.items()
        )
        transferred = self.history.transfer_to_spouse_list[date_index][symbol].quantity
        shown = self._show_dates(contended)
        return (
            f"On {date_index} you transferred {strip_zeros(transferred)} units "
            f"of {symbol} to a spouse and {others}, which the day's renames "
            f"make one holding, and you also acquired "
            f"{identity.acquired_under} on {shown}.\n"
            "Every transfer of the holding that day has to be identified "
            "against those acquisitions under the same-day and 30-day rules "
            "(TCGA 1992 s105(1)), and only one of the rows can have them. "
            "This tool identifies each transfer row against the acquisitions "
            "under a single name, so it cannot share them between rows "
            "written under different names of the one holding.\n"
            "What to do: record the day's transfers under one name, which is "
            "what they are. Failing that, work this day out by hand and "
            "consider taking professional advice, then leave the affected "
            "symbol out of the input and add its figures to your return "
            "yourself."
        )

    def _same_day_sale_and_transfer_message(
        self,
        identity: DayIdentity,
        symbol: str,
        date_index: datetime.date,
        contended: list[datetime.date],
    ) -> str:
        """Explain why a same-day sale and transfer cannot be calculated."""
        disposed = self._day_rows(self.history.disposal_list, date_index, identity)
        sold_under = list(disposed)
        sold = sum(disposed.values(), Decimal(0))
        transferred = self.history.transfer_to_spouse_list[date_index][symbol].quantity
        shown = self._show_dates(contended)
        has_gift = any(
            (date_index, name) in self.history.gift_disposals for name in sold_under
        )
        has_sale = any(
            (date_index, name) in self.history.sale_days for name in sold_under
        )
        if has_gift and has_sale:
            verb, noun = ("sold or gave away", "disposal")
        elif has_gift:
            verb, noun = ("gave away", "gift")
        else:
            verb, noun = ("sold", "sale")
        # The day's renames can put the two rows under different names of the
        # one holding, and then neither ticker alone tells the whole story.
        aliases = (
            ""
            if sold_under == [symbol]
            else (
                f" The {noun} is recorded under {' and '.join(sold_under)}, which "
                f"the day's renames make one holding with {symbol}."
            )
        )
        return (
            f"On {date_index} you {verb} {strip_zeros(sold)} units of {symbol} "
            f"and transferred {strip_zeros(transferred)} to a spouse, and you "
            f"also acquired {identity.acquired_under} on {shown}.{aliases}\n"
            f"Both the {noun} and the transfer have to be identified against those "
            "acquisitions under the same-day and 30-day rules, but they cannot "
            "share them: TCGA 1992 s105(1) treats everything disposed of on one "
            "day as a single transaction, and here one part is chargeable while "
            "the other is no gain/no loss. HMRC does not say how to split the "
            "acquisitions between the two, so this tool will not guess.\n"
            "What to do: work this disposal out by hand and consider taking "
            "professional advice, then leave the affected symbol out of the "
            "input and add its figures to your return yourself. Everything else "
            "in your history still calculates normally.\n"
            "Do not move the real transaction dates to get past this: the dates "
            "are what the identification rules run on, so changing them changes "
            "the tax."
        )

    def _record_transfers_to_spouse(
        self,
        date_index: datetime.date,
        tax_year_start_index: datetime.date,
        calculation_log: CalculationLog,
        bnb_claimed_before_today: dict[tuple[datetime.date, str], Decimal],
    ) -> None:
        """Process and log no gain/no loss transfers to spouse for a single day.

        Each transfer is identified against the transferor's own acquisitions
        exactly like a disposal (see ``process_disposal``), but with a nil gain,
        so it is kept out of the taxable disposal totals.
        """
        if date_index not in self.history.transfer_to_spouse_list:
            return
        for symbol in self.history.transfer_to_spouse_list[date_index]:
            identity = self._day_identity(symbol, date_index, after_renames=True)
            # HMRC does not define how to identify a taxable sale and a no gain/no
            # loss transfer of the same shares on the same day, so refuse the
            # cases where that identification actually changes the answer. A
            # rename today makes the day's names one holding, so the sale need
            # not state the same ticker as the transfer to be a sale of it.
            sold = self._day_rows(self.history.disposal_list, date_index, identity)
            # Transfers of the one holding written under its other names: no
            # gain either way, so nothing about the tax turns on which row
            # gets the day's purchase, but only one of them can have it.
            transferred_elsewhere = self._day_rows(
                self.history.transfer_to_spouse_list, date_index, identity, skip=symbol
            )
            if (sold or transferred_elsewhere) and (
                contended := self._contending_acquisitions(
                    identity, date_index, bnb_claimed_before_today
                )
            ):
                raise CalculationError(
                    self._same_day_sale_and_transfer_message(
                        identity, symbol, date_index, contended
                    )
                    if sold
                    else self._transfers_under_two_names_message(
                        identity, symbol, date_index, contended
                    )
                )
            gain, entries = self.process_disposal(
                symbol, date_index, no_gain_no_loss=True
            )
            assert gain == 0, gain
            base_cost = sum((entry.allowable_cost for entry in entries), Decimal(0))
            # Surface transfers made in the reported period; the rest of the
            # history walk logs them at DEBUG.
            LOGGER.log(
                logging.INFO if self.date_in_tax_year(date_index) else logging.DEBUG,
                "Transferred %s units of %s to spouse on %s "
                "(no gain/no loss, base cost £%s)",
                self.history.transfer_to_spouse_list[date_index][symbol].quantity,
                symbol,
                date_index,
                round_decimal(base_cost, 2),
            )
            if date_index >= tax_year_start_index:
                calculation_log[date_index][f"transfer-to-spouse${symbol}"] = entries

    def process_eri(
        self,
        symbol: str,
        date_index: datetime.date,
    ) -> CalculationEntry | None:
        """Process single excess reported income."""
        eri = self.get_eri(symbol, date_index)
        assert eri is not None
        amount = self.run.portfolio[eri.symbol].amount
        quantity = self.run.portfolio[eri.symbol].quantity

        if quantity == 0:
            return None

        allowable_cost = quantity * eri.price

        if allowable_cost == 0:
            return None

        new_amount = amount + allowable_cost
        LOGGER.debug(
            "Detected excess reported income of %s on %s, "
            "modyfing the cost amount from %s to %s",
            eri.symbol,
            eri.date,
            amount,
            new_amount,
        )
        self.run.portfolio[eri.symbol].amount = new_amount

        if self.date_in_tax_year(eri.distribution_date):
            self.run.eris_distribution[eri.distribution_date][symbol] += (
                ExcessReportedIncomeDistribution(
                    price=eri.price,
                    amount=allowable_cost,
                    quantity=quantity,
                )
            )

        return CalculationEntry(
            RuleType.EXCESS_REPORTED_INCOME,
            quantity=quantity,
            amount=-amount,
            new_quantity=quantity,
            gain=None,
            fees=Decimal(0),
            new_pool_cost=new_amount,
            allowable_cost=allowable_cost,
            eris=[eri],
        )

    def _record_option_disposals(
        self,
        date_index: datetime.date,
        calculation_log: CalculationLog,
    ) -> tuple[int, Decimal, Decimal, Decimal, Decimal]:
        """Add one day's written-option grants to the report totals and log."""
        count = 0
        proceeds = Decimal(0)
        costs = Decimal(0)
        gains = Decimal(0)
        losses = Decimal(0)
        for option_name, option in self.history.option_disposal_list.get(
            date_index, {}
        ).items():
            raw_gain = option.proceeds - option.allowable_cost
            gain = round_decimal(raw_gain, 2)
            count += 1
            proceeds += option.proceeds
            costs += option.proceeds - gain
            calculation_log[date_index][f"option${option_name}"] = [
                CalculationEntry(
                    rule_type=RuleType.OPTION,
                    quantity=option.quantity,
                    amount=option.proceeds,
                    fees=Decimal(0),
                    gain=raw_gain,
                    allowable_cost=option.allowable_cost,
                    new_quantity=Decimal(0),
                    new_pool_cost=Decimal(0),
                )
            ]
            if gain > 0:
                gains += gain
            else:
                losses += gain
        return count, proceeds, costs, gains, losses

    def walk(self) -> WalkResult:
        """Replay every day from the start of history to the tax year end."""
        begin_index = INTERNAL_START_DATE
        tax_year_start_index = self.tax_year_start_date
        end_index = self.tax_year_end_date
        disposal_count = 0
        disposal_proceeds = Decimal(0)
        allowable_costs = Decimal(0)
        capital_gain = Decimal(0)
        capital_loss = Decimal(0)
        gift_loss = Decimal(0)
        exempt_disposal_count = 0
        exempt_disposal_proceeds = Decimal(0)

        # The first pass left its estimates in the portfolio; the walk rebuilds
        # it from nothing.
        self.run.portfolio.clear()
        self.run.spin_off_entries.clear()
        calculation_log: CalculationLog = defaultdict(dict)

        for date_index in (
            begin_index + datetime.timedelta(days=x)
            for x in range((end_index - begin_index).days + 1)
        ):
            # What earlier disposals have already bed-and-breakfasted, captured
            # before today's own disposals add to it. Only those earlier claims
            # settle whether a later acquisition is still up for grabs.
            bnb_claimed_before_today = (
                {
                    (day, ticker): data.quantity
                    for day, tickers in self.run.bnb_list.items()
                    for ticker, data in tickers.items()
                }
                if date_index in self.history.transfer_to_spouse_list
                else {}
            )
            # A rename records that two names are one holding, and what each
            # of them brought into the day is what tells a holding of its own
            # from what the day's own rows put there (see _day_identity).
            self._day_open_pools = (
                {
                    name: position
                    for name, position in self.run.portfolio.items()
                    if position.quantity != 0 or position.amount != 0
                }
                if date_index in self.history.rename_list
                else {}
            )
            self.apply_split_openings(date_index)
            if date_index in self.history.acquisition_list:
                for symbol in self._acquisition_order(date_index):
                    calculation_entries = self.process_acquisition(
                        symbol,
                        date_index,
                    )
                    if date_index >= tax_year_start_index:
                        calculation_log[date_index][f"buy${symbol}"] = (
                            calculation_entries
                        )
            for source, entries in self.run.spin_off_entries.pop(
                date_index, {}
            ).items():
                if date_index >= tax_year_start_index:
                    calculation_log[date_index][f"spin-off${source}"] = entries
            self.record_split_reconciliations(
                date_index, tax_year_start_index, calculation_log
            )
            if date_index in self.history.disposal_list:
                for symbol in self.history.disposal_list[date_index]:
                    transaction_capital_gain, calculation_entries = (
                        self.process_disposal(symbol, date_index)
                    )
                    if date_index >= tax_year_start_index:
                        transaction_amount = self.history.disposal_list[date_index][
                            symbol
                        ].amount
                        transaction_fees = self.history.disposal_list[date_index][
                            symbol
                        ].fees
                        transaction_disposal_proceeds = (
                            transaction_amount + transaction_fees
                        )
                        transaction_quantity = self.history.disposal_list[date_index][
                            symbol
                        ].quantity
                        calculated_quantity = Decimal(0)
                        calculated_proceeds = Decimal(0)
                        calculated_gain = Decimal(0)
                        for entry in calculation_entries:
                            calculated_quantity += entry.quantity
                            calculated_proceeds += entry.amount + entry.fees
                            calculated_gain += entry.gain
                        assert transaction_quantity == calculated_quantity
                        assert round_decimal(
                            transaction_disposal_proceeds, 10
                        ) == round_decimal(calculated_proceeds, 10), (
                            f"{transaction_disposal_proceeds} != {calculated_proceeds}"
                        )
                        assert transaction_capital_gain == round_decimal(
                            calculated_gain, 2
                        )
                        prefix = self._disposal_log_prefix(date_index, symbol)
                        calculation_log[date_index][f"{prefix}${symbol}"] = (
                            calculation_entries
                        )
                        if prefix == "exempt":
                            exempt_disposal_count += 1
                            exempt_disposal_proceeds += transaction_disposal_proceeds
                            LOGGER.debug(
                                "EXEMPT DISPOSAL on %s of %s, quantity %s, proceeds £%s",
                                date_index,
                                symbol,
                                transaction_quantity,
                                round_decimal(transaction_disposal_proceeds, 2),
                            )
                        else:
                            disposal_count += 1
                            disposal_proceeds += transaction_disposal_proceeds
                            allowable_costs += (
                                transaction_disposal_proceeds - transaction_capital_gain
                            )
                            LOGGER.debug(
                                "DISPOSAL on %s of %s, quantity %s, capital gain $%s",
                                date_index,
                                symbol,
                                transaction_quantity,
                                round_decimal(transaction_capital_gain, 2),
                            )
                            if transaction_capital_gain > 0:
                                capital_gain += transaction_capital_gain
                            elif prefix == "gift":
                                # A clogged loss: usable only against gains on
                                # disposals to the same connected person while
                                # still connected (TCGA 1992 s18(3)). Kept out of
                                # the loss total and reported as its own.
                                gift_loss += transaction_capital_gain
                            else:
                                capital_loss += transaction_capital_gain

            if date_index >= tax_year_start_index:
                (
                    option_count,
                    option_proceeds,
                    option_costs,
                    option_gains,
                    option_losses,
                ) = self._record_option_disposals(date_index, calculation_log)
                disposal_count += option_count
                disposal_proceeds += option_proceeds
                allowable_costs += option_costs
                capital_gain += option_gains
                capital_loss += option_losses

            if date_index in self.history.rename_list:
                for old, new in self.history.rename_list[date_index].items():
                    entry = self.process_rename(old, new)
                    if date_index >= tax_year_start_index:
                        calculation_log[date_index][f"rename${old}"] = [entry]

            self._record_transfers_to_spouse(
                date_index,
                tax_year_start_index,
                calculation_log,
                bnb_claimed_before_today,
            )

            self.verify_split_day_closes(date_index)

            # Excess Reported incomes should be reported at the end of the day
            if date_index in self.history.eris:
                for symbol in self.history.eris[date_index]:
                    maybe_entry = self.process_eri(symbol, date_index)
                    if not maybe_entry:
                        continue

                    if date_index >= tax_year_start_index:
                        eris = maybe_entry.eris
                        assert eris
                        calculation_log[date_index][
                            f"excess-reported-income${symbol}"
                        ] = [maybe_entry]

            # Lastly all the ERI distribution events
            if date_index in self.run.eris_distribution:
                for symbol in self.run.eris_distribution[date_index]:
                    data = self.run.eris_distribution[date_index][symbol]
                    is_interest = symbol in self.interest_fund_tickers
                    if is_interest:
                        self.run.total_foreign_interest += data.amount
                    self.run.calculation_log_yields[date_index][
                        f"excess-reported-income-distribution${symbol}"
                    ] = [
                        CalculationEntry(
                            RuleType.EXCESS_REPORTED_INCOME_DISTRIBUTION,
                            quantity=data.quantity,
                            amount=data.amount,
                            new_quantity=data.quantity,
                            gain=None,
                            fees=Decimal(0),
                            new_pool_cost=data.amount,
                            allowable_cost=None,
                            eris=[
                                ExcessReportedIncome(
                                    price=data.price,
                                    symbol=symbol,
                                    date=date_index - ERI_TAX_DATE_DELTA,
                                    distribution_date=date_index,
                                    is_interest=is_interest,
                                ),
                            ],
                        )
                    ]
        return WalkResult(
            disposal_count,
            disposal_proceeds,
            allowable_costs,
            capital_gain,
            capital_loss,
            gift_loss,
            exempt_disposal_count,
            exempt_disposal_proceeds,
            calculation_log,
        )
