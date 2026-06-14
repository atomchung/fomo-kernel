#!/usr/bin/env python3
"""
trade-review · trade-recap engine v0.2
實作 5 維行為診斷算法 → 一張 VY 鏡片復盤卡的「機械層」(抓大放小)。
純函式：trades CSV → 5 維 metrics → 卡片(選 top 1-2)。動機那層由 SKILL.md 的對話流程補。

用法：python3 trade_recap.py [trades.csv ...]   (預設吃 ../mock/mock_trades.csv)
隱私：本檔不含任何真實帳戶路徑;預設只跑 mock 資料。用戶自己的 CSV 由參數傳入,留在本機。
"""
import csv, os, sys, statistics, datetime as dt
from collections import defaultdict, deque

DEFAULT_CSV = os.path.join(os.path.dirname(__file__), "..", "mock", "mock_trades.csv")

N_FWD = 30          # 賣出後 N 交易日看續漲（tunable）
SELL_EARLY_TH = 0.10
SECTOR_MAX_TH = 0.50
RF_ANNUAL = 0.043   # 無風險利率(年)：美國短期國庫券約 4.3%，Jensen's Alpha 用（tunable）

# ── ticker → (sector, thematic?)  thematic=1 代表同屬一個跨產業主題(如 AI capex)= VY B2 的「driver」──
# 這張表只是「常見股 fallback」。主路徑:SKILL 指引 Claude 對『實際持倉』用世界知識生成 driver map
# (含冷門股 + 跨 sector 主題),寫成 JSON 餵進來覆寫 → 冷門股不再變「未分類」、分散維不失準。
DRIVER_FALLBACK = {
    # 半導體
    "NVDA":("半導體",1),"AMD":("半導體",1),"MU":("半導體",1),"AVGO":("半導體",1),"ARM":("半導體",1),
    "TSM":("半導體",1),"MRVL":("半導體",1),"ALAB":("半導體",1),"CRDO":("半導體",1),"ASML":("半導體",1),
    "LRCX":("半導體",1),"AMAT":("半導體",1),"KLAC":("半導體",1),"INTC":("半導體",1),"DRAM":("半導體",1),
    # 軟體 / 雲 / AI 應用
    "ORCL":("軟體雲",1),"PLTR":("軟體雲",1),"GOOG":("軟體雲",1),"GOOGL":("軟體雲",1),"META":("軟體雲",1),
    "MSFT":("軟體雲",1),"NOW":("軟體雲",1),"CRWD":("軟體雲",1),"NET":("軟體雲",1),"DDOG":("軟體雲",1),
    "RBRK":("軟體雲",1),"SNOW":("軟體雲",1),"PANW":("軟體雲",1),
    # AI 資料中心電力 / 核能 / 儲能
    "VRT":("資料中心電力",1),"NUKZ":("資料中心電力",1),"SMR":("資料中心電力",1),"OKLO":("資料中心電力",1),
    "EOSE":("資料中心電力",1),"GEV":("資料中心電力",1),"CEG":("資料中心電力",1),
    "TSLA":("電動車AI",1),
    # 非 AI driver
    "MSTR":("加密",0),"COIN":("加密",0),"MARA":("加密",0),
    "HOOD":("金融科技",0),"SOFI":("金融科技",0),"GRAB":("金融科技",0),"NU":("金融科技",0),
    "CAVA":("消費",0),"CELH":("消費",0),"HIMS":("消費",0),
    "MP":("稀土材料",0),"ONDS":("無人機國防",0),"NOK":("電信",0),
    # ETF / 大盤 / 商品 / 債
    "SPY":("大盤ETF",0),"VT":("大盤ETF",0),"VOO":("大盤ETF",0),"EWY":("區域ETF",0),
    "IAU":("商品",0),"IEF":("債券",0),
}
_DRIVER_MAP = dict(DRIVER_FALLBACK)
def driver(t): return _DRIVER_MAP.get(t, ("未分類", 0))
def load_driver_map(path):
    """載入 Claude 生成的 {ticker: [sector, thematic]} JSON 覆寫 fallback。讓冷門股也有正確 driver。"""
    import json
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
        for t, v in m.items():
            _DRIVER_MAP[t] = (v[0], int(v[1]))
        return len(m)
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return 0

# ─────────────────────────── 1. 解析 ───────────────────────────
def load(paths):
    rows = []
    seen = set()        # 多份對帳單(完整歷史 + 最新增量)重疊期去重 → 合併到最新日期
    for p in paths:
        with open(p, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if (r.get("RecordType") or "").strip() != "Trade":
                    continue
                act = (r.get("Action") or "").strip().upper()
                if act not in ("BUY", "SELL"):
                    continue
                sym = (r.get("Symbol") or "").strip()
                if not sym:
                    continue
                try:
                    qty = abs(float(r["Quantity"])); px = float(r["Price"])
                except (ValueError, KeyError):
                    continue
                if px <= 0 or qty <= 0:   # 濾掉 split/free-share/journal 等 price=0 的偽交易
                    continue
                d = dt.date.fromisoformat(r["TradeDate"].strip())
                rec = (sym, act.lower(), round(qty, 2), round(px, 4), d)
                if rec in seen:           # 完全相同的交易 = 重疊期重複,跳過
                    continue
                seen.add(rec)
                rows.append(dict(ticker=sym, side=act.lower(), qty=qty, price=px, date=d))
    rows.sort(key=lambda x: x["date"])
    return rows

# ───────────────────── 2. FIFO round-trip 配對 ─────────────────────
def round_trips(rows):
    lots = defaultdict(deque)   # ticker -> deque[(qty, price, date)]
    rts = []
    for r in rows:
        t = r["ticker"]
        if r["side"] == "buy":
            lots[t].append([r["qty"], r["price"], r["date"]])
        else:
            q = r["qty"]
            while q > 1e-9 and lots[t]:
                lot = lots[t][0]
                take = min(q, lot[0])
                ret = (r["price"] - lot[1]) / lot[1]
                hold = (r["date"] - lot[2]).days
                rts.append(dict(ticker=t, entry=lot[2], exit=r["date"], qty=take,
                                buy_px=lot[1], sell_px=r["price"], ret=ret, hold=hold))
                lot[0] -= take; q -= take
                if lot[0] <= 1e-9:
                    lots[t].popleft()
    return rts, lots

# ───────────────────── 3. 持倉重建（成本基礎）─────────────────────
def positions(rows):
    pos = defaultdict(lambda: [0.0, 0.0])   # ticker -> [shares, cost_total]
    avg_down = []                            # 虧損中加碼事件
    for r in rows:
        t = r["ticker"]; sh, cost = pos[t]
        if r["side"] == "buy":
            if sh > 1e-9 and r["price"] < (cost / sh) * 0.90:  # 買價比 avg_cost 低 >10% = 有意義攤平（濾掉微幅 DCA）
                avg_down.append(dict(ticker=t, date=r["date"], px=r["price"], avg=cost/sh))
            pos[t][0] += r["qty"]; pos[t][1] += r["qty"] * r["price"]
        else:
            if sh > 1e-9:
                ac = cost / sh
                pos[t][1] -= min(r["qty"], sh) * ac
                pos[t][0] -= r["qty"]
    held = {t: (sh, c) for t, (sh, c) in pos.items() if sh > 1e-6}
    return held, avg_down

# ───────────────────── 4. yfinance 補價（賣太早）─────────────────────
def fetch_prices(tickers, start):
    try:
        import yfinance as yf
    except ImportError:
        return None, "yfinance 未安裝"
    try:
        data = yf.download(sorted(set(tickers) | {"SPY", "QQQ", "SOXX"}), start=start,
                           progress=False, auto_adjust=True)["Close"]
    except Exception as e:
        return None, f"yfinance 下載失敗: {e}"
    if data is None or data.empty:
        return None, "yfinance 無資料"
    if data.ndim == 1:
        data = data.to_frame()
    return data, None

def adaptive_n_fwd(rows):
    """賣出後觀察窗隨資料長度自適應:資料短就用短窗,讓半年資料的近端賣出也算得到 winner_early。"""
    span = (rows[-1]["date"] - rows[0]["date"]).days
    return 30 if span >= 365 else 20 if span >= 120 else 10   # ≥1年→30d,半年→20d,更短→10d

def fwd_from_px(rts, data, n_fwd=N_FWD):
    import pandas as pd
    if data is None:
        return None, None
    fwds, last_px = [], {}
    for r in rts:
        t = r["ticker"]
        if t not in data.columns: continue
        col = data[t].dropna()
        if col.empty: continue
        last_px[t] = float(col.iloc[-1])
        after = col[col.index > pd.Timestamp(r["exit"])]
        if len(after) == 0: continue
        target = after.iloc[min(n_fwd-1, len(after)-1)]
        r["fwd"] = (float(target) - r["sell_px"]) / r["sell_px"]
        r["fwd_trunc"] = len(after) < n_fwd
        fwds.append(r["fwd"])
    return fwds, last_px

def _port_daily_returns(rows, px):
    """重建投組日報酬序列(昨日持股 × 今日價,排除當日買賣現金流)。"""
    import pandas as pd
    from collections import defaultdict
    days = list(px.index)
    ev = defaultdict(list)
    for r in rows:
        ev[pd.Timestamp(r["date"])].append(r)
    shares = defaultdict(float); prev = {}; port_rets = {}
    for i, day in enumerate(days):
        if i > 0 and prev:
            num = den = 0.0
            for t, sh in prev.items():
                if sh == 0 or t not in px.columns: continue
                p1 = px[t].iloc[i]; p0 = px[t].iloc[i-1]
                if pd.isna(p1) or pd.isna(p0): continue
                num += sh * p1; den += sh * p0
            if den > 0: port_rets[day] = num / den - 1
        for r in ev.get(day, []):
            shares[r["ticker"]] += r["qty"] if r["side"] == "buy" else -r["qty"]
        prev = dict(shares)
    return pd.Series(port_rets)

def _regress(port, bench_px, rf_annual):
    """對單一 benchmark 回歸 → β / Jensen α / 超額報酬。"""
    import pandas as pd
    df = pd.DataFrame({"p": port, "s": bench_px.pct_change()}).dropna()
    df = df[df["p"].abs() < 0.5]                 # 去離群(split/資料錯)
    if len(df) < 60: return None
    rf_d = rf_annual / 252.0
    beta = df["p"].cov(df["s"]) / df["s"].var()
    alpha_ann = (df["p"].mean() - (rf_d + beta * (df["s"].mean() - rf_d))) * 252.0
    port_tot = (1 + df["p"]).prod() - 1
    bench_tot = (1 + df["s"]).prod() - 1
    return dict(beta=beta, alpha_ann=alpha_ann, port_tot=port_tot,
                bench_tot=bench_tot, excess=port_tot - bench_tot, n=len(df))

BENCH_LABEL = {"SPY": "大盤", "QQQ": "科技股", "SOXX": "半導體"}

def dim_alpha_beta(rows, data, rf_annual=RF_ANNUAL):
    """E2：投組日報酬 vs 多基準回歸。多基準才誠實——贏 SPY 可能只是贏在板塊,不是 alpha。"""
    try:
        import pandas as pd  # noqa
    except ImportError:
        return dict(dim="alpha/beta", note="無 pandas")
    if data is None or "SPY" not in list(getattr(data, "columns", [])):
        return dict(dim="alpha/beta", note="無價格/SPY")
    px = data.ffill()
    port = _port_daily_returns(rows, px)
    benchmarks = {}
    for b in ["SPY", "QQQ", "SOXX"]:
        if b in px.columns:
            r = _regress(port, px[b], rf_annual)
            if r: benchmarks[b] = r
    if "SPY" not in benchmarks:
        return dict(dim="alpha/beta", note="樣本不足")
    spy = benchmarks["SPY"]
    return dict(dim="alpha/beta", tier=3, benchmarks=benchmarks, n=spy["n"],
                beta=spy["beta"], alpha_ann=spy["alpha_ann"],          # 頂層保留 SPY 值給 overview
                port_tot=spy["port_tot"], spy_tot=spy["bench_tot"], excess_vs_spy=spy["excess"])

def print_alpha_beta(d):
    print("\n" + "─"*60)
    print("  你的報酬怎麼來的 · 把運氣(大盤+賽道)和技巧(選股)分開")
    if d.get("note"):
        print(f"    （{d['note']}）"); return
    bs = d["benchmarks"]
    spy = bs["SPY"]
    port = spy["port_tot"]; vs_spy = spy["excess"]
    print(f"    過去 {d['n']} 個交易日:投組 {port*100:+.0f}%、大盤 SPY {spy['bench_tot']*100:+.0f}% → 你贏大盤 {vs_spy*100:+.0f}pp")
    print(f"    ① alpha(vs 通用大盤,調風險後):{spy['alpha_ann']*100:+.0f}%/年,β {spy['beta']:.2f}（波動是大盤 {spy['beta']:.1f} 倍）")
    print(f"    ② 你贏大盤的 {vs_spy*100:+.0f}pp,有多少是『選股』、多少是『押對賽道』?——換對照看敏感度:")
    for sk, tag in [("QQQ", "科技,較中性"), ("SOXX", "半導體,事後最強板塊→偏嚴苛")]:
        if sk in bs:
            pick = port - bs[sk]["bench_tot"]      # 你 vs 該賽道 = 純選股力
            print(f"         你的選股 vs {sk}({tag}):  {pick*100:>+5.0f}pp")
    if "QQQ" in bs:                                  # verdict 用較中性的 QQQ,不用事後最強的 SOXX
        pick_n = port - bs["QQQ"]["bench_tot"]
        if pick_n < -0.05:
            print(f"    ▸ 準確認知:連較中性的 QQQ 你選股都輸 {-pick_n*100:.0f}pp,選股確實是弱項——"
                  f"但別全盤否定:ETF 也買爛股、錯過妖股,選股的上限你還留著。")
        else:
            print(f"    ▸ 準確認知:你選股贏中性的 QQQ {pick_n*100:+.0f}pp,只是跑不贏事後最強的 SOXX。"
                  f"選股不算爛,別被嚴苛基準嚇到。")
    print(f"    ⚠️ 基準的坑:SOXX 是『事後』最強板塊,拿它比有存活者偏差(馬後炮),你也不一定該全押半導體。"
          f"\n       真正公平的對照是『你當時板塊配置的混合』;這版先用現成 ETF 近似,重點看『選股 alpha 對基準多敏感』。")
    print(f"    (持倉法日報酬近似;alpha 基準=通用大盤 SPY。)")

# ─────────────────────────── 5. 五維 metrics ───────────────────────────
MIN_WINNERS = 5     # winner_early 至少要這麼多「賣掉的贏家」才算可信(半年資料通常達得到)

def dim_exit(rts, fwds):
    # rts 已在 main 預先濾成「決策賣出」（排除大盤/債/商品 ETF 再平衡）
    early_rate = avg_forgone = winner_early = None
    n_winners = 0
    scored = [r for r in rts if "fwd" in r]
    n_trunc = sum(1 for r in scored if r.get("fwd_trunc"))
    if scored:
        early_rate = sum(1 for r in scored if r["fwd"] > SELL_EARLY_TH) / len(scored)
        avg_forgone = statistics.mean(r["fwd"] for r in scored)
        winners = [r for r in scored if r["ret"] > 0]                 # 賣掉賺錢的
        n_winners = len(winners)
        if winners:
            winner_early = sum(1 for r in winners if r["fwd"] > SELL_EARLY_TH) / len(winners)
    low_conf = n_winners < MIN_WINNERS    # 樣本太少 → 仍給數字但標「低信賴」,不直接降權消失
    win_holds = [r["hold"] for r in rts if r["ret"] > 0]
    lose_holds = [r["hold"] for r in rts if r["ret"] < 0]
    hw = statistics.median(win_holds) if win_holds else 0
    hl = statistics.median(lose_holds) if lose_holds else 0
    disp = hl - hw
    win_rate = sum(1 for r in rts if r["ret"] > 0) / len(rts) if rts else 0
    sev = max((early_rate or 0), (winner_early or 0), (avg_forgone or 0)/0.2, disp/60)
    if low_conf: sev *= 0.7              # 低樣本只打折,不歸零——半年資料也看得到方向
    trig = (early_rate is not None and early_rate > 0.5) or (winner_early is not None and winner_early > 0.5) \
           or (avg_forgone is not None and avg_forgone > 0.08) or disp > 20
    return dict(dim="出場紀律", tier=1, triggered=trig, severity=min(max(sev,0),1),
                early_rate=early_rate, avg_forgone=avg_forgone, winner_early=winner_early,
                disp_gap=disp, hold_win=hw, hold_lose=hl, sell_win_rate=win_rate,
                n_rt=len(rts), n_scored=len(scored), n_trunc=n_trunc,
                n_winners=n_winners, low_conf=low_conf)

def dim_size(rows, held, last_px):
    # 用市值（有 yf）或成本算當前權重；entry size_pct 用累計淨投入代理
    cum, sizes = 0.0, []
    for r in rows:
        if r["side"] == "buy":
            cum += r["qty"] * r["price"]
            sizes.append((r["qty"] * r["price"]) / cum if cum else 0)
    vals = {}
    for t, (sh, cost) in held.items():
        px = (last_px or {}).get(t)
        vals[t] = sh * px if px else cost
    tot = sum(vals.values()) or 1
    weights = {t: v / tot for t, v in vals.items()}
    max_t = max(weights, key=weights.get) if weights else None
    max_pct = weights.get(max_t, 0)
    sev = min(max((max_pct - 0.20) / 0.30, 0), 1)
    return dict(dim="部位 sizing", tier=1, triggered=max_pct > 0.25,
                severity=sev, max_ticker=max_t, max_pct=max_pct,
                avg_pct=statistics.mean(weights.values()) if weights else 0, weights=weights)

def dim_diversify(held, last_px):
    vals = {}
    for t, (sh, cost) in held.items():
        px = (last_px or {}).get(t); vals[t] = sh * px if px else cost
    tot = sum(vals.values()) or 1
    w = {t: v / tot for t, v in vals.items()}
    sec = defaultdict(float); ai = 0.0
    for t, wt in w.items():
        s, is_ai = driver(t); sec[s] += wt; ai += wt * is_ai
    max_sec = max(sec, key=sec.get) if sec else None
    max_sec_pct = sec.get(max_sec, 0)
    top3 = sum(sorted(w.values(), reverse=True)[:3])
    sev = min(max((max(max_sec_pct, ai) - 0.40) / 0.40, 0), 1)
    trig = (len(w) >= 8 and max_sec_pct > SECTOR_MAX_TH) or top3 > 0.60 or ai > 0.60
    return dict(dim="分散", tier=2, triggered=trig, severity=sev, n=len(w),
                max_sector=max_sec, max_sector_pct=max_sec_pct, ai_pct=ai,
                top3=top3, sectors=dict(sec))

def dim_hold(rts):
    # B.4 修(2026-06-13)：改判「同一檔內的時間框架一致性」，不再用整組合 IQR。
    # 理由：長線+短線混合本來就跨度大（owner 中位 127 天卻被舊版誤判 sev 1.0）。
    # 真問題是「同一檔 ticker 又當沖又長抱」= 沒有一致框架；不同檔用不同框架是合理的兩套策略。
    hs = [r["hold"] for r in rts]
    if not hs:
        return dict(dim="持有時間", tier=2, triggered=False, severity=0)
    med = statistics.median(hs)
    q = sorted(hs); iqr = (q[int(.75*len(q))-1] - q[int(.25*len(q))]) if len(q) > 3 else 0
    overtrading = (5 - med) / 5 if med < 5 else 0          # 中位過短 = 疑似無 edge 的過度交易
    by_ticker = defaultdict(list)
    for r in rts:
        by_ticker[r["ticker"]].append(r["hold"])
    multi = {t: hl for t, hl in by_ticker.items() if len(hl) >= 3}   # 只看有 ≥3 次 round-trip 的檔
    incon = {t: hl for t, hl in multi.items() if any(h < 5 for h in hl) and any(h > 30 for h in hl)}
    incon_rate = len(incon) / len(multi) if multi else 0  # 同檔又當沖(<5d)又長抱(>30d)的比例
    sev = min(max(max(overtrading, incon_rate), 0), 1)
    return dict(dim="持有時間", tier=2, triggered=(med < 5 or incon_rate > 0.3),
                severity=sev, median_hold=med, iqr=iqr, min=min(hs), max=max(hs),
                incon_rate=incon_rate, n_incon=len(incon), n_multi=len(multi),
                incon_tickers=sorted(incon.keys()))

def dim_avgdown(avg_down, held, last_px, size_dim):
    cnt = len(avg_down)
    breach = 0
    for e in avg_down:
        w = size_dim["weights"].get(e["ticker"], 0)
        if w > 0.25: breach += 1
    # breach（攤平破 size 上限）才是危險訊號；原始攤平次數對常買 dip 的人不可靠 → 降權 + 封頂 0.8
    # （spec C-limit：無法從交易區分計畫性建倉 vs 恐慌攤平 → 此維以「問句」呈現，不當高信心判決）
    # B.5 修(2026-06-13)：觸發改為 breach 主導 —— 攤平到「破自己 size 上限」才是洞；
    # 純次數高（常買 dip 的 DCA）只當資訊，不入卡（owner 143 次/0 breach 被舊版誤排第三）。
    sev = min(0.5*breach + cnt/600, 0.8)
    tickers = sorted({e["ticker"] for e in avg_down})
    return dict(dim="加碼攤平", tier=1, triggered=(breach >= 1),
                severity=sev, count=cnt, breach=breach, tickers=tickers)

# ─────────────────────────── 6. 卡片選擇 + 渲染 ───────────────────────────
# ── 鏡片層(可換大師):洞的「規矩 + 引言」來自 lens 檔,engine 不 hardcode VY ──
_LENS = None
DEFAULT_LENS = os.path.join(os.path.dirname(__file__), "..", "rubric", "vincent-yu.lens.json")
def load_lens(path=DEFAULT_LENS):
    """載入鏡片檔(規矩/引言/找動機問句)。換大師 = 換這個檔,engine 不動。"""
    global _LENS
    import json
    try:
        with open(path, encoding="utf-8") as f: _LENS = json.load(f)
        return _LENS.get("master")
    except (OSError, ValueError): return None

CARD_LIB_FALLBACK = {
 "出場紀律": ("賣出前先寫一句『我賣的理由是 thesis 破了，還是手癢/想換現金?』",
              "在清醒時先把出場規則寫好，把判斷從『當下』移到『事前』。(VY)"),
 "部位 sizing": ("下單前先決定『這筆最多佔幾 %、為什麼是這個數而不是兩倍』。",
                 "門檻低只配小部位，門檻高才配得起大部位。(VY)"),
 "分散": ("加新倉前先問『它跟我最大那塊是不是同一個 driver?』是 → 不加。",
          "分散不是檔數多，是讓持有的標的來自不同的 underlying drivers。(VY)"),
 "持有時間": ("每筆進場先標『短線/波段/長線』，出場只准用同框架的理由。",
              "先想清楚你的時間軸，讓所有後續分析匹配它。(VY)"),
 "加碼攤平": ("往下加碼前必須寫出『一個進場時不知道的新證據』；寫不出 → 不加。",
              "不要出現『再加碼就能回本』就破線。(VY)"),
}
def card_for(dim):
    """(rule, quote):優先用 lens 檔(可換大師),載入失敗用 fallback。"""
    if _LENS and dim in _LENS.get("dims", {}):
        d = _LENS["dims"][dim]; m = _LENS.get("master", "VY")
        return d.get("rule", ""), f"{d.get('quote', '')}（{m}）"
    return CARD_LIB_FALLBACK.get(dim, ("", ""))

def dim_strength(exit_dim, size_dim, avgdown_dim, div_dim, hold_dim, rts=None):
    """負回饋循環解藥:卡片先給『你做對的最強一件事』再給洞。白話 + 附具體案例,不講黑話。
    (demand-side 研究:看虧損=ego受傷;先肯定 → 降低防衛 → 才聽得進那一刀。)"""
    c = []
    we = exit_dim.get("winner_early")
    if we is not None and we < 0.35 and not exit_dim.get("low_conf"):
        eg = ""
        if rts:   # 找一筆「賣了賺錢、賣完幾乎沒再漲」的具體案例佐證
            cand = [r for r in rts if r.get("ret", 0) > 0.10 and r.get("fwd") is not None and r["fwd"] < 0.03]
            if cand:
                b = max(cand, key=lambda r: r["ret"])
                eg = f"（例:{b['ticker']} 你賺 {b['ret']*100:.0f}% 出場,賣完它只動 {b['fwd']*100:+.0f}%——沒賣早）"
        c.append((0.7+(0.35-we), f"該獲利了結時你不手軟:賣掉的賺錢單只有 {we*100:.0f}% 事後繼續漲,代表你不會「抱著賺錢的捨不得賣、結果回吐」{eg}"))
    mp = size_dim.get("max_pct", 1)
    if mp < 0.22:
        c.append((1-mp, f"單筆部位有控制:押最重的一檔也只佔 {mp*100:.0f}%,沒把身家壓在一檔上"))
    if avgdown_dim.get("breach", 1) == 0 and avgdown_dim.get("count", 0) >= 2:
        c.append((0.65, f"就算往下加碼 {avgdown_dim['count']} 次,也沒有任何一筆失控加到爆倉(都守在部位上限內)"))
    if not div_dim.get("triggered") and div_dim.get("n", 0) >= 5:
        c.append((0.6, f"{div_dim['n']} 檔分布在不同 driver,沒有全押在同一個故事上"))
    if not hold_dim.get("triggered") and hold_dim.get("median_hold"):
        c.append((0.5, f"進出有一致的節奏:中位持有 {hold_dim['median_hold']:.0f} 天,不是隨機亂買亂賣"))
    if not c: return None
    c.sort(reverse=True); return c[0][1]

def best_worst(rts):
    """結果最好 / 最差的具體 round-trip,給卡片當案例(點:列出做得最好跟最不好的決策)。"""
    closed = [r for r in rts if r.get("ret") is not None]
    if not closed: return None, None
    return max(closed, key=lambda r: r["ret"]), min(closed, key=lambda r: r["ret"])

def overview_stats(rts, ab, held=None, last_px=None):
    """金額導向總覽:已實現(賣掉落袋)+ 未實現(還抱著)都要算,只報一個會失真。"""
    pnls = [r["qty"] * (r["sell_px"] - r["buy_px"]) for r in rts
            if r.get("sell_px") and r.get("buy_px")]
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p < 0]
    win_sum, loss_sum = sum(wins), sum(losses)      # loss_sum 為負
    avg_win = win_sum / len(wins) if wins else 0
    avg_loss = loss_sum / len(losses) if losses else 0
    payoff = avg_win / abs(avg_loss) if avg_loss else 0          # 平均賺 / 平均賠
    pf = win_sum / abs(loss_sum) if loss_sum else 0              # 賺總額 / 賠總額(獲利因子)
    realized = win_sum + loss_sum
    unrealized = sum(sh * last_px[t] - c for t, (sh, c) in (held or {}).items()
                     if last_px and t in last_px)                 # 還抱著的帳面盈虧
    return dict(n_rt=len(rts), realized=realized, unrealized=unrealized,
                total_pnl=realized + unrealized, win_sum=win_sum, loss_sum=loss_sum,
                n_wins=len(wins), n_losses=len(losses), avg_win=avg_win, avg_loss=avg_loss,
                payoff=payoff, pf=pf, ab=ab)

def what_if(held, last_px):
    """把『會一起倒』的空話換成可量化的 what-if:AI 暴險市值 × 回檔情境。"""
    if not last_px: return None
    ai = sum(sh * last_px[t] for t, (sh, c) in held.items() if driver(t)[1] == 1 and t in last_px)
    tot = sum(sh * last_px[t] for t, (sh, c) in held.items() if t in last_px)
    if tot <= 0: return None
    return dict(ai_mval=ai, ai_pct=ai / tot, drop30=ai * 0.30, drop50=ai * 0.50)

def time_trend(rts, avg_down):
    """時間維度:按年看關鍵行為指標,讓用戶看到自己『有沒有在進步』(復盤的留存核心)。"""
    from collections import defaultdict
    yr = defaultdict(lambda: {"pnl": 0.0, "w": 0, "l": 0, "we_w": 0, "we_n": 0, "ad": 0})
    for r in rts:
        y = r["exit"].year
        yr[y]["pnl"] += r["qty"] * (r["sell_px"] - r["buy_px"])
        yr[y]["w" if r["ret"] > 0 else "l"] += 1
        if r.get("ret", 0) > 0 and r.get("fwd") is not None:
            yr[y]["we_n"] += 1
            if r["fwd"] > SELL_EARLY_TH: yr[y]["we_w"] += 1
    for e in avg_down:
        yr[e["date"].year]["ad"] += 1
    return dict(sorted(yr.items()))

def prescribe(ab, dims, overview):
    """處方層:從歸因 + 診斷生成優化路徑——揚長 edge / 外包短板 / 砍損耗。每條盡量附可驗 metric。
    關鍵:處方是『放大你強的 + 外包你弱的』,不是通用避短。力量來自歸因精確(ChatGPT 沒你的數字不敢這樣說)。"""
    dd = {d["dim"]: d for d in dims}
    rx = []
    bs = (ab or {}).get("benchmarks", {})
    if "SPY" in bs and "QQQ" in bs:                          # 雙基準(中性 QQQ + 嚴苛 SOXX)判選股,避開 survivorship bias
        spy = bs["SPY"]
        pick_q = spy["port_tot"] - bs["QQQ"]["bench_tot"]    # 你 vs 中性科技
        pick_s = (spy["port_tot"] - bs["SOXX"]["bench_tot"]) if "SOXX" in bs else pick_q  # 你 vs 事後最強板塊
        if spy["excess"] > 0.10:                             # 贏大盤 → 揚長是「假設」不是「定論」
            rx.append(dict(kind="揚長(假設,待驗證)", verify="記錄『下一個賽道』判斷,事後對帳",
                           text=("你贏大盤主要靠『押對賽道/方向』(換哪個基準都成立)。但這只是『假設你有方向判斷力』,"
                                 "不是已證實的 edge——押對 AI 也可能只是站到風口。壓測它:寫下你『下一個看好的賽道』、記時間,"
                                 "看未來兩三次準不準,對了才叫 edge。")))
        if pick_q < -0.05 and pick_s < -0.05:               # 連中性基準都輸 → 選股確實弱
            rx.append(dict(kind="外包短板(漸進)", verify="被動部位佔比(升→好)",
                           text=("你選股連中性的 QQQ 都輸——優化不是『別選股』(你享受它、ETF 也會錯過妖股),"
                                 "是『撥一部分資金被動化托底』,選股當衛星。(流程建議,非標的建議)")))
        elif pick_q > 0.05 and pick_s > 0.05:               # 連嚴苛基準都贏 → 選股真 edge
            rx.append(dict(kind="揚長", text="你選股連最嚴苛的 SOXX 都贏,這是真 edge——別讓 sizing/紀律稀釋它。"))
        else:                                                # 一正一負 → 無定論,誠實不硬下處方
            rx.append(dict(kind="選股:目前無定論", text=(
                f"你的選股 alpha 對基準極敏感(vs 中性 QQQ {pick_q*100:+.0f}pp、vs 事後最強 SOXX {pick_s*100:+.0f}pp)——"
                f"換個比法結論就翻。誠實說:資料還判不出你『會不會選股』,別急著外包、也別自滿。"
                f"要定論,得拿『你當時的板塊配置混合』當對照(進階,還沒做)。")))
    ad = dd.get("加碼攤平", {})
    if ad.get("count", 0) >= 10 or ad.get("breach", 0) >= 1:
        rx.append(dict(kind="砍損耗", verify="虧損加碼次數(降→好)",
                       rule="虧損部位一律不加碼;真想加,先整筆賣掉隔天重買(逼你重新面對『現在還會買它嗎』)",
                       text=f"虧損中加碼 {ad.get('count', 0)} 次是你操盤損耗的大宗——這是最該先砍的純扣分動作。"))
    sz = dd.get("部位 sizing", {})
    if sz.get("max_pct", 0) > 0.30 and not any(r["kind"] == "砍損耗" for r in rx):
        rx.append(dict(kind="砍損耗", verify="單筆最大佔比(降→好)",
                       rule=f"單筆部位上限定死 20%,超過就減",
                       text=f"最大一筆 {sz.get('max_ticker')} 佔 {sz.get('max_pct', 0)*100:.0f}%,單一押注過重。"))
    return rx

def ticker_diagnosis(rts, avg_down, held, last_px, top_n=7):
    """標的層診斷(對事不對人):每檔的金額影響(已實現+未實現)+ 行為標籤,
    **按 |金額| 排序只取 top**——抓大放小,佔金額小的標的不糾結。"""
    agg = defaultdict(lambda: dict(realized=0.0, unreal=0.0, win_n=0, win_early=0,
                                   avgdown=0, cur_ret=None, mval=0.0, held=False))
    for r in rts:                                          # 已實現損益 + 賣太早
        a = agg[r["ticker"]]
        a["realized"] += r["qty"] * (r["sell_px"] - r["buy_px"])
        if r.get("ret", 0) > 0 and r.get("fwd") is not None:
            a["win_n"] += 1
            if r["fwd"] > SELL_EARLY_TH: a["win_early"] += 1
    tot_mval = sum(sh * last_px[t] for t, (sh, c) in held.items() if t in last_px) or 1.0
    for t, (sh, cost) in held.items():                    # 未實現 + 當前盈虧 + 權重
        px = last_px.get(t)
        if px:
            a = agg[t]; a["held"] = True
            a["unreal"] = sh * px - cost
            a["cur_ret"] = (px - cost / sh) / (cost / sh) if sh else 0
            a["mval"] = sh * px
    for e in avg_down:
        agg[e["ticker"]]["avgdown"] += 1
    out = []
    for t, a in agg.items():
        impact = a["realized"] + a["unreal"]
        if abs(impact) < 1: continue
        cur, wpct, tags = a["cur_ret"], a["mval"] / tot_mval, []
        if a["avgdown"] >= 1 and cur is not None and cur < -0.25:
            tags.append(f"✗凹單:虧 {cur*100:.0f}% 還往下加 {a['avgdown']} 次")
        elif cur is not None and cur < -0.40:
            tags.append(f"✗套牢:{cur*100:.0f}% 還抱著沒處理")
        if a["win_n"] >= 2 and a["win_early"] / a["win_n"] > 0.5:
            tags.append(f"✗賣太早:{a['win_early']}/{a['win_n']} 筆賣完續漲")
        if wpct > 0.25:
            tags.append(f"⚠押太重:佔組合 {wpct*100:.0f}%")
        if cur is not None and cur > 0.20:
            if a["avgdown"] <= 1:
                tags.append(f"✓紀律持有:賺 {cur*100:.0f}%、沒亂加")
            elif a["avgdown"] <= 4:
                tags.append(f"✓賺錢:賺 {cur*100:.0f}%(中途加 {a['avgdown']} 次,還算節制)")
            else:                                     # 大量虧損加碼 + 現賺 = 僥倖,不是紀律(避免 outcome bias)
                tags.append(f"⚠凹單僥倖:現賺 {cur*100:.0f}% 但虧損中加了 {a['avgdown']} 次——漲回來是運氣,跟套牢凹單同款行為")
        if not tags:
            tags.append("— 大致中性")
        out.append(dict(ticker=t, impact=impact, tags=tags))
    out.sort(key=lambda x: abs(x["impact"]), reverse=True)
    return out[:top_n]
def number_line(d):
    n = d["dim"]
    if n == "出場紀律":
        s = []
        if d["early_rate"] is not None:
            we = f"；賣掉賺錢的有 {d['winner_early']*100:.0f}% 續漲（賣太早）" if d.get("winner_early") is not None else ""
            s.append(f"{d['n_rt']} 筆決策賣出（{d['n_scored']} 有 fwd、{d['n_trunc']} 截斷）中 {d['early_rate']*100:.0f}% 在 {N_FWD} 天後更高、平均續漲 {d['avg_forgone']*100:+.1f}%{we}")
        s.append(f"賺錢抱 {d['hold_win']:.0f} 天 / 賠錢抱 {d['hold_lose']:.0f} 天（處置缺口 {d['disp_gap']:+.0f}）")
        return "；".join(s)
    if n == "部位 sizing":
        return f"你最大一筆 {d['max_ticker']} 佔 {d['max_pct']*100:.0f}%，其餘平均 {d['avg_pct']*100:.0f}%"
    if n == "分散":
        return f"你持有 {d['n']} 檔看似分散，但 AI capex 暴險 {d['ai_pct']*100:.0f}%、最大板塊「{d['max_sector']}」{d['max_sector_pct']*100:.0f}%、top3 {d['top3']*100:.0f}%——同一個 driver"
    if n == "持有時間":
        base = f"你持有時間 {d['min']}~{d['max']} 天、中位 {d['median_hold']:.0f} 天"
        if d.get("n_incon", 0) > 0:
            return base + f"；其中 {d['n_incon']}/{d['n_multi']} 檔同一檔又當沖又長抱（{', '.join(d['incon_tickers'][:5])}）——同檔沒有一致框架"
        return base + f"（中位 {d['median_hold']:.0f} 天 = 你的主框架；同檔框架大致一致）"
    if n == "加碼攤平":
        return f"你有 {d['count']} 次在虧損倉往下加碼（{', '.join(d['tickers'][:6])}），其中 {d['breach']} 次加到 >25%"
    return ""

def render(dims, strength=None, overview=None, best=None, worst=None, wi=None, trend=None, rx=None, tdiag=None):
    TW = {1: 1.0, 2: 0.7}
    trig = [d for d in dims if d["triggered"]]
    trig.sort(key=lambda d: d["severity"] * TW[d["tier"]], reverse=True)
    master = (_LENS or {}).get("master", "Vincent Yu")
    print("="*60)
    print(f"  trade-recap · 鏡片 {master}  (引擎產出)")
    print("="*60)
    if overview:                                   # 〔總覽〕已實現 + 未實現都報,看金額不看筆數
        o = overview; ab = o.get("ab") or {}
        print(f"\n〔總覽 · 金額〕帳面總損益 {o['total_pnl']:+,.0f}")
        print(f"  = 已實現(賣掉落袋){o['realized']:+,.0f}  +  未實現(還抱著){o['unrealized']:+,.0f}")
        print(f"  你『主動買賣』的盈虧比 {o['payoff']:.1f}（平均賺 ${o['avg_win']:,.0f} vs 平均賠 ${abs(o['avg_loss']):,.0f}）")
        if ab and not ab.get("note"):
            warn = "" if ab["n"] >= 252 else f"  ⚠️ 只 {ab['n']} 天,要 ≥1 年才準"
            print(f"  贏大盤 {ab['excess_vs_spy']*100:+.0f}pp｜β {ab['beta']:.2f}（漲跌是大盤 {ab['beta']:.1f} 倍）"
                  f"｜真本事 α 年化 {ab['alpha_ann']*100:+.0f}%{warn}")
        elif ab and ab.get("note"):
            print(f"  α/β:{ab['note']}")
    if best and worst:                             # 做得最好 / 最差的一筆具體交易
        print(f"\n〔做得最好 / 最差的一筆〕")
        print(f"  ✅ 最賺:{best['ticker']} {best['ret']*100:+.0f}%（{best['buy_px']:.0f}→{best['sell_px']:.0f},抱 {best['hold']} 天）")
        print(f"  ❌ 最虧:{worst['ticker']} {worst['ret']*100:+.0f}%（{worst['buy_px']:.0f}→{worst['sell_px']:.0f},抱 {worst['hold']} 天）")
    if wi:                                          # what-if:可量化的情境,不講「會一起倒」空話
        print(f"\n〔what if〕你 AI 暴險市值約 ${wi['ai_mval']:,.0f}（佔 {wi['ai_pct']*100:.0f}%）")
        print(f"  AI 回檔 30%(一般修正)→ 帳面 -${wi['drop30']:,.0f}；回檔 50%(2022級熊市)→ -${wi['drop50']:,.0f}。撐得住嗎?")
    if tdiag:                                       # 標的層診斷:按金額排序,對事不對人(小倉不糾結)
        print(f"\n〔標的層診斷 · 按金額排序,只看影響大的(小倉不糾結)〕")
        for d in tdiag:
            print(f"  {d['ticker']:<6}{d['impact']:>+11,.0f}   {' ｜ '.join(d['tags'])}")
    print("\n[5 維 severity（× tier 權重後排序）+ 原始數字]")
    for d in sorted(dims, key=lambda d: d["severity"]*TW[d["tier"]], reverse=True):
        flag = "🔴" if d["triggered"] else "⚪"
        print(f"  {flag} {d['dim']:<8} sev={d['severity']:.2f} ×tier{d['tier']} = {d['severity']*TW[d['tier']]:.2f}")
        print(f"      {number_line(d)}")
    print("\n" + "─"*60)
    if strength:                                   # 先肯定做對的一件事(降 ego 防衛),再給洞
        intro = (_LENS or {}).get("strength_intro", "先說你做對的一件事:")
        print(f"  ✅ {intro}")
        print(f"     {strength}\n")
    if trig:
        print("  復盤卡（top 1-2 最高代價的洞）：\n")
        for d in trig[:2]:
            rule, quote = card_for(d["dim"])
            print(f"  ▍最大漏洞 · {d['dim']}")
            print(f"    {number_line(d)}")
            print(f"    ▸ {quote}\n")
    else:
        print("  這幾個地基你目前都守住了。\n")
    if rx:                                          # 處方層:揚長 / 外包短板 / 砍損耗
        print("  〔怎麼優化 · 放大你強的 + 外包你弱的 + 砍掉純損耗〕")
        for r in rx:
            v = f"  〔下次驗:{r['verify']}〕" if r.get("verify") else ""
            print(f"    ▸ {r['kind']}:{r['text']}{v}")
        actionable = [r for r in rx if r.get("rule")]
        if actionable:
            print(f"\n  ★ 下次只改這一件(可立即執行 + 可驗):{actionable[0]['rule']}")

# ─────────────────────────── main ───────────────────────────
def main():
    paths = sys.argv[1:] or [DEFAULT_CSV]
    rows = load(paths)
    master = load_lens()                                  # 鏡片(預設 VY,可換大師檔)
    dm = os.environ.get("TR_DRIVER_MAP")                  # Claude 生成的 driver map(冷門股分類)
    n_dm = load_driver_map(dm) if dm else 0
    rts, open_lots = round_trips(rows)
    held, avg_down = positions(rows)
    tickers = {r["ticker"] for r in rts} | set(held.keys())
    start = (min((r["entry"] for r in rts), default=rows[0]["date"]) - dt.timedelta(days=10)).isoformat()
    px, yf_err = fetch_prices(tickers, start)
    fwds, last_px = fwd_from_px(rts, px, adaptive_n_fwd(rows))   # 觀察窗隨資料長度自適應
    print(f"# 載入 {len(rows)} 筆交易（{rows[0]['date']} ~ {rows[-1]['date']}），"
          f"{len(rts)} 個 round-trip，當前持倉 {len(held)} 檔。", end="")
    print(f" yfinance: {'OK' if not yf_err else yf_err}｜鏡片: {master or 'fallback'}"
          f"｜driver map: {n_dm} 檔" + (" (純 fallback,冷門股可能失準)" if not n_dm else ""))
    BROAD = {"大盤ETF", "商品", "債券", "區域ETF"}   # 再平衡/現金管理，非選股決策
    decision_rts = [r for r in rts if driver(r["ticker"])[0] not in BROAD]
    print(f"# 出場紀律只看「決策賣出」：{len(decision_rts)}/{len(rts)} round-trip"
          f"（排除 {len(rts)-len(decision_rts)} 筆大盤/債/商品 ETF 再平衡）")
    d_size = dim_size(rows, held, last_px)
    d_exit = dim_exit(decision_rts, fwds); d_div = dim_diversify(held, last_px)
    d_hold = dim_hold(rts); d_avgdown = dim_avgdown(avg_down, held, last_px, d_size)
    dims = [d_exit, d_size, d_div, d_hold, d_avgdown]
    strength = dim_strength(d_exit, d_size, d_avgdown, d_div, d_hold, decision_rts)  # 先給做對的(附案例)
    ab = dim_alpha_beta(rows, px)
    overview = overview_stats(decision_rts, ab, held, last_px)   # 已實現 + 未實現都報
    best, worst = best_worst(decision_rts)                 # 做得最好/最差的一筆
    wi = what_if(held, last_px)                            # 可量化的 what-if
    trend = time_trend(decision_rts, avg_down)             # (engine 保留,卡片暫不顯示)
    rx = prescribe(ab, dims, overview)                     # 處方層:揚長/外包/砍損耗
    tdiag = ticker_diagnosis(rts, avg_down, held, last_px) # 標的層:按金額排序,對事不對人
    render(dims, strength, overview, best, worst, wi, trend, rx, tdiag)
    print_alpha_beta(ab)

if __name__ == "__main__":
    main()
