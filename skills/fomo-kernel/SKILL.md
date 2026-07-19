---
name: fomo-kernel
description: Review a user's trade CSV or position snapshot into one local review card and an append-only investment-thesis record, with at most one user-chosen next-time rule. Transaction history supports behavioral diagnosis; a position snapshot supports an opening structural check. Use for trade reviews, transaction postmortems, brokerage-statement reviews, position reviews, and equivalent requests in any supported language. Do not use for stock picks, market forecasts, or security research.
---

# fomo-kernel

Turn transaction history into one focused behavior-review card, or a position snapshot into one narrow opening portfolio check. Both routes preserve thesis continuity and at most one user-chosen rule.

## Non-negotiable rules

1. Use only numbers present in engine artifacts. Never calculate, fill in, or alter numeric facts.
2. Do not provide buy or sell recommendations. Review behavior, motives, thesis evolution, and process rules.
3. Obtain an answer for every `required:true` item in `question_queue` before preview.
4. A card has exactly one final commitment at most. The user may choose a candidate, provide a custom rule, or skip.
5. Keep trade data and derived state local. Show the review card (`card-private.*`) by default, following the delivery contract in `references/card-delivery.md`; use only `card-public.md` as a share-safe artifact. The product does not publish or upload it.
6. Treat `sessions/<session_id>/bundle.json` as the canonical completed result. Never hand-edit projections as if they were authoritative.
7. Invoke the engine only through the `engine/review.py` CLI (`prepare`, `resume`, `preview`, `finalize`, or `repair-projections`). Never call another `engine/*` script or import engine modules directly; those paths bypass lifecycle validation, required-question gates, and canonical session state.
8. For a position table or screenshot, transcribe only the broker-declared facts into the normalized snapshot JSON envelope and keep that temporary file outside the repository, such as under `/tmp`. Do not calculate weights, P&L, cycle IDs, metrics, or ETF classifications, and do not use a cloud OCR service.
9. An incomplete snapshot may produce a bounded review, but it must not become an accounting anchor. Later transaction files may unlock history-dependent diagnostics; ledger-derived current holdings remain canonical, and any claim about an unreconciled current broker view must fail closed.

## Canonical entry point

```bash
cd skills/fomo-kernel
python3 engine/review.py prepare <CSV...> --language en
python3 engine/review.py prepare --route snapshot_review \
  --snapshot-json /tmp/fomo-kernel-positions.json --language en
```

For transaction history, the agent must understand and normalize broker data locally into:
`Symbol / Action(BUY|SELL) / Quantity / Price / TradeDate / RecordType(Trade)`.
Add `Market / Currency` for non-US instruments when available. Do not ask the user to normalize the file. Symbol and cash-anchor rules (Taiwan `.TW`/`.TWO` suffixes, ROC dates, `--cash`) live in `references/data-contract.md`.

For a position table or screenshot, transcribe the displayed facts locally into the JSON envelope in `references/data-contract.md`, save that temporary file outside the repository, then pass it through `--snapshot-json`. The agent may map broker labels, normalize dates, and add the complete provider ticker suffix; it may not derive weights, returns, cycle IDs, or card/state artifacts. There is no engine OCR or cloud-upload path.

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
- `references/interaction-delivery.md`

## Fixed lifecycle

1. `prepare`: run the engine, reconstruct active theses, deduplicate questions, and return a Review Plan.
2. Agent work: declare the host capabilities in the privacy-safe UX receipt, make only permitted qualitative judgments, ask every required question once using the native control or fixed fallback in `references/interaction-delivery.md`, create inferred theses for uncovered positions, and write a narrative with no digits.
3. `preview`: validate answers, evidence, theses, and narrative; then render private and public previews.
4. Show the complete review-card preview inline, record the actual delivery mode, then ask the user to choose a candidate rule, provide a custom rule, or skip. Artifact generation alone is not card delivery.
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
- Do not invent `thesis_id`, `event_id`, `revises`, or `decision_cursor`. The engine assigns stable identity and links each accepted event to the prior event for that cycle.
- A `new_evidence` decision requires `evidence_delta.claim` and `evidence_delta.source` or preview must fail.
- Treat confirmed evidence as "the user confirmed this was part of the decision," not as external fact verification. Do not invent `observed_at`; the engine preserves missing observation time separately from review capture time.
- `prepare` ranks eligible motive, recent-exit, and matured 30/60/90 checkpoint questions using engine-owned amount or P&L impact and returns at most three. Ask every returned question. `skip` semantics differ by kind: skipping an exit-reason capture is durable (that reason is never asked again), while skipping a `due_revisit` verdict is not saved and the same checkpoint legitimately returns next review.
- Do not guess ETF classes. Use a local `--instrument-map` for uncommon instruments. Unknown instruments receive no allocation exemption.
- A snapshot review is an opening portfolio check, not a transaction-history diagnosis. Discuss only engine-owned cost or value weights, single-position risk, driver concentration, ETF structure, and data integrity. Do not claim averaging-down counts, exit discipline, holding behavior, win rate, payoff ratio, alpha, or historical motives from a snapshot.
- Add an inferred thesis for every uncovered snapshot-origin cycle and label it as inferred. Invite the user to provide transaction history later; only that later history may unlock the historical behavior dimensions. Do not say that transaction import reconciles a newer broker view: ledger-derived current holdings stay canonical until an explicit snapshot reconciliation exists.
- A second complete snapshot routes through the same prepare call into reconciliation: show the engine-owned narrow diff from `engine_state.snapshot_reconciliation` before preview, state that finalize records the result (`reconciled` keeps the anchor; `adjusted` writes an adjustment event and adopts the newer declaration as the anchor), and never infer why values differ.

## Language and sharing

`--language zh-TW|en` controls user-visible questions, rules, and cards. Both locales use the same engine facts and policy; localization is a rendering concern, not a second analysis workflow.

Each completed session produces:

- `card-private.md` and `card-private.html`: the complete local review card, using the localized review-card name from copy assets.
- `card-public.md`: a separately rendered share-safe artifact. Transaction-history reviews may retain fixed behavior-pattern copy and engine-owned beta / benchmark-excess percentage points; snapshot reviews retain only fixed structural-baseline copy. Neither form includes amounts, dates, tickers, exact weights, session IDs, or agent-authored free text. It is not uploaded or published.

## Test drive

If the user has no data but wants to see the experience:

```bash
python3 engine/review.py prepare --test-drive --language en
```

Test drive follows the same lifecycle with `persist:false`. It runs in an isolated root directory: read `review_plan.state_root` from the prepare output and pass it as `--root <state_root>` to every later `preview`, `finalize`, and `resume` call, or they will not find the session. It must not project into the user's coach memory, and every conversation and card must be visibly labeled as demo data.
