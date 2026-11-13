"""Settlement pricing uses a fresh quote and the brokerage date together.

All transactions and prices here are invented. Network responses are replaced
at the Yahoo boundary so these tests exercise the same pricing path as a run.
"""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from cgt_calc.args_parser import create_parser
from cgt_calc.cli import calculate_cgt
from cgt_calc.currency_converter import CurrencyConverter
from cgt_calc.current_price_fetcher import CurrentPriceFetcher
from cgt_calc.exceptions import CgtError, InvalidTransactionError
from cgt_calc.isin_converter import IsinConverter
from cgt_calc.main import CapitalGainsCalculator
from cgt_calc.model import CurrencyCode, RuleType
from cgt_calc.parsers.schwab import AwardPrices, SchwabParser
from cgt_calc.share_prices import SharePrices
from cgt_calc.spin_off_handler import SpinOffHandler
from tests.general.test_current_price_fetcher import FakeHistoryTicker
from tests.schwab.helpers import load_via_cli

AWARD_DATE = dt.date(2024, 4, 30)
SETTLEMENT_DATE = dt.date(2024, 5, 1)
USD = CurrencyCode("USD")
HEADER = [
    "Date",
    "Action",
    "Symbol",
    "Description",
    "Quantity",
    "Price",
    "Fees & Comm",
    "Amount",
]


@pytest.fixture(autouse=True)
def _restore_parser_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave direct parser users with the state this test inherited."""
    monkeypatch.setattr(SchwabParser, "acquisition_date", "vesting")
    monkeypatch.setattr(SchwabParser, "awards_prices", AwardPrices({}))


def _main_history(
    tmp_path: Path,
    *,
    date: str = "05/01/2024 as of 04/30/2024",
    price: str = "",
    quantities: tuple[str, ...] = ("10",),
    sale: bool = False,
) -> Path:
    """Write the main history, newest first, optionally with a same-day sale."""
    path = tmp_path / "transactions.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADER)
        if sale:
            writer.writerow(
                ["05/01/2024", "Sell", "FOO", "FOO CORP", "10", "$20", "", "$200"]
            )
        for quantity in quantities:
            writer.writerow(
                [
                    date,
                    "Stock Plan Activity",
                    "FOO",
                    "FOO CORP",
                    quantity,
                    price,
                    "",
                    "",
                ]
            )
    return path


def _awards(tmp_path: Path, *, date: str = "04/30/2024") -> Path:
    """Write an award valuation which differs from the market quote."""
    path = tmp_path / "awards.csv"
    path.write_text(
        f"Date,Symbol,FairMarketValuePrice\n{date},FOO,\n,,$10\n", encoding="utf-8"
    )
    return path


def _market(
    monkeypatch: pytest.MonkeyPatch,
    *,
    price: float = 15,
    currency: str = "USD",
    days_late: int = 0,
) -> list[FakeHistoryTicker]:
    """Record lookups, returning the original daily close in its currency."""
    looked_up: list[FakeHistoryTicker] = []

    def ticker(symbol: str) -> FakeHistoryTicker:
        assert symbol == "FOO"
        result = FakeHistoryTicker(price, currency, days_late=days_late)
        looked_up.append(result)
        return result

    monkeypatch.setattr("yfinance.Ticker", ticker)
    return looked_up


def _no_quote(*args: object) -> None:
    raise AssertionError("The default must not fetch a historical quote")


@pytest.mark.parametrize("explicit", [False, True], ids=["default", "explicit-vesting"])
def test_vesting_keeps_the_award_date_and_price_without_a_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, explicit: bool
) -> None:
    """Existing users keep both their valuation and acquisition date."""
    monkeypatch.setattr(CurrentPriceFetcher, "get_closing_quote", _no_quote)
    flags = {"schwab_acquisition_date": "vesting"} if explicit else {}
    transactions = load_via_cli(
        schwab_file=str(_main_history(tmp_path)),
        schwab_award_file=str(_awards(tmp_path)),
        **flags,
    )

    assert [(row.date, row.price) for row in transactions] == [
        (AWARD_DATE, Decimal(10))
    ]


@pytest.mark.parametrize("export_price", ["", "$999"])
@pytest.mark.parametrize("award_given", [False, True])
def test_settlement_ignores_exported_prices_and_needs_no_award_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    export_price: str,
    *,
    award_given: bool,
) -> None:
    """The close replaces even an existing price; the award search is unused.

    The award is ten days earlier, beyond the old seven-day search window.
    """
    looked_up = _market(monkeypatch)
    flags = (
        {"schwab_award_file": str(_awards(tmp_path, date="04/21/2024"))}
        if award_given
        else {}
    )
    transactions = load_via_cli(
        schwab_file=str(_main_history(tmp_path, price=export_price)),
        schwab_acquisition_date="settlement",
        **flags,
    )

    assert [(row.date, row.price, row.currency) for row in transactions] == [
        (SETTLEMENT_DATE, Decimal(15), USD)
    ]
    assert len(looked_up) == 1
    assert looked_up[0].requested["start"] == "2024-05-01"
    assert looked_up[0].requested["auto_adjust"] is False


def test_grants_delivered_together_share_a_quote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each delivery is retained while one market request prices them all."""
    looked_up = _market(monkeypatch)
    transactions = load_via_cli(
        schwab_file=str(_main_history(tmp_path, quantities=("4", "6"))),
        schwab_acquisition_date="settlement",
    )

    assert sum(row.quantity or Decimal(0) for row in transactions) == Decimal(10)
    assert all(row.price == Decimal(15) for row in transactions)
    assert len(looked_up) == 1


def _calculator() -> CapitalGainsCalculator:
    """Give the two months deliberately different FX rates."""
    converter = CurrencyConverter(
        None,
        {AWARD_DATE: {USD: Decimal("1.25")}, SETTLEMENT_DATE: {USD: Decimal("1.5")}},
    )
    return CapitalGainsCalculator(
        2024,
        converter,
        IsinConverter(None),
        CurrentPriceFetcher(converter),
        SpinOffHandler(None),
        SharePrices(),
        [],
        balance_check=False,
    )


@pytest.mark.parametrize(
    ("mode", "expected_cost", "rule"),
    [
        ("vesting", Decimal(80), RuleType.SECTION_104),
        ("settlement", Decimal(100), RuleType.SAME_DAY),
    ],
)
def test_the_selected_date_controls_both_matching_and_fx(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_cost: Decimal,
    rule: RuleType,
) -> None:
    """April's award price/FX yields £80; May's closing price/FX yields £100."""
    _market(monkeypatch)
    transactions = load_via_cli(
        schwab_file=str(_main_history(tmp_path, sale=True)),
        schwab_award_file=str(_awards(tmp_path)),
        schwab_acquisition_date=mode,
    )
    report = _calculator().calculate(transactions)

    assert report.allowable_costs == expected_cost
    disposal = report.calculation_log[SETTLEMENT_DATE]["sell$FOO"]
    assert any(
        entry.rule_type is rule and entry.allowable_cost == expected_cost
        for entry in disposal
    )


def test_later_splits_do_not_reduce_the_settlement_price(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later ten-for-one split must not divide the original cost by ten."""
    monkeypatch.setattr(
        "yfinance.Ticker",
        lambda symbol: FakeHistoryTicker(3, "USD", splits=(0, 10)),
    )
    transactions = load_via_cli(
        schwab_file=str(_main_history(tmp_path)),
        schwab_acquisition_date="settlement",
    )

    assert transactions[0].price == Decimal(30)


def test_settlement_does_not_parse_an_unused_award_fmv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A price-only input may remain, but its FMV cannot enter the calculation."""
    _market(monkeypatch)
    awards = _awards(tmp_path)
    awards.write_text(
        awards.read_text(encoding="utf-8").replace("$10", "$not-a-price"),
        encoding="utf-8",
    )
    transactions = load_via_cli(
        schwab_file=str(_main_history(tmp_path)),
        schwab_award_file=str(awards),
        schwab_acquisition_date="settlement",
    )

    assert transactions[0].price == Decimal(15)


@pytest.mark.parametrize("entry", ["file", "directory", "plain"])
def test_cli_import_paths_use_the_settlement_quote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entry: str,
) -> None:
    """File, directory and recognised INPUT forms all reach market pricing."""
    main = _main_history(tmp_path)
    _market(monkeypatch)
    monkeypatch.setattr(
        CurrencyConverter, "create", lambda path: _calculator().currency_converter
    )
    if entry == "file":
        inputs = ["--schwab-file", str(main)]
    elif entry == "directory":
        inputs = ["--schwab-dir", str(tmp_path)]
    else:
        inputs = [str(main)]
    args = create_parser().parse_args(
        [
            "--year",
            "2024",
            *inputs,
            "--schwab-acquisition-date",
            "settlement",
            "--no-report",
            "--no-balance-check",
            "--exchange-rates-file",
            "",
            "--isin-translation-file",
            "",
            "--spin-offs-file",
            "",
        ]
    )
    calculate_cgt(args)

    assert "FOO: 10.00, £100.00" in capsys.readouterr().out


def test_missing_settlement_quote_does_not_use_the_award_or_next_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An available award and a quote on the next day cannot fill the gap."""
    _market(monkeypatch, days_late=1)
    with pytest.raises(
        InvalidTransactionError, match=r"No historical closing price.*2024-05-01"
    ):
        load_via_cli(
            schwab_file=str(_main_history(tmp_path)),
            schwab_award_file=str(_awards(tmp_path)),
            schwab_acquisition_date="settlement",
        )


@pytest.mark.parametrize("currency", ["EUR", "GBP", "GBp", ""])
def test_a_quote_in_another_currency_is_not_read_as_us_dollars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, currency: str
) -> None:
    """A different listing, or unknown quote currency, cannot silently price it."""
    _market(monkeypatch, currency=currency)
    with pytest.raises(InvalidTransactionError, match="states currency"):
        load_via_cli(
            schwab_file=str(_main_history(tmp_path)),
            schwab_acquisition_date="settlement",
        )


@pytest.mark.parametrize("price", [float("nan"), float("inf"), 0.0, -1.0])
def test_invalid_quotes_stop_the_calculation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, price: float
) -> None:
    """A failed quote cannot manufacture a zero or undefined acquisition cost."""
    _market(monkeypatch, price=price)
    with pytest.raises(InvalidTransactionError, match="finite and positive"):
        load_via_cli(
            schwab_file=str(_main_history(tmp_path)),
            schwab_acquisition_date="settlement",
        )


def test_settlement_is_not_supported_for_complete_award_imports() -> None:
    """Do not silently retain vest valuations in an unsupported import path."""
    complete = Path("tests/schwab/data/equity_award/schwab_equity_award_v2.json")
    with pytest.raises(
        CgtError, match="Complete Equity Awards imports are not supported"
    ):
        load_via_cli(
            schwab_award_file=str(complete), schwab_acquisition_date="settlement"
        )


def test_today_has_no_final_daily_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not replace a historical valuation with a still-changing daily bar."""
    monkeypatch.setattr(CurrentPriceFetcher, "get_closing_quote", _no_quote)
    today = dt.datetime.now(dt.UTC).date().strftime("%m/%d/%Y")
    with pytest.raises(InvalidTransactionError, match="completed day's closing price"):
        load_via_cli(
            schwab_file=str(_main_history(tmp_path, date=today)),
            schwab_acquisition_date="settlement",
        )


def test_a_later_run_resets_the_date_choice_and_quote_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both pricing and default selection belong to this invocation alone."""
    main, awards = _main_history(tmp_path), _awards(tmp_path)
    _market(monkeypatch, price=15)
    first = load_via_cli(schwab_file=str(main), schwab_acquisition_date="settlement")
    _market(monkeypatch, price=18)
    second = load_via_cli(schwab_file=str(main), schwab_acquisition_date="settlement")
    monkeypatch.setattr(CurrentPriceFetcher, "get_closing_quote", _no_quote)
    default = load_via_cli(schwab_file=str(main), schwab_award_file=str(awards))

    assert [first[0].price, second[0].price, default[0].price] == [
        Decimal(15),
        Decimal(18),
        Decimal(10),
    ]
    assert [first[0].date, second[0].date, default[0].date] == [
        SETTLEMENT_DATE,
        SETTLEMENT_DATE,
        AWARD_DATE,
    ]
