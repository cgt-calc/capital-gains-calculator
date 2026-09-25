"""Tests for IsinConverter network behaviour."""

from __future__ import annotations

import datetime
from decimal import Decimal
import logging
from typing import TYPE_CHECKING, NoReturn

import pytest
from requests import exceptions as requests_exceptions

from cgt_calc.const import RuntimeMode
from cgt_calc.exceptions import (
    ExternalApiError,
    InvalidTransactionError,
    IsinTranslationError,
    ParsingError,
    UnexpectedColumnCountError,
)
import cgt_calc.isin_converter
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode, Isin

if TYPE_CHECKING:
    from pathlib import Path

# Well-formed but unassigned, so it cannot be in the bundled translation data.
UNKNOWN_ISIN = Isin("ZZ0000000008")


class OfflineSession:
    """Session stub that fails every request."""

    def post(
        self,
        url: str,
        json: list[dict[str, str]],
        headers: dict[str, str],
        timeout: int,
    ) -> NoReturn:
        """Simulate a network failure."""
        raise requests_exceptions.ConnectionError(f"offline: {url}")


def test_live_isin_lookup_announces_itself(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live OpenFIGI lookup logs a progress line before the request."""
    converter = IsinConverter()
    monkeypatch.setattr(converter, "session", OfflineSession())

    with caplog.at_level(logging.INFO), pytest.raises(ExternalApiError):
        converter.get_symbols(UNKNOWN_ISIN)

    assert f"Looking up ISIN {UNKNOWN_ISIN} via OpenFIGI..." in caplog.text


# Real, valid ISINs that are not in the bundled translation data.
ISIN_A = Isin("US0378331005")
ISIN_B = Isin("US5949181045")

# ISINs with a curated exchange alias.
NVIDIA_ISIN = Isin("US67066G1040")
BROADCOM_ISIN = Isin("US11135F1012")

# A bundled row listing one security under two tickers.
VANGUARD_ISIN = Isin("IE00B3XXRP09")

FigiData = list[dict[str, str]]


class FakeResponse:
    """Canned OpenFIGI response."""

    def __init__(self, payload: list[dict[str, FigiData]]) -> None:
        """Store the payload."""
        self._payload = payload
        self.text = str(payload)

    def json(self) -> list[dict[str, FigiData]]:
        """Return the payload."""
        return self._payload


class FakeSession:
    """Session stub returning a canned response."""

    def __init__(self, payload: list[dict[str, FigiData]]) -> None:
        """Store the payload."""
        self._payload = payload
        self.calls = 0

    def post(
        self,
        url: str,
        json: list[dict[str, str]],
        headers: dict[str, str],
        timeout: int,
    ) -> FakeResponse:
        """Return the canned response."""
        self.calls += 1
        return FakeResponse(self._payload)


def _transaction(isin: Isin | None, symbol: str | None) -> BrokerTransaction:
    """Create a minimal transaction with the given ISIN and symbol."""
    return BrokerTransaction(
        date=datetime.date(2023, 1, 1),
        action=ActionType.BUY,
        symbol=symbol,
        description="test",
        quantity=Decimal(1),
        price=Decimal(1),
        fees=Decimal(0),
        amount=Decimal(-1),
        currency=CurrencyCode("USD"),
        broker="Test",
        isin=isin,
    )


def _converter_with_session(
    monkeypatch: pytest.MonkeyPatch, payload: list[dict[str, FigiData]]
) -> tuple[IsinConverter, FakeSession]:
    """Create a converter with a canned OpenFIGI session."""
    converter = IsinConverter()
    session = FakeSession(payload)
    monkeypatch.setattr(converter, "session", session)
    return converter, session


def test_fetch_live_prefers_london_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefer LN tickers and cache the result."""
    converter, session = _converter_with_session(
        monkeypatch,
        [
            {
                "data": [
                    {"ticker": "FOO", "exchCode": "LN"},
                    {"ticker": "FOOL", "exchCode": "LI"},
                    {"ticker": "FOOUS", "exchCode": "UN"},
                ]
            }
        ],
    )

    assert converter.get_symbols(ISIN_A) == {"FOO"}
    # The second lookup is served from the cache.
    assert converter.get_symbols(ISIN_A) == {"FOO"}
    assert session.calls == 1


def test_fetch_live_falls_back_to_uk_exchanges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fall back to other UK exchanges when LN is missing."""
    converter, _ = _converter_with_session(
        monkeypatch,
        [
            {
                "data": [
                    {"ticker": "FOOL", "exchCode": "LI"},
                    {"ticker": "FOOUS", "exchCode": "UN"},
                ]
            }
        ],
    )

    assert converter.get_symbols(ISIN_A) == {"FOOL"}


def test_fetch_live_falls_back_to_shortest_ticker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the shortest ticker when no UK exchange is available."""
    converter, _ = _converter_with_session(
        monkeypatch,
        [
            {
                "data": [
                    {"ticker": "FOOLONG", "exchCode": "UN"},
                    {"ticker": "FOO", "exchCode": "UW"},
                ]
            }
        ],
    )

    assert converter.get_symbols(ISIN_A) == {"FOO"}


def test_fetch_live_invalid_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return no symbols on unexpected responses."""
    converter, _ = _converter_with_session(monkeypatch, [{}])

    assert converter.get_symbols(ISIN_A) == set()


def test_fetch_live_no_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return no symbols when the response has no tickers."""
    converter, _ = _converter_with_session(monkeypatch, [{"data": []}])

    assert converter.get_symbols(ISIN_A) == set()


def test_validate_data_rejects_empty_symbol() -> None:
    """Reject ticker lists containing empty values."""
    converter = IsinConverter()
    converter.data = {ISIN_A: {"FOO", ""}}

    with pytest.raises(IsinTranslationError, match="contains an empty value"):
        converter.validate_data()


def test_validate_data_rejects_conflicting_symbols() -> None:
    """Reject the same ticker linked to two ISINs."""
    converter = IsinConverter()
    converter.data = {ISIN_A: {"FOO"}, ISIN_B: {"FOO"}}

    with pytest.raises(IsinTranslationError, match="already linked"):
        converter.validate_data()


def test_add_from_transaction_rejects_two_transaction_symbols() -> None:
    """Reject a second, unrecognised ticker for an ISIN this run has seen.

    Both tickers come from transactions, so the holding really would be
    pooled and matched as two.
    """
    converter = IsinConverter()
    converter.add_from_transaction(_transaction(ISIN_A, "FOO"))

    with pytest.raises(InvalidTransactionError, match="does not match FOO"):
        converter.add_from_transaction(_transaction(ISIN_A, "BAR"))


def test_add_from_transaction_accepts_reference_only_conflict() -> None:
    """Accept a ticker that only disagrees with stored reference data.

    A cache row left by an earlier run says FOO; this run's transactions all
    say BAR. Nothing splits, so the run has to go through with BAR recorded
    as another name for the ISIN.
    """
    converter = IsinConverter()
    converter.data[ISIN_A] = {"FOO"}

    transaction = _transaction(ISIN_A, "BAR")
    converter.add_from_transaction(transaction)

    assert transaction.symbol == "BAR"
    assert converter.get_symbols(ISIN_A) == {"FOO", "BAR"}


@pytest.mark.parametrize(
    ("isin", "alias", "canonical"),
    [(NVIDIA_ISIN, "NVD", "NVDA"), (BROADCOM_ISIN, "1YD", "AVGO")],
)
def test_add_from_transaction_normalises_exchange_alias(
    isin: Isin, alias: str, canonical: str
) -> None:
    """Rewrite a known exchange alias, on the transaction itself."""
    converter = IsinConverter()

    aliased = _transaction(isin, alias)
    converter.add_from_transaction(aliased)
    converter.add_from_transaction(_transaction(isin, canonical))

    assert aliased.symbol == canonical
    assert converter.get_symbols(isin) == {canonical}


def test_add_from_transaction_normalises_alias_resolved_from_reference() -> None:
    """Rewrite a known alias on a row with no ISIN of its own, too.

    docs/extra-data-and-options.md documents listing every verified ticker
    for an ISIN on one cache row, such as ``US67066G1040,NVD,NVDA``. A broker
    that never reports an ISIN still has its ISIN resolved from that row, so
    the alias must be applied there as well - resolving the ISIN without
    then normalising the ticker would leave NVD and NVDA pooling separately
    exactly as before.
    """
    converter = IsinConverter()
    converter.data[NVIDIA_ISIN] = {"NVD", "NVDA"}

    aliased = _transaction(None, "NVD")
    converter.add_from_transaction(aliased)
    converter.add_from_transaction(_transaction(None, "NVDA"))

    assert aliased.symbol == "NVDA"
    assert converter.transaction_symbols[NVIDIA_ISIN] == {"NVDA"}


def test_add_from_transaction_alias_is_scoped_to_its_isin() -> None:
    """The same ticker code under another ISIN is left alone.

    Ticker codes belong to an exchange, not to a security, so NVD is only
    NVDA for the ISIN it was confirmed against.
    """
    converter = IsinConverter()

    unrelated = _transaction(ISIN_A, "NVD")
    converter.add_from_transaction(unrelated)

    assert unrelated.symbol == "NVD"


def test_add_from_transaction_rejects_one_ticker_under_two_isins() -> None:
    """Reject a ticker this run has already tied to a different ISIN.

    Both claims come from transactions, so the two securities would be pooled
    and matched as one holding. Reference data cannot excuse this one:
    validate_data already forbids a ticker linked to two ISINs there.
    """
    converter = IsinConverter()
    converter.add_from_transaction(_transaction(ISIN_A, "SAME"))

    with pytest.raises(InvalidTransactionError, match="already used for"):
        converter.add_from_transaction(_transaction(ISIN_B, "SAME"))


def test_add_from_transaction_rejects_reference_owned_ticker_reassignment() -> None:
    """A stored row giving the ticker to another ISIN is still a conflict.

    Unlike a same-ISIN mismatch, this direction cannot be a stale cache row:
    validate_data() already guarantees one ISIN per reference ticker, so a
    transaction reusing one reference gave to a different security is the
    real thing this check exists to catch, not a false positive to excuse.
    """
    converter = IsinConverter()
    converter.data[ISIN_B] = {"SAME"}

    with pytest.raises(InvalidTransactionError, match="already used for"):
        converter.add_from_transaction(_transaction(ISIN_A, "SAME"))


def test_add_from_transaction_checks_isin_less_rows_against_reference() -> None:
    """A row with no ISIN is still checked when reference data names one.

    A broker that never reports an ISIN would otherwise let its rows split a
    holding past every check above just by leaving the field blank: the
    ticker alone is enough to find the ISIN reference already links it to.
    """
    converter = IsinConverter()
    converter.data[ISIN_A] = {"FOO"}
    converter.add_from_transaction(_transaction(ISIN_A, "BAR"))

    with pytest.raises(InvalidTransactionError, match="does not match BAR"):
        converter.add_from_transaction(_transaction(None, "FOO"))


def test_add_from_transaction_links_isin_less_row_without_conflict() -> None:
    """A row with no ISIN is recorded once reference data resolves one."""
    converter = IsinConverter()
    converter.data[ISIN_A] = {"FOO"}

    converter.add_from_transaction(_transaction(None, "FOO"))

    assert converter.transaction_symbols[ISIN_A] == {"FOO"}


def test_add_from_transactions_reads_identities_before_normalising() -> None:
    """A row with no ISIN is named by a later row that carries one.

    IBKR puts the ISIN in a dividend description but not in a trade one, and
    the trades come first. Taking each row as it arrives leaves the earlier
    ones unresolved, so the export is read for identities first.
    """
    converter = IsinConverter()

    trade = _transaction(None, "NVD")
    converter.add_from_transactions([trade, _transaction(NVIDIA_ISIN, "NVD")])

    assert trade.symbol == "NVDA"
    assert converter.transaction_symbols[NVIDIA_ISIN] == {"NVDA"}


@pytest.mark.parametrize("reverse", [False, True], ids=["alias-first", "alias-last"])
def test_add_from_transactions_rejects_one_exported_ticker_for_two_isins(
    reverse: bool,
) -> None:
    """Refuse a ticker the export gives to two securities, in either order.

    The ownership check below cannot catch this one: the alias rewrite moves
    the NVD row that belongs to Nvidia to NVDA before the check runs, so the
    two NVD rows never collide there. Left to go through, the trade that
    names no ISIN of its own joins whichever listing the export stated first,
    which is the holding it lands in decided by row order.
    """
    converter = IsinConverter()
    identities = [_transaction(NVIDIA_ISIN, "NVD"), _transaction(ISIN_A, "NVD")]
    if reverse:
        identities.reverse()

    with pytest.raises(InvalidTransactionError, match="is exported for ISIN"):
        converter.add_from_transactions([_transaction(None, "NVD"), *identities])


def test_add_from_transaction_ignores_isin_less_unknown_ticker() -> None:
    """A row with no ISIN and no reference match records nothing."""
    converter = IsinConverter()

    converter.add_from_transaction(_transaction(None, "UNKNOWN"))

    assert converter.transaction_symbols == {}


def test_add_from_transaction_accepts_a_bundled_multi_ticker_row() -> None:
    """Accept every ticker of a bundled row listing a security under several.

    IE00B3XXRP09 ships as VUSA and VUSD, and those holdings pool separately
    today. That silent split predates the exchange-alias work and is not what
    this check is for, so both tickers go through as they always have.
    """
    converter = IsinConverter()
    assert converter.data[VANGUARD_ISIN] == {"VUSA", "VUSD"}

    converter.add_from_transaction(_transaction(VANGUARD_ISIN, "VUSA"))
    converter.add_from_transaction(_transaction(VANGUARD_ISIN, "VUSD"))

    assert converter.transaction_symbols[VANGUARD_ISIN] == {"VUSA", "VUSD"}


def test_add_from_transaction_ignores_rows_without_both_fields() -> None:
    """A row missing a ticker or an ISIN records nothing."""
    converter = IsinConverter()

    converter.add_from_transaction(_transaction(ISIN_A, None))

    assert converter.transaction_symbols == {}


def test_add_from_transaction_adds_mapping() -> None:
    """Add new mappings and expose them in the symbol map."""
    converter = IsinConverter()

    converter.add_from_transaction(_transaction(ISIN_A, "FOO"))

    assert converter.get_symbols(ISIN_A) == {"FOO"}
    assert converter.get_symbol_to_isin_map()["FOO"] == ISIN_A


def test_symbol_map_skips_unknown_tickers() -> None:
    """An ISIN recorded without a ticker must not claim the empty symbol.

    validate_data deliberately allows an ISIN whose ticker is unknown to be
    stored with an empty symbol. Letting that into the symbol map gives it an
    "" key, and callers resolving a transaction with no symbol - interest, a
    transfer, a fee - would then match that ISIN by accident.
    """
    converter = IsinConverter()
    converter.data[ISIN_A] = {""}
    converter.data[ISIN_B] = {"FOO"}

    symbol_map = converter.get_symbol_to_isin_map()

    assert "" not in symbol_map
    assert ISIN_A not in symbol_map.values()
    assert symbol_map["FOO"] == ISIN_B


def test_translation_file_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write looked-up mappings to the translation file and read them back."""
    monkeypatch.setattr(cgt_calc.isin_converter, "CGT_MODE", RuntimeMode.PROD)
    translation_file = tmp_path / "isin_translation.csv"

    converter = IsinConverter(isin_translation_file=translation_file)
    monkeypatch.setattr(
        converter,
        "session",
        FakeSession([{"data": [{"ticker": "FOO", "exchCode": "LN"}]}]),
    )
    assert converter.get_symbols(ISIN_A) == {"FOO"}

    assert (
        translation_file.read_text(encoding="utf-8") == f"ISIN,symbol\n{ISIN_A},FOO\n"
    )

    restored = IsinConverter(isin_translation_file=translation_file)
    assert restored.get_symbols(ISIN_A) == {"FOO"}


def test_translation_file_excludes_transaction_symbols(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never cache a ticker learned from a transaction.

    The file caches OpenFIGI lookups. A transaction ticker stored there is
    read back as reference data by the next run, which is how a history
    holding one listing of a security used to fail a later run holding the
    other.
    """
    monkeypatch.setattr(cgt_calc.isin_converter, "CGT_MODE", RuntimeMode.PROD)
    translation_file = tmp_path / "isin_translation.csv"

    converter = IsinConverter(isin_translation_file=translation_file)
    converter.add_from_transaction(_transaction(ISIN_A, "FOO"))

    assert converter.write_data == {}
    assert not translation_file.exists()


def test_live_lookup_refuses_a_ticker_another_isin_owns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse to cache a looked-up ticker the bundled table already assigns.

    The cache is read back alongside the bundled table, so writing the row
    would leave a file that fails the next run's construction.
    """
    monkeypatch.setattr(cgt_calc.isin_converter, "CGT_MODE", RuntimeMode.PROD)
    translation_file = tmp_path / "isin_translation.csv"
    converter = IsinConverter(isin_translation_file=translation_file)
    monkeypatch.setattr(
        converter,
        "session",
        FakeSession([{"data": [{"ticker": "VUSA", "exchCode": "LN"}]}]),
    )

    with pytest.raises(IsinTranslationError, match="already linked"):
        converter.get_symbols(UNKNOWN_ISIN)

    assert not translation_file.exists()


def test_translation_file_empty(tmp_path: Path) -> None:
    """Treat an empty translation file as no data."""
    translation_file = tmp_path / "isin_translation.csv"
    translation_file.write_text("")

    converter = IsinConverter(isin_translation_file=translation_file)

    assert converter.write_data == {}


def test_translation_file_bad_header(tmp_path: Path) -> None:
    """Raise on translation files with an unexpected header."""
    translation_file = tmp_path / "isin_translation.csv"
    translation_file.write_text("Wrong,Header\n")

    with pytest.raises(ParsingError, match="Unexpected header"):
        IsinConverter(isin_translation_file=translation_file)


def test_translation_file_invalid_row(tmp_path: Path) -> None:
    """Raise with row context on invalid translation rows."""
    translation_file = tmp_path / "isin_translation.csv"
    translation_file.write_text("ISIN,symbol\nNOTANISIN,FOO\n")

    with pytest.raises(ParsingError, match="invalid ISIN") as excinfo:
        IsinConverter(isin_translation_file=translation_file)

    assert excinfo.value.row_index == 2


def test_translation_file_row_without_a_symbol(tmp_path: Path) -> None:
    """Refuse a translation row that names no symbol, with its row number."""
    translation_file = tmp_path / "isin_translation.csv"
    translation_file.write_text(f"ISIN,symbol\n{ISIN_A}\n")

    with pytest.raises(UnexpectedColumnCountError) as excinfo:
        IsinConverter(isin_translation_file=translation_file)

    assert excinfo.value.row_index == 2
