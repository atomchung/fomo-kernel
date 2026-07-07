#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_context.py — 市場背景注入(#37):SPY / QQQ / VIX 的窗口漲跌 + YTD,給復盤卡的市場語境。

解的問題:卡只有「你做了什麼」,沒有「市場那週在幹嘛」——你虧 3% 是選錯股,還是大盤本來就
跌 3%?你「買在高點」,還是買在 QQQ +5% 的相對強勢週?缺語境,歸因與動機判讀都失真。

分工(與 repo 慣例一致):
  - fetch 層薄:yfinance 抓日線;離線 / 未裝 → error 欄誠實退化,絕不 crash、不擋主流程。
  - 計算層純函式(compute_context):吃 {symbol: [(date_iso, close), ...]},零 pandas 依賴,
    離線測試直接餵 list(tests/test_market_context.py)。
  - 窗口由呼叫端(SKILL)決定:對帳模式 = 上次 review 的 date_end → 本次 date_end;
    初診 = date_end 往前 7 天。engine 不猜週期。

語意:
  - window_ret = 窗口起日「前一個收盤」→ 窗口最後收盤(週漲跌的自然讀法);
    找不到前收(資料頭太短)→ null,不硬算。
  - ytd_ret   = 去年最後一個收盤 → 窗口最後收盤;同樣缺錨 → null。
  - VIX 是水平值不是資產報酬:給 last / prev(窗口前收)/ delta,不給 ytd。

CLI(JSON stdout / 訊息 stderr,同 ledger / revisit 慣例):
  python3 market_context.py --start 2026-06-09 --end 2026-06-13
"""
import argparse
import datetime as dt
import json
import sys

SYMBOLS = ("SPY", "QQQ", "^VIX")          # ^VIX 的呈現名 = VIX(輸出鍵去掉 ^)
FETCH_PAD_DAYS = 14                       # 窗口前多抓幾天,才有「前收」與年末錨點的餘裕


# ─────────────────────── 計算層(純函式,離線可測)───────────────────────

def _prev_close(series, cutoff):
    """series=[(date_iso, close), ...] 升冪;回 cutoff(iso)**之前**最後一筆收盤,無 → None。"""
    prev = None
    for d, c in series:
        if d < cutoff:
            prev = c
        else:
            break
    return prev


def _window_last(series, start, end):
    """窗口 [start, end] 內最後一筆 (date, close);無 → (None, None)。"""
    last_d = last_c = None
    for d, c in series:
        if start <= d <= end:
            last_d, last_c = d, c
    return last_d, last_c


def compute_context(prices, start, end):
    """prices={symbol: [(date_iso, close), ...] 升冪};回 benchmarks dict(見 docstring 語意)。
    個別 symbol 缺資料 → 該 symbol 缺鍵(呼叫端/卡面按缺什麼講什麼,不編)。"""
    out = {}
    year_anchor = f"{end[:4]}-01-01"
    for sym, series in (prices or {}).items():
        series = sorted(series)
        last_d, last_c = _window_last(series, start, end)
        if last_c is None:
            continue                                   # 窗口內整條沒價 → 誠實缺席
        name = sym.lstrip("^")
        if name.upper() == "VIX":
            prev = _prev_close(series, start)
            out[name] = {"last": round(last_c, 2),
                         "prev": round(prev, 2) if prev is not None else None,
                         "delta": round(last_c - prev, 2) if prev is not None else None,
                         "last_date": last_d}
            continue
        base = _prev_close(series, start)
        ytd_base = _prev_close(series, year_anchor)
        out[name] = {
            "window_ret": round(last_c / base - 1.0, 6) if base else None,
            "ytd_ret": round(last_c / ytd_base - 1.0, 6) if ytd_base else None,
            "last_close": round(last_c, 4),
            "last_date": last_d,
        }
    return out


# ─────────────────────── fetch 層(薄;離線誠實退化)───────────────────────

def fetch_series(start, end, symbols=SYMBOLS):
    """yfinance 抓 [start-pad, end] 日線收盤 → ({symbol: [(date_iso, close)...]}, error|None)。
    pad 往前涵蓋到去年 12 月中,YTD 錨點(去年最後收盤)才抓得到。"""
    try:
        import yfinance as yf
    except ImportError:
        return None, "yfinance 未安裝(市場背景缺席)"
    d_start = dt.date.fromisoformat(start)
    d_end = dt.date.fromisoformat(end)
    fetch_from = min(d_start - dt.timedelta(days=FETCH_PAD_DAYS),
                     dt.date(d_end.year - 1, 12, 15))
    try:
        data = yf.download(list(symbols), start=fetch_from.isoformat(),
                           end=(d_end + dt.timedelta(days=1)).isoformat(),
                           progress=False, auto_adjust=True)
    except Exception as e:  # noqa: BLE001  # 網路/來源錯誤形態多,一律退化不 crash
        return None, f"yfinance 下載失敗: {e}"
    if data is None or len(data) == 0:
        return None, "yfinance 無資料"
    closes = data["Close"]
    prices = {}
    for sym in symbols:
        col = closes.get(sym) if hasattr(closes, "get") else None
        if col is None:
            continue
        series = [(idx.date().isoformat(), float(v))
                  for idx, v in col.items() if v == v]      # v==v 濾 NaN
        if series:
            prices[sym] = series
    if not prices:
        return None, "yfinance 無資料"
    return prices, None


# ─────────────────────────────── CLI ───────────────────────────────

def main(argv=None):
    ap = argparse.ArgumentParser(description="fomo-kernel 市場背景(#37):SPY/QQQ/VIX 窗口漲跌+YTD")
    ap.add_argument("--start", required=True, help="窗口起日 YYYY-MM-DD(含)")
    ap.add_argument("--end", required=True, help="窗口迄日 YYYY-MM-DD(含)")
    a = ap.parse_args(argv)
    try:
        if dt.date.fromisoformat(a.start) > dt.date.fromisoformat(a.end):
            print("❌ start 不可晚於 end", file=sys.stderr)
            return 1
    except ValueError as e:
        print(f"❌ 日期格式錯:{e}", file=sys.stderr)
        return 1
    prices, err = fetch_series(a.start, a.end)
    benchmarks = compute_context(prices, a.start, a.end) if prices else {}
    if err:
        print(f"⚠️  {err}", file=sys.stderr)
    print(json.dumps({"start": a.start, "end": a.end,
                      "benchmarks": benchmarks, "error": err},
                     ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
