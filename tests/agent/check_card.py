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

B-9(#542 升級)是 section-scoped:規則是「biggest-hole 段落本身要有具體數字,且
——只在這個洞本身是 per-ticker 事實時——要點名一個真標的」,不是「全卡任一處有
數字就算」。三層退化,見 check_card() 的 B-9 區塊:(1) 定位不到 risks 區 `[X]`
hole panel → 退回全卡層舊 proxy;(2) 定位得到且 context 給得出這個洞自己的權威
ticker 判定(exit_discipline/diversification 這類天生無標的的洞不強求 ticker,
只查數字)→ 精準版;(3) 定位得到但沒有 context → 啟發式退化,改用 Block 2(關鍵
交易)逐檔列的 ticker 當 proxy 池,池也空時退回第 1 層同款的全卡層 proxy。三層都
不誤殺這條規則從未設計要管的卡面形狀——這個邊界是拿 tests/persona_sweep.py 的
156 個真卡跑出來的,不是憑空猜的:第一版只做兩層就讓 108/468 張真卡假警報。

B-1(#542)不在 check_card() 的固定清單裡:它需要 case 自己宣告「洗白標的是哪一檔、
允許/禁止標籤各是什麼」,這是 case 資料而非 checker 常數,所以是獨立函式
check_ticker_diagnosis(),由呼叫端(run_case.sh --headless 讀 case.yaml 的
subject_ticker/subject_forbidden_tags/subject_allowed_tags 三欄)決定何時、用什麼
參數呼叫——見 cases/washer.yaml。標籤比對讀 copy/<locale>.json 的 instrument_tags
(card_renderer.localized_instrument_tag 的同一份目錄),不是卡面字面詞的第二份手抄本。

跑法:
  python3 tests/agent/check_card.py <card.md|-> [context.json]   # context = bundle/card/state JSON
  cat card.md | python3 tests/agent/check_card.py -
  python3 tests/agent/check_card.py <card.md> --ticker INTC \
      --forbidden-tags suspected_dca \
      --allowed-tags suspected_averaging_down_losing,adds_pending_confirmation   # 加驗 B-1
可 import:
  from check_card import check_card, check_ticker_diagnosis
  findings = check_card(card_text)                    # list[Finding];[f for f in findings if not f.passed]
  findings = check_card(card_text, context=json.load(open("bundle.json")))  # 啟用 S-2 資料對照
  finding = check_ticker_diagnosis(card_text, "INTC",
                                   forbidden_tags=["suspected_dca"],
                                   allowed_tags=["suspected_averaging_down_losing"])  # B-1
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
#    #671:舊版是 `[一-鿿][,:;][一-鿿]`——列舉三個字元、且要求標點「緊鄰」兩側都是
#    CJK。兩個洞都被實際卡片踩到:半形括號根本不在字元類裡;而 `…站得住);但…` 裡
#    `;` 的左鄰是 `)` 不是 CJK,那個半形括號等於幫半形分號擋掉了檢查。
#    改成命名機制:任一個半形標點,只要「跨過中間其他半形標點與空白之後」左右都還
#    在同一段 CJK 文字裡,就是違規。全形標點是句讀邊界——跨過它就是另一個子句了,
#    不算同一段(否則整段中文裡任何一個英文括號都會被連坐)。
#    數字格式仍天然豁免:兩側緊鄰都是數字的(千分位 1,850、時間 12:30)直接跳過。
_HALFWIDTH_MARKS = ",;:!?()"
_CJK_RUN_CHAR = re.compile(r"[一-鿿぀-ヿ㐀-䶿]")
_CJK_RUN_STOPPERS = "。，、；：！？（）「」『』《》…\n"


def _cjk_on_side(text, index, step):
    """True when the nearest meaningful character on that side is CJK."""
    i = index + step
    while 0 <= i < len(text):
        ch = text[i]
        if _CJK_RUN_CHAR.match(ch):
            return True
        if ch in _CJK_RUN_STOPPERS:
            return False
        i += step
    return False


def halfwidth_in_cjk_run(text):
    """The offending fragment, or None. Public: the catalog gate reads it too."""
    for i, ch in enumerate(text or ""):
        if ch not in _HALFWIDTH_MARKS:
            continue
        if text[i - 1:i].isdigit() and text[i + 1:i + 2].isdigit():
            continue
        if _cjk_on_side(text, i, -1) and _cjk_on_side(text, i, 1):
            return text[max(0, i - 6):i + 7]
    return None

# ── 抽象規矩黑名單(B-7;「這 ChatGPT 也會講」的空泛句)──────────────────────────
#    與 judge_narrative.py RUBRIC 第 3 條同源;改一邊要同步另一邊。
_ABSTRACT_RULES = ("注意分散", "加碼前想清楚", "控制風險")

# ── 具體數字(B-9 降級 proxy;金額 / 百分比 / 小數 / 千分位)──────────────────────
#    定位不到 hole 段落或 ticker 池時的全卡層降級路徑用這條(見 check_card() 的
#    B-9 區塊)——維持原本「須是有格式的數字」門檻,避免卡面別處一個孤立的年份
#    數字(如 session_id、日期片段)就矇混過關。
_CONCRETE_NUM = re.compile(r"\$[\d,]+|\d+ *%|\d+\.\d+|\d{1,3}(?:,\d{3})+")

# ── 具體數字(B-9 section-scoped 精準版;#542)──────────────────────────────────
#    B-9 原文是「最大的洞段落須含 ticker + 數字」。一旦真的把搜尋範圍收斂到 hole
#    段落自己的文字,門檻可以比 _CONCRETE_NUM 寬:card_renderer 的 hole_lines 模板
#    (holding_period/exit_discipline 那幾支)本就會印純整數計次/天數("median 6
#    days"、"2 of 2 names")而不夾 $ 或 %,而敘事欄位(narrative.*)有引擎端的
#    digit-ban 鐵律(tests/test_digit_ban.py)——這個範圍裡任何數字都保證是引擎
#    模板填的事實,不是自由文字混進來的雜訊,所以「有沒有數字」比「數字長什麼
#    形狀」更準。persona sweep 抓到的第二個假陽性:holding_period 的洞句子全是
#    純整數,舊門檻在這個範圍內反而搜不到。
_HOLE_SECTION_NUM = re.compile(r"\d")

# ── ticker 的字面形狀(#542;單一宣告,_ROW_TICKER_RE 與 _TICKER_TOKEN_RE 都從這裡
#    組,不各自刻一份)。三種形狀取自 schemas/price-feed.schema.json 的 ticker
#    pattern 本身給的例子(NVDA / 2330.TW / ^TWII)——那份 schema 的 pattern 本身
#    太寬(任何 ≤20 字元英數字都合法,連 "Historical"、"ETF" 這種散文詞也會過),
#    不能直接拿來當「這個 token 是不是 ticker」的判斷式,所以這裡窄化成美股
#    (1-5 碼大寫英數,選填 .A/.B 股別後綴)/台股(2-6 碼數字 + .TW 或 .TWO)/
#    指數(^ 開頭)三種實際會出現的形狀:
_TICKER_SHAPE = r"[A-Z][A-Z0-9]{0,4}(?:\.[A-Z]{1,3})?|\d{2,6}\.TWO?|\^[A-Z]{2,6}"

# ── B-9 的 ticker 池(#542;Block 2「關鍵交易」逐檔列的形狀:card_renderer 的
#    'rows' kind 一律印成 "- TICKER amount(tags)")────────────────────────────
_ROW_TICKER_RE = re.compile(rf"^- ({_TICKER_SHAPE})\b")

# ── copy 模板的 {placeholder} 記法(#542;B-1 的 instrument_tags 比對、B-9 的
#    hole_lines 靜態模板判定,兩處共用同一份「模板轉 regex / 認出無 placeholder
#    模板」邏輯,不各自手刻一份)───────────────────────────────────────────────
_PLACEHOLDER_RE = re.compile(r"\{[^{}]+\}")

# ── B-9 靜態 hole_lines 模板的最短長度門檻(#542)────────────────────────────────
#    hole_lines 字典裡混著兩把「接續字元」(joiner="; "、ticker_joiner=", "),
#    不是句子模板,卻一樣無 placeholder;不設下限,任何含逗號空格的普通句子都會
#    被誤認成「這句是零數字的已知模板」而免試數字。兩支真正的靜態句子模板都遠超
#    40 字元,10 已足夠把兩支接續字元擋在外面又不誤傷未來可能加入的短句模板。
_MIN_TEMPLATE_LEN = 10


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


# ═══════════════ #542:section-scope 定位(B-1 + B-9 共用)═══════════════
# 兩條斷言都需要「在卡面文字裡找到某個結構化區塊」,而不是整卡亂搜。共用同一套
# 定位邏輯,讀 copy/<locale>.json 的 blocks/sections/instrument_tags 當唯一權威
# (card_renderer._card_structure / _trades_block / _risks_block 產生這些區塊時
# 用的正是同一份目錄),不在這裡另建一份手抄的中英文字面詞黑名單。

def _section_span(lines: list, title: str):
    """``## <title>`` 標題到下一個 ``## `` 標題(或檔尾)之間的行,不含標題本身。
    ``title`` 為空或找不到該標題 → None(讓呼叫端分得出「這張卡沒有這個 block」
    跟「這個 block 是空的」——空 list 是後者)。"""
    if not title:
        return None
    start = next((i for i, ln in enumerate(lines) if ln == f"## {title}"), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    return lines[start + 1:end]


def _hole_section(body: str):
    """biggest-hole 段落自己的文字範圍:從 ``[X] <hole 標籤>`` 那一行開始,到下一個
    空行或下一個 ``[`` 開頭的 panel 標記為止(card_renderer._risks_block 的
    ``style: "hole"`` panel,mark ``X``)。找不到 → None(v1 人話卡、手工測試字串、
    或這期真的沒有洞的卡——呼叫端一律退回 proxy,不誤判這些從來不是這條規則要管
    的形狀)。中英兩個 locale 的標籤都試,因為呼叫端不見得知道卡的語言。"""
    lines = body.splitlines()
    for lang in ("zh-TW", "en"):
        label = ((_load_copy(lang).get("sections") or {}).get("hole") or "").strip()
        if not label:
            continue
        needle = f"[X] {label}"
        start = next((i for i, ln in enumerate(lines) if ln.startswith(needle)), None)
        if start is None:
            continue
        end = start + 1
        while end < len(lines) and lines[end].strip() and not lines[end].startswith("["):
            end += 1
        return "\n".join(lines[start:end])
    return None


def _hole_number_optional(hole_section: str) -> bool:
    """True when this hole's own rendered text matches one of the small set
    of copy ``hole_lines`` templates that carry **zero** ``{placeholder}``
    tokens at all (currently ``holding_no_data`` / ``holding_same_day`` in
    both locales) — a template with no placeholder can never render a
    number, by construction of the catalog itself, so B-9's number
    requirement is vacuous for it (#542: persona sweep found a real
    ``all completed trades were same-day; no overnight holding`` hole with
    zero digits, correctly grounded but numberless by design). Reads the
    catalog to find which templates are placeholder-free instead of hard-
    coding the two dimension names — a template that later gains a
    placeholder loses this exemption automatically, and a new numberless
    template gains it automatically.

    ``hole_lines`` also carries two structural join strings (``joiner``,
    ``ticker_joiner`` — ``"; "`` / ``", "``), not sentence templates; they
    are placeholder-free too, and being one or two characters they substring
    -match almost any prose (a real bug this function shipped with once
    already — a plain "no ticker, no number" sentence like "winners were
    held far longer than losers" was wrongly exempted because it happens to
    contain ", "). ``_MIN_TEMPLATE_LEN`` excludes both by their shape rather
    than by name, so a future short structural key added to the same dict
    does not reopen this hole."""
    for lang in ("zh-TW", "en"):
        for template in (_load_copy(lang).get("hole_lines") or {}).values():
            if (isinstance(template, str) and len(template) >= _MIN_TEMPLATE_LEN
                    and not _PLACEHOLDER_RE.search(template) and template in hole_section):
                return True
    return False


def _known_tickers(body: str) -> set:
    """這張卡在 Block 2(關鍵交易)裡真的列出逐檔列的 ticker 集合——B-9 拿來驗證
    hole 段落點名的是不是一個真標的,而不是任何一個大寫字母就算數。Block 2 沒有
    逐檔列(缺價降級、或診斷檔數 < 2)時回空集合。

    這是個**下界**,不是完整清單:Block 2 的逐檔列只認有現價 / 有非零已實現+未
    實現損益的標的(card_renderer._instrument_rows),而 biggest-hole 段落點名的
    ticker 來自完全不同的引擎算式——加碼攤平之類的行為維度是從原始交易紀錄算
    的,不需要現價,兩者常常不是同一組標的(#542 修復:persona sweep 抓到一張
    「Block 2 只有 CVX/F 兩檔有現價,但 hole 段落正確點名的是完全不同的
    CVS/INTC/PYPL 三檔」的真卡,若只信 Block 2 這個池會把正確的卡誤判成違規)。
    真正的權威來源是 _top_hole_tickers() 讀的「這個洞自己的」raw 資料——這裡
    只在 context 沒給(B-1 的情境;B-1 不需要 context)或想額外擴大接受範圍
    (精準版 union 這個池,見 check_card())時當輔助訊號。"""
    lines = body.splitlines()
    tickers = set()
    for lang in ("zh-TW", "en"):
        title = ((_load_copy(lang).get("blocks") or {}).get("trades") or "").strip()
        for ln in _section_span(lines, title) or []:
            m = _ROW_TICKER_RE.match(ln)
            if m:
                tickers.add(m.group(1))
    return tickers


_TICKER_TOKEN_RE = re.compile(rf"^(?:{_TICKER_SHAPE})$")  # whole-string match, not a substring search


def _raw_tickers(value, out: set) -> None:
    """遞迴收集看起來像 ticker 的字串(dict 的 key 和純字串 value 都算),來源限定
    在引擎維度自己的 raw 資料(dims_raw / top_holes[].raw)。這份資料只有 dim 名、
    severity/tier 這類數字、和標的相關欄位(tickers/ticker_counts/max_ticker/
    risk_weights/incon_tickers/...)——不像整張 card JSON 那樣還混著 currency_meta
    的貨幣代碼(USD 這種三個大寫字母的假陽性來源),所以直接掃這個範圍夠精準,
    不用為每個維度各自認欄位名(那會變成 _hole_line() 那份 dim → 欄位對照的
    第二份手抄本,development-guide.md §7 點名的重複推導)。"""
    if isinstance(value, str):
        if _TICKER_TOKEN_RE.match(value):
            out.add(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and _TICKER_TOKEN_RE.match(k):
                out.add(k)
            _raw_tickers(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _raw_tickers(v, out)


def _top_hole_tickers(context):
    """B-9 的權威 ticker 判定:從 context(bundle / engine card JSON,S-2 同一個
    _context_card 來源)裡,只讀**最大那個洞自己**(``top_holes[0]``)的 raw 資料——
    跟 card_renderer._hole_line() 組那句話用的是同一份資料,而不是從卡面文字
    另猜一次。

    刻意只看 top_holes[0],不 union 其他維度:ticker 要不要求,是「這個洞本身
    是不是 per-ticker 事實」的問題,不是「這張卡別處有沒有標的」——
    exit_discipline / diversification 這兩種洞天生沒有 ticker 欄位(前者只講
    天數/勝率,後者講的是 sector 不是個股),若把所有維度的 ticker 混在一起當
    池,會變成「卡上任一處有標的」就通過,連 exit_discipline 這種洞該不該被
    要求都測不出來;更糟的是反過來——union 池非空但這個洞真的沒有 ticker 時,
    會誤判成「該有 ticker 卻沒引用」而錯殺(#542:persona sweep 對 fundamental
    persona 的真卡抓到這個回歸,exit_discipline 洞本身零標的,卻因為 Block 2
    的逐檔列非空被要求引用)。

    回傳 ``None``(不是空集合)代表「context 沒給可用的 top_holes 資料,判不了
    這個洞是不是 per-ticker」——呼叫端據此退回啟發式;回傳空集合則是**權威**
    結論「這個洞的原始資料裡沒有任何 ticker 欄位」,呼叫端不該再拿別處的標的
    池湊數。只看 top_holes[0] 而非重新套用 card_renderer._applicable_holes 的
    篩選,是刻意的簡化:那個篩選需要 dimension_id() 等 card_renderer 內部函式,
    這支檔案刻意不 import renderer 邏輯(維持 checker 是獨立 oracle);[0] 在
    絕大多數卡上就是真正被渲染的洞,篩選生效的少數情況最多只是少要求一次
    ticker,不會誤殺好卡。"""
    card = _context_card(context)
    holes = [h for h in ((card or {}).get("top_holes") or []) if isinstance(h, dict)]
    if not holes:
        return None
    out: set = set()
    _raw_tickers(holes[0].get("raw") or {}, out)
    return out


def _ticker_diagnosis_block(body: str, ticker: str):
    """一檔標的在 Block 2 自己的列 + 緊接著的縮排子行(card_renderer._trades_block
    / render_private 的 'rows' kind 產生的確切形狀:``- TICKER amount(tags)``
    後面跟著 ``  - sub`` 們)。這張卡在 Block 2 裡沒有這檔的列 → None——B-1 把
    這視為失敗而非靜默放行(見 check_ticker_diagnosis)。"""
    lines = body.splitlines()
    for lang in ("zh-TW", "en"):
        title = ((_load_copy(lang).get("blocks") or {}).get("trades") or "").strip()
        span = _section_span(lines, title) if title else None
        if not span:
            continue
        start = next((i for i, ln in enumerate(span)
                      if re.match(rf"^- {re.escape(ticker)}\b", ln)), None)
        if start is None:
            continue
        end = start + 1
        while end < len(span) and span[end].startswith("  "):
            end += 1
        return "\n".join(span[start:end])
    return None


def _tag_needle_pattern(code: str):
    """把 copy ``instrument_tags[code]`` 的模板(card_renderer.
    localized_instrument_tag 解析的同一份目錄)編成一條能在兩個 locale 都比對的
    regex,而不是把渲染後的字面詞第二次手抄進這支檔案。``{placeholder}`` 換成
    non-greedy 萬用字元,不管引擎最後填了什麼數字都比對得到。未知 code 直接
    raise——這是呼叫端(case 宣告)的設定錯誤,不該被靜默吞掉變成一條永遠通過的
    死斷言。"""
    alternatives = []
    for lang in ("zh-TW", "en"):
        template = (_load_copy(lang).get("instrument_tags") or {}).get(code)
        if not template:
            continue
        parts = _PLACEHOLDER_RE.split(template)
        alternatives.append(".*?".join(re.escape(p) for p in parts))
    if not alternatives:
        raise ValueError(f"unknown instrument_tags code: {code!r}")
    return re.compile("|".join(f"(?:{a})" for a in alternatives), re.S)


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
    m = halfwidth_in_cjk_run(text)
    findings.append(Finding("A-13", m is None,
                            "中文字間標點全形統一(數字格式除外)",
                            m or ""))

    # B-7 抽象規矩黑名單
    hit = next((p for p in _ABSTRACT_RULES if p in text), None)
    findings.append(Finding("B-7", hit is None,
                            "規矩不是空泛黑名單句(注意分散 / 控制風險 這類)",
                            hit or ""))

    # B-9 biggest-hole 段落本身要有具體數字,且——只在這個洞本身是 per-ticker
    # 事實時——要點名一個真標的(#542 升級,見檔頭說明)。三層,退化式:
    #
    #   1. 定位不到 hole 段落 → 整段規則不適用,退回全卡層舊 proxy(v1 卡、
    #      手工測試字串、真的沒有洞的卡)。
    #   2. 定位得到,且 context 給得出「這個洞自己」的權威 ticker 判定
    #      (_top_hole_tickers 非 None)→ 精準版:數字必查;ticker 只在這個洞
    #      的原始資料真的有標的欄位時才要求,exit_discipline/diversification
    #      這類天生無標的的洞不強求(#542 第二個回歸:persona sweep 對
    #      fundamental persona 的真卡抓到——那張卡的洞是 exit_discipline,
    #      零標的,卻因為 Block 2 逐檔列非空被誤判成「該引用卻沒引用」)。
    #   3. 定位得到,但沒有 context 可判權威(_top_hole_tickers 回 None)→
    #      啟發式退化:改用 Block 2(關鍵交易)逐檔列的 ticker 當 proxy 池
    #      (見 _known_tickers 的下界說明——這一路的已知限制:B-1 不需要
    #      context,所以走這條路;真正的卡面產線(persona sweep 等)一律有
    #      context,精準版才是主路徑)。Block 2 也拿不到任何標的時,退回第 1
    #      層同款的全卡層 proxy。
    #
    # 數字門檻用 _HOLE_SECTION_NUM(裸數字)而非 _CONCRETE_NUM:一旦鎖定在
    # hole 段落自己的文字裡,任何數字都保證是引擎模板填的事實(敘事欄位有
    # digit-ban 鐵律),裸整數(holding_period 那類「6 天」)不該被漏接。
    # _hole_number_optional() 再放行一種形狀:少數 hole_lines 模板(目前只有
    # holding_no_data / holding_same_day)整句沒有任何 {placeholder},天生不
    # 可能印出數字——這種模板本身就是具體事實(「這期全是當沖,零隔夜倉」),
    # 不該因為沒有數字就被判成空泛(#542 第三個回歸:persona sweep 對
    # day_trader persona 的真卡抓到)。
    body_for_b9 = _card_body(text)
    hole_section = _hole_section(body_for_b9)
    if hole_section is None:
        has_num = bool(_CONCRETE_NUM.search(text))
        findings.append(Finding("B-9", has_num,
                                "卡含 ≥1 具體數字(降級 proxy:定位不到 hole 段落)",
                                "" if has_num else "全卡查無金額 / % / 小數"))
    else:
        has_num = (bool(_HOLE_SECTION_NUM.search(hole_section))
                  or _hole_number_optional(hole_section))
        top_hole_tickers = _top_hole_tickers(context)   # None = 沒有權威判定
        if top_hole_tickers is not None:
            pool = top_hole_tickers | _known_tickers(body_for_b9)
            cited = {t for t in pool if re.search(rf"\b{re.escape(t)}\b", hole_section)}
            ticker_required = bool(top_hole_tickers)
            ok_b9 = has_num and (bool(cited) if ticker_required else True)
            missing = [p for p, present in
                      (("數字", has_num), ("ticker", bool(cited) if ticker_required else True))
                      if not present]
            evidence = ("" if ok_b9 else
                        f"biggest-hole 段落缺 {'/'.join(missing)}:{hole_section.strip()[:80]!r}")
            findings.append(Finding("B-9", ok_b9,
                                    "biggest-hole 段落含具體數字,且該維度有 ticker 事實時有引用",
                                    evidence))
        else:
            block2_tickers = _known_tickers(body_for_b9)
            if block2_tickers:
                cited = {t for t in block2_tickers
                        if re.search(rf"\b{re.escape(t)}\b", hole_section)}
                ok_b9 = has_num and bool(cited)
                evidence = "" if ok_b9 else (
                    f"biggest-hole 段落缺 "
                    f"{'/'.join(p for p, present in (('數字', has_num), ('ticker', bool(cited))) if not present)}"
                    f":{hole_section.strip()[:80]!r}")
                findings.append(Finding("B-9", ok_b9,
                                        "biggest-hole 段落含 ticker + 具體數字"
                                        "(無 context,Block 2 逐檔列當 proxy 池)",
                                        evidence))
            else:
                has_num_fallback = bool(_CONCRETE_NUM.search(text))
                findings.append(Finding("B-9", has_num_fallback,
                                        "卡含 ≥1 具體數字(降級 proxy:無 context 且 Block 2 無逐檔列)",
                                        "" if has_num_fallback else "全卡查無金額 / % / 小數"))

    # S 系列(v2 私卡限定;非 v2 文字回空 list,不影響舊 eval case)
    findings.extend(check_structure(text, context))

    return findings


def check_ticker_diagnosis(text: str, ticker: str, forbidden_tags=(), allowed_tags=()) -> Finding:
    """B-1(#542):case 指定的洗白標的,自己的診斷列不得命中禁止標籤、命中時須
    落在允許標籤集合裡(有給的話),而且 headline 洞(risks 區 ``[X]`` panel)仍要
    在卡上——三件事任一件不成立都算這條斷言被踩。

    ``ticker``/``forbidden_tags``/``allowed_tags`` 是呼叫端(case)宣告的資料,
    不是這支檔案裡的常數:``forbidden_tags``/``allowed_tags`` 是 copy
    ``instrument_tags`` 的 code(card_renderer.localized_instrument_tag 解析的
    同一份目錄的 key,例如 ``suspected_dca``),不是卡面上會印出的字面詞——見
    cases/washer.yaml 的 subject_ticker / subject_forbidden_tags /
    subject_allowed_tags 三欄,以及 run_case.sh 怎麼把它們轉成這裡的參數。"""
    body = _card_body(text)
    block = _ticker_diagnosis_block(body, ticker)
    problems = []
    if block is None:
        problems.append(f"找不到 {ticker} 的逐檔診斷列(Block 2 無 '- {ticker} ...' 這一列)")
    else:
        hit_forbidden = [code for code in forbidden_tags
                         if _tag_needle_pattern(code).search(block)]
        if hit_forbidden:
            problems.append(f"{ticker} 命中禁止標籤:{', '.join(hit_forbidden)}")
        if allowed_tags:
            hit_allowed = [code for code in allowed_tags
                           if _tag_needle_pattern(code).search(block)]
            if not hit_allowed:
                problems.append(f"{ticker} 未命中任一允許標籤(允許集合:{', '.join(allowed_tags)})")
    if _hole_section(body) is None:
        problems.append("headline 洞([X] hole panel)未上卡")
    return Finding("B-1", not problems,
                   f"{ticker} 標籤屬允許集合且不在禁止集合;headline 洞仍在",
                   "; ".join(problems))


def _parse_argv(argv):
    """把 CLI 參數拆成既有的位置參數(<card.md|-> [context.json])加上 #542 新增
    的 B-1 選填旗標(--ticker/--forbidden-tags/--allowed-tags,逗號分隔的 code
    清單),讓沒帶新旗標的既有呼叫方式行為完全不變。"""
    positional = []
    ticker = None
    forbidden, allowed = [], []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--ticker" and i + 1 < len(argv):
            ticker = argv[i + 1]
            i += 2
        elif arg == "--forbidden-tags" and i + 1 < len(argv):
            forbidden = [c for c in argv[i + 1].split(",") if c]
            i += 2
        elif arg == "--allowed-tags" and i + 1 < len(argv):
            allowed = [c for c in argv[i + 1].split(",") if c]
            i += 2
        else:
            positional.append(arg)
            i += 1
    return positional, ticker, forbidden, allowed


def _main() -> int:
    positional, ticker, forbidden_tags, allowed_tags = _parse_argv(sys.argv[1:])
    if len(positional) not in (1, 2):
        print(f"用法: {sys.argv[0]} <card.md|-> [context.json] "
              f"[--ticker T --forbidden-tags a,b --allowed-tags c,d]", file=sys.stderr)
        return 2
    if positional[0] == "-":
        text = sys.stdin.read()
    else:
        with open(positional[0], encoding="utf-8") as f:
            text = f.read()
    context = None
    if len(positional) == 2:
        with open(positional[1], encoding="utf-8") as f:
            context = json.load(f)

    findings = check_card(text, context)
    if ticker:
        findings.append(check_ticker_diagnosis(text, ticker, forbidden_tags, allowed_tags))
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
