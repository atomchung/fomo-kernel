#!/usr/bin/env python3
"""Offline interlocks for the opt-in TradeEvaluation answer judge (#590)."""
import contextlib
import copy
import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATH = ROOT / "evals" / "judge_trade_answers.py"


def load_module():
    spec = importlib.util.spec_from_file_location("trade_answer_judge", PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def loaded_bank(judge):
    fixtures, problems = judge.load_fixtures()
    assert not problems, problems
    assert fixtures
    return fixtures


def declared_sample(answer, axes):
    expected_fail = set(answer.get("judge_fails") or ())
    return {
        axis: {
            "verdict": "fail" if axis in expected_fail else "pass",
            "reason": f"synthetic {axis} reason",
        }
        for axis in axes
    }


def test_bank_has_real_gate_and_orthogonal_witnesses():
    judge = load_module()
    fixtures = loaded_bank(judge)
    assert not judge.validate_witness_bank(fixtures)
    fixture = fixtures[0]
    answers = {answer["id"]: answer for answer in fixture["answers"]}
    expected = {
        "buried_but_synthesized": ["decision_focus"],
        "self_negating_rule_read": ["internal_consistency"],
        "prioritized_fact_recap": ["decision_synthesis"],
        "lead_then_caveat_padding": ["caveat_discipline"],
        "all_axes_pass_compact": [],
        "all_axes_pass_expanded": [],
    }
    eligible = {answer["id"]: answer for answer in judge.eligible_answers(fixture)}
    assert {key: value.get("judge_fails", []) for key, value in eligible.items()} == expected
    assert "deterministic_reject_unsupported" not in eligible
    assert "eligible_for_judge" not in json.dumps(fixture)


def test_fixture_expectation_never_decides_production_eligibility():
    judge = load_module()
    fixture = loaded_bank(judge)[0]
    rejected = next(answer for answer in fixture["answers"]
                    if answer["id"] == "deterministic_reject_unsupported")
    result = judge.deterministic_eligibility(fixture, rejected)
    assert result.eligible is False
    assert "restates what the user said as public_fact" in result.reason

    # The same prose becomes eligible when its referenced structured case is
    # production-valid. The fixture's `expect_eligible: false` has no control
    # over this function and therefore cannot become the old trusted switch.
    changed = dict(rejected, agent_case_ref="grounded_two_sided")
    assert judge.deterministic_eligibility(fixture, changed).eligible is True

    # Conversely, dropping one required production anchor rejects an answer
    # whose fixture expectation still says true.
    broken = copy.deepcopy(fixture)
    broken["agent_cases"]["grounded_two_sided"]["against"].pop(0)
    accepted = next(answer for answer in broken["answers"] if answer["expect_eligible"])
    result = judge.deterministic_eligibility(broken, accepted)
    assert result.eligible is False
    assert "basis.price_observations" in result.reason


def test_rejected_answer_makes_no_model_call_and_zero_over_zero_fails():
    judge = load_module()
    fixture = copy.deepcopy(loaded_bank(judge)[0])
    fixture["answers"] = [next(answer for answer in fixture["answers"]
                                if not answer["expect_eligible"])]
    calls = []
    receipts = []

    def should_not_run(*_args):
        calls.append(True)
        raise AssertionError("production-rejected answer reached the model")

    with contextlib.redirect_stdout(io.StringIO()):
        rc = judge.run_judge(
            [fixture], backend="stub", model="stub", sample_one=should_not_run,
            filtered=True,
            append_receipt=lambda row: receipts.append(row) or pathlib.Path("/tmp/receipt"),
        )
    assert rc == 1
    assert calls == []
    assert receipts[0]["judged_answer_count"] == 0
    assert receipts[0]["rejected_answer_count"] == 1
    assert "nothing to judge" in " ".join(receipts[0]["failures"])


def test_blind_material_contains_frozen_truth_without_fixture_labels():
    judge = load_module()
    fixture = loaded_bank(judge)[0]
    answer = fixture["answers"][0]
    text = judge.material(judge.episode_view(fixture), answer)
    assert "FICTIONAL-A" in text
    assert "The latest customer conversations sound more urgent." in text
    assert '"must_state"' in text
    assert fixture["title"] not in text
    assert "judge_fails" not in text
    assert "agent_case_ref" not in text
    assert answer["id"] not in text
    assert "deterministic_reject_unsupported" not in text
    assert judge.SYSTEM != judge.BASE.SYSTEM
    assert "Ordering, adjacency, and repetition are evidence" in judge.SYSTEM


def test_adapter_injects_its_system_rubric_and_material_into_shared_backend():
    judge = load_module()
    fixture = loaded_bank(judge)[0]
    episode = judge.episode_view(fixture)
    answer = fixture["answers"][0]
    prompt = judge.BASE._agy_prompt(
        episode, answer, judge.AXES, system=judge.SYSTEM,
        rubric=judge.RUBRIC, material_fn=judge.material)
    assert prompt.startswith(judge.SYSTEM)
    assert judge.RUBRIC["decision_focus"]["holds"] in prompt
    assert "THE FROZEN TRADE EVALUATION" in prompt
    tool = judge.BASE._tool(judge.AXES, rubric=judge.RUBRIC)
    assert tool["input_schema"]["properties"]["decision_focus"]["properties"] \
        ["verdict"]["description"] == judge.RUBRIC["decision_focus"]["one_line"]


def test_axis_report_keeps_every_vote_reason_tally_and_ambiguity():
    judge = load_module()
    axis = "decision_focus"
    samples = [
        {axis: {"verdict": "pass", "reason": "minority reason"}},
        {axis: {"verdict": "fail", "reason": "majority reason one"}},
        {axis: {"verdict": "fail", "reason": "majority reason two"}},
        None,
    ]
    report = judge.BASE.axis_report(samples, axis, "pass")
    assert report["verdict"] == "fail"
    assert report["tally"] == {"pass": 1, "fail": 2}
    assert report["unusable"] == 1
    assert report["samples"][1]["reason"] == "majority reason one"
    failures, _, whole = judge.BASE.grade_answer_report(
        {"id": "T"}, {"id": "A", "judge_fails": []}, [axis], samples)
    assert failures
    assert "majority reason one" in " ".join(failures)
    assert whole["matched"] is False

    split = judge.BASE.axis_report(samples[:2], axis, "pass")
    assert split["verdict"] == "ambiguous"
    assert split["matched"] is False


def test_a_verdict_without_a_nonempty_reason_is_not_a_usable_vote():
    judge = load_module()
    axis = "decision_focus"
    missing = json.dumps({axis: {"verdict": "pass"}})
    empty = json.dumps({axis: {"verdict": "pass", "reason": "  "}})
    assert judge.BASE._parse_verdicts(missing, [axis]) is None
    assert judge.BASE._parse_verdicts(empty, [axis]) is None

    report = judge.BASE.axis_report(
        [{axis: {"verdict": "pass"}}], axis, "pass")
    assert report["verdict"] == "ambiguous"
    assert report["unusable"] == 1
    assert report["matched"] is False


def test_agy_call_uses_argv_effort_absolute_path_and_retries_transient_errors():
    judge = load_module()
    fixture = loaded_bank(judge)[0]
    episode = judge.episode_view(fixture)
    answer = fixture["answers"][0]
    calls = []
    original = judge.BASE.subprocess.run
    verdicts = declared_sample(answer, judge.AXES)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if len(calls) < 3:
            return types.SimpleNamespace(
                returncode=1, stdout="",
                stderr="Error: Agent execution terminated due to error.")
        return types.SimpleNamespace(
            returncode=0, stdout=json.dumps(verdicts), stderr="")

    judge.BASE.subprocess.run = run
    try:
        found = judge.BASE.judge_once_agy(
            "gemini-test-high", episode, answer, judge.AXES,
            system=judge.SYSTEM, rubric=judge.RUBRIC, material_fn=judge.material)
    finally:
        judge.BASE.subprocess.run = original
    assert found == verdicts
    assert len(calls) == 3
    argv, kwargs = calls[0]
    assert argv[0] == judge.BASE.AGY_PATH
    assert os.path.isabs(argv[0])
    assert argv[argv.index("--model") + 1] == "gemini-test-high"
    assert argv[argv.index("--effort") + 1] == judge.BASE.EFFORT
    assert "THE FROZEN TRADE EVALUATION" in argv[argv.index("-p") + 1]
    assert "input" not in kwargs and kwargs["timeout"] >= 180


def test_receipt_digests_bind_both_fixture_and_judge_contract():
    judge = load_module()
    fixtures = loaded_bank(judge)
    assert judge.RECEIPTS.canonical_sha256({"b": 2, "a": 1}) == \
        judge.RECEIPTS.canonical_sha256({"a": 1, "b": 2})
    original = judge.RECEIPTS.canonical_sha256(fixtures)
    changed = copy.deepcopy(fixtures)
    changed[0]["answers"][0]["prose"] += " changed"
    assert judge.RECEIPTS.canonical_sha256(changed) != original

    contract = judge.judge_contract_digest()
    changed_rubric = copy.deepcopy(judge.RUBRIC)
    changed_rubric["decision_focus"]["holds"] += " Changed contract."
    assert judge.judge_contract_digest(rubric=changed_rubric) != contract


def test_receipt_store_is_durable_readable_and_refuses_corruption():
    judge = load_module()
    previous = os.environ.get("TRADE_COACH_HOME")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TRADE_COACH_HOME"] = tmp
            path = judge.RECEIPTS.append_receipt({"status": "pass", "run": 1})
            assert path == pathlib.Path(tmp) / "judge" / "trade-answer-runs.jsonl"
            assert judge.RECEIPTS.read_history() == [{"run": 1, "status": "pass"}]
            with path.open("a", encoding="utf-8") as handle:
                handle.write("not-json\n")
            try:
                judge.RECEIPTS.read_history()
            except judge.RECEIPTS.ReceiptError as exc:
                assert f"{path}:2" in str(exc)
            else:
                raise AssertionError("malformed receipt line was silently skipped")
    finally:
        if previous is None:
            os.environ.pop("TRADE_COACH_HOME", None)
        else:
            os.environ["TRADE_COACH_HOME"] = previous


def test_receipt_fsync_failure_is_not_evidence():
    judge = load_module()
    previous_root = os.environ.get("TRADE_COACH_HOME")
    original_fsync = judge.RECEIPTS._fsync_dir
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TRADE_COACH_HOME"] = tmp

            def fail_sync(_path):
                raise OSError("synthetic fsync failure")

            judge.RECEIPTS._fsync_dir = fail_sync
            try:
                judge.RECEIPTS.append_receipt({"status": "pass"})
            except judge.RECEIPTS.ReceiptError as exc:
                assert "synthetic fsync failure" in str(exc)
            else:
                raise AssertionError("receipt append passed without durable directory sync")
    finally:
        judge.RECEIPTS._fsync_dir = original_fsync
        if previous_root is None:
            os.environ.pop("TRADE_COACH_HOME", None)
        else:
            os.environ["TRADE_COACH_HOME"] = previous_root


def test_live_runner_records_per_axis_evidence_before_passing():
    judge = load_module()
    fixtures = loaded_bank(judge)
    receipts = []
    calls = []

    def sample(_episode, answer, axes):
        calls.append(answer["id"])
        return declared_sample(answer, axes)

    with contextlib.redirect_stdout(io.StringIO()):
        rc = judge.run_judge(
            fixtures, backend="stub", model="stub", sample_one=sample,
            append_receipt=lambda row: receipts.append(row) or pathlib.Path("/tmp/receipt"),
        )
    assert rc == 0
    assert len(calls) == 6 * judge.BASE.RUNS
    receipt = receipts[0]
    assert receipt["status"] == "pass"
    assert receipt["calibration"]["state"] == "uncalibrated"
    assert receipt["judge_contract_digest"] == judge.judge_contract_digest()
    assert receipt["judged_answer_count"] == 6
    accepted = next(row for row in receipt["answers"]
                    if row["answer_id"] == "buried_but_synthesized")
    axis = accepted["report"]["axes"]["decision_focus"]
    assert axis["expected"] == "fail" and axis["verdict"] == "fail"
    assert len(axis["samples"]) == judge.BASE.RUNS


def test_runner_fails_if_receipt_cannot_be_persisted_and_records_api_errors():
    judge = load_module()
    fixtures = loaded_bank(judge)

    def good(_episode, answer, axes):
        return declared_sample(answer, axes)

    def refuse_record(_row):
        raise judge.RECEIPTS.ReceiptError("synthetic store refusal")

    with contextlib.redirect_stdout(io.StringIO()):
        assert judge.run_judge(
            fixtures, backend="stub", model="stub", sample_one=good,
            append_receipt=refuse_record) == 1

    receipts = []

    def api_error(*_args):
        raise RuntimeError("synthetic backend refusal")

    with contextlib.redirect_stdout(io.StringIO()):
        rc = judge.run_judge(
            fixtures, backend="stub", model="stub", sample_one=api_error,
            append_receipt=lambda row: receipts.append(row) or pathlib.Path("/tmp/receipt"),
        )
    assert rc == 1
    assert receipts[0]["status"] == "fail"
    accepted = next(row for row in receipts[0]["answers"]
                    if row["eligibility"] == "accepted")
    assert accepted["sample_errors"][0]["error"] == "synthetic backend refusal"
    assert accepted["report"]["unusable_samples"] == judge.BASE.RUNS


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
