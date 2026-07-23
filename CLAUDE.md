# CLAUDE.md — Maintainer guide

> This file is for contributors changing the repository. Runtime behavior is defined by [skills/fomo-kernel/SKILL.md](skills/fomo-kernel/SKILL.md); [AGENTS.md](AGENTS.md) is only a thin cross-agent router. Do not duplicate the complete runtime contract here.

## Repository role

`fomo-kernel` is a public repository that external users can clone and install. The deterministic Python engine lives in `skills/fomo-kernel/engine/`. `SKILL.md` defines runtime orchestration, and `AGENTS.md` routes agents that do not automatically discover skills.

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

The default suite is offline, deterministic, and does not require pytest. It covers engine units, JSON/state contracts, price paths, the snapshot-anchored ledger, revisit/swap behavior, market context, problem tracking, persona fixtures, the state loop, artifact checkers, local data controls, session idempotency, the v2 review lifecycle, documentation language, and agent workflow boundaries.

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
- Before merging several PRs, review semantic overlap as well as textual conflicts. If the engine changed, run the persona sweep — `python3 tests/persona_sweep.py` (every mock persona × both locales as offline pre-commitment first-review renders; gates S-1..S-4 plus the HTML layout invariants; weekly-only surfaces stay covered by the fixture suites; add `--baseline <other-checkout>/skills/fomo-kernel/engine` to prove Markdown parity against pre-change main) — and require a clean exit.
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
| HTML card design system | `card-template.html` (design-provenance reference) ↔ the `_HTML_SHIM_CSS` / `_HTML_WIDGET_CSS` literals in `card_renderer.render_html()`. Palette, dark-mode rules, and layout constraints must match; `tests/test_card_html.py` gates the renderer output while `card-template.html` documents the intent. Before adding or reshaping a visual element, read [docs/design-guidelines.md](docs/design-guidelines.md): it defines the module slot contracts that keep one element's change from forcing its neighbours to be re-tuned. |
| Design-bundle preview CSS | `tools/design_bundle.py` `TOKENS`/`CARD` literals (`.rc2`/`--` aliases) ↔ `_HTML_WIDGET_CSS` / `card-template.html`. Hand-mirrored with no test gate: after changing the runtime card CSS, re-check the CARD block and rebuild the bundle (`python3 skills/fomo-kernel/tools/design_bundle.py`). |

Date product assumptions when using them for prioritization. Reconfirm assumptions that are several weeks old or contradicted by new evidence.

## Public-repository quality bar

- Use only synthetic mock data.
- Write public documentation for readers who do not have the conversation context.
- Preserve deterministic, fail-closed behavior at workflow and persistence boundaries.
