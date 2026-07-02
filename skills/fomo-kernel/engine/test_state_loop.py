#!/usr/bin/env python3
"""
測「最小心臟」:把一份交易 CSV 按時間切兩段,累積跑「初診 → 對帳」,
驗第二張卡有沒有【真的對帳第一張承諾的規矩】(而不是重新初診同一個洞)。

對應 requirements.md §10.3 的驗收項。不需網路:行為維(sizing/攤平)不靠 yfinance,
α/β 缺價格時走 note 分支,測試照常通過。

跑法:python3 test_state_loop.py   (預設 ../mock/mock_trades.csv,切點 2024-07-01)
"""
import csv, json, os, subprocess, sys, tempfile, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "trade_recap.py")
DEFAULT_CSV = os.path.join(HERE, "..", "mock", "mock_trades.csv")


def split_csv(src, cutoff, d1, d2):
    """按 TradeDate 把 src 切成兩個 CSV:< cutoff → d1(初診期)、>= cutoff → d2(增量期)。"""
    with open(src, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        header, rows = rd.fieldnames, list(rd)
    seg1 = [r for r in rows if (r.get("TradeDate") or "").strip() < cutoff]
    seg2 = [r for r in rows if (r.get("TradeDate") or "").strip() >= cutoff]
    for path, seg in ((d1, seg1), (d2, seg2)):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(seg)
    return len(seg1), len(seg2)


def run_engine(csv_paths, state_out):
    """跑 engine,設 TR_STATE_OUT 取結構化 state。回傳 state dict。"""
    env = dict(os.environ, TR_STATE_OUT=state_out)
    r = subprocess.run([sys.executable, ENGINE, *csv_paths],
                       env=env, capture_output=True, text=True, timeout=180)  # fail-fast:本機裝 yfinance 時別讓網路卡住整套(review)
    assert r.returncode == 0, f"engine 失敗:\n{r.stderr}"
    with open(state_out, encoding="utf-8") as f:
        return json.load(f)


def coach_turn(state, log_path):
    """模擬 SKILL.md 雙模式:讀 log → 空=初診 / 非空=對帳 → 收尾 append。
    回傳 (mode, 給人看的對帳敘述)。"""
    log = []
    if os.path.exists(log_path) and os.path.getsize(log_path) > 0:
        with open(log_path, encoding="utf-8") as f:
            log = [json.loads(ln) for ln in f if ln.strip()]

    if not log:                                              # ── 初診 ──
        mode = "初診"
        if state["insufficient_data"]:
            line = f"樣本不足(rt={state['n_round_trips']}),先不出 commitment(§4.4)"
        else:
            c = state["commitment"]
            line = (f"最大的洞 = {state['headline_dim']};"
                    f"★下次只改這一件 → {c['rule'][:22]}…;"
                    f"建立追蹤錨點:{c['metric_key']}={c['metric_value']}(目標 {c['goal']})")
    else:                                                    # ── 對帳 ──
        mode = "對帳"
        last = log[-1]
        lc = last.get("commitment")
        if not lc:
            line = "上次樣本不足、無 commitment → 這次重新初診"
        else:
            mk, old = lc["metric_key"], lc["metric_value"]
            new = state["metrics"].get(mk)
            if old is None or new is None:
                trend = "無法比較(缺值)"
            elif new < old:
                trend = f"↓改善(降 {old}→{new})"
            elif new > old:
                trend = f"↑退步(升 {old}→{new})"
            else:
                trend = f"→持平({old})"
            line = (f"上次承諾「{lc['rule'][:16]}…」 → 對帳 {mk}:{trend}。"
                    f"新一輪最大的洞 = {state['headline_dim']}")

    entry = {                                                # 收尾 append(只存薄狀態,不存交易)
        "ts_date_end": state["date_end"],
        "headline_dim": state["headline_dim"],
        "commitment": state["commitment"],
        "metrics_snapshot": {k: state["metrics"].get(k)
                             for k in ("max_pos_pct", "avgdown_count", "avgdown_breach")},
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return mode, line


def test_insufficient_span_gate():
    """#21.4:≥3 round-trip 但全擠在 <60 交易日(≈84 日曆日)→ 仍應判 insufficient。
    舊碼只查 len(rts)<3 會放行(把缺資料的猜測當已確認承諾);新碼加 span gate 才擋得住。
    長跨度 case 反向守:同樣 3 round-trip、跨度 >84 日,不該被 span gate 誤殺。"""
    tmp = tempfile.mkdtemp(prefix="tr_span_test_")
    HEADER = "Symbol,Action,Quantity,Price,TradeDate,RecordType\n"
    def _csv(name, trades):
        p = os.path.join(tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(HEADER)
            for sym, act, qty, px, d in trades:
                f.write(f"{sym},{act},{qty},{px},{d},Trade\n")
        return p
    short = _csv("short.csv", [                                # 3 round-trip,跨度 39 天(<84)
        ("AAA", "BUY", 10, 100, "2024-01-02"), ("AAA", "SELL", 10, 110, "2024-01-10"),
        ("BBB", "BUY", 10, 100, "2024-01-15"), ("BBB", "SELL", 10, 90,  "2024-01-25"),
        ("CCC", "BUY", 10, 100, "2024-02-01"), ("CCC", "SELL", 10, 120, "2024-02-10")])
    long_ = _csv("long.csv", [                                 # 同 3 round-trip,跨度 ~280 天(>84)
        ("AAA", "BUY", 10, 100, "2024-01-02"), ("AAA", "SELL", 10, 110, "2024-02-10"),
        ("BBB", "BUY", 10, 100, "2024-04-15"), ("BBB", "SELL", 10, 90,  "2024-06-25"),
        ("CCC", "BUY", 10, 100, "2024-08-01"), ("CCC", "SELL", 10, 120, "2024-10-08")])
    s_short = run_engine([short], os.path.join(tmp, "st_short.json"))
    s_long = run_engine([long_], os.path.join(tmp, "st_long.json"))
    assert s_short["n_round_trips"] == 3, f"short 應有 3 rt,實得 {s_short['n_round_trips']}"
    assert s_long["n_round_trips"] == 3, f"long 應有 3 rt,實得 {s_long['n_round_trips']}"
    assert s_short["insufficient_data"] is True, \
        "≥3 rt 但跨度<84 日應判 insufficient(#21.4 span gate;舊碼 len(rts)<3 會在此漏放)"
    assert s_long["insufficient_data"] is False, \
        "≥3 rt 且跨度>84 日不該被 span gate 誤殺"
    assert s_short["commitment"] is None, "insufficient → commitment 必須為 None(§4.4)"
    print("✅ #21.4 insufficient span gate（<60 交易日擋假承諾 / 長跨度不誤殺）全過\n")


def test_classify_adds_fixes():
    """#41 G:① 首筆建倉不算加碼(loss_ratio 不被稀釋、n_adds 不 overcount)
    ② oversell 不讓股數變負污染後續加碼偵測(clamp 對齊 positions)。"""
    from trade_recap import classify_adds
    def _B(t, q, px, d): return {"ticker": t, "side": "buy", "qty": q, "price": px, "date": dt.date.fromisoformat(d)}
    def _S(t, q, px, d): return {"ticker": t, "side": "sell", "qty": q, "price": px, "date": dt.date.fromisoformat(d)}
    # G1:1 筆初始建倉(高價)+ 3 筆虧損加碼 → loss_ratio 應 3/3=1.0、n_adds=3(舊碼含首筆 = 3/4=0.75、n=4)
    g1 = classify_adds([_B("AAA", 100, 100, "2024-01-01"), _B("AAA", 100, 90, "2024-02-01"),
                        _B("AAA", 100, 80, "2024-03-01"), _B("AAA", 100, 70, "2024-04-01")])
    assert g1["AAA"]["n_adds"] == 3, f"首筆不該算加碼,期望 n_adds=3,實得 {g1['AAA']['n_adds']}"
    assert abs(g1["AAA"]["loss_ratio"] - 1.0) < 1e-9, \
        f"3 筆加碼全虧 → loss_ratio 應=1.0(舊碼含首筆會被稀釋成 0.75),實得 {g1['AAA']['loss_ratio']}"
    # G2:oversell(持 100 賣 200)後再進場、再虧損加碼 → 不因股數變負而漏掉那筆加碼
    g2 = classify_adds([_B("BBB", 100, 100, "2024-01-01"), _S("BBB", 200, 110, "2024-01-15"),
                        _B("BBB", 100, 80, "2024-02-01"), _B("BBB", 100, 70, "2024-03-01")])
    assert g2["BBB"]["loss_ratio"] > 0.0, \
        f"oversell 後的虧損加碼應被偵測(舊碼股數變負會漏 → loss_ratio=0.0),實得 {g2['BBB']['loss_ratio']}"
    # G3(review 補):2 筆全虧加碼、間隔不規律 → 不該因單一 gap 的 pstdev=0 被誤判「規律」而標定投
    g3 = classify_adds([_B("ZZZ", 100, 100, "2024-01-01"), _B("ZZZ", 100, 90, "2024-01-02"),
                        _B("ZZZ", 100, 80, "2024-04-01")])
    assert g3["ZZZ"]["cls"] != "疑似定投", \
        f"2 筆不規律全虧加碼不該標定投(單一 gap pstdev=0 誤判規律),實得 {g3['ZZZ']['cls']}"
    print("✅ #41 G classify_adds（首筆不算加碼 / oversell 不污染 / 單一gap不誤判規律）全過\n")


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
    cutoff = sys.argv[2] if len(sys.argv) > 2 else "2024-07-01"

    # current_cycles 邊界單元測（雙審 codex：補 oversell/重建/缺期初，不只檢查欄位存在）
    from trade_recap import current_cycles
    def _R(t, s, q, d): return {"ticker": t, "side": s, "qty": q, "date": dt.date.fromisoformat(d)}
    _c = current_cycles([_R("X","buy",10,"2024-01-01"), _R("X","sell",50,"2024-02-01"),
                         _R("X","buy",5,"2024-03-01"), _R("X","buy",5,"2024-03-15")])
    assert _c.get("X") == {"start":"2024-03-01","seq":2}, f"oversell 污染 cycle: {_c}"   # 舊 bug：sh 跌負→seq=3
    _c = current_cycles([_R("Y","buy",10,"2024-01-01"), _R("Y","sell",10,"2024-02-01"), _R("Y","buy",5,"2024-03-01")])
    assert _c.get("Y") == {"start":"2024-03-01","seq":2}, f"清倉重建 seq: {_c}"
    _c = current_cycles([_R("Z","sell",50,"2024-01-01"), _R("Z","buy",10,"2024-02-01")])
    assert _c.get("Z") == {"start":"2024-02-01","seq":1}, f"缺期初: {_c}"
    print("✅ current_cycles 邊界（oversell / 清倉重建 / 缺期初）全過\n")

    test_insufficient_span_gate()                              # #21.4:60 交易日 gate
    test_classify_adds_fixes()                                 # #41 G:首筆/oversell

    tmp = tempfile.mkdtemp(prefix="tr_state_test_")
    seg1, seg2 = os.path.join(tmp, "seg1.csv"), os.path.join(tmp, "seg2.csv")
    log = os.path.join(tmp, "log.jsonl")
    n1, n2 = split_csv(src, cutoff, seg1, seg2)
    print(f"切點 {cutoff}:seg1 {n1} 列 / seg2 {n2} 列(累積對帳:第二次餵 seg1+seg2)\n")

    # ── 第一次:初診(只有 seg1)──
    st1 = run_engine([seg1], os.path.join(tmp, "state1.json"))
    m1, l1 = coach_turn(st1, log)
    print(f"【第 1 張卡 · {m1}】 期間 {st1['date_start']}~{st1['date_end']}"
          f"(交易 {st1['n_trades']}、round-trip {st1['n_round_trips']})")
    print(f"  {l1}\n")

    # ── 第二次:對帳(seg1+seg2 累積,模擬用戶補上最新對帳單)──
    st2 = run_engine([seg1, seg2], os.path.join(tmp, "state2.json"))
    m2, l2 = coach_turn(st2, log)
    print(f"【第 2 張卡 · {m2}】 期間 {st2['date_start']}~{st2['date_end']}"
          f"(交易 {st2['n_trades']}、round-trip {st2['n_round_trips']})")
    print(f"  {l2}\n")

    # ── 驗收(§10.3):第二張卡必須【基於第一張承諾的維度】對帳,不是重新初診 ──
    ok = []
    ok.append(("第一張產生了 commitment", st1["commitment"] is not None))
    ok.append(("第二張是『對帳』模式(讀到上次 log)", m2 == "對帳"))
    last_commit_mk = st1["commitment"]["metric_key"] if st1["commitment"] else None
    ok.append(("第二張對帳的是『第一張承諾那一維』的 metric",
               last_commit_mk is not None and last_commit_mk in st2["metrics"]))
    # 真正基於上次:對帳敘述裡帶了第一張的 metric_key + 新舊值對比
    ok.append(("對帳敘述引用了上次承諾的 metric_key", last_commit_mk and last_commit_mk in l2))
    ok.append(("log.jsonl 累積了兩輪(記憶+持續)",
               sum(1 for _ in open(log, encoding="utf-8")) == 2))
    # Phase B：holdings snapshot（目標3）回歸保護 + 守雙審採納
    ok.append(("state schema v2 + 有 holdings snapshot",
               st2.get("schema_version") == 2 and bool(st2.get("holdings", {}).get("positions"))))
    ok.append(("holdings 每檔有 cycle_id + 絕對值、不含 weight（雙審 gemini#4）",
               all("cycle_id" in p and "shares" in p and "weight" not in p
                   for p in st2["holdings"]["positions"].values())))
    ok.append(("holdings 標 is_complete=False（不宣稱完整持倉，雙審 codex#3）",
               st2["holdings"].get("is_complete") is False))

    print("── 驗收 ──")
    allok = True
    for name, cond in ok:
        print(f"  {'✅' if cond else '❌'} {name}")
        allok = allok and cond
    # 額外印出對帳的實際數字,讓人眼確認「真的在比同一維」
    mk = last_commit_mk
    if mk:
        print(f"\n  對帳實證:承諾規矩追蹤 {mk} = "
              f"{st1['metrics'].get(mk)}(初診)→ {st2['metrics'].get(mk)}(對帳)")
    print(f"\n{'✅ 通過:第二張卡真的在對帳第一張的規矩,不是重新初診。' if allok else '❌ 失敗'}")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
