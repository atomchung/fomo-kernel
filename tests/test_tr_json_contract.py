#!/usr/bin/env python3
"""TR_JSON / state 契約測試(#61)—— SKILL 唯一消費介面的紅綠燈。

守三條契約,任何欄位改名/消失/型別變化都必須讓 CI 變紅:
  1. TR_JSON 頂層 key 集合【恰等於】SKILL.md Step 1 消費清單(top_holes 改名 topHoles 這類突變在此陣亡)。
  2. TR_STATE_OUT 薄 state 的 key 集合 + metrics 鍵 + cycle_id 格式(單一事實源 = engine 的 CYCLE_ID_RE)。
  3. 收尾 CLI(engine/coach.py,#148 首刀:原 SKILL 內嵌 heredoc 的 code 化)真的能吃 engine
     寫出的 state —— #41 的 cycle_id 三段 vs 兩段漂移,就是漏了這層「消費者煙霧測試」;
     另鎖 coach ↔ trade_recap 的 CYCLE_ID_RE 同步、SKILL.md 不再長回收尾 heredoc。

全程離線、確定性:對 subprocess 注入假 yfinance(import 即 ImportError)→ 引擎走「未安裝→不連網」降級,
本機裝了 yfinance 也不會連網、不隨行情漂(對 #64 的此檔範圍先行示範)。
零依賴(標準庫,免 pytest);跑法:python3 tests/test_tr_json_contract.py
"""
import json
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "skills" / "fomo-kernel"
ENGINE = SKILL_DIR / "engine" / "trade_recap.py"
SKILL_MD = SKILL_DIR / "SKILL.md"
MOCK_CSV = SKILL_DIR / "mock" / "mock_trades.csv"

sys.path.insert(0, str(ENGINE.parent))
import trade_recap  # noqa: E402  # 只取常數(CYCLE_ID_RE),不跑 main
import coach  # noqa: E402  # 只取常數(CYCLE_ID_RE 同步斷言),不跑 main

# ── SKILL.md Step 1/3 消費清單:改 engine 輸出欄位 = 改這份契約,兩邊要一起動 ──
TR_JSON_KEYS = {
    "schema_version", "philosophy", "strength", "overview",
    "best_trade", "worst_trade", "what_if", "ticker_diagnosis",
    "thesis_questions", "top_holes", "candidate_rules", "prescriptions",
    "alpha_beta_breakdown", "payoff_attribution", "dims_raw", "data_integrity",
    "currency_meta",                                    # #51/#129 PR-2a:聚合幣別/fx/分幣桶
    "cash",                                             # #171 PR-1 呈現層:帳戶現金上卡(balance/weight/source/reliable/recent_net_deposit;None=未提供)
    "honesty_ledger",                                   # #82:卡面必講的誠實點清單(觸發項聚合;空=無缺口)
}
STATE_KEYS = {
    "schema_version", "date_start", "date_end", "n_trades", "n_round_trips",
    "n_held", "headline_dim", "headline_metric", "commitment", "metrics",
    "rule", "insufficient_data", "holdings",
    "currency_meta",                                    # #51/#129 PR-2a(optional 附加欄,單幣 USD 時內容多為 None)
    "cash",                                             # #171 PR-1:帳戶現金地基(balance/weight/source/reliable/recent_net_deposit;None=未提供現金錨點)
    "problem_events", "problem_opportunities",          # #137 問題帳:事件規約 + Opportunity Check 快照
}
# SKILL Step 1「metrics:全 metric 快照」+ 對帳反查用鍵;收尾 CLI 另存 metrics_snapshot 全量快照
STATE_METRIC_KEYS = {
    "max_pos_pct", "max_pos_ticker", "avgdown_count", "avgdown_breach",
    "payoff", "ai_pct", "max_sector_pct", "top3_pct", "n_holdings",
    "beta", "alpha_ann", "alpha_t", "alpha_credible",   # alpha v2(#80):α 永遠出數,t 一起存
}

PASS = 0


def ok(cond, label, detail=""):
    global PASS
    if not cond:
        print(f"❌ FAIL: {label}  {detail}")
        sys.exit(1)
    PASS += 1
    print(f"  ✅ {label}")


def run_engine_offline(tmp, csv=None, state_name="last_state.json"):
    """跑一次 engine(TR_JSON + TR_STATE_OUT),注入假 yfinance 強制離線。"""
    shim = pathlib.Path(tmp) / "shim"
    if not shim.exists():
        shim.mkdir()
        (shim / "yfinance.py").write_text('raise ImportError("offline shim: 契約測試強制離線")\n',
                                          encoding="utf-8")
    state_out = pathlib.Path(tmp) / state_name
    import os
    env = dict(os.environ, TR_JSON="1", TR_STATE_OUT=str(state_out),
               PYTHONPATH=str(shim))
    r = subprocess.run([sys.executable, str(ENGINE), str(csv or MOCK_CSV)],
                       cwd=SKILL_DIR, env=env, capture_output=True, text=True, timeout=120)
    return r, state_out


def extract_skill_py_blocks():
    """抽出 SKILL.md 收尾段內嵌的 python heredoc(<<'PY' … PY)——真正的 state 消費者。"""
    text = SKILL_MD.read_text(encoding="utf-8")
    return re.findall(r"<<'PY'\n(.*?)\nPY\n", text, flags=re.S)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        r, state_path = run_engine_offline(tmp)

        # ── 1. TR_JSON 契約 ──
        ok(r.returncode == 0, "engine 離線跑 mock exit 0", r.stderr[-300:])
        ok("yfinance 未安裝" in r.stderr, "確實走離線降級路徑(shim 生效)", r.stderr[:200])
        card = json.loads(r.stdout)                       # stdout 必須是純 JSON,parse 失敗即紅
        ok(set(card.keys()) == TR_JSON_KEYS, "TR_JSON 頂層 key 恰等於 SKILL 消費清單",
           f"多了 {set(card.keys()) - TR_JSON_KEYS} / 少了 {TR_JSON_KEYS - set(card.keys())}")
        ok(card["schema_version"] == 1, "card schema_version == 1", repr(card["schema_version"]))
        ok("is_demo" not in card, "is_demo 已移除(#89):engine 對任何輸入路徑一致,無 demo 分支")
        ok(isinstance(card["philosophy"], str) and card["philosophy"], "philosophy 非空字串")

        cr = card["candidate_rules"]
        ok(isinstance(cr, list) and 1 <= len(cr) <= 3, "candidate_rules 是 1–3 條列表", repr(cr)[:120])
        ok(all(isinstance(x.get("rule"), str) and x["rule"] for x in cr),
           "candidate_rules 每條有非空 rule")

        tq = card["thesis_questions"]                     # 離線可為空(thesis_q 需價格);非空時驗形狀
        ok(isinstance(tq, list), "thesis_questions 是列表")
        ok(all(set(q.keys()) == {"ticker", "question"} for q in tq),
           "thesis_questions 元素 = {ticker, question}", repr(tq)[:120])

        th = card["top_holes"]
        ok(isinstance(th, list) and 1 <= len(th) <= 2,
           "top_holes 是 1–2 條(mock 設計必觸發洞)", repr([h.get('dim') for h in th]))
        need = {"dim", "severity", "tier_weight", "number_line", "lens_rule", "lens_quote", "raw"}
        ok(all(need <= set(h.keys()) for h in th), "top_holes 每條含敘事所需 7 欄位")

        dims = card["dims_raw"]
        ok(isinstance(dims, list) and len(dims) == 5, "dims_raw 恰 5 維", repr(len(dims)))
        ok(all({"dim", "severity", "triggered", "tier"} <= set(d.keys()) for d in dims),
           "每維含 dim/severity/triggered/tier")
        ok(set(card["data_integrity"].keys()) == {"orphan_sells", "unclassified_drivers"},
           "data_integrity = {orphan_sells, unclassified_drivers}")

        ov = card["overview"]
        need_ov = {"n_rt", "realized", "unrealized", "unrealized_coverage", "total_pnl",
                   "win_sum", "loss_sum", "n_wins", "n_losses", "avg_win", "avg_loss",
                   "payoff", "pf", "ab"}
        ok(set(ov.keys()) == need_ov, "overview 頂層欄位恰等於契約",
           f"diff: {set(ov.keys()) ^ need_ov}")
        cov = ov["unrealized_coverage"]
        ok(set(cov.keys()) == {"priced_n", "held_n", "unpriced"},
           "unrealized_coverage = {priced_n, held_n, unpriced}(#82)", repr(cov))
        ok(cov["priced_n"] + len(cov["unpriced"]) == cov["held_n"],
           "priced_n + len(unpriced) == held_n(覆蓋率算式自洽)", repr(cov))
        ok(cov["priced_n"] == 0 and len(cov["unpriced"]) == cov["held_n"] > 0,
           "強制離線 shim 下,全部持倉都抓不到現價 → unrealized_coverage 如實反映全未覆蓋",
           repr(cov))

        # ── honesty_ledger 契約(#82:JSON 模式補上人話卡本有的「該揭露清單」聚合)──
        hl = card["honesty_ledger"]
        ok(isinstance(hl, list), "honesty_ledger 是列表", repr(hl)[:120])
        ok(all({"key", "status", "data"} <= set(e.keys()) for e in hl),
           "honesty_ledger 每項 = {key,status,data}", repr(hl)[:150])
        HL_KEYS = {"alpha_credibility", "sector_attribution", "unclassified_drivers",
                   "unrealized_coverage", "orphan_sells", "currency_mix", "cash_reliability"}
        ok(all(e["key"] in HL_KEYS for e in hl),
           "honesty_ledger key 都在允許集合", repr([e["key"] for e in hl]))
        hl_keys = {e["key"] for e in hl}
        # 只收『觸發的』誠實缺口:離線 shim 全持倉無現價 → unrealized_coverage 必在(與 overview 聚合一致);
        # 單幣 mock → currency_mix 不觸發(空缺口不進 ledger,證明不是無條件塞滿)
        ok("unrealized_coverage" in hl_keys,
           "離線未覆蓋 → ledger 觸發 unrealized_coverage", repr(sorted(hl_keys)))
        ok("currency_mix" not in hl_keys,
           "單幣 mock → currency_mix 不進 ledger(觸發式,非無條件)", repr(sorted(hl_keys)))

        # ── 1a. #171 呈現層:card.cash 形狀 + 無 TR_CASH 錨點時降級,cash_reliability 觸發式 ──
        ccash = card["cash"]
        ok(isinstance(ccash, dict) and set(ccash.keys()) ==
           {"balance", "weight", "source", "reliable", "recent_net_deposit"},
           "card.cash 5 欄位齊(與 state.cash 同源)", repr(ccash)[:120])
        ok(ccash["source"] == "csv_sum" and ccash["reliable"] is False,
           "無 TR_CASH 錨點 → card.cash 降級 csv_sum + reliable=False", repr(ccash))
        # mock 淨買入 → balance<0 → weight=None(算不出,不上卡)→ cash_reliability 不觸發(只在有可誤導 weight 時才進 ledger)
        ok(ccash["weight"] is None and "cash_reliability" not in hl_keys,
           "無錨點且淨買入 → weight=None,cash_reliability 不進 ledger(不冒充也不空吠)", repr(sorted(hl_keys)))

        # ── 2. state 契約 ──
        st = json.loads(state_path.read_text(encoding="utf-8"))
        ok(set(st.keys()) == STATE_KEYS, "state 頂層 key 恰等於契約",
           f"多了 {set(st.keys()) - STATE_KEYS} / 少了 {STATE_KEYS - set(st.keys())}")
        ok(st["schema_version"] == 2, "state schema_version == 2", repr(st["schema_version"]))
        ok(set(st["metrics"].keys()) == STATE_METRIC_KEYS, "metrics 鍵集合恰等於契約",
           f"diff: {set(st['metrics'].keys()) ^ STATE_METRIC_KEYS}")
        ok(st["insufficient_data"] is False, "mock(19 筆跨 300+ 天)不觸發 insufficient gate")
        cm = st["commitment"]
        ok(isinstance(cm, dict) and set(cm.keys()) == {"rule", "metric_key", "metric_value", "goal"},
           "commitment = {rule, metric_key, metric_value, goal}", repr(cm)[:120])
        ok(cm["metric_key"] in st["metrics"], "commitment.metric_key 可在 metrics 反查(對帳的前提)")

        hold = st["holdings"]
        ok(set(hold.keys()) == {"as_of", "derived_from", "is_complete", "positions"},
           "holdings 結構 4 欄位")
        pos = hold["positions"]
        ok(len(pos) >= 1, "mock 至少 1 檔在手持倉")
        ok(cov["held_n"] == len(pos),
           "overview.unrealized_coverage.held_n 與 state.holdings.positions 檔數一致(同一份 held,對帳不分岔)",
           f"cov.held_n={cov['held_n']} vs len(pos)={len(pos)}")
        for t, p in pos.items():
            ok({"shares", "cost", "avg_cost", "cycle_start", "cycle_id"} <= set(p.keys()),
               f"position[{t}] 5 欄位齊")
            cid = p["cycle_id"]
            ok(bool(trade_recap.CYCLE_ID_RE.match(cid) or trade_recap.CYCLE_ID_UNKNOWN_RE.match(cid)),
               f"cycle_id 格式合契約({cid})——單一事實源 = engine.CYCLE_ID_RE")

        # ── 2a. #171 現金地基:state.cash 形狀 + 無 TR_CASH 錨點時降級為 csv_sum/不可信 ──
        cash = st["cash"]
        ok(isinstance(cash, dict) and set(cash.keys()) ==
           {"balance", "weight", "source", "reliable", "recent_net_deposit"},
           "state.cash 5 欄位齊(balance/weight/source/reliable/recent_net_deposit)", repr(cash)[:120])
        ok(cash["source"] == "csv_sum" and cash["reliable"] is False,
           "無 TR_CASH 錨點 → cash 降級 csv_sum + reliable=False(honesty 據此揭露,不冒充精確)",
           repr(cash))

        # ── 2b. #162 接線:main flow 的 held 必須走 FIFO 剩餘,不是 positions() 的 avg cost ──
        # 單元層(test_engine_units)測的是 fifo_held 純函式;這段釘「main() 真的接上它」——
        # 攤平後部分賣出,state.holdings.cost 必須=FIFO 剩餘批成本;接線混回 avg cost 即紅。
        fifo_csv = pathlib.Path(tmp) / "fifo_case.csv"
        fifo_csv.write_text("Symbol,Quantity,Price,Action,TradeDate,RecordType\n"
                            "XFIF,10,100.00,BUY,2026-01-05,Trade\n"
                            "XFIF,10,200.00,BUY,2026-02-05,Trade\n"
                            "XFIF,10,180.00,SELL,2026-03-05,Trade\n", encoding="utf-8")
        r2b, state2b_path = run_engine_offline(tmp, csv=fifo_csv, state_name="fifo_state.json")
        ok(r2b.returncode == 0, "#162 攤平部分賣出 case 離線 exit 0", r2b.stderr[-300:])
        p2b = json.loads(state2b_path.read_text(encoding="utf-8"))["holdings"]["positions"]["XFIF"]
        ok(abs(p2b["cost"] - 2000.0) < 1e-6,
           "#162 接線:holdings.cost = FIFO 剩餘批 2000(avg cost 混基礎會是 1500)",
           f"cost={p2b['cost']}")
        ok(abs(json.loads(r2b.stdout)["overview"]["realized"] - 800.0) < 1e-6,
           "#162:realized 維持 FIFO 配對 +800(基礎歸一動的是未實現側)")

        # ── 3. 收尾 CLI 煙霧測試(coach.py = 真消費者跑真 state;#148 heredoc 下沉)──
        blocks = extract_skill_py_blocks()
        ok(len(blocks) == 1, "SKILL.md 只剩 part 5a 一段 heredoc(part 1/2/4/5b 已 CLI 化,別長回來)",
           f"抽到 {len(blocks)} 段")
        ok(coach.CYCLE_ID_RE.pattern == trade_recap.CYCLE_ID_RE.pattern
           and coach.CYCLE_ID_UNKNOWN_RE.pattern == trade_recap.CYCLE_ID_UNKNOWN_RE.pattern,
           "coach ↔ trade_recap 的 CYCLE_ID 契約同一條(coach 刻意不 import engine,以此鎖複製 drift)")
        import os
        home = pathlib.Path(tmp) / "home"
        (home / ".trade-coach").mkdir(parents=True)
        # 用 engine 剛寫出的【真】state 餵 CLI——schema 漂移(如 #41 的 cycle_id)在這層陣亡
        (home / ".trade-coach" / "last_state.json").write_text(
            state_path.read_text(encoding="utf-8"), encoding="utf-8")
        env = dict(os.environ, HOME=str(home))
        COACH = str(SKILL_DIR / "engine" / "coach.py")

        # close:用戶親選規矩蓋過 engine 預設
        r1 = subprocess.run([sys.executable, COACH, "close",
                             "--rule", "測試規矩:單筆上限 20%", "--metric", "max_pos_pct"],
                            env=env, capture_output=True, text=True, timeout=60)
        ok(r1.returncode == 0, "coach close exit 0", r1.stderr[-300:])
        log_lines = (home / ".trade-coach" / "log.jsonl").read_text(encoding="utf-8").strip().splitlines()
        ok(len(log_lines) == 1, "log.jsonl append 恰 1 行")
        entry = json.loads(log_lines[0])
        ok(entry["commitment"]["rule"] == "測試規矩:單筆上限 20%",
           "log 存的是教練最終規矩(參數蓋過 engine 預設)")
        ok(entry["commitment"]["metric_key"] == "max_pos_pct"
           and entry["commitment"]["metric_value"] == st["metrics"]["max_pos_pct"],
           "commitment.metric_value 從 state.metrics 反查成功")
        ok(set(entry["metrics_snapshot"].keys()) == set(st["metrics"].keys()),
           "metrics_snapshot = state.metrics 全量快照(開場變化摘要的資料源,#129 PR-4)")
        r2 = subprocess.run([sys.executable, COACH, "close", "--rule", "SKIP"],
                            env=env, capture_output=True, text=True, timeout=60)
        ok(r2.returncode == 0 and json.loads(r2.stdout)["commitment"] is None,
           "close --rule SKIP → commitment null、metrics 照存(#56 這週不承諾)", r2.stdout[:200])
        r3 = subprocess.run([sys.executable, COACH, "close",
                             "--rule", "x", "--metric", "no_such_key"],
                            env=env, capture_output=True, text=True, timeout=60)
        ok(r3.returncode != 0, "close --metric 填錯 key 拒收(#148 gate:不再靜默存 None)",
           r3.stderr[-200:])

        # append-theses:空清單不炸;2 段 cycle_id 整批拒收;合法混排落 2 行 + id 生成
        tj = pathlib.Path(tmp) / "theses.json"
        tj.write_text("[]", encoding="utf-8")
        r4 = subprocess.run([sys.executable, COACH, "append-theses", str(tj),
                             "--session-date", st["date_end"]],
                            env=env, capture_output=True, text=True, timeout=60)
        ok(r4.returncode == 0, "append-theses 空清單跑不炸", r4.stderr[-300:])
        tj.write_text(json.dumps([{"ticker": "NVDA", "cycle_id": "NVDA#2024-01-12",
                                   "maturity": "inferred"}]), encoding="utf-8")
        r5 = subprocess.run([sys.executable, COACH, "append-theses", str(tj),
                             "--session-date", st["date_end"]],
                            env=env, capture_output=True, text=True, timeout=60)
        theses_file = home / ".trade-coach" / "theses.jsonl"
        ok(r5.returncode != 0 and not theses_file.exists(),
           "append-theses 2 段 cycle_id 整批拒收、0 筆落盤(#41 的坑在 CLI 陣亡)", r5.stderr[-200:])
        tj.write_text(json.dumps([
            {"ticker": "NVDA", "cycle_id": "NVDA#2024-01-12#1", "why": "w", "maturity": "inferred"},
            {"event": "exit_narrative", "ticker": "NVDA", "cycle_id": "NVDA#2024-01-12#1",
             "revisit_id": "NVDA#2026-07-01#40.0", "exit_date": "2026-07-01",
             "exit_reason": "thesis_broken", "capture": "user"}]), encoding="utf-8")
        r6 = subprocess.run([sys.executable, COACH, "append-theses", str(tj),
                             "--session-date", st["date_end"]],
                            env=env, capture_output=True, text=True, timeout=60)
        rows = [json.loads(x) for x in
                theses_file.read_text(encoding="utf-8").strip().splitlines()]
        ok(r6.returncode == 0 and len(rows) == 2
           and rows[0]["thesis_id"].startswith("NVDA-") and rows[0]["status"] == "active"
           and rows[1]["narrative_id"].startswith("exit-NVDA-")
           and all(x["session_date"] == st["date_end"] for x in rows),
           "append-theses 合法混排落 2 行;thesis_id/narrative_id/session_date 由 CLI 生成")

        # #36:emotion/confidence 選填但填了要在 enum 內——happy path 落盤保留;壞值整批拒收
        tj.write_text(json.dumps([{"ticker": "AMD", "cycle_id": "AMD#2024-02-01#1", "why": "w",
                                   "maturity": "inferred", "emotion": "fomo", "emotion_inferred": True,
                                   "confidence": "low", "confidence_inferred": True}]), encoding="utf-8")
        r6b = subprocess.run([sys.executable, COACH, "append-theses", str(tj),
                              "--session-date", st["date_end"]],
                             env=env, capture_output=True, text=True, timeout=60)
        amd = [json.loads(x) for x in theses_file.read_text(encoding="utf-8").strip().splitlines()
               if json.loads(x).get("ticker") == "AMD"]
        ok(r6b.returncode == 0 and amd and amd[0]["emotion"] == "fomo"
           and amd[0]["confidence"] == "low" and amd[0]["emotion_inferred"] is True,
           "#36 append-theses 保留合法 emotion/confidence + _inferred 旗標", r6b.stderr[-200:])
        tj.write_text(json.dumps([{"ticker": "MU", "cycle_id": "MU#2024-02-01#1", "why": "w",
                                   "maturity": "inferred", "emotion": "excited"}]), encoding="utf-8")
        r6c = subprocess.run([sys.executable, COACH, "append-theses", str(tj),
                              "--session-date", st["date_end"]],
                             env=env, capture_output=True, text=True, timeout=60)
        ok(r6c.returncode != 0 and "emotion" in r6c.stderr
           and "MU#" not in theses_file.read_text(encoding="utf-8"),
           "#36 append-theses 壞 emotion 值整批拒收(0 筆落盤,擋填錯值靜默存髒)", r6c.stderr[-200:])

        # append-rules:PKEY 對映 + rule_id/status/created 生成
        rj = pathlib.Path(tmp) / "rules.json"
        rj.write_text(json.dumps([{"text": "AI 暴險封頂 70%", "metric_key": "ai_pct",
                                   "source": "user_chosen"}]), encoding="utf-8")
        r7 = subprocess.run([sys.executable, COACH, "append-rules", str(rj),
                             "--created", st["date_end"]],
                            env=env, capture_output=True, text=True, timeout=60)
        rrow = json.loads((home / ".trade-coach" / "rules.jsonl")
                          .read_text(encoding="utf-8").strip())
        ok(r7.returncode == 0 and rrow["problem_key"] == "concentration"
           and rrow["status"] == "tracking" and rrow["created"] == st["date_end"]
           and rrow["rule_id"].startswith("rule-"),
           "append-rules metric_key→problem_key 對映 + rule_id/status/created 由 CLI 生成")

        # save-card:同日重跑遞增檔名,不蓋舊卡
        cf = pathlib.Path(tmp) / "card.md"
        cf.write_text("---\ndate: d\n---\n卡", encoding="utf-8")
        for _ in range(2):
            r8 = subprocess.run([sys.executable, COACH, "save-card", str(cf),
                                 "--date", "2026-07-07"],
                                env=env, capture_output=True, text=True, timeout=60)
        cards = sorted(p.name for p in (home / ".trade-coach" / "cards").glob("*.md"))
        ok(r8.returncode == 0 and cards == ["2026-07-07-2.md", "2026-07-07.md"],
           "save-card 同日重跑檔名遞增,不蓋舊卡", str(cards))

    print(f"\n✅ TR_JSON / state 契約測試全過({PASS} 項)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
