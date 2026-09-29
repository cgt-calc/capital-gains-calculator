"""A spin-off's two prices come from `--initial-prices-file` or from Yahoo.

The cost is split in the ratio of the two holdings' prices on the day, so a
price the user gave and one looked up must not be mixed: a looked-up price is
in pounds, a given one in whatever currency it was written in. Each case runs
the command-line calculation with Yahoo replaced, and FOO keeps nine tenths of
a £100 pool when priced at nine times BAR.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cgt_calc.args_parser import create_parser
from cgt_calc.cli import calculate_cgt
from cgt_calc.exceptions import CalculationError

from .test_current_price_fetcher import FakeHistoryTicker

if TYPE_CHECKING:
    from pathlib import Path


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    source: str,
    dest: str,
    given: dict[str, int] | None,
    market: dict[str, int] | None,
) -> str:
    """Spin `dest` off £100 of `source` on 2020-11-16 and return the report.

    `given` is written to an initial prices file; with None, no file is passed.
    `market` is what Yahoo answers; with None, any lookup fails the test.
    """
    raw = tmp_path / "raw.csv"
    raw.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2020-11-02,TRANSFER,,1,1000,0,GBP\n"
        f"2020-11-02,BUY,{source},10,10,0,GBP\n"
        f"2020-11-16,SPIN_OFF,{dest},10,0,0,GBP\n"
    )
    spin_offs = tmp_path / "spin_offs.csv"
    spin_offs.write_text(f"dst,src\n{dest},{source}\n")
    args = [
        "--year",
        "2020",
        "--raw-file",
        str(raw),
        "--spin-offs-file",
        str(spin_offs),
        "--exchange-rates-file",
        "",
        "--isin-translation-file",
        "",
        "--no-report",
    ]
    if given is not None:
        prices = tmp_path / "prices.csv"
        prices.write_text(
            "date,symbol,price\n"
            + "".join(
                f'"Nov 16, 2020",{name},{price}\n' for name, price in given.items()
            )
        )
        args += ["--initial-prices-file", str(prices)]

    def ticker(symbol: str) -> FakeHistoryTicker:
        if market is None:
            raise AssertionError(f"{symbol} was looked up on Yahoo")
        return FakeHistoryTicker(market[symbol], "GBP")

    monkeypatch.setattr("cgt_calc.current_price_fetcher.yf.Ticker", ticker)
    calculate_cgt(create_parser().parse_args(args))
    return capsys.readouterr().out


def test_a_prices_file_prices_a_spin_off_without_a_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both prices given: they split the cost, and Yahoo is not asked."""
    out = _run(
        tmp_path,
        monkeypatch,
        capsys,
        source="FOO",
        dest="BAR",
        given={"FOO": 90, "BAR": 10},
        market=None,
    )

    assert "  FOO: 10.00, £90.00\n" in out
    assert "  BAR: 10.00, £10.00\n" in out


def test_a_prices_file_with_one_of_the_two_prices_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One price given: refused before Yahoo is asked for the other.

    The source is the one priced here; the rename tests price the new holding.
    """
    with pytest.raises(CalculationError, match="gives a price for FOO but not for BAR"):
        _run(
            tmp_path,
            monkeypatch,
            capsys,
            source="FOO",
            dest="BAR",
            given={"FOO": 90},
            market=None,
        )


def test_bundled_prices_do_not_price_a_spin_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The bundled vest prices include VTRS on 2020-11-16, and are not read.

    Read, they would price VTRS but not PFE, and the spin-off would be refused
    instead of priced from Yahoo as it was before a prices file could reach it.
    """
    out = _run(
        tmp_path,
        monkeypatch,
        capsys,
        source="PFE",
        dest="VTRS",
        given=None,
        market={"PFE": 90, "VTRS": 10},
    )

    assert "  PFE: 10.00, £90.00\n" in out
    assert "  VTRS: 10.00, £10.00\n" in out
