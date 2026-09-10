# Charles Schwab

cgt-calc reads the CSV export of a Charles Schwab brokerage account. It treats every value in the
export as US dollars and converts it to pounds using HMRC's monthly exchange rate for the month of
each transaction.

If you receive shares from an employer, you may also need an Equity Awards export to supply the
market price used for each employer-share acquisition. It can be a CSV or a JSON file. Read
[Equity awards](#equity-awards) before exporting it: Schwab currently produces more than one award
layout, and cgt-calc does not support all of them.

## Export the complete transaction history

Use the Schwab website rather than a positions export, statement, realised gain/loss report or PDF:

1. Open the transaction history for the taxable brokerage account.
2. Select the longest date range available. Start with the account's first transaction if possible,
    as explained in [Before you start](../usage.md#before-you-start). End today or at least 30 days
    after the end of the tax year you are calculating, because a purchase in the 30 days after a
    disposal can affect how that disposal is matched.
3. Include all transaction types. A trade-only export can omit deposits, dividends, tax, fees and
    corporate actions needed by the calculation.
4. Export the results as CSV.

The exact controls are behind the Schwab login and may change. Use the account's transaction-history
CSV whose columns include `Date`, `Action`, `Symbol`, `Description`, `Price`, `Quantity`,
`Fees & Comm` and `Amount`. You can compare it with the
[sanitised example](https://github.com/cgt-calc/capital-gains-calculator/blob/main/tests/schwab/data/schwab_transactions.csv).

Schwab may limit how much history can be exported at once. If your history needs several exports,
repeat the process with consecutive date ranges, making sure there are no gaps.

## Prepare the directory

One export goes to `--schwab-file`. Several go in a directory passed to `--schwab-dir`, so you do
not have to combine them yourself:

```text
schwab/
├── 2022-04-06_to_2023-04-05.csv
├── 2023-04-06_to_2024-04-05.csv
└── 2024-04-06_to_2025-04-05.csv
```

The base filenames do not matter. cgt-calc reads every CSV file directly inside the directory, but
it does not search subdirectories.

Two rules to get right:

- **Do not put an Equity Awards CSV in this directory.** It is also a `.csv`, so cgt-calc would read
    it as transaction history and stop with `Missing columns in Schwab transaction file`. Keep it
    elsewhere. Pass an award-price CSV with `--schwab-award-file`. For a complete transaction
    export, follow
    [Combining a main history with a complete export](#combining-a-main-history-with-a-complete-export).
- **Do not put exports from two different Schwab accounts in one directory.** The CSV does not say
    which account a row belongs to, so cgt-calc cannot separate them. Combining several accounts is
    not supported.

Overlaps are **not** safe here, unlike some other brokers. A Schwab CSV carries no transaction ID,
so cgt-calc cannot tell a row repeated by an overlap from a genuine repeat of the same trade: two
identical buys of the same size and price on one day are a real thing. If two exports have
overlapping transaction-date spans, cgt-calc refuses them rather than count both.

## Generate the report

For the 2025/26 tax year, run:

```shell
cgt-calc --year 2025 --schwab-file schwab_transactions.csv
```

`--year 2025` means 6 April 2025 to 5 April 2026. Follow [Generate and Review a Report](../usage.md)
to find and check the output.

If your history needed several exports, point `--schwab-dir` at the directory holding them instead:

```shell
cgt-calc --year 2025 --schwab-dir schwab/
```

`--schwab-file` and `--schwab-dir` cannot be used together.

If a `Stock Plan Activity` row has no price, also pass the supported Equity Awards export described
below. Either layout goes to the same option:

```shell
cgt-calc --year 2025 \
  --schwab-file schwab_transactions.csv \
  --schwab-award-file schwab_awards.csv

cgt-calc --year 2025 \
  --schwab-file schwab_transactions.csv \
  --schwab-award-file schwab_awards.json
```

## Recognised activity

The importer recognises these exact values from the main CSV's `Action` column:

| Exported action                                                      | How cgt-calc handles it                                                             |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `Buy`, `Sell`                                                        | A purchase or disposal, including `Fees & Comm`                                     |
| `Cancel Buy`                                                         | Removes the cancellation and the matching `Buy`                                     |
| `Stock Plan Activity`                                                | An employer-share acquisition at the exported or Equity Awards price; no cash moves |
| `Qualified Dividend`, `Cash Dividend`, `Qual Div Reinvest`           | Dividend income                                                                     |
| `Div Adjustment`, `Special Qual Div`, `Non-Qualified Div`            | Dividend income                                                                     |
| `NRA Tax Adj`, `NRA Withholding`, `NRA Withhold`, `Foreign Tax Paid` | Tax deducted from a dividend                                                        |
| `Credit Interest`, `Bond Interest`                                   | Interest income                                                                     |
| `Short Term Cap Gain`, `Long Term Cap Gain`                          | A fund distribution reported with dividend income                                   |
| `ADR Mgmt Fee`                                                       | A charge added to the holding's pooled cost and deducted from cash                  |
| `Adjustment`, `IRS Withhold Adj`, `Wire Funds Adj`                   | A cash-balance correction                                                           |
| `MoneyLink Transfer`, `MoneyLink Deposit`, `MoneyLink Adj`           | A cash movement only                                                                |
| `Wire Funds`, `Wire Sent`, `Wire Funds Received`, `Funds Received`   | A cash movement only                                                                |
| `Misc Cash Entry`, `Service Fee`, `Journal`, `Cash In Lieu`          | A cash movement only                                                                |
| `Visa Purchase`                                                      | A cash movement only                                                                |
| `Security Transfer`                                                  | An `ACH`-related cash movement; an `Amount` is required and no holding is moved     |
| `Reinvest Shares`                                                    | A purchase of the reinvested shares                                                 |
| `Reinvest Dividend`                                                  | Unsupported; the row is ignored and cgt-calc prints a warning                       |
| `Stock Split`                                                        | A share reorganisation: the pooled cost is unchanged and spread over the new count  |
| `Spin-off`                                                           | Adds the new holding and moves part of the old holding's pooled cost to it          |
| `Cash Merger` followed by `Cash Merger Adj`                          | A disposal for cash, built from the adjacent amount and quantity rows               |
| `Full Redemption Adj` followed by `Full Redemption`                  | A disposal for cash, built from the adjacent amount and quantity rows               |
| `Sell to Open`                                                       | Writing an equity option: a disposal of the option on the grant date                |
| `Buy to Close`                                                       | Closing a written option: an allowable cost of the original grant                   |
| `Expired`                                                            | A written option that lapsed; the grant keeps the premium as its gain               |
| `Assigned`                                                           | A written option that was assigned; the premium moves into the share transaction    |

An `NRA Tax Adj`, `NRA Withholding`, `NRA Withhold` or `Foreign Tax Paid` row is treated as tax on
account interest only when its `Symbol` is blank and its `Description` contains `SCHWAB1 INT`.
Otherwise, it is treated as dividend tax.

For `Reinvest Dividend`, check the Schwab statement and the finished report. Confirm that the
dividend income and reinvested purchase were recorded by other rows; do not assume the ignored row
duplicates them.

cgt-calc changes Schwab's old `FB` ticker to `META`, so transactions under both names share one
holding in the report.

An unknown action stops the import. Do not delete a financial transaction simply to make the run
finish; identify what happened and check [Known limitations](#known-limitations) first.

### Cancelled purchases

Schwab keeps both a cancelled purchase and its `Cancel Buy` row in the export. cgt-calc removes both
when their symbol, quantity and price match and the purchase is no more than five days before the
cancellation. An unmatched cancellation stops the import because leaving either row in the
calculation could create a purchase that never happened.

### Dates containing `as of`

Schwab can show a date such as `08/18/2023 as of 08/15/2023`. cgt-calc uses the first date,
`08/18/2023`, for the transaction. An Equity Awards export, CSV or JSON, can supply a market price
from the earlier date, but it does not change the transaction date.

That choice affects the tax year and the same-day and 30-day matching rules. Check any such row
carefully when the two dates cross 5 April or another purchase or disposal falls between them.

### Bonds and Treasury securities

When Schwab identifies a bond with a valid nine-character CUSIP, it may quote the price per $100 of
face value. cgt-calc converts that to a price per $1 and checks it against the quantity and total
amount. Accrued interest found through that check is included with the transaction costs.

If the total does not support the conversion, cgt-calc prints a warning and leaves the exported
price unchanged. Do not ignore that warning: compare the price, face value, cash amount and accrued
interest with the trade confirmation before relying on the result.

### Corporate actions

A `Stock Split` row states the change the event made to this account's holding: positive for the new
shares a split added, negative for the shares a consolidation removed. cgt-calc applies it as a
share reorganisation: nothing is bought or sold, and the pooled cost is unchanged and spread over
the new count ([TCGA 1992 s127](https://www.legislation.gov.uk/ukpga/1992/12/part/IV/chapter/II)).

Two situations are refused rather than guessed:

- If the same security also has units from another input, one account's change does not state the
    whole pooled holding's. Replace the Schwab row's effect with a RAW
    [`STOCK_SPLIT` row](raw.md#share-reorganisations) stating the change to all of them together;
    where a date has both, the RAW row is used and the Schwab row is ignored. See
    [`also has units from`](raw.md#also-has-units-from-on-a-stock_split-row).
- Because Schwab rows carry dates without times, a purchase, sale, gift or transfer of the same
    security on the day of the split cannot be placed before or after it, and the day is refused.
    See [`cannot be placed either side of it`](raw.md#cannot-be-placed-either-side-of-it).

For a `Spin-off`, cgt-calc needs to know the old holding from which the new shares came. It asks for
the old ticker during an interactive run and saves the answer in `out/spin_offs.csv`. For a
non-interactive run, add the required `dst,src` row first or choose another cache with
[`--spin-offs-file`](../extra-data-and-options.md#spin-off-source-mappings). It then uses the two
holdings' market values to divide the existing pooled cost. Check this result against the company's
reorganisation documents.

Cash mergers are supported only when the exported pair represents shares leaving in return for cash.
cgt-calc warns because a merger that also gives you replacement shares needs different treatment and
is not covered.

## Written equity options

The importer supports standard US equity options written with `Sell to Open`, including covered
calls. It reconciles these outcomes:

- **`Buy to Close`:** the closing debit is an allowable cost of the original option grant. The gain
    remains dated when the option was written.
- **`Expired`:** the premium, less the opening transaction costs, remains the gain on the grant.
- **`Assigned`:** a call premium is added to the proceeds from selling the underlying shares; a put
    premium reduces the acquisition cost of the shares bought on assignment. The usual share
    identification rules then apply.

This follows HMRC's guidance for investors in
[traded options (CG55536)](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg55536),
[closing out a written option (CG55545)](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg55545),
and
[exercise by the grantor (CG12313)](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg12313).
It assumes the activity is investment activity taxed under the capital gains rules rather than a
trade.

Both Schwab option-symbol formats are accepted, for example `META 09/20/2024 500.00 C` and
`META  240920C00500000`. Support assumes a standard contract delivering 100 shares.

Include the complete history from each `Sell to Open` through its close, expiry or assignment, even
where that crosses 5 April. A later close changes the gain on the original grant; a later assignment
replaces that option gain with the combined share transaction. Re-run earlier tax years when a
previously open option reaches either outcome, and amend a submitted return if necessary.

Schwab posts the underlying share transaction of an assignment on its own settlement date, which is
a business day or two later and can fall in the next tax year. cgt-calc dates the share transaction
on the assignment instead, and takes its amounts from the exported settlement row. That row has to
be in the export: the shares are never invented from the assignment alone, because the settlement
row turning up later would then dispose of the same shares twice. The import stops, naming the
transaction it looked for, when it is missing, when more than one exported row could be it, when the
only candidate does not reconcile with the strike, quantity and fees, or when one contract is
assigned more than once on a day, which the export gives no way to pair up with its share rows.

The money keeps the settlement date. During import, cgt-calc represents the exported share row as a
cash movement on the date Schwab paid or collected the money. This prevents the assignment from
showing a shortfall before settlement.

If a balance-check error includes the generated cash movement, its description is
`Settlement of the META put option expiring 2024-05-17 at a strike of 50 assigned on 2024-05-17`.

Purchased options (`Buy to Open`, `Sell to Close`, `Exercised`) are not supported. Neither are
cash-settled index options, nor adjusted contracts, whose root carries a numeric suffix such as
`AAPL1` and which no longer deliver 100 shares. The parser stops with an explicit error for those
rows instead of treating contracts as shares. Box spreads are not recognised as financing: their
legs are read as ordinary written and purchased options, and the purchased legs stop the import.

## Equity awards

Schwab exports your Equity Awards account in two different shapes, and cgt-calc uses them for
different things:

- a **price-only export**, which prices the vests already listed in your main transaction CSV and
    imports no transactions of its own. It comes as the award-price CSV, and as a JSON export whose
    every row is a `Lapse`.
- a **complete transaction export**, which is the award account's own history. It comes as JSON or
    as CSV, and cgt-calc reads both.

You do not choose which one Schwab gives you, so open the file and look rather than predicting it
from your share plan:

- **CSV:** check the heading. `FairMarketValuePrice` and no `VestFairMarketValue` means the
    award-price CSV. `VestFairMarketValue` and `PurchaseFairMarketValue` means the complete export.
- **JSON:** look at the `"Action"` of each transaction. Older exports spell the field `"action"`.
    Nothing but `"Lapse"` means the price-only export. A file holding `"Deposit"`, `"Sale"` or
    `"Dividend"` is the complete export.
- **Neither:** a JSON file holding `"Lapse"` together with cash rows such as `"Journal"` or
    `"Wire Transfer"` is rejected. cgt-calc reads vest prices only from an export whose every row is
    a `Lapse`, and those cash rows are discarded, so the file would import nothing and price
    nothing. Export the same history as the award-price CSV, which reads the vests and ignores the
    cash rows.

Both go to `--schwab-award-file`, which reads the contents and works out which one it has. What
changes is what cgt-calc does with the file, described in the two sections below.

### Price-only export

Use this method when the main transaction CSV contains `Stock Plan Activity` rows with a blank
`Price`:

1. In Schwab, open the Equity Awards account. Schwab's
    [grant guide](https://eac.schwab.com/content/how-to-accept-your-grant) shows how to reach the
    account from **Accounts → Equity Awards**.
2. Export its complete transaction history as CSV or as JSON.
3. Check the file as described above: the CSV heading contains `Date`, `Symbol` and
    `FairMarketValuePrice`, or every JSON transaction is a `Lapse`.
4. Pass it with `--schwab-award-file` alongside the main CSV.

The award-history export control is behind the Schwab login, so its wording may change. The file's
contents, rather than the button name, determine whether cgt-calc supports it.

This extra file does **not** import a second set of transactions. It supplies a missing market price
for a vest in the main CSV. cgt-calc looks for the same symbol on the activity date or one of the
previous six days, which covers awards dated around weekends and holidays.

Always pass it with `--schwab-file` or `--schwab-dir`. On its own it prices nothing: the JSON form
is refused, and the award-price CSV is accepted but produces an empty calculation.

The supported award-price CSV layout stores one activity across two CSV rows. Keep the file
unchanged. A
[sanitised example](https://github.com/cgt-calc/capital-gains-calculator/blob/main/tests/schwab/data/rsu_settlement/awards.csv)
shows the expected structure.

The two layouts are not otherwise interchangeable. The JSON reader refuses three things the CSV
reader does not check, and each of them stops the run with an error naming the file:

- a `Lapse` that states no market value, or one of zero or less;
- two grants vesting on the same day at different prices, because the pair does not say which figure
    is the acquisition cost;
- a symbol whose price could be stated in pre-split units, either because your main history holds a
    `Stock Split` for it or because cgt-calc's own split table records one on or after the earliest
    vest the file prices. The price comes from the award file and the number of shares from the main
    history, and neither says whether the split restated both.

If the JSON export holds anything besides lapses, such as a `Journal` row moving cash out, cgt-calc
will not read prices from it. Export the same history as the award-price CSV, which reads the vests
and ignores the cash rows.

#### What cgt-calc does not check

Nothing verifies that the two files come from linked accounts, that either is complete, or that the
quantities agree. The price lookup takes the nearest award date at or before the activity date,
within six days. Finding a price that way is not evidence that the two rows describe the same vest.

A split cgt-calc has not been told about, and that your main history does not record, cannot be
detected at all. A price and a quantity in different units produce a wrong acquisition cost rather
than an error.

You can check the quantities yourself, but the two files do not identify each other, so start from
your vest or settlement confirmations rather than from the dates:

1. From the confirmations, list the grants that vested and the shares each one delivered to the
    brokerage account.
2. Find the award rows for those grants and add up their `NetSharesDeposited`, not their gross
    `Quantity`, which includes the shares withheld for tax.
3. Find the `Stock Plan Activity` rows that delivered them and add up their quantities. The two
    totals should agree.
4. Check the `FairMarketValuePrice` on those award rows against the price on the confirmations.

Work per grant rather than per date. One award date can carry several grants, one delivery can be
split over more than one posting date, and a posting date can carry shares from more than one award
date, so a posting-date total need not equal an award-date total. Investigate anything that does not
line up rather than assuming the nearest date is right: two awards a few days apart with the same
net quantity but different prices both satisfy the lookup, and only one of them is your vest.

### Complete transaction export

The complete export is the award account's own history rather than a price list, so it is imported
on its own:

```shell
cgt-calc --year 2025 --schwab-award-file schwab_awards.json
cgt-calc --year 2025 --schwab-award-file schwab_awards.csv
```

cgt-calc identifies the format from the file's contents, so both layouts produce the same result.

The complete CSV states one transaction per row, followed by a row for each lot or grant with the
transaction columns left blank. Keep the file exactly as Schwab exported it: cgt-calc reads those
blank columns as the marker that a row continues the transaction above it.

Use either layout only for a transaction history Schwab exported. Open the file first and confirm
its rows are dated transactions with actions such as `Deposit`, `Sale` or `Dividend`, rather than a
summary of your holdings or outstanding grants. Do not import the same vest, sale, dividend or cash
movement again through `--schwab-file`. cgt-calc does not yet reconcile a main history against a
complete award history, so anything present in both is counted twice. Passing a complete export with
`--schwab-award-file` alongside `--schwab-file` or `--schwab-dir` is refused for that reason.

The complete importer supports restricted-stock vests, ESPP purchases, sales, forced quick sales,
dividends, dividend tax and forced cash disbursements. It recognises gifts but stops so that you can
classify the recipient, as explained below. It skips `Lapse` rows because they repeat the shares
already recorded by the related vest. A price-only export has no such vest row, which is why its
lapses are read for their prices instead. For an ESPP purchase it uses the market value on the
purchase date as the acquisition cost, on the assumption that the discount was taxed as employment
income. Check that assumption against your payroll and award records.

#### Combining a main history with a complete export

If you need a main history and a complete award export in one run, the older
`--schwab-equity-award-json` still accepts that combination and keeps working:

```shell
cgt-calc --year 2025 --schwab-file schwab_transactions.csv \
    --schwab-equity-award-json schwab_awards.json
```

Nothing checks the two files against each other, so make sure no transaction appears in both. That
option only adds transactions; it does not price vests. The main history therefore has to import on
its own, and a `Stock Plan Activity` it cannot price still needs the award-price CSV passed with
`--schwab-award-file` as well.

#### Share splits in complete exports

Schwab has sometimes updated old award records inconsistently after a share split. The complete
importer contains specific handling for Alphabet (`GOOG` and `GOOGL`) and NVIDIA (`NVDA`). For any
other ticker it prints a warning because it does not know how Schwab changed the old quantities.

After a new split, download the complete history again and check every pre-split acquisition and
disposal quantity against a statement. A stale export can mix old and new units without changing the
cash totals, so the balance check cannot detect the error.

If cgt-calc warns that a disposal is cheaper by about the split multiplier, compare the named
disposal's quantity and proceeds with a statement. The warning can mean that Schwab already adjusted
the quantity for the split, but a real fall in the share price can look the same.

#### Gifts in a complete export

A `Gift` row says that shares left the account but not who received them. cgt-calc therefore stops
and asks you to classify it:

- For a spouse or civil partner, follow
    [Transfers to a spouse or civil partner](raw.md#transfers-to-a-spouse-or-civil-partner) and add
    the suggested `TRANSFER_TO_SPOUSE` row through a small RAW file.
- For anyone else, follow [Gifts to anyone else](raw.md#gifts-to-anyone-else) and add the suggested
    `GIFT` or `GIFT_UNCONNECTED` row using a verified market value.

The error prints the complete RAW line to copy into a small CSV. The complete importer can restate
an old gift for a later split, so the quantity may not match the number of shares shown on the gift
date. Where both readings are possible, the error prints two candidate lines: check a statement and
use the one with the correct quantity.

## Known limitations

- Transfers of shares are not reconstructed from the main CSV. In particular, `Journaled Shares` is
    unsupported. Despite its name, `Security Transfer` is supported only as an `ACH`-related cash
    movement: it requires an `Amount` and does not move shares. Follow the RAW transfer or gift
    instructions only after establishing what the transfer was and what cost should move with it.
- A main history and a complete Equity Awards export are not reconciled against each other. Import
    one or the other, or make sure no vest, sale, dividend or cash movement appears in both.
- A cash merger that gives replacement shares as well as cash is not supported. The cash-only
    importer prints a warning for every merger so you remember to check this.
- All values in Schwab CSV and JSON files are treated as USD. Changing a currency symbol in the CSV
    does not convert the data.
- cgt-calc does not remove duplicates from Schwab exports, because the CSV has no transaction ID to
    tell a duplicate from a genuine repeat of the same trade. Every transaction must appear exactly
    once. `--schwab-dir` refuses exports whose transaction-date spans overlap rather than count
    both.
- Exports from more than one Schwab account cannot be combined. Nothing in the CSV identifies the
    account.

## Troubleshooting

### `Cannot price a vest`

The named `Stock Plan Activity` has no price in the main CSV. Export the Equity Awards history and
pass it with `--schwab-award-file`. Either layout of the price-only export will do: the award-price
CSV, or the JSON export whose every row is a `Lapse`. If you already did, check that it contains the
same ticker and a `FairMarketValuePrice` dated no more than six days before the activity.

If its JSON holds `Lapse` rows together with cash rows such as `Journal` or `Wire Transfer`,
cgt-calc will not read prices from it. Export the same history as the award-price CSV, which reads
the vests and ignores the cash rows.

If the file instead uses the `VestFairMarketValue` layout, or its JSON holds `Deposit`, `Sale` or
`Dividend` rows, it is the complete transaction export, and cgt-calc cannot use it to price a vest
in your main history. Which way round to work depends on what each file holds:

- If the complete export holds everything you need for the year, including any purchases, sales,
    dividends and cash movements, pass it with `--schwab-award-file` on its own and leave out
    `--schwab-file`. Check first: anything that happened only in the main account is not in that
    export and would be left out of the calculation.
- If the main history holds everything you need, ask Schwab for the award-price CSV as well and pass
    the two together: the main history with `--schwab-file`, the award-price CSV with
    `--schwab-award-file`.

If neither file holds everything, cgt-calc cannot reconcile the two for you, but
[Combining a main history with a complete export](#combining-a-main-history-with-a-complete-export)
describes the one route that still works and what you have to check yourself. Do not rename columns
or invent a price merely to bypass the error.

### `Missing columns` or a row/column-count error

Make sure `--schwab-file` points to the main transaction-history CSV. A positions export, realised
gain/loss report, statement or spreadsheet converted from PDF has a different layout.

`--schwab-award-file` reports a file it does not recognise differently, saying what each supported
export looks like: “This is not a Schwab Equity Awards export cgt-calc reads.” Check the file's
heading against that message. The award-price CSV holds `Date`, `Symbol` and `FairMarketValuePrice`;
the complete CSV holds `VestFairMarketValue` and `PurchaseFairMarketValue`; the JSON export starts
with `{`. Whichever of the three it is, it goes to `--schwab-award-file` rather than
`--schwab-file`.

With `--schwab-dir`, this error names the offending file: take it out of the directory. If it is the
award-price CSV, pass it with `--schwab-award-file` instead. If it is a complete transaction export,
`--schwab-award-file` will not take it alongside `--schwab-dir`: see
[Combining a main history with a complete export](#combining-a-main-history-with-a-complete-export).

If you combined several history ranges, confirm that there is one header, every data row has the
same number of fields, and none of the source files used a different export format.

### `is not a regular file` or `could not be read`

With `--schwab-dir`, every entry matching `*.csv` must be a plain, readable file. Remove or move out
a subdirectory, broken symlink, or file the current user lacks permission to read.

### `Unknown action`

Compare the action named in the error with [Recognised activity](#recognised-activity). Common
unsupported examples include `Journaled Shares`. Do not remove the row until you know whether it
represents a trade, transfer or corporate action that must be recorded another way.

First upgrade cgt-calc using the same method you used to install it. If an unchanged export still
contains an unsupported action, open a
[GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) with the complete
error and a sanitised copy of the row.

### An unmatched `Cancel Buy`

Re-export without transaction filters and make sure the date range includes the original purchase.
The matching `Buy` must have the same ticker, quantity and price and be no more than five days
before the cancellation. Check the pair against Schwab before following the row-removal instruction
in the error.

### A cash merger or full redemption pair is rejected

Use the unchanged export. The two rows must be adjacent and have the same date, symbol and
description. One supplies the proceeds and the other the negative quantity. A changed order, extra
value or replacement shares means the event is not the supported cash-only shape.

### `The lapse on ... reports ... shares`

This complete-export error means the share count on a `Lapse` row disagrees with the related
deposited and withheld counts after applying the split history currently known to cgt-calc. Download
a fresh complete export, then compare the named lapse, its vest and the split history with the
original award statement. cgt-calc stops rather than applying a split factor that may be wrong. If a
fresh export still fails, cgt-calc may not yet know about a recent split; open a
[GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) with the error and a
sanitised copy of the relevant records.

### `ESPP purchase on ... bought ... shares but accounts for ...`

This complete-export error means the purchased share count does not equal the shares deposited,
withheld and sold for tax after any known split. Compare those figures with the ESPP confirmation
and split history. Do not change a quantity merely to make the import continue.

### `is not a date cgt-calc reads` or `states no ...Date`

Equity Awards exports write every date as MM/DD/YYYY, and cgt-calc reads only that form. The error
names the transaction and the field, spelled as your export spells it: `Date` for a transaction's
own date, and `VestDate` or `PurchaseDate` for one inside a vest or an ESPP purchase. Where the
export states a value, the error quotes it so that you can search the file for it.

Re-export from Schwab and use the file unchanged. If you need to look inside it, open it in a text
editor rather than a spreadsheet: a spreadsheet can reinterpret a date column according to its own
settings, so `06/12/2023` may be read as 6 December where Schwab means 12 June, and written back in
a form cgt-calc does not read.

This check catches only a date it cannot read at all. A swapped day and month that is still a valid
MM/DD/YYYY date passes it: `12/06/2023` is read as 6 December whatever it was meant to be. That is
why working from an unedited export matters more than correcting dates by hand.

If an unchanged export still fails, open a
[GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) with the complete
error and a sanitised copy of the row.

### An export overlaps another one

Two files in the `--schwab-dir` directory have earliest-to-latest transaction-date spans that
overlap. They need not share any individual date: one file running from March to December and
another holding a single June transaction is enough, because cgt-calc cannot then rule out that the
two exports cover the same period. Because a Schwab CSV has no transaction ID, it cannot tell which
rows are repeats, so it refuses rather than count them twice. Re-export the ranges so they do not
overlap, or delete the redundant file.

If the two files are exports from different Schwab accounts, that is the cause: cgt-calc cannot
combine accounts, because no column says which account a row came from.

### `Reached a negative balance` or `Tried to sell not owned symbol`

Check that the export reaches the deposits and purchases that funded or created the later activity.
Also check for a missing award vest, unsupported share transfer, gap between downloaded ranges or a
transaction imported twice.

Do not add made-up cash or use `--no-balance-check` simply to silence the error. Use that option
only after you understand why the Schwab history cannot reconcile and have checked its completeness
another way.

## Check the finished report

Compare “Portfolio at the end of … tax year” with the Schwab account record for 5 April. Check each
disposal quantity and proceeds, every employer-share vest and market value, dividends and tax,
interest, bond face values, split-adjusted quantities and corporate-action warnings.

Do not upload an unredacted Schwab export to GitHub. It contains account activity, holdings, award
identifiers and other sensitive financial information.
