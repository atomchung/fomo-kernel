#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""answer_provenance.py — semantic provenance gate for a ``--agent-case``
answer (issue #414, "Production provenance gate for decision-surface
answers", Wave A scope only).

``review.py::_validate_agent_case`` already enforces the *structural* shape
of ``--agent-case``: it is an object with exactly ``for``/``against``, each
a list of objects carrying at least ``claim``/``provenance``,
``provenance`` one of ``review.AGENT_CASE_PROVENANCE``, ``claim`` a
non-empty string. It deliberately stops there and does not pin the exact
field set a claim may carry — that is provenance-dependent (``anchor``/
``worsens`` for ``engine_fact``, ``source``/``as_of`` for ``public_fact``)
and belongs to this module alone (#479 Wave B). That check has no opinion
on whether a claim labelled
``engine_fact`` actually describes the frozen consequence it claims to,
whether a ``public_fact`` claim carries a citation, or whether either side
is merely present-but-empty. This module is that missing semantic layer:
given the *frozen* inputs ``cmd_consider`` already computes, it decides
whether an ``agent_case`` payload may be persisted or shown to the user at
all.

Public API (frozen for Wave B, #414's single integration owner, per the
issue's Wave allocation comment)::

    validate_agent_case(agent_case, *, basis, consequence,
                         rule_collisions=(), user_statements=())
    required_coverage(basis, consequence, rule_collisions=())
    build_record(basis, consequence, rule_collisions)
    resolve_anchor(record, anchor) -> value | UNRESOLVED

``validate_agent_case`` is the gate. The other three are the pieces of it
``evaluation_challenge`` reads so that the block telling the agent what an
answer owes, and the check refusing an answer that leaves something out,
are one implementation seen from two sides (#479 Wave B cut 2). Note what
this deliberately is *not*: ``validate_agent_case`` derives its own
requirements by calling ``required_coverage`` internally and takes no
caller-supplied list of them, so no caller can weaken the gate by handing
it a short one.

``agent_case``, ``basis``, ``consequence``, and ``rule_collisions`` are
exactly ``cmd_consider``'s own frozen shapes — the same dicts that already
end up as ``row["agent_case"]``, ``row["basis"]``, ``row["consequence"]``,
and ``row["rule_collisions"]`` (schemas/trade-evaluation.schema.json).
``user_statements`` is new: an optional, frozen collection of exact strings
the user is on record as having said for this evaluation (#479's
decision-context envelope is the expected future supplier; empty by
default, so a context-free ``consider`` call — which never captured user
prose — validates exactly as if this parameter did not exist, preserving
issue #414's "Preserve: context-free consider compatibility"). No I/O, no
file paths, no global state: every input is a value the caller already
froze, and every output is either a clean return (the answer is
provenance-clean) or an ``AnswerProvenanceError`` naming exactly which
claim, and which rule, failed. There is no third "valid-with-warnings"
outcome — the issue's "returns a decisive result the caller can fail
closed on" requirement — so Wave B can call this immediately before
``_append_evaluation_row`` (review.py:5490, right after the existing
structural call to ``_validate_agent_case`` at review.py:5434) exactly the
way every other hand-rolled validator in this engine is already used: a
raise before that point means the row is never written and never returned
to the caller.

Design decision — the anchor form
----------------------------------
An ``engine_fact`` claim must carry a new ``anchor`` field: a dot-separated
path into a single frozen bundle assembled from ``basis``, ``consequence``,
and ``rule_collisions`` (``build_record``) — e.g.
``"consequence.after.max_pct"``, ``"basis.stale_days"``, or
``"rule_collisions.rule-1.worsens"`` (``rule_collisions`` is re-keyed by its
own ``rule_id`` — the identity the agent already has, from the very JSON
``consider`` handed back to it — rather than addressed by list position,
which shifts the moment a rule is muted between one review and the next).

This mirrors a mechanism already shipped in this codebase for exactly the
same purpose: ``question_surface.py``'s ``grounding_refs`` /
``_resolve_context_ref`` (question_surface.py:244) walk a dotted path into
a ``context`` object and refuse to resolve to a container ("must resolve to
one supplied fact"). The difference here is scale — ``context`` there is a
small, curated, hand-enumerated whitelist (``GROUNDING_REFS``);
``consequence``'s own shape is large, carries list-valued fields
(``rule_collisions``, ``excluded_holdings``), and grows whenever
consequence.py grows. A hand-enumerated whitelist of every leaf path here
would need a code change every time consequence.py gained a field — exactly
the mirrored surface docs/maintainer-guide.md warns against. Walking the three named roots
generically, and refusing anything that does not bottom out at a scalar,
gets the same "resolves to one fact, never a container" guarantee without a
second copy of consequence.py's field list. This is mechanically exactly
what the issue asks for: an agent can produce it (it already holds the
frozen JSON and can read off the path) and a validator can check it
mechanically (pure dict/list traversal, no interpretation).

Design decision — number extraction and comparison
-----------------------------------------------------
``conditions.numbers_in`` (conditions.py:203) is reused directly and
unchanged — never reimplemented — for the reason its own module docstring
gives for existing at all: "the check and the gate must not disagree about
what a number is." It extracts every standalone number in a text as a set
of floats (comma-stripped, decimal-aware); this module never writes its own
number-scanning regex.

The frozen value a claim is checked against is compared at the single
display scale this codebase's own numbers are actually written in. Every
percentage-shaped reading consequence.py produces (``max_pct``, ``top3``,
``ai_pct``, ``max_sector_pct``, ``weights[...]``, ``cash["weight"]``,
``delta[...]``) is a fraction in [-1, 1] and is always written in prose
multiplied by 100 ("34%", never "0.34"); everything else (``stale_days``,
share/holding counts, dollar balances) is written as-is. Rather than
hand-listing which field *names* are percentages (a second copy of
consequence.py's own field shape, one more thing to keep in sync as that
module grows), the rule reads the *value*: a frozen number whose magnitude
is at most 1 is compared against both itself and itself x100; anything
larger is compared only as-is. A claim is accepted when one of its quoted
numbers lands within ``_DISPLAY_TOLERANCE`` (0.5, in whichever scale
applies) of the frozen value — tight enough to catch a materially different
number (34% cited as 40%) while tolerating the rounding any hand-written
claim will naturally do (0.3423419... written as "34%"). Like
``conditions.restates_threshold``, the comparison is on magnitude only:
``numbers_in`` never carries a sign (a leading "-" in real text is a hyphen
far more often than a minus), so a quoted number is compared in absolute
value — the same limitation conditions.py's own docstring names for the
identical reason.

Design decision — the provenance vocabulary is restated, not imported
--------------------------------------------------------------------------
``PROVENANCE`` below is byte-identical to ``review.AGENT_CASE_PROVENANCE``
(review.py:105) and is a restatement, not an import: Wave B's job is making
``review.py`` import *this* module, so this module importing ``review.py``
back would be a circular import from the day that integration lands.
Nothing in this file imports ``review``. ``tests/test_answer_provenance.py``
imports both modules — a test is not part of either module's own import
graph — and asserts the two tuples are equal, the same cross-check
``tests/test_consider.py::test_consider_decisions_constant_matches_the_schemas_decision_enum``
already runs for ``CONSIDER_DECISIONS``, so the two constants cannot
silently drift even though neither module imports the other.

Design decision — public_fact citation fields are ``source`` / ``as_of``
-------------------------------------------------------------------------
A ``public_fact`` claim must carry ``source`` and ``as_of`` — not
``source_ref`` / ``observed_at``, the alternate names the issue's own prose
also offers. This follows a decision already shipped in this repository,
ahead of this module: ``schemas/condition-slot.schema.json``'s
``observation`` ``$defs`` (``value``/``as_of``/``source``) states in its own
description, "source + as_of is the same provenance anchor #414's
public_fact tag requires and the price envelope already uses" — and
``schemas/price-feed.schema.json`` does indeed name its own citation fields
``source`` and ``as_of``/``date`` the same way. Picking ``source_ref`` /
``observed_at`` here would be a second, competing public-fact vocabulary the
day someone reads both schemas side by side — exactly the mirrored-surface
drift docs/maintainer-guide.md forbids — so this module uses the pair the codebase already
committed to.

Design decision — the already_over / worsens direction (case 7)
---------------------------------------------------------------------
``worsens`` is a boolean the engine computes (consequence.py's
``_worsened``); free prose has no reliable, mechanical way to encode "this
trade makes it worse" versus "this trade improves it" that a validator
could check without scoring sentiment — exactly the "general NLI/
factuality scoring" the issue defers. So a claim whose anchor targets a
``rule_collisions[...].state`` or ``.worsens`` field, where the frozen row
is ``already_over`` and ``worsens`` is not null (both axes are
independently material — see consequence.py's own module docstring on why
``state`` and ``worsens`` are two fields, never folded into one), must carry
its own ``worsens`` boolean matching the frozen one exactly. This is the
same shape ``source``/``as_of`` already impose on ``public_fact`` — a small
piece of structured metadata riding alongside the prose, checked exactly,
rather than an attempt to read the prose itself. Reading the prose was
considered and rejected for a second, independent reason:
``question_surface._assert_no_internal_leak`` (question_surface.py:275)
already prohibits echoing an internal snake_case token like
``already_over`` into user-facing text, so a check that required the word
"worsens" or "already_over" to literally appear in the claim would fight an
existing rule instead of agreeing with it.

Design decision — a user statement promoted to public_fact (case 8)
------------------------------------------------------------------------
``user_statements`` is a caller-supplied collection of exact strings (see
the API section above). A ``public_fact`` claim is rejected when its text,
whitespace-collapsed and case-folded, equals one of them — catching the
mechanical failure the issue names: an agent copies the user's own words
and mislabels them with a citation tag they never earned. What this does
*not* catch is a paraphrase of a user statement re-offered as
``public_fact``; that is the same general NLI/factuality scoring the issue
defers, and this module does not attempt it.

``user_statements`` is deliberately **required**, with no default. Passing
``()`` means "this caller captured nothing", which is a real and legal
answer — but it has to be said. A default would have made forgetting the
argument and declaring it empty the same keystroke, and the two are not the
same fact: the first silently disables case 8 for every answer the
integration ever validates, while every test in this module keeps passing
because tests pass the argument explicitly. That is the shape of green
suite this repo has shipped bugs behind before. When #479's decision
context lands, its captured `reason`/`why_now` are what belongs here.

What this module does not do
--------------------------------
- It does not verify that a non-numeric ``engine_fact`` claim's prose is
  semantically consistent with its anchor's resolved value (for example,
  that a claim anchored at a ``state: "unjudged"`` field does not go on to
  describe the rule as settled either way). The anchor is proven to resolve
  to the real frozen value, but nothing here reads the sentence around it.
  Checking that mechanically, for free text, in general, is exactly the
  NLI/factuality scoring #414 defers; a regex narrowly fitted to this one
  case would be the same false confidence docs/maintainer-guide.md's "a checker that stays
  green under its own mutation is not evidence" warns against.
- It is wired into review.py (#479 Wave B, the single integration owner
  named in issue #414's Wave allocation comment): ``cmd_consider`` calls
  ``validate_agent_case`` once ``consequence``/``rule_collisions`` are
  frozen and before the row is built, appended, or emitted, passing the
  supplied ``--decision-context``'s exact ``reason``/``why_now`` as
  ``user_statements`` (``()`` when no context was supplied). A rejection
  there raises ``ReviewError`` and nothing is stored or returned for that
  attempt. Round-trip/idempotency for a *valid* payload and
  read-compatibility for a legacy row recorded before this gate existed are
  properties of that integration, proved in ``tests/test_consider.py``, not
  of this pure function.
"""
from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping

import conditions


class AnswerProvenanceError(ValueError):
    """Raised when an ``--agent-case`` answer fails semantic provenance
    validation. A ``ValueError`` subclass, matching every other hand-rolled
    validator in this engine (``ConditionError``, ``PriceFeedError``,
    ``ReviewError``, ...) so a caller can catch it the same uniform way."""


# Mirrors review.AGENT_CASE_PROVENANCE (review.py:105) exactly. Restated, not
# imported — see the module docstring's "provenance vocabulary" section for
# why an import would be circular, and tests/test_answer_provenance.py for
# the cross-check that keeps the two from silently drifting apart.
PROVENANCE = ("engine_fact", "public_fact", "agent_judgment")

_SIDES = ("for", "against")

# The three frozen inputs an anchor may address. Deliberately not every key
# `cmd_consider` freezes (`premise`, `decision`, ...) -- a claim about the
# hypothetical trade's own shape or about resolution state is not a "record"
# claim in the sense this module checks; see the anchor-form design note.
_ANCHOR_ROOTS = ("basis", "consequence", "rule_collisions")

# A list index segment: no sign, no leading zero (so "01" is refused rather
# than silently read as 1 -- an anchor is meant to be copied verbatim from
# the frozen JSON's own indices, never hand-adjusted).
_INDEX_RE = re.compile(r"^(0|[1-9][0-9]*)$")

# Half a display unit: half a percentage point, half a dollar, half a
# day/share count. Tight enough to catch a materially different number
# (34% cited as 40%) while tolerant of the rounding any hand-written prose
# claim naturally does (0.3423419... written as "34%"). See the module
# docstring's "number extraction and comparison" section.
_DISPLAY_TOLERANCE = 0.5

# A frozen numeric value is treated as a fraction in need of x100 percent
# scaling only when its own magnitude sits in this range -- see the same
# docstring section for why this reads the value rather than the field name.
_FRACTION_MAGNITUDE = 1.0 + 1e-9

_JUDGMENT_FIELDS = frozenset({"claim", "provenance"})
_PUBLIC_FACT_REQUIRED = frozenset({"claim", "provenance", "source", "as_of"})
_ENGINE_FACT_ALLOWED = frozenset({"claim", "provenance", "anchor", "worsens"})

# Sentinel distinct from any real frozen value (including None, which this
# module deliberately treats as "nothing there" -- see resolve_anchor).
# Public alongside `resolve_anchor` and `build_record`: evaluation_challenge
# resolves the anchors it hands the agent through this module's own walk
# rather than a second one written there, so an anchor the challenge offers
# is always an anchor this file will accept (#479 Wave B cut 2).
UNRESOLVED = object()


# ─────────────────────────── anchor resolution ───────────────────────────

def build_record(basis, consequence, rule_collisions):
    """The single bundle an anchor is resolved against. `rule_collisions`
    (a list, in `tracking`'s own order -- see consequence.rule_collision)
    is re-keyed by its own `rule_id` so an anchor addresses a rule by the
    identity the agent already has, not by a list position that shifts
    when a rule is muted. A row with no rule_id (never produced by
    problems.py in practice; the schema allows null defensively) is simply
    not addressable by anchor."""
    by_rule = {}
    if rule_collisions:
        if not isinstance(rule_collisions, (list, tuple)):
            raise AnswerProvenanceError("rule_collisions must be a list")
        for row in rule_collisions:
            if isinstance(row, Mapping) and row.get("rule_id"):
                by_rule[row["rule_id"]] = row
    return {"basis": basis, "consequence": consequence, "rule_collisions": by_rule}


def resolve_anchor(record, anchor):
    """Walk a dot-separated path into `record`. Returns `UNRESOLVED` unless
    the path exists end-to-end and bottoms out at one scalar fact (not a
    container, not null) -- see the module docstring's anchor-form design
    note for why a container must not resolve."""
    if not isinstance(anchor, str) or not anchor.strip():
        return UNRESOLVED
    parts = anchor.split(".")
    if parts[0] not in _ANCHOR_ROOTS:
        return UNRESOLVED
    node = record
    for part in parts:
        if isinstance(node, Mapping):
            if part not in node:
                return UNRESOLVED
            node = node[part]
        elif isinstance(node, list):
            if not _INDEX_RE.match(part) or int(part) >= len(node):
                return UNRESOLVED
            node = node[int(part)]
        else:
            return UNRESOLVED
    if node is None or isinstance(node, (dict, list)):
        return UNRESOLVED
    return node


# ─────────────────────────── number matching ───────────────────────────

def _numeric_claim_matches(value, text):
    """True when `text` (via conditions.numbers_in) quotes a number that
    equals `value` -- at `value`'s own scale, and at x100 percent scale
    when `value` is fraction-shaped. See the module docstring's number
    extraction and comparison design note."""
    quoted = conditions.numbers_in(text)
    if not quoted:
        return False
    magnitude = abs(float(value))
    candidates = {magnitude}
    if magnitude <= _FRACTION_MAGNITUDE:
        candidates.add(magnitude * 100)
    return any(abs(q - c) <= _DISPLAY_TOLERANCE for q in quoted for c in candidates)


# ─────────────────────────── user-statement matching ───────────────────────────

def _normalize_statement(text):
    return " ".join(str(text or "").split()).strip().casefold()


def _restates_a_user_statement(text, user_statements):
    normalized = _normalize_statement(text)
    return any(normalized == _normalize_statement(stated) for stated in (user_statements or ()))


# ─────────────────────────── per-claim checks ───────────────────────────

def _check_engine_fact_claim(claim, label, record):
    extra = set(claim) - _ENGINE_FACT_ALLOWED
    if extra:
        raise AnswerProvenanceError(
            f"{label} (engine_fact) carries fields it must not: {sorted(extra)}")
    anchor = claim.get("anchor")
    if not isinstance(anchor, str) or not anchor.strip():
        raise AnswerProvenanceError(
            f"{label} is labelled engine_fact but carries no record anchor")
    resolved = resolve_anchor(record, anchor)
    if resolved is UNRESOLVED:
        raise AnswerProvenanceError(
            f"{label}.anchor {anchor!r} does not resolve to the frozen basis, consequence, "
            "or rule_collisions")

    if isinstance(resolved, bool):
        pass  # a bare boolean fact: nothing to number-match (see docstring)
    elif isinstance(resolved, (int, float)):
        if not _numeric_claim_matches(resolved, claim["claim"]):
            raise AnswerProvenanceError(
                f"{label} quotes a number that does not match the frozen value at {anchor!r}")

    # Case 7: a claim describing a material already_over collision must
    # carry its own worsens direction, matching the frozen one exactly.
    parts = anchor.split(".")
    row = None
    if len(parts) >= 2 and parts[0] == "rule_collisions":
        row = record["rule_collisions"].get(parts[1])
    material = (row is not None and parts[-1] in ("state", "worsens")
               and row.get("state") == "already_over" and row.get("worsens") is not None)
    if material:
        asserted = claim.get("worsens")
        if not isinstance(asserted, bool):
            raise AnswerProvenanceError(
                f"{label} describes an already-over rule but omits the worsens direction")
        if asserted != row["worsens"]:
            raise AnswerProvenanceError(
                f"{label}.worsens is reversed against the frozen direction at {anchor!r}")
    elif "worsens" in claim:
        raise AnswerProvenanceError(
            f"{label} carries worsens outside a material already_over anchor")

    return anchor, resolved


def _check_public_fact_claim(claim, label, user_statements):
    missing = _PUBLIC_FACT_REQUIRED - set(claim)
    if missing:
        raise AnswerProvenanceError(
            f"{label} is labelled public_fact but is missing {', '.join(sorted(missing))}")
    extra = set(claim) - _PUBLIC_FACT_REQUIRED
    if extra:
        raise AnswerProvenanceError(
            f"{label} (public_fact) carries fields it must not: {sorted(extra)}")
    # .get(), not [] -- defense in depth if the `missing` gate above is ever
    # weakened by a future edit, this degrades to a clean AnswerProvenanceError
    # naming the field rather than an uncaught KeyError (exactly the failure
    # the mutation dance surfaced for this check; see the module's mutation
    # table entry 5).
    source = claim.get("source")
    if not isinstance(source, str) or not source.strip():
        raise AnswerProvenanceError(f"{label}.source must be non-empty text")
    as_of = claim.get("as_of")
    if not isinstance(as_of, str):
        raise AnswerProvenanceError(f"{label}.as_of must be an ISO date (YYYY-MM-DD)")
    try:
        dt.date.fromisoformat(as_of.strip())
    except ValueError:
        raise AnswerProvenanceError(f"{label}.as_of is not an ISO date: {as_of!r}")
    if _restates_a_user_statement(claim["claim"], user_statements):
        raise AnswerProvenanceError(
            f"{label} restates what the user said as public_fact; a user statement may only be "
            "described as what the user stated, never promoted to an external fact")


def _check_agent_judgment_claim(claim, label):
    extra = set(claim) - _JUDGMENT_FIELDS
    if extra:
        raise AnswerProvenanceError(
            f"{label} (agent_judgment) carries fields it must not: {sorted(extra)}")


def _check_claim(claim, side, index, record, user_statements):
    """Dispatch one claim to its provenance-specific checker. Returns
    `(anchor, resolved_value)` for an engine_fact claim (feeding the
    cross-claim disclosure-coverage check below), or None otherwise."""
    label = f"agent_case.{side}[{index}]"
    if not isinstance(claim, dict):
        raise AnswerProvenanceError(f"{label} must be an object")
    text = claim.get("claim")
    if not isinstance(text, str) or not text.strip():
        raise AnswerProvenanceError(f"{label}.claim must be non-empty text")
    provenance = claim.get("provenance")
    # Case 1: unlabeled (provenance missing/None) and multiply-labelled
    # (provenance supplied as a list rather than one of the three strings)
    # both fail this single membership test -- a list is never `in` a tuple
    # of strings, so no separate "is it a list" branch is needed.
    if provenance not in PROVENANCE:
        raise AnswerProvenanceError(
            f"{label} carries no single valid provenance label; provenance must be exactly "
            "one of " + ", ".join(PROVENANCE))
    if provenance == "engine_fact":
        return _check_engine_fact_claim(claim, label, record)
    if provenance == "public_fact":
        _check_public_fact_claim(claim, label, user_statements)
        return None
    _check_agent_judgment_claim(claim, label)
    return None


# ─────────────────────────── cross-claim checks ───────────────────────────

# The collision states a case must actually cite. Deliberately not every
# state `references/trade-consequence.md` requires an *answer* to name:
# `unjudged`/`unmapped` mean "this rule could not be evaluated", which the
# answer owes the user in prose (evaluation_challenge's `must_state` carries
# them for exactly that reason) but which does not belong on either side of a
# case for or against the trade. A book with eight behavioral rules would
# otherwise need eight claims saying nothing was measured, and an answer
# padded to satisfy a checker is the failure mode `docs/eval-design.md`
# already names. `would_breach` and `already_over` are the two where silence
# reads as approval, so they are the two that are enforced.
_COVERED_STATES = ("would_breach", "already_over")


def required_coverage(basis, consequence, rule_collisions=()):
    """Every fact this evaluation's case must cite, as coverage paths.

    The single declaration of the question "what may an answer not leave
    out". `validate_agent_case` refuses a submission that misses one, and
    `evaluation_challenge.build_challenge` shows the same list to the agent
    as part of what the answer owes — one function, read twice, so the
    surface that states the obligation and the gate that enforces it cannot
    drift (#479 Wave B cut 2). Deriving it in both places is precisely the
    hand-mirrored surface docs/maintainer-guide.md forbids.

    Each entry is ``{"path", "owes", "key"}``. `path` is matched by prefix:
    covered when some engine_fact claim's anchor equals it or sits under it,
    so `basis` accepts any `basis.*` citation and
    `rule_collisions.<id>` accepts either `.state` or `.worsens`. A path is
    therefore a coverage target, not necessarily a resolvable anchor of its
    own -- the containers here deliberately do not resolve. Where two paths
    nest, an anchor pays the narrower one only (`_paid_path`).
    """
    out = []
    for index, key in enumerate(consequence.get("disclosures") or ()):
        out.append({"path": f"consequence.disclosures.{index}",
                    "owes": "disclosure", "key": key})

    stale_days = basis.get("stale_days")
    stale = (isinstance(stale_days, (int, float)) and not isinstance(stale_days, bool)
            and stale_days > 0)
    # `!=`, not a membership test: `unverified` and the replay-only
    # `declared_partial` (#549) both mean "not a declared-complete book", and
    # so would any value a future basis source introduces. Only an
    # affirmatively complete book is exempt.
    incomplete = basis.get("completeness") != "declared_complete"
    if stale or incomplete:
        out.append({"path": "basis", "owes": "basis",
                    "key": "stale_and_unverified" if (stale and incomplete)
                           else ("stale" if stale else "unverified")})

    # #618. Which market session valued this book. Enforced rather than only
    # stated, on #479 Wave B's own rule that what the agent is told it owes and
    # what a case is refused for dropping are one list: since #611 every weight
    # in the answer is a share of a current close, so an answer that never says
    # which close leaves the user unable to attribute a number that moved.
    #
    # Its own entry rather than a widened `basis` one. The `basis` entry is
    # satisfiable by any `basis.*` citation, so folding this into it would make
    # a claim about `basis.source` discharge the price-day obligation, which is
    # an obligation nothing enforces — the exact failure the two-directional
    # test exists to catch.
    observations = basis.get("price_observations")
    if isinstance(observations, Mapping) and isinstance(observations.get("as_of"), str) \
            and observations["as_of"]:
        out.append({"path": "basis.price_observations", "owes": "price_basis",
                    "key": "price_observed"})

    for row in rule_collisions or ():
        if not isinstance(row, Mapping) or row.get("state") not in _COVERED_STATES:
            continue
        # A row with no rule_id is not addressable by anchor at all, so it
        # cannot be required -- requiring an uncitable fact would make every
        # case on that book unsubmittable. The challenge still states it.
        if not row.get("rule_id"):
            continue
        out.append({"path": f"rule_collisions.{row['rule_id']}",
                    "owes": "rule_collision", "key": row["state"]})
    return tuple(out)


def _under(anchor, path):
    return anchor == path or anchor.startswith(path + ".")


def _paid_path(anchor, paths):
    """The *most specific* required path this anchor discharges, or None.

    Prefix matching alone made a nested obligation pay for its own parent as
    well: `basis` is matched by any `basis.*` anchor, so once
    `basis.price_observations` became a required path of its own (#618), a
    single claim citing the price day covered the staleness obligation too and
    a case that never mentioned staleness was accepted. The whole suite stayed
    green, because until then no two required paths ever nested.

    One claim pays one debt: the longest matching path wins, and the broader
    obligation stays open until something cites it that is not already
    answering a narrower one. Behaviour is unchanged wherever the required
    paths are disjoint, which is every case that existed before #618 --
    `consequence.disclosures.<i>` and `rule_collisions.<id>` never nest.
    """
    candidates = [path for path in paths if _under(anchor, path)]
    return max(candidates, key=len) if candidates else None


def _check_required_coverage(resolved_anchors, required):
    """Case 6: nothing on `required_coverage`'s list may be silently dropped
    from the answer. "Covered" means at least one engine_fact claim anchors
    at or under the path -- the same bar every other engine_fact claim
    already has to clear, not a new, softer one -- and anchors under a
    narrower required path pay that one instead (see `_paid_path`)."""
    paths = [entry["path"] for entry in required]
    paid = {_paid_path(anchor, paths) for anchor, _ in resolved_anchors}
    missing = [entry for entry in required if entry["path"] not in paid]
    if missing:
        raise AnswerProvenanceError(
            "agent_case leaves uncovered what this evaluation owes: "
            + "; ".join(f"{entry['owes']} {entry['key']} "
                        f"(anchor an engine_fact under {entry['path']})"
                        for entry in missing))


# ─────────────────────────── public entry point ───────────────────────────

def validate_agent_case(agent_case, *, basis, consequence, rule_collisions=(), user_statements):
    """Semantically validate a two-sided `--agent-case` payload against the
    frozen engine result it claims to describe. Returns None when the
    answer is provenance-clean; raises AnswerProvenanceError, naming the
    exact claim and rule, otherwise. See the module docstring for the full
    API contract, the design decisions behind each check, and what this
    function deliberately does not attempt."""
    if not isinstance(agent_case, dict) or set(agent_case) != set(_SIDES):
        raise AnswerProvenanceError("agent_case must be an object with exactly 'for' and 'against'")
    if not isinstance(basis, Mapping):
        raise AnswerProvenanceError("basis must be an object")
    if not isinstance(consequence, Mapping):
        raise AnswerProvenanceError("consequence must be an object")

    record = build_record(basis, consequence, rule_collisions)

    resolved_anchors = []
    for side in _SIDES:
        claims = agent_case[side]
        # Case 5: an empty side is refused -- the existing structural check
        # (review._validate_agent_case) accepts a present-but-empty list,
        # which is exactly the gap this module closes.
        if not isinstance(claims, list) or not claims:
            raise AnswerProvenanceError(f"agent_case.{side} must be a non-empty list of claims")
        for index, claim in enumerate(claims):
            resolved = _check_claim(claim, side, index, record, user_statements)
            if resolved is not None:
                resolved_anchors.append(resolved)

    _check_required_coverage(resolved_anchors,
                             required_coverage(basis, consequence, rule_collisions))
