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
- [layout-constraints.md](layout-constraints.md) restates this contract for a
  designer with no repository context: what is load-bearing, what the visual
  system currently is, and which questions are open. It is written to be handed
  to an outside design review; it never overrides this file.

## 2. Canonical structure: keynote + four blocks

Every committed review card renders, in this order:

| # | Block | Content | Demo-card anchor |
|---|---|---|---|
| 0 | **Keynote** | One sentence that states the period's most important judgment, followed by the review window (date span) on its own line. The window scopes every number on the card, so the reader gets it before anything else; it is card-level metadata, not a property of any single indicator (owner ruling 2026-07-22 — §9). It runs from the first trade to the date the card is **valued** at — the latest retrieved close (`price_snapshot.as_of`), not the last trade — whenever that is later, and it is the card's only date range: no indicator states a second window or a day count of its own (owner ruling 2026-07-23 — §9). | "On paper you're up +$138k, but almost all of it is 'held and never sold'" |
| 1 | **Performance** | Fixed internal order: ① absolute P&L for the period (realized + unrealized) → ② the period's rate of return, annualized (engine: IRR; **presentation must never print the token "IRR"** — use the plain phrase "annualized return" in the card's locale) → ③ comparison vs market: excess in pp, β, what the benchmark did (monthly cadence — §3). The market backdrop (the VIX level) is one line at the top of this block on a surface with no vs-market tile to hold it; once the vs-market ("excess") indicator does render, the backdrop folds into that indicator instead of standing as its own line (#344 — see the tile/prose note below the table). The benchmark's own window return used to lead that backdrop and was cut 2026-07-23 (#366 — §9): the card carries no period-scoped personal figure to compare it against. The review window itself is **not** here: it leads the keynote (row 0). The former market-timeline section is **not** a standalone block. The concentration stress line ("drop 30% → −$X") rides the exposure indicator (final placement follows PR #265). | Total P&L line, win/loss ratio, "Beat the market +247pp · β 2.04 · 30% drawdown = −$50k", α split indent |
| 2 | **Key trades** | Instruments ranked by \|money impact\|, each row = ticker + amount + verdict tag. Motive answers (from Step-2 questions) and exit records attach to the row of the instrument they concern; they are not standalone sections. | "PLTR +$74,058 [v] likely DCA · [!] too heavy 50%" |
| 3 | **Risks & problems** | Up to three panels in this order: `[v]` what you did right (top strength, plus at most one `amplify` prescription row — the strongest claim only), `[X]` the biggest hole (plus the `outsource` row when it fired), and `[?]` patterns the engine detected but has not judged. Behavior patterns fold in here. | "[v] averaged down but stayed within cap / [X] position sizing — PLTR 50%" |
| 4 | **Next step** | Exactly one rule to change next period, and nothing that reads as a second instruction. | "[*] hard-cap any single position at 20%" |

**Block 1's KPI tiles and its prose never restate the same figure** (#344,
owner ruling 2026-07-22; generalized to the whole card by the 2026-07-23
layout ruling — §9 R1): the HTML surface renders the KPI tile grid (P&L,
payoff ratio, vs-market excess, annualized alpha) directly above the same
Block-1 lines rendered as prose; a number a tile already carries in full must
not also stand as its own sentence in the prose immediately below it. The
same rule governs the comparator block further down: its headline figure is
the excess the tile already carries, so it renders only on a card with no
excess tile — in practice never, since `_attribution_facts` and the excess
tile read the same field under the same single-scope gate, which is why the
renderer keeps the check as a guard rather than a live branch. The comparator
rows themselves (`vs QQQ`, `vs SOXX`) are alternative benchmarks no tile
shows, so they stay. The
prose keeps only what a tile cannot hold — decomposition (the largest
realized drag and its counterfactual; the allocation/selection split of the
benchmark excess), an interpretive caveat (the alpha confidence interval and
its plain-language reading), or a fact no tile exists for at all (cumulative
and account-level return, cash, the concentration stress line) — never a
second copy of a headline number. The vs-market sentence carries the excess
and β only, which is exactly the excess tile's value and sub, so HTML drops
it whole (#362/#363); it is Markdown's sole carrier of both.
Markdown has no tile grid, so every one of these lines stays its full,
untouched self there — it is that surface's only carrier of the figure, not
a duplicate of anything. The market backdrop — the VIX level; the benchmark's
window return was cut 2026-07-23, see §9 — follows the same rule: it folds
into the vs-market tile's sub line on HTML once that tile renders, and stays a
standalone line — on both surfaces — on any card where no such tile exists this
period (month-gated, mixed-market, or missing benchmark data), since nothing
else would carry it then. The review-window span does **not** follow this rule:
it renders once in the keynote preamble on every card and every surface (owner
ruling 2026-07-22, §9), because it scopes the whole card rather than qualifying
the benchmark comparison.

**Block 3's `[?]` panel** (#303) holds read-only observations — exit
opportunity-cost being the first — that the engine surfaces without a verdict.
Its label must state that no answer is expected, and it must name the
instruments so the pattern is checkable. A pattern the engine *does* want
answered belongs in the question queue, not here: when the review has room in
its density band it queues the grounded `exit_consistency` motive question about
the same facts, and this panel yields to it. A pattern is either answerable this
review (the question) or a read-only observation (this panel), never both.

**Block 4 renders, in order** (#301): the rule, then the positions or behavior
counts it would act on (#302), then — only when the same card credits a
strength the rule appears to contradict — one engine-owned sentence stating the
order of operations. Nothing else. Specifically:

- **No prescription list.** `amplify` rows belong to Block 3's `[v]`,
  `outsource` to Block 3's `[X]`, and `cut_loss` rows are already represented by
  the rule the engine derived from them. Rendering them here produced up to
  five imperatives at once, some of them opposing ("don't let sizing dilute
  your edge" beside "PLTR is too heavy at 49%"), and the reader was left to
  arbitrate. That was the #301 QA finding.
- **`narrative.rule_rationale` is not rendered.** It duplicated the trade-off
  sentence; between an authored restatement and a derived one, the card keeps
  the derived one.
- **The threshold travels inside the rule text** (#317), interpolated from
  `POSITION_CAP`, so the reader is not left recalling what "the cap" was. It is
  a constant, not a per-period fact, so the tracked rule text stays stable
  across weeks. Threshold alignment and user-set caps are tracked in #324; do
  **not** add card copy explaining that the threshold is a generic baseline —
  that is the caveat noise this contract exists to remove.
- **The positions named under the rule are the ones the engine actually
  flagged** (#328): the sizing dimension filters on `OVERSIZE_TRIGGER` (the
  diagnostic line that opens the `cut_oversize` prescription), not on
  `POSITION_CAP` above — a holding between the two was never judged a problem
  by any engine path, so listing it there would make the card stricter than
  the engine's own judgment. Named entries are capped at
  `RULE_TARGETS_DISPLAY_LIMIT` (#349); any remainder collapses into one
  localized "+N more" tail instead of an enumerated dump.

Renderer sections not mapped above (standalone market timeline, standalone
motive/exit/ETF sections, …) merge into the blocks as described or disappear;
they are the accumulation the owner flagged as "each iteration got worse".

### Closing synthesis (optional 5th block)

Owner ruling 2026-07-22
([#345](https://github.com/atomchung/fomo-kernel/issues/345)): the four
blocks above are a diagnosis-then-prescription arc — Performance, Key
trades, and Risks & problems build the picture, and Next step commits one
action. Nothing in that arc synthesizes *across* sections: concentration
level, dollar exposure, and a drawdown scenario each land as separate
sentences, and the reader has to assemble the judgment alone (#345's
trigger finding).

A 5th block, carried by `narrative.synthesis`, may append **after** Next
step — never between existing blocks, and it never reorders or rewords
them. It is a closing reflection, explicitly not an opening hook: the
keynote (Block 0) already carries the period's headline judgment; this
block closes the story after the reader has seen the diagnosis and the one
committed action. It condenses the period's single most important
cross-section judgment into two to three sentences with a point of view —
a synthesis, not a second fact list (that restatement disease is what
[#344](https://github.com/atomchung/fomo-kernel/issues/344) is filed
against). Same authoring contract as every other narrative field:
qualitative only, digit-ban enforced by `card_renderer.validate_narrative`
(§8).

This is not a Note: a Note explains an existing card element (an honesty
caveat, a metadata gap); the closing synthesis says something the rest of
the card does not — the distinction the owner drew when separately
declining to grow the card with explanatory Notes.

Optional and fail-closed: when `narrative.synthesis` is absent or empty,
the block does not exist — no header, no placeholder line — unlike the
four mandatory blocks, which always render at least a neutral one-line
note. An agent is never required to write it; a period with nothing that
rises to a synthesis should simply omit it.

### Markdown reader path

The canonical private Markdown puts a small read-first blockquote after the
keynote and before Block 1: the already-rendered lead line of the Block-3
`[X]` panel and the lead line of the Block-4 `[*]` panel. It is a presentation
projection for conversation-only and CLI fallback, not a fifth content block:
it repeats no calculation, does not create an additional recommendation, and
does not change the four-block order below. This lets a reader find the
headline, one key risk, and the one next rule before scanning performance
detail. Missing diagnostics do not get a synthetic risk summary.

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
| 1③ vs market | benchmark series resolvable for the period **and** the monthly slot is open — first full review of the calendar month, judged by the review's own `date_end` against committed-session history and frozen at prepare into `engine_card.vs_market_gate` (#284; unreadable history fails closed toward showing); single-scope (mixed-market keeps per-market rows, #205) | one-line note only when benchmark data is missing on a review whose monthly slot is open; a month-gated review renders **nothing — no gap note** (absolute P&L + annualized return stand alone) |
| 1 stress line | `what_if` complete (label+mval+drop30/50+pct; `card_renderer.py:1349`) | omit the line (it decorates an indicator, not a block) |
| 2 instrument rows | currency known and ≥2 diagnosed tickers with nonzero impact (`_instrument_rows`) | one-line note listing what was traded |
| 2 behavior tags | engine per-ticker diagnosis present | row renders without tags |
| 2 motive/exit attachments | Step-2 answers / exit records exist for that ticker | row renders without them |
| 3 strengths + hole | engine diagnosis present | one-line note |
| 3 `[v]` amplify row | an `amplify` / `amplify_hypothesis` / `selection_inconclusive` prescription exists (strongest one only, in that order) | panel renders with the strength line alone |
| 3 `[?]` pattern panel | an unjudged pattern fired (today: `sold_winner_early` tags on ≥1 instrument) **and** the review did not queue the answerable `exit_consistency` question about it | omit the panel — either nothing fired, or the question already carries the facts answerably |
| 4 next step | always — falls back to restating the standing rule when the engine proposes no change | — |
| 4 rule targets | the commitment carries a `dim` and that dimension has per-position facts (`risk_weights` over the cap; per-ticker averaging-down counts) | fall back to the aggregate `#248` grounding sentence; never leave the rule unanchored |
| 4 trade-off line | the rule's dimension shrinks a position (`position_sizing` / `diversification`) **and** the card carries an `amplify` row | omit the line — an unconditional one is caveat noise |
| snapshot route | suppresses history-performance modules by design (`card_renderer.py:1411`) | Block 1 = position-structure baseline only |
| structural / empty tier (thin first file) | engine tiers a first review with fewer than `MIN_ROUND_TRIPS` closed round trips (`review._review_tier`, #306); round-trip count decides, span is advisory only | Block 4 = opening-check baseline that names what unlocks the behavioral review (exit, holding, win/loss) — no forced commitment, no question string; other blocks still render whatever the thin file supports |
| 5 closing synthesis | `narrative.synthesis` authored by the agent (#345) — the one module lit by agent judgment rather than an engine data prerequisite | omit the block entirely — no gap note, no header (§2) |

Cadence tiers (#237, wired by #277, all five sub-decisions now ruled):

- **light** (review span ≤5 trading days; auto-detected per #240): capture
  only — no card, no counted question budget, no commitment. The `capture`
  CLI appends motive/emotion facts to the thesis book under a derived
  session id; they reconcile at the next full review. Because no card
  renders, none of this contract's block rules apply to a light session.
- **full**: the keynote + four-block card defined here.
- **Monthly vs-market cadence** (owner ruling 2026-07-21, closing #237
  item 3): the vs-market comparison (excess pp, α, β, attribution split)
  renders on the **first full review of each calendar month**; other full
  reviews render Block 1 with absolute P&L and annualized return only —
  the vs-market lines are simply absent, with no gap note. Short windows
  never render long-window cumulative α/β (the #277 trigger defect).
  Implemented by #284 (after #283): review.py derives "first this month"
  from committed sessions (snapshot and demo sessions do not consume the
  slot; light sessions never finalize a card, so they neither consume nor
  reset it), freezes the decision into `engine_card.vs_market_gate`, and
  requires the segment-hosted honesty keys (`alpha_credibility`,
  `sector_attribution`) only when the segment renders.

## 4. Honesty / caveat placement

> **2026-07-22 ruling reverses this section's 2026-07-21 original**
> (per-number placement, "rides the number it qualifies"). See §9 for the
> reasoning; the rule below is current.

- Every honesty-ledger sentence **collapses into one footnote at the end of
  Block 1** — collapsed/`<details>` on HTML (one bulleted `<li>` per
  sentence, sharing the section's existing `<ul>` bullet styling), one
  bulleted line per sentence on text (demo-card anchor: the "Data notes"
  footnote after the vs-market indicators; 2026-07-22 owner bullet-pass
  ruling, §9). None of them ride an individual indicator line anymore.
- Hard rule: no caveat prose block in the opening; the full honesty ledger
  (the key count grows over time; see `build_honesty_ledger()` for the
  current set) never renders as consecutive paragraphs mid-block (root
  cause B in #276) or as a wall of per-number interruptions (2026-07-22
  reversal, same root cause, reached from real high-density data instead). Per-key disclosure
  *conditions* live in `build_honesty_ledger()` (CLAUDE.md "Honesty decisions
  belong in code"), not new SKILL.md prose — placement itself is now a
  single rule with no per-key table to maintain.
- A Block-1 gap note names the **actual** blocker. When price retrieval
  itself failed (#289), the annualized and vs-market notes say so instead of
  reciting the cash-anchor or benchmark-symbol reason; the renderer selects
  the variant from `engine_card.price_provenance`, never from prose. The
  `price_source` sentence itself collapses into the Block-1 footnote like
  every other honesty key — it rides no indicator line. `build_honesty_ledger()`
  emits it ahead of `unrealized_coverage` (where the prices came from is the
  cause, incomplete coverage is its symptom), and because the footnote lists
  sentences in ledger order, that cause-before-symptom order is preserved.

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

- S-1 block presence and order (keynote + four mandatory blocks, plus the
  optional 5th closing-synthesis block when `narrative.synthesis` is
  authored — #345).
- S-2 module lighting matches the §3 prerequisite table given the state file.
- S-3 caveat placement (no consecutive caveat paragraphs; none before Block 1;
  and — since the 2026-07-22 footnote ruling — none inside Block 1 at all).
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
| 2026-07-21 | language | Resolution order confirmed: conversation-first; stored preference is the non-interactive fallback (`output-language.md` §1). |
| 2026-07-21 | language | Internal canonical language is English; user language exists only at presentation and interaction layers. Check logic must split from language data — parked as #281. |
| 2026-07-21 | language | Dev-phase: persisted zh literals get no compatibility mapping; clean up on demand. |
| 2026-07-21 | axis 3 | Cadence tiers finalized: light (≤5 trading days) = capture-only with no card; full = the four-block card. Vs-market segment renders monthly (first full review each calendar month) — closes #237 item 3; implementation #284. |
| 2026-07-21 | axis 3 | Month-gate implementation landed (#284): the decision is frozen at prepare into `engine_card.vs_market_gate` (fail-closed toward showing on unreadable history); gated reviews render no gap note; `alpha_credibility`/`sector_attribution` are required honesty keys only when the segment renders; S-2 accepts the gate signal and stays strict in both directions. |
| 2026-07-22 | axis 1/root cause B | **Reverses** the 2026-07-21 ruling above: caveats no longer ride individual numbers (§4). Every triggered honesty sentence now collapses into the Block-1 footnote instead. Reason: real high-density accounts (5+ triggered keys) fragmented the indicator list into a wall of one-caveat-per-number interruptions — the same "consecutive paragraph wall" root cause B was meant to prevent, just relocated from the opening into the indicator list. Source: owner_live dogfood on real data, [#276](https://github.com/atomchung/fomo-kernel/issues/276) 2026-07-22 comment (owner: every number was followed by a caveat sentence, interrupting the narrative and hurting readability — this kind of explanatory caveat should move to the top or a collapsed area instead of being interspersed line by line). `_HONESTY_HOSTS`/`_place_caveats` (the per-key → indicator-tag host table) are removed from `card_renderer.py`; the footnote text itself also moved from one joined paragraph to one sentence per line, so collapsing the wall does not just re-form it at the bottom. |
| 2026-07-22 | axis 2 | Mixed-market vs-market rows group visually by market (a `[TW]`/`[US]` label precedes each market's cluster) on both surfaces, but only when 2+ markets actually render — a single-market card (the common case) is unaffected, since there is nothing to disambiguate. Source: same 2026-07-22 #276 comment (owner: the performance section's layout was messy, and the TW and US portions in particular should be split into separate modules rather than interleaved). Pure layout: `alpha_beta_breakdown.by_market` already computes TW and US separately; no engine change. |
| 2026-07-22 | axis 1/§4 follow-up | Owner review of the rendered footnote asked for a further bullet pass: the Block-1 footnote's disclosures and the TW/US-grouped vs-market lines (both, §4/axis 2 above) each render as one bulleted line — reusing the existing `<ul>`/`<li>` markup and its CSS, not a new bullet system. The main Block-1 number lines (absolute P&L, payoff, annualized/account, cash, the stress line) are explicitly unaffected — bullets apply only inside the footnote and inside a rendered TW/US module. A hypothetical future multi-sentence honesty entry (none exist today — every `narrative.honesty` value is one digit-free sentence by contract) would fall back to a plain paragraph instead of one bullet; the always-single-sentence, engine-templated vs-market lines bullet unconditionally, since a decimal-counting exception check would misread their own numbers (e.g. "β 1.10") as a second sentence. |
| 2026-07-22 | new (#345) | Owner ruling: build a closing synthesis this round. `narrative.synthesis` may append as an optional 5th block strictly after Next step — never inserted between, and never reordering or rewording, the four mandatory blocks ruled 2026-07-21. It condenses the period's most important cross-section judgment into two to three sentences with a point of view; it is not a second fact list ([#344](https://github.com/atomchung/fomo-kernel/issues/344)) and not a Note (a Note explains an existing element; this says something new). Absent or empty: the block does not render at all — no header, no placeholder — the same clean-degradation shape as any other optional narrative field. A new schema field (not an extension of `mirror` or `strength`, both of which are already committed to a different, specific placement — `mirror` opens the card, `strength` lives in Block 3's `[v]` panel) keeps each field's job singular; `ALLOWED_NARRATIVE` is still the one source of truth other surfaces (schema, authoring_contract, S-1) derive from or must stay synchronized with. |
| 2026-07-22 | new ([#344](https://github.com/atomchung/fomo-kernel/issues/344)) | Owner ruling: the Performance-block prose must not recite a number its KPI tile already shows in full — the prose's job is what a tile cannot hold (decomposition, an interval and its plain-language reading, cross-period narrative), not a second copy of the tile. `_performance_items()` tags the pnl, payoff, and alpha sentences with the KPI-tile id they would duplicate; `render_html`'s indicator loop drops (pnl/payoff) or trims to its non-duplicate remainder (alpha: keeps the 95% interval and caveat, drops the headline "alpha was +X%" clause) whichever of those a tile actually rendered for. `render_private` (Markdown, no tile grid) is untouched by this mechanism — every sentence stays exactly as before, since it is that surface's only carrier of the figure. The vs-market benchmark sentence (excess pp, β, and the raw portfolio/benchmark returns) is deliberately unchanged on both surfaces: `check_card.py` S-2 needles its exact shape as proof the module rendered, and it also carries the raw returns no tile shows. Separately, the review-window span and the SPY/VIX backdrop (`_period_line`) fold into the vs-market ("excess") KPI tile's sub line via a new `_kpi_tiles(period_line=...)` parameter, once that tile renders; `render_html` drops the standalone line only then, since the tile now carries it — every other card (month-gated, mixed-market, or missing benchmark data) keeps the standalone line on both surfaces, unchanged, since no tile exists there to hold it. Source: [#344](https://github.com/atomchung/fomo-kernel/issues/344), triggered by `/fomo-qa` dogfood (2026-07-22): the code itself already documented the bug — `render_html`'s `kpi_grid()` comment read "the tiles restate the opening indicator lines as the template's KPI row; the lines stay below as the story block." |
| 2026-07-22 | new ([#315](https://github.com/atomchung/fomo-kernel/issues/315)) | Owner comment on #344: a sentence explaining a classification rule ("allocation ETFs are excluded from single-stock concentration: TICKER X%") is not a fact about the user's own behavior — it belongs with the card's other collected disclosures, not standing alone in the body. `_etf_lines()`'s two classification sentences (allocation, concentrated) now render inside Block 2's existing ETF caveat — the same one `etf_metadata` already used — joined into one caveat line rather than one each, so a second "caveat wall" (the root cause the 2026-07-22 footnote ruling above removed from Block 1) does not reappear here. In passing: `_etf_lines()`'s two sentences were hardcoded per-language literals that bypassed `load_copy()` — the only spot in the renderer's Block-1/2 area that did — now routed through a new `copy.etf_classification` key like every other rendered string. A sibling with the same character exists in `render_public()` (~line 2807; a ticker-free variant of the same explanation) but is out of scope here: the public card has no block/caveat system of its own to route it into, and it does not stand beside any other disclosure the way the private card's version did. |
| 2026-07-23 | axis 2 / layout system | Owner ruling: the card's layout rules become a system instead of a case log. Root cause: colour was the only tokenized axis, so every layout ruling had to name a pixel rather than a scale — 16 distinct spacing values and 7 type steps accumulated, and each ruling bound itself to one symptom instead of the mechanism. Measured proof that this recurs: ruling 5 below ("card-level metadata goes at the top, not inside an indicator") removed the review window from a KPI tile, but the same defect returned through a different trigger — the sparkline plus its caption held one tile at 209px, and grid row-stretch padded all four to match, leaving roughly 110px of dead space in each of the other three; separately a month-gated review lights only 2 tiles while `.grid4` still declared `repeat(4,1fr)`, leaving 332px of a 634px row empty. Six rules replace the case log, each stated as *what determines this* and, where possible, machine-checked: **R1** one value is expressed once per card (the attribution headline no longer restates the excess KPI tile — see §2); **R2** a tile is label + value + a sub capped at two lines, so the tallest cell is bounded rather than one field being banned; **R3** the KPI column count equals the number of tiles that lit up (`data-n`), never a fixed four; **R4** exactly one L1 per card and it is Block 4, which now carries its own ground (`.sec.keystep`) — the product promises one action, so nothing may outrank it; **R5** decorative graphics are block-scoped, not indicator-scoped (the curve renders as a `.trend` strip below the grid); **R6** a note attaches to the scope it explains (block-level classification notes sit on a rule at the block's foot, not trailing the last row). Layout tokens `--rc-sp-*` (6 steps), `--rc-tx-*` (6 steps: 13px and 19px retired, 17px named for the L1 rule), `--rc-r-*` (3 steps) land in `_HTML_WIDGET_CSS` and mirror into `card-template.html`. Measured after: tiles 209px → 109px at 760px and 93px at 520px, same-row spread 0, zero clipped subs, no horizontal overflow. Enforced by `test_layout_uses_the_token_scales_not_ad_hoc_pixels` (spacing/type declarations may not hardcode pixels — catches drift the per-selector mirror test cannot, because it fails even when both surfaces drift together), `test_kpi_tile_never_hosts_the_sparkline`, and `test_next_step_is_the_cards_only_emphasis_ground`. Markdown is untouched: R2/R3/R5 are HTML-only (no tile grid exists there), R1 and R6 hold on both surfaces. |
| 2026-07-22 | axis 1 / [#344](https://github.com/atomchung/fomo-kernel/issues/344) follow-up | Owner ruling after seeing the rendered result: the review window (date span) leads the card, in the keynote preamble, rather than riding the vs-market KPI tile's sub line. Two reasons, one measured and one conceptual. Measured: carrying span + β + benchmark return + VIX made that one tile's sub 70 characters against 26 / 21 / 9 for its three neighbours, so it wrapped to roughly three lines and the grid's default row stretch padded the entire tile row. Conceptual: the window scopes every number on the card, so it is card-level metadata, not a property of the benchmark comparison — the reader needs it before the first figure, not beside the third tile. `_period_line()` splits into `_period_span()` (keynote preamble, both surfaces, exactly once per card) and `_market_backdrop()` (benchmark window return + VIX; folds into the excess tile on HTML, holds Block 1's line on Markdown and on any card with no excess tile). This supersedes the span half of the 2026-07-22 #344 ruling above; the prose-must-not-recite-a-tile half stands unchanged. |
| 2026-07-23 | [#363](https://github.com/atomchung/fomo-kernel/issues/363) | Owner ruling: **one date range per card, and it ends where the card's numbers end.** `/fomo-qa` dogfood surfaced two windows on the same card — the keynote span (`engine_state.date_start` → `date_end`, first to last *trade*) and the holdings-only return's own "over the 1296-day window", which is measured to the latest retrieved close. They are not the same stretch for anyone who has not traded recently, and that is the normal case for a long-term holder, not a fixture artifact. Resolution: `_period_span()` runs the window to `price_snapshot.as_of` whenever that is later than `date_end`, because every unrealized figure on the card (market value, exposure, the drawdown scenario, the holdings return) is priced there — labelling the card with trade dates alone understated the stretch its numbers cover. The holdings-return sentence then states no window of its own: with the span ending on the same date the return is measured to (both are `px.index[-1]`), the day count only asked the reader to convert a duration back into a period the card had already given them in dates. Scope guard: this is display-layer only. `engine_state.date_end` keeps its meaning everywhere it is load-bearing — the vs-market month gate (`_vs_market_gate`) and session override order (`(date_end, session_id)`) — and the extension is fail-closed: an as-of date that is absent, earlier, or malformed leaves the span exactly as before, so a bad value can never push the rendered window past the engine's own dates. Still open in #363 and deliberately not decided here: the "time-weighted return" wording (renaming it collides with the vs-market sentence's own comparable-holdings total return — two different numbers that would both read as "cumulative return"), the alpha interval's placement, the account-level gate sentence's tone, and the bullet pass. |
| 2026-07-23 | [#366](https://github.com/atomchung/fomo-kernel/issues/366) | Owner ruling: **cut the benchmark's window return from the market backdrop.** Its charter (#37) was attribution support — "you lost 3%: was that stock selection, or did the market fall 3%?" — and that charter describes a real need. It cannot be served from this card, for two verified reasons. (a) *Nothing period-local to compare it against.* `overview_stats()` computes `total_pnl` as every round trip ever plus every current holding, and the vs-market excess is whole-history as well, so the window return was the only period-scoped figure on a card made entirely of cumulative ones — the reader's half of the comparison ("what I made this period") is not on the card at all. (b) *The motive layer never consumed it.* `market_context` reaches only `_period_span()` and `_market_backdrop()`; question generation does not read it, so the second half of its charter was unwired. Placement compounded both: since #344 it sat in the excess tile's sub, putting a window-scoped return directly under a whole-history excess value, with the card using one label ("same period") for two different measurement windows. Removed from `_market_backdrop()` and from `copy.period.spy` in both locales. The VIX half stays: a volatility *level* carries neither defect. No engine change — `market_context` is computed from the already-fetched shared price frame, so nothing is refetched for it and `_period_span()` still uses its dates as a fallback. [#366](https://github.com/atomchung/fomo-kernel/issues/366) records the condition that earns the number back: a period-local section for it to sit beside, labelled with its own window. |
| 2026-07-23 | [#362](https://github.com/atomchung/fomo-kernel/issues/362) + [#363](https://github.com/atomchung/fomo-kernel/issues/363) | Owner ruling: **one concept, one indicator** — and the return indicators are exactly four: absolute money made or lost, cumulative return, annualized return, and excess return. `/fomo-qa` dogfood found the vs-market sentence still printing in full beside the excess tile that already showed its `+N pp` value and `β` sub. Investigating it surfaced the larger defect: the sentence opened with two absolute total returns (`port_tot`, `spy_tot`), and **`port_tot` is the same concept as the card's cumulative return** (`acct_perf.hold_twr`) computed by a different pipeline — the alpha/beta regression's aligned day set drops days either side lacks data for and days the portfolio moved more than ±50%, so the card answered "what did you make?" with two different numbers (703% and 684% on the dogfood persona) and gave the reader no way to tell why. `port_tot`/`spy_tot` are regression intermediates that leaked onto the card; they are internal again. The excess they feed is a genuinely different concept and stays, with its allocation/selection split as its explanation. What the sentence still states is exactly the excess tile's value and sub, so it takes the pnl/payoff treatment: `kpi_id="excess"` with an empty `html_text` — HTML drops it whole, Markdown keeps it as its only carrier. `check_card.py`'s S-2 needle tracks the new sentence shape in both locales; it reads the Markdown card, which is why the removal does not blind it. Known consequence, accepted: the cumulative return and the excess are measured on different day sets, so subtracting one from the other does not yield the benchmark's return — the card prints no benchmark absolute, so the subtraction is not invited. Printing one would require unifying the basis first, which would break the split's identity (allocation + selection = excess). Still open in #363: the remaining Block-1 items (the "time-weighted return" wording, the alpha interval's placement, the account-gate sentence's tone, the bullet pass) and the second excess indicator — cumulative `+N pp` and annualized α are two indicators for one concept, which this same ruling forbids. |
