"""The header check of every CSV file cgt-calc reads that is not a broker export.

Each format declares its columns once, beside its reader. The reader maps
the header with `positions` and plain-INPUT recognition asks `matches`, and
both go through `problems`, so a header one accepts the other cannot refuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .exceptions import ParsingError
from .util import csv_fields

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def _names(names: Sequence[str]) -> str:
    return ", ".join(repr(name) if name else "(no name)" for name in names)


@dataclass(frozen=True)
class CsvColumns:
    """The columns of one of cgt-calc's own CSV formats.

    A header names each column once, ignoring letter case and spaces around
    the name, in any order unless `in_order` is set.
    """

    required: tuple[str, ...]
    optional: tuple[str, ...] = ()
    # Set where the rows are not one value per column: every cell of an ISIN
    # translation row after the first is a ticker, so the ISIN comes first.
    in_order: bool = False

    def _known(self) -> dict[str, str]:
        """Map each column's case-folded name to the name as declared."""
        return {name.casefold(): name for name in (*self.required, *self.optional)}

    def problems(self, header: Sequence[str]) -> str | None:
        """Say what is wrong with `header`, or None when it is this format's."""
        known = self._known()
        found = [cell.strip().casefold() for cell in header]
        # Quoted as Python quotes a string, so that a space at the end of a
        # name, or a character that cannot be seen, shows up.
        shown = repr(csv_fields(list(header)))
        if not any(name in known for name in found):
            # As many optional columns as the line has cells beyond the
            # required ones, so a row of data gets a header as wide as it.
            extra = self.optional[: max(0, len(header) - len(self.required))]
            example = csv_fields([*self.required, *extra])
            if all(not name for name in found):
                return (
                    "This line must be the header, but it is blank. Put this "
                    "header line in its place, with the names in the order of "
                    f"the values in each row:\n  {example}"
                )
            # A row of data and a header with every name wrong look alike, so
            # the advice covers both and never says to replace the line.
            return (
                "This line must be the header, but it names none of the "
                f"columns:\n  {shown}\nIf it is a row of data, add this header "
                "line above it, with the names in the order of its values. If it "
                "is the header, correct each name where it stands:\n"
                f"  {example}"
            )
        missing = [name for name in self.required if name.casefold() not in found]
        unknown = [
            cell.strip()
            for cell, name in zip(header, found, strict=True)
            if name not in known
        ]
        repeated = list(
            dict.fromkeys(
                known[name] for name in found if name in known and found.count(name) > 1
            )
        )
        if not (missing or unknown or repeated):
            if self.in_order and tuple(known[name] for name in found) != self.required:
                first = self.required[0]
                return (
                    "This line must be the header, but its columns are in "
                    f"another order:\n  {shown}\nEvery cell after the {first} is "
                    f"a ticker, so the {first} comes first. Use this header line, "
                    f"and move each row's {first} to the start of the row:\n"
                    f"  {csv_fields(list(self.required))}"
                )
            return None
        parts = []
        if missing:
            parts.append(f"Missing columns: {_names(missing)}.")
        if unknown:
            parts.append(f"Unknown columns: {_names(unknown)}.")
        if repeated:
            parts.append(f"Repeated columns: {_names(repeated)}.")
        # The names to use, not a line to paste: pasted over a header in
        # another order, it would put values under the wrong names.
        example = csv_fields(
            [
                *self.required,
                *(name for name in self.optional if name.casefold() in found),
            ]
        )
        order = "in this order" if self.in_order else "once each, in any order"
        message = (
            "This line must be the header, but it does not name the right "
            f"columns:\n  {shown}\n{' '.join(parts)}\n"
            "Correct each name where it stands, so every value stays under its "
            "name; if this line is a row of data, add a header line above it "
            f"instead. The header names these columns, {order}:\n  {example}"
        )
        if self.optional:
            message += f"\nOptional columns: {_names(self.optional)}."
        return message

    def matches(self, header: Sequence[str]) -> bool:
        """Say whether `header` is this format's, as its reader decides."""
        return self.problems(header) is None

    def positions(
        self, header: Sequence[str], file: Path, *, row_index: int = 1
    ) -> dict[str, int]:
        """Map each column `header` names to its index, in the header's order.

        Raise the reader's error when `problems` finds one, so no header is
        read that recognition would not name.
        """
        problem = self.problems(header)
        if problem is not None:
            raise ParsingError(file, problem, row_index=row_index)
        known = self._known()
        return {known[cell.strip().casefold()]: i for i, cell in enumerate(header)}
