#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evaluation_challenge.py — what one ``consider`` answer owes the user
(issue #479's "Visible challenge contract", Wave B second cut).

Wave B's first cut wired ``answer_provenance.validate_agent_case`` into
``cmd_consider``: a *fabricated* case is now refused before it is stored or
returned. That closed the "the agent may not lie" half. It left the other
half entirely open — nothing said what a truthful answer has to *contain*,
so an agent could satisfy every gate by saying almost nothing at all. The
obligations existed, but only as prose spread across
``references/trade-consequence.md`` ("name what nobody checked, every
time"), ``SKILL.md`` rule 3 and ``freeform-answers.md``, to be re-derived by
hand on every call from a payload with roughly forty fields in it.

This module computes them instead. ``build_challenge`` reads exactly the
values ``cmd_consider`` has already frozen and returns one block naming what
this specific answer must put in front of this specific user: which facts,
in the user's own words which sentences, which of their own rules this trade
collides with, which limitations the numbers carry, and what the engine did
not look at. The block is emitted, never stored — see "Emitted, not stored"
below.

This is CLAUDE.md's "Honesty decisions belong in code" applied to the
freeform surface. The card already works this way: ``build_honesty_ledger()``
decides which limitations a card must disclose, and ``SKILL.md`` does not
carry a growing list of "if field exists, add a sentence" instructions.
``consider`` is the product's other delivery surface and had no equivalent —
its disclosure obligations were instructions an agent had to remember, which
is the weakest enforcement tier this repository recognizes
(docs/development-guide.md section 4).

The relationship to SKILL.md rule 8
------------------------------------
Rule 8 says a freeform answer — explicitly including a ``consider`` call —
is brief: no chart, no artifact, no multi-tool production. The same rule
also says "Brevity bounds what an answer produces, never which facts it
owes". Those two clauses only coexist if something states the floor, and
until now nothing did: an agent economizing on production had no way to tell
which of the forty payload fields were the ones it could not drop.

``must_state`` is that floor, and it is deliberately a list of *facts*, not
of sentences. Several facts belong in one sentence — the basis topic's four
entries are one clause ("computed on your recorded book as of the 20th, nine
days old"), not four bullet points. The block bounds an answer from below
and says nothing about its length; a short answer that carries every entry
is exactly what rule 8 asks for, and a long one that drops a rule collision
still fails.

What is mechanically enforced, and what is not
------------------------------------------------
Only ``required_coverage`` is. It is imported from
``answer_provenance.required_coverage`` rather than derived a second time
here, so the facts a ``--agent-case`` submission is *refused* for dropping
and the facts this block *tells the agent* it owes are one list, read twice,
and cannot drift apart. It is deliberately a subset: it names what a case
must cite, not everything an answer must say, because forcing an
``agent_case`` claim for every cash balance would inflate the answer to
satisfy a checker — the "eval must not pin current wording" failure this
repository has already shipped once.

Everything else here — that the user's exact words are quoted rather than
paraphrased, that the unchecked list is spoken aloud — reaches the user
through the agent, and nothing offline can observe whether it arrived. That
is the same instruction-only footing ``docs/development-guide.md`` section 4
already admits for the recommendation ban and ``freeform-answers.md`` admits
for brevity itself. Stating the obligation as computed data rather than as
remembered prose is the improvement available at this layer; claiming the
delivery is verified offline would not be true. The delivery half is
observed by an owner-live receipt (#488), whose formal ``consider`` route
contract and walkthrough belong to #544 Slice B.

Emitted, not stored
--------------------
``cmd_consider`` puts this block in what it emits and not on the
``trade_evaluations.jsonl`` row. Two reasons, both of them rules this
repository has paid for. It is a pure function of ``premise``/``basis``/
``consequence``/``rule_collisions``/``context``, every one of which is
already frozen on that row, so storing it would be a derived duplicate that
can disagree with its own inputs. And no reader needs the historical
version: ``--resolve`` copies frozen fields forward without consulting it,
and ``_evaluation_reconciliation`` reads dates and quantities. Writing a
field nothing reads back is issue #429's defect, and it is cheaper not to
write it than to discover later that nobody did.

It likewise never enters ``_evaluation_id``'s seed, for the reason the
``agent_case`` row in CLAUDE.md already states about itself: the seed
identifies the subject being evaluated — this trade, this book, this
stated reason — and a presentation obligation is not part of that subject.
Because the block is derived from seeded inputs, adding it would not even
change which calls converge; it would only move every existing id.

The anchor guarantee
---------------------
Every ``anchor`` this module emits has been resolved against the frozen
record before it is handed over, using ``answer_provenance``'s own resolver
rather than a second walk written here. So an anchor the challenge offers is
always an anchor ``validate_agent_case`` will accept: the surface that tells
the agent how to cite a fact and the gate that judges the citation agree by
construction, not by two authors keeping the same rule in mind.

This is load-bearing rather than tidy. Anchors are dot-separated paths and
some real tickers contain a dot (``2330.TW``), so a hand-built
``consequence.after.weights.2330.TW`` reads as ``weights → "2330" → "TW"``
and resolves to nothing. An entry whose anchor does not resolve keeps its
value and loses its ``anchor`` key — the fact is still owed and still
stated, it simply cannot be cited by path — which is why ``anchor`` is
optional on an entry and why dropping the unresolvable ones entirely would
have been the wrong repair.
"""
from __future__ import annotations

from collections.abc import Mapping

import answer_provenance


# Presentation order for `must_state`. The basis comes first because every
# number after it is measured against that book, and the disclosures come
# last because they qualify what precedes them. An agent is free to compose
# these into whatever prose the moment calls for; the order is the order the
# facts depend on each other, not a script.
TOPICS = ("basis", "position", "concentration", "cash", "rule_collision",
          "disclosure", "excluded_holding")

# What `consider` never looked at. The first four are unconditional and are
# lifted verbatim out of references/trade-consequence.md's own sentence
# ("Liquidity, valuation, tax consequences, and whether the position still
# fits this person are all real risks the engine does not measure") — the
# point of this constant is that the list stops being a thing an agent has
# to remember having read. The last two are conditional on a decision
# context existing, below.
#
# Deliberately disjoint from `consequence.DISCLOSURES`. A disclosure is a
# gap in a number the engine did compute; an unchecked item is a risk it
# never went near. Folding them together would let "the weights are on cost"
# and "nobody looked at liquidity" read as the same kind of caveat, and the
# second is the one silence turns into a clean bill of health.
UNCHECKED_ALWAYS = ("liquidity", "valuation", "tax", "position_fit")

# Only with a decision context. `evidence_delta` is #479's "whether the
# supplied input contains an evidence delta or only a price delta, labelled
# as judgment where semantic": the engine cannot tell new information from a
# price move that feels like new information, so it names the question as one
# it did not settle rather than leaving the agent's read of it unlabelled.
UNCHECKED_WITH_CONTEXT = ("evidence_delta",)
# Only when the user actually pointed at something. The engine does not
# fetch, date or believe an evidence reference; it records that one was
# cited (references/trade-consequence.md, "What the user said").
UNCHECKED_WITH_EVIDENCE = ("evidence_refs_unverified",)

# The floor the two-sided case must clear, restated here so the block is
# self-contained for a reader who has only this payload. The enforcement of
# it lives in answer_provenance.validate_agent_case, which refuses an empty
# side; this is the same number said where the agent is reading.
CASE_REQUIRED = {"for": 1, "against": 1}

# A collision that needs saying. `clear` is the only state that may pass in
# silence: every other one, including the two that mean "not evaluated",
# must be named rather than absent — an unevaluated rule presented as no
# issue tells the user something the engine never checked
# (references/trade-consequence.md, "Reading a rule collision").
_SPEAKING_STATES = ("would_breach", "already_over", "unjudged", "unmapped")


def _entry(record, topic, anchor, value, detail=None):
    """One owed fact. `anchor` is dropped, and only the anchor, when it does
    not resolve against the frozen record — see the module docstring's
    anchor guarantee for why an unresolvable path must not be offered and
    must not take the fact down with it."""
    entry = {"topic": topic, "value": value}
    if answer_provenance.resolve_anchor(record, anchor) is not answer_provenance.UNRESOLVED:
        entry["anchor"] = anchor
    if detail is not None:
        entry["detail"] = detail
    return entry


def _basis_entries(record, basis):
    """Which book answered, how current it was, how it was obtained, and its
    exact identity. Unconditional: there is no such thing as a consequence
    with no basis, and a weight quoted without the book it was measured on
    is the same number said about an unknown denominator.

    `completeness` is on this list and not only in `required_coverage`.
    "This came from an unreconciled CSV import rather than a declared
    snapshot" is the limitation a user can act on; `state_version` is the
    identity a QA run compares mechanically. Both are owed, and leaving the
    first to be inferred from the second would be the more useful half going
    unsaid."""
    out = []
    for field in ("source", "as_of", "stale_days", "completeness", "state_version"):
        if field in basis:
            out.append(_entry(record, "basis", f"basis.{field}", basis[field]))
    return out


def _position_entries(record, premise, consequence):
    """What this trade does to the position itself — the question the user
    actually asked. `before` only when the ticker is already held: a weight
    of zero for a name they do not own is arithmetic, not a fact worth a
    sentence."""
    ticker = premise.get("ticker") if isinstance(premise, Mapping) else None
    if not ticker:
        return []
    out = []
    before = (consequence.get("before") or {}).get("weights") or {}
    after = (consequence.get("after") or {}).get("weights") or {}
    if ticker in before:
        out.append(_entry(record, "position", f"consequence.before.weights.{ticker}",
                          before[ticker]))
    if ticker in after:
        out.append(_entry(record, "position", f"consequence.after.weights.{ticker}",
                          after[ticker]))
    delta = (consequence.get("delta") or {}).get("ticker_weight")
    if isinstance(delta, (int, float)) and not isinstance(delta, bool):
        out.append(_entry(record, "position", "consequence.delta.ticker_weight", delta))
    return out


def _concentration_entries(record, consequence):
    """Concentration and driver overlap after the trade. `top3` is always
    computable and always said; `ai_pct`/`max_sector_pct` are null on a book
    with no classification at all, and a null is nothing to state — the
    `unmapped_driver` disclosure is what covers that case. The two triggers
    are stated only when they are on: a flag that is false is the absence of
    a fact, and listing it would pad the floor with non-events."""
    after = consequence.get("after") or {}
    out = []
    for field in ("top3", "ai_pct", "max_sector_pct"):
        value = after.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(_entry(record, "concentration", f"consequence.after.{field}", value))
    for field in ("oversize_triggered", "concentration_triggered"):
        if after.get(field) is True:
            out.append(_entry(record, "concentration", f"consequence.after.{field}", True))
    return out


def _cash_entries(record, consequence):
    """What the trade leaves in cash. Whether that balance can be trusted is
    the separate `cash_unreliable` disclosure, which rides the disclosure
    topic rather than being folded in here."""
    cash = (consequence.get("after") or {}).get("cash") or {}
    out = []
    for field in ("balance", "weight"):
        value = cash.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.append(_entry(record, "cash", f"consequence.after.cash.{field}", value))
    return out


def _rule_collision_entries(record, rule_collisions):
    """Every rule of the user's own this trade collides with, plus every one
    that could not be judged. The rule's own text rides along in `detail` so
    the answer can quote what they wrote rather than paraphrase it, and
    `worsens` rides along because an `already_over` line that this trade
    *improves* reads identically without it (references/trade-consequence.md).

    A row with no `rule_id` cannot be addressed by anchor at all — the
    schema allows the null defensively and problems.py does not produce one
    — so it is stated without an anchor rather than dropped. Dropping it
    would turn a defensive null into a silently unmentioned rule."""
    out = []
    for row in rule_collisions or ():
        if not isinstance(row, Mapping) or row.get("state") not in _SPEAKING_STATES:
            continue
        detail = {"rule_id": row.get("rule_id"), "text": row.get("text"),
                  "worsens": row.get("worsens")}
        out.append(_entry(record, "rule_collision",
                          f"rule_collisions.{row.get('rule_id')}.state",
                          row.get("state"), detail=detail))
    return out


def _disclosure_entries(record, consequence):
    """Every limitation the engine attached to these numbers, addressed by
    its own index so the anchor matches the frozen list position a case must
    cite."""
    return [_entry(record, "disclosure", f"consequence.disclosures.{index}", key)
            for index, key in enumerate(consequence.get("disclosures") or ())]


def _excluded_holding_entries(record, consequence):
    """Which positions are outside the denominator every percentage above was
    measured against (#515). `partial_book` says that something was excluded;
    these say what, and the reference requires them named wherever a derived
    percentage appears — so they are owed facts, not payload colour."""
    out = []
    for index, row in enumerate(consequence.get("excluded_holdings") or ()):
        if not isinstance(row, Mapping) or not row.get("ticker"):
            continue
        out.append(_entry(record, "excluded_holding",
                          f"consequence.excluded_holdings.{index}.ticker",
                          row["ticker"], detail={"reason": row.get("reason")}))
    return out


def _quote_verbatim(context):
    """The user's own sentences, marked as the one part of this answer that
    may not be reworded. Empty on a context-free call, which captured no
    prose to quote.

    Their words are already frozen on the row; what this adds is that they
    are owed *back* to the user in the answer. An answer that silently
    reworded the stated reason into the agent's own phrasing would be
    presenting the agent's reading of the decision as the user's, which is
    the same substitution `validate_agent_case`'s case 8 refuses in the
    other direction."""
    if not isinstance(context, Mapping):
        return []
    out = []
    for field in ("reason", "why_now"):
        text = context.get(field)
        if isinstance(text, str) and text.strip():
            out.append({"field": field, "text": text})
    for index, ref in enumerate(context.get("evidence_refs") or ()):
        if isinstance(ref, str) and ref.strip():
            out.append({"field": f"evidence_refs[{index}]", "text": ref})
    return out


def _unchecked(context):
    keys = list(UNCHECKED_ALWAYS)
    if isinstance(context, Mapping):
        keys.extend(UNCHECKED_WITH_CONTEXT)
        if context.get("evidence_refs"):
            keys.extend(UNCHECKED_WITH_EVIDENCE)
    return keys


def build_challenge(*, premise, basis, consequence, rule_collisions=(), context=None):
    """What this answer owes the user, computed from what the engine froze.

    Pure: every argument is a value ``cmd_consider`` already holds, nothing
    is read from disk, and calling this twice on the same inputs returns the
    same block. Context-free calls get one too — the disclosures, the
    collisions and the unchecked list are owed whether or not the user
    stated a reason; only ``quote_verbatim`` is empty and only
    ``evidence_delta``/``evidence_refs_unverified`` drop out of
    ``unchecked``.

    Keys:

    ``must_state``
        Ordered owed facts, each ``{topic, value}`` plus ``anchor`` when the
        fact is addressable. Facts, not sentences — see the module
        docstring on rule 8.
    ``quote_verbatim``
        The user's own words, to be reproduced rather than summarized.
    ``unchecked``
        What the engine did not look at, this call.
    ``case_required``
        The floor for a two-sided case.
    ``required_coverage``
        The mechanically enforced subset: what a ``--agent-case`` submission
        is refused for leaving out. Imported from ``answer_provenance`` so
        this block and that gate cannot disagree.
    """
    if not isinstance(basis, Mapping):
        raise ValueError("basis must be an object")
    if not isinstance(consequence, Mapping):
        raise ValueError("consequence must be an object")
    record = answer_provenance.build_record(basis, consequence, rule_collisions)

    must_state = []
    must_state.extend(_basis_entries(record, basis))
    must_state.extend(_position_entries(record, premise, consequence))
    must_state.extend(_concentration_entries(record, consequence))
    must_state.extend(_cash_entries(record, consequence))
    must_state.extend(_rule_collision_entries(record, rule_collisions))
    must_state.extend(_disclosure_entries(record, consequence))
    must_state.extend(_excluded_holding_entries(record, consequence))

    return {
        "must_state": must_state,
        "quote_verbatim": _quote_verbatim(context),
        "unchecked": _unchecked(context),
        "case_required": dict(CASE_REQUIRED),
        "required_coverage": [dict(entry) for entry in
                              answer_provenance.required_coverage(
                                  basis, consequence, rule_collisions)],
    }
