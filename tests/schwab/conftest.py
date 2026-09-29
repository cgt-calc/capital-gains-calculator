"""Shared fixtures for the Schwab tests."""

import pytest

from cgt_calc.parsers.schwab import AwardPrices, SchwabParser


@pytest.fixture(autouse=True)
def _reset_awards_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep one test's class-level award prices out of the next."""
    monkeypatch.setattr(SchwabParser, "awards_prices", AwardPrices(award_prices={}))
