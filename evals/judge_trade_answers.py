#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Opt-in semantic judge for final TradeEvaluation answers (#590).

This is a thin specialization over ``evals/judge_episodes.py``. It reuses that
harness's backend resolution, blind single-answer prompt, repeated sampling,
fail-closed parsing, majority voting, and ``ambiguous`` semantics. It adds no
runtime call and never runs in the default offline suite.

Usage:
  python3 evals/judge_trade_answers.py --plan
  python3 evals/judge_trade_answers.py
  python3 evals/judge_trade_answers.py TA-001

The deterministic truth/coverage gate remains first. A witness with
``eligible_for_judge: false`` is reported and never sent to a model.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import judge_episodes as BASE  # noqa: E402

FIXTURE_DIR = HERE / "trade_answers"
AXES = (
    "decision_focus",
    "internal_consistency",
    "decision_synthesis",
    "caveat_discipline",
)

RUBRIC = {
    "decision_focus": {
        "one_line": "one highest-value decision tension is visible early",
        "holds": (
            "The answer makes one material rule collision or dominant portfolio "
            "consequence visible before routine basis facts. The user should not "
            "have to reconstruct the main point from an equal-weight list."
        ),
        "breaks": (
            "The main point is buried, routine metadata leads, or all facts receive "
            "the same prominence. Length alone is irrelevant."
        ),
    },
    "internal_consistency": {
        "one_line": "the lead, support, counter-case, and limitations can all be true together",
        "holds": (
            "The answer does not later negate or silently invalidate its own lead. "
            "A limitation may narrow a claim, but it cannot make the opening claim "
            "incompatible with the same frozen facts."
        ),
        "breaks": (
            "Two parts make incompatible claims about the same rule collision, "
            "portfolio consequence, or evidence state."
        ),
    },
    "decision_synthesis": {
        "one_line": "grounded facts are connected into a book-specific trade-off",
        "holds": (
            "The answer explains what the frozen facts mean for this decision. It "
            "connects the book, premise, rule, and why-now into a supported tension "
            "without inventing a recommendation or forecast."
        ),
        "breaks": (
            "The answer merely translates fields into sentences, lists metrics, or "
            "states facts without a decision implication."
        ),
    },
    "caveat_discipline": {
        "one_line": "material limitations are attached once and proportionately",
        "holds": (
            "Each limitation appears beside the claim it qualifies and does not "
            "obscure the actual decision. Required caveats remain present."
        ),
        "breaks": (
            "Generic warnings, repeated caveats, or a disclaimer block dominates "
            "the answer; or a material limitation is detached from the claim it "
            "changes."
        ),
    },
}


def load_fixtures(selected: set[str] | None = None):
    fixtures, problems = [], []
    for path in sorted(FIXTURE_DIR.glob("TA-*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{path.name}: unreadable fixture: {exc}")
            continue
        if selected and item.get("id") not in selected:
            continue
        unknown = sorted(set(item.get("axes") or []) - set(AXES))
        missing = sorted(set(AXES) - set(item.get("axes") or []))
        if unknown or missing:
            problems.append(
                f"{path.name}: axis registry drift; unknown={unknown}, missing={missing}")
        answers = item.get("answers") or []
        if not answers:
            problems.append(f"{path.name}: no answers")
        for answer in answers:
            bad = sorted(set(answer.get("judge_fails") or []) - set(item.get("axes") or []))
            if bad:
                problems.append(f"{path.name}/{answer.get('id')}: unknown judge_fails {bad}")
            if answer.get("eligible_for_judge") is False and not answer.get("ineligible_reason"):
                problems.append(
                    f"{path.name}/{answer.get('id')}: ineligible answer needs a reason")
        fixtures.append(item)
    if selected:
        found = {item.get("id") for item in fixtures}
        for wanted in sorted(selected - found):
            problems.append(f"unknown fixture {wanted}")
    return fixtures, problems


def episode_view(fixture):
    return {
        "id": fixture["id"],
        "question": fixture["question"],
    }


def eligible_answers(fixture):
    return [answer for answer in fixture["answers"]
            if answer.get("eligible_for_judge", True)]


def coverage_report(observed):
    failures = []
    for axis in AXES:
        seen = observed.get(axis, set())
        if seen != {"pass", "fail"}:
            failures.append(
                f"coverage: {axis} must be observed passing and failing; saw {sorted(seen)}")
    return failures


def validate_witness_bank(fixtures):
    """Offline structural interlocks; safe for the default test suite."""
    failures = []
    for fixture in fixtures:
        axes = tuple(fixture["axes"])
        eligible = eligible_answers(fixture)
        if not eligible:
            failures.append(f"{fixture['id']}: no answer is eligible for semantic judging")
            continue
        for axis in axes:
            failures_for_axis = [a for a in eligible if axis in (a.get("judge_fails") or [])]
            passes_for_axis = [a for a in eligible if axis not in (a.get("judge_fails") or [])]
            if not failures_for_axis:
                failures.append(f"{fixture['id']}: {axis} has no failing witness")
            if not passes_for_axis:
                failures.append(f"{fixture['id']}: {axis} has no passing witness")
        skipped = [a for a in fixture["answers"] if not a.get("eligible_for_judge", True)]
        if not skipped:
            failures.append(
                f"{fixture['id']}: no deterministic-gate rejection witness is present")
    return failures


def plan(fixtures):
    calls = 0
    for fixture in fixtures:
        print(f"FIXTURE {fixture['id']} [{fixture['declared_by']}]")
        for answer in fixture["answers"]:
            if not answer.get("eligible_for_judge", True):
                print(f"  SKIP  {answer['id']}: {answer['ineligible_reason']}")
                continue
            expected = set(answer.get("judge_fails") or [])
            verdicts = ", ".join(
                f"{axis}={'fail' if axis in expected else 'pass'}"
                for axis in fixture["axes"])
            print(f"  JUDGE {answer['id']}: {verdicts}")
            calls += BASE.RUNS
    print(f"\n{calls} model call(s) at {BASE.RUNS} run(s) per eligible answer")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", nargs="*", help="fixture id(s); default all")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()

    BASE.RUBRIC.update(RUBRIC)
    fixtures, problems = load_fixtures(set(args.fixture) or None)
    problems.extend(validate_witness_bank(fixtures))
    if problems:
        for problem in problems:
            print(f"FAIL  {problem}")
        return 1
    if args.plan:
        plan(fixtures)
        return 0

    backend, model = BASE.resolve_backend()
    anthropic = client = None
    if backend == "anthropic":
        import anthropic
        client = anthropic.Anthropic()

    def sample_one(fixture, answer, axes):
        episode = episode_view(fixture)
        if backend == "agy":
            return BASE.judge_once_agy(model, episode, answer, axes)
        return BASE.judge_once(model, client, anthropic, episode, answer, axes)

    failures, observed, unratified = [], {}, set()
    for fixture in fixtures:
        axes = tuple(fixture["axes"])
        if fixture.get("declared_by") != "owner":
            unratified.update(axes)
        for answer in fixture["answers"]:
            if not answer.get("eligible_for_judge", True):
                print(f"SKIP  {fixture['id']}/{answer['id']}  deterministic gate failed")
                continue
            samples = [sample_one(fixture, answer, axes) for _ in range(BASE.RUNS)]
            found, observed_here = BASE.grade_answer(
                episode_view(fixture), answer, axes, samples)
            failures.extend(found)
            for axis, values in observed_here.items():
                observed.setdefault(axis, set()).update(values)
            print(f"{'FAIL' if found else 'PASS'}  {fixture['id']}/{answer['id']}  "
                  f"{len(axes)} axis/axes x {BASE.RUNS} run(s)")

    if not args.fixture:
        failures.extend(coverage_report(observed))
    else:
        print("NOTE  coverage not evaluated on a filtered run")
    if unratified:
        print("NOTE  calibration: uncalibrated — expectations are agent-declared for "
              + ", ".join(sorted(unratified)))
    else:
        print("NOTE  calibration: every expectation is owner-ratified")
    for failure in failures:
        print(f"FAIL  {failure}")
    print("\nTradeEvaluation answer judge: " +
          (f"FAIL: {len(failures)} failure(s)" if failures else
           "PASS: judge reproduced every declared per-axis verdict"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
