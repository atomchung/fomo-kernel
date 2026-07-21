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

The engine, schemas, lifecycle, and policy remain locale-neutral. `--language` selects copy and rendering only. The runtime output-language contract — resolution order, neutrality obligations, known violations, and the new-locale checklist — lives in [docs/output-language.md](output-language.md).

Stable dimension identifiers are English snake case, for example `position_sizing`, `averaging_down`, and `entry_style`. Localized dimension labels and card wording live only in `copy/<locale>.json`; lens configurations use the stable identifiers and English implementation text.
