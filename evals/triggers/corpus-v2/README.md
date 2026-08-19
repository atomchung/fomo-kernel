# Decision-lane trigger corpus v2 (#827)

This directory is the current release authority for FOMO Kernel trigger
routing. `evals/triggers/run_triggers.py` defaults here and requires the exact
`v2` version stamp in both corpus files and recorded attempt rows.

Each locale has an author-visible `calibration` split and an independently
authored blind `holdout` split. Each file contains six prompts in each class:

- `named_security_decision` — a live decision about a named security;
- `candidate_discovery` — an explicit request to find or shortlist investment
  candidates;
- `adjacent_negative` — pure education, constituent/fact retrieval, or general
  market-data lookup without an investment decision.

The holdout gate is deliberately strict: all six prompts in all three classes
must route correctly in every measured host/locale cell. Positive classes route
to `trigger`; the negative class routes to `no_trigger`. A partial cell never
passes.

The pre-#827 files under `../corpus/` are archival evidence with superseded
labels. Keep them immutable and do not pass them to the v2 gate as release
evidence.
