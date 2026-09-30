"""Test how --schwab-award-file routes each Schwab Equity Awards export.

Schwab states an Equity Awards account in more than one layout, and the option
takes any of them: the award-price CSV that prices vests listed in the main
history, and the complete transaction history as JSON or as CSV. Which one a
file holds is read from its contents, so these tests care about the contents
and deliberately not about the extension.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
import sys

import pytest

from cgt_calc.exceptions import CgtError, ParsingError
from cgt_calc.model import ActionType
from cgt_calc.parsers.schwab import SchwabParser
from cgt_calc.parsers.schwab_equity_award_json import SchwabAwardTransaction
from tests.schwab.helpers import load_via_cli

EQUITY_AWARD = Path("tests") / "schwab" / "data" / "equity_award"
RSU = Path("tests") / "schwab" / "data" / "rsu_settlement"
COMPLETE_JSON = EQUITY_AWARD / "schwab_equity_award_v2.json"
COMPLETE_CSV = EQUITY_AWARD / "schwab_equity_award_v2.csv"
# A complete CSV that also carries the award-price CSV's own columns.
AMBIGUOUS_COMPLETE_CSV = EQUITY_AWARD / "nvda_synthetic.csv"
PRICE_CSV = RSU / "awards.csv"
MAIN_HISTORY = RSU / "transactions.csv"
# A main history whose vests need no award file, so it loads beside any export.
PRICED_MAIN_HISTORY = Path("tests") / "schwab" / "data" / "schwab_transactions.csv"
LAPSE_PRICING = Path("tests") / "schwab" / "data" / "lapse_pricing"
LAPSE_MAIN_HISTORY = LAPSE_PRICING / "transactions.csv"
LAPSE_ONLY_JSON = LAPSE_PRICING / "awards.json"

PRICE_HEADER = (
    '"Date","Action","Symbol","Description","Quantity","FeesAndCommissions",'
    '"DisbursementElection","Amount","AwardDate","AwardId",'
    '"FairMarketValuePrice","SalePrice","SharesSoldWithheldForTaxes",'
    '"NetSharesDeposited","Taxes"\n'
)


def _renamed(tmp_path: Path, source: Path, name: str) -> str:
    """Copy a fixture under a name whose extension contradicts its contents."""
    target = tmp_path / name
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return str(target)


def test_a_complete_json_export_is_imported(tmp_path: Path) -> None:
    """The complete JSON history imports its own transactions."""
    transactions = load_via_cli(
        schwab_award_file=_renamed(tmp_path, COMPLETE_JSON, "a.csv")
    )

    assert transactions
    assert not SchwabParser.awards_prices


def test_a_complete_csv_export_is_imported(tmp_path: Path) -> None:
    """The complete CSV history does too, under a JSON name."""
    transactions = load_via_cli(
        schwab_award_file=_renamed(tmp_path, COMPLETE_CSV, "a.json")
    )

    assert transactions
    assert not SchwabParser.awards_prices


def test_a_complete_export_carrying_the_price_column_is_still_complete() -> None:
    """The complete layout has to be tested before the price layout.

    A real complete CSV also states FairMarketValuePrice, so checking the
    price columns first would route this file to the price reader: it would
    import none of its transactions and offer prices no vest asked for.
    """
    transactions = load_via_cli(schwab_award_file=str(AMBIGUOUS_COMPLETE_CSV))

    assert transactions
    assert not SchwabParser.awards_prices


def test_a_price_csv_supplies_prices_and_imports_nothing(tmp_path: Path) -> None:
    """The award-price CSV is still a price list, whatever it is called."""
    transactions = load_via_cli(
        schwab_award_file=_renamed(tmp_path, PRICE_CSV, "a.json")
    )

    assert transactions == []
    assert SchwabParser.awards_prices


def test_both_award_options_together_are_refused() -> None:
    """A price file beside a complete export is no longer a route.

    It was documented for an account with vests both delivered and held, which
    no export has shown exists. The old option keeps a destination of its own
    so that this is refused, where sharing one would drop a file in silence.
    """
    with pytest.raises(CgtError, match="were both given"):
        load_via_cli(
            schwab_file=str(MAIN_HISTORY),
            schwab_award_file=str(PRICE_CSV),
            schwab_equity_award_json=str(COMPLETE_JSON),
        )


@pytest.mark.parametrize("main", ["schwab_file", "schwab_dir"])
def test_a_complete_export_beside_a_main_history_is_imported_with_a_warning(
    main: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing reconciles the two, so both are imported and the run says so.

    The directory branch once returned only the directory's rows, which was
    harmless while this combination was refused.
    """
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "transactions.csv").write_text(
        PRICED_MAIN_HISTORY.read_text(encoding="utf-8"), encoding="utf-8"
    )
    history = str(PRICED_MAIN_HISTORY) if main == "schwab_file" else str(directory)

    with caplog.at_level(logging.WARNING):
        transactions = load_via_cli(
            **{main: history}, schwab_award_file=str(COMPLETE_JSON)
        )

    award_rows = [t for t in transactions if isinstance(t, SchwabAwardTransaction)]
    assert len(award_rows) == 8
    assert len(transactions) > len(award_rows)
    warnings = [
        r.getMessage()
        for r in caplog.records
        if "alongside the main Schwab history" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert warnings[0].startswith(f"{COMPLETE_JSON} was imported")
    assert "counted twice" in warnings[0]


def test_a_vest_beside_a_complete_export_says_that_export_prices_nothing() -> None:
    """The award file was given, so the error must not say it was not.

    A complete export states no price for a vest in the main history, and the
    way out is a different file, not the same option again.
    """
    with pytest.raises(ParsingError, match="Cannot price a vest") as exc_info:
        load_via_cli(
            schwab_file=str(MAIN_HISTORY), schwab_award_file=str(COMPLETE_JSON)
        )

    message = str(exc_info.value)
    assert "is a complete Equity Awards export, which prices no vest" in message
    assert "Pass the award-price CSV with --schwab-award-file instead" in message


def test_the_old_option_routes_like_the_canonical_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """It warns, then reads its file by content as --schwab-award-file does.

    Before, it only added transactions, so a price-only export given to it
    beside a main history priced nothing and the run stopped at the first vest.
    """
    with caplog.at_level(logging.WARNING):
        transactions = load_via_cli(
            schwab_file=str(LAPSE_MAIN_HISTORY),
            schwab_equity_award_json=str(LAPSE_ONLY_JSON),
        )

    assert (
        "Option '--schwab-equity-award-json' is deprecated; use "
        "'--schwab-award-file' instead." in caplog.text
    )
    vests = [t for t in transactions if t.action is ActionType.STOCK_ACTIVITY]
    assert vests
    assert all(vest.price is not None for vest in vests)


@pytest.mark.parametrize(
    "content",
    ["", "Symbol,Quantity,Price\nAAPL,10,$100.00\n", "not a Schwab export at all\n"],
)
def test_content_of_no_known_layout_names_the_layouts(
    tmp_path: Path, content: str
) -> None:
    """An unrecognised award file says what each supported export looks like."""
    award_file = tmp_path / "positions.csv"
    award_file.write_text(content, encoding="utf-8")

    with pytest.raises(ParsingError, match="not a Schwab Equity Awards export"):
        load_via_cli(schwab_award_file=str(award_file))


def test_a_header_only_price_csv_is_read_as_a_price_csv(tmp_path: Path) -> None:
    """The header decides, not whether the file went on to price anything.

    Inferring the layout from an empty result would report this as an
    unrecognised file instead of the empty price list it is.
    """
    award_file = tmp_path / "awards.csv"
    award_file.write_text(PRICE_HEADER, encoding="utf-8")

    with pytest.raises(ParsingError, match="Cannot price a vest"):
        load_via_cli(schwab_file=str(MAIN_HISTORY), schwab_award_file=str(award_file))


def test_a_header_only_complete_export_is_read_as_a_complete_export(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Same for the complete layout: it reports no transactions, not a bad file."""
    award_file = tmp_path / "awards.csv"
    header = COMPLETE_CSV.read_text(encoding="utf-8").splitlines()[0]
    award_file.write_text(f"{header}\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert load_via_cli(schwab_award_file=str(award_file)) == []

    assert "No transactions detected in file" in caplog.text


@pytest.mark.parametrize("lead", ["\ufeff", "\n", " \t\r\n"])
def test_a_price_csv_behind_leading_noise_is_read(tmp_path: Path, lead: str) -> None:
    """The complete export already tolerates this, and now both layouts do."""
    award_file = tmp_path / "awards.csv"
    award_file.write_text(
        lead + PRICE_CSV.read_text(encoding="utf-8"), encoding="utf-8"
    )

    assert load_via_cli(schwab_award_file=str(award_file)) == []
    assert SchwabParser.awards_prices


@pytest.mark.parametrize("fixture", [COMPLETE_JSON, PRICE_CSV])
def test_the_award_file_may_be_piped(
    fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A canonical option that cannot be piped to would be a downgrade."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(fixture.read_text(encoding="utf-8")))

    load_via_cli(schwab_award_file="-")

    assert bool(SchwabParser.awards_prices) == (fixture is PRICE_CSV)


@pytest.mark.parametrize("fixture", [COMPLETE_JSON, COMPLETE_CSV, PRICE_CSV])
def test_the_award_file_is_announced_once(
    fixture: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The file is read once, so it is announced once, as it was before."""
    with caplog.at_level(logging.INFO):
        load_via_cli(schwab_award_file=str(fixture))

    assert caplog.text.count("Parsing ") == 1
    # parsing_msg prints forward slashes on every platform, so compare against
    # that form rather than str(), which is backslashed on Windows.
    assert fixture.as_posix() in caplog.text


def test_a_complete_export_clears_the_prices_of_the_run_before() -> None:
    """awards_prices is class-level, so every load has to assign it.

    Left alone, this run's vests would be priced from the last run's file.
    """
    load_via_cli(schwab_award_file=str(PRICE_CSV))
    assert SchwabParser.awards_prices

    load_via_cli(schwab_award_file=str(COMPLETE_JSON))

    assert not SchwabParser.awards_prices


def test_no_award_file_clears_the_prices_of_the_run_before() -> None:
    """The same for a run passing no award file at all.

    Separate from the test above on purpose: chaining the two would leave this
    assertion trivially true, because the complete export already emptied the
    prices.
    """
    load_via_cli(schwab_award_file=str(PRICE_CSV))
    assert SchwabParser.awards_prices

    load_via_cli()

    assert not SchwabParser.awards_prices
