# Design guidelines — module contracts and how to add a visual element

> **What this file is for.** The other two layout documents describe *what the
> card renders* ([output-contract.md](output-contract.md), the authority on
> structure and order) and *what a designer may change*
> ([layout-constraints.md](layout-constraints.md)). Neither says how to add or
> change one visual element **without having to re-tune the ones around it**.
> That is this file's only job. It does not restate section order, and it never
> overrides output-contract.md.

## 1. The problem this exists to prevent

Before 2026-07-23 the card could only be changed a little at a time, and each
small change forced unrelated parts to be re-adjusted. The cause was not any
one bad decision — it was that **modules were coupled through side effects
nobody had written down**:

- A grid row stretches to its tallest cell, so the one cell that carried a
  chart plus a caption silently set the height of all four. Fixing "the review
  window must not sit in a tile" fixed one trigger; the same defect returned
  through the sparkline. Two more attempts (a full-width strip, then a hero
  band) each solved the stated problem and created a new one.
- Spacing and type were raw pixels, so changing the rhythm meant editing every
  component instead of one scale.
- The column count was hardcoded, so every data scenario needed its own
  special case — and the most common scenario got none, leaving half a row
  empty.

Each of those is the same shape of bug: **a rule that named a symptom instead
of the mechanism**. This file exists so the next element can be added by
filling an existing contract rather than by discovering the coupling the hard
way.

## 2. The core idea: modules own slots, elements fill slots

A module is a container with a **fixed set of slots and a fixed shape**. A new
visual element is added by **filling an existing module's slots**, not by
inventing a new module.

The metric cell is the worked example. Its contract is three slots:

| Slot | Holds | Constraint |
|---|---|---|
| label | one short line | always present |
| body | one figure **or one graphic** | fixed height, matches the figure's line box |
| sub | one qualifying line | capped at two lines, wraps rather than truncating |

The period curve is not a new module. It is a metric cell whose body slot
holds a line instead of a number and whose sub slot holds the peak/trough
instead of a decomposition. Because it fills the same three slots at the same
heights, it cannot change the height of its neighbours — which is exactly what
the earlier standalone-strip and hero-band attempts could not guarantee.

**Test before adding a new module type:** can the element be expressed as an
existing module's slots? If yes, it must be. A new module type is a contract
change: it needs its own slot definition, its own mechanical check, and an
entry in §7.

## 3. What keeps modules independent

Three mechanisms, all already in place. Anything new must not undermine them.

### 3.1 Uniform shape within a container

Every child of a container has the same slot structure. This is what makes
`align-items: stretch` safe: if all cells are three slots at fixed heights, no
cell can pad another. The moment one child carries more parts than its
siblings, the container's sizing becomes a coupling channel.

### 3.2 Tokens, not raw values

Spacing, type, and radius come from `--rc-sp-*`, `--rc-tx-*`, `--rc-r-*`.
A rhythm change edits the scale, not the components. A declaration that
hardcodes a pixel re-establishes the coupling and fails
`test_layout_uses_the_token_scales_not_ad_hoc_pixels`.

Raw pixels remain correct for geometry that is not a position on a rhythm
scale: bar heights, fixed column widths, hairline borders, pill radii, and
media-query breakpoints.

### 3.3 Container parameters derived from data, not from scenario

The metric grid takes `data-n` — the number of cells that actually lit up —
and derives its column count from it. No scenario needs its own rule, and a
scenario nobody anticipated still lays out correctly. Prefer one derived
parameter over a table of per-scenario cases.

## 4. Adding a visual element

1. **Locate the module.** Which existing container does it belong to, and can
   it be expressed in that module's slots? If yes, stop here and fill them.
2. **Check the neighbours.** Does the element change the container's sizing for
   anything else? If it can, the shape is wrong — rework it to fit the slot
   contract instead of adding a special case for the container.
3. **Use the scales.** No new spacing or type value unless the scale genuinely
   lacks a step; adding a step is a decision, because both stylesheets must
   carry it.
4. **Derive, do not enumerate.** If behaviour differs across scenarios, find
   the data property that explains the difference and derive from it.
5. **Synchronize the mirrored surfaces** — `card_renderer.py`'s
   `_HTML_WIDGET_CSS` and `card-template.html` (enforced per selector), and the
   Markdown path if the element carries a fact rather than a decoration.
6. **Add the mechanical check** (§7) and mutation-test it: break the rule on
   purpose and confirm the test fails.
7. **Measure, do not eyeball.** Render the real fixtures and measure geometry
   across cell counts, both locales, and at least two viewport widths.

## 5. What not to do

- **Do not write a rule that names a field.** "The review window may not sit
  in a tile" only blocks one trigger. Name the mechanism: what determines the
  cell's height.
- **Do not let one module's content decide another module's size.** If that is
  possible, the slot contract is not being honoured.
- **Do not compute in the presentation layer.** Every number comes from the
  engine; the renderer places it. A layout that needs a new number needs an
  engine field.
- **Do not state one value twice.** A figure has exactly one home per card;
  the other places describe what that home cannot hold.
- **Do not let a hand-written parallel stylesheet become a second source of
  truth.** The Claude Design bundle (`tools/design_bundle.py`) derives its CSS
  from `_HTML_WIDGET_CSS` at build time (#368 Phase 1) instead of hand-copying
  it — rerun the tool after a runtime CSS change to refresh `ds-bundle/`.
  `card-template.html`'s `.rc`-scoped rules are held equal to the runtime's,
  declaration for declaration, by
  `test_widget_fragment_css_stays_mirrored_with_card_template`, so a change to
  one that is not mirrored in the other fails the suite instead of drifting
  silently.

## 6. Module inventory

Current containers and the slots they own. Adding to this table is a contract
change; filling existing slots is not.

| Module | Slots | Owns | Must not |
|---|---|---|---|
| Section (`.sec`) | title, content | vertical rhythm between blocks | reorder or rename blocks (output-contract.md §2) |
| Metric cell (`.m`) | label, body, sub | one figure or one graphic | exceed the three slots; host a second body |
| Metric grid (`.kpi`) | cells | column count from `data-n` | hardcode a column count |
| Verdict panel (`.panel`) | label, lines | severity via left stripe + label colour | carry an action; that is Block 4's |
| Rule block (`.rule` in `.keystep`) | label, rule, grounding | the card's only L1 | appear more than once |
| Trade row (`.trow`) | ticker, amount, tags, sub | one instrument's money impact | become a second ranking system |
| Footnote (`.fnote`) | summary, list | every triggered disclosure, collapsed | let a disclosure ride an indicator line |
| Block note (`.cavt`) | one line | a note scoped to the whole block | trail the last row as if it qualified it |

## 7. Mechanical checks

A module contract that is not checked will drift. Current coverage:

| Contract | Check |
|---|---|
| Cells are uniform three-slot shapes | `test_every_kpi_cell_has_the_same_three_part_shape` |
| Cells in a row are the same height | measured spread = 0 across cell counts and locales |
| A sub's content fits its two-line cap at the narrowest supported width, both locales, realistic magnitudes | `test_pnl_and_payoff_tile_subs_fit_the_two_line_cap_at_narrow_widths` |
| Column count equals cell count | `test_kpi_dashboard_uses_metric_boxes_not_flat_paragraphs` |
| Spacing and type come from the scales | `test_layout_uses_the_token_scales_not_ad_hoc_pixels` |
| Exactly one L1, and nothing after it | `test_next_step_is_the_cards_only_emphasis_ground` |
| Runtime and template's `.rc`-scope agree, declaration for declaration, both directions | `test_widget_fragment_css_stays_mirrored_with_card_template` |
| One value, one home | attribution headline assertion in `test_rich_layout_renders_template_blocks_from_shared_facts` |

Not yet mechanical, and therefore still able to drift: note scoping (§6's
"Block note"), and the visual weight ordering between L2/L3/L4.

## 8. Known convergence debt

The card currently speaks three visual languages for "relative magnitude" —
`.spark` (a line), `.track`/`.fill` (trade-row bars), and `.abar` (comparator
bars). `.track` and `.abar` encode the same kind of quantity with different
styling. Converging them into one bar primitive would remove a whole class of
"which bar style does this use" decisions from future work. Tracked in
[#359](https://github.com/atomchung/fomo-kernel/issues/359).
