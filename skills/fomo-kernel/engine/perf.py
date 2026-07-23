#!/usr/bin/env python3
"""perf.py — 帳戶級績效(#171 B 路線):daily 鏈式 TWR + cash drag + 帳戶 IRR(XIRR)。

規格拍板(2026-07-12,沉澱在 #171 issue comment,勿在此偏離):
- **daily 鏈式,不做 Modified Dietz**:每日 V_t = 持倉市值 + 現金,混合慣例剝離金流——
  流入走 BOD(進分母:入金當天到位、當天不計超額,防「零起點除爆」)、流出走 EOD
  (沖回分子:清倉/提領當天的執行報酬仍入鏈,防「清倉日分母歸零」):
  r_t = (V_t − F_out_t) / (V_{t-1} + F_in_t) − 1。
  MD 是「拿不到期中估值」世界的近似;這裡日價 + 日精度金流都有。
- **帳戶級不配新大盤基準**:增量資訊是 cash drag = 帳戶TWR − 持倉TWR;
  「該不該乾脆買指數」續由 per-market α 面板回答(US→SPY、TW→^TWII,不合成)。
- **外部金流 = deposit / withdrawal / other**(對帳口徑;other=ACH/Transfer 有 Amount 就是
  真金流,漏計會讓報酬虛高——與 recent_net_deposit 排除 other 的行為判讀口徑刻意分開)。
- **可用性繼承 cash reliable 三態**:csv_sum → 帳戶柱不出(只出持倉柱);partial → 出但
  basis 記缺錨點幣別(honesty 揭露);anchored → 全出。歷史現金餘額 = 「現在餘額 − 其後
  金流」回滾,CSV 漏記入金會讓整段餘額平移、污染每天的分子分母,gate 就是為此。
  partial 之下,盲算幣別的回滾若跌到負值 = 假設已破裂,帳戶柱同樣降 None。

會計基(復權價,實作註腳非規格):fetch_prices 用 auto_adjust=True,股息已折入價內
(H 桶份額 × 復權價 = 含息總回報)。股息現金又真實進了現金桶 → 同一筆息會被 V 計兩次,
故 dividend 列為**補償性外部流**沖銷(F 含 dividend)。持倉桶金流 F_H **由 rows 的
qty×price 生成(價格口徑)**,不依賴 CSV Amount 欄——手續費(trade 淨額與價格口徑的差)
由現金桶吸收,歸帳戶層成本,不進 hold 柱。interest / fee 留在現金桶內不沖銷(利息是
現金桶的真實收益、費用是真實成本,都該進帳戶報酬)。IRR 端點只用 V_0 / V_T + 存提流。

抓不到價的檔(下市/冷門/yfinance 限流)**以交易成本平線入 H 桶**(`at_cost_tickers`,
零報酬、不進報酬歸因,honesty 揭露)——不能整檔剔除:金流照真(F_H 有它、現金桶扣了錢)
而資產憑空消失,會把該檔翻成假 −100%、V 也被打凹(限流缺一檔就毀整條序列,實測過)。
平線 = 買入日 H 增量恰等於 F_H 流入,對 r 恆等中性。判定是整檔的(px 全缺、或首個有效價
晚於該檔首筆交易),不做期中切換——切換日的市價/成本跳點沒有金流對應,會污染 r。

**帳戶柱多一道資料完整性 gate**:現金史回滾需要「每筆交易都有現金足跡」——買賣不動
現金桶時,加倉日會被鏈式誤算成暴漲(V 跳而 F=0)。來源完全沒有 Amount 欄時,
load_cash_flows 用 qty×price 估滿足跡(#375;與 F_H 同一個價格口徑,差別只在手續費/稅
沒被扣掉,basis.estimated_footprint 揭露),所以這道 gate 剩下的職責是擋「部分有、部分
沒有」的混合來源:kind=trade 的金流筆數 < rows 交易筆數 → 帳戶級 TWR/IRR 誠實不出
(hold 柱不受影響,它只吃 rows + 價格)。

**擋卡原因是結構化的**(#375):算不出時 `out["gate"] = {"status", "data"}`、算得出但有
限制時 `out["notes"] = [{"status", "data"}, ...]`,兩者分家。perf.py 不寫使用者看得到的
句子——語言由 copy catalog 決定,新增一種原因不必回頭補文案(舊版寫死繁中句子,渲染層
分不出原因,不管實際是什麼都印「等現金錨點補齊」)。

持倉柱只在有持倉的日子上鏈(同一混合慣例,分母 = H_{t-1} + 當日買入);帳戶柱全窗上鏈——
空倉期 = 100% 現金照走,踏空/躲跌自動涵蓋(#164 縫 A 在帳戶級閉合)。

狀態側零依賴:標準庫 only(px/fx_series 是呼叫端傳入的 pandas 物件,只用其方法,不 import pandas)。
"""
import datetime as dt
from bisect import bisect_left
from collections import defaultdict

EPS = 1e-6
MIN_IRR_DAYS = 90          # 同 #164:年化窗 <90 天標「太短,年化無意義」不出數
_EXT_KINDS = ("deposit", "withdrawal", "other")   # 對帳口徑的外部金流(拍板四)
RESIDUAL_TAINT_TH = 0.10   # #180:殘差(換 USD)佔帳戶規模超此比例 → 大缺口,帳戶柱算不出、出解鎖邀請
# #375:回滾隱含的「期初閒置現金」佔期初帳戶值超此比例 → 「存提款不可見」的前提才值得揭露。
# 這個比例就是失真的代理量:沒有存提款紀錄時,後來才存進來的錢會被回滾成期初就躺在帳上的
# 現金,帳戶 TWR 被系統性低估(合成案例:share 0.5 → 差 33pp)。share≈0 = 回滾其實是對的
# (錢在期初就全部投進去了),此時揭露只是空吠,不觸發。
IMPLIED_START_CASH_TH = 0.05


def _reason(status, **data):
    """一條「機器可讀的原因」= {status, data},對齊 honesty ledger 的 {key, status, data}。

    #375:這裡**永遠不寫使用者看得到的句子**。舊版把繁中句子寫死在 perf.py,渲染層只能
    靠 truthiness 判斷「有沒有被擋」,於是不管真正原因是什麼都印同一句「等現金錨點補齊」——
    使用者已經補了錨點、真正原因是缺 Amount 欄時,卡片給的下一步是錯的。原因由引擎算、
    語言由 copy catalog 決定,新增一種原因不必回頭補句子(也順帶讓這些原因有了英文版)。"""
    return {"status": status, "data": data}


# ───────────────────────── XIRR solver(#164 / #171 共用) ─────────────────────────
def xirr(cashflows, lo=-0.999, hi=10.0, grid=240):
    """年化金額加權報酬。cashflows = [(date, cf)] 投資人視角(投入負 / 取回正,期末市值
    當一筆正流)。NPV(r) = Σ cf·(1+r)^(−Δyr);在 (lo, hi] 網格掃描符號變化,恰一個變號
    區間才二分求根;0 或 ≥2 個(非常規金流多解)→ None 誠實跳過(#164 規格:掃不到唯一根不猜)。"""
    flows = [(d, float(a)) for d, a in cashflows if abs(float(a)) > 1e-12]
    if len(flows) < 2:
        return None
    if not (any(a > 0 for _, a in flows) and any(a < 0 for _, a in flows)):
        return None                                   # 全同號 → 無根
    d0 = min(d for d, _ in flows)
    yrs = [((d - d0).days / 365.0, a) for d, a in flows]
    scale = sum(abs(a) for _, a in yrs)

    def npv(r):
        return sum(a * (1.0 + r) ** (-t) for t, a in yrs)

    pts, vals = [], []
    for i in range(grid + 1):
        r = lo + (hi - lo) * (i / grid) ** 2          # 低利率區較密
        try:
            v = npv(r)
        except (OverflowError, ZeroDivisionError):
            continue
        pts.append(r); vals.append(v)
    brackets = [(pts[i - 1], pts[i]) for i in range(1, len(pts))
                if vals[i] == 0 or (vals[i] > 0) != (vals[i - 1] > 0)]
    if len(brackets) != 1:
        return None
    a, b = brackets[0]
    va = npv(a)
    for _ in range(200):
        m = (a + b) / 2.0
        vm = npv(m)
        if abs(vm) < 1e-9 * scale or (b - a) < 1e-10:
            return m
        if (vm > 0) == (va > 0):
            a, va = m, vm
        else:
            b = m
    return (a + b) / 2.0


# ───────────────────── 多錨點對帳殘差(#180) ─────────────────────
def cash_reconcile_residuals(snapshots, cash_flows, fx=None, abs_floor=50.0, pct=0.01):
    """多錨點對帳殘差(#180):ledger 累積的現金 snapshot 相鄰兩兩對帳,量化漏記金流。

    snapshots = [{"as_of": date|str, "cash": {ccy: balance}}]——從 ledger snapshot events
    抽(含 cash 欄者);cash_flows = load_cash_flows 輸出 [{date, amount, kind, currency}]。
    回 [{currency, start, end, prev_balance, next_balance, flows_sum, residual}](只收超閾值),
    依 (currency, start) 穩定排序。<2 錨點 → []（單錨點無殘差可算,不吠)。

    四條語意契約(#180 owner + adversarial review 拍板,改動別違反):
    1. 只相鄰配對(A→B、B→C,絕不 A→C):全對錨點會把同一筆漏記在多段重複歸因,殘差不可
       加、用戶看到假的多筆。
    2. 閾值分母用 Σ|flow|(絕對值和,非淨流):淨流會被「大額入金+提款」自我抵銷
       (+1e4−9.9e3 淨$100 → 1% 閾值變$1 → rounding 爆假殘差);Σ|flow| 反映該段現金活動量級。
    3. 缺席幣別≠0:snapshot A 只宣告 {USD}、B 宣告 {USD,TWD} → TWD 在 A 缺席 ≠ 餘額 0;
       per-currency explode,各幣別只在「都宣告了該幣別的相鄰錨點對」之間算殘差。
    4. 同 as_of 多筆 tie-break 對齊 ledger.latest_anchor(檔案序較後=較新宣告):以 dict 覆蓋
       實現(snapshots 按檔案序、後寫覆蓋前寫),不與帳本推導的錨點語意分岔。

    殘差 = bal_next − (bal_prev + Σ_{prev < date ≤ next} flow)。**中性數字**:零金流卻餘額變動
    可能是漏記利息/費用/入金任一種 → residual 只是量,下游文案禁斷言「漏入金」。
    abs_floor 是 aggregate(USD)語意,per-currency 用 fx({ccy: usd_per_unit})換算成原幣門檻
    (缺 fx → 原值近似);pct×Σ|flow| 是相對門檻,取兩者大者(小額利息尾差不吠)。
    純標準庫、確定性(對齊檔頭「狀態側零依賴」)。
    """
    snaps = snapshots or []
    flows = cash_flows or []
    if len(snaps) < 2:
        return []
    fx = fx or {}

    # per-currency explode:{ccy: {as_of: balance}}。dict 後寫覆蓋前寫 = 同 as_of 取檔案序較後(契約4)。
    per_ccy = defaultdict(dict)
    for snap in snaps:
        as_of = snap.get("as_of")
        if isinstance(as_of, str):
            as_of = dt.date.fromisoformat(as_of)
        for ccy, bal in (snap.get("cash") or {}).items():
            if bal is None:
                continue
            per_ccy[ccy][as_of] = float(bal)

    out = []
    for ccy, amap in per_ccy.items():
        dates = sorted(amap)                      # as_of 升序;缺席該幣別的 snapshot 自然不在(契約3)
        if len(dates) < 2:
            continue                              # 該幣別 <2 錨點,無相鄰對可算
        ccy_flows = [cf for cf in flows if cf.get("currency", "USD") == ccy]
        floor = abs_floor / fx[ccy] if fx.get(ccy) else abs_floor   # $50 USD → 該幣別等值門檻
        for i in range(1, len(dates)):            # 只相鄰(契約1)
            t0, t1 = dates[i - 1], dates[i]
            seg = [cf["amount"] for cf in ccy_flows if t0 < cf["date"] <= t1]
            flows_sum = sum(seg)
            gross = sum(abs(a) for a in seg)      # Σ|flow|(契約2)
            residual = amap[t1] - (amap[t0] + flows_sum)
            if abs(residual) > max(floor, pct * gross):
                out.append({"currency": ccy,
                            "start": t0.isoformat(), "end": t1.isoformat(),
                            "prev_balance": round(amap[t0], 2),
                            "next_balance": round(amap[t1], 2),
                            "flows_sum": round(flows_sum, 2),
                            "residual": round(residual, 2)})
    out.sort(key=lambda r: (r["currency"], r["start"]))
    return out


# ───────────────────────── 帳戶級 V_t 序列 + 三數字 ─────────────────────────
def _fx_getter(currencies, px_index, fx_series, fx_spot):
    """回 (fx_at(ccy, i), fx_approx):每交易日的 usd_per_unit。優先 fx_series(每日,含匯損益,
    ffill/bfill 補洞);缺 → 即期常數近似(fx_approx=True);連即期都缺 → 1.0(data_integrity
    的 fx_gaps 已在上游揭露)。USD 恆 1。"""
    fx_approx = False
    cols = {}
    for c in currencies:
        if c == "USD":
            continue
        ser = None
        if fx_series is not None and c in getattr(fx_series, "columns", ()):
            # 假日/日曆錯位的 ffill(+頭部 bfill)是常規估值,不算近似——只有整條缺、退回即期常數才標。
            s = fx_series[c].reindex(px_index).ffill().bfill()
            if not bool(s.isna().any()):
                ser = [float(v) for v in s]
        if ser is None:
            rate = (fx_spot or {}).get(c)
            if rate:
                fx_approx = True                      # 全窗用今日即期 = 匯損益歸零的近似
                ser = [float(rate)] * len(px_index)
            else:
                ser = [1.0] * len(px_index)           # 缺匯率:原幣近似(上游 fx_gaps 揭露)
        cols[c] = ser

    def fx_at(c, i):
        return 1.0 if c == "USD" else cols[c][i]
    return fx_at, fx_approx


def account_perf(rows, px, cash_flows, cash_data, cur_map,
                 fx_spot=None, fx_series=None, cash_residuals=None):
    """帳戶級績效三數字(#171):acct_twr / cash_drag / irr_annual(+持倉柱 hold_twr 當 drag
    參照)。輸出 dict 固定鍵(值可 None),算不了 → {"note": ...} fail-closed(同 pnl_curve)。
    rows/cash_flows/cash_data/cur_map = 引擎 load / load_cash_flows / cash_position /
    currency_map 的原樣輸出;px / fx_series = fetch_prices / fetch_fx_series 的 DataFrame。"""
    if px is None or getattr(px, "empty", True) or not rows:
        return {"gate": _reason("no_prices")}
    if len(px.index) < 2:
        return {"gate": _reason("short_price_series", days=len(px.index))}

    days = list(px.index)
    day_dates = [d.date() for d in days]
    n = len(days)
    px_ff = px.ffill()
    cash_flows = cash_flows or []
    by_ccy = (cash_data or {}).get("by_currency") or {}
    cash_ccys = sorted(set(by_ccy) | {cf.get("currency", "USD") for cf in cash_flows})
    all_ccys = sorted(set(cash_ccys) | {cur_map.get(r["ticker"], "USD") for r in rows} | {"USD"})
    fx_at, fx_approx = _fx_getter(all_ccys, px.index, fx_series, fx_spot)

    def day_idx(d):
        """flow/trade 日 → 掛到 ≥d 的第一個交易日(BOD:週末入金下個開盤到位);窗尾外 → None。"""
        i = bisect_left(day_dates, d)
        return i if i < n else None

    trades_at = defaultdict(list)
    for r in rows:
        i = day_idx(r["date"])
        if i is not None:
            trades_at[i].append(r)
    flows_at = defaultdict(list)
    for cf in cash_flows:
        i = day_idx(cf["date"])
        if i is not None:
            flows_at[i].append(cf)

    # 現金史:per-currency 從「現在餘額」回滾 —— C_c(t) = bal_now − Σ_{金流日 > t} amount。
    # bal_now 來自 cash_position(錨點語意的單一事實源,不在此重算錨點配對)。
    cash_ser = {}
    broken_ccys = []
    for c in cash_ccys:
        bal_now = float((by_ccy.get(c) or {}).get("balance") or 0.0)
        fl = sorted((cf for cf in cash_flows if cf.get("currency", "USD") == c),
                    key=lambda x: x["date"])
        ser = [0.0] * n
        j, acc = len(fl) - 1, 0.0
        for i in range(n - 1, -1, -1):
            while j >= 0 and fl[j]["date"] > day_dates[i]:
                acc += fl[j]["amount"]
                j -= 1
            ser[i] = bal_now - acc
        cash_ser[c] = ser
        if not (by_ccy.get(c) or {}).get("reliable") and min(ser) < -0.01:
            broken_ccys.append(c)                     # 盲算桶回滾出負現金 = 假設破裂

    # 無價檔判定(整檔):px 全缺、或首個有效價晚於該檔首筆交易 → 以成本平線入桶(見檔頭)
    first_trade = {}
    for r in rows:
        t = r["ticker"]
        if t not in first_trade or r["date"] < first_trade[t]:
            first_trade[t] = r["date"]
    at_cost = set()
    for t in first_trade:
        if t not in px_ff.columns:
            at_cost.add(t)
            continue
        fv = px_ff[t].first_valid_index()
        if fv is None or fv.date() > first_trade[t]:
            at_cost.add(t)

    # 每日 H(持倉市值,復權價含息)/ C(現金)/ 金流按符號拆流入(BOD 進分母)/流出(EOD 沖分子)
    H = [0.0] * n
    C = [0.0] * n
    F_in = [0.0] * n                                  # 帳戶外部流入(deposit/other 正向+股息沖銷)
    F_out = [0.0] * n                                  # 帳戶外部流出(withdrawal/other 負向)
    FH_in = [0.0] * n                                 # 持倉桶流入(買入)
    FH_out = [0.0] * n                                # 持倉桶流出(賣出)
    shares = defaultdict(float)
    cost_book = defaultdict(float)                    # at-cost 檔的成本簿(USD,平線貢獻)
    for i in range(n):
        for r in trades_at.get(i, ()):                # EOD 持股:當天交易計入當天收盤估值
            t = r["ticker"]
            amt = r["qty"] * r["price"] * fx_at(cur_map.get(t, "USD"), i)
            if r["side"] == "buy":
                shares[t] += r["qty"]
                FH_in[i] += amt                       # 買入 = 錢進持倉桶(價格口徑,不依賴 Amount)
                if t in at_cost:
                    cost_book[t] += amt
            else:
                shares[t] -= r["qty"]
                FH_out[i] += -amt                     # 賣出 = 錢出桶(EOD 沖回分子)
                if t in at_cost:
                    cost_book[t] = max(0.0, cost_book[t] - amt)  # 粗減(賣價計),clamp 不翻負
        hv = sum(cost_book.values())                  # 平線:買入日增量恰等於 FH_in,對 r 中性
        for t, sh in shares.items():
            if sh <= 1e-9 or t in at_cost:
                continue
            p = float(px_ff[t].iloc[i])
            if p != p:                                # 理論殘餘 NaN(有價檔首價前不該持股)→ 當日跳過
                continue
            hv += sh * p * fx_at(cur_map.get(t, "USD"), i)
        H[i] = hv
        C[i] = sum(cash_ser[c][i] * fx_at(c, i) for c in cash_ccys)
        for cf in flows_at.get(i, ()):
            if cf["kind"] not in _EXT_KINDS and cf["kind"] != "dividend":
                continue                              # trade 由 rows 生成 F_H;interest/fee 留現金桶
            # dividend:復權價已含息 → 股息現金列補償性外部流沖雙計(見檔頭)
            amt = cf["amount"] * fx_at(cf.get("currency", "USD"), i)
            if amt >= 0:
                F_in[i] += amt
            else:
                F_out[i] += amt
    V = [H[i] + C[i] for i in range(n)]

    # 鏈式:帳戶柱全窗;持倉柱只在有持倉的日子。混合慣例 r = (V_t − F_out)/(V_{t-1} + F_in) − 1。
    # 因子 ≤0(淨值/桶值穿越非正:深 margin、資料洞)→ 該日不入鏈 + 計數——TWR 無法穿越破產點,
    # 硬乘會讓整條鏈永久歸零或翻號(×0 之後全毀,實測過限流缺價時的假 −100%)。
    acct_f, n_acct = 1.0, 0
    hold_f, n_hold = 1.0, 0
    skipped = 0
    for i in range(1, n):
        base_a = V[i - 1] + F_in[i]
        fac_a = (V[i] - F_out[i]) / base_a if base_a > EPS else None
        if fac_a is not None and fac_a > 0:
            acct_f *= fac_a
            n_acct += 1
        elif abs(V[i]) > EPS or abs(V[i - 1]) > EPS or abs(F_in[i]) + abs(F_out[i]) > EPS:
            skipped += 1                              # 分母非正/因子非正但帳上有量 → 該日估不出,誠實計數
        base_h = H[i - 1] + FH_in[i]
        if base_h > EPS:
            fac_h = (H[i] - FH_out[i]) / base_h
            if fac_h > 0:
                hold_f *= fac_h
                n_hold += 1
            else:
                skipped += 1
    hold_twr = hold_f - 1.0 if n_hold else None

    w_days = [i for i in range(n) if V[i] > EPS]
    avg_cash_w = (sum(C[i] / V[i] for i in w_days) / len(w_days)) if w_days else None
    avg_cash_usd = (sum(C[i] for i in w_days) / len(w_days)) if w_days else 0.0

    window = {"start": day_dates[0].isoformat(), "end": day_dates[-1].isoformat(),
              "days": (day_dates[-1] - day_dates[0]).days, "traded_days": n}
    # #375 L3:估算足跡與「存提款不可見」的量化揭露來源。條件算在引擎(CLAUDE.md
    # 「Honesty decisions belong in code」),文案在 renderer/copy。
    #   estimated_footprint:trade 現金足跡是 qty×price 估的(來源沒有 Amount 欄),
    #     手續費/稅/匯損被排除在現金桶外 → 誤差隨週轉率放大(美股零佣金低週轉是雜訊,
    #     台股賣出證交稅 0.3% + 高週轉則不是)。
    #   external_flows_absent:整份流水沒有任何存/提款列、窗夠長,且回滾隱含的期初閒置
    #     現金佔比夠大 → 回滾隱含「這段期間現金變化 100% 只來自交易」。實測:真的存過錢
    #     時,回滾會把後來才存進來的錢當成期初就有的現金,帳戶 TWR 被系統性低估(合成案例
    #     差 33pp),而舊版對此完全零揭露。implied_start_cash_share 給出這個前提有多重
    #     ——它同時是觸發條件與失真代理量,所以不對「錢在期初就全投進去」的帳戶空吠。
    # 口徑用 _EXT_KINDS(對帳口徑,含 other=ACH/Transfer)而不是 deposit/withdrawal:
    # 這裡問的是「外部金錢進出看不看得見」,而鏈式 TWR 本來就把 other 當真外部流處理
    # (見檔頭拍板四)。用窄口徑會對「其實有一筆 ACH 轉帳在檔裡」的來源誤判成不可見。
    external_flow_rows = sum(1 for cf in cash_flows if cf["kind"] in _EXT_KINDS)
    start_share = (C[0] / V[0]) if V[0] > EPS else None
    basis = {"cash_source": (cash_data or {}).get("source"),
             "unanchored": sorted(c for c, v in by_ccy.items() if not v.get("reliable")),
             "at_cost_tickers": sorted(at_cost),
             "fx_approx": fx_approx, "skipped_days": skipped,
             "estimated_footprint": any(cf.get("estimated") for cf in cash_flows
                                        if cf["kind"] == "trade"),
             "external_flow_rows": external_flow_rows,
             "external_flows_absent": (external_flow_rows == 0
                                       and window["days"] >= MIN_IRR_DAYS
                                       and (start_share or 0.0) > IMPLIED_START_CASH_TH),
             "implied_start_cash_share": (round(start_share, 4)
                                          if start_share is not None else None)}
    out = {"acct_twr": None, "hold_twr": hold_twr, "cash_drag": None,
           "drag_dollar_approx": None, "avg_cash_weight": None, "irr_annual": None,
           "window": window, "basis": basis, "gate": None, "notes": []}

    # gate:可用性繼承 cash reliable 三態(拍板;anchored / partial 才出帳戶柱)
    notes = []
    src = (cash_data or {}).get("source")
    if src not in ("anchored", "partial"):
        out["gate"] = _reason("no_cash_anchor", source=src)
        return out
    n_trade_flows = sum(1 for cf in cash_flows if cf["kind"] == "trade")
    if n_trade_flows < len(rows):
        # 部分交易有現金足跡、部分沒有 → 兩種口徑混在同一條現金史裡對不起來,誠實不出。
        # (完全沒有 Amount 欄的來源不會走到這裡:load_cash_flows 已用 qty×price 估滿,
        #  #375;這條剩下的職責是擋「混合來源」。)
        out["gate"] = _reason("mixed_trade_footprint",
                              with_footprint=n_trade_flows, trades=len(rows))
        return out
    if broken_ccys:
        out["gate"] = _reason("negative_cash_rollback", currencies=list(broken_ccys))
        return out
    # #180 大缺口 gate:殘差大到會實質污染每天淨值(相對帳戶規模)→ 帳戶柱算不出、出「解鎖邀請」,
    # 持倉柱 hold_twr 已在 out 照給。判定用相對量綱(殘差換 USD / 帳戶總值峰值),對齊 #172 相對哲學。
    # 中性文案:殘差成因可能漏記入金/提款/股息,不斷言是哪種(#180 契約)。
    if cash_residuals:
        acct_scale = max((abs(v) for v in V), default=0.0) or 1.0
        blocking = [r for r in cash_residuals
                    if abs(r["residual"]) * fx_at(r["currency"], n - 1) / acct_scale > RESIDUAL_TAINT_TH]
        if blocking:
            seg = max(blocking, key=lambda r: abs(r["residual"]))
            out["gate"] = _reason("cash_residual", currency=seg["currency"],
                                  start=seg["start"], end=seg["end"],
                                  residual=round(abs(seg["residual"]), 2))
            return out

    acct_twr = acct_f - 1.0 if n_acct else None
    out["acct_twr"] = acct_twr
    if acct_twr is None:
        # 每道 gate 都過了,但沒有任何一天算得出可用的鏈式因子(深 margin / 每一步都有
        # 資料洞)。舊版這裡靜靜落地成 acct_twr=None + 一句諮詢 note,渲染層看到 note
        # 非空就印「等現金錨點補齊」——把「鏈算不出來」講成「錨點沒補」(#375)。
        out["gate"] = _reason("chain_unavailable", skipped_days=skipped)
    out["avg_cash_weight"] = round(avg_cash_w, 4) if avg_cash_w is not None else None
    if acct_twr is not None and hold_twr is not None:
        out["cash_drag"] = acct_twr - hold_twr
        # 機會成本金額(輔助口徑,反事實=閒錢跟著持倉跑):平均現金 × 持倉報酬
        out["drag_dollar_approx"] = round(avg_cash_usd * hold_twr, 2)

    # 帳戶 IRR:CF_0 = −V_0(窗前結餘視同期初虛擬投入)+ 窗內存提流 + 期末 V_T
    if window["days"] < MIN_IRR_DAYS:
        notes.append(_reason("irr_window_short", days=window["days"], min_days=MIN_IRR_DAYS))
    elif V[-1] > EPS:
        cfs = []
        if V[0] > EPS:
            cfs.append((day_dates[0], -V[0]))
        for i in range(1, n):
            for cf in flows_at.get(i, ()):
                if cf["kind"] in _EXT_KINDS:
                    cfs.append((day_dates[i], -cf["amount"] * fx_at(cf.get("currency", "USD"), i)))
        cfs.append((day_dates[-1], V[-1]))
        out["irr_annual"] = xirr(cfs)
        if out["irr_annual"] is None:
            notes.append(_reason("irr_no_unique_root"))
    if skipped:
        notes.append(_reason("chain_days_skipped", skipped=skipped))
    # 諮詢註記(算得出、但有限制)與 gate(擋卡原因)分家:舊版兩者共用同一個 note 欄,
    # 渲染層只能靠 truthiness 判斷,分不出「被擋」和「算出來了但要交代」(#375)。
    out["notes"] = notes
    return out
