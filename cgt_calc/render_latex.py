"""Render PDF report with LaTeX."""

import logging
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Final

from colorama import Fore
import jinja2

from .const import LATEX_TEMPLATE_RESOURCE, PACKAGE_NAME
from .exceptions import LatexRenderError, MissingExternalToolError, saving
from .logging import style_text
from .model import CapitalGainsReport
from .report_view import build_report_view
from .util import strip_zeros

LOGGER = logging.getLogger(__name__)

# The characters LaTeX acts on, and how each is written as text. A name from
# a broker file can hold any of them: "LifeStrategy 80% Equity Fund". The last
# three are ordinary to LaTeX but print as other signs in its default font.
_LATEX_ESCAPES: Final = str.maketrans(
    {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
        "<": r"\textless{}",
        ">": r"\textgreater{}",
        "|": r"\textbar{}",
    }
)


def _escape_latex(value: object) -> str:
    """Escape a value the template prints, whatever its type, as its text."""
    return str(value).translate(_LATEX_ESCAPES)


def render_pdf(
    report: CapitalGainsReport,
    output_path: Path,
    *,
    skip_pdflatex: bool = False,
) -> None:
    """Render LaTeX to a PDF report."""
    jobname = output_path.stem
    out_dir = output_path.parent
    tex_path = out_dir / f"{jobname}.tex"
    progress = (
        f"Writing LaTeX report to {tex_path}..."
        if skip_pdflatex
        else f"Writing PDF report to {output_path}..."
    )
    LOGGER.info(
        "\n%s\n",
        style_text(progress, colour=Fore.CYAN, emoji="💾", stream=sys.stderr),
    )
    latex_template_env = jinja2.Environment(
        block_start_string="\\BLOCK{",
        block_end_string="}",
        variable_start_string="\\VAR{",
        variable_end_string="}",
        comment_start_string="\\#{",
        comment_end_string="}",
        line_statement_prefix="%%",
        line_comment_prefix="%#",
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
        # Applied to every value the template prints, so a line added to it
        # later is covered too.
        finalize=_escape_latex,
        loader=jinja2.PackageLoader(PACKAGE_NAME, "resources"),
        extensions=["jinja2.ext.loopcontrols"],
    )
    latex_template_env.filters["money"] = "{:,}".format
    template = latex_template_env.get_template(LATEX_TEMPLATE_RESOURCE)
    output_text = template.render(
        view=build_report_view(report),
        strip_zeros=strip_zeros,
    )

    elsewhere = "To save it somewhere else, pass --output."
    with saving("the report", tex_path if skip_pdflatex else output_path, elsewhere):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # Without pdflatex the LaTeX source is the report: save it for the user
        # instead of producing a PDF.
        if skip_pdflatex:
            tex_path.write_text(output_text, encoding="utf-8")
            return
        if output_path.exists():
            # pdflatex stops at a PDF it cannot write, and the error for that
            # only points at its log.
            with output_path.open("r+b"):
                pass

    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", prefix="cgt_calc_", suffix=".tex", delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(output_text)

    log_path = out_dir / f"{jobname}.latex.log"

    try:
        if shutil.which("pdflatex") is None:
            raise MissingExternalToolError("pdflatex")
        cmd = [
            "pdflatex",
            "-file-line-error",
            "-halt-on-error",
            "-interaction=nonstopmode",
            f"-output-directory={out_dir}",
            f"-jobname={jobname}",
            str(tmp_path),
        ]
        with saving("the report", output_path, elsewhere):
            log = log_path.open("w", encoding="utf-8")
        with log:
            subprocess.run(cmd, check=True, stdout=log, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as err:
        raise LatexRenderError(log_path) from err
    finally:
        # Always attempt to clean up temp and aux files.
        tmp_path.unlink(missing_ok=True)
        for ext in (".log", ".aux"):
            (out_dir / f"{jobname}{ext}").unlink(missing_ok=True)
