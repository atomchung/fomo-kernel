#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""conditions.py — condition slots (#412): a condition the engine cannot compute
is stored, not refused.

Until now the commitment gate had two exits: a ``metric_key`` the engine already
computes, or ``ReviewError``. A user who says *"I'll sell if quarterly revenue
growth drops under 30%"* got the error — and that condition is the most
informative input a review receives, because it is exactly the part of the
user's thinking the engine's defaults did not anticipate.

A slot is that condition given an identity. Three fields carry the design:

===========  ==================================================================
criterion    the user's own words, stored and displayed verbatim (#396)
threshold    ``{value, unit, direction}`` — the comparison, structured
query        a neutral factual lookup, authored once and frozen at creation
===========  ==================================================================

**The comparison never enters the query.** A yes/no lookup (*"did growth fall
below 30%?"*) biases retrieval toward confirmation: the search returns the
nearest matching real event and attaches it to the timeframe the question
supplied, producing a real source, a real number, and a wrong date — a failure
indistinguishable from a correct answer at the point of use. So the engine does
the comparison itself, from ``threshold`` plus a supplied observation, and
``restates_threshold`` fails closed on a query carrying the threshold value.
Stating the criterion as a threshold is correct and is never rewritten; only
folding that threshold into the lookup is prohibited.

**The tier is derived from evidence, never declared.** An agent that asserts its
own tier can assert a wrong one. What the engine can see is whether a numeric
threshold exists and whether a baseline observation with a source and an as-of
date came back with it:

===========================  ==============  ===================================
threshold + observation      ``researched``  watchable; adjudicated with evidence
no numeric threshold         ``unmapped``    nothing to compare against
no baseline observation      ``unmapped``    nothing was found to anchor it
===========================  ==============  ===================================

``unmapped`` is a first-class state, not a failure: it records that the user
committed to something real that we cannot check, which is honest and still
useful next review. ``derived`` and ``fetched`` are the engine's own tiers,
assigned where those paths already live (``state.metrics``, the price
envelope); they are named here so the vocabulary has one home.

**The baseline is taken at commit time**, so a condition that is already met
when the user commits to it is visible immediately rather than a quarter later,
and reconciliation reuses the same then/now shape a ``derived`` rule already has.

**What the mechanical half does not prove.** A restatement that drops the number
(*"did growth fall below the threshold?"*) passes ``restates_threshold`` — the
gate gets the numeric leak, which is the form the failure actually takes, not
neutrality in general. Whether a query is genuinely neutral is rubric-judge work
(``evals/episodes/README.md``, "The two halves").

Storage is ``<root>/conditions.jsonl``: append-only, one row per slot, session
stamped, the same shape as ``theses.jsonl`` and ``rules.jsonl``. A separate file
is the firewall, not a filing preference — ``problems.py`` reconciles rules
mechanically against problem events, and a researched verdict must never enter
``held_streak`` or the graduation statistics on the same footing as a
mechanically verified one (``docs/development-guide.md`` section 5).

**A re-stated criterion is a new slot row, not an edit.** ``build_slot`` takes
an engine-assigned ``revises`` kwarg (the parent slot dict); the new row
inherits the parent's ``line_id`` (or the parent's own ``slot_id``, when the
parent is itself a line's root). ``slot_line_id`` and ``fold_slots`` mirror
``problems.rule_line_id`` and ``load_rules_report``'s revises-chain
reconstruction — same precedent, a second store, tolerant of a fork on read
rather than raising (the write-side guard against creating one is later work).

**The per-period check is a second store, not a second row shape in this one.**
``<root>/condition_checks.jsonl`` holds one append-only row per (slot, review
period): ``build_check`` validates the agent's lookup envelope exactly as
``build_slot`` validates the commitment envelope — the same fail-closed
discipline, an independent ``lookup_status`` axis, "new information arrived"
identified by period + document + value/summary so a re-worded result for the
same period cannot spoof newness, and a two-sided numeric/event verdict split
that never lets a user's answer overwrite what the engine itself computed.
``load_checks`` reads the store back tolerant of corruption, and
``previous_check_for`` resolves check history across a ``revises`` chain so a
revised slot inherits its line's history rather than starting over. Check rows
are an event stream and slot rows are identity — mixing the two shapes in one
file would blur that distinction the moment a reader had to tell them apart by
content instead of by which file they came from.

Still not built here: the flow that calls any of this during a live review, and
the surface that shows a check's verdict to the user. Those are ``review.py``
wiring and land in the next PR of this cut; this module's own test suite is
what keeps this cut from being the written-never-read debt
``docs/development-guide.md`` section 3 warns about in the meantime.
"""

import datetime as dt
import json
import os
import re

KINDS = ("numeric", "event")
TIERS = ("derived", "fetched", "researched", "unmapped")
DIRECTIONS = ("below", "above")
VERDICTS = ("met", "near_line", "not_met", "unknown")
UNMAPPED_REASONS = ("no_threshold", "no_baseline", "no_adjudicator")

# Per-period check (#412 second half). A check row's own vocabulary — separate
# from the slot vocabulary above because a check answers different questions
# (was this looked up this period? did it change? who said the final word?).
LOOKUP_STATUSES = ("ok", "failed", "not_checked")
INFORMATION_STATES = ("new_period", "restated", "no_new_data")
VERDICT_SOURCES = ("engine", "user")
NUMERIC_ANSWERS = ("confirmed", "overridden")
EVENT_ANSWERS = ("yes", "no")

# The near-line margin is frozen when the condition is created. Left adjustable
# it becomes a hidden lever over how often the user is asked (#412).
NEAR_LINE_FRACTION = 0.1

# Free text a durable row will carry forever. Bounds match `review.py`'s note cap.
MAX_TEXT = 500
MAX_SOURCE = 200

_FIELDS = frozenset({"kind", "criterion", "query", "threshold", "near_line", "observation"})
# A check envelope's own field set. `revises`/`line_id` are deliberately absent
# from _FIELDS above (a slot's input never carries them) and from this set too
# (a check does not revise anything) — both are engine-assigned, never agent input.
_CHECK_FIELDS = frozenset({"lookup_status", "reason", "observation", "user_response"})

# A standalone number: `Q3` and `FY2026` are labels, not quantities, so the digit
# glued to a word never counts as the threshold leaking into the query. A bare
# `.5` counts — it is a quantity however it is spelled.
_NUMBER = re.compile(r"(?<![\w.])(?:\d[\d,]*(?:\.\d+)?|\.\d+)")


class ConditionError(ValueError):
    """Raised on a condition the engine must refuse to store as watchable."""


def _text(value, field, limit=None):
    text = str(value or "").strip()
    if not text:
        raise ConditionError(f"condition.{field} is required")
    if limit and len(text) > limit:
        raise ConditionError(f"condition.{field} is longer than {limit} characters")
    return text


def _finite(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConditionError(f"condition.{field} must be a number")
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise ConditionError(f"condition.{field} must be a finite number")
    return value


def numbers_in(text):
    """Every standalone number in a text, normalized so 30 and 30.0 compare equal."""
    return {float(token.replace(",", "")) for token in _NUMBER.findall(str(text or ""))}


def restates_threshold(query, threshold):
    """The one mechanical prohibition on a query: it must not carry the threshold.

    This is the whole criterion-is-not-the-query rule expressed as something a
    machine can settle. See the module docstring for why a yes/no lookup fails in
    a way the reader cannot detect.

    Compared on magnitude, and across the one scale a percentage is routinely
    written in: a `-5%` line written `-5` still leaks when the query says `5%`
    (the number pattern cannot see a minus — a leading `-` in real text is a
    hyphen or a date separator far more often than a sign), and a `0.30` line
    leaks when the query says `30%`. A query is a question, so it rarely carries
    a magnitude at all; over-refusing one costs a rephrase, while under-refusing
    freezes a confirmation-biased lookup into the record permanently."""
    if not threshold:
        return False
    line = abs(float(threshold["value"]))
    leaked = {abs(value) for value in numbers_in(query)}
    return bool(leaked & {line, line * 100, line / 100})


def _threshold(raw):
    """The structured comparison, or None when the criterion carries none."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConditionError("condition.threshold must be an object")
    if raw.get("direction") not in DIRECTIONS:
        raise ConditionError("condition.threshold.direction must be one of " + ", ".join(DIRECTIONS))
    return {"value": _finite(raw.get("value"), "threshold.value"),
            "unit": _text(raw.get("unit"), "threshold.unit"),
            "direction": raw["direction"]}


def _observation(raw):
    """One evidence record.

    ``source`` + ``as_of`` is the anchor #414's ``public_fact`` tag requires and
    the price envelope already uses — one provenance vocabulary, so a later
    review can re-read the basis of a verdict instead of re-deriving it."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConditionError("condition.observation must be an object")
    as_of = _text(raw.get("as_of"), "observation.as_of")
    try:
        dt.date.fromisoformat(as_of)
    except ValueError:
        raise ConditionError("condition.observation.as_of must be an ISO date (YYYY-MM-DD)")
    out = {"value": _finite(raw.get("value"), "observation.value"),
           "as_of": as_of,
           "source": _text(raw.get("source"), "observation.source", MAX_SOURCE)}
    # period/document are what a later check compares against to tell "new
    # information arrived" from "the same quarter, re-worded" (#412).
    for optional in ("period", "document"):
        text = str(raw.get(optional) or "").strip()
        if text:
            out[optional] = text
    return out


def _near_line(raw, threshold):
    if raw is None:
        # A line at zero ("sell if free cash flow goes negative") has no scale to
        # take a fraction of, and a silent 0 would disable the near-line state for
        # the whole turns-negative class without saying so. Ask instead.
        if not threshold["value"]:
            raise ConditionError("condition.near_line is required when the threshold is zero: "
                                 "there is no magnitude to derive a margin from")
        return round(abs(threshold["value"]) * NEAR_LINE_FRACTION, 6)
    margin = _finite(raw, "near_line")
    if margin < 0:
        raise ConditionError("condition.near_line must not be negative")
    return margin


def evaluate(threshold, observation, near_line):
    """``met`` / ``near_line`` / ``not_met`` — computed here, never asserted.

    ``unknown`` whenever there is nothing to compare: an absent observation is a
    condition that could not be checked, which must never read as "checked and
    fine".

    A zero margin disables the band rather than turning exact equality into its
    only member: `near_line` exists to ask early, and "ask me at exactly the
    line and nowhere else" is not a margin anyone chose."""
    if not threshold or not observation:
        return "unknown"
    value, line = observation["value"], threshold["value"]
    met = value < line if threshold["direction"] == "below" else value > line
    if met:
        return "met"
    return "near_line" if near_line > 0 and abs(value - line) <= near_line else "not_met"


def build_slot(raw, *, slot_id, created, session_id=None, revises=None):
    """Validate one agent-supplied condition envelope into a durable slot row.

    ``kind`` is written on every row even though only one value is watchable
    today. These rows are append-only and never rewritten, so a row that says
    what it is costs one field now, while inferring an event condition from the
    absence of a threshold would be permanent — and wrong the moment a numeric
    condition arrives without one.

    ``revises`` (engine-assigned, never accepted on the payload): the parent
    slot dict, when this row re-states an earlier criterion. A re-statement is
    a new row, not an edit to the old one — the old row is a fact about what
    was true when it was written — so the new row records which slot_id it
    replaces and inherits that line's identity (``slot_line_id``), the same
    revises-chain shape ``problems.py`` already uses for rules."""
    if not isinstance(raw, dict):
        raise ConditionError("commitment.condition must be an object")
    unknown = set(raw) - _FIELDS
    if unknown:
        raise ConditionError("commitment.condition has unknown fields: " + ", ".join(sorted(unknown)))
    kind = raw.get("kind") or "numeric"
    if kind not in KINDS:
        raise ConditionError("condition.kind must be one of " + ", ".join(KINDS))
    criterion = _text(raw.get("criterion"), "criterion", MAX_TEXT)
    query = _text(raw.get("query"), "query", MAX_TEXT)
    if query == criterion:
        raise ConditionError("condition.query restates the criterion verbatim: "
                             "a lookup asks what the quantity is, not whether the line was crossed")
    threshold = _threshold(raw.get("threshold"))
    if restates_threshold(query, threshold):
        raise ConditionError("condition.query carries the threshold value: the comparison happens "
                             "after retrieval, never inside the lookup")
    observation = _observation(raw.get("observation"))
    slot = {"slot_id": _text(slot_id, "slot_id"), "kind": kind, "criterion": criterion,
            "query": query, "created": _text(created, "created")}
    if session_id:
        slot["session_id"] = session_id
    if revises is not None:
        if not isinstance(revises, dict) or not revises.get("slot_id"):
            raise ConditionError("revises must be a slot row with a slot_id")
        slot["revises"] = revises["slot_id"]
        slot["line_id"] = slot_line_id(revises)
    # Evidence is kept on every row that has it, watchable or not. It is what was
    # actually found at commit time, and dropping it would make the record thinner
    # than the conversation that produced it.
    if observation is not None:
        slot["baseline"] = observation
    if kind == "event":
        # An event has no line to compare, so its verdict has to come from the
        # person: the agent brings evidence, the user answers yes or no, and that
        # answer is the verdict of record. The flow that asks is not built yet, so
        # the honest state today is unmapped — never a guessed occurrence.
        slot["tier"], slot["unmapped_reason"] = "unmapped", "no_adjudicator"
        return slot
    if threshold is None:
        slot["tier"], slot["unmapped_reason"] = "unmapped", "no_threshold"
        return slot
    slot["threshold"] = threshold
    slot["near_line"] = _near_line(raw.get("near_line"), threshold)
    if observation is None:
        slot["tier"], slot["unmapped_reason"] = "unmapped", "no_baseline"
        return slot
    slot["tier"] = "researched"
    slot["baseline_verdict"] = evaluate(threshold, observation, slot["near_line"])
    return slot


def slot_line_id(slot):
    """A slot line's stable identity = the slot_id of that line's first row.

    Mirrors ``problems.rule_line_id``: a re-stated criterion gets a new
    slot_id via the ``revises`` chain, but it is the same line the user is
    tracking. Root rows written before ``line_id`` existed have no such field
    on disk, so identity falls back to slot_id — a root row IS its own line's
    first row."""
    return str(slot.get("line_id") or slot.get("slot_id") or "")


def fold_slots(slots):
    """Per-line view of a slot store: ``{line_id: {"latest": row, "chain": [rows]}}``,
    plus how many lines ended up forked.

    Mirrors ``problems.load_rules_report``'s revises-chain reconstruction, but
    tolerant rather than raising: a store can develop two heads on one line
    (two rows independently revising the same parent — e.g. two sessions
    racing) and a reader must still return something rather than crash on it.
    The write-side guard that prevents a fork from being created belongs to
    the flow-wiring PR; this is the read side, which must survive a fork that
    already happened. File order wins ties, matching every other append-only
    reader in this codebase: the head chosen for a forked line is whichever
    un-superseded row appears last in the file."""
    superseded = set()
    for row in slots:
        if isinstance(row, dict) and row.get("revises"):
            superseded.add(row["revises"])

    lines = {}
    for row in slots:
        if not isinstance(row, dict) or not row.get("slot_id"):
            continue
        line = slot_line_id(row)
        lines.setdefault(line, {"latest": None, "chain": []})["chain"].append(row)

    forked = 0
    for entry in lines.values():
        heads = [row for row in entry["chain"] if row["slot_id"] not in superseded]
        if len(heads) > 1:
            forked += 1
        entry["latest"] = heads[-1] if heads else entry["chain"][-1]

    return lines, forked


def latest_by_line(slots):
    """Convenience: ``{line_id: latest row}``, discarding the chain and fork
    count. Most callers only want the current state of each line."""
    lines, _forked = fold_slots(slots)
    return {line: entry["latest"] for line, entry in lines.items()}


def load_slots(path):
    """Return ``(slots, unreadable)`` — every stored slot in file order, and how
    many lines could not be read.

    One corrupt line must not cost the user the rest of their record, but a
    silent skip would make corruption look like a condition they never wrote.
    ``problems.load_book`` already sets this precedent with ``skipped_lines``:
    degrade, then say by how much."""
    slots, unreadable = [], 0
    if not path or not os.path.exists(path):
        return slots, unreadable
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
            if isinstance(row, dict) and row.get("slot_id") and row.get("criterion"):
                slots.append(row)
            else:
                unreadable += 1
    return slots, unreadable


# ─────────────────────── per-period checks (#412) ───────────────────────
#
# A check answers different questions than a slot does: was this looked up
# this period? did the world's answer change? who said the final word? Its own
# envelope and its own store (<root>/condition_checks.jsonl) keep those
# questions from leaking into the slot's identity-shaped row.

_CHECK_OBSERVATION_FIELDS = frozenset({"value", "summary", "as_of", "source", "period", "document"})
_USER_RESPONSE_FIELDS = frozenset({"answer", "note", "answered_at"})


def _check_observation(raw, kind):
    """One check's evidence record. Same provenance anchor as a slot's
    baseline (``source`` + ``as_of``), but the payload is EITHER a numeric
    ``value`` (numeric slots) OR a text ``summary`` (event slots) — never
    both, never neither. Both is ambiguous about which side of the kind split
    this evidence is for; neither carries no evidence at all."""
    if not isinstance(raw, dict):
        raise ConditionError("check.observation must be an object")
    unknown = set(raw) - _CHECK_OBSERVATION_FIELDS
    if unknown:
        raise ConditionError("check.observation has unknown fields: " + ", ".join(sorted(unknown)))
    has_value, has_summary = "value" in raw, "summary" in raw
    if has_value and has_summary:
        raise ConditionError("check.observation must carry exactly one of value or summary, not both")
    if not has_value and not has_summary:
        raise ConditionError("check.observation needs value (numeric) or summary (event)")
    if has_value and kind == "event":
        raise ConditionError("check.observation.value is for a numeric condition; this slot is event")
    if has_summary and kind == "numeric":
        raise ConditionError("check.observation.summary is for an event condition; this slot is numeric")
    as_of = _text(raw.get("as_of"), "observation.as_of")
    try:
        dt.date.fromisoformat(as_of)
    except ValueError:
        raise ConditionError("check.observation.as_of must be an ISO date (YYYY-MM-DD)")
    out = {"as_of": as_of, "source": _text(raw.get("source"), "observation.source", MAX_SOURCE)}
    if has_value:
        out["value"] = _finite(raw.get("value"), "observation.value")
    else:
        out["summary"] = _text(raw.get("summary"), "observation.summary", MAX_TEXT)
    # period/document: same role as on a slot's baseline — half of what tells
    # "new information arrived" from "the same period, re-worded" (#412).
    for optional in ("period", "document"):
        text = str(raw.get(optional) or "").strip()
        if text:
            out[optional] = text
    return out


def _user_response(raw, kind):
    """The user's reaction to one check, or None when none was given.
    ``answer``'s vocabulary depends on the slot's kind — confirmed/overridden
    for numeric, yes/no for event — and the wrong kind's vocabulary is
    rejected rather than silently accepted: an event condition answered
    "confirmed" would have no engine_verdict to confirm."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConditionError("check.user_response must be an object")
    unknown = set(raw) - _USER_RESPONSE_FIELDS
    if unknown:
        raise ConditionError("check.user_response has unknown fields: " + ", ".join(sorted(unknown)))
    valid = NUMERIC_ANSWERS if kind == "numeric" else EVENT_ANSWERS
    answer = raw.get("answer")
    if answer not in valid:
        raise ConditionError(
            f"check.user_response.answer must be one of {', '.join(valid)} for a {kind} condition")
    answered_at = _text(raw.get("answered_at"), "user_response.answered_at")
    try:
        dt.date.fromisoformat(answered_at)
    except ValueError:
        raise ConditionError("check.user_response.answered_at must be an ISO date (YYYY-MM-DD)")
    out = {"answer": answer, "answered_at": answered_at}
    note = str(raw.get("note") or "").strip()
    if note:
        if len(note) > MAX_TEXT:
            raise ConditionError("check.user_response.note is longer than 500 characters")
        out["note"] = note
    return out


def _check_identity(observation):
    """The ``(effective_period, document, value-or-summary)`` triple #412
    names as what "new information" is judged against. ``effective_period``
    is the observation's own reporting period when it has one, else its
    as_of date — a price-like condition has no reporting period, and a new
    quote date IS new data for it."""
    period = observation.get("period") or observation["as_of"]
    marker = observation["value"] if "value" in observation else observation.get("summary")
    return period, observation.get("document"), marker


def _information_state(observation, reference):
    """``new_period`` / ``restated`` / ``no_new_data`` for this check's
    observation against ``reference`` (the previous ok-check's observation,
    or the slot's baseline) — or ``new_period`` when there is no reference at
    all, because nothing about this observation has been seen before."""
    if not reference:
        return "new_period"
    cur_period, cur_document, cur_marker = _check_identity(observation)
    ref_period, ref_document, ref_marker = _check_identity(reference)
    if cur_period != ref_period:
        return "new_period"
    if cur_document == ref_document and cur_marker == ref_marker:
        # A re-worded result for the same period cannot spoof newness — owner
        # ruling (#412).
        return "no_new_data"
    return "restated"


def build_check(raw, *, slot, previous, check_id, session_id=None, date_end):
    """Validate one agent-supplied check envelope into a durable check row.

    ``slot`` is the concrete slot row this check evaluates — its kind, frozen
    threshold, and frozen near_line come from there; a check never re-derives
    or re-negotiates them. ``previous`` is the previous ok-check for this
    slot's line, or None (``previous_check_for`` resolves it; this function
    only consumes the result, so it stays a pure function of its arguments,
    exactly like ``build_slot``).

    A lookup that did not succeed this period (``failed`` / ``not_checked``)
    forbids ``observation`` and reports ``final_verdict="unknown"``
    unconditionally: there is no fresh evidence this period for a user to
    confirm, override, or answer against, whatever else the envelope carries.
    An override rejects a met/near_line finding but never touches
    ``engine_verdict`` — the engine's own read is not overwritten, only
    out-voted for this check's verdict of record."""
    if not isinstance(raw, dict):
        raise ConditionError("commitment.check must be an object")
    unknown = set(raw) - _CHECK_FIELDS
    if unknown:
        raise ConditionError("commitment.check has unknown fields: " + ", ".join(sorted(unknown)))
    if not isinstance(slot, dict) or not slot.get("slot_id"):
        raise ConditionError("check.slot must be a slot row with a slot_id")
    kind = slot.get("kind") or "numeric"

    lookup_status = raw.get("lookup_status")
    if lookup_status not in LOOKUP_STATUSES:
        raise ConditionError("check.lookup_status must be one of " + ", ".join(LOOKUP_STATUSES))

    reason = str(raw.get("reason") or "").strip()
    if reason and len(reason) > MAX_TEXT:
        raise ConditionError("check.reason is longer than 500 characters")

    observation_raw = raw.get("observation")
    if lookup_status == "ok":
        if observation_raw is None:
            raise ConditionError("check.observation is required when lookup_status is ok")
        observation = _check_observation(observation_raw, kind)
    else:
        if observation_raw is not None:
            raise ConditionError("check.observation is forbidden unless lookup_status is ok")
        observation = None

    user_response = _user_response(raw.get("user_response"), kind)

    check = {"check_id": _text(check_id, "check_id"), "slot_id": _text(slot["slot_id"], "slot_id"),
             "session_id": session_id or None, "date_end": _text(date_end, "date_end"),
             "created": _text(date_end, "created"), "lookup_status": lookup_status}
    if reason:
        check["reason"] = reason
    if observation is not None:
        check["observation"] = observation
    if user_response is not None:
        check["user_response"] = user_response

    # information_state: only meaningful when this check found something this
    # period. Compare against the previous ok-check's observation, else the
    # slot's own baseline, else there is nothing to call this "not new" against.
    if lookup_status == "ok":
        reference = previous.get("observation") if previous else None
        if reference is None:
            reference = slot.get("baseline")
        check["information_state"] = _information_state(observation, reference)
    else:
        check["information_state"] = None

    # engine_verdict: the engine's own read, computed once and never rewritten
    # by a later user response (see override, below). Event conditions have no
    # line to compare, so the engine never computes one; neither does a check
    # that found nothing this period.
    if kind == "numeric" and lookup_status == "ok":
        engine_verdict = evaluate(slot.get("threshold"), observation, slot.get("near_line") or 0)
    else:
        engine_verdict = None
    check["engine_verdict"] = engine_verdict

    # final_verdict / verdict_source: the verdict of record.
    if lookup_status != "ok":
        final_verdict, verdict_source = "unknown", "engine"
    elif kind == "numeric":
        if user_response is None:
            final_verdict, verdict_source = engine_verdict, "engine"
        elif user_response["answer"] == "confirmed":
            final_verdict, verdict_source = engine_verdict, "user"
        else:  # overridden
            final_verdict, verdict_source = "not_met", "user"
    else:  # event
        if user_response is None:
            # Store, never infer: without the user's answer there IS no verdict.
            final_verdict, verdict_source = "unknown", "engine"
        elif user_response["answer"] == "yes":
            final_verdict, verdict_source = "met", "user"
        else:  # no
            final_verdict, verdict_source = "not_met", "user"
    check["final_verdict"] = final_verdict
    check["verdict_source"] = verdict_source
    return check


def load_checks(path):
    """Return ``(checks, unreadable)`` — every stored check row in file order,
    and how many lines could not be read. Mirrors ``load_slots``: one corrupt
    line must not cost the user the rest of the check history, but a silent
    skip would make corruption look like a check that never happened."""
    checks, unreadable = [], 0
    if not path or not os.path.exists(path):
        return checks, unreadable
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
            if isinstance(row, dict) and row.get("check_id") and row.get("slot_id"):
                checks.append(row)
            else:
                unreadable += 1
    return checks, unreadable


def previous_check_for(checks, slots, slot):
    """The most recent ok-check anywhere on ``slot``'s line, in file order, or
    None.

    Resolved via line identity (``slot_line_id``) rather than ``slot_id``, so
    a slot that was revised — a new slot_id on the same line — still inherits
    the check history its earlier slot_id accumulated. A re-stated criterion
    does not reset "new information" back to unknown; only the criterion text
    changed, not the world the checks are watching."""
    line = slot_line_id(slot)
    line_slot_ids = {row["slot_id"] for row in slots
                     if isinstance(row, dict) and row.get("slot_id") and slot_line_id(row) == line}
    candidates = [c for c in checks if isinstance(c, dict) and c.get("slot_id") in line_slot_ids
                  and c.get("lookup_status") == "ok"]
    return candidates[-1] if candidates else None
