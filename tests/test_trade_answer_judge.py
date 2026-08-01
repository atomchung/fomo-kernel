#!/usr/bin/env python3
"""Offline interlocks for the opt-in TradeEvaluation answer judge (#590)."""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATH = ROOT / "evals" / "judge_trade_answers.py"


def load_module():
    spec = importlib.util.spec_from_file_location("trade_answer_judge", PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_bank_has_orthogonal_pass_and_fail_witnesses():
    judge = load_module()
    fixtures, problems = judge.load_fixtures()
    assert not problems, problems
    assert not judge.validate_witness_bank(fixtures)
    assert fixtures


def test_unsupported_answer_is_not_eligible_for_model_calls():
    judge = load_module()
    fixtures, problems = judge.load_fixtures({"TA-001"})
    assert not problems, problems
    fixture = fixtures[0]
    skipped = [a for a in fixture["answers"] if not a.get("eligible_for_judge", True)]
    eligible = judge.eligible_answers(fixture)
    assert [a["id"] for a in skipped] == ["compact_but_unsupported"]
    assert all(a["id"] != "compact_but_unsupported" for a in eligible)
    assert skipped[0]["ineligible_reason"].startswith("deterministic truth and coverage gate failed")


def test_each_axis_is_semantically_separated_by_a_single_failure_witness():
    judge = load_module()
    fixtures, problems = judge.load_fixtures({"TA-001"})
    assert not problems, problems
    answers = {a["id"]: a for a in judge.eligible_answers(fixtures[0])}
    expected = {
        "complete_but_flat": ["decision_focus"],
        "contextually_inconsistent": ["internal_consistency"],
        "translated_fact_list": ["decision_synthesis"],
        "disclaimer_dominant": ["caveat_discipline"],
        "supported_synthesis": [],
        "necessarily_longer_pass": [],
    }
    assert {key: value.get("judge_fails", []) for key, value in answers.items()} == expected


def test_adapter_reuses_existing_judge_primitives():
    judge = load_module()
    assert judge.BASE.resolve_backend
    assert judge.BASE.judge_once_agy
    assert judge.BASE.judge_once
    assert judge.BASE.grade_answer
    assert judge.BASE.vote


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = []
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:  # noqa: BLE001 - standalone stdlib test runner
            failures.append((test.__name__, exc))
            print(f"FAIL {test.__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
