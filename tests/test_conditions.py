#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Condition slots (#412) — unit tests. Offline, deterministic, no pytest.

What this file settles:
  A. The tier is derived from evidence, never taken from the payload.
  B. The comparison never enters the query (the criterion-is-not-the-query gate),
     and a fiscal-quarter label is not mistaken for a threshold leak.
  C. The engine performs the comparison — including at commit time, so a line
     that is already crossed is visible before the user walks away thinking they
     set a tripwire.
  D. The near-line margin is frozen at creation.
  E. Everything that cannot be settled fails closed with a message naming the field.

The end-to-end half — a condition surviving finalize, staying out of rules.jsonl,
and being read back into the next review — lives in tests/test_review_v2.py,
where the CLI harness already is.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "skills", "fomo-kernel", "engine"))
import conditions  # noqa: E402

GROWTH = {
    "criterion": "quarterly revenue growth drops under 30%",
    "query": "what was the most recent quarterly revenue, and the year-ago quarter?",
    "threshold": {"value": 30, "unit": "%", "direction": "below"},
    "observation": {"value": 38.0, "as_of": "2026-05-20", "source": "Q1 FY2027 press release",
                    "period": "FY2027Q1", "document": "8-K 2026-05-20"},
}


def _slot(**overrides):
    raw = dict(GROWTH)
    for key, value in overrides.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    return conditions.build_slot(raw, slot_id="slot-test-0", created="2026-07-26",
                                 session_id="2026-07-26__test")


def _rejects(expect_fragment, **overrides):
    try:
        _slot(**overrides)
    except conditions.ConditionError as exc:
        assert expect_fragment in str(exc), \
            f"rejected for the wrong reason: wanted {expect_fragment!r}, got {str(exc)!r}"
        return
    raise AssertionError(f"should have been rejected ({expect_fragment})")


# ─────────────────────── A. tier is derived, not declared ───────────────────────

def test_threshold_plus_observation_is_researched():
    slot = _slot()
    assert slot["tier"] == "researched"
    assert slot["baseline"]["source"] == "Q1 FY2027 press release"
    assert slot["criterion"] == GROWTH["criterion"], "the user's words are stored verbatim"


def test_no_threshold_falls_to_unmapped_not_to_a_guess():
    slot = _slot(threshold=None)
    assert (slot["tier"], slot["unmapped_reason"]) == ("unmapped", "no_threshold")
    assert "baseline_verdict" not in slot, "nothing was compared, so nothing may read as a verdict"
    assert slot["criterion"] and slot["query"], "an unwatchable condition is still stored in full"


def test_nothing_found_at_commit_time_falls_to_unmapped():
    slot = _slot(observation=None)
    assert (slot["tier"], slot["unmapped_reason"]) == ("unmapped", "no_baseline")
    assert "baseline_verdict" not in slot


def test_tier_cannot_be_supplied_by_the_payload():
    """An agent that declares its own tier can declare a wrong one."""
    _rejects("unknown fields", tier="researched")


# ─────────────────── B. the comparison never enters the query ───────────────────

def test_query_carrying_the_threshold_is_rejected():
    _rejects("threshold value", query="did quarterly revenue growth fall below 30%?")


def test_query_identical_to_the_criterion_is_rejected():
    _rejects("restates the criterion", query=GROWTH["criterion"])


def test_a_fiscal_quarter_label_is_not_a_threshold_leak():
    """`Q3` and `FY2026` are labels. A gate that reads their digits as the
    threshold would push agents into vaguer queries to get past it."""
    slot = _slot(threshold={"value": 3, "unit": "x", "direction": "below"},
                 query="what was Q3 FY2026 net leverage, and the quarter before it?")
    assert slot["tier"] == "researched"


def test_the_threshold_is_matched_by_value_not_by_spelling():
    assert conditions.restates_threshold("revenue of 1,000 in the latest quarter",
                                         {"value": 1000.0}), "thousands separators still leak"
    assert conditions.restates_threshold("margin of 30.0 percent", {"value": 30})
    assert not conditions.restates_threshold("margin in the latest quarter", {"value": 30})


# ───────────────────────── C. the engine compares ─────────────────────────

def test_a_line_already_crossed_is_visible_at_commit_time():
    """The point of taking a baseline: a user can otherwise commit to a
    condition that is already breached and carry it for weeks unknowingly."""
    slot = _slot(observation={"value": 21.0, "as_of": "2026-05-20", "source": "press release"})
    assert slot["baseline_verdict"] == "met"


def test_comfortably_clear_of_the_line_is_not_met():
    assert _slot()["baseline_verdict"] == "not_met"


def test_just_off_the_line_is_near_line():
    slot = _slot(observation={"value": 32.0, "as_of": "2026-05-20", "source": "press release"})
    assert slot["baseline_verdict"] == "near_line", "30 +/- 3 is inside the frozen margin"


def test_direction_above_is_the_mirror_image():
    slot = _slot(threshold={"value": 4.0, "unit": "x", "direction": "above"},
                 criterion="net leverage climbs over 4x",
                 query="what was net debt to EBITDA in the latest reported quarter?",
                 observation={"value": 4.6, "as_of": "2026-05-20", "source": "10-Q"})
    assert slot["baseline_verdict"] == "met"


def test_evaluate_without_evidence_is_unknown_never_fine():
    """A condition that could not be checked must never read as checked and fine."""
    assert conditions.evaluate(GROWTH["threshold"], None, 3.0) == "unknown"
    assert conditions.evaluate(None, GROWTH["observation"], 3.0) == "unknown"


# ─────────────────────── D. the near-line margin is frozen ───────────────────────

def test_near_line_defaults_to_a_tenth_of_the_threshold():
    assert _slot()["near_line"] == 3.0


def test_near_line_is_stored_as_given_when_supplied():
    assert _slot(near_line=0.5)["near_line"] == 0.5


def test_negative_near_line_is_rejected():
    _rejects("near_line", near_line=-1)


# ─────────────────────────── E. everything else fails closed ───────────────────────────

def test_provenance_is_mandatory_on_an_observation():
    _rejects("observation.source",
             observation={"value": 38.0, "as_of": "2026-05-20", "source": "  "})
    _rejects("observation.as_of", observation={"value": 38.0, "source": "10-Q"})
    _rejects("ISO date", observation={"value": 38.0, "as_of": "May 2026", "source": "10-Q"})


def test_a_non_numeric_observation_is_rejected():
    _rejects("observation.value",
             observation={"value": "38%", "as_of": "2026-05-20", "source": "10-Q"})


def test_threshold_needs_a_value_a_unit_and_a_side():
    _rejects("threshold.direction", threshold={"value": 30, "unit": "%"})
    _rejects("threshold.direction", threshold={"value": 30, "unit": "%", "direction": "under"})
    _rejects("threshold.unit", threshold={"value": 30, "direction": "below"})
    _rejects("threshold.value", threshold={"value": "30", "unit": "%", "direction": "below"})


def test_criterion_and_query_are_both_required():
    _rejects("condition.criterion", criterion="   ")
    _rejects("condition.query", query=None)


# ─────────────────────────────── the store ───────────────────────────────

def test_load_slots_skips_a_corrupt_line_instead_of_losing_the_record():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "conditions.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"slot_id": "slot-a", "criterion": "margin under 40%"}\n')
            handle.write("{not json\n")
            handle.write('{"no_slot_id": true}\n')
            handle.write('{"slot_id": "slot-b", "criterion": "growth under 30%"}\n')
        slots = conditions.load_slots(path)
        assert [row["slot_id"] for row in slots] == ["slot-a", "slot-b"]


def test_load_slots_on_a_missing_file_is_empty_not_an_error():
    assert conditions.load_slots(os.path.join("no", "such", "conditions.jsonl")) == []


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
