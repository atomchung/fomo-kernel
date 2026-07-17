# PRD: snapshot-anchored ledger, markets, currencies, and memory

Status: core ledger, multi-market foundations, and initial snapshot onboarding implemented. Decision date: 2026-07-06; initial adapter completed 2026-07-17. Second and subsequent snapshot reconciliation remains P1.

## Requirements

1. Accept both declared positions and transaction history because most users cannot provide a complete lifetime ledger.
2. Preserve accurate accounting for US and Taiwan markets and multiple currencies.
3. Retain decisions, review conclusions, and card-to-card changes.
4. Support due post-exit checks.
5. Measure swap opportunity cost when one sale funds another purchase.

The core model is: a complete initial declared position snapshot may become the accounting anchor; an incomplete snapshot may produce a bounded review but is not an anchor. Later transactions update the ledger and may unlock supported history-dependent diagnosis. Ledger-derived current holdings remain canonical, and a newer broker view is not considered reconciled until the deferred repeated-snapshot contract compares it explicitly.

## Event model

`~/.trade-coach/ledger.jsonl` is an append-only local event stream with schema versions.

Implemented initial-anchor and transaction events:

```json
{"type":"snapshot","as_of":"2026-07-06","source":"user_declared","positions":[{"ticker":"NVDA","market":"US","currency":"USD","shares":40,"avg_cost":152.3}],"cash":{"USD":8200}}
{"type":"trade","date":"2026-07-08","ticker":"2330.TW","market":"TW","currency":"TWD","action":"BUY","qty":100,"price":985,"fee":42}
```

The intended P1 repeated-snapshot reconciler may emit an explicit adjustment such as the following; the initial adapter does not claim to produce it:

```json
{"type":"adjustment","date":"2026-07-09","ticker":"NVDA","delta_shares":-5,"reason":"reconcile declared snapshot with derived holdings"}
```

## Holding derivation

1. Use the accepted complete initial snapshot as the anchor.
2. Apply only trades with `date > snapshot.as_of`.
3. If no snapshot exists, replay all available trades and mark completeness limitations.

An accepted complete snapshot represents end-of-day state, so same-day trades are already reflected. Missing pre-anchor history is normal and does not invalidate the ledger-derived holdings. An incomplete snapshot does not enter this derivation. A second or subsequent broker snapshot does not supersede the canonical current holdings until the deferred reconciler compares it with the ledger.

## Reconciliation (deferred P1)

The target behavior when the user supplies another position snapshot is:

- If derived and declared holdings agree, mark the ledger reconciled.
- If they differ, show the narrow difference, accept the newer declaration as the new anchor, and write an adjustment event preserving the history.

Do not infer the cause of a mismatch. It may represent a missing trade, transfer, split, fee, or data error.

This diff-and-adjustment path is not implemented by the initial adapter. Until it exists, any claim that ledger holdings match a newer broker view fails closed.

## Separate consumers

| Consumer | Data | Completeness rule |
|---|---|---|
| Accounting and holdings | accepted complete initial anchor plus post-anchor transactions | strict from the anchor forward; ledger-derived holdings stay canonical |
| Behavior diagnosis | all visible transactions | broader sample with explicit gaps |

Missing average cost may still allow market-value concentration but not complete unrealized P&L. Snapshot-origin cycles must indicate left-truncated holding history.

## Snapshot onboarding contract

A position table or screenshot enters through one runtime path. The agent transcribes only broker-declared facts into the normalized JSON envelope, keeps the source local, and calls:

```bash
python3 engine/review.py prepare --route snapshot_review \
  --snapshot-json /tmp/fomo-kernel-positions.json
```

The agent may map broker labels, normalize dates, and complete provider ticker suffixes. It keeps the temporary normalized JSON outside the repository. It may not calculate weights, P&L, cycle IDs, risk metrics, driver concentration, or ETF classifications, and it may not assemble engine card/state artifacts. The engine has no OCR or cloud-upload path; a screenshot is a local agent input, not an engine image format.

The opening portfolio check may claim only engine-owned cost or value weights, single-position risk, driver concentration, ETF structure, and data integrity. It initializes an inferred thesis for every uncovered open cycle and labels the holding history as left-truncated. Averaging-down counts, exit discipline, holding behavior, win rate, payoff ratio, alpha, and historical motives remain unavailable until later transaction history supports them.

A complete initial snapshot may establish the accounting anchor. An incomplete snapshot still yields the bounded opening check but is not projected as an anchor. Later transaction files may unlock supported historical dimensions while ledger-derived current holdings remain canonical. The initial adapter does not compare a second or subsequent snapshot, emit a holdings diff or adjustment, or certify that the ledger matches a fresh broker view.

## Multi-market and currency policy

- Store every event in original currency with explicit `market` and `currency`.
- Normalize Taiwan tickers to the data-provider convention when fetching prices.
- Convert only for aggregate presentation; preserve original-currency detail for brokerage reconciliation.
- Use cached rates offline and disclose the rate date. If no rate exists, show original currencies rather than guessing.
- Compare each market sub-portfolio with its own benchmark. Never synthesize a cross-market total alpha.
- Keep behavioral concentration global because one user can hold the same driver across markets.

## Memory product behavior

The first seconds of a returning review should prove continuity through:

1. the prior commitment and current metric
2. the active thesis and any new evidence
3. the largest structural change since the prior session

Canonical session bundles preserve cards and decisions. Projections provide compatibility and can be rebuilt.

## Revisit and swap

- When shares reach zero or fall past the configured reduction threshold, enqueue post-exit windows from the ledger event.
- Use a bounded historical backlog so cold start does not create an interrogation queue.
- Pair a sale with a nearby purchase as a swap candidate, then require user confirmation.
- Judge a swap by relative outcome, not whether the sold asset rose in isolation.

## Implementation slices

1. Core transaction and initial-anchor ledger event layer. Implemented.
2. Market/currency fields, FX gates, and per-market benchmarks.
3. Event-driven revisit and swap analysis.
4. Canonical card history and progress summary.
5. Initial snapshot adapter for locally normalized screenshot or table onboarding. Implemented 2026-07-17.
6. Second and subsequent snapshot diff, reconciliation, and explicit adjustment/new-anchor events. Deferred P1.

## Non-negotiable boundaries

- All ledger, session, card, and revisit data stays local.
- The ledger is a fact layer, not a new governance wiki or daily net-asset-value system.
- Accounting supports the card; it does not create a second dashboard product.
- Every network dependency has an offline, cache, or explicit-missing path.
