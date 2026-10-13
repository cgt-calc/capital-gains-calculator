"""Helpers for ERI importer tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    import datetime as dt
    from decimal import Decimal
    from pathlib import Path

    from cgt_calc.parsers.eri.model import ERITransaction


def write_xlsx(path: Path, rows: list[list[object]]) -> Path:
    """Write rows to an xlsx file as-is, without an implicit header."""
    pd.DataFrame(rows).to_excel(path, index=False, header=False)
    return path


def check_transaction(
    transaction: ERITransaction,
    isin: str,
    date: dt.date,
    price: Decimal,
    currency: str,
) -> None:
    """Assert the key fields of an ERI transaction."""
    assert transaction.isin == isin
    assert transaction.date == date
    assert transaction.price == price
    assert transaction.currency == currency
