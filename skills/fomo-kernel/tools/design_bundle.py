#!/usr/bin/env python3
"""Build the card design-system bundle uploaded to Claude Design.

Emits self-contained previews -- inline CSS, no external references, and a
first-line ``@dsCard`` marker so the Design System pane indexes each file.
Output goes to ``ds-bundle/`` beside this script; upload it with the
DesignSync tool (project "fomo-kernel — 復盤卡排版系統").

    python3 skills/fomo-kernel/tools/design_bundle.py

``TOKENS`` and ``CARD`` below are DERIVED at import time from the runtime
stylesheets in ``skills/fomo-kernel/engine/card_renderer.py`` -- not
hand-copied (#368 Phase 1). This docstring used to warn that they were a
second, hand-mirrored source of truth that "drifts silently" per
``docs/design-guidelines.md`` §5; #361 proved the warning right by drifting
two values the same day it was written. The rule now:

* ``CARD`` is ``_HTML_WIDGET_CSS`` itself, mechanically transformed: the
  widget class ``.rc`` becomes ``.rc2`` (these previews are never loaded next
  to the real widget, so the classes must not collide), and the local
  variable-alias prefix ``--rc-`` becomes ``--``. One thing is dropped rather
  than transformed: the two rules whose declarations are *entirely*
  ``--rc-*`` custom properties (the light and dark blocks that alias a host
  theme variable -- or its fallback -- into a local name). Those exist so the
  real widget still looks right embedded in a host page that never defines
  ``--surface-2`` etc.; naively renaming them would make ``.rc2`` declare
  e.g. ``--surface-2: var(--surface-2, #fff)`` -- a self-reference, which CSS
  resolves by inheriting the ancestor's value rather than by using the
  written fallback. ``TOKENS`` is that ancestor instead: a flat ``:root``
  declaration with no indirection left to break.
* ``TOKENS`` reads its values from wherever the runtime actually defines
  them. Every surface/text/border/radius color and the spacing/type/radius
  scale trace to the fallback (or literal) half of the same two ``--rc-*``
  alias rules ``CARD`` excludes -- see above. The page background
  (``--page-bg``) traces to ``_HTML_SHIM_CSS``'s ``body`` rule, and the
  widget's font stack traces to the plain (non-token) ``font-family``
  declaration on ``_HTML_WIDGET_CSS``'s own styling rule for ``.rc``. None of
  these live only in ``card-template.html``'s ``:root`` -- that file happens
  to name the same host-theme values (it is the reference example of a host
  page that supplies them), plus a few tokens (``--font-voice``, backing
  only the template's demo-only ``.quote`` treatment) that this bundle has
  no use for, because the runtime does not implement that element either.

Regenerate after any change to ``_HTML_WIDGET_CSS``, ``_HTML_SHIM_CSS``, or
``card-template.html``: run this file again and re-upload ``ds-bundle/``.
Nothing enforces that automatically -- there is no test gate on design
output -- but there is no longer a second hand-typed copy of the CSS for
that step to fall out of sync with.

All fixture values are synthetic mock data, matching the repo's public-data
rule. Do not paste real trade figures into the previews.

``ds-bundle-README.md`` must stay English-only: it is copied into the generated
bundle, and that output path is inside a tree the documentation-language test
walks with ``rglob``. The HTML previews may hold Traditional Chinese, since
they mock a zh-TW card and are not Markdown.
"""
import pathlib
import re
import shutil
import sys

OUT = pathlib.Path(__file__).parent / "ds-bundle"

# Same relative-to-__file__ path computation tests/persona_sweep.py uses to
# find the engine, then a plain sys.path insertion (this tool only ever needs
# the current checkout's card_renderer -- no baseline-swapping use case).
TOOLS_DIR = pathlib.Path(__file__).resolve().parent
ENGINE_DIR = TOOLS_DIR.parent / "engine"
sys.path.insert(0, str(ENGINE_DIR))
import card_renderer  # noqa: E402


# --------------------------------------------------------- CSS derivation
# A minimal, stdlib-only CSS rule scanner: enough to split _HTML_WIDGET_CSS
# and _HTML_SHIM_CSS into (selector, declarations, media-condition) triples,
# with one level of @media nesting resolved (the only depth either stylesheet
# uses today). Comments are stripped first -- naive brace-counting otherwise
# misparses whatever follows a comment that precedes an @media block.
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)


def _split_top_level_rules(css_text):
    """Yield (selector, body, media_condition_or_None) for every rule."""
    css_text = _COMMENT_RE.sub("", css_text)

    def rules_in(text, media):
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch.isspace():
                i += 1
                continue
            if ch == "@":
                open_brace = text.index("{", i)
                condition = text[i:open_brace].strip()
                depth = 1
                j = open_brace + 1
                while depth:
                    if text[j] == "{":
                        depth += 1
                    elif text[j] == "}":
                        depth -= 1
                    j += 1
                inner = text[open_brace + 1:j - 1]
                yield from rules_in(inner, condition)
                i = j
                continue
            open_brace = text.index("{", i)
            selector = text[i:open_brace].strip()
            close_brace = text.index("}", open_brace)
            body = text[open_brace + 1:close_brace]
            yield selector, body, media
            i = close_brace + 1
    yield from rules_in(css_text, None)


def _declarations(body):
    """A rule body's `;`-separated `prop:value` pairs, order preserved."""
    out = []
    for chunk in body.split(";"):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        name, _, value = chunk.partition(":")
        out.append((name.strip(), value.strip()))
    return out


def _dash_rc_to_dash(text):
    return text.replace("--rc-", "--")


def _rc_to_rc2(text):
    return re.sub(r"\.rc\b", ".rc2", text)


def _is_alias_rule(selector, decls):
    """True for the two `.rc{--rc-*: ...}` blocks (light + the dark @media
    companion) that alias a host theme variable, or its fallback, into a
    local `--rc-*` name -- identified by shape (every declaration is a
    `--rc-*` custom property), not by position, so this still finds them if
    the runtime file is ever reordered."""
    return selector == ".rc" and bool(decls) and all(n.startswith("--rc-") for n, _ in decls)


def _resolve_alias_value(value):
    """An alias declaration's value is either `var(--host-name, fallback)`
    (every color/border/radius token) or a literal (the spacing/type scale)
    or a bare `var(--rc-other)` reference to another local alias (only
    `--rc-r-md`, which rides `--rc-radius`). TOKENS has no host to defer to,
    so a passthrough resolves to its fallback; a reference to another alias
    stays a live reference (rc-stripped) so overriding one still moves the
    other; a literal is unchanged."""
    value = value.strip()
    if value.startswith("var(") and value.endswith(")"):
        inner = value[4:-1]
        name, _, fallback = inner.partition(",")
        if fallback:
            return fallback.strip()
        return "var(" + _dash_rc_to_dash(name.strip()) + ")"
    return value


def _extract_tokens(widget_css):
    """(light, dark) dicts of token-name -> css value, read from the two
    alias rules in _HTML_WIDGET_CSS (see _is_alias_rule)."""
    light, dark = {}, {}
    for selector, body, media in _split_top_level_rules(widget_css):
        decls = _declarations(body)
        if not _is_alias_rule(selector, decls):
            continue
        target = dark if media == "@media (prefers-color-scheme:dark)" else light
        for name, value in decls:
            target[name[len("--rc-"):]] = _resolve_alias_value(value)
    return light, dark


def _extract_card_css(widget_css):
    """Every _HTML_WIDGET_CSS rule except the two alias rules (TOKENS' job,
    not CARD's -- see the module docstring), reassembled with `.rc` ->
    `.rc2` and `--rc-` -> `--` applied to every selector and declaration."""
    lines = []
    for selector, body, media in _split_top_level_rules(widget_css):
        decls = _declarations(body)
        if _is_alias_rule(selector, decls):
            continue
        new_selector = _rc_to_rc2(selector)
        new_body = "".join(f"{_dash_rc_to_dash(name)}:{_dash_rc_to_dash(value)};"
                            for name, value in decls)
        rule = f"{new_selector}{{{new_body}}}"
        if media:
            rule = f"{media}{{{rule}}}"
        lines.append(rule)
    return "\n".join(lines) + "\n"


def _extract_page_background(shim_css):
    """(light, dark) page background from _HTML_SHIM_CSS's `body` rule -- the
    runtime's own page-level chrome around the widget, which this bundle's
    `--page-bg` token mirrors for its own (differently laid out) pages."""
    light = dark = None
    for selector, body, media in _split_top_level_rules(shim_css):
        if selector != "body":
            continue
        for name, value in _declarations(body):
            if name == "background":
                if media == "@media (prefers-color-scheme:dark)":
                    dark = value
                else:
                    light = value
    return light, dark


def _extract_widget_font_family(widget_css):
    """The plain (non-token) `font-family` the runtime sets directly on the
    widget's own styling rule for `.rc` -- reused for this bundle's `body`
    so the font stack is not typed a third time."""
    for selector, body, media in _split_top_level_rules(widget_css):
        if selector == ".rc" and not media:
            for name, value in _declarations(body):
                if name == "font-family":
                    return value
    return None


# Names as the runtime's own comment above _HTML_WIDGET_CSS groups them:
# "Host theme variables (--surface-*, --text-*, --border, --radius) win when
# present" (the --rc-* alias rules), then the layout scale the same comment
# block documents as unshared by any host ("Layout tokens (--rc-sp-*,
# --rc-tx-*, --rc-r-*) exist because colour was the only tokenized axis").
_HOST_THEME_NAMES = ["surface-2", "surface-1", "surface-key",
                     "text-primary", "text-secondary", "text-muted",
                     "text-success", "text-danger", "text-accent",
                     "border", "border-key", "radius"]
_SCALE_NAMES = ["sp-1", "sp-2", "sp-3", "sp-4", "sp-5", "sp-6",
                "tx-micro", "tx-small", "tx-body", "tx-lead", "tx-rule", "tx-figure",
                "r-sm", "r-md", "r-lg"]


def _decls_line(names, table, extra=()):
    parts = [f"--{name}:{table[name]}" for name in names if name in table]
    parts += [f"--{key}:{value}" for key, value in extra if value is not None]
    return "; ".join(parts) + ";"


def _build_tokens_css(widget_css, shim_css):
    light, dark = _extract_tokens(widget_css)
    page_bg_light, page_bg_dark = _extract_page_background(shim_css)
    font_family = _extract_widget_font_family(widget_css)

    light_theme = _decls_line(_HOST_THEME_NAMES, light, extra=[("page-bg", page_bg_light)])
    dark_theme = _decls_line(_HOST_THEME_NAMES, dark, extra=[("page-bg", page_bg_dark)])
    scale = _decls_line(_SCALE_NAMES, light)

    # data-theme[dark]/[light] repeat the same theme-variant declarations at
    # higher specificity than the prefers-color-scheme media query, so a
    # forced theme wins regardless of OS preference -- a bundle-only feature
    # (the Design System preview pane's own toggle) with no runtime
    # counterpart to derive; only the values inside it are derived.
    return f"""\
:root{{
  {light_theme}
  {scale}
}}
@media (prefers-color-scheme:dark){{:root{{
  {dark_theme}
}}}}
:root[data-theme="dark"]{{
  {dark_theme}
}}
:root[data-theme="light"]{{
  {light_theme}
}}
body{{margin:0; background:var(--page-bg); color:var(--text-primary);
  font-family:{font_family};
  line-height:1.6; padding:var(--sp-6); font-variant-numeric:tabular-nums;}}
.spec{{font-size:var(--tx-small); color:var(--text-muted); margin:0 0 var(--sp-4);
  border-left:2px solid var(--border); padding-left:var(--sp-3); line-height:1.6;}}
.spec b{{color:var(--text-secondary);}}
"""


TOKENS = _build_tokens_css(card_renderer._HTML_WIDGET_CSS, card_renderer._HTML_SHIM_CSS)
CARD = _extract_card_css(card_renderer._HTML_WIDGET_CSS)


def page(group, name, subtitle, body, extra_css="", card=True):
    css = TOKENS + (CARD if card else "") + extra_css
    return (f'<!-- @dsCard group="{group}" name="{name}" subtitle="{subtitle}" -->\n'
            '<!doctype html>\n<html lang="zh-Hant">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            f'<title>{name}</title>\n<style>\n{css}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n')


def write(path, text):
    p = OUT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# Rebuild the generated previews only, then copy in the hand-written README so
# a fresh checkout produces the complete uploadable bundle.
for sub in ("foundations", "components", "scenarios"):
    target = OUT / sub
    if target.exists():
        shutil.rmtree(target)
OUT.mkdir(parents=True, exist_ok=True)
shutil.copyfile(pathlib.Path(__file__).parent / "ds-bundle-README.md", OUT / "README.md")

# ---------------------------------------------------------------- foundations
swatches = [
    ("--text-primary", "主要文字", "keynote、KPI 值、規則本文"),
    ("--text-secondary", "次要文字", "正文段落"),
    ("--text-muted", "弱化文字", "label、caveat、footnote"),
    ("--text-success", "語意 · 正向", "獲利數字、做對的事"),
    ("--text-danger", "語意 · 負向", "虧損數字、最大漏洞"),
    ("--text-accent", "強調", "唯一 L1：下一步規則"),
    ("--surface-1", "襯底", "tile、panel"),
    ("--surface-2", "卡片底", "卡片本體"),
    ("--surface-key", "L1 襯底", "下一步區塊"),
    ("--border", "分隔線", "區塊之間、卡片外框"),
]
rows = "".join(
    f'<div class="sw"><span class="chip" style="background:var({t})"></span>'
    f'<div class="swtxt"><b>{n}</b><code>{t}</code><span>{u}</span></div></div>'
    for t, n, u in swatches)
write("foundations/color.html", page(
    "Foundations", "Color", "10 個 token · light/dark 皆定義",
    f'<p class="spec">顏色是現有系統<b>已經做對</b>的部分，本次不動。10 個 token 全部在兩個 theme 都有值；'
    f'任何新顏色都必須同時提供 light 與 dark。語意色（正向／負向）與強調色是分開的角色，不可互相代用。</p>'
    f'<div class="grid">{rows}</div>',
    extra_css=".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:var(--sp-3)}"
              ".sw{display:flex;gap:var(--sp-3);align-items:center;background:var(--surface-1);"
              "border-radius:var(--r-md);padding:var(--sp-3)}"
              ".chip{width:34px;height:34px;border-radius:var(--r-sm);border:1px solid var(--border);flex:none}"
              ".swtxt{display:flex;flex-direction:column;min-width:0}"
              ".swtxt b{font-size:var(--tx-body)}"
              ".swtxt code{font-family:ui-monospace,Menlo,monospace;font-size:var(--tx-micro);color:var(--text-muted)}"
              ".swtxt span{font-size:var(--tx-micro);color:var(--text-muted)}",
    card=False))

sp_rows = "".join(
    f'<div class="sr"><code>--sp-{i}</code><span class="bar" style="width:{v}px"></span>'
    f'<span class="v">{v}px</span><span class="u">{u}</span></div>'
    for i, v, u in [(1, 4, "label 與值、tag 之間"), (2, 8, "同組元素、KPI grid gap"),
                    (3, 12, "區塊內段落間、tile 縱向 padding"), (4, 16, "tile 橫向 padding、panel padding"),
                    (5, 20, "section 縱向 padding"), (6, 24, "section 橫向 padding")])
write("foundations/spacing.html", page(
    "Foundations", "Spacing", "16 個裸值 → 6 步 4px scale",
    f'<p class="spec">現況：widget CSS 有 <b>16 個不同的間距值</b>（2/4/5/6/8/10/11/12/13/14/15/16/18/19/20/22px），'
    f'沒有任何 scale——顏色有 token，版面沒有。這是「排版沒有統一風格」最直接的機械成因。'
    f'收斂後全部落在 4px 基準的 6 步內。</p><div class="list">{sp_rows}</div>',
    extra_css=".list{display:flex;flex-direction:column;gap:var(--sp-2)}"
              ".sr{display:grid;grid-template-columns:72px 1fr 56px 2fr;gap:var(--sp-3);align-items:center;"
              "background:var(--surface-1);border-radius:var(--r-md);padding:var(--sp-2) var(--sp-3)}"
              ".sr code{font-family:ui-monospace,Menlo,monospace;font-size:var(--tx-small);color:var(--text-accent)}"
              ".bar{height:10px;background:var(--text-accent);opacity:.45;border-radius:2px;display:block}"
              ".v{font-size:var(--tx-small);color:var(--text-secondary);font-family:ui-monospace,Menlo,monospace}"
              ".u{font-size:var(--tx-micro);color:var(--text-muted)}",
    card=False))

ty_rows = "".join(
    f'<div class="tr"><code>--tx-{k}</code><span class="samp" style="font-size:{v}px">'
    f'往下加碼前寫出新證據 +$76,647</span><span class="v">{v}px</span><span class="u">{u}</span></div>'
    for k, v, u in [("micro", 11, "tile sub、footnote、tag"), ("small", 12, "label、caveat、區塊標題"),
                    ("body", 14, "正文（13px 併入）"), ("lead", 15, "導言"),
                    ("rule", 17, "唯一 L1：下一步規則"),
                    ("figure", 20, "keynote、KPI 值（19px 併入）")])
write("foundations/type.html", page(
    "Foundations", "Type scale", "7 步 → 6 步；13/19px 併掉，17px 給 L1",
    f'<p class="spec">砍掉 13px 與 19px 是唯一會動到現有像素的改動：13px（attribution label、rule 註腳）併入 14px，'
    f'19px（KPI 值、attribution 大字）併入 20px。字體本身沿用產品的 <b>system-ui + Noto Sans TC</b>，'
    f'資料值用 <b>ui-monospace</b> 並開 tabular-nums；不引入 webfont。</p><div class="list">{ty_rows}</div>',
    extra_css=".list{display:flex;flex-direction:column;gap:var(--sp-2)}"
              ".tr{display:grid;grid-template-columns:96px 1fr 56px 160px;gap:var(--sp-3);align-items:baseline;"
              "background:var(--surface-1);border-radius:var(--r-md);padding:var(--sp-3)}"
              ".tr code{font-family:ui-monospace,Menlo,monospace;font-size:var(--tx-small);color:var(--text-accent)}"
              ".samp{color:var(--text-primary);line-height:1.4}"
              ".v{font-size:var(--tx-small);color:var(--text-secondary);font-family:ui-monospace,Menlo,monospace}"
              ".u{font-size:var(--tx-micro);color:var(--text-muted)}",
    card=False))

write("foundations/emphasis.html", page(
    "Foundations", "Emphasis levels", "四級視覺權重 · 全卡只有一個 L1",
    '<p class="spec">產品承諾是「這週只改一件事」，所以 <b>Block 4 必須是整張卡唯一的 L1</b>。'
    '現況它與 Block 3 的 panel 樣式完全相同，而次要的 comparator 反而用 19px 大字獨立成塊——權重與語意是倒過來的。</p>'
    '<div class="lv"><div class="tagl l1">L1 · 唯一</div><div class="ex">'
    '<div class="keyrule"><p class="klabel">下次只改這一件</p>'
    '<p class="kmain">往下加碼前寫出一個進場時不知道的新證據；寫不出，不加。</p></div>'
    '<p class="note">獨立底色 + accent 左邊條 + 17px。整張卡僅此一處。</p></div></div>'
    '<div class="lv"><div class="tagl l2">L2</div><div class="ex">'
    '<p style="font-size:var(--tx-figure);font-weight:600;margin:0;letter-spacing:-.01em">+$76,647</p>'
    '<p class="note">keynote 標題、KPI tile 的值。20px / 600。</p></div></div>'
    '<div class="lv"><div class="tagl l3">L3</div><div class="ex">'
    '<p style="margin:0;font-size:var(--tx-body);color:var(--text-secondary)">持倉報酬 321%，同期 SPY 60%。</p>'
    '<p class="note">正文段落、交易列。14px。</p></div></div>'
    '<div class="lv"><div class="tagl l4">L4</div><div class="ex">'
    '<p style="margin:0;font-size:var(--tx-small);color:var(--text-muted)">其他基準　vs QQQ +243pp　vs SOXX +96pp</p>'
    '<p class="note">comparator、caveat、footnote、metadata。12px muted，永不放大。</p></div></div>',
    extra_css=".lv{display:grid;grid-template-columns:80px 1fr;gap:var(--sp-4);margin:0 0 var(--sp-3);"
              "background:var(--surface-1);border-radius:var(--r-md);padding:var(--sp-4)}"
              ".tagl{font-size:var(--tx-micro);font-weight:600;letter-spacing:.1em}"
              ".l1{color:var(--text-accent)}.l2{color:var(--text-primary)}"
              ".l3{color:var(--text-secondary)}.l4{color:var(--text-muted)}"
              ".ex{display:flex;flex-direction:column;gap:var(--sp-2);min-width:0}"
              ".note{margin:0;font-size:var(--tx-micro);color:var(--text-muted)}"
              ".keyrule{display:flex;flex-direction:column;gap:var(--sp-2);"
              "border-left:3px solid var(--text-accent);padding-left:var(--sp-4);"
              "background:var(--surface-key);padding-top:var(--sp-3);padding-bottom:var(--sp-3);"
              "border-radius:0 var(--r-md) var(--r-md) 0}"
              ".klabel{margin:0;font-size:var(--tx-micro);font-weight:600;color:var(--text-accent);letter-spacing:.1em}"
              ".kmain{margin:0;font-size:17px;font-weight:600;color:var(--text-primary);line-height:1.5}",
    card=False))

# ---------------------------------------------------------------- components
def curve_cell(label="這期走勢", sub="高點 +3% · 低點 −1%",
               path="M0,22 L100,25 L200,10 L300,2", tone="pos"):
    """The period curve as one grid cell: label, line in the value slot, sub.

    `tone` selects `.spark.pos`/`.spark.neg` -- the runtime's own class on
    the `<svg>` itself (`card_renderer._sparkline_svg`), not a bare `<svg>`.
    #368 Phase 1 derives this file's CARD CSS from `_HTML_WIDGET_CSS`, which
    styles `.spark`/`.spark path`; markup here has to match that or the
    derived CSS has nothing to select and the line renders unstyled."""
    return (f'<div class="m curve"><p class="lbl">{label}</p>'
            f'<div class="cval"><svg class="spark {tone}" viewBox="0 0 300 25" preserveAspectRatio="none" '
            f'aria-hidden="true"><path d="{path}"/></svg></div>'
            f'<p class="sub">{sub}</p></div>')

write("components/kpi-grid.html", page(
    "Components", "Metric grid", "欄數 = 亮起的格數；曲線是其中一格",
    '<p class="spec"><b>R2</b> 每一格都是同樣三段：label、一個主體 slot、一行 sub（至多兩行，換行不截斷）。'
    'grid row 會拉齊到最高格，所以要管的是「最高格由什麼決定」——靠結構統一綁住，而不是禁止某個欄位。'
    '<b>R3</b> 欄數由 <code>data-n</code> 決定，不再寫死 <code>repeat(4,1fr)</code>。'
    '<b>R5（2026-07-23 修正）</b>圖形<b>可以</b>放進格子——原本的診斷是錯的。209px 死白的成因不是「格子裡有圖」，'
    '而是那一格裝了五個部分而鄰居只有三個。線佔數字的位置、高低點佔 sub 的位置，結構就一致了。'
    '<b>已落地實測</b>：同行內每格 93px、spread 0，各行填滿 97–99%，被截斷的 sub 0 個。</p>'
    '<div class="rc2"><div class="sec"><h2>3 格（最常見：2 指標 + 曲線）</h2><div class="kpi" data-n="3">'
    '<div class="m"><p class="lbl">帳面總損益</p><p class="val neg">-$300</p><p class="sub">已實現 +$200 · 未實現 -$500</p></div>'
    + curve_cell() +
    '<div class="m"><p class="lbl">已實現盈虧比</p><p class="val">1.4</p><p class="sub">平均賺 +$140 vs 賠 $100</p></div>'
    '</div></div><div class="sec"><h2>4 格（3 指標 + 曲線）</h2><div class="kpi" data-n="4">'
    '<div class="m"><p class="lbl">帳面總損益</p><p class="val neg">-$300</p><p class="sub">已實現 +$200 · 未實現 -$500</p></div>'
    + curve_cell() +
    '<div class="m"><p class="lbl">已實現盈虧比</p><p class="val">1.4</p><p class="sub">賺 +$140 vs 賠 $100</p></div>'
    '<div class="m"><p class="lbl">相對大盤</p><p class="val pos">+261pp</p><p class="sub">β 2.05 · 對比 SPY</p></div>'
    '</div></div><div class="sec"><h2>5 格（4 指標 + 曲線）：兩行三格，曲線跨兩格</h2><div class="kpi" data-n="5">'
    '<div class="m"><p class="lbl">帳面總損益</p><p class="val neg">-$300</p><p class="sub">已實現 +$200 · 未實現 -$500</p></div>'
    + curve_cell() +
    '<div class="m"><p class="lbl">已實現盈虧比</p><p class="val">1.4</p><p class="sub">賺 +$140 vs 賠 $100</p></div>'
    '<div class="m"><p class="lbl">相對大盤</p><p class="val pos">+261pp</p><p class="sub">β 2.05 · 對比 SPY</p></div>'
    '<div class="m"><p class="lbl">年化 α</p><p class="val">+33%</p><p class="sub">統計上還不可信</p></div>'
    '</div></div></div>'))

write("components/curve-cell.html", page(
    "Components", "Curve cell", "曲線＝一格，緊鄰它描述的數字",
    '<p class="spec">曲線的價值大約等於一個指標，所以它就拿一個指標的空間——不是一條橫幅，也不是獨立區塊。'
    '它緊接在帳面總損益之後：其他格說「這期結束在哪裡」，這一格說「怎麼走到那裡的」——'
    '呈現過程中的波動，而不是某個時間切片的單一點。</p>'
    '<p class="spec">結構和其他格完全相同：label / 主體 slot（放線，高度對齊數字）/ 一行 sub（放高低點）。'
    '這正是它能安穩待在格子裡的原因。<b>待辦（#359）</b>：這一格目前只呈現、不判斷，'
    '可考慮讓 sub 改放引擎算出的判斷句，例如「78% 的損益來自 3 個交易日」。</p>'
    '<div class="rc2"><div class="sec"><h2>這期的績效</h2><div class="kpi" data-n="3">'
    '<div class="m"><p class="lbl">帳面總損益</p><p class="val neg">-$300</p><p class="sub">已實現 +$200 · 未實現 -$500</p></div>'
    + curve_cell() +
    '<div class="m"><p class="lbl">已實現盈虧比</p><p class="val">1.4</p><p class="sub">平均賺 +$140 vs 賠 $100</p></div>'
    '</div></div></div>'))

write("components/trade-row.html", page(
    "Components", "Trade row", "ticker + 金額 + 判定標籤；註解歸屬區塊",
    '<p class="spec">依金額絕對值排序。標籤數量可變，過長時換行而非截斷。'
    '<b>R6</b>：區塊級的分類說明（如 ETF 口徑）用分隔線收在區塊底部，'
    '不接在最後一列後面——否則讀者會把一般規則誤讀成該檔個案。</p>'
    '<div class="rc2"><div class="sec"><h2>關鍵交易</h2>'
    '<div class="trow"><div class="ttop"><span class="tk">PLTR</span><span class="tamt pos">+$76,647</span>'
    '<span class="ttags"><span class="tag">押太重：佔組合 49%</span></span></div>'
    '<p class="rsub">有新的可驗證證據，判斷與證據邊界已保存。</p></div>'
    '<div class="trow"><div class="ttop"><span class="tk">NVDA</span><span class="tamt pos">+$58,524</span>'
    '<span class="ttags"><span class="tag">紀律持有：賺 150%</span></span></div></div>'
    '<div class="trow"><div class="ttop"><span class="tk">AMD</span><span class="tamt neg">-$1,000</span>'
    '<span class="ttags"><span class="tag">大致中性</span></span></div></div>'
    '<p class="cavt">配置型 ETF 已從單一股票集中度排除（SPY 58%），且缺費用率資料——缺口講明，不當成零。</p>'
    '</div></div>'))

write("components/panels.html", page(
    "Components", "Verdict panels", "三種判定用色條區分，不只靠括號符號",
    '<p class="spec">三個 panel 承載的份量完全不同：肯定、最重要的問題、未下判定的觀察。'
    '現況只靠 <code>[v]</code>／<code>[X]</code>／<code>[?]</code> 括號符號區分，視覺上幾乎一樣。'
    '改用左側色條把嚴重度編進形狀，掃視時就能分辨。</p>'
    '<div class="rc2"><div class="sec"><h2>風險與問題</h2>'
    '<div class="panel strength"><p class="panel-label">你做對的一件事</p><p>你守住了其他部位的上限。</p></div>'
    '<div class="panel hole"><p class="panel-label">最大的行為漏洞</p>'
    '<p>你有 3 次在虧損倉往下加碼，其中 1 次加碼當下佔成本 &gt;25%。沒有新事實，這動作就只是修補成本。</p></div>'
    '<div class="panel pattern"><p class="panel-label">觀察到的型態（不需回答）</p>'
    '<p>NVDA、AMD 兩檔在獲利後較早出場。這期只記錄，不做判定。</p></div>'
    '</div></div>'))

write("components/next-step.html", page(
    "Components", "Next step (L1)", "整張卡唯一的視覺重心",
    '<p class="spec"><b>R4</b>：獨立底色 + accent 左邊條 + 17px，是全卡唯一的 L1。'
    '產品的價值主張就是「結束時只剩一件事要改」，版面必須讓這件事贏過其他所有區塊。'
    '可機械檢查：L1 元素數量恆為 1。</p>'
    '<div class="rc2"><div class="sec"><h2>風險與問題</h2>'
    '<div class="panel hole"><p class="panel-label">最大的行為漏洞</p><p>你有 3 次在虧損倉往下加碼。</p></div></div>'
    '<div class="sec keystep"><div class="panel rule"><p class="panel-label">下次只改這一件</p>'
    '<p class="rmain">往下加碼前寫出一個進場時不知道的新證據；寫不出，不加。</p>'
    '<p class="rground">本期實況：你在虧損部位往下加碼 3 次，包括 PLTR。</p></div></div></div>'))

write("components/footnote.html", page(
    "Components", "Disclosure footnote", "揭露集中收合，不逐句打斷敘事",
    '<p class="spec">2026-07-22 拍板：每條 honesty ledger 句子收進 Block 1 末端的單一 footnote。'
    '真實高密度帳戶會同時觸發 5 條以上，逐句插在數字之間會把指標列打成一面牆。'
    '收合後 summary 只帶數量，不帶內容。</p>'
    '<div class="rc2"><div class="sec"><h2>這期的績效</h2>'
    '<div class="kpi" data-n="2">'
    '<div class="m"><p class="lbl">帳面總損益</p><p class="val neg">-$300</p><p class="sub">已實現 +$200 · 未實現 -$500</p></div>'
    '<div class="m"><p class="lbl">年化 α</p><p class="val">+33%</p><p class="sub">統計上還不可信</p></div></div>'
    '<details class="fnote" open><summary>資料說明 · 4 則</summary><ul>'
    '<li>價格來自本機快取，非即時報價。</li>'
    '<li>未實現損益的涵蓋率為 82%，其餘標的缺價格。</li>'
    '<li>年化 α 的樣本數未達可信門檻，僅供參考。</li>'
    '<li>本期含多幣別部位，合計金額以帳戶幣別換算。</li>'
    '</ul></details></div></div>'))

# ---------------------------------------------------------------- scenarios
def scenario(fname, name, subtitle, spec, body):
    write(f"scenarios/{fname}", page("Scenarios", name, subtitle,
                                     f'<p class="spec">{spec}</p><div class="rc2">{body}</div>'))


KEYNOTE = ('<div class="sec"><p class="eyebrow">復盤卡</p>'
           '<h1>價格變低，不等於 thesis 自動變強</h1>'
           '<div class="tags"><span class="tag">2026-01-01 → 2026-07-14</span>'
           '<span class="tag">只留在本機</span></div>'
           '<p class="lead">這次加碼只有在理由能被下次復盤驗證時，才算有意識的決策。</p></div>')
NEXTSTEP = ('<div class="sec keystep"><div class="panel rule"><p class="panel-label">下次只改這一件</p>'
            '<p class="rmain">往下加碼前寫出一個進場時不知道的新證據；寫不出，不加。</p>'
            '<p class="rground">本期實況：你在虧損部位往下加碼 3 次，包括 PLTR。</p></div></div>')

scenario("a-full.html", "A · 完整卡", "4 tiles · 每月首次 · vs-market 已開",
         '每月第一次 full review 才渲染 vs-market。<b>R1</b> 在這裡最吃重：261 這個數字現況出現三處'
         '（tile、prose 的「+261 個百分點」、attribution 大字），收斂後只留在 tile，'
         'prose 只講 tile 裝不下的拆解與情境，comparator 降為一行 L4。',
         KEYNOTE +
         '<div class="sec"><h2>這期的績效</h2><div class="kpi" data-n="5">'
         '<div class="m"><p class="lbl">帳面總損益</p><p class="val neg">-$300</p><p class="sub">已實現 +$200 · 未實現 -$500</p></div>'
         + curve_cell() +
         '<div class="m"><p class="lbl">已實現盈虧比</p><p class="val">1.4</p><p class="sub">賺 +$140 vs 賠 $100</p></div>'
         '<div class="m"><p class="lbl">相對大盤</p><p class="val pos">+261pp</p><p class="sub">β 2.05 · 對比 SPY</p></div>'
         '<div class="m"><p class="lbl">年化 α</p><p class="val">+33%</p><p class="sub">統計上還不可信</p></div></div>'
         '<p>持倉報酬 321%，同期 SPY 60%。</p>'
         '<p>AI 概念股（跨板塊）暴險約 $170,963，佔 98%；回檔 30% 帳面 −$51,289、回檔 50% −$85,482，撐得住嗎？</p>'
         '<p class="cavt">其他基準　vs QQQ <b>+243pp</b>　vs SOXX <b>+96pp</b></p>'
         '<details class="fnote"><summary>資料說明 · 2 則</summary><ul>'
         '<li>本期算不出年化報酬：現金流錨點不完整。</li>'
         '<li>年化 α 的樣本數未達可信門檻，僅供參考。</li></ul></details></div>'
         '<div class="sec"><h2>關鍵交易</h2>'
         '<div class="trow"><div class="ttop"><span class="tk">PLTR</span><span class="tamt pos">+$76,647</span>'
         '<span class="ttags"><span class="tag">押太重：佔組合 49%</span></span></div></div>'
         '<div class="trow"><div class="ttop"><span class="tk">NVDA</span><span class="tamt pos">+$58,524</span>'
         '<span class="ttags"><span class="tag">紀律持有：賺 150%</span></span></div></div>'
         '<div class="trow"><div class="ttop"><span class="tk">AMD</span><span class="tamt neg">-$1,000</span>'
         '<span class="ttags"><span class="tag">大致中性</span></span></div></div>'
         '<p class="cavt">配置型 ETF 已從單一股票集中度排除（SPY 58%），且缺費用率資料——缺口講明，不當成零。</p></div>'
         '<div class="sec"><h2>風險與問題</h2>'
         '<div class="panel strength"><p class="panel-label">你做對的一件事</p><p>你守住了其他部位的上限。</p></div>'
         '<div class="panel hole"><p class="panel-label">最大的行為漏洞</p>'
         '<p>你有 3 次在虧損倉往下加碼，其中 1 次加碼當下佔成本 &gt;25%。</p></div></div>' + NEXTSTEP +
         '<div class="foot">session_id: 2026-07-14__8ddc25d506f4 · zh-TW</div>')

scenario("b-lean.html", "B · 精簡卡（最常見）", "2 tiles · 月度 gate 關閉",
         '<b>最常見的週度卡。</b>月度 gate 讓 vs-market 不渲染，只剩 2 個 tile。'
         '現況 grid 仍寫死 4 欄，634px 的 KPI 區有超過一半是空的（實測 332px）。加上曲線格共三格，正好一行填滿。'
         '兩句「算不出」的 gap note 合併成一行，避免兩個獨立段落各佔一行卻都在講「沒有資料」。',
         KEYNOTE +
         '<div class="sec"><h2>這期的績效</h2><div class="kpi" data-n="3">'
         '<div class="m"><p class="lbl">帳面總損益</p><p class="val neg">-$300</p><p class="sub">已實現 +$200 · 未實現 -$500</p></div>'
         + curve_cell() +
         '<div class="m"><p class="lbl">已實現盈虧比</p><p class="val">1.4</p><p class="sub">平均賺 +$140 vs 賠 $100</p></div></div>'
         '<p class="cavt">本期算不出年化報酬與大盤比較：現金流錨點不完整、缺可用基準序列。</p></div>'
         '<div class="sec"><h2>關鍵交易</h2><p>本期沒有可排序的標的層診斷。</p>'
         '<div class="trow"><div class="ttop"><span class="tk">PLTR</span>'
         '<span class="ttags"><span class="tag">有新的可驗證證據</span></span></div>'
         '<p class="rsub">判斷與證據邊界已保存，供下次對帳。</p></div>'
         '<p class="cavt">配置型 ETF 已從單一股票集中度排除（SPY 58%），且缺費用率資料——缺口講明，不當成零。</p></div>'
         '<div class="sec"><h2>風險與問題</h2>'
         '<div class="panel strength"><p class="panel-label">你做對的一件事</p><p>你守住了其他部位的上限。</p></div>'
         '<div class="panel hole"><p class="panel-label">最大的行為漏洞</p>'
         '<p>你有 3 次在虧損倉往下加碼，其中 1 次加碼當下佔成本 &gt;25%。沒有新事實，這動作就只是修補成本。</p></div></div>'
         + NEXTSTEP + '<div class="foot">session_id: 2026-07-14__8ddc25d506f4 · zh-TW</div>')

scenario("c-thin.html", "C · 薄歷史首次復盤", "0 tiles · 每位新用戶會遇到一次",
         '成交筆數不足 <code>MIN_ROUND_TRIPS</code> 時，引擎不做行為判定。'
         '<b>不出 KPI 區</b>（沒有可信數字就不擺空格子），Block 4 換成「解鎖條件」——'
         '但仍然是唯一的 L1，版面承諾不因資料稀薄而消失。這是新用戶的第一印象，'
         '空洞的四格骨架比誠實的一句話傷害更大。',
         '<div class="sec"><p class="eyebrow">復盤卡</p><h1>這份紀錄還太短，先不下行為判斷</h1>'
         '<div class="tags"><span class="tag">2026-07-01 → 2026-07-14</span>'
         '<span class="tag">只留在本機</span></div>'
         '<p class="lead">已經看到兩筆完整進出，再多一些就能開始比較你的出場一致性。</p></div>'
         '<div class="sec"><h2>這期的績效</h2>'
         '<p class="cavt">本期只有 2 筆完整round trip，不足以計算盈虧比與年化報酬。已記錄的部位變化會保留到下次。</p></div>'
         '<div class="sec"><h2>關鍵交易</h2>'
         '<div class="trow"><div class="ttop"><span class="tk">VOO</span><span class="tamt pos">+$1,240</span></div></div>'
         '<div class="trow"><div class="ttop"><span class="tk">AAPL</span><span class="tamt neg">-$310</span></div></div></div>'
         '<div class="sec keystep"><div class="panel rule"><p class="panel-label">下次解鎖什麼</p>'
         '<p class="rmain">再累積 3 筆完整進出，就能開始檢查你的出場一致性與持有期間。</p>'
         '<p class="rground">目前 2/5 筆。這期不設行為規則。</p></div></div>')

scenario("d-mixed.html", "D · 台美混合", "4 tiles · 持台股者每月",
         '<code>[TW]</code>／<code>[US]</code> 分組只在<b>兩個市場都真的渲染時</b>才出現——'
         '單一市場卡（常見情形）沒有要消歧義的對象，加了標籤反而是雜訊。'
         '兩組用同一種結構，避免同一張卡出現兩套版面語言。',
         '<div class="sec"><h2>這期的績效</h2><div class="kpi" data-n="5">'
         '<div class="m"><p class="lbl">帳面總損益</p><p class="val pos">+$42,180</p><p class="sub">已實現 +$8,400 · 未實現 +$33,780</p></div>'
         + curve_cell(sub="高點 +14% · 低點 −3%", path="M0,20 L100,23 L200,8 L300,2") +
         '<div class="m"><p class="lbl">已實現盈虧比</p><p class="val">2.1</p><p class="sub">賺 +$2,100 vs 賠 $1,000</p></div>'
         '<div class="m"><p class="lbl">相對大盤</p><p class="val pos">+18pp</p><p class="sub">加權平均 · 雙市場</p></div>'
         '<div class="m"><p class="lbl">年化 α</p><p class="val">+11%</p><p class="sub">樣本足夠</p></div></div>'
         '<p class="panel-label">[TW]</p><p>持倉報酬 24%，同期加權指數 9%；β 1.15。</p>'
         '<p class="panel-label">[US]</p><p>持倉報酬 31%，同期 SPY 12%；β 1.42。</p>'
         '<details class="fnote"><summary>資料說明 · 1 則</summary><ul>'
         '<li>本期含多幣別部位，合計金額以帳戶幣別換算。</li></ul></details></div>'
         '<div class="sec"><h2>關鍵交易</h2>'
         '<div class="trow"><div class="ttop"><span class="tk">2330</span><span class="tamt pos">+$21,400</span>'
         '<span class="ttags"><span class="tag">紀律持有</span></span></div></div>'
         '<div class="trow"><div class="ttop"><span class="tk">NVDA</span><span class="tamt pos">+$14,900</span>'
         '<span class="ttags"><span class="tag">押太重：佔組合 28%</span></span></div></div></div>' + NEXTSTEP)

scenario("e-dense.html", "E · 高密度揭露", "4 tiles · 5+ honesty keys · 真實帳戶常見",
         '真實帳戶常同時觸發 5 條以上揭露。2026-07-22 的翻案就是從這個場景來的：'
         '原本「caveat 貼著它解釋的數字」在高密度下把指標列打成一面牆。'
         '現在全部收進 Block 1 末端的單一 footnote，<b>預設收合</b>，summary 只帶數量。'
         '這是「避免過度堆積」在最壞情況下的表現。',
         '<div class="sec"><h2>這期的績效</h2><div class="kpi" data-n="5">'
         '<div class="m"><p class="lbl">帳面總損益</p><p class="val pos">+$12,840</p><p class="sub">已實現 +$3,200 · 未實現 +$9,640</p></div>'
         + curve_cell(sub="高點 +9% · 低點 −6%", path="M0,12 L80,22 L160,6 L240,18 L300,9") +
         '<div class="m"><p class="lbl">已實現盈虧比</p><p class="val">0.8</p><p class="sub">賺 +$800 vs 賠 $1,000</p></div>'
         '<div class="m"><p class="lbl">相對大盤</p><p class="val neg">−4pp</p><p class="sub">β 1.31 · 對比 SPY</p></div>'
         '<div class="m"><p class="lbl">年化 α</p><p class="val">−2%</p><p class="sub">統計上還不可信</p></div></div>'
         '<p>最大的已實現拖累來自 TSLA −$4,100；若當時未加碼，本期已實現會是 −$900。</p>'
         '<details class="fnote"><summary>資料說明 · 6 則</summary><ul>'
         '<li>價格來自本機快取，非即時報價。</li>'
         '<li>未實現損益的涵蓋率為 74%，其餘標的缺價格。</li>'
         '<li>年化 α 的樣本數未達可信門檻，僅供參考。</li>'
         '<li>板塊歸因僅涵蓋 68% 的部位。</li>'
         '<li>本期含多幣別部位，合計金額以帳戶幣別換算。</li>'
         '<li>現金餘額由推算得出，未與對帳單核對。</li></ul></details></div>' + NEXTSTEP)

scenario("f-snapshot.html", "F · Snapshot 路線", "1–2 tiles · 無交易史",
         '使用者只提供持倉快照、沒有交易紀錄時，引擎<b>刻意</b>不渲染歷史績效模組。'
         'Block 1 只剩持倉結構，一樣不留空欄——欄數跟著實際亮起的 tile 走。'
         '這個場景證明「欄數自適應」不是為了美觀，而是資料前提的必然結果。',
         '<div class="sec"><p class="eyebrow">復盤卡</p><h1>單一標的佔了組合的一半</h1>'
         '<div class="tags"><span class="tag">持倉快照 · 2026-07-14</span></div>'
         '<p class="lead">沒有交易紀錄，這次只看結構，不看行為。</p></div>'
         '<div class="sec"><h2>持倉結構</h2><div class="kpi" data-n="2">'
         '<div class="m"><p class="lbl">持倉市值</p><p class="val">$174,382</p><p class="sub">7 檔標的</p></div>'
         '<div class="m"><p class="lbl">最大單一部位</p><p class="val neg">49%</p><p class="sub">PLTR</p></div></div>'
         '<p class="cavt">沒有交易紀錄，本期不計算損益、報酬與大盤比較。</p></div>'
         '<div class="sec"><h2>關鍵持倉</h2>'
         '<div class="trow"><div class="ttop"><span class="tk">PLTR</span><span class="tamt">$85,447</span>'
         '<span class="ttags"><span class="tag">佔組合 49%</span></span></div></div>'
         '<div class="trow"><div class="ttop"><span class="tk">NVDA</span><span class="tamt">$48,120</span>'
         '<span class="ttags"><span class="tag">佔組合 28%</span></span></div></div></div>'
         '<div class="sec keystep"><div class="panel rule"><p class="panel-label">下次只改這一件</p>'
         '<p class="rmain">把任一單一部位壓到 20% 以下，或寫下你為什麼接受這個集中度。</p>'
         '<p class="rground">目前 PLTR 49%、NVDA 28%。</p></div></div>')

print("files:")
for f in sorted(OUT.rglob("*")):
    if f.is_file():
        print(" ", f.relative_to(OUT), f.stat().st_size)
