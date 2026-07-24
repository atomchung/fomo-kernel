# Output language contract

> Status: draft v1, extracted from `docs/output-contract.md` §5 per owner
> ruling (2026-07-21, [#276](https://github.com/atomchung/fomo-kernel/issues/276)):
> language is a standalone contract because the product must support **any**
> locale, not a zh/en pair. This file governs runtime user-visible output
> (cards, questions, notes). Repository/document language rules stay in
> `docs/language-policy.md`.

## 1. Resolution order — which language a review speaks

1. **Conversation language wins.** The agent must pass `--language` matching
   the language the user is conversing in (already enforced at the QA layer;
   a zh conversation that yields an en card is a defect, #262).
2. **Unsupported tags fall back to `en`.** A requested language with no
   `copy/<locale>.json` resolves to English — never to zh-TW. Matching is
   exact up to case (`zh-tw` → `zh-TW`); there is no base-tag negotiation.
   zh variants without a copy file (zh-HK, zh-Hans) follow the same strict
   fallback — ruled 2026-07-24, owner confirmed no exception mapping on
   [#389](https://github.com/atomchung/fomo-kernel/issues/389).
   `card_renderer.resolve_language()` is the single implementation;
   `review.py prepare` resolves once at the CLI boundary so fingerprints,
   plans, and renderers only see canonical locales.
3. **Stored user preference** is the fallback when the conversation language
   is ambiguous or the run is headless (cron, scripted). *Storage mechanism
   (coach state) is phase 2 — not yet implemented.*
4. **Default `en`** when no signal exists (no `--language`, no stored
   preference).

> Ruled 2026-07-21: owner confirmed conversation-first; the stored preference
> serves non-interactive runs only.
> Ruled 2026-07-24 ([#389](https://github.com/atomchung/fomo-kernel/issues/389)):
> unknown or unsupported languages fall back to `en`, and the no-signal
> default flips from `zh-TW` to `en`. This supersedes the 2026-07-21 default.
> Card surfaces falling back to `en` does not change the conversation-layer
> rule: the agent keeps conversing in the user's language and must not
> hand-translate card text (SKILL.md "Language and sharing").

## 2. Architecture rule (existing policy, restated with teeth)

`docs/language-policy.md` already rules: *engine, schemas, lifecycle stay
locale-neutral; `--language` selects copy and rendering only; stable
identifiers are English snake_case; localized wording lives only in
`skills/fomo-kernel/copy/<locale>.json`.*

Consequences this contract makes explicit:

- **Internal canonical language is English** (owner ruling 2026-07-21).
  Everything produced internally — codes, identifiers, intermediate
  artifacts, logs — is English; the user's language exists only at the
  presentation layer (cards) and the interaction layer (questions shown,
  answers collected). Every locale, zh-TW included, renders from the same
  English-baseline internals.
- **No user-visible string may be baked into engine data values.** Engine
  output carries stable codes; the renderer's copy layer localizes them.
- **A locale gap is a defect, not a reduction.** The four-block skeleton
  (`docs/output-contract.md` §2) renders equivalently in every supported
  locale; depth may shrink for *data* reasons only, never for language.
- Adding a locale must require **only** a new `copy/<locale>.json` (plus
  format rules, §5) — zero engine edits. That is the definition of done for
  the neutrality refactor. Since #389 the CLI no longer hardcodes the locale
  list (`resolve_language()` discovers supported locales from the copy
  directory), so this promise holds mechanically: dropping the file in makes
  the tag resolvable.

## 3. Known violations to repair (phase 1 target)

Engine currently bakes zh vocabulary into data values, so en cards silently
lose content (all line refs at main `08a5606`):

| Violation | Site | en-card effect |
|---|---|---|
| Stress scenario label is engine zh text; template sentence zh-only | `card_renderer.py:1338-1352` (`_stress_lines`, `if language == "en": return []`) | stress line absent |
| Prescription texts (preserve/test/cut) are engine zh strings | `card_renderer.py:1393-1403` (`_improve_rows`) | improve rows absent |
| Behavior tags (zh labels for "likely DCA", "too heavy", …) are engine zh vocabulary | `_instrument_rows` (tags dropped when `language == "en"`) | rows lose verdict tags |
| The zh literal printing "annualized IRR" violates the no-jargon rule | `card_renderer.py:773` | jargon token on zh card |
| The spelled-out zh numeral for "95%" (fullwidth percent) mixes digit styles (#272) | `card_renderer.py:583` | mixed-style sentence |

Repair direction: engine emits stable codes (`averaging_in`, `oversized`,
scenario/prescription kinds); `copy/<locale>.json` gains the label strings;
renderer resolves codes → copy. Persisted artifacts that already store zh
literals need no compatibility mapping — dev-phase ruling (owner
2026-07-21): stale data is cleaned up on demand, not migrated.

**zh-CN transitional waiver (owner-ruled 2026-07-24,
[#387](https://github.com/atomchung/fomo-kernel/issues/387)):** `zh-CN`
shipped as a mechanically-converted copy file before the engine's hardcoded
Traditional stem/option literals were migrated, so zh-CN question surfaces
mix Simplified (copy) with Traditional (engine ternaries) today. This is a
deliberate interim exception to the #356 "mixed language is a defect" rule,
scoped to Traditional/Simplified mixing on zh-CN only. The leak inventory
and migration plan live on #387;
`tests/test_review_v2.py::test_prepare_zh_cn_renders_simplified_copy_with_documented_mixed_script`
pins the current state and flips into the purity gate when the migration
lands.

Repaired, kept here as the worked example (#356): Block 4's standing-rule
placeholder interpolated `engine_state.rule` — a v1-only zh sentence
`trade_recap.prescribe` hardcodes — into the localized wrapper, so English
cards printed Chinese rather than losing content. The engine now also emits
`engine_state.rule_dim` and the renderer resolves the rule text from copy
`rules`. Note the failure shape: every violation above drops content on the
en card, which reads as a thin card; this one *added* the wrong language, and
no per-sentence test saw it. `tests/persona_sweep.py` therefore gates the
whole rendered card — an English card carries no CJK on any of the three
surfaces, on a first *or* a second review (the reconciliation opener and the
revisit checkpoints exist only on the latter, and interpolate stored text from
the prior review, which is where a zh literal has the most room to hide).

## 4. Out of scope for this contract

- **v1 zh human card** (`trade_recap.py`) is zh-only legacy; the CLAUDE.md
  number-line mirror obligation is unchanged. Do not i18n v1.
- **Conversation-layer prose** (agent-authored option copy, chat wording)
  follows SKILL.md instructions and the LLM judge — mechanical checks here
  cover card surfaces only. #262-style mixing in *options* is a SKILL/judge
  matter, not fixed by engine neutrality.

## 5. New-locale checklist

1. `copy/<locale>.json` — full key parity with `en.json` (25 top-level keys).
2. Number/currency format rules for the locale (digit grouping, percent
   style; digit-ban quantifier rules are zh-specific — decide the locale's
   equivalent or mark n/a).
3. Checker review: language-targeted assertions (e.g. A-13 CJK punctuation)
   are per-locale; add or waive explicitly. Splitting check logic from
   language data is tracked in
   [#281](https://github.com/atomchung/fomo-kernel/issues/281) (parked).
4. HTML font stack covers the locale's script.
5. One mock-persona QA run in that locale; card must pass the S-series
   structure checks (`docs/output-contract.md` §8).

## 6. Enforcement

- Copy-key parity across locale files — mechanical test.
- S-4 (jargon/digit-style) runs per-locale.
- Structure-equivalence: for one fixture persona, zh and en cards must light
  the same modules given the same state (asserts §2 "gap = defect").
- `tests/test_doc_language.py` continues to gate this file's own language.
