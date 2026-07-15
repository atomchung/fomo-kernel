# Snapshot review flow

Use when only a position snapshot is available and complete transaction history is not.

The snapshot adapter must first convert the snapshot into an engine card and state, then call:

```bash
python3 engine/review.py prepare --route snapshot_review \
  --card-json <card.json> --state-json <state.json>
```

Discuss only claims supported by the snapshot: cost weights, single-position risk, driver concentration, ETF structure, and data integrity. Do not infer averaging-down counts, exit discipline, win rate, payoff ratio, or historical motives.

Create an inferred thesis for every open cycle so a later transaction import can reconcile against it. State clearly that this is an opening portfolio check and invite the user to provide transaction history later to unlock behavioral diagnostics.

The remaining lifecycle matches first review: required questions, thesis updates, qualitative narrative, preview, one commitment, and finalize.
