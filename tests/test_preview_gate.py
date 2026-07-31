#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`finalize` may not commit a card the user was never shown (#628).

`preview` renders the card; `finalize` commits it. Until this gate, nothing
linked the two, and the measurement on `main` was unambiguous in both
directions: a `prepare` -> `finalize` run that skipped `preview` entirely
committed successfully, while the same run with answers `preview` rejects was
rejected by `finalize` with the identical error, unprompted. So `finalize`
already carried its own complete validation, and what `preview` uniquely
provided -- the user seeing the real card before it is committed -- was
guaranteed by instruction alone. #357 recorded five recurrences of an agent
skipping an instructed step in this exact lifecycle, including one where the
agent recorded its skip correctly and the mechanical receipt gate still passed.

This suite owns the gate. Every test drives the real `engine/review.py` CLI --
never an imported engine function -- because a gate proven by calling its own
helper stays green when the call site is severed, which is the escape this
repository has shipped repeatedly.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "skills" / "fomo-kernel" / "engine"
REVIEW = ENGINE_DIR / "review.py"
MOCK = ROOT / "skills" / "fomo-kernel" / "mock" / "sample_momentum.csv"

# The market must not be an input to these assertions (#620). Declared in
# tests/offline_posture.py so a direct `python3 tests/<this file>` run and a
# `run_all.py` run reach the same answer; TR_TEST_NETWORK=1 still opts in.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_posture  # noqa: E402
offline_posture.apply()
sys.path.insert(0, str(ENGINE_DIR))
import session as session_engine  # noqa: E402


def _run(*args):
    return subprocess.run([sys.executable, str(REVIEW), *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)


def _error(proc):
    return json.loads(proc.stdout).get("error", "")


def _choice(question):
    """The cheapest valid answer to one queued question, by kind.

    The two routes this suite drives queue different kinds -- `initial_thesis`
    on the CSV first review, `headline_motive` on a test drive -- so the answer
    cannot be one literal. It is an explicit map rather than a read of the
    plan's own `requirements_by_choice`, because that contract describes a
    choice's own follow-up fields and not the cross-field rules layered on top
    of it: `planned_entry` advertises no requirements and still needs a
    captured thesis of maturity draft or testable. This suite owns the preview
    precondition; the question contracts belong to the suites that own them.
    """
    return {
        "initial_thesis": "no_clear_thesis",
        "rule_breach": "keep_tracking",
        "revisit": "skip",
        "due_revisit": "skip",
        "condition_crossing": "skip",
        "condition_basis": "skip",
    }.get(question["kind"], "skip" if question["kind"] == "rationale_refresh"
                                     else "deliberate_plan")


def _thesis_updates(plan, maturity="inferred"):
    return [{"ticker": row["ticker"], "cycle_id": row["cycle_id"],
             "why": "A portfolio role that remains inferred for now", "horizon": None,
             "exit_trigger": "A later review contradicts the inferred role",
             "target_size": "bounded", "driver": "opening view", "maturity": maturity}
            for row in plan.get("missing_thesis_positions") or []]


def _narrative_for(plan, headline="A lower price is not automatically a stronger thesis"):
    return {
        "headline": headline,
        "mirror": "The action is deliberate only when its reason survives the next review.",
        "counterfactual": "Without a new fact, the action would have been cost-basis repair.",
        "rule_rationale": "This rule turns conviction into something falsifiable.",
        "honesty": {key: "This limitation is stated rather than filled in."
                    for key in plan["card_plan"]["required_honesty_keys"]},
    }


class Session:
    """One prepared review on a throwaway root, with its own answers/narrative.

    `--root` is always explicit, so the account's own coach root is never the
    one under test.
    """

    def __init__(self, tmp, *, test_drive=False):
        self.tmp = pathlib.Path(tmp)
        # This suite runs offline, so no close is retrievable. #623 refuses a
        # card that reports unretrievable prices when recovery was never
        # attempted, which would block every preview here before this gate is
        # ever reached. Declaring the dead end is the sanctioned clearance and
        # keeps the subject of these tests the preview receipt, not the price.
        args = ["prepare", MOCK, "--root", self.tmp / "coach-root", "--language", "en",
                "--prices-unavailable", "offline test fixture: no provider is reachable"]
        if test_drive:
            args.append("--test-drive")
        prepared = _run(*args)
        assert prepared.returncode == 0, prepared.stdout + prepared.stderr
        emitted = json.loads(prepared.stdout)["review_plan"]
        self.session_id = emitted["session_id"]
        # A test drive relocates its own state; every later command must be
        # pointed at the root `prepare` reports, not at the one it was given.
        self.root = pathlib.Path(emitted["state_root"] if test_drive
                                 else self.tmp / "coach-root")
        self.plan = session_engine.load_pending(str(self.root), self.session_id)["plan"]
        self.narrative_path = self.tmp / "narrative.json"
        self.narrative_path.write_text(json.dumps(_narrative_for(self.plan)), encoding="utf-8")

    def answers(self, name, *, commitment=None, observation="Agent interpretation stays separate",
                maturity="inferred"):
        payload = {
            "session_id": self.session_id,
            "answers": [{"question_id": question["id"], "choice": _choice(question)}
                        for question in self.plan["question_queue"]],
            "thesis_updates": _thesis_updates(self.plan, maturity),
            "observations": [observation],
        }
        if commitment is not None:
            payload["commitment"] = {"choice": commitment}
        path = self.tmp / f"answers-{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def preview(self, answers_path):
        return _run("preview", "--root", self.root, "--session-id", self.session_id,
                    "--answers", answers_path, "--narrative", self.narrative_path)

    def finalize(self, answers_path):
        return _run("finalize", "--root", self.root, "--session-id", self.session_id,
                    "--answers", answers_path, "--narrative", self.narrative_path)

    def committed(self):
        return (self.root / "sessions" / self.session_id).is_dir()


# ───────────────────────── A. the gate itself ─────────────────────────


def test_finalize_refuses_a_session_whose_preview_never_ran():
    """The user scene #628 exists for: a committed card carrying a standing
    rule the user never saw, because the agent went from answers to finalize."""
    with tempfile.TemporaryDirectory() as tmp:
        review = Session(tmp)
        answers = review.answers("final", commitment="candidate_0")
        refused = review.finalize(answers)
        assert refused.returncode == 2, refused.stdout + refused.stderr
        assert not review.committed(), \
            "a refused finalize must leave no committed session behind"


def test_the_refusal_names_running_preview_as_the_fix_and_says_nothing_was_lost():
    """A refusal that reads like a lost session is a defect: `AGENTS.md` is
    explicit that an existing canonical session is not data loss."""
    with tempfile.TemporaryDirectory() as tmp:
        review = Session(tmp)
        message = _error(review.finalize(review.answers("final", commitment="candidate_0")))
        assert "preview" in message, "the fix must be named, not merely implied"
        assert "Nothing was lost" in message and "still pending" in message, \
            "the refusal must not read like a session that has to be rebuilt"
        # The pending session really is still there and still usable.
        assert (review.root / ".pending" / review.session_id).is_dir()


def test_a_preview_makes_the_same_finalize_commit():
    """The counterweight: without it, a gate that refuses everything passes."""
    with tempfile.TemporaryDirectory() as tmp:
        review = Session(tmp)
        assert review.preview(review.answers("preview")).returncode == 0
        final = review.finalize(review.answers("final", commitment="candidate_0"))
        assert final.returncode == 0, final.stdout + final.stderr
        assert review.committed()


def test_a_preview_of_different_answers_does_not_satisfy_the_gate():
    """Otherwise the gate certifies a card the user did not see. The preview
    here really ran, and really rendered a card -- of a different review."""
    with tempfile.TemporaryDirectory() as tmp:
        review = Session(tmp)
        previewed = review.answers("preview", observation="A different observation entirely")
        assert review.preview(previewed).returncode == 0
        refused = review.finalize(review.answers("final", commitment="candidate_0"))
        assert refused.returncode == 2, refused.stdout + refused.stderr
        assert "different card" in _error(refused)
        assert not review.committed()


def test_a_preview_of_a_different_narrative_does_not_satisfy_the_gate():
    """The narrative is the card's prose, so it is part of what was shown."""
    with tempfile.TemporaryDirectory() as tmp:
        review = Session(tmp)
        answers = review.answers("both", commitment="candidate_0")
        assert review.preview(answers).returncode == 0
        narrative = json.loads(review.narrative_path.read_text(encoding="utf-8"))
        narrative["headline"] = "An entirely different headline for the same review"
        review.narrative_path.write_text(json.dumps(narrative), encoding="utf-8")
        refused = review.finalize(answers)
        assert refused.returncode == 2, refused.stdout + refused.stderr
        assert not review.committed()


def test_the_commitment_is_the_one_field_that_may_move_after_the_preview():
    """`SKILL.md`'s lifecycle shows the card, *then* asks for the rule, so a
    receipt keyed on the commitment would make its own contract unsatisfiable.

    Both directions are asserted together, because that pairing is the whole
    justification: previewing without a commitment and committing one is the
    documented flow, and previewing one choice and committing another is the
    same flow after the user changed their mind while looking at the card.
    """
    for previewed, committed in (({}, {"commitment": "candidate_0"}),
                                 ({"commitment": "candidate_1"}, {"commitment": "candidate_0"}),
                                 ({}, {"commitment": "skip"})):
        with tempfile.TemporaryDirectory() as tmp:
            review = Session(tmp)
            assert review.preview(review.answers("preview", **previewed)).returncode == 0
            final = review.finalize(review.answers("final", **committed))
            assert final.returncode == 0, \
                f"preview {previewed} -> finalize {committed}: {final.stdout}{final.stderr}"
            assert review.committed()


# ────────────────── B. what the gate must not have broken ──────────────────


def test_finalize_still_rejects_bad_answers_with_its_own_error():
    """#628 scope item 3. The gate is a precondition on *committing*, so it
    runs after the draft finalize already validates -- a session that skipped
    preview and carries invalid answers must still be told what is invalid,
    which is the second direction of the issue's own measurement.
    """
    with tempfile.TemporaryDirectory() as tmp:
        review = Session(tmp)
        bad = review.answers("bad", commitment="candidate_0", maturity="not-a-real-maturity")
        refused = review.finalize(bad)
        assert refused.returncode == 2
        assert "maturity" in _error(refused), \
            "finalize must stay a complete independent validator, not defer to preview"
        assert "preview" not in _error(refused)


def test_an_identical_finalize_retry_after_the_commit_is_still_a_no_op():
    """The documented-safe retry runs when the pending directory -- and with it
    the receipt -- is already gone. Refusing it would turn this gate into the
    data loss it is meant to prevent."""
    with tempfile.TemporaryDirectory() as tmp:
        review = Session(tmp)
        assert review.preview(review.answers("preview")).returncode == 0
        answers = review.answers("final", commitment="candidate_0")
        assert review.finalize(answers).returncode == 0
        assert not (review.root / ".pending" / review.session_id).exists()
        retry = review.finalize(answers)
        assert retry.returncode == 0, retry.stdout + retry.stderr
        assert json.loads(retry.stdout)["status"] == "no-op"


def test_a_test_drive_session_walks_the_same_two_steps():
    """`--test-drive` is a full review in an isolated root, so it has a preview
    stage and is gated like one -- and must still complete end to end."""
    with tempfile.TemporaryDirectory() as tmp:
        review = Session(tmp, test_drive=True)
        assert review.plan["route"] == "test_drive"
        skipped = review.finalize(review.answers("skip", commitment="candidate_0"))
        assert skipped.returncode == 2, "a test drive is not exempt from the gate"
        assert "preview" in _error(skipped)
        assert review.preview(review.answers("preview")).returncode == 0
        final = review.finalize(review.answers("final", commitment="candidate_0"))
        assert final.returncode == 0, final.stdout + final.stderr
        assert review.committed()


def test_the_routes_with_no_preview_stage_are_untouched():
    """#628 scope item 4, confirmed by running each rather than by reading the
    call graph. None of these commits a review card, so none may acquire a
    preview precondition -- a gate that leaked into `consider` or `refresh`
    would break a route that has no preview to run.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        root = tmp / "coach-root"
        snapshot = tmp / "positions.json"
        snapshot.write_text(json.dumps({
            "as_of": "2026-07-16",
            "positions": [{"ticker": "NVDA", "shares": 10, "avg_cost": 100,
                           "market": "US", "currency": "USD"}],
            "fx": {"USD": 1}}), encoding="utf-8")
        premise = json.dumps({"ticker": "NVDA", "side": "buy", "price": 120.0, "qty": 1})
        checked = {
            "consider": _run("consider", MOCK, "--root", root, "--premise", premise),
            "refresh": _run("refresh", "--root", root, "--snapshot-json", snapshot),
            "capture": _run("capture", "--root", root, "--session-id", "absent",
                            "--entries", snapshot),
            "resume": _run("resume", "--root", root),
            "repair-projections": _run("repair-projections", "--root", root),
            "set-cap": _run("set-cap", "--root", root, "--pct", "0.25"),
        }
        for route, proc in checked.items():
            body = proc.stdout + proc.stderr
            assert "preview receipt" not in body and "finalize refuses" not in body, \
                f"{route} acquired a preview precondition it has no preview stage for: {body}"
        # The two that legitimately succeed with nothing recorded yet still do.
        assert checked["consider"].returncode == 0, checked["consider"].stdout
        assert checked["resume"].returncode == 0, checked["resume"].stdout


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = []
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except AssertionError as exc:
            failures.append(test.__name__)
            print(f"FAIL  {test.__name__} {exc}")
    print(f"\n{len(tests) - len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
