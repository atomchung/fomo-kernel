#!/usr/bin/env python3
"""Copy corpus: a generated golden of the card copy no persona reaches.

    python3 tests/copy_corpus.py                 # compare against the golden
    python3 tests/copy_corpus.py --update        # regenerate it
    python3 tests/copy_corpus.py --report        # coverage, no comparison

#402 knife 5. `test` is touched by 90% of commits, the single largest
surface in the fan-out baseline, and `tests/test_card_html.py` churned its
own line count once over in sixty commits. A large share of that file is
**hand-mirrored expectation**: a copy sentence written a second time inside
a test function so a corruption of `copy/*.json` is caught. Seven such tests
had accreted (account gate, annualized gap, reconciliation, best-strength
fallback, alpha interval, exit follow-ups, problem ledger), each one a
bespoke fixture plus its own literal block, and every new copy key in those
registers demanded another.

This module keeps the pin and removes the hand-mirroring, using the move
CLAUDE.md's mirrored-surfaces table already prescribes and #401 already
applied to `card-template.html`: **generate the mirror, commit it, and fail
the suite when a fresh regeneration disagrees.** The expectation still does
not share a source with the value under test at comparison time — the golden
is bytes on disk, so a `copy/*.json` corruption reddens this suite exactly
as a hand-written literal did (#373's tautology is avoided for the same
reason `test_card_template_matches_its_generator` avoids it). What changes
is the cost of an *intended* wording change: one regeneration and one
focused diff, instead of hunting literals across 2,380 lines of test code.

**Scenes are minimal bundles, on purpose.** Every scene hand-authors the
smallest bundle that lights its branch and renders it through the real
delivery surfaces (`render_private`, `render_html`, `render_public`) rather
than calling a private helper — a sentence that reaches a renderer helper
but not the card is a fake green of type 4 (docs/development-guide.md §2).
Lifecycle fidelity is not this corpus's job: `tests/persona_sweep.py` renders
real engine output for every mock persona, and `tests/test_review_v2.py`
owns the lifecycle. What those two cannot see is precisely what is collected
here — branches that need review history, a snapshot route, live-price
alpha, or an engine gate no fixture portfolio triggers.

**Fixture staleness is self-reporting.** A hand-built bundle that drifts out
of the shape `review.py` produces stops lighting its branch, and the keys it
covered fall into the `unreached` section of the golden. That is a visible
diff, not a silent pass, which is what makes minimal bundles safe here.

Deterministic, offline, stdlib only. No subprocess, no clock, no network.
"""
import argparse
import html as html_lib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "fomo-kernel"
sys.path.insert(0, str(SKILL / "engine"))
import card_renderer  # noqa: E402

GOLDEN = ROOT / "tests" / "golden" / "copy-corpus.txt"
LOCALES = ("zh-TW", "en")

# Catalog namespaces whose wording no rendered scene claims yet. Listed here
# rather than dumped per-key into the golden: most of them belong to the
# question layer (`review.py` renders those into the Review Plan, not the
# card), so they are a roadmap for future scenes, not a defect list. Removing
# a name from this tuple is how a new scene claims a register.
UNCLAIMED_NOTE = "not claimed by any scene in this corpus"


# --------------------------------------------------------------------------
# Scenes
# --------------------------------------------------------------------------
def _bundle(language, card=None, plan=None, narrative=None, **extra):
    """The smallest bundle `render_private` accepts, plus the scene's own facts."""
    out = {
        "schema_version": 2,
        "session_id": "corpus",
        "language": language,
        "engine_card": card or {},
        "review_plan": plan or {},
        "narrative": narrative or {"headline": "Headline.", "mirror": "Mirror."},
    }
    out.update(extra)
    return out


# `_GATE_STATUSES` used to live in test_card_html.py. It is the engine's own
# register of reasons the account-level pillar can be gated; `default` is not
# a status but the fallback an unrecognized one takes, reached the way a
# bundle written by an older engine reaches it.
GATE_STATUSES = ("no_cash_anchor", "mixed_trade_footprint", "negative_cash_rollback",
                 "cash_residual", "chain_unavailable")


def _account_gate(status):
    def build(language):
        gate = ({"status": "a_status_added_later", "data": {}} if status == "default"
                else {"status": status, "data": {}})
        return _bundle(language, card={
            "acct_perf": {"hold_twr": 0.5, "acct_twr": None, "gate": gate}})
    return build


def _annualized_gap(status):
    """No holdings pillar: the Block-1 gap note speaks instead of a gate line."""
    def build(language):
        card = {"acct_perf": {"gate": {"status": status, "data": {}}}}
        if status == "price_provenance":
            # #289 keeps precedence over the gate's own blocker.
            card = {"acct_perf": {"gate": {"status": "short_price_series", "data": {}}},
                    "price_provenance": {"mode": "unavailable"}}
        return _bundle(language, card=card)
    return build


_RECONCILIATION_RULES = {
    "zh-TW": "單筆部位上限定死 30%；超過就減，不新增。",
    "en": "Cap any single position at 30%; trim if it goes over, and do not add.",
}


def _reconciliation(with_metric):
    def build(language):
        prior = {"rule": _RECONCILIATION_RULES[language]}
        state = {}
        if with_metric:
            prior.update({"metric_key": "max_pos_pct", "metric_value": 0.51})
            state = {"metrics": {"max_pos_pct": 0.42}}
        return _bundle(language,
                       plan={"state_snapshot": {"prior_commitment": prior}},
                       engine_state=state)
    return build


# `_best_strength` ranks its candidates and renders only the winner, so one
# scene per dimension is the only way to reach all five sentences. Each entry
# satisfies exactly its own condition.
BEST_STRENGTH_DIMS = {
    "exit_discipline": {"dim": "出場紀律", "winner_early": 0.2},
    "position_sizing": {"dim": "部位 sizing", "max_pct": 0.18},
    "averaging_down": {"dim": "加碼攤平", "breach": 0, "count": 3, "tickers": ["AAA"]},
    "diversification": {"dim": "分散", "triggered": False, "n": 6},
    "holding_period": {"dim": "持有時間", "triggered": False, "median_hold": 41},
}


def _best_strength(dimension):
    def build(language):
        dims = [dict(BEST_STRENGTH_DIMS[dimension])] if dimension else [
            {"dim": "加碼攤平", "triggered": True, "severity": 0.8}]
        return _bundle(language, card={"strength": None, "dims_raw": dims})
    return build


_ALPHA_BASE = {
    "bench": "SPY", "beta": 2.05, "alpha_ann": 0.33,
    "port_tot": 3.21, "spy_tot": 0.60, "excess_vs_spy": 2.61,
    "benchmarks": {"SPY": {"excess": 2.61}, "QQQ": {"excess": 2.43}},
}


def _alpha_interval(credible, ci95, scope=None):
    """The two below-grid alpha notes are independent triggers (#363), so all
    four (credible x interval-sign) combinations are rendered. `scope` reaches
    the per-market suffix, which only a mixed-market card carries."""
    def build(language):
        breakdown = dict(_ALPHA_BASE)
        breakdown["credible"] = credible
        breakdown["alpha_stat"] = {"alpha_ann": breakdown["alpha_ann"], "ci95": list(ci95)}
        if scope:
            breakdown["scope"] = scope
            # `_benchmark_rows` drops a market row missing any of the three
            # totals, and no benchmark row means no alpha line at all.
            breakdown["by_market"] = {scope: {"port_tot": 1.8, "spy_tot": 0.4,
                                              "excess_vs_spy": 1.4, "beta": 1.1}}
        return _bundle(language, card={"alpha_beta_breakdown": breakdown})
    return build


def _due_revisit(revisit_id, checkpoint, ticker, compare, swaps=()):
    return {"kind": "due_revisit", "revisit_id": revisit_id, "checkpoint": checkpoint,
            "ticker": ticker, "compare": compare, "swaps": list(swaps)}


def _exit_followup(language):
    """Every branch of the follow-up entries on one bundle, price date frozen."""
    return _bundle(
        language,
        plan={
            "question_queue": [
                _due_revisit("r1", 30, "AAA", {}),
                _due_revisit("r2", 60, "BBB", {"needs_prices": ["BBB", "CCC"]}),
                _due_revisit("r3", 90, "DDD",
                             {"swap_net_pp": 4.0, "orig_ret": -0.1, "swap_ret": 0.2},
                             [{"ticker": "EEE"}]),
                _due_revisit("r4", 30, "FFF", {"idle_cash": True, "orig_ret": 0.33}),
            ],
            "state_snapshot": {"exit_backlog": {
                "summary": {"count": 4, "full": 3, "reduce": 1,
                            "span": {"first": "2026-01-02", "last": "2026-05-06"},
                            "top_tickers": [["GGG", 2]], "priced": 2,
                            "avg_hindsight_pp": 5.5, "sold_before_rise": 1},
                "items": [
                    {"ticker": "HHH", "kind": "full", "exit_date": "2026-02-03",
                     "compare": {"swap_net_pp": 1.0, "orig_ret": 0.1, "swap_ret": 0.2}},
                    # Only two backlog rows render (`items[:2]`), so the
                    # idle-proceeds row lives in the fallback scene below.
                    {"ticker": "JJJ", "kind": "full", "exit_date": "2026-04-05",
                     "compare": {"needs_prices": ["JJJ"]}},
                ]}}},
        revisit_resolutions=[
            {"revisit_id": "r1", "checkpoint": 30, "status": "still_valid", "note": "NOTE"},
            {"revisit_id": "r2", "checkpoint": 60, "status": "modified"},
            {"revisit_id": "r3", "checkpoint": 90, "status": "falsified"},
            {"revisit_id": "r4", "checkpoint": 30, "status": "still_valid"},
        ],
        engine_state={"price_snapshot": {"as_of": "2026-07-20"}},
    )


def _exit_followup_fallback(language):
    """No frozen price date and no ticker. The two locales are deliberately
    asymmetric: the English swap lead contributes nothing while the Chinese
    one contributes a single space, because the templates join to the clause
    that follows differently. That space is `exit_followup.frozen_lead_absent`
    in the catalog, not an accident of formatting."""
    return _bundle(
        language,
        plan={
            "question_queue": [_due_revisit(
                "r1", 30, None, {"swap_net_pp": 2.0, "orig_ret": 0.1, "swap_ret": 0.3})],
            "state_snapshot": {"exit_backlog": {
                "summary": {"count": 1, "full": 1, "reduce": 0, "span": {}, "top_tickers": []},
                "items": [{"ticker": None, "kind": "full", "exit_date": "2026-02-03",
                           "compare": {"orig_ret": 0.12}},
                          {"ticker": "III", "kind": "reduce", "exit_date": "2026-03-04",
                           "compare": {"idle_cash": True, "orig_ret": -0.05}}]}}},
        revisit_resolutions=[{"revisit_id": "r1", "checkpoint": 30, "status": "still_valid"}],
    )


# `_problem_lines` renders at most `top[:3]`, so the eight problem names take
# three scenes. Trends are varied across them so all three `trends` words ride
# along rather than needing scenes of their own.
PROBLEM_GROUPS = (
    ("oversize", "concentration", "avgdown_breach"),
    ("sell_winner_early", "hold_inconsistency", "exit_anxiety"),
    ("fomo_entry", "horizon_break"),
)
_TRENDS = ("worse", "better", "flat")


def _problem_ledger(keys, with_rules):
    """`_problem_lines` reads `problem_stats`, which only a review with history
    carries, so no first-review card renders one of these sentences.

    `dims_raw` is not decoration: `_risks_block` returns the "no behavioral
    diagnosis" note and never reaches `_problem_lines` when the card has no
    scored dimension, so a fixture without one pins nothing. The pin this
    scene replaces called `_problem_lines` directly and could not have seen
    that — the reason scenes render through the delivery surface.
    """
    def build(language):
        stats = {
            "top": list(keys),
            "per_key": {key: {"recent_count": 3 + index, "prev_count": 1 + index,
                              "trend": _TRENDS[index % len(_TRENDS)]}
                        for index, key in enumerate(keys)},
        }
        extra = {}
        if with_rules:
            stats["rules_check"] = [{"rule_id": "r-kept", "text": "RULE_KEPT",
                                     "verdict": "held", "held_streak": 1}]
            extra["rule_breach_decisions"] = [
                {"rule_id": "r-broken", "rule_text": "RULE_BROKEN",
                 "decision": "exception", "note": "NOTE_TEXT"}]
        return _bundle(
            language,
            card={"dims_raw": [{"dim": "加碼攤平", "triggered": True, "severity": 0.8}]},
            plan={"state_snapshot": {"problem_stats": stats}},
            **extra)
    return build


# (name, register(s) claimed, builder). `registers` names the top-level
def _condition_state(verdict=None, reason=None, kind="numeric"):
    """#412: the engine's own read of a user-authored condition, on the card.

    No persona reaches this — a condition slot is created by a user writing
    their own rule, which no mock CSV can do — and it is exactly the branch that
    must not go quiet: a line already crossed at commit time, a condition nobody
    could anchor, or a yes/no watch that has just started, has to say so where
    the rule is printed."""
    condition = {"slot_id": "corpus", "kind": kind,
                 "criterion": "sell if quarterly revenue growth drops under 30%",
                 "query": "what was the most recent quarterly revenue?",
                 "tier": "unmapped" if reason else "researched"}
    if verdict:
        condition["baseline_verdict"] = verdict
    if reason:
        condition["unmapped_reason"] = reason

    def build(language):
        return _bundle(language, commitment={
            "rule": condition["criterion"], "origin": "custom", "condition": condition})
    return build


# #412 second half. The per-period check lines live in the reconciliation
# opener, and no persona reaches any of them for the same reason: every one
# needs a standing condition, which only a user writing their own rule creates.
_CHECK_CRITERION = "sell if quarterly revenue growth drops under 30%"
_CHECK_SLOT = {
    "slot_id": "corpus-slot", "kind": "numeric", "criterion": _CHECK_CRITERION,
    "query": "what was the most recent quarterly revenue?", "tier": "researched",
    "threshold": {"value": 30, "unit": "%", "direction": "below"}, "near_line": 3.0,
    "baseline": {"value": 38.0, "as_of": "2026-05-20", "source": "results release"},
}


def _check_row(**over):
    row = {"check_id": "corpus-check", "slot_id": "corpus-slot", "session_id": "corpus",
           "date_end": "2026-08-26", "created": "2026-08-26", "lookup_status": "ok",
           "observation": {"value": 36.0, "as_of": "2026-08-20", "source": "10-Q"},
           "information_state": "new_period", "engine_verdict": "not_met",
           "final_verdict": "not_met", "verdict_source": "engine"}
    row.update(over)
    return row


def _condition_check(prior=None, checks=(), summary=None, slots=None):
    """One reconciliation-opener scene: the then/now pair, the per-condition
    readings, and the "what did this review not get to" sentence.

    `condition_slots_due` is what the fact and blind lines resolve a check's
    criterion through — the plan's own bounded roster — so a scene that omits
    it renders nothing, which is the visible-diff staleness contract this
    corpus relies on."""
    def build(language):
        snapshot = {"condition_slots_due": list(slots if slots is not None else [_CHECK_SLOT])}
        if prior is not None:
            snapshot["prior_commitment"] = prior
        if summary is not None:
            snapshot["condition_slots_summary"] = summary
        return _bundle(language, plan={"state_snapshot": snapshot},
                       condition_checks=[dict(row) for row in checks])
    return build


def _condition_then_now(kind="numeric", **check_over):
    prior = {"rule": _CHECK_CRITERION, "condition": dict(_CHECK_SLOT, kind=kind)}
    if kind == "event":
        prior["condition"] = {key: value for key, value in prior["condition"].items()
                              if key not in ("threshold", "near_line", "baseline")}
    return _condition_check(prior=prior, checks=[_check_row(**check_over)])


# `copy/*.json` keys this scene is responsible for lighting; the coverage
# report below reads them.
SCENES = (
    ("condition_state/already_met", ("condition_state",), _condition_state(verdict="met")),
    ("condition_state/no_threshold", (), _condition_state(reason="no_threshold")),
    ("condition_state/no_baseline", (), _condition_state(reason="no_baseline")),
    # A pre-check-flow row on disk. It is never rewritten, so its sentence has
    # to keep resolving long after new event slots stopped being written this way.
    ("condition_state/no_adjudicator", (),
     _condition_state(reason="no_adjudicator", kind="event")),
    ("condition_state/event_watch_started", (), _condition_state(kind="event")),
    ("condition_state/silent_when_watched", (), _condition_state(verdict="not_met")),
    # The per-period check, on the card. Each verdict wording is its own scene
    # because the renderer prints exactly one of them per then/now pair.
    ("condition_check/then_now_not_met", ("condition_check",), _condition_then_now()),
    ("condition_check/then_now_met", (),
     _condition_then_now(observation={"value": 21.0, "as_of": "2026-08-20", "source": "10-Q"},
                         engine_verdict="met", final_verdict="met")),
    ("condition_check/then_now_near_line", (),
     _condition_then_now(observation={"value": 31.0, "as_of": "2026-08-20", "source": "10-Q"},
                         engine_verdict="near_line", final_verdict="near_line")),
    ("condition_check/then_now_unknown", (),
     _condition_then_now(observation={"value": 36.0, "as_of": "2026-08-20", "source": "10-Q"},
                         engine_verdict=None, final_verdict="unknown")),
    ("condition_check/then_now_event", (),
     _condition_then_now(kind="event",
                         observation={"summary": "the CEO is still in role", "as_of": "2026-08-20",
                                      "source": "8-K"},
                         engine_verdict=None, final_verdict="unknown")),
    ("condition_check/then_now_no_baseline", (),
     _condition_check(prior={"rule": _CHECK_CRITERION,
                             "condition": {key: value for key, value in _CHECK_SLOT.items()
                                           if key != "baseline"}},
                      checks=[_check_row()])),
    # A condition-anchored commitment whose line nobody checked keeps the bare
    # statement rather than borrowing the metric branch's shape (#412).
    ("condition_check/then_now_absent_falls_back", (),
     _condition_check(prior={"rule": _CHECK_CRITERION, "condition": dict(_CHECK_SLOT)},
                      checks=[_check_row(lookup_status="failed", observation=None,
                                         information_state=None, engine_verdict=None,
                                         final_verdict="unknown",
                                         reason="the quarter has not been reported")])),
    ("condition_check/fact_and_looks_wrong", (), _condition_check(checks=[_check_row()])),
    ("condition_check/fact_without_a_source", (),
     _condition_check(checks=[_check_row(observation={"value": 36.0, "as_of": "2026-08-20"})])),
    ("condition_check/blind_with_reason", (),
     _condition_check(checks=[_check_row(lookup_status="failed", observation=None,
                                         information_state=None, engine_verdict=None,
                                         final_verdict="unknown",
                                         reason="the publisher withdrew the release")])),
    ("condition_check/blind_without_a_reason", (),
     _condition_check(checks=[_check_row(lookup_status="failed", observation=None,
                                         information_state=None, engine_verdict=None,
                                         final_verdict="unknown")])),
    ("condition_check/summary_when_something_was_missed", (),
     _condition_check(checks=[_check_row()],
                      summary={"lines_total": 4, "due_now": 4, "beyond_cap": 0,
                               "unmapped_lines": 0, "unreadable_slots": 0,
                               "unreadable_checks": 0})),
    # Silence is a branch too: a review that checked everything says nothing,
    # and an unchecked period that never listed the condition prints no facts.
    ("condition_check/silent_when_everything_was_checked", (),
     _condition_check(checks=[_check_row()],
                      summary={"lines_total": 1, "due_now": 1, "beyond_cap": 0,
                               "unmapped_lines": 0, "unreadable_slots": 0,
                               "unreadable_checks": 0})),
    ("condition_check/silent_when_nothing_was_checked", (), _condition_check()),
    *[(f"account_gate/{status}", ("account_gate",), _account_gate(status))
      for status in GATE_STATUSES + ("default",)],
    *[(f"annualized_gap/{status}", (), _annualized_gap(status))
      for status in ("no_prices", "short_price_series", "accounting_reconciliation",
                     "price_provenance", "a_status_added_later")],
    ("reconciliation/with_metric", ("reconciliation",), _reconciliation(True)),
    ("reconciliation/plain", (), _reconciliation(False)),
    ("best_strength/no_signal", ("best_strength",), _best_strength(None)),
    *[(f"best_strength/{dimension}", (), _best_strength(dimension))
      for dimension in sorted(BEST_STRENGTH_DIMS)],
    ("alpha_interval/credible_positive", ("alpha_interval",),
     _alpha_interval(True, (0.07, 0.54))),
    ("alpha_interval/credible_negative", (), _alpha_interval(True, (-0.05, 0.74))),
    ("alpha_interval/unreliable_positive", (), _alpha_interval(False, (0.05, 0.74))),
    ("alpha_interval/unreliable_negative", (), _alpha_interval(False, (-0.10, 0.74))),
    ("alpha_interval/per_market_scope", (), _alpha_interval(True, (0.07, 0.54), scope="TW")),
    ("exit_followup/all_branches", ("exit_followup",), _exit_followup),
    ("exit_followup/fallbacks", (), _exit_followup_fallback),
    *[(f"problems/{'_'.join(group[:1])}", ("problems", "problem_keys") if index == 0 else (),
       _problem_ledger(group, with_rules=index == 0))
      for index, group in enumerate(PROBLEM_GROUPS)],
)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
_TAG = re.compile(r"<[^>]+>")
_DROP = re.compile(r"<(style|script)\b.*?</\1>", re.S | re.I)


def html_text(markup):
    """The card's words with its markup and design tokens removed.

    The corpus pins copy, not layout: `tests/test_card_html.py` owns the
    structural contract and `tests/persona_sweep.py` owns HTML drift. Keeping
    CSS out of the golden means a palette change cannot bury a wording change
    in the diff.
    """
    text = _DROP.sub(" ", markup)
    text = re.sub(r"<(br|/p|/h[1-6]|/div|/li|/tr)\s*/?>", "\n", text, flags=re.I)
    text = _TAG.sub("", text)
    text = html_lib.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)


def render_scene(build, language):
    """The three delivery surfaces, in the order a reader meets them.

    The Markdown front matter is dropped: it carries session identity and
    privacy flags, no catalog copy, and repeating five identical lines per
    scene would bury the wording this golden exists to show.
    `tests/test_card_html.py` owns that header.
    """
    bundle = build(language)
    private = _FRONTMATTER.sub("", card_renderer.render_private(bundle)).strip()
    return (
        ("private.md", private),
        ("private.html", html_text(card_renderer.render_html(bundle))),
        ("public.md", card_renderer.render_public(bundle).strip()),
    )


# --------------------------------------------------------------------------
# Copy-key coverage
# --------------------------------------------------------------------------
# A value shorter than this carries no evidence: `"."`, `"; "`, `"low"` and
# their kin match somewhere in any card by accident, so counting them as
# "reached" would inflate coverage without pinning anything.
MIN_EVIDENCE = 8
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def copy_leaves(node, prefix=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from copy_leaves(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from copy_leaves(value, f"{prefix}[{index}]")
    elif isinstance(node, str):
        yield prefix, node


def value_pattern(value):
    """A regex for a catalog value whose `{placeholders}` are unknown at rest.

    A placeholder becomes `.*?` rather than `.+?`: several of them resolve to
    the empty string on the very branch that is worth pinning (`{scope}` on a
    single-market card, `{example}` with no ticker to name), and requiring a
    character there silently reports those keys as unreached. Whitespace is
    made flexible because the renderers wrap and join, and a line break inside
    a sentence is a layout fact rather than a copy change.
    """
    # Split on whitespace *before* escaping: `re.escape` escapes spaces too
    # (they are meaningful under `re.VERBOSE`), so substituting on its output
    # would leave a literal backslash in front of every `\s+`.
    parts = [r"\s+".join(re.escape(token) for token in part.split()) if part.strip()
             else r"\s*"
             for part in _PLACEHOLDER.split(value)]
    return re.compile(".*?".join(parts))


def coverage(corpus_by_locale):
    """Per locale: which claimed-register keys the rendered corpus carries."""
    claimed = {register for _name, registers, _build in SCENES for register in registers}
    out = {}
    for language in LOCALES:
        catalog = json.loads((SKILL / "copy" / f"{language}.json").read_text(encoding="utf-8"))
        text = corpus_by_locale[language]
        reached, unreached, trivial = [], [], []
        for key, value in copy_leaves(catalog):
            if key.split(".")[0] not in claimed:
                continue
            stripped = _PLACEHOLDER.sub("", value).strip()
            if len(stripped) < MIN_EVIDENCE:
                trivial.append(key)
            elif value_pattern(value).search(text):
                reached.append(key)
            else:
                unreached.append(key)
        out[language] = {"reached": sorted(reached), "unreached": sorted(unreached),
                         "trivial": sorted(trivial)}
    unclaimed = sorted({key.split(".")[0] for key, _ in copy_leaves(
        json.loads((SKILL / "copy" / "en.json").read_text(encoding="utf-8")))} - claimed)
    return out, unclaimed


# --------------------------------------------------------------------------
# Corpus
# --------------------------------------------------------------------------
LAST_COVERAGE = {}


def build_corpus():
    chunks = ["# Generated by tests/copy_corpus.py --update. Do not hand-edit.",
              "# Every scene renders through the real delivery surfaces; a diff here",
              "# is a wording or renderer change, and must be an intended one."]
    corpus_by_locale = {language: [] for language in LOCALES}
    for name, _registers, build in SCENES:
        for language in LOCALES:
            for surface, text in render_scene(build, language):
                chunks.append(f"\n{'=' * 74}\n{name} [{language}] {surface}\n{'=' * 74}")
                chunks.append(text)
                corpus_by_locale[language].append(text)
    joined = {language: "\n".join(parts) for language, parts in corpus_by_locale.items()}

    per_locale, unclaimed = coverage(joined)
    LAST_COVERAGE.clear()
    LAST_COVERAGE.update(per_locale)
    chunks.append(f"\n{'=' * 74}\ncopy-key coverage\n{'=' * 74}")
    for language in LOCALES:
        stats = per_locale[language]
        chunks.append(f"[{language}] reached {len(stats['reached'])} · "
                      f"unreached {len(stats['unreached'])} · "
                      f"trivial {len(stats['trivial'])}")
        for key in stats["unreached"]:
            chunks.append(f"  unreached: {key}")
    chunks.append(f"\nregisters {UNCLAIMED_NOTE} ({len(unclaimed)}):")
    for register in unclaimed:
        chunks.append(f"  {register}")
    return "\n".join(chunks) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--update", action="store_true",
                    help="regenerate the golden instead of comparing against it")
    ap.add_argument("--report", action="store_true",
                    help="print copy-key coverage and exit")
    args = ap.parse_args(argv)

    corpus = build_corpus()
    stranded = {language: stats["unreached"]
                for language, stats in LAST_COVERAGE.items() if stats["unreached"]}

    if args.report:
        print(corpus.split("copy-key coverage", 1)[1].strip())
        return 0

    if args.update:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        previous = GOLDEN.read_text(encoding="utf-8") if GOLDEN.exists() else ""
        GOLDEN.write_text(corpus, encoding="utf-8")
        print("unchanged" if previous == corpus else "golden rewritten:", GOLDEN)
        return 0

    if stranded:
        # A claimed register whose key reaches no surface is either dead copy
        # or a scene that stopped lighting its branch. Both are defects, and
        # both are invisible without this check — the reason it is a gate and
        # not a note in the golden. The escape hatch is to stop claiming the
        # register in `SCENES`, which is a visible decision.
        print("FAIL copy corpus: claimed copy keys reach no rendered surface",
              file=sys.stderr)
        for language, keys in sorted(stranded.items()):
            for key in keys:
                print(f"  [{language}] {key}", file=sys.stderr)
        return 1

    if not GOLDEN.exists():
        print(f"FAIL missing golden: {GOLDEN}\n     run: python3 tests/copy_corpus.py --update",
              file=sys.stderr)
        return 1
    expected = GOLDEN.read_text(encoding="utf-8")
    if expected == corpus:
        scenes = len(SCENES) * len(LOCALES) * 3
        reached = sum(len(stats["reached"]) for stats in LAST_COVERAGE.values())
        print(f"PASS copy corpus: {scenes} rendered surfaces match the golden "
              f"({reached} copy keys pinned)")
        return 0

    got, want = corpus.splitlines(), expected.splitlines()
    print("FAIL copy corpus drifted from tests/golden/copy-corpus.txt", file=sys.stderr)
    for index in range(max(len(got), len(want))):
        line_got = got[index] if index < len(got) else "<missing>"
        line_want = want[index] if index < len(want) else "<missing>"
        if line_got != line_want:
            print(f"  first difference at line {index + 1}", file=sys.stderr)
            print(f"    golden:   {line_want}", file=sys.stderr)
            print(f"    rendered: {line_got}", file=sys.stderr)
            break
    print("  intended change? python3 tests/copy_corpus.py --update, then read the diff",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
