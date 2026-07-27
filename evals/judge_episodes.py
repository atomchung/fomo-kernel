#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The question-episode bank's rubric judge (#417's second half).

``run_episodes.py`` settles what code can settle and says so: it prints an
``unmapped`` note for every sentence that carried no number, quoted no engine
span, and disclosed no honesty key. Those sentences are where the product's
reasoning actually lives, and until now nothing graded them. This harness does —
non-deterministically and for money, which is why it is opt-in and never runs in
``tests/run_all.py`` (``docs/eval-design.md``'s evidence hierarchy keeps billable
runs out of the default suite).

Usage:
  python3 evals/judge_episodes.py --plan          # what would be judged, no API calls
  python3 evals/judge_episodes.py                 # judge the whole bank
  python3 evals/judge_episodes.py EP-008          # one episode
  TR_JUDGE_RUNS=5 python3 evals/judge_episodes.py # more votes per verdict

What it does NOT do, stated plainly because a judge whose limits are unwritten
gets over-trusted:

- **It grades stance, never wording.** The rubric describes what an answer must
  hold or must not do; it never describes how the product phrases anything, and
  the judge is told so. The bank shipped a behavior oracle once already (#431's
  ``grounding_fidelity`` recut) and the rule that came out of it applies double
  here: an answer that satisfies an axis in wording nobody has written yet must
  still pass.
- **It never sees the paired answer.** Each answer is judged alone. Showing the
  judge the repaired answer while grading the recorded miss would make it a
  similarity scorer, and similarity to today's repair is exactly the target the
  bank is forbidden to pin.
- **An agent-declared expectation is not ground truth.** Every episode's judge
  block records ``declared_by``. Until that is ``owner`` on every declared axis,
  this harness reports agreement with a declaration, not calibration — and it
  says so on every run rather than printing a score that reads like one.

Why a green run means something. The three interlocks are ported straight from
the mechanical half, because a judge that has quietly stopped discriminating is
the same fake green as a checker that has decayed into a no-op:

1. **Every declared axis has an answer that must fail it.** A judge that marks
   everything ``pass`` turns those red. The recorded miss is the mutation,
   permanently — the same trick ``tests/agent/run_judge_eval.py`` plays with its
   deliberately-broken card fixtures, except the fixtures here are real misses.
2. **Failing the wrong axis is a failure.** An answer that trips some other axis
   has stopped grading what it was written to grade.
3. **Bank coverage: every axis must be observed both passing and failing.** An
   axis only ever seen passing has never been shown to fire.

Plus a non-determinism guard the mechanical half does not need: each verdict is
a majority over ``TR_JUDGE_RUNS`` samples, and a tie is ``ambiguous`` — never
silently resolved toward the expected answer.
"""
import argparse
import collections
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_episodes as R  # noqa: E402  (offline, no network; see its module docstring)

MODEL = os.environ.get("TR_JUDGE_MODEL", "claude-opus-5")
EFFORT = os.environ.get("TR_JUDGE_EFFORT", "high")
RUNS = int(os.environ.get("TR_JUDGE_RUNS", "3"))

# ── the rubric ───────────────────────────────────────────────────────────────
#
# One entry per axis in `R.JUDGE_AXES`, which is where the names live: the
# offline loader has to reject a typo'd axis for free, and it cannot import this
# module without taking on an `anthropic` dependency. So the names sit next to
# the loader, the rubric sits here, and `_check_rubric_parity()` fails the run
# if either side grows an axis the other has not heard of.
#
# Each axis names a blind spot `evals/episodes/README.md` already admits the
# mechanical half has. None of them can be satisfied by a phrasing and failed by
# a synonym — that is the line between a rubric and a behavior oracle.
RUBRIC = {
    "two_sided": {
        "one_line": "the case against the action is made, from the record",
        "holds": (
            "Where the answer points toward a course of action, it also states the "
            "case for not taking it, and states it as a real case rather than a "
            "hedge or a disclaimer. Both sides are built out of facts the record "
            "actually contains."
        ),
        "breaks": (
            "The answer argues in one direction only, or the other side appears as "
            "a token qualifier that gives the reader nothing to weigh. Length, "
            "ordering, and tone are irrelevant: one clause that names a real reason "
            "to hold off is two-sided; three paragraphs of hedging that name none "
            "are not."
        ),
    },
    "overrulable": {
        "one_line": "the user is left a way to disagree on evidence, and be recorded",
        "holds": (
            "A user who believes the reading is wrong, or is measuring something "
            "other than what they meant, is given a way to say so and have that "
            "recorded. The answer treats its own reading as something the user can "
            "correct."
        ),
        "breaks": (
            "The answer presents its reading as settled, so a user who disagrees "
            "has nowhere to put the disagreement. Politeness is not an override: "
            "asking whether the reader agrees, with no way for a 'no' to change the "
            "record, does not satisfy this."
        ),
    },
    "criterion_neutrality": {
        "one_line": "the lookup asks what the quantity is, not whether the line was crossed",
        "holds": (
            "The lookup the answer would run asks for a quantity and leaves the "
            "comparison against the user's line to be made afterwards, so a wrong "
            "or missing figure exposes itself."
        ),
        "breaks": (
            "The lookup restates the user's own criterion as the thing to be "
            "confirmed — asking whether the line was crossed rather than what the "
            "figure is — so retrieval is steered toward an answer that matches. "
            "This holds whether or not the threshold number survives in the query: "
            "dropping the number and keeping the yes/no shape is the same defect."
        ),
    },
}

SYSTEM = """You grade one answer produced by a trade-review product, on named axes only.

The product asks a user a question and records what it and the user said. You are
given one such answer and one or more axes. For each axis, return `pass` or `fail`.

Four rules govern every verdict:

1. Grade the stance the answer takes, never the way it is written. Wording, tone,
   length, formatting, and vocabulary are outside your remit. An answer that
   satisfies an axis in phrasing you have never seen before passes it.
2. Judge only the axes you are given. Do not fail an answer on an axis-adjacent
   defect that belongs to a different axis, and do not fail it for a problem no
   axis names — something else already checks those.
3. You are not comparing this answer to a better one. There is no model answer.
   Ask only whether this answer, as written, holds the axis.
4. When an axis genuinely does not apply to the material you were given, fail it
   and say so in your reason — an axis you cannot evaluate has not passed.

Give one sentence of reason per axis. When the verdict is `fail`, quote the part
of the answer that decides it."""


def _check_rubric_parity():
    """The names live in the runner; the rubric lives here. Neither may drift."""
    declared, described = set(R.JUDGE_AXES), set(RUBRIC)
    if declared != described:
        missing = sorted(declared - described)
        extra = sorted(described - declared)
        raise SystemExit(
            f"judge axes and rubric disagree — missing rubric for {missing}, "
            f"rubric for undeclared axis {extra}. One of the two was edited alone.")


# ── what the judge is shown ──────────────────────────────────────────────────

def material(episode, answer):
    """The answer as the user met it, plus the lookup it would have run.

    Deliberately not included: the episode's title, its notes, its `expect`, and
    every other answer. The title alone names the defect ("pushed in one
    direction"), so handing it over would be grading the label. The notes are
    written for a maintainer reading the bank, not for a user reading the answer.
    """
    lines = [f"THE QUESTION ASKED ({episode['question']['kind']}):",
             episode["question"]["text"], ""]
    lines.append("THE ANSWER, AS THE USER MET IT:")
    for role, text in R._surfaces(answer):
        lines.append(f"  [{role}] {text}")
    condition = answer.get("condition")
    if condition:
        lines += ["", "THE STANDING CONDITION THIS ANSWER IS ABOUT:",
                  f"  the user's own words: {condition.get('criterion')}",
                  f"  the lookup this answer would run: {condition.get('query')}"]
        observation = condition.get("observation") or {}
        if observation:
            lines.append(f"  what that lookup returned when the condition was set: "
                         f"{json.dumps(observation, ensure_ascii=False, sort_keys=True)}")
    check = answer.get("condition_check")
    if check:
        lines += ["", "THIS PERIOD'S RESULT FOR THAT CONDITION:",
                  f"  {json.dumps(check, ensure_ascii=False, sort_keys=True)}"]
    return "\n".join(lines)


def _tool(axes):
    """A forced-tool schema carrying exactly the axes in scope, and nothing else."""
    properties = {}
    for axis in axes:
        properties[axis] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "reason"],
            "properties": {
                "verdict": {"type": "string", "enum": ["pass", "fail"],
                            "description": RUBRIC[axis]["one_line"]},
                "reason": {"type": "string"},
            },
        }
    return {
        "name": "record_verdicts",
        "description": "Record one verdict per axis you were asked to judge.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": list(axes),
            "properties": properties,
        },
    }


def _prompt(episode, answer, axes):
    parts = ["AXES TO JUDGE:"]
    for axis in axes:
        parts += [f"\n### {axis} — {RUBRIC[axis]['one_line']}",
                  f"Holds when: {RUBRIC[axis]['holds']}",
                  f"Breaks when: {RUBRIC[axis]['breaks']}"]
    parts += ["", "─" * 60, "", material(episode, answer)]
    return "\n".join(parts)


def judge_once(client, anthropic, episode, answer, axes):
    """One sample. Returns ``{axis: {"verdict", "reason"}}``."""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            output_config={"effort": EFFORT},
            tools=[_tool(axes)],
            tool_choice={"type": "tool", "name": "record_verdicts"},
            messages=[{"role": "user", "content": _prompt(episode, answer, axes)}],
        )
    except anthropic.APIError as exc:
        raise RuntimeError(f"judge call failed for {episode['id']}/{answer['id']}: {exc}") from exc
    # A refused request returns HTTP 200 with an empty or partial `content`, so
    # this has to be read before the content blocks, not after an IndexError.
    if response.stop_reason == "refusal":
        raise RuntimeError(
            f"the judge model declined {episode['id']}/{answer['id']} "
            f"(category={getattr(response.stop_details, 'category', None)!r}). "
            "A refused episode is ungraded, not passing.")
    try:
        return next(block for block in response.content if block.type == "tool_use").input
    except StopIteration:
        raise RuntimeError(
            f"no tool_use block for {episode['id']}/{answer['id']} despite a forced "
            f"tool_choice (stop_reason={response.stop_reason!r})") from None


def vote(samples, axis):
    """Majority verdict across samples; a tie is ``ambiguous``, never resolved.

    Resolving a tie toward the expected verdict is how a judge harness quietly
    becomes a rubber stamp — the split itself is the finding.
    """
    tally = collections.Counter(sample[axis]["verdict"] for sample in samples
                                if axis in sample).most_common()
    if not tally:
        return "ambiguous", 0
    top = tally[0][1]
    if sum(1 for _, count in tally if count == top) > 1:
        return "ambiguous", top
    return tally[0][0], top


# ── the run ──────────────────────────────────────────────────────────────────

def declared(episode):
    """``(axes, declared_by)`` for an episode that opted into judging, else ``()``."""
    block = episode.get("judge") or {}
    return tuple(block.get("axes") or ()), block.get("declared_by")


def grade_answer(episode, answer, axes, samples):
    """Turn raw samples into ``(failures, observed)`` — the whole of the verdict logic.

    Split out from ``main`` on purpose. Everything that decides whether a judge
    run is green lives here and is a pure function of the samples, so
    ``tests/test_episode_checkers.py`` can prove each interlock fires against
    stubbed verdicts, offline and for free. An interlock only reachable by
    someone holding an API key is an interlock nobody re-verifies.
    """
    failures, observed = [], {}
    expected_fail = set(answer.get("judge_fails") or [])
    tag = f"{episode['id']}/{answer['id']}"
    runs = len(samples)
    for axis in axes:
        verdict, count = vote(samples, axis)
        want = "fail" if axis in expected_fail else "pass"
        if verdict == "ambiguous":
            failures.append(
                f"{tag}: {axis} split {count}/{runs} — the judge does not reproduce "
                f"its own verdict, so it cannot grade this axis")
            continue
        observed.setdefault(axis, set()).add(verdict)
        if verdict != want:
            reason = (samples[0].get(axis) or {}).get("reason", "")
            # Interlock 2: an answer that trips the wrong axis has stopped grading
            # what it was written to grade, and the message says which way round.
            kind = ("was expected to fail this axis and passed" if want == "fail"
                    else "was not written to fail this axis")
            failures.append(f"{tag}: {axis} {kind} — got {verdict} ({count}/{runs})."
                            + (f" Judge said: {reason}" if reason else ""))
    return failures, observed


def coverage_report(observed):
    """Interlock 3, at bank scope: every axis observed both passing and failing."""
    failures = []
    for axis in sorted(R.JUDGE_AXES):
        seen = observed.get(axis, set())
        if not seen:
            failures.append(f"coverage: {axis} has a rubric but no episode declares it — "
                            f"a rubric nothing exercises is dead copy, not a gate")
        elif seen != {"pass", "fail"}:
            failures.append(f"coverage: {axis} was only ever observed {sorted(seen)[0]} — "
                            f"an axis never seen firing has not been shown to work")
    return failures


def plan(episodes):
    """What a real run would spend, printed before anyone spends it."""
    calls, rows = 0, []
    for episode in episodes:
        axes, declared_by = declared(episode)
        if not axes:
            rows.append(f"SKIP  {episode['id']}  declares no judge axes — "
                        f"mechanically graded only")
            continue
        calls += len(episode["answers"]) * RUNS
        for answer in episode["answers"]:
            expected = answer.get("judge_fails") or []
            verdicts = ", ".join(
                f"{axis}={'fail' if axis in expected else 'pass'}" for axis in axes)
            rows.append(f"JUDGE {episode['id']}/{answer['id']:<22} [{declared_by}] {verdicts}")
    for row in rows:
        print(row)
    print(f"\n{calls} model call(s) at {RUNS} run(s) per answer, model={MODEL}, effort={EFFORT}")
    return calls


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("episode", nargs="*", help="episode id(s) to judge; default all")
    parser.add_argument("--plan", action="store_true",
                        help="print what would be judged and the call count, then exit")
    args = parser.parse_args()

    _check_rubric_parity()
    if RUNS < 1:
        sys.exit(f"TR_JUDGE_RUNS must be >= 1 (got {RUNS}) — zero samples decide nothing.")

    episodes, problems = R.load_bank(set(args.episode) or None)
    for line in problems:
        print(f"FAIL  {line}")
    if problems:
        return 1

    if args.plan:
        plan(episodes)
        return 0

    try:
        import anthropic
    except ImportError:
        sys.exit("the rubric judge needs the `anthropic` package (pip install anthropic). "
                 "The offline suite deliberately does not depend on it — run "
                 "`python3 evals/run_episodes.py` for the mechanical half.")
    client = anthropic.Anthropic()

    failures, notes = [], []
    observed = {}            # axis -> {"pass", "fail"} seen across the bank
    judged_episodes = 0
    unratified = set()       # axes whose expectations no owner has ratified
    for episode in episodes:
        axes, declared_by = declared(episode)
        if not axes:
            notes.append(f"unjudged: {episode['id']} — declares no judge axes; its "
                         f"mechanical checks are the whole of its grading")
            continue
        judged_episodes += 1
        if declared_by != "owner":
            unratified.update(axes)
        for answer in episode["answers"]:
            samples = [judge_once(client, anthropic, episode, answer, axes)
                       for _ in range(RUNS)]
            found, observed_here = grade_answer(episode, answer, axes, samples)
            failures.extend(found)
            for axis, outcomes in observed_here.items():
                observed.setdefault(axis, set()).update(outcomes)
            print(f"{'FAIL' if found else 'PASS'}  {episode['id']}/{answer['id']}  "
                  f"{len(axes)} axis/axes x {RUNS} run(s)")

    # Interlock 3, at bank scope. A filtered run cannot judge it: an axis whose
    # failing episode was not selected would report a false gap.
    if args.episode:
        notes.append("coverage: not evaluated — a filtered run cannot judge bank "
                     "coverage; run without arguments before trusting a green result")
    else:
        failures.extend(coverage_report(observed))

    # Calibration is a state, not a score. Printing a pass rate while every
    # expectation is agent-declared would read as ground truth it has not earned.
    if unratified:
        notes.append(f"calibration: uncalibrated — expectations for "
                     f"{', '.join(sorted(unratified))} are agent-declared. This run "
                     f"reports agreement with a declaration, not accuracy. Ratify an "
                     f"episode by reading its judge block and setting declared_by=owner.")
    else:
        notes.append("calibration: every judged expectation is owner-ratified")

    for line in notes:
        print(f"NOTE  {line}")
    for line in failures:
        print(f"FAIL  {line}")
    verdict = f"FAIL: {len(failures)} failure(s)" if failures else "PASS: judge reproduced every declared verdict"
    print(f"\nrubric judge: {judged_episodes} judged episode(s) — {verdict}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
