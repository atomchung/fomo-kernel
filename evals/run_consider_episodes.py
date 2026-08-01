#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_consider_episodes.py — bounded synthetic-user consider-flow evaluator.

One command that:
1. Sets up a bounded synthetic book from mock CSVs
2. Runs ``review.py consider`` to get the engine payload + challenge block
3. Feeds the challenge to an LLM (bounded synthetic user) to produce a
   consider answer
4. Runs the answer through existing mechanical checks (number_provenance,
   privacy_trace, etc.) imported from ``run_episodes.py``
5. Optionally grades it with the rubric judge (two_sided, overrulable)
6. Records findings with ``declared_by: agent`` (owner_unreviewed)

Billable and opt-in — never part of ``tests/run_all.py``. Same backend
resolution as ``judge_episodes.py`` (agy default, anthropic fallback).

Usage::

    python3 evals/run_consider_episodes.py              # run all scenarios
    python3 evals/run_consider_episodes.py --plan        # show what would run
    python3 evals/run_consider_episodes.py --scenario add_to_position
    python3 evals/run_consider_episodes.py --judge       # include rubric judge
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
SKILL_DIR = REPO / "skills" / "fomo-kernel"
EVALS_DIR = REPO / "evals"

# Reuse the episode bank's mechanical checks without duplicating them.
# run_episodes.py is in the same directory — import it directly.
sys.path.insert(0, str(EVALS_DIR))
import run_episodes as R   # noqa: E402
import judge_episodes as J  # noqa: E402

# ──────────────────────────── scenario corpus ────────────────────────────────

SCENARIOS = [
    {
        "id": "add_to_position",
        "title": "Normal add to an existing holding, priced on cost, with decision context",
        "fixture": {
            "trades": "skills/fomo-kernel/mock/sample_ai_holder.csv",
            "driver_map": "skills/fomo-kernel/mock/sample_ai_holder.driver_map.json",
            "route": "first_review",
            "locale": "en",
        },
        "premise": {
            "ticker": "NVDA", "side": "buy", "price": 127.50, "qty": 100,
        },
        "decision_context": {
            "reason": "NVDA is still my highest-conviction name in the book.",
            "why_now": "It dropped hard this week and the discount feels too good to pass up.",
        },
        "needs_finalized_review": False,
        "checks": ["number_provenance", "privacy_trace", "surface_hygiene"],
        "judge_axes": ["two_sided", "overrulable"],
    },
    {
        "id": "sell_existing",
        "title": "Sell half of largest position, expect already_over with worsens:false",
        "fixture": {
            "trades": "skills/fomo-kernel/mock/sample_ai_holder.csv",
            "driver_map": "skills/fomo-kernel/mock/sample_ai_holder.driver_map.json",
            "route": "first_review",
            "locale": "en",
        },
        "premise": {
            "ticker": "NVDA", "side": "sell", "price": 127.50, "qty": 50,
        },
        "decision_context": None,
        "needs_finalized_review": False,
        "checks": ["number_provenance", "privacy_trace", "surface_hygiene"],
        "judge_axes": ["two_sided"],
    },
    {
        "id": "partial_book",
        "title": "Add with a partial book — one holding excluded, rest answers",
        "fixture": {
            "trades": "skills/fomo-kernel/mock/sample_momentum.csv",
            "driver_map": "skills/fomo-kernel/mock/sample_momentum.driver_map.json",
            "route": "first_review",
            "locale": "en",
        },
        "premise": {
            "ticker": "AAPL", "side": "buy", "price": 190.0, "qty": 50,
        },
        "decision_context": None,
        "needs_finalized_review": False,
        "checks": ["number_provenance", "privacy_trace", "surface_hygiene"],
        "judge_axes": ["two_sided"],
    },
    {
        "id": "whole_book_refusal",
        "title": "Non-recoverable refusal with usable_facts from a finalized review",
        "fixture": {
            "trades": "skills/fomo-kernel/mock/sample_ai_holder.csv",
            "driver_map": "skills/fomo-kernel/mock/sample_ai_holder.driver_map.json",
            "route": "first_review",
            "locale": "en",
        },
        "premise": {
            "ticker": "NVDA", "side": "buy", "price": 127.50, "qty": 100,
        },
        "decision_context": {
            "reason": "NVDA is still my highest-conviction name in the book.",
            "why_now": "It dropped hard this week and the discount feels too good to pass up.",
        },
        "needs_finalized_review": True,
        # usable_facts_grounding is the check for this refusal shape
        "checks": ["usable_facts_grounding", "privacy_trace"],
        "judge_axes": ["two_sided"],
    },
]


# ─────────────────────────── synthetic-user prompt ───────────────────────────
#
# This prompt is a constraint, not a behavior oracle. It tells the LLM what
# facts must appear (the challenge block), not what phrasing to use.

SYNTHETIC_USER_SYSTEM = """\
You are a bounded synthetic user for an automated evaluation. You will produce
a consider-flow answer — plain conversation, not a card — responding to a
pre-trade evaluation from a trade-review product.

Your answer MUST satisfy these structural constraints:

1. EVERY entry in `challenge.must_state` must appear in your prose, using the
   values from the engine payload. You may paraphrase the framing but the
   numbers must match the engine's own values (exact or rounded to at most
   two decimal places, fractions as percentages).

2. If `challenge.quote_verbatim` is non-empty, reproduce the user's exact
   words — do not paraphrase, summarize, or translate them.

3. Name every item in `challenge.unchecked` — these are risks the engine
   did not measure. Silence about an unchecked risk reads as a clean bill
   of health the engine never gave.

4. The case MUST be two-sided: at least one claim FOR the trade and at least
   one claim AGAINST.

5. Do not invent numbers. Every number in your answer must trace to the
   engine payload's `before`, `after`, `delta`, `basis`, or
   `rule_collisions`. If a number does not appear in the payload, do not
   use it.

6. Do not use internal engine identifiers (snake_case codes like
   `max_pos_pct`, `ai_pct`, etc.) on the user-facing surface. Use natural
   language equivalents.

7. Keep the answer to two compact paragraphs plus one resolution sentence.

8. End with a resolution sentence that keeps the decision open — the user
   chooses to act, decline, or modify.

9. No CJK characters in English answers. No English metric labels in
   non-English answers.

10. Do not name tickers or symbols that are not in the engine payload.

Return ONLY a JSON object with this shape:
{
  "prose": "Your full answer text as the user would read it.",
  "presented_options": [
    {"label": "Keep this open", "maps_to": "open", "description": ""},
    {"label": "Decline", "maps_to": "declined", "description": ""},
    {"label": "Modify the size", "maps_to": "modified", "description": ""}
  ]
}

No other text, no code fences, no explanation outside the JSON."""


SYNTHETIC_USER_PROMPT_TEMPLATE = """\
ENGINE PAYLOAD (the frozen evaluation):
{payload}

CHALLENGE BLOCK (the floor of your answer — every entry must be present):
{challenge}

DISCLOSURES on this evaluation: {disclosures}

UNCHECKED items: {unchecked}

Produce the answer JSON now."""


# For the refusal/decision-framing shape, the prompt is different.
SYNTHETIC_USER_SYSTEM_REFUSAL = """\
You are a bounded synthetic user for an automated evaluation. You will produce
a decision-framing answer for a non-recoverable consider refusal — the engine
could not compute a consequence, but it carried forward `usable_facts` from
the last finalized review.

Your answer MUST satisfy these constraints:

1. Lead with a decision tension — what the trade-off actually is — never
   the engine's error message and never a request to restart.

2. Frame at least two of the user's own nominated options — the holdings
   they are weighing. State what each option commits the user to believing.

3. Cite ONLY numbers from the `usable_facts` packet. No numbers from
   anywhere else. These are the only computed facts this refusal carries.

4. The case must be two-sided.

5. Say once that the exact post-trade consequence is unavailable.

6. Never name which security to sell.

7. Do not use internal engine identifiers on the user-facing surface.

Return ONLY a JSON object:
{
  "prose": "Your full answer text.",
  "presented_options": [
    {"label": "Option A", "maps_to": "open", "description": ""},
    {"label": "Option B", "maps_to": "open", "description": ""}
  ]
}"""

SYNTHETIC_USER_PROMPT_REFUSAL = """\
ENGINE REFUSAL:
{error}

USABLE FACTS (from the last finalized review — the only numbers you may cite):
{usable_facts}

USER'S DECISION CONTEXT:
{decision_context}

Produce the answer JSON now."""


# ──────────────────────────── fixture setup ───────────────────────────────────

def _offline_env():
    """Same offline contract as run_episodes.py / tests/persona_sweep.py."""
    env = dict(os.environ)
    stubs = pathlib.Path(tempfile.mkdtemp())
    (stubs / "yfinance.py").write_text('raise ImportError("offline stub")\n', encoding="utf-8")
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(stubs), env.get("PYTHONPATH")) if part)
    return env, stubs


def _setup_fixture(scenario, workdir):
    """Run ``review.py prepare`` to build a ledger for the scenario."""
    root = workdir / "root"
    env, stubs = _offline_env()
    env["TRADE_COACH_HOME"] = str(root)

    fixture = scenario["fixture"]
    trades = pathlib.Path(fixture["trades"]).relative_to("skills/fomo-kernel")

    argv = [sys.executable, "engine/review.py", "prepare", str(trades),
            "--language", fixture["locale"], "--route", fixture["route"]]
    if fixture.get("driver_map"):
        driver_map = pathlib.Path(fixture["driver_map"]).relative_to("skills/fomo-kernel")
        argv += ["--driver-map", str(driver_map)]

    done = subprocess.run(argv, cwd=SKILL_DIR, capture_output=True, text=True, env=env)
    plans = list(root.glob(".pending/*/plan.json"))
    if done.returncode != 0 or len(plans) != 1:
        tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or [""]
        return None, None, stubs, f"prepare failed (rc={done.returncode}): {tail[0]}"

    plan = json.loads(plans[0].read_text(encoding="utf-8"))

    # If scenario needs a finalized review (for usable_facts on refusal),
    # run a minimal finalize to populate last_state.json.
    if scenario.get("needs_finalized_review"):
        session_dir = plans[0].parent
        session_id = session_dir.name
        finalize_argv = [sys.executable, "engine/review.py", "finalize",
                         "--root", str(root), "--session", session_id,
                         "--language", fixture["locale"]]
        finalize_done = subprocess.run(finalize_argv, cwd=SKILL_DIR,
                                       capture_output=True, text=True, env=env)
        if finalize_done.returncode != 0:
            # Some finalize failures are expected (missing answers etc).
            # For the refusal scenario, we mainly need last_state.json.
            # Try to create a minimal one from the plan if finalize fails.
            _write_minimal_last_state(root, plan)

    return plan, root, stubs, None


def _write_minimal_last_state(root, plan):
    """Write a minimal last_state.json from the plan for refusal scenarios."""
    engine_state = plan.get("engine_state") or {}
    metrics = engine_state.get("metrics") or {}
    commitment = None
    rules = plan.get("card_plan", {}).get("candidate_rules") or []
    if rules:
        rule = rules[0]
        commitment = {
            "rule": rule.get("user_text") or rule.get("label") or "Sample rule",
            "metric_key": rule.get("metric_key") or "max_pos_pct",
            "metric_value": metrics.get(rule.get("metric_key") or "max_pos_pct", 0.3),
            "goal": rule.get("goal") or "down",
        }
    state = {**metrics}
    if commitment:
        state["commitment"] = commitment
    state_path = root / "last_state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ──────────────────────────── consider invocation ────────────────────────────

def _run_consider(scenario, root, env):
    """Run ``review.py consider`` and return the parsed stdout JSON."""
    premise = json.dumps(scenario["premise"], ensure_ascii=False)
    argv = [sys.executable, "engine/review.py", "consider",
            "--root", str(root), "--premise", premise]

    if scenario.get("decision_context"):
        ctx = json.dumps(scenario["decision_context"], ensure_ascii=False)
        argv += ["--decision-context", ctx]

    fixture = scenario["fixture"]
    if fixture.get("driver_map"):
        driver_map = pathlib.Path(fixture["driver_map"]).relative_to("skills/fomo-kernel")
        argv += ["--driver-map", str(driver_map)]

    done = subprocess.run(argv, cwd=SKILL_DIR, capture_output=True, text=True,
                          env=env, timeout=120)

    try:
        payload = json.loads(done.stdout)
    except json.JSONDecodeError:
        return None, f"consider stdout is not JSON: {done.stdout[:200]}"

    return payload, None


# ──────────────────────────── LLM call ───────────────────────────────────────

def _build_prompt(payload, scenario):
    """Build the synthetic user prompt from the engine payload."""
    if payload.get("status") == "error":
        # This is a refusal — use the framing prompt
        return (SYNTHETIC_USER_SYSTEM_REFUSAL,
                SYNTHETIC_USER_PROMPT_REFUSAL.format(
                    error=payload.get("error", ""),
                    usable_facts=json.dumps(payload.get("usable_facts"), indent=2,
                                            ensure_ascii=False),
                    decision_context=json.dumps(scenario.get("decision_context"),
                                                indent=2, ensure_ascii=False)))

    challenge = payload.get("challenge") or {}
    consequence = payload.get("consequence") or {}

    return (SYNTHETIC_USER_SYSTEM,
            SYNTHETIC_USER_PROMPT_TEMPLATE.format(
                payload=json.dumps({
                    "basis": payload.get("basis"),
                    "consequence": consequence,
                    "rule_collisions": payload.get("rule_collisions"),
                }, indent=2, ensure_ascii=False),
                challenge=json.dumps(challenge, indent=2, ensure_ascii=False),
                disclosures=json.dumps(
                    (consequence.get("after") or consequence.get("disclosures") or []),
                    ensure_ascii=False),
                unchecked=json.dumps(challenge.get("unchecked", []),
                                     ensure_ascii=False)))


def _parse_answer(raw_text):
    """Extract the JSON answer from LLM output, tolerating fences."""
    text = raw_text.strip()
    # Strip code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
    if text.endswith("```"):
        text = text[:text.rfind("```")]
    text = text.strip()

    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def generate_answer_agy(model, system_prompt, user_prompt):
    """Generate a consider answer using the agy CLI."""
    combined = system_prompt + "\n\n" + user_prompt
    combined += ("\n\nReturn ONLY a JSON object — no prose, no code fence — "
                 "with keys 'prose' and 'presented_options'.")
    try:
        finished = subprocess.run(
            ["agy", "--model", model, "-p", combined],
            capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return None, "agy timed out"
    if finished.returncode != 0:
        return None, f"agy failed (rc={finished.returncode})"
    answer = _parse_answer(finished.stdout)
    if answer is None:
        return None, f"could not parse agy output as JSON: {finished.stdout[:200]}"
    return answer, None


def generate_answer_anthropic(model, client, anthropic_mod, system_prompt, user_prompt):
    """Generate a consider answer using the Anthropic SDK."""
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except anthropic_mod.APIError as exc:
        return None, f"anthropic API error: {exc}"
    text = response.content[0].text if response.content else ""
    answer = _parse_answer(text)
    if answer is None:
        return None, f"could not parse anthropic output as JSON: {text[:200]}"
    return answer, None


# ──────────────────────────── mechanical checks ──────────────────────────────

def _build_facts_for_consider(plan, scenario, payload):
    """Build the facts dict the mechanical checks need.

    Reuses ``run_episodes.engine_facts`` for the plan-based facts, then
    augments with consider-specific data.
    """
    # Build a minimal episode-shaped dict for engine_facts
    episode_shape = {
        "fixture": scenario["fixture"],
        "question": {"kind": "free_form", "asked_by": "user",
                     "text": "consider evaluation"},
    }
    facts = R.engine_facts(plan, episode_shape)

    # Augment with consider payload numbers
    if payload.get("status") != "error":
        for _key, value in R._leaves(payload):
            if isinstance(value, bool) or value is None:
                continue
            if isinstance(value, (int, float)):
                facts["numbers"].add(float(value))
            elif isinstance(value, str):
                for match in R.NUMBER.finditer(value):
                    facts["numbers"].add(float(match.group(0).replace(",", "")))
                facts["tokens"].update(R.UPPER_TOKEN.findall(value))
                for date in R.DATE.findall(value):
                    facts["dates"].add(date)
    else:
        # Refusal shape: usable_facts is the bounded allow-set
        uf = payload.get("usable_facts") or {}
        concentration = uf.get("concentration") or {}
        for key in R.CONSIDER_REFUSAL_CONCENTRATION_KEYS:
            value = concentration.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            value = float(value)
            facts["usable_facts_numbers"].add(value)
            for places in (0, 1, 2):
                facts["usable_facts_numbers"].add(round(value * 100, places))
        commitment = uf.get("commitment") or {}
        for key in ("metric_value",):
            value = commitment.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value = float(value)
                facts["usable_facts_numbers"].add(value)
                for places in (0, 1, 2):
                    facts["usable_facts_numbers"].add(round(value * 100, places))

    return facts


def run_mechanical_checks(scenario, answer, facts):
    """Run the declared mechanical checks and return findings."""
    all_findings = {}
    for name in scenario["checks"]:
        # Build a minimal episode shape for checks that need it
        episode_shape = {
            "fixture": scenario["fixture"],
            "question": {"kind": "free_form", "asked_by": "user",
                         "text": "consider evaluation"},
            "must_disclose": [],
        }
        findings, looked = R.run_check(name, episode_shape, answer, facts)
        if not looked:
            all_findings[name] = [f"check {name!r} had nothing to inspect"]
        elif findings:
            all_findings[name] = findings
    return all_findings


# ──────────────────────────── judge integration ──────────────────────────────

CONSIDER_JUDGE_AXES = ("two_sided", "overrulable")

CONSIDER_RUBRIC = {
    "two_sided": J.RUBRIC["two_sided"],
    "overrulable": J.RUBRIC["overrulable"],
}

CONSIDER_JUDGE_SYSTEM = """\
You grade one answer produced by a trade-review product's consider (pre-trade
evaluation) feature, on named axes only.

The product computes the consequence of one hypothetical trade against the
user's current book and produces a textual answer. You are given that answer
and one or more axes. For each axis, return `pass` or `fail`.

Four rules govern every verdict:

1. Grade the stance the answer takes, never the way it is written.
2. Judge only the axes you are given.
3. You are not comparing this answer to a better one.
4. When an axis genuinely does not apply, fail it and say so.

Give one sentence of reason per axis. When the verdict is `fail`, quote the
part of the answer that decides it."""


def _consider_material(scenario, answer):
    """What the judge sees."""
    lines = [f"THE CONSIDER EVALUATION ({scenario['id']}):",
             scenario["title"], "",
             "THE ANSWER, AS THE USER MET IT:"]
    prose = answer.get("prose") or ""
    lines.append(f"  [prose] {prose}")
    for i, opt in enumerate(answer.get("presented_options") or []):
        lines.append(f"  [option[{i}].label] {opt.get('label', '')}")
    return "\n".join(lines)


def _consider_judge_prompt(scenario, answer, axes):
    parts = ["AXES TO JUDGE:"]
    for axis in axes:
        rubric = CONSIDER_RUBRIC[axis]
        parts += [f"\n### {axis} — {rubric['one_line']}",
                  f"Holds when: {rubric['holds']}",
                  f"Breaks when: {rubric['breaks']}"]
    parts += ["", "─" * 60, "", _consider_material(scenario, answer)]
    return "\n".join(parts)


def judge_consider_answer(scenario, answer, axes, backend, model):
    """Judge one consider answer. Returns verdicts dict or None."""
    if backend == "agy":
        shape = {axis: {"verdict": "pass|fail", "reason": "one sentence"} for axis in axes}
        prompt = (CONSIDER_JUDGE_SYSTEM + "\n\n"
                  + _consider_judge_prompt(scenario, answer, axes)
                  + "\n\nReturn ONLY a JSON object:\n"
                  + json.dumps(shape, indent=2))
        try:
            finished = subprocess.run(
                ["agy", "--model", model, "-p", prompt],
                capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return None
        if finished.returncode != 0:
            return None
        return J._parse_verdicts(finished.stdout, axes)
    elif backend == "anthropic":
        import anthropic
        client = anthropic.Anthropic()
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4000,
                system=CONSIDER_JUDGE_SYSTEM,
                tools=[J._tool(axes)],
                tool_choice={"type": "tool", "name": "record_verdicts"},
                messages=[{"role": "user",
                           "content": _consider_judge_prompt(scenario, answer, axes)}],
            )
        except anthropic.APIError:
            return None
        try:
            return next(block for block in response.content
                        if block.type == "tool_use").input
        except StopIteration:
            return None
    return None


# ──────────────────────────── result recording ───────────────────────────────

def _result(scenario, status, answer, payload, mechanical, judge_verdicts,
            model_name):
    """Build a structured result record."""
    return {
        "scenario_id": scenario["id"],
        "title": scenario["title"],
        "status": status,
        "declared_by": "agent",
        "mechanical_findings": mechanical,
        "judge_verdicts": judge_verdicts,
        "answer": answer,
        "engine_payload_hash": hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:16] if payload else None,
        "model": model_name,
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


# ──────────────────────────── the runner ─────────────────────────────────────

JUDGE_RUNS = int(os.environ.get("TR_JUDGE_RUNS", "3"))


def run_scenario(scenario, backend, model, do_judge=False):
    """Run one consider scenario end-to-end. Returns a result dict."""
    with tempfile.TemporaryDirectory() as tmp:
        workdir = pathlib.Path(tmp)

        # 1. Setup fixture
        plan, root, stubs, error = _setup_fixture(scenario, workdir)
        if error:
            return _result(scenario, "error", None, None,
                           {"setup": [error]}, {}, model)

        env, _ = _offline_env()
        env["TRADE_COACH_HOME"] = str(root)

        # 2. Run consider
        payload, error = _run_consider(scenario, root, env)
        if error:
            return _result(scenario, "error", None, None,
                           {"consider": [error]}, {}, model)

        # 3. Generate synthetic answer
        system_prompt, user_prompt = _build_prompt(payload, scenario)
        if backend == "agy":
            answer, error = generate_answer_agy(model, system_prompt, user_prompt)
        else:
            import anthropic as anthropic_mod
            client = anthropic_mod.Anthropic()
            answer, error = generate_answer_anthropic(
                model, client, anthropic_mod, system_prompt, user_prompt)

        if error:
            return _result(scenario, "error", None, payload,
                           {"generation": [error]}, {}, model)

        # 4. Mechanical checks
        facts = _build_facts_for_consider(plan, scenario, payload)
        mechanical = run_mechanical_checks(scenario, answer, facts)

        # 5. Judge (optional)
        judge_verdicts = {}
        if do_judge and scenario.get("judge_axes"):
            axes = [a for a in scenario["judge_axes"]
                    if a in CONSIDER_RUBRIC]
            if axes:
                samples = [judge_consider_answer(scenario, answer, axes,
                                                 backend, model)
                           for _ in range(JUDGE_RUNS)]
                for axis in axes:
                    verdict, count = J.vote(
                        [s for s in samples if s is not None], axis)
                    judge_verdicts[axis] = {
                        "verdict": verdict, "runs": JUDGE_RUNS,
                        "votes": count,
                    }

        status = "fail" if mechanical else "pass"
        return _result(scenario, status, answer, payload, mechanical,
                       judge_verdicts, model)


def show_plan(scenarios, backend=None, model=None):
    """Show what would be run without making any LLM calls."""
    print("Consider-flow synthetic-user eval — plan\n")
    total_gen_calls = 0
    total_judge_calls = 0
    for sc in scenarios:
        judge = ", ".join(sc.get("judge_axes", []))
        checks = ", ".join(sc["checks"])
        print(f"  {sc['id']}")
        print(f"    title:   {sc['title']}")
        print(f"    fixture: {sc['fixture']['trades']}")
        print(f"    premise: {json.dumps(sc['premise'], ensure_ascii=False)}")
        print(f"    checks:  {checks}")
        print(f"    judge:   {judge or '(none)'}")
        print()
        total_gen_calls += 1
        total_judge_calls += len(sc.get("judge_axes", [])) * JUDGE_RUNS
    print(f"Total: {len(scenarios)} scenario(s), "
          f"{total_gen_calls} generation call(s), "
          f"{total_judge_calls} judge call(s) at {JUDGE_RUNS} run(s) each")
    print(f"Backend: {backend or 'unresolved'}, Model: {model or 'unresolved'}")
    print(f"All findings will be declared_by: agent (owner_unreviewed)")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", action="store_true",
                        help="show what would be run, no LLM calls")
    parser.add_argument("--scenario", type=str, default=None,
                        help="run a single scenario by id")
    parser.add_argument("--judge", action="store_true",
                        help="include rubric judge (additional LLM calls)")
    parser.add_argument("--output", type=str, default=None,
                        help="write results JSON to this path")
    args = parser.parse_args()

    scenarios = SCENARIOS
    if args.scenario:
        scenarios = [s for s in SCENARIOS if s["id"] == args.scenario]
        if not scenarios:
            valid = ", ".join(s["id"] for s in SCENARIOS)
            sys.exit(f"unknown scenario {args.scenario!r}; choose from: {valid}")

    if args.plan:
        try:
            backend, model = J.resolve_backend()
        except SystemExit:
            backend, model = None, None
        show_plan(scenarios, backend, model)
        return 0

    backend, model = J.resolve_backend()
    print(f"consider eval: backend={backend}, model={model}\n")

    results = []
    failures = 0
    for scenario in scenarios:
        print(f"{'─' * 60}")
        print(f"RUNNING  {scenario['id']}: {scenario['title']}")
        result = run_scenario(scenario, backend, model, do_judge=args.judge)
        results.append(result)

        if result["status"] == "error":
            errors = result["mechanical_findings"]
            for category, msgs in errors.items():
                for msg in msgs:
                    print(f"  ERROR  {category}: {msg}")
            failures += 1
        elif result["status"] == "fail":
            for check_name, findings in result["mechanical_findings"].items():
                for finding in findings:
                    print(f"  FAIL   {check_name}: {finding}")
            failures += 1
        else:
            print(f"  PASS   mechanical checks clean")

        if result.get("judge_verdicts"):
            for axis, v in result["judge_verdicts"].items():
                print(f"  JUDGE  {axis}: {v['verdict']} "
                      f"({v['votes']}/{v['runs']})")

        print(f"  STATUS {result['status']}  "
              f"declared_by={result['declared_by']}")
        print()

    print(f"{'═' * 60}")
    verdict = f"FAIL: {failures} failure(s)" if failures else "PASS"
    print(f"consider eval: {len(scenarios)} scenario(s) — {verdict}")
    print(f"calibration: uncalibrated — all expectations are "
          f"agent-declared (owner_unreviewed)")

    if args.output:
        out_path = pathlib.Path(args.output)
        out_path.write_text(
            json.dumps(results, indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"\nResults written to {out_path}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
