---
name: fomo-kernel
description: Review a user's trade CSV or position snapshot into one local review card and an append-only investment-thesis record, with at most one user-chosen next-time rule. Transaction history supports behavioral diagnosis; a position snapshot supports an opening structural check. Use for trade reviews, transaction postmortems, brokerage-statement reviews, position reviews, and equivalent requests in any supported language. Do not use for stock picks, market forecasts, or security research.
---

# fomo-kernel

Turn transaction history into one focused behavior-review card, or a position snapshot into one narrow opening portfolio check. Both routes preserve thesis continuity and at most one user-chosen rule.

The product's value is continuity: this review reconciles against the last one, and the next one reconciles against this. That is why the boundaries below hold — a number that changes meaning between weeks makes the whole loop unreadable.

## Non-negotiable rules

1. **Numbers come from engine artifacts only.** Never calculate, fill in, or adjust a numeric fact, ranking, weight, or identity. Determinism is what makes week-to-week reconciliation trustworthy: this week's 12% and next week's 12% have to be the same 12%.
2. **Invoke the engine only through the `engine/review.py` CLI** (`prepare`, `resume`, `preview`, `finalize`, `capture`, `render`, `repair-projections`, `set-cap`, `doctor`). Do not call another `engine/*` script or import engine modules directly — those paths bypass lifecycle validation, required-question gates, and canonical session state.
3. **Review process, not securities.** Discuss behavior, motives, thesis evolution, and process rules. Do not recommend what to buy or sell.
4. **Never invent, interpolate, or recall a market price.** When a host blocks the engine's own retrieval, `review_plan.input.price_feed.request` names what is unpriced; look those closes up from a recognized market-data source, transcribe them into the envelope in `references/price-feed.md`, and rerun `prepare --prices`. A price you cannot find stays missing — a missing price is never a delisting verdict or a zero return.
5. **Trade data stays local.** The source CSV, session bundles, and the ledger never enter cloud memory. The review card is private to the user: show `card-private.*` by default per `references/card-delivery.md`. Local files, terminal output, and private-by-default in-client rendering are all fine; publishing, posting, or sending it to a third party is not. `card-public.md` is the one share-safe artifact, and only if the user asks.
6. **Transcribe snapshots, do not analyze them.** For a position table or screenshot, copy only broker-declared facts into the JSON envelope and keep that file outside the repository, such as under `/tmp`. Do not derive weights, P&L, cycle IDs, metrics, or ETF classifications, and do not use a cloud OCR service — there is no engine OCR or upload path.
7. **`sessions/<session_id>/bundle.json` is the completed result.** Projections are rebuilt from it, never hand-edited as if they were authoritative.

## Canonical entry point

Preflight once after install. The engine fail-soft degrades — silently dropping current prices, P&L, alpha/beta, and market context — when optional runtime dependencies are missing, so verify them before first use:

```bash
cd skills/fomo-kernel
pip install -r requirements.txt   # yfinance + pandas + rich
python3 engine/review.py doctor   # lists what each dep unlocks; non-zero if one is missing

python3 engine/review.py prepare <CSV...> --language en
python3 engine/review.py prepare --route snapshot_review \
  --snapshot-json /tmp/fomo-kernel-positions.json --language en
```

For transaction history, normalize broker data locally into `Symbol / Action(BUY|SELL) / Quantity / Price / TradeDate / RecordType(Trade)`; add `Market / Currency` for non-US instruments. Carry `Amount` through whenever the source has it — it is the broker's own cash-account change for that row, already net of fees, and it makes account-level return exact instead of estimated. Do not ask the user to normalize the file themselves. Symbol and cash-anchor rules live in `references/data-contract.md`.

The engine prices the portfolio itself, so a normal review passes no prices. If the host blocks that, `prepare` still completes in a degraded mode and reports the gap in `review_plan.input.price_feed`.

`prepare` returns a Review Plan, not a card. Read only the flow it selects in `review_plan.flow_path`:

- `flows/first-review.md`
- `flows/first-review-structural.md` — a first review the engine tiered `structural`/`empty`: an opening structural check, no question string, no forced commitment
- `flows/weekly-review.md`
- `flows/snapshot-review.md`
- `flows/test-drive.md`

Then read the shared rules:

- `references/agent-boundaries.md`
- `references/interaction-delivery.md`
- `references/card-delivery.md`
- `references/thesis-policy.md`
- `references/data-contract.md`

## Fixed lifecycle

1. `prepare` — run the engine, reconstruct active theses, deduplicate questions, return a Review Plan.
2. Agent work — resolve one host adapter, ask every required question once, create inferred theses for uncovered positions, and write a digit-free narrative.
3. `preview` — validate answers, evidence, theses, and narrative; render private and public previews.
4. Show the complete card preview inline, then ask the user to choose a candidate rule, write their own, or skip. Generating an artifact is not showing it.
5. `finalize` — validate the commitment, atomically commit the canonical bundle, rebuild projections.

```bash
python3 engine/review.py preview \
  --session-id <ID> --answers /tmp/answers.json --narrative /tmp/narrative.json

python3 engine/review.py finalize \
  --session-id <ID> --answers /tmp/answers.json --narrative /tmp/narrative.json
```

After an interruption, resume rather than rerunning the engine — refetching live data would silently change the facts the user already answered against:

```bash
python3 engine/review.py resume
python3 engine/review.py resume --session-id <ID>
```

If finalize committed the bundle but a projection failed, repair it without re-questioning the user. This is a repairable state, not a lost session:

```bash
python3 engine/review.py repair-projections
```

**Light-tier exception.** When `review_plan.state_snapshot.cadence.tier == "light"` (too short a span since the last review), steps 2–5 do not apply. Follow `flows/light-capture.md`: ask at most one light question, call `capture` to append the motive/emotion fact, and stop. No card, no commitment, no `preview`/`finalize`.

## Agent artifact contract

The engine validates completeness, provenance, identity, and gates at `preview`; it will tell you what is wrong. Your side of the contract is the qualitative work it cannot check:

- **Narrative** (`schemas/narrative.schema.json`): qualitative prose, no digits. The renderer owns every amount, percentage, date, ticker, and metric, so prose can never become a competing source of truth. Write one sentence in `narrative.honesty` for each key in `card_plan.required_honesty_keys`, following `card-spec.md`.
- **Theses**: add one `thesis_updates` entry per missing-thesis `cycle_id`, using the field vocabulary in `review_plan.authoring_contract` — the unchanged `cycle_id` plus the qualitative fields; the engine prefills the mechanical ones and assigns all identity. State the inference source, and never present an inferred thesis as user-confirmed.
- **Questions**: `prepare` ranks motive, recent-exit, matured checkpoint, and first-review entry-thesis questions by engine-owned impact and returns a route-scoped count (first review three to five, weekly one to three, snapshot none). Ask all of them; it never pads to reach a minimum. `skip` semantics differ by kind: skipping an exit-reason capture is durable, while a skipped `due_revisit` legitimately returns next review.
- **Evidence**: a confirmed evidence delta means "the user confirmed this was part of the decision," not that the claim is externally true. Do not invent `observed_at`.
- **Instruments**: do not guess ETF classes. Use a local `--instrument-map` for uncommon instruments; unknown instruments get no allocation exemption.

A snapshot review is an opening portfolio check, not a transaction-history diagnosis. Discuss engine-owned cost or value weights, single-position risk, driver concentration, ETF structure, and data integrity. Averaging-down counts, exit discipline, holding behavior, win rate, payoff ratio, alpha, and historical motives need transaction history — invite the user to add it later. Ledger-derived current holdings stay canonical until an explicit snapshot reconciliation succeeds, so an incomplete snapshot can produce a bounded review but never becomes an accounting anchor.

If a new observation could overturn the top behavioral leak, add it to `observations` and rerun preview rather than editing the engine artifact.

## Language and sharing

`--language` controls user-visible questions, rules, and cards. Always pass the language the user is conversing in: a zh-TW conversation runs with `--language zh-TW`, zh-CN with `--language zh-CN`. If the user's language has no copy asset (anything other than `zh-TW`, `zh-CN`, `en` today) or you cannot tell, pass `--language en`; the engine applies the same fallback, so card surfaces come out in English either way. Keep conversing in the user's language and do not hand-translate card text. The `en` in the examples above is a placeholder, not a default.

Each completed session produces `card-private.md` and `card-private.html` (the complete local card) and `card-public.md` (a separately rendered share-safe artifact — no amounts, dates, tickers, exact weights, session IDs, or agent free text; no upload path exists).

## Test drive

If the user has no data but wants to see the experience:

```bash
python3 engine/review.py prepare --test-drive --language en
```

Test drive follows the same lifecycle with `persist:false`, in an isolated root. Read `review_plan.state_root` from the prepare output and pass it as `--root <state_root>` to every later `preview`, `finalize`, and `resume`, or they will not find the session. It must never project into the user's coach memory, and every card and message must be visibly labeled demo data.
