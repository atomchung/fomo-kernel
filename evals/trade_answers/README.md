# TradeEvaluation answer witnesses

This directory holds the first bounded answer-level evaluation asset for #590.
It is **maintainer tooling**, not a runtime dependency and not product
acceptance.

## What is evaluated

Only four semantic properties that deterministic code cannot honestly settle:

- `decision_focus` — one highest-value tension is visible early;
- `internal_consistency` — lead, support, counter-case, and limitations can all
  be true together;
- `decision_synthesis` — grounded facts become a decision-specific trade-off;
- `caveat_discipline` — limitations stay attached and proportionate.

The axis meanings are the #590 specialization of the global voice rules in
`docs/output-voice.md`: V4, V5, V6, and the decision-value posture. This folder
does not create a second product voice contract.

## Gate order

```text
synthetic frozen context
→ deterministic truth / coverage eligibility
→ independent semantic judge
→ per-axis report
→ owner calibration
```

An answer marked `eligible_for_judge: false` is never sent to a model. The
semantic judge therefore cannot turn an unsupported or incomplete answer into a
pass.

## Run

```bash
python3 evals/judge_trade_answers.py --plan
python3 evals/judge_trade_answers.py
TR_JUDGE_RUNS=5 python3 evals/judge_trade_answers.py TA-001
```

Backend, model, repeated-vote, fail-closed parsing, and ambiguity behavior are
inherited from `evals/judge_episodes.py`.

## Trust boundary

- One answer is judged alone. Paired or repaired answers, expected verdicts,
  fixture title, and maintainer notes are hidden from the model.
- A tie or unreadable output is `ambiguous`, never a pass.
- Results are reported per axis. There is no aggregate quality score.
- `declared_by: agent` means uncalibrated. Only owner review may change it to
  `owner`.
- Fixtures are fictional. Real holdings, trades, amounts, dates, reasons, or
  private-repository content must never enter this directory.

## Adding a witness

Add a new failure shape only after an observed product miss cannot be diagnosed
by the existing witnesses. A passing answer is evidence that the invariants can
hold; it is not a reference answer or wording template.
