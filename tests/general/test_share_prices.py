"""Tests for share prices."""

from __future__ import annotations

import datetime
from decimal import Decimal
import re
from typing import TYPE_CHECKING

import pytest

from cgt_calc.args_parser import create_parser
from cgt_calc.cli import calculate_cgt
from cgt_calc.exceptions import (
    BundledPriceCurrencyError,
    ParsingError,
    SharePriceMissingError,
)
from cgt_calc.share_prices import SharePrices

if TYPE_CHECKING:
    from pathlib import Path


def test_load_custom_file(tmp_path: Path) -> None:
    """Load prices from a custom file."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text('date,symbol,price\n"Mar 08, 2021",FOO,10.5\n')

    prices = SharePrices(prices_file=prices_file)

    assert prices.get(datetime.date(2021, 3, 8), "FOO", "USD") == Decimal("10.5")


def test_get_missing_price(tmp_path: Path) -> None:
    """Raise when no price is stored for the date and symbol."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text("date,symbol,price\n")

    prices = SharePrices(prices_file=prices_file)

    with pytest.raises(
        SharePriceMissingError,
        match=re.escape(
            "No share price for FOO on 2021-03-08: add it to a file passed with "
            "--prices-file"
        ),
    ):
        prices.get(datetime.date(2021, 3, 8), "FOO", "USD")


@pytest.mark.parametrize(
    "rows",
    [
        pytest.param(("META,300", "META,270.50"), id="same-ticker"),
        pytest.param(("META,300", "FB,270.50"), id="earlier-ticker"),
    ],
)
def test_two_prices_for_one_security_on_one_day_are_refused(
    tmp_path: Path, rows: tuple[str, str]
) -> None:
    """Keeping either price would be a guess, and the row order decided it."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text(
        "date,symbol,price\n" + "".join(f'"Feb 16, 2021",{row}\n' for row in rows)
    )

    with pytest.raises(
        ParsingError,
        match=re.escape(
            "row 3: Two rows price META on 2021-02-16, at 300 and 270.50. A price "
            "under an earlier ticker, such as FB for META, is the same security's. "
            "Keep the right price and remove the other row."
        ),
    ):
        SharePrices(prices_file=prices_file)


def test_the_same_price_under_an_earlier_ticker_is_accepted(tmp_path: Path) -> None:
    """A copied bundled FB row beside the user's own META row agrees with it.

    The docs say to copy in the bundled rows still needed, and the missing
    price error asked for a META row, so a file can hold both.
    """
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text(
        'date,symbol,price\n"Feb 16, 2021",META,270.50\n"Feb 16, 2021",FB,270.50\n'
    )

    prices = SharePrices(prices_file=prices_file)

    assert prices.get(datetime.date(2021, 2, 16), "META", "USD") == Decimal("270.50")


def test_invalid_row(tmp_path: Path) -> None:
    """Raise with row context on invalid rows."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text('date,symbol,price\n"Mar 08, 2021",FOO\n')

    with pytest.raises(ParsingError) as excinfo:
        SharePrices(prices_file=prices_file)

    assert excinfo.value.row_index == 2


def test_invalid_date(tmp_path: Path) -> None:
    """Report file and row context for unparsable dates."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text("date,symbol,price\n2021-03-08,FOO,10.5\n")

    with pytest.raises(ParsingError, match="Invalid date format") as excinfo:
        SharePrices(prices_file=prices_file)

    message = str(excinfo.value)
    assert excinfo.value.row_index == 2
    assert "2021-03-08" in message
    assert "share_prices.csv" in message


def test_invalid_price(tmp_path: Path) -> None:
    """Report file and row context for unparsable prices."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text('date,symbol,price\n"Mar 08, 2021",FOO,ten\n')

    with pytest.raises(ParsingError, match="Invalid decimal price") as excinfo:
        SharePrices(prices_file=prices_file)

    message = str(excinfo.value)
    assert excinfo.value.row_index == 2
    assert "ten" in message
    assert "share_prices.csv" in message


@pytest.mark.parametrize("price", ["-1", "NaN", "Infinity"])
def test_invalid_numeric_price(tmp_path: Path, price: str) -> None:
    """Negative and non-finite prices are reported as invalid file data."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text(f'date,symbol,price\n"Mar 08, 2021",FOO,{price}\n')

    with pytest.raises(ParsingError, match="finite and non-negative") as excinfo:
        SharePrices(prices_file=prices_file)

    assert excinfo.value.row_index == 2


@pytest.mark.parametrize(
    ("currency", "given", "expected"),
    [
        pytest.param("USD", None, "  GOOG: 1.00, £1,061.68\n", id="bundled-usd"),
        pytest.param("GBP", "1000", "  GOOG: 1.00, £1,000.00\n", id="file-gbp"),
        pytest.param("GBP", None, None, id="bundled-gbp-refused"),
    ],
)
def test_a_vest_without_a_price_is_priced_in_its_own_currency(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    currency: str,
    given: str | None,
    expected: str | None,
) -> None:
    """A price from your file is in the vest's currency; a bundled one is USD.

    The bundled GOOG price on 2021-03-25 is $1,491.764, £1,061.68 at that
    month's rate. Read for a GBP vest it would be taken as £1,491.76.
    """
    raw = tmp_path / "raw.csv"
    raw.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        f"2021-03-25,STOCK_ACTIVITY,GOOG,1,,0,{currency}\n"
    )
    args = [
        "--year",
        "2020",
        "--raw-file",
        str(raw),
        "--exchange-rates-file",
        "tests/exchange_rates_data.csv",
        "--isin-translation-file",
        "",
        "--spin-offs-file",
        "",
        "--no-report",
    ]
    if given is not None:
        prices = tmp_path / "prices.csv"
        prices.write_text(f'date,symbol,price\n"Mar 25, 2021",GOOG,{given}\n')
        args += ["--prices-file", str(prices)]

    if expected is None:
        with pytest.raises(
            BundledPriceCurrencyError,
            match=re.escape(
                "is in USD, but the transaction is in GBP. Enter the vest's price in "
                "the row, or give it in the transaction's currency in a file passed "
                "with --prices-file."
            ),
        ):
            calculate_cgt(create_parser().parse_args(args))
        return
    calculate_cgt(create_parser().parse_args(args))
    assert expected in capsys.readouterr().out


def test_a_vest_under_a_renamed_ticker_finds_the_price_under_the_old_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bundled table files Facebook's vests under FB, read as META.

    The vest is renamed to META when it is read, so a price filed under FB
    has to be renamed too, or it is never found. $270.50 at February 2021's
    rate of 1.3632 is £198.43.
    """
    raw = tmp_path / "raw.csv"
    raw.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        "2021-02-16,STOCK_ACTIVITY,FB,1,,0,USD\n"
    )
    args = [
        "--year",
        "2020",
        "--raw-file",
        str(raw),
        "--exchange-rates-file",
        "tests/exchange_rates_data.csv",
        "--isin-translation-file",
        "",
        "--spin-offs-file",
        "",
        "--no-report",
    ]

    calculate_cgt(create_parser().parse_args(args))

    assert "  META: 1.00, £198.43\n" in capsys.readouterr().out
