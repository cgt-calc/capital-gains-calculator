"""Test the JSON Equity Awards export whose every transaction is a Lapse.

Schwab writes this export for a plan that delivers the vested shares to a
linked brokerage account: the awards account records the vest and its price
but never holds the shares. It therefore prices the ``Stock Plan Activity``
rows of a main history and imports nothing of its own, which is what the
award-price CSV does. The CSV form of the same account is already read that
way; these tests cover the JSON form.

Every fixture here is synthetic. The relationships between the two files were
observed in a real export; none of the symbols, dates, quantities or prices
were.
"""

from __future__ import annotations

import datetime
from decimal import Decimal
import json
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING

import pytest

from cgt_calc.args_parser import create_parser
from cgt_calc.exceptions import CgtError, ParsingError
from cgt_calc.parsers.schwab import AwardPrices, SchwabParser
from cgt_calc.parsers.schwab_equity_award_json import (
    JsonRowType,
    SchwabEquityAwardsParser,
)
from tests.utils import build_cmd, report_path, stderr_alerts

if TYPE_CHECKING:
    from collections.abc import Callable

    from cgt_calc.model import BrokerTransaction

LAPSE_PRICING = Path("tests") / "schwab" / "data" / "lapse_pricing"
MAIN_HISTORY = LAPSE_PRICING / "transactions.csv"
AWARD_JSON = LAPSE_PRICING / "awards.json"

EQUITY_AWARD = Path("tests") / "schwab" / "data" / "equity_award"
COMPLETE_JSON = EQUITY_AWARD / "schwab_equity_award_v2.json"
COMPLETE_CSV = EQUITY_AWARD / "schwab_equity_award_v2.csv"


@pytest.fixture(autouse=True)
def _reset_awards_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep changes to the class-level award prices out of other test modules."""
    monkeypatch.setattr(SchwabParser, "awards_prices", AwardPrices(award_prices={}))


def _lapse(
    date: str = "06/12/2023",
    symbol: str = "ZQX",
    *,
    quantity: str = "100",
    details: dict[str, JsonRowType] | None = None,
    detail_rows: list[dict[str, JsonRowType]] | None = None,
) -> dict[str, JsonRowType]:
    """One Lapse transaction, as the JSON export states it."""
    if detail_rows is None:
        stated = {
            "AwardDate": "06/12/2020",
            "AwardId": "C0001",
            "FairMarketValuePrice": "$10.00",
            "SalePrice": "",
            "SharesSoldWithheldForTaxes": "40",
            "NetSharesDeposited": "60",
            "Taxes": "$400.00",
        }
        stated.update(details or {})
        detail_rows = [{"Details": stated}]
    return {
        "Date": date,
        "Action": "Lapse",
        "Symbol": symbol,
        "Quantity": quantity,
        "Description": "Restricted Stock Lapse",
        "FeesAndCommissions": None,
        "DisbursementElection": None,
        "Amount": None,
        "TransactionDetails": detail_rows,
    }


def _export(
    tmp_path: Path, *transactions: dict[str, JsonRowType], name: str = "a.json"
) -> str:
    """Write an Equity Awards JSON export holding the given transactions."""
    target = tmp_path / name
    target.write_text(json.dumps({"Transactions": list(transactions)}), "utf-8")
    return str(target)


def _load(**flags: str) -> list[BrokerTransaction]:
    """Load through the CLI wiring rather than calling the parser directly."""
    argv = ["--year", "2023"]
    for flag, value in flags.items():
        argv += [f"--{flag.replace('_', '-')}", value]
    args = create_parser().parse_args(argv)
    return SchwabParser.load_from_args(args)


def _bare_main(tmp_path: Path) -> str:
    """Write a main history with no vest, so a test can be about the award file."""
    target = tmp_path / "main.csv"
    target.write_text(
        '"Date","Action","Symbol","Description","Quantity","Price",'
        '"Fees & Comm","Amount"\n'
        '"05/01/2023","MoneyLink Transfer","","Tfr BANK","","","","$10000.00"\n',
        encoding="utf-8",
    )
    return str(target)


def _prices(tmp_path: Path, award_file: str) -> AwardPrices:
    """Load a price-only export alongside a main history and return its prices."""
    assert _load(schwab_file=_bare_main(tmp_path), schwab_award_file=award_file) != []
    return SchwabParser.awards_prices


# --- What counts as a price-only export -------------------------------------


def test_an_export_of_nothing_but_lapses_prices_vests_and_imports_nothing() -> None:
    """The whole point: the JSON form now does what its CSV form always did."""
    transactions = _load(
        schwab_file=str(MAIN_HISTORY), schwab_award_file=str(AWARD_JSON)
    )

    assert transactions
    assert not any(
        transaction.source and transaction.source.file == AWARD_JSON
        for transaction in transactions
    )
    assert SchwabParser.awards_prices


def test_a_cash_row_alongside_the_lapses_is_refused_by_name(tmp_path: Path) -> None:
    """An empty result does not mean the file held nothing but lapses.

    Journal and Wire Transfer are discarded too, so this export would import
    nothing and price nothing. Naming the action is the only way the reader
    can tell why the file they can see prices in was not used for them.
    """
    award_file = _export(
        tmp_path,
        _lapse(),
        {"Date": "06/20/2023", "Action": "Journal", "Symbol": None, "Amount": "$1.00"},
    )

    with pytest.raises(ParsingError, match="Journal") as exc_info:
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)

    assert "award-price CSV" in str(exc_info.value)


def test_a_purchase_that_acquired_nothing_is_not_a_price_only_export(
    tmp_path: Path,
) -> None:
    """An ESPP purchase whose every share went to tax also yields no transaction.

    Dropping it is right: no shares reached the pool. Classifying the file by
    what came out would put a complete export on the pricing path, where
    nothing has been reasoned about how its acquisitions meet a main history.
    """
    award_file = _export(
        tmp_path,
        _lapse(),
        {
            "Date": "06/20/2023",
            "Action": "Deposit",
            "Symbol": "ZQX",
            "Quantity": "10",
            "Description": "ESPP",
            "TransactionDetails": [
                {
                    "Details": {
                        "PurchaseDate": "06/20/2023",
                        "PurchaseFairMarketValue": "$10.00",
                        "PurchasePrice": "$8.50",
                        "SubscriptionDate": "01/01/2023",
                        "SubscriptionFairMarketValue": "$10.00",
                        "Shares": "10",
                        "SharesSoldWithheldForTaxes": "10",
                        "NetSharesDeposited": "0",
                    }
                }
            ],
        },
    )

    with pytest.raises(CgtError, match="not supported yet"):
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)


def test_an_export_with_no_transactions_is_not_a_price_only_export(
    tmp_path: Path,
) -> None:
    """It keeps the empty-result handling it had, rather than gaining prices."""
    award_file = _export(tmp_path)

    with pytest.raises(CgtError, match="not supported yet"):
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)


def test_an_unknown_action_keeps_its_own_error(tmp_path: Path) -> None:
    """Classification does not get in front of the parser's own complaint."""
    award_file = _export(
        tmp_path,
        {
            "Date": "06/20/2023",
            "Action": "Rehypothecation",
            "Symbol": "ZQX",
            "Description": "Something new",
        },
    )

    with pytest.raises(ParsingError, match="Unknown action: Rehypothecation"):
        _load(schwab_award_file=award_file)


# --- Reading one price ------------------------------------------------------


def test_a_lapse_states_a_date_a_symbol_and_a_price(tmp_path: Path) -> None:
    """The three things the main history needs to cost a vest."""
    award_file = _export(tmp_path, _lapse("06/12/2023", "ZQX"))

    prices = _prices(tmp_path, award_file)

    assert prices.get(datetime.date(2023, 6, 12), "ZQX")[1] == 10


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        ("", "states no price"),
        (None, "states no price"),
        ("$0.00", "states a price of 0"),
        ("-$1.00", "states a price of -1"),
        ("not a number", "is not a number"),
    ],
)
def test_a_lapse_cgt_calc_cannot_cost_names_the_field(
    tmp_path: Path, price: str | None, expected: str
) -> None:
    """Every row here is a vest, so a row with no usable price is a failure.

    The award-price CSV is free to skip such a row because it holds several
    kinds of row. This export holds one.
    """
    award_file = _export(tmp_path, _lapse(details={"FairMarketValuePrice": price}))

    with pytest.raises(ParsingError, match=expected) as exc_info:
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)

    message = str(exc_info.value)
    assert "Transactions[0].TransactionDetails[0].Details" in message
    assert "a.json" in message


def test_a_lapse_that_omits_the_price_field_entirely_fails(tmp_path: Path) -> None:
    """An absent field and a blank one say the same thing here."""
    row = _lapse()
    del row["TransactionDetails"][0]["Details"]["FairMarketValuePrice"]
    award_file = _export(tmp_path, row)

    with pytest.raises(ParsingError, match="states no price"):
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)


def test_a_lapse_with_no_symbol_fails(tmp_path: Path) -> None:
    """A price nothing can be matched to a holding is not a price."""
    award_file = _export(tmp_path, _lapse(symbol="  "))

    with pytest.raises(ParsingError, match="names no symbol"):
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)


def test_a_lapse_with_an_unreadable_date_fails(tmp_path: Path) -> None:
    """The date is what the main history's activity is matched against."""
    award_file = _export(tmp_path, _lapse(date="12 June 2023"))

    with pytest.raises(ParsingError, match="is not a date cgt-calc reads"):
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)


def test_a_lapse_stating_no_date_fails(tmp_path: Path) -> None:
    """A price with no date cannot be matched to the vest it prices."""
    award_file = _export(tmp_path, _lapse(date=""))

    with pytest.raises(ParsingError, match=r"Transactions\[0\] states no Date"):
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)


def test_a_lapse_stating_two_grants_in_one_row_fails(tmp_path: Path) -> None:
    """One lapse prices one vest, and cgt-calc will not pick between two."""
    detail_rows = [
        {"Details": {"AwardId": "C0001", "FairMarketValuePrice": "$10.00"}},
        {"Details": {"AwardId": "C0002", "FairMarketValuePrice": "$11.00"}},
    ]
    award_file = _export(tmp_path, _lapse(detail_rows=detail_rows))

    with pytest.raises(ParsingError, match="holds 2 entries, not one"):
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)


@pytest.mark.parametrize(
    ("detail_rows", "expected"),
    [
        pytest.param([None], "states no fields", id="entry holds nothing"),
        pytest.param(["a grant"], "states no fields", id="entry is not an object"),
        pytest.param(
            [{"Details": None}], "Details states no fields", id="wrapper holds nothing"
        ),
        pytest.param(
            {"Details": {}}, "does not hold a list of grants", id="not a list at all"
        ),
    ],
)
def test_a_malformed_detail_container_names_itself(
    tmp_path: Path, detail_rows: JsonRowType, expected: str
) -> None:
    """A row shaped nothing like a grant is refused, not left to the arithmetic.

    Each of these used to escape as whatever the next operation raised, naming
    neither the file nor the field.
    """
    award_file = _export(tmp_path, _lapse(detail_rows=detail_rows))

    with pytest.raises(ParsingError, match=expected) as exc_info:
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)

    message = str(exc_info.value)
    assert "Transactions[0].TransactionDetails" in message
    assert "a.json" in message


def test_the_sort_value_wins_over_a_rounded_displayed_price(tmp_path: Path) -> None:
    """Some exports round the displayed figure on purpose.

    Every other number in this parser prefers the companion without comparing
    the two, and requiring them to agree would reject those files.
    """
    award_file = _export(
        tmp_path,
        _lapse(
            details={
                "FairMarketValuePrice": "$10.00",
                "FairMarketValuePriceSortValue": 10.004,
            }
        ),
    )

    prices = _prices(tmp_path, award_file)

    assert prices.get(datetime.date(2023, 6, 12), "ZQX")[1] == Decimal("10.004")


def test_a_lapse_stating_its_details_without_a_wrapper_is_read(
    tmp_path: Path,
) -> None:
    """The older schema puts the detail fields on the entry itself."""
    award_file = _export(
        tmp_path,
        _lapse(detail_rows=[{"AwardId": "C0001", "FairMarketValuePrice": "$10.00"}]),
    )

    prices = _prices(tmp_path, award_file)

    assert prices.get(datetime.date(2023, 6, 12), "ZQX")[1] == 10


def test_a_renamed_ticker_is_filed_under_the_name_it_is_looked_up_by(
    tmp_path: Path,
) -> None:
    """AwardPrices.get renames the symbol it is asked for before searching."""
    award_file = _export(tmp_path, _lapse(symbol="FB"))

    prices = _prices(tmp_path, award_file)

    assert prices.get(datetime.date(2023, 6, 12), "FB")[1] == 10
    assert "META" in prices.award_prices[datetime.date(2023, 6, 12)]


# --- Several grants on one day ----------------------------------------------


def test_grants_vesting_together_at_one_price_collapse_to_one_entry(
    tmp_path: Path,
) -> None:
    """The observed export states one row per grant, and they agree."""
    award_file = _export(
        tmp_path,
        _lapse(details={"AwardId": "C0001"}),
        _lapse(details={"AwardId": "C0002"}),
    )

    prices = _prices(tmp_path, award_file)

    assert prices.award_prices == {datetime.date(2023, 6, 12): {"ZQX": 10}}


def test_grants_vesting_together_at_different_prices_are_refused(
    tmp_path: Path,
) -> None:
    """The price map holds one price per day, so the last row would win.

    Two grants vesting on one day share that day's market value; where the
    export says otherwise, nothing in it says which figure is the cost.
    """
    award_file = _export(
        tmp_path,
        _lapse(details={"FairMarketValuePrice": "$10.00"}),
        _lapse(details={"FairMarketValuePrice": "$11.00"}),
    )

    with pytest.raises(ParsingError) as exc_info:
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)

    message = str(exc_info.value)
    assert "Transactions[0]" in message
    assert "Transactions[1]" in message
    assert "10" in message
    assert "11" in message


# --- Units --------------------------------------------------------------


@pytest.mark.parametrize(
    "priced",
    [
        pytest.param(["06/01/2021"], id="split after every priced row"),
        pytest.param(["07/20/2021"], id="a vest on the split itself"),
        pytest.param(["06/01/2021", "01/03/2024"], id="split inside the range"),
    ],
)
def test_a_split_that_could_have_restated_a_priced_vest_is_refused(
    tmp_path: Path, priced: list[str]
) -> None:
    """The price comes from this file and the share count from the other one.

    An export is generated after the split, so a vest before one can already
    have been restated even where no row in either file mentions a split. The
    boundary is inclusive: a vest on the day itself is the one least likely to
    have been restated consistently.
    """
    award_file = _export(tmp_path, *[_lapse(date, "NVDA") for date in priced])

    with pytest.raises(ParsingError, match="post-split units") as exc_info:
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)

    assert "NVDA" in str(exc_info.value)


@pytest.mark.parametrize(
    "award_file_of",
    [
        pytest.param(lambda p: _export(p, _lapse("06/01/2021", "NVDA")), id="tabled"),
        pytest.param(lambda p: str(AWARD_JSON), id="main history"),
    ],
)
def test_a_split_refusal_offers_the_alternative_conditionally(
    tmp_path: Path, award_file_of: Callable[[Path], str]
) -> None:
    """The complete export states both units, and may not be to hand.

    Which export an awards account produces is an inference from the fields
    the two layouts carry, so the message keys off the file the reader turns
    out to have. It also replaces the main history rather than joining it, so
    it can only be used when it holds everything the calculation needs.
    """
    main = _main_history_with_a_split(tmp_path, "01/08/2024")

    with pytest.raises(CgtError) as exc_info:
        _load(schwab_file=main, schwab_award_file=award_file_of(tmp_path))

    message = str(exc_info.value)
    assert "If you have a complete Equity Awards export" in message
    assert "only if it holds everything the calculation needs" in message
    assert "would be left out" in message


def test_the_tabled_split_refusal_does_not_tie_a_shorter_export_to_the_year(
    tmp_path: Path,
) -> None:
    """Every vest in the main history is priced as that history is read.

    The tax year asked for filters the report, not the parsing, so a vest
    outside it still needs a price. A shorter award export is only safe when
    it still covers every vest the main history states.
    """
    award_file = _export(tmp_path, _lapse("06/01/2021", "NVDA"))

    with pytest.raises(ParsingError) as exc_info:
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)

    message = str(exc_info.value)
    assert "If no vest in your main history needs a price on or before" in message
    assert "whatever tax year you are calculating" in message


def test_a_split_before_every_priced_vest_is_not_a_problem(tmp_path: Path) -> None:
    """Nothing dated after a split can have been restated for it."""
    award_file = _export(tmp_path, _lapse("06/11/2024", "NVDA"))

    assert _prices(tmp_path, award_file)


def test_a_split_of_another_symbol_is_not_a_problem(tmp_path: Path) -> None:
    """The refusal is per symbol, not per file."""
    award_file = _export(tmp_path, _lapse("06/12/2023", "ZQX"))

    assert _prices(tmp_path, award_file)


def test_a_split_of_a_symbol_the_award_file_does_not_price_is_ignored(
    tmp_path: Path,
) -> None:
    """A split only puts the units of the vests priced from lapses in doubt."""
    main = tmp_path / "transactions.csv"
    main.write_text(
        MAIN_HISTORY.read_text(encoding="utf-8")
        + '"01/08/2024","Stock Split","OTH","OTHER CORP","10","","",""\n',
        encoding="utf-8",
    )

    assert _load(schwab_file=str(main), schwab_award_file=str(AWARD_JSON))


def _main_history_with_a_split(tmp_path: Path, date: str, symbol: str = "ZQX") -> str:
    """Write the paired fixture's main history with one Stock Split added."""
    main = tmp_path / "transactions.csv"
    main.write_text(
        MAIN_HISTORY.read_text(encoding="utf-8")
        + f'"{date}","Stock Split","{symbol}","{symbol} CORP","100","","",""\n',
        encoding="utf-8",
    )
    return str(main)


# The paired fixture prices its earliest ZQX vest on 2023-06-12.
@pytest.mark.parametrize(
    "split",
    [
        pytest.param("06/12/2023", id="on the earliest priced vest"),
        pytest.param("01/08/2024", id="after every priced vest"),
    ],
)
def test_a_split_in_the_main_history_is_refused(tmp_path: Path, split: str) -> None:
    """The split table is not the only place a split can turn up.

    Checked over the whole history rather than per row, because the export can
    state the split after the vest whose units it puts in doubt.
    """
    main = _main_history_with_a_split(tmp_path, split)

    with pytest.raises(ParsingError, match="stock split") as exc_info:
        _load(schwab_file=main, schwab_award_file=str(AWARD_JSON))

    message = str(exc_info.value)
    assert "out by the split factor" in message
    # The file to go and look at, and the row in it.
    assert "transactions.csv" in message
    assert ", row 10" in message


def test_a_main_history_split_before_every_priced_vest_is_not_a_problem(
    tmp_path: Path,
) -> None:
    """A split has no earlier award record to have restated.

    Someone who held the share before joining the plan is the ordinary case,
    and the split table already draws this boundary. Drawing it in only one of
    the two places would refuse them for no reason.
    """
    main = _main_history_with_a_split(tmp_path, "05/10/2023")

    assert _load(schwab_file=main, schwab_award_file=str(AWARD_JSON))


def test_a_standalone_export_is_answered_before_its_rows_are_read(
    tmp_path: Path,
) -> None:
    """Classify, check the input combination, and only then harvest.

    Reading the rows first answers a file passed without the main history it
    exists to price with a complaint about its contents. This export would
    fail the split check, and that is not what its reader needs to hear.
    """
    award_file = _export(tmp_path, _lapse("06/01/2021", "NVDA"))

    with pytest.raises(CgtError, match="vest prices and no transactions"):
        _load(schwab_award_file=award_file)


@pytest.mark.parametrize(
    ("details", "field"),
    [
        pytest.param(
            {"FairMarketValuePriceSortValue": "not a number"},
            "FairMarketValuePriceSortValue",
            id="companion holds a string",
        ),
        pytest.param(
            {"FairMarketValuePrice": 10.5},
            "FairMarketValuePrice",
            id="displayed price is a JSON number",
        ),
    ],
)
def test_a_number_field_holding_something_else_names_itself(
    tmp_path: Path, details: dict[str, JsonRowType], field: str
) -> None:
    """Neither of these is a string this parser can read.

    Both used to escape as the exception the arithmetic happened to raise,
    which named no file and no field.
    """
    award_file = _export(tmp_path, _lapse(details=details))

    with pytest.raises(ParsingError, match="is not a number") as exc_info:
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)

    message = str(exc_info.value)
    assert f"Details.{field}" in message
    assert "a.json" in message


# --- What the rest of the parser sees ---------------------------------------


def test_the_complete_export_reaches_the_same_parser_through_either_option() -> None:
    """The new classification must not change what a Deposit-shape file does."""
    canonical = _load(schwab_award_file=str(COMPLETE_JSON))
    args = create_parser().parse_args(
        ["--year", "2023", "--schwab-equity-award-json", str(COMPLETE_JSON)]
    )

    assert canonical == SchwabEquityAwardsParser.load_from_args(args)
    assert canonical


def test_the_main_history_still_records_where_its_rows_came_from() -> None:
    """Nothing on the pricing path goes near the main history's own stamping."""
    transactions = _load(
        schwab_file=str(MAIN_HISTORY), schwab_award_file=str(AWARD_JSON)
    )

    for transaction in transactions:
        assert transaction.source is not None
        assert transaction.source.file == MAIN_HISTORY
        assert transaction.source.account is not None


def test_a_json_error_names_a_field_path_and_a_csv_error_names_a_row(
    tmp_path: Path,
) -> None:
    """A JSON document has no rows, so it points at the field instead."""
    award_file = _export(tmp_path, _lapse(details={"FairMarketValuePrice": ""}))
    csv_file = tmp_path / "complete.csv"
    lines = COMPLETE_CSV.read_text(encoding="utf-8").splitlines()
    csv_file.write_text("\n".join([lines[0], "just,one,cell"]) + "\n", encoding="utf-8")

    with pytest.raises(ParsingError) as json_error:
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=award_file)
    with pytest.raises(ParsingError) as csv_error:
        _load(schwab_award_file=str(csv_file))

    assert ", row " not in str(json_error.value)
    assert "TransactionDetails[0]" in str(json_error.value)
    assert ", row 2" in str(csv_error.value)


# --- A price list on its own ------------------------------------------------


def test_a_price_only_export_on_its_own_is_refused() -> None:
    """Today this reports zero gains from a file full of vests.

    The omission can move the result either way, so the objection is not that
    it understates the tax but that the figure cannot be checked.
    """
    with pytest.raises(CgtError, match="vest prices and no transactions") as exc_info:
        _load(schwab_award_file=str(AWARD_JSON))

    assert "--schwab-file <main history>" in str(exc_info.value)


def test_a_price_only_export_on_its_own_is_refused_by_the_old_option() -> None:
    """The old option cannot price a vest and never will.

    Pointing a user holding this export at it is a dead end, so its message
    shows the command that does work.
    """
    args = create_parser().parse_args(
        ["--year", "2023", "--schwab-equity-award-json", str(AWARD_JSON)]
    )

    with pytest.raises(CgtError, match="does not price vests") as exc_info:
        SchwabEquityAwardsParser.load_from_args(args)

    assert "--schwab-file <main history> --schwab-award-file" in str(exc_info.value)


def test_the_old_option_with_a_main_history_keeps_its_existing_error() -> None:
    """The registry loads the main history first, and that error already works.

    Getting in front of it would replace advice that now succeeds with advice
    saying the same thing.
    """
    args = create_parser().parse_args(
        [
            "--year",
            "2023",
            "--schwab-file",
            str(MAIN_HISTORY),
            "--schwab-equity-award-json",
            str(AWARD_JSON),
        ]
    )

    with pytest.raises(ParsingError, match="Cannot price a vest"):
        SchwabParser.load_from_args(args)


def test_a_price_only_export_may_accompany_the_old_option() -> None:
    """Pricing a vest and importing an award history is not a conflict.

    The award-price CSV has always been allowed here, and this export does the
    same job.
    """
    transactions = _load(
        schwab_file=str(MAIN_HISTORY),
        schwab_award_file=str(AWARD_JSON),
        schwab_equity_award_json=str(COMPLETE_JSON),
    )

    assert transactions


# --- The whole calculation --------------------------------------------------


def test_run_over_the_paired_fixture(request: pytest.FixtureRequest) -> None:
    """A main history priced entirely from the Lapse rows of a JSON export."""
    cmd = build_cmd(
        "--year",
        "2023",
        "--schwab-file",
        str(MAIN_HISTORY),
        "--schwab-award-file",
        str(AWARD_JSON),
        "--output",
        report_path(request),
    )
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", check=False)

    if result.returncode:
        pytest.fail(f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    assert stderr_alerts(result.stderr) == []
    expected_file = LAPSE_PRICING / "expected_output.txt"
    assert result.stdout == expected_file.read_text(encoding="utf-8"), (
        "Run with example files generated unexpected outputs, "
        "if you added new features update the test with:\n"
        f"{' '.join(param or chr(39) * 2 for param in cmd)} > {expected_file}"
    )
