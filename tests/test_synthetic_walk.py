#!/usr/bin/env python3
"""Offline contract tests for Issue #718's one synthetic-user walk."""
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


def run(*args):
    return subprocess.run([sys.executable, str(RUNNER), *args], cwd=ROOT,
                          text=True, capture_output=True, timeout=90)


def test_plan_has_no_model_calls():
    result = run("consider-ai-momentum", "--user-backend", "stub", "--plan")
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["scenario"] == "consider-ai-momentum"
    assert plan["route"] == "consider" and plan["model_calls"] == 0


def test_stub_walk_keeps_the_real_route_but_reports_uncaptured_production_answer():
    with tempfile.TemporaryDirectory() as tmp:
        result = run("consider-ai-momentum", "--user-backend", "stub", "--no-semantic-judge", "--output-dir", tmp)
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert {key: report[key] for key in ("workflow", "deterministic", "semantic_judge", "owner_acceptance")} == {
            "workflow": "pass", "deterministic": "pass", "semantic_judge": "skipped",
            "owner_acceptance": "owner_unreviewed"}
        assert report["production_answer_capture"] == "unavailable"
        assert report["ux_receipt_status"] == "incomplete_product_delivery"
        assert report["candidate_artifact"] is None
        assert not (pathlib.Path(tmp) / "captured-product-surface.json").exists()


def test_invalid_synthetic_action_fails_before_route_or_judge():
    sys.path.insert(0, str(ROOT / "qa"))
    from synthetic_user import SyntheticUserError, parse_action
    try:
        parse_action({"action_type": "provide_text", "text": "invented fact"},
                     allowed_actions={"provide_text"}, allowed_text={"declared fact"})
    except SyntheticUserError:
        return
    raise AssertionError("undeclared synthetic text was accepted")


def test_semantic_timeout_preserves_a_combined_receipt_state():
    sys.path.insert(0, str(ROOT / "qa"))
    import run_synthetic_walk as runner
    with mock.patch.object(runner.subprocess, "run",
                           side_effect=subprocess.TimeoutExpired(cmd="judge", timeout=1)):
        assert runner._semantic_judge({}, pathlib.Path("candidate.json"),
                                      pathlib.Path("source.json")) == "unavailable"


if __name__ == "__main__":
    test_plan_has_no_model_calls()
    test_stub_walk_keeps_the_real_route_but_reports_uncaptured_production_answer()
    test_invalid_synthetic_action_fails_before_route_or_judge()
    test_semantic_timeout_preserves_a_combined_receipt_state()
    print("synthetic walk tests: ok")
