"""Sharesight parser."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
import logging
from typing import TYPE_CHECKING, ClassVar, Final, TextIO, override

from cgt_calc.exceptions import (
    InvalidTransactionError,
    ParsingError,
    UnexpectedColumnCountError,
)
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    TransactionSource,
)
from cgt_calc.util import parse_decimal

from .base_parsers import BaseDirParser

if TYPE_CHECKING:
    from pathlib import Path

STOCK_ACTIVITY_COMMENT_MARKER: Final[str] = "Stock Activity"
LOGGER = logging.getLogger(__name__)


class DividendColumn(StrEnum):
    """Columns available in Sharesight dividend sections."""

    CODE = "Code"
    NAME = "Name"
    DATE_PAID = "Date Paid"
    COMMENTS = "Comments"
    NET_DIVIDEND = "Net Dividend"
    TAX_DEDUCTED = "Tax Deducted"
    TAX_CREDIT = "Tax Credit"
    GROSS_DIVIDEND = "Gross Dividend"
    EXCHANGE_RATE = "Exchange Rate"
    CURRENCY = "Currency"
    NET_AMOUNT = "Net Amount"
    FOREIGN_TAX_DEDUCTED = "Foreign Tax Deducted"
    GROSS_AMOUNT = "Gross Amount"


@dataclass(frozen=True)
class DividendSectionSchema:
    """Schema describing a dividend section in the income report."""

    section_name: str
    expected_columns: tuple[DividendColumn, ...]
    amount_column: DividendColumn
    tax_column: DividendColumn
    currency_column: DividendColumn | None
    default_currency: CurrencyCode | None = None


LOCAL_DIVIDEND_SCHEMA = DividendSectionSchema(
    section_name="Sharesight local dividend header",
    expected_columns=(
        DividendColumn.CODE,
        DividendColumn.NAME,
        DividendColumn.DATE_PAID,
        DividendColumn.NET_DIVIDEND,
        DividendColumn.TAX_DEDUCTED,
        DividendColumn.TAX_CREDIT,
        DividendColumn.GROSS_DIVIDEND,
        DividendColumn.COMMENTS,
    ),
    amount_column=DividendColumn.GROSS_DIVIDEND,
    tax_column=DividendColumn.TAX_DEDUCTED,
    currency_column=None,
    default_currency=CurrencyCode("GBP"),
)


FOREIGN_DIVIDEND_SCHEMA = DividendSectionSchema(
    section_name="Sharesight foreign dividend header",
    expected_columns=(
        DividendColumn.CODE,
        DividendColumn.NAME,
        DividendColumn.DATE_PAID,
        DividendColumn.EXCHANGE_RATE,
        DividendColumn.CURRENCY,
        DividendColumn.NET_AMOUNT,
        DividendColumn.FOREIGN_TAX_DEDUCTED,
        DividendColumn.GROSS_AMOUNT,
        DividendColumn.COMMENTS,
    ),
    amount_column=DividendColumn.GROSS_AMOUNT,
    tax_column=DividendColumn.FOREIGN_TAX_DEDUCTED,
    currency_column=DividendColumn.CURRENCY,
    default_currency=None,
)


class TradeColumn(StrEnum):
    """Columns present in the Sharesight trades report."""

    MARKET = "Market"
    CODE = "Code"
    NAME = "Name"
    TYPE = "Type"
    DATE = "Date"
    QUANTITY = "Quantity"
    PRICE = "Price *"
    BROKERAGE = "Brokerage *"
    CURRENCY = "Currency"
    BROKERAGE_CURRENCY = "Brokerage Currency"
    EXCHANGE_RATE = "Exchange Rate"
    VALUE = "Value"
    COMMENTS = "Comments"


REQUIRED_TRADE_COLUMNS: Final[tuple[TradeColumn, ...]] = (
    TradeColumn.MARKET,
    TradeColumn.CODE,
    TradeColumn.TYPE,
    TradeColumn.DATE,
    TradeColumn.QUANTITY,
    TradeColumn.PRICE,
    TradeColumn.CURRENCY,
)

# Sharesight has changed the spreadsheet headings over time. Keep one internal
# vocabulary so that the legacy and newer export generations are interpreted
# identically.
TRADE_COLUMN_ALIASES: Final[dict[str, TradeColumn]] = {
    "Market": TradeColumn.MARKET,
    "Market Code": TradeColumn.MARKET,
    "Code": TradeColumn.CODE,
    "Name": TradeColumn.NAME,
    "Type": TradeColumn.TYPE,
    "Date": TradeColumn.DATE,
    "Quantity": TradeColumn.QUANTITY,
    "Qty": TradeColumn.QUANTITY,
    "Price *": TradeColumn.PRICE,
    "Price": TradeColumn.PRICE,
    "Brokerage *": TradeColumn.BROKERAGE,
    "Brokerage": TradeColumn.BROKERAGE,
    "Currency": TradeColumn.CURRENCY,
    "Instrument Currency": TradeColumn.CURRENCY,
    "Brokerage Currency": TradeColumn.BROKERAGE_CURRENCY,
    "Exchange Rate": TradeColumn.EXCHANGE_RATE,
    "Exch. Rate": TradeColumn.EXCHANGE_RATE,
    "Value": TradeColumn.VALUE,
    "Comments": TradeColumn.COMMENTS,
}


class SharesightTransaction(BrokerTransaction):
    """Sharesight transaction.

    Just a marker type for now
    """


class RowIterator(Iterator[list[str]]):
    """Iterator for CSV rows that keeps track of line number."""

    def __init__(self, rows: Iterable[list[str]]) -> None:
        """Initialise RowIterator."""
        self.rows = iter(rows)
        self.line = 0

    @override
    def __next__(self) -> list[str]:
        """Produce next element and increment line number."""
        elm = next(self.rows)
        self.line += 1
        return elm

    @override
    def __iter__(self) -> RowIterator:
        """Return an iterator for this object."""
        return self


class SharesightParser(BaseDirParser[SharesightTransaction]):
    """Sharesight parser."""

    arg_name = "sharesight"
    pretty_name = "Sharesight"
    format_name = "CSV"
    glob_dir = "*.csv"
    deprecated_flags: ClassVar[list[str]] = ["--sharesight"]

    # ===== public API =====

    @classmethod
    @override
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[SharesightTransaction]:
        """Parse Sharesight transactions from reports."""
        if file_path.match("Taxable Income Report*.csv", case_sensitive=False):
            return list(cls._parse_income_report(file, file_path))

        if file_path.match("All Trades Report*.csv", case_sensitive=False):
            return list(cls._parse_trade_report(file, file_path))
        return []

    @classmethod
    @override
    def post_process_transactions(
        cls, transactions: list[SharesightTransaction]
    ) -> list[SharesightTransaction]:
        """Sort transactions by date."""
        transactions.sort(key=lambda t: t.date)
        return transactions

    # ===== internal helpers =====

    @staticmethod
    def _parse_date(val: str) -> date:
        """Parse a Sharesight report date."""
        return datetime.strptime(val, "%d/%m/%Y").date()

    @staticmethod
    def _parse_decimal[Col: StrEnum](
        row_dict: Mapping[Col, str], column: Col
    ) -> Decimal:
        """Convert column value to Decimal."""
        return parse_decimal(row_dict[column], f"column '{column.value}'", strip=",")

    @classmethod
    def _maybe_decimal[Col: StrEnum](
        cls, row_dict: Mapping[Col, str], column: Col
    ) -> Decimal | None:
        """Convert column value to Decimal if provided."""
        raw_value = row_dict.get(column, "")
        return cls._parse_decimal(row_dict, column) if raw_value else None

    @staticmethod
    def _validate_header(
        header: list[str],
        expected: Iterable[StrEnum],
        *,
        file: Path,
        section: str,
    ) -> None:
        """Validate that all expected columns are present."""
        expected_values = {column.value for column in expected}
        header_values = {column for column in header if column}
        missing = expected_values - header_values
        if missing:
            raise ParsingError(
                file,
                f"Missing expected columns in {section}: {', '.join(sorted(missing))}",
            )

    @staticmethod
    def _trade_header_columns(header: list[str]) -> list[TradeColumn | None]:
        """Map the known All Trades headings to their internal columns."""
        columns = [TRADE_COLUMN_ALIASES.get(column) for column in header]

        # One export generation replaced `Currency` with `Brokerage Currency`.
        # Treat it as the transaction currency only when the report does not
        # provide the newer, unambiguous `Instrument Currency` column as well.
        if (
            TradeColumn.CURRENCY not in columns
            and TradeColumn.BROKERAGE_CURRENCY in columns
        ):
            columns[columns.index(TradeColumn.BROKERAGE_CURRENCY)] = (
                TradeColumn.CURRENCY
            )
        return columns

    @classmethod
    def _parse_dividend_payments(
        cls,
        schema: DividendSectionSchema,
        rows: RowIterator,
        file: Path,
    ) -> Iterable[SharesightTransaction]:
        """Parse dividend payments from Sharesight data."""
        header = next(rows, None)
        if header is None:
            return

        cls._validate_header(
            header,
            schema.expected_columns,
            file=file,
            section=schema.section_name,
        )

        for row in rows:
            if row[0] == "Total":
                # Don't use the totals row, but it signals the end of the section
                break

            if len(row) != len(header):
                raise UnexpectedColumnCountError(row, len(header), file)

            row_dict: dict[DividendColumn, str] = {}
            for column, value in zip(header, row, strict=True):
                try:
                    dividend_column = DividendColumn(column)
                except ValueError:
                    # Sharesight may append informational columns such as the
                    # income country or type. Required calculation fields were
                    # already checked above, so unrelated additions are safe to
                    # ignore.
                    continue
                row_dict[dividend_column] = value

            dividend_date = cls._parse_date(row_dict[DividendColumn.DATE_PAID])
            symbol = row_dict[DividendColumn.CODE]
            description = row_dict[DividendColumn.COMMENTS]
            broker = "Sharesight"

            if schema.default_currency:
                currency = schema.default_currency
            else:
                if schema.currency_column is None:
                    raise ValueError(
                        f"Missing currency column definition for {schema.section_name}"
                    )

                currency_raw = row_dict.get(schema.currency_column, "").strip()
                if not currency_raw:
                    raise ValueError(
                        "Missing currency in column "
                        f"'{schema.currency_column.value}' for {schema.section_name}"
                    )
                currency = CurrencyCode(currency_raw)

            amount = cls._parse_decimal(row_dict, schema.amount_column)
            tax = cls._maybe_decimal(row_dict, schema.tax_column)

            yield SharesightTransaction(
                date=dividend_date,
                action=ActionType.DIVIDEND,
                symbol=symbol,
                description=description,
                broker=broker,
                currency=currency,
                amount=amount,
                quantity=None,
                price=None,
                fees=Decimal(0),
                source=TransactionSource(row=rows.line),
            )

            # Generate the tax as a separate transaction
            if tax:
                yield SharesightTransaction(
                    date=dividend_date,
                    action=ActionType.DIVIDEND_TAX,
                    symbol=symbol,
                    description=description,
                    broker=broker,
                    currency=currency,
                    amount=-tax,
                    quantity=None,
                    price=None,
                    fees=Decimal(0),
                    source=TransactionSource(row=rows.line),
                )

    @classmethod
    def _parse_local_income(
        cls, rows: RowIterator, file: Path
    ) -> Iterable[SharesightTransaction]:
        """Parse Local Income section from Sharesight data."""
        for row in rows:
            if row[0] == "Total Local Income":
                return

            if row[0] == "Dividend Payments":
                yield from cls._parse_dividend_payments(
                    LOCAL_DIVIDEND_SCHEMA, rows, file
                )

    @classmethod
    def _parse_income_report(
        cls, file: TextIO, file_path: Path
    ) -> Iterable[SharesightTransaction]:
        """Parse the Taxable Income Report from Sharesight."""
        rows = list(csv.reader(file))

        # Use our custom iterator for error reporting
        rows_iter = RowIterator(rows)
        try:
            for row in rows_iter:
                if row[0] == "Local Income":
                    yield from cls._parse_local_income(rows_iter, file_path)
                elif row[0] == "Foreign Income":
                    yield from cls._parse_dividend_payments(
                        FOREIGN_DIVIDEND_SCHEMA, rows_iter, file_path
                    )
        except ParsingError as err:
            err.add_row_context(rows_iter.line)
            raise
        except ValueError as err:
            raise ParsingError(file_path, str(err), row_index=rows_iter.line) from err

    @classmethod
    def _parse_trades(
        cls, header: list[str], rows: RowIterator, file: Path
    ) -> Iterable[SharesightTransaction]:
        """Parse content in All Trades Report from Sharesight."""
        header_columns = cls._trade_header_columns(header)
        cls._validate_header(
            [column.value if column is not None else "" for column in header_columns],
            REQUIRED_TRADE_COLUMNS,
            file=file,
            section="Sharesight trades header",
        )
        if TradeColumn.COMMENTS not in header_columns:
            LOGGER.warning(
                "Sharesight trades report has no Comments column; "
                "Stock Activity markers cannot be detected"
            )

        for row in rows:
            if not any(row):
                # There is an empty row at the end of the trades list
                break

            if len(row) != len(header):
                raise UnexpectedColumnCountError(row, len(header), file)

            row_dict = {
                column: value
                for column, value in zip(header_columns, row, strict=True)
                if column is not None
            }

            trade_type = row_dict[TradeColumn.TYPE]
            if trade_type == "Buy":
                action = ActionType.BUY
            elif trade_type == "Sell":
                action = ActionType.SELL
            else:
                raise ValueError(f"Unknown action: {trade_type}")

            market = row_dict[TradeColumn.MARKET]
            symbol = f"{market}:{row_dict[TradeColumn.CODE]}"
            trade_date = cls._parse_date(row_dict[TradeColumn.DATE])
            quantity = cls._parse_decimal(row_dict, TradeColumn.QUANTITY)
            price = cls._parse_decimal(row_dict, TradeColumn.PRICE)
            fees = cls._maybe_decimal(row_dict, TradeColumn.BROKERAGE) or Decimal(0)
            currency = CurrencyCode(row_dict[TradeColumn.CURRENCY])
            description = row_dict.get(TradeColumn.COMMENTS, "")
            broker = "Sharesight"
            gbp_value = cls._maybe_decimal(row_dict, TradeColumn.VALUE)

            # Sharesight's reports conventions are slightly different from our
            # conventions:
            # - Quantity is negative on sell
            # - Amount is negative when selling, and positive when buying
            #   (as it tracks portfolio value, not account balance)
            # - Value provided is always in GBP
            # - Foreign exchange transactions are shown as BUY and SELL

            if market == "FX":
                # While Sharesight provides an exchange rate, it is not precise enough
                # for cryptocurrency transactions
                if not gbp_value:
                    raise ValueError("Missing Value in FX transaction")

                price = abs(gbp_value / quantity)
                currency = CurrencyCode("GBP")

            # Checked after the FX override so the fee is compared with the
            # currency the transaction is actually recorded in.
            brokerage_currency = row_dict.get(
                TradeColumn.BROKERAGE_CURRENCY, currency
            ).strip()
            if fees and brokerage_currency and brokerage_currency != currency:
                raise ValueError(
                    "Brokerage Currency "
                    f"{brokerage_currency!r} differs from transaction currency "
                    f"{currency!r}; fees in another currency are not supported"
                )

            # Make amount positive on sell and negative on buy
            amount = -(quantity * price) - fees
            # Make quantity always positive
            quantity = abs(quantity)

            # Create the transaction object now so we can report it in exceptions
            transaction = SharesightTransaction(
                date=trade_date,
                action=action,
                symbol=symbol,
                description=description,
                quantity=quantity,
                price=price,
                fees=fees,
                amount=amount,
                currency=currency,
                broker=broker,
                source=TransactionSource(row=rows.line),
            )

            # Sharesight has no native support for stock activity, so use a string
            # in the trade comment to mark it
            if STOCK_ACTIVITY_COMMENT_MARKER.lower() in description.lower():
                # Stock activity that is not a grant is weird and unsupported
                if action != ActionType.BUY:
                    raise InvalidTransactionError(
                        transaction, "Stock activity must have Type=Buy."
                    )

                transaction.action = ActionType.STOCK_ACTIVITY
                transaction.amount = None

            yield transaction

    @classmethod
    def _parse_trade_report(
        cls, file: TextIO, file_path: Path
    ) -> Iterable[SharesightTransaction]:
        """Parse All Trades Report from Sharesight."""
        rows = list(csv.reader(file))

        # Use our custom iterator for error reporting
        rows_iter = RowIterator(rows)
        for row in rows_iter:
            # Skip everything until we find the header
            if (
                "Code" in row
                and "Type" in row
                and ("Market" in row or "Market Code" in row)
            ):
                header = row
                try:
                    yield from cls._parse_trades(header, rows_iter, file_path)
                except ParsingError as err:
                    err.add_row_context(rows_iter.line)
                    raise
                except ValueError as err:
                    raise ParsingError(
                        file_path, str(err), row_index=rows_iter.line
                    ) from err
