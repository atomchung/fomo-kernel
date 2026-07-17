#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""horizon.py — thesis 時間軸矛盾判定(#148 item5:SKILL Step 2 prose 閾值下沉)。

門檻 deterministic(#136):原本 SKILL.md 靠 LLM 每週自己算持有天數 + 眼球比閾值
(說年持有<90天…),錯一次就漏抓/誤抓。這裡把「算天數 + 比閾值」收進純函式,
**閾值數字的單一事實源移到本檔**,SKILL 只讀輸出的觸線標記、負責怎麼講(照 #82
「判定進 engine、文案留 Claude」)。

純狀態側,不 import trade_recap(同 ledger/revisit/coach 前例,保持標準庫、免 pandas)。
**刻意不做 active-thesis 重建**(那是 #148 第 2 項,gated on #60):輸入 = SKILL 已重建
的 active theses(每筆帶 cycle_id + horizon,清倉的另帶 exit_date),本檔只算 holding_days
(從 cycle_id 內嵌的起始日)+ 判閾值。

矛盾規則(horizon 缺欄 / null / 非三值 → 靜默跳過,不回補):
  新資料使用 locale-neutral `weeks` / `quarters` / `years`;舊 `週` / `季` / `年` 同義保留。
  清倉太快(有 exit_date):years <90d / quarters <21d → exit_too_fast
  抱太久(無 exit_date):  weeks >60d / quarters >180d → held_too_long
  (weeks 快清、years 長抱 = 正常,不標)

CLI:
  python3 horizon.py scan <active_theses.json> --as-of YYYY-MM-DD
    # active_theses.json: [{"cycle_id":"INTC#2026-01-05#1","horizon":"季",
    #                       "ticker":"INTC","maturity":"inferred","exit_date":"2026-02-10"?}, ...]
    #   exit_date 有 = 清倉(holding_days = exit_date − cycle_start);無 = 續抱(as_of − cycle_start)
    # stdout: [{cycle_id,ticker,horizon,holding_days,kind,exited,maturity}, ...](只含觸線者)
可 import:from horizon import horizon_contradiction, scan
"""
import argparse
import datetime as dt
import json
import sys

HORIZON_ALIASES = {
    "週": "weeks", "周": "weeks", "week": "weeks", "weeks": "weeks",
    "季": "quarters", "quarter": "quarters", "quarters": "quarters",
    "年": "years", "year": "years", "years": "years",
}
HORIZONS = {"weeks", "quarters", "years"}
# 閾值單一事實源(#148 item5 下沉點;dogfood 後可調,SKILL 不再各存一份)
EXIT_FAST = {"years": 90, "quarters": 21}    # 有 exit_date 且 holding_days < 門檻 → 清倉太快
HELD_LONG = {"weeks": 60, "quarters": 180}   # 無 exit_date 且 holding_days > 門檻 → 抱太久


def normalize_horizon(value):
    """Return the locale-neutral horizon id while accepting legacy stored values."""
    if not isinstance(value, str):
        return None
    return HORIZON_ALIASES.get(value.strip().lower())


def horizon_contradiction(horizon, holding_days, exited):
    """回矛盾類型字串 or None。門檻 deterministic;horizon 非三值一律 None(靜默跳過)。"""
    horizon = normalize_horizon(horizon)
    if horizon not in HORIZONS:
        return None
    if exited:
        thr = EXIT_FAST.get(horizon)
        return "exit_too_fast" if (thr is not None and holding_days < thr) else None
    thr = HELD_LONG.get(horizon)
    return "held_too_long" if (thr is not None and holding_days > thr) else None


def _cycle_start(cycle_id):
    """從 cycle_id(ticker#YYYY-MM-DD#n)取起始日;ticker#unknown / 壞格式 → None。"""
    parts = (cycle_id or "").split("#")
    if len(parts) < 2:
        return None
    try:
        return dt.date.fromisoformat(parts[1])
    except ValueError:                                # 'unknown' 或非日期 → 無從算天數
        return None


def scan(theses, as_of):
    """對 active theses 掃 horizon 觸線。回 markers(只含觸線者),SKILL 照讀。

    theses: [{cycle_id, horizon, ticker?, maturity?, exit_date?}, ...]
    as_of:  ISO 日期字串(= engine state 的 date_end),續抱標的算到這天。
    """
    as_of_d = dt.date.fromisoformat(as_of)
    out = []
    for t in theses:
        horizon, cid = normalize_horizon(t.get("horizon")), t.get("cycle_id")
        if horizon not in HORIZONS or not cid:        # 缺 horizon / cycle_id → 跳過
            continue
        start = _cycle_start(cid)
        if start is None:                             # #unknown / 壞 cycle_id → 無從算,跳過
            continue
        exit_date = t.get("exit_date")
        exited = exit_date is not None
        try:
            end = dt.date.fromisoformat(exit_date) if exited else as_of_d
        except (ValueError, TypeError):
            continue                                  # 壞 exit_date → 跳過(不硬算)
        holding_days = (end - start).days
        kind = horizon_contradiction(horizon, holding_days, exited)
        if kind:
            out.append({"cycle_id": cid, "ticker": t.get("ticker"),
                        "horizon": horizon, "holding_days": holding_days,
                        "kind": kind, "exited": exited,
                        "maturity": t.get("maturity")})
    return out


def _cmd_scan(args):
    with open(args.file, encoding="utf-8") as f:
        theses = json.load(f)
    if not isinstance(theses, list):
        print("active_theses JSON 必須是陣列(可為空 [])", file=sys.stderr)
        return 2
    print(json.dumps(scan(theses, args.as_of), ensure_ascii=False))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="thesis 時間軸矛盾判定(狀態側,純閾值)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="掃 active theses 的 horizon 觸線矛盾")
    s.add_argument("file", help="active theses 陣列 JSON(帶 cycle_id/horizon,清倉的帶 exit_date)")
    s.add_argument("--as-of", required=True, help="續抱標的算到這天(= engine state date_end)")
    s.set_defaults(fn=_cmd_scan)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
