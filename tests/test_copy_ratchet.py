#!/usr/bin/env python3
"""#368 Phase 1: a one-way ratchet on hardcoded language branches in
``card_renderer.py``.

Issue #368 (owner ruling 2026-07-23, plans A+B) diagnosed the renderer's
recurring mixed-language regressions (e.g. #356) as a structural problem, not
a one-off bug: prose is picked with inline ``if en`` / ``if not en`` branches
scattered through the file instead of living in ``copy/en.json`` and
``copy/zh-TW.json`` as data. The owner rejected a standalone checker project
in favor of folding the check into a ratchet here, because a checker that
only ever adds rules is itself the kind of accreting structure #368 is
trying to stop.

**The contract:**

* ``LANGUAGE_BRANCH_PATTERN`` counts hardcoded language-branch idioms in
  ``skills/fomo-kernel/engine/card_renderer.py``. ``BASELINE`` below pins the
  count measured on ``main`` the day this ratchet was introduced.
* This test only asserts ``count <= BASELINE`` — the count may fall, never
  rise. A rise means a change added a new inline language branch instead of
  routing new prose through ``copy/*.json``, which #368 forbids outright.
* #368 Phase 2 lands as several mechanical migration PRs, each moving one
  block of sentences from inline branches into the copy files. Every PR that
  does this must LOWER ``BASELINE`` to the newly measured count *in the same
  PR* — the ratchet does not self-adjust, and leaving the old (higher)
  baseline in place after a real reduction just wastes the headroom instead
  of banking it.
* When the count reaches zero, delete the ``<=`` slack: change the assertion
  to ``assert count == 0`` (or simply keep ``BASELINE = 0``, which has the
  same effect) and this test becomes a permanent ban on reintroducing inline
  language branches in this file. Per #368's "終點驗收" (finish-line
  checklist), that is also the point where a purely-copy PR's file set
  should contain no ``.py`` changes at all.

**Key parity is covered elsewhere.** ``tests/test_card_html.py`` already has
``test_locale_copy_files_keep_key_parity``, which asserts
``skills/fomo-kernel/copy/en.json`` and ``zh-TW.json`` carry the exact same
recursive key set (#279). That test already exists and runs in the "Card
HTML and delivery contract" suite, so it is not duplicated here.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
CARD_RENDERER = ROOT / "skills" / "fomo-kernel" / "engine" / "card_renderer.py"

# What counts as a "hardcoded language-branch point": the idioms
# card_renderer.py actually uses today to pick between two hardcoded
# sentences by language.
#
#   - `if not en` / `if en`      the cached local boolean, in `if` statements
#                                 and in the ternary form
#                                 `"english text" if en else "中文文字"`
#                                 (`en = copy.get("language") == "en"` is
#                                 counted once, below, via the comparison on
#                                 its right-hand side — not double-counted
#                                 here).
#   - `language == "en"`          the bare local variable compared directly
#   - `language != "en"`          (both directions; used to gate zh-only or
#                                  en-only sentences)
#   - `copy["language"] == "en"`  the same comparison, un-cached, read
#   - `copy.get("language") == "en"`  straight off the copy/bundle dict
#
# Deliberately excluded:
#   - `str(language).lower().startswith("en")` (3 call sites): this
#     normalizes an arbitrary caller-supplied language string down to the
#     canonical "en"/"zh-TW" pair — it picks a *code*, not a hardcoded
#     sentence, and Phase-2 copy migrations do not touch it.
#   - `DISPLAY_CURRENCY_BY_LANGUAGE` and bare `"zh-TW"` fallbacks (e.g.
#     `language or "zh-TW"`): these pick a currency code or a default
#     language code, not narrative copy.
LANGUAGE_BRANCH_PATTERN = re.compile(
    r'if\s+not\s+en\b'
    r'|if\s+en\b'
    r'|\blanguage\s*==\s*"en"'
    r'|\blanguage\s*!=\s*"en"'
    r'|copy\["language"\]\s*==\s*"en"'
    r'|copy\.get\("language"\)\s*==\s*"en"'
)

# Measured by running LANGUAGE_BRANCH_PATTERN.findall() over
# skills/fomo-kernel/engine/card_renderer.py on main @ 29a328d (2026-07-23,
# the commit this ratchet was introduced against). See the module docstring
# for what happens to this number as #368 Phase 2 proceeds.
#
# #368 Phase 2 batch 1 (2026-07-23): migrated _next_block's 5 bilingual
# inline-ternary sentence pairs into copy/en.json + copy/zh-TW.json
# (block_missing.rule_skip / rule_snapshot / rule_structural /
# rule_insufficient_data / snapshot_unlock), and dropped the now-dead
# `en = language == "en"` local it left behind. 97 -> 91 (-6: 5 ternaries +
# 1 now-unused language-comparison assignment).
#
# #368 Phase 2 batch 2 (2026-07-23): migrated the Performance/Block-1 helper
# cluster's hardcoded bilingual sentences into copy/en.json + copy/zh-TW.json
# under 5 new top-level groups -- pnl_lines.{display,original}.*
# (_original_pnl_lines / _overview_lines), alpha_interval.*
# (_alpha_interval_line), best_strength.no_signal (_best_strength),
# reconciliation.* (_reconciliation_lines), and snapshot.{overview,strength,
# holes}.* (the snapshot trio: _snapshot_overview_lines /
# _snapshot_strength_line / _snapshot_hole_lines). Dropped the now-dead
# `en = copy.get("language") == "en"` local in _snapshot_overview_lines.
# 91 -> 74 (-17: 3 + 3 + 1 + 1 + 1 + 4 + 1 + 3 branch points across the 8
# functions, matching LANGUAGE_BRANCH_PATTERN.findall() run before/after).
BASELINE = 74


def test_language_branch_count_only_ratchets_down():
    text = CARD_RENDERER.read_text(encoding="utf-8")
    count = len(LANGUAGE_BRANCH_PATTERN.findall(text))
    assert count <= BASELINE, (
        f"card_renderer.py now has {count} hardcoded language-branch points "
        f"(LANGUAGE_BRANCH_PATTERN), up from the pinned BASELINE of "
        f"{BASELINE}. This ratchet (#368 Phase 1) only tightens: route new "
        "language-dependent prose through copy/en.json + copy/zh-TW.json "
        "instead of adding an inline `if en` / `if not en` branch here. If "
        "you genuinely lowered the count (a #368 Phase 2 migration batch), "
        "lower BASELINE in this file to the newly measured count in the "
        "same PR — see the module docstring."
    )


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS: {len(tests)} copy-ratchet test(s); "
          f"language-branch count baseline={BASELINE}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
