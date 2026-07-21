# Language policy

The repository separates implementation language from market-facing localization.

## English-only surfaces

Use English for:

- `AGENTS.md` and `CLAUDE.md`
- all Markdown under `docs/`, except no exceptions are currently needed
- `BACKLOG.md` and `evals/EVALS.md`
- `skills/fomo-kernel/SKILL.md`, flows, references, rubrics, specifications, and mock documentation
- English runtime assets such as `card-template.html`, `copy/en.json`, evaluation prompts, and lens JSON
- developer-facing test documentation

Do not mix translated explanations into these files. One implementation contract makes cross-agent behavior easier to review and keeps code identifiers, schemas, and documentation aligned.

## Bilingual GTM surfaces

GTM content may have separate, complete localized artifacts:

- `README.md`: default English landing page
- `README.zh-TW.md`: Traditional Chinese landing page
- `docs/demo-card-en.html` and `docs/demo-card.html`, plus their rendered images

Keep claims, examples, and numeric values synchronized across locale variants. Translate wording, not product behavior.

## Product localization

User-visible product copy is stored by locale, such as `skills/fomo-kernel/copy/en.json` and `skills/fomo-kernel/copy/zh-TW.json`. Locale files are not implementation instructions and must remain separated rather than mixing languages in one contract.

The engine, schemas, lifecycle, and policy remain locale-neutral. `--language` selects copy and rendering only.

Stable dimension identifiers are English snake case, for example `position_sizing`, `averaging_down`, and `entry_style`. Localized dimension labels and card wording live only in `copy/<locale>.json`; lens configurations use the stable identifiers and English implementation text.

The same rule covers every coded engine emission: behavior tags, stress-test scenarios, and prescription rows travel as stable English snake_case codes plus raw params (#279), and the renderers resolve them through `copy/<locale>.json`. Adding a locale for these surfaces requires only a new copy file, not an engine change.
