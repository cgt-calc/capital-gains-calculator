# Transfers, gifts and fund income

Three kinds of event usually need a row you write yourself, because a broker export does not tell
cgt-calc enough about them:

- shares you transfer to your spouse or civil partner, or receive from them;
- shares you give to anyone else;
- income that a UK accumulation fund keeps and adds to your units.

You do not have to convert your whole history to the [RAW format](brokers/raw.md) for them. Pass a
small RAW file of just these rows, under the [header row](brokers/raw.md#create-the-csv), alongside
your broker export. If you already use a RAW file, add the rows to it instead, each value under its
column: cgt-calc reads only one.

```shell
cgt-calc --year 2024 transactions.csv extra-rows.csv
```

## Transfers to a spouse or civil partner

Shares given to a spouse or civil partner usually move at **no gain / no loss**: nothing is taxable
for you, and they inherit your base cost
([CG22200](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg22200)). Record them with
the `TRANSFER_TO_SPOUSE` action.

```csv
2024-03-16,TRANSFER_TO_SPOUSE,META,21.5,0.00,0.00,USD
```

Leave `price` at `0`, since a gift has no sale price. Put any fee you paid to make the transfer in
`fees`: it is added to the base cost the recipient inherits.

A complete Schwab Equity Awards export does list gifts, without saying who received them. If all of
them went to your spouse or civil partner, you can pass `--gifts-to-spouse` instead of adding a row
for each: see
[Gifts that all went to your spouse or civil partner](brokers/schwab.md#gifts-that-all-went-to-your-spouse-or-civil-partner).

The report and the PDF show the base cost that passes to the recipient, and the text report prints
the exact RAW row to give them, with the header line it goes under. Under each transfer, the PDF
lists the matching rules that applied, with the shares and base cost each one covers, as it does for
a sale: see [Labels in the PDF](usage.md#labels-in-the-pdf).

### If you received the shares

Record them arriving with `TRANSFER_FROM_SPOUSE`, with the base cost per unit as the `price`. The
transferor's report prints the row ready to use, with the header line that starts a new RAW file. To
add it to a RAW file whose header lists the columns in another order or has the
[optional columns](brokers/raw.md#create-the-csv), put each value under its column, with a blank
cell for any other column. The shares enter your Section 104 pool at that cost, acquired on the
transfer date, and no money is added to your cash balance.

```csv
2024-03-16,TRANSFER_FROM_SPOUSE,META,21.5,95.60,0.00,GBP
```

Your broker may show the shares arriving, but it does not know what they cost, so leave that row out
of your export rather than let it be read as a purchase at nothing.

!!! warning "Check that you qualify"

    No gain / no loss applies if you were living together at some point in the tax year of the
    transfer, and since 6 April 2023 for a period after separating as well
    ([CG22420](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg22420)). If you do not
    qualify, or the shares went to anyone else, record a [gift](#gifts-to-anyone-else) instead.

Buying the same shares within 30 days of a transfer changes the figure. The transfer is matched
against that purchase in the same way a sale would be, so the recipient inherits the cost of those
shares rather than your pool average. The PDF shows that part of the transfer on its own line:
**SAME DAY** for a purchase that day, or **BED AND BREAKFAST** with the purchase date. Selling and
transferring the same shares on the same day is refused when there is such a purchase, because there
is no rule for splitting it between the two; the error says what to do instead.

## Gifts to anyone else

Shares given to anyone other than a spouse or civil partner are a **disposal at market value**
([TCGA 1992 s17](https://www.legislation.gov.uk/ukpga/1992/12/section/17)): you are taxed as if you
had sold them for what they were worth on the day, although no money changed hands. Record them with
the `GIFT` action. The `price` is the market value of the gift divided by its units. Market value
has its own rules ([s272](https://www.legislation.gov.uk/ukpga/1992/12/section/272)): for shares
quoted on an exchange it follows the day's quoted prices, while unquoted shares need a defensible
open-market valuation of the holding you gave away, since the size of the holding changes their
value per share ([CG59562](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg59562)).
HMRC can check such a valuation after the disposal (form CG34). If the count has been restated for a
later split (Schwab's export does this), divide the value of the whole gift by the restated count. A
holding that has become worthless can be given away at a market value of `0`, and the whole cost
becomes a loss.

Gifts to connected persons made within six years of each other are valued as a series
([s19](https://www.legislation.gov.uk/ukpga/1992/12/section/19),
[CG14650](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg14650)): when the pieces
are worth more together than apart, each gift's consideration becomes its share of the value of
everything given, and each later gift enlarges the series, which can revise the earlier gifts,
including those in years already filed. A typical holding of quoted shares is unaffected, since
every share has the same price, but for unquoted shares given in stages enter the s19-apportioned
value as the `price`, and amend the earlier years yourself when a later gift revises them. A
transfer to your spouse stays no gain / no loss, but it still counts towards the series when the
same holding is split between a spouse and someone else
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
connected persons, and people acting together to control a company. That is not the whole
definition, so read the section if in doubt. If the recipient is not a connected person, a friend
say, use `GIFT_UNCONNECTED` instead; the only difference is what happens to a loss, and when unsure
`GIFT` is the safe choice, since it can only overstate. A sale and a `GIFT` of the same shares on
one day cannot be computed (they are one disposal under s105(1), and a loss on it could not be
split), while a sale and a `GIFT_UNCONNECTED` are one ordinary disposal, reported as a sale. Several
`GIFT` rows for one symbol on one day must state the same value per unit, and they mean one gift to
one person: their fees and their gain or loss merge into one result, which is right for one
recipient and wrong for several, since a clogged loss to one person cannot net a gain to another. If
a day's gifts went to different people, leave that symbol out of the input and work it out by hand,
as with any day cgt-calc refuses.

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
    your other losses. The SA108 notes cover this under "Transferring assets between connected
    people". cgt-calc does not know who received which gift, so it never sets a clogged loss
    against a gain on another gift to the same person; if that applies to you, do that part by
    hand.

Gifts to charity ([s257](https://www.legislation.gov.uk/ukpga/1992/12/section/257)) are no gain / no
loss, as are the other transfers listed in
[CG12920](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg12920): to employee
trusts, housing associations and the nation. They are not disposals at market value, and cgt-calc
has no way to record them yet.

## Income reinvested in accumulation units

A UK accumulation fund keeps its income and adds it to the value of the units you already hold. That
income is taxable although it is never paid out to you, and it counts as an amount spent on the
holding ([TCGA 1992 s99B](https://www.legislation.gov.uk/ukpga/1992/12/section/99B),
[CG57707](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg57707)).

Neither Vanguard nor Hargreaves Lansdown pays it into your account, and cgt-calc does not count it
from their exports. Other brokers have not been checked. With another broker, first run cgt-calc
without the rows for this income: if the PDF lists a dividend from the fund for that distribution
under **Interest and Dividends Events**, cgt-calc already counts that income, so do not add them.

Record the income and the spending as two rows, each for the amount of income on the fund's tax
voucher and dated on the fund's
[distribution date](https://www.gov.uk/hmrc-internal-manuals/investment-funds/ifm02220) for that
income. That is the day the income is treated as paid and the amount as spent. It can be up to four
months after the end of the period the income covers, and so in a later tax year. For £20 of income
on a holding of `FUND` with a distribution date of 28 June 2024:

```csv
2024-06-28,DIVIDEND,FUND,1,20.00,0.00,GBP
2024-06-28,FEE,FUND,1,-20.00,0.00,GBP
```

- The `DIVIDEND` row reports the income. Where the
    [tax voucher](https://www.gov.uk/hmrc-internal-manuals/savings-and-investment-manual/saim2200)
    shows an interest distribution, as a bond fund's does, write `INTEREST` with a blank `symbol`
    instead.
- The `FEE` row adds the same amount to the cost of the holding. Enter it as a negative `price`: a
    positive one stops the run with `Fee amount must not be positive`. Write its `symbol` exactly as
    the report names the holding.
- A voucher for an interest distribution before 6 April 2017 can show
    [income tax deducted](https://www.gov.uk/hmrc-internal-manuals/investment-funds/ifm02222). Only
    the amount after tax was reinvested: enter the gross interest on the `INTEREST` row, the tax as
    a negative `price` on an `INTEREST_TAX` row and the net amount on the `FEE` row.

Together the rows leave the cash balance unchanged. With the `FEE` alone, the income goes unreported
and the cash balance is £20 short, which can stop the run with `Reached a negative balance`.

To check the result, run cgt-calc for the tax year that date falls in. The summary lists the income
under Dividends or Interest, and the PDF report has an entry on that date headed “Cost added to FUND
with no new shares: £20.00”. Under that heading, “Number of units in the pool” should be the units
you hold and “new pool cost” £20 more than the pool cost before that entry. If you bought more of
the fund the same day, the entry follows the purchase, so compare it with the “new pool cost” under
the purchase.

If cgt-calc stops with
`Cannot add the cost of the FEE row for FUND on 2024-06-28: no units of FUND are held that day`, you
hold no units under the `FEE` row's `symbol` on that date:

- If cgt-calc knows the holding by another name, write that name as the `symbol` of the `FEE` row.
    The run still lists the holdings you have at the end of your files under “Final portfolio”. For
    one you have since sold, run without the `FEE` row and read the name in the report for the tax
    year of the sale.
- If the date is before the first purchase in your files, check the date and that no earlier
    purchase is missing.
- If you had sold or given away the whole holding before that date, cgt-calc does not work out
    whether the income adds to the cost of those units. Replace the `FEE` with an `ADJUSTMENT` of
    the same amount and a blank `symbol`, which keeps the balance right without adding the cost
    anywhere, and consider professional advice.

cgt-calc does not make this check when the `symbol` is the old or the new ticker of a
[ticker rename in a Vanguard export](brokers/vanguard.md#supported-activity) dated on the `FEE`
row's date or later. For such a `FEE`, check its entry in the PDF as described above. If “Number of
units in the pool” is 0, one of the three cases above applies, and the cost can end up on units you
buy later under either ticker.

This is for a fund based in the UK. For an accumulating fund or ETF based elsewhere, such as an
Irish one, see [Offshore funds (ERI)](offshore-funds.md).
