# Installation

## Prerequisites

- **Python 3.12** or newer (tested on 3.12, 3.13, and 3.14)
- **LaTeX** if you want a PDF report. Install `pdflatex` using the instructions below. You can run
    cgt-calc without it by using `--no-report`.

## Install cgt-calc

Install the calculator with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```shell
uv tool install cgt-calc
```

Alternatively, install it with [pipx](https://pipx.pypa.io/latest/how-to/install-pipx.html):

```shell
pipx install cgt-calc
```

Or use [pip](https://pip.pypa.io/en/stable/getting-started/) inside an active Python virtual
environment:

```shell
python -m pip install cgt-calc
```

Or run it directly without installing:

```shell
uvx cgt-calc
```

## Nix

The Nix community maintains a
[`cgt-calc` package in nixpkgs](https://search.nixos.org/packages?query=cgt-calc), which bundles
`pdflatex` so no separate LaTeX install is needed:

```shell
nix run nixpkgs#cgt-calc
```

## Installing LaTeX

### macOS

```shell
brew install --cask mactex-no-gui
```

### Debian/Ubuntu

```shell
apt install texlive-latex-base
```

### Windows

[Install MiKTeX.](https://miktex.org/download)

## Check the installation

Run:

```shell
cgt-calc --version
```

If you use `uvx` instead of installing cgt-calc, run `uvx cgt-calc --version`. Once the command
prints a version, choose your [broker](brokers/index.md) and follow the [report guide](usage.md).

### If the command is not found

If installation succeeded but your terminal cannot find `cgt-calc`, use the step for your install
method, then try `cgt-calc --version` again:

- **uv:** run `uv tool update-shell`, then open a new terminal. This adds uv's tool directory to the
    places your shell searches for commands; see
    [uv's instructions](https://docs.astral.sh/uv/guides/tools/#installing-tools).
- **pipx:** run `pipx ensurepath`, then open a new terminal.
- **pip:** activate the same Python virtual environment you used to install cgt-calc.

## Upgrade cgt-calc

Use the same method you used to install it:

| Install method                                                                                      | Upgrade command                            |
| --------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| [uv](https://docs.astral.sh/uv/guides/tools/#upgrading-tools)                                       | `uv tool upgrade cgt-calc`                 |
| [pipx](https://pipx.pypa.io/latest/how-to/manage-installed-apps.html#upgrade-one)                   | `pipx upgrade cgt-calc`                    |
| [pip](https://pip.pypa.io/en/stable/cli/pip_install/#examples), in the original virtual environment | `python -m pip install --upgrade cgt-calc` |

Run `cgt-calc --version` afterwards to check the installed version. If you use `uvx`, run
`uvx cgt-calc@latest --version` to fetch and check the latest release, then use
`uvx cgt-calc@latest` with your usual report options. Docker users should follow
[Updating the image](docker.md#updating-the-image).

## Shell completions (optional)

`cgt-calc` can generate a tab completion script for `bash`, `zsh`, `fish`, `tcsh` and PowerShell.
Create the directory and save the script for your shell:

```shell
# bash
mkdir -p ~/.local/share/bash-completion/completions
cgt-calc --print-completion bash > ~/.local/share/bash-completion/completions/cgt-calc

# zsh (the target directory must be in your $fpath)
mkdir -p ~/.zfunc
cgt-calc --print-completion zsh > ~/.zfunc/_cgt-calc

# fish
mkdir -p ~/.config/fish/completions
cgt-calc --print-completion fish > ~/.config/fish/completions/cgt-calc.fish
```

For PowerShell, create the completions directory if needed and generate the script:

```powershell
$completionDir = "$HOME\.config\powershell\completions"
New-Item -ItemType Directory -Force $completionDir | Out-Null
cgt-calc --print-completion powershell > "$completionDir\cgt-calc.ps1"
```

Then add the following line to your PowerShell profile (`$PROFILE`):

```powershell
. "$HOME\.config\powershell\completions\cgt-calc.ps1"
```

Regenerate the script after upgrading to pick up new options.
