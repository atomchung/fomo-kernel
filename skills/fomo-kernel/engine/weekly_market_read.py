"""A bounded, read-only companion brief for a prepared weekly review (#683).

This module deliberately consumes the Review Plan rather than market_data: the
weekly card has already frozen its market observations.  It therefore cannot
start a second provider pass, change the card, or write canonical state.
"""

from __future__ import annotations

MAX_HOLDINGS = 3
MAX_WATCH_ITEMS = 2
FOCUSES = ("business_evidence", "position_size", "price_behavior", "not_sure")


def _as_of(row, fallback):
    return row.get("last_date") or fallback


def _selected_holdings(plan):
    state = plan.get("engine_state") or {}
    card = plan.get("engine_card") or {}
    held = set(((state.get("holdings") or {}).get("positions") or {}))
    rows = []
    # ticker_diagnosis is already an engine-ranked, card-facing fact.  Do not
    # invent a parallel importance score or promote an arbitrary holding.
    for row in card.get("ticker_diagnosis") or []:
        ticker = row.get("ticker")
        if ticker in held and ticker not in {x["ticker"] for x in rows}:
            rows.append({"ticker": ticker, "engine_fact": "ticker_diagnosis",
                         "tags": [x.get("code") for x in row.get("tags") or [] if x.get("code")]})
        if len(rows) == MAX_HOLDINGS:
            break
    return rows


def build(plan, *, focus=None):
    """Return a session-local WeeklyMarketRead, or an explicit omission.

    ``focus`` is a user's optional attention answer.  It is neither validated
    review input nor saved anywhere; it only changes this response's watch.
    """
    if plan.get("route") != "weekly_review":
        return {"status": "omitted", "reason": "not_weekly_review"}
    snapshot = plan.get("state_snapshot") or {}
    context = snapshot.get("market_context") or {}
    window = {"start": context.get("start"), "end": context.get("end")}
    provenance = ((plan.get("input") or {}).get("price_feed") or {}).get("provenance") or {}
    fallback_as_of = provenance.get("as_of")
    benchmarks = context.get("benchmarks") or {}
    facts = []
    for name in ("SPY", "QQQ", "VIX"):
        row = benchmarks.get(name) or {}
        as_of = _as_of(row, fallback_as_of)
        if row and as_of:
            facts.append({"kind": "engine_fact", "subject": name,
                          "source": "frozen_market_resolution", "as_of": as_of,
                          "observation": row})
    holdings = _selected_holdings(plan)
    if not (window["start"] and window["end"] and facts and holdings):
        return {"status": "omitted", "reason": "no_book_specific_connection"}
    chosen = holdings[0]
    focus = focus or "not_sure"
    if focus not in FOCUSES:
        raise ValueError("weekly-market focus is not an offered value")
    tags = set(chosen["tags"])
    options = ["business_evidence", "price_behavior"]
    if "too_heavy" in tags:
        options[1] = "position_size"
    if focus not in options and focus != "not_sure":
        raise ValueError("weekly-market focus is not grounded in this brief")
    watch = ({
        "business_evidence": {"subject": chosen["ticker"], "check": "verify company-specific evidence before treating the move as a broad-market explanation"},
        "position_size": {"subject": chosen["ticker"], "check": "compare the recorded position size with the existing cap before treating a lower price as new evidence"},
        "price_behavior": {"subject": chosen["ticker"], "check": "separate the observed price move from a new evidence delta before acting"},
        "not_sure": {"subject": chosen["ticker"], "check": "identify whether business evidence or the recorded position size is the relevant check before acting"},
    })[focus]
    return {"status": "available", "window": window, "market_facts": facts,
            "selected_holdings": holdings, "connection": {"ticker": chosen["ticker"], "engine_fact": chosen["engine_fact"]},
            "decision_risk": "do_not_infer_user_reason_from_market_context",
            "next_week_watch": [watch], "optional_question": {
                "required": False, "choices": options + ["not_sure"], "selected": focus,
                "prompt": "Which observed tension should set next week's check?"},
            "persistence": "none"}


def render_zh_tw(brief):
    """Small, auditable host fallback; hosts may phrase judgment around this plan."""
    if brief.get("status") != "available":
        return ""
    window = brief["window"]
    facts = brief["market_facts"]
    fact_lines = []
    for fact in facts:
        obs = fact["observation"]
        value = obs.get("window_ret", obs.get("delta", obs.get("last")))
        fact_lines.append(f"{fact['subject']}: {value}（frozen engine fact；source: {fact['source']}；as_of: {fact['as_of']}）")
    ticker = brief["connection"]["ticker"]
    watch = brief["next_week_watch"][0]
    choices = "／".join(brief["optional_question"]["choices"])
    return "\n".join([
        "## 本周市場發生了什麼",
        f"窗口：{window['start']} 至 {window['end']}。" + "；".join(fact_lines),
        "## 對你的組合意味著什麼",
        f"{ticker} 是既有 engine ticker_diagnosis 選出的持倉；這是 engine fact，不是你的持有動機。",
        "## 這周最容易犯的錯誤",
        "把凍結的市場背景或公開觀察直接當成你的投資理由；這是 agent judgment，必須由你確認。",
        "## 下周關注",
        f"- {watch['subject']}：{watch['check']}。",
        "## 可選問題",
        f"{brief['optional_question']['prompt']}（{choices}；可跳過）",
    ])
