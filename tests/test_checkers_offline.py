#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_card.py / check_state.py 的離線驗活(#60 最小驗收 harness 的確定性核心)。

eval-design.md §6:斷言本身也可能是「死的」(不會因為卡真的踩雷就亮紅)。這支證明
兩支 checker 是活的——**乾淨輸入全過、刻意壞掉的輸入必掛對應條**,無網路、確定性,
所以進得了 tests/run_all.py(headless 產卡那段非確定性 + 有成本,不進 CI,見 §7)。

分工:
  check_card  對三張 judge fixture 跑機檢 + 逐條 micro 驗活(每條各一 trip / 一 clean)
  check_state 用 coach.py【真實寫入】當 known-good oracle(§6)+ 手工壞檔驗紅 + 差分/append

跑法:python3 tests/test_checkers_offline.py
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
AGENT_DIR = ROOT / "tests" / "agent"
FIXTURES = AGENT_DIR / "fixtures"
COACH = str(ROOT / "skills" / "fomo-kernel" / "engine" / "coach.py")

sys.path.insert(0, str(AGENT_DIR))
from check_card import check_card, check_ticker_diagnosis  # noqa: E402
# #671: the module too — the catalog gates below call its A-13 mechanism
# directly rather than restating the rule a second time.
import check_card as check_card_module  # noqa: E402
from check_state import check_state, differential, append_only  # noqa: E402

_fails = []


def ok(cond, msg, extra=""):
    print(("✅" if cond else "❌") + f" {msg}" + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        _fails.append(msg)


def _card_fail_ids(text):
    return {f.assertion for f in check_card(text) if not f.passed}


# ─────────────────────── check_card ───────────────────────

def test_card_fixtures():
    """三張 judge fixture:乾淨卡全過;兩張壞卡各自踩到預期集合(⊆,容忍附帶違規)。"""
    good = (FIXTURES / "card_good.txt").read_text(encoding="utf-8")
    ok(_card_fail_ids(good) == set(),
       "card_good 過全部卡面鐵律(修全形後成真乾淨參照)", str(_card_fail_ids(good)))

    dash = (FIXTURES / "card_bad_dashboard.txt").read_text(encoding="utf-8")
    ok({"A-2", "A-3", "A-12", "B-7"} <= _card_fail_ids(dash),
       "card_bad_dashboard 踩 A-2/A-3/A-12/B-7(dashboard 化的機檢子集)", str(_card_fail_ids(dash)))

    vague = (FIXTURES / "card_bad_vague.txt").read_text(encoding="utf-8")
    ok({"B-7", "B-9"} <= _card_fail_ids(vague),
       "card_bad_vague 踩 B-7/B-9(空泛黑名單 + 無具體數字)", str(_card_fail_ids(vague)))


def test_card_each_assertion_alive():
    """逐條:一個會踩、一個乾淨——證明每條斷言活著且不誤判(eval-design §6)。"""
    cases = [
        ("A-2", "部位 sizing: 0.71 🔴", "部位押得有點重"),
        ("A-3", "〔這次成績〕還可以", "這次成績還可以"),
        ("A-6", "勝率 62% 是你的主數字", "盈虧比 0.24 是你的主數字"),
        ("A-12", "你的 max_pos_pct 到 31%", "你的最大單注到 31%"),
        ("A-13", "想分散,結果沒有", "想分散，結果沒有"),
        # #671: the two holes a delivered card walked through. A half-width
        # paren is not one of the three characters the old class enumerated;
        # and in `站得住);但` the paren stood between the semicolon and the
        # Chinese, shielding it from a rule that demanded CJK on both sides.
        ("A-13", "貢獻 5pp(描述性、這數字站得住);但樣本不足", "貢獻 5pp（描述性、這數字站得住）；但樣本不足"),
        ("A-13", "AI 概念股(跨板塊)佔比偏高", "AI 概念股（跨板塊）佔比偏高"),
        ("B-7", "下次規矩:控制風險", "下次規矩:INTC 虧損不再加碼"),
        ("B-9", "紀律不佳、風險偏高、需要注意", "INTC 這半年虧 $1,240"),
    ]
    for aid, bad, clean in cases:
        ok(aid in _card_fail_ids(bad), f"{aid} 抓得到違規案例", bad)
        ok(aid not in _card_fail_ids(clean), f"{aid} 乾淨案例不誤判", clean)


# ─────────────────────── check_state ───────────────────────

_MIN_STATE = {
    "date_end": "2026-07-01", "headline_dim": "avgdown",
    "commitment": None, "insufficient_data": False,
    "metrics": {"max_pos_pct": 0.31, "avgdown_count": 4, "ai_pct": 0.55},
}
_THESIS = [{"ticker": "INTC", "cycle_id": "INTC#2026-01-05#1", "why": "w", "maturity": "inferred"}]


def _write_state_via_coach(home, rule, metric):
    """用 coach.py 真實寫入一個 ~/.trade-coach(known-good oracle,§6):close + append-theses。"""
    tc = home / ".trade-coach"
    tc.mkdir(parents=True, exist_ok=True)
    (tc / "last_state.json").write_text(json.dumps(_MIN_STATE), encoding="utf-8")
    env = dict(os.environ, HOME=str(home))
    tj = home / "theses.json"
    tj.write_text(json.dumps(_THESIS), encoding="utf-8")
    r1 = subprocess.run([sys.executable, COACH, "close", "--rule", rule, "--metric", metric],
                        env=env, capture_output=True, text=True, timeout=60)
    r2 = subprocess.run([sys.executable, COACH, "append-theses", str(tj),
                         "--session-date", _MIN_STATE["date_end"]],
                        env=env, capture_output=True, text=True, timeout=60)
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr
    return tc


def test_state_oracle_good():
    """coach 真實寫出的狀態 = known-good:check_state 必須全過(否則 checker 比 writer 還嚴,是死斷言)。"""
    with tempfile.TemporaryDirectory() as tmp:
        tc = _write_state_via_coach(pathlib.Path(tmp), "單筆上限 20%", "max_pos_pct")
        bad = [f for f in check_state(tc) if not f.passed]
        ok(not bad, "coach 真實輸出過 check_state 全部條(known-good oracle)",
           ", ".join(f.assertion for f in bad))


def test_state_each_assertion_alive():
    """手工弄壞 known-good 的各面向,對應條必紅(§6 known-bad)。"""
    with tempfile.TemporaryDirectory() as tmp:
        base = _write_state_via_coach(pathlib.Path(tmp), "單筆上限 20%", "max_pos_pct")

        # S-4 缺 theses.jsonl → 收尾跳過
        d4 = pathlib.Path(tmp) / "s4"
        d4.mkdir()
        (d4 / "log.jsonl").write_text((base / "log.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
        ok("S-4" in {f.assertion for f in check_state(d4) if not f.passed},
           "S-4 抓到收尾跳過(theses.jsonl 沒建)")

        # S-1 log.jsonl 有壞行
        d1 = pathlib.Path(tmp) / "s1"
        d1.mkdir()
        (d1 / "log.jsonl").write_text('{"date_end":"x"}\n這不是 JSON\n', encoding="utf-8")
        (d1 / "theses.jsonl").write_text((base / "theses.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
        ok("S-1" in {f.assertion for f in check_state(d1) if not f.passed},
           "S-1 抓到非 JSON 壞行")

        # S-2 log 缺欄
        d2 = pathlib.Path(tmp) / "s2"
        d2.mkdir()
        (d2 / "log.jsonl").write_text('{"date_end":"2026-07-01","commitment":null}\n', encoding="utf-8")
        (d2 / "theses.jsonl").write_text((base / "theses.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
        ok("S-2" in {f.assertion for f in check_state(d2) if not f.passed},
           "S-2 抓到 log 缺 headline_dim/metrics_snapshot")

        # S-3 theses 缺 cycle_id
        d3 = pathlib.Path(tmp) / "s3"
        d3.mkdir()
        (d3 / "log.jsonl").write_text((base / "log.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
        (d3 / "theses.jsonl").write_text('{"ticker":"INTC","maturity":"inferred"}\n', encoding="utf-8")
        ok("S-3" in {f.assertion for f in check_state(d3) if not f.passed},
           "S-3 抓到 theses 缺 cycle_id")


def test_state_differential_and_append():
    """B-3 差分(換答案→commitment.metric_key 不同)+ A-7 append-only,各驗一 pass 一 fail。"""
    with tempfile.TemporaryDirectory() as tmp:
        a = _write_state_via_coach(pathlib.Path(tmp) / "a", "單筆上限 20%", "max_pos_pct")
        b = _write_state_via_coach(pathlib.Path(tmp) / "b", "AI 曝險封頂 40%", "ai_pct")
        la, lb = a / "log.jsonl", b / "log.jsonl"
        ok(differential(la, lb).passed,
           "B-3 兩種答案 → commitment.metric_key 不同(Step 2 不是儀式)")
        ok(not differential(la, la).passed,
           "B-3 同一份 log 對自己 → 不算差分(斷言活著,不會永遠綠)")

    ok(append_only(3, 5).passed, "A-7 行數增(3→5)判 append-only 通過")
    ok(not append_only(5, 3).passed, "A-7 行數縮(5→3)判 append-only 失敗(斷言活著)")


# ─────────────────── check_card S 系列(output-contract §8)───────────────────

_COPY = json.loads((ROOT / "skills" / "fomo-kernel" / "copy" / "zh-TW.json")
                   .read_text(encoding="utf-8"))
_BLOCKS = _COPY["blocks"]
_MISSING = _COPY["block_missing"]
_TAGS = _COPY["instrument_tags"]
_SECTIONS = _COPY["sections"]


def _v2_card(titles=None, block1=None, block2=None, block3=None, summary=None, tail=""):
    """手工組一張最小 v2 私卡(front matter + keynote + 四 block，選填第 5 個
    summary block,#345)。``summary`` 給 body 行清單時,預設標題自動附在四大
    block 之後(``titles`` 顯式指定時由呼叫端自己控制順序/標題,供越序測試用)。"""
    titles = titles if titles is not None else (
        [_BLOCKS[key] for key in ("performance", "trades", "risks", "next")]
        + ([_BLOCKS["summary"]] if summary is not None else []))
    block1 = block1 if block1 is not None else [
        "復盤區間 2026-01-01 → 2026-07-14",
        "帳面總損益 -$300（已實現 +$200 · 未實現 -$500）",
        _MISSING["annualized"], _MISSING["vs_market"]]
    block2 = block2 if block2 is not None else [_MISSING["trades"]]
    block3 = block3 if block3 is not None else ["[X] 最大的行為漏洞：INTC 虧 $1,240 仍加碼"]
    bodies = [block1, block2, block3, ["[*] 下次只改這一件：單筆上限 20%"]]
    if summary is not None:
        bodies.append(summary)
    lines = ["---", "session_id: probe", "privacy: private", "language: zh-TW", "---",
             "", "# 帳面賺的靠 beta，操作靠紀律", ""]
    for title, body in zip(titles, bodies):
        lines.extend([f"## {title}", ""] + body + [""])
    return "\n".join(lines) + tail + "\n"


_S2_CONTEXT = {  # overview 亮、其餘模組全暗、診斷有 → 對應 _v2_card 預設缺料 note 佈局
    "engine_card": {"overview": {"total_pnl": -300, "realized": 200, "unrealized": -500},
                    "acct_perf": {"note": "offline"}, "alpha_beta_breakdown": {},
                    "ticker_diagnosis": [], "top_holes": [{"dim": "加碼攤平"}],
                    "currency_meta": {"aggregate_currency": "USD"}}}


def test_card_structure_series_alive():
    """S-1..S-4 逐條:乾淨 v2 卡全過;亂序 / 刪 block / 疊 caveat / 越序 caveat /
    Block 1 內殘留單條 inline caveat(2026-07-22 起應全數收進 footnote,#276)/
    IRR token / 混數字風格 / S-2 兩向(靜默省略、多印缺料 note)各自踩紅;
    非 v2 文字完全不出 S findings(v1 eval case 零影響)。"""
    clean = _v2_card()
    ok(not {f.assertion for f in check_card(clean, _S2_CONTEXT) if not f.passed},
       "v2 乾淨卡 S 系列全過",
       str({f.assertion: f.evidence for f in check_card(clean, _S2_CONTEXT) if not f.passed}))

    shuffled = _v2_card(titles=[_BLOCKS["trades"], _BLOCKS["performance"],
                                _BLOCKS["risks"], _BLOCKS["next"]])
    ok("S-1" in _card_fail_ids(shuffled), "S-1 抓 block 亂序")
    dropped = _v2_card(titles=[_BLOCKS["performance"], _BLOCKS["risks"], _BLOCKS["next"]])
    ok("S-1" in _card_fail_ids(dropped), "S-1 抓少一個 block")

    # #345: optional 5th block(結尾 synthesis)排在下一步之後、標題正確 → 全過;
    # 標題不對,或插在四大 block 中間而非排在最後,兩者都該讓 S-1 踩雷。
    with_summary = _v2_card(summary=["這期的處境由集中度主導，往前看它仍是最大的擺動因子。"])
    ok(not {f.assertion for f in check_card(with_summary, _S2_CONTEXT) if not f.passed},
       "S 系列:第 5 個 summary block(下一步之後、標題正確)全過",
       str({f.assertion: f.evidence for f in check_card(with_summary, _S2_CONTEXT) if not f.passed}))
    wrong_summary_title = _v2_card(
        titles=[_BLOCKS[key] for key in ("performance", "trades", "risks", "next")]
        + ["不是總結的標題"],
        summary=["這期的處境由集中度主導，往前看它仍是最大的擺動因子。"])
    ok("S-1" in _card_fail_ids(wrong_summary_title),
       "S-1 抓第 5 個 block 標題不是 copy.blocks.summary")

    misplaced_lines = ["---", "session_id: probe", "privacy: private", "language: zh-TW", "---",
                       "", "# 帳面賺的靠 beta，操作靠紀律", ""]
    for title, body in (
        (_BLOCKS["performance"], ["復盤區間 2026-01-01 → 2026-07-14",
                                  "帳面總損益 -$300（已實現 +$200 · 未實現 -$500）",
                                  _MISSING["annualized"], _MISSING["vs_market"]]),
        (_BLOCKS["trades"], [_MISSING["trades"]]),
        (_BLOCKS["summary"], ["這期的處境由集中度主導，往前看它仍是最大的擺動因子。"]),
        (_BLOCKS["risks"], ["[X] 最大的行為漏洞：INTC 虧 $1,240 仍加碼"]),
        (_BLOCKS["next"], ["[*] 下次只改這一件：單筆上限 20%"]),
    ):
        misplaced_lines.extend([f"## {title}", ""] + body + [""])
    misplaced = "\n".join(misplaced_lines) + "\n"
    ok("S-1" in _card_fail_ids(misplaced),
       "S-1 抓 summary 插在四大 block 中間(risks 與 next 之間)而非排在最後")

    stacked = _v2_card(block1=["帳面總損益 -$300（已實現 +$200 · 未實現 -$500）",
                               "  （caveat 甲）", "  （caveat 乙）", "  （caveat 丙）",
                               _MISSING["annualized"], _MISSING["vs_market"]])
    ok("S-3" in _card_fail_ids(stacked), "S-3 抓三連發 caveat 牆")
    early = _v2_card(block1=["  （caveat 先於任何指標）",
                             "帳面總損益 -$300（已實現 +$200 · 未實現 -$500）",
                             _MISSING["annualized"], _MISSING["vs_market"]])
    ok("S-3" in _card_fail_ids(early), "S-3 抓 Block 1 首行 caveat")
    # 2026-07-22 ruling (#276): caveats no longer ride Block-1 indicators at
    # all, so a single, non-stacked, non-first-line caveat inside Block 1 is
    # now itself a violation — the old two checks above would both miss it.
    mid_caveat = _v2_card(block1=["帳面總損益 $-300（已實現 $+200 · 未實現 $-500）",
                                  "  （caveat 在中間）",
                                  _MISSING["annualized"], _MISSING["vs_market"]])
    ok("S-3" in _card_fail_ids(mid_caveat),
       "S-3 抓 Block 1 內任何 inline caveat(非首行、未疊也算違規)")

    irr = _v2_card(tail="\n年化 IRR 15% 不該這樣寫。")
    ok("S-4" in _card_fail_ids(irr), "S-4 抓 IRR token")
    mixed = _v2_card(tail="\n這期贏三成，數字是 30%。")
    ok("S-4" in _card_fail_ids(mixed), "S-4 抓單句混用拼寫數與阿拉伯數")

    silent = _v2_card(block1=["帳面總損益 -$300（已實現 +$200 · 未實現 -$500）"])
    ok("S-2" in {f.assertion for f in check_card(silent, _S2_CONTEXT) if not f.passed},
       "S-2 抓靜默省略(前提缺但缺料 note 沒出)")
    over = _v2_card(block1=["帳面總損益 -$300（已實現 +$200 · 未實現 -$500）",
                            _MISSING["absolute_pnl"],
                            _MISSING["annualized"], _MISSING["vs_market"]])
    ok("S-2" in {f.assertion for f in check_card(over, _S2_CONTEXT) if not f.passed},
       "S-2 抓多印缺料 note(前提在卻說算不出)")
    priced_out = _v2_card(block1=["帳面總損益 -$300（已實現 +$200 · 未實現 -$500）",
                                  _MISSING["annualized_prices"],
                                  _MISSING["vs_market_prices"]])
    ok(not any(f.assertion == "S-2" and not f.passed
               for f in check_card(priced_out, _S2_CONTEXT)),
       "S-2 認得 *_prices 缺料變體(#289/#321:價格檢索被擋時 renderer 換用的同義 note)")
    ok(not any(f.assertion == "S-2" and not f.passed for f in check_card(clean)),
       "S-2 沒給 context 時降級跳過,不誤殺")

    ok(not any(f.assertion.startswith("S-") for f in check_card("INTC 這半年虧 $1,240")),
       "非 v2 文字不出 S findings(v1 eval case 零影響)")

    # #284 月度 vs-market gate:S-2 認 engine_card.vs_market_gate 訊號,雙向嚴格。
    _vs_line = "持倉對 SPY 的超額報酬 +13 個百分點；β 1.31。"
    _ab_lit = {"port_tot": 0.24, "spy_tot": 0.11, "excess_vs_spy": 0.13, "bench": "SPY"}
    ungated_ctx = {"engine_card": dict(_S2_CONTEXT["engine_card"],
                                       alpha_beta_breakdown=_ab_lit)}
    gated_ctx = {"engine_card": dict(_S2_CONTEXT["engine_card"],
                                     alpha_beta_breakdown=_ab_lit,
                                     vs_market_gate={"render": False,
                                                     "basis": "already_rendered_this_month",
                                                     "month": "2026-07"})}
    with_segment = _v2_card(block1=["帳面總損益 -$300（已實現 +$200 · 未實現 -$500）",
                                    _MISSING["annualized"], _vs_line])
    without_segment = _v2_card(block1=["帳面總損益 -$300（已實現 +$200 · 未實現 -$500）",
                                       _MISSING["annualized"]])
    gap_note_while_gated = _v2_card(block1=["帳面總損益 -$300（已實現 +$200 · 未實現 -$500）",
                                            _MISSING["annualized"], _MISSING["vs_market"]])
    ok(not any(f.assertion == "S-2" and not f.passed
               for f in check_card(with_segment, ungated_ctx)),
       "S-2 未 gated + 前提在 + 段落上卡 → 過")
    ok(not any(f.assertion == "S-2" and not f.passed
               for f in check_card(without_segment, gated_ctx)),
       "S-2 gated 卡整段不出且無 gap note → 過")
    ok("S-2" in {f.assertion for f in check_card(without_segment, ungated_ctx) if not f.passed},
       "S-2 抓未 gated 而段落漏上卡(前提在、note 也沒出)")
    ok("S-2" in {f.assertion for f in check_card(with_segment, gated_ctx) if not f.passed},
       "S-2 抓 gated 期段落仍上卡")
    ok("S-2" in {f.assertion for f in check_card(gap_note_while_gated, gated_ctx) if not f.passed},
       "S-2 抓 gated 期出了 gap note(§3:整段直接不出)")


# ─────────── #542:B-9 section-scope 升級 + B-1(check_ticker_diagnosis)驗活 ───────────

def _zh_row(ticker, amount, tag_code, **params):
    """Block 2 一檔逐檔列,card_renderer 'rows' kind 的確切形狀
    ('- TICKER amount（tag）'),標籤文字從真的 zh-TW instrument_tags 模板
    format 出來,而不是在測試裡手打一份第二版字面詞。"""
    return f"- {ticker} {amount}（{_TAGS[tag_code].format(**params)}）"


def test_card_b1_and_b9_section_scoped_alive():
    """#542:B-9 的 section-scope 升級,和全新的 B-1(check_ticker_diagnosis),各自
    驗活。

    B-9 的關鍵反例是「biggest-hole 段落本身空泛,但卡別處(Block 2 的逐檔列)有
    數字」——這正是舊 whole-card proxy 會誤放行、issue #542 點名要擋的形狀,所以
    這裡特別做成一組「只改 hole 段落文字、其餘不動」的最小對照,而不是隨手換一張
    壞卡(那樣測不出是不是真的在管 section 範圍)。

    B-1 的四種紅各自獨立驗證:標籤命中禁止集合 / 命中了但不在允許集合裡 /
    headline 洞沒上卡 / 卡上根本找不到這檔的診斷列——四條缺一都要能各自觸發,
    不是隨便哪個字串改了就一起紅、一起綠。
    """
    forbidden_row = _zh_row("INTC", "-$1,240", "suspected_dca", n_adds=3)
    allowed_row = _zh_row("INTC", "-$1,240", "suspected_averaging_down_losing",
                          n_adds=3, cur_pct="38%", price_note="")
    neutral_row = _zh_row("INTC", "-$1,240", "roughly_neutral")
    hole_grounded = f"[X] {_SECTIONS['hole']}：INTC 虧 $1,240 仍加碼"
    hole_vague = f"[X] {_SECTIONS['hole']}：紀律不佳、風險偏高、需要注意"
    allowed_codes = ["suspected_averaging_down_losing", "suspected_averaging_down_recovered",
                     "adds_pending_confirmation"]

    # ── B-9:hole 段落本身要有 ticker + 數字,不能靠卡片別處的數字矇混過關 ──
    grounded_hole = _v2_card(block2=[allowed_row], block3=[hole_grounded])
    ok("B-9" not in _card_fail_ids(grounded_hole),
       "B-9 過:biggest-hole 段落本身就含 ticker(INTC)+ 數字($1,240)")

    vague_hole_numbers_elsewhere = _v2_card(block2=[allowed_row], block3=[hole_vague])
    ok("B-9" in _card_fail_ids(vague_hole_numbers_elsewhere),
       "B-9 抓到:hole 段落本身無 ticker/數字,即使 Block 2 別處有數字"
       "(舊 proxy 會誤放行的形狀,#542)", str(_card_fail_ids(vague_hole_numbers_elsewhere)))

    no_ticker_pool = _v2_card(block3=[hole_vague])   # block2 用預設(無逐檔列;缺價降級的形狀)
    ok("B-9" not in _card_fail_ids(no_ticker_pool),
       "B-9 沒有已知 ticker 池(Block 2 無逐檔列)時退回舊 proxy,不誤殺",
       str(_card_fail_ids(no_ticker_pool)))

    # ── B-1:洗白標的的標籤 + headline 洞 ──────────────────────────────────
    good = check_ticker_diagnosis(_v2_card(block2=[allowed_row], block3=[hole_grounded]),
                                  "INTC", forbidden_tags=["suspected_dca"],
                                  allowed_tags=allowed_codes)
    ok(good.passed, "B-1 過:允許標籤命中、禁止標籤未命中、headline 洞仍在", good.evidence)

    whitewashed = check_ticker_diagnosis(
        _v2_card(block2=[forbidden_row], block3=[hole_grounded]),
        "INTC", forbidden_tags=["suspected_dca"], allowed_tags=allowed_codes)
    ok(not whitewashed.passed and "suspected_dca" in whitewashed.evidence,
       "B-1 抓到:INTC 命中禁止標籤(疑似定投/洗白)", whitewashed.evidence)

    neither = check_ticker_diagnosis(
        _v2_card(block2=[neutral_row], block3=[hole_grounded]),
        "INTC", forbidden_tags=["suspected_dca"], allowed_tags=allowed_codes)
    ok(not neither.passed,
       "B-1 抓到:標籤既非禁止也非任一允許集合(roughly_neutral)", neither.evidence)

    hole_missing = check_ticker_diagnosis(
        _v2_card(block2=[allowed_row], block3=[]),
        "INTC", forbidden_tags=["suspected_dca"], allowed_tags=allowed_codes)
    ok(not hole_missing.passed and "headline" in hole_missing.evidence,
       "B-1 抓到:headline 洞沒上卡(risks 區沒有 [X] panel)", hole_missing.evidence)

    row_missing = check_ticker_diagnosis(
        _v2_card(block2=[_MISSING["trades"]], block3=[hole_grounded]),
        "INTC", forbidden_tags=["suspected_dca"])
    ok(not row_missing.passed and "找不到" in row_missing.evidence,
       "B-1 抓到:卡上根本沒有 INTC 的逐檔診斷列", row_missing.evidence)


def test_card_b9_context_aware_ticker_gate_alive():
    """#542 continued: the first cut of the B-9 section-scope upgrade passed
    every test above (all text-only or hand-built context) and still broke
    108 of 468 real rendered cards when run against tests/persona_sweep.py's
    full persona x locale x route corpus. Three more real shapes had to be
    found and fixed before the sweep went green again; each is pinned here
    so a regression is caught locally without needing the full sweep:

    - some hole dimensions (exit_discipline, diversification) never carry a
      ticker fact at all -- the ticker bar must come from *this hole's own*
      raw data (top_holes[0].raw), not from tickers priced anywhere else on
      the card (Block 2's rows are a completely independent derivation --
      only priced/nonzero-impact tickers, while a hole's tickers come from
      raw trade history regardless of current price).
    - a few hole_lines templates (holding_same_day, holding_no_data) render
      with zero digits by construction (no {placeholder} in the template at
      all) -- the number bar must not punish those.
    - Taiwan tickers (2330.TW) start with a digit, not a letter -- the
      ticker-shape regex must recognize them too, in both the hole-citation
      check and Block 2's row scan.
    """
    # ── (1) ticker not required when the hole's own raw data carries none ──
    exit_hole = f"[X] {_SECTIONS['hole']}：winners held 120 days / losers held 378 days (disposition gap +258)"
    exit_ctx = {"top_holes": [{"raw": {"dim": "出場紀律", "n_rt": 2, "hold_win": 120,
                                       "hold_lose": 378, "disp_gap": 258}}]}
    exit_card = _v2_card(block3=[exit_hole])
    ok(not any(f.assertion == "B-9" and not f.passed for f in check_card(exit_card, exit_ctx)),
       "B-9 過(context 版):exit_discipline 洞本身無 ticker 事實,只要求數字",
       str({f.assertion: f.evidence for f in check_card(exit_card, exit_ctx) if not f.passed}))

    exit_hole_no_num = f"[X] {_SECTIONS['hole']}：winners were held far longer than losers, a clear disposition gap"
    exit_card_no_num = _v2_card(block3=[exit_hole_no_num])
    ok(any(f.assertion == "B-9" and not f.passed for f in check_card(exit_card_no_num, exit_ctx)),
       "B-9 抓到(context 版):exit_discipline 免 ticker 不代表也免數字")

    # ── (2) ticker IS required when the hole's own raw data carries one ────
    avgdown_ctx = {"top_holes": [{"raw": {"dim": "加碼攤平", "count": 6, "breach": 2,
                                          "tickers": ["INTC"]}}]}
    uncited_hole = f"[X] {_SECTIONS['hole']}：你有 6 次在虧損倉往下加碼，其中 2 次加碼當下佔成本 >25%"
    uncited_card = _v2_card(block3=[uncited_hole])
    ok(any(f.assertion == "B-9" and not f.passed for f in check_card(uncited_card, avgdown_ctx)),
       "B-9 抓到(context 版):這個洞的 raw 資料明明有 ticker(INTC),句子卻沒點名",
       str({f.assertion: f.evidence for f in check_card(uncited_card, avgdown_ctx) if not f.passed}))

    cited_hole = f"[X] {_SECTIONS['hole']}：你有 6 次在虧損倉往下加碼(INTC)，其中 2 次加碼當下佔成本 >25%"
    cited_card = _v2_card(block3=[cited_hole])
    ok(not any(f.assertion == "B-9" and not f.passed for f in check_card(cited_card, avgdown_ctx)),
       "B-9 過(context 版):ticker 事實存在,句子也確實點名")

    # ── (3) a placeholder-free hole_lines template needs no digit ──────────
    # 同款 context 需求(見上面 exit_ctx 的註解):沒有 context/ticker 池時,這張
    # 手工卡的 Block 1 預設內容自帶 "$300",會被全卡層降級 proxy 撿到而誤判通過,
    # 測不出 _hole_number_optional 本身有沒有在做事。holding_period 天生無
    # ticker,借用 exit_ctx(同樣是空 ticker 需求)即可,不必另造一個。
    static_hole = f"[X] {_SECTIONS['hole']}：{_COPY['hole_lines']['holding_same_day']}"
    static_card = _v2_card(block3=[static_hole])
    ok(not any(f.assertion == "B-9" and not f.passed for f in check_card(static_card, exit_ctx)),
       "B-9 過:holding_same_day 是零 placeholder 模板,天生沒有數字也算數",
       str({f.assertion: f.evidence for f in check_card(static_card, exit_ctx) if not f.passed}))

    # exit_ctx(空 ticker 需求)當 context,逼精準版走到底只剩數字門檻要判——不然
    # 這張手工卡的 Block 1 預設內容自帶 "$300",沒有 context 時會整卡層降級 proxy
    # 撿到那個數字,誤判成通過(這條斷言原本就是這樣寫壞過一次,見上面 exit_ctx
    # 案例的姊妹版)。
    vague_lookalike = f"[X] {_SECTIONS['hole']}：紀律不太好,要多注意"
    vague_card = _v2_card(block3=[vague_lookalike])
    ok(any(f.assertion == "B-9" and not f.passed for f in check_card(vague_card, exit_ctx)),
       "B-9 抓到:沒有數字、也不比對任何已知零 placeholder 模板的空泛句仍算違規")

    # ── (4) Taiwan tickers (digit-first) are recognized as ticker-shaped ───
    tw_row = "- 2330.TW -TWD 90,000（roughly neutral）"
    tw_hole_cited = (f"[X] {_SECTIONS['hole']}：your largest single position 2330.TW was 87%, "
                     f"against a 3% average across the rest")
    tw_card_cited = _v2_card(block2=[tw_row], block3=[tw_hole_cited])
    ok("B-9" not in _card_fail_ids(tw_card_cited),
       "B-9 過:2330.TW 這種數字開頭的台股代碼也算得出 ticker(無 context,Block 2 proxy 池)")

    tw_hole_uncited = f"[X] {_SECTIONS['hole']}：your largest single position was 87%, against a 3% average"
    tw_card_uncited = _v2_card(block2=[tw_row], block3=[tw_hole_uncited])
    ok("B-9" in _card_fail_ids(tw_card_uncited),
       "B-9 抓到:2330.TW 在 Block 2 池裡,但 hole 段落沒點名任何標的")



def test_a13_covers_the_shipped_catalogs():
    """The rule holds where the card's words actually come from (#671).

    A-13 runs on rendered output; the sentences it judges are assembled from
    `copy/<locale>.json`, so a catalog string carrying a half-width mark is a
    violation waiting for the branch that renders it. Checking the catalog is
    what makes the rule cover branches no persona reaches — the same reason
    `tests/copy_corpus.py` exists.
    """
    for locale in ("zh-TW", "zh-CN", "en"):
        path = ROOT / "skills" / "fomo-kernel" / "copy" / f"{locale}.json"
        offenders = []

        def walk(node, trail=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{trail}.{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{trail}[{index}]")
            elif isinstance(node, str):
                found = check_card_module.halfwidth_in_cjk_run(node)
                if found:
                    offenders.append((trail, found))

        walk(json.loads(path.read_text(encoding="utf-8")))
        ok(not offenders, f"{locale} 文案無 CJK 段落內的半形標點",
           "; ".join(f"{t}: {f}" for t, f in offenders[:4]))


def test_a13_pairs_brackets_in_the_catalogs():
    """A converted bracket keeps its partner: half a pair is worse than none."""
    for locale in ("zh-TW", "zh-CN", "en"):
        path = ROOT / "skills" / "fomo-kernel" / "copy" / f"{locale}.json"
        unbalanced = []

        def walk(node, trail=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{trail}.{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{trail}[{index}]")
            elif isinstance(node, str):
                if (node.count("（") != node.count("）")
                        or node.count("(") != node.count(")")):
                    unbalanced.append((trail, node[:60]))

        walk(json.loads(path.read_text(encoding="utf-8")))
        ok(not unbalanced, f"{locale} 文案括號成對",
           "; ".join(f"{t}: {s}" for t, s in unbalanced[:3]))


def main():
    test_card_fixtures()
    test_card_each_assertion_alive()
    test_card_structure_series_alive()
    test_card_b1_and_b9_section_scoped_alive()
    test_card_b9_context_aware_ticker_gate_alive()
    test_state_oracle_good()
    test_a13_covers_the_shipped_catalogs()
    test_a13_pairs_brackets_in_the_catalogs()
    test_state_each_assertion_alive()
    test_state_differential_and_append()
    print()
    if _fails:
        print(f"❌ {len(_fails)} 條驗活失敗 —— checker 或 fixture 有問題,先修再用。")
        return 1
    print("✅ check_card / check_state 全部驗活通過(斷言證明是活的)。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
