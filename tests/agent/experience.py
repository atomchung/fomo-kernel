#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Multi-week experience driver for the #60 AI-simulated-user harness.

This is the productized form of the owner's one-off manual session: take a
persona's weekly windows (:mod:`windows`), and for each week drive the *real*
CLI — ``python3 engine/review.py prepare|preview|finalize`` — against an
isolated ``--root``, answering the emitted ``question_queue`` in the persona's
voice with the deterministic :mod:`policies` layer, then choosing or skipping
the single commitment.  Everything is offline: prices never leave the frozen
``--card-json`` / ``--state-json`` artifacts, and an optional per-week CSV is
ingested only to raise exit-capture questions.

Two outputs come from one run:

* a structured :class:`ExperienceResult` the offline assertion suite
  (``tests/test_experience_harness.py``) inspects; and
* a readable Markdown transcript (``render_transcript``) — the design-review
  deliverable that shows the experience the way the user lives it: every
  question verbatim (including woven memory stems), the persona's answer, the
  commitment choice, and the final card text inline.  Same inputs → identical
  bytes, so it diffs cleanly across engine changes.

The harness reuses the #160 checkers (``check_card`` / ``check_state``) rather
than reimplementing card or state invariants.

Ceiling (issue #159 layer (c), EVALS.md): this drives the file/JSON contract,
not the interactive ``AskUserQuestion`` tool path, and it never judges tone.
See EXPERIENCE.md.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
REVIEW = REPO / "skills" / "fomo-kernel" / "engine" / "review.py"

sys.path.insert(0, str(HERE))
from check_card import check_card  # noqa: E402
from check_state import check_state  # noqa: E402
from policies import POLICIES  # noqa: E402
from windows import weeks_for  # noqa: E402


class ExperienceError(RuntimeError):
    pass


@dataclass
class WeekRecord:
    index: int
    label: str
    language: str
    session_id: str
    route: str
    questions: list  # [{id, kind, ticker, question}]
    answers: dict  # the full answer payload sent to the CLI
    narrative: dict
    commitment_choice: str
    private_card: str
    public_card: str
    session_path: str
    private_html_exists: bool
    finalize_status: str
    returning: bool
    prior_commitment_rule: str | None
    root: str = ""
    answers_path: str = ""
    narrative_path: str = ""
    missing_thesis_positions: list = field(default_factory=list)


@dataclass
class ExperienceResult:
    persona: str
    language: str
    weeks: list  # [WeekRecord]


# ── CLI plumbing ────────────────────────────────────────────────────────────

def _cli(*args):
    """Run the real review.py subprocess offline; return parsed JSON stdout."""
    run = subprocess.run([sys.executable, str(REVIEW), *map(str, args)], cwd=REPO,
                         capture_output=True, text=True, timeout=90)
    if run.returncode != 0:
        raise ExperienceError(
            f"review.py {args[0]} exited {run.returncode}\n{run.stdout}\n{run.stderr}")
    try:
        return json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        raise ExperienceError(f"review.py {args[0]} returned non-JSON:\n{run.stdout}") from exc


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ── One week ────────────────────────────────────────────────────────────────

def run_week(root, workdir, policy, window, week_index):
    language = policy.language
    week_dir = pathlib.Path(workdir) / f"week{week_index + 1}"
    week_dir.mkdir(parents=True, exist_ok=True)
    card_path = _write_json(week_dir / "card.json", window.card)
    state_path = _write_json(week_dir / "state.json", window.state)

    prepare_args = ["prepare", "--root", root, "--language", language,
                    "--card-json", card_path, "--state-json", state_path]
    if window.csv:
        csv_path = week_dir / "trades.csv"
        csv_path.write_text(window.csv, encoding="utf-8")
        prepare_args.insert(1, str(csv_path))
    prepared = _cli(*prepare_args)
    plan = prepared.get("review_plan") or {}
    if not plan:
        raise ExperienceError(f"prepare returned no review_plan: {prepared}")

    questions = [{"id": q.get("id"), "kind": q.get("kind"), "ticker": q.get("ticker"),
                  "required": bool(q.get("required")), "question": q.get("question")}
                 for q in plan.get("question_queue") or []]

    answers = policy.build_answers(plan, week_index)
    narrative = policy.build_narrative(plan)
    answers_path = _write_json(week_dir / "answers.json", answers)
    narrative_path = _write_json(week_dir / "narrative.json", narrative)
    commitment_choice = (answers.get("commitment") or {}).get("choice", "skip")

    session_id = plan["session_id"]
    _cli("preview", "--root", root, "--session-id", session_id,
         "--answers", answers_path, "--narrative", narrative_path)
    finalized = _cli("finalize", "--root", root, "--session-id", session_id,
                     "--answers", answers_path, "--narrative", narrative_path)

    session_path = finalized["path"]
    private_card = pathlib.Path(finalized["private_card"]).read_text(encoding="utf-8")
    public_card = pathlib.Path(finalized["public_card"]).read_text(encoding="utf-8")
    prior = ((plan.get("state_snapshot") or {}).get("prior_commitment") or {})
    progress = ((plan.get("state_snapshot") or {}).get("review_progress") or {})

    return WeekRecord(
        index=week_index, label=window.label, language=language, session_id=session_id,
        route=plan.get("route"), questions=questions, answers=answers, narrative=narrative,
        commitment_choice=commitment_choice, private_card=private_card, public_card=public_card,
        session_path=session_path,
        private_html_exists=bool(finalized.get("private_card_html")),
        finalize_status=finalized.get("status"),
        returning=bool(progress.get("returning")),
        prior_commitment_rule=prior.get("rule"),
        root=str(root), answers_path=str(answers_path), narrative_path=str(narrative_path),
        missing_thesis_positions=list(plan.get("missing_thesis_positions") or []),
    )


def run_experience(persona_name, root=None, workdir=None):
    """Drive every weekly window for a persona through the real CLI."""
    policy = POLICIES.get(persona_name)
    if policy is None:
        raise ExperienceError(f"unknown persona: {persona_name!r} (have {sorted(POLICIES)})")
    windows = weeks_for(persona_name)

    cleanup = None
    if root is None:
        cleanup = tempfile.mkdtemp(prefix=f"experience-{persona_name}-")
        root = os.path.join(cleanup, "root")
        workdir = os.path.join(cleanup, "work")
    if workdir is None:
        workdir = os.path.join(root, ".harness-work")
    os.makedirs(workdir, exist_ok=True)

    records = [run_week(root, workdir, policy, window, i) for i, window in enumerate(windows)]
    return ExperienceResult(persona=persona_name, language=policy.language, weeks=records)


def refinalize(week):
    """Re-run finalize on an already-committed session; return (result, card).

    The finalize transaction is idempotent: a committed session re-drafts to the
    identical bundle and rewrites the same bytes, which is what makes the
    documented-safe retry safe.  Used to assert resume/finalize idempotency.
    """
    finalized = _cli("finalize", "--root", week.root, "--session-id", week.session_id,
                     "--answers", week.answers_path, "--narrative", week.narrative_path)
    card = pathlib.Path(finalized["private_card"]).read_text(encoding="utf-8")
    return finalized, card


# ── Checkers (reuse #160, do not reimplement) ───────────────────────────────

def _is_known_checker_limitation(finding):
    """True for a check_card finding that is a known checker/prose collision.

    check_card's A-12 internal-metric-key blocklist (``_INTERNAL_KEYS``)
    includes the bare token ``baseline``.  That token appears verbatim in
    legitimate English card copy — "structural baseline", "the same baseline
    remains available next time" — so any English card that reaches the
    skip-commitment or snapshot copy trips a *false* A-12.  The engine copy is
    correct; the blocklist term is simply too broad for English prose, and the
    existing #160 suite never exercised check_card against an English card, so
    the collision stayed latent until this harness ran one.  We recognise this
    one finding precisely (evidence is exactly "baseline") so a genuine
    internal-key leak — whose evidence would be e.g. ``max_pos_pct`` — still
    fails.  Reported to maintainers; not silently patched into check_card.
    """
    return finding.assertion == "A-12" and finding.evidence.strip() == "baseline"


def check_result(result):
    """Run the #160 card/state checkers over every produced artifact.

    ``check_card`` encodes the *private* card's ironclad rules (B-9 even
    requires a concrete number); the public card deliberately strips every
    amount/date, so it is checked for leakage separately (``public_leaks``),
    not by re-running the private invariants against it.

    Returns ``(failures, known_limitations)``: real failures the gate must
    treat as red, and the documented check_card/English-prose collisions kept
    visible but non-fatal.
    """
    failures, known = [], []
    last_root = None
    for week in result.weeks:
        for finding in check_card(week.private_card):
            if finding.passed:
                continue
            context = f"{result.persona} {week.label} private card"
            if _is_known_checker_limitation(finding):
                known.append((context, finding))
            else:
                failures.append((context, finding))
        last_root = str(pathlib.Path(week.session_path).parent.parent)
    if last_root:
        for finding in check_state(last_root):
            if not finding.passed:
                failures.append((f"{result.persona} state", finding))
    return failures, known


def public_leaks(week, private_terms):
    """Any private ticker/amount/date that leaked into the public card.

    The public card is engine-rendered from fixed copy and severity bands and
    must never carry user-session specifics.  ``private_terms`` are the tokens
    the caller pulled from the private window (tickers, amounts, dates).
    """
    return [term for term in private_terms if term and term in week.public_card]


# ── Transcript (design-review deliverable) ──────────────────────────────────

def _fence(text):
    """Wrap card text in a fenced block, escaping any collision defensively."""
    body = text.rstrip("\n").replace("```", "`​``")
    return "```text\n" + body + "\n```"


def render_transcript(result):
    """A deterministic Markdown transcript of the whole experience.

    No absolute paths or timestamps: every line is derived from the frozen
    inputs and the engine-rendered card text, so identical inputs produce
    byte-identical output.
    """
    lines = []
    lines.append(f"# Experience transcript — {result.persona}")
    lines.append("")
    lines.append(f"- Persona: `{result.persona}`")
    lines.append(f"- Language: `{result.language}`")
    lines.append(f"- Weeks: {len(result.weeks)}")
    lines.append("")
    lines.append("> Deterministic AI-simulated-user run. Questions and card text are "
                 "verbatim engine output; answers are the persona policy's fixed choices.")
    lines.append("")

    for week in result.weeks:
        lines.append(f"## Week {week.index + 1} — {week.label}")
        lines.append("")
        lines.append(f"*session `{week.session_id}` · route `{week.route}`*")
        lines.append("")
        if week.returning and week.prior_commitment_rule:
            lines.append(f"_Returning review. Last week's committed rule: "
                         f"“{week.prior_commitment_rule}”._")
            lines.append("")

        answers_by_qid = {a.get("question_id"): a for a in week.answers.get("answers") or []}
        if week.questions:
            lines.append("### Questions asked (verbatim) and persona answers")
            lines.append("")
            for n, q in enumerate(week.questions, 1):
                lines.append(f"{n}. **[{q['kind']}]** {q['question']}")
                answer = answers_by_qid.get(q["id"], {})
                # find the option list from the plan is not stored; describe from answer only
                choice = answer.get("choice")
                extra = []
                if answer.get("evidence_delta"):
                    ev = answer["evidence_delta"]
                    if result.language.startswith("en"):
                        extra.append(f'claim "{ev.get("claim")}" (source: {ev.get("source")})')
                    else:
                        extra.append(f"claim「{ev.get('claim')}」(source：{ev.get('source')})")
                if answer.get("note"):
                    extra.append(("note: " if result.language.startswith("en") else "note：")
                                 + str(answer["note"]))
                tail = (" — " + "; ".join(extra)) if extra else ""
                lines.append(f"   - _answer_: `{choice}`{tail}")
            lines.append("")
        else:
            lines.append("_No questions this week (nothing perishable or unanswered returned)._")
            lines.append("")

        for missing in week.missing_thesis_positions:
            update = next((u for u in week.answers.get("thesis_updates") or []
                           if u.get("cycle_id") == missing.get("cycle_id")), {})
            if update:
                lines.append(f"- _new thesis for {update.get('ticker')}_: "
                             f"“{update.get('why')}”")
        if week.missing_thesis_positions:
            lines.append("")

        commit = week.commitment_choice
        if commit == "skip":
            lines.append("**Commitment:** skipped — no rule chosen this week.")
        else:
            lines.append(f"**Commitment:** chose `{commit}`.")
        lines.append("")

        lines.append("### Final private card")
        lines.append("")
        lines.append(_fence(week.private_card))
        lines.append("")
        lines.append("### Shareable public card")
        lines.append("")
        lines.append(_fence(week.public_card))
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Drive a persona through the multi-week experience harness (offline).")
    parser.add_argument("persona", choices=sorted(POLICIES),
                        help="which simulated user to run")
    parser.add_argument("--transcript", metavar="PATH",
                        help="write a readable Markdown transcript to PATH")
    parser.add_argument("--root", help="isolated state root (default: a fresh temp dir)")
    parser.add_argument("--check", action="store_true",
                        help="also run the #160 card/state checkers and report failures")
    args = parser.parse_args(argv)

    result = run_experience(args.persona, root=args.root)

    if args.transcript:
        text = render_transcript(result)
        out = pathlib.Path(args.transcript)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote transcript: {out}  ({len(result.weeks)} weeks, {len(text)} bytes)")
    else:
        print(render_transcript(result))

    if args.check:
        failures, known = check_result(result)
        for context, finding in known:
            print(f"CHECK NOTE (known check_card limitation): {context}: {finding}",
                  file=sys.stderr)
        for context, finding in failures:
            print(f"CHECK FAIL: {context}: {finding}", file=sys.stderr)
        if failures:
            return 1
        print("checkers: all card/state invariants passed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
