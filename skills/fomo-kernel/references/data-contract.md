# Data contract and recovery

Authority order:

1. `sessions/<session_id>/bundle.json`: complete immutable session.
2. State, plan, answers, narrative, private/public cards, and manifest in the same directory: manifest-locked artifacts.
3. `last_state.json`, `log.jsonl`, `theses.jsonl`, `thesis_decisions.jsonl`, `rules.jsonl`, `problems.jsonl`, and `cards/`: rebuildable compatibility projections.

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
  "fx": {"USD": 1, "TWD": 0.0307},
  "is_complete": true
}
```

- `as_of` and a non-empty `positions` array are required. `as_of` is the non-future end-of-day date represented by the statement; ask one short question if the source does not show it rather than inventing a date.
- Every position requires a complete `ticker`, positive `shares`, supported `market` (`US` or `TW`), and explicit original `currency`. `avg_cost` and `market_value` are optional positive original-currency facts. Repeated rows for the same ticker may remain separate in the envelope; the engine merges them only when their market and currency agree and fails closed on a conflict.
- `cash`, `fx`, and `is_complete` are optional. `fx` values are positive USD-per-unit rates copied from a reliable source, with `USD` fixed at one; omit an unavailable rate instead of deriving or guessing it. Set `is_complete:false` when the supplied positions are known to cover only part of the account. Mixed-currency or incomplete snapshots without one consistent valuation basis stay valid, but global weight-based conclusions remain unscored. An incomplete snapshot may produce this bounded review, but it is not an accounting anchor.
- A screenshot or broker-specific table is an agent input, not an engine image format. Transcribe the displayed facts locally into this envelope, without cloud OCR or upload, save the temporary JSON outside the repository (for example `/tmp/fomo-kernel-positions.json`), then call `review.py prepare --route snapshot_review --snapshot-json <path>`. `fomo-kernel-positions.json` is the recommended filename; the repository's `.gitignore` also matches this exact name at any path depth as a backstop in case it is ever created inside the repository. Do not place calculated weights, P&L, cycle IDs, metrics, driver labels, ETF classifications, engine card fields, or engine state fields in the envelope.
- A complete initial snapshot may become the local accounting anchor. Later transaction-history imports may unlock supported history-dependent diagnostics while ledger-derived current holdings remain canonical. They do not by themselves reconcile a newer broker view, so any current-view claim that needs that comparison must fail closed. Second and subsequent snapshot diff, reconciliation, and adjustment events remain deferred P1 work.
- A snapshot alone does not support claims about prior adds, exits, holding behavior, win rate, payoff, alpha, or motives.

ETF policy: broad-market, regional, bond, and commodity ETFs are diversified allocations. Sector, thematic, and leveraged ETFs remain concentrated risk. Treat an unknown ticker conservatively as equity. Missing expense ratio or tracking error belongs in the honesty ledger and must never be filled with zero.

Symbols: write the complete yfinance symbol so the engine can price every position. Taiwan listed stocks take `.TW` (for example `2330.TW`) and OTC stocks take `.TWO` (for example `6488.TWO`); listed-versus-OTC is the agent's world knowledge — the engine has no symbol table and a bare `2330` silently disappears from pricing, alpha, and concentration. Convert ROC-calendar dates (for example 114/07/01) to ISO before handing data to the engine, and preserve explicit `market` and `currency` fields for snapshot positions and non-US trade rows.

Cash anchor: statements usually carry a cash balance row — read it instead of asking. Pass it to prepare as `--cash '{"currency":"USD","amount":8200,"as_of":"<date>"}'`; use a JSON list with one anchor per account for multi-currency accounts (for example one TWD and one USD anchor). Only ask the user one short question when no balance appears anywhere. Without an anchor the engine degrades gracefully (`cash.reliable=false`) and the card shows the holdings pillar plus an unlock invitation instead of account-level return — never guess a balance to force the unlock.
