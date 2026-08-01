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
# These are synthetic-user inputs, not product questions.  They deliberately
# live at the A01 scenario boundary: a real host owns the user-visible wording
# and must supply any byte-identical answer capture itself.
QA_CONTEXT_STEPS = (
    {"id": "a01_reason", "field": "reason",
     "surface": "Synthetic A01 user input: provide the declared reason.",
     "allowed_actions": ["provide_text"]},
    {"id": "a01_why_now", "field": "why_now",
     "surface": "Synthetic A01 user input: provide the declared timing fact.",
     "allowed_actions": ["provide_text"]},
)


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
    transcript, context, turns, seen_surfaces, seen_actions = [], {}, 0, set(), set()
    user = (StubSyntheticUser([SCENARIO["reason"], SCENARIO["why_now"]]) if user_backend == "stub"
            else AgySyntheticUser(SCENARIO))
    for question in QA_CONTEXT_STEPS:
        if turns >= MAX_TURNS:
            raise WalkError("synthetic walk exceeded max_turns")
        field, surface = question.get("field"), question.get("surface")
        if field not in {"reason", "why_now"} or not isinstance(surface, str):
            raise WalkError("consider returned an unsupported context question")
        envelope = {"step_id": question.get("id"), "surface": surface,
                    "allowed_actions": question.get("allowed_actions"),
                    "route_state": "qa_context", "terminal": False}
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
                 "--decision-context", context_path, "--language", "en")
    evaluation_id = final["evaluation"]["evaluation_id"]
    _receipt(env, "start", "--session-id", evaluation_id, "--client", "synthetic-user", "--route", "consider",
             "--adapter", "plain_text")
    # No `question_presented`, `answers_received`, `evaluation_presented`, or
    # `resolution_presented` receipt event is honest here.  The two inputs are
    # scenario policy, not a host-delivered product surface, and this checkout
    # cannot byte-capture the host's final answer.
    _receipt(env, "event", "--session-id", evaluation_id, "--event", "findings_recorded",
             "--finding", "not-episodable:#718:production_answer_capture_unavailable")
    receipt_path = coach / "ux" / f"{evaluation_id}.jsonl"
    report = {"workflow": "pass", "deterministic": "pass",
              "production_answer_capture": "unavailable", "semantic_judge": "skipped",
              "owner_acceptance": "owner_unreviewed", "scenario": SCENARIO_ID,
              "persona": SCENARIO["persona_id"], "turn_count": turns, "final_resolution": None,
              "route_transitions": ["qa_context", "considered"],
              "ux_receipt_status": "incomplete_product_delivery",
              "time_to_first_value": None, "clarification_cost": {"truth_critical": 0,
              "optional_enrichment": 0, "repeated_or_unnecessary": 0},
              "candidate_artifact": None, "candidate_sha256": None,
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
                          "production_answer_capture": "external_host_required",
                          "semantic_judge": "requires_external_capture"}, sort_keys=True))
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
