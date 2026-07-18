# Snapshot review flow

Use when only a position snapshot is available and complete transaction history is not.

Transcribe a position table or screenshot locally into the normalized JSON envelope in `references/data-contract.md`. Copy only broker-declared facts and keep the temporary JSON outside the repository, for example under `/tmp`. Do not calculate weights, P&L, cycle IDs, metrics, driver concentration, or ETF classifications, and do not assemble card/state artifacts by hand. The engine has no OCR or cloud-upload path.

Invoke the only runtime engine entry point:

```bash
python3 engine/review.py prepare --route snapshot_review \
  --snapshot-json /tmp/fomo-kernel-positions.json --language en
```

Discuss only claims supported by the snapshot and emitted by the engine: cost or value weights, single-position risk, driver concentration, ETF structure, and data integrity. Do not infer averaging-down counts, exit discipline, holding behavior, win rate, payoff ratio, alpha, or historical motives.

Create an inferred thesis for every open cycle so later evidence can extend its history. State clearly that this is an opening portfolio check and invite the user to provide transaction history later to unlock supported behavioral diagnostics. Later transactions do not assert a fresh broker view: ledger-derived current holdings remain canonical, and current-view claims fail closed until a subsequent snapshot has an explicit reconciliation path.

An incomplete snapshot still produces a bounded review, but it is not an accounting anchor. Second and subsequent snapshot diff, reconciliation, and adjustment events are a deferred P1 capability; do not describe the initial adapter as implementing them.

The remaining lifecycle matches first review: answer every `required:true` question returned by the plan, create the thesis updates and qualitative narrative, preview, choose at most one commitment, and finalize. A snapshot plan may return an empty question queue because it contains no action history; do not invent a motive question to fill it.
