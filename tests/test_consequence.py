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
  D. consequence: the disclosure keys (cost_basis, cash_unreliable,
     unmapped_driver) each fire under their own condition and stay absent
     otherwise, and a book whose currencies cannot be converted is refused
     rather than disclosed (#600 — the retired mixed_currency_no_fx).
  D2. consequence: the two book-level legibility keys (#598's
     unclassified_book, #599's etf_not_decomposed) name which positions the
     concentration figures could not read and at what weight, stay disjoint,
     respect #172's residual floor, and are both silent on a book that is
     fully legible.
  D3. portfolio_state / consequence (#751): the hypothetical premise's own
     cash flow is deducted (or credited, for a sell) from an anchored balance
     regardless of how the cash anchor's as_of relates to the premise's own
     date — before, on, or after it — closing the silent non-deduction a
     postdated or same-day anchor produced. The unanchored csv_sum path and
     every caller that does not opt in via `premise_row` are pinned as
     unaffected.
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
  G. rule_collision's concentration causality is judged per metric_key
     against that metric's own line, never dim_diversify's shared triggered
     flag: a fresh cross of one metric is not hidden behind a sibling metric
     already being over a different line (external review MAJOR 1), and a
     fresh cross of max_sector_pct's own 40% line is not reported clear just
     because dim_diversify's shared flag additionally gates on >= 8 holdings
     (external review MAJOR 2 — a false negative). The lines this module
     compares against are read live from trade_recap's own named constants,
     never a copied literal, so the two cannot drift apart.

All fixtures are read from skills/fomo-kernel/mock/*.csv, except section G's
two tests, which reproduce the external reviewer's counterexample books
verbatim (exact tickers and dollar amounts) rather than paraphrasing them.
Every asserted number below was measured by running the code, not guessed
(see PR description / session notes for the probe transcript).
"""
import datetime as dt
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


def _dollar_book(holdings, date="2024-01-01"):
    """One buy row per (ticker, dollar_amount) pair, qty=1.0 so price IS the
    dollar amount — the exact shape the external reviewer's counterexamples
    were stated in ("NVDA $50, MSTR $30, ..."), reproduced verbatim rather
    than translated into an existing CSV fixture that might not isolate the
    same condition."""
    d = dt.date.fromisoformat(date)
    return [dict(ticker=ticker, side="buy", qty=1.0, price=float(amount), date=d,
                market="US", currency="USD") for ticker, amount in holdings]


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


def test_mixed_currency_without_an_fx_map_is_refused_not_disclosed():
    """#600. The old behaviour summed TWD face values into a USD denominator
    at a factor of 1.0 and attached `mixed_currency_no_fx`, whose own wording
    said "aggregate figures are incomplete". They were not incomplete, they
    were a different book: at ~31 TWD to the dollar the smaller holding reads
    as the larger one. A reader cannot recover that from a disclosure, so the
    state is refused, matching #497's canonical lane and AGENTS.md boundary 6."""
    rows = _rows("sample_tw_mixed.csv")   # TWD + USD
    premise = {"ticker": "AAPL", "side": "buy", "price": 190.0, "qty": 10.0}
    try:
        cq.consequence(rows, premise)
    except cq.ConsequenceError as exc:
        message = str(exc)
    else:
        raise AssertionError("a mixed-currency book with no FX rate must not produce a state")
    assert "TWD" in message, message
    # The remedy is actionable in one round trip, and the message says so
    # rather than only naming the fault.
    assert "--prices" in message, message
    assert "mixed_currency_no_fx" not in cq.DISCLOSURES


def test_a_no_longer_emitted_disclosure_is_still_declared_as_retired():
    """A key removed from DISCLOSURES without being written down anywhere
    would make the stored-row enum look like an oversight rather than a
    deliberate replay carve-out, and the next cleanup would delete it."""
    assert "mixed_currency_no_fx" in cq.RETIRED_DISCLOSURES


def test_mixed_currency_with_a_covering_fx_map_still_answers():
    rows = _rows("sample_tw_mixed.csv")
    premise = {"ticker": "AAPL", "side": "buy", "price": 190.0, "qty": 10.0}
    result = cq.consequence(rows, premise, fx={"TWD": 0.031})
    assert result["after"]["mixed_currency"] is True
    # `mixed_currency: true` survives as a statement about how the denominator
    # was built, not as a limitation — everything here was converted at the
    # caller's own rate before being added.
    assert "fx_gaps" not in result["after"]
    assert 0.0 < result["after"]["max_pct"] <= 1.0


def test_a_partially_covering_fx_map_is_refused_naming_only_the_missing_one():
    """A book in three currencies with two rates supplied is the same defect as
    one with none: the third still enters the denominator at face value."""
    rows = _rows("sample_tw_mixed.csv")
    premise = {"ticker": "AAPL", "side": "buy", "price": 190.0, "qty": 10.0}
    try:
        cq.consequence(rows, premise, fx={"EUR": 1.08})
    except cq.ConsequenceError as exc:
        assert "TWD" in str(exc)
        assert "EUR" not in str(exc), "a currency this book does not hold is not the fault"
    else:
        raise AssertionError("an fx map that misses a held currency must be refused")


def test_a_single_currency_book_never_needs_an_fx_rate():
    """The counterweight. #600's refusal must not reach a book that is
    self-consistent without any conversion at all — a whole-TWD book included,
    matching trade_recap's own single-currency convention."""
    rows = _rows("sample_momentum.csv")   # USD only
    premise = {"ticker": "NVDA", "side": "buy", "price": 120.0, "qty": 1.0}
    result = cq.consequence(rows, premise)
    assert result["after"]["mixed_currency"] is False


# ───────────── D2. what the concentration figures could not read ─────────────
# #598 / #599. Every assertion below was measured by running the code against
# these fixtures, not predicted.

def test_a_state_carries_what_its_own_concentration_readings_could_not_read():
    """The rule this section exists to hold, and the one that was broken when
    these lists lived in `consequence()` alone.

    A caller who only wants "what does my book look like right now" calls
    `portfolio_state` and never touches `consequence()`. Probed with a throwaway
    `cmd_exposure` subcommand written the obvious way — read the book, print the
    weights and `ai_pct` — that caller got the readings and none of the
    limitations, with the whole offline suite green. So the answer travels in
    the same dict as the question: any consumer of a state gets both or neither.
    """
    rows = _rows("sample_pyramid.csv")
    state = cq.portfolio_state(rows)
    assert "ai_pct" in state and "max_sector_pct" in state
    # Membership first, so a state that stopped carrying these reports as this
    # assertion rather than as a KeyError that halts the rest of the file.
    assert {"unclassified_holdings", "undecomposed_etfs"} <= set(state), (
        "a state reports concentration readings without the positions they were "
        f"measured without: {sorted(state)}")
    assert [row["ticker"] for row in state["unclassified_holdings"]] == ["COST", "UNH"]
    assert state["undecomposed_etfs"] == []
    # And a state is self-describing: the named weights are that state's own,
    # never a denominator some other reader computed.
    for row in state["unclassified_holdings"]:
        assert _close(row["weight"], state["weights"][row["ticker"]])


def test_consequence_reports_exactly_what_the_after_state_already_answered():
    """One derivation, not two. `consequence()` reads the lists `after` carries
    rather than asking again — the second ask is how a state and the disclosure
    beside it come to describe the same book differently."""
    rows = _rows("sample_pyramid.csv")
    premise = {"ticker": "COST", "side": "buy", "price": 500.0, "qty": 5.0}
    result = cq.consequence(rows, premise)
    assert result["unclassified_holdings"] == result["after"]["unclassified_holdings"]
    assert result["undecomposed_etfs"] == result["after"]["undecomposed_etfs"]
    # `before` answers for its own book, which is a different one — the premise
    # has not joined it. Both are correct; only `after` qualifies the numbers
    # this result reports.
    assert "unclassified_holdings" in result["before"]


def test_an_unclassified_held_position_is_named_with_the_weight_it_carries():
    """#598's silent half. `unmapped_driver` above has only ever looked at the
    premise's own ticker, so a book could carry large unclassified positions —
    each contributing zero to ai_pct and dropped from max_sector_pct's
    numerator — with nothing saying the figures were measured over part of the
    book. COST and UNH are absent from DRIVER_FALLBACK, so this fixture is
    entirely unclassified and its concentration figures are entirely silent."""
    rows = _rows("sample_pyramid.csv")
    premise = {"ticker": "COST", "side": "buy", "price": 500.0, "qty": 5.0}
    result = cq.consequence(rows, premise)
    assert "unclassified_book" in result["disclosures"]
    named = result["unclassified_holdings"]
    assert [row["ticker"] for row in named] == ["COST", "UNH"], named
    # Sorted by weight descending, so the largest thing the numbers could not
    # read is the first thing an answer reaches for.
    assert named[0]["weight"] > named[1]["weight"]
    assert _close(sum(row["weight"] for row in named), 1.0, tol=1e-9), (
        "this whole book is unclassified, so the named weights must exhaust it")
    # And the figures the disclosure qualifies really are the silent ones.
    assert result["after"]["ai_pct"] == 0.0
    assert result["after"]["max_sector_pct"] == 0


def test_a_fully_classified_book_carries_neither_book_level_key():
    """The counterweight: a disclosure that fires on every book says nothing.
    Every holding here is in DRIVER_FALLBACK and none of them is a fund."""
    rows = _rows("sample_ai_holder.csv")
    premise = {"ticker": "NVDA", "side": "buy", "price": 120.0, "qty": 1.0}
    result = cq.consequence(rows, premise)
    assert result["unclassified_holdings"] == []
    assert result["undecomposed_etfs"] == []
    assert "unclassified_book" not in result["disclosures"]
    assert "etf_not_decomposed" not in result["disclosures"]


def test_a_held_fund_is_named_as_undecomposed_with_how_it_was_treated():
    """#599. Nothing in this engine looks through a fund, and the two ways it
    fails to are opposite: an allocation kind leaves the concentration
    numerator wholesale, a sector/thematic kind counts as one opaque ticker.
    An answer that says which one happened is saying something different about
    the same weight, so both facts travel."""
    rows = _dollar_book([("SPY", 50.0), ("SOXX", 30.0), ("NVDA", 20.0)])
    premise = {"ticker": "NVDA", "side": "buy", "price": 1.0, "qty": 1.0}
    result = cq.consequence(rows, premise)
    assert "etf_not_decomposed" in result["disclosures"]
    by_ticker = {row["ticker"]: row for row in result["undecomposed_etfs"]}
    assert set(by_ticker) == {"SPY", "SOXX"}, by_ticker
    assert by_ticker["SPY"]["kind"] == "broad_market_etf"
    assert by_ticker["SPY"]["allocation_exempt"] is True
    assert by_ticker["SOXX"]["kind"] == "thematic_etf"
    assert by_ticker["SOXX"]["allocation_exempt"] is False
    # The defect in one line: a textbook semiconductor fund is 30% of this
    # book and the AI reading cannot see a cent of it.
    assert result["after"]["ai_pct"] < 0.30


def test_a_fund_is_never_owed_twice_by_both_book_level_keys():
    """The two lists are disjoint by construction. SOXX is a recognized fund
    AND has no DRIVER_FALLBACK entry, so a naive implementation names it in
    both and the answer owes two sentences about one position — the second of
    which ("no sector classification") points at the wrong remedy, since a
    driver map cannot make a fund's constituents visible."""
    rows = _dollar_book([("SOXX", 60.0), ("NVDA", 40.0)])
    premise = {"ticker": "NVDA", "side": "buy", "price": 1.0, "qty": 1.0}
    result = cq.consequence(rows, premise)
    unclassified = {row["ticker"] for row in result["unclassified_holdings"]}
    etfs = {row["ticker"] for row in result["undecomposed_etfs"]}
    assert not unclassified & etfs
    assert etfs == {"SOXX"}


def test_a_fund_the_instrument_map_does_not_know_reads_as_an_unclassified_name():
    """The real #599 case, and why the split above is where it is. A regional
    active fund absent from instruments.FALLBACK is not an ETF as far as this
    engine is concerned: it lands in the #598 list, where --instrument-map is
    the half of the remedy that moves it. What it must never be is silent."""
    rows = _dollar_book([("0056.TW", 70.0), ("NVDA", 30.0)])
    premise = {"ticker": "NVDA", "side": "buy", "price": 1.0, "qty": 1.0}
    result = cq.consequence(rows, premise)
    assert [row["ticker"] for row in result["unclassified_holdings"]] == ["0056.TW"]
    assert result["undecomposed_etfs"] == []


def test_the_premise_ticker_joins_the_book_these_keys_describe():
    """Read off `after`, not `before`: a user asking to buy a name nothing can
    classify is being told about the book that trade produces."""
    rows = _dollar_book([("NVDA", 100.0)])
    result = cq.consequence(rows, {"ticker": "CPRT", "side": "buy",
                                   "price": 50.0, "qty": 1.0})
    assert [row["ticker"] for row in result["unclassified_holdings"]] == ["CPRT"]


def test_a_residual_position_does_not_buy_the_answer_a_sentence_about_dust():
    """Both lists apply #172's residual floor. A required disclosure is a
    sentence the answer owes; a position under a tenth of a percent moves no
    concentration figure and does not earn one."""
    rows = _dollar_book([("NVDA", 100000.0), ("CPRT", 50.0)])
    premise = {"ticker": "NVDA", "side": "buy", "price": 1.0, "qty": 1.0}
    result = cq.consequence(rows, premise)
    assert result["after"]["weights"]["CPRT"] < tr.RESIDUAL_POS_TH
    assert result["unclassified_holdings"] == []
    assert "unclassified_book" not in result["disclosures"]
    # ... and the same position above the floor is named, so the test above
    # is not passing because the list is simply never populated.
    bigger = cq.consequence(_dollar_book([("NVDA", 100.0), ("CPRT", 50.0)]), premise)
    assert [row["ticker"] for row in bigger["unclassified_holdings"]] == ["CPRT"]


# ─────── D3. cash anchor vs. the hypothetical trade's own date (#751) ───────
# A cash anchor states a real balance that, by construction, has never seen a
# trade that has not happened yet. trade_recap._cash_balance_one_ccy sums cash
# flows dated strictly after the anchor's as_of; before #751, the appended
# hypothetical row was just one more dated flow, so an anchor whose as_of
# landed on or after the premise's own date made the filter skip the
# hypothetical's cash effect too -- after.cash.balance came back identical to
# before's, reliable:true, with no disclosure anywhere saying so. The four
# consequence()-level tests below build the same one-holding book and vary
# only the anchor's as_of relative to the premise's date, which is what
# isolates the mechanism; the last test pins the fix's own opt-in parameter
# one level below consequence().

def _one_holding_book():
    """AAA bought for $1000 on 2024-01-01. A premise with no explicit `date`
    defaults to 2024-01-02 (validate_premise: last row + 1 day)."""
    return _dollar_book([("AAA", 1000.0)], date="2024-01-01")


def test_an_anchor_strictly_before_the_premise_date_deducts_correctly():
    """Regression guard for the case that already worked: anchor as_of
    (2023-12-31) is before both the historical AAA buy and the premise, so
    both flow into the balance in the ordinary way. 10000 - 1000 (AAA) - 500
    (premise) = 8500."""
    rows = _one_holding_book()
    anchor = {"as_of": "2023-12-31", "amount": 10000.0, "currency": "USD"}
    premise = {"ticker": "BBB", "side": "buy", "price": 500.0, "qty": 1.0}
    result = cq.consequence(rows, premise, cash_anchor=anchor)
    assert result["premise"]["date"].isoformat() == "2024-01-02"
    assert _close(result["before"]["cash"]["balance"], 9000.0)
    assert _close(result["after"]["cash"]["balance"], 8500.0)
    assert result["after"]["cash"]["reliable"] is True
    assert _close(result["delta"]["cash"]["balance"], -500.0)


def test_an_anchor_dated_the_same_day_as_the_premise_still_deducts():
    """The exact regression the issue's own follow-up pinned: anchor as_of
    (2024-01-02) equals the premise's defaulted date. Before #751 this was
    silently unreduced (after == before == 10000.0, reliable:true, no
    disclosure); the fix must deduct the premise's own $500 cost regardless."""
    rows = _one_holding_book()
    anchor = {"as_of": "2024-01-02", "amount": 10000.0, "currency": "USD"}
    premise = {"ticker": "BBB", "side": "buy", "price": 500.0, "qty": 1.0}
    result = cq.consequence(rows, premise, cash_anchor=anchor)
    assert result["premise"]["date"].isoformat() == "2024-01-02"
    assert _close(result["before"]["cash"]["balance"], 10000.0)
    assert _close(result["after"]["cash"]["balance"], 9500.0), \
        "the premise's own cost must be deducted even when the anchor postdates it exactly"
    assert result["after"]["cash"]["reliable"] is True
    assert "cash_unreliable" not in result["disclosures"]
    assert _close(result["delta"]["cash"]["balance"], -500.0)


def test_an_anchor_dated_after_the_premise_still_deducts():
    """The issue's original repro shape: anchor as_of (2024-01-05) is later
    still than the premise's defaulted date. Same fix, same expectation."""
    rows = _one_holding_book()
    anchor = {"as_of": "2024-01-05", "amount": 10000.0, "currency": "USD"}
    premise = {"ticker": "BBB", "side": "buy", "price": 500.0, "qty": 1.0}
    result = cq.consequence(rows, premise, cash_anchor=anchor)
    assert _close(result["before"]["cash"]["balance"], 10000.0)
    assert _close(result["after"]["cash"]["balance"], 9500.0)
    assert result["after"]["cash"]["reliable"] is True


def test_a_sell_premise_still_credits_proceeds_against_a_postdated_anchor():
    """Sign correctness the other direction: a sell's proceeds must be added,
    not skipped, under the same postdated-anchor condition. AAA is held (1
    share, cost basis $1000 here since _dollar_book's qty is 1.0); selling it
    at $1200 credits the cash balance by exactly $1200."""
    rows = _one_holding_book()
    anchor = {"as_of": "2024-01-02", "amount": 10000.0, "currency": "USD"}
    premise = {"ticker": "AAA", "side": "sell", "price": 1200.0, "qty": 1.0}
    result = cq.consequence(rows, premise, cash_anchor=anchor)
    assert _close(result["after"]["cash"]["balance"], 11200.0)
    assert _close(result["delta"]["cash"]["balance"], 1200.0)


def test_no_anchor_still_sums_the_premises_own_flow_regardless_of_date():
    """The unanchored csv_sum path never date-filtered at all, so it was
    never the #751 bug's carrier -- pinned here so the premise_row branch
    added for the anchored path cannot silently stop covering this one."""
    rows = _one_holding_book()
    premise = {"ticker": "BBB", "side": "buy", "price": 500.0, "qty": 1.0}
    result = cq.consequence(rows, premise)   # no cash_anchor
    assert result["before"]["cash"]["source"] == "csv_sum"
    assert _close(result["before"]["cash"]["balance"], -1000.0)
    assert _close(result["after"]["cash"]["balance"], -1500.0)
    assert "cash_unreliable" in result["disclosures"]


def test_portfolio_state_premise_row_parameter_is_opt_in():
    """Direct mechanism pin, one level below `consequence()`: passing the
    same appended row back as `premise_row` is what turns the fix on. Every
    caller that does not pass it -- every `portfolio_state` call above this
    section, and review.py's own plain snapshot reads -- must keep seeing
    exactly the pre-#751 arithmetic for a postdated anchor, so the parameter
    is additive, never a change to the default path."""
    rows = _one_holding_book()
    anchor = {"as_of": "2024-01-02", "amount": 10000.0, "currency": "USD"}
    normalized = cq.validate_premise(
        {"ticker": "BBB", "side": "buy", "price": 500.0, "qty": 1.0}, rows)
    premise_row = cq._premise_row(normalized)
    unfixed = cq.portfolio_state(rows + [premise_row], cash_anchor=anchor)
    fixed = cq.portfolio_state(rows + [premise_row], cash_anchor=anchor,
                               premise_row=premise_row)
    assert _close(unfixed["cash"]["balance"], 10000.0), \
        "without premise_row, the pre-fix arithmetic must still reproduce exactly"
    assert _close(fixed["cash"]["balance"], 9500.0)


# ───────────────── E. rule_collision ─────────────────

def test_evaluable_metrics_return_would_breach_only_when_the_after_state_crosses_the_line():
    """sample_fundamental.csv, cost basis: max_pct starts at 0.17665... (JNJ),
    well under the 25% oversize trigger, and top3 starts at 0.5206, under its
    0.60 line. A small buy (notional 100) must stay clear on both; a large
    buy (notional 5000, comfortably over the 2706.67 breakeven that puts
    AAPL's weight over 25%) pushes top3 to 0.6001461988304093 — just over its
    own line — and must flip both to would_breach.

    top3_pct, not ai_pct, is the concentration metric exercised here: every
    ticker in this fixture is unmapped/non-AI (no driver_map sidecar is
    loaded), so ai_pct is 0.0 regardless of trade size and a rule tracking it
    must never move — see the companion test below, which is the positive
    proof that this is no longer a bug."""
    rows = _rows("sample_fundamental.csv")
    before = cq.portfolio_state(rows)
    assert before["oversize_triggered"] is False
    assert before["concentration_triggered"] is False
    rules_report = ([_rule("max_pos_pct"), _rule("top3_pct")], [], 0)

    small = {"ticker": "AAPL", "side": "buy", "price": 180.0, "notional": 100.0}
    small_out = cq.rule_collision(rows, small, rules_report)
    assert {row["metric_key"]: row["state"] for row in small_out} == {
        "max_pos_pct": "clear", "top3_pct": "clear"}

    large = {"ticker": "AAPL", "side": "buy", "price": 180.0, "notional": 5000.0}
    large_out = cq.rule_collision(rows, large, rules_report)
    assert {row["metric_key"]: row["state"] for row in large_out} == {
        "max_pos_pct": "would_breach", "top3_pct": "would_breach"}
    # cross-check against the same fields consequence() itself reports
    large_result = cq.consequence(rows, large)
    assert large_result["after"]["oversize_triggered"] is True
    assert _close(large_result["after"]["top3"], 0.6001461988304093)
    assert large_result["after"]["top3"] > tr.TOP3_MAX_TH


def test_a_rule_on_one_concentration_metric_is_not_moved_by_a_sibling_crossing_its_own_line():
    """The precise regression this fixes: the same large AAPL buy above
    crosses top3's own 0.60 line, but AAPL contributes nothing to ai_pct (no
    driver_map sidecar is loaded, so every ticker here is unmapped/non-AI).
    Before the fix, ai_pct's state was read off dim_diversify's shared
    triggered flag, which top3 alone had already flipped True -- so a rule
    tracking ai_pct specifically would have read already_over/would_breach
    for a trade that never touched its own reading at all. It must read
    clear, unconditionally, in both directions."""
    rows = _rows("sample_fundamental.csv")
    rules_report = ([_rule("ai_pct")], [], 0)
    large = {"ticker": "AAPL", "side": "buy", "price": 180.0, "notional": 5000.0}
    result = cq.consequence(rows, large)
    assert result["before"]["ai_pct"] == 0.0 and result["after"]["ai_pct"] == 0.0
    assert result["after"]["concentration_triggered"] is True, \
        "the shared flag IS triggered here (via top3) -- the point is that ai_pct must not borrow it"
    out = cq.rule_collision(rows, large, rules_report)
    assert out[0]["state"] == "clear", \
        f"ai_pct must not react to top3 crossing its own, different line: {out[0]}"


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
    """sample_ai_holder.csv: max_sector_pct (0.5076 > 0.40) and ai_pct
    (1.0 > 0.60) are each independently already over their OWN line before
    this trade, and each independently stays over after buying 200 more TSM
    — a conclusion no longer read off dim_diversify's shared flag at all
    (see _concentration_collision), but their own readings move oppositely:
    max_sector_pct actually rises (0.5076 -> 0.6608, worsens True) while
    ai_pct sits at its 1.0 ceiling on both sides (unchanged, worsens False).
    If worsens read a shared signal instead of each metric's own field, both
    would say the same thing; they must not."""
    rows = _rows("sample_ai_holder.csv")
    before = cq.portfolio_state(rows)
    assert before["max_sector_pct"] > tr.SECTOR_MAX_TH
    assert before["ai_pct"] > tr.AI_MAX_TH
    assert before["concentration_triggered"] is True  # true here too, but not what state/worsens now read
    rules_report = ([_rule("max_sector_pct"), _rule("ai_pct")], [], 0)
    premise = {"ticker": "TSM", "side": "buy", "price": 180.0, "qty": 200.0}

    out = cq.rule_collision(rows, premise, rules_report)
    by_key = {row["metric_key"]: row for row in out}
    assert by_key["max_sector_pct"]["state"] == "already_over"
    assert by_key["max_sector_pct"]["worsens"] is True
    assert by_key["ai_pct"]["state"] == "already_over"
    assert by_key["ai_pct"]["worsens"] is False


# ───────────────── G. concentration causality — external review MAJOR 1 & 2 ─────────────────

def test_major_1_a_fresh_cross_of_this_rules_own_metric_is_not_hidden_by_a_sibling():
    """External review MAJOR 1, book and premise verbatim: NVDA $50, MSTR $30,
    HOOD $10, CAVA $10 ($100 total). NVDA is the only AI-thematic driver
    entry among these four, so ai_pct = NVDA's own weight = 0.5, under its
    0.60 line. top3 is 0.9 (NVDA+MSTR+HOOD), already over ITS 0.60 line
    before any trade -- so the shared concentration_triggered flag is True
    before this trade even happens, for a reason that has nothing to do with
    ai_pct. Buying $30 more NVDA raises ai_pct to 80/130 = 0.6153846... ,
    crossing 0.60 for the first time. A rule tracking ai_pct specifically
    must read would_breach, not already_over -- already_over would mean this
    trade did not cause the AI-exposure line to be crossed, and it did."""
    rows = _dollar_book([("NVDA", 50.0), ("MSTR", 30.0), ("HOOD", 10.0), ("CAVA", 10.0)])
    before = cq.portfolio_state(rows)
    assert tr.driver("NVDA")[1] == 1 and tr.driver("MSTR")[1] == 0 and \
        tr.driver("HOOD")[1] == 0 and tr.driver("CAVA")[1] == 0, \
        "the fixture's premise (only NVDA is AI-thematic) must hold for this test to mean anything"
    assert _close(before["ai_pct"], 0.5)
    assert before["ai_pct"] < tr.AI_MAX_TH
    assert _close(before["top3"], 0.9) and before["top3"] > tr.TOP3_MAX_TH
    assert before["concentration_triggered"] is True, \
        "the shared flag is already triggered before this trade, via top3 -- not via ai_pct"

    premise = {"ticker": "NVDA", "side": "buy", "price": 50.0, "notional": 30.0}
    result = cq.consequence(rows, premise)
    assert _close(result["after"]["ai_pct"], 80.0 / 130.0)
    assert _close(result["after"]["ai_pct"], 0.6153846153846154)
    assert result["after"]["ai_pct"] > tr.AI_MAX_TH

    out = cq.rule_collision(rows, premise, ([_rule("ai_pct")], [], 0))
    assert out[0]["state"] == "would_breach", \
        f"ai_pct crossed its own line for the first time; must not read already_over: {out[0]}"


def test_major_2_a_fresh_cross_of_max_sector_pct_is_not_reported_clear():
    """External review MAJOR 2, book and premise verbatim: six equal $100
    holdings (HOOD, SOFI, MSTR, CAVA, MP, ONDS; $600 total), all mapped to
    distinct sectors except HOOD/SOFI, which share "金融科技" (fintech) at
    $200/$600 = 0.3333, under the 0.40 line. Buying $100 of GRAB (also
    "金融科技") raises that sector to $300/$700 = 0.42857142857142855, over
    the line -- but with 7 risk holdings (still under dim_diversify's own
    >= 8 holdings guard on its shared flag) and top3/ai nowhere near 0.60,
    the shared concentration_triggered flag stays False throughout. Before
    the fix this returned "clear": the user committed to keeping a sector
    under 40%, this trade takes it over 40%, and the tool said there was no
    collision -- a false negative, the worst shape this vocabulary can
    produce."""
    rows = _dollar_book([("HOOD", 100.0), ("SOFI", 100.0), ("MSTR", 100.0),
                         ("CAVA", 100.0), ("MP", 100.0), ("ONDS", 100.0)])
    before = cq.portfolio_state(rows)
    assert before["n_holdings"] == 6
    assert before["max_sector"] == "金融科技" and _close(before["max_sector_pct"], 1.0 / 3.0)
    assert before["max_sector_pct"] < tr.SECTOR_MAX_TH
    assert before["concentration_triggered"] is False

    premise = {"ticker": "GRAB", "side": "buy", "price": 100.0, "notional": 100.0}
    result = cq.consequence(rows, premise)
    assert tr.driver("GRAB")[0] == "金融科技", "GRAB must land in the same sector as HOOD/SOFI"
    assert _close(result["after"]["max_sector_pct"], 300.0 / 700.0)
    assert _close(result["after"]["max_sector_pct"], 0.42857142857142855)
    assert result["after"]["max_sector_pct"] > tr.SECTOR_MAX_TH
    assert result["after"]["concentration_triggered"] is False, \
        "the shared flag stays False here (7 risk holdings, under the >=8 guard) -- that is the bug"

    out = cq.rule_collision(rows, premise, ([_rule("max_sector_pct")], [], 0))
    assert out[0]["state"] == "would_breach", \
        f"max_sector_pct crossed its own 40% line; must not read clear: {out[0]}"


def test_concentration_lines_are_read_live_from_trade_recaps_named_constants():
    """Anti-drift pin (per the fix direction): _concentration_line must
    return trade_recap's own named constants, not an independently chosen or
    copied literal that could match today and silently diverge the next time
    someone tunes dim_diversify's thresholds."""
    assert cq._concentration_line("ai_pct") == tr.AI_MAX_TH
    assert cq._concentration_line("max_sector_pct") == tr.SECTOR_MAX_TH
    assert cq._concentration_line("top3_pct") == tr.TOP3_MAX_TH


def test_the_ai_pct_boundary_agrees_with_dim_diversifys_own_strict_comparison():
    """Not just equal constants: the same boundary. Four AI-thematic
    semiconductor names (NVDA/AMD/MU/AVGO, $15 each = 60%) plus two unrelated
    non-AI names ($20 each) keep top3 (0.55) and max_sector_pct (0.6, but on
    only 6 risk holdings, under dim_diversify's own >= 8 guard) both out of
    the way, isolating the ai clause: at exactly ai_pct == AI_MAX_TH,
    dim_diversify's own strict `>` does not trigger, and a $1 nudge that
    pushes ai_pct to 0.6039603960396039 does. _concentration_collision must
    agree at the same point, not a value that merely looks the same today."""
    rows_at = _dollar_book([("NVDA", 15.0), ("AMD", 15.0), ("MU", 15.0), ("AVGO", 15.0),
                            ("MSTR", 20.0), ("HOOD", 20.0)])
    rows_over = _dollar_book([("NVDA", 15.0), ("AMD", 15.0), ("MU", 15.0), ("AVGO", 16.0),
                              ("MSTR", 20.0), ("HOOD", 20.0)])
    at = cq.portfolio_state(rows_at)
    over = cq.portfolio_state(rows_over)

    assert _close(at["ai_pct"], tr.AI_MAX_TH) and _close(at["top3"], 0.55)
    assert at["concentration_triggered"] is False, \
        "exactly at the line is not over it, on dim_diversify's own strict >"
    assert _close(over["ai_pct"], 0.6039603960396039) and over["ai_pct"] > tr.AI_MAX_TH
    assert over["concentration_triggered"] is True

    # rule_collision must draw would_breach exactly where dim_diversify's own
    # trig flips, not one increment before or after it.
    state, worsens = cq._concentration_collision("ai_pct", at, over)
    assert state == "would_breach"
    assert worsens is None


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
