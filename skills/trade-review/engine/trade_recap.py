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

# ── ticker → (sector, ai_capex?)  AI capex = 同一個底層 driver（VY B2 的「driver」代理）──
DRIVER = {
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
def driver(t): return DRIVER.get(t, ("其他", 0))

# ─────────────────────────── 1. 解析 ───────────────────────────
def load(paths):
    rows = []
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
        data = yf.download(sorted(set(tickers) | {"SPY"}), start=start,
                           progress=False, auto_adjust=True)["Close"]
    except Exception as e:
        return None, f"yfinance 下載失敗: {e}"
    if data is None or data.empty:
        return None, "yfinance 無資料"
    if data.ndim == 1:
        data = data.to_frame()
    return data, None

def fwd_from_px(rts, data):
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
        target = after.iloc[min(N_FWD-1, len(after)-1)]
        r["fwd"] = (float(target) - r["sell_px"]) / r["sell_px"]
        r["fwd_trunc"] = len(after) < N_FWD
        fwds.append(r["fwd"])
    return fwds, last_px

def dim_alpha_beta(rows, data, rf_annual=RF_ANNUAL):
    """Tier3 · E2：重建投組日報酬 vs SPY 回歸 → β、Jensen's Alpha。驗『你以為的 edge 是不是 beta』。"""
    try:
        import pandas as pd
    except ImportError:
        return dict(dim="alpha/beta", tier=3, triggered=False, severity=0, note="無 pandas")
    if data is None or "SPY" not in list(getattr(data, "columns", [])):
        return dict(dim="alpha/beta", tier=3, triggered=False, severity=0, note="無價格/SPY")
    from collections import defaultdict
    px = data.ffill()
    days = list(px.index)
    ev = defaultdict(list)
    for r in rows:
        ev[pd.Timestamp(r["date"])].append(r)
    shares = defaultdict(float); prev = {}; port_rets = {}
    for i, day in enumerate(days):
        if i > 0 and prev:                       # 用「昨日持股 × 今日價」算日報酬，排除當日買賣的現金流
            num = den = 0.0
            for t, sh in prev.items():
                if sh == 0 or t not in px.columns: continue
                p1 = px[t].iloc[i]; p0 = px[t].iloc[i-1]
                if pd.isna(p1) or pd.isna(p0): continue
                num += sh * p1; den += sh * p0
            if den > 0:
                port_rets[day] = num / den - 1
        for r in ev.get(day, []):
            shares[r["ticker"]] += r["qty"] if r["side"] == "buy" else -r["qty"]
        prev = dict(shares)
    port = pd.Series(port_rets)
    spy = px["SPY"].pct_change()
    df = pd.DataFrame({"p": port, "s": spy}).dropna()
    df = df[df["p"].abs() < 0.5]                  # 去離群（split/資料錯）
    if len(df) < 60:
        return dict(dim="alpha/beta", tier=3, triggered=False, severity=0, note=f"樣本不足 {len(df)} 天")
    rf_d = rf_annual / 252.0                                       # 無風險利率(日)
    beta = df["p"].cov(df["s"]) / df["s"].var()
    # Jensen's Alpha（通用標準）= 你的報酬 − [無風險 + β×(大盤 − 無風險)]
    alpha_daily = df["p"].mean() - (rf_d + beta * (df["s"].mean() - rf_d))
    alpha_ann = alpha_daily * 252.0
    port_tot = (1 + df["p"]).prod() - 1
    spy_tot = (1 + df["s"]).prod() - 1
    excess_vs_spy = port_tot - spy_tot                            # 入門版：純比大盤(不調風險)
    rf_period = (1 + rf_d) ** len(df) - 1
    jensen_period = port_tot - (rf_period + beta * (spy_tot - rf_period))   # 期間 Jensen α
    beta_frac = 1 - max(jensen_period, 0) / port_tot if port_tot > 0 else 1.0
    return dict(dim="alpha/beta", tier=3, triggered=(beta_frac > 0.6 and port_tot > 0),
                severity=min(max(beta_frac, 0), 1), beta=beta, alpha_ann=alpha_ann,
                port_tot=port_tot, spy_tot=spy_tot, jensen_period=jensen_period,
                excess_vs_spy=excess_vs_spy, beta_frac=beta_frac, rf_annual=rf_annual, n=len(df))

def print_alpha_beta(d):
    print("\n" + "─"*60)
    print("  真本事檢驗 · alpha / beta（把運氣和大盤扣掉，還剩多少是你的）")
    if d.get("note"):
        print(f"    （{d['note']}）"); return
    print(f"    過去 {d['n']} 個交易日：你的投組 {d['port_tot']*100:+.0f}%、大盤SPY {d['spy_tot']*100:+.0f}%")
    print(f"    ① 有沒有贏大盤   超額報酬 {d['excess_vs_spy']*100:+.0f} 個百分點")
    print(f"    ② 冒了多少險     你的漲跌是大盤的 {d['beta']:.2f} 倍（像 {d['beta']:.1f} 倍槓桿的雲霄飛車）")
    print(f"    ③ 真本事         Jensen's Alpha 年化 {d['alpha_ann']*100:+.1f}%"
          f"（扣掉無風險利率 {d['rf_annual']*100:.1f}% 和風險後，仍多賺的；正=有料）")
    print(f"    ▸ 一句話：你贏大盤 {d['excess_vs_spy']*100:+.0f}pp，但扣掉『膽子大』後，真本事約 {d['alpha_ann']*100:+.0f}%/年")
    print(f"    ▸ 下次只改：每季只認 alpha，別把『大盤＋槓桿』給你的，當成自己的能力。")

# ─────────────────────────── 5. 五維 metrics ───────────────────────────
def dim_exit(rts, fwds):
    # rts 已在 main 預先濾成「決策賣出」（排除大盤/債/商品 ETF 再平衡）
    early_rate = avg_forgone = winner_early = None
    scored = [r for r in rts if "fwd" in r]
    n_trunc = sum(1 for r in scored if r.get("fwd_trunc"))
    if scored:
        early_rate = sum(1 for r in scored if r["fwd"] > SELL_EARLY_TH) / len(scored)
        avg_forgone = statistics.mean(r["fwd"] for r in scored)
        winners = [r for r in scored if r["ret"] > 0]                 # 賣掉賺錢的
        if winners:
            winner_early = sum(1 for r in winners if r["fwd"] > SELL_EARLY_TH) / len(winners)
    win_holds = [r["hold"] for r in rts if r["ret"] > 0]
    lose_holds = [r["hold"] for r in rts if r["ret"] < 0]
    hw = statistics.median(win_holds) if win_holds else 0
    hl = statistics.median(lose_holds) if lose_holds else 0
    disp = hl - hw
    win_rate = sum(1 for r in rts if r["ret"] > 0) / len(rts) if rts else 0
    sev = max((early_rate or 0), (winner_early or 0), (avg_forgone or 0)/0.2, disp/60)
    trig = (early_rate is not None and early_rate > 0.5) or (winner_early is not None and winner_early > 0.5) \
           or (avg_forgone is not None and avg_forgone > 0.08) or disp > 20
    return dict(dim="出場紀律", tier=1, triggered=trig, severity=min(max(sev,0),1),
                early_rate=early_rate, avg_forgone=avg_forgone, winner_early=winner_early,
                disp_gap=disp, hold_win=hw, hold_lose=hl, sell_win_rate=win_rate,
                n_rt=len(rts), n_scored=len(scored), n_trunc=n_trunc)

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
CARD_LIB = {
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

def render(dims):
    TW = {1: 1.0, 2: 0.7}
    trig = [d for d in dims if d["triggered"]]
    trig.sort(key=lambda d: d["severity"] * TW[d["tier"]], reverse=True)
    print("="*60)
    print("  trade-recap · 鏡片 Vincent Yu  (引擎產出，非人肉)")
    print("="*60)
    print("\n[5 維 severity（× tier 權重後排序）+ 原始數字]")
    for d in sorted(dims, key=lambda d: d["severity"]*TW[d["tier"]], reverse=True):
        flag = "🔴" if d["triggered"] else "⚪"
        print(f"  {flag} {d['dim']:<8} sev={d['severity']:.2f} ×tier{d['tier']} = {d['severity']*TW[d['tier']]:.2f}")
        print(f"      {number_line(d)}")
    print("\n" + "─"*60)
    if not trig:
        print("  這 5 個地基你目前都守住了。進階維度需要你補一句下單理由。")
        return
    print("  復盤卡（top 1-2 最高代價的洞）：\n")
    for d in trig[:2]:
        rule, quote = CARD_LIB[d["dim"]]
        print(f"  ▍最大漏洞 · {d['dim']}")
        print(f"    {number_line(d)}")
        print(f"    ▸ 下次只改這一件：{rule}")
        print(f"    ▸ {quote}\n")

# ─────────────────────────── main ───────────────────────────
def main():
    paths = sys.argv[1:] or [DEFAULT_CSV]
    rows = load(paths)
    rts, open_lots = round_trips(rows)
    held, avg_down = positions(rows)
    tickers = {r["ticker"] for r in rts} | set(held.keys())
    start = (min((r["entry"] for r in rts), default=rows[0]["date"]) - dt.timedelta(days=10)).isoformat()
    px, yf_err = fetch_prices(tickers, start)
    fwds, last_px = fwd_from_px(rts, px)
    print(f"# 載入 {len(rows)} 筆交易（{rows[0]['date']} ~ {rows[-1]['date']}），"
          f"{len(rts)} 個 round-trip，當前持倉 {len(held)} 檔。", end="")
    print(f" yfinance: {'OK' if not yf_err else yf_err}")
    BROAD = {"大盤ETF", "商品", "債券", "區域ETF"}   # 再平衡/現金管理，非選股決策
    decision_rts = [r for r in rts if driver(r["ticker"])[0] not in BROAD]
    print(f"# 出場紀律只看「決策賣出」：{len(decision_rts)}/{len(rts)} round-trip"
          f"（排除 {len(rts)-len(decision_rts)} 筆大盤/債/商品 ETF 再平衡）")
    d_size = dim_size(rows, held, last_px)
    dims = [dim_exit(decision_rts, fwds), d_size, dim_diversify(held, last_px),
            dim_hold(rts), dim_avgdown(avg_down, held, last_px, d_size)]
    render(dims)
    print_alpha_beta(dim_alpha_beta(rows, px))

if __name__ == "__main__":
    main()
