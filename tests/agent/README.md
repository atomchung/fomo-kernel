# Agent-level evaluation harness

Implementation authority is [docs/eval-design.md](../../docs/eval-design.md). The harness separates checks according to the strongest available evidence.

## Layers

- **Offline deterministic checks**: regular-expression and JSON assertions over card/state artifacts. These run in `tests/run_all.py`.
- **LLM narrative judge**: optional and non-deterministic. It evaluates prose quality rather than mechanical contract violations and requires an API key.
- **Headless card generation**: optional, non-deterministic, and billable. It runs the skill and feeds resulting artifacts into the two layers above.

## Files

- `check_card.py`: deterministic card invariants from the card specification and eval design.
- `check_state.py`: finalization and trajectory artifacts not already owned by `coach.py` or JSON contract tests.
- `../../skills/fomo-kernel/tools/ux_receipt.py`: privacy-safe host capability and generated-versus-presented trajectory receipt.
- `../test_checkers_offline.py`: mutation probes that prove known-good artifacts pass and intentionally broken artifacts fail.
- `../test_interaction_trajectory.py`: deterministic native-control, text-fallback, card-delivery, and weekly-memory receipt probes.
- `judge_narrative.py`: optional narrative-quality rubric.
- `run_judge_eval.py`: mutation probes for the judge fixtures.
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
tests/agent/run_case.sh --headless tests/agent/cases/washer.yaml
```

## Headless limitation

Headless `claude -p` does not expose the same interactive question tool as a normal session, so it can exercise only the fixed text fallback. Artifact checkers remain valid because they inspect outputs rather than the internal conversation. `ux_receipt.py` makes the fallback and inline-card trajectory deterministic, but a real native control still requires an interactive session and an owner verdict; use `manual-cross-client-ux.md` for that gate.

## Maintenance rule

When card narrative policy changes, update `judge_narrative.py`. When machine-checkable assertions in `docs/eval-design.md` change, update the matching check in `check_card.py` or `check_state.py`. Keep the deterministic checks in CI and keep non-deterministic generation opt-in.
