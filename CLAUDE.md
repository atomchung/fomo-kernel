# CLAUDE.md — Maintainer guide

> This file is for contributors changing the repository. Runtime behavior is defined by [skills/fomo-kernel/SKILL.md](skills/fomo-kernel/SKILL.md); [AGENTS.md](AGENTS.md) is only a thin cross-agent router. Do not duplicate the complete runtime contract here.

## Repository role

`fomo-kernel` is a public repository that external users can clone and install. The deterministic Python engine lives in `skills/fomo-kernel/engine/`. `SKILL.md` defines runtime orchestration, and `AGENTS.md` routes agents that do not automatically discover skills.

## Development discipline

[docs/development-guide.md](docs/development-guide.md) is the failure-mode ledger distilled from shipped mistakes; update it there, with receipts, when a new pattern earns a place. The four rules that must survive every session:

- A new checker lands with proof its matching mutation fails; a checker that stays green under its mutation is not evidence.
- Before touching a renderer or output path, list the surfaces the changed code path reaches and name the oracle covering each.
- Prefer generating a synchronized surface over hand-mirroring it (`skills/fomo-kernel/tools/design_bundle.py` is the precedent).
- The second recurrence of a symptom is a design issue, not a second patch.

## Contract synchronization

- Treat `skills/fomo-kernel/SKILL.md` as the runtime contract entry point. If engine behavior changes what a user sees, update the relevant flow, reference, schema, renderer contract, and the thin summary when necessary in the same change.
- Keep `AGENTS.md` limited to routing and non-negotiable boundaries.
- Keep developer documentation and skill instructions in English. Follow [docs/language-policy.md](docs/language-policy.md) for the GTM and localization exceptions.

## Honesty decisions belong in code

`build_honesty_ledger()` decides which limitations a card must disclose, including alpha credibility, missing live prices, incomplete sector attribution, unknown drivers, orphan sells, currency mixing, cash reliability, and ETF metadata gaps.

- Put disclosure conditions in the engine. Put locale-specific wording in renderer copy. Do not scatter new `if field exists, add a sentence` instructions through `SKILL.md`.
- Treat the ledger as an internal rendering gate, not a checklist printed on the card. The card should remain a coherent story.
- Keep `SKILL.md` thin. New honesty keys should not make the entry-point prompt grow.

The synchronization chain is: `build_honesty_ledger()` ↔ renderer and copy ↔ card policy ↔ eval design ↔ contract tests.

## Tests

Run before and after changing the engine or runtime contract:

```bash
python3 tests/run_all.py
TR_TEST_NETWORK=1 python3 tests/run_all.py  # optional beta-direction and market-context network smoke
```

The default suite is offline, deterministic, and does not require pytest. It covers engine units, JSON/state contracts, price paths, the snapshot-anchored ledger, revisit/swap behavior, market context, problem tracking, persona fixtures, the state loop, artifact checkers, local data controls, session idempotency, the v2 review lifecycle, the card copy corpus, the question-episode bank's mechanical half ([evals/episodes/](evals/episodes/README.md)), documentation language, and agent workflow boundaries.

After an intended wording change, regenerate the copy golden in the same commit and read its diff:

```bash
python3 tests/copy_corpus.py --update
```

Do not commit after changing engine output, price handling, sorting, or orchestration unless the complete offline suite passes.

## Dogfood QA

Maintainer dogfood QA on every client (Claude Code, Codex, Antigravity, ...) follows [docs/qa-runbook.md](docs/qa-runbook.md): latest-main version gate, isolated `TRADE_COACH_HOME` root, `ux_receipt` coverage, an archived manifest, and `tools/privacy_lint.py` on any real-data text before it is posted to this public repository (#274). A run that skips any gate is not a QA run and its results are not citable as QA evidence.

## Claude Code hooks

Committed hooks in `.claude/` enforce the test gate. Hook `if:` filters have been observed to be unreliable in the supported Claude Code setup. Every hook script must inspect `tool_input.command` from stdin and exit immediately for unrelated commands. Follow the self-filtering pattern in `pre_commit_test_gate.sh`.

## Privacy boundary

`.gitignore` blocks real CSV files and allows only fixtures under `skills/fomo-kernel/mock/`. Do not weaken or bypass this mechanism. Never include real trade records in commits, tests, or documentation examples.

## Commit and PR conventions

Follow the existing history:

```text
<type>(<scope>): <description> (closes #NN) (#PR)
<type>: <description>
```

Check `gh issue list`, `gh pr list`, and `git log --grep` before opening work so you do not duplicate an active or completed fix.

When multiple sessions are active:

- Claim the issue before editing and check for overlapping PRs.
- Fetch before creating a branch from the latest `origin/main`.
- Search fixtures, documentation, and tests for other instances of the same root cause.
- Before merging several PRs, review semantic overlap as well as textual conflicts. The persona sweep (`tests/persona_sweep.py`) runs inside `tests/run_all.py`, so its gates are already enforced; what is **not** automatic is its byte-parity half. When the engine changed, run `python3 tests/persona_sweep.py --baseline <other-checkout>/skills/fomo-kernel/engine` and read the Markdown drift against pre-change main — every drifting card should be one you meant to change.
- Remove worktrees and local branches only after confirming the merged commit is reachable from main and no other session uses them.

## Mirrored surfaces

| Fact | Surfaces that must stay synchronized |
|---|---|
| Output structure & language | `docs/output-contract.md` (single authority on section order) and `docs/output-language.md` (locale contract) ↔ `card_renderer.py` ↔ `references/card-policy.md` / `card-spec.md` (subordinated: wording and in-block ranking only) |
| Runtime behavior | engine ↔ `SKILL.md` and routed flows/references ↔ `docs/eval-design.md` ↔ `evals/EVALS.md` |
| Demo card values | English README ↔ English demo HTML/image; Traditional Chinese README ↔ Traditional Chinese demo HTML/image. Values must match; only wording differs. |
| GTM documentation | `README.md` is the English default; `README.zh-TW.md` is the complete Traditional Chinese counterpart. Keep language links and substantive product claims synchronized. |
| Hole number-line copy | `trade_recap.number_line()` (v1 human-card zh narration) ↔ `card_renderer._hole_line()` (v2 card; independent en implementation). The two per-dimension number narrations must stay semantically in sync. |
| Agent-supplied price envelope | `price_feed.py` (parser and adapters) ↔ `schemas/price-feed.schema.json` ↔ `references/price-feed.md` ↔ `review.py prepare --prices` ↔ `tests/test_price_feed.py`. The envelope's field rules are stated once in the schema; the reference explains when and how the agent may fill it. |
| Agent-supplied condition envelope (#412) | `conditions.py` (the validator, the tier derivation, and the comparison) ↔ `schemas/condition-slot.schema.json` (its `input` sub-object is `$ref`-ed by `answers.schema.json` and `evals/episodes/episode.schema.json`, so the envelope is defined once) ↔ `references/condition-slots.md` ↔ `review.py` `_slot_commitment` ↔ `tests/test_conditions.py`. `evals/run_episodes.py`'s `condition_integrity` check imports the engine validator rather than restating its rules, and reads numbers with `conditions.numbers_in` on both sides so the check and the gate cannot disagree about what a number is. The card's own read of a slot is `card_renderer.condition_state_line` ↔ `copy/*.json` `condition_state` ↔ the `condition_state/*` scenes in `tests/copy_corpus.py`. |
| Per-period condition check flow (#412 second half, #434) | `conditions.py` `build_check` / `checks_for_line` / `previous_check_for` / `last_check_for` ↔ `schemas/condition-check.schema.json` (its `input` sub-object is `$ref`-ed by `answers.schema.json` and `episode.schema.json`) ↔ `review.py` (`CONDITION_LOOKUP_CAP`, `_condition_due`, `_condition_questions`, `_build_condition_records`) ↔ `session.py`'s `condition_checks.jsonl` projection ↔ `references/condition-slots.md` ↔ `flows/weekly-review.md` step 0b ↔ `tests/test_review_v2.py`. Three bounds, one disclosure: `CONDITION_LOOKUP_CAP` (plan), `CONDITION_CROSSING_LIMIT` (questions), and `card_renderer.CONDITION_CARD_LINES` (card) all drop something, and `card_renderer._condition_summary_line` is the single place that says so — it reads `condition_slots_summary.lines_total` *and* the classification `_condition_outcomes` gives every check, which is the same one the card lines render from. A condition is silent only when it is **settled** (the user answered, or nothing was raised); "a question was queued" is not settlement, because a skip and an undelivered question both leave the row unanswered. A check row carries **two independent dispositions** — a crossing axis and a basis axis — and they must never be collapsed into one value: a row can be open on both at once, it prints both notes on one reading, and the summary counts *concerns*, not rows. A bound that stops being disclosed is the same defect as an unchecked condition presented as fine. Line identity has one reader: `conditions.slot_line_id`, called in `review.py` (which stamps `line_id` on every due entry and on `prior_commitment.condition`); the renderer only ever compares stamped values. `evals/run_episodes.py`'s `condition_check_integrity` imports `build_slot`/`build_check` rather than restating them, and its fixture seeds the standing condition from the episode's own answers so there is no second declaration to drift. |
| Question kinds | `review.py` `_question_queue` ↔ `schemas/review-plan.schema.json` `question_queue.items.kind` enum **and** its per-kind payload properties (`additionalProperties: false`) ↔ `question_surface.ELIGIBLE_KINDS` + its requirement maps ↔ `evals/run_episodes.py` `QUESTION_CONSUMERS` (which reads the kind list from that same enum, so an undeclared kind fails the suite) ↔ `references/interaction-delivery.md`. An agent-authored kind additionally needs `schemas/question-opportunity.schema.json` (`intent`, `context`) and the `grounding_ref` enum, which is mirrored in `question-surface.schema.json`, `answers.schema.json`, and `question_surface.GROUNDING_REFS`. |
| Agent-supplied condition-check envelope (#412) | `conditions.py` (`build_check`, the `information_state` identity comparison, and the `revises`-chain readers `slot_line_id`/`fold_slots`/`previous_check_for`) ↔ `schemas/condition-check.schema.json` (its `input` sub-object mirrors the slot envelope's `input` pattern) ↔ `tests/test_conditions.py`. This pair is the pure-engine half only: nothing in `review.py`, `session.py`, or `card_renderer.py` calls it yet, and the mirrored surfaces those files would add — a `references/condition-checks.md`, a `review.py` check-and-verdict binding, a card copy scene — land with the flow-wiring PR that makes this row grow, not this one. |
| HTML card design system | `card-template.html` is generated by `tools/gen_card_template.py` from `card-template.src.html` plus the runtime's own `_HTML_SHIM_CSS` / `_HTML_WIDGET_CSS` literals in `card_renderer.render_html()` (#401) — it is not hand-mirrored, so it cannot disagree with the runtime on palette, dark-mode rules, or layout constraints; `tests/test_card_html.py::test_card_template_matches_its_generator` fails the suite if the committed file and a fresh regeneration disagree. Design-provenance prose and template-only illustrative CSS (a few icons, one accessibility idea the runtime does not implement yet) are authored in `card-template.src.html`, not in the generated file. Before adding or reshaping a visual element, read [docs/design-guidelines.md](docs/design-guidelines.md): it defines the module slot contracts that keep one element's change from forcing its neighbours to be re-tuned. |
| Card copy on the branches no persona reaches | `skills/fomo-kernel/copy/*.json` ↔ `tests/golden/copy-corpus.txt`, generated by `tests/copy_corpus.py --update` from real `render_private`/`render_html`/`render_public` output (#402 knife 5). Never hand-edit the golden, and never pin a catalog sentence as a literal inside a test again: add a scene instead. `tests/copy_corpus.py` also fails when a key in a claimed register reaches no surface, so dead copy and a renderer that silently stopped emitting a sentence both surface as red rather than as a passing suite. |
| Design-bundle preview CSS | `tools/design_bundle.py` `TOKENS`/`CARD` (`.rc2`/`--` aliases) are derived at build time from `_HTML_WIDGET_CSS` / `_HTML_SHIM_CSS`, not hand-copied. Rerun `python3 skills/fomo-kernel/tools/design_bundle.py` after a runtime CSS change to refresh `ds-bundle/`. |

Date product assumptions when using them for prioritization. Reconfirm assumptions that are several weeks old or contradicted by new evidence.

## Public-repository quality bar

- Use only synthetic mock data.
- Write public documentation for readers who do not have the conversation context.
- Preserve deterministic, fail-closed behavior at workflow and persistence boundaries.
