#!/usr/bin/env python3
"""``engine/snapshot_adapter.py`` card/state field contract (#771).

``snapshot_adapter.prepare()`` used to zero ``state["cash"]``,
``card["cash"]``, ``card["what_if"]``, ``card["ticker_diagnosis"]`` and
``card["strength"]`` unconditionally -- not because a position snapshot
cannot support them, but because the trade lane's fields were switched off
wholesale rather than each asked "can a snapshot support this?" (#771). This
file locks the corrected, per-field judgment:

  - A declared cash balance reaches ``state["cash"]``/``card["cash"]`` when
    every declared currency converts into the book's aggregate currency, and
    stays ``None`` (not a silently-wrong partial sum) when it cannot (#649's
    identity-factor lesson).
  - ``what_if`` (the concentration stress row) and the per-position
    unrealized-P&L ranking in ``card["ticker_diagnosis"]``/
    ``card["overview"]["unrealized"]`` need a *real* current market value,
    not a cost-basis stand-in -- both are supportable only on the
    ``market_value`` valuation basis, never on ``cost``.
  - ``card["strength"]`` ("what you did right") is exactly as available as
    the sizing/diversification dimensions it is built from -- reused via
    ``trade_recap.dim_strength`` with every history-only input blanked,
    rather than reimplemented here.

Card-rendering reach is a separate, later concern: ``card_renderer.py``'s
``_card_facts`` (route == "snapshot_review") and the ``if snapshot:``
branches inside ``_performance_block``/``_risks_block`` currently bypass all
of these fields regardless of what this adapter supplies (confirmed by
manually injecting fully-populated values into a snapshot bundle and
observing no change in ``render_private``/``render_html`` output). This file
therefore asserts the adapter's own output contract -- the facts a renderer
would need -- not rendered text; wiring the renderer to consume them is
tracked separately.

Run:
  python3 tests/test_snapshot_adapter.py
"""
import json
import os
import pathlib
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE)
import snapshot_adapter  # noqa: E402
import trade_recap  # noqa: E402


def _prepare(tmp, payload, name="snapshot.json"):
    """Run the real adapter entry point over one payload, no CLI involved."""
    path = pathlib.Path(tmp) / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return snapshot_adapter.prepare(str(path))


# Three positions with both avg_cost and market_value, mixed USD/TWD with fx
# supplied for both -- the fixture `tests/test_review_v2.py::_snapshot_json`
# already uses. Reusing its exact shape keeps the two suites' expectations
# about this fixture from silently diverging (development-guide.md #7: two
# readers of one fact must not each derive their own answer).
_MIXED_PRICED = {
    "as_of": "2026-07-16",
    "positions": [
        {"ticker": "SPY", "shares": 2, "avg_cost": 600, "market_value": 1240,
         "market": "US", "currency": "USD"},
        {"ticker": "QQQ", "shares": 10, "avg_cost": 500, "market_value": 5100,
         "market": "US", "currency": "USD"},
        {"ticker": "2330.TW", "shares": 1000, "avg_cost": 1000,
         "market_value": 1040000, "market": "TW", "currency": "TWD"},
    ],
    "fx": {"USD": 1, "TWD": 0.033},
}


# ─────────────────────────── cash ───────────────────────────

def test_cash_reaches_state_and_card_when_declared_and_convertible():
    payload = dict(_MIXED_PRICED, cash={"USD": 500, "TWD": 100000})
    with tempfile.TemporaryDirectory() as tmp:
        card, state, _meta = _prepare(tmp, payload)
        # Both artifacts must read the *same* computed summary -- one
        # computation, two consumers, never two that could drift apart.
        assert card["cash"] is state["cash"]
        cash = card["cash"]
        # 500 USD + 100000 TWD * 0.033 USD/TWD = 500 + 3300 = 3800.
        assert cash["balance"] == 3800.0, cash
        assert cash["reliable"] is True
        assert cash["source"] == "user_declared"
        assert cash["recent_net_deposit"] is None, \
            "a snapshot has no cash-flow history to measure a period against"
        assert cash["by_currency"] == {
            "USD": {"balance": 500.0, "source": "user_declared", "reliable": True},
            "TWD": {"balance": 100000.0, "source": "user_declared", "reliable": True},
        }
        # Held value (aggregate USD) = 1240 + 5100 + 1040000*0.033 = 40,660.
        # weight = 3800 / (3800 + 40660).
        assert abs(cash["weight"] - 3800.0 / (3800.0 + 40660.0)) < 1e-9, cash


def test_cash_stays_none_when_the_envelope_declares_none():
    with tempfile.TemporaryDirectory() as tmp:
        card, state, _meta = _prepare(tmp, dict(_MIXED_PRICED))
        assert card["cash"] is None
        assert state["cash"] is None
        # The raw declaration is absent too -- nothing to recover from.
        assert state["snapshot_anchor"].get("cash") is None


def test_cash_fails_closed_rather_than_summing_an_unconvertible_currency():
    """#649: a currency the fx map cannot bridge to the aggregate must never
    be summed in at an implicit 1.0 -- the whole aggregate stays unavailable
    rather than silently reporting a partial (and wrong) total."""
    payload = {
        "as_of": "2026-07-20",
        "positions": [{"ticker": "2330.TW", "shares": 1000, "avg_cost": 900,
                       "market_value": 985000, "market": "TW", "currency": "TWD"}],
        "cash": {"EUR": 500},
        "fx": {"TWD": 0.0307},  # no EUR rate anywhere
    }
    with tempfile.TemporaryDirectory() as tmp:
        card, state, _meta = _prepare(tmp, payload)
        assert card["cash"] is None, \
            "an unconvertible cash currency must fail closed, not report a wrong number"
        assert state["cash"] is None
        # The raw declaration still reaches the ledger anchor even though the
        # aggregate view could not be built from it.
        assert state["snapshot_anchor"]["cash"] == {"EUR": 500.0}


def test_cash_in_the_sole_non_usd_position_currency_needs_no_fx_at_all():
    """A literal currency match is an identity, not a rate lookup -- a
    single-currency book's cash converts even when no fx map was supplied."""
    payload = {
        "as_of": "2026-07-20",
        "positions": [{"ticker": "2330.TW", "shares": 1000, "avg_cost": 900,
                       "market_value": 985000, "market": "TW", "currency": "TWD"}],
        "cash": {"TWD": 200000},
    }
    with tempfile.TemporaryDirectory() as tmp:
        card, _state, _meta = _prepare(tmp, payload)
        assert card["currency_meta"]["aggregate_currency"] == "TWD"
        assert card["cash"]["balance"] == 200000.0
        assert card["cash"]["by_currency"]["TWD"]["balance"] == 200000.0


def test_cash_weight_is_none_when_position_weights_are_unavailable():
    payload = {
        "as_of": "2026-07-20",
        "positions": [
            {"ticker": "NVDA", "shares": 10, "avg_cost": 100, "market_value": 2000,
             "market": "US", "currency": "USD"},
            {"ticker": "2330.TW", "shares": 100, "avg_cost": 900, "market_value": 98500,
             "market": "TW", "currency": "TWD"},
        ],
        "cash": {"USD": 500},
        # fx omits TWD entirely -> weights_available becomes False for the
        # mixed position book, but cash (declared in USD, the aggregate
        # currency for a mixed book) still converts on its own.
    }
    with tempfile.TemporaryDirectory() as tmp:
        card, _state, _meta = _prepare(tmp, payload)
        assert card["snapshot_summary"]["weights_available"] is False
        assert card["cash"] is not None
        assert card["cash"]["balance"] == 500.0
        assert card["cash"]["weight"] is None, \
            "no portfolio denominator exists without position weights"


# ─────────────────────────── what_if ───────────────────────────

def test_what_if_reaches_the_card_on_the_market_value_basis():
    with tempfile.TemporaryDirectory() as tmp:
        card, _state, _meta = _prepare(tmp, dict(_MIXED_PRICED))
        wi = card["what_if"]
        assert wi is not None
        assert wi["scenario"] == {"kind": "single_ticker", "ticker": "2330.TW"}
        # mval = shares * (global_value / shares) = the ticker's own
        # aggregate-currency market value: 1,040,000 TWD * 0.033 = 34,320 USD.
        assert wi["mval"] == 34320.0, wi
        assert wi["drop30"] == 34320.0 * 0.30
        assert wi["drop50"] == 34320.0 * 0.50
        assert abs(wi["pct"] - 34320.0 / (1240.0 + 5100.0 + 34320.0)) < 1e-9


def test_what_if_stays_none_on_the_cost_basis_proxy():
    """No `market_value` anywhere -- `weights_available` is still True (the
    cost-basis proxy supports position weights), but `what_if` claims a
    current-price exposure a cost proxy cannot honestly state."""
    payload = {
        "as_of": "2026-07-20",
        "positions": [
            {"ticker": "NVDA", "shares": 10, "avg_cost": 100, "market": "US",
             "currency": "USD"},
            {"ticker": "PLTR", "shares": 10, "avg_cost": 50, "market": "US",
             "currency": "USD"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        card, _state, _meta = _prepare(tmp, payload)
        assert card["snapshot_summary"]["valuation_basis"] == "cost"
        assert card["snapshot_summary"]["weights_available"] is True
        assert card["what_if"] is None
        assert card["ticker_diagnosis"] == []
        assert card["overview"] == {}


def test_what_if_is_a_well_diversified_none_not_a_missing_data_none():
    """A genuinely diversified book (no candidate crosses the 25% stress
    threshold) must return the same honest ``None`` `trade_recap.what_if`
    itself returns for that case -- not distinguishable from a data gap, by
    the same contract the trade lane already lives with."""
    payload = {
        "as_of": "2026-07-20",
        "positions": [
            {"ticker": t, "shares": 10, "avg_cost": 100, "market_value": 1000,
             "market": "US", "currency": "USD"}
            for t in ("MSTR", "HOOD", "CAVA", "MP", "ONDS", "NOK")
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        card, _state, _meta = _prepare(tmp, payload)
        assert card["snapshot_summary"]["valuation_basis"] == "market_value"
        assert card["what_if"] is None


# ─────────────────────── ticker_diagnosis / overview.unrealized ───────────────────────

def test_ticker_diagnosis_and_overview_unrealized_match_the_engine_on_a_shared_fixture():
    with tempfile.TemporaryDirectory() as tmp:
        card, _state, _meta = _prepare(tmp, dict(_MIXED_PRICED))
        diag = card["ticker_diagnosis"]
        assert [row["ticker"] for row in diag] == ["2330.TW", "QQQ", "SPY"], \
            "ranked by |impact| descending, same convention as the trade lane"
        impacts = {row["ticker"]: row["impact"] for row in diag}
        # SPY: 1240 - 2*600 = 40. QQQ: 5100 - 10*500 = 100.
        # 2330.TW: (1,040,000 - 1000*1000) TWD * 0.033 = 40,000 * 0.033 = 1,320.
        assert impacts == {"SPY": 40.0, "QQQ": 100.0, "2330.TW": 1320.0}
        too_heavy = [row for row in diag if row["ticker"] == "2330.TW"][0]
        assert too_heavy["tags"][0]["code"] == "too_heavy"
        assert card["overview"]["unrealized"] == 1460.0
        assert card["overview"]["unrealized_coverage"] == {
            "held_n": 3, "priced_n": 3, "unpriced": []}


def test_a_position_missing_avg_cost_is_excluded_but_disclosed_not_silently_dropped():
    payload = {
        "as_of": "2026-07-20",
        "positions": [
            {"ticker": "NVDA", "shares": 10, "avg_cost": 100, "market_value": 2000,
             "market": "US", "currency": "USD"},
            {"ticker": "PLTR", "shares": 10, "market_value": 500,
             "market": "US", "currency": "USD"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        card, _state, _meta = _prepare(tmp, payload)
        assert [row["ticker"] for row in card["ticker_diagnosis"]] == ["NVDA"]
        assert card["overview"]["unrealized"] == 1000.0  # 2000 - 10*100
        assert card["overview"]["unrealized_coverage"] == {
            "held_n": 2, "priced_n": 1, "unpriced": ["PLTR"]}
        # PLTR's own market value must still weigh into what_if and sizing --
        # only its P&L is unknowable, not its current exposure.
        assert card["what_if"] is not None


def test_overview_never_carries_a_fabricated_realized_or_total_pnl():
    """A snapshot has no transaction history, so "realized P&L" is not a
    fact this route has and reports zero of -- it is a fact this route
    cannot state at all. `trade_recap.overview_stats(rts=[], ...)` would
    compute a real (if coincidentally zero-looking) ``realized: 0.0``, which
    `card_renderer._overview_lines` would read as "zero realized gains"
    rather than "not applicable". Both keys must be absent, not `None`
    placeholders that a `.get()` reader cannot tell apart from a real zero."""
    with tempfile.TemporaryDirectory() as tmp:
        card, _state, _meta = _prepare(tmp, dict(_MIXED_PRICED))
        assert "realized" not in card["overview"]
        assert "total_pnl" not in card["overview"]
        assert "payoff" not in card["overview"]


def test_overview_unrealized_reports_a_genuine_zero_not_a_measurement_gap():
    """Every position's market_value happens to equal its cost exactly --
    the sum really is zero, and `unrealized_coverage` must say every held
    position was priced, not mimic the "nothing was measured" shape."""
    payload = {
        "as_of": "2026-07-20",
        "positions": [
            {"ticker": "NVDA", "shares": 10, "avg_cost": 100, "market_value": 1000,
             "market": "US", "currency": "USD"},
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        card, _state, _meta = _prepare(tmp, payload)
        assert card["overview"]["unrealized"] == 0.0
        assert card["overview"]["unrealized_coverage"] == {
            "held_n": 1, "priced_n": 1, "unpriced": []}


# ─────────────────────────── strength ───────────────────────────

def test_strength_reaches_the_card_reusing_the_engines_own_derivation():
    payload = {
        "as_of": "2026-07-20",
        "positions": [
            {"ticker": t, "shares": 10, "avg_cost": 100, "market_value": 1000,
             "market": "US", "currency": "USD"}
            for t in ("MSTR", "HOOD", "CAVA", "MP", "ONDS", "NOK")
        ],
    }
    with tempfile.TemporaryDirectory() as tmp:
        card, _state, _meta = _prepare(tmp, payload)
        size = next(row for row in card["dims_raw"] if row["dim"] == "部位 sizing")
        diversification = next(row for row in card["dims_raw"] if row["dim"] == "分散")
        # Recomputed through the exact same engine function this adapter
        # calls, with the exact dims this card actually carries -- not a
        # hand-pinned wording copy that would drift the moment
        # `dim_strength`'s own text changes for an unrelated reason.
        expected = trade_recap.dim_strength({}, size, {}, diversification, {})
        assert card["strength"] == expected
        assert card["strength"] is not None
        assert "17%" in card["strength"], card["strength"]


def test_strength_stays_none_when_nothing_supports_a_positive_claim():
    with tempfile.TemporaryDirectory() as tmp:
        # The mixed fixture is 84% concentrated in one name across only 3
        # holdings -- neither the sizing nor the diversification candidate
        # can honestly fire.
        card, _state, _meta = _prepare(tmp, dict(_MIXED_PRICED))
        assert card["strength"] is None


def test_strength_is_none_without_position_weights():
    payload = {
        "as_of": "2026-07-20",
        "positions": [{"ticker": "NVDA", "shares": 10, "avg_cost": 100,
                       "market": "US", "currency": "USD"},
                      {"ticker": "2330.TW", "shares": 10, "avg_cost": 100,
                       "market": "TW", "currency": "TWD"}],
        # No fx for TWD -> weights_available False.
    }
    with tempfile.TemporaryDirectory() as tmp:
        card, _state, _meta = _prepare(tmp, payload)
        assert card["snapshot_summary"]["weights_available"] is False
        assert card["strength"] is None


# ─────────────────────────── runner ───────────────────────────

def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _main() else 0)
