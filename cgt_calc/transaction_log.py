"""Functions to work with HMRC transaction log."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .model import (
    DayAcquisitions,
    ExcessReportedIncome,
    HmrcTransactionData,
    HmrcTransactionLog,
)

if TYPE_CHECKING:
    import datetime as dt
    from decimal import Decimal

    from .model import AcquisitionLog


def has_key[T](
    transactions: dict[dt.date, dict[str, T]],
    date_index: dt.date,
    symbol: str,
) -> bool:
    """Check if transaction log has entry for date_index and symbol.

    Generic over what a log records, so that the acquisition log, whose
    entries keep a day's purchases apart from what a reorganisation or a fee
    put in, is asked the same way as the rest.
    """
    return date_index in transactions and symbol in transactions[date_index]


def day_acquisitions(
    current_list: AcquisitionLog,
    date_index: dt.date,
    symbol: str,
) -> DayAcquisitions:
    """Return what a day has put into a holding, starting an empty record."""
    return current_list.setdefault(date_index, {}).setdefault(symbol, DayAcquisitions())


def add_to_list(
    current_list: HmrcTransactionLog,
    date_index: dt.date,
    symbol: str,
    quantity: Decimal,
    amount: Decimal,
    fees: Decimal,
    eris: list[ExcessReportedIncome] | None = None,
) -> None:
    """Add entry to given transaction log."""
    current_list.setdefault(date_index, {})
    current_list[date_index].setdefault(symbol, HmrcTransactionData())
    current_list[date_index][symbol] += HmrcTransactionData(
        quantity=quantity, amount=amount, fees=fees, eris=eris or []
    )
