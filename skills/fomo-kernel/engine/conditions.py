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

Deliberately not built here: the per-period check (lookup status, evidence
snapshot, two-sided adjudication) and the ``revises`` chain that a re-stated
criterion needs. Both are written by the check flow, and a store that can hold
rows nothing writes is the written-never-read debt this repository keeps paying
(``docs/development-guide.md`` section 3).
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

# The near-line margin is frozen when the condition is created. Left adjustable
# it becomes a hidden lever over how often the user is asked (#412).
NEAR_LINE_FRACTION = 0.1

# Free text a durable row will carry forever. Bounds match `review.py`'s note cap.
MAX_TEXT = 500
MAX_SOURCE = 200

_FIELDS = frozenset({"kind", "criterion", "query", "threshold", "near_line", "observation"})

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


def build_slot(raw, *, slot_id, created, session_id=None):
    """Validate one agent-supplied condition envelope into a durable slot row.

    ``kind`` is written on every row even though only one value is watchable
    today. These rows are append-only and never rewritten, so a row that says
    what it is costs one field now, while inferring an event condition from the
    absence of a threshold would be permanent — and wrong the moment a numeric
    condition arrives without one."""
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
