# Contributing

Thanks for your interest in improving **cgt-calc** — contributions of all kinds are welcome! If you
find a bug or have feature ideas, please open an
[issue](https://github.com/cgt-calc/capital-gains-calculator/issues) or pull request.

## Getting started

This project uses [uv](https://docs.astral.sh/uv/) for dependency management, testing, and builds.

### 1. Install uv

Follow [uv’s installation guide](https://docs.astral.sh/uv/getting-started/installation/):

```shell
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Clone the repository

```shell
git clone https://github.com/cgt-calc/capital-gains-calculator.git
cd capital-gains-calculator
```

### 3. Set up the environment

```shell
uv sync
```

This command creates a virtual environment and installs all project and development dependencies
into it. Run it again after pulling new changes to update dependencies.

## Writing documentation

Write for someone using cgt-calc to prepare their figures, without assuming they know the code or
tax terminology. Developer pages can assume programming knowledge. Use plain British English,
address the reader as “you”, and explain unfamiliar terms while keeping the names shown in the
product and HMRC guidance.

- **Start with the task.** Give prerequisites, steps and expected results in the order the reader
    needs them. Keep optional explanations and detailed reference out of the main sequence; link to
    them where they help. Split a page when it serves separate tasks, not just because it is long.
- **Make the page easy to scan.** Use descriptive headings, numbered steps for a sequence, bullets
    for related items, and tables for comparisons. Put conditions before the instructions they
    affect. Keep each paragraph about one idea.
- **Preserve useful detail.** Keep examples and warnings that prevent mistakes. For a limitation,
    say what is unsupported, whether cgt-calc detects it, what could go wrong, and what to do.
    Remove repetition and unnecessary wording without rewriting prose that already works.
- **Check the evidence.** Verify behaviour against the implementation and tests. Check tax, security
    and privacy claims against current primary sources, and link to the relevant section. Check
    commands, output labels, calculations and citations before editing the prose. Use small examples
    with invented names, dates and values; do not copy or rescale a real transaction history.

### Using an LLM

Treat text from a large language model (LLM) as a draft. Give it the reader's task, existing page,
relevant code and tests, and checked sources. Request a focused edit without invented details. Apply
the evidence checks above yourself; a second LLM agreeing is not verification. Read the whole page
afterwards to check the order of steps, remove repetition and retain important limitations.

### Check your documentation change

Update navigation in `zensical.toml` and links from other pages when moving content. When a heading
moves to another page, add a redirect from its old address under `redirect_maps` in `zensical.toml`,
so that links cgt-calc has printed and existing bookmarks still work. Run the
[formatting, spelling and grammar checks](#code-style), then build the site and check links:

```shell
uv run --group docs zensical build --clean
scripts/check_links.sh
```

Preview the result with:

```shell
uv run --group docs zensical serve
```

Open the local address printed in the terminal. Follow the changed instructions and links, open any
old address you redirected, and check that headings, tables and code blocks are readable on both a
narrow and a wide screen.

## Code layout

The calculation has two passes:

`broker files → parsers → BrokerTransaction → ingestion → matching → CapitalGainsReport → renderers`

- `cgt_calc/parsers/` reads broker exports and converts their rows into `BrokerTransaction` values.
    Keep broker-specific columns and normalisation here, and keep tax matching outside the parsers.
- `cgt_calc/ingestion.py` validates transactions and runs the first pass. It records dividends and
    interest for `income.py` to process, and delegates share-reorganisation planning to
    `stock_split_planning.py`.
- `cgt_calc/calculator_state.py` holds `PreparedHistory`, which the first pass fills and the second
    pass only reads, and `RunState`, which the second pass builds and which is reset at the start of
    every calculation.
- `cgt_calc/matching.py` runs the second pass, applying the same-day, bed-and-breakfast and Section
    104 matching rules.
- `cgt_calc/model.py` defines shared values and the report model. `render_text.py` and
    `render_latex.py` format that report without calculating it.
- `cgt_calc/main.py` connects the stages behind `CapitalGainsCalculator`, whose `calculate()` runs
    the two passes in order. `cgt_calc/cli.py` handles command-line input and output.

Put a change in the module that owns its behaviour. Extract a new module only when one complete
concern can move behind explicit inputs; do not split a file only to reduce its line count.

## Code style

All checks in CI must pass before merging changes.

We use:

- [ruff](https://docs.astral.sh/ruff/) — for Python linting and formatting
- [mypy](https://mypy-lang.org/) — for static type checking
- [ty](https://docs.astral.sh/ty/) — for additional static type checking
- [pytest](https://docs.pytest.org/) — for running tests
- [dprint](https://dprint.dev/) — for formatting Markdown, YAML, TOML, JSON, and Dockerfiles
- [shfmt](https://github.com/mvdan/sh#shfmt) - for formatting shell scripts
- [codespell](https://github.com/codespell-project/codespell) - for catching common misspellings
- [Harper](https://writewithharper.com/) - for grammar and spell checking of comments, docstrings,
    and Markdown docs

`prek` can be used to run all checks with one command (see below).

Links in Markdown are checked separately by [lychee](https://lychee.cli.rs/), which runs in CI. Run
`scripts/check_links.sh` to check them locally. It makes two passes: an offline one over local files
and heading anchors, then a network one over external links. Pass `offline` or `external` to run
just one. Shared settings live in `lychee.toml`.

The project uses **Python 3.12** as the minimum supported version.

## Prek

`prek` is fully compatible with `pre-commit`, so `pre-commit` can be used as well.

Install [`prek`](https://prek.j178.dev/) first, e.g. using `uv` or `pipx`:

```shell
uv tool install prek
```

Installing it globally avoids issues when `prek` invokes `uv` inside hooks.

Activate the `prek` hook:

```shell
prek install
```

This will automatically check code style, linting, and types before each commit.

Type suppressions must name the diagnostic for both checkers. When both tools report the same
intentional violation, keep their targeted comments together:

```python
value = untyped_value  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
```

You can also run all checks on the repository manually:

```shell
prek run --all-files
```

Or you can run single hook:

```shell
prek run mypy --all-files
prek run ty --all-files
prek run pytest
prek run --hook-stage manual python-typing-update --all-files
```

Harper runs as a regular hook via `uv` (the `harper-cli` dev dependency), so no separate
installation is needed. Project vocabulary (tickers, broker names, identifiers) lives in
`.harper-dictionary.txt`; the Harper editor extension picks it up automatically. Words that the
dictionary cannot whitelist — Harper dialect-gates US spellings such as product names — are accepted
via regexes in `.harper-ignore.txt`.

## Running linters and tests manually

You can also run linters and tests directly:

```shell
uv run pytest
uv run pytest -k <expr> -q # run subset
uv run ruff check .
uv run mypy cgt_calc tests scripts
uv run ty check cgt_calc tests scripts
```

## Managing dependencies

| Task                                                        | Command                             |
| ----------------------------------------------------------- | ----------------------------------- |
| Add a runtime dependency                                    | `uv add <package-name>`             |
| Add a development dependency                                | `uv add --group dev <package-name>` |
| Upgrade dependencies                                        | `uv lock --upgrade`, then `uv sync` |
| Apply manual edits to `pyproject.toml` and update `uv.lock` | `uv sync`                           |

## Updating the example report

To regenerate the example PDF report used in the docs, run:

```shell
./scripts/generate_example_report.sh
```

This writes `docs/assets/example_report.pdf`. Commit the updated file if your changes affect report
generation. The example source transactions and fixed exchange rates live in
`scripts/example_data/`, so generation is deterministic and does not call the exchange-rate API.

After regenerating the PDF, update the images made from it: the teaser the README shows and the
full-page images the docs show:

```shell
./scripts/generate_example_preview.sh
```
