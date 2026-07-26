#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probes for the question-episode bank's mechanical checkers (#417).

`evals/run_episodes.py` replays real misses; this file proves the six checkers
it replays them with still look at something. Every probe is a pair: a clean
input the checker must stay quiet on, and one minimal mutation it must catch.
A checker that passes both halves of its pair cannot be silently a no-op —
CLAUDE.md's rule is that a new checker lands with proof its matching mutation
fails, and `eval-design.md` section "Mutation testing" says the same.

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

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "evals"))
import run_episodes as R  # noqa: E402

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


# ── grounding_fidelity ───────────────────────────────────────────────────────

def _option(maps_to, description, label="x"):
    return {"presented_options": [{"maps_to": maps_to, "label": label,
                                   "description": description}]}


def test_grounding_fidelity_accepts_a_verbatim_grounding():
    engine = FACTS["candidates"]["candidate_0"]
    answer = _option("candidate_0", engine["grounding"] + engine["rule"])
    assert R.check_grounding_fidelity(answer, FACTS) == []


def test_grounding_fidelity_catches_a_paraphrase():
    """#293's first half: the candidate had a grounding and it was rewritten."""
    answer = _option("candidate_0", "Your biggest position is way over the cap.")
    findings = R.check_grounding_fidelity(answer, FACTS)
    assert any("not quoted verbatim" in message for message in findings), findings


def test_grounding_fidelity_catches_an_invented_grounding():
    """#293's second half: the candidate had none and one was written for it."""
    answer = _option("candidate_1", "This period you sold three winners early.")
    findings = R.check_grounding_fidelity(answer, FACTS)
    assert any("no engine field authored" in message for message in findings), findings


def test_grounding_fidelity_accepts_an_ungrounded_candidate_presented_bare():
    answer = _option("candidate_1", FACTS["candidates"]["candidate_1"]["rule"])
    assert R.check_grounding_fidelity(answer, FACTS) == []


def test_grounding_fidelity_catches_an_unknown_candidate():
    findings = R.check_grounding_fidelity(_option("candidate_9", "anything"), FACTS)
    assert findings and "not a candidate rule" in findings[0], findings


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
    assert findings and "nothing was found" in findings[0], findings


def test_condition_integrity_accepts_an_unmapped_slot_said_out_loud():
    assert R.check_condition_integrity(
        _answer(prose="Recorded, in your words: sell if quarterly revenue growth drops under "
                      "30%. I could not find a published figure, so I cannot watch this one.",
                observation=None), FACTS) == []


def test_condition_integrity_abstains_when_the_answer_carries_no_envelope():
    _findings, looked = R.run_check("condition_integrity", {}, {"prose": "no condition here"}, FACTS)
    assert looked is False, "a check with nothing to inspect has abstained, not passed"


# ── the interlocks ───────────────────────────────────────────────────────────

def test_a_declared_check_with_nothing_to_inspect_reports_no_data():
    """Interlock 2, at the checker layer: grounding_fidelity in front of an
    answer with no options has abstained, not passed."""
    _findings, looked = R.run_check("grounding_fidelity", {}, {"prose": "no options here"}, FACTS)
    assert looked is False


def test_replay_fails_an_episode_whose_declared_check_abstains():
    """Interlock 2, at the layer that consumes the signal.

    The probe above only proves ``run_check`` reports "nothing to inspect"; it
    says nothing about whether ``replay`` acts on it. The 2026-07-26 mutation
    dance for this PR found exactly that gap — neutering ``replay``'s
    ``if not looked`` left the suite green, which is fake-green type 5 (right
    assertion, wrong layer; docs/development-guide.md section 2)."""
    episode = {
        "id": "EP-000", "checks": ["grounding_fidelity"],
        "question": {"asked_by": "user", "kind": "free_form", "text": "?"},
        "answers": [{"id": "a", "expect": "pass", "prose": "no options here"}],
    }
    failures, observed, _unmapped = R.replay(episode, FACTS)
    assert any("nothing to inspect" in message for message in failures), failures
    assert observed["grounding_fidelity"] == set(), (
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


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS all {len(tests)} episode checker probes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
