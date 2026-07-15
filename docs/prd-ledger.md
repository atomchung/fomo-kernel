# PRD: snapshot-anchored ledger, markets, currencies, and memory

Status: core ledger and multi-market foundations implemented; complete snapshot onboarding remains P1. Decision date: 2026-07-06.

## Requirements

1. Accept both declared positions and transaction history because most users cannot provide a complete lifetime ledger.
2. Preserve accurate accounting for US and Taiwan markets and multiple currencies.
3. Retain decisions, review conclusions, and card-to-card changes.
4. Support due post-exit checks.
5. Measure swap opportunity cost when one sale funds another purchase.

The core model is: a declared position snapshot is the accounting anchor; later transactions update it. Behavioral diagnosis may still use all visible transactions with explicit completeness limits.

## Event model

`~/.trade-coach/ledger.jsonl` is an append-only local event stream with schema versions.

```json
{"type":"snapshot","as_of":"2026-07-06","source":"user_declared","positions":[{"ticker":"NVDA","market":"US","currency":"USD","shares":40,"avg_cost":152.3}],"cash":{"USD":8200}}
{"type":"trade","date":"2026-07-08","ticker":"2330.TW","market":"TW","currency":"TWD","action":"BUY","qty":100,"price":985,"fee":42}
{"type":"adjustment","date":"2026-07-09","ticker":"NVDA","delta_shares":-5,"reason":"reconcile declared snapshot with derived holdings"}
```

## Holding derivation

1. Use the latest snapshot as the anchor.
2. Apply only trades with `date > snapshot.as_of`.
3. If no snapshot exists, replay all available trades and mark completeness limitations.

A snapshot represents end-of-day state, so same-day trades are already reflected. Missing pre-anchor history is normal and does not invalidate current holdings.

## Reconciliation

When the user supplies another position snapshot:

- If derived and declared holdings agree, mark the ledger reconciled.
- If they differ, show the narrow difference, accept the newer declaration as the new anchor, and write an adjustment event preserving the history.

Do not infer the cause of a mismatch. It may represent a missing trade, transfer, split, fee, or data error.

## Separate consumers

| Consumer | Data | Completeness rule |
|---|---|---|
| Accounting and holdings | anchor plus post-anchor transactions | strict from the anchor forward |
| Behavior diagnosis | all visible transactions | broader sample with explicit gaps |

Missing average cost may still allow market-value concentration but not complete unrealized P&L. Snapshot-origin cycles must indicate left-truncated holding history.

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

1. Ledger event layer and reconciliation.
2. Market/currency fields, FX gates, and per-market benchmarks.
3. Event-driven revisit and swap analysis.
4. Canonical card history and progress summary.
5. Complete snapshot adapter for direct screenshot or table onboarding.

## Non-negotiable boundaries

- All ledger, session, card, and revisit data stays local.
- The ledger is a fact layer, not a new governance wiki or daily net-asset-value system.
- Accounting supports the card; it does not create a second dashboard product.
- Every network dependency has an offline, cache, or explicit-missing path.
