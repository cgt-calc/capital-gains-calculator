# Trading 212

cgt-calc reads the CSV account history exported by Trading 212. Give it a directory rather than a
single file because a complete account history may require several exports.

Export the Invest account only. Do not include ISA activity: income and capital gains from
[investments in an ISA do not need to be declared](https://www.gov.uk/individual-savings-accounts/how-isas-work).
If you also export the ISA for your own records, keep it in a separate directory. The CSV does not
identify the account type, so cgt-calc cannot separate the accounts later.

## Export your account history

1. In Trading 212, open **Menu**, then **History**.
2. Select the export button.
3. Choose the date range. Exporting the history since the account was opened is the safest option;
    see [Before you start](../usage.md#before-you-start) for why earlier transactions may be needed.
4. Select every available data category so that orders, cash transactions, dividends and interest
    are included.
5. Download the CSV file.
6. If your complete history requires multiple exports, repeat the process with consecutive date
    ranges, making sure there are no gaps.

The official
[Trading 212 export instructions](https://helpcentre.trading212.com/hc/en-us/articles/360016898917-Can-I-export-the-trading-data-from-my-account)
show the current controls. Use the account-history export, not a PDF statement or the separate pie
export.

## Prepare the directory

Put the CSV files directly in one directory, for example:

```text
trading212/
├── from_2022-04-06_to_2023-04-05.csv
├── from_2023-04-06_to_2024-04-05.csv
└── from_2024-04-06_to_2025-04-05.csv
```

The base filenames do not matter. cgt-calc reads every CSV file directly inside the directory, but
it does not search subdirectories. Do not add unrelated CSV files, and make sure there are no gaps
between date ranges. Matching transactions in overlapping files are reconciled using their recorded
contents and the number of copies in each export. For ordinary transactions, cgt-calc ignores
fractional seconds when matching overlapping files, so older exports without milliseconds still
match newer ones. Reorganisation rows use their exact times, because those times are needed to
validate the event. IDs help distinguish otherwise identical fills, but are not treated as globally
unique because Trading 212 can reuse one ID for different transactions. If overlapping files
represent one split in both the older one-row form and the newer open/close form, cgt-calc refuses
rather than risk applying it twice; replace them with one complete export covering the named dates.

One overlap cannot be reconciled: if two exports give the same transaction different IDs, cgt-calc
reads them as two separate fills and counts both. Reorganisation rows are refused rather than
doubled when that happens, but an ordinary trade is not, so check the final portfolio against
Trading 212 if you keep overlapping exports.

You can compare the structure with this
[sanitised example export](https://github.com/cgt-calc/capital-gains-calculator/blob/main/tests/trading212/data/2024/inputs/transactions.csv).

## Generate the report

For the 2024/25 tax year, run:

```shell
cgt-calc --year 2024 --trading212-dir trading212/
```

`--year 2024` means 6 April 2024 to 5 April 2025. Follow [Generate and Review a Report](../usage.md)
to find and check the output.

## Supported activity

The Trading 212 parser currently handles:

| Activity          | Included transactions                                                                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Orders            | Market, limit, stop and stop-limit buys and sells                                                                                                                  |
| Income            | Ordinary, manufactured and property income dividends; dividend adjustments; cash, lending and fund interest                                                        |
| Cash activity     | Deposits, withdrawals, card credits, card debits, card refunds, currency conversions, result adjustments and cashback adjustments                                  |
| Corporate actions | Stock splits and share consolidations, labelled `Stock split open` and `Stock split close` or `Stock Split`; `Spin off`                                            |
| Costs and taxes   | Transaction, regulatory and currency-conversion fees; stamp duty, stamp duty reserve tax and French transaction tax, including costs charged in a foreign currency |

### Stock splits and share consolidations

Trading 212 exports a share reorganisation as two rows: `Stock split close` states your complete
position before it and `Stock split open` states your complete position after it. Both are needed,
and cgt-calc combines them into one event. They can be written in either order, and they can fall in
different exports, which is why the whole directory is read before they are paired.

Forward splits and consolidations are both supported, including fractional holdings and ratios that
are not whole numbers. The event is not a sale or a purchase: no money moves, no gain or loss
arises, and the pooled cost is unchanged. Only the number of units changes, so a later disposal
takes a different share of the same cost
([TCGA 1992 s127](https://www.legislation.gov.uk/ukpga/1992/12/part/IV/chapter/II),
[CG51805](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51805)). The report shows
the event on its own, with the counts either side and the ratio.

The `Total`, `Price / share` and `Exchange rate` columns on those rows are checked against each
other and against the counts, and are not read as proceeds or as a new cost. Both currency columns
have to be filled in: a price with no stated currency cannot be checked against a total, and an
exchange rate does not supply the missing name. A row that states a result, a fee, a tax or a cash
movement is refused: a consolidation that pays cash may be a part disposal rather than a
reorganisation, and that is not the same calculation.

Every original split row is checked before overlapping copies are removed. If a half is missing, if
two halves cannot be matched one to one, or if the two rows disagree about the ticker, identifier,
currencies, time or value of the position, cgt-calc stops and names the rows, fields and values.
Check those rows against Trading 212 and replace partial files with one complete export covering the
event date.

If you hold one security under two tickers that cgt-calc [combines](#tickers-and-exchange-listings),
Trading 212 may state a reorganisation once for each listing, and both then apply to the one
holding. If they fall on the same day, cgt-calc stops with **has more than one reorganisation**. If
they fall on different days, it applies the reorganisation twice, with no error. Check that the
report shows it once. If cgt-calc stops or the report shows it twice, remove the `Stock split close`
and `Stock split open` rows of one listing from a copy of the export: the other listing's rows give
the ratio, and cgt-calc applies it to the whole holding.

### Dates and time zones

Trading 212 timestamps every transaction in UTC. cgt-calc converts each one to UK time, GMT in
winter and BST in summer, before taking the date. The tax year boundary and the same-day and 30-day
matching rules all run on UK calendar days, and the boundary always falls inside BST, so a
transaction stamped after 23:00 UTC on 5 April belongs to the following tax year.

### Tickers and exchange listings

Trading 212 names a security by the ticker of the listing you traded, so one security can appear
under two tickers: the German line of a US share carries its German code, and a fund can have a
different ticker for each exchange and currency it trades in. cgt-calc pools, matches and prices
holdings by ticker, so it rewrites the aliases it has confirmed to one ticker for the security, and
the report shows one holding rather than two. That ticker may not be the one you traded: `NVD` under
`US67066G1040` is reported as `NVDA`. On a row that names its ISIN, the rewrite is scoped to that
ISIN: the same ticker code under another security is left alone.

Rows from your other brokers are combined in the same way, and a row without an ISIN is matched by
its ticker alone. Two things can then go wrong, neither with an error:

- If another broker uses either ticker of a pair for a different security, the two securities are
    pooled as one holding.
- If cgt-calc cannot identify another broker's ticker, that row stays a holding of its own until you
    add the ticker to your
    [ISIN to ticker mapping](../extra-data-and-options.md#isin-to-ticker-translation).

Check each holding and disposal in the report against your records.

If one broker pays a fund's distribution in dollars and another pays it in pounds on the same day,
cgt-calc stops with **Cannot combine amounts in different currencies**. See
[Income reported in more than one currency](../extra-data-and-options.md#income-reported-in-more-than-one-currency).

Only confirmed pairs are rewritten. For any other pair:

- **cgt-calc stops** where your exports disagree about a security: an unrecognised second ticker for
    one ISIN, or one ticker used for two ISINs. It refuses rather than guess, because combining the
    wrong two holdings, or splitting one, changes the gain.
- **The security is kept as two holdings, with no error,** where the bundled list of securities, or
    a row you added to your ISIN to ticker mapping, gives it both tickers. Each holding has its own
    cost, so do not rely on the gains calculated for them. The summary may show only one of the
    tickers if you have sold the other, so check your exports for one ISIN with two different
    tickers.

In either case, please
[open an issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) with the ISIN and
both tickers.

### Dividends and foreign tax

On a dividend row, both the `Total` and the `Price / share` are after foreign tax, such as US tax on
a US share. cgt-calc records the dividend before that tax and the tax withheld as tax at source, as
it does for other brokers. The report's dividend income then includes the tax, and where cgt-calc
knows the double taxation treaty with the share's country, so do its treaty figures.

Tax already in your account's currency, such as US tax in a dollar account, is used as it is. When
the tax is given in the share's currency instead, cgt-calc converts it with the row's own figures:
the tax is the same share of the `Total` as it is of the number of shares times the price per share.
The dividend before tax less the tax is then the amount you received. For example, 10 shares at
$0.85 after tax, with $1.50 withheld and £6.80 received, are recorded as a £8.00 dividend and £1.20
of tax at source. The row's `Exchange rate` is not used.

cgt-calc applies treaty relief only when the tax matches the treaty rate to within a penny.
Otherwise, it prints a warning and leaves the treaty out of the report. That happens when the tax
was withheld at another rate, as on some depositary receipts. It can also happen with an older
export: those seen up to early 2024 round the price per share to cents, which makes the converted
tax slightly off on a dividend from many shares. If the export is old, replace it with a new export
of the same period, which gives the price in full. Otherwise, check the tax withheld in Trading
212's record of the dividend and work out the relief outside cgt-calc.

If the row's figures do not allow a conversion, for example because the number of shares is missing,
cgt-calc records the amount received and prints a warning naming the dividend. Add the tax withheld
to your dividend income yourself and, if you use
[`--income`](../usage.md#estimate-the-tax-from-your-income), to the figure you pass.

A row that takes back an earlier dividend is recorded at the amount taken back, with a warning. Its
tax is not converted, so the report keeps the original dividend's tax at source and any treaty
relief on it. The report's dividend income and tax at source are then both too high by the original
dividend's tax, and its treaty relief by the relief on the original dividend, which the PDF report
shows beside it. That relief is not more than the tax, apart from a penny of rounding, so the
taxable dividends are not understated. If you copy these figures from the report, take the original
dividend's tax out of the dividend income and the tax at source, and its relief out of the treaty
relief. The `--income` estimate includes the extra income too, which makes the estimate slightly
high.

### Currency conversions

A `Currency conversion` row moves cash between two currencies of your account. cgt-calc takes the
amount in `Currency conversion from amount` off the balance of that currency and adds the amount in
`Currency conversion to amount` to the balance of the other. The fee is the row's `Total`, charged
in the currency of that total, which is the currency you converted to in the exports examined.
cgt-calc does not treat the conversion as a disposal and reports no gain or loss on it.

### Known limitations

- Some tax withheld is left in the amount received and does not appear separately in the report: tax
    in pounds or pence, whatever your account's currency, tax with no currency given, and tax on
    fund interest distributions (`Dividend (Interest)`). Tax in pounds may be UK tax, for example on
    a property income distribution, which is not foreign tax. Add that tax to your income yourself
    and, if you use [`--income`](../usage.md#estimate-the-tax-from-your-income), to the figure you
    pass.
- cgt-calc does not use a dividend row's label to tell income from capital.
    `Dividend (Tax exempted)` is on rows where Trading 212 records no tax withheld, and the exports
    examined use it both for ordinary dividends and for repayments of share premium. cgt-calc
    records either as dividend income. Whether such a repayment is income or capital for UK tax
    depends on how the company made it under the law of its own country
    ([SAIM5210](https://www.gov.uk/hmrc-internal-manuals/savings-and-investment-manual/saim5210)),
    which the row does not show, so check the company's announcement of the payment. If it was
    capital, it is a capital distribution
    ([TCGA 1992 s122](https://www.legislation.gov.uk/ukpga/1992/12/section/122)) and not dividend
    income: take it out of the dividend income you report and work out the capital gains treatment
    yourself.
- The treaty applied to a dividend follows the first two letters of the share's ISIN, which do not
    always show where the company is based. A dividend on the New York shares of a Dutch company,
    for example, can be given the US treaty without a warning; see
    [Income reported in more than one currency](../extra-data-and-options.md#income-reported-in-more-than-one-currency).
- Share transfers between accounts or brokers, labelled `Transfer in` or `Transfer out`, are not
    supported, and cgt-calc stops at the row.
- In an export with no `Currency conversion from amount` and `Currency conversion to amount`
    columns, only the fee of a currency conversion is read, not the cash it moved. Export the period
    again: the exports examined have those columns whenever they hold a conversion. Gains and income
    are not affected, because the balance is used only for the balance check and the `Final balance`
    lines of the summary.
- One run covers one Trading 212 account. `--trading212-dir` takes a single directory and every file
    in it is read as one account, because nothing in the CSV identifies which account a row belongs
    to. If you hold the same security in two Trading 212 accounts, their exports cannot be combined
    in one run: a reorganisation of that security appears twice, and cgt-calc refuses rather than
    guess which half belongs to which account.
- A reorganisation cgt-calc cannot pair is refused while the CSV is read, which is before the
    calculator assembles the day. A RAW `STOCK_SPLIT` row, which overrides an unprovable
    reorganisation from any other input, therefore cannot override this one.
- The same reorganisation reported by two brokers cannot be told from two separate events, so
    cgt-calc refuses a day with more than one for the same security.
- A known ticker change is supported because cgt-calc applies its built-in
    [ticker renames](../extra-data-and-options.md#ticker-renames) before pairing. For example, `FB`
    on a row carrying Meta's ISIN is treated as `META`; an unknown ticker change is refused.
- A genuine ISIN change is always refused: if both halves state an ISIN and the values differ,
    cgt-calc will not pair them. Moving a holding between two different securities needs
    relationship data the export does not carry.
- A reorganisation of a very small holding can leave the ratio unrecoverable, because the exported
    counts are rounded. cgt-calc still calculates it exactly when your whole holding is the one the
    export describes; otherwise it stops rather than guess the ratio. Ratios beyond about 1,000 to 1
    are outside the range it searches, and one of those applied to a holding of around a
    hundred-thousandth of a share is a narrow edge case where the nearest ratio within range may be
    used instead of the event being reported as unrecoverable.
- A trade of the same security stamped between the two halves of a reorganisation is refused: there
    is no telling whether its count is in the units before it or after it. The same applies to a
    same-day trade from another input that carries no comparable time.
- A consolidation paid for partly in cash needs checking by hand. Where the cash is a return of
    value rather than income it is a capital distribution, and the event is a part disposal
    ([TCGA 1992 s122](https://www.legislation.gov.uk/ukpga/1992/12/section/122)) rather than a
    cost-preserving reorganisation. Trading 212 labels such a payment `Dividend` or an adjustment,
    which parses cleanly, so nothing here will notice.
- Share distributions, labelled `Stock distribution` or `Custom stock distribution`, are not
    supported, and cgt-calc stops at the row. In the exports examined, Trading 212 uses the label
    for a stock dividend, an issue of warrants, the new shares of a company separation and the
    shares received in a takeover. These are not all taxed in the same way, and the row does not say
    which it is. A distribution alongside a consolidation is usually a company separation, where the
    pooled cost has to be apportioned between the two resulting holdings by market value rather than
    preserved whole. Once you have established what the distribution was, and if one of the
    [RAW actions](raw.md#actions-to-use) records it, you can remove its row from a working copy of
    the export and enter the distribution in a separate file passed with `--raw-file`; see
    [Combining RAW with a broker export](raw.md#combining-raw-with-a-broker-export). No RAW action
    records a takeover paid in shares; see the next item.
- A takeover paid in shares is not supported. Trading 212 exports the shares you gave up as a
    `Market sell` with a price and a total of zero, and cgt-calc stops at a sale like that: read as
    a sale, the row would report the whole cost of those shares as a loss you did not make. The new
    shares arrive on a `Stock distribution` row or, for a takeover some years ago, on no row at all.
    cgt-calc cannot calculate those two holdings. To calculate the rest of the account, leave both
    out: in a working copy of the export, remove every row for the old shares and for the new ones
    except their dividends, not only the row cgt-calc stopped at. The report and any tax it
    estimates then leave out every gain and loss on those two holdings, including a later sale of
    the new shares: work the two holdings out by hand (consider professional advice) and add their
    gains and losses to the report's figures. The cash from the rows you remove is also missing from
    the balance cgt-calc keeps. If that takes the balance below zero, the run stops with
    `Reached a negative balance`; run it again with `--no-balance-check`, which turns the check off
    for every input in the run.
- The
    [export for a Trading 212 contract for difference account](https://helpcentre.trading212.com/hc/en-us/articles/36243765206301-How-to-export-the-trading-data-from-my-CFD-account)
    uses a different, record-based CSV format that this parser does not support.

Do not delete an unsupported transaction from the export to make the calculation run, unless you
record it in a RAW file as described above for share distributions, or leave both securities out as
described above for a takeover. The missing activity could make the resulting holdings and gains
incorrect.

## Troubleshooting

### `Unknown column(s)` or `Unknown action`

Trading 212 occasionally changes its export format. First, upgrade cgt-calc using the same method
you used to install it and try again. If the error remains, open a
[GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) containing:

- your cgt-calc version from `cgt-calc --version`;
- the complete error message;
- the CSV header for an unknown column; or
- a sanitised failing row for an unknown action.

Do not upload an unredacted account export: it can contain transaction IDs and other financial
information.

### A transaction is described differently by two exports

Two exports give the same Trading 212 transaction ID for what looks like the same transaction, at
the same second and for the same action, but they disagree about its details. That normally means
one export was taken after Trading 212 restated the transaction, for example after correcting a fee.

cgt-calc cannot tell which version is right, so it keeps both. Your totals may therefore count that
transaction twice. Re-export the overlapping period so that every file describes it the same way,
replace the older file, and run the calculation again.

### A same-second warning about identical transactions appears

cgt-calc found an unusually large group of otherwise identical transactions within one second. It
kept the correct number, so the totals are unchanged.

Check that the rows in the named files are genuine. If they are, please
[open an issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new); otherwise replace
the affected exports with a fresh one.

### No transactions are detected

Check that the directory contains CSV files directly, rather than inside another directory. The file
extension is matched case-insensitively, so `.csv`, `.CSV` and mixed-case variants all work. Also
check that you passed the directory itself to `--trading212-dir`, not the path to one CSV file.

### The balance or portfolio looks wrong

Re-export the history with all data categories selected. Check for a missing date range, files from
the wrong account, or an unsupported action listed above.

### `Tried to sell`

The history does not hold the shares being sold. Check first for a missing date range or a missing
file. If the shares came from a takeover paid in shares, the export may have no row for them: see
[Known limitations](#known-limitations), and do not add a purchase for them to make the calculation
run.

### A total does not match its shares and price

cgt-calc warns when a trade's `Total` differs from its number of shares times its price, after the
exchange rate and fees, by more than the rounding of those figures can explain. The warning names
the transaction and gives both totals. cgt-calc continues and uses the `Total` and the number of
shares in the row, not the price. Compare the row with the transaction in Trading 212. If the total
and the number of shares there are the ones in the row, the report is unaffected. If either differs,
export the period again, or correct it in a working copy of the export, and run the calculation
again.
