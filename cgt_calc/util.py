"""Utility functions."""

from collections.abc import Generator
from contextlib import contextmanager
import decimal
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys
import textwrap
from typing import TextIO


def parse_decimal(value: str, context: str, *, strip: str = "") -> Decimal:
    """Parse an exported field as a finite decimal amount.

    `context` names the field in the error, in whatever wording the parser
    already uses, e.g. `column 'Quantity'`. `strip` lists the characters to
    remove before parsing, such as the thousands separators and currency
    symbols a broker writes into a number; the error still quotes the field
    as the export wrote it.

    "NaN" and "Infinity" parse as decimals but are not amounts. Refused here,
    where the field is still known, rather than at the first arithmetic that
    touches them, which raises a bare decimal exception naming neither the
    file nor the row.
    """
    try:
        parsed = Decimal(value.translate(str.maketrans("", "", strip)))
    except InvalidOperation as err:
        raise ValueError(f"Invalid decimal in {context}: {value!r}") from err
    if not parsed.is_finite():
        raise ValueError(f"Invalid decimal in {context}: {value!r}")
    return parsed


def round_decimal(value: Decimal, digits: int = 0) -> Decimal:
    """Round decimal to given precision."""
    with decimal.localcontext() as ctx:
        ctx.rounding = decimal.ROUND_HALF_UP
        return Decimal(round(value, digits))


def normalize_amount(amount: Decimal) -> Decimal:
    """Normalize amount to prevent unbounded precision growth.

    Financial amounts are rounded to 10 decimal places, which is far more
    precision than needed for real-world financial calculations, while preventing
    precision from growing unbounded through currency conversions with repeating
    decimals (e.g., GBP/USD conversions).

    This allows using Python's default 28-digit Decimal precision instead of
    requiring 50+ digits.
    """
    return round_decimal(amount, 10)


def indent_entry(entry: object) -> str:
    """Indent every line of one item in a message that lists several.

    A transaction renders over more than one line, so indenting only the
    first would leave every line at the same depth and a list of two rows
    would read as a list of four.
    """
    return textwrap.indent(str(entry), "  ")


def strip_zeros(value: Decimal) -> str:
    """Strip trailing zeros from Decimal."""
    return f"{value:.10f}".rstrip("0").rstrip(".")


def exact_str(value: Decimal) -> str:
    """Format a Decimal in full, for output that is meant to be read back.

    `strip_zeros` is for display and stops at ten decimal places, which
    turns a small enough value into "0". A value that will be parsed again
    has to keep every digit it has, in plain notation so the reader does
    not need to cope with exponents. Formatting never rounds, unlike
    `normalize()`, which applies the context precision.

    Also the fallback for `display_str` when shortening a figure would
    hide it.
    """
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


# Past what the brokers here export, so an exported figure is not marked,
# and the precision the calculation itself normalises to.
DISPLAY_PLACES = 10


def display_str(value: Decimal) -> str:
    """Format a figure for someone reading it, marking a shortened one.

    Several parsers state no price and divide an amount by a quantity to
    get one, so the figure carries the full precision of that division: a
    holding bought for 2381.35 renders a price of
    99.02666666666666666666666667, which buries the figures either side of
    it. Shortening it silently would state a figure neither the export nor
    the calculation holds, so a shortened one is marked.

    A figure that cannot be shortened is printed whole rather than refused,
    because every caller is an error already naming a row the user has to
    find. That covers "NaN" and "Infinity", a value with more integer
    digits than a rounded copy will fit in, and one small enough that
    shortening would leave "0": a figure the reader cannot check is worse
    than a long one.
    """
    if not value.is_finite():
        return exact_str(value)
    if value == 0:
        # A figure subtracted to nothing keeps the sign it was heading for,
        # and "amount -0" reads as a fault in the tool rather than a row
        # that came to nothing.
        return "0"
    try:
        shown = round_decimal(value, DISPLAY_PLACES)
    except InvalidOperation:
        return exact_str(value)
    if shown == value:
        return exact_str(value)
    if shown == 0:
        return exact_str(value)
    return f"~{exact_str(shown)}"


def luhn_check_digit(payload: str) -> int:
    """Return the check digit for a string of digits using the Luhn algorithm.

    Reference: https://en.wikipedia.org/wiki/Luhn_algorithm
    """
    if len(payload) % 2 == 1:
        payload = f"0{payload}"  # zero pad so length is even
    checksum = 0

    luhn_even_digit_max_value = 9
    luhn_even_digit_multiplier = 2
    for idx, digit_char in enumerate(payload[::-1]):
        digit = int(digit_char)
        if idx % 2 == 0:
            digit *= luhn_even_digit_multiplier
            if digit > luhn_even_digit_max_value:
                digit -= luhn_even_digit_max_value
        checksum += digit

    return (
        10 - (checksum % 10)
    ) % 10  # using mod operator twice ensures the check digit is < 10


def approx_equal(
    val_a: Decimal, val_b: Decimal, approx_quantity: Decimal = Decimal("0.01")
) -> bool:
    """Check if two decimals are equal within approx_quantity.

    It is not clear how Schwab or other brokers round the dollar value,
    so assume the values are equal if they are within approx_quantity.
    Defaults to 0.01.
    """
    return abs(val_a - val_b) < approx_quantity


def approx_equal_scaled(
    val_a: Decimal,
    val_b: Decimal,
    *,
    reference: Decimal,
    rel_tolerance: Decimal,
    abs_tolerance: Decimal = Decimal(0),
) -> bool:
    """Check two decimals agree to a tolerance that grows with the values.

    `approx_equal` takes a fixed tolerance, which fits a figure whose error
    is a rounding step. It does not fit one reconstructed from a price and an
    exchange rate: that error is a proportion of the value, so a bound fitted
    to a holding worth a few hundred pounds rejects an ordinary one. Give
    `reference` the value the error scales with, and `abs_tolerance` any
    fixed rounding on top of it.
    """
    return abs(val_a - val_b) < abs_tolerance + rel_tolerance * abs(reference)


def open_with_parents(path: Path, *, clear_content: bool = True) -> TextIO:
    """Open a file for writing, creating parent directories if they do not exist.

    Newline translation is disabled because every caller writes CSV:
    csv.writer emits its own CRLF terminators, which translation would
    double into CR CR LF on Windows.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("w" if clear_content else "r+", encoding="utf8", newline="")


if sys.platform == "win32":
    import msvcrt

    @contextmanager
    def exclusive_lock(file: TextIO) -> Generator[None]:
        """Hold an exclusive advisory lock on the whole open file."""
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    @contextmanager
    def exclusive_lock(file: TextIO) -> Generator[None]:
        """Hold an exclusive advisory lock on the whole open file."""
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(file.fileno(), fcntl.LOCK_UN)
