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
import math
import os
import re


class RenderError(ValueError):
    pass


HERE = os.path.dirname(os.path.abspath(__file__))
COPY_DIR = os.path.join(os.path.dirname(HERE), "copy")
ALLOWED_NARRATIVE = {"headline", "mirror", "counterfactual", "rule_rationale", "strength", "honesty"}
DIMENSION_ID_BY_LEGACY_LABEL = {
    "出場紀律": "exit_discipline",
    "部位 sizing": "position_sizing",
    "分散": "diversification",
    "持有時間": "holding_period",
    "加碼攤平": "averaging_down",
    "alpha/beta": "alpha_beta",
    "進場": "entry_style",
}
MARKET_BENCHMARKS = {"TW": "^TWII", "US": "SPY"}
DISPLAY_CURRENCY_BY_LANGUAGE = {"en": "USD", "zh-TW": "TWD", "zh-CN": "CNY"}


def load_copy(language):
    language = "en" if str(language).lower().startswith("en") else "zh-TW"
    with open(os.path.join(COPY_DIR, language + ".json"), encoding="utf-8") as f:
        return json.load(f)


# ── Spelled-out numeric-claim detection (issue #194 item 1) ──────────────────
# The narrative contract is "no numbers; magnitudes come only from the engine".
# ``re.search(r"\d", ...)`` already rejects ASCII and Unicode digits (30, ３０,
# ٣), but not spelled-out quantities, so zh users could smuggle "三成"/"五萬" and
# en users "thirty percent".  ``numeric_claim`` closes that hole with a
# deterministic pass (regex + word tables, no LLM) shared by zh and en.  It is
# the authoritative gate; ``schemas/narrative.schema.json`` documents this and
# no longer tries to express the rule in an ECMA-262 pattern (see that file's
# ``$comment``).
#
# Design bias (from the issue): a false positive only costs the agent a rewrite
# (annoying but safe); a false negative puts a hallucinated number on the card
# (a product red line).  So the rules lean strict, and idioms that merely reuse
# a numeral character are exempted through an explicit allowlist that is punched
# out of the text before scanning.

# CJK numerals that can head a spelled-out quantity.
_CJK_NUMERALS = "零〇一二兩三四五六七八九十百千萬億兆"
# "Hard" units are almost never word-heads, so a numeral in front is always a
# quantity claim (percent / colloquial percent / multiple).
_CJK_HARD_UNITS = "％%趴倍"
# "Soft" units also head common words (成本, 個人, 天氣…), so a numeral+unit only
# counts when the unit sits at a word boundary (not glued to another non-numeral
# Han letter that would form a compound word).
_CJK_SOFT_UNITS = "成元塊股張檔天日週月年季次個點"

# Idioms that reuse a numeral character without asserting a quantity.  They are
# removed before scanning so the rules below never see them.  Tunable: extend
# this list rather than loosening a rule when a legitimate idiom is rejected.
_ZH_IDIOMS = (
    "一起", "一同", "一直", "一致", "一度", "一旦", "一時", "一向", "一律",
    "一連", "一再", "一舉", "一切", "一定", "一般", "一樣", "一些", "一味",
    "一環", "一線", "一路", "一員", "一體", "一如", "一概", "一心", "一面",
    "一來", "統一", "唯一", "專一", "每一", "進一步",
    "一一", "一五一十", "三三兩兩", "兩兩", "三兩", "三天兩頭",
    "十分", "十足", "十全",
    "百分之百", "百分百", "百般",
    "千萬別", "千萬不", "千萬勿", "千萬要", "千萬記", "千萬得", "千萬莫", "千萬請",
    "萬一", "萬分", "萬萬", "萬全", "萬難", "萬象", "萬能", "萬無",
    "兩難", "兩者", "兩極", "兩全", "兩可", "兩相", "兩敗", "兩性", "兩岸", "兩用",
    "第一", "第二", "第三", "第四", "第五",
    "一次性", "再一次", "一次到位", "一次又一次",
)

# Consecutive CJK numerals (三十, 一百, 五萬, 二〇二六) read as an actual number.
_CJK_COMPOUND_RE = re.compile(f"[{_CJK_NUMERALS}]{{2,}}")
# Numeral + hard unit (兩倍, 五趴, 三％).
_CJK_HARD_RE = re.compile(f"[{_CJK_NUMERALS}][{_CJK_HARD_UNITS}]")
# Numeral + soft unit (三成, 五股, 十張); boundary is checked in code.
_CJK_SOFT_RE = re.compile(f"[{_CJK_NUMERALS}][{_CJK_SOFT_UNITS}]")
# Percentage spelled as 百分之X (百分之三十, 百分之五); 百分之百 is an idiom, stripped first.
_CJK_PCT_RE = re.compile(f"百分之[{_CJK_NUMERALS}]")
# Approximate quantifiers (幾十, 數百, 幾成, 幾倍) — 幾/數 only count before a
# magnitude/unit, so 數字/幾乎/多數 stay clean.
_CJK_APPROX_RE = re.compile(f"[幾數](?=[十百千萬億{_CJK_HARD_UNITS}成])")

# English number words and the units that turn them into quantity claims.
_EN_SMALL = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
             "thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
             "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety")
_EN_MAG = "hundred|thousand|million|billion|trillion"
_EN_NUM = f"(?:{_EN_SMALL}|{_EN_MAG})"
_EN_UNIT = "percent|percents|percentage\\s+points?|pp|times|dollars?|cents?|shares?"
# Number word(s) + unit: "thirty percent", "two times", "five thousand dollars".
_EN_UNIT_RE = re.compile(rf"\b{_EN_NUM}(?:[\s-]+(?:{_EN_NUM}|and))*[\s-]+(?:{_EN_UNIT})\b", re.I)
# Compound number: "twenty five", "one hundred".
_EN_COMPOUND_RE = re.compile(rf"\b{_EN_NUM}[\s-]+{_EN_NUM}\b", re.I)
# Standalone plural magnitude: "thousands", "millions".
_EN_MAG_RE = re.compile(r"\b(?:hundreds|thousands|millions|billions|trillions)\b", re.I)


def _forms_word(ch):
    """A non-numeral Han letter after a soft unit means the unit heads a
    compound word (成本, 個人), not a magnitude."""
    return bool(ch) and "一" <= ch <= "鿿" and ch not in _CJK_NUMERALS


def numeric_claim(text):
    """Return a short reason if ``text`` carries a spelled-out numeric/quantity
    claim (CJK or English), else ``None``.

    Deterministic (regex + word tables, no LLM).  ASCII/Unicode digits are
    handled by ``validate_narrative`` via ``re.search(r"\\d", ...)``; this
    function only covers spelled-out forms.
    """
    if not isinstance(text, str):
        return None
    scan = text
    for idiom in _ZH_IDIOMS:
        scan = scan.replace(idiom, " ")
    if _CJK_COMPOUND_RE.search(scan):
        return "spelled-out CJK number (e.g. 三十/五萬)"
    if _CJK_HARD_RE.search(scan):
        return "CJK numeral with a unit (e.g. 倍/趴/%)"
    for match in _CJK_SOFT_RE.finditer(scan):
        after = scan[match.end():match.end() + 1]
        if not _forms_word(after):
            return "CJK numeral with a measure word (e.g. 成/股/次)"
    if _CJK_PCT_RE.search(scan):
        return "CJK percentage (百分之…)"
    if _CJK_APPROX_RE.search(scan):
        return "approximate CJK quantity (e.g. 幾十/數百)"
    if _EN_UNIT_RE.search(scan) or _EN_COMPOUND_RE.search(scan) or _EN_MAG_RE.search(scan):
        return "English number-word quantity (e.g. thirty percent)"
    return None


def validate_narrative(narrative):
    if not isinstance(narrative, dict):
        raise RenderError("narrative must be an object")
    extra = set(narrative) - ALLOWED_NARRATIVE
    if extra:
        raise RenderError("unknown narrative fields: " + ", ".join(sorted(extra)))
    for key, value in narrative.items():
        if key == "honesty":
            if not isinstance(value, dict):
                raise RenderError("narrative.honesty must be an object of ledger-key -> sentence")
            for hkey, hval in value.items():
                if not isinstance(hval, str) or not hval.strip():
                    raise RenderError(f"narrative.honesty.{hkey} must be a non-empty string")
                if re.search(r"\d", hval):
                    raise RenderError(f"narrative.honesty.{hkey} contains digits; numeric claims must come from engine output")
                reason = numeric_claim(hval)
                if reason:
                    raise RenderError(f"narrative.honesty.{hkey} contains a numeric claim ({reason}); magnitudes must come from engine output")
            continue
        if not isinstance(value, str) or not value.strip():
            raise RenderError(f"narrative.{key} must be a non-empty string")
        if re.search(r"\d", value):
            raise RenderError(f"narrative.{key} contains digits; numeric claims must come from engine output")
        reason = numeric_claim(value)
        if reason:
            raise RenderError(f"narrative.{key} contains a numeric claim ({reason}); magnitudes must come from engine output")
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


# ── Candidate-rule grounding (#248) ──────────────────────────────────────────
# The canonical rule text stays a reusable template — it is tracked across
# weeks in rules.jsonl, so a single period's tickers must never be baked into
# it. The tie to the user's actual positions travels as a separate
# engine-authored ``grounding`` sentence instead. Facts come only from
# existing engine_card output (dims_raw / ticker_diagnosis): no new
# computation, and a dimension without citable facts omits the sentence
# rather than printing an empty shell.
RULE_GROUNDING_TICKER_LIMIT = 2


def _grounding_dims(card):
    dims = {}
    for row in (card or {}).get("dims_raw") or []:
        if isinstance(row, dict) and row.get("dim"):
            dims[dimension_id(row.get("dim"))] = row
    return dims


def _diagnosis_ticker_order(card):
    """ticker_diagnosis is already |impact|-ranked by the engine; reuse that
    order so grounding cites the money-relevant names deterministically."""
    order = []
    for row in (card or {}).get("ticker_diagnosis") or []:
        ticker = row.get("ticker") if isinstance(row, dict) else None
        if isinstance(ticker, str) and ticker and ticker not in order:
            order.append(ticker)
    return order


def rule_grounding_facts(card, dim_id):
    """Deterministic per-dimension grounding facts, or ``None`` when the
    dimension has nothing citable in this period's engine card."""
    dims = _grounding_dims(card)
    dim = dims.get(dim_id)
    if not isinstance(dim, dict):
        return None
    if dim_id == "averaging_down":
        tickers = [t for t in dim.get("tickers") or [] if isinstance(t, str) and t]
        count = dim.get("count")
        if not tickers or not isinstance(count, (int, float)) or count < 1:
            return None
        ranked = [t for t in _diagnosis_ticker_order(card) if t in set(tickers)]
        ranked += [t for t in tickers if t not in ranked]
        return {"tickers": ranked[:RULE_GROUNDING_TICKER_LIMIT], "count": int(count)}
    if dim_id == "position_sizing":
        ticker = dim.get("max_ticker")
        pct = _positive_rate(dim.get("max_pct"))
        if not isinstance(ticker, str) or not ticker or pct is None:
            return None
        return {"tickers": [ticker], "pct": pct}
    if dim_id == "diversification":
        # The diversification dimension carries no per-ticker weights of its
        # own; the sizing dimension's risk weights (same engine card, residual
        # and allocation-ETF noise already excluded) name the top positions.
        pct = _positive_rate(dim.get("top3"))
        weights = (dims.get("position_sizing") or {}).get("risk_weights")
        if pct is None or not isinstance(weights, dict):
            return None
        ranked = sorted((t for t in weights
                         if isinstance(t, str) and t
                         and _positive_rate(weights.get(t)) is not None),
                        key=lambda t: (-float(weights[t]), t))
        if not ranked:
            return None
        return {"tickers": ranked[:3], "pct": pct}
    if dim_id == "holding_period":
        tickers = [t for t in dim.get("incon_tickers") or [] if isinstance(t, str) and t]
        if not tickers:
            return None
        return {"tickers": tickers[:RULE_GROUNDING_TICKER_LIMIT]}
    # exit_discipline (and any future dimension) has no per-ticker fact in the
    # engine card yet; stay silent rather than inventing a reference.
    return None


def localized_rule_grounding(dim, language, card):
    """One engine-authored sentence citing this period's actual positions for
    a candidate rule, or ``None`` when the dimension has no citable facts."""
    dim_id = dimension_id(dim)
    facts = rule_grounding_facts(card, dim_id)
    if not facts:
        return None
    copy = load_copy(language)
    template = (copy.get("rule_grounding") or {}).get(dim_id)
    if not template:
        return None
    tickers = facts.get("tickers") or []
    joiner = ", " if copy["language"] == "en" else "、"
    values = {
        "tickers": joiner.join(tickers),
        "ticker": tickers[0] if tickers else "",
        "count": facts.get("count", ""),
        "pct": f"{facts['pct'] * 100:.0f}%" if facts.get("pct") is not None else "",
    }
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return None


# ── Stable-code → copy resolution (#279 i18n phase 1) ────────────────────────
# The engine emits locale-neutral snake_case codes plus raw params for behavior
# tags, prescription rows, and the stress scenario; all localized wording lives
# in copy/<locale>.json. Legacy bundles that persisted zh literals (pre-#279)
# keep today's behavior by design — zh renders them verbatim, en omits them —
# with no migration layer (owner ruling on #279: dev-phase, no compat mapping).


def _tag_format_values(params):
    """Presentation formatting for raw tag params, shared by every locale."""
    values = {}
    for key in ("n_adds", "win_early", "win_n"):
        number = _finite_number((params or {}).get(key))
        if number is not None:
            values[key] = int(number)
    cur = _finite_number((params or {}).get("cur"))
    if cur is not None:
        values["cur_pct"] = f"{cur * 100:.0f}%"
        values["cur_abs_pct"] = f"{abs(cur) * 100:.0f}%"
        values["cur_signed_pct"] = f"{cur * 100:+.0f}%"
    wpct = _finite_number((params or {}).get("wpct"))
    if wpct is not None:
        values["wpct_pct"] = f"{wpct * 100:.0f}%"
    return values


def localized_instrument_tag(tag, language):
    """Resolve one engine behavior tag to display text, or ``None`` to omit.

    Coded tags ({"code", "params"}) resolve through copy ``instrument_tags``.
    Legacy string tags (persisted zh literals) render verbatim on zh only."""
    if isinstance(tag, str):
        return (tag or None) if language != "en" else None
    if not isinstance(tag, dict):
        return None
    template = (load_copy(language).get("instrument_tags") or {}).get(tag.get("code"))
    if not template:
        return None
    try:
        return template.format(**_tag_format_values(tag.get("params")))
    except (KeyError, IndexError, ValueError):
        return None


def localized_prescription(item, language):
    """Resolve one prescription row to ``{"kind", "text"}``, or ``None``.

    Coded rows resolve kind + sentence template through copy; legacy rows
    (persisted zh ``kind``/``text``) render verbatim on zh only."""
    if not isinstance(item, dict):
        return None
    copy = load_copy(language)
    code = item.get("code")
    if not code:
        kind = str(item.get("kind") or "").strip()
        text = str(item.get("text") or "").strip()
        return {"kind": kind, "text": text} if language != "en" and kind and text else None
    kind = (copy.get("prescription_kinds") or {}).get(item.get("kind"))
    template = (copy.get("prescription_texts") or {}).get(code)
    if not kind or not template:
        return None
    params = item.get("params") or {}
    values = {}
    for key, target in (("excess", "excess_pp"), ("allocation", "alloc_pp"),
                        ("selection", "sel_pp")):
        number = _finite_number(params.get(key))
        if number is not None:
            values[target] = f"{number * 100:+.0f}"
    count = _finite_number(params.get("count"))
    if count is not None:
        values["count"] = int(count)
    max_pct = _finite_number(params.get("max_pct"))
    if max_pct is not None:
        values["max_pct_pct"] = f"{max_pct * 100:.0f}%"
    if params.get("ticker") is not None:
        values["ticker"] = str(params["ticker"])
    if code == "selection_inconclusive":
        texts = copy.get("prescription_texts") or {}
        t = _finite_number(params.get("t"))
        if t is not None:
            note_template = texts.get("selection_inconclusive_t_wide") or ""
            values["t_note"] = note_template.format(t=f"{t:.1f}")
        else:
            values["t_note"] = texts.get("selection_inconclusive_t_unstable") or ""
    try:
        return {"kind": kind, "text": template.format(**values)}
    except (KeyError, IndexError, ValueError):
        return None


def localized_stress_label(stress, language):
    """Resolve the stress scenario to its localized subject label, or ``None``.

    Coded scenarios resolve through copy ``stress_test.labels``; the legacy
    persisted ``label`` zh literal renders verbatim on zh only."""
    scenario = (stress or {}).get("scenario")
    if isinstance(scenario, dict):
        labels = (load_copy(language).get("stress_test") or {}).get("labels") or {}
        template = labels.get(scenario.get("kind"))
        if not template:
            return None
        try:
            label = template.format(sector=scenario.get("sector") or "",
                                    ticker=scenario.get("ticker") or "")
        except (KeyError, IndexError, ValueError):
            return None
        return label.strip() or None
    legacy = str((stress or {}).get("label") or "").strip()
    return (legacy or None) if language != "en" else None


def _currency(card):
    return ((card.get("currency_meta") or {}).get("aggregate_currency") or "USD").upper()


def default_display_currency(language):
    normalized = str(language or "zh-TW").strip().lower()
    if normalized.startswith("en"):
        return DISPLAY_CURRENCY_BY_LANGUAGE["en"]
    if normalized.startswith("zh-cn") or normalized.startswith("zh-hans"):
        return DISPLAY_CURRENCY_BY_LANGUAGE["zh-CN"]
    return DISPLAY_CURRENCY_BY_LANGUAGE["zh-TW"]


def _positive_rate(value):
    number = _finite_number(value)
    return number if number is not None and number > 0 else None


def _display_context(card, language):
    """Return the frozen aggregate-to-display conversion, or an honest gap.

    A mixed portfolio is aggregated by the engine in a common currency (USD in
    the current contract).  The locale changes only its presentation.  Old
    bundles without the explicit display fields remain readable by deriving the
    rate from their frozen ``currency_meta.fx`` map.
    """
    meta = (card or {}).get("currency_meta") or {}
    aggregate = _currency(card or {})
    if not meta.get("mixed"):
        return {"currency": aggregate, "factor": 1.0, "source": "identity", "as_of": None}

    requested = str(meta.get("requested_display_currency") or
                    default_display_currency(language)).upper()
    fx = meta.get("fx") or {}
    currencies = list(meta.get("currencies") or [])
    explicit_gaps = (((card or {}).get("data_integrity") or {}).get("fx_gaps") or [])
    held_rate_missing = bool(explicit_gaps) or bool(currencies and any(
        str(currency).upper() != "USD" and _positive_rate(fx.get(str(currency).upper())) is None
        for currency in currencies
    ))
    if held_rate_missing:
        return {"currency": None, "factor": None, "source": "unavailable", "as_of": None,
                "requested": requested, "reason": "portfolio_fx_gap"}
    source = meta.get("display_fx_source")
    selected = meta.get("display_currency")
    if source == "unavailable" or selected is None and "display_currency" in meta:
        return {"currency": None, "factor": None, "source": "unavailable", "as_of": None,
                "requested": requested, "reason": meta.get("display_fx_reason")}
    selected = str(selected or requested).upper()
    aggregate_rate = 1.0 if aggregate == "USD" else _positive_rate(fx.get(aggregate))
    selected_rate = (1.0 if selected == "USD" else
                     _positive_rate(meta.get("display_fx_rate")) or _positive_rate(fx.get(selected)))
    if aggregate_rate is None or selected_rate is None:
        return {"currency": None, "factor": None, "source": "unavailable", "as_of": None,
                "requested": requested, "reason": meta.get("display_fx_reason")}
    return {"currency": selected, "factor": aggregate_rate / selected_rate,
            "source": source or "current", "as_of": meta.get("display_fx_as_of"),
            "requested": requested}


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


def _display_money(value, context, absolute=False):
    if not context.get("currency") or context.get("factor") is None:
        return None
    converted = None if value is None else float(value) * float(context["factor"])
    return (_money_abs if absolute else _money)(converted, context["currency"])


def _currency_note(card, language):
    context = _display_context(card, language)
    if context.get("source") == "cached":
        when = context.get("as_of")
        if language == "en":
            return (f"Display conversion uses the FX rate cached at the prior review ({when})."
                    if when else "Display conversion uses the FX rate cached at the prior review.")
        return (f"顯示換算沿用上次對帳匯率（截至 {when}）。"
                if when else "顯示換算沿用上次對帳匯率。")
    if context.get("source") == "unavailable":
        if context.get("reason") == "portfolio_fx_gap":
            if language == "en":
                return "At least one held-currency FX rate was unavailable; amounts remain in original currencies."
            return "至少一個持倉幣別缺少可靠匯率；金額保留原幣，不把近似聚合值當成精確換算。"
        requested = context.get("requested") or default_display_currency(language)
        if language == "en":
            return f"No reliable {requested} display rate was available; amounts remain in original currencies."
        return f"找不到可靠的 {requested} 顯示匯率；金額保留原幣，不做猜測換算。"
    return None


def _original_pnl_lines(card, language):
    rows = ((card.get("currency_meta") or {}).get("pnl_by_currency") or {})
    lines = []
    for currency, row in sorted(rows.items()):
        realized = _finite_number((row or {}).get("realized"))
        unrealized = _finite_number((row or {}).get("unrealized"))
        if realized is None and unrealized is None:
            continue
        if realized is not None and unrealized is not None:
            total = realized + unrealized
            if language == "en":
                lines.append(f"{currency} P&L was {_money(total, currency)}: "
                             f"{_money(realized, currency)} realized and "
                             f"{_money(unrealized, currency)} unrealized.")
            else:
                lines.append(f"{currency} 帳面損益 {_money(total, currency)}，其中已實現 "
                             f"{_money(realized, currency)}、未實現 {_money(unrealized, currency)}。")
        elif realized is not None:
            lines.append((f"{currency} realized P&L was {_money(realized, currency)}."
                          if language == "en" else
                          f"{currency} 已實現損益 {_money(realized, currency)}。"))
        else:
            lines.append((f"{currency} unrealized P&L was {_money(unrealized, currency)}."
                          if language == "en" else
                          f"{currency} 未實現損益 {_money(unrealized, currency)}。"))
    return lines


def _overview_lines(card, language):
    overview = card.get("overview") or {}
    context = _display_context(card, language)
    if context.get("currency"):
        total_value = _finite_number(overview.get("total_pnl"))
        realized_value = _finite_number(overview.get("realized"))
        unrealized_value = _finite_number(overview.get("unrealized"))
        if total_value is not None and realized_value is not None and unrealized_value is not None:
            total = _display_money(total_value, context)
            realized = _display_money(realized_value, context)
            unrealized = _display_money(unrealized_value, context)
            if language == "en":
                return [f"Total P&L was {total}: {realized} realized and {unrealized} unrealized."]
            return [f"帳面總損益 {total}，其中已實現 {realized}、未實現 {unrealized}。"]
        if realized_value is not None:
            realized = _display_money(realized_value, context)
            return ([f"Realized P&L was {realized}; current unrealized P&L was not scored."]
                    if language == "en" else
                    [f"已實現損益 {realized}；目前未實現損益未評分。"])
        if unrealized_value is not None:
            unrealized = _display_money(unrealized_value, context)
            return ([f"Unrealized P&L was {unrealized}; realized P&L was unavailable."]
                    if language == "en" else
                    [f"未實現損益 {unrealized}；已實現損益無法取得。"])
        return []
    return _original_pnl_lines(card, language)


def _pct(value, digits=0):
    return "—" if value is None else f"{float(value) * 100:.{digits}f}%"


def _finite_number(value):
    """Return a finite engine-owned number, or None without inventing a zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _benchmark_pp(value):
    """Format an engine ratio as signed percentage points without negative zero."""
    number = _finite_number(value)
    if number is None:
        return "—"
    # Match the renderer's existing whole-point, half-even rounding while
    # converting the rounded result to int so a negative zero is impossible.
    points = int(round(number * 100))
    return f"{points:+d}"


def _beta_text(value):
    """Format a finite beta to two decimals without exposing negative zero."""
    number = _finite_number(value)
    if number is None:
        return None
    rounded = round(number, 2)
    if rounded == 0:
        rounded = 0.0
    return f"{rounded:.2f}"


def _benchmark_rows(card):
    """Normalize single- and mixed-market attribution for deterministic rendering.

    Mixed-market cards intentionally ignore the compatibility fields copied to
    the top level: those fields describe only the largest market, not a combined
    portfolio.  Only the two engine-supported market identifiers are rendered,
    in a stable order, and incomplete rows are omitted rather than zero-filled.
    """
    ab = card.get("alpha_beta_breakdown") or {}
    by_market = ab.get("by_market")
    if isinstance(by_market, dict) and by_market:
        rows = []
        for market in MARKET_BENCHMARKS:
            row = by_market.get(market)
            if not isinstance(row, dict) or row.get("note"):
                continue
            if any(_finite_number(row.get(key)) is None
                   for key in ("port_tot", "spy_tot", "excess_vs_spy")):
                continue
            rows.append((market, MARKET_BENCHMARKS[market], row))
        return rows
    if not isinstance(ab, dict) or ab.get("note"):
        return []
    if any(_finite_number(ab.get(key)) is None
           for key in ("port_tot", "spy_tot", "excess_vs_spy")):
        return []
    bench = ab.get("bench")
    if bench not in set(MARKET_BENCHMARKS.values()):
        bench = None
    return [(None, bench, ab)]


def _private_benchmark_line(market, bench, row, language):
    port = _finite_number(row.get("port_tot"))
    benchmark = _finite_number(row.get("spy_tot"))
    excess = _finite_number(row.get("excess_vs_spy"))
    beta = _beta_text(row.get("beta"))
    beta_suffix = ((f"; β {beta}" if language == "en" else f"；β {beta}")
                   if beta is not None else "")
    if language == "en":
        subject = f"{market} holdings" if market else "The measured portfolio"
        comparator = bench or "its market benchmark"
        return (f"{subject} returned {_pct(port)} versus {_pct(benchmark)} for {comparator}, "
                f"a {_benchmark_pp(excess)} pp difference{beta_suffix}.")
    subject = f"{market} 部位" if market else "可比較的持倉"
    comparator = bench or "市場大盤"
    return (f"{subject}報酬 {_pct(port)}，同期 {comparator} {_pct(benchmark)}，"
            f"相差 {_benchmark_pp(excess)} 個百分點{beta_suffix}。")


def _private_split_lines(market, row, language):
    """Explain positive benchmark excess using the engine's accounting split."""
    excess = _finite_number(row.get("excess_vs_spy"))
    split = row.get("excess_split") or {}
    allocation = _finite_number(split.get("allocation"))
    selection = _finite_number(split.get("selection"))
    if excess is None or excess <= 0 or allocation is None or selection is None:
        return []
    subject_en = f"{market}'s" if market else "The portfolio's"
    prefix_zh = f"{market} " if market else ""
    if language == "en":
        line = (f"{subject_en} {_benchmark_pp(excess)} pp excess split into "
                f"{_benchmark_pp(allocation)} pp from market/sector allocation and "
                f"{_benchmark_pp(selection)} pp from security selection.")
    else:
        line = (f"{prefix_zh}贏大盤的 {_benchmark_pp(excess)} 個百分點拆為："
                f"市場／賽道配置 {_benchmark_pp(allocation)} 個百分點、"
                f"標的選擇 {_benchmark_pp(selection)} 個百分點。")
    # Coverage limitations belong to the engine-triggered sector_attribution
    # honesty entry, which _performance_lines places once after this split.
    return [line]


def _alpha_interval_line(ab, language):
    stat = ab.get("alpha_stat") or {}
    alpha = _finite_number(stat.get("alpha_ann"))
    ci = stat.get("ci95")
    if alpha is None or not isinstance(ci, (list, tuple)) or len(ci) != 2:
        return None
    low, high = (_finite_number(ci[0]), _finite_number(ci[1]))
    if low is None or high is None:
        return None
    market = ab.get("scope") if isinstance(ab.get("by_market"), dict) else None
    scope_en = f" for {market} holdings" if market in MARKET_BENCHMARKS else ""
    scope_zh = f"（{market} 部位）" if market in MARKET_BENCHMARKS else ""
    if language == "en":
        return (f"Risk-adjusted alpha{scope_en} was {alpha * 100:+.0f}% annualized, "
                f"with a 95% interval from {low * 100:+.0f}% to {high * 100:+.0f}%; "
                "the interval controls how strong the conclusion may be.")
    # #272: Arabic digits for the interval level — one digit style per sentence.
    return (f"風險調整後 alpha{scope_zh}年化 {alpha * 100:+.0f}%，"
            f"95% 區間為 {low * 100:+.0f}% 到 {high * 100:+.0f}%；"
            "定論強度以這個區間為準。")


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


# ── Monthly vs-market cadence (#284, output contract §3) ─────────────────────
# The vs-market comparison (benchmark line, split, alpha interval, comparator
# rows, excess/alpha KPI tiles) renders on the first full review of each
# calendar month. review.py freezes that decision into the engine card at
# prepare time (precedent: _apply_display_currency freezes display currency);
# the renderer only reads the frozen decision. A card without the field
# (legacy bundles, direct engine output) always renders the segment —
# fail-closed toward showing. When the segment is gated out, the honesty keys
# whose sentences ride its lines are not required from the agent and must not
# leak into the footnote (review.py filters required_honesty_keys with the
# same constant).
VS_MARKET_HONESTY_KEYS = ("alpha_credibility", "sector_attribution")


def vs_market_suppressed(card):
    """True only when a prepare-time month-gate explicitly suppressed ③ vs market."""
    gate = (card or {}).get("vs_market_gate")
    return isinstance(gate, dict) and gate.get("render") is False


def _honesty_lines(bundle, copy):
    """Sentence per triggered honesty key, agent-authored first (#82).

    The agent writes the wording in narrative.honesty (gated at preview so every
    triggered key is covered); fixed copy strings remain only as a fallback for
    re-rendering bundles committed before this contract. Returns a dict so the
    renderer can weave each sentence into its related section instead of
    printing the ledger as a checklist."""
    card = bundle.get("engine_card") or {}
    authored = (bundle.get("narrative") or {}).get("honesty") or {}
    fallback = copy.get("honesty") or {}
    suppressed = vs_market_suppressed(card)
    lines = {}
    for entry in card.get("honesty_ledger") or []:
        key = entry.get("key")
        if key in lines:
            continue
        if suppressed and key in VS_MARKET_HONESTY_KEYS:
            # #284: the sentences' host lines are month-gated out; the keys are
            # not required from the agent and a copy fallback must not surface
            # them in the footnote either.
            continue
        lines[key] = authored.get(key) or fallback.get(key) or key
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


def _decision_entries(bundle, copy):
    """(ticker, line) pairs so Block 2 can attach each motive to its instrument
    row (contract §2); a pair without a row keeps its line at block level."""
    labels = copy.get("add_choices") or {}
    entries = []
    for event in bundle.get("thesis_decisions") or []:
        label = labels.get(event.get("decision"), event.get("decision"))
        ticker = event.get("ticker") or "position"
        if copy.get("language") == "en":
            entries.append((event.get("ticker"),
                            f"{ticker}: {label}. The decision and its evidence boundary were saved for the next review."))
        else:
            entries.append((event.get("ticker"),
                            f"{ticker}：{label}。這個判斷與證據邊界已保存，供下次對帳。"))
    return entries


def _headline_motive_entries(bundle, copy):
    """Return the localized rendering of typed headline-motive decisions.

    The event's context is copied from the engine-owned question opportunity.
    A ticker/fact therefore appears only when the engine supplied it; rendering
    never mines the user's prose or infers a security from the chosen class.
    """
    labels = copy.get("headline_motive_choices") or {}
    entries = []
    en = copy.get("language") == "en"
    for event in bundle.get("headline_motive_events") or []:
        choice = event.get("decision")
        label = labels.get(choice, choice)
        context = event.get("context") or {}
        dimension = (context.get("headline_dimension") or {}).get("label")
        ticker = context.get("ticker")
        fact = context.get("asked_because")
        subject = dimension or ("highlighted behavior" if en else "這次浮現的行為")
        if en:
            parts = []
            if fact:
                parts.append(f"Engine context: {str(fact).rstrip('.')}.")
            parts.append(f"Motive recorded for {subject}: {label}.")
            parts.append("This recorded choice was saved for a later review.")
        else:
            parts = []
            if fact:
                parts.append(f"引擎脈絡：{str(fact).rstrip('。')}。")
            parts.append(f"「{subject}」的動機記為：{label}。")
            parts.append("這個選項已保存，供後續復盤對帳。")
        entries.append((ticker, " ".join(parts)))
    return entries


def _exit_entries(bundle, copy):
    entries = []
    for event in bundle.get("exit_narratives") or []:
        if event.get("capture") == "skipped":
            continue
        kind = event.get("exit_kind") or "full"
        labels = (copy.get("exit_choices") or {}).get(kind) or {}
        reason = event.get("exit_reason")
        note = event.get("note")
        label = labels.get(reason, reason) if reason else note
        if not label:
            continue
        if reason and note:
            label = f"{label} ({note})"
        ticker = event.get("ticker") or ("position" if copy.get("language") == "en" else "這筆部位")
        if copy.get("language") == "en":
            action = "exit" if kind == "full" else "reduction"
            entries.append((event.get("ticker"),
                            f"{ticker}: you recorded the {action} reason as “{label}”. "
                            "This preserves the reason at the time; it does not judge the outcome yet."))
        else:
            action = "清倉" if kind == "full" else "減倉"
            entries.append((event.get("ticker"),
                            f"{ticker}：你把這次{action}記為「{label}」。"
                            "這裡只保存當時的理由，尚未判斷決策結果。"))
    return entries


# ── Block 1 caveat placement (output contract §4) ────────────────────────────
# Every honesty sentence rides the number it qualifies: one indented line
# directly under that indicator. The key → host mapping is renderer-side
# (consumer) knowledge next to _honesty_lines; review.py stays untouched.
# Hosts are indicator tags emitted by _performance_items; a key tries its
# chain in order and an occupied host line never takes a second caveat, so
# the ledger can never re-form the consecutive-paragraph wall (#276 root
# cause B). Keys with no reachable host collapse into the Block-1 footnote.
_HONESTY_HOSTS = {
    "unrealized_coverage": ("pnl",),
    "orphan_sells": ("payoff", "pnl"),
    "currency_mix": ("currency_note", "pnl"),
    "cash_reliability": ("cash", "account"),
    "acct_perf_basis": ("account", "cash"),
    "alpha_credibility": ("alpha", "benchmark"),
    "sector_attribution": ("split", "benchmark"),
    "unclassified_drivers": ("stress", "benchmark"),
}
# etf_metadata rides the ETF lines in Block 2 (special-cased in _card_structure);
# every other key (snapshot/accounting reconciliation, future keys) is footnote-only.


def _place_caveats(items, honesty):
    """Insert hosted honesty sentences as caveat items under their indicators.

    ``items`` are Block-1 indicator dicts ({"kind", "tag", "text"}); ``honesty``
    is consumed for every placed key and keeps the leftovers for the footnote.
    Placement is deterministic: ledger order for keys, first free host wins,
    at most one caveat per indicator line."""
    placements = {}
    for key in [k for k in list(honesty) if k in _HONESTY_HOSTS]:
        for tag in _HONESTY_HOSTS[key]:
            index = next((i for i, item in enumerate(items)
                          if item["kind"] == "line" and item.get("tag") == tag
                          and i not in placements), None)
            if index is not None:
                placements[index] = key
                break
    out = []
    for index, item in enumerate(items):
        out.append(item)
        key = placements.get(index)
        if key is not None:
            out.append({"kind": "caveat", "tag": None, "text": honesty.pop(key)})
    return out


def _performance_items(card, language):
    """Block-1 indicator lines as tagged items, in the contract §2 order:
    ① absolute P&L (KPI-mirror line) → payoff/drag → ② annualized/account →
    cash → ③ vs market (benchmark/split/alpha) → alternative comparators.

    Tags are the caveat hosts (_HONESTY_HOSTS); text wording reuses the same
    sentences the card always printed so no engine number changes shape."""
    copy = load_copy(language)
    overview = card.get("overview") or {}
    display = _display_context(card, language)
    en = language == "en"
    kpi_copy = copy.get("kpi") or {}
    items = []

    def line(tag, text):
        items.append({"kind": "line", "tag": tag, "text": text})

    # ① absolute P&L: one numbers-summary line mirroring the HTML KPI tile
    # (README anchor "Total P&L +$138,058 (realized $19k + unrealized $119k)");
    # partial or original-currency accounts keep the sentence fallbacks.
    total = _finite_number(overview.get("total_pnl"))
    realized = _finite_number(overview.get("realized"))
    unrealized = _finite_number(overview.get("unrealized"))
    if (display.get("currency") and total is not None and realized is not None
            and unrealized is not None and kpi_copy.get("pnl") and kpi_copy.get("pnl_sub")):
        sub = kpi_copy["pnl_sub"].format(realized=_display_money(realized, display),
                                         unrealized=_display_money(unrealized, display))
        line("pnl", f"{kpi_copy['pnl']} {_display_money(total, display)}"
             + (f" ({sub})" if en else f"（{sub}）"))
    else:
        for text in _overview_lines(card, language):
            line("pnl", text)
    currency_note = _currency_note(card, language)
    if currency_note:
        line("currency_note", currency_note)
    payoff = overview.get("payoff")
    if payoff is not None:
        avg_win = _display_money(overview.get("avg_win"), display)
        avg_loss = _display_money(overview.get("avg_loss"), display, absolute=True)
        if (avg_win is not None and avg_loss is not None
                and kpi_copy.get("payoff") and kpi_copy.get("payoff_sub")):
            sub = kpi_copy["payoff_sub"].format(win=avg_win, loss=avg_loss)
            line("payoff", f"{kpi_copy['payoff']} {float(payoff):.1f}"
                 + (f" ({sub})" if en else f"（{sub}）"))
        else:
            line("payoff", (f"Realized payoff ratio was {payoff:.1f}; average gain/loss amounts remain "
                            "in original currencies." if en else
                            f"已實現盈虧比 {payoff:.1f}；平均盈虧金額因顯示匯率缺失而保留原幣。"))
    pa = card.get("payoff_attribution") or {}
    cf = pa.get("counterfactual") or {}
    if cf.get("ticker"):
        after = "—" if cf.get("payoff") is None else f"{float(cf['payoff']):.1f}"
        drag = _display_money(cf.get("drag"), display)
        if en and drag is not None:
            line("drag", f"The largest realized drag was {cf['ticker']} at {drag}; "
                         f"without it, the payoff ratio would have been {after}.")
        elif not en and drag is not None:
            line("drag", f"最大已實現拖累是 {cf['ticker']}，淨影響 {drag}；"
                         f"拿掉它後盈虧比會是 {after}。")
        else:
            line("drag", (f"The largest realized drag was {cf['ticker']}; without it, the payoff ratio "
                          f"would have been {after}." if en else
                          f"最大已實現拖累是 {cf['ticker']}；拿掉它後盈虧比會是 {after}。"))
    # ② annualized return / account pillar (#179/#181): verbatim engine numbers;
    # a gated account level renders the unlock invitation, never the raw note.
    # Cash drag stays a neutral observation, never a verdict on holding cash.
    ap = card.get("acct_perf") or {}
    if ap.get("hold_twr") is not None:
        window = (ap.get("window") or {}).get("days")
        if en:
            line("account_hold", f"Holdings-only time-weighted return was {_pct(ap.get('hold_twr'))}"
                 + (f" over the {int(window)}-day window." if window else "."))
        else:
            line("account_hold", f"持倉柱的時間加權報酬為 {_pct(ap.get('hold_twr'))}"
                 + (f"（{int(window)} 天窗口）。" if window else "。"))
        if ap.get("acct_twr") is not None:
            if en:
                text = f"Account-level time-weighted return was {_pct(ap.get('acct_twr'))}"
                if ap.get("irr_annual") is not None:
                    # Output contract: plain phrase, not the IRR jargon token.
                    text += f"; annualized return was {_pct(ap.get('irr_annual'))}"
                if ap.get("cash_drag") is not None:
                    text += (f"; the gap versus the holdings pillar, {_pct(ap.get('cash_drag'))}, "
                             "is explained by holding cash — an observation, not a verdict")
                line("account", text + ".")
            else:
                text = f"帳戶級時間加權報酬為 {_pct(ap.get('acct_twr'))}"
                if ap.get("irr_annual") is not None:
                    # Output contract: plain phrase, not the IRR jargon token.
                    text += f"，年化報酬 {_pct(ap.get('irr_annual'))}"
                if ap.get("cash_drag") is not None:
                    text += f"；與持倉柱的差距 {_pct(ap.get('cash_drag'))} 來自持有現金——這是觀察，不是對錯判定"
                line("account", text + "。")
        elif ap.get("note"):
            line("account_gate",
                 "Account-level return stays locked until cash has a complete anchor; "
                 "the holdings pillar above is unaffected." if en else
                 "帳戶級報酬先不出，等現金錨點補齊即解鎖；上面的持倉柱不受影響。")
    cash = card.get("cash") or {}
    if cash.get("reliable") and cash.get("balance") is not None:
        display_cash = _display_money(cash.get("balance"), display)
        if en and display_cash is not None:
            line("cash", f"Anchored account cash was {display_cash}"
                 + (f", {_pct(cash.get('weight'))} of the account." if cash.get("weight") is not None else "."))
        elif not en and display_cash is not None:
            line("cash", f"有餘額錨點的帳戶現金為 {display_cash}"
                 + (f"，佔帳戶 {_pct(cash.get('weight'))}。" if cash.get("weight") is not None else "。"))
        else:
            original = []
            for currency, row in sorted((cash.get("by_currency") or {}).items()):
                if (row or {}).get("balance") is not None:
                    original.append(_money((row or {}).get("balance"), currency))
            if original:
                line("cash", ("Anchored account cash by original currency: " + ", ".join(original) + "."
                              if en else "有餘額錨點的帳戶現金（原幣）：" + "、".join(original) + "。"))
    # ③ vs market: benchmark rows, the winning split, the alpha interval, then
    # the alternative comparators the HTML bars show (md keeps them as one line).
    # Monthly cadence (#284, contract §3): a prepare-time gate suppresses the
    # whole segment on later full reviews of the same month — the lines are
    # simply absent, and _performance_block skips the gap note too.
    if not vs_market_suppressed(card):
        ab = card.get("alpha_beta_breakdown") or {}
        benchmark_rows = _benchmark_rows(card)
        for market, bench, row in benchmark_rows:
            line("benchmark", _private_benchmark_line(market, bench, row, language))
            for text in _private_split_lines(market, row, language):
                line("split", text)
        if benchmark_rows:
            alpha_line = _alpha_interval_line(ab, language)
            if alpha_line:
                line("alpha", alpha_line)
        attribution = _attribution_facts(card)
        if attribution:
            items.append({"kind": "attr_rows", "tag": None,
                          "text": " · ".join("vs " + row["label"] + " " + row["pp"]
                                             for row in attribution["rows"])})
    return items


def _performance_lines(card, language, honesty=None):
    """Legacy flat projection of the Block-1 performance cluster.

    Unit-test compatibility shim over ``_performance_items`` +
    ``_place_caveats``; the card structure consumes the structured items
    directly. Leftover honesty (minus the Block-2-hosted etf_metadata) is
    appended so a triggered disclosure can never be dropped."""
    honesty = dict(honesty) if honesty is not None else {}
    items = _place_caveats(_performance_items(card, language), honesty)
    lines = [item["text"] for item in items if item["kind"] in ("line", "caveat")]
    lines.extend(honesty[key] for key in honesty if key != "etf_metadata")
    return lines


def _metric_display(key, value):
    if value is None:
        return "—"
    if key and key.endswith("_pct"):
        return _pct(value)
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return f"{value}"


def _reconciliation_lines(bundle, language):
    """#151/#152 loop anchor: open the card against last time's commitment.

    Prints the committed rule plus the metric's then/now values verbatim from
    engine state — the renderer never computes a delta, and the agent never
    touches the numbers."""
    prior = ((bundle.get("review_plan") or {}).get("state_snapshot") or {}).get("prior_commitment") or {}
    if not prior.get("rule"):
        return []
    key = prior.get("metric_key")
    then_v = prior.get("metric_value")
    now_v = ((bundle.get("engine_state") or {}).get("metrics") or {}).get(key) if key else None
    if language == "en":
        line = f"Last time you committed: \"{prior['rule']}\""
        if then_v is not None and now_v is not None:
            # A-12: never print internal metric keys on the card — values only.
            line += f" — the tracked number was {_metric_display(key, then_v)} then, {_metric_display(key, now_v)} now"
        return [line + "."]
    line = f"上次你承諾：「{prior['rule']}」"
    if then_v is not None and now_v is not None:
        line += f"——追蹤的數字當時 {_metric_display(key, then_v)}，這次 {_metric_display(key, now_v)}"
    return [line + "。"]


def _review_opening_lines(bundle, language):
    """Show the history frozen at prepare time, never a racy global ordinal."""
    reconciliation = _reconciliation_lines(bundle, language)
    progress = (((bundle.get("review_plan") or {}).get("state_snapshot") or {})
                .get("review_progress") or {})
    if not isinstance(progress, dict):
        progress = {}
    try:
        completed = int(progress.get("completed_reviews_before_start"))
    except (TypeError, ValueError):
        completed = 0
    milestone = None
    if progress.get("returning") is True and completed > 0:
        if language == "en":
            noun = "review" if completed == 1 else "reviews"
            milestone = f"When this review started, you already had {completed} completed {noun}."
        else:
            milestone = f"開始這次復盤時，你已有 {completed} 次完成紀錄。"
    if milestone and reconciliation:
        reconciliation[0] = f"{reconciliation[0]} {milestone}"
    elif milestone:
        reconciliation = [milestone]
    return reconciliation


def _copy_string(copy, key, fallback):
    """Read a localized string while keeping older copy bundles renderable."""
    value = copy.get(key)
    if not isinstance(value, str) or not value.strip():
        value = (copy.get("sections") or {}).get(key)
    return value if isinstance(value, str) and value.strip() else fallback


def _snapshot_summary(card):
    summary = card.get("snapshot_summary") or {}
    return summary if isinstance(summary, dict) else {}


def _snapshot_overview_lines(card, copy):
    """Render only facts supported by an opening position snapshot."""
    summary = _snapshot_summary(card)
    en = copy.get("language") == "en"
    positions_n = _finite_number(summary.get("positions_n"))
    positions = (str(int(positions_n)) if positions_n is not None
                 and positions_n.is_integer() else None)
    as_of = summary.get("as_of")
    basis = summary.get("valuation_basis")
    weights_available = summary.get("weights_available") is True

    if en:
        subject = f"{positions} supplied positions" if positions is not None else "the supplied positions"
        opening = f"This is an opening portfolio check of {subject}"
        if as_of:
            opening += f" as of {as_of}"
        opening += "."
        if weights_available and basis == "market_value":
            valuation = "Structural weights use the supplied market-value basis."
        elif weights_available and basis == "cost":
            valuation = "Structural weights use the supplied cost basis."
        else:
            valuation = "No reliable valuation basis was available, so weight-based structure remains unscored."
        scope = _copy_string(
            copy, "snapshot_scope",
            "Transaction-history dimensions — averaging down, exit discipline, win rate, payoff, and historical motives — were not scored. Import transaction history later to unlock them.",
        )
    else:
        subject = f"使用者提供的 {positions} 個持倉" if positions is not None else "使用者提供的持倉"
        opening = f"這是針對{subject}的開場組合檢查"
        if as_of:
            opening += f"，快照截至 {as_of}"
        opening += "。"
        if weights_available and basis == "market_value":
            valuation = "結構權重採使用者提供的市值口徑。"
        elif weights_available and basis == "cost":
            valuation = "結構權重採使用者提供的成本口徑。"
        else:
            valuation = "目前沒有可靠估值口徑，因此不評分依賴權重的組合結構。"
        scope = _copy_string(
            copy, "snapshot_scope",
            "交易歷史維度——攤平、出場紀律、勝率、盈虧比與歷史動機——這次都不評分；之後匯入交易紀錄即可解鎖。",
        )

    integrity = []
    missing_avg_cost = summary.get("missing_avg_cost") or []
    fx_gaps = summary.get("fx_gaps") or []
    if isinstance(missing_avg_cost, list) and missing_avg_cost:
        tickers = ", ".join(str(x) for x in missing_avg_cost)
        integrity.append((f"Average cost was missing for: {tickers}." if en else
                          f"以下持倉缺少平均成本：{tickers}。"))
    if isinstance(fx_gaps, list) and fx_gaps:
        currencies = ", ".join(str(x) for x in fx_gaps)
        integrity.append((f"Reliable FX coverage was missing for: {currencies}." if en else
                          f"以下幣別缺少可靠匯率：{currencies}。"))
    return [opening, valuation] + integrity + [scope]


def _snapshot_strength_line(card, language):
    summary = _snapshot_summary(card)
    complete = summary.get("is_complete") is True
    weighted = summary.get("weights_available") is True
    if language == "en":
        if complete and weighted:
            return "The supplied snapshot establishes a complete structural baseline for the opening portfolio check."
        if weighted:
            return "The available fields establish a structural baseline; missing inputs remain explicit rather than inferred."
        return "The supplied positions establish a structural baseline; weight-based strengths remain unscored."
    if complete and weighted:
        return "這份持倉快照已建立完整的開場組合結構基線。"
    if weighted:
        return "現有欄位已建立組合結構基線；缺少的輸入維持明示，不用推測補齊。"
    return "已用使用者提供的持倉建立結構基線；依賴權重的優勢暫不評分。"


def _snapshot_hole_line(card, language):
    summary = _snapshot_summary(card)
    holes = card.get("top_holes") or []
    if summary.get("weights_available") is True and holes:
        hole = holes[0] if isinstance(holes[0], dict) else {}
        raw = hole.get("raw") or {}
        dim_id = dimension_id(raw.get("dim")) if raw.get("dim") else None
        # A position snapshot can support only structural dimensions. Never let
        # an accidentally carried history dimension become a snapshot claim.
        if dim_id in {"position_sizing", "diversification"}:
            line = _hole_line(hole, language)
            if line:
                return line
            label = localized_dimension(dim_id, language)
            return ((f"The leading structural risk in the available snapshot was {label}."
                     if language == "en" else f"現有快照的主要結構風險是「{label}」。"))
    if language == "en":
        return "This opening check establishes a structural baseline without treating unavailable weights as low risk."
    return "這次開場檢查只建立結構基線；無法取得的權重不會被當成低風險。"


def _trade_lines(card, language):
    best, worst = card.get("best_trade"), card.get("worst_trade")
    if not best or not worst:
        return []
    mixed = bool((card.get("currency_meta") or {}).get("mixed"))

    def amount(trade):
        currency = trade.get("currency")
        if not currency and not mixed:
            currency = _currency(card)
        return _money(trade.get("pnl"), str(currency).upper()) if currency else None

    best_amount, worst_amount = amount(best), amount(worst)
    if language == "en":
        return [
            (f"Best: {best['ticker']} {_pct(best.get('ret'))}, {best_amount} realized."
             if best_amount else f"Best: {best['ticker']} {_pct(best.get('ret'))}."),
            (f"Worst: {worst['ticker']} {_pct(worst.get('ret'))}, {worst_amount} realized."
             if worst_amount else f"Worst: {worst['ticker']} {_pct(worst.get('ret'))}."),
        ]
    return [
        (f"最賺：{best['ticker']} {_pct(best.get('ret'))}，已實現 {best_amount}。"
         if best_amount else f"最賺：{best['ticker']} {_pct(best.get('ret'))}。"),
        (f"最虧：{worst['ticker']} {_pct(worst.get('ret'))}，已實現 {worst_amount}。"
         if worst_amount else f"最虧：{worst['ticker']} {_pct(worst.get('ret'))}。"),
    ]


def _signed_pct(value, digits=1):
    return "—" if value is None else f"{float(value) * 100:+.{digits}f}%"


def _signed_pp(value, digits=1):
    return "—" if value is None else f"{float(value) * 100:+.{digits}f} pp"


def _period_line(bundle, copy):
    """Block 1's one-line period label (contract §2): the review span from
    engine state, plus at most two demoted market indicators (primary
    benchmark window return and VIX) — the former standalone market-timeline
    section collapses into this line and nothing else."""
    state = bundle.get("engine_state") or {}
    context = (((bundle.get("review_plan") or {}).get("state_snapshot") or {})
               .get("market_context") or {})
    period_copy = copy.get("period") or {}
    start = state.get("date_start") or context.get("start")
    end = state.get("date_end") or context.get("end")
    label = None
    try:
        if start and end and period_copy.get("span"):
            label = period_copy["span"].format(start=start, end=end)
        elif end and period_copy.get("as_of"):
            label = period_copy["as_of"].format(end=end)
    except (KeyError, IndexError, ValueError):
        label = None
    pieces = [] if label is None else [label]
    benchmarks = context.get("benchmarks") or {}
    spy = benchmarks.get("SPY") or {}
    if spy.get("window_ret") is not None and period_copy.get("spy"):
        try:
            pieces.append(period_copy["spy"].format(ret=_signed_pct(spy["window_ret"])))
        except (KeyError, IndexError, ValueError):
            pass
    vix = benchmarks.get("VIX") or {}
    if vix.get("last") is not None and period_copy.get("vix"):
        value = f"{float(vix['last']):.1f}"
        if vix.get("delta") is not None:
            value += f" ({float(vix['delta']):+.1f})"
        try:
            pieces.append(period_copy["vix"].format(value=value))
        except (KeyError, IndexError, ValueError):
            pass
    return " · ".join(pieces) if pieces else None


def _horizon_entries(bundle, copy):
    markers = ((((bundle.get("review_plan") or {}).get("state_snapshot") or {})
                .get("horizon_markers")) or [])
    labels = copy.get("horizons") or {}
    en = copy.get("language") == "en"
    entries = []
    for marker in markers:
        ticker = marker.get("ticker") or ("Position" if en else "這筆部位")
        horizon_label = labels.get(marker.get("horizon"), marker.get("horizon"))
        days = marker.get("holding_days")
        inferred = marker.get("maturity") == "inferred"
        if marker.get("kind") == "exit_too_fast":
            if en:
                voice = "inferred" if inferred else "recorded"
                entries.append((marker.get("ticker"),
                                f"{ticker}: the {voice} thesis horizon was {horizon_label}, but it ended after {days} days; "
                                "this is a timeline mismatch, not a verdict about the motive."))
            else:
                voice = "原先推測" if inferred else "已記錄"
                entries.append((marker.get("ticker"),
                                f"{ticker}：{voice}的 thesis 時間軸是「{horizon_label}」，{days} 天後就出場；"
                                "這是時間軸不一致，不替動機下定論。"))
        elif marker.get("kind") == "held_too_long":
            if en:
                voice = "inferred" if inferred else "recorded"
                entries.append((marker.get("ticker"),
                                f"{ticker}: the {voice} thesis horizon was {horizon_label}, but it is still open after {days} days; "
                                "the horizon has drifted and needs clarification."))
            else:
                voice = "原先推測" if inferred else "已記錄"
                entries.append((marker.get("ticker"),
                                f"{ticker}：{voice}的 thesis 時間軸是「{horizon_label}」，持有 {days} 天後仍未結束；"
                                "時間軸已漂移，仍需釐清。"))
    return entries


def _exit_followup_entries(bundle, copy):
    """Exit follow-up facts for Block 2: per-revisit lines as (ticker, line)
    pairs that attach to instrument rows, plus the portfolio-level backlog
    cluster as loose lines (no single row can host it)."""
    plan = bundle.get("review_plan") or {}
    price_as_of = (((bundle.get("engine_state") or {}).get("price_snapshot") or {}).get("as_of"))
    questions = {(row.get("revisit_id"), str(row.get("checkpoint"))): row
                 for row in plan.get("question_queue") or [] if row.get("kind") == "due_revisit"}
    due_labels = copy.get("due_choices") or {}
    en = copy.get("language") == "en"
    pairs = []
    lines = []
    for event in bundle.get("revisit_resolutions") or []:
        question = questions.get((event.get("revisit_id"), str(event.get("checkpoint"))))
        if not question:
            continue
        ticker = question.get("ticker") or ("position" if en else "這筆部位")
        label = due_labels.get(event.get("status"), event.get("status"))
        line = (f"{ticker}, {event.get('checkpoint')}-day check: {label}." if en else
                f"{ticker}，{event.get('checkpoint')} 天複核：{label}。")
        if event.get("note"):
            line += (f' Note: "{event["note"]}".' if en else f' 註記：「{event["note"]}」。')
        compare = question.get("compare") or {}
        needs = compare.get("needs_prices") or []
        if needs:
            missing = ", ".join(needs)
            when = f" as of {price_as_of}" if en and price_as_of else (f"（截至 {price_as_of}）" if price_as_of else "")
            line += (f" Current prices are missing for {missing}{when}, so no outcome comparison was made." if en else
                     f" {missing} 缺現價{when}，本期不判結果。")
        elif compare.get("swap_net_pp") is not None:
            swaps = ", ".join(sorted({row.get("ticker") for row in question.get("swaps") or []
                                      if row.get("ticker")})) or ("the replacement" if en else "換入標的")
            if en:
                lead = f" Using prices frozen on {price_as_of}," if price_as_of else ""
                line += (f"{lead} the original moved {_signed_pct(compare.get('orig_ret'))}; {swaps} moved "
                         f"{_signed_pct(compare.get('swap_ret'))}; swap net {_signed_pp(compare.get('swap_net_pp'))}.")
            else:
                lead = f" 以 {price_as_of} 凍結現價計，" if price_as_of else " "
                line += (f"{lead}原標的後續 {_signed_pct(compare.get('orig_ret'))}；{swaps}同期 "
                         f"{_signed_pct(compare.get('swap_ret'))}；swap 淨差 {_signed_pp(compare.get('swap_net_pp'))}。")
        elif compare.get("idle_cash") and compare.get("orig_ret") is not None:
            when = f" using prices frozen on {price_as_of}" if en and price_as_of else (f"（以 {price_as_of} 凍結現價計）" if price_as_of else "")
            line += (f" Proceeds stayed idle while the original moved {_signed_pct(compare.get('orig_ret'))}{when}." if en else
                     f" 賣後資金閒置，原標的同期 {_signed_pct(compare.get('orig_ret'))}{when}。")
        pairs.append((question.get("ticker"), line))
    backlog = (((plan.get("state_snapshot") or {}).get("exit_backlog")) or {})
    summary = backlog.get("summary") or {}
    if summary.get("count"):
        top = ", ".join(f"{ticker} ×{count}" for ticker, count in summary.get("top_tickers") or [])
        span = summary.get("span") or {}
        if en:
            line = (f"Historical exit backlog: {summary.get('count')} unresolved exits "
                    f"({summary.get('full')} full, {summary.get('reduce')} reductions)")
            if span.get("first") and span.get("last"):
                line += f" from {span['first']} to {span['last']}"
            if top:
                line += f"; most frequent: {top}"
            line += "."
        else:
            line = (f"歷史出場 backlog 尚有 {summary.get('count')} 筆未複核"
                    f"（清倉 {summary.get('full')}、減倉 {summary.get('reduce')}）")
            if span.get("first") and span.get("last"):
                line += f"，期間 {span['first']} 到 {span['last']}"
            if top:
                line += f"；最常出現：{top}"
            line += "。"
        if summary.get("priced"):
            if en:
                line += (f" Across {summary.get('priced')} price-covered exits, the average post-exit move was "
                         f"{_signed_pp(summary.get('avg_hindsight_pp'))}; "
                         f"{summary.get('sold_before_rise')} later rose.")
            else:
                line += (f" 有現價可回看的 {summary.get('priced')} 筆，出場後平均走勢為 "
                         f"{_signed_pp(summary.get('avg_hindsight_pp'))}；其中 "
                         f"{summary.get('sold_before_rise')} 筆後續上漲。")
        lines.append(line)
        for item in (backlog.get("items") or [])[:2]:
            ticker = item.get("ticker") or ("position" if en else "這筆部位")
            kind = item.get("kind")
            if en:
                action = "full exit" if kind == "full" else "reduction"
                detail = f"Backlog focus: {ticker}, {action} on {item.get('exit_date')}."
            else:
                action = "清倉" if kind == "full" else "減倉"
                detail = f"Backlog 優先回看：{ticker}，{item.get('exit_date')} {action}。"
            compare = item.get("compare") or {}
            needs = compare.get("needs_prices") or []
            if needs:
                missing = ", ".join(needs)
                when = f" as of {price_as_of}" if en and price_as_of else (f"（截至 {price_as_of}）" if price_as_of else "")
                detail += (f" No frozen-price comparison for {missing}{when}." if en else
                           f" {missing} 缺凍結現價{when}，不判結果。")
            elif compare.get("swap_net_pp") is not None:
                if en:
                    when = f" using prices frozen on {price_as_of}" if price_as_of else ""
                    detail += (f" The original moved {_signed_pct(compare.get('orig_ret'))}{when}; "
                               f"the replacement moved {_signed_pct(compare.get('swap_ret'))}; "
                               f"swap net {_signed_pp(compare.get('swap_net_pp'))}.")
                else:
                    when = f"（以 {price_as_of} 凍結現價計）" if price_as_of else ""
                    detail += (f" 原標的後續 {_signed_pct(compare.get('orig_ret'))}{when}；"
                               f"換入標的同期 {_signed_pct(compare.get('swap_ret'))}；"
                               f"swap 淨差 {_signed_pp(compare.get('swap_net_pp'))}。")
            elif compare.get("idle_cash") and compare.get("orig_ret") is not None:
                if en:
                    when = f" using prices frozen on {price_as_of}" if price_as_of else ""
                    detail += (f" Proceeds stayed idle while the original moved "
                               f"{_signed_pct(compare.get('orig_ret'))}{when}.")
                else:
                    when = f"（以 {price_as_of} 凍結現價計）" if price_as_of else ""
                    detail += f" 賣後資金閒置，原標的同期 {_signed_pct(compare.get('orig_ret'))}{when}。"
            elif compare.get("orig_ret") is not None:
                if en:
                    when = f" using prices frozen on {price_as_of}" if price_as_of else ""
                    detail += f" The original moved {_signed_pct(compare.get('orig_ret'))}{when}."
                else:
                    when = f"（以 {price_as_of} 凍結現價計）" if price_as_of else ""
                    detail += f" 原標的後續 {_signed_pct(compare.get('orig_ret'))}{when}。"
            lines.append(detail)
    return pairs, lines


def _problem_lines(bundle, copy):
    stats = ((((bundle.get("review_plan") or {}).get("state_snapshot") or {})
             .get("problem_stats")) or {})
    if not stats:
        return []
    en = copy.get("language") == "en"
    names = copy.get("problem_keys") or {}
    trends = copy.get("trends") or {}
    lines = []
    for key in (stats.get("top") or [])[:3]:
        row = (stats.get("per_key") or {}).get(key) or {}
        name = names.get(key, key.replace("_", " "))
        trend = trends.get(row.get("trend"), row.get("trend"))
        if en:
            lines.append(f"{name}: {row.get('recent_count', 0)} events in the recent window versus "
                         f"{row.get('prev_count', 0)} before ({trend}).")
        else:
            lines.append(f"{name}：近期 {row.get('recent_count', 0)} 次，前期 {row.get('prev_count', 0)} 次（{trend}）。")
    decisions = copy.get("rule_breach_decisions") or {}
    decided_rules = set()
    for event in bundle.get("rule_breach_decisions") or []:
        decided_rules.add(event.get("rule_id"))
        rule = event.get("rule_text") or event.get("rule_id")
        label = decisions.get(event.get("decision"), event.get("decision"))
        if en:
            line = f'Rule "{rule}": {label}.'
            if event.get("note"):
                line += f' Note: "{event["note"]}".'
        else:
            line = f"規矩「{rule}」：{label}。"
            if event.get("note"):
                line += f' 註記：「{event["note"]}」。'
        lines.append(line)
    for rule in stats.get("rules_check") or []:
        if (rule.get("rule_id") not in decided_rules and rule.get("verdict") == "held"
                and int(rule.get("held_streak") or 0) == 1):
            text = rule.get("text") or rule.get("rule_id")
            lines.append((f'Rule "{text}" was kept in the latest observable period.' if en else
                          f"規矩「{text}」在最近一個可觀測週期守住了。"))
    return lines


# ── Rich-layout facts (#247) ────────────────────────────────────────────
# Structured presentation facts consumed by BOTH surfaces: render_private
# writes them as text lines, render_html as the card-template.html layout
# (KPI grid, ranked instrument bars, stress row, attribution bars, improve
# rows).  The facts layer only formats engine-owned numbers through the same
# helpers the Markdown card uses — it never computes one.  A missing engine
# field drops its tile/row instead of inventing a value, so degraded cards
# (snapshot, insufficient, fx gaps) keep today's plainer shape.


def _kpi_tiles(card, context, copy):
    """Up to four headline tiles: P&L, payoff, benchmark excess, alpha."""
    overview = card.get("overview") or {}
    kpi_copy = copy.get("kpi") or {}
    tiles = []

    total = _finite_number(overview.get("total_pnl"))
    realized = _finite_number(overview.get("realized"))
    unrealized = _finite_number(overview.get("unrealized"))
    total_text = _display_money(total, context)
    if total is not None and total_text:
        sub = None
        realized_text = _display_money(realized, context)
        unrealized_text = _display_money(unrealized, context)
        if realized_text and unrealized_text and kpi_copy.get("pnl_sub"):
            sub = kpi_copy["pnl_sub"].format(realized=realized_text, unrealized=unrealized_text)
        tiles.append({"id": "pnl", "label": kpi_copy.get("pnl"), "value": total_text,
                      "tone": "neg" if total < 0 else "pos", "sub": sub, "spark": True})

    payoff = _finite_number(overview.get("payoff"))
    if payoff is not None and kpi_copy.get("payoff"):
        sub = None
        win_text = _display_money(_finite_number(overview.get("avg_win")), context)
        loss_text = _display_money(_finite_number(overview.get("avg_loss")), context, absolute=True)
        if win_text and loss_text and kpi_copy.get("payoff_sub"):
            sub = kpi_copy["payoff_sub"].format(win=win_text, loss=loss_text)
        tiles.append({"id": "payoff", "label": kpi_copy["payoff"],
                      "value": f"{payoff:.1f}", "tone": None, "sub": sub})

    ab = card.get("alpha_beta_breakdown") or {}
    # Mixed-market cards keep their per-market text rows (#205); a synthetic
    # top-level figure would recreate the total-alpha the engine refuses.
    # The excess and alpha tiles belong to the month-gated vs-market segment
    # (#284): on a gated review they disappear with the rest of ③.
    single_scope = not ab.get("by_market") and not vs_market_suppressed(card)
    excess = _finite_number(ab.get("excess_vs_spy")) if single_scope else None
    if excess is not None and kpi_copy.get("excess"):
        beta_text = _beta_text(ab.get("beta"))
        sub = kpi_copy["excess_sub"].format(beta=beta_text) if beta_text and kpi_copy.get("excess_sub") else None
        tiles.append({"id": "excess", "label": kpi_copy["excess"],
                      "value": f"{_benchmark_pp(excess)}pp",
                      "tone": "neg" if excess < 0 else "pos", "sub": sub})

    alpha = _finite_number(ab.get("alpha_ann")) if single_scope else None
    if alpha is not None and kpi_copy.get("alpha"):
        credible = bool(ab.get("credible"))
        value = _signed_pct(alpha, digits=0)
        tiles.append({"id": "alpha", "label": kpi_copy["alpha"],
                      "value": value if credible else f"{value} *",
                      "tone": None,
                      "sub": None if credible else "* " + (kpi_copy.get("alpha_unreliable") or "")})
    return tiles


def _instrument_rows(card, context, language):
    """Ranked per-instrument money impact for the template's bar list.

    Amounts are the engine's aggregate-view impacts converted like every other
    aggregate figure; bar widths are pure presentation geometry (share of the
    largest |impact|), the same class of scaling the sparkline already does.
    Behavior tags are stable engine codes resolved through copy (#279); legacy
    persisted zh literals stay on the zh card only."""
    diagnosis = card.get("ticker_diagnosis") or []
    if not context.get("currency") or len(diagnosis) < 2:
        return []
    rows = []
    peak = max((abs(_finite_number(row.get("impact")) or 0.0) for row in diagnosis), default=0.0)
    if peak <= 0:
        return []
    for row in diagnosis:
        impact = _finite_number(row.get("impact"))
        amount = _display_money(impact, context)
        ticker = str(row.get("ticker") or "").strip()
        if impact is None or not amount or not ticker:
            continue
        tags = [text for text in (localized_instrument_tag(tag, language)
                                  for tag in (row.get("tags") or [])) if text]
        rows.append({"ticker": ticker, "amount": amount,
                     "tone": "neg" if impact < 0 else "pos",
                     "tags": tags,
                     "width_pct": max(2, int(round(abs(impact) / peak * 100)))})
    return rows if len(rows) >= 2 else []


def _stress_lines(card, context, language):
    """The what-if concentration stress row (engine ``what_if``).

    The engine emits a locale-neutral scenario code (#279); the label and the
    sentence template come from copy ``stress_test``. Legacy bundles that
    persisted a zh ``label`` literal keep rendering it on the zh card only."""
    stress = card.get("what_if") or {}
    if not stress:
        return []
    exposure = _display_money(_finite_number(stress.get("mval")), context, absolute=True)
    drop30 = _display_money(_finite_number(stress.get("drop30")), context, absolute=True)
    drop50 = _display_money(_finite_number(stress.get("drop50")), context, absolute=True)
    label = localized_stress_label(stress, language)
    pct = _finite_number(stress.get("pct"))
    template = (load_copy(language).get("stress_test") or {}).get("line")
    if not (exposure and drop30 and drop50 and label and template) or pct is None:
        return []
    try:
        return [template.format(label=label, exposure=exposure, pct=_pct(pct),
                                drop30=drop30, drop50=drop50)]
    except (KeyError, IndexError, ValueError):
        return []


def _attribution_facts(card):
    """Benchmark-comparison rows for the attribution bars (private card only).

    Single-scope cards only: a mixed-market card keeps its per-market rows
    (#205) and never synthesizes one comparable series.  Row order follows the
    engine's benchmark map; widths scale to the largest |excess|."""
    if vs_market_suppressed(card):
        # #284: the comparator bars are part of the month-gated segment.
        return None
    ab = card.get("alpha_beta_breakdown") or {}
    if ab.get("by_market"):
        return None
    benchmarks = ab.get("benchmarks") or {}
    port = _finite_number(ab.get("port_tot"))
    bench_tot = _finite_number(ab.get("spy_tot"))
    headline = _finite_number(ab.get("excess_vs_spy"))
    primary = str(ab.get("bench") or "")
    rows = []
    for symbol, row in benchmarks.items():
        # The headline already states the primary-benchmark excess; the rows
        # exist for the alternative comparators (template: "vs QQQ / vs SOXX").
        if str(symbol) == primary:
            continue
        excess = _finite_number((row or {}).get("excess"))
        if excess is not None:
            rows.append({"label": str(symbol), "excess": excess,
                         "pp": f"{_benchmark_pp(excess)}pp"})
    if headline is None or not rows:
        return None
    peak = max(abs(row["excess"]) for row in rows)
    if peak <= 0:
        return None
    for row in rows:
        row["width_pct"] = max(2, int(round(abs(row["excess"]) / peak * 100)))
    return {"headline": f"{_benchmark_pp(headline)}pp",
            "tone": "neg" if headline < 0 else "pos",
            "port": _signed_pct(port, digits=0) if port is not None else None,
            "bench": _signed_pct(bench_tot, digits=0) if bench_tot is not None else None,
            "rows": rows}


def _improve_rows(card, language):
    """Prescription rows (amplify / outsource / cut). Coded rows resolve
    through copy (#279); legacy persisted zh rows stay on the zh card only."""
    rows = []
    for item in card.get("prescriptions") or []:
        resolved = localized_prescription(item, language)
        if resolved and resolved["kind"] and resolved["text"]:
            rows.append(resolved)
    return rows


def _card_facts(bundle, copy):
    """Assemble the rich-layout facts shared by both surfaces (#247)."""
    card = bundle.get("engine_card") or {}
    language = copy["language"]
    context = _display_context(card, language)
    if bundle.get("route") == "snapshot_review":
        # Snapshot cards intentionally suppress history-performance panels; the
        # rich layout has nothing honest to add there yet.
        return {"kpi": [], "instruments": [], "stress": [], "attribution": None, "improve": []}
    return {
        "kpi": _kpi_tiles(card, context, copy),
        "instruments": _instrument_rows(card, context, language),
        "stress": _stress_lines(card, context, language),
        "attribution": _attribution_facts(card),
        "improve": _improve_rows(card, language),
    }


def _performance_block(bundle, card, copy, facts, honesty, snapshot):
    """Block 1 (Performance): the ordered indicator items plus footnote texts.

    Contract §2/§3: period label on top, then ① absolute P&L → ② annualized →
    ③ vs market; a module whose prerequisite is missing renders one localized
    neutral line, never silent omission. The stress line rides the exposure
    indicator area unconditionally when its data exists (#265 intent: no
    unrelated hole ever absorbs it — final placement is Block 1). Hosted
    honesty sentences ride their numbers (§4); the leftovers become the
    footnote. Returns ``(items, footnote_texts)``."""
    language = copy["language"]
    if snapshot:
        # Snapshot route: position-structure baseline only (§3 last row); the
        # agent-authored limitation sentences have no indicator hosts here and
        # collapse into the footnote instead of a caveat wall.
        items = [{"kind": "line", "tag": None, "text": text}
                 for text in _snapshot_overview_lines(card, copy)]
        footnote = [honesty.pop(key) for key in list(honesty)]
        return items, footnote
    missing = copy.get("block_missing") or {}
    items = []
    period = _period_line(bundle, copy)
    if period:
        items.append({"kind": "line", "tag": "period", "text": period})
    perf = _performance_items(card, language)
    if not any(item.get("tag") == "pnl" for item in perf):
        perf.insert(0, {"kind": "line", "tag": None, "text": missing.get("absolute_pnl", "")})
    if not any(item.get("tag") in ("account_hold", "account", "account_gate") for item in perf):
        index = next((i for i, item in enumerate(perf)
                      if item.get("tag") in ("cash", "benchmark")), len(perf))
        perf.insert(index, {"kind": "line", "tag": None, "text": missing.get("annualized", "")})
    if (not any(item.get("tag") == "benchmark" for item in perf)
            and not vs_market_suppressed(card)):
        # §3: a month-gated review renders no gap note — the vs-market lines
        # are simply absent. The one-line note stays for genuinely missing
        # benchmark data on a review whose monthly slot is open.
        perf.append({"kind": "line", "tag": None, "text": missing.get("vs_market", "")})
    items.extend(perf)
    for text in facts["stress"]:
        items.append({"kind": "line", "tag": "stress", "text": text})
    items = _place_caveats(items, honesty)
    footnote = [honesty.pop(key) for key in list(honesty)]
    items = [item for item in items if item.get("text")]
    return items, footnote


def _trades_block(bundle, card, copy, facts, etf_lines, etf_honesty, snapshot):
    """Block 2 (Key trades): ranked instrument rows are the spine; motive
    answers, exit records, follow-ups, horizon mirrors, and best/worst
    realized trades attach as sub-lines under the row of the instrument they
    concern. Facts no row can host stay as block-level lines, so nothing is
    lost when the spine cannot render (§3: one neutral line instead)."""
    language = copy["language"]
    en = language == "en"
    missing = copy.get("block_missing") or {}
    row_tickers = {row["ticker"] for row in facts["instruments"]}
    subs = {ticker: [] for ticker in row_tickers}
    loose = []

    def push(ticker, text):
        if ticker and str(ticker) in subs:
            subs[str(ticker)].append(text)
        else:
            loose.append(text)

    if not snapshot:
        for ticker, text in _headline_motive_entries(bundle, copy):
            # A headline motive belongs under Key trades only when the
            # engine-owned context names a ticker that already has a rendered
            # instrument row.  Ungrounded/unmatched events are routed to Risks.
            if ticker and str(ticker) in row_tickers:
                push(ticker, text)
        for ticker, text in _decision_entries(bundle, copy):
            push(ticker, text)
        for ticker, text in _exit_entries(bundle, copy):
            push(ticker, text)
        followup_pairs, followup_loose = _exit_followup_entries(bundle, copy)
        for ticker, text in followup_pairs:
            push(ticker, text)
        loose.extend(followup_loose)
        for ticker, text in _horizon_entries(bundle, copy):
            push(ticker, text)
        trade_lines = _trade_lines(card, language)
        for trade, text in zip((card.get("best_trade"), card.get("worst_trade")), trade_lines):
            push((trade or {}).get("ticker"), text)

    blocks = []
    if facts["instruments"]:
        rows = [{**row, "subs": subs.get(row["ticker"], [])} for row in facts["instruments"]]
        blocks.append(("rows", rows))
    else:
        traded = [str(row.get("ticker")) for row in card.get("ticker_diagnosis") or []
                  if isinstance(row, dict) and row.get("ticker")]
        if not traded:
            traded = [trade.get("ticker") for trade in (card.get("best_trade"), card.get("worst_trade"))
                      if isinstance(trade, dict) and trade.get("ticker")]
        traded = list(dict.fromkeys(traded))
        note = None
        if traded and missing.get("trades_traded"):
            try:
                note = missing["trades_traded"].format(
                    tickers=(", " if en else "、").join(traded))
            except (KeyError, IndexError, ValueError):
                note = None
        if not note:
            note = missing.get("trades", "")
        if note:
            blocks.append(("paragraph", [note]))
    if loose:
        blocks.append(("bullets", loose))
    if etf_lines:
        blocks.append(("bullets", etf_lines))
        if etf_honesty:
            # §4: the etf_metadata sentence rides the ETF facts it qualifies.
            blocks.append(("caveat", [etf_honesty]))
    return blocks


def _risks_block(bundle, card, copy, narrative, snapshot, trade_tickers=None):
    """Block 3 (Risks and problems): the [v] strength / [X] hole pair as
    panels, with behavior patterns folded in below."""
    language = copy["language"]
    sections_copy = copy["sections"]
    missing = copy.get("block_missing") or {}
    holes = card.get("top_holes") or []
    trade_tickers = set(trade_tickers or [])
    motive_lines = [text for ticker, text in _headline_motive_entries(bundle, copy)
                    if not ticker or str(ticker) not in trade_tickers]
    blocks = []
    if not snapshot and not holes and not card.get("dims_raw") and not motive_lines:
        note = missing.get("risks", "")
        return [("paragraph", [note])] if note else []
    strength_label = (_copy_string(copy, "snapshot_strength", sections_copy["strength"])
                      if snapshot else sections_copy["strength"])
    strength_line = (_snapshot_strength_line(card, language) if snapshot else
                     narrative.get("strength") or _best_strength(card, language))
    blocks.append(("panel", {"style": "strength", "mark": "v", "label": strength_label,
                             "blocks": [("paragraph", [strength_line])]}))
    hole_label = (_copy_string(copy, "snapshot_hole", sections_copy["hole"])
                  if snapshot else sections_copy["hole"])
    hole_inner = []
    if snapshot:
        hole_inner.append(("paragraph", [_snapshot_hole_line(card, language)]))
    elif holes:
        hole_inner.append(("paragraph", [_hole_line(holes[0], language)]))
    if not snapshot and narrative.get("counterfactual"):
        hole_inner.append(("paragraph", [narrative["counterfactual"]]))
    if hole_inner:
        blocks.append(("panel", {"style": "hole", "mark": "X", "label": hole_label,
                                 "blocks": hole_inner}))
    problem_lines = [] if snapshot else _problem_lines(bundle, copy)
    if problem_lines:
        blocks.append(("bullets", problem_lines))
    if motive_lines:
        blocks.append(("bullets", motive_lines))
    return blocks


def _next_block(bundle, copy, facts, state, snapshot):
    """Block 4 (Next step): improve rows plus exactly one committed rule.

    §3: this block always lights — when the engine proposes no change it
    restates the standing rule, and a truly empty review says so in one
    neutral localized line instead of disappearing."""
    language = copy["language"]
    en = language == "en"
    sections_copy = copy["sections"]
    missing = copy.get("block_missing") or {}
    commitment = bundle.get("commitment") or {}
    blocks = []
    if facts["improve"]:
        blocks.append(("improve", facts["improve"]))
    rule_inner = []
    rule = commitment.get("rule")
    if rule:
        rule_inner.append(("paragraph", [rule]))
        # #248: engine-owned sub-line grounding the committed rule in this
        # period's actual positions; the canonical rule text stays generic.
        grounding = commitment.get("grounding")
        if isinstance(grounding, str) and grounding.strip():
            rule_inner.append(("grounding", [grounding]))
        rationale = (bundle.get("narrative") or {}).get("rule_rationale")
        if rationale:
            rule_inner.append(("paragraph", [rationale]))
    elif ((bundle.get("answers") or {}).get("commitment") or {}).get("choice") == "skip":
        rule_inner.append(("paragraph", [
            "你這次選擇不設新承諾；下次仍可用同一份基線對帳。" if not en
            else "You chose not to set a new commitment; the same baseline remains available next time."]))
    elif snapshot:
        rule_inner.append(("paragraph", [
            "這次開場檢查先保留結構基線，不強迫設定承諾。" if not en
            else "This opening check keeps the structural baseline without forcing a commitment."]))
    elif state.get("insufficient_data"):
        rule_inner.append(("paragraph", [
            "樣本仍短，這次不硬塞承諾；先把它當基線。" if not en
            else "The sample is still short, so this review sets a baseline without forcing a commitment."]))
    else:
        standing = state.get("rule")
        text = None
        if standing and missing.get("rule_standing"):
            try:
                text = missing["rule_standing"].format(rule=standing)
            except (KeyError, IndexError, ValueError):
                text = None
        if not text:
            text = missing.get("rule", "")
        if text:
            rule_inner.append(("paragraph", [text]))
    if rule_inner:
        blocks.append(("panel", {"style": "rule", "mark": "*", "label": sections_copy["rule"],
                                 "blocks": rule_inner}))
    return blocks


def _card_structure(bundle):
    """Assemble the private card's structured content once (#225).

    Both ``render_private`` (canonical Markdown) and ``render_html`` (styled
    HTML artifact) consume this single assembly, so the two surfaces cannot
    drift into different content-policy decisions. The section skeleton is the
    output contract's canonical shape (docs/output-contract.md §2): keynote
    preamble plus exactly four blocks — Performance, Key trades, Risks and
    problems, Next step — with block titles from ``copy.blocks``. Block
    content is ``(kind, payload)`` tuples: ``paragraph`` / ``bullets`` /
    ``grounding`` line lists, ``indicators`` (Block-1 items with caveat and
    attr-row kinds), ``footnote`` (collapsed leftovers), ``rows`` (instrument
    spine with attached sub-lines), ``panel`` (strength/hole/rule), and
    ``improve`` (prescription rows)."""
    language = bundle.get("language") or "zh-TW"
    copy = load_copy(language)
    narrative = validate_narrative(bundle.get("narrative") or {})
    card = bundle.get("engine_card") or {}
    state = bundle.get("engine_state") or {}
    sections_copy = copy["sections"]
    blocks_copy = copy.get("blocks") or {}
    snapshot = bundle.get("route") == "snapshot_review"
    facts = _card_facts(bundle, copy)

    badges = [copy["private_badge"]]
    if bundle.get("route") == "test_drive":
        badges.append(copy["demo_badge"])

    preamble = []
    opening = [] if snapshot else _review_opening_lines(bundle, copy["language"])
    if opening:
        preamble.append(("paragraph", opening))
    preamble.append(("paragraph", [narrative["mirror"]]))

    # #82: honesty sentences are woven next to the numbers they qualify (§4) —
    # never printed as a standalone checklist section. etf_metadata rides the
    # ETF facts in Block 2 when they render; every unhosted sentence collapses
    # into the Block-1 footnote so a triggered disclosure can never be dropped.
    honesty = _honesty_lines(bundle, copy)
    etf_lines = _etf_lines(card, copy["language"])
    etf_honesty = honesty.pop("etf_metadata", None) if etf_lines else None

    trades_blocks = _trades_block(bundle, card, copy, facts, etf_lines, etf_honesty, snapshot)
    performance_items, footnote = _performance_block(bundle, card, copy, facts, honesty, snapshot)
    performance_blocks = [("indicators", performance_items)]
    if footnote:
        performance_blocks.append(("footnote", footnote))

    performance_title = (_copy_string(copy, "snapshot_numbers", blocks_copy.get("performance", ""))
                         if snapshot else blocks_copy.get("performance", ""))
    sections = [
        {"id": "performance", "title": performance_title, "blocks": performance_blocks},
        {"id": "trades", "title": blocks_copy.get("trades", ""), "blocks": trades_blocks},
        {"id": "risks", "title": blocks_copy.get("risks", ""),
         "blocks": _risks_block(bundle, card, copy, narrative, snapshot,
                                 [row.get("ticker") for row in facts["instruments"]])},
        {"id": "next", "title": blocks_copy.get("next", ""),
         "blocks": _next_block(bundle, copy, facts, state, snapshot)},
    ]

    return {
        "session_id": bundle.get("session_id"),
        "route": bundle.get("route"),
        "language": copy["language"],
        "copy": copy,
        "headline": narrative["headline"],
        "badges": badges,
        "preamble": preamble,
        "sections": sections,
        "facts": facts,
    }


def _caveat_md(text, en):
    """One indented full-line parenthetical: the caveat shape S-3 recognizes."""
    return f"  ({text})" if en else f"  （{text}）"


def _panel_md(panel, en):
    """[mark] label: first line, remaining lines plain, grounding indented —
    the README text-card anchor shape ([v]/[X]/[*] lines)."""
    joiner = ": " if en else "："
    out = []
    first = True
    for kind, rows in panel["blocks"]:
        for row in rows:
            if not row:
                continue
            if first:
                out.append(f"[{panel['mark']}] {panel['label']}{joiner}{row}")
                first = False
            elif kind == "grounding":
                out.append(f"  └ {row}")
            else:
                out.append(row)
    return out or [f"[{panel['mark']}] {panel['label']}"]


def render_private(bundle):
    structure = _card_structure(bundle)
    copy = structure["copy"]
    en = structure["language"] == "en"
    lines = [
        "---",
        f"session_id: {structure['session_id']}",
        "privacy: private",
        f"language: {structure['language']}",
        "---",
        "",
        f"# {structure['headline']}",
        "",
    ]
    for badge in structure["badges"]:
        lines.extend([f"> {badge}", ""])
    for _kind, block in structure["preamble"]:
        lines.extend(list(block) + [""])
    for section in structure["sections"]:
        lines.extend([f"## {section['title']}", ""])
        for kind, block in section["blocks"]:
            if kind == "bullets":
                lines.extend([f"- {x}" for x in block] + [""])
            elif kind == "rows":
                wrap = ((lambda tags: " (" + "; ".join(tags) + ")") if en else
                        (lambda tags: "（" + "；".join(tags) + "）"))
                for row in block:
                    lines.append("- " + row["ticker"] + " " + row["amount"]
                                 + (wrap(row["tags"]) if row["tags"] else ""))
                    lines.extend(f"  - {sub}" for sub in row.get("subs") or [])
                lines.append("")
            elif kind == "indicators":
                for item in block:
                    if item["kind"] == "caveat":
                        lines.append(_caveat_md(item["text"], en))
                    else:
                        lines.append(item["text"])
                lines.append("")
            elif kind == "footnote":
                label = copy.get("footnote_label", "")
                joined = (" " if en else "").join(block)
                lines.extend([f"{label}{': ' if en else '：'}{joined}", ""])
            elif kind == "caveat":
                # Rides the block right above it (e.g. the ETF facts): no
                # blank line in between, so the sentence stays attached.
                if lines and lines[-1] == "":
                    lines.pop()
                lines.extend([_caveat_md(x, en) for x in block if x] + [""])
            elif kind == "panel":
                lines.extend(_panel_md(block, en) + [""])
            elif kind == "improve":
                joiner = ": " if en else "："
                lines.extend([f"- {row['kind']}{joiner}{row['text']}" for row in block] + [""])
            else:
                lines.extend(list(block) + [""])
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


def _public_performance_lines(card, language):
    """Share only allowlisted market labels and engine-owned relative scalars."""
    lines = []
    for market, _bench, row in _benchmark_rows(card):
        excess = _finite_number(row.get("excess_vs_spy"))
        beta = _beta_text(row.get("beta"))
        if excess is None or beta is None:
            continue
        if language == "en":
            subject = market or "Portfolio"
            lines.append(f"{subject}: {_benchmark_pp(excess)} pp versus its market benchmark; β {beta}.")
        else:
            subject = market or "可比較部位"
            lines.append(f"{subject}：相對各自市場大盤 {_benchmark_pp(excess)} 個百分點；β {beta}。")
    return lines


def render_public(bundle):
    """Render a conservative shareable card without user-authored free text."""
    language = bundle.get("language") or "zh-TW"
    copy = load_copy(language)
    card = bundle.get("engine_card") or {}
    snapshot = bundle.get("route") == "snapshot_review"
    snapshot_summary = _snapshot_summary(card)
    holes = card.get("top_holes") or []
    hole = holes[0] if holes else {}
    raw = hole.get("raw") or {}
    dim_id = dimension_id(raw.get("dim")) if raw.get("dim") else None
    dim_label = (copy.get("dimensions") or {}).get(dim_id) if dim_id else None
    pattern = ((copy.get("public_patterns") or {}).get(dim_id)
               if dim_id and not snapshot else None)
    severity_value = _finite_number(hole.get("severity"))
    severity = (None if snapshot and severity_value is None
                else _public_band(hole.get("severity"), copy["language"]))
    commitment = bundle.get("commitment") or {}
    # Candidate rules resolve to fixed copy strings; custom rules (and anything of
    # unknown origin) render as a generic localized line so user-authored text —
    # which may carry tickers, amounts, or dates — never reaches the public card.
    rule = None
    if commitment:
        if commitment.get("origin") == "candidate":
            rule = localized_rule(commitment.get("dim"), language)
        if not rule:
            rule = copy.get("public_custom_rule")
    structural_hole = (snapshot and snapshot_summary.get("weights_available") is True
                       and bool(holes) and dim_id in {"position_sizing", "diversification"})
    if copy["language"] == "en":
        if structural_hole:
            mirror = f"This opening portfolio check identified {dim_label or 'portfolio structure'} as the leading structural risk"
            mirror += f", with {severity} pressure." if severity else "."
        elif snapshot:
            mirror = ("This opening portfolio check establishes a structural baseline; "
                      "transaction-history behavior remains unscored.")
        elif not holes:
            mirror = "This review did not rank a leading behavior pattern from the available history."
        else:
            mirror = f"This review found {severity} behavioral pressure in {dim_label or 'the leading diagnostic dimension'}."
        structure = "Diversified allocation ETFs were separated from single-name risk; focused ETFs remained concentration risk."
    else:
        if structural_hole:
            mirror = f"這次開場組合檢查把「{dim_label or '組合結構'}」列為主要結構風險"
            mirror += f"，風險壓力為{severity}。" if severity else "。"
        elif snapshot:
            mirror = "這次開場組合檢查只建立結構基線；交易歷史行為維度維持未評分。"
        elif not holes:
            mirror = "這次可用歷史不足以排序出主要行為模式。"
        else:
            mirror = f"這次復盤在「{dim_label or '主要行為維度'}」看見{severity}程度的行為壓力。"
        structure = "配置型 ETF 與單一標的風險分開計算；產業、主題與槓桿 ETF 仍保留集中風險。"
    if pattern:
        mirror += " " + pattern
    lines = [
        "---", "privacy: public", f"language: {copy['language']}", "---", "",
        f"# {copy['title']}", "", f"> {copy['public_badge']}", "", mirror, "",
    ]
    if bundle.get("route") == "test_drive":
        lines[9:9] = [f"> {copy['demo_badge']}", ""]
    performance = [] if snapshot else _public_performance_lines(card, copy["language"])
    if performance:
        lines.extend([f"## {copy['sections']['performance']}", ""] + [f"- {x}" for x in performance] + [""])
    ps = card.get("portfolio_structure") or {}
    if ps.get("allocation_etfs") or ps.get("concentrated_etfs"):
        lines.extend([f"## {copy['sections']['etf']}", "", structure, ""])
    if rule:
        lines.extend([f"## {copy['sections']['rule']}", "", rule, ""])
    return "\n".join(lines).rstrip() + "\n"


# ── Styled HTML card (#225) ──────────────────────────────────────────────────
# Design provenance: card-template.html (2026-07-04 UI review). Runtime truth
# lives here; design-rule changes must land in both files. Constraints: flat,
# light/dark via prefers-color-scheme, system font stack, one <=20px heading,
# outlined tags, neutral surfaces, semantic color only on section labels and
# P&L accents, font weights 400/500, no emoji, no icon font, and zero external
# requests (no http(s) URLs anywhere in the document).

# Document-level shim: lets the artifact open directly in a browser. The widget
# fragment below is self-contained and does not depend on this shim.
_HTML_SHIM_CSS = """\
body{margin:0;background:#eceae1;color:#1a1915;padding:28px 16px;display:flex;justify-content:center;
font-family:system-ui,-apple-system,"Segoe UI","Noto Sans TC",sans-serif}
@media (prefers-color-scheme:dark){body{background:#1a1917;color:#f5f4ef}}
.page{width:680px;max-width:100%}"""

# Widget-fragment styles. Host theme variables (--surface-*, --text-*, --border,
# --radius) win when present; the var() fallbacks keep the fragment readable in
# hosts without them, with a prefers-color-scheme dark set of fallbacks.
_HTML_WIDGET_CSS = """\
.rc{--rc-surface-2:var(--surface-2,#ffffff);--rc-surface-1:var(--surface-1,#f5f4ef);
--rc-text-primary:var(--text-primary,#1a1915);--rc-text-secondary:var(--text-secondary,#5f5e5a);
--rc-text-muted:var(--text-muted,#8a8980);--rc-text-success:var(--text-success,#3b6d11);
--rc-text-danger:var(--text-danger,#a32d2d);--rc-text-accent:var(--text-accent,#185fa5);
--rc-border:var(--border,rgba(0,0,0,0.10));--rc-radius:var(--radius,8px)}
@media (prefers-color-scheme:dark){.rc{--rc-surface-2:var(--surface-2,#2b2a27);
--rc-surface-1:var(--surface-1,#232220);--rc-text-primary:var(--text-primary,#f5f4ef);
--rc-text-secondary:var(--text-secondary,#b4b2a9);--rc-text-muted:var(--text-muted,#8a8980);
--rc-text-success:var(--text-success,#a7be83);--rc-text-danger:var(--text-danger,#df8b84);
--rc-text-accent:var(--text-accent,#a9b5c2);--rc-border:var(--border,rgba(255,250,240,0.10))}}
.rc{font-family:system-ui,-apple-system,"Segoe UI","Noto Sans TC",sans-serif;font-weight:400;
color:var(--rc-text-primary);background:var(--rc-surface-2);border:0.5px solid var(--rc-border);
border-radius:12px;overflow:hidden;line-height:1.6}
.rc .sec{padding:18px 22px}
.rc .sec+.sec{border-top:0.5px solid var(--rc-border)}
.rc .eyebrow{font-size:12px;color:var(--rc-text-muted);margin:0 0 6px}
.rc h1{font-size:20px;font-weight:500;margin:0;line-height:1.35}
.rc .tags{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 0}
.rc .tag{display:inline-flex;align-items:center;font-size:12px;padding:1px 8px;border-radius:6px;
line-height:1.5;background:transparent;border:0.5px solid var(--rc-border);color:var(--rc-text-secondary)}
.rc .lead{font-size:14px;color:var(--rc-text-secondary);line-height:1.7;margin:12px 0 0}
.rc h2{font-size:15px;font-weight:500;margin:0 0 10px;color:var(--rc-text-primary)}
.rc p{font-size:14px;color:var(--rc-text-secondary);line-height:1.7;margin:0}
.rc p+p,.rc ul+p,.rc p+ul{margin-top:8px}
.rc ul{margin:0;padding-left:20px}
.rc li{font-size:14px;color:var(--rc-text-secondary);line-height:1.7;margin:0 0 8px}
.rc li:last-child{margin-bottom:0}
.rc .spark{display:block;width:100%;height:34px;margin:12px 0 0}
.rc .spark path{fill:none;stroke:var(--rc-text-muted);stroke-width:1.5;stroke-linecap:round;
stroke-linejoin:round;opacity:.85}
.rc .spark.pos path{stroke:var(--rc-text-success)}
.rc .spark.neg path{stroke:var(--rc-text-danger)}
.rc .pos{color:var(--rc-text-success)}
.rc .neg{color:var(--rc-text-danger)}
.rc .grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 4px}
@media (max-width:520px){.rc .grid4{grid-template-columns:repeat(2,1fr)}}
@media (max-width:380px){.rc .grid4{grid-template-columns:1fr}}
.rc .m{background:var(--rc-surface-1);border-radius:var(--rc-radius);padding:13px 15px}
.rc .m .lbl{font-size:12px;color:var(--rc-text-secondary);margin:0}
.rc .m .val{font-size:19px;font-weight:500;margin:5px 0 0;line-height:1.25;color:var(--rc-text-primary)}
.rc .m .val.pos{color:var(--rc-text-success)}
.rc .m .val.neg{color:var(--rc-text-danger)}
.rc .m .sub{font-size:11px;color:var(--rc-text-muted);margin:4px 0 0;line-height:1.4}
.rc .m .spark{height:22px;margin:8px 0 0}
.rc .trow{margin:0 0 11px}
.rc .trow:last-of-type{margin-bottom:0}
.rc .ttop{display:flex;align-items:baseline;gap:10px}
.rc .tk{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:14px;font-weight:500;min-width:52px}
.rc .tamt{font-size:14px;font-weight:500;min-width:78px;text-align:right}
.rc .ttags{display:flex;flex-wrap:wrap;gap:6px;flex:1}
.rc .track{height:4px;border-radius:99px;background:var(--rc-surface-1);margin:6px 0 0;overflow:hidden}
.rc .fill{height:100%;border-radius:99px;background:var(--rc-text-muted);opacity:.7}
.rc .fill.neg{background:var(--rc-text-danger);opacity:.85}
.rc .cap{font-size:12px;color:var(--rc-text-muted);margin:12px 0 0;line-height:1.6}
.rc .attr-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;margin:0 0 14px}
.rc .attr-head .big{font-size:19px;font-weight:500}
.rc .arow{display:grid;grid-template-columns:1fr 70px;gap:12px;align-items:center;margin:8px 0}
.rc .arow .al{font-size:13px;color:var(--rc-text-secondary)}
.rc .arow .av{font-size:14px;font-weight:500;text-align:right;font-family:ui-monospace,"SF Mono",Menlo,monospace}
.rc .abar{height:4px;border-radius:99px;background:var(--rc-surface-1);margin:5px 0 0;overflow:hidden}
.rc .abar div{height:100%;border-radius:99px;background:var(--rc-text-muted);opacity:.7}
.rc .rx{display:flex;flex-direction:column;gap:2px;padding:11px 0;border-top:0.5px solid var(--rc-border)}
.rc .rx:first-of-type{border-top:none;padding-top:2px}
.rc .rx .kind{font-size:13px;font-weight:500;margin:0;color:var(--rc-text-primary)}
.rc .rx .desc{font-size:13px;color:var(--rc-text-secondary);line-height:1.55;margin:0}
.rc .panel{background:var(--rc-surface-1);border:0.5px solid var(--rc-border);
border-radius:var(--rc-radius);padding:16px 18px}
.rc .panel+.panel{margin-top:10px}
.rc .panel-label{font-size:12px;font-weight:500;margin:0 0 8px}
.rc .strength .panel-label{color:var(--rc-text-success)}
.rc .hole .panel-label{color:var(--rc-text-danger)}
.rc .rule .panel-label{color:var(--rc-text-accent)}
.rc .rule .rmain{font-size:15px;color:var(--rc-text-primary);line-height:1.65;font-weight:500}
.rc .rule .rground{font-size:13px;color:var(--rc-text-muted);line-height:1.6}
.rc .cavt{font-size:12px;color:var(--rc-text-muted);line-height:1.6;padding-left:14px}
.rc p+.cavt{margin-top:2px}
.rc .rsub{font-size:12px;color:var(--rc-text-muted);line-height:1.55;margin:2px 0 8px;padding-left:14px}
.rc .fnote{margin:12px 0 0}
.rc .fnote summary{font-size:12px;color:var(--rc-text-muted);cursor:pointer}
.rc .fnote p{font-size:12px;color:var(--rc-text-muted);margin:6px 0 0}
.rc .foot{font-size:11px;color:var(--rc-text-muted);line-height:1.6;background:var(--rc-surface-1)}"""


def _sparkline_svg(card):
    """Inline-SVG cumulative P&L sparkline from engine ``pnl_curve.points``.

    Renders only when at least two finite points exist.  Note-form or missing
    curve data omits the sparkline silently: card-spec forbids inventing a new
    user-facing caveat for it.  One thin line, colored only by the final sign,
    per the card-template design reference.  No external references, so the
    artifact stays request-free."""
    # Decorative field, fail-soft contract: any wrong-typed curve (adapter or
    # --card-json input) must omit the sparkline, never abort the render.
    curve = (card or {}).get("pnl_curve")
    if not isinstance(curve, dict) or curve.get("note"):
        return None
    points = curve.get("points")
    if not isinstance(points, list):
        return None
    values = []
    for point in points:
        if not isinstance(point, dict):
            continue
        number = _finite_number(point.get("cum_ret"))
        if number is not None:
            values.append(number)
    if len(values) < 2:
        return None
    width, height, pad = 120.0, 28.0, 2.0
    low, high = min(values), max(values)
    spread = high - low
    coords = []
    for index, value in enumerate(values):
        x = index * width / (len(values) - 1)
        y = (height / 2.0 if spread <= 0
             else pad + (high - value) * (height - 2 * pad) / spread)
        coords.append(f"{x:.1f},{y:.1f}")
    tone = "neg" if math.copysign(1.0, values[-1]) < 0 else "pos"  # -0.0 counts as a loss
    path = "M" + " L".join(coords)
    return (f'<svg class="spark {tone}" viewBox="0 0 {width:.0f} {height:.0f}" '
            f'preserveAspectRatio="none" aria-hidden="true"><path d="{path}"/></svg>')


def _html_block(kind, rows, lead_class=None):
    """Render one structure block; ``lead_class`` styles the first paragraph."""
    rows = [row for row in rows if row]
    if not rows:
        return ""
    if kind == "bullets":
        return "<ul>" + "".join(f"<li>{html.escape(row)}</li>" for row in rows) + "</ul>"
    parts = []
    for index, row in enumerate(rows):
        attr = f' class="{lead_class}"' if index == 0 and lead_class else ""
        parts.append(f"<p{attr}>{html.escape(row)}</p>")
    return "".join(parts)


def render_html(bundle):
    """Self-contained styled HTML artifact for the private card (#225).

    Consumes the same ``_card_structure`` assembly as ``render_private``, so
    the HTML card can never show facts the canonical Markdown card does not.
    The body between the WIDGET-FRAGMENT markers is a host-independent
    ``<style>`` + ``<div class="rc">`` pair that graphical surfaces can lift
    directly; delivery rules live in ``references/card-delivery.md``."""
    structure = _card_structure(bundle)
    copy = structure["copy"]
    e = html.escape
    version_id = (bundle.get("engine_version") or {}).get("id") or "unknown"

    header = [f'<p class="eyebrow">{e(copy["title"])}</p>',
              f"<h1>{e(structure['headline'])}</h1>",
              '<div class="tags">'
              + "".join(f'<span class="tag">{e(badge)}</span>' for badge in structure["badges"])
              + "</div>"]
    for _kind, block in structure["preamble"]:
        for row in block:
            if row:
                header.append(f'<p class="lead">{e(row)}</p>')
    body = ['<div class="sec">' + "".join(header) + "</div>"]

    # Snapshot cards have no performance panel, and their engine card carries no
    # pnl_curve; the route guard keeps that existing conditional explicit.
    spark = (None if structure["route"] == "snapshot_review"
             else _sparkline_svg(bundle.get("engine_card") or {}))
    facts = structure["facts"]

    def kpi_grid():
        tiles = []
        for tile in facts["kpi"]:
            tone = f' {tile["tone"]}' if tile.get("tone") else ""
            parts = []
            if tile.get("label"):
                parts.append(f'<p class="lbl">{e(tile["label"])}</p>')
            parts.append(f'<p class="val{tone}">{e(tile["value"])}</p>')
            if tile.get("sub"):
                parts.append(f'<p class="sub">{e(tile["sub"])}</p>')
            if tile.get("spark") and spark:
                parts.append(spark)
            tiles.append('<div class="m">' + "".join(parts) + "</div>")
        return '<div class="grid4">' + "".join(tiles) + "</div>" if tiles else ""

    def instrument_bars(rows):
        parts = []
        for row in rows:
            tone = f' {row["tone"]}' if row.get("tone") else ""
            fill = ' neg' if row.get("tone") == "neg" else ""
            tags = "".join(f'<span class="tag">{e(tag)}</span>' for tag in row["tags"])
            parts.append(
                '<div class="trow"><div class="ttop">'
                f'<span class="tk">{e(row["ticker"])}</span>'
                f'<span class="tamt{tone}">{e(row["amount"])}</span>'
                + (f'<div class="ttags">{tags}</div>' if tags else "")
                + f'</div><div class="track"><div class="fill{fill}" '
                  f'style="width:{row["width_pct"]}%"></div></div>'
                + "".join(f'<p class="rsub">{e(sub)}</p>' for sub in row.get("subs") or [])
                + "</div>")
        return parts

    def attribution_bars():
        attribution = facts["attribution"]
        tone = f' {attribution["tone"]}' if attribution.get("tone") else ""
        parts = [f'<p class="attr-head"><span class="big{tone}">{e(attribution["headline"])}</span></p>']
        for row in attribution["rows"]:
            parts.append(f'<div class="arow"><span class="al">vs {e(row["label"])}</span>'
                         f'<span class="av">{e(row["pp"])}</span></div>'
                         f'<div class="abar"><div style="width:{row["width_pct"]}%"></div></div>')
        return parts

    def improve_rows(rows):
        return ['<div class="rx">'
                f'<p class="kind">{e(row["kind"])}</p><p class="desc">{e(row["text"])}</p></div>'
                for row in rows]

    def indicator_items(items):
        parts = []
        for item in items:
            if item["kind"] == "caveat":
                parts.append(f'<p class="cavt">{e(item["text"])}</p>')
            elif item["kind"] == "attr_rows":
                # The attribution bars carry these comparator rows on HTML.
                continue
            elif item.get("text"):
                parts.append(f"<p>{e(item['text'])}</p>")
        return parts

    def panel_html(panel):
        inner = []
        first = True
        for kind, rows in panel["blocks"]:
            for row in rows:
                if not row:
                    continue
                lead_class = ("rmain" if panel["style"] == "rule" and first else
                              "rground" if kind == "grounding" else None)
                attr = f' class="{lead_class}"' if lead_class else ""
                inner.append(f"<p{attr}>{e(row)}</p>")
                first = False
        return (f'<div class="panel {panel["style"]}">'
                f'<p class="panel-label">{e(panel["label"])}</p>'
                + "".join(inner) + "</div>")

    for section in structure["sections"]:
        sid = section["id"]
        rendered = []
        for kind, block in section["blocks"]:
            if kind == "indicators":
                rendered.extend(indicator_items(block))
            elif kind == "footnote":
                label = copy.get("footnote_label", "")
                inner = "".join(f"<p>{e(text)}</p>" for text in block)
                rendered.append(f'<details class="fnote"><summary>{e(label)}</summary>'
                                f"{inner}</details>")
            elif kind == "rows":
                rendered.extend(instrument_bars(block))
            elif kind == "panel":
                rendered.append(panel_html(block))
            elif kind == "improve":
                rendered.extend(improve_rows(block))
            elif kind == "caveat":
                rendered.extend(f'<p class="cavt">{e(text)}</p>' for text in block if text)
            else:
                chunk = _html_block(kind, block)
                if chunk:
                    rendered.append(chunk)
        if sid == "performance":
            # The attribution bars merge into Block 1 (contract §2), placed
            # after the vs-market sentences and before the footnote.
            if facts["attribution"]:
                insert_at = next((index for index, chunk in enumerate(rendered)
                                  if chunk.startswith('<details class="fnote"')),
                                 len(rendered))
                rendered[insert_at:insert_at] = attribution_bars()
            grid = kpi_grid()
            if grid:
                # The tiles restate the opening indicator lines as the
                # template's KPI row; the lines stay below as the story block.
                rendered.insert(0, grid)
                if spark and not any(t.get("spark") for t in facts["kpi"]):
                    rendered.insert(1, spark)
            elif spark:
                rendered.insert(1 if rendered else 0, spark)
        body.append(f'<div class="sec"><h2>{e(section["title"])}</h2>'
                    + "".join(rendered) + "</div>")
    body.append('<div class="sec foot">'
                f"session_id: {e(str(structure['session_id']))} · "
                f"language: {e(structure['language'])}</div>")

    fragment = ("<!-- WIDGET-FRAGMENT-START -->\n"
                f"<style>\n{_HTML_WIDGET_CSS}\n</style>\n"
                '<div class="rc">\n' + "\n".join(body) + "\n</div>\n"
                "<!-- WIDGET-FRAGMENT-END -->")
    return ("<!doctype html>\n"
            f'<html lang="{e(structure["language"])}"><head>\n'
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f'<meta name="engine-version" content="{e(version_id)}">\n'
            f"<title>{e(copy['title'])}</title>\n"
            f"<style>\n{_HTML_SHIM_CSS}\n</style>\n"
            "</head>\n<body>\n"
            '<div class="page">\n'
            f"{fragment}\n"
            "</div>\n</body>\n</html>\n")
