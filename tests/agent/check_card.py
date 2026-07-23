#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_card.py — 卡面鐵律機檢(離線、確定性;docs/eval-design.md §4 A/B 系列可機檢部分)。

判定哲學(eval-design §1「code-check > LLM-judge > 人工」):能 regex / 子字串斷言的
絕不用 judge。本檔管「卡犯規沒」(純文字機檢),judge_narrative.py 管「敘事好不好」
(需 LLM)——兩支分工,別把機檢項丟給 judge(judge 非確定性、有成本)。

**斷言的單一權威**:A/B 系列 = docs/eval-design.md 編號(鐵律文本住 skills/fomo-kernel/
card-spec.md);S 系列(結構檢)= docs/output-contract.md §8。改那些文件的對應鐵律時,
同步這裡的 CHECKS——漂移防線見 eval-design §5。

S 系列只對 v2 renderer 私卡生效(front matter 含 privacy: private + language);
v1 人話卡 / 任意文字不出 S findings,舊 eval case 行為零變:
  S-1  keynote + 四大 block 標題齊且序正(標題取自 copy/<locale>.json blocks);
       第 5 個選填 block(結尾 synthesis,narrative.synthesis,#345)沒寫就不該
       出現——出現時標題必須恰為 copy.blocks.summary 且排在四大 block 之後,
       絕不能插在中間或取代任一個既有 block
  S-2  模組點亮與 §3 資料前提表一致(需 --context 給 card/state JSON;沒給則降級跳過)。
       vs_market 認月度 gate 訊號(engine_card.vs_market_gate,#284):gated 卡
       整段不出且無 gap note 才過;未 gated 而前提在,段落必須真的上卡
  S-3  caveat 佈局:不得連續 caveat 段、不得先於 Block 1、Block 1 內不得殘留任何
       inline caveat(2026-07-22 起全數收進 footnote,#276)
  S-4  語言規則(output-language.md §5):禁 IRR token、單句內禁混用數字風格

每條 check 回一個 Finding(assertion 編號 / passed / 證據原句)。任一 FAIL → CLI exit 1。
只驗**卡面輸出**,不驗 repo 文檔(A-13 同款限定)。

跑法:
  python3 tests/agent/check_card.py <card.md|-> [context.json]   # context = bundle/card/state JSON
  cat card.md | python3 tests/agent/check_card.py -
可 import:
  from check_card import check_card
  findings = check_card(card_text)                    # list[Finding];[f for f in findings if not f.passed]
  findings = check_card(card_text, context=json.load(open("bundle.json")))  # 啟用 S-2 資料對照
"""
import json
import math
import os
import re
import sys
from dataclasses import dataclass


@dataclass
class Finding:
    assertion: str          # eval-design 編號,如 "A-2"
    passed: bool
    label: str              # 這條在檢查什麼(人話)
    evidence: str = ""      # 扣分時引卡上原句;通過時留空

    def __str__(self) -> str:
        mark = "✅" if self.passed else "❌"
        tail = f"  → {self.evidence}" if (self.evidence and not self.passed) else ""
        return f"{mark} {self.assertion:<4} {self.label}{tail}"


# ── 內部 metric key 黑名單(A-12;card-spec 說話原則:工程內部名不上卡)──────────
#    bare "baseline" 曾在此表(2026-07-21 前):它是英文單字,會誤中合法卡面散文
#    ("...sets a baseline without forcing a commitment.")——真正的內部欄位名是
#    底線接壤的 `baseline_note`(review.py/coach.py 內部 commitment 記帳鍵,從未
#    上卡),改成這個 snake_case 全名才符合本表其餘項目「真的是內部識別碼」的前提。
_INTERNAL_KEYS = re.compile(
    r"max_pos_pct|ai_pct|avgdown_count|max_sector_pct|top3_pct|metric_key|baseline_note")

# ── 5 維 severity 小數表(A-2;card-spec 🚫「卡是故事不是 dashboard」)────────────
_SEVERITY_TABLE = re.compile(r"0?\.\d+ *[🔴🟡]")

# ── 標籤拼接 / 內部註記(A-3;同上)──────────────────────────────────────────────
_BRACKET_TAG = re.compile(r"〔.+〕")
_POINT_TAG = re.compile(r"← *點\d")
_LITERAL_TAGS = ("(引擎產出)", "(供參)", "（引擎產出）", "（供參）")

# ── 首段以勝率當主數字(A-6;card-spec 數字鐵律「金額 > 筆數勝率」)──────────────
_WINRATE = re.compile(r"勝率 *\d+ *%|\d+ *勝 *\d+ *負")

# ── 卡面半形標點夾在中日文字之間(A-13;全形統一,數字格式除外)────────────────
#    兩側都要 CJK,所以「$1,850,平均」(digit,CJK)不會誤判——數字格式天然被排除。
_HALFWIDTH_PUNCT = re.compile(r"[一-鿿][,:;][一-鿿]")

# ── 抽象規矩黑名單(B-7;「這 ChatGPT 也會講」的空泛句)──────────────────────────
#    與 judge_narrative.py RUBRIC 第 3 條同源;改一邊要同步另一邊。
_ABSTRACT_RULES = ("注意分散", "加碼前想清楚", "控制風險")

# ── 具體數字(B-9 proxy;金額 / 百分比 / 小數 / 千分位)────────────────────────────
#    B-9 原文是「最大的洞段落須含 ticker + 數字」;本機檢取全卡層的保守 proxy:
#    「卡須含 ≥1 具體數字」。純形容詞堆砌卡(零數字)必掛;section 級 ticker 定位與
#    黑話翻譯鄰近度留 case-spec / judge(見 README「這裡沒做的」)。
_CONCRETE_NUM = re.compile(r"\$[\d,]+|\d+ *%|\d+\.\d+|\d{1,3}(?:,\d{3})+")


# ═══════════════ S 系列(結構檢;權威 = docs/output-contract.md §8)═══════════════
# 只對 v2 renderer 私卡生效(front matter privacy: private)。標題 / 缺料 note 的
# 對照文本一律讀 copy/<locale>.json——checker 不自帶第二份 wording 事實源。

_COPY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "..", "skills", "fomo-kernel", "copy")
_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
# 卡面 caveat 的唯一形狀(renderer 契約):縮排 + 整行括號。
_CAVEAT_LINE_RE = re.compile(r"^[ \t]+[（(].*[)）][ \t]*$")
_IRR_TOKEN_RE = re.compile(r"\bIRR\b")
# 中文拼寫數量(混風格檢用;窄集,寧漏勿誤殺——完整判定住 card_renderer.numeric_claim)
_ZH_SPELLED_QTY_RE = re.compile(r"百分之[零〇一二兩三四五六七八九十百]"
                                r"|[零〇一二兩三四五六七八九十百千萬]+[成趴倍％]")
_EN_SPELLED_QTY_RE = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|"
    r"fifty|sixty|seventy|eighty|ninety|hundred|thousand)[\s-]+"
    r"(?:percent|percentage\s+points?|pp)\b", re.I)
_SENTENCE_SPLIT_RE = re.compile(r"[。．.!?！？;；]\s*|\n")


def _front_matter(text: str) -> dict:
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}
    fm = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fm[key.strip()] = value.strip()
    return fm


def _card_body(text: str) -> str:
    match = _FRONT_MATTER_RE.match(text)
    return text[match.end():] if match else text


def _load_copy(language) -> dict:
    name = "en" if str(language).lower().startswith("en") else "zh-TW"
    try:
        with open(os.path.join(_COPY_DIR, name + ".json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _context_card(context):
    """從 bundle / card / state JSON 撈 engine card(S-2 對照源);認不出 → None。"""
    if not isinstance(context, dict):
        return None
    if isinstance(context.get("engine_card"), dict):
        return context["engine_card"]
    if any(key in context for key in ("overview", "top_holes", "ticker_diagnosis",
                                      "alpha_beta_breakdown")):
        return context
    return None


def _s1_block_titles(body: str, copy: dict) -> "Finding":
    blocks = copy.get("blocks") or {}
    expected = [blocks.get(key) for key in ("performance", "trades", "risks", "next")]
    if not all(expected):
        return Finding("S-1", False, "keynote + 四大 block 齊且序正",
                       "copy 檔缺 blocks.* 標題,無從對照")
    titles = [line[3:].strip() for line in body.splitlines() if line.startswith("## ")]
    # snapshot 路線的 Block 1 標題採 snapshot_numbers 覆寫(§3 snapshot row)。
    snapshot_first = copy.get("snapshot_numbers")
    ok_first = titles[:1] and titles[0] in {expected[0], snapshot_first}
    core_ok = bool(ok_first) and titles[1:4] == expected[1:]
    # #345: an optional 5th block (closing synthesis) may follow Next step
    # when narrative.synthesis is authored — output-contract.md §2. It never
    # replaces or reorders the four mandatory blocks above, so the only two
    # legal title-list lengths are 4 (absent) or 5 (present, and its title
    # must be exactly copy.blocks.summary — never any other trailing title).
    summary_title = blocks.get("summary")
    tail_ok = len(titles) == 4 or (len(titles) == 5 and bool(summary_title)
                                    and titles[4] == summary_title)
    ok = core_ok and tail_ok
    headline = any(line.startswith("# ") for line in body.splitlines())
    evidence = "" if (ok and headline) else f"標題序:{titles!r}" + ("" if headline else ";缺 keynote(# 行)")
    return Finding("S-1", ok and headline, "keynote + 四大 block 齊且序正", evidence)



# vs-market 段落上卡的機檢 needle(S-2 用):鏡照 card_renderer._private_benchmark_line
# 的 headline 句形——段落一出必有這行(單一與逐市場皆同款式)。與 _CAVEAT_LINE_RE
# 同類的 renderer 契約形狀;改 renderer 句形要同步這裡。
# 2026-07-23(#363「一個概念一個指標」):句形從「…相差 +N 個百分點」改為
# 「…的超額報酬 +N 個百分點」——兩個絕對報酬(port_tot/spy_tot)已退回內部,
# 不再上卡,needle 跟著改抓超額本身。
_VS_SEGMENT_ZH_RE = re.compile(r"超額報酬 [+-]\d+ 個百分點")
_VS_SEGMENT_EN_RE = re.compile(r"beat .+ by [+-]\d+ pp")


def _s2_module_lighting(body: str, copy: dict, context, language) -> "Finding":
    card = _context_card(context)
    if card is None:
        return Finding("S-2", True, "模組點亮 vs 資料前提表(未給 context,降級跳過)")
    missing_copy = copy.get("block_missing") or {}
    if not missing_copy:
        return Finding("S-2", False, "模組點亮 vs 資料前提表", "copy 檔缺 block_missing.*")
    if isinstance(card.get("snapshot_summary"), dict):
        # snapshot 路線刻意壓掉歷史績效模組(§3 最後一列),缺料 note 不適用。
        return Finding("S-2", True, "模組點亮 vs 資料前提表(snapshot 路線,依約壓掉歷史模組)")
    overview = card.get("overview") or {}
    pnl_rows = ((card.get("currency_meta") or {}).get("pnl_by_currency") or {})
    has_abs = (any(_finite(overview.get(key)) is not None
                   for key in ("total_pnl", "realized", "unrealized"))
               or any(_finite((row or {}).get("realized")) is not None
                      or _finite((row or {}).get("unrealized")) is not None
                      for row in pnl_rows.values()))
    has_ann = _finite((card.get("acct_perf") or {}).get("hold_twr")) is not None
    ab = card.get("alpha_beta_breakdown") or {}
    by_market = ab.get("by_market")
    if isinstance(by_market, dict) and by_market:
        has_vs = any(isinstance(row, dict) and not row.get("note")
                     and all(_finite(row.get(key)) is not None
                             for key in ("port_tot", "spy_tot", "excess_vs_spy"))
                     for row in by_market.values())
    else:
        has_vs = (isinstance(ab, dict) and not ab.get("note")
                  and all(_finite(ab.get(key)) is not None
                          for key in ("port_tot", "spy_tot", "excess_vs_spy")))
    diagnosed = [row for row in (card.get("ticker_diagnosis") or [])
                 if isinstance(row, dict) and _finite(row.get("impact"))]
    rows_indeterminate = bool((card.get("currency_meta") or {}).get("mixed"))
    has_rows = len(diagnosed) >= 2
    has_diag = bool(card.get("top_holes") or card.get("dims_raw"))

    problems = []
    # #289/#321: when price retrieval was blocked, the renderer swaps in the
    # *_prices variant of the same missing-data note — either variant counts
    # as "the note was shown" (both directions of the check).
    # #375: the annualized gap note now has one variant per structured engine
    # gate status (card_renderer.ANNUALIZED_GAP_NOTE_BY_GATE). Any of them
    # counts as "the note was shown" — a variant missing from this tuple would
    # make the checker report a silent omission that did not happen.
    for key, lit, note_keys in (("absolute_pnl", has_abs, ("absolute_pnl",)),
                                ("annualized", has_ann,
                                 ("annualized", "annualized_prices",
                                  "annualized_short_series", "annualized_reconciliation")),
                                ("risks", has_diag, ("risks",))):
        notes = [missing_copy.get(note_key) or "" for note_key in note_keys]
        shown = any(note and note in body for note in notes)
        if not lit and not shown:
            problems.append(f"{key}: 前提缺但缺料 note 沒出(靜默省略)")
        if lit and shown:
            problems.append(f"{key}: 前提在但卡上出了缺料 note")
    # vs_market 認月度 gate 訊號(#284,§3 monthly cadence):prepare 凍進
    # engine_card.vs_market_gate。gated → 整段與缺料 note 都不得出(無 gap note);
    # 未 gated → 缺料 note 兩向照舊,且前提在時段落必須真的上卡(嚴防靜默漏段)。
    gate = card.get("vs_market_gate")
    vs_gated = isinstance(gate, dict) and gate.get("render") is False
    vs_notes = [missing_copy.get(note_key) or ""
                for note_key in ("vs_market", "vs_market_prices")]
    vs_note_shown = any(note and note in body for note in vs_notes)
    segment_re = (_VS_SEGMENT_EN_RE if str(language).lower().startswith("en")
                  else _VS_SEGMENT_ZH_RE)
    vs_segment_shown = bool(segment_re.search(body))
    if vs_gated:
        if vs_segment_shown:
            problems.append("vs_market: 月度 gate 壓掉的段落仍上卡")
        if vs_note_shown:
            problems.append("vs_market: 月度 gate 期不得出缺料 note(§3:整段直接不出、無 gap note)")
    else:
        if not has_vs and not vs_note_shown:
            problems.append("vs_market: 前提缺但缺料 note 沒出(靜默省略)")
        if has_vs and vs_note_shown:
            problems.append("vs_market: 前提在但卡上出了缺料 note")
        if has_vs and not vs_segment_shown:
            problems.append("vs_market: 前提在且未被 gate,但 vs-market 段沒上卡")
    # Both trades variants (with/without a traded-ticker list) share this stem.
    trades_needle = (missing_copy.get("trades") or "").rstrip("。.")
    trades_note_shown = bool(trades_needle) and trades_needle in body
    if not has_rows and not rows_indeterminate and not trades_note_shown:
        problems.append("trades: 標的列前提缺但缺料 note 沒出")
    if has_rows and not rows_indeterminate and trades_note_shown:
        problems.append("trades: 標的列前提在但卡上出了缺料 note")
    return Finding("S-2", not problems, "模組點亮 vs 資料前提表(§3)", "; ".join(problems))


def _s3_caveat_placement(body: str) -> "Finding":
    lines = body.splitlines()
    caveat_indices = [index for index, line in enumerate(lines)
                      if _CAVEAT_LINE_RE.match(line)]
    problems = []
    adjacent = next((lines[b] for a, b in zip(caveat_indices, caveat_indices[1:])
                     if b - a == 1), None)
    if adjacent:
        problems.append(f"連續 caveat 段:{adjacent.strip()}")
    header_indices = [index for index, line in enumerate(lines) if line.startswith("## ")]
    if header_indices:
        block1 = header_indices[0]
        before = next((lines[i] for i in caveat_indices if i < block1), None)
        if before is not None:
            problems.append(f"Block 1 前(keynote 區)出現 caveat:{before.strip()}")
        # 2026-07-22 ruling (output-contract.md §4/§9, #276): caveats no
        # longer ride Block-1 indicators at all — every triggered honesty
        # sentence collapses into the footnote instead. Any caveat-shaped
        # line inside Block 1 (first line or not, stacked or not) is now a
        # structural violation on its own, superseding the narrower
        # first-line-only check this replaced.
        block1_end = header_indices[1] if len(header_indices) > 1 else len(lines)
        inline = next((lines[i] for i in caveat_indices if block1 < i < block1_end), None)
        if inline is not None:
            problems.append(f"Block 1 內出現 inline caveat(應集中 footnote):{inline.strip()}")
    return Finding("S-3", not problems,
                   "caveat 佈局:不連續、不先於 Block 1 指標、Block 1 內不得有 inline caveat",
                   "; ".join(problems))


def _s4_language_rules(body: str, language) -> "Finding":
    problems = []
    irr = _IRR_TOKEN_RE.search(body)
    if irr:
        problems.append("IRR token 上卡(須用「年化報酬」/ annualized return)")
    spelled = (_ZH_SPELLED_QTY_RE if not str(language).lower().startswith("en")
               else _EN_SPELLED_QTY_RE)
    mixed = next((s.strip() for s in _SENTENCE_SPLIT_RE.split(body)
                  if s and re.search(r"\d", s) and spelled.search(s)), None)
    if mixed:
        problems.append(f"單句混用數字風格:{mixed[:60]}")
    return Finding("S-4", not problems,
                   "語言規則:無 IRR token、單句單一數字風格", "; ".join(problems))


def check_structure(text: str, context=None) -> list["Finding"]:
    """S 系列(output-contract §8)。只對 v2 私卡出 findings;其餘回空 list。"""
    fm = _front_matter(text)
    if fm.get("privacy") != "private" or not fm.get("language"):
        return []
    body = _card_body(text)
    copy = _load_copy(fm["language"])
    return [
        _s1_block_titles(body, copy),
        _s2_module_lighting(body, copy, context, fm["language"]),
        _s3_caveat_placement(body),
        _s4_language_rules(body, fm["language"]),
    ]


def _first_paragraph(text: str) -> str:
    """首段 = 第一個非空白區塊(到第一個空行為止)。A-6 只看首段。"""
    for block in re.split(r"\n\s*\n", text.strip()):
        if block.strip():
            return block.strip()
    return ""


def check_card(text: str, context=None) -> list[Finding]:
    """對一張卡跑全部離線鐵律機檢。回 list[Finding](含通過項,方便呈現全貌)。

    ``context``(選填)= bundle / engine card / state 的已解析 JSON,供 S-2 對照
    §3 資料前提表;不給則 S-2 降級跳過。S 系列只對 v2 私卡出 findings。"""
    findings: list[Finding] = []

    # A-2 5 維 severity 小數表
    m = _SEVERITY_TABLE.search(text)
    findings.append(Finding("A-2", m is None,
                            "無 5 維 severity 小數表(0.71 🔴 這類)",
                            m.group(0) if m else ""))

    # A-3 標籤拼接 / 內部註記
    tag_hit = (_BRACKET_TAG.search(text) or _POINT_TAG.search(text)
               or next((t for t in _LITERAL_TAGS if t in text), None))
    ev = tag_hit if isinstance(tag_hit, str) else (tag_hit.group(0) if tag_hit else "")
    findings.append(Finding("A-3", not tag_hit,
                            "無標籤拼接〔…〕/ ←點n /(引擎產出)/(供參)", ev))

    # A-6 首段以勝率當主數字
    m = _WINRATE.search(_first_paragraph(text))
    findings.append(Finding("A-6", m is None,
                            "首段不以勝率 / 勝負筆數當主數字",
                            m.group(0) if m else ""))

    # A-12 內部 metric key 外洩
    m = _INTERNAL_KEYS.search(text)
    findings.append(Finding("A-12", m is None,
                            "卡面無工程內部 metric key(max_pos_pct 這類)",
                            m.group(0) if m else ""))

    # A-13 半形標點夾中日文字
    m = _HALFWIDTH_PUNCT.search(text)
    findings.append(Finding("A-13", m is None,
                            "中文字間標點全形統一(數字格式除外)",
                            m.group(0) if m else ""))

    # B-7 抽象規矩黑名單
    hit = next((p for p in _ABSTRACT_RULES if p in text), None)
    findings.append(Finding("B-7", hit is None,
                            "規矩不是空泛黑名單句(注意分散 / 控制風險 這類)",
                            hit or ""))

    # B-9(proxy)卡須含具體數字
    has_num = bool(_CONCRETE_NUM.search(text))
    findings.append(Finding("B-9", has_num,
                            "卡含 ≥1 具體數字(非純形容詞堆砌)",
                            "" if has_num else "全卡查無金額 / % / 小數"))

    # S 系列(v2 私卡限定;非 v2 文字回空 list,不影響舊 eval case)
    findings.extend(check_structure(text, context))

    return findings


def _main() -> int:
    if len(sys.argv) not in (2, 3):
        print(f"用法: {sys.argv[0]} <card.md|-> [context.json]", file=sys.stderr)
        return 2
    if sys.argv[1] == "-":
        text = sys.stdin.read()
    else:
        with open(sys.argv[1], encoding="utf-8") as f:
            text = f.read()
    context = None
    if len(sys.argv) == 3:
        with open(sys.argv[2], encoding="utf-8") as f:
            context = json.load(f)

    findings = check_card(text, context)
    for f in findings:
        print(f)
    failed = [f for f in findings if not f.passed]
    print()
    if failed:
        print(f"❌ {len(failed)}/{len(findings)} 條鐵律被踩:"
              f"{', '.join(f.assertion for f in failed)}")
        return 1
    print(f"✅ 全部 {len(findings)} 條卡面鐵律通過。")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
