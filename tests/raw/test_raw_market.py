"""Tests for the RAW `market` column: which security a shared ticker is.

`OPEN` is both the iShares fund that cgt-calc's bundled ISIN list gives the
London ticker `OPEN` (IE00BD0B9B76) and Opendoor in the US. Every history
here is invented.
"""

from __future__ import annotations

import csv
import re
import subprocess
from typing import TYPE_CHECKING

import pytest

from cgt_calc.parsers.raw import COLUMNS
from tests.utils import build_cmd, run_cli, stderr_alerts

if TYPE_CHECKING:
    from pathlib import Path

_SEPARATE_RUNS = (
    "https://cgt-calc.uk/extra-data-and-options/#two-securities-in-separate-runs"
)
_SCHWAB_HEADER = ["Date", "Action", "Symbol", "Description", "Price", "Quantity"]
_TRADING212_HEADER = (
    "Action,Time,ISIN,Ticker,Name,No. of shares,Price / share,"
    "Currency (Price / share),Exchange rate,Result,Currency (Result),Total,"
    "Currency (Total),Withholding tax,Currency (Withholding tax),Transaction fee,"
    "Notes,ID,Currency conversion fee,Currency (Currency conversion fee),"
    "Currency (Transaction fee)"
)


def _write_raw(path: Path, symbol: str, markets: tuple[str, str]) -> None:
    """Buy 100 at £2 on 3 May 2022 and sell 40 at £3 on 31 January 2023."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(
            [
                [*COLUMNS, "market"],
                ["2022-05-03", "BUY", symbol, "100", "2", "0", "GBP", markets[0]],
                ["2023-01-31", "SELL", symbol, "40", "3", "0", "GBP", markets[1]],
            ]
        )


def _write_schwab(path: Path) -> None:
    """Buy 10 OPEN at $2 on 3 May 2022: Schwab's OPEN is a US symbol."""
    path.write_text(
        ",".join([*_SCHWAB_HEADER, "Fees & Comm", "Amount"])
        + "\n05/03/2022,Buy,OPEN,OPENDOOR TECHNOLOGIES,$2,10,$0,-$20\n",
        encoding="utf-8",
    )


def _write_trading212(path: Path, symbol: str) -> None:
    """Buy 10 at £2 on 3 May 2022, exported with an ISIN of its own."""
    path.write_text(
        f"{_TRADING212_HEADER}\n"
        f"Market buy,2022-05-03 10:00:00,US0000000002,{symbol},Another Company,"
        "10,2.00,GBP,1.00,,GBP,20.00,GBP,,,,,EOF1,,,\n",
        encoding="utf-8",
    )


def _cmd(tmp_path: Path, *inputs: str) -> list[str]:
    """Report 2022/23 from `inputs`, with no ISIN translations of the user's."""
    return build_cmd(
        "--year",
        "2022",
        *inputs,
        "--isin-translation-file",
        str(tmp_path / "isin_translation.csv"),
        "--no-balance-check",
        "--no-report",
    )


@pytest.mark.parametrize(
    ("market", "costs", "gain", "eri"),
    [
        pytest.param("US", "80.00", "40.00", None, id="US: Opendoor"),
        pytest.param("LSE", "84.82", "35.18", "12.04", id="LSE: the London fund"),
    ],
)
def test_a_raw_market_says_which_security_a_shared_ticker_is(
    tmp_path: Path, market: str, costs: str, gain: str, eri: str | None
) -> None:
    """The market settles `OPEN` without the warning, either way.

    Buy 100 OPEN at £2, sell 40 at £3: proceeds 40 x 3 = £120.00.

    - US, Opendoor: no fund income; cost 40 x 2 = £80.00, gain £40.00.
    - LSE, the fund: its report for the year to 31 May 2022 is USD 0.1356 a
      unit (bundled iShares data), received six months later, so converted
      at November 2022's HMRC rate of 1.1259: 100 x 0.1356 / 1.1259 =
      £12.0437, income of £12.04. The 40 sold carry 40/100 of it: cost
      80 + 4.8175 = £84.82, gain 120 - 84.8175 = £35.18.

    Without the column the run takes the fund's figures and warns.
    """
    raw_file = tmp_path / "raw.csv"
    _write_raw(raw_file, "OPEN", (market, market))

    result = run_cli(_cmd(tmp_path, "--raw-file", str(raw_file)))

    assert stderr_alerts(result.stderr) == []
    assert re.search(rf"\n  Allowable costs: +£{re.escape(costs)}\n", result.stdout)
    assert re.search(rf"\n  Gain: +£{re.escape(gain)}\n", result.stdout)
    if eri is None:
        assert "Excess Reported Income" not in result.stdout
    else:
        assert f"\n  OPEN: £{eri} (included as dividend)\n" in result.stdout


@pytest.mark.parametrize(
    ("schwab", "markets", "other_row"),
    [
        pytest.param(
            True,
            ("LSE", "LSE"),
            "2022-05-03 Buy 10 OPEN, price 2 USD, amount -20\n"
            '    Charles Schwab "OPENDOOR TECHNOLOGIES", read from row 2 of '
            "{schwab_file}",
            id="a Schwab row is US",
        ),
        pytest.param(
            False,
            ("US", "LSE"),
            "2022-05-03 Buy 100 OPEN, price 2 GBP, amount -200\n"
            "    Unknown, read from row 2 of {raw_file}",
            id="two RAW rows",
        ),
    ],
)
def test_one_ticker_under_two_markets_is_refused(
    tmp_path: Path, schwab: bool, markets: tuple[str, str], other_row: str
) -> None:
    """Two markets for one ticker are two securities the run would pool.

    RAW is read after every broker export, so the RAW row is the one the
    error stops at, and the row it disagrees with is shown above it.
    """
    raw_file = tmp_path / "raw.csv"
    _write_raw(raw_file, "OPEN", markets)
    schwab_file = tmp_path / "schwab.csv"
    _write_schwab(schwab_file)
    inputs = ["--raw-file", str(raw_file)]
    if schwab:
        inputs += ["--schwab-file", str(schwab_file)]
    this_row = (
        "2022-05-03 Buy 100 OPEN, price 2 GBP, amount -200\n"
        f"  Unknown, read from row 2 of {raw_file}"
        if schwab
        else "2023-01-31 Sell 40 OPEN, price 3 GBP, amount 120\n"
        f"  Unknown, read from row 3 of {raw_file}"
    )

    result = subprocess.run(
        _cmd(tmp_path, *inputs), capture_output=True, encoding="utf-8", check=False
    )

    assert result.returncode == 1
    assert result.stderr.endswith(
        "\nERROR: Ticker OPEN is from market LSE on this row and from market US on "
        "another row of this run:\n"
        f"  {other_row.format(schwab_file=schwab_file, raw_file=raw_file)}\n"
        "Two securities under one ticker would be pooled and matched as one "
        "holding. If a market is wrong, correct it. If they are two securities, "
        "calculate them in separate runs; to split the files and combine the "
        f"reports, see {_SEPARATE_RUNS}\n{this_row}\n\n"
    )


def test_a_london_row_beside_another_isin_for_the_bundled_ticker_is_refused(
    tmp_path: Path,
) -> None:
    """The run cannot tell whether a London OPEN is the fund or the exported one.

    The bundled list gives London's OPEN to IE00BD0B9B76, and Trading 212
    exports OPEN with another ISIN.
    """
    raw_file = tmp_path / "raw.csv"
    _write_raw(raw_file, "OPEN", ("LSE", "LSE"))
    trading212_dir = tmp_path / "trading212"
    trading212_dir.mkdir()
    _write_trading212(trading212_dir / "2022.csv", "OPEN")

    result = subprocess.run(
        _cmd(
            tmp_path,
            "--raw-file",
            str(raw_file),
            "--trading212-dir",
            str(trading212_dir),
        ),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 1
    assert result.stderr.endswith(
        "\nERROR: Ticker OPEN is a London ticker on this row, which the bundled "
        "list gives ISIN IE00BD0B9B76, and another row of this run exports it "
        "with ISIN US0000000002, so cgt-calc cannot tell whether they are one "
        "security. Pooled, two securities would be matched as one holding. If "
        "they are two securities, calculate them in separate runs; to split the "
        f"files and combine the reports, see {_SEPARATE_RUNS}. If this row is the "
        "security exported with ISIN US0000000002, leave its market blank.\n"
        "2022-05-03 Buy 100 OPEN, price 2 GBP, amount -200\n"
        f"  Unknown, read from row 2 of {raw_file}\n\n"
    )


def test_a_blank_market_beside_a_schwab_row_is_the_schwab_security(
    tmp_path: Path,
) -> None:
    """A blank market is unknown, so it joins the US OPEN instead of clashing.

    The whole run's OPEN is then Opendoor, so no fund income is added.
    """
    raw_file = tmp_path / "raw.csv"
    _write_raw(raw_file, "OPEN", ("", ""))
    schwab_file = tmp_path / "schwab.csv"
    _write_schwab(schwab_file)

    result = run_cli(
        _cmd(tmp_path, "--raw-file", str(raw_file), "--schwab-file", str(schwab_file))
    )

    assert "Excess Reported Income" not in result.stdout


def test_a_london_row_under_a_bundled_us_symbol_is_not_refused(
    tmp_path: Path,
) -> None:
    """Only a bundled London ticker says a London row is the bundled fund.

    The bundled list gives CHIQ to a fund with a US ISIN, US37950E4089, as
    its US symbol. A London CHIQ beside Trading 212's CHIQ with another ISIN
    is therefore not refused: it takes that ISIN, as a row with no market
    does.
    """
    raw_file = tmp_path / "raw.csv"
    _write_raw(raw_file, "CHIQ", ("LSE", "LSE"))
    trading212_dir = tmp_path / "trading212"
    trading212_dir.mkdir()
    _write_trading212(trading212_dir / "2022.csv", "CHIQ")

    run_cli(
        _cmd(
            tmp_path,
            "--raw-file",
            str(raw_file),
            "--trading212-dir",
            str(trading212_dir),
        )
    )
