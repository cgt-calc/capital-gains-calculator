# RAW Format

cgt-calc reads a simple seven-column CSV when your broker is not supported or when you need to add a
supported event that its export does not identify. You write this file yourself from the broker's
records; it is not the CSV that a broker supplies.

## Create the CSV

Include this header exactly, in this order:

```csv
date,action,symbol,quantity,price,fees,currency
```

The columns are:

| Column     | Value                                                                                                                             |
| ---------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `date`     | Transaction date in `YYYY-MM-DD` format                                                                                           |
| `action`   | One of the documented [actions](#actions-to-use); write the name in uppercase                                                     |
| `symbol`   | Instrument ticker; leave blank only where the action table allows it                                                              |
| `quantity` | Number of shares or units, positive except where the action table says otherwise; use `1` when `price` holds the full cash amount |
| `price`    | Price per unit, or the full cash amount (positive or negative) when `quantity` is `1`                                             |
| `fees`     | Positive fees deducted from the cash amount; leave blank or use `0` when there are none                                           |
| `currency` | Three-letter currency code for the price, fees and resulting amount, such as `USD`                                                |

The header is required. cgt-calc can infer it from a file without a header for compatibility, but
warns because it then has to assume that the columns are in the order above.

Here is a complete small example:

```csv
date,action,symbol,quantity,price,fees,currency
2024-01-02,TRANSFER,,1,1000.00,0.00,GBP
2024-01-03,BUY,ACME,100,7.50,5.00,GBP
2024-06-01,DIVIDEND,ACME,100,0.04,0.00,GBP
2025-01-03,SELL,ACME,25,8.20,5.00,GBP
```

You can also compare the format with the
[tested example](https://github.com/cgt-calc/capital-gains-calculator/blob/main/tests/raw/data/test_data_2.csv).

### How the cash amount is calculated

The format has no separate `amount` column. For every action except `BUY`, cgt-calc calculates:

```text
amount = quantity × price − fees
```

For `BUY`, it makes `quantity × price` negative first and then subtracts the fees. Enter positive
quantities, positive unit prices and positive fees for ordinary purchases and disposals; cgt-calc
therefore records the total cost of a purchase as negative and the net proceeds of a disposal as
positive.

For a cash-only row, use `quantity` `1` and put the full amount in `price`: positive for money
received and negative for money paid. A £250 deposit and a £40 withdrawal look like this:

```csv
2024-02-05,TRANSFER,,1,250.00,0.00,GBP
2024-03-14,TRANSFER,,1,-40.00,0.00,GBP
```

Do not leave `quantity` or `price` blank. On a row that moves cash, such as a trade, dividend,
deposit or withdrawal, cgt-calc then has no amount to apply and stops with `Amount missing`.

If a `STOCK_ACTIVITY` row has no `price`, cgt-calc looks it up by ticker and date in the
[share prices](../extra-data-and-options.md#missing-share-prices) and reads it in the row's
`currency`. If there is none, the run stops with `No share price`: enter the market value per unit
in `price`.

## Actions to use

Use the following action names when writing a RAW file. The cgt-calc source code contains more
action names for broker imports, but a RAW row using one may be ignored or fail because it lacks the
extra details that action needs.

| Action                           | What to enter                                                                                                                                                                      |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BUY`                            | Ticker, positive quantity and positive unit price. `fees` increases the cash cost and allowable acquisition cost.                                                                  |
| `SELL`                           | Ticker, positive quantity and positive unit price. `fees` reduces the cash proceeds and gain.                                                                                      |
| `STOCK_ACTIVITY`                 | Shares acquired without paying cash, such as a vest: ticker, quantity and market value per unit.                                                                                   |
| `DIVIDEND`                       | Ticker and gross dividend. Use the actual shares and dividend per share, or use quantity `1` and the gross total as `price`.                                                       |
| `DIVIDEND_TAX`                   | Ticker, quantity `1` and the tax deducted as a negative `price`.                                                                                                                   |
| `CAPITAL_GAIN`                   | Identical to `DIVIDEND` in every calculation; it exists only for broker exports. Write `DIVIDEND` instead.                                                                         |
| `INTEREST`                       | Interest received: leave `symbol` blank, use quantity `1` and the gross total as a positive `price`.                                                                               |
| `INTEREST_TAX`                   | Tax deducted from interest: leave `symbol` blank, use quantity `1` and the deduction as a negative `price`.                                                                        |
| `TRANSFER`                       | Cash added or removed: leave `symbol` blank, use quantity `1`, and use a positive `price` for a deposit or a negative one for a withdrawal.                                        |
| `ADJUSTMENT`                     | A cash-only correction or charge: write it like `TRANSFER`. It affects the balance but no holding or taxable income.                                                               |
| `FEE`                            | A cost that should increase one holding's pooled cost: enter its ticker, quantity `1`, the charge as a negative `price`, and `fees` `0`.                                           |
| `CASH_MERGER`, `FULL_REDEMPTION` | A disposal for cash: write it like `SELL`. Use only when no replacement shares were received.                                                                                      |
| `STOCK_SPLIT`                    | Ticker, the change a reorganisation made to your entire pooled holding, negative for a consolidation, price `0` and fees `0`; see [Share reorganisations](#share-reorganisations). |
| `SPIN_OFF`                       | New ticker, quantity received, price `0` and fees `0`; cgt-calc works the rest out itself, as described below.                                                                     |
| `TRANSFER_TO_SPOUSE`             | See [Transfers to a spouse or civil partner](#transfers-to-a-spouse-or-civil-partner).                                                                                             |
| `TRANSFER_FROM_SPOUSE`           | See [If you received the shares](#if-you-received-the-shares).                                                                                                                     |
| `GIFT`, `GIFT_UNCONNECTED`       | See [Gifts to anyone else](#gifts-to-anyone-else).                                                                                                                                 |

For a reinvested dividend, write two rows in this order: a `DIVIDEND` for the income, then a `BUY`
for the shares bought with it. Do not use the internal `REINVEST_DIVIDENDS` action: the calculator
ignores it. Excess Reported Income uses the separate
[ERI_RAW format](../custom-eri-data.md#eri_raw-format), not this file.

A `FEE` adds the charge to that holding's cost, which reduces a later gain, so use it only where the
charge is genuinely part of what that holding cost you rather than a charge for running the account.
If you are unsure, use `ADJUSTMENT`: it takes the money out of the balance without touching any
cost, which is the safer way to be wrong.

For a `SPIN_OFF`, cgt-calc needs to know which holding the new shares came from. It looks the new
ticker up in [`--spin-offs-file`](../extra-data-and-options.md#spin-off-source-mappings), asks you
when it is not there, and saves your answer to that file so it only asks once; a run that cannot
ask, such as one in a script, stops and tells you how to supply the row yourself. It then looks up a
closing price for the new and the old ticker on that date to divide the old holding's pooled cost
between them, so a price for both has to be available: from Yahoo Finance, or given for both in a
file passed with [`--prices-file`](../extra-data-and-options.md#missing-share-prices).

Date the `SPIN_OFF` row on the first day both shares trade normally on their own. The company or
exchange usually announces it as the ex-date, and it can be a day after the distribution or payment
date. HMRC divides the cost by the two holdings' values on that day
([CG52002](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg52002)); before it, the
old shares' price still includes the new ones. For example, GE paid out GE HealthCare shares on 3
January 2023, but normal trading in them began on 4 January, so the row belongs on 4 January. If you
also bought or sold the old shares that day, cgt-calc stops, because it cannot tell whether the
trade came before or after the spin-off.

## Known limitations

- The format has no ISIN or source-country column. For a dividend with withholding tax, when
    cgt-calc cannot match the ticker to a known ISIN, it guesses the source country from the
    currency when possible: `USD` is treated as US and `PLN` as Poland. An investment domiciled
    elsewhere can consequently receive the wrong treaty treatment without a source-country warning;
    that warning is issued only when withholding tax was deducted and cgt-calc cannot infer a
    country from the currency. Verify foreign dividend income and tax against the broker statement
    rather than assuming that treaty limits were applied.
- The format does not identify the instrument type. It is intended for ordinary shares and funds; do
    not rely on it for options, futures, bonds, contracts for difference, crypto assets or another
    instrument whose UK tax treatment differs.
- The seven columns cannot describe every internal action. Ticker renames and cancelled purchases
    are examples that a RAW row cannot express; use only the documented actions above.

## Combining RAW with a broker export

Every RAW row is assigned to a separate broker called `Unknown`. A complete history converted to RAW
can therefore reconcile its own cash. Its cash balance and interest stay separate from a named
broker export passed with another flag. In particular, a RAW `DIVIDEND_TAX` cannot be attached to
that broker's dividend, and a RAW deposit cannot fund a purchase in that broker's balance. Dividends
themselves are pooled by ticker and date across every broker, so a payment entered here that is
already in the export is added to it rather than listed separately.

Non-cash rows such as a spouse transfer can be supplied alongside a broker export as described
below. For any replacement or correction, make sure the original activity is not also imported, and
check both the final holdings and each broker balance. Keep the downloaded export unchanged for your
records; make changes only in a working copy.

## Generate the report

For the tax year 2024/25, run:

```shell
cgt-calc --year 2024 --raw-file raw_data.csv
```

`--year 2024` means 6 April 2024 to 5 April 2025. Follow [Generate and Review a Report](../usage.md)
to find and check the output.

Include cash deposits and withdrawals if your records contain them so the balance check can detect
an incomplete conversion. If the source genuinely has no cash history, use `--no-balance-check` only
after checking that every acquisition, disposal, income payment, fee and corporate action is
present.

## The order of rows on one date

The `date` column carries no time, so within a single day the order you write the rows in is the
only record of what happened first. Write each day's rows in the order the transactions actually
happened, and give every `quantity` and `price` in the units that were in force at that moment.

This matters most on the day of a share split, because the change a `STOCK_SPLIT` row states (see
[Share reorganisations](#share-reorganisations)) depends on how many shares you held when it ran.
Say you held 11 shares and sold 5 that morning. Six shares went into a 20-for-1 split and became
120, so the split created 114, and the sale above it is written in pre-split shares at the pre-split
price:

```csv
2022-06-06,SELL,AMZN,5,2400.00,0.00,USD
2022-06-06,STOCK_SPLIT,AMZN,114,0.00,0.00,USD
```

Had you sold after the split instead, all 11 shares went into it and became 220, so the split
created 209, and the sale below it is written in post-split shares at the post-split price:

```csv
2022-06-06,STOCK_SPLIT,AMZN,209,0.00,0.00,USD
2022-06-06,SELL,AMZN,100,120.00,0.00,USD
```

!!! warning "Same-day chronology across inputs"

    Within one RAW file the row order settles which units a same-day trade is stated in. Between
    two inputs it does not, and neither does the order cgt-calc merges them in, so a same-day trade
    from another input is refused unless both sides carry times. Put the day's rows for that symbol
    in one input, or work that day out by hand.

cgt-calc has no way to check the order you wrote. A sale placed on the wrong side of a `STOCK_SPLIT`
row is refused when its quantity only makes sense on the other side, but a quantity that is
plausible in both unit systems computes without complaint and gives wrong figures. After a run,
check the report's reorganisation entry: it states the unit counts either side of the event, so a
sale that landed in the wrong units shows up as a count you do not recognise.

This is about the RAW file you write yourself. cgt-calc assumes nothing about the order of the rows
inside a broker's own export, so if you keep a small RAW file alongside a broker export, it is the
RAW rows that need to be in the order things happened.

## Share reorganisations

A stock split or a share consolidation restates a holding: the old shares are not disposed of and
the new ones are not acquired, so no gain or loss arises and the pooled cost is unchanged
([TCGA 1992 s127](https://www.legislation.gov.uk/ukpga/1992/12/part/IV/chapter/II),
[CG51805](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51805)). Only the number
of units changes, so a later disposal takes a different share of the same cost.

The `quantity` is the **change** the event made to your holding, not the total you hold afterwards.
Your broker's statement usually gives the total, so subtract what you held: 11 shares through a
20-for-1 split become 220, and the row says `209`.

```csv
2022-06-06,STOCK_SPLIT,AMZN,209,0.00,0.00,USD
```

Writing the total instead is not something cgt-calc can catch: `220` reads as a plausible 21-for-1
split of the same 11 shares, and every later figure for the holding comes out wrong.

A consolidation shrinks the holding, so its `quantity` is negative: 100 shares consolidated
100-for-1 lose 99.

```csv
2026-02-02,STOCK_SPLIT,RKT,-99,0.00,0.00,GBP
```

Leave `price` and `fees` at `0`: nothing is bought, sold or paid for. cgt-calc refuses a
`STOCK_SPLIT` row that states either, rather than dropping the figure without telling you. A
reorganisation row in a broker export is held to the same rule, and its amount column counts too:
some exports state an amount with no price beside it, and that money is refused rather than dropped.

### Cash in lieu of a fractional entitlement

A consolidation rarely divides evenly. cgt-calc keeps whatever fraction of a unit the arithmetic
leaves, which is right only if you still hold it. Where the registrar sold the fraction and paid you
the cash instead, the payment is consideration under
[TCGA 1992 s128(3)](https://www.legislation.gov.uk/ukpga/1992/12/section/128): a part disposal of
the holding, whose cost is apportioned under s129 rather than identified against acquisitions the
way a sale of shares is
([CG51875](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51875)). Where the
payment is small, [s122(2)](https://www.legislation.gov.uk/ukpga/1992/12/section/122) can instead
treat it as no disposal at all and deduct the payment from the holding's pooled cost. HMRC generally
accepts a payment as small when it is no more than 5% of the holding's value or no more than £3,000
([CG57835](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg57835),
[CG57800](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg57800)).

cgt-calc does not model either treatment explicitly. For a reorganisation into one class, HMRC
permits the s129 cost to be apportioned by the number of shares sold
([CG51892](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg51892)), and a `SELL` row
for the fraction and payment gives that result if no shares of the same class are acquired that day
or in the following 30 days. If there is such an acquisition, ordinary share identification matches
the `SELL` against it and uses its cost instead, which is wrong for s128(3). If you cannot establish
that this condition holds, record no row for the payment. Without a row, the pool keeps both the
sold fraction and cost that should have been removed or reduced, so correct its quantity, cost and
later figures by hand (consider professional advice).

### Holdings spread across brokers

If you hold the same security through more than one broker, add up their holdings before working out
the number, which is the change to all of them together. A broker's own export states the change to
its own account, and adding that to a pool built from several would restate the holding by a ratio
that was never the corporate one. cgt-calc refuses a broker's single-row split when the pool has
units from another source, and says so.

A RAW `STOCK_SPLIT` row is the answer to that refusal, and you do not have to edit the broker export
to use it: where a date has both, the RAW row states the change to the whole holding, so cgt-calc
uses it and ignores the broker's own row for the same event.

Which accounts a holding is built from is remembered until a day closes with the whole pooled
holding at zero, and a ticker rename carries the record to the new name along with the holding. So
after one account sells all of its units, a single-row split reported by the other is still refused
until that happens: cgt-calc does not track which account each remaining unit came from, so it will
not assume they are all at the reporting one. The RAW row above is the answer there too.

## Transfers to a spouse or civil partner

Shares given to a spouse or civil partner usually move at **no gain / no loss**: nothing is taxable
for you, and they inherit your base cost
([CG22200](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg22200)). Record them with
the `TRANSFER_TO_SPOUSE` action.

```csv
2024-03-16,TRANSFER_TO_SPOUSE,META,21.5,0.00,0.00,USD
```

Leave `price` at `0`, since a gift has no sale price. Put any fee you paid to make the transfer in
`fees` — it is added to the base cost the recipient inherits.

No broker export marks these, so you add the row yourself. You do not have to convert your whole
history to the RAW format to do it: pass a small RAW file of just the transfers alongside your
broker export.

```shell
cgt-calc --year 2024 --schwab-file transactions.csv --raw-file transfers.csv
```

The report and the PDF show the base cost that passes to the recipient, and the text report prints
the exact RAW row to give them.

### If you received the shares

Record them arriving with `TRANSFER_FROM_SPOUSE`, with the base cost per unit as the `price`. The
transferor's report prints the row ready to use. The shares enter your Section 104 pool at that
cost, acquired on the transfer date, and no money is added to your cash balance.

```csv
2024-03-16,TRANSFER_FROM_SPOUSE,META,21.5,95.60,0.00,GBP
```

Your broker may show the shares arriving, but it does not know what they cost, so leave that row out
of your export rather than let it be read as a purchase at nothing.

!!! warning "Check that you qualify"

    No gain / no loss applies if you were living together at some point in the tax year of the
    transfer, and since 6 April 2023 for a period after separating as well — see
    [CG22420](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg22420). If you do not
    qualify, or the shares went to anyone else, record a [gift](#gifts-to-anyone-else) instead.

Buying the same shares within 30 days of a transfer changes the figure. The transfer is matched
against that purchase in the same way a sale would be, so the recipient inherits the cost of those
shares rather than your pool average. Selling and transferring the same shares on the same day is
refused when there is such a purchase, because there is no rule for splitting it between the two;
the error says what to do instead.

## Gifts to anyone else

Shares given to anyone other than a spouse or civil partner are a **disposal at market value**
([TCGA 1992 s17](https://www.legislation.gov.uk/ukpga/1992/12/section/17)): you are taxed as if you
had sold them for what they were worth on the day, although no money changed hands. Record them with
the `GIFT` action. The `price` is the market value of the gift divided by its units. Market value
has its own rules ([s272](https://www.legislation.gov.uk/ukpga/1992/12/section/272)): for shares
quoted on an exchange it follows the day's quoted prices, while unquoted shares need a defensible
open-market valuation of the holding you gave away — the size of the holding changes the value per
share ([CG59562](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg59562)), and HMRC
can check a valuation after the disposal (form CG34). If the count has been restated for a later
split (Schwab's export does this), divide the value of the whole gift by the restated count. A
holding that has become worthless can be given away at a market value of `0`, and the whole cost
becomes a loss.

Gifts to connected persons made within six years of each other are valued as a series
([s19](https://www.legislation.gov.uk/ukpga/1992/12/section/19),
[CG14650](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg14650)): when the pieces
are worth more together than apart, each gift's consideration becomes its share of the value of
everything given, and each later gift enlarges the series, which can revise the earlier gifts —
already-filed years included. A typical holding of quoted shares is unaffected, since every share
has the same price, but for unquoted shares given in stages enter the s19-apportioned value as the
`price`, and amend the earlier years yourself when a later gift revises them. A transfer to your
spouse stays no gain / no loss, but it still counts towards the series when the same holding is
split between a spouse and someone else
([CG14710](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg14710)).

```csv
2024-03-16,GIFT,META,21.5,480.00,0.00,USD
```

The same identification rules as a sale apply, and a gain counts like any other.

`GIFT` is for a connected person, which
[s286](https://www.legislation.gov.uk/ukpga/1992/12/section/286) defines at some length: your
relatives (brothers, sisters, parents, grandparents, children, grandchildren) and their spouses,
your spouse's relatives and their spouses, your business partners and their spouses and relatives,
trustees of a settlement you or a connected person set up, companies you control alone or with
connected persons, and people acting together to control a company — among others, so read the
section if in doubt. If the recipient is not a connected person, a friend say, use
`GIFT_UNCONNECTED` instead; the only difference is what happens to a loss, and when unsure `GIFT` is
the safe choice, since it can only overstate. A sale and a `GIFT` of the same shares on one day
cannot be computed (they are one disposal under s105(1), and a loss on it could not be split), while
a sale and a `GIFT_UNCONNECTED` are one ordinary disposal, reported as a sale. Several `GIFT` rows
for one symbol on one day must state the same value per unit, and they mean one gift to one person:
their fees and their gain or loss merge into one result, which is right for one recipient and wrong
for several, since a clogged loss to one person cannot net a gain to another. If a day's gifts went
to different people, leave that symbol out of the input and work it out by hand, as with any day
cgt-calc refuses.

cgt-calc works out the gain before any relief. Shares in a trading company that is not listed on a
recognised stock exchange, or in your personal company, may qualify for
[Gift Hold-over Relief](https://www.gov.uk/gift-holdover-relief), which defers the gain. That is a
claim you and the recipient make on your returns; the report shows the gain in full.

!!! warning "A loss on a `GIFT` is clogged"

    A loss on a disposal to a connected person can only be set against gains on disposals to the
    same person while you are still connected
    ([s18(3)](https://www.legislation.gov.uk/ukpga/1992/12/section/18),
    [CG14561](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg14561)). HMRC calls
    this a clogged loss. cgt-calc shows it as "Losses on gifts" rather than in "Loss", and it does
    not reduce the gain. It is still a loss: keep a separate record of it and carry it forward with
    your other losses — the SA108 notes cover this under "Transferring assets between connected
    people". cgt-calc does not know who received which gift, so it never sets a clogged loss
    against a gain on another gift to the same person; if that applies to you, do that part by
    hand.

Gifts to charity ([s257](https://www.legislation.gov.uk/ukpga/1992/12/section/257)) are no gain / no
loss, as are the other transfers listed in
[CG12920](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg12920): to employee
trusts, housing associations and the nation. They are not disposals at market value, and cgt-calc
has no way to record them yet.

## Troubleshooting

### `Unknown action`

Use an action from [Actions to use](#actions-to-use). Do not copy another name from the cgt-calc
source code: some actions exist only for broker imports and a RAW row cannot supply their additional
data. For a real event that the table does not cover, do not force it into the nearest-looking
action; verify its UK tax treatment and open a
[GitHub issue](https://github.com/cgt-calc/capital-gains-calculator/issues/new) if cgt-calc should
support it.

### `Amount missing`

The row has no usable product of `quantity` and `price`. For a cash-only action, set quantity to `1`
and put the full amount in `price`: positive for money received and negative for money paid. See
[How the cash amount is calculated](#how-the-cash-amount-is-calculated).

### A header or column-count error

Use the seven columns from [Create the CSV](#create-the-csv), in that order, and keep every comma,
including the empty `symbol` field in a cash row. Save the file as UTF-8 CSV rather than converting
a PDF or pasting a formatted currency value such as `£1,000` into one field.

### `Reached a negative balance` for broker `Unknown`

Check the sign of every cash row and include the deposits, sales and income that funded later
purchases or withdrawals. If this RAW file supplements a named broker export, remember that their
balances are separate; see
[Combining RAW with a broker export](#combining-raw-with-a-broker-export).

### `Tried to sell`

Include the earlier acquisition and any split, spin-off or transfer that established the holding.
Use the same ticker throughout unless the broker-specific history supplies a supported rename. Check
the final portfolio and every disposal against the broker records rather than adding a made-up
purchase to make the calculation run.

### `also has units from` on a `STOCK_SPLIT` row

A broker's own split row states the change at that one account, and the holding also has units that
another input put there, so applying it to the whole pool would restate the holding by a ratio that
was never the corporate one. Add up the holdings across your accounts and write one RAW
`STOCK_SPLIT` row stating the change to all of them together; where a date has both, the RAW row is
used and the broker's own row for the same event is ignored. See
[Holdings spread across brokers](#holdings-spread-across-brokers).

### `cannot be placed either side of it`

A same-day row changes the quantity of the security, but cgt-calc cannot establish whether that
quantity uses the units before or after the reorganisation. This happens across separate inputs,
within a broker export whose row order is not a record of what happened first, and when a timed row
falls at or between the two timestamps of a paired reorganisation. If you know the real order, put
that day's rows for the symbol in one RAW input in [that order](#the-order-of-rows-on-one-date),
without [importing the same rows twice](#combining-raw-with-a-broker-export). Otherwise, work that
day out by hand and leave its rows out.

Do not upload an unredacted RAW file to GitHub: it can contain dates, holdings, income and other
sensitive financial information.
