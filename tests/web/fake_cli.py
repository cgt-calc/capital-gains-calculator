"""Stand-in for ``python -m cgt_calc.cli`` used by the web tests.

It reads the same arguments as the real tool but does no calculation, so the
tests exercise the web layer without pandas, network access or LaTeX.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time


def main(argv: list[str]) -> int:
    """Behave according to the marker files and options in the arguments."""
    print("Parsing", " ".join(argv), file=sys.stderr, flush=True)
    inputs = sorted(
        f"{path}:{path.read_text(encoding='utf-8', errors='replace')}"
        for path in Path().rglob("*")
        if path.is_file() and path.parts[0] != "out"
    )
    print("Inputs", inputs, file=sys.stderr, flush=True)
    if "--fail" in argv:
        print("ERROR: something went wrong", file=sys.stderr, flush=True)
        return 1
    if "--slow" in argv:
        time.sleep(30)
    if os.environ.get("NO_COLOR") != "1":
        print("ERROR: colour was not disabled", file=sys.stderr, flush=True)
        return 1
    print("WARNING: a warning", file=sys.stderr, flush=True)
    out = Path("out")
    out.mkdir(exist_ok=True)
    (out / "calculations.pdf").write_bytes(b"%PDF-1.4 fake")
    print("Portfolio at the end of the tax year")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
