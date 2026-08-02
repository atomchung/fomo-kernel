#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""answer_provenance.py (#414, Wave A) — offline, deterministic, no pytest.

engine/answer_provenance.py::validate_agent_case is a new, self-contained
semantic layer on top of review.py's existing *structural* --agent-case
check (review._validate_agent_case, review.py:5126): shape, exact key set,
non-empty claim string, provenance in AGENT_CASE_PROVENANCE. That existing
check has no opinion on whether a claim labelled engine_fact actually
corresponds to the frozen consequence it claims to, whether its numbers
match, or whether either side of the case is merely present-but-empty.
This file proves the missing semantic layer rejects exactly the eight
adversarial cases issue #414 names, accepts a well-formed answer, and
cannot silently drift from review.py's own provenance vocabulary or this
module's own new schema.

This file never imports or exercises review.py's CLI (cmd_consider) --
that integration is explicitly Wave B's job, a separate lane from this
one (issue #414's Wave allocation comment). It imports review.py's module
object exactly once, for the one cross-check named above.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ENGINE_DIR = os.path.join(REPO, "skills", "fomo-kernel", "engine")
SCHEMAS_DIR = os.path.join(REPO, "skills", "fomo-kernel", "schemas")
sys.path.insert(0, ENGINE_DIR)
import answer_provenance  # noqa: E402
import review as review_engine  # noqa: E402


# ─────────────────────────────── fixtures ───────────────────────────────
#
# A minimal, realistic frozen trio shaped exactly like cmd_consider's own
# output (row["basis"], row["consequence"], row["rule_collisions"] --
# schemas/trade-evaluation.schema.json): a book already over its own
# 25% NVDA cap, growing further, on a five-day-stale ledger-backed basis
# with one open disclosure. Every fixture function returns a fresh literal
# so no test can mutate another test's data by sharing a dict.

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
        "before": {"max_pct": 0.20, "weights": {"NVDA": 0.20}},
        "after": {"max_pct": 0.34, "weights": {"NVDA": 0.34}},
        "delta": {"max_pct": 0.14},
        "disclosures": ["cash_unreliable"],
        "excluded_holdings": [],
    }
    base.update(overrides)
    return base


def _rule_collisions():
    return [{"rule_id": "rule-1", "text": "Cap NVDA at 25%.", "metric_key": "max_pos_pct",
             "problem_key": "oversize", "state": "already_over", "worsens": True,
             "rule_effect": "worsened_existing_breach", "limit": 0.25,
             "limit_source": "user_cap"}]


def _legacy_rule_collisions():
    """The same row as it was written before #579 added `rule_effect` -- a
    row already sitting on a user's trade_evaluations.jsonl. The gate has to
    keep checking the direction it does carry rather than reading a missing
    field as no obligation."""
    return [{key: value for key, value in row.items()
             if key not in ("rule_effect", "limit", "limit_source")}
            for row in _rule_collisions()]


def _max_pct_claim(**overrides):
    claim = {"claim": "This grows NVDA from 20% to 34% of your book.",
             "provenance": "engine_fact", "anchor": "consequence.after.max_pct"}
    claim.update(overrides)
    return claim


def _effect_claim(**overrides):
    """#579: the transition, declared as a six-way assertion beside the
    prose. Nothing offline can read a sentence for direction, so the claim
    carries the frozen effect and it is checked exactly."""
    claim = {"claim": "NVDA is already over your 25% cap, and this trade makes it worse.",
             "provenance": "engine_fact", "anchor": "rule_collisions.rule-1.rule_effect",
             "rule_effect": "worsened_existing_breach"}
    claim.update(overrides)
    return claim


def _worsens_claim(**overrides):
    """The pre-#579 two-way version, for a stored row that carries no
    effect."""
    claim = {"claim": "NVDA is already over your 25% cap, and this trade makes it worse.",
             "provenance": "engine_fact", "anchor": "rule_collisions.rule-1.worsens",
             "worsens": True}
    claim.update(overrides)
    return claim


def _disclosure_claim():
    return {"claim": "Cash reliability is currently degraded for this book.",
            "provenance": "engine_fact", "anchor": "consequence.disclosures.0"}


def _staleness_claim():
    return {"claim": "The book was last reconciled 5 days ago.",
            "provenance": "engine_fact", "anchor": "basis.stale_days"}


def _judgment_claim():
    return {"claim": "AI capex momentum remains strong across the sector.",
            "provenance": "agent_judgment"}


def _valid_case():
    """A well-formed agent_case against the fixtures above: one judgment
    claim for, and four engine_fact claims against, jointly covering both
    the open disclosure and the material staleness -- so acceptance is
    proven on a case that actually exercises every check, not one that
    passes only because nothing applicable was said."""
    return {
        "for": [_judgment_claim()],
        "against": [_max_pct_claim(), _effect_claim(), _disclosure_claim(), _staleness_claim()],
    }


def _legacy_case():
    """The same case against a row recorded before `rule_effect` existed:
    the direction is declared as the old boolean, and the gate still checks
    it."""
    return {
        "for": [_judgment_claim()],
        "against": [_max_pct_claim(), _worsens_claim(), _disclosure_claim(), _staleness_claim()],
    }


def _validate(case, basis=None, consequence=None, rule_collisions=None, user_statements=()):
    return answer_provenance.validate_agent_case(
        case,
        basis=_basis() if basis is None else basis,
        consequence=_consequence() if consequence is None else consequence,
        rule_collisions=_rule_collisions() if rule_collisions is None else rule_collisions,
        user_statements=user_statements,
    )


def _rejects(fragment, case, **kwargs):
    try:
        _validate(case, **kwargs)
    except answer_provenance.AnswerProvenanceError as exc:
        assert fragment in str(exc), f"rejected for the wrong reason: wanted {fragment!r}, got {str(exc)!r}"
        return
    raise AssertionError(f"expected rejection containing {fragment!r}, but the payload was accepted")


def _schema(name):
    with open(os.path.join(SCHEMAS_DIR, name), encoding="utf-8") as f:
        return json.load(f)


# ───────────────────────── 1. unlabeled / multiply labelled ─────────────────────────

def test_claim_with_no_provenance_label_is_rejected():
    case = _valid_case()
    del case["against"][0]["provenance"]
    _rejects("no single valid provenance label", case)


def test_claim_with_multiple_provenance_labels_is_rejected():
    case = _valid_case()
    case["against"][0]["provenance"] = ["engine_fact", "public_fact"]
    _rejects("no single valid provenance label", case)


# ───────────────────────── 2. unresolved record anchor ─────────────────────────

def test_engine_fact_claim_missing_anchor_is_rejected():
    case = _valid_case()
    del case["against"][0]["anchor"]
    _rejects("carries no record anchor", case)


def test_engine_fact_claim_with_an_anchor_that_does_not_resolve_is_rejected():
    case = _valid_case()
    case["against"][0]["anchor"] = "consequence.after.nonexistent_field"
    _rejects("does not resolve", case)


def test_engine_fact_claim_anchored_at_a_container_is_rejected():
    """An anchor must resolve to one scalar fact, never a container -- the
    same "must resolve to one supplied fact" bar question_surface.py's
    _resolve_context_ref already holds grounding refs to."""
    case = _valid_case()
    case["against"][0]["anchor"] = "consequence.after"
    _rejects("does not resolve", case)


# ───────────────────────── 3. number mismatch ─────────────────────────

def test_engine_fact_claim_with_a_mismatched_number_is_rejected():
    case = _valid_case()
    case["against"][0]["claim"] = "This grows NVDA to 55% of your book."
    _rejects("quotes a number that does not match", case)


def test_engine_fact_claim_with_no_number_at_all_is_rejected():
    """A number-valued anchor with a claim that cites no number at all
    fails the same way as a wrong number -- silence is not a match."""
    case = _valid_case()
    case["against"][0]["claim"] = "This makes NVDA your largest position by far."
    _rejects("quotes a number that does not match", case)


# ───────────────────────── 4. public_fact missing citation ─────────────────────────

def test_public_fact_claim_missing_source_is_rejected():
    case = _valid_case()
    case["against"].append({"claim": "Reuters reported new export curbs on AI chips.",
                            "provenance": "public_fact", "as_of": "2026-07-20"})
    _rejects("missing source", case)


def test_public_fact_claim_missing_as_of_is_rejected():
    case = _valid_case()
    case["against"].append({"claim": "Reuters reported new export curbs on AI chips.",
                            "provenance": "public_fact", "source": "Reuters"})
    _rejects("missing as_of", case)


# ───────────────────────── 5. empty for/against ─────────────────────────

def test_empty_for_side_is_rejected():
    case = _valid_case()
    case["for"] = []
    _rejects("agent_case.for must be a non-empty list", case)


def test_empty_against_side_is_rejected():
    case = _valid_case()
    case["against"] = []
    _rejects("agent_case.against must be a non-empty list", case)


# ───────────────────────── 6. dropped basis/staleness or disclosure ─────────────────────────

def test_agent_case_that_drops_a_given_disclosure_is_rejected():
    case = _valid_case()
    case["against"] = [c for c in case["against"] if c.get("anchor") != "consequence.disclosures.0"]
    _rejects("disclosure cash_unreliable", case)


def test_agent_case_that_drops_the_staleness_disclosure_is_rejected():
    case = _valid_case()
    case["against"] = [c for c in case["against"] if c.get("anchor") != "basis.stale_days"]
    _rejects("basis stale", case)


def test_agent_case_over_a_declared_incomplete_basis_without_a_basis_claim_is_rejected():
    """Staleness is one of two ways a basis becomes material -- an
    incomplete/unverified book is the other, even at stale_days == 0."""
    case = _valid_case()
    case["against"] = [c for c in case["against"] if c.get("anchor") != "basis.stale_days"]
    _rejects("basis unverified", case,
             basis=_basis(stale_days=0, completeness="unverified"))


# ───────────── 7. reversed / omitted rule transition (#579) ─────────────
#
# The direction a case is built on is the thing that failed owner-live
# acceptance, and free prose has no mechanical encoding of "this trade
# improves it" a validator could check without scoring sentiment. So the
# transition rides beside the prose and is checked exactly -- the same shape
# source/as_of already impose on a public_fact claim.

def test_reversed_rule_effect_is_rejected():
    """The exact untruth #579 owns, in the direction the owner received it:
    the frozen transition is a worsening and the case declares the opposite.
    A boolean could not have caught the reverse of this -- both
    `improved_but_still_over` and `unchanged_existing_breach` are
    `worsens: false`."""
    case = _valid_case()
    case["against"][1]["rule_effect"] = "improved_but_still_over"
    _rejects("must declare rule_effect 'worsened_existing_breach'", case)


def test_an_improving_transition_claimed_as_a_breach_is_rejected():
    """The owner's own direction. The frozen row says this trade reduced an
    over-cap position; a case asserting it created or worsened the breach is
    refused before it is stored or shown."""
    collisions = [{"rule_id": "rule-1", "text": "Cap NVDA at 20%.",
                   "metric_key": "max_pos_pct", "problem_key": "oversize",
                   "state": "already_over", "worsens": False,
                   "rule_effect": "improved_but_still_over", "limit": 0.20,
                   "limit_source": "user_cap"}]
    for wrong in ("new_breach", "worsened_existing_breach"):
        case = _valid_case()
        case["against"][1] = _effect_claim(
            claim="This puts NVDA over the 20% line you set.", rule_effect=wrong)
        _rejects("must declare rule_effect 'improved_but_still_over'", case,
                 rule_collisions=collisions)
    # ... and the truthful declaration on the same frozen row is accepted, so
    # the check above is a direction test rather than a blanket refusal.
    case = _valid_case()
    case["against"][1] = _effect_claim(
        claim="This cuts NVDA back toward the 20% line you set without reaching it.",
        rule_effect="improved_but_still_over")
    _validate(case, rule_collisions=collisions)


def test_omitted_rule_effect_is_rejected():
    case = _valid_case()
    del case["against"][1]["rule_effect"]
    _rejects("declares none", case)


def test_rule_effect_outside_a_collision_anchor_is_rejected():
    """The inverse strictness check: a transition declared about something
    that is not a rule collision has nothing to be checked against."""
    case = _valid_case()
    case["against"][0]["rule_effect"] = "new_breach"  # anchored at consequence.after.max_pct
    _rejects("carries rule_effect outside a rule collision anchor", case)


def test_a_row_whose_effect_contradicts_its_own_state_is_refused_by_the_gate():
    """#579's compatibility clause on the gate's side: the frozen row itself
    is refused, before any claim about it is judged."""
    collisions = [{"rule_id": "rule-1", "text": "Cap NVDA at 25%.",
                   "metric_key": "max_pos_pct", "problem_key": "oversize",
                   "state": "clear", "worsens": None,
                   "rule_effect": "worsened_existing_breach", "limit": 0.25,
                   "limit_source": "user_cap"}]
    _rejects("cannot accompany state", _valid_case(), rule_collisions=collisions)


def test_a_legacy_row_still_has_its_direction_checked():
    """A row recorded before rule_effect existed carries the direction only
    as `worsens`, and the older gate still runs on it -- dropping it the
    moment a newer one exists would leave a replayed row unguarded."""
    _validate(_legacy_case(), rule_collisions=_legacy_rule_collisions())

    reversed_case = _legacy_case()
    reversed_case["against"][1]["worsens"] = False
    _rejects("worsens is reversed", reversed_case, rule_collisions=_legacy_rule_collisions())

    omitted = _legacy_case()
    del omitted["against"][1]["worsens"]
    _rejects("omits the worsens direction", omitted,
             rule_collisions=_legacy_rule_collisions())


def test_worsens_beside_a_rule_effect_is_accepted_but_still_checked():
    """The same fact said less precisely. Accepted alongside the effect on a
    row that has a direction, and never believed over the frozen one."""
    case = _valid_case()
    case["against"][1]["worsens"] = True
    _validate(case)

    reversed_case = _valid_case()
    reversed_case["against"][1]["worsens"] = False
    _rejects("worsens is reversed", reversed_case)


def test_worsens_field_outside_a_material_anchor_is_rejected():
    """The inverse strictness check: worsens must not appear on a claim
    that is not describing a material already_over collision."""
    case = _valid_case()
    case["against"][0]["worsens"] = True  # anchored at consequence.after.max_pct, not a rule
    _rejects("carries worsens outside a material already_over anchor", case)


# ───────────── 7b. the engine's own vocabulary is not the answer ─────────────

def test_a_claim_that_narrates_an_internal_token_is_rejected():
    """#674/#676's half of the same owner-live failure, on the same surface.
    An implementation identifier is not a limitation the user can act on."""
    for token in ("already_over", "improved_but_still_over", "cash_unreliable",
                  "partial_book", "unavailable_cost"):
        case = _valid_case()
        case["against"].append({"claim": f"The engine reports {token} for this book.",
                                "provenance": "agent_judgment"})
        _rejects("narrates the engine's internal vocabulary", case)


def test_the_leak_check_does_not_fire_on_ordinary_language():
    """The check is deliberately snake_case-only, the same discipline
    question_surface._assert_no_internal_leak already uses: a rule that
    rejected the words 'cost basis' or 'clear' would reject correct copy
    rather than a leak."""
    case = _valid_case()
    case["against"].append({
        "claim": "These weights are computed on cost basis, not on current prices, and "
                 "one holding sits outside that denominator entirely.",
        "provenance": "agent_judgment"})
    _validate(case)


def test_the_leak_universe_is_the_engines_own_enums():
    """Drift discipline: the tokens refused are read off consequence.py's
    vocabularies, so a new disclosure key or effect is covered the day it is
    added rather than the day someone remembers this list."""
    import consequence as consequence_engine
    declared = set(consequence_engine.COLLISION_STATES + consequence_engine.RULE_EFFECTS
                   + consequence_engine.DISCLOSURES + consequence_engine.EXCLUSION_REASONS
                   + consequence_engine.LIMIT_SOURCES)
    assert set(answer_provenance._INTERNAL_TOKENS) == {t for t in declared if "_" in t}
    assert "clear" not in answer_provenance._INTERNAL_TOKENS, \
        "a single word that is also ordinary language is deliberately not checked"


# ───────────────────────── 8. user statement promoted to public_fact ─────────────────────────

def test_user_statement_promoted_to_public_fact_is_rejected():
    statement = "I want to buy because my neighbor said NVDA will 10x."
    case = _valid_case()
    case["against"].append({"claim": statement, "provenance": "public_fact",
                            "source": "Neighbor conversation", "as_of": "2026-07-29"})
    _rejects("restates what the user said as public_fact", case, user_statements=[statement])


def test_user_statement_with_different_whitespace_is_still_caught():
    """Normalized (whitespace-collapsed, case-folded) equality, not brittle
    byte-for-byte matching -- see the module docstring's stated limitation
    that a genuine paraphrase is NOT caught (deferred as NLI/factuality
    scoring), which this test does not attempt to disprove."""
    statement = "I want to buy because my neighbor said NVDA will 10x."
    case = _valid_case()
    case["against"].append({"claim": "  I want to buy  because my neighbor said NVDA will 10x. ",
                            "provenance": "public_fact", "source": "Neighbor conversation",
                            "as_of": "2026-07-29"})
    _rejects("restates what the user said as public_fact", case, user_statements=[statement])


# ───────────────────────── acceptance: a well-formed answer ─────────────────────────

def test_a_well_formed_agent_case_is_accepted():
    """The counterpart every rejection test above needs: proof this is a
    validator, not a function that rejects everything. Exercises all four
    checks positively in one payload -- anchor resolution, number
    matching, the worsens direction, and both disclosure-coverage paths."""
    try:
        _validate(_valid_case())
    except answer_provenance.AnswerProvenanceError as exc:
        raise AssertionError(f"a well-formed agent_case must be accepted, got: {exc}") from exc


def test_a_context_free_case_with_no_open_disclosures_or_staleness_needs_no_basis_or_disclosure_claim():
    """Preserve issue #414's frozen parity requirement ("context-free
    consider compatibility"): when nothing is material, an agent_case
    naming only agent_judgment claims still validates."""
    case = {"for": [_judgment_claim()], "against": [_judgment_claim()]}
    try:
        _validate(case, basis=_basis(stale_days=0), consequence=_consequence(disclosures=[]),
                 rule_collisions=[])
    except answer_provenance.AnswerProvenanceError as exc:
        raise AssertionError(f"a case with nothing material to disclose must be accepted, got: {exc}") from exc


# ───────────────────────── drift guards ─────────────────────────

def test_provenance_vocabulary_matches_reviews_agent_case_provenance():
    """answer_provenance.PROVENANCE is a restatement of
    review.AGENT_CASE_PROVENANCE, not an import (see the module docstring
    for why importing review.py back would be circular once Wave B wires
    this module in). This is the mechanical cross-check that keeps the two
    from silently drifting apart, the same discipline
    test_consider.py::test_consider_decisions_constant_matches_the_schemas_decision_enum
    already applies to CONSIDER_DECISIONS."""
    assert answer_provenance.PROVENANCE == review_engine.AGENT_CASE_PROVENANCE


_CLAIM_DEF_BY_PROVENANCE = {
    "engine_fact": "engineFactClaim",
    "public_fact": "publicFactClaim",
    "agent_judgment": "judgmentClaim",
}


def test_valid_case_matches_the_new_schemas_declared_shape():
    """Spot-check a validator-accepted payload against
    schemas/answer-provenance.schema.json -- the same "no jsonschema
    dependency, hand-pin the vocabulary" idiom test_consider.py's own
    _check_evaluation_shape already uses for the sibling schema."""
    schema = _schema("answer-provenance.schema.json")
    case = _valid_case()
    for side in ("for", "against"):
        for claim in case[side]:
            defn = schema["$defs"][_CLAIM_DEF_BY_PROVENANCE[claim["provenance"]]]
            required = set(defn["required"])
            allowed = set(defn["properties"])
            assert required <= set(claim), f"claim is missing a schema-required field: {required - set(claim)}"
            assert set(claim) <= allowed, f"claim carries a field the schema does not declare: {set(claim) - allowed}"


def test_answer_provenance_error_is_a_value_error():
    """Matches every other hand-rolled validator's exception convention in
    this engine (ConditionError, PriceFeedError, ReviewError, ...)."""
    assert issubclass(answer_provenance.AnswerProvenanceError, ValueError)


# ─────────────────────────────── runner ───────────────────────────────

def test_user_statements_cannot_be_forgotten_into_disabling_its_own_check():
    """The one argument whose absence would silently switch a check off.

    Every check in this module fails an answer that violates it. This one
    fails nothing when `user_statements` is empty — there is no statement to
    have been promoted. So a default of `()` would make "I captured nothing"
    and "I forgot the argument" the same keystroke, and only the first is a
    real answer. Nothing in the suite would notice: every test here passes
    the argument explicitly, so case 8 would go on passing while the
    integration validated every real answer with the check inert.

    Required, therefore. `()` stays legal and stays a decision someone made.
    """
    import inspect
    parameter = inspect.signature(
        answer_provenance.validate_agent_case).parameters["user_statements"]
    assert parameter.default is inspect.Parameter.empty, (
        "validate_agent_case's user_statements grew a default. A caller that "
        "omits it would disable the user-statement-promoted-to-public_fact "
        "check for every answer, and this suite would stay green because its "
        "own tests all pass it explicitly. Keep it required; pass () to mean "
        "'nothing captured'.")


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
    total = len(_tests())
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
