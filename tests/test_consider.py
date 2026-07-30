#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""review.py consider (Layer 2 CLI entry point) -- offline, deterministic, no pytest.

`consider` wires engine/consequence.py's three public functions
(validate_premise / consequence / rule_collision -- already covered end to end
by tests/test_consequence.py) into the review.py CLI, with a new persisted
record (<root>/trade_evaluations.jsonl,
schemas/trade-evaluation.schema.json). This file settles the parts
consequence.py's own suite cannot: data-source resolution (CSV vs a
ledger-reconstructed book), the frozen evaluation row, --resolve's
append-only fold, the CLI's own fail-closed and validation surfaces, and
(section K) that _evaluation_id changes whenever anything that changes the
frozen consequence changes -- not only when the arguments the id used to name
by hand happened to differ (external review BLOCK finding).

All fixtures are built under a temp root; nothing here reads or writes a real
coach root. Every subprocess call passes --root explicitly.
"""
import csv
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "skills" / "fomo-kernel" / "engine"
REVIEW = ENGINE_DIR / "review.py"
SCHEMAS = ROOT / "skills" / "fomo-kernel" / "schemas"
MOCK = ROOT / "skills" / "fomo-kernel" / "mock"
COACH_PY = ENGINE_DIR / "coach.py"

sys.path.insert(0, str(ENGINE_DIR))

# The market must not be an input to these assertions (#620). Declared in
# tests/offline_posture.py so a direct `python3 tests/<this file>` run and a
# `run_all.py` run reach the same answer; TR_TEST_NETWORK=1 still opts in.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_posture  # noqa: E402
offline_posture.apply()
import price_feed as price_feed_engine  # noqa: E402
import ledger as ledger_engine  # noqa: E402
import portfolio_basis as portfolio_basis_engine  # noqa: E402
import review as review_engine  # noqa: E402
import session as session_engine  # noqa: E402
import trade_recap as tr_engine  # noqa: E402


# ─────────────────────────────── helpers ───────────────────────────────

def _run(*args):
    return subprocess.run([sys.executable, str(REVIEW), *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)


def _ok(run):
    assert run.returncode == 0, f"expected success, got {run.returncode}: {run.stdout}{run.stderr}"
    return json.loads(run.stdout)


def _fails(run, fragment):
    assert run.returncode != 0, f"expected failure, got exit 0: {run.stdout}"
    payload = json.loads(run.stdout)
    assert payload.get("status") == "error", f"expected a status:error payload, got {payload}"
    assert fragment in payload["error"], f"wanted {fragment!r} in error, got {payload['error']!r}"
    return payload


def _evaluation_path(root):
    return os.path.join(root, "trade_evaluations.jsonl")


def _read_evaluations(root):
    path = _evaluation_path(root)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


_CSV_HEADER = ["Symbol", "Quantity", "Price", "Action", "TradeDate", "RecordType"]


def _write_csv(path, trades):
    """trades: [(symbol, action, qty, price, iso_date), ...]."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_HEADER)
        for symbol, action, qty, price, date in trades:
            writer.writerow([symbol, qty, price, action, date, "Trade"])


def _write_ledger(path, events):
    with open(path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _snapshot_event(as_of, positions):
    return {"type": "snapshot", "as_of": as_of,
            "source": ledger_engine.DECLARED_BOOK_SOURCE, "positions": positions}


def _trade_event(date, ticker, action, qty, price, market="US", currency="USD"):
    return {"type": "trade", "date": date, "ticker": ticker, "action": action,
            "qty": qty, "price": price, "market": market, "currency": currency}


def _schema(name):
    with open(SCHEMAS / name, encoding="utf-8") as f:
        return json.load(f)


EVALUATION_SCHEMA = _schema("trade-evaluation.schema.json")
PREMISE_SCHEMA = _schema("trade-premise.schema.json")
PLAN_SCHEMA = _schema("review-plan.schema.json")
CONTEXT_SCHEMA = _schema("decision-context.schema.json")
# trade-evaluation.schema.json's own `agent_case` property is a bare $ref to
# this file (#479 Wave B) rather than a restated claim shape -- see that
# property's own description. Loaded separately here for the same reason:
# EVALUATION_SCHEMA["properties"]["agent_case"] carries no "required" or
# "properties" of its own any more to read a claim's shape off of.
ANSWER_PROVENANCE_SCHEMA = _schema("answer-provenance.schema.json")
# review.AGENT_CASE_PROVENANCE / answer_provenance.PROVENANCE's own values,
# restated here only as dict keys into ANSWER_PROVENANCE_SCHEMA's three
# per-provenance $defs -- the same mapping idea
# test_answer_provenance.py's _CLAIM_DEF_BY_PROVENANCE already uses for the
# identical lookup in that file's own suite.
_CLAIM_DEF_BY_PROVENANCE = {
    "engine_fact": "engineFactClaim",
    "public_fact": "publicFactClaim",
    "agent_judgment": "judgmentClaim",
}


def _check_evaluation_shape(row):
    """Spot-check the row against trade-evaluation.schema.json, the
    same "no jsonschema dependency, pin the vocabulary" idiom
    test_consequence.py already uses for trade-premise.schema.json."""
    required = set(EVALUATION_SCHEMA["required"])
    assert required <= set(row), f"missing required fields: {required - set(row)}"
    allowed = set(EVALUATION_SCHEMA["properties"])
    assert set(row) <= allowed, f"row carries undeclared fields: {set(row) - allowed}"

    assert row["decision"] in EVALUATION_SCHEMA["properties"]["decision"]["enum"]
    assert row["decided_on"] is None or isinstance(row["decided_on"], str)

    basis_schema = EVALUATION_SCHEMA["properties"]["basis"]
    assert set(basis_schema["required"]) <= set(row["basis"])
    assert row["basis"]["source"] in basis_schema["properties"]["source"]["enum"]
    assert isinstance(row["basis"]["stale_days"], int) and row["basis"]["stale_days"] >= 0
    assert set(row["basis"]) <= set(basis_schema["properties"]), (
        f"basis carries undeclared fields: {set(row['basis']) - set(basis_schema['properties'])}")
    if "valuation_coverage" in row["basis"]:
        coverage_schema = basis_schema["properties"]["valuation_coverage"]
        coverage = row["basis"]["valuation_coverage"]
        assert set(coverage) == set(coverage_schema["properties"])
        assert coverage["scope"] in coverage_schema["properties"]["scope"]["enum"]
        assert coverage["currencies"] == sorted(coverage["currencies"])
    # #618. Absent on an unpriced row and, when present, a real per-instrument
    # record whose summary is the newest observation in it -- never a frame
    # date declared over instruments it does not describe.
    observed = row["basis"].get("price_observations")
    assert not (observed is not None and row["basis"]["valuation_basis"] == "unpriced"), (
        f"an unpriced book grew a market session it never observed: {observed}")
    if observed is not None:
        assert set(observed) == set(basis_schema["properties"]["price_observations"]["properties"])
        assert observed["by_ticker"], "an empty observation map is an absent one"
        assert observed["as_of"] == max(observed["by_ticker"].values())
        for day in observed["by_ticker"].values():
            assert isinstance(day, str) and len(day) == 10 and day[4] == day[7] == "-"

    consequence_schema = EVALUATION_SCHEMA["properties"]["consequence"]
    assert set(consequence_schema["required"]) <= set(row["consequence"])
    # additionalProperties: false, checked rather than assumed. Without this,
    # an engine that starts freezing a new field onto the row -- as #598/#599
    # do -- ships a row the schema rejects while the whole suite stays green,
    # because nothing offline here runs a real validator.
    assert set(row["consequence"]) <= set(consequence_schema["properties"]), (
        f"consequence carries undeclared fields: "
        f"{set(row['consequence']) - set(consequence_schema['properties'])}")
    allowed_disclosures = set(consequence_schema["properties"]["disclosures"]["items"]["enum"])
    assert set(row["consequence"]["disclosures"]) <= allowed_disclosures

    collision_states = set(
        EVALUATION_SCHEMA["properties"]["rule_collisions"]["items"]["properties"]["state"]["enum"])
    for collision in row["rule_collisions"]:
        assert collision["state"] in collision_states
        assert collision["worsens"] in (True, False, None)
        if collision["state"] != "already_over":
            assert collision["worsens"] is None, "worsens only applies to already_over"

    # premise: normalized form must be a valid instance of trade-premise.schema.json --
    # a subset of its declared properties, carrying every field that schema requires,
    # and never notional (validate_premise consumes it into qty at validation time).
    premise = row["premise"]
    assert set(premise) <= set(PREMISE_SCHEMA["properties"])
    assert set(PREMISE_SCHEMA["required"]) <= set(premise)
    assert "notional" not in premise
    assert premise["side"] in ("buy", "sell")

    # context (#479 Wave A) is optional and, when absent, absent -- never a
    # stored null. That distinction is load-bearing: review._evaluation_id
    # keys the identity seed on exactly this presence test, so a row carrying
    # `context: null` would be an evaluation whose id says it had no context
    # and whose content says it had an empty one.
    if "context" in row:
        assert isinstance(row["context"], dict), "a stored context is an object, never null"
        assert set(CONTEXT_SCHEMA["required"]) <= set(row["context"])
        assert set(row["context"]) <= set(CONTEXT_SCHEMA["properties"])
        for field in ("reason", "why_now"):
            assert isinstance(row["context"][field], str) and row["context"][field].strip()
        refs = row["context"].get("evidence_refs", [])
        assert len(refs) <= CONTEXT_SCHEMA["properties"]["evidence_refs"]["maxItems"]

    if "agent_case" in row:
        assert set(row["agent_case"]) == {"for", "against"}
        for side in ("for", "against"):
            claims = row["agent_case"][side]
            assert claims, f"agent_case.{side} must be non-empty once agent_case is sent"
            for claim in claims:
                assert claim["provenance"] in _CLAIM_DEF_BY_PROVENANCE
                defn = ANSWER_PROVENANCE_SCHEMA["$defs"][_CLAIM_DEF_BY_PROVENANCE[claim["provenance"]]]
                assert set(defn["required"]) <= set(claim), (
                    f"{side} claim missing required fields for {claim['provenance']}: "
                    f"{set(defn['required']) - set(claim)}")
                assert set(claim) <= set(defn["properties"]), (
                    f"{side} claim carries fields {claim['provenance']} must not: "
                    f"{set(claim) - set(defn['properties'])}")


# ──────────────── evaluation_reconciliation fixtures (section J) ────────────────
#
# review._evaluation_reconciliation reads trade_evaluations.jsonl and a
# list of trade_recap-shaped rows directly, so these tests build both by hand
# rather than routing every case through a real `consider` call (which stamps
# `created` as wall-clock today -- unusable for pinning date-boundary cases).

def _open_evaluation(evaluation_id, created, ticker, side, qty=10, price=100.0, date=None):
    """A minimal, schema-shaped open evaluation row. Only evaluation_id,
    created, premise, and decision are read by _evaluation_reconciliation;
    the rest are placeholders satisfying trade-evaluation.schema.json's
    required fields -- the same posture _snapshot_event/_trade_event already
    take toward ledger.jsonl fixtures in this file."""
    return {
        "evaluation_id": evaluation_id,
        "created": created,
        "premise": {"ticker": ticker, "side": side, "qty": qty, "price": price,
                    "date": date or created, "currency": "USD"},
        "basis": {"source": "snapshot_anchor", "as_of": created, "stale_days": 0,
                  "completeness": "declared_complete", "cost_basis": "average_cost",
                  "valuation_basis": "unpriced", "reconciliation_ref": None,
                  "state_version": "pb-v1:" + "0" * 64},
        "consequence": {"before": {}, "after": {}, "delta": {}, "disclosures": []},
        "rule_collisions": [],
        "decision": "open",
        "decided_on": None,
    }


def _write_evaluations(root, rows):
    path = _evaluation_path(root)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _tr_row(ticker, side, qty, price, date):
    """trade_recap-shaped row -- the same shape _ledger_trade_events,
    _rows_from_ledger, and trade_recap.load all return, with `date` as a real
    datetime.date rather than a string."""
    return {"ticker": ticker, "side": side, "qty": qty, "price": price,
            "date": dt.date.fromisoformat(date), "market": "US", "currency": "USD"}


def _check_reconciliation_shape(payload):
    """Spot-check evaluation_reconciliation against review-plan.schema.json
    -- the same manual-pin idiom _check_evaluation_shape uses for the
    sibling schema (offline suite carries no jsonschema dependency)."""
    schema = PLAN_SCHEMA["properties"]["evaluation_reconciliation"]
    required = set(schema["required"])
    assert required <= set(payload), f"missing required fields: {required - set(payload)}"
    allowed = set(schema["properties"])
    assert set(payload) <= allowed, f"undeclared fields: {set(payload) - allowed}"

    item_schema = schema["properties"]["items"]["items"]
    item_required = set(item_schema["required"])
    item_allowed = set(item_schema["properties"])
    status_enum = set(item_schema["properties"]["status"]["enum"])
    for item in payload["items"]:
        assert item_required <= set(item), f"item missing fields: {item_required - set(item)}"
        assert set(item) <= item_allowed, f"item carries undeclared fields: {set(item) - item_allowed}"
        assert item["status"] in status_enum
        if item["status"] == "matched":
            assert item["matched_trade"] is not None
            matched_schema = item_schema["properties"]["matched_trade"]
            assert set(matched_schema["required"]) <= set(item["matched_trade"])
        else:
            assert item["matched_trade"] is None

    summary_schema = schema["properties"]["summary"]
    assert set(summary_schema["required"]) <= set(payload["summary"])
    summary = payload["summary"]
    assert summary["shown"] == len(payload["items"])
    assert summary["open_total"] == summary["shown"] + summary["beyond_cap"]


def _minimal_prepare_artifacts(tmp, date_end):
    """The smallest --card-json/--state-json pair `prepare` accepts (the same
    idiom test_review_v2.py's own _artifacts() uses), parameterized on
    date_end so date-boundary fixtures can pin it precisely instead of riding
    wall-clock "today". evaluation_reconciliation reads only the ledger and
    evaluations file plus state.date_end, so holdings/metrics content here
    is irrelevant to it -- this only has to be valid enough for `prepare` to
    complete."""
    state = {
        "schema_version": 2,
        "date_start": "2026-01-01", "date_end": date_end,
        "n_trades": 0, "n_round_trips": 0, "n_held": 0,
        "headline_dim": None, "headline_metric": None, "commitment": None,
        "metrics": {"max_pos_pct": 0.1, "max_pos_ticker": None, "avgdown_count": 0,
                    "avgdown_breach": 0, "payoff": None, "ai_pct": 0.0,
                    "max_sector_pct": 0.0, "top3_pct": 0.0, "n_holdings": 0,
                    "exit_severity": 0.0, "hold_severity": 0.0,
                    "beta": None, "alpha_ann": None, "alpha_t": None, "alpha_credible": None,
                    "longest_hold_days": None, "longest_hold_ticker": None,
                    "worst_cur_ret": None, "worst_cur_ret_ticker": None},
        "rule": None, "insufficient_data": True,
        "holdings": {"as_of": date_end, "derived_from": "trades_csv",
                     "positions": {}},
        "currency_meta": {"aggregate_currency": "USD", "mixed": False},
        "portfolio_structure": None,
        "cash": None,
        "problem_events": [],
        "problem_opportunities": {},
    }
    card = {
        "schema_version": 1, "philosophy": "test", "strength": None,
        "overview": {"total_pnl": 0, "realized": 0, "unrealized": 0, "payoff": None,
                     "avg_win": None, "avg_loss": None},
        "what_if": None, "ticker_diagnosis": [], "thesis_questions": [], "top_holes": [],
        "candidate_rules": [], "prescriptions": [], "alpha_beta_breakdown": {},
        "payoff_attribution": {}, "dims_raw": [], "data_integrity": {},
        "currency_meta": {"aggregate_currency": "USD"}, "cash": None,
        "acct_perf": {"note": "offline"}, "portfolio_structure": None,
        "honesty_ledger": [],
        "pnl_curve": {"note": "offline"},
    }
    card_path = pathlib.Path(tmp) / "card.json"
    state_path = pathlib.Path(tmp) / "state.json"
    card_path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return card_path, state_path


def _prepare_plan(tmp, root, date_end):
    """Run a real `prepare` against a minimal card/state pair and return the
    full persisted plan (engine_card/engine_state included) the way
    test_review_v2.py's _pending_plan reads it -- prepare's stdout only
    carries the agent-facing projection."""
    card_path, state_path = _minimal_prepare_artifacts(tmp, date_end)
    run = _run("prepare", "--root", root, "--card-json", card_path, "--state-json", state_path)
    payload = _ok(run)
    session_id = payload["review_plan"]["session_id"]
    plan = session_engine.load_pending(str(root), session_id)["plan"]
    assert "evaluation_reconciliation" in payload["review_plan"], \
        "the agent-facing projection must not drop evaluation_reconciliation"
    return plan


# ─────────────────── A. data-source resolution ───────────────────

def test_csv_path_produces_an_evaluation_with_transactions_basis():
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        payload = _ok(run)
        row = payload["evaluation"]
        assert row["basis"]["source"] == "transactions"
        _check_evaluation_shape(row)
        assert _read_evaluations(tmp) == [row]


def test_ledger_path_consumes_and_discloses_the_canonical_portfolio_basis():
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), [
            _snapshot_event("2026-01-01", [
                {"ticker": "NVDA", "shares": 100, "avg_cost": 100.0, "market": "US", "currency": "USD"}]),
            _trade_event("2026-02-01", "NVDA", "buy", 20, 120.0),
        ])
        run = _run("consider", "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 150.0, "qty": 10}')
        payload = _ok(run)
        row = payload["evaluation"]
        assert row["basis"]["source"] == "snapshot_anchor"
        assert row["basis"]["as_of"] == "2026-02-01"   # the ledger's own latest row, not today
        assert row["basis"]["completeness"] == "declared_complete"
        assert row["basis"]["cost_basis"] == "average_cost"
        assert row["basis"]["valuation_basis"] == "unpriced"
        assert row["basis"]["reconciliation_ref"]["anchor_as_of"] == "2026-01-01"
        assert row["basis"]["state_version"].startswith("pb-v1:")
        assert row["basis"]["valuation_coverage"]["scope"] == "full_current_book"
        assert row["basis"]["valuation_coverage"]["cost_fallback"] == ["NVDA"]
        assert row["basis"]["valuation_coverage"]["currencies"] == ["USD"]
        _check_evaluation_shape(row)


def test_csv_paths_take_precedence_over_a_present_ledger_file():
    """A CSV path is an explicit, deliberate choice of a different book; a
    ledger.jsonl sitting in the same root must not silently blend in."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), [
            _snapshot_event("2020-01-01", [
                {"ticker": "AAPL", "shares": 10, "avg_cost": 50.0, "market": "US", "currency": "USD"}]),
        ])
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        payload = _ok(run)
        assert payload["evaluation"]["basis"]["source"] == "transactions"
        assert "AAPL" not in payload["evaluation"]["consequence"]["before"]["held"]


def test_ledger_reconstruction_matches_derive_holdings_for_the_same_events():
    """The book consider reasons over and ledger.derive_holdings' own read of
    the same events must not disagree about what the user holds. Compared on
    FIFO terms (trade_recap.round_trips / fifo_held -- the same pipeline
    consequence.portfolio_state itself calls), not a naive sum: a sell's
    trade price is what it executed at, not the cost basis it removes, so
    comparing "signed qty * price" against derive_holdings' own
    average-cost-reduction bookkeeping would compare two different
    conventions and fail for the wrong reason. With exactly one lot per
    ticker here, FIFO-held and average-cost-held provably agree -- both
    reduce the same single lot proportionally -- so this is a same-basis
    comparison, not a coincidence of the fixture."""
    events = [
        _snapshot_event("2026-01-01", [
            {"ticker": "NVDA", "shares": 100, "avg_cost": 100.0, "market": "US", "currency": "USD"},
            {"ticker": "AMD", "shares": 50, "avg_cost": 80.0, "market": "US", "currency": "USD"},
        ]),
        _trade_event("2026-01-01", "NVDA", "buy", 999, 1.0),   # same-day as anchor: must be ignored
        _trade_event("2026-02-01", "NVDA", "buy", 20, 120.0),
        _trade_event("2026-03-01", "AMD", "sell", 10, 90.0),
    ]
    rows = review_engine._rows_from_ledger(events)
    derived = ledger_engine.derive_holdings(events)["holdings"]

    _rts, open_lots = tr_engine.round_trips(rows)
    fifo_held = tr_engine.fifo_held(open_lots)

    assert set(fifo_held) == set(derived)
    for ticker, (shares, cost) in fifo_held.items():
        assert abs(shares - derived[ticker]["shares"]) < 1e-6, ticker
        assert abs(cost - derived[ticker]["cost_total"]) < 1e-2, ticker


def test_consider_uses_portfolio_basis_cost_for_multi_lot_partial_sell():
    """The ledger's canonical average-cost book and consider's before state
    must agree even where the old FIFO reconstruction did not: add a second
    lot, then sell only part of the combined position."""
    events = [
        _snapshot_event("2026-01-01", [
            {"ticker": "NVDA", "shares": 100, "avg_cost": 100.0, "market": "US", "currency": "USD"},
            {"ticker": "AMD", "shares": 100, "avg_cost": 50.0, "market": "US", "currency": "USD"},
        ]),
        _trade_event("2026-01-10", "NVDA", "buy", 100, 200.0),
        _trade_event("2026-01-11", "NVDA", "sell", 50, 150.0),
    ]
    basis = portfolio_basis_engine.query_current_book(events, reference_as_of="2026-01-11")
    assert basis and basis.completeness == "declared_complete"
    expected = basis.current_book["holdings"]["NVDA"]
    projection = portfolio_basis_engine.sizing_projection(basis)
    assert projection and projection.applicable
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), events)
        row = _ok(_run("consider", "--root", tmp,
                       "--premise", '{"ticker": "NVDA", "side": "buy", "price": 160.0, "qty": 1}'))["evaluation"]
        held = row["consequence"]["before"]["held"]["NVDA"]
        assert held["shares"] == expected["shares"] == 150.0
        assert abs(held["cost"] - expected["cost_total"]) < 1e-6
        assert row["basis"]["state_version"] == basis.state_version
        assert row["consequence"]["before"]["weights"]["NVDA"] == projection.values["NVDA"]["weight"]
        assert row["basis"]["valuation_coverage"] == projection.coverage


def test_consider_succeeds_however_the_current_book_was_recorded():
    """#485/#549: how a book came to be recorded -- a holdings view the user
    declared, a legacy row they marked as covering part of an account, a book
    replayed purely from trade history, or that replay written down by the
    trade-import lane -- must never gate whether `consider` can compute a
    verdict; only a genuinely missing fact does (see the fail-closed proof
    further below). Mirrors test_consider_uses_portfolio_basis_cost_for_multi_
    lot_partial_sell's rigor: compare the CLI's own numbers against a
    basis/projection built directly from the same events, not merely "it did
    not error".

    The middle case is #549's own regression: a `trades_derived` restatement
    is a recorded book for the lane that asks whether one exists, and must
    still leave `consider` reading the replay it summarizes -- not re-based on
    it, and not re-gated by it.
    """
    cases = [
        ("declared_complete",
         [{**_snapshot_event("2025-06-01", [{"ticker": "NVDA", "shares": 10, "avg_cost": 100.0,
                                             "market": "US", "currency": "USD"}]),
           "is_complete": False}]),
        ("unverified",
         [_trade_event("2026-01-01", "NVDA", "buy", 10, 100.0),
          {"type": "snapshot", "as_of": "2026-01-01",
           "source": ledger_engine.DERIVED_BOOK_SOURCE,
           "positions": [{"ticker": "NVDA", "shares": 10.0, "avg_cost": 100.0,
                          "market": "US", "currency": "USD"}]}]),
        ("unverified",
         [_trade_event("2026-01-01", "NVDA", "buy", 10, 100.0)]),
    ]
    for expected_completeness, events in cases:
        basis = portfolio_basis_engine.query_current_book(events, reference_as_of="2026-01-01")
        assert basis is not None and basis.completeness == expected_completeness
        expected_holding = basis.current_book["holdings"]["NVDA"]
        projection = portfolio_basis_engine.sizing_projection(basis)
        assert projection is not None and projection.applicable
        with tempfile.TemporaryDirectory() as tmp:
            _write_ledger(os.path.join(tmp, "ledger.jsonl"), events)
            row = _ok(_run("consider", "--root", tmp,
                           "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 1}'))["evaluation"]
            assert row["basis"]["completeness"] == expected_completeness
            held = row["consequence"]["before"]["held"]["NVDA"]
            assert held["shares"] == expected_holding["shares"] == 10.0
            assert abs(held["cost"] - expected_holding["cost_total"]) < 1e-6
            weight = row["consequence"]["before"]["weights"]["NVDA"]
            assert weight == projection.values["NVDA"]["weight"] == 1.0
            assert row["basis"]["state_version"] == basis.state_version
            _check_evaluation_shape(row)
            assert os.path.exists(_evaluation_path(tmp))


def test_consider_succeeds_end_to_end_after_prepare_ingests_a_trades_csv():
    """The regression net for the actual shipped bug (#485, #496): every
    other fixture in this file seeds the ledger through a declared
    `_snapshot_event`, which is exactly why a completeness gate on
    `rows_from_portfolio_basis` shipped green and stayed green -- it never ran
    against the product's own primary input route. Here the ledger is built the
    way a real user's is: `prepare` ingesting a trades CSV. Without this test,
    that gate could come back and every other fixture in this file would still
    pass.

    Since #549 that route also writes down the book it derived, so the ledger
    holds one `trades_derived` snapshot beside the trades. This test pins both
    halves of that change: the row is written (a later holdings view now has a
    predecessor to update), and it changes nothing about what `consider`
    reads -- still `transaction_replay`, still `unverified`, still the shares
    the trades produce."""
    with tempfile.TemporaryDirectory() as tmp:
        prepare_run = _run("prepare", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                           "--route", "weekly_review")
        assert prepare_run.returncode == 0, (
            f"prepare on a trades CSV must succeed: {prepare_run.returncode}: "
            f"{prepare_run.stdout}{prepare_run.stderr}")

        ledger_path = os.path.join(tmp, "ledger.jsonl")
        with open(ledger_path, encoding="utf-8") as f:
            written_events = [json.loads(line) for line in f if line.strip()]
        assert written_events, "prepare must append the CSV's trades to the ledger"
        recorded = [event for event in written_events if event["type"] == "snapshot"]
        assert {event["type"] for event in written_events} == {"trade", "snapshot"}
        assert len(recorded) == 1 and recorded[0]["source"] == ledger_engine.DERIVED_BOOK_SOURCE, (
            "#549: the primary CSV route records the book it derived, exactly once, "
            "and marks how it was learned")
        assert "is_complete" not in recorded[0], (
            "#549 removed the flag; a recorded book never claims to cover an account")
        assert ledger_engine.latest_anchor(written_events) is recorded[0]
        assert ledger_engine.latest_anchor(written_events, declared_only=True) is None, (
            "a restatement of the replay must never become the replay's own base")

        basis = portfolio_basis_engine.query_current_book(
            written_events, reference_as_of=dt.date.today().isoformat())
        assert basis is not None and basis.completeness == "unverified"
        expected_holding = basis.current_book["holdings"]["NVDA"]
        projection = portfolio_basis_engine.sizing_projection(basis)
        assert projection is not None and projection.applicable

        row = _ok(_run("consider", "--root", tmp,
                       "--premise", '{"ticker": "NVDA", "side": "buy", "qty": 5, '
                                    '"price": 130.0, "currency": "USD"}'))["evaluation"]
        assert row["basis"]["source"] == "transaction_replay"
        assert row["basis"]["completeness"] == "unverified"
        held = row["consequence"]["before"]["held"]["NVDA"]
        assert held["shares"] == expected_holding["shares"] == 120.0
        assert abs(held["cost"] - expected_holding["cost_total"]) < 1e-6
        weight = row["consequence"]["before"]["weights"]["NVDA"]
        assert weight == projection.values["NVDA"]["weight"]
        assert row["basis"]["state_version"] == basis.state_version
        _check_evaluation_shape(row)
        assert os.path.exists(_evaluation_path(tmp))


def test_an_unusable_holding_is_excluded_and_named_instead_of_refusing_everything():
    """#515's own acceptance sentence, run end to end.

    Six positions, one with no declared cost, and the user asks about a
    seventh. Before this leaf the whole evaluation was refused -- which
    gives the user nothing and is indistinguishable from a broken product.
    The ruling: compute over the part that can be used and name what was
    left out.

    Note what the three tests this replaces had in common: every one of them
    used a book with exactly ONE holding, so "refuse the book" and "exclude
    this holding" produced identical output and nothing here could tell them
    apart. That is why the defect shipped, and it is why this fixture holds
    six."""
    priced = [{"ticker": t, "shares": 10, "avg_cost": 100.0, "market": "US", "currency": "USD"}
              for t in ("AAA", "BBB", "CCC", "DDD", "EEE")]
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), [
            _snapshot_event("2026-01-01", priced + [
                {"ticker": "DARK", "shares": 10, "market": "US", "currency": "USD"}]),  # no avg_cost
        ])
        row = _ok(_run("consider", "--root", tmp,
                       "--premise", '{"ticker": "NEWCO", "side": "buy", "qty": 5, '
                                    '"price": 100.0, "currency": "USD"}'))["evaluation"]
        result = row["consequence"]
        assert "partial_book" in result["disclosures"]
        assert result["excluded_holdings"] == [{"ticker": "DARK", "reason": "unavailable_cost"}], (
            "the excluded holding must be named, not merely counted -- a partial "
            "denominator that does not say WHICH holding is missing is barely "
            "better than one that does not say it is partial")
        assert "DARK" not in result["before"]["weights"], (
            "an unvaluable holding must be absent from the denominator, never "
            "silently counted at zero")
        assert set(result["before"]["weights"]) == {"AAA", "BBB", "CCC", "DDD", "EEE"}
        assert abs(sum(result["before"]["weights"].values()) - 1.0) < 1e-9, (
            "the weights the user is shown must sum over the priced part")
        assert result["after"]["weights"]["NEWCO"] > 0, "the question was actually answered"
        _check_evaluation_shape(row)


def test_a_book_where_nothing_can_be_valued_still_refuses():
    """Exclude-and-disclose has a floor. Excluding the only holding leaves an
    empty denominator, which is not a bounded answer -- it is no answer, and
    inventing one would be worse than refusing. This is the mutation boundary
    for #515: turning a whole-book refusal into a per-holding exclusion is
    right; letting the last holding go and answering anyway is not."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), [
            _snapshot_event("2026-01-01", [
                {"ticker": "NVDA", "shares": 10, "market": "US", "currency": "USD"}]),  # no avg_cost
        ])
        run = _run("consider", "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        _fails(run, "no holding that can be valued: NVDA")
        assert not os.path.exists(_evaluation_path(tmp))


def test_consider_refuses_mixed_usd_twd_current_book_before_any_verdict():
    """#497: native USD/TWD values have no canonical aggregate without an
    FX frame.  The #496 projection must therefore stop consider before it can
    persist or expose sizing, concentration, or rule-collision output."""
    events = [_snapshot_event("2026-01-01", [
        {"ticker": "USD", "shares": 2, "avg_cost": 10.0, "market": "US", "currency": "USD"},
        {"ticker": "TWD", "shares": 3, "avg_cost": 100.0, "market": "TW", "currency": "TWD"},
    ])]
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), events)
        run = _run("consider", "--root", tmp,
                   "--premise", '{"ticker": "USD", "side": "buy", "price": 15.0, "qty": 1}')
        _fails(run, "no usable current-book sizing projection")
        assert not os.path.exists(_evaluation_path(tmp))


def test_same_day_ledger_mutation_changes_the_frozen_basis_version():
    anchor = _snapshot_event("2026-01-01", [
        {"ticker": "NVDA", "shares": 10, "avg_cost": 100.0, "market": "US", "currency": "USD"}])
    with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
        _write_ledger(os.path.join(one, "ledger.jsonl"), [anchor])
        _write_ledger(os.path.join(two, "ledger.jsonl"), [anchor, _trade_event("2026-01-01", "NVDA", "buy", 1, 110)])
        premise = '{"ticker": "NVDA", "side": "buy", "price": 120.0, "qty": 1}'
        first = _ok(_run("consider", "--root", one, "--premise", premise))["evaluation"]
        second = _ok(_run("consider", "--root", two, "--premise", premise))["evaluation"]
        assert first["consequence"]["before"]["held"] == second["consequence"]["before"]["held"]
        assert first["basis"]["state_version"] != second["basis"]["state_version"]


def test_duplicate_ticker_in_one_anchor_matches_derive_holdings_last_wins():
    """A ticker declared twice in one snapshot anchor is malformed input, not
    two positions. ledger.derive_holdings reads it as one (its own pos[t] =
    {...} assignment lets the later declaration overwrite the earlier one);
    reconstruction here must agree rather than double-counting the ticker."""
    events = [
        _snapshot_event("2026-01-01", [
            {"ticker": "NVDA", "shares": 100, "avg_cost": 100.0, "market": "US", "currency": "USD"},
            {"ticker": "NVDA", "shares": 40, "avg_cost": 90.0, "market": "US", "currency": "USD"},
        ]),
    ]
    rows = review_engine._rows_from_ledger(events)
    derived = ledger_engine.derive_holdings(events)["holdings"]

    assert len(rows) == 1, "a duplicate declaration must not become two rows"
    assert rows[0]["qty"] == derived["NVDA"]["shares"] == 40.0
    assert rows[0]["price"] == 90.0


# ─────────────────────────── B. staleness ───────────────────────────

def test_stale_days_is_computed_and_nonzero_for_an_old_record():
    with tempfile.TemporaryDirectory() as tmp:
        old_date = "2024-01-15"
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), [
            _snapshot_event(old_date, [
                {"ticker": "NVDA", "shares": 10, "avg_cost": 100.0, "market": "US", "currency": "USD"}]),
        ])
        run = _run("consider", "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        payload = _ok(run)
        basis = payload["evaluation"]["basis"]
        expected = (dt.date.today() - dt.date.fromisoformat(old_date)).days
        assert basis["stale_days"] == expected
        assert basis["stale_days"] > 0
        assert basis["as_of"] == old_date


# ─────────────────────────── C. fail-closed ───────────────────────────

def test_absent_ledger_fails_closed_rather_than_answering():
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        _fails(run, "no usable trade or snapshot history")
        assert not os.path.exists(_evaluation_path(tmp)), "a failed call must not write a row"


def test_empty_ledger_file_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "ledger.jsonl"), "w", encoding="utf-8").close()
        run = _run("consider", "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        _fails(run, "no usable trade or snapshot history")


def test_corrupt_ledger_fails_closed_rather_than_reconstructing_a_partial_book():
    """#462: _consider_rows' ledger-reconstruction fallback must refuse a
    ledger with an unreadable row rather than silently answering the
    rule-collision question against a shortened book. Unlike the absent/empty
    cases above, this ledger has a real, usable snapshot anchor -- the old
    (pre-#462) load_ledger would have happily dropped the bad line and let
    consider answer with a wrong position, not a missing one."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), [
            _snapshot_event("2026-01-01", [
                {"ticker": "NVDA", "shares": 100, "avg_cost": 100.0,
                 "market": "US", "currency": "USD"}]),
        ])
        with open(os.path.join(tmp, "ledger.jsonl"), "a", encoding="utf-8") as f:
            f.write("not json at all\n")
        run = _run("consider", "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        _fails(run, "unreadable row(s)")
        assert not os.path.exists(_evaluation_path(tmp)), "a failed call must not write a row"


def test_a_readable_ledger_with_no_trustworthy_book_fails_closed():
    """#613: the fourth ledger-route refusal, and the only one nothing asserted.

    Unlike the corrupt case above, `load_ledger`'s strict scan passes here with
    nothing skipped -- every row is valid JSON carrying a declared event type.
    What fails is one level in: `portfolio_basis.query_current_book` cannot
    state a book it trusts and returns None. This refusal is the only thing
    between that and a pre-trade answer computed against a book the engine
    could not read (AGENTS.md boundary 6). Deleted, `basis.to_dict()` is
    reached on None, and AttributeError is not in `review.main`'s caught tuple
    -- the fail-closed boundary degrades into a traceback with no error
    payload at all.

    `query_current_book` has two independent ways to answer None and one
    fixture per branch is driven, because a single shape would leave the other
    deletable. Which gate each trips is asserted rather than described: a
    quantity that is not a number trips *both* (`_norm_trade` rejects it and
    `derive_holdings` also flags it), so it looks like coverage of the second
    branch and is not -- the preflight refuses first and the integrity check
    never runs. Pinning the pair is what keeps that mistake from being
    re-made here.

    The in-process preconditions are the load-bearing half: without them a
    change that reroutes either shape to an earlier gate leaves this test
    green with the refusal it names never exercised.
    """
    held = {"ticker": "NVDA", "shares": 10, "avg_cost": 100.0,
            "market": "US", "currency": "USD"}
    # label -> (events, preflight_passes, expects_bad_integrity)
    cases = {
        # Structurally fine, semantically not a book: shares cannot be negative.
        # derive_holdings reports *nothing* -- no integrity item, no skipped
        # line, no other signal anywhere in the system. This refusal is the
        # only one there is.
        "semantically_unreadable_snapshot": (
            [_snapshot_event("2026-01-01", [dict(held, shares=-5)])], False, False),
        # The other branch, and the only kind that reaches it: `since`/
        # `since_basis` are engine-assigned provenance (#485 Slice C, #531),
        # inside SNAPSHOT_POSITION_KEYS so the preflight passes them through
        # unexamined, and validated one level in by derive_holdings.
        "bad_position_provenance": (
            [_snapshot_event("2026-01-01", [
                dict(held, since="2025-01-01", since_basis="not_a_declared_basis")])], True, True),
    }
    for label, (events, preflight_passes, expects_bad_integrity) in cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = os.path.join(tmp, "ledger.jsonl")
            _write_ledger(ledger_path, events)

            loaded, skipped = ledger_engine.load_ledger(ledger_path)
            assert skipped == 0, (
                f"{label}: the strict scan must pass, or an earlier gate is doing the refusing")
            assert portfolio_basis_engine._semantically_known_events(loaded) is preflight_passes, (
                f"{label}: expected preflight_passes={preflight_passes}; this fixture has "
                "changed which branch it covers")
            integrity = ledger_engine.derive_holdings(loaded)["integrity"]
            assert bool(integrity) is expects_bad_integrity, (
                f"{label}: expected bad integrity={expects_bad_integrity}, got {integrity}")
            assert portfolio_basis_engine.query_current_book(
                loaded, skipped_lines=skipped,
                reference_as_of=dt.date.today().isoformat()) is None, (
                f"{label}: this fixture no longer reaches the refusal under test")

            run = _run("consider", "--root", tmp,
                       "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
            _fails(run, "no trustworthy canonical current book")
            assert not os.path.exists(_evaluation_path(tmp)), (
                f"{label}: a failed call must not write a row")


def test_csv_with_no_trade_rows_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = os.path.join(tmp, "empty.csv")
        _write_csv(csv_path, [])
        run = _run("consider", csv_path, "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        _fails(run, "empty book")


def test_missing_csv_path_fails_closed_with_a_clear_message():
    with tempfile.TemporaryDirectory() as tmp:
        missing = os.path.join(tmp, "does-not-exist.csv")
        run = _run("consider", missing, "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        _fails(run, "does not exist")


def test_a_declared_market_value_never_becomes_an_undisclosed_cost_fallback():
    """The original point of this test survives #515 intact and is now
    testable properly: a declared market value is a valuation, not a cost, and
    it must not quietly stand in for one. What changes is the consequence --
    that holding is excluded and named rather than taking the whole book down
    with it."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), [
            _snapshot_event("2026-01-01", [
                {"ticker": "SOLID", "shares": 10, "avg_cost": 100.0,
                 "market": "US", "currency": "USD"},
                {"ticker": "NVDA", "shares": 10, "market_value": 1500.0,
                 "market": "US", "currency": "USD"}]),
        ])
        row = _ok(_run("consider", "--root", tmp,
                       "--premise", '{"ticker": "SOLID", "side": "buy", "qty": 1, '
                                    '"price": 100.0, "currency": "USD"}'))["evaluation"]
        result = row["consequence"]
        assert result["excluded_holdings"] == [{"ticker": "NVDA", "reason": "unavailable_cost"}]
        assert "NVDA" not in result["before"]["held"], (
            "1500.0 is what the position is worth, not what it cost; letting it "
            "become a cost basis would put an unsupplied number into every "
            "weight on the card")


def test_a_priced_holding_with_no_cost_refuses_with_two_working_paths():
    """The sub-case #515's fix surfaced rather than created.

    A holding with a current price and no cost on record can be *valued* by the
    canonical projection but cannot be represented in the consequence engine at
    all, because a synthetic row carries cost as its price. Keeping the
    projection's whole-book denominator for `before` while `after` is computed
    without that position would compare two different books, so this refuses.

    What the refusal owes the user is a way forward, and it has two that both
    work today. Without that, "supplying more data made it fail" is all they
    see. #528 tracks removing the refusal entirely."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), [
            _snapshot_event("2026-01-01", [
                {"ticker": "AAA", "shares": 10, "avg_cost": 100.0,
                 "market": "US", "currency": "USD"},
                {"ticker": "DARK", "shares": 10, "market": "US", "currency": "USD"}]),
        ])
        feed = os.path.join(tmp, "prices.json")
        with open(feed, "w", encoding="utf-8") as handle:
            json.dump({"as_of": "2026-01-02", "source": "broker", "prices": [
                {"ticker": "AAA", "close": 100.0, "date": "2026-01-02", "currency": "USD"},
                {"ticker": "DARK", "close": 50.0, "date": "2026-01-02", "currency": "USD"}]},
                handle)
        run = _run("consider", "--root", tmp, "--prices", feed,
                   "--premise", '{"ticker": "AAA", "side": "buy", "qty": 1, '
                                '"price": 100.0, "currency": "USD"}')
        payload = _fails(run, "DARK has a current price but no cost on record")
        assert "without --prices" in payload["error"] and "average cost" in payload["error"], (
            "a refusal that names no way forward reads as a broken product")
        # Path two from that message, verified rather than merely promised.
        row = _ok(_run("consider", "--root", tmp,
                       "--premise", '{"ticker": "AAA", "side": "buy", "qty": 1, '
                                    '"price": 100.0, "currency": "USD"}'))["evaluation"]
        assert row["consequence"]["excluded_holdings"] == [
            {"ticker": "DARK", "reason": "unavailable_cost"}]


def test_a_rule_judged_against_a_partial_book_says_so():
    """#515's second invariant. The user wrote their cap against their whole
    book. An excluded position reads as weight zero here, so a cap that the
    hidden position is breaching comes back `clear` -- a verdict computed on a
    different denominator than the promise was made against, presented as the
    same claim. It may still be computed; it may not pass silently."""
    priced = [{"ticker": t, "shares": 10, "avg_cost": 100.0, "market": "US", "currency": "USD"}
              for t in ("AAA", "BBB")]
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), [
            _snapshot_event("2026-01-01", priced + [
                {"ticker": "DARK", "shares": 10, "market": "US", "currency": "USD"}]),
        ])
        with open(os.path.join(tmp, "rules.jsonl"), "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "rule", "rule_id": "r1", "date": "2026-01-01",
                "text": "Cap any single position at 25%.",
                "metric_key": "max_pos_pct", "problem_key": "oversize",
                "status": "tracking"}) + "\n")
        payload = _ok(_run("consider", "--root", tmp,
                           "--premise", '{"ticker": "AAA", "side": "buy", "qty": 1, '
                                        '"price": 100.0, "currency": "USD"}'))
        collisions = payload["evaluation"]["rule_collisions"]
        assert collisions, "the tracked rule must still be evaluated, not suppressed"
        assert all(row.get("partial_book") is True for row in collisions), (
            "every collision judged on the bounded book must carry the marker")


# ────────────────────────── D. premise validation ──────────────────────────

def test_malformed_premise_is_refused_with_a_useful_message():
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "price": 130.0, "qty": 5}')  # missing side
        _fails(run, "premise.side must be one of")


def test_premise_sell_of_unheld_ticker_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                   "--premise", '{"ticker": "AAPL", "side": "sell", "price": 130.0, "qty": 5}')
        _fails(run, "not currently held")


def test_premise_accepts_a_file_path_as_well_as_inline_json():
    with tempfile.TemporaryDirectory() as tmp:
        premise_path = os.path.join(tmp, "premise.json")
        with open(premise_path, "w", encoding="utf-8") as f:
            json.dump({"ticker": "AMD", "side": "buy", "price": 160.0, "qty": 5}, f)
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                   "--premise", premise_path)
        payload = _ok(run)
        assert payload["evaluation"]["premise"]["ticker"] == "AMD"


def test_consider_requires_premise_or_resolve():
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp)
        _fails(run, "requires --premise")


# ───────────────────────── E. resolve / fold ─────────────────────────

def test_resolve_appends_a_new_row_rather_than_rewriting():
    with tempfile.TemporaryDirectory() as tmp:
        created = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                           "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}'))
        evaluation_id = created["evaluation"]["evaluation_id"]

        resolved = _ok(_run("consider", "--root", tmp, "--resolve", evaluation_id,
                            "--decision", "acted"))
        assert resolved["evaluation"]["decision"] == "acted"
        assert resolved["evaluation"]["decided_on"] == dt.date.today().isoformat()

        on_disk = _read_evaluations(tmp)
        assert len(on_disk) == 2, "resolve must append, never rewrite the original row"
        assert on_disk[0]["decision"] == "open"
        assert on_disk[0]["evaluation_id"] == evaluation_id
        assert on_disk[1]["decision"] == "acted"
        assert on_disk[1]["evaluation_id"] == evaluation_id
        # every other frozen field survives the resolution unchanged
        assert on_disk[0]["consequence"] == on_disk[1]["consequence"]
        assert on_disk[0]["premise"] == on_disk[1]["premise"]


def test_fold_returns_the_latest_decision():
    with tempfile.TemporaryDirectory() as tmp:
        created = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                           "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}'))
        evaluation_id = created["evaluation"]["evaluation_id"]
        _ok(_run("consider", "--root", tmp, "--resolve", evaluation_id, "--decision", "modified"))
        _ok(_run("consider", "--root", tmp, "--resolve", evaluation_id, "--decision", "acted"))

        on_disk = _read_evaluations(tmp)
        assert len(on_disk) == 3
        latest = review_engine._fold_evaluations(on_disk)
        assert latest[evaluation_id]["decision"] == "acted"


def test_resolve_of_unknown_evaluation_id_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", "--root", tmp, "--resolve", "eval-doesnotexist",
                   "--decision", "acted")
        _fails(run, "no evaluation matching")


def test_decision_flag_requires_resolve():
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}',
                   "--decision", "acted")
        _fails(run, "--decision only applies together with --resolve")


def test_resolve_forbids_combining_with_premise():
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", "--root", tmp, "--resolve", "eval-x", "--decision", "acted",
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        _fails(run, "--resolve takes no premise")


def test_resolve_rejects_a_decision_of_open():
    """`open` is a row's starting state, never something --resolve records to
    -- argparse's own choices= is the gate, not a hand-written check."""
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", "--root", tmp, "--resolve", "eval-x", "--decision", "open")
        assert run.returncode != 0
        assert "invalid choice" in run.stderr.lower()


# ───────────────────────── F. schema and vocabulary drift ─────────────────────────

def test_created_row_round_trips_against_the_schema():
    with tempfile.TemporaryDirectory() as tmp:
        payload = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                           "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}'))
        _check_evaluation_shape(payload["evaluation"])


def test_resolved_row_round_trips_against_the_schema():
    with tempfile.TemporaryDirectory() as tmp:
        created = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                           "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}'))
        resolved = _ok(_run("consider", "--root", tmp, "--resolve",
                            created["evaluation"]["evaluation_id"], "--decision", "declined"))
        _check_evaluation_shape(resolved["evaluation"])


def test_consider_decisions_constant_matches_the_schemas_decision_enum():
    """review.CONSIDER_DECISIONS feeds --decision's argparse choices; the
    schema's decision enum is the same vocabulary plus "open", a row's
    starting state and never something --resolve records to. A second
    person adding a decision value in one place and not the other is exactly
    the "two readers, one fact" shape (development-guide.md section 7)."""
    schema_enum = set(EVALUATION_SCHEMA["properties"]["decision"]["enum"])
    assert schema_enum == set(review_engine.CONSIDER_DECISIONS) | {"open"}


def test_collision_states_constant_is_exactly_what_the_schema_declares():
    import consequence as cq  # noqa: E402  (engine dir already on sys.path)
    schema_states = set(
        EVALUATION_SCHEMA["properties"]["rule_collisions"]["items"]["properties"]["state"]["enum"])
    assert schema_states == set(cq.COLLISION_STATES)


def test_disclosures_constant_is_exactly_what_the_schema_declares():
    """The stored-row enum is what the engine can emit, plus exactly the keys
    it deliberately retired (#600's `mixed_currency_no_fx`). Both halves are
    declared once, in consequence.py, so a key can neither be emitted without
    the schema accepting it nor linger in the schema without someone having
    written down that it is history."""
    import consequence as cq  # noqa: E402
    schema_disclosures = set(
        EVALUATION_SCHEMA["properties"]["consequence"]["properties"]["disclosures"]["items"]["enum"])
    assert schema_disclosures == set(cq.DISCLOSURES) | set(cq.RETIRED_DISCLOSURES)
    assert not set(cq.DISCLOSURES) & set(cq.RETIRED_DISCLOSURES), (
        "a key cannot be both live and retired")


def test_a_retired_disclosure_is_not_offered_by_the_live_challenge_block():
    """The other half of the same fact. The stored row keeps a retired key
    readable; the challenge is emitted fresh on every call and never stored, so
    carrying one there would tell an agent it may owe a disclosure nothing can
    produce."""
    import consequence as cq  # noqa: E402
    challenge_keys = set(
        CHALLENGE_SCHEMA["properties"]["required_coverage"]["items"]["properties"]["key"]["enum"])
    assert not challenge_keys & set(cq.RETIRED_DISCLOSURES)
    assert set(cq.DISCLOSURES) <= challenge_keys


# ────── F2. what the answer could not read, through the real CLI (#598/#599/#600) ──────

def _fx_envelope(path, closes, fx=None, as_of="2026-07-30"):
    """A minimal --prices envelope. `closes` is {ticker: (close, currency)}."""
    payload = {"as_of": as_of, "source": "test",
               "prices": [{"ticker": ticker, "close": close, "date": as_of, "currency": currency}
                          for ticker, (close, currency) in sorted(closes.items())]}
    if fx:
        payload["fx"] = [{"currency": currency, "usd_per_unit": rate, "date": as_of}
                         for currency, rate in sorted(fx.items())]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


_TW_CLOSES = {"2330.TW": (1100.0, "TWD"), "2454.TW": (1400.0, "TWD"),
              "6488.TWO": (900.0, "TWD"), "AAPL": (230.0, "USD"), "AMD": (170.0, "USD"),
              "GOOG": (190.0, "USD"), "MSFT": (460.0, "USD")}
_TW_PREMISE = '{"ticker": "AAPL", "side": "buy", "price": 230.0, "qty": 10}'


def test_the_csv_lane_refuses_a_mixed_currency_book_with_no_fx_rate():
    """#600, end to end on the lane that had the defect. `usd_view` resolved a
    missing rate as 1.0, so a 220,000 TWD holding entered a USD denominator at
    face value: on this fixture that is roughly a 31x overstatement of its
    weight and a corresponding suppression of every USD holding's share, which
    can invert which position is the largest. The message names the currency
    and the remedy, because the caller can act on both in one round trip."""
    with tempfile.TemporaryDirectory() as tmp:
        payload = _fails(_run("consider", str(MOCK / "sample_tw_mixed.csv"), "--root", tmp,
                              "--premise", _TW_PREMISE), "TWD")
        assert "--prices" in payload["error"]
        # Refused before anything was recorded: a book that cannot be added up
        # has no evaluation to store.
        assert _read_evaluations(tmp) == []


def test_the_same_book_answers_once_the_rate_is_supplied():
    """The counterweight, and the proof the refusal above is about the missing
    rate rather than about mixed currency itself."""
    with tempfile.TemporaryDirectory() as tmp:
        prices = _fx_envelope(os.path.join(tmp, "px.json"), _TW_CLOSES, fx={"TWD": 0.0317})
        payload = _ok(_run("consider", str(MOCK / "sample_tw_mixed.csv"), "--root", tmp,
                           "--prices", prices, "--premise", _TW_PREMISE))
        after = payload["evaluation"]["consequence"]["after"]
        assert after["mixed_currency"] is True
        assert "fx_gaps" not in after, "the always-empty companion field is gone (#429)"
        weights = after["weights"]
        # Converted, the TWD leader is ~55% of the book. Unconverted it read as
        # ~98%, and every USD holding as noise.
        assert 0.50 < weights["2330.TW"] < 0.60, weights
        assert weights["MSFT"] > 0.05, weights
        assert "mixed_currency_no_fx" not in payload["evaluation"]["consequence"]["disclosures"]


def test_a_partially_legible_book_says_so_on_the_row_and_in_what_the_answer_owes():
    """#598 through the whole chain. The frozen row carries which positions the
    concentration figures could not read, and because `required_coverage` is
    derived from `disclosures`, the key also becomes something an --agent-case
    is refused for dropping — the obligation and the gate are one list."""
    with tempfile.TemporaryDirectory() as tmp:
        prices = _fx_envelope(os.path.join(tmp, "px.json"), _TW_CLOSES, fx={"TWD": 0.0317})
        payload = _ok(_run("consider", str(MOCK / "sample_tw_mixed.csv"), "--root", tmp,
                           "--prices", prices, "--premise", _TW_PREMISE))
        stored = payload["evaluation"]["consequence"]
        assert "unclassified_book" in stored["disclosures"]
        named = {row["ticker"]: row["weight"] for row in stored["unclassified_holdings"]}
        # The defect in one line: the book's largest holding by far is a
        # semiconductor company under its primary foreign listing, absent from
        # a fallback table with no foreign entries at all, so max_sector_pct
        # is measured without it.
        assert "2330.TW" in named and named["2330.TW"] > 0.50, named
        assert stored["after"]["max_sector_pct"] < named["2330.TW"]
        challenge = payload["challenge"]
        assert any(entry["key"] == "unclassified_book"
                   for entry in challenge["required_coverage"]), challenge["required_coverage"]
        assert any(entry["topic"] == "disclosure" and entry["value"] == "unclassified_book"
                   for entry in challenge["must_state"])
        # The row round-trips against the schema with the new fields on it.
        _check_evaluation_shape(payload["evaluation"])


def test_a_supplied_driver_map_is_a_real_remedy_not_just_advice():
    """The reference tells the agent to build a --driver-map and re-ask. If
    doing so did not actually clear the key, that instruction would be busywork
    and the disclosure would be permanent noise on every mixed-market book."""
    with tempfile.TemporaryDirectory() as tmp:
        prices = _fx_envelope(os.path.join(tmp, "px.json"), _TW_CLOSES, fx={"TWD": 0.0317})
        driver_map = os.path.join(tmp, "drivers.json")
        with open(driver_map, "w", encoding="utf-8") as handle:
            json.dump({ticker: ["semis", 1] for ticker in
                       ("2330.TW", "2454.TW", "6488.TWO", "AAPL", "AMD", "GOOG", "MSFT")}, handle)
        payload = _ok(_run("consider", str(MOCK / "sample_tw_mixed.csv"), "--root", tmp,
                           "--prices", prices, "--driver-map", driver_map,
                           "--premise", _TW_PREMISE))
        stored = payload["evaluation"]["consequence"]
        assert stored["unclassified_holdings"] == []
        assert "unclassified_book" not in stored["disclosures"]
        # And the figure the disclosure was qualifying moves to what it should
        # have said all along.
        assert stored["after"]["max_sector_pct"] > 0.90


def test_a_supplied_instrument_map_moves_a_holding_to_the_limitation_that_is_true():
    """#599's other half of the same remedy. Declaring a ticker a fund does not
    make its constituents visible — nothing here does that — so the position
    moves out of `unclassified_holdings`, where a driver map would have been
    the fix, and into `undecomposed_etfs`, where the limitation stated is the
    real one."""
    with tempfile.TemporaryDirectory() as tmp:
        prices = _fx_envelope(os.path.join(tmp, "px.json"), _TW_CLOSES, fx={"TWD": 0.0317})
        instrument_map = os.path.join(tmp, "instruments.json")
        with open(instrument_map, "w", encoding="utf-8") as handle:
            json.dump({"2454.TW": {"kind": "sector_etf"}}, handle)
        payload = _ok(_run("consider", str(MOCK / "sample_tw_mixed.csv"), "--root", tmp,
                           "--prices", prices, "--instrument-map", instrument_map,
                           "--premise", _TW_PREMISE))
        stored = payload["evaluation"]["consequence"]
        assert "2454.TW" not in {row["ticker"] for row in stored["unclassified_holdings"]}
        etfs = {row["ticker"]: row for row in stored["undecomposed_etfs"]}
        assert etfs["2454.TW"]["kind"] == "sector_etf"
        assert etfs["2454.TW"]["allocation_exempt"] is False
        assert "etf_not_decomposed" in stored["disclosures"]


# ───────────────────────── G. rule collisions ─────────────────────────

def test_rule_collision_is_reported_and_a_muted_rule_is_excluded():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "rules.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"rule_id": "rule-1", "text": "Keep NVDA under 25% of the book.",
                                "metric_key": "max_pos_pct", "problem_key": "oversize",
                                "created": "2024-06-01"}) + "\n")
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 20}')
        payload = _ok(run)
        collisions = payload["evaluation"]["rule_collisions"]
        assert len(collisions) == 1
        assert collisions[0]["rule_id"] == "rule-1"
        assert collisions[0]["state"] == "already_over"
        assert collisions[0]["worsens"] is True

        with open(os.path.join(tmp, "profile.json"), "w", encoding="utf-8") as f:
            json.dump({"muted_rules": ["rule-1"]}, f)
        payload = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                           "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 20}'))
        assert payload["evaluation"]["rule_collisions"] == []


# ───────────────────────── H. agent_case ─────────────────────────

def test_agent_case_requires_both_sides():
    with tempfile.TemporaryDirectory() as tmp:
        case_path = os.path.join(tmp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump({"for": [{"claim": "ok", "provenance": "agent_judgment"}]}, f)
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}',
                   "--agent-case", case_path)
        _fails(run, "must carry both 'for' and 'against'")


def test_agent_case_rejects_an_unknown_provenance():
    with tempfile.TemporaryDirectory() as tmp:
        case_path = os.path.join(tmp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump({"for": [{"claim": "ok", "provenance": "vibes"}], "against": []}, f)
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}',
                   "--agent-case", case_path)
        _fails(run, "provenance must be one of")


def _valid_agent_case_for_sample_momentum():
    """A provenance-clean case against the frozen consequence
    `consider --premise '{"ticker":"NVDA","side":"buy","price":130.0,"qty":5}'`
    produces over sample_momentum.csv: after.max_pct ~60.8%, disclosures
    ["cost_basis", "cash_unreliable"], basis.completeness "unverified".
    answer_provenance's case 6 requires an anchored claim covering every
    disclosure plus one basis/staleness fact -- this fixture is the minimal
    case that clears every check, used everywhere a passing --agent-case is
    needed against this exact premise/CSV pair."""
    return {
        "for": [
            {"claim": "Momentum is intact.", "provenance": "agent_judgment"},
            {"claim": "This is priced on cost, not a live market value.",
             "provenance": "engine_fact", "anchor": "consequence.disclosures.0"},
            {"claim": "The cash balance has no anchor and is a running sum only.",
             "provenance": "engine_fact", "anchor": "consequence.disclosures.1"},
        ],
        "against": [
            {"claim": "This grows NVDA to about 61% of the book, already the largest position.",
             "provenance": "engine_fact", "anchor": "consequence.after.max_pct"},
            {"claim": "This book comes from an unreconciled CSV import, not a declared snapshot.",
             "provenance": "engine_fact", "anchor": "basis.completeness"},
        ],
    }


def test_agent_case_is_stored_when_supplied_and_absent_when_not():
    with tempfile.TemporaryDirectory() as tmp:
        case_path = os.path.join(tmp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump(_valid_agent_case_for_sample_momentum(), f)
        with_case = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                             "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}',
                             "--agent-case", case_path))
        assert "agent_case" in with_case["evaluation"]
        _check_evaluation_shape(with_case["evaluation"])

        without_case = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                                "--premise",
                                '{"ticker": "NVDA", "side": "buy", "price": 131.0, "qty": 5}'))
        assert "agent_case" not in without_case["evaluation"]


def test_agent_case_with_a_wrong_number_is_rejected_on_the_production_path():
    """#414 / #479 Wave B: engine/answer_provenance.py::validate_agent_case
    is wired into cmd_consider itself, not only exercised in that module's
    own unit tests. A claim that quotes a number the frozen consequence does
    not support is refused through the real CLI -- and because the check
    runs before _append_evaluation_row, nothing lands in
    trade_evaluations.jsonl for the rejected attempt. The frozen after.max_pct
    for this exact premise/CSV pair is ~60.8% (see
    _valid_agent_case_for_sample_momentum); 90% is well outside tolerance."""
    with tempfile.TemporaryDirectory() as tmp:
        case_path = os.path.join(tmp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump({"for": [{"claim": "The trade is a modest sizing increase.",
                                "provenance": "agent_judgment"}],
                      "against": [{"claim": "This grows NVDA to about 90% of the book.",
                                   "provenance": "engine_fact",
                                   "anchor": "consequence.after.max_pct"}]}, f)
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                  "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}',
                  "--agent-case", case_path)
        _fails(run, "quotes a number that does not match the frozen value")
        assert _read_evaluations(tmp) == [], "a rejected agent_case must append nothing"


def test_agent_case_public_fact_restating_the_users_own_decision_context_is_rejected():
    """The load-bearing wiring named in #479 Wave B: --decision-context's
    exact reason/why_now become validate_agent_case's user_statements, so an
    agent that copies the user's own words back and mislabels them as an
    outside public_fact citation is refused (answer_provenance.py case 8) --
    through the real CLI, with both flags supplied together, not just inside
    that module's own fixtures."""
    with tempfile.TemporaryDirectory() as tmp:
        reason = "It is still my highest-conviction name in the book."
        context_path = os.path.join(tmp, "context.json")
        with open(context_path, "w", encoding="utf-8") as f:
            json.dump({"reason": reason,
                      "why_now": "Their main supplier raised capacity guidance this morning."}, f)
        case_path = os.path.join(tmp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump({"for": [{"claim": reason, "provenance": "public_fact",
                               "source": "Reuters", "as_of": "2026-07-29"}],
                      "against": [{"claim": "This trade would grow the position further.",
                                   "provenance": "agent_judgment"}]}, f)
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                  "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}',
                  "--decision-context", context_path, "--agent-case", case_path)
        _fails(run, "restates what the user said as public_fact")
        assert _read_evaluations(tmp) == [], "a rejected agent_case must append nothing"


def test_agent_case_public_fact_not_restating_context_is_accepted():
    """The other direction of the same wiring: a public_fact claim that does
    not match the supplied decision context's words is not case 8, and a
    fully provenance-clean case (with a real decision context alongside it)
    is stored, not refused."""
    with tempfile.TemporaryDirectory() as tmp:
        context_path = os.path.join(tmp, "context.json")
        with open(context_path, "w", encoding="utf-8") as f:
            json.dump({"reason": "It is still my highest-conviction name in the book.",
                      "why_now": "Their main supplier raised capacity guidance this morning."}, f)
        case = _valid_agent_case_for_sample_momentum()
        case["against"].append(
            {"claim": "The stock trades at a much higher multiple than a year ago.",
             "provenance": "public_fact", "source": "Market data provider", "as_of": "2026-07-29"})
        case_path = os.path.join(tmp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump(case, f)
        result = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                          "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}',
                          "--decision-context", context_path, "--agent-case", case_path))
        assert "agent_case" in result["evaluation"]
        assert "context" in result["evaluation"]
        _check_evaluation_shape(result["evaluation"])


def test_agent_case_with_an_empty_side_is_rejected_though_structurally_a_list():
    """answer_provenance.py case 5: an empty for/against list passes the
    cheap structural precheck (`isinstance(claims, list)` is true for `[]`)
    but is refused by the semantic gate -- the exact gap #479 Wave B closes.
    A minimal, anchor-free reproduction, kept distinct from the
    wrong-number test above so a future change to the numeric-tolerance
    check cannot accidentally make this one pass for the wrong reason."""
    with tempfile.TemporaryDirectory() as tmp:
        case_path = os.path.join(tmp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump({"for": [], "against": [{"claim": "Momentum is intact.",
                                               "provenance": "agent_judgment"}]}, f)
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                  "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}',
                  "--agent-case", case_path)
        _fails(run, "must be a non-empty list of claims")
        assert _read_evaluations(tmp) == [], "a rejected agent_case must append nothing"


# ───────────────────────── I. read-only w.r.t. review state ─────────────────────────

def test_consider_never_writes_rules_or_creates_session_scaffolding():
    with tempfile.TemporaryDirectory() as tmp:
        _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                 "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}'))
        assert not os.path.exists(os.path.join(tmp, "rules.jsonl"))
        assert not os.path.exists(os.path.join(tmp, "problems.jsonl"))
        assert not os.path.exists(os.path.join(tmp, "sessions"))
        assert not os.path.exists(os.path.join(tmp, ".pending"))
        assert not os.path.exists(os.path.join(tmp, "log.jsonl"))
        # the one file consider is allowed to create
        assert os.path.exists(_evaluation_path(tmp))


def test_trade_evaluations_is_registered_in_coach_data_files():
    text = COACH_PY.read_text(encoding="utf-8")
    assert '"trade_evaluations.jsonl"' in text
    # exercised for real, not just grepped: data-status/-export/-reset must see it
    with tempfile.TemporaryDirectory() as tmp:
        _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                 "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}'))
        status = subprocess.run([sys.executable, str(COACH_PY), "data-status", "--root", tmp],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert status.returncode == 0, status.stdout + status.stderr
        names = {entry["name"] for entry in json.loads(status.stdout)["files"]}
        assert "trade_evaluations.jsonl" in names
        entry = next(e for e in json.loads(status.stdout)["files"]
                     if e["name"] == "trade_evaluations.jsonl")
        assert entry["exists"] is True and entry["lines"] == 1

        reset = subprocess.run([sys.executable, str(COACH_PY), "data-reset", "--confirm", "--root", tmp],
                               cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert reset.returncode == 0, reset.stdout + reset.stderr
        assert not os.path.exists(_evaluation_path(tmp)), "data-reset must remove the file"


# ─────────────────── J. evaluation_reconciliation (prepare) ───────────────────

def test_matched_evaluation_reports_the_trades_date_and_qty():
    with tempfile.TemporaryDirectory() as tmp:
        _write_evaluations(tmp, [_open_evaluation("eval-1", "2026-07-12", "NVDA", "buy")])
        rows = [_tr_row("NVDA", "buy", 20, 135.0, "2026-07-14")]
        result = review_engine._evaluation_reconciliation(tmp, rows, "2026-07-20")
        _check_reconciliation_shape(result)
        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["evaluation_id"] == "eval-1"
        assert item["status"] == "matched"
        assert item["matched_trade"] == {"date": "2026-07-14", "qty": 20}
        assert result["summary"] == {"open_total": 1, "shown": 1, "beyond_cap": 0}


def test_unmatched_evaluation_when_no_trade_exists():
    with tempfile.TemporaryDirectory() as tmp:
        _write_evaluations(tmp, [_open_evaluation("eval-1", "2026-07-12", "NVDA", "buy")])
        result = review_engine._evaluation_reconciliation(tmp, [], "2026-07-20")
        _check_reconciliation_shape(result)
        assert result["items"][0]["status"] == "unmatched"
        assert result["items"][0]["matched_trade"] is None


def test_opposite_side_trade_does_not_match():
    with tempfile.TemporaryDirectory() as tmp:
        _write_evaluations(tmp, [_open_evaluation("eval-1", "2026-07-12", "NVDA", "buy")])
        rows = [_tr_row("NVDA", "sell", 20, 135.0, "2026-07-14")]
        result = review_engine._evaluation_reconciliation(tmp, rows, "2026-07-20")
        assert result["items"][0]["status"] == "unmatched", \
            "a sell must never satisfy an evaluation that asked about a buy"


def test_different_ticker_trade_does_not_match():
    with tempfile.TemporaryDirectory() as tmp:
        _write_evaluations(tmp, [_open_evaluation("eval-1", "2026-07-12", "NVDA", "buy")])
        rows = [_tr_row("AMD", "buy", 20, 135.0, "2026-07-14")]
        result = review_engine._evaluation_reconciliation(tmp, rows, "2026-07-20")
        assert result["items"][0]["status"] == "unmatched"


def test_trade_dated_before_the_evaluation_does_not_match():
    with tempfile.TemporaryDirectory() as tmp:
        _write_evaluations(tmp, [_open_evaluation("eval-1", "2026-07-12", "NVDA", "buy")])
        rows = [_tr_row("NVDA", "buy", 20, 135.0, "2026-07-10")]  # before created
        result = review_engine._evaluation_reconciliation(tmp, rows, "2026-07-20")
        assert result["items"][0]["status"] == "unmatched", \
            "a trade that predates the evaluation cannot be its answer"


def test_trade_dated_after_date_end_does_not_match():
    with tempfile.TemporaryDirectory() as tmp:
        _write_evaluations(tmp, [_open_evaluation("eval-1", "2026-07-12", "NVDA", "buy")])
        rows = [_tr_row("NVDA", "buy", 20, 135.0, "2026-07-25")]  # after this review's date_end
        result = review_engine._evaluation_reconciliation(tmp, rows, "2026-07-20")
        assert result["items"][0]["status"] == "unmatched", \
            "a trade outside this review's window belongs to a later reconciliation, not this one"


def test_boundary_dates_are_inclusive():
    with tempfile.TemporaryDirectory() as tmp:
        _write_evaluations(tmp, [_open_evaluation("eval-1", "2026-07-12", "NVDA", "buy")])
        on_created = [_tr_row("NVDA", "buy", 5, 100.0, "2026-07-12")]
        assert review_engine._evaluation_reconciliation(
            tmp, on_created, "2026-07-20")["items"][0]["status"] == "matched"
        on_date_end = [_tr_row("NVDA", "buy", 5, 100.0, "2026-07-20")]
        assert review_engine._evaluation_reconciliation(
            tmp, on_date_end, "2026-07-20")["items"][0]["status"] == "matched"


def test_earliest_qualifying_trade_is_reported_when_several_match():
    with tempfile.TemporaryDirectory() as tmp:
        _write_evaluations(tmp, [_open_evaluation("eval-1", "2026-07-01", "NVDA", "buy")])
        rows = [_tr_row("NVDA", "buy", 30, 140.0, "2026-07-15"),
                _tr_row("NVDA", "buy", 12, 128.0, "2026-07-05")]
        result = review_engine._evaluation_reconciliation(tmp, rows, "2026-07-20")
        assert result["items"][0]["matched_trade"] == {"date": "2026-07-05", "qty": 12}


def test_resolved_evaluation_does_not_appear():
    with tempfile.TemporaryDirectory() as tmp:
        open_row = _open_evaluation("eval-1", "2026-07-12", "NVDA", "buy")
        resolved_row = {**open_row, "decision": "acted", "decided_on": "2026-07-15"}
        _write_evaluations(tmp, [open_row, resolved_row])
        result = review_engine._evaluation_reconciliation(tmp, [], "2026-07-20")
        assert result["items"] == []
        assert result["summary"] == {"open_total": 0, "shown": 0, "beyond_cap": 0}


def test_reconciliation_is_empty_and_harmless_with_no_evaluations_file():
    """The overwhelmingly common case (a root that has never called
    `consider`): a missing file must read as zero open evaluations, never
    an error."""
    with tempfile.TemporaryDirectory() as tmp:
        result = review_engine._evaluation_reconciliation(tmp, [], "2026-07-20")
        assert result == {"items": [], "summary": {"open_total": 0, "shown": 0, "beyond_cap": 0}}


def test_cap_holds_and_discloses_what_it_held_back():
    with tempfile.TemporaryDirectory() as tmp:
        rows = [_open_evaluation(f"eval-{i}", f"2026-07-{i:02d}", "NVDA", "buy")
                for i in range(1, 12)]
        _write_evaluations(tmp, rows)
        result = review_engine._evaluation_reconciliation(tmp, [], "2026-07-20")
        _check_reconciliation_shape(result)
        assert review_engine.EVALUATION_RECONCILE_CAP == 8
        assert len(result["items"]) == 8
        assert result["summary"] == {"open_total": 11, "shown": 8, "beyond_cap": 3}
        assert [item["evaluation_id"] for item in result["items"]] == \
            [f"eval-{i}" for i in range(1, 9)], \
            "oldest created first; a capped list must not skip around"


def test_prepare_wires_reconciliation_and_never_mutates_the_evaluation_file():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        _write_evaluations(root, [
            _open_evaluation("eval-matched", "2026-07-01", "NVDA", "buy", qty=20, price=130.0),
            _open_evaluation("eval-unmatched", "2026-07-01", "AMD", "sell", qty=5, price=90.0),
        ])
        _write_ledger(os.path.join(root, "ledger.jsonl"), [
            _trade_event("2026-07-05", "NVDA", "buy", 20, 135.0),
        ])
        before = pathlib.Path(_evaluation_path(root)).read_bytes()

        plan = _prepare_plan(tmp, root, "2026-07-20")
        recon = plan["evaluation_reconciliation"]
        _check_reconciliation_shape(recon)
        by_id = {item["evaluation_id"]: item for item in recon["items"]}
        assert by_id["eval-matched"]["status"] == "matched"
        assert by_id["eval-matched"]["matched_trade"] == {"date": "2026-07-05", "qty": 20.0}
        assert by_id["eval-unmatched"]["status"] == "unmatched"
        assert by_id["eval-unmatched"]["matched_trade"] is None
        assert recon["summary"] == {"open_total": 2, "shown": 2, "beyond_cap": 0}

        after = pathlib.Path(_evaluation_path(root)).read_bytes()
        assert before == after, ("prepare must never rewrite trade_evaluations.jsonl -- "
                                 "decision moves only through consider --resolve")


def test_prepare_reconciliation_is_empty_and_harmless_with_no_evaluations_file():
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        plan = _prepare_plan(tmp, root, "2026-07-20")
        assert plan["evaluation_reconciliation"] == {
            "items": [], "summary": {"open_total": 0, "shown": 0, "beyond_cap": 0}}
        assert not os.path.exists(_evaluation_path(root)), \
            "prepare must never create an evaluations file that did not exist"


def test_prepare_fails_closed_on_a_corrupt_ledger_before_evaluation_reconciliation():
    """#462: _build_plan's own ledger.load_ledger call (distinct from
    _ingest_trades' and _prepare_exit_capture's -- both bypassed here via
    --test-drive and omitting a CSV path, isolating this one) feeds
    evaluation_reconciliation. A corrupt row must block prepare rather than
    let a matched/unmatched verdict be computed from a silently shortened
    ledger -- this is the "every route computes this" read the review.py
    comment above ledger.load_ledger describes, exercised on the route with
    the fewest other ledger reads in its way."""
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
        card_path, state_path = _minimal_prepare_artifacts(tmp, "2026-07-20")
        _write_ledger(os.path.join(root, "ledger.jsonl"), [
            _trade_event("2026-07-05", "NVDA", "buy", 20, 135.0),
        ])
        with open(os.path.join(root, "ledger.jsonl"), "a", encoding="utf-8") as f:
            f.write("not json at all\n")
        run = _run("prepare", "--test-drive", "--root", root,
                   "--card-json", card_path, "--state-json", state_path)
        _fails(run, "unreadable row(s)")


# ───────────────── K. evaluation_id — content-addressed identity ─────────────────
# External review BLOCK finding: the id must change whenever anything that
# changed the frozen consequence changed, not only when premise/basis/created
# do. review._evaluation_id seeds on the frozen consequence and
# rule_collisions themselves (not an enumerated list of inputs), which closes
# the whole class at once -- --cash is the reviewer's own reproduction, but
# the same mechanism covers --prices, --driver-map, --instrument-map, and the
# position cap override without naming any of them individually.

def test_identical_inputs_produce_the_identical_evaluation_id():
    """Two consider calls with byte-identical arguments, the same day, must
    converge on the same id -- and _append_evaluation_row's content-based
    idempotency then makes the second call a no-op, not a second row."""
    with tempfile.TemporaryDirectory() as tmp:
        args = ["consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
               "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}',
               "--cash", '{"as_of": "2026-07-26", "amount": 1000, "currency": "USD"}']
        first = _ok(_run(*args))
        second = _ok(_run(*args))
        assert first["evaluation"]["evaluation_id"] == second["evaluation"]["evaluation_id"]
        rows = _read_evaluations(tmp)
        assert len(rows) == 1, f"a byte-identical repeat must not append a second row: {rows}"


def test_a_different_cash_anchor_the_same_day_produces_a_different_evaluation_id():
    """External review BLOCK, reproduced verbatim: the same ledger, the same
    premise, the same day, but --cash amount 0 on the first call and 1000 on
    the second. Before the fix the id was seeded on premise/basis/created
    alone -- none of which differ here -- so both calls produced the SAME
    evaluation_id despite freezing materially different consequences, and
    _fold_evaluations' latest-wins semantics silently treated the second as
    superseding the first: a later --resolve naming that id would land on
    whichever row happened to be folded last, not necessarily the one the
    user meant. The anchor's as_of (2026-07-26) postdates every row in
    sample_momentum.csv, so no other cash flow is counted after it and the
    frozen cash balance equals the anchor amount exactly -- a clean,
    unambiguous discriminator between the two calls."""
    with tempfile.TemporaryDirectory() as tmp:
        premise = '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}'
        first = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                         "--premise", premise,
                         "--cash", '{"as_of": "2026-07-26", "amount": 0, "currency": "USD"}'))
        second = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                          "--premise", premise,
                          "--cash", '{"as_of": "2026-07-26", "amount": 1000, "currency": "USD"}'))

        assert first["evaluation"]["consequence"]["after"]["cash"]["balance"] == 0.0
        assert second["evaluation"]["consequence"]["after"]["cash"]["balance"] == 1000.0
        id_1 = first["evaluation"]["evaluation_id"]
        id_2 = second["evaluation"]["evaluation_id"]
        assert id_1 != id_2, \
            "two materially different frozen consequences must not collide on one evaluation_id"

        rows = _read_evaluations(tmp)
        assert len(rows) == 2, f"both evaluations must be preserved, not folded into one: {rows}"
        assert {row["evaluation_id"] for row in rows} == {id_1, id_2}


# ───────────────── L. decision context (#479 Wave A) ─────────────────
# The optional envelope carrying what the user said at the moment they were
# deciding: their reason, their why-now, and a bounded handful of things they
# pointed at. Three properties this section exists to hold, each one an
# acceptance criterion of #479 that the obvious implementation breaks:
#
#   1. an absent context contributes NOTHING to the identity seed, so a caller
#      who never sends one keeps minting the ids they always did;
#   2. a different context on the same premise/book/day mints a distinct
#      evaluation, while the frozen arithmetic is byte-identical;
#   3. an over-limit envelope is refused with its limit named, never truncated.
#
# All fixtures below are fictional and public-safe (#479's own privacy
# boundary): a made-up ticker where the row is built by hand, and the repo's
# synthetic mock CSV where a real call is needed.

# A fixed, fictional evaluation. Nothing here reads a clock, so this is the one
# place in this file where a hash can be pinned as a literal at all.
_PINNED_PREMISE = {"ticker": "ACME", "side": "buy", "qty": 10.0, "price": 25.0,
                   "date": "2026-03-02", "currency": "USD"}
_PINNED_BASIS = {"source": "snapshot_anchor", "as_of": "2026-03-01", "stale_days": 0,
                 "completeness": "declared_complete", "cost_basis": "average_cost",
                 "valuation_basis": "unpriced", "reconciliation_ref": None,
                 "state_version": "pb-v1:" + "0" * 64}
_PINNED_CREATED = "2026-03-01"
_PINNED_CONSEQUENCE = {"before": {}, "after": {}, "delta": {}, "disclosures": [],
                       "excluded_holdings": []}
_PINNED_COLLISIONS = []

# Two contexts differing in why_now alone -- #479's acceptance names exactly
# this pair, because it is the one an agent produces when the user re-asks the
# same trade with a different story about why today.
_CONTEXT_EVIDENCE = {
    "reason": "This is my highest-conviction name and the build-out still has room.",
    "why_now": "Their main supplier guided capacity up this morning.",
    "evidence_refs": ["Supplier capacity guidance, this morning"],
}
_CONTEXT_PRICE = {
    "reason": "This is my highest-conviction name and the build-out still has room.",
    "why_now": "It is down 8% today and I do not want to miss the bounce.",
    "evidence_refs": ["Supplier capacity guidance, this morning"],
}


def _consider_with(tmp, context=None, premise=None):
    args = ["consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
            "--premise", premise or '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}']
    if context is not None:
        args += ["--decision-context", json.dumps(context, ensure_ascii=False)]
    return _run(*args)


def test_a_context_free_evaluation_id_is_exactly_what_it_was_before_context_existed():
    """The pin the whole envelope hangs on.

    `_evaluation_id` hashes `session.canonical({...})`. The obvious way to add
    an optional field is to put `"context": context` in that dict
    unconditionally -- which changes the hash of *every* call, including every
    context-free one. An existing user's next plain re-ask would then mint a
    fresh id instead of converging on the row already on disk: a duplicated
    row, and both of #479's own "context-free consider remains byte compatible"
    and "exact retry does not duplicate" criteria broken at once, silently,
    with the entire suite still green (nothing else in this file pins an id
    *value*).

    So the value below is not a fixture -- it is the literal this function
    returned on main@52df7f9, before `context` was a parameter at all, run
    against these same arguments. It must never move again. If a future change
    to the seed makes this fail, that change is the finding.
    """
    assert review_engine._evaluation_id(
        _PINNED_PREMISE, _PINNED_BASIS, _PINNED_CREATED,
        _PINNED_CONSEQUENCE, _PINNED_COLLISIONS) == "eval-a8d8c02f625ce105"
    # ... and passing the parameter explicitly as absent is the same call.
    assert review_engine._evaluation_id(
        _PINNED_PREMISE, _PINNED_BASIS, _PINNED_CREATED,
        _PINNED_CONSEQUENCE, _PINNED_COLLISIONS, context=None) == "eval-a8d8c02f625ce105"
    # A supplied context must move it -- otherwise the pin above is satisfied
    # by a seed that ignores context entirely, and this test would be green on
    # an implementation that fails every other test in this section.
    assert review_engine._evaluation_id(
        _PINNED_PREMISE, _PINNED_BASIS, _PINNED_CREATED,
        _PINNED_CONSEQUENCE, _PINNED_COLLISIONS,
        context=_CONTEXT_EVIDENCE) != "eval-a8d8c02f625ce105"


def test_a_context_free_row_carries_no_context_key_at_all():
    """The storage half of the same fact: absent, not null. The row's presence
    test and the seed's are the same condition, so a stored null would be a row
    whose id says no context and whose content says an empty one."""
    with tempfile.TemporaryDirectory() as tmp:
        payload = _ok(_consider_with(tmp))
        row = payload["evaluation"]
        assert "context" not in row, f"a context-free row must not carry the key: {row}"
        assert _read_evaluations(tmp)[0] == row
        _check_evaluation_shape(row)


def test_a_different_why_now_mints_a_distinct_evaluation_with_identical_arithmetic():
    """#479's acceptance, both halves in one place: same premise, same book,
    same day, different why_now -> distinct evaluations; and no context field
    changes any numeric computation. The second assertion is what keeps the
    first honest -- seeding identity on the context is only legitimate because
    `consequence` and `rule_collisions` are computed from the premise and the
    book, before the seed is taken, and never see the context at all."""
    with tempfile.TemporaryDirectory() as tmp:
        first = _ok(_consider_with(tmp, _CONTEXT_EVIDENCE))["evaluation"]
        second = _ok(_consider_with(tmp, _CONTEXT_PRICE))["evaluation"]

        assert first["evaluation_id"] != second["evaluation_id"], \
            "the same trade asked with a different why_now is a different question"
        assert first["created"] == second["created"], "fixture must pin the same day"

        for field in ("consequence", "rule_collisions", "basis", "premise"):
            assert json.dumps(first[field], sort_keys=True) == \
                json.dumps(second[field], sort_keys=True), \
                f"{field} must be byte-identical: no context field touches arithmetic"

        assert first["context"] == _CONTEXT_EVIDENCE
        assert second["context"] == _CONTEXT_PRICE
        rows = _read_evaluations(tmp)
        assert len(rows) == 2, f"neither may fold over the other: {rows}"
        _check_evaluation_shape(first)
        _check_evaluation_shape(second)


def test_an_identical_context_bearing_retry_is_still_a_no_op():
    """Idempotency survives the seed change: the same call twice converges on
    one id and appends one row, exactly as a context-free repeat does."""
    with tempfile.TemporaryDirectory() as tmp:
        first = _ok(_consider_with(tmp, _CONTEXT_EVIDENCE))["evaluation"]
        second = _ok(_consider_with(tmp, _CONTEXT_EVIDENCE))["evaluation"]
        assert first["evaluation_id"] == second["evaluation_id"]
        assert len(_read_evaluations(tmp)) == 1


def test_evidence_refs_over_the_cap_are_refused_and_the_error_names_the_limit():
    """An unbounded evidence list is the #429 shape -- it rides into agent
    context on every later turn that surfaces the evaluation and grows without
    limit. It is refused rather than truncated: a shortened list would read
    back as everything the user cited, a claim they never made. The cap itself
    must appear in the message, or the caller cannot tell what to send instead.
    """
    cap = review_engine.EVALUATION_EVIDENCE_REFS_CAP
    with tempfile.TemporaryDirectory() as tmp:
        at_cap = dict(_CONTEXT_EVIDENCE, evidence_refs=[f"note {i}" for i in range(cap)])
        row = _ok(_consider_with(tmp, at_cap))["evaluation"]
        assert len(row["context"]["evidence_refs"]) == cap, "the cap itself must be accepted"

        over_cap = dict(_CONTEXT_EVIDENCE, evidence_refs=[f"note {i}" for i in range(cap + 1)])
        payload = _fails(_consider_with(tmp, over_cap), "evidence_refs")
        assert str(cap) in payload["error"], \
            f"the refusal must name the limit, got {payload['error']!r}"
        assert str(cap + 1) in payload["error"], "and what was actually sent"

        rows = _read_evaluations(tmp)
        assert len(rows) == 1, "a refused call records nothing"
        assert len(rows[0]["context"]["evidence_refs"]) == cap, \
            "and above all does not store a truncated list"


def test_oversized_reason_or_why_now_is_refused_rather_than_shortened():
    with tempfile.TemporaryDirectory() as tmp:
        limit = review_engine.EVALUATION_CONTEXT_TEXT_MAX
        for field in ("reason", "why_now"):
            over = dict(_CONTEXT_EVIDENCE)
            over[field] = "x" * (limit + 1)
            payload = _fails(_consider_with(tmp, over), field)
            assert str(limit) in payload["error"], "the refusal must name the limit"
        assert not os.path.exists(_evaluation_path(tmp)), "nothing is recorded"

        long_ref = dict(_CONTEXT_EVIDENCE,
                        evidence_refs=["y" * (review_engine.EVALUATION_EVIDENCE_REF_MAX + 1)])
        _fails(_consider_with(tmp, long_ref), "evidence_refs[0]")


def test_decision_context_requires_both_reason_and_why_now():
    """The same both-sides-or-nothing rule --agent-case follows. Telling new
    evidence apart from a price move is the question this envelope exists to
    make askable; a reason with no why_now is the half that lets it pass
    unasked. The refusal names the fix, because the caller's next move is to
    ask the user one more question -- which is the product working, not
    failing."""
    with tempfile.TemporaryDirectory() as tmp:
        for missing in ("reason", "why_now"):
            partial = {k: v for k, v in _CONTEXT_EVIDENCE.items()
                       if k != missing and k != "evidence_refs"}
            payload = _fails(_consider_with(tmp, partial), "must carry both")
            assert missing in payload["error"]
        _fails(_consider_with(tmp, {}), "must carry both")
        _fails(_consider_with(tmp, dict(_CONTEXT_EVIDENCE, reason="   ")), "non-empty string")
        # a flag sent with nothing behind it is a lost statement, not a
        # context-free call: refused by name rather than quietly ignored
        _fails(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                    "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}',
                    "--decision-context", ""),
               "--decision-context must not be empty")
        # the escape hatch the refusal points at must actually work
        _ok(_consider_with(tmp))


def test_decision_context_refuses_a_field_the_schema_does_not_declare():
    """Derived from the schema rather than mirrored against it: every declared
    property must be accepted together, and a name it does not declare must be
    refused. A field the agent invents is not silently dropped -- the row is
    the user's own words, and a dropped one is a statement that vanished."""
    with tempfile.TemporaryDirectory() as tmp:
        declared = {"reason": "A real reason.", "why_now": "A real change.",
                    "evidence_refs": ["One reference"]}
        assert set(declared) == set(CONTEXT_SCHEMA["properties"]), \
            "this fixture must exercise every declared property"
        row = _ok(_consider_with(tmp, declared))["evaluation"]
        assert row["context"] == declared

        payload = _fails(_consider_with(tmp, dict(declared, sentiment="bullish")),
                         "unknown fields")
        assert "sentiment" in payload["error"]


def test_decision_context_bounds_are_the_same_numbers_the_schema_publishes():
    """review.py's constants feed the refusals; decision-context.schema.json's
    maxItems/maxLength are what a reader of the contract sees. A second person
    relaxing one and not the other is the "two readers, one fact" shape
    (development-guide.md section 7) -- the same drift test
    test_consider_decisions_constant_matches_the_schemas_decision_enum runs
    over the decision enum."""
    props = CONTEXT_SCHEMA["properties"]
    assert props["evidence_refs"]["maxItems"] == review_engine.EVALUATION_EVIDENCE_REFS_CAP
    assert props["evidence_refs"]["items"]["maxLength"] == review_engine.EVALUATION_EVIDENCE_REF_MAX
    for field in ("reason", "why_now"):
        assert props[field]["maxLength"] == review_engine.EVALUATION_CONTEXT_TEXT_MAX
    assert set(CONTEXT_SCHEMA["required"]) == {"reason", "why_now"}
    # and the evaluation row reaches this shape by $ref, never by restating it
    ref = EVALUATION_SCHEMA["properties"]["context"]["$ref"]
    assert ref == "decision-context.schema.json", \
        "the agent-facing shape is declared once, the way `premise` already is"


def test_a_context_survives_resolve_export_and_reset():
    """Round-trip, #479's acceptance: the exact supplied context must come back
    unchanged through retry/idempotency/export, and data-reset must still see
    the file. --resolve appends a new row for the same id, so the context has
    to travel with it -- a resolution that dropped the user's words would leave
    the record thinner than the conversation that produced it."""
    with tempfile.TemporaryDirectory() as tmp:
        created = _ok(_consider_with(tmp, _CONTEXT_EVIDENCE))["evaluation"]
        resolved = _ok(_run("consider", "--root", tmp, "--resolve",
                            created["evaluation_id"], "--decision", "acted"))["evaluation"]
        assert resolved["context"] == _CONTEXT_EVIDENCE, "the words travel with the resolution"
        assert resolved["evaluation_id"] == created["evaluation_id"], \
            "resolving does not re-derive the id, so the context cannot move it"
        _check_evaluation_shape(resolved)

        export = os.path.join(tmp, "export.zip")
        run = subprocess.run([sys.executable, str(COACH_PY), "data-export",
                              "--out", export, "--root", tmp],
                             cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stdout + run.stderr
        import zipfile
        with zipfile.ZipFile(export) as zf:
            exported = zf.read("trade_evaluations.jsonl").decode("utf-8")
        rows = [json.loads(line) for line in exported.splitlines() if line.strip()]
        assert [row["context"] for row in rows] == [_CONTEXT_EVIDENCE, _CONTEXT_EVIDENCE], \
            "the export carries the user's words verbatim, not a summary of them"

        reset = subprocess.run([sys.executable, str(COACH_PY), "data-reset", "--confirm",
                                "--root", tmp], cwd=ROOT, capture_output=True, text=True,
                               timeout=30)
        assert reset.returncode == 0, reset.stdout + reset.stderr
        assert not os.path.exists(_evaluation_path(tmp))


def test_resolve_refuses_a_decision_context():
    """--resolve takes no premise and no other consideration flag: the
    evaluation it names already froze the user's words along with everything
    else. Accepting one here would let a later call restate what was said at
    decision time, under an id that still hashes the original."""
    with tempfile.TemporaryDirectory() as tmp:
        created = _ok(_consider_with(tmp, _CONTEXT_EVIDENCE))["evaluation"]
        run = _run("consider", "--root", tmp, "--resolve", created["evaluation_id"],
                   "--decision", "acted",
                   "--decision-context", json.dumps(_CONTEXT_PRICE, ensure_ascii=False))
        _fails(run, "--decision-context")


def test_a_row_written_before_context_existed_stays_readable():
    """Replay compatibility, the engine_version/authoring_contract precedent: a
    row on a user's disk from before this field existed carries no context, and
    every reader must go on working -- the fold, --resolve, and the review's own
    reconciliation. A resolution of one stays context-free rather than being
    back-filled with an empty envelope."""
    with tempfile.TemporaryDirectory() as tmp:
        legacy = _open_evaluation("eval-legacyrow000001", "2026-02-01", "ACME", "buy")
        assert "context" not in legacy, "the fixture must be a genuine pre-context row"
        _write_evaluations(tmp, [legacy])

        folded = review_engine._fold_evaluations(_read_evaluations(tmp))
        assert set(folded) == {"eval-legacyrow000001"}

        resolved = _ok(_run("consider", "--root", tmp, "--resolve", "eval-legacyrow000001",
                            "--decision", "declined"))["evaluation"]
        assert "context" not in resolved, "an old row is not back-filled with an empty context"
        assert resolved["decision"] == "declined"

        # and a review still reconciles it, beside a context-bearing sibling
        _ok(_consider_with(tmp, _CONTEXT_EVIDENCE))
        rows = [_tr_row("ACME", "buy", 10, 100.0, "2026-02-05")]
        payload = review_engine._evaluation_reconciliation(tmp, rows, "2026-02-28")
        _check_reconciliation_shape(payload)
        assert payload["summary"]["open_total"] == 1, \
            "the declined legacy row is settled; only the new one is open"


# ───────────────── M. split basis on both routes (#550/#558) ─────────────────
#
# `consider`'s promise is to challenge a contemplated trade against the book
# the user actually holds. A share count accumulated across a split is not that
# book: 90 bought before a ten-for-one, minus 100 sold after it, is zero. The
# ledger route was fixed in #567; the CSV route reads `trade_recap.load()` rows
# and had no adjustment at all.

_SPLIT_CSV = [("NVDA", "BUY", 90, 150.00, "2023-01-10"),
              ("NVDA", "BUY", 30, 480.00, "2023-11-15"),
              ("NVDA", "SELL", 20, 950.00, "2024-05-20"),
              ("NVDA", "SELL", 100, 197.00, "2026-07-28")]
_NVDA_TEN_FOR_ONE = [["2024-06-10", 10]]
_NVDA_PREMISE = ('{"ticker": "NVDA", "side": "buy", "qty": 10, '
                 '"price": 197.0, "currency": "USD"}')


def _price_feed(path, *, splits=None, ticker="NVDA", close=197.0, as_of="2026-07-29"):
    row = {"ticker": ticker, "close": close, "date": as_of, "currency": "USD"}
    if splits is not None:
        row["splits"] = splits
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"as_of": as_of, "source": "broker", "prices": [row]}, handle)
    return path


def test_the_csv_route_reasons_over_a_split_adjusted_book():
    """The user holds 900 NVDA. Told nothing about the split, `consider`
    computes the whole answer — weight, concentration, what the new buy does
    to the book — against a book that does not contain the position at all,
    and says nothing about it. The envelope's own `splits` is what it reads;
    no fetch is added to this command."""
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = os.path.join(tmp, "trades.csv")
        _write_csv(csv_path, _SPLIT_CSV)
        told = _price_feed(os.path.join(tmp, "told.json"), splits=_NVDA_TEN_FOR_ONE)
        row = _ok(_run("consider", csv_path, "--root", tmp, "--prices", told,
                       "--premise", _NVDA_PREMISE))["evaluation"]
        held = row["consequence"]["before"]["held"]
        assert "NVDA" in held, f"the split-crossing position must be in the book: {held}"
        assert abs(held["NVDA"]["shares"] - 900.0) < 1e-6, held["NVDA"]

        # The control: the same CSV with a silent envelope is the pre-fix
        # answer, which is a book with no NVDA in it — kept here so a later
        # change that makes both paths agree by accident is visible.
        silent = _price_feed(os.path.join(tmp, "silent.json"))
        blind = _run("consider", csv_path, "--root", tmp, "--prices", silent,
                     "--premise", _NVDA_PREMISE)
        blind_held = _ok(blind)["evaluation"]["consequence"]["before"]["held"]
        assert "NVDA" not in blind_held, \
            f"pre-#558 behaviour must stay reproducible with no map: {blind_held}"


def test_a_book_that_never_split_answers_identically_with_or_without_a_map():
    """The counterweight, and the reason the adjustment may be applied
    unconditionally: a map that says nothing about this book must change
    nothing about the answer — including the frozen `state_version`, which is
    a digest of the very rows the adjustment would have touched."""
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = os.path.join(tmp, "trades.csv")
        _write_csv(csv_path, [("ACME", "BUY", 10, 100.00, "2026-01-05"),
                              ("ACME", "SELL", 4, 120.00, "2026-02-05")])
        premise = ('{"ticker": "ACME", "side": "buy", "qty": 1, '
                   '"price": 120.0, "currency": "USD"}')
        without = _price_feed(os.path.join(tmp, "a.json"), ticker="ACME", close=120.0)
        withmap = _price_feed(os.path.join(tmp, "b.json"), ticker="ACME", close=120.0,
                              splits=_NVDA_TEN_FOR_ONE)   # a split on a ticker not held
        first = _ok(_run("consider", csv_path, "--root", tmp, "--prices", without,
                         "--premise", premise))["evaluation"]
        second = _ok(_run("consider", csv_path, "--root", tmp, "--prices", withmap,
                          "--premise", premise))["evaluation"]
        assert first["basis"]["state_version"] == second["basis"]["state_version"], \
            "an inapplicable split map must not move the frozen basis identity"
        assert first["consequence"] == second["consequence"], "nor any computed number"


def test_the_ledger_route_takes_the_supplied_map_too():
    """Both routes resolve the map the same way, so the answer does not depend
    on which book happened to answer. Before this, the ledger route read only
    the map a previous review froze — a root that has never been reviewed had
    no way to be told, even by an agent holding the fact."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), [
            _trade_event("2023-01-10", "NVDA", "BUY", 90, 150.0),
            _trade_event("2023-11-15", "NVDA", "BUY", 30, 480.0),
            _trade_event("2024-05-20", "NVDA", "SELL", 20, 950.0),
            _trade_event("2026-07-28", "NVDA", "SELL", 100, 197.0)])
        told = _price_feed(os.path.join(tmp, "told.json"), splits=_NVDA_TEN_FOR_ONE)
        row = _ok(_run("consider", "--root", tmp, "--prices", told,
                       "--premise", _NVDA_PREMISE))["evaluation"]
        held = row["consequence"]["before"]["held"]
        assert "NVDA" in held and abs(held["NVDA"]["shares"] - 900.0) < 1e-6, held


def test_a_malformed_split_in_the_envelope_never_reaches_the_book():
    """Fail closed. A ratio is a multiplier on a share count, so a bad one
    silently dropped is a confident wrong number — the rule `splits.py` already
    holds, asserted here on the path that reaches a user."""
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = os.path.join(tmp, "trades.csv")
        _write_csv(csv_path, _SPLIT_CSV)
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as handle:
            json.dump({"as_of": "2026-07-29", "source": "broker", "prices": [
                {"ticker": "NVDA", "close": 197.0, "date": "2026-07-29", "currency": "USD",
                 "splits": [["2024-06-10", "not-a-ratio"]]}]}, handle)
        run = _run("consider", csv_path, "--root", tmp, "--prices", bad,
                   "--premise", _NVDA_PREMISE)
        assert run.returncode != 0, f"a bad ratio must refuse, not round down to 1: {run.stdout}"
        assert not _read_evaluations(tmp), "nothing may be recorded from a refused envelope"


# ─────────────────────────────── runner ───────────────────────────────

# ───────────── M. the visible challenge (#479 Wave B, second cut) ─────────────
#
# Wave B's first cut made a fabricated `--agent-case` unstorable; nothing in
# it reached the user. This section is the other half: `consider` now emits
# `challenge`, the engine's own statement of what this answer owes -- which
# facts, whose exact words, which of the user's own rules, which limitations,
# and what nobody looked at.
#
# `tests/test_evaluation_challenge.py` proves the builder in isolation. What
# is proved *here*, and only here, is that the block survives the real CLI:
# that it is emitted at all, that its anchors are usable against the frozen
# row a separate process wrote, and that the gate refuses a case leaving out
# what the block named. Deleting `"challenge": challenge` from cmd_consider's
# `_emit` turns this section red -- which is issue #479's own acceptance
# criterion ("removing the visible TradeEvaluation consumer makes an
# integration test and #488 receipt fail"); the #488 receipt half is
# #544 Slice B's formal `consider` route contract, not this file's.


CHALLENGE_SCHEMA = _schema("evaluation-challenge.schema.json")
_CHALLENGE_KEYS = set(CHALLENGE_SCHEMA["required"])


def _check_challenge_shape(challenge):
    """Spot-check a real emitted block against evaluation-challenge.schema.json
    -- the same manual-pin idiom `_check_evaluation_shape` uses for the
    sibling schema, because the offline suite carries no jsonschema
    dependency. A schema nothing validates against is documentation that
    can quietly stop describing the thing it names."""
    props = CHALLENGE_SCHEMA["properties"]
    assert set(challenge) == set(props), f"key set drifted: {sorted(challenge)}"

    entry_props = props["must_state"]["items"]["properties"]
    topic_enum = set(entry_props["topic"]["enum"])
    detail_allowed = set(entry_props["detail"]["properties"])
    for entry in challenge["must_state"]:
        assert set(entry) <= set(entry_props), f"undeclared entry field: {sorted(entry)}"
        assert set(props["must_state"]["items"]["required"]) <= set(entry)
        assert entry["topic"] in topic_enum
        assert isinstance(entry["value"], (str, int, float, bool))
        if "anchor" in entry:
            assert entry["anchor"].split(".")[0] in ("basis", "consequence", "rule_collisions")
        if "detail" in entry:
            assert set(entry["detail"]) <= detail_allowed

    quote_props = props["quote_verbatim"]["items"]["properties"]
    for quoted in challenge["quote_verbatim"]:
        assert set(quoted) == set(quote_props)
        assert quoted["field"] in ("reason", "why_now") or quoted["field"].startswith("evidence_refs[")
        assert isinstance(quoted["text"], str) and quoted["text"]

    unchecked_enum = set(props["unchecked"]["items"]["enum"])
    assert set(challenge["unchecked"]) <= unchecked_enum
    assert len(challenge["unchecked"]) >= props["unchecked"]["minItems"]
    assert len(set(challenge["unchecked"])) == len(challenge["unchecked"])

    assert set(challenge["case_required"]) == set(props["case_required"]["properties"])
    for side in ("for", "against"):
        assert challenge["case_required"][side] >= 1

    coverage_props = props["required_coverage"]["items"]["properties"]
    for required in challenge["required_coverage"]:
        assert set(required) == set(coverage_props)
        assert required["owes"] in set(coverage_props["owes"]["enum"])
        assert required["key"] in set(coverage_props["key"]["enum"])
        assert required["path"]


def _collision_root(tmp, cap_text="Cap any single position at 25%."):
    """A ledger-backed root holding three equal positions plus one tracked
    rule, so a further buy in one of them collides with a real rule of the
    user's own rather than with an empty rotation."""
    _write_ledger(os.path.join(tmp, "ledger.jsonl"), [
        _snapshot_event("2026-01-01", [
            {"ticker": t, "shares": 10, "avg_cost": 100.0, "market": "US", "currency": "USD"}
            for t in ("AAA", "BBB", "CCC")])])
    with open(os.path.join(tmp, "rules.jsonl"), "w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "type": "rule", "rule_id": "r1", "date": "2026-01-01", "text": cap_text,
            "metric_key": "max_pos_pct", "problem_key": "oversize",
            "status": "tracking"}) + "\n")
    return '{"ticker": "AAA", "side": "buy", "qty": 20, "price": 100.0, "currency": "USD"}'


def _claim_citing(entry, collisions):
    """A minimally-valid engine_fact claim for one must_state entry, built
    from the entry itself so a case assembled this way is literally 'what
    the challenge asked for' rather than a hand-written approximation."""
    value = entry["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        text = f"Stating the fact recorded at {entry['anchor']}."
    elif abs(value) <= 1.0:
        text = f"That reading is {value * 100:.1f}% of the book."
    else:
        text = f"That reading is {value}."
    claim = {"claim": text, "provenance": "engine_fact", "anchor": entry["anchor"]}
    parts = entry["anchor"].split(".")
    if parts[0] == "rule_collisions" and parts[-1] in ("state", "worsens"):
        row = next((r for r in collisions if r.get("rule_id") == parts[1]), None)
        if row and row.get("state") == "already_over" and row.get("worsens") is not None:
            claim["worsens"] = row["worsens"]
    return claim


def _case_from_challenge(challenge, collisions, skip=None):
    claims = []
    for required in challenge["required_coverage"]:
        if skip is not None and required["path"] == skip:
            continue
        entry = next((e for e in challenge["must_state"]
                      if "anchor" in e and (e["anchor"] == required["path"]
                                            or e["anchor"].startswith(required["path"] + "."))),
                     None)
        assert entry is not None, (
            f"the block requires {required['path']} but states no citable fact under it, "
            "so no case can be built that clears the gate")
        claims.append(_claim_citing(entry, collisions))
    return {"for": [{"claim": "Conviction is intact.", "provenance": "agent_judgment"}],
            "against": claims}


def test_a_considered_trade_puts_the_whole_challenge_in_front_of_the_caller():
    """The consumer-removal test. `consider`'s answer is plain conversation,
    so the only thing standing between the engine's frozen result and a user
    told half of it is this block. Every key must arrive populated."""
    with tempfile.TemporaryDirectory() as tmp:
        premise = _collision_root(tmp)
        payload = _ok(_run("consider", "--root", tmp, "--premise", premise))

        assert "challenge" in payload, (
            "consider computed the consequence and told the user nothing about what "
            "the answer owes -- the visible half of #479 Wave B is gone")
        challenge = payload["challenge"]
        assert set(challenge) == _CHALLENGE_KEYS, (
            f"challenge key set drifted: {sorted(challenge)}")
        _check_challenge_shape(challenge)

        topics = {e["topic"] for e in challenge["must_state"]}
        for owed in ("basis", "position", "concentration", "rule_collision", "disclosure"):
            assert owed in topics, f"nothing in must_state states the {owed}"
        assert challenge["unchecked"], "an answer that names no unchecked risk reads as a clean bill"
        assert challenge["case_required"] == {"for": 1, "against": 1}
        assert challenge["required_coverage"], (
            "this book is stale and carries disclosures; the case owes something")


def test_the_challenge_names_the_rule_this_trade_collides_with():
    """The gap this cut actually closes on the user's side. Before it, a
    collision with a rule the user wrote themselves lived in the payload and
    nothing said it had to be spoken."""
    with tempfile.TemporaryDirectory() as tmp:
        premise = _collision_root(tmp)
        payload = _ok(_run("consider", "--root", tmp, "--premise", premise))
        rows = [e for e in payload["challenge"]["must_state"]
                if e["topic"] == "rule_collision"]
        assert rows, "a colliding rule must be named among the owed facts"
        assert rows[0]["detail"]["text"] == "Cap any single position at 25%.", (
            "the rule's own words must ride along so the answer can quote rather than paraphrase")
        assert any(r["owes"] == "rule_collision" for r in payload["challenge"]["required_coverage"])


def test_a_case_silent_about_a_collided_rule_is_refused_on_the_production_path():
    """The same obligation, enforced. A case that covers every disclosure and
    the stale basis but never mentions the rule this trade is over is refused
    before anything is stored -- silence about a broken rule reads to the user
    as a rule that held."""
    with tempfile.TemporaryDirectory() as tmp:
        premise = _collision_root(tmp)
        payload = _ok(_run("consider", "--root", tmp, "--premise", premise))
        challenge, collisions = payload["challenge"], payload["evaluation"]["rule_collisions"]
        rule_path = next(r["path"] for r in challenge["required_coverage"]
                         if r["owes"] == "rule_collision")

        case_path = os.path.join(tmp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump(_case_from_challenge(challenge, collisions, skip=rule_path), f)
        run = _run("consider", "--root", tmp, "--premise", premise, "--agent-case", case_path)
        _fails(run, rule_path)
        assert len(_read_evaluations(tmp)) == 1, (
            "the refused attempt appended a row; the gate must fail closed")


def test_a_case_built_from_the_challenge_is_accepted_end_to_end():
    """The anchor guarantee, across a process boundary. Every anchor the
    block hands over has to be one the gate accepts against the row a
    separate `consider` invocation froze -- if the two ever disagreed, the
    product would be telling the agent to cite paths it then refuses."""
    with tempfile.TemporaryDirectory() as tmp:
        premise = _collision_root(tmp)
        payload = _ok(_run("consider", "--root", tmp, "--premise", premise))
        challenge, collisions = payload["challenge"], payload["evaluation"]["rule_collisions"]

        case_path = os.path.join(tmp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump(_case_from_challenge(challenge, collisions), f)
        second = _ok(_run("consider", "--root", tmp, "--premise", premise,
                          "--agent-case", case_path))
        assert "agent_case" in second["evaluation"]
        assert second["evaluation"]["evaluation_id"] == payload["evaluation"]["evaluation_id"], (
            "agent_case is not in the identity seed, so attaching one must converge on "
            "the same evaluation rather than minting a second")


def test_the_users_exact_words_come_back_in_the_challenge():
    """The one part of the answer that may not be reworded. `quote_verbatim`
    is what makes 'in their own words' an obligation the answer carries
    rather than a property of the stored row nobody has to read back."""
    context = {"reason": "It is still my highest-conviction name.",
               "why_now": "Their main supplier raised capacity guidance this morning.",
               "evidence_refs": ["Supplier capacity guidance, this morning"]}
    with tempfile.TemporaryDirectory() as tmp:
        payload = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                           "--premise", '{"ticker": "NVDA", "side": "buy", '
                                        '"price": 130.0, "qty": 5}',
                           "--decision-context", json.dumps(context)))
        _check_challenge_shape(payload["challenge"])
        quoted = {q["field"]: q["text"] for q in payload["challenge"]["quote_verbatim"]}
        assert quoted["reason"] == context["reason"]
        assert quoted["why_now"] == context["why_now"]
        assert quoted["evidence_refs[0]"] == context["evidence_refs"][0]
        assert "evidence_refs_unverified" in payload["challenge"]["unchecked"], (
            "the engine neither fetched nor dated the cited evidence and must say so")


def test_a_context_free_call_gets_a_challenge_too_and_stores_none_of_it():
    """Two invariants at once. A context-free `consider` is a complete use of
    the surface, so it owes the same disclosures -- and the block is derived
    from fields the row already freezes, so storing it would be a duplicate
    able to disagree with its own inputs (#429's written-never-read defect in
    the other direction)."""
    premise = '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}'
    with tempfile.TemporaryDirectory() as tmp:
        payload = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                           "--premise", premise))
        _check_challenge_shape(payload["challenge"])
        assert payload["challenge"]["quote_verbatim"] == []
        assert payload["challenge"]["required_coverage"], (
            "a context-free call owes its disclosures exactly the same")
        row = payload["evaluation"]
        assert "challenge" not in row, "the block is emitted, never stored"
        assert _read_evaluations(tmp) == [row]
        _check_evaluation_shape(row)

        # ... and re-asking converges on the same row rather than duplicating,
        # which is the property that would break first if the block had
        # slipped into the identity seed.
        again = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                         "--premise", premise))
        assert again["evaluation"]["evaluation_id"] == row["evaluation_id"]


def test_the_challenge_is_absent_from_a_resolution():
    """`--resolve` records what the user did with an evaluation already
    considered. There is no new answer being composed, so there is nothing
    owed -- emitting a challenge there would present a fresh obligation for a
    question that was already answered."""
    with tempfile.TemporaryDirectory() as tmp:
        created = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                           "--premise", '{"ticker": "NVDA", "side": "buy", '
                                        '"price": 130.0, "qty": 5}'))
        resolved = _ok(_run("consider", "--root", tmp, "--decision", "acted",
                            "--resolve", created["evaluation"]["evaluation_id"]))
        assert "challenge" not in resolved


# ───── M2. which market session the answer was valued at (#618) ─────
#
# Before #611 a `consider` weight was a share of cost, or of whatever the last
# review froze, so the same premise re-asked returned the same weights. It is
# now a share of the current close — which is the point, and which makes every
# number a function of a market day the row never named. A user who asks twice
# could see that the numbers moved with no way to tell whether the market moved
# or their own book did.
#
# What is proved here, and only here, is that the fact survives the real CLI in
# both directions: an offline run grows no date at all, and a priced run freezes
# one the block then names and the gate then enforces.

_PRICE_DAY = "2026-07-30"
_EARLIER_DAY = "2026-07-29"


def _priced_collision_root(tmp):
    """`_collision_root`'s book (AAA/BBB/CCC) with a supplied envelope that
    prices it on a deliberately MIXED frame: two instruments on the frame's
    own newest session, one a day earlier, and one close for an instrument the
    book has never held — which must not be dated, because no number in this
    answer used it."""
    premise = _collision_root(tmp)
    feed = os.path.join(tmp, "px.json")
    rows = [("AAA", 130.0, _PRICE_DAY), ("BBB", 90.0, _PRICE_DAY),
            ("CCC", 110.0, _EARLIER_DAY), ("ZZZ", 44.0, _PRICE_DAY)]
    with open(feed, "w", encoding="utf-8") as handle:
        json.dump({"as_of": _PRICE_DAY, "source": "fixture",
                   "prices": [{"ticker": t, "close": c, "date": d, "currency": "USD"}
                              for t, c, d in rows]}, handle)
    return premise, feed


def test_an_offline_answer_carries_no_price_day_at_all():
    """The hard rule of #618, on the production path. Nothing retrieved means
    nothing observed, and an answer that manufactures a date there — a null, a
    placeholder, or today's — would tell the user the numbers came from a
    market session that never priced this book."""
    with tempfile.TemporaryDirectory() as tmp:
        premise = _collision_root(tmp)
        payload = _ok(_run_env(_offline_env(), "consider", "--root", tmp, "--premise", premise))
        basis = payload["evaluation"]["basis"]
        assert basis["valuation_basis"] == "unpriced"
        assert "price_observations" not in basis, (
            f"an offline consider grew a price observation: {basis['price_observations']}")
        _check_evaluation_shape(payload["evaluation"])

        challenge = payload["challenge"]
        assert not [e for e in challenge["must_state"] if e["topic"] == "price_basis"], (
            "an unpriced answer was told to state a market session it never had")
        assert not [r for r in challenge["required_coverage"] if r["owes"] == "price_basis"], (
            "an unpriced answer cannot cite a price day, so it must not be refused for not citing one")
        _check_challenge_shape(challenge)


def test_a_priced_answer_freezes_the_session_each_number_was_valued_at():
    with tempfile.TemporaryDirectory() as tmp:
        premise, feed = _priced_collision_root(tmp)
        payload = _ok(_run_env(_offline_env(), "consider", "--root", tmp,
                               "--prices", feed, "--premise", premise))
        basis = payload["evaluation"]["basis"]
        assert basis["valuation_basis"] == "priced"
        assert basis["price_observations"] == {
            "as_of": _PRICE_DAY,
            "by_ticker": {"AAA": _PRICE_DAY, "BBB": _PRICE_DAY, "CCC": _EARLIER_DAY}}, (
            "the frozen observations must be per instrument, scoped to what this answer "
            f"needed priced, and summarized by their own newest: {basis['price_observations']}")
        _check_evaluation_shape(payload["evaluation"])
        assert _read_evaluations(tmp)[0]["basis"] == basis, (
            "the stored row must carry the same observations the caller was handed")


def test_the_priced_answer_owes_the_session_and_names_the_instrument_off_it():
    with tempfile.TemporaryDirectory() as tmp:
        premise, feed = _priced_collision_root(tmp)
        challenge = _ok(_run_env(_offline_env(), "consider", "--root", tmp,
                                 "--prices", feed, "--premise", premise))["challenge"]
        _check_challenge_shape(challenge)
        entries = [e for e in challenge["must_state"] if e["topic"] == "price_basis"]
        assert [e["value"] for e in entries] == [_PRICE_DAY, _EARLIER_DAY], (
            f"the frame session and the one instrument off it are both owed: {entries}")
        assert entries[0]["anchor"] == "basis.price_observations.as_of"
        assert entries[1]["detail"] == {"ticker": "CCC"}, (
            "the instrument the frame summary does not describe must be named")
        assert any(r["owes"] == "price_basis" and r["path"] == "basis.price_observations"
                   for r in challenge["required_coverage"]), (
            "stating the price day is enforced, not merely announced")


def test_a_case_silent_about_the_price_day_is_refused_on_the_production_path():
    """The other direction of #479 Wave B's one-list rule, for #618's fact:
    what the block says is owed is what a case is refused for dropping."""
    with tempfile.TemporaryDirectory() as tmp:
        premise, feed = _priced_collision_root(tmp)
        payload = _ok(_run_env(_offline_env(), "consider", "--root", tmp,
                               "--prices", feed, "--premise", premise))
        challenge, collisions = payload["challenge"], payload["evaluation"]["rule_collisions"]

        case_path = os.path.join(tmp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump(_case_from_challenge(challenge, collisions,
                                           skip="basis.price_observations"), f)
        run = _run_env(_offline_env(), "consider", "--root", tmp, "--prices", feed,
                       "--premise", premise, "--agent-case", case_path)
        _fails(run, "basis.price_observations")
        assert len(_read_evaluations(tmp)) == 1, (
            "the refused attempt appended a row; the gate must fail closed")

        # ... and the same case with the price day put back is accepted, so
        # the refusal above is about that one fact and not about the shape of
        # a case built this way.
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump(_case_from_challenge(challenge, collisions), f)
        accepted = _ok(_run_env(_offline_env(), "consider", "--root", tmp, "--prices", feed,
                                "--premise", premise, "--agent-case", case_path))
        assert "agent_case" in accepted["evaluation"]


# ───── N. automatic market-data resolution (#605 section E) ─────
#
# `consider` may now retrieve current market facts when no `--prices` is handed
# over: it used to reason about the book at whatever the last review froze, and to
# refuse a mixed-currency book outright for want of a rate (#602). Everything an
# offline suite can say about that lives here; the live half — that a resolved run
# really values the book at today's prices and that a same-day `prepare` bundle
# satisfies a later `consider` with zero provider calls — needs a provider, and is
# what `TR_TEST_NETWORK=1` plus `tests/test_market_data.py`'s witness cover.

def _offline_env():
    env = dict(os.environ)
    env["TR_OFFLINE"] = "1"
    return env


def _run_env(env, *args):
    return subprocess.run([sys.executable, str(REVIEW), *map(str, args)], cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=120)


def test_n_offline_resolution_leaves_the_answer_exactly_as_it_was():
    """The regression guard this whole feature rests on.

    Ninety-odd tests in this file drive `consider` with no `--prices`, and every
    one of them is only still meaningful because an offline resolution degrades to
    *exactly* the pre-#605 answer — same evaluation_id, same frozen consequence,
    byte for byte. If it did not, the entire suite would be silently re-baselined
    by a retrieval change, which is the shape of green-suite lie this repository
    keeps paying for.
    """
    premise = '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}'
    with tempfile.TemporaryDirectory() as tmp:
        first = _ok(_run_env(_offline_env(), "consider", str(MOCK / "sample_momentum.csv"),
                             "--root", tmp, "--premise", premise))
    with tempfile.TemporaryDirectory() as tmp:
        again = _ok(_run_env(_offline_env(), "consider", str(MOCK / "sample_momentum.csv"),
                             "--root", tmp, "--premise", premise))
    assert first["evaluation"]["evaluation_id"] == again["evaluation"]["evaluation_id"]
    assert first["evaluation"]["consequence"] == again["evaluation"]["consequence"], (
        "an offline consider must be deterministic; if retrieval leaks into it the frozen "
        "consequence moves with the market and every stored evaluation stops being replayable")
    assert first["evaluation"]["basis"]["valuation_basis"] == "unpriced", (
        "with nothing retrieved the book must read as unpriced, which is exactly what a pre-#605 "
        f"consider produced — and is the contrast the live run earns 'priced' against: "
        f"{first['evaluation']['basis']}")


def test_n_the_supplied_lane_still_answers_from_the_envelope_alone():
    """`--prices` is authoritative for what it declares, and the new resolution
    branch must not have disturbed it.

    The zero-provider-call half of that claim needs a call counter and is asserted
    in `tests/test_market_data.py`
    (`test_f_a_supplied_envelope_is_never_served_from_a_yahoo_cache_entry`); what
    this one adds is the route-level fact that the supplied close is the one the
    answer used, with no offline posture forcing the outcome.
    """
    with tempfile.TemporaryDirectory() as tmp:
        feed = os.path.join(tmp, "px.json")
        with open(feed, "w", encoding="utf-8") as handle:
            json.dump({"as_of": "2026-07-29", "source": "broker", "prices": [
                {"ticker": "NVDA", "close": 111.0, "date": "2026-07-29", "currency": "USD"}]},
                handle)
        # No TR_OFFLINE: if the supplied lane resolved anything, this would fetch.
        env = dict(os.environ)
        env.pop("TR_OFFLINE", None)
        payload = _ok(_run_env(env, "consider", str(MOCK / "sample_momentum.csv"),
                               "--root", tmp, "--prices", feed,
                               "--premise", '{"ticker": "NVDA", "side": "buy", '
                                            '"price": 130.0, "qty": 5}'))
        assert payload["evaluation"]["basis"]["source"] == "transactions"
        assert payload["evaluation"]["basis"]["valuation_basis"] == "priced", (
            "the supplied envelope must still price the book: "
            f"{payload['evaluation']['basis']}")
        assert payload["evaluation"]["consequence"]["after"]["held"], payload


def test_n_the_resolver_is_asked_for_the_premise_ticker_and_the_books_own_origin():
    """What the request must contain, checked without a provider.

    Two facts the request cannot get from the book (the book does not exist yet):
    the premise's ticker, which may be an instrument the user has never held, and
    the oldest date a consumer will rebase a split from — the ledger anchor on that
    route, which routinely predates every trade in any CSV.
    """
    sys.path.insert(0, str(ENGINE_DIR))
    import review as review_engine

    class Args:
        paths = [str(MOCK / "sample_momentum.csv")]

    with tempfile.TemporaryDirectory() as tmp:
        universe = review_engine._consider_market_universe(Args(), tmp)
        assert universe is not None, "a CSV book must yield a universe"
        assert "NVDA" in universe["currency_by_ticker"]
        assert universe["currencies"] == {"USD"}
        assert universe["origin"] <= min(universe["origin"], "2030-01-01")
        import trade_recap as trade_recap_engine
        rows = trade_recap_engine.load([str(MOCK / "sample_momentum.csv")])
        assert universe["origin"] == min(str(row["date"]) for row in rows), (
            "the origin must be the oldest date in the source, since that is the oldest date a "
            f"split gets rebased from: {universe['origin']}")


_FAKE_PROVIDER = '''
# Injected as usercustomize (never sitecustomize — Homebrew ships its own and
# shadowing it removes site-packages) so the real `review.py consider` subprocess
# runs against a deterministic provider. This is what makes the resolution route
# testable offline at all.
import datetime as dt
import os
import sys
sys.path.insert(0, os.environ["ENGINE_DIR"])
CLOSES = {"NVDA": 1000.0, "AMD": 200.0, "AVGO": 300.0, "MU": 50.0, "TSM": 100.0,
          "ARM": 60.0, "PLTR": 40.0, "MRVL": 70.0, "AMAT": 80.0, "SPY": 500.0,
          "ZZZZ": 10.0}


def _fake_download(symbols, start, end=None):
    import pandas as pd
    open(os.environ["PROVIDER_LOG"], "a").write(",".join(sorted(symbols)) + "\\n")
    index = pd.DatetimeIndex([dt.datetime(2026, 7, 29)])
    data = {}
    for symbol in symbols:
        data[("Close", symbol)] = [CLOSES.get(symbol, 123.0)]
        data[("Stock Splits", symbol)] = [float("nan")]
    frame = pd.DataFrame(data, index=index)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    return frame


import market_data
market_data._download = _fake_download
# The other half of the provider seam. Without it `_from_yahoo` answers
# `provider_missing` above the fake and never calls it, which is what every CI
# runner does -- they install no yfinance on purpose (#621). Safe to assign
# outright here, unlike in-process: this runs in a throwaway subprocess.
market_data._provider_available = lambda: True
'''


def _with_fake_provider(tmp, *args):
    """Run the real CLI against a deterministic provider, and report what it asked
    for. TR_OFFLINE is removed on purpose: the point is to exercise the resolution
    path, not the degradation."""
    sitedir = os.path.join(tmp, "provider-site")
    os.makedirs(sitedir, exist_ok=True)
    with open(os.path.join(sitedir, "usercustomize.py"), "w", encoding="utf-8") as handle:
        handle.write(_FAKE_PROVIDER)
    log = os.path.join(tmp, "provider.log")
    env = dict(os.environ)
    env.pop("TR_OFFLINE", None)
    env["ENGINE_DIR"] = str(ENGINE_DIR)
    env["PROVIDER_LOG"] = log
    env["PYTHONPATH"] = os.pathsep.join(
        [sitedir, str(ENGINE_DIR), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    run = _run_env(env, *args)
    try:
        with open(log, encoding="utf-8") as handle:
            asked = [line.strip().split(",") for line in handle if line.strip()]
    except OSError:
        asked = []
    return run, asked


def test_n_the_route_really_resolves_and_prices_the_book_it_reasons_about():
    """The route-level gate, and the one that survives a severed call site.

    `test_n_the_resolved_facts_reach_the_answer_through_the_supplied_lane` below
    drives `_resolve_consider_prices` directly, so removing the call from
    `cmd_consider` leaves it green — exactly the call-graph escape that got past
    the split gates three times in one day (#572, #576, #577). This drives the real
    CLI against an injected provider instead, so the observable is what the user
    gets: a book valued at retrieved prices rather than an unpriced one.
    """
    premise = '{"ticker": "NVDA", "side": "buy", "price": 1000.0, "qty": 5}'
    with tempfile.TemporaryDirectory() as tmp:
        run, asked = _with_fake_provider(
            tmp, "consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
            "--premise", premise)
        payload = _ok(run)
        assert asked, "the route made no provider request at all — it is not resolving"
        assert len(asked) == 1, f"one resolve per call, got {len(asked)}: {asked}"
        assert "NVDA" in asked[0]
        basis = payload["evaluation"]["basis"]
        assert basis["valuation_basis"] == "priced", (
            "the answer must be computed over a book valued at the retrieved prices; unpriced "
            f"means the resolution never reached it: {basis}")
        after = payload["evaluation"]["consequence"]["after"]
        assert after.get("ai_pct") is not None, after
        # And the retrieved close is the one that got used: NVDA at 1000 makes it
        # the dominant position in this book, which cost basis alone would not.
        assert after["max_ticker"] == "NVDA", (
            "the resolved price must be what the weights were computed from — at 1000 a share "
            f"NVDA dominates this book, which its cost basis alone would not: "
            f"max_ticker={after.get('max_ticker')} weights={after.get('weights')}")


def test_n_a_resolved_run_and_an_equivalent_supplied_run_agree(  # noqa: D401
):
    """#605's acceptance criterion: a supplied run and a resolved run produce the
    same normalized valuation facts. Holds by construction — both enter through
    `price_feed.parse` — and is pinned here because "by construction" stops being
    true the moment someone adds a second lane."""
    premise = '{"ticker": "NVDA", "side": "buy", "price": 1000.0, "qty": 5}'
    with tempfile.TemporaryDirectory() as tmp:
        resolved = _ok(_with_fake_provider(
            tmp, "consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
            "--premise", premise)[0])
    with tempfile.TemporaryDirectory() as tmp:
        # The same closes, handed over instead of retrieved.
        closes = {"NVDA": 1000.0, "AMD": 200.0, "AVGO": 300.0, "MU": 50.0, "TSM": 100.0,
                  "ARM": 60.0, "PLTR": 40.0, "MRVL": 70.0, "AMAT": 80.0}
        feed = os.path.join(tmp, "px.json")
        with open(feed, "w", encoding="utf-8") as handle:
            json.dump({"as_of": "2026-07-29", "source": "yahoo (engine resolver)",
                       "prices": [{"ticker": t, "close": c, "date": "2026-07-29",
                                   "currency": "USD"} for t, c in sorted(closes.items())]},
                      handle)
        supplied = _ok(_run_env(_offline_env(), "consider",
                                str(MOCK / "sample_momentum.csv"), "--root", tmp,
                                "--prices", feed, "--premise", premise))
    left = resolved["evaluation"]["consequence"]
    right = supplied["evaluation"]["consequence"]
    for key in ("before", "after"):
        assert left[key] == right[key], (
            f"resolved and supplied must produce identical {key} facts, or there are two lanes "
            f"where the design allows one:\n  resolved={left[key]}\n  supplied={right[key]}")


def test_n_the_resolved_facts_reach_the_answer_through_the_supplied_lane():
    """The wiring itself, gated offline with a stubbed resolver.

    Without this the only proof that `consider` resolves at all needs a live
    provider, so a change that quietly stopped resolving would leave the whole
    offline suite green — the supply-side blind spot that has shipped in this
    repository three times. The stub also pins the *shape* of the handoff: what
    comes back is a parsed `price_feed`, because the resolved facts are required to
    enter through the lane that already reconciles currencies, checks the split
    basis and builds the valuation manifest.
    """
    sys.path.insert(0, str(ENGINE_DIR))
    import market_data
    import review as review_engine

    class Args:
        paths = [str(MOCK / "sample_momentum.csv")]

    captured = {}

    def fake_resolve(request, **kwargs):
        captured["request"] = request
        frame_rows = {t: 100.0 for t in request["instruments"]}
        import pandas as pd
        frame = pd.DataFrame([list(frame_rows.values())], columns=list(frame_rows),
                             index=pd.DatetimeIndex([dt.datetime(2026, 7, 29)]))
        return market_data.MarketDataBundle(source="yahoo", request=request, frame=frame,
                                            splits={}, fx={"USD": 1.0})

    real = market_data.resolve
    market_data.resolve = fake_resolve
    try:
        with tempfile.TemporaryDirectory() as tmp:
            feed, bundle = review_engine._resolve_consider_prices(
                Args(), tmp, premise_ticker="ZZZZ")
    finally:
        market_data.resolve = real

    assert captured, "consider must actually ask the resolver"
    assert "ZZZZ" in captured["request"]["instruments"], (
        "the premise's own ticker must be requested — it may be an instrument the user has never "
        f"held, and it is the one this question is about: {captured['request']['instruments']}")
    assert feed is not None and "ZZZZ" in feed["prices"], (
        f"the resolved facts must come back as a parsed price feed: {feed}")
    assert bundle is not None and bundle.source == "yahoo"


def test_n_a_non_usd_premise_gets_its_own_currency_requested():
    """A premise may name an instrument the book has never held, in a currency the
    book has never held either — a USD-only book asked about a TWD listing.

    Adding the ticker without its currency requested no rate for it and declared
    the row as USD, so automatic retrieval could not complete an otherwise valid
    non-USD question (external review, finding 8). The downstream refusal was
    correct; it was refusing something the provider could have answered.
    """
    sys.path.insert(0, str(ENGINE_DIR))
    import market_data
    import review as review_engine

    class Args:
        paths = [str(MOCK / "sample_momentum.csv")]

    captured = {}

    def fake_resolve(request, **kwargs):
        captured["request"] = request
        import pandas as pd
        cols = {t: 100.0 for t in request["instruments"]}
        cols.update({market_data.fx_symbol(c): 30.0 for c in request["currencies"]})
        frame = pd.DataFrame([list(cols.values())], columns=list(cols),
                             index=pd.DatetimeIndex([dt.datetime(2026, 7, 29)]))
        fx = {"USD": 1.0}
        fx.update({c: round(1.0 / 30.0, 6) for c in request["currencies"]})
        return market_data.MarketDataBundle(source="yahoo", request=request, frame=frame,
                                            splits={}, fx=fx)

    real = market_data.resolve
    market_data.resolve = fake_resolve
    try:
        with tempfile.TemporaryDirectory() as tmp:
            feed, _bundle = review_engine._resolve_consider_prices(
                Args(), tmp, premise_ticker="2330.TW", premise_currency="TWD")
    finally:
        market_data.resolve = real

    assert "TWD" in captured["request"]["currencies"], (
        "the premise's currency must be requested, or no rate is ever retrieved for it: "
        f"{captured['request']['currencies']}")
    assert feed is not None
    assert feed["prices"]["2330.TW"]["currency"] == "TWD", (
        "and the envelope must declare that currency rather than defaulting it to USD, which is "
        f"what `price_feed.currency_conflicts` compares against the trades: {feed['prices']}")
    assert price_feed_engine.fx_rates(feed).get("TWD"), feed


def test_n_an_empty_root_yields_no_universe_rather_than_a_bad_request():
    """Nothing to ask about is not an error at this layer — `_consider_rows`'
    refusals own it and say something more useful."""
    sys.path.insert(0, str(ENGINE_DIR))
    import review as review_engine

    class Args:
        paths = []

    with tempfile.TemporaryDirectory() as tmp:
        assert review_engine._consider_market_universe(Args(), tmp) is None


def test_n_resolve_market_data_refuses_a_request_it_cannot_trust():
    with tempfile.TemporaryDirectory() as tmp:
        _fails(_run_env(_offline_env(), "resolve-market-data", "--root", tmp,
                        "--request", '{"instruments": ["NVDA"], "window_start": "2026-06-01", '
                                     '"rebase_origin": "2020-01-01"}'),
               "rebase_origin")
        _fails(_run_env(_offline_env(), "resolve-market-data", "--root", tmp,
                        "--request", '{"instruments": [], "window_start": "2026-06-01"}'),
               "market-data request")


def test_n_resolve_market_data_writes_no_state_of_its_own():
    """It is an acquisition entry point, not a lifecycle one: no session, no
    ledger, no evaluation row. `coach.DATA_FILES` already covers the one thing it
    does touch, the shared same-day cache."""
    with tempfile.TemporaryDirectory() as tmp:
        run = _run_env(_offline_env(), "resolve-market-data", "--root", tmp,
                       "--request", '{"instruments": ["NVDA"], "window_start": "2026-06-01"}')
        payload = _ok(run)
        assert payload["envelope"] is None and payload["source"] == "unavailable", payload
        assert [g["code"] for g in payload["gaps"]] == ["network_disabled"]
        left = sorted(os.listdir(tmp))
        assert left == [] or left == ["cache"], (
            f"resolve-market-data must write no lifecycle state, found: {left}")


# ───── O. price recovery on a forward-looking decision (#629) ─────
#
# When the provider is unavailable, `prepare` has always returned a complete
# recovery kit and `consider` returned one enum value -- `valuation_basis:
# "unpriced"` -- naming nothing, while `consider --prices` already existed.
# The cost of that silence is measured below: on the momentum fixture the
# largest position reads 59.8% of the book on cost and 45.9% at market, and the
# second and third positions by size swap places. Concentration is what
# `consider` exists to answer, so on this book it answered the decision question
# backwards.
#
# Everything here is offline and deterministic. `_MOMENTUM_CLOSES` is a fixture,
# not a live quote: the point is that the same book answers differently once
# *any* current prices reach it, and pinning the numbers is what makes the
# difference assertable at all.

_MOMENTUM_CLOSES = {"NVDA": 193.925, "AMD": 487.875, "MRVL": 182.14}
_MOMENTUM_PREMISE = '{"ticker": "NVDA", "side": "buy", "price": 196.6, "qty": 50}'


def _momentum_envelope(path, as_of="2026-07-30"):
    """The `--prices` envelope a completed recovery hands back."""
    payload = {"as_of": as_of, "source": "fixture closes for a deterministic test",
               "prices": [{"ticker": ticker, "close": close, "date": as_of,
                           "currency": "USD"}
                          for ticker, close in sorted(_MOMENTUM_CLOSES.items())]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return path


def _momentum_consider(tmp, *extra):
    return _ok(_run_env(_offline_env(), "consider", str(MOCK / "sample_momentum.csv"),
                        "--root", tmp, "--premise", _MOMENTUM_PREMISE, *extra))


def test_o_an_unpriced_consider_emits_the_recovery_kit_naming_what_is_missing():
    """The gap this closes. Before it there was nothing to assert: the answer
    carried `valuation_basis: "unpriced"` and no missing-instrument list, no
    envelope pointer and no instruction, so a host had no way to learn that
    recovery was the move rather than relaying a cost-weighted answer."""
    with tempfile.TemporaryDirectory() as tmp:
        payload = _momentum_consider(tmp)
    assert payload["evaluation"]["basis"]["valuation_basis"] == "unpriced"
    kit = payload["price_feed"]
    assert kit["provenance"]["mode"] == "unavailable", kit["provenance"]
    assert kit["request"]["tickers"] == ["AMD", "MRVL", "NVDA"], (
        "the manifest must name the instruments this answer needs priced -- the held book "
        f"plus the premise ticker -- not every ticker in the file: {kit['request']}")
    assert kit["request"]["envelope"] == "references/price-feed.md"
    assert kit["recovery"] == {"attempted": False, "outcome": "not_attempted"}
    assert "consider with --prices <path>" in kit["next_action"], kit["next_action"]
    assert "--prices-unavailable" in kit["next_action"], (
        "the kit must also name the honest dead end, or a host that looked and found nothing "
        "has no stated move except the cost-basis answer this issue removes")


def test_o_the_recovery_kit_is_the_prepare_builder_rather_than_a_second_one():
    """Anti-fork gate, and the reason this is worth a test of its own: a
    hand-written second manifest is the mirrored surface
    docs/maintainer-guide.md forbids, and two copies of an instruction drift on
    the first edit to either. `prepare`'s wording was already correct, so
    `consider`'s must be that same string with only the two things that
    genuinely differ substituted -- which subcommand takes the envelope back,
    and where the caller finds the manifest in the payload it is holding.
    """
    with tempfile.TemporaryDirectory() as tmp:
        plan = _ok(_run_env(_offline_env(), "prepare", str(MOCK / "sample_momentum.csv"),
                            "--root", tmp, "--language", "en"))
        prepared = plan["review_plan"]["input"]["price_feed"]["next_action"]
    with tempfile.TemporaryDirectory() as tmp:
        considered = _momentum_consider(tmp)["price_feed"]["next_action"]
    rewritten = (prepared.replace("input.price_feed.request", "price_feed.request")
                 .replace("rerun prepare with", "rerun consider with"))
    assert considered.startswith(rewritten), (
        "the consider recovery kit is no longer the prepare builder's own sentence:\n"
        f"  prepare (rewritten): {rewritten!r}\n  consider:            {considered!r}")


def test_o_the_envelope_round_trip_prices_the_book_and_moves_the_answer():
    """The measured stake, as a fixture. Same book, same premise, the only
    difference being whether current prices reached the computation."""
    with tempfile.TemporaryDirectory() as tmp:
        on_cost = _momentum_consider(tmp)["evaluation"]
    with tempfile.TemporaryDirectory() as tmp:
        envelope = _momentum_envelope(os.path.join(tmp, "prices.json"))
        at_market = _momentum_consider(tmp, "--prices", envelope)["evaluation"]

    assert on_cost["basis"]["valuation_basis"] == "unpriced"
    assert at_market["basis"]["valuation_basis"] == "priced"

    cost_weights = on_cost["consequence"]["before"]["weights"]
    market_weights = at_market["consequence"]["before"]["weights"]
    assert set(cost_weights) == set(market_weights) == {"NVDA", "AMD", "MRVL"}

    # 1. The largest position moves far enough to cross an ordinary "no single
    #    position over half the book" rule in the wrong direction.
    assert on_cost["consequence"]["before"]["max_ticker"] == "NVDA"
    assert at_market["consequence"]["before"]["max_ticker"] == "NVDA"
    assert cost_weights["NVDA"] > 0.55, cost_weights
    assert market_weights["NVDA"] < 0.50, market_weights
    assert cost_weights["NVDA"] - market_weights["NVDA"] > 0.10, (
        "the fixture no longer reproduces the divergence this issue exists for: "
        f"cost {cost_weights['NVDA']} vs market {market_weights['NVDA']}")

    # 2. And the ranking beneath it inverts, which no disclosure about the basis
    #    would tell the user.
    assert cost_weights["MRVL"] > cost_weights["AMD"], cost_weights
    assert market_weights["AMD"] > market_weights["MRVL"], market_weights

    # A fully covered envelope leaves nothing outstanding: no manifest, no
    # recovery block, no instruction to act on.
    assert "cost_basis" in on_cost["consequence"]["disclosures"]
    assert "cost_basis" not in at_market["consequence"]["disclosures"]


def test_o_a_priced_answer_states_its_provenance_and_asks_for_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        envelope = _momentum_envelope(os.path.join(tmp, "prices.json"))
        kit = _momentum_consider(tmp, "--prices", envelope)["price_feed"]
    assert kit["provenance"]["mode"] == "agent_feed", kit["provenance"]
    assert kit["provenance"]["coverage"]["missing"] == []
    assert "request" not in kit and "recovery" not in kit and "next_action" not in kit, (
        f"a fully priced answer has nothing outstanding to state: {kit}")


def test_o_a_declared_dead_end_refuses_rather_than_answering_on_cost_basis():
    """The owner ruling this issue closed on (2026-07-31): recovery genuinely
    failing degrades to a refusal, never to a cost-basis answer. Deliberately
    the opposite of the review-card lane, where the identical declaration
    unlocks delivery of the degraded card -- a retrospective card's cost weights
    describe what the user actually paid, a forward concentration decision
    computed on cost describes a book that no longer exists.
    """
    with tempfile.TemporaryDirectory() as tmp:
        run = _run_env(_offline_env(), "consider", str(MOCK / "sample_momentum.csv"),
                       "--root", tmp, "--premise", _MOMENTUM_PREMISE,
                       "--prices-unavailable",
                       "the exchange's own market-data site publishes no close for these")
        payload = _fails(run, "refused rather than answered on cost basis")
        assert "consequence" not in json.dumps(payload), payload
        assert not os.path.exists(_evaluation_path(tmp)), (
            "a refused question must leave no evaluation row behind")


def test_o_a_declared_dead_end_over_a_priced_book_still_answers():
    """The refusal is scoped to the harm, not to the flag. A declaration made
    beside prices that did arrive has nothing to refuse."""
    with tempfile.TemporaryDirectory() as tmp:
        envelope = _momentum_envelope(os.path.join(tmp, "prices.json"))
        payload = _momentum_consider(tmp, "--prices", envelope,
                                     "--prices-unavailable", "checked, then found them anyway")
    assert payload["evaluation"]["basis"]["valuation_basis"] == "priced"


def test_o_a_dead_end_declaration_must_name_the_sources_checked():
    """The same single validator `prepare` uses (#623): the escape hatch is a
    declaration, so it has to say something. Two commands accept the flag and a
    second copy of this rule would let one accept a claim the other refuses."""
    with tempfile.TemporaryDirectory() as tmp:
        _fails(_run_env(_offline_env(), "consider", str(MOCK / "sample_momentum.csv"),
                        "--root", tmp, "--premise", _MOMENTUM_PREMISE,
                        "--prices-unavailable", "x"),
               "must name the market-data sources you checked")


def test_o_resolve_takes_no_dead_end_declaration():
    with tempfile.TemporaryDirectory() as tmp:
        _fails(_run_env(_offline_env(), "consider", "--root", tmp,
                        "--resolve", "ev_whatever", "--decision", "acted",
                        "--prices-unavailable", "the exchange's own market-data site"),
               "--prices-unavailable")


def _tests():
    return [(name, obj) for name, obj in sorted(globals().items())
            if name.startswith("test_") and callable(obj)]


if __name__ == "__main__":
    failed = 0
    for name, fn in _tests():
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 -- surface unexpected errors as failures, not crashes
            failed += 1
            print(f"ERROR {name}: {exc!r}")
    total = len(_tests())
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
