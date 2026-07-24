#!/usr/bin/env python3
"""Focused checks for the non-UX automated QA preflight command."""

import contextlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills" / "fomo-kernel" / "tools" / "qa_preflight.py"

module_spec = importlib.util.spec_from_file_location("qa_preflight", TOOL)
qa_preflight = importlib.util.module_from_spec(module_spec)
assert module_spec.loader is not None
sys.modules[module_spec.name] = qa_preflight
module_spec.loader.exec_module(qa_preflight)


def test_base_report_never_claims_formal_or_host_ux():
    with mock.patch.object(qa_preflight, "_git", side_effect=("head", "main")):
        report = qa_preflight._base_report(ROOT, "unverified")
    assert report["formal_qa"] is False
    assert report["human_involvement"] == "agent_simulated"
    assert report["ux_evidence"] == "not_assessed"
    assert report["receipt"] == "not_created"
    assert report["revision"] == {
        "head": "head",
        "origin_main": "main",
        "head_matches_origin_main": False,
        "remote_freshness": "unverified",
    }


def test_status_survives_a_checkout_with_no_resolvable_origin_main():
    """A shallow or narrow-refspec checkout -- this repo's own CI included --
    may never populate the origin/main remote-tracking ref. status promises
    to work network-free on whatever is already on disk, so a missing ref
    must report origin_main:None, not raise."""
    def flaky_git(_root, *args):
        if args[-1] == "origin/main":
            raise qa_preflight.PreflightError("fatal: Needed a single revision")
        return "head"
    with mock.patch.object(qa_preflight, "_git", side_effect=flaky_git):
        report = qa_preflight._base_report(ROOT, "unverified")
    assert report["revision"] == {
        "head": "head",
        "origin_main": None,
        "head_matches_origin_main": False,
        "remote_freshness": "unverified",
    }


def test_refresh_falls_back_to_fetch_head_when_origin_main_ref_never_updates():
    """Right after a fetch, FETCH_HEAD is always set -- unlike origin/main,
    which depends on the checkout's fetch refspec actually mapping remote
    branches. A refreshed report must not go blind just because that mapping
    is absent (the exact CI failure test_cli_status_prints_json caught)."""
    def flaky_git(_root, *args):
        if args[-1] == "origin/main":
            raise qa_preflight.PreflightError("fatal: Needed a single revision")
        if args[-1] == "FETCH_HEAD":
            return "fetched"
        return "head"
    with mock.patch.object(qa_preflight, "_git", side_effect=flaky_git):
        report = qa_preflight._base_report(ROOT, "refreshed")
    assert report["revision"] == {
        "head": "head",
        "origin_main": "fetched",
        "head_matches_origin_main": False,
        "remote_freshness": "refreshed",
    }


def test_tool_docs_preserve_temporary_fixture_boundary():
    assert "temporary review and receipt fixtures" in qa_preflight.__doc__
    assert "not attributable QA evidence" in qa_preflight.__doc__


def test_run_suite_uses_python_and_propagates_exit_status():
    with tempfile.TemporaryDirectory() as tmp:
        runner = pathlib.Path(tmp) / "runner.py"
        runner.write_text("import sys\nsys.exit(7)\n", encoding="utf-8")
        assert qa_preflight._run_suite(ROOT, runner) == 7


def test_passing_run_suite_captures_verbose_runner_output():
    completed = subprocess.CompletedProcess(["runner"], 0, stdout="noisy success\n", stderr="")
    with mock.patch.object(qa_preflight.subprocess, "run", return_value=completed) as run:
        assert qa_preflight._run_suite(ROOT) == 0
    assert run.call_args.kwargs["capture_output"] is True
    assert run.call_args.kwargs["text"] is True


def test_run_suite_uses_an_ephemeral_root_not_the_callers_state():
    completed = subprocess.CompletedProcess(["runner"], 0, stdout="", stderr="")
    inherited = {
        "TRADE_COACH_HOME": "/real/root",
        "TR_TEST_NETWORK": "1",
        "TR_CASH": "private-input",
        "TR_STATE_OUT": "/real/output.json",
    }
    with mock.patch.dict(qa_preflight.os.environ, inherited), \
            mock.patch.object(qa_preflight.site, "getusersitepackages", return_value="/dependency-site"), \
            mock.patch.object(qa_preflight.site, "getuserbase", return_value="/dependency-base"), \
            mock.patch.object(qa_preflight.os.path, "isdir", return_value=True), \
            mock.patch.object(qa_preflight.subprocess, "run", return_value=completed) as run:
        assert qa_preflight._run_suite(ROOT) == 0
    env = run.call_args.kwargs["env"]
    for key in ("TR_TEST_NETWORK", "TR_CASH", "TR_STATE_OUT"):
        assert key not in env
    assert "TRADE_COACH_HOME" not in env
    assert env["HOME"] != qa_preflight.os.environ.get("HOME")
    assert pathlib.Path(env["HOME"]).name.startswith("fomo-qa-preflight-")
    assert env["PYTHONUSERBASE"] == "/dependency-base"
    assert env["PYTHONPATH"].split(qa_preflight.os.pathsep)[0] == "/dependency-site"


def test_status_is_network_free_and_content_free():
    with mock.patch.object(qa_preflight, "_git", side_effect=("head", "main")), \
            mock.patch.object(qa_preflight, "_emit") as emit, \
            mock.patch.object(qa_preflight, "_refresh") as refresh:
        assert qa_preflight.main(["--repo-root", str(ROOT), "status"]) == 0
    refresh.assert_not_called()
    report = emit.call_args.args[0]
    assert report["status"] == "ready"
    assert report["revision"]["remote_freshness"] == "unverified"
    assert "card" not in json.dumps(report)


def test_refresh_records_only_a_refreshed_revision_status():
    with mock.patch.object(qa_preflight, "_git", side_effect=("same", "same")), \
            mock.patch.object(qa_preflight, "_emit") as emit, \
            mock.patch.object(qa_preflight, "_refresh") as refresh:
        assert qa_preflight.main(["--repo-root", str(ROOT), "refresh"]) == 0
    refresh.assert_called_once_with(ROOT)
    report = emit.call_args.args[0]
    assert report["formal_qa"] is False
    assert report["revision"]["remote_freshness"] == "refreshed"
    assert report["revision"]["head_matches_origin_main"] is True


def test_run_reports_contract_failure_without_promoting_it_to_qa():
    with mock.patch.object(qa_preflight, "_git", side_effect=("head", "main")), \
            mock.patch.object(qa_preflight, "_run_suite", return_value=1), \
            mock.patch.object(qa_preflight, "_emit") as emit:
        assert qa_preflight.main(["--repo-root", str(ROOT), "run"]) == 1
    report = emit.call_args.args[0]
    assert report["status"] == "engine_contract_fail"
    assert report["formal_qa"] is False


def test_cli_status_prints_json():
    result = subprocess.run(
        [sys.executable, str(TOOL), "--repo-root", str(ROOT), "status"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["kind"] == "fomo_kernel_contract_preflight"
    assert report["formal_qa"] is False


def test_run_with_refresh_flag_fetches_before_the_suite_and_reports_refreshed():
    with mock.patch.object(qa_preflight, "_git", side_effect=("head", "main")), \
            mock.patch.object(qa_preflight, "_refresh") as refresh, \
            mock.patch.object(qa_preflight, "_run_suite", return_value=0), \
            mock.patch.object(qa_preflight, "_emit") as emit:
        assert qa_preflight.main(["--repo-root", str(ROOT), "run", "--refresh"]) == 0
    refresh.assert_called_once_with(ROOT)
    report = emit.call_args.args[0]
    assert report["revision"]["remote_freshness"] == "refreshed"
    assert report["status"] == "engine_contract_pass"


def test_repo_root_validator_accepts_a_checkout_and_rejects_a_bare_directory():
    assert qa_preflight._repo_root(str(ROOT)) == ROOT
    with tempfile.TemporaryDirectory() as tmp:
        try:
            qa_preflight._repo_root(tmp)
            raised = False
        except Exception as exc:
            raised = isinstance(exc, qa_preflight.argparse.ArgumentTypeError)
        assert raised, "a directory with no .git and no tests/run_all.py must be rejected"


def test_report_path_writes_the_same_json_emitted_to_stdout():
    report = {"status": "ready"}
    with tempfile.TemporaryDirectory() as tmp:
        target = pathlib.Path(tmp) / "nested" / "preflight.json"
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            qa_preflight._emit(report, target)
        printed = captured.getvalue().strip()
        written = target.read_text(encoding="utf-8").strip()
    assert printed == written == json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"PASS: {len(tests)} qa preflight tests")


if __name__ == "__main__":
    main()
