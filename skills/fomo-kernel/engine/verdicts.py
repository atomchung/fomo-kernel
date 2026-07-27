#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verdicts.py -- behavior verdicts (#446 cut 1): stop recomputing-and-discarding
a said-vs-done judgment the engine already makes every period.

``horizon.horizon_contradiction()`` compares a thesis's declared holding
horizon against how long the position was actually held, and returns
``exit_too_fast`` / ``held_too_long`` / ``None``. Today that judgment is folded
into ``review._horizon_markers``, truncated to ``HORIZON_MARKER_LIMIT`` for the
card's attention budget, and thrown away at the end of the process --
"this is the third period running" is unanswerable. This module turns it into
an append-only row at ``<root>/verdicts.jsonl``, one per (subject, rule,
period).

**Separate store, for the firewall ``conditions.py:66-72`` already gives**: a
verdict must never enter ``problems.check_rules``' mechanical reconciliation --
polluting ``held_streak`` with a permanent ``skipped`` is exactly the
corruption the Opportunity Check firewall exists to prevent, and a said-vs-done
judgment has no problem key to join against in the first place. This module
never imports ``problems`` (``tests/test_verdicts.py`` pins it, mirroring
``conditions.py``'s own gate).

**The replay contract**: a verdict recomputes to the same ``outcome`` from its
own frozen inputs, under its frozen rule version, forever -- regardless of what
later happens to the thesis row its ``subject`` points at or the profile label
its optional ``profile_label_id`` references::

    replay(rule.id, rule.version, subject.value, observed.value, observed.closed) == outcome

``replay`` is deliberately a pure function of exactly those five scalars. It
never re-reads a store, never re-folds a thesis line, and never looks up a
profile label -- there is no parameter for any of that, by construction. A
verdict's ``profile_label_id`` (optional, #446 cut 2 -- not built yet) is a
back-reference only, load-bearing for nothing; removing it changes no replay
result.

**Rule-version discipline**: the one way replay can fail silently is a
parameter change with no version bump. ``RULE_PARAMS`` holds every rule's
versioned parameter block, frozen by hand -- never read live from
``horizon.py`` -- so a later edit to ``horizon.py``'s own thresholds cannot
silently rewrite what an existing version means. ``RULE_PARAM_DIGEST`` pins
one hash per ``(rule_id, version)`` as a literal (not recomputed at import
time, which would make the check vacuous, and not one combined hash over the
whole table, so a failing assertion names the exact version that moved
instead of just "something changed"); ``tests/test_verdicts.py`` asserts
every entry agrees with its own pinned hash, and that the entry set matches
``RULE_PARAMS`` exactly in both directions, so editing a threshold inside an
existing version without adding a new one and updating its digest turns the
suite red. A companion test cross-checks ``RULE_PARAMS``'s live version
against ``horizon.EXIT_FAST`` / ``horizon.HELD_LONG`` so the two modules
cannot drift apart unnoticed in the other direction either -- someone editing
``horizon.py`` directly, without ever touching this file.

The digest is necessary but not sufficient, and is deliberately the WEAKER of
two guards (external review, #446 BLOCK 1): it only proves ``RULE_PARAMS``
and this literal agree *today*. Changing a published version's threshold and
updating its digest entry in the same edit -- the natural, obvious way to
make a red digest test green again -- passes the digest test while silently
rewriting what every already-stored verdict under that version replays to.
What a digest edit cannot silence is ``tests/test_verdicts.py``'s frozen
replay corpus: literal ``(subject_value, observed_value, observed_closed) ->
outcome`` rows at and one either side of every version-1 threshold, asserted
directly against ``replay()`` and never derived from ``RULE_PARAMS`` or
``horizon.py``. Moving a threshold turns the corpus red regardless of what
the digest says; the only way to make it green again is to publish a new
version and leave the old one's numbers alone, which is the behaviour this
module exists to force. A reader who trusts the digest alone and has not
read this paragraph will "fix" the wrong thing first: update the digest,
watch it go green, ship the regression.

**No ``input`` sub-object**, for the reason ``condition-check.schema.json``
already gives about ``user_response``: every field here is engine-assigned. An
agent that could write its own verdict could record an adjudication nobody
made, undetectably after the fact.

**Dedup**: one row per (subject.row_id, rule.id, rule.version, date_end) --
globally, not just within one session (external review, #446 BLOCK 2:
the original cut 1 shape got this wrong). ``session._append_session_rows``
still does the write, and still owns the *same-session* idempotent-retry /
fail-closed-on-conflict contract every other durable store in this codebase
already relies on -- but its dedup key is ``session_id`` alone, and
``--session-nonce`` exists precisely so a user can review identical state as
a genuinely distinct session (``references/data-contract.md``). That is
correct nonce behaviour, not a bug to route around, so a verdict's OWN
identity has to be checked before a row ever reaches that helper:
``verdict_identity()`` names the (subject, rule, period) tuple, and
``dedupe_against_other_sessions()`` drops a new row whose identity already
exists under a *different* session_id. Same-session rows are left alone, so
``_append_session_rows``'s own retry contract for the current session is
untouched -- this only closes the gap that helper cannot see. Two rows
sharing an identity but disagreeing on ``outcome`` fail closed
(``VerdictError``): every ingredient behind a verdict's outcome
(holding_days, exited) is pure date/state arithmetic with no session-specific
input, so two honest sessions judging the same subject and period can never
legitimately disagree -- a mismatch means something upstream is already
inconsistent, and silently keeping one side would hide that rather than
surface it.

A position that stays over its horizon line still re-triggers every period
it is reviewed: recurrence across DIFFERENT ``date_end`` values is not
noise, it is the material the profile layer (#446 cut 2) consumes. Only the
same (subject, rule, period) triple collapses; a later period is always a
new triple, never deduped against an earlier one.
"""
import datetime as dt
import hashlib
import json

import horizon

VERDICT_SOURCES = ("engine", "user")

# Every rule usable here names its own thresholds under its own version.
# Version 1 mirrors horizon.py's live EXIT_FAST/HELD_LONG tables exactly as
# they stand today (#446 cut 1's only rule) -- frozen by hand, not read live
# from horizon.py, so a later edit to horizon.py cannot silently rewrite what
# version 1 means. test_verdicts.py cross-checks the two so they cannot drift
# apart unnoticed.
RULE_PARAMS = {
    "horizon_contradiction": {
        1: {
            "exit_fast": {"years": 90, "quarters": 21},
            "held_long": {"weeks": 60, "quarters": 180},
        },
    },
}

# The live version new rows stamp into rule.version. _horizon_params refuses
# any version RULE_PARAMS does not carry, including this one if it is ever
# bumped without adding the matching entry first.
HORIZON_RULE_VERSION = 1


def _params_digest(params):
    """A stable hash of a RULE_PARAMS-shaped dict. Deliberately not called at
    import time to produce RULE_PARAM_DIGEST below -- that would make the
    agreement test vacuous, since a live recomputation always agrees with
    itself. Call this by hand to regenerate the pinned literal."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Pinned literals, one per (rule_id, version) -- never one combined hash over
# the whole RULE_PARAMS tree (external review, #446 BLOCK 1: a single hash
# only tells a reader "something in RULE_PARAMS changed"; per-entry digests
# make a failing assertion name the exact rule and version that moved).
# Recompute one entry with _params_digest(RULE_PARAMS[rule_id][version]) and
# update only that entry, by hand, in the same commit as the version bump
# that motivated the change. See the module docstring's "Rule-version
# discipline" section -- including why this digest is the WEAKER of the two
# guards there, and must never be trusted alone.
RULE_PARAM_DIGEST = {
    ("horizon_contradiction", 1): "1a5b8b4d503eda4a94724dfe35d360d34441085b3dd735be22684beaa7df07e9",
}


class VerdictError(ValueError):
    """Raised on a rule id/version replay cannot resolve."""


def _horizon_params(rule_version):
    params = RULE_PARAMS.get("horizon_contradiction", {}).get(rule_version)
    if params is None:
        raise VerdictError(f"unknown horizon_contradiction rule version: {rule_version!r}")
    return params


def _horizon_outcome(params, subject_value, observed_value, observed_closed):
    """Mirrors horizon.horizon_contradiction's comparison shape exactly, but
    reads its thresholds from a frozen, versioned ``params`` block instead of
    horizon.py's live module constants -- the whole reason replay can survive
    horizon.py's thresholds moving on to a later version. horizon value is
    normalized the same way horizon.py normalizes it (legacy zh aliases
    included), so a verdict frozen against an old stored alias still replays."""
    subject_value = horizon.normalize_horizon(subject_value)
    if subject_value not in horizon.HORIZONS:
        return None
    if not isinstance(observed_value, (int, float)) or isinstance(observed_value, bool):
        return None
    if observed_closed:
        threshold = params["exit_fast"].get(subject_value)
        return "exit_too_fast" if (threshold is not None and observed_value < threshold) else None
    threshold = params["held_long"].get(subject_value)
    return "held_too_long" if (threshold is not None and observed_value > threshold) else None


def replay(rule_id, rule_version, subject_value, observed_value, observed_closed):
    """Recompute a verdict's outcome purely from its five frozen inputs. See
    the module docstring's replay contract. Never reads anything else --
    not a labels store, not a fresh fold of the row subject.row_id names."""
    if rule_id != "horizon_contradiction":
        raise VerdictError(f"unknown rule id: {rule_id!r}")
    params = _horizon_params(rule_version)
    return _horizon_outcome(params, subject_value, observed_value, observed_closed)


def _window(window_to, holding_days):
    """{"from", "to"} for one observation, derived from the same two values
    horizon.py's own holding_days arithmetic already produced -- never a
    second parse of the cycle_id (that parsing stays inside horizon.py's
    ``_cycle_start``, the one reader). None when either ingredient is
    unusable, so a verdict this unsure of its own window is not written."""
    if not window_to or holding_days is None:
        return None
    try:
        end = dt.date.fromisoformat(str(window_to))
        start = end - dt.timedelta(days=int(holding_days))
    except (TypeError, ValueError, OverflowError):
        return None
    return {"from": start.isoformat(), "to": end.isoformat()}


def build_horizon_verdict(marker, *, row_id, session_id, date_end, window_to):
    """One durable verdicts.jsonl row from one horizon.scan marker (#446 cut
    1). ``marker`` is one element of ``review._horizon_markers_all``'s
    UNTRUNCATED result -- never ``review._horizon_markers``'s display-capped
    slice (#444's round 3-4 defect, repeated verbatim otherwise: nothing
    downstream may be scoped to a display cap's slice).

    ``row_id`` is the thesis row that stated the horizon this marker judged --
    its ``event_id``, the identity of that specific revision, never its
    stable cross-revision ``thesis_id``. A later revision writes a new
    event_id, so an already-written verdict is never silently repointed at a
    restatement it never judged.

    Returns None when a required ingredient is missing or unusable, mirroring
    horizon.py's own skip-rather-than-guess discipline: a verdict this unsure
    of its own subject or window is not written at all, rather than written
    with a guessed value."""
    if not row_id or not session_id or not date_end:
        return None
    outcome = marker.get("kind")
    if outcome not in ("exit_too_fast", "held_too_long"):
        return None
    subject_value = horizon.normalize_horizon(marker.get("horizon"))
    if subject_value not in horizon.HORIZONS:
        return None
    holding_days = marker.get("holding_days")
    window = _window(window_to, holding_days)
    if window is None:
        return None
    return {
        "rule": {"id": "horizon_contradiction", "version": HORIZON_RULE_VERSION},
        "subject": {"store": "theses", "row_id": row_id, "field": "horizon", "value": subject_value},
        "observed": {"metric": "holding_days", "value": holding_days, "window": window,
                     "closed": bool(marker.get("exited"))},
        "outcome": outcome,
        "verdict_source": "engine",
        "date_end": date_end,
        "session_id": session_id,
    }


def verdict_identity(row):
    """The (subject, rule, period) tuple that names which fact a verdict row
    is a judgment about -- deliberately excluding session_id. Two rows
    sharing this tuple are the SAME verdict even when built by two different
    sessions: ``--session-nonce`` legitimately produces a distinct session_id
    for identical underlying state (``references/data-contract.md``), and
    every ingredient behind a verdict's outcome is pure date/state
    arithmetic, so two honest sessions judging the same subject and period
    always compute the same row. See the module docstring's "Dedup"
    section."""
    rule = row.get("rule") or {}
    subject = row.get("subject") or {}
    return (rule.get("id"), rule.get("version"), subject.get("store"),
            subject.get("row_id"), subject.get("field"), row.get("date_end"))


def dedupe_against_other_sessions(existing_rows, new_rows, session_id):
    """Filter ``new_rows`` (this session's freshly built verdicts) against
    ``existing_rows`` (every verdicts.jsonl row already on disk, from any
    session) on ``verdict_identity``, dropping a new row whose identity
    already exists under a DIFFERENT session_id.

    This runs BEFORE ``session._append_session_rows`` ever sees the rows.
    That helper's own dedup is keyed on session_id alone, which is exactly
    wrong for a verdict's identity (external review, #446 BLOCK 2): a
    distinct ``--session-nonce`` reviewing identical state and date builds a
    verdict identical in everything but session_id, and the session-scoped
    helper has no way to recognize the two as the same fact. Existing rows
    from THIS session are deliberately left untouched here -- they are
    ``_append_session_rows``'s own idempotent-retry / fail-closed-on-conflict
    contract to enforce, and this function must not duplicate or shadow it.

    A new row whose identity already exists with a DIFFERENT outcome raises
    ``VerdictError`` instead of silently keeping the first or appending both.
    Deliberate choice, not an incidental default: an outcome disagreement
    under one identity means the engine computed two different answers to
    the same said-vs-done question, which cannot happen from honest inputs
    (holding_days and exited are pure functions of state and date_end, never
    of session_id) -- so it signals an inconsistency upstream that a silent
    pick would hide rather than surface, matching this codebase's existing
    fail-closed-on-conflict convention for every other durable store.
    """
    known = {}
    for row in existing_rows:
        if row.get("session_id") != session_id:
            known.setdefault(verdict_identity(row), row)
    kept = []
    for row in new_rows:
        identity = verdict_identity(row)
        prior = known.get(identity)
        if prior is not None:
            if prior.get("outcome") != row.get("outcome"):
                raise VerdictError(
                    f"conflicting verdict outcome for {identity}: "
                    f"{prior.get('outcome')!r} (session {prior.get('session_id')!r}) "
                    f"vs {row.get('outcome')!r} (session {row.get('session_id')!r})")
            continue
        known[identity] = row
        kept.append(row)
    return kept
