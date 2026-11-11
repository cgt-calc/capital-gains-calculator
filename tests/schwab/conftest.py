"""Shared fixtures for the Schwab tests."""

import pytest

from cgt_calc.parsers.schwab import AwardPrices, SchwabParser


@pytest.fixture(autouse=True)
def _reset_award_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep one test's class-level award settings out of the next."""
    monkeypatch.setattr(SchwabParser, "awards_prices", AwardPrices(award_prices={}))
    monkeypatch.setattr(SchwabParser, "use_award_date", True)
