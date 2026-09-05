"""Tests for the parsed-transaction CSV export."""

from __future__ import annotations

import argparse
import csv
import dataclasses
from dataclasses import fields
import datetime
from decimal import Decimal
import io
import json
import logging
from pathlib import Path
import subprocess
import sys
from typing import TYPE_CHECKING, Self

import pytest

from cgt_calc.cli import _save_transaction_dump
from cgt_calc.exceptions import TransactionDumpError
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CapitalAdjustment,
    CurrencyCode,
    DatedAmount,
    Isin,
    OptionContract,
    OptionType,
    TransactionSource,
    WrittenOptionTaxData,
)
from cgt_calc.transaction_dumper import DUMP_FIELDS, dump_transactions
from tests.utils import build_cmd, report_path

if TYPE_CHECKING:
    from collections.abc import Sequence

RAW_HEADER = "date,action,symbol,quantity,price,fees,currency\n"

DATE = datetime.date(2023, 4, 25)
ZERO = Decimal(0)
ONE = Decimal(1)
USD = CurrencyCode("USD")


def _minimal(
    *,
    action: ActionType = ActionType.BUY,
    symbol: str | None = "GOOG",
    description: str = "",
    quantity: Decimal | None = ONE,
    price: Decimal | None = ONE,
    fees: Decimal = ZERO,
    amount: Decimal | None = ONE,
    broker: str = "Test Broker",
) -> BrokerTransaction:
    """Build a transaction with only the required fields set."""
    return BrokerTransaction(
        date=DATE,
        action=action,
        symbol=symbol,
        description=description,
        quantity=quantity,
        price=price,
        fees=fees,
        amount=amount,
        currency=USD,
        broker=broker,
    )


def _rich() -> BrokerTransaction:
    """Build a transaction that populates every exported field."""
    return BrokerTransaction(
        date=datetime.date(2023, 4, 25),
        action=ActionType.OPTION_GRANT,
        symbol="GOOG",
        description="Vest, tranche 2",
        quantity=Decimal("10.5"),
        price=Decimal("100.123456"),
        fees=Decimal("1.50"),
        amount=Decimal("1054.125"),
        currency=CurrencyCode("USD"),
        broker="Test Broker",
        isin=Isin("US02079K3059"),
        # Two currencies, deliberately not in sorted order.
        foreign_fees={
            CurrencyCode("PLN"): Decimal("0.30"),
            CurrencyCode("EUR"): Decimal(2),
        },
        ambiguous_quantity=Decimal(21),
        option_contract=OptionContract(
            underlying="GOOG",
            expiry=datetime.date(2024, 5, 17),
            strike=Decimal(500),
            option_type=OptionType.CALL,
        ),
        written_option_tax=WrittenOptionTaxData(
            taxable_quantity=Decimal(2),
            close_costs=[
                DatedAmount(
                    date=datetime.date(2023, 6, 1),
                    amount=Decimal("12.34"),
                    currency=CurrencyCode("USD"),
                )
            ],
        ),
        capital_adjustments=[
            CapitalAdjustment(
                date=datetime.date(2023, 5, 2),
                net_amount=Decimal("-3.5"),
                fees=Decimal("0.25"),
                currency=CurrencyCode("USD"),
            )
        ],
        affects_cash_balance=False,
        source=TransactionSource(
            parser="RAW format",
            account="RAW format #1",
            file=Path("history/raw.csv"),
            row=7,
            index=3,
            timestamp=datetime.datetime(2023, 4, 25, 14, 30, 5),
            rows_in_time_order=True,
        ),
    )


def _dump_to_string(transactions: Sequence[BrokerTransaction]) -> str:
    """Export to an in-memory stream opened the way the CLI opens the file."""
    stream = io.StringIO(newline="")
    dump_transactions(transactions, stream)
    return stream.getvalue()


def _read_rows(text: str) -> list[dict[str, str]]:
    """Parse exported CSV text back into rows."""
    return list(csv.DictReader(io.StringIO(text, newline="")))


def test_header_is_every_parsed_field() -> None:
    """The export covers the model, excluding only calculation_quantity.

    A new BrokerTransaction field fails here until someone decides whether it
    belongs in the snapshot.
    """
    model_fields = {field.name for field in fields(BrokerTransaction)}

    assert set(DUMP_FIELDS) == model_fields - {"calculation_quantity"}
    assert DUMP_FIELDS[-1] == "source"
    assert len(set(DUMP_FIELDS)) == len(DUMP_FIELDS)


def test_empty_history_writes_the_header_alone(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No transactions still produces valid CSV, and nothing else anywhere."""
    text = _dump_to_string([])

    assert text == ",".join(DUMP_FIELDS) + "\r\n"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_every_field_is_exported() -> None:
    """Each header column carries the parsed value, exactly as parsed."""
    (row,) = _read_rows(_dump_to_string([_rich()]))

    assert row["date"] == "2023-04-25"
    assert row["action"] == "OPTION_GRANT"
    assert row["symbol"] == "GOOG"
    assert row["description"] == "Vest, tranche 2"
    assert row["quantity"] == "10.5"
    assert row["price"] == "100.123456"
    assert row["fees"] == "1.50"
    assert row["amount"] == "1054.125"
    assert row["currency"] == "USD"
    assert row["broker"] == "Test Broker"
    assert row["isin"] == "US02079K3059"
    assert row["ambiguous_quantity"] == "21"
    assert row["affects_cash_balance"] == "false"
    assert json.loads(row["foreign_fees"]) == {"EUR": "2", "PLN": "0.30"}
    assert json.loads(row["option_contract"]) == {
        "underlying": "GOOG",
        "expiry": "2024-05-17",
        "strike": "500",
        "option_type": "CALL",
        "multiplier": "100",
    }
    assert json.loads(row["written_option_tax"]) == {
        "taxable_quantity": "2",
        "close_costs": [{"date": "2023-06-01", "amount": "12.34", "currency": "USD"}],
    }
    assert json.loads(row["capital_adjustments"]) == [
        {
            "date": "2023-05-02",
            "net_amount": "-3.5",
            "fees": "0.25",
            "currency": "USD",
        }
    ]
    assert json.loads(row["source"]) == {
        "parser": "RAW format",
        "account": "RAW format #1",
        # The exporter writes a path as its own platform spells it.
        "file": str(Path("history/raw.csv")),
        "row": 7,
        "index": 3,
        "timestamp": "2023-04-25T14:30:05",
        "rows_in_time_order": True,
    }


def test_nested_integers_and_booleans_keep_their_json_types() -> None:
    """Source row and index stay JSON numbers, and the order flag a boolean."""
    (row,) = _read_rows(_dump_to_string([_rich()]))
    source = json.loads(row["source"])

    assert source["row"] == 7
    assert source["index"] == 3
    assert source["rows_in_time_order"] is True


def test_nested_json_sorts_keys_for_a_stable_diff() -> None:
    """Two exports of the same records compare byte for byte."""
    text = _dump_to_string([_rich()])
    (row,) = _read_rows(text)

    assert row["foreign_fees"] == '{"EUR":"2","PLN":"0.30"}'
    assert text == _dump_to_string([_rich()])


def test_none_zero_and_empty_collections_are_distinguishable() -> None:
    """A missing value empties the field; zero and empty collections do not."""
    (row,) = _read_rows(
        _dump_to_string(
            [
                _minimal(
                    symbol=None,
                    quantity=None,
                    price=None,
                    amount=None,
                    fees=ZERO,
                    description="",
                    broker="",
                )
            ]
        )
    )

    assert row["symbol"] == ""
    assert row["quantity"] == ""
    assert row["price"] == ""
    assert row["amount"] == ""
    assert row["isin"] == ""
    assert row["description"] == ""
    assert row["broker"] == ""
    assert row["fees"] == "0"
    assert row["foreign_fees"] == "{}"
    assert row["capital_adjustments"] == "[]"
    assert row["source"] == ""


def test_exact_amounts_are_not_rounded() -> None:
    """Values are exported at full precision, not display-rounded."""
    (row,) = _read_rows(
        _dump_to_string(
            [_minimal(amount=Decimal("1054.125"), price=Decimal("0.123456789"))]
        )
    )

    assert row["amount"] == "1054.125"
    assert row["price"] == "0.123456789"


def test_descriptions_separate_two_otherwise_identical_rows() -> None:
    """A rename's source ticker lives in the description, so it must survive."""
    first = _minimal(action=ActionType.RENAME, description="renamed from FB")
    second = _minimal(action=ActionType.RENAME, description="renamed from META")

    descriptions = [
        row["description"] for row in _read_rows(_dump_to_string([first, second]))
    ]

    assert descriptions == ["renamed from FB", "renamed from META"]


@pytest.mark.parametrize(
    "description",
    [
        "comma, inside",
        'quote " inside',
        "carriage\rreturn",
        "line\nfeed",
        "windows\r\nbreak",
        "café ☕ 日本",
    ],
)
def test_awkward_text_round_trips_through_the_file(
    tmp_path: Path, description: str
) -> None:
    """Separators and newlines inside a field survive as written bytes.

    Read back in binary and decoded without newline translation: universal
    newlines would hide a bare CR turned into a record break.
    """
    destination = tmp_path / "parsed.csv"
    with destination.open("x", encoding="utf-8", newline="") as stream:
        dump_transactions([_minimal(description=description)], stream)

    text = destination.read_bytes().decode("utf-8")
    (row,) = _read_rows(text)

    assert row["description"] == description


def test_export_neither_mutates_nor_reorders(tmp_path: Path) -> None:
    """The snapshot is the records as they were, in the order they arrived."""
    transactions = [
        _minimal(symbol="AAPL", description="first"),
        _minimal(symbol="GOOG", description="second"),
    ]
    before = [dataclasses.replace(transaction) for transaction in transactions]

    destination = tmp_path / "parsed.csv"
    with destination.open("x", encoding="utf-8", newline="") as stream:
        dump_transactions(transactions, stream)

    assert transactions == before
    assert [
        row["description"]
        for row in _read_rows(destination.read_text(encoding="utf-8"))
    ] == [
        "first",
        "second",
    ]

    # A later calculation stage changing the records cannot reach the file.
    transactions[0].symbol = "CHANGED"
    assert [
        row["symbol"] for row in _read_rows(destination.read_text(encoding="utf-8"))
    ] == [
        "AAPL",
        "GOOG",
    ]


def test_an_unexportable_value_is_refused() -> None:
    """An unknown type raises rather than being written through repr()."""
    transaction = _minimal()
    transaction.description = object()  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    with pytest.raises(TypeError, match="Cannot export"):
        _dump_to_string([transaction])


def test_the_stream_stays_open_for_the_caller() -> None:
    """The exporter never closes a stream it was handed."""
    stream = io.StringIO(newline="")

    dump_transactions([_minimal()], stream)

    assert not stream.closed


class _FailingStream:
    """A destination that fails when the test says it should.

    A buffered write only reaches the disk when the buffer fills or the file
    is closed, so a full disk can surface at either point.
    """

    def __init__(self, when: str) -> None:
        """Fail at `when`, which is "write" or "close"."""
        self.when = when
        self.closed = False

    def _boom(self) -> None:
        raise OSError(28, "No space left on device")

    def write(self, text: str) -> int:
        """Record the write, or fail it."""
        if self.when == "write":
            self._boom()
        return len(text)

    def close(self) -> None:
        """Flush and close, or fail doing so."""
        self.closed = True
        if self.when == "close":
            self._boom()

    def __enter__(self) -> Self:
        """Enter the context, as an open file does."""
        return self

    def __exit__(self, *_exc: object) -> None:
        """Close on the way out, as an open file does."""
        self.close()


@pytest.mark.parametrize("failing", ["create", "write", "close"])
def test_a_failed_export_names_the_path_and_confirms_nothing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    failing: str,
) -> None:
    """A disk that fills up at any point stops the run with an actionable error."""
    destination = tmp_path / "parsed.csv"

    def _open(*_args: object, **_kwargs: object) -> _FailingStream:
        if failing == "create":
            raise OSError(28, "No space left on device")
        return _FailingStream(failing)

    monkeypatch.setattr(Path, "open", _open)
    args = argparse.Namespace(
        dump_transactions=destination,
        no_report=True,
        exchange_rates_file=None,
        isin_translation_file=None,
        spin_offs_file=None,
    )

    with caplog.at_level(logging.INFO), pytest.raises(TransactionDumpError) as error:
        _save_transaction_dump(args, [_minimal()])

    message = str(error.value)
    assert str(destination) in message
    assert "No space left on device" in message
    assert "remove it or choose another filename" in message
    # The caller raises, so the run never reaches the calculation, and the
    # confirmation that the snapshot is available is never printed.
    assert "Saved" not in caplog.text


def _raw_file(tmp_path: Path, *rows: str) -> str:
    """Write a RAW-format history and return its path."""
    path = tmp_path / "history.csv"
    path.write_text(RAW_HEADER + "".join(f"{row}\n" for row in rows), encoding="utf-8")
    return str(path)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI and capture its output."""
    return subprocess.run(
        build_cmd(*args), capture_output=True, encoding="utf-8", check=False
    )


def _run_bare(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI with exactly these arguments and nothing added.

    build_cmd appends --no-pdflatex and its own exchange rate cache, which a
    test choosing those paths itself has to be able to override.
    """
    return subprocess.run(
        [sys.executable, "-m", "cgt_calc.main", *args],
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def test_cli_saves_the_csv_and_still_reports(
    request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    """The summary and the report are exactly what a run without the flag gives."""
    destination = tmp_path / "parsed.csv"
    source = (
        "--schwab-equity-award-json",
        "tests/schwab/data/equity_award/schwab_equity_award_v2.json",
    )
    output = report_path(request)

    plain = _run("--year", "2023", *source, "--output", output)
    dumped = _run(
        "--year",
        "2023",
        *source,
        "--dump-transactions",
        str(destination),
        "--output",
        output,
    )

    assert plain.returncode == 0
    assert dumped.returncode == 0
    assert dumped.stdout == plain.stdout
    assert "Tax summary for 2023/2024" in dumped.stdout
    # The report lands beside the --output path, named after its stem.
    report = Path(output)
    assert list(report.parent.glob(f"{report.stem}*")) != []

    rows = _read_rows(destination.read_text(encoding="utf-8"))
    assert len(rows) == 8
    assert rows[0]["action"] == "STOCK_ACTIVITY"


def test_cli_saves_the_csv_without_a_report(tmp_path: Path) -> None:
    """--no-report still calculates and still saves the snapshot."""
    destination = tmp_path / "parsed.csv"

    result = _run(
        "--year",
        "2023",
        "--schwab-equity-award-json",
        "tests/schwab/data/equity_award/schwab_equity_award_v2.json",
        "--dump-transactions",
        str(destination),
        "--no-report",
    )

    assert result.returncode == 0
    assert "Tax summary for 2023/2024" in result.stdout
    assert len(_read_rows(destination.read_text(encoding="utf-8"))) == 8


@pytest.mark.parametrize(
    ("rows", "confirmation"),
    [
        ((), "Saved 0 parsed transactions to"),
        (("2023-04-25,BUY,XYZ,10,5.00,0.00,USD",), "Saved 1 parsed transaction to"),
        (
            (
                "2023-04-25,BUY,XYZ,10,5.00,0.00,USD",
                "2023-06-15,BUY,XYZ,10,5.00,0.00,USD",
            ),
            "Saved 2 parsed transactions to",
        ),
    ],
)
def test_cli_confirms_the_saved_file_on_stderr(
    tmp_path: Path, rows: tuple[str, ...], confirmation: str
) -> None:
    """The confirmation names the count and path, and stays off stdout."""
    destination = tmp_path / "parsed.csv"

    result = _run(
        "--year",
        "2023",
        "--raw-file",
        _raw_file(tmp_path, *rows),
        "--dump-transactions",
        str(destination),
        "--no-report",
        "--no-balance-check",
    )

    assert result.returncode == 0
    assert f"{confirmation} {destination}" in result.stderr
    assert "Saved" not in result.stdout
    assert "Saved" not in destination.read_text(encoding="utf-8")
    # Saved before the calculation it precedes.
    assert result.stderr.index("Saved") < result.stderr.index("First pass complete")


def test_cli_keeps_the_csv_when_the_calculation_fails(tmp_path: Path) -> None:
    """A snapshot saved before calculation survives the failure it explains."""
    destination = tmp_path / "parsed.csv"

    result = _run(
        "--year",
        "2023",
        "--raw-file",
        _raw_file(tmp_path, "2023-08-31,SELL,XYZ,10,5.00,0.00,USD"),
        "--dump-transactions",
        str(destination),
        "--no-report",
        "--no-balance-check",
    )

    assert result.returncode != 0
    assert "Tried to sell not owned symbol" in result.stderr
    (row,) = _read_rows(destination.read_text(encoding="utf-8"))
    assert row["action"] == "SELL"
    assert row["symbol"] == "XYZ"


def test_cli_exports_transactions_outside_the_reported_year(tmp_path: Path) -> None:
    """The snapshot is every loaded row; --year only selects what is reported."""
    destination = tmp_path / "parsed.csv"

    result = _run(
        "--year",
        "2023",
        "--raw-file",
        _raw_file(
            tmp_path,
            "2022-05-03,BUY,XYZ,10,5.00,0.00,USD",
            "2023-06-15,BUY,XYZ,10,5.00,0.00,USD",
        ),
        "--dump-transactions",
        str(destination),
        "--no-report",
        "--no-balance-check",
    )

    assert result.returncode == 0
    assert [
        row["date"] for row in _read_rows(destination.read_text(encoding="utf-8"))
    ] == [
        "2022-05-03",
        "2023-06-15",
    ]


def test_cli_refuses_an_existing_destination(tmp_path: Path) -> None:
    """An existing file is never overwritten, and nothing is calculated."""
    destination = tmp_path / "parsed.csv"
    destination.write_bytes(b"keep me")

    result = _run(
        "--year",
        "2023",
        "--raw-file",
        _raw_file(tmp_path, "2023-04-25,BUY,XYZ,10,5.00,0.00,USD"),
        "--dump-transactions",
        str(destination),
        "--no-report",
    )

    assert result.returncode != 0
    assert "already exists" in result.stderr
    assert str(destination) in result.stderr
    assert destination.read_bytes() == b"keep me"
    assert "Saved" not in result.stderr
    assert "First pass complete" not in result.stderr


def test_cli_refuses_a_missing_parent_directory(tmp_path: Path) -> None:
    """A destination in a directory that does not exist is reported, not created."""
    destination = tmp_path / "missing" / "parsed.csv"

    result = _run(
        "--year",
        "2023",
        "--raw-file",
        _raw_file(tmp_path, "2023-04-25,BUY,XYZ,10,5.00,0.00,USD"),
        "--dump-transactions",
        str(destination),
        "--no-report",
    )

    assert result.returncode != 0
    assert str(destination.parent) in result.stderr
    assert not destination.parent.exists()
    assert "Saved" not in result.stderr
    assert "First pass complete" not in result.stderr


@pytest.mark.parametrize(
    ("name", "extra"),
    [
        ("calculations.pdf", ()),
        ("calculations.tex", ()),
        # Written by pdflatex next to the report and removed afterwards.
        ("calculations.latex.log", ()),
        ("calculations.log", ()),
        ("calculations.aux", ()),
        ("rates.csv", ("--exchange-rates-file", "{directory}/rates.csv")),
        ("isin.csv", ("--isin-translation-file", "{directory}/isin.csv")),
        ("spin_offs.csv", ("--spin-offs-file", "{directory}/spin_offs.csv")),
    ],
)
def test_cli_refuses_a_destination_it_would_overwrite(
    tmp_path: Path, name: str, extra: tuple[str, ...]
) -> None:
    """A path the run writes later cannot be the snapshot destination."""
    directory = tmp_path / "out"
    directory.mkdir()

    result = _run_bare(
        "--year",
        "2023",
        "--raw-file",
        _raw_file(tmp_path, "2023-04-25,BUY,XYZ,10,5.00,0.00,USD"),
        "--exchange-rates-file",
        "tests/exchange_rates_data.csv",
        "--dump-transactions",
        str(directory / name),
        "--output",
        str(directory / "calculations.pdf"),
        # Last wins, so a case naming its own cache overrides the default above.
        *(argument.format(directory=directory) for argument in extra),
    )

    assert result.returncode != 0
    assert "Choose a different filename" in result.stderr
    assert not (directory / name).exists()


def test_cli_refuses_the_pdf_an_output_without_a_suffix_produces(
    tmp_path: Path,
) -> None:
    """Pdflatex names the PDF after the report's stem, whatever --output said."""
    directory = tmp_path / "out"
    directory.mkdir()

    result = _run_bare(
        "--year",
        "2023",
        "--raw-file",
        _raw_file(tmp_path, "2023-04-25,BUY,XYZ,10,5.00,0.00,USD"),
        "--exchange-rates-file",
        "tests/exchange_rates_data.csv",
        "--dump-transactions",
        str(directory / "report.pdf"),
        "--output",
        str(directory / "report"),
    )

    assert result.returncode != 0
    assert "the PDF report from --output" in result.stderr
    assert not (directory / "report.pdf").exists()


def test_cli_creates_no_file_when_parsing_fails(tmp_path: Path) -> None:
    """Parsing runs first, so a broken export leaves no half-made snapshot."""
    destination = tmp_path / "parsed.csv"
    history = tmp_path / "history.csv"
    history.write_text(
        RAW_HEADER + "2023-04-25,NONSENSE,XYZ,10,5,0,USD\n", encoding="utf-8"
    )

    result = _run(
        "--year",
        "2023",
        "--raw-file",
        str(history),
        "--dump-transactions",
        str(destination),
        "--no-report",
    )

    assert result.returncode != 0
    assert result.stderr.count("ERROR") >= 1
    assert not destination.exists()
