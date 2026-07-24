# Layout constraints — what a designer may change, and what is load-bearing

> Written for a design review by someone with no prior context on this
> repository. It states what the review card is, what already constrains its
> layout, and which of those constraints are negotiable.
>
> For the authoritative section order see [output-contract.md](output-contract.md);
> this file exists to make that contract legible to a designer and to say
> *why* each rule is there. Where the two disagree, output-contract.md wins.
> For how to add or change one element without re-tuning its neighbours, see
> [design-guidelines.md](design-guidelines.md).

## 1. What this card is

The product reads a user's trade history and returns **one card per review**.
It is not a dashboard and not a report — it is a single scrollable artifact a
person reads once a week, in about a minute, and then acts on. The whole
product's value proposition is that it ends with **exactly one thing to change
next week**.

That framing drives every layout rule below. Anything that makes the card feel
like a data dump is a product failure, not a style preference.

Two design facts follow from it:

- **Length is a first-class constraint.** The owner's standing ruling is that
  card bloat is the disease being treated. A change that adds lines needs to
  justify itself; a change that removes them usually does not.
- **The reader is the person whose money it is.** They are not analysing a
  portfolio, they are finding out whether they behaved well. Sections are
  ordered as a narrative (what happened → what you traded → what went wrong →
  what to do), not by data type.

## 2. There are two surfaces, and they are not the same design

| | HTML widget | Markdown |
|---|---|---|
| Where | Rendered inline in the chat client | Plain text, and a local `.md` file |
| KPI tiles | **Yes** — a four-tile grid | **No** — no tile grid exists |
| Styling | Full CSS, light + dark | None beyond Markdown |
| Same content? | Same *facts*, different *carriers* | |

This distinction causes most of the subtle rules. A figure that lives in a
tile on HTML has no tile on Markdown, so the Markdown prose must still carry
it. That is why some sentences render on one surface and not the other — it
is deliberate, not drift.

Both surfaces are produced from one assembly function, so a design that only
works on one of them cannot ship.

## 3. Hard structure (mechanically enforced)

Every card renders in this order:

| # | Block | What it carries |
|---|---|---|
| 0 | **Keynote** | One sentence: the period's most important judgment. Then the review window (date span) on its own line. Then one reflective line. |
| 1 | **Performance** | Absolute P&L → annualized return → comparison vs market. On HTML this is a four-tile grid plus prose below it. |
| 2 | **Key trades** | Instruments ranked by size of money impact; each row is ticker + amount + verdict tags. |
| 3 | **Risks & problems** | Up to three panels, in order: `[v]` what you did right, `[X]` the biggest hole, `[?]` patterns detected but not judged. |
| 4 | **Next step** | Exactly one rule to change, and nothing that reads as a second instruction. |
| 5 | **Closing synthesis** | *Optional.* Two to three sentences of cross-section judgment. Renders only when authored; absent means the section does not exist at all — no header, no placeholder. |

Four automated checkers gate this on every render:

- **S-1** — the four mandatory blocks are present, correctly titled, in order.
  The optional fifth may only appear last, and only with its exact title.
- **S-2** — a module that has the data to render must actually render.
- **S-3** — caveat placement: no two consecutive caveat paragraphs, no caveat
  before Block 1, no inline caveat left inside Block 1.
- **S-4** — language rules, including a ban on certain internal jargon
  reaching the card.

**A design that reorders, merges, or renames these blocks fails CI.** Changing
the order is possible, but it is a contract change: it means updating
output-contract.md, the checkers, and the eval definitions together. Treat it
as a decision, not a tweak.

## 4. Where the content comes from (this limits what you can restyle)

Two different producers fill this card, and they have different rules:

- **The engine** computes every number, ranking, threshold, and tag. Nothing
  in the presentation layer may calculate or adjust a figure.
- **An AI agent** writes the narrative sentences — the keynote, the reflective
  line, the strength framing, the closing synthesis. Those fields are under a
  **digit ban**: no numerals, and no spelled-out numeric magnitudes. Numbers
  come only from engine output.

Practical consequence for design: **you cannot move a number into a narrative
sentence, and you cannot ask the narrative to summarize figures.** If a layout
needs a number in a new place, the engine has to produce it there.

There is also an honesty mechanism: when the data has known limits (a short
sample, missing prices, incomplete sector coverage, mixed currencies), the
engine decides that a disclosure is mandatory and the renderer places it. As
of the 2026-07-22 ruling every such sentence collects into **one footnote at
the end of Block 1** rather than interrupting the prose. Disclosures cannot be
dropped by a design; they can be relocated only as a group.

## 5. The visual system that exists today

Small and deliberately plain. It is not a brand system; treat it as a floor,
not a ceiling.

**Colour** — ten CSS custom properties, all themeable, all with light/dark
values:

```
--rc-surface-1  --rc-surface-2  --rc-border  --rc-radius
--rc-text-primary  --rc-text-secondary  --rc-text-muted
--rc-text-accent  --rc-text-success  --rc-text-danger
```

Dark mode is driven by `prefers-color-scheme`. Any new colour must exist in
both themes.

**Spacing, type, and radius are tokenized too** (2026-07-23). Before that date
colour was the only tokenized axis, which is why every layout ruling in §6 had
to name a pixel instead of a scale — 16 distinct spacing values had
accumulated. The scales now are:

```
--rc-sp-1..6    4 8 12 16 20 24 px
--rc-tx-*       micro 11 · small 12 · body 14 · lead 15 · rule 17 · figure 20
--rc-r-*        sm 6 · md 8 · lg 12
```

`13px` and `19px` were retired into `14`/`20`. `17px` exists for exactly one
thing: the Block-4 rule, the card's single L1. A spacing or type declaration
that hardcodes a pixel now fails a test — geometry (bar heights, fixed column
widths, hairline borders) legitimately does not, since those are not positions
on a rhythm scale.

**Layout primitives**

- Metric grid: columns follow the cell count via `data-n` (1–5), 8px gap.
  Every cell is the same three parts — label, one body slot, one sub line
  (capped at two lines). The body slot holds a number, or a line.
- The period curve is one of those cells, placed directly after the P&L
  metric it plots: its line occupies the body slot at the value's height,
  its peak/trough caption occupies the sub. At five cells the row wraps to
  two rows of three and the curve spans two, filling both rows exactly.
- Attribution rows: `1fr 70px`
- Trade rows: flex, with fixed-width ticker (52px) and amount (78px) columns

**Responsive breakpoints** — two, both max-width:

| Breakpoint | What changes |
|---|---|
| 560px | Metric grid drops to two columns; the curve stops spanning |
| 300px | Trade rows wrap, tags shrink to 11px, attribution rows collapse to one column |

The old 380px "drop to one column" tier was removed: a single-column KPI area
turns the block into a long stack, and `data-n` already prevents the empty
columns that tier was working around. The 300px tier came from a squeeze
report and **still has not been confirmed against a real client window
width** — if the actual narrow case is 400–500px, that tier never fires.

**Two copies of the CSS.** The stylesheet exists as a reference HTML template
*and* as string literals inside the renderer. They must stay identical;
a test enforces it. A design handed over as CSS needs to land in both.

## 6. Rulings already made (each one came from a real failure)

These are not preferences. Each was a bug report from dogfooding.

1. **No explanatory Notes on the card.** When something is confusing, the fix
   is to make the sentence itself precise, or to remove the element — never to
   add a line explaining it. Piling on explanations is what made the card
   bloated in the first place.
2. **A tile and the prose beneath it must never state the same figure.** The
   prose's job is what a tile cannot hold: decomposition, a caveat's
   plain-language reading, cross-period narrative. (**Refined 2026-07-24,
   #363**: an interval turned out to be something a tile *can* hold, in a
   short form sized for its sub — see rule 7.)
3. **Everything ranks by size of money impact, never by percentage return.**
   A metric that ranked by return rate was removed outright because it
   surfaced a trivially small trade next to figures ordered by amount.
4. **Lists get a cap and a remainder.** An enumerated dump of every matching
   ticker reads as raw data; the card shows the top few and collapses the
   rest into a "+N more" tail.
5. **Card-level metadata goes at the top, not inside an indicator.** The
   review window was briefly folded into a KPI tile's sub-line; that made one
   cell roughly three times the text of its neighbours, and grid row-stretch
   padded the whole row. It now leads the card. **Superseded 2026-07-23 by
   the cell contract in §5** — this ruling named one field, so the same defect
   came back through another: the sparkline plus its caption held one cell at
   209px and stretched all four to match. The rule is now about what bounds a
   cell's height, not about which field is banned from it.

   A first attempt at that fix banned charts from cells outright and moved the
   curve to a strip of its own. That was the same mistake one level up: it
   named the *chart* instead of the *shape*. The curve then sat nowhere near
   the number it plots. What actually bounds the height is every cell having
   the same three parts, so a chart is welcome in a cell as long as its line
   takes the value's slot and its caption takes the sub's.
6. **A section with nothing to say does not render.** No empty headers, no
   placeholder text, no "N/A".
7. **One value is expressed once per card.** The excess figure used to appear
   three times — as a KPI tile, as a sentence in the prose, and as a display
   figure heading the comparator block. The tile keeps it; the prose keeps
   only what a tile cannot hold (a caveat's reading, a decomposition); the
   comparator headline is gone. The alpha tile's own sub picked up a second
   figure the same way (2026-07-24, #363): the 95% interval, in a short form
   that fits the sub's two-line cap, whenever the data can build one — a
   card with no usable interval falls back to what the sub held before. What
   the sub still cannot hold when the interval does render there — the
   not-yet-credible legend, the negative-interval caveat — is exactly the
   "reading" this rule already meant; it moved to one line below the whole
   grid instead of standing directly under the tile, since two tiles' worth
   of prose no longer fit under just one of them.
8. **Exactly one L1, and it is Block 4.** The product's promise is a single
   action, so the section carrying it has its own ground while every other
   section shares the card surface. Previously Block 3's panels and Block 4's
   rule were the same treatment, which left the one committed action
   indistinguishable from the diagnosis above it.

## 7. What is open for design

Genuinely unresolved, and where an outside eye would help most:

- **Information hierarchy within Block 1.** Still the densest region: tiles,
  a trend strip, prose, a comparator row, and a footnote. The 2026-07-23 pass
  removed the dead space and the duplicate figure, but did not answer whether
  the tile grid is the right primitive for this content in the first place.
- **Density of Block 2.** Each instrument row carries a ticker, an amount, and
  a variable number of verdict tags, some of which now also carry a current
  price and average cost. Rows are single-line by rule, but tag overflow is
  unsolved.
- **Whether the two gap notes should merge.** A month-gated card renders
  "cannot compute annualized return: …" and "cannot compare to market: …" as
  two separate sentences that both say *this period lacks data*. Merging them
  is a copy change, not a layout one, so it was left out of the layout pass.
- **Dark mode has never been reviewed by a designer.** It exists because the
  tokens exist.

Resolved on 2026-07-23, recorded here so a reviewer knows why they look
settled: the `[v]` / `[X]` / `[?]` panels now encode severity in a coloured
left stripe rather than only a bracket glyph, and the card does have a visual
centre of gravity — Block 4 carries its own ground as the single L1.

## 8. What a usable design deliverable looks like here

Because content is engine-produced and structure is contract-enforced, the
most useful output is **not** a pixel comp of one ideal card. It is:

1. A rule set expressed against the block structure in §3 — what each block's
   visual weight should be relative to the others, and why.
2. Token-level changes (colour, scale, spacing) rather than new bespoke
   values, so they survive both surfaces and both themes.
3. Explicit handling of the degenerate cases, which are common in real data:
   a card with no benchmark comparison, a user with one instrument, a
   thin-history first review that legitimately has almost nothing to show, and
   a card where four separate disclosures fire at once.
4. A statement of what to do when content overflows the intended shape — a
   ticker list too long for one line, a narrative sentence longer than
   expected — since the engine cannot guarantee lengths.
