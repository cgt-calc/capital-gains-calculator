[![PyPI version](https://img.shields.io/pypi/v/cgt-calc?style=flat-square)](https://pypi.org/project/cgt-calc/)
[![CI](https://img.shields.io/github/actions/workflow/status/cgt-calc/capital-gains-calculator/ci.yml?style=flat-square&label=CI)](https://github.com/cgt-calc/capital-gains-calculator/actions)
[![codecov](https://img.shields.io/codecov/c/github/cgt-calc/capital-gains-calculator?style=flat-square)](https://app.codecov.io/gh/cgt-calc/capital-gains-calculator)

# <img src="https://cgt-calc.uk/assets/logo.svg" alt="" width="34" valign="middle"> UK Capital Gains Calculator

Use cgt-calc to calculate **UK capital gains** from your investment transaction history and generate
a detailed report from a supported broker export.

Supported sources include **Charles Schwab**, **Freetrade**, **Hargreaves Lansdown**, **Interactive
Brokers**, **Morgan Stanley**, **Revolut** (shares and crypto), **Sharesight**, **Trading 212**,
**Vanguard**, or a custom **RAW** format. Check your broker's guide before starting, especially for
employer shares, because not every award export or transaction type is supported.

See the [full documentation](https://cgt-calc.uk/) for installation, broker export guides, offshore
funds (ERI), extra data and options, and Docker.

## 🚀 Quick Start

Install with [uv](https://docs.astral.sh/uv/getting-started/installation/) (or pipx/pip) and
generate a report for a tax year:

```shell
uv tool install cgt-calc
cgt-calc --year 2025 --schwab-file schwab_transactions.csv
```

Replace the Schwab option with the one for your broker. LaTeX is needed to create the PDF report.
See [Installation](https://cgt-calc.uk/installation/), [Brokers](https://cgt-calc.uk/brokers/), and
[Generate a report](https://cgt-calc.uk/usage/) for the complete steps.

## What cgt-calc calculates

For supported transactions, cgt-calc calculates capital gains in **GBP** using the UK **same-day**,
**30-day ("bed and breakfast")** and **Section 104 holding** rules. The terminal summary and
detailed **PDF report** show **Disposal proceeds** (sale amounts or values for other disposals),
**Allowable costs** (costs deducted when calculating gains), gains, losses, dividends and interest.
See [Generate a report](https://cgt-calc.uk/usage/) for how to create and check them.

For supported tax years, cgt-calc also provides [tax estimates](https://cgt-calc.uk/tax-estimates/),
which use your income if you give it. cgt-calc covers only the transactions you supply, does not
work out your final tax bill and does not submit a return. Check your
[broker's limitations](https://cgt-calc.uk/brokers/) and the
[offshore funds limitations](https://cgt-calc.uk/offshore-funds/#unsupported-functionality) before
relying on the result.

## 📊 Example Report

This compact 2025/26 example shows foreign-currency transactions, how sales are matched with shares
acquired at different times, gains and losses, a dividend with overseas tax, and cash interest.

<a href="https://cgt-calc.uk/assets/example_report.pdf">
  <img src="https://cgt-calc.uk/assets/example_report_preview.webp" alt="Preview of the 2025/26 example report" width="600">
</a>

## 🤝 Contributing

Contributions are welcome! If you find a bug, have feature ideas, or want to add support for more
brokers, please open an **issue** or **pull request**. See the
[development guide](https://cgt-calc.uk/development/contributing/).

## ⚠️ Disclaimer

This tool is a calculation aid, **not tax advice**. It calculates capital gains figures from the
transaction history you provide and is designed to apply the UK tax rules it supports, following the
legislation and published HMRC guidance. Its authors and contributors are not acting as your tax
advisers, the results may contain errors, and tax rules change.

You are responsible for providing complete data and checking the figures against your records and
current HMRC guidance before using them in a tax return. For unusual or uncertain circumstances,
consider consulting a suitably qualified tax adviser. The software is provided "as is", without
warranty of any kind; see the [MIT License](LICENSE).
