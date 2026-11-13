"""Test the Trading 212 aliases that are also another security's US symbol.

Trading 212 exports General Dynamics' German line as `GDX`, and the London
dollar lines of three funds as `SMH`, `WCLD` and `DFNS`, each beside the
ticker the report uses (`GD`, `SMGB`, `KLWD`, `DFNG`). In the US the same
letters are the VanEck Gold Miners ETF, the VanEck Semiconductor ETF, the
WisdomTree Cloud Computing Fund and T3 Defense. A row without an ISIN under
one of them is the Trading 212 security only when its market says so.

Every history here is invented and in pounds:

- Trading 212, both listings: 10 April 2024, buy 10 under the reported
    ticker at 200; 1 May 2024, buy 5 under the alias at 200; 2 September
    2024, sell 12 under the reported ticker at 250.
- Trading 212, alias only: 1 May 2024, buy 5 at 200; 2 September 2024,
    sell 2 at 250.
"""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

import pytest

from tests.utils import build_cmd, run_cli, stderr_alerts

if TYPE_CHECKING:
    from pathlib import Path

SEPARATE_RUNS = (
    "https://cgt-calc.uk/extra-data-and-options/#two-securities-in-separate-runs"
)
T212_HEADER = (
    "Action,Time (UTC),ISIN,Ticker,Name,No. of shares,Price / share,"
    "Currency (Price / share),Exchange rate,Result,Currency (Result),Total,"
    "Currency (Total),ID"
)
GOLD_MINERS_UCITS_ISIN = "IE00BQQP9F84"
# The alias, the ticker it is reported under, its table entry's ISIN and the
# name Trading 212 gives the security.
PAIRS = [
    pytest.param("GDX", "GD", "US3695501086", "General Dynamics", id="GDX"),
    pytest.param("SMH", "SMGB", "IE00BMC38736", "VanEck Semiconductor", id="SMH"),
    pytest.param("WCLD", "KLWD", "IE00BJGWQN72", "WisdomTree Cloud", id="WCLD"),
    pytest.param("DFNS", "DFNG", "IE000YYE6WK5", "VanEck Defense", id="DFNS"),
]


def _t212_row(
    action: str, day: str, isin: str, ticker: str, name: str, quantity: int, price: int
) -> str:
    return (
        f"{action},{day} 10:00:00,{isin},{ticker},{name},{quantity},{price}.00,GBP,"
        f"1.00,,GBP,{quantity * price}.00,GBP,{action[7:]}-{day}"
    )


def _both_listings(alias: str, reported: str, isin: str, name: str) -> list[str]:
    return [
        _t212_row("Market buy", "2024-04-10", isin, reported, name, 10, 200),
        _t212_row("Market buy", "2024-05-01", isin, alias, name, 5, 200),
        _t212_row("Market sell", "2024-09-02", isin, reported, name, 12, 250),
    ]


def _alias_only(alias: str, isin: str, name: str) -> list[str]:
    return [
        _t212_row("Market buy", "2024-05-01", isin, alias, name, 5, 200),
        _t212_row("Market sell", "2024-09-02", isin, alias, name, 2, 250),
    ]


def _inputs(
    tmp_path: Path, t212_rows: list[str], raw_rows: list[str]
) -> tuple[Path, Path]:
    """Write a Trading 212 export and a RAW file; return the export and the file."""
    t212_dir = tmp_path / "trading212"
    t212_dir.mkdir()
    export = t212_dir / "export.csv"
    export.write_text("\n".join([T212_HEADER, *t212_rows]) + "\n", encoding="utf-8")
    raw = tmp_path / "raw.csv"
    raw.write_text("\n".join(raw_rows) + "\n", encoding="utf-8")
    return export, raw


def _args(export: Path, raw: Path) -> list[str]:
    return build_cmd(
        "--year",
        "2024",
        "--trading212-dir",
        str(export.parent),
        "--raw-file",
        str(raw),
        "--isin-translation-file",
        str(raw.parent / "isin_translation.csv"),
        "--no-balance-check",
        "--no-report",
    )


def _holdings(stdout: str) -> dict[str, str]:
    """Return the final portfolio as ticker to quantity."""
    block = stdout.split("Final portfolio\n", 1)[1].split("\n\n", 1)[0]
    return dict(line.strip().split(": ") for line in block.splitlines())


def _figure(stdout: str, label: str) -> str:
    match = re.search(rf"^  {label}:\s+£([\d,.]+)$", stdout, re.MULTILINE)
    assert match is not None, stdout
    return match.group(1)


@pytest.mark.parametrize(("alias", "reported", "isin", "name"), PAIRS)
def test_a_us_row_under_an_alias_is_another_security(
    tmp_path: Path, alias: str, reported: str, isin: str, name: str
) -> None:
    """A RAW row marked US under the alias is not the Trading 212 security.

    Trading 212 holds the alias only. The RAW rows, marked US, are the US
    security: 15 April 2024, buy 100 at 30; 1 October 2024, sell 50 at 35.

    - Trading 212: proceeds 2 x 250 = 500, cost 2 x 200 = 400, gain 100.
    - RAW: proceeds 50 x 35 = 1,750, cost 50 x 30 = 1,500, gain 250.
    - Total gain 350, no loss.

    Pooled, as `main` had them before: 105 shares for 4,000. The sale of 2
    costs 4,000 x 2 / 105 = 76.19, a gain of 423.81; the sale of 50 costs
    3,923.81 x 50 / 103 = 1,904.76, a loss of 154.76.
    """
    export, raw = _inputs(
        tmp_path,
        _alias_only(alias, isin, name),
        [
            "date,action,symbol,quantity,price,fees,currency,market",
            f"2024-04-15,BUY,{alias},100,30,0,GBP,US",
            f"2024-10-01,SELL,{alias},50,35,0,GBP,US",
        ],
    )

    result = run_cli(_args(export, raw))

    assert stderr_alerts(result.stderr) == []
    assert _holdings(result.stdout) == {reported: "3.00", alias: "50.00"}
    assert _figure(result.stdout, "Gain") == "350.00"
    assert _figure(result.stdout, "Loss") == "0.00"


@pytest.mark.parametrize(
    ("alias", "reported", "isin", "name", "holdings", "gain"),
    [
        pytest.param(
            "GDX",
            "GD",
            "US3695501086",
            "General Dynamics",
            {"GD": "3.00", "GDX": "10.00"},
            "850.00",
            id="GDX: London's GDX is another fund",
        ),
        pytest.param(
            "SMH",
            "SMGB",
            "IE00BMC38736",
            "VanEck Semiconductor",
            {"SMGB": "13.00"},
            "760.00",
            id="SMH: the fund's London line",
        ),
        pytest.param(
            "WCLD",
            "KLWD",
            "IE00BJGWQN72",
            "WisdomTree Cloud",
            {"KLWD": "13.00"},
            "760.00",
            id="WCLD: the fund's London line",
        ),
        pytest.param(
            "DFNS",
            "DFNG",
            "IE000YYE6WK5",
            "VanEck Defense",
            {"DFNG": "13.00"},
            "760.00",
            id="DFNS: the fund's London line",
        ),
    ],
)
def test_a_london_row_under_an_alias_joins_only_a_london_line(
    tmp_path: Path,
    alias: str,
    reported: str,
    isin: str,
    name: str,
    holdings: dict[str, str],
    gain: str,
) -> None:
    """A RAW row marked LSE is the fund's London line, but not General Dynamics.

    Trading 212 holds both listings. The RAW rows are marked LSE: 15 April
    2024, buy 20 at 235; 1 October 2024, sell 10 at 260.

    - The same fund, one holding: 2,000 + 4,700 + 1,000 = 7,700 for 35
        shares, 220 each. The sale of 12 gains 3,000 - 2,640 = 360 and the
        sale of 10 gains 2,600 - 2,200 = 400: 760, with 13 left.
    - GDX, two holdings: Trading 212's 15 cost 3,000, and the sale of 12
        gains 3,000 - 2,400 = 600. The RAW sale of 10 gains 10 x (260 -
        235) = 250. Total 850.

    `main` stopped on the two Trading 212 listings: `does not match`.
    """
    export, raw = _inputs(
        tmp_path,
        _both_listings(alias, reported, isin, name),
        [
            "date,action,symbol,quantity,price,fees,currency,market",
            f"2024-04-15,BUY,{alias},20,235,0,GBP,LSE",
            f"2024-10-01,SELL,{alias},10,260,0,GBP,LSE",
        ],
    )

    result = run_cli(_args(export, raw))

    assert stderr_alerts(result.stderr) == []
    assert _holdings(result.stdout) == holdings
    assert _figure(result.stdout, "Gain") == gain
    assert _figure(result.stdout, "Loss") == "0.00"


def test_a_market_on_one_row_covers_the_tickers_other_rows(tmp_path: Path) -> None:
    """A blank market under the alias takes the market another row gives it.

    Trading 212 holds both listings of the VanEck Semiconductor fund. The
    RAW purchase is marked US, and the sale leaves the market blank: 15 April
    2024, buy 20 SMH at 235; 1 October 2024, sell 10 at 260. Both are the US
    fund, so the two holdings stay apart: Trading 212's sale of 12 gains
    3,000 - 2,400 = 600, the RAW sale gains 10 x (260 - 235) = 250, 850 in all.
    """
    export, raw = _inputs(
        tmp_path,
        _both_listings("SMH", "SMGB", "IE00BMC38736", "VanEck Semiconductor"),
        [
            "date,action,symbol,quantity,price,fees,currency,market",
            "2024-04-15,BUY,SMH,20,235,0,GBP,US",
            "2024-10-01,SELL,SMH,10,260,0,GBP,",
        ],
    )

    result = run_cli(_args(export, raw))

    assert stderr_alerts(result.stderr) == []
    assert _holdings(result.stdout) == {"SMGB": "3.00", "SMH": "10.00"}
    assert _figure(result.stdout, "Gain") == "850.00"


@pytest.mark.parametrize(
    ("alias", "reported", "isin", "name", "both"),
    [
        *(pytest.param(*pair.values, False, id=pair.id) for pair in PAIRS),
        pytest.param(
            "GDX",
            "GD",
            "US3695501086",
            "General Dynamics",
            True,
            id="GDX beside both Trading 212 listings",
        ),
    ],
)
def test_a_row_with_no_isin_or_market_under_an_alias_is_refused(
    tmp_path: Path, alias: str, reported: str, isin: str, name: str, both: bool
) -> None:
    """Nothing says which of the two securities a bare RAW row is.

    The RAW file has no market column: 15 April 2024, buy 100 at 30; 1
    October 2024, sell 50 at 35. Beside the alias alone, `main` pooled them
    with Trading 212's holding with no error; beside both listings, it
    stopped on the listings with `does not match`. The run stops at the RAW
    purchase, the first row it cannot place, and shows the Trading 212 row
    that exported the alias.
    """
    t212_rows = (
        _both_listings(alias, reported, isin, name)
        if both
        else _alias_only(alias, isin, name)
    )
    export, raw = _inputs(
        tmp_path,
        t212_rows,
        [
            "date,action,symbol,quantity,price,fees,currency",
            f"2024-04-15,BUY,{alias},100,30,0,GBP",
            f"2024-10-01,SELL,{alias},50,35,0,GBP",
        ],
    )
    exported_on = 3 if both else 2

    result = subprocess.run(
        _args(export, raw), capture_output=True, encoding="utf-8", check=False
    )

    assert result.returncode == 1, result.stdout
    assert result.stderr.endswith(
        f"\nERROR: Ticker {alias} has no ISIN on this row and no market in this "
        f"run, and another row of this run exports {alias} with ISIN {isin}, "
        f"reported as {reported}:\n"
        f"  2024-05-01 Buy 5 {alias}, price 200 GBP, amount -1000\n"
        f'    Trading212 "{name}" (ISIN {isin}), read from row {exported_on} of '
        f"{export}\n"
        f"{alias} is also the US symbol of another security, so cgt-calc cannot "
        "tell which of the two this row is. Pooled, two securities would be "
        "matched as one holding. If this row is in your RAW file, write in its "
        "market column where its security is listed. If they are two securities "
        "and their rows cannot say so, calculate them in separate runs; to split "
        f"the files and combine the reports, see {SEPARATE_RUNS}\n"
        f"2024-04-15 Buy 100 {alias}, price 30 GBP, amount -3000\n"
        f"  Unknown, read from row 2 of {raw}\n\n"
    )


def test_a_row_with_no_isin_beside_trading_212s_london_gdx_fund_joins_it(
    tmp_path: Path,
) -> None:
    """Only General Dynamics' ISIN makes a bare GDX row ambiguous.

    Trading 212 also exports London's `GDX`, the VanEck Gold Miners UCITS
    fund, under IE00BQQP9F84: 10 April 2024, buy 10 at 200; 1 May 2024, buy
    5 at 200; 2 September 2024, sell 12 at 250. A RAW row under `GDX` with no
    ISIN or market takes that ISIN, as any row without an ISIN does: 15
    April 2024, buy 20 at 235; 1 October 2024, sell 10 at 260. One holding of
    35 shares for 7,700, 220 each: the sale of 12 gains 3,000 - 2,640 = 360,
    the sale of 10 gains 2,600 - 2,200 = 400, 760 in all, with 13 left.
    """
    fund = "VanEck Gold Miners UCITS"
    export, raw = _inputs(
        tmp_path,
        [
            _t212_row(
                "Market buy", "2024-04-10", GOLD_MINERS_UCITS_ISIN, "GDX", fund, 10, 200
            ),
            _t212_row(
                "Market buy", "2024-05-01", GOLD_MINERS_UCITS_ISIN, "GDX", fund, 5, 200
            ),
            _t212_row(
                "Market sell",
                "2024-09-02",
                GOLD_MINERS_UCITS_ISIN,
                "GDX",
                fund,
                12,
                250,
            ),
        ],
        [
            "date,action,symbol,quantity,price,fees,currency",
            "2024-04-15,BUY,GDX,20,235,0,GBP",
            "2024-10-01,SELL,GDX,10,260,0,GBP",
        ],
    )

    result = run_cli(_args(export, raw))

    assert stderr_alerts(result.stderr) == []
    assert _holdings(result.stdout) == {"GDX": "13.00"}
    assert _figure(result.stdout, "Gain") == "760.00"
