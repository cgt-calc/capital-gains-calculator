"""Tests for the broker registry."""

from __future__ import annotations

import csv
import datetime
from decimal import Decimal
import io
import logging
from typing import TYPE_CHECKING

import pytest

from cgt_calc.args_parser import create_parser
from cgt_calc.const import RENAME_DESCRIPTION_PREFIX
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode, Isin
from cgt_calc.parsers.base_parsers import BaseParser, BaseSingleFileParser
from cgt_calc.parsers.broker_registry import BrokerRegistry, _resolve_isins
from cgt_calc.parsers.eri.raw import ERIRawParser
from cgt_calc.parsers.raw import RawParser
from cgt_calc.parsers.vanguard import VanguardTransaction

if TYPE_CHECKING:
    from pathlib import Path


def _vanguard_warning(caplog: pytest.LogCaptureFixture) -> str:
    """Return the unmapped-symbol warning, whatever else the run logged."""
    warnings = [
        record.getMessage()
        for record in caplog.records
        if "no ISIN mapping was found" in record.getMessage()
    ]
    assert len(warnings) == 1, f"Expected exactly one warning, got {warnings}"
    return warnings[0]


def _write_vanguard_csv(tmp_path: Path, fund_name: str = "Foo Fund") -> Path:
    """Write a one-row Vanguard cash table whose symbol is a fund name."""
    vanguard_file = tmp_path / "vanguard.csv"
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Date", "Details", "Amount", "Balance"])
    writer.writerow(["09/03/2022", f"Bought 10 {fund_name}", "-100.00", "0"])
    vanguard_file.write_text(buffer.getvalue(), encoding="utf-8")
    return vanguard_file


def test_load_all_transactions_without_files(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Warn when no transactions are found."""
    args = create_parser().parse_args(["--year", "2023"])

    with caplog.at_level(logging.WARNING):
        transactions = BrokerRegistry.load_all_transactions(args, IsinConverter())

    assert transactions == []
    assert "Found 0 broker transactions" in caplog.text


def test_load_all_transactions_from_raw_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Load and sort transactions from a single broker."""
    raw_file = tmp_path / "raw.csv"
    raw_file.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2023-02-09,DIVIDEND,OPRA,4200,0.80,0.0,USD\n"
        "2022-11-14,SELL,META,19,116.00,0.05,USD\n"
    )
    args = create_parser().parse_args(["--year", "2023", "--raw-file", str(raw_file)])

    with caplog.at_level(logging.INFO):
        transactions = BrokerRegistry.load_all_transactions(args, IsinConverter())

    assert len(transactions) == 2
    assert "Loaded 2 transactions from RAW format" in caplog.text
    # Transactions are sorted by date.
    assert [str(transaction.date) for transaction in transactions] == [
        "2022-11-14",
        "2023-02-09",
    ]


@pytest.mark.parametrize(
    "fund_name",
    ["Foo Fund", "World ex-U.K. Equity Index Fund, Acc", 'Fund "A" Shares'],
)
def test_vanguard_warning_lists_symbols_as_usable_csv_fields(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, fund_name: str
) -> None:
    """List each symbol as text that can be pasted into the cache verbatim."""
    vanguard_file = _write_vanguard_csv(tmp_path, fund_name)
    args = create_parser().parse_args(
        ["--year", "2023", "--vanguard-file", str(vanguard_file)]
    )

    with caplog.at_level(logging.WARNING):
        BrokerRegistry.load_all_transactions(
            args, IsinConverter(isin_translation_file=tmp_path / "isin.csv")
        )

    warning = _vanguard_warning(caplog)
    listed = warning.split("was found for: ")[1].split(". cgt-calc")[0]
    # The user copies this into the cache, so reading it back as CSV has to
    # return the symbol unchanged: a bare name that a comma would split, or
    # a quote kept as part of the symbol, would never match.
    assert next(csv.reader([f"IE00B50MZ724,{listed}"])) == ["IE00B50MZ724", fund_name]


def test_vanguard_symbol_without_isin_mapping_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Warn that ERI cannot be matched to an unmapped Vanguard symbol."""
    vanguard_file = _write_vanguard_csv(tmp_path)
    args = create_parser().parse_args(
        ["--year", "2023", "--vanguard-file", str(vanguard_file)]
    )

    cache = tmp_path / "isin_translation.csv"
    converter = IsinConverter(isin_translation_file=cache)

    with caplog.at_level(logging.WARNING):
        BrokerRegistry.load_all_transactions(args, converter)

    warning = _vanguard_warning(caplog)
    assert "no ISIN mapping was found for: Foo Fund" in warning
    assert f"--isin-translation-file cache at {cache}." in warning
    # Appending a row that drops an alias breaks a later run, so the advice
    # has to say how the cache is keyed, not just where it lives.
    assert "single row listing every symbol it is known by" in warning
    assert (
        "https://cgt-calc.uk/extra-data-and-options/#isin-to-ticker-translation"
        in warning
    )


def test_vanguard_warning_omits_cache_path_when_flag_is_cleared(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Do not advertise a cache location when the flag was explicitly cleared."""
    vanguard_file = _write_vanguard_csv(tmp_path)
    args = create_parser().parse_args(
        [
            "--year",
            "2023",
            "--vanguard-file",
            str(vanguard_file),
            "--isin-translation-file",
            "",
        ]
    )

    converter = IsinConverter(isin_translation_file=args.isin_translation_file)

    with caplog.at_level(logging.WARNING):
        BrokerRegistry.load_all_transactions(args, converter)

    warning = _vanguard_warning(caplog)
    assert "no ISIN mapping was found for: Foo Fund" in warning
    assert "--isin-translation-file cache. " in warning


def test_vanguard_symbol_with_isin_mapping_does_not_warn(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Do not warn when a Vanguard symbol has an explicit ISIN mapping."""
    vanguard_file = _write_vanguard_csv(tmp_path)
    args = create_parser().parse_args(
        ["--year", "2023", "--vanguard-file", str(vanguard_file)]
    )
    converter = IsinConverter()
    converter.data[Isin("US0378331005")] = {"Foo Fund"}

    with caplog.at_level(logging.WARNING):
        BrokerRegistry.load_all_transactions(args, converter)

    assert "no ISIN mapping was found" not in caplog.text


def test_vanguard_display_name_does_not_define_parser_provenance() -> None:
    """Ignore a non-Vanguard transaction whose broker label happens to match."""
    transaction = BrokerTransaction(
        date=datetime.date(2022, 3, 9),
        action=ActionType.BUY,
        symbol="Foo Fund",
        description="",
        quantity=Decimal(10),
        price=Decimal(10),
        fees=Decimal(0),
        amount=Decimal(-100),
        currency=CurrencyCode("GBP"),
        broker="Vanguard",
    )

    assert _unmapped([transaction], {}) == []


def test_vanguard_symbol_resolved_by_another_broker_does_not_warn() -> None:
    """Stay quiet when another broker's row already supplies the ISIN.

    ERI matches through that row, so telling the user to add a mapping would
    send them after a change that makes no difference.
    """
    vanguard = VanguardTransaction.from_fields(
        date=datetime.date(2022, 3, 9),
        action=ActionType.BUY,
        symbol="VWRP",
        quantity=Decimal(10),
        price=Decimal(10),
        amount=Decimal(-100),
        currency=CurrencyCode("GBP"),
        is_reversal=False,
    )
    elsewhere = BrokerTransaction(
        date=datetime.date(2022, 3, 9),
        action=ActionType.BUY,
        symbol="VWRP",
        description="",
        quantity=Decimal(5),
        price=Decimal(10),
        fees=Decimal(0),
        amount=Decimal(-50),
        currency=CurrencyCode("GBP"),
        broker="Trading212",
        isin=Isin("IE00BK5BQT80"),
    )

    isins, unmapped = _resolve_isins([vanguard, elsewhere], {})
    # The same pass hands ERI the ISIN the other broker's row supplies.
    assert isins == {Isin("IE00BK5BQT80")}
    assert unmapped == []
    assert _unmapped([vanguard], {}) == ["VWRP"]


def test_vanguard_blank_symbol_is_not_reported() -> None:
    """A whitespace-only symbol must not render as a phantom empty entry."""
    blank = VanguardTransaction.from_fields(
        date=datetime.date(2022, 3, 9),
        action=ActionType.BUY,
        symbol="",
        quantity=Decimal(1),
        price=Decimal(1),
        amount=Decimal(-1),
        currency=CurrencyCode("GBP"),
        is_reversal=False,
    )

    whitespace = VanguardTransaction.from_fields(
        date=datetime.date(2022, 3, 9),
        action=ActionType.BUY,
        symbol=" ",
        quantity=Decimal(1),
        price=Decimal(1),
        amount=Decimal(-1),
        currency=CurrencyCode("GBP"),
        is_reversal=False,
    )

    assert _unmapped([blank], {}) == []
    assert _unmapped([whitespace], {}) == []


def _unmapped(
    transactions: list[BrokerTransaction], isin_map: dict[str, Isin]
) -> list[str]:
    """Return just the unmapped-symbol half of the resolution."""
    return _resolve_isins(transactions, isin_map)[1]


def test_vanguard_symbol_renamed_to_a_mapped_symbol_does_not_warn() -> None:
    """A mapping for the new name also covers rows carrying the old one."""
    pre_rename = VanguardTransaction.from_fields(
        date=datetime.date(2022, 3, 9),
        action=ActionType.BUY,
        symbol="VDXX",
        quantity=Decimal(10),
        price=Decimal(10),
        amount=Decimal(-100),
        currency=CurrencyCode("GBP"),
        is_reversal=False,
    )
    rename = VanguardTransaction.from_fields(
        date=datetime.date(2022, 3, 10),
        action=ActionType.RENAME,
        symbol="VGER",
        quantity=Decimal(0),
        price=None,
        amount=Decimal(0),
        currency=CurrencyCode("GBP"),
        is_reversal=False,
        description=f"{RENAME_DESCRIPTION_PREFIX}VDXX",
    )
    mapped = {"VGER": Isin("IE00BK5BQT80")}

    assert _unmapped([pre_rename, rename], mapped) == []
    # Without the mapping both names are genuinely unmatchable.
    assert _unmapped([pre_rename, rename], {}) == ["VDXX", "VGER"]


@pytest.mark.parametrize(
    "parser",
    [*BrokerRegistry._BROKERS, ERIRawParser],  # noqa: SLF001
    ids=lambda parser: parser.pretty_name,
)
def test_only_the_raw_format_promises_rows_in_time_order(
    parser: type[BaseParser],
) -> None:
    """Only a hand-written RAW file states that a day's rows are in order.

    ``rows_in_time_order`` is what lets a row be placed either side of a
    same-day share reorganisation with no exported time to go on, so a parser
    that claims it without its format saying so would settle a tax question
    from the order a broker happened to export in. RAW earns it because
    docs/brokers/raw.md tells the user writing the file to keep that order.

    Every registered parser is pinned, not just RAW: the flag is inherited, so
    a parser added later by copying ``RawParser`` would otherwise claim the
    guarantee in silence.
    """
    assert issubclass(parser, BaseSingleFileParser)
    assert parser.rows_in_time_order is (parser is RawParser)
