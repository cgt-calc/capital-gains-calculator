"""Tests for the value types in cgt_calc.model."""

from __future__ import annotations

from dataclasses import replace
import datetime
from decimal import Decimal
from pathlib import Path
from typing import override

import pytest

from cgt_calc.exceptions import CalculationError, InvalidTransactionError
from cgt_calc.model import (
    ActionType,
    BrokerTransaction,
    CurrencyCode,
    ForeignCurrencyAmount,
    Isin,
    TransactionSource,
)

# Apple, a real ISIN with a valid check digit.
VALID_ISIN = "US0378331005"
USD = CurrencyCode("USD")


def test_isin_accepts_a_valid_identifier() -> None:
    """A well-formed ISIN is preserved verbatim."""
    assert Isin(VALID_ISIN) == VALID_ISIN


def test_isin_normalises_case_and_whitespace() -> None:
    """Lowercase input and stray whitespace are normalised away."""
    assert Isin("  us0378331005 ") == VALID_ISIN


@pytest.mark.parametrize(
    "value",
    [
        "",
        "US037833100",  # too short
        "US03783310055",  # too long
        "US037833100X",  # check digit is not a digit
        "0S0378331005",  # country code is not alphabetic
    ],
)
def test_isin_rejects_malformed_identifiers(value: str) -> None:
    """Anything that is not ISIN-shaped is refused."""
    with pytest.raises(ValueError, match="Invalid ISIN"):
        Isin(value)


def test_isin_rejects_a_bad_check_digit() -> None:
    """A correctly shaped identifier with a wrong checksum is refused."""
    with pytest.raises(ValueError, match="Invalid ISIN checksum"):
        Isin("US0378331006")


def test_isin_is_usable_as_a_string() -> None:
    """The type stays interchangeable with str for lookups and formatting."""
    isin = Isin(VALID_ISIN)
    assert isinstance(isin, str)
    assert {VALID_ISIN: "AAPL"}[isin] == "AAPL"
    assert f"{isin}" == VALID_ISIN


def test_isin_rejects_attribute_assignment() -> None:
    """The value type carries no instance dict."""
    isin = Isin(VALID_ISIN)
    with pytest.raises(AttributeError):
        isin.symbol = "AAPL"  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]


def test_isin_parse_returns_none_for_invalid_input() -> None:
    """The lenient constructor reports failure instead of raising."""
    assert Isin.parse("not-an-isin") is None


def test_isin_parse_normalises_valid_input() -> None:
    """The lenient constructor normalises exactly like the strict one."""
    assert Isin.parse("us0378331005") == Isin(VALID_ISIN)


def _transaction(isin: Isin | None, currency: CurrencyCode = USD) -> BrokerTransaction:
    return BrokerTransaction(
        date=datetime.date(2023, 1, 1),
        action=ActionType.BUY,
        symbol="FOO",
        description="test",
        quantity=Decimal(1),
        price=Decimal(1),
        fees=Decimal(0),
        amount=Decimal(-1),
        currency=currency,
        broker="Test",
        isin=isin,
    )


def test_broker_transaction_rejects_a_bare_invalid_isin() -> None:
    """A caller that skips the type still cannot smuggle in a bad ISIN."""
    with pytest.raises(ValueError, match="Invalid ISIN"):
        _transaction("NOTANISIN")  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def test_broker_transaction_coerces_a_bare_valid_isin() -> None:
    """A valid bare string is normalised into the value type."""
    transaction = _transaction("us0378331005")  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    assert isinstance(transaction.isin, Isin)
    assert transaction.isin == VALID_ISIN


def test_broker_transaction_keeps_an_isin_instance_as_is() -> None:
    """An already-validated identifier passes through untouched."""
    isin = Isin(VALID_ISIN)
    assert _transaction(isin).isin is isin


def test_currency_code_accepts_a_valid_code() -> None:
    """A well-formed ISO 4217 code is preserved verbatim."""
    assert CurrencyCode("USD") == "USD"


def test_currency_code_strips_whitespace() -> None:
    """Stray whitespace around the code is tolerated."""
    assert CurrencyCode("  USD ") == "USD"


@pytest.mark.parametrize("value", ["usd", "Usd", "GBp"])
def test_currency_code_does_not_fold_case(value: str) -> None:
    """Case is meaningful ("GBp" is pence), so it is never guessed at."""
    with pytest.raises(ValueError, match="Invalid currency code"):
        CurrencyCode(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "US",  # too short
        "USDD",  # too long
        "US1",  # not alphabetic
        "£",  # a symbol, not a code
        "US Dollar",  # a name, not a code
        "GBp",  # pence, not a currency code
    ],
)
def test_currency_code_rejects_malformed_codes(value: str) -> None:
    """Anything that is not three letters is refused."""
    with pytest.raises(ValueError, match="Invalid currency code"):
        CurrencyCode(value)


def test_currency_code_accepts_a_code_unknown_to_stale_tables() -> None:
    """Membership is HMRC's call via its rate table, not a bundled ISO list."""
    assert CurrencyCode("VES") == "VES"


def test_currency_code_is_usable_as_a_string() -> None:
    """The type stays interchangeable with str for lookups and formatting."""
    code = CurrencyCode("GBP")
    assert isinstance(code, str)
    assert {"GBP": 1}[code] == 1
    assert f"{code}" == "GBP"


def test_currency_code_rejects_attribute_assignment() -> None:
    """The value type carries no instance dict."""
    code = CurrencyCode("GBP")
    with pytest.raises(AttributeError):
        code.symbol = "£"  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]


def test_currency_code_parse_returns_none_for_invalid_input() -> None:
    """The lenient constructor reports failure instead of raising."""
    assert CurrencyCode.parse("GBp") is None


def test_currency_code_parse_normalises_valid_input() -> None:
    """The lenient constructor normalises exactly like the strict one."""
    assert CurrencyCode.parse(" GBP ") == CurrencyCode("GBP")


def test_foreign_currency_amount_rejects_mixed_currencies() -> None:
    """Amounts sourced from incompatible input rows produce a domain error."""
    usd = ForeignCurrencyAmount(Decimal(1), CurrencyCode("USD"))
    gbp = ForeignCurrencyAmount(Decimal(1), CurrencyCode("GBP"))

    with pytest.raises(CalculationError, match="different currencies"):
        usd + gbp


def test_nonzero_foreign_amount_requires_a_currency() -> None:
    """A missing input currency produces a domain error rather than an assertion."""
    invalid = ForeignCurrencyAmount(Decimal(1))

    with pytest.raises(CalculationError, match="Currency missing"):
        ForeignCurrencyAmount() + invalid

    # The left operand is validated by the same rule as the right one.
    with pytest.raises(CalculationError, match="Currency missing"):
        invalid + ForeignCurrencyAmount()


def test_broker_transaction_rejects_a_bare_invalid_currency() -> None:
    """A caller that skips the type still cannot smuggle in a bad currency."""
    with pytest.raises(ValueError, match="Invalid currency code"):
        _transaction(None, currency="GBp")  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def test_broker_transaction_coerces_a_bare_valid_currency() -> None:
    """A valid bare string is normalised into the value type."""
    transaction = _transaction(None, currency=" USD ")  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    assert isinstance(transaction.currency, CurrencyCode)
    assert transaction.currency == "USD"


def test_broker_transaction_keeps_a_currency_code_instance_as_is() -> None:
    """An already-validated code passes through untouched."""
    currency = CurrencyCode("USD")
    assert _transaction(None, currency=currency).currency is currency


def _transaction_with_fees(
    foreign_fees: dict[CurrencyCode, Decimal],
) -> BrokerTransaction:
    transaction = _transaction(None)
    return replace(transaction, foreign_fees=foreign_fees)


def test_broker_transaction_rejects_a_bare_invalid_foreign_fee_currency() -> None:
    """Foreign fee keys are held to the same rule as the transaction currency."""
    with pytest.raises(ValueError, match="Invalid currency code"):
        _transaction_with_fees({"GBp": Decimal(1)})  # type: ignore[dict-item]  # ty: ignore[invalid-argument-type]


def test_broker_transaction_coerces_bare_valid_foreign_fee_currencies() -> None:
    """Valid bare keys are normalised into the value type."""
    transaction = _transaction_with_fees({" USD ": Decimal(1)})  # type: ignore[dict-item]  # ty: ignore[invalid-argument-type]
    assert list(transaction.foreign_fees) == [CurrencyCode("USD")]
    assert all(isinstance(key, CurrencyCode) for key in transaction.foreign_fees)


def test_broker_transaction_sums_foreign_fees_that_coerce_to_one_currency() -> None:
    """Keys that normalise to the same code add up rather than overwrite."""
    transaction = _transaction_with_fees(
        {" USD ": Decimal(1), "USD": Decimal(2)}  # type: ignore[dict-item]  # ty: ignore[invalid-argument-type]
    )
    assert transaction.foreign_fees == {CurrencyCode("USD"): Decimal(3)}


def test_broker_transaction_keeps_typed_foreign_fees_as_is() -> None:
    """An already-typed fee table passes through untouched."""
    fees = {CurrencyCode("USD"): Decimal(1)}
    assert _transaction_with_fees(fees).foreign_fees is fees


class _HashedTransaction(BrokerTransaction):
    """A transaction hashed by identity fields, as broker parsers hash theirs."""

    @override
    def __hash__(self) -> int:
        """Hash on what identifies the row, never on where it was read from."""
        return hash((self.date, self.symbol, self.quantity))


def _hashed(source: TransactionSource) -> _HashedTransaction:
    return _HashedTransaction(
        date=datetime.date(2023, 1, 1),
        action=ActionType.BUY,
        symbol="FOO",
        description="test",
        quantity=Decimal(1),
        price=Decimal(1),
        fees=Decimal(0),
        amount=Decimal(-1),
        currency=USD,
        broker="Test",
        source=source,
    )


def test_source_is_not_part_of_transaction_equality() -> None:
    """One row two overlapping exports both state stays one row.

    Parsers deduplicate by comparing transactions, so the file a row came
    from, the line it sat on and the boundary it was loaded under have to
    stay out of the comparison and out of the hash. Left in, every row two
    exports share would survive twice and double the holding.
    """
    first = _hashed(
        TransactionSource(
            parser="Trading 212",
            account="Trading 212 #1",
            file=Path("2026-01.csv"),
            row=7,
            index=5,
            timestamp=datetime.datetime(2026, 1, 5, 9, 30, tzinfo=datetime.UTC),
        )
    )
    second = _hashed(
        TransactionSource(
            parser="Trading 212",
            account="Trading 212 #2",
            file=Path("2026-02.csv"),
            row=2,
            index=0,
        )
    )

    assert first == second
    assert len({first, second}) == 1


def _sale(**changes: object) -> BrokerTransaction:
    """Build a disposal to render, with any field overridden."""
    transaction = BrokerTransaction(
        date=datetime.date(2024, 6, 27),
        action=ActionType.SELL,
        symbol="FOO",
        description="FOO INC",
        quantity=Decimal(10),
        price=Decimal("12.50"),
        fees=Decimal("0.02"),
        amount=Decimal("124.98"),
        currency=USD,
        broker="Charles Schwab",
    )
    return replace(transaction, **changes)  # type: ignore[arg-type]


def test_str_states_the_row_without_python_syntax() -> None:
    """Errors print this, so it has to read as a transaction, not a dump."""
    rendered = str(_sale())

    assert rendered.startswith("2024-06-27 Sell 10 FOO, price 12.5 USD")
    assert "amount 124.98" in rendered
    assert "fees 0.02" in rendered
    assert "Charles Schwab" in rendered
    assert '"FOO INC"' in rendered
    for python_syntax in ("Decimal(", "datetime.date(", "ActionType.", "<", "="):
        assert python_syntax not in rendered


def test_str_leaves_out_what_the_export_did_not_state() -> None:
    """An absent field is absent, not None, an empty dict or an empty list."""
    rendered = str(
        _sale(
            symbol=None,
            description="",
            quantity=None,
            price=None,
            amount=None,
            fees=Decimal(0),
        )
    )

    assert rendered == "2024-06-27 Sell\n  Charles Schwab"
    for empty in ("None", "{}", "[]", "''"):
        assert empty not in rendered


def test_str_names_the_file_and_row_it_was_read_from() -> None:
    """The provenance is the one field that says where to look.

    The path is written the way the platform writes one, with backslashes
    on Windows: it is there for the reader to open, so it has to match what
    their own tools show them.
    """
    path = Path("exports/main.csv")

    rendered = str(_sale(source=TransactionSource(file=path, row=8)))

    assert f"read from row 8 of {path}" in rendered


def test_str_states_the_currency_against_the_amount_when_there_is_no_price() -> None:
    """A dividend states no price, so the currency goes with the amount."""
    rendered = str(
        _sale(
            action=ActionType.DIVIDEND,
            quantity=None,
            price=None,
            fees=Decimal(0),
            amount=Decimal("3.10"),
            isin=Isin(VALID_ISIN),
        )
    )

    assert rendered.startswith("2024-06-27 Dividend FOO, amount 3.1 USD")
    assert f"ISIN {VALID_ISIN}" in rendered


def test_str_names_a_file_that_states_no_row() -> None:
    """A parser that tracks the file but not the line still says which file."""
    rendered = str(_sale(source=TransactionSource(file=Path("main.csv"))))

    assert "read from main.csv" in rendered
    assert "row" not in rendered


def test_str_reads_the_broker_name_and_isin_as_one_phrase() -> None:
    """Three ways of saying what the row is, so they are not three items.

    The comma that is left marks the break that matters, from what the row
    is to where it was read from.
    """
    transaction = _sale(
        isin=Isin(VALID_ISIN), source=TransactionSource(file=Path("main.csv"), row=8)
    )

    rendered = str(transaction)

    assert (
        f'  Charles Schwab "FOO INC" (ISIN {VALID_ISIN}), read from row 8 of main.csv'
    ) in rendered


def test_str_never_names_the_source_account() -> None:
    """`account` is a calculation-local token, not a broker account number."""
    rendered = str(
        _sale(
            source=TransactionSource(
                file=Path("main.csv"), row=8, account="opaque-boundary-token"
            )
        )
    )

    assert "opaque-boundary-token" not in rendered


def test_str_states_a_foreign_fee_in_its_own_currency() -> None:
    """The row's own currency is stated once; another currency carries its own."""
    rendered = str(_sale(foreign_fees={CurrencyCode("EUR"): Decimal("1.20")}))

    assert "price 12.5 USD" in rendered
    assert "fees 1.2 EUR" in rendered


def test_str_states_the_other_count_an_ambiguous_row_could_mean() -> None:
    """Where a split leaves the count uncertain, both readings are shown."""
    rendered = str(_sale(ambiguous_quantity=Decimal(40)))

    assert "10 (or 40) FOO" in rendered


def test_str_marks_a_derived_price_it_shortened() -> None:
    """A price a parser divided out runs long, and shortening it is stated.

    Printing the division in full buries the figures either side of it;
    printing it short and unmarked states a figure the export does not hold.
    """
    price = Decimal("523.6400000971875840180380156")
    quantity = Decimal("3.130074725274725274725274725")

    rendered = str(_sale(price=price, quantity=quantity))

    assert "~3.1300747253 FOO" in rendered
    assert "price ~523.6400000972 USD" in rendered


def test_str_leaves_an_exported_figure_as_the_export_states_it() -> None:
    """Eight decimal places is a real Freetrade price, not a division."""
    rendered = str(_sale(price=Decimal("716.14212813"), quantity=Decimal("0.01475964")))

    assert "0.01475964 FOO" in rendered
    assert "price 716.14212813 USD" in rendered
    assert "~" not in rendered


def test_str_states_a_figure_too_small_to_shorten_in_full() -> None:
    """Shortening this one leaves "0", which is worse than a long figure."""
    rendered = str(_sale(price=Decimal("0.000000000000123")))

    assert "price 0.000000000000123 USD" in rendered


def test_str_states_a_figure_too_large_to_shorten_in_full() -> None:
    """Rounding a copy of this one does not fit, and raising loses the row.

    Absurd for money, and an absurd figure is exactly what the errors
    calling this exist to report, so the row still has to print.
    """
    rendered = str(_sale(amount=Decimal("1E+19")))

    assert "amount 10000000000000000000" in rendered


def test_str_states_a_figure_that_is_not_a_number() -> None:
    """Rounding one raises, and an error about it has to print the row."""
    rendered = str(_sale(amount=Decimal("Infinity")))

    assert "amount Infinity" in rendered


def test_str_states_an_amount_of_nothing_without_a_sign() -> None:
    """A row that came to nothing reads as an error in the tool as "-0"."""
    rendered = str(_sale(amount=Decimal("-0.00")))

    assert "amount 0," in rendered


def test_action_reads_as_a_word_wherever_it_is_interpolated() -> None:
    """Messages interpolate an action directly, so its own str has to read."""
    assert str(ActionType.BUY) == "Buy"
    assert str(ActionType.STOCK_ACTIVITY) == "Stock activity"
    assert str(ActionType.DIVIDEND_TAX) == "Dividend tax"


def test_str_leaves_out_a_description_that_only_repeats_the_symbol() -> None:
    """Some exports set the description to the ticker, which says nothing."""
    assert str(_sale(description="FOO")).endswith("Charles Schwab")


def test_repr_still_carries_every_field() -> None:
    """__str__ is for the reader; __repr__ stays the full dump for debugging."""
    rendered = repr(_sale())

    assert "Decimal('12.50')" in rendered
    assert "capital_adjustments=[]" in rendered


def test_an_error_about_a_transaction_prints_the_readable_form() -> None:
    """Every raise site reaches this one message through the base class."""
    transaction = _sale(source=TransactionSource(file=Path("main.csv"), row=8))

    error = InvalidTransactionError(transaction, "Tried to sell more than the balance")

    assert str(error) == (
        "Tried to sell more than the balance for the following transaction:\n"
        "2024-06-27 Sell 10 FOO, price 12.5 USD, amount 124.98, fees 0.02\n"
        '  Charles Schwab "FOO INC", read from row 8 of main.csv'
    )
