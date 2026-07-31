#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The rationale integration slice (#403): direct entry → next review. No pytest.

What this file settles — and the first item is the acceptance condition the
whole slice exists for:

  A. A statement recorded outside a review is quoted back by the next review,
     with the user's own words and the date they said them, instead of the
     review asking the same fact from zero. Until this passes, a row in
     `position_rationales.jsonl` is a writer without a reader (#429) and nothing
     may call it memory.
  B. A reason recorded days ago is not re-asked. The value of #403 is a series
     over quarters, and a question that arrives too often trains the user to
     answer it without thinking.
  C. Answering `same` records a confirmation, `changed` records the new words
     while keeping the old, and `skip` records nothing at all.
  D. A rationale that cannot be recorded does not fail the review — the owner's
     2026-07-31 ruling that a rationale and its neighbours are independent
     outcomes with separate receipts — and is never reported as recorded.
  E. A forked subject is not asked about, because there the reader's order is
     not the record's and the question could quote a statement the user did not
     most recently make.

Division of labour: `tests/test_position_rationale.py` owns the stream, its fold
and the bounded reader. This file owns the two entrances and the plan surface
between them. The light-capture migration is a later slice and is not here.

Run:
  python3 tests/test_rationale_review.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILL = os.path.join(ROOT, "skills", "fomo-kernel")
ENGINE = os.path.join(SKILL, "engine")
CLI = os.path.join(ENGINE, "review.py")
MOCK = os.path.join(SKILL, "mock", "sample_pyramid.csv")

sys.path.insert(0, HERE)
import offline_posture  # noqa: E402
offline_posture.apply()
sys.path.insert(0, ENGINE)
import position_rationale as pr  # noqa: E402
import review as review_engine  # noqa: E402


def _run(*argv, root=None):
    out = subprocess.run([sys.executable, CLI, *argv, "--root", root],
                         capture_output=True, text=True, timeout=300)
    try:
        return out.returncode, json.loads(out.stdout or "{}")
    except ValueError:
        raise AssertionError(f"non-JSON from {argv[0]}: {out.stdout[-400:]}\n{out.stderr[-400:]}")


def _prepared(root):
    """A fresh Review Plan, discarding any pending one first so a second call in
    the same test reflects state recorded in between."""
    pending = os.path.join(root, ".pending")
    if os.path.isdir(pending):
        subprocess.run([sys.executable, "-c",
                        f"import shutil;shutil.rmtree({pending!r})"], check=True)
    code, payload = _run("prepare", MOCK, "--language", "en", root=root)
    assert code == 0, payload
    return payload["review_plan"]


def _say(root, ticker, statement, *, stated_at=None):
    argv = ["record-rationale", "--ticker", ticker, "--statement", statement]
    if stated_at:
        argv += ["--stated-at", stated_at]
    code, payload = _run(*argv, root=root)
    assert code == 0, payload
    return payload


def _refresh_question(plan):
    for row in plan.get("question_queue") or []:
        if row.get("kind") == "rationale_refresh":
            return row
    return None


# ─────────────── A. the acceptance condition ───────────────

def test_a_statement_recorded_outside_a_review_is_quoted_back_by_the_next_one():
    """#403's stop boundary, and the only thing that turns a stored row into
    memory. The user says why they hold something, on their own, with no review
    running. The next review opens by putting it back to them -- their wording,
    their date -- so the question is confirm-or-correct rather than reconstruct
    from memory."""
    with tempfile.TemporaryDirectory() as root:
        _prepared(root)
        said = "會員費續約率還在爬，我當定存放"
        recorded = _say(root, "COST", said, stated_at="2026-01-10")
        assert recorded["status"] == "appended"

        plan = _prepared(root)
        state = (plan["state_snapshot"]["position_rationales"] or [])
        entry = next(row for row in state if row["ticker"] == "COST")
        assert entry["statement"] == said, "verbatim, into the plan"
        assert entry["stated_at"] == "2026-01-10"
        assert entry["days_since"] > 0

        question = _refresh_question(plan)
        assert question is not None, (
            "a reason this old earns a candidate slot; without a question the "
            "statement reaches the plan and still nothing asks about it")
        assert said in question["question"], (
            "the stem quotes the user's own words rather than paraphrasing them")
        assert "2026-01-10" in question["question"], (
            "and dates the quote, so it reads as a record and not a present-tense claim")
        assert question["prior_rationale"]["event_id"] == recorded["event_id"], (
            "the question names the exact event the answer will supersede")
        assert question["ticker"] == "COST"


def test_the_plan_says_so_even_when_nothing_was_ever_recorded():
    """The key is present and empty rather than absent, so "you have told me
    nothing about why you hold these" is a claim the plan makes rather than a
    silence a reader has to interpret."""
    with tempfile.TemporaryDirectory() as root:
        plan = _prepared(root)
        state = plan["state_snapshot"]
        assert state["position_rationales"] == []
        summary = state["position_rationales_summary"]
        assert summary["positions_with_a_recorded_reason"] == 0
        assert summary["positions_without_one"] >= 1
        assert _refresh_question(plan) is None, "and nothing is asked about nothing"


# ─────────────── B. not asking too often ───────────────

def test_a_reason_given_days_ago_is_not_re_asked():
    with tempfile.TemporaryDirectory() as root:
        _prepared(root)
        _say(root, "COST", "just told you this")     # stated_at defaults to today
        plan = _prepared(root)
        entry = next(row for row in plan["state_snapshot"]["position_rationales"]
                     if row["ticker"] == "COST")
        assert entry["days_since"] == 0
        assert _refresh_question(plan) is None, (
            "re-asking now would learn nothing and teach the user to answer "
            "without thinking, which is worse than not asking")


def test_at_most_one_refresh_question_and_the_stalest_wins():
    with tempfile.TemporaryDirectory() as root:
        _prepared(root)
        _say(root, "COST", "the older reason", stated_at="2026-01-10")
        _say(root, "UNH", "the less old reason", stated_at="2026-03-20")
        plan = _prepared(root)
        asked = [row for row in plan["question_queue"] or []
                 if row["kind"] == "rationale_refresh"]
        assert len(asked) <= 1, "one slot, competing with everything else this week"
        if asked:
            assert asked[0]["ticker"] == "COST", "the stalest reason is the one worth asking about"
        ordered = [row["ticker"] for row in plan["state_snapshot"]["position_rationales"]]
        assert ordered == ["COST", "UNH"], "and the surface itself is oldest-first"


# ─────────────── C. what an answer does ───────────────

def _answered_plan(root, ticker, choice, note=None):
    """Drive the review-lane consumer directly against a real prepared plan.

    The consumer is the unit under test here; a full prepare/preview/finalize
    walk is `tests/test_review_v2.py`'s job and would not exercise anything more
    of this path.
    """
    plan = _prepared(root)
    question = _refresh_question(plan)
    assert question is not None and question["ticker"] == ticker, question
    answer = {"question_id": question["id"], "choice": choice}
    if note is not None:
        answer["note"] = note
    return review_engine._record_rationale_answers(
        plan, {"answers": [answer]}, {question["id"]: answer})


def test_answering_same_records_a_confirmation_without_restating_the_words():
    with tempfile.TemporaryDirectory() as root:
        _prepared(root)
        first = _say(root, "COST", "the reason I gave in January", stated_at="2026-01-10")
        written = _answered_plan(root, "COST", "same")
        assert len(written) == 1 and written[0]["status"] == "appended"

        view = pr.query(root, written[0]["cycle_id"])
        assert view["change"] == "no_change"
        assert view["latest"]["act"] == "confirmation"
        assert "user_statement" not in view["latest"], (
            "a confirmation says the wording still holds; copying it forward "
            "would restate it on a day the user did not say it")
        assert view["effective"]["event_id"] == first["event_id"]
        assert view["latest"]["capture_source"] == "review"


def test_answering_changed_records_the_new_words_and_keeps_the_old():
    with tempfile.TemporaryDirectory() as root:
        _prepared(root)
        _say(root, "COST", "the reason I gave in January", stated_at="2026-01-10")
        written = _answered_plan(root, "COST", "changed", note="改成看它電商營收佔比")
        assert written[0]["status"] == "appended"

        view = pr.query(root, written[0]["cycle_id"])
        assert view["total_count"] == 2, "append, never rewrite"
        assert view["change"] == "changed"
        assert view["effective"]["user_statement"] == "改成看它電商營收佔比"
        assert view["items"][0]["user_statement"] == "the reason I gave in January", (
            "the superseded wording stays exactly as they wrote it")


def test_answering_changed_without_words_is_refused():
    with tempfile.TemporaryDirectory() as root:
        _prepared(root)
        _say(root, "COST", "the reason I gave in January", stated_at="2026-01-10")
        try:
            _answered_plan(root, "COST", "changed", note="   ")
        except review_engine.ReviewError as exc:
            assert "own words" in str(exc)
        else:
            raise AssertionError("recording that it changed without saying to what stores nothing")


def test_answering_skip_records_nothing():
    with tempfile.TemporaryDirectory() as root:
        _prepared(root)
        first = _say(root, "COST", "the reason I gave in January", stated_at="2026-01-10")
        assert _answered_plan(root, "COST", "skip") == []
        view = pr.query(root, first["cycle_id"])
        assert view["total_count"] == 1, (
            "the honest shape of a question posed and not answered: nothing "
            "pretends the user weighed in")


def test_a_choice_the_question_did_not_offer_is_refused():
    with tempfile.TemporaryDirectory() as root:
        _prepared(root)
        _say(root, "COST", "the reason I gave in January", stated_at="2026-01-10")
        try:
            _answered_plan(root, "COST", "invented")
        except review_engine.ReviewError as exc:
            assert "not one of the choices" in str(exc)
        else:
            raise AssertionError("an unoffered choice must fail closed")


def test_the_same_session_answering_twice_is_a_no_op():
    """Finalize is idempotent and a documented-safe retry, so the review lane
    must not append a second event for the same session and subject."""
    with tempfile.TemporaryDirectory() as root:
        _prepared(root)
        _say(root, "COST", "the reason I gave in January", stated_at="2026-01-10")
        plan = _prepared(root)
        question = _refresh_question(plan)
        answer = {"question_id": question["id"], "choice": "same"}
        amap = {question["id"]: answer}
        first = review_engine._record_rationale_answers(plan, {"answers": [answer]}, amap)
        again = review_engine._record_rationale_answers(plan, {"answers": [answer]}, amap)
        assert first[0]["status"] == "appended" and again[0]["status"] == "no-op"
        assert again[0]["event_id"] == first[0]["event_id"]
        assert pr.query(root, first[0]["cycle_id"])["total_count"] == 2


# ─────────────── D/E. what fails, and how loudly ───────────────

def test_a_rationale_that_cannot_be_recorded_does_not_fail_the_review():
    """The mechanism `tests/test_split_basis.py`'s `_ROUTES_NOT_DRIVEN` entry for
    `finalize` names. A card the user has already been shown is not discarded
    because a statement could not be attached to a position -- the owner's
    2026-07-31 ruling on independent outcomes with separate receipts. The
    converse matters just as much: a refused rationale is never reported as
    recorded."""
    with tempfile.TemporaryDirectory() as root:
        _prepared(root)
        _say(root, "COST", "the reason I gave in January", stated_at="2026-01-10")
        plan = _prepared(root)
        question = _refresh_question(plan)
        # The head moved after the question was asked: the user answered about a
        # statement that is no longer current, and guessing which they meant is
        # the one thing this stream must never do.
        _say(root, "COST", "something I said in between", stated_at="2026-07-01")
        answer = {"question_id": question["id"], "choice": "same"}
        try:
            review_engine._record_rationale_answers(
                plan, {"answers": [answer]}, {question["id"]: answer})
        except pr.PositionRationaleError as exc:
            assert "expected predecessor" in str(exc)
        else:
            raise AssertionError("an answer against a moved head must fail closed")
        # And cmd_finalize catches exactly this pair, so the card still commits
        # and the failure is surfaced rather than swallowed.
        import inspect
        source = inspect.getsource(review_engine.cmd_finalize)
        assert "_record_rationale_answers" in source
        assert "position_rationale.PositionRationaleError" in source, (
            "the refusal above must be caught at finalize, or a rationale that "
            "cannot be attached takes the user's whole review down with it")
        assert '"rationale_error": rationale_error' in source, (
            "and it must be reported: a refused rationale is never silently absent")


def test_a_forked_subject_is_not_asked_about():
    with tempfile.TemporaryDirectory() as root:
        _prepared(root)
        first = _say(root, "COST", "the original reason", stated_at="2026-01-10")
        branch = pr.build_event(
            subject={"cycle_id": first["cycle_id"], "ticker": "COST",
                     "market": "US", "currency": "USD"},
            act="statement", user_statement="a second child of the same parent",
            stated_at="2026-02-01", capture_source="direct", state_version="sv-x",
            supersedes=first["event_id"])
        _say(root, "COST", "the first child", stated_at="2026-02-01")
        with open(pr._rationale_path(root), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(branch, ensure_ascii=False, sort_keys=True) + "\n")

        plan = _prepared(root)
        entry = next(row for row in plan["state_snapshot"]["position_rationales"]
                     if row["ticker"] == "COST")
        assert entry["forked"], "the plan says the subject is forked"
        assert _refresh_question(plan) is None, (
            "and asks nothing about it: the reader's order is not the record's "
            "there, so the stem could quote a statement the user did not most "
            "recently make")


def _main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _main() else 0)
