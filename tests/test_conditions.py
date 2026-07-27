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
  F. A re-stated criterion is a new slot row on the same line (the `revises`
     chain), and a reader tolerates a fork rather than raising on one.
  G. A check's lookup_status branches, and only `ok` may carry an observation
     or a user_response — a non-ok lookup has no fresh evidence for either.
  H. A check's observation is either numeric (`value`) or textual (`summary`),
     never both, never neither, and never the other kind's shape.
  I. A check's user_response vocabulary depends on the slot's kind.
  J. The verdict of record: engine-computed by default, user-confirmed,
     user-overridden (which never rewrites engine_verdict), or — for an event
     slot with no answer yet — honestly unknown rather than guessed.
  K. information_state: new_period / restated / no_new_data, compared against
     the previous ok-check or, absent one, the slot's own baseline — including
     probes pinning that the marker comparison is genuine numeric equality
     (0.3 == 0.30; +0.0 == -0.0) and not a magnitude/scale-tolerant proxy of it
     (30 != -30; 30 != 3000).
  L. The check store: previous_check_for across a revision boundary, and
     load_checks' tolerance for corruption and a missing file.
  M. The firewall is physical: conditions.py never imports problems.
  N. Produced rows carry exactly what the schema declares.
  O. The two alerts the engine cannot derive (`event_alert`, `basis_alert`):
     each raises one question and decides nothing, and neither may exist
     without evidence behind it.
  P. Line history: "when was this last looked at" and "what did the last
     successful lookup find" are different questions, and both cross a
     revision boundary.

The end-to-end half — a condition surviving finalize, staying out of rules.jsonl,
being read back into the next review, checked each period, and adjudicated by
the user — lives in tests/test_review_v2.py, where the CLI harness already is.
"""
import ast
import json
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


def _event_slot(**overrides):
    raw = {"kind": "event", "criterion": "sell if the CEO leaves",
           "query": "who is the current chief executive, and when did they take the role?"}
    for key, value in overrides.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    return conditions.build_slot(raw, slot_id="slot-test-evt", created="2026-07-26",
                                 session_id="2026-07-26__test")


def _child_slot(revises, criterion="quarterly revenue growth drops under 25%", slot_id="slot-test-child",
                created="2026-08-26", **overrides):
    raw = dict(GROWTH, criterion=criterion)
    for key, value in overrides.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    return conditions.build_slot(raw, slot_id=slot_id, created=created,
                                 session_id="2026-07-26__test", revises=revises)


# Comfortably clear of GROWTH's threshold (30, below, near_line 3.0), so tests
# that are not about the verdict itself do not accidentally land on met/near_line.
NUMERIC_OBS_1 = {"value": 36.0, "as_of": "2026-08-20", "source": "10-Q",
                 "period": "FY2027Q2", "document": "10-Q 2026-08-20"}
EVENT_OBS_1 = {"summary": "CEO still in role per the latest 8-K", "as_of": "2026-08-20", "source": "8-K"}


def _check(slot=None, previous=None, check_id="chk-test-0", session_id="2026-08-26__test",
           date_end="2026-08-26", **overrides):
    """One built check row. ``user_response`` / ``basis_resolution`` are routed to
    the keyword arguments they now are: they carry what the *user* said, and an
    envelope may not report that (external review, round 1). Passing them here
    stands in for the engine folding in an answer to a question it actually
    posed — the smuggling refusal itself is tested through ``build_check``
    directly, below."""
    slot = slot if slot is not None else _slot()
    raw = {"lookup_status": "ok", "observation": dict(NUMERIC_OBS_1)}
    engine_assigned = {}
    for key, value in overrides.items():
        target = engine_assigned if key in conditions._ENGINE_ASSIGNED_CHECK_FIELDS else raw
        if value is None:
            target.pop(key, None)
        else:
            target[key] = value
    return conditions.build_check(raw, slot=slot, previous=previous, check_id=check_id,
                                  session_id=session_id, date_end=date_end, **engine_assigned)


def _event_check(slot=None, previous=None, **overrides):
    overrides.setdefault("observation", dict(EVENT_OBS_1))
    return _check(slot=slot if slot is not None else _event_slot(), previous=previous, **overrides)


def _check_rejects(expect_fragment, slot=None, **overrides):
    try:
        _check(slot=slot, **overrides)
    except conditions.ConditionError as exc:
        assert expect_fragment in str(exc), \
            f"rejected for the wrong reason: wanted {expect_fragment!r}, got {str(exc)!r}"
        return
    raise AssertionError(f"should have been rejected ({expect_fragment})")


def _schema(name):
    path = os.path.join(REPO, "skills", "fomo-kernel", "schemas", name)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


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


def test_an_unwatchable_condition_keeps_the_evidence_that_was_found():
    """`stored in full` has to mean it. The observation was validated (source,
    ISO as-of, numeric value) and it is what someone actually found — dropping it
    would leave the record thinner than the conversation that produced it."""
    slot = _slot(threshold=None)
    assert "baseline" in slot, f"the evidence found at commit time was dropped: {slot}"
    assert slot["baseline"]["value"] == 38.0, slot
    assert slot["baseline"]["source"] and slot["baseline"]["as_of"]
    assert "baseline_verdict" not in slot, "evidence is not a verdict"


def test_an_event_condition_says_it_is_one_rather_than_being_inferred():
    """These rows are append-only and never rewritten. A row that says what it is
    costs one field; inferring `event` from a missing threshold would be permanent
    and wrong the first time a numeric condition arrives without one."""
    slot = _slot(kind="event", criterion="sell if the CEO leaves",
                 query="who is the current chief executive, and when did they take the role?",
                 threshold=None, observation=None)
    assert slot["kind"] == "event"
    assert slot["tier"] == "researched", \
        "an event has no line to compare, so its verdict comes from the user — and now that " \
        "the check flow asks, the adjudicator it was missing exists"
    assert "unmapped_reason" not in slot, slot
    assert "baseline_verdict" not in slot, \
        "the engine never computes an event verdict, at commit time or any other time"


def test_an_event_condition_is_watchable_without_a_threshold_a_numeric_one_is_not():
    """The flip is scoped to events. A numeric condition with no line is still
    `unmapped`: there is nothing for the engine to compare and no yes/no question
    a user could answer in its place."""
    assert _slot(kind="event", criterion="sell if the CEO leaves",
                 query="who is the current chief executive?",
                 threshold=None, observation=None)["tier"] == "researched"
    blind = _slot(threshold=None)
    assert (blind["tier"], blind["unmapped_reason"]) == ("unmapped", "no_threshold")


def test_a_pre_flow_event_row_still_reads_as_an_event_row():
    """`no_adjudicator` rows were written while nothing asked the user for the
    verdict. They are facts about what was true then and are never rewritten, so
    the reason stays a documented enum member and the readers keep handling it."""
    assert "no_adjudicator" in conditions.UNMAPPED_REASONS
    legacy = {"slot_id": "slot-old-0", "kind": "event", "criterion": "sell if the CEO leaves",
              "query": "who is the current chief executive?", "created": "2026-07-01",
              "tier": "unmapped", "unmapped_reason": "no_adjudicator"}
    assert conditions.slot_line_id(legacy) == "slot-old-0"
    lines, forked = conditions.fold_slots([legacy])
    assert forked == 0 and lines["slot-old-0"]["latest"] is legacy
    # A check against it still builds: the tier is a display fact, not a gate.
    row = conditions.build_check({"lookup_status": "ok",
                                  "observation": dict(EVENT_OBS_1)},
                                 slot=legacy, previous=None, check_id="chk-legacy",
                                 date_end="2026-08-26")
    assert row["final_verdict"] == "unknown" and row["engine_verdict"] is None


def test_a_numeric_condition_is_the_default_kind():
    assert _slot()["kind"] == "numeric"
    _rejects("condition.kind", kind="researched")


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
    assert conditions.restates_threshold("margin of .5 percent", {"value": 0.5}), \
        "a bare decimal is a quantity however it is spelled"
    assert not conditions.restates_threshold("margin in the latest quarter", {"value": 30})


def test_a_negative_line_cannot_slip_past_the_gate():
    """The number pattern cannot see a minus sign — a leading `-` in real text is
    a hyphen or a date separator far more often — so the comparison is made on
    magnitude. Without this, the whole negative-fundamentals class (#412's
    canonical case) escaped the one mechanical gate the module has."""
    _rejects("threshold value",
             criterion="sell if operating margin falls below -5%",
             threshold={"value": -5, "unit": "%", "direction": "below"},
             query="did operating margin fall below -5% last quarter?")


def test_the_same_line_written_at_another_scale_still_leaks():
    """0.30 and 30% are the same line; a gate that reads only one spelling would
    be bypassed by the more natural phrasing."""
    assert conditions.restates_threshold("did the margin fall below 30%?", {"value": 0.30})
    assert conditions.restates_threshold("did the margin fall below 0.3?", {"value": 30})


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


def test_a_line_at_zero_must_state_its_own_margin():
    """"Sell if free cash flow goes negative" has no magnitude to take a tenth of.
    Defaulting to zero would disable the near-line state for that whole class
    without ever saying so."""
    zero = {"value": 0, "unit": "USD", "direction": "below"}
    _rejects("near_line is required", threshold=zero,
             criterion="sell if free cash flow goes negative",
             query="what was free cash flow in the latest reported quarter?")
    slot = _slot(threshold=zero, near_line=1_000_000.0,
                 criterion="sell if free cash flow goes negative",
                 query="what was free cash flow in the latest reported quarter?",
                 observation={"value": 400000.0, "as_of": "2026-05-20", "source": "10-Q"})
    assert slot["baseline_verdict"] == "near_line"


def test_a_zero_margin_disables_the_band_instead_of_pinning_it_to_equality():
    """`near_line` exists to ask early. "Ask me at exactly the line and nowhere
    else" is not a margin anyone chose."""
    assert conditions.evaluate({"value": 30.0, "direction": "below"}, {"value": 30.0}, 0.0) \
        == "not_met"
    assert conditions.evaluate({"value": 30.0, "direction": "below"}, {"value": 30.0}, 1.0) \
        == "near_line"


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

def test_load_slots_reports_the_lines_it_could_not_read():
    """Degrading is right; degrading silently is not. A dropped row would make
    corruption look like a condition the user never wrote."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "conditions.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"slot_id": "slot-a", "criterion": "margin under 40%"}\n')
            handle.write("{not json\n")
            handle.write('{"no_slot_id": true}\n')
            handle.write('{"slot_id": "slot-b", "criterion": "growth under 30%"}\n')
        slots, unreadable = conditions.load_slots(path)
        assert [row["slot_id"] for row in slots] == ["slot-a", "slot-b"]
        assert unreadable == 2, "both the unparseable line and the unidentifiable row count"


def test_load_slots_on_a_missing_file_is_empty_not_an_error():
    assert conditions.load_slots(os.path.join("no", "such", "conditions.jsonl")) == ([], 0)


# ────────────────────── F. the revises chain (line identity) ──────────────────────

def test_revises_sets_the_parent_and_a_root_rows_line_id_is_its_own_slot_id():
    parent = _slot()
    child = _child_slot(parent)
    assert child["revises"] == parent["slot_id"]
    assert child["line_id"] == parent["slot_id"], "a root row's line_id is its own slot_id"


def test_revises_inherits_the_chains_root_line_id_not_the_immediate_parents_slot_id():
    root = _slot()
    middle = _child_slot(root, criterion="v2", slot_id="slot-test-1", created="2026-08-01")
    tip = _child_slot(middle, criterion="v3", slot_id="slot-test-2", created="2026-08-26")
    assert tip["revises"] == middle["slot_id"]
    assert tip["line_id"] == root["slot_id"], "line_id is the chain's root, not the immediate parent"


def test_slot_line_id_falls_back_to_slot_id_for_a_root_row_written_before_this_field_existed():
    old_root = {"slot_id": "slot-old-0", "criterion": "x"}
    assert conditions.slot_line_id(old_root) == "slot-old-0"


def test_revises_must_be_a_slot_row_with_a_slot_id():
    try:
        _child_slot({"not_a_slot": True})
    except conditions.ConditionError as exc:
        assert "revises must be a slot row" in str(exc)
    else:
        raise AssertionError("a revises value with no slot_id should have been rejected")


def test_input_envelope_cannot_smuggle_revises_or_line_id():
    """revises/line_id are engine-assigned kwargs to build_slot, never content
    an agent supplies on the payload — the unknown-field guard already covers
    them since they were never added to the legal field set."""
    _rejects("unknown fields", revises="slot-x")
    _rejects("unknown fields", line_id="line-x")


# ─────────── the thesis a condition falsifies (#416 C2 / #412) ───────────


def test_a_thesis_falsifier_slot_records_the_cycle_it_guards():
    """#416's ratified direction: a thesis falsifier IS a condition slot, so it
    carries which thesis it belongs to rather than a parallel lifecycle inside
    thesis.py doing the same reconciliation a second time."""
    raw = dict(GROWTH)
    slot = conditions.build_slot(raw, slot_id="slot-test-t0", created="2026-07-26",
                                 session_id="2026-07-26__test",
                                 thesis_cycle_id="NVDA#2026-01-05#1")
    assert slot["thesis_cycle_id"] == "NVDA#2026-01-05#1"
    assert conditions.build_slot(dict(GROWTH), slot_id="slot-test-p0",
                                 created="2026-07-26").get("thesis_cycle_id") is None, \
        "a commitment condition guards the portfolio and names no thesis"


def test_input_envelope_cannot_smuggle_the_thesis_it_is_attached_to():
    """An agent that could name the cycle could attach a condition to a thesis
    the user never wrote. Engine-assigned, exactly like revises/line_id."""
    _rejects("unknown fields", thesis_cycle_id="NVDA#2026-01-05#1")


def test_a_revision_inherits_the_thesis_it_was_written_to_falsify():
    """Re-stating a criterion is not re-attaching it. Without inheritance the
    user reworks the wording of their own falsifier and it silently stops being
    a falsifier of anything — one of the two ways this linkage can be lost."""
    parent = conditions.build_slot(dict(GROWTH), slot_id="slot-test-t0", created="2026-07-26",
                                   session_id="2026-07-26__test",
                                   thesis_cycle_id="NVDA#2026-01-05#1")
    child = _child_slot(parent)
    assert child.get("thesis_cycle_id") == "NVDA#2026-01-05#1", \
        f"a revision lost the thesis it guards: {child.get('thesis_cycle_id')!r}"
    assert child["line_id"] == parent["slot_id"], "and it is still the same line"


def test_a_revision_that_names_a_different_thesis_fails_closed():
    """The other way it can be lost: silently retargeting a line whose whole
    check history was accumulated against one thesis."""
    parent = conditions.build_slot(dict(GROWTH), slot_id="slot-test-t0", created="2026-07-26",
                                   thesis_cycle_id="NVDA#2026-01-05#1")
    try:
        conditions.build_slot(dict(GROWTH, criterion="v2"), slot_id="slot-test-t1",
                              created="2026-08-26", revises=parent,
                              thesis_cycle_id="AMD#2026-02-02#1")
    except conditions.ConditionError as exc:
        assert "contradicts the row it revises" in str(exc), str(exc)
    else:
        raise AssertionError("a revision may not move a condition to another thesis")


def test_a_thesis_linked_slot_matches_the_schemas_declared_shape():
    schema = _schema("condition-slot.schema.json")
    slot = conditions.build_slot(dict(GROWTH), slot_id="slot-test-t0", created="2026-07-26",
                                 session_id="2026-07-26__test",
                                 thesis_cycle_id="NVDA#2026-01-05#1")
    assert set(slot) <= set(schema["properties"]), \
        f"row has a field the schema does not declare: {set(slot) - set(schema['properties'])}"
    assert "thesis_cycle_id" in schema["properties"]
    assert "thesis_cycle_id" not in schema["properties"]["input"]["properties"], \
        "the agent-facing envelope must not offer the field the engine assigns"


def test_fold_slots_groups_a_chain_and_reports_no_fork():
    root = _slot()
    child = _child_slot(root, criterion="v2", slot_id="slot-test-1", created="2026-08-01")
    lines, forked = conditions.fold_slots([root, child])
    line = conditions.slot_line_id(root)
    assert set(lines) == {line}
    assert lines[line]["latest"]["slot_id"] == child["slot_id"]
    assert [row["slot_id"] for row in lines[line]["chain"]] == [root["slot_id"], child["slot_id"]]
    assert forked == 0


def test_fold_slots_counts_a_fork_and_file_order_wins_the_latest():
    """Two rows independently revising the same parent is a fork — the
    write-side guard against creating one is later work; this is the tolerant
    read side, which must survive a fork that already happened."""
    root = _slot()
    head_a = _child_slot(root, criterion="a", slot_id="slot-test-a", created="2026-08-01")
    head_b = _child_slot(root, criterion="b", slot_id="slot-test-b", created="2026-08-02")
    lines, forked = conditions.fold_slots([root, head_a, head_b])
    assert forked == 1
    line = conditions.slot_line_id(root)
    assert lines[line]["latest"]["slot_id"] == head_b["slot_id"], \
        "file order wins ties on a forked line: whichever head appears last"


def test_latest_by_line_is_a_thin_view_of_fold_slots():
    root = _slot()
    child = _child_slot(root, criterion="v2", slot_id="slot-test-1", created="2026-08-01")
    latest = conditions.latest_by_line([root, child])
    assert latest[conditions.slot_line_id(root)]["slot_id"] == child["slot_id"]


# ───────────────────── G. a check's lookup_status branches ─────────────────────

def test_lookup_status_must_be_one_of_the_three_named_values():
    _check_rejects("check.lookup_status", lookup_status="pending")


def test_lookup_status_ok_requires_an_observation():
    _check_rejects("check.observation is required", observation=None)


def test_lookup_status_failed_forbids_an_observation():
    _check_rejects("check.observation is forbidden", lookup_status="failed")


def test_lookup_status_not_checked_forbids_an_observation():
    _check_rejects("check.observation is forbidden", lookup_status="not_checked")


def test_lookup_status_failed_with_no_observation_reads_unknown_and_keeps_the_reason():
    row = _check(lookup_status="failed", observation=None, reason="fetch timed out")
    assert row["lookup_status"] == "failed"
    assert "observation" not in row
    assert row["reason"] == "fetch timed out"
    assert (row["information_state"], row["engine_verdict"]) == (None, None)
    assert (row["final_verdict"], row["verdict_source"]) == ("unknown", "engine")


def test_lookup_status_not_checked_with_no_observation_reads_unknown_too():
    row = _check(lookup_status="not_checked", observation=None)
    assert (row["information_state"], row["engine_verdict"],
            row["final_verdict"], row["verdict_source"]) == (None, None, "unknown", "engine")


def test_reason_is_optional_and_capped():
    row = _check(lookup_status="failed", observation=None)
    assert "reason" not in row
    _check_rejects("check.reason is longer than 500", lookup_status="failed", observation=None,
                   reason="x" * 501)


def test_check_envelope_rejects_an_unknown_field():
    _check_rejects("unknown fields", extra_field="nope")


def test_user_response_requires_a_successful_lookup():
    """External review (round 1), BLOCK: a lookup that did not succeed has no
    fresh evidence this period for a user to confirm, override, or answer
    against. Without this gate, an append-only row could carry a
    user_response beside final_verdict="unknown" forever — a contradiction
    every future reader would have to special-case."""
    _check_rejects("check.user_response requires a successful lookup",
                   lookup_status="failed", observation=None,
                   user_response={"answer": "confirmed", "answered_at": "2026-08-27"})
    _check_rejects("check.user_response requires a successful lookup",
                   lookup_status="not_checked", observation=None,
                   user_response={"answer": "confirmed", "answered_at": "2026-08-27"})


# ───────────────── H. an observation is numeric xor textual, never both ─────────────────

def test_a_numeric_slot_check_rejects_a_summary_only_observation():
    _check_rejects("summary is for an event condition",
                   observation={"summary": "growth looks fine", "as_of": "2026-08-20", "source": "10-Q"})


def test_an_event_slot_check_rejects_a_value_only_observation():
    _check_rejects("value is for a numeric condition", slot=_event_slot(),
                   observation={"value": 1.0, "as_of": "2026-08-20", "source": "8-K"})


def test_check_observation_rejects_both_value_and_summary():
    _check_rejects("exactly one of value or summary",
                   observation={"value": 36.0, "summary": "x", "as_of": "2026-08-20", "source": "10-Q"})


def test_check_observation_rejects_neither_value_nor_summary():
    _check_rejects("needs value", observation={"as_of": "2026-08-20", "source": "10-Q"})


def test_check_observation_provenance_is_mandatory():
    _check_rejects("observation.source", observation={**NUMERIC_OBS_1, "source": "  "})
    _check_rejects("observation.as_of", observation={"value": 36.0, "source": "10-Q"})
    _check_rejects("ISO date", observation={"value": 36.0, "as_of": "August 2026", "source": "10-Q"})


def test_check_observation_rejects_an_unknown_field():
    _check_rejects("check.observation has unknown fields", observation={**NUMERIC_OBS_1, "nonsense": 1})


# ───────────────── I. user_response vocabulary depends on the slot's kind ─────────────────

def test_numeric_user_response_accepts_confirmed_and_overridden():
    confirmed = _check(user_response={"answer": "confirmed", "answered_at": "2026-08-27"})
    assert confirmed["verdict_source"] == "user"
    overridden = _check(user_response={"answer": "overridden", "answered_at": "2026-08-27"})
    assert overridden["verdict_source"] == "user"


def test_numeric_user_response_rejects_event_vocabulary():
    _check_rejects("must be one of confirmed, overridden",
                   user_response={"answer": "yes", "answered_at": "2026-08-27"})


def test_event_user_response_accepts_yes_and_no():
    yes = _event_check(user_response={"answer": "yes", "answered_at": "2026-08-27"})
    assert yes["verdict_source"] == "user"
    no = _event_check(user_response={"answer": "no", "answered_at": "2026-08-27"})
    assert no["verdict_source"] == "user"


def test_event_user_response_rejects_numeric_vocabulary():
    _check_rejects("must be one of yes, no", slot=_event_slot(), observation=dict(EVENT_OBS_1),
                   user_response={"answer": "confirmed", "answered_at": "2026-08-27"})


def test_user_response_rejects_an_unknown_field():
    _check_rejects("check.user_response has unknown fields",
                   user_response={"answer": "confirmed", "answered_at": "2026-08-27", "extra": 1})


def test_user_response_note_is_optional_and_capped():
    row = _check(user_response={"answer": "confirmed", "answered_at": "2026-08-27", "note": "looks right"})
    assert row["user_response"]["note"] == "looks right"
    _check_rejects("longer than 500",
                   user_response={"answer": "confirmed", "answered_at": "2026-08-27", "note": "x" * 501})


def test_user_response_answered_at_must_be_an_iso_date():
    _check_rejects("answered_at must be an ISO date",
                   user_response={"answer": "confirmed", "answered_at": "August 2026"})


# ───────────────────────── J. the verdict of record ─────────────────────────

def test_numeric_no_user_response_final_verdict_equals_engine_verdict():
    row = _check()
    assert row["final_verdict"] == row["engine_verdict"]
    assert row["verdict_source"] == "engine"


def test_numeric_confirmed_keeps_engine_verdict_under_a_user_source():
    row = _check(user_response={"answer": "confirmed", "answered_at": "2026-08-27"})
    assert row["final_verdict"] == row["engine_verdict"]
    assert row["verdict_source"] == "user"


def test_override_preserves_engine_verdict_and_never_overwrites_it():
    """The design's own words: an override rejects a met/near_line finding,
    but engine_verdict keeps the engine's original — never overwritten."""
    met_obs = {"value": 21.0, "as_of": "2026-08-20", "source": "10-Q", "period": "FY2027Q2"}
    row = _check(observation=met_obs, user_response={"answer": "overridden", "answered_at": "2026-08-27"})
    assert row["engine_verdict"] == "met", "the engine's own read must survive an override untouched"
    assert row["final_verdict"] == "not_met", "the override rejects the engine's met finding"
    assert row["verdict_source"] == "user"


def test_event_check_without_a_user_response_is_unknown_not_a_guess():
    """Store, never infer: without the user's answer there IS no verdict."""
    row = _event_check()
    assert row["engine_verdict"] is None, "the engine never computes an event verdict"
    assert (row["final_verdict"], row["verdict_source"]) == ("unknown", "engine")
    assert "user_response" not in row


def test_event_yes_is_met_and_no_is_not_met_both_user_sourced():
    yes = _event_check(user_response={"answer": "yes", "answered_at": "2026-08-27"})
    assert (yes["final_verdict"], yes["verdict_source"]) == ("met", "user")
    no = _event_check(user_response={"answer": "no", "answered_at": "2026-08-27"})
    assert (no["final_verdict"], no["verdict_source"]) == ("not_met", "user")


def test_a_lookup_that_did_not_succeed_is_unknown_regardless_of_kind():
    numeric = _check(lookup_status="not_checked", observation=None)
    event = _event_check(lookup_status="not_checked", observation=None)
    assert numeric["final_verdict"] == event["final_verdict"] == "unknown"
    assert numeric["verdict_source"] == event["verdict_source"] == "engine"


# ───────────────────────── K. information_state ─────────────────────────

def test_information_state_is_null_unless_lookup_status_is_ok():
    row = _check(lookup_status="not_checked", observation=None)
    assert row["information_state"] is None


def test_first_check_against_the_slots_own_baseline_is_no_new_data_on_a_match():
    """GROWTH's baseline: value 38.0, period FY2027Q1, document '8-K 2026-05-20'."""
    slot = _slot()
    row = conditions.build_check(
        {"lookup_status": "ok",
         "observation": {"value": 38.0, "as_of": "2026-05-21", "source": "Q1 FY2027 press release",
                         "period": "FY2027Q1", "document": "8-K 2026-05-20"}},
        slot=slot, previous=None, check_id="chk-1", date_end="2026-08-26")
    assert row["information_state"] == "no_new_data", \
        "same period, same document, same value re-read later must not read as new"


def test_first_check_against_the_slots_own_baseline_is_restated_on_a_changed_value():
    slot = _slot()
    row = conditions.build_check(
        {"lookup_status": "ok",
         "observation": {"value": 37.0, "as_of": "2026-05-21", "source": "amended press release",
                         "period": "FY2027Q1", "document": "8-K 2026-05-20"}},
        slot=slot, previous=None, check_id="chk-1", date_end="2026-08-26")
    assert row["information_state"] == "restated"


def test_first_check_with_no_slot_baseline_is_new_period():
    slot = _slot(observation=None)  # unmapped/no_baseline: nothing to compare against yet
    row = conditions.build_check({"lookup_status": "ok", "observation": dict(NUMERIC_OBS_1)},
                                 slot=slot, previous=None, check_id="chk-1", date_end="2026-08-26")
    assert row["information_state"] == "new_period"


def test_a_new_effective_period_is_new_period_even_with_a_previous_check():
    first = _check()
    second = _check(previous=first, observation={**NUMERIC_OBS_1, "value": 34.0,
                                                 "period": "FY2027Q3", "document": "10-Q 2026-11-20"})
    assert second["information_state"] == "new_period"


def test_same_period_same_document_same_value_is_no_new_data():
    first = _check()
    second = _check(previous=first, observation=dict(NUMERIC_OBS_1))
    assert second["information_state"] == "no_new_data"


def test_same_period_different_value_is_restated():
    first = _check()
    second = _check(previous=first, observation={**NUMERIC_OBS_1, "value": 34.0})
    assert second["information_state"] == "restated"


def test_same_period_different_document_is_restated_even_with_the_same_value():
    first = _check()
    second = _check(previous=first, observation={**NUMERIC_OBS_1, "document": "10-Q/A 2026-08-25"})
    assert second["information_state"] == "restated"


def test_as_of_is_the_effective_period_when_there_is_no_reporting_period():
    """Price-like conditions have no reporting period; a new quote date IS new
    data for them."""
    price_1 = {"value": 100.0, "as_of": "2026-08-20", "source": "quote"}
    price_2 = {"value": 100.0, "as_of": "2026-08-21", "source": "quote"}
    first = _check(observation=price_1)
    moved = _check(previous=first, observation=price_2)
    assert moved["information_state"] == "new_period", "no period field: as_of is the effective period"
    same = _check(previous=first, observation=dict(price_1))
    assert same["information_state"] == "no_new_data", "identical as_of, source, and value: nothing new"


# External review (round 1), MARK: the numeric marker comparison had no test
# pinning that it is genuine numeric equality, not some textual proxy of it.
# All four probes below hold period and document fixed and vary only the
# marker, so each one isolates the marker comparison specifically.

def _marker_probe(first_value, second_value):
    obs_1 = {"value": first_value, "as_of": "2026-08-20", "source": "quote",
             "period": "FY2027Q2", "document": "10-Q 2026-08-20"}
    obs_2 = {"value": second_value, "as_of": "2026-08-27", "source": "quote",
             "period": "FY2027Q2", "document": "10-Q 2026-08-20"}
    first = _check(observation=obs_1)
    return _check(previous=first, observation=obs_2)


def test_marker_0_3_and_0_30_are_the_same_value_not_different_text():
    assert _marker_probe(0.3, 0.30)["information_state"] == "no_new_data"


def test_marker_30_and_negative_30_are_different_values():
    """Guards specifically against copy-pasting restates_threshold's
    magnitude-only (abs-value) comparison style into this gate: that style is
    correct for detecting a threshold leak (#412, restates_threshold), but
    wrong here — a swing from +30 to -30 is not "the same number, unsigned",
    it is a completely different reading."""
    assert _marker_probe(30, -30)["information_state"] == "restated"


def test_marker_30_and_3000_are_different_values():
    """Guards against a scale-tolerant comparison (the same restates_threshold
    precedent checks value*100/value/100 for a percent-vs-decimal leak) being
    reused here, where it would wrongly treat a 100x change as "the same
    number at a different scale"."""
    assert _marker_probe(30, 3000)["information_state"] == "restated"


def test_marker_positive_and_negative_zero_are_the_same_value():
    """The genuinely mutation-sensitive pin. 0.3 and 0.30 parse to the exact
    same float and Python's str() of that one float object is necessarily
    identical either way (str(0.3) == str(0.30) == '0.3') — no implementation
    of the marker comparison, correct or broken, can tell those two apart, so
    that probe alone cannot catch a comparison that silently degraded to
    string equality. 0.0 and -0.0 can: they are numerically equal
    (0.0 == -0.0) but stringify to different text ('0.0' vs '-0.0'), so this
    is the pair that actually goes red under a stringify mutation — see the
    mutation-verification log in the PR body."""
    assert _marker_probe(0.0, -0.0)["information_state"] == "no_new_data"


# ───────────────────────── L. the check store ─────────────────────────

def test_previous_check_for_resolves_the_previous_ok_check_on_the_same_line():
    slot = _slot()
    first = _check(slot=slot)
    assert conditions.previous_check_for([first], [slot], slot) is first


def test_previous_check_for_ignores_a_failed_check():
    slot = _slot()
    first_ok = _check(slot=slot, check_id="chk-1")
    failed = _check(slot=slot, check_id="chk-2", lookup_status="failed", observation=None)
    assert conditions.previous_check_for([first_ok, failed], [slot], slot) is first_ok


def test_previous_check_for_crosses_a_revision_boundary():
    """A revised slot must inherit its line's check history: the criterion's
    wording changed, not the world the checks were watching."""
    root = _slot()
    ok_on_root = _check(slot=root, check_id="chk-1")
    child = _child_slot(root)
    assert conditions.previous_check_for([ok_on_root], [root, child], child) is ok_on_root


def test_previous_check_for_returns_none_when_the_line_has_no_ok_check_yet():
    slot = _slot()
    assert conditions.previous_check_for([], [slot], slot) is None


def test_load_checks_reports_the_lines_it_could_not_read():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "condition_checks.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"check_id": "chk-a", "slot_id": "slot-a"}\n')
            handle.write("{not json\n")
            handle.write('{"no_check_id": true}\n')
            handle.write('{"check_id": "chk-b", "slot_id": "slot-b"}\n')
        checks, unreadable = conditions.load_checks(path)
        assert [row["check_id"] for row in checks] == ["chk-a", "chk-b"]
        assert unreadable == 2, "both the unparseable line and the unidentifiable row count"


def test_load_checks_on_a_missing_file_is_empty_not_an_error():
    assert conditions.load_checks(os.path.join("no", "such", "condition_checks.jsonl")) == ([], 0)


# ─────────── O. the two alerts the engine cannot derive (#412 / #434) ───────────

def test_an_event_alert_is_stored_only_when_it_fired():
    """A `false` is the ordinary state of every quiet event check. Writing it
    everywhere would make the absence of an alert look like a recorded decision."""
    quiet = _event_check()
    assert "event_alert" not in quiet, quiet
    assert _event_check(event_alert=False).get("event_alert") is None
    assert _event_check(event_alert=True)["event_alert"] is True


def test_an_event_alert_never_decides_the_event():
    """It raises the question and settles nothing: without the user's answer the
    verdict is still unknown, alert or no alert."""
    row = _event_check(event_alert=True)
    assert (row["final_verdict"], row["verdict_source"]) == ("unknown", "engine")
    assert row["engine_verdict"] is None


def test_an_event_alert_is_refused_on_a_numeric_slot():
    """A numeric crossing is the engine's own comparison. Accepting an asserted
    one would put an agent's reading on the same footing as the computation."""
    _check_rejects("event_alert is for an event condition", event_alert=True)


def test_an_event_alert_requires_an_observation_behind_it():
    _check_rejects("event_alert requires lookup_status ok", slot=_event_slot(),
                   lookup_status="failed", observation=None, event_alert=True)


def test_a_basis_alert_carries_a_note_and_optional_provenance():
    row = _check(basis_alert={"note": "the reporting segment was restated this quarter",
                              "source": "10-Q", "as_of": "2026-08-20"})
    assert row["basis_alert"]["note"].startswith("the reporting segment")
    assert row["basis_alert"]["source"] == "10-Q" and row["basis_alert"]["as_of"] == "2026-08-20"
    bare = _check(basis_alert={"note": "the fiscal calendar shifted"})
    assert set(bare["basis_alert"]) == {"note"}


def test_a_basis_alert_fails_closed_on_its_own_fields():
    _check_rejects("basis_alert.note is required", basis_alert={"source": "10-Q"})
    _check_rejects("basis_alert has unknown fields", basis_alert={"note": "x", "verdict": "met"})
    _check_rejects("basis_alert.as_of must be an ISO date",
                   basis_alert={"note": "x", "as_of": "last quarter"})
    _check_rejects("basis_alert requires lookup_status ok", lookup_status="failed",
                   observation=None, basis_alert={"note": "x"})


def test_a_basis_alert_does_not_move_the_verdict():
    """Doubt about the basis is not a verdict on the line. The comparison the
    engine can still perform is still performed and still reported."""
    plain, doubted = _check(), _check(basis_alert={"note": "the segment was restated"})
    assert plain["engine_verdict"] == doubted["engine_verdict"] == "not_met"
    assert plain["final_verdict"] == doubted["final_verdict"]


def test_the_envelope_cannot_report_what_the_user_said():
    """External review (round 1), BLOCK: `user_response` and `basis_resolution`
    used to be envelope fields. An agent could therefore write
    `{"answer": "overridden"}` beside its own lookup and record a verdict for a
    question the user was never shown — and the stored row would be
    indistinguishable, forever, from one they actually answered. They are
    keyword arguments now, and the envelope is refused *by name* rather than as
    a generic unknown field, because the reason matters to whoever hits it."""
    for smuggled in ({"answer": "overridden", "answered_at": "2026-08-27"},
                     {"answer": "confirmed", "answered_at": "2026-08-27"}):
        try:
            conditions.build_check({"lookup_status": "ok", "observation": dict(NUMERIC_OBS_1),
                                    "user_response": smuggled},
                                   slot=_slot(), previous=None, check_id="chk", date_end="2026-08-26")
        except conditions.ConditionError as exc:
            assert "must not carry user_response" in str(exc), exc
            assert "question they were actually shown" in str(exc), exc
        else:
            raise AssertionError("an envelope carrying user_response must be refused")
    try:
        conditions.build_check({"lookup_status": "ok", "observation": dict(NUMERIC_OBS_1),
                                "basis_alert": {"note": "x"}, "basis_resolution": "kept"},
                               slot=_slot(), previous=None, check_id="chk", date_end="2026-08-26")
    except conditions.ConditionError as exc:
        assert "must not carry basis_resolution" in str(exc), exc
    else:
        raise AssertionError("an envelope carrying basis_resolution must be refused")


def test_the_engine_may_still_fold_in_an_answer_it_posed():
    """The other half: the refusal is about the *envelope*, not about the field.
    An answer to a question the engine actually asked still lands on the row."""
    row = conditions.build_check({"lookup_status": "ok", "observation": dict(NUMERIC_OBS_1)},
                                 slot=_slot(), previous=None, check_id="chk", date_end="2026-08-26",
                                 user_response={"answer": "overridden", "answered_at": "2026-08-27"})
    assert row["user_response"]["answer"] == "overridden"
    assert (row["final_verdict"], row["verdict_source"]) == ("not_met", "user")
    assert row["engine_verdict"] == "not_met", "the engine's own read is never overwritten"


def test_the_input_schema_no_longer_advertises_the_engine_assigned_fields():
    """The readable contract must not invite what the code refuses."""
    schema = _schema("condition-check.schema.json")
    offered = set(schema["properties"]["input"]["properties"])
    assert not (offered & set(conditions._ENGINE_ASSIGNED_CHECK_FIELDS)), offered
    assert offered == set(conditions._CHECK_FIELDS), \
        "the documented envelope and the accepted envelope are one fact in two places"


def test_a_basis_resolution_requires_the_question_it_answers():
    _check_rejects("basis_resolution requires a basis_alert", basis_resolution="kept")
    _check_rejects("basis_resolution must be one of",
                   basis_alert={"note": "x"}, basis_resolution="ignored")
    kept = _check(basis_alert={"note": "x"}, basis_resolution="kept")
    assert kept["basis_resolution"] == "kept"


# ───────── P. line history: what was last looked at vs last found ─────────

def test_last_check_for_counts_a_failed_lookup_as_attention():
    """Ordering the due list by successful lookups alone would park a line that
    fails every week at the front of the queue forever."""
    slot = _slot()
    ok = _check(check_id="chk-1", date_end="2026-08-01")
    failed = _check(check_id="chk-2", date_end="2026-08-26",
                    lookup_status="failed", observation=None)
    checks = [ok, failed]
    assert conditions.previous_check_for(checks, [slot], slot)["check_id"] == "chk-1"
    assert conditions.last_check_for(checks, [slot], slot)["check_id"] == "chk-2"
    assert conditions.last_check_for([], [slot], slot) is None


def test_line_history_crosses_a_revision_boundary():
    parent = _slot()
    child = _child_slot(parent)
    checks = [_check(check_id="chk-parent", date_end="2026-08-01"),
              _check(slot=child, check_id="chk-child", date_end="2026-09-01",
                     lookup_status="not_checked", observation=None)]
    rows = conditions.checks_for_line(checks, [parent, child], child)
    assert [row["check_id"] for row in rows] == ["chk-parent", "chk-child"], \
        "a re-stated criterion inherits its line's history rather than starting over"
    assert conditions.last_check_for(checks, [parent, child], child)["check_id"] == "chk-child"
    assert conditions.previous_check_for(checks, [parent, child], child)["check_id"] == "chk-parent"


# ───────────────────────── M. the firewall is physical ─────────────────────────

def test_conditions_module_never_imports_problems():
    """Check/verdict data must never reach rules.jsonl, held_streak, or the
    graduation statistics (docs/development-guide.md section 5). If
    conditions.py ever imports problems, that door is open even if nothing
    walks through it yet."""
    source_path = os.path.join(REPO, "skills", "fomo-kernel", "engine", "conditions.py")
    with open(source_path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=source_path)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "problems" not in imported, \
        "conditions.py must never import problems: that would be a path into rules.jsonl/held_streak"


# ─────────────────── N. produced rows match the schema's declared shape ───────────────────

def test_condition_check_schema_pins_the_same_vocabulary_as_the_code():
    """Offline suite has no jsonschema validator; pin the vocabulary (mirrors
    test_review_v2.py's test_schemas_cover_due_revisit_and_resolutions)."""
    schema = _schema("condition-check.schema.json")
    assert set(schema["properties"]["lookup_status"]["enum"]) == set(conditions.LOOKUP_STATUSES)
    info_enum = schema["properties"]["information_state"]["enum"]
    assert set(x for x in info_enum if x is not None) == set(conditions.INFORMATION_STATES)
    assert None in info_enum
    verdict_enum = schema["properties"]["engine_verdict"]["enum"]
    assert set(x for x in verdict_enum if x is not None) == set(conditions.VERDICTS)
    assert None in verdict_enum
    assert set(schema["properties"]["final_verdict"]["enum"]) == set(conditions.VERDICTS)
    assert set(schema["properties"]["verdict_source"]["enum"]) == set(conditions.VERDICT_SOURCES)
    answers = set(schema["$defs"]["user_response"]["properties"]["answer"]["enum"])
    assert answers == set(conditions.NUMERIC_ANSWERS) | set(conditions.EVENT_ANSWERS)
    # External review (round 1), MARK: the answer enum pools two kinds'
    # vocabularies; the split itself is engine-enforced (_user_response), and
    # the schema should say so rather than silently look wider than the code.
    assert "$comment" in schema["$defs"]["user_response"]["properties"]["answer"]
    assert set(schema["properties"]["basis_resolution"]["enum"]) == set(conditions.BASIS_RESOLUTIONS)
    assert schema["properties"]["event_alert"]["const"] is True, \
        "a stored event_alert is only ever the fired state; false is written as absence"


def test_check_observation_schema_expresses_the_value_summary_xor():
    """External review (round 1), MARK: the code already enforces value XOR
    summary (_check_observation); the schema should say so structurally, not
    only in prose. This suite has no jsonschema validator (per this schema
    file's own docstring) and none is added here — a structural read of the
    oneOf shape is the pin, mirroring this file's existing
    "no jsonschema dependency, pin the vocabulary" idiom."""
    schema = _schema("condition-check.schema.json")
    one_of = schema["$defs"]["observation"]["oneOf"]
    assert len(one_of) == 2, "value XOR summary is exactly two alternatives"
    # Pair each branch's own required with its own forbidden — not just
    # independent membership across the two branches, which could pass on a
    # mismatched (and useless) pairing too.
    branches = {frozenset(b["required"]): frozenset(b["not"]["required"]) for b in one_of}
    assert branches.get(frozenset({"value"})) == frozenset({"summary"}), \
        "the value branch must require value and forbid summary"
    assert branches.get(frozenset({"summary"})) == frozenset({"value"}), \
        "the summary branch must require summary and forbid value"


def test_produced_check_rows_match_the_schemas_declared_shape():
    """Structural drift check: every field a produced row carries is declared
    in the schema, and every field the schema requires is on the row. Not a
    full jsonschema validator (this suite carries no such dependency, per the
    schema file's own docstring) — a same-keys pin that fails the moment the
    two drift."""
    schema = _schema("condition-check.schema.json")
    allowed = set(schema["properties"])
    required = set(schema["required"])
    rows = [_check(), _check(lookup_status="failed", observation=None), _event_check(),
            _event_check(user_response={"answer": "yes", "answered_at": "2026-08-27"}),
            _event_check(event_alert=True),
            _check(basis_alert={"note": "the segment was restated", "source": "10-Q",
                                "as_of": "2026-08-20"}, basis_resolution="kept")]
    for row in rows:
        assert required <= set(row), f"row is missing a required field: {required - set(row)}"
        assert set(row) <= allowed, f"row has a field the schema does not declare: {set(row) - allowed}"


def test_produced_slot_row_with_revises_matches_the_slot_schemas_declared_shape():
    schema = _schema("condition-slot.schema.json")
    allowed = set(schema["properties"])
    parent = _slot()
    child = _child_slot(parent)
    assert set(child) <= allowed, f"row has a field the schema does not declare: {set(child) - allowed}"
    assert set(schema["required"]) <= set(child)


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
