#!/usr/bin/env python3
"""Offline contract tests for Issue #718's one synthetic-user walk.

The walk's job is not to pass. It is to leave an honest record: the visible
turns in order, an attempt that stays counted whatever happens to it, and a
partial trace when the harness dies mid-trajectory. These tests hold that
record to its contract, including on the paths where the run fails.
"""
import json
import pathlib
import subprocess
import sys
import tempfile
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import offline_posture  # noqa: E402
offline_posture.apply()

RUNNER = ROOT / "qa" / "run_synthetic_walk.py"
SCENARIOS = ROOT / "qa" / "scenarios"
sys.path.insert(0, str(ROOT / "qa"))
import run_synthetic_walk as runner  # noqa: E402
from turn_trace import RunLedger, TraceError, TurnTrace, build_report, classify_surface  # noqa: E402


def run(*args):
    return subprocess.run([sys.executable, str(RUNNER), *args], cwd=ROOT,
                          text=True, capture_output=True, timeout=180)


def walk(tmp, *extra):
    result = run("consider-ai-momentum", "--user-backend", "stub", "--no-semantic-judge",
                 "--output-dir", tmp, *extra)
    return result, json.loads(result.stdout)


def raw_trace(tmp):
    lines = (pathlib.Path(tmp) / "turn-trace.local.jsonl").read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    return [row for row in rows if row.get("stage") == "setup"], [row for row in rows if "index" in row]


def test_plan_has_no_model_calls():
    result = run("consider-ai-momentum", "--user-backend", "stub", "--plan")
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["scenario"] == "consider-ai-momentum"
    assert plan["route"] == "consider" and plan["model_calls"] == 0
    assert plan["visible_turns"] == 3 and plan["consider_invocations"] == 2


def test_stub_walk_keeps_the_real_route_but_reports_uncaptured_production_answer():
    with tempfile.TemporaryDirectory() as tmp:
        result, report = walk(tmp)
        assert result.returncode == 0, result.stdout + result.stderr
        assert {key: report[key] for key in ("workflow", "deterministic", "semantic_judge", "owner_acceptance")} == {
            "workflow": "pass", "deterministic": "pass", "semantic_judge": "skipped",
            "owner_acceptance": "owner_unreviewed"}
        assert report["production_answer_capture"] == "unavailable"
        assert report["ux_receipt_status"] == "incomplete_product_delivery"
        assert report["candidate_artifact"] is None
        assert not (pathlib.Path(tmp) / "captured-product-surface.json").exists()


def test_invalid_synthetic_action_fails_before_route_or_judge():
    from synthetic_user import SyntheticUserError, parse_action
    try:
        parse_action({"action_type": "provide_text", "text": "invented fact"},
                     allowed_actions={"provide_text"}, allowed_text={"declared fact"})
    except SyntheticUserError:
        return
    raise AssertionError("undeclared synthetic text was accepted")


def test_semantic_timeout_preserves_a_combined_receipt_state():
    with mock.patch.object(runner.subprocess, "run",
                           side_effect=subprocess.TimeoutExpired(cmd="judge", timeout=1)):
        assert runner._semantic_judge({}, pathlib.Path("candidate.json"),
                                      pathlib.Path("source.json")) == "unavailable"


# --- the ordered visible trace ------------------------------------------------

def test_the_walk_records_three_ordered_visible_turns_around_the_real_route():
    with tempfile.TemporaryDirectory() as tmp:
        _, report = walk(tmp)
        assert report["turn_count"] == 3 and report["correction_turn_count"] == 1
        assert [turn["role"] for turn in report["turn_index"]] == ["assistant", "user", "assistant"]
        assert [turn["message_type"] for turn in report["turn_index"]] == [
            "decision_result", "correction", "decision_result"]
        assert [turn["index"] for turn in report["turn_index"]] == [1, 2, 3]
        assert [turn["route_state"] for turn in report["turn_index"]] == [
            "considered", "correction_received", "reconsidered"]
        # the route really ran twice, and the second run is the corrected one
        assert report["route_transitions"] == ["qa_context", "considered", "correction_received", "reconsidered"]
        assert report["final_resolution"] == "open"
        setup, turns = raw_trace(tmp)
        assert len(setup) == 2 and len(turns) == 3
        assert all(turn["text"].strip() for turn in turns)


def test_the_public_report_carries_digests_and_never_surface_text():
    with tempfile.TemporaryDirectory() as tmp:
        _, report = walk(tmp)
        blob = json.dumps(report, ensure_ascii=False)
        _, turns = raw_trace(tmp)
        for turn in turns:
            assert turn["text"][:40] not in blob, "a raw surface reached the public report"
        for entry in report["turn_index"]:
            assert "text" not in entry
            assert len(entry["surface_sha256"]) == 64 and entry["chars"] > 0
        assert len(report["ordered_surface_digest"]) == 64


def test_reordering_the_same_turns_changes_the_ordered_surface_digest():
    def trace_of(*texts):
        trace = TurnTrace()
        for text in texts:
            trace.record(role="assistant", message_type="decision_result", text=text,
                         provenance="fixture", route_state="considered")
        return trace.ordered_surface_digest
    assert trace_of("first", "second") != trace_of("second", "first")
    assert trace_of("first", "second") == trace_of("first", "second")


def test_a_trace_records_only_visible_roles_types_and_provenance():
    trace = TurnTrace()
    for bad in ({"role": "system"}, {"message_type": "musing"}, {"provenance": "chain_of_thought"},
                {"text": "   "}, {"route_state": ""}):
        kwargs = {"role": "assistant", "message_type": "decision_result", "text": "a surface",
                  "provenance": "fixture", "route_state": "considered"}
        kwargs.update(bad)
        try:
            trace.record(**kwargs)
        except TraceError:
            continue
        raise AssertionError(f"trace accepted {bad}")


# --- honest attempt accounting -------------------------------------------------

def _accounting_holds(report):
    assert report["campaigns_started"] == 1
    assert report["route_runs_started"] == 1
    settled = report["product_passes"] + report["product_failures"]
    assert settled + report["harness_incomplete"] == report["route_runs_started"]
    assert report["route_runs_terminal"] == settled
    assert report["stop_reason"]


def test_a_started_route_run_is_counted_before_the_first_turn_exists():
    ledger = RunLedger()
    ledger.start_campaign()
    ledger.start_route()
    report = build_report(ledger=ledger, trace=TurnTrace(), base={})
    assert report["harness_incomplete"] == 1 and report["route_runs_terminal"] == 0
    assert report["workflow"] == "incomplete" and report["stop_reason"] == "in_progress"
    assert report["turn_count"] == 0


def test_the_ledger_refuses_to_settle_a_run_that_is_not_open():
    ledger = RunLedger()
    ledger.start_campaign()
    ledger.start_route()
    ledger.settle("product_fail", stop_reason="process_narration")
    for call in (lambda: ledger.settle("product_pass", stop_reason="again"),
                 lambda: ledger.settle("looks_fine", stop_reason="unknown verdict")):
        try:
            call()
        except TraceError:
            continue
        raise AssertionError("the ledger settled a run it should have refused")


def test_both_a_pass_and_a_failure_stay_in_the_denominator():
    with tempfile.TemporaryDirectory() as tmp:
        _, passing = walk(tmp)
    with tempfile.TemporaryDirectory() as tmp:
        result, failing = walk(tmp, "--surface-script", "mutated")
    _accounting_holds(passing)
    _accounting_holds(failing)
    assert (passing["product_passes"], passing["product_failures"]) == (1, 0)
    assert (failing["product_passes"], failing["product_failures"]) == (1 - 1, 1)
    assert result.returncode == 1, "a product failure must not exit like a pass"


def test_an_unlisted_harness_error_never_exits_like_a_product_failure():
    """Exit code 1 is reserved for a product verdict, so nothing else may take it."""
    import contextlib
    import io
    captured = io.StringIO()
    with mock.patch.object(runner, "run_walk", side_effect=json.JSONDecodeError("bad", "doc", 0)):
        with contextlib.redirect_stdout(captured):
            code = runner.main(["consider-ai-momentum", "--user-backend", "stub"])
    assert code == 2, "an unlisted harness fault must not exit like a failing product"
    report = json.loads(captured.getvalue())
    assert report["stop_reason"] == "harness_error:JSONDecodeError"
    assert report["campaigns_started"] == 1 and report["product_failures"] == 0


def test_a_harness_error_before_the_route_still_reports_a_started_campaign():
    result = run("consider-ai-momentum", "--user-backend", "stub", "--surface-script",
                 str(SCENARIOS / "does-not-exist.json"))
    assert result.returncode == 2, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["campaigns_started"] == 1
    assert report["route_runs_started"] == 0 and report["harness_incomplete"] == 0
    assert report["stop_reason"].startswith("harness_error:")
    assert report["workflow"] == "fail" and report["owner_acceptance"] == "owner_unreviewed"


# --- the controlled regression -------------------------------------------------

def test_the_mutation_changes_only_the_final_assistant_surface():
    baseline = json.loads((SCENARIOS / "consider-ai-momentum-correction.json").read_text(encoding="utf-8"))
    mutated = json.loads((SCENARIOS / "consider-ai-momentum-correction-mutated.json").read_text(encoding="utf-8"))
    assert baseline["turns"][:2] == mutated["turns"][:2], "the mutation moved something other than turn three"
    assert baseline["turns"][2]["text"] != mutated["turns"][2]["text"]
    assert baseline["policy"] == mutated["policy"]


def test_implementation_narration_is_a_product_surface_failure_with_all_turns_kept():
    with tempfile.TemporaryDirectory() as tmp:
        result, report = walk(tmp, "--surface-script", "mutated")
        assert result.returncode == 1
        assert report["product_failures"] == 1 and report["product_passes"] == 0
        assert report["harness_incomplete"] == 0, "a bad answer is a product failure, not an incomplete run"
        assert report["stop_reason"] == "process_narration"
        assert report["turn_count"] == 3 and report["correction_turn_count"] == 1
        assert [turn["message_type"] for turn in report["turn_index"]] == [
            "decision_result", "correction", "process_error"]
        final = report["surface_findings"][-1]
        assert final["turn"] == 3 and final["verdict"] == "product_fail"
        assert final["process_narration_markers"] and not final["delivers_decision_result"]
        _, turns = raw_trace(tmp)
        assert len(turns) == 3, "the failing run must keep every visible turn"


def test_classification_reads_the_surface_not_the_fixture_label():
    engine = {"instrument": "FICTIONAL-A", "resolutions": ["open", "modified", "declined"]}
    delivered = classify_surface(
        "FICTIONAL-A grows into an already concentrated book; the decision stays open.", **engine)
    narrated = classify_surface(
        "I found the root cause and will fix it on the next pass.", **engine)
    silent = classify_surface("Understood, thanks for the extra detail.", **engine)
    assert delivered["verdict"] == "product_pass"
    assert narrated["verdict"] == "product_fail" and narrated["reason"] == "process_narration"
    # the structural signal catches a surface that names no marker at all
    assert silent["verdict"] == "product_fail" and silent["reason"] == "no_decision_result_delivered"


# --- partial failure retention -------------------------------------------------

def _raise_on_second_consider(exc):
    real = runner._consider
    seen = []

    def wrapper(*args, **kwargs):
        seen.append(1)
        if len(seen) == 2:
            raise exc
        return real(*args, **kwargs)
    return wrapper


def test_a_crash_after_the_correction_keeps_a_partial_trace_and_stays_incomplete():
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(runner, "_consider",
                               _raise_on_second_consider(RuntimeError("harness died"))):
            try:
                runner.run_walk(user_backend="stub", output_dir=tmp, surface_script="baseline")
            except RuntimeError:
                pass
            else:
                raise AssertionError("the injected crash did not propagate")
        report = json.loads((pathlib.Path(tmp) / "combined-receipt.json").read_text(encoding="utf-8"))
        _accounting_holds(report)
        assert report["harness_incomplete"] == 1
        assert report["product_passes"] == 0 and report["product_failures"] == 0
        assert report["route_runs_terminal"] == 0 and report["workflow"] == "incomplete"
        assert report["stop_reason"] == "harness_error:RuntimeError"
        assert report["turn_count"] == 2 and report["correction_turn_count"] == 1
        setup, turns = raw_trace(tmp)
        assert len(setup) == 2 and len(turns) == 2, "the partial trace was lost"
        assert [turn["message_type"] for turn in turns] == ["decision_result", "correction"]


def test_the_cli_reports_the_real_ledger_of_a_run_that_died_mid_trajectory():
    """stdout is the record. It may not say a run that started never began."""
    import contextlib
    import io
    captured = io.StringIO()
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(runner, "_consider", _raise_on_second_consider(RuntimeError("died"))):
            with contextlib.redirect_stdout(captured):
                code = runner.main(["consider-ai-momentum", "--user-backend", "stub",
                                    "--output-dir", tmp])
        assert code == 2
        report = json.loads(captured.getvalue())
        _accounting_holds(report)
        assert report["route_runs_started"] == 1, "the CLI reported a started run as never begun"
        assert report["harness_incomplete"] == 1 and report["turn_count"] == 2
        assert report["stop_reason"] == "harness_error:RuntimeError"
        on_disk = json.loads((pathlib.Path(tmp) / "combined-receipt.json").read_text(encoding="utf-8"))
        assert on_disk["stop_reason"] == report["stop_reason"], "stdout and disk disagree"


def test_a_verdict_whose_write_failed_is_never_announced_as_recorded():
    """The reported state must be a state that reached disk, not one that tried."""
    real = runner._write

    def fails_on_the_settled_report(path, value):
        if isinstance(value, dict) and value.get("product_passes"):
            raise OSError("disk went away")
        return real(path, value)
    with tempfile.TemporaryDirectory() as tmp:
        sink = {}
        with mock.patch.object(runner, "_write", fails_on_the_settled_report):
            try:
                runner.run_walk(user_backend="stub", output_dir=tmp,
                                surface_script="baseline", report_sink=sink)
            except OSError:
                pass
            else:
                raise AssertionError("the failing write did not propagate")
        report = sink["report"]
        _accounting_holds(report)
        assert report["product_passes"] == 0, "a verdict that never reached disk was announced"
        assert report["harness_incomplete"] == 1 and report["route_runs_started"] == 1
        on_disk = json.loads((pathlib.Path(tmp) / "combined-receipt.json").read_text(encoding="utf-8"))
        assert on_disk["product_passes"] == 0


def test_the_public_error_report_never_carries_engine_output():
    import contextlib
    import io
    captured = io.StringIO()
    secret = "PRIVATE-ENGINE-STDERR-abc123"
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(runner, "_consider",
                               _raise_on_second_consider(RuntimeError(secret))):
            with contextlib.redirect_stdout(captured):
                runner.main(["consider-ai-momentum", "--user-backend", "stub", "--output-dir", tmp])
        assert secret not in captured.getvalue(), "raw engine output reached the public receipt"
        report = json.loads(captured.getvalue())
        assert report["error_type"] == "RuntimeError"
        detail = pathlib.Path(report["harness_diagnostics"]).read_text(encoding="utf-8")
        assert secret in detail, "the detail was dropped instead of routed"


def test_a_script_cannot_invent_a_resolution_word_the_engine_never_had():
    assert set(runner.engine_decisions()) >= {"open", "declined", "modified"}
    baseline = json.loads((SCENARIOS / "consider-ai-momentum-correction.json").read_text(encoding="utf-8"))
    forged = json.loads(json.dumps(baseline))
    forged["policy"]["terminal_options"] = ["banana"]
    forged["turns"][2]["text"] = "FICTIONAL-A banana"
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "forged.json"
        path.write_text(json.dumps(forged), encoding="utf-8")
        try:
            runner.load_surface_script(str(path))
        except runner.WalkError:
            return
    raise AssertionError("a fixture manufactured a passing decision from an invented word")


def test_the_walk_invokes_the_real_route_twice():
    """The --plan count is a constant; this is the one that would notice."""
    calls = []
    real = runner._consider

    def counted(*args, **kwargs):
        calls.append(kwargs.get("name"))
        return real(*args, **kwargs)
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(runner, "_consider", counted):
            runner.run_walk(user_backend="stub", output_dir=tmp, surface_script="baseline")
    assert calls == ["decision-context.json", "decision-context-corrected.json"], calls
    plan = json.loads(run("consider-ai-momentum", "--user-backend", "stub", "--plan").stdout)
    assert plan["consider_invocations"] == len(calls)


def test_machine_detail_goes_to_its_own_artifact_and_never_into_a_visible_surface():
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(runner, "_consider",
                               _raise_on_second_consider(runner.WalkError("Traceback: engine stderr"))):
            report = runner.run_walk(user_backend="stub", output_dir=tmp, surface_script="baseline")
        _accounting_holds(report)
        assert report["harness_incomplete"] == 1 and report["stop_reason"] == "route_process_failed"
        assert report["product_passes"] == 0 and report["product_failures"] == 0
        assert report["turn_count"] == 3
        _, turns = raw_trace(tmp)
        assert turns[-1]["message_type"] == "process_error" and turns[-1]["provenance"] == "harness"
        assert turns[-1]["text"] == runner.ROUTE_FAILURE_SURFACE
        assert "engine stderr" not in turns[-1]["text"]
        diagnostics = json.loads(pathlib.Path(report["harness_diagnostics"]).read_text(encoding="utf-8"))
        assert "engine stderr" in diagnostics["error"] and diagnostics["stage"] == "corrected_evaluation"


# --- what a surface script may not do ------------------------------------------

def test_a_surface_script_cannot_relabel_reshape_or_misquote_the_trajectory():
    baseline = json.loads((SCENARIOS / "consider-ai-momentum-correction.json").read_text(encoding="utf-8"))
    broken = []

    def variant(mutate):
        script = json.loads(json.dumps(baseline))
        mutate(script)
        return script

    broken.append(variant(lambda s: s["turns"].pop()))
    broken.append(variant(lambda s: s["turns"][2].update(message_type="decision_result")))
    broken.append(variant(lambda s: s["turns"][1]["corrected_context"].update(reason="something else")))
    broken.append(variant(lambda s: s["turns"][0].update(provenance="harness")))
    broken.append(variant(lambda s: s["policy"].update(terminal_options=[])))
    with tempfile.TemporaryDirectory() as tmp:
        for index, script in enumerate(broken):
            path = pathlib.Path(tmp) / f"broken-{index}.json"
            path.write_text(json.dumps(script), encoding="utf-8")
            try:
                runner.load_surface_script(str(path))
            except runner.WalkError:
                continue
            raise AssertionError(f"the loader accepted broken script {index}")


def test_the_committed_scripts_are_fictional_and_load():
    for name in ("consider-ai-momentum-correction", "consider-ai-momentum-correction-mutated"):
        script = runner.load_surface_script(str(SCENARIOS / f"{name}.json"))
        assert script["data_class"] == "fictional"
        blob = json.dumps(script, ensure_ascii=False)
        assert "FICTIONAL-" in blob
        for turn in script["turns"]:
            assert "chain_of_thought" not in turn and "tool_log" not in turn


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_") and callable(case):
            case()
            print(f"  {name}: ok")
    print("synthetic walk tests: ok")
