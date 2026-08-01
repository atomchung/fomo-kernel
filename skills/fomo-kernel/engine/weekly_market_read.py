"""A bounded, read-only companion brief for a prepared weekly review (#683).

The brief never treats a broad market move as relevant merely because a book
exists.  Its only first-slice connection is a settled engine diagnosis of an
over-concentrated holding *and* a rising, already-frozen VIX observation.  If
either fact is absent, the whole block is omitted rather than becoming a
generic recap.
"""

from __future__ import annotations

import math


MAX_HOLDINGS = 3
MAX_WATCH_ITEMS = 2
FOCUSES = ("business_evidence", "position_size", "not_sure")


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def _as_of(row, fallback):
    return row.get("last_date") or fallback


def _concentrated_holdings(plan):
    """Return only held names that the existing engine marked too_heavy."""
    state = plan.get("engine_state") or {}
    card = plan.get("engine_card") or {}
    held = set(((state.get("holdings") or {}).get("positions") or {}))
    rows = []
    for row in card.get("ticker_diagnosis") or []:
        ticker = row.get("ticker")
        tags = [tag.get("code") for tag in row.get("tags") or [] if tag.get("code")]
        if ticker in held and "too_heavy" in tags and ticker not in {item["ticker"] for item in rows}:
            rows.append({"ticker": ticker, "engine_fact": "ticker_diagnosis.too_heavy"})
        if len(rows) == MAX_HOLDINGS:
            break
    return rows


def _watch(ticker, focus, vix_fact):
    shared = {
        "condition": "VIX rose in the frozen review window",
        "subject": "VIX",
        "source": vix_fact["source"],
        "as_of": vix_fact["as_of"],
    }
    checks = {
        "business_evidence": "Before explaining a move with the market, check whether new company-specific evidence exists.",
        "position_size": "Before treating a lower price as evidence, compare the recorded position size with the existing cap.",
        "not_sure": "Before acting, decide whether company-specific evidence or the recorded position size is the relevant check.",
    }
    return {"subject": ticker, "check": checks[focus], "trigger": shared}


def build(plan, *, focus=None):
    """Return a session-local WeeklyMarketRead, or an explicit omission.

    ``focus`` is a user answer only for the second, read-only presentation.  A
    first read has ``selected: null`` and is already complete when skipped.
    """
    if plan.get("route") != "weekly_review":
        return {"status": "omitted", "reason": "not_weekly_review"}
    if focus is not None and focus not in FOCUSES:
        raise ValueError("weekly-market focus is not an offered value")

    snapshot = plan.get("state_snapshot") or {}
    context = snapshot.get("market_context") or {}
    window = {"start": context.get("start"), "end": context.get("end")}
    provenance = ((plan.get("input") or {}).get("price_feed") or {}).get("provenance") or {}
    vix = (context.get("benchmarks") or {}).get("VIX") or {}
    as_of = _as_of(vix, provenance.get("as_of"))
    holdings = _concentrated_holdings(plan)
    # A valid connection needs both sides.  A missing/flat/falling VIX is not a
    # reason to attach a market story to a book, so it omits the whole block.
    if not (window["start"] and window["end"] and holdings and as_of and _finite(vix.get("delta")) and vix["delta"] > 0):
        return {"status": "omitted", "reason": "no_book_specific_connection"}

    chosen = holdings[0]
    vix_fact = {
        "kind": "engine_fact",
        "subject": "VIX",
        "source": "frozen_market_resolution",
        "as_of": as_of,
        "observation": {"delta": vix["delta"], "last": vix.get("last"), "last_date": vix.get("last_date")},
    }
    selected = focus
    effective_focus = focus or "not_sure"
    return {
        "status": "available",
        "window": window,
        "market_facts": [vix_fact],
        "selected_holdings": holdings,
        "connection": {
            "ticker": chosen["ticker"],
            "relation": "engine_diagnosed_overconcentration_during_rising_volatility",
            "engine_facts": ["ticker_diagnosis.too_heavy", "market_context.VIX.delta_positive"],
        },
        "decision_risk": {
            "kind": "concentration_blindness_during_rising_volatility",
            "basis": "The engine marked this held name too_heavy while the frozen VIX observation rose.",
        },
        "next_week_watch": [_watch(chosen["ticker"], effective_focus, vix_fact)],
        "optional_question": {
            "required": False,
            "choices": list(FOCUSES),
            "selected": selected,
            "prompt": "Which check would make next week's follow-up more useful?",
        },
        "persistence": "none",
    }


def render_zh_tw(brief):
    """Render the bounded companion without exposing engine process labels."""
    if brief.get("status") != "available":
        return ""
    window = brief["window"]
    fact = brief["market_facts"][0]
    ticker = brief["connection"]["ticker"]
    watch = brief["next_week_watch"][0]
    text = [
        "## 本週市場發生了什麼",
        f"{window['start']} 至 {window['end']}，VIX 上升 {fact['observation']['delta']}。市場資料截至 {fact['as_of']}。",
        "## 對你的組合意味著什麼",
        f"{ticker} 已被本次檢視標記為部位偏重；在波動上升時，先檢查既有集中度，比替價格變動找理由更重要。",
        "## 這週最容易犯的錯誤",
        "把波動中的價格變化直接當成新的投資證據，卻沒有先確認部位大小或公司基本面的新資訊。",
        "## 下週關注",
        f"- {ticker}：{watch['check']}（觸發條件：{watch['trigger']['condition']}；截至 {watch['trigger']['as_of']}）",
    ]
    if brief["optional_question"]["selected"] is None:
        text.extend([
            "## 可選問題",
            "如果只選一個方向，下週先看公司新證據，還是先看部位大小？可跳過。",
        ])
    return "\n".join(text)


def render_en(brief):
    """English fallback for the same bounded, decision-first brief."""
    if brief.get("status") != "available":
        return ""
    window = brief["window"]
    fact = brief["market_facts"][0]
    ticker = brief["connection"]["ticker"]
    watch = brief["next_week_watch"][0]
    text = [
        "## What happened in markets this week",
        f"From {window['start']} to {window['end']}, VIX rose by {fact['observation']['delta']}. Market data as of {fact['as_of']}.",
        "## What it means for your portfolio",
        f"{ticker} is already marked as an overweight position in this review. With volatility rising, check the existing concentration before explaining a price move.",
        "## The easiest mistake this week",
        "Treating a volatile price move as new investment evidence before checking position size or new company-specific information.",
        "## Watch next week",
        f"- {ticker}: {watch['check']} (trigger: {watch['trigger']['condition']}; as of {watch['trigger']['as_of']})",
    ]
    if brief["optional_question"]["selected"] is None:
        text.extend([
            "## Optional question",
            "If you choose one direction, should next week start with new company evidence or position size? You can skip this.",
        ])
    return "\n".join(text)


def render(brief, language):
    return render_zh_tw(brief) if language == "zh-TW" else render_en(brief)
