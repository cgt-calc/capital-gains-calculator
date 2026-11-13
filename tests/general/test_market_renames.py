"""Test renames held to one market, decided once for the whole run.

Royal Mail traded in London as RMG until 5 October 2022 and as IDS from then
on, and RMG was also RMG Acquisition Corp's symbol on the NYSE until December
2020. Marsh & McLennan Companies traded in New York as MMC until 14 January
2026 and as MRSH from then on, and MMC was Management Consulting Group's
London ticker until 2020. So neither rename can go by ticker and date alone.
A row with the company's ISIN or the rename's market is renamed as its file
is read; a row with neither takes what the rest of the run exported under
the old ticker.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import TYPE_CHECKING

from tests.utils import build_cmd, run_cli, stderr_alerts

if TYPE_CHECKING:
    from pathlib import Path

RMG_ISIN = "GB00BDVZYZ77"
SEPARATE_RUNS = (
    "https://cgt-calc.uk/extra-data-and-options/#two-securities-in-separate-runs"
)
RAW_HEADER = "date,action,symbol,quantity,price,fees,currency,market"
FREETRADE_HEADER = (
    "Title,Type,Timestamp,Account Currency,Total Amount,Buy / Sell,Ticker,ISIN,"
    "Price per Share in Account Currency,Stamp Duty,Quantity,Venue,Order ID,"
    "Order Type,Instrument Currency,Total Shares Amount,Price per Share,FX Rate,"
    "Base FX Rate,FX Fee (BPS),FX Fee Amount,Dividend Ex Date,Dividend Pay Date,"
    "Dividend Eligible Quantity,Dividend Amount Per Share,"
    "Dividend Gross Distribution Amount,Dividend Net Distribution Amount,"
    "Dividend Withheld Tax Percentage,Dividend Withheld Tax Amount"
)
# 1 February 2021: buy 10 RMG at 200; 1 June 2021: sell 5 RMG at 300.
FREETRADE_ROYAL_MAIL = [
    (
        "Royal Mail,ORDER,2021-06-01T10:00:00.000Z,GBP,1500.00,SELL,RMG,"
        f"{RMG_ISIN},300.00000000,0.00,5.00000000,London Stock Exchange,ORD2,BASIC,"
        "GBP,1500.00,300.00000000,,,0,,,,,,,,,"
    ),
    (
        "Royal Mail,ORDER,2021-02-01T10:00:00.000Z,GBP,2000.00,BUY,RMG,"
        f"{RMG_ISIN},200.00000000,0.00,10.00000000,London Stock Exchange,ORD1,BASIC,"
        "GBP,2000.00,200.00000000,,,,,,,,,,,,"
    ),
]
IBKR_HEADER = [
    "Statement,Header,Field Name,Field Value",
    "Statement,Data,Title,Transaction History",
    'Statement,Data,Period,"January 1, 2021 - December 31, 2023"',
    'Statement,Data,WhenGenerated,"2023-12-31, 23:59:59 EST"',
    "Summary,Header,Field Name,Field Value",
    "Summary,Data,Base Currency,GBP",
    "Summary,Data,Starting Cash,0.0",
    "Summary,Data,Change,{change}",
    "Summary,Data,Ending Cash,{change}",
    (
        "Transaction History,Header,Date,Account,Description,Transaction Type,Symbol,"
        "Quantity,Price,Gross Amount ,Commission,Net Amount"
    ),
]
# 4 January 2021: IBKR buys 10 RMG at 100. Its trades carry no ISIN.
IBKR_ROYAL_MAIL_BUY = (
    "Transaction History,Data,2021-01-04,U***00000,ROYAL MAIL PLC,Buy,RMG,10.0,"
    "100.0,-1000.0,-,-1000.0"
)
T212_HEADER = (
    "Action,Time (UTC),ISIN,Ticker,Name,No. of shares,Price / share,"
    "Currency (Price / share),Exchange rate,Result,Currency (Result),Total,"
    "Currency (Total),ID"
)
SCHWAB_HEADER = "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount"


def _write(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _ibkr(tmp_path: Path, change: str, *rows: str) -> Path:
    header = [line.format(change=change) for line in IBKR_HEADER]
    return _write(tmp_path / "ibkr.csv", [*header, *rows])


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return run_cli(build_cmd("--no-report", "--no-balance-check", *args))


def _refused(*args: str) -> str:
    """Run a history that has to stop, and return what it printed."""
    result = subprocess.run(
        build_cmd("--no-report", "--no-balance-check", *args),
        capture_output=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode != 0, result.stdout
    return result.stderr


def _holdings(stdout: str) -> dict[str, str]:
    """Return the final portfolio as ticker to quantity."""
    block = stdout.split("Final portfolio\n", 1)[1].split("\n\n", 1)[0]
    return dict(line.strip().split(": ") for line in block.splitlines())


def _figure(stdout: str, label: str) -> str:
    match = re.search(rf"^  {label}:\s+£([\d,.]+)$", stdout, re.MULTILINE)
    assert match is not None, stdout
    return match.group(1)


def test_a_trade_without_an_isin_joins_the_company_its_ticker_was_exported_for(
    tmp_path: Path,
) -> None:
    """Codex's #1193 history stays one holding, now named IDS.

    1. 4 January 2021: IBKR buys 10 RMG at 100; the trade has no ISIN.
    2. 1 February 2021: Freetrade buys 10 RMG at 200, ISIN GB00BDVZYZ77.
    3. 1 March 2021: IBKR pays an RMG(GB00BDVZYZ77) dividend.
    4. 1 June 2021: Freetrade sells 5 RMG at 300.

    The dividend and the Freetrade rows name Royal Mail under RMG, and
    nothing else, so the IBKR trade is Royal Mail's too: (10 x 100 + 10 x
    200) / 20 = 150 a share, 5 x 150 = 750 allowed, 1,500 - 750 = 750 gain.
    Renaming only the rows that name the ISIN would leave the trade apart
    and the sale costed at 200 a share alone: 1,000 allowed, 500 gain.
    """
    ibkr = _ibkr(
        tmp_path,
        "-999.0",
        "Transaction History,Data,2021-03-01,U***00000,RMG(GB00BDVZYZ77) Cash "
        "Dividend GBP 0.10 per Share,Dividend,RMG,-,-,1.0,-,1.0",
        IBKR_ROYAL_MAIL_BUY,
    )
    freetrade = _write(
        tmp_path / "freetrade.csv", [FREETRADE_HEADER, *FREETRADE_ROYAL_MAIL]
    )

    result = _run(
        "--year", "2021", "--interactive-brokers-file", str(ibkr),
        "--freetrade-file", str(freetrade),
    )  # fmt: skip

    assert _holdings(result.stdout) == {"IDS": "15.00"}
    assert _figure(result.stdout, "Allowable costs") == "750.00"
    assert _figure(result.stdout, "Gain") == "750.00"
    assert stderr_alerts(result.stderr) == []


def test_a_row_exported_under_the_new_ticker_says_nothing_about_the_old_one(
    tmp_path: Path,
) -> None:
    """An IDS dividend does not make an RMG trade Royal Mail's.

    1. 4 January 2021: IBKR buys 10 RMG at 100, with no ISIN.
    2. 4 January 2023: IBKR pays an IDS(GB00BDVZYZ77) dividend of 1.

    No row exported under RMG names a security, so the trade could be Royal
    Mail's or the 2020 NYSE company's. It stays RMG, and the warning says
    how to settle it.
    """
    ibkr = _ibkr(
        tmp_path,
        "-999.0",
        "Transaction History,Data,2023-01-04,U***00000,IDS(GB00BDVZYZ77) Cash "
        "Dividend GBP 0.10 per Share,Dividend,IDS,-,-,1.0,-,1.0",
        IBKR_ROYAL_MAIL_BUY,
    )

    result = _run("--year", "2022", "--interactive-brokers-file", str(ibkr))

    assert _holdings(result.stdout) == {"RMG": "10.00"}
    assert stderr_alerts(result.stderr) == [
        (
            "WARNING: Rows under RMG name no ISIN or market, and no row of this run "
            "exported under RMG names its ISIN or market, so they are kept as RMG. "
            "They may be Royal Mail, whose ticker changed to IDS, or another "
            "security that used RMG. If they are Royal Mail rows in your RAW file, "
            "write LSE in their market column. See "
            "https://cgt-calc.uk/extra-data-and-options/#ticker-renames"
        )
    ]


def test_evidence_survives_the_rows_trading_212_merges_into_a_reorganisation(
    tmp_path: Path,
) -> None:
    """The only RMG row Trading 212 exports is the close half of the change.

    1. 4 January 2021: IBKR buys 10 RMG at 100, with no ISIN.
    2. 1 February 2021: Trading 212 buys 10 IDS at 200, ISIN GB00BDVZYZ77.
    3. 5 October 2022: Trading 212 closes 10 RMG and opens 10 IDS.
    4. 1 June 2023: Trading 212 sells 5 IDS at 300.

    Pairing replaces both halves with one IDS event, but the close half was
    exported as RMG for Royal Mail, so the IBKR trade joins the holding:
    (10 x 100 + 10 x 200) / 20 = 150 a share, 5 x 150 = 750 allowed,
    1,500 - 750 = 750 gain.
    """
    ibkr = _ibkr(tmp_path, "-1000.0", IBKR_ROYAL_MAIL_BUY)
    t212 = tmp_path / "t212"
    t212.mkdir()
    name = '"International Distributions Services"'
    _write(
        t212 / "export.csv",
        [
            T212_HEADER,
            'Deposit,2021-01-10T09:00:00+00:00,,,,,,,,,,5000.00,"GBP",dep-1',
            (
                f"Market buy,2021-02-01T10:00:00+00:00,{RMG_ISIN},IDS,{name},"
                '10.0000000000,200.00,GBP,,,,2000.00,"GBP",buy-1'
            ),
            (
                f"Stock split close,2022-10-05T07:00:00+00:00,{RMG_ISIN},RMG,"
                '"Royal Mail",10.0000000000,200.00,GBP,,0.00,"GBP",2000.00,"GBP",'
                "close-1"
            ),
            (
                f"Stock split open,2022-10-05T07:00:00+00:00,{RMG_ISIN},IDS,{name},"
                '10.0000000000,200.00,GBP,,,,2000.00,"GBP",open-1'
            ),
            (
                f"Market sell,2023-06-01T10:00:00+00:00,{RMG_ISIN},IDS,{name},"
                '5.0000000000,300.00,GBP,,500.00,"GBP",1500.00,"GBP",sell-1'
            ),
        ],
    )

    result = _run(
        "--year", "2023", "--interactive-brokers-file", str(ibkr),
        "--trading212-dir", str(t212),
    )  # fmt: skip

    assert _holdings(result.stdout) == {"IDS": "15.00"}
    assert _figure(result.stdout, "Allowable costs") == "750.00"
    assert _figure(result.stdout, "Gain") == "750.00"


def test_a_raw_history_marked_lse_crosses_the_change(tmp_path: Path) -> None:
    """RAW rows marked LSE are Royal Mail's, before the change and after.

    1. 1 February 2021: buy 10 RMG at 200.
    2. 1 June 2023: sell 5 IDS at 300.

    5 x 200 = 1,000 allowed, 1,500 - 1,000 = 500 gain. Without the market,
    the sale finds no IDS held.
    """
    raw = _write(
        tmp_path / "raw.csv",
        [
            RAW_HEADER,
            "2021-02-01,BUY,RMG,10,200,0,GBP,LSE",
            "2023-06-01,SELL,IDS,5,300,0,GBP,LSE",
        ],
    )

    result = _run("--year", "2023", "--raw-file", str(raw))

    assert _holdings(result.stdout) == {"IDS": "5.00"}
    assert _figure(result.stdout, "Allowable costs") == "1,000.00"
    assert _figure(result.stdout, "Gain") == "500.00"


def test_a_schwab_call_written_as_mmc_expires_as_mrsh(tmp_path: Path) -> None:
    """OCC moved Marsh's option root to MRSH with the ticker, on 14 January 2026.

    1. 18 December 2025: buy 100 MMC at 200 dollars.
    2. 30 December 2025: write 1 MMC 20 March 2026 220 call for 299.35 dollars
       after fees.
    3. 20 March 2026: the call expires, shown as MRSH.

    The premium is the gain: 299.35 / 1.3126 (December 2025) = 228.06. The
    shares were bought as MMC and are held as MRSH.
    """
    schwab = _write(
        tmp_path / "schwab.csv",
        [
            SCHWAB_HEADER,
            "03/20/2026,Expired,MRSH 03/20/2026 220.00 C,Expiration,,1,,",
            (
                "12/30/2025,Sell to Open,MMC 03/20/2026 220.00 C,Open,$3.00,1,$0.65,"
                "$299.35"
            ),
            "12/18/2025,Buy,MMC,MARSH & MCLENNAN,$200.00,100,,-$20000.00",
            "12/17/2025,MoneyLink Deposit,,,,,,$50000.00",
        ],
    )

    result = _run("--year", "2025", "--schwab-file", str(schwab))

    assert _holdings(result.stdout) == {"MRSH": "100.00"}
    assert _figure(result.stdout, "Gain") == "228.06"


def test_a_schwab_vest_finds_its_award_price_across_the_change(
    tmp_path: Path,
) -> None:
    """The Equity Awards lapse says MMC and the history's vest says MRSH.

    1. 13 January 2026: 40 MMC lapse at a fair market value of 200 dollars,
       16 withheld, 24 delivered.
    2. 15 January 2026: the history shows 24 MRSH arriving.
    3. 25 March 2026: sell 10 MRSH at 220 dollars.

    Cost: 24 x 200 = 4,800 dollars / 1.3329 (January 2026) = 3,601.170, so
    10/24 of it = 1,500.488. Proceeds: 2,200 / 1.3573 (March 2026) =
    1,620.865. Gain: 1,620.865 - 1,500.488 = 120.38.
    """
    schwab = _write(
        tmp_path / "schwab.csv",
        [
            (
                '"Date","Action","Symbol","Description","Quantity","Price",'
                '"Fees & Comm","Amount"'
            ),
            (
                '"03/25/2026","Sell","MRSH","MARSH & MCLENNAN","10","$220.00","",'
                '"$2200.00"'
            ),
            '"01/15/2026","Stock Plan Activity","MRSH","MARSH & MCLENNAN","24","","",""',
        ],
    )
    lapse = {
        "Date": "01/13/2026",
        "Action": "Lapse",
        "Symbol": "MMC",
        "Quantity": "40",
        "Description": "Restricted Stock Lapse",
        "FeesAndCommissions": None,
        "DisbursementElection": None,
        "Amount": None,
        "TransactionDetails": [
            {
                "Details": {
                    "AwardDate": "01/13/2023",
                    "AwardId": "C0001",
                    "FairMarketValuePrice": "$200.00",
                    "SalePrice": "",
                    "SharesSoldWithheldForTaxes": "16",
                    "NetSharesDeposited": "24",
                    "Taxes": "$3200.00",
                }
            }
        ],
    }
    awards = tmp_path / "awards.json"
    awards.write_text(
        json.dumps(
            {"FromDate": "01/01/2026", "ToDate": "01/31/2026", "Transactions": [lapse]}
        ),
        encoding="utf-8",
    )

    result = _run(
        "--year", "2025", "--schwab-file", str(schwab),
        "--schwab-award-file", str(awards),
    )  # fmt: skip

    assert _holdings(result.stdout) == {"MRSH": "14.00"}
    assert _figure(result.stdout, "Gain") == "120.38"


def test_a_row_of_the_other_market_keeps_its_ticker(tmp_path: Path) -> None:
    """RAW RMG rows marked US are RMG Acquisition Corp's, not Royal Mail's.

    1. 2 June 2020: buy 100 RMG at 10 dollars, marked US.
    2. 1 October 2020: sell 40 of them at 12 dollars.
    3. Freetrade's Royal Mail rows: buy 10 RMG at 200, sell 5 at 300 in 2021.

    Two holdings: 60 RMG and 5 IDS. The 2021/22 sale is Royal Mail's alone:
    5 x 200 = 1,000 allowed, 1,500 - 1,000 = 500 gain.
    """
    raw = _write(
        tmp_path / "raw.csv",
        [
            RAW_HEADER,
            "2020-06-02,BUY,RMG,100,10,0,USD,US",
            "2020-10-01,SELL,RMG,40,12,0,USD,US",
        ],
    )
    freetrade = _write(
        tmp_path / "freetrade.csv", [FREETRADE_HEADER, *FREETRADE_ROYAL_MAIL]
    )

    result = _run(
        "--year", "2021", "--raw-file", str(raw), "--freetrade-file", str(freetrade)
    )  # fmt: skip

    assert _holdings(result.stdout) == {"IDS": "5.00", "RMG": "60.00"}
    assert _figure(result.stdout, "Allowable costs") == "1,000.00"
    assert _figure(result.stdout, "Gain") == "500.00"
    assert stderr_alerts(result.stderr) == []


def test_a_row_that_shows_nothing_beside_both_securities_is_refused(
    tmp_path: Path,
) -> None:
    """Freetrade shows Royal Mail under RMG and a RAW row a US security.

    A RAW RMG row with no market could be either, so the run stops and
    names both rows that show a security, and the row it cannot place.
    Freetrade reads its rows oldest first, so the Royal Mail row it names is
    the purchase on row 3.
    """
    freetrade = _write(
        tmp_path / "freetrade.csv", [FREETRADE_HEADER, *FREETRADE_ROYAL_MAIL]
    )
    raw = _write(
        tmp_path / "raw.csv",
        [
            RAW_HEADER,
            "2021-01-04,BUY,RMG,10,100,0,USD,US",
            "2021-01-05,BUY,RMG,10,100,0,USD,",
        ],
    )

    stderr = _refused(
        "--year", "2021", "--freetrade-file", str(freetrade), "--raw-file", str(raw)
    )  # fmt: skip

    assert re.search(
        r"ERROR: Ticker RMG names no ISIN or market on this row, and this run shows "
        r"two securities under RMG: Royal Mail, whose ticker changed to IDS, on\n"
        rf".*\(ISIN {RMG_ISIN}\), read from row 3 of {re.escape(str(freetrade))}\n"
        r"and another security on\n"
        rf".*read from row 2 of {re.escape(str(raw))}\n"
        r"so cgt-calc cannot tell which of them this row is\. Two securities under "
        r"one ticker would be pooled and matched as one holding\. If this row is in "
        r"your RAW file and you know where its security is listed, write that in "
        r"its market column: LSE for Royal Mail, or the other security's market if "
        r"it is US or LSE\. Rows you cannot place that way, such as rows of a broker "
        r"export, need the two securities calculated in separate runs; to split the "
        r"files and combine the reports, see "
        rf"{re.escape(SEPARATE_RUNS)}\n"
        rf".*read from row 3 of {re.escape(str(raw))}\n",
        stderr,
        re.DOTALL,
    ), stderr


def test_the_refusal_names_the_file_of_a_row_schwab_dropped(tmp_path: Path) -> None:
    """A cancelled Schwab purchase is still evidence, and still has a place.

    Schwab drops a Buy and its Cancel Buy before the shared loader records
    which file a row came from. The cancelled MMC rows were still exported as
    Marsh, and the refusal prints the first of them with its file and row.
    """
    schwab = _write(
        tmp_path / "schwab.csv",
        [
            SCHWAB_HEADER,
            "12/19/2025,Cancel Buy,MMC,MARSH & MCLENNAN,$200.00,10,$0.00,$0.00",
            "12/18/2025,Buy,MMC,MARSH & MCLENNAN,$200.00,10,$0.00,-$2000.00",
            "12/17/2025,Buy,MMC,MARSH & MCLENNAN,$201.00,5,$0.00,-$1005.00",
            "12/16/2025,MoneyLink Deposit,,,,,,$5000.00",
        ],
    )
    raw = _write(
        tmp_path / "raw.csv",
        [
            RAW_HEADER,
            "2019-06-03,BUY,MMC,1000,0.20,0,GBP,LSE",
            "2025-12-18,BUY,MMC,1,200,0,USD,",
        ],
    )

    stderr = _refused(
        "--year", "2025", "--schwab-file", str(schwab), "--raw-file", str(raw)
    )  # fmt: skip

    assert re.search(
        r"two securities under MMC: Marsh & McLennan Companies, whose ticker "
        r"changed to MRSH, on\n"
        rf".*read from row 2 of {re.escape(str(schwab))}\n"
        r"and another security on\n",
        stderr,
        re.DOTALL,
    ), stderr


def test_rows_that_show_nothing_stay_under_the_old_ticker(tmp_path: Path) -> None:
    """A RAW MMC history with no market could be Marsh's or the London company's.

    1. 18 December 2025: buy 10 MMC at 200 dollars.
    2. 25 March 2026: sell 4 MMC at 220 dollars.

    It stays one MMC holding, with a warning. 10 x 200 = 2,000 dollars /
    1.3126 = 1,523.69, 4/10 of it = 609.48 allowed; 880 / 1.3573 = 648.35
    proceeds; 38.87 gain.
    """
    raw = _write(
        tmp_path / "raw.csv",
        [
            "date,action,symbol,quantity,price,fees,currency",
            "2025-12-18,BUY,MMC,10,200,0,USD",
            "2026-03-25,SELL,MMC,4,220,0,USD",
        ],
    )

    result = _run("--year", "2025", "--raw-file", str(raw))

    assert _holdings(result.stdout) == {"MMC": "6.00"}
    assert _figure(result.stdout, "Allowable costs") == "609.48"
    assert _figure(result.stdout, "Gain") == "38.87"
    assert stderr_alerts(result.stderr) == [
        (
            "WARNING: Rows under MMC name no ISIN or market, and no row of this run "
            "exported under MMC names its ISIN or market, so they are kept as MMC. "
            "They may be Marsh & McLennan Companies, whose ticker changed to MRSH, "
            "or another security that used MMC. If they are Marsh & McLennan "
            "Companies rows in your RAW file, write US in their market column. See "
            "https://cgt-calc.uk/extra-data-and-options/#ticker-renames"
        )
    ]
