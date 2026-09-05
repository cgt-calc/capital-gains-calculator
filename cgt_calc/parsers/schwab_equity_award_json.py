"""Charles Schwab Equity Award transaction export parser.

Schwab states one Equity Awards history in two export layouts, JSON and CSV.
Both are read here, and which one a file is comes from its contents.

To get the data from Schwab:
1. Open https://client.schwab.com/app/accounts/history/#/
2. Make sure Equity Award Center is selected
3. Select date range 'Previous 4 Years' and click SEARCH
4. At the top right, click on Export and select either type
5. If you have had Equity Awards for more than 4 years, good news:
   Schwab now allows you to export all the data history (which you do need
   to calculate the CGT). In that case:
   * repeat the process to export older data
   * manually combine the data into a single file
"""

from __future__ import annotations

from contextlib import contextmanager
import csv
from dataclasses import InitVar, dataclass
import datetime
import decimal
from decimal import Decimal
from enum import Enum, auto
import io
import json
import logging
from typing import TYPE_CHECKING, Any, ClassVar, Final, TextIO, override

from cgt_calc.const import TICKER_RENAMES
from cgt_calc.exceptions import CgtError, ParsingError, UnexpectedColumnCountError
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    TransactionSource,
)
from cgt_calc.util import round_decimal

from .base_parsers import BaseSingleFileParser, read_input

if TYPE_CHECKING:
    import argparse
    from collections.abc import Iterator, Sequence
    from pathlib import Path

OPTIONAL_DETAILS_NAME: Final = "Details"
FIELD_TO_SCHEMA: Final = {"transactions": 1, "Transactions": 2}

# The columns a complete Equity Awards CSV states for the transaction itself.
# A row that blanks all of them continues the transaction above it.
PARENT_COLUMNS: Final = (
    "Date",
    "Action",
    "Symbol",
    "Description",
    "Quantity",
    "FeesAndCommissions",
    "DisbursementElection",
    "Amount",
)

# What tells a complete export from the award-price CSV, which shares the
# parent columns above but prices vests instead of listing transactions. The
# last two columns are the ones only a complete export has.
COMPLETE_CSV_COLUMNS: Final = frozenset(
    (*PARENT_COLUMNS, "VestFairMarketValue", "PurchaseFairMarketValue")
)

# Where the parent row of a CSV transaction was read from. Not a Schwab field,
# so it is stripped back off before the row is converted.
CSV_ROW_INDEX: Final = "_cgt_calc_row_index"

# The payroll side of a vest. It states the vest price, and nothing else in
# an export states it for a plan that delivers the shares elsewhere.
LAPSE_ACTION: Final = "Lapse"

# Actions the converter discards, so an export holding nothing but these
# imports nothing whatever else it states.
DISCARDED_ACTIONS: Final = frozenset({"Journal", "Wire Transfer", LAPSE_ACTION})

LOGGER = logging.getLogger(__name__)


class Restatement(Enum):
    """What the export did to records that predate a split."""

    ACQUISITIONS_ONLY = auto()
    """Acquisitions restated into current units, disposals left in their own.

    Only disposals need converting, and the split dates alone say by how much.
    """

    UNRELIABLE = auto()
    """Some records restated and some not, with nothing in the file saying which.

    The price has to give the units away, so this needs `presplit_price_floor`.
    """


@dataclass(frozen=True)
class SplitHistory:
    """Splits for one symbol, and what the export did to records before them."""

    splits: tuple[tuple[datetime.date, int], ...]
    """Ratios against the first day on which counts are in the new units."""

    restatement: Restatement

    presplit_price_floor: Decimal | None = None
    """A price above which a row is certainly still in pre-split units.

    Set it above the highest price the share reached before its earliest split
    and below the lowest price it can plausibly reach after: everything in
    between is indistinguishable. Only meaningful with UNRELIABLE.
    """

    def __post_init__(self) -> None:
        """Reject a table that cannot be applied."""
        if (self.restatement is Restatement.UNRELIABLE) != (
            self.presplit_price_floor is not None
        ):
            raise ValueError(
                "presplit_price_floor is required for UNRELIABLE and "
                "meaningless otherwise"
            )


# Splits dated to the first day on which the export states counts in the new
# units, which is the day after a split effective at the close.
#
# Dates rather than a price heuristic wherever the export allows it: a
# heuristic needs the market price of the day, which means a network call, and
# it stops working as soon as the real price passes the threshold it guesses
# with. GOOG is UNRELIABLE only because its export leaves nothing better.
SPLITS: Final = {
    "NVDA": SplitHistory(
        ((datetime.date(2021, 7, 20), 4), (datetime.date(2024, 6, 10), 10)),
        Restatement.ACQUISITIONS_ONLY,
    ),
    "GOOG": SplitHistory(
        ((datetime.date(2022, 7, 16), 20),),
        Restatement.UNRELIABLE,
        # GOOG never traded above $175 * 20 = $3500 before the split.
        presplit_price_floor=Decimal(175),
    ),
    "GOOGL": SplitHistory(
        ((datetime.date(2022, 7, 16), 20),),
        Restatement.UNRELIABLE,
        presplit_price_floor=Decimal(175),
    ),
}


def split_multiplier(symbol: str | None, on: datetime.date) -> int:
    """Factor turning a share count printed on `on` into current units.

    Prices divide by the same factor, so the money is unchanged.
    """
    history = SPLITS.get(symbol or "")
    if history is None:
        return 1
    factor = 1
    for effective, ratio in history.splits:
        if on < effective:
            factor *= ratio
    return factor


def _restates_acquisitions_only(symbol: str | None) -> bool:
    """Whether disposals of `symbol` are the only records left un-restated."""
    history = SPLITS.get(symbol or "")
    return history is not None and history.restatement is Restatement.ACQUISITIONS_ONLY


def _detail_counts_multiplier(symbol: str | None, on: datetime.date) -> int:
    """Factor turning counts inside a row's details into current units.

    A row's own `Quantity` is restated for later splits while the counts inside
    its `TransactionDetails` are in the units of their own day. The two are
    therefore only comparable through this factor, and a count taken from the
    details only reaches the pool through it.

    Returns 1 where the export does not restate uniformly, because there the
    difference cannot be told apart in the first place.
    """
    if not _restates_acquisitions_only(symbol):
        return 1
    return split_multiplier(symbol, on)


@dataclass
class FieldNames:
    """Names of the fields in a Schwab export, depending on the schema version."""

    # Note that the schema version is not an official Schwab one, just something
    # we use internally in this code:
    schema_version: InitVar[int] = 2

    transactions: str = "Transactions"
    description: str = "Description"
    action: str = "Action"
    symbol: str = "Symbol"
    quantity: str = "Quantity"
    amount: str = "Amount"
    fees: str = "FeesAndCommissions"
    transac_details: str = "TransactionDetails"
    shares: str = "Shares"
    vest_date: str = "VestDate"
    vest_fair_market_value: str = "VestFairMarketValue"
    award_date: str = "AwardDate"
    award_id: str = "AwardId"
    date: str = "Date"
    sale_price: str = "SalePrice"
    purchase_date: str = "PurchaseDate"
    purchase_fair_market_value: str = "PurchaseFairMarketValue"
    net_shares_deposited: str = "NetSharesDeposited"
    shares_withheld: str = "SharesWithheld"
    shares_sold: str = "SharesSold"
    shares_sold_withheld_for_taxes: str = "SharesSoldWithheldForTaxes"
    fair_market_value_price: str = "FairMarketValuePrice"
    tax_withholding_method: str = "TaxWithholdingMethod"

    def __post_init__(self, schema_version: int) -> None:
        """Set correct field names if the schema is not the default one.

        Automatically run on object initialization.
        """
        if schema_version == 1:
            self.transactions = "transactions"
            self.description = "description"
            self.action = "action"
            self.symbol = "symbol"
            self.quantity = "quantity"
            self.amount = "amount"
            self.fees = "totalCommissionsAndFees"
            self.transac_details = "transactionDetails"
            self.shares = "shares"
            self.vest_date = "vestDate"
            self.vest_fair_market_value = "vestFairMarketValue"
            self.award_date = "awardDate"
            self.award_id = "awardName"
            self.date = "eventDate"
            self.sale_price = "salePrice"
            self.purchase_date = "purchaseDate"
            self.purchase_fair_market_value = "purchaseFairMarketValue"
            self.net_shares_deposited = "netSharesDeposited"
            self.shares_withheld = "sharesWithheld"
            self.shares_sold = "sharesSold"
            self.shares_sold_withheld_for_taxes = "sharesSoldWithheldForTaxes"
            self.fair_market_value_price = "fairMarketValuePrice"
            self.tax_withholding_method = "taxWithholdingMethod"


# We want enough decimals to cover what Schwab gives us (up to 4 decimals)
# divided by the share-split factor (20), so we keep 6 decimals.
# We don't want more decimals than necessary or we risk converting
# the float number format approximations into Decimals
# (e.g. a number 1.0001 in JSON may become 1.00010001 when parsed
# into float, but we want to get Decimal('1.0001'))
ROUND_DIGITS = 6

JsonRowType = Any  # type: ignore[explicit-any]


def action_from_str(label: str, file: Path) -> ActionType:
    """Convert string label to ActionType."""
    if label == "Buy":
        return ActionType.BUY

    if label in {"Sell", "Sale", "Forced Quick Sell"}:
        return ActionType.SELL

    if label in {
        "MoneyLink Transfer",
        "Misc Cash Entry",
        "Service Fee",
        "Wire Funds",
        "Wire Transfer",
        "Funds Received",
        "Journal",
        "Cash In Lieu",
        # Proceeds of the historical autosale programme wired out of the
        # account, see https://github.com/cgt-calc/capital-gains-calculator/issues/488
        "Forced Disbursement",
    }:
        return ActionType.TRANSFER

    if label in {"Stock Plan Activity", "Deposit"}:
        return ActionType.STOCK_ACTIVITY

    if label in {"Qualified Dividend", "Cash Dividend", "Dividend"}:
        return ActionType.DIVIDEND

    if label in {
        "NRA Tax Adj",
        "NRA Withholding",
        "Foreign Tax Paid",
        "Tax Reversal",
        "Tax Withholding",
    }:
        return ActionType.DIVIDEND_TAX

    if label == "ADR Mgmt Fee":
        return ActionType.FEE

    if label in {"Adjustment", "IRS Withhold Adj"}:
        return ActionType.ADJUSTMENT

    if label in {"Short Term Cap Gain", "Long Term Cap Gain"}:
        return ActionType.CAPITAL_GAIN

    if label == "Spin-off":
        return ActionType.SPIN_OFF

    if label == "Credit Interest":
        return ActionType.INTEREST

    if label == "Reinvest Shares":
        return ActionType.REINVEST_SHARES

    if label == "Reinvest Dividend":
        return ActionType.REINVEST_DIVIDENDS

    if label == "Wire Funds Received":
        return ActionType.WIRE_FUNDS_RECEIVED

    # Shares moved out of the account with no money attached. Who received
    # them decides the tax, and the export does not say, so the calculator
    # refuses it with instructions rather than assuming.
    if label == "Gift":
        return ActionType.UNCLASSIFIED_GIFT

    raise ParsingError(file, f"Unknown action: {label}")


class _UnparseableNumber(decimal.InvalidOperation):
    """A field that does not hold a finite number, and which field it was.

    `Decimal` accepts NaN and Infinity, and neither survives what this parser
    does next: a NaN raises out of the first check of its sign and an Infinity
    passes every such check and reaches the arithmetic. Both are as useless
    here as any other nonsense in the field, so they are raised as the same
    thing.

    It carries the field and the value because most of the places that read a
    number know which field it came from but not what row it belongs to, and
    the ones that know the row are too far out to know the field. Deriving
    from `InvalidOperation` keeps the handlers that already translate this
    into their own message working unchanged.
    """

    def __init__(self, field: str, value: object) -> None:
        """Name the field and the value that is not a finite number."""
        self.field = field
        self.value = value
        super().__init__(f"{field} is not a finite number: {value!r}")


def _decimal_from_str(price_str: str, field: str = "number") -> Decimal:
    """Convert a number as string to a Decimal.

    Remove $ sign, and comma thousand separators so as to handle dollar amounts
    such as "$1,250.00".

    A field Schwab states as a string can arrive as something else: JSON allows
    a bare number where the export writes a quoted one, and the decoder hands
    that over as a `Decimal`, which has no `replace`. That is as unusable here
    as any other nonsense in the field, so it is refused the same way rather
    than escaping as an `AttributeError` with no field name on it.
    """
    try:
        value = Decimal(price_str.replace("$", "").replace(",", "").strip())
    except (decimal.InvalidOperation, AttributeError, TypeError, ValueError) as err:
        raise _UnparseableNumber(field, price_str) from err
    if not value.is_finite():
        raise _UnparseableNumber(field, price_str)
    return value


def _decimal_from_number_or_str(
    row: JsonRowType,
    field_basename: str,
    field_float_suffix: str = "SortValue",
) -> Decimal:
    """Get a number from a row, preferably from the number field.

    Fall back to the string representation field, or default to Decimal(0)
    if the fields are not there or both have a value of None or empty string.
    """
    # We prefer native number to strings as more efficient/safer parsing
    float_name = f"{field_basename}{field_float_suffix}"
    if float_name in row and row[float_name] is not None:
        # json.load reads the NaN and Infinity literals Python allows in JSON
        # through parse_constant rather than parse_float, so a number field is
        # no safer than a string one here. Nor is it certain to hold a number
        # at all: the companion is only a convention, and Decimal() raises on
        # a string it cannot read and on a type it cannot take.
        try:
            value = Decimal(row[float_name])
        except (decimal.InvalidOperation, TypeError, ValueError) as err:
            raise _UnparseableNumber(float_name, row[float_name]) from err
        if not value.is_finite():
            raise _UnparseableNumber(float_name, row[float_name])
        return value

    # An empty string means the field is not filled in, the same as it being
    # absent. Schwab writes one for FeesAndCommissions on a commission-free
    # sale. Note this is deliberately not done inside _decimal_from_str: an
    # empty *price* must raise rather than become a zero cost basis, which
    # would be taxed in full.
    if (
        field_basename in row
        and row[field_basename] is not None
        and row[field_basename] != ""
    ):
        return _decimal_from_str(row[field_basename], field_basename)

    return Decimal(0)


def _is_integer(number: Decimal) -> bool:
    return number % 1 == 0


def _optional_decimal(details: JsonRowType, field_name: str) -> Decimal | None:
    """Read a count Schwab may leave blank.

    An absent field and an empty one mean the same thing: Schwab had nothing to
    put there. A "0" does not — it is a count, and a real one.
    """
    value = details.get(field_name)
    if value is None or value == "":
        return None
    return _decimal_from_str(value, field_name)


def _lot_share_sum(row: JsonRowType, names: FieldNames) -> Decimal | None:
    """Recover a sale's quantity where the row rounds it and the lots do not.

    Returns None when the lots cannot answer: one of them states no count, or
    they are all whole and so cannot hold a fraction the row dropped.
    """
    total = Decimal(0)
    found_decimals = False
    for subtransac in row[names.transac_details]:
        details = subtransac.get(OPTIONAL_DETAILS_NAME, subtransac)
        if names.shares not in details:
            return None
        shares = _decimal_from_str(details[names.shares], names.shares)
        total += shares
        if not _is_integer(shares):
            found_decimals = True
    return total if found_decimals else None


def _lot_price(row: JsonRowType, names: FieldNames) -> Decimal | None:
    """Return the one price every lot sold at, or None if they differ."""
    prices = set()
    for subtransac in row[names.transac_details]:
        details = subtransac.get(OPTIONAL_DETAILS_NAME, subtransac)
        if names.sale_price not in details:
            return None
        prices.add(details[names.sale_price])
    if len(prices) != 1:
        return None
    return _decimal_from_str(prices.pop(), names.sale_price)


def _recovered_sale_quantity(
    row: JsonRowType,
    names: FieldNames,
    quantity: Decimal,
    amount: Decimal,
    fees: Decimal,
) -> Decimal | None:
    """Return the count a sale really disposed of, where its Quantity rounds it.

    The lots answer first, when they kept the decimals the row dropped. Failing
    that the money answers, provided every lot sold at one price — and that is
    the only witness left when a lot stating zero shares hides the fraction
    from both count fields at once.

    The money is only consulted when the row does not already reconcile with
    it, because the price is rounded for display and recomputing from it turns
    an exact 5 into 4.999995.

    Returns None when neither answers: lots sold at different prices leave the
    row's own figure as the best there is.
    """
    lots = _lot_share_sum(row, names)
    if lots is not None:
        return lots

    price = _lot_price(row, names)
    if not price:
        return None
    if round_decimal(quantity * price - fees, 2) == amount:
        return None
    return (amount + fees) / price


def _espp_net_shares(
    details: JsonRowType,
    names: FieldNames,
    quantity: Decimal,
    multiplier: int,
    file: Path,
) -> Decimal:
    """How many of an ESPP purchase's shares reach the pool.

    All of them, unless tax was settled out of the purchase itself, in which
    case what was deposited, withheld and sold has to add back up to what was
    bought. Shares taken for tax were never acquired, so they are not a
    disposal and produce no gain; they simply never enter the pool.

    The counts are in the units of the purchase date and `quantity` is
    restated, so both the identity and the pooled figure go through
    `multiplier`. Without it a purchase made before a later split reads as one
    that does not add up, and the file stops parsing.
    """
    deposited = _optional_decimal(details, names.net_shares_deposited)
    withheld = _optional_decimal(details, names.shares_withheld)
    sold = _optional_decimal(details, names.shares_sold)
    method = details.get(names.tax_withholding_method)

    if deposited is None and withheld is None and sold is None and not method:
        return quantity

    when = details.get(names.purchase_date, "an unknown date")

    if deposited is None:
        raise ParsingError(
            file,
            f"ESPP purchase on {when} settles tax in shares ({method}) but "
            "does not say how many were deposited",
        )

    accounted = (
        deposited + (withheld or Decimal(0)) + (sold or Decimal(0))
    ) * multiplier
    if accounted != quantity:
        raise ParsingError(
            file,
            f"ESPP purchase on {when} bought {quantity} shares but accounts "
            f"for {accounted}: {deposited} deposited, {withheld or 0} "
            f"withheld, {sold or 0} sold, at a split multiplier of "
            f"{multiplier}. Pooling the deposited count alone would drop the "
            "difference without saying so",
        )

    return deposited * multiplier


def _number_error(
    err: _UnparseableNumber, row: JsonRowType, names: FieldNames, file_path: Path
) -> ParsingError:
    """Say which row a field that is not a number belongs to.

    The reads that already refuse a bad field with a message of their own
    never reach this; it is for the rest, so that none of them can leave a
    user with a traceback instead of the row to go and look at.
    """
    return ParsingError(
        file_path,
        f"Invalid {err.field} '{err.value}' for "
        f"{row.get(names.action)} on {row.get(names.date)}",
    )


def _transaction(
    row: JsonRowType, file_path: Path, names: FieldNames
) -> SchwabAwardTransaction:
    """Build one transaction, naming the row where a field is not a number."""
    try:
        return SchwabAwardTransaction(row, file_path, names)
    except _UnparseableNumber as err:
        raise _number_error(err, row, names, file_path) from err


def _check_lapse_units(
    rows: list[JsonRowType], file_path: Path, names: FieldNames
) -> None:
    """Check the split table against the record that states both units.

    A Lapse is not an acquisition and is discarded, but on the way past it
    proves something no other record can. Its Quantity is restated for later
    splits while the counts inside it are in the units of their own day, so the
    two are related by exactly the split multiplier and nothing else.

    That makes the split table falsifiable using only the file. If a date is
    wrong, or a split has happened that this code has not been told about, it
    surfaces here — before any disposal has been converted on the strength of
    it.
    """
    for row in rows:
        if row.get(names.action) != LAPSE_ACTION:
            continue
        with _csv_row_errors(row.get(CSV_ROW_INDEX), file_path):
            _check_one_lapse(row, file_path, names)


def _check_one_lapse(row: JsonRowType, file_path: Path, names: FieldNames) -> None:
    """Check one Lapse row's stated units against the split table."""
    symbol = row.get(names.symbol)
    symbol = TICKER_RENAMES.get(symbol, symbol)
    if not _restates_acquisitions_only(symbol):
        return

    detail_rows = row.get(names.transac_details) or []
    if not detail_rows:
        return
    details = detail_rows[0].get(OPTIONAL_DETAILS_NAME, detail_rows[0])

    try:
        deposited = _optional_decimal(details, names.net_shares_deposited)
        withheld = _optional_decimal(details, names.shares_sold_withheld_for_taxes)
        quantity = _decimal_from_number_or_str(row, names.quantity)
    except _UnparseableNumber as err:
        raise _number_error(err, row, names, file_path) from err

    if deposited is None or withheld is None:
        return

    date = datetime.datetime.strptime(row[names.date], "%m/%d/%Y").date()
    multiplier = _detail_counts_multiplier(symbol, date)
    expected = (deposited + withheld) * multiplier

    if quantity != expected:
        raise ParsingError(
            file_path,
            f"The lapse on {row[names.date]} reports {quantity} shares, "
            f"but {deposited} deposited plus {withheld} withheld at a "
            f"split multiplier of {multiplier} makes {expected}. Either "
            "the split table in this file is wrong or a split is missing "
            "from it; every disposal before that split would be converted "
            "by the wrong factor",
        )


def _check_disposal_units(
    transactions: Sequence[BrokerTransaction], file_path: Path
) -> None:
    """Warn where a disposal looks like it had already been restated.

    Converting disposals rests on Schwab restating acquisitions and leaving
    disposals alone. That is what its NVDA exports do, but it is an observation
    about the file rather than a guarantee, and the GOOG handling in this same
    parser exists because Schwab restated some records and not others.

    Acquisitions are the yardstick: once a disposal is converted the two are in
    the same units, so a disposal should cost about what shares cost around it,
    and one converted twice is cheaper by the whole multiplier.

    This warns rather than raises because the yardstick is a price. A share
    that halves between the nearest vest and the sale trips the 4:1 threshold
    on its own, which is a week NVDA has had more than once. The checks that do
    raise are the ones a price move cannot reach.
    """
    acquisitions = [
        transaction
        for transaction in transactions
        if transaction.action == ActionType.STOCK_ACTIVITY
        and transaction.price
        and _restates_acquisitions_only(transaction.symbol)
    ]
    if not acquisitions:
        return

    for transaction in transactions:
        if (
            transaction.action != ActionType.SELL
            or not _restates_acquisitions_only(transaction.symbol)
            or not transaction.price
        ):
            continue

        multiplier = split_multiplier(transaction.symbol, transaction.date)
        if multiplier == 1:
            continue

        same_symbol = [
            acquisition
            for acquisition in acquisitions
            if acquisition.symbol == transaction.symbol
        ]
        if not same_symbol:
            continue

        nearest = min(
            same_symbol,
            key=lambda acquisition: abs((acquisition.date - transaction.date).days),
        )
        if not nearest.price:
            continue

        ratio = transaction.price / nearest.price

        # Between "these agree" and "this one is a multiplier too cheap", take
        # whichever is nearer on a log scale: ratio < 1/sqrt(multiplier), or
        # ratio^2 * multiplier < 1 without a square root. The two hypotheses
        # are a factor of 4 apart at the very least, so the midpoint is not a
        # threshold that needs tuning.
        if ratio * ratio * multiplier < 1:
            LOGGER.warning(
                "%s: the disposal on %s works out at %s a share once converted "
                "for the %s:1 split, against %s for the acquisition on %s. "
                "Being cheaper by about the split multiplier is what it looks "
                "like when Schwab had already restated a disposal and it was "
                "converted a second time, which would take the wrong number of "
                "shares out of the pool while leaving the proceeds right. A "
                "share that simply fell that far between the two dates looks "
                "the same, so check it rather than trust it",
                file_path,
                transaction.date,
                round_decimal(transaction.price, 4),
                multiplier,
                round_decimal(nearest.price, 4),
                nearest.date,
            )


def _decimal_field(
    value: str, field: str, what: str, date: datetime.date, file: Path
) -> Decimal:
    """Parse one of Schwab's string numbers, naming it if it is not one."""
    try:
        return _decimal_from_str(value, field)
    except decimal.InvalidOperation as err:
        raise ParsingError(
            file,
            f"Invalid {field} '{value}' for {what} on {date}",
        ) from err


def _parse_vest_price(
    details: JsonRowType, names: FieldNames, date: datetime.date, file: Path
) -> Decimal:
    """Parse the Vest Fair Market Value for a vest Deposit."""
    vest_fmv_str = details.get(names.vest_fair_market_value)
    if not vest_fmv_str:
        raise ParsingError(
            file,
            f"Missing or empty {names.vest_fair_market_value} for Deposit on {date}",
        )
    price = _decimal_field(
        vest_fmv_str, names.vest_fair_market_value, "Deposit", date, file
    )
    if price <= 0:
        raise ParsingError(
            file,
            f"The Deposit on {date} states a {names.vest_fair_market_value} of "
            f"'{vest_fmv_str}'. That figure is what the vested shares cost for "
            "tax, so at zero or less they would enter the pool for nothing and "
            "the whole of the proceeds would be taxed when they are sold. "
            "Correct it in the export from your vest confirmation.",
        )
    return price


def _sale_lots(
    row: JsonRowType, names: FieldNames, date: datetime.date, file: Path
) -> list[tuple[Decimal | None, str | None, Decimal | None]]:
    """Every lot of a Sale, as (share count, price as printed, price).

    Either field is None where the export leaves it out, which it does: a lot
    may state a count without a price or the other way round, and which of
    them is load-bearing depends on the caller.

    What a lot does state has to make sense, though, and that is checked for
    every sale. A row whose own Quantity kept its decimals works its price out
    from the money and needs none of these fields, but one that states a
    nonsense figure is not the row it appears to be, and the pool is not the
    place to find that out.
    """
    subtransacs = row.get(names.transac_details)
    if not subtransacs:
        raise ParsingError(
            file,
            f"Expected transaction details for Sale on {date}",
        )

    lots: list[tuple[Decimal | None, str | None, Decimal | None]] = []
    for subtransac in subtransacs:
        details = subtransac.get(OPTIONAL_DETAILS_NAME, subtransac)

        shares = None
        if names.shares in details:
            # Schwab only provides this one as a string:
            shares = _decimal_field(
                details[names.shares], names.shares, "Sale", date, file
            )
            # A zero is ordinary — Schwab writes one for a lot it has split
            # the count away from — but a negative one would quietly take
            # shares off the disposal.
            if shares < 0:
                raise ParsingError(
                    file,
                    f"The Sale on {date} states {shares} shares for one of its "
                    f"lots. A lot cannot dispose of a negative number of "
                    f"shares, and this one would take {-shares} off the number "
                    f"sold. Correct the {names.shares} field in the export "
                    "from your trade confirmation.",
                )

        price_str = details.get(names.sale_price) or None
        price = None
        if price_str is not None:
            price = _decimal_field(price_str, names.sale_price, "Sale", date, file)
            if price <= 0:
                raise ParsingError(
                    file,
                    f"The Sale on {date} states a {names.sale_price} of "
                    f"'{price_str}' for one of its lots. A sale has to have a "
                    "positive price, and the number of shares sold is worked "
                    "out from it, so this row cannot be used. Correct it in "
                    "the export from your trade confirmation.",
                )

        lots.append((shares, price_str, price))

    return lots


def _parse_sale_subtransactions(
    row: JsonRowType,
    names: FieldNames,
    date: datetime.date,
    quantity: Decimal,
    amount: Decimal,
    fees: Decimal,
    file: Path,
) -> tuple[Decimal, Decimal]:
    """Parse sub-transactions for a Sale to compute quantity and price."""
    lots = _sale_lots(row, names, date, file)

    subtransac_have_quantities = True
    subtransac_shares_sum = Decimal()  # Decimal 0
    found_share_decimals = False

    for shares, _price_str, _price in lots:
        if shares is None:
            subtransac_have_quantities = False
            break
        subtransac_shares_sum += shares
        if not _is_integer(shares):
            found_share_decimals = True

    if subtransac_have_quantities and found_share_decimals:
        # The lots kept the decimals the row rounded away. Their counts are
        # each non-negative and one of them is a fraction, so the sum is
        # positive; the check is here because what it guards is a division.
        if subtransac_shares_sum <= 0:
            raise ParsingError(
                file,
                f"The Sale on {date} has lots adding up to "
                f"{subtransac_shares_sum} shares, so what a share sold for "
                f"cannot be worked out. Take the counts from your trade "
                f"confirmation and correct the {names.shares} fields in the "
                "export.",
            )
        return subtransac_shares_sum, (amount + fees) / subtransac_shares_sum

    # Schwab sometimes only gives us overall transaction amount, and sale
    # price of the sub-transactions. We can only work-out the correct
    # quantity if all sub-transactions have the same price. Whole share
    # counts on the lots are no help: Schwab rounds those the same way it
    # rounds the row's own Quantity, so they cannot settle it either.
    prices: dict[str, Decimal] = {}
    for _shares, price_str, price in lots:
        if price_str is None or price is None:
            raise ParsingError(
                file,
                f"Missing or empty {names.sale_price} for Sale on {date}",
            )
        prices[price_str] = price

    if len(prices) > 1:
        raise ParsingError(
            file,
            f"The Sale on {date} of {amount} was made up of lots sold at "
            f"different prices ({', '.join(sorted(prices))}), and the "
            f"export gives no reliable {names.shares} count for each lot, so "
            "the number of shares sold cannot be worked out. Take the counts "
            "from the trade confirmation for that day and split this sale in "
            "the export into one transaction per sale price.",
        )

    price = next(iter(prices.values()))

    # Check if the transaction was lacking decimals. Sometimes we did just
    # sell an integer number of shares. If we unconditionally perform the
    # quantity update step here, small roundings that were necessary to
    # determine the amount on the broker side can cause tiny decimal
    # deviations in the quantity, which can then cause errors later on, e.g.
    # triggering assertions about selling more than was available.
    if round_decimal(quantity * price - fees, 2) != amount:
        quantity = (amount + fees) / price

    return quantity, price


def _row_decimal(
    row: JsonRowType, field: str, names: FieldNames, raw_action: str, file: Path
) -> Decimal:
    """Read one of a row's own count or money fields, refusing what is not one.

    These are read before the row's action has been looked at, so this is the
    one place that can name the field for them.
    """
    try:
        return _decimal_from_number_or_str(row, field)
    except decimal.InvalidOperation as err:
        printed = row.get(f"{field}SortValue")
        if printed is None:
            printed = row.get(field)
        raise ParsingError(
            file,
            f"Invalid {field} '{printed}' for {raw_action} on {row.get(names.date)}",
        ) from err


def _parse_cash_amount(
    row: JsonRowType,
    names: FieldNames,
    raw_action: str,
    date: datetime.date,
    file: Path,
) -> Decimal:
    """Parse amount for dividend, tax, or transfer transactions.

    These rows are nothing but money, so one that states none is malformed
    rather than a zero.
    """
    if not row.get(names.amount) and row.get(f"{names.amount}SortValue") is None:
        raise ParsingError(
            file,
            f"Missing or empty {names.amount} for {raw_action} on {date}",
        )

    return _decimal_from_number_or_str(row, names.amount)


class SchwabAwardTransaction(BrokerTransaction):
    """Represent a single Schwab equity award transaction."""

    def __init__(self, row: JsonRowType, file: Path, field_names: FieldNames) -> None:
        """Create a new SchwabAwardTransaction from a decoded row."""
        names = field_names
        description = row[names.description]
        self.raw_action = row[names.action]

        # Set where a purchase is known to have pooled nothing. A quantity of
        # zero reached any other way is a malformed row, and has to stay
        # visible so the calculator can refuse it.
        self.acquired_nothing = False
        action = action_from_str(self.raw_action, file)
        symbol = row.get(names.symbol)
        symbol = TICKER_RENAMES.get(symbol, symbol)
        if symbol not in SPLITS:
            LOGGER.warning(
                "The Schwab Equity Awards parser was only tested for the "
                "stocks whose splits it knows (%s), but found symbol '%s'. "
                "Everything else is parsed as usual; what is not applied is "
                "stock split normalization, which this parser only knows for "
                "the symbols above. If '%s' has split since the earliest "
                "transaction here, check the share counts against a broker "
                "statement before filing.",
                ", ".join(sorted(SPLITS)),
                symbol,
                symbol,
            )
        quantity = _row_decimal(row, names.quantity, names, self.raw_action, file)
        amount = _row_decimal(row, names.amount, names, self.raw_action, file)
        fees = _row_decimal(row, names.fees, names, self.raw_action, file)
        if row[names.action] == "Deposit":
            if len(row[names.transac_details]) != 1:
                raise ParsingError(
                    file,
                    "Expected a single Transaction Details for a Deposit, but "
                    f"found {len(row[names.transac_details])}",
                )
            if OPTIONAL_DETAILS_NAME in row[names.transac_details][0]:
                details = row[names.transac_details][0]["Details"]
            else:
                details = row[names.transac_details][0]

            if description == "ESPP":
                # An ESPP purchase, not a vest, and it carries different
                # fields. The cost basis is the market value on the purchase
                # date rather than the discounted price paid, because the
                # discount is taxed as employment income through payroll.
                # Using the price paid understates the basis roughly tenfold
                # on the older purchases.
                date = datetime.datetime.strptime(
                    details[names.purchase_date], "%m/%d/%Y"
                ).date()
                price = _decimal_from_str(
                    details[names.purchase_fair_market_value],
                    names.purchase_fair_market_value,
                )

                # Since August 2025 the tax on a purchase is settled out of the
                # shares themselves, and only the rest reaches the pool. Before
                # that these fields are empty and Quantity is already net.
                #
                # Whether the field holds anything is not the signal. An empty
                # string and a "0" both do, and they mean opposite things:
                # nothing was withheld, and everything was. Reading either as
                # "use this count" loses the whole purchase from the pool
                # without saying so.
                #
                # So the counts have to account for every share bought. That
                # settles it either way, and it is the same identity a Lapse
                # has to satisfy.
                quantity = _espp_net_shares(
                    details,
                    names,
                    quantity,
                    _detail_counts_multiplier(symbol, date),
                    file,
                )
                self.acquired_nothing = quantity == 0

                amount = price * quantity
                description = f"ESPP purchase on {details[names.purchase_date]}"
            else:
                date = datetime.datetime.strptime(
                    details[names.vest_date], "%m/%d/%Y"
                ).date()
                # Schwab only provides this one as a string:
                price = _parse_vest_price(details, names, date, file)
                if amount == Decimal(0):
                    amount = price * quantity
                description = (
                    f"Vest from Award Date "
                    f"{details[names.award_date]} "
                    f"(ID {details[names.award_id]})"
                )
        elif row[names.action] in {"Sale", "Forced Quick Sell"}:
            date = datetime.datetime.strptime(row[names.date], "%m/%d/%Y").date()

            if _restates_acquisitions_only(symbol):
                # Derive the price from the money rather than from the per-lot
                # SalePrice. Amount is net of fees, so the gross is rebuilt
                # first. Two reasons this is not just a shortcut:
                #
                # A disposal can span lots sold at different prices — the
                # 600-share sale of 30 May 2025 has two — and the branch below
                # refuses those outright. And the per-lot price is printed in
                # the units of the trade date while the pool is in current
                # ones, so it would need converting anyway. Working from the
                # money sidesteps both, and the money is the same in any units.
                #
                # The count still has to be recovered when the row has rounded
                # it away, or the pool loses the fraction silently.
                #
                # The lots are checked first for the same reason as on the
                # other two paths: the helpers below read them without
                # judging them, so a negative count would just be summed in
                # and take shares out of the pool with the money still right.
                _sale_lots(row, names, date, file)
                quantity = (
                    _recovered_sale_quantity(row, names, quantity, amount, fees)
                    or quantity
                )
                price = (amount + fees) / quantity
            elif not _is_integer(quantity):
                # Nothing to recover: the row kept its own decimals, and the
                # money gives the price a share sold at. The lots are checked
                # all the same, so a malformed one cannot reach the pool on
                # the strength of a fractional Quantity.
                _sale_lots(row, names, date, file)
                price = (amount + fees) / quantity
            else:
                quantity, price = _parse_sale_subtransactions(
                    row,
                    names,
                    date,
                    quantity,
                    amount,
                    fees,
                    file,
                )

        elif action is ActionType.UNCLASSIFIED_GIFT:
            date = datetime.datetime.strptime(row[names.date], "%m/%d/%Y").date()
            price = None
            # A gift moves shares, not money. Anything in these fields means
            # the row is not what this branch assumes it is.
            if amount or fees:
                raise ParsingError(
                    file, "Unexpected amount or fees on a gift of shares."
                )
            description = self.raw_action
        elif action in {
            ActionType.DIVIDEND,
            ActionType.DIVIDEND_TAX,
            # Pure cash movements, e.g. Forced Disbursement wiring out the
            # proceeds of the autosale programme.
            ActionType.TRANSFER,
        }:
            date = datetime.datetime.strptime(row[names.date], "%m/%d/%Y").date()
            price = None
            amount = _parse_cash_amount(row, names, self.raw_action, date, file)
            description = self.raw_action
        else:
            raise ParsingError(
                file, f"Parsing for action {row[names.action]} is not implemented!"
            )

        currency = CurrencyCode("USD")
        broker = "Charles Schwab"
        super().__init__(
            date,
            action,
            symbol,
            description,
            quantity,
            price,
            fees,
            amount,
            currency,
            broker,
        )

        history = SPLITS.get(symbol or "")
        if history is None:
            return
        if history.restatement is Restatement.ACQUISITIONS_ONLY:
            self._convert_disposal_to_current_units()
        else:
            self._convert_row_priced_before_a_split(history)

    def _convert_disposal_to_current_units(self) -> None:
        """Convert a disposal into current share units.

        Schwab restates acquisitions for later splits — an NVDA vest of 3,000
        at $22.341 is recorded that way even though 300 at $223.41 is what
        happened — but leaves disposals in the units of their own day. Only
        disposals need converting, and a gift is one: shares leave the account
        the same way, so its count is printed in the units of its own day too.

        Missing this costs 54 shares on the two 2023 disposals, and the pool
        stays wrong by that much for every year afterwards.
        """
        if self.action not in {ActionType.SELL, ActionType.UNCLASSIFIED_GIFT}:
            return

        self._rescale(split_multiplier(self.symbol, self.date))

    def _convert_row_priced_before_a_split(self, history: SplitHistory) -> None:
        """Convert a row whose price shows it was never restated.

        Where the export restates some records and not others, with nothing
        saying which, the price is the only witness left: above the floor it
        can only be pre-split money. GOOG is the reason this exists — as of
        2022-08-07 its exports were restated only in part.
        """
        floor = history.presplit_price_floor
        assert floor is not None  # guaranteed by SplitHistory.__post_init__
        multiplier = split_multiplier(self.symbol, self.date)
        if (
            self.action is ActionType.UNCLASSIFIED_GIFT
            and multiplier != 1
            and self.quantity
        ):
            # A gift carries no money, so the price cannot say which units the
            # count is in, and being wrong here is wrong by the whole
            # multiplier. Record both readings rather than pick one; the
            # calculator refuses unless the user's own row settles it.
            self.ambiguous_quantity = round_decimal(
                self.quantity * multiplier, ROUND_DIGITS
            )
            return
        if self.price and self.price > floor and self.quantity:
            self._rescale(multiplier)

    def _rescale(self, multiplier: int) -> None:
        """Restate this row in current units, leaving the money unchanged."""
        if multiplier == 1:
            return
        if self.quantity:
            self.quantity = round_decimal(self.quantity * multiplier, ROUND_DIGITS)
        if self.price:
            self.price = round_decimal(self.price / multiplier, ROUND_DIGITS)


def _read_json_transactions(
    content: str, file_path: Path
) -> tuple[list[JsonRowType], FieldNames]:
    """Decode a Schwab Equity Awards JSON export and say which schema it is."""
    try:
        data = json.loads(content, parse_float=Decimal, parse_int=Decimal)
    except json.decoder.JSONDecodeError as exception:
        raise ParsingError(
            file_path,
            "Could not parse content as JSON",
        ) from exception

    for field_name, schema_version in FIELD_TO_SCHEMA.items():
        if field_name in data:
            fields = FieldNames(schema_version)
            break
    else:
        raise ParsingError(
            file_path,
            f"Expected top level field ({', '.join(FIELD_TO_SCHEMA.keys())}) "
            "not found: the JSON data is not in the expected format",
        )

    if not isinstance(data[fields.transactions], list):
        raise ParsingError(
            file_path,
            f"'{fields.transactions}' is not a list: the JSON data is not "
            "in the expected format",
        )

    return data[fields.transactions], fields


# What to offer after refusing to price a vest whose units cannot be settled.
# Conditional on the file the reader turns out to have rather than on their
# share plan: which export an awards account produces is an inference from the
# fields the two layouts carry, not something Schwab documents, and the
# complete export replaces the main history rather than joining it.
COMPLETE_EXPORT_CAVEAT: Final = (
    "If you have a complete Equity Awards export, which states each vest's "
    "price and share count together, it imports on its own with "
    "--schwab-award-file. Use it only if it holds everything the calculation "
    "needs: it replaces the main history rather than joining it, so anything "
    "that happened only in the brokerage account would be left out."
)


def price_only_alone_message(*, canonical_option: bool) -> str:
    """Say why an export that only prices vests cannot be used on its own."""
    message = (
        "This Equity Awards export holds vest prices and no transactions: "
        "every row in it is a Lapse. It prices the Stock Plan Activity rows of "
        "a main transaction history rather than importing a history of its "
        "own, so it has to be passed with one:\n"
        "  cgt-calc --schwab-file <main history> --schwab-award-file <this file>"
    )
    if not canonical_option:
        message += (
            "\n--schwab-equity-award-json only adds transactions and does not "
            "price vests, so it cannot use this file at all."
        )
    return message


def lapse_only_rows(
    content: str, file_path: Path
) -> tuple[list[JsonRowType], FieldNames] | None:
    """Return the rows of a JSON award export whose every row is a Lapse.

    This export prices the vests of a main history and imports nothing of its
    own, which is what the award-price CSV does; its CSV form is already read
    that way by the award-price reader in `schwab.py`. It appears to be what
    Schwab writes for a plan whose shares are delivered to a linked brokerage
    account, so that the awards account records the vest and its price but
    never holds the shares. That reading of the two layouts is an inference
    from the fields they carry, and nothing here depends on it: the file is
    classified by what it contains.

    Returns ``None`` where the export is not one of those and belongs to the
    importer that reads a complete history: it is not JSON, it states no
    transactions at all, or it states an action beyond a lapse.

    Classification is separate from `lapse_award_prices` so that a caller can
    settle what the file is, and whether it was passed with the main history
    it needs, before any of its rows are validated. Harvesting first would
    answer a file passed on its own with a complaint about its contents
    instead of with the input it is missing.
    """
    content = content.lstrip("\ufeff \t\r\n")
    if not content.startswith("{"):
        # The complete CSV is out of scope: no export of that layout carrying
        # lapse pricing is on record, and the price CSV that does carry it is
        # read by schwab.py before anything reaches this parser.
        return None
    rows, names = _read_json_transactions(content, file_path)
    # Classification is by action rather than by an empty transaction list: an
    # export that yields nothing is not necessarily one that holds nothing but
    # lapses, and pricing a complete history from its lapses would apply this
    # path's reasoning to acquisitions it has never been asked about.
    actions = {row.get(names.action) for row in rows}
    if actions == {LAPSE_ACTION}:
        return rows, names
    if LAPSE_ACTION in actions and actions <= DISCARDED_ACTIONS:
        others = ", ".join(sorted(str(action) for action in actions - {LAPSE_ACTION}))
        raise ParsingError(
            file_path,
            f"This Equity Awards export states {others} alongside its lapses. "
            "Those rows move cash rather than shares, so cgt-calc discards "
            "them, and it reads vest prices only from an export whose every "
            "row is a Lapse: this file would import nothing and price "
            "nothing. Export the same history as the award-price CSV, which "
            "reads the vests and ignores the cash rows, and pass that with "
            "--schwab-award-file.",
        )
    return None


def lapse_award_prices(
    rows: list[JsonRowType], file_path: Path, names: FieldNames
) -> dict[datetime.date, dict[str, Decimal]]:
    """Read a vest price from every row of a classified price-only export."""
    prices: dict[datetime.date, dict[str, Decimal]] = {}
    priced_at: dict[tuple[datetime.date, str], tuple[Decimal, str]] = {}
    for index, row in enumerate(rows):
        where = f"{names.transactions}[{index}]"
        date, symbol, price = _lapse_price(row, where, file_path, names)
        previous = priced_at.get((date, symbol))
        if previous is not None and previous[0] != price:
            raise ParsingError(
                file_path,
                f"{where} prices the {symbol} vest of {date} at {price}, and "
                f"{previous[1]} prices the same vest at {previous[0]}. Grants "
                "vesting on one day share that day's market value, so cgt-calc "
                "cannot tell which of the two is the acquisition cost. Correct "
                "the export against your vest confirmations.",
            )
        priced_at[date, symbol] = (price, where)
        prices.setdefault(date, {})[symbol] = price

    _reject_symbols_a_split_could_restate(prices, file_path)
    return prices


def _lapse_price(
    row: JsonRowType, where: str, file_path: Path, names: FieldNames
) -> tuple[datetime.date, str, Decimal]:
    """Read one Lapse row's date, normalised symbol and vest price.

    Everything is required. In an export of nothing but lapses a row that
    states no price is a vest that cannot be costed, and skipping it here
    would surface later as a failure reported against the main history, by
    which point the file that could not answer is no longer named.
    """
    stated_symbol = row.get(names.symbol)
    symbol = stated_symbol.strip() if isinstance(stated_symbol, str) else ""
    if not symbol:
        raise ParsingError(
            file_path,
            f"{where}.{names.symbol} names no symbol, so the vest this row "
            "prices cannot be matched to a holding in the main history.",
        )
    # AwardPrices.get renames the symbol it is asked for, so an entry filed
    # under the old spelling would never be found.
    symbol = TICKER_RENAMES.get(symbol, symbol)

    stated_date = row.get(names.date)
    try:
        date = datetime.datetime.strptime(stated_date, "%m/%d/%Y").date()
    except (TypeError, ValueError) as err:
        raise ParsingError(
            file_path,
            f"{where}.{names.date} is not a date cgt-calc reads: "
            f"{stated_date!r}. Equity Awards exports state MM/DD/YYYY.",
        ) from err

    detail_rows = row.get(names.transac_details) or []
    if not isinstance(detail_rows, list):
        raise ParsingError(
            file_path,
            f"{where}.{names.transac_details} does not hold a list of grants, "
            "so this file is not the layout cgt-calc reads. A lapse states "
            "the vest it prices as one entry in that list.",
        )
    if len(detail_rows) != 1:
        raise ParsingError(
            file_path,
            f"{where}.{names.transac_details} holds {len(detail_rows)} "
            "entries, not one. A lapse states the vest it prices once, and "
            "cgt-calc will not guess which market value applies.",
        )
    details = detail_rows[0]
    path = f"{where}.{names.transac_details}[0]"
    if isinstance(details, dict) and OPTIONAL_DETAILS_NAME in details:
        details = details[OPTIONAL_DETAILS_NAME]
        path = f"{path}.{OPTIONAL_DETAILS_NAME}"
    # Checked after the unwrapping, so that an entry stating a `Details` key
    # holding nothing is reported against the key rather than against the
    # entry that carries it.
    if not isinstance(details, dict):
        raise ParsingError(
            file_path,
            f"{path} states no fields for the grant this lapse prices, so "
            "there is no market value in it to read.",
        )
    return date, symbol, _lapse_vest_price(details, names, path, file_path)


def _lapse_vest_price(
    details: JsonRowType, names: FieldNames, path: str, file_path: Path
) -> Decimal:
    """Read one lapse's market value, refusing anything that is not one.

    The read prefers a `SortValue` companion over the displayed string without
    comparing the two, as every other number in this parser does. Some exports
    round the displayed figure on purpose, so requiring them to agree would
    reject valid files.
    """
    field = names.fair_market_value_price
    stated = [details.get(name) for name in (field, f"{field}SortValue")]
    if all(value is None or value == "" for value in stated):
        raise ParsingError(
            file_path,
            f"{path}.{field} states no price. Every row in this export is a "
            "lapse, so a lapse with no market value is a vest cgt-calc cannot "
            "cost. Re-export the history, or pass the award-price CSV instead.",
        )
    try:
        price = _decimal_from_number_or_str(details, field)
    except _UnparseableNumber as err:
        raise ParsingError(
            file_path, f"{path}.{err.field} is not a number: {err.value!r}"
        ) from err
    if price <= 0:
        raise ParsingError(
            file_path,
            f"{path}.{field} states a price of {price}. That figure is what "
            "the vested shares cost for tax, so at zero or less they would "
            "enter the pool for nothing and the whole of the proceeds would be "
            "taxed when they are sold. Correct it from your vest confirmation.",
        )
    return price


def _reject_symbols_a_split_could_restate(
    prices: dict[datetime.date, dict[str, Decimal]], file_path: Path
) -> None:
    """Refuse a symbol whose price and quantity could be in different units.

    On this path the price comes from the award export and the share count
    from the main history. A split restates the records before it, and neither
    file says whether it restated both. Nothing is added to or removed from
    either history here, which makes the transactions safe and says nothing
    about the price: one wrong by the split factor moves the tax silently, so
    where the two cannot be shown to be in the same units, refuse.

    A split falling after every priced row disqualifies the symbol just as one
    inside the range does. An export is generated after the split, so a vest
    before it can already have been restated even when nothing in either file
    mentions the split at all. The boundary is inclusive: a vest on the day
    itself is the one least likely to be restated consistently, and cgt-calc
    cannot tell which side of the close it was priced from.
    """
    earliest: dict[str, datetime.date] = {}
    for date, by_symbol in prices.items():
        for symbol in by_symbol:
            if date < earliest.get(symbol, datetime.date.max):
                earliest[symbol] = date

    for symbol, first in sorted(earliest.items()):
        history = SPLITS.get(symbol)
        if history is None:
            continue
        for effective, ratio in history.splits:
            if first > effective:
                continue
            raise ParsingError(
                file_path,
                f"Schwab states {symbol} share counts in post-split units from "
                f"{effective}, and this export prices a {symbol} vest on "
                f"{first}, on or before that. The price comes from this file "
                "and the share count from the main history, and neither says "
                "whether the split restated both, so the acquisition cost "
                f"could be out by a factor of {ratio} with nothing to show "
                "for it. If no vest in your main history needs a price on or "
                f"before {effective}, re-export the award history from a date "
                "after it. Every vest in the main history is priced whatever "
                "tax year you are calculating, so a shorter export still has "
                "to cover them all. " + COMPLETE_EXPORT_CAVEAT,
            )


@contextmanager
def _csv_row_errors(row_index: int | None, file_path: Path) -> Iterator[None]:
    """Point whatever a row raises at the CSV line it was stated on.

    A JSON export gives an error nothing better than the file to name, and is
    left as it is. A CSV one gives the physical row, which is what the user has
    to look at, so both a `ParsingError` raised without one and a field the CSV
    can omit entirely are answered with it.
    """
    if row_index is None:
        yield
        return
    try:
        yield
    except ParsingError as err:
        err.add_row_context(row_index)
        raise
    except KeyError as err:
        raise ParsingError(
            file_path,
            f"{err.args[0]} is missing from this transaction",
            row_index=row_index,
        ) from err
    except ValueError as err:
        raise ParsingError(file_path, str(err), row_index=row_index) from err


def _csv_aware_transaction(
    row: JsonRowType, file_path: Path, names: FieldNames
) -> SchwabAwardTransaction:
    """Build one transaction, keeping the CSV row it was stated on."""
    row_index = row.pop(CSV_ROW_INDEX, None)
    if row_index is None:
        return _transaction(row, file_path, names)

    with _csv_row_errors(row_index, file_path):
        transaction = _transaction(row, file_path, names)
    transaction.source = TransactionSource(row=row_index)
    return transaction


def _read_csv_transactions(content: str, file_path: Path) -> list[JsonRowType]:
    """Decode a complete Equity Awards CSV into the rows the converter reads.

    The layout states one transaction as a parent row followed by a detail row
    per lot or grant, with the parent columns left blank on the details. Only a
    row blanking every parent column continues the transaction above it, so a
    repeated transaction stays two transactions rather than becoming lots of
    one: nothing in the export says which of those two a repeat is.
    """
    reader = csv.reader(io.StringIO(content))
    header = next(reader, None)
    if header is None or not COMPLETE_CSV_COLUMNS.issubset(header):
        raise ParsingError(
            file_path,
            "Missing columns in Charles Schwab Equity Awards file: "
            f"{sorted(COMPLETE_CSV_COLUMNS.difference(header or []))}. A file "
            "holding Date, Symbol and FairMarketValuePrice is the award-price "
            "file, which prices a vest in a main history rather than listing "
            "transactions; pass it with --schwab-award-file.",
            row_index=1,
        )

    names = FieldNames()
    rows: list[JsonRowType] = []
    # The lots of the transaction being read, and None until one is open.
    lots: list[JsonRowType] | None = None
    for row_index, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(header):
            raise UnexpectedColumnCountError(
                row, len(header), file_path, row_index=row_index
            )

        values = dict(zip(header, row, strict=True))
        # Blank parent columns are kept: the converter reads them by name and
        # already treats an empty field as absent. Blank detail columns are
        # dropped, because the lot helpers test whether a field is there at
        # all, and an empty one would be read as a malformed number instead of
        # a count the export did not state.
        parent = {name: values.get(name, "").strip() for name in PARENT_COLUMNS}
        details = {
            name: value.strip()
            for name, value in values.items()
            if name not in PARENT_COLUMNS and value.strip()
        }

        if any(parent.values()):
            if not parent["Date"] or not parent["Action"]:
                raise ParsingError(
                    file_path,
                    "A Charles Schwab Equity Awards transaction row needs both "
                    f"Date and Action, but this one states {parent['Date'] or 'no date'} "
                    f"and {parent['Action'] or 'no action'}",
                    row_index=row_index,
                )
            if details:
                raise ParsingError(
                    file_path,
                    "This row states a transaction and also holds detail "
                    "fields. A transaction states its lots on the rows below "
                    "it, so this file is not the layout cgt-calc reads",
                    row_index=row_index,
                )
            lots = []
            rows.append(
                {**parent, names.transac_details: lots, CSV_ROW_INDEX: row_index}
            )
            continue

        if lots is None:
            raise ParsingError(
                file_path,
                "Found a detail row before any transaction to attach it to",
                row_index=row_index,
            )
        lots.append({OPTIONAL_DETAILS_NAME: details})

    return rows


class SchwabEquityAwardsParser(BaseSingleFileParser[SchwabAwardTransaction]):
    """Parser for Charles Schwab Equity Awards transaction exports.

    Schwab states one Equity Awards history in two layouts, a JSON export and
    a CSV one. They carry the same fields under the same names, so only the
    decoding differs and everything after it is shared.
    """

    arg_name = "schwab-equity-award"
    pretty_name = "Charles Schwab Equity Awards"
    format_name = "JSON"
    argument_help: ClassVar[str | None] = (
        "Charles Schwab Equity Awards transaction history, JSON or complete "
        "CSV. Prefer --schwab-award-file; this option remains the way to "
        "combine the history with --schwab-file or --schwab-dir"
    )
    deprecated_flags: ClassVar[list[str]] = ["--schwab_equity_award_json"]

    @classmethod
    @override
    def load_from_args(cls, args: argparse.Namespace) -> list[BrokerTransaction]:
        """Load the export, refusing one that can only price vests.

        This option adds transactions and never prices a vest, so an export
        that holds nothing but prices imports nothing through it and produces
        a report of zero gains rather than an error. Only checked when there
        is no main history: with one, `SchwabParser` runs first and an
        unpriced vest raises `Cannot price a vest`, which already names the
        route that reads this file.
        """
        file_path = args.schwab_equity_award_json
        if file_path is None or args.schwab_file or args.schwab_dir:
            return super().load_from_args(args)
        content = read_input(file_path, cls.encoding)
        if lapse_only_rows(content, file_path) is not None:
            raise CgtError(price_only_alone_message(canonical_option=False))
        # list is invariant, so widen explicitly rather than return list[T].
        transactions: list[BrokerTransaction] = list(
            cls.load_from_stream(io.StringIO(content), file_path)
        )
        return transactions

    @classmethod
    @override
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[SchwabAwardTransaction]:
        """Read Schwab Equity Awards transactions from JSON or CSV."""
        # The contents choose the layout, not the filename: users rename these
        # exports, and the name can say JSON where the contents are CSV.
        content = file.read().lstrip("\ufeff \t\r\n")
        fields = FieldNames()
        if content.startswith("{"):
            rows, fields = _read_json_transactions(content, file_path)
        else:
            rows = _read_csv_transactions(content, file_path)

        # Before anything is converted: the split table has to survive the one
        # record that can contradict it.
        _check_lapse_units(rows, file_path, fields)

        transactions = [
            _csv_aware_transaction(transac, file_path, fields)
            for transac in rows
            # Journal and Wire Transfer move cash between accounts.
            #
            # Lapse is the payroll side of a vest and duplicates its Deposit,
            # which already holds the net shares that reach the pool. Its own
            # counts are in the units of the day while its price is restated,
            # so pairing them puts a tenth of the shares in at the full price.
            #
            # Tax Withholding is deliberately not skipped: it is US tax at
            # source on dividends, and it is needed for SA106 and foreign tax
            # credit relief.
            if transac[fields.action] not in DISCARDED_ACTIONS
        ]

        # A purchase whose every share went to tax acquired nothing, so it is
        # not offered as an acquisition of zero, which the calculator rightly
        # refuses. Only that case: a zero that arrives any other way — a vest
        # whose Quantity is blank, say — is a malformed row, and refusing it
        # loudly beats dropping it and reporting a smaller holding.
        transactions = [
            transaction
            for transaction in transactions
            if not transaction.acquired_nothing
        ]

        # And after: each converted disposal against the acquisitions around it.
        _check_disposal_units(transactions, file_path)

        transactions.reverse()
        return transactions


# The class read only JSON when it was named, and the name is on the option,
# in the registry and in every existing caller.
SchwabEquityAwardsJSONParser = SchwabEquityAwardsParser
