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
JUDGE = ROOT / "evals" / "judge_trade_answers.py"


def run(*args):
    return subprocess.run([sys.executable, str(RUNNER), *args], cwd=ROOT,
                          text=True, capture_output=True, timeout=90)


def test_plan_has_no_model_calls():
    result = run("consider-ai-momentum", "--user-backend", "stub", "--plan")
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["scenario"] == "consider-ai-momentum"
    assert plan["route"] == "consider" and plan["model_calls"] == 0


def test_stub_walk_uses_real_route_and_captures_exact_candidate():
    with tempfile.TemporaryDirectory() as tmp:
        result = run("consider-ai-momentum", "--user-backend", "stub", "--no-semantic-judge", "--output-dir", tmp)
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert {key: report[key] for key in ("workflow", "deterministic", "semantic_judge", "owner_acceptance")} == {
            "workflow": "pass", "deterministic": "pass", "semantic_judge": "skipped",
            "owner_acceptance": "owner_unreviewed"}
        root = pathlib.Path(tmp)
        candidate = json.loads((root / "candidate.json").read_text(encoding="utf-8"))
        captured = json.loads((root / "captured-product-surface.json").read_text(encoding="utf-8"))
        assert candidate["fixture_id"] == "synthetic-consider-ai-momentum"
        assert candidate["presented_text"] == captured["presented_text"]
        assert candidate["agent_case"] == captured["agent_case"]
        assert candidate["presented_text"] == "".join(
            candidate["presented_text"][part["start"]:part["end"]] for part in candidate["segments"])
        # This is the existing #705 judge, now bound to the captured frozen
        # evaluation rather than a second answer rubric.
        judged = subprocess.run([sys.executable, str(JUDGE), "--answer-file", str(root / "candidate.json"),
                                 "--source-fixture", str(root / "source-fixture.json"), "--plan"],
                                cwd=ROOT, text=True, capture_output=True, timeout=60)
        assert judged.returncode == 0, judged.stdout + judged.stderr


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
    test_stub_walk_uses_real_route_and_captures_exact_candidate()
    test_invalid_synthetic_action_fails_before_route_or_judge()
    test_semantic_timeout_preserves_a_combined_receipt_state()
    print("synthetic walk tests: ok")
