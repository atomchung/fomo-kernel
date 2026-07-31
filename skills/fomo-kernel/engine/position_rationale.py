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

The event contract here is the one the owner accepted on 2026-07-31 (#403,
"Maintainer disposition"), which superseded the issue body on two details. Both
are implemented below; neither is this module's invention. One addition is:
idempotency by head comparison rather than id collision, which the accepted
append semantics do not name and which the chain makes necessary.

1. **The content address carries the predecessor.** Frozen, the address is the
   payload minus ``recorded_at``, so a user who says A, then B, then A, then B —
   one sitting, same day, same book — has their fourth statement hash identically
   to their second and silently dropped as a retry, while the writer returns
   success. That single case is the whole justification; an immediate repeat is
   a no-op either way, and same-day confirmations collapse under head comparison
   either way.

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

1 and 3 must travel together. Dropping the derived label while the address still
has no predecessor makes the collision *worse* — the label is currently acting
as an accidental discriminator, so A→B→A would collide at the third event
instead of the fourth.

Three refusals rather than resolutions, all from the same accepted disposition:
a subject with two children of one predecessor, an ``expected_predecessor`` that
is no longer the head, and a review session returning with different words under
an id it already used. Each could be "resolved" by picking a winner; each winner
would be one the user never nominated.

``mapping_status`` is not stored here, and this is the one open item on #403.
The row carries an optional ``condition_ref``; whether that condition is
monitorable is ``conditions.py``'s answer, and the owner's 2026-07-31 ruling
makes it three states rather than two — a monitored condition, one that is
specific but currently blind, and wording that names nothing observable. A
boolean beside the pointer could not carry the middle state, and duplicating the
tier here would be a second reader of ``conditions.TIERS``.
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


def _readable(row):
    """Is this row safe for every reader below to dereference?

    Deliberately strict about the fields the fold and the bounded query read
    without guarding — a row admitted here and missing one of them would crash
    the reader while `unreadable` still said the file was clean, which inverts
    the degrade-then-say-by-how-much contract instead of merely weakening it.
    `voice` is checked for the same reason it is a const: a row claiming any
    other voice must never be handed back as the user's own words.
    """
    if not isinstance(row, dict):
        return False
    subject = row.get("subject")
    if not isinstance(subject, dict) or any(
            not isinstance(subject.get(key), str) or not subject[key] for key in SUBJECT_KEYS):
        return False
    if not isinstance(row.get("supersedes"), str) and row.get("supersedes") is not None:
        # An unhashable or non-string pointer would raise out of the chain walk
        # and take reads *and* writes for this subject with it, permanently.
        return False
    return (isinstance(row.get("event_id"), str) and row["event_id"]
            and row.get("act") in ACTS
            and isinstance(row.get("stated_at"), str) and row["stated_at"]
            and row.get("voice") == VOICE
            and row.get("capture_source") in CAPTURE_SOURCES)


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
    # errors="replace" so one truncated multi-byte character costs that line
    # rather than every statement in the file: a crash mid-write of a non-ASCII
    # statement would otherwise raise on every later read *and* every later
    # append, which is the opposite of what this function promises.
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                unreadable += 1
                continue
            if _readable(row):
                rows.append(row)
            else:
                unreadable += 1
    return rows, unreadable


def _subject_ids(cycle_id, aliases):
    """The one subject, under every id the engine has proven to be it.

    Identity is ``thesis.py``'s domain, not this module's: the caller passes the
    engine-proven relinks (``thesis.build_snapshot_cycle_relinks``) rather than
    this reader guessing at continuity. #563's later-history upgrade moves a
    cycle's start, and a reader that matched the current id alone would return
    nothing for statements the user still owns — remembered, then silently gone,
    which is the failure this stream exists to prevent.
    """
    return {str(cycle_id)} | {str(alias) for alias in (aliases or ()) if alias}


def chain_for(rows, cycle_id, aliases=()):
    """Every event on one subject, oldest first, ordered by the chain itself.

    File order is the tiebreaker, not the ordering: a row's predecessor is what
    places it, so a stream written out of order still reads correctly. A row
    whose predecessor is absent from the file — a dangling pointer left by a
    truncated copy — is kept and treated as a root rather than dropped, because
    losing a statement to a broken link is the loss this stream exists to stop.
    """
    wanted = _subject_ids(cycle_id, aliases)
    own = [row for row in rows if (row.get("subject") or {}).get("cycle_id") in wanted]
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

    def _root_depth(event_id):
        parent = by_id[event_id].get("supersedes")
        if parent and parent in by_id and _cycles(event_id):
            # A cycle is only reachable in a hand-edited file. Memoizing the
            # zero it produces would serve that caller's descent path to every
            # later lookup, so this branch is computed fresh each time.
            return 0
        return _depth(event_id, set())

    def _cycles(event_id):
        seen, node = set(), event_id
        while node and node in by_id and node not in seen:
            seen.add(node)
            node = by_id[node].get("supersedes")
        return node in seen

    # Depth places an event inside its own chain. Between two independent chains
    # -- which a relink merge legitimately produces -- depth says nothing, so the
    # date the user spoke breaks the tie before file order does. Without it the
    # newest statement can sit mid-list while an older one is reported as latest.
    return sorted(own, key=lambda row: (_root_depth(row["event_id"]),
                                        row.get("stated_at") or "",
                                        order[row["event_id"]]))


def forks_in(rows, cycle_id, aliases=()):
    """Predecessors with more than one child — a corrupt subject, reported not healed.

    Two events superseding the same predecessor cannot both be the current
    reason, and choosing between them by file order or timestamp would pick a
    winner the user never nominated. A reader must still return what it can, so
    `chain_for` linearizes rather than crashing; this is how it says the order it
    produced is not the record's own. `append_locked` refuses outright, because
    a new event superseding an ambiguous head would bury one branch for good.
    """
    wanted = _subject_ids(cycle_id, aliases)
    children = {}
    for row in rows:
        if (row.get("subject") or {}).get("cycle_id") not in wanted:
            continue
        parent = row.get("supersedes")
        if parent:
            children.setdefault(parent, []).append(row["event_id"])
    return sorted(parent for parent, kids in children.items() if len(kids) > 1)


def classify(rows, cycle_id, event_id=None, aliases=()):
    """Derive `initial | changed | no_change` for one event — the frozen
    vocabulary, computed by the one reader rather than stored by the writer.

    Lives here so every consumer reads the same derivation. An integration
    surface that rolled its own would be the second reader of one fact, which is
    the defect not storing the label was meant to end.
    """
    chain = chain_for(rows, cycle_id, aliases)
    if not chain:
        return None
    row = next((r for r in chain if r["event_id"] == event_id), chain[-1]) \
        if event_id else chain[-1]
    if row.get("act") == "confirmation":
        return "no_change"
    return "initial" if not row.get("supersedes") else "changed"


def head_of(rows, cycle_id, aliases=()):
    """The subject's latest event, or None. What a new event supersedes."""
    chain = chain_for(rows, cycle_id, aliases)
    return chain[-1] if chain else None


def effective_statement(rows, cycle_id, aliases=()):
    """The wording currently in force: the latest event that is a statement.

    Derived by walking back through confirmations rather than stored, and never
    copied onto a confirmation row. A user who confirms nothing changed has not
    restated their reason, and recording it as though they had would put words
    in their mouth on a date they did not say them.
    """
    for row in reversed(chain_for(rows, cycle_id, aliases)):
        if row.get("act") == "statement":
            return row
    return None


def build_event(*, subject, act, capture_source, state_version, stated_at=None,
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
        # Frozen default: the invocation's local calendar date. Sub-day precision
        # is deliberately not what separates two same-day revisions — the
        # predecessor chain is. A user referring to an earlier day supplies that
        # date; nothing here invents one on their behalf.
        "stated_at": _iso_date(stated_at or dt.date.today().isoformat(), "stated_at"),
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
    if supersedes is not None and not isinstance(supersedes, str):
        raise PositionRationaleError("supersedes must be an event id string or None")
    for key, value in (("origin_id", origin_id), ("condition_ref", condition_ref)):
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            # Omitting an empty string would make it indistinguishable from
            # absent in the address, so two different sessions could hash
            # identically and the second act be lost as a retry.
            raise PositionRationaleError(f"{key} must be a non-empty string when supplied")
        identity[key] = value

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


def append_locked(root, *, subject, act, capture_source, state_version,
                  stated_at=None, user_statement=None, origin_id=None,
                  condition_ref=None, recorded_at=None, expected_predecessor=None,
                  aliases=()):
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

    An immediate repeat of the head's exact act is a no-op, deliberately and by
    the accepted contract — "identical payload against the same predecessor is
    an idempotent no-op". What the chain buys is the *non-immediate* repeat: the
    fourth event of A→B→A→B is a real re-statement with its own id, where the
    address without a predecessor would have swallowed it. Saying the user can
    restate identical words back-to-back would be a promise this code does not
    keep, and the contract does not ask it to.

    Two further refusals the accepted disposition requires, both fail-closed
    rather than resolved by picking a winner: an already-forked subject, and an
    `expected_predecessor` that is no longer the head.
    """
    path = _rationale_path(root)
    rows, _unreadable = load(path)
    cycle_id = _subject(subject)["cycle_id"]

    forks = forks_in(rows, cycle_id, aliases)
    if forks:
        raise PositionRationaleError(
            f"{cycle_id} has two events superseding {', '.join(forks)}; the head is "
            "ambiguous and appending would bury one branch. Repair the stream rather "
            "than letting file order choose which reason the user meant")

    head = head_of(rows, cycle_id, aliases)
    head_id = (head or {}).get("event_id")
    if expected_predecessor is not None and expected_predecessor != head_id:
        raise PositionRationaleError(
            f"expected predecessor {expected_predecessor or '(none)'} but the head is "
            f"{head_id or '(none)'}; something was recorded in between. Re-read and retry "
            "rather than forking the subject")

    row = build_event(subject=subject, act=act, stated_at=stated_at,
                      capture_source=capture_source, state_version=state_version,
                      supersedes=head_id, user_statement=user_statement,
                      origin_id=origin_id, condition_ref=condition_ref,
                      recorded_at=recorded_at)

    if capture_source == "review" and origin_id:
        for prior in chain_for(rows, cycle_id, aliases):
            if not (prior.get("capture_source") == "review"
                    and prior.get("origin_id") == str(origin_id)):
                continue
            if _same_act(prior, row):
                return {"path": path, "appended": 0, "status": "no-op",
                        "event_id": prior["event_id"]}
            # A replay is a no-op; a session that comes back saying something
            # *different* under the same id is not a replay, and silently
            # keeping the older words is the loss this stream exists to stop.
            # `session._append_session_rows` refuses the same shape.
            raise PositionRationaleError(
                f"session {origin_id} already recorded a different rationale for {cycle_id} "
                f"({prior['event_id']}); a re-finalize may not silently replace the user's "
                "words. Record the correction as a new statement instead")
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


def query(root, cycle_id, *, cap=QUERY_CAP, aliases=()):
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
    chain = chain_for(rows, cycle_id, aliases)
    total = len(chain)
    if not chain:
        return {"cycle_id": cycle_id, "items": [], "effective": None, "latest": None,
                "change": None, "span": None, "total_count": 0, "shown_count": 0, "beyond_cap": 0,
                "unreadable": unreadable, "forked": []}

    earliest, latest = chain[0], chain[-1]
    if total == 1:
        shown = [earliest]
    elif total == 2:
        shown = [earliest, latest]
    else:
        # Earliest and latest are always sent; the rest of the budget goes to the
        # most recent remaining, because a reason's recent history is what a
        # question is worth asking against.
        room = max(0, cap - 2)
        # `x[-0:]` is `x[0:]`, so a cap of 0/1/2 would silently return the whole
        # chain while reporting `beyond_cap: 0` -- the bounded surface unbounded,
        # asserting nothing was held back.
        middle = chain[1:-1][-room:] if room else []
        shown = [earliest] + middle + [latest]
    dates = sorted(row["stated_at"] for row in chain)
    return {
        "cycle_id": cycle_id,
        "items": shown,
        # Derived, never stored: the wording in force is the latest statement,
        # which a run of confirmations does not change.
        "effective": effective_statement(rows, cycle_id, aliases),
        "latest": latest,
        # The frozen `initial | changed | no_change` vocabulary, derived here so
        # no consumer has to re-derive it and disagree.
        "change": classify(rows, cycle_id, aliases=aliases),
        # Non-empty means the subject is forked and this order is the reader's,
        # not the record's. A caller must not present it as the user's history.
        "forked": forks_in(rows, cycle_id, aliases),
        "span": {"first": dates[0], "last": dates[-1]},
        # #450 froze these two names; do not shorten them to match
        # `_evaluation_reconciliation`'s local spelling.
        "total_count": total,
        "shown_count": len(shown),
        "beyond_cap": total - len(shown),
        "unreadable": unreadable,
    }
