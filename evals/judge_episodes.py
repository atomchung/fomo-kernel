#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The question-episode bank's rubric judge (#417's second half).

``run_episodes.py`` settles what code can settle and says so: it prints an
``unmapped`` note for every sentence that carried no number, quoted no engine
span, and disclosed no honesty key. Those sentences are where the product's
reasoning actually lives, and until now nothing graded them. This harness does —
non-deterministically, which is why it is opt-in and never runs in
``tests/run_all.py`` (``docs/eval-design.md``'s evidence hierarchy keeps
non-deterministic runs out of the default suite).

Two backends, chosen automatically: the Antigravity CLI (``agy``, no API key,
and a different vendor from the one that authored the answers) or the Anthropic
SDK (portable, and shape-guaranteed by forced tool use). See ``resolve_backend``.

Usage:
  python3 evals/judge_episodes.py --plan          # what would be judged, no model calls
  python3 evals/judge_episodes.py                 # judge the whole bank
  python3 evals/judge_episodes.py EP-008          # one episode
  TR_JUDGE_RUNS=5 python3 evals/judge_episodes.py # more votes per verdict
  TR_JUDGE_BACKEND=anthropic python3 evals/judge_episodes.py   # pin the backend
  TR_JUDGE_MODEL=gemini-3.6-flash-high python3 evals/judge_episodes.py

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

Plus two guards the mechanical half does not need. Each verdict is a majority
over ``TR_JUDGE_RUNS`` samples, and a tie is ``ambiguous`` — never silently
resolved toward the expected answer. And on a backend with no shape guarantee, a
reply the harness cannot read is an unusable sample rather than a verdict: it is
dropped, reported, and leaves its axis short of votes. Silence from the judge
must never read as agreement with the judge.
"""
import argparse
import collections
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_episodes as R  # noqa: E402  (offline, no network; see its module docstring)

EFFORT = os.environ.get("TR_JUDGE_EFFORT", "high")
RUNS = int(os.environ.get("TR_JUDGE_RUNS", "3"))

# ── which model answers, and how it is reached ───────────────────────────────
#
# Two backends, because the two things they are good at do not overlap.
#
# `anthropic` is the portable one: any maintainer with their own key can run
# this, including in CI, on a checkout that knows nothing about this machine.
# It also gives the strongest possible guarantee on the response *shape* —
# forced tool use plus `strict: true`, so a malformed verdict is impossible
# rather than merely unlikely.
#
# `agy` is the independent one, and on this repository that matters more than
# it looks. The answers under test were authored by Claude, and a Claude judge
# shares its priors about what good reasoning is; `agy models` reaches Gemini
# and GPT-OSS tiers, so the judge can be a different vendor from the judged.
# It needs no API key. What it cannot offer is a shape guarantee: `agy -p`
# returns plain text, so `_parse_verdicts` has to fail closed instead.
#
# Default is `auto`: whichever is actually available, agy first. A maintainer
# who has neither is told both routes rather than one.
BACKEND = os.environ.get("TR_JUDGE_BACKEND", "auto")
DEFAULT_MODELS = {"anthropic": "claude-opus-5", "agy": "gemini-3.1-pro-high"}
MODEL = os.environ.get("TR_JUDGE_MODEL")  # resolved per backend in `resolve_backend`
AGY_ATTEMPTS = 3
AGY_TIMEOUT_SECONDS = 300
STRUCTURED_MAX_TOKENS = 16000
STRUCTURED_TOOL_CHOICE = {"type": "tool", "name": "record_verdicts"}


def _resolve_agy_path(configured=None, *, which=shutil.which):
    """Resolve an executable override or the first ``agy`` on PATH.

    The returned path is absolute so the executable probed by
    ``installed_backends`` is the executable later handed to ``subprocess``.
    An explicit override is authoritative: an invalid override does not quietly
    fall through to a different installation on PATH.
    """
    candidate = os.path.expanduser(configured) if configured else which("agy")
    if not candidate:
        return None
    candidate = os.path.abspath(candidate)
    if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
        return None
    return candidate


AGY_PATH = _resolve_agy_path(os.environ.get("TR_JUDGE_AGY_PATH"))

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


def _tool(axes, *, rubric=None):
    """A forced-tool schema carrying exactly the axes in scope, and nothing else."""
    rubric = RUBRIC if rubric is None else rubric
    properties = {}
    for axis in axes:
        properties[axis] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "reason"],
            "properties": {
                "verdict": {"type": "string", "enum": ["pass", "fail"],
                            "description": rubric[axis]["one_line"]},
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


def _prompt(episode, answer, axes, *, rubric=None, material_fn=None):
    rubric = RUBRIC if rubric is None else rubric
    material_fn = material if material_fn is None else material_fn
    parts = ["AXES TO JUDGE:"]
    for axis in axes:
        parts += [f"\n### {axis} — {rubric[axis]['one_line']}",
                  f"Holds when: {rubric[axis]['holds']}",
                  f"Breaks when: {rubric[axis]['breaks']}"]
    parts += ["", "─" * 60, "", material_fn(episode, answer)]
    return "\n".join(parts)


def installed_backends():
    """What this machine can actually reach. The only impure part of resolution."""
    return {"agy": AGY_PATH is not None,
            "anthropic": importlib.util.find_spec("anthropic") is not None}


def resolve_backend(name=BACKEND, available=None):
    """``(backend, model)``, or raise with both routes named.

    ``available`` is injected so the resolution *logic* can be tested on a
    machine that has neither backend — which is every CI runner, since the
    offline suite depends on neither by design. A probe that called this bare
    would be asserting what happens to be installed rather than what the code
    decides, which is the same shape as the fake green this workstream already
    caught once (a probe reading the state a gate produces instead of driving
    the gate). Found by CI, which had neither backend and went red while the
    author's machine stayed green.
    """
    available = installed_backends() if available is None else available
    if name == "auto":
        # agy first: it needs no key, so on a machine that has both this is the
        # route that runs without further setup.
        name = next((candidate for candidate in ("agy", "anthropic") if available[candidate]), None)
        if name is None:
            raise SystemExit(
                "the rubric judge needs a model. Either install the Antigravity CLI "
                "(`agy`, no API key needed) or `pip install anthropic` and set a key. "
                "The offline suite deliberately depends on neither — run "
                "`python3 evals/run_episodes.py` for the mechanical half.")
    if name not in DEFAULT_MODELS:
        raise SystemExit(f"unknown TR_JUDGE_BACKEND {name!r}; choose "
                         f"{' or '.join(sorted(DEFAULT_MODELS))}, or leave it unset for auto")
    if not available[name]:
        raise SystemExit(f"TR_JUDGE_BACKEND={name} was requested but it is not installed here")
    return name, MODEL or DEFAULT_MODELS[name]


# The one thing a JSON-mode API gives for free and a CLI cannot: a guaranteed
# shape. Everything below is what replaces that guarantee.
FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.M)


def extract_json_object(raw):
    """The outermost JSON object in free CLI text, or ``None``.

    Shared with the narrative judge (#511): both stand in for a shape guarantee
    the CLI cannot give, so both have to fail closed on the same inputs. What
    the object must *contain* stays with each caller — this only finds it.
    """
    text = FENCE.sub("", raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_verdicts(raw, axes):
    """Verdicts from free text, or ``None`` when the text does not carry them.

    Returns None rather than raising, and never guesses: a response this cannot
    read is an unusable *sample*, which `grade_answer` folds into the same
    `ambiguous` state a split vote produces — a failure. Inferring a verdict
    from prose is how a judge harness starts agreeing with itself.
    """
    parsed = extract_json_object(raw)
    if parsed is None:
        return None
    verdicts = {}
    for axis in axes:
        entry = parsed.get(axis)
        reason = entry.get("reason") if isinstance(entry, dict) else None
        if (not isinstance(entry, dict)
                or entry.get("verdict") not in {"pass", "fail"}
                or not isinstance(reason, str)
                or not reason.strip()):
            return None      # a partial answer is not a sample; all-or-nothing
        verdicts[axis] = {"verdict": entry["verdict"],
                          "reason": reason.strip()}
    return verdicts


def _agy_prompt(episode, answer, axes, *, system=None, rubric=None,
                material_fn=None):
    shape = {axis: {"verdict": "pass|fail", "reason": "one sentence"} for axis in axes}
    system = SYSTEM if system is None else system
    return (system + "\n\n" + _prompt(
        episode, answer, axes, rubric=rubric, material_fn=material_fn) +
            "\n\nReturn ONLY a JSON object — no prose, no code fence — shaped exactly:\n"
            + json.dumps(shape, indent=2))


def cli_call_spec(model, prompt):
    """The canonical CLI invocation for one sample, whatever the prompt says.

    Every judge that reaches this backend goes through here, so the argv shape,
    the timeout and the attempt budget are decided once (#511). A judge that
    assembled its own argv would be a second place to keep the `agy` contract
    below in sync.
    """
    return {
        "argv": [AGY_PATH, "-p", prompt, "--model", model,
                 "--effort", EFFORT],
        "subprocess_kwargs": {
            "capture_output": True,
            "text": True,
            "timeout": AGY_TIMEOUT_SECONDS,
        },
        "max_attempts": AGY_ATTEMPTS,
    }


def agy_call_spec(model, episode, answer, axes, *, system=None, rubric=None,
                  material_fn=None):
    """Canonical description of the exact CLI call used for one sample."""
    return cli_call_spec(model, _agy_prompt(
        episode, answer, axes, system=system, rubric=rubric,
        material_fn=material_fn))


def run_cli_sample(call, axes, parse=None):
    """One sample through the Antigravity CLI. Returns parsed output or None.

    Three constraints, all load-bearing (`agy` 1.1.7): headless `agy` does not
    read stdin at all, so the prompt travels in the argument; the call goes
    through an argument list and never a shell, because episode prose contains
    backticks and `$` a shell would expand into something else entirely; and a
    timeout is mandatory, since a hung CLI would otherwise stall the run with
    no verdict and no error.

    `parse` is what the caller's own reply shape needs; it must fail closed by
    returning None, never by guessing. The narrative judge grades 0–5 scores
    rather than pass/fail verdicts, and shares everything above.
    """
    parse = _parse_verdicts if parse is None else parse
    if not call.get("argv") or call["argv"][0] is None:
        raise RuntimeError("agy judge call could not start: no executable was resolved")
    empty_retried = False
    for attempt in range(call["max_attempts"]):
        try:
            finished = subprocess.run(
                call["argv"], **call["subprocess_kwargs"])
        except subprocess.TimeoutExpired:
            continue
        except OSError as exc:
            # argv can exceed ARG_MAX, and an executable can disappear between
            # discovery and launch. Both are failed samples that the caller
            # must receipt, not process-level exits that erase the run.
            raise RuntimeError(f"agy judge call could not start: {exc}") from exc
        if finished.returncode == 0:
            if finished.stdout.strip():
                return parse(finished.stdout, axes)
            # The CLI contract asks for one retry on an empty success. Do not
            # spend all three attempts on a fluent but malformed JSON reply —
            # parsing that fail-closed output remains the vote interlock's job.
            if empty_retried:
                return None
            empty_retried = True
            continue
        transient = "Agent execution terminated due to error." in (
            (finished.stderr or "") + (finished.stdout or ""))
        if not transient:
            return None
        # agy already applies backend backoff internally, but a surfaced
        # 502/503 remains retryable at the caller boundary (skill contract).
        if attempt + 1 == call["max_attempts"]:
            return None
    return None


def judge_once_agy(model, episode, answer, axes, *, system=None, rubric=None,
                   material_fn=None, call_spec=None):
    """One episode-bank sample through the CLI backend. Verdicts or None."""
    call = call_spec or agy_call_spec(
        model, episode, answer, axes, system=system, rubric=rubric,
        material_fn=material_fn)
    return run_cli_sample(call, axes)


def structured_call_spec(model, episode, answer, axes, *, system=None,
                         rubric=None, material_fn=None):
    """Canonical kwargs handed to the structured-tool model client."""
    system = SYSTEM if system is None else system
    return {
        "model": model,
        "max_tokens": STRUCTURED_MAX_TOKENS,
        "system": system,
        "output_config": {"effort": EFFORT},
        "tools": [_tool(axes, rubric=rubric)],
        "tool_choice": dict(STRUCTURED_TOOL_CHOICE),
        "messages": [{"role": "user", "content": _prompt(
            episode, answer, axes, rubric=rubric, material_fn=material_fn)}],
    }


def judge_once(model, client, anthropic, episode, answer, axes, *, system=None,
               rubric=None, material_fn=None, request=None):
    """One sample. Returns ``{axis: {"verdict", "reason"}}``."""
    request = request or structured_call_spec(
        model, episode, answer, axes, system=system, rubric=rubric,
        material_fn=material_fn)
    try:
        response = client.messages.create(**request)
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


def _usable_axis_entry(sample, axis):
    entry = sample.get(axis) if isinstance(sample, dict) else None
    reason = entry.get("reason") if isinstance(entry, dict) else None
    return (entry if isinstance(entry, dict)
            and entry.get("verdict") in {"pass", "fail"}
            and isinstance(reason, str) and reason.strip() else None)


def axis_report(samples, axis, expected):
    """Return the complete, auditable vote record for one axis.

    The report preserves run order and every reason. A missing axis is
    unusable just like an unreadable sample; it never disappears into a
    denominator that makes the majority look stronger than it was.
    """
    sample_rows = []
    usable = []
    for run, sample in enumerate(samples, start=1):
        entry = _usable_axis_entry(sample, axis)
        if entry is None:
            sample_rows.append({"run": run, "usable": False})
            continue
        reason = entry["reason"]
        row = {"run": run, "usable": True, "verdict": entry["verdict"],
               "reason": reason.strip()}
        sample_rows.append(row)
        usable.append({axis: {"verdict": entry["verdict"],
                              "reason": reason.strip()}})
    verdict, count = vote(usable, axis)
    tally = collections.Counter(row["verdict"] for row in sample_rows
                                if row["usable"])
    unusable = sum(1 for row in sample_rows if not row["usable"])
    classification = ("ambiguous" if verdict == "ambiguous" or unusable else
                      "agreement" if verdict == expected else "disagreement")
    return {
        "expected": expected,
        "verdict": verdict,
        "majority_count": count,
        "tally": {name: tally.get(name, 0) for name in ("pass", "fail")},
        "unusable": unusable,
        "samples": sample_rows,
        "classification": classification,
        "matched": classification == "agreement",
    }


# ── the run ──────────────────────────────────────────────────────────────────

def declared(episode):
    """``(axes, declared_by)`` for an episode that opted into judging, else ``()``."""
    block = episode.get("judge") or {}
    return tuple(block.get("axes") or ()), block.get("declared_by")


def grade_answer_report(episode, answer, axes, samples):
    """Turn samples into failures, observed verdicts, and an auditable report.

    Split out from ``main`` on purpose. Everything that decides whether a judge
    run is green lives here and is a pure function of the samples, so
    ``tests/test_episode_checkers.py`` can prove each interlock fires against
    stubbed verdicts, offline and for free. An interlock only reachable by
    someone holding an API key is an interlock nobody re-verifies.
    """
    failures, observed = [], {}
    expected_fail = set(answer.get("judge_fails") or [])
    tag = f"{episode['id']}/{answer['id']}"
    original_samples = list(samples)
    runs = len(original_samples)
    # A backend with no shape guarantee returns None when its reply could not be
    # read. Those samples are dropped, never guessed at, and their absence is
    # reported: an axis left with no verdict votes `ambiguous` below, which is a
    # failure. Silence from the judge must not read as agreement with it.
    wholly_unreadable = sum(1 for sample in original_samples
                            if not isinstance(sample, dict))
    fully_usable = sum(
        1 for sample in original_samples
        if isinstance(sample, dict)
        and all(_usable_axis_entry(sample, axis) is not None for axis in axes))
    unusable = runs - fully_usable
    if wholly_unreadable:
        failures.append(f"{tag}: {wholly_unreadable}/{runs} sample(s) came back unreadable — "
                        f"a reply the harness cannot parse is not a verdict")
    axis_reports = {}
    for axis in axes:
        want = "fail" if axis in expected_fail else "pass"
        axis_reports[axis] = axis_report(original_samples, axis, want)
        verdict = axis_reports[axis]["verdict"]
        count = axis_reports[axis]["majority_count"]
        partial = axis_reports[axis]["unusable"] - wholly_unreadable
        if partial:
            failures.append(f"{tag}: {partial}/{runs} sample(s) omitted {axis} — "
                            "a partial reply is not a verdict")
        classification = axis_reports[axis]["classification"]
        if verdict == "ambiguous":
            failures.append(
                f"{tag}: {axis} split {count}/{runs} — the judge does not reproduce "
                f"its own verdict, so it cannot grade this axis")
            continue
        if classification == "ambiguous":
            # The majority is retained as evidence, but any unusable vote keeps
            # this answer-axis out of both agreement and disagreement rates.
            continue
        observed.setdefault(axis, set()).add(verdict)
        if verdict != want:
            reason = next((row["reason"] for row in axis_reports[axis]["samples"]
                           if row.get("verdict") == verdict and row.get("reason")), "")
            # Interlock 2: an answer that trips the wrong axis has stopped grading
            # what it was written to grade, and the message says which way round.
            kind = ("was expected to fail this axis and passed" if want == "fail"
                    else "was not written to fail this axis")
            failures.append(f"{tag}: {axis} {kind} — got {verdict} ({count}/{runs})."
                            + (f" Judge said: {reason}" if reason else ""))
    report = {
        "tag": tag,
        "runs": runs,
        "usable_samples": len(original_samples) - unusable,
        "unusable_samples": unusable,
        "axes": axis_reports,
        "matched": not failures,
    }
    return failures, observed, report


def grade_answer(episode, answer, axes, samples):
    """Compatibility wrapper returning the original two-value result."""
    failures, observed, _ = grade_answer_report(episode, answer, axes, samples)
    return failures, observed


def nothing_to_judge(episodes, selected):
    """The reason this run would grade nothing, or ``None`` if it would grade something.

    Every other interlock here asks whether a *verdict* was right. This one asks
    whether any verdict happened at all, and it is the one a filtered run can
    still trip: `judge_episodes.py EP-001` selects an episode that declares no
    axes, so no failure is reachable, coverage is skipped as unevaluable, and the
    run printed PASS having made zero model calls. That is the "green while
    grading nothing" shape this repository rejects, and it survived the first
    mutation dance because every mutation there assumed something was graded.
    Found in external review, 2026-07-27.
    """
    if any(declared(episode)[0] for episode in episodes):
        return None
    where = f" (selected: {', '.join(sorted(selected))})" if selected else " in the bank"
    return (f"nothing to judge — no episode{where} declares any judge axis, so this run "
            f"would grade no answer and its result could not be read as a pass")


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


def plan(episodes, backend=None, model=None):
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
    print(f"\n{calls} model call(s) at {RUNS} run(s) per answer, "
          f"backend={backend or 'unresolved'}, model={model or 'unresolved'}")
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
        # Resolve if we can, but never fail a dry run on a missing backend: the
        # point of --plan is to see the shape and the cost before installing
        # anything.
        try:
            backend, model = resolve_backend()
        except SystemExit as reason:
            backend, model = None, None
            print(f"NOTE  no backend resolved yet — {reason}")
        plan(episodes, backend, model)
        return 0

    # Before anything is spent: a run that would grade nothing is not a passing
    # run. Checked here rather than at the end so it fails without an API key,
    # and so a probe can reach it.
    empty = nothing_to_judge(episodes, args.episode)
    if empty:
        print(f"FAIL  {empty}")
        return 1

    backend, model = resolve_backend()
    anthropic = client = None
    if backend == "anthropic":
        import anthropic
        client = anthropic.Anthropic()
    sample_one = (
        (lambda episode, answer, axes: judge_once_agy(model, episode, answer, axes))
        if backend == "agy" else
        (lambda episode, answer, axes: judge_once(model, client, anthropic, episode, answer, axes)))

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
            samples = [sample_one(episode, answer, axes) for _ in range(RUNS)]
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
    print(f"\nrubric judge ({backend}, {model}): {judged_episodes} judged episode(s) — {verdict}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
