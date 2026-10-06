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


def test_a_prices_file_without_its_header_is_refused(tmp_path: Path) -> None:
    """A first line that is a price is not skipped as if it were the header."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text('"Apr 01, 2024",ACME,94.00\n"Apr 01, 2024",NEWCO,17.50\n')

    with pytest.raises(
        ParsingError, match="Unexpected header in share prices file"
    ) as excinfo:
        SharePrices(prices_file=prices_file)

    assert excinfo.value.row_index == 1


def test_an_empty_prices_file_holds_no_prices(tmp_path: Path) -> None:
    """A file with nothing in it has no header to check, and is not an error."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.touch()

    assert SharePrices(prices_file=prices_file).prices == {}


@pytest.mark.parametrize(
    "content",
    [
        'Date, Symbol, Price\n"Mar 08, 2021", FOO, 10.5\n',
        '\ufeffdate,symbol,price\n"Mar 08, 2021",FOO,10.5\n',
    ],
    ids=["capitals and a space after each comma", "byte-order mark"],
)
def test_a_prices_file_is_read_as_typed_or_saved(tmp_path: Path, content: str) -> None:
    """Capitals, a space after each comma and Excel's "CSV UTF-8" are accepted."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text(content, encoding="utf8")

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
    ("rows", "message"),
    [
        pytest.param(
            ("GOOG,300", "GOOG,270.50"),
            "row 3: Two rows price GOOG on 2021-02-16, at 300 and 270.50. Keep the "
            "right price and remove the other row.",
            id="same-ticker",
        ),
        pytest.param(
            ("META,300", "FB,270.50"),
            "row 3: Two rows price META on 2021-02-16, at 300 and 270.50. FB is an "
            "earlier ticker of META, so both rows price the same security. Keep the "
            "right price and remove the other row.",
            id="earlier-ticker",
        ),
    ],
)
def test_two_prices_for_one_security_on_one_day_are_refused(
    tmp_path: Path, rows: tuple[str, str], message: str
) -> None:
    """Keeping either price would be a guess, and the row order decided it."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text(
        "date,symbol,price\n" + "".join(f'"Feb 16, 2021",{row}\n' for row in rows)
    )

    with pytest.raises(ParsingError, match=re.escape(message)):
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


@pytest.mark.parametrize(
    "blank", ["", "   ", ",,"], ids=["empty line", "only spaces", "only commas"]
)
def test_a_prices_file_skips_a_blank_row(tmp_path: Path, blank: str) -> None:
    """A row with nothing in it is ignored, not read as a price."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text(
        f'date,symbol,price\n"Mar 08, 2021",FOO,10.5\n{blank}\n"Mar 09, 2021",FOO,11\n'
    )

    prices = SharePrices(prices_file=prices_file)

    assert prices.get(datetime.date(2021, 3, 8), "FOO", "USD") == Decimal("10.5")
    assert prices.get(datetime.date(2021, 3, 9), "FOO", "USD") == Decimal(11)


def test_a_half_filled_prices_row_is_reported_at_its_line(tmp_path: Path) -> None:
    """A row with any cell filled is not blank, and blank rows above it still count."""
    prices_file = tmp_path / "share_prices.csv"
    prices_file.write_text("date,symbol,price\n\n,FOO,10.5\n")

    with pytest.raises(ParsingError, match="Invalid date format") as excinfo:
        SharePrices(prices_file=prices_file)

    assert excinfo.value.row_index == 3


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
    ("symbol", "date", "currency", "given", "expected"),
    [
        pytest.param(
            "GOOG",
            "2021-03-25",
            "USD",
            None,
            "  GOOG: 1.00, £1,061.68\n",
            id="bundled-usd",
        ),
        pytest.param(
            "GOOG",
            "2021-03-25",
            "GBP",
            "1000",
            "  GOOG: 1.00, £1,000.00\n",
            id="file-gbp",
        ),
        pytest.param("GOOG", "2021-03-25", "GBP", None, None, id="bundled-gbp-refused"),
        # The vest is read as META, and the bundled price is filed under FB.
        pytest.param(
            "FB",
            "2021-02-16",
            "USD",
            None,
            "  META: 1.00, £198.43\n",
            id="bundled-under-earlier-ticker",
        ),
    ],
)
def test_a_vest_without_a_price_is_priced_in_its_own_currency(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    symbol: str,
    date: str,
    currency: str,
    given: str | None,
    expected: str | None,
) -> None:
    """A price from your file is in the vest's currency; a bundled one is USD.

    The bundled GOOG price on 2021-03-25 is $1,491.764, £1,061.68 at that
    month's rate. Read for a GBP vest it would be taken as £1,491.76. The
    bundled FB price on 2021-02-16 is $270.50, £198.43 at 1.3632, found for a
    vest read as META.
    """
    raw = tmp_path / "raw.csv"
    raw.write_text(
        "date,action,symbol,quantity,price,fees,currency\n"
        f"{date},STOCK_ACTIVITY,{symbol},1,,0,{currency}\n"
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
        day = datetime.date.fromisoformat(date).strftime("%b %d, %Y")
        prices.write_text(f'date,symbol,price\n"{day}",{symbol},{given}\n')
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
