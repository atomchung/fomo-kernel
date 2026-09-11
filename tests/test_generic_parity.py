#!/usr/bin/env python3
"""Offline interlocks for Issue #715's opt-in Generic Parity probe."""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
sys.path.insert(0, str(EVALS))
import generic_parity as GP  # noqa: E402


def load_runner():
    spec = importlib.util.spec_from_file_location("generic_parity_runner", EVALS / "run_generic_parity.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def args(output_dir):
    return argparse.Namespace(generator_backend="agy", generator_model="generator-test",
        judge_backend="agy", judge_model="judge-test", runs=3, output_dir=pathlib.Path(output_dir))


def test_three_case_bank_and_canonical_bundle_are_closed():
    cases = GP.load_cases(("A01", "A07", "A10"))
    assert [case["id"] for case in cases] == ["A01", "A07", "A10"]
    bundle = GP.instruction_bundle()
    assert [item["path"] for item in bundle] == [
        "AGENTS.md", "docs/output-voice.md",
        "skills/fomo-kernel/references/decision-framing.md",
        "skills/fomo-kernel/references/research-priors.md",
    ]
    assert all("consider_surface" not in item["text"] for item in bundle)
    assert not GP.validate_witness_bank()


def test_deterministic_gates_catch_specificity_process_and_question_count():
    prompt = GP.load_cases(("A01",))[0]["prompt"]
    findings = GP.deterministic_findings(
        "Use VOO at 8%, then tell the engine your workflow from /Users/x? Why now？ What is your stop？", prompt)
    assert any("numeric" in finding for finding in findings)
    assert any("named fund" in finding for finding in findings)
    assert any("process" in finding for finding in findings)
    assert any("private" in finding for finding in findings)
    assert any("question" in finding for finding in findings)
    assert not any("numeric" in finding for finding in GP.deterministic_findings(
        "1. 策略選項\n2. 例外條件\n\n### 3. 最後問題", prompt))


def test_disposition_never_promotes_ambiguity_or_semantic_disagreement():
    assert GP.receipt_disposition(["new numeric specificity"], {}) == "deterministic_fail"
    assert GP.receipt_disposition([], {"x": {"classification": "ambiguous"}}) == "ambiguous"
    assert GP.receipt_disposition([], {"x": {"classification": "disagreement"}}) == "semantic_fail"
    assert GP.receipt_disposition([], {"x": {"classification": "agreement"}}) == "candidate_pass"


def test_witnesses_drive_each_declared_semantic_axis_without_model_calls():
    runner = load_runner()
    for witness in GP.load_witnesses():
        expected_fail = set(witness.get("judge_fails", ()))
        sample = {axis: {"verdict": "fail" if axis in expected_fail else "pass",
                         "reason": "synthetic declared witness"} for axis in GP.AXES}
        failures, _observed, report = runner.BASE.grade_answer_report(
            {"id": witness["case_id"]}, {"id": witness["id"], "judge_fails": list(expected_fail)},
            GP.AXES, [sample, sample, sample])
        assert not failures, (witness["id"], failures)
        assert all(row["classification"] == "agreement" for row in report["axes"].values())


def test_plan_is_zero_call_and_names_the_twelve_call_shape():
    runner = load_runner()
    cases = GP.load_cases(("A01", "A07", "A10"))
    with tempfile.TemporaryDirectory() as tmp:
        output_dir = pathlib.Path(tmp) / "plan-writes-nothing"
        result = runner.plan(cases, args(output_dir))
        assert not output_dir.exists()
    assert result["planned_model_calls"] == 12
    assert result["instruction_paths"][-1].endswith("research-priors.md")
    assert result["controlled_mutation"] == {"case_id": "A01", "witness_id": "question_first",
                                                "judge_calls": 0, "caught": True}


def test_run_preserves_exact_output_and_short_circuits_the_judge_on_a_hard_gate():
    runner = load_runner()
    original = runner.BASE.resolve_backend
    runner.BASE.resolve_backend = lambda name: (name, f"{name}-stub")
    calls = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            def generate(case, _prompt):
                return "A01 answer? second question？" if case["id"] == "A01" else "safe completion"

            def judge(case, answer, run):
                calls.append((case["id"], answer, run))
                return {axis: {"verdict": "pass", "reason": "synthetic"} for axis in GP.AXES}

            receipt, run_dir = runner.run(GP.load_cases(("A01", "A07", "A10")), args(tmp),
                                             generate=generate, judge=judge)
            rows = {row["case_id"]: row for row in receipt["cases"]}
            assert rows["A01"]["disposition"] == "deterministic_fail"
            assert not [call for call in calls if call[0] == "A01"]
            assert (run_dir / "A01.raw.txt").read_text(encoding="utf-8") == "A01 answer? second question？"
            parsed = json.loads((run_dir / "receipt.json").read_text(encoding="utf-8"))
            assert parsed["owner_acceptance"] == "owner_unreviewed"
            assert parsed["merge_authorization"] == "not_granted"
            assert parsed["controlled_mutation"]["caught"] is True
            assert parsed["independent_model_families"] is False
            assert "trade-coach" not in str(run_dir)
    finally:
        runner.BASE.resolve_backend = original


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
