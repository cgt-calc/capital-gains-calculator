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
from typing import TYPE_CHECKING

import pytest

from cgt_calc.args_parser import create_parser
from cgt_calc.exceptions import CgtError, ParsingError
from cgt_calc.parsers.schwab import AwardPrices, SchwabParser
from cgt_calc.parsers.schwab_equity_award_json import SchwabEquityAwardsParser

if TYPE_CHECKING:
    from cgt_calc.model import BrokerTransaction

EQUITY_AWARD = Path("tests") / "schwab" / "data" / "equity_award"
RSU = Path("tests") / "schwab" / "data" / "rsu_settlement"
COMPLETE_JSON = EQUITY_AWARD / "schwab_equity_award_v2.json"
COMPLETE_CSV = EQUITY_AWARD / "schwab_equity_award_v2.csv"
# A complete CSV that also carries the award-price CSV's own columns.
AMBIGUOUS_COMPLETE_CSV = EQUITY_AWARD / "nvda_synthetic.csv"
PRICE_CSV = RSU / "awards.csv"
MAIN_HISTORY = RSU / "transactions.csv"

PRICE_HEADER = (
    '"Date","Action","Symbol","Description","Quantity","FeesAndCommissions",'
    '"DisbursementElection","Amount","AwardDate","AwardId",'
    '"FairMarketValuePrice","SalePrice","SharesSoldWithheldForTaxes",'
    '"NetSharesDeposited","Taxes"\n'
)


@pytest.fixture(autouse=True)
def _reset_awards_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep changes to the class-level award prices out of other test modules."""
    monkeypatch.setattr(SchwabParser, "awards_prices", AwardPrices(award_prices={}))


def _load(**flags: str) -> list[BrokerTransaction]:
    """Load through the CLI wiring rather than calling the parser directly.

    load_from_args is the layer that classifies the award file and fills in
    awards_prices, so bypassing it would test nothing this PR changed.
    """
    argv = ["--year", "2023"]
    for flag, value in flags.items():
        argv += [f"--{flag.replace('_', '-')}", value]
    args = create_parser().parse_args(argv)
    return SchwabParser.load_from_args(args)


def _renamed(tmp_path: Path, source: Path, name: str) -> str:
    """Copy a fixture under a name whose extension contradicts its contents."""
    target = tmp_path / name
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return str(target)


def test_a_complete_json_export_is_imported(tmp_path: Path) -> None:
    """The complete JSON history imports its own transactions."""
    transactions = _load(schwab_award_file=_renamed(tmp_path, COMPLETE_JSON, "a.csv"))

    assert transactions
    assert not SchwabParser.awards_prices


def test_a_complete_csv_export_is_imported(tmp_path: Path) -> None:
    """The complete CSV history does too, under a JSON name."""
    transactions = _load(schwab_award_file=_renamed(tmp_path, COMPLETE_CSV, "a.json"))

    assert transactions
    assert not SchwabParser.awards_prices


def test_a_complete_export_carrying_the_price_column_is_still_complete() -> None:
    """The complete layout has to be tested before the price layout.

    A real complete CSV also states FairMarketValuePrice, so checking the
    price columns first would route this file to the price reader: it would
    import none of its transactions and offer prices no vest asked for.
    """
    transactions = _load(schwab_award_file=str(AMBIGUOUS_COMPLETE_CSV))

    assert transactions
    assert not SchwabParser.awards_prices


def test_a_price_csv_supplies_prices_and_imports_nothing(tmp_path: Path) -> None:
    """The award-price CSV is still a price list, whatever it is called."""
    transactions = _load(schwab_award_file=_renamed(tmp_path, PRICE_CSV, "a.json"))

    assert transactions == []
    assert SchwabParser.awards_prices


@pytest.mark.parametrize("fixture", [COMPLETE_JSON, COMPLETE_CSV])
def test_the_canonical_option_matches_the_old_one(fixture: Path) -> None:
    """A complete export reaches the same parser through either option."""
    canonical = _load(schwab_award_file=str(fixture))
    args = create_parser().parse_args(
        ["--year", "2023", "--schwab-equity-award-json", str(fixture)]
    )

    assert canonical == SchwabEquityAwardsParser.load_from_args(args)
    assert canonical


def test_a_price_csv_may_still_accompany_the_old_option() -> None:
    """Pricing a vest and importing an award history is not a conflict."""
    transactions = _load(
        schwab_file=str(MAIN_HISTORY),
        schwab_award_file=str(PRICE_CSV),
        schwab_equity_award_json=str(COMPLETE_JSON),
    )

    assert transactions


def test_a_complete_export_through_both_award_options_is_refused() -> None:
    """Two complete exports cannot be told apart from one passed twice."""
    with pytest.raises(CgtError, match="could duplicate transactions") as exc_info:
        _load(
            schwab_award_file=str(COMPLETE_JSON),
            schwab_equity_award_json=str(COMPLETE_JSON),
        )

    assert "Pass the history once" in str(exc_info.value)


@pytest.mark.parametrize("main", ["schwab_file", "schwab_dir"])
def test_a_complete_export_with_a_main_history_is_refused(
    main: str, tmp_path: Path
) -> None:
    """Reconciling the two histories is not supported yet, so it is refused."""
    directory = tmp_path / "schwab"
    directory.mkdir()
    (directory / "transactions.csv").write_text(
        MAIN_HISTORY.read_text(encoding="utf-8"), encoding="utf-8"
    )
    history = str(MAIN_HISTORY) if main == "schwab_file" else str(directory)

    with pytest.raises(CgtError, match="not supported yet") as exc_info:
        _load(**{main: history}, schwab_award_file=str(COMPLETE_JSON))

    message = str(exc_info.value)
    # The way out, and what that option cannot do for the main history.
    assert "--schwab-equity-award-json" in message
    assert "it does not price vests" in message


def test_the_duplicate_input_is_reported_before_the_main_history() -> None:
    """Both conditions at once: the unconditional problem is named first.

    The other message offers --schwab-equity-award-json as the way to combine
    a history, which is no help to someone who has already passed it.
    """
    with pytest.raises(CgtError) as exc_info:
        _load(
            schwab_file=str(MAIN_HISTORY),
            schwab_award_file=str(COMPLETE_JSON),
            schwab_equity_award_json=str(COMPLETE_JSON),
        )

    assert "could duplicate transactions" in str(exc_info.value)


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
        _load(schwab_award_file=str(award_file))


def test_a_header_only_price_csv_is_read_as_a_price_csv(tmp_path: Path) -> None:
    """The header decides, not whether the file went on to price anything.

    Inferring the layout from an empty result would report this as an
    unrecognised file instead of the empty price list it is.
    """
    award_file = tmp_path / "awards.csv"
    award_file.write_text(PRICE_HEADER, encoding="utf-8")

    with pytest.raises(ParsingError, match="Cannot price a vest"):
        _load(schwab_file=str(MAIN_HISTORY), schwab_award_file=str(award_file))


def test_a_header_only_complete_export_is_read_as_a_complete_export(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Same for the complete layout: it reports no transactions, not a bad file."""
    award_file = tmp_path / "awards.csv"
    header = COMPLETE_CSV.read_text(encoding="utf-8").splitlines()[0]
    award_file.write_text(f"{header}\n", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert _load(schwab_award_file=str(award_file)) == []

    assert "No transactions detected in file" in caplog.text


@pytest.mark.parametrize("lead", ["\ufeff", "\n", " \t\r\n"])
def test_a_price_csv_behind_leading_noise_is_read(tmp_path: Path, lead: str) -> None:
    """The complete export already tolerates this, and now both layouts do."""
    award_file = tmp_path / "awards.csv"
    award_file.write_text(
        lead + PRICE_CSV.read_text(encoding="utf-8"), encoding="utf-8"
    )

    assert _load(schwab_award_file=str(award_file)) == []
    assert SchwabParser.awards_prices


@pytest.mark.parametrize("fixture", [COMPLETE_JSON, PRICE_CSV])
def test_the_award_file_may_be_piped(
    fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A canonical option that cannot be piped to would be a downgrade."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(fixture.read_text(encoding="utf-8")))

    _load(schwab_award_file="-")

    assert bool(SchwabParser.awards_prices) == (fixture is PRICE_CSV)


@pytest.mark.parametrize("fixture", [COMPLETE_JSON, COMPLETE_CSV, PRICE_CSV])
def test_the_award_file_is_announced_once(
    fixture: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The file is read once, so it is announced once, as it was before."""
    with caplog.at_level(logging.INFO):
        _load(schwab_award_file=str(fixture))

    assert caplog.text.count("Parsing ") == 1
    # parsing_msg prints forward slashes on every platform, so compare against
    # that form rather than str(), which is backslashed on Windows.
    assert fixture.as_posix() in caplog.text


def test_a_complete_export_clears_the_prices_of_the_run_before() -> None:
    """awards_prices is class-level, so every load has to assign it.

    Left alone, this run's vests would be priced from the last run's file.
    """
    _load(schwab_award_file=str(PRICE_CSV))
    assert SchwabParser.awards_prices

    _load(schwab_award_file=str(COMPLETE_JSON))

    assert not SchwabParser.awards_prices


def test_no_award_file_clears_the_prices_of_the_run_before() -> None:
    """The same for a run passing no award file at all.

    Separate from the test above on purpose: chaining the two would leave this
    assertion trivially true, because the complete export already emptied the
    prices.
    """
    _load(schwab_award_file=str(PRICE_CSV))
    assert SchwabParser.awards_prices

    _load()

    assert not SchwabParser.awards_prices
