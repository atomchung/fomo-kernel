# PRD: snapshot-anchored ledger, markets, currencies, and memory

Status: core ledger, multi-market foundations, initial snapshot onboarding, and second-snapshot reconciliation implemented. Decision date: 2026-07-06; initial adapter completed 2026-07-17; repeated-snapshot reconciliation completed 2026-07-18 (#220).

## Requirements

1. Accept both declared positions and transaction history because most users cannot provide a complete lifetime ledger.
2. Preserve accurate accounting for US and Taiwan markets and multiple currencies.
3. Retain decisions, review conclusions, and card-to-card changes.
4. Support due post-exit checks.
5. Measure swap opportunity cost when one sale funds another purchase.

The core model is: a complete initial declared position snapshot may become the accounting anchor; an incomplete snapshot may produce a bounded review but is not an anchor. Later transactions update the ledger and may unlock supported history-dependent diagnosis. Ledger-derived current holdings remain canonical, and a newer broker view is considered reconciled only after the repeated-snapshot contract below compares it explicitly.

## Event model

`~/.trade-coach/ledger.jsonl` is an append-only local event stream with schema versions.

Implemented anchor and transaction events:

```json
{"type":"snapshot","as_of":"2026-07-06","source":"user_declared","positions":[{"ticker":"NVDA","market":"US","currency":"USD","shares":40,"avg_cost":152.3}],"cash":{"USD":8200}}
{"type":"trade","date":"2026-07-08","ticker":"2330.TW","market":"TW","currency":"TWD","action":"BUY","qty":100,"price":985,"fee":42}
```

The repeated-snapshot reconciler emits two additional event shapes, both engine-assigned and content-addressed so replays are no-ops. A clean comparison appends a reconciliation mark; a difference appends one adjustment event carrying the complete narrow diff, followed by the newly declared snapshot as the new anchor:

```json
{"type":"reconciliation","date":"2026-07-15","status":"reconciled","declared_snapshot_id":"snapshot-...","against":{"as_of":"2026-07-06","snapshot_id":"snapshot-..."},"reconciliation_id":"reconcile-...","session_id":"..."}
{"type":"adjustment","date":"2026-07-15","reason":"snapshot_reconciliation","declared_snapshot_id":"snapshot-...","against":{"as_of":"2026-07-06","snapshot_id":"snapshot-..."},"diff":{"positions":[{"ticker":"NVDA","kind":"shares","derived":40.0,"declared":35.0}],"cash":[]},"adjustment_id":"adjust-...","session_id":"..."}
```

## Holding derivation

1. Use the accepted complete initial snapshot as the anchor.
2. Apply only trades with `date > snapshot.as_of`.
3. If no snapshot exists, replay all available trades and mark completeness limitations.

An accepted complete snapshot represents end-of-day state, so same-day trades are already reflected. Missing pre-anchor history is normal and does not invalidate the ledger-derived holdings. An incomplete snapshot does not enter this derivation. A second or subsequent broker snapshot does not supersede the canonical current holdings until the reconciler compares it with the ledger; adoption then flows through the same `latest_anchor` ordering (`as_of` first, `projection_sequence` for same-day declarations).

## Reconciliation (implemented 2026-07-18, #220)

The target behavior when the user supplies another position snapshot is:

- If derived and declared holdings agree, mark the ledger reconciled.
- If they differ, show the narrow difference, accept the newer declaration as the new anchor, and write an adjustment event preserving the history.

Do not infer the cause of a mismatch. It may represent a missing trade, transfer, split, fee, or data error.

Implementation contract (`ledger.snapshot_reconciliation`, entered only through `review.py prepare --snapshot-json`):

- The comparison is time-aligned: derived holdings are computed as of the declared end-of-day `as_of`, so ledger trades dated after it are not part of the comparison and still apply on top of an adopted anchor.
- The narrow diff lists per-ticker shares (`SHARES_TOL`), market, currency, and avg_cost differences, tickers present on only one side, and per-currency cash differences (`CASH_TOL`). Every value stays in its original currency; avg_cost is compared only when both sides state a number, and an omitted declared cash object is treated as no claim.
- Prepare freezes the diff and verdict into the Review Plan; finalize recomputes it under the root projection lock and fails closed if the ledger changed in between, so an unpreviewed adjustment can never be written.
- The clean path appends only the content-addressed reconciliation mark: the anchor, its cycle identities, and the root-wide `projection_sequence` counter stay untouched.
- Fail-closed edges: an incomplete second declaration is rejected, a declaration older than the current anchor is rejected, and history without a complete anchor (replay-only trades or an unrepaired ledger projection) keeps the original initial-onboarding rejection.

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

A complete initial snapshot may establish the accounting anchor. An incomplete snapshot still yields the bounded opening check but is not projected as an anchor. Later transaction files may unlock supported historical dimensions while ledger-derived current holdings remain canonical. A second or subsequent complete snapshot enters through the same command and is routed to the reconciliation contract above; only that comparison may certify that the ledger matches a fresh broker view or adopt the newer declaration.

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
6. Second and subsequent snapshot diff, reconciliation, and explicit adjustment/new-anchor events. Implemented 2026-07-18 (#220).

## Non-negotiable boundaries

- All ledger, session, card, and revisit data stays local.
- The ledger is a fact layer, not a new governance wiki or daily net-asset-value system.
- Accounting supports the card; it does not create a second dashboard product.
- Every network dependency has an offline, cache, or explicit-missing path.
