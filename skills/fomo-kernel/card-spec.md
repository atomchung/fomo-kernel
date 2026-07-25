# Review card content specification

> Structure authority is [docs/output-contract.md](../../docs/output-contract.md);
> execution is `engine/card_renderer.py` plus `references/card-policy.md`. This
> file records what the card is for and how to word the honesty sentences the
> agent owns. Agents do not assemble or redact cards manually.

## Purpose

One conclusion card after the required motive questions are answered, connecting the user's own numbers to one behavioral leak, one qualitative thesis reading, and one rule they chose.

Write for a reader who understands a brokerage statement. Use account language directly — realized and unrealized P&L, payoff ratio, position, weight, stop — and translate internal field names and academic terms into plain language.

## What a good card does

- Leads with account impact, not trade count or win rate.
- Names one real strength before the largest leak. The strength is not politeness; it is what makes the leak audible.
- Grounds the leak in an engine-owned number and a concrete transaction. When the leak is averaging down and an add-motive classification was recorded, name that classification beside the dollar cost — a reasoned add that still lost money is a different problem from adding only to lower the cost basis.
- Adds qualitative motive or thesis interpretation only after the user has answered.
- States every triggered honesty-ledger limitation in natural prose.
- Ends with at most one if-then rule the user chose. Skipping is valid.
- May close with a synthesis (`narrative.synthesis`): two or three sentences condensing the period's most important cross-section judgment — concentration level, dollar exposure, and what a drawdown would mean, for example — into one conclusion. Omit it rather than writing a placeholder.

Keep it coherent. A card is a story, not several dashboards pasted together.

## Not on a card

Raw severity tables, raw or unanswered `thesis_questions`, internal labels such as `max_pos_pct` or `metric_key`, agent-computed or rewritten numbers, several competing action items, buy or sell advice for a security, shaming or personality judgments, and a rule the agent picked on the user's behalf.

## Honesty ledger

`build_honesty_ledger()` decides which caveats must appear; the agent decides how each is said. The Review Plan exposes the triggered keys as `card_plan.required_honesty_keys`, and you supply one qualitative, digit-free sentence per key in `narrative.honesty`. Preview fails on a missing or extra key.

Placement is not yours to choose: the renderer collapses performance-related sentences into the single Block-1 footnote, and ETF metadata gaps render in Block 2 beside the ETF classification lines. Neither becomes a standalone checklist section.

State each limitation neutrally and narrowly. Never guess the cause of an unexplained residual, and never imply the number is wrong — it is uncertain by a stated amount.

| Key | What the sentence should say |
|---|---|
| `alpha_credibility` | Names the sample or interval limit without calling skill durable, and says the excess may still trace mostly to market or sector exposure rather than selection. This matters most exactly when the period looks good, because self-attribution credits skill for wins and the market for losses. |
| `sector_attribution` | Part of the allocation-versus-selection split is unattributed. |
| `unclassified_drivers` | Some instruments are unclassified, so concentration may be understated. |
| `price_source` | Where current prices came from — an external source and its as-of date, or that retrieval failed and no portfolio-level return could be computed. Never read a missing price as a delisting or a zero return. |
| `price_plausibility` | Names the ticker whose supplied close differs sharply from that instrument's own last recorded trade price, and says the source should be re-verified. This is a caveat, not a correction: it never hedges the price actually used on the card. |
| `unrealized_coverage` | Unrealized P&L covers only part of the open portfolio. |
| `orphan_sells` | Some realized P&L was excluded for lacking a known entry, implying incomplete history. |
| `currency_mix` | Aggregate figures cross currencies and conversion is approximate. |
| `cash_reliability` | Cash lacks a complete anchor, and what would unlock it. |
| `acct_perf_basis` | What the account-level figures rest on. Read `status` for the dominant limit and state only that one — see below. |
| `etf_metadata` | Missing expense-ratio or tracking-error data was not treated as zero. |

`acct_perf_basis` statuses:

- `external_flows_absent` — the record holds no deposit or withdrawal at all, so the reconstruction assumes account cash moved only through trading; money paid in during the period understates the return. `data.implied_start_cash_share` says how much of the opening value that assumption carries, so the sentence can be specific about the size of the doubt.
- `estimated_footprint` — each trade's cash effect was estimated as quantity times price because the source carried no broker amount, excluding commissions and taxes.
- Remaining statuses name partial anchors, cost-line pricing, or FX approximation.

## Performance framing

- Compare against a market benchmark only when engine output supports it. In multi-market portfolios each market meets its own benchmark; never synthesize a total alpha.
- Account TWR, holding TWR, cash drag, and IRR answer different questions. Use engine-provided values and copy only.
- Read positive cash drag as protection in a falling market and negative cash drag as diluted participation. Cash is not inherently a mistake.
- Use alpha capability language only when the engine marks it credible; otherwise show the interval and the uncertainty.

## Prescription boundary

This product coaches process rather than selecting securities. A prescription may amplify a demonstrated strength, outsource a decision layer that consistently destroys value, or remove a measurable leak with a mechanical rule. It may not recommend what to buy or sell.

Candidate rules bind to an engine metric so the next review can evaluate them. The user chooses, rewrites, or skips. A candidate may carry an engine-authored `grounding` sentence citing this period's actual positions; the reusable rule text stays generic, because that generic text is what `rules.jsonl` tracks across weeks, and the grounding renders only on private surfaces.

Where each class renders is fixed by `docs/output-contract.md` §2, not a wording choice: an **amplify** row describes what the period proved, so it sits beside the Block-3 strength; an **outsource** row is a weakness finding, so it sits under the Block-3 hole; a **cut** row is already represented by the rule the engine derived from it and is not printed twice. Block 4 holds one action — a prescription list beside the committed rule makes the card issue several imperatives at once, some of them opposing.

## Rendering

`card_renderer.py` renders every artifact from one shared structured assembly: canonical private Markdown (the card text source of truth), the independently structured public Markdown, and a self-contained HTML card. Deliver those artifacts rather than rewriting the card in chat; the per-surface decision tree is `references/card-delivery.md`.

The HTML card follows `card-template.html`: flat, light and dark via `prefers-color-scheme`, system fonts, one heading, outlined tags, semantic color only on section labels and P&L accents, no emoji, no icon font, zero external requests. Rich blocks — the KPI tile grid, ranked per-instrument money bars, the concentration stress row, benchmark attribution bars, and an optional P&L sparkline — each render only when their engine fields exist and degrade silently otherwise. The document wraps a host-independent widget fragment between `<!-- WIDGET-FRAGMENT-START -->` and `<!-- WIDGET-FRAGMENT-END -->` markers for graphical surfaces. Behavior tags, stress scenarios, and prescriptions travel as stable English snake_case codes plus raw params and resolve through `copy/<locale>.json`, so every locale including English gets these blocks from its copy file alone.
