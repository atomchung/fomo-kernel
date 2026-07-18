# Snapshot review flow

Use when only a position snapshot is available and complete transaction history is not.

Transcribe a position table or screenshot locally into the normalized JSON envelope in `references/data-contract.md`. Copy only broker-declared facts and keep the temporary JSON outside the repository, for example under `/tmp`. `fomo-kernel-positions.json` is the recommended filename for that temporary file; the repository's `.gitignore` also matches this exact name at any path depth as a backstop in case it is ever created inside the repository. Do not calculate weights, P&L, cycle IDs, metrics, driver concentration, or ETF classifications, and do not assemble card/state artifacts by hand. The engine has no OCR or cloud-upload path.

Invoke the only runtime engine entry point:

```bash
python3 engine/review.py prepare --route snapshot_review \
  --snapshot-json /tmp/fomo-kernel-positions.json --language en
```

Discuss only claims supported by the snapshot and emitted by the engine: cost or value weights, single-position risk, driver concentration, ETF structure, and data integrity. Do not infer averaging-down counts, exit discipline, holding behavior, win rate, payoff ratio, alpha, or historical motives.

Create an inferred thesis for every open cycle so later evidence can extend its history. State clearly that this is an opening portfolio check and invite the user to provide transaction history later to unlock supported behavioral diagnostics. Later transactions do not assert a fresh broker view: ledger-derived current holdings remain canonical, and only the explicit snapshot reconciliation below may certify or adopt a newer broker view.

When the coach root already has an accounting anchor, the same prepare call routes the new declaration to reconciliation instead of onboarding. Read `engine_state.snapshot_reconciliation` from the Review Plan and show the user its narrow engine-owned diff before preview: each row states only a derived value and a declared value (shares, market, currency, avg_cost, one-sided tickers, per-currency cash). Never suggest why they differ — a missing trade, transfer, split, fee, or data error look identical here. State plainly what finalize will record: `reconciled` marks the ledger as matching and keeps the current anchor; `adjusted` writes one adjustment event preserving history and adopts the newer declaration as the anchor. Finalize is the confirmation step; if it reports that the ledger changed after prepare, rerun prepare with the same snapshot instead of editing anything.

An incomplete snapshot still produces a bounded review on an empty root, but it is not an accounting anchor and it cannot reconcile existing history — the engine rejects it; ask for the complete account view. A declaration older than the current anchor is also rejected.

The remaining lifecycle matches first review: answer every `required:true` question returned by the plan, create the thesis updates and qualitative narrative, preview, choose at most one commitment, and finalize. A snapshot plan may return an empty question queue because it contains no action history; do not invent a motive question to fill it.
