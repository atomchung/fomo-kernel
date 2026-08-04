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

This is docs/maintainer-guide.md's "Honesty decisions belong in code" applied
freeform surface. The card already works this way: ``build_honesty_ledger()``
decides which limitations a card must disclose, and ``SKILL.md`` does not
carry a growing list of "if field exists, add a sentence" instructions.
``consider`` is the product's other delivery surface and had no equivalent —
its disclosure obligations were instructions an agent had to remember, which
is the weakest enforcement tier this repository recognizes
(docs/development-guide.md section 4).

The relationship to SKILL.md's answer shape
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
``agent_case`` row in docs/maintainer-guide.md already states about itself: the seed
identifies the subject being evaluated — this trade, this book, this
stated reason — and a presentation obligation is not part of that subject.
Because the block is derived from seeded inputs, adding it would not even
change which calls converge; it would only move every existing id.

The rule-effect split (#579)
-----------------------------
``must_state``'s rule-collision entry used to state the *collision state* —
``already_over`` — as the fact the answer owed. That is where the book
stands, and the user asked what this trade does. On 2026-08-02 an owner
asked about a sell taking a position from 80% to 75% against a self-authored
20% cap and was told the trade violated the rule; the payload said
``already_over`` with ``worsens: false`` and the composition happened at
prose time, which is exactly where a truth-critical distinction cannot live.

So the stated fact is now ``consequence``'s deterministic ``rule_effect``,
and the state and its direction stay on the entry one level down, as machine
diagnostics a QA run reconciles against. Beside it, ``rule_effects`` is the
product-safe projection: per rule, the effect, the line it was judged
against, whose line that is, and the meanings the answer must and must not
attach to it. Those two halves — the complete diagnostics on the stored row,
the bounded product meaning in this block — are #713's repair step 4, and
repairing either alone re-opens the other.

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
import consequence as consequence_engine


# Presentation order for `must_state`. The basis comes first because every
# number after it is measured against that book, and the disclosures come
# last because they qualify what precedes them. `price_basis` sits second for
# the same dependency reason: which book, then which market session that book
# was valued at, then every number measured from the two. An agent is free to
# compose these into whatever prose the moment calls for; the order is the
# order the facts depend on each other, not a script.
TOPICS = ("basis", "price_basis", "position", "concentration", "cash",
          "rule_collision", "disclosure", "excluded_holding")

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

# A collision that needs saying, keyed on the *transaction effect* rather
# than on the book's absolute state (#579). `compliant` is the only effect
# that may pass in silence — the true non-event. Every other one, including
# the two that mean "not evaluated", must be named rather than absent: an
# unevaluated rule presented as no issue tells the user something the engine
# never checked (references/trade-consequence.md, "Reading a rule
# collision").
#
# The old key was `state`, and `clear` was the silent one. That silenced
# `resolved_existing_breach` too: a trade that takes a self-authored line
# from broken back to held said nothing at all, because the state vocabulary
# cannot tell it apart from a book that was never over. Reading the effect
# is what makes the difference expressible.
_SILENT_EFFECT = "compliant"

# The pre-#579 rule, kept for a row recorded before `rule_effect` existed.
# Such a row never reaches here from `cmd_consider`, which always computes a
# fresh collision; this is the defensive half of #579's compatibility clause
# and it exists so the floor and the gate stay one list on an old row too --
# `answer_provenance._LEGACY_COVERED_STATES` is its counterpart, and a
# challenge that silently dropped what that gate still requires would make
# every case on such a book unsubmittable.
_LEGACY_SPEAKING_STATES = ("would_breach", "already_over", "unjudged", "unmapped")

# What the answer must and must not convey about each effect — semantic
# slots, never wording, in the same spirit as `must_state` being facts rather
# than sentences. This is the product-safe projection #579 section 3 asks
# for: the minimum meaning the agent may realize, derived from the
# deterministic effect, and the meanings it may not attach to it.
#
# It is a fixed six-row table over one route's rule effect, deliberately not
# a general response-plan vocabulary (#713's scope guard). The negative half
# is the load-bearing one: PR #608 stated the same rule as prose and an
# improving trade was still described as a breach, so what an answer may
# *not* say is data here rather than a paragraph to remember.
_SLOT_CROSSED = "crossed_by_this_trade"
_SLOT_OVER_BEFORE = "over_before_this_trade"
_SLOT_TOWARD = "moved_toward_the_line"
_SLOT_FURTHER = "moved_further_over"
_SLOT_UNCHANGED = "unchanged_by_this_trade"
_SLOT_OVER_AFTER = "over_after"
_SLOT_UNDER_AFTER = "under_after"
_SLOT_NOT_EVALUATED = "not_evaluated"
_SLOT_RULE_HELD = "rule_held"

RULE_EFFECT_SLOTS = (_SLOT_CROSSED, _SLOT_OVER_BEFORE, _SLOT_TOWARD, _SLOT_FURTHER,
                     _SLOT_UNCHANGED, _SLOT_OVER_AFTER, _SLOT_UNDER_AFTER,
                     _SLOT_NOT_EVALUATED, _SLOT_RULE_HELD)

_MUST_CONVEY = {
    "new_breach": (_SLOT_CROSSED, _SLOT_OVER_AFTER),
    "worsened_existing_breach": (_SLOT_OVER_BEFORE, _SLOT_FURTHER, _SLOT_OVER_AFTER),
    "improved_but_still_over": (_SLOT_OVER_BEFORE, _SLOT_TOWARD, _SLOT_OVER_AFTER),
    "unchanged_existing_breach": (_SLOT_OVER_BEFORE, _SLOT_UNCHANGED, _SLOT_OVER_AFTER),
    "resolved_existing_breach": (_SLOT_OVER_BEFORE, _SLOT_UNDER_AFTER),
    "unjudged": (_SLOT_NOT_EVALUATED,),
    "unmapped": (_SLOT_NOT_EVALUATED,),
}

_MUST_NOT_CONVEY = {
    "new_breach": (_SLOT_OVER_BEFORE, _SLOT_TOWARD),
    "worsened_existing_breach": (_SLOT_CROSSED, _SLOT_TOWARD),
    "improved_but_still_over": (_SLOT_CROSSED, _SLOT_FURTHER),
    "unchanged_existing_breach": (_SLOT_CROSSED, _SLOT_FURTHER, _SLOT_TOWARD),
    "resolved_existing_breach": (_SLOT_CROSSED, _SLOT_OVER_AFTER),
    "unjudged": (_SLOT_RULE_HELD,),
    "unmapped": (_SLOT_RULE_HELD,),
}


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


def _price_basis_entries(record, basis):
    """Which market session valued this book (#618).

    ``basis.as_of`` is the last row of the *record* — the last trade or the
    snapshot anchor. It says nothing about when the market was observed, and
    since #611 every weight below is a share of a current close rather than of
    cost. Without this the same premise re-asked returns different numbers with
    no attributable cause, which is the #429-class question that comes back as
    a dogfood finding.

    Present exactly when the frozen basis carries the observations — an
    unpriced run has none, and the `cost_basis` disclosure is what speaks for
    that answer instead. Nothing here manufactures a date.

    The frame summary is stated unconditionally and a per-ticker date is added
    only where it differs from that summary. This is #583 §2's rule made
    brief: naming every ticker on a same-day frame would pad the floor with
    one entry per holding saying the same thing, while a frame date alone lets
    one fresh instrument stand in for a stale one. The exceptions are exactly
    the instruments the summary does not describe.

    A ticker whose own name contains a dot (``2330.TW``) keeps its date and
    loses only its anchor, like every other unaddressable fact here — so its
    identity rides in `detail`, which is the only place an answer could read
    it back from once the path is gone.
    """
    observations = basis.get("price_observations")
    if not isinstance(observations, Mapping):
        return []
    as_of = observations.get("as_of")
    if not isinstance(as_of, str) or not as_of:
        return []
    out = [_entry(record, "price_basis", "basis.price_observations.as_of", as_of)]
    by_ticker = observations.get("by_ticker")
    if isinstance(by_ticker, Mapping):
        for ticker in sorted(by_ticker):
            day = by_ticker[ticker]
            if isinstance(day, str) and day and day != as_of:
                out.append(_entry(record, "price_basis",
                                  f"basis.price_observations.by_ticker.{ticker}",
                                  day, detail={"ticker": ticker}))
    return out


def _position_entries(record, premise, consequence):
    """What this trade does to the position itself — the question the user
    actually asked. `before` only when the ticker is already held: a weight
    of zero for a name they do not own is arithmetic, not a fact worth a
    sentence.

    The premise's own price leads this topic when it was defaulted (#777,
    owner ruling 2026-08-02): `price_basis: "observed"` means the caller
    stated no price and consequence.validate_premise filled it from the
    engine's own observed close rather than asking for one. "Priced at the
    last close" is the fact the owner ruling requires the user see -- an
    unstated assumption silently promoted to a market fact is exactly what
    the *required* field existed to prevent, so the one thing that keeps the
    default honest is saying, every time it fires, that it fired. No anchor:
    `premise` is not one of `answer_provenance._ANCHOR_ROOTS`, so this fact
    is stated but not citable by path, like every other unaddressable entry
    this module already returns (see the module docstring's anchor
    guarantee). Silent when the user supplied their own price -- that is the
    ordinary case and states nothing new about it here."""
    ticker = premise.get("ticker") if isinstance(premise, Mapping) else None
    if not ticker:
        return []
    out = []
    if isinstance(premise, Mapping) and premise.get("price_basis") == "observed":
        out.append(_entry(record, "position", None, premise.get("price"),
                          detail={"price_basis": "observed"}))
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


def _measured(field, after):
    """Whether a concentration reading is a measurement at all.

    `top3` always is: it is pure weight and needs no classification.

    The two driver readings are the trap. On a book where nothing is
    classified, `trade_recap.dim_diversify` builds `classified_sec` empty,
    so `max_sector` comes back None — but `max_sector_pct` falls out of a
    `.get(None, 0)` default and `ai_pct` sums to `0.0` over zero AI weights.
    Both are **0, not null**. A zero there means "nobody looked", and an
    answer that states it as an owed fact tells the user their book has no
    AI exposure and no sector concentration, which the engine never
    measured. That is the same thing `unjudged` exists to prevent one
    surface over, and the reason this function reads a sibling field
    instead of the value's own type.

    `max_sector` being present is the signal that at least one position
    carries a real sector, so a zero beside it is a real zero. A non-zero
    `ai_pct` is self-evidently measured and stands on its own — a driver map
    that flags AI on a position it leaves sector-unclassified would
    otherwise have its one real reading suppressed.
    """
    value = after.get(field)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    if field == "top3":
        return True
    if field == "max_sector_pct":
        return after.get("max_sector") is not None
    return bool(value) or after.get("max_sector") is not None


def _concentration_entries(record, consequence):
    """Concentration and driver overlap after the trade — every reading that
    is actually a measurement (see `_measured`). The two triggers are stated
    only when they are on: a flag that is false is the absence of a fact, and
    listing it would pad the floor with non-events."""
    after = consequence.get("after") or {}
    out = [_entry(record, "concentration", f"consequence.after.{field}", after[field])
           for field in ("top3", "ai_pct", "max_sector_pct") if _measured(field, after)]
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


def _speaking_effect(row):
    """The transaction effect this row owes the user, or None when the row is
    not a collision that needs saying.

    `rule_effect` is the value read, never `state`: the absolute state cannot
    tell an improving trade from a worsening one, nor a resolved line from
    one that was never crossed (consequence.RULE_EFFECTS). A row recorded
    before that field existed has no effect to return, so this says so and
    the one caller that must still speak about such a row —
    `_rule_collision_entries` — falls back explicitly rather than having a
    missing field quietly reinterpreted here."""
    if not isinstance(row, Mapping):
        return None
    effect = row.get("rule_effect")
    if effect is None or effect == _SILENT_EFFECT:
        return None
    disagreement = consequence_engine.effect_disagrees_with_state(row.get("state"), effect)
    if disagreement:
        # #579's compatibility clause. A row whose two vocabularies contradict
        # each other cannot be projected as either, so the answer is refused
        # rather than composed from whichever field was read first.
        raise ValueError(f"rule_collisions row {row.get('rule_id')!r}: {disagreement}")
    return effect


def _rule_collision_entries(record, rule_collisions):
    """Every rule of the user's own this trade collides with, plus every one
    that could not be judged. The rule's own text rides along in `detail` so
    the answer can quote what they wrote rather than paraphrase it.

    The stated `value` is the **transaction effect**, not the book's absolute
    state (#579). `already_over` as the headline fact is what owner-live
    acceptance failed on: a sell taking a position from 80% to 75% against a
    20% cap owes the user "this reduces it and it is still above your line",
    and the state alone says only "over", which reads as an accusation. The
    absolute state and its `worsens` direction stay on the entry as machine
    diagnostics — they are what a QA run reconciles against — but they are no
    longer the fact the answer is told to state.

    A row with no `rule_id` cannot be addressed by anchor at all — the
    schema allows the null defensively and problems.py does not produce one
    — so it is stated without an anchor rather than dropped. Dropping it
    would turn a defensive null into a silently unmentioned rule."""
    out = []
    for row in rule_collisions or ():
        if not isinstance(row, Mapping):
            continue
        effect = _speaking_effect(row)
        if effect is not None:
            field, value = "rule_effect", effect
        elif row.get("rule_effect") is None and row.get("state") in _LEGACY_SPEAKING_STATES:
            field, value = "state", row["state"]
        else:
            continue
        detail = {"rule_id": row.get("rule_id"), "text": row.get("text"),
                  "state": row.get("state"), "worsens": row.get("worsens")}
        out.append(_entry(record, "rule_collision",
                          f"rule_collisions.{row.get('rule_id')}.{field}",
                          value, detail=detail))
    return out


def _rule_effect_projection(rule_collisions):
    """The product-safe projection of every speaking collision (#579 section
    3, #713 repair step 4): what the answer must convey about this rule and
    what it may not, derived from the deterministic effect.

    Deliberately carries no `state`, no `worsens`, and no retrieval or
    recovery chronology. Those remain complete on the evaluation row, which
    is what QA and a later replay read; this block is the half a user-facing
    answer is built from, and the separation is the point — the repair for a
    wrong rule meaning and the repair for internal diagnostics reaching the
    user are one cut on one surface, not two competing ones.

    `limit` is the line the effect was classified against and `limit_source`
    says whose line it is. Both are here because the correct sentence names
    the number ("still above your 20% rule") and because a threshold this
    engine picked must never be handed back to the user as a rule they
    wrote."""
    out = []
    for row in rule_collisions or ():
        effect = _speaking_effect(row)
        if effect is None:
            continue  # compliant, or a legacy row with no deterministic effect
        entry = {"rule_id": row.get("rule_id"), "text": row.get("text"),
                 "effect": effect,
                 "must_convey": list(_MUST_CONVEY[effect]),
                 "must_not_convey": list(_MUST_NOT_CONVEY[effect])}
        limit = row.get("limit")
        if isinstance(limit, (int, float)) and not isinstance(limit, bool):
            entry["limit"] = limit
            entry["limit_source"] = row.get("limit_source")
        out.append(entry)
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
    ``rule_effects``
        The product-safe projection of what this trade does to each of the
        user's own rules: the deterministic effect, the line it was judged
        against, and the meanings the answer must and must not attach to it.
        Empty when no rule speaks.
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
    must_state.extend(_price_basis_entries(record, basis))
    must_state.extend(_position_entries(record, premise, consequence))
    must_state.extend(_concentration_entries(record, consequence))
    must_state.extend(_cash_entries(record, consequence))
    must_state.extend(_rule_collision_entries(record, rule_collisions))
    must_state.extend(_disclosure_entries(record, consequence))
    must_state.extend(_excluded_holding_entries(record, consequence))

    return {
        "must_state": must_state,
        "rule_effects": _rule_effect_projection(rule_collisions),
        "quote_verbatim": _quote_verbatim(context),
        "unchecked": _unchecked(context),
        "case_required": dict(CASE_REQUIRED),
        "required_coverage": [dict(entry) for entry in
                              answer_provenance.required_coverage(
                                  basis, consequence, rule_collisions)],
    }
