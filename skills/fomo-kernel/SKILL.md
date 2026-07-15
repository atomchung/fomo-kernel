---
name: fomo-kernel
description: Review a user's trade CSV or position snapshot into one behavior card, one user-chosen next-time rule, and an append-only investment-thesis record. Use for trade reviews, transaction postmortems, brokerage-statement reviews, position reviews, and equivalent requests in any supported language. Do not use for stock picks, market forecasts, or security research.
---

# fomo-kernel

Turn trading data into one focused review card: the largest behavioral leak, the thesis behind any add, and one rule for the next review cycle.

## Non-negotiable rules

1. Use only numbers present in engine artifacts. Never calculate, fill in, or alter numeric facts.
2. Do not provide buy or sell recommendations. Review behavior, motives, thesis evolution, and process rules.
3. Obtain an answer for every `required:true` item in `question_queue` before preview.
4. A card has exactly one final commitment at most. The user may choose a candidate, provide a custom rule, or skip.
5. Keep trade data and derived state local. Show the private card by default; use only the public card for sharing.
6. Treat `sessions/<session_id>/bundle.json` as the canonical completed result. Never hand-edit projections as if they were authoritative.

## Canonical entry point

```bash
cd skills/fomo-kernel
python3 engine/review.py prepare <CSV...> --language en
```

The agent must understand and normalize broker data locally into:
`Symbol / Action(BUY|SELL) / Quantity / Price / TradeDate / RecordType(Trade)`.
Add `Market / Currency` for non-US instruments when available. Do not ask the user to normalize the file. Symbol and cash-anchor rules (Taiwan `.TW`/`.TWO` suffixes, ROC dates, `--cash`) live in `references/data-contract.md`.

`prepare` creates a Review Plan; it does not create a conclusion card. Read only the flow selected by `review_plan.flow_path`:

- `flows/first-review.md`
- `flows/weekly-review.md`
- `flows/snapshot-review.md`
- `flows/test-drive.md`

Then read the shared rules:

- `references/agent-boundaries.md`
- `references/thesis-policy.md`
- `references/card-policy.md`
- `references/data-contract.md`

## Fixed lifecycle

1. `prepare`: run the engine, reconstruct active theses, deduplicate questions, and return a Review Plan.
2. Agent work: make only permitted qualitative judgments, ask every required question, create inferred theses for uncovered positions, and write a narrative with no digits.
3. `preview`: validate answers, evidence, theses, and narrative; then render private and public previews.
4. Show the private preview. Ask the user to choose a candidate rule, provide a custom rule, or skip.
5. `finalize`: validate the final commitment, atomically commit the canonical session bundle, then rebuild compatibility projections.

```bash
python3 engine/review.py preview \
  --session-id <ID> --answers /tmp/answers.json --narrative /tmp/narrative.json

python3 engine/review.py finalize \
  --session-id <ID> --answers /tmp/answers.json --narrative /tmp/narrative.json
```

Do not rerun the engine after an interruption:

```bash
python3 engine/review.py resume
python3 engine/review.py resume --session-id <ID>
```

If finalization committed the canonical bundle but a projection failed, repair it without re-questioning the user:

```bash
python3 engine/review.py repair-projections
```

## Agent artifact contract

- Validate `answers.json` against `schemas/answers.schema.json`.
- Validate `narrative.json` against `schemas/narrative.schema.json`; it may contain qualitative prose only and no digits.
- Write one sentence in `narrative.honesty` for every key in `card_plan.required_honesty_keys`, following the wording guidance in `card-spec.md`. Preview fails on a missing or untriggered key; the renderer weaves each sentence into the section it qualifies.
- Add one `thesis_updates` entry for every missing-thesis `cycle_id`. Default to `maturity:"inferred"` and state the inference source; never present it as user-confirmed.
- A `new_evidence` decision requires `evidence_delta.claim` and `evidence_delta.source` or preview must fail.
- Do not guess ETF classes. Use a local `--instrument-map` for uncommon instruments. Unknown instruments receive no allocation exemption.

## Language and sharing

`--language zh-TW|en` controls user-visible questions, rules, and cards. Both locales use the same engine facts and policy; localization is a rendering concern, not a second analysis workflow.

Each completed session produces:

- `card-private.md` and `card-private.html`: complete local review artifacts.
- `card-public.md`: a separately rendered shareable view without amounts, dates, tickers, exact weights, session IDs, or agent-authored free text.

## Test drive

If the user has no data but wants to see the experience:

```bash
python3 engine/review.py prepare --test-drive --language en
```

Test drive follows the same lifecycle with `persist:false`. It runs in an isolated root directory: read `review_plan.state_root` from the prepare output and pass it as `--root <state_root>` to every later `preview`, `finalize`, and `resume` call, or they will not find the session. It must not project into the user's coach memory, and every conversation and card must be visibly labeled as demo data.
