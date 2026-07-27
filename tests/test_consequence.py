#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""consequence.py — Layer 2 hypothetical-trade arithmetic (#TODO) — unit tests.
Offline, deterministic, no pytest.

What this file settles:
  A. trade-premise.schema.json's oneOf(qty, notional) shape, and that
     validate_premise's normalized output matches the schema's declared shape.
  B. validate_premise: every fail-closed branch, and that a sell of exactly
     the held quantity is accepted rather than rejected or clamped.
  C. portfolio_state / consequence: a buy raises the target's weight and a
     sell lowers it, matching an independently computed expectation; qty and
     notional forms of the same trade produce identical results; a brand-new
     position raises n_holdings.
  D. consequence: the four disclosure keys (cost_basis, cash_unreliable,
     unmapped_driver, mixed_currency_no_fx) each fire under their own
     condition and stay absent otherwise.
  E. rule_collision: would_breach / already_over / clear real verdicts for
     the five evaluable metric keys (including the avgdown_count pair of
     qualifying-average-down plus weight breach), unjudged for
     exit_severity/hold_severity (never a pass), unmapped for a metric_key
     with no problem_key, and muted rules excluded from the rotation.
  F. rule_collision's causal attribution: already_over and would_breach are
     not the same fact wearing two names. A book already over a line reads
     already_over for a trade that improves it (a sell, or an unrelated tiny
     buy) rather than would_breach, and only a trade that freshly crosses the
     line — for max_pos_pct, judged on the premise ticker's own weight, not
     the book's max_ticker — earns would_breach. worsens carries the
     already_over case's second axis (did the relevant reading move in the
     bad direction) and is None everywhere else.

All fixtures are read from skills/fomo-kernel/mock/*.csv; every asserted
number below was measured by running the code against those fixtures, not
guessed (see PR description / session notes for the probe transcript).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MOCK = os.path.join(REPO, "skills", "fomo-kernel", "mock")
sys.path.insert(0, os.path.join(REPO, "skills", "fomo-kernel", "engine"))
import consequence as cq  # noqa: E402
import session  # noqa: E402
import trade_recap as tr  # noqa: E402


def _schema(name):
    path = os.path.join(REPO, "skills", "fomo-kernel", "schemas", name)
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _rows(name):
    return tr.load([os.path.join(MOCK, name)])


def _rule(metric_key, rule_id=None, **overrides):
    row = {"rule_id": rule_id or f"rule-{metric_key}", "text": f"tracks {metric_key}",
           "metric_key": metric_key, "problem_key": session.PKEY.get(metric_key)}
    row.update(overrides)
    return row


def _rejects(premise, rows, expect_fragment):
    try:
        cq.validate_premise(premise, rows)
    except cq.ConsequenceError as exc:
        assert expect_fragment in str(exc), \
            f"rejected for the wrong reason: wanted {expect_fragment!r}, got {str(exc)!r}"
        return
    raise AssertionError(f"should have been rejected ({expect_fragment}): {premise}")


def _close(a, b, tol=1e-9):
    return abs(a - b) <= tol


# ───────────────── A. schema shape ─────────────────

def test_schema_requires_exactly_one_of_qty_or_notional():
    """oneOf shape is the pin, mirroring test_conditions.py's own
    "no jsonschema dependency, pin the vocabulary" idiom."""
    schema = _schema("trade-premise.schema.json")
    one_of = schema["oneOf"]
    assert len(one_of) == 2, "qty XOR notional is exactly two alternatives"
    branches = {frozenset(b["required"]): frozenset(b["not"]["required"]) for b in one_of}
    assert branches.get(frozenset({"qty"})) == frozenset({"notional"}), \
        "the qty branch must require qty and forbid notional"
    assert branches.get(frozenset({"notional"})) == frozenset({"qty"}), \
        "the notional branch must require notional and forbid qty"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"ticker", "side", "price"}


def test_normalized_premise_matches_the_schemas_declared_shape():
    """Structural drift check: validate_premise's output carries no field the
    schema does not declare, and every field the schema requires is present.
    notional is deliberately absent from the normalized form (consumed at
    validation time, converted to qty) — this is what proves that, not a
    literal field-by-field mirror."""
    schema = _schema("trade-premise.schema.json")
    rows = _rows("sample_momentum.csv")
    normalized = cq.validate_premise({"ticker": "NVDA", "side": "buy", "price": 120.0,
                                      "notional": 1000.0}, rows)
    assert set(normalized) <= set(schema["properties"]), \
        f"normalized premise has a field the schema does not declare: {set(normalized) - set(schema['properties'])}"
    assert set(schema["required"]) <= set(normalized)
    assert "notional" not in normalized, "notional is consumed into qty, not carried forward"


# ───────────────── B. validate_premise — fail-closed discipline ─────────────────

def test_rejects_unknown_side():
    rows = _rows("sample_momentum.csv")
    _rejects({"ticker": "NVDA", "side": "hold", "price": 100, "qty": 1}, rows, "side must be one of")


def test_rejects_non_positive_price_qty_notional():
    rows = _rows("sample_momentum.csv")
    _rejects({"ticker": "NVDA", "side": "buy", "price": 0, "qty": 1}, rows, "price must be positive")
    _rejects({"ticker": "NVDA", "side": "buy", "price": -5, "qty": 1}, rows, "price must be positive")
    _rejects({"ticker": "NVDA", "side": "buy", "price": 100, "qty": 0}, rows, "qty must be positive")
    _rejects({"ticker": "NVDA", "side": "buy", "price": 100, "qty": -1}, rows, "qty must be positive")
    _rejects({"ticker": "NVDA", "side": "buy", "price": 100, "notional": -500}, rows,
             "notional must be positive")


def test_rejects_both_or_neither_of_qty_and_notional():
    rows = _rows("sample_momentum.csv")
    _rejects({"ticker": "NVDA", "side": "buy", "price": 100, "qty": 1, "notional": 100}, rows,
             "exactly one of qty or notional")
    _rejects({"ticker": "NVDA", "side": "buy", "price": 100}, rows, "exactly one of qty or notional")


def test_rejects_a_date_earlier_than_the_last_ledger_row():
    rows = _rows("sample_momentum.csv")   # last row: 2024-06-18
    _rejects({"ticker": "NVDA", "side": "buy", "price": 100, "qty": 1, "date": "2024-01-01"},
             rows, "earlier than the ledger's last row")


def test_a_same_day_date_is_accepted_not_rejected():
    rows = _rows("sample_momentum.csv")
    normalized = cq.validate_premise(
        {"ticker": "NVDA", "side": "buy", "price": 100, "qty": 1, "date": "2024-06-18"}, rows)
    assert normalized["date"].isoformat() == "2024-06-18"


def test_rejects_a_sell_of_a_ticker_not_held():
    rows = _rows("sample_momentum.csv")   # holds AMD, MRVL, NVDA only
    _rejects({"ticker": "AAPL", "side": "sell", "price": 100, "qty": 1}, rows, "not currently held")


def test_rejects_a_sell_exceeding_the_held_quantity_rather_than_clamping():
    rows = _rows("sample_momentum.csv")   # NVDA held: 120 shares
    _rejects({"ticker": "NVDA", "side": "sell", "price": 100, "qty": 1000}, rows,
             "more than the 120 currently held")


def test_a_sell_of_exactly_the_held_quantity_is_accepted():
    """The boundary the clamping test above implies: exactly-held is a valid
    full exit, not an off-by-one rejection."""
    rows = _rows("sample_momentum.csv")
    normalized = cq.validate_premise({"ticker": "NVDA", "side": "sell", "price": 100, "qty": 120.0}, rows)
    assert normalized["qty"] == 120.0
    after = cq.portfolio_state(rows + [cq._premise_row(normalized)])
    assert "NVDA" not in after["held"], "a full exit must not leave a residual position"


def test_rejects_unknown_fields():
    rows = _rows("sample_momentum.csv")
    _rejects({"ticker": "NVDA", "side": "buy", "price": 100, "qty": 1, "leverage": 2}, rows,
             "unknown fields")


def test_rejects_an_unusable_ticker_symbol():
    rows = _rows("sample_momentum.csv")
    _rejects({"ticker": "nv da!", "side": "buy", "price": 100, "qty": 1}, rows,
             "not a usable engine symbol")


def test_rejects_a_non_dict_premise():
    _rejects("not a dict", [], "premise must be an object")


def test_date_defaults_to_the_day_after_the_last_ledger_row():
    rows = _rows("sample_momentum.csv")   # last row: 2024-06-18
    normalized = cq.validate_premise({"ticker": "NVDA", "side": "buy", "price": 100, "qty": 1}, rows)
    assert normalized["date"].isoformat() == "2024-06-19"


def test_currency_defaults_from_the_ledgers_own_currency_for_a_held_ticker():
    rows = _rows("sample_tw_mixed.csv")
    normalized = cq.validate_premise({"ticker": "2330.TW", "side": "buy", "price": 600, "qty": 100}, rows)
    assert normalized["currency"] == "TWD"
    normalized_us = cq.validate_premise({"ticker": "AAPL", "side": "buy", "price": 190, "qty": 10}, rows)
    assert normalized_us["currency"] == "USD"


def test_a_sell_is_refused_on_an_empty_ledger():
    _rejects({"ticker": "NVDA", "side": "sell", "price": 100, "qty": 1}, [], "not currently held")


# ───────────────── C. core arithmetic ─────────────────

def test_buy_raises_the_targets_weight_matching_an_independent_calculation():
    """sample_momentum.csv: AMD 30sh/$4800, MRVL 70sh/$4900, NVDA 120sh/$14400
    (cost basis, no last_px). Total cost 24100; NVDA weight = 14400/24100 =
    0.5975103734439834. A buy of notional = 20% of the total cost base
    (4820.0) should move it to (14400+4820)/(24100+4820) =
    19220/28920 = 0.6645919778699861 — computed independently here, not read
    back from the function under test."""
    rows = _rows("sample_momentum.csv")
    total_cost = 4800.0 + 4900.0 + 14400.0
    assert total_cost == 24100.0
    premise = {"ticker": "NVDA", "side": "buy", "price": 120.0, "notional": total_cost * 0.2}
    result = cq.consequence(rows, premise)
    assert _close(result["before"]["max_pct"], 0.5975103734439834)
    expected_after = (14400.0 + 4820.0) / (24100.0 + 4820.0)
    assert _close(result["after"]["max_pct"], expected_after)
    assert _close(result["after"]["max_pct"], 0.6645919778699861)
    assert result["after"]["max_ticker"] == "NVDA"
    assert _close(result["delta"]["max_pct"], expected_after - 0.5975103734439834)


def test_sell_lowers_the_targets_weight_matching_an_independent_calculation():
    """Selling half the NVDA position (60 of 120 shares, cost basis halves
    proportionally to 7200) should move NVDA's weight to
    (14400-7200)/(24100-7200) = 7200/16900 = 0.42603550295857984."""
    rows = _rows("sample_momentum.csv")
    premise = {"ticker": "NVDA", "side": "sell", "price": 130.0, "qty": 60.0}
    result = cq.consequence(rows, premise)
    expected_after = (14400.0 - 7200.0) / (24100.0 - 7200.0)
    assert _close(result["after"]["max_pct"], expected_after)
    assert _close(result["after"]["max_pct"], 0.4260355029585799)
    assert result["delta"]["max_pct"] < 0, "a sell must lower the weight, not raise it"
    assert result["after"]["oversize_triggered"] is True, "60% remains well over the 25% trigger"


def test_notional_and_qty_forms_of_the_same_trade_produce_identical_results():
    rows = _rows("sample_momentum.csv")
    premise_qty = {"ticker": "NVDA", "side": "buy", "price": 120.0, "qty": 40.166666666666664}
    premise_notional = {"ticker": "NVDA", "side": "buy", "price": 120.0, "notional": 4820.0}
    result_qty = cq.consequence(rows, premise_qty)
    result_notional = cq.consequence(rows, premise_notional)
    assert result_qty["premise"]["qty"] == result_notional["premise"]["qty"]
    assert result_qty["after"] == result_notional["after"]
    assert result_qty["delta"] == result_notional["delta"]


def test_a_brand_new_position_raises_n_holdings():
    rows = _rows("sample_momentum.csv")   # AAPL is not among AMD/MRVL/NVDA
    premise = {"ticker": "AAPL", "side": "buy", "price": 190.0, "qty": 10.0}
    result = cq.consequence(rows, premise)
    assert result["before"]["n_holdings"] == 3
    assert result["after"]["n_holdings"] == 4
    assert result["delta"]["n_holdings"] == 1
    assert _close(result["delta"]["ticker_weight"], result["after"]["weights"]["AAPL"])


def test_sector_concentration_moves_with_an_independently_computed_expectation():
    """sample_ai_holder.csv holds NVDA/AVGO/TSM (半導體) and MSFT/GOOGL/PLTR
    (軟體雲), cost basis: 半導體 = 24900+7200+3600 = 35700, 軟體雲 =
    16800+15200+4800 = 36800, total 72500 -> max_sector is 軟體雲 at
    36800/72500 = 0.5075862068965518. Buying 200 more TSM at 180 (notional
    36000) raises 半導體 to 35700+36000=71700 against a new total of 108500:
    71700/108500 = 0.6608294930875576, flipping max_sector to 半導體."""
    rows = _rows("sample_ai_holder.csv")
    before = cq.portfolio_state(rows)
    assert before["max_sector"] == "軟體雲"
    assert _close(before["max_sector_pct"], 36800.0 / 72500.0)
    premise = {"ticker": "TSM", "side": "buy", "price": 180.0, "qty": 200.0}
    result = cq.consequence(rows, premise)
    assert result["after"]["max_sector"] == "半導體"
    assert _close(result["after"]["max_sector_pct"], 71700.0 / 108500.0)
    assert _close(result["after"]["max_sector_pct"], 0.6608294930875576)
    assert _close(result["delta"]["max_sector_pct"],
                 71700.0 / 108500.0 - 36800.0 / 72500.0)


# ───────────────── D. disclosures ─────────────────

def test_cost_basis_vs_priced_runs_differ_in_basis_and_disclosure():
    rows = _rows("sample_pyramid.csv")
    premise = {"ticker": "COST", "side": "buy", "price": 500.0, "qty": 5.0}
    cost_result = cq.consequence(rows, premise)
    assert cost_result["after"]["basis"] == "cost"
    assert "cost_basis" in cost_result["disclosures"]

    priced_result = cq.consequence(rows, premise, last_px={"COST": 700.0, "UNH": 550.0})
    assert priced_result["after"]["basis"] == "priced"
    assert "cost_basis" not in priced_result["disclosures"]
    # basis is not merely a label: cost vs priced must produce different weights.
    assert cost_result["after"]["max_pct"] != priced_result["after"]["max_pct"]


def test_unreliable_cash_produces_its_disclosure_key():
    rows = _rows("sample_pyramid.csv")
    premise = {"ticker": "COST", "side": "buy", "price": 500.0, "qty": 5.0}
    result = cq.consequence(rows, premise)   # no cash_anchor supplied
    assert result["after"]["cash"]["reliable"] is False
    assert result["after"]["cash"]["source"] == "csv_sum"
    assert "cash_unreliable" in result["disclosures"]


def test_an_anchored_cash_balance_has_no_unreliable_disclosure():
    rows = _rows("sample_pyramid.csv")
    premise = {"ticker": "COST", "side": "buy", "price": 500.0, "qty": 5.0}
    anchor = {"as_of": rows[-1]["date"].isoformat(), "amount": 5000.0}
    result = cq.consequence(rows, premise, cash_anchor=anchor)
    assert result["after"]["cash"]["reliable"] is True
    assert result["after"]["cash"]["source"] == "anchored"
    assert "cash_unreliable" not in result["disclosures"]


def test_an_unmapped_driver_ticker_produces_its_disclosure_key():
    """COST/UNH are not in trade_recap's DRIVER_FALLBACK table and no
    driver_map sidecar is loaded in this process, so driver() honestly
    returns the "未分類" fallback."""
    rows = _rows("sample_pyramid.csv")
    assert tr.driver("COST") == ("未分類", 0)
    premise = {"ticker": "COST", "side": "buy", "price": 500.0, "qty": 5.0}
    result = cq.consequence(rows, premise)
    assert "unmapped_driver" in result["disclosures"]


def test_a_mapped_driver_ticker_has_no_unmapped_disclosure():
    """Contrast case: NVDA is in the built-in DRIVER_FALLBACK table, so the
    same premise shape on a fixture that holds it must not raise the key."""
    rows = _rows("sample_momentum.csv")
    assert tr.driver("NVDA")[0] != "未分類"
    premise = {"ticker": "NVDA", "side": "buy", "price": 120.0, "qty": 1.0}
    result = cq.consequence(rows, premise)
    assert "unmapped_driver" not in result["disclosures"]


def test_mixed_currency_without_an_fx_map_produces_its_disclosure_key():
    rows = _rows("sample_tw_mixed.csv")   # TWD + USD
    premise = {"ticker": "AAPL", "side": "buy", "price": 190.0, "qty": 10.0}
    result = cq.consequence(rows, premise)
    assert result["after"]["mixed_currency"] is True
    assert result["after"]["fx_gaps"] == ["TWD"]
    assert "mixed_currency_no_fx" in result["disclosures"]


def test_mixed_currency_with_a_covering_fx_map_has_no_disclosure_key():
    rows = _rows("sample_tw_mixed.csv")
    premise = {"ticker": "AAPL", "side": "buy", "price": 190.0, "qty": 10.0}
    result = cq.consequence(rows, premise, fx={"TWD": 0.031})
    assert result["after"]["fx_gaps"] == []
    assert "mixed_currency_no_fx" not in result["disclosures"]


# ───────────────── E. rule_collision ─────────────────

def test_evaluable_metrics_return_would_breach_only_when_the_after_state_crosses_the_line():
    """sample_fundamental.csv, cost basis: max_pct starts at 0.17665... (JNJ),
    well under the 25% oversize trigger, and concentration is untriggered
    (top3 0.5206, under 60%). A small buy (notional 100) must stay clear on
    both; a large buy (notional 5000, comfortably over the 2706.67 breakeven
    that puts AAPL's weight over 25%) must flip both to would_breach."""
    rows = _rows("sample_fundamental.csv")
    before = cq.portfolio_state(rows)
    assert before["oversize_triggered"] is False
    assert before["concentration_triggered"] is False
    rules_report = ([_rule("max_pos_pct"), _rule("ai_pct")], [], 0)

    small = {"ticker": "AAPL", "side": "buy", "price": 180.0, "notional": 100.0}
    small_out = cq.rule_collision(rows, small, rules_report)
    assert {row["metric_key"]: row["state"] for row in small_out} == {
        "max_pos_pct": "clear", "ai_pct": "clear"}

    large = {"ticker": "AAPL", "side": "buy", "price": 180.0, "notional": 5000.0}
    large_out = cq.rule_collision(rows, large, rules_report)
    assert {row["metric_key"]: row["state"] for row in large_out} == {
        "max_pos_pct": "would_breach", "ai_pct": "would_breach"}
    # cross-check against the same fields consequence() itself reports
    large_result = cq.consequence(rows, large)
    assert large_result["after"]["oversize_triggered"] is True
    assert large_result["after"]["concentration_triggered"] is True


def test_avgdown_would_breach_for_a_qualifying_average_down_that_breaches_weight():
    """sample_pyramid.csv: COST held 20sh/$12500 (avg cost 625/share); 90% of
    that is 562.5. A buy at 500 (< 562.5) qualifies as an average-down, and
    COST's pre-trade cost weight (12500 / (12500+10400 UNH) = 0.5460...) is
    well over trade_recap.AVGDOWN_BREACH_W (0.25), so it must breach."""
    rows = _rows("sample_pyramid.csv")
    rules_report = ([_rule("avgdown_count")], [], 0)
    premise = {"ticker": "COST", "side": "buy", "price": 500.0, "qty": 5.0}
    out = cq.rule_collision(rows, premise, rules_report)
    assert out[0]["state"] == "would_breach"


def test_avgdown_clear_for_a_mild_dip_a_sell_and_a_brand_new_position():
    rows = _rows("sample_pyramid.csv")
    rules_report = ([_rule("avgdown_count")], [], 0)
    # 580 is above 90% of the 625 avg cost (562.5): does not even qualify.
    mild = cq.rule_collision(rows, {"ticker": "COST", "side": "buy", "price": 580.0, "qty": 5.0},
                             rules_report)
    assert mild[0]["state"] == "clear"
    # A sell can never be an average-down.
    sell = cq.rule_collision(rows, {"ticker": "COST", "side": "sell", "price": 700.0, "qty": 5.0},
                             rules_report)
    assert sell[0]["state"] == "clear"
    # A brand-new position has no prior avg cost to average down from.
    new_pos = cq.rule_collision(rows, {"ticker": "AAPL", "side": "buy", "price": 50.0, "qty": 5.0},
                                rules_report)
    assert new_pos[0]["state"] == "clear"


def test_exit_and_hold_severity_are_unjudged_never_a_pass():
    """The load-bearing assertion in this file: exit_severity/hold_severity
    describe realized selling/holding behaviour across history that one
    hypothetical trade cannot settle. They must never come back "clear"
    (which a naive reader could mistake for "held"/passing) — they must
    come back as the distinct unjudged state."""
    rows = _rows("sample_momentum.csv")
    rules_report = ([_rule("exit_severity"), _rule("hold_severity")], [], 0)
    premise = {"ticker": "NVDA", "side": "buy", "price": 120.0, "qty": 1.0}
    out = cq.rule_collision(rows, premise, rules_report)
    states = {row["metric_key"]: row["state"] for row in out}
    assert states == {"exit_severity": "unjudged", "hold_severity": "unjudged"}
    assert "clear" not in states.values() and "would_breach" not in states.values()


def test_a_metric_key_with_no_problem_key_mapping_is_unmapped():
    rows = _rows("sample_momentum.csv")
    rules_report = ([_rule("made_up_metric"), _rule(None, rule_id="rule-no-metric")], [], 0)
    premise = {"ticker": "NVDA", "side": "buy", "price": 120.0, "qty": 1.0}
    out = cq.rule_collision(rows, premise, rules_report)
    assert [row["state"] for row in out] == ["unmapped", "unmapped"]


def test_every_pkey_metric_key_is_classified_as_evaluable_or_falls_to_unjudged():
    """All seven session.PKEY metric keys must come back as a real verdict or
    unjudged — never unmapped, since every one of them does have a
    problem_key by construction."""
    rows = _rows("sample_momentum.csv")
    rules_report = ([_rule(key) for key in session.PKEY], [], 0)
    premise = {"ticker": "NVDA", "side": "buy", "price": 120.0, "qty": 1.0}
    out = cq.rule_collision(rows, premise, rules_report)
    for row in out:
        assert row["state"] != "unmapped", f"a PKEY-mapped metric_key must not be unmapped: {row}"
        assert row["state"] in cq.COLLISION_STATES


def test_muted_rules_are_excluded_from_the_rotation():
    rows = _rows("sample_momentum.csv")
    tracking = [_rule("max_pos_pct", rule_id="r-tracking")]
    muted = [_rule("ai_pct", rule_id="r-muted")]
    rules_report = (tracking, muted, 0)
    premise = {"ticker": "NVDA", "side": "buy", "price": 120.0, "qty": 1.0}
    out = cq.rule_collision(rows, premise, rules_report)
    assert [row["rule_id"] for row in out] == ["r-tracking"], \
        "a muted rule must not be evaluated: 'currently-tracked' excludes it"


# ───────────────── F. rule_collision — causal attribution vs book state ─────────────────
# sample_momentum.csv's book is already oversized before any of these premises
# (NVDA alone is 0.5975, the 0.25 trigger line is trade_recap.OVERSIZE_TRIGGER):
# these cases prove would_breach cannot be read off the after state's triggered
# flag alone, or every one of them — including the two that improve the book —
# would wrongly come back would_breach.

def test_an_improving_sell_of_an_already_over_position_is_already_over_not_would_breach():
    """The product failure this section exists to prevent: selling a third of
    the oversized NVDA position (120 -> 80 shares) drops NVDA's own weight
    from 0.5975 to 0.4974 and the book's max_pct along with it — a real
    improvement — but NVDA (still the book max) remains over the 0.25 line on
    both sides, so this is the book's pre-existing condition, not a fresh
    breach this trade caused."""
    rows = _rows("sample_momentum.csv")
    rules_report = ([_rule("max_pos_pct")], [], 0)
    premise = {"ticker": "NVDA", "side": "sell", "price": 130.0, "qty": 40.0}
    result = cq.consequence(rows, premise)
    assert _close(result["before"]["weights"]["NVDA"], 0.5975103734439834)
    assert _close(result["after"]["weights"]["NVDA"], 0.49740932642487046)
    assert result["after"]["oversize_triggered"] is True

    out = cq.rule_collision(rows, premise, rules_report)
    assert out[0]["state"] == "already_over", \
        f"an improving sell of an oversized position must not read would_breach: {out[0]}"
    assert out[0]["worsens"] is False, "the book's max_pct fell; this trade did not worsen it"


def test_a_small_unrelated_buy_on_an_already_over_book_is_already_over_not_would_breach():
    """A tiny buy of MRVL (0.2033 -> 0.2079, nowhere near the 0.25 line on its
    own) on a book already oversized by NVDA. The premise ticker never
    crosses anything, so attributing would_breach to it would blame this
    trade for a condition NVDA created. The book's max_pct even improves
    slightly (0.5975 -> 0.5941, since the denominator grows) — still not
    worsening."""
    rows = _rows("sample_momentum.csv")
    rules_report = ([_rule("max_pos_pct")], [], 0)
    premise = {"ticker": "MRVL", "side": "buy", "price": 70.0, "qty": 2.0}
    result = cq.consequence(rows, premise)
    assert result["before"]["weights"]["MRVL"] < 0.25 and result["after"]["weights"]["MRVL"] < 0.25
    assert result["after"]["max_pct"] < result["before"]["max_pct"], "the book's max_pct improves here too"

    out = cq.rule_collision(rows, premise, rules_report)
    assert out[0]["state"] == "already_over"
    assert out[0]["worsens"] is False


def test_a_buy_that_crosses_the_premise_tickers_own_line_on_a_clear_book_is_would_breach():
    """sample_fundamental.csv's book is clear before this trade
    (oversize_triggered False, every position under 0.25). A buy sized to
    push AAPL's own weight from 0.1592 past the 0.25 line to 0.3129 is what
    this state exists to name: a fresh, attributable breach."""
    rows = _rows("sample_fundamental.csv")
    rules_report = ([_rule("max_pos_pct")], [], 0)
    premise = {"ticker": "AAPL", "side": "buy", "price": 180.0, "notional": 5000.0}
    result = cq.consequence(rows, premise)
    assert result["before"]["oversize_triggered"] is False
    assert result["before"]["weights"]["AAPL"] < 0.25 < result["after"]["weights"]["AAPL"]

    out = cq.rule_collision(rows, premise, rules_report)
    assert out[0]["state"] == "would_breach"
    assert out[0]["worsens"] is None, "worsens only applies to already_over"


def test_a_buy_that_digs_an_already_over_book_deeper_is_already_over_worsens_true():
    """Buying 20 more shares of the already-oversized NVDA position (120 ->
    140 shares) raises both NVDA's own weight and the book's max_pct
    (0.5975 -> 0.6367): still the same pre-existing line, but this time this
    trade genuinely makes it worse."""
    rows = _rows("sample_momentum.csv")
    rules_report = ([_rule("max_pos_pct")], [], 0)
    premise = {"ticker": "NVDA", "side": "buy", "price": 130.0, "qty": 20.0}
    result = cq.consequence(rows, premise)
    assert _close(result["after"]["max_pct"], 0.6367041198501873)
    assert result["after"]["max_pct"] > result["before"]["max_pct"]

    out = cq.rule_collision(rows, premise, rules_report)
    assert out[0]["state"] == "already_over"
    assert out[0]["worsens"] is True


def test_concentration_trio_worsens_reads_its_own_metric_not_the_shared_flag():
    """sample_ai_holder.csv is already concentration_triggered before this
    trade (top3 0.7848 > 0.60) and stays triggered after buying 200 more TSM
    — both metric_keys land on already_over off the same shared flag, but
    their own readings move oppositely: max_sector_pct actually rises
    (0.5076 -> 0.6608, worsens True) while ai_pct sits at its 1.0 ceiling on
    both sides (unchanged, worsens False). If worsens read the shared
    triggered flag instead of each metric's own field, both would say the
    same thing; they must not."""
    rows = _rows("sample_ai_holder.csv")
    before = cq.portfolio_state(rows)
    assert before["concentration_triggered"] is True
    rules_report = ([_rule("max_sector_pct"), _rule("ai_pct")], [], 0)
    premise = {"ticker": "TSM", "side": "buy", "price": 180.0, "qty": 200.0}

    out = cq.rule_collision(rows, premise, rules_report)
    by_key = {row["metric_key"]: row for row in out}
    assert by_key["max_sector_pct"]["state"] == "already_over"
    assert by_key["max_sector_pct"]["worsens"] is True
    assert by_key["ai_pct"]["state"] == "already_over"
    assert by_key["ai_pct"]["worsens"] is False


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
