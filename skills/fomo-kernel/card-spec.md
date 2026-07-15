# Review card content specification

> Execution authority in v2 is `engine/card_renderer.py` plus `references/card-policy.md`. This file records the design rationale and acceptance boundaries. Agents do not assemble or redact cards manually.

## Purpose

Produce one conclusion card after all required motive questions are answered. The card should connect the user's own numbers to one behavioral leak, one qualitative thesis interpretation, and one user-chosen next-time rule.

The target reader understands a brokerage statement. Use standard account language directly: realized and unrealized P&L, payoff ratio, position, weight, and stop. Translate internal field names and explain academic terms in plain language.

## Required properties

- Lead with account impact, not trade count or win rate.
- Name one real strength before the largest leak.
- Ground the largest leak in an engine-owned number and a concrete transaction when available.
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

`build_honesty_ledger()` determines which caveats must appear. The renderer integrates them into the relevant narrative section rather than printing the ledger as a checklist.

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
- In multi-market portfolios, show each market against its own benchmark; never synthesize a total alpha.
- Treat account TWR, holding TWR, cash drag, and IRR as different questions. Use only engine-provided values and copy.
- Interpret positive cash drag as protection in a falling market and negative cash drag as diluted participation; do not treat cash as inherently wrong.
- Use alpha capability language only when the engine marks it credible. Otherwise show the interval and uncertainty.

## Prescription boundary

The product coaches process rather than selecting securities. A prescription may:

- amplify a demonstrated strength
- outsource a decision layer that consistently destroys value
- remove a measurable behavioral leak with a mechanical rule

It may not recommend what to buy or sell. Candidate rules must bind to an engine metric so the next review can evaluate them. The user chooses, rewrites, or skips the final rule.

## Rendering

`card_renderer.py` produces canonical Markdown and dependency-free HTML from the same structured content. Deliver those artifacts rather than rewriting the card in the chat. HTML may add a small P&L sparkline when `pnl_curve.points` is available; missing or unsupported curve data should not create a new user-facing caveat unless the honesty ledger requires one.
