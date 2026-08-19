# Decision-lane trigger corpus v2 (#827)

This directory measures whether a real host engages FOMO Kernel for the wider
decision-support contract introduced by #827. A named-security investment
decision or an explicit request to discover candidates should trigger. Pure
education and general market-data lookup without an investment decision should
not.

The runner is an offline instrument. It validates inputs, builds an attempt
plan, records operator-observed routes, and scores results. It never launches a
host, reaches a network, or proves trigger accuracy by itself.

```bash
python3 evals/triggers/run_triggers.py validate
python3 evals/triggers/run_triggers.py dry-run
python3 evals/triggers/run_triggers.py record --help
python3 evals/triggers/run_triggers.py score --help
```

## Current and archival authority

| Path | Role |
|---|---|
| `corpus-v2/<locale>/<split>.json` | current #827 corpus: three locales, calibration and holdout |
| `schema/prompt-corpus-v2.schema.json` | readable v2 prompt contract |
| `schema/trigger-attempt.schema.json` | readable v2 result-row contract |
| `run_triggers.py` | current v2 loader, recorder, and scorer |
| `../../tests/test_triggers.py` | deterministic gates and mutation probes |
| `corpus/<locale>/<split>.json` | frozen pre-#827 historical evidence; never current release authority |
| `schema/prompt-corpus.schema.json` | readable contract for the historical corpus |

Do not relabel the historical holdout. Its old boundary intentionally treated
some no-book recommendations and discovery requests as negative, so scoring it
against #827 would validate the opposite product contract. The default runner
accepts only `version: "v2"` and defaults only to `corpus-v2/`.

## V2 classes and routes

Each locale and split has six prompts in each class:

- `named_security_decision` -> `trigger`
- `candidate_discovery` -> `trigger`
- `adjacent_negative` -> `no_trigger`

Adjacent negatives cover the boundary near both positive classes and a general
education/lookup boundary. They are decision-adjacent, not easy unrelated
prompts. Every prompt is synthetic and generic; no owner holdings, amounts,
motives, or other private facts belong in this corpus.

The three locale sets are authored for `en`, `zh-TW`, and `zh-CN`, rather than
mechanically transliterating one Chinese set into the other. Each locale has a
calibration split and an independently authored holdout. The runner requires
the splits to be disjoint by id and normalized text and requires the exact
locale/split/class id sequence.

## Scoring and fail-closed behavior

The compact v2 gate requires all six prompts in each class to route correctly.
One wrong route fails that class. Zero attempts are `not_run`; a partial class
is `incomplete`; neither can pass. Classes, hosts, locales, and splits are
reported independently and are never pooled into an average that can hide one
failed boundary.

Result files are append-only JSONL. `record` derives `class` and
`expected_route` from the current corpus; callers provide only the observed
`actual_route` and a non-empty account of what happened. Every row carries
`corpus_version: "v2"`; unversioned and stale rows are refused rather than
folded into current evidence. File order, not the informational timestamp, is
the authority when a later attempt corrects an earlier one.

## What green validation does not prove

- It does not prove a real host loaded or invoked the Skill. The operator must
  run the frozen holdout in the host and record what actually happened.
- Text disjointness does not prove semantic independence; it only catches
  duplicate ids and normalized prompt reuse.
- Host version, model, and installed skill population are operator-observed
  fields. A separate harness-controlled execution receipt is needed when those
  facts must be independently proven.
- Corpus validation is not the four-scene old-vs-new Skill A/B required by
  #827. That comparison also measures answer usefulness, writes, and blind
  owner preference.
