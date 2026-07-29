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

When the coach root already has an accounting anchor and the newer view holds something only the user can settle — a position the record has and the view does not, a position the view has and the record does not, or a large move on a large holding — `prepare` refuses and names `refresh`. That is not a fork to weigh up: recording new facts and discussing them are two jobs, and the book is brought up to date first whether or not the user asked for a review. Go to `flows/book-refresh.md`, which asks about exactly those things and adopts the rest without ceremony, then come back here with the same declaration and give it its review. Do not try to route around the refusal, and never present a card built on a book you already know has an unanswered question in it.

Any smaller difference is not a fork at all and needs no detour. When the coach root already has an accounting anchor, the same prepare call routes the new declaration to reconciliation instead of onboarding. Read `engine_state.snapshot_reconciliation` from the Review Plan and show the user its narrow engine-owned diff before preview: each row states only a derived value and a declared value (shares, market, currency, avg_cost, one-sided tickers, per-currency cash). Never suggest why they differ — a missing trade, transfer, split, fee, or data error look identical here. State plainly what finalize will record: `reconciled` marks the ledger as matching and keeps the current anchor; `adjusted` writes one adjustment event preserving history and adopts the newer declaration as the anchor. Finalize is the confirmation step; if it reports that the ledger changed after prepare, rerun prepare with the same snapshot instead of editing anything — and if that rerun now asks for `refresh`, something moved that the user has to answer for first.

An incomplete snapshot still produces a bounded review on an empty root, but it is not an accounting anchor and it cannot reconcile existing history — the engine rejects it; ask for the complete account view. A declaration older than the current anchor is also rejected.

The remaining lifecycle matches first review: declare capabilities and record presentation following `references/interaction-delivery.md`, answer every `required:true` question returned by the plan once, create the thesis updates and qualitative narrative, preview, choose at most one commitment, and finalize. Show the previewed and final cards inline following `references/card-delivery.md`. A snapshot plan normally returns an empty question queue because it contains no action history; do not invent a motive question or dynamic surface to fill it.
