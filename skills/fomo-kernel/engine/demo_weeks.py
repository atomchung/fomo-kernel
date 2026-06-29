#!/usr/bin/env python3
"""
三週體驗模擬:把一份交易 CSV 按時間切成 N 個【累積】段(模擬你分 N 次回來復盤),
逐次跑「初診 → 對帳 → 對帳…」,把每一週的卡【怎麼引用上一週的承諾】、
本機狀態 ~/.trade-coach/log.jsonl 【怎麼一行行長出來】show 給你看。

跟 test_state_loop.py 的差別:那支是 pass/fail 驗收;這支是給人看的【體驗 demo】。

跑法:
  python3 demo_weeks.py                                   # mock,預設切點
  python3 demo_weeks.py <你的CSV> 2024-06-15,2024-09-15   # 真實資料 + 自訂切點
  TR_DRIVER_MAP=driver.json python3 demo_weeks.py <CSV> …  # 真實持倉的 driver map
全程在獨立 temp 目錄跑,不碰你正式的 ~/.trade-coach。
"""
import csv, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(HERE, "trade_recap.py")
DEFAULT_CSV = os.path.join(HERE, "..", "mock", "mock_trades.csv")
PCT = lambda v: f"{v*100:.0f}%" if isinstance(v, (int, float)) else "—"


def cumulative_csvs(src, cuts, outdir):
    """按 cuts(遞增日期)把 src 切成累積段:段 k = TradeDate < cuts[k](最後一段=全部)。"""
    with open(src, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        header, rows = rd.fieldnames, list(rd)
    bounds = list(cuts) + ["9999-99-99"]
    paths = []
    for k, b in enumerate(bounds):
        keep = [r for r in rows if (r.get("TradeDate") or "").strip() < b]
        p = os.path.join(outdir, f"week{k+1}.csv")
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header); w.writeheader(); w.writerows(keep)
        paths.append(p)
    return paths


def run_engine(csv_path, state_out):
    env = dict(os.environ, TR_STATE_OUT=state_out)
    r = subprocess.run([sys.executable, ENGINE, csv_path], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"engine 失敗:\n{r.stderr}")
    return json.load(open(state_out, encoding="utf-8"))


def rule_plain(rule):
    return (rule or "").split(";")[0].split("(")[0][:30]


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
    cuts = (sys.argv[2].split(",") if len(sys.argv) > 2 else ["2024-06-15", "2024-09-15"])
    tmp = tempfile.mkdtemp(prefix="tr_demo_")
    log = os.path.join(tmp, "log.jsonl")
    weeks = cumulative_csvs(src, cuts, tmp)
    labels = ["初次使用(初診)", "回來復盤(對帳)", "再回來(對帳)"] + \
             [f"第{k+1}次(對帳)" for k in range(3, len(weeks))]

    print(f"資料源:{os.path.basename(src)} → 切成 {len(weeks)} 個累積段(模擬分 {len(weeks)} 次回來)\n")
    for k, wcsv in enumerate(weeks):
        st = run_engine(wcsv, os.path.join(tmp, f"state{k+1}.json"))
        prior = [json.loads(l) for l in open(log, encoding="utf-8")] if os.path.exists(log) else []

        print("━" * 64)
        print(f"  第 {k+1} 週 · {labels[k]}    "
              f"{st['date_start']}~{st['date_end']} · {st['n_trades']} 筆 / {st['n_round_trips']} round-trip")
        print("━" * 64)

        # ── 對帳:有上一週、且上週有 commitment → 先比上週那條規矩 ──
        if prior and prior[-1].get("commitment"):
            c = prior[-1]["commitment"]
            mk, old = c["metric_key"], c["metric_value"]
            new = st["metrics"].get(mk)
            fmt = PCT if mk == "max_pos_pct" else (lambda v: v)
            if old is None or new is None:
                verdict = "缺值,無法比"
            elif new < old:
                verdict = f"↓ 在改善({fmt(old)} → {fmt(new)})"
            elif new > old:
                verdict = f"↑ 變糟了({fmt(old)} → {fmt(new)})"
            else:
                verdict = f"→ 沒動({fmt(old)})"
            print(f"  ✅ 先對帳上週承諾「{rule_plain(c['rule'])}…」")
            print(f"      盯的數字 {mk}:{verdict}")
        elif prior:
            print("  ✅ 先對帳上週:上週樣本不足、沒立規矩,這次重新初診")
        else:
            print("  (第一次,沒有上週可對帳 → 純初診)")

        # ── 新一輪的洞 + 本週承諾 ──
        if st["insufficient_data"]:
            print(f"  ⚠ 本週樣本還太薄(round-trip {st['n_round_trips']}),只做體檢、不硬立規矩")
        else:
            hk = st["headline_metric"]; hv = hk.get("value")
            fmt = PCT if hk.get("key") == "max_pos_pct" else (lambda v: v)
            print(f"  🔴 本週最大的洞:{st['headline_dim']}（{hk.get('key')} = {fmt(hv)}）")
            c = st["commitment"]
            if c:
                print(f"  ★ 本週承諾:{rule_plain(c['rule'])}… → 之後盯 {c['metric_key']}")

        # ── 收尾 append(模擬 SKILL 收尾)→ context 長一行 ──
        entry = {"week": k + 1, "date_end": st["date_end"], "headline_dim": st["headline_dim"],
                 "commitment": st["commitment"],
                 "metrics_snapshot": {m: st["metrics"].get(m)
                                      for m in ("max_pos_pct", "avgdown_count", "avgdown_breach")}}
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"  💾 收尾:本機記憶 log.jsonl 現在 {k+1} 行\n")

    # ── 把累積出來的 context 攤開給你看 ──
    print("═" * 64)
    print("  跑完後,~/.trade-coach/log.jsonl 累積成這樣(這就是『記憶』):")
    print("═" * 64)
    for l in open(log, encoding="utf-8"):
        e = json.loads(l)
        c = e.get("commitment")
        cm = f"承諾 {c['metric_key']}={c['metric_value']}" if c else "無承諾(樣本不足)"
        print(f"  週{e['week']} | 到 {e['date_end']} | 洞={e['headline_dim']} | {cm}")
    print("\n每次回來:讀最後一行的承諾 → 重算 → 比那個數字 → 報進度 → 找新洞 → 再 append。")


if __name__ == "__main__":
    main()
