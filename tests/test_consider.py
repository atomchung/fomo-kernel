#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""review.py consider (Layer 2 CLI entry point) -- offline, deterministic, no pytest.

`consider` wires engine/consequence.py's three public functions
(validate_premise / consequence / rule_collision -- already covered end to end
by tests/test_consequence.py) into the review.py CLI, with a new persisted
record (<root>/pre_trade_consultations.jsonl,
schemas/pre-trade-consultation.schema.json). This file settles the parts
consequence.py's own suite cannot: data-source resolution (CSV vs a
ledger-reconstructed book), the frozen consultation row, --resolve's
append-only fold, and the CLI's own fail-closed and validation surfaces.

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
import review as review_engine  # noqa: E402
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


def _consultation_path(root):
    return os.path.join(root, "pre_trade_consultations.jsonl")


def _read_consultations(root):
    path = _consultation_path(root)
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


CONSULTATION_SCHEMA = _schema("pre-trade-consultation.schema.json")
PREMISE_SCHEMA = _schema("trade-premise.schema.json")


def _check_consultation_shape(row):
    """Spot-check the row against pre-trade-consultation.schema.json, the
    same "no jsonschema dependency, pin the vocabulary" idiom
    test_consequence.py already uses for trade-premise.schema.json."""
    required = set(CONSULTATION_SCHEMA["required"])
    assert required <= set(row), f"missing required fields: {required - set(row)}"
    allowed = set(CONSULTATION_SCHEMA["properties"])
    assert set(row) <= allowed, f"row carries undeclared fields: {set(row) - allowed}"

    assert row["decision"] in CONSULTATION_SCHEMA["properties"]["decision"]["enum"]
    assert row["decided_on"] is None or isinstance(row["decided_on"], str)

    basis_schema = CONSULTATION_SCHEMA["properties"]["basis"]
    assert set(basis_schema["required"]) <= set(row["basis"])
    assert row["basis"]["source"] in basis_schema["properties"]["source"]["enum"]
    assert isinstance(row["basis"]["stale_days"], int) and row["basis"]["stale_days"] >= 0

    consequence_schema = CONSULTATION_SCHEMA["properties"]["consequence"]
    assert set(consequence_schema["required"]) <= set(row["consequence"])
    allowed_disclosures = set(consequence_schema["properties"]["disclosures"]["items"]["enum"])
    assert set(row["consequence"]["disclosures"]) <= allowed_disclosures

    collision_states = set(
        CONSULTATION_SCHEMA["properties"]["rule_collisions"]["items"]["properties"]["state"]["enum"])
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
        case_schema = CONSULTATION_SCHEMA["properties"]["agent_case"]
        assert set(case_schema["required"]) <= set(row["agent_case"])
        provenances = set(CONSULTATION_SCHEMA["$defs"]["claim"]["properties"]["provenance"]["enum"])
        for side in ("for", "against"):
            for claim in row["agent_case"][side]:
                assert set(claim) == {"claim", "provenance"}
                assert claim["provenance"] in provenances


# ─────────────────── A. data-source resolution ───────────────────

def test_csv_path_produces_a_consultation_with_transactions_basis():
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        payload = _ok(run)
        row = payload["consultation"]
        assert row["basis"]["source"] == "transactions"
        _check_consultation_shape(row)
        assert _read_consultations(tmp) == [row]


def test_ledger_path_produces_a_consultation_with_ledger_basis():
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), [
            _snapshot_event("2026-01-01", [
                {"ticker": "NVDA", "shares": 100, "avg_cost": 100.0, "market": "US", "currency": "USD"}]),
            _trade_event("2026-02-01", "NVDA", "buy", 20, 120.0),
        ])
        run = _run("consider", "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 150.0, "qty": 10}')
        payload = _ok(run)
        row = payload["consultation"]
        assert row["basis"]["source"] == "ledger"
        assert row["basis"]["as_of"] == "2026-02-01"   # the ledger's own latest row, not today
        _check_consultation_shape(row)


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
        assert payload["consultation"]["basis"]["source"] == "transactions"
        assert "AAPL" not in payload["consultation"]["consequence"]["before"]["held"]


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
        basis = payload["consultation"]["basis"]
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
        assert not os.path.exists(_consultation_path(tmp)), "a failed call must not write a row"


def test_empty_ledger_file_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "ledger.jsonl"), "w", encoding="utf-8").close()
        run = _run("consider", "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        _fails(run, "no usable trade or snapshot history")


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


def test_anchor_position_with_no_cost_basis_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), [
            _snapshot_event("2026-01-01", [
                {"ticker": "NVDA", "shares": 10, "market": "US", "currency": "USD"}]),  # no avg_cost
        ])
        run = _run("consider", "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        _fails(run, "no avg_cost or market_value")


def test_anchor_position_falls_back_to_market_value_when_avg_cost_is_missing():
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), [
            _snapshot_event("2026-01-01", [
                {"ticker": "NVDA", "shares": 10, "market_value": 1500.0,
                 "market": "US", "currency": "USD"}]),
        ])
        run = _run("consider", "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        payload = _ok(run)
        held = payload["consultation"]["consequence"]["before"]["held"]["NVDA"]
        assert held["shares"] == 10.0
        assert abs(held["cost"] - 1500.0) < 1e-6   # 150/share implied by market_value / shares


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
        assert payload["consultation"]["premise"]["ticker"] == "AMD"


def test_consider_requires_premise_or_resolve():
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp)
        _fails(run, "requires --premise")


# ───────────────────────── E. resolve / fold ─────────────────────────

def test_resolve_appends_a_new_row_rather_than_rewriting():
    with tempfile.TemporaryDirectory() as tmp:
        created = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                           "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}'))
        consultation_id = created["consultation"]["consultation_id"]

        resolved = _ok(_run("consider", "--root", tmp, "--resolve", consultation_id,
                            "--decision", "acted"))
        assert resolved["consultation"]["decision"] == "acted"
        assert resolved["consultation"]["decided_on"] == dt.date.today().isoformat()

        on_disk = _read_consultations(tmp)
        assert len(on_disk) == 2, "resolve must append, never rewrite the original row"
        assert on_disk[0]["decision"] == "open"
        assert on_disk[0]["consultation_id"] == consultation_id
        assert on_disk[1]["decision"] == "acted"
        assert on_disk[1]["consultation_id"] == consultation_id
        # every other frozen field survives the resolution unchanged
        assert on_disk[0]["consequence"] == on_disk[1]["consequence"]
        assert on_disk[0]["premise"] == on_disk[1]["premise"]


def test_fold_returns_the_latest_decision():
    with tempfile.TemporaryDirectory() as tmp:
        created = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                           "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}'))
        consultation_id = created["consultation"]["consultation_id"]
        _ok(_run("consider", "--root", tmp, "--resolve", consultation_id, "--decision", "modified"))
        _ok(_run("consider", "--root", tmp, "--resolve", consultation_id, "--decision", "acted"))

        on_disk = _read_consultations(tmp)
        assert len(on_disk) == 3
        latest = review_engine._fold_consultations(on_disk)
        assert latest[consultation_id]["decision"] == "acted"


def test_resolve_of_unknown_consultation_id_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", "--root", tmp, "--resolve", "consult-doesnotexist",
                   "--decision", "acted")
        _fails(run, "no consultation matching")


def test_decision_flag_requires_resolve():
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}',
                   "--decision", "acted")
        _fails(run, "--decision only applies together with --resolve")


def test_resolve_forbids_combining_with_premise():
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", "--root", tmp, "--resolve", "consult-x", "--decision", "acted",
                   "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}')
        _fails(run, "--resolve takes no premise")


def test_resolve_rejects_a_decision_of_open():
    """`open` is a row's starting state, never something --resolve records to
    -- argparse's own choices= is the gate, not a hand-written check."""
    with tempfile.TemporaryDirectory() as tmp:
        run = _run("consider", "--root", tmp, "--resolve", "consult-x", "--decision", "open")
        assert run.returncode != 0
        assert "invalid choice" in run.stderr.lower()


# ───────────────────────── F. schema and vocabulary drift ─────────────────────────

def test_created_row_round_trips_against_the_schema():
    with tempfile.TemporaryDirectory() as tmp:
        payload = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                           "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}'))
        _check_consultation_shape(payload["consultation"])


def test_resolved_row_round_trips_against_the_schema():
    with tempfile.TemporaryDirectory() as tmp:
        created = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                           "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}'))
        resolved = _ok(_run("consider", "--root", tmp, "--resolve",
                            created["consultation"]["consultation_id"], "--decision", "declined"))
        _check_consultation_shape(resolved["consultation"])


def test_consider_decisions_constant_matches_the_schemas_decision_enum():
    """review.CONSIDER_DECISIONS feeds --decision's argparse choices; the
    schema's decision enum is the same vocabulary plus "open", a row's
    starting state and never something --resolve records to. A second
    person adding a decision value in one place and not the other is exactly
    the "two readers, one fact" shape (development-guide.md section 7)."""
    schema_enum = set(CONSULTATION_SCHEMA["properties"]["decision"]["enum"])
    assert schema_enum == set(review_engine.CONSIDER_DECISIONS) | {"open"}


def test_collision_states_constant_is_exactly_what_the_schema_declares():
    import consequence as cq  # noqa: E402  (engine dir already on sys.path)
    schema_states = set(
        CONSULTATION_SCHEMA["properties"]["rule_collisions"]["items"]["properties"]["state"]["enum"])
    assert schema_states == set(cq.COLLISION_STATES)


def test_disclosures_constant_is_exactly_what_the_schema_declares():
    import consequence as cq  # noqa: E402
    schema_disclosures = set(
        CONSULTATION_SCHEMA["properties"]["consequence"]["properties"]["disclosures"]["items"]["enum"])
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
        collisions = payload["consultation"]["rule_collisions"]
        assert len(collisions) == 1
        assert collisions[0]["rule_id"] == "rule-1"
        assert collisions[0]["state"] == "already_over"
        assert collisions[0]["worsens"] is True

        with open(os.path.join(tmp, "profile.json"), "w", encoding="utf-8") as f:
            json.dump({"muted_rules": ["rule-1"]}, f)
        payload = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                           "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 20}'))
        assert payload["consultation"]["rule_collisions"] == []


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
        assert "agent_case" in with_case["consultation"]
        _check_consultation_shape(with_case["consultation"])

        without_case = _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                                "--premise",
                                '{"ticker": "NVDA", "side": "buy", "price": 131.0, "qty": 5}'))
        assert "agent_case" not in without_case["consultation"]


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
        assert os.path.exists(_consultation_path(tmp))


def test_pre_trade_consultations_is_registered_in_coach_data_files():
    text = COACH_PY.read_text(encoding="utf-8")
    assert '"pre_trade_consultations.jsonl"' in text
    # exercised for real, not just grepped: data-status/-export/-reset must see it
    with tempfile.TemporaryDirectory() as tmp:
        _ok(_run("consider", str(MOCK / "sample_momentum.csv"), "--root", tmp,
                 "--premise", '{"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 5}'))
        status = subprocess.run([sys.executable, str(COACH_PY), "data-status", "--root", tmp],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert status.returncode == 0, status.stdout + status.stderr
        names = {entry["name"] for entry in json.loads(status.stdout)["files"]}
        assert "pre_trade_consultations.jsonl" in names
        entry = next(e for e in json.loads(status.stdout)["files"]
                     if e["name"] == "pre_trade_consultations.jsonl")
        assert entry["exists"] is True and entry["lines"] == 1

        reset = subprocess.run([sys.executable, str(COACH_PY), "data-reset", "--confirm", "--root", tmp],
                               cwd=ROOT, capture_output=True, text=True, timeout=30)
        assert reset.returncode == 0, reset.stdout + reset.stderr
        assert not os.path.exists(_consultation_path(tmp)), "data-reset must remove the file"


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
