#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Opt-in semantic judge for final TradeEvaluation answers (#590).

This is a thin specialization over ``evals/judge_episodes.py``. It reuses that
harness's backend resolution, blind single-answer prompt, repeated sampling,
fail-closed parsing, majority voting, and ``ambiguous`` semantics. It adds no
runtime call. Only its deterministic interlocks run in the default offline
suite; model calls remain explicit and opt-in.

Usage:
  python3 evals/judge_trade_answers.py --plan
  python3 evals/judge_trade_answers.py
  python3 evals/judge_trade_answers.py TA-001

The production answer-provenance/coverage validator runs first. Fixture labels
only say what that validator is expected to decide; they never decide whether
an answer reaches the model. Every live run leaves a durable local receipt.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys
from dataclasses import dataclass

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import judge_episodes as BASE  # noqa: E402
import judge_receipts as RECEIPTS  # noqa: E402

ENGINE_DIR = HERE.parent / "skills" / "fomo-kernel" / "engine"
sys.path.insert(0, str(ENGINE_DIR))
import answer_provenance  # noqa: E402
import evaluation_challenge  # noqa: E402

FIXTURE_DIR = HERE / "trade_answers"
AXES = (
    "decision_focus",
    "internal_consistency",
    "decision_synthesis",
    "caveat_discipline",
)

SYSTEM = """You grade one final answer produced from a frozen TradeEvaluation.

You receive the user's question, the exact frozen engine inputs, the production
challenge derived from them, and only the answer being graded. For each named
axis return `pass` or `fail`.

Five rules govern every verdict:

1. Judge the meaning, not resemblance to a preferred answer. Wording, tone,
   formatting, vocabulary, and length by themselves are never defects.
2. Ordering, adjacency, and repetition are evidence only when the named axis
   explicitly makes lead placement or caveat attachment part of its meaning.
3. Treat the frozen payload as the record. Do not recalculate its arithmetic,
   invent missing market facts, or turn this task into investment advice.
4. Judge each axis independently. Do not fail an answer for a defect owned by
   another axis or by the deterministic provenance gate that ran before you.
5. If an axis genuinely cannot be evaluated from the answer and frozen record,
   fail it and say why. An unevaluable axis has not passed.

Give one sentence of reason per axis. For a failure, quote the answer span that
decides it. Expected labels, paired answers, fixture titles, and notes are never
shown to you."""

RUBRIC = {
    "decision_focus": {
        "one_line": "one supported decision-relevant lead appears before secondary material",
        "voice_rule_ids": ["V1", "V4"],
        "holds": (
            "One supported rule collision or dominant portfolio consequence is "
            "clearly the lead before secondary basis and limitation material."
        ),
        "breaks": (
            "The decision-relevant point is buried after secondary material or "
            "presented with no greater salience than routine facts. Length alone "
            "is irrelevant."
        ),
    },
    "internal_consistency": {
        "one_line": "claims about the same consequence, rule, and evidence state are mutually compatible",
        "voice_rule_ids": [],
        "holds": (
            "The lead, support, counter-case, and limitations do not negate one "
            "another when they describe the same frozen fact or evidence state."
        ),
        "breaks": (
            "Two parts make incompatible claims about the same rule collision, "
            "portfolio consequence, or evidence state."
        ),
    },
    "decision_synthesis": {
        "one_line": "frozen facts are connected into an explicit decision implication",
        "voice_rule_ids": ["V3"],
        "holds": (
            "The answer itself states what the relationship between the frozen "
            "facts means for the choice. A reader does not have to compare values "
            "or supply the missing 'therefore'."
        ),
        "breaks": (
            "Facts are prioritized, juxtaposed, translated, or summarized, but the "
            "answer never states their relationship or decision implication; the "
            "reader must perform the comparison or supply the 'therefore'."
        ),
    },
    "caveat_discipline": {
        "one_line": "each material limitation is placed once with the claim it narrows",
        "voice_rule_ids": ["V6"],
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


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    reason: str | None = None


def _frozen(fixture):
    return fixture.get("frozen_evaluation") or {}


def _user_statements(evaluation):
    context = evaluation.get("context")
    if not isinstance(context, dict):
        return ()
    return tuple(context[key] for key in ("reason", "why_now")
                 if isinstance(context.get(key), str) and context[key])


def _challenge(evaluation):
    return evaluation_challenge.build_challenge(
        premise=evaluation.get("premise"),
        basis=evaluation.get("basis"),
        consequence=evaluation.get("consequence"),
        rule_collisions=evaluation.get("rule_collisions") or (),
        context=evaluation.get("context"),
    )


def deterministic_eligibility(fixture, answer):
    """Run the production structured-case gate; fixture labels have no say."""
    case_ref = answer.get("agent_case_ref")
    agent_case = (fixture.get("agent_cases") or {}).get(case_ref)
    if agent_case is None:
        return Eligibility(False, f"unknown agent_case_ref {case_ref!r}")
    evaluation = _frozen(fixture)
    for field in ("premise", "basis", "consequence"):
        if not isinstance(evaluation.get(field), dict):
            return Eligibility(False, f"frozen_evaluation.{field} must be an object")
    if not isinstance(evaluation.get("rule_collisions"), list):
        return Eligibility(False, "frozen_evaluation.rule_collisions must be a list")
    if not isinstance(evaluation.get("context"), dict):
        return Eligibility(False, "frozen_evaluation.context must be an object")
    try:
        # Building the challenge is part of eligibility even though
        # validate_agent_case derives the enforced subset itself. It proves
        # the exact frozen payload shown to the judge can drive the same
        # production obligation builder used by `consider`.
        _challenge(evaluation)
        answer_provenance.validate_agent_case(
            agent_case,
            basis=evaluation.get("basis"),
            consequence=evaluation.get("consequence"),
            rule_collisions=evaluation.get("rule_collisions") or (),
            user_statements=_user_statements(evaluation),
        )
    except (answer_provenance.AnswerProvenanceError, TypeError, ValueError) as exc:
        return Eligibility(False, str(exc))
    return Eligibility(True)


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
        if item.get("declared_by") not in {"agent", "owner"}:
            problems.append(f"{path.name}: declared_by must be agent or owner")
        answers = item.get("answers") or []
        if not answers:
            problems.append(f"{path.name}: no answers")
        evaluation = _frozen(item)
        required = {"premise", "basis", "consequence", "rule_collisions", "context"}
        missing_frozen = sorted(required - set(evaluation))
        if missing_frozen:
            problems.append(f"{path.name}: frozen_evaluation missing {missing_frozen}")
        cases = item.get("agent_cases")
        if not isinstance(cases, dict) or not cases:
            problems.append(f"{path.name}: agent_cases must be a non-empty object")
        for answer in answers:
            bad = sorted(set(answer.get("judge_fails") or []) - set(item.get("axes") or []))
            if bad:
                problems.append(f"{path.name}/{answer.get('id')}: unknown judge_fails {bad}")
            if not isinstance(answer.get("expect_eligible"), bool):
                problems.append(
                    f"{path.name}/{answer.get('id')}: expect_eligible must be boolean")
                continue
            if answer.get("agent_case_ref") not in (cases or {}):
                problems.append(
                    f"{path.name}/{answer.get('id')}: unknown agent_case_ref "
                    f"{answer.get('agent_case_ref')!r}")
                continue
            actual = deterministic_eligibility(item, answer)
            if actual.eligible != answer["expect_eligible"]:
                problems.append(
                    f"{path.name}/{answer.get('id')}: production eligibility "
                    f"was {actual.eligible}, expected {answer['expect_eligible']}"
                    + (f" ({actual.reason})" if actual.reason else ""))
            if not actual.eligible and answer.get("judge_fails"):
                problems.append(
                    f"{path.name}/{answer.get('id')}: an ineligible answer cannot "
                    "declare semantic judge failures")
        fixtures.append(item)
    if selected:
        found = {item.get("id") for item in fixtures}
        for wanted in sorted(selected - found):
            problems.append(f"unknown fixture {wanted}")
    return fixtures, problems


def episode_view(fixture):
    evaluation = _frozen(fixture)
    return {
        "id": fixture["id"],
        "question": fixture["question"],
        "frozen_evaluation": evaluation,
        "challenge": _challenge(evaluation),
    }


def eligible_answers(fixture):
    return [answer for answer in fixture["answers"]
            if deterministic_eligibility(fixture, answer).eligible]


def material(episode, answer):
    """Blind material for this adapter; no fixture labels or sibling answers."""
    lines = [f"THE USER'S QUESTION ({episode['question']['kind']}):",
             episode["question"]["text"], "",
             "THE FROZEN TRADE EVALUATION (authoritative input, not a reference answer):",
             json.dumps(episode["frozen_evaluation"], ensure_ascii=False,
                        sort_keys=True, indent=2), "",
             "THE PRODUCTION CHALLENGE DERIVED FROM THOSE FROZEN FACTS:",
             json.dumps(episode["challenge"], ensure_ascii=False,
                        sort_keys=True, indent=2), "",
             "THE ANSWER, AS THE USER WOULD MEET IT:"]
    for role, text in BASE.R._surfaces(answer):
        lines.append(f"  [{role}] {text}")
    return "\n".join(lines)


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
            failures_for_axis = [a for a in eligible
                                 if (a.get("judge_fails") or []) == [axis]]
            passes_for_axis = [a for a in eligible if axis not in (a.get("judge_fails") or [])]
            if not failures_for_axis:
                failures.append(
                    f"{fixture['id']}: {axis} has no isolated single-axis failing witness")
            if not passes_for_axis:
                failures.append(f"{fixture['id']}: {axis} has no passing witness")
        skipped = [a for a in fixture["answers"]
                   if not deterministic_eligibility(fixture, a).eligible]
        if not skipped:
            failures.append(
                f"{fixture['id']}: no deterministic-gate rejection witness is present")
    return failures


def plan(fixtures, backend=None, model=None):
    calls = 0
    for fixture in fixtures:
        print(f"FIXTURE {fixture['id']} [{fixture['declared_by']}]")
        for answer in fixture["answers"]:
            eligibility = deterministic_eligibility(fixture, answer)
            if not eligibility.eligible:
                print(f"  SKIP  {answer['id']}: production gate: {eligibility.reason}")
                continue
            expected = set(answer.get("judge_fails") or [])
            verdicts = ", ".join(
                f"{axis}={'fail' if axis in expected else 'pass'}"
                for axis in fixture["axes"])
            print(f"  JUDGE {answer['id']}: {verdicts}")
            calls += BASE.RUNS
    print(f"\n{calls} model call(s) at {BASE.RUNS} run(s) per eligible answer, "
          f"backend={backend or 'unresolved'}, model={model or 'unresolved'}")
    return calls


def _calibration(fixtures):
    declarations = sorted({fixture.get("declared_by") for fixture in fixtures})
    owner_ratified = declarations == ["owner"]
    return {
        "state": "owner_ratified" if owner_ratified else "uncalibrated",
        "declared_by": declarations,
        "note": ("every selected expectation is owner-ratified" if owner_ratified else
                 "agent-declared expectations measure agreement, not accuracy"),
    }


def judge_contract_digest(*, axes=None, system=None, rubric=None):
    """Bind a receipt to the exact semantic contract used for its verdicts."""
    axes = AXES if axes is None else tuple(axes)
    system = SYSTEM if system is None else system
    rubric = RUBRIC if rubric is None else rubric
    return RECEIPTS.canonical_sha256({
        "axes": list(axes),
        "system": system,
        "rubric": {axis: rubric[axis] for axis in axes},
    })


def _show_history(limit):
    try:
        rows = RECEIPTS.read_history()
    except RECEIPTS.ReceiptError as exc:
        print(f"FAIL  {exc}")
        return 1
    if not rows:
        print(f"No TradeEvaluation judge receipts at {RECEIPTS.history_path()}")
        return 0
    print(f"{RECEIPTS.history_path()} ({len(rows)} run(s); newest "
          f"{min(limit, len(rows))})")
    for row in rows[-limit:]:
        selected = ",".join(row.get("fixture_ids") or ()) or "?"
        print(f"{row.get('run_at', '?')}  {row.get('status', '?').upper():4}  "
              f"{row.get('backend', '?')}/{row.get('model', '?')}  "
              f"scope={row.get('scope', '?')} fixtures={selected} "
              f"fixture={str(row.get('fixture_digest', '?'))[:12]} "
              f"contract={str(row.get('judge_contract_digest', '?'))[:12]}")
    return 0


def run_judge(fixtures, *, backend, model, sample_one, filtered=False,
              append_receipt=RECEIPTS.append_receipt):
    """Execute one selected bank and persist its complete per-axis evidence."""
    failures, observed = [], {}
    answer_reports = []
    judged_answers = 0
    for fixture in fixtures:
        axes = tuple(fixture["axes"])
        for answer in fixture["answers"]:
            eligibility = deterministic_eligibility(fixture, answer)
            if not eligibility.eligible:
                print(f"SKIP  {fixture['id']}/{answer['id']}  production gate: "
                      f"{eligibility.reason}")
                answer_reports.append({
                    "fixture_id": fixture["id"],
                    "answer_id": answer["id"],
                    "eligibility": "rejected",
                    "eligibility_reason": eligibility.reason,
                })
                continue
            judged_answers += 1
            samples, sample_errors = [], []
            episode = episode_view(fixture)
            for run in range(1, BASE.RUNS + 1):
                try:
                    sample = sample_one(episode, answer, axes)
                except RuntimeError as exc:
                    sample = None
                    sample_errors.append({"run": run, "error": str(exc)})
                samples.append(sample)
            found, observed_here, report = BASE.grade_answer_report(
                episode, answer, axes, samples)
            failures.extend(found)
            for axis, values in observed_here.items():
                observed.setdefault(axis, set()).update(values)
            answer_reports.append({
                "fixture_id": fixture["id"],
                "answer_id": answer["id"],
                "eligibility": "accepted",
                "sample_errors": sample_errors,
                "failures": found,
                "report": report,
            })
            print(f"{'FAIL' if found else 'PASS'}  {fixture['id']}/{answer['id']}")
            for axis in axes:
                axis_report = report["axes"][axis]
                tally = axis_report["tally"]
                print(f"  {axis}: expected={axis_report['expected']} "
                      f"got={axis_report['verdict']} "
                      f"pass={tally['pass']} fail={tally['fail']} "
                      f"unusable={axis_report['unusable']}")

    notes = []
    if judged_answers == 0:
        failures.append("nothing to judge — every selected answer failed the production gate")
    if filtered:
        notes.append("coverage not evaluated on a filtered run")
    else:
        failures.extend(coverage_report(observed))
    calibration = _calibration(fixtures)
    notes.append(f"calibration: {calibration['state']} — {calibration['note']}")

    receipt = {
        "schema_version": 1,
        "run_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "backend": backend,
        "model": model,
        "effort": BASE.EFFORT,
        "runs_per_answer": BASE.RUNS,
        "scope": "filtered" if filtered else "full_bank",
        "fixture_ids": [fixture["id"] for fixture in fixtures],
        "fixture_digest": RECEIPTS.canonical_sha256(fixtures),
        "judge_contract_digest": judge_contract_digest(),
        "judged_answer_count": judged_answers,
        "rejected_answer_count": sum(
            report["eligibility"] == "rejected" for report in answer_reports),
        "calibration": calibration,
        "answers": answer_reports,
        "failures": failures,
        "notes": notes,
        "status": "fail" if failures else "pass",
    }
    try:
        receipt_path = append_receipt(receipt)
    except RECEIPTS.ReceiptError as exc:
        print(f"FAIL  {exc}")
        print("\nTradeEvaluation answer judge: FAIL — verdict was not durably recorded")
        return 1

    for note in notes:
        print(f"NOTE  {note}")
    for failure in failures:
        print(f"FAIL  {failure}")
    print(f"RECEIPT  {receipt_path}")
    verdict = (f"FAIL: {len(failures)} failure(s)" if failures else
               "PASS: judge reproduced every declared per-axis verdict")
    print(f"\nTradeEvaluation answer judge ({backend}, {model}): {verdict}")
    return 1 if failures else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", nargs="*", help="fixture id(s); default all")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--history", nargs="?", type=int, const=20, default=None,
                        metavar="N", help="show recent receipts without model calls")
    args = parser.parse_args(argv)

    if args.history is not None:
        if args.fixture or args.plan:
            parser.error("--history cannot be combined with fixtures or --plan")
        if args.history < 1:
            parser.error("--history N requires N >= 1")
        return _show_history(args.history)

    if BASE.RUNS < 1:
        print(f"FAIL  TR_JUDGE_RUNS must be >= 1 (got {BASE.RUNS})")
        return 1

    fixtures, problems = load_fixtures(set(args.fixture) or None)
    if not problems:
        problems.extend(validate_witness_bank(fixtures))
    if problems:
        for problem in problems:
            print(f"FAIL  {problem}")
        return 1
    if args.plan:
        try:
            backend, model = BASE.resolve_backend()
        except SystemExit as reason:
            backend = model = None
            print(f"NOTE  no backend resolved yet — {reason}")
        plan(fixtures, backend, model)
        return 0

    backend, model = BASE.resolve_backend()
    anthropic = client = None
    if backend == "anthropic":
        import anthropic
        client = anthropic.Anthropic()

    def sample_one(episode, answer, axes):
        if backend == "agy":
            return BASE.judge_once_agy(
                model, episode, answer, axes, system=SYSTEM, rubric=RUBRIC,
                material_fn=material)
        return BASE.judge_once(
            model, client, anthropic, episode, answer, axes, system=SYSTEM,
            rubric=RUBRIC, material_fn=material)

    return run_judge(fixtures, backend=backend, model=model,
                     sample_one=sample_one, filtered=bool(args.fixture))


if __name__ == "__main__":
    sys.exit(main())
