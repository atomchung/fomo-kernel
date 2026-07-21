# Output contract — what a committed review renders, and why

> Status: **draft v1**, encoding the owner rulings recorded in
> [#276](https://github.com/atomchung/fomo-kernel/issues/276) (2026-07-21).
> Once merged, this file is the single authority on output structure.

## 1. Authority

- **This file outranks every other description of output order.**
  `references/card-policy.md` (story sequence) and `card-spec.md` (display
  priorities) remain valid for wording, redaction, and narrative rules, but
  where they imply a different section order or section set, this file wins.
  Both files must carry a one-line pointer back here.
- **The canonical shape is the README demo card** (the text mock card in
  `README.md` / `README.zh-TW.md` and `docs/demo-card.html`). Owner ruling
  2026-07-21: that version is right; later renderer iterations drifted away
  from it. Drift from the demo shape is a bug, not an evolution.
- `card-template.html` stays as HTML design provenance (palette, dark mode),
  synchronized with `card_renderer.render_html()` per CLAUDE.md; it no longer
  defines section order.

## 2. Canonical structure: keynote + four blocks

Every committed review card renders, in this order:

| # | Block | Content | Demo-card anchor |
|---|---|---|---|
| 0 | **Keynote** | One sentence that states the period's most important judgment. | "On paper you're up +$138k, but almost all of it is 'held and never sold'" |
| 1 | **Performance** | Fixed internal order: ① absolute P&L for the period (realized + unrealized) → ② the period's rate of return, annualized (engine: IRR; **presentation must never print the token "IRR"** — use the plain phrase "annualized return" in the card's locale) → ③ comparison vs market: excess in pp, β, what the benchmark did. Period label (date span) is one line at the top of this block — the former market-timeline section collapses into one or two indicators here and is **not** a standalone block. The concentration stress line ("drop 30% → −$X") rides the exposure indicator (final placement follows PR #265). | Total P&L line, win/loss ratio, "Beat the market +247pp · β 2.04 · 30% drawdown = −$50k", α split indent |
| 2 | **Key trades** | Instruments ranked by \|money impact\|, each row = ticker + amount + verdict tag. Motive answers (from Step-2 questions) and exit records attach to the row of the instrument they concern; they are not standalone sections. | "PLTR +$74,058 [v] likely DCA · [!] too heavy 50%" |
| 3 | **Risks & problems** | `[v]` what you did right (top strength) paired with `[X]` the biggest hole. Behavior patterns fold in here. | "[v] averaged down but stayed within cap / [X] position sizing — PLTR 50%" |
| 4 | **Next step** | Exactly one rule to change next period. | "[*] hard-cap any single position at 20%" |

Renderer sections not mapped above (standalone market timeline, standalone
motive/exit/ETF sections, …) merge into the blocks as described or disappear;
they are the accumulation the owner flagged as "each iteration got worse".

## 3. Module × data-prerequisite table

Owner ruling: **the skeleton is constant — constraints cut depth inside a
block, never the block itself.** Each module states what data lights it up.
When the prerequisite is missing, the block renders a single neutral line
("not computable this period: missing Y", localized) — never a prose wall,
never silent omission.

| Module | Lights up when (engine ground truth) | When missing |
|---|---|---|
| Keynote | always (any committed review) | — |
| 1① absolute P&L | trades or snapshot with cost basis | one-line note |
| 1② annualized return | cash-flow anchors complete (deposit/withdraw history; `perf.py` gate, #180) | one-line note; never estimate |
| 1③ vs market | benchmark series resolvable for the period; single-scope (mixed-market keeps per-market rows, #205) | one-line note |
| 1 stress line | `what_if` complete (label+mval+drop30/50+pct; `card_renderer.py:1349`) | omit the line (it decorates an indicator, not a block) |
| 2 instrument rows | currency known and ≥2 diagnosed tickers with nonzero impact (`_instrument_rows`) | one-line note listing what was traded |
| 2 behavior tags | engine per-ticker diagnosis present | row renders without tags |
| 2 motive/exit attachments | Step-2 answers / exit records exist for that ticker | row renders without them |
| 3 strengths + hole | engine diagnosis present | one-line note |
| 4 next step | always — falls back to restating the standing rule when the engine proposes no change | — |
| snapshot route | suppresses history-performance modules by design (`card_renderer.py:1411`) | Block 1 = position-structure baseline only |

Cadence tiers (#237 / PR #277): a tier selects **depth caps** per block (rows
shown, questions asked, whether ③ renders this time) on top of this same
table. Tier semantics land here once #277 merges — TBD marker, do not invent.

## 4. Honesty / caveat placement

- Every honesty-ledger sentence **rides the number it qualifies**: one line,
  indented, immediately under that indicator (demo-card anchor: the α-caveat
  line under the attribution split).
- A caveat that has no host number goes to a footnote line at the end of
  Block 1 — collapsed/`<details>` on HTML, single trailing line on text.
- Hard rule: no caveat prose block in the opening; the 12-key ledger never
  again renders as consecutive paragraphs (root cause B in #276). Per-key
  host-number mapping is an implementation table to be added next to
  `build_honesty_ledger()`, not new SKILL.md prose.

## 5. Language and number rules

Extracted to **[`docs/output-language.md`](output-language.md)** (owner
ruling 2026-07-21: language is a standalone contract — the product must
support any locale, so the engine stays locale-neutral and all user-visible
wording lives in `copy/<locale>.json`). Two rules remain here because they
are structural, not linguistic:

1. Every judgment anchors to a concrete ticker + amount — the reader must
   always know *which trades* a sentence is about.
2. % means absolute return, pp means excess; the first co-occurrence in a
   card carries a half-sentence bridge.

## 6. Narrative red lines

- Coverage gaps the engine chose not to ask about render as neutral facts
  only — never as user fault, never as the keynote (authoring contract,
  `review.py:1472`).
- The opening never complains about data quality (that is §4's footnote job).

## 7. Known debt (not part of the contract's steady state)

- **Locale gaps on the en card** — the engine bakes zh vocabulary into data
  values, so stress/improve/behavior-tag content drops off en cards. Full
  inventory, repair direction, and phase plan: `docs/output-language.md` §3.
  Until repaired, en cards cut depth but must keep the four-block skeleton.

## 8. Enforcement

Today's checks are a **ban list** (`tests/agent/check_card.py` A-series:
no severity tables, no metric keys, no win-rate openings, …), renderer
determinism (`tests/test_card_html.py`), an LLM judge
(`tests/agent/judge_narrative.py`), and human dogfood (`/fomo-qa` receipt).
None of them asserts structure — which is why every drift shipped green.

This contract adds an **S-series** to `check_card.py`:

- S-1 block presence and order (keynote + four blocks).
- S-2 module lighting matches the §3 prerequisite table given the state file.
- S-3 caveat placement (no consecutive caveat paragraphs; none before
  Block 1's indicators).
- S-4 language rules §5.3/§5.4 (jargon tokens, mixed digit styles).

Markdown and HTML cards share one facts assembly (`_card_facts`, #247), so
the same S-assertions run on both surfaces — the HTML card is checked through
the existing `test_card_html.py` parsing path. Narrative tone stays with the
LLM judge and dogfood verdicts: structure is mechanical, prose is judged.

## 9. Ruling log

| Date | Axis | Ruling |
|---|---|---|
| 2026-07-21 | root cause A | Single authority = README demo shape; this file records it. Draft's "policy 9-section vs spec priorities" conflict resolved by subordination (§1). |
| 2026-07-21 | axis 1 | Opening = keynote + performance. (Draft recommendation "lead with top hole" **rejected** — the keynote already carries the top judgment.) |
| 2026-07-21 | axis 1/root cause B | Caveats ride their numbers (§4). |
| 2026-07-21 | axis 2 | Four-block order fixed (§2); hole stays after trades, before next step; stress rides Block 1 exposure (align PR #265); market timeline demoted to Block-1 indicators. |
| 2026-07-21 | axis 2 | Performance internal order: absolute → annualized return (never print "IRR") → vs market. |
| 2026-07-21 | axis 3 | Skeleton constant, constraints cut depth; every module declares its data prerequisite (§3). Tier caps follow #277. |
| 2026-07-21 | axis 4 | Text card is self-sufficient (README text mock is the floor); missing prerequisites render one-line notes. |
| 2026-07-21 | axis 5 | Four owner principles adopted (§5); three small fixes (95% literal, %/pp bridge, option-copy jargon scan) approved in direction. |
| 2026-07-21 | axis 6 | Red lines stay contract + LLM-judge (soft); no preview hard-check for now. |
