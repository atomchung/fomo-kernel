# Data contract and recovery

Authority order:

1. `sessions/<session_id>/bundle.json`: complete immutable session.
2. State, plan, answers, narrative, private/public cards, and manifest in the same directory: manifest-locked artifacts.
3. `last_state.json`, `log.jsonl`, `theses.jsonl`, `thesis_decisions.jsonl`, `rules.jsonl`, `conditions.jsonl`, `problems.jsonl`, and `cards/`: rebuildable compatibility projections.

If prepare is interrupted, read `.pending/<session_id>` through `review.py resume`; do not refetch live prices.

If finalize fails before the atomic rename, no session is committed. The pending session remains available for correction and retry.

If finalize fails after the rename while writing projections, the session is complete. Run `review.py repair-projections`; do not delete the bundle or ask the user again.

Retrying the same session with identical content is a no-op. Retrying the same session with different content fails closed. To review identical state as a distinct session, pass an explicit `--session-nonce` to prepare.

Schemas:

- Review Plan: `schemas/review-plan.schema.json`
- Answers: `schemas/answers.schema.json`
- Prose narrative: `schemas/narrative.schema.json`
- Canonical bundle: `schemas/session-bundle.schema.json`

Snapshot envelope:

```json
{
  "as_of": "2026-07-06",
  "positions": [
    {
      "ticker": "NVDA",
      "shares": 40,
      "avg_cost": 152.3,
      "market_value": 6800,
      "market": "US",
      "currency": "USD"
    }
  ],
  "cash": {"USD": 8200},
  "fx": {"USD": 1, "TWD": 0.0307}
}
```

- `as_of` and a non-empty `positions` array are required. `as_of` is the non-future end-of-day date represented by the statement; ask one short question if the source does not show it rather than inventing a date.
- Every position requires a complete `ticker`, positive `shares`, supported `market` (`US` or `TW`), and explicit original `currency`. `avg_cost` and `market_value` are optional positive original-currency facts. Repeated rows for the same ticker may remain separate in the envelope; the engine merges them only when their market and currency agree and fails closed on a conflict.
- `cash` and `fx` are optional. `fx` values are positive USD-per-unit rates copied from a reliable source, with `USD` fixed at one; omit an unavailable rate instead of deriving or guessing it. A mixed-currency snapshot without one consistent valuation basis stays valid, but global weight-based conclusions remain unscored. There is no completeness field: whatever the user hands over is what the system records, and a view covering one brokerage of several is a recorded book like any other (#549). If the record then holds a position this view omits, the engine asks about that specific difference through the refresh lane below — a question about an observed fact, not about an account it cannot see.
- A screenshot or broker-specific table is an agent input, not an engine image format. Transcribe the displayed facts locally into this envelope, without cloud OCR or upload, save the temporary JSON outside the repository (for example `/tmp/fomo-kernel-positions.json`), then call `review.py prepare --route snapshot_review --snapshot-json <path>`. `fomo-kernel-positions.json` is the recommended filename; the repository's `.gitignore` also matches this exact name at any path depth as a backstop in case it is ever created inside the repository. Do not place calculated weights, P&L, cycle IDs, metrics, driver labels, ETF classifications, engine card fields, or engine state fields in the envelope.
- Every source records the book at the time it arrives, and its `source` records which kind of source it was without that deciding anything (#549). A holdings view records `user_declared`; a transaction import records `trades_derived` after the trades it summarizes. Ledger-derived current holdings stay canonical either way — a `trades_derived` row is that derivation written down, so it is never replayed as if it replaced the trades. Later transaction-history imports may unlock supported history-dependent diagnostics; they do not by themselves reconcile a newer broker view, which is what the refresh lane below is for.
- Canonical means it is also the denominator. A review reconciles what it derived from the file it was given against that recorded book, and account-wide readings — position sizing, diversification and sector exposure, ETF structure, unrealized P&L — are stated only when the file covers it. Handing over just this period's trades is ordinary use and is not an error: the review still runs, the behavioural half (exits, holding time, averaging down, payoff) still reads from those trades, and the account-wide half is withdrawn for that period and said so on the card rather than being restated from a partial view. Re-supplying the full history restores it. Nothing here asks the user to reformat anything (#630).
- A second or subsequent complete snapshot goes through the same `--snapshot-json` prepare call and routes to reconciliation instead of onboarding. The engine compares it with ledger-derived holdings as of the declared `as_of` and freezes the verdict into `engine_state.snapshot_reconciliation`: a `status` of `reconciled` or `adjusted`, plus a narrow fact diff — per-ticker shares, market, currency, avg_cost, one-sided tickers, and per-currency cash, all in original currency. The diff states values, never causes: a missing trade, a transfer, a split, a fee, and a data error all look identical here, so show it without explaining it. Finalize records the outcome (`reconciled` keeps the current anchor; `adjusted` appends one adjustment event preserving history and adopts the newer declaration). Identities are engine-assigned and content-addressed, so a finalize replay is a no-op. A declaration older than the recorded book, a root that has recorded no book at all, and a ledger that changed between prepare and finalize are all rejected — rerun prepare rather than editing artifacts. A declaration carrying a difference only the user can settle is also refused and routed to `refresh` (next bullet): the engine puts every second declaration to the book-update lane first and refuses exactly the ones that lane would raise a confirmation for, so recording precedes discussing (#530). Every other difference reviews normally and is adopted as above.
- Updating the recorded book is its own flow, and once a book exists a newer view goes through it whenever anything in it needs the user's answer. `review.py refresh --snapshot-json <path>` takes the same envelope, returns the same narrow diff, and is decoupled from the review lifecycle: no card, no review question budget, no session (`flows/book-refresh.md`, `schemas/book-refresh.schema.json`). It asks about exactly three things before adopting anything, and every raised item goes into one question, never one per ticker. A position the record holds and the new view omits, which the ledger does not already explain, is always asked about — the engine cannot tell a sale from a screenshot that missed it, and the two answers lead to different states: `sold` removes the position and records that it is absent as of that date **without inventing a fill**, while `not_captured` carries the position forward with `carried:true`. A position the new view holds and the record does not is confirmed too (#531): it destroys nothing, but it arrives with no provenance and the engine cannot recover it later, so the answer states roughly how many months it has been held plus a cost when the view carries none. The engine, not the agent, converts those months into a cycle start, and stores it stamped as an estimate so no surface can print it as an exact day; "I don't know" is an ordinary answer and keeps the existing `ticker#unknown` cycle, which drops that holding from holding-period diagnostics rather than inventing a date. A large share change on a position that is a large part of the book asks to be confirmed or re-supplied. Everything else — small changes, cash differences, market and currency differences — is adopted and disclosed with no question. Preparing a refresh writes nothing; adopting one refuses if the recorded book changed in between.
- A snapshot alone does not support claims about prior adds, exits, holding behavior, win rate, payoff, alpha, or motives.

ETF policy: broad-market, regional, bond, and commodity ETFs are diversified allocations. Sector, thematic, and leveraged ETFs remain concentrated risk. Treat an unknown ticker conservatively as equity. Missing expense ratio or tracking error belongs in the honesty ledger and must never be filled with zero.

Symbols: write the complete yfinance symbol so the engine can price every position. Taiwan listed stocks take `.TW` (for example `2330.TW`) and OTC stocks take `.TWO` (for example `6488.TWO`); listed-versus-OTC is the agent's world knowledge — the engine has no symbol table and a bare `2330` silently disappears from pricing, alpha, and concentration. Convert ROC-calendar dates (for example 114/07/01) to ISO before handing data to the engine, and preserve explicit `market` and `currency` fields for snapshot positions and non-US trade rows. Always set `Currency` (and `Market`) for a non-US trade row during normalization; never leave it for the engine to guess. An omitted `Currency` column silently defaults to `USD` in the ledger, so a `.TW`/`.TWO` row without it prices, costs, and asks about that position in the wrong currency even though the underlying amount is correct.

Prices: the engine retrieves its own prices and no review requires the agent to supply them. When the host blocks that retrieval, `prepare` reports it in `review_plan.input.price_feed` and emits the machine-readable manifest of what is still unpriced. The agent may then look those closes up in a recognized market-data source and hand them back through `prepare --prices` using the envelope in [price-feed.md](price-feed.md) — declared facts only, never an invented, interpolated, or remembered price, and never a missing price read as a delisting or a zero return.

Cash anchor: statements usually carry a cash balance row — read it instead of asking, and pass it to prepare as `--cash '{"currency":"USD","amount":8200,"as_of":"<date>"}'`, or a JSON list with one anchor per account for multi-currency accounts. Never guess a balance to force the unlock.

When no balance appears anywhere, **the engine says so and the ask waits for the card.** `review_plan.input.cash_anchor` states the situation on every route:

- `anchored` — nothing to do.
- `absent` / `partial` — this review has no anchor, or only some currencies do. The entry names `unanchored_currencies` (ask about those, by name), `unlocks` (account-level return, annualized return, cash drag — the account pillar, and nothing else on the card), and `ask_after: "card_presented"`.
- `not_applicable` with a `reason` — `light_tier` (a light week keeps its single-question promise and is never asked), `snapshot_envelope` (a declared snapshot states cash inline), `test_drive` (no accounting anchor is persisted). This is an explicit claim rather than a missing key, so "this route never asks" is something a reader can check.

`ask_after` is the whole point, and it is the opposite of `input.price_feed.request` sitting beside it. A price gap is recovered *before* the user is shown anything, because every price-dependent number is degraded without it. A missing cash anchor degrades one pillar and leaves every other number identical, so asking first buys nothing and spends the user's attention before they have seen anything (#507 principle 1). Ask in the same message as the card, alongside the rule choice: state what answering unlocks and that skipping keeps the holdings-only view, so the ask is informed rather than blind.

If they answer, recompute in place:

```bash
python3 engine/review.py add-cash --session-id <ID> \
  --cash '{"currency":"USD","amount":8200,"as_of":"<date>"}'
```

This re-enters the same review with the anchor added and reuses that session's frozen prices; it refuses outright if anything but the anchor moved, because the user answered against the card those numbers rendered. Answers, narrative, and frozen question surfaces carry over untouched; the returned session id supersedes the one you passed, and `card_plan.required_honesty_keys` gains the account-basis key, which needs one more sentence in `narrative.honesty`. Rerun `preview` on the returned session and show the recomputed card. If they skip, the card keeps its holdings pillar and its unlock invitation exactly as before, and the review finishes normally — skipping is a real answer, not a failure.

On `first_review` and full-tier `weekly_review`, record which of the three happened (`cash_anchor_checked`, `references/ux-receipt.md`): `found_in_source` before the first question or card, `provided` or `declined` after the card the question was attached to. There is no outcome meaning "the agent decided not to ask" — that is what made a run where the user never got the chance look identical to one where they declined (#357, fifth recurrence).

One anchor is enough: the engine reconstructs the account's historical cash balance by rolling that single point backward through each trade's cash footprint, so the user never has to supply a day-by-day record. Two things follow from that, worth knowing because neither is visible in the data:

- The footprint comes from the source's own `Amount` column when it has one, so **preserve `Amount` during normalization whenever it exists**. Without it the engine estimates each footprint as quantity times price and says so on the card; that estimate excludes commissions, taxes, and FX costs, so its error grows with turnover.
- A source whose rows are all trades carries no deposit or withdrawal record, so the rebuilt history assumes account cash moved only through trading. Money the user actually paid in later gets rolled back into a starting balance they never held, which understates return. The engine discloses this assumption with its own magnitude rather than applying it silently.

Neither limit affects the holdings pillar, which never depended on cash.

Silenced rules (`profile.json`): a user who no longer wants to be asked about a rule should not have to delete it to get quiet. `review.py mute-rule --rule-id <id>` records the rule line as silenced, and `--unmute` reverses it. A silenced rule leaves the card's attention rotation and is never raised unprompted, but it is still reconciled every period, so the standing verdict and streak are there in `problem_stats.muted_rules` when the user comes back to look. Two properties make that promise real rather than nominal: the mute is a standing preference in `profile.json` and never a row in `rules.jsonl` — that file is a rebuildable projection above, so a mute stored there would be erased by the first `repair-projections` and the user would start being asked again with no signal — and the identity is the rule *line* (the root of its `revises` chain), so a later revision of the same rule inherits the silence and nothing about muting can move the id that answered breach questions are keyed on.

Standing single-position cap (`profile.json`): sizing thresholds are universal by default — a single name over 25% is flagged and a rule to cap it at 20% is offered. The default is deliberately not derived from the user's own history; personalizing off their average would normalize a bad habit. A user may instead commit to their own cap. When the user states a single-position limit during a review (for example "my cap is 25%"), record it with `review.py set-cap --pct <fraction>` (a fraction strictly between 0 and 1, e.g. `0.25`); `review.py set-cap --clear` reverts to the universal default. The value persists in `profile.json` (`{"max_position_pct": 0.25}`), a standing preference outside per-session state. Once set, the next review's diagnosis, prescription, and the interpolated number in the sizing rule all reconcile against the user's number instead of the default. An out-of-range value is rejected fail-closed rather than stored. This is the one supported personalization of a threshold: it comes from the user's explicit commitment, never from their trade distribution. Do not add any card note explaining the number — the rule already carries it.
