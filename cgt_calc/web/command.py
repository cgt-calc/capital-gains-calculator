"""Turn submitted form values into the command line that is run."""

from __future__ import annotations

from dataclasses import dataclass, field
import shlex
from typing import TYPE_CHECKING

from .forms import FieldKind, flatten

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from .forms import Section

PROGRAM = "cgt-calc"


@dataclass(frozen=True, slots=True)
class Submission:
    """What was entered in the form for a run.

    Kept with the run so its options can be shown again, and its uploaded
    inputs used for another run. ``uploaded`` maps an option name to the saved
    path, relative to the folder the tool ran in.
    """

    text: Mapping[str, str] = field(default_factory=dict)
    checked: frozenset[str] = frozenset()
    uploaded: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Command:
    """Arguments of one run, as they would be typed in a terminal."""

    argv: tuple[str, ...]

    @property
    def display(self) -> str:
        """The command as a shell line, for the report and for copying."""
        return shlex.join((PROGRAM, *self.argv))


def _pair(flag: str, value: str) -> tuple[str, ...]:
    """Return an option with its value.

    A value starting with a dash is joined with ``=``. Given separately, the
    parser would read it as another option, so text typed into the form could
    switch on options the form does not offer.
    """
    if value.startswith("-"):
        return (f"{flag}={value}",)
    return (flag, value)


def build_command(
    sections: Sequence[Section],
    *,
    text: Mapping[str, str],
    checked: Collection[str],
    uploaded: Mapping[str, str],
) -> Command:
    """Build the arguments for a run.

    ``text`` holds typed values by option name, ``checked`` the switched-on
    options, and ``uploaded`` the saved path of each upload, relative to the
    directory the run happens in.
    """
    argv: list[str] = []
    for option in flatten(sections):
        match option.kind:
            case FieldKind.FILE | FieldKind.FILES:
                if option.dest in uploaded:
                    argv.extend(_pair(option.flag, uploaded[option.dest]))
            case FieldKind.TEXT | FieldKind.YEAR | FieldKind.DATE:
                value = text.get(option.dest, "").strip()
                if value:
                    argv.extend(_pair(option.flag, value))
            case FieldKind.FLAG:
                if option.dest in checked:
                    argv.append(option.flag)
            case FieldKind.OUTPUT_FILE:
                if option.dest in checked:
                    argv.extend((option.flag, option.output_path))
    return Command(tuple(argv))
