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
  python3 evals/judge_trade_answers.py --answer-file /tmp/candidate.json --plan
  python3 evals/judge_trade_answers.py --answer-file /tmp/candidate.json

The production answer-provenance and presentation-fidelity validators run
first. Fixture labels only say what those validators are expected to decide;
they never decide whether an answer reaches the model. Every model-call run
leaves a durable local receipt.
"""
from __future__ import annotations

import argparse
import copy
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

TOOLS_DIR = HERE.parent / "skills" / "fomo-kernel" / "tools"
sys.path.insert(0, str(TOOLS_DIR))
import ux_receipt as UX_RECEIPT  # noqa: E402

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
        "one_line": "claims about the same consequence, rule, and evidence state are mutually compatible with each other and the frozen record",
        "voice_rule_ids": [],
        "holds": (
            "The lead, support, counter-case, and limitations do not negate one "
            "another or contradict the frozen record when they describe the same "
            "fact or evidence state."
        ),
        "breaks": (
            "Two parts make incompatible claims about the same rule collision, "
            "portfolio consequence, or evidence state; or the answer contradicts "
            "the frozen record on one of those states."
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
    delivery_fidelity: dict | None = None


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


def _answer_case(fixture, answer):
    inline = answer.get("agent_case")
    case_ref = answer.get("agent_case_ref")
    if inline is not None and case_ref is not None:
        raise ValueError("answer must carry agent_case or agent_case_ref, not both")
    if inline is not None:
        if not isinstance(inline, dict):
            raise ValueError("agent_case must be an object")
        return inline
    agent_case = (fixture.get("agent_cases") or {}).get(case_ref)
    if agent_case is None:
        raise ValueError(f"unknown agent_case_ref {case_ref!r}")
    return agent_case


def _presented_text(answer):
    return "\n".join(text for _role, text in BASE.R._surfaces(answer))


def _claim_bound_presentation(agent_case, claim_order, presented_text):
    """Prove every candidate surface byte came from its validated case.

    Free prose cannot be mechanically checked for semantic entailment. The
    candidate lane therefore accepts a stricter capture artifact: every case
    claim is presented exactly once, in a declared order, separated only by a
    newline. This prevents an unlabelled sentence from being appended beside a
    valid case and borrowing that case's production provenance verdict.
    """
    if not isinstance(agent_case, dict):
        raise ValueError("candidate agent_case must be an object")
    expected = []
    for side in ("for", "against"):
        claims = agent_case.get(side)
        if not isinstance(claims, list) or not claims:
            raise ValueError(f"candidate agent_case.{side} must be a non-empty list")
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict) or not isinstance(claim.get("claim"), str) \
                    or not claim["claim"].strip():
                raise ValueError(
                    f"candidate agent_case.{side}[{index}].claim must be non-empty text")
        expected.extend((side, index) for index in range(len(claims)))
    if not isinstance(claim_order, list) or not claim_order:
        raise ValueError("candidate presented_claim_order must be a non-empty list")

    refs = []
    for position, ref in enumerate(claim_order):
        if not isinstance(ref, dict) or set(ref) != {"side", "index"}:
            raise ValueError(
                f"candidate presented_claim_order[{position}] must contain only side/index")
        side, index = ref.get("side"), ref.get("index")
        if side not in {"for", "against"}:
            raise ValueError(
                f"candidate presented_claim_order[{position}].side must be for or against")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 \
                or index >= len(agent_case[side]):
            raise ValueError(
                f"candidate presented_claim_order[{position}].index is out of range")
        refs.append((side, index))
    if len(refs) != len(set(refs)):
        raise ValueError("candidate presented_claim_order repeats a case claim")
    missing = sorted(set(expected) - set(refs))
    if missing:
        raise ValueError(
            f"candidate presented_claim_order omits case claim(s) {missing}")

    rendered = "\n".join(agent_case[side][index].get("claim", "")
                         for side, index in refs)
    if presented_text != rendered:
        raise ValueError(
            "candidate presented_text must exactly equal the ordered validated case claims")
    return {
        "claim_count": len(refs),
        "presented_text_digest": RECEIPTS.canonical_sha256(presented_text),
    }


def _number_fact_view(evaluation, challenge):
    """Build the allow-list consumed by the shared numeric provenance checker."""
    numbers, dates = set(), set()
    for _key, value in BASE.R._leaves({"evaluation": evaluation, "challenge": challenge}):
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            number = float(value)
            numbers.add(number)
            if abs(number) <= 1:
                for places in range(5):
                    numbers.add(round(number * 100, places))
        elif isinstance(value, str):
            dates.update(BASE.R.DATE.findall(value))
            for match in BASE.R.NUMBER.finditer(value):
                numbers.add(float(match.group(0).replace(",", "")))
    return {"numbers": numbers, "dates": dates}


def _delivery_fidelity(fixture, answer, agent_case):
    """Run the production presentation check plus shared number provenance."""
    evaluation = _frozen(fixture)
    derived = _challenge(evaluation)
    captured = answer.get("captured_challenge")
    if captured is not None and captured != derived:
        raise ValueError(
            "candidate challenge does not exactly match the challenge derived from its fixture")
    presented = _presented_text(answer)
    evidence = UX_RECEIPT._challenge_fidelity_payload({
        "challenge": derived,
        "presented_text": presented,
    })
    number_findings = BASE.R.check_number_provenance(
        answer, _number_fact_view(evaluation, derived))
    evidence["number_provenance_findings"] = number_findings
    if answer.get("kind") == "candidate_output":
        evidence["claim_bound_presentation"] = _claim_bound_presentation(
            agent_case, answer.get("presented_claim_order"), presented)
    if evidence["facts_missing"]:
        raise ValueError(
            f"presented answer omits {evidence['facts_missing']} machine-checkable "
            "challenge fact(s)")
    if evidence["quotes_expected"] and not evidence["quotes_verbatim"]:
        raise ValueError("presented answer does not reproduce every owed user quote verbatim")
    if number_findings:
        raise ValueError("; ".join(number_findings))
    return evidence


def deterministic_eligibility(fixture, answer):
    """Run the production structured-case gate; fixture labels have no say."""
    evaluation = _frozen(fixture)
    for field in ("premise", "basis", "consequence"):
        if not isinstance(evaluation.get(field), dict):
            return Eligibility(False, f"frozen_evaluation.{field} must be an object")
    if not isinstance(evaluation.get("rule_collisions"), list):
        return Eligibility(False, "frozen_evaluation.rule_collisions must be a list")
    if not isinstance(evaluation.get("context"), dict):
        return Eligibility(False, "frozen_evaluation.context must be an object")
    try:
        agent_case = _answer_case(fixture, answer)
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
        delivery = _delivery_fidelity(fixture, answer, agent_case)
    except (answer_provenance.AnswerProvenanceError, UX_RECEIPT.ReceiptError,
            TypeError, ValueError) as exc:
        return Eligibility(False, str(exc))
    return Eligibility(True, delivery_fidelity=delivery)


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


def load_candidate(path, fixtures):
    """Load one exact presented answer without editing the witness bank."""
    path = pathlib.Path(path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, None, [f"{path}: unreadable candidate answer: {exc}"]
    if not isinstance(payload, dict):
        return None, None, [f"{path}: candidate answer must be a JSON object"]
    allowed = {
        "schema_version", "fixture_id", "answer_id", "agent_case", "challenge",
        "presented_text", "presented_claim_order", "generator",
    }
    problems = []
    unknown = sorted(set(payload) - allowed)
    if unknown:
        problems.append(f"{path}: candidate answer has unknown fields {unknown}")
    if payload.get("schema_version") != 2:
        problems.append(f"{path}: schema_version must be 2")
    fixture_id = payload.get("fixture_id")
    answer_id = payload.get("answer_id")
    if not isinstance(fixture_id, str) or not fixture_id:
        problems.append(f"{path}: fixture_id must be a non-empty string")
    if not isinstance(answer_id, str) or not answer_id:
        problems.append(f"{path}: answer_id must be a non-empty string")
    if not isinstance(payload.get("agent_case"), dict):
        problems.append(f"{path}: agent_case must be an object")
    if not isinstance(payload.get("challenge"), dict):
        problems.append(f"{path}: challenge must be an object")
    if not isinstance(payload.get("presented_text"), str) \
            or not payload["presented_text"].strip():
        problems.append(f"{path}: presented_text must be a non-empty string")
    if "generator" in payload and not isinstance(payload["generator"], dict):
        problems.append(f"{path}: generator must be an object when present")
    matches = [fixture for fixture in fixtures if fixture.get("id") == fixture_id]
    if not matches:
        problems.append(f"{path}: unknown fixture_id {fixture_id!r}")
    if not problems:
        try:
            _claim_bound_presentation(
                payload["agent_case"], payload.get("presented_claim_order"),
                payload["presented_text"])
        except ValueError as exc:
            problems.append(f"{path}: {exc}")
    if problems:
        return None, payload, problems

    source = matches[0]
    candidate = copy.deepcopy(source)
    candidate["answers"] = [{
        "id": answer_id,
        "kind": "candidate_output",
        "agent_case": payload["agent_case"],
        "captured_challenge": payload["challenge"],
        "presented_claim_order": payload["presented_claim_order"],
        "judge_fails": [],
        "prose": payload["presented_text"],
    }]
    return candidate, payload, []


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


def plan(fixtures, backend=None, model=None, *, run_kind="fixture_witness"):
    calls = 0
    for fixture in fixtures:
        print(f"FIXTURE {fixture['id']} [{fixture['declared_by']}; {run_kind}]")
        for answer in fixture["answers"]:
            eligibility = deterministic_eligibility(fixture, answer)
            if not eligibility.eligible:
                print(f"  SKIP  {answer['id']}: production gate: {eligibility.reason}")
                continue
            expected = set(answer.get("judge_fails") or [])
            verdicts = ", ".join(
                f"{axis}={'fail' if axis in expected else 'pass'}"
                for axis in fixture["axes"])
            label = "CANDIDATE" if run_kind == "candidate_output" else "JUDGE"
            print(f"  {label} {answer['id']}: {verdicts}")
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


def judge_input_request(episode, answer, axes, *, backend, model,
                        material_fn=None):
    """Return the canonical call specification used for one judge sample."""
    material_fn = material if material_fn is None else material_fn
    if backend == "agy":
        return {
            "transport": "agy",
            "call_spec": BASE.agy_call_spec(
                model, episode, answer, axes, system=SYSTEM, rubric=RUBRIC,
                material_fn=material_fn),
        }
    return {
        "transport": f"{backend}_structured_tool",
        "call_spec": BASE.structured_call_spec(
            model, episode, answer, axes, system=SYSTEM, rubric=RUBRIC,
            material_fn=material_fn),
    }


def judge_input_digest(episode, answer, axes, *, backend, model,
                       material_fn=None):
    """Bind one report to every argument in the canonical judge call."""
    return RECEIPTS.canonical_sha256(judge_input_request(
        episode, answer, axes, backend=backend, model=model,
        material_fn=material_fn))


def axis_summary(answer_reports, axes):
    """Defined per-axis agreement/disagreement/ambiguity numerators and rates."""
    summary = {}
    for axis in axes:
        counts = {name: 0 for name in ("agreement", "disagreement", "ambiguous")}
        for answer in answer_reports:
            if answer.get("eligibility") != "accepted":
                continue
            report = ((answer.get("report") or {}).get("axes") or {}).get(axis)
            if not isinstance(report, dict):
                continue
            classification = report.get("classification", "ambiguous")
            if classification not in counts:
                classification = "ambiguous"
            counts[classification] += 1
        total = sum(counts.values())
        summary[axis] = {
            "counts": {**counts, "total": total},
            "rates": {
                name: (counts[name] / total if total else None)
                for name in counts
            },
        }
    return summary


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
        calibration = row.get("calibration") or {}
        declarations = ",".join(calibration.get("declared_by") or ()) or "?"
        print(f"{row.get('run_at', '?')}  {row.get('status', '?').upper():4}  "
              f"{row.get('backend', '?')}/{row.get('model', '?')}  "
              f"kind={row.get('run_kind', 'fixture_witness')} "
              f"scope={row.get('scope', '?')} fixtures={selected} "
              f"calibration={calibration.get('state', '?')}[{declarations}] "
              f"fixture={str(row.get('fixture_digest', '?'))[:12]} "
              f"contract={str(row.get('judge_contract_digest', '?'))[:12]}")
        summary = row.get("axis_summary") or {}
        if summary:
            cells = []
            for axis, report in summary.items():
                counts = report.get("counts") or {}
                cells.append(
                    f"{axis}:a{counts.get('agreement', 0)}/"
                    f"d{counts.get('disagreement', 0)}/"
                    f"u{counts.get('ambiguous', 0)}")
            print("  axes " + " ".join(cells))
    return 0


def _emit_after_receipt(lines):
    """Best-effort CLI output after evidence is already durable.

    A downstream reader may close stdout early (for example ``| head``). That
    must never unwind through the model-call loop before its receipt append.
    """
    try:
        for line in lines:
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            safe = line.encode(encoding, errors="backslashreplace").decode(encoding)
            print(safe)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except (BrokenPipeError, OSError):
            pass


def run_judge(fixtures, *, backend, model, sample_one, filtered=False,
              run_kind="fixture_witness", candidate_digest=None,
              candidate_artifact=None, source_fixture_digest=None,
              append_receipt=RECEIPTS.append_receipt):
    """Execute one selected bank and persist its complete per-axis evidence."""
    if run_kind == "candidate_output":
        if not isinstance(candidate_artifact, dict):
            raise ValueError(
                "candidate_output requires the exact candidate_artifact for its receipt")
        if len(fixtures) != 1 or len(fixtures[0].get("answers") or []) != 1:
            raise ValueError("candidate_output must contain exactly one fixture and one answer")
        candidate_answer = fixtures[0]["answers"][0]
        expected = {
            "fixture_id": fixtures[0].get("id"),
            "answer_id": candidate_answer.get("id"),
            "agent_case": candidate_answer.get("agent_case"),
            "challenge": candidate_answer.get("captured_challenge"),
            "presented_claim_order": candidate_answer.get("presented_claim_order"),
            "presented_text": candidate_answer.get("prose"),
        }
        mismatched = sorted(
            key for key, value in expected.items()
            if candidate_artifact.get(key) != value)
        if candidate_answer.get("kind") != "candidate_output" or mismatched:
            raise ValueError(
                "candidate_output fixture does not reproduce its exact artifact"
                + (f" (mismatched: {mismatched})" if mismatched else ""))
        artifact_digest = RECEIPTS.canonical_sha256(candidate_artifact)
        if candidate_digest is not None and candidate_digest != artifact_digest:
            raise ValueError("candidate_digest does not match candidate_artifact")
        candidate_digest = artifact_digest

    failures, observed = [], {}
    answer_reports = []
    judge_inputs = {}
    output_lines = []
    judged_answers = 0
    for fixture in fixtures:
        axes = tuple(fixture["axes"])
        for answer in fixture["answers"]:
            eligibility = deterministic_eligibility(fixture, answer)
            if not eligibility.eligible:
                output_lines.append(
                    f"SKIP  {fixture['id']}/{answer['id']}  production gate: "
                    f"{eligibility.reason}")
                answer_reports.append({
                    "fixture_id": fixture["id"],
                    "answer_id": answer["id"],
                    "answer_kind": answer.get("kind", "fixture_witness"),
                    "eligibility": "rejected",
                    "eligibility_reason": eligibility.reason,
                })
                continue
            judged_answers += 1
            samples, sample_errors = [], []
            episode = episode_view(fixture)
            input_request = judge_input_request(
                episode, answer, axes, backend=backend, model=model)
            input_digest = RECEIPTS.canonical_sha256(input_request)
            judge_inputs[input_digest] = copy.deepcopy(input_request)
            for run in range(1, BASE.RUNS + 1):
                try:
                    sample = sample_one(episode, answer, axes, input_request)
                except Exception as exc:  # noqa: BLE001 - every failed call is receipted
                    sample = None
                    sample_errors.append({
                        "run": run,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    })
                samples.append(sample)
            found, observed_here, report = BASE.grade_answer_report(
                episode, answer, axes, samples)
            failures.extend(found)
            for axis, values in observed_here.items():
                observed.setdefault(axis, set()).update(values)
            answer_reports.append({
                "fixture_id": fixture["id"],
                "answer_id": answer["id"],
                "answer_kind": answer.get("kind", "fixture_witness"),
                "eligibility": "accepted",
                "delivery_fidelity": eligibility.delivery_fidelity,
                "judge_input_digest": input_digest,
                "sample_errors": sample_errors,
                "failures": found,
                "report": report,
            })
            output_lines.append(
                f"{'FAIL' if found else 'PASS'}  {fixture['id']}/{answer['id']}")
            for axis in axes:
                axis_report = report["axes"][axis]
                tally = axis_report["tally"]
                output_lines.append(
                    f"  {axis}: expected={axis_report['expected']} "
                    f"got={axis_report['verdict']} "
                    f"result={axis_report['classification']} "
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
    if run_kind == "candidate_output":
        notes.append("candidate target: every semantic axis passes; this is not a fixture label")

    summaries = axis_summary(answer_reports, AXES)

    receipt = {
        "schema_version": 3,
        "run_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "backend": backend,
        "model": model,
        "effort": BASE.EFFORT,
        "runs_per_answer": BASE.RUNS,
        "run_kind": run_kind,
        "scope": ("candidate_output" if run_kind == "candidate_output" else
                  "filtered" if filtered else "full_bank"),
        "fixture_ids": [fixture["id"] for fixture in fixtures],
        "fixture_digest": RECEIPTS.canonical_sha256(fixtures),
        "source_fixture_digest": source_fixture_digest,
        "candidate_digest": candidate_digest,
        "candidate_artifact": (copy.deepcopy(candidate_artifact)
                               if run_kind == "candidate_output" else None),
        "judge_contract_digest": judge_contract_digest(),
        "judge_inputs": judge_inputs,
        "judged_answer_count": judged_answers,
        "rejected_answer_count": sum(
            report["eligibility"] == "rejected" for report in answer_reports),
        "calibration": calibration,
        "axis_summary": summaries,
        "answers": answer_reports,
        "failures": failures,
        "notes": notes,
        "status": "fail" if failures else "pass",
    }
    try:
        receipt_path = append_receipt(receipt)
    except RECEIPTS.ReceiptError as exc:
        output_lines.extend([
            f"FAIL  {exc}",
            "",
            "TradeEvaluation answer judge: FAIL — verdict was not durably recorded",
        ])
        _emit_after_receipt(output_lines)
        return 1

    for note in notes:
        output_lines.append(f"NOTE  {note}")
    for failure in failures:
        output_lines.append(f"FAIL  {failure}")
    for axis, summary in summaries.items():
        counts, rates = summary["counts"], summary["rates"]
        rendered_rates = " ".join(
            f"{name}_rate={rates[name]:.3f}" if rates[name] is not None else
            f"{name}_rate=n/a"
            for name in ("agreement", "disagreement", "ambiguous"))
        output_lines.append(
            f"AXIS  {axis} agreement={counts['agreement']} "
            f"disagreement={counts['disagreement']} ambiguous={counts['ambiguous']} "
            f"total={counts['total']} {rendered_rates}")
    output_lines.append(f"RECEIPT  {receipt_path}")
    success = ("candidate met the pass target on every semantic axis"
               if run_kind == "candidate_output" else
               "judge reproduced every declared per-axis verdict")
    verdict = f"FAIL: {len(failures)} failure(s)" if failures else f"PASS: {success}"
    output_lines.extend([
        "",
        f"TradeEvaluation answer judge ({backend}, {model}): {verdict}",
    ])
    _emit_after_receipt(output_lines)
    return 1 if failures else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", nargs="*", help="fixture id(s); default all")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument(
        "--answer-file", metavar="PATH",
        help="judge one exact candidate-output artifact instead of committed witnesses")
    parser.add_argument("--history", nargs="?", type=int, const=20, default=None,
                        metavar="N", help="show recent receipts without model calls")
    args = parser.parse_args(argv)

    if args.history is not None:
        if args.fixture or args.plan or args.answer_file:
            parser.error("--history cannot be combined with fixtures, --plan, or --answer-file")
        if args.history < 1:
            parser.error("--history N requires N >= 1")
        return _show_history(args.history)

    if BASE.RUNS < 1:
        print(f"FAIL  TR_JUDGE_RUNS must be >= 1 (got {BASE.RUNS})")
        return 1

    if args.answer_file and args.fixture:
        parser.error("--answer-file selects its fixture internally; do not pass fixture ids")

    candidate_digest = source_fixture_digest = None
    run_kind = "candidate_output" if args.answer_file else "fixture_witness"
    if args.answer_file:
        bank, problems = load_fixtures()
        if not problems:
            problems.extend(validate_witness_bank(bank))
        candidate = payload = None
        if not problems:
            candidate, payload, candidate_problems = load_candidate(args.answer_file, bank)
            problems.extend(candidate_problems)
        if candidate is not None and payload is not None:
            source = next(item for item in bank if item["id"] == candidate["id"])
            source_fixture_digest = RECEIPTS.canonical_sha256(source)
            candidate_digest = RECEIPTS.canonical_sha256(payload)
            fixtures = [candidate]
        else:
            fixtures = []
    else:
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
        calls = plan(fixtures, backend, model, run_kind=run_kind)
        return 0 if calls else 1

    backend, model = BASE.resolve_backend()
    anthropic = client = None
    if backend == "anthropic":
        import anthropic
        client = anthropic.Anthropic()

    def sample_one(episode, answer, axes, input_request):
        if backend == "agy":
            return BASE.judge_once_agy(
                model, episode, answer, axes, system=SYSTEM, rubric=RUBRIC,
                material_fn=material, call_spec=input_request["call_spec"])
        return BASE.judge_once(
            model, client, anthropic, episode, answer, axes, system=SYSTEM,
            rubric=RUBRIC, material_fn=material,
            request=input_request["call_spec"])

    return run_judge(fixtures, backend=backend, model=model,
                     sample_one=sample_one,
                     filtered=bool(args.fixture) or bool(args.answer_file),
                     run_kind=run_kind, candidate_digest=candidate_digest,
                     candidate_artifact=payload if args.answer_file else None,
                     source_fixture_digest=source_fixture_digest)


if __name__ == "__main__":
    sys.exit(main())
