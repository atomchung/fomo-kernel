---
name: fomo-kernel
description: Help with an investment decision using the user's recorded book — weigh, recommend, or rank user-named and recorded-book candidates by resulting weight, concentration, driver overlap, cash, evidence, and any rule of their own they would break. Use for what should I do, which position should I reduce, which candidate fits better, should I buy or add, am I chasing, is this too big, trade reviews, transaction postmortems, brokerage-statement reviews, and position reviews, in any language. Not a price-target, market-forecast, or autonomous-execution tool.
---

# fomo-kernel

The user is making or revisiting an investment decision. Use the recorded book and relevant evidence, then recommend what to do. The boundaries in `AGENTS.md` hold throughout; this file is how the decision lane runs.

## Answer a live decision

```bash
cd skills/fomo-kernel
python3 engine/review.py consider --premise '{"ticker":"NVDA","side":"buy","qty":20}' --language <tag>
```

A premise needs a `ticker`, a `side`, and one of `qty` or `notional`. Everything else is optional and engine-defaulted: an unstated price becomes the engine's own observed close, an unstated date reads as "if I did this next". `schemas/trade-premise.schema.json` is the field contract. The book comes from the user's recorded ledger — pass normalized trade CSVs as positional arguments only when no ledger exists yet.

Pass `--language` as the tag the user is writing in; an unsupported tag falls back to `en`. Keep conversing in their language and never hand-translate engine copy.

First run only: `pip install -r requirements.txt`, then `python3 engine/review.py doctor`. The engine fail-soft degrades without its optional dependencies — silently dropping current prices, P&L, alpha/beta, and market context — so verify once rather than discovering it inside an answer.

## The response is the contract

The payload is the authority for portfolio facts. External research is optional and relevance-driven; when used, keep it sourced and separate from engine facts.

- `evaluation.consequence` — the book `before` and `after` the trade, and the `delta`: weights, largest position, top three, sector and AI share, cash. Also `disclosures`, and the holdings the numbers were measured *without*.
- `evaluation.rule_collisions` — the user's own rules this trade touches, each with the `rule_effect` naming how it moves.
- `challenge` — computed for this call: `must_state` (portfolio facts the answer owes, each with its `anchor`), `rule_effects` (with `must_convey` / `must_not_convey` per rule), `quote_verbatim` (the user's own words, never relabeled as an outside source), `unchecked` (available research dimensions; surface only those material to the recommendation), and `case_required`.
- `disclosures_display` — each disclosure already written as a sentence in the user's language. Use it rather than translating a key.
- `prior_decision` — present only when the user already resolved one earlier consideration of this same ticker: their own stored words, and what they reported doing about it, never proof they did it. Use `prior_decision` only when it changes the current lead judgment, evidence requirement, process action, or the one question worth asking; otherwise ignore it.

Read the answer out of that payload. Do not recompute it, round it, extend it, or fill a gap in it. A number the engine did not state does not go in the answer.

## Research only what could change the recommendation

The engine computes portfolio consequence; it is not a company-research service. Do not fetch a standing market packet on every call. Look up current price, recent movement, valuation, an event, or operating evidence only when that fact is material to the user's question or could change the recommendation. A found event never becomes the user's motive until they confirm it is. `references/market-lookup.md` owns the bounded lookup and provenance contract.

## Shape of the answer

1. **Lead with the recommendation.** Say what to do and the reason that decides it. This may be proceed, choose one candidate, resize, reduce a named recorded position, delay, collect evidence, revise, cancel, or no trade.
2. **Support it with the few facts that carry the decision.** State a counter-case only when it could materially change the action; symmetry is not a requirement.
3. **State only material limitations.** Keep a truth-critical denominator, unit, or pricing set beside its number; place any other material evidence gap where it makes the recommendation clearest. Do not dump every unchecked dimension.
4. **Ask at most one question**, only when its answers would branch to different advice. It need not be last when natural dialogue makes another placement clearer. If nothing branches, ask nothing.
5. **Stop.** Do not append a mandatory workflow or resolution sentence.

Judgment of your own — thesis, valuation, timing, recommendation, and ranking — is welcome, labeled as yours and separable from engine facts. What stays out: price targets, market forecasts, a security neither the user named nor the recorded book contains, and any claim about what the user did or will do.

**Candidate comparison.** For bounded fan-out, run each candidate with `consider --ephemeral` against the existing recorded book; this computes the consequence without creating durable evaluation rows. Rank the candidates using portfolio consequence plus any relevant sourced research. Then rerun only the selected or still-live candidate without `--ephemeral` so the canonical evaluation records the decision actually being considered. Do not turn this into an open-ended stock screener: candidates come from the user or their recorded book.

Nothing about the engine, schemas, sessions, validators, retries, or this contract belongs in the answer.

## What the response may ask you for

- **Unpriced instruments.** The payload names them and how to hand them back. Look those closes up from the publisher's own page, transcribe them into the envelope in `references/price-feed.md`, and rerun with `--prices <path>`. Transcription, not analysis: the close, nothing else. If the sources genuinely publish nothing, `--prices-unavailable '<sources you checked>'` refuses the question instead of answering a forward decision on cost basis. Never invent, interpolate, or recall a price; a missing price is not a delisting and not a zero return.
- **No recorded book.** `consider` fails closed. Frame the decision instead, under `references/decision-framing.md`.

A refusal does not end the turn. You still owe the judgment that holds without the numbers the engine would not compute — say plainly what could not be checked and name what would unblock it, because the user's next move is to close that gap. Never present a degraded number as if it were the real one: a forward-looking decision is refused rather than answered on cost weights precisely because cost weights can invert which position is the largest.

## After the answer

Persistent `consider` records the evaluation; `consider --ephemeral` does not. When the user later says what they did, record it against the persistent evaluation rather than starting a new one:

```bash
python3 engine/review.py consider --resolve <evaluation_id> --decision acted|declined|modified
```

`acted` is the user's report, not proof. Only a later transaction import proves a trade happened. Never write, imply, or carry forward an execution the user has not reported or the ledger does not show.

## Other jobs

Reach for these when the user asks for them. None of them routes an ordinary decision.

| The user wants | Do this |
|---|---|
| To load or refresh their book from broker data | `references/data-contract.md`, then `prepare` or `refresh` |
| A periodic behavior-review card | `prepare`, then read only the flow it names in `review_plan.flow_path` |
| To see their positions | `python3 engine/review.py positions` |
| To try the experience with no data | `prepare --test-drive`, then pass `--root <review_plan.state_root>` to every later command of that session |
| To continue after an interruption | `python3 engine/review.py resume` — never refetch prices mid-session |
| A failed projection repaired | `python3 engine/review.py repair-projections` |

An ad hoc question gets a direct answer in text. Do not produce a chart, artifact, or multi-tool output the user did not ask for.
