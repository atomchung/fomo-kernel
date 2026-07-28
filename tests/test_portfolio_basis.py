#!/usr/bin/env python3
"""Focused contract tests for the #484 PortfolioBasis query service."""
import copy
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE)

import portfolio_basis as pb  # noqa: E402


def _snap(date, positions, **extra):
    return {"type": "snapshot", "as_of": date, "positions": positions, **extra}


def _trade(date, ticker, action, qty, price, **extra):
    return {"type": "trade", "date": date, "ticker": ticker, "action": action,
            "qty": qty, "price": price, **extra}


def test_transaction_replay_is_unverified_and_stable():
    events = [_trade("2026-07-01", "NVDA", "buy", 10, 100)]
    one = pb.query_current_book(events, reference_as_of="2026-07-03")
    two = pb.query_current_book(list(events), reference_as_of="2026-07-03")
    assert one and one.source == "transaction_replay"
    assert one.completeness == "unverified" and one.stale_days == 2
    assert one.cost_basis == "average_cost" and one.valuation_basis == "unpriced"
    assert one.state_version == two.state_version
    assert one.current_book["holdings"]["NVDA"]["shares"] == 10.0


def test_valuation_frame_is_typed_and_changes_basis_version():
    events = [_snap("2026-07-01", [{"ticker": "A", "shares": 2, "avg_cost": 10,
                                      "currency": "USD"}])]
    frame = pb.build_valuation_frame(
        as_of="2026-07-02", positions={"A": {"currency": "USD"}}, prices={"A": 15},
        aggregate_currency="USD", fx_to_aggregate={}, price_provenance="engine_fetch",
        fx_provenance="engine_fetch").to_dict()
    basis = pb.query_current_book(events, reference_as_of="2026-07-02", valuation_manifest=frame)
    legacy = pb.query_current_book(events, reference_as_of="2026-07-02",
                                   valuation_manifest={"as_of": "2026-07-02", "prices": {"A": 15}})
    assert basis and legacy and basis.state_version != legacy.state_version
    assert basis.current_book["valuation_manifest"]["aggregate_currency"] == "USD"
    assert basis.current_book["valuation_manifest"]["contract_version"] == pb.VALUATION_FRAME_VERSION


def test_valuation_frame_rejects_missing_fx_and_malformed_native_price():
    events = [_snap("2026-07-01", [{"ticker": "A", "shares": 1, "avg_cost": 10, "currency": "TWD"}])]
    missing_fx = pb.build_valuation_frame(
        as_of="2026-07-02", positions={"A": {"currency": "TWD"}}, prices={"A": 15},
        aggregate_currency="USD", fx_to_aggregate={}, price_provenance="engine_fetch",
        fx_provenance="unavailable").to_dict()
    basis = pb.query_current_book(events, valuation_manifest=missing_fx)
    assert basis and basis.current_book["valuation_manifest"]["coverage"]["missing_fx"] == ["TWD"]


def test_valuation_frame_exact_partition_and_state_version_sensitivity():
    events = [_snap("2026-07-01", [{"ticker": "A", "shares": 1, "avg_cost": 10, "currency": "USD"},
                                     {"ticker": "B", "shares": 1, "avg_cost": 20, "currency": "TWD"}])]
    frame = pb.build_valuation_frame(
        as_of="2026-07-02", positions={"A": {"currency": "USD"}, "B": {"currency": "TWD"}},
        prices={"A": 11}, aggregate_currency="USD", fx_to_aggregate={"TWD": 0.03},
        price_provenance="price_feed", fx_provenance="fx_feed").to_dict()
    assert frame["coverage"] == {"missing_price": [{"ticker": "B", "currency": "TWD"}], "missing_fx": []}
    one = pb.query_current_book(events, valuation_manifest=frame)
    changed = copy.deepcopy(frame); changed["fx_to_aggregate"]["TWD"]["rate"] = 0.04
    two = pb.query_current_book(events, valuation_manifest=changed)
    assert one and two and one.state_version != two.state_version
    bad = copy.deepcopy(frame); bad["prices"]["X"] = bad["prices"].pop("A")
    try:
        pb.query_current_book(events, valuation_manifest=bad)
    except pb.PortfolioBasisError:
        pass
    else:
        raise AssertionError("prices must exactly partition current holdings")


def test_valuation_frame_rejects_adversarial_shapes_without_typeerror():
    frame = pb.build_valuation_frame(
        as_of="2026-07-02", positions={"A": {"currency": "USD"}}, prices={},
        aggregate_currency="USD", fx_to_aggregate={}, price_provenance="unavailable",
        fx_provenance="engine_fetch").to_dict()
    for mutate in (
        lambda x: x.__setitem__("prices", []),
        lambda x: x["coverage"].__setitem__("missing_price", [{"ticker": "A", "currency": "USD"}] * 2),
        lambda x: x["fx_to_aggregate"].__setitem__("EUR", {"rate": 1, "provenance": "x", "as_of": "2026-07-02"}),
    ):
        bad = copy.deepcopy(frame); mutate(bad)
        try:
            pb.validate_valuation_frame(bad, positions={"A": {"currency": "USD"}})
        except pb.PortfolioBasisError:
            pass
        else:
            raise AssertionError("adversarial valuation frame accepted")
    for kwargs in (
        {"prices": {"X": 1}, "fx_to_aggregate": {}},
        {"prices": {}, "fx_to_aggregate": {"EUR": 1}},
    ):
        try:
            pb.build_valuation_frame(as_of="2026-07-02", positions={"A": {"currency": "USD"}},
                                     aggregate_currency="USD", price_provenance="unavailable",
                                     fx_provenance="engine_fetch", **kwargs)
        except pb.PortfolioBasisError:
            pass
        else:
            raise AssertionError("unknown builder input accepted")


def test_nested_frame_partition_cannot_be_forged_with_a_recomputed_state_version():
    basis = pb.query_current_book(
        [_snap("2026-07-01", [{"ticker": "A", "shares": 1, "avg_cost": 10, "currency": "USD"}])],
        valuation_manifest=pb.build_valuation_frame(
            as_of="2026-07-02", positions={"A": {"currency": "USD"}}, prices={"A": 11},
            aggregate_currency="USD", fx_to_aggregate={}, price_provenance="price_feed",
            fx_provenance="fx_feed").to_dict())
    forged = basis.to_dict()
    forged["current_book"]["valuation_manifest"]["prices"] = {
        "X": forged["current_book"]["valuation_manifest"]["prices"].pop("A")}
    forged["state_version"] = pb._digest(pb._version_payload(forged))
    try:
        pb.validate_portfolio_basis(forged)
    except pb.PortfolioBasisError:
        pass
    else:
        raise AssertionError("rehashed nested frame must still partition current holdings")


def test_anchor_and_post_anchor_semantics_and_same_day_version():
    anchor = _snap("2026-07-05", [{"ticker": "NVDA", "shares": 10, "avg_cost": 100}],
                   cash={"USD": 1000}, source="broker", projection_sequence=2)
    same_day = _trade("2026-07-05", "NVDA", "buy", 1, 110)
    later = _trade("2026-07-06", "NVDA", "buy", 1, 110)
    before = pb.query_current_book([anchor, same_day], reference_as_of="2026-07-06")
    after = pb.query_current_book([anchor, same_day, later], reference_as_of="2026-07-06")
    assert before and after
    assert before.completeness == "declared_complete"
    assert before.current_book["holdings"]["NVDA"]["shares"] == 10.0
    assert after.current_book["holdings"]["NVDA"]["shares"] == 11.0
    assert before.state_version != after.state_version
    assert after.reconciliation_ref["projection_sequence"] == 2


def test_anchor_day_trade_changes_version_without_stacking_holdings():
    anchor = _snap("2026-07-05", [{"ticker": "NVDA", "shares": 10, "avg_cost": 100}])
    same_day = _trade("2026-07-05", "NVDA", "buy", 1, 110)
    before = pb.query_current_book([anchor])
    after = pb.query_current_book([anchor, same_day])
    assert before and after
    assert before.current_book["holdings"] == after.current_book["holdings"], "ledger does not stack same-day trades"
    assert before.state_version != after.state_version
    assert after.current_book["version_evidence"][-1]["date"] == "2026-07-05"


def test_same_day_book_affecting_snapshot_cash_and_sequence_change_version():
    base = _snap("2026-07-05", [{"ticker": "A", "shares": 1, "avg_cost": 10}], cash={"USD": 1}, projection_sequence=1)
    cash_changed = {**base, "cash": {"USD": 2}}
    sequence_changed = {**base, "projection_sequence": 2}
    a = pb.query_current_book([base])
    b = pb.query_current_book([cash_changed])
    c = pb.query_current_book([sequence_changed])
    assert a and b and c and len({a.state_version, b.state_version, c.state_version}) == 3


def test_higher_same_day_projection_sequence_wins_and_position_order_is_noise():
    lower = _snap("2026-07-05", [{"ticker": "A", "shares": 1, "avg_cost": 10}], projection_sequence=1)
    higher = _snap("2026-07-05", [{"ticker": "B", "shares": 2, "avg_cost": 20}, {"ticker": "A", "shares": 1, "avg_cost": 10}], projection_sequence=2)
    selected = pb.query_current_book([higher, lower])
    reordered = pb.query_current_book([{**higher, "positions": list(reversed(higher["positions"]))}])
    original = pb.query_current_book([higher])
    assert selected and original and reordered
    assert set(selected.current_book["holdings"]) == {"A", "B"}
    assert selected.reconciliation_ref["projection_sequence"] == 2
    assert original.state_version == reordered.state_version


def test_freshness_reference_is_not_a_current_book_version_change():
    events = [_trade("2026-07-01", "A", "buy", 1, 10)]
    old = pb.query_current_book(events, reference_as_of="2026-07-02")
    fresh = pb.query_current_book(events, reference_as_of="2026-07-05")
    assert old and fresh and old.stale_days == 1 and fresh.stale_days == 4
    assert old.state_version == fresh.state_version


def test_incomplete_snapshot_is_not_anchor_and_remains_distinct():
    partial = _snap("2026-07-10", [{"ticker": "A", "shares": 999}], is_complete=False)
    basis = pb.query_current_book([_trade("2026-07-01", "A", "buy", 2, 10), partial])
    assert basis and basis.source == "transaction_replay"
    assert basis.completeness == "declared_partial"
    assert basis.current_book["holdings"]["A"]["shares"] == 2.0


def test_partial_sell_unknown_cost_and_valuation_manifest_affect_version():
    events = [_snap("2026-07-01", [{"ticker": "A", "shares": 10}]),
              _trade("2026-07-02", "A", "sell", 4, 20)]
    unpriced = pb.query_current_book(events, reference_as_of="2026-07-03")
    priced = pb.query_current_book(events, reference_as_of="2026-07-03",
                                    valuation_manifest={"as_of": "2026-07-03", "prices": {"A": 21}})
    assert unpriced and priced
    assert unpriced.current_book["holdings"]["A"]["avg_cost"] is None
    assert unpriced.cost_basis == "partial_average_cost"
    assert priced.valuation_basis == "priced" and priced.state_version != unpriced.state_version


def test_oversell_integrity_is_visible_and_claim_affecting_but_bad_input_fails_closed():
    oversold = pb.query_current_book([_trade("2026-07-01", "A", "buy", 1, 10),
                                       _trade("2026-07-02", "A", "sell", 2, 20)])
    clean = pb.query_current_book([_trade("2026-07-01", "A", "buy", 1, 10)])
    assert oversold and clean and oversold.integrity and oversold.state_version != clean.state_version
    assert pb.query_current_book([{"type": "trade", "date": "bad"}]) is None
    assert pb.query_current_book([], skipped_lines=1) is None


def test_semantic_preflight_rejects_invalid_snapshot_and_unknown_direct_event():
    valid = _trade("2026-07-01", "A", "buy", 1, 10)
    malformed_snapshot = {"type": "snapshot", "as_of": "not-a-date", "positions": []}
    malformed_positions = {"type": "snapshot", "as_of": "2026-07-02", "positions": "not-a-list"}
    assert pb.query_current_book([malformed_snapshot, valid]) is None
    assert pb.query_current_book([malformed_snapshot]) is None
    assert pb.query_current_book([malformed_positions, valid]) is None
    assert pb.query_current_book([valid, {"type": "unknown", "date": "2026-07-02"}]) is None


def test_semantic_preflight_rejects_adapter_shape_and_valuation_damage_without_throwing():
    valid = _trade("2026-07-01", "A", "buy", 1, 10)
    bad_positions = [
        {"ticker": "A", "shares": 0},
        {"ticker": "A", "shares": 1, "market_value": -1},
        {"ticker": "A", "shares": 1, "market": 7},
        {"ticker": "A", "shares": 1, "unexpected": True},
    ]
    for position in bad_positions:
        assert pb.query_current_book([_snap("2026-07-02", [position]), valid]) is None
    assert pb.query_current_book([_snap("2026-07-02", [{"ticker": "A", "shares": 1}], is_complete="yes"), valid]) is None
    assert pb.query_current_book([_snap("2026-07-02", [{"ticker": "A", "shares": 1}], projection_sequence=0), valid]) is None
    assert pb.query_current_book([_trade("2026-07-01", "A", "buy", 1, 10, currency=3)]) is None
    try:
        pb.query_current_book([valid], valuation_manifest={"as_of": "2026-07-02", "prices": {"A": 0}})
        raise AssertionError("zero valuation price must be rejected")
    except pb.PortfolioBasisError:
        pass


def test_strict_ledger_reader_never_derives_across_corrupt_jsonl():
    with tempfile.TemporaryDirectory() as root:
        ledger_path = os.path.join(root, "ledger.jsonl")
        open(ledger_path, "w", encoding="utf-8").write("not json\n")
        assert pb.query_current_book_from_ledger(ledger_path) is None


def test_schema_and_typed_contract_reject_adversarial_mutations_and_bad_hash():
    schema_path = os.path.join(ROOT, "skills", "fomo-kernel", "schemas", "portfolio-basis.schema.json")
    schema = json.loads(open(schema_path, encoding="utf-8").read())
    assert "declared_partial" in schema["properties"]["completeness"]["enum"]
    assert schema["$defs"]["currentBook"]["additionalProperties"] is False
    assert schema["$defs"]["integrity"]["additionalProperties"] is False
    basis = pb.query_current_book([_trade("2026-07-01", "A", "buy", 1, 10)])
    assert basis
    mutations = []
    malformed = copy.deepcopy(basis.to_dict()); malformed["source"] = "invented"; mutations.append(malformed)
    malformed = copy.deepcopy(basis.to_dict()); malformed["current_book"] = {}; mutations.append(malformed)
    malformed = copy.deepcopy(basis.to_dict()); malformed["current_book"]["holdings"]["A"]["shares"] = 2; mutations.append(malformed)
    malformed = copy.deepcopy(basis.to_dict()); malformed["current_book"]["unexpected"] = True; mutations.append(malformed)
    malformed = copy.deepcopy(basis.to_dict()); malformed["state_version"] = "pb-v1:" + "0" * 64; mutations.append(malformed)
    for malformed in mutations:
        try:
            pb.validate_portfolio_basis(malformed)
            raise AssertionError("adversarial PortfolioBasis payload must be rejected")
        except pb.PortfolioBasisError:
            pass


def test_sizing_projection_is_canonical_for_full_priced_mixed_and_cost_books():
    events = [_trade("2026-07-01", "A", "buy", 2, 10),
              _trade("2026-07-01", "B", "buy", 1, 20)]
    full_basis = pb.query_current_book(events, reference_as_of="2026-07-02",
                                       valuation_manifest={"as_of": "2026-07-02", "prices": {"A": 15, "B": 30}})
    full = pb.sizing_projection(full_basis)
    assert full and full.applicable and full.reason is None
    assert full.denominator == 60 and full.coverage["scope"] == "full_current_book"
    assert full.values["A"] == {"value": 30.0, "weight": 0.5, "source": "price", "currency": "USD", "applicable": True, "reason": None}
    assert full.values["B"]["weight"] == 0.5

    mixed_basis = pb.query_current_book(events, reference_as_of="2026-07-02",
                                        valuation_manifest={"as_of": "2026-07-02", "prices": {"A": 15}})
    mixed = pb.sizing_projection(mixed_basis)
    assert mixed and mixed.applicable and mixed.denominator == 50
    assert mixed.coverage == {"scope": "full_current_book", "total_holdings": 2, "valued_holdings": 2,
                              "priced": ["A"], "cost_fallback": ["B"], "unavailable": [], "currencies": ["USD"]}
    assert mixed.values["A"]["weight"] == 0.6 and mixed.values["B"]["weight"] == 0.4

    cost = pb.sizing_projection(pb.query_current_book(events, reference_as_of="2026-07-02"))
    assert cost and cost.applicable and cost.denominator == 40
    assert cost.coverage["scope"] == "full_current_book"
    assert {row["source"] for row in cost.values.values()} == {"cost_fallback"}


def test_sizing_projection_marks_unavailable_and_empty_books_inapplicable():
    events = [_snap("2026-07-01", [{"ticker": "A", "shares": 1, "avg_cost": 10},
                                    {"ticker": "B", "shares": 1}])]
    basis = pb.query_current_book(events, reference_as_of="2026-07-02",
                                  valuation_manifest={"as_of": "2026-07-02", "prices": {"A": 12}})
    projection = pb.sizing_projection(basis)
    assert projection and projection.applicable and projection.denominator == 12
    assert projection.coverage["scope"] == "bounded_valued_subset"
    assert projection.coverage["unavailable"] == ["B"]
    assert projection.values["B"] == {"value": None, "weight": None, "source": "unavailable", "currency": "USD",
                                        "applicable": False, "reason": "value_unavailable"}

    empty = pb.sizing_projection(pb.query_current_book([], reference_as_of="2026-07-02"))
    assert empty and not empty.applicable and empty.reason == "empty_current_book"
    assert empty.denominator is None and empty.values == {}

    no_value = pb.sizing_projection(pb.query_current_book([_snap("2026-07-01", [{"ticker": "A", "shares": 1}])],
                                                           reference_as_of="2026-07-02"))
    assert no_value and not no_value.applicable and no_value.reason == "no_valued_holdings"
    assert no_value.values["A"]["weight"] is None


def test_sizing_projection_fails_closed_on_invalid_or_tampered_basis():
    assert pb.sizing_projection(None) is None
    basis = pb.query_current_book([_trade("2026-07-01", "A", "buy", 1, 10)])
    assert basis
    basis.current_book["holdings"]["A"]["shares"] = 2
    assert pb.sizing_projection(basis) is None


def test_sizing_projection_validator_rejects_weight_and_coverage_lies():
    basis = pb.query_current_book([_trade("2026-07-01", "A", "buy", 2, 10),
                                   _trade("2026-07-01", "B", "buy", 1, 20)],
                                  reference_as_of="2026-07-02",
                                  valuation_manifest={"as_of": "2026-07-02", "prices": {"A": 15, "B": 30}})
    projection = pb.sizing_projection(basis)
    assert projection
    mutations = []
    bad_weight = copy.deepcopy(projection.to_dict()); bad_weight["values"]["A"]["weight"] = 0.75; mutations.append(bad_weight)
    bad_sum = copy.deepcopy(projection.to_dict()); bad_sum["values"]["A"]["weight"] = 0.4; mutations.append(bad_sum)
    bad_source = copy.deepcopy(projection.to_dict()); bad_source["coverage"]["cost_fallback"] = ["A"]; bad_source["coverage"]["priced"] = ["B"]; mutations.append(bad_source)
    bad_scope = copy.deepcopy(projection.to_dict()); bad_scope["coverage"]["scope"] = "bounded_valued_subset"; mutations.append(bad_scope)
    duplicate = copy.deepcopy(projection.to_dict()); duplicate["coverage"]["priced"] = ["A", "B", "A"]; mutations.append(duplicate)
    bad_currencies = copy.deepcopy(projection.to_dict()); bad_currencies["coverage"]["currencies"] = "USD"; mutations.append(bad_currencies)
    for mutation in mutations:
        try:
            pb.validate_sizing_projection(mutation)
            raise AssertionError("projection mutation must be rejected")
        except pb.PortfolioBasisError:
            pass


def test_sizing_projection_fails_closed_on_finite_multiplication_overflow():
    basis = pb.query_current_book(
        [_snap("2026-07-01", [{"ticker": "A", "shares": 1e308, "avg_cost": 1}])],
        reference_as_of="2026-07-02",
        valuation_manifest={"as_of": "2026-07-02", "prices": {"A": 2.0}},
    )
    assert basis
    assert pb.sizing_projection(basis) is None


def test_sizing_projection_refuses_mixed_native_currency_without_fx_frame():
    basis = pb.query_current_book(
        [_trade("2026-07-01", "USD", "buy", 2, 10, currency="USD"),
         _trade("2026-07-01", "TWD", "buy", 3, 100, currency="TWD")],
        reference_as_of="2026-07-02",
        valuation_manifest={"as_of": "2026-07-02", "prices": {"USD": 15, "TWD": 110}},
    )
    projection = pb.sizing_projection(basis)
    assert projection and not projection.applicable
    assert projection.reason == "mixed_native_currencies" and projection.denominator is None
    assert projection.coverage == {"scope": "unavailable_mixed_currency", "total_holdings": 2,
                                   "valued_holdings": 0, "priced": [], "cost_fallback": [],
                                   "unavailable": ["TWD", "USD"], "currencies": ["TWD", "USD"]}
    assert all(row["value"] is None and row["weight"] is None
               and row["reason"] == "mixed_native_currencies" for row in projection.values.values())

    forged = copy.deepcopy(projection.to_dict())
    forged["applicable"] = True; forged["reason"] = None; forged["denominator"] = 330
    forged["coverage"]["scope"] = "full_current_book"; forged["coverage"]["priced"] = ["TWD", "USD"]
    forged["coverage"]["currencies"] = ["USD"]
    forged["coverage"]["unavailable"] = []; forged["coverage"]["valued_holdings"] = 2
    for ticker, row in forged["values"].items():
        row.update(value=30 if ticker == "USD" else 300, weight=1 / 11 if ticker == "USD" else 10 / 11,
                   source="price", applicable=True, reason=None)
    try:
        pb.validate_sizing_projection(forged)
        raise AssertionError("forged mixed-currency applicable projection must be rejected")
    except pb.PortfolioBasisError:
        pass


def test_sizing_projection_uses_complete_frozen_frame_for_mixed_currency_and_rejects_forgery():
    events = [_trade("2026-07-01", "USD", "buy", 2, 10, currency="USD"),
              _trade("2026-07-01", "TWD", "buy", 3, 100, currency="TWD")]
    frame = pb.build_valuation_frame(
        as_of="2026-07-02", positions={"USD": {"currency": "USD"}, "TWD": {"currency": "TWD"}},
        prices={"USD": 15, "TWD": 110}, aggregate_currency="USD", fx_to_aggregate={"TWD": 0.03},
        price_provenance="price_feed", fx_provenance="fx_feed",
    )
    basis = pb.query_current_book(events, reference_as_of="2026-07-02", valuation_manifest=frame.to_dict())
    projection = pb.sizing_projection(basis)
    assert projection and projection.applicable and projection.reason is None
    assert projection.coverage["scope"] == "full_current_book" and projection.coverage["currencies"] == ["TWD", "USD"]
    assert projection.aggregate_currency == "USD"
    assert projection.frame and projection.frame["aggregate_currency"] == "USD"
    assert projection.frame["basis_state_version"] == basis.state_version
    assert projection.values["USD"]["value"] == 30 and projection.values["TWD"]["value"] == 9.9
    assert projection.values["TWD"]["frame_proof"] == {
        "native_currency": "TWD", "native_price": 110.0, "fx_rate": 0.03, "converted_value": 9.9,
        "frame_identity": projection.frame["identity"], "basis_state_version": basis.state_version,
    }
    assert abs(projection.denominator - 39.9) < 1e-12

    mutations = []
    forged_fx = copy.deepcopy(projection.to_dict()); forged_fx["values"]["TWD"]["frame_proof"]["fx_rate"] = 1.0; mutations.append(forged_fx)
    forged_price = copy.deepcopy(projection.to_dict()); forged_price["values"]["TWD"]["frame_proof"]["native_price"] = 100; mutations.append(forged_price)
    forged_value = copy.deepcopy(projection.to_dict()); forged_value["values"]["TWD"]["value"] = 330; forged_value["values"]["TWD"]["frame_proof"]["converted_value"] = 330; forged_value["denominator"] = 360; forged_value["values"]["USD"]["weight"] = 1 / 12; forged_value["values"]["TWD"]["weight"] = 11 / 12; mutations.append(forged_value)
    forged_identity = copy.deepcopy(projection.to_dict()); forged_identity["frame"]["identity"] = "vf-v1:" + "0" * 64; forged_identity["values"]["USD"]["frame_proof"]["frame_identity"] = forged_identity["frame"]["identity"]; forged_identity["values"]["TWD"]["frame_proof"]["frame_identity"] = forged_identity["frame"]["identity"]; mutations.append(forged_identity)
    forged_state = copy.deepcopy(projection.to_dict()); forged_state["frame"]["basis_state_version"] = "pb-v1:" + "0" * 64; forged_state["values"]["USD"]["frame_proof"]["basis_state_version"] = forged_state["frame"]["basis_state_version"]; forged_state["values"]["TWD"]["frame_proof"]["basis_state_version"] = forged_state["frame"]["basis_state_version"]; mutations.append(forged_state)
    for mutation in mutations:
        try:
            pb.validate_sizing_projection(mutation, basis=basis)
            raise AssertionError("forged frame-bound projection must be rejected")
        except pb.PortfolioBasisError:
            pass

    missing_fx_frame = pb.build_valuation_frame(
        as_of="2026-07-02", positions={"USD": {"currency": "USD"}, "TWD": {"currency": "TWD"}},
        prices={"USD": 15, "TWD": 110}, aggregate_currency="USD", fx_to_aggregate={},
        price_provenance="price_feed", fx_provenance="unavailable",
    )
    missing_fx_basis = pb.query_current_book(events, reference_as_of="2026-07-02",
                                             valuation_manifest=missing_fx_frame.to_dict())
    missing_fx = pb.sizing_projection(missing_fx_basis)
    assert missing_fx and not missing_fx.applicable and missing_fx.reason == "mixed_native_currencies"


def test_sizing_projection_fails_closed_when_currency_identity_is_missing_or_invalid():
    missing = pb.query_current_book([_trade("2026-07-01", "A", "buy", 1, 10, currency="")])
    invalid = pb.query_current_book([_trade("2026-07-01", "A", "buy", 1, 10, currency=3)])
    nope_trade = pb.query_current_book([_trade("2026-07-01", "A", "buy", 1, 10, currency="NOPE")])
    nope_snapshot = pb.query_current_book([_snap("2026-07-01", [{"ticker": "A", "shares": 1, "currency": "NOPE"}])])
    assert missing is None and invalid is None and nope_trade is None and nope_snapshot is None
    assert all(pb.sizing_projection(item) is None for item in (missing, invalid, nope_trade, nope_snapshot))


def _main():
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS ", name)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("FAIL ", name, type(exc).__name__, exc)
    print(f"{len(tests) - failed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    raise SystemExit(_main())
