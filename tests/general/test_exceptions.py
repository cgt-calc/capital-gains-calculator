"""Unit tests for custom exception helpers."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

import pytest

import cgt_calc.exceptions
from cgt_calc.exceptions import (
    AmountMissingError,
    CalculatedAmountDiscrepancyError,
    CalculationError,
    CgtError,
    ExchangeRateMissingError,
    ExternalApiError,
    InitialPriceMissingError,
    InteractiveInputRequiredError,
    InvalidTransactionError,
    IsinMissingError,
    IsinTranslationError,
    LatexRenderError,
    MarketDataMissingError,
    MissingExternalToolError,
    ParsingError,
    PriceMissingError,
    QuantityMissingError,
    QuantityNotPositiveError,
    SymbolMissingError,
    TransactionDumpError,
    UnclassifiedGiftError,
    UnexpectedColumnCountError,
    UnexpectedRowCountError,
    UnsupportedBrokerActionError,
    UnsupportedBrokerCurrencyError,
)
from cgt_calc.model import ActionType, BrokerTransaction, CurrencyCode

DATE = datetime.date(2023, 1, 1)

TRANSACTION = BrokerTransaction(
    date=DATE,
    action=ActionType.BUY,
    symbol="FOO",
    description="test",
    quantity=Decimal(1),
    price=Decimal(1),
    fees=Decimal(0),
    amount=Decimal(-1),
    currency=CurrencyCode("USD"),
    broker="Test",
)


def test_parsing_error_message_without_row() -> None:
    """Base message includes file and original detail."""

    err = ParsingError(Path("file.csv"), "broken header")

    assert str(err) == "While parsing file.csv: broken header"


def test_parsing_error_message_with_row() -> None:
    """Row context is included when supplied at construction."""

    err = ParsingError(Path("file.csv"), "bad value", row_index=7)

    assert str(err) == "While parsing file.csv, row 7: bad value"


def test_parsing_error_add_row_context_updates_message() -> None:
    """Adding row context after creation updates the message."""

    err = ParsingError(Path("file.csv"), "bad value")

    err.add_row_context(9)
    assert str(err) == "While parsing file.csv, row 9: bad value"

    err.add_row_context(11)
    assert str(err) == "While parsing file.csv, row 11: bad value"


def test_add_row_context_on_parsing_error_subclass() -> None:
    """Row context can be attached through ParsingError subclasses.

    File readers catch ParsingError and call add_row_context, so the
    machinery has to work on every subclass, not just the base class.
    """
    err = UnsupportedBrokerActionError(Path("f.csv"), "TestBroker", "Dance")

    err.add_row_context(5)

    assert err.row_index == 5
    assert "row 5" in str(err)


# Each error paired with the inputs a user needs to locate the problem.
# The exact wording is free to change; losing the context is a regression.
CONTEXT_CASES: list[tuple[CgtError, list[str]]] = [
    (
        InvalidTransactionError(TRANSACTION, "unexpected fees"),
        ["unexpected fees", "FOO", "2023"],
    ),
    (AmountMissingError(TRANSACTION), ["FOO", "2023"]),
    (SymbolMissingError(TRANSACTION), ["FOO", "2023"]),
    (PriceMissingError(TRANSACTION), ["FOO", "2023"]),
    (IsinMissingError(TRANSACTION), ["FOO", "2023"]),
    (QuantityMissingError(TRANSACTION), ["FOO", "2023"]),
    (QuantityNotPositiveError(TRANSACTION), ["FOO", "2023"]),
    # Both the calculated and the supplied amount are needed to see the gap.
    (CalculatedAmountDiscrepancyError(TRANSACTION, Decimal(-42)), ["-42", "-1", "FOO"]),
    (
        UnsupportedBrokerActionError(Path("f.csv"), "TestBroker", "Dance"),
        ["f.csv", "TestBroker", "Dance"],
    ),
    (
        UnsupportedBrokerCurrencyError(Path("f.csv"), "TestBroker", "XXX"),
        ["f.csv", "TestBroker", "XXX"],
    ),
    (
        UnexpectedColumnCountError(["only", "two"], 3, Path("f.csv")),
        ["f.csv", "3", "only", "two"],
    ),
    (UnexpectedRowCountError(6, Path("f.csv")), ["f.csv", "6"]),
    (ExchangeRateMissingError("USD", DATE), ["USD", "2023-01-01"]),
    (InitialPriceMissingError("FOO", DATE), ["FOO", "2023-01-01"]),
    (LatexRenderError(Path("render.log")), ["render.log"]),
    (MissingExternalToolError("pdflatex"), ["pdflatex"]),
    (IsinTranslationError("ISIN XS123 is broken"), ["ISIN XS123 is broken"]),
    (
        TransactionDumpError("parsed.csv already exists"),
        ["parsed.csv already exists"],
    ),
    (
        ExternalApiError("https://api.example.com/rates", "boom"),
        ["https://api.example.com/rates", "boom"],
    ),
    (
        InteractiveInputRequiredError("FOO", DATE, Path("spin_offs.csv")),
        ["FOO", "2023-01-01", "spin_offs.csv"],
    ),
    (MarketDataMissingError("FOO", DATE), ["FOO", "2023-01-01"]),
    (
        UnclassifiedGiftError(TRANSACTION, [Decimal(1)]),
        # The row to paste, the shares it covers, and the other outcome.
        [
            "2023-01-01,TRANSFER_TO_SPOUSE,FOO,1,0.00,0.00,USD",
            "2023-01-01,GIFT,FOO,1,<value per unit>,0.00,USD",
            "1 units of FOO",
            "Test",
            "disposal at market value",
        ],
    ),
    (
        # Both readings of a count printed before a split, and why.
        UnclassifiedGiftError(TRANSACTION, [Decimal(1), Decimal(20)]),
        [
            "2023-01-01,TRANSFER_TO_SPOUSE,FOO,1,0.00,0.00,USD",
            "2023-01-01,TRANSFER_TO_SPOUSE,FOO,20,0.00,0.00,USD",
            "either 1 or 20 shares",
        ],
    ),
]


@pytest.mark.parametrize(
    ("error", "fragments"),
    CONTEXT_CASES,
    ids=[type(error).__name__ for error, _ in CONTEXT_CASES],
)
def test_error_message_contains_context(error: CgtError, fragments: list[str]) -> None:
    """Error messages name the inputs needed to locate the problem."""
    message = str(error)

    for fragment in fragments:
        assert fragment in message


def test_every_exception_has_a_context_case() -> None:
    """Every exception in the module is exercised by the context sweep.

    A new exception with a broken message would otherwise only surface
    in the failing user run that raises it.
    """
    bare_base_classes = {CgtError, CalculationError}
    tested_directly = {ParsingError}
    covered = (
        {type(error) for error, _ in CONTEXT_CASES}
        | bare_base_classes
        | tested_directly
    )

    module_classes = {
        obj
        for obj in vars(cgt_calc.exceptions).values()
        if isinstance(obj, type) and issubclass(obj, CgtError)
    }

    assert module_classes <= covered
