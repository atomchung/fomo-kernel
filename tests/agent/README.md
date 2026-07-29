# Agent-level evaluation harness

Implementation authority is [docs/eval-design.md](../../docs/eval-design.md). The harness separates checks according to the strongest available evidence.

## Layers

- **Offline deterministic checks**: regular-expression and JSON assertions over card/state artifacts. These run in `tests/run_all.py`.
- **LLM narrative judge**: optional and non-deterministic. It evaluates prose quality rather than mechanical contract violations and requires an API key.
- **Headless card generation**: optional, non-deterministic, and billable. It runs the skill and feeds resulting artifacts into the two layers above.

## Files

- `check_card.py`: deterministic card invariants from the card specification and eval design. `check_ticker_diagnosis()` (B-1, #542) is a separate, case-driven function: it takes a subject ticker plus forbidden/allowed instrument-tag codes as arguments rather than reading them from a constant, because that identity is case data — see `cases/washer.yaml`'s `subject_ticker`/`subject_forbidden_tags`/`subject_allowed_tags` and how `run_case.sh --headless` forwards them as `--ticker`/`--forbidden-tags`/`--allowed-tags`.
- `check_state.py`: finalization and trajectory artifacts not already owned by `coach.py` or JSON contract tests.
- `../../skills/fomo-kernel/tools/ux_receipt.py`: local presentation trace — host capability plus generated-versus-presented card evidence, stored in the protected state dir (`~/.trade-coach/ux/`).
- `../test_checkers_offline.py`: mutation probes that prove known-good artifacts pass and intentionally broken artifacts fail.
- `../test_interaction_trajectory.py`: deterministic native-control, text-fallback, card-delivery, and weekly-memory presentation-trace probes.
- `judge_narrative.py`: optional narrative-quality rubric.
- `run_judge_eval.py`: mutation probes for the judge fixtures. Each run appends one line to `judge/narrative-runs.jsonl` inside the protected state directory (`--state-root` conventions: `TRADE_COACH_HOME`, else `~/.trade-coach`), never into this repository. A single run only says whether the judge passed today; the record is what makes gradual blunting visible — a bad card drifting from 1 to 4 still reads as a pass on any one run. `--history` is the reader, and flags a fixture whose verdict changed since the previous recorded run.
- `../test_judge_harness_offline.py`: the two files above have interlocks that are pure logic — the manifest gate, the refusal branch, and the request and schema shape. This probes them offline, so they are re-verified without an API key and run inside `tests/run_all.py`.
- `fixtures/`: known-good and intentionally broken card examples.
- `personas.md`: scripted users and differential pairs.
- `cases/*.yaml`: input, persona, run mode, and assertion declarations.
- `run_case.sh`: offline checking and optional headless orchestration.

## Commands

```bash
python3 tests/test_checkers_offline.py
python3 tests/agent/check_card.py tests/agent/fixtures/card_good.txt
python3 tests/agent/run_case.sh --check my_card.md ~/.trade-coach

export ANTHROPIC_API_KEY=...
python3 tests/agent/run_judge_eval.py
python3 tests/agent/run_judge_eval.py --history   # recorded runs, no API call
tests/agent/run_case.sh --headless tests/agent/cases/washer.yaml

# B-1 (#542): case-declared subject ticker + tag codes, checked directly
python3 tests/agent/check_card.py my_card.md \
    --ticker INTC --forbidden-tags suspected_dca --allowed-tags suspected_averaging_down_losing
```

## Headless limitation

Headless `claude -p` does not expose the same interactive question tool as a normal session, so it can exercise only the fixed text fallback. Artifact checkers remain valid because they inspect outputs rather than the internal conversation. `ux_receipt.py` makes the fallback and inline-card trajectory deterministic, but a real native control still requires an interactive session and an owner verdict; use `manual-cross-client-ux.md` for that gate.

## Maintenance rule

When card narrative policy changes, update `judge_narrative.py`. When machine-checkable assertions in `docs/eval-design.md` change, update the matching check in `check_card.py` or `check_state.py`. Keep the deterministic checks in CI and keep non-deterministic generation opt-in.
