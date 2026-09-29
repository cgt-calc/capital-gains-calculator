"""Vanguard transaction parser."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal
from enum import StrEnum
import io
import re
from typing import TYPE_CHECKING, ClassVar, Final, TextIO, override

from cgt_calc.const import RENAME_DESCRIPTION_PREFIX
from cgt_calc.exceptions import ParsingError, UnexpectedColumnCountError
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    TransactionSource,
)
from cgt_calc.util import parse_decimal

from .base_parsers import BaseSingleFileParser

if TYPE_CHECKING:
    from pathlib import Path


class CashColumn(StrEnum):
    """Columns in the Cash Transactions table."""

    DATE = "Date"
    DETAILS = "Details"
    AMOUNT = "Amount"
    BALANCE = "Balance"


class InvestmentColumn(StrEnum):
    """Columns in the Investment Transactions table."""

    DATE = "Date"
    INVESTMENT_NAME = "InvestmentName"
    TRANSACTION_DETAILS = "TransactionDetails"
    QUANTITY = "Quantity"
    PRICE = "Price"
    COST = "Cost"


CASH_COLUMNS: Final[set[str]] = {c.value for c in CashColumn}
INVESTMENT_COLUMNS: Final[set[str]] = {c.value for c in InvestmentColumn}
# Column names (excluding Date) that signal a summary/footer row when they
# appear as the first cell of a row (e.g. "Balance,23.59" or "Cost,12545").
_SUMMARY_LABELS: Final[set[str]] = (CASH_COLUMNS | INVESTMENT_COLUMNS) - {"Date"}


class TableType(StrEnum):
    """Type of Vanguard CSV table."""

    CASH = "cash"
    INVESTMENT = "investment"


_SECTION_TYPES: Final[dict[str, TableType]] = {
    "Cash Transactions": TableType.CASH,
    "Investment Transactions": TableType.INVESTMENT,
}
# Errors name a table the way the export does, so both come from one mapping.
_SECTION_NAMES: Final[dict[TableType, str]] = {
    table_type: name for name, table_type in _SECTION_TYPES.items()
}


BOUGHT_RE = re.compile(r"^Bought ([\d,]*\.?\d+) (.+?)(?:\s*\(([^()]+)\))?$")
SOLD_RE = re.compile(r"^Sold ([\d,]*\.?\d+) (.+?)(?:\s*\(([^()]+)\))?$")
DIV_RE = re.compile(r"^DIV: ([^\.]+)\.[^ ]+ @ ([A-Z]+) (\d*[,\.]?\d*)")
TRANSFER_RE = re.compile(
    r".*(Regular Deposit|Cash transfer|Deposit via|Deposit for|Payment by|"
    r"Account [Ff]ee|ETF dealing fee).*"
)
NAMECHANGE_RE = re.compile(
    r"^NameChange:\s*([A-Z0-9]+)(?:\.\S+)?"
    r"(?:\s+replaced with\s+([A-Z0-9]+)(?:\.\S+)?)?\s*$"
)
INVESTMENT_NAME_RE = re.compile(r"^(.+?)(?:\s*\(([^()]+)\))?$")
# A cell opening with a date is the Date column of a row whose separators were
# lost, not decoration, however few cells the row was split into.
_DATE_PREFIX_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}\b")
_PAGE_LABEL_RE = re.compile(r"^page\s+\d+(?:\s*(?:of|/)\s*\d+)?$", re.IGNORECASE)

INTEREST_STR = "Cash Account Interest"
REVERSAL_STR = "Reversal of "


def action_from_str(label: str, file: Path) -> ActionType:
    """Convert label to ActionType."""

    if not label:
        raise ParsingError(file, "Missing action text in Details or TransactionDetails")

    if TRANSFER_RE.match(label):
        return ActionType.TRANSFER

    if BOUGHT_RE.match(label):
        return ActionType.BUY

    if SOLD_RE.match(label):
        return ActionType.SELL

    if DIV_RE.match(label):
        return ActionType.DIVIDEND

    if label == INTEREST_STR:
        return ActionType.INTEREST

    if label.startswith(REVERSAL_STR):
        reversed_label = label[len(REVERSAL_STR) :]
        # Supported reversals were stripped before this, so a trade reaching
        # here is one nothing can be done with. The row itself is understood,
        # unlike an unknown action, so say what is actually missing from it.
        if BOUGHT_RE.match(reversed_label) or SOLD_RE.match(reversed_label):
            raise ParsingError(
                file,
                f"Cannot import a reversed trade: {label}. A reversed purchase "
                "or disposal has to be reconciled with the original trade, "
                "which this row does not identify, and no export containing "
                "one has been examined, so cgt-calc cannot establish the "
                "correct treatment. Keep the export unchanged and please "
                "report this row so that the format can be supported. See "
                "https://cgt-calc.uk/brokers/vanguard/#a-reversed-purchase-or-disposal",
            )

    raise ParsingError(file, f"Unknown action: {label}")


def _parse_decimal(value: str, context: str) -> Decimal:
    """Parse decimal value, raising ValueError with contextual message on failure."""

    return parse_decimal(value, context, strip=",")


def _parse_details(
    details: str, action: ActionType, amount: Decimal
) -> tuple[str | None, Decimal | None, Decimal | None, CurrencyCode]:
    """Extract symbol, quantity, price, currency from a Details string."""
    currency = CurrencyCode("GBP")
    quantity = None
    price = None
    symbol = None
    if action == ActionType.BUY:
        match = BOUGHT_RE.match(details)
        assert match
        quantity = _parse_decimal(match.group(1), "Details quantity")
        symbol = match.group(3) or match.group(2).strip()
        price = abs(amount) / quantity
    elif action == ActionType.SELL:
        match = SOLD_RE.match(details)
        assert match
        quantity = _parse_decimal(match.group(1), "Details quantity")
        symbol = match.group(3) or match.group(2).strip()
        price = amount / quantity
    elif action == ActionType.DIVIDEND:
        match = DIV_RE.match(details)
        assert match
        symbol = match.group(1)
        currency = CurrencyCode(match.group(2))
        price = _parse_decimal(match.group(3), "Details price")
        quantity = Decimal(round(amount / price))
    return symbol, quantity, price, currency


def _strip_reversal(details: str) -> tuple[str, bool]:
    """Strip the prefix from a reversal of income or of a cash movement.

    Reversing those negates one exported amount and needs no matching. A
    reversed purchase or disposal instead has to be reconciled with the trade
    it refers to, so stripping the prefix would import it as a fresh trade in
    the wrong direction. Leave it prefixed: `action_from_str` recognises the
    prefix and reports what the row is missing.
    """
    if details.startswith(REVERSAL_STR):
        reversed_details = details[len(REVERSAL_STR) :]
        if (
            DIV_RE.match(reversed_details)
            or reversed_details == INTEREST_STR
            or TRANSFER_RE.match(reversed_details)
        ):
            return reversed_details, True
    return details, False


def _symbol_from_details(details: str) -> str | None:
    """Extract symbol from a buy/sell/dividend details string."""
    match = BOUGHT_RE.match(details) or SOLD_RE.match(details)
    if match:
        return match.group(3) or match.group(2).strip()
    match = DIV_RE.match(details)
    if match:
        return match.group(1)
    return None


def _symbol_from_investment_name(investment_name: str) -> str | None:
    """Extract symbol from an InvestmentName column value, e.g. 'Foo ETF (FOO)'."""
    match = INVESTMENT_NAME_RE.match(investment_name)
    if not match:
        return None
    return match.group(2) or match.group(1).strip()


class VanguardTransaction(BrokerTransaction):
    """Represents a single Vanguard transaction."""

    is_reversal: bool
    details_text: str

    def __init__(
        self,
        header: list[str],
        row_raw: list[str],
        file: Path,
    ):
        """Create transaction from a cash table CSV row."""
        if len(row_raw) != len(header):
            raise UnexpectedColumnCountError(row_raw, len(header), file)

        row = dict(zip(header, row_raw, strict=False))

        date = datetime.datetime.strptime(row[CashColumn.DATE], "%d/%m/%Y").date()
        self.details_text, self.is_reversal = _strip_reversal(row[CashColumn.DETAILS])
        self.action = action_from_str(self.details_text, file)
        self.amount = _parse_decimal(row[CashColumn.AMOUNT], CashColumn.AMOUNT.value)
        self.symbol, self.quantity, self.price, self.currency = _parse_details(
            self.details_text, self.action, self.amount
        )

        super().__init__(
            date=date,
            action=self.action,
            symbol=self.symbol,
            description="",
            quantity=self.quantity,
            price=self.price,
            fees=Decimal(0),
            amount=self.amount,
            currency=CurrencyCode("GBP"),
            broker="Vanguard",
            isin=None,
        )

    def enrich_details(self, investment_row: dict[str, str] | None = None) -> None:
        """Fill investment transaction details missing from the cash transaction table."""
        if investment_row is not None and self.quantity is None:
            assert self.amount is not None
            self.symbol = _symbol_from_details(self.details_text)
            self.quantity = abs(
                _parse_decimal(
                    investment_row[InvestmentColumn.QUANTITY],
                    InvestmentColumn.QUANTITY.value,
                )
            )
            self.price = abs(self.amount) / self.quantity

    @classmethod
    def from_fields(
        cls,
        date: datetime.date,
        action: ActionType,
        symbol: str | None,
        quantity: Decimal | None,
        price: Decimal | None,
        amount: Decimal | None,
        currency: CurrencyCode,
        *,
        is_reversal: bool,
        description: str = "",
    ) -> VanguardTransaction:
        """Create a VanguardTransaction from pre-parsed fields."""
        txn = object.__new__(cls)
        txn.is_reversal = is_reversal
        txn.details_text = ""
        BrokerTransaction.__init__(
            txn,
            date,
            action,
            symbol,
            description,
            quantity,
            price,
            Decimal(0),
            amount,
            currency,
            "Vanguard",
        )
        return txn


def _make_transaction_from_investment(
    row: dict[str, str],
    file: Path,
) -> VanguardTransaction | None:
    """Create a VanguardTransaction from an Investment row, or None for the NameChange zero-out half."""
    details, is_reversal = _strip_reversal(row[InvestmentColumn.TRANSACTION_DETAILS])
    date = datetime.datetime.strptime(row[InvestmentColumn.DATE], "%d/%m/%Y").date()
    quantity = _parse_decimal(
        row[InvestmentColumn.QUANTITY], InvestmentColumn.QUANTITY.value
    )
    if not details and quantity != 0:
        # Infer action (from the quantity sign) and symbol (from InvestmentName)
        # for pre ~Oct 2021 rows without TransactionDetails
        action = ActionType.BUY if quantity > 0 else ActionType.SELL
        symbol = _symbol_from_investment_name(row[InvestmentColumn.INVESTMENT_NAME])
    else:
        match = NAMECHANGE_RE.match(details)
        if match:
            old_ticker, new_ticker = match.group(1), match.group(2)
            # NameChange rows come in a zero-out/add-new pair; only the
            # "replaced with" half carries both tickers. Skip the other half.
            if not new_ticker:
                return None
            return VanguardTransaction.from_fields(
                date=date,
                action=ActionType.RENAME,
                symbol=new_ticker,
                quantity=Decimal(0),
                price=None,
                amount=Decimal(0),
                currency=CurrencyCode("GBP"),
                is_reversal=False,
                description=f"{RENAME_DESCRIPTION_PREFIX}{old_ticker}",
            )
        action = action_from_str(details, file)
        symbol = _symbol_from_details(details)

    return VanguardTransaction.from_fields(
        date=date,
        action=action,
        symbol=symbol,
        quantity=abs(quantity),
        price=_parse_decimal(row[InvestmentColumn.PRICE], InvestmentColumn.PRICE.value),
        amount=_parse_decimal(row[InvestmentColumn.COST], InvestmentColumn.COST.value),
        currency=CurrencyCode("GBP"),
        is_reversal=is_reversal,
    )


def by_date_and_action(
    transaction: VanguardTransaction,
) -> tuple[datetime.date, bool, bool, bool]:
    """Sort by date and action type."""
    # Deprioritize BUY and reversal transaction to prevent balance errors
    # Deprioritize debits (e.g. fee charged) so credits (e.g. fee cleared)
    # are processed first, preventing temporary negative balances.
    is_debit = transaction.amount is not None and transaction.amount < 0
    return (
        transaction.date,
        transaction.action == ActionType.BUY,
        is_debit,
        transaction.is_reversal,
    )


def _detect_delimiter(text: str) -> str:
    """Detect whether the file uses comma or tab as delimiter."""
    for delimiter in (",", "\t"):
        rows = csv.reader(io.StringIO(text), delimiter=delimiter)
        for row in rows:
            stripped = {cell.strip() for cell in row}
            if stripped >= CASH_COLUMNS or stripped >= INVESTMENT_COLUMNS:
                return delimiter

    # Header validation still produces the useful column error when the file
    # does not contain an exported header.
    first_line = text.split("\n", 1)[0]
    if "\t" in first_line:
        return "\t"
    return ","


def _trim_trailing_empty_cells(row: list[str], minimum_length: int = 0) -> list[str]:
    """Remove cosmetic empty columns without dropping required blank fields."""
    end = len(row)
    while end > minimum_length and not row[end - 1].strip():
        end -= 1
    return row[:end]


def _is_table_boundary(row: list[str]) -> bool:
    """Report whether the row starts a table, as a header or a section title."""
    stripped = {cell.strip() for cell in row}
    first = row[0].strip() if row else ""
    return (
        stripped >= CASH_COLUMNS
        or stripped >= INVESTMENT_COLUMNS
        or first in _SECTION_TYPES
    )


def _decoration_text(row: list[str]) -> str | None:
    """Return the lone text of a row that carries no transaction fields.

    A row whose separators were lost also collapses to one cell, so a cell
    opening with a date is reported as data rather than as decoration.
    """
    populated = [cell.strip() for cell in row if cell.strip()]
    if len(populated) != 1 or _DATE_PREFIX_RE.match(populated[0]):
        return None
    return populated[0]


def _is_collapsed_transaction(row: list[str]) -> bool:
    """Report whether a lone cell holds a row that lost its field separators.

    Used above the first header, where the columns are still unknown. A report
    date and a covered period are dated single cells too, so a delimiter after
    the date is required as evidence: a delimiter survives inside one cell only
    when the row was quoted whole or written with the wrong separator. Within a
    table the shape alone is enough, which is what `_decoration_text` reports.
    """
    populated = [cell.strip() for cell in row if cell.strip()]
    if len(populated) != 1:
        return False
    match = _DATE_PREFIX_RE.match(populated[0])
    if match is None:
        return False
    remainder = populated[0][match.end() :]
    return any(delimiter in remainder for delimiter in (",", ";", "\t"))


def _trailing_decoration(lines: list[tuple[int, list[str]]]) -> set[int]:
    """Find the positions of decorative rows that no table data follows.

    Legacy footers sit below the last transaction of their table. A decorative
    row anywhere above one is only ignored when it is a recognised pagination
    label, so that a mangled transaction cannot pass as a footer.
    """
    trailing: set[int] = set()
    below_last_row = True
    for position in reversed(range(len(lines))):
        row = lines[position][1]
        if not any(cell.strip() for cell in row):
            continue
        if _is_table_boundary(row):
            below_last_row = True
        elif _decoration_text(row) is not None:
            if below_last_row:
                trailing.add(position)
        else:
            below_last_row = False
    return trailing


def _looks_like_transaction(row: list[str]) -> bool:
    """Report whether the row opens with a parsable date and an activity."""
    cells = iter(row)
    date_text = next(cells, "")
    details = next(cells, "")
    if not details.strip():
        return False
    try:
        datetime.datetime.strptime(date_text.strip(), "%d/%m/%Y")
    except ValueError:
        return False
    return True


class _TableSplitter:
    """Split an export into its tables, refusing shapes that would lose rows.

    A Vanguard worksheet pages its tables, so section titles, headers, running
    summaries and footers repeat throughout the file. Each is only accepted
    where it cannot hide a transaction that the reader would otherwise drop.
    """

    def __init__(self, lines: list[tuple[int, list[str]]], file_path: Path):
        """Prepare to read the rows of one exported file."""
        self.lines = lines
        self.file_path = file_path
        self.trailing = _trailing_decoration(lines)
        self.tables: dict[TableType, list[tuple[int, list[str]]]] = {}
        self.current: list[tuple[int, list[str]]] = []
        self.current_type: TableType | None = None
        # A section title whose header has not been recognised yet.
        self.closed_type: TableType | None = None
        self.after_summary = False
        self.seen_types: set[TableType] = set()
        self.seen_sections: set[TableType] = set()
        self.orphan_row_index: int | None = None

    def split(
        self,
    ) -> tuple[
        list[tuple[int, list[str]]] | None,
        list[tuple[int, list[str]]] | None,
    ]:
        """Return the cash and investment tables, or None where absent."""
        for position, (row_index, row) in enumerate(self.lines):
            self._read_row(row, position, row_index)
        self._flush()
        if self.orphan_row_index is not None and self.tables:
            raise ParsingError(
                self.file_path,
                "Found a transaction row above the first table header",
                row_index=self.orphan_row_index,
            )
        return self.tables.get(TableType.CASH), self.tables.get(TableType.INVESTMENT)

    def _read_row(self, row: list[str], position: int, row_index: int) -> None:
        stripped = {cell.strip() for cell in row}
        first = row[0].strip() if row else ""
        section_type = _SECTION_TYPES.get(first)
        if stripped >= CASH_COLUMNS:
            self._start_table(TableType.CASH, row, row_index)
        elif stripped >= INVESTMENT_COLUMNS:
            self._start_table(TableType.INVESTMENT, row, row_index)
        elif section_type is not None:
            self._start_section(section_type, row_index)
        elif not any(cell.strip() for cell in row):
            return
        elif self.current_type is not None:
            self._add_row(row, position, row_index, first)
        elif self.closed_type is not None:
            self._reject_row_under_title(row, position, row_index)
        elif self.orphan_row_index is None and (
            _looks_like_transaction(row) or _is_collapsed_transaction(row)
        ):
            # Remember rather than raise: when no table is found at all, the
            # header validator has the more useful message about the columns.
            self.orphan_row_index = row_index

    def _flush(self) -> None:
        if self.current_type is not None:
            self.tables[self.current_type] = list(self.current)

    def _start_table(
        self, table_type: TableType, row: list[str], row_index: int
    ) -> None:
        # Header cells become the row keys, so pad them out here rather than
        # letting a cosmetically spaced heading miss its column.
        header = [cell.strip() for cell in _trim_trailing_empty_cells(row)]
        if self.closed_type is not None and self.closed_type is not table_type:
            raise ParsingError(
                self.file_path,
                f"Found the {_SECTION_NAMES[table_type]} table header under the "
                f"{_SECTION_NAMES[self.closed_type]} section title",
                row_index=row_index,
            )
        if table_type in self.seen_types:
            if (
                self.current_type is table_type
                and self.current
                and header == self.current[0][1]
            ):
                # Paginated exports can repeat the unchanged table header.
                self.after_summary = False
                return
            raise ParsingError(
                self.file_path,
                f"Multiple {_SECTION_NAMES[table_type]} table headers found",
                row_index=row_index,
            )
        self._flush()
        self.seen_types.add(table_type)
        self.current = [(row_index, header)]
        self.current_type = table_type
        self.closed_type = None
        self.after_summary = False

    def _start_section(self, section_type: TableType, row_index: int) -> None:
        self.after_summary = False
        if self.current_type is section_type:
            # A page can repeat its section title before repeating the
            # unchanged header. Keep appending to the active table.
            return
        if section_type in self.seen_sections or section_type in self.seen_types:
            raise ParsingError(
                self.file_path,
                f"Multiple {_SECTION_NAMES[section_type]} section titles found",
                row_index=row_index,
            )
        self.seen_sections.add(section_type)
        self._flush()
        self.current = []
        self.current_type = None
        self.closed_type = section_type

    def _add_row(
        self, row: list[str], position: int, row_index: int, first: str
    ) -> None:
        assert self.current_type is not None
        if first in _SUMMARY_LABELS:
            # A running summary can end a page. Require a repeated title or
            # header before accepting more transactions from the same table.
            self.after_summary = True
        elif self._is_decoration(row, position):
            return
        elif self.after_summary and _looks_like_transaction(row):
            raise ParsingError(
                self.file_path,
                "Found a transaction row after the "
                f"{_SECTION_NAMES[self.current_type]} summary",
                row_index=row_index,
            )
        else:
            width = len(self.current[0][1])
            self.current.append((row_index, _trim_trailing_empty_cells(row, width)))

    def _reject_row_under_title(
        self, row: list[str], position: int, row_index: int
    ) -> None:
        # The section title was announced but its header was not recognised, so
        # the columns of everything below it are unknown.
        assert self.closed_type is not None
        if self._is_decoration(row, position):
            return
        raise ParsingError(
            self.file_path,
            f"Found a row outside the {_SECTION_NAMES[self.closed_type]} table",
            row_index=row_index,
        )

    def _is_decoration(self, row: list[str], position: int) -> bool:
        text = _decoration_text(row)
        return text is not None and (
            position in self.trailing or bool(_PAGE_LABEL_RE.fullmatch(text))
        )


def _find_investment_match(
    txn: VanguardTransaction,
    investment_lookup: dict[tuple[str, bool], list[dict[str, str]]],
) -> dict[str, str] | None:
    """Find the best matching investment row for a cash transaction.

    Rows are keyed by their activity text and whether they reverse it, so a
    reversal is enriched from the matching reversal rather than the original.

    When multiple investment rows share the same TransactionDetails key:
    - BUY: earliest investment date on or after the cash date (cash transaction happen first, then investment buy)
    - SELL: latest investment date on or before the cash date (investment sell happen first, then cash in)
    """
    candidates = investment_lookup.get((txn.details_text, txn.is_reversal))
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    def _investment_date(r: dict[str, str]) -> datetime.date:
        return datetime.datetime.strptime(r[InvestmentColumn.DATE], "%d/%m/%Y").date()

    if txn.action == ActionType.BUY:
        after = [r for r in candidates if _investment_date(r) >= txn.date]
        return min(after, key=_investment_date) if after else None
    # SELL or other: latest date on or before the cash date
    before = [r for r in candidates if _investment_date(r) <= txn.date]
    return max(before, key=_investment_date) if before else None


class VanguardParser(BaseSingleFileParser[VanguardTransaction]):
    """Parser for Vanguard transaction files."""

    arg_name = "vanguard"
    pretty_name = "Vanguard"
    format_name = "CSV"
    deprecated_flags: ClassVar[list[str]] = ["--vanguard"]

    @classmethod
    @override
    def load_from_file(
        cls,
        file_path: Path,
        *,
        warn_on_empty: bool = True,
        show_parsing_msg: bool = True,
        account: str | None = None,
    ) -> list[VanguardTransaction]:
        """Load a text CSV and report Excel or encoding mistakes clearly."""
        if file_path.suffix.casefold() in {".xls", ".xlsx", ".xlsm", ".xlsb"}:
            raise ParsingError(
                file_path,
                "Vanguard Excel workbooks are not supported; save the General "
                "Account worksheet as a CSV file.",
            )
        try:
            return super().load_from_file(
                file_path,
                warn_on_empty=warn_on_empty,
                show_parsing_msg=show_parsing_msg,
                account=account,
            )
        except UnicodeDecodeError as err:
            raise ParsingError(
                file_path,
                "Vanguard input must be a UTF-8 CSV text file.",
            ) from err

    @classmethod
    def _validate_cash_header(cls, header: list[str], file: Path) -> None:
        """Check if header matches the cash transactions columns."""
        expected = [c.value for c in CashColumn]
        if len(header) != len(expected):
            raise UnexpectedColumnCountError(header, len(expected), file, row_index=1)
        for index, (exp, act) in enumerate(zip(expected, header, strict=True), start=1):
            if exp != act:
                raise ParsingError(
                    file,
                    f"Expected column {index} to be '{exp}' but found '{act}'",
                    row_index=1,
                )

    @classmethod
    @override
    def read_transactions(
        cls, file: TextIO, file_path: Path
    ) -> list[VanguardTransaction]:
        """Read Vanguard transactions from exported transaction report file."""
        raw_text = file.read().removeprefix("\ufeff")
        delimiter = _detect_delimiter(raw_text)
        csv_reader = csv.reader(io.StringIO(raw_text), delimiter=delimiter)
        lines = [(csv_reader.line_num, row) for row in csv_reader]

        if not lines:
            raise ParsingError(file_path, "Vanguard CSV file is empty")

        cash_lines, investment_lines = _TableSplitter(lines, file_path).split()

        if cash_lines is None:
            if investment_lines is None:
                cls._validate_cash_header(
                    _trim_trailing_empty_cells(lines[0][1]), file_path
                )
            raise ParsingError(
                file_path,
                "Vanguard input must include the Cash Transactions table; "
                "an Investment Transactions table cannot be calculated alone.",
            )

        # RENAMEs emit immediately; other investment rows populate the cash lookup.
        investment_lookup: dict[tuple[str, bool], list[dict[str, str]]] = {}
        transactions: list[VanguardTransaction] = []
        if investment_lines is not None:
            _, investment_header = investment_lines[0]
            investment_rows: list[tuple[int, dict[str, str]]] = []
            for row_index, row in investment_lines[1:]:
                if len(row) != len(investment_header):
                    raise UnexpectedColumnCountError(
                        row,
                        len(investment_header),
                        file_path,
                        row_index=row_index,
                    )
                investment_rows.append(
                    (row_index, dict(zip(investment_header, row, strict=False)))
                )

            for row_index, investment_row in investment_rows:
                # Only a RENAME keeps its transaction, but every row is read:
                # reading is what rejects an unknown action or an unreadable
                # number here. Skipping it for the rest would accept them
                # silently, so do not narrow this call to NameChange rows.
                try:
                    txn = _make_transaction_from_investment(investment_row, file_path)
                except ParsingError as err:
                    err.add_row_context(row_index)
                    raise
                except ValueError as err:
                    raise ParsingError(
                        file_path, str(err), row_index=row_index
                    ) from err
                if txn is None:
                    continue  # zero-out half of a NameChange pair
                txn.source = TransactionSource(row=row_index)
                if txn.action is ActionType.RENAME:
                    transactions.append(txn)
                    continue
                # Key on the same normalisation the cash side looks up with,
                # otherwise a reversal never finds its investment row.
                details, is_reversal = _strip_reversal(
                    investment_row[InvestmentColumn.TRANSACTION_DETAILS]
                )
                if details:
                    key = (details, is_reversal)
                    investment_lookup.setdefault(key, []).append(investment_row)

        _, cash_header = cash_lines[0]
        for row_index, row in cash_lines[1:]:
            if len(row) != len(cash_header):
                raise UnexpectedColumnCountError(
                    row,
                    len(cash_header),
                    file_path,
                    row_index=row_index,
                )
            try:
                cash_txn = VanguardTransaction(cash_header, row, file_path)
            except ParsingError as err:
                err.add_row_context(row_index)
                raise
            except ValueError as err:
                raise ParsingError(file_path, str(err), row_index=row_index) from err
            cash_txn.source = TransactionSource(row=row_index)
            transactions.append(cash_txn)

        # Resolve missing quantity/price with values from the investment table, see
        # https://github.com/cgt-calc/capital-gains-calculator/issues/758
        for txn in transactions:
            matched_inv = _find_investment_match(txn, investment_lookup)
            txn.enrich_details(matched_inv)

        transactions.sort(key=by_date_and_action)
        return transactions
