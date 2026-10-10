"""Trading 212 parser."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from itertools import combinations
import logging
import math
from pathlib import Path
from typing import ClassVar, Final, NoReturn, TextIO, override

from cgt_calc.const import UK_CURRENCY, UK_TIMEZONE
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    Isin,
    TransactionSource,
)
from cgt_calc.stock_splits import (
    StockSplitEvent,
    StockSplitTransaction,
    UnresolvedRatio,
    quantity_sign,
    recover_ratio,
)
from cgt_calc.util import (
    approx_equal,
    approx_equal_scaled,
    normalize_amount,
    parse_decimal,
)

from .base_parsers import BaseDirParser, read_csv_rows


class Trading212Column(StrEnum):
    """Columns exported in the Trading 212 transaction CSV."""

    ACTION = "Action"
    TIME = "Time"
    TIME_UTC = "Time (UTC)"
    ISIN = "ISIN"
    TICKER = "Ticker"
    NAME = "Name"
    NO_OF_SHARES = "No. of shares"
    PRICE_PER_SHARE = "Price / share"
    CURRENCY_PRICE_PER_SHARE = "Currency (Price / share)"
    EXCHANGE_RATE = "Exchange rate"
    RESULT_GBP = "Result (GBP)"
    RESULT = "Result"
    CURRENCY_RESULT = "Currency (Result)"
    TOTAL_GBP = "Total (GBP)"
    TOTAL = "Total"
    CURRENCY_TOTAL = "Currency (Total)"
    WITHHOLDING_TAX = "Withholding tax"
    CURRENCY_WITHHOLDING_TAX = "Currency (Withholding tax)"
    CHARGE_AMOUNT_GBP = "Charge amount (GBP)"
    CHARGE_AMOUNT = "Charge amount"
    CURRENCY_CHARGE_AMOUNT = "Currency (Charge amount)"
    DEPOSIT_FEE = "Deposit fee"
    CURRENCY_DEPOSIT_FEE = "Currency (Deposit fee)"
    TRANSACTION_FEE_GBP = "Transaction fee (GBP)"
    TRANSACTION_FEE = "Transaction fee"
    FINRA_FEE_GBP = "Finra fee (GBP)"
    FINRA_FEE = "Finra fee"
    STAMP_DUTY_GBP = "Stamp duty (GBP)"
    STAMP_DUTY = "Stamp duty"
    CURRENCY_STAMP_DUTY = "Currency (Stamp duty)"
    STAMP_DUTY_RESERVE_TAX_GBP = "Stamp duty reserve tax (GBP)"
    STAMP_DUTY_RESERVE_TAX = "Stamp duty reserve tax"
    CURRENCY_STAMP_DUTY_RESERVE_TAX = "Currency (Stamp duty reserve tax)"
    FRENCH_TRANSACTION_TAX = "French transaction tax"
    CURRENCY_FRENCH_TRANSACTION_TAX = "Currency (French transaction tax)"
    NOTES = "Notes"
    TRANSACTION_ID = "ID"
    CURRENCY_CONVERSION_FEE_GBP = "Currency conversion fee (GBP)"
    CURRENCY_CONVERSION_FEE = "Currency conversion fee"
    CURRENCY_CURRENCY_CONVERSION_FEE = "Currency (Currency conversion fee)"
    CURRENCY_CONVERSION_FROM_AMOUNT = "Currency conversion from amount"
    CURRENCY_CURRENCY_CONVERSION_FROM_AMOUNT = (
        "Currency (Currency conversion from amount)"
    )
    CURRENCY_CONVERSION_TO_AMOUNT = "Currency conversion to amount"
    CURRENCY_CURRENCY_CONVERSION_TO_AMOUNT = "Currency (Currency conversion to amount)"
    CURRENCY_TRANSACTION_FEE = "Currency (Transaction fee)"
    CURRENCY_FINRA_FEE = "Currency (Finra fee)"
    MERCHANT_CATEGORY = "Merchant category"
    MERCHANT_NAME = "Merchant name"


COLUMN_SET: Final[set[str]] = {column.value for column in Trading212Column}
LOGGER = logging.getLogger(__name__)

# A share reorganisation is exported as two rows: the position before it and
# the position after it, in either order. These are values in the Action
# column, not columns of their own, so they are deliberately not part of
# Trading212Column: adding them there would make _validate_header accept a
# column literally named "Stock split open".
SPLIT_OPEN_ACTION: Final = "Stock split open"
SPLIT_CLOSE_ACTION: Final = "Stock split close"
SPLIT_ACTIONS: Final = frozenset({SPLIT_OPEN_ACTION, SPLIT_CLOSE_ACTION})

CONVERSION_ACTION: Final = "Currency conversion"

# Actions the Trading 212 page lists under its known limitations: share
# distributions, and transfers of shares between accounts. A distribution
# may be a stock dividend, a warrant issue, one side of a demerger or the
# shares received in a takeover. Those are not all taxed alike, and the row
# does not say which it is.
UNSUPPORTED_ACTIONS: Final = frozenset(
    {
        "Stock distribution",
        "Custom stock distribution",
        "Transfer in",
        "Transfer out",
    }
)

# Two rounded account-currency totals, so the only error between them is the
# rounding itself.
TOTAL_ABS_TOLERANCE: Final = Decimal("0.01")

# Trading 212 prints a total to two decimal places in every currency.
TOTAL_UNIT: Final = Decimal("0.01")
# The fewest decimal places it prints a price and an exchange rate to. A figure
# with fewer has only lost trailing zeros to a spreadsheet or to typing, and is
# as precise as before.
PRICE_UNIT: Final = Decimal("0.01")
RATE_UNIT: Final = Decimal("0.00001")

# A value rebuilt from an exported price and exchange rate misses by a
# proportion of itself rather than by a fixed step, so this bound has to be
# relative. Measured across the fourteen rows of seven real reorganisations,
# the worst disagreement between the two halves of a pair is 1.8e-9 of the
# value, which this leaves about fifty times over. A fixed bound near the
# absolute figures those events show (up to 5.9e-8) is fitted to holdings
# worth a few hundred pounds and rejects ordinary ones: at 1.8e-9 a GBP
# 100,000 position already differs by 1.8e-4.
VALUE_REL_TOLERANCE: Final = Decimal("1E-7")

# A reorganisation is two rows: the position before it and the one after.
HALVES_PER_SPLIT: Final = 2

# The two halves are stamped at the same instant or milliseconds apart.
SPLIT_PAIR_MAX_GAP: Final = timedelta(seconds=1)

# How far apart two rows can be and still be shown to each other as the
# reorganisation the reader was looking for. Deliberately wider than
# SPLIT_PAIR_MAX_GAP: a pair that misses the gap has to reach the check that
# names the gap, or it is reported as a half whose partner is missing while
# the partner sits a second away in the same file.
SPLIT_CANDIDATE_WINDOW: Final = timedelta(minutes=1)

# Fields a reorganisation cannot carry. It moves no money and realises no
# gain, so anything here means the row is not the reorganisation it claims to
# be. A currency beside one of these cannot mislead: the amount is zero.
SPLIT_ZERO_COLUMNS: Final[tuple[Trading212Column, ...]] = (
    Trading212Column.RESULT,
    Trading212Column.RESULT_GBP,
    Trading212Column.WITHHOLDING_TAX,
    Trading212Column.CHARGE_AMOUNT,
    Trading212Column.CHARGE_AMOUNT_GBP,
    Trading212Column.DEPOSIT_FEE,
    Trading212Column.TRANSACTION_FEE,
    Trading212Column.TRANSACTION_FEE_GBP,
    Trading212Column.FINRA_FEE,
    Trading212Column.FINRA_FEE_GBP,
    Trading212Column.STAMP_DUTY,
    Trading212Column.STAMP_DUTY_GBP,
    Trading212Column.STAMP_DUTY_RESERVE_TAX,
    Trading212Column.STAMP_DUTY_RESERVE_TAX_GBP,
    Trading212Column.FRENCH_TRANSACTION_TAX,
    Trading212Column.CURRENCY_CONVERSION_FEE,
    Trading212Column.CURRENCY_CONVERSION_FEE_GBP,
    Trading212Column.CURRENCY_CONVERSION_FROM_AMOUNT,
    Trading212Column.CURRENCY_CONVERSION_TO_AMOUNT,
)


# The amount a currency conversion took from one currency and the amount it
# added to another, each with the column naming its currency.
CONVERSION_COLUMNS: Final = (
    (
        Trading212Column.CURRENCY_CONVERSION_FROM_AMOUNT,
        Trading212Column.CURRENCY_CURRENCY_CONVERSION_FROM_AMOUNT,
    ),
    (
        Trading212Column.CURRENCY_CONVERSION_TO_AMOUNT,
        Trading212Column.CURRENCY_CURRENCY_CONVERSION_TO_AMOUNT,
    ),
)


@dataclass(frozen=True)
class FeeColumn:
    """How to read one group of Trading 212 fee columns.

    Broker fees with a blank currency are ambiguous so they must state
    one (`currency_required`); statutory taxes tolerate a blank currency
    (folded into the transaction currency) since some exports leave it
    empty.
    """

    attribute: str
    gbp_column: Trading212Column | None
    bare_column: Trading212Column
    currency_column: Trading212Column
    currency_required: bool


# French transaction tax counts as stamp duty since both are statutory
# transaction taxes that increase the cost basis.
FEE_COLUMNS: Final[list[FeeColumn]] = [
    FeeColumn(
        attribute="transaction_fee",
        gbp_column=Trading212Column.TRANSACTION_FEE_GBP,
        bare_column=Trading212Column.TRANSACTION_FEE,
        currency_column=Trading212Column.CURRENCY_TRANSACTION_FEE,
        currency_required=True,
    ),
    FeeColumn(
        attribute="finra_fee",
        gbp_column=Trading212Column.FINRA_FEE_GBP,
        bare_column=Trading212Column.FINRA_FEE,
        currency_column=Trading212Column.CURRENCY_FINRA_FEE,
        currency_required=True,
    ),
    FeeColumn(
        attribute="stamp_duty",
        gbp_column=Trading212Column.STAMP_DUTY_GBP,
        bare_column=Trading212Column.STAMP_DUTY,
        currency_column=Trading212Column.CURRENCY_STAMP_DUTY,
        currency_required=False,
    ),
    FeeColumn(
        attribute="stamp_duty",
        gbp_column=Trading212Column.STAMP_DUTY_RESERVE_TAX_GBP,
        bare_column=Trading212Column.STAMP_DUTY_RESERVE_TAX,
        currency_column=Trading212Column.CURRENCY_STAMP_DUTY_RESERVE_TAX,
        currency_required=False,
    ),
    FeeColumn(
        attribute="stamp_duty",
        gbp_column=None,
        bare_column=Trading212Column.FRENCH_TRANSACTION_TAX,
        currency_column=Trading212Column.CURRENCY_FRENCH_TRANSACTION_TAX,
        currency_required=False,
    ),
    FeeColumn(
        attribute="conversion_fee",
        gbp_column=Trading212Column.CURRENCY_CONVERSION_FEE_GBP,
        bare_column=Trading212Column.CURRENCY_CONVERSION_FEE,
        currency_column=Trading212Column.CURRENCY_CURRENCY_CONVERSION_FEE,
        currency_required=True,
    ),
]


def decimal_or_none(
    row: dict[Trading212Column, str], column: Trading212Column
) -> Decimal | None:
    """Convert a column value to Decimal.

    Return None when the value is blank, missing, or "Not available".
    """

    value = row.get(column)
    if value is None or value in {"", "Not available"}:
        return None
    return parse_decimal(value, column.value)


def last_digit(value: Decimal, coarsest: Decimal) -> Decimal:
    """Return one unit of the last digit an exported figure is printed to.

    Never more than `coarsest`, the unit Trading 212 prints that figure to at
    the least.
    """
    exponent = value.as_tuple().exponent
    assert isinstance(exponent, int), "an exported figure is a finite number"
    return min(Decimal(1).scaleb(exponent), coarsest)


def datetime_from_str(value: str) -> datetime:
    """Convert a timestamp to an aware UTC datetime.

    Exports have used whole seconds and milliseconds, and the column
    renamed to "Time (UTC)" may spell the zone out. Every export states
    its times in UTC, so a value without a zone is read as UTC too.
    """

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise ValueError(f"Invalid timestamp: {value!r}") from err
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def action_from_str(label: str, file: Path) -> ActionType:
    """Convert label to ActionType."""
    if label in {
        "Market buy",
        "Limit buy",
        "Stop buy",
        "Stop limit buy",
    }:
        return ActionType.BUY

    if label in {
        "Market sell",
        "Limit sell",
        "Stop sell",
        "Stop limit sell",
    }:
        return ActionType.SELL

    if label in {
        "Card credit",
        "Card debit",
        "Card refund",
        "Deposit",
        "Withdrawal",
    }:
        return ActionType.TRANSFER

    if label in {
        "Dividend (Ordinary)",
        "Dividend (Dividend)",
        "Dividend (Dividends paid by us corporations)",
        "Dividend (Dividends paid by foreign corporations)",
        "Dividend (Dividend manufactured payment)",
        "Dividend (Property income distribution)",
        # Trading 212 records no tax withheld on these rows. That says nothing
        # about whether the payment is income: real exports use the label for
        # ordinary dividends and for repayments of share premium, which the
        # page tells the user to check.
        "Dividend (Tax exempted)",
        "Dividend adjustment",
    }:
        return ActionType.DIVIDEND

    if label in {
        "Interest on cash",
        "Lending interest",
        # Interest distributions from bond and money market funds,
        # taxed as savings income rather than dividends.
        "Dividend (Interest)",
    }:
        return ActionType.INTEREST

    if label == "Stock Split":
        return ActionType.STOCK_SPLIT

    if label in SPLIT_ACTIONS:
        # Half of a reorganisation. The two halves are combined into one
        # event once the whole directory has been read; a half that never
        # finds its partner is refused there.
        return ActionType.STOCK_SPLIT

    if label == "Spin off":
        return ActionType.SPIN_OFF

    if label in {
        CONVERSION_ACTION,
        "Result adjustment",
        "Spending cashback",
    }:
        return ActionType.ADJUSTMENT

    if label in UNSUPPORTED_ACTIONS:
        raise ParsingError(
            file,
            f"{label} rows are not supported. See "
            "https://cgt-calc.uk/brokers/trading212/#known-limitations",
        )

    raise ParsingError(file, f"Unknown action: {label}")


class Trading212Transaction(BrokerTransaction):
    """Represents a single Trading 212 transaction."""

    def __init__(self, header: list[str], row_raw: list[str], file: Path) -> None:
        """Create transaction from CSV row."""
        if len(row_raw) != len(header):
            raise UnexpectedColumnCountError(row_raw, len(header), file)

        row: dict[Trading212Column, str] = {
            Trading212Column(column): value
            for column, value in zip(header, row_raw, strict=False)
        }

        # Older exports call the column "Time", newer ones "Time (UTC)".
        time_str = row.get(Trading212Column.TIME) or row.get(Trading212Column.TIME_UTC)
        if not time_str:
            raise ValueError(
                f"Missing {Trading212Column.TIME.value} "
                f"or {Trading212Column.TIME_UTC.value}"
            )
        self.datetime = datetime_from_str(time_str)
        # The instant is kept in UTC for ordering, but the date that drives
        # the tax year and the matching rules is the UK one.
        date = self.datetime.astimezone(UK_TIMEZONE).date()
        self.raw_action = row[Trading212Column.ACTION]
        action = action_from_str(self.raw_action, file)
        self.converted = self._converted(row)

        symbol = row[Trading212Column.TICKER] or None
        description = row[Trading212Column.NAME]

        quantity = decimal_or_none(row, Trading212Column.NO_OF_SHARES)
        self.price_foreign = decimal_or_none(row, Trading212Column.PRICE_PER_SHARE)
        self.currency_foreign = row[Trading212Column.CURRENCY_PRICE_PER_SHARE]
        self.exchange_rate = decimal_or_none(row, Trading212Column.EXCHANGE_RATE)

        if Trading212Column.TOTAL in row:
            amount = decimal_or_none(row, Trading212Column.TOTAL)
            currency_raw = row[Trading212Column.CURRENCY_TOTAL]
            # The total of a currency conversion is its fee, in the currency
            # received. With no fee the total is zero, and Trading 212 leaves
            # its currency blank.
            if self.converted and amount == 0 and not currency_raw:
                currency_raw = self.converted[-1][1]
            currency = CurrencyCode(currency_raw)
        else:
            amount = decimal_or_none(row, Trading212Column.TOTAL_GBP)
            currency = CurrencyCode("GBP")

        if (
            amount is not None
            and (action == ActionType.BUY or self.raw_action == "Withdrawal")
            and amount > 0
        ):
            amount *= -1

        # Trading 212 exports the shares given up in a takeover paid in shares
        # as a sell at a price and a total of zero. Nothing was sold, and read
        # as a sale the row would report the whole cost as a loss. Both must be
        # zero: a price of zero with a real total is a share priced below a cent,
        # and a total of zero with a real price is the sale of a tiny fraction.
        if action is ActionType.SELL and amount == 0 and self.price_foreign == 0:
            raise ValueError(
                f"A sale of {symbol} at a price and a total of zero cannot be read "
                "as a sale. Trading 212 exports the shares given up in a takeover "
                "paid in shares this way, and reading the row as a sale would "
                "report their whole cost as a loss. See "
                "https://cgt-calc.uk/brokers/trading212/#known-limitations"
            )

        self.transaction_fee = Decimal(0)
        self.finra_fee = Decimal(0)
        self.stamp_duty = Decimal(0)
        self.conversion_fee = Decimal(0)
        foreign_fees: dict[CurrencyCode, Decimal] = {}
        for attr, fee_amount, fee_currency in self._read_fees(row):
            if fee_currency is None or fee_currency == currency:
                setattr(self, attr, getattr(self, attr) + fee_amount)
            else:
                fee_code = CurrencyCode(fee_currency)
                foreign_fees[fee_code] = (
                    foreign_fees.get(fee_code, Decimal(0)) + fee_amount
                )

        fees = (
            self.transaction_fee
            + self.finra_fee
            + self.conversion_fee
            + self.stamp_duty
        )

        # With fees pending conversion the price below is provisional;
        # the calculation engine re-derives it after folding them in.
        # A zero count leaves no price to derive, and dividing by it raises
        # before the row can be refused with something a reader can act on.
        price = (
            abs(amount + fees) / quantity if amount is not None and quantity else None
        )

        # Shares times price, converted and with the fees, should give the
        # total. Every figure in the row is printed to so many digits, so the
        # two may differ by one unit of the last digit of the total, of the
        # price for every share, and of the exchange rate across the whole
        # value. A fixed allowance per share warned on small purchases of a
        # share priced in pence, where a penny of rounding in the total is a
        # large part of a penny a share. One unit and not half: the eight-place
        # exchange rate of a current export is cut off, not rounded, and real
        # rows are out by a whole unit of its last digit.
        # A fee in the total's currency gets no allowance: the total is the
        # rounded value and the rounded fee, so taking the fee off leaves the
        # rounded value, as it does in every real row with a conversion fee,
        # stamp duty, stamp duty reserve tax or French transaction tax. A fee
        # in the price's currency is converted here at the row's rate, which
        # may not be how it reached the total. No real row has one, so such a
        # row is allowed a unit of the total: enough for three fees that were
        # each rounded to a penny on their own.
        # Not on a dividend row of any kind: its price per share is after
        # foreign tax, and the exchange rate a current export gives on it runs
        # the other way.
        if (
            amount is not None
            and quantity
            and self.price_foreign is not None
            and not self.raw_action.startswith("Dividend")
            and (self.currency_foreign == "GBP" or self.exchange_rate is not None)
        ):
            exchange_rate = self.exchange_rate or Decimal(1)
            check_fees = self._checkable_fees(fees, foreign_fees, exchange_rate)
            if check_fees is not None:
                price_unit = last_digit(self.price_foreign, PRICE_UNIT)
                implied = quantity * self.price_foreign / exchange_rate
                rounding = TOTAL_UNIT + quantity * price_unit / exchange_rate
                # A price in the total's own currency is not converted, so its
                # exchange rate of one is exact however it is printed.
                if self.exchange_rate is not None and self.currency_foreign != currency:
                    rate_unit = last_digit(self.exchange_rate, RATE_UNIT)
                    rounding += implied * rate_unit / exchange_rate
                if foreign_fees:
                    rounding += TOTAL_UNIT
                gap = abs(amount + check_fees) - implied
                if abs(gap) > rounding:
                    total = abs(amount)
                    LOGGER.warning(
                        "The total of the %s %s on %s is %s %s, but its shares, "
                        "price, exchange rate and fees come to %s %s. Check the "
                        "transaction in Trading 212 before relying on the report.",
                        symbol,
                        self.raw_action,
                        date,
                        total,
                        currency,
                        (total - gap).quantize(TOTAL_UNIT),
                        currency,
                    )

        # A dividend is recorded before foreign tax once overlapping exports are
        # merged (finalize_transactions): the merge does not compare the price
        # per share, so the amount it compares must not depend on it.
        self.withholding_tax = decimal_or_none(row, Trading212Column.WITHHOLDING_TAX)
        self.withholding_currency = (
            row.get(Trading212Column.CURRENCY_WITHHOLDING_TAX) or None
        )

        isin_raw = row[Trading212Column.ISIN]
        isin = Isin(isin_raw) if isin_raw else None
        # An export without the ID column gives None; one with the column
        # but no value gives "". Both mean "no identity", so they must not
        # be told apart when matching rows across exports.
        # Every instant an export stated for this transaction. A merge keeps
        # one copy of a row two exports both report, and the copies can
        # disagree below the second. The chronology a reorganisation is
        # checked against has to see all of them: keeping only the survivor's
        # would let one export's stamp erase another's evidence that a trade
        # fell at or between the two halves.
        self.stated_instants: frozenset[datetime] = frozenset({self.datetime})

        transaction_id = (row.get(Trading212Column.TRANSACTION_ID) or "").strip()
        self.transaction_id = transaction_id or None
        self.notes = row.get(Trading212Column.NOTES)
        # The row as it was exported. Pairing reads it to prove that a
        # reorganisation row fills nothing it should not. Deduplication does
        # not: it groups by the recorded transaction content instead, because
        # Trading 212 reuses ids and its exports have changed columns more
        # than once, so neither the id nor the raw cells identify a
        # transaction across exports.
        self.exported_row = row
        broker = "Trading212"
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
            isin,
            foreign_fees=foreign_fees,
        )

    def _converted(
        self, row: dict[Trading212Column, str]
    ) -> list[tuple[Decimal, CurrencyCode]]:
        """Return the cash a currency conversion moved: out of one currency, into another.

        The row's total is only the fee: the amount received arrives in full
        and the fee is charged on top of it.
        """
        if self.raw_action != CONVERSION_ACTION:
            return []
        moved: list[tuple[Decimal, CurrencyCode]] = []
        for sign, (amount_column, currency_column) in zip(
            (-1, 1), CONVERSION_COLUMNS, strict=True
        ):
            amount = decimal_or_none(row, amount_column)
            currency = row.get(currency_column)
            # A conversion too small to show on one side prints 0.00 there.
            if amount is None or amount < 0 or not currency:
                raise ValueError(
                    "A currency conversion has to give the amount converted from "
                    "and the amount converted to, each with its currency and "
                    "neither below zero. Check this row against Trading 212 and "
                    "export it again."
                )
            moved.append((sign * amount, CurrencyCode(currency)))
        return moved

    def conversion_cash(self) -> list[BrokerTransaction]:
        """Return the cash this conversion moved, as one row for each currency."""
        rows = []
        for amount, currency in self.converted:
            row = BrokerTransaction(
                date=self.date,
                action=ActionType.ADJUSTMENT,
                symbol=None,
                # The export names no security, so a message about the balance
                # would otherwise show an adjustment with nothing to say what.
                description=CONVERSION_ACTION,
                quantity=None,
                price=None,
                fees=Decimal(0),
                amount=amount,
                currency=currency,
                broker=self.broker,
            )
            row.source = self.source
            rows.append(row)
        return rows

    def separate_foreign_tax(self) -> BrokerTransaction | None:
        """Record this dividend before foreign tax; return the tax as its own row.

        The tax is read once: afterwards the amount includes it.
        """
        foreign_tax = self._foreign_tax()
        self.withholding_tax = None
        if foreign_tax is None:
            return None
        assert self.amount is not None, "a dividend with tax has an amount"
        self.amount += foreign_tax
        if self.quantity:
            self.price = abs(self.amount + self.fees) / self.quantity
        tax = BrokerTransaction(
            date=self.date,
            action=ActionType.DIVIDEND_TAX,
            symbol=self.symbol,
            description=self.description,
            quantity=None,
            price=None,
            fees=Decimal(0),
            amount=-foreign_tax,
            currency=self.currency,
            broker=self.broker,
            isin=self.isin,
        )
        tax.source = self.source
        return tax

    def _foreign_tax(self) -> Decimal | None:
        """Return the foreign tax withheld from this dividend, in the account currency.

        The row gives the amount received after the tax in the account's
        currency. Tax already in that currency is used as it is.

        Tax in the share's currency is converted with the row's own figures.
        The price per share is after the tax too (it is the declared dividend
        less the tax in every real export checked, from 2020 to 2026, #709
        among them), so shares times price is the amount received in the
        share's currency, and the tax is the same share of one as of the
        other. The row's exchange rate is not used: exports made before late
        2025 give none on a dividend, and it adds nothing to these figures.

        Tax in pounds or pence stays in the amount received, whatever the
        account's currency: it may be UK tax, for example on a property income
        distribution, which is not foreign tax. So does tax with no currency
        given, which an export means as the account's currency. So does the tax
        on a row that takes a dividend back: treaty relief is worked out per
        payment, so converting the reversal would cancel the dividend but leave
        the original's relief standing.
        """
        tax = self.withholding_tax
        if (
            self.action is not ActionType.DIVIDEND
            or self.amount is None
            or not tax
            or self.withholding_currency in {None, UK_CURRENCY, "GBX"}
        ):
            return None
        if self.amount > 0 and tax > 0:
            if self.withholding_currency == self.currency:
                return tax
            received = (self.quantity or Decimal(0)) * (
                self.price_foreign or Decimal(0)
            )
            if self.withholding_currency == self.currency_foreign and received > 0:
                return normalize_amount(tax * self.amount / received)
        if self.amount < 0 and tax < 0:
            LOGGER.warning(
                "The %s dividend taken back on %s is recorded at the %s %s taken "
                "back, and its %s %s of tax is not converted: the original "
                "dividend's tax at source and any treaty relief on it stay in the "
                "report",
                self.symbol,
                self.date,
                -self.amount,
                self.currency,
                -tax,
                self.withholding_currency,
            )
            return None
        LOGGER.warning(
            "The %s dividend on %s is recorded at the %s %s received, without the "
            "%s %s of tax withheld, which cannot be converted from this row",
            self.symbol,
            self.date,
            self.amount,
            self.currency,
            tax,
            self.withholding_currency,
        )
        return None

    def _checkable_fees(
        self,
        fees: Decimal,
        foreign_fees: dict[CurrencyCode, Decimal],
        exchange_rate: Decimal,
    ) -> Decimal | None:
        """Total fees to add back for the price consistency check.

        Foreign amounts in the instrument currency are converted with the
        export's own exchange rate. Returns None when one is in some other
        currency, which the export alone cannot convert.
        """
        total = fees
        for fee_currency, fee_amount in foreign_fees.items():
            if fee_currency != self.currency_foreign:
                return None
            total += fee_amount / exchange_rate
        return total

    @staticmethod
    def _read_fees(
        row: dict[Trading212Column, str],
    ) -> list[tuple[str, Decimal, str | None]]:
        """Read fee columns as (attribute, amount, currency) entries.

        A None currency means the export left it blank; only statutory
        taxes tolerate that (treated as the transaction currency).
        """
        entries: list[tuple[str, Decimal, str | None]] = []
        for fee_column in FEE_COLUMNS:
            if fee_column.gbp_column is not None:
                gbp_amount = decimal_or_none(row, fee_column.gbp_column) or Decimal(0)
                if gbp_amount:
                    entries.append((fee_column.attribute, gbp_amount, "GBP"))
            bare_amount = decimal_or_none(row, fee_column.bare_column) or Decimal(0)
            if bare_amount > 0:
                fee_currency = row.get(fee_column.currency_column) or None
                if fee_currency is None and fee_column.currency_required:
                    raise ValueError(
                        f"Missing currency for {fee_column.bare_column.value}"
                    )
                entries.append((fee_column.attribute, bare_amount, fee_currency))
        return entries

    @property
    def is_split_half(self) -> bool:
        """Whether this row is one half of a share reorganisation."""
        return self.raw_action in SPLIT_ACTIONS

    @property
    def is_reorganisation(self) -> bool:
        """Whether this row states a share reorganisation, in either form."""
        return self.action is ActionType.STOCK_SPLIT

    def recorded_fields(self) -> tuple[object, ...]:
        """Return the exported transaction content used to merge overlaps.

        Two rows sharing this are candidates for being one transaction that
        two overlapping exports both report. The fields are listed rather
        than read from `dataclasses.fields`, so that adding a field to the
        model cannot silently change what counts as the same transaction.

        The timestamp is truncated to a whole second. Older exports state
        seconds and newer ones milliseconds, so comparing the stated instant
        would keep both copies of every row in an overlap that spans that
        change. Comparing the aware UTC value is also what lets the `Time`
        and `Time (UTC)` columns meet. `date` derives from the same value, so
        it needs no entry of its own.

        The ID is deliberately absent: Trading 212 reuses one ID across a buy
        and the sell that closes it, so an ID tells rows apart within a group
        rather than forming one.

        So are `price_foreign`, `currency_foreign`, `exchange_rate`, the
        withholding tax and `notes`. Keeping them would block a merge whenever
        two exports differ only in presentation. Only a dividend's foreign tax
        is worked out, from the price and the withholding tax, after the merge,
        so two copies that round the price differently can give slightly
        different tax depending on which copy is kept, enough for the treaty
        check to fail on one of them.

        Reorganisation rows are the exception on both counts, and take the
        exact instant and those figures back. See below.
        """
        recorded = (
            self.datetime.replace(microsecond=0),
            self.action,
            self.raw_action,
            self.symbol,
            self.description,
            self.quantity,
            self.price,
            self.fees,
            self.amount,
            self.currency,
            self.broker,
            self.isin,
            tuple(sorted(self.foreign_fees.items())),
            self.ambiguous_quantity,
            # The cash a conversion moved is read from these, not from its total.
            tuple(self.converted),
        )
        if not self.is_reorganisation:
            return recorded
        # A reorganisation is proved by rows read together, and the checks
        # that protect it read more than the content above: pairing the two
        # halves compares the figures below, and a trade is refused for being
        # stamped at or between the halves, which turns on their exact instants.
        # Merging two rows that differ in any of that would let one export's
        # account of the event stand in for another's, and the evidence that
        # made the other refuse would go with it. So a reorganisation row
        # merges only with one that states the event identically.
        return (
            *recorded,
            self.datetime,
            self.price_foreign,
            self.currency_foreign,
            self.exchange_rate,
        )

    def where(self) -> str:
        """Name the file and line this row came from."""
        if self.source is None or self.source.file is None:
            return f"{self.raw_action} on {self.date}"
        return f"{self.source.file}, row {self.source.row}"


def _as_trading212(transaction: BrokerTransaction) -> Trading212Transaction:
    """Narrow a transaction this parser read from a Trading 212 export.

    Checked rather than asserted: `python -O` drops an assert, and what
    follows one here is an attribute only this class has, so the invariant
    would fail as an `AttributeError` from somewhere else entirely.
    """
    if not isinstance(transaction, Trading212Transaction):
        raise TypeError(
            f"{type(transaction).__name__} was not read from a Trading 212 export"
        )
    return transaction


@dataclass(frozen=True)
class SplitHalf:
    """One row of a share reorganisation, with its fields checked.

    `close` states the complete position before the event and `open` the
    complete position after it. Both restate one holding, so both carry the
    same value; nothing here is proceeds, expenditure or a new cost basis.
    """

    transaction: Trading212Transaction
    # The holding this row restates. Kept here rather than read back off the
    # transaction, where it is still `str | None`: `_read_split_half` refuses
    # a blank ticker, and carrying the proven value spares everything
    # downstream a check for a state that cannot reach it.
    symbol: str
    quantity: Decimal
    price: Decimal
    price_currency: str
    total: Decimal
    exchange_rate: Decimal

    @property
    def is_open(self) -> bool:
        """Whether this is the position after the event."""
        return self.transaction.raw_action == SPLIT_OPEN_ACTION

    @property
    def value(self) -> Decimal:
        """The position's value in the account currency.

        Each half is divided by its own exchange rate. The two halves of a
        pair can carry different rates, and because Trading 212 derives both
        rows from one account-currency total, those per-row rates are exactly
        what makes the two values agree. Pairing them the wrong way round
        inflates the disagreement a thousandfold and makes a sound tolerance
        look far too tight.
        """
        return self.quantity * self.price / self.exchange_rate


def _split_error(transaction: Trading212Transaction, message: str) -> ParsingError:
    """Build a parsing error pointing at the row that caused it."""
    source = transaction.source
    file = source.file if source is not None and source.file is not None else Path()
    row = source.row if source is not None else None
    return ParsingError(file, message, row_index=row)


def _exported_time(transaction: Trading212Transaction) -> str:
    """Return the timestamp spelling used by this export row."""
    row = transaction.exported_row
    return (
        row.get(Trading212Column.TIME)
        or row.get(Trading212Column.TIME_UTC)
        or transaction.datetime.isoformat()
    )


def _stated_times(transaction: Trading212Transaction) -> str:
    """Return every instant the exports stated for this row.

    A merged row usually states one, and then this reads as the export
    wrote it. Where two exports disagreed below the second, naming only the
    survivor's would point the reader at a stamp that is not the one the
    refusal turned on.
    """
    exported = _exported_time(transaction)
    if len(transaction.stated_instants) <= 1:
        return exported
    others = sorted(
        instant.isoformat()
        for instant in transaction.stated_instants
        if instant != transaction.datetime
    )
    return f"{exported}, also reported as {', '.join(others)}"


def _describe_half(half: SplitHalf) -> str:
    """Describe one pairing candidate with its source and checked fields."""
    transaction = half.transaction
    return (
        f"{transaction.raw_action} at {transaction.where()} "
        f"(Ticker={half.symbol!r}, No. of shares={half.quantity}, "
        f"Price / share={half.price} {half.price_currency}, "
        f"Exchange rate={half.exchange_rate}, Total={half.total} "
        f"{transaction.currency}, Time={_exported_time(transaction)})"
    )


def _split_period(halves: list[SplitHalf]) -> str:
    """Name every UK date covered by a set of pairing rows."""
    days = sorted({half.transaction.date for half in halves})
    return str(days[0]) if len(days) == 1 else f"{days[0]} through {days[-1]}"


def _split_pair_remedy(day: object) -> str:
    """Tell a user how to replace ambiguous or incomplete pairing evidence.

    The second sentence is not padding. A RAW `STOCK_SPLIT` row is what
    settles every other unprovable reorganisation, and it cannot settle this
    one: the refusal happens while the CSV is read, before the calculator
    assembles the day and reaches the RAW row that would override it. Saying
    only "re-export" leaves a reader whose export is already complete with
    nothing to do but edit the file, which the sentence before forbids.
    """
    return (
        "Check the named fields and rows against Trading 212, then replace "
        f"these files with one complete export covering {day}; do not delete "
        "or edit a row merely to force a match. If a complete export still "
        "states this, cgt-calc cannot work the day out and a RAW STOCK_SPLIT "
        "row cannot override it: leave this account out and work the day out "
        "by hand (consider professional advice)."
    )


def _check_split_has_no_money(transaction: Trading212Transaction) -> None:
    """Refuse financial evidence that contradicts a share reorganisation."""
    row = transaction.exported_row
    for column in SPLIT_ZERO_COLUMNS:
        # Several of these columns are read nowhere else, so this is their
        # first parse -- and it happens long after `read_transactions` has
        # stopped wrapping a bad cell in a `ParsingError`. Left bare, an
        # unreadable one reaches the user as "Unexpected error!" and a
        # traceback with no file and no row.
        try:
            value = decimal_or_none(row, column)
        except ValueError as err:
            raise _split_error(transaction, str(err)) from err
        if value is not None and (not value.is_finite() or value != 0):
            raise _split_error(
                transaction,
                f"{transaction.raw_action} states {column.value} of {value}. A "
                "share reorganisation moves no money and realises nothing, so "
                "this row is not one, or it is not supported. Check this row "
                "against Trading 212 and replace the export with a complete "
                f"one covering {transaction.date}.",
            )


def _read_split_half(transaction: Trading212Transaction) -> SplitHalf:
    """Check one reorganisation row and read the fields pairing needs."""
    symbol = transaction.symbol
    if not symbol:
        raise _split_error(
            transaction,
            f"{transaction.raw_action} does not say which holding it restates: "
            "the ticker is blank.",
        )
    quantity = transaction.quantity
    if quantity is None or not quantity.is_finite() or quantity <= 0:
        raise _split_error(
            transaction, f"{transaction.raw_action} needs a positive share count"
        )
    price = transaction.price_foreign
    if price is None or not price.is_finite() or price <= 0:
        raise _split_error(
            transaction, f"{transaction.raw_action} needs a positive price per share"
        )
    # A price is a number of some currency, and pairing multiplies it out
    # into a position value that is then compared against another row's.
    # An exchange rate does not supply the missing currency: it says how many
    # of one buy the other, not which two those are. Left unchecked, a rate
    # of 1 was enough to let two halves reconcile on figures neither row said
    # the currency of.
    if transaction.currency_foreign in {"", "Not available"}:
        raise _split_error(
            transaction,
            f"{transaction.raw_action} states a price of {price} but leaves "
            f"{Trading212Column.CURRENCY_PRICE_PER_SHARE.value} "
            f"{transaction.currency_foreign!r}. A price with no currency "
            "cannot be checked against the total. Re-export the range "
            f"covering {transaction.date} with every column included.",
        )
    total = transaction.amount
    # Zero is allowed: a small enough position rounds to nothing in the
    # account currency. Negative is not; the position has a value.
    if total is None or not total.is_finite() or total < 0:
        raise _split_error(
            transaction,
            f"{transaction.raw_action} needs a total of zero or more, in the "
            "account currency",
        )
    rate = transaction.exchange_rate
    if rate is None:
        if transaction.currency_foreign != transaction.currency:
            raise _split_error(
                transaction,
                f"{transaction.raw_action} is priced in "
                f"{transaction.currency_foreign} and totalled in "
                f"{transaction.currency} but states no exchange rate",
            )
        rate = Decimal(1)
    elif not rate.is_finite() or rate <= 0:
        raise _split_error(
            transaction, f"{transaction.raw_action} needs a positive exchange rate"
        )
    _check_split_has_no_money(transaction)
    return SplitHalf(
        transaction=transaction,
        symbol=symbol,
        quantity=quantity,
        price=price,
        price_currency=transaction.currency_foreign,
        total=total,
        exchange_rate=rate,
    )


def _half_incompatibilities(open_half: SplitHalf, close_half: SplitHalf) -> list[str]:
    """Name every check that prevents two rows forming one reorganisation.

    A differing identifier is deliberately not tested here. Two rows for one
    ticker on one day under two identifiers are a reorganisation that changes
    the security as well, and saying so is more use than leaving them
    unpaired for no stated reason.
    """
    issues: list[str] = []
    gap = open_half.transaction.datetime - close_half.transaction.datetime
    if not timedelta(0) <= gap <= SPLIT_PAIR_MAX_GAP:
        issues.append(
            f"Time: {SPLIT_CLOSE_ACTION}="
            f"{_exported_time(close_half.transaction)}; {SPLIT_OPEN_ACTION}="
            f"{_exported_time(open_half.transaction)} (the open must be from "
            "zero to one second after the close)"
        )
    # Two already-rounded account-currency figures, so no relative part.
    if not approx_equal(open_half.total, close_half.total, TOTAL_ABS_TOLERANCE):
        issues.append(
            f"Total: {SPLIT_CLOSE_ACTION}={close_half.total}; "
            f"{SPLIT_OPEN_ACTION}={open_half.total}"
        )
    issues.extend(
        (
            f"{half.transaction.raw_action} calculated position value="
            f"{half.value} {half.transaction.currency}; Total={half.total} "
            f"{half.transaction.currency}"
        )
        for half in (open_half, close_half)
        if not approx_equal_scaled(
            half.value,
            half.total,
            reference=half.total,
            rel_tolerance=VALUE_REL_TOLERANCE,
            abs_tolerance=TOTAL_ABS_TOLERANCE,
        )
    )
    if not approx_equal_scaled(
        open_half.value,
        close_half.value,
        reference=max(abs(open_half.value), abs(close_half.value)),
        rel_tolerance=VALUE_REL_TOLERANCE,
    ):
        issues.append(
            f"calculated position value: {SPLIT_CLOSE_ACTION}="
            f"{close_half.value}; {SPLIT_OPEN_ACTION}={open_half.value}"
        )
    return issues


# Where a row came from. Unique per row, and the handle the merge marks rows
# by, so that transactions never need to be hashable.
type Provenance = tuple[Path, int]

# The most subset combinations the merge will rank before giving up and taking
# rows by their own timestamps. C(12, 6) is the dearest way to choose from
# twelve rows, so anything a real export offers stays exact. The gate is the
# number of candidates rather than the bucket size: C(13, 13) is one candidate
# and C(100, 1) is a hundred, both trivial, and the first is how a single file
# arrives here.
ANONYMOUS_CANDIDATE_LIMIT: Final = 924


def _source_file(transaction: Trading212Transaction) -> Path:
    """Return the export a row was read from."""
    source = transaction.source
    assert source is not None, "transactions are stamped before post-processing"
    assert source.file is not None
    return source.file


def _provenance(transaction: Trading212Transaction) -> Provenance:
    """Identify one row by where it came from."""
    source = transaction.source
    assert source is not None
    assert source.index is not None
    return (_source_file(transaction), source.index)


def _row_order_key(transaction: Trading212Transaction) -> tuple[bool, int, int]:
    """Order rows within one source, most informative timestamp first.

    A row stating a fraction of a second is preferred to one stating a whole
    second, because the two can be the same transaction reported at different
    precision and the finer value is the better record.
    """
    microsecond = transaction.datetime.microsecond
    source = transaction.source
    index = source.index if source is not None and source.index is not None else 0
    return (microsecond == 0, microsecond, index)


def _collection_order_key(
    rows: list[Trading212Transaction],
) -> tuple[int, int, tuple[int, ...], tuple[Provenance, ...]]:
    """Order whole candidate selections, best first.

    Prefers keeping fractional timestamps, then keeping distinct ones. The
    second test is what stops an export that repeats one fraction displacing
    one that tells its rows apart: given `.100`/`.500` against `.100`/`.100`,
    both have two fractions, but only the first has two different ones.
    Without it the ascending comparison would pick `(.100, .100)` and lose the
    `.500` occurrence.

    The last two are tie-breaks with no meaning of their own, present so that
    the order is total and the result cannot depend on filenames.
    """
    fractions = sorted(row.datetime.microsecond for row in rows)
    return (
        -sum(1 for fraction in fractions if fraction),
        -len(set(fractions)),
        tuple(fractions),
        tuple(sorted(_provenance(row) for row in rows)),
    )


class Trading212Parser(BaseDirParser[BrokerTransaction]):
    """Trading 212 parser."""

    arg_name = "trading212"
    pretty_name = "Trading 212"
    format_name = "CSV"
    glob_dir = "*.csv"
    deprecated_flags: ClassVar[list[str]] = ["--trading212"]

    @classmethod
    @override
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[BrokerTransaction]:
        """Parse Trading 212 transactions from CSV file."""
        lines = read_csv_rows(file, file_path)
        if not lines:
            raise ParsingError(file_path, "Trading 212 CSV file is empty")
        header = lines[0]
        cls._validate_header(header, file_path)
        lines = lines[1:]
        transactions: list[BrokerTransaction] = []
        for index, row in enumerate(lines, start=2):
            try:
                transaction = Trading212Transaction(header, row, file_path)
            except ParsingError as err:
                err.add_row_context(index)
                raise
            except ValueError as err:
                raise ParsingError(file_path, str(err), row_index=index) from err
            transaction.source = TransactionSource(
                row=index, timestamp=transaction.datetime
            )
            transactions.append(transaction)
        return transactions

    @staticmethod
    def _validate_header(header: list[str], file: Path) -> None:
        """Check if header is valid. Not all columns exist in every export."""
        unknown = set(header) - COLUMN_SET
        if unknown:
            raise ParsingError(
                file, f"Unknown column(s) {', '.join(sorted(unknown))}", row_index=1
            )

    @staticmethod
    def _by_instant(transaction: BrokerTransaction) -> datetime:
        """Sort by the instant the export stated."""
        if isinstance(transaction, StockSplitTransaction):
            # A reorganisation, which is two rows combined into one event.
            # It exists from the instant of its `open` half.
            return max(transaction.event.instants)
        return _as_trading212(transaction).datetime

    @classmethod
    @override
    def post_process_transactions(
        cls, transactions: list[BrokerTransaction]
    ) -> list[BrokerTransaction]:
        """Collapse rows two exports both report, and put them in order."""
        return sorted(cls._deduplicate(transactions), key=cls._by_instant)

    @classmethod
    def _deduplicate(
        cls, transactions: list[BrokerTransaction]
    ) -> list[BrokerTransaction]:
        """Merge overlapping exports without losing within-file multiplicity.

        Exports are requested by date range, so consecutive downloads
        routinely cover the same days and report the same transaction twice.
        Rows recording the same content are gathered into a group, and how
        many of them are real is settled by the export reporting the most:
        no export may be assumed complete, but none is expected to invent a
        fill either.

        This runs per file as well as over a directory, and on one file it
        cannot change anything: that file supplies the whole group, so the
        count it reports is the count kept. Removing a repeated row there
        would lose a real fill, and the invariant is what stops it.

        Every split row is checked before any overlap copy can be dropped, so
        a richer schema cannot hide a cash leg or other contrary evidence.
        """
        groups: dict[tuple[object, ...], list[Trading212Transaction]] = defaultdict(
            list
        )
        for row in transactions:
            transaction = _as_trading212(row)
            if transaction.is_split_half:
                _read_split_half(transaction)
            elif transaction.action is ActionType.STOCK_SPLIT:
                _check_split_has_no_money(transaction)
            groups[transaction.recorded_fields()].append(transaction)

        selected: set[Provenance] = set()
        for group in groups.values():
            selected.update(cls._select_group(group))
            # The copies this group collapses into one can disagree below the
            # second, and the survivor is chosen without regard to a
            # reorganisation it might sit inside. Give every kept row the
            # instants all of them stated, so the chronology check still sees
            # the evidence the discarded copies carried.
            instants = frozenset(row.datetime for row in group)
            if len(instants) > 1:
                for row in group:
                    row.stated_instants = instants
        cls._warn_conflicting_content(groups)

        # Filtering the original list rather than concatenating the groups
        # keeps the relative order of what survives, which matters because
        # the sort that follows is stable and rows within one second tie on
        # its key.
        return [
            row for row in transactions if _provenance(_as_trading212(row)) in selected
        ]

    @classmethod
    def _select_group(cls, group: list[Trading212Transaction]) -> set[Provenance]:
        """Decide which rows recording one transaction to keep.

        Identified rows are taken a whole source bucket at a time, so that a
        repeated copy in one export can never stand in for a distinct
        occurrence in another, and so that an ID reported twice by one export
        keeps both of its rows.
        """
        by_source: dict[Path, list[Trading212Transaction]] = defaultdict(list)
        for row in group:
            by_source[_source_file(row)].append(row)
        file_count = max(len(rows) for rows in by_source.values())

        # One pass over the group, rather than rescanning every source for
        # each distinct ID, which is quadratic once a group holds many.
        id_buckets: dict[str, dict[Path, list[Trading212Transaction]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for row in group:
            if row.transaction_id:
                id_buckets[row.transaction_id][_source_file(row)].append(row)

        identified: list[Trading212Transaction] = []
        for transaction_id in sorted(id_buckets):
            buckets = list(id_buckets[transaction_id].values())
            multiplicity = max(len(bucket) for bucket in buckets)
            tied = [bucket for bucket in buckets if len(bucket) == multiplicity]
            best = min(tied, key=_collection_order_key)
            identified += best

        chosen = list(identified)
        needed = max(file_count, len(identified)) - len(identified)
        if needed:
            chosen += cls._best_anonymous(by_source, identified, needed, file_count)
        return {_provenance(row) for row in chosen}

    @classmethod
    def _best_anonymous(
        cls,
        by_source: dict[Path, list[Trading212Transaction]],
        identified: list[Trading212Transaction],
        needed: int,
        file_count: int,
    ) -> list[Trading212Transaction]:
        """Fill the remaining slots from one export's rows without an ID.

        Only the exports reporting the most rows for this content are
        eligible, and the completed selection is ranked as a whole rather
        than the anonymous rows alone. Ranking them alone would let a second
        copy of a timestamp already taken beat a row carrying one not yet
        represented.
        """
        best_key = None
        best: list[Trading212Transaction] = []
        # Any source that could not be ranked exactly taints the comparison,
        # not just the one that wins: its approximate subset is what gets
        # ranked, so it can lose a contest its exact subset would have won,
        # and which export the rows come from then depends on the gate.
        approximated: list[Path] = []
        for source in sorted(by_source):
            rows = by_source[source]
            if len(rows) != file_count:
                continue
            anonymous = sorted(
                (row for row in rows if row.transaction_id is None),
                key=_row_order_key,
            )
            subset, used_fallback = cls._best_subset(identified, anonymous, needed)
            if used_fallback:
                approximated.append(source)
            key = _collection_order_key([*identified, *subset])
            if best_key is None or key < best_key:
                best_key, best = key, subset

        if approximated:
            LOGGER.warning(
                "Too many ways to choose %d of the transactions that share one "
                "second and are identical in every detail used to match "
                "exports, so they were taken by timestamp alone from %s, "
                "preferring rows that state a fraction of a second over rows "
                "that state a whole one, then the lowest fractions. The number "
                "of transactions and the tax figures are unaffected, but which "
                "rows were kept, and which export they came from, may differ "
                "from an exact comparison.",
                needed,
                ", ".join(str(source) for source in approximated),
            )
        return best

    @staticmethod
    def _best_subset(
        identified: list[Trading212Transaction],
        anonymous: list[Trading212Transaction],
        needed: int,
    ) -> tuple[list[Trading212Transaction], bool]:
        """Choose `needed` of one export's rows without an ID.

        Ranking every candidate is exact but grows combinatorially, so it is
        gated on how many candidates there are rather than how many rows:
        taking all of a large bucket, or one row from it, is cheap and stays
        exact. Returns the rows and whether that gate was hit, so the caller
        can report it once it knows which source won.
        """
        if math.comb(len(anonymous), needed) <= ANONYMOUS_CANDIDATE_LIMIT:
            exact = min(
                combinations(anonymous, needed),
                key=lambda subset: _collection_order_key([*identified, *subset]),
            )
            return list(exact), False
        return anonymous[:needed], True

    @staticmethod
    def _warn_conflicting_content(
        groups: dict[tuple[object, ...], list[Trading212Transaction]],
    ) -> None:
        """Report an ID that two exports describe differently.

        Trading 212 reuses an ID across a buy and the sell that closes it, so
        an ID under several groups is ordinary and must not warn. What is not
        ordinary is the same ID at the same instant for the same action
        described two ways, which is one export restating a row another still
        reports the old way. Both are kept, so the totals may count it twice,
        and only re-exporting can settle it.
        """
        sources: dict[tuple[str, tuple[object, ...]], set[Path]] = defaultdict(set)
        for fingerprint, group in groups.items():
            for row in group:
                if row.transaction_id:
                    sources[row.transaction_id, fingerprint].add(_source_file(row))

        by_id: dict[str, list[tuple[object, ...]]] = defaultdict(list)
        for transaction_id, fingerprint in sources:
            by_id[transaction_id].append(fingerprint)

        for transaction_id, fingerprints in by_id.items():
            for first, second in combinations(fingerprints, 2):
                # A fingerprint opens with the truncated instant and the
                # action, which together are what a restatement holds fixed
                # and a reused ID does not.
                if first[:2] != second[:2]:
                    continue
                files = sources[transaction_id, first]
                others = sources[transaction_id, second]
                if files & others:
                    # One export lists both, so they are separate rows rather
                    # than two accounts of one row.
                    continue
                LOGGER.warning(
                    "Transaction ID %s is described differently by %s. Both "
                    "rows were kept, so totals may count this transaction "
                    "twice. Re-export the overlapping range so the files "
                    "agree.",
                    transaction_id,
                    " and ".join(str(f) for f in sorted(files | others)),
                )

    @classmethod
    @override
    def finalize_transactions(
        cls, transactions: list[BrokerTransaction]
    ) -> list[BrokerTransaction]:
        """Pair reorganisation rows, then add the rows a dividend or a conversion implies.

        Each needs every export of the account, merged.
        """
        return cls._add_conversion_cash(
            cls._add_foreign_tax(cls._pair_reorganisations(transactions))
        )

    @classmethod
    def _pair_reorganisations(
        cls, transactions: list[BrokerTransaction]
    ) -> list[BrokerTransaction]:
        """Combine each pair of reorganisation rows into one event.

        This has to see the whole directory at once. The two halves can land
        in different exports, and `post_process_transactions` runs per file
        as well, so pairing there would reject a half whose partner is in the
        next file.
        """
        halves = [
            transaction
            for transaction in transactions
            if isinstance(transaction, Trading212Transaction)
            and transaction.is_split_half
        ]
        if not halves:
            return transactions
        # By identity: two halves of one reorganisation can be identical rows
        # apart from their action, and comparing by value would drop one.
        paired = {id(half) for half in halves}
        rest = [
            transaction for transaction in transactions if id(transaction) not in paired
        ]
        split_halves = [_read_split_half(half) for half in halves]
        legacy_splits = [
            _as_trading212(transaction)
            for transaction in rest
            if transaction.action is ActionType.STOCK_SPLIT
        ]
        events = cls._pair_split_halves(split_halves, legacy_splits)
        for event in events:
            cls._refuse_rows_between_halves(event, rest)
        return sorted([*rest, *events], key=cls._by_instant)

    @staticmethod
    def _add_foreign_tax(
        transactions: list[BrokerTransaction],
    ) -> list[BrokerTransaction]:
        """Record each dividend before foreign tax, with the tax on a row after it.

        This runs once overlapping exports are merged, so a dividend that two
        exports both report gets one tax row, and its warning is printed once.
        """
        result: list[BrokerTransaction] = []
        for transaction in transactions:
            result.append(transaction)
            if isinstance(transaction, Trading212Transaction):
                tax = transaction.separate_foreign_tax()
                if tax is not None:
                    result.append(tax)
        return result

    @staticmethod
    def _add_conversion_cash(
        transactions: list[BrokerTransaction],
    ) -> list[BrokerTransaction]:
        """Follow each currency conversion with the cash it moved.

        This runs once overlapping exports are merged, as the foreign tax
        does, so a conversion that two exports both report moves its cash once.
        """
        result: list[BrokerTransaction] = []
        for transaction in transactions:
            result.append(transaction)
            if isinstance(transaction, Trading212Transaction):
                result.extend(transaction.conversion_cash())
        return result

    @staticmethod
    def _split_candidates(halves: list[SplitHalf]) -> list[list[SplitHalf]]:
        """Group unresolved halves only far enough to write one useful error.

        Event ownership is settled before this diagnostic window is used.
        Each group is anchored at its first row rather than extended from its
        last, so a chain of unrelated rows cannot grow it without bound.

        The account bucket has exactly one key today: `finalize_transactions`
        runs once per declared boundary, and every file in one runs under one
        token. It is kept as the guard it is rather than dropped, because the
        rows it would otherwise put in one error belong to accounts the docs
        promise are never read together -- but it is a guard, not a grouping,
        so nothing here relies on it separating anything.
        """
        by_account: dict[object, list[SplitHalf]] = defaultdict(list)
        for half in halves:
            source = half.transaction.source
            by_account[source.account if source is not None else None].append(half)

        clusters: list[list[SplitHalf]] = []
        for account_halves in by_account.values():
            # Stable, so halves stamped at one instant keep the order they
            # were read in and the grouping is deterministic.
            account_halves.sort(key=lambda half: half.transaction.datetime)
            cluster: list[SplitHalf] = []
            for half in account_halves:
                if (
                    cluster
                    and half.transaction.datetime - cluster[0].transaction.datetime
                    > SPLIT_CANDIDATE_WINDOW
                ):
                    clusters.append(cluster)
                    cluster = []
                cluster.append(half)
            clusters.append(cluster)
        return clusters

    @classmethod
    def _pair_split_halves(
        cls,
        halves: list[SplitHalf],
        legacy_splits: list[Trading212Transaction],
    ) -> list[StockSplitTransaction]:
        """Pair every row only when the strict evidence has one full matching."""
        possibilities: dict[int, list[SplitHalf]] = {id(half): [] for half in halves}
        opens = [half for half in halves if half.is_open]
        closes = [half for half in halves if not half.is_open]
        for close_half in closes:
            close_source = close_half.transaction.source
            close_key = (
                close_source.account if close_source is not None else None,
                close_half.symbol,
                close_half.price_currency,
                close_half.transaction.currency,
            )
            for open_half in opens:
                open_source = open_half.transaction.source
                open_key = (
                    open_source.account if open_source is not None else None,
                    open_half.symbol,
                    open_half.price_currency,
                    open_half.transaction.currency,
                )
                if close_key == open_key and not _half_incompatibilities(
                    open_half, close_half
                ):
                    possibilities[id(close_half)].append(open_half)
                    possibilities[id(open_half)].append(close_half)

        pairs: list[tuple[SplitHalf, SplitHalf]] = []
        unresolved: list[SplitHalf] = []
        visited: set[int] = set()
        for half in halves:
            if id(half) in visited:
                continue
            if not possibilities[id(half)]:
                visited.add(id(half))
                unresolved.append(half)
                continue
            component: list[SplitHalf] = []
            pending = [half]
            while pending:
                candidate = pending.pop()
                if id(candidate) in visited:
                    continue
                visited.add(id(candidate))
                component.append(candidate)
                pending.extend(possibilities[id(candidate)])
            # Exactly one close and one open, or refuse. Edges only join a
            # close to an open, so a two-row component is necessarily one of
            # each. A larger component means several rows share the same
            # evidence, and searching it for a unique matching is a factorial
            # walk on rows deduplication legitimately keeps (distinct ids
            # across overlapping exports), while the calculator refuses more
            # than one reorganisation of one security on one date anyway.
            if len(component) == HALVES_PER_SPLIT:
                open_half = next(half for half in component if half.is_open)
                close_half = next(half for half in component if not half.is_open)
                pairs.append((open_half, close_half))
            else:
                cls._refuse_unpaired_group(component, component)

        events: list[StockSplitTransaction] = []
        for open_half, close_half in pairs:
            cls._refuse_legacy_split(open_half, close_half, legacy_splits)
            events.append(cls._combine(open_half, close_half))

        for candidates in cls._split_candidates(unresolved):
            opens = [half for half in candidates if half.is_open]
            closes = [half for half in candidates if not half.is_open]
            if len(opens) == 1 and len(closes) == 1:
                open_half, close_half = opens[0], closes[0]
                pairing_fields = (
                    (
                        Trading212Column.TICKER.value,
                        close_half.symbol,
                        open_half.symbol,
                    ),
                    (
                        Trading212Column.CURRENCY_PRICE_PER_SHARE.value,
                        close_half.price_currency,
                        open_half.price_currency,
                    ),
                    (
                        Trading212Column.CURRENCY_TOTAL.value,
                        close_half.transaction.currency,
                        open_half.transaction.currency,
                    ),
                )
                disagreements = [
                    f"{field}: {SPLIT_CLOSE_ACTION}={close!r}; "
                    f"{SPLIT_OPEN_ACTION}={after!r}"
                    for field, close, after in pairing_fields
                    if close != after
                ]
                if disagreements:
                    row = open_half.transaction
                    raise _split_error(
                        row,
                        f"The only {SPLIT_CLOSE_ACTION} and {SPLIT_OPEN_ACTION} "
                        f"rows stamped together around {row.date} disagree "
                        f"about {'; '.join(disagreements)}. Rows: "
                        f"{_describe_half(close_half)}; "
                        f"{_describe_half(open_half)}. "
                        f"{_split_pair_remedy(_split_period(candidates))}",
                    )

            groups: dict[tuple[object, ...], list[SplitHalf]] = defaultdict(list)
            for half in candidates:
                source = half.transaction.source
                groups[
                    source.account if source is not None else None,
                    half.symbol,
                    half.price_currency,
                    half.transaction.currency,
                ].append(half)
            for group in groups.values():
                related = [
                    half for half in candidates if half.symbol == group[0].symbol
                ]
                cls._refuse_unpaired_group(group, related)
        return events

    @staticmethod
    def _refuse_legacy_split(
        open_half: SplitHalf,
        close_half: SplitHalf,
        legacy_splits: list[Trading212Transaction],
    ) -> None:
        """Refuse old and new export shapes that may state one event twice.

        Both of the pair's UK dates count, not just the one the event ends up
        filed under. A pair stamped either side of UK midnight puts its close
        on the previous day, and a legacy row on that day, more than a window
        from either half, once escaped both tests and let the same
        consolidation be applied a second time -- silently, because the
        calculator plans each day on its own and never saw the two together.
        """
        pair_dates = {
            open_half.transaction.date,
            close_half.transaction.date,
        }
        conflicts = [
            row
            for row in legacy_splits
            if row.symbol == open_half.symbol
            and (
                row.date in pair_dates
                or min(
                    abs(row.datetime - open_half.transaction.datetime),
                    abs(row.datetime - close_half.transaction.datetime),
                )
                <= SPLIT_CANDIDATE_WINDOW
            )
        ]
        if not conflicts:
            return
        rows = [open_half.transaction, close_half.transaction, *conflicts]
        days = sorted({row.date for row in rows})
        period = str(days[0]) if len(days) == 1 else f"{days[0]} through {days[-1]}"
        row = conflicts[0]
        legacy = "; ".join(
            f"{split.raw_action} at {split.where()} "
            f"(Ticker={split.symbol!r}, No. of shares={split.quantity}, "
            f"Time={_exported_time(split)})"
            for split in conflicts
        )
        raise _split_error(
            row,
            f"{row.symbol} is reported as both Stock Split and Stock split "
            "close/open. They may describe one corporate action, so applying "
            "both could restate the holding twice, while discarding either "
            f"could erase a separate event. Rows: {legacy}; "
            f"{_describe_half(close_half)}; {_describe_half(open_half)}. "
            f"{_split_pair_remedy(period)}",
        )

    @staticmethod
    def _refuse_unpaired_group(
        group: list[SplitHalf], candidates: list[SplitHalf]
    ) -> NoReturn:
        """Refuse rows the ownership matcher could not pair.

        A half without its partner says nothing about what happened to the
        holding, and a larger group has no reading worth guessing.
        ``candidates`` contains only the nearby rows for this holding, so the
        error cannot implicate an unrelated security.
        """
        opens = [half for half in group if half.is_open]
        closes = [half for half in group if not half.is_open]
        if len(opens) != 1 or len(closes) != 1:
            row = (opens or closes)[0].transaction
            period = _split_period(candidates)
            when = f"on {period}" if " through " not in period else f"across {period}"
            raise _split_error(
                row,
                f"{row.symbol} {when} has {len(closes)} "
                f"{SPLIT_CLOSE_ACTION} and {len(opens)} {SPLIT_OPEN_ACTION} "
                "rows. A reorganisation is exported as the position before it "
                "and the position after it, once each. Candidate rows: "
                f"{'; '.join(_describe_half(half) for half in candidates)}. "
                f"{_split_pair_remedy(period)}",
            )
        open_half, close_half = opens[0], closes[0]
        incompatibilities = _half_incompatibilities(open_half, close_half)
        detail = "; ".join(incompatibilities) or "there is no unique pairing"
        row = open_half.transaction
        raise _split_error(
            row,
            f"The {SPLIT_OPEN_ACTION} and {SPLIT_CLOSE_ACTION} rows for "
            f"{row.symbol} on {row.date} cannot be proved to be the two "
            f"halves of one event: {detail}. Rows: "
            f"{_describe_half(close_half)}; {_describe_half(open_half)}. "
            f"{_split_pair_remedy(_split_period(group))}",
        )

    @classmethod
    def _combine(
        cls, open_half: SplitHalf, close_half: SplitHalf
    ) -> StockSplitTransaction:
        """Build the one event these two rows describe."""
        after = open_half.transaction
        before = close_half.transaction
        if (
            before.isin is not None
            and after.isin is not None
            and before.isin != after.isin
        ):
            raise _split_error(
                after,
                f"The reorganisation of {after.symbol} on {after.date} changes "
                f"the ISIN from {before.isin} to {after.isin}, which is a "
                "different security. Moving a holding between two securities "
                "needs authoritative relationship data this cannot get from "
                f"the export. Rows: {close_half.transaction.where()}; "
                f"{open_half.transaction.where()}. "
                f"{_split_pair_remedy(_split_period([close_half, open_half]))}",
            )
        ratio = recover_ratio(close_half.quantity, open_half.quantity)
        if isinstance(ratio, UnresolvedRatio):
            # Not a parsing failure. A holding the export states in full is
            # replaced outright and needs no ratio, so refusing the whole
            # file here would reject it for a number nothing asked for. The
            # calculator refuses where the ratio is actually used.
            LOGGER.debug(
                "No single ratio for the %s reorganisation on %s: %s",
                after.symbol,
                after.date,
                ratio.describe(),
            )
        # Proven non-blank when the half was read, so no check here.
        symbol = open_half.symbol
        event = StockSplitEvent(
            date=after.date,
            broker=after.broker,
            symbol_before=symbol,
            symbol_after=symbol,
            isin_before=before.isin,
            isin_after=after.isin,
            before_quantity=close_half.quantity,
            after_quantity=open_half.quantity,
            ratio=ratio,
            instants=(before.datetime, after.datetime),
        )
        return StockSplitTransaction(
            event=event,
            description=after.description,
            currency=after.currency,
            source=after.source,
        )

    @staticmethod
    def _refuse_rows_between_halves(
        event: StockSplitTransaction, others: list[BrokerTransaction]
    ) -> None:
        """Refuse a trade stamped at or between the two halves.

        Such a row is on neither side of the reorganisation, so whether its
        count is stated in the units before it or the units after it cannot
        be proved.
        """
        first = min(event.event.instants)
        last = max(event.event.instants)
        for row in others:
            if row.symbol != event.symbol or quantity_sign(row.action) == 0:
                continue
            other = _as_trading212(row)
            if any(first <= instant <= last for instant in other.stated_instants):
                raise _split_error(
                    other,
                    f"This row is stamped at or between the two halves of the "
                    f"{event.symbol} reorganisation on {event.date}, so there "
                    "is no telling whether its share count is stated in the "
                    f"units before it or the units after it. Rows: "
                    f"{other.raw_action} at {other.where()} "
                    f"(No. of shares={other.quantity}, "
                    f"Time={_stated_times(other)}); the reorganisation at "
                    f"{first} and {last}. Check this row against Trading 212; "
                    "if it really was dealt at or between those two times, "
                    f"leave {event.symbol} out and work it out by hand "
                    "(consider professional advice).",
                )
