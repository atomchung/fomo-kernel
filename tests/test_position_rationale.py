#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The canonical position-rationale stream (#403 Front B). Offline, no pytest.

What this file settles:

  A. A user's own words round-trip byte for byte, and a changed reason is a new
     event rather than a rewrite of the old one.
  B. A retry is a no-op and a revert is not. These are the two halves the frozen
     contract could not separate, and getting one wrong loses a statement the
     user made while the writer reports success.
  C. A confirmation says the prior wording still holds without restating it, and
     two confirmations on different days are two events.
  D. Nothing is joined across subjects, and a corrupt line costs one row rather
     than the record — and is counted rather than swallowed.
  E. The bounded reader (#450) reports its truncation honestly, follows
     engine-proven cycle relinks, derives the frozen `initial | changed |
     no_change` vocabulary, and computes the span, the totals and the effective
     statement from the untruncated chain.
  F. Three ambiguities fail closed rather than resolving to a winner the user
     never nominated: a forked subject, a moved expected predecessor, and a
     review session returning with different words under an id it already used.

The three mutations this file exists to fail, per docs/development-guide.md §2:

  1. Drop ``supersedes`` from the content address (#403's pre-amendment shape) —
     ``test_a_reason_the_user_returns_to_is_not_swallowed_as_a_retry`` fails,
     because the user's fourth statement hashes onto their second.
  2. Replace head comparison with id-collision idempotency —
     ``test_a_write_that_landed_before_its_receipt_is_not_stored_twice`` fails.
  3. Store ``initial``/``changed`` and read it back instead of deriving position
     from the pointer — ``test_where_an_event_sits_in_its_chain_is_derived`` fails
     once a later event is discovered ahead of it, which Front A's relink does by
     design.

Division of labour: this file owns the stream, the fold and the bounded reader.
The direct CLI entry, the review collector and the light-capture migration are
later slices and are not exercised here — until one lands, a row in this file is
not yet memory, and no test here may claim otherwise.

Run:
  python3 tests/test_position_rationale.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE)
import position_rationale as pr  # noqa: E402
sys.path.insert(0, HERE)
import rationale_fixture  # noqa: E402

ACME = {"cycle_id": "ACME#2026-06-30#1", "ticker": "ACME",
        "market": "US", "currency": "USD"}
WIDGET = {"cycle_id": "WIDGET#2026-06-30#1", "ticker": "WIDGET",
          "market": "US", "currency": "USD"}
# A rebought ACME: same ticker, different cycle. Nothing may cross this line.
ACME_2 = {"cycle_id": "ACME#2026-07-20#2", "ticker": "ACME",
          "market": "US", "currency": "USD"}


def _say(root, subject, text, *, stated_at="2026-07-01", source="direct",
         origin_id=None, state_version="sv-1"):
    return pr.append(root, subject=subject, act="statement", user_statement=text,
                     stated_at=stated_at, capture_source=source, origin_id=origin_id,
                     state_version=state_version)


def _confirm(root, subject, *, stated_at="2026-07-02", state_version="sv-1"):
    return pr.append(root, subject=subject, act="confirmation", stated_at=stated_at,
                     capture_source="direct", state_version=state_version)


def _rows(root):
    with open(pr._rationale_path(root), encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# ─────────────────── A. the words, and what supersedes what ───────────────────

def test_the_users_own_words_round_trip_byte_for_byte():
    """Not stripped, not tidied, not normalized. A stream that rephrases the
    user is a stream of the product's words, and the whole reason to store a
    self-report verbatim is that our defaults did not anticipate it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        text = "  管理層換人之後我才進場的，想再看兩季  "
        out = _say(root, ACME, text)
        assert out["status"] == "appended"
        assert _rows(root)[0]["user_statement"] == text, "including the spaces"
        assert pr.query(root, ACME["cycle_id"])["effective"]["user_statement"] == text


def test_a_changed_reason_is_a_new_event_that_names_the_one_it_follows():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        first = _say(root, ACME, "for the dividend")
        second = _say(root, ACME, "for the buyback")
        rows = _rows(root)
        assert len(rows) == 2, "a changed reason appends; it never rewrites"
        assert rows[0]["user_statement"] == "for the dividend", "the old wording stays"
        assert rows[1]["supersedes"] == first["event_id"]
        assert rows[0]["supersedes"] is None, (
            "a first event names no predecessor, and that -- not a stored label -- "
            "is what says it is first")
        assert pr.query(root, ACME["cycle_id"])["effective"]["event_id"] == \
            second["event_id"]


def test_where_an_event_sits_in_its_chain_is_derived():
    """Mutation 3's target. Front A's `build_snapshot_cycle_relinks` re-parents a
    subject retroactively, so an event honestly written "first" can end up behind
    earlier ones on the merged subject. Position must therefore be a property of
    the chain read now, never a label frozen at write time.

    Simulated here the way a relink produces it: a row for the same subject that
    the file did not contain when the later ones were written.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        _say(root, ACME, "for the dividend")
        _say(root, ACME, "for the buyback")
        chain = pr.chain_for(_rows(root), ACME["cycle_id"])
        assert [row["supersedes"] is None for row in chain] == [True, False]

        discovered = pr.build_event(
            subject=ACME, act="statement", user_statement="the original reason, found later",
            stated_at="2026-05-01", capture_source="light_capture", state_version="sv-0")
        with open(pr._rationale_path(root), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(discovered, ensure_ascii=False, sort_keys=True) + "\n")

        chain = pr.chain_for(_rows(root), ACME["cycle_id"])
        assert len(chain) == 3
        roots = [row for row in chain if row["supersedes"] is None]
        assert len(roots) == 2, (
            "two chain roots read honestly as two origins, one discovered later; "
            "two rows each storing 'this was the first' would read as a contradiction")
        assert pr.query(root, ACME["cycle_id"])["total_count"] == 3


# ─────────────────── B. retry versus revert, the load-bearing pair ───────────────────

def test_a_reason_the_user_returns_to_is_not_swallowed_as_a_retry():
    """Mutation 1's target, and the defect behind the owner's accepted
    predecessor-linked identity. The user says A, then B, then A again, then B
    again -- one
    sitting, same day, same book. Without the predecessor in the content address
    the fourth statement hashes identically to the second and is dropped as an
    "exact retry", while the writer returns a success receipt naming the older
    event. The record would then contradict the user and report that it agreed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        a1 = _say(root, ACME, "for the dividend")
        b1 = _say(root, ACME, "for the buyback")
        a2 = _say(root, ACME, "for the dividend")
        b2 = _say(root, ACME, "for the buyback")
        assert [out["status"] for out in (a1, b1, a2, b2)] == ["appended"] * 4
        assert len({out["event_id"] for out in (a1, b1, a2, b2)}) == 4, (
            "four acts, four events; a returning reason is a real act and the "
            "second B is not the first one happening again")
        assert len(_rows(root)) == 4
        assert pr.query(root, ACME["cycle_id"])["effective"]["user_statement"] == \
            "for the buyback", "and what is in force is what they last said"


def test_a_write_that_landed_before_its_receipt_is_not_stored_twice():
    """Mutation 2's target, and the hole the predecessor pointer opens on its own.

    An exact retry must be a no-op. Id-collision cannot deliver that once the
    pointer is in the address: the retry re-reads a head that is now its own row,
    so it hashes to something new and the same words land twice. Idempotency is
    therefore head comparison, which is what `review._append_evaluation_row`
    already does for the same reason.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        first = _say(root, ACME, "for the dividend")
        retry = _say(root, ACME, "for the dividend")
        assert retry["status"] == "no-op" and retry["appended"] == 0
        assert retry["event_id"] == first["event_id"], (
            "the retry is told which event already records this act")
        assert len(_rows(root)) == 1


def test_a_review_that_is_finalized_twice_does_not_resurrect_its_statement():
    """A re-finalized session must not overwrite a newer direct statement.

    Head comparison alone would not catch this -- the head has moved on -- so the
    review lane is idempotent on its own session and subject, the same key
    `session._append_session_rows` uses.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        _say(root, ACME, "collected during the review", source="review",
             origin_id="session-abc")
        _say(root, ACME, "and then I changed my mind")
        replay = _say(root, ACME, "collected during the review", source="review",
                      origin_id="session-abc")
        assert replay["status"] == "no-op"
        assert len(_rows(root)) == 2
        assert pr.query(root, ACME["cycle_id"])["effective"]["user_statement"] == \
            "and then I changed my mind", "the newer direct statement stays in force"


def test_a_review_session_that_comes_back_with_different_words_fails_closed():
    """The session key makes a replay a no-op. It must not make a *correction* a
    no-op: a user who resumes a review and rewords their reason has said
    something new, and keeping the older words while reporting success is the
    exact loss this stream exists to stop. `session._append_session_rows` refuses
    the same shape rather than choosing which version to keep."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        first = _say(root, ACME, "because of the dividend", source="review",
                     origin_id="session-abc")
        try:
            _say(root, ACME, "because of the dividend AND the buyback",
                 source="review", origin_id="session-abc")
        except pr.PositionRationaleError as exc:
            assert "already recorded a different rationale" in str(exc)
            assert first["event_id"] in str(exc)
        else:
            raise AssertionError("a changed statement under a used session id must fail closed")
        assert len(_rows(root)) == 1, "and nothing is written on the way to refusing"


def test_a_forked_subject_is_reported_and_refused_rather_than_healed():
    """Two events superseding one predecessor cannot both be the current reason.
    The accepted disposition requires corruption be reported rather than resolved
    by file order or timestamp -- so the reader says `forked` and the writer
    refuses, instead of appending onto a head that buries one branch for good."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        root_event = _say(root, ACME, "the original reason")
        branch = pr.build_event(subject=ACME, act="statement",
                                user_statement="a second child of the same parent",
                                stated_at="2026-07-02", capture_source="direct",
                                state_version="sv-1", supersedes=root_event["event_id"])
        _say(root, ACME, "the first child", stated_at="2026-07-02")
        with open(pr._rationale_path(root), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(branch, ensure_ascii=False, sort_keys=True) + "\n")

        assert pr.query(root, ACME["cycle_id"])["forked"] == [root_event["event_id"]]
        try:
            _say(root, ACME, "a reason recorded on top of the fork")
        except pr.PositionRationaleError as exc:
            assert "ambiguous" in str(exc)
        else:
            raise AssertionError("appending onto a forked subject must fail closed")


def test_an_expected_predecessor_that_moved_fails_closed():
    """A caller that read the head, then acted, must not silently fork the
    subject when something landed in between."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        stale = _say(root, ACME, "the reason I read before acting")
        _say(root, ACME, "something recorded in between", stated_at="2026-07-02")
        try:
            pr.append(root, subject=ACME, act="statement", user_statement="mine",
                      stated_at="2026-07-03", capture_source="direct",
                      state_version="sv-1", expected_predecessor=stale["event_id"])
        except pr.PositionRationaleError as exc:
            assert "expected predecessor" in str(exc)
        else:
            raise AssertionError("a moved head must fail closed, not fork")
        assert len(_rows(root)) == 2


def test_the_frozen_change_vocabulary_is_derived_by_this_reader():
    """`initial | changed | no_change` is not stored. It must still be computed
    in exactly one place, or every integration surface rolls its own and the two
    disagree -- which is the defect not storing it was meant to end."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        _say(root, ACME, "the first reason")
        assert pr.query(root, ACME["cycle_id"])["change"] == "initial"
        _say(root, ACME, "a different reason", stated_at="2026-07-02")
        assert pr.query(root, ACME["cycle_id"])["change"] == "changed"
        _confirm(root, ACME, stated_at="2026-07-03")
        assert pr.query(root, ACME["cycle_id"])["change"] == "no_change"
        assert pr.query(root, "NOSUCH#2026-01-01#1")["change"] is None


def test_stated_at_defaults_to_the_local_calendar_date():
    """Frozen by the accepted disposition. The chain, not sub-day precision,
    separates two same-day revisions -- so a date is enough, and inventing a
    clock time for a statement the user did not timestamp would be a fact
    nobody stated."""
    import datetime as _dt
    event = pr.build_event(subject=ACME, act="statement", user_statement="a reason",
                           capture_source="direct", state_version="sv-1")
    assert event["stated_at"] == _dt.date.today().isoformat()
    backdated = pr.build_event(subject=ACME, act="statement", user_statement="a reason",
                               stated_at="2026-01-05", capture_source="direct",
                               state_version="sv-1")
    assert backdated["stated_at"] == "2026-01-05", "an explicit date always wins"


# ─────────────────── C. confirmations ───────────────────

def test_a_confirmation_holds_the_prior_wording_without_restating_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        said = _say(root, ACME, "for the dividend")
        out = _confirm(root, ACME)
        rows = _rows(root)
        assert "user_statement" not in rows[1], (
            "copying the wording forward would restate it on a day they did not "
            "say it; the pointer is how the record keeps what is in force")
        assert rows[1]["supersedes"] == said["event_id"]
        query = pr.query(root, ACME["cycle_id"])
        assert query["effective"]["event_id"] == said["event_id"]
        assert query["latest"]["event_id"] == out["event_id"], (
            "what is in force and what happened most recently are different "
            "questions, and a review needs both")


def test_two_confirmations_on_different_days_are_two_events():
    """The frozen contract collided them at the second: two `no_change` rows
    naming the same prior statement were byte-identical. A confirmation's whole
    informational content is that it happened and when, and #403 makes "how long
    since the last statement" an input to whether the user is asked at all."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        _say(root, ACME, "for the dividend")
        first = _confirm(root, ACME, stated_at="2026-07-02")
        again = _confirm(root, ACME, stated_at="2026-07-09")
        assert first["event_id"] != again["event_id"]
        assert len(_rows(root)) == 3
        same_day = _confirm(root, ACME, stated_at="2026-07-09")
        assert same_day["status"] == "no-op", (
            "confirming twice in one day is one confirmation -- absorbed "
            "deliberately by head comparison, not lost to a hash collision")


def test_a_confirmation_with_nothing_to_confirm_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        try:
            _confirm(root, ACME)
        except pr.PositionRationaleError as exc:
            assert "nothing to confirm" in str(exc)
        else:
            raise AssertionError("a confirmation on an empty chain must fail closed")


def test_the_act_decides_whether_words_are_owed_in_both_directions():
    for act, kwargs, fragment in (
            ("statement", {}, "requires the user's own words"),
            ("statement", {"user_statement": "   "}, "requires the user's own words"),
            ("confirmation", {"user_statement": "restated"}, "carries no statement")):
        try:
            pr.build_event(subject=ACME, act=act, stated_at="2026-07-01",
                           capture_source="direct", state_version="sv-1",
                           supersedes="position-rationale-0000", **kwargs)
        except pr.PositionRationaleError as exc:
            assert fragment in str(exc), (act, kwargs, str(exc))
        else:
            raise AssertionError(f"{act} {kwargs} must be refused")


# ─────────────────── D. subjects, and a damaged file ───────────────────

def test_nothing_is_joined_across_subjects():
    """Including across a sale and rebuy of the same ticker, which is the join
    #403 forbids by name: the two cycles are different positions and the reason
    for one is not evidence about the other."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        _say(root, ACME, "the reason for the first ACME")
        _say(root, WIDGET, "an unrelated reason")
        _say(root, ACME_2, "the reason for the one I bought back")
        for subject, expected in ((ACME, "the reason for the first ACME"),
                                  (WIDGET, "an unrelated reason"),
                                  (ACME_2, "the reason for the one I bought back")):
            query = pr.query(root, subject["cycle_id"])
            assert query["total_count"] == 1, subject["cycle_id"]
            assert query["effective"]["user_statement"] == expected
        assert pr.query(root, "NOSUCH#2026-01-01#1")["total_count"] == 0


def test_a_corrupt_line_costs_one_row_and_is_counted():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        _say(root, ACME, "for the dividend")
        _say(root, ACME, "for the buyback")
        with open(pr._rationale_path(root), "a", encoding="utf-8") as handle:
            handle.write("{not json at all\n")
            handle.write(json.dumps({"event_id": "x", "act": "statement"}) + "\n")
        query = pr.query(root, ACME["cycle_id"])
        assert query["total_count"] == 2, "the readable rows survive"
        assert query["unreadable"] == 2, (
            "and the count says so: a silently dropped row would make `total` a "
            "lie, which is worse than saying the record is damaged")


def test_an_append_onto_a_file_with_no_trailing_newline_writes_two_rows():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        _say(root, ACME, "for the dividend")
        path = pr._rationale_path(root)
        with open(path, encoding="utf-8") as handle:
            body = handle.read()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body.rstrip("\n"))          # a prior crash, mid-write
        _say(root, ACME, "for the buyback")
        assert len(_rows(root)) == 2, "the guard writes the missing newline first"


# ─────────────────── E. the bounded reader (#450) ───────────────────

def test_the_bounded_reader_says_what_it_held_back():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        events = [_say(root, ACME, f"reason {n}", stated_at=f"2026-07-{n:02d}")
                  for n in range(1, 13)]
        query = pr.query(root, ACME["cycle_id"])
        assert query["total_count"] == 12
        assert query["shown_count"] == pr.QUERY_CAP == 8
        assert query["beyond_cap"] == 4
        assert query["items"][0]["event_id"] == events[0]["event_id"], "earliest is kept"
        assert query["items"][-1]["event_id"] == events[-1]["event_id"], "and latest"
        assert [row["user_statement"] for row in query["items"][1:-1]] == \
            [f"reason {n}" for n in range(6, 12)], "the rest is the most recent"
        assert query["span"] == {"first": "2026-07-01", "last": "2026-07-12"}, (
            "the span spans the whole record, not the window: the cap keeps a "
            "plan surface readable and must never shrink what it reports about")
        assert query["effective"]["user_statement"] == "reason 12"


def test_a_short_history_is_returned_whole():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        one = _say(root, ACME, "the only reason")
        query = pr.query(root, ACME["cycle_id"])
        assert (query["total_count"], query["shown_count"], query["beyond_cap"]) == (1, 1, 0)
        assert query["items"][0]["event_id"] == one["event_id"]
        assert query["span"] == {"first": "2026-07-01", "last": "2026-07-01"}
        _say(root, ACME, "a second reason", stated_at="2026-07-05")
        query = pr.query(root, ACME["cycle_id"])
        assert (query["total_count"], query["shown_count"], query["beyond_cap"]) == (2, 2, 0)


def test_an_out_of_order_file_still_reads_in_chain_order():
    """A copied or repaired file can present rows in any order. The predecessor
    is what places an event, so the chain -- not the line number -- decides."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        _say(root, ACME, "first")
        _say(root, ACME, "second")
        _say(root, ACME, "third")
        rows = _rows(root)
        with open(pr._rationale_path(root), "w", encoding="utf-8") as handle:
            for row in reversed(rows):
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        query = pr.query(root, ACME["cycle_id"])
        assert [row["user_statement"] for row in query["items"]] == \
            ["first", "second", "third"]
        assert query["effective"]["user_statement"] == "third"


def test_state_version_is_required_because_a_subject_was_resolved_against_a_book():
    try:
        pr.build_event(subject=ACME, act="statement", user_statement="a reason",
                       stated_at="2026-07-01", capture_source="direct",
                       state_version="")
    except pr.PositionRationaleError as exc:
        assert "state_version" in str(exc)
    else:
        raise AssertionError("a rationale with no book version must fail closed")


def test_a_subject_is_never_a_ticker_on_its_own():
    for subject, fragment in (({"ticker": "ACME"}, "missing"),
                              ({**ACME, "sector": "tech"}, "unknown fields")):
        try:
            pr.build_event(subject=subject, act="statement", user_statement="a reason",
                           stated_at="2026-07-01", capture_source="direct",
                           state_version="sv-1")
        except pr.PositionRationaleError as exc:
            assert fragment in str(exc), (subject, str(exc))
        else:
            raise AssertionError(f"{subject} must be refused")


def test_recorded_at_is_not_part_of_the_identity():
    """Otherwise no replay is ever a no-op, and the file grows a row per retry."""
    early = pr.build_event(subject=ACME, act="statement", user_statement="a reason",
                           stated_at="2026-07-01", capture_source="direct",
                           state_version="sv-1", recorded_at="2026-07-01T09:00:00+00:00")
    later = pr.build_event(subject=ACME, act="statement", user_statement="a reason",
                           stated_at="2026-07-01", capture_source="direct",
                           state_version="sv-1", recorded_at="2026-07-04T17:30:00+00:00")
    assert early["event_id"] == later["event_id"]
    assert early["recorded_at"] != later["recorded_at"], "but both are recorded"


def test_a_row_the_reader_would_crash_on_is_counted_not_admitted():
    """A row admitted by `load` and then dereferenced by the fold would kill the
    reader while `unreadable` still said the file was clean -- worse than a
    silent skip, because it inverts the contract instead of weakening it. Every
    field a later reader touches without guarding is checked on the way in."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        good = _say(root, ACME, "a real reason")
        base = _rows(root)[0]
        broken = [
            {**base, "event_id": "no-stated-at", "stated_at": None},
            {**base, "event_id": "unhashable-pointer", "supersedes": ["a", "b"]},
            {**base, "event_id": "not-the-users-voice", "voice": "agent_guess"},
            {**base, "event_id": "bad-source", "capture_source": "invented"},
            {**base, "event_id": "thin-subject", "subject": {"cycle_id": ACME["cycle_id"]}},
        ]
        with open(pr._rationale_path(root), "a", encoding="utf-8") as handle:
            for row in broken:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

        query = pr.query(root, ACME["cycle_id"])
        assert query["total_count"] == 1 and query["unreadable"] == len(broken)
        assert query["effective"]["event_id"] == good["event_id"]
        # And the store stays writable: an unhashable pointer must not brick it.
        assert _say(root, ACME, "a later reason", stated_at="2026-07-02")["status"] == "appended"


def test_a_torn_multibyte_character_costs_one_line_not_the_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        _say(root, ACME, "管理層換人之後我才進場的")
        with open(pr._rationale_path(root), "ab") as handle:
            handle.write(b'{"event_id":"torn","user_statement":"\xe7\xae\xa1\xe7\n')
        query = pr.query(root, ACME["cycle_id"])
        assert query["total_count"] == 1, "the readable statement survives"
        assert query["effective"]["user_statement"] == "管理層換人之後我才進場的"


def test_a_small_cap_actually_bounds_the_reader():
    """`x[-0:]` is `x[0:]`, so a cap of 0/1/2 returning the whole chain while
    reporting `beyond_cap: 0` would be the bounded surface unbounded, asserting
    nothing was held back."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        for n in range(1, 13):
            _say(root, ACME, f"reason {n}", stated_at=f"2026-07-{n:02d}")
        for cap in (0, 1, 2, 3, 8):
            query = pr.query(root, ACME["cycle_id"], cap=cap)
            assert query["shown_count"] == len(query["items"]) <= max(2, cap), cap
            assert query["beyond_cap"] == 12 - query["shown_count"], cap


def test_an_optional_field_supplied_empty_is_refused_not_silently_dropped():
    """Omitting it would make it indistinguishable from absent in the address,
    so two different review sessions could hash identically and the second act
    be lost as a retry."""
    for key in ("origin_id", "condition_ref"):
        try:
            pr.build_event(subject=ACME, act="statement", user_statement="a reason",
                           stated_at="2026-07-01", capture_source="review",
                           state_version="sv-1", **{key: ""})
        except pr.PositionRationaleError as exc:
            assert key in str(exc)
        else:
            raise AssertionError(f"an empty {key} must be refused")


def test_an_independent_branch_cannot_bury_a_newer_statement():
    """Depth places an event inside its own chain and says nothing between two
    chains -- which a relink merge legitimately produces. Ordering those by file
    order alone lets an older statement be reported as the user's current one."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        old = "ACME#2026-01-15#1"
        newer = pr.build_event(subject={**ACME, "cycle_id": old}, act="statement",
                               user_statement="what I said most recently",
                               stated_at="2026-08-20", capture_source="direct",
                               state_version="sv-2")
        with open(os.path.join(tmp, "seed.jsonl"), "w") as _:
            pass
        _say(root, ACME, "an older reason on the other branch", stated_at="2026-07-03")
        with open(pr._rationale_path(root), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(newer, ensure_ascii=False, sort_keys=True) + "\n")
        query = pr.query(root, ACME["cycle_id"], aliases=[old])
        assert query["total_count"] == 2
        assert query["effective"]["user_statement"] == "what I said most recently", (
            "the later statement is the user's current reason even though the "
            "other branch was written to the file first")


def test_a_relinked_cycle_keeps_the_statements_recorded_under_its_old_id():
    """#450 froze it: the reader follows engine-proven cycle relinks. #563's
    later-history upgrade moves a cycle's start, so the id the user's statements
    were written under is no longer the id the book calls that position. A reader
    matching only the current id would report nothing -- the position would look
    remembered and then silently lose its reason, which is the exact failure this
    stream exists to prevent.

    Identity stays `thesis.py`'s to prove: the caller passes the relinks, this
    reader never guesses at continuity."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        _say(root, ACME, "the reason I gave before the history upgrade")
        upgraded = "ACME#2026-01-15#1"          # #563 moved the start date

        blind = pr.query(root, upgraded)
        assert blind["total_count"] == 0, "without the relink the memory is gone"

        seen = pr.query(root, upgraded, aliases=[ACME["cycle_id"]])
        assert seen["total_count"] == 1
        assert seen["effective"]["user_statement"] == \
            "the reason I gave before the history upgrade"
        assert seen["change"] == "initial"

        # And an append continues the same chain rather than starting a new one.
        out = pr.append(root, subject={**ACME, "cycle_id": upgraded}, act="statement",
                        user_statement="and this is why I still hold it",
                        stated_at="2026-07-02", capture_source="direct",
                        state_version="sv-2", aliases=[ACME["cycle_id"]])
        assert out["status"] == "appended"
        after = pr.query(root, upgraded, aliases=[ACME["cycle_id"]])
        assert after["total_count"] == 2 and after["change"] == "changed"
        assert after["items"][-1]["supersedes"] == seen["effective"]["event_id"], (
            "the upgraded cycle's first statement supersedes the pre-upgrade one, "
            "rather than opening a second origin on the same position")


def test_append_deadlocks_inside_a_held_lock_and_append_locked_does_not():
    """The hazard the `append` / `append_locked` split exists for, pinned rather
    than left to a docstring. `session._projection_lock` is a POSIX flock taken
    per file descriptor, so re-entering it from the same process blocks forever
    -- and the caller that would do this is `review.py` finalize, where the cost
    is a hung review rather than an error."""
    probe = (
        "import sys, tempfile, os\n"
        f"sys.path.insert(0, {ENGINE!r})\n"
        "import session, position_rationale as pr\n"
        "S = {'cycle_id':'A#1','ticker':'A','market':'US','currency':'USD'}\n"
        "root = tempfile.mkdtemp()\n"
        "with session.projection_transaction(root) as locked:\n"
        "    pr.%s(locked, subject=S, act='statement', user_statement='x',\n"
        "          stated_at='2026-07-01', capture_source='direct', state_version='v')\n"
        "print('done')\n")
    import subprocess
    try:
        subprocess.run([sys.executable, "-c", probe % "append"],
                       capture_output=True, timeout=6)
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError(
            "append() inside a held projection lock must block -- if this stopped "
            "being true the split is no longer load-bearing and should be simplified")
    done = subprocess.run([sys.executable, "-c", probe % "append_locked"],
                          capture_output=True, timeout=60, text=True)
    assert done.returncode == 0 and "done" in done.stdout, done.stderr


def test_the_shared_fixture_reads_the_way_every_consumer_must_read_it():
    """#450's shared fixture, exercised through the same public reader the
    integration slice will use. It exists so this suite, the integration slice
    and #450's later multi-source reader assert over one history instead of each
    inventing its own -- two readers with two fixtures is how they end up
    disagreeing about what the record says."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "coach")
        seeded = rationale_fixture.seed(root)
        want = seeded["expect"]

        acme = pr.query(root, seeded["acme"]["cycle_id"])
        assert acme["total_count"] == want["acme_total"]
        assert acme["effective"]["user_statement"] == want["acme_effective"]
        assert acme["change"] == want["acme_change"], (
            "the latest act is a confirmation, so nothing changed -- while the "
            "wording in force is still the statement it confirms")
        assert acme["latest"]["act"] == "confirmation"

        widget = pr.query(root, seeded["widget"]["cycle_id"])
        assert widget["change"] == want["widget_change"]
        assert widget["effective"]["user_statement"] == want["widget_effective"]

        relinked = seeded["relinked"]
        blind = pr.query(root, relinked["cycle_id"])
        assert blind["total_count"] == want["relinked_total_without_alias"], (
            "without the engine-proven relink the pre-upgrade statement is "
            "invisible -- the failure the fixture carries this case to catch")
        seen = pr.query(root, relinked["cycle_id"], aliases=relinked["aliases"])
        assert seen["total_count"] == want["relinked_total"]
        assert seen["effective"]["user_statement"] == want["relinked_effective"]

        empty = pr.query(root, seeded["orphan"]["cycle_id"])
        assert empty["total_count"] == want["orphan_total"]
        assert empty["effective"] is None and empty["change"] is None, (
            "a position with no recorded reason returns an empty history rather "
            "than raising or inventing one")


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
