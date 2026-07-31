#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Replay the question-episode bank's mechanical half (#417).

An **episode** is one dogfood or real-use miss, frozen as data:
``(state fixture, question, answers with expected verdicts)``. The bank lives in
``evals/episodes/``; this runner executes the deterministic, offline half of the
grading and nothing else. The rubric judge (#417's second half) is a separate,
opt-in, billable harness and never runs here — ``docs/eval-design.md`` keeps
non-deterministic runs out of the default suite.

Why a bank at all: before #417 every dogfood miss converted into an issue and
nothing else, so no miss was ever replayed against a later engine. An episode
is the replayable form. Because the fixture is a mock CSV rather than a frozen
artifact dump, each replay re-derives the engine's own facts through
``review.py prepare`` — the bank cannot drift out of sync with the engine the
way a committed artifact mirror would (development-guide section 1).

What the mechanical half proves, per answer under test:

``number_provenance``   every number on a user-facing surface traces to a number
                        the engine actually emitted (never-loosen rule 1: agent
                        prose derives nothing)
``honesty_coverage``    every honesty key this episode puts in scope is both
                        still triggered by the fixture and disclosed by the
                        answer, in a digit-free sentence
``privacy_trace``       every ticker-shaped token traces to the synthetic
                        fixture or the engine artifacts, and no internal
                        position-id format appears (#274's text channel)
``surface_hygiene``     no snake_case engine identifier reached a user-facing
                        surface (#262: raw option values and metric keys)
``locale_purity``       an ``en`` surface carries no CJK; a non-``en`` surface
                        carries no untranslated English metric label (#262)
``condition_check_integrity``
                        a per-period condition result survives the engine's own
                        check validator, every figure in the prose traces to
                        the record, and a lookup that failed is spoken as a
                        lookup that failed (#412 second half)

Every check is an **invariant** — something the product must never do. None of
them compares an answer against the wording the product happens to ship today,
because the question layer is being reshaped (#395, #396, #412) and a test that
pinned current phrasing would report the improvement as a regression. Owner
direction, 2026-07-26: answers are expected to get freer, more varied and more
restrained over time, so a passing answer here is a **witness that the
invariants hold**, never a model answer to copy.

The first cut of this bank got that wrong. It shipped a ``grounding_fidelity``
check requiring every word of a presented candidate rule to be a verbatim
substring of an engine field — a behavior oracle in an invariant's clothing,
which would have failed exactly the agent-authored, two-sided reasoning #412 is
building. It is gone. What #293 actually violated (an invented fact in the slot
where measured facts live) has no mechanical test today, so EP-002 stays in the
bank recorded but ungraded, ``blocked_on`` #414.

Beyond the answer checks, one gate asks the other question — **did asking buy
anything** (``QUESTION_CONSUMERS`` below, #429).

Three interlocks keep a green run meaningful (development-guide section 2 —
"a checker that stays green under its mutation is not evidence"):

1. Every graded episode declares at least one answer that MUST fail, and names
   the checks it must fail on. A checker that decays into a no-op turns those
   answers green and this runner red.
2. A declared check that finds nothing to look at is a failure, not a pass. An
   answer carrying no options cannot quietly satisfy an option-level check.
3. Bank coverage: every check must be observed both passing and failing at
   least once across the bank, or the run reports the gap.

Usage:
  python3 evals/run_episodes.py                 # replay the whole bank
  python3 evals/run_episodes.py EP-002 EP-003   # replay selected episodes
  python3 evals/run_episodes.py --list
"""
import argparse
import ast
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
SKILL_DIR = REPO / "skills" / "fomo-kernel"
EPISODE_DIR = REPO / "evals" / "episodes"
COPY_DIR = SKILL_DIR / "copy"
TESTS_DIR = REPO / "tests"


def _load_module(name, path):
    """Import a repo script by path (persona_sweep's helper, same contract)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Borrowed rather than restated, so there is one definition of each rule:
#   check_card._INTERNAL_KEYS  the internal-field-name ban already enforced on
#                              the card face (A-12)
#   persona_sweep.CJK          the #356 CJK ranges
#   privacy_lint.POSITION_ID   the internal position-id format (#274)
#   card_renderer.numeric_claim the narrative digit ban, spelled-out forms
#                              included (#194)
#   conditions.build_slot      the engine's own condition-slot validator (#412),
#                              so the episode grades an envelope against the gate
#                              that actually ships rather than a copy of its rules
_check_card = _load_module("episodes_check_card", TESTS_DIR / "agent" / "check_card.py")
_persona_sweep = _load_module("episodes_persona_sweep", TESTS_DIR / "persona_sweep.py")
_privacy_lint = _load_module("episodes_privacy_lint", SKILL_DIR / "tools" / "privacy_lint.py")
_card_renderer = _load_module("episodes_card_renderer", SKILL_DIR / "engine" / "card_renderer.py")
conditions = _load_module("episodes_conditions", SKILL_DIR / "engine" / "conditions.py")

INTERNAL_KEYS = _check_card._INTERNAL_KEYS
CJK = _persona_sweep.CJK
POSITION_ID = _privacy_lint.POSITION_ID
numeric_claim = _card_renderer.numeric_claim

# A bare number, thousands separators included. Unsigned on purpose: a leading
# "-" in real copy is a hyphen or an ISO date separator far more often than a
# negative sign, and DATE below consumes date spans before this pattern runs.
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Ticker-shaped: all-caps/digits with at least one letter, optional market or
# class suffix. Two characters minimum so English "I" and "A" never match.
UPPER_TOKEN = re.compile(r"\b(?=[A-Z0-9]*[A-Z])[A-Z0-9]{2,7}(?:[.-][A-Z0-9]{1,4})?\b")
SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
# Plan values that carry no engine fact: absolute paths, content hashes, and
# session fingerprints would otherwise donate their digits to the provenance
# allow-set and quietly widen it.
NOISE_KEYS = {"state_root", "engine_meta", "path", "fingerprint", "session_id",
              "engine_version", "cycle_id", "id", "thesis_id", "event_id"}
HEX_TOKEN = re.compile(r"^[0-9a-f]{12,}$")

CHECK_NAMES = ("number_provenance", "honesty_coverage", "privacy_trace",
               "surface_hygiene", "locale_purity", "condition_integrity",
               "condition_check_integrity")
ANSWER_PART_KEYS = ("prose", "presented_options", "discloses")

# The rubric judge's axes (#417 second half). The names live here rather than in
# `evals/judge_episodes.py` for one reason: a typo'd axis has to fail in the free
# offline suite, and this loader cannot import that module without taking on its
# `anthropic` dependency. The judge reads this tuple and holds the rubric text;
# it refuses to run if either side has grown an axis the other never heard of.
# Nothing in this file grades an axis — a judge verdict is not deterministic and
# does not belong in the default suite (`docs/eval-design.md`'s hierarchy).
JUDGE_AXES = ("two_sided", "overrulable", "criterion_neutrality")

# ── The question-consumer gate (#429) ────────────────────────────────────────
#
# The bank's other half, and the more expensive failure of the two. A check on an
# *answer* asks "did it lie". This asks "did the question buy anything" — and for
# two kinds the answer today is no: the user's classification is written to an
# append-only projection nothing reads, so the card is byte-identical to skipping
# the question (#429 carries the differential receipt).
#
# Owner ruling, 2026-07-26: "pure reflective value, zero mechanical consumption"
# is NOT a legitimate reason for a question to exist here. The agent reads the
# Review Plan every review, so any stored answer *can* be replayed —
# `headline_motive_events` already is. Silence means nobody wired it. There is
# therefore no `reflection_only` state, by design: a kind with no verifiable
# consumer fails, and the only way to stay green is the shrinking ratchet below.
#
# The list of kinds is NOT maintained here: it is read from the Review Plan
# schema's own `kind` enum, so a new question kind that ships without a
# declaration fails this gate instead of slipping in unnoticed.
#
#   sink    the bundle key the answer becomes
#   reaches claims to verify: "card" (the sink is referenced by the renderer that
#           delivers it) or "state:<key>" (that key exists in a real prepared
#           plan's state_snapshot, so the next review can see it)
QUESTION_CONSUMERS = {
    "add_thesis": ("thesis_decisions", ("card", "state:thesis_states")),
    "headline_motive": ("headline_motive_events", ("card", "state:headline_motive_events")),
    "revisit": ("revisit_resolutions", ("card", "state:due_revisits")),
    "due_revisit": ("revisit_resolutions", ("card", "state:due_revisits")),
    "rule_breach": ("rule_breach_decisions", ("card", "state:problem_stats")),
    "initial_thesis": ("initial_thesis_events", ()),
    "exit_consistency": ("exit_consistency_events", ()),
    # #412: a crossing answer becomes the `user_response` on the check row it
    # was asked about (one row, complete story), and the next review reads the
    # whole line back through the bounded due list. Both halves are verifiable:
    # the renderer reads `condition_checks` for the then/now and the fact lines,
    # and `condition_slots_due` is a real key in a prepared plan.
    "condition_crossing": ("condition_checks", ("card", "state:condition_slots_due")),
    # A basis answer either records `basis_resolution` on the same check row or
    # writes a new slot row on the line — which is what the *next* review's due
    # list carries forward, criterion and all.
    "condition_basis": ("condition_checks", ("card", "state:condition_slots_due")),
    # #403: the answer is written straight into the canonical
    # position_rationales.jsonl through the one append service, not projected
    # out of the session bundle the way every other kind's is -- a second writer
    # into a canonical stream would be the parallel store the issue forbids.
    # So the verifiable consumer is the plan key the *next* review reads it back
    # through, and there is deliberately no card claim: #403 parks card layout,
    # and a rationale is a fact surface rather than a card section.
    "rationale_refresh": ("position_rationales", ("state:position_rationales",)),
}
# Kinds whose answer reaches nothing, each pinned to the issue that owns the
# disposition. A ratchet, not an exemption: a kind that becomes wired must leave
# this list, and a newly unwired kind cannot join it without an issue.
KNOWN_UNWIRED = {"initial_thesis": "#429", "exit_consistency": "#429"}

# ── The field-level question-consumer gate (#451) ───────────────────────────
#
# QUESTION_CONSUMERS proves a sink's *name* reaches a surface. #451 found that
# this is satisfied by a single field: `add_thesis` passed with
# `_decision_entries` reading `decision` and `ticker` while `note` -- the
# user's own words on why they added -- and `evidence_delta` -- claim/source/
# observed_at/falsifier, mandatory input when the user classifies an add as
# `new_evidence` -- had no reader at all. This section deepens the "card"
# claim to field-level: it derives, from source, which fields an event
# actually CAN carry (the builder's own dict-literal + subscript-assignment
# keys -- the same idiom `tests/test_coach_data_cli.py` parses for #452's
# registry check, rather than hand-listing field names a second time), and
# which of those fields the card's own consuming loop actually reads, and
# fails when a field is in the first set and not the second, unless declared
# in KNOWN_UNWIRED_FIELDS with a reason.
#
# Scope, stated rather than silently assumed (development-guide section 2:
# "an honestly-scoped narrower check ... is a better outcome than a broad
# check nobody trusts"):
#
#   - Covers the "card" claim only. The "state:<key>" claim keeps today's
#     key-existence granularity: proving a specific *field* of an event
#     reaches a specific *field* of state_snapshot needs tracing through a
#     second transformation (thesis.reconstruct_states and friends) that does
#     not share the source shape below, and that transformation's inputs and
#     outputs live in different files per kind. Not attempted here.
#   - Covers `add_thesis`, `headline_motive`, `revisit`, `due_revisit`, and
#     `rule_breach` -- every kind whose sink is built by a single function
#     following the `event = {...literal...}` + zero or more
#     `event["key"] = ...` idiom (confirmed by reading all four builders) and
#     consumed by a single `for event in bundle.get(sink) or []:` loop in
#     card_renderer.py (confirmed the same way).
#   - Does NOT cover `condition_crossing` / `condition_basis`. Their sink,
#     `condition_checks`, is read by more than a dozen functions across
#     card_renderer.py (`_condition_now`, `_condition_outcome`,
#     `_condition_reading_line`, `_condition_summary_line`, ... -- the
#     "Per-period condition check flow" row of
#     docs/maintainer-guide.md's mirrored-surfaces table names this
#     cluster explicitly), not one contained loop. A
#     trustworthy field scan there needs a real call-graph across that
#     cluster, not a single-function AST walk, and this cut does not attempt
#     one rather than ship an imprecise version of it. These two kinds stay
#     at today's name-level precision -- KNOWN_UNWIRED_FIELDS carries no
#     `condition_checks` entries, and none should be added by mechanically
#     extending this section's pattern to it.
#   - `initial_thesis` / `exit_consistency` are excluded because they are
#     already fully unwired at the *kind* level (KNOWN_UNWIRED above) --
#     field-level detail on top of "nothing reads this sink" adds no fact a
#     reader does not already have.
#
# `question_field_consumers()` below fails loudly (rather than passing
# vacuously) if either half of its source pattern stops matching: a builder
# function that disappears or is found but yields zero fields, or a
# `bundle.get(sink)` loop that disappears from card_renderer.py -- the same
# "abstention is not a pass" interlock `run_check` already applies to answer
# checks.
QUESTION_FIELD_SOURCES = {
    # sink -> (path under skills/fomo-kernel/, function that builds the
    # event, the local variable name the function builds it into)
    "thesis_decisions": ("engine/thesis.py", "build_decision_events", "event"),
    "headline_motive_events": ("engine/review.py", "_build_headline_motive_events", "event"),
    "revisit_resolutions": ("engine/review.py", "_build_revisit_resolutions", "event"),
    "rule_breach_decisions": ("engine/review.py", "_build_rule_breach_decisions", "event"),
}

# Declared per (sink, field): a field the builder constructs that the card's
# consuming loop does not read, with why. A ratchet exactly like
# KNOWN_UNWIRED above, one level down: a field leaving this dict without a
# matching new read in the loop fails the check ("now read but still
# declared"), and a field the builder no longer constructs fails it too
# ("stale declaration") -- so this list can only ever describe the present,
# never accumulate dead entries silently. Every field here was found by the
# scan added for #451, filed as #480 for disposition; the reasons are the
# grounds for *why a reader has not been demanded yet*, not a claim that the
# field should never get one.
KNOWN_UNWIRED_FIELDS = {
    "thesis_decisions": {
        "note": "#480 the #451 finding itself -- the user's own words on why they added",
        "evidence_delta": "#480 mandatory on new_evidence (claim/source/observed_at/falsifier); no reader anywhere in engine/",
        "cycle_id": "#480 engine join key to the thesis cycle, consumed by thesis.reconstruct_states, not the card",
        "session_id": "#480 engine bookkeeping (which review session wrote this)",
        "event": "#480 literal event-type discriminator, constant per kind",
        "schema_version": "#480 versioning metadata",
        "review_date": "#480 per-event timestamp; the card's as-of date comes from engine_state, not per-decision",
        "decision_cursor": "#480 internal decision-sequence marker (thesis continuity plumbing)",
        "decision_id": "#480 legacy content-addressed id, superseded by event_id",
        "thesis_id": "#480 engine join key to the thesis row",
        "revises": "#480 audit-trail link to the event this supersedes",
        "evidence_id": "#480 content-addressed id for evidence_delta; the content itself is evidence_delta, flagged separately above",
        "provenance": "#480 audit metadata (capture/confirm timing); not new content beyond evidence_delta",
        "event_id": "#480 content-addressed identity hash",
    },
    "headline_motive_events": {
        "event": "#480 literal event-type discriminator",
        "event_id": "#480 content-addressed identity hash",
        "question_id": "#480 engine join key to the asked question",
        "review_date": "#480 per-event timestamp; the card's as-of date comes from engine_state, not per-decision",
        "schema_version": "#480 versioning metadata",
        "session_id": "#480 engine bookkeeping",
    },
    "revisit_resolutions": {
        "date": "#480 per-event timestamp; the card's as-of date comes from engine_state, not per-decision",
        "session_id": "#480 engine bookkeeping",
        "type": "#480 literal event-type discriminator",
    },
    "rule_breach_decisions": {
        "event": "#480 literal event-type discriminator",
        "event_id": "#480 content-addressed identity hash",
        "schema_version": "#480 versioning metadata",
        "session_id": "#480 engine bookkeeping",
        "review_date": "#480 per-event timestamp; the card's as-of date comes from engine_state, not per-decision",
        "problem_key": "#480 engine join key to the problem-tracking row this breach came from",
        "breach_week": "#480 which week the breach occurred; not reflected back to the user anywhere",
        "evidence": "#480 the specific breach occurrences backing the question; unread, the closest analogue here to evidence_delta",
        "recent_count": "#480 decision-time snapshot; the card's trend line reads live problem_stats instead",
        "recent_amount": "#480 decision-time snapshot; the card's trend line reads live problem_stats instead",
        "trend": "#480 decision-time snapshot; the card's trend line reads live problem_stats instead",
    },
}

# Not answer surfaces: these are what the agent would send the engine, graded by
# `condition_integrity` / `condition_check_integrity` against the engine's own
# validators. An answer carrying only these and nothing the user reads is still
# an empty answer. `previous_check` is the episode's stated history — the
# reference `build_check` classifies "new information" against.
ANSWER_PAYLOAD_KEYS = ("condition", "condition_check", "previous_check")


# ─────────────────────────── episode loading ────────────────────────────────

def validate_episode(raw, rel):
    """Structural validation, fail-closed. A typo must not silently skip work.

    ``evals/episodes/episode.schema.json`` is the readable contract; this is the
    enforcement, because the offline suite carries no jsonschema dependency.
    """
    problems = []

    def require(condition, message):
        if not condition:
            problems.append(f"{rel}: {message}")

    require(isinstance(raw, dict), "episode must be a JSON object")
    if problems:
        return problems
    unknown = set(raw) - {"id", "title", "source", "moment", "fixture", "question",
                          "blocked_on", "judge",
                          "checks", "must_disclose", "answers"}
    require(not unknown, f"unknown top-level field(s): {sorted(unknown)}")
    for field in ("id", "title", "moment"):
        require(isinstance(raw.get(field), str) and raw.get(field), f"{field} must be a non-empty string")
    source = raw.get("source") or {}
    require(isinstance(source, dict), "source must be an object")
    require(isinstance(source.get("refs"), list) and source.get("refs"),
            "source.refs must list the issue(s) or run this episode was converted from")
    require(isinstance(source.get("date"), str) and source.get("date"),
            "source.date must record when the miss was observed")

    fixture = raw.get("fixture") or {}
    require(isinstance(fixture, dict), "fixture must be an object")
    trades = fixture.get("trades")
    require(isinstance(trades, str) and trades.startswith("skills/fomo-kernel/mock/"),
            "fixture.trades must be a synthetic CSV under skills/fomo-kernel/mock/")
    if isinstance(trades, str) and not (REPO / trades).is_file():
        problems.append(f"{rel}: fixture.trades does not exist: {trades}")
    require(fixture.get("route") in {"first_review", "weekly_review"},
            "fixture.route must be first_review or weekly_review")
    require(isinstance(fixture.get("locale"), str) and (COPY_DIR / f"{fixture.get('locale')}.json").is_file(),
            "fixture.locale must name a shipped copy catalog")

    question = raw.get("question") or {}
    require(isinstance(question, dict), "question must be an object")
    require(question.get("asked_by") in {"engine", "user"}, "question.asked_by must be engine or user")
    require(isinstance(question.get("kind"), str) and question.get("kind"),
            "question.kind must be an engine question kind, commitment_choice, or free_form")
    require(isinstance(question.get("text"), str) and question.get("text"),
            "question.text must record what the user was actually asked")

    # An episode with no checks is the honest `unmapped` state, not a mistake:
    # the miss is recorded and replayed, and nothing today can grade it. It has
    # to name what it waits on, so it cannot become a silent parking space.
    checks = raw.get("checks")
    require(isinstance(checks, list), "checks must be a list")
    blocked_on = raw.get("blocked_on")
    if isinstance(checks, list) and not checks:
        require(isinstance(blocked_on, str) and blocked_on,
                "an episode with no checks must name what it is blocked_on (an issue reference)")
    elif blocked_on:
        problems.append(f"{rel}: blocked_on is set on an episode that declares checks — an "
                        "episode is either graded or waiting, not both")
    if isinstance(checks, list):
        bad = [name for name in checks if name not in CHECK_NAMES]
        require(not bad, f"unknown check(s): {bad}")
    require(isinstance(raw.get("must_disclose", []), list), "must_disclose must be a list")
    if raw.get("must_disclose") and "honesty_coverage" not in (checks or []):
        problems.append(f"{rel}: must_disclose is set but honesty_coverage is not in checks")
    if "honesty_coverage" in (checks or []) and not raw.get("must_disclose"):
        problems.append(f"{rel}: honesty_coverage is declared but must_disclose is empty — the "
                        "check would have no key to look for, and an abstention is not a pass")

    # The rubric judge's declarations. Validated here, graded nowhere in this
    # file: the offline suite must reject a typo for free, and it must not be
    # able to report a non-deterministic verdict as a deterministic result.
    judge = raw.get("judge")
    judge_axes = []
    if judge is not None:
        if not isinstance(judge, dict):
            problems.append(f"{rel}: judge must be an object")
        else:
            unknown = set(judge) - {"axes", "declared_by"}
            require(not unknown, f"judge has unknown field(s): {sorted(unknown)}")
            axes = judge.get("axes")
            if not (isinstance(axes, list) and axes):
                problems.append(f"{rel}: judge.axes must name at least one axis — an "
                                "empty judge block is the absence of one")
            else:
                bad = [name for name in axes if name not in JUDGE_AXES]
                require(not bad, f"unknown judge axis/axes: {bad}")
                if len(set(axes)) != len(axes):
                    problems.append(f"{rel}: judge.axes repeats an axis")
                judge_axes = [name for name in axes if name in JUDGE_AXES]
            # Who declared these expectations decides what a green judge run may
            # be called. An agent-declared block is a hypothesis the judge agrees
            # with; only an owner-ratified one is calibration.
            require(judge.get("declared_by") in {"agent", "owner"},
                    "judge.declared_by must be agent or owner")

    # One answer is enough: the recorded miss is the asset. A repaired answer is
    # a witness that the invariants can be satisfied, not a required target —
    # pinning "what good looks like" is what this bank got wrong the first time.
    answers = raw.get("answers")
    require(isinstance(answers, list) and answers, "answers must hold at least the recorded miss")
    if not isinstance(answers, list):
        return problems
    seen_ids, failing = set(), 0
    for index, answer in enumerate(answers):
        tag = f"{rel}: answers[{index}]"
        if not isinstance(answer, dict):
            problems.append(f"{tag} must be an object")
            continue
        unknown = set(answer) - {"id", "expect", "fails", "note", "judge_fails",
                                 *ANSWER_PART_KEYS, *ANSWER_PAYLOAD_KEYS}
        if unknown:
            problems.append(f"{tag} unknown field(s): {sorted(unknown)}")
        if not (isinstance(answer.get("id"), str) and answer.get("id")):
            problems.append(f"{tag} needs a non-empty id")
        elif answer["id"] in seen_ids:
            problems.append(f"{tag} duplicate answer id {answer['id']!r}")
        else:
            seen_ids.add(answer["id"])
        if answer.get("expect") not in {"pass", "fail"}:
            problems.append(f"{tag} expect must be pass or fail")
        if not any(answer.get(key) for key in ANSWER_PART_KEYS):
            problems.append(f"{tag} carries no answer surface ({', '.join(ANSWER_PART_KEYS)})")
        if not checks and answer.get("fails"):
            problems.append(f"{tag} names fails on an ungraded episode — `expect` records the "
                            "intent for when a gate exists; there is no check to name yet")
        elif answer.get("expect") == "fail" and checks:
            failing += 1
            fails = answer.get("fails")
            if not (isinstance(fails, list) and fails):
                problems.append(f"{tag} expects failure but does not name the check(s) it must fail")
            else:
                outside = [name for name in fails if name not in (checks or [])]
                if outside:
                    problems.append(f"{tag} fails names check(s) the episode does not declare: {outside}")
        elif answer.get("fails"):
            problems.append(f"{tag} expects a pass but names fails")
        for option in answer.get("presented_options") or []:
            if not isinstance(option, dict) or not option.get("maps_to"):
                problems.append(f"{tag} every presented option needs maps_to")
        if not isinstance(answer.get("discloses", {}), dict):
            problems.append(f"{tag} discloses must be an object of key -> sentence")
        judge_fails = answer.get("judge_fails")
        if judge_fails is not None:
            if not judge_axes:
                problems.append(f"{tag} names judge_fails on an episode that declares no "
                                "judge axes — there is no axis to fail yet")
            elif not (isinstance(judge_fails, list) and judge_fails):
                problems.append(f"{tag} judge_fails must name at least one axis, or be "
                                "absent (absent means: expected to pass every declared axis)")
            else:
                outside = [name for name in judge_fails if name not in judge_axes]
                if outside:
                    problems.append(f"{tag} judge_fails names axis/axes the episode does "
                                    f"not declare: {outside}")
    # The judge's first interlock, enforced offline so it cannot be discovered
    # only by someone who paid for a run: an axis no answer is expected to fail
    # cannot show that the judge still looks at it.
    for axis in judge_axes:
        if not any(isinstance(answer, dict) and axis in (answer.get("judge_fails") or [])
                   for answer in answers):
            problems.append(f"{rel}: judge axis {axis!r} is declared but no answer is "
                            "expected to fail it — the episode cannot prove the judge "
                            "still discriminates on that axis")
    if not failing and checks:
        problems.append(f"{rel}: no answer expects failure — an episode whose every answer "
                        "passes cannot prove its checks still look at anything")
    return problems


def load_bank(selected=None):
    """Return ``(episodes, problems)`` for every ``EP-*.json`` in the bank."""
    episodes, problems = [], []
    paths = sorted(EPISODE_DIR.glob("EP-*.json"))
    if not paths:
        return [], [f"{EPISODE_DIR.relative_to(REPO)}: no episodes found"]
    for path in paths:
        rel = path.relative_to(REPO)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{rel}: invalid JSON: {exc}")
            continue
        found = validate_episode(raw, rel)
        if found:
            problems.extend(found)
            continue
        raw["_path"] = rel
        episodes.append(raw)
    # Duplicate ids are checked across every valid file, before any selection:
    # a collision one id-filter away from being visible is still a collision.
    ids = [episode["id"] for episode in episodes]
    for duplicate in sorted({name for name in ids if ids.count(name) > 1}):
        problems.append(f"duplicate episode id across files: {duplicate}")
    if selected:
        for name in sorted(selected - set(ids)):
            problems.append(f"no episode with id {name}")
        episodes = [episode for episode in episodes if episode["id"] in selected]
    return episodes, problems


# ────────────────────────────── engine facts ─────────────────────────────────

def _seed_condition_state(episode, root, workdir):
    """Give the fixture the standing condition an episode's own answers describe.

    A ``condition_crossing`` question exists only when the user already
    committed to a condition and this period's lookup came back — state no mock
    CSV can produce, because a condition is something a person writes. Rather
    than adding a second place to declare it, the seed is taken from the
    episode's own answers: they are minimal pairs, so the condition and the
    lookup are the same across them, and the one the answers are *about* is
    exactly the one the engine should be asked to queue a question for.

    Returns the ``--condition-checks`` argv, or ``[]`` when the episode is not
    about a per-period check.
    """
    source = next((answer for answer in episode["answers"]
                   if isinstance(answer.get("condition_check"), dict)
                   and isinstance(answer.get("condition"), dict)), None)
    if source is None:
        return []
    slot = conditions.build_slot(source["condition"], slot_id="slot-episode-0",
                                 created="2026-07-01", session_id="episode")
    root.mkdir(parents=True, exist_ok=True)
    (root / "conditions.jsonl").write_text(
        json.dumps(slot, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    path = workdir / "condition-checks.json"
    path.write_text(json.dumps({"condition_checks": [
        {"slot_id": slot["slot_id"], "check": source["condition_check"]}]}), encoding="utf-8")
    return ["--condition-checks", str(path)]


def prepare_fixture(episode, workdir):
    """Run the real engine offline over the episode's synthetic CSV.

    Same offline contract as ``tests/persona_sweep.py``: a PYTHONPATH-injected
    yfinance ImportError stub, so an open-position persona degrades
    deterministically instead of reaching the network, and an isolated
    ``TRADE_COACH_HOME`` so no episode can see production coach state.
    """
    fixture = episode["fixture"]
    root = workdir / "root"
    stubs = workdir / "stubs"
    stubs.mkdir(parents=True, exist_ok=True)
    (stubs / "yfinance.py").write_text('raise ImportError("offline stub")\n', encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(stubs), env.get("PYTHONPATH")) if part)
    env["TRADE_COACH_HOME"] = str(root)
    try:
        condition_argv = _seed_condition_state(episode, root, workdir)
    except conditions.ConditionError as exc:
        return None, f"the engine refuses this episode's standing condition: {exc}"
    relative = pathlib.Path(fixture["trades"]).relative_to("skills/fomo-kernel")
    done = subprocess.run(
        [sys.executable, "engine/review.py", "prepare", str(relative),
         "--language", fixture["locale"], "--route", fixture["route"], *condition_argv],
        cwd=SKILL_DIR, capture_output=True, text=True, env=env)
    plans = list(root.glob(".pending/*/plan.json"))
    if done.returncode != 0 or len(plans) != 1:
        tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or [""]
        return None, f"prepare failed (rc={done.returncode}, pending={len(plans)}): {tail[0]}"
    return json.loads(plans[0].read_text(encoding="utf-8")), None


def _leaves(node, key=None):
    """Yield ``(key, value)`` for every scalar leaf, dropping noise subtrees."""
    if isinstance(node, dict):
        for child_key, child in node.items():
            if child_key in NOISE_KEYS:
                continue
            yield from _leaves(child, child_key)
    elif isinstance(node, list):
        for child in node:
            yield from _leaves(child, key)
    else:
        yield key, node


def _is_opaque(text):
    return "/" in text or "\\" in text or bool(HEX_TOKEN.match(text))


def engine_facts(plan, episode):
    """Everything the checks are allowed to treat as engine-authored truth."""
    locale = episode["fixture"]["locale"]
    numbers, strings = set(), []
    for _key, value in _leaves(plan):
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            numbers.add(float(value))
        elif isinstance(value, str) and not _is_opaque(value):
            strings.append(value)
            for match in NUMBER.finditer(value):
                numbers.add(float(match.group(0).replace(",", "")))

    copy_locale = json.loads((COPY_DIR / f"{locale}.json").read_text(encoding="utf-8"))
    copy_en = json.loads((COPY_DIR / "en.json").read_text(encoding="utf-8"))
    fixture_text = (REPO / episode["fixture"]["trades"]).read_text(encoding="utf-8")

    # Traceable tokens: the synthetic ledger's own symbols, plus every all-caps
    # token the engine or its copy catalog already uses (USD, ETF, benchmark
    # names). Anything else on an answer surface came from outside the fixture,
    # which is exactly the #274 leak shape.
    tokens = set(UPPER_TOKEN.findall(fixture_text))
    for text in strings:
        tokens.update(UPPER_TOKEN.findall(text))
    for catalog in (copy_locale, copy_en):
        for _key, value in _leaves(catalog):
            if isinstance(value, str):
                tokens.update(UPPER_TOKEN.findall(value))

    # Engine identifiers that must never reach a user-facing surface: stable
    # snake_case codes (docs/language-policy.md) drawn from the plan itself, so
    # a new question kind or choice value is covered without editing this file.
    identifiers = {key for key in copy_en.get("dimensions", {})}
    identifiers.update(rule.get("id") for rule in
                       ((plan.get("card_plan") or {}).get("candidate_rules") or []))
    for question in plan.get("question_queue") or []:
        identifiers.add(question.get("id"))
        identifiers.add(question.get("kind"))
        for option in question.get("options") or []:
            identifiers.add(option.get("value"))
        contract = ((question.get("question_opportunity") or {}).get("answer_contract") or {})
        identifiers.update(contract.get("canonical_choices") or [])
    identifiers = {name for name in identifiers
                   if isinstance(name, str) and SNAKE_CASE.match(name)}

    # #262's mixed-language leak: an English metric label on a localized
    # surface. Only labels this locale actually translates count — zh-TW's own
    # "部位 sizing" keeps an English word on purpose.
    local_labels = {str(value) for value in copy_locale.get("dimensions", {}).values()}
    foreign_labels = {str(value) for value in copy_en.get("dimensions", {}).values()
                      if str(value) not in local_labels} if locale != "en" else set()

    return {
        "locale": locale,
        "engine_text": "\n".join(strings),
        "numbers": numbers,
        "dates": {match for text in strings for match in DATE.findall(text)},
        "tokens": tokens,
        "identifiers": identifiers,
        "foreign_labels": foreign_labels,
        "candidates": {rule.get("id"): rule for rule in
                       ((plan.get("card_plan") or {}).get("candidate_rules") or [])},
        "honesty_keys": set((plan.get("card_plan") or {}).get("required_honesty_keys") or []),
        "question_kinds": {question.get("kind") for question in (plan.get("question_queue") or [])},
    }


# ──────────────────────────────── the checks ─────────────────────────────────

def _surfaces(answer):
    """Every string the user would have read, tagged with where it sat."""
    out = [("prose", answer.get("prose") or "")]
    for index, option in enumerate(answer.get("presented_options") or []):
        out.append((f"option[{index}].label", option.get("label") or ""))
        out.append((f"option[{index}].description", option.get("description") or ""))
    for key, sentence in sorted((answer.get("discloses") or {}).items()):
        out.append((f"discloses[{key}]", sentence or ""))
    return [(role, text) for role, text in out if text]


def _number_matches(value, allowed):
    """True when ``value`` is an engine number, or that number rounded."""
    for candidate in allowed:
        if abs(candidate - value) <= 1e-9:
            return True
        for places in (0, 1, 2):
            if abs(round(candidate, places) - value) <= 1e-9:
                return True
    return False


def check_number_provenance(answer, facts):
    """Never-loosen rule 1, applied to an answer surface instead of a card.

    #414 will gate free-form answers in the product; this is the same question
    asked of a recorded answer: could the engine have produced this figure?
    """
    findings = []
    for role, text in _surfaces(answer):
        remainder = text
        for date in DATE.findall(text):
            if date not in facts["dates"]:
                findings.append(f"{role}: date {date} appears in no engine artifact")
            remainder = remainder.replace(date, " ")
        for match in NUMBER.finditer(remainder):
            raw = match.group(0)
            value = float(raw.replace(",", ""))
            if not _number_matches(value, facts["numbers"]):
                findings.append(f"{role}: {raw} traces to no engine number — "
                                "the answer derived or invented it")
    return findings


def check_honesty_coverage(episode, answer, facts):
    """Coverage, not adequacy: a triggered limitation must be said out loud.

    Scope is the episode's ``must_disclose``, verified to still be a subset of
    what the fixture triggers — a card carries the engine's own equality gate in
    ``_draft_bundle``, but an answer to one question legitimately touches only
    part of the ledger. Whether the sentence is *good* is judge work; that it
    exists, is digit-free, and is not copy-pasted across keys is mechanical.
    """
    findings = []
    scope = set(episode.get("must_disclose") or [])
    stale = sorted(scope - facts["honesty_keys"])
    if stale:
        findings.append(f"episode puts {stale} in scope but the fixture no longer triggers "
                        "them — the episode or the engine moved, and this can no longer "
                        "grade what it was written to grade")
    disclosed = answer.get("discloses") or {}
    for key in sorted(scope & facts["honesty_keys"]):
        sentence = (disclosed.get(key) or "").strip()
        if not sentence:
            findings.append(f"discloses[{key}]: triggered by the fixture, disclosed nowhere "
                            "in the answer")
        elif numeric_claim(sentence):
            findings.append(f"discloses[{key}]: makes a numeric claim; a disclosure carries "
                            "the limitation, the engine carries the magnitude")
    for key in sorted(set(disclosed) - facts["honesty_keys"]):
        findings.append(f"discloses[{key}]: this fixture does not trigger that limitation")
    seen = {}
    for key, sentence in sorted(disclosed.items()):
        sentence = (sentence or "").strip()
        if sentence and sentence in seen:
            findings.append(f"discloses[{key}]: byte-identical to discloses[{seen[sentence]}] — "
                            "one sentence cannot disclose two different limitations")
        seen[sentence] = key
    return findings


def check_privacy_trace(answer, facts):
    """#274's text channel, inverted into a positive test.

    The public repository can never hold real trade data, so an episode's every
    ticker-shaped token must trace to the synthetic fixture or the engine's own
    artifacts. A back-converted real-use miss that kept a real symbol fails
    here, which is what "keep only the failure structure" means mechanically.
    """
    findings = []
    for role, text in _surfaces(answer):
        for match in POSITION_ID.finditer(text):
            findings.append(f"{role}: internal position-id format on a user-facing surface")
        for match in UPPER_TOKEN.finditer(text):
            token = match.group(0)
            if token not in facts["tokens"]:
                findings.append(f"{role}: {token} traces to neither the synthetic fixture nor "
                                "the engine artifacts")
    return findings


def check_surface_hygiene(answer, facts):
    """#262, first half: internal identifiers are not user-facing copy.

    Stable snake_case codes are the engine's vocabulary
    (docs/language-policy.md); the user reads the copy catalog's words.
    """
    findings = []
    for role, text in _surfaces(answer):
        for identifier in sorted(facts["identifiers"]):
            if identifier in text:
                findings.append(f"{role}: engine identifier {identifier!r} reached a "
                                "user-facing surface")
        internal = INTERNAL_KEYS.search(text)
        if internal:
            findings.append(f"{role}: internal field name {internal.group(0)!r} reached a "
                            "user-facing surface")
    return findings


def check_locale_purity(answer, facts):
    """#262, second half, plus #356's direction.

    ``en`` surface: no CJK, the rule ``persona_sweep`` already holds cards to.
    Localized surface: no English metric label this locale translates. There is
    deliberately no blanket Latin ban — a zh card legitimately carries tickers,
    currency codes, and benchmark names.
    """
    findings = []
    for role, text in _surfaces(answer):
        if facts["locale"] == "en":
            if CJK.search(text):
                findings.append(f"{role}: CJK on an en surface")
            continue
        for label in sorted(facts["foreign_labels"]):
            if label.lower() in text.lower():
                findings.append(f"{role}: untranslated metric label {label!r} on a "
                                f"{facts['locale']} surface")
    return findings


SENTENCE = re.compile(r"[^.。!！?？\n]+")


def unmapped_claims(answer, facts):
    """What the mechanical half did not grade — reported, never inferred.

    #412's enum-gated-surface standard, turned on this harness: an enum for what
    is mechanically decidable, `unmapped` as a first-class honest state for what
    is not, and never a silent drop. A sentence carrying no number, quoting no
    engine span, and disclosing no honesty key has passed the hygiene checks and
    nothing else; its substance waits on the rubric judge, and on #414 for the
    product-side provenance gate. Never a failure — the over-trust would be
    printing nothing and letting a green run read as "the answer is good".
    """
    out = []
    for role, text in _surfaces(answer):
        if role.startswith("discloses["):
            continue
        for match in SENTENCE.finditer(text):
            sentence = match.group(0).strip()
            if len(sentence) < 12 or NUMBER.search(sentence):
                continue
            if sentence in facts["engine_text"]:
                continue
            out.append((role, sentence))
    return out


def check_condition_integrity(answer, facts):
    """#412: the condition a user reaches for that the engine cannot compute.

    Three mechanical questions, all derived from the engine rather than restated
    here:

    1. **Does the engine accept this envelope?** ``conditions.build_slot`` is the
       gate that ships, so a query with the threshold folded into it ("did growth
       fall below 30%?") fails here for the same reason it fails in a real
       review. A yes/no lookup returns the nearest matching real event attached
       to the timeframe the question supplied — a real source, a real number, and
       a wrong date, indistinguishable from a correct answer at the point of use.
    2. **Did the user's own words reach the user?** The criterion is stored and
       displayed verbatim (#396); a paraphrase means the record and the screen
       have quietly diverged.
    3. **Does the prose match what the engine could actually see?** A
       ``researched`` slot must show the baseline back — "it is 38% right now" is
       a fact the user reacts to instantly, where "I will compare the latest
       quarter against the year-ago quarter" is a method they have to simulate.
       An ``unmapped`` slot must carry no figure beyond the user's own criterion:
       nothing was found, so any number in the answer was invented.

    Deliberately **not** paired with ``number_provenance`` on the same episode: a
    researched figure legitimately comes from outside the engine, which is the
    whole point of the tier. Its discipline is the source + as-of anchor, the
    same one #414's ``public_fact`` tag will require of a free-form answer.

    Reconciled against #431's recut, which arrived in parallel with this check
    and removed ``grounding_fidelity`` for being a behavior oracle wearing an
    invariant's clothes: none of the three questions above compares an answer
    against wording the product happens to ship. The first is the engine's own
    gate. The second demands that the *user's* words survive, which is #396's
    invariant and says nothing about the product's. The third bounds which
    figures may appear, not how any of them is phrased. An answer that passes is
    a witness that those hold — never a model answer to copy.
    """
    envelope = answer.get("condition")
    try:
        slot = conditions.build_slot(envelope, slot_id="episode-slot",
                                     created="2026-01-01")
    except conditions.ConditionError as exc:
        return [f"condition: the engine refuses this envelope — {exc}"]
    findings = []
    surfaces = _surfaces(answer)
    if not any(slot["criterion"] in text for _role, text in surfaces):
        findings.append(f"condition: the criterion reached no surface verbatim "
                        f"(stored: {slot['criterion']!r})")
    # Both sides are scanned with the engine's own number reader. Mixing this
    # module's NUMBER (which sees the 3 in "Q3") with `conditions.numbers_in`
    # (which does not) made the check red an honest answer that quoted the user's
    # criterion verbatim — the very thing it demands three lines above.
    stated = set()
    for _role, text in surfaces:
        stated |= conditions.numbers_in(DATE.sub(" ", text))
    # A researched figure comes from outside the engine, so it cannot be traced
    # the way `number_provenance` traces one. What it can be traced to is this
    # slot's own record: the user's criterion, the line, and what was found.
    allowed = conditions.numbers_in(slot["criterion"])
    if slot.get("threshold"):
        allowed.add(abs(float(slot["threshold"]["value"])))
    baseline = (slot.get("baseline") or {}).get("value")
    if baseline is not None:
        allowed.add(float(baseline))
    unsourced = sorted(value for value in stated if not _number_matches(value, allowed))
    if unsourced:
        findings.append(f"condition: the answer states {unsourced}, which is neither the user's "
                        "own line nor anything the lookup returned — an unsourced figure in a "
                        "coaching answer reads exactly like a checked one")
    if slot["tier"] == "researched":
        if not _number_matches(float(baseline), stated):
            findings.append(f"condition: the baseline {baseline} was looked up but never shown "
                            "back — a basis the user cannot see is one they cannot correct")
    return findings


# What a `lookup_status: failed` answer must actually say. An allow-set rather
# than a required phrase, for the same reason `honesty_coverage` is coverage and
# not adequacy: the wording will keep changing (#395/#396), and a check that
# only the current template satisfies is a behavior oracle. What cannot change
# is that *some* word for "not checked" appears — its absence is the failure.
BLIND_MARKERS = ("not check", "could not", "couldn't", "cannot", "can't", "unable",
                 "no figure", "nothing", "blind", "unavailable", "did not find",
                 "didn't find", "failed", "no reading", "not available",
                 "沒查到", "查不到", "找不到", "無法", "盯不到", "沒有數字")
# Phrases a failed lookup must never be narrated with. Deliberately tiny and
# literal — this half is a floor, not a style gate.
CHECKED_AND_FINE = ("still clear", "still above", "still below", "no change", "unchanged",
                    "on track", "within range", "nothing to worry", "all good", "fine")


def check_condition_check_integrity(answer, facts):
    """#412's second half: the per-period check, graded against the engine.

    ``condition_integrity`` settles the *commitment* moment — the criterion, the
    query, the tier. This settles the moment after it, which fails differently:
    the condition is already stored and watchable, and what can go wrong is the
    reading. Three mechanical questions, every one derived from the engine
    rather than restated here:

    1. **Does the engine accept this result against this condition?** The slot
       is built with ``build_slot`` and the check with ``build_check``, the two
       gates that actually ship. A summary sent for a numeric line, a verdict
       asserted on the envelope, an answer attached to a lookup that failed —
       all refused here for the same reason they are refused in a live review.
    2. **Does every number in the prose trace to the record?** The observation,
       the threshold, and the baseline are what the answer may state. Both sides
       are read with ``conditions.numbers_in`` — the engine's own number reader,
       the same one on both sides — so a fiscal-quarter label is not mistaken
       for a quantity and a criterion quoted verbatim never reddens the check
       that demands it (the mistake ``condition_integrity`` made first).
    3. **Is a failed lookup spoken as one?** A check that came back with nothing
       and is narrated as checked-and-fine is the exact failure the whole tier
       exists to prevent: the user walks away believing a tripwire is armed. The
       positive half is an allow-set, never a required sentence.

    Deliberately not paired with ``number_provenance``, for the same reason its
    sibling is not: a researched figure legitimately comes from outside the
    engine. Its discipline is the source + as-of anchor, and here also that the
    figure the prose states is the figure the envelope carried.
    """
    envelope = answer.get("condition_check")
    slot_input = answer.get("condition")
    if not isinstance(envelope, dict):
        return ["condition check: the answer carries no check envelope to grade"]
    try:
        slot = conditions.build_slot(slot_input, slot_id="episode-slot", created="2026-01-01")
    except conditions.ConditionError as exc:
        return [f"condition check: the engine refuses the condition this check is for — {exc}"]
    try:
        check = conditions.build_check(envelope, slot=slot, previous=answer.get("previous_check"),
                                       check_id="episode-check", date_end="2026-08-26")
    except conditions.ConditionError as exc:
        return [f"condition check: the engine refuses this result — {exc}"]

    findings = []
    surfaces = _surfaces(answer)
    stated = set()
    for _role, text in surfaces:
        stated |= conditions.numbers_in(DATE.sub(" ", text))
    allowed = conditions.numbers_in(slot["criterion"])
    if slot.get("threshold"):
        allowed.add(abs(float(slot["threshold"]["value"])))
    for source in ((slot.get("baseline") or {}), (check.get("observation") or {})):
        if source.get("value") is not None:
            allowed.add(float(source["value"]))
    unsourced = sorted(value for value in stated if not _number_matches(value, allowed))
    if unsourced:
        findings.append(f"condition check: the answer states {unsourced}, which is neither the "
                        "user's own line nor anything this period's lookup returned — an "
                        "unsourced figure in a coaching answer reads exactly like a checked one")
    if check["lookup_status"] == "ok" and "value" in (check.get("observation") or {}):
        if not _number_matches(float(check["observation"]["value"]), stated):
            findings.append(f"condition check: this period's reading "
                            f"{check['observation']['value']} was looked up but never shown back "
                            "— a basis the user cannot see is one they cannot correct")
    if check["lookup_status"] == "failed":
        prose = " ".join(text for _role, text in surfaces).casefold()
        if not any(marker in prose for marker in BLIND_MARKERS):
            findings.append("condition check: the lookup failed and no surface says so — an "
                            "unchecked condition presented without that word is how a user comes "
                            "to believe a tripwire is armed")
        reassuring = sorted(phrase for phrase in CHECKED_AND_FINE if phrase in prose)
        if reassuring:
            findings.append(f"condition check: the lookup failed and the answer says {reassuring} "
                            "— that is the verdict of a check that succeeded, told about one "
                            "that did not")
    return findings


def run_check(name, episode, answer, facts):
    """Return ``(findings, looked_at_something)``.

    The second value is interlock 2: a check with nothing in front of it has not
    passed, it has abstained, and an abstention on a declared check is a
    failure. Otherwise an answer could satisfy an option-level or envelope-level
    check by carrying neither.
    """
    if name == "number_provenance":
        surfaces = _surfaces(answer)
        return check_number_provenance(answer, facts), bool(surfaces)
    if name == "honesty_coverage":
        scope = set(episode.get("must_disclose") or [])
        return check_honesty_coverage(episode, answer, facts), bool(scope)
    if name == "privacy_trace":
        return check_privacy_trace(answer, facts), bool(_surfaces(answer))
    if name == "surface_hygiene":
        return check_surface_hygiene(answer, facts), bool(_surfaces(answer))
    if name == "locale_purity":
        return check_locale_purity(answer, facts), bool(_surfaces(answer))
    if name == "condition_integrity":
        return check_condition_integrity(answer, facts), answer.get("condition") is not None
    if name == "condition_check_integrity":
        return (check_condition_check_integrity(answer, facts),
                answer.get("condition_check") is not None)
    raise AssertionError(f"unknown check {name}")


# ──────────────────────────────── the runner ─────────────────────────────────

def replay(episode, facts):
    """Grade every answer in one episode. Returns ``(failures, observed)``.

    ``observed`` maps check -> set of outcomes seen ("pass"/"fail"), which the
    bank-level coverage report consumes.
    """
    failures, unmapped = [], []
    observed = {name: set() for name in episode["checks"]}
    tag = episode["id"]

    question = episode["question"]
    if question["asked_by"] == "engine":
        if question["kind"] == "commitment_choice":
            if not facts["candidates"]:
                failures.append(f"{tag}: the fixture emits no candidate rule, so the "
                                "commitment question this episode replays no longer exists")
        elif question["kind"] not in facts["question_kinds"]:
            failures.append(f"{tag}: the fixture no longer queues a {question['kind']!r} "
                            f"question (queued: {sorted(facts['question_kinds'])})")

    for answer in episode["answers"]:
        ungraded = unmapped_claims(answer, facts)
        if ungraded:
            role, sentence = ungraded[0]
            unmapped.append(f"unmapped: {tag}/{answer['id']} — {len(ungraded)} sentence(s) "
                            f"graded on hygiene only, substance waits on the judge "
                            f"(first, {role}: {sentence[:60]!r})")
        found = {}
        for name in episode["checks"]:
            findings, looked = run_check(name, episode, answer, facts)
            if not looked:
                failures.append(f"{tag}/{answer['id']}: declared check {name!r} had nothing "
                                "to inspect — a check that abstains is not a check that passed")
                continue
            observed[name].add("fail" if findings else "pass")
            if findings:
                found[name] = findings
        verdict = "fail" if found else "pass"
        if verdict != answer["expect"]:
            detail = ("; ".join(f"{name}: {message}" for name, messages in sorted(found.items())
                                for message in messages) or "no findings")
            failures.append(f"{tag}/{answer['id']}: expected {answer['expect']}, got "
                            f"{verdict} — {detail}")
            continue
        if verdict == "fail":
            expected = set(answer["fails"])
            actual = set(found)
            if actual != expected:
                detail = "; ".join(f"{name}: {message}" for name, messages in sorted(found.items())
                                   for message in messages)
                failures.append(f"{tag}/{answer['id']}: failed on {sorted(actual)} but the "
                                f"episode records {sorted(expected)} — {detail}")
    return failures, observed, unmapped


def question_consumers(plan):
    """#429: every question kind must buy something, and prove it.

    ``(failures, notes)``. Two proof modes, both read from real artifacts rather
    than from prose: ``card`` means the sink key is referenced by the renderer
    that delivers the card, ``state:<key>`` means that key exists in a prepared
    plan's ``state_snapshot``, which is what the next review reads.

    The kind list comes from the Review Plan schema's own enum, so this gate
    cannot be satisfied by forgetting to mention a kind.

    What it does not prove: that the sentence the sink reaches is worth reading.
    A card line nobody acts on still counts as reached here — that half is the
    rubric judge's, calibrated against owner ratings.
    """
    failures, notes = [], []
    schema = json.loads((SKILL_DIR / "schemas" / "review-plan.schema.json")
                        .read_text(encoding="utf-8"))
    try:
        kinds = set(schema["properties"]["question_queue"]["items"]["properties"]["kind"]["enum"])
    except (KeyError, TypeError):
        return (["question consumers: the Review Plan schema no longer exposes a question "
                 "`kind` enum — this gate cannot derive what to check"], notes)

    renderer_source = (SKILL_DIR / "engine" / "card_renderer.py").read_text(encoding="utf-8")
    state_keys = set((plan.get("state_snapshot") or {}).keys())

    for kind in sorted(kinds - set(QUESTION_CONSUMERS)):
        failures.append(f"question consumers: {kind!r} is a question the engine can ask, with no "
                        "declared consumer — declare where its answer goes, or the user is "
                        "answering into a void (#429)")
    for kind in sorted(set(QUESTION_CONSUMERS) - kinds):
        failures.append(f"question consumers: {kind!r} is declared here but the schema no longer "
                        "lists it as a question kind — remove the declaration")

    for kind in sorted(kinds & set(QUESTION_CONSUMERS)):
        sink, reaches = QUESTION_CONSUMERS[kind]
        verified = []
        for claim in reaches:
            if claim == "card":
                if sink in renderer_source:
                    verified.append("card")
                else:
                    failures.append(f"question consumers: {kind!r} claims its answer reaches the "
                                    f"card, but {sink!r} appears nowhere in card_renderer.py")
            elif claim.startswith("state:"):
                key = claim.split(":", 1)[1]
                if key in state_keys:
                    verified.append(claim)
                else:
                    failures.append(f"question consumers: {kind!r} claims its answer reaches the "
                                    f"next review through state_snapshot.{key}, which this plan "
                                    "does not carry")
            else:
                failures.append(f"question consumers: {kind!r} declares an unknown proof mode "
                                f"{claim!r}")
        if verified:
            if kind in KNOWN_UNWIRED:
                failures.append(f"question consumers: {kind!r} is now wired ({', '.join(verified)}) "
                                f"but still sits in KNOWN_UNWIRED — remove it and close "
                                f"{KNOWN_UNWIRED[kind]}")
            continue
        # Nothing verified. Only the tracked ratchet keeps this green; there is
        # deliberately no `reflection_only` state (owner ruling, see above).
        if kind in KNOWN_UNWIRED:
            notes.append(f"unwired question: {kind!r} — the answer becomes {sink!r} and nothing "
                         f"reads it; tracked in {KNOWN_UNWIRED[kind]}")
        else:
            failures.append(f"question consumers: {kind!r} declares no reachable consumer and is "
                            "not a tracked defect — wire it, delete the question, or add it to "
                            "KNOWN_UNWIRED with an issue")
    return failures, notes


# ── Field-level scan (#451): parse the one idiom, not the field names ───────
#
# Both halves below read source with `ast`, the same "parse the idiom instead
# of hand-listing what it produces" move `tests/test_coach_data_cli.py` made
# for #452's registry check. Neither half executes the modules it reads —
# `thesis.py` and `review.py` are read as text and parsed, never imported —
# so this carries no new runtime dependency between the eval harness and the
# engine's import graph.

def _dict_literal_string_keys(node):
    """String keys of an ``ast.Dict`` literal; a non-string or computed key
    (none of the builders below use one) is silently not a field name."""
    if not isinstance(node, ast.Dict):
        return set()
    return {key.value for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)}


def _subscript_string_key(node):
    """The literal string key of an ``ast.Subscript`` slice. Python 3.9+ only
    (the slice is the key expression directly, no ``ast.Index`` wrapper) —
    consistent with this repo's existing AST scanners, which make the same
    assumption."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _event_fields_built(tree, func_name, var_name):
    """``(found, fields)``: every literal string key assigned into
    ``var_name`` anywhere inside module-level function ``func_name`` — the
    initial ``var_name = {...}`` dict literal, plus every later
    ``var_name["key"] = ...`` subscript assignment, conditional branches
    included. The union across branches is deliberate: a field a builder sets
    on only one path is still a field the persisted event may carry, so it
    still needs a reader or a declared reason it does not have one yet.

    ``found`` is False when no function of that name exists in the module, so
    a rename fails this check loudly instead of silently scanning nothing —
    the same "abstention is not a pass" interlock the answer checks already
    apply (``run_check``'s ``looked_at_something``)."""
    func_node = next((node for node in ast.walk(tree)
                      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and node.name == func_name), None)
    if func_node is None:
        return False, set()
    fields = set()
    for node in ast.walk(func_node):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id == var_name:
            fields |= _dict_literal_string_keys(node.value)
        elif (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
              and target.value.id == var_name):
            key = _subscript_string_key(target.slice)
            if key:
                fields.add(key)
    return True, fields


def _event_fields_read(tree, sink, bundle_param="bundle"):
    """``(found, fields)``: every literal string field read directly off the
    loop variable of a ``for x in <bundle_param>.get("<sink>") or []:`` loop,
    anywhere in the module. Deliberately shallow — a field read off a second
    variable derived from the loop variable (e.g. ``context = event.get(
    "context") or {}``) counts ``context`` as read without following it
    further, because the question this check answers is "does this top-level
    field of the persisted event have a reader", not "is every nested field
    of a reader field also read".

    ``found`` is False when no such loop exists anywhere in the file, so a
    renderer refactor that changes this shape fails loudly — with a message
    naming the shape assumption that broke — instead of silently reporting
    every field of that sink as unconsumed."""
    loops = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.For) and isinstance(node.target, ast.Name)):
            continue
        it = node.iter
        if isinstance(it, ast.BoolOp) and isinstance(it.op, ast.Or) and it.values:
            it = it.values[0]  # unwrap the `bundle.get(sink) or []` idiom
        if (isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute)
                and it.func.attr == "get" and isinstance(it.func.value, ast.Name)
                and it.func.value.id == bundle_param and it.args
                and isinstance(it.args[0], ast.Constant) and it.args[0].value == sink):
            loops.append(node)
    if not loops:
        return False, set()
    fields = set()
    for loop in loops:
        loop_var = loop.target.id
        for node in ast.walk(loop):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == loop_var and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                fields.add(node.args[0].value)
            elif (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                  and node.value.id == loop_var):
                key = _subscript_string_key(node.slice)
                if key:
                    fields.add(key)
    return True, fields


def question_field_consumers():
    """#451: deepen the "card" claim from "the sink's name appears somewhere
    in card_renderer.py" to "this specific field of the sink is read by the
    loop that consumes it". See the section comment above QUESTION_FIELD_
    SOURCES for exactly which sinks this covers and why the rest are not
    attempted. ``(failures, notes)``, same shape as ``question_consumers``.

    Pure source analysis — no prepared plan or bundle needed, unlike
    ``question_consumers``'s state-snapshot half, so this runs unconditionally
    wherever it is called."""
    failures, notes = [], []
    renderer_path = SKILL_DIR / "engine" / "card_renderer.py"
    renderer_tree = ast.parse(renderer_path.read_text(encoding="utf-8"), filename=str(renderer_path))

    # Every sink some kind in QUESTION_CONSUMERS claims reaches "card",
    # deduplicated — revisit/due_revisit share one sink (revisit_resolutions)
    # and must not be scanned, or reported on, twice.
    card_sinks = sorted({sink for sink, reaches in QUESTION_CONSUMERS.values() if "card" in reaches})

    for sink in card_sinks:
        if sink not in QUESTION_FIELD_SOURCES:
            notes.append(f"field consumers: {sink!r} reaches the card but is outside this gate's "
                        "declared scope (see the section comment above QUESTION_FIELD_SOURCES) "
                        "— still checked only at the sink-name level")
            continue
        rel_path, func_name, var_name = QUESTION_FIELD_SOURCES[sink]
        builder_path = SKILL_DIR / rel_path
        builder_tree = (renderer_tree if builder_path == renderer_path
                        else ast.parse(builder_path.read_text(encoding="utf-8"), filename=str(builder_path)))
        found_builder, exists = _event_fields_built(builder_tree, func_name, var_name)
        if not found_builder:
            failures.append(f"field consumers: {sink!r} declares its fields are built by "
                            f"{func_name!r} in {rel_path}, which no longer exists — update "
                            "QUESTION_FIELD_SOURCES")
            continue
        if not exists:
            failures.append(f"field consumers: {func_name!r} in {rel_path} builds no literal "
                            f"field for {sink!r} — the dict-literal/subscript-assignment pattern "
                            "this scan relies on no longer matches; fix the scan, this is not "
                            "evidence the event carries zero fields")
            continue
        found_loop, consumed = _event_fields_read(renderer_tree, sink)
        if not found_loop:
            failures.append(f"field consumers: no `for x in bundle.get({sink!r}) or []:` loop "
                            "found in card_renderer.py — the shape this scan relies on no longer "
                            "matches; the check needs updating, not silencing")
            continue
        declared = KNOWN_UNWIRED_FIELDS.get(sink, {})
        for field in sorted(exists - consumed):
            if field in declared:
                notes.append(f"unwired field: {sink}.{field} — {declared[field]}")
                continue
            failures.append(f"field consumers: {sink}.{field} is constructed but not read by "
                            f"the card's loop over {sink!r} — wire it, or add it to "
                            "KNOWN_UNWIRED_FIELDS with an issue")
        for field in sorted(set(declared) & consumed):
            failures.append(f"field consumers: {sink}.{field} is now read but still sits in "
                            "KNOWN_UNWIRED_FIELDS — remove the stale declaration")
        for field in sorted(set(declared) - exists):
            failures.append(f"field consumers: {sink}.{field} is declared in "
                            "KNOWN_UNWIRED_FIELDS but the builder no longer constructs it — "
                            "remove the stale declaration")
    # A whole sink orphaned — no kind claims it reaches the card anymore, so
    # nothing above ever visited its declarations — is the same staleness one
    # level up. Checked separately because the loop above only ever looks at
    # `card_sinks`, and an entry that fell out of that set would otherwise
    # never be read again by this function.
    for sink in sorted(set(KNOWN_UNWIRED_FIELDS) - set(card_sinks)):
        failures.append(f"field consumers: {sink!r} has KNOWN_UNWIRED_FIELDS declarations but no "
                        "kind in QUESTION_CONSUMERS claims it reaches the card anymore — remove "
                        "the stale sink-level declaration")
    return failures, notes


def coverage_report(observed_total, declared):
    """Interlock 3: a check nobody exercises both ways is not evidence.

    ``tests/persona_sweep.py`` learned this the expensive way — its first answer
    policy lit none of the surfaces it gated and still reported success.
    """
    failures, notes = [], []
    for name in CHECK_NAMES:
        outcomes = observed_total.get(name, set())
        if name not in declared:
            notes.append(f"coverage: {name} — no episode declares it yet")
            continue
        if "fail" not in outcomes:
            failures.append(f"coverage: no episode makes {name} fail — the bank cannot tell "
                            "whether it still looks at anything")
        if "pass" not in outcomes:
            failures.append(f"coverage: no episode makes {name} pass — it may be failing "
                            "everything for a reason unrelated to the miss it grades")
    return failures, notes


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("episode", nargs="*", help="episode id(s) to replay; default all")
    parser.add_argument("--list", action="store_true", help="list the bank and exit")
    args = parser.parse_args()

    episodes, problems = load_bank(set(args.episode) or None)
    if args.list:
        for episode in episodes:
            print(f"{episode['id']}  {episode['title']}")
            print(f"          fixture={episode['fixture']['trades']} "
                  f"locale={episode['fixture']['locale']} "
                  f"refs={','.join(str(ref) for ref in episode['source']['refs'])}")
        for line in problems:
            print(f"FAIL  {line}")
        return 1 if problems else 0

    failures = list(problems)
    observed_total, declared, unmapped = {}, set(), []
    reference_plan = None
    for episode in episodes:
        declared.update(episode["checks"])
        with tempfile.TemporaryDirectory() as tmp:
            plan, error = prepare_fixture(episode, pathlib.Path(tmp))
            if error:
                failures.append(f"{episode['id']}: {error}")
                continue
            if reference_plan is None and episode["fixture"]["route"] == "first_review":
                reference_plan = plan
            facts = engine_facts(plan, episode)
        if not episode["checks"]:
            unmapped.append(f"unmapped: {episode['id']} — recorded but ungraded, no mechanical "
                            f"check can settle it yet ({episode.get('blocked_on')})")
            continue
        episode_failures, observed, episode_unmapped = replay(episode, facts)
        failures.extend(episode_failures)
        unmapped.extend(episode_unmapped)
        for name, outcomes in observed.items():
            observed_total.setdefault(name, set()).update(outcomes)
        if not episode_failures:
            print(f"PASS  {episode['id']}  {len(episode['answers'])} answer(s) graded on "
                  f"{', '.join(sorted(episode['checks']))}")

    if args.episode:
        # Coverage is a property of the whole bank: on a filtered run, a check
        # whose failing episode was not selected would report a false gap. Say
        # out loud that the interlock did not run rather than skipping quietly.
        notes = ["coverage: not evaluated — a filtered run cannot judge bank coverage; "
                 "run without arguments before trusting a green result"]
    else:
        coverage_failures, notes = coverage_report(observed_total, declared)
        failures.extend(coverage_failures)
        if reference_plan is None:
            failures.append("question consumers: no first_review plan was prepared, so the gate "
                            "could not run — an ungated run is not a green run")
        else:
            consumer_failures, consumer_notes = question_consumers(reference_plan)
            failures.extend(consumer_failures)
            notes.extend(consumer_notes)
        # Pure source analysis (#451) — no prepared plan needed, but grouped
        # with the gate above so a filtered run's "not evaluated" messaging
        # stays honest about which gates a partial run skips.
        field_failures, field_notes = question_field_consumers()
        failures.extend(field_failures)
        notes.extend(field_notes)
    for line in unmapped + notes:
        print(f"NOTE  {line}")
    for line in failures:
        print(f"FAIL  {line}")
    verdict = f"FAIL: {len(failures)} failure(s)" if failures else "PASS: bank replayed clean"
    print(f"\nepisode bank: {len(episodes)} episode(s) — {verdict}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
