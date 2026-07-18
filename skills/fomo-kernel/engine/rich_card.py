#!/usr/bin/env python3
"""
fomo-kernel · rich_card — v1「人話卡」的 Rich 終端渲染層(從 trade_recap.py 抽出,refs #216 刀2a)。

trade_recap.py 是純函式引擎(CSV → metrics → JSON/state);這張人話卡只服務兩條路徑:
README 教的直跑 demo、demo_weeks.py 的 subprocess 呼叫。v2 lifecycle(review.py)一律以
TR_JSON=1 走引擎 stdout + state 檔,完全不經過本檔。

依賴方向單向:rich_card → trade_recap(讀純函式 number_line / _rank_holes 與常數
HEADLINE_TIER_W / MIN_ENTRY_BUYS)。trade_recap 不得於頂部 import rich_card——它的 main()
用延遲 import 呼叫本檔,避免 import 環,並讓「純函式引擎」名實相符(頂部零 rich 依賴)。
隱私:本檔不含任何真實帳戶路徑,只渲染引擎已算好的結構。
"""
import trade_recap

try:
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.text import Text
    from rich.rule import Rule
    from rich.table import Table
    from rich.padding import Padding
    _HAS_RICH = True
except ImportError:                  # 引擎核心(純函式 / TR_JSON 路徑)不需 rich;缺 rich 時仍可 import,別硬依賴(對齊 #26)
    _HAS_RICH = False

# 卡片固定寬度（含邊框），與 ccstory 對齊；中英混排靠 Rich East Asian Width
CARD_WIDTH = 84
_console = Console(width=CARD_WIDTH, highlight=False) if _HAS_RICH else None

def _no_rich_notice(what="復盤卡"):
    """缺 rich 時的優雅降級:純函式 / TR_JSON 不受影響,只有人話卡需要 rich 渲染。"""
    print(f"（{what}需要 rich 才能渲染:pip install rich;或用 TR_JSON=1 取得免 rich 的完整結構化輸出）")

def _money(v, with_sign=True):
    """金額帶 +/- 並上色（綠正紅負）；with_sign=False 時不強制正號。"""
    s = f"{v:+,.0f}" if with_sign else f"{v:,.0f}"
    return Text(s, style="bold green" if v >= 0 else "bold red")

def _pct(v, unit="%", bold=False):
    """unit='pp' 用在「超額報酬」對比,'%' 用在「個股/組合報酬率」。"""
    s = f"{v*100:+.0f}{unit}"
    style = ("bold " if bold else "") + ("green" if v >= 0 else "red")
    return Text(s, style=style)

def print_alpha_beta(d):
    """獨立 Panel:把報酬拆成「運氣(大盤+賽道)」vs「技巧(選股)」。"""
    if not _HAS_RICH:
        return
    if d.get("note"):
        _console.print()
        _console.print(Panel(
            Text(d['note'], style="dim"),
            title="[bold]你的報酬怎麼來的[/]  [dim]· 運氣 vs 技巧[/]",
            title_align="left", border_style="cyan", padding=(1, 2), width=CARD_WIDTH,
        ))
        return
    bench = d.get("bench") or "SPY"                     # per-market(#129 PR-2b):主基準隨市場,別硬編 SPY(純台股=^TWII)
    bs = d["benchmarks"]; spy = bs[bench]
    port = spy["port_tot"]; vs_spy = spy["excess"]
    st = d.get("alpha_stat") or {}; sp = d.get("excess_split") or {}
    t = Text()
    if d.get("scope"):                                  # 混市場:人話卡只展示 scope 市場,要標明範圍(完整 per-market 在 TR_JSON)
        t.append(f"(僅含 {d['scope']} 部位;其他市場見 TR_JSON by_market)\n", style="dim")
    t.append(f"過去 {d['n']} 個交易日:投組 ")
    t.append(f"{port*100:+.0f}%", style="bold green" if port >= 0 else "bold red")
    t.append(f"、大盤 {bench} ")
    t.append(f"{spy['bench_tot']*100:+.0f}%", style="green" if spy['bench_tot'] >= 0 else "red")
    t.append(" → 你贏大盤 " if vs_spy >= 0 else " → 你輸大盤 ")
    t.append(f"{vs_spy*100:+.0f}pp", style="bold green" if vs_spy >= 0 else "bold red")
    t.append("\n\n① 這 ", style="bold")
    t.append(f"{vs_spy*100:+.0f}pp", style="bold")
    t.append(" 從哪來(對照=你當時的板塊配置混合,兩項相加=贏大盤):", style="bold")
    if sp:
        alloc, sel = sp["allocation"], sp["selection"]
        t.append("\n   押對賽道(板塊配置):  ")
        t.append(f"{alloc*100:+.0f}pp", style="bold green" if alloc >= 0 else "bold red")
        t.append("\n   板塊內選股:          ")
        t.append(f"{sel*100:+.0f}pp", style="bold green" if sel >= 0 else "bold red")
        if sp.get("coverage", 1.0) < 0.995 and sp.get("unproxied"):
            miss = "、".join(sp["unproxied"][:4])
            t.append(f"\n   (板塊對照覆蓋 {sp['coverage']*100:.0f}% 市值;{miss} 無板塊 ETF → 按大盤計、歸入選股)",
                     style="dim")
    else:
        t.append("\n   拆帳算不出(缺板塊價格)", style="dim")
    t.append("\n\n② α(vs 通用大盤,調風險後):  ", style="bold")
    if st:
        ci = st.get("ci95") or [None, None]
        t.append(f"年化 {st['alpha_ann']*100:+.0f}%", style="bold cyan")
        if st.get("se_ann") is not None:              # se_ann==0(完美複製品)是合法值,別被 truthy 檢查漏掉
            t.append(f"  (95% 區間 {ci[0]*100:+.0f}%~{ci[1]*100:+.0f}%)", style="dim")
        t.append(f"   β {spy['beta']:.2f} (波動是大盤 {spy['beta']:.1f} 倍)\n   ")
        if st.get("grade") == "significant":
            t.append("樣本 ≥1 年且統計顯著——這塊可以當能力談(正負都算數)。", style="bold")
        elif st.get("grade") == "suggestive":
            t.append("有跡象但未達顯著——傾向有,還不能下定論。", style="yellow")
        else:
            gate = st.get("gate") or {}
            why = "樣本不到 1 年" if gate.get("reason") == "sample_short" else "區間太寬(常見原因:持倉集中、個股雜訊大)"
            t.append(f"統計上分不出是本事還是運氣({why})——工具的侷限,不是說你沒本事;拆帳與行為層照樣能看。",
                     style="dim")
    else:
        t.append(f"β {spy['beta']:.2f} (波動是大盤 {spy['beta']:.1f} 倍)——α 統計量缺(樣本不足)", style="dim")
    t.append(f"\n\n(持倉法日報酬近似;α 基準={bench};拆帳=Brinson 式兩層,配置+選股=贏大盤)", style="dim italic")
    _console.print()
    _console.print(Panel(
        t,
        title="[bold]你的報酬怎麼來的[/]  [dim]· 把運氣(大盤+賽道)和技巧(選股)分開[/]",
        title_align="left", border_style="cyan", padding=(1, 2), width=CARD_WIDTH,
    ))

def print_payoff_attr(pa):
    """獨立 Panel:已實現交易的貢獻度,誰在撐 vs 誰在拖,加反事實。"""
    if not _HAS_RICH:
        return
    if not pa:
        return
    fmt = lambda v: "—" if v is None else f"{v:.1f}"        # None=無虧損可比,別印 ∞(#21.2);人話在下方補
    t = Text()
    t.append("盈虧比 ")
    if pa["payoff"] is None:                                # 沒有任何已實現虧損 → 比率無意義,不印 ∞
        t.append("—", style="bold cyan")
        t.append(f"   {pa['n']} 筆已實現全是賺的,沒有虧損可拿來比\n")
    else:
        t.append(f"{fmt(pa['payoff'])}", style="bold cyan")
        t.append(f"   平均賺 ${pa['avg_win']:,.0f}  /  賠 ${abs(pa['avg_loss']):,.0f}  ({pa['n']} 筆已實現)\n")
    t.append("\n撐盤 ", style="bold green")
    t.append("(佔總賺):  ", style="dim green")
    t.append("、".join(f"{tk} ${w:,.0f}({p*100:.0f}%)" for tk, w, p in pa["carriers"]) or "(無已實現獲利)")
    t.append("\n拖累 ", style="bold red")
    t.append("(佔總賠):  ", style="dim red")
    t.append("、".join(f"{tk} ${l:,.0f}({p*100:.0f}%)" for tk, l, p in pa["draggers"]) or "(無已實現虧損)")
    cf = pa["counterfactual"]
    if cf:
        if cf["payoff"] is None:                            # 拿掉最大拖累後就沒有虧損了 → 它是唯一拖累
            t.append(f"\n\n→ 它是你唯一的已實現虧損:拿掉 {cf['ticker']} (淨 ${cf['drag']:,.0f}) 後,"
                     f"已實現就只剩賺的、沒有虧損可比了", style="dim")
        else:
            t.append(f"\n\n→ 拿掉最大拖累 {cf['ticker']} (淨 ${cf['drag']:,.0f}) 後,盈虧比 ", style="dim")
            t.append(f"{fmt(pa['payoff'])}", style="dim")
            t.append(" → ", style="dim")
            t.append(f"{fmt(cf['payoff'])}", style="bold cyan")
    _console.print()
    _console.print(Panel(
        t,
        title="[bold]盈虧比拆解[/]  [dim]· 誰在撐、誰在拖(已實現交易的貢獻度)[/]",
        title_align="left", border_style="cyan", padding=(1, 2), width=CARD_WIDTH,
    ))

def print_entry_style(d_entry):
    """〔風格雛形〕進場相對位置(追高 vs 抄底)——純 print,不進洞排序、只報方向。
    d_entry 由 trade_recap.dim_entry_style() 算好後傳入:engine 算數字、rich_card 只負責印,維持單向依賴。"""
    print("\n" + "─"*60)
    print("  〔風格雛形 · 進場相對位置(對事不對人,只報方向;閥未接)〕")
    if d_entry.get("note"):
        print(f"    {d_entry['note']}——這維要 yfinance 日線才算得出")
    else:
        zh = {"strength": "偏追高/順勢——買在區間高位(動能派視為策略、價值派視為追高)",
              "weakness": "偏抄底/逆勢——買在區間低位(價值派視為紀律、動能派視為接刀)",
              None: "無明顯方向(中性)"}
        conf = "樣本足" if not d_entry["low_conf"] else f"低信賴:可定位買入僅 {d_entry['n']} 筆(<{trade_recap.MIN_ENTRY_BUYS})"
        print(f"    {zh[d_entry['lean']]}")
        print(f"    進場區間位置中位 {d_entry['median_pct']*100:.0f}%（{d_entry['n']} 筆 · {conf}）"
              f" lean={d_entry['lean'] or '—'}")

def render(dims, strength=None, overview=None, best=None, worst=None, wi=None, rx=None, tdiag=None, cash=None, acct=None, lens=None):
    """把復盤卡渲染成一張 Rich Panel（cyan 邊框，ANSI color，中英對齊）。
    架構：一張外框大 Panel，內部按段用 Rule(───) 分節；五維行為診斷用 bar chart 取代內部加權公式。"""
    if not _HAS_RICH:
        _no_rich_notice(); return
    trig = trade_recap._rank_holes(dims)                                # #63:單一事實源,不再複製 tier 權重
    master = (lens or {}).get("philosophy", "交易哲學鏡片")
    parts = []

    # 〔總覽 · 金額〕
    if overview:
        o = overview; ab = o.get("ab") or {}
        ov = Text()
        ov.append("帳面總損益  ", style="bold")
        ov.append_text(_money(o['total_pnl']))
        ov.append("\n  = 已實現 ")
        ov.append_text(_money(o['realized']))
        ov.append("   未實現 ")
        ov.append_text(_money(o['unrealized']))
        cov = o.get("unrealized_coverage") or {}
        if cov.get("unpriced"):                                  # 未實現非全覆蓋 → 明講缺誰(#82:別讓省略靜默發生)
            ov.append(f"\n  ⚠ 未實現僅反映 {cov['priced_n']}/{cov['held_n']} 檔持倉,"
                      f"缺現價:{'、'.join(cov['unpriced'])}", style="dim yellow")
        ov.append("\n盈虧比 ")
        if o['payoff'] is None:                                # 無已實現虧損 → 比率無意義,不印 0/∞(#21.2 補完)
            ov.append("—", style="bold")
            ov.append("   沒有已實現虧損可比,全賺")
        else:
            ov.append(f"{o['payoff']:.1f}", style="bold")
            ov.append(f"   平均賺 ${o['avg_win']:,.0f}  vs  平均賠 ${abs(o['avg_loss']):,.0f}")
        if ab and not ab.get("note"):
            ov.append("\n贏大盤 ")
            ov.append_text(_pct(ab['excess_vs_spy'], unit="pp", bold=True))
            sp = ab.get("excess_split") or {}
            if sp:                                             # 拆帳恆等式:賽道 + 選股 = 贏大盤(永遠可出)
                ov.append("  = 押對賽道 ")
                ov.append(f"{sp['allocation']*100:+.0f}pp", style="bold")
                ov.append(" + 板塊內選股 ")
                ov.append(f"{sp['selection']*100:+.0f}pp", style="bold")
            ov.append(f"\nβ {ab['beta']:.2f}  (漲跌是大盤 {ab['beta']:.1f} 倍)")
            st = ab.get("alpha_stat") or {}
            if st:                                             # α 永遠出數,語氣看統計(#80)
                ov.append("   α ")
                ov.append(f"年化 {st['alpha_ann']*100:+.0f}%", style="bold cyan")
                if ab.get("credible"):
                    ov.append(" (≥1 年 + 統計顯著,可當能力談)", style="bold")
                else:
                    ov.append(" (區間寬,分不出本事還是運氣 → 見下方)", style="dim")
        elif ab and ab.get("note"):
            ov.append(f"\nα/β:{ab['note']}", style="dim")
        # 帳戶現金(#171):只在有現金餘額錨點(reliable)時報 weight/入金;無錨點靠 csv_sum 盲算不上卡,交 honesty 揭露
        if cash and cash.get("reliable") and cash.get("weight") is not None:
            ov.append("\n帳戶現金 ")
            ov.append(f"${cash['balance']:,.0f}", style="bold")
            ov.append(f"（佔帳戶 {cash['weight']*100:.0f}%）")
            rnd = cash.get("recent_net_deposit") or 0
            if rnd:                                            # 本期外部淨流入/出:入金判讀鉤子(該不該部署)
                ov.append(f"  本期淨{'入' if rnd > 0 else '提'}金 ")
                ov.append(f"${abs(rnd):,.0f}", style="bold")
        # 帳戶級績效(#171 B 路線):gate 過了(現金錨點可信)才有 acct_twr;講法中性報「現金效應」,
        # 正負翻譯(稀釋 vs 擋跌)是 Claude 卡的事(card-spec),人話卡只給數字。
        if acct and acct.get("acct_twr") is not None:
            ov.append("\n帳戶級(含現金) ")
            ov.append_text(_pct(acct["acct_twr"], bold=True))
            if acct.get("cash_drag") is not None:
                ov.append(f"   現金效應 {acct['cash_drag']*100:+.1f}pp")
                if acct.get("avg_cash_weight") is not None:
                    ov.append(f"（均 {acct['avg_cash_weight']*100:.0f}% 現金）", style="dim")
            if acct.get("irr_annual") is not None:
                ov.append("   帳戶年化 IRR ")
                ov.append_text(_pct(acct["irr_annual"], bold=True))
        parts.append(Padding(ov, (0, 1)))

    # 〔做得最好 / 最差的一筆〕
    if best and worst:
        parts.append(Rule(style="dim cyan"))
        # 明標這是「已賣出 round-trip」報酬,跟下方標的層的「仍持有 cost→現價」cur_ret 區隔(#21.1)
        parts.append(Padding(Text("做得最好 / 最差的一筆  ·  已賣出 round-trip(買→賣)", style="bold"), (0, 1)))
        bw = Text()
        bw.append("✓ 最賺  ", style="bold green")
        bw.append(f"{best['ticker']:<5} ")
        bw.append_text(_pct(best['ret'], bold=True))
        bw.append(f"   {best['buy_px']:.0f} → {best['sell_px']:.0f}   抱 {best['hold']} 天")
        bw.append("\n✗ 最虧  ", style="bold red")
        bw.append(f"{worst['ticker']:<5} ")
        bw.append_text(_pct(worst['ret'], bold=True))
        bw.append(f"   {worst['buy_px']:.0f} → {worst['sell_px']:.0f}   抱 {worst['hold']} 天")
        parts.append(Padding(bw, (0, 1)))

    # 〔what if〕— 動態挑「最大集中暴險」(AI thematic / 最大 sector / 最大個股 取最高)
    # 都低於 25% 門檻(真分散)→ wi 為 None,整段省略
    if wi:
        parts.append(Rule(style="dim cyan"))
        wif = Text()
        wif.append("what if · 最大集中暴險壓測", style="bold yellow")
        wif.append(f"\n你 {wi['label']} 暴險約 ${wi['mval']:,.0f}  (佔 ")
        wif.append(f"{wi['pct']*100:.0f}%", style="bold")
        wif.append(")")
        wif.append("\n  回檔 30% (一般修正)  → 帳面 ")
        wif.append(f"-${wi['drop30']:,.0f}", style="red")
        wif.append("\n  回檔 50% (深熊)       → 帳面 ")
        wif.append(f"-${wi['drop50']:,.0f}", style="bold red")
        wif.append("   撐得住嗎?", style="italic dim")
        parts.append(Padding(wif, (0, 1)))

    # 〔標的層診斷〕
    if tdiag:
        parts.append(Rule(style="dim cyan"))
        parts.append(Padding(Text("標的層診斷  ·  按金額排序,只看影響大的", style="bold"), (0, 1)))
        tbl = Table(show_header=False, box=None, padding=(0, 1), pad_edge=False, expand=False)
        tbl.add_column(width=6, no_wrap=True)
        tbl.add_column(justify="right", width=11, no_wrap=True)
        tbl.add_column(overflow="fold")
        for d in tdiag:
            tbl.add_row(
                Text(d['ticker'], style="bold"),
                _money(d['impact']),
                '  '.join(d['tags'])
            )
        parts.append(Padding(tbl, (0, 1)))
        # thesis_q 不印在卡上 → Step 2 對話用(SKILL L77-79「確認在出卡之前」);
        # 留在 tdiag dict 給 SKILL 取用,卡上只放用戶答完的定論(規格鐵律 issue #20)。

    # 5 維行為診斷 — 用 bar 取代「sev=0.80 ×tier1」內部加權公式
    parts.append(Rule(style="dim cyan"))
    parts.append(Padding(Text("5 維行為診斷  ·  bar 越長代表這項對你影響越大,紅色 = 已觸發", style="bold"), (0, 1)))
    dim_tbl = Table(show_header=False, box=None, padding=(0, 1), pad_edge=False, expand=False)
    dim_tbl.add_column(width=1, no_wrap=True)            # ● ○
    dim_tbl.add_column(width=11, no_wrap=True)           # 維度名（夠塞「部位 sizing」）
    dim_tbl.add_column(width=14, no_wrap=True)           # bar
    dim_tbl.add_column(overflow="fold")                  # 描述
    for d in sorted(dims, key=lambda d: d["severity"]*trade_recap.HEADLINE_TIER_W[d["tier"]], reverse=True):
        triggered = d["triggered"]
        sev_w = d["severity"] * trade_recap.HEADLINE_TIER_W[d["tier"]]
        filled = max(0, min(14, int(round(sev_w * 14))))
        bar = "█" * filled + "░" * (14 - filled)
        if not triggered:
            flag, dot_style, bar_style = "○", "dim", "dim"
        elif sev_w >= 0.7:
            flag, dot_style, bar_style = "●", "bold red", "red"
        elif sev_w >= 0.4:
            flag, dot_style, bar_style = "●", "yellow", "yellow"
        else:
            flag, dot_style, bar_style = "●", "dim yellow", "dim yellow"
        dim_tbl.add_row(
            Text(flag, style=dot_style),
            Text(d['dim'], style="bold" if triggered else "dim"),
            Text(bar, style=bar_style),
            Text(trade_recap.number_line(d), style="" if triggered else "dim"),
        )
    parts.append(Padding(dim_tbl, (0, 1)))

    # 先肯定 + 復盤卡（top 1-2 最高代價的洞）
    parts.append(Rule(style="dim cyan"))
    if strength:
        intro = (lens or {}).get("strength_intro", "先說你做對的一件事:")
        st = Text("✓ ", style="bold green")
        st.append(intro, style="bold green")
        st.append(f"\n  {strength}")
        parts.append(Padding(st, (0, 1)))
    if trig:
        parts.append(Padding(Text("\n復盤卡  ·  top 1-2 最高代價的洞", style="bold"), (0, 1)))
        for d in trig[:2]:
            # lens quote 不當段尾結語(SKILL L192「鏡片引言別當結語」);
            # 留 card_for 給 build_card_data/SKILL 融入敘事,卡上只放數字白話。
            block = Table(show_header=False, box=None, padding=(0, 0), pad_edge=False, expand=False)
            block.add_column(width=2, no_wrap=True)
            block.add_column(overflow="fold")
            block.add_row(Text("▍", style="bold red"),
                          Text(f"最大漏洞 · {d['dim']}", style="bold red"))
            block.add_row("", Text(trade_recap.number_line(d)))
            parts.append(Padding(block, (0, 1)))
    else:
        parts.append(Padding(Text("這幾個地基你目前都守住了。", style="green"), (0, 1)))

    # 處方層
    if rx:
        parts.append(Rule(style="dim cyan"))
        parts.append(Padding(Text("怎麼優化  ·  放大你強的 + 外包你弱的 + 砍掉純損耗", style="bold"), (0, 1)))
        rx_tbl = Table(show_header=False, box=None, padding=(0, 0), pad_edge=False, expand=False)
        rx_tbl.add_column(width=2, no_wrap=True)
        rx_tbl.add_column(overflow="fold")
        for r in rx:
            cell = Text()
            cell.append(f"{r['kind']}:", style="bold")
            cell.append(r['text'])
            if r.get("verify"):
                cell.append(f"  〔下次驗:{r['verify']}〕", style="dim italic")
            rx_tbl.add_row(Text("▸", style="bold"), cell)
        parts.append(Padding(rx_tbl, (0, 1)))
        actionable = [r for r in rx if r.get("rule")]
        if actionable:
            n = min(len(actionable), 3)
            if n == 1:                                  # 只 1 條 → 單行(避免「從這 1 條候選挑」語意怪)
                star_hdr = Text("\n★ 下次只改這一件 ", style="bold yellow")
                star_hdr.append("(可立即執行 + 可驗)", style="dim yellow")
                parts.append(Padding(star_hdr, (0, 1)))
                parts.append(Padding(Text(actionable[0]['rule'], style="bold"), (0, 3)))
            else:                                       # 2-3 條候選讓用戶挑/改一條(#29:prescribe 已能產多條)
                star_hdr = Text("\n★ 下次只改這一件 ", style="bold yellow")
                star_hdr.append(f"(從這 {n} 條候選挑/改一條)", style="dim yellow")
                parts.append(Padding(star_hdr, (0, 1)))
                cand_tbl = Table(show_header=False, box=None, padding=(0, 1), pad_edge=False, expand=False)
                cand_tbl.add_column(width=2, no_wrap=True)
                cand_tbl.add_column(overflow="fold")
                for i, r in enumerate(actionable[:3], 1):
                    cand_tbl.add_row(Text(f"{i}.", style="bold yellow"), Text(r['rule'], style="bold"))
                parts.append(Padding(cand_tbl, (0, 3)))

    _console.print()
    _console.print(Panel(
        Group(*parts),
        title=f"[bold]trade-recap  ·  鏡片 {master}[/]",
        title_align="left",
        border_style="cyan",
        padding=(1, 2),
        width=CARD_WIDTH,
    ))
