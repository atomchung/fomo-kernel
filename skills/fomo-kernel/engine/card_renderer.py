#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic private/public card renderer.

The agent supplies prose-only interpretation in ``narrative``.  All displayed
numbers are selected from engine output here; narrative fields containing digits
are rejected to keep the engine's numeric authority enforceable in code.
"""
from __future__ import annotations

import html
import json
import os
import re


class RenderError(ValueError):
    pass


HERE = os.path.dirname(os.path.abspath(__file__))
COPY_DIR = os.path.join(os.path.dirname(HERE), "copy")
ALLOWED_NARRATIVE = {"headline", "mirror", "counterfactual", "rule_rationale", "strength"}
DIMENSION_ID_BY_LEGACY_LABEL = {
    "出場紀律": "exit_discipline",
    "部位 sizing": "position_sizing",
    "分散": "diversification",
    "持有時間": "holding_period",
    "加碼攤平": "averaging_down",
    "alpha/beta": "alpha_beta",
    "進場": "entry_style",
}


def load_copy(language):
    language = "en" if str(language).lower().startswith("en") else "zh-TW"
    with open(os.path.join(COPY_DIR, language + ".json"), encoding="utf-8") as f:
        return json.load(f)


def validate_narrative(narrative):
    if not isinstance(narrative, dict):
        raise RenderError("narrative must be an object")
    extra = set(narrative) - ALLOWED_NARRATIVE
    if extra:
        raise RenderError("unknown narrative fields: " + ", ".join(sorted(extra)))
    for key, value in narrative.items():
        if not isinstance(value, str) or not value.strip():
            raise RenderError(f"narrative.{key} must be a non-empty string")
        if re.search(r"\d", value):
            raise RenderError(f"narrative.{key} contains digits; numeric claims must come from engine output")
    if not narrative.get("headline") or not narrative.get("mirror"):
        raise RenderError("narrative.headline and narrative.mirror are required")
    return narrative


def dimension_id(dim):
    """Return the stable English dimension identifier for legacy engine labels."""
    return DIMENSION_ID_BY_LEGACY_LABEL.get(dim, dim)


def localized_dimension(dim, language):
    copy = load_copy(language)
    dim_id = dimension_id(dim)
    return (copy.get("dimensions") or {}).get(dim_id, dim_id.replace("_", " "))


def localized_rule(dim, language):
    return (load_copy(language).get("rules") or {}).get(dimension_id(dim))


def _currency(card):
    return ((card.get("currency_meta") or {}).get("aggregate_currency") or "USD").upper()


def _money(value, currency):
    if value is None:
        return "—"
    symbol = "$" if currency == "USD" else currency + " "
    return f"{symbol}{float(value):+,.0f}"


def _money_abs(value, currency):
    if value is None:
        return "—"
    symbol = "$" if currency == "USD" else currency + " "
    return f"{symbol}{abs(float(value)):,.0f}"


def _pct(value, digits=0):
    return "—" if value is None else f"{float(value) * 100:.{digits}f}%"


def _hole_line(hole, language):
    if language != "en":
        return hole.get("number_line") or ""
    d = hole.get("raw") or {}
    dim = dimension_id(d.get("dim"))
    if dim == "exit_discipline":
        rate = _pct(d.get("early_rate"))
        return (f"Across {d.get('n_rt', 0)} decision exits, {rate} were higher after the review window; "
                f"winning positions were held {d.get('hold_win', 0):.0f} days versus "
                f"{d.get('hold_lose', 0):.0f} days for losing positions.")
    if dim == "position_sizing":
        return (f"The largest single-risk position was {d.get('max_ticker')}, at {_pct(d.get('max_pct'))}; "
                f"the average of the other risk positions was {_pct(d.get('avg_pct'))}.")
    if dim == "diversification":
        return (f"The portfolio held {d.get('n', 0)} positions, but the top three non-allocation risks were "
                f"{_pct(d.get('top3'))} and the largest classified driver was {_pct(d.get('max_sector_pct'))}.")
    if dim == "holding_period":
        if d.get("no_data"):
            return "There are not yet enough closed round trips to diagnose holding-time consistency."
        return (f"Holding periods ranged from {d.get('min', 0)} to {d.get('max', 0)} days, "
                f"with a median of {d.get('median_hold', 0):.0f} days.")
    if dim == "averaging_down":
        return (f"There were {d.get('count', 0)} adds to losing positions; "
                f"{d.get('breach', 0)} crossed the position-size boundary at the time of the add.")
    return ""


def _best_strength(card, language):
    if language != "en" and card.get("strength"):
        return card["strength"]
    dims = card.get("dims_raw") or []
    safe = [d for d in dims if not d.get("triggered")]
    if not safe:
        return ("這期沒有足夠強的正向訊號；先把注意力留給最大的洞。" if language != "en"
                else "No positive behavior was strong enough to claim; keep attention on the largest leak.")
    dim = min(safe, key=lambda d: float(d.get("severity") or 0)).get("dim")
    return f"The cleanest part of this review was {localized_dimension(dim, language)}."


def _honesty_lines(card, copy):
    messages = copy.get("honesty") or {}
    seen = set()
    lines = []
    for entry in card.get("honesty_ledger") or []:
        key = entry.get("key")
        if key in seen:
            continue
        seen.add(key)
        lines.append(messages.get(key) or key)
    return lines


def _etf_lines(card, language):
    ps = card.get("portfolio_structure") or {}
    allocation = ps.get("allocation_etfs") or []
    concentrated = ps.get("concentrated_etfs") or []
    if not allocation and not concentrated:
        return []
    if language == "en":
        lines = []
        if allocation:
            lines.append("Diversified allocation ETFs were separated from single-name concentration: " +
                         ", ".join(f"{x['ticker']} {_pct(x.get('weight'))}" for x in allocation) + ".")
        if concentrated:
            lines.append("Sector, thematic, or leveraged ETFs remained concentration risk: " +
                         ", ".join(f"{x['ticker']} {_pct(x.get('weight'))}" for x in concentrated) + ".")
        return lines
    lines = []
    if allocation:
        lines.append("配置型 ETF 已從單一股票集中度排除：" +
                     "、".join(f"{x['ticker']} {_pct(x.get('weight'))}" for x in allocation) + "。")
    if concentrated:
        lines.append("產業／主題／槓桿 ETF 仍算集中風險：" +
                     "、".join(f"{x['ticker']} {_pct(x.get('weight'))}" for x in concentrated) + "。")
    return lines


def _decision_lines(bundle, copy):
    labels = copy.get("add_choices") or {}
    lines = []
    for event in bundle.get("thesis_decisions") or []:
        label = labels.get(event.get("decision"), event.get("decision"))
        ticker = event.get("ticker") or "position"
        if copy.get("language") == "en":
            lines.append(f"{ticker}: {label}. The decision and its evidence boundary were saved for the next review.")
        else:
            lines.append(f"{ticker}：{label}。這個判斷與證據邊界已保存，供下次對帳。")
    return lines


def _performance_lines(card, language):
    """Render important existing product facts without giving the agent a calculator."""
    overview = card.get("overview") or {}
    currency = _currency(card)
    en = language == "en"
    lines = []
    payoff = overview.get("payoff")
    if payoff is not None:
        if en:
            lines.append(f"Realized payoff ratio was {payoff:.1f}; the average win was "
                         f"{_money(overview.get('avg_win'), currency)} versus "
                         f"{_money_abs(overview.get('avg_loss'), currency)} for the average loss.")
        else:
            lines.append(f"已實現盈虧比 {payoff:.1f}；平均賺 {_money(overview.get('avg_win'), currency)}，"
                         f"平均賠 {_money_abs(overview.get('avg_loss'), currency)}。")
    ab = card.get("alpha_beta_breakdown") or {}
    if not ab.get("note") and ab.get("port_tot") is not None:
        bench = ab.get("bench") or "SPY"
        if en:
            line = (f"The measured portfolio returned {_pct(ab.get('port_tot'))} versus {_pct(ab.get('spy_tot'))} "
                    f"for {bench}, a {float(ab.get('excess_vs_spy') or 0) * 100:+.0f} pp difference.")
        else:
            line = (f"可比較的持倉報酬 {_pct(ab.get('port_tot'))}，同期 {bench} {_pct(ab.get('spy_tot'))}，"
                    f"相差 {float(ab.get('excess_vs_spy') or 0) * 100:+.0f} 個百分點。")
        lines.append(line)
        stat = ab.get("alpha_stat") or {}
        if stat.get("alpha_ann") is not None and stat.get("ci95"):
            low, high = stat["ci95"]
            if en:
                lines.append(f"Risk-adjusted alpha was {float(stat['alpha_ann']) * 100:+.0f}% annualized, "
                             f"with a 95% interval from {float(low) * 100:+.0f}% to {float(high) * 100:+.0f}%; "
                             "the interval controls how strong the conclusion may be.")
            else:
                lines.append(f"風險調整後 alpha 年化 {float(stat['alpha_ann']) * 100:+.0f}%，"
                             f"九十五％區間為 {float(low) * 100:+.0f}% 到 {float(high) * 100:+.0f}%；"
                             "定論強度以這個區間為準。")
    cash = card.get("cash") or {}
    if cash.get("reliable") and cash.get("balance") is not None:
        if en:
            lines.append(f"Anchored account cash was {_money(cash.get('balance'), currency)}"
                         + (f", {_pct(cash.get('weight'))} of the account." if cash.get("weight") is not None else "."))
        else:
            lines.append(f"有餘額錨點的帳戶現金為 {_money(cash.get('balance'), currency)}"
                         + (f"，佔帳戶 {_pct(cash.get('weight'))}。" if cash.get("weight") is not None else "。"))
    pa = card.get("payoff_attribution") or {}
    cf = pa.get("counterfactual") or {}
    if cf.get("ticker"):
        after = "—" if cf.get("payoff") is None else f"{float(cf['payoff']):.1f}"
        if en:
            lines.append(f"The largest realized drag was {cf['ticker']} at {_money(cf.get('drag'), currency)}; "
                         f"without it, the payoff ratio would have been {after}.")
        else:
            lines.append(f"最大已實現拖累是 {cf['ticker']}，淨影響 {_money(cf.get('drag'), currency)}；"
                         f"拿掉它後盈虧比會是 {after}。")
    return lines


def _trade_lines(card, language):
    best, worst = card.get("best_trade"), card.get("worst_trade")
    if not best or not worst:
        return []
    currency = _currency(card)
    if language == "en":
        return [
            f"Best: {best['ticker']} {_pct(best.get('ret'))}, {_money(best.get('pnl'), currency)} realized.",
            f"Worst: {worst['ticker']} {_pct(worst.get('ret'))}, {_money(worst.get('pnl'), currency)} realized.",
        ]
    return [
        f"最賺：{best['ticker']} {_pct(best.get('ret'))}，已實現 {_money(best.get('pnl'), currency)}。",
        f"最虧：{worst['ticker']} {_pct(worst.get('ret'))}，已實現 {_money(worst.get('pnl'), currency)}。",
    ]


def render_private(bundle):
    language = bundle.get("language") or "zh-TW"
    copy = load_copy(language)
    narrative = validate_narrative(bundle.get("narrative") or {})
    card = bundle.get("engine_card") or {}
    state = bundle.get("engine_state") or {}
    sections = copy["sections"]
    overview = card.get("overview") or {}
    currency = _currency(card)
    holes = card.get("top_holes") or []
    commitment = bundle.get("commitment") or {}

    lines = [
        "---",
        f"session_id: {bundle.get('session_id')}",
        "privacy: private",
        f"language: {copy['language']}",
        "---",
        "",
        f"# {narrative['headline']}",
        "",
        f"> {copy['private_badge']}",
        "",
    ]
    if bundle.get("route") == "test_drive":
        lines.extend([f"> {copy['demo_badge']}", ""])
    lines.extend([
        narrative["mirror"], "", f"## {sections['numbers']}", "",
        ((f"帳面總損益 {_money(overview.get('total_pnl'), currency)}，其中已實現 "
          f"{_money(overview.get('realized'), currency)}、未實現 {_money(overview.get('unrealized'), currency)}。")
         if copy["language"] != "en" else
         (f"Total P&L was {_money(overview.get('total_pnl'), currency)}: "
          f"{_money(overview.get('realized'), currency)} realized and "
          f"{_money(overview.get('unrealized'), currency)} unrealized.")),
        "",
    ])
    performance = _performance_lines(card, copy["language"])
    if performance:
        lines.extend(performance + [""])
    lines.extend([
        f"## {sections['strength']}",
        "",
        narrative.get("strength") or _best_strength(card, copy["language"]),
        "",
    ])
    trades = _trade_lines(card, copy["language"])
    if trades:
        lines.extend([f"## {sections['trades']}", ""] + [f"- {x}" for x in trades] + [""])
    lines.extend([f"## {sections['hole']}", ""])
    if holes:
        lines.extend([_hole_line(holes[0], copy["language"]), ""])
    if narrative.get("counterfactual"):
        lines.extend([narrative["counterfactual"], ""])

    decisions = _decision_lines(bundle, copy)
    if decisions:
        lines.extend([f"## {sections['motive']}", ""] + [f"- {x}" for x in decisions] + [""])
    etf_lines = _etf_lines(card, copy["language"])
    if etf_lines:
        lines.extend([f"## {sections['etf']}", ""] + [f"- {x}" for x in etf_lines] + [""])
    honesty = _honesty_lines(card, copy)
    if honesty:
        lines.extend([f"## {sections['honesty']}", ""] + [f"- {x}" for x in honesty] + [""])

    rule = commitment.get("rule")
    if rule:
        lines.extend([f"## {sections['rule']}", "", rule, ""])
        if narrative.get("rule_rationale"):
            lines.extend([narrative["rule_rationale"], ""])
    elif ((bundle.get("answers") or {}).get("commitment") or {}).get("choice") == "skip":
        lines.extend([f"## {sections['rule']}", "",
                      ("你這次選擇不設新承諾；下次仍可用同一份基線對帳。" if copy["language"] != "en"
                       else "You chose not to set a new commitment; the same baseline remains available next time."), ""])
    elif state.get("insufficient_data"):
        lines.extend([f"## {sections['rule']}", "",
                      ("樣本仍短，這次不硬塞承諾；先把它當基線。" if copy["language"] != "en"
                       else "The sample is still short, so this review sets a baseline without forcing a commitment."), ""])
    return "\n".join(lines).rstrip() + "\n"


def _public_band(value, language):
    value = float(value or 0)
    if value < 0.25:
        return "低" if language != "en" else "low"
    if value < 0.40:
        return "中" if language != "en" else "moderate"
    if value < 0.60:
        return "高" if language != "en" else "high"
    return "很高" if language != "en" else "very high"


def render_public(bundle):
    """Render a conservative shareable card without user-authored free text."""
    language = bundle.get("language") or "zh-TW"
    copy = load_copy(language)
    card = bundle.get("engine_card") or {}
    holes = card.get("top_holes") or []
    hole = holes[0] if holes else {}
    raw = hole.get("raw") or {}
    dim = raw.get("dim")
    severity = _public_band(hole.get("severity"), copy["language"])
    rule = (bundle.get("commitment") or {}).get("rule")
    if copy["language"] == "en":
        mirror = f"This review found {severity} behavioral pressure in {dim or 'the leading diagnostic dimension'}."
        structure = "Diversified allocation ETFs were separated from single-name risk; focused ETFs remained concentration risk."
    else:
        mirror = f"這次復盤在「{dim or '主要行為維度'}」看見{severity}程度的行為壓力。"
        structure = "配置型 ETF 與單一標的風險分開計算；產業、主題與槓桿 ETF 仍保留集中風險。"
    lines = [
        "---", "privacy: public", f"language: {copy['language']}", "---", "",
        f"# {copy['title']}", "", f"> {copy['public_badge']}", "", mirror, "",
    ]
    if bundle.get("route") == "test_drive":
        lines[10:10] = [f"> {copy['demo_badge']}", ""]
    ps = card.get("portfolio_structure") or {}
    if ps.get("allocation_etfs") or ps.get("concentrated_etfs"):
        lines.extend([f"## {copy['sections']['etf']}", "", structure, ""])
    if rule:
        lines.extend([f"## {copy['sections']['rule']}", "", rule, ""])
    return "\n".join(lines).rstrip() + "\n"


def render_html(markdown_text, title="Trade Review Card"):
    """Dependency-free HTML artifact; Markdown remains the canonical card text."""
    escaped = html.escape(markdown_text)
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>{html.escape(title)}</title><style>"
            "body{margin:0;background:#f4f1ea;color:#191919;font:17px/1.65 system-ui,sans-serif}"
            "article{max-width:760px;margin:40px auto;padding:40px;background:#fff;border:1px solid #d8d1c4;"
            "box-shadow:0 12px 30px #0001}pre{white-space:pre-wrap;font:inherit;margin:0}</style></head>"
            f"<body><article><pre>{escaped}</pre></article></body></html>\n")
