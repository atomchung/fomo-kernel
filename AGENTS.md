# AGENTS.md — fomo-kernel

> Thin routing guidance for Codex, Cursor, Claude Code, and other coding agents. Human-facing product documentation lives in [README.md](README.md). The only cross-agent workflow entry point is [skills/fomo-kernel/SKILL.md](skills/fomo-kernel/SKILL.md).

## When to trigger

Trigger when a user asks for a trade review, transaction postmortem, brokerage-statement review, or provides a trade CSV or position snapshot.

## Workflow

1. Read `skills/fomo-kernel/SKILL.md` completely.
2. Normalize brokerage data locally. Do not require the user to reformat it.
3. Start from the single orchestration entry point:

   ```bash
   cd skills/fomo-kernel
   python3 engine/review.py prepare <CSV...> --language en
   ```

4. Read the returned `review_plan.flow_path` and shared references. Ask only questions in `question_queue` with `required:true`. Use a native option UI when available; otherwise present the same options in plain text.
5. Produce schema-valid answers and a narrative with no digits, then call `preview`. Fix rejected artifacts instead of bypassing the validator.
6. Show the review-card preview (`card-private-preview.md`) and ask the user to choose one candidate rule, supply a custom rule, or skip. Write that choice to `answers.commitment` and call `finalize`.
7. Deliver the review card at `sessions/<id>/card-private.md`. Deliver `card-public.md` only when the user asks for a share-safe artifact; there is no publishing feature yet.

After an interruption, use `review.py resume`; do not refetch live prices. If a projection fails, use `review.py repair-projections`. An existing canonical session is not data loss.

Test drive (`prepare --test-drive`) runs in an isolated root: pass `--root <review_plan.state_root>` to every later command of that session.

## Non-negotiable boundaries

1. Numbers, rankings, cycle IDs, metrics, and ETF exemptions come from code. The agent must not calculate, invent, or alter them.
2. Do not provide buy or sell recommendations. Review behavior, motives, thesis evolution, and the next process rule.
3. Required motive questions cannot be skipped. A `new_evidence` decision requires both a claim and a source.
4. Each card has at most one final rule, chosen by the user. Skipping is valid.
5. Keep trade data local and out of cloud memory. Never mix private-card content into a public card.
6. Invoke the engine only through the `engine/review.py` CLI (`prepare`, `resume`, `preview`, `finalize`, or `repair-projections`). Never call another `engine/*` script or import engine modules directly; those paths bypass lifecycle validation, required-question gates, and canonical session state.

## Why this bridge stays thin

Claude, Codex, and Cursor perform the same small set of high-value judgments. Mode flows, schemas, validators, session commits, and renderers are shared repository code. A thin bridge prevents each agent from maintaining a separate long prompt that drifts over time.
