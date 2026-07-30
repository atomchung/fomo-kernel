#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probes for the question-episode bank's mechanical checkers (#417).

`evals/run_episodes.py` replays real misses; this file proves the six checkers
it replays them with still look at something. Every probe is a pair: a clean
input the checker must stay quiet on, and one minimal mutation it must catch.
A checker that passes both halves of its pair cannot be silently a no-op —
docs/maintainer-guide.md's rule is that a new checker lands with proof
its matching mutation fails, and `eval-design.md` section "Mutation testing" says the same.

Same split as `tests/test_checkers_offline.py` for the card checkers: the bank
holds converted misses only, and synthetic checker probes live here rather than
inflating the bank with cases no user ever hit.

No engine subprocess: the checkers take a facts dict, so the probes build one
directly and stay hermetic. `evals/run_episodes.py` covers the real
prepare-derived path on every run.
"""
import json
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))
import run_episodes as R  # noqa: E402
import judge_episodes as J  # noqa: E402  (imports `anthropic` lazily, inside main)

EPISODE_DIR = ROOT / "evals" / "episodes"

# One synthetic engine-fact set, standing in for a prepared plan. Values are
# invented; nothing here comes from a real ledger.
FACTS = {
    "locale": "en",
    "engine_text": "This period: your largest single position INTC is 43% of the portfolio.\n"
                   "Cap any single position at 20%. Trim if it goes over, and do not add.",
    "numbers": {20.0, 43.0, 6.0, 3560.0},
    "dates": {"2024-05-08"},
    "tokens": {"CVS", "INTC", "USD"},
    "identifiers": {"position_sizing", "deliberate_plan"},
    "foreign_labels": set(),
    "candidates": {
        "candidate_0": {"id": "candidate_0", "dim": "position_sizing",
                        "grounding": "This period: your largest single position INTC is 43% of the portfolio.",
                        "rule": "Cap any single position at 20%. Trim if it goes over, and do not add."},
        "candidate_1": {"id": "candidate_1", "dim": "exit_discipline",
                        "rule": "Before exiting, name the fact that completed or broke the thesis."},
    },
    "honesty_keys": {"cash_reliability", "price_source"},
    "question_kinds": {"headline_motive"},
}


def _facts(**over):
    merged = dict(FACTS)
    merged.update(over)
    return merged


# ── number_provenance ────────────────────────────────────────────────────────

def test_number_provenance_accepts_engine_numbers_and_rounding():
    answer = {"prose": "The cap is 20% and the basis was USD 3,560 on 2024-05-08."}
    assert R.check_number_provenance(answer, FACTS) == []


def test_number_provenance_catches_a_derived_figure():
    """The mutation that matters: arithmetic the engine never emitted."""
    answer = {"prose": "Those two together are 63% of the book."}
    findings = R.check_number_provenance(answer, FACTS)
    assert findings and "63" in findings[0], findings


def test_number_provenance_catches_an_unsourced_date():
    findings = R.check_number_provenance({"prose": "You exited on 2024-05-09."}, FACTS)
    assert findings and "2024-05-09" in findings[0], findings


def test_number_provenance_reads_every_surface_not_only_prose():
    """A number can arrive on an option label; checking prose alone is fake-green
    type 4 (harness surface is not the delivery surface)."""
    answer = {"presented_options": [{"maps_to": "candidate_0", "label": "Cap at 35%",
                                     "description": ""}]}
    findings = R.check_number_provenance(answer, FACTS)
    assert findings and "option[0].label" in findings[0], findings


# ── honesty_coverage ─────────────────────────────────────────────────────────

def test_honesty_coverage_accepts_a_digit_free_disclosure():
    episode = {"must_disclose": ["cash_reliability"]}
    answer = {"discloses": {"cash_reliability": "Cash has no anchor here, so the account view stays closed."}}
    assert R.check_honesty_coverage(episode, answer, FACTS) == []


def test_honesty_coverage_catches_silence():
    episode = {"must_disclose": ["cash_reliability"]}
    findings = R.check_honesty_coverage(episode, {"prose": "All good."}, FACTS)
    assert findings and "disclosed nowhere" in findings[0], findings


def test_honesty_coverage_catches_a_numeric_claim_in_the_disclosure():
    episode = {"must_disclose": ["cash_reliability"]}
    answer = {"discloses": {"cash_reliability": "Cash is off by about thirty percent here."}}
    findings = R.check_honesty_coverage(episode, answer, FACTS)
    assert findings and "numeric claim" in findings[0], findings


def test_honesty_coverage_catches_one_sentence_reused_for_two_keys():
    episode = {"must_disclose": ["cash_reliability", "price_source"]}
    shared = "This part of the data has a known limitation."
    answer = {"discloses": {"cash_reliability": shared, "price_source": shared}}
    findings = R.check_honesty_coverage(episode, answer, FACTS)
    assert any("byte-identical" in message for message in findings), findings


def test_honesty_coverage_catches_a_key_the_fixture_no_longer_triggers():
    """The drift alarm: an episode must fail loudly rather than grade nothing."""
    episode = {"must_disclose": ["etf_metadata"]}
    answer = {"discloses": {"etf_metadata": "Fund metadata is incomplete."}}
    findings = R.check_honesty_coverage(episode, answer, FACTS)
    assert any("no longer triggers" in message for message in findings), findings


# ── privacy_trace ────────────────────────────────────────────────────────────

def test_privacy_trace_accepts_fixture_tokens():
    assert R.check_privacy_trace({"prose": "CVS and INTC in USD."}, FACTS) == []


def test_privacy_trace_catches_an_untraceable_symbol():
    findings = R.check_privacy_trace({"prose": "Also your NFLX position."}, FACTS)
    assert findings and "NFLX" in findings[0], findings


def test_privacy_trace_catches_the_internal_position_id_format():
    findings = R.check_privacy_trace({"prose": "See CVS#2024-05-08#1."}, FACTS)
    assert any("position-id" in message for message in findings), findings


# ── surface_hygiene ──────────────────────────────────────────────────────────

def test_surface_hygiene_accepts_copy_catalog_wording():
    assert R.check_surface_hygiene({"prose": "The plan was set before the trade."}, FACTS) == []


def test_surface_hygiene_catches_a_snake_case_identifier():
    findings = R.check_surface_hygiene({"prose": "Pick deliberate_plan."}, FACTS)
    assert findings and "deliberate_plan" in findings[0], findings


def test_surface_hygiene_catches_an_internal_field_name():
    """Reuses check_card's card-face ban list, so one definition covers both."""
    findings = R.check_surface_hygiene({"prose": "max_pos_pct is high."}, FACTS)
    assert findings, findings


# ── locale_purity ────────────────────────────────────────────────────────────

def test_locale_purity_catches_cjk_on_an_en_surface():
    findings = R.check_locale_purity({"prose": "Your largest position 佔 43%."}, FACTS)
    assert findings and "CJK" in findings[0], findings


def test_locale_purity_accepts_cjk_on_a_localized_surface():
    facts = _facts(locale="zh-TW", foreign_labels={"position sizing"})
    assert R.check_locale_purity({"prose": "本期最大單一部位偏重。"}, facts) == []


def test_locale_purity_catches_an_untranslated_metric_label():
    """#262: the label arrived in English inside a Traditional Chinese session."""
    facts = _facts(locale="zh-TW", foreign_labels={"position sizing"})
    findings = R.check_locale_purity({"prose": "這次 position sizing 背後的原因？"}, facts)
    assert findings and "position sizing" in findings[0], findings


def test_locale_purity_keeps_a_localized_label_that_embeds_english():
    """zh-TW's own dimension label is "部位 sizing" — a blanket Latin ban would
    fail the engine's own copy, which is why only translated labels count."""
    facts = _facts(locale="zh-TW", foreign_labels={"position sizing"})
    assert R.check_locale_purity({"prose": "這次「部位 sizing」背後的原因？"}, facts) == []


# ── condition_integrity ──────────────────────────────────────────────────────

_CONDITION = {
    "criterion": "sell if quarterly revenue growth drops under 30%",
    "query": "what was the most recent quarterly revenue, and the year-ago quarter?",
    "threshold": {"value": 30, "unit": "%", "direction": "below"},
    "observation": {"value": 38.0, "as_of": "2026-05-20", "source": "quarterly results release"},
}
_SHOWN_BACK = ("Recorded, in your words: sell if quarterly revenue growth drops under 30%. "
               "The latest reported quarter is 38% against the year-ago quarter.")


def _answer(prose=_SHOWN_BACK, **condition_over):
    condition = dict(_CONDITION)
    for key, value in condition_over.items():
        if value is None:
            condition.pop(key, None)
        else:
            condition[key] = value
    return {"prose": prose, "condition": condition}


def test_condition_integrity_accepts_a_neutral_query_with_the_basis_shown_back():
    assert R.check_condition_integrity(_answer(), FACTS) == []


def test_condition_integrity_catches_the_criterion_restated_as_a_yes_no_query():
    """#412's named failure: folding the comparison into the lookup steers
    retrieval toward confirmation."""
    findings = R.check_condition_integrity(
        _answer(query="did quarterly revenue growth fall below 30%?"), FACTS)
    assert findings and "engine refuses" in findings[0], findings


def test_condition_integrity_catches_a_paraphrased_criterion():
    findings = R.check_condition_integrity(
        _answer(prose="Recorded: revenue growth below the 30% line. It is 38% today."), FACTS)
    assert findings and "no surface verbatim" in findings[0], findings


def test_condition_integrity_catches_a_basis_that_was_never_shown_back():
    """A basis the user cannot see is one they cannot correct — the whole reason
    the lookup happens in the same exchange as the commitment."""
    findings = R.check_condition_integrity(
        _answer(prose="Recorded, in your words: sell if quarterly revenue growth drops "
                      "under 30%. I'll compare each quarter against the year-ago quarter."),
        FACTS)
    assert findings and "never shown back" in findings[0], findings


def test_condition_integrity_catches_a_figure_invented_for_an_unmapped_slot():
    """Nothing was found, so a number in the answer came from the answer."""
    findings = R.check_condition_integrity(
        _answer(prose="Recorded, in your words: sell if quarterly revenue growth drops "
                      "under 30%. It sits around 38% today.", observation=None), FACTS)
    assert findings and "neither the user's own line nor anything the lookup returned" in findings[0], \
        findings


def test_condition_integrity_catches_a_figure_invented_beside_a_real_baseline():
    """The researched tier's numbers come from outside the engine, so
    `number_provenance` cannot trace them. What they can be traced to is this
    slot's own record — the user's line, and what the lookup returned."""
    findings = R.check_condition_integrity(
        _answer(prose=_SHOWN_BACK + " It was 55% two years ago and should reach 12% next year."),
        FACTS)
    assert findings and "12.0" in findings[0] and "55.0" in findings[0], findings


def test_condition_integrity_does_not_mistake_a_quarter_label_for_a_figure():
    """The check reads both sides with the engine's own number reader. Scanning
    prose with a looser one made it red an honest answer that quoted the user's
    criterion verbatim — the very thing it demands one line earlier."""
    criterion = "sell if Q3 net promoter score falls under 40"
    answer = {"prose": f"Recorded, in your words: {criterion}. I could not find a published "
                       "figure, so I cannot watch this one.",
              "condition": {"criterion": criterion,
                            "query": "what is the most recently published net promoter score?",
                            "threshold": {"value": 40, "unit": "points", "direction": "below"}}}
    assert R.check_condition_integrity(answer, FACTS) == []


def test_condition_integrity_accepts_an_unmapped_slot_said_out_loud():
    assert R.check_condition_integrity(
        _answer(prose="Recorded, in your words: sell if quarterly revenue growth drops under "
                      "30%. I could not find a published figure, so I cannot watch this one.",
                observation=None), FACTS) == []


def test_condition_integrity_abstains_when_the_answer_carries_no_envelope():
    _findings, looked = R.run_check("condition_integrity", {}, {"prose": "no condition here"}, FACTS)
    assert looked is False, "a check with nothing to inspect has abstained, not passed"


# ── condition_check_integrity ────────────────────────────────────────────────

_THIS_PERIOD = {"lookup_status": "ok",
                "observation": {"value": 21.0, "as_of": "2026-08-20", "source": "10-Q",
                                "period": "FY2027Q2", "document": "10-Q 2026-08-20"}}
_TWO_SIDED = ("You set this: sell if quarterly revenue growth drops under 30%. This period it "
              "came back at 21%, against the 38% on record when you wrote the rule. If you think "
              "that figure is measuring something else, say so and I will record it as not "
              "crossed.")


def _check_answer(prose=_TWO_SIDED, condition=None, **check_over):
    envelope = json.loads(json.dumps(_THIS_PERIOD))
    for key, value in check_over.items():
        if value is None:
            envelope.pop(key, None)
        else:
            envelope[key] = value
    return {"prose": prose,
            "condition": condition if condition is not None else dict(_CONDITION),
            "condition_check": envelope}


def test_condition_check_integrity_accepts_a_two_sided_answer_whose_numbers_trace():
    assert R.check_condition_check_integrity(_check_answer(), FACTS) == []


def test_condition_check_integrity_catches_a_figure_the_lookup_never_returned():
    """The shape a one-sided push takes in practice: the argument needs a fact
    the record does not have, so it reaches for one."""
    findings = R.check_condition_check_integrity(
        _check_answer(prose=_TWO_SIDED + " It was 34.5% last quarter, so this is a trend."), FACTS)
    assert findings and "34.5" in findings[0], findings


def test_condition_check_integrity_catches_a_reading_that_was_never_shown_back():
    findings = R.check_condition_check_integrity(
        _check_answer(prose="You set this: sell if quarterly revenue growth drops under 30%. "
                            "Your line was crossed this period."), FACTS)
    assert findings and "never shown back" in findings[0], findings


def test_condition_check_integrity_catches_a_failed_lookup_told_as_a_clean_one():
    """The failure the whole tier exists to prevent: a period nobody could look
    at, narrated as a period where everything was fine."""
    findings = R.check_condition_check_integrity(
        _check_answer(prose="Quarterly revenue growth is still clear of your 30% line at 38%.",
                      lookup_status="failed", observation=None,
                      reason="the quarter has not been reported"), FACTS)
    assert findings, findings
    assert any("no surface says so" in f for f in findings), findings
    assert any("still clear" in f for f in findings), findings


def test_condition_check_integrity_accepts_a_failed_lookup_said_out_loud():
    assert R.check_condition_check_integrity(
        _check_answer(prose="I could not check this one this period: the quarter has not been "
                            "reported. It is currently blind rather than clear.",
                      lookup_status="failed", observation=None,
                      reason="the quarter has not been reported"), FACTS) == []


def test_condition_check_integrity_defers_to_the_engines_own_check_validator():
    """The gate that ships is the gate that grades. A summary sent for a numeric
    line is refused here for the same reason a live review refuses it — and the
    rule is never restated in this file, so the two cannot drift."""
    findings = R.check_condition_check_integrity(
        _check_answer(observation={"summary": "growth fell", "as_of": "2026-08-20",
                                   "source": "10-Q"}), FACTS)
    assert findings and "refuses this result" in findings[0], findings


def test_condition_check_integrity_refuses_an_envelope_that_reports_a_verdict():
    """External review, BLOCK: `user_response` is what the USER said, and an
    envelope may not report it — otherwise an episode (and a live agent) could
    record `overridden` for a question nobody was shown. The refusal comes from
    the engine's own validator, so the bank and the runtime cannot disagree."""
    findings = R.check_condition_check_integrity(
        _check_answer(user_response={"answer": "overridden", "answered_at": "2026-08-26"}), FACTS)
    assert findings and "refuses this result" in findings[0], findings
    assert "must not carry user_response" in findings[0], findings


def test_condition_check_integrity_does_not_mistake_a_quarter_label_for_a_figure():
    """Both sides read with the engine's own number reader. Scanning prose with
    a looser one would redden an answer that quoted the user's criterion
    verbatim — the mistake its sibling check made first."""
    condition = dict(_CONDITION, criterion="sell if Q3 revenue growth drops under 30%")
    answer = _check_answer(
        prose="You set this: sell if Q3 revenue growth drops under 30%. This period: 21%.",
        condition=condition)
    assert R.check_condition_check_integrity(answer, FACTS) == []


def test_condition_check_integrity_abstains_when_the_answer_carries_no_check():
    _findings, looked = R.run_check("condition_check_integrity", {},
                                    {"prose": "no check here"}, FACTS)
    assert looked is False, "a check with nothing to inspect has abstained, not passed"


# ── the interlocks ───────────────────────────────────────────────────────────

def test_a_declared_check_with_nothing_to_inspect_reports_no_data():
    """Interlock 2, at the checker layer: honesty_coverage in front of an episode
    that puts no key in scope has abstained, not passed."""
    _findings, looked = R.run_check("honesty_coverage", {}, {"prose": "nothing in scope"}, FACTS)
    assert looked is False


def test_replay_fails_an_episode_whose_declared_check_abstains():
    """Interlock 2, at the layer that consumes the signal.

    The probe above only proves ``run_check`` reports "nothing to inspect"; it
    says nothing about whether ``replay`` acts on it. The 2026-07-26 mutation
    dance for this PR found exactly that gap — neutering ``replay``'s
    ``if not looked`` left the suite green, which is fake-green type 5 (right
    assertion, wrong layer; docs/development-guide.md section 2)."""
    episode = {
        "id": "EP-000", "checks": ["honesty_coverage"], "must_disclose": [],
        "question": {"asked_by": "user", "kind": "free_form", "text": "?"},
        "answers": [{"id": "a", "expect": "pass", "prose": "nothing in scope"}],
    }
    failures, observed, _unmapped = R.replay(episode, FACTS)
    assert any("nothing to inspect" in message for message in failures), failures
    assert observed["honesty_coverage"] == set(), (
        "an abstention must not count as an observed outcome for coverage")


def test_replay_rejects_an_answer_that_fails_the_wrong_check():
    """Interlock 1's sharp edge: failing *a* check is not evidence; failing the
    recorded check is. Without this, any later regression would look like the
    episode still working."""
    episode = {
        "id": "EP-000", "checks": ["number_provenance", "surface_hygiene"],
        "question": {"asked_by": "user", "kind": "free_form", "text": "?"},
        "answers": [{"id": "miss", "expect": "fail", "fails": ["surface_hygiene"],
                     "prose": "Those two together are 63% of the book."}],
    }
    failures, _observed, _unmapped = R.replay(episode, FACTS)
    assert failures and "but the episode records" in failures[0], failures


def test_replay_flags_a_fixture_that_stopped_posing_the_question():
    episode = {
        "id": "EP-000", "checks": ["number_provenance"],
        "question": {"asked_by": "engine", "kind": "initial_thesis", "text": "?"},
        "answers": [{"id": "a", "expect": "pass", "prose": "The cap is 20%."}],
    }
    failures, _observed, _unmapped = R.replay(episode, FACTS)
    assert any("no longer queues" in message for message in failures), failures


def test_replay_flags_a_commitment_episode_without_candidates():
    episode = {
        "id": "EP-000", "checks": ["number_provenance"],
        "question": {"asked_by": "engine", "kind": "commitment_choice", "text": "?"},
        "answers": [{"id": "a", "expect": "pass", "prose": "The cap is 20%."}],
    }
    failures, _observed, _unmapped = R.replay(episode, _facts(candidates={}))
    assert any("no candidate rule" in message for message in failures), failures


def test_unmapped_reports_prose_the_checks_never_inspected():
    """#412's standard turned on the harness: what could not be decided is
    reported as `unmapped`, not silently dropped and not guessed."""
    answer = {"prose": "This felt like the right moment to step back."}
    ungraded = R.unmapped_claims(answer, FACTS)
    assert ungraded and ungraded[0][0] == "prose", ungraded


def test_unmapped_does_not_report_an_engine_quoted_span():
    """A verbatim engine sentence was inspected — by the checks that own it."""
    answer = {"prose": "Cap any single position at 20%"}
    assert R.unmapped_claims(answer, FACTS) == []


def test_unmapped_never_turns_into_a_failure():
    """It is a report, not a gate. A green replay must stay green while saying
    out loud how much of the answer nothing verified."""
    episode = {
        "id": "EP-000", "checks": ["surface_hygiene"],
        "question": {"asked_by": "user", "kind": "free_form", "text": "?"},
        "answers": [{"id": "miss", "expect": "fail", "fails": ["surface_hygiene"],
                     "prose": "The reason was deliberate_plan, which felt right at the time."},
                    {"id": "ok", "expect": "pass",
                     "prose": "This felt like the right moment to step back."}],
    }
    failures, _observed, unmapped = R.replay(episode, FACTS)
    assert failures == [], failures
    assert unmapped and "graded on hygiene only" in unmapped[0], unmapped


# ── the question-consumer gate (#429) ────────────────────────────────────────

# A plan carrying the state_snapshot keys the real engine emits, so the gate's
# `state:` proofs have something truthful to check against.
CONSUMER_PLAN = {"state_snapshot": {"thesis_states": [], "headline_motive_events": [],
                                    "due_revisits": [], "problem_stats": {},
                                    "condition_slots_due": []}}


def _with_consumers(consumers, unwired):
    """Swap the declarations for one probe, then put them back.

    Both incoming maps are copied first: a probe legitimately passes the live
    dict (``_with_consumers(R.QUESTION_CONSUMERS, {})`` to change only the
    ratchet), and clearing the target before updating from the same object would
    silently wipe it — which made one probe assert against an empty registry
    instead of the mutation it meant to test."""
    incoming = (dict(consumers), dict(unwired))
    saved = (dict(R.QUESTION_CONSUMERS), dict(R.KNOWN_UNWIRED))

    class Swap:
        def __enter__(self):
            R.QUESTION_CONSUMERS.clear()
            R.QUESTION_CONSUMERS.update(incoming[0])
            R.KNOWN_UNWIRED.clear()
            R.KNOWN_UNWIRED.update(incoming[1])

        def __exit__(self, *_exc):
            R.QUESTION_CONSUMERS.clear()
            R.QUESTION_CONSUMERS.update(saved[0])
            R.KNOWN_UNWIRED.clear()
            R.KNOWN_UNWIRED.update(saved[1])
    return Swap()


def test_question_consumers_passes_on_the_shipped_declarations():
    """The gate is green today only because the two unwired kinds are tracked."""
    failures, notes = R.question_consumers(CONSUMER_PLAN)
    assert failures == [], failures
    assert any("initial_thesis" in note for note in notes), notes
    assert any("exit_consistency" in note for note in notes), notes


def test_question_consumers_catches_an_undeclared_question_kind():
    """The kind list comes from the schema, so forgetting to declare one fails.
    This is the mutation that matters: a new question shipping with no consumer."""
    consumers = {k: v for k, v in R.QUESTION_CONSUMERS.items() if k != "revisit"}
    with _with_consumers(consumers, R.KNOWN_UNWIRED):
        failures, _notes = R.question_consumers(CONSUMER_PLAN)
    assert any("'revisit'" in f and "no declared consumer" in f for f in failures), failures


def test_question_consumers_catches_an_unverifiable_card_claim():
    consumers = dict(R.QUESTION_CONSUMERS)
    consumers["revisit"] = ("a_sink_no_renderer_mentions", ("card",))
    with _with_consumers(consumers, R.KNOWN_UNWIRED):
        failures, _notes = R.question_consumers(CONSUMER_PLAN)
    assert any("appears nowhere in card_renderer.py" in f for f in failures), failures


def test_question_consumers_catches_an_unverifiable_state_claim():
    consumers = dict(R.QUESTION_CONSUMERS)
    consumers["revisit"] = ("revisit_resolutions", ("state:a_key_no_plan_carries",))
    with _with_consumers(consumers, R.KNOWN_UNWIRED):
        failures, _notes = R.question_consumers(CONSUMER_PLAN)
    assert any("does not carry" in f for f in failures), failures


def test_question_consumers_refuses_an_untracked_unwired_kind():
    """No `reflection_only` state: an answer nothing reads fails unless an issue
    owns it. Owner ruling 2026-07-26 — the agent reads state every review, so a
    missing consumer means nobody wired it, not that it cannot be wired."""
    with _with_consumers(R.QUESTION_CONSUMERS, {}):
        failures, _notes = R.question_consumers(CONSUMER_PLAN)
    assert any("not a tracked defect" in f for f in failures), failures


def test_question_consumers_tightens_when_a_tracked_kind_gets_wired():
    """The ratchet only turns one way: a kind that gains a consumer must leave
    the list, or the run says so."""
    consumers = dict(R.QUESTION_CONSUMERS)
    consumers["initial_thesis"] = ("headline_motive_events", ("card",))
    with _with_consumers(consumers, R.KNOWN_UNWIRED):
        failures, _notes = R.question_consumers(CONSUMER_PLAN)
    assert any("is now wired" in f and "KNOWN_UNWIRED" in f for f in failures), failures


def test_question_consumers_fails_when_the_schema_stops_exposing_kinds():
    """The gate must not silently pass when its own input disappears."""
    saved = R.SKILL_DIR
    try:
        R.SKILL_DIR = pathlib.Path("/nonexistent-skill-dir")
        try:
            failures, _notes = R.question_consumers(CONSUMER_PLAN)
        except FileNotFoundError:
            return  # a hard error is also a refusal to pass
        assert failures, "an unreadable schema must not read as a green gate"
    finally:
        R.SKILL_DIR = saved


# ── the recorded-but-ungraded (unmapped) episode shape ───────────────────────

def test_ungraded_episode_is_valid_when_it_names_what_it_waits_on():
    episode = _episode(checks=[], blocked_on="#414",
                       answers=[{"id": "miss", "expect": "fail", "prose": "x"}])
    assert R.validate_episode(episode, "probe.json") == []


def test_ungraded_episode_without_blocked_on_is_rejected():
    episode = _episode(checks=[], answers=[{"id": "miss", "expect": "fail", "prose": "x"}])
    problems = R.validate_episode(episode, "probe.json")
    assert any("blocked_on" in message for message in problems), problems


def test_ungraded_episode_may_not_name_fails():
    episode = _episode(checks=[], blocked_on="#414",
                       answers=[{"id": "miss", "expect": "fail",
                                 "fails": ["number_provenance"], "prose": "x"}])
    problems = R.validate_episode(episode, "probe.json")
    assert any("no check to name yet" in message for message in problems), problems


def test_graded_episode_may_not_claim_blocked_on():
    problems = R.validate_episode(_episode(blocked_on="#414"), "probe.json")
    assert any("either graded or waiting" in message for message in problems), problems


def test_coverage_report_demands_both_outcomes_per_check():
    """Interlock 3, the persona-sweep lesson: a check nobody ever makes fail is
    not evidence that it works."""
    failures, _notes = R.coverage_report({"number_provenance": {"pass"}}, {"number_provenance"})
    assert failures and "no episode makes number_provenance fail" in failures[0], failures
    failures, _notes = R.coverage_report({"number_provenance": {"pass", "fail"}},
                                         {"number_provenance"})
    assert failures == []


# ── the bank's own structural gates ──────────────────────────────────────────

def _episode(**over):
    base = {
        "id": "EP-900", "title": "probe", "moment": "post_card",
        "source": {"kind": "dogfood_miss", "refs": ["#1"], "date": "2026-07-26"},
        "fixture": {"trades": "skills/fomo-kernel/mock/sample_value.csv",
                    "route": "first_review", "locale": "en"},
        "question": {"asked_by": "user", "kind": "free_form", "text": "?"},
        "checks": ["number_provenance"],
        "answers": [{"id": "miss", "expect": "fail", "fails": ["number_provenance"],
                     "prose": "63%"},
                    {"id": "ok", "expect": "pass", "prose": "20%"}],
    }
    base.update(over)
    return base


def test_valid_probe_episode_has_no_structural_problems():
    assert R.validate_episode(_episode(), "probe.json") == []


def test_episode_with_no_failing_answer_is_rejected():
    """An all-passing episode proves nothing; the loader refuses it."""
    answers = [{"id": "ok", "expect": "pass", "prose": "20%"},
               {"id": "ok2", "expect": "pass", "prose": "43%"}]
    problems = R.validate_episode(_episode(answers=answers), "probe.json")
    assert any("no answer expects failure" in message for message in problems), problems


def test_episode_with_an_unknown_check_is_rejected():
    problems = R.validate_episode(_episode(checks=["number_provenanc"]), "probe.json")
    assert any("unknown check" in message for message in problems), problems


def test_episode_pointing_outside_the_mock_fixtures_is_rejected():
    fixture = {"trades": "~/private/real_trades.csv", "route": "first_review", "locale": "en"}
    problems = R.validate_episode(_episode(fixture=fixture), "probe.json")
    assert any("synthetic CSV" in message for message in problems), problems


def test_episode_naming_a_check_it_does_not_declare_is_rejected():
    answers = [{"id": "miss", "expect": "fail", "fails": ["privacy_trace"], "prose": "63%"},
               {"id": "ok", "expect": "pass", "prose": "20%"}]
    problems = R.validate_episode(_episode(answers=answers), "probe.json")
    assert any("does not declare" in message for message in problems), problems


def test_episode_with_must_disclose_but_no_honesty_check_is_rejected():
    problems = R.validate_episode(_episode(must_disclose=["cash_reliability"]), "probe.json")
    assert any("honesty_coverage is not in checks" in message for message in problems), problems


def test_episode_declaring_honesty_coverage_without_a_key_is_rejected():
    """The other direction: the check would abstain on every answer, and an
    abstention caught at run time is a worse error surface than one caught here."""
    answers = [{"id": "miss", "expect": "fail", "fails": ["honesty_coverage"], "prose": "x"},
               {"id": "ok", "expect": "pass", "prose": "20%"}]
    problems = R.validate_episode(
        _episode(checks=["honesty_coverage"], answers=answers), "probe.json")
    assert any("must_disclose is empty" in message for message in problems), problems


# ── the rubric judge's declarations and interlocks (#417 second half) ────────
#
# The judge itself is billable and non-deterministic, so it never runs here.
# What runs here is everything that decides whether a judge run may be called
# green — the structural gates on the declarations, and the verdict arithmetic,
# both pure functions. An interlock only reachable by someone holding an API key
# is an interlock nobody re-verifies, which is the same fake green in a costlier
# wrapper. `evals/judge_episodes.py` imports `anthropic` inside `main()` for
# exactly this reason: these probes import the module and the offline suite
# stays dependency-free.

def _judged(axes=("two_sided",), fails=("two_sided",), declared_by="agent"):
    return _episode(
        judge={"axes": list(axes), "declared_by": declared_by},
        answers=[{"id": "miss", "expect": "fail", "fails": ["number_provenance"],
                  "prose": "63%", "judge_fails": list(fails)},
                 {"id": "ok", "expect": "pass", "prose": "20%"}])


def _samples(verdicts, runs=3):
    """`runs` identical samples, so the majority is unambiguous by construction."""
    return [{axis: {"verdict": verdict, "reason": "probe"}
             for axis, verdict in verdicts.items()} for _ in range(runs)]


def test_valid_judge_block_has_no_structural_problems():
    assert R.validate_episode(_judged(), "probe.json") == []


def test_unknown_judge_axis_is_rejected():
    problems = R.validate_episode(_judged(axes=["two_side"], fails=["two_side"]), "probe.json")
    assert any("unknown judge axis" in message for message in problems), problems


def test_judge_axis_no_answer_is_expected_to_fail_is_rejected():
    """Interlock 1, offline half: an axis nothing exercises cannot show the judge
    still discriminates on it — the judge's version of a check that abstains."""
    episode = _judged(axes=["two_sided", "overrulable"], fails=["two_sided"])
    problems = R.validate_episode(episode, "probe.json")
    assert any("'overrulable' is declared but no answer" in message
               for message in problems), problems


def test_judge_fails_naming_an_undeclared_axis_is_rejected():
    episode = _judged(axes=["two_sided"], fails=["two_sided", "overrulable"])
    problems = R.validate_episode(episode, "probe.json")
    assert any("does not declare" in message for message in problems), problems


def test_judge_fails_without_a_judge_block_is_rejected():
    answers = [{"id": "miss", "expect": "fail", "fails": ["number_provenance"],
                "prose": "63%", "judge_fails": ["two_sided"]},
               {"id": "ok", "expect": "pass", "prose": "20%"}]
    problems = R.validate_episode(_episode(answers=answers), "probe.json")
    assert any("declares no judge axes" in message for message in problems), problems


def test_judge_block_without_declared_by_is_rejected():
    """Who declared the expectations decides whether a green run is calibration
    or merely agreement, so it cannot be omitted and inferred later."""
    episode = _judged()
    del episode["judge"]["declared_by"]
    problems = R.validate_episode(episode, "probe.json")
    assert any("declared_by" in message for message in problems), problems


def test_judge_verdicts_that_match_the_declaration_are_green():
    episode = _judged(axes=["two_sided"], fails=["two_sided"])
    miss, ok = episode["answers"]
    failures, observed = J.grade_answer(episode, miss, ["two_sided"],
                                        _samples({"two_sided": "fail"}))
    assert failures == [], failures
    assert observed == {"two_sided": {"fail"}}
    failures, observed = J.grade_answer(episode, ok, ["two_sided"],
                                        _samples({"two_sided": "pass"}))
    assert failures == [], failures
    assert observed == {"two_sided": {"pass"}}


def test_a_judge_that_always_passes_turns_the_recorded_miss_red():
    """The mutation for the whole harness. A judge that has stopped
    discriminating marks the recorded miss `pass`; the recorded miss is the
    permanent mutation that catches it."""
    episode = _judged(axes=["two_sided"], fails=["two_sided"])
    failures, _ = J.grade_answer(episode, episode["answers"][0], ["two_sided"],
                                 _samples({"two_sided": "pass"}))
    assert any("expected to fail this axis and passed" in message
               for message in failures), failures


def test_failing_the_wrong_axis_is_a_failure():
    """Interlock 2: an answer that trips an axis it was not written to trip has
    stopped grading what it was written to grade."""
    episode = _judged(axes=["two_sided", "overrulable"], fails=["two_sided", "overrulable"])
    ok = episode["answers"][1]
    failures, _ = J.grade_answer(episode, ok, ["two_sided", "overrulable"],
                                 _samples({"two_sided": "pass", "overrulable": "fail"}))
    assert len(failures) == 1, failures
    assert "overrulable was not written to fail this axis" in failures[0], failures


def test_a_split_verdict_is_ambiguous_and_never_resolved_toward_the_expectation():
    """Non-determinism guard: a tie is the finding. Resolving it toward the
    declared verdict is how a judge harness becomes a rubber stamp."""
    episode = _judged(axes=["two_sided"], fails=["two_sided"])
    split = [{"two_sided": {"verdict": "fail", "reason": "a"}},
             {"two_sided": {"verdict": "pass", "reason": "b"}}]
    failures, observed = J.grade_answer(episode, episode["answers"][0], ["two_sided"], split)
    assert any("split" in message for message in failures), failures
    assert observed == {}, "an ambiguous verdict must not count toward coverage"


def test_a_run_that_would_judge_nothing_is_not_a_pass():
    """External review, 2026-07-27: `judge_episodes.py EP-001` selected an episode
    declaring no axes, so no failure was reachable, coverage was skipped as
    unevaluable, and the run printed PASS having made zero model calls. Every
    mutation in the first dance assumed something was graded, so none of them
    could see it."""
    bank, _problems = R.load_bank()
    unjudged = [e for e in bank if not (e.get("judge") or {}).get("axes")]
    judged = [e for e in bank if (e.get("judge") or {}).get("axes")]
    assert unjudged and judged, "the bank needs both kinds for this probe to mean anything"
    assert J.nothing_to_judge(unjudged, {unjudged[0]["id"]}) is not None
    assert J.nothing_to_judge([], set()) is not None
    assert J.nothing_to_judge(judged, set()) is None
    assert J.nothing_to_judge(bank, set()) is None


# The backend seam. `agy` needs no key and is a different vendor from the one
# that authored the answers, which is the point; what it cannot give is the
# shape guarantee forced tool use provides, so everything below is what replaces
# that guarantee. None of it calls a model.

def test_a_reply_the_harness_cannot_read_is_not_a_verdict():
    axes = ["two_sided"]
    good = '{"two_sided": {"verdict": "fail", "reason": "one direction only"}}'
    assert J._parse_verdicts(good, axes)["two_sided"]["verdict"] == "fail"
    # Real shapes a CLI returns: fenced, and wrapped in prose.
    assert J._parse_verdicts("```json\n" + good + "\n```", axes) is not None
    assert J._parse_verdicts("Sure! Here is my assessment:\n" + good, axes) is not None
    for unreadable in ("", "no json here at all", "{not json}", '{"two_sided": "fail"}',
                       '{"two_sided": {"verdict": "maybe", "reason": "x"}}',
                       '["two_sided"]', '{"overrulable": {"verdict": "fail", "reason": "x"}}'):
        assert J._parse_verdicts(unreadable, axes) is None, unreadable


def test_a_partial_reply_is_discarded_whole():
    """All-or-nothing: keeping the axes a reply did answer, while silently
    dropping the one it skipped, is how a judge starts grading less than it
    claims to."""
    both = ["two_sided", "overrulable"]
    partial = '{"two_sided": {"verdict": "fail", "reason": "x"}}'
    assert J._parse_verdicts(partial, both) is None
    assert J._parse_verdicts(partial, ["two_sided"]) is not None


def test_unusable_samples_are_reported_and_never_counted_as_agreement():
    episode = _judged(axes=["two_sided"], fails=["two_sided"])
    miss = episode["answers"][0]
    # Every sample unreadable: the axis gets no votes, so it votes ambiguous —
    # a failure — and the unusable count is stated rather than swallowed.
    failures, observed = J.grade_answer(episode, miss, ["two_sided"], [None, None, None])
    assert any("unreadable" in message for message in failures), failures
    assert any("split" in message for message in failures), failures
    assert observed == {}, "an unreadable sample must not count toward coverage"
    # One unusable among three still reports it, and the survivors still decide.
    failures, observed = J.grade_answer(
        episode, miss, ["two_sided"],
        [None] + _samples({"two_sided": "fail"}, runs=2))
    assert any("1/3 sample(s) came back unreadable" in m for m in failures), failures
    assert observed == {"two_sided": {"fail"}}


def test_backend_resolution_names_both_routes_and_refuses_an_unknown_one():
    """Availability is injected, never probed, so this decides the same thing on
    every machine. The first cut called `resolve_backend()` bare and passed only
    where a backend happened to be installed — asserting the environment rather
    than the logic, which is the fake-green shape this file already caught once.
    CI, which has neither backend, went red while the author's machine stayed
    green."""
    both = {"agy": True, "anthropic": True}
    only_sdk = {"agy": False, "anthropic": True}
    neither = {"agy": False, "anthropic": False}

    # auto prefers agy: on a machine with both, it is the one needing no key.
    assert J.resolve_backend("auto", both) == ("agy", J.DEFAULT_MODELS["agy"])
    assert J.resolve_backend("auto", only_sdk) == ("anthropic", J.DEFAULT_MODELS["anthropic"])
    # An explicit choice is honoured even when the other is also present.
    assert J.resolve_backend("anthropic", both)[0] == "anthropic"

    for name, available, expected in (
            ("auto", neither, "needs a model"),          # names both routes, not one
            ("hunyuan", both, "unknown TR_JUDGE_BACKEND"),
            ("agy", only_sdk, "not installed here"),     # asked for by name, absent
    ):
        try:
            J.resolve_backend(name, available)
        except SystemExit as reason:
            assert expected in str(reason), (name, reason)
        else:
            raise AssertionError(f"resolve_backend({name!r}, {available}) should have failed")
    # The no-backend message must name both routes, so a maintainer is not sent
    # down the one that happens to be mentioned first.
    try:
        J.resolve_backend("auto", neither)
    except SystemExit as reason:
        assert "agy" in str(reason) and "anthropic" in str(reason), reason


def test_anthropic_judge_uses_the_resolved_model_for_default_and_override():
    """The main sampler must pass resolve_backend's model, not global MODEL."""
    sent = []

    class Messages:
        def create(self, **kwargs):
            sent.append(kwargs)
            return types.SimpleNamespace(
                stop_reason="tool_use", stop_details=None,
                content=[types.SimpleNamespace(
                    type="tool_use", input={"two_sided": {"verdict": "pass", "reason": "x"}})])

    client = types.SimpleNamespace(messages=Messages())
    anthropic = types.SimpleNamespace(APIError=RuntimeError)
    episode = _judged(axes=("two_sided",), fails=())
    answer = episode["answers"][0]
    original_model = J.MODEL
    try:
        J.MODEL = None
        backend, model = J.resolve_backend("auto", {"agy": False, "anthropic": True})
        assert backend == "anthropic" and model == J.DEFAULT_MODELS["anthropic"]
        J.judge_once(model, client, anthropic, episode, answer, ("two_sided",))
        assert sent[-1]["model"] == model

        J.MODEL = "claude-opus-5-explicit"
        _backend, override = J.resolve_backend("anthropic", {"agy": False, "anthropic": True})
        J.judge_once(override, client, anthropic, episode, answer, ("two_sided",))
        assert sent[-1]["model"] == "claude-opus-5-explicit"
    finally:
        J.MODEL = original_model


def test_judge_coverage_requires_every_axis_seen_both_passing_and_failing():
    """Interlock 3: an axis only ever observed passing has never been seen fire."""
    everything = {axis: {"pass", "fail"} for axis in R.JUDGE_AXES}
    assert J.coverage_report(everything) == []
    only_passing = dict(everything, two_sided={"pass"})
    assert any("only ever observed pass" in message
               for message in J.coverage_report(only_passing)), only_passing
    undeclared = {axis: outcomes for axis, outcomes in everything.items()
                  if axis != "two_sided"}
    assert any("no episode declares it" in message
               for message in J.coverage_report(undeclared)), undeclared


def test_judge_rubric_and_axis_names_cannot_drift():
    """The names live in the runner (the offline loader must reject a typo for
    free); the rubric lives in the judge. Either growing alone must fail the run.

    This drives the gate against a mutated state rather than asserting the state
    directly: `assert set(RUBRIC) == set(JUDGE_AXES)` stays green even when
    `_check_rubric_parity` has decayed into a no-op, which is the shape of fake
    green the 2026-07-24 probe audit named (structure asserted, gate unobserved).
    """
    J._check_rubric_parity()
    for axis, entry in J.RUBRIC.items():
        assert entry["holds"] and entry["breaks"] and entry["one_line"], axis
    original = dict(J.RUBRIC)
    try:
        J.RUBRIC = {axis: entry for axis, entry in original.items()
                    if axis != R.JUDGE_AXES[0]}
        try:
            J._check_rubric_parity()
            raise AssertionError("an axis with no rubric did not fail the parity gate")
        except SystemExit as exit_code:
            assert "missing rubric" in str(exit_code), exit_code
        J.RUBRIC = dict(original, invented_axis={"one_line": "x", "holds": "x", "breaks": "x"})
        try:
            J._check_rubric_parity()
            raise AssertionError("a rubric for an undeclared axis did not fail the parity gate")
        except SystemExit as exit_code:
            assert "undeclared axis" in str(exit_code), exit_code
    finally:
        J.RUBRIC = original


def test_the_judge_is_never_shown_the_paired_answer_or_the_maintainer_note():
    """Showing either would make the judge a similarity scorer against today's
    repair, which is exactly the target the bank is forbidden to pin (#431)."""
    episode, _problems = R.load_bank({"EP-008"})[0][0], None
    miss = next(a for a in episode["answers"] if a["id"] == "recorded_miss")
    other = next(a for a in episode["answers"] if a["id"] != "recorded_miss")
    shown = J.material(episode, miss)
    assert miss["prose"] in shown
    assert other["prose"] not in shown
    assert miss["note"] not in shown
    assert episode["title"] not in shown


def test_committed_bank_is_structurally_valid_and_schema_fields_match():
    """The shipped bank must load, and the schema doc must not drift from the
    enforcement: every field the loader accepts is a documented property."""
    episodes, problems = R.load_bank()
    assert not problems, problems
    assert len(episodes) >= 3, "the first deliverable is 3-5 episodes"
    schema = json.loads((EPISODE_DIR / "episode.schema.json").read_text(encoding="utf-8"))
    documented = set(schema["properties"])
    for episode in episodes:
        undocumented = set(episode) - documented - {"_path"}
        assert not undocumented, f"{episode['id']}: undocumented field(s) {sorted(undocumented)}"
    answer_properties = set(
        schema["properties"]["answers"]["items"]["properties"])
    for episode in episodes:
        for answer in episode["answers"]:
            undocumented = set(answer) - answer_properties
            assert not undocumented, f"{episode['id']}/{answer['id']}: {sorted(undocumented)}"
    enum = set(schema["properties"]["checks"]["items"]["enum"])
    assert enum == set(R.CHECK_NAMES), (
        f"schema check enum and runner CHECK_NAMES diverged: {enum ^ set(R.CHECK_NAMES)}")
    axes = set(schema["properties"]["judge"]["properties"]["axes"]["items"]["enum"])
    assert axes == set(R.JUDGE_AXES), (
        f"schema judge axes and runner JUDGE_AXES diverged: {axes ^ set(R.JUDGE_AXES)}")
    judge_properties = set(
        schema["properties"]["answers"]["items"]["properties"]["judge_fails"])
    assert "items" in judge_properties, "judge_fails must document its item shape"


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS all {len(tests)} episode checker probes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
