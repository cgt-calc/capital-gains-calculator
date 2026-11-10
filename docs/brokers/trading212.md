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

`--year 2024` means 6 April 2024 to 5 April 2025. Follow [Generate a report](../usage.md) to find
and check the output.

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
different exports, which is why the whole directory is read before they are paired. An older split
may have no rows at all: see
[A split with no rows in the export](#a-split-with-no-rows-in-the-export).

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

#### A split with no rows in the export

Trading 212 does not export `Stock split close` and `Stock split open` rows for every split. In the
exports examined, they first appear in December 2024, and an earlier split has neither them nor the
older single `Stock Split` row: NVIDIA's 10-for-1 split of 10 June 2024 is one. Your purchases
before the split are then counted in old shares and your sales after it in new ones. For the splits
listed below cgt-calc can stop instead, in the cases described there. Otherwise, it cannot tell that
the split is missing:

- A sale of more shares than the old count holds stops the run with
    [`Tried to sell`](#tried-to-sell).
- Any other sale goes through with no error. It takes too large a share of the cost, so the report
    understates the gain or shows a loss you did not make.
- A missing consolidation gives no error at all. Each sale takes too small a share of the cost, so
    the report overstates the gain.

cgt-calc knows the splits in this table, all made before December 2024. The date is the first day
the shares traded at the new count:

| Share                                     | Ticker  | First day at the new count | Split    | ISIN                         |
| ----------------------------------------- | ------- | -------------------------- | -------- | ---------------------------- |
| NVIDIA                                    | `NVDA`  | 20 July 2021               | 4-for-1  | US67066G1040                 |
| Amazon                                    | `AMZN`  | 6 June 2022                | 20-for-1 | US0231351067                 |
| DexCom                                    | `DXCM`  | 13 June 2022               | 4-for-1  | US2521311074                 |
| Shopify                                   | `SHOP`  | 29 June 2022               | 10-for-1 | CA82509L1076                 |
| Alphabet Class A                          | `GOOGL` | 18 July 2022               | 20-for-1 | US02079K3059                 |
| Alphabet Class C                          | `GOOG`  | 18 July 2022               | 20-for-1 | US02079K1079                 |
| Tesla                                     | `TSLA`  | 25 August 2022             | 3-for-1  | US88160R1014                 |
| Palo Alto Networks                        | `PANW`  | 14 September 2022          | 3-for-1  | US6974351057                 |
| Novo Nordisk (American depositary shares) | `NVO`   | 20 September 2023          | 2-for-1  | US6701002056                 |
| Walmart                                   | `WMT`   | 26 February 2024           | 3-for-1  | US9311421039                 |
| NVIDIA                                    | `NVDA`  | 10 June 2024               | 10-for-1 | US67066G1040                 |
| Chipotle Mexican Grill                    | `CMG`   | 26 June 2024               | 50-for-1 | US1696561059                 |
| Broadcom                                  | `AVGO`  | 15 July 2024               | 10-for-1 | US11135F1012                 |
| MicroStrategy (now Strategy)              | `MSTR`  | 8 August 2024              | 10-for-1 | US5949724083                 |
| Cintas                                    | `CTAS`  | 12 September 2024          | 4-for-1  | US1729081059                 |
| Deckers Outdoor                           | `DECK`  | 17 September 2024          | 6-for-1  | US2435371073                 |
| Super Micro Computer                      | `SMCI`  | 1 October 2024             | 10-for-1 | US86800U1043 or US86800U3023 |
| Lam Research                              | `LRCX`  | 3 October 2024             | 10-for-1 | US5128071082 or US5128073062 |

cgt-calc stops when your history holds the share going into that day, a Trading 212 row with one of
those ISINs buys or sells it on or after that day, and no split row dated that day gives the split's
ratio. With no split row on that day, the message begins with the ticker and **traded at a new
count**. With one that gives another ratio, it says the split **does not match** the split that day.
It does not check other splits, a share you hold across the split without trading it at Trading 212
afterwards, or rows without an ISIN, so the checks below still apply. Apple's and Tesla's splits of
August 2020 are not in the table: cgt-calc stops at those even with a split row, as
[Apple and Tesla in August 2020](#apple-and-tesla-in-august-2020) explains.

cgt-calc reads NVIDIA and Alphabet (`GOOG` and `GOOGL`) shares in a complete Schwab Equity Awards
export at the new count (see
[Share splits in complete exports](schwab.md#share-splits-in-complete-exports)), so it does not
check such a holding that the export has added shares to since the holding last ended a day empty.
That stays true after those award shares are sold, while other shares keep the holding above zero. A
split applies to the whole holding, and cgt-calc cannot restate only part of one: if such a holding
also has Trading 212 shares bought before the split, the result may be wrong without an error. Work
the split out by hand (consider professional advice). Do not add a split row for it: the row would
restate the award shares too, and cgt-calc would not stop.

Super Micro Computer's and Lam Research's ISINs changed with their splits. If your export lists the
share under the first ISIN in the table before the split and the second after it, cgt-calc stops
earlier, with `is exported for ISIN`. If the export's own split rows change the ISIN, it stops with
`changes the ISIN` while reading the export. A RAW row gets past neither. Do not calculate the two
ISINs in separate runs, as the first message suggests for two different securities: here they are
one holding, and splitting it would give the wrong cost. Work the holding out by hand (consider
professional advice).

To find one that gave no error, compare the final portfolio cgt-calc prints with your holdings in
the account you exported. The final portfolio is the position after the last row of your exports, so
the two can be compared when the exports run to today. A share with fewer shares in the portfolio
than you hold may have a missing split, and one with more a missing consolidation: check whether the
company has split or consolidated its shares since you first bought them.

Add the split yourself in a [RAW file](raw.md):

1. Find the first day the shares traded at the new count, from the table above or from the company's
    announcement of the split.
2. Work out how many shares you held at the start of that day: add up `No. of shares` for your
    purchases before it and take off your sales before it. If an earlier split of the share has no
    rows either, add a row for that one first and count the shares it added.
3. Work out how many shares the split added. Two shares through a 10-for-1 split become 20, so the
    split added 18. A consolidation takes shares away, so its number is negative.
4. Write one `STOCK_SPLIT` row with that date, the ticker and that number:

    ```csv
    date,action,symbol,quantity,price,fees,currency
    2024-06-10,STOCK_SPLIT,NVDA,18,0.00,0.00,USD
    ```

5. Pass the file along with the export. cgt-calc reads only one RAW file, so if you already pass
    one, add the row to that file instead.

    ```shell
    cgt-calc --year 2024 --trading212-dir trading212/ --raw-file splits.csv
    ```

When it checks one of the splits in the table, cgt-calc also checks that your row gives that split's
ratio. Otherwise, it cannot check the number you write. Either way, check it in two ways. Run it for
the tax year of the split and find the share reorganisation in the report: its ratio should be the
company's, 10 for the NVIDIA split in the example. Then compare the final portfolio with your
holdings again: if the count of the share still differs, look for another split with no rows. If you
hold the share at another broker as well, see
[Holdings spread across brokers](raw.md#holdings-spread-across-brokers).

If you also bought or sold the share on the day of the split, cgt-calc stops with
[`cannot be placed either side of it`](raw.md#cannot-be-placed-either-side-of-it), because the RAW
row has no time to compare with the time of the trade. Take the row out of the RAW file and add the
split to a working copy of the export instead, as a row with only these cells filled, each under its
column of the header:

- `Action`: `Stock Split`
- `Time`: a time on that day before your first trade at the new count, such as `2024-06-10 00:00:00`
- `Ticker`: the ticker
- `No. of shares`: the number of shares the split added
- `Currency (Total)`: the currency of your account, such as `GBP`

cgt-calc reads every trade after that time as made at the new count. It still stops at a trade
earlier that day; the entry linked above says what to do then.

#### A split row on another day or with another ratio

When cgt-calc stops for one of the splits in the table above, it names the share's split rows on
other days, or the split rows of that day when they do not give the split's ratio. It never moves or
corrects them. It does not name a row dated the day of another split of the share that it knows,
such as NVIDIA's of 20 July 2021 or Tesla's of 31 August 2020: that row records the other split. Do
not add a second split row while one of them may record the split: two rows apply it twice. What to
do depends on where the row is:

- **A `STOCK_SPLIT` row in your RAW file.** First check that it records this split and not another
    split of the same share: a row for that one belongs on its own day. Then change its date to the
    split day, or correct its number to the shares the split added to the whole holding. A RAW row
    has no time, so if you also traded the share on the split day, cgt-calc stops with
    [`cannot be placed either side of it`](raw.md#cannot-be-placed-either-side-of-it): take the row
    out of the RAW file and use a `Stock Split` row in a working copy of the export instead, as
    described above.
- **A `Stock Split` row you added to a working copy of the export.** Correct its number of shares,
    or move its `Time` to the split day before your first trade at the new count. Move it only if no
    trade of the share falls between its old time and the new one; otherwise work the split out by
    hand (consider professional advice).
- **The export's own split rows**, `Stock split close` and `Stock split open` or an older
    `Stock Split` row. Their times place the day's trades either side of the split, so do not change
    their date or time, and do not add a split row beside them. A `Stock Split` row beside them is
    refused. A RAW row beside them is used in their place only when they change the holding by no
    more than it does, so it is refused when they state too large a change, and it has no time, so a
    trade of the share that day is refused too. Check with Trading 212 which event the rows record.
    If they record this split, in a working copy of the export:

    1. Remove them from every file in the directory that has them. Overlapping exports can each hold
        a copy, and a copy left behind is still read.
    2. If they were dated the split day, add one `Stock Split` row at the time of the removed
        `Stock split open` row, or of the removed `Stock Split` row, stating how many shares the
        split added: exactly nine times your holding before it for a 10-for-1 split. Trades after
        that time are then read at the new count. A trade of the share earlier that day than the row
        is refused, because a single row does not show which count it was made at: work that day out
        by hand. This replaces both a pair that gives another ratio and a pair whose ratio cgt-calc
        cannot recover.
    3. If they were dated another day, add the split on the split day as for a split with no rows,
        but only if the export has no trade of the share between the removed rows and the start of
        the split day. A trade in between was made at the count Trading 212 had by then, which
        moving the split would change, so work it out by hand instead.

    If you hold the share at another broker as well, see
    [Holdings spread across brokers](raw.md#holdings-spread-across-brokers).

- **A split row from another broker's export**, such as Schwab's or Revolut's. The steps above are
    for Trading 212 files only. Do not add a split row beside it. Check the row against that
    broker's records. If it records this split and is dated another day, change its date to the
    split day in a working copy of that export, but only if no trade of the share falls between its
    date and the split day. If it gives another ratio, correct its number there. If a trade does
    fall in between, or the row's time and order cannot be kept, work the split out by hand
    (consider professional advice).

#### Apple and Tesla in August 2020

Apple's 4-for-1 split and Tesla's 5-for-1 split both took effect from 31 August 2020. At these two,
Trading 212 did not split fractions of a share: it closed the fractional part of each holding for
cash. cgt-calc does not know how an export shows that, so no split row can make the result right.
cgt-calc stops when your history holds Apple (ISIN US0378331005) or Tesla (US88160R1014) going into
31 August 2020 and a Trading 212 row with that ISIN buys or sells it on or after that day, even if
you have added a split row. The message begins with the ticker and **traded at a new count**, and
says that Trading 212 **closed any fraction of a share for cash**. A history that bought the share
only after the split, or sold all of it before, runs as usual. Tesla's 3-for-1 split of 25 August
2022 is checked like the others in the table above.

Work the holding out by hand (consider professional advice). To help cgt-calc support these splits,
open a [GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) that says
what your export shows:

- whether your rows from before 31 August 2020 are at the price per share before the split: on 28
    August 2020, Apple closed at about 500 USD and Tesla at about 2,200 USD;
- any split rows for the share, and their dates;
- how the fraction closed for cash appears, for example as a sale or a deposit, and its date.

Do not attach the export or post amounts or quantities: anyone can read an issue (see
[Reporting problems](../reporting-problems.md)).

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
[`--income`](../tax-estimates.md#estimate-the-tax-from-your-income), to the figure you pass.

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
    and, if you use [`--income`](../tax-estimates.md#estimate-the-tax-from-your-income), to the
    figure you pass.
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
- A trade of the same security stamped at or between the two halves of a reorganisation is refused:
    there is no telling whether its count is in the units before it or after it. The same applies to
    a same-day trade from another input that carries no comparable time.
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
    the export and enter the distribution in the RAW file you pass with `--raw-file`, or start one
    if you do not use one yet; see
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
information. For guidance on preparing a redacted example, see
[Reporting problems](../reporting-problems.md).

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
the wrong account, or an unsupported action listed above. A share with fewer shares in the final
portfolio than you hold may have a split the export has no rows for: see
[A split with no rows in the export](#a-split-with-no-rows-in-the-export).

### `Tried to sell`

The history does not hold the shares being sold. Check first for a missing date range or a missing
file.

If the company split its shares after you bought them, the export may have no rows for the split. A
sale of exactly ten times the holding in the message, for example, points to a 10-for-1 split. See
[A split with no rows in the export](#a-split-with-no-rows-in-the-export).

If the shares came from a takeover paid in shares, the export may have no row for them: see
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
