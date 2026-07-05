#!/usr/bin/env python3
"""TR_JSON / state 契約測試(#61)—— SKILL 唯一消費介面的紅綠燈。

守三條契約,任何欄位改名/消失/型別變化都必須讓 CI 變紅:
  1. TR_JSON 頂層 key 集合【恰等於】SKILL.md Step 1 消費清單(top_holes 改名 topHoles 這類突變在此陣亡)。
  2. TR_STATE_OUT 薄 state 的 key 集合 + metrics 鍵 + cycle_id 格式(單一事實源 = engine 的 CYCLE_ID_RE)。
  3. SKILL.md 內嵌的收尾 python 片段(log append / theses append)真的能吃 engine 寫出的 state
     —— #41 的 cycle_id 三段 vs 兩段漂移,就是漏了這層「消費者煙霧測試」。

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

# ── SKILL.md Step 1/3 消費清單:改 engine 輸出欄位 = 改這份契約,兩邊要一起動 ──
TR_JSON_KEYS = {
    "schema_version", "philosophy", "strength", "overview",
    "best_trade", "worst_trade", "what_if", "ticker_diagnosis",
    "thesis_questions", "top_holes", "candidate_rules", "prescriptions",
    "alpha_beta_breakdown", "payoff_attribution", "dims_raw", "data_integrity",
}
STATE_KEYS = {
    "schema_version", "date_start", "date_end", "n_trades", "n_round_trips",
    "n_held", "headline_dim", "headline_metric", "commitment", "metrics",
    "rule", "insufficient_data", "holdings",
}
# SKILL Step 1「metrics:全 metric 快照」+ 對帳反查用鍵;收尾片段另存 metrics_snapshot 4 鍵(子集)
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


def run_engine_offline(tmp):
    """跑一次 engine(TR_JSON + TR_STATE_OUT),注入假 yfinance 強制離線。"""
    shim = pathlib.Path(tmp) / "shim"
    shim.mkdir()
    (shim / "yfinance.py").write_text('raise ImportError("offline shim: 契約測試強制離線")\n',
                                      encoding="utf-8")
    state_out = pathlib.Path(tmp) / "last_state.json"
    import os
    env = dict(os.environ, TR_JSON="1", TR_STATE_OUT=str(state_out),
               PYTHONPATH=str(shim))
    r = subprocess.run([sys.executable, str(ENGINE), str(MOCK_CSV)],
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
        for t, p in pos.items():
            ok({"shares", "cost", "avg_cost", "cycle_start", "cycle_id"} <= set(p.keys()),
               f"position[{t}] 5 欄位齊")
            cid = p["cycle_id"]
            ok(bool(trade_recap.CYCLE_ID_RE.match(cid) or trade_recap.CYCLE_ID_UNKNOWN_RE.match(cid)),
               f"cycle_id 格式合契約({cid})——單一事實源 = engine.CYCLE_ID_RE")

        # ── 3. SKILL.md 收尾片段煙霧測試(真消費者跑真 state)──
        blocks = extract_skill_py_blocks()
        ok(len(blocks) >= 2, "SKILL.md 抽得到 ≥2 段收尾 python(log append / theses append)",
           f"抽到 {len(blocks)} 段")
        import os
        home = pathlib.Path(tmp) / "home"
        (home / ".trade-coach").mkdir(parents=True)
        # 用 engine 剛寫出的【真】state 餵 SKILL 片段——schema 漂移(如 #41 的 cycle_id)在這層陣亡
        (home / ".trade-coach" / "last_state.json").write_text(
            state_path.read_text(encoding="utf-8"), encoding="utf-8")
        env = dict(os.environ, HOME=str(home))
        r1 = subprocess.run([sys.executable, "-", "測試規矩:單筆上限 20%", "max_pos_pct"],
                            input=blocks[0], env=env, capture_output=True, text=True, timeout=60)
        ok(r1.returncode == 0, "收尾片段 1(log append)exit 0", r1.stderr[-300:])
        log_lines = (home / ".trade-coach" / "log.jsonl").read_text(encoding="utf-8").strip().splitlines()
        ok(len(log_lines) == 1, "log.jsonl append 恰 1 行")
        entry = json.loads(log_lines[0])
        ok(entry["commitment"]["rule"] == "測試規矩:單筆上限 20%",
           "log 存的是教練最終規矩(參數蓋過 engine 預設)")
        ok(entry["commitment"]["metric_key"] == "max_pos_pct"
           and entry["commitment"]["metric_value"] == st["metrics"]["max_pos_pct"],
           "commitment.metric_value 從 state.metrics 反查成功")
        ok(set(entry["metrics_snapshot"].keys()) == {"ai_pct", "max_pos_pct", "avgdown_count", "avgdown_breach"},
           "metrics_snapshot 4 鍵齊(週對帳的最小快照)")
        r2 = subprocess.run([sys.executable, "-"], input=blocks[1], env=env,
                            capture_output=True, text=True, timeout=60)
        ok(r2.returncode == 0, "收尾片段 2(theses append)空清單跑不炸", r2.stderr[-300:])

    print(f"\n✅ TR_JSON / state 契約測試全過({PASS} 項)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
