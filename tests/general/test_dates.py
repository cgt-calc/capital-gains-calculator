"""Tests for date helpers."""

from __future__ import annotations

import datetime as dt

import pytest

from cgt_calc.dates import get_tax_year_end, get_tax_year_start, is_date


def test_is_date() -> None:
    """Accept plain dates."""
    assert is_date(dt.date(2023, 1, 1)) is True


def test_is_date_rejects_datetime() -> None:
    """Reject datetime instances."""
    with pytest.raises(TypeError, match=r"should be datetime\.date"):
        is_date(dt.datetime(2023, 1, 1, 12, 0))


def test_tax_year_bounds() -> None:
    """Tax years run from 6 April to 5 April."""
    assert get_tax_year_start(2023) == dt.date(2023, 4, 6)
    assert get_tax_year_end(2023) == dt.date(2024, 4, 5)
