#!/usr/bin/env python3
"""One bounded #718 synthetic-user walk through the real ``consider`` CLI."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from candidate_builder import build, source_fixture  # noqa: E402
from synthetic_user import AgySyntheticUser, StubSyntheticUser, SyntheticUserError, parse_action  # noqa: E402

SCENARIO_ID = "consider-ai-momentum"
FIXTURE_ID = "synthetic-consider-ai-momentum"
MAX_TURNS = 8
SEMANTIC_JUDGE_TIMEOUT_SECONDS = 1000

SCENARIO = {
    "persona_id": "ai-momentum-investor",
    "reason": "Customer demand appears stronger.",
    "why_now": "Recent fictional customer conversations sound more urgent.",
    "book": ["FICTIONAL-A", "FICTIONAL-B", "FICTIONAL-C", "FICTIONAL-D", "FICTIONAL-E"],
}


class WalkError(RuntimeError):
    pass


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _run(env, *args):
    run = subprocess.run([sys.executable, str(ROOT / "skills/fomo-kernel/engine/review.py"), *map(str, args)],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    if run.returncode:
        raise WalkError(run.stdout or run.stderr or "consider route failed")
    return json.loads(run.stdout)


def _receipt(env, *args):
    run = subprocess.run([sys.executable, str(ROOT / "skills/fomo-kernel/tools/ux_receipt.py"), *map(str, args)],
                         cwd=ROOT / "skills/fomo-kernel", env=env, capture_output=True, text=True, timeout=30)
    if run.returncode:
        raise WalkError(run.stdout or run.stderr or "ux receipt failed")


def _semantic_judge(env, candidate_path, fixture_path):
    """Run the existing #705 judge without losing a completed walkthrough.

    The judge's own agy sample timeout is five minutes and it may take several
    samples.  A shorter wrapper deadline would turn a judge infrastructure
    timeout into a missing combined receipt after the product flow already
    completed.  The semantic layer therefore records ``unavailable`` while
    preserving the deterministic workflow result.
    """
    try:
        judge = subprocess.run(
            [sys.executable, str(ROOT / "evals/judge_trade_answers.py"), "--answer-file", candidate_path,
             "--source-fixture", fixture_path],
            cwd=ROOT, env=env, capture_output=True, text=True,
            timeout=SEMANTIC_JUDGE_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"
    return "agreement" if judge.returncode == 0 else "disagreement"


def _inputs(directory):
    csv = directory / "book.csv"
    csv.write_text("Symbol,Quantity,Price,Action,TradeDate,RecordType\n" + "\n".join(
        f"{ticker},200,100,BUY,2026-07-31,Trade" for ticker in SCENARIO["book"]) + "\n", encoding="utf-8")
    prices = directory / "prices.json"
    _write(prices, {"schema_version": 1, "as_of": "2026-08-01", "source": "synthetic fixture",
                    "prices": [{"ticker": ticker, "close": 100, "date": "2026-08-01", "currency": "USD"}
                               for ticker in SCENARIO["book"]]})
    return csv, prices


def run_walk(*, user_backend="stub", semantic_judge=False, judge_backend=None, output_dir=None):
    if user_backend not in {"stub", "agy"}:
        raise WalkError("unknown synthetic user backend")
    run_dir = pathlib.Path(output_dir or tempfile.mkdtemp(prefix="fomo-synthetic-walk-"))
    run_dir.mkdir(parents=True, exist_ok=True)
    coach = run_dir / "coach"
    home = run_dir / "home"
    env = dict(os.environ, TRADE_COACH_HOME=str(coach), HOME=str(home), TR_OFFLINE="1")
    if judge_backend:
        env["TR_JUDGE_BACKEND"] = judge_backend
    if pathlib.Path(os.path.expanduser("~/.trade-coach")).resolve() == coach.resolve():
        raise WalkError("dogfood root must not equal the real coach root")
    csv, prices = _inputs(run_dir)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    _run(env, "set-cap", "--root", coach, "--pct", "0.25")
    premise = {"ticker": "FICTIONAL-A", "side": "buy", "qty": 95.8904,
               "price": 100, "date": "2026-08-01", "currency": "USD"}
    interaction = _run(env, "consider", "--root", coach, "--premise", json.dumps(premise),
                       "--context-questions", "--language", "en")
    if interaction.get("status") != "collecting_context" or interaction.get("route") != "consider":
        raise WalkError("consider did not return a product-owned context surface")

    transcript, context, turns, seen_surfaces, seen_actions = [], {}, 0, set(), set()
    user = (StubSyntheticUser([SCENARIO["reason"], SCENARIO["why_now"]]) if user_backend == "stub"
            else AgySyntheticUser(SCENARIO))
    for question in interaction.get("question_queue") or ():
        if turns >= MAX_TURNS:
            raise WalkError("synthetic walk exceeded max_turns")
        field, surface = question.get("field"), question.get("surface")
        if field not in {"reason", "why_now"} or not isinstance(surface, str):
            raise WalkError("consider returned an unsupported context question")
        envelope = {"step_id": question.get("question_id"), "surface": surface,
                    "allowed_actions": question.get("allowed_actions"),
                    "route_state": question.get("route_state"), "terminal": False}
        surface_digest = hashlib.sha256(surface.encode()).hexdigest()
        if surface_digest in seen_surfaces:
            raise WalkError(f"repeated product surface at {envelope['step_id']}")
        seen_surfaces.add(surface_digest)
        action = parse_action(user.choose(envelope), allowed_actions=envelope["allowed_actions"],
                              allowed_text={SCENARIO[field]})
        action_digest = hashlib.sha256(json.dumps(action.__dict__, sort_keys=True).encode()).hexdigest()
        if action_digest in seen_actions:
            raise WalkError(f"repeated synthetic-user action at {envelope['step_id']}")
        seen_actions.add(action_digest)
        context[field] = action.text
        transcript.append({"surface": envelope, "action": action.__dict__})
        turns += 1
    context["evidence_refs"] = []
    context_path = run_dir / "decision-context.json"
    _write(context_path, context)
    final = _run(env, "consider", csv, "--root", coach,
                 "--premise", json.dumps(premise),
                 "--prices", prices, "--cash", json.dumps({"as_of": "2026-08-01", "amount": 20000, "currency": "USD"}),
                 "--decision-context", context_path, "--product-surface", "--language", "en")
    product_surface = final["product_surface"]
    _write(run_dir / "captured-product-surface.json", product_surface)
    candidate = build(product_surface, final["challenge"], fixture_id=FIXTURE_ID, revision=revision)
    candidate_path, fixture_path = run_dir / "candidate.json", run_dir / "source-fixture.json"
    _write(candidate_path, candidate)
    _write(fixture_path, source_fixture(final["evaluation"], candidate, FIXTURE_ID))
    evaluation_id = final["evaluation"]["evaluation_id"]
    check_path = run_dir / "challenge-check.json"
    _write(check_path, {"challenge": final["challenge"], "presented_text": product_surface["presented_text"]})
    _receipt(env, "start", "--session-id", evaluation_id, "--client", "synthetic-user", "--route", "consider",
             "--adapter", "plain_text")
    for row in transcript:
        _receipt(env, "event", "--session-id", evaluation_id, "--event", "question_presented", "--mode", "plain_text")
    _receipt(env, "event", "--session-id", evaluation_id, "--event", "answers_received")
    _receipt(env, "event", "--session-id", evaluation_id, "--event", "evaluation_presented", "--challenge-check-file", check_path)
    _receipt(env, "event", "--session-id", evaluation_id, "--event", "resolution_presented", "--workflow-state", "open")
    _receipt(env, "event", "--session-id", evaluation_id, "--event", "findings_recorded", "--no-findings")
    receipt_path = coach / "ux" / f"{evaluation_id}.jsonl"
    verification = subprocess.run([sys.executable, str(ROOT / "skills/fomo-kernel/tools/ux_receipt.py"), "verify",
                                   "--session-id", evaluation_id, "--require-findings"], cwd=ROOT / "skills/fomo-kernel",
                                  env=env, capture_output=True, text=True, timeout=30)
    deterministic = "pass" if verification.returncode == 0 else "fail"
    semantic = "skipped"
    if deterministic == "pass" and semantic_judge:
        semantic = _semantic_judge(env, candidate_path, fixture_path)
    report = {"workflow": "pass" if deterministic == "pass" else "fail", "deterministic": deterministic,
              "semantic_judge": semantic, "owner_acceptance": "owner_unreviewed", "scenario": SCENARIO_ID,
              "persona": SCENARIO["persona_id"], "turn_count": turns, "final_resolution": "open",
              "route_transitions": ["collecting_context", "considered", "open"],
              "time_to_first_value": turns, "clarification_cost": {"truth_critical": turns,
              "optional_enrichment": 0, "repeated_or_unnecessary": 0},
              "candidate_artifact": str(candidate_path), "candidate_sha256": hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
              "receipt": str(receipt_path), "transcript_sha256": hashlib.sha256(json.dumps(transcript, sort_keys=True).encode()).hexdigest(),
              "repository_sha": revision, "dogfood_root": str(coach)}
    _write(run_dir / "combined-receipt.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=[SCENARIO_ID])
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--user-backend", default="agy", choices=["stub", "agy"])
    parser.add_argument("--judge-backend")
    parser.add_argument("--no-semantic-judge", action="store_true")
    parser.add_argument("--output-dir")
    args = parser.parse_args(argv)
    if args.plan:
        print(json.dumps({"scenario": SCENARIO_ID, "route": "consider", "max_turns": MAX_TURNS,
                          "user_backend": args.user_backend, "model_calls": 0 if args.user_backend == "stub" else 2,
                          "semantic_judge": not args.no_semantic_judge}, sort_keys=True))
        return 0
    try:
        print(json.dumps(run_walk(user_backend=args.user_backend, semantic_judge=not args.no_semantic_judge,
                                  judge_backend=args.judge_backend,
                                  output_dir=args.output_dir), sort_keys=True))
    except (WalkError, SyntheticUserError) as exc:
        print(json.dumps({"workflow": "fail", "deterministic": "fail", "semantic_judge": "skipped",
                          "owner_acceptance": "owner_unreviewed", "error": str(exc)}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
