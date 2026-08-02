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
own mutation is not evidence (docs/maintainer-guide.md).

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
  9. `_price_basis_entries` returns [] so the market session never reaches
     must_state (#618)
       -> test_a_priced_answer_owes_the_market_session_it_was_valued_at,
          test_an_instrument_off_the_frame_date_is_named_and_a_matching_one_is_not,
          test_a_dotted_ticker_off_the_frame_date_keeps_its_fact_and_its_identity,
          test_every_topic_appears_and_in_the_declared_order,
          test_every_required_coverage_path_is_reachable_from_must_state,
          and test_consider's the_priced_answer_owes_the_session_and_names_the_
          instrument_off_it
 10. `review._price_observation_record` defaults an unpriced run to today's
     date instead of returning None
       -> test_consider's an_offline_answer_carries_no_price_day_at_all, plus
          eleven more through `_check_evaluation_shape`
 11. `answer_provenance.required_coverage` stops emitting the price_basis
     entry, so the block states an obligation the gate does not enforce
       -> test_a_stale_and_unverified_basis_is_reported_as_both and
          test_a_price_day_citation_does_not_also_pay_the_staleness_obligation,
          plus test_consider's a_case_silent_about_the_price_day_is_refused_on_
          the_production_path
 12. `_paid_path` takes the broadest matching path rather than the narrowest,
     which is what plain prefix matching did before #618
       -> test_a_case_covering_exactly_what_the_challenge_asked_for_is_accepted
          and test_a_price_day_citation_does_not_also_pay_the_staleness_obligation

#579's own mutations, run the same way (cp backup, clear __pycache__, read
the exit code, restore, re-verify green):

 13. `_rule_collision_entries` states `row["state"]` again, restoring the
     status-only interpretation that failed owner-live acceptance
       -> test_the_stated_fact_is_the_transaction_effect_and_the_state_is_
          demoted, and test_consider's a_trade_that_reduces_an_over_cap_
          position_is_never_delivered_as_a_breach
 14. `_MUST_NOT_CONVEY["improved_but_still_over"]` loses
     `crossed_by_this_trade`, so an improving trade may be called a breach
       -> test_the_improving_case_owes_both_truths_and_forbids_the_breach_
          reading, and the same test_consider case
 15. `_rule_effect_projection` carries `state`/`worsens` into the
     user-facing block (raw diagnostic narration)
       -> test_the_projection_carries_no_machine_diagnostic_field
 16. `answer_provenance._COVERED_EFFECTS` loses `improved_but_still_over`,
     so the gate stops requiring what the block still states
       -> test_the_improving_case_owes_both_truths_and_forbids_the_breach_
          reading
 17. the legacy fallback in `_rule_collision_entries` is removed, so a row
     recorded before `rule_effect` stops speaking while the gate still
     requires it
       -> test_a_legacy_row_still_states_what_the_gate_still_requires

Eight more, on `consequence.py` and `answer_provenance.py`, are recorded in
the PR description; every one of the twelve turned its named suite red.
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
import consequence as consequence_engine  # noqa: E402
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
        # Priced, on a deliberately MIXED frame (#618): NVDA printed on the
        # frame's own newest session and PLTR a day earlier, so both halves of
        # the price_basis topic have something real to exercise -- the frame
        # summary every priced answer owes, and the per-instrument exception a
        # single frame date would have hidden (#583 section 2).
        "valuation_basis": "priced", "reconciliation_ref": None,
        "price_observations": {"as_of": "2026-01-06",
                               "by_ticker": {"NVDA": "2026-01-06",
                                             "PLTR": "2026-01-05"}},
        "state_version": "pb-v1:" + "0" * 64,
    }
    base.update(overrides)
    return base


def _unpriced_basis(**overrides):
    """The other lane: no current price reached this book, so it carries no
    price observation at all -- not a null, not today's date."""
    base = _basis(valuation_basis="unpriced")
    base.pop("price_observations")
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
         "problem_key": "oversize", "state": "already_over", "worsens": True,
         "rule_effect": "worsened_existing_breach", "limit": 0.25,
         "limit_source": "user_cap"},
        {"rule_id": "rule-2", "text": "Never sell a winner inside a month.",
         "metric_key": "hold_days", "problem_key": "exit_timing",
         "state": "unjudged", "worsens": None, "rule_effect": "unjudged",
         "limit": None, "limit_source": None},
        {"rule_id": "rule-3", "text": "Stay clear at all times.", "metric_key": "max_pos_pct",
         "problem_key": "oversize", "state": "clear", "worsens": None,
         "rule_effect": "compliant", "limit": 0.25, "limit_source": "user_cap"},
    ]


def _legacy_rule_collisions():
    """The same three rows as they were written before #579 added
    `rule_effect` -- a row already on a user's `trade_evaluations.jsonl`.
    Every reader here has to keep working on one of these, and the floor and
    the gate have to keep agreeing about it, or the case a legacy row still
    requires becomes one no case can be built for."""
    rows = []
    for row in _rule_collisions():
        rows.append({key: value for key, value in row.items()
                     if key not in ("rule_effect", "limit", "limit_source")})
    return rows


def _improving_collision():
    """The owner's own 2026-08-02 case in fictional numbers: a position over
    a self-authored 20% cap, and a trade that reduces it without clearing
    the line."""
    return [{"rule_id": "rule-1", "text": "Cap NVDA at 20%.", "metric_key": "max_pos_pct",
             "problem_key": "oversize", "state": "already_over", "worsens": False,
             "rule_effect": "improved_but_still_over", "limit": 0.20,
             "limit_source": "user_cap"}]


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
    # answer_provenance case 7: a claim anchored at a rule collision must
    # declare the frozen transition -- `rule_effect` since #579, and the
    # pre-#579 `worsens` boolean on a row that predates it. Read off the
    # frozen row rather than restated here.
    parts = entry["anchor"].split(".")
    if parts[0] == "rule_collisions" and parts[-1] in ("state", "worsens", "rule_effect"):
        row = next((r for r in collisions if r.get("rule_id") == parts[1]), None)
        if row and row.get("rule_effect") in consequence_engine.DIRECTIONAL_RULE_EFFECTS:
            claim["rule_effect"] = row["rule_effect"]
        elif row and row.get("state") == "already_over" and row.get("worsens") is not None:
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
    for basis in (_basis(), _unpriced_basis()):
        challenge = _build(basis=basis, rule_collisions=collisions)
        answer_provenance.validate_agent_case(
            _case_covering(challenge, collisions), basis=basis,
            consequence=_consequence(), rule_collisions=collisions, user_statements=())


def test_dropping_any_single_entry_the_challenge_named_is_refused():
    """The floor is a floor. Every entry the block named is load-bearing:
    removing exactly one and leaving the rest intact must be refused, so
    the block cannot list an obligation nothing enforces.

    Run over both valuation lanes (#618). The priced one is what proves the
    price day is enforced and not merely announced; the unpriced one is what
    proves nothing was quietly made mandatory on a book that never had a
    price observation to cite."""
    collisions = _rule_collisions()
    for basis in (_basis(), _unpriced_basis()):
        challenge = _build(basis=basis, rule_collisions=collisions)
        assert challenge["required_coverage"], "fixture must exercise at least one obligation"
        for required in challenge["required_coverage"]:
            case = _case_covering(challenge, collisions, skip=required["path"])
            try:
                answer_provenance.validate_agent_case(
                    case, basis=basis, consequence=_consequence(),
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


# ─────────── 2b. which market session priced the book (#618) ───────────
#
# Since #611 a `consider` weight is a share of the current close rather than
# of cost, so the same premise re-asked returns different numbers. Without
# the price day in the answer the user cannot tell whether the market moved
# or their own book did -- and "the numbers moved" with no attributable cause
# is the #429-class question that comes back as a dogfood finding.


def test_a_priced_answer_owes_the_market_session_it_was_valued_at():
    challenge = _build()
    priced = [e for e in challenge["must_state"] if e["topic"] == "price_basis"]
    assert priced, "a priced answer that never says which close it used"
    frame = priced[0]
    assert frame["value"] == "2026-01-06"
    assert frame["anchor"] == "basis.price_observations.as_of"
    # ... and it is a different fact from the record's own as_of, which is the
    # whole reason this exists: one is the last trade, the other is the market.
    basis_dates = {e["value"] for e in challenge["must_state"]
                   if e["topic"] == "basis" and e["anchor"] == "basis.as_of"}
    assert basis_dates == {"2026-01-01"} and frame["value"] not in basis_dates


def test_an_unpriced_answer_owes_no_price_day_and_is_given_none():
    """The hard rule. An offline answer must not grow a date -- not a null,
    not a placeholder, not today's -- and must not be told it owes one."""
    challenge = _build(basis=_unpriced_basis())
    assert not [e for e in challenge["must_state"] if e["topic"] == "price_basis"], (
        "an unpriced book was handed a market session it never observed")
    assert not [r for r in challenge["required_coverage"] if r["owes"] == "price_basis"], (
        "an unpriced answer cannot cite a price day, so it must not be required to")


def test_an_instrument_off_the_frame_date_is_named_and_a_matching_one_is_not():
    """#583 section 2, one surface over. A frame date alone lets one fresh
    close stand in for a stale one, and a date per holding on a same-day
    frame is one entry per position saying the same thing. The exceptions
    are exactly the instruments the summary does not describe."""
    challenge = _build()
    named = {e["detail"]["ticker"]: e["value"] for e in challenge["must_state"]
             if e["topic"] == "price_basis" and "detail" in e}
    assert named == {"PLTR": "2026-01-05"}, (
        f"only the instrument off the frame date is owed its own entry; got {named}")
    same_day = _basis(price_observations={"as_of": "2026-01-06",
                                          "by_ticker": {"NVDA": "2026-01-06",
                                                        "PLTR": "2026-01-06"}})
    entries = [e for e in _build(basis=same_day)["must_state"] if e["topic"] == "price_basis"]
    assert len(entries) == 1 and "detail" not in entries[0], (
        "a same-day frame owes one sentence, not one per holding")


def test_a_dotted_ticker_off_the_frame_date_keeps_its_fact_and_its_identity():
    """`basis.price_observations.by_ticker.2330.TW` walks as by_ticker ->
    '2330' -> 'TW' and resolves to nothing. The date is still owed, and the
    ticker rides in `detail` -- without it the answer would carry a market
    session with no instrument attached."""
    basis = _basis(price_observations={"as_of": "2026-01-06",
                                       "by_ticker": {"NVDA": "2026-01-06",
                                                     "2330.TW": "2026-01-05"}})
    entries = [e for e in _build(basis=basis)["must_state"] if e["topic"] == "price_basis"]
    dotted = [e for e in entries if e.get("detail", {}).get("ticker") == "2330.TW"]
    assert len(dotted) == 1, f"the dotted instrument's session was dropped: {entries}"
    assert dotted[0]["value"] == "2026-01-05"
    assert "anchor" not in dotted[0], "a path that cannot resolve must not be offered"


def test_a_price_day_citation_does_not_also_pay_the_staleness_obligation():
    """One claim, one debt. `basis.price_observations` is the first required
    path to sit under another (`basis`), and plain prefix matching let a
    single price-day citation discharge staleness too -- a case that never
    mentioned the stale record accepted, the whole suite green."""
    basis, consequence = _basis(), _consequence()
    case = {"for": [{"claim": "Conviction is intact.", "provenance": "agent_judgment"}],
            "against": [{"claim": "Priced at the sixth's closes.", "provenance": "engine_fact",
                         "anchor": "basis.price_observations.as_of"}]}
    try:
        answer_provenance.validate_agent_case(
            case, basis=basis, consequence=consequence, rule_collisions=(),
            user_statements=())
    except answer_provenance.AnswerProvenanceError as exc:
        assert "basis" in str(exc), exc
        assert "basis.price_observations" not in str(exc), (
            f"the price day WAS cited; only staleness is still owed: {exc}")
        return
    raise AssertionError(
        "a case citing only the price day was accepted on a stale book -- the price "
        "citation paid the staleness obligation it never addressed")


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


def test_an_unclassified_book_states_no_driver_concentration():
    """The zero that means "nobody looked".

    `dim_diversify` returns `max_sector: None` when nothing is classified,
    and in that same branch `max_sector_pct` falls out of a `.get(None, 0)`
    default while `ai_pct` sums to 0.0 over zero AI weights. Both are 0
    rather than null, so a null check lets them through and the answer owes
    the user "your AI exposure is 0% and your largest sector is 0%" on a
    book where sector attribution does not exist. That is an unknown
    presented as a fact — shipped in the first cut of #479 Wave B, found by
    reading a real payload rather than by any test.
    """
    consequence = _consequence()
    consequence["after"].update({"ai_pct": 0.0, "max_sector_pct": 0, "max_sector": None})
    consequence["disclosures"] = ["unmapped_driver"]
    challenge = _build(consequence=consequence)
    stated = {e["anchor"] for e in _anchored(challenge, "concentration")}
    assert "consequence.after.top3" in stated, (
        "top3 is pure weight and needs no classification; it is still owed")
    assert "consequence.after.ai_pct" not in stated
    assert "consequence.after.max_sector_pct" not in stated
    # ... and the disclosure that explains the absence is still required.
    assert any(r["key"] == "unmapped_driver" for r in challenge["required_coverage"])


def test_a_real_zero_beside_a_classified_sector_is_still_stated():
    """The counterweight, and the reason this reads a sibling field rather
    than the value. A book with real sectors and no AI names really is 0% AI,
    and suppressing that would hide a measurement the user is entitled to."""
    consequence = _consequence()
    consequence["after"].update({"ai_pct": 0.0, "max_sector_pct": 0.45,
                                 "max_sector": "Industrials"})
    stated = {e["anchor"] for e in _anchored(_build(consequence=consequence), "concentration")}
    assert "consequence.after.ai_pct" in stated, (
        "0% AI on a classified book is a measurement, not an absence")
    assert "consequence.after.max_sector_pct" in stated


def test_an_ai_reading_survives_a_book_with_no_classified_sector():
    """A driver map may flag AI on a position whose sector it leaves
    unclassified. `max_sector` is None there, but the AI weight was really
    measured, and a rule keyed only on `max_sector` would drop the one real
    reading on the book."""
    consequence = _consequence()
    consequence["after"].update({"ai_pct": 0.52, "max_sector_pct": 0, "max_sector": None})
    stated = {e["anchor"] for e in _anchored(_build(consequence=consequence), "concentration")}
    assert "consequence.after.ai_pct" in stated
    assert "consequence.after.max_sector_pct" not in stated


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
        _unpriced_basis(stale_days=0, completeness="declared_complete"),
        _consequence(disclosures=[]), ())
    assert exempt == (), "a fresh, declared-complete book with no disclosures owes nothing"
    # ... and pricing that same exempt book owes exactly one thing: which
    # session it was priced at (#618). Nothing else about the basis moved.
    priced = answer_provenance.required_coverage(
        _basis(stale_days=0, completeness="declared_complete"),
        _consequence(disclosures=[]), ())
    assert [dict(r) for r in priced] == [
        {"path": "basis.price_observations", "owes": "price_basis", "key": "price_observed"}], priced


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

    # #579's own vocabularies. Both directions: an effect the schema
    # publishes that no table row supplies, and a slot a table row emits
    # that the schema does not admit, are equally invisible to the
    # behavioral tests above.
    effects = schema["rule_effects"]["items"]["properties"]
    assert set(effects["effect"]["enum"]) == (
        set(consequence_engine.RULE_EFFECTS) - {"compliant"}), (
        "the projected effect enum must be every effect that speaks, and only those")
    assert set(effects["limit_source"]["enum"]) == set(consequence_engine.LIMIT_SOURCES)
    for key in ("must_convey", "must_not_convey"):
        assert set(effects[key]["items"]["enum"]) == set(
            evaluation_challenge.RULE_EFFECT_SLOTS), f"{key} slot vocabulary drift"
    declared = {slot for effect in effects["effect"]["enum"]
                for slot in evaluation_challenge._MUST_CONVEY[effect]
                + evaluation_challenge._MUST_NOT_CONVEY[effect]}
    assert declared == set(evaluation_challenge.RULE_EFFECT_SLOTS), (
        "a slot in the vocabulary that no effect ever uses is a value the contract "
        f"publishes and nothing produces: {sorted(set(evaluation_challenge.RULE_EFFECT_SLOTS) - declared)}")


def test_required_coverage_vocabulary_matches_the_schemas_enums():
    """`required_coverage` is answer_provenance's, so its vocabulary is
    answer_provenance's too -- the schema publishes it and nothing else
    checks that the two still describe the same set."""
    schema = _challenge_schema()["properties"]["required_coverage"]["items"]["properties"]
    assert set(schema["owes"]["enum"]) == {"disclosure", "basis", "price_basis",
                                           "rule_collision"}
    keys = set(schema["key"]["enum"])
    # #618's own literal. Named here rather than left to the behavioral tests
    # because an enum value no code path emits and a code path no enum admits
    # are both invisible to them.
    assert "price_observed" in keys, (
        "the price-day obligation's key is missing from the schema's key enum")
    emitted = {entry["key"] for entry in answer_provenance.required_coverage(
        _basis(), _consequence(), _rule_collisions())}
    assert emitted <= keys, f"required_coverage emits a key the schema does not publish: {emitted - keys}"
    assert set(answer_provenance._COVERED_EFFECTS) <= keys, (
        "a rule effect the gate enforces is missing from the schema's key enum")
    assert set(answer_provenance._LEGACY_COVERED_STATES) <= keys, (
        "the pre-#579 fallback still emits a collision state the schema does not publish")
    import consequence as consequence_engine
    assert set(consequence_engine.DISCLOSURES) <= keys, (
        "consequence.py grew a disclosure the challenge schema does not publish")
    assert set(answer_provenance._COVERED_EFFECTS) <= set(consequence_engine.RULE_EFFECTS), (
        "the gate enforces an effect consequence.py does not produce")


# ─────────── 6. the product-safe rule-effect projection (#579) ───────────
#
# The block that carries what the answer may say about a rule collision, and
# what it may not. PR #608 said the same thing in prose and the next
# owner-live walk still described an improving trade as a breach, so the
# negative half is data here.

def _projected(challenge, rule_id):
    return next((row for row in challenge["rule_effects"] if row["rule_id"] == rule_id), None)


def test_the_improving_case_owes_both_truths_and_forbids_the_breach_reading():
    """The exact 2026-08-02 failure, expressed as the projection that must
    replace it. Three positive slots, because the correct statement carries
    the improvement AND that the line is still crossed AND that it was
    crossed before this trade -- and two negative ones naming the reading
    the owner actually received."""
    challenge = _build(rule_collisions=_improving_collision())
    row = _projected(challenge, "rule-1")
    assert row is not None, "an improving collision must still speak"
    # And the floor and the gate agree about it: the line is still crossed
    # after this trade, so silence about it would read as approval.
    assert [(entry["path"], entry["key"]) for entry in challenge["required_coverage"]
            if entry["owes"] == "rule_collision"] == [
        ("rule_collisions.rule-1", "improved_but_still_over")], (
        "an improving collision that leaves the line crossed must still be a claim the "
        "case is refused for dropping")
    assert row["effect"] == "improved_but_still_over"
    assert set(row["must_convey"]) == {"over_before_this_trade", "moved_toward_the_line",
                                       "over_after"}
    assert "crossed_by_this_trade" in row["must_not_convey"], \
        "a trade that reduces an over-cap position did not cross the line"
    assert "moved_further_over" in row["must_not_convey"], \
        "and it did not move against the rule either"
    # The line and whose line it is, so the answer can say "your 20% rule"
    # without either inventing the number or attributing an engine default.
    assert row["limit"] == 0.20 and row["limit_source"] == "user_cap"


def test_every_effect_that_speaks_gets_a_projection_and_the_two_halves_are_disjoint():
    """One row per speaking effect, and no slot both required and forbidden.
    A table that let the same meaning sit on both sides would be satisfiable
    and refusable at once, which is a contract that says nothing."""
    speaking = [effect for effect in consequence_engine.RULE_EFFECTS if effect != "compliant"]
    for effect in speaking:
        collisions = [{"rule_id": "r", "text": "A rule.", "metric_key": "max_pos_pct",
                       "problem_key": "oversize",
                       "state": consequence_engine._EFFECT_STATES[effect][0],
                       "worsens": True if effect == "worsened_existing_breach" else None,
                       "rule_effect": effect, "limit": None, "limit_source": None}]
        row = _projected(_build(rule_collisions=collisions), "r")
        assert row is not None, f"{effect} must be projected"
        assert row["effect"] == effect
        assert row["must_convey"], f"{effect} owes at least one meaning"
        assert row["must_not_convey"], f"{effect} forbids at least one meaning"
        assert not (set(row["must_convey"]) & set(row["must_not_convey"])), \
            f"{effect} both requires and forbids the same slot"
        assert set(row["must_convey"]) <= set(evaluation_challenge.RULE_EFFECT_SLOTS)
        assert set(row["must_not_convey"]) <= set(evaluation_challenge.RULE_EFFECT_SLOTS)
        assert "limit" not in row, "no line was judged, so none is quoted"


def test_a_compliant_rule_is_the_only_one_that_passes_in_silence():
    challenge = _build()
    projected = {row["rule_id"] for row in challenge["rule_effects"]}
    assert projected == {"rule-1", "rule-2"}, (
        "rule-3 is compliant and must not be projected; rule-2 is unjudged and must be, "
        f"got {sorted(projected)}")


def test_the_projection_carries_no_machine_diagnostic_field():
    """#713 repair step 4: the complete diagnostics stay on the evaluation
    row for QA and replay, and the block the answer is built from carries the
    consequence and nothing about how the engine reached it."""
    for row in _build()["rule_effects"]:
        assert "state" not in row and "worsens" not in row, (
            f"the product projection leaks a machine diagnostic: {sorted(row)}")
        assert set(row) <= {"rule_id", "text", "effect", "limit", "limit_source",
                            "must_convey", "must_not_convey"}, sorted(row)


def test_the_stated_fact_is_the_transaction_effect_and_the_state_is_demoted():
    """`must_state`'s rule_collision entry used to state `already_over` as
    the fact owed. That is the accusation the owner received. The state is
    still carried -- a QA run reconciles against it -- but as detail, not as
    the fact."""
    entry = next(e for e in _build(rule_collisions=_improving_collision())["must_state"]
                 if e["topic"] == "rule_collision")
    assert entry["value"] == "improved_but_still_over"
    assert entry["value"] not in consequence_engine.COLLISION_STATES, \
        "the stated fact must not be a collision state at all"
    assert entry["anchor"] == "rule_collisions.rule-1.rule_effect"
    assert entry["detail"]["state"] == "already_over" and entry["detail"]["worsens"] is False, \
        "the diagnostics are kept, one level down, not deleted"


def test_a_resolved_breach_is_stated_but_not_required_in_the_case():
    """Under the state vocabulary a resolved breach was `clear` and said
    nothing at all. It speaks now -- and it is deliberately not on
    `required_coverage`: silence about good news cannot hide a risk, and the
    floor is allowed to be wider than the gate."""
    collisions = [{"rule_id": "rule-1", "text": "Cap NVDA at 20%.",
                   "metric_key": "max_pos_pct", "problem_key": "oversize",
                   "state": "clear", "worsens": None,
                   "rule_effect": "resolved_existing_breach", "limit": 0.20,
                   "limit_source": "user_cap"}]
    challenge = _build(rule_collisions=collisions)
    assert _projected(challenge, "rule-1")["effect"] == "resolved_existing_breach"
    assert any(e["topic"] == "rule_collision" for e in challenge["must_state"])
    assert not [entry for entry in challenge["required_coverage"]
                if entry["owes"] == "rule_collision"], \
        "a resolved breach is stated, never demanded as a claim"


def test_a_row_whose_effect_contradicts_its_own_state_is_refused():
    """#579's compatibility clause, on the surface that reads the row. A
    hand-built or corrupted pair cannot be projected as either half."""
    collisions = [{"rule_id": "rule-1", "text": "Cap NVDA at 20%.",
                   "metric_key": "max_pos_pct", "problem_key": "oversize",
                   "state": "clear", "worsens": None,
                   "rule_effect": "worsened_existing_breach", "limit": 0.20,
                   "limit_source": "user_cap"}]
    try:
        _build(rule_collisions=collisions)
    except ValueError as exc:
        assert "cannot accompany state" in str(exc), str(exc)
        return
    raise AssertionError("a contradictory rule_effect/state pair must not be projected")


def test_a_legacy_row_still_states_what_the_gate_still_requires():
    """A row written before #579 carries no effect. It must not vanish from
    the floor while the gate still requires it -- that combination makes
    every case on such a book unsubmittable, which is the failure the
    floor-and-gate tests above exist to catch, arriving through the
    compatibility path instead."""
    challenge = _build(rule_collisions=_legacy_rule_collisions())
    required = {entry["path"] for entry in challenge["required_coverage"]
                if entry["owes"] == "rule_collision"}
    assert required == {"rule_collisions.rule-1"}
    stated = {e["anchor"] for e in _anchored(challenge, "rule_collision")}
    assert stated == {"rule_collisions.rule-1.state", "rule_collisions.rule-2.state"}, (
        "a legacy row falls back to stating its collision state, and the unjudged rule is "
        f"still named: {sorted(stated)}")
    assert challenge["rule_effects"] == [], \
        "a legacy row has no deterministic effect, so nothing is projected for it"


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
