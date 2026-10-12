# Tax estimates

Use this page to understand the tax figures in the terminal summary and decide whether to add
`--income`. These figures are estimates, not your final tax bill; check
[what they leave out](#what-the-estimates-leave-out) before using them. For the steps to create and
check your report, see [Generate a report](usage.md).

## Tax at the basic and higher rate

When a full tax year has a **Taxable gain**, the terminal shows the Capital Gains Tax on it twice,
as **Tax at basic rate** and **Tax at higher rate**. The PDF report does not include these figures.

cgt-calc does not know your income, so it cannot tell which rate you pay unless you
[give your income](#estimate-the-tax-from-your-income). The notes under the two figures give the
rates and the Income Tax basic rate limit for that tax year, which is £37,700 for 2025/26:

- **Tax at basic rate** is your tax if your taxable income for that year plus the taxable gain is
    £37,700 or less.
- **Tax at higher rate** is your tax if your taxable income for that year is £37,700 or more. This
    includes additional rate taxpayers.
- If your taxable income is under £37,700 but the taxable gain takes you over it, you pay the basic
    rate on the part of it that fits under £37,700 and the higher rate on the rest. Your tax is then
    between the two figures.

For example, in 2025/26 the rates are 18% and 24%. With a taxable gain of £12,000 the terminal shows
£2,160 and £2,880. If your taxable income is £30,000:

1. £7,700 of the £37,700 is still unused.
2. The first £7,700 of the gain is taxed at 18%, which is £1,386.
3. The other £4,300 is taxed at 24%, which is £1,032.
4. Your tax is £2,418.

Taxable income is your income after the Personal Allowance and other Income Tax reliefs. Follow
HMRC's steps to [work out which rate you pay](https://www.gov.uk/capital-gains-tax/rates). Two
things to know about the limit
([CG21204](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg21204)):

- Gift Aid donations and pension contributions that get tax relief at source raise your limit, so
    more of your gain is taxed at the basic rate. cgt-calc does not know about them.
- If you pay Scottish or Welsh Income Tax, the limit for Capital Gains Tax is still the UK one shown
    in the notes.

The rates changed during two tax years: on 23 June 2010 and on 30 October 2024. For 2010/11 and
2024/25, cgt-calc taxes each gain at the rates for the day of the sale or other disposal. It deducts
the year's losses and the annual exempt amount from the gains taxed at the highest rate first,
whenever in the year the loss arose. This gives the lowest tax, and you may set them against your
gains in whichever way benefits you most
([CG10246](https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual/cg10246)).

In 2010/11, gains before 23 June 2010 are taxed at 18% whatever your income and do not count towards
the limit
([Finance (No. 2) Act 2010, Schedule 1, paragraph 18](https://www.legislation.gov.uk/ukpga/2010/31/schedule/1)).
When part of your taxable gain was made before that date, the notes say how much of it was made from
23 June 2010; compare only that amount with the limit. If none of it was made from that date, the
terminal shows one figure, **Tax at 18%**. It shows the same for 2008/09 and 2009/10, when everyone
paid 18%.

### What the estimates leave out

The figures are estimates to help you plan, not your final tax bill, and the last note in the
terminal says so. They cover only the **Taxable gain** in the report, at the rates for shares, so
they leave out:

- gains and losses that are not in the files you supplied, including those on other assets such as
    property, which can be taxed at different rates
- losses brought forward from earlier years
- Business Asset Disposal Relief (formerly Entrepreneurs' Relief) and Investors' Relief
- losses on gifts to connected people, shown as **Losses on gifts**, which can only reduce gains on
    disposals to the [same person](brokers/raw.md#gifts-to-anyone-else)

The figures also tax a gain on an
[offshore fund that was ever non-reporting](offshore-funds.md#unsupported-functionality) as a
capital gain, although it can be taxed as income. Work out that disposal outside cgt-calc.

No tax figures are shown when the taxable gain is zero or when you use
[`--from` and `--to`](usage.md#report-part-of-a-tax-year-advanced), even for a period that covers
the whole tax year.

## Estimate the tax from your income

Add `--income` to get one figure in place of the two. Pass your income for the tax year before the
Personal Allowance, without the dividends and interest in the files you supply. For most employees
that is the pay shown on the P60:

```shell
cgt-calc --year 2025 --schwab-file schwab_transactions.csv --income 45000
```

cgt-calc then:

1. Adds the dividends and interest in the report to the income you gave.
2. Deducts the standard Personal Allowance for the year, which is £12,570 for 2025/26.
3. Works out how much of the basic rate limit that taxable income leaves unused.
4. Taxes that much of the gain at the basic rate and the rest at the higher rate.

The terminal shows **Estimated tax** in place of the two figures, with notes that give each step so
that you can check it:

```text
  Taxable gain:        £12,000.00
  Estimated tax:        £2,653.80
Income £45,000.00, plus £1,200.00 dividends and £300.00 interest from these files, less the £12,570 Personal Allowance: taxable income £33,930.00, which leaves £3,770.00 of the £37,700 basic rate limit unused.
Of the taxable gain, £3,770.00 is taxed at 18% and £8,230.00 at 24%.
```

In the figure you pass, include:

- pay from every job, taxable benefits, pensions, rental profit and any other taxable income
- dividends and interest from accounts that are not in the files you supply

Leave out income that is not taxed, such as interest and dividends in an ISA.

cgt-calc cannot know the following, so allow for them in the figure you pass:

- **A Personal Allowance that is not the standard one.** Subtract any Blind Person's Allowance. Add
    any part of your allowance that you transferred with Marriage Allowance. If you received
    Marriage Allowance, change nothing: it reduces your tax, not your taxable income. Before 2016/17
    some older people had a higher age-related allowance; subtract the extra.
- **Gift Aid donations and pension contributions that get tax relief at source.** They raise your
    basic rate limit. Subtract their gross amount: what you paid plus the basic rate tax relief
    added to it. If that takes the figure below your Personal Allowance, the estimate can be too
    high, because cgt-calc cannot count the rest.
- **Other Income Tax reliefs.** Subtract anything else that reduces your taxable income, such as a
    trading loss you claim against your income.
- **Dividends and interest exported after tax.** cgt-calc adds them as the report shows them. If
    your broker's export gives an amount after tax was taken off, as Revolut does for
    [dividends](brokers/revolut.md#dividends-and-withholding-tax) and Trading 212 does in
    [some cases](brokers/trading212.md#known-limitations), add that tax unless you have put it into
    the report.
- **Dividends before 6 April 2016.** cgt-calc does not add them, because the amount taxed is not the
    amount received (see [Choose the tax year](usage.md#choose-the-tax-year)). Add their taxable
    amount yourself. The terminal reminds you when the report has such dividends.

The Personal Allowance shrinks when income is over £100,000. That does not change the estimate: at
that income all of the gain is taxed at the higher rate. For the same reason, when your income
leaves none of the limit unused, the notes give the allowance as "£12,570 at most".

For 2010/11 and 2024/25, when the rates changed during the year, cgt-calc sets the losses, the
annual exempt amount and the unused part of the limit against the gains where they save the most
tax. HMRC confirms you may use them
[in the most beneficial way](https://www.gov.uk/government/publications/changes-to-the-rates-of-capital-gains-tax/capital-gains-tax-rates-of-tax).
The estimate is the tax for the whole year. It is not the adjustment figure that the 2024/25 Self
Assessment return asks for.

`--income` needs a full tax year, so it cannot be combined with `--from` and `--to`. Write it in
whole pounds, such as `45000`. For 2008/09 and 2009/10 it changes nothing: everyone paid 18%
whatever their income, so the terminal still shows one figure, **Tax at 18%**. The same goes for
2010/11 when all of your taxable gain was made before 23 June 2010. The estimate leaves out the same
things as the two figures: see [What the estimates leave out](#what-the-estimates-leave-out).
