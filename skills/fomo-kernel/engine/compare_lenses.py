"""compare_lenses · 多哲學對照引擎(N-way, 2-D stance)

⚠️ STATUS: 對照工具 / v2c,尚未接進 skill 主卡片流(valve 整合見 docs/v2c-lens-selection.md,屬 v2a+)。
本檔的 stance × lean 是「哲學層」schema 種子。鏡片庫現有 6 面哲學(皆含 stance/lean),compare_lenses
可實跑 N-way 對照(見 __main__)。注意:① divergence = severity × dist 的排序不可蓋過「先看大額虧損」
的收斂鐵律;② 缺 stance 的維,valve 啟用時應視為 OFF(非 aligned),見藍圖 §4。

把「用 2~N 把不同哲學的尺照同一份交易、顯示分歧」做成可重複跑的函式。
北極星:對照的價值不在「給 N 份意見」,在「逼出一個岔路」——岔路本身就是診斷工具
(使用者想反駁哪一邊,就暴露他信哪套)。所以只端分歧最大的那一個洞(收斂鐵律照舊)。

核心:severity 是 lens 不變的(機械層算的行為金融),所以「分歧」不能只看 severity。
分歧在兩位大師對同一個洞的 *stance(立場)* 與 *lean(方向)* 不同 —— 2-D:

  軸1 stance(是不是洞):aligned 普世同看 / conditional 有後門 / unconditional 一律破戒 / inverted 這不是洞是策略
  軸2 lean(該怎麼做的方向):如進場 strength(買強) vs weakness(買弱) vs gap(預期差)

為什麼要 2-D:1-D 只看 stance 會把「動能(買強,inverted)」與「安全邊際(買弱,inverted)」
判成距離 0 —— 其實兩者最對立。lean 軸把方向相反但同樣『不當洞』的兩派拉開。

  pair_distance = max( |J(stance_a)−J(stance_b)| / 2 ,  LEAN_W × [lean 相反?] )
  divergence(洞) = severity × max_pairwise( pair_distance )   # N 片裡最對立的一對

接線:compare_lenses(dims, lenses) 的 dims 就是 trade_recap 各 dim 函式回傳的 dict
(dim/severity/triggered/tier + number fields),可直接接真實 engine;本檔 __main__ 用
fixture 跑、免 yfinance、可離線重現。
"""
import itertools
import json
import os

LENS_DIR = os.path.join(os.path.dirname(__file__), "..", "rubric")
DIMENSION_ID_BY_LEGACY_LABEL = {
    "出場紀律": "exit_discipline", "部位 sizing": "position_sizing",
    "分散": "diversification", "持有時間": "holding_period",
    "加碼攤平": "averaging_down", "alpha/beta": "alpha_beta", "進場": "entry_style",
}

STANCE_J = {"inverted": -1.0, "conditional": 0.5, "aligned": 0.0, "unconditional": 1.0}
# aligned = 0.0(中立基線):普世/meta 派(如交易心理)不該被當「最對立」,它沒在 fork。
# 它對各派的距離由 lean 軸補(有 lean 才算方向分歧),所以仍保留「VY 在乎 sizing」這種訊號。
LEAN_W = 0.6
STANCE_ZH = {
    "aligned": "普世·兩派同看",
    "conditional": "看動機·有後門",
    "unconditional": "一律破戒·無後門",
    "inverted": "這不是洞·是本派策略",
}
STANCE_VERB = {"conditional": "問你", "unconditional": "斷言", "inverted": "反問", "aligned": "問你"}


def load_lens(name):
    with open(os.path.join(LENS_DIR, f"{name}.lens.json"), encoding="utf-8") as f:
        return json.load(f)


def pair_distance(da, db):
    """2-D stance 距離 ∈ [0,1]。da/db = 兩片 lens 對同一個 dim 的 dict。"""
    ja = STANCE_J.get(da.get("stance", "aligned"), 0.7)
    jb = STANCE_J.get(db.get("stance", "aligned"), 0.7)
    j_dist = abs(ja - jb) / 2.0
    la, lb = da.get("lean"), db.get("lean")
    lean_dist = 1.0 if (la and lb and la != lb) else 0.0
    return max(j_dist, LEAN_W * lean_dist)


class _Safe(dict):
    def __missing__(self, k):
        return "—"


def _fill(text, dim):
    return text.format_map(_Safe(dim))


def compare_lenses(dims, lenses):
    """N 片 lens 對同一份 dims 的對照。回傳按分歧度排序的 list,
    每項記下最對立的一對 master(a, b)。只計 triggered 的洞。"""
    rows = []
    for d in dims:
        key = d.get("dim_id") or DIMENSION_ID_BY_LEGACY_LABEL.get(d["dim"], d["dim"])
        if not d.get("triggered"):
            continue
        present = [L for L in lenses if key in L.get("dims", {})]
        if len(present) < 2:
            continue
        best = (0.0, present[0], present[1])
        for a, b in itertools.combinations(present, 2):
            dist = pair_distance(a["dims"][key], b["dims"][key])
            if dist > best[0]:
                best = (dist, a, b)
        dist, la, lb = best
        rows.append({
            "key": key, "sev": d["severity"], "dist": dist,
            "div": d["severity"] * dist, "a": la, "b": lb, "dim": d, "present": present,
        })
    rows.sort(key=lambda r: r["div"], reverse=True)
    return rows


def _name(lens):
    """顯示用學派短名:優先 philosophy(v1 去名),否則 master 去括號。engine 不印人名。"""
    return (lens.get("philosophy") or lens.get("master", "?")).split("(")[0].strip()


def _short(lens, key):
    d = lens["dims"][key]
    lean = f"·{d['lean']}" if d.get("lean") else ""
    return f"{_name(lens):<8} [{d.get('stance','aligned')}{lean}]"


def render(rows):
    print("\n═══ compare_lenses · N-way 多哲學對照 ═══")
    print("選洞規則:divergence = severity × stance距離(2-D:立場 × 方向)。挑分歧最大那個,不是 severity 最大那個。\n")
    if not rows:
        print("  沒有觸發的洞可對照。")
        return
    print("[分歧排序]  洞          severity × 2D距離 = 分歧度   最對立的一對")
    for r in rows:
        mark = "🔱" if r is rows[0] else ("🔸" if r["div"] > 0 else "⚪")
        pair = f"{_name(r['a'])} ⟷ {_name(r['b'])}" if r["dist"] > 0 else "(無岔路)"
        print(f"  {mark} {r['key']:<9} {r['sev']:.2f} × {r['dist']:.2f} = {r['div']:.2f}   {pair}")

    top = rows[0]
    if top["div"] == 0:
        print("\n  所有觸發的洞,各派立場一致 → 沒有岔路可端(普世層,單鏡即可)。")
        return
    key, dim = top["key"], top["dim"]
    print(f"\n▼ 最大岔路:{key}   (分歧度 {top['div']:.2f})")
    print("  各派立場:")
    for L in top["present"]:
        print(f"    · {_short(L, key)}")

    print("\n  最對立的兩把尺:")
    for lens in (top["a"], top["b"]):
        d = lens["dims"][key]
        stance = d.get("stance", "aligned")
        verb = STANCE_VERB.get(stance, "問你")
        print(f"\n  〔{_name(lens)}〕 立場:{STANCE_ZH[stance]}" + (f" · 方向:{d['lean']}" if d.get("lean") else ""))
        print(f"     {verb}:{_fill(d['motive_q'], dim)}")
        print(f"     規矩:{d['rule']}")
        print(f"     「{d['quote']}」")
    print("\n  → 你想反駁哪一邊,就是你的派別。對照不告訴你誰對,照出你信哪套。")


# ── demo fixture:「NVDA 虧損攤平 + AI 集中」組合(免 yfinance,可離線重現) ──
# 數字 = trade_recap 各 dim 函式會算出的形狀;真實跑時換成 trade_recap 的 dims。
DEMO_DIMS = [
    dict(dim="部位 sizing", tier=1, triggered=True, severity=0.50, max_ticker="NVDA", max_pct=42),
    dict(dim="加碼攤平", tier=1, triggered=True, severity=0.75, count=4, breach=1, tickers="NVDA"),
    dict(dim="出場紀律", tier=1, triggered=True, severity=0.45, winner_early=52),
    dict(dim="分散", tier=2, triggered=True, severity=0.85, n=5, ai_pct=78),
    dict(dim="持有時間", tier=2, triggered=False, severity=0.10),
    dict(dim="alpha/beta", tier=3, triggered=True, severity=0.65, excess=12, beta=1.7),
]

LENS_NAMES = ["vincent-yu", "momentum-discipline", "concentration-conviction",
              "margin-of-safety", "trading-psychology", "grayscale-thinking"]

if __name__ == "__main__":
    # v2c 守門:只載入實際存在的鏡片檔,<2 面就優雅退化,不 crash(原本寫死 5 面只存在 1 面)。
    available = [n for n in LENS_NAMES
                 if os.path.exists(os.path.join(LENS_DIR, f"{n}.lens.json"))]
    if len(available) < 2:
        print("compare_lenses 需要 ≥2 面鏡片才有岔路可端;目前可用:"
              + (", ".join(available) or "(無)")
              + "。多哲學對照是 v2c —— 等第二面哲學鏡片寫出來、補上 stance/lean 後再跑。")
        raise SystemExit(0)
    lenses = [load_lens(n) for n in available]
    print("載入鏡片:" + " / ".join(L.get("philosophy") or L.get("master", "?") for L in lenses))
    render(compare_lenses(DEMO_DIMS, lenses))
