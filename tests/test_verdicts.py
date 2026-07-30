#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Behavior verdicts (#446 cut 1) -- unit and wiring tests. Offline,
deterministic, no pytest.

What this file settles:
  A. RULE_PARAMS / RULE_PARAM_DIGEST agreement, and a cross-check against
     horizon.py's live thresholds -- together, the mechanical block against a
     parameter change landing in either file without a version bump.
  B. replay() recomputes exit_too_fast / held_too_long / None purely from its
     five frozen scalar inputs, refuses an unknown rule id or version, and its
     signature is pinned to exactly those five scalars.
  C. build_horizon_verdict(): produced rows match the schema's declared shape,
     carry no `input` sub-object, replay to their own outcome, and a verdict
     is refused (returns None) rather than guessed when a required ingredient
     is missing.
  D. review._horizon_markers_all is the untruncated sibling of
     review._horizon_markers: same computation, the display slice is always a
     prefix of the full list, and review._horizon_verdict_rows is built from
     the untruncated list -- never the display cap.
  E. Wiring: a finalized review appends verdict rows for a long-held position;
     a retry is idempotent; a third consecutive period appends a second row on
     the same (never-revised) subject; nothing reaches rules.jsonl or the
     mechanical rule reconciliation. A distinct --session-nonce reviewing
     identical state and date must not duplicate a verdict (BLOCK 2).
  F. The firewall is physical: verdicts.py never imports problems.
  G. Replay survives supersession -- the two required tests: a verdict
     replays identically after the profile label it references is superseded,
     and after the thesis row its subject points at is superseded.
  H. Frozen replay corpus (external review, #446 BLOCK 1): literal
     (subject_value, observed_value, observed_closed) -> outcome rows at
     every version-1 threshold boundary, independent of RULE_PARAMS -- what a
     digest edit alone cannot silence.
  I. verdict_identity() / dedupe_against_other_sessions() (external review,
     #446 BLOCK 2): a verdict's own (subject, rule, period) identity, checked
     across sessions before a row ever reaches _append_session_rows' own
     session-scoped idempotency, and the deliberate fail-closed choice for a
     same-identity outcome conflict.

The weekly-cadence wiring tests in section E reuse test_review_v2's CLI
harness (subprocess review.py prepare/finalize), where that machinery already
lives, rather than re-implementing it here.
"""
import ast
import inspect
import json
import os
import pathlib
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ENGINE = os.path.join(REPO, "skills", "fomo-kernel", "engine")
SCHEMAS = pathlib.Path(REPO) / "skills" / "fomo-kernel" / "schemas"
sys.path.insert(0, ENGINE)
sys.path.insert(0, HERE)
import horizon  # noqa: E402
import problems as problems_engine  # noqa: E402
import review as review_engine  # noqa: E402
import thesis as thesis_engine  # noqa: E402
import verdicts  # noqa: E402
import test_review_v2 as v2  # noqa: E402


def _schema(name):
    with open(SCHEMAS / name, encoding="utf-8") as handle:
        return json.load(handle)


def _marker(**overrides):
    row = {"cycle_id": "PLTR#2026-01-01#1", "ticker": "PLTR", "horizon": "quarters",
           "holding_days": 194, "kind": "held_too_long", "exited": False, "maturity": "inferred"}
    row.update(overrides)
    return row


def _build(marker=None, **overrides):
    base = dict(row_id="thesis-update-abc", session_id="2026-07-14__s1",
                date_end="2026-07-14", window_to="2026-07-14")
    base.update(overrides)
    return verdicts.build_horizon_verdict(marker if marker is not None else _marker(), **base)


# ─────────────────────── A. rule-version discipline ───────────────────────

def test_rule_param_digest_matches_its_pinned_hash_per_rule_version():
    """The mechanical block: editing a threshold inside an existing
    RULE_PARAMS version without regenerating and updating that version's
    pinned literal turns this red. See verdicts.py's module docstring,
    'Rule-version discipline'. RULE_PARAM_DIGEST holds one frozen literal per
    (rule_id, version) -- never one combined hash over the whole table
    (external review, #446 BLOCK 1), so this asserts each entry separately:
    a failure names the exact rule and version whose parameters moved,
    rather than just 'something in RULE_PARAMS changed'. Each literal is
    frozen, not a live recomputation -- a live one would always agree with
    itself and make this assertion vacuous."""
    for (rule_id, version), pinned in verdicts.RULE_PARAM_DIGEST.items():
        live = verdicts.RULE_PARAMS[rule_id][version]
        assert verdicts._params_digest(live) == pinned, (rule_id, version)


def test_rule_param_digest_entries_match_rule_params_exactly():
    """The inverse direction: every (rule_id, version) RULE_PARAMS carries
    needs exactly one pinned digest entry -- a new version landing with no
    matching digest would otherwise ship undigested, and a stale digest entry
    for a version RULE_PARAMS no longer carries would never be checked
    against anything."""
    live_keys = {(rule_id, version) for rule_id, versions in verdicts.RULE_PARAMS.items()
                for version in versions}
    assert live_keys == set(verdicts.RULE_PARAM_DIGEST), \
        "RULE_PARAM_DIGEST must name exactly the (rule_id, version) pairs RULE_PARAMS carries"


def test_current_horizon_rule_version_matches_horizon_pys_live_thresholds():
    """Closes the gap the digest test alone leaves open: someone editing
    horizon.py's EXIT_FAST/HELD_LONG directly, without ever touching
    verdicts.py, would not change RULE_PARAMS's own digest -- nothing in
    RULE_PARAMS is a live reference to horizon.py's constants (deliberately;
    see the module docstring). This is the other half of 'changing a
    threshold without bumping the version turns the suite red', for an edit
    that lands in horizon.py instead of verdicts.py."""
    live = verdicts.RULE_PARAMS["horizon_contradiction"][verdicts.HORIZON_RULE_VERSION]
    assert live["exit_fast"] == dict(horizon.EXIT_FAST)
    assert live["held_long"] == dict(horizon.HELD_LONG)


# ─────────────────────────────── B. replay() ───────────────────────────────

def test_replay_recomputes_every_horizon_contradiction_branch():
    cases = [
        ("years", 30, True, "exit_too_fast"),    # exited early on a years thesis
        ("years", 200, True, None),               # exited, but not early
        ("years", 400, False, None),               # years has no held_long entry -- long-hold is normal
        ("quarters", 10, True, "exit_too_fast"),
        ("quarters", 194, False, "held_too_long"),
        ("weeks", 194, False, "held_too_long"),
        ("weeks", 10, True, None),                  # weeks has no exit_fast entry -- quick-exit is normal
        ("週", 194, False, "held_too_long"),          # legacy zh alias normalizes the same as "weeks"
        ("not-a-real-horizon", 400, False, None),     # unmapped value -> silent skip, never a guess
        (None, 400, False, None),
    ]
    for subject_value, observed_value, closed, expected in cases:
        got = verdicts.replay("horizon_contradiction", 1, subject_value, observed_value, closed)
        assert got == expected, (subject_value, observed_value, closed, got, expected)


def test_replay_rejects_an_unknown_rule_id_or_version():
    try:
        verdicts.replay("not_a_real_rule", 1, "years", 30, True)
        assert False, "an unknown rule id must raise, not silently guess"
    except verdicts.VerdictError as exc:
        assert "not_a_real_rule" in str(exc)
    try:
        verdicts.replay("horizon_contradiction", 99, "years", 30, True)
        assert False, "an unknown rule version must raise, not silently fall back to another one"
    except verdicts.VerdictError as exc:
        assert "99" in str(exc)


def test_replay_signature_is_exactly_the_five_frozen_scalars():
    """A structural lock on the replay contract: adding a parameter here (a
    labels store, a live thesis fold, anything reachable beyond the
    verdict's own frozen fields) is exactly the mutation the two
    supersession tests in section G exist to catch; this pins the shape so
    that a mutation of that kind cannot even be expressed without this test
    noticing first."""
    params = list(inspect.signature(verdicts.replay).parameters)
    assert params == ["rule_id", "rule_version", "subject_value", "observed_value", "observed_closed"]


# ───────────────────────── C. build_horizon_verdict() ─────────────────────────

def test_build_horizon_verdict_matches_the_schemas_declared_shape():
    schema = _schema("behavior-verdict.schema.json")
    allowed = set(schema["properties"])
    required = set(schema["required"])
    row = _build()
    assert row is not None
    assert required <= set(row), f"row is missing a required field: {required - set(row)}"
    assert set(row) <= allowed, f"row has a field the schema does not declare: {set(row) - allowed}"
    assert set(row["rule"]) == set(schema["properties"]["rule"]["required"])
    assert set(row["subject"]) == set(schema["properties"]["subject"]["required"])
    assert set(row["observed"]) == set(schema["properties"]["observed"]["required"])
    assert set(row["observed"]["window"]) == set(
        schema["properties"]["observed"]["properties"]["window"]["required"])
    assert row["rule"] == {"id": "horizon_contradiction", "version": verdicts.HORIZON_RULE_VERSION}
    assert row["subject"] == {"store": "theses", "row_id": "thesis-update-abc",
                              "field": "horizon", "value": "quarters"}
    assert row["observed"] == {"metric": "holding_days", "value": 194,
                               "window": {"from": "2026-01-01", "to": "2026-07-14"}, "closed": False}
    assert row["outcome"] == "held_too_long"
    assert row["verdict_source"] == "engine"
    assert row["date_end"] == "2026-07-14"
    assert row["session_id"] == "2026-07-14__s1"


def test_behavior_verdict_schema_has_no_input_sub_object():
    """The issue's explicit design constraint: every field here is
    engine-assigned. An agent able to write its own verdict could record an
    adjudication nobody made, undetectably after the fact -- the same reason
    condition-check.schema.json refuses user_response/basis_resolution on its
    own envelope."""
    schema = _schema("behavior-verdict.schema.json")
    assert "input" not in schema["properties"]
    assert schema["additionalProperties"] is False


def test_behavior_verdict_schema_pins_verdict_source_vocabulary():
    schema = _schema("behavior-verdict.schema.json")
    assert set(schema["properties"]["verdict_source"]["enum"]) == set(verdicts.VERDICT_SOURCES)
    assert "agent" not in schema["properties"]["verdict_source"]["enum"], \
        "agent is deliberately absent from this vocabulary until #414 lands"


def test_build_horizon_verdict_replays_to_its_own_outcome():
    row = _build()
    replayed = verdicts.replay(row["rule"]["id"], row["rule"]["version"],
                               row["subject"]["value"], row["observed"]["value"],
                               row["observed"]["closed"])
    assert replayed == row["outcome"]


def test_build_horizon_verdict_skips_rather_than_guesses():
    assert _build(row_id=None) is None, \
        "no thesis row identity to freeze against -- must not guess one"
    assert _build(session_id=None) is None, \
        "no session id -- session._append_session_rows cannot dedupe without one"
    assert _build(date_end=None) is None
    assert _build(window_to=None) is None, \
        "no window to freeze -- must not write a verdict with a guessed span"
    assert _build(marker=_marker(kind=None)) is None, \
        "a non-triggering marker (kind None) must never become a verdict"
    assert _build(marker=_marker(kind="something_else")) is None
    assert _build(marker=_marker(horizon="not-a-real-horizon")) is None, \
        "an unmapped horizon value must be skipped silently, never written as-is"
    assert _build(marker=_marker(holding_days=None)) is None


# ──────────── D. _horizon_markers_all is the untruncated sibling ────────────

def test_horizon_markers_all_is_untruncated_and_the_display_slice_is_its_prefix():
    """A realistic multi-position portfolio: five theses, each far enough
    past its own quarters threshold (180 days) to trigger held_too_long.
    review._horizon_markers (the existing, display-facing function) must
    still cap at HORIZON_MARKER_LIMIT; the new _horizon_markers_all must
    return every one of them, and the capped list must be an exact PREFIX of
    the full one -- same computation, same ranking, just not cut short. This
    is the regression lock for #446's central design claim."""
    state = {"date_end": "2026-07-14", "holdings": {"positions": {
        "NVDA": {"cycle_id": "NVDA#2025-11-01#1", "cost": 9000},
        "AMD": {"cycle_id": "AMD#2025-10-15#1", "cost": 8000},
        "SMCI": {"cycle_id": "SMCI#2025-09-01#1", "cost": 7000},
        "AVGO": {"cycle_id": "AVGO#2025-08-01#1", "cost": 6000},
        "ARM": {"cycle_id": "ARM#2025-12-01#1", "cost": 5000},
    }}}
    theses = [
        {"cycle_id": "NVDA#2025-11-01#1", "ticker": "NVDA", "horizon": "quarters"},
        {"cycle_id": "AMD#2025-10-15#1", "ticker": "AMD", "horizon": "quarters"},
        {"cycle_id": "SMCI#2025-09-01#1", "ticker": "SMCI", "horizon": "quarters"},
        {"cycle_id": "AVGO#2025-08-01#1", "ticker": "AVGO", "horizon": "quarters"},
        {"cycle_id": "ARM#2025-12-01#1", "ticker": "ARM", "horizon": "quarters"},
    ]
    cycle_ids = [row["cycle_id"] for row in theses]
    all_markers = review_engine._horizon_markers_all(state, theses, cycle_ids, [])
    capped = review_engine._horizon_markers(state, theses, cycle_ids, [])
    assert len(all_markers) == 5, all_markers
    assert all(m["kind"] == "held_too_long" for m in all_markers)
    assert len(capped) == review_engine.HORIZON_MARKER_LIMIT == 2
    assert capped == all_markers[:review_engine.HORIZON_MARKER_LIMIT], \
        "the display slice must be an exact prefix of the untruncated list"
    assert {m["cycle_id"] for m in all_markers} == set(cycle_ids)


def test_horizon_verdict_rows_are_built_from_the_untruncated_list_not_the_display_cap():
    """The brief's central requirement, exercised directly against
    review._horizon_verdict_rows: with more triggering positions than
    HORIZON_MARKER_LIMIT, the number of verdict rows built must exceed the
    display cap, and every triggering cycle must be represented."""
    date_end = "2026-07-14"
    theses = [
        {"cycle_id": "NVDA#2025-11-01#1", "ticker": "NVDA", "horizon": "quarters", "event_id": "ev-nvda"},
        {"cycle_id": "AMD#2025-10-15#1", "ticker": "AMD", "horizon": "quarters", "event_id": "ev-amd"},
        {"cycle_id": "SMCI#2025-09-01#1", "ticker": "SMCI", "horizon": "quarters", "event_id": "ev-smci"},
        {"cycle_id": "AVGO#2025-08-01#1", "ticker": "AVGO", "horizon": "quarters", "event_id": "ev-avgo"},
        {"cycle_id": "ARM#2025-12-01#1", "ticker": "ARM", "horizon": "quarters", "event_id": "ev-arm"},
    ]
    plan = {
        "route": "weekly_review",
        "engine_state": {"date_end": date_end, "holdings": {"positions": {
            row["ticker"]: {"cycle_id": row["cycle_id"], "cost": 1000} for row in theses
        }}},
        "state_snapshot": {"thesis_states": theses, "recent_exits": []},
    }
    rows = review_engine._horizon_verdict_rows(plan, "2026-07-14__probe")
    assert len(rows) == 5, rows
    assert len(rows) > review_engine.HORIZON_MARKER_LIMIT
    assert {row["subject"]["row_id"] for row in rows} == {
        "ev-nvda", "ev-amd", "ev-smci", "ev-avgo", "ev-arm"}
    assert all(row["date_end"] == date_end and row["session_id"] == "2026-07-14__probe" for row in rows)
    assert all(row["outcome"] == "held_too_long" for row in rows)


def test_horizon_verdict_rows_is_empty_for_snapshot_review_and_when_nothing_triggers():
    plan_snapshot = {"route": "snapshot_review", "engine_state": {"date_end": "2026-07-14"},
                     "state_snapshot": {}}
    assert review_engine._horizon_verdict_rows(plan_snapshot, "s1") == []
    plan_empty = {"route": "weekly_review", "engine_state": {"date_end": "2026-07-14",
                  "holdings": {"positions": {}}}, "state_snapshot": {}}
    assert review_engine._horizon_verdict_rows(plan_empty, "s1") == []


# ─────────────────── E. wiring: finalize appends verdict rows ───────────────────

def _week(tmp, root, date_end, tag, language="en"):
    """One full prepare -> answer -> finalize cycle through the real CLI, on
    the shared PLTR fixture (cycle_start 2026-01-01, horizon quarters
    declared week 1 via test_review_v2._answers' fixed thesis_updates).
    thesis_updates is submitted only when a position is actually missing a
    thesis, matching real agent behavior and avoiding a spurious re-revision
    on every week the fixture is reused."""
    plan = v2._prepare_dated(tmp, root, date_end, tag, language=language)
    answers = v2._answers(plan, commitment="skip")
    if not plan["missing_thesis_positions"]:
        answers["thesis_updates"] = []
    result = v2._finalize(tmp, root, plan, answers, tag)
    assert result["projection_error"] is None, result
    return plan, result


def _verdict_rows(root):
    path = pathlib.Path(root) / "verdicts.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_finalized_review_appends_a_verdict_row_for_a_long_held_position():
    """#446 cut 1's wiring, end to end through the real CLI. PLTR's thesis
    (horizon quarters, declared week 1) is still held 222 days later at week
    2, past the 180-day threshold -- held_too_long fires, and unlike every
    prior period, this time it lands on disk."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        _week(tmp, root, "2026-07-14", "w1")
        assert _verdict_rows(root) == [], \
            "no prior thesis exists yet at week 1's prepare time -- nothing to judge"

        plan2, _ = _week(tmp, root, "2026-08-11", "w2")
        assert plan2["state_snapshot"]["horizon_markers"], \
            "fixture must actually trigger a horizon marker on week 2"

        rows = _verdict_rows(root)
        assert len(rows) == 1, rows
        row = rows[0]
        assert row["date_end"] == "2026-08-11"
        assert row["outcome"] == "held_too_long"
        assert row["subject"]["store"] == "theses"
        assert row["subject"]["field"] == "horizon"
        assert row["subject"]["value"] == "quarters"
        assert row["observed"]["metric"] == "holding_days"
        assert row["observed"]["value"] == 222, row  # 2026-01-01 -> 2026-08-11
        assert row["observed"]["window"] == {"from": "2026-01-01", "to": "2026-08-11"}
        assert row["observed"]["closed"] is False
        assert row["verdict_source"] == "engine"
        assert row["rule"] == {"id": "horizon_contradiction", "version": verdicts.HORIZON_RULE_VERSION}

        replayed = verdicts.replay(row["rule"]["id"], row["rule"]["version"],
                                   row["subject"]["value"], row["observed"]["value"],
                                   row["observed"]["closed"])
        assert replayed == row["outcome"], "a freshly written row must itself satisfy the replay gate"

        rules_path = pathlib.Path(root) / "rules.jsonl"
        assert not rules_path.exists() or not [
            line for line in rules_path.read_text(encoding="utf-8").splitlines() if line.strip()], \
            "a verdict must never become a rules.jsonl row"
        stats = problems_engine.snapshot(str(pathlib.Path(root) / "problems.jsonl"),
                                         str(rules_path), today="2026-08-11")
        assert not stats["rules_check"], \
            "a behavior verdict must not appear in the mechanical rule reconciliation"


def test_a_third_consecutive_period_appends_a_second_row_on_the_same_subject():
    """held_too_long re-triggering every period is not noise -- it is the
    material the profile layer (#446 cut 2) is meant to consume. Two
    genuinely different periods must both land on disk, both referencing the
    same thesis row (never revised across these weeks)."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        _week(tmp, root, "2026-07-14", "w1")
        _week(tmp, root, "2026-08-11", "w2")
        _week(tmp, root, "2026-09-08", "w3")
        rows = _verdict_rows(root)
        assert [row["date_end"] for row in rows] == ["2026-08-11", "2026-09-08"]
        assert [row["observed"]["value"] for row in rows] == [222, 250]
        assert len({row["subject"]["row_id"] for row in rows}) == 1, \
            "the thesis was never revised across these weeks -- both verdicts point at the same row"


def test_a_repeated_finalize_appends_one_verdict_row_and_a_changed_one_fails_closed():
    """Append-only state with an idempotent finalize is on the never-loosen
    list; a new projection inherits neither for free."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        _week(tmp, root, "2026-07-14", "w1")
        plan2 = v2._prepare_dated(tmp, root, "2026-08-11", "w2", language="en")
        answers = v2._answers(plan2, commitment="skip")
        answers["thesis_updates"] = []
        a_path = pathlib.Path(tmp) / "answers_retry.json"
        n_path = pathlib.Path(tmp) / "narrative_retry.json"
        a_path.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
        n_path.write_text(json.dumps(v2._narrative("en"), ensure_ascii=False), encoding="utf-8")
        args = ("--session-id", plan2["session_id"], "--root", root,
                "--answers", a_path, "--narrative", n_path)

        first = v2._run_finalize(*args)
        assert first.returncode == 0, first.stdout + first.stderr
        assert len(_verdict_rows(root)) == 1
        retry = v2._run_finalize(*args)               # documented-safe retry
        assert retry.returncode == 0, retry.stdout + retry.stderr
        assert len(_verdict_rows(root)) == 1, "an identical retry must not append a second row"


def _week_nonce(tmp, root, date_end, tag, nonce):
    """Like _week, but forces a genuinely distinct session over IDENTICAL
    state and date via --session-nonce (references/data-contract.md) -- the
    exact case BLOCK 2's fix targets. Reimplements _prepare_dated's few lines
    locally because that helper takes no nonce argument and adding one is out
    of this fix's scope (tests/test_review_v2.py is not touched)."""
    card_path, state_path = v2._artifacts(tmp)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["date_end"] = date_end
    dated = pathlib.Path(tmp) / f"state_{tag}.json"
    dated.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    csv_path = v2._trade_csv(tmp)
    run = v2._run("prepare", csv_path, "--root", root, "--language", "en",
                 "--card-json", card_path, "--state-json", dated,
                 "--session-nonce", nonce)
    assert run.returncode == 0, run.stdout + run.stderr
    plan = v2._pending_plan(root, run.stdout)
    answers = v2._answers(plan, commitment="skip")
    if not plan["missing_thesis_positions"]:
        answers["thesis_updates"] = []
    result = v2._finalize(tmp, root, plan, answers, tag)
    assert result["projection_error"] is None, result
    return plan, result


def test_a_distinct_session_nonce_over_identical_state_and_date_does_not_duplicate_a_verdict():
    """BLOCK 2 (external review, #446): --session-nonce exists deliberately
    so a user can review identical state as a genuinely distinct session
    (references/data-contract.md) -- two prepare/finalize cycles over the
    SAME date_end and SAME underlying state, differing only by nonce, are two
    real sessions with two different session_ids. Before this fix,
    _append_session_rows' idempotency (keyed on session_id alone) could not
    see that the second session's row was the same fact as the first's, and
    appended a duplicate. Both sessions independently compute the identical
    PLTR/quarters/held_too_long judgment at week 2 (222 days) -- only ONE
    verdict row must survive, carrying whichever session finalized first."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        _week(tmp, root, "2026-07-14", "w1")
        plan_a, _ = _week_nonce(tmp, root, "2026-08-11", "w2-alpha", "alpha")
        plan_b, _ = _week_nonce(tmp, root, "2026-08-11", "w2-beta", "beta")
        assert plan_a["session_id"] != plan_b["session_id"], \
            "the fixture must actually exercise two distinct sessions"

        rows = _verdict_rows(root)
        assert len(rows) == 1, \
            f"a distinct --session-nonce must not duplicate a verdict for the same triple: {rows}"
        assert rows[0]["date_end"] == "2026-08-11"
        assert rows[0]["outcome"] == "held_too_long"
        assert rows[0]["session_id"] == plan_a["session_id"], \
            "the first session to finalize keeps the row; the second's identical row is deduped"


# ─────────────────────── F. the firewall is physical ───────────────────────

def test_verdicts_module_never_imports_problems():
    """A verdict must never enter problems.check_rules' mechanical
    reconciliation (docs/development-guide.md section 5; conditions.py:66-72
    gives the same reasoning for conditions.jsonl). If verdicts.py ever
    imports problems, that door is open even if nothing walks through it
    yet."""
    source_path = os.path.join(ENGINE, "verdicts.py")
    with open(source_path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=source_path)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "problems" not in imported, \
        "verdicts.py must never import problems: that would be a path into rules.jsonl/held_streak"


# ─────────────────── G. replay survives supersession ───────────────────

def test_a_verdict_replays_identically_after_the_profile_label_it_references_is_superseded():
    """profile_label_id is a back-reference only, load-bearing for nothing
    (#446 cut 2 is not built yet -- there is no profile_labels.jsonl or
    fold_labels() for a real label to be superseded in). What is testable
    today, and is the whole content of the claim: replay's signature carries
    no channel for a labels store to reach it at all (locked structurally by
    test_replay_signature_is_exactly_the_five_frozen_scalars above), so
    varying profile_label_id on a stored row -- to a fresh id, to one that
    would name a hypothetically-since-superseded label, or clearing it
    entirely -- can never change what replay recomputes.

    Uses an exited/years marker rather than _build()'s default: a held/quarters
    marker's outcome happens to survive a naive subject_value substitution
    (both "quarters" and "weeks" cross held_too_long's threshold at 194 days),
    which would let a real mutation hide behind this test. exited=True on
    "years" (exit_too_fast, threshold 90) does not survive a substitution to
    "weeks" (no exit_fast entry at all) -- see the matching-mutation report."""
    row = _build(marker=_marker(horizon="years", holding_days=30, kind="exit_too_fast", exited=True))
    frozen = (row["rule"]["id"], row["rule"]["version"], row["subject"]["value"],
             row["observed"]["value"], row["observed"]["closed"])
    baseline = verdicts.replay(*frozen)
    assert baseline == row["outcome"]

    for candidate in (None, "label-momentum-v1",
                     "label-momentum-v2-supersedes-v1", "label-does-not-exist"):
        varied = dict(row)
        if candidate is None:
            varied.pop("profile_label_id", None)
        else:
            varied["profile_label_id"] = candidate
        again = verdicts.replay(varied["rule"]["id"], varied["rule"]["version"],
                                varied["subject"]["value"], varied["observed"]["value"],
                                varied["observed"]["closed"])
        assert again == baseline == row["outcome"], \
            "replay must be unaffected by profile_label_id, whatever it names or whether it is present"


def test_a_verdict_replays_identically_after_the_thesis_row_its_subject_points_at_is_superseded():
    """A real supersession, using thesis.py's actual revision mechanism: the
    thesis for one cycle is first written with horizon=years (holding 30
    days, exited -> exit_too_fast), then revised to horizon=weeks (which has
    no exit_fast threshold at all -- weeks exiting quickly is normal, per
    horizon.py's own docstring). The CURRENT fold for this cycle now reports
    "weeks", not "years", under a NEW event_id: subject.row_id names the
    FIRST revision, which is no longer the line's current head. A verdict
    frozen against that first row must still replay to exit_too_fast --
    proving replay reads only its own frozen subject.value, never a fresh
    fold of the row it names."""
    cycle_id = "PLTR#2026-01-01#1"
    row_v1 = {"cycle_id": cycle_id, "horizon": "years", "why": "long-term thesis"}
    state_v1 = thesis_engine.reconstruct_states([row_v1], [], [cycle_id])
    event_id_v1 = state_v1[0]["event_id"]
    assert state_v1[0]["horizon"] == "years"

    verdict = verdicts.build_horizon_verdict(
        _marker(cycle_id=cycle_id, horizon="years", holding_days=30, kind="exit_too_fast", exited=True),
        row_id=event_id_v1, session_id="2026-07-14__s1", date_end="2026-07-14", window_to="2026-01-31")
    assert verdict is not None and verdict["outcome"] == "exit_too_fast"
    assert verdict["subject"]["row_id"] == event_id_v1

    # The thesis is revised: the SAME cycle, a NEW row, a different horizon.
    row_v2 = {"cycle_id": cycle_id, "horizon": "weeks", "why": "shortened the thesis",
             "revises": event_id_v1}
    state_v2 = thesis_engine.reconstruct_states([row_v1, row_v2], [], [cycle_id])
    assert state_v2[0]["horizon"] == "weeks", "supersession must be real: the CURRENT fold moved on"
    assert state_v2[0]["event_id"] != event_id_v1, \
        "the old row's identity is no longer the line's current head"

    replayed = verdicts.replay(verdict["rule"]["id"], verdict["rule"]["version"],
                               verdict["subject"]["value"], verdict["observed"]["value"],
                               verdict["observed"]["closed"])
    assert replayed == verdict["outcome"] == "exit_too_fast", \
        "replay must use the verdict's own frozen subject.value ('years'), never the row's " \
        "current ('weeks') fold -- which would silently return None instead"


# ──────── H. frozen replay corpus (external review, #446 BLOCK 1) ────────

# Literal (subject_value, observed_value, observed_closed) -> expected_outcome
# rows for horizon_contradiction rule version 1, covering the decision
# boundary of every threshold (exactly at the threshold, one day either side)
# plus the two horizon/exited combinations that have no threshold at all.
# These expected outcomes are HAND-COMPUTED against version 1's published
# thresholds (EXIT_FAST = {"years": 90, "quarters": 21}, HELD_LONG =
# {"weeks": 60, "quarters": 180}) and pinned as literals -- deliberately
# NEVER derived from verdicts.RULE_PARAMS or horizon.py's live constants, and
# never regenerated by running replay() and recording what it returns. That
# independence is the whole point: RULE_PARAM_DIGEST can be kept "in
# agreement" by updating it in the same edit that moves a threshold, which
# makes a red digest test green again while silently rewriting what version 1
# means for every already-stored verdict. This corpus cannot be silenced that
# way -- moving a threshold changes what replay() returns for these exact
# inputs, and the only way to turn this test green again is to publish a new
# rule version and leave version 1's numbers untouched, which is the
# behaviour the replay contract exists to force. See verdicts.py's module
# docstring, "Rule-version discipline", for the full argument.
HORIZON_CONTRADICTION_V1_CORPUS = [
    # -- exit_fast["years"] = 90: exited, holding_days < 90 -> exit_too_fast
    ("years", 89, True, "exit_too_fast"),
    ("years", 90, True, None),
    ("years", 91, True, None),
    # -- exit_fast["quarters"] = 21
    ("quarters", 20, True, "exit_too_fast"),
    ("quarters", 21, True, None),
    ("quarters", 22, True, None),
    # -- "weeks" has no exit_fast entry: a quick exit is never a verdict
    ("weeks", 0, True, None),
    # -- held_long["weeks"] = 60: not exited, holding_days > 60 -> held_too_long
    ("weeks", 59, False, None),
    ("weeks", 60, False, None),
    ("weeks", 61, False, "held_too_long"),
    # -- held_long["quarters"] = 180
    ("quarters", 179, False, None),
    ("quarters", 180, False, None),
    ("quarters", 181, False, "held_too_long"),
    # -- "years" has no held_long entry: a long hold is never a verdict
    ("years", 100000, False, None),
]


def test_horizon_contradiction_v1_frozen_corpus_pins_every_threshold_boundary():
    """The mechanism the digest test alone cannot provide (external review,
    #446 BLOCK 1): a parameter change to a PUBLISHED version, even paired
    with an updated RULE_PARAM_DIGEST entry in the same commit -- the
    natural, obvious way to make a red digest test green again -- turns this
    corpus red where the digest test goes green. Matching mutation: change
    RULE_PARAMS["horizon_contradiction"][1]["exit_fast"]["years"] from 90 to
    80 and update that entry's RULE_PARAM_DIGEST to match --
    test_rule_param_digest_matches_its_pinned_hash_per_rule_version passes
    again, but replay("horizon_contradiction", 1, "years", 89, True) now
    returns None where this corpus pins "exit_too_fast" (89 is no longer
    below the moved threshold of 80). Only this test catches that."""
    for subject_value, observed_value, observed_closed, expected in HORIZON_CONTRADICTION_V1_CORPUS:
        got = verdicts.replay("horizon_contradiction", 1, subject_value, observed_value, observed_closed)
        assert got == expected, (subject_value, observed_value, observed_closed, got, expected)


# ──────── I. verdict-identity dedup (external review, #446 BLOCK 2) ────────

def _row(**overrides):
    """A minimal, schema-shaped verdict row for identity/dedup tests."""
    base = {
        "rule": {"id": "horizon_contradiction", "version": 1},
        "subject": {"store": "theses", "row_id": "thesis-update-abc",
                   "field": "horizon", "value": "quarters"},
        "observed": {"metric": "holding_days", "value": 222,
                    "window": {"from": "2026-01-01", "to": "2026-08-11"}, "closed": False},
        "outcome": "held_too_long",
        "verdict_source": "engine",
        "date_end": "2026-08-11",
        "session_id": "2026-08-11__alpha",
    }
    base.update(overrides)
    return base


def test_verdict_identity_ignores_session_id_observed_and_verdict_source():
    """Identity is (rule.id, rule.version, subject.store, subject.row_id,
    subject.field, date_end) only -- two rows differing solely in
    session_id, observed, or verdict_source are the SAME identity, because
    session_id is exactly the field two distinct --session-nonce sessions
    reviewing identical state legitimately differ on, and the rest do not
    name what the verdict is ABOUT."""
    a = _row(session_id="2026-08-11__alpha")
    b = _row(session_id="2026-08-11__beta", verdict_source="user",
            observed={"metric": "holding_days", "value": 999,
                     "window": {"from": "x", "to": "y"}, "closed": True})
    assert verdicts.verdict_identity(a) == verdicts.verdict_identity(b)


def test_verdict_identity_distinguishes_rule_subject_and_period():
    base = _row()
    variants = [
        _row(rule={"id": "horizon_contradiction", "version": 2}),
        _row(subject={"store": "theses", "row_id": "thesis-update-DIFFERENT",
                     "field": "horizon", "value": "quarters"}),
        _row(subject={"store": "theses", "row_id": "thesis-update-abc",
                     "field": "OTHER_FIELD", "value": "quarters"}),
        _row(date_end="2026-09-08"),
    ]
    identities = {verdicts.verdict_identity(base)} | {verdicts.verdict_identity(v) for v in variants}
    assert len(identities) == 1 + len(variants), "each variant must be its own distinct identity"


def test_dedupe_drops_a_same_identity_row_from_a_different_session():
    """The direct fix for BLOCK 2: session beta's row is dropped because
    session alpha already recorded the identical (subject, rule, period)
    triple -- the scenario a distinct --session-nonce produces over
    identical state and date."""
    existing = [_row(session_id="2026-08-11__alpha")]
    new = [_row(session_id="2026-08-11__beta")]
    kept = verdicts.dedupe_against_other_sessions(existing, new, "2026-08-11__beta")
    assert kept == []


def test_dedupe_leaves_same_session_rows_alone():
    """A retry of THIS session must not be filtered here -- that is
    _append_session_rows's own idempotent-retry contract to enforce. If this
    function silently dropped it instead, a genuinely changed retry under the
    same session_id would never reach the helper that is supposed to fail it
    closed."""
    existing = [_row(session_id="2026-08-11__alpha")]
    new = [_row(session_id="2026-08-11__alpha")]
    kept = verdicts.dedupe_against_other_sessions(existing, new, "2026-08-11__alpha")
    assert kept == new, "same-session rows must pass through untouched"


def test_dedupe_keeps_rows_with_a_genuinely_different_identity():
    existing = [_row(session_id="2026-08-11__alpha")]
    new = [_row(session_id="2026-09-08__gamma", date_end="2026-09-08",
               observed={"metric": "holding_days", "value": 250,
                        "window": {"from": "2026-01-01", "to": "2026-09-08"}, "closed": False})]
    kept = verdicts.dedupe_against_other_sessions(existing, new, "2026-09-08__gamma")
    assert kept == new, "a different period is a different triple, never deduped against an earlier one"


def test_dedupe_fails_closed_on_a_conflicting_outcome_for_the_same_identity():
    """Deliberate choice, not an incidental default: silently keeping the
    first row or appending both were both defensible, but a same-identity
    outcome disagreement means the engine computed two different
    said-vs-done answers from what should be pure date/state arithmetic --
    an upstream inconsistency a silent pick would hide. Fail closed instead,
    mirroring this codebase's existing convention for every other durable
    store's same-identity conflict."""
    existing = [_row(session_id="2026-08-11__alpha", outcome="held_too_long")]
    new = [_row(session_id="2026-08-11__beta", outcome="exit_too_fast")]
    try:
        verdicts.dedupe_against_other_sessions(existing, new, "2026-08-11__beta")
        assert False, "a conflicting outcome under the same identity must raise, not silently resolve"
    except verdicts.VerdictError as exc:
        assert "held_too_long" in str(exc) and "exit_too_fast" in str(exc)


def test_dedupe_collapses_a_same_identity_pair_within_one_new_batch_too():
    """Two rows in the SAME new_rows batch sharing an identity (should not
    happen from a single session's own _horizon_verdict_rows, which builds at
    most one row per triggering marker, but the function must not misbehave
    if it ever did) collapse the same way a cross-session duplicate does."""
    same = _row(session_id="2026-08-11__alpha")
    kept = verdicts.dedupe_against_other_sessions([], [same, dict(same)], "2026-08-11__alpha")
    assert len(kept) == 1


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
