# Behavior diagnosis: evaluate actions, not identities

> Design decision from 2026-06-14: do not force users into a single trader type. A person's positions often combine several styles, and hard classification creates avoidable false diagnoses.

## Core model

Diagnose behavior in three layers:

1. **Universal loss mechanisms**: actions that are usually harmful regardless of style, such as revenge trading, uncontrolled averaging down, unbounded sizing, or high turnover without compensating edge.
2. **Context-dependent behaviors**: actions whose meaning depends on a declared strategy, such as buying weakness, buying strength, pyramiding, concentration, or long holding periods.
3. **Instrument-level contradictions**: several tags may apply to the same position. Explain the causal chain rather than assigning one identity to the whole user.

Examples:

- Repeated losing-position adds can create an oversized position. The useful conclusion is the action chain, not "you are a value investor."
- Several AI tickers can still be one concentrated driver exposure.
- A short-term framework that silently turns into a long-term hold after a loss is a time-horizon contradiction.

## Evidence before labels

Ask for motive only where the engine identifies a high-cost contradiction. Do not require the user to label every trade before analysis.

For a losing-position add, distinguish:

- a pre-existing tranche plan
- genuinely new evidence
- a valuation-only change
- price-only averaging
- unresolved or skipped classification

Do not accept self-description as proof. A `new_evidence` classification needs a concrete claim and source that changed a falsifiable part of the thesis.

## Style as context

Style is useful when it changes how a signal should be interpreted:

- Buying near a range high can be disciplined for momentum and inconsistent for a value strategy.
- Buying near a range low can be disciplined for value and dangerous for momentum.
- Concentration can be intentional only when the user can state the thesis, downside, falsifier, and sizing logic.

The engine should expose observations and confidence. The agent asks a focused question when the same signal has opposite meanings under plausible frameworks.

## Output rule

The final card still converges on one largest behavioral leak and one rule. Multi-label diagnosis improves the explanation; it does not justify a longer checklist.

Use plain behavior language:

- Prefer "you kept adding as the position lost money and it became the largest holding."
- Avoid identity labels such as "you are an emotional value trap investor."

## Implementation direction

- Keep stable numeric detection in `engine/trade_recap.py`.
- Keep motive and evidence validation in the v2 review lifecycle.
- Keep instrument-level tags additive rather than mutually exclusive.
- Add new universal loss detectors only when they can be measured and tied to a testable rule.
- Treat style detection as a confidence-bearing observation, not a permanent user profile.
