"""A dividend, or a dividend and its tax, in currencies that cannot be added.

Each stop names the dividend and says what to do. Where the two amounts are
in GBP and one foreign currency, `--autoconvert-currency` is the fix; between
two foreign currencies it does not help. The bare texts are owned by the
model and converter tests; these tests own what the command line prints.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import HmrcRateMissingError
from tests.utils import build_cmd

if TYPE_CHECKING:
    from collections.abc import Sequence

GUIDE = (
    "See https://cgt-calc.uk/extra-data-and-options/"
    "#income-reported-in-more-than-one-currency"
)
SCHWAB_HEADER = "Date,Action,Symbol,Description,Price,Quantity,Fees & Comm,Amount\n"
RAW_HEADER = "date,action,symbol,quantity,price,fees,currency\n"

# One AAA dividend on 3 June 2024, a date the test rates already hold for
# USD. Each RAW row is a quantity and a price, so its amount is their product.
# No figure ends in 0, which an error would print without.
SCHWAB_USD = "06/03/2024,Qualified Dividend,AAA,AAA INC,,,,$5.25\n"
RAW_GBP = "2024-06-03,DIVIDEND,AAA,1,3.95,0,GBP\n"
RAW_USD = "2024-06-03,DIVIDEND,AAA,1,5.25,0,USD\n"
RAW_EUR = "2024-06-03,DIVIDEND,AAA,1,4.55,0,EUR\n"

GBP_ADVICE = (
    "If they are separate payments of this dividend, such as one to each of "
    "two accounts, pass --autoconvert-currency to add them together in pounds."
)
FOREIGN_ADVICE = (
    "--autoconvert-currency combines GBP with one foreign currency; it does not "
    "convert between foreign currencies. Report these rows in one currency."
)


def _refused(
    tmp_path: Path,
    *,
    schwab: Sequence[str] = (),
    raw: Sequence[str] = (),
    autoconvert: bool = False,
    year: str = "2024",
) -> str:
    """Run a tax year over the given rows, which must stop; return stderr.

    A run of its own, with the shared rates file, as a user would make it.
    """
    # Tax in a currency the account holds no cash in overdraws it; that check
    # is not what these tests are about.
    args = ["--year", year, "--no-report", "--no-balance-check"]
    if schwab:
        schwab_file = tmp_path / "s.csv"
        schwab_file.write_text(SCHWAB_HEADER + "".join(schwab), encoding="utf-8")
        args += ["--schwab-file", str(schwab_file)]
    if raw:
        raw_file = tmp_path / "raw.csv"
        raw_file.write_text(RAW_HEADER + "".join(raw), encoding="utf-8")
        args += ["--raw-file", str(raw_file)]
    if autoconvert:
        args.append("--autoconvert-currency")
    result = subprocess.run(
        build_cmd(*args), capture_output=True, encoding="utf-8", check=False
    )
    assert result.returncode == 1, result.stderr
    return result.stderr


def _schwab_row(tmp_path: Path) -> str:
    """Return the Schwab dividend as an error lists it: what it says, then where."""
    return (
        "2024-06-03 Dividend AAA, amount 5.25 USD\n"
        f'  Charles Schwab "AAA INC", read from row 2 of {tmp_path / "s.csv"}'
    )


def _raw_row(tmp_path: Path, row: int, price: str, currency: str) -> str:
    """Return a RAW dividend of one unit as an error lists it.

    A RAW file names no broker, so the row's broker is "Unknown".
    """
    return (
        f"2024-06-03 Dividend 1 AAA, price {price} {currency}, amount {price}\n"
        f"  Unknown, read from row {row} of {tmp_path / 'raw.csv'}"
    )


def test_a_dividend_in_gbp_and_a_foreign_currency_names_both_rows_and_the_option(
    tmp_path: Path,
) -> None:
    """One broker pays in USD and another in GBP, and the option is off.

    The Schwab file is read before the RAW file, so its USD row is the
    dividend's total when the GBP row arrives: "USD and GBP", in that order.
    """
    err = _refused(tmp_path, schwab=[SCHWAB_USD], raw=[RAW_GBP])

    assert err.endswith(
        "ERROR: Cannot combine amounts in different currencies: USD and GBP. "
        "The AAA dividend on 2024-06-03 is reported in both:\n"
        f"{_schwab_row(tmp_path)}\n"
        f"{_raw_row(tmp_path, 2, '3.95', 'GBP')}\n"
        f"{GBP_ADVICE} {GUIDE}\n\n"
    )


def test_a_gbp_total_meeting_a_foreign_row_names_its_first_gbp_row(
    tmp_path: Path,
) -> None:
    """Two GBP payments are read before a USD one, and the option is off.

    The GBP rows (2 and 3) add up, so the USD row (4) clashes with a GBP
    total: "GBP and USD". The row shown beside it is the first in GBP, row 2,
    and GBP is still one of the two, so the option is the fix.
    """
    err = _refused(
        tmp_path, raw=[RAW_GBP, "2024-06-03,DIVIDEND,AAA,1,2.15,0,GBP\n", RAW_USD]
    )

    assert err.endswith(
        "ERROR: Cannot combine amounts in different currencies: GBP and USD. "
        "The AAA dividend on 2024-06-03 is reported in both:\n"
        f"{_raw_row(tmp_path, 2, '3.95', 'GBP')}\n"
        f"{_raw_row(tmp_path, 4, '5.25', 'USD')}\n"
        f"{GBP_ADVICE} {GUIDE}\n\n"
    )


def test_a_missing_exchange_rate_keeps_its_own_message(tmp_path: Path) -> None:
    """With the option, a GBP row needs the other currency's rate to be added.

    HMRC has no rate for VES in June 2010, the bolivar soberano dating from
    2018, and rates before February 2015 are never downloaded. The stop is
    the missing rate, not a clash of currencies the option already accepts.
    """
    err = _refused(
        tmp_path,
        raw=[
            "2010-06-03,DIVIDEND,AAA,1,3.95,0,GBP\n",
            "2010-06-03,DIVIDEND,AAA,1,4.55,0,VES\n",
        ],
        autoconvert=True,
        year="2010",
    )

    missing = HmrcRateMissingError(
        "VES", dt.date(2010, 6, 3), Path("tests/exchange_rates_data.csv")
    )
    assert err.endswith(f"ERROR: {missing}\n\n")


@pytest.mark.parametrize(
    "autoconvert",
    [
        # Refused where the two amounts are added.
        pytest.param(False, id="without the option"),
        # Refused by the option itself, which converts only GBP.
        pytest.param(True, id="with the option"),
    ],
)
def test_a_dividend_in_two_foreign_currencies_names_both_rows(
    tmp_path: Path,
    autoconvert: bool,
) -> None:
    """USD at one broker and EUR at another: the option is no fix either way."""
    err = _refused(
        tmp_path,
        schwab=[SCHWAB_USD],
        raw=[RAW_EUR],
        autoconvert=autoconvert,
    )

    assert err.endswith(
        "ERROR: Cannot combine amounts in different currencies: USD and EUR. "
        "The AAA dividend on 2024-06-03 is reported in both:\n"
        f"{_schwab_row(tmp_path)}\n"
        f"{_raw_row(tmp_path, 2, '4.55', 'EUR')}\n"
        f"{FOREIGN_ADVICE} {GUIDE}\n\n"
    )


def test_the_row_named_beside_another_is_one_in_the_total_currency(
    tmp_path: Path,
) -> None:
    """With the option, a GBP row read first is folded into the USD total.

    The total is then stated in USD, so the EUR row clashes with USD, and the
    row to show beside it is the USD one (row 3), not the day's first (row 2).
    """
    err = _refused(
        tmp_path,
        raw=[RAW_GBP, RAW_USD, RAW_EUR],
        autoconvert=True,
    )

    assert err.endswith(
        "ERROR: Cannot combine amounts in different currencies: USD and EUR. "
        "The AAA dividend on 2024-06-03 is reported in both:\n"
        f"{_raw_row(tmp_path, 3, '5.25', 'USD')}\n"
        f"{_raw_row(tmp_path, 4, '4.55', 'EUR')}\n"
        f"{FOREIGN_ADVICE} {GUIDE}\n\n"
    )


GBP_TAX_ADVICE = "If both currencies are right, pass --autoconvert-currency."
FOREIGN_TAX_ADVICE = "--autoconvert-currency works only when one of the two is in GBP."


@pytest.mark.parametrize(
    ("dividend", "tax", "autoconvert", "advice"),
    [
        # One of the two is GBP, so the option would accept them, whichever
        # of them it is.
        pytest.param(
            "USD", "GBP", False, GBP_TAX_ADVICE, id="gbp tax without the option"
        ),
        pytest.param(
            "GBP", "USD", False, GBP_TAX_ADVICE, id="gbp dividend without the option"
        ),
        # Neither is GBP: the option does not help, whether or not it is on.
        pytest.param(
            "USD", "EUR", False, FOREIGN_TAX_ADVICE, id="eur tax without the option"
        ),
        pytest.param(
            "USD", "EUR", True, FOREIGN_TAX_ADVICE, id="eur tax with the option"
        ),
    ],
)
def test_a_dividend_and_its_tax_in_two_currencies_names_the_dividend(
    tmp_path: Path,
    dividend: str,
    tax: str,
    autoconvert: bool,
    advice: str,
) -> None:
    """A dividend whose tax is in another currency, on the same day.

    The totals compared can each come from several rows, so no row is listed:
    the symbol, the date and both currencies find them.
    """
    err = _refused(
        tmp_path,
        raw=[
            f"2024-06-03,DIVIDEND,AAA,1,5.25,0,{dividend}\n",
            f"2024-06-03,DIVIDEND_TAX,AAA,1,-0.75,0,{tax}\n",
        ],
        autoconvert=autoconvert,
    )

    assert err.endswith(
        "ERROR: Dividend and withholding tax currencies do not match for AAA on "
        f"2024-06-03: dividend {dividend}, tax {tax}. {advice} {GUIDE}\n\n"
    )
