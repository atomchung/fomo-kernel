#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evaluation_challenge.py (#479 Wave B, second cut) — offline, no pytest.

Wave B's first cut made a fabricated `--agent-case` unstorable. It said
nothing about what a *truthful* answer has to contain, so an agent could
clear every gate by saying almost nothing. `build_challenge` computes that
missing floor from the values `cmd_consider` has already frozen.

This file proves three things about it, in descending order of how much they
matter:

1. The floor and the gate agree. A case covering exactly what the challenge
   asked for is accepted, and dropping any single entry the challenge named
   is refused. This is the test that fails if the block ever under-reports
   (an obligation the gate enforces but the agent was never told about) or
   over-reports (an obligation nothing enforces). Both directions are live
   failures, not hypotheticals -- see the mutation table below.
2. An anchor the challenge hands over always resolves. A dot-separated path
   cannot address a ticker that itself contains a dot, and the entry has to
   survive that with its fact intact and its anchor gone.
3. Obligations that cannot be mechanically enforced are still stated: the
   user's exact words, and what nobody checked.

Mutation table -- each row was applied to engine/evaluation_challenge.py or
engine/answer_provenance.py, the suite re-run, and the named test confirmed
red before the mutation was reverted. A checker that stays green under its
own mutation is not evidence (CLAUDE.md).

  1. `_SPEAKING_STATES` loses `would_breach`/`already_over`, so a collided
     rule is no longer stated
       -> test_a_case_covering_exactly_what_the_challenge_asked_for_is_accepted,
          test_every_required_coverage_path_is_reachable_from_must_state,
          and test_consider's the_challenge_names_the_rule_this_trade_collides_with
  2. `answer_provenance._COVERED_STATES` loses `already_over`, so the gate
     stops enforcing what the block still says is owed
       -> test_an_unjudged_rule_is_stated_but_not_required_in_the_case
  3. `_entry` stops resolving and always attaches the anchor
       -> test_a_ticker_containing_a_dot_keeps_its_fact_and_loses_its_anchor,
          test_a_collision_row_with_no_rule_id_is_stated_without_an_anchor
  4. `_entry` drops the whole entry when the anchor does not resolve
       -> the same two tests
  5. `_quote_verbatim` returns the reason casefolded/whitespace-collapsed
       -> test_the_users_own_words_are_carried_verbatim
  6. `UNCHECKED_ALWAYS` loses `tax`
       -> test_the_four_unconditional_unchecked_items_are_always_present
  7. `required_coverage` stops skipping a rule row with no `rule_id` and
     emits the uncitable `rule_collisions.None`
       -> test_a_collision_row_with_no_rule_id_is_stated_without_an_anchor
  8. `cmd_consider` stops emitting the block (#479's own acceptance
     criterion: removing the visible consumer must turn an integration test
     red)
       -> test_consider's whole section M, headed by
          test_a_considered_trade_puts_the_whole_challenge_in_front_of_the_caller
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ENGINE_DIR = os.path.join(REPO, "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE_DIR)
import answer_provenance  # noqa: E402
import evaluation_challenge  # noqa: E402


# ─────────────────────────────── fixtures ───────────────────────────────
#
# Shaped exactly like cmd_consider's own frozen trio (row["basis"],
# row["consequence"], row["rule_collisions"] --
# schemas/trade-evaluation.schema.json). A book five days stale, already
# over its own NVDA cap and growing, with one open disclosure, one excluded
# holding, and one rule that cannot be settled by a single hypothetical --
# so every branch of `must_state` has something real in it rather than
# being exercised by an empty list.

def _premise(**overrides):
    base = {"ticker": "NVDA", "side": "buy", "qty": 20, "price": 130.0,
            "date": "2026-01-06", "currency": "USD"}
    base.update(overrides)
    return base


def _basis(**overrides):
    base = {
        "source": "snapshot_anchor", "as_of": "2026-01-01", "stale_days": 5,
        "completeness": "declared_complete", "cost_basis": "average_cost",
        "valuation_basis": "unpriced", "reconciliation_ref": None,
        "state_version": "pb-v1:" + "0" * 64,
    }
    base.update(overrides)
    return base


def _consequence(**overrides):
    base = {
        "before": {"max_pct": 0.20, "weights": {"NVDA": 0.20}, "top3": 0.55,
                   "ai_pct": 0.40, "max_sector_pct": 0.40,
                   "oversize_triggered": False, "concentration_triggered": False,
                   "cash": {"balance": 8000.0, "weight": 0.08}},
        "after": {"max_pct": 0.34, "weights": {"NVDA": 0.34}, "top3": 0.61,
                  "ai_pct": 0.52, "max_sector_pct": 0.52,
                  "oversize_triggered": True, "concentration_triggered": True,
                  "cash": {"balance": 5400.0, "weight": 0.05}},
        "delta": {"max_pct": 0.14, "ticker_weight": 0.14},
        "disclosures": ["cash_unreliable"],
        "excluded_holdings": [{"ticker": "ACME", "reason": "unavailable_cost"}],
    }
    base.update(overrides)
    return base


def _rule_collisions():
    return [
        {"rule_id": "rule-1", "text": "Cap NVDA at 25%.", "metric_key": "max_pos_pct",
         "problem_key": "oversize", "state": "already_over", "worsens": True},
        {"rule_id": "rule-2", "text": "Never sell a winner inside a month.",
         "metric_key": "hold_days", "problem_key": "exit_timing",
         "state": "unjudged", "worsens": None},
        {"rule_id": "rule-3", "text": "Stay clear at all times.", "metric_key": "max_pos_pct",
         "problem_key": "oversize", "state": "clear", "worsens": None},
    ]


def _context(**overrides):
    base = {"reason": "It is still my highest-conviction name.",
            "why_now": "Their main supplier raised capacity guidance this morning.",
            "evidence_refs": ["Supplier capacity guidance, this morning"]}
    base.update(overrides)
    return base


def _build(premise=None, basis=None, consequence=None, rule_collisions=None, context=None):
    return evaluation_challenge.build_challenge(
        premise=_premise() if premise is None else premise,
        basis=_basis() if basis is None else basis,
        consequence=_consequence() if consequence is None else consequence,
        rule_collisions=_rule_collisions() if rule_collisions is None else rule_collisions,
        context=context)


def _anchored(challenge, topic=None):
    return [e for e in challenge["must_state"]
            if "anchor" in e and (topic is None or e["topic"] == topic)]


def _topics(challenge):
    return [e["topic"] for e in challenge["must_state"]]


# ─────────── 1. the floor and the gate are the same list ───────────
#
# The whole point of routing required_coverage through answer_provenance
# rather than deriving it twice. These two tests are the behavioral proof
# of that agreement: they compare what the block TELLS the agent against
# what the gate REFUSES the agent for, on the same frozen inputs, and fail
# in both directions.

def _claim_for(entry, collisions):
    """A minimally-valid engine_fact claim citing one must_state entry --
    built from the entry itself, so the case under test is literally
    'exactly what the challenge asked for' rather than a hand-authored
    approximation of it."""
    value = entry["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        text = f"Stating the fact at {entry['anchor']}."
    elif abs(value) <= 1.0:
        text = f"This reading is {value * 100:.1f}% of the book."
    else:
        text = f"This reading is {value}."
    claim = {"claim": text, "provenance": "engine_fact", "anchor": entry["anchor"]}
    # answer_provenance case 7: a claim anchored at a material already_over
    # rule's state/worsens must carry the frozen direction. Read off the
    # entry's own detail rather than restated here.
    parts = entry["anchor"].split(".")
    if parts[0] == "rule_collisions" and parts[-1] in ("state", "worsens"):
        row = next((r for r in collisions if r.get("rule_id") == parts[1]), None)
        if row and row.get("state") == "already_over" and row.get("worsens") is not None:
            claim["worsens"] = row["worsens"]
    return claim


def _case_covering(challenge, collisions, skip=None):
    """One engine_fact claim per required_coverage entry, citing the first
    must_state anchor that sits under that path, plus one judgment claim so
    the `for` side is non-empty."""
    claims = []
    for required in challenge["required_coverage"]:
        if skip is not None and required["path"] == skip:
            continue
        entry = next((e for e in _anchored(challenge)
                      if e["anchor"] == required["path"]
                      or e["anchor"].startswith(required["path"] + ".")), None)
        assert entry is not None, (
            f"the block requires {required['path']} but states no citable fact under it, "
            "so no case can be built that clears the gate")
        claims.append(_claim_for(entry, collisions))
    return {"for": [{"claim": "Conviction is intact.", "provenance": "agent_judgment"}],
            "against": claims}


def test_a_case_covering_exactly_what_the_challenge_asked_for_is_accepted():
    """The floor is reachable. If the block ever asked for something the
    gate does not enforce, or named a path no claim can be built against,
    this is where it shows up -- the case here is generated from the block,
    never hand-written to match it."""
    collisions = _rule_collisions()
    challenge = _build(rule_collisions=collisions)
    answer_provenance.validate_agent_case(
        _case_covering(challenge, collisions), basis=_basis(),
        consequence=_consequence(), rule_collisions=collisions, user_statements=())


def test_dropping_any_single_entry_the_challenge_named_is_refused():
    """The floor is a floor. Every entry the block named is load-bearing:
    removing exactly one and leaving the rest intact must be refused, so
    the block cannot list an obligation nothing enforces."""
    collisions = _rule_collisions()
    challenge = _build(rule_collisions=collisions)
    assert challenge["required_coverage"], "fixture must exercise at least one obligation"
    for required in challenge["required_coverage"]:
        case = _case_covering(challenge, collisions, skip=required["path"])
        try:
            answer_provenance.validate_agent_case(
                case, basis=_basis(), consequence=_consequence(),
                rule_collisions=collisions, user_statements=())
        except answer_provenance.AnswerProvenanceError as exc:
            assert required["path"] in str(exc), (
                f"refused, but the message never names the dropped {required['path']}: {exc}")
            continue
        raise AssertionError(
            f"the challenge named {required['path']} as owed, but a case dropping it "
            "was accepted -- the block is listing an obligation nothing enforces")


def test_every_required_coverage_path_is_reachable_from_must_state():
    """An obligation the agent cannot cite is an obligation it cannot meet.
    Every required path must have at least one anchored must_state entry at
    or under it -- otherwise the block names a debt with no way to pay it."""
    challenge = _build()
    for required in challenge["required_coverage"]:
        assert any(e["anchor"] == required["path"]
                   or e["anchor"].startswith(required["path"] + ".")
                   for e in _anchored(challenge)), (
            f"required_coverage names {required['path']} but no must_state entry "
            "carries an anchor under it")


def test_an_unjudged_rule_is_stated_but_not_required_in_the_case():
    """The deliberate asymmetry. `unjudged`/`unmapped` must be NAMED --
    presenting an unevaluated rule as no issue tells the user something the
    engine never checked -- but requiring a for/against claim for each
    would make a book with eight behavioral rules need eight claims saying
    nothing was measured."""
    challenge = _build()
    stated = {e["detail"]["rule_id"] for e in challenge["must_state"]
              if e["topic"] == "rule_collision"}
    assert "rule-2" in stated, "an unjudged rule must still be stated to the user"
    assert "rule-3" not in stated, "a clear rule needs no sentence"
    required = {r["path"] for r in challenge["required_coverage"]}
    assert "rule_collisions.rule-1" in required, "an already_over rule must be cited"
    assert "rule_collisions.rule-2" not in required, (
        "an unjudged rule must not be forced into the case")


# ─────────── 2. every anchor handed over resolves ───────────

def test_every_anchor_the_challenge_emits_resolves_against_the_frozen_record():
    """The anchor guarantee. The surface that tells the agent how to cite a
    fact and the gate that judges the citation must agree by construction:
    an anchor offered here is always one validate_agent_case accepts."""
    basis, consequence, collisions = _basis(), _consequence(), _rule_collisions()
    challenge = _build(basis=basis, consequence=consequence, rule_collisions=collisions)
    record = answer_provenance.build_record(basis, consequence, collisions)
    for entry in _anchored(challenge):
        resolved = answer_provenance.resolve_anchor(record, entry["anchor"])
        assert resolved is not answer_provenance.UNRESOLVED, (
            f"emitted an anchor that does not resolve: {entry['anchor']}")
        assert resolved == entry["value"], (
            f"{entry['anchor']} resolves to {resolved!r}, but the entry says {entry['value']!r}")


def test_a_ticker_containing_a_dot_keeps_its_fact_and_loses_its_anchor():
    """2330.TW is a real ticker and `consequence.after.weights.2330.TW`
    reads as weights -> "2330" -> "TW", which resolves to nothing. The fact
    is still owed and must still be stated; only the citation path drops."""
    consequence = _consequence(
        before={"max_pct": 0.20, "weights": {"2330.TW": 0.20}, "top3": 0.55,
                "cash": {"balance": 8000.0, "weight": 0.08}},
        after={"max_pct": 0.34, "weights": {"2330.TW": 0.34}, "top3": 0.61,
               "cash": {"balance": 5400.0, "weight": 0.05}},
        delta={"ticker_weight": 0.14})
    challenge = _build(premise=_premise(ticker="2330.TW"), consequence=consequence)
    positions = [e for e in challenge["must_state"] if e["topic"] == "position"]
    weight_entries = [e for e in positions if e["value"] in (0.20, 0.34)]
    assert len(weight_entries) == 2, (
        "the before/after weight of a dotted ticker must still be stated; "
        f"got {positions}")
    assert all("anchor" not in e for e in weight_entries), (
        "a path that cannot resolve must not be offered as a citation")
    # The delta rides a fixed path with no ticker in it, so it keeps its anchor.
    delta = [e for e in positions if e["value"] == 0.14]
    assert delta and "anchor" in delta[0], (
        "consequence.delta.ticker_weight carries no ticker and must stay citable")


def test_a_collision_row_with_no_rule_id_is_stated_without_an_anchor():
    """The schema allows a null rule_id defensively and problems.py does not
    produce one. If it ever appears, the rule must still be named: dropping
    it would turn a defensive null into a silently unmentioned collision."""
    collisions = [{"rule_id": None, "text": "Cap NVDA at 25%.", "metric_key": "max_pos_pct",
                   "problem_key": "oversize", "state": "would_breach", "worsens": None}]
    challenge = _build(rule_collisions=collisions)
    rows = [e for e in challenge["must_state"] if e["topic"] == "rule_collision"]
    assert len(rows) == 1 and rows[0]["value"] == "would_breach"
    assert "anchor" not in rows[0], "an unaddressable row must not carry a citation path"
    assert not [r for r in challenge["required_coverage"] if r["owes"] == "rule_collision"], (
        "an uncitable collision must not be required, or no case on this book is submittable")


# ─────────── 3. what the gate cannot enforce is still stated ───────────

def test_the_users_own_words_are_carried_verbatim():
    """The one part of the answer that may not be reworded. An answer that
    silently rephrased the stated reason would present the agent's reading
    of the decision as the user's."""
    context = _context()
    challenge = _build(context=context)
    quoted = {q["field"]: q["text"] for q in challenge["quote_verbatim"]}
    assert quoted["reason"] == context["reason"]
    assert quoted["why_now"] == context["why_now"]
    assert quoted["evidence_refs[0]"] == context["evidence_refs"][0]


def test_a_context_free_call_still_owes_everything_except_the_users_words():
    """Context-free `consider` is a complete use of the surface (#479's
    frozen parity matrix). Its disclosures, collisions and unchecked items
    are owed exactly the same; only the quoting obligation is empty."""
    with_context = _build(context=_context())
    without = _build(context=None)
    assert without["quote_verbatim"] == []
    assert without["must_state"] == with_context["must_state"], (
        "the stated facts must not depend on whether the user gave a reason")
    assert without["required_coverage"] == with_context["required_coverage"]
    assert "evidence_delta" not in without["unchecked"]
    assert "evidence_refs_unverified" not in without["unchecked"]


def test_the_four_unconditional_unchecked_items_are_always_present():
    """references/trade-consequence.md's own sentence, lifted into code so
    it stops being something an agent has to remember having read."""
    for challenge in (_build(), _build(context=_context())):
        for key in ("liquidity", "valuation", "tax", "position_fit"):
            assert key in challenge["unchecked"], f"{key} dropped off the unchecked list"


def test_evidence_refs_are_named_unverified_only_when_the_user_cited_something():
    """The engine does not fetch, date or believe an evidence reference. It
    says so when one exists, and says nothing when none does."""
    cited = _build(context=_context())
    assert "evidence_refs_unverified" in cited["unchecked"]
    assert "evidence_delta" in cited["unchecked"]
    none_cited = _build(context=_context(evidence_refs=[]))
    assert "evidence_refs_unverified" not in none_cited["unchecked"], (
        "nothing was cited, so there is nothing unverified to name")
    assert "evidence_delta" in none_cited["unchecked"], (
        "a stated reason still leaves the evidence-versus-price question unsettled")


# ─────────── 4. shape, order and purity ───────────

def test_every_topic_appears_and_in_the_declared_order():
    """The order is the order the facts depend on each other -- the basis
    first because every number after it is measured against that book, the
    disclosures last because they qualify what precedes them."""
    challenge = _build()
    seen = _topics(challenge)
    for topic in evaluation_challenge.TOPICS:
        assert topic in seen, f"the fixture exercises no {topic} entry"
    positions = [evaluation_challenge.TOPICS.index(t) for t in seen]
    assert positions == sorted(positions), f"topics out of declared order: {seen}"


def test_a_false_trigger_is_not_stated_as_a_fact():
    """A flag that is false is the absence of a fact. Listing it would pad
    the floor with non-events, which is how a floor stops being read."""
    consequence = _consequence()
    consequence["after"]["oversize_triggered"] = False
    consequence["after"]["concentration_triggered"] = False
    challenge = _build(consequence=consequence)
    assert not [e for e in challenge["must_state"] if e["value"] is False]


def test_the_excluded_holding_is_named_not_merely_counted():
    """#515: `partial_book` says THAT something was excluded; the identity
    says WHAT, and the reference requires it named wherever a derived
    percentage appears."""
    challenge = _build()
    excluded = [e for e in challenge["must_state"] if e["topic"] == "excluded_holding"]
    assert [e["value"] for e in excluded] == ["ACME"]
    assert excluded[0]["detail"]["reason"] == "unavailable_cost"


def test_building_twice_returns_the_same_block_and_mutates_no_input():
    """Pure. cmd_consider calls this on values it has already frozen and
    then stores those same values; a builder that edited them in place
    would change what the row records."""
    premise, basis = _premise(), _basis()
    consequence, collisions, context = _consequence(), _rule_collisions(), _context()
    frozen = copy.deepcopy((premise, basis, consequence, collisions, context))
    first = evaluation_challenge.build_challenge(
        premise=premise, basis=basis, consequence=consequence,
        rule_collisions=collisions, context=context)
    second = evaluation_challenge.build_challenge(
        premise=premise, basis=basis, consequence=consequence,
        rule_collisions=collisions, context=context)
    assert first == second
    assert (premise, basis, consequence, collisions, context) == frozen, (
        "build_challenge edited one of the values cmd_consider is about to store")


def test_a_stale_and_unverified_basis_is_reported_as_both():
    """The two reasons a basis must be cited are independent and a row can
    carry both. Collapsing them would let a message name one and hide the
    other."""
    both = answer_provenance.required_coverage(
        _basis(stale_days=5, completeness="unverified"), _consequence(), ())
    assert [r for r in both if r["owes"] == "basis"][0]["key"] == "stale_and_unverified"
    stale_only = answer_provenance.required_coverage(
        _basis(stale_days=5, completeness="declared_complete"), _consequence(), ())
    assert [r for r in stale_only if r["owes"] == "basis"][0]["key"] == "stale"
    fresh_only = answer_provenance.required_coverage(
        _basis(stale_days=0, completeness="unverified"), _consequence(), ())
    assert [r for r in fresh_only if r["owes"] == "basis"][0]["key"] == "unverified"
    exempt = answer_provenance.required_coverage(
        _basis(stale_days=0, completeness="declared_complete"),
        _consequence(disclosures=[]), ())
    assert exempt == (), "a fresh, declared-complete book with no disclosures owes nothing"


# ─────────── 5. the module's vocabularies and the schema's ───────────
#
# The same drift discipline `tests/test_consider.py` already runs for
# CONSIDER_DECISIONS against trade-evaluation.schema.json's `decision` enum.
# Without it a constant here and an enum there can disagree in the direction
# the shape check cannot see: a name the code never emits, or an enum entry
# no code path produces, both leave every behavioral test green while the
# published contract stops describing the thing it names.

def _challenge_schema():
    with open(os.path.join(REPO, "skills", "fomo-kernel", "schemas",
                           "evaluation-challenge.schema.json"), encoding="utf-8") as f:
        return json.load(f)


def test_module_vocabularies_match_the_schemas_enums():
    schema = _challenge_schema()["properties"]
    topics = schema["must_state"]["items"]["properties"]["topic"]["enum"]
    assert list(evaluation_challenge.TOPICS) == list(topics), (
        "evaluation_challenge.TOPICS and the schema's topic enum disagree; the enum is "
        "also the declared presentation order, so this must stay an ordered equality")

    unchecked = set(schema["unchecked"]["items"]["enum"])
    declared = set(evaluation_challenge.UNCHECKED_ALWAYS
                   + evaluation_challenge.UNCHECKED_WITH_CONTEXT
                   + evaluation_challenge.UNCHECKED_WITH_EVIDENCE)
    assert declared == unchecked, (
        f"unchecked vocabulary drift: code {sorted(declared)} vs schema {sorted(unchecked)}")
    assert schema["unchecked"]["minItems"] == len(evaluation_challenge.UNCHECKED_ALWAYS), (
        "the schema's minItems is the count of unconditional unchecked items; a new "
        "unconditional item must move both")

    assert set(evaluation_challenge.CASE_REQUIRED) == set(
        schema["case_required"]["properties"])


def test_required_coverage_vocabulary_matches_the_schemas_enums():
    """`required_coverage` is answer_provenance's, so its vocabulary is
    answer_provenance's too -- the schema publishes it and nothing else
    checks that the two still describe the same set."""
    schema = _challenge_schema()["properties"]["required_coverage"]["items"]["properties"]
    assert set(schema["owes"]["enum"]) == {"disclosure", "basis", "rule_collision"}
    keys = set(schema["key"]["enum"])
    assert set(answer_provenance._COVERED_STATES) <= keys, (
        "a collision state the gate enforces is missing from the schema's key enum")
    import consequence as consequence_engine
    assert set(consequence_engine.DISCLOSURES) <= keys, (
        "consequence.py grew a disclosure the challenge schema does not publish")


def _tests():
    return [(name, obj) for name, obj in sorted(globals().items())
            if name.startswith("test_") and callable(obj)]


if __name__ == "__main__":
    failed = 0
    for name, fn in _tests():
        try:
            fn()
            print(f"✅ {name}")
        except AssertionError as exc:
            failed += 1
            print(f"❌ {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 -- an unexpected raise is a failure, not a crash
            failed += 1
            print(f"❌ {name}: {exc!r}")
    total = len(_tests())
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
