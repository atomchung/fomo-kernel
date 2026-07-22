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
| 1 | **Performance** | Fixed internal order: ① absolute P&L for the period (realized + unrealized) → ② the period's rate of return, annualized (engine: IRR; **presentation must never print the token "IRR"** — use the plain phrase "annualized return" in the card's locale) → ③ comparison vs market: excess in pp, β, what the benchmark did (monthly cadence — §3). Period label (date span) is one line at the top of this block — the former market-timeline section collapses into one or two indicators here and is **not** a standalone block. The concentration stress line ("drop 30% → −$X") rides the exposure indicator (final placement follows PR #265). | Total P&L line, win/loss ratio, "Beat the market +247pp · β 2.04 · 30% drawdown = −$50k", α split indent |
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
| 1③ vs market | benchmark series resolvable for the period **and** the monthly slot is open — first full review of the calendar month, judged by the review's own `date_end` against committed-session history and frozen at prepare into `engine_card.vs_market_gate` (#284; unreadable history fails closed toward showing); single-scope (mixed-market keeps per-market rows, #205) | one-line note only when benchmark data is missing on a review whose monthly slot is open; a month-gated review renders **nothing — no gap note** (absolute P&L + annualized return stand alone) |
| 1 stress line | `what_if` complete (label+mval+drop30/50+pct; `card_renderer.py:1349`) | omit the line (it decorates an indicator, not a block) |
| 2 instrument rows | currency known and ≥2 diagnosed tickers with nonzero impact (`_instrument_rows`) | one-line note listing what was traded |
| 2 behavior tags | engine per-ticker diagnosis present | row renders without tags |
| 2 motive/exit attachments | Step-2 answers / exit records exist for that ticker | row renders without them |
| 3 strengths + hole | engine diagnosis present | one-line note |
| 4 next step | always — falls back to restating the standing rule when the engine proposes no change | — |
| snapshot route | suppresses history-performance modules by design (`card_renderer.py:1411`) | Block 1 = position-structure baseline only |

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
- Hard rule: no caveat prose block in the opening; the 12-key ledger never
  renders as consecutive paragraphs mid-block (root cause B in #276) or as a
  wall of per-number interruptions (2026-07-22 reversal, same root cause,
  reached from real high-density data instead). Per-key disclosure
  *conditions* live in `build_honesty_ledger()` (CLAUDE.md "Honesty decisions
  belong in code"), not new SKILL.md prose — placement itself is now a
  single rule with no per-key table to maintain.

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
