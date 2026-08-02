#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic private/public card renderer.

The agent supplies prose-only interpretation in ``narrative``.  All displayed
numbers are selected from engine output here; narrative fields containing digits
are rejected to keep the engine's numeric authority enforceable in code.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import re


class RenderError(ValueError):
    pass


HERE = os.path.dirname(os.path.abspath(__file__))
COPY_DIR = os.path.join(os.path.dirname(HERE), "copy")
ALLOWED_NARRATIVE = {"headline", "mirror", "counterfactual", "rule_rationale", "strength", "honesty",
                     "synthesis"}
DIMENSION_ID_BY_LEGACY_LABEL = {
    "出場紀律": "exit_discipline",
    "部位 sizing": "position_sizing",
    "分散": "diversification",
    "持有時間": "holding_period",
    "加碼攤平": "averaging_down",
    "alpha/beta": "alpha_beta",
    "進場": "entry_style",
}
# Sector labels arrive from `trade_recap.SECTOR_MAP` as zh literals, the same
# shape as the dimension labels above and mapped the same way (#387). A label
# with no entry here is a user-supplied driver-map category, which passes
# through verbatim — the engine cannot localize a name the user invented.
SECTOR_ID_BY_LEGACY_LABEL = {
    "債券": "bonds",
    "加密": "crypto",
    "區域ETF": "regional_etf",
    "半導體": "semiconductors",
    "商品": "commodities",
    "大盤ETF": "broad_market_etf",
    "未分類": "unclassified",
    "消費": "consumer",
    "無人機國防": "drones_defense",
    "稀土材料": "rare_earth_materials",
    "資料中心電力": "datacenter_power",
    "軟體雲": "software_cloud",
    "金融科技": "fintech",
    "電信": "telecom",
    "電動車AI": "ev_ai",
}
MARKET_BENCHMARKS = {"TW": "^TWII", "US": "SPY"}
DISPLAY_CURRENCY_BY_LANGUAGE = {"en": "USD", "zh-TW": "TWD", "zh-CN": "CNY"}


DEFAULT_LANGUAGE = "en"


def supported_languages():
    """Locales that have a copy file. Dropping a new copy/<locale>.json here is
    the complete registration step (output-language contract §2); nothing else
    hardcodes the locale list."""
    return {os.path.splitext(name)[0] for name in os.listdir(COPY_DIR)
            if name.endswith(".json")}


def resolve_language(language):
    """Map a requested language tag to a supported copy locale.

    Owner ruling 2026-07-24 (#389): an unknown or unsupported tag falls back to
    English, never to zh-TW. Matching is exact up to case (``zh-tw`` → ``zh-TW``);
    there is deliberately no base-tag negotiation — whether zh variants should
    exception-map to zh-TW is an open product question on #389, and until it is
    ruled they follow the strict fallback. Idempotent for canonical values.
    """
    requested = str(language or "").strip().lower()
    for candidate in sorted(supported_languages()):
        if requested == candidate.lower():
            return candidate
    return DEFAULT_LANGUAGE


def load_copy(language):
    with open(os.path.join(COPY_DIR, resolve_language(language) + ".json"),
              encoding="utf-8") as f:
        return json.load(f)


def _format_copy(template, **values):
    """Fill a copy template, or return ``None`` if it is missing or malformed.

    The #368 migration routes prose through ``copy/*.json`` instead of inline
    language branches, which makes "the key is absent" a reachable state during
    a partial migration. Returning ``None`` keeps that a dropped line rather
    than a crashed render; key parity across locales is gated mechanically by
    ``tests/test_card_html.py::test_locale_copy_files_keep_key_parity``, so an
    absent key fails the suite rather than silently shipping."""
    if not template:
        return None
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return None


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

# CJK numerals that can head a spelled-out quantity. Simplified variants
# (两/万/亿) sit alongside Traditional so a zh-CN narrative cannot smuggle
# "五万" through a table tuned for "五萬" (#387).
_CJK_NUMERALS = "零〇一二兩两三四五六七八九十百千萬万億亿兆"
# "Hard" units are almost never word-heads, so a numeral in front is always a
# quantity claim (percent / colloquial percent / multiple).
_CJK_HARD_UNITS = "％%趴倍"
# "Soft" units also head common words (成本, 個人, 天氣…), so a numeral+unit only
# counts when the unit sits at a word boundary (not glued to another non-numeral
# Han letter that would form a compound word). Simplified variants included
# (块张档个点周 for 塊張檔個點週).
_CJK_SOFT_UNITS = "成元塊块股張张檔档天日週周月年季次個个點点"

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
    # Simplified counterparts of the idioms above whose characters differ
    # (shared-form idioms like 一起/十分/第一 need no duplicate).
    "一时", "一连", "一举", "一样", "一体", "一线", "一员", "一环",
    "统一", "专一", "进一步",
    "三三两两", "两两", "三两", "三天两头",
    "千万别", "千万不", "千万勿", "千万要", "千万记", "千万得", "千万莫", "千万请",
    "万一", "万分", "万万", "万全", "万难", "万象", "万能", "万无",
    "两难", "两者", "两极", "两全", "两可", "两相", "两败", "两性", "两岸", "两用",
)

# Consecutive CJK numerals (三十, 一百, 五萬, 二〇二六) read as an actual number.
_CJK_COMPOUND_RE = re.compile(f"[{_CJK_NUMERALS}]{{2,}}")
# Numeral + hard unit (兩倍, 五趴, 三％).
_CJK_HARD_RE = re.compile(f"[{_CJK_NUMERALS}][{_CJK_HARD_UNITS}]")
# Numeral + soft unit (三成, 五股, 十張); boundary is checked in code.
_CJK_SOFT_RE = re.compile(f"[{_CJK_NUMERALS}][{_CJK_SOFT_UNITS}]")
# Percentage spelled as 百分之X (百分之三十, 百分之五); 百分之百 is an idiom, stripped first.
_CJK_PCT_RE = re.compile(f"百分之[{_CJK_NUMERALS}]")
# Approximate quantifiers (幾十, 數百, 幾成, 幾倍; simplified 几十/数百) — 幾/數/几/数
# only count before a magnitude/unit, so 數字/幾乎/多數/数字/几乎 stay clean.
_CJK_APPROX_RE = re.compile(f"[幾數几数](?=[十百千萬万億亿{_CJK_HARD_UNITS}成])")

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


def localized_sector(sector, language):
    """Resolve an engine sector label to display text (#387).

    Unmapped labels are user-supplied driver-map categories and pass through
    unchanged; there is nothing to translate them to."""
    if not sector:
        return ""
    sector_id = SECTOR_ID_BY_LEGACY_LABEL.get(sector)
    if not sector_id:
        return sector
    return (load_copy(language).get("sectors") or {}).get(sector_id, sector)


def localized_rule(dim, language, cap=None):
    """The canonical rule text for a dimension.

    #317 (from #326): templates may carry ``{cap}`` so the user reads the
    threshold in the rule itself instead of having to remember it. The cap is a
    standing value, not a per-period fact, so the text stays stable across weeks
    and rules.jsonl tracking (which keys on rule_id) is unaffected.

    #324: ``cap`` is the user's standing single-position override when set;
    otherwise the interpolation falls back to the universal ``POSITION_CAP``.
    Callers pass the raw ``state.max_position_pct`` (or ``None``) and this
    resolves the effective cap fail-closed."""
    template = (load_copy(language).get("rules") or {}).get(dimension_id(dim))
    if not isinstance(template, str):
        return template
    try:
        return template.format(cap=f"{effective_position_cap(cap):.0%}")
    except (KeyError, IndexError, ValueError):
        return template


# ── Candidate-rule grounding (#248) ──────────────────────────────────────────
# The canonical rule text stays a reusable template — it is tracked across
# weeks in rules.jsonl, so a single period's tickers must never be baked into
# it. The tie to the user's actual positions travels as a separate
# engine-authored ``grounding`` sentence instead. Facts come only from
# existing engine_card output (dims_raw / ticker_diagnosis): no new
# computation, and a dimension without citable facts omits the sentence
# rather than printing an empty shell.
RULE_GROUNDING_TICKER_LIMIT = 2
# #349: past this many named entries the Block-4 targets line reads as a raw
# data dump rather than a point of view (owner dogfood finding), so the
# remainder collapses into one localized "+N more" tail instead. Distinct
# from RULE_GROUNDING_TICKER_LIMIT above (a different, shorter sentence in
# the question layer) and from the diversification/holding_period "top 3
# cluster" slices in rule_grounding_items below, which already name at most
# 3 positions by definition and so never hit this limit.
RULE_TARGETS_DISPLAY_LIMIT = 4
# 與 trade_recap.POSITION_CAP 同一條契約:card_renderer 刻意不 import trade_recap
# (與 coach/horizon 前例同語意 — 保持純標準庫、免 pandas),兩處常數由
# test_card_html 斷言同步。改一處必改另一處。
POSITION_CAP = 0.20
# #328: mirrors trade_recap.OVERSIZE_TRIGGER under the same stdlib-only,
# no-import-trade_recap boundary as POSITION_CAP above. This is the
# diagnostic trigger the engine actually flags a position_sizing hole
# against; POSITION_CAP is only the coach's suggested target once the rule
# is committed. The two are deliberately different since #334: a holding
# between them was never judged a problem by any engine path, so any
# user-visible list of "positions this rule would act on" must filter on
# this constant, not on POSITION_CAP.
OVERSIZE_TRIGGER = 0.25


def valid_position_cap(value):
    """(0,1) 的 float,否則 None(fail-closed)。trade_recap.valid_position_cap 的 stdlib 鏡像,
    與 POSITION_CAP 同一條「不 import trade_recap」的邊界(#324)。"""
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return None
    return pct if 0 < pct < 1 else None


def effective_position_cap(override=None):
    """規矩文案帶的「建議上限」:用戶自訂(合法時)否則通用預設 POSITION_CAP。"""
    return valid_position_cap(override) or POSITION_CAP


def effective_oversize_trigger(override=None):
    """The diagnostic trigger: the user's standing single-position override
    when valid, otherwise the universal OVERSIZE_TRIGGER. Stdlib mirror of
    trade_recap.effective_oversize_trigger (#328) — this decides which
    positions a targets list names, not what the rule text recommends
    compressing down to (that stays effective_position_cap)."""
    return valid_position_cap(override) or OVERSIZE_TRIGGER


def _grounding_dims(card):
    dims = {}
    for row in (card or {}).get("dims_raw") or []:
        if isinstance(row, dict) and row.get("dim") and row.get("applicable", True):
            dims[dimension_id(row.get("dim"))] = row
    return dims


def _dimension_is_applicable(card, dim):
    """Whether this card still supports a dimension claim.

    The engine marks zero-denominator current-book dimensions inapplicable.
    Renderers must honor that marker too: a replayed/hand-built legacy card
    must not turn an empty book into a sizing or diversification strength,
    hole, or target merely because it carries stale rows.  A prior
    user-confirmed standing rule remains visible for reconciliation.
    """
    dim_id = dimension_id(dim)
    for row in (card or {}).get("dims_raw") or []:
        if isinstance(row, dict) and dimension_id(row.get("dim")) == dim_id:
            return row.get("applicable", True) is not False
    return True


def _applicable_holes(card):
    holes = []
    for hole in ((card or {}).get("top_holes") or []):
        if not isinstance(hole, dict):
            continue
        raw = hole.get("raw") or {}
        # A hole is its own claim.  Do not rely only on dims_raw: legacy or
        # hand-built cards can omit it, while the hole still explicitly says
        # its fact is inapplicable.
        if hole.get("applicable", True) is False or raw.get("applicable", True) is False:
            continue
        dim = raw.get("dim") or hole.get("dim")
        if _dimension_is_applicable(card, dim):
            holes.append(hole)
    return holes


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


def rule_grounding_items(card, dim_id, cap_override=None):
    """The positions or behavior counts the committed rule would act on this
    period (#302), ranked, or ``[]`` when nothing is citable.

    Deliberately separate from ``rule_grounding_facts``: that sentence is also
    consumed by the question layer, so widening it there would change wording
    users answer against. These items only ever render inside Block 4. Facts
    come from the same engine card — no new computation, no event detail (dates
    and prices stay in ``problem_events``); the card needs names the reader can
    match against their own positions, not a transaction log.

    ``cap_override`` is the user's standing single-position override
    (``state.max_position_pct``, #324): threaded into ``effective_oversize_
    trigger`` so a custom cap moves the position_sizing filter the same way
    it already moves the committed rule text and the engine's own severity.
    """
    dims = _grounding_dims(card)
    dim = dims.get(dim_id)
    if not isinstance(dim, dict):
        return []
    if dim_id == "position_sizing":
        # #328: filter on the diagnostic *trigger*, not the stricter coach
        # target (POSITION_CAP). Only crossing the trigger makes the engine
        # flag sizing as a hole and open the cut_oversize prescription in the
        # first place — a holding between the two was never judged a problem
        # by any engine path, so naming it here made the card stricter than
        # the engine's own judgment. A rule that lists compliant holdings
        # also reads as noise, which is the Block-4 bloat #301 is undoing.
        weights = dim.get("risk_weights")
        if not isinstance(weights, dict):
            return []
        trigger = effective_oversize_trigger(cap_override)
        over = [(t, _positive_rate(w)) for t, w in weights.items()
                if isinstance(t, str) and t and _positive_rate(w) is not None
                and float(w) > trigger]
        return [{"ticker": t, "kind": "pct", "value": v}
                for t, v in sorted(over, key=lambda item: (-item[1], item[0]))]
    if dim_id == "averaging_down":
        counts = dim.get("ticker_counts")
        if not isinstance(counts, dict):
            return []
        ranked = [(t, int(n)) for t, n in counts.items()
                  if isinstance(t, str) and t and isinstance(n, (int, float)) and n >= 1]
        return [{"ticker": t, "kind": "count", "value": n}
                for t, n in sorted(ranked, key=lambda item: (-item[1], item[0]))]
    if dim_id == "diversification":
        # The concentration rule acts on the cluster, so name the top weights
        # rather than only the single largest one.
        weights = (dims.get("position_sizing") or {}).get("risk_weights")
        if not isinstance(weights, dict):
            return []
        ranked = [(t, _positive_rate(w)) for t, w in weights.items()
                  if isinstance(t, str) and t and _positive_rate(w) is not None]
        return [{"ticker": t, "kind": "pct", "value": v}
                for t, v in sorted(ranked, key=lambda item: (-item[1], item[0]))[:3]]
    if dim_id == "holding_period":
        tickers = [t for t in dim.get("incon_tickers") or [] if isinstance(t, str) and t]
        return [{"ticker": t, "kind": "bare", "value": None} for t in tickers[:3]]
    return []


def localized_rule_targets(dim, language, card, cap_override=None):
    """The one-line "what this rule would catch this period" list (#302), or
    ``None`` when the dimension has nothing citable.

    #349: named entries are capped at ``RULE_TARGETS_DISPLAY_LIMIT`` (items
    are already ranked by impact in ``rule_grounding_items``) so the line
    stays a short, scannable point of view rather than an enumerated dump;
    any remainder collapses into one localized "+N more" tail. If the copy
    contract has no ``more_suffix`` for the active locale, fall back to
    showing every entry instead of silently dropping the overflow — an
    over-long line is preferable to one that hides facts without saying so.
    """
    items = rule_grounding_items(card, dimension_id(dim), cap_override)
    if not items:
        return None
    copy = load_copy(language)
    rule_targets_copy = copy.get("rule_targets") or {}
    template = rule_targets_copy.get("line")
    if not template:
        return None
    shown = items[:RULE_TARGETS_DISPLAY_LIMIT]
    overflow = len(items) - len(shown)
    more_suffix = rule_targets_copy.get("more_suffix") if overflow > 0 else None
    if overflow > 0 and not more_suffix:
        shown, overflow = items, 0
    joiner = ", " if copy["language"] == "en" else "、"
    parts = []
    for item in shown:
        if item["kind"] == "pct":
            parts.append(f"{item['ticker']} {item['value'] * 100:.0f}%")
        elif item["kind"] == "count":
            unit = rule_targets_copy.get("count_unit", "")
            parts.append(f"{item['ticker']} {item['value']}{unit}")
        else:
            parts.append(item["ticker"])
    joined = joiner.join(parts)
    if overflow > 0:
        try:
            joined += more_suffix.format(n=overflow)
        except (KeyError, IndexError, ValueError):
            pass
    try:
        return template.format(items=joined)
    except (KeyError, IndexError, ValueError):
        return None


# ── Strength / rule trade-off reconciliation (#301) ──────────────────────────
# A card can hold a demonstrated strength and a concentration risk that point
# at the same position: "don't let sizing dilute your edge" beside "PLTR is
# 49%, too heavy" reads as two contradictory orders. The engine already ranks
# them — only ``cut_loss`` prescriptions carry a ``rule`` and can be committed;
# ``amplify`` rows never can. Block 4 therefore renders exactly one action and
# states the relationship, instead of listing both as peers.
# Ranked strongest claim first: a card states one thing the period proved, not
# a list. "Proven edge" outranks "still a hypothesis" outranks "can't tell yet".
_AMPLIFY_KINDS = ("amplify", "amplify_hypothesis", "selection_inconclusive")
_TRADEOFF_DIMS = ("position_sizing", "diversification")


def amplify_row(card, language):
    """The single strongest strength claim among the prescription rows, or
    ``None``.

    These rows describe what the period proved rather than something to do, so
    they belong beside the ``[v]`` strength in Block 3, not in Block 4. Only
    one renders: stacking all three restates the same attribution split three
    ways, which is the caveat pile-up #276 is unwinding."""
    by_kind = {}
    for item in (card or {}).get("prescriptions") or []:
        if not isinstance(item, dict) or item.get("kind") not in _AMPLIFY_KINDS:
            continue
        by_kind.setdefault(item["kind"], item)
    for kind in _AMPLIFY_KINDS:
        if kind not in by_kind:
            continue
        resolved = localized_prescription(by_kind[kind], language)
        if resolved and resolved["text"]:
            return resolved
    return None


def outsource_row(card, language):
    """The ``outsource`` prescription, which reads as a weakness finding rather
    than a strength: it fires when selection alpha is credibly negative.

    It belongs under the ``[X]`` hole, not next to the strength, and never in
    Block 4 — it carries no committable rule, so rendering it there would put a
    second imperative beside the one committed rule (#301)."""
    for item in (card or {}).get("prescriptions") or []:
        if isinstance(item, dict) and item.get("kind") == "outsource":
            resolved = localized_prescription(item, language)
            if resolved and resolved["text"]:
                return resolved
    return None


def rule_tradeoff_line(card, dim, language):
    """One engine-owned sentence reconciling the committed rule with a strength
    the same card claims, or ``None`` when the two do not collide.

    Collision means the rule asks the user to shrink a position while an
    ``amplify`` row credits the very selection that built it. Silence when the
    rule targets another dimension: an unconditional sentence here would be the
    caveat-noise #301 exists to remove."""
    if dimension_id(dim) not in _TRADEOFF_DIMS:
        return None
    kinds = {item.get("kind") for item in (card or {}).get("prescriptions") or []
             if isinstance(item, dict)}
    if "amplify" in kinds:
        code = "sizing_vs_proven_edge"
    elif "amplify_hypothesis" in kinds:
        code = "sizing_vs_hypothesis"
    else:
        return None
    return ((load_copy(language).get("rule_tradeoff") or {}).get(code)) or None


def condition_state_line(commitment, language):
    """One engine-owned sentence about the condition this commitment carries, or
    ``None`` when there is nothing the engine can say (#412).

    The engine performed this comparison itself at commit time, so this is the
    one place a wrong lookup becomes visible to the user: a line already crossed
    when they committed to it is a decision to make now, not a tripwire to watch,
    and a condition nobody could anchor must not sit on the card looking watched.

    Copy-fallback only, like the reconciliation opener's breach sentence — it
    never reads `narrative.honesty`, so it reaches the reader regardless of how
    the agent worded anything. Digit-free: the magnitudes stay in the record."""
    condition = (commitment or {}).get("condition") or {}
    code = None
    if condition.get("baseline_verdict") == "met":
        code = "already_met"
    elif condition.get("tier") == "unmapped":
        code = {"no_threshold": "unmapped_no_threshold",
                "no_baseline": "unmapped_no_baseline",
                # Written before the check flow existed; the row is never
                # rewritten, so the sentence it resolves to has to stay.
                "no_adjudicator": "unmapped_no_adjudicator"}.get(condition.get("unmapped_reason"))
    elif (condition.get("kind") or "numeric") == "event":
        # #412: an event condition has no baseline comparison to report, so the
        # commit exchange would otherwise end in silence — and silence is how a
        # user comes to think nothing was recorded. The watch starting IS the
        # news; the adjudication is a later review's.
        code = "event_watch_started"
    if not code:
        return None
    return ((load_copy(language).get("condition_state") or {}).get(code)) or None


def condition_value(value, unit):
    """One condition figure as the user wrote its unit (#412).

    A condition's unit is free text the user chose ("%", "USD", "x", "days"),
    not one of the engine's own metric families, so this neither converts nor
    re-scales: it prints the number that was found and the unit it was found
    in. A `%` unit is glued to the number the way a percentage is written
    everywhere else on the card; anything else takes a space, because "38 days"
    is a phrase and "38days" is a typo."""
    number = _finite_number(value)
    if number is None:
        return None
    text = str(int(number)) if number == int(number) else f"{number:g}"
    unit = str(unit or "").strip()
    if not unit:
        return text
    return f"{text}%" if unit == "%" else f"{text} {unit}"


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


def _tag_format_values(params, language):
    """Presentation formatting for raw tag params, one locale at a time.

    #347: current price and average cost per share travel beside ``cur_ret``
    so a reader cannot mistake the position's current-vs-cost percentage for a
    trade amount. ``price_note`` is one self-contained fragment (not two bare
    placeholders) so a template can reference it unconditionally: it renders
    as an empty string whenever either raw number is missing (older bundles,
    a tag that never carried price data), keeping this byte-identical to the
    pre-#347 text in that case."""
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
    px = _finite_number((params or {}).get("px"))
    avg_cost = _finite_number((params or {}).get("avg_cost"))
    if px is not None and avg_cost is not None:
        template = (load_copy(language).get("tag_values") or {}).get("price_note")
        values["price_note"] = _format_copy(
            template, px=f"{px:,.2f}", avg_cost=f"{avg_cost:,.2f}") or ""
    else:
        values["price_note"] = ""
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
        return template.format(**_tag_format_values(tag.get("params"), language))
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
    # Resolve first so an unsupported tag inherits the en fallback's currency
    # (#389) instead of a zh-TW bias; canonical locales keep their mapping.
    return DISPLAY_CURRENCY_BY_LANGUAGE.get(resolve_language(language),
                                            DISPLAY_CURRENCY_BY_LANGUAGE["en"])


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
    """Signed money, sign-before-symbol (#311): +$10,960 / -$3,000 / +TWD 1,234.

    ``f"{value:+,.0f}"`` always starts with a forced '+' or '-' (Python's sign
    format spec); slicing that leading character off and re-prepending it
    before the currency symbol reorders "$+10,960" into "+$10,960" without
    touching the grouping/rounding behavior underneath."""
    if value is None:
        return "—"
    symbol = "$" if currency == "USD" else currency + " "
    signed = f"{float(value):+,.0f}"
    return f"{signed[0]}{symbol}{signed[1:]}"


def _money_abs(value, currency):
    if value is None:
        return "—"
    symbol = "$" if currency == "USD" else currency + " "
    return f"{symbol}{abs(float(value)):,.0f}"


def _money_compact(value, currency):
    """Signed money, rounded to the nearest thousand once |value| >= $10,000
    (e.g. +$119k), else identical to `_money`.

    A metric cell's sub has room for roughly one 3-4 digit figure per
    wrapped line; a realized/unrealized pair at 5-6 digit magnitudes never
    fits two-up in that slot no matter how the surrounding words are
    trimmed (#382), so the figure itself has to shrink there. Below the
    threshold the full amount is already short enough to fit, and rounding
    a $200 gain to "$0k" would misstate it, so this defers to `_money`
    unchanged."""
    if value is None:
        return "—"
    if abs(float(value)) < 10000:
        return _money(value, currency)
    symbol = "$" if currency == "USD" else currency + " "
    signed = f"{float(value) / 1000.0:+,.0f}"
    return f"{signed[0]}{symbol}{signed[1:]}k"


def _display_money(value, context, absolute=False):
    if not context.get("currency") or context.get("factor") is None:
        return None
    converted = None if value is None else float(value) * float(context["factor"])
    return (_money_abs if absolute else _money)(converted, context["currency"])


def _display_money_compact(value, context):
    """`_display_money`'s FX-conversion step, formatted through
    `_money_compact` instead of `_money` -- see that docstring for why."""
    if not context.get("currency") or context.get("factor") is None:
        return None
    converted = None if value is None else float(value) * float(context["factor"])
    return _money_compact(converted, context["currency"])


def _currency_note(card, language):
    context = _display_context(card, language)
    note = load_copy(language).get("currency_note") or {}
    if context.get("source") == "cached":
        when = context.get("as_of")
        if when:
            return _format_copy(note.get("cached_dated"), when=when)
        return note.get("cached")
    if context.get("source") == "unavailable":
        if context.get("reason") == "portfolio_fx_gap":
            return note.get("portfolio_fx_gap")
        requested = context.get("requested") or default_display_currency(language)
        return _format_copy(note.get("no_rate"), currency=requested)
    return None


def _original_pnl_lines(card, language):
    rows = ((card.get("currency_meta") or {}).get("pnl_by_currency") or {})
    pnl_copy = (load_copy(language).get("pnl_lines") or {}).get("original") or {}
    lines = []
    for currency, row in sorted(rows.items()):
        realized = _finite_number((row or {}).get("realized"))
        unrealized = _finite_number((row or {}).get("unrealized"))
        if realized is None and unrealized is None:
            continue
        if realized is not None and unrealized is not None:
            total = realized + unrealized
            lines.append(pnl_copy["both"].format(
                currency=currency, total=_money(total, currency),
                realized=_money(realized, currency), unrealized=_money(unrealized, currency)))
        elif realized is not None:
            lines.append(pnl_copy["realized_only"].format(
                currency=currency, realized=_money(realized, currency)))
        else:
            lines.append(pnl_copy["unrealized_only"].format(
                currency=currency, unrealized=_money(unrealized, currency)))
    return lines


def unrealized_is_measured(overview):
    """False when positions are held but not one of them has a current price.

    The engine sums only priced positions, so a portfolio with no retrievable
    prices produces a zero that means "nothing was measured", not "no gain".
    Printing that zero would treat missing data as zero, which the card spec
    forbids and #289 saw happen. The engine owns the condition
    (``unrealized_coverage``); the renderer only chooses the wording.
    """
    coverage = (overview or {}).get("unrealized_coverage") or {}
    return not (coverage.get("held_n") and not coverage.get("priced_n"))


def _overview_lines(card, language):
    overview = card.get("overview") or {}
    context = _display_context(card, language)
    if context.get("currency"):
        total_value = _finite_number(overview.get("total_pnl"))
        realized_value = _finite_number(overview.get("realized"))
        unrealized_value = _finite_number(overview.get("unrealized"))
        if not unrealized_is_measured(overview):
            # Falls through to the realized-only sentence, which already says
            # the unrealized side was not scored.
            unrealized_value = None
        pnl_copy = (load_copy(language).get("pnl_lines") or {}).get("display") or {}
        if total_value is not None and realized_value is not None and unrealized_value is not None:
            total = _display_money(total_value, context)
            realized = _display_money(realized_value, context)
            unrealized = _display_money(unrealized_value, context)
            return [pnl_copy["total"].format(total=total, realized=realized, unrealized=unrealized)]
        if realized_value is not None:
            realized = _display_money(realized_value, context)
            return [pnl_copy["realized_only"].format(realized=realized)]
        if unrealized_value is not None:
            unrealized = _display_money(unrealized_value, context)
            return [pnl_copy["unrealized_only"].format(unrealized=unrealized)]
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


def _pp(value, digits=0):
    """Format a *difference of two returns* as signed percentage points, with
    the unit baked in like ``_pct()`` bakes in "%" -- never percent (output
    contract §5: "% means absolute return, pp means excess").

    ``cash_drag = acct_twr − hold_twr`` is exactly this kind of value; the
    account-level TWR and holdings-only TWR it is built from are each an
    absolute return and stay on ``_pct()``. Signed and negative-zero-free like
    ``_benchmark_pp``/``_signed_pp``, but (a) keyed next to ``_pct()`` as a
    drop-in unit fix for that call shape, and (b) parameterized on ``digits``
    like ``_pct()`` rather than fixed at one precision."""
    number = _finite_number(value)
    if number is None:
        return "—"
    points = round(number * 100, digits)
    if points == 0:
        points = 0.0  # avoid "-0"/"-0.0"
    return f"{points:+.{digits}f}pp"


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
    """The benchmark-excess sentence: how far the holdings ran ahead of their
    market, and at what beta.

    Owner ruling 2026-07-23 (#363, "one concept, one indicator"): the two
    absolute total returns this sentence used to open with — ``port_tot`` and
    ``spy_tot`` — no longer render. ``port_tot`` is *the same concept* as the
    card's cumulative return (`acct_perf.hold_twr`), computed by a different
    pipeline: the regression's aligned day set drops days either side is
    missing and days the portfolio moved more than ±50%, so it answered "what
    did you make?" with a second, smaller number and no way for the reader to
    tell why. It is a regression intermediate that leaked onto the card. The
    excess it feeds is a genuinely different concept and stays; the raw pair
    goes back to being internal.

    What remains is exactly what the excess KPI tile carries (value + β sub),
    so the sentence takes the pnl/payoff treatment: ``kpi_id="excess"`` with an
    empty ``html_text``, i.e. HTML drops it whenever that tile rendered, and it
    stands alone on Markdown and on any card with no such tile."""
    excess = _finite_number(row.get("excess_vs_spy"))
    beta = _beta_text(row.get("beta"))
    copy = load_copy(language).get("benchmark_line") or {}
    beta_suffix = ("" if beta is None
                   else _format_copy(copy.get("beta_suffix"), beta=beta) or "")
    subject = (_format_copy(copy.get("subject_market"), market=market) if market
               else copy.get("subject_default"))
    comparator = bench or copy.get("comparator_default")
    return _format_copy(copy.get("line"), subject=subject or "",
                        comparator=comparator or "",
                        excess=_benchmark_pp(excess), beta=beta_suffix)


def _private_split_lines(market, row, language):
    """Explain positive benchmark excess using the engine's accounting split."""
    excess = _finite_number(row.get("excess_vs_spy"))
    split = row.get("excess_split") or {}
    allocation = _finite_number(split.get("allocation"))
    selection = _finite_number(split.get("selection"))
    if excess is None or excess <= 0 or allocation is None or selection is None:
        return []
    copy = load_copy(language).get("split_lines") or {}
    subject = (_format_copy(copy.get("subject_market"), market=market) if market
               else copy.get("subject_default"))
    line = _format_copy(copy.get("line"), subject=subject or "",
                        excess=_benchmark_pp(excess),
                        allocation=_benchmark_pp(allocation),
                        selection=_benchmark_pp(selection))
    if line is None:
        return []
    # Coverage limitations belong to the engine-triggered sector_attribution
    # honesty entry, which collapses into the Block-1 footnote rather than
    # appearing next to this split (2026-07-22 ruling, §4).
    return [line]


def _alpha_interval_line(ab, language):
    """The full alpha-interval sentence: headline "alpha was +X% annualized",
    its 95% interval, and (#313) a plain-language caveat when the interval's
    lower bound is negative.

    This is Markdown's only carrier of the interval — no tile grid exists
    there — and it is also what HTML falls back to on any card whose alpha
    tile did not render this period (mixed-market, month-gated): the caller,
    ``_performance_items``, tags this item ``kpi_id="alpha"``, and HTML only
    swaps in the trimmed ``html_text`` variant when that tile actually
    rendered (render_html's ``indicator_items``). On a card whose alpha tile
    *did* render, the interval instead lives in the tile's own sub
    (``_alpha_tile_sub``, #363) and this full sentence is Markdown-only; what
    a tile's sub still cannot hold is ``_alpha_standalone_note``. Before #363
    this function also had a ``lead=False``/"compact" mode that dropped the
    headline clause for a below-grid HTML line holding just the interval —
    that line no longer exists (the interval moved into the tile itself), so
    the mode and the copy key it read (``alpha_interval.compact``) are gone
    rather than left unreachable."""
    stat = ab.get("alpha_stat") or {}
    alpha = _finite_number(stat.get("alpha_ann"))
    ci = stat.get("ci95")
    if alpha is None or not isinstance(ci, (list, tuple)) or len(ci) != 2:
        return None
    low, high = (_finite_number(ci[0]), _finite_number(ci[1]))
    if low is None or high is None:
        return None
    alpha_copy = load_copy(language).get("alpha_interval") or {}
    market = ab.get("scope") if isinstance(ab.get("by_market"), dict) else None
    scope = (alpha_copy.get("scope_suffix", "").format(market=market)
             if market in MARKET_BENCHMARKS else "")
    # #313: a lower bound below zero is statistically opaque to a retail reader
    # ("95% interval from -10% to +74%" does not by itself say whether that is
    # good or bad news). Append one plain-language sentence, both locales, only
    # when the condition holds -- the card stays a coherent story rather than
    # printing a caveat nobody needs when the interval is comfortably positive.
    plain = alpha_copy.get("negative_caveat", "") if low < 0 else ""
    # #272: Arabic digits for the interval level — one digit style per sentence.
    alpha_text = f"{alpha * 100:+.0f}"
    low_text = f"{low * 100:+.0f}"
    high_text = f"{high * 100:+.0f}"
    return alpha_copy["lead"].format(scope=scope, alpha=alpha_text,
                                      low=low_text, high=high_text) + plain


def _alpha_tile_sub(ab, language):
    """The alpha KPI tile's sub slot: the 95% interval, in the short form a
    tile's sub can actually hold (design-guidelines §6: capped at two lines).

    Returns ``None`` when ``alpha_stat.ci95`` is absent or not finite — the
    interval cannot be built — so the caller (``_kpi_tiles``) can fall back to
    the pre-#363 sub (the not-yet-credible legend, or nothing). That fallback
    matters: without it, a non-credible alpha whose ci95 happens to be missing
    would lose the only legend for its value's "*" suffix. The condition is
    read once, from the data (ci95 present and finite), not enumerated per
    scenario (design-guidelines §3.3) — a future caller with a differently
    shaped gap still degrades the same way."""
    stat = ab.get("alpha_stat") or {}
    ci = stat.get("ci95")
    if not isinstance(ci, (list, tuple)) or len(ci) != 2:
        return None
    low, high = (_finite_number(ci[0]), _finite_number(ci[1]))
    if low is None or high is None:
        return None
    template = (load_copy(language).get("alpha_interval") or {}).get("tile_sub")
    if not template:
        return None
    return template.format(low=f"{low * 100:+.0f}", high=f"{high * 100:+.0f}")


def _alpha_standalone_note(ab, language):
    """#363: what the alpha tile's sub can no longer hold once ``_alpha_tile_sub``
    fills it with the interval — the not-yet-credible legend for the value's
    "*" suffix, and the #313 plain-language reading of a negative lower bound.

    Each half fires on its own pre-existing, independent trigger (``credible``;
    ``ci95``'s low bound) exactly as it always has — this only changes *where*
    each renders, never *when*. The two triggers are not merged: a card can
    hit either, both, or neither. Both halves name their own subject ("年化
    α"/"annualized α") because this line no longer sits directly beneath a
    lead clause that already named alpha for it (the way the pre-#363 prose
    line, or the tile's own label, did) — see the #363 ruling in
    docs/output-contract.md for why a bare "the interval..." would be
    orphaned here. Returns "" when neither triggers, which is what lets HTML
    print no line at all beneath a credible tile with a wholly-positive
    interval — the point of moving the interval into the tile in the first
    place."""
    stat = ab.get("alpha_stat") or {}
    ci = stat.get("ci95")
    if not isinstance(ci, (list, tuple)) or len(ci) != 2:
        return ""
    low = _finite_number(ci[0])
    if low is None:
        return ""
    alpha_copy = load_copy(language).get("alpha_interval") or {}
    parts = []
    if not ab.get("credible"):
        note = alpha_copy.get("below_unreliable")
        if note:
            parts.append(note)
    if low < 0:
        note = alpha_copy.get("below_negative")
        if note:
            parts.append(note)
    return " ".join(parts)


def _hole_line(hole, language):
    """The leading hole's number narration, computed from the raw dimension.

    Until #387 this function had two unequal halves: any non-``en`` locale
    returned ``hole["number_line"]`` — a zh sentence ``trade_recap.number_line``
    had already rendered — while ``en`` got a shorter sentence written natively
    here. That was a locale gap of the kind §2 of the output-language contract
    calls a defect, and it hid behind the structure-equivalence check because
    both cards print exactly one line: the en reader of a ``holding_period``
    hole saw a range and a median and was never told what the leak *was* (that
    the same name is both day-traded and held long).

    Both locales now render the richer narration from the same raw dimension
    through copy ``hole_lines``. ``number_line`` stays on the engine card for
    the v1 card, and the maintainer guide's mirror obligation between the two
    now covers two genuinely independent implementations rather than one
    string reused."""
    d = hole.get("raw") or {}
    if d.get("applicable", True) is False:
        return ""
    dim = dimension_id(d.get("dim"))
    copy = (load_copy(language).get("hole_lines") or {})
    def tickers(values, limit):
        return (copy.get("ticker_joiner") or ", ").join(
            str(t) for t in (values or [])[:limit])

    if dim == "exit_discipline":
        parts = []
        if d.get("early_rate") is not None:
            winner_early = ""
            if d.get("winner_early") is not None:
                winner_early = _format_copy(copy.get("exit_winner_early"),
                                            winner_early=_pct(d.get("winner_early"))) or ""
            parts.append(_format_copy(
                copy.get("exit_forward"), n_rt=d.get("n_rt", 0),
                n_scored=d.get("n_scored", 0), n_trunc=d.get("n_trunc", 0),
                early_rate=_pct(d.get("early_rate")), n_fwd=d.get("n_fwd", ""),
                avg_forgone=f"{float(d.get('avg_forgone') or 0) * 100:+.1f}%",
                winner_early=winner_early))
        parts.append(_format_copy(
            copy.get("exit_holding"),
            hold_win=f"{float(d.get('hold_win') or 0):.0f}",
            hold_lose=f"{float(d.get('hold_lose') or 0):.0f}",
            disp_gap=f"{float(d.get('disp_gap') or 0):+.0f}"))
        return (copy.get("joiner") or "; ").join(p for p in parts if p)
    if dim == "position_sizing":
        return _format_copy(copy.get("position_sizing"),
                            max_ticker=d.get("max_ticker"),
                            max_pct=_pct(d.get("max_pct")),
                            avg_pct=_pct(d.get("avg_pct"))) or ""
    if dim == "diversification":
        # #754: "one and the same driver" only holds when dim_diversify() has
        # actually verified every top-3-by-weight ticker sits inside the
        # dominant driver bucket (``top3_same_driver``) — top3 itself is a
        # pure market-value ranking, independent of driver classification, so
        # the claim must not be printed unconditionally onto it.
        same_driver_note = ""
        if d.get("top3_same_driver"):
            same_driver_note = copy.get("diversification_same_driver") or ""
        return _format_copy(copy.get("diversification"), n=d.get("n", 0),
                            ai_pct=_pct(d.get("ai_pct")),
                            max_sector=localized_sector(d.get("max_sector"), language),
                            max_sector_pct=_pct(d.get("max_sector_pct")),
                            top3=_pct(d.get("top3")),
                            same_driver_note=same_driver_note) or ""
    if dim == "holding_period":
        if d.get("no_data"):
            return copy.get("holding_no_data") or ""
        if d.get("all_same_day"):
            return copy.get("holding_same_day") or ""
        median = f"{float(d.get('median_hold') or 0):.0f}"
        base = _format_copy(copy.get("holding_base"), min=d.get("min", 0),
                            max=d.get("max", 0), median_hold=median)
        if d.get("n_incon", 0) > 0:
            return _format_copy(copy.get("holding_inconsistent"), base=base,
                                n_incon=d.get("n_incon"), n_multi=d.get("n_multi"),
                                tickers=tickers(d.get("incon_tickers"), 5)) or ""
        return _format_copy(copy.get("holding_consistent"), base=base,
                            median_hold=median) or ""
    if dim == "averaging_down":
        # #348: "crossed the position-size boundary" read like today's market-value
        # concentration and collided with the sizing dimension's wording. Both anchors
        # must be in the sentence: "at the moment of that add" (not now) and "cost
        # basis" (not market value).
        return _format_copy(copy.get("averaging_down"), count=d.get("count", 0),
                            tickers=tickers(d.get("tickers"), 6),
                            breach=d.get("breach", 0)) or ""
    return ""


def _best_strength(card, language):
    """The "one thing you did right" line, ranked from the raw dimensions.

    Same #387 story as ``_hole_line``: the zh path returned the sentence
    ``trade_recap.dim_strength`` had already rendered, while ``en`` fell back to
    a generic "the cleanest part of this review was <dimension>". Both locales
    now rank the same weighted candidates here and render through copy.

    Known gap, deliberate: ``dim_strength``'s exit-discipline candidate can
    append a worked example ("you sold X up 40%, it moved +1% after"), built
    from the round-trip list. The v2 engine card does not carry round trips —
    ``ticker_diagnosis`` holds impact and tags, not per-trade ret/fwd — so that
    parenthetical cannot be rebuilt here, and this branch renders the base
    sentence in both locales. Restoring it needs the engine to emit a
    structured strength (code + params) the way ``top_holes[].raw`` already
    does, which is a change inside the v1 file the contract fences off."""
    dims = _grounding_dims(card)
    copy = (load_copy(language).get("best_strength") or {})
    candidates = []

    exit_dim = dims.get("exit_discipline") or {}
    winner_early = _finite_number(exit_dim.get("winner_early"))
    if winner_early is not None and winner_early < 0.35 and not exit_dim.get("low_conf"):
        candidates.append((0.7 + (0.35 - winner_early),
                           _format_copy(copy.get("exit_discipline"),
                                        winner_early=_pct(winner_early))))
    size_dim = dims.get("position_sizing") or {}
    max_pct = _finite_number(size_dim.get("max_pct"))
    if max_pct is not None and max_pct < 0.22:
        candidates.append((1 - max_pct, _format_copy(copy.get("position_sizing"),
                                                     max_pct=_pct(max_pct))))
    avg_dim = dims.get("averaging_down") or {}
    if avg_dim.get("breach", 1) == 0 and avg_dim.get("count", 0) >= 2:
        first = (avg_dim.get("tickers") or [""])[0]
        example = _format_copy(copy.get("averaging_down_example"), ticker=first) if first else ""
        candidates.append((0.65, _format_copy(copy.get("averaging_down"),
                                              count=avg_dim.get("count"),
                                              example=example or "")))
    div_dim = dims.get("diversification") or {}
    if not div_dim.get("triggered") and div_dim.get("n", 0) >= 5:
        candidates.append((0.6, _format_copy(copy.get("diversification"),
                                             n=div_dim.get("n"))))
    hold_dim = dims.get("holding_period") or {}
    median_hold = _finite_number(hold_dim.get("median_hold"))
    if not hold_dim.get("triggered") and median_hold:
        candidates.append((0.5, _format_copy(copy.get("holding_period"),
                                             median_hold=f"{median_hold:.0f}")))

    ranked = [text for _weight, text in sorted(candidates, key=lambda c: -c[0]) if text]
    if ranked:
        return ranked[0]
    return copy.get("no_signal", "")


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


def price_retrieval_blocked(card):
    """True when price availability — not the cash anchor — blocks the return.

    #289: a host that cannot reach the price source is a data-availability
    failure, and the gap note must name that blocker instead of reciting the
    cash-flow reason. Cards without price provenance (legacy bundles) keep the
    original wording.
    """
    provenance = (card or {}).get("price_provenance") or {}
    if provenance.get("mode") == "unavailable":
        return True
    coverage = provenance.get("coverage") or {}
    return bool(coverage.get("requested_n")) and not coverage.get("priced_n")


# #375: gate status -> the block_missing key whose sentence names that blocker.
# Only the statuses that come back as a bare `acct_perf` (no hold_twr, so the
# account line never renders and the gap note speaks instead) need an entry; a
# status missing from this map degrades to the generic `annualized` sentence,
# so a future engine gate never renders blank. The account-level counterpart —
# a blocker that still leaves the holdings pillar standing — is the
# `account_gate` copy group, selected in _performance_items.
ANNUALIZED_GAP_NOTE_BY_GATE = {
    "no_prices": "annualized_prices",
    "short_price_series": "annualized_short_series",
    "accounting_reconciliation": "annualized_reconciliation",
}


def _annualized_gap_note(card, missing):
    """The Block-1 gap note for a missing annualized/account module (§4).

    Price retrieval failure keeps its own #289 variant and wins, because
    `price_provenance` is the authority on that specific blocker.
    """
    if price_retrieval_blocked(card):
        return missing.get("annualized_prices", "")
    status = ((card.get("acct_perf") or {}).get("gate") or {}).get("status")
    key = ANNUALIZED_GAP_NOTE_BY_GATE.get(status)
    return (missing.get(key) if key else None) or missing.get("annualized", "")


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


# ── Opening value, stated before the first question (#714) ───────────────────
# The route asks three to five questions before anything the engine found is
# visible, so a first-time user completes an interview to learn whether the
# product found anything at all. This block is the engine's own leading finding
# projected *before* answers exist.
#
# It computes nothing. Both sentences are the ones the card would print anyway:
# the leading hole's number narration through `_hole_line`, and the first
# limitation the ledger already ordered through `_honesty_lines`. That is the
# whole point — a second ranking here would be a parallel truth about which
# finding leads, and the card and the opening could then disagree.
OPENING_VALUE_SCHEMA_VERSION = 1


def build_opening_value(bundle, language, *, questions_required):
    """The leading engine finding, before the first question is asked.

    Returns None when the engine has no applicable hole to lead with — an
    honest absence, not an invented sentence. The caller omits the key rather
    than emitting an empty block, so a resumed pending session written before
    this contract is indistinguishable from a run that had nothing to say.

    `boundary` is the ledger's first triggered limitation, in the ledger's own
    order. It is deliberately *not* selected for relevance to the finding: the
    ledger order is the engine's, and picking "the limitation that qualifies
    this finding" would be exactly the parallel ranking the block exists to
    avoid. It is absent when nothing is triggered.
    """
    card = bundle.get("engine_card") or {}
    copy = load_copy(language)
    opening_copy = copy.get("opening_value") or {}
    line = ""
    dim_id = None
    for hole in card.get("top_holes") or []:
        if not isinstance(hole, dict):
            continue
        line = _hole_line(hole, language)
        if line:
            dim_id = dimension_id((hole.get("raw") or {}).get("dim"))
            break
    if not line:
        return None
    count = max(int(questions_required or 0), 0)
    # Singular/plural through copy rather than a hardcoded noun: #682 shipped
    # "1 completed reviews" by dropping exactly this slot, and no gate saw it.
    template = opening_copy.get("questions_one" if count == 1 else "questions_many")
    value = {
        "schema_version": OPENING_VALUE_SCHEMA_VERSION,
        "label": opening_copy.get("label") or "",
        "finding": {"dimension": dim_id, "line": line},
        "questions_required": count,
        "questions_line": _format_copy(template, count=count) or "",
    }
    honesty = _honesty_lines(bundle, copy)
    for entry in card.get("honesty_ledger") or []:
        key = (entry or {}).get("key")
        if key in honesty:
            value["boundary"] = {"key": key, "line": honesty[key]}
            break
    return value


def _etf_lines(card, language):
    """Each triggered ETF classification as one sentence: which tickers, and
    the rule that explains why they were split into that bucket. Wording is
    copy-templated like every other rendered string (#315: these two
    sentences were hardcoded per-language literals here, bypassing
    ``load_copy()`` — the only spot in the renderer that did). Returns a
    list of 0-2 sentences; empty when the card holds no classified ETFs.

    The caller routes this into the collected Block-2 caveat rather than the
    fact-bullet list (#315): "why X was excluded/exempted" is a
    classification-rule explanation, not a fact about the user's own
    behavior, so it belongs with the card's other collected disclosures
    instead of standing alone in the body."""
    ps = card.get("portfolio_structure") or {}
    allocation = ps.get("allocation_etfs") or []
    concentrated = ps.get("concentrated_etfs") or []
    if not allocation and not concentrated:
        return []
    etf_copy = load_copy(language).get("etf_classification") or {}
    joiner = ", " if language == "en" else "、"
    lines = []
    if allocation and etf_copy.get("allocation"):
        listed = joiner.join(f"{x['ticker']} {_pct(x.get('weight'))}" for x in allocation)
        lines.append(etf_copy["allocation"].format(list=listed))
    if concentrated and etf_copy.get("concentrated"):
        listed = joiner.join(f"{x['ticker']} {_pct(x.get('weight'))}" for x in concentrated)
        lines.append(etf_copy["concentrated"].format(list=listed))
    return lines


def _decision_entries(bundle, copy):
    """(ticker, line) pairs so Block 2 can attach each motive to its instrument
    row (contract §2); a pair without a row keeps its line at block level."""
    labels = copy.get("add_choices") or {}
    entries = []
    for event in bundle.get("thesis_decisions") or []:
        label = labels.get(event.get("decision"), event.get("decision"))
        entry_copy = copy.get("decision_entries") or {}
        ticker = event.get("ticker") or entry_copy.get("ticker_default")
        line = _format_copy(entry_copy.get("line"), ticker=ticker or "", label=label)
        if line:
            entries.append((event.get("ticker"), line))
    return entries


def _headline_motive_entries(bundle, copy):
    """Return the localized rendering of typed headline-motive decisions.

    The event's context is copied from the engine-owned question opportunity.
    A ticker/fact therefore appears only when the engine supplied it; rendering
    never mines the user's prose or infers a security from the chosen class.
    """
    labels = copy.get("headline_motive_choices") or {}
    entries = []
    for event in bundle.get("headline_motive_events") or []:
        choice = event.get("decision")
        label = labels.get(choice, choice)
        context = event.get("context") or {}
        dimension = (context.get("headline_dimension") or {}).get("label")
        ticker = context.get("ticker")
        fact = context.get("asked_because")
        motive_copy = copy.get("motive_entries") or {}
        subject = dimension or motive_copy.get("subject_default")
        terminator = motive_copy.get("terminator") or ""
        parts = []
        if fact:
            parts.append(_format_copy(motive_copy.get("context"),
                                      fact=str(fact).rstrip(terminator)))
        parts.append(_format_copy(motive_copy.get("recorded"),
                                  subject=subject or "", label=label))
        parts.append(motive_copy.get("saved"))
        entries.append((ticker, " ".join(part for part in parts if part)))
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
        exit_copy = copy.get("exit_entries") or {}
        ticker = event.get("ticker") or exit_copy.get("ticker_default")
        action = exit_copy.get("action_full" if kind == "full" else "action_partial")
        line = _format_copy(exit_copy.get("line"), ticker=ticker or "",
                            action=action or "", label=label)
        if line:
            entries.append((event.get("ticker"), line))
    return entries


# ── Block 1 caveat placement (output contract §4) ────────────────────────────
# 2026-07-22 ruling (output-contract.md §4/§9, issue #276 owner_live dogfood,
# 2026-07-22 comment): reverses the 2026-07-21 per-number placement. Real
# high-density accounts (5+ triggered honesty keys) fragmented the indicator
# list into a wall of one-caveat-per-number interruptions — every triggered
# sentence now collapses into the single Block-1 footnote instead (below,
# where ``honesty`` is popped wholesale). ``_HONESTY_HOSTS``/``_place_caveats``
# (the old per-key → indicator-tag host mapping) are gone; tags on the items
# below now exist only for the "insert a missing-module note" logic in
# _performance_block and the market-grouping below, not for caveat hosting.
# etf_metadata still rides the ETF lines in Block 2 (special-cased in
# _card_structure) — that placement is unrelated to this Block-1 ruling.
# #289 price_source: it needs no host entry here. The footnote lists sentences
# in honesty_ledger order, and build_honesty_ledger() emits price_source ahead
# of unrealized_coverage (cause before symptom), so that reading order is
# preserved by the ledger itself, not by a host-number race.

# ── Block 1 KPI-tile / story-line overlap (#344) ─────────────────────────────
# The HTML surface renders the KPI grid (facts["kpi"], built by _kpi_tiles)
# directly above these same items rendered as prose — a number a tile already
# carries must not also stand as a sentence right below it. Markdown has no
# tile grid, so its indicator lines stay the sole carrier of every figure and
# are never touched by this mechanism. An item that a KPI tile can fully or
# partly replace on HTML carries ``kpi_id`` (the matching tile's ``id``) and
# ``html_text`` (what HTML shows instead when that tile actually rendered on
# this card — "" omits the line entirely, a shorter string keeps only what
# the tile cannot hold, e.g. the alpha confidence interval). render_html's
# indicator_items() reads these two keys; render_private ignores them and
# always uses the full ``text``, exactly as before this change.


def _cash_line_text(card, context, language):
    """The declared/anchored cash sentence built from ``card["cash"]``.

    Shared by the trade lane's ``_performance_items`` and the snapshot
    route's Block 1 (#771): both read the identical ``balance``/``weight``/
    ``source``/``reliable``/``by_currency`` shape — ``trade_recap.
    cash_position()`` for the trade lane, ``snapshot_adapter._cash_summary``
    for the snapshot lane, which documents this exact contract on itself so a
    renderer consuming the field cannot tell which lane produced it. One
    sentence-building implementation instead of two keeps it that way.
    Returns ``None`` when the card carries no reliable balance to state."""
    cash = card.get("cash") or {}
    if not (cash.get("reliable") and cash.get("balance") is not None):
        return None
    cash_copy = load_copy(language).get("cash_lines") or {}
    display_cash = _display_money(cash.get("balance"), context)
    if display_cash is not None:
        if cash.get("weight") is not None:
            return _format_copy(cash_copy.get("anchored_with_weight"),
                                cash=display_cash, weight=_pct(cash.get("weight")))
        return _format_copy(cash_copy.get("anchored"), cash=display_cash)
    original = []
    for currency, row in sorted((cash.get("by_currency") or {}).items()):
        if (row or {}).get("balance") is not None:
            original.append(_money((row or {}).get("balance"), currency))
    if not original:
        return None
    joiner = cash_copy.get("amount_joiner") or ", "
    return _format_copy(cash_copy.get("by_currency"), amounts=joiner.join(original))


def _performance_items(card, language):
    """Block-1 indicator lines as tagged items, in the contract §2 order:
    ① absolute P&L (KPI-mirror line) → payoff/drag → ② annualized/account →
    cash → ③ vs market (benchmark/split/alpha) → alternative comparators.

    Tags feed the missing-module fallback logic in _performance_block; text
    wording reuses the same sentences the card always printed so no engine
    number changes shape. Per-market vs-market lines also carry a ``market``
    key ("TW"/"US") so the renderers can visually group a mixed-market card's
    rows by market (#276 2026-07-22 dogfood note) — absent for single-market
    cards, where grouping has nothing to disambiguate."""
    copy = load_copy(language)
    payoff_copy = copy.get("payoff_lines") or {}
    overview = card.get("overview") or {}
    display = _display_context(card, language)
    en = language == "en"
    kpi_copy = copy.get("kpi") or {}
    items = []

    def line(tag, text, market=None, kpi_id=None, html_text=""):
        item = {"kind": "line", "tag": tag, "text": text}
        if market:
            item["market"] = market
        if kpi_id:
            # #344: only the exact KPI-tile-mirror branches pass kpi_id; a
            # fallback sentence (e.g. _overview_lines below) never does, so it
            # always renders in full on every surface, HTML included.
            item["kpi_id"] = kpi_id
            item["html_text"] = html_text
        items.append(item)

    # ① absolute P&L: one numbers-summary line mirroring the HTML KPI tile
    # (README anchor "Total P&L +$138,058 (realized $19k + unrealized $119k)");
    # partial or original-currency accounts keep the sentence fallbacks.
    total = _finite_number(overview.get("total_pnl"))
    realized = _finite_number(overview.get("realized"))
    unrealized = _finite_number(overview.get("unrealized"))
    if (display.get("currency") and total is not None and realized is not None
            and unrealized is not None and unrealized_is_measured(overview)
            and kpi_copy.get("pnl") and kpi_copy.get("pnl_sub")):
        sub = kpi_copy["pnl_sub"].format(realized=_display_money(realized, display),
                                         unrealized=_display_money(unrealized, display))
        # #344: this sentence is byte-for-byte what the pnl KPI tile already
        # shows (label + value + sub) — HTML omits it whenever that tile
        # rendered; Markdown (no tile grid) always keeps it.
        line("pnl", f"{kpi_copy['pnl']} {_display_money(total, display)}"
             + (f" ({sub})" if en else f"（{sub}）"), kpi_id="pnl")
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
            # #344: byte-for-byte the payoff KPI tile's own label+value+sub —
            # same HTML-omits/Markdown-keeps treatment as the pnl line above.
            line("payoff", f"{kpi_copy['payoff']} {float(payoff):.1f}"
                 + (_format_copy(payoff_copy.get("sub_wrap"), sub=sub) or ""),
                 kpi_id="payoff")
        else:
            line("payoff", _format_copy(payoff_copy.get("original_currency"),
                                        payoff=f"{payoff:.1f}"))
    pa = card.get("payoff_attribution") or {}
    cf = pa.get("counterfactual") or {}
    if cf.get("ticker"):
        after = "—" if cf.get("payoff") is None else f"{float(cf['payoff']):.1f}"
        drag = _display_money(cf.get("drag"), display)
        if drag is not None:
            line("drag", _format_copy(payoff_copy.get("drag_with_amount"),
                                      ticker=cf["ticker"], drag=drag, after=after))
        else:
            line("drag", _format_copy(payoff_copy.get("drag_plain"),
                                      ticker=cf["ticker"], after=after))
    # ② annualized return / account pillar (#179/#181): verbatim engine numbers;
    # a gated account level renders the unlock invitation, never the raw note.
    # Cash drag stays a neutral observation, never a verdict on holding cash.
    ap = card.get("acct_perf") or {}
    if ap.get("hold_twr") is not None:
        # #363: no day count here, and no second date range either. The card
        # states its window once, at the top (_period_span), and that window
        # now ends on the same price date this return is measured to — both
        # are px.index[-1]. A raw "1296-day window" made the reader convert a
        # duration into a period the card had already given them in dates.
        # #363 also renames this indicator (and the account-level one below)
        # "cumulative return": the engine field (hold_twr/acct_twr) and its
        # computation are unchanged, only the word a reader sees — "time-
        # weighted return" named the methodology, not what it measures, and
        # read as unexplained jargon. `irr_annual` below is a genuinely
        # different, already-annualized indicator and keeps its own wording.
        account_copy = copy.get("account_perf") or {}
        line("account_hold", account_copy["holdings_only"].format(value=_pct(ap.get("hold_twr"))))
        if ap.get("acct_twr") is not None:
            text = account_copy["account_base"].format(value=_pct(ap.get("acct_twr")))
            if ap.get("irr_annual") is not None:
                # Output contract: plain phrase, not the IRR jargon token.
                text += account_copy["annualized_suffix"].format(value=_pct(ap.get("irr_annual")))
            if ap.get("cash_drag") is not None:
                # #363: cash_drag = acct_twr − hold_twr is a difference of two
                # returns, so its unit is percentage points, never percent —
                # _pp(), not _pct() (output contract §5's "% means absolute
                # return, pp means excess"; this sentence used to render it as
                # "-33%" instead of "-33pp").
                text += account_copy["cash_drag_suffix"].format(value=_pp(ap.get("cash_drag")))
            line("account", text + account_copy["terminator"])
        elif ap.get("gate") or ap.get("note"):
            # #375 (contract §4: a gap note names the *actual* blocker): the
            # engine hands over a structured {status, data} reason and the
            # sentence comes from copy, so a locked account level says what is
            # actually blocking it. It used to print one hardcoded sentence
            # ("locked until cash has a complete anchor") for every blocker,
            # which told a user who had already supplied the anchor to go do
            # the thing they had just done. A bundle from before this contract
            # carries the old free-text `note` and no status, and falls back to
            # that same generic sentence rather than rendering nothing.
            gate_copy = copy.get("account_gate") or {}
            status = (ap.get("gate") or {}).get("status")
            text = gate_copy.get(status) or gate_copy.get("default") or ""
            if text:
                line("account_gate", text)
    cash_text = _cash_line_text(card, display, language)
    if cash_text:
        line("cash", cash_text)
    # ③ vs market: benchmark rows, the winning split, the alpha interval, then
    # the alternative comparators the HTML bars show (md keeps them as one line).
    # Monthly cadence (#284, contract §3): a prepare-time gate suppresses the
    # whole segment on later full reviews of the same month — the lines are
    # simply absent, and _performance_block skips the gap note too.
    if not vs_market_suppressed(card):
        ab = card.get("alpha_beta_breakdown") or {}
        benchmark_rows = _benchmark_rows(card)
        for market, bench, row in benchmark_rows:
            # #362/#363: everything this sentence still states — the excess and
            # β — is exactly what the excess tile carries, so HTML drops it
            # whole whenever that tile rendered. A mixed-market or month-gated
            # card has no excess tile, so it stands there, as it does on
            # Markdown.
            line("benchmark", _private_benchmark_line(market, bench, row, language),
                 market=market, kpi_id="excess", html_text="")
            for text in _private_split_lines(market, row, language):
                # No kpi_id: the allocation/selection split is the card's only
                # explanation of where the excess came from — nothing on the
                # tile grid carries it, so it survives dedup on every surface.
                line("split", text, market=market)
        if benchmark_rows:
            alpha_line = _alpha_interval_line(ab, language)
            if alpha_line:
                # #363: the 95% interval moved into the alpha tile's own sub
                # (_alpha_tile_sub) whenever that tile renders, so HTML no
                # longer needs this line to carry it. html_text now holds only
                # what the tile's sub still cannot — the not-yet-credible
                # legend and the #313 negative-interval caveat, each on its
                # own trigger, joined into one self-anchored standalone line
                # (_alpha_standalone_note) — or "" when neither fires, which
                # is the common case: a credible alpha with a wholly-positive
                # interval now prints nothing below the grid at all. Markdown
                # always gets the full sentence above — it has no tile to
                # carry any of this, and a card with no alpha tile this
                # period (mixed-market, month-gated) keeps the full sentence
                # on HTML too, via the same kpi_id/html_text mechanism.
                line("alpha", alpha_line, kpi_id="alpha",
                     html_text=_alpha_standalone_note(ab, language))
        attribution = _attribution_facts(card)
        if attribution:
            items.append({"kind": "attr_rows", "tag": None,
                          "text": " · ".join("vs " + row["label"] + " " + row["pp"]
                                             for row in attribution["rows"])})
    return items


def _performance_lines(card, language, honesty=None):
    """Legacy flat projection of the Block-1 performance cluster.

    Unit-test compatibility shim over ``_performance_items``; the card
    structure consumes the structured items directly. All honesty (minus the
    Block-2-hosted etf_metadata) is appended as footnote text so a triggered
    disclosure can never be dropped — every key lands here now, since caveats
    no longer ride individual indicator lines (2026-07-22 ruling, §4)."""
    honesty = dict(honesty) if honesty is not None else {}
    items = _performance_items(card, language)
    lines = [item["text"] for item in items if item["kind"] == "line"]
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


def _commitment_direction(goal, then_value, now_value):
    """Compare the two engine-owned readings against the stored rule goal."""
    if goal not in {"up", "down"}:
        return None
    try:
        if now_value == then_value:
            return "unchanged"
        better = now_value < then_value if goal == "down" else now_value > then_value
    except TypeError:
        return None
    return "improved" if better else "worsened"


def _condition_check_rows(bundle):
    return [row for row in bundle.get("condition_checks") or [] if isinstance(row, dict)]


def _condition_now(check, condition, copy):
    """What this period found for one condition, as one phrase — never a verdict
    on its own. ``None`` when the lookup did not succeed: an absent figure must
    read as absent, and a period with no evidence has nothing to put here."""
    if not check or check.get("lookup_status") != "ok":
        return None
    observation = check.get("observation") or {}
    if "value" in observation:
        return condition_value(observation["value"],
                              (condition.get("threshold") or {}).get("unit"))
    summary = str(observation.get("summary") or "").strip()
    return summary or None


def _condition_verdict_sentence(check, copy):
    """The engine's one-sentence read of a check's verdict of record.

    Copy-fallback only, like the breach sentence below it — it never consults
    ``narrative.honesty``, so it reaches the reader however the agent worded
    anything. A verdict the engine could not reach says so rather than going
    quiet: silence next to a then/now pair reads as "fine"."""
    check_copy = copy.get("condition_check") or {}
    return check_copy.get("verdict_" + str((check or {}).get("final_verdict") or "unknown"))


def _condition_reconciliation_line(prior, checks, copy, index):
    """The prior commitment's condition, then and now.

    A condition's then/now is the same shape a tracked metric already has, and
    for the same reason: what makes the loop visible is the pair, not the
    latest number. ``then`` is what was found when the user committed — the
    baseline the engine took in that same exchange — and ``now`` is what this
    period's check found. Returns ``None`` when nothing was checked this
    period, and the caller falls back to the bare statement: a condition with
    no fresh reading must not be dressed up as one that has one.

    The match is on **line**, never on ``slot_id``. A check names the live head
    of its line, and after a second revision that head is neither the slot the
    prior commitment recorded nor its line root — so a slot_id comparison went
    quiet exactly when the user's condition had the most history behind it. The
    engine resolves both sides (``review.py`` stamps ``line_id`` on the prior
    commitment and on every due entry); this reads them (external review,
    round 1)."""
    condition = prior.get("condition") or {}
    line_id = condition.get("line_id") or condition.get("slot_id")
    if not condition or not line_id:
        return None
    check = next((row for row in checks
                  if (index.get(row.get("slot_id")) or {}).get("line_id") == line_id), None)
    now = _condition_now(check, condition, copy)
    if now is None:
        return None
    check_copy = copy.get("condition_check") or {}
    if (condition.get("kind") or "numeric") == "event":
        line = _format_copy(check_copy.get("then_now_event"), rule=prior["rule"], now=now)
    else:
        then = condition_value((condition.get("baseline") or {}).get("value"),
                               (condition.get("threshold") or {}).get("unit"))
        template = "then_now" if then is not None else "then_now_no_baseline"
        line = _format_copy(check_copy.get(template), rule=prior["rule"],
                            then=then or "", now=now)
    if not line:
        return None
    verdict = _condition_verdict_sentence(check, copy)
    return f"{line} {verdict}" if verdict else line


# How many per-condition lines of each kind one card may carry. The record holds
# every check; the card holds the ones a reader can act on. Whatever is trimmed
# here is counted and stated by the summary line below — a card that quietly
# shows two of five readings is the same defect as a cap that does not say what
# it dropped, one level down (external review, round 1).
#
# The cap does not bind a row open on both axes at once (crossing
# unanswered/deferred *and* basis open) — see `_condition_trim_group`. Owner
# ruling, 2026-07-27: information completeness wins, so a dual-concern
# condition may never be trimmed off the card even though the summary already
# counted both of its concerns (#412 recorded followup, #438).
CONDITION_CARD_LINES = 2


def _condition_index(bundle):
    """``slot_id -> the condition it belongs to``, from the two places the engine
    stamps one.

    The plan's due list is where the engine already resolved every line
    identity, so the card reads ``line_id`` rather than deriving it. That is the
    point of the field: matching a check to a commitment through ``slot_id``
    alone broke silently after a second revision, because the new check names
    the newest slot while the prior commitment still names the one before it.

    But that list is **capped**, and a check for a line beyond the cap is legal
    — so joining on it alone dropped those readings off the card entirely while
    the summary still counted them as checked (external review, round 2).
    ``condition_slots_context`` carries exactly those, stamped by the engine
    from the same derivation. Two engine-stamped sources, one merge, and still
    nothing worked out here: a retired line appears in neither, which is how the
    renderer knows to leave it to the retirement sentence."""
    snapshot = ((bundle.get("review_plan") or {}).get("state_snapshot") or {})
    index = {}
    for source in (snapshot.get("condition_slots_due") or [],
                   bundle.get("condition_slots_context") or []):
        for entry in source:
            if isinstance(entry, dict) and entry.get("slot_id"):
                index[entry["slot_id"]] = entry
    return index


def _queued_crossings(bundle):
    """The condition lines this review put a crossing question *to the queue* for.

    Deliberately not the silence condition — being asked is not being answered
    (external review, round 2). This only separates two different facts once a
    line is already known to be unresolved: "it lost the one-question budget"
    and "it was asked and nobody answered" are different things to tell a user,
    and each gets its own sentence."""
    return {question.get("line_id")
            for question in ((bundle.get("review_plan") or {}).get("question_queue") or [])
            if isinstance(question, dict) and question.get("kind") == "condition_crossing"}


def _condition_reading(check, condition):
    """This period's figure or fact for one condition, as the card prints it."""
    observation = check.get("observation") or {}
    if "value" in observation:
        return condition_value(observation.get("value"),
                               (condition.get("threshold") or {}).get("unit"))
    return str(observation.get("summary") or "").strip() or None


def thesis_guard_sentence(condition, copy):
    """The thesis a condition guards, said wherever that condition speaks.

    #416's ratified direction made a thesis falsifier a condition slot. The card
    has no thesis block, so without this the adjudication would arrive detached
    from the claim it settles — a reading about "the CEO leaving" with nothing
    saying which position that was ever a reason to hold.

    Read from ``thesis_link``, which ``review.py`` stamps on the plan's own due
    entry. The renderer never works out which thesis a slot belongs to: one
    place derives that fact, every other surface reads the stamped value."""
    link = (condition or {}).get("thesis_link")
    ticker = (link or {}).get("ticker") if isinstance(link, dict) else None
    if not ticker:
        return None
    return _format_copy((copy.get("condition_check") or {}).get("thesis_guard"),
                        ticker=ticker)


def _condition_reading_line(check, condition, copy, notes):
    """One reading, plus every sentence that says where it stands.

    The reading and the status sentences are separate copy keys on purpose. The
    figure prints identically whatever its disposition, so only the notes vary,
    and — because a single check can be unresolved on two independent axes at
    once — a row can carry more than one. No combination needs its own
    template."""
    reading = _condition_reading(check, condition)
    if reading is None:
        return None
    observation = check.get("observation") or {}
    source, as_of = observation.get("source"), observation.get("as_of")
    check_copy = copy.get("condition_check") or {}
    template = "fact" if (source and as_of) else "fact_no_source"
    line = _format_copy(check_copy.get(template), criterion=condition.get("criterion") or "",
                        value=reading, source=source, as_of=as_of)
    if not line:
        return None
    guard = thesis_guard_sentence(condition, copy)
    if guard:
        # Before the status sentences: what this condition is for comes ahead of
        # what is happening to it (#416 C2).
        line = f"{line} {guard}"
    for note_key, values in notes:
        note = _format_copy(check_copy.get(note_key), **values)
        if note:
            line = f"{line} {note}"
    return line


# A check row's two dispositions. They are **independent axes**, not competing
# values of one field, because a single row can genuinely carry both facts:
# `build_check` writes `basis_alert` and `engine_verdict` on the same row, and
# `_condition_questions` emits a `condition_basis` *and* a `condition_crossing`
# for that line. The round-2 cut ran them through one single-valued if-chain, so
# whichever matched first won and the other fact was neither printed nor counted
# — a user could answer the crossing, skip the basis question, and never hear
# again that the measurement may have moved underneath the line they just
# confirmed (external review, round 3).
#
#   crossing axis   None (not a candidate) | settled | unanswered | deferred
#   basis axis      None (no alert)        | open    | resolved
CONDITION_CROSSING_STATES = (None, "settled", "unanswered", "deferred")
CONDITION_BASIS_STATES = (None, "open", "resolved")
# The states that mean "this review did not close this concern". A row can be
# open on both axes at once, and that is two concerns, not one.
CONDITION_OPEN_CROSSING = ("unanswered", "deferred")


def _condition_outcome(check, condition, queued):
    """``(crossing_state, basis_state)`` for one successful check.

    ``settled`` means the user actually answered — **not** that a question was
    queued. A queued question that ended in a skip, or one an interrupted host
    never delivered, leaves the row with no ``user_response`` at all; treating
    "asked" as "answered" put the crossed line straight back into silence
    (external review, round 2).

    The basis axis reads the same way and for the same reason: a raised concern
    with no ``basis_resolution`` was never settled, so a card that prints its
    reading as an ordinary all-clear fact has quietly dropped the doubt."""
    # Candidacy comes from the engine's own read, never from `final_verdict`:
    # an override moves the verdict of record to `not_met` while leaving the
    # engine's finding intact, and that check must stay quiet rather than
    # reappearing as an all-clear reading.
    if not (check.get("engine_verdict") in ("met", "near_line")
            or bool(check.get("event_alert"))):
        crossing_state = None
    elif check.get("user_response"):
        crossing_state = "settled"
    elif condition.get("line_id") in queued:
        crossing_state = "unanswered"
    else:
        crossing_state = "deferred"

    if not check.get("basis_alert"):
        basis_state = None
    elif check.get("basis_resolution"):
        basis_state = "resolved"
    else:
        basis_state = "open"
    return crossing_state, basis_state


def _condition_notes(check, condition, crossing_state, basis_state):
    """The status sentences one reading carries, in fixed order: what the line
    itself is doing, then what may be wrong with how it is measured."""
    event = (condition.get("kind") or "numeric") == "event"
    notes = []
    if crossing_state in CONDITION_OPEN_CROSSING:
        suffix = "_event" if event else ""
        notes.append((f"note_{crossing_state}{suffix}", {}))
    if basis_state == "open":
        notes.append(("note_basis_open",
                      {"note": (check.get("basis_alert") or {}).get("note") or ""}))
    return notes


def _condition_open_concerns(crossing_state, basis_state):
    """How many separate things this row leaves unresolved — zero, one, or two."""
    return int(crossing_state in CONDITION_OPEN_CROSSING) + int(basis_state == "open")


def _condition_outcomes(bundle, checks):
    """``[(check, condition, status, crossing_state, basis_state)]`` per readable check.

    One classification, read by both the card lines and the summary count."""
    index = _condition_index(bundle)
    queued = _queued_crossings(bundle)
    out = []
    for check in checks:
        condition = index.get(check.get("slot_id"))
        if not condition:
            continue
        status = check.get("lookup_status")
        if status == "failed":
            out.append((check, condition, "failed", None, None))
        elif status == "ok":
            crossing_state, basis_state = _condition_outcome(check, condition, queued)
            out.append((check, condition, "ok", crossing_state, basis_state))
    return out


def _condition_trim_group(rows):
    """Keep every dual-open row plus the first ``CONDITION_CARD_LINES`` of the
    rest, in the group's original order.

    ``rows`` is ``[(line, dual_open), ...]``. A dual-open row (open on both
    the crossing and the basis axis — see ``_condition_reading_lines``) is
    never trimmed, by owner ruling (2026-07-27): information completeness
    wins, so a condition carrying two live concerns must never disappear from
    the card while the summary still counts both. A non-dual row's position
    in the kept set is unaffected — the cap still keeps only the first
    ``CONDITION_CARD_LINES`` of them, exactly as before this exemption
    existed."""
    kept = []
    regular_kept = 0
    for line, dual_open in rows:
        if dual_open or regular_kept < CONDITION_CARD_LINES:
            kept.append(line)
            if not dual_open:
                regular_kept += 1
    return kept


def _condition_reading_lines(bundle, checks, copy):
    """``(lines, shown, describable)`` — every per-condition line this card carries.

    Four groups, in the order a reader needs them:

    0. **Retirements** — a thesis condition that stopped being checked this
       period because its position was fully exited. Said once, in the review
       where it happens.
    1. **Unresolved crossings** — a line the engine read as crossed (or an event
       the agent flagged) that the user has not answered on. Either it lost the
       one-question budget, or it was asked and got no answer; the two say so
       differently, because they are different facts. Before this existed a
       crossed line could go completely unmentioned, which is the single worst
       outcome this whole tier exists to prevent.
    2. **Open basis concerns** — the agent said the measurement may have
       changed and nobody settled it. Printing its reading as an ordinary
       all-clear fact would drop the doubt exactly the way a silent crossing
       dropped the crossing.
    3. **Readings clear of the line**, with one non-blocking "if a figure looks
       wrong, say so" for the group — showing the figure is how a wrong basis
       exposes itself, and that only helps if the user is invited to say so.
    4. **Failed lookups**, stated plainly with their reason.

    A row is grouped by its most urgent open fact but **prints every one it
    has**: an unresolved crossing whose basis is also in doubt carries both
    sentences on one reading, because the two are independent and dropping
    either loses something the user needs (external review, round 3).

    A check the user settled on both axes renders nothing: the exchange told
    that story. ``not_checked`` rows are the summary's business — eight
    apologies would bury the one lookup that actually broke.

    A row open on both axes at once is exempt from the per-kind cap below
    (`_condition_trim_group`) and always renders, however many are due —
    owner ruling, 2026-07-27, on the #438 recorded followup: trimming a
    dual-concern condition off the card is an information-completeness loss
    the disclosure sentence cannot repair, because a reader who reads only
    the lines would never see that condition at all.
    """
    check_copy = copy.get("condition_check") or {}
    groups = {"unanswered": [], "deferred": [], "basis_open": [], "fact": [], "blind": [],
              "retired": []}
    # #416 C2: the thesis conditions that stopped being checked *this* period,
    # because their position was fully exited. An event, so it is said once and
    # the plan is empty of it on every later review — the engine decides which
    # period that is (`_condition_lines`), never this. Without it a user who
    # watched a falsifier last week sees it vanish, which reads as the system
    # having quietly stopped rather than as a deliberate retirement.
    for entry in (((bundle.get("review_plan") or {}).get("state_snapshot") or {})
                  .get("condition_slots_retired") or []):
        if not isinstance(entry, dict) or not entry.get("ticker"):
            continue
        line = _format_copy(check_copy.get("thesis_retired"),
                            criterion=entry.get("criterion") or "", ticker=entry["ticker"])
        if line:
            groups["retired"].append((line, False))
    for check, condition, status, crossing_state, basis_state in _condition_outcomes(
            bundle, checks):
        if status == "failed":
            criterion = condition.get("criterion") or ""
            reason = str(check.get("reason") or "").strip()
            line = (_format_copy(check_copy.get("blind_with_reason"),
                                 criterion=criterion, reason=reason) if reason
                    else _format_copy(check_copy.get("blind"), criterion=criterion))
            if line:
                guard = thesis_guard_sentence(condition, copy)
                groups["blind"].append((f"{line} {guard}" if guard else line, False))
            continue
        notes = _condition_notes(check, condition, crossing_state, basis_state)
        if notes:
            # Grouped by the crossing axis when it is open, else by the basis
            # concern — but the line itself carries both notes either way.
            group = (crossing_state if crossing_state in CONDITION_OPEN_CROSSING
                     else "basis_open")
        elif crossing_state is None and check.get("final_verdict") == "not_met":
            group = "fact"
        else:
            continue                      # settled on every axis; nothing to say
        line = _condition_reading_line(check, condition, copy, notes)
        if line:
            dual_open = (crossing_state in CONDITION_OPEN_CROSSING and basis_state == "open")
            groups[group].append((line, dual_open))
    describable = sum(len(rows) for rows in groups.values())
    kept = {kind: _condition_trim_group(rows) for kind, rows in groups.items()}
    shown = sum(len(rows) for rows in kept.values())
    lines = kept["unanswered"] + kept["deferred"] + kept["basis_open"]
    if kept["fact"]:
        lines += kept["fact"]
        looks_wrong = check_copy.get("fact_looks_wrong")
        if looks_wrong:
            lines.append(looks_wrong)
    lines += kept["blind"]
    lines += kept["retired"]
    return lines, shown, describable


def _condition_summary_line(bundle, checks, copy, shown, describable):
    """How much of the record this review looked at, and how much of that is
    on this card.

    Two ways a card can be incomplete, and it must speak for either. **Not
    settled**: a failed lookup, one nobody ran, a line the plan's cap held
    back (#434), a crossing the user has not answered on, or a basis concern
    nobody resolved — none of those is closed, so none may pass as fine. **Not
    shown**: the per-kind line caps trimmed a reading that was taken. A card
    that quietly prints two of five readings is making the same claim of
    completeness the cap disclosure exists to prevent (external review,
    round 1). A row open on both axes is exempt from that cap
    (`_condition_trim_group`) and is therefore always counted as *shown*,
    never as trimmed — the #412 recorded followup this closes.

    The unsettled count comes from ``_condition_outcomes`` — the same
    classification the lines above render — so the card and its own summary
    cannot disagree about what was left open (external review, round 2).

    It counts **concerns, not rows**: a check whose crossing is unanswered
    *and* whose basis is in doubt left two separate things open, and a count
    that said "1" there would be claiming something its own card contradicts
    (external review, round 3). The copy says "concerns" so the number matches
    what it names.

    Silent only when everything was checked, settled, and printed."""
    summary = (((bundle.get("review_plan") or {}).get("state_snapshot") or {})
               .get("condition_slots_summary") or {})
    total = _finite_number(summary.get("lines_total"))
    if total is None or total <= 0:
        return None
    total = int(total)
    checked = sum(1 for row in checks if row.get("lookup_status") == "ok")
    unsettled = sum(_condition_open_concerns(crossing_state, basis_state)
                    for _check, _condition, _status, crossing_state, basis_state
                    in _condition_outcomes(bundle, checks))
    # "Open" is anything this review did not close: never looked at, looked at
    # and failed, held back by the cap, or checked and left unresolved.
    open_count = max(0, total - checked) + unsettled
    trimmed = max(0, describable - shown)
    if open_count <= 0 and trimmed <= 0:
        return None
    # Composed from single-purpose sentences rather than one template per
    # combination: the two incompletenesses are independent, and a combined
    # string has to say "0 still open" in the case where only trimming happened.
    check_copy = copy.get("condition_check") or {}
    parts = [_format_copy(check_copy.get("summary_checked"), checked=checked, total=total)]
    if open_count > 0:
        parts.append(_format_copy(check_copy.get("summary_open"), deferred=open_count))
    if trimmed > 0:
        parts.append(_format_copy(check_copy.get("summary_trimmed"),
                                  shown=shown, describable=describable))
    parts = [part for part in parts if part]
    return " ".join(parts) or None


def _reconciliation_lines(bundle, language):
    """#151/#152 loop anchor: open the card against last time's commitment.

    Prints the committed rule plus the metric's then/now values verbatim from
    engine state — the renderer never computes a delta, and the agent never
    touches the numbers.

    #292: when the engine's honesty_ledger carries a `prior_commitment_breach`
    entry (review.py's `_flag_prior_commitment_breach` matched this exact rule
    against this period's draft problem_events), one more sentence is appended
    stating the breach as fact. That sentence is copy-fallback only — same
    provenance as the then/now numbers above it — and never reads
    narrative.honesty, so it is guaranteed to reach the reader regardless of
    how the agent's separately-required honesty sentence turns out to be
    worded or where it lands on the card."""
    copy = load_copy(language)
    checks = _condition_check_rows(bundle)
    prior = ((bundle.get("review_plan") or {}).get("state_snapshot") or {}).get("prior_commitment") or {}
    lines = []
    if prior.get("rule"):
        key = prior.get("metric_key")
        then_v = prior.get("metric_value")
        now_v = ((bundle.get("engine_state") or {}).get("metrics") or {}).get(key) if key else None
        recon_copy = copy.get("reconciliation") or {}
        # #412: a commitment anchored to a condition reconciles the same way a
        # metric-anchored one does — the baseline taken when it was written
        # against what this period's check found. It degrades to the bare
        # statement when nothing was checked, rather than borrowing the
        # metric branch's shape for numbers it does not have.
        condition_line = _condition_reconciliation_line(prior, checks, copy,
                                                        _condition_index(bundle))
        if condition_line:
            line = condition_line
        elif then_v is not None and now_v is not None:
            metric = (recon_copy.get("metric_labels") or {}).get(key)
            direction = _commitment_direction(prior.get("goal"), then_v, now_v)
            if metric and direction:
                line = _format_copy(recon_copy.get("statement_with_metric"), rule=prior["rule"],
                                    metric=metric, then=_metric_display(key, then_v),
                                    now=_metric_display(key, now_v),
                                    direction=(recon_copy.get("direction") or {}).get(direction))
            else:
                # Legacy commitments can carry recorded readings without the
                # engine-owned goal or a known localized metric label. Those
                # facts do not establish what the quantity means or which
                # direction is better, so preserve only the commitment rather
                # than inventing either semantic (or formatting a newer copy
                # template with missing fields).
                line = _format_copy(recon_copy.get("statement"), rule=prior["rule"])
        else:
            line = recon_copy["statement"].format(rule=prior["rule"])
        breached = any(entry.get("key") == "prior_commitment_breach"
                       for entry in (bundle.get("engine_card") or {}).get("honesty_ledger") or [])
        if breached:
            fallback = (copy.get("honesty") or {}).get("prior_commitment_breach")
            if fallback:
                line += f" {fallback}"
        lines.append(line)
    # The standing conditions from earlier reviews, in the same breath as the
    # prior commitment: this whole block answers one question — what happened to
    # the things you asked to be watched. The summary reads what the lines
    # above actually printed, so trimming can never pass as completeness.
    reading_lines, shown, describable = _condition_reading_lines(bundle, checks, copy)
    lines += reading_lines
    summary = _condition_summary_line(bundle, checks, copy, shown, describable)
    if summary:
        lines.append(summary)
    return lines


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
        milestone_copy = load_copy(language).get("review_milestone") or {}
        noun = milestone_copy.get("noun_one" if completed == 1 else "noun_many")
        milestone = _format_copy(milestone_copy.get("line"),
                                 completed=completed, noun=noun or "")
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
    """Render only facts supported by an opening position snapshot.

    #316: the out-of-scope disclosure (which history-only dimensions this
    review cannot score) is not repeated here. ``snapshot_scope`` is always
    present on a snapshot card's honesty ledger, so it already collapses into
    the Block-1 footnote (contract §4: a caveat with no host number goes to
    the footnote, never a second copy in the opening). Stating it again in
    this function's prose would double the same sentence and crowd out the
    structural facts this review actually establishes.
    """
    summary = _snapshot_summary(card)
    overview_copy = (copy.get("snapshot") or {}).get("overview") or {}
    positions_n = _finite_number(summary.get("positions_n"))
    positions = (str(int(positions_n)) if positions_n is not None
                 and positions_n.is_integer() else None)
    as_of = summary.get("as_of")
    basis = summary.get("valuation_basis")
    weights_available = summary.get("weights_available") is True

    subject = (overview_copy["subject_with_count"].format(positions=positions)
               if positions is not None else overview_copy["subject_generic"])
    opening = (overview_copy["opening_as_of"].format(subject=subject, as_of=as_of) if as_of
               else overview_copy["opening"].format(subject=subject))
    if weights_available and basis == "market_value":
        valuation = overview_copy["valuation_market_value"]
    elif weights_available and basis == "cost":
        valuation = overview_copy["valuation_cost"]
    else:
        valuation = overview_copy["valuation_unavailable"]

    integrity = []
    missing_avg_cost = summary.get("missing_avg_cost") or []
    fx_gaps = summary.get("fx_gaps") or []
    if isinstance(missing_avg_cost, list) and missing_avg_cost:
        tickers = ", ".join(str(x) for x in missing_avg_cost)
        integrity.append(overview_copy["missing_avg_cost"].format(tickers=tickers))
    if isinstance(fx_gaps, list) and fx_gaps:
        currencies = ", ".join(str(x) for x in fx_gaps)
        integrity.append(overview_copy["missing_fx"].format(currencies=currencies))
    return [opening, valuation] + integrity


def _snapshot_strength_line(card, language):
    """What this opening check established, in one sentence.

    Two states, and completeness is not one of them (#549). Until then a third
    branch praised a "complete structural baseline" whenever the agent had left
    `is_complete` unset — a claim about an external account the product does not
    model, and the same flag whose `false` value silently stopped a user's book
    from ever updating. What remains is what the engine can actually see: either
    the supplied facts supported weights, or they did not.
    """
    summary = _snapshot_summary(card)
    weighted = summary.get("weights_available") is True
    strength_copy = (load_copy(language).get("snapshot") or {}).get("strength") or {}
    return strength_copy.get("weighted" if weighted else "baseline", "")


def _snapshot_hole_lines(card, language):
    """Structural risk narrative(s) for the [X] panel.

    #316: a position snapshot can score exactly two structural dimensions
    (position sizing, diversification). Both ride the panel when the engine
    flagged both as a concern instead of one silently displacing the other —
    structure is the whole of what a snapshot review can diagnose, so neither
    finding should read as a dropped leftover. Returns a list of one or more
    lines (never empty)."""
    summary = _snapshot_summary(card)
    holes = _applicable_holes(card)
    hole_copy = (load_copy(language).get("snapshot") or {}).get("holes") or {}
    if summary.get("weights_available") is True:
        lines = []
        for hole in holes:
            if not isinstance(hole, dict):
                continue
            raw = hole.get("raw") or {}
            dim_id = dimension_id(raw.get("dim")) if raw.get("dim") else None
            # A position snapshot can support only structural dimensions. Never let
            # an accidentally carried history dimension become a snapshot claim.
            if dim_id not in {"position_sizing", "diversification"}:
                continue
            line = _hole_line(hole, language)
            if not line:
                label = localized_dimension(dim_id, language)
                line = hole_copy.get("leading_risk", "").format(label=label)
            lines.append(line)
        if lines:
            return lines
        # Weights were available but neither structural dimension triggered —
        # a clean structural read is itself the finding, not a data gap.
        return [hole_copy.get("clean_structure", "")]
    return [hole_copy.get("no_weights", "")]


def _signed_pct(value, digits=1):
    return "—" if value is None else f"{float(value) * 100:+.{digits}f}%"


def _signed_pp(value, digits=1):
    return "—" if value is None else f"{float(value) * 100:+.{digits}f} pp"


def _is_after(candidate, reference):
    """True when both are parseable ISO dates and ``candidate`` is the later
    one. Fail-closed: an unparseable or malformed value answers False, so a
    bad date can never push a rendered window past the engine's own."""
    try:
        return (dt.date.fromisoformat(str(candidate))
                > dt.date.fromisoformat(str(reference)))
    except (TypeError, ValueError):
        return False


def _period_span(bundle, copy):
    """The review window on its own (contract §2): which stretch of the user's
    history this card covers.

    Owner ruling 2026-07-22: this belongs at the very top of the card, in the
    keynote preamble. It scopes the entire card — every number below is "over
    this window" — so it is card-level metadata, not a property of any one
    indicator. It previously rode the excess KPI tile's sub line together with
    the market backdrop (#344), which pushed that one cell to roughly three
    times the text of its neighbours and, under the grid's default row
    stretch, padded the whole tile row. Split from ``_market_backdrop`` so
    each lands where it belongs.

    Owner ruling 2026-07-23 (#363): one range, and it ends where the card's
    numbers actually end. ``engine_state.date_end`` is the last *trade* date,
    but every unrealized figure on the card — market value, exposure, the
    drawdown scenario, the holdings-only return — is priced as of the latest
    close the engine retrieved (``price_snapshot.as_of``, the same
    ``px.index[-1]`` that ``acct_perf.window.end`` comes from). For anyone who
    has not traded recently the two differ by months, and labelling the card
    with the trade dates alone understated the stretch its numbers cover. So
    the span runs to the price date whenever that is later. This is a
    display-layer extension only: ``date_end`` keeps its own meaning
    everywhere it is load-bearing (the vs-market month gate, session override
    order), and a card with no retrieved prices renders exactly as before."""
    state = bundle.get("engine_state") or {}
    context = (((bundle.get("review_plan") or {}).get("state_snapshot") or {})
               .get("market_context") or {})
    period_copy = copy.get("period") or {}
    start = state.get("date_start") or context.get("start")
    end = state.get("date_end") or context.get("end")
    as_of = (state.get("price_snapshot") or {}).get("as_of")
    if end and as_of and _is_after(as_of, end):
        end = as_of
    try:
        if start and end and period_copy.get("span"):
            return period_copy["span"].format(start=start, end=end)
        if end and period_copy.get("as_of"):
            return period_copy["as_of"].format(end=end)
    except (KeyError, IndexError, ValueError):
        return None
    return None


def _market_backdrop(bundle, copy):
    """The demoted market indicator — VIX — that qualifies the
    excess-versus-market reading.

    Unlike the review span this IS a property of the benchmark comparison, so
    it stays with the excess KPI tile on HTML (and holds Block 1's line on
    Markdown, which has no tile grid). Returns None when it is unavailable.

    The primary benchmark's window return used to lead this line. Owner ruling
    2026-07-23 removed it (#366): it was the only period-scoped figure on a
    card whose personal numbers are all cumulative since inception
    (``overview_stats`` sums every round trip ever plus every current
    holding), so the reader had nothing to compare it against — and after #344
    put it in the excess tile's sub, a window-scoped return sat directly under
    a whole-history excess value, with both labelled "same period". Its stated
    second job, qualifying motive reads, was never wired either: nothing in
    question generation consumes ``market_context``. #366 records the one
    condition that earns it back — a period-local section for it to sit
    beside. A volatility *level* carries neither defect, so VIX stays."""
    context = (((bundle.get("review_plan") or {}).get("state_snapshot") or {})
               .get("market_context") or {})
    period_copy = copy.get("period") or {}
    pieces = []
    benchmarks = context.get("benchmarks") or {}
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
    horizon_copy = copy.get("horizon_entries") or {}
    entries = []
    for marker in markers:
        kind = marker.get("kind")
        if kind not in ("exit_too_fast", "held_too_long"):
            continue
        ticker = marker.get("ticker") or horizon_copy.get("ticker_default")
        horizon_label = labels.get(marker.get("horizon"), marker.get("horizon"))
        inferred = marker.get("maturity") == "inferred"
        voice = horizon_copy.get("voice_inferred" if inferred else "voice_recorded")
        line = _format_copy(horizon_copy.get(kind), ticker=ticker or "",
                            voice=voice or "", horizon=horizon_label,
                            days=marker.get("holding_days"))
        if line:
            entries.append((marker.get("ticker"), line))
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
    text = copy.get("exit_followup") or {}
    as_of = text["as_of"].format(as_of=price_as_of) if price_as_of else ""
    frozen_note = text["frozen_note"].format(as_of=price_as_of) if price_as_of else ""
    pairs = []
    lines = []
    for event in bundle.get("revisit_resolutions") or []:
        question = questions.get((event.get("revisit_id"), str(event.get("checkpoint"))))
        if not question:
            continue
        line = text["check"].format(
            ticker=question.get("ticker") or text["position_fallback"],
            checkpoint=event.get("checkpoint"),
            label=due_labels.get(event.get("status"), event.get("status")))
        if event.get("note"):
            line += text["note"].format(note=event["note"])
        compare = question.get("compare") or {}
        needs = compare.get("needs_prices") or []
        if needs:
            line += text["needs_prices"].format(missing=", ".join(needs), when=as_of)
        elif compare.get("swap_net_pp") is not None:
            swaps = ", ".join(sorted({row.get("ticker") for row in question.get("swaps") or []
                                      if row.get("ticker")})) or text["replacement_fallback"]
            # The lead is locale-owned in both directions: with no frozen date
            # en contributes nothing and zh contributes a space, because their
            # templates join to the following clause differently.
            lead = (text["frozen_lead"].format(as_of=price_as_of) if price_as_of
                    else text["frozen_lead_absent"])
            line += text["swap"].format(
                lead=lead, orig=_signed_pct(compare.get("orig_ret")), swaps=swaps,
                swap=_signed_pct(compare.get("swap_ret")),
                net=_signed_pp(compare.get("swap_net_pp")))
        elif compare.get("idle_cash") and compare.get("orig_ret") is not None:
            line += text["idle"].format(orig=_signed_pct(compare.get("orig_ret")),
                                        when=frozen_note)
        pairs.append((question.get("ticker"), line))
    backlog = (((plan.get("state_snapshot") or {}).get("exit_backlog")) or {})
    summary = backlog.get("summary") or {}
    if summary.get("count"):
        top = ", ".join(f"{ticker} ×{count}" for ticker, count in summary.get("top_tickers") or [])
        span = summary.get("span") or {}
        line = text["backlog"].format(count=summary.get("count"), full=summary.get("full"),
                                      reduce=summary.get("reduce"))
        if span.get("first") and span.get("last"):
            line += text["backlog_span"].format(first=span["first"], last=span["last"])
        if top:
            line += text["backlog_top"].format(top=top)
        line += text["sentence_end"]
        if summary.get("priced"):
            line += text["backlog_priced"].format(
                priced=summary.get("priced"),
                avg=_signed_pp(summary.get("avg_hindsight_pp")),
                rose=summary.get("sold_before_rise"))
        lines.append(line)
        for item in (backlog.get("items") or [])[:2]:
            detail = text["focus"].format(
                ticker=item.get("ticker") or text["position_fallback"],
                action=text["action_full"] if item.get("kind") == "full" else text["action_reduce"],
                exit_date=item.get("exit_date"))
            compare = item.get("compare") or {}
            needs = compare.get("needs_prices") or []
            # #670: the magnitude printed here is `impact`, the same figure that
            # ordered this list — layout-constraints.md §6 ruling 3 ("everything
            # ranks by size of money impact, never by percentage return"). The
            # rates it replaced could not explain the ranking, and priced a trim
            # as if the whole position had gone; `impact` is notional x net move,
            # so a small parcel of a large mover reports a small figure without
            # the line ever having to explain the fraction. Engine-computed
            # (revisit.py), never derived here (design-guidelines.md §5).
            impact = item.get("impact")
            if needs:
                detail += text["focus_needs_prices"].format(
                    missing=", ".join(needs), when=as_of)
            elif impact is not None:
                detail += text["focus_impact"].format(
                    impact=_money(impact, item.get("currency") or "USD"), when=frozen_note)
            elif compare.get("orig_ret") is not None:
                detail += text["focus_orig"].format(
                    orig=_signed_pct(compare.get("orig_ret")), when=frozen_note)
            lines.append(detail)
    return pairs, lines


def _problem_lines(bundle, copy):
    stats = ((((bundle.get("review_plan") or {}).get("state_snapshot") or {})
             .get("problem_stats")) or {})
    if not stats:
        return []
    text = copy.get("problems") or {}
    names = copy.get("problem_keys") or {}
    trends = copy.get("trends") or {}
    lines = []
    for key in (stats.get("top") or [])[:3]:
        row = (stats.get("per_key") or {}).get(key) or {}
        lines.append(text["trend_line"].format(
            name=names.get(key, key.replace("_", " ")),
            recent=row.get("recent_count", 0), prev=row.get("prev_count", 0),
            trend=trends.get(row.get("trend"), row.get("trend"))))
    decisions = copy.get("rule_breach_decisions") or {}
    decided_rules = set()
    for event in bundle.get("rule_breach_decisions") or []:
        decided_rules.add(event.get("rule_id"))
        line = text["breach_decision"].format(
            rule=event.get("rule_text") or event.get("rule_id"),
            label=decisions.get(event.get("decision"), event.get("decision")))
        if event.get("note"):
            line += text["breach_note"].format(note=event["note"])
        lines.append(line)
    for rule in stats.get("rules_check") or []:
        if (rule.get("rule_id") not in decided_rules and rule.get("verdict") == "held"
                and int(rule.get("held_streak") or 0) == 1):
            lines.append(text["rule_held"].format(
                rule=rule.get("text") or rule.get("rule_id")))
    return lines


# ── Rich-layout facts (#247) ────────────────────────────────────────────
# Structured presentation facts consumed by BOTH surfaces: render_private
# writes them as text lines, render_html as the card-template.html layout
# (KPI grid, ranked instrument bars, stress row, attribution bars, improve
# rows).  The facts layer only formats engine-owned numbers through the same
# helpers the Markdown card uses — it never computes one.  A missing engine
# field drops its tile/row instead of inventing a value, so degraded cards
# (snapshot, insufficient, fx gaps) keep today's plainer shape.


def kpi_tile_plan(card):
    """Which headline tiles a card earns — as a locale-neutral decision.

    This is the judgment half of the KPI grid, split out of ``_kpi_tiles``
    so that *what the card says* stops being decided in the same breath as
    *how it reads in one language*. Nothing here loads copy, takes a
    ``language``, or formats a number: every tile carries its raw engine
    value plus the copy KEY its slot should resolve to, and the presentation
    half below turns that into text for one locale.

    Why it matters beyond tidiness: with the two halves fused, adding a
    locale or restyling the grid both meant editing this logic, and a wording
    change could not be made without touching engine code (measured at
    P(engine | copy) = 0.96 by ``tools/change_surface.py``). Split, the plan
    is the thing tests and future non-card surfaces can consume directly.

    Slot vocabulary follows the metric-cell contract in
    docs/design-guidelines.md §2 — ``label`` / ``value`` / ``sub``, where the
    value slot holds exactly one figure and the sub slot at most one
    qualifying line. A ``fmt`` names the formatter the presentation half must
    apply; a ``sub`` names a copy key plus the raw params it interpolates.

    Tiles are *candidates*: a slot whose value cannot be formatted in the
    active display context (no currency, no FX factor) is dropped by the
    presentation half, not predicted here. That keeps data availability — an
    engine fact — separate from display capability, which is not.
    """
    overview = card.get("overview") or {}
    tiles = []

    total = _finite_number(overview.get("total_pnl"))
    if total is not None:
        # #382: the tile's sub -- unlike its value -- has room for roughly
        # one figure per wrapped line, so a 5-6 digit realized/unrealized
        # pair goes through the compact ($119k) formatter even though the
        # headline value stays full precision. Markdown's mirror of this same
        # sub (_performance_items) has no such width limit and keeps full
        # precision, via plain money.
        # An unmeasured unrealized leg is an ABSENT slot (``None``), not a
        # slot holding ``None``. The distinction is load-bearing: a present
        # slot whose value is missing still formats as an em dash, which is
        # how a measured-but-empty figure reads, whereas an absent slot
        # resolves to nothing and collapses the whole sub. Printing
        # "— unrealized" would state a measurement that was never taken.
        # The key stays in the dict either way — dropping it would break the
        # template outright instead of degrading.
        sub_params = {
            "realized": {"fmt": "money_compact",
                         "value": _finite_number(overview.get("realized"))},
            "unrealized": ({"fmt": "money_compact",
                            "value": _finite_number(overview.get("unrealized"))}
                           if unrealized_is_measured(overview) else None),
        }
        tiles.append({"id": "pnl", "label_key": "pnl",
                      "value": {"fmt": "money", "value": total},
                      "tone": "neg" if total < 0 else "pos",
                      "sub": {"key": "pnl_sub", "params": sub_params}})

    payoff = _finite_number(overview.get("payoff"))
    if payoff is not None:
        tiles.append({"id": "payoff", "label_key": "payoff",
                      "value": {"fmt": "ratio1", "value": payoff}, "tone": None,
                      "sub": {"key": "payoff_sub", "params": {
                          "win": {"fmt": "money", "value": _finite_number(overview.get("avg_win"))},
                          "loss": {"fmt": "money_abs",
                                   "value": _finite_number(overview.get("avg_loss"))}}}})

    ab = card.get("alpha_beta_breakdown") or {}
    # Mixed-market cards keep their per-market text rows (#205); a synthetic
    # top-level figure would recreate the total-alpha the engine refuses.
    # The excess and alpha tiles belong to the month-gated vs-market segment
    # (#284): on a gated review they disappear with the rest of ③.
    single_scope = not ab.get("by_market") and not vs_market_suppressed(card)

    excess = _finite_number(ab.get("excess_vs_spy")) if single_scope else None
    if excess is not None:
        tiles.append({"id": "excess", "label_key": "excess",
                      "value": {"fmt": "pp", "value": excess},
                      "tone": "neg" if excess < 0 else "pos",
                      "sub": {"key": "excess_sub", "params": {
                          "beta": {"fmt": "beta", "value": ab.get("beta")}},
                          # #344: the VIX backdrop that qualifies the excess
                          # reading folds into this sub rather than standing
                          # as its own sentence beneath the grid.
                          "appends_backdrop": True}})

    alpha = _finite_number(ab.get("alpha_ann")) if single_scope else None
    if alpha is not None:
        # #363: the sub carries the 95% interval whenever alpha_stat.ci95 can
        # build one; a card with no usable ci95 falls back to exactly the
        # pre-#363 sub, so a non-credible alpha never loses the footnote for
        # its value's "*" just because no interval exists. What a tile's sub
        # still cannot hold when the interval *does* render there — that same
        # "*" legend, and the #313 negative-interval caveat — moves to the
        # standalone line below the grid (_alpha_standalone_note, read via
        # the "alpha" item's html_text in _performance_items).
        credible = bool(ab.get("credible"))
        tiles.append({"id": "alpha", "label_key": "alpha",
                      "value": {"fmt": "signed_pct0", "value": alpha},
                      "tone": None, "credible": credible,
                      "sub": {"kind": "alpha_interval_or_legend"}})
    return tiles


def _format_slot(spec, context):
    """Resolve one plan slot's raw value into display text for this context.

    Returns ``None`` when the slot is absent, or when the context cannot
    render it (no currency, no FX factor) — every caller treats that as "this
    slot did not survive", which is what keeps a missing price from printing
    as zero. A *present* slot holding no value is different: it formats as an
    em dash, because a measured-but-empty figure and an unmeasured one are
    not the same claim.
    """
    if not spec:
        return None
    fmt, value = spec.get("fmt"), spec.get("value")
    if fmt == "money":
        return _display_money(value, context)
    if fmt == "money_abs":
        return _display_money(value, context, absolute=True)
    if fmt == "money_compact":
        return _display_money_compact(value, context)
    if value is None:
        return None
    if fmt == "ratio1":
        return f"{float(value):.1f}"
    if fmt == "pp":
        return f"{_benchmark_pp(value)}pp"
    if fmt == "signed_pct0":
        return _signed_pct(value, digits=0)
    if fmt == "beta":
        return _beta_text(value)
    raise RenderError(f"unknown KPI slot format: {fmt!r}")


def _kpi_tiles(card, context, copy, backdrop=None):
    """Up to four headline tiles: P&L, payoff, benchmark excess, alpha.

    The presentation half of ``kpi_tile_plan`` — it resolves that plan's copy
    keys against one locale and its raw values against one display context.
    A tile whose label copy is missing, or whose value cannot be formatted in
    this context, is dropped rather than rendered half-empty.

    ``backdrop`` (#344) is the VIX level that qualifies the excess reading;
    when the excess tile renders it folds into its sub. The primary
    benchmark's window return rode here too until owner ruling 2026-07-23 cut
    it (#366 — a window-scoped return under a whole-history excess, with
    nothing period-local on the card to compare it against). The review span
    deliberately does NOT ride along — owner ruling 2026-07-22 put it at the
    top of the card (see ``_period_span``), because carrying both made this
    one cell roughly three times the text of its neighbours and the grid's
    row stretch padded the entire row. ``None``/absent leaves the excess tile
    exactly as before (e.g. direct callers that only need beta).

    The period curve is one more cell in this grid (2026-07-23 layout
    ruling, R5 as corrected): its line takes the value slot and its caption
    the sub line, so the row keeps one bounded height — a cell allowed to
    grow sets the height for every neighbour."""
    kpi_copy = copy.get("kpi") or {}
    tiles = []
    for plan in kpi_tile_plan(card):
        label = kpi_copy.get(plan["label_key"])
        value_text = _format_slot(plan["value"], context)
        if not value_text or (plan["id"] != "pnl" and not label):
            continue

        spec = plan.get("sub") or {}
        if spec.get("kind") == "alpha_interval_or_legend":
            interval = _alpha_tile_sub(card.get("alpha_beta_breakdown") or {}, copy["language"])
            sub = (interval if interval is not None
                   else (None if plan["credible"]
                         else "* " + (kpi_copy.get("alpha_unreliable") or "")))
        else:
            parts = {name: _format_slot(s, context) for name, s in (spec.get("params") or {}).items()}
            template = kpi_copy.get(spec.get("key"))
            resolved = (template.format(**parts)
                        if template and parts and all(parts.values()) else None)
            sub_parts = [p for p in (resolved,) if p]
            if spec.get("appends_backdrop") and backdrop:
                sub_parts.append(backdrop)
            sub = " · ".join(sub_parts) if sub_parts else None

        if plan["id"] == "alpha" and not plan["credible"]:
            value_text = f"{value_text} *"
        tiles.append({"id": plan["id"], "label": label, "value": value_text,
                      "tone": plan["tone"], "sub": sub})
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


def _card_facts(bundle, copy):
    """Assemble the rich-layout facts shared by both surfaces (#247).

    #301 removed the ``improve`` prescription rows: ``amplify`` rows now render
    beside the Block-3 strength, ``outsource`` under the Block-3 hole, and
    ``cut_loss`` rows are already carried by the rule the engine derived from
    them, so listing them again in Block 4 only produced competing imperatives.
    The v1 rich card (``rich_card.py``) still renders the full prescription
    layer through ``localized_prescription``.

    #771: this used to short-circuit to all-empty for ``snapshot_review``
    before calling any of the four functions below — a leftover from when
    ``snapshot_adapter.py`` zeroed every field wholesale, so there was
    genuinely nothing here to find. The adapter now fills what a position
    snapshot supports (``ticker_diagnosis``, ``what_if``) and leaves the rest
    absent for real reasons (``alpha_beta_breakdown`` is ``{}``, ``overview``
    carries no ``total_pnl``/``payoff`` — a snapshot has no transaction
    history to compute either from). One computation is therefore correct for
    every route: each function below already drops what its own inputs
    cannot support (see their own docstrings), so ``kpi``/``attribution`` come
    out empty on this route because the engine gave them nothing, not because
    this function special-cased the route — the distinction the short-circuit
    used to erase, in violation of "a module that has the data to render must
    actually render" (docs/layout-constraints.md §3, checker S-2)."""
    card = bundle.get("engine_card") or {}
    language = copy["language"]
    context = _display_context(card, language)
    # #344: the market backdrop _performance_block prints as Block 1's line
    # folds into the excess tile's sub here too — both call sites read the one
    # _market_backdrop so the two never drift apart. The review span is not
    # part of it; it renders once in the keynote preamble (_period_span).
    backdrop = _market_backdrop(bundle, copy)
    return {
        "kpi": _kpi_tiles(card, context, copy, backdrop=backdrop),
        "instruments": _instrument_rows(card, context, language),
        "stress": _stress_lines(card, context, language),
        "attribution": _attribution_facts(card),
    }


def _performance_block(bundle, card, copy, facts, honesty, snapshot):
    """Block 1 (Performance): the ordered indicator items plus footnote texts.

    Contract §2/§3: period label on top, then ① absolute P&L → ② annualized →
    ③ vs market; a module whose prerequisite is missing renders one localized
    neutral line, never silent omission. The stress line rides the exposure
    indicator area unconditionally when its data exists (#265 intent: no
    unrelated hole ever absorbs it — final placement is Block 1). Every
    triggered honesty sentence collapses into the Block-1 footnote (§4,
    2026-07-22 ruling — no more per-number caveat placement). Returns
    ``(items, footnote_texts)``."""
    language = copy["language"]
    if snapshot:
        # Snapshot route (#771): position-structure baseline, then declared
        # cash, then concentration stress — the same relative order the trade
        # lane uses (baseline facts, cash, stress last), minus everything a
        # snapshot has no transaction history for (payoff, annualized/account
        # return, vs-market comparison), which stay out entirely rather than
        # rendering a partial or estimated version. The agent-authored
        # limitation sentences have no indicator hosts here and collapse into
        # the footnote instead of a caveat wall.
        context = _display_context(card, language)
        items = [{"kind": "line", "tag": None, "text": text}
                 for text in _snapshot_overview_lines(card, copy)]
        cash_text = _cash_line_text(card, context, language)
        if cash_text:
            items.append({"kind": "line", "tag": "cash", "text": cash_text})
        for text in facts["stress"]:
            items.append({"kind": "line", "tag": "stress", "text": text})
        footnote = [honesty.pop(key) for key in list(honesty)]
        items = [item for item in items if item.get("text")]
        return items, footnote
    missing = copy.get("block_missing") or {}
    # #289: name the actual blocker. When the host could not retrieve prices at
    # all, the benchmark comparison is missing for a data-availability reason,
    # not because the benchmark symbol is at fault. The annualized module's own
    # variant selection moved into _annualized_gap_note (#375), which reads the
    # engine's structured gate as well as this flag.
    price_blocked = price_retrieval_blocked(card)
    items = []
    period = _market_backdrop(bundle, copy)
    if period:
        # #344: the SPY/VIX backdrop folds into the "相對大盤" (excess) KPI
        # tile's sub line when that tile renders on this card (built in
        # _kpi_tiles from this same _market_backdrop call) — HTML omits the
        # standalone line then. A card with no excess tile this period
        # (month-gated, mixed-market, or missing benchmark data) has nowhere
        # else for it to live, so HTML keeps the line; Markdown, which has no
        # tile grid at all, always keeps it. The review span is not here — it
        # renders once at the top of the card (_period_span).
        items.append({"kind": "line", "tag": "period", "text": period,
                      "kpi_id": "excess", "html_text": ""})
    perf = _performance_items(card, language)
    if not any(item.get("tag") == "pnl" for item in perf):
        perf.insert(0, {"kind": "line", "tag": None, "text": missing.get("absolute_pnl", "")})
    if not any(item.get("tag") in ("account_hold", "account", "account_gate") for item in perf):
        index = next((i for i, item in enumerate(perf)
                      if item.get("tag") in ("cash", "benchmark")), len(perf))
        perf.insert(index, {"kind": "line", "tag": None,
                            "text": _annualized_gap_note(card, missing)})
    if (not any(item.get("tag") == "benchmark" for item in perf)
            and not vs_market_suppressed(card)):
        # §3: a month-gated review renders no gap note — the vs-market lines
        # are simply absent. The one-line note stays for genuinely missing
        # benchmark data on a review whose monthly slot is open.
        perf.append({"kind": "line", "tag": None,
                     "text": (missing.get("vs_market_prices") if price_blocked
                              else missing.get("vs_market", ""))})
    items.extend(perf)
    for text in facts["stress"]:
        items.append({"kind": "line", "tag": "stress", "text": text})
    # 2026-07-22 ruling (§4/§9, #276): every triggered honesty sentence goes
    # to the footnote — real high-density accounts (5+ keys) fragmented the
    # indicator list when each one rode its own number instead.
    footnote = [honesty.pop(key) for key in list(honesty)]
    items = [item for item in items if item.get("text")]
    return items, footnote


def _trades_block(bundle, card, copy, facts, etf_lines, etf_honesty, snapshot):
    """Block 2 (Key trades): ranked instrument rows are the spine; motive
    answers, exit records, follow-ups, and horizon mirrors attach as sub-lines
    under the row of the instrument they concern. Facts no row can host stay
    as block-level lines, so nothing is lost when the spine cannot render
    (§3: one neutral line instead)."""
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

    blocks = []
    if facts["instruments"]:
        rows = [{**row, "subs": subs.get(row["ticker"], [])} for row in facts["instruments"]]
        blocks.append(("rows", rows))
    else:
        traded = [str(row.get("ticker")) for row in card.get("ticker_diagnosis") or []
                  if isinstance(row, dict) and row.get("ticker")]
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
    if etf_lines or etf_honesty:
        # #315: an ETF classification sentence ("allocation ETFs are excluded
        # from concentration: TICKER X%") explains a classification rule, not
        # a fact about the user's own behavior, so — like the etf_metadata
        # honesty sentence it already sits beside — it rides the collected
        # caveat instead of standing alone as its own fact bullet. Both join
        # into one caveat line rather than one each: S-3 bans consecutive
        # caveat paragraphs precisely because a stack of them is the same
        # "caveat wall" root cause the 2026-07-22 footnote ruling removed
        # from Block 1; a second wall must not reappear here in Block 2.
        caveats = list(etf_lines)
        if etf_honesty:
            caveats.append(etf_honesty)
        blocks.append(("caveat", [" ".join(caveats)]))
    return blocks


def exit_consistency_facts(card):
    """Aggregate the per-instrument ``sold_winner_early`` tags into one fact set.

    This is the single engine-derived source of truth shared by two surfaces:
    the read-only ``[?]`` pattern panel (#303) and the answerable
    exit-consistency question the review engine may queue for the same facts
    (#303 interactive half). Returns ``{"early", "total", "instruments":
    [{"ticker", "early", "total"}, …]}`` sorted most-consistent-first, or
    ``None`` when no instrument carries the tag."""
    entries = []
    for row in (card or {}).get("ticker_diagnosis") or []:
        if not isinstance(row, dict):
            continue
        for tag in row.get("tags") or []:
            code = tag.get("code") if isinstance(tag, dict) else None
            if code != "sold_winner_early":
                continue
            params = tag.get("params") or {}
            early, total = params.get("win_early"), params.get("win_n")
            if not isinstance(early, (int, float)) or not isinstance(total, (int, float)):
                continue
            entries.append({"ticker": row.get("ticker"), "early": int(early), "total": int(total)})
    entries = [e for e in entries if e["ticker"] and e["total"]]
    if not entries:
        return None
    entries.sort(key=lambda e: (-(e["early"] / e["total"]), -e["early"], e["ticker"]))
    return {"early": sum(e["early"] for e in entries),
            "total": sum(e["total"] for e in entries),
            "instruments": entries}


def exit_consistency_named(facts, language):
    """The top-3 ``TICKER early/total`` list both surfaces name (#303)."""
    joiner = ", " if str(language).lower().startswith("en") else "、"
    return joiner.join(f"{e['ticker']} {e['early']}/{e['total']}"
                       for e in (facts or {}).get("instruments", [])[:3])


def exit_consistency_line(card, copy):
    """The one fact sentence shared by the ``[?]`` panel and the question stem.

    Returns ``(facts, sentence)`` so callers that also need the raw counts (the
    question builder) do not re-aggregate. ``sentence`` is ``None`` when the
    copy template is missing; ``facts`` is ``None`` when nothing fired."""
    facts = exit_consistency_facts(card)
    if not facts:
        return None, None
    template = (copy.get("patterns_panel") or {}).get("sold_winner_early")
    if not template:
        return facts, None
    try:
        return facts, template.format(early=facts["early"], total=facts["total"],
                                      tickers=exit_consistency_named(facts, copy["language"]))
    except (KeyError, IndexError, ValueError):
        return facts, None


def _pattern_panel(card, copy, snapshot, suppress=False):
    """Block 3 ``[?]`` panel (#303): read-only patterns the engine detected but
    has not judged, collected in one place.

    Exit opportunity-cost tags used to sit scattered across the instrument rows,
    where a reader could not tell whether a reply was expected. The panel names
    the instruments so the pattern is checkable, and its label says outright
    that no answer is wanted. Returns ``None`` when nothing fired — or when
    ``suppress`` is set because the review already put this exact pattern to the
    user as an answerable question, so a "no answer needed" observation would
    contradict the question they just saw."""
    if snapshot or suppress:
        return None
    facts, line = exit_consistency_line(card, copy)
    label = (copy.get("patterns_panel") or {}).get("label")
    if not facts or not line or not label:
        return None
    return ("panel", {"style": "pattern", "mark": "?", "label": label,
                      "blocks": [("paragraph", [line])]})


def _exit_consistency_asked(bundle):
    """True when the review queued the answerable exit-consistency question, so
    the ``[?]`` observation panel yields to it (#303)."""
    queue = (bundle.get("review_plan") or {}).get("question_queue") or []
    return any(isinstance(q, dict) and q.get("kind") == "exit_consistency" for q in queue)


def _risks_block(bundle, card, copy, narrative, snapshot, trade_tickers=None):
    """Block 3 (Risks and problems): the [v] strength / [X] hole / [?] pattern
    panels, with behavior patterns folded in below.

    #301: the ``[v]`` panel also carries the ``amplify`` prescription rows.
    They describe what the period proved, not an action, so listing them beside
    the one committed rule in Block 4 made the card read as several competing
    orders. Here they sit next to the strength they qualify.

    #771: ``card["strength"]`` — the pre-rendered zh sentence
    ``trade_recap.dim_strength``/the snapshot adapter emit — is not read on
    either route here, and that is pre-existing, not a snapshot gap. The trade
    lane's own ``[v]`` panel already ignores it in favor of recomputing
    ``_best_strength`` from ``dims_raw`` (a locale-neutral computation two
    lines below); consuming the raw field directly would both violate the
    zh-only-legacy-literal boundary ``_instrument_rows`` documents and
    duplicate a fact ``dims_raw`` already answers. The snapshot branch below
    mirrors that shape with ``_snapshot_strength_line`` (which reads
    ``snapshot_summary.weights_available``, the axis #549 ratified) rather
    than ``_best_strength`` itself, because a snapshot's ``dims_raw`` never
    carries the exit-discipline/averaging-down/holding-period candidates that
    function's ``no_signal`` fallback is worded for ("no positive behavior")
    — wording that fits a transaction history, not a point-in-time holdings
    check with none to judge."""
    language = copy["language"]
    sections_copy = copy["sections"]
    missing = copy.get("block_missing") or {}
    holes = _applicable_holes(card)
    trade_tickers = set(trade_tickers or [])
    motive_lines = [text for ticker, text in _headline_motive_entries(bundle, copy)
                    if not ticker or str(ticker) not in trade_tickers]
    pattern_panel = _pattern_panel(card, copy, snapshot,
                                   suppress=_exit_consistency_asked(bundle))
    blocks = []
    if (not snapshot and not holes and not card.get("dims_raw")
            and not motive_lines and not pattern_panel):
        note = missing.get("risks", "")
        return [("paragraph", [note])] if note else []
    strength_label = (_copy_string(copy, "snapshot_strength", sections_copy["strength"])
                      if snapshot else sections_copy["strength"])
    strength_line = (_snapshot_strength_line(card, language) if snapshot else
                     narrative.get("strength") or _best_strength(card, language))
    strength_inner = [("paragraph", [strength_line])]
    amplify = None if snapshot else amplify_row(card, language)
    if amplify:
        strength_inner.append(("paragraph", [amplify["text"]]))
    blocks.append(("panel", {"style": "strength", "mark": "v", "label": strength_label,
                             "blocks": strength_inner}))
    hole_label = (_copy_string(copy, "snapshot_hole", sections_copy["hole"])
                  if snapshot else sections_copy["hole"])
    hole_inner = []
    if snapshot:
        hole_inner.extend(("paragraph", [line]) for line in _snapshot_hole_lines(card, language))
    elif holes:
        hole_inner.append(("paragraph", [_hole_line(holes[0], language)]))
    if not snapshot:
        outsource = outsource_row(card, language)
        if outsource:
            hole_inner.append(("paragraph", [outsource["text"]]))
    if not snapshot and narrative.get("counterfactual"):
        hole_inner.append(("paragraph", [narrative["counterfactual"]]))
    if hole_inner:
        blocks.append(("panel", {"style": "hole", "mark": "X", "label": hole_label,
                                 "blocks": hole_inner}))
    if pattern_panel:
        blocks.append(pattern_panel)
    problem_lines = [] if snapshot else _problem_lines(bundle, copy)
    if problem_lines:
        blocks.append(("bullets", problem_lines))
    if motive_lines:
        blocks.append(("bullets", motive_lines))
    return blocks


def _next_block(bundle, copy, facts, state, snapshot):
    """Block 4 (Next step): exactly one committed rule — nothing else.

    §2 of the output contract, and the README demo card it is anchored to,
    allow a single action here. #301: the ``improve`` prescription rows used to
    render above the rule, so the block issued up to five imperatives at once,
    some of them opposing ("don't let sizing dilute your edge" beside "PLTR is
    too heavy at 49%"). ``amplify`` rows now belong to Block 3's ``[v]`` panel;
    the remaining ``cut_loss`` rows are already represented by the rule the
    engine derived from them. What survives here is the rule, the positions it
    would act on (#302), and — only when the card also claims a strength the
    rule appears to contradict — one engine-owned sentence stating the order of
    operations (#301).

    ``narrative.rule_rationale`` is deliberately no longer rendered: it is the
    agent's free-text restatement of why the rule matters, and it overlapped
    with the engine-owned trade-off line. Between an authored sentence and a
    derived one, the card keeps the derived one.

    §3: this block always lights — when the engine proposes no change it
    restates the standing rule, and a truly empty review says so in one
    neutral localized line instead of disappearing."""
    language = copy["language"]
    sections_copy = copy["sections"]
    missing = copy.get("block_missing") or {}
    commitment = bundle.get("commitment") or {}
    card = bundle.get("engine_card") or {}
    blocks = []
    rule_inner = []
    # Applicability gates this period's generated findings, not a user's
    # previously chosen standing rule.  An empty current book must not erase
    # the commitment they explicitly asked the next review to reconcile.
    dim = commitment.get("dim")
    rule = commitment.get("rule")
    if rule:
        rule_inner.append(("paragraph", [rule]))
        # #302: name the positions or behavior counts this rule would act on,
        # at the same level as the rule itself. The aggregate grounding
        # sentence (#248) is the fallback when the dimension has no per-position
        # facts, so a rule is never left unanchored.
        # Only the commitment's own dimension: build_state warns that the rule
        # need not match headline_dim, so falling back to it would list the
        # wrong positions under the rule. A custom rule without a dimension
        # keeps the aggregate grounding sentence instead.
        # #328: the same standing cap override that shaped the rule text and
        # the engine's own severity (state.max_position_pct, #324) must also
        # govern which positions this line names.
        targets = (localized_rule_targets(dim, language, card, state.get("max_position_pct"))
                   if dim else None)
        grounding = commitment.get("grounding")
        if targets:
            rule_inner.append(("grounding", [targets]))
        elif isinstance(grounding, str) and grounding.strip():
            rule_inner.append(("grounding", [grounding]))
        tradeoff = rule_tradeoff_line(card, dim, language) if dim else None
        if tradeoff:
            # Same sub-line level as the targets: both qualify the rule above
            # them rather than introducing anything new.
            rule_inner.append(("grounding", [tradeoff]))
        # #412: the engine's own comparison of a user-authored condition. Same
        # sub-line level, same reason — it qualifies the rule directly above it.
        condition_state = condition_state_line(commitment, language)
        if condition_state:
            rule_inner.append(("grounding", [condition_state]))
    elif ((bundle.get("answers") or {}).get("commitment") or {}).get("choice") == "skip":
        rule_inner.append(("paragraph", [missing.get("rule_skip", "")]))
    elif snapshot:
        rule_inner.append(("paragraph", [missing.get("rule_snapshot", "")]))
    elif (state.get("review_tier") or {}).get("tier") == "structural":
        # #306: a thin first file is an opening structural check, not a full
        # behavioral review. Keep the structural baseline (no forced commitment)
        # and name the one thing that unlocks the behavioral review, so the card
        # reads as a coherent opening step rather than a review apologizing for
        # what it lacks. Framed off the tier, not insufficient_data, so a
        # high-frequency short-window file (behavioral, span-short) is not caught.
        rule_inner.append(("paragraph", [missing.get("rule_structural", "")]))
    elif state.get("insufficient_data"):
        rule_inner.append(("paragraph", [missing.get("rule_insufficient_data", "")]))
    else:
        # #356: ``state["rule"]`` is a v1-only zh literal — trade_recap.prescribe
        # writes the sentence in Traditional Chinese — so interpolating it here
        # leaked Chinese into English cards. Resolve the canonical rule text from
        # copy "rules" through the prescription's dimension instead, the same
        # resolution the committed private card and the public card already use.
        # A prescription without a resolvable dimension falls through to the
        # generic localized line rather than the untranslated literal.
        prescription = (localized_rule(state.get("rule_dim"), language,
                                       cap=state.get("max_position_pct"))
                        if state.get("rule_dim") else None)
        # #546: this branch also fires at preview time on a user's very first
        # review — ``require_commitment=False`` nulls ``bundle["commitment"]``
        # before the user has ever chosen a rule, and ``state["rule_dim"]`` is
        # this period's fresh prescription, never a carried-forward answer. "The
        # standing rule remains" is a continuity claim, true only when a prior
        # commitment was actually persisted, so it is gated on the same
        # predicate ``_reconciliation_lines`` already reads for #292: a genuine
        # first review has nothing to restate and gets the pending-choice line
        # instead, naming the same recommendation as awaiting the user's choice.
        #
        # #645: that gate decided *whether* to claim continuity and left the
        # quoted rule resolved from ``state["rule_dim"]`` — so a returning user
        # read "the standing rule remains" followed by a rule they never chose.
        # The prior commitment is the only record of what is standing, so it is
        # what gets quoted, and continuity is claimed only when the two are
        # *proven* to name the same dimension. ``commitment["dim"]`` is that
        # proof and it already exists: ``review._candidate_rules`` stamps the
        # canonical ``dimension_id`` on every candidate row and
        # ``_resolve_commitment`` carries it into the stored commitment. Both
        # sides go through ``dimension_id`` because the two namespaces differ —
        # a stored commitment carries the canonical id, ``state["rule_dim"]``
        # the legacy label — and a custom commitment may supply either.
        #
        # A commitment with no ``dim`` cannot be compared without guessing: a
        # condition slot drops the field deliberately (``_slot_commitment``) and
        # a custom rule need not carry one. That case fails safe onto the same
        # pending-choice line — the one wrapper here that asserts neither
        # continuity nor divergence — rather than inferring a dimension from
        # ``metric_key`` through a mapping no existing reader owns.
        prior_commitment = (((bundle.get("review_plan") or {}).get("state_snapshot") or {})
                            .get("prior_commitment") or {})
        standing_rule = prior_commitment.get("rule")
        prior_dim = prior_commitment.get("dim")
        comparable = bool(prior_dim) and bool(state.get("rule_dim"))
        if not standing_rule:
            # #546: nothing was ever persisted, so nothing is standing.
            standing_key, fields = "rule_pending", {"rule": prescription}
        elif not comparable:
            # #645 fail-safe: something is standing, but not something this
            # payload can compare. Claim neither continuity nor divergence.
            standing_key, fields = "rule_pending", {"rule": prescription}
        elif dimension_id(prior_dim) == dimension_id(state.get("rule_dim")):
            standing_key, fields = "rule_standing", {"rule": standing_rule}
        else:
            standing_key, fields = "rule_diverged", {"standing": standing_rule,
                                                     "recommendation": prescription}
        text = None
        if all(fields.values()) and missing.get(standing_key):
            try:
                text = missing[standing_key].format(**fields)
            except (KeyError, IndexError, ValueError):
                text = None
        if not text:
            text = missing.get("rule", "")
        if text:
            rule_inner.append(("paragraph", [text]))
    if snapshot:
        # #316: exactly one unlock hint, appended here regardless of which
        # commitment sub-branch above fired (baseline-only or explicit skip) —
        # the card's last block names the concrete payoff of importing history
        # instead of interrupting the structure story earlier with a repeated
        # disclosure, and it must not silently disappear behind the "skip"
        # acknowledgment.
        rule_inner.append(("paragraph", [missing.get("snapshot_unlock", "")]))
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
    preamble plus four mandatory blocks — Performance, Key trades, Risks and
    problems, Next step — with block titles from ``copy.blocks``. An optional
    5th block, the closing synthesis (#345, ``narrative.synthesis``), appends
    after Next step only when the agent authors it; when absent it is not in
    ``sections`` at all — no header, no placeholder, unlike the four mandatory
    blocks above, which always render something (falling back to a neutral
    one-line note rather than disappearing). Block content is ``(kind,
    payload)`` tuples: ``paragraph`` / ``bullets`` / ``grounding`` line lists,
    ``indicators`` (Block-1 line and attr-row items — a mixed-market item may
    carry a ``market`` grouping key, §2/§9), ``footnote`` (every triggered
    honesty sentence, 2026-07-22 ruling §4), ``rows`` (instrument spine with
    attached sub-lines), ``panel`` (strength/hole/rule), and ``improve``
    (prescription rows)."""
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
    # Owner ruling 2026-07-22: the review window leads the card. It scopes
    # every number below it, so the reader needs it before anything else --
    # and it is card-level metadata rather than a property of any single
    # indicator, which is why it no longer rides the excess tile's sub line.
    period_span = _period_span(bundle, copy)
    if period_span:
        preamble.append(("paragraph", [period_span]))
    opening = [] if snapshot else _review_opening_lines(bundle, copy["language"])
    if opening:
        preamble.append(("paragraph", opening))
    preamble.append(("paragraph", [narrative["mirror"]]))

    # #82: honesty sentences are woven next to the numbers they qualify (§4) —
    # never printed as a standalone checklist section. etf_metadata rides the
    # ETF lines in Block 2 when they render; every unhosted sentence collapses
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
    # #345: optional 5th block — a closing synthesis appended after Next step,
    # present only when the agent authors narrative.synthesis. Unlike the four
    # mandatory blocks above (which always render, falling back to a neutral
    # one-line note when data is missing), this section has no fallback text:
    # an absent or empty field means the section does not exist at all — no
    # header, no placeholder — the same clean-degradation shape as any other
    # unauthored optional narrative field. validate_narrative already
    # guarantees a present value is a non-empty, digit-free string, so a plain
    # truthiness check is sufficient here.
    synthesis = narrative.get("synthesis")
    if synthesis:
        sections.append({"id": "summary", "title": blocks_copy.get("summary", ""),
                         "blocks": [("paragraph", [synthesis])]})

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
    """One indented full-line parenthetical: the caveat shape S-3 recognizes.

    Since the 2026-07-22 footnote ruling (§4), the only remaining caller is
    the Block-2 ETF metadata caveat — Block-1 honesty no longer uses this
    shape at all (it collapses into the plain-line footnote instead)."""
    return f"  ({text})" if en else f"  （{text}）"


_SENTENCE_END = "。.!?！？"


def _is_bulletable(text):
    """True for a short, single-sentence honesty footnote line — the shape
    a bullet suits.

    2026-07-22 owner ruling (bullet pass on the footnote): bullet every
    disclosure *except* a complete multi-sentence narrative paragraph,
    which should stay a plain paragraph instead of being crammed into one
    bullet. In practice every `narrative.honesty` value is already a single
    sentence — it is contractually one qualitative, digit-free sentence per
    key (card-spec.md), enforced by ``validate_narrative`` raising on any
    digit — so this only guards a hypothetical future multi-sentence entry;
    it does not change today's output. Counting sentence-terminal
    punctuation is a safe proxy *only* for this digit-free text.

    Deliberately NOT used for the TW/US grouped vs-market lines: those are
    engine-templated (``_private_benchmark_line``/``_private_split_lines``)
    and always exactly one sentence by construction, but they are full of
    decimal numbers (e.g. "β 1.10") whose "." would be misread as a second
    sentence boundary by this same counting proxy — counting would be
    actively wrong there, not just unnecessary. Those lines bullet
    unconditionally instead (see the market-gated call sites)."""
    body = text.rstrip()
    if body and body[-1] in _SENTENCE_END:
        body = body[:-1]
    return not any(ch in _SENTENCE_END for ch in body)


def _bulleted_html(texts):
    """Render each bulletable text as one <li> inside a <ul>; a non-bulletable
    (multi-sentence) text renders as its own <p> instead. Consecutive
    bulletable texts share one <ul> so the list never fragments into one
    <ul> per item. Reuses the existing .rc ul/li styling (_HTML_WIDGET_CSS)
    — no new CSS for this ruling."""
    parts = []
    pending = []

    def flush():
        if pending:
            parts.append("<ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in pending) + "</ul>")
            pending.clear()

    for text in texts:
        if _is_bulletable(text):
            pending.append(text)
        else:
            flush()
            parts.append(f"<p>{html.escape(text)}</p>")
    flush()
    return "".join(parts)


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


def _read_first_panels(structure):
    """Return existing leading risk/rule lines for a text-first scan.

    Markdown hosts have no visual KPI grid to keep the conclusion in view.
    The compact blockquote rendered by ``render_private`` therefore repeats
    only the lead line from the canonical Risk and Next-step panels before
    the detailed four-block card. It is not a second diagnosis, calculation,
    or rule.
    """
    copy = structure["copy"]
    en = structure["language"] == "en"
    sections = {section["id"]: section for section in structure["sections"]}
    entries = []
    for section_id, style, label_key in (("risks", "hole", "risk"),
                                         ("next", "rule", "next")):
        section = sections.get(section_id) or {}
        panel = next((block for kind, block in section.get("blocks") or []
                      if kind == "panel" and block.get("style") == style), None)
        if panel:
            entries.append((copy["markdown_summary"][label_key], _panel_md(panel, en)[0]))
    return entries


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
    # #325: plain-text reader path. The title remains the keynote; the leading
    # risk and rule are visible before performance detail while the complete
    # four-block narrative remains in its fixed order below.
    for label, value in _read_first_panels(structure):
        lines.extend([f"> **{label}**", ">", f"> {value}", ""])
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
                # Mixed-market grouping (#276 2026-07-22 dogfood note): only
                # label when 2+ markets actually appear in this block — a
                # single-market card has nothing to disambiguate, so it stays
                # exactly as before. No caveat items reach this loop anymore
                # (§4 2026-07-22 ruling): every honesty sentence is footnoted.
                # 2026-07-24 owner bullet pass (#363) supersedes the
                # 2026-07-22 one recorded here through 2026-07-23: that
                # ruling bulleted only the grouped vs-market lines and left
                # the main Block-1 number lines (pnl/payoff/account/cash/
                # stress/...) as plain paragraphs, reserving bullets for the
                # footnote and the TW/US modules. The owner has now ruled
                # that every Block-1 indicator line gets a "- " bullet on
                # this (Markdown) surface. The HTML surface is unchanged — it
                # keeps <p> paragraphs there, because its KPI tile grid
                # already carries the visual structure and its own Block-1
                # prose runs to only about four lines, unlike Markdown, which
                # has no tile grid to lean on and renders every figure as
                # prose. Bullet unconditionally here (no _is_bulletable
                # check): these lines are engine-templated and always
                # exactly one sentence by construction, but they are full of
                # decimal numbers (e.g. "β 1.10") that _is_bulletable's
                # sentence-counting would misread as a second sentence —
                # that check is for honesty text specifically (digit-free by
                # contract), not this.
                markets = {item.get("market") for item in block if item.get("market")}
                current_market = None
                for item in block:
                    market = item.get("market") if len(markets) > 1 else None
                    if market and market != current_market:
                        lines.append(f"[{market}]")
                    current_market = market
                    lines.append(f"- {item['text']}")
                lines.append("")
            elif kind == "footnote":
                # One sentence per line (not one joined paragraph): the same
                # fix this ruling makes to Block 1 — a wall of honesty
                # sentences run together is still a wall, just moved.
                # 2026-07-22 owner bullet pass: each disclosure gets a "- "
                # bullet; a non-bulletable multi-sentence entry (none exist
                # today — see _is_bulletable) would stay a plain line.
                label = copy.get("footnote_label", "")
                lines.append(f"{label}{':' if en else '：'}")
                lines.extend(f"- {text}" if _is_bulletable(text) else text for text in block)
                lines.append("")
            elif kind == "caveat":
                # Rides the block right above it (e.g. the ETF facts): no
                # blank line in between, so the sentence stays attached.
                if lines and lines[-1] == "":
                    lines.pop()
                lines.extend([_caveat_md(x, en) for x in block if x] + [""])
            elif kind == "panel":
                lines.extend(_panel_md(block, en) + [""])
            else:
                lines.extend(list(block) + [""])
    return "\n".join(lines).rstrip() + "\n"


def _public_band(value, language):
    value = float(value or 0)
    bands = load_copy(language).get("public_band") or {}
    if value < 0.25:
        return bands.get("low")
    if value < 0.40:
        return bands.get("moderate")
    if value < 0.60:
        return bands.get("high")
    return bands.get("very_high")


def _public_performance_lines(card, language):
    """Share only allowlisted market labels and engine-owned relative scalars."""
    perf_copy = load_copy(language).get("public_performance") or {}
    lines = []
    for market, _bench, row in _benchmark_rows(card):
        excess = _finite_number(row.get("excess_vs_spy"))
        beta = _beta_text(row.get("beta"))
        if excess is None or beta is None:
            continue
        subject = market or perf_copy.get("subject_default")
        line = _format_copy(perf_copy.get("line"), subject=subject or "",
                            excess=_benchmark_pp(excess), beta=beta)
        if line:
            lines.append(line)
    return lines


def render_public(bundle):
    """Render a conservative shareable card without user-authored free text."""
    language = bundle.get("language") or "zh-TW"
    copy = load_copy(language)
    card = bundle.get("engine_card") or {}
    snapshot = bundle.get("route") == "snapshot_review"
    snapshot_summary = _snapshot_summary(card)
    holes = _applicable_holes(card)
    hole = holes[0] if holes else {}
    raw = hole.get("raw") or {}
    dim_id = dimension_id(raw.get("dim")) if raw.get("dim") else None
    dim_label = (copy.get("dimensions") or {}).get(dim_id) if dim_id else None
    pattern = None
    if dim_id and not snapshot:
        patterns = copy.get("public_patterns") or {}
        if dim_id == "holding_period" and raw.get("all_same_day"):
            pattern = patterns.get("holding_period_same_day")
        else:
            pattern = patterns.get(dim_id)
    severity_value = _finite_number(hole.get("severity"))
    severity = (None if snapshot and severity_value is None
                else _public_band(hole.get("severity"), copy["language"]))
    commitment = bundle.get("commitment") or {}
    # #324: the public card re-derives the canonical rule text (never the
    # user-authored commitment["rule"], which may carry tickers/amounts/dates),
    # so it must thread the standing single-position cap override itself — the
    # same value review.py bakes into the private card's committed rule.
    cap_override = (bundle.get("engine_state") or {}).get("max_position_pct")
    # Candidate rules resolve to fixed copy strings; custom rules (and anything of
    # unknown origin) render as a generic localized line so user-authored text —
    # which may carry tickers, amounts, or dates — never reaches the public card.
    rule = None
    if commitment:
        if commitment.get("origin") == "candidate":
            rule = localized_rule(commitment.get("dim"), language, cap=cap_override)
        if not rule:
            rule = copy.get("public_custom_rule")
    structural_hole = (snapshot and snapshot_summary.get("weights_available") is True
                       and bool(holes) and dim_id in {"position_sizing", "diversification"})
    mirror_copy = copy.get("public_mirror") or {}
    if structural_hole:
        dim = dim_label or mirror_copy.get("structural_dim_default")
        key = "structural_with_severity" if severity else "structural"
        mirror = _format_copy(mirror_copy.get(key), dim=dim or "", severity=severity)
    elif snapshot:
        mirror = mirror_copy.get("snapshot_baseline")
    elif not holes:
        mirror = mirror_copy.get("no_holes")
    else:
        dim = dim_label or mirror_copy.get("behavioral_dim_default")
        mirror = _format_copy(mirror_copy.get("behavioral"), dim=dim or "",
                              severity=severity)
    structure = mirror_copy.get("structure")
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
# Design provenance: card-template.src.html (2026-07-04 UI review). Runtime
# truth lives here; the template is GENERATED from the two CSS literals below
# by tools/gen_card_template.py (#401), so a design-rule change only needs to
# land here -- rerun that script afterward to refresh the committed
# card-template.html (tests/test_card_html.py fails if it is stale).
# Constraints: flat, light/dark via prefers-color-scheme, system font stack,
# one <=20px heading, outlined tags, neutral surfaces, semantic color only on
# section labels and P&L accents, font weights 400/500, no emoji, no icon
# font, and zero external requests (no http(s) URLs anywhere in the document).

# Document-level shim: lets the artifact open directly in a browser. The widget
# fragment below is self-contained and does not depend on this shim.
_HTML_SHIM_CSS = """\
body{margin:0;background:#eceae1;color:#1a1915;padding:28px 16px;display:flex;justify-content:center;
font-family:system-ui,-apple-system,"Segoe UI","Noto Sans TC","Noto Sans SC",sans-serif}
@media (prefers-color-scheme:dark){body{background:#1a1917;color:#f5f4ef}}
.page{width:680px;max-width:100%}"""

# Widget-fragment styles. Host theme variables (--surface-*, --text-*, --border,
# --radius) win when present; the var() fallbacks keep the fragment readable in
# hosts without them, with a prefers-color-scheme dark set of fallbacks.
#
# Layout tokens (--rc-sp-*, --rc-tx-*, --rc-r-*) exist because colour was the
# only tokenized axis: spacing used 16 ad-hoc values and type used 7 steps, so
# every layout ruling had to name a pixel instead of a scale.  See
# docs/layout-constraints.md §5.  Generated into card-template.html by
# tools/gen_card_template.py (#401); see docs/maintainer-guide.md.
_HTML_WIDGET_CSS = """\
.rc{--rc-surface-2:var(--surface-2,#ffffff);--rc-surface-1:var(--surface-1,#f5f4ef);
--rc-surface-key:var(--surface-key,#f0eee6);
--rc-text-primary:var(--text-primary,#1a1915);--rc-text-secondary:var(--text-secondary,#5f5e5a);
--rc-text-muted:var(--text-muted,#6d6c65);--rc-text-success:var(--text-success,#3b6d11);
--rc-text-danger:var(--text-danger,#a32d2d);--rc-text-accent:var(--text-accent,#185fa5);
--rc-border:var(--border,rgba(0,0,0,0.10));--rc-border-key:var(--border-key,rgba(24,95,165,0.35));
--rc-radius:var(--radius,8px);
--rc-sp-1:4px;--rc-sp-2:8px;--rc-sp-3:12px;--rc-sp-4:16px;--rc-sp-5:20px;--rc-sp-6:24px;
--rc-tx-micro:11px;--rc-tx-small:12px;--rc-tx-body:14px;--rc-tx-lead:15px;--rc-tx-rule:17px;
--rc-tx-figure:20px;
--rc-r-sm:6px;--rc-r-md:var(--rc-radius);--rc-r-lg:12px}
@media (prefers-color-scheme:dark){.rc{--rc-surface-2:var(--surface-2,#2b2a27);
--rc-surface-1:var(--surface-1,#232220);--rc-surface-key:var(--surface-key,#1b1d20);
--rc-text-primary:var(--text-primary,#f5f4ef);
--rc-text-secondary:var(--text-secondary,#b4b2a9);--rc-text-muted:var(--text-muted,#939289);
--rc-text-success:var(--text-success,#a7be83);--rc-text-danger:var(--text-danger,#df8b84);
--rc-text-accent:var(--text-accent,#a8c8f0);--rc-border:var(--border,rgba(255,250,240,0.10));
--rc-border-key:var(--border-key,rgba(168,200,240,0.42))}}
.rc{font-family:system-ui,-apple-system,"Segoe UI","Noto Sans TC","Noto Sans SC",sans-serif;font-weight:400;
color:var(--rc-text-primary);background:var(--rc-surface-2);border:0.5px solid var(--rc-border);
border-radius:var(--rc-r-lg);overflow:hidden;line-height:1.6;font-variant-numeric:tabular-nums}
.rc .sec{padding:var(--rc-sp-5) var(--rc-sp-6)}
.rc .sec+.sec{border-top:0.5px solid var(--rc-border)}
.rc .eyebrow{font-size:var(--rc-tx-small);color:var(--rc-text-muted);margin:0 0 var(--rc-sp-1)}
.rc h1{font-size:var(--rc-tx-figure);font-weight:500;margin:0;line-height:1.35}
.rc .tags{display:flex;flex-wrap:wrap;gap:var(--rc-sp-1);margin:var(--rc-sp-2) 0 0}
.rc .tag{display:inline-flex;align-items:center;font-size:var(--rc-tx-small);padding:1px var(--rc-sp-2);
border-radius:var(--rc-r-sm);
line-height:1.5;background:transparent;border:0.5px solid var(--rc-border);color:var(--rc-text-secondary)}
.rc .lead{font-size:var(--rc-tx-body);color:var(--rc-text-secondary);line-height:1.7;margin:var(--rc-sp-3) 0 0}
.rc h2{font-size:var(--rc-tx-lead);font-weight:500;margin:0 0 var(--rc-sp-2);color:var(--rc-text-primary)}
.rc p{font-size:var(--rc-tx-body);color:var(--rc-text-secondary);line-height:1.7;margin:0}
.rc p+p,.rc ul+p,.rc p+ul{margin-top:var(--rc-sp-2)}
.rc ul{margin:0;padding-left:var(--rc-sp-5)}
.rc li{font-size:var(--rc-tx-body);color:var(--rc-text-secondary);line-height:1.7;margin:0 0 var(--rc-sp-2)}
.rc li:last-child{margin-bottom:0}
.rc .spark{display:block;width:100%;height:34px;margin:0}
.rc .spark path{fill:none;stroke:var(--rc-text-muted);stroke-width:1.5;stroke-linecap:round;
stroke-linejoin:round;opacity:.85}
.rc .spark.pos path{stroke:var(--rc-text-success)}
.rc .spark.neg path{stroke:var(--rc-text-danger)}
.rc .pos{color:var(--rc-text-success)}
.rc .neg{color:var(--rc-text-danger)}
.rc .kpi{display:grid;gap:var(--rc-sp-2);margin:0 0 var(--rc-sp-1)}
.rc .kpi[data-n="1"]{grid-template-columns:minmax(0,1fr)}
.rc .kpi[data-n="2"]{grid-template-columns:repeat(2,minmax(0,1fr))}
.rc .kpi[data-n="3"]{grid-template-columns:repeat(3,minmax(0,1fr))}
.rc .kpi[data-n="4"]{grid-template-columns:repeat(4,minmax(0,1fr))}
/* Five cells is four metrics plus the curve. Five equal columns would leave
   each one too narrow for its sub line, so they wrap to two rows of three
   with the curve spanning two: 1 + 2 + 3 fills both rows exactly, and the
   curve gets the width it needs to read as a shape. */
.rc .kpi[data-n="5"]{grid-template-columns:repeat(3,minmax(0,1fr))}
.rc .kpi[data-n="5"] .curve{grid-column:span 2}
/* Two columns cannot host a two-column span without stranding the cell
   beside it on a row of its own, so the curve drops back to one cell here. */
@media (max-width:560px){.rc .kpi[data-n="3"],.rc .kpi[data-n="4"],
.rc .kpi[data-n="5"]{grid-template-columns:repeat(2,minmax(0,1fr))}
.rc .kpi[data-n="5"] .curve{grid-column:auto}}
/* The line occupies the value's slot, at the value's height, so this cell is
   the same three-part shape as every other tile and cannot stretch the row. */
.rc .m.curve .cval{margin:var(--rc-sp-1) 0 0;height:25px}
.rc .m.curve .spark{height:25px;margin:0}
.rc .m{background:var(--rc-surface-1);border-radius:var(--rc-r-md);
padding:var(--rc-sp-3) var(--rc-sp-4)}
.rc .m .lbl{font-size:var(--rc-tx-small);color:var(--rc-text-secondary);margin:0}
.rc .m .val{font-size:var(--rc-tx-figure);font-weight:500;margin:var(--rc-sp-1) 0 0;line-height:1.25;
color:var(--rc-text-primary)}
.rc .m .val.pos{color:var(--rc-text-success)}
.rc .m .val.neg{color:var(--rc-text-danger)}
.rc .m .sub{font-size:var(--rc-tx-micro);color:var(--rc-text-muted);margin:var(--rc-sp-1) 0 0;
line-height:1.45;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.rc .trow{margin:0 0 var(--rc-sp-3)}
.rc .trow:last-of-type{margin-bottom:0}
.rc .ttop{display:flex;align-items:baseline;gap:var(--rc-sp-3)}
.rc .tk{font-family:ui-monospace,"SF Mono",Menlo,monospace;font-size:var(--rc-tx-body);
font-weight:500;min-width:52px}
.rc .tamt{font-size:var(--rc-tx-body);font-weight:500;min-width:78px;text-align:right}
.rc .ttags{display:flex;flex-wrap:wrap;gap:var(--rc-sp-1);flex:1}
@media (max-width:300px){.rc .ttop{flex-wrap:wrap}}
@media (max-width:300px){.rc .ttags .tag{font-size:var(--rc-tx-micro)}}
.rc .track{height:4px;border-radius:99px;background:var(--rc-surface-1);margin:var(--rc-sp-1) 0 0;
overflow:hidden}
.rc .fill{height:100%;border-radius:99px;background:var(--rc-text-muted);opacity:.7}
.rc .fill.neg{background:var(--rc-text-danger);opacity:.85}
.rc .cap{font-size:var(--rc-tx-small);color:var(--rc-text-muted);margin:var(--rc-sp-3) 0 0;line-height:1.6}
.rc .arow{display:grid;grid-template-columns:1fr 70px;gap:var(--rc-sp-3);align-items:center;
margin:var(--rc-sp-2) 0}
.rc .arow .al{font-size:var(--rc-tx-body);color:var(--rc-text-secondary)}
.rc .arow .av{font-size:var(--rc-tx-body);font-weight:500;text-align:right;
font-family:ui-monospace,"SF Mono",Menlo,monospace}
.rc .abar{height:4px;border-radius:99px;background:var(--rc-surface-1);margin:var(--rc-sp-1) 0 0;
overflow:hidden}
.rc .abar div{height:100%;border-radius:99px;background:var(--rc-text-muted);opacity:.7}
@media (max-width:300px){.rc .arow{grid-template-columns:1fr}}
.rc .panel{background:var(--rc-surface-1);border:0.5px solid var(--rc-border);
border-left:3px solid var(--rc-text-muted);
border-radius:var(--rc-r-md);padding:var(--rc-sp-3) var(--rc-sp-4)}
.rc .panel+.panel{margin-top:var(--rc-sp-2)}
.rc .panel-label{font-size:var(--rc-tx-small);font-weight:500;margin:0 0 var(--rc-sp-1)}
.rc .strength{border-left-color:var(--rc-text-success)}
.rc .strength .panel-label{color:var(--rc-text-success)}
.rc .hole{border-left-color:var(--rc-text-danger)}
.rc .hole .panel-label{color:var(--rc-text-danger)}
.rc .pattern .panel-label{color:var(--rc-text-muted)}
.rc .sec.keystep{background:var(--rc-surface-key);border-top:0.5px solid var(--rc-border-key)}
.rc .rule{background:transparent;border:0;border-left:3px solid var(--rc-text-accent);
border-radius:0;padding:0 0 0 var(--rc-sp-4)}
.rc .rule .panel-label{color:var(--rc-text-accent);font-size:var(--rc-tx-micro);
letter-spacing:.1em;margin:0 0 var(--rc-sp-2)}
.rc .rule .rmain{font-size:var(--rc-tx-rule);color:var(--rc-text-primary);line-height:1.5;font-weight:600;
letter-spacing:-.01em}
.rc .rule .rground{font-size:var(--rc-tx-small);color:var(--rc-text-muted);line-height:1.6;
margin-top:var(--rc-sp-2)}
.rc .cavt{font-size:var(--rc-tx-small);color:var(--rc-text-muted);line-height:1.6;
border-top:0.5px solid var(--rc-border);padding-top:var(--rc-sp-2);margin-top:var(--rc-sp-3)}
.rc p+.cavt{margin-top:var(--rc-sp-3)}
.rc .rsub{font-size:var(--rc-tx-small);color:var(--rc-text-muted);line-height:1.55;
margin:var(--rc-sp-1) 0 var(--rc-sp-2);padding-left:var(--rc-sp-2)}
.rc .fnote{margin:var(--rc-sp-3) 0 0;border-top:0.5px solid var(--rc-border);padding-top:var(--rc-sp-2)}
.rc .fnote summary{font-size:var(--rc-tx-small);color:var(--rc-text-muted);cursor:pointer}
.rc .fnote p{font-size:var(--rc-tx-small);color:var(--rc-text-muted);margin:var(--rc-sp-1) 0 0}
.rc .fnote li{font-size:var(--rc-tx-small);color:var(--rc-text-muted);margin:var(--rc-sp-1) 0 0}
.rc .foot{font-size:var(--rc-tx-micro);color:var(--rc-text-muted);line-height:1.6;
background:var(--rc-surface-1);padding:var(--rc-sp-3) var(--rc-sp-6)}"""


def _sparkline_svg(card, copy=None):
    """Inline-SVG cumulative P&L sparkline from engine ``pnl_curve.points``,
    with a minimal peak/trough caption riding under it (#312).

    Renders only when at least two finite points exist.  Note-form or missing
    curve data omits the sparkline silently: card-spec forbids inventing a new
    user-facing caveat for it.  One thin line, colored only by the final sign,
    per the card-template design reference.  No external references, so the
    artifact stays request-free.

    The caption carried the curve's start and end dates until 2026-07-23.
    ``pnl_curve`` anchors its first point to the start of the review period
    (``trade_recap.pnl_curve``), so those dates restated the review window the
    keynote already leads with — the one-value-once rule.  It now names only
    the peak and trough, which no other element on the card carries, and no
    longer depends on the points having usable dates."""
    # Decorative field, fail-soft contract: any wrong-typed curve (adapter or
    # --card-json input) must omit the sparkline, never abort the render.
    curve = (card or {}).get("pnl_curve")
    if not isinstance(curve, dict) or curve.get("note"):
        return None
    points = curve.get("points")
    if not isinstance(points, list):
        return None
    values = []
    dates = []
    for point in points:
        if not isinstance(point, dict):
            continue
        number = _finite_number(point.get("cum_ret"))
        if number is None:
            continue
        values.append(number)
        date = point.get("date")
        dates.append(date if isinstance(date, str) and date.strip() else None)
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
    svg = (f'<svg class="spark {tone}" viewBox="0 0 {width:.0f} {height:.0f}" '
           f'preserveAspectRatio="none" aria-hidden="true"><path d="{path}"/></svg>')
    template = ((copy or {}).get("kpi") or {}).get("spark_caption")
    if not template:
        return svg
    try:
        # start/end stay available to any locale that still interpolates them,
        # but the shipped copy no longer does — see the docstring.
        caption = template.format(start=dates[0] or "", end=dates[-1] or "",
                                  peak=_signed_pct(high, digits=0),
                                  trough=_signed_pct(low, digits=0))
    except (KeyError, IndexError, ValueError):
        return svg
    return svg + f'<p class="cap">{html.escape(caption)}</p>'


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
             else _sparkline_svg(bundle.get("engine_card") or {}, copy))
    facts = structure["facts"]

    def _tile_html(tile):
        """One secondary metric box: label, value, and a sub capped at two lines.

        Grid rows stretch to their tallest cell, so a tile allowed to grow
        without bound pads every neighbour -- that is how a sparkline plus its
        caption once forced a whole row to 209px.  The two-line cap bounds the
        tallest cell instead of forbidding one particular field, which is what
        the narrower "the review window may not sit in a tile" ruling did;
        that one only blocked a single source of the same defect.  The sub
        wraps rather than truncating: dropping half of "realized X ·
        unrealized Y" loses a figure the reader needs."""
        tone = f' {tile["tone"]}' if tile.get("tone") else ""
        parts = []
        if tile.get("label"):
            parts.append(f'<p class="lbl">{e(tile["label"])}</p>')
        parts.append(f'<p class="val{tone}">{e(tile["value"])}</p>')
        if tile.get("sub"):
            parts.append(f'<p class="sub">{e(tile["sub"])}</p>')
        return '<div class="m">' + "".join(parts) + "</div>"

    def _curve_tile_html():
        """The period path as one more cell in the metric row.

        The curve is worth about as much as a single metric, so it gets a
        single metric's space -- not a hero band, and not a full-width strip.
        Standing beside the P&L figure it qualifies, it reads as the process
        behind that number rather than a decoration: the tiles state where the
        period ended, this cell states how it got there.

        It is deliberately the same three-part shape as every other tile
        (label, body, one-line sub) with the line occupying the value's slot
        at the value's height. The original defect was never "a chart sits in
        a tile" -- it was that this tile carried five parts where its
        neighbours carried three, and grid rows stretch to their tallest
        cell."""
        if not spark:
            return ""
        # ``_sparkline_svg`` returns the line optionally followed by its own
        # caption paragraph; the caption becomes this tile's sub line.
        line, _, caption_tail = spark.partition('<p class="cap">')
        label = ((copy.get("kpi") or {}).get("curve") or "").strip()
        parts = []
        if label:
            parts.append(f'<p class="lbl">{e(label)}</p>')
        parts.append(f'<div class="cval">{line}</div>')
        if caption_tail:
            # Re-tag it as a sub so it sits exactly where every other tile's
            # sub sits, keeping all cells the same height.
            parts.append('<p class="sub">' + caption_tail)
        return '<div class="m curve">' + "".join(parts) + "</div>"

    def kpi_block():
        """The metric row: the lit metrics plus the period curve as one cell.

        The column count is the number of cells that actually lit up, never a
        fixed four -- a month-gated review lights two metrics, and a hardcoded
        ``repeat(4,1fr)`` left more than half the row empty."""
        cells = []
        for tile in facts["kpi"]:
            cells.append(_tile_html(tile))
            # The curve traces cumulative P&L, so it follows that figure
            # directly. If no P&L metric lit this period it goes last, where
            # it still reads as the period's path but claims no neighbour.
            if tile.get("id") == "pnl":
                curve = _curve_tile_html()
                if curve:
                    cells.append(curve)
        if not any('class="m curve"' in cell for cell in cells):
            curve = _curve_tile_html()
            if curve:
                cells.append(curve)
        if not cells:
            return ""
        return f'<div class="kpi" data-n="{len(cells)}">' + "".join(cells) + "</div>"

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
        """Comparator rows for the alternative benchmarks.

        The headline figure is deliberately absent when the excess KPI tile
        rendered: it is the same number the tile already carries in full, and
        printing it again as a 19px display figure made a secondary fact the
        heaviest element in the block.  On a card with no excess tile (mixed
        market, month-gated, missing benchmark data) nothing else carries it,
        so the headline stays."""
        attribution = facts["attribution"]
        parts = []
        if "excess" not in {tile["id"] for tile in facts["kpi"]}:
            tone = f' {attribution["tone"]}' if attribution.get("tone") else ""
            parts.append(
                f'<p class="attr-head"><span class="big{tone}">{e(attribution["headline"])}</span></p>')
        for row in attribution["rows"]:
            parts.append(f'<div class="arow"><span class="al">vs {e(row["label"])}</span>'
                         f'<span class="av">{e(row["pp"])}</span></div>'
                         f'<div class="abar"><div style="width:{row["width_pct"]}%"></div></div>')
        return parts

    def indicator_items(items):
        # Mixed-market grouping (#276 2026-07-22 dogfood note): only label
        # when 2+ markets actually appear — a single-market card has nothing
        # to disambiguate, so it stays exactly as before. No item here is
        # ever kind "caveat" anymore (§4 2026-07-22 ruling): every honesty
        # sentence is footnoted instead, via the .fnote <details> below.
        # 2026-07-22 owner bullet pass: grouped vs-market lines (market key
        # set) share one <ul>/<li> list per market cluster, reusing the
        # existing .rc ul/li styling — no new CSS. Main Block-1 number lines
        # (no market key) stay plain <p>, exactly as before. Bullet these
        # market-tagged lines unconditionally (no _is_bulletable check):
        # they are engine-templated and always exactly one sentence by
        # construction, but full of decimal numbers (e.g. "β 1.10") that
        # _is_bulletable's sentence-counting would misread as a second
        # sentence — that check is for honesty text specifically
        # (digit-free by contract), not this.
        # #344: an item whose kpi_id names a tile that actually rendered on
        # this card (grid built above from the same facts["kpi"]) swaps in
        # html_text instead of its full text — "" drops the line entirely
        # (the tile already carries it in full), a shorter string keeps only
        # what the tile cannot hold. render_private never reads these two
        # keys, so Markdown (no tile grid) is untouched by this branch.
        tile_ids = {tile["id"] for tile in facts["kpi"]}
        parts = []
        markets = {item.get("market") for item in items if item.get("market")}
        current_market = None
        pending = []

        def flush():
            if pending:
                parts.append("<ul>" + "".join(f"<li>{e(x)}</li>" for x in pending) + "</ul>")
                pending.clear()

        for item in items:
            if item["kind"] == "attr_rows":
                # The attribution bars carry these comparator rows on HTML.
                continue
            market = item.get("market") if len(markets) > 1 else None
            if market != current_market:
                flush()
                if market:
                    parts.append(f'<p class="panel-label">[{e(market)}]</p>')
            current_market = market
            kpi_id = item.get("kpi_id")
            text = item.get("html_text") if kpi_id and kpi_id in tile_ids else item.get("text")
            if not text:
                continue
            if market:
                pending.append(text)
            else:
                flush()
                parts.append(f"<p>{e(text)}</p>")
        flush()
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
                # 2026-07-22 owner bullet pass: each disclosure renders as a
                # <li> (reusing the existing .rc ul/li styling — no new CSS);
                # _bulleted_html falls back to <p> for the hypothetical
                # non-bulletable (multi-sentence) entry, none of which exist
                # today (see _is_bulletable).
                label = copy.get("footnote_label", "")
                inner = _bulleted_html(block)
                rendered.append(f'<details class="fnote"><summary>{e(label)}</summary>'
                                f"{inner}</details>")
            elif kind == "rows":
                rendered.extend(instrument_bars(block))
            elif kind == "panel":
                rendered.append(panel_html(block))
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
            # Metrics first, then the story block: the prose keeps only what
            # a tile cannot hold (#344).
            metrics = kpi_block()
            if metrics:
                rendered.insert(0, metrics)
        # Block 4 is the card's single visual centre of gravity: the product
        # promises exactly one thing to change, so the section carrying it gets
        # its own ground while every other section shares the card surface.
        section_class = "sec keystep" if sid == "next" else "sec"
        body.append(f'<div class="{section_class}"><h2>{e(section["title"])}</h2>'
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
