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
import ast
import json
import pathlib
import re
import sys

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
#
# #375 (2026-07-23): the account-level gate sentence moved out of its inline
# ternary into the new copy/*.json `account_gate` group, because the engine now
# hands the renderer a structured {status, data} blocker instead of a hardcoded
# zh sentence — the gate's six reasons are copy keys, not branches. 74 -> 73.
#
# #368 Phase 2 batch 3 (2026-07-23): the Risks cluster's two data-driven
# helpers -- `_exit_followup_entries` (19) into `exit_followup.*` (23 leaf
# keys: the per-revisit checkpoint line and its note, the three price-basis
# suffixes, the swap / idle comparisons, and the whole exit-backlog cluster
# including its focus rows) and `_problem_lines` (4) into `problems.*` (4
# leaf keys: the trend line, a breach decision and its note, and the
# rule-kept line). 74 -> 51 (-23).
#
# `_hole_line`'s single branch is deliberately NOT in this batch. It is not a
# bilingual pair: the zh side reads `hole["number_line"]` (narrated by v1's
# `trade_recap.number_line()`) while the en side builds the sentence in the
# renderer from raw dimension fields. Collapsing it into copy means deciding
# what happens to that cross-file mirror, which docs/maintainer-guide.md's
# "Hole number-line copy" row documents as a standing obligation -- a design call, not a
# mechanical move.
#
# 50, not 51 or 73: #375 and batch 3 were open at the same time and each
# lowered BASELINE from 74 on its own — by 1 and by 23, over disjoint
# branches. Merging took the measured count on the merged tree rather than
# either side's number, which is the only resolution that banks both
# reductions instead of leaving the ratchet a notch loose.
#
# #363 (2026-07-24): while rewriting the account_hold/account cumulative-
# return sentences (batch 4's leftover list names `_performance_items` at 13
# branches), the two `if en` branches these particular sentences used moved
# into the new copy/*.json `account_perf` group (holdings_only, account_base,
# annualized_suffix, cash_drag_suffix, terminator — split at each optional
# clause so the existing irr_annual/cash_drag conditional-concatenation code
# keeps working unchanged). The rest of `_performance_items`'s branches
# (pnl/payoff/drag/cash) are untouched — out of this issue's scope. 50 -> 48.
#
# #387 (2026-07-25): the card-surface half of the copy migration. Twelve
# functions moved their sentences into copy/*.json — `_currency_note`,
# `_private_benchmark_line`, `_private_split_lines`, `_decision_entries`,
# `_headline_motive_entries`, `_exit_entries`, `_review_opening_lines`,
# `_horizon_entries`, `_public_band`, `_public_performance_lines`,
# `_tag_format_values`, and `render_public`'s mirror/structure block — plus
# the payoff/drag/cash branches #363 explicitly left behind. Verified
# behaviour-preserving the whole way: persona_sweep --baseline reported
# 156/156 byte-identical after every batch. 48 -> 16 (17 after the migration
# itself; the last one was a dead `en` local the migration stranded in
# `_headline_motive_entries`, found reviewing the PR).
#
# What the remaining 16 are: `numeric_claim`'s script-specific validation
# tables and the joiner/punctuation picks that are grammar rather than prose
# (`_forms_word`, list joiners). Those are #281's territory (split check logic
# from language data, parked), not prose migration — so 17 is not obviously
# reducible by moving more sentences, and the next PR to touch this number
# should say which category it is draining.
BASELINE = 16


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


# Every way ``card_renderer.py`` subscripts the ``sections`` group with a
# literal key. The group has exactly one dynamic reader — ``_copy_string``'s
# ``(copy.get("sections") or {}).get(key)`` fallback — and all three of its call
# sites pass ``snapshot_strength`` / ``snapshot_hole`` / ``snapshot_numbers``,
# which are top-level copy keys, not section keys. So the literal subscripts
# below are the complete reader set, and a section key absent from them is
# genuinely unrendered.
SECTION_READ_PATTERNS = (
    re.compile(r'sections_copy\[["\'](\w+)["\']\]'),
    re.compile(r'\[["\']sections["\']\]\[["\'](\w+)["\']\]'),
)
COPY_DIR = ROOT / "skills" / "fomo-kernel" / "copy"


def test_every_section_heading_has_a_renderer_that_reads_it():
    """A copy key nothing renders is not neutral — it is a migration decoy.

    #368's whole method is to move hardcoded prose into ``copy/*.json`` and
    watch the ratchet fall. A heading with no reader accepts that prose and
    reports the same progress while rendering nothing, so the ratchet drops
    without the card changing. Ten of the fifteen ``sections`` keys were in
    exactly that state when this test was written (leftovers from the
    pre-#286 standalone-section card, which four blocks replaced); they were
    deleted rather than kept as landing sites.

    Two of the five survivors — ``performance`` and ``etf`` — are read only by
    ``render_public``. They are covered by
    ``test_review_v2.test_public_card_renders_the_two_section_headings_only_it_owns``,
    since the persona sweep cannot light them (it runs offline, and no mock
    persona holds an ETF)."""
    source = CARD_RENDERER.read_text(encoding="utf-8")
    read = {key for pattern in SECTION_READ_PATTERNS for key in pattern.findall(source)}
    # Every copy locale is held to the same key contract — enumerate the
    # directory (the #389 supported-locale source of truth), not a fixed pair,
    # so a new locale file cannot ship with dead or missing section keys.
    for locale in sorted(path.stem for path in COPY_DIR.glob("*.json")):
        defined = set(json.loads(
            (COPY_DIR / f"{locale}.json").read_text(encoding="utf-8"))["sections"])
        unread = sorted(defined - read)
        assert not unread, (
            f"copy/{locale}.json defines sections {unread} that card_renderer.py "
            "never subscripts. Delete them, or render them — do not leave them "
            "as somewhere for a #368 Phase 2 batch to move sentences into, "
            "because the ratchet would fall while the card stayed the same. "
            "(If you added a genuinely dynamic reader for the group, extend "
            "SECTION_READ_PATTERNS and say why in the comment above it.)")
        missing = sorted(read - defined)
        assert not missing, (
            f"card_renderer.py reads sections {missing}, which copy/{locale}.json "
            "does not define — that is a KeyError on the rendering path.")


# --- Plan/presentation split -------------------------------------------
#
# The ratchet above stops new inline language branches from appearing. It
# cannot stop the deeper version of the same problem: a function that decides
# *what the card says* while also resolving *how it reads*, so the two can
# never be changed independently. `kpi_tile_plan` was the first function split
# along that seam (the judgment half emits raw values plus copy KEYS; the
# presentation half resolves them against one locale and one display
# context). These two tests keep that seam from silently closing again, and
# are where the next split function gets registered.
PLAN_LAYER_FUNCTIONS = ("kpi_tile_plan",)

# Names that only a presentation function has any business touching.
COPY_BOUND_NAMES = {"copy", "language", "load_copy", "kpi_copy", "sections_copy",
                    "blocks_copy", "localized_dimension", "localized_rule"}


def test_plan_layer_functions_never_reach_for_copy_or_language():
    """A plan function that can see copy will eventually read it.

    Checked structurally rather than by naming convention: every identifier
    the function body mentions, plus its own parameters, must be free of the
    copy/locale vocabulary. A plan function is then locale-neutral by
    construction, which is what lets a new locale ship as a copy file with no
    ``.py`` change at all (#368's finish line, measured as P(engine | copy) by
    ``tools/change_surface.py``)."""
    tree = ast.parse(CARD_RENDERER.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in PLAN_LAYER_FUNCTIONS):
            continue
        found.add(node.name)
        params = {a.arg for a in node.args.args + node.args.kwonlyargs}
        leaked = sorted((params | {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                         | {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)})
                        & COPY_BOUND_NAMES)
        assert not leaked, (
            f"{node.name}() is registered as a plan-layer (locale-neutral) function "
            f"but reaches for {leaked}. Decide what the card says here using engine "
            "values and copy KEYS only; resolve the wording in the presentation half. "
            "If this function is no longer plan-layer, remove it from "
            "PLAN_LAYER_FUNCTIONS and say why.")
        # Natural-language text in a plan function is the same defect wearing
        # a different hat: a sentence here is a sentence no locale can replace.
        for text in (n.value for n in ast.walk(node) if isinstance(n, ast.Constant)):
            assert not (isinstance(text, str) and re.search(r"[一-鿿]", text)), (
                f"{node.name}() contains CJK literal {text!r}. Plan-layer functions "
                "emit copy keys, never prose.")
    assert found == set(PLAN_LAYER_FUNCTIONS), (
        f"PLAN_LAYER_FUNCTIONS names {sorted(set(PLAN_LAYER_FUNCTIONS) - found)}, which "
        "card_renderer.py does not define — a renamed or deleted function silently "
        "stops being checked.")


# A card rich enough to earn all four KPI tiles, so the assertion below sees
# every slot shape the plan can emit rather than whichever subset a sparse
# fixture happens to trigger.
FULL_KPI_CARD = {
    "overview": {"total_pnl": 38500.0, "realized": 30000.0, "unrealized": 8500.0,
                 "unrealized_coverage": {"held_n": 3, "priced_n": 3},
                 "payoff": 1.8, "avg_win": 5200.0, "avg_loss": -2900.0},
    "alpha_beta_breakdown": {"excess_vs_spy": 0.042, "beta": 1.13,
                             "alpha_ann": 0.061, "credible": True,
                             "alpha_stat": {"ci95": [0.012, 0.11]}},
}


def test_kpi_tile_plan_emits_keys_and_numbers_but_no_display_text():
    """The plan's every string must be an identifier, not something a reader sees.

    This is the runtime half of the split: even a plan function that imports
    no copy could still bake in an English label or a pre-formatted "+$38.5k".
    Requiring snake_case identifiers throughout makes that impossible to do by
    accident — and makes the plan safe to hand to a future non-card surface
    (a different host's native widget, an export) that has its own vocabulary.
    """
    sys.path.insert(0, str(ROOT / "skills" / "fomo-kernel" / "engine"))
    import card_renderer  # noqa: E402  (engine path inserted just above)

    plan = card_renderer.kpi_tile_plan(FULL_KPI_CARD)
    assert [tile["id"] for tile in plan] == ["pnl", "payoff", "excess", "alpha"], (
        f"a full card should earn all four KPI tiles, got {[t['id'] for t in plan]}")

    identifier = re.compile(r"^[a-z][a-z0-9_]*$")
    def walk(node, where):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{where}.{key}")
        elif isinstance(node, (list, tuple)):
            for i, value in enumerate(node):
                walk(value, f"{where}[{i}]")
        elif isinstance(node, str):
            assert identifier.match(node), (
                f"kpi_tile_plan{where} = {node!r} is display text, not an identifier. "
                "The plan carries copy keys and raw numbers; the presentation half "
                "turns them into something a person reads.")
    walk(plan, "")


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
