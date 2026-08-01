# Consider-flow synthetic-user evaluator

Automated end-to-end evaluation of the `consider` (pre-trade evaluation)
flow. One command drives a bounded synthetic user through the real engine,
captures the answer, and grades it with existing mechanical checks and the
rubric judge.

## What this proves

For each scenario in the corpus:

1. **The engine runs** — `review.py prepare` builds a fixture,
   `review.py consider` computes a consequence and emits a challenge block.
2. **A bounded LLM can satisfy the challenge** — the synthetic user prompt
   is a constraint (the challenge block), not a behavior oracle.
3. **Mechanical invariants hold** — the produced answer passes
   `number_provenance`, `privacy_trace`, `surface_hygiene`, and (for
   refusal shapes) `usable_facts_grounding`, all imported from
   `run_episodes.py`.
4. **Rubric axes hold** — `two_sided` and `overrulable`, graded by the
   same judge infrastructure as the episode bank.

## What this does not prove

- **Answer quality.** The prompt tells the LLM what facts to include, not
  how to phrase them. A mechanically clean answer can still be useless.
  That gap is the rubric judge's, and it is opt-in.
- **Real-user behavior.** A synthetic user that follows the prompt is not
  evidence that a real agent follows `trade-consequence.md`. The episode
  bank grades real misses; this grades the constraint's own coverage.
- **Calibration.** Every finding is `declared_by: agent`. Owner ratification
  is separate, matching the episode bank's existing contract.

## The synthetic user prompt

The prompt is deliberately a **constraint**, not a **behavior oracle**:

- It names what facts must appear (from the challenge block).
- It names what numbers are allowed (from the engine payload).
- It names structural requirements (two-sided, resolution sentence).
- It does **not** name phrasing, ordering, tone, or length.

A miss means either the prompt has a blind spot (the constraint is
incomplete) or the model genuinely violated a boundary (the constraint
caught something).

## `declared_by: agent`

Every finding — mechanical and judge — carries `declared_by: agent`.
This means:

- A green run is "the agent's own hypothesis reproduced," not "the answer
  is good."
- A red run is a genuine finding — either the model or the prompt needs
  attention.
- Owner ratification converts a finding to `declared_by: owner`, at which
  point it becomes calibration, not hypothesis.

## Scenarios

| ID | Shape | Key test |
|---|---|---|
| `add_to_position` | Normal buy, priced on cost, with decision context | Challenge block coverage, verbatim quotes, two-sided case |
| `sell_existing` | Sell half of largest position | `worsens: false` on `already_over` correctly described |
| `partial_book` | One holding excluded, rest answers | `partial_book` disclosure in same breath as the number |
| `whole_book_refusal` | Non-recoverable refusal | `usable_facts_grounding` — cite only the bounded packet |

## Usage

```bash
# Show what would run (no LLM calls, no cost)
python3 evals/run_consider_episodes.py --plan

# Run all scenarios (billable)
python3 evals/run_consider_episodes.py

# Run one scenario
python3 evals/run_consider_episodes.py --scenario add_to_position

# Include rubric judge (additional LLM calls)
python3 evals/run_consider_episodes.py --judge

# Save results to a file
python3 evals/run_consider_episodes.py --output results.json
```

## Environment variables

Same as `judge_episodes.py`:

| Variable | Default | Purpose |
|---|---|---|
| `TR_JUDGE_BACKEND` | `auto` | `agy`, `anthropic`, or `auto` |
| `TR_JUDGE_MODEL` | per backend | Override the model |
| `TR_JUDGE_RUNS` | `3` | Samples per judge axis |
