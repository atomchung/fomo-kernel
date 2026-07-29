# PRD: snapshot-anchored ledger, markets, currencies, and memory

Status: core ledger, multi-market foundations, initial snapshot onboarding, and second-snapshot reconciliation implemented. Decision date: 2026-07-06; initial adapter completed 2026-07-17; repeated-snapshot reconciliation completed 2026-07-18 (#220).

## Requirements

1. Accept both declared positions and transaction history because most users cannot provide a complete lifetime ledger.
2. Preserve accurate accounting for US and Taiwan markets and multiple currencies.
3. Retain decisions, review conclusions, and card-to-card changes.
4. Support due post-exit checks.
5. Measure swap opportunity cost when one sale funds another purchase.

The core model is: every accepted source records the book at the time it arrives — a declared position snapshot records it as a declaration, a transaction file records the book its own replay derives (#549) — and which kind of source it was never decides whether it may anchor or be analyzed. Later transactions update the ledger and may unlock supported history-dependent diagnosis. Ledger-derived current holdings remain canonical, and a newer broker view is considered reconciled only after the repeated-snapshot contract below compares it explicitly.

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

A fourth engine-assigned shape records a disappearance the user confirmed as a sale, when the record holds no fill for it (#485 Slice C). Only finalize appends it, and it carries no price and no quantity — the omission is structural, not a convention: `ledger.build_position_absence` validates its own key set against `ABSENCE_KEYS`/`ABSENCE_FORBIDDEN_KEYS` and raises rather than persist a manufactured fill. This is the same prohibition `condition-check.schema.json` states about engine-written verdicts, applied to accounting: win rate, payoff and exit discipline may never be computed from a number nobody supplied.

```json
{"type":"position_absence","date":"2026-07-15","ticker":"ACME","cycle_id":"ACME#2026-06-30#1","absence_id":"absence-...","session_id":"..."}
```

Like `adjustment`, it stays out of holding derivation: the accompanying new anchor is what moves the book, and applying both would double-count. Its purpose is to be *readable* — `revisit.absence_exits` turns it into a real exit row (`kind: "full"`, `exit_price: null`), so the confirmation the user gave is not a written-never-read field (#429's failure class). The shares, currency, market and cost basis that row carries are copied from the recorded book as it stood immediately before the row was appended, never inferred from a price. Positions the user keeps because the new view simply did not capture them are carried into the new anchor with `"carried": true` on the position row; the flag is written only when true, because every snapshot payload is content-addressed and an unconditional `false` would rewrite every existing `snapshot_id`.

The mirror-image answer rides the same anchor row (#531). A position that *appeared* in the declaration is confirmed too, and the user's rough answer to "how long have you held this" becomes `"since"` plus `"since_basis"` on that position — the conversion from months to a date happens in `book_refresh._months_before`, never in the agent. The two keys are one fact and `snapshot_adapter` validates them as a pair: `since` cannot exist without the `since_basis: "user_estimate"` stamp beside it, which is how "a reconstructed start date is never rendered as an exact date" holds at the storage layer rather than as a rule every renderer has to remember. `since_basis: "unknown"` carries no date at all and `ledger.derive_holdings` turns it into the two-segment `ticker#unknown` cycle that already exists for an undatable open. Like `carried`, both are written only when they apply, for the same content-addressing reason; and like `carried`, neither enters `portfolio_basis._normalized_anchor`, so two declarations of the same book still share one `state_version` regardless of how each row's start was learned. A later refresh copies an existing stamp forward onto the same position (`book_refresh._carry_forward_provenance`) so the answer is not spent on one review — but only while the record still traces that position to its anchor, because a position sold and bought back is a different cycle the ledger can date itself.

`ledger.append_events` can stamp a `recorded_at` date alongside the schema version `v` (#472): *when this system learned the fact*, as distinct from the event's own `date`/`as_of` (*when the thing happened*). A trade imported weeks late still carries its true trade date, but `recorded_at` marks the later day it actually entered the ledger — the field a future rule backtest needs to replay history without look-ahead bias, since without it a late import reads, to the replay, as if it had been known on its trade date.

The stamp is opt-in, with no wall-clock default inside the writer: `append_events` is the shared write path for `problems.jsonl` and `revisit.jsonl` too (`problems.py`, `revisit.py`), and `revisit.jsonl` already has its own field for this concept (`enqueued_at`, with its own reader and its own legacy-absence handling) — a silent wall-clock default inside the shared writer would hand every revisit/problem row a second, redundant, non-deterministic date. So a caller passes `recorded_at` only when it genuinely knows the answer: `review.py`'s `_ingest_trades` injects the review period's own `date_end`, the same proxy `recorded_at` already uses elsewhere in this codebase (`review.py`'s `_build_exit_narratives`, for the analogous thesis-event distinction); the standalone CLI paths with no review context (`ledger.py append-trades`/`append-snapshot`) compose `dt.date.today()` at the call site instead of relying on the writer to supply it. A caller that passes nothing gets no field written — absence means *unknown*, never a guessed or back-filled value, the same rule that governs rows written before this field existed. Nothing reads this field yet — it is write-only until a consumer needs it.

`session.py`'s snapshot/reconciliation/adjustment projection (`_project_snapshot_anchor`, two `ledger.append_events` calls) now passes the committing bundle's own `engine_state.date_end` too, closing the gap #473 shipped with (that PR's body has the history) — a positions-snapshot review's anchor, reconciliation mark, and adjustment event all carry the same review-period proxy an `_ingest_trades`-written trade row gets. `date_end` for this route is `snapshot["as_of"]` (`snapshot_adapter.py`), the same value `_project_snapshot_anchor` already validates as a parseable ISO date before either call site runs. The stamp is added to each event only after its content-addressed id (`snapshot_id`/`reconciliation_id`/`adjustment_id`) is already hashed, and `_snapshot_payload`'s field whitelist excludes it as well, so a replay (`repair-projections`) recognizes a pre-fix row with no `recorded_at` as the same fact rather than appending a duplicate.

## Holding derivation

1. Use the latest *declared* snapshot as the anchor.
2. Apply only trades with `date > snapshot.as_of`.
3. If no declaration exists, replay all available trades and mark completeness limitations.

A declared snapshot represents end-of-day state, so same-day trades are already reflected. Missing pre-anchor history is normal and does not invalidate the ledger-derived holdings.

A `trades_derived` snapshot row is deliberately **not** an anchor for this derivation (#549). It is this derivation's own result written down at the time an import produced it, so replaying the trades it summarizes reproduces it exactly, while re-basing on it would discard what a summary row cannot carry: the real cycle start, the cycle sequence, and the add count. `ledger.latest_anchor(events, declared_only=True)` is the single reader of that distinction, and `tests/test_ledger.py` gates the equality it rests on. Every other reader — "has this root recorded a book at all", the refresh lane's `against` stamp — sees both kinds alike, which is the whole point of writing the row. A second or subsequent broker snapshot does not supersede the canonical current holdings until the reconciler compares it with the ledger; adoption then flows through the same `latest_anchor` ordering (`as_of` first, `projection_sequence` for same-day declarations).

## Reconciliation (implemented 2026-07-18, #220)

The target behavior when the user supplies another position snapshot is:

- If derived and declared holdings agree, mark the ledger reconciled.
- If they differ, show the narrow difference, accept the newer declaration as the new anchor, and write an adjustment event preserving the history.

Do not infer the cause of a mismatch. It may represent a missing trade, transfer, split, fee, or data error.

Since #530, recording new facts and discussing them are two commands. `prepare --route snapshot_review` puts every second declaration to the book-update lane (`book_refresh.plan_refresh`, a pure read) and refuses exactly the declarations that lane would raise a confirmation for — a vanished position, or a large move on a large holding — naming `review.py refresh` as the next step. Only that lane asks whether a position missing from the new view was sold or merely missed by the capture, and only an answer to that question can record the exit. Every other difference, `avg_cost` drift included, is reviewed and adopted on the review lane as before: nothing there needs a human answer, and the next reconciliation still sees the truth. The reconciliation *status* is deliberately not the criterion — `derive_holdings` keeps a moving-average cost while brokers may use FIFO or amortize fees, so two correct systems disagree past the half-cent tolerance on almost every real book.

Implementation contract (`ledger.snapshot_reconciliation`, entered through `review.py prepare --snapshot-json` and `review.py refresh --snapshot-json`):

- The comparison is time-aligned: derived holdings are computed as of the declared end-of-day `as_of`, so ledger trades dated after it are not part of the comparison and still apply on top of an adopted anchor.
- The narrow diff lists per-ticker shares (`SHARES_TOL`), market, currency, and avg_cost differences, tickers present on only one side, and per-currency cash differences (`CASH_TOL`). Every value stays in its original currency; avg_cost is compared only when both sides state a number, and an omitted declared cash object is treated as no claim.
- Prepare freezes the diff and verdict into the Review Plan; finalize recomputes it under the root projection lock and fails closed if the ledger changed in between, so an unpreviewed adjustment can never be written.
- The clean path appends only the content-addressed reconciliation mark: the anchor, its cycle identities, and the root-wide `projection_sequence` counter stay untouched.
- Fail-closed edges: an incomplete second declaration is rejected, a declaration older than the current anchor is rejected, history without a complete anchor (replay-only trades or an unrepaired ledger projection) keeps the original initial-onboarding rejection, and a declaration the book-update lane would have to ask about is refused on the review lane before any append (#530).

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

Every incoming source records the book at its own time (#549): a holdings view records it as `user_declared`, and a transaction import records the book it derived as `trades_derived`. Nothing has to qualify to be recorded, and the source marking is never read as an eligibility test. Later transaction files may unlock supported historical dimensions while ledger-derived current holdings remain canonical. A second or subsequent complete snapshot enters through the same command and is routed to the reconciliation contract above; only that comparison may certify that the ledger matches a fresh broker view or adopt the newer declaration, and a declaration carrying something only the user can settle is sent to `refresh` first (#530).

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
- An exit with no recorded fill is still an exit: it enters the same queue, the same 30/60/90 checkpoints and the same importance ranking (using its recorded cost basis as the magnitude, which is not proceeds and is never presented as such). What it may not enter is any figure that needs a fill — hindsight return, win rate, payoff, exit discipline — and the card names it rather than dropping it silently (`unpriced_exits` honesty key).

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
