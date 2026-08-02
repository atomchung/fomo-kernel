#!/usr/bin/env python3
"""One bounded #718 synthetic-user walk through the real ``consider`` CLI.

The walk drives the real route twice -- once on the scenario's declared motive,
once on the synthetic user's correction -- and records the three visible turns
around it: the initial product answer, the correction that moves the route on,
and the corrected answer. What the engine computes stays the engine's; what
this file adds is the ordered record of what was shown, and an attempt count a
later failure can re-bucket but never erase.

Exit codes are part of the contract, because "the run failed" and "the run
never finished" are different facts:

    0  a terminal route run whose product surfaces passed
    1  a terminal route run whose product surfaces failed
    2  the run never reached a terminal verdict (harness_incomplete)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import traceback

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from synthetic_user import AgySyntheticUser, StubSyntheticUser, SyntheticUserError, parse_action  # noqa: E402
from turn_trace import (PRODUCT_PROVENANCE, RunLedger, TraceError, TurnTrace,  # noqa: E402
                        build_report, classify_surface)

SCENARIO_ID = "consider-ai-momentum"
FIXTURE_ID = "synthetic-consider-ai-momentum"
MAX_TURNS = 8
SEMANTIC_JUDGE_TIMEOUT_SECONDS = 1000
SURFACE_SCRIPTS = {
    "baseline": HERE / "scenarios" / "consider-ai-momentum-correction.json",
    "mutated": HERE / "scenarios" / "consider-ai-momentum-correction-mutated.json",
}
# What the user is shown when the corrected evaluation does not complete. The
# machine detail behind it goes to the separate diagnostics artifact, never
# into a visible surface: raw tool output is not something a user ever saw.
ROUTE_FAILURE_SURFACE = ("The corrected evaluation did not complete, so there is no updated "
                         "decision result to show. Nothing was recorded for it.")

SCENARIO = {
    "persona_id": "ai-momentum-investor",
    "reason": "Customer demand appears stronger.",
    "why_now": "Recent fictional customer conversations sound more urgent.",
    "book": ["FICTIONAL-A", "FICTIONAL-B", "FICTIONAL-C", "FICTIONAL-D", "FICTIONAL-E"],
}
PREMISE = {"ticker": "FICTIONAL-A", "side": "buy", "qty": 95.8904,
           "price": 100, "date": "2026-08-01", "currency": "USD"}
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


def load_surface_script(name_or_path):
    """Load the three-turn correction trajectory, refusing an unusable shape.

    The script owns what a host would have shown; it never owns the verdict.
    Every check here is structural, and no entry may label its own message
    type -- that is classified from the surface itself -- so a fixture cannot
    declare itself passing, nor smuggle a fourth turn or a missing correction
    past the trace.
    """
    path = SURFACE_SCRIPTS.get(name_or_path) or pathlib.Path(name_or_path)
    try:
        script = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WalkError(f"unreadable surface script {path}: {exc}") from exc
    turns = script.get("turns")
    if not isinstance(turns, list) or len(turns) != 3:
        raise WalkError("a correction trajectory is exactly three visible turns")
    if [turn.get("role") for turn in turns] != ["assistant", "user", "assistant"]:
        raise WalkError("trajectory must be assistant, user correction, assistant")
    for turn in (turns[0], turns[2]):
        if turn.get("provenance") not in PRODUCT_PROVENANCE:
            raise WalkError("an assistant surface must declare a product provenance")
        if not isinstance(turn.get("text"), str) or not turn["text"].strip():
            raise WalkError("an assistant surface needs text")
        if "message_type" in turn:
            raise WalkError("a surface script may not label its own message type")
    correction = turns[1]
    allowed_text = correction.get("allowed_text")
    context = correction.get("corrected_context")
    if not isinstance(allowed_text, str) or not allowed_text.strip():
        raise WalkError("the correction turn needs allowed_text")
    if not isinstance(context, dict) or set(context) != {"reason", "why_now"}:
        raise WalkError("the correction turn needs a corrected reason and why_now")
    for value in context.values():
        # The engine quotes the user verbatim.  If what is fed to the route is
        # not literally inside what the user was shown saying, the challenge
        # would quote words nobody said.
        if not isinstance(value, str) or value not in allowed_text:
            raise WalkError("corrected context must appear verbatim in the correction the user made")
    options = (script.get("policy") or {}).get("terminal_options")
    if not isinstance(options, list) or not options or not all(isinstance(o, str) for o in options):
        raise WalkError("the script must declare terminal_options")
    return script


def _ask(user, envelope, *, allowed_text, seen_surfaces, seen_actions):
    """One bounded synthetic-user step, with the loop guards #718 requires."""
    surface_digest = hashlib.sha256(envelope["surface"].encode()).hexdigest()
    if surface_digest in seen_surfaces:
        raise WalkError(f"repeated product surface at {envelope['step_id']}")
    seen_surfaces.add(surface_digest)
    action = parse_action(user.choose(envelope), allowed_actions=envelope["allowed_actions"],
                          allowed_text=allowed_text)
    action_digest = hashlib.sha256(json.dumps(action.__dict__, sort_keys=True).encode()).hexdigest()
    if action_digest in seen_actions:
        raise WalkError(f"repeated synthetic-user action at {envelope['step_id']}")
    seen_actions.add(action_digest)
    return action


def run_walk(*, user_backend="stub", semantic_judge=False, judge_backend=None,
             output_dir=None, surface_script="baseline"):
    ledger = RunLedger()
    ledger.start_campaign()
    trace = TurnTrace()
    if user_backend not in {"stub", "agy"}:
        raise WalkError("unknown synthetic user backend")
    script = load_surface_script(surface_script)
    resolutions = script["policy"]["terminal_options"]
    run_dir = pathlib.Path(output_dir or tempfile.mkdtemp(prefix="fomo-synthetic-walk-"))
    run_dir.mkdir(parents=True, exist_ok=True)
    coach = run_dir / "coach"
    home = run_dir / "home"
    env = dict(os.environ, TRADE_COACH_HOME=str(coach), HOME=str(home), TR_OFFLINE="1")
    if judge_backend:
        env["TR_JUDGE_BACKEND"] = judge_backend
    if pathlib.Path(os.path.expanduser("~/.trade-coach")).resolve() == coach.resolve():
        raise WalkError("dogfood root must not equal the real coach root")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    base = {"scenario": SCENARIO_ID, "persona": SCENARIO["persona_id"],
            "surface_script": script["id"],
            "surface_provenance": sorted({turn["provenance"] for turn in script["turns"]
                                          if turn["role"] == "assistant"}),
            "deterministic": "fail", "semantic_judge": "skipped",
            "semantic_judge_reason": "fixture_surfaces_are_not_a_product_capture",
            "production_answer_capture": "unavailable",
            "ux_receipt_status": "incomplete_product_delivery",
            "owner_acceptance": "owner_unreviewed",
            "route_transitions": [], "final_resolution": None,
            "time_to_first_value": None,
            "clarification_cost": {"truth_critical": 0, "optional_enrichment": 0,
                                   "repeated_or_unnecessary": 0},
            "surface_findings": [], "candidate_artifact": None, "candidate_sha256": None,
            "receipt": None, "raw_trace": str(run_dir / "turn-trace.local.jsonl"),
            "harness_diagnostics": None,
            "repository_sha": revision, "dogfood_root": str(coach)}

    def flush():
        report = build_report(ledger=ledger, trace=trace, base=base)
        _write(run_dir / "combined-receipt.json", report)
        trace.write_raw(run_dir / "turn-trace.local.jsonl")
        return report

    def diagnose(detail):
        """Machine detail lives here, in its own artifact -- never in a surface."""
        path = run_dir / "harness-diagnostics.local.json"
        _write(path, detail)
        base["harness_diagnostics"] = str(path)

    # The attempt is counted, and reads as incomplete on disk, before the first
    # turn exists.  A hard crash from here on therefore leaves an honest run
    # rather than no run at all.
    ledger.start_route()
    flush()
    try:
        report = _walk_body(env=env, run_dir=run_dir, coach=coach, script=script,
                            resolutions=resolutions, user_backend=user_backend,
                            ledger=ledger, trace=trace, base=base, flush=flush,
                            diagnose=diagnose)
    except BaseException as exc:  # noqa: BLE001 -- re-raised below; the point is the flush
        ledger.stop_incomplete(f"harness_error:{type(exc).__name__}")
        diagnose({"stage": "walk", "error_type": type(exc).__name__, "error": str(exc),
                  "traceback": traceback.format_exc()})
        flush()
        raise
    return report


def _walk_body(*, env, run_dir, coach, script, resolutions, user_backend,
               ledger, trace, base, flush, diagnose):
    csv, prices = _inputs(run_dir)
    _run(env, "set-cap", "--root", coach, "--pct", "0.25")
    correction = script["turns"][1]
    responses = [SCENARIO["reason"], SCENARIO["why_now"], correction["allowed_text"]]
    user = (StubSyntheticUser(responses) if user_backend == "stub" else AgySyntheticUser(SCENARIO))
    seen_surfaces, seen_actions = set(), set()

    context = {}
    for question in QA_CONTEXT_STEPS:
        field_name, surface = question.get("field"), question.get("surface")
        if field_name not in {"reason", "why_now"} or not isinstance(surface, str):
            raise WalkError("consider returned an unsupported context question")
        envelope = {"step_id": question["id"], "surface": surface,
                    "allowed_actions": question["allowed_actions"],
                    "route_state": "qa_context", "terminal": False}
        action = _ask(user, envelope, allowed_text={SCENARIO[field_name]},
                      seen_surfaces=seen_surfaces, seen_actions=seen_actions)
        context[field_name] = action.text
        trace.record_setup(field_name=field_name, text=action.text)
    context["evidence_refs"] = []
    base["route_transitions"].append("qa_context")

    first = _consider(env, run_dir, csv, prices, coach, context, name="decision-context.json")
    evaluation_id = first["evaluation"]["evaluation_id"]
    base["receipt"] = str(coach / "ux" / f"{evaluation_id}.jsonl")
    base["route_transitions"].append("considered")
    base["deterministic"] = "pass"
    _receipt(env, "start", "--session-id", evaluation_id, "--client", "synthetic-user", "--route", "consider",
             "--adapter", "plain_text")
    # No `question_presented`, `answers_received`, `evaluation_presented`, or
    # `resolution_presented` receipt event is honest here.  The two inputs are
    # scenario policy, not a host-delivered product surface, and this checkout
    # cannot byte-capture the host's final answer.
    _receipt(env, "event", "--session-id", evaluation_id, "--event", "findings_recorded",
             "--finding", "not-episodable:#718:production_answer_capture_unavailable")
    flush()

    findings = base["surface_findings"]
    opening = _turn(trace, script["turns"][0], resolutions, findings, flush, route_state="considered")
    if opening["delivers_decision_result"]:
        base["time_to_first_value"] = trace.turn_count

    envelope = {"step_id": "correction", "surface": script["turns"][0]["text"],
                "allowed_actions": ["provide_text"], "route_state": "considered", "terminal": False}
    action = _ask(user, envelope, allowed_text={correction["allowed_text"]},
                  seen_surfaces=seen_surfaces, seen_actions=seen_actions)
    trace.record(role="user", message_type="correction", text=action.text,
                 provenance="fixture", route_state="correction_received")
    base["route_transitions"].append("correction_received")
    flush()

    corrected = dict(correction["corrected_context"], evidence_refs=[])
    try:
        second = _consider(env, run_dir, csv, prices, coach, corrected,
                           name="decision-context-corrected.json")
    except WalkError as exc:
        diagnose({"stage": "corrected_evaluation", "error_type": type(exc).__name__, "error": str(exc)})
        trace.record(role="assistant", message_type="process_error", provenance="harness",
                     route_state="route_failed", text=ROUTE_FAILURE_SURFACE)
        ledger.stop_incomplete("route_process_failed")
        base["deterministic"] = "fail"
        return flush()
    # A real check, not a fixture's opinion: the route must now quote the words
    # the synthetic user actually corrected it with.
    quoted = {row.get("text") for row in second["challenge"]["quote_verbatim"]}
    if [value for value in correction["corrected_context"].values() if value not in quoted]:
        raise WalkError("the corrected evaluation did not quote the user's correction verbatim")
    base["route_transitions"].append("reconsidered")
    base["final_resolution"] = second["evaluation"]["decision"]
    if trace.turn_count >= MAX_TURNS:
        raise WalkError("synthetic walk exceeded max_turns")

    _turn(trace, script["turns"][2], resolutions, findings, flush, route_state="reconsidered")
    failed = [finding for finding in findings if finding["verdict"] == "product_fail"]
    ledger.settle("product_fail" if failed else "product_pass",
                  stop_reason=(failed[0]["reason"] if failed else "corrected_result_delivered"))
    return flush()


def _turn(trace, entry, resolutions, findings, flush, *, route_state):
    """Classify one assistant surface, then record what it actually is.

    The message type is derived, never declared: a surface that narrates the
    system's own repair work is recorded as ``process_error`` even though it
    arrived in the slot where a decision result was due.
    """
    finding = classify_surface(entry["text"], instrument=PREMISE["ticker"], resolutions=resolutions)
    message_type = "decision_result" if finding["verdict"] == "product_pass" else "process_error"
    turn = trace.record(role="assistant", message_type=message_type, text=entry["text"],
                        provenance=entry["provenance"], route_state=route_state)
    findings.append({"turn": turn.index, **finding})
    flush()
    return finding


def _consider(env, run_dir, csv, prices, coach, context, *, name):
    path = run_dir / name
    _write(path, context)
    return _run(env, "consider", csv, "--root", coach,
                "--premise", json.dumps(PREMISE),
                "--prices", prices,
                "--cash", json.dumps({"as_of": "2026-08-01", "amount": 20000, "currency": "USD"}),
                "--decision-context", path, "--language", "en")


def _exit_code(report):
    if report["harness_incomplete"]:
        return 2
    return 1 if report["product_failures"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", choices=[SCENARIO_ID])
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--user-backend", default="agy", choices=["stub", "agy"])
    parser.add_argument("--judge-backend")
    parser.add_argument("--no-semantic-judge", action="store_true")
    parser.add_argument("--output-dir")
    parser.add_argument("--surface-script", default="baseline",
                        help="baseline, mutated, or a path to a three-turn correction script")
    args = parser.parse_args(argv)
    if args.plan:
        print(json.dumps({"scenario": SCENARIO_ID, "route": "consider", "max_turns": MAX_TURNS,
                          "user_backend": args.user_backend,
                          "model_calls": 0 if args.user_backend == "stub" else 3,
                          "surface_script": args.surface_script, "visible_turns": 3,
                          "consider_invocations": 2,
                          "production_answer_capture": "external_host_required",
                          "semantic_judge": "requires_external_capture"}, sort_keys=True))
        return 0
    ledger = RunLedger()
    ledger.start_campaign()
    try:
        report = run_walk(user_backend=args.user_backend, semantic_judge=not args.no_semantic_judge,
                          judge_backend=args.judge_backend, output_dir=args.output_dir,
                          surface_script=args.surface_script)
    except (WalkError, SyntheticUserError, TraceError, OSError, subprocess.SubprocessError) as exc:
        # The campaign started, so it is counted.  Whether the route run itself
        # started is not knowable here, so this path never claims one did -- the
        # run_dir report it already flushed is the record of that.
        ledger.stop_incomplete(f"harness_error:{type(exc).__name__}")
        report = build_report(ledger=ledger, trace=TurnTrace(),
                              base={"scenario": SCENARIO_ID, "deterministic": "fail",
                                    "semantic_judge": "skipped", "owner_acceptance": "owner_unreviewed",
                                    "error": str(exc)})
        print(json.dumps(report, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return _exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
