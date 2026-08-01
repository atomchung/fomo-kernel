# TradeEvaluation answer witnesses

This directory holds bounded, synthetic answer-level evidence for #590. It is
maintainer tooling, not a runtime dependency and not product acceptance.

## Two gates, two separate lanes

The deterministic lane runs the real production gate:
`answer_provenance.validate_agent_case`. It resolves each answer's
`agent_case_ref` against that fixture's frozen `basis`, `consequence`, and
`rule_collisions`, and supplies the frozen context's exact `reason` and
`why_now` as user statements. Unsupported anchors, missing required coverage,
one-sided cases, and a user statement promoted to `public_fact` fail closed
before any model call. `expect_eligible` is a fixture expectation checked
against that result; it is not a trusted eligibility switch. This validates
the referenced structured case, not every sentence in the answer's free prose.

The semantic lane runs only for deterministically eligible answers. The judge
sees one answer, the question, and the frozen evaluation facts. It does not see
sibling answers, witness IDs, expected failures, `agent_case_ref`, eligibility
expectations, fixture titles, or maintainer notes. The deterministic and
semantic lanes are separate and may run concurrently; neither lane's verdict
is used as the other lane's input.

## What the semantic judge evaluates

| LLM axis | Voice mapping | Relationship to the independent #610 human lane |
|---|---|---|
| `decision_focus` | V1, V4 | Additional #590 diagnostic; #610 has no standalone focus check |
| `internal_consistency` | No direct V-ID | Additional #590 diagnostic; not V5 and not a counter-case verdict |
| `decision_synthesis` | V3 plus the global decision-value posture | Partial evidence for #610's owner check that the answer adds a new connection; structured grounding remains deterministic |
| `caveat_discipline` | V6 | Partial evidence for #610's owner check that the answer still stands after limitation sentences are covered |

The production gate validates the structured `agent_case`; it does not
mechanically prove every sentence in the free prose. The semantic judge is also
not asked to reproduce that gate.

These axes specialize the global voice contract in `docs/output-voice.md`; they
do not create a second product voice contract.

## Run

```bash
python3 evals/judge_trade_answers.py --plan
python3 evals/judge_trade_answers.py
TR_JUDGE_RUNS=5 python3 evals/judge_trade_answers.py TA-001
python3 evals/judge_trade_answers.py --history 20
```

Backend, model, repeated-vote, fail-closed parsing, and ambiguity behavior are
inherited from `evals/judge_episodes.py`. A tied vote is `ambiguous`; an
unreadable sample is unusable and fails the run even when the remaining votes
agree. Results remain per axis rather than being folded into an aggregate
quality score. Each durable receipt records separate fixture and judge-contract
digests, so history does not treat a rubric or system-prompt change as the same
evaluation run.

## Trust and acceptance boundary

- Fixtures contain synthetic facts only. Real holdings, trades, amounts,
  dates, reasons, or private-repository content never belong here.
- `declared_by: agent` means uncalibrated. Only owner review may change it to
  `owner`; this bank carries no owner ratification.
- #610 still owns its four human checks. In particular, a real V5 counter-case
  and a more-concrete next step have no equivalent LLM axis in this slice.
- An LLM pass is not #610 acceptance and is not product acceptance. It is one
  bounded diagnostic result over one frozen synthetic answer.

## Adding a witness

Add a new failure shape only after an observed product miss cannot be diagnosed
by the existing orthogonal witnesses. A passing answer shows that the named
invariants can coexist; it is not a reference answer or wording template.
