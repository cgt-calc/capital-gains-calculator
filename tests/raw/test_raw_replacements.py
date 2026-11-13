"""Test RAW rows that name a broker, and those that replace a row of its export."""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
import logging
from pathlib import Path
import re
import subprocess

import pytest

from cgt_calc.const import US_MARKET
from cgt_calc.exceptions import ParsingError
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    Isin,
    TransactionSource,
)
from cgt_calc.parsers.raw import RawParser
from cgt_calc.raw_replacements import resolve_raw_brokers
from tests.general.test_calc import SHYG_FUND, _eri, create_calculator, get_report
from tests.trading212.test_trading212_splits import _award_export, _award_vest
from tests.utils import build_cmd, run_cli, stderr_alerts

DOCS = "https://cgt-calc.uk/brokers/raw/#correct-a-row-in-a-broker-export"
RAW_HEADER = "date,action,symbol,quantity,price,fees,currency,broker,replaces"
SCHWAB_HEADER = (
    '"Date","Action","Symbol","Description","Quantity","Price","Fees & Comm","Amount"'
)


def _write(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _raw(tmp_path: Path, *rows: str) -> Path:
    return _write(tmp_path / "raw.csv", [RAW_HEADER, *rows])


def _monthly_payer(tmp_path: Path, june_tax: str, correction: str | None) -> Path:
    """Write a Schwab history of a monthly payer, newest row first as Schwab does.

    100 FOO bought on 1 May 2024 with the 1,000 deposited. Dividends of 100 on
    3 June and 200 on 30 June, each with tax withheld that day, and an
    `NRA Tax Adj` on 27 June: 24 days after the first payment, inside the 30
    days back a correction may lie, and 3 days before the second, inside the
    5 days ahead. Either payment could claim it, so cgt-calc leaves it out.
    The correction is row 4 of the file.
    """
    rows = [
        '"06/30/2024","NRA Withholding","FOO","FOO CORP","","","","-$30.00"',
        '"06/30/2024","Cash Dividend","FOO","FOO CORP","","","","$200.00"',
    ]
    if correction is not None:
        rows.append(
            f'"06/27/2024","NRA Tax Adj","FOO","FOO CORP","","","","{correction}"'
        )
    rows += [
        f'"06/03/2024","NRA Withholding","FOO","FOO CORP","","","","{june_tax}"',
        '"06/03/2024","Cash Dividend","FOO","FOO CORP","","","","$100.00"',
        '"05/01/2024","Buy","FOO","FOO CORP","100","$10.00","","-$1000.00"',
        '"05/01/2024","MoneyLink Transfer","","Tfr BANK","","","","$1000.00"',
    ]
    return _write(tmp_path / "schwab.csv", [SCHWAB_HEADER, *rows])


def _refused(cmd: list[str]) -> str:
    """Run a history that has to stop, and return its error line."""
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)
    assert result.returncode != 0, result.stdout
    return result.stderr


@pytest.mark.parametrize(
    ("june_tax", "correction", "raw_price"),
    [
        pytest.param("-$10.00", "-$5.00", "-5.00", id="further tax"),
        pytest.param("-$30.00", "$15.00", "15.00", id="refund"),
    ],
)
def test_a_tax_correction_dated_on_its_dividend_by_a_raw_row(
    tmp_path: Path, june_tax: str, correction: str, raw_price: str
) -> None:
    """A RAW row moves the correction onto 3 June without editing the export.

    Further tax: June's 10 withheld plus the 5 moved is 15, the 15% treaty
    rate on 100, and June 30's 30 is 15% of 200, so neither payment fails
    the treaty check. Refund: June's 30 less the 15 refunded is 15. Either
    way 45 was taken in all, and the balance is 1,000 deposited, 1,000
    spent, 300 of dividends and 45 of tax: 255, all of it Schwab's, with no
    `Unknown` ledger beside it. The refund is positive, as the export's row
    is.
    """
    schwab = _monthly_payer(tmp_path, june_tax, correction)
    raw = _raw(
        tmp_path,
        f"2024-06-03,DIVIDEND_TAX,FOO,1,{raw_price},0.00,USD,Charles Schwab,2024-06-27",
    )

    result = run_cli(
        build_cmd(
            "--year",
            "2024",
            "--schwab-file",
            str(schwab),
            "--raw-file",
            str(raw),
            "--no-report",
        )
    )

    assert stderr_alerts(result.stderr) == []
    assert (
        "Using RAW row 2 (DIVIDEND_TAX FOO on 2024-06-03) in place of the Charles "
        f"Schwab row dated 2024-06-27 (row 4 of {schwab})"
    ) in result.stderr
    assert "\n  Charles Schwab: 255.00 (USD)\n" in result.stdout
    assert "Unknown" not in result.stdout
    assert "\n  FOO: 300.00, excluding 45.00 taxed at source (USD)\n" in result.stdout


def test_an_added_row_joins_the_ledger_of_the_broker_it_names(tmp_path: Path) -> None:
    """A row naming a broker, however spelt, is booked with that broker's rows.

    The export has no correction here; the RAW row adds 5 of tax on 3 June.
    Booked under Schwab it finds Schwab's dividend that day: 10 + 5 = 15 is
    15% of 100, and the balance is 1,000 - 1,000 + 300 - 45 = 255. Booked
    apart it would find no dividend and take the balance below zero.
    """
    schwab = _monthly_payer(tmp_path, "-$10.00", None)
    raw = _raw(
        tmp_path, "2024-06-03,DIVIDEND_TAX,FOO,1,-5.00,0.00,USD,CHARLES  SCHWAB,"
    )

    result = run_cli(
        build_cmd(
            "--year",
            "2024",
            "--schwab-file",
            str(schwab),
            "--raw-file",
            str(raw),
            "--no-report",
        )
    )

    assert stderr_alerts(result.stderr) == []
    assert "Using RAW row" not in result.stderr
    assert "\n  Charles Schwab: 255.00 (USD)\n" in result.stdout
    assert "\n  FOO: 300.00, excluding 45.00 taxed at source (USD)\n" in result.stdout


SCHWAB_VEST = [
    '"06/15/2023","Sell","ZQX","ZQX CORP","60","$22.50","","$1350.00"',
    '"06/15/2023","Stock Plan Activity","ZQX","ZQX CORP INC CLASS A","60","","",""',
    '"05/01/2023","Buy","ZQX","ZQX CORP","100","$10.00","","-$1000.00"',
    '"05/01/2023","MoneyLink Transfer","","Tfr BANK","","","","$1000.00"',
]
LATER_SPLIT = '"09/15/2023","Stock Split","ZQX","ZQX CORP","100","","",""'
LAPSE_JSON = """{
  "FromDate": "01/01/2023",
  "ToDate": "12/31/2023",
  "Transactions": [{
    "Date": "06/12/2023", "Action": "Lapse", "Symbol": "ZQX", "Quantity": "100",
    "Description": "Restricted Stock Lapse", "FeesAndCommissions": null,
    "DisbursementElection": null, "Amount": null,
    "TransactionDetails": [{"Details": {
      "AwardDate": "06/12/2020", "AwardId": "C0001", "FairMarketValuePrice": "$20.00",
      "SalePrice": "", "SharesSoldWithheldForTaxes": "40", "NetSharesDeposited": "60",
      "Taxes": "$800.00"}}]
  }]
}"""
LAPSE_CSV = [
    (
        '"Date","Action","Symbol","Description","Quantity","FeesAndCommissions",'
        '"DisbursementElection","Amount","AwardDate","AwardId","FairMarketValuePrice",'
        '"SalePrice","SharesSoldWithheldForTaxes","NetSharesDeposited","Taxes"'
    ),
    (
        '"06/12/2023","Lapse","ZQX","Restricted Stock Lapse","100","","","","","","",'
        '"","","",""'
    ),
    '"","","","","","","","","06/12/2020","C0001","$20.00","","40","60","$800.00"',
]


@pytest.mark.parametrize(
    ("award_file", "split", "row", "held"),
    [
        pytest.param("awards.json", False, 3, "100", id="equity awards json"),
        pytest.param("awards.csv", True, 4, "200", id="award price csv, later split"),
    ],
)
def test_a_vest_taxed_on_another_day_replaced_by_a_raw_row(
    tmp_path: Path, award_file: str, split: bool, row: int, held: str
) -> None:
    """The vest takes the day the employer taxed and that day's value.

    The award file dates the vest of 60 on its Lapse, 12 June 2023, at 20.00.
    The employer taxed it on 15 June at 22.00, the day 60 were sold at 22.50,
    so the sale is matched to it under the same-day rule. At the June 2023
    rate of 1.2366 dollars to the pound, proceeds are 1,350 / 1.2366 =
    1,091.70, the cost 1,320 / 1.2366 = 1,067.44 and the gain 24.26.

    The holding is 100 + 60 - 60 = 100. A later Schwab split row adding 100
    still applies, making 200: the replacing row is in the export's account.
    """
    history = [LATER_SPLIT, *SCHWAB_VEST] if split else SCHWAB_VEST
    schwab = _write(tmp_path / "schwab.csv", [SCHWAB_HEADER, *history])
    awards = tmp_path / award_file
    if split:
        _write(awards, LAPSE_CSV)
    else:
        awards.write_text(LAPSE_JSON, encoding="utf-8")
    raw = _raw(
        tmp_path,
        "2023-06-15,STOCK_ACTIVITY,ZQX,60,22.00,0.00,USD,Charles Schwab,2023-06-12",
    )

    result = run_cli(
        build_cmd(
            "--year",
            "2023",
            "--schwab-file",
            str(schwab),
            "--schwab-award-file",
            str(awards),
            "--raw-file",
            str(raw),
            "--no-report",
        )
    )

    assert stderr_alerts(result.stderr) == []
    assert (
        "Using RAW row 2 (STOCK_ACTIVITY ZQX on 2023-06-15) in place of the Charles "
        f"Schwab row dated 2023-06-12 (row {row} of {schwab})"
    ) in result.stderr
    assert f"Final portfolio\n  ZQX: {held}.00\n" in result.stdout
    assert re.search(r"Disposal proceeds:\s+£1,091\.70\n", result.stdout)
    assert re.search(r"Allowable costs:\s+£1,067\.44\n", result.stdout)
    assert re.search(r"Gain:\s+£24\.26\n", result.stdout)


@pytest.mark.parametrize(
    ("vest", "raw_date", "message"),
    [
        pytest.param(
            "06/03/2024",
            "2024-06-05",
            "Cannot replace the NVDA vest of 30 dated 2024-06-03",
            id="vest before the split",
        ),
        pytest.param(
            "06/15/2024",
            "2024-06-08",
            "Cannot replace the NVDA vest of 30 dated 2024-06-15",
            id="replacing row before the split",
        ),
    ],
)
def test_an_equity_awards_vest_restated_for_a_split_is_not_replaced(
    tmp_path: Path, vest: str, raw_date: str, message: str
) -> None:
    """A vest the export restated for NVIDIA's split of 10 June 2024 is refused.

    The Equity Awards export states it in units after the split, while a RAW
    row is read in the units of its own date, so either date before the split
    would put 30 shares of the wrong size in the pool.
    """
    awards = _award_export(tmp_path, [_award_vest(vest, "NVDA", "30", "$115.00")])
    vest_date = dt.datetime.strptime(vest, "%m/%d/%Y").date()
    raw = _raw(
        tmp_path,
        f"{raw_date},STOCK_ACTIVITY,NVDA,30,120.00,0.00,USD,Charles Schwab,{vest_date}",
    )

    stderr = _refused(
        build_cmd(
            "--year",
            "2024",
            "--schwab-award-file",
            awards,
            "--raw-file",
            str(raw),
            "--no-report",
        )
    )

    assert (
        f"ERROR: While parsing {raw}, row 2: {message} in the Schwab Equity Awards "
        "export: cgt-calc reads it from that export in units after the NVDA "
        "split of 2024-06-10, while a RAW row is read in the units in force on "
        "its own date. Edit the vest's date and price in a working copy of the "
        f"Equity Awards export instead, keeping its quantity. See {DOCS}\n"
    ) in stderr


def test_an_equity_awards_vest_after_the_split_is_replaced(tmp_path: Path) -> None:
    """Both dates after the split leave nothing restated, so the vest is replaced.

    The 30 shares are held once: the export's vest is gone, not added to.
    """
    awards = _award_export(
        tmp_path, [_award_vest("06/15/2024", "NVDA", "30", "$115.00")]
    )
    raw = _raw(
        tmp_path,
        "2024-06-21,STOCK_ACTIVITY,NVDA,30,120.00,0.00,USD,Charles Schwab,2024-06-15",
    )

    result = run_cli(
        build_cmd(
            "--year",
            "2024",
            "--schwab-award-file",
            awards,
            "--raw-file",
            str(raw),
            "--no-report",
        )
    )

    # A JSON export has no rows to number, so the line names the file alone.
    assert (
        "Using RAW row 2 (STOCK_ACTIVITY NVDA on 2024-06-21) in place of the Charles "
        f"Schwab row dated 2024-06-15 ({awards})\n"
    ) in result.stderr
    assert "Final portfolio\n  NVDA: 30.00\n" in result.stdout


def _award_cash(date: str, action: str, amount: str) -> dict[str, object]:
    """Build an NVDA dividend or withholding, as the Equity Awards export states one."""
    return {
        "Date": date,
        "Action": action,
        "Symbol": "NVDA",
        "Quantity": None,
        "Description": "Debit" if amount.startswith("-") else "Credit",
        "FeesAndCommissions": None,
        "DisbursementElection": None,
        "Amount": amount,
        "TransactionDetails": [],
    }


def test_an_equity_awards_tax_row_before_the_split_is_replaced(tmp_path: Path) -> None:
    """Only a vest has a count to restate, so tax before the split is replaced.

    The export pays an NVDA dividend of 100 dollars on 3 June 2024 and withholds
    15 on 5 June, both before the split of 10 June. Moved onto the payment, the
    tax is 15% of 100, the US treaty rate, so no warning; the balance is
    100 - 15 = 85.
    """
    awards = _award_export(
        tmp_path,
        [
            _award_cash("06/05/2024", "Tax Withholding", "-$15.00"),
            _award_cash("06/03/2024", "Dividend", "$100.00"),
        ],
    )
    raw = _raw(
        tmp_path,
        "2024-06-03,DIVIDEND_TAX,NVDA,1,-15.00,0.00,USD,Charles Schwab,2024-06-05",
    )

    result = run_cli(
        build_cmd(
            "--year",
            "2024",
            "--schwab-award-file",
            awards,
            "--raw-file",
            str(raw),
            "--no-report",
        )
    )

    assert stderr_alerts(result.stderr) == []
    assert (
        "Using RAW row 2 (DIVIDEND_TAX NVDA on 2024-06-03) in place of the Charles "
        f"Schwab row dated 2024-06-05 ({awards})\n"
    ) in result.stderr
    assert "\n  Charles Schwab: 85.00 (USD)\n" in result.stdout
    assert "\n  NVDA: 100.00, excluding 15.00 taxed at source (USD)\n" in result.stdout


@pytest.mark.parametrize(
    ("value", "cost"),
    [
        pytest.param("880.00,0.00,USD", "7,064.30", id="dollars"),
        pytest.param("700.00,0.00,GBP", "7,000.00", id="pounds"),
    ],
)
def test_a_main_history_vest_before_the_split_is_replaced(
    tmp_path: Path, value: str, cost: str
) -> None:
    """Only the Equity Awards export restates vests, so this one is replaced.

    The main history's vest of 10 NVDA on 3 May 2024 is before the split of 10
    June, and is in the units of its own date, as the RAW row is. Re-dated to
    2 May at 880 dollars, it costs 8,800 at the May 2024 rate of 1.2457
    dollars to the pound, 7,064.30. At 700 pounds it costs 7,000.00: a vest
    the history prices can be restated in another currency, as a payslip
    states it, unlike one it leaves unpriced.
    """
    schwab = _write(
        tmp_path / "schwab.csv",
        [
            SCHWAB_HEADER,
            '"05/03/2024","Stock Plan Activity","NVDA","NVIDIA CORP","10","$900.00","",""',
        ],
    )
    raw = _raw(
        tmp_path,
        f"2024-05-02,STOCK_ACTIVITY,NVDA,10,{value},Charles Schwab,2024-05-03",
    )

    result = run_cli(
        build_cmd(
            "--year",
            "2024",
            "--schwab-file",
            str(schwab),
            "--raw-file",
            str(raw),
            "--no-report",
        )
    )

    assert stderr_alerts(result.stderr) == []
    assert (
        "Using RAW row 2 (STOCK_ACTIVITY NVDA on 2024-05-02) in place of the Charles "
        f"Schwab row dated 2024-05-03 (row 2 of {schwab})\n"
    ) in result.stderr
    assert f"\n  NVDA: 10.00, £{cost}\n" in result.stdout


UNPRICED_VEST = [
    '"08/18/2023","Sell","ZQX","ZQX CORP","20","$23.00","","$460.00"',
    '"08/18/2023","Stock Plan Activity","ZQX","ZQX CORP INC CLASS A","60","","",""',
    '"07/31/2023","Stock Plan Activity","ZQX","ZQX CORP INC CLASS A","40","$18.00","",""',
]
UNPRICED_RAW = (
    "2023-08-15,STOCK_ACTIVITY,ZQX,60,22.00,0.00,USD,Charles Schwab,2023-08-18"
)


def _unpriced_run(
    tmp_path: Path, history: list[str], raw_row: str, year: str = "2023"
) -> tuple[list[str], Path, Path]:
    """Return the command for a Schwab history and RAW file, with no award file."""
    schwab = _write(tmp_path / "schwab.csv", [SCHWAB_HEADER, *history])
    raw = _raw(tmp_path, raw_row)
    cmd = build_cmd(
        "--year",
        year,
        "--schwab-file",
        str(schwab),
        "--raw-file",
        str(raw),
        "--no-report",
    )
    return cmd, schwab, raw


def test_a_vest_without_a_price_is_replaced_without_an_award_file(
    tmp_path: Path,
) -> None:
    """The RAW row gives the vest the history leaves unpriced its date and value.

    No award file is given, so Schwab's vest of 60 posted on 18 August 2023
    has no price. The RAW row dates it 15 August at 22 dollars. The vest of 40
    on 31 July keeps the history's own 18 dollars. At the July 2023 rate of
    1.2732 dollars to the pound and August's 1.2886, the pool of 100 costs
    720 / 1.2732 + 1,320 / 1.2886 = 565.5042 + 1,024.3675 = 1,589.8718. The
    sale of 20 on 18 August finds no acquisition that day or in the 30 after,
    so it takes a fifth of the pool, 317.9744, against proceeds of
    460 / 1.2886 = 356.9766. The gain is rounded once worked out, 39.0022 to
    39.00, and the report states the cost as the proceeds less that gain,
    356.9766 - 39.00 = 317.98. Left on Schwab's 18 August, the vest would have
    been matched to the sale under the same-day rule instead, at
    440 / 1.2886 = 341.46. The 80 left cost 1,589.8718 - 317.9744 = 1,271.90.

    FOO's two-for-one split on 16 August falls between the vest's two dates,
    but it is another share's, so it does not stop the replacement. ZQX's own
    split on 25 September comes after both, so it does not either: it doubles
    the 80 left to 160 at the same cost.
    """
    history = [
        '"09/25/2023","Stock Split","ZQX","ZQX CORP","80","","",""',
        *UNPRICED_VEST[:2],
        '"08/16/2023","Stock Split","FOO","FOO CORP","10","","",""',
        UNPRICED_VEST[2],
        '"07/31/2023","Buy","FOO","FOO CORP","10","$5.00","","-$50.00"',
        '"07/31/2023","MoneyLink Transfer","","Tfr BANK","","","","$50.00"',
    ]
    cmd, schwab, _ = _unpriced_run(tmp_path, history, UNPRICED_RAW)

    result = run_cli(cmd)

    assert stderr_alerts(result.stderr) == []
    assert (
        "Using RAW row 2 (STOCK_ACTIVITY ZQX on 2023-08-15) in place of the Charles "
        f"Schwab row dated 2023-08-18 (row 4 of {schwab})\n"
    ) in result.stderr
    assert re.search(r"Disposal proceeds:\s+£356\.98\n", result.stdout)
    assert re.search(r"Allowable costs:\s+£317\.98\n", result.stdout)
    assert re.search(r"Gain:\s+£39\.00\n", result.stdout)
    assert "\n  ZQX: 160.00, £1,271.90\n" in result.stdout


def test_a_vest_without_a_price_after_a_known_split_is_replaced(
    tmp_path: Path,
) -> None:
    """A split cgt-calc knows of before both dates leaves the units alone.

    NVIDIA's splits of 20 July 2021 and 10 June 2024 are both before the vest
    of 10 posted on 21 June 2024 and the RAW row's 15 June. At 120 dollars, at
    the June 2024 rate of 1.2709 dollars to the pound, it costs
    1,200 / 1.2709 = 944.21.
    """
    history = ['"06/21/2024","Stock Plan Activity","NVDA","NVIDIA CORP","10","","",""']
    cmd, schwab, _ = _unpriced_run(
        tmp_path,
        history,
        "2024-06-15,STOCK_ACTIVITY,NVDA,10,120.00,0.00,USD,Charles Schwab,2024-06-21",
        year="2024",
    )

    result = run_cli(cmd)

    assert stderr_alerts(result.stderr) == []
    assert (
        "Using RAW row 2 (STOCK_ACTIVITY NVDA on 2024-06-15) in place of the Charles "
        f"Schwab row dated 2024-06-21 (row 2 of {schwab})\n"
    ) in result.stderr
    assert "\n  NVDA: 10.00, £944.21\n" in result.stdout


@pytest.mark.parametrize(
    ("history", "raw_row", "message"),
    [
        pytest.param(
            UNPRICED_VEST,
            "2023-08-15,STOCK_ACTIVITY,ZQX,60,,0.00,USD,Charles Schwab,2023-08-18",
            "ZQX vest of 60 dated 2023-08-18, which has no price in the Schwab "
            "history, with a row that does not state a positive price. Give the "
            "market value of one share on the day you acquired the shares.",
            id="no price",
        ),
        pytest.param(
            UNPRICED_VEST,
            "2023-08-15,STOCK_ACTIVITY,ZQX,60,0.00,0.00,USD,Charles Schwab,2023-08-18",
            "ZQX vest of 60 dated 2023-08-18, which has no price in the Schwab "
            "history, with a row that does not state a positive price. Give the "
            "market value of one share on the day you acquired the shares.",
            id="zero price",
        ),
        pytest.param(
            UNPRICED_VEST,
            "2023-08-15,STOCK_ACTIVITY,ZQX,60,22.00,0.00,GBP,Charles Schwab,2023-08-18",
            "ZQX vest of 60 dated 2023-08-18, which has no price in the Schwab "
            "history, with a row in GBP. Give the price in USD, as the history "
            "does.",
            id="another currency",
        ),
        pytest.param(
            UNPRICED_VEST,
            "2023-08-15,STOCK_ACTIVITY,ZQX,60,22.00,1.00,USD,Charles Schwab,2023-08-18",
            "ZQX vest of 60 dated 2023-08-18, which has no price in the Schwab "
            "history, with a row that has fees. A vest costs no fees: leave them "
            "at 0.",
            id="fees",
        ),
        pytest.param(
            [
                *UNPRICED_VEST,
                '"08/15/2023","Stock Split","ZQX","ZQX CORP","40","","",""',
            ],
            UNPRICED_RAW,
            "ZQX vest of 60 dated 2023-08-18, which has no price in the Schwab "
            "history, with a row dated 2023-08-15: ZQX split or consolidated on "
            "2023-08-15, so cgt-calc cannot tell whether the vest's quantity is "
            "in the units of 2023-08-15. Edit the vest's date, quantity and "
            "price in a working copy of the history instead.",
            id="split on the replacing row's date",
        ),
        pytest.param(
            [
                UNPRICED_VEST[0],
                '"08/18/2023","Stock Split","ZQX","ZQX CORP","40","","",""',
                *UNPRICED_VEST[1:],
            ],
            UNPRICED_RAW,
            "ZQX vest of 60 dated 2023-08-18, which has no price in the Schwab "
            "history, with a row dated 2023-08-15: ZQX split or consolidated on "
            "2023-08-18, so cgt-calc cannot tell whether the vest's quantity is "
            "in the units of 2023-08-15. Edit the vest's date, quantity and "
            "price in a working copy of the history instead.",
            id="split on the vest's date",
        ),
        pytest.param(
            [
                '"08/25/2023","Stock Split","ZQX","ZQX CORP","40","","",""',
                *UNPRICED_VEST,
            ],
            "2023-08-31,STOCK_ACTIVITY,ZQX,60,22.00,0.00,USD,Charles Schwab,2023-08-18",
            "ZQX vest of 60 dated 2023-08-18, which has no price in the Schwab "
            "history, with a row dated 2023-08-31: ZQX split or consolidated on "
            "2023-08-25, so cgt-calc cannot tell whether the vest's quantity is "
            "in the units of 2023-08-31. Edit the vest's date, quantity and "
            "price in a working copy of the history instead.",
            id="replacing row after the vest",
        ),
        pytest.param(
            [
                '"06/11/2024","Stock Plan Activity","NVDA","NVIDIA CORP","10","","",""',
            ],
            "2024-06-08,STOCK_ACTIVITY,NVDA,10,120.00,0.00,USD,Charles Schwab,2024-06-11",
            "NVDA vest of 10 dated 2024-06-11, which has no price in the Schwab "
            "history, with a row dated 2024-06-08: NVDA split or consolidated on "
            "2024-06-10, so cgt-calc cannot tell whether the vest's quantity is "
            "in the units of 2024-06-08. Edit the vest's date, quantity and "
            "price in a working copy of the history instead.",
            id="known split between the dates",
        ),
    ],
)
def test_a_replacement_of_a_vest_without_a_price_is_refused(
    tmp_path: Path, history: list[str], raw_row: str, message: str
) -> None:
    """With no price to fall back on, the RAW row has to state the whole cost.

    A missing or zero price, another currency or fees would each leave a cost
    other than the vest's market value. A split on either date or between
    them, from the history or one cgt-calc knows of (NVIDIA's of 10 June
    2024), may leave the vest's count in units of the wrong date.
    """
    cmd, _, raw = _unpriced_run(tmp_path, history, raw_row)

    stderr = _refused(cmd)

    assert (
        f"ERROR: While parsing {raw}, row 2: Cannot replace the {message} See {DOCS}\n"
    ) in stderr


@pytest.mark.parametrize("replaced", [True, False])
def test_a_dump_writes_the_vest_without_a_price_as_parsed(
    tmp_path: Path, replaced: bool
) -> None:
    """The dump is saved before the replacement, whether or not the run stops.

    It shows the vest as Schwab posted it, on 18 August with no price, and the
    RAW row beside it: what was read, not what the calculation used. That
    posted date is the one the RAW row names in `replaces`.
    """
    raw_row = UNPRICED_RAW if replaced else "2023-08-15,BUY,ZQX,1,22.00,0.00,USD,,"
    cmd, _, _ = _unpriced_run(tmp_path, UNPRICED_VEST, raw_row)
    destination = tmp_path / "parsed.csv"

    result = subprocess.run(
        [*cmd, "--dump-transactions", str(destination)],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )

    assert (result.returncode == 0) is replaced, result.stderr
    assert ("Cannot price a vest" in result.stderr) is not replaced
    with destination.open(encoding="utf-8", newline="") as dump:
        rows = list(csv.DictReader(dump))
    assert [
        (row["date"], row["action"], row["quantity"], row["price"], row["broker"])
        for row in rows
    ] == [
        ("2023-07-31", "STOCK_ACTIVITY", "40", "18.00", "Charles Schwab"),
        (
            ("2023-08-15", "STOCK_ACTIVITY", "60", "22.00", "Charles Schwab")
            if replaced
            else ("2023-08-15", "BUY", "1", "22.00", "Unknown")
        ),
        ("2023-08-18", "STOCK_ACTIVITY", "60", "", "Charles Schwab"),
        ("2023-08-18", "SELL", "20", "23.00", "Charles Schwab"),
    ]


def test_alike_vests_without_a_price_are_refused(tmp_path: Path) -> None:
    """Two vests of 60 posted the same day may have vested on different days.

    Neither has a price to tell it apart, so the RAW row replaces neither.
    """
    history = [UNPRICED_VEST[1], *UNPRICED_VEST]
    cmd, _, raw = _unpriced_run(tmp_path, history, UNPRICED_RAW)

    stderr = _refused(cmd)

    assert (
        f"ERROR: While parsing {raw}, row 2: Several Charles Schwab vests match "
        "this replacement and none has a price, so cgt-calc cannot tell which "
        "one it replaces:\n"
    ) in stderr
    assert (
        f"\nEdit the vests in a working copy of the export instead. See {DOCS}\n"
    ) in stderr
    assert stderr.count("read from row") == 2


@pytest.mark.parametrize(
    ("raw_rows", "message"),
    [
        pytest.param(
            ["2024-06-03,DIVIDEND_TAX,FOO,1,-5.00,0.00,USD,Charles Schwab,2024-06-28"],
            "row 2: Cannot find the Charles Schwab row this DIVIDEND_TAX row "
            "replaces: no Charles Schwab DIVIDEND_TAX row for FOO dated 2024-06-28 "
            "has amount -5.00 USD. The nearest, with the dates cgt-calc reads, "
            "which --dump-transactions writes:\n  2024-06-27 ",
            id="another date",
        ),
        pytest.param(
            ["2024-06-03,DIVIDEND_TAX,FOO,1,-6.00,0.00,USD,Charles Schwab,2024-06-27"],
            "row 2: Cannot find the Charles Schwab row this DIVIDEND_TAX row "
            "replaces: no Charles Schwab DIVIDEND_TAX row for FOO dated 2024-06-27 "
            "has amount -6.00 USD. The nearest, with the dates cgt-calc reads, "
            "which --dump-transactions writes:\n  2024-06-27 ",
            id="another amount",
        ),
        pytest.param(
            ["2024-06-03,DIVIDEND_TAX,BAR,1,-5.00,0.00,USD,Charles Schwab,2024-06-27"],
            "row 2: Cannot find the Charles Schwab row this DIVIDEND_TAX row "
            "replaces: no Charles Schwab DIVIDEND_TAX row for BAR dated 2024-06-27 "
            "has amount -5.00 USD. Charles Schwab has no DIVIDEND_TAX rows for BAR. "
            f"See {DOCS}\n",
            id="another symbol",
        ),
        pytest.param(
            [
                "2024-06-03,DIVIDEND_TAX,FOO,1,-5.00,0.00,USD,Charles Schwab,2024-06-27",
                "2024-06-04,DIVIDEND_TAX,FOO,1,-5.00,0.00,USD,Charles Schwab,2024-06-27",
            ],
            "row 3: Cannot find a Charles Schwab row left for this DIVIDEND_TAX "
            "row to replace: rows above it already replace every Charles Schwab "
            "DIVIDEND_TAX row for FOO dated 2024-06-27 like it. Remove the "
            f"repeated row. See {DOCS}",
            id="one row replaced twice",
        ),
    ],
)
def test_a_replacement_that_matches_no_row_is_refused(
    tmp_path: Path, raw_rows: list[str], message: str
) -> None:
    """A replacing row never stays as an added one, which would count it twice.

    The nearest rows are listed nearest first: 27 June, then 30 June.
    """
    schwab = _monthly_payer(tmp_path, "-$10.00", "-$5.00")
    raw = _raw(tmp_path, *raw_rows)

    stderr = _refused(
        build_cmd(
            "--year",
            "2024",
            "--schwab-file",
            str(schwab),
            "--raw-file",
            str(raw),
            "--no-report",
        )
    )

    assert f"ERROR: While parsing {raw}, {message}" in stderr
    if "nearest" in message:
        assert stderr.index("  2024-06-27 ") < stderr.index("  2024-06-30 ")


def _export_row(
    action: ActionType,
    date: dt.date,
    amount: Decimal,
    *,
    quantity: Decimal | None = None,
    price: Decimal | None = None,
    currency: str = "USD",
    account: str = "Charles Schwab #1",
    row: int = 2,
    symbol: str = "FOO",
    isin: Isin | None = None,
    market: str | None = None,
) -> BrokerTransaction:
    """Read a row of a Schwab export, in the account boundary given."""
    return BrokerTransaction(
        date=date,
        action=action,
        symbol=symbol,
        description="",
        quantity=quantity,
        price=price,
        fees=Decimal(0),
        amount=amount,
        currency=CurrencyCode(currency),
        broker="Charles Schwab",
        isin=isin,
        market=market,
        source=TransactionSource(
            parser="Charles Schwab",
            account=account,
            file=Path(f"{account}.csv"),
            row=row,
            index=row - 2,
        ),
    )


# A Canadian ISIN, made up, with a valid check digit.
CANADIAN_ISIN = Isin("CA1234567891")


def _vest(
    *,
    price: str = "20",
    currency: str = "USD",
    account: str = "Charles Schwab #1",
    isin: Isin | None = None,
    market: str | None = None,
) -> BrokerTransaction:
    """Return a vest of 60 FOO on 15 June 2023."""
    return _export_row(
        ActionType.STOCK_ACTIVITY,
        dt.date(2023, 6, 15),
        Decimal(60) * Decimal(price),
        quantity=Decimal(60),
        price=Decimal(price),
        currency=currency,
        account=account,
        row=2 if (price, currency, isin, market) == ("20", "USD", None, None) else 3,
        isin=isin,
        market=market,
    )


def _tax(*, isin: Isin | None = None, market: str | None = None) -> BrokerTransaction:
    """Return 5 dollars of FOO tax on 27 June 2024."""
    return _export_row(
        ActionType.DIVIDEND_TAX,
        dt.date(2024, 6, 27),
        Decimal(-5),
        row=2 if (isin, market) == (None, None) else 3,
        isin=isin,
        market=market,
    )


def _raw_rows(tmp_path: Path, *rows: str) -> list[BrokerTransaction]:
    return list(RawParser().load_from_file(_raw(tmp_path, *rows)))


VEST_RAW = "2023-06-12,STOCK_ACTIVITY,FOO,60,22.00,0.00,USD,Charles Schwab,2023-06-15"
TAX_RAW = "2024-06-03,DIVIDEND_TAX,FOO,1,-5.00,0.00,USD,Charles Schwab,2024-06-27"


@pytest.mark.parametrize(
    ("targets", "raw_row", "tax_year"),
    [
        pytest.param([_vest(), _vest(price="21")], VEST_RAW, 2023, id="price"),
        pytest.param([_vest(), _vest(currency="GBP")], VEST_RAW, 2023, id="currency"),
        pytest.param(
            [_vest(), _vest(account="Charles Schwab Equity Awards #2")],
            VEST_RAW,
            2023,
            id="account",
        ),
        pytest.param(
            [_vest(), _vest(isin=CANADIAN_ISIN)], VEST_RAW, 2023, id="vest ISIN"
        ),
        pytest.param(
            [_vest(), _vest(market=US_MARKET)], VEST_RAW, 2023, id="vest market"
        ),
        pytest.param([_tax(), _tax(isin=CANADIAN_ISIN)], TAX_RAW, 2024, id="tax ISIN"),
        pytest.param([_tax(), _tax(market=US_MARKET)], TAX_RAW, 2024, id="tax market"),
    ],
)
def test_competing_rows_that_differ_are_refused(
    tmp_path: Path, targets: list[BrokerTransaction], raw_row: str, tax_year: int
) -> None:
    """Two rows the RAW row could replace, which would leave different results.

    Vests at 20 and 21 dollars leave 60 shares costing 1,200 or 1,260; at 20
    dollars or 20 pounds the same figure is two costs; in two accounts the one
    replaced decides which a later single-row split counts as its own. A tax
    row or vest with an ISIN or a US market, and one without, would hand the
    replacing row a different security. Each pair differs in that one field.
    """
    with pytest.raises(ParsingError) as error:
        get_report(
            create_calculator(tax_year=tax_year, balance_check=False),
            [*targets, *_raw_rows(tmp_path, raw_row)],
        )

    message = error.value.message
    assert message.startswith(
        f"While parsing {tmp_path / 'raw.csv'}, row 2: Several Charles Schwab rows "
        "match this replacement and they differ, so cgt-calc cannot tell which "
        "one it replaces:\n"
    )
    assert message.endswith(
        f"\nEdit the row in a working copy of the export instead. See {DOCS}"
    )
    assert message.count("read from row") == 2


@pytest.mark.parametrize(
    ("target", "raw_row"),
    [
        pytest.param(_tax(isin=CANADIAN_ISIN), TAX_RAW, id="tax row with an ISIN"),
        pytest.param(_vest(market=US_MARKET), VEST_RAW, id="vest from a US market"),
    ],
)
def test_a_replacing_row_carries_the_security_of_the_row_it_replaces(
    tmp_path: Path, target: BrokerTransaction, raw_row: str
) -> None:
    """The row in the replaced row's place keeps the ISIN and market it had.

    They say what the security is: an ISIN gives a dividend's source country,
    and a US market keeps a US symbol from being read as a London fund's
    ticker. The run reads both from every row before the replacement, so no
    report can show them change; this checks the row the replacement hands
    on, so that they hold whatever runs after it.
    """
    raw_rows = _raw_rows(tmp_path, raw_row)

    resolved = resolve_raw_brokers([target, *raw_rows])

    assert len(resolved) == 1
    (replacing,) = resolved
    assert replacing.date == raw_rows[0].date
    assert (replacing.isin, replacing.market) == (target.isin, target.market)


def test_a_vest_of_another_quantity_is_not_the_row_replaced(tmp_path: Path) -> None:
    """A replacing vest keeps the count of the one it replaces: 59 finds no 60."""
    raw_rows = _raw_rows(
        tmp_path,
        "2023-06-12,STOCK_ACTIVITY,FOO,59,22.00,0.00,USD,Charles Schwab,2023-06-15",
    )

    with pytest.raises(ParsingError) as error:
        get_report(
            create_calculator(tax_year=2023, balance_check=False), [_vest(), *raw_rows]
        )

    assert error.value.message.startswith(
        f"While parsing {tmp_path / 'raw.csv'}, row 2: Cannot find the Charles "
        "Schwab row this STOCK_ACTIVITY row replaces: no Charles Schwab "
        "STOCK_ACTIVITY row for FOO dated 2023-06-15 has quantity 59."
    )


def test_equal_tax_rows_in_two_accounts_are_interchangeable(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Tax is booked by broker, not account, so either equal row can be replaced.

    The stock plan and brokerage accounts each have 5 of tax on 27 June. One
    moves onto 3 June, where 10 + 5 = 15 is 15% of 100. The other is still
    claimed by both payments and left out with its warning, once.
    """
    june_3, june_27, june_30 = (dt.date(2024, 6, d) for d in (3, 27, 30))
    awards = "Charles Schwab Equity Awards #2"
    transactions = [
        _export_row(ActionType.DIVIDEND, june_3, Decimal(100)),
        _export_row(ActionType.DIVIDEND_TAX, june_3, Decimal(-10)),
        _export_row(ActionType.DIVIDEND_TAX, june_27, Decimal(-5), row=4),
        _export_row(ActionType.DIVIDEND_TAX, june_27, Decimal(-5), account=awards),
        _export_row(ActionType.DIVIDEND, june_30, Decimal(200)),
        _export_row(ActionType.DIVIDEND_TAX, june_30, Decimal(-30)),
        *_raw_rows(
            tmp_path,
            "2024-06-03,DIVIDEND_TAX,FOO,1,-5.00,0.00,USD,Charles Schwab,2024-06-27",
        ),
    ]
    caplog.set_level(logging.INFO)

    get_report(create_calculator(tax_year=2024, balance_check=False), transactions)

    assert caplog.text.count("Using RAW row 2") == 1
    assert [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ] == [
        (
            "Dividend tax of -5.00 USD for FOO on 2024-06-27 could have been taken "
            "from the dividend on 2024-06-03 or 2024-06-30, so it is left out of "
            "the report! Date it on the one it belongs to to have it counted. A "
            "RAW row can replace a row of a broker export with the right date: "
            f"see {DOCS}"
        )
    ]


@pytest.mark.parametrize(
    ("exported", "message"),
    [
        pytest.param(
            True,
            "The broker 'Charles Schwabb' is not the broker of any other input in "
            "this run. Leave the broker blank to keep the row in the RAW file's own "
            "ledger, or use one of: Charles Schwab.",
            id="another broker loaded",
        ),
        pytest.param(
            False,
            "The broker 'Charles Schwabb' is not the broker of any other input in "
            "this run, and no other input names one. Leave the broker blank to "
            "keep the row in the RAW file's own ledger.",
            id="only RAW rows",
        ),
    ],
)
def test_a_broker_no_other_input_is_from_is_refused(
    tmp_path: Path, exported: bool, message: str
) -> None:
    """A typo would open a ledger of its own, and a RAW row cannot vouch for itself.

    The second RAW row names the same broker, which is still refused. A
    fund's report of excess income comes with the placeholder broker `N/A`,
    which is no broker's, so it is not offered either.
    """
    raw_rows = _raw_rows(
        tmp_path,
        "2024-06-03,TRANSFER,,1,5.00,0.00,USD,Charles Schwabb,",
        "2024-06-03,TRANSFER,,1,5.00,0.00,USD,Charles Schwabb,",
    )
    exported_rows = [_eri(dt.date(2024, 6, 30), SHYG_FUND, 1)]
    if exported:
        exported_rows.append(
            _export_row(ActionType.DIVIDEND, dt.date(2024, 6, 3), Decimal(100))
        )

    with pytest.raises(ParsingError) as error:
        get_report(
            create_calculator(tax_year=2024, balance_check=False),
            [*exported_rows, *raw_rows],
        )

    assert error.value.message == (
        f"While parsing {tmp_path / 'raw.csv'}, row 2: {message} See {DOCS}"
    )
