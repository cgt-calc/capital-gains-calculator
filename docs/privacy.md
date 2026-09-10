# Privacy & Disclaimer

## Privacy and data security

**Your transaction files stay on your computer.** cgt-calc processes them locally. Lookup payloads
do not include transaction amounts or personal fields, but external services still receive
connection data such as your IP address. cgt-calc may also send identifiers and dates for the
lookups below.

The external services used are:

- [**UK Trade Tariff API**](https://www.trade-tariff.service.gov.uk/exchange_rates) – to fetch
    monthly GBP exchange rates for 2021 onwards
- [**HMRC's legacy exchange-rate service**](https://www.hmrc.gov.uk/softwaredevelopers/2020-exrates.html)
    – to fetch monthly GBP exchange rates for periods before 2021
- [**Open FIGI API**](https://www.openfigi.com/api/overview) – to translate ISIN codes to tickers
    when needed (only ISIN codes are queried)
- [**Yahoo Finance**](https://uk.help.yahoo.com/kb/finance/privacy-policy-sln6177.html) – to fetch
    current prices when you use `--unrealized-gains`, or historical prices when the transaction
    history contains a spin-off (ticker symbols and, for historical lookups, dates are queried)

Reports and cache files are saved locally. Store them under the same access, retention and deletion
controls as your own tax records.

**Error messages can quote your own data.** When cgt-calc refuses a transaction it prints that
transaction, which can include the path of the file it was read from and the row number within it. A
path can carry your username. The broker pages ask for the complete error message when you report a
problem: read it first, and shorten a path or replace a figure you would rather not publish. See the
[instructions for your broker](brokers/index.md). The `--dump-transactions` export also includes the
source file path and row number for every transaction.

## Disclaimer

This tool is a calculation aid, **not tax advice**. It calculates capital gains figures from the
transaction history you provide and is designed to apply the UK tax rules it supports, following the
legislation and published HMRC guidance. Its authors and contributors are not acting as your tax
advisers, the results may contain errors, and tax rules change.

You are responsible for providing complete data and checking the figures against your records and
current HMRC guidance before using them in a tax return. For unusual or uncertain circumstances,
consider consulting a suitably qualified tax adviser. The software is provided "as is", without
warranty of any kind; see the
[MIT License](https://github.com/cgt-calc/capital-gains-calculator/blob/main/LICENSE).
