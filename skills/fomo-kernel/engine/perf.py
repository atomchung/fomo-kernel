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

**帳戶柱多一道資料完整性 gate**:現金史回滾需要「每筆交易都有 Amount 現金足跡」——
CSV 沒有 Amount 欄時,買賣不動現金桶,加倉日會被鏈式誤算成暴漲(V 跳而 F=0)。故
kind=trade 的金流筆數 < rows 交易筆數 → 帳戶級 TWR/IRR 誠實不出(hold 柱不受影響,
它只吃 rows + 價格)。

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
                 fx_spot=None, fx_series=None):
    """帳戶級績效三數字(#171):acct_twr / cash_drag / irr_annual(+持倉柱 hold_twr 當 drag
    參照)。輸出 dict 固定鍵(值可 None),算不了 → {"note": ...} fail-closed(同 pnl_curve)。
    rows/cash_flows/cash_data/cur_map = 引擎 load / load_cash_flows / cash_position /
    currency_map 的原樣輸出;px / fx_series = fetch_prices / fetch_fx_series 的 DataFrame。"""
    if px is None or getattr(px, "empty", True) or not rows:
        return {"note": "無價格資料,帳戶級績效不算(離線或全缺價)"}
    if len(px.index) < 2:
        return {"note": "價格序列不足兩天,帳戶級績效不算"}

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
    basis = {"cash_source": (cash_data or {}).get("source"),
             "unanchored": sorted(c for c, v in by_ccy.items() if not v.get("reliable")),
             "at_cost_tickers": sorted(at_cost),
             "fx_approx": fx_approx, "skipped_days": skipped}
    out = {"acct_twr": None, "hold_twr": hold_twr, "cash_drag": None,
           "drag_dollar_approx": None, "avg_cash_weight": None, "irr_annual": None,
           "window": window, "basis": basis, "note": None}

    # gate:可用性繼承 cash reliable 三態(拍板;anchored / partial 才出帳戶柱)
    notes = []
    src = (cash_data or {}).get("source")
    if src not in ("anchored", "partial"):
        out["note"] = "現金無錨點(csv_sum),帳戶級 TWR/IRR 不出——只出持倉柱;補 TR_CASH 錨點即解鎖"
        return out
    n_trade_flows = sum(1 for cf in cash_flows if cf["kind"] == "trade")
    if n_trade_flows < len(rows):
        # CSV 缺 Amount 欄(或部分交易無現金足跡)→ 買賣不動現金桶,現金史回滾必錯
        # (加倉日 V 跳而 F=0 → 被鏈式誤算成暴漲)。錨點在也救不了,誠實不出。
        out["note"] = (f"流水裡只有 {n_trade_flows}/{len(rows)} 筆交易有 Amount 現金足跡,"
                       f"現金史重建不出來——帳戶級 TWR/IRR 不出;匯出含 Amount 欄的流水即解鎖")
        return out
    if broken_ccys:
        out["note"] = (f"無錨點幣別 {'/'.join(broken_ccys)} 的現金回滾出現負值(入金沒記全,"
                       f"假設破裂)——帳戶級 TWR/IRR 不出;補該幣別錨點即解鎖")
        return out

    acct_twr = acct_f - 1.0 if n_acct else None
    out["acct_twr"] = acct_twr
    out["avg_cash_weight"] = round(avg_cash_w, 4) if avg_cash_w is not None else None
    if acct_twr is not None and hold_twr is not None:
        out["cash_drag"] = acct_twr - hold_twr
        # 機會成本金額(輔助口徑,反事實=閒錢跟著持倉跑):平均現金 × 持倉報酬
        out["drag_dollar_approx"] = round(avg_cash_usd * hold_twr, 2)

    # 帳戶 IRR:CF_0 = −V_0(窗前結餘視同期初虛擬投入)+ 窗內存提流 + 期末 V_T
    if window["days"] < MIN_IRR_DAYS:
        notes.append(f"窗 {window['days']} 天 <{MIN_IRR_DAYS},年化 IRR 無意義不出數")
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
            notes.append("IRR 掃不到唯一根(非常規金流),誠實跳過")
    if skipped:
        notes.append(f"{skipped} 個交易日淨值非正或估不出(深 margin/資料洞),未入鏈")
    out["note"] = ";".join(notes) or None
    return out
