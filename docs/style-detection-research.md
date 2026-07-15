# Research: detecting style from transaction history

Status: research input for multi-lens P1. The goal is to detect behavior axes on which legitimate philosophies disagree, not to assign a permanent trader identity.

## Universal versus style-dependent signals

Most transaction signals mix a universal loss mechanism with a style choice. Separate them before applying a lens.

| Signal | Universal component | Style component |
|---|---|---|
| Entry location | minimal | strength/breakout versus weakness/discount |
| Exit behavior | anchoring to breakeven or refusal to realize error | let winners run versus harvest mean reversion |
| Add direction | escalating martingale size, recovery motive, or missing thesis | pyramid strength versus add at verified discount |
| Turnover | uncompensated cost is broadly harmful | only justified by demonstrated short-horizon edge |
| Holding period | silent horizon drift is harmful | the chosen horizon itself is a strategy preference |
| Concentration | unbounded downside and correlated drivers are universal risks | intentional concentration may be style-consistent |

Universal components remain reviewable under every lens. Only the style component may create a defend question or divergent interpretation.

## Best first style axis: entry relative position

Entry location is the cleanest mechanical disagreement:

- Momentum interprets buying near strength or a breakout as confirmation.
- Value or contrarian approaches interpret the same entry as paying too much and prefer verified weakness or discount.

Candidate engine features:

- entry price divided by trailing 252-day high
- skip-month formation return over a defined lookback
- range percentile as a secondary feature

Every output must state its lookback. The same trade can look like momentum over six months and mean reversion over one month or three years.

Use a confidence gate. Per-trade percentiles are noisy and transactions in one ticker are not independent. Small samples should create a weak observation, not a diagnosis.

## Exit behavior

Separate:

- universal disposition effects: selling near breakeven, refusing to realize a loss, or moving the horizon after the trade fails
- style choice: harvesting mean reversion versus letting a trend continue

Measure the user's rule and outcome rather than assuming every early winner exit is wrong.

## Add behavior

Separate:

- universal failure: size escalates as evidence weakens, the goal is merely to recover, or no falsifier exists
- style choice: adding at a verified valuation discount versus pyramiding after strength confirms the thesis

The v2 thesis decision enum and evidence gate provide the qualitative ground truth needed to interpret the mechanical direction.

## Methodological guardrails

1. Report behavior tendencies, not identity labels.
2. Keep lookbacks, data coverage, and sample size explicit.
3. Avoid inferring intention from price direction alone.
4. Treat transactions in one driver as correlated evidence.
5. Validate against synthetic opposite-style fixtures and real user review.
6. Keep style selection out of numeric facts and ETF policy.
7. If lenses agree, do not manufacture a comparison.

## Recommended implementation order

1. Add entry-relative-position features behind an observation-only gate.
2. Test opposite interpretations with verified momentum and value lenses.
3. Add the user answer to the thesis record rather than a permanent profile label.
4. Allow multi-lens comparison only when the style signal is confident and the selected lenses disagree.

## Research basis

The original study drew on momentum, reversal, disposition-effect, and turnover literature, including Jegadeesh and Titman, George and Hwang, De Bondt and Thaler, Odean, and Frazzini. Recheck the primary papers before publishing numeric claims or quotations in GTM material.
