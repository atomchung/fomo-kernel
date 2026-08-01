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


def candidate_payload(judge, fixture, *, answer_id="current-output",
                      rule_before="20%", extra_against_claim=None):
    # Define the ordinary user-visible answer first. The structured case and
    # spans below annotate this exact surface; they do not render or rewrite it
    # into a claim-only dialect for the evaluator.
    lead = ("The decision is whether the user's still-unverified demand observation "
            "justifies a temporary exception to the recorded cap.")
    quoted_context = ("The user's exact premise and why-now are: Customer demand appears "
                      "stronger. The latest customer conversations sound more urgent.")
    synthesis = ("A real, durable change could make the exception deliberate; without "
                 "verification, the trade instead relaxes the rule when it becomes "
                 "inconvenient.")
    price_basis = "The recorded book used prices observed on 2026-08-01."
    collision = (f"FICTIONAL-A would move from {rule_before} to 27%, a 7% increase, so "
                 "the proposed add crosses this rule: Keep any single position at or "
                 "below 25% of the recorded book.")
    consequence = ("The add also puts top-three concentration at 63.5%, classified AI and "
                   "maximum-sector exposure at 27%, and cash at $10,410.96 or 8.6758%.")
    limitation = (
        "This is a declared-complete recorded snapshot rather than a live broker view, "
        "so liquidity, valuation, tax, broader position fit, and whether the evidence "
        "actually changed remain unchecked."
    )
    resolution = (
        "Your call: keep it open, decline it, or modify the size; nothing has been executed."
    )
    first_paragraph = " ".join(
        [lead, collision, *([extra_against_claim] if extra_against_claim else []), consequence])
    second_paragraph = " ".join(
        [price_basis, quoted_context, synthesis, limitation])
    presented_text = f"{first_paragraph}\n\n{second_paragraph}\n\n{resolution}"

    agent_case = {
        "for": [
            {
                "claim": lead,
                "provenance": "agent_judgment",
            },
            {
                "claim": quoted_context,
                "provenance": "agent_judgment",
            },
        ],
        "against": [
            {
                "claim": price_basis,
                "provenance": "engine_fact",
                "anchor": "basis.price_observations.as_of",
            },
            {
                "claim": collision,
                "provenance": "engine_fact",
                "anchor": "rule_collisions.rule-fixture-cap.state",
            },
            {
                "claim": consequence,
                "provenance": "engine_fact",
                "anchor": "consequence.after.top3",
            },
        ],
    }
    if extra_against_claim is not None:
        agent_case["against"].append({
            "claim": extra_against_claim,
            "provenance": "agent_judgment",
        })

    challenge = judge._challenge(judge._frozen(fixture))
    segments = []
    cursor = 0

    def add(kind, text, **fields):
        nonlocal cursor
        start = presented_text.index(text, cursor)
        assert start == cursor
        cursor = start + len(text)
        segments.append({"kind": kind, **fields, "start": start, "end": cursor})

    def claim(side, index):
        add("claim_ref", agent_case[side][index]["claim"], side=side, index=index)

    claim("for", 0)
    add("separator", " ")
    claim("against", 1)
    if extra_against_claim is not None:
        add("separator", " ")
        claim("against", len(agent_case["against"]) - 1)
    add("separator", " ")
    claim("against", 2)
    add("separator", "\n\n")
    claim("against", 0)
    add("separator", " ")
    claim("for", 1)
    add("separator", " ")
    add("connective", synthesis, provenance="agent_judgment")
    add("separator", " ")
    add("limitation", limitation,
        obligation_refs=[
            "must_state[0]", "must_state[3]",
            *[f"unchecked.{key}" for key in challenge["unchecked"]],
        ])
    add("separator", "\n\n")
    add("resolution", resolution,
        workflow_options=["open", "declined", "modified"])
    assert cursor == len(presented_text)
    assert "".join(presented_text[item["start"]:item["end"]]
                   for item in segments) == presented_text

    return {
        "schema_version": 3,
        "fixture_id": fixture["id"],
        "answer_id": answer_id,
        "agent_case": agent_case,
        "challenge": challenge,
        "segments": segments,
        "presented_text": presented_text,
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


def test_torn_or_malformed_receipt_history_stops_the_run_before_model_calls():
    judge = load_module()
    fixture = copy.deepcopy(loaded_bank(judge)[0])
    fixture["answers"] = [next(
        answer for answer in fixture["answers"]
        if answer["id"] == "all_axes_pass_compact")]
    previous = os.environ.get("TRADE_COACH_HOME")
    calls = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TRADE_COACH_HOME"] = tmp
            path = judge.RECEIPTS.history_path()
            path.parent.mkdir(parents=True)
            for unsafe in ('{"status":"partial"', "not-json\n"):
                path.write_text(unsafe, encoding="utf-8")
                before = path.read_bytes()
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = judge.run_judge(
                        [fixture], backend="stub", model="stub",
                        sample_one=lambda *_args: calls.append(True), filtered=True)
                assert rc == 1
                assert calls == []
                assert path.read_bytes() == before
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


def test_normal_candidate_output_round_trips_exactly_and_changes_without_fixture_edits():
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
        assert "\n\n" in passing_payload["presented_text"]
        assert passing_payload["presented_text"].endswith(
            "Your call: keep it open, decline it, or modify the size; "
            "nothing has been executed.")
        assert {segment["kind"] for segment in passing_payload["segments"]} == {
            "claim_ref", "connective", "limitation", "resolution", "separator"}
        write_candidate(path, passing_payload)
        cli_output = io.StringIO()
        with contextlib.redirect_stdout(cli_output):
            assert judge.main(["--answer-file", str(path), "--plan"]) == 0
        assert "candidate_output" in cli_output.getvalue()
        assert "CANDIDATE current-output" in cli_output.getvalue()
        passing, loaded, problems = judge.load_candidate(path, bank)
        assert not problems, problems
        assert loaded["presented_text"] == passing_payload["presented_text"]
        assert passing["answers"][0]["prose"] == passing_payload["presented_text"]
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

        changed_payload = candidate_payload(
            judge, fixture, answer_id="current-output-contradiction",
            extra_against_claim="Even so, the proposed add remains within the user's cap.")
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
    payload = candidate_payload(judge, fixture, rule_before="2%")
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


def test_candidate_accepts_the_display_magnitude_of_a_negative_engine_delta():
    judge = load_module()
    bank = loaded_bank(judge)
    fixture = bank[0]
    payload = candidate_payload(
        judge, fixture,
        extra_against_claim="The trade reduces recorded cash by $9,589.04.")
    claim = payload["agent_case"]["against"][-1]
    claim["provenance"] = "engine_fact"
    claim["anchor"] = "consequence.delta.cash.balance"
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "candidate.json"
        write_candidate(path, payload)
        candidate, _loaded, problems = judge.load_candidate(path, bank)
    assert not problems, problems
    eligibility = judge.deterministic_eligibility(candidate, candidate["answers"][0])
    assert eligibility.eligible, eligibility.reason


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
        assert any("trailing text is unbound" in problem for problem in problems)

    direct = copy.deepcopy(fixture)
    direct["answers"] = [{
        "id": "unsupported-extra-claim",
        "kind": "candidate_output",
        "agent_case": payload["agent_case"],
        "captured_challenge": payload["challenge"],
        "segments": payload["segments"],
        "judge_fails": [],
        "prose": payload["presented_text"],
    }]
    eligibility = judge.deterministic_eligibility(direct, direct["answers"][0])
    assert eligibility.eligible is False
    assert "trailing text is unbound" in eligibility.reason


def test_candidate_receipt_binds_every_surface_the_model_will_see():
    judge = load_module()
    bank = loaded_bank(judge)
    fixture = bank[0]
    payload = candidate_payload(judge, fixture)
    direct = copy.deepcopy(fixture)
    answer = {
        "id": payload["answer_id"],
        "kind": "candidate_output",
        "agent_case": payload["agent_case"],
        "captured_challenge": payload["challenge"],
        "segments": copy.deepcopy(payload["segments"]),
        "judge_fails": [],
        "prose": payload["presented_text"],
        "presented_options": [{
            "label": "", "description": "open, decline, or modify",
        }],
    }
    # Make the direct candidate structurally eligible over its full visible
    # surface while leaving the artifact's presented_text smaller. Before the
    # exact-surface interlock, the receipt hashed only `prose` even though the
    # judge input included this extra option description.
    old_resolution = answer["segments"][-1]
    old_resolution.pop("workflow_options")
    old_resolution["kind"] = "connective"
    old_resolution["provenance"] = "agent_judgment"
    extra_start = len(answer["prose"])
    extra_text = "open, decline, or modify"
    answer["segments"].extend([
        {"kind": "separator", "start": extra_start, "end": extra_start + 1},
        {
            "kind": "resolution",
            "workflow_options": ["open", "declined", "modified"],
            "start": extra_start + 1,
            "end": extra_start + 1 + len(extra_text),
        },
    ])
    direct["answers"] = [answer]
    artifact = copy.deepcopy(payload)
    artifact["segments"] = copy.deepcopy(answer["segments"])
    assert judge.deterministic_eligibility(direct, answer).eligible

    calls = []
    try:
        judge.run_judge(
            [direct], backend="stub", model="stub",
            sample_one=lambda *_args: calls.append(True), filtered=True,
            run_kind="candidate_output", candidate_artifact=artifact,
            append_receipt=lambda _row: pathlib.Path("/tmp/receipt"))
    except ValueError as exc:
        assert "presented_text" in str(exc)
    else:
        raise AssertionError("a smaller candidate artifact reached the model")
    assert calls == []


def test_candidate_answer_cannot_use_the_fixture_witness_run_kind():
    judge = load_module()
    bank = loaded_bank(judge)
    payload = candidate_payload(judge, bank[0])
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "candidate.json"
        write_candidate(path, payload)
        candidate, _loaded, problems = judge.load_candidate(path, bank)
    assert not problems, problems
    calls = []
    try:
        judge.run_judge(
            [candidate], backend="stub", model="stub",
            sample_one=lambda *_args: calls.append(True), filtered=True,
            append_receipt=lambda _row: pathlib.Path("/tmp/receipt"))
    except ValueError as exc:
        assert "run_kind=candidate_output" in str(exc)
    else:
        raise AssertionError("a candidate ran under fixture_witness receipt semantics")
    assert calls == []


def test_candidate_segments_reject_span_and_obligation_mutations():
    judge = load_module()
    bank = loaded_bank(judge)
    fixture = bank[0]
    original = candidate_payload(judge, fixture)

    gap = copy.deepcopy(original)
    gap["segments"][1]["start"] += 1
    overlap = copy.deepcopy(original)
    overlap["segments"][1]["start"] -= 1
    out_of_range = copy.deepcopy(original)
    out_of_range["segments"][-1]["end"] += 1
    bool_offset = copy.deepcopy(original)
    bool_offset["segments"][0]["start"] = False
    unknown_kind = copy.deepcopy(original)
    unknown_kind["segments"][1]["kind"] = "free_prose"
    unknown_obligation = copy.deepcopy(original)
    limitation = next(segment for segment in unknown_obligation["segments"]
                      if segment["kind"] == "limitation")
    limitation["obligation_refs"][0] = "unchecked.fabricated_fact"
    bad_resolution = copy.deepcopy(original)
    resolution = next(segment for segment in bad_resolution["segments"]
                      if segment["kind"] == "resolution")
    resolution["workflow_options"] = ["open", "acted", "modified"]
    missing_resolution_markers = copy.deepcopy(original)
    resolution = next(segment for segment in missing_resolution_markers["segments"]
                      if segment["kind"] == "resolution")
    replacement = "This paragraph merely ends the answer and gives no available choice."
    missing_resolution_markers["presented_text"] = (
        missing_resolution_markers["presented_text"][:resolution["start"]]
        + replacement
    )
    resolution["end"] = len(missing_resolution_markers["presented_text"])
    duplicate_claim = copy.deepcopy(original)
    claim_segments = [segment for segment in duplicate_claim["segments"]
                      if segment["kind"] == "claim_ref"]
    claim_segments[1]["side"] = claim_segments[0]["side"]
    claim_segments[1]["index"] = claim_segments[0]["index"]
    omitted_claim = copy.deepcopy(original)
    first_claim = next(segment for segment in omitted_claim["segments"]
                       if segment["kind"] == "claim_ref")
    first_claim.clear()
    first_claim.update({
        "kind": "connective", "provenance": "agent_judgment",
        "start": 0, "end": original["segments"][0]["end"],
    })
    mismatched_claim = copy.deepcopy(original)
    mismatched_claim["agent_case"]["for"][0]["claim"] += " Changed."
    bad_connective = copy.deepcopy(original)
    connective = next(segment for segment in bad_connective["segments"]
                      if segment["kind"] == "connective")
    connective["provenance"] = "engine_fact"

    def blank_span(payload, kind):
        segment = next(item for item in payload["segments"] if item["kind"] == kind)
        text = payload["presented_text"]
        payload["presented_text"] = (
            text[:segment["start"]]
            + " " * (segment["end"] - segment["start"])
            + text[segment["end"]:]
        )

    blank_limitation = copy.deepcopy(original)
    blank_span(blank_limitation, "limitation")
    blank_resolution = copy.deepcopy(original)
    blank_span(blank_resolution, "resolution")

    nonfinal_resolution = copy.deepcopy(original)
    limitation = next(segment for segment in nonfinal_resolution["segments"]
                      if segment["kind"] == "limitation")
    resolution = next(segment for segment in nonfinal_resolution["segments"]
                      if segment["kind"] == "resolution")
    refs = limitation.pop("obligation_refs")
    options = resolution.pop("workflow_options")
    limitation["kind"] = "resolution"
    limitation["workflow_options"] = options
    resolution["kind"] = "limitation"
    resolution["obligation_refs"] = refs

    mutations = {
        "gap": gap,
        "overlap": overlap,
        "out_of_range": out_of_range,
        "bool_offset": bool_offset,
        "unknown_kind": unknown_kind,
        "unknown_obligation": unknown_obligation,
        "bad_resolution": bad_resolution,
        "missing_resolution_markers": missing_resolution_markers,
        "duplicate_claim": duplicate_claim,
        "omitted_claim": omitted_claim,
        "mismatched_claim": mismatched_claim,
        "bad_connective": bad_connective,
        "blank_limitation": blank_limitation,
        "blank_resolution": blank_resolution,
        "nonfinal_resolution": nonfinal_resolution,
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "candidate.json"
        for name, payload in mutations.items():
            write_candidate(path, payload)
            candidate, _loaded, problems = judge.load_candidate(path, bank)
            assert candidate is None, name
            assert problems, name


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
