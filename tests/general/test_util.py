"""Tests for cgt_calc.util helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest

from cgt_calc.util import approx_equal, exact_str


@pytest.mark.parametrize(
    ("val_a", "val_b", "approx_quantity", "expected"),
    [
        # The 0.01 tolerance the default uses, given explicitly.
        (Decimal("1.00"), Decimal("1.005"), Decimal("0.01"), True),
        (Decimal("1.00"), Decimal("1.02"), Decimal("0.01"), False),
        # A tight explicit tolerance (0.0001) must actually be honoured:
        # a difference of 0.005 is well within the old hardcoded 0.01
        # default but must fail a tighter 0.0001 tolerance.
        (Decimal("1.0000"), Decimal("1.0050"), Decimal("0.0001"), False),
        (Decimal("1.0000"), Decimal("1.00005"), Decimal("0.0001"), True),
        # A looser explicit tolerance (1) must also be honoured: a
        # difference that would fail the hardcoded 0.01 default should
        # pass here.
        (Decimal("1.00"), Decimal("1.50"), Decimal(1), True),
        (Decimal("1.00"), Decimal("3.00"), Decimal(1), False),
    ],
)
def test_approx_equal_respects_approx_quantity(
    val_a: Decimal, val_b: Decimal, approx_quantity: Decimal, expected: bool
) -> None:
    """approx_equal must compare against the given tolerance, not a hardcoded one."""
    assert approx_equal(val_a, val_b, approx_quantity) is expected


def test_approx_equal_default_tolerance() -> None:
    """approx_equal defaults to a tolerance of 0.01 when none is given."""
    assert approx_equal(Decimal("1.00"), Decimal("1.005")) is True
    assert approx_equal(Decimal("1.00"), Decimal("1.02")) is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("10.0000000000"), "10"),
        (Decimal(400), "400"),
        (Decimal("0.01") / Decimal(1_000_000_000), "0.00000000001"),
        (Decimal("0.00000000001"), "0.00000000001"),
        (Decimal(100) / Decimal(3), "33.33333333333333333333333333"),
        # More digits than the context holds: kept, not rounded to 28.
        (Decimal("1.23456789012345678901234567891"), "1.23456789012345678901234567891"),
        (Decimal("1E+2"), "100"),
        (Decimal("0E-10"), "0"),
    ],
)
def test_exact_str_keeps_every_digit(value: Decimal, expected: str) -> None:
    """Output meant to be parsed again must not round to nothing or use exponents."""
    assert exact_str(value) == expected
    assert Decimal(exact_str(value)) == value
