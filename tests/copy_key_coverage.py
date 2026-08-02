#!/usr/bin/env python3
"""Copy key coverage: which user-visible card copy keys no fixture can see (#787).

    python3 tests/copy_key_coverage.py                 # gate: fail naming zero-coverage keys
    python3 tests/copy_key_coverage.py --report         # full per-locale coverage report

Three defects shipped in two days under a fully green suite (#787), and none
of them were a missing test in the sense the suite already checks for --
each was a copy key that had *never been rendered by anything the suite
looks at*, so a fixture change, an engine change, or a plain typo in
``copy/*.json`` had no oracle to turn red:

1. **#733** -- ``review_milestone.line`` hardcodes the English plural. The
   line reached no scene in ``tests/copy_corpus.py`` and no literal in any
   test, so ``grep -c "completed review" tests/golden/copy-corpus.txt`` was 0
   on the commit that shipped it.
2. **#766** -- ``tests/test_card_html.py`` runs a hand-maintained test list;
   4 of its 67 tests never execute. Green light protecting code that never ran.
3. **``instrument_tags.roughly_neutral``** (#779, PR #786) -- a 156-persona
   byte-parity sweep was cited as evidence the key's removal was safe. The
   string never reached a card in any fixture, so the sweep could not have
   told either way.

**What this checks, and why two sources.** ``tests/golden/copy-corpus.txt``
(via ``tests/copy_corpus.py``'s ``SCENES``) is the repository's one *pinned,
diffed, human-reviewed* copy fixture, but its own coverage claim is
opt-in per top-level register (``coverage()``'s ``claimed`` set) -- of the
85 top-level ``copy/en.json`` registers, only 9 are claimed; the other 76 sit
in a "not claimed by any scene" list its own docstring calls "a roadmap, not
a defect list." Reading only that file as evidence would therefore flag
most of an ordinary card's own copy (``hole_lines``, ``sections``, ``title``,
...) as "uncovered," which both drowns the real gaps in noise and is false:
those keys are lit on every persona's card, just not by a ``copy_corpus.py``
scene. So the second source is a **fresh, in-process render of every mock
persona** (``tests/persona_sweep.py``'s own bundle-building machinery,
``build_fixtures`` + ``card_renderer.render_*``) -- the same production
renderer, the same mock CSVs the repository already trusts elsewhere, run
here and discarded (not a second golden; nothing is persisted). A key counts
as covered when either source's rendered text matches its catalog value
(placeholders tolerant, short/trivial values excluded -- the same
``value_pattern``/``MIN_EVIDENCE`` copy_corpus.py already uses, imported
rather than re-derived).

**Coverage is not correctness.** A key can show "covered" while the one
rendered instance that covers it is itself wrong -- #733's own line is the
worked example: a fresh persona render of it reads literally "you already
had 1 completed reviews," which *does* satisfy this checker's placeholder-
tolerant pattern match (the covering evidence contains the very bug), while
still being invisible to ``tests/copy_corpus.py``'s golden because nothing
in ``SCENES`` renders that register. This checker answers "does any
evidence exist," never "is the wording right" -- that second question needs
a human reading a diff, which is exactly what moving a key into a
``copy_corpus.py`` scene (pinned, regenerated, diffed on every change)
buys and this checker cannot.

**Scope: card-reachable registers only.** ``copy/*.json`` also carries the
interactive question layer (``*_choices``, ``*_descriptions``,
``condition_crossing``, ``condition_basis``, ...) that ``review.py`` renders
into the Review Plan's ``question_queue``, never onto a card --
``evals/run_episodes.py``'s ``QUESTION_CONSUMERS`` is that surface's own
oracle. Enumerating those here would both be checking the wrong contract and
buries the ones that matter. The reachable set is derived by parsing
``card_renderer.py``'s own AST for every literal register name it actually
subscripts or ``.get()``s (directly, or through the one generic
``_copy_string(copy, key, fallback)`` helper) -- mechanical and
self-updating, the same discipline ``tests/test_market_data.py``'s
provider-import AST guard and ``tests/test_copy_ratchet.py``'s
``PLAN_LAYER_FUNCTIONS`` check already use here, rather than a hand list
that drifts the way #766 shows ``test_card_html.py``'s own list already has.

**The allowlist is not a dumping ground.** Every allowlisted key carries a
non-empty, specific reason -- ``main()``'s ``empty_reason`` check gates this
mechanically: strip (or blank out) any entry's reason string and that key
reverts to failing the same way an unreached key would, rather than being
silently accepted because its name still appears in the dict. Two kinds of
reason appear:

- **Genuinely unreachable.** ``instrument_tags.roughly_neutral`` is the one
  instance today: PR #786 means no current engine path ever assigns that
  tag on a fresh diagnosis, so no fixture -- present or future -- can cover
  it by rendering a new card. It stays in ``copy/*.json`` only to resolve a
  tag code a session persisted before #786 may still carry on disk.
- **Pre-existing gap, named rather than fixed here.** Building this checker
  is what makes the other ~300 zero-coverage keys visible for the first
  time (run ``--report`` for the full per-locale list); closing each one
  needs either a new ``copy_corpus.py`` scene or a mock persona whose data
  happens to cross that branch, which is real product/fixture work outside
  a test-infrastructure issue's scope. Recording them by name, grouped by
  the shared reason their branch is unexercised, is the #787 acceptance bar
  itself ("a decision on the record rather than a silence") -- it is a
  one-way ratchet in the same spirit as ``tests/test_copy_ratchet.py``'s
  ``BASELINE``, except every entry is a named key with a reason rather than
  a bare count, so it cannot silently absorb an unrelated new gap: a key
  that was never uncovered before and has no fixture today is not in this
  dict, and therefore still fails.

Deterministic given the mock CSVs and engine on disk; not offline in the
strict sense ``tests/copy_corpus.py`` is (persona_sweep.build_fixtures runs
review.py prepare through real subprocesses against an isolated throwaway
TRADE_COACH_HOME with yfinance stubbed out -- the same posture
tests/persona_sweep.py itself runs under as its own suite entry). No network,
no real trade data, no mutation of anything under version control.
"""
import argparse
import ast
import json
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
SKILL = ROOT / "skills" / "fomo-kernel"

sys.path.insert(0, str(TESTS_DIR))
import offline_posture  # noqa: E402
offline_posture.apply()

sys.path.insert(0, str(SKILL / "engine"))
import card_renderer  # noqa: E402
import copy_corpus  # noqa: E402
import persona_sweep  # noqa: E402

LOCALES = copy_corpus.LOCALES  # ("zh-TW", "zh-CN", "en")
CARD_RENDERER_SRC = SKILL / "engine" / "card_renderer.py"


# --------------------------------------------------------------------------
# Which registers a card can possibly show (#787's "render-reachable")
# --------------------------------------------------------------------------
def reachable_registers(all_registers):
    """Top-level ``copy/en.json`` keys ``card_renderer.py`` actually reads.

    A register counts as reachable when its literal name appears as a
    ``Subscript`` key, or as an argument to a call named ``get`` or whose
    name contains "copy" (covers both ``x.get("key")`` and the one generic
    helper ``_copy_string(copy, "key", fallback)``). Verified by hand against
    every one of ``card_renderer.py``'s ``load_copy(language)`` call sites
    when this file was written: 63 registers reached directly, 3 more
    (``snapshot_hole``, ``snapshot_numbers``, ``snapshot_strength``) only
    through ``_copy_string`` -- both are covered by the rule above. The two
    near-miss false positives a plain "any string literal anywhere" scan
    would add (``condition_crossing``, ``exit_consistency`` -- both appear
    only inside ``question.get("kind") == "..."`` comparisons, a different
    concept spelled the same) are excluded by construction, since a
    ``Compare`` node is neither a ``Subscript`` nor a ``Call`` argument.
    """
    tree = ast.parse(CARD_RENDERER_SRC.read_text(encoding="utf-8"), filename=str(CARD_RENDERER_SRC))
    reachable = set()

    class Visitor(ast.NodeVisitor):
        def visit_Subscript(self, node):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value in all_registers:
                reachable.add(key.value)
            self.generic_visit(node)

        def visit_Call(self, node):
            func_name = (node.func.attr if isinstance(node.func, ast.Attribute)
                         else node.func.id if isinstance(node.func, ast.Name) else "")
            if func_name == "get" or "copy" in func_name.lower():
                for arg in node.args:
                    if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                            and arg.value in all_registers):
                        reachable.add(arg.value)
            self.generic_visit(node)

    Visitor().visit(tree)
    return reachable


# --------------------------------------------------------------------------
# Evidence source 1: the committed golden, split back out per locale
# --------------------------------------------------------------------------
_SECTION = re.compile(r"\n=+\n(?P<header>[^\n]+)\n=+\n")
_HEADER_LANG = re.compile(r"\[(zh-TW|zh-CN|en)\]")


def golden_text_by_locale():
    """Per-locale text of ``tests/golden/copy-corpus.txt``.

    Parsed from the golden's own ``name [language] surface`` section markers
    (``tests/copy_corpus.py``'s ``build_corpus``) rather than re-rendering
    ``SCENES`` here a second time: reading the committed file is what makes
    this evidence source literally *a golden fixture* on disk, the same
    artifact ``tests/copy_corpus.py`` diffs and a reviewer reads. If the file
    is missing or stale, ``tests/copy_corpus.py`` (which runs earlier in
    ``tests/run_all.py``) already fails the suite for that on its own.
    """
    text = copy_corpus.GOLDEN.read_text(encoding="utf-8")
    matches = list(_SECTION.finditer(text))
    per_locale = {locale: [] for locale in LOCALES}
    for index, match in enumerate(matches):
        lang_match = _HEADER_LANG.search(match.group("header"))
        if not lang_match:
            continue  # the trailing "copy-key coverage" section
        language = lang_match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        per_locale[language].append(text[start:end])
    return {language: "\n".join(parts) for language, parts in per_locale.items()}


# --------------------------------------------------------------------------
# Evidence source 2: every mock persona, rendered fresh
# --------------------------------------------------------------------------
def persona_text_by_locale():
    """Fresh persona cards, built the way ``tests/persona_sweep.py`` itself
    does (``build_fixtures`` + the three ``card_renderer.render_*``
    surfaces), concatenated per locale. Nothing here is written outside a
    throwaway ``tempfile.TemporaryDirectory`` -- this is a second evidence
    *read*, not a second corpus. ``persona_sweep.LOCALES`` is ``("zh-TW",
    "en")`` only, so ``zh-CN`` contributes nothing here; that locale's
    evidence is golden-only, which is the documented zh-CN transitional lag
    (docs/output-language.md), not a bug in this file.
    """
    per_locale = {locale: [] for locale in LOCALES}
    with tempfile.TemporaryDirectory(prefix="copy-key-coverage-") as tmp:
        bundles_dir, failures, _notes = persona_sweep.build_fixtures(pathlib.Path(tmp))
        if failures:
            raise RuntimeError(
                "persona fixture build failed -- fix tests/persona_sweep.py (or its mock "
                "CSVs) before trusting this coverage report:\n" + "\n".join(failures))
        for bundle_file in sorted(bundles_dir.glob("*.bundle.json")):
            bundle = json.loads(bundle_file.read_text(encoding="utf-8"))
            language = bundle.get("language")
            if language not in per_locale:
                continue
            private = copy_corpus._FRONTMATTER.sub("", card_renderer.render_private(bundle)).strip()
            html_text = copy_corpus.html_text(card_renderer.render_html(bundle))
            public = card_renderer.render_public(bundle).strip()
            per_locale[language].extend((private, html_text, public))
    return {language: "\n".join(parts) for language, parts in per_locale.items()}


# --------------------------------------------------------------------------
# Allowlist -- every entry a specific, non-empty, on-the-record reason
# --------------------------------------------------------------------------
def _expand(reason, keys):
    return {key: reason for key in keys}


_REASON_ROUGHLY_NEUTRAL = (
    "PR #786 (closes #779): a missing rule match now leaves an instrument's "
    "tags list empty instead of defaulting to a fabricated 'roughly neutral' "
    "verdict, so no engine path assigns this tag on a fresh diagnosis -- no "
    "fixture, present or future, can cover it by rendering a new card. The "
    "copy key survives only to resolve a tag code a session persisted before "
    "#786 may still carry on disk (tests/test_engine_units.py pins the engine "
    "half: test_ticker_diagnosis_never_fabricates_a_neutral_tag_when_nothing_matched)."
)

_REASON_SNAPSHOT_ROUTE = (
    "#787 pre-existing gap: the snapshot_review route. tests/persona_sweep.py's "
    "own module docstring already documents this as unexercised ('the "
    "snapshot_review and test_drive routes stay uncovered here -- no mock "
    "persona exercises either'), and no tests/copy_corpus.py scene builds a "
    "snapshot bundle either. Closing this needs a snapshot-route scene or "
    "fixture, which is product/fixture work, not this checker's job."
)

_REASON_HONESTY_LEDGER = (
    "#787 pre-existing gap: each honesty_ledger key is a disclosure for one "
    "specific rare data-quality condition (a stale FX cache, an unclassified "
    "ETF, an orphan sell, a currency mix, ...) that no current "
    "tests/copy_corpus.py scene or mock persona CSV happens to construct. "
    "tests/copy_corpus.py's own _sizing_coverage_bounded/_unpriced_exits_excluded "
    "scenes are the precedent for closing one of these; the rest are unclaimed."
)

_REASON_SECTORS = (
    "#787 pre-existing gap: sector/classification labels resolved per ticker "
    "through an instrument map no current mock persona CSV or copy_corpus.py "
    "scene declares a holding in. Reachable in principle (add a scene, or a "
    "persona holding, in that sector); not fixed in this PR."
)

_REASON_PRESCRIPTION_UNCLAIMED = (
    "#787 pre-existing gap: this prescription_texts/prescription_kinds branch "
    "needs a specific dim_size + rule_grounding combination no current "
    "tests/copy_corpus.py scene or persona CSV drives the engine into. "
    "#757 closed the two branches (selection_inconclusive and its t_wide "
    "sub-line) an actual simulated review reached; the rest remain unclaimed."
)

_REASON_HOLE_LINES_UNCLAIMED = (
    "#787 pre-existing gap: tests/copy_corpus.py's own comment on "
    "hole_lines/position_sizing already documents the shape -- 'hole_lines "
    "carries nine other templates this one scene does not exercise, and "
    "claiming it would report every one of them unreached.' #757 closed "
    "exit_forward (an actual simulated review reached it); the remaining "
    "templates each need their own scene."
)

_REASON_EXIT_CHOICE_LABELS = (
    "#787 pre-existing gap: tests/persona_sweep.py's PREFERRED_CHOICES picks "
    "one deterministic exit-reason answer per persona on purpose (module "
    "docstring), so the other exit_choices labels never render on any "
    "persona card, and no copy_corpus.py scene claims this register either."
)

_REASON_RECAP_LABEL_UNCLAIMED = (
    "#787 pre-existing gap: this is a short recap label card_renderer.py "
    "reads back to restate a past answer (add_choices/due_choices/"
    "headline_motive_choices/block_missing/etc.) -- it only renders on the "
    "specific answered-question branch that produced this exact value, which "
    "no current copy_corpus.py scene or persona CSV/answer path reaches."
)

_REASON_MISC_UNCLAIMED = (
    "#787 pre-existing gap: no current tests/copy_corpus.py scene or mock "
    "persona CSV happens to drive card_renderer.py into this specific "
    "branch. Named here rather than silently absorbed, per #787's own "
    "acceptance bar; closing it is follow-up fixture/scene work."
)

_REASON_ZH_CN_NO_PERSONA = (
    "#787 pre-existing gap, zh-CN only: tests/persona_sweep.py's own LOCALES "
    "tuple is ('zh-TW', 'en') -- zh-CN is not rendered by any mock persona, "
    "so this locale's only possible evidence source is "
    "tests/golden/copy-corpus.txt, and no tests/copy_corpus.py scene claims "
    "this register. This key is reached for zh-TW and/or en through persona "
    "evidence; the gap is specifically the documented zh-CN transitional lag "
    "(docs/output-language.md 'zh-CN transitional waiver'), not a broken key."
)

ALLOWLIST = {}
ALLOWLIST.update(_expand(_REASON_ROUGHLY_NEUTRAL, [
    "instrument_tags.roughly_neutral",
]))
ALLOWLIST.update(_expand(_REASON_SNAPSHOT_ROUTE, [
    "snapshot.holes.clean_structure", "snapshot.holes.leading_risk", "snapshot.holes.no_weights",
    "snapshot.overview.missing_avg_cost", "snapshot.overview.missing_fx", "snapshot.overview.opening",
    "snapshot.overview.opening_as_of", "snapshot.overview.subject_generic",
    "snapshot.overview.subject_with_count", "snapshot.overview.valuation_cost",
    "snapshot.overview.valuation_market_value", "snapshot.overview.valuation_unavailable",
    "snapshot.strength.baseline", "snapshot.strength.weighted",
    "snapshot_hole", "snapshot_numbers", "snapshot_strength",
]))
ALLOWLIST.update(_expand(_REASON_HONESTY_LEDGER, [
    "honesty.accounting_reconciliation", "honesty.acct_perf_basis", "honesty.alpha_credibility",
    "honesty.cash_reliability", "honesty.currency_mix", "honesty.etf_metadata",
    "honesty.orphan_sells", "honesty.price_plausibility", "honesty.price_source",
    "honesty.sector_attribution",
    "honesty.snapshot_reconciliation", "honesty.snapshot_scope",
    "honesty.unclassified_drivers", "honesty.unrealized_coverage",
]))
ALLOWLIST.update(_expand(_REASON_SECTORS, [
    "sectors.broad_market_etf", "sectors.commodities", "sectors.consumer",
    "sectors.datacenter_power", "sectors.drones_defense", "sectors.ev_ai",
    "sectors.rare_earth_materials", "sectors.regional_etf", "sectors.semiconductors",
    "sectors.unclassified",
]))
ALLOWLIST.update(_expand(_REASON_PRESCRIPTION_UNCLAIMED, [
    "prescription_kinds.amplify", "prescription_kinds.amplify_hypothesis",
    "prescription_kinds.cut_loss", "prescription_kinds.outsource",
    "prescription_kinds.selection_inconclusive",
    "prescription_texts.amplify_hypothesis", "prescription_texts.amplify_selection_edge",
    "prescription_texts.cut_averaging_down", "prescription_texts.cut_oversize",
    "prescription_texts.outsource_selection",
]))
ALLOWLIST.update(_expand(_REASON_HOLE_LINES_UNCLAIMED, [
    "hole_lines.diversification_same_driver",
    "hole_lines.holding_consistent", "hole_lines.holding_no_data",
]))
ALLOWLIST.update(_expand(_REASON_EXIT_CHOICE_LABELS, [
    "exit_choices.full.anxiety", "exit_choices.full.other", "exit_choices.full.price_target",
    "exit_choices.full.skip", "exit_choices.full.swap",
    "exit_choices.reduce.anxiety", "exit_choices.reduce.other", "exit_choices.reduce.price_target",
    "exit_choices.reduce.skip", "exit_choices.reduce.swap", "exit_choices.reduce.thesis_broken",
]))
ALLOWLIST.update(_expand(_REASON_RECAP_LABEL_UNCLAIMED, [
    "add_choices.new_evidence", "add_choices.planned_tranche", "add_choices.price_only",
    "add_choices.skip", "add_choices.valuation_change",
    "due_descriptions.falsified",
    "headline_motive_choices.emotional_reaction", "headline_motive_choices.external_constraint",
    "block_missing.rule_diverged", "block_missing.rule_insufficient_data",
    "block_missing.rule_snapshot", "block_missing.snapshot_unlock", "block_missing.trades_traded",
    "decision_entries.line",
    "rule_breach_decisions.keep_tracking", "rule_breach_decisions.revise_rule",
]))
ALLOWLIST.update(_expand(_REASON_MISC_UNCLAIMED, [
    "account_perf.account_base", "account_perf.annualized_suffix", "account_perf.cash_drag_suffix",
    "asked_because.exit_notional", "asked_because.pnl_impact", "asked_because.position_cost",
    "benchmark_line.comparator_default",
    "cash_lines.anchored", "cash_lines.anchored_with_weight", "cash_lines.by_currency",
    "currency_note.cached", "currency_note.cached_dated", "currency_note.no_rate",
    "currency_note.portfolio_fx_gap",
    "demo_badge",
    "dimensions.alpha_beta", "dimensions.entry_style",
    "etf_classification.allocation", "etf_classification.concentrated",
    "horizon_entries.exit_too_fast", "horizon_entries.held_too_long",
    "horizon_entries.ticker_default", "horizon_entries.voice_inferred", "horizons.quarters",
    "instrument_tags.adds_pending_confirmation", "instrument_tags.deep_underwater",
    "instrument_tags.suspected_averaging_down_losing", "instrument_tags.suspected_averaging_down_recovered",
    "instrument_tags.suspected_dca",
    "kpi.curve", "kpi.spark_caption",
    "opening_value.label", "opening_value.questions_many", "opening_value.questions_one",
    "patterns_panel.label", "patterns_panel.sold_winner_early",
    "payoff_lines.original_currency",
    "pnl_lines.display.total", "pnl_lines.display.unrealized_only",
    "pnl_lines.original.both",
    "public_band.moderate",
    "public_mirror.behavioral_dim_default", "public_mirror.snapshot_baseline",
    "public_mirror.structural", "public_mirror.structural_dim_default",
    "public_mirror.structural_with_severity", "public_mirror.structure",
    "public_patterns.alpha_beta",
    "public_patterns.entry_style",
    "rule_grounding.averaging_down",
    "rule_targets.more_suffix",
    "rule_tradeoff.sizing_vs_hypothesis", "rule_tradeoff.sizing_vs_proven_edge",
    "rules.diversification",
    "sections.etf",
    "split_lines.line", "split_lines.subject_default",
    "stress_test.labels.ai_thematic",
]))
ALLOWLIST.update(_expand(_REASON_ZH_CN_NO_PERSONA, [
    "block_missing.rule_pending", "block_missing.rule_skip", "block_missing.rule_standing",
    "block_missing.rule_structural",
    "exit_entries.line",
    "hole_lines.averaging_down", "hole_lines.diversification",
    "hole_lines.holding_base", "hole_lines.holding_inconsistent", "hole_lines.holding_same_day",
    "honesty.prior_commitment_breach",
    "instrument_tags.too_heavy",
    "kpi.pnl_sub",
    "motive_entries.recorded", "motive_entries.saved",
    "payoff_lines.drag_plain", "payoff_lines.drag_with_amount",
    "pnl_lines.display.realized_only",
    "public_patterns.averaging_down", "public_patterns.diversification",
    "public_patterns.holding_period",
    "public_patterns.holding_period_same_day",
    "rule_grounding.diversification", "rule_grounding.holding_period",
    "rule_grounding.position_sizing",
    "rule_targets.line",
    "rules.averaging_down", "rules.exit_discipline", "rules.holding_period",
    "rules.position_sizing",
]))
ALLOWLIST.update(_expand(
    "#787 pre-existing gap: reached on the en persona sweep (a tickerless "
    "decision entry occurs for at least one mock persona's answers), but no "
    "zh-TW or zh-CN persona bundle produces one and no copy_corpus.py scene "
    "claims decision_entries -- data-dependent on which mock CSV/answer path "
    "happens to omit a ticker, not a systemic locale gap like the bucket above.",
    ["decision_entries.ticker_default"],
))


# --------------------------------------------------------------------------
# Coverage computation
# --------------------------------------------------------------------------
def compute_coverage():
    """Per locale: reached / unreached / trivial / allowlisted leaf keys,
    scoped to card-reachable registers. Reuses copy_corpus.py's own
    ``copy_leaves``/``value_pattern``/``MIN_EVIDENCE`` -- one reader of "is
    this catalog value evidenced," not a second derivation of the same fact
    (docs/development-guide.md section 7).

    Also returns ``never_saved_anyone`` -- allowlist keys that never once
    needed the allowlist to stay green (reached directly in every locale
    they appear in) and could be deleted. An entry reached in *some* locale
    (e.g. via persona evidence that only exists for zh-TW/en) but still
    needed by another (zh-CN, which persona_sweep never renders) is not
    stale: it is doing real work for the locale that lacks the evidence, so
    staleness is judged per key across all locales, never per (key, locale).
    """
    en_catalog = json.loads((SKILL / "copy" / "en.json").read_text(encoding="utf-8"))
    reachable = reachable_registers(set(en_catalog.keys()))

    golden = golden_text_by_locale()
    persona = persona_text_by_locale()
    catalogs = {language: json.loads((SKILL / "copy" / f"{language}.json").read_text(encoding="utf-8"))
                for language in LOCALES}

    report = {}
    ever_saved = set()     # allowlist keys that were actually needed somewhere
    ever_seen_reachable = set()  # allowlist keys that appear in a reachable register at all
    for language in LOCALES:
        text = golden[language] + "\n" + persona[language]
        reached, unreached, trivial, allowlisted = [], [], [], []
        for key, value in copy_corpus.copy_leaves(catalogs[language]):
            if key.split(".")[0] not in reachable:
                continue
            if key in ALLOWLIST:
                ever_seen_reachable.add(key)
            stripped = copy_corpus._PLACEHOLDER.sub("", value).strip()
            if len(stripped) < copy_corpus.MIN_EVIDENCE:
                trivial.append(key)
                continue
            if copy_corpus.value_pattern(value).search(text):
                reached.append(key)
            elif key in ALLOWLIST and ALLOWLIST[key].strip():
                allowlisted.append(key)
                ever_saved.add(key)
            else:
                unreached.append(key)
        report[language] = {
            "reached": sorted(reached), "unreached": sorted(unreached),
            "trivial": sorted(trivial), "allowlisted": sorted(allowlisted),
        }
    never_saved_anyone = sorted(ever_seen_reachable - ever_saved)
    return report, reachable, never_saved_anyone


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--report", action="store_true",
                     help="print full per-locale coverage and exit 0 regardless of gaps")
    args = ap.parse_args(argv)

    report, reachable, never_saved_anyone = compute_coverage()

    if args.report:
        print(f"card-reachable registers ({len(reachable)}): {', '.join(sorted(reachable))}\n")
        for language in LOCALES:
            stats = report[language]
            print(f"[{language}] reached={len(stats['reached'])} "
                  f"unreached={len(stats['unreached'])} "
                  f"allowlisted={len(stats['allowlisted'])} "
                  f"trivial={len(stats['trivial'])}")
            for key in stats["unreached"]:
                print(f"  UNREACHED  {key}")
            for key in stats["allowlisted"]:
                print(f"  allowlisted  {key}  -- {ALLOWLIST[key].splitlines()[0][:88]}")
        if never_saved_anyone:
            print(f"\nallowlist entries that never saved any locale ({len(never_saved_anyone)}) "
                  "-- reached directly everywhere they appear, so the entry could be deleted:")
            for key in never_saved_anyone:
                print(f"  {key}")
        return 0

    unreached = {language: stats["unreached"] for language, stats in report.items()
                 if stats["unreached"]}
    empty_reason = sorted(key for key in ALLOWLIST if not ALLOWLIST[key].strip())

    if empty_reason:
        print("FAIL copy key coverage: allowlisted key(s) with an empty reason", file=sys.stderr)
        for key in empty_reason:
            print(f"  {key}", file=sys.stderr)
        return 1

    if unreached:
        total = sum(len(keys) for keys in unreached.values())
        print(f"FAIL copy key coverage: {total} card-reachable copy key(s) reach no golden "
              "fixture and no fresh persona render", file=sys.stderr)
        for language, keys in sorted(unreached.items()):
            for key in keys:
                print(f"  [{language}] {key}", file=sys.stderr)
        print("  add a tests/copy_corpus.py scene, a mock-persona fixture that reaches it, "
              "or an ALLOWLIST entry with a specific reason (see this file's module "
              "docstring)", file=sys.stderr)
        return 1

    reached_total = sum(len(stats["reached"]) for stats in report.values())
    allowlisted_total = sum(len(stats["allowlisted"]) for stats in report.values())
    print(f"PASS copy key coverage: {reached_total} card-reachable copy keys evidenced "
          f"({allowlisted_total} allowlisted with a reason on record)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
