# Transfers, gifts and fund income

Four kinds of event usually need a row you write yourself, because a broker export does not tell
cgt-calc enough about them:

- shares you transfer to your spouse or civil partner, or receive from them;
- shares you give to anyone else;
- shares anyone else gives you, or that you inherit;
- income that a UK accumulation fund keeps and adds to your units.

You do not have to convert your whole history to the [RAW format](brokers/raw.md) for them. Pass a
small RAW file of just these rows, under the [header row](brokers/raw.md#create-the-csv), alongside
your broker export. If you already use a RAW file, add the rows to it instead, each value under its
column: cgt-calc reads only one.

```shell
cgt-calc --year 2024 transactions.csv extra-rows.csv
```

Shares you move between your own accounts need no row of their own when you include both brokers'
exports: see
[Shares you moved between your own accounts](#shares-you-moved-between-your-own-accounts).

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
cell for any other column. The shares enter your Section 104 pool, acquired on the transfer date at
the supplied base cost.

Enter any separate allowable expense you incurred in acquiring the shares in `fees`: cgt-calc adds
it to their cost without taking it from your cash balance. Check that the transfer qualifies for no
gain/no loss and that this is your own allowable acquisition expense, not a cost already included in
the supplied base cost. cgt-calc cannot verify those facts. If you cannot establish them, work this
holding out separately (consider professional advice). See
[TCGA 1992 s38](https://www.legislation.gov.uk/ukpga/1992/12/section/38) and
[HMRC CG15250](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg15250) for allowable
costs and the purpose test.

```csv
2024-03-16,TRANSFER_FROM_SPOUSE,META,21.5,95.60,0.00,GBP
```

Your broker may show the shares arriving, but it does not know what they cost. Leave that row out of
your export: the `TRANSFER_FROM_SPOUSE` row records the shares instead. Write its `symbol` exactly
as your own report names the holding: your broker may name it differently from your spouse's, and
under another name the shares become a separate holding.

!!! warning "Check that you qualify"

    No gain / no loss applies if you were living together at some point in the tax year of the
    transfer. Since 6 April 2023 it also applies for a period after separating and, once a divorce
    or dissolution has started, at any time under a divorce or separation agreement or a court order
    ([CG22200](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg22200),
    [CG22420](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg22420)). These rules
    look at the day the transfer counts as made, which under a court order or a binding agreement
    can differ from the day the shares moved: the bullet on a transfer from a spouse or civil
    partner under
    [Shares you were given or inherited](#shares-you-were-given-or-inherited) says how to find it.
    If you gave the shares and do not qualify, or they went to anyone else, record a
    [gift](#gifts-to-anyone-else) instead. If you received shares in a transfer that does not
    qualify, record them as
    [Shares you were given or inherited](#shares-you-were-given-or-inherited) describes.

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

If you transferred shares to your spouse or civil partner, or a former one, and the transfer does
not [qualify](#if-you-received-the-shares) for no gain / no loss, date and value the row as for the
person who received them: see the bullet on a transfer from a spouse or civil partner under
[Shares you were given or inherited](#shares-you-were-given-or-inherited). Under a court order or a
binding agreement, that date can differ from the day the shares moved.

Gifts to connected persons made within six years of each other are valued as a series
([s19](https://www.legislation.gov.uk/ukpga/1992/12/section/19),
[CG14650](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg14650)): when the pieces
are worth more together than apart, each gift's consideration becomes its share of the value of
everything given, and each later gift enlarges the series, which can revise the earlier gifts,
including those in years already filed. A typical holding of quoted shares is unaffected, since
every share has the same price, but for unquoted shares given in stages enter the s19-apportioned
value as the `price`, and amend the earlier years yourself when a later gift revises them. A
qualifying transfer to your spouse stays no gain / no loss, but it still counts towards the series
when the same holding is split between a spouse and someone else
([CG14710](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg14710)).

```csv
2024-03-16,GIFT,META,21.5,480.00,0.00,USD
```

The same identification rules as a sale apply, and a gain counts like any other.

`GIFT` is for a connected person, which
[s286](https://www.legislation.gov.uk/ukpga/1992/12/section/286) defines at some length: your spouse
or civil partner until the divorce or dissolution is made final, your relatives (brothers, sisters,
parents, grandparents, children, grandchildren) and their spouses, your spouse's relatives and their
spouses, your business partners and their spouses and relatives, trustees of a settlement you or a
connected person set up, companies you control alone or with connected persons, and people acting
together to control a company. That is not the whole definition, so read the section if in doubt. If
the recipient is not a connected person, a friend say, use `GIFT_UNCONNECTED` instead; the only
difference is what happens to a loss, and when unsure `GIFT` is the safe choice, since it can only
overstate. A sale and a `GIFT` of the same shares on one day cannot be computed (they are one
disposal under s105(1), and a loss on it could not be split), while a sale and a `GIFT_UNCONNECTED`
are one ordinary disposal, reported as a sale. Several `GIFT` rows for one symbol on one day must
state the same value per unit, and they mean one gift to one person: their fees and their gain or
loss merge into one result, which is right for one recipient and wrong for several, since a clogged
loss to one person cannot net a gain to another. If a day's gifts went to different people, leave
that symbol out of the input and work it out by hand, as with any day cgt-calc refuses.

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

## Shares you were given or inherited

Shares given to you by anyone other than your spouse or civil partner, or left to you when someone
died, usually cost you their market value on the day of the gift or of the death, although you paid
nothing ([TCGA 1992 s17](https://www.legislation.gov.uk/ukpga/1992/12/section/17),
[s62](https://www.legislation.gov.uk/ukpga/1992/12/section/62)). So do shares from your spouse or
civil partner, or a former one, in a transfer that does not [qualify](#if-you-received-the-shares)
for no gain / no loss
([CG22420](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg22420)). No broker export
gives cgt-calc that value, so record the shares with a `STOCK_ACTIVITY` row, with the value per
share as the `price`:

- **A gift:** date the row on the day of the gift, at the market value that day
    ([CG66450](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg66450)).
    [Gifts to anyone else](#gifts-to-anyone-else) says how market value is worked out.
- **An inheritance:** date the row on the date of death, even if the shares reached your account
    later, at their market value that day
    ([CG31140](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg31140)). If
    inheritance tax was payable and HMRC agreed the value of the shares for it, use that value
    ([s274](https://www.legislation.gov.uk/ukpga/1992/12/section/274),
    [CG32224](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg32224)). If the
    deceased's holding was shared out between heirs, each heir's cost is a proportion of the whole
    holding's value: enter that value divided by the holding's number of shares, not a value for
    your part alone. Ask the executors for it if you do not have it.
- **A transfer from your spouse or civil partner, or a former one, that does not qualify:** date the
    row on the day the transfer counts as made, at the market value that day. Under a court order it
    is usually the date of the order. If the order was made before the divorce or dissolution was
    made final (for a divorce, the Final Order or decree absolute) and the shares moved afterwards,
    it is the day the divorce or dissolution was made final. If they moved before then, the order
    counts as a contract made on its date, and the transfer counts as made on that date or, if the
    contract was conditional, on the day the condition was met
    ([s28](https://www.legislation.gov.uk/ukpga/1992/12/section/28),
    [CG22420](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg22420)). Whether it
    was conditional depends on the order's terms
    ([CG14261](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg14261)): read the
    order, and take professional advice if it does not make this clear. Without a court order it is
    the day the shares moved, unless you had already made a binding agreement to transfer them: then
    [CG22410](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg22410) says which date
    applies.

```csv
2023-06-01,STOCK_ACTIVITY,ACME,200,5.00,0.00,GBP
```

The shares join any you already hold under the same `symbol`, so write it exactly as the report
names that holding. No money is added to your cash balance. Enter the value: cgt-calc accepts a
`price` of `0` but warns about it, naming the row, because it leaves only any fees in the cost and
overstates the gain on a later sale. If you, or for an inheritance the executors, paid a cost of
having the shares transferred to you, such as a broker's fee for the transfer, enter it in `fees`:
it is an allowable cost ([s38](https://www.legislation.gov.uk/ukpga/1992/12/section/38), and
[s64](https://www.legislation.gov.uk/ukpga/1992/12/section/64) for an inheritance), so cgt-calc adds
it to the cost of the shares, without taking it from your cash balance. If your broker export shows
the shares arriving, leave that row out of the export: this row records them instead.

If the giver claimed [Gift Hold-over Relief](https://www.gov.uk/gift-holdover-relief), which you
claim together with them, your cost is the market value less the gain held over
([s165](https://www.legislation.gov.uk/ukpga/1992/12/section/165),
[s260](https://www.legislation.gov.uk/ukpga/1992/12/section/260)). Enter that reduced cost per share
as the `price`.

## Shares you moved between your own accounts

Moving shares between taxable accounts in your own name, such as from one broker to another, is not
a disposal. A broker usually holds your shares as your nominee, and the law treats shares held by a
nominee as yours, and what the nominee does with them as done by you
([TCGA 1992 s60](https://www.legislation.gov.uk/ukpga/1992/12/section/60)). The shares keep the
dates and costs of their purchases. Shares of the same class that you hold at several brokers are
also one holding, your Section 104 pool
([s104](https://www.legislation.gov.uk/ukpga/1992/12/section/104)).

cgt-calc treats them the same way: it combines a security's rows from every file in a run into one
holding, and the same-day and 30-day rules can match a sale at one broker with a purchase at
another. What you add depends on which brokers' exports you have and cgt-calc can read: treat a
broker that cgt-calc does not support (see [Brokers](brokers/index.md)) as one whose export you do
not have.

### Both brokers' exports

Pass both exports in one run, as for any two brokers. The old broker's export has the purchases and
the new broker's the later sales, so the move itself needs no row.

If the run stops at a row for the move, remove that row from a working copy of the export, and keep
the download unchanged for your records. Trading 212's `Transfer in` and `Transfer out` rows stop
the run, and so does a Schwab `Security Transfer` row for shares, which has a quantity but no amount
and stops with `Amount missing`. Also remove a row that records the shares arriving as a purchase,
such as a `Buy` you entered in Sharesight for the move: cgt-calc reads it as another purchase, so
the shares are counted twice. A row for the move that does neither needs no change: the
[checks below](#check-the-result) show whether the result is right.

### Only the new broker's export

If you no longer have the old broker's export, or cgt-calc cannot read it, record the shares'
history there in your RAW file:

1. Add a `BUY` row for each purchase of the shares, dated on the day you bought them, with the price
    and fees you paid. The old broker's statements or contract notes give these. For shares that
    vested there, add a `STOCK_ACTIVITY` row instead, as the
    [RAW actions](brokers/raw.md#actions-to-use) describe, dated on the day your employer taxed the
    shares as income and at their market value that day. That is usually the vest date, but it can
    be another day, such as the day the shares were issued: your award statement or payslip, or your
    payroll team, can tell you which. Also add every other change to the holding there: a sale, a
    split, or income added to accumulation units, which adds to the holding's cost (see
    [Income reinvested in accumulation units](#income-reinvested-in-accumulation-units)).
2. Fund each `BUY` with a `TRANSFER` row on the same date and in the same currency. A RAW row with
    no `broker` uses the RAW file's own `Unknown` balance, and without the money for the purchase
    that balance goes negative and the run stops with
    [`Reached a negative balance`](brokers/raw.md#reached-a-negative-balance-for-broker-unknown) for
    broker `Unknown`. The deposit stands for the money you paid in at the old broker and does not
    change any other broker's balance. Deposit only what the cash already in your RAW file does not
    cover: a deposit you have recorded or the proceeds of a sale can pay for some or all of the
    purchase, and money recorded twice leaves cash you never had in the final balance. With no other
    cash in the file, as in the example below, deposit what the purchase cost including fees.
3. If the new broker's export has a row for the shares arriving that stops the run or records them
    as a purchase, remove it from a working copy, as above.

For 100 `ACME` bought at £10.00 with a £5.00 fee on 2 May 2023:

```csv
2023-05-02,TRANSFER,,1,1005.00,0.00,GBP
2023-05-02,BUY,ACME,100,10.00,5.00,GBP
```

!!! warning "Date each purchase on the day you made it"

    Do not date the shares on the day they moved, even if the new broker shows them arriving with
    their cost. A purchase dated then can be matched with a sale of the same shares on that day or
    in the 30 days before it, which gives a wrong gain without an error.

### Only the old broker's export

cgt-calc then counts the shares as still held after the move, which is right, since they are still
yours. Add what happened to the security at the new broker as RAW rows: each sale as a `SELL`, each
purchase as a `BUY` funded as in step 2 above, a split or consolidation as the
[checks below](#check-the-result) describe under "A later split", and income added to accumulation
units as [described below](#income-reinvested-in-accumulation-units). Without them, a sale is
missing from the report or its cost is wrong. If the old broker's export has a row for the move that
stops the run, remove it from a working copy.

### Check the result

As well as the [usual checks](usage.md#check-the-result):

- **One holding under one ticker.** Check that both brokers' exports, and your RAW rows, give the
    security the same ticker. cgt-calc combines only the ticker pairs it already supports, so `ACME`
    at one broker and `ACME.L` at the other stay two holdings. A sale at the new broker can then be
    costed from the shares under its own ticker alone, which gives a wrong gain without an error,
    even when the portfolio section looks right. Depending on the rows, the run may instead stop
    with `Tried to sell` or, when both exports give the ISIN, with `does not match`. Give every row
    the same ticker, in your RAW rows or in a working copy of the export. Do not add both tickers to
    an [ISIN translation](extra-data-and-options.md#isin-to-ticker-translation) file instead: that
    does not combine the holdings, and it removes the `does not match` stop without correcting the
    result.
- **A later split.** If the security is split or consolidated after the move, add a RAW
    `STOCK_SPLIT` row for the change to the whole holding, as
    [Holdings spread across brokers](brokers/raw.md#holdings-spread-across-brokers) describes, even
    when the run succeeds without it: cgt-calc then uses it instead of the brokers' split rows for
    that event. A broker's single split row, such as Schwab's `Stock Split`, states the change to
    its own account only, so after a move of part of the holding cgt-calc can accept it and leave
    the shares that moved out of the change, which gives a wrong gain without an error. The
    exception is Trading 212's `Stock split close` and `Stock split open` rows, which state the
    account's whole position before and after: if the run accepts them, leave them in place without
    a RAW row. If it refuses them, for example because part of the holding moved out of Trading 212,
    add the RAW row. A RAW row replaces only split rows that cgt-calc can read: if the run stops
    because it cannot read a broker's row at all, see that broker's guide.
- **What you hold.** Compare the quantity of each holding in the portfolio section with the total
    you held across your brokers on the date in its heading. Some exports drop a row for shares
    arriving without a message. If the shares' history is missing and you have not sold them, the
    holding is missing from the portfolio section or its quantity is too low, and nothing else in
    the report shows it.

Only the rows that Trading 212 and Schwab write for a move have been checked. Another broker's
export may label the move differently: if the run stops at a row that you can tell is the move,
remove it in the same way.

These steps cover moves between taxable accounts in your own name only:

- Shares that went to or came from anyone else are not a move between your own accounts. See
    [Transfers to a spouse or civil partner](#transfers-to-a-spouse-or-civil-partner),
    [Gifts to anyone else](#gifts-to-anyone-else) and
    [Shares you were given or inherited](#shares-you-were-given-or-inherited).
- A move between a taxable account and an ISA or a pension, or one that changed the shares into
    another class or type of units, such as a fund's units converting to another class, is not
    covered. Work that holding out separately (consider professional advice).
- Two accounts at the same broker may not combine in one run: check that broker's guide, such as the
    [Trading 212 known limitations](brokers/trading212.md#known-limitations).

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
