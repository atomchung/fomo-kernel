#!/usr/bin/env python3
"""Offline interlocks for the opt-in TradeEvaluation answer judge (#590)."""
import contextlib
import copy
import importlib.util
import io
import json
import os
import pathlib
import stat
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


def render_candidate_claims(payload):
    return "\n".join(
        payload["agent_case"][ref["side"]][ref["index"]]["claim"]
        for ref in payload["presented_claim_order"])


def candidate_payload(judge, fixture, *, answer_id="current-output"):
    agent_case = {
        "for": [
            {
                "claim": ("The decision is whether the user's still-unverified demand "
                          "observation justifies a temporary exception to the recorded cap."),
                "provenance": "agent_judgment",
            },
            {
                "claim": ("The user's exact premise and why-now are: Customer demand appears "
                          "stronger. The latest customer conversations sound more urgent."),
                "provenance": "agent_judgment",
            },
            {
                "claim": ("A real, durable change could make the exception deliberate; without "
                          "verification, the trade instead relaxes the rule when it becomes "
                          "inconvenient."),
                "provenance": "agent_judgment",
            },
            {
                "claim": ("The user can keep the trade open while checking the claim, resize it "
                          "to avoid the breach, or decline it."),
                "provenance": "agent_judgment",
            },
        ],
        "against": [
            {
                "claim": "The recorded book used prices observed on 2026-08-01.",
                "provenance": "engine_fact",
                "anchor": "basis.price_observations.as_of",
            },
            {
                "claim": ("FICTIONAL-A would move from 20% to 27%, a 7% increase, so the proposed "
                          "add crosses this rule: Keep any single position at or below 25% of the "
                          "recorded book."),
                "provenance": "engine_fact",
                "anchor": "rule_collisions.rule-fixture-cap.state",
            },
            {
                "claim": ("The add also puts top-three concentration at 63.5%, classified AI and "
                          "maximum-sector exposure at 27%, and cash at $10,410.96 or 8.6758%."),
                "provenance": "engine_fact",
                "anchor": "consequence.after.top3",
            },
            {
                "claim": ("Because this is not a live broker view, and liquidity, valuation, tax, "
                          "broader position fit, and whether the evidence actually changed were "
                          "not checked, the crossing identifies a decision conflict but does not "
                          "certify execution-time conditions."),
                "provenance": "agent_judgment",
            },
        ],
    }
    order = [
        {"side": "for", "index": 0},
        {"side": "against", "index": 0},
        {"side": "against", "index": 1},
        {"side": "against", "index": 2},
        {"side": "for", "index": 1},
        {"side": "for", "index": 2},
        {"side": "against", "index": 3},
        {"side": "for", "index": 3},
    ]
    return {
        "schema_version": 2,
        "fixture_id": fixture["id"],
        "answer_id": answer_id,
        "agent_case": agent_case,
        "challenge": judge._challenge(judge._frozen(fixture)),
        "presented_claim_order": order,
        "presented_text": "\n".join(
            agent_case[ref["side"]][ref["index"]]["claim"] for ref in order),
        "generator": {"host": "synthetic-test", "revision": "current"},
    }


def write_candidate(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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
    valid_prose = next(answer["prose"] for answer in fixture["answers"]
                       if answer["id"] == "all_axes_pass_expanded")
    changed = dict(rejected, agent_case_ref="grounded_two_sided", prose=valid_prose)
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
    assert report["classification"] == "ambiguous"
    assert report["matched"] is False
    assert whole["usable_samples"] == 3
    assert whole["unusable_samples"] == 1
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
    assert report["classification"] == "ambiguous"
    assert report["matched"] is False

    failures, _, whole = judge.BASE.grade_answer_report(
        {"id": "T"}, {"id": "A", "judge_fails": []}, [axis], [
            {axis: {"verdict": "pass", "reason": "one"}},
            {axis: {"verdict": "pass", "reason": "two"}},
            {axis: {"verdict": "pass", "reason": ""}},
        ])
    assert failures
    assert whole["usable_samples"] == 2
    assert whole["unusable_samples"] == 1
    assert whole["axes"][axis]["matched"] is False


def test_agy_call_uses_argv_effort_absolute_path_and_retries_transient_errors():
    judge = load_module()
    fixture = loaded_bank(judge)[0]
    episode = judge.episode_view(fixture)
    answer = fixture["answers"][0]
    calls = []
    original = judge.BASE.subprocess.run
    original_path = judge.BASE.AGY_PATH
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
    judge.BASE.AGY_PATH = "/synthetic/absolute/agy"
    try:
        found = judge.BASE.judge_once_agy(
            "gemini-test-high", episode, answer, judge.AXES,
            system=judge.SYSTEM, rubric=judge.RUBRIC, material_fn=judge.material)
    finally:
        judge.BASE.subprocess.run = original
        judge.BASE.AGY_PATH = original_path
    assert found == verdicts
    assert len(calls) == 3
    argv, kwargs = calls[0]
    assert argv[0] == "/synthetic/absolute/agy"
    assert os.path.isabs(argv[0])
    assert argv[argv.index("--model") + 1] == "gemini-test-high"
    assert argv[argv.index("--effort") + 1] == judge.BASE.EFFORT
    assert "THE FROZEN TRADE EVALUATION" in argv[argv.index("-p") + 1]
    assert "input" not in kwargs and kwargs["timeout"] >= 180


def test_agy_resolution_keeps_override_and_path_discovery_portable():
    judge = load_module()
    with tempfile.TemporaryDirectory() as tmp:
        executable = pathlib.Path(tmp) / "agy"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o700)
        resolved = os.path.abspath(executable)
        assert judge.BASE._resolve_agy_path(
            str(executable), which=lambda _name: None) == resolved
        assert judge.BASE._resolve_agy_path(
            None, which=lambda name: str(executable) if name == "agy" else None) == resolved
        missing = pathlib.Path(tmp) / "missing"
        assert judge.BASE._resolve_agy_path(
            str(missing), which=lambda _name: str(executable)) is None


def test_agy_launch_oserror_becomes_a_receiptable_runtime_error():
    judge = load_module()
    fixture = loaded_bank(judge)[0]
    episode = judge.episode_view(fixture)
    answer = fixture["answers"][0]
    original_run = judge.BASE.subprocess.run
    original_path = judge.BASE.AGY_PATH
    judge.BASE.AGY_PATH = "/synthetic/absolute/agy"
    judge.BASE.subprocess.run = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        OSError("synthetic E2BIG"))
    try:
        try:
            judge.BASE.judge_once_agy(
                "model", episode, answer, judge.AXES,
                system=judge.SYSTEM, rubric=judge.RUBRIC, material_fn=judge.material)
        except RuntimeError as exc:
            assert "synthetic E2BIG" in str(exc)
        else:
            raise AssertionError("agy launch OSError escaped the receiptable error contract")
    finally:
        judge.BASE.subprocess.run = original_run
        judge.BASE.AGY_PATH = original_path


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

    fixture = fixtures[0]
    episode = judge.episode_view(fixture)
    answer = fixture["answers"][0]
    exact_input = judge.judge_input_digest(
        episode, answer, judge.AXES, backend="agy", model="model-a")
    changed_input = judge.judge_input_digest(
        episode, answer, judge.AXES, backend="agy", model="model-a",
        material_fn=lambda ep, ans: judge.material(ep, ans) + "\nchanged")
    assert changed_input != exact_input

    structured = judge.judge_input_request(
        episode, answer, judge.AXES, backend="anthropic", model="model-a")
    call = structured["call_spec"]
    assert call["max_tokens"] == judge.BASE.STRUCTURED_MAX_TOKENS
    assert call["output_config"] == {"effort": judge.BASE.EFFORT}
    assert call["tool_choice"] == judge.BASE.STRUCTURED_TOOL_CHOICE
    assert call["messages"][0]["content"]

    original_tokens = judge.BASE.STRUCTURED_MAX_TOKENS
    original_choice = judge.BASE.STRUCTURED_TOOL_CHOICE
    try:
        baseline = judge.judge_input_digest(
            episode, answer, judge.AXES, backend="anthropic", model="model-a")
        judge.BASE.STRUCTURED_MAX_TOKENS += 1
        assert judge.judge_input_digest(
            episode, answer, judge.AXES, backend="anthropic", model="model-a") \
            != baseline
        judge.BASE.STRUCTURED_MAX_TOKENS = original_tokens
        judge.BASE.STRUCTURED_TOOL_CHOICE = {
            "type": "tool", "name": "different_tool"}
        assert judge.judge_input_digest(
            episode, answer, judge.AXES, backend="anthropic", model="model-a") \
            != baseline
    finally:
        judge.BASE.STRUCTURED_MAX_TOKENS = original_tokens
        judge.BASE.STRUCTURED_TOOL_CHOICE = original_choice


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


def test_untrusted_surrogate_reason_is_durably_receipted_after_model_calls():
    judge = load_module()
    fixture = copy.deepcopy(loaded_bank(judge)[0])
    fixture["answers"] = [next(
        answer for answer in fixture["answers"]
        if answer["id"] == "all_axes_pass_compact")]
    hostile_reason = "isolated surrogate: \ud800"
    calls = []

    def sample(_episode, answer, axes, _input_request):
        calls.append(answer["id"])
        return {axis: {"verdict": "pass", "reason": hostile_reason}
                for axis in axes}

    previous = os.environ.get("TRADE_COACH_HOME")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TRADE_COACH_HOME"] = tmp
            with contextlib.redirect_stdout(io.StringIO()):
                rc = judge.run_judge(
                    [fixture], backend="stub", model="stub", sample_one=sample,
                    filtered=True)
            assert rc == 0
            assert len(calls) == judge.BASE.RUNS
            rows = judge.RECEIPTS.read_history()
            assert len(rows) == 1
            stored = rows[0]["answers"][0]["report"]["axes"] \
                ["decision_focus"]["samples"][0]["reason"]
            assert stored == hostile_reason
            assert b"\\ud800" in judge.RECEIPTS.history_path().read_bytes()
            assert judge.RECEIPTS.canonical_sha256({"reason": hostile_reason})
    finally:
        if previous is None:
            os.environ.pop("TRADE_COACH_HOME", None)
        else:
            os.environ["TRADE_COACH_HOME"] = previous


def test_receipt_append_locks_and_refuses_an_incomplete_tail():
    judge = load_module()
    previous = os.environ.get("TRADE_COACH_HOME")
    original_flock = judge.RECEIPTS.fcntl.flock
    calls = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TRADE_COACH_HOME"] = tmp

            def observed_lock(fd, operation):
                calls.append(operation)
                return original_flock(fd, operation)

            judge.RECEIPTS.fcntl.flock = observed_lock
            judge.RECEIPTS.append_receipt({"status": "pass", "run": 1})
            assert judge.RECEIPTS.fcntl.LOCK_EX in calls
            assert judge.RECEIPTS.fcntl.LOCK_UN in calls

            path = judge.RECEIPTS.history_path()
            path.write_text('{"status":"partial"', encoding="utf-8")
            before = path.read_bytes()
            try:
                judge.RECEIPTS.append_receipt({"status": "pass", "run": 2})
            except judge.RECEIPTS.ReceiptError as exc:
                assert "incomplete row" in str(exc)
            else:
                raise AssertionError("a new receipt was appended to a torn JSON row")
            assert path.read_bytes() == before
    finally:
        judge.RECEIPTS.fcntl.flock = original_flock
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
            assert judge.RECEIPTS.read_history() == []
    finally:
        judge.RECEIPTS._fsync_dir = original_fsync
        if previous_root is None:
            os.environ.pop("TRADE_COACH_HOME", None)
        else:
            os.environ["TRADE_COACH_HOME"] = previous_root


def test_receipt_file_fsync_failure_rolls_back_the_readable_row():
    judge = load_module()
    previous_root = os.environ.get("TRADE_COACH_HOME")
    original_fsync = judge.RECEIPTS.os.fsync
    failed = False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TRADE_COACH_HOME"] = tmp

            def fail_first_file_sync(fd):
                nonlocal failed
                if stat.S_ISREG(os.fstat(fd).st_mode) and not failed:
                    failed = True
                    raise OSError("synthetic file fsync failure")
                return original_fsync(fd)

            judge.RECEIPTS.os.fsync = fail_first_file_sync
            try:
                judge.RECEIPTS.append_receipt({"status": "pass"})
            except judge.RECEIPTS.ReceiptError as exc:
                assert "synthetic file fsync failure" in str(exc)
            else:
                raise AssertionError("receipt append passed after file fsync failed")
            assert failed is True
            assert judge.RECEIPTS.read_history() == []
            assert judge.RECEIPTS.history_path().read_bytes() == b""
    finally:
        judge.RECEIPTS.os.fsync = original_fsync
        if previous_root is None:
            os.environ.pop("TRADE_COACH_HOME", None)
        else:
            os.environ["TRADE_COACH_HOME"] = previous_root


def test_live_runner_records_per_axis_evidence_before_passing():
    judge = load_module()
    fixtures = loaded_bank(judge)
    receipts = []
    calls = []

    def sample(_episode, answer, axes, _input_request):
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
    assert receipt["run_kind"] == "fixture_witness"
    assert receipt["axis_summary"]["decision_focus"]["counts"] == {
        "agreement": 6, "disagreement": 0, "ambiguous": 0, "total": 6}
    accepted = next(row for row in receipt["answers"]
                    if row["answer_id"] == "buried_but_synthesized")
    axis = accepted["report"]["axes"]["decision_focus"]
    assert axis["expected"] == "fail" and axis["verdict"] == "fail"
    assert len(axis["samples"]) == judge.BASE.RUNS
    assert accepted["delivery_fidelity"]["facts_missing"] == 0
    assert accepted["judge_input_digest"]
    assert receipt["judge_inputs"][accepted["judge_input_digest"]] \
        ["call_spec"]["model"] == "stub"


def test_stdout_closing_after_model_calls_cannot_prevent_the_receipt():
    judge = load_module()
    fixture = copy.deepcopy(loaded_bank(judge)[0])
    fixture["answers"] = [next(
        answer for answer in fixture["answers"]
        if answer["id"] == "all_axes_pass_compact")]
    calls, receipts = [], []

    def sample(_episode, answer, axes, _input_request):
        calls.append(answer["id"])
        return declared_sample(answer, axes)

    class BrokenPipe:
        def write(self, _text):
            raise BrokenPipeError("synthetic closed reader")

        def flush(self):
            pass

        def close(self):
            pass

    with contextlib.redirect_stdout(BrokenPipe()):
        rc = judge.run_judge(
            [fixture], backend="stub", model="stub", sample_one=sample,
            filtered=True,
            append_receipt=lambda row: receipts.append(row) or pathlib.Path("/tmp/receipt"),
        )
    assert rc == 0
    assert len(calls) == judge.BASE.RUNS
    assert len(receipts) == 1
    assert receipts[0]["status"] == "pass"


def test_candidate_output_changes_the_result_without_editing_the_fixture():
    judge = load_module()
    bank = loaded_bank(judge)
    fixture = bank[0]
    source_digest = judge.RECEIPTS.canonical_sha256(fixture)
    receipts = []

    def semantic_stub(_episode, answer, axes, _input_request):
        result = declared_sample(answer, axes)
        if "remains within the user's cap" in answer["prose"]:
            result["internal_consistency"] = {
                "verdict": "fail",
                "reason": "the candidate contradicts the frozen would-breach state",
            }
        return result

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "candidate.json"
        passing_payload = candidate_payload(judge, fixture)
        write_candidate(path, passing_payload)
        cli_output = io.StringIO()
        with contextlib.redirect_stdout(cli_output):
            assert judge.main(["--answer-file", str(path), "--plan"]) == 0
        assert "candidate_output" in cli_output.getvalue()
        assert "CANDIDATE current-output" in cli_output.getvalue()
        passing, loaded, problems = judge.load_candidate(path, bank)
        assert not problems, problems
        assert judge.deterministic_eligibility(passing, passing["answers"][0]).eligible
        with contextlib.redirect_stdout(io.StringIO()):
            assert judge.run_judge(
                [passing], backend="stub", model="stub", sample_one=semantic_stub,
                filtered=True, run_kind="candidate_output",
                candidate_digest=judge.RECEIPTS.canonical_sha256(loaded),
                candidate_artifact=loaded,
                source_fixture_digest=source_digest,
                append_receipt=lambda row: receipts.append(row) or pathlib.Path("/tmp/receipt"),
            ) == 0

        changed_payload = copy.deepcopy(passing_payload)
        changed_payload["answer_id"] = "current-output-contradiction"
        changed_payload["agent_case"]["against"].append({
            "claim": "Even so, the proposed add remains within the user's cap.",
            "provenance": "agent_judgment",
        })
        changed_payload["presented_claim_order"].append({
            "side": "against", "index": 4})
        changed_payload["presented_text"] = render_candidate_claims(changed_payload)
        write_candidate(path, changed_payload)
        changed, loaded_changed, problems = judge.load_candidate(path, bank)
        assert not problems, problems
        assert judge.deterministic_eligibility(changed, changed["answers"][0]).eligible
        with contextlib.redirect_stdout(io.StringIO()):
            assert judge.run_judge(
                [changed], backend="stub", model="stub", sample_one=semantic_stub,
                filtered=True, run_kind="candidate_output",
                candidate_digest=judge.RECEIPTS.canonical_sha256(loaded_changed),
                candidate_artifact=loaded_changed,
                source_fixture_digest=source_digest,
                append_receipt=lambda row: receipts.append(row) or pathlib.Path("/tmp/receipt"),
            ) == 1

    assert judge.RECEIPTS.canonical_sha256(fixture) == source_digest
    assert receipts[0]["status"] == "pass"
    assert receipts[1]["status"] == "fail"
    assert receipts[0]["source_fixture_digest"] == receipts[1]["source_fixture_digest"]
    assert receipts[0]["candidate_digest"] != receipts[1]["candidate_digest"]
    assert receipts[0]["candidate_artifact"] == passing_payload
    assert receipts[1]["candidate_artifact"] == changed_payload
    assert receipts[0]["judge_inputs"]
    first_answer = next(row for row in receipts[0]["answers"]
                        if row["eligibility"] == "accepted")
    exact_request = receipts[0]["judge_inputs"][first_answer["judge_input_digest"]]
    assert passing_payload["presented_text"] in \
        exact_request["call_spec"]["messages"][0]["content"]
    assert receipts[1]["axis_summary"]["internal_consistency"]["counts"] \
        ["disagreement"] == 1


def test_candidate_runs_production_delivery_checks_before_any_model_call():
    judge = load_module()
    bank = loaded_bank(judge)
    fixture = bank[0]
    payload = candidate_payload(judge, fixture)
    payload["agent_case"]["against"][1]["claim"] = \
        payload["agent_case"]["against"][1]["claim"].replace(
        "from 20% to 27%", "from 2% to 27%")
    payload["presented_text"] = render_candidate_claims(payload)
    calls, receipts = [], []
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "candidate.json"
        write_candidate(path, payload)
        candidate, _loaded, problems = judge.load_candidate(path, bank)
        assert not problems, problems
        eligibility = judge.deterministic_eligibility(candidate, candidate["answers"][0])
        assert eligibility.eligible is False
        assert "omits" in eligibility.reason or "traces to no engine number" in eligibility.reason
        with contextlib.redirect_stdout(io.StringIO()):
            rc = judge.run_judge(
                [candidate], backend="stub", model="stub",
                sample_one=lambda *_args: calls.append(True), filtered=True,
                run_kind="candidate_output",
                candidate_artifact=payload,
                append_receipt=lambda row: receipts.append(row) or pathlib.Path("/tmp/receipt"))
    assert rc == 1
    assert calls == []
    assert receipts[0]["judged_answer_count"] == 0
    assert receipts[0]["answers"][0]["answer_kind"] == "candidate_output"


def test_candidate_cannot_append_an_unlabelled_claim_beside_a_valid_case():
    judge = load_module()
    bank = loaded_bank(judge)
    fixture = bank[0]
    payload = candidate_payload(judge, fixture)
    payload["presented_text"] += \
        "\nManagement has independently confirmed stronger demand."

    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "candidate.json"
        write_candidate(path, payload)
        candidate, _loaded, problems = judge.load_candidate(path, bank)
        assert candidate is None
        assert any("ordered validated case claims" in problem for problem in problems)

    direct = copy.deepcopy(fixture)
    direct["answers"] = [{
        "id": "unsupported-extra-claim",
        "kind": "candidate_output",
        "agent_case": payload["agent_case"],
        "captured_challenge": payload["challenge"],
        "presented_claim_order": payload["presented_claim_order"],
        "judge_fails": [],
        "prose": payload["presented_text"],
    }]
    eligibility = judge.deterministic_eligibility(direct, direct["answers"][0])
    assert eligibility.eligible is False
    assert "ordered validated case claims" in eligibility.reason


def test_runner_fails_if_receipt_cannot_be_persisted_and_records_api_errors():
    judge = load_module()
    fixtures = loaded_bank(judge)

    def good(_episode, answer, axes, _input_request):
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
    assert accepted["sample_errors"][0]["error_type"] == "RuntimeError"
    assert accepted["sample_errors"][0]["error"] == "synthetic backend refusal"
    assert accepted["report"]["unusable_samples"] == judge.BASE.RUNS

    receipts.clear()

    def launch_error(*_args):
        raise OSError("synthetic E2BIG")

    with contextlib.redirect_stdout(io.StringIO()):
        rc = judge.run_judge(
            fixtures, backend="stub", model="stub", sample_one=launch_error,
            append_receipt=lambda row: receipts.append(row) or pathlib.Path("/tmp/receipt"),
        )
    assert rc == 1
    assert receipts[0]["status"] == "fail"
    accepted = next(row for row in receipts[0]["answers"]
                    if row["eligibility"] == "accepted")
    assert accepted["sample_errors"][0] == {
        "run": 1, "error_type": "OSError", "error": "synthetic E2BIG"}


def test_history_discloses_calibration_and_axis_outcomes():
    judge = load_module()
    original = judge.RECEIPTS.read_history
    judge.RECEIPTS.read_history = lambda: [{
        "run_at": "2026-08-01T00:00:00Z",
        "status": "pass",
        "backend": "agy",
        "model": "model",
        "scope": "full_bank",
        "run_kind": "fixture_witness",
        "fixture_ids": ["TA-001"],
        "fixture_digest": "a" * 64,
        "judge_contract_digest": "b" * 64,
        "calibration": {"state": "uncalibrated", "declared_by": ["agent"]},
        "axis_summary": {
            "decision_focus": {
                "counts": {"agreement": 5, "disagreement": 1,
                           "ambiguous": 0, "total": 6},
            },
        },
    }]
    try:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            assert judge._show_history(20) == 0
        output = stream.getvalue()
        assert "calibration=uncalibrated[agent]" in output
        assert "decision_focus:a5/d1/u0" in output
    finally:
        judge.RECEIPTS.read_history = original


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
