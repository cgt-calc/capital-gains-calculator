# Revolut crypto

cgt-calc reads the CSV account statement of Revolut's crypto account. It is a separate export from
the [Revolut Invest statement](revolut.md), passed with its own option, and both can be used in one
run.

HMRC applies the share matching rules to cryptoassets: a disposal is matched with coins of the same
kind bought on the same day, then with those bought in the following 30 days, then with the pool of
older ones
([HMRC CRYPTO22200](https://www.gov.uk/hmrc-internal-manuals/cryptoassets-manual/crypto22200)).
cgt-calc pools each coin separately, so bitcoin and ether never share a pool.

## Export your account statement

Revolut produces the statement from the crypto section of its app. The menu names change between app
versions, so look for the account statement there, choose **CSV** rather than PDF, and set the
period to run from your first crypto transaction until today.

Keep the exported columns unchanged: `Symbol`, `Type`, `Quantity`, `Price`, `Value`, `Fees` and
`Date`. You can compare yours with the
[sanitised example](https://github.com/cgt-calc/capital-gains-calculator/blob/main/tests/revolut_crypto/data/statement.csv).

## Generate the report

For the 2025/26 tax year, run:

```shell
cgt-calc --year 2025 --revolut-crypto-file revolut-crypto.csv
```

Add `--revolut-file` and any other broker in the same run: the annual exempt amount is shared by
every gain you make in the year.

## Supported activity

| Type   | How cgt-calc handles it                                                      |
| ------ | ---------------------------------------------------------------------------- |
| `Buy`  | An acquisition costing the `Value` plus the `Fees`                           |
| `Sell` | A disposal for the `Value`, with the `Fees` as an allowable cost of the sale |

`Value` is the worth of the coins bought or sold and `Fees` is charged on top of it. The price is
recalculated as `Value` divided by `Quantity`, so the cost or proceeds always agree with the total
Revolut recorded rather than its rounded `Price`. Each row keeps the currency of its `Value`, which
can be `$`, `£` or `€`, and a foreign amount is converted with
[HMRC's exchange rate](../extra-data-and-options.md#exchange-rates) for its date.

Every other type, such as `Send`, stops the run with `Unsupported action`. A `Send` can be a move to
another wallet of yours, which is not a disposal, or a payment or gift to someone else, which is,
and the row does not say which. Work out what each one was. If it was a disposal, record it in a
[RAW file](raw.md) as a `SELL` at the coins' value that day, or as a
[gift](raw.md#gifts-to-anyone-else) if you gave the coins away. Then rerun with
[`--skip-unsupported-rows`](../usage.md#skip-unsupported-rows) to leave the original row out.

### Symbols

A coin's symbol is reported with a `CRYPTO:` prefix, as in `CRYPTO:BTC`. Some exchange-traded
products use a coin's symbol as their ticker, and the prefix keeps the coins out of their pool.

### Cash balance

The statement records the coins, not the money: purchases are paid from your Revolut current account
and sales are paid into it. cgt-calc does not keep a cash balance for this statement, so no balance
check applies to it and `--no-balance-check` is not needed.

### Dates and time zones

The statement does not say which time zone its times are in. cgt-calc reads them as UK time, the
time the tax year and the matching rules run on. A transaction close to midnight could belong to the
day before or after if Revolut wrote it in another zone, so check any that fall near 5 April or near
another transaction of the same coin.

## Known limitations

- Only `Buy` and `Sell` rows are read; see [Supported activity](#supported-activity).
- From the 2024/25 tax year, the Self Assessment return asks for gains on cryptoassets separately
    from other gains. The report combines every input, so run cgt-calc a second time with only
    `--revolut-crypto-file` to get the cryptoasset figures, and use the combined run for the annual
    exempt amount and the tax.
- Staking rewards, airdrops, coin-for-coin exchanges and other cryptoasset activity outside this
    statement are not covered.
