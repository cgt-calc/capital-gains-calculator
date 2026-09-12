"""Unreadable dates in a Schwab Equity Awards export name the row they are in.

Every date the parser reads goes through one guard, so that a hand-edited or
hand-combined export answers with the row to go and look at instead of a bare
``ValueError`` or ``KeyError``. Each case here is one field that guard is
reached by, mutated in an export that parses otherwise.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cgt_calc.exceptions import ParsingError
from cgt_calc.parsers.schwab_equity_award_json import (
    JsonRowType,
    SchwabEquityAwardsParser,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from cgt_calc.parsers.schwab_equity_award_json import SchwabAwardTransaction

EQUITY_AWARD = Path("tests") / "schwab" / "data" / "equity_award"
JSON_FIXTURE = EQUITY_AWARD / "nvda_synthetic.json"
CSV_FIXTURE = EQUITY_AWARD / "nvda_synthetic.csv"

# An ISO date rather than a nonsense string: it is the form a reader is most
# likely to write by hand, and what a converted export tends to hold.
NOT_A_DATE = "2023-06-12"


def _rows() -> list[JsonRowType]:
    """Return the synthetic NVDA export's transactions, safe to mutate."""
    export = json.loads(JSON_FIXTURE.read_text(encoding="utf-8"))
    rows: list[JsonRowType] = export["Transactions"]
    return copy.deepcopy(rows)


def _parse(rows: list[JsonRowType]) -> list[SchwabAwardTransaction]:
    """Read those transactions back as the export they came from."""
    content = json.dumps({"Transactions": rows})
    return SchwabEquityAwardsParser.read_transactions(
        io.StringIO(content), JSON_FIXTURE
    )


def _find(rows: list[JsonRowType], action: str, description: str) -> JsonRowType:
    """Return the first transaction stating that action and description."""
    for row in rows:
        if row["Action"] == action and row["Description"] == description:
            return row
    raise AssertionError(f"no {action} / {description} row in {JSON_FIXTURE}")


def _row(row: JsonRowType) -> JsonRowType:
    """Return the transaction itself."""
    return row


def _details(row: JsonRowType) -> JsonRowType:
    """Return the single lot or grant the transaction states."""
    details: JsonRowType = row["TransactionDetails"][0]["Details"]
    return details


# Every date field the parser reads, named by the row that states it. The
# expected wording is the whole point of the test, so it is spelled out
# rather than derived from the row.
DATE_FIELDS = [
    pytest.param(
        "Lapse", "Restricted Stock Lapse", _row, "Date", "The Lapse of NVDA", id="lapse"
    ),
    pytest.param(
        "Deposit",
        "ESPP",
        _details,
        "PurchaseDate",
        "The Deposit of NVDA on 02/26/2021",
        id="espp-purchase",
    ),
    pytest.param(
        "Deposit",
        "RS",
        _details,
        "VestDate",
        "The Deposit of NVDA on 09/15/2021",
        id="vest",
    ),
    pytest.param("Sale", "Share Sale", _row, "Date", "The Sale of NVDA", id="sale"),
    pytest.param(
        "Dividend", "Credit", _row, "Date", "The Dividend of NVDA", id="dividend"
    ),
]

PARAMETRISED = pytest.mark.parametrize(
    ("action", "description", "container", "field", "where"), DATE_FIELDS
)


@PARAMETRISED
def test_a_date_that_cannot_be_read_names_the_row_and_the_value(
    action: str,
    description: str,
    container: Callable[[JsonRowType], JsonRowType],
    field: str,
    where: str,
) -> None:
    """The stated value is what a reader searches the export for."""
    rows = _rows()
    container(_find(rows, action, description))[field] = NOT_A_DATE

    with pytest.raises(ParsingError) as err:
        _parse(rows)

    assert (
        f"{where} states {field} as '{NOT_A_DATE}', which is not a date "
        "cgt-calc reads" in str(err.value)
    )


@PARAMETRISED
def test_a_date_field_that_is_absent_names_the_row_and_the_field(
    action: str,
    description: str,
    container: Callable[[JsonRowType], JsonRowType],
    field: str,
    where: str,
) -> None:
    """There is no value to quote, so the row has to carry the message."""
    rows = _rows()
    del container(_find(rows, action, description))[field]

    with pytest.raises(ParsingError) as err:
        _parse(rows)

    assert f"{where} states no {field}" in str(err.value)


def test_a_date_field_holding_nothing_reads_as_one_that_is_absent() -> None:
    """A blank cell states no date, and saying so beats quoting an empty one."""
    rows = _rows()
    _find(rows, "Sale", "Share Sale")["Date"] = ""

    with pytest.raises(ParsingError, match="The Sale of NVDA states no Date"):
        _parse(rows)


def test_a_gift_with_a_date_that_cannot_be_read_names_the_row() -> None:
    """A gift moves shares rather than money, and is named the same way."""
    content = json.dumps(
        {
            "Transactions": [
                {
                    "Date": NOT_A_DATE,
                    "Action": "Gift",
                    "Symbol": "GOOG",
                    "Quantity": "2",
                    "Description": "Share Transfer",
                    "FeesAndCommissions": None,
                    "Amount": None,
                    "TransactionDetails": [],
                }
            ]
        }
    )

    with pytest.raises(ParsingError, match="The Gift of GOOG states Date as"):
        SchwabEquityAwardsParser.read_transactions(io.StringIO(content), JSON_FIXTURE)


def test_the_csv_layout_names_the_row_the_date_is_on() -> None:
    """The CSV form is read by the same code, and adds its own row number."""
    lines = CSV_FIXTURE.read_text(encoding="utf-8").splitlines(keepends=True)
    # The lot of the ESPP purchase on the row above it, which is where that
    # purchase states the date it is priced on.
    lines[2] = lines[2].replace("02/26/2021", NOT_A_DATE, 1)
    assert NOT_A_DATE in lines[2]

    with pytest.raises(ParsingError) as err:
        SchwabEquityAwardsParser.read_transactions(
            io.StringIO("".join(lines)), CSV_FIXTURE
        )

    assert err.value.row_index == 2
    assert (
        "The Deposit of NVDA on 02/26/2021 states PurchaseDate as "
        f"'{NOT_A_DATE}'" in str(err.value)
    )


def test_an_award_date_that_cannot_be_read_still_parses() -> None:
    """AwardDate is quoted into a description and never read as a date.

    Guarding it would refuse an export cgt-calc can calculate from, so the
    harmless case stays harmless.
    """
    rows = _rows()
    vest = _find(rows, "Deposit", "RS")
    _details(vest)["AwardDate"] = NOT_A_DATE
    award_id = _details(vest)["AwardId"]

    descriptions = [transaction.description for transaction in _parse(rows)]

    assert f"Vest from Award Date {NOT_A_DATE} (ID {award_id})" in descriptions


def test_a_date_stated_as_a_bare_number_is_quoted_back() -> None:
    """The JSON decoder hands a bare number over as a Decimal, not a string.

    The row does state something, so the message quotes it rather than
    saying the field is not there.
    """
    rows = _rows()
    _find(rows, "Sale", "Share Sale")["Date"] = 6122023

    with pytest.raises(ParsingError, match="The Sale of NVDA states Date as Decimal"):
        _parse(rows)


def test_a_cash_row_with_no_symbol_is_named_by_its_action_alone() -> None:
    """A wire out of the account states no symbol, and still names its row."""
    content = json.dumps(
        {
            "Transactions": [
                {
                    "Date": NOT_A_DATE,
                    "Action": "Forced Disbursement",
                    "Symbol": None,
                    "Quantity": None,
                    "Description": "Debit",
                    "FeesAndCommissions": None,
                    "Amount": "-$2,490.13",
                    "TransactionDetails": [],
                }
            ]
        }
    )

    with pytest.raises(ParsingError, match="The Forced Disbursement states Date as"):
        SchwabEquityAwardsParser.read_transactions(io.StringIO(content), JSON_FIXTURE)
