# Review card content specification

> Structure authority is [docs/output-contract.md](../../docs/output-contract.md): its keynote + four-block order outranks the display priorities below, which now rank numbers *inside* the performance block only. Execution authority in v2 is `engine/card_renderer.py` plus `references/card-policy.md`. This file records the design rationale and acceptance boundaries. Agents do not assemble or redact cards manually.

## Purpose

Produce one conclusion card after all required motive questions are answered. The card should connect the user's own numbers to one behavioral leak, one qualitative thesis interpretation, and one user-chosen next-time rule.

The target reader understands a brokerage statement. Use standard account language directly: realized and unrealized P&L, payoff ratio, position, weight, and stop. Translate internal field names and explain academic terms in plain language.

## Required properties

- Lead with account impact, not trade count or win rate.
- Name one real strength before the largest leak.
- Ground the largest leak in an engine-owned number and a concrete transaction when available. When the leak is an averaging-down pattern with a recorded add-motive classification, name that classification beside the dollar cost so outcome does not stand in for process — a reasoned add that still lost money is a different problem from adding only to lower the cost basis.
- Include qualitative motive or thesis interpretation only after the user answers required questions.
- Surface every triggered honesty-ledger limitation in natural prose.
- End with at most one user-chosen if-then rule. Skipping is valid.
- Keep the writing coherent. A card is a story, not several dashboards pasted together.

## Prohibited content

- Raw five-dimension severity tables.
- Raw `thesis_questions` or unanswered questions.
- Internal labels such as `max_pos_pct`, `metric_key`, `baseline`, or implementation notes.
- Agent-computed numbers or rewritten engine facts.
- Several recommendations or action items.
- Buy or sell advice for a security.
- Shaming or personality judgments.
- A rule selected by the agent on the user's behalf.

## Private and public cards

The private card may include account amounts, dates, tickers, position weights, transaction examples, thesis evidence, and the qualitative agent narrative.

The public card is a separately rendered structured view. It excludes:

- absolute amounts and share counts
- exact dates
- tickers and full holdings
- exact position weights
- session IDs
- evidence text and agent-authored free prose

It may retain a fixed, de-identified behavior-pattern sentence plus engine-owned beta and benchmark-excess percentage points. For mixed-market portfolios, public comparison lines use only the market labels and say "market benchmark"; benchmark symbols and holding tickers remain private.

Do not create the public card by applying regular-expression redaction to the private card. Independent rendering prevents portfolio reconstruction and accidental disclosure.

## Numeric truth

The renderer is the only bridge from engine facts to displayed numbers. Agent narrative contains no digits so it cannot become a competing truth source.

Important display priorities:

1. Realized and unrealized P&L, with coverage limitations when triggered.
2. Payoff ratio and average gain/loss, rather than win-count framing.
3. Portfolio versus benchmark and the alpha interval when available.
4. Reliable cash position and account-level performance when the engine allows it.
5. Largest realized drag and its engine-computed counterfactual.
6. ETF portfolio structure and explicit metadata gaps.

When a field is unavailable, omit it or use renderer-owned honesty copy. Never infer a value and never treat missing data as zero.

## Honesty ledger

`build_honesty_ledger()` determines which caveats must appear; the agent writes how each one is said (#82: conditions in the engine, wording with the agent). The Review Plan exposes the triggered keys as `card_plan.required_honesty_keys`; the agent supplies one qualitative, digit-free sentence per key in `narrative.honesty`, and preview fails when any key is missing or extra. Per [output-contract.md §4](../../docs/output-contract.md) (2026-07-22 ruling), the renderer collapses every performance-related sentence into the single Block-1 footnote — none of them ride an individual number anymore, since real high-density accounts fragmented the indicator list when they did. ETF metadata gaps still render beside the ETF facts in Block 2 (a separate, unaffected placement). Neither ever becomes a standalone checklist section. Fixed copy strings exist only as a fallback for re-rendering bundles committed before this contract.

Per-key wording guidance: `alpha_credibility` names the sample or interval limit without calling skill durable, and states that the excess return may still trace mostly to market or sector exposure rather than selection — self-attribution credits skill for wins and blames the market for losses, so this matters most exactly when the period looks good; `sector_attribution` says part of the allocation-versus-selection split is unattributed; `unclassified_drivers` says concentration may be understated; `unrealized_coverage` says unrealized P&L is incomplete; `orphan_sells` says some realized P&L was excluded for lacking a known entry; `currency_mix` says aggregate figures cross currencies; `cash_reliability` says cash lacks a complete anchor and what unlocks it; `acct_perf_basis` says account performance rests on partial cost or FX approximations; `etf_metadata` says missing expense-ratio or tracking-error data was not treated as zero.

Examples include:

- alpha interval is not statistically credible
- unrealized P&L covers only part of the open portfolio
- some drivers lack a sector benchmark
- some instruments are unclassified
- orphan sells imply incomplete transaction history
- currency conversion is approximate
- cash balances or account performance are not fully anchored
- ETF expense ratio or tracking error is missing

The card must state the limitation neutrally and narrowly. It must not guess the cause of an unexplained residual.

## Performance framing

- Compare the held portfolio with the appropriate market benchmark only when engine output supports the comparison.
- In multi-market portfolios, show each market against its own benchmark; never synthesize a total alpha. The renderer consumes `by_market` directly and ignores the top-level compatibility row for these comparison lines.
- Treat account TWR, holding TWR, cash drag, and IRR as different questions. Use only engine-provided values and copy.
- Interpret positive cash drag as protection in a falling market and negative cash drag as diluted participation; do not treat cash as inherently wrong.
- Use alpha capability language only when the engine marks it credible. Otherwise show the interval and uncertainty.

## Prescription boundary

The product coaches process rather than selecting securities. A prescription may:

- amplify a demonstrated strength
- outsource a decision layer that consistently destroys value
- remove a measurable behavioral leak with a mechanical rule

It may not recommend what to buy or sell. Candidate rules must bind to an engine metric so the next review can evaluate them. The user chooses, rewrites, or skips the final rule. A candidate may carry an engine-authored `grounding` sentence citing this period's actual positions; the reusable rule text stays generic (it is what rules.jsonl tracks across weeks), and the grounding renders only on private surfaces, never on the share-safe card.

Where each class renders is fixed by `docs/output-contract.md` §2 and is not a wording choice (#301): an **amplify** row describes what the period proved, so it sits beside the Block-3 `[v]` strength (strongest one only); an **outsource** row is a weakness finding, so it sits under the Block-3 `[X]` hole; a **cut** row is already represented by the rule the engine derived from it, so it is not printed again. Block 4 holds one action. A prescription list beside the committed rule is a regression, not a richer card — it made the card issue several imperatives at once, some of them opposing.

## Rendering

`card_renderer.py` renders every card artifact from one shared structured assembly: the canonical private Markdown (`card-private.md`, the card text source of truth), the separately structured public Markdown, and a styled self-contained HTML card (`card-private.html` at finalize, `card-private-preview.html` at preview). Deliver those artifacts rather than rewriting the card in chat; the per-surface delivery decision tree for agents is `references/card-delivery.md`.

The HTML card renders at the visual level of the `card-template.html` design reference: flat, light and dark via `prefers-color-scheme`, system font stack, a single heading, outlined tags, neutral section surfaces with semantic color only on section labels and P&L accents, no emoji, no icon font, and zero external requests. The rich layout blocks come from the same structured facts both surfaces share (#247): a KPI tile grid for the headline figures, ranked per-instrument money bars with engine behavior tags, the concentration stress row (inside the hole panel only when the top hole is itself a concentration-family dimension; as its own titled section otherwise, so an unrelated leak never absorbs it), and benchmark-comparison attribution bars — each block renders only when its engine fields exist and degrades silently otherwise. Prescription rows are no longer a block of their own (#301); see the prescription boundary below for where each class renders. The engine emits stable English snake_case codes plus raw params for behavior tags, the stress scenario, and prescriptions (#279); the renderer resolves them through `copy/<locale>.json`, so every locale — including English — receives these blocks from its copy file alone. Legacy bundles that persisted pre-#279 zh literals render them verbatim on the zh card only and omit them elsewhere; there is no read-time migration (owner ruling on #279). The document wraps a host-independent widget fragment between `<!-- WIDGET-FRAGMENT-START -->` and `<!-- WIDGET-FRAGMENT-END -->` markers for graphical surfaces. When `pnl_curve.points` carries at least two points, the HTML adds a small inline-SVG P&L sparkline colored by the final sign, with a caption underneath giving the start~end date range and the peak/trough of the same points (#312) — no full axis, just enough context that the line is interpretable; note-form or missing curve data omits the sparkline (and its caption) silently, and a point missing only its date drops the caption while the line itself still renders. This must not create a new user-facing caveat unless the honesty ledger requires one.
