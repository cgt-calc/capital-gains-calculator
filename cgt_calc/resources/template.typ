// The calculations report. render_pdf passes the report as JSON in
// sys.inputs.view, every amount, quantity and date already printed as a
// string: pounds with thousands separators, unit counts without trailing
// zeros, dates spelled out. Counts and event numbers stay integers. A string
// inserted with #value prints as text, never as markup, so nothing from a
// broker file is read as markup.
//
// Directly after #value, Typst reads "." with a letter, "(" or "[" as part
// of the expression, and takes a ";" as its end: write "\;" for a semicolon
// there.
//
// The text is set in Noto Sans, the files under fonts/noto-sans. Typst draws a
// character Noto Sans lacks from the fonts it embeds, New Computer Modern
// first. No ligatures: the "ff" of "differ" would share its glyph with the
// character ﬀ, and the PDF's text maps a glyph back to one string, so ﬀ in a
// holding's name would read back as "ff". No hyphenation, so words stay whole
// in the PDF's text. A #box keeps a phrase such as "(£1.00/unit)" on one line.
//
// The summary comes first. Each event's matching lines are a table, with the
// pool after each line in a row of its own.
#let view = json(bytes(sys.inputs.view))
#let navy = rgb(24, 55, 100)
#let accent = rgb(37, 99, 163)
#let muted = rgb(91, 105, 116)
#let hairline = rgb(197, 208, 214)
#set page(paper: "a4", margin: (x: 2cm, top: 2.6cm, bottom: 2.9cm),
  header: context if here().page() > 1 {
    set text(size: 8.5pt, fill: muted)
    block(width: 100%, stroke: (bottom: 0.4pt + hairline), inset: (bottom: 0.35em))[Capital Gains Tax Calculations for #view.title_period]
  },
  footer: context align(center, text(size: 8.5pt, fill: muted)[Page #counter(page).display() of #counter(page).final().first()]),
)
#set text(font: ("Noto Sans", "New Computer Modern"), size: 12pt, lang: "en", hyphenate: false, ligatures: false, number-width: "tabular")
#set par(leading: 0.7em, spacing: 0.7em)
// Noto Sans draws these letters as a base glyph and a separate mark, and the
// mark does not read back from the PDF's text. New Computer Modern draws each
// as one glyph that does.
#show regex("[\u{01F9}\u{1E3F}\u{1E43}\u{1E45}\u{1E63}\u{1EB9}\u{1ECB}\u{1ECD}\u{1EE5}]"): set text(font: "New Computer Modern")
#show heading.where(level: 1): it => block(above: 2.83em, below: 0.7em, sticky: true, text(size: 15pt, weight: "bold", fill: navy, it.body))
#show heading.where(level: 2): it => block(above: 2.11em, below: 0.7em, sticky: true, width: 100%, text(size: 13pt, weight: "semibold", fill: accent, it.body))
#show heading.where(level: 3): it => block(above: 0em, below: 1.2em, sticky: true, text(size: 14pt, weight: "semibold", fill: navy, it.body))
#let summary-table(..rows) = table(
  columns: (1fr, auto),
  stroke: (x, y) => (bottom: 0.4pt + hairline),
  inset: (x: 0pt, y: 0.5em),
  align: (left + bottom, right + bottom),
  ..rows,
)
#let total(body) = text(weight: "bold", fill: navy, body)
#let note(body) = text(fill: muted, body)
// An event's title on one line and, when it has one, its price on the next,
// in body text. The PDF bookmark drops the line break, so a space before it
// keeps the title and price apart there; the page hides it at the line end.
#let event-heading(prefix, item, detail: none) = heading(level: 3, {
  [#prefix #item]
  if detail != none {
    [ ]
    linebreak()
    text(size: 12pt, weight: "regular", fill: black, detail)
  }
})
// Each event sits beside a light bar. The caller keeps an event of up to four
// matching lines on one page.
#let event-group(breakable: true, body) = block(width: 100%, breakable: breakable, above: 2.11em, below: 0em, inset: (left: 13pt, y: 2pt), stroke: (left: 2.5pt + rgb(176, 200, 227)), body)
#let header-cell(body) = text(size: 0.917em, weight: "semibold", fill: muted, body)
// One table per event: the rule, its quantity and the event's figures, then a
// row with the pool after the line. The line's number spans both rows and
// cannot break, so a page break never parts a rule's figures from the row
// under them. A long header takes more than one line, and the rule's column
// gets the width the figures leave, so a name such as BED AND BREAKFAST can
// wrap when the figures are large. A bed-and-breakfast line's date note
// therefore opens the pool row, which spans the table, in a box that keeps it
// on one line. A disposal's last header spans its gain-or-loss label and
// figure. The exempt table, with the longest headers, is set at 10.5 pt.
#let lines-table(event) = {
  let exempt = event.kind == "exempt"
  let split = event.is_disposal or exempt
  let headers = if exempt {
    ([Quantity], [Disposal \ proceeds], [Cost used for \ this illustrative \ calculation], [Disregarded \ gain or loss])
  } else if event.is_disposal {
    ([Quantity], [Disposal \ proceeds], [Allowable \ cost], [Gain or loss])
  } else if event.kind == "spin_off" {
    ([Quantity], [Original cost], [After spin-off])
  } else if event.kind == "eri" {
    ([Quantity], [Original cost], [After income \ distribution])
  } else if event.kind == "transfer_to_spouse" {
    ([Quantity], [Base cost])
  } else {
    ([Quantity], [Actual cost])
  }
  block(above: 0.7em, below: 0em, text(size: if exempt { 10.5pt } else { 1em }, table(
    columns: (1.4em, 1fr, ..headers.map(_ => auto), ..if split { (auto,) }),
    stroke: none,
    inset: (x, y) => (x: 0.3em, top: if y > 1 and calc.odd(y) { 0.65em } else { 0.35em }, bottom: 0.35em),
    align: (x, y) => if x < 2 or (split and x == 5) { left } else { right },
    table.header(
      [], header-cell[Rule],
      ..headers.enumerate().map(((i, header)) => if split and i == headers.len() - 1 {
        table.cell(colspan: 2, align: left, header-cell(header))
      } else {
        header-cell(header)
      }),
      table.hline(stroke: 0.6pt + hairline),
    ),
    ..for (i, line) in event.lines.enumerate() {
      let last = i == event.lines.len() - 1
      (
        table.cell(rowspan: 2, breakable: false, text(fill: muted)[#(i + 1).]),
        [#text(weight: "bold", tracking: 0.05em)[#line.rule]],
        [#line.quantity],
      )
      if split {
        ([£#line.proceeds], [£#line.cost])
        if line.is_gain {
          ([#text(fill: muted)[#if exempt [disregarded ]gain]], [£#line.gain])
        } else {
          ([#text(fill: muted)[#if exempt [disregarded ]loss]], [£#line.loss])
        }
      } else if event.kind == "spin_off" {
        ([#text(fill: muted)[of #line.spin_off_source] £#line.amount], [£#line.new_pool_cost])
      } else if event.kind == "eri" {
        ([#text(fill: muted)[of #event.symbol] £#line.amount], [£#line.new_pool_cost])
      } else if event.kind == "transfer_to_spouse" {
        ([£#line.cost],)
      } else {
        ([£#line.amount],)
      }
      (table.cell(colspan: if split { 6 } else if headers.len() == 3 { 4 } else { 3 }, inset: (x: 0.3em, top: 0.35em, bottom: if last { 0.35em } else { 0.65em }), align: left)[#text(fill: muted)[
        #if line.bnb_date != none [#box[#if exempt [calculation] else if event.kind == "transfer_to_spouse" [base cost] else [allowable cost] on #line.bnb_date] \ ]
        #if event.kind == "option_grant" [
          The option grant does not change the Section 104 holding of the underlying shares.
        ] else [
          #if event.pool_units_left [Number of units left in the pool:] else [Number of units in the pool:]
          #line.new_quantity#if line.has_pool_units {
            [, new pool cost: £#line.new_pool_cost #box[(£#line.pool_per_unit/unit)]]
            if event.is_acquisition and line.spin_off_source != none {
              [ \ Cost basis calculated based on cost of #line.spin_off_source share pool from which #event.symbol shares were spun off]
            }
            if event.is_acquisition {
              for eri in line.eris [ \ Cost basis increased by £#eri.cost #box[(£#eri.unit_price/unit)], due to excess reported income on #box[#eri.date]#if eri.estimated_until != none [, estimated at the period-end rate until #box[#eri.estimated_until]]]
            }
          }.
          #for eri in line.pending_eris [ \ The cost of this disposal includes the excess reported income for the period ending #box[#eri.period_end], whose fund distribution date, #box[#eri.distribution_date], comes after the disposal. The cost takes the same pound amount as the income#if eri.estimated [, estimated at the period-end rate until then]. The regulations do not settle whether the cost should instead use the rate for the disposal date: see the section on disposals before the distribution date in the documentation on offshore funds.]
        ]
      ]],)
      if not last { (table.hline(stroke: 0.4pt + hairline),) }
    },
  )))
}

#block(below: 1.4em, width: 100%, stroke: (bottom: 1.2pt + accent), inset: (bottom: 0.5em), text(size: 18pt, weight: "bold", fill: navy)[Capital Gains Tax Calculations for #view.title_period])

= Overall - Capital Gains
#summary-table(
  [Number of acquisitions], [#view.summary.acquisition_count],
  [Number of disposals], [#view.summary.disposal_count],
  [Total disposal proceeds], [£#view.summary.disposal_proceeds],
  [Total allowable costs], [£#view.summary.allowable_costs],
  [Total capital gain before loss], [£#view.summary.total_gain],
  [Total capital loss], [£#view.summary.total_loss],
  ..if view.summary.has_gift_loss {
    ([Losses on gifts, kept separate], [£#view.summary.gift_loss])
  },
  ..if view.summary.exempt_disposal_count > 0 {
    (
      [Number of exempt disposals], [#view.summary.exempt_disposal_count],
      [Total exempt disposal proceeds], [£#view.summary.exempt_disposal_proceeds],
    )
  },
  table.hline(stroke: 0.8pt + navy),
  total[Total capital gain after loss], total[£#view.summary.net_gain],
)
#note[Amounts are rounded to the nearest penny individually, so a line may differ from its components by a penny.]
#if view.summary.includes_estimated_eri [
  #note[The allowable costs include excess reported income estimated at the period-end rate, as marked on the disposals below. Run the calculation again after its distribution date for the final figures.]
]

= Overall - Dividends and Interests
#summary-table(
  [Number of dividend distributions], [#view.income_summary.dividend_count],
  [Number of interest transactions], [#view.income_summary.interest_count],
  [Number of interest tax transactions], [#view.income_summary.interest_tax_count],
  [Number of excess reported income distributions], [#view.income_summary.eri_count],
  [Total amount of dividends proceeds], [£#view.income_summary.total_dividends],
  ..if view.income_summary.eri_dividends != none {
    (table.cell(colspan: 2, inset: (left: 1em))[#note[(including £#view.income_summary.eri_dividends from excess reported income distributions#if view.income_summary.eri_dividends_estimated [, at least partly estimated at the period-end rate])]],)
  },
  [Total amount of dividends tax allowance due to double taxation treaties], [£#view.income_summary.treaty_allowance],
  ..if view.income_summary.dividend_allowance != none {
    ([Total amount of dividends tax yearly allowance], [£#view.income_summary.dividend_allowance])
  },
  ..if view.income_summary.taxable_dividends != none {
    (total[Total amount of taxable dividends proceeds], total[£#view.income_summary.taxable_dividends])
  },
  ..if view.income_summary.explains_dividend_tax_credit {
    (table.cell(colspan: 2)[#note[Most dividends before 6 April 2016 carried a tax credit, so this report does not work out the taxable amount. Use your dividend vouchers and HMRC's guidance on tax credits.]],)
  },
  [Total amount of UK interests proceeds], [£#view.income_summary.uk_interest],
  [Total amount of foreign interests proceeds], [£#view.income_summary.foreign_interest],
  ..if view.income_summary.eri_interest != none {
    (table.cell(colspan: 2, inset: (left: 1em))[#note[(including £#view.income_summary.eri_interest from excess reported income distributions#if view.income_summary.eri_interest_estimated [, at least partly estimated at the period-end rate])]],)
  },
  [Total amount of interest tax paid], [£#view.income_summary.interest_tax],
)

#pagebreak()
= Capital Gains Events

#for day in view.days [

== #day.date
#for event in day.events [
#event-group(breakable: event.lines.len() > 4)[
#if event.is_disposal [
  #if event.kind == "option_grant" [
    #event-heading([Disposal #event.number:], [grant of #event.quantity contract#if not event.is_one_unit [s] of the #event.symbol], detail: [for £#event.proceeds])
  ] else [
    #event-heading([Disposal #event.number:], [#event.quantity units of #event.symbol], detail: [#if event.is_gift [given away at a market value of] else [for] £#event.proceeds #box[(£#event.per_unit/unit)]#if event.has_fees [, #box[£#event.fees fees] allowed as cost]])
  ]
  #if event.outcome == "gain" [
    Chargeable *gain* is £#event.gain#if event.is_gift [, before any relief:
    a gift of shares that qualify for Gift #box[Hold-over] Relief has the gain deferred by a joint claim on your returns]

    #text(fill: muted)[Capital gain to date is £#event.gain_to_date]
  ] else if event.outcome == "nil" [
    Neither a gain nor a loss.
  ] else if event.outcome == "clogged_loss" [
    *Clogged loss* of £#event.loss on a gift, kept out of the loss total:
    a loss on a gift to a connected person can only be set against gains on disposals to the same person while still connected (TCGA 1992 s18(3)).

    #text(fill: muted)[Losses on gifts to date are £#event.gift_loss_to_date]
  ] else [
    Chargeable *loss* is £#event.loss

    #text(fill: muted)[Capital loss to date is £#event.loss_to_date]
  ]
] else if event.kind == "acquisition" [
  #event-heading([Acquisition #event.number:], [#event.quantity units of #event.symbol], detail: [for £#event.cost #box[(£#event.per_unit/unit)]#if event.has_fees [, #box[including £#event.fees fees]]])
] else if event.kind == "management_fee" [
  #heading(level: 3)[Cost added to #event.symbol with no new shares: £#event.cost]
] else if event.kind == "spin_off" [
  #if event.has_units [
    #event-heading([Spin-off adjustment #event.number:], [#event.spin_off_source cost adjusted because of #event.spin_off_dest shares spun off])
  ]
] else if event.kind == "eri" [
  #if event.has_units [
    #event-heading([Excess Reported Income:], [#event.symbol cost increased by £#event.cost #box[(£#event.eri_unit_price/unit)]])
    #if event.eri_estimated_until != none [
      Estimated at the period-end rate until the fund distribution date, #box[#event.eri_estimated_until]. Run the calculation again after that date for the final figure.
    ]
  ]
] else if event.kind == "rename" [
  #heading(level: 3)[#event.symbol was renamed to #event.renamed_to#if event.has_units [: #event.quantity units with pool cost £#event.cost transferred]]
] else if event.kind == "split" [
  #event-heading([Share reorganisation:], [#event.split.day_open_quantity units of #event.symbol restated as #event.split.scaled_day_open_quantity (ratio #event.split.ratio_description)])
  Neither a disposal nor an acquisition: the new holding is the same asset, held from the same dates, with the same pooled cost (TCGA 1992 s127).

  Stated as #event.split.broker_before_quantity units before the event and #event.split.broker_after_quantity after.
  #if event.split.is_reconciled [

    Reconciled to that count by #event.split.reconciliation_delta units, from #event.split.quantity_before_reconciliation to #event.split.quantity_after_reconciliation.
  ]

  Units in the pool at this point in the day: #event.pool_quantity#if event.pool_has_units [, pool cost: £#event.pool_cost #box[(£#event.pool_per_unit/unit)]].
] else if event.kind == "transfer_to_spouse" [
  #event-heading([Transferred to spouse:], [#event.quantity units of #event.symbol (no gain/no loss)])
  Base cost of £#event.base_cost_passed passes to the recipient.
] else if event.kind == "exempt" [
  #event-heading([Exempt disposal:], [#event.quantity units of #event.symbol], detail: [valued at £#event.proceeds #box[(£#event.per_unit/unit)]#if event.has_fees [, #box[£#event.fees disposal expenses] included in the illustrative calculation]])
  The cost and disposal expenses shown below are used for this illustrative calculation only;
  they are not allowable deductions against other gains.

  #if event.outcome == "gain" [
    Gain is £#event.gain\; the gain or loss is disregarded (exempt asset).
  ] else if event.outcome == "nil" [
    Neither a gain nor a loss; the gain or loss is disregarded (exempt asset).
  ] else [
    Loss is £#event.loss\; the gain or loss is disregarded (exempt asset).
  ]
]

#if event.lines.len() > 0 { lines-table(event) }
]

]

]

#if view.income_days.len() > 0 [
= Interest and Dividends Events

#for day in view.income_days [

== #day.date
#for event in day.events [
#if event.kind == "dividend" [
  *Dividend #event.number:* #event.symbol#if event.is_interest [ (interest)] for £#event.amount#if not event.is_interest [ (tax paid at source: £#event.tax_at_source)]#if event.treaty_country != none [, tax treaty amount: £#event.treaty_amount (treaty rate for #event.treaty_country: #event.treaty_rate%)]
] else if event.kind == "interest" [
  *Interest #event.origin #event.number:* #event.symbol for £#event.amount
] else if event.kind == "interest_tax" [
  *Interest tax #event.origin #event.number:* #event.symbol for £#event.amount
] else if event.kind == "eri_distribution" [
  *Excess Reported Income Distribution #event.number*: #event.symbol#if event.is_interest [ (interest)] else [ (dividend)] for £#event.amount
  #box[(£#event.unit_price/unit)]#if event.estimated_until != none [, estimated at the period-end rate until #box[#event.estimated_until]]
]

]

]
]
