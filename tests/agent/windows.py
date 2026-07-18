#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Weekly engine-artifact windows for the #60 experience harness.

The owner's manual proof split a persona's trade CSV into weekly windows and
fed each window through the real CLI.  Running the *raw* engine on a CSV needs
live prices (``trade_recap`` calls yfinance), which breaks the harness's
zero-network / determinism contract.  So each weekly window here is a
precomputed ``(engine_card, engine_state)`` pair — exactly the artifact shape
``review.py prepare`` accepts through ``--card-json`` / ``--state-json``, the
same offline path ``tests/test_review_v2.py`` drives.  The pair stands in for
one week's ``_run_engine`` output; the values are synthetic and fixed, so the
whole experience is byte-for-byte reproducible.

A window may also carry a trade ``csv`` snippet.  That CSV is ingested into the
isolated root's ledger (offline, like ``_prepare_with_trades``) purely to raise
exit-capture questions — it never fetches a price.  ``cycle_id`` and
``decision_cursor`` evolve week to week the way a real portfolio would (a new
add advances the cursor; an unchanged position keeps it), which is what makes
the add-motive dedup and the memory-weave replay observable.
"""
import copy
from dataclasses import dataclass, field


@dataclass
class Window:
    """One week of one persona: the engine artifacts plus optional ledger CSV."""
    label: str
    card: dict
    state: dict
    csv: str | None = None
    notes: dict = field(default_factory=dict)


# ── Base artifact templates ────────────────────────────────────────────────
# Minimal-but-valid card/state modelled on tests/test_review_v2.py::_artifacts.
# The averaging-down headline keeps a resolvable candidate rule (avgdown_count
# is the dimension's metric), and a single honesty key keeps narratives small.


def _state(*, date_end, positions, max_pos_ticker):
    return {
        "schema_version": 2,
        "date_start": "2026-01-01", "date_end": date_end,
        "n_trades": 8, "n_round_trips": 3, "n_held": len(positions),
        "headline_dim": "加碼攤平",
        "headline_metric": {"key": "avgdown_count", "value": 3},
        "commitment": None,
        "metrics": {
            "max_pos_pct": 0.42, "max_pos_ticker": max_pos_ticker, "avgdown_count": 3,
            "avgdown_breach": 1, "payoff": 1.4, "ai_pct": 0.42,
            "max_sector_pct": 0.42, "top3_pct": 0.42, "n_holdings": len(positions),
            "exit_severity": 0.2, "hold_severity": 0.1,
            "beta": None, "alpha_ann": None, "alpha_t": None, "alpha_credible": None,
        },
        "rule": None, "insufficient_data": False,
        "holdings": {"as_of": date_end, "derived_from": "trades_csv", "is_complete": False,
                     "positions": positions},
        "currency_meta": {"aggregate_currency": "USD", "mixed": False},
        "portfolio_structure": {
            "schema_version": 1, "allocation_weight": 0.58, "concentrated_etf_weight": 0,
            "allocation_etfs": [{"ticker": "SPY", "kind": "broad_market_etf", "weight": 0.58}],
            "concentrated_etfs": [],
            "metadata_gaps": [{"ticker": "SPY", "fields": ["expense_ratio"]}]},
        "cash": None,
        "problem_events": [{"key": "avgdown_breach", "kind": "event", "week": date_end,
                            "ticker": max_pos_ticker, "amount": 1, "note": "fixture"}],
        "problem_opportunities": {"avgdown_breach": True},
    }


def _card(*, thesis_questions):
    hole = {"dim": "加碼攤平", "severity": 0.8, "tier_weight": 1.0,
            "number_line": "在虧損倉往下加碼的次數偏高",
            "lens_rule": "往下加碼前先寫新證據。", "lens_quote": "先驗證再加碼。",
            "raw": {"dim": "加碼攤平", "tier": 1, "triggered": True, "severity": 0.8,
                    "count": 3, "breach": 1, "tickers": [q["ticker"] for q in thesis_questions] or ["PLTR"]}}
    return {
        "schema_version": 1, "philosophy": "test lens",
        "strength": "你守住了其他部位的上限。",
        "overview": {"total_pnl": -300, "realized": 200, "unrealized": -500,
                     "payoff": 1.4, "avg_win": 140, "avg_loss": -100},
        "best_trade": {"ticker": "NVDA", "ret": 0.2, "pnl": 200},
        "worst_trade": {"ticker": "AMD", "ret": -0.1, "pnl": -100}, "what_if": None,
        "ticker_diagnosis": [], "thesis_questions": thesis_questions,
        "top_holes": [hole],
        "candidate_rules": [{"dim": "加碼攤平", "rule": "往下加碼前先寫新證據。"}],
        "prescriptions": [], "alpha_beta_breakdown": {}, "payoff_attribution": {},
        "dims_raw": [hole["raw"]], "data_integrity": {},
        "currency_meta": {"aggregate_currency": "USD"}, "cash": None,
        "acct_perf": {"note": "offline"},
        "portfolio_structure": None,  # filled below to match the state
        "honesty_ledger": [{"key": "etf_metadata", "status": "partial", "data": {}}],
        "pnl_curve": {"note": "offline"},
    }


def _position(*, cycle_id, cursor, avg_cost=100, shares=10, cost=1000, cycle_start="2026-01-01"):
    return {"shares": shares, "cost": cost, "avg_cost": avg_cost, "cycle_start": cycle_start,
            "cycle_id": cycle_id, "add_count": 3, "decision_cursor": cursor}


def _pair(*, date_end, positions, thesis_tickers, question_text):
    """Assemble a consistent (card, state) window from a compact spec."""
    max_pos_ticker = next(iter(positions))
    state = _state(date_end=date_end, positions=positions, max_pos_ticker=max_pos_ticker)
    tq = [{"ticker": t, "question": question_text} for t in thesis_tickers]
    card = _card(thesis_questions=tq)
    card["portfolio_structure"] = state["portfolio_structure"]
    return card, state


# ── Persona weekly windows ─────────────────────────────────────────────────
# Each returns a list[Window]; the driver walks them in order against one root.


def steady_conviction_weeks():
    """PLTR conviction holder averaging down with evidence, two weeks.

    Week 1 opens the PLTR thesis; week 2 adds again (cursor advances), so the
    add question re-fires and its stem replays the week-1 thesis (memory weave),
    while the card opens by reconciling the week-1 committed rule.
    """
    q = "PLTR 加碼時有新證據，還是只想攤低成本？"
    c1, s1 = _pair(date_end="2026-07-07",
                   positions={"PLTR": _position(cycle_id="PLTR#2026-01-01#1",
                                                cursor="PLTR#2026-01-01#1#add#2")},
                   thesis_tickers=["PLTR"], question_text=q)
    c2, s2 = _pair(date_end="2026-07-14",
                   positions={"PLTR": _position(cycle_id="PLTR#2026-01-01#1",
                                                cursor="PLTR#2026-01-01#1#add#3")},
                   thesis_tickers=["PLTR"], question_text=q)
    return [
        Window("week-1 opening add", c1, s1,
               notes={"expect_kinds": ["add_thesis"], "commit": "candidate"}),
        Window("week-2 add again", c2, s2,
               notes={"expect_kinds": ["add_thesis"], "expect_memory_weave": True,
                      "expect_reconciliation": True, "commit": "skip"}),
    ]


def anxious_skipper_weeks():
    """MSFT holder who sells fast and won't name reasons, two weeks.

    Week 1 carries a TSLA exit inside the capture window; the persona skips its
    reason.  Week 2 keeps the same MSFT cursor and the same CSV, so neither the
    already-answered add nor the skipped exit returns — only the fallback
    motive question remains, and no rule is ever committed.
    """
    q = "The MSFT add: new evidence, a planned tranche, a valuation change, or only the lower price?"
    exit_csv = ("Symbol,Action,Quantity,Price,TradeDate,RecordType,Market,Currency\n"
                "TSLA,BUY,10,100,2026-06-20,Trade,US,USD\n"
                "TSLA,SELL,10,140,2026-07-03,Trade,US,USD\n")
    c1, s1 = _pair(date_end="2026-07-07",
                   positions={"MSFT": _position(cycle_id="MSFT#2026-06-01#1",
                                                cursor="MSFT#2026-06-01#1#add#2",
                                                cycle_start="2026-06-01")},
                   thesis_tickers=["MSFT"], question_text=q)
    c2, s2 = _pair(date_end="2026-07-14",
                   positions={"MSFT": _position(cycle_id="MSFT#2026-06-01#1",
                                                cursor="MSFT#2026-06-01#1#add#2",
                                                cycle_start="2026-06-01")},
                   thesis_tickers=["MSFT"], question_text=q)
    return [
        Window("week-1 skipped exit", c1, s1, csv=exit_csv,
               notes={"expect_kinds": ["revisit", "add_thesis"], "skip_exit": "TSLA",
                      "commit": "skip"}),
        Window("week-2 nothing returns", c2, s2, csv=exit_csv,
               notes={"expect_kinds": ["headline_motive"], "exit_not_reasked": "TSLA",
                      "commit": "skip"}),
    ]


# name -> weekly-window factory (fresh dicts every call so a mutated run cannot
# leak into the next).
PERSONA_WINDOWS = {
    "steady_conviction": steady_conviction_weeks,
    "anxious_skipper": anxious_skipper_weeks,
}


def weeks_for(name):
    factory = PERSONA_WINDOWS.get(name)
    if factory is None:
        raise KeyError(f"unknown persona windows: {name!r}")
    return copy.deepcopy(factory())
