---
name: fomo-kernel
description: Help with investment decisions and explicit candidate searches — research, discover, compare, rank, and recommend with or without a recorded book. Use for buy, add, reduce, trade-review, brokerage-statement, and position-review decisions. A book adds engine-computed portfolio consequences. Never claim unsupported portfolio facts or execution.
---

# fomo-kernel

Use relevant evidence and the recorded book when portfolio consequences matter; then recommend what to do. Missing inputs narrow claims, not Skill engagement. `AGENTS.md` holds throughout.

## Answer a live decision

Use `consider` when the user supplies a trade premise and asks what it does to a recorded book. It is the deterministic portfolio-consequence path, never a prerequisite for research, discovery, or a non-portfolio recommendation.

```bash
cd skills/fomo-kernel
python3 engine/review.py consider --premise '{"ticker":"NVDA","side":"buy","qty":20}' --language <tag>
```

A premise needs a `ticker`, a `side`, and one of `qty` or `notional`. Everything else is optional and engine-defaulted: an unstated price becomes the engine's own observed close, an unstated date reads as "if I did this next". `schemas/trade-premise.schema.json` is the field contract. The book comes from the user's recorded ledger — pass normalized trade CSVs as positional arguments only when no ledger exists yet.

Pass `--language` as the tag the user is writing in; an unsupported tag falls back to `en`. Keep conversing in their language and never hand-translate engine copy.

First run only: `pip install -r requirements.txt`, then `python3 engine/review.py doctor`. The engine fail-soft degrades without its optional dependencies — silently dropping current prices and market context — so verify once rather than mid-answer.

## The response is the contract

The payload is the authority for portfolio facts. External research is optional and relevance-driven; when used, keep it sourced and separate from engine facts.

- `evaluation.consequence` — the book `before` and `after` the trade, and the `delta`: weights, largest position, top three, sector and AI share, cash. Also `disclosures`, and the holdings the numbers were measured *without*.
- `evaluation.rule_collisions` — the user's own rules this trade touches, each with the `rule_effect` naming how it moves.
- `challenge` — this call's `must_state` (facts the answer owes, with `anchor`s), `may_state` (owed on no call — state one only when it decides this call), `machine_state` (machines only, never rendered), `rule_effects` (`must_convey` / `must_not_convey` per rule), `quote_verbatim` (the user's own words, never relabeled as an outside source), `unchecked` (never enumerated), and `case_required`.
- `disclosures_display` — each disclosure as a sentence in the user's language. Use it as an end-block line rather than translating a key.
- `prior_decision` — present only when the user already resolved one earlier consideration of this same ticker: their own stored words, and what they reported doing about it, never proof they did it. Use `prior_decision` only when it changes the current lead judgment, evidence requirement, process action, or a decision-changing question; otherwise ignore it.

Read portfolio consequence from that payload; never recompute or fill its gaps. An absent portfolio-derived number stays out. Public numbers need source and as-of, and neither they nor a forecast substitutes for portfolio fact.

## Research only what could change the recommendation

The engine computes portfolio consequence; it is not a company-research service. Look up current price, recent movement, valuation, an event, or operating evidence only when that fact is material to the user's question or could change the recommendation. A found event never becomes the user's motive until they confirm it is. `references/market-lookup.md` owns the bounded lookup and provenance contract.

## Shape of the answer

One shape, every answer (`../../docs/expression-contract.md` §3 owns it; this is its projection, not a second wording). **A fact lives on exactly one floor, and twice is a bug.** *Top:* one sentence — the stance and the reason that decides it (proceed, resize, delay, collect evidence, choose one candidate, no trade). *Middle:* only blocks that add a new decision-relevant fact or judgment — delete one; if the decision does not change, delete it. There live the numbers that would flip the call, every `rule_effects` entry (never optional), a truth-critical denominator, unit, or pricing set beside its number, and a falsifier on any directional call — the counter-case needs no section. *Bottom:* the rest of the inventory stays in the data layer; say once you can expand it. *End:* one compact block for other material limitations; machine anchors and engine narration nowhere.

Never manufacture a scenario nobody asked for, restate a system default as insight, hedge in couplets, or make one point twice. Ask only decision-changing questions, then stop. `references/trade-consequence.md` holds the rest.

Label judgment — thesis, valuation, timing, forecast, recommendation, ranking, selection — separate from engine facts. Give a target or forecast's material assumptions and uncertainty; never disguise it as fact or certainty. Never claim what the user did or will do.

**Candidate discovery and comparison.** For an explicit search, report universe,
filters, as-of point, material exclusions, and coverage limits; never imply
exhaustive coverage. Stop by marginal decision value, cost, and latency. When
book consequence matters, run each candidate with `consider --ephemeral`, rank
from those results plus sourced research, then rerun only the user-selected or
still-live candidate without the flag. Rejected candidates leave no canonical
evaluation row.

## What the response may ask you for

- **Unpriced instruments.** The payload says how to return them. Read closes from the publisher's page, transcribe the `references/price-feed.md` envelope, and rerun with `--prices <path>`. If none are published, `--prices-unavailable '<sources checked>'` refuses only the current-value portfolio consequence; still give supported non-portfolio judgment. Never invent, interpolate, or recall a price; missing is not delisted or zero.
- **No recorded book.** `consider` fails closed for book-derived claims. Continue with supported research and judgment, and frame the decision under `references/decision-framing.md`; do not manufacture portfolio precision or persist the conversation.

A refusal does not end the turn. You still owe the judgment that holds without the numbers the engine would not compute — say plainly what could not be checked and name what would unblock it. Never present a degraded number as if it were the real one: a forward-looking decision is refused rather than answered on cost weights, which can invert which position is the largest.

## After the answer

Persistent `consider` records the evaluation; `consider --ephemeral` does not. Say once that a record is a consideration, not an execution. When the user later says what they did, record it against the persistent evaluation rather than starting a new one:

```bash
python3 engine/review.py consider --resolve <evaluation_id> --decision acted|declined|modified
```

`acted` is the user's report, not proof. Only a later transaction import proves a trade happened. Never write, imply, or carry forward an execution the user has not reported or the ledger does not show.

## Other jobs

Reach for these when the user asks. None routes an ordinary decision.

| The user wants | Do this |
|---|---|
| To load or refresh their book from broker data | `references/data-contract.md`, then `prepare` or `refresh` |
| A periodic behavior-review card | `prepare`, then read only the flow it names in `review_plan.flow_path` |
| To see their positions | `python3 engine/review.py positions` |
| To try the experience with no data | `prepare --test-drive`, then pass `--root <review_plan.state_root>` to every later command of that session |
| To continue after an interruption | `python3 engine/review.py resume` — never refetch prices mid-session |
| A failed projection repaired | `python3 engine/review.py repair-projections` |

A simple ad hoc question defaults to a fast, direct text answer; scale research, tools, and visuals to decision value and report material coverage limits (`references/freeform-answers.md`).
