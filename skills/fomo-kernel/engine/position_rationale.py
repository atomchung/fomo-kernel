#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
position_rationale.py — why the user says they still hold one position (#403)

A canonical append-only stream, ``<root>/position_rationales.jsonl``: one chain
per active position cycle, recording the user's own words for why they continue
to hold it. Nothing here rewrites a row. A changed reason is a new event whose
predecessor is the one it follows, so the record keeps what was said and when it
stopped being current, rather than the current answer alone.

This module owns the writer, the fold and one bounded reader (#450). It has no
CLI, reads no review session and never asks a question; the direct entry point,
the review collector and the light-capture migration are separate slices, and
until one of them lands a row in this file is not yet memory — it is a writer
without a reader, which is #429's failure class and is exactly why this module
ships with the bounded query rather than after it.

Three contract decisions differ from the shape frozen on #403, each because the
frozen shape loses a user's words. They are one change, not three:

1. **The content address carries the predecessor.** Frozen, the address is the
   payload minus ``recorded_at``, so a user who says A, then B, then A, then B
   has their fourth statement hash identically to their second and silently
   dropped as a retry — while the writer returns success. Two ``no_change``
   confirmations of the same statement on the same day collide at the second.
   With the predecessor in the address every real act is distinct.

2. **Idempotency is head comparison, not id collision.** The pointer alone
   breaks the retry it was meant to preserve: a write that lands and then dies
   before its receipt re-reads a head that is now its own row, so the retry
   hashes differently and stores the same words twice. Comparing against the
   head is what makes a retry a no-op, and it is what
   ``review._append_evaluation_row`` already does for the same reason.

3. **The stored discriminator is the user's act, not a derived label.**
   ``initial`` versus ``changed`` is position in the chain, and the writer must
   not freeze it: ``thesis.build_snapshot_cycle_relinks`` re-parents a subject
   retroactively by design — Front A of this same issue — so an event honestly
   written ``initial`` can end up behind three earlier ones on the merged
   subject, with the row and the fold then disagreeing and nothing detecting it.
   ``act`` records what the user did (said something / confirmed nothing
   changed); ``supersedes is None`` records that nothing preceded it, which is
   the same fact carried where a later discovery cannot falsify it.

Deviation 1 and 3 must travel together. Dropping the derived label while the
address still has no predecessor makes the collision *worse* — the label is
currently acting as an accidental discriminator, so A→B→A would collide at the
third event instead of the fourth.

``mapping_status`` is likewise not stored, for reason 3's argument: a row either
carries a ``condition_ref`` or it does not, and a second encoding of that in the
same row is one more thing that can disagree with itself.
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import session  # noqa: E402
import thesis  # noqa: E402


class PositionRationaleError(ValueError):
    """Refusal from this module. Never raised for a corrupt line on read."""


SCHEMA_V = 1
# What the user did. Deliberately not `initial | changed | no_change`: the first
# two are the chain's shape rather than the user's act, and reusing those tokens
# in stored data is the confusion this split exists to end.
ACTS = ("statement", "confirmation")
CAPTURE_SOURCES = ("direct", "review", "light_capture")
VOICE = "user_verbatim"
SUBJECT_KEYS = ("cycle_id", "ticker", "market", "currency")

# The bounded reader's shape (#450): earliest, latest, and up to six more of the
# most recent remaining. Eight total, the same bound `review.CONDITION_LOOKUP_CAP`
# and `review.EVALUATION_RECONCILE_CAP` carry, and for the same reason — a plan
# surface re-sent as agent context on every later turn must not grow without one.
QUERY_CAP = 8
RECENT_CAP = QUERY_CAP - 2


def _rationale_path(root):
    return os.path.join(root, "position_rationales.jsonl")


def _iso_date(value, label):
    try:
        return dt.date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise PositionRationaleError(f"{label} must be an ISO date (YYYY-MM-DD)") from exc


def _subject(raw):
    """Validate the engine-resolved subject. The caller never supplies a guess.

    Every key is required: a rationale joined on ticker alone would cross an
    unproven sale and rebuy boundary, which is precisely what #403 forbids and
    what the market/currency pair exists to make impossible.
    """
    if not isinstance(raw, dict):
        raise PositionRationaleError("subject must be an object")
    missing = [key for key in SUBJECT_KEYS if not raw.get(key)]
    if missing:
        raise PositionRationaleError("subject is missing: " + ", ".join(missing))
    extra = sorted(set(raw) - set(SUBJECT_KEYS))
    if extra:
        raise PositionRationaleError("subject has unknown fields: " + ", ".join(extra))
    return {key: str(raw[key]) for key in SUBJECT_KEYS}


def load(path):
    """Return ``(rows, unreadable)`` — every stored event in file order, and how
    many lines could not be read.

    One corrupt line must not cost the user the rest of what they said, but a
    silent skip would make corruption look like a statement they never made, and
    it would make the bounded reader's ``total`` a lie. ``conditions.load_slots``
    sets the precedent: degrade, then say by how much.
    """
    rows, unreadable = [], 0
    if not path or not os.path.exists(path):
        return rows, unreadable
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                unreadable += 1
                continue
            if (isinstance(row, dict) and row.get("event_id")
                    and isinstance(row.get("subject"), dict)
                    and row["subject"].get("cycle_id") and row.get("act") in ACTS):
                rows.append(row)
            else:
                unreadable += 1
    return rows, unreadable


def chain_for(rows, cycle_id):
    """Every event on one subject, oldest first, ordered by the chain itself.

    File order is the tiebreaker, not the ordering: a row's predecessor is what
    places it, so a stream written out of order still reads correctly. A row
    whose predecessor is absent from the file — a dangling pointer left by a
    truncated copy — is kept and treated as a root rather than dropped, because
    losing a statement to a broken link is the loss this stream exists to stop.
    """
    own = [row for row in rows if (row.get("subject") or {}).get("cycle_id") == cycle_id]
    by_id = {row["event_id"]: row for row in own}
    depth_memo, order = {}, {row["event_id"]: index for index, row in enumerate(own)}

    def _depth(event_id, seen):
        if event_id in depth_memo:
            return depth_memo[event_id]
        row = by_id.get(event_id)
        parent = (row or {}).get("supersedes")
        if not row or not parent or parent not in by_id or parent in seen:
            # No parent, a parent outside this file, or a cycle a hand-edited
            # file could contain: a root, never a crash.
            depth_memo[event_id] = 0
            return 0
        depth_memo[event_id] = _depth(parent, seen | {event_id}) + 1
        return depth_memo[event_id]

    return sorted(own, key=lambda row: (_depth(row["event_id"], set()), order[row["event_id"]]))


def head_of(rows, cycle_id):
    """The subject's latest event, or None. What a new event supersedes."""
    chain = chain_for(rows, cycle_id)
    return chain[-1] if chain else None


def effective_statement(rows, cycle_id):
    """The wording currently in force: the latest event that is a statement.

    Derived by walking back through confirmations rather than stored, and never
    copied onto a confirmation row. A user who confirms nothing changed has not
    restated their reason, and recording it as though they had would put words
    in their mouth on a date they did not say them.
    """
    for row in reversed(chain_for(rows, cycle_id)):
        if row.get("act") == "statement":
            return row
    return None


def build_event(*, subject, act, stated_at, capture_source, state_version,
                supersedes=None, user_statement=None, origin_id=None,
                condition_ref=None, recorded_at=None):
    """Build one validated, content-addressed event. Writes nothing.

    ``recorded_at`` is stamped after the identity is computed and is never part
    of it, so a replay of the same act reproduces the same id. Optional fields
    are omitted from the identity rather than sent as null: a key that appears
    only sometimes moves the digest of every row that lacks it, which is the
    defect ``review._evaluation_id``'s docstring records at length.
    """
    if act not in ACTS:
        raise PositionRationaleError(f"act must be one of: {', '.join(ACTS)}")
    if capture_source not in CAPTURE_SOURCES:
        raise PositionRationaleError(
            f"capture_source must be one of: {', '.join(CAPTURE_SOURCES)}")
    if not state_version:
        raise PositionRationaleError(
            "state_version is required: a rationale states which book resolved its subject")
    # The act decides whether words are owed, and the validator enforces it in
    # both directions. A confirmation carrying a statement would be a second
    # copy of wording the user did not restate; a statement without one would be
    # an empty row claiming they said something.
    if act == "statement":
        if not isinstance(user_statement, str) or not user_statement.strip():
            raise PositionRationaleError("a statement requires the user's own words")
    elif user_statement is not None:
        raise PositionRationaleError(
            "a confirmation carries no statement: it says the prior wording still holds, "
            "and copying that wording forward would restate it on a day it was not said")
    if act == "confirmation" and not supersedes:
        raise PositionRationaleError(
            "a confirmation must name the event it confirms; there is nothing to confirm first")

    identity = {
        "schema_version": SCHEMA_V,
        "subject": _subject(subject),
        "act": act,
        "stated_at": _iso_date(stated_at, "stated_at"),
        # Always present, null for a first event. Unlike the optional keys below
        # this one is constant across every row, so a null cannot shift another
        # row's digest — and it is the fact `initial` used to claim.
        "supersedes": supersedes or None,
        "voice": VOICE,
        "capture_source": capture_source,
        "state_version": str(state_version),
    }
    if user_statement is not None:
        # Byte-for-byte. Not stripped, not normalized, not tidied into product
        # phrasing — the whole point of the stream is that these are their words.
        identity["user_statement"] = user_statement
    if origin_id:
        identity["origin_id"] = str(origin_id)
    if condition_ref:
        identity["condition_ref"] = str(condition_ref)

    row = dict(identity)
    row["event_id"] = thesis.stable_event_id("position-rationale", identity)
    row["recorded_at"] = (recorded_at or dt.datetime.now(dt.timezone.utc)
                          .replace(microsecond=0).isoformat())
    return row


def append(root, **kwargs):
    """Append one rationale under the root write lock. See ``append_locked``.

    A caller already inside ``session.projection_transaction`` must call
    ``append_locked`` instead: the lock is a POSIX ``flock`` taken per file
    descriptor, so re-entering it from the same process deadlocks rather than
    nesting. That is why the two are separate functions instead of a flag.
    """
    with session.projection_transaction(root) as locked_root:
        return append_locked(locked_root, **kwargs)


def append_locked(root, *, subject, act, stated_at, capture_source, state_version,
                  user_statement=None, origin_id=None, condition_ref=None,
                  recorded_at=None):
    """Resolve the subject's head, then append unless this act is already recorded.

    Two idempotency rules, and they answer different questions.

    **Head comparison** answers "did this exact act just happen?" — a retry
    whose write landed before the receipt was lost finds its own row as the head
    and returns it rather than storing the same words twice. This is the reason
    the id alone cannot carry idempotency once the predecessor is in the address:
    the retry reads a different head, so it hashes differently.

    **Session comparison** answers "has this review already said this?" — a
    re-finalized session must not resurrect its statement over a newer direct
    one the user has since recorded. Keyed on the producing session and subject,
    mirroring ``session._append_session_rows``, and checked before the head so a
    replay is a no-op regardless of what has happened since.

    A statement whose words equal its predecessor's is legal and is *not* a
    retry: it can only be a deliberate revert to an earlier reason, which the
    frozen three-value contract could not represent at all. An immediate repeat
    is what head comparison absorbs.
    """
    path = _rationale_path(root)
    rows, _unreadable = load(path)
    cycle_id = _subject(subject)["cycle_id"]

    if capture_source == "review" and origin_id:
        for row in chain_for(rows, cycle_id):
            if row.get("capture_source") == "review" and row.get("origin_id") == str(origin_id):
                return {"path": path, "appended": 0, "status": "no-op",
                        "event_id": row["event_id"]}

    head = head_of(rows, cycle_id)
    row = build_event(subject=subject, act=act, stated_at=stated_at,
                      capture_source=capture_source, state_version=state_version,
                      supersedes=(head or {}).get("event_id"),
                      user_statement=user_statement, origin_id=origin_id,
                      condition_ref=condition_ref, recorded_at=recorded_at)
    if head is not None and _same_act(head, row):
        return {"path": path, "appended": 0, "status": "no-op",
                "event_id": head["event_id"]}

    existing = next((r for r in rows if r.get("event_id") == row["event_id"]), None)
    if existing is not None:
        # Content-addressed ids are unique by construction once the predecessor
        # is in the address, so reaching here means the file disagrees with the
        # address that named it. Fail closed rather than pick a winner.
        raise PositionRationaleError(
            f"{row['event_id']} already exists with different content; "
            "the stream was edited outside the engine")

    os.makedirs(root, exist_ok=True)
    prefix = ""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as handle:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                # A prior crash left no trailing newline; appending onto that
                # line would corrupt two rows instead of writing one.
                prefix = "\n"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(prefix + json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {"path": path, "appended": 1, "status": "appended", "event_id": row["event_id"]}


def _same_act(head, candidate):
    """Is the candidate the same act the head already records?

    Compared on what the user did, never on the whole row: ``recorded_at``
    differs on every retry by construction, and ``supersedes`` differs precisely
    in the crashed-retry case this exists to absorb.
    """
    keys = ("act", "stated_at", "user_statement", "capture_source", "origin_id",
            "condition_ref", "state_version")
    return all(head.get(key) == candidate.get(key) for key in keys)


def query(root, cycle_id, *, cap=QUERY_CAP):
    """The bounded position-rationale reader (#450).

    Returns the earliest event, the latest, and up to six of the most recent
    remaining — with ``total``, ``shown`` and ``beyond_cap`` so no reader can
    mistake the list for the whole record. The span, the totals and the
    effective statement are computed from the *untruncated* chain: the cap
    exists to keep a plan surface readable, never to shrink the durable record.

    ``unreadable`` is reported rather than swallowed. A corrupt line means the
    count is a floor, and a reader told a floor is a lie is worse off than one
    told nothing.
    """
    path = _rationale_path(root)
    rows, unreadable = load(path)
    chain = chain_for(rows, cycle_id)
    total = len(chain)
    if not chain:
        return {"cycle_id": cycle_id, "items": [], "effective": None, "latest": None,
                "span": None, "total": 0, "shown": 0, "beyond_cap": 0,
                "unreadable": unreadable}

    earliest, latest = chain[0], chain[-1]
    if total == 1:
        shown = [earliest]
    elif total == 2:
        shown = [earliest, latest]
    else:
        # Earliest and latest are always sent; the rest of the budget goes to the
        # most recent remaining, because a reason's recent history is what a
        # question is worth asking against.
        shown = [earliest] + chain[1:-1][-max(0, cap - 2):] + [latest]
    dates = sorted(row["stated_at"] for row in chain)
    return {
        "cycle_id": cycle_id,
        "items": shown,
        # Derived, never stored: the wording in force is the latest statement,
        # which a run of confirmations does not change.
        "effective": effective_statement(rows, cycle_id),
        "latest": latest,
        "span": {"first": dates[0], "last": dates[-1]},
        "total": total,
        "shown": len(shown),
        "beyond_cap": total - len(shown),
        "unreadable": unreadable,
    }
