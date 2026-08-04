---
name: fomo-kernel
description: Weigh a trade the user is deciding on against their own recorded book — resulting weight, concentration, driver overlap, cash, and any rule of their own it would break — and answer with one direct judgment. Use for pre-trade questions such as should I buy this, am I chasing, should I add here, is this too big, or does this break my own rule; also for trade reviews, transaction postmortems, brokerage-statement reviews, and position reviews, in any language. Not a price-target or market-forecast tool.
---

# fomo-kernel

The user is making or revisiting a live trading decision. Compute what it does to their own book, then answer it. The boundaries in `AGENTS.md` hold throughout; this file is how the decision lane runs.

## Answer a live decision

```bash
cd skills/fomo-kernel
python3 engine/review.py consider --premise '{"ticker":"NVDA","side":"buy","qty":20}' --language <tag>
```

A premise needs a `ticker`, a `side`, and one of `qty` or `notional`. Everything else is optional and engine-defaulted: an unstated price becomes the engine's own observed close, an unstated date reads as "if I did this next". `schemas/trade-premise.schema.json` is the field contract. The book comes from the user's recorded ledger — pass normalized trade CSVs as positional arguments only when no ledger exists yet.

Pass `--language` as the tag the user is writing in; an unsupported tag falls back to `en`. Keep conversing in their language and never hand-translate engine copy.

First run only: `pip install -r requirements.txt`, then `python3 engine/review.py doctor`. The engine fail-soft degrades without its optional dependencies — silently dropping current prices, P&L, alpha/beta, and market context — so verify once rather than discovering it inside an answer.

## The response is the contract

Everything the answer needs is in the payload. No other file is required to write it.

- `evaluation.consequence` — the book `before` and `after` the trade, and the `delta`: weights, largest position, top three, sector and AI share, cash. Also `disclosures`, and the holdings the numbers were measured *without*.
- `evaluation.rule_collisions` — the user's own rules this trade touches, each with the `rule_effect` naming how it moves.
- `challenge` — computed for this call, and the closest thing to a checklist you get: `must_state` (facts this answer owes, each with the record `anchor` it came from), `rule_effects` (with `must_convey` / `must_not_convey` per rule — say the first, never the second), `quote_verbatim` (the user's own words, reproduced unreworded and never relabeled as an outside source), `unchecked` (what nobody looked at — silence here reads as a clean bill of health), and `case_required`.
- `disclosures_display` — each disclosure already written as a sentence in the user's language. Use it rather than translating a key.

Read the answer out of that payload. Do not recompute it, round it, extend it, or fill a gap in it. A number the engine did not state does not go in the answer.

## Shape of the answer

1. **Lead with the judgment.** One process action — proceed, probe smaller, reduce, delay, collect evidence, revise, cancel, or no trade — and the one tension that decides it. Not a preamble, not a summary of what you are about to do.
2. **Give the counter-case to that lead**, engaging its strongest support rather than setting an unrelated warning beside it. Not an equal-weight list of pros and cons.
3. **Attach each material limitation once**, beside the claim it qualifies.
4. **Ask at most one question, last**, and only when its answers would branch to different advice. If nothing branches, ask nothing.
5. **Stop.** When the evidence supports the trade, say so and stop.

Judgment of your own — thesis, valuation, timing — is welcome, labeled as yours and separable from the engine's facts. What stays out: price targets, market forecasts, a security the user did not nominate, and any claim about what the user did or will do.

Nothing about the engine, schemas, sessions, validators, retries, or this contract belongs in the answer.

## What the response may ask you for

- **Unpriced instruments.** The payload names them and how to hand them back. Look those closes up from the publisher's own page, transcribe them into the envelope in `references/price-feed.md`, and rerun with `--prices <path>`. Transcription, not analysis: the close, nothing else. If the sources genuinely publish nothing, `--prices-unavailable '<sources you checked>'` refuses the question instead of answering a forward decision on cost basis. Never invent, interpolate, or recall a price; a missing price is not a delisting and not a zero return.
- **No recorded book.** `consider` fails closed. Frame the decision instead, under `references/decision-framing.md`.

## After the answer

The engine records the evaluation. When the user later says what they did, record it against that same evaluation rather than starting a new one:

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
