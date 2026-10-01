"""Standard equity-option support for Charles Schwab exports.

Schwab exports an option lifecycle as separate rows: the grant, then whatever
ended it, and for an assignment a stock row of its own posted on settlement.
UK tax treats the grant as the disposal (TCGA 1992 s144), so the rows have to
be reconciled into what actually happened before the calculator sees them.
That is all this module does; `schwab.py` reads the CSV and calls
`reconcile_written_options` once per declared account.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import datetime
from decimal import Decimal
import re
from typing import TYPE_CHECKING, Final

from cgt_calc.exceptions import ParsingError
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CapitalAdjustment,
    DatedAmount,
    OptionContract,
    OptionType,
    WrittenOptionTaxData,
)
from cgt_calc.ticker_renames import current_ticker
from cgt_calc.util import approx_equal, strip_zeros

if TYPE_CHECKING:
    from collections.abc import Container
    from pathlib import Path

    from cgt_calc.model import TransactionSource

    # schwab.py imports this module at runtime, so the edge back is type-only.
    from cgt_calc.parsers.schwab import SchwabTransaction


OPTION_ASSIGNMENT_SHARES: Final = Decimal(100)
# How far after an assignment its stock row may be posted. Settlement runs in
# business days, so the gap in calendar days depends on where the weekend and
# any holidays fall, and the standard period was T+2 until 28 May 2024, which
# older exports still contain. A Friday assignment settling T+2 over a
# two-holiday long weekend lands six days later, so the window is wider than
# any of those and the candidate has to reconcile exactly to be used at all.
OPTION_SETTLEMENT_MATCH_DAYS: Final = 7
# Roots whose options settle in cash rather than delivering shares. A floor,
# not a claim to recognise every index: it refuses the ones a symbol alone can
# identify, and anything else that does not deliver 100 shares fails later,
# where the assignment cannot be reconciled with a stock row.
CASH_SETTLED_ROOTS: Final = frozenset(
    {
        "DJX",
        "MRUT",
        "NDX",
        "NDXP",
        "NQX",
        "OEX",
        "RUT",
        "RUTW",
        "SPX",
        "SPXW",
        "VIX",
        "VIXW",
        "XEO",
        "XND",
        "XSP",
    }
)

_HUMAN_OPTION_SYMBOL: Final = re.compile(
    r"^\s*(?P<underlying>[A-Z][A-Z.\-]{0,9})\s+"
    r"(?P<expiry>\d{2}/\d{2}/\d{4})\s+"
    r"\$?(?P<strike>\d+(?:\.\d+)?)\s+"
    r"(?P<option_type>[CP])\s*$",
    re.IGNORECASE,
)
_OCC_OPTION_SYMBOL: Final = re.compile(
    r"^\s*(?P<underlying>[A-Z][A-Z.\-]{0,9})\s+"
    r"(?P<expiry>\d{6})(?P<option_type>[CP])"
    r"(?P<strike>\d{8})\s*$",
    re.IGNORECASE,
)


def parse_option_contract(symbol: str, date: datetime.date) -> OptionContract | None:
    """Parse Schwab's human-readable or OCC equity-option symbol.

    `date` is the row's, which says what the underlying's ticker names.
    """
    human_match = _HUMAN_OPTION_SYMBOL.fullmatch(symbol)
    if human_match:
        expiry = datetime.datetime.strptime(
            human_match.group("expiry"), "%m/%d/%Y"
        ).date()
        strike = Decimal(human_match.group("strike"))
        option_type = (
            OptionType.CALL
            if human_match.group("option_type").upper() == "C"
            else OptionType.PUT
        )
        underlying = human_match.group("underlying").upper()
    else:
        occ_match = _OCC_OPTION_SYMBOL.fullmatch(symbol)
        if not occ_match:
            return None
        expiry = datetime.datetime.strptime(occ_match.group("expiry"), "%y%m%d").date()
        strike = Decimal(occ_match.group("strike")) / Decimal(1000)
        option_type = (
            OptionType.CALL
            if occ_match.group("option_type").upper() == "C"
            else OptionType.PUT
        )
        underlying = occ_match.group("underlying").upper()

    if underlying in CASH_SETTLED_ROOTS:
        return None

    return OptionContract(
        underlying=current_ticker(underlying, date),
        expiry=expiry,
        strike=strike,
        option_type=option_type,
        multiplier=OPTION_ASSIGNMENT_SHARES,
    )


type SchwabRowKey = tuple[Path, int]


def _row_source(transaction: SchwabTransaction) -> TransactionSource:
    """Return where one row was read from, which reconciliation needs."""
    source = transaction.source
    if source is None or source.file is None or source.row is None:
        raise TypeError("Schwab row has no stamped source provenance")
    return source


def _row_file(transaction: SchwabTransaction) -> Path:
    """Return the file one row was read from, for pointing an error at it."""
    file = _row_source(transaction).file
    assert file is not None
    return file


def _row_key(transaction: SchwabTransaction) -> SchwabRowKey:
    """Identify one exported row by the file and line it was read from.

    Object identity would do while every row happens to be alive in one
    list, but it says nothing about which row of which export this is, and
    breaks silently the first time a pass rebuilds a row rather than keeping
    it. Stamped provenance is what actually identifies a row. Reconciliation
    runs once per declared account, so the file and line are unique within
    everything it sees.
    """
    source = _row_source(transaction)
    assert source.file is not None
    assert source.row is not None
    return source.file, source.row


@dataclass
class _WrittenOptionLot:
    """Unsettled quantity from one Sell to Open row."""

    transaction: SchwabTransaction
    remaining: Decimal
    assigned: Decimal = Decimal(0)


@dataclass
class _OptionAssignment:
    """An assignment row and the grants whose contracts it consumed."""

    transaction: SchwabTransaction
    allocations: list[tuple[_WrittenOptionLot, Decimal]]


def _option_quantity(transaction: SchwabTransaction, file: Path) -> Decimal:
    """Return and validate a Schwab option contract count."""
    quantity = transaction.quantity
    if (
        quantity is None
        or not quantity.is_finite()
        or quantity <= 0
        or quantity != quantity.to_integral_value()
    ):
        raise ParsingError(
            file,
            f"Invalid contract quantity for {transaction.raw_action} of "
            f"{transaction.symbol} on {transaction.date}: {quantity}",
        )
    return quantity


def _validate_written_option_row(
    transaction: SchwabTransaction,
    file: Path,
) -> None:
    """Validate the cash fields used to reconcile a written option."""
    quantity = _option_quantity(transaction, file)
    amount = transaction.amount
    price = transaction.price
    fees = transaction.fees
    if not fees.is_finite() or fees < 0:
        raise ParsingError(
            file,
            f"Invalid fees for {transaction.raw_action} of {transaction.symbol} "
            f"on {transaction.date}: {fees}",
        )

    if transaction.action is ActionType.OPTION_GRANT:
        valid = (
            amount is not None
            and amount.is_finite()
            and amount >= 0
            and price is not None
            and price.is_finite()
            and price >= 0
        )
        sign_description = "non-negative premium proceeds"
    elif transaction.action is ActionType.OPTION_CLOSE:
        valid = (
            amount is not None
            and amount.is_finite()
            and amount <= 0
            and price is not None
            and price.is_finite()
            and price >= 0
        )
        sign_description = "non-positive closing cost"
    else:
        valid = (amount is None or amount == 0) and price is None and fees == 0
        sign_description = "no price, fees or cash amount"

    if not valid:
        raise ParsingError(
            file,
            f"Invalid {transaction.raw_action} row for {transaction.symbol} on "
            f"{transaction.date}: expected {sign_description} and a positive "
            f"whole contract quantity, got quantity {quantity}, price {price}, "
            f"fees {fees}, amount {amount}",
        )

    if transaction.action in {ActionType.OPTION_GRANT, ActionType.OPTION_CLOSE}:
        assert amount is not None
        assert price is not None
        contract = transaction.option_contract
        assert contract is not None
        gross_premium = quantity * contract.multiplier * price
        expected_amount = (
            gross_premium - fees
            if transaction.action is ActionType.OPTION_GRANT
            else -(gross_premium + fees)
        )
        implied_price = (
            (amount + fees) / (quantity * contract.multiplier)
            if transaction.action is ActionType.OPTION_GRANT
            else (-amount - fees) / (quantity * contract.multiplier)
        )
        if not approx_equal(amount, expected_amount) and abs(
            implied_price - price
        ) >= Decimal("0.0001"):
            raise ParsingError(
                file,
                f"Option premium does not match quantity, price and fees for "
                f"{transaction.raw_action} of {transaction.symbol} on "
                f"{transaction.date}: expected {expected_amount}, got {amount}",
            )


def _allocate_option_outcome(
    outcome: SchwabTransaction,
    open_lots: dict[OptionContract, list[_WrittenOptionLot]],
    file: Path,
) -> list[tuple[_WrittenOptionLot, Decimal]]:
    """Allocate a close, expiry or assignment to earlier grants FIFO."""
    contract = outcome.option_contract
    assert contract is not None
    needed = _option_quantity(outcome, file)
    allocations: list[tuple[_WrittenOptionLot, Decimal]] = []
    for lot in open_lots.get(contract, []):
        if lot.transaction.date > outcome.date or lot.remaining == 0:
            continue
        quantity = min(needed, lot.remaining)
        allocations.append((lot, quantity))
        lot.remaining -= quantity
        needed -= quantity
        if needed == 0:
            break

    if needed:
        raise ParsingError(
            file,
            f"Schwab reports {outcome.raw_action} of {outcome.quantity} contract(s) "
            f"for {contract} on {outcome.date}, but only "
            f"{_option_quantity(outcome, file) - needed} earlier written "
            "contract(s) are available. Include the missing Sell to Open rows "
            "in the export.",
        )
    return allocations


def _assignment_adjustments(
    allocations: list[tuple[_WrittenOptionLot, Decimal]],
) -> list[CapitalAdjustment]:
    """Return grant premiums to fold into an assigned share transaction."""
    adjustments = []
    for lot, quantity in allocations:
        opening = lot.transaction
        opening_quantity = opening.quantity
        assert opening_quantity is not None
        amount = opening.amount
        assert amount is not None
        proportion = quantity / opening_quantity
        adjustments.append(
            CapitalAdjustment(
                date=opening.date,
                net_amount=amount * proportion,
                fees=opening.fees * proportion,
                currency=opening.currency,
            )
        )
    return adjustments


def _settles_assignment(
    candidate: SchwabTransaction,
    contract: OptionContract,
    assigned_shares: Decimal,
) -> bool:
    """Whether a candidate row's cash matches delivery at the strike."""
    amount = candidate.amount
    if candidate.price != contract.strike or amount is None:
        return False
    gross_amount = assigned_shares * contract.strike
    expected = (
        gross_amount - candidate.fees
        if candidate.action is ActionType.SELL
        else -(gross_amount + candidate.fees)
    )
    return approx_equal(amount, expected)


def _find_assignment_share_transaction(
    transactions: list[SchwabTransaction],
    assignment: SchwabTransaction,
    assigned_shares: Decimal,
    used: Container[SchwabRowKey],
    file: Path,
) -> SchwabTransaction:
    """Find Schwab's separate stock row created by an option assignment."""
    contract = assignment.option_contract
    assert contract is not None
    expected_action = (
        ActionType.SELL if contract.option_type is OptionType.CALL else ActionType.BUY
    )
    candidates = [
        transaction
        for transaction in transactions
        if _row_key(transaction) not in used
        and transaction.option_contract is None
        and transaction.action is expected_action
        and transaction.symbol == contract.underlying
        and transaction.quantity == assigned_shares
        and assignment.date
        <= transaction.date
        <= assignment.date + datetime.timedelta(days=OPTION_SETTLEMENT_MATCH_DAYS)
    ]
    settling = [
        candidate
        for candidate in candidates
        if _settles_assignment(candidate, contract, assigned_shares)
    ]
    if len(settling) > 1:
        raise ParsingError(
            file,
            f"Cannot identify which {contract.underlying} stock transaction "
            f"belongs to the assignment of {contract} on {assignment.date}",
        )
    if not settling and not candidates:
        # Synthesizing the shares here would double the disposal as soon as
        # the settlement row turned out to be present after all, posted
        # outside the window or under a symbol this did not expect. It is
        # also how a cash-settled contract that slipped past the root check
        # would acquire 100 shares of an index that delivers none.
        raise ParsingError(
            file,
            f"The assignment of {contract} on {assignment.date} has no "
            f"{contract.underlying} transaction that settles it: expected a "
            f"{expected_action.name.lower()} of {strip_zeros(assigned_shares)} "
            f"shares at {strip_zeros(contract.strike)}, dated on or within "
            f"{OPTION_SETTLEMENT_MATCH_DAYS} days of the assignment. Export "
            "the history through settlement, which can be days later than the "
            "assignment. If the contract settles in cash rather than in "
            "shares, it is not supported: remove its rows and record the "
            "result by hand.",
        )
    if not settling:
        # A row this close to the assignment is far more likely to be its
        # settlement than an unrelated trade, so a mismatch is a reason to
        # stop rather than to look past it.
        raise ParsingError(
            file,
            f"A {contract.underlying} stock transaction on {candidates[0].date} "
            f"looks like the settlement of the assignment of {contract} on "
            f"{assignment.date}, but its price and amount do not reconcile "
            f"with delivery of {assigned_shares} shares at "
            f"{strip_zeros(contract.strike)}. Correct the row, or remove the "
            "option rows and record the trade by hand.",
        )
    return settling[0]


def _apply_option_assignment(
    transactions: list[SchwabTransaction],
    assignment: _OptionAssignment,
    settled_share_rows: dict[SchwabRowKey, BrokerTransaction],
    file: Path,
) -> BrokerTransaction:
    """Return the assigned shares, dated on the assignment itself.

    Schwab posts the underlying trade on its settlement date, which can be
    several days later and can fall in the next tax year. The disposal or
    acquisition happens when the option is assigned, so the exported row
    supplies the money and this supplies the date.

    The money itself keeps that later date: the account is funded when it
    settles, not when the option is assigned, and moving the cash early
    rejects a funded account. `settled_share_rows` collects the cash-only
    row each exported settlement becomes, keyed by the row it replaces.
    """
    transaction = assignment.transaction
    contract = transaction.option_contract
    assert contract is not None
    contracts = sum((quantity for _, quantity in assignment.allocations), Decimal(0))
    assigned_shares = contracts * contract.multiplier
    share_transaction = _find_assignment_share_transaction(
        transactions,
        transaction,
        assigned_shares,
        settled_share_rows,
        file,
    )
    amount = share_transaction.amount
    assert amount is not None
    price = share_transaction.price
    assert price is not None
    settled_share_rows[_row_key(share_transaction)] = BrokerTransaction(
        date=share_transaction.date,
        action=ActionType.TRANSFER,
        symbol=None,
        # This row is synthetic, and the negative-balance error prints it
        # verbatim to someone who never wrote it, so it has to say what it is.
        description=f"Settlement of the {contract} assigned on {transaction.date}",
        quantity=None,
        price=None,
        # The fee is already deducted from the settlement amount, and the tax
        # row is what claims it as a cost.
        fees=Decimal(0),
        amount=amount,
        currency=share_transaction.currency,
        broker=share_transaction.broker,
        source=share_transaction.source,
    )
    return BrokerTransaction(
        date=transaction.date,
        action=share_transaction.action,
        symbol=contract.underlying,
        description=share_transaction.description,
        quantity=assigned_shares,
        price=price,
        fees=share_transaction.fees,
        amount=amount,
        currency=share_transaction.currency,
        broker=share_transaction.broker,
        source=share_transaction.source,
        capital_adjustments=_assignment_adjustments(assignment.allocations),
        affects_cash_balance=False,
    )


@dataclass
class _OptionActivity:
    """What one account's option rows said, once read as lifecycles."""

    open_lots: dict[OptionContract, list[_WrittenOptionLot]]
    assignments: dict[tuple[datetime.date, OptionContract], _OptionAssignment]


def _record_close_costs(
    close: SchwabTransaction,
    allocations: list[tuple[_WrittenOptionLot, Decimal]],
) -> None:
    """Spread a closing debit over the grants it closed, largest lot last."""
    amount = close.amount
    assert amount is not None
    total_cost = -amount
    allocated_cost = Decimal(0)
    total_quantity = _option_quantity(close, _row_file(close))
    for index, (lot, quantity) in enumerate(allocations):
        cost = (
            total_cost - allocated_cost
            if index == len(allocations) - 1
            else total_cost * quantity / total_quantity
        )
        allocated_cost += cost
        tax_data = lot.transaction.written_option_tax
        assert tax_data is not None
        tax_data.close_costs.append(DatedAmount(close.date, cost, close.currency))


def _scan_option_activity(
    transactions: list[SchwabTransaction],
) -> _OptionActivity:
    """Validate option rows and allocate every outcome to its grants."""
    open_lots: dict[OptionContract, list[_WrittenOptionLot]] = defaultdict(list)
    assignments: dict[tuple[datetime.date, OptionContract], _OptionAssignment] = {}

    for transaction in transactions:
        contract = transaction.option_contract
        if contract is None:
            continue
        _validate_written_option_row(transaction, _row_file(transaction))
        if transaction.action is ActionType.OPTION_GRANT:
            quantity = _option_quantity(transaction, _row_file(transaction))
            transaction.written_option_tax = WrittenOptionTaxData(quantity)
            open_lots[contract].append(_WrittenOptionLot(transaction, quantity))
            continue

        allocations = _allocate_option_outcome(
            transaction, open_lots, _row_file(transaction)
        )
        if transaction.action is ActionType.OPTION_CLOSE:
            _record_close_costs(transaction, allocations)
        elif transaction.action is ActionType.OPTION_ASSIGNMENT:
            for lot, quantity in allocations:
                lot.assigned += quantity
            key = (transaction.date, contract)
            if key in assignments:
                # Each assignment settles into a stock row of its own, and
                # nothing in the export says which row belongs to which
                # assignment. Merging them asks for one row of the combined
                # size, which Schwab did not write, and pairing them up is
                # guesswork.
                raise ParsingError(
                    _row_file(transaction),
                    f"Schwab reports more than one assignment of {contract} on "
                    f"{transaction.date}. Reconciling each one to the shares "
                    "it delivered is not supported: remove the rows for this "
                    "contract and record the result by hand.",
                )
            assignments[key] = _OptionAssignment(transaction, allocations)

    # An assigned contract's premium belongs to the share transaction, so it
    # must not also be taxed as a disposal of the option.
    for lots in open_lots.values():
        for lot in lots:
            tax_data = lot.transaction.written_option_tax
            assert tax_data is not None
            tax_data.taxable_quantity -= lot.assigned

    return _OptionActivity(open_lots=open_lots, assignments=assignments)


def _emit_option_transactions(
    transactions: list[SchwabTransaction],
    activity: _OptionActivity,
) -> list[BrokerTransaction]:
    """Replace every terminal option row with what it really was."""
    settled_share_rows: dict[SchwabRowKey, BrokerTransaction] = {}
    settlements = {
        _row_key(assignment.transaction): _apply_option_assignment(
            transactions,
            assignment,
            settled_share_rows,
            _row_file(assignment.transaction),
        )
        for assignment in activity.assignments.values()
    }

    # The assigned shares take the place of the assignment row, so they keep
    # the position the outcome had in the history rather than landing after
    # every other row of the export.
    reconciled: list[BrokerTransaction] = []
    for transaction in transactions:
        row_key = _row_key(transaction)
        settled = settled_share_rows.get(row_key)
        if settled is not None:
            # The shares moved to the assignment date; the cash stays here.
            reconciled.append(settled)
            continue
        if transaction.action is ActionType.OPTION_EXPIRY:
            # An expiry ends the option and moves no cash: the premium is
            # already the grant's gain.
            continue
        settlement = settlements.get(row_key)
        reconciled.append(settlement if settlement is not None else transaction)
    return reconciled


def reconcile_written_options(
    transactions: list[SchwabTransaction],
) -> list[BrokerTransaction]:
    """Resolve Schwab written-option lifecycles for UK capital gains."""
    return _emit_option_transactions(transactions, _scan_option_activity(transactions))
