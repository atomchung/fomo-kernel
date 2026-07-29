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
    return {"type": "snapshot", "as_of": as_of, "source": "user_declared",
            "is_complete": True, "positions": positions}


def _trade_event(date, ticker, action, qty, price, market="US", currency="USD"):
    return {"type": "trade", "date": date, "ticker": ticker, "action": action,
            "qty": qty, "price": price, "market": market, "currency": currency}


def _schema(name):
    with open(SCHEMAS / name, encoding="utf-8") as f:
        return json.load(f)


EVALUATION_SCHEMA = _schema("trade-evaluation.schema.json")
PREMISE_SCHEMA = _schema("trade-premise.schema.json")
PLAN_SCHEMA = _schema("review-plan.schema.json")


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
    if "valuation_coverage" in row["basis"]:
        coverage_schema = basis_schema["properties"]["valuation_coverage"]
        coverage = row["basis"]["valuation_coverage"]
        assert set(coverage) == set(coverage_schema["properties"])
        assert coverage["scope"] in coverage_schema["properties"]["scope"]["enum"]
        assert coverage["currencies"] == sorted(coverage["currencies"])

    consequence_schema = EVALUATION_SCHEMA["properties"]["consequence"]
    assert set(consequence_schema["required"]) <= set(row["consequence"])
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

    if "agent_case" in row:
        case_schema = EVALUATION_SCHEMA["properties"]["agent_case"]
        assert set(case_schema["required"]) <= set(row["agent_case"])
        provenances = set(EVALUATION_SCHEMA["$defs"]["claim"]["properties"]["provenance"]["enum"])
        for side in ("for", "against"):
            for claim in row["agent_case"][side]:
                assert set(claim) == {"claim", "provenance"}
                assert claim["provenance"] in provenances


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
        "holdings": {"as_of": date_end, "derived_from": "trades_csv", "is_complete": False,
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


def test_consider_succeeds_on_a_declared_partial_or_unverified_current_book():
    """#485: how a book was declared -- a snapshot the user marked partial,
    or a book replayed purely from trade history with no snapshot at all --
    must never gate whether `consider` can compute a verdict; only a
    genuinely missing fact does (see the fail-closed proof further below).
    Mirrors test_consider_uses_portfolio_basis_cost_for_multi_lot_partial_
    sell's rigor: compare the CLI's own numbers against a basis/projection
    built directly from the same events, not merely "it did not error".

    The `declared_partial` case cannot be a bare partial snapshot on its
    own: `ledger.latest_anchor` never treats a partial snapshot as an
    anchor ("review evidence, not a replacement for the complete account"),
    so a ledger holding nothing else would legitimately fail the *empty
    holdings* gate below -- a real fact gate, untouched by this change.
    Keeping an old partial snapshot alongside a later complete one exercises
    `declared_partial` (`_partial_snapshot_present` looks at all history,
    not just the anchor) while still leaving real holdings to assert on.
    """
    cases = [
        ("declared_partial",
         [{**_snapshot_event("2025-06-01", []), "is_complete": False},
          _snapshot_event("2026-01-01", [{"ticker": "NVDA", "shares": 10, "avg_cost": 100.0,
                                          "market": "US", "currency": "USD"}])]),
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
    other fixture in this file seeds the ledger through
    `_snapshot_event(..., is_complete=True)`, which is exactly why a
    completeness gate on `rows_from_portfolio_basis` shipped green and
    stayed green -- it never ran against the product's own primary input
    route. Here the ledger is built the way a real user's is: `prepare`
    ingesting a trades CSV, which writes only `trade` events and no
    snapshot, ever. Without this test, that gate could come back and every
    other fixture in this file would still pass."""
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
        assert {event["type"] for event in written_events} == {"trade"}, (
            "the primary CSV route must never write a snapshot event -- that is "
            "what made the now-removed completeness gate unreachable")

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
    import consequence as cq  # noqa: E402
    schema_disclosures = set(
        EVALUATION_SCHEMA["properties"]["consequence"]["properties"]["disclosures"]["items"]["enum"])
    assert schema_disclosures == set(cq.DISCLOSURES)


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


def test_agent_case_is_stored_when_supplied_and_absent_when_not():
    with tempfile.TemporaryDirectory() as tmp:
        case_path = os.path.join(tmp, "case.json")
        with open(case_path, "w", encoding="utf-8") as f:
            json.dump({"for": [{"claim": "Momentum is intact.", "provenance": "agent_judgment"}],
                      "against": [{"claim": "Already the largest position.",
                                   "provenance": "engine_fact"}]}, f)
        with_case = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                             "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}',
                             "--agent-case", case_path))
        assert "agent_case" in with_case["evaluation"]
        _check_evaluation_shape(with_case["evaluation"])

        without_case = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                                "--premise",
                                '{"ticker": "NVDA", "side": "buy", "price": 131.0, "qty": 5}'))
        assert "agent_case" not in without_case["evaluation"]


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


# ─────────────────────────────── runner ───────────────────────────────

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
