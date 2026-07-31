#!/usr/bin/env python3
"""Structured HTML card and delivery-contract tests (#225; offline, stdlib only).

Drives a real test-drive session through the review.py CLI in an isolated root
(prepared artifacts, so no engine subprocess and no ledger access), then
asserts the preview/finalize HTML artifacts satisfy the renderer contract in
card-spec.md "Rendering": structured markup rather than a whole-document
``<pre>`` dump, self-contained (zero external requests), light/dark aware,
exactly one widget-fragment pair, localized from copy assets, numerically
consistent with the canonical Markdown card, and sparkline-conditional (#312:
including its date-range / peak-trough caption).
Doc-consistency assertions bind SKILL.md and the flows to
references/card-delivery.md and keep card-template.html de-orphaned.
"""
import copy
import html
import json
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "fomo-kernel"
sys.path.insert(0, str(ROOT / "tests"))
import test_review_v2 as v2  # noqa: E402  (shared CLI fixtures and helpers)
import card_renderer  # noqa: E402  (engine path added by test_review_v2)

SVG_RE = re.compile(r"<svg.*?</svg>(?:<p class=\"cap\">.*?</p>)?", re.S)
def _strip_curve_cell(card):
    """Drop the whole ``<div class="m curve">`` cell, nested markup included.

    The curve is one cell in the metric grid, and that cell nests a ``.cval``
    wrapper, so a non-greedy regex would stop at the first ``</div>``. Removing
    it also drops the grid's cell count by one, which ``data-n`` reflects."""
    open_tag = '<div class="m curve">'
    start = card.find(open_tag)
    if start == -1:
        return card
    depth, index = 0, start
    while index < len(card):
        if card.startswith("<div", index):
            depth += 1
            index = card.index(">", index) + 1
        elif card.startswith("</div>", index):
            depth -= 1
            index += len("</div>")
            if depth == 0:
                stripped = card[:start] + card[index:]
                # one fewer cell in the row
                return re.sub(r'(<div class="kpi" data-n=")(\d+)(">)',
                              lambda m: m.group(1) + str(int(m.group(2)) - 1) + m.group(3),
                              stripped, count=1)
        else:
            index += 1
    return card
CURVE_POINTS = [
    {"date": "2026-06-30", "cum_ret": 0.0},
    {"date": "2026-07-04", "cum_ret": -0.012},
    {"date": "2026-07-08", "cum_ret": 0.018},
    {"date": "2026-07-14", "cum_ret": 0.034},
]
# Discovered, not enumerated: a new flow route must route card delivery too,
# so the invariant has to quantify over every flow file that ships.
FLOW_FILES = tuple(sorted(p.name for p in (SKILL / "flows").glob("*.md")))


def _copy_title(language):
    # Use the same loader render_html uses so the assertion tracks the copy the
    # renderer actually reads (load_copy normalizes the language).
    return card_renderer.load_copy(language)["title"]


def _artifacts_with_curve(tmp):
    """Reuse the deterministic v2 fixture, upgraded with real curve points so
    the CLI path exercises the sparkline (the stock fixture is note-form)."""
    card_path, state_path = v2._artifacts(tmp)
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["pnl_curve"] = {"points": [dict(point) for point in CURVE_POINTS]}
    card_path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    return card_path, state_path


def _drive(language):
    """prepare(test-drive) -> preview -> finalize through the real CLI."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "demo-root"
        card, state = _artifacts_with_curve(tmp)
        prepared = v2._run("prepare", "--test-drive", "--root", root,
                           "--card-json", card, "--state-json", state,
                           "--language", language)
        assert prepared.returncode == 0, prepared.stdout + prepared.stderr
        plan = json.loads(prepared.stdout)["review_plan"]

        answers = pathlib.Path(tmp) / "answers.json"
        narrative = pathlib.Path(tmp) / "narrative.json"
        answers.write_text(json.dumps(v2._answers(plan), ensure_ascii=False), encoding="utf-8")
        narrative.write_text(json.dumps(v2._narrative(language), ensure_ascii=False),
                             encoding="utf-8")
        preview = v2._run("preview", "--root", root, "--session-id", plan["session_id"],
                          "--answers", answers, "--narrative", narrative)
        assert preview.returncode == 0, preview.stdout + preview.stderr
        preview_payload = json.loads(preview.stdout)
        preview_html_path = preview_payload.get("private_card_html_path")
        preview_html = (pathlib.Path(preview_html_path).read_text(encoding="utf-8")
                        if preview_html_path and pathlib.Path(preview_html_path).exists()
                        else None)
        pending_dir = root / ".pending" / plan["session_id"]
        pending_existed = pending_dir.is_dir()

        answers.write_text(json.dumps(v2._answers(plan, commitment="candidate_0"),
                                      ensure_ascii=False), encoding="utf-8")
        final = v2._run_finalize("--root", root, "--session-id", plan["session_id"],
                        "--answers", answers, "--narrative", narrative)
        assert final.returncode == 0, final.stdout + final.stderr
        result = json.loads(final.stdout)
        session_dir = pathlib.Path(result["path"])
        return {
            "plan": plan,
            "preview_payload": preview_payload,
            "preview_html_path": preview_html_path,
            "preview_html": preview_html,
            "pending_existed": pending_existed,
            "pending_exists_after_finalize": pending_dir.exists(),
            "finalize_payload": result,
            "html": (session_dir / "card-private.html").read_text(encoding="utf-8"),
            "markdown": (session_dir / "card-private.md").read_text(encoding="utf-8"),
            "bundle": json.loads((session_dir / "bundle.json").read_text(encoding="utf-8")),
        }


_RUNS = {}


def _session(language):
    if language not in _RUNS:
        _RUNS[language] = _drive(language)
    return _RUNS[language]


def test_finalize_html_is_structured_not_a_pre_dump():
    for language in ("zh-TW", "en"):
        html = _session(language)["html"]
        assert '<div class="rc">' in html, "structured card container missing"
        assert "<h2>" in html, "section headings missing"
        assert '<div class="sec' in html, "section surfaces missing"
        # The pre-#225 renderer escaped the whole Markdown card into one <pre>.
        assert "<pre" not in html, "old whole-document <pre> dump came back"


def test_engine_version_stamped_on_private_card_not_public():
    """#250: every session/card self-reports which build produced it, as pure
    metadata — present on the plan, the bundle, and the private HTML card's
    ``<meta>``, frozen consistently, but never leaked into the share-safe public
    card or the plain card face."""
    for language in ("zh-TW", "en"):
        run = _session(language)
        plan_ver = run["plan"].get("engine_version")
        bundle_ver = run["bundle"].get("engine_version")
        # Present and well-formed on plan + bundle.
        assert isinstance(plan_ver, dict) and plan_ver.get("id"), "plan missing engine_version.id"
        assert plan_ver.get("source") in ("file", "git", "unknown"), "bad engine_version.source"
        # Bundle carries the same stamp the plan froze (one provenance, not two).
        assert bundle_ver == plan_ver, "bundle engine_version must be frozen from the plan"
        # Stamped on the private HTML card's metadata, carrying the id.
        assert '<meta name="engine-version"' in run["html"], "private HTML card missing version meta"
        assert plan_ver["id"] in run["html"], "private HTML card should carry the version id"
        # NEVER on the share-safe public card, and never as plain card-face text.
        public_md = card_renderer.render_public(run["bundle"])
        assert "engine-version" not in public_md, "version metadata leaked into the public card"
        assert plan_ver["id"] not in public_md, "version id leaked into the public card"
        assert "engine-version" not in run["markdown"], "version meta leaked into the markdown card face"


def test_html_is_self_contained():
    for language in ("zh-TW", "en"):
        html = _session(language)["html"]
        assert "http://" not in html and "https://" not in html, \
            "HTML artifact must make zero external requests"


def test_html_supports_dark_mode():
    for language in ("zh-TW", "en"):
        assert "prefers-color-scheme" in _session(language)["html"]


def test_exactly_one_widget_fragment_pair():
    for language in ("zh-TW", "en"):
        html = _session(language)["html"]
        assert html.count("<!-- WIDGET-FRAGMENT-START -->") == 1
        assert html.count("<!-- WIDGET-FRAGMENT-END -->") == 1
        fragment = html.split("<!-- WIDGET-FRAGMENT-START -->", 1)[1] \
                       .split("<!-- WIDGET-FRAGMENT-END -->", 1)[0]
        assert "<style>" in fragment and '<div class="rc"' in fragment, \
            "widget fragment must carry its own <style> and .rc container"


def test_localized_title_from_copy_assets():
    for language in ("zh-TW", "en"):
        html = _session(language)["html"]
        title = _copy_title(language)
        assert f"<title>{title}</title>" in html, f"copy title missing for {language}"


def test_engine_numbers_match_markdown_card():
    run = _session("zh-TW")
    for token in ("-$300", "+$200"):
        assert token in run["markdown"], f"engine value missing from Markdown: {token}"
        assert token in run["html"], f"engine value missing from HTML: {token}"
    # #344: "已實現盈虧比 1.4" duplicated the payoff KPI tile one-for-one, so
    # HTML drops the sentence (the tile alone carries it there) while
    # Markdown — which has no tile grid — keeps the full sentence. The value
    # itself must still reach HTML, inside the tile.
    assert "已實現盈虧比 1.4" in run["markdown"]
    assert "已實現盈虧比 1.4" not in run["html"], \
        "#344: the payoff line must not also stand as prose once its tile renders"
    assert '<div class="m"><p class="lbl">已實現盈虧比</p><p class="val">1.4</p>' in run["html"]


def test_markdown_reader_path_surfaces_existing_risk_and_rule_before_performance():
    """#325: text-only hosts expose the key decisions without adding facts."""
    for language in ("zh-TW", "en"):
        run = _session(language)
        structure = card_renderer._card_structure(run["bundle"])
        copy = structure["copy"]
        body = run["markdown"].split("---", 2)[2].lstrip()
        performance = f"## {copy['blocks']['performance']}"
        assert body.index(f"# {structure['headline']}") < body.index(performance)
        for label, line in card_renderer._read_first_panels(structure):
            summary = f"> **{label}**\n>\n> {line}"
            assert summary in body, f"{language} Markdown reader path lost {label!r}"
            assert body.index(summary) < body.index(performance), \
                f"{language} {label!r} must be visible before performance detail"
            assert body.count(line) >= 2, \
                f"{language} reader path must project, not replace, its canonical panel"


def test_cli_private_markdown_is_the_committed_canonical_card():
    """#325: the terminal fallback is a renderer output, never new CLI copy."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "demo-root"
        card, state = _artifacts_with_curve(tmp)
        prepared = v2._run("prepare", "--test-drive", "--root", root,
                           "--card-json", card, "--state-json", state,
                           "--language", "zh-TW")
        assert prepared.returncode == 0, prepared.stdout + prepared.stderr
        plan = json.loads(prepared.stdout)["review_plan"]
        answers = pathlib.Path(tmp) / "answers.json"
        narrative = pathlib.Path(tmp) / "narrative.json"
        answers.write_text(json.dumps(v2._answers(plan, commitment="candidate_0"),
                           ensure_ascii=False), encoding="utf-8")
        narrative.write_text(json.dumps(v2._narrative("zh-TW"), ensure_ascii=False),
                             encoding="utf-8")
        finalized = v2._run_finalize("--root", root, "--session-id", plan["session_id"],
                            "--answers", answers, "--narrative", narrative)
        assert finalized.returncode == 0, finalized.stdout + finalized.stderr
        path = pathlib.Path(json.loads(finalized.stdout)["path"])
        rendered = v2._run("render", "--root", root, "--session-id", plan["session_id"],
                           "--format", "private-markdown")
        assert rendered.returncode == 0, rendered.stdout + rendered.stderr
        assert rendered.stdout == (path / "card-private.md").read_text(encoding="utf-8")


def test_sparkline_renders_only_with_curve_points():
    run = _session("zh-TW")
    html = run["html"]
    assert html.count("<svg") == 1, "curve fixture must yield exactly one sparkline"
    assert 'class="spark pos"' in html, "sparkline tone must follow the final sign"

    # Same bundle without curve data: no sparkline, and no other text change —
    # card-spec forbids inventing a new user-facing caveat for a missing curve.
    without = copy.deepcopy(run["bundle"])
    without["engine_card"].pop("pnl_curve", None)
    html_without = card_renderer.render_html(without)
    assert "<svg" not in html_without
    assert _strip_curve_cell(html) == html_without, \
        "removing curve data may only remove its cell, nothing else"

    note_form = copy.deepcopy(run["bundle"])
    note_form["engine_card"]["pnl_curve"] = {"note": "無資料"}
    html_note = card_renderer.render_html(note_form)
    assert "<svg" not in html_note and "無資料" not in html_note, \
        "note-form curve must be omitted silently, not printed"
    assert _strip_curve_cell(html) == html_note


def test_sparkline_caption_names_peak_and_trough_without_the_window():
    """#312 gave the curve a caption so it is not an unlabeled decoration; the
    2026-07-23 layout pass narrowed what that caption may say.

    ``trade_recap.pnl_curve`` anchors its first point to the start of the
    review period, so the caption's old start~end range restated the window
    the keynote already leads with -- one value, stated twice. It now names
    only the peak and trough, which no other element on the card carries, and
    no longer depends on the points carrying usable dates at all."""
    for language, peak_word, trough_word in (("zh-TW", "高點", "低點"),
                                             ("en", "peak", "trough")):
        card = _session(language)["html"]
        caption = f'<p class="sub">{peak_word} +3% · {trough_word} -1%</p>'
        assert caption in card, f"{language} caption missing or wrong: {caption!r}"
        assert not re.search(r'<p class="sub">[^<]*\d{4}-\d{2}-\d{2}', card), \
            f"{language} caption must not restate a date range (the keynote carries the window)"
        # It occupies the curve cell's sub slot, which is what keeps that cell
        # the same height as every other cell in the row.
        assert re.search(r'<div class="m curve">.*?' + re.escape(caption), card, re.S), \
            f"{language} caption must be the curve cell's sub line"

    # Dates are no longer a prerequisite for the caption, so stripping every
    # one of them must leave the card byte-identical.
    run = _session("zh-TW")
    bundle = copy.deepcopy(run["bundle"])
    points = [dict(point) for point in bundle["engine_card"]["pnl_curve"]["points"]]
    for point in points:
        point.pop("date", None)
    bundle["engine_card"]["pnl_curve"] = {"points": points}
    assert card_renderer.render_html(bundle) == run["html"], \
        "the caption no longer reads dates, so removing them must change nothing"


# #247: engine fields that light up the card-template rich layout. Values are
# synthetic but shaped exactly like trade_recap output on the committed mocks.
_RICH_CARD_FIELDS = {
    "ticker_diagnosis": [
        {"ticker": "PLTR", "impact": 76647.0, "tags": ["⚠押太重：佔組合 49%"]},
        {"ticker": "NVDA", "impact": 58524.0, "tags": ["✓紀律持有：賺 150%"]},
        {"ticker": "AMD", "impact": -1000.0, "tags": ["— 大致中性"]},
    ],
    "what_if": {"label": "AI 概念股(跨板塊)", "mval": 170963.0, "pct": 0.983,
                "drop30": 51289.0, "drop50": 85482.0},
    "prescriptions": [
        {"kind": "保留強項", "text": "保留研究流程，別讓集中度吃掉它。"},
        {"kind": "砍損耗", "text": "沒有新證據就往虧損倉加碼，是第一個要移除的行為。"},
    ],
    "alpha_beta_breakdown": {
        "bench": "SPY", "beta": 2.05, "alpha_ann": 0.33, "credible": False,
        "port_tot": 3.21, "spy_tot": 0.60, "excess_vs_spy": 2.61,
        "benchmarks": {"SPY": {"excess": 2.61}, "QQQ": {"excess": 2.43},
                       "SOXX": {"excess": 0.96}},
    },
}


def _rich_bundle(language):
    bundle = copy.deepcopy(_session(language)["bundle"])
    bundle["engine_card"].update(copy.deepcopy(_RICH_CARD_FIELDS))
    return bundle


def test_rich_layout_renders_template_blocks_from_shared_facts():
    """#247: engine facts render as the card-template layout — KPI grid, ranked
    instrument bars, stress row, attribution bars — and every rich number
    appears in BOTH surfaces (one _card_structure facts source).

    #301 retired the improve/prescription rows: the legacy zh rows in this
    fixture carry no stable ``kind`` code, so they resolve to nothing and the
    card no longer prints a prescription list anywhere."""
    bundle = _rich_bundle("zh-TW")
    html = card_renderer.render_html(bundle)
    markdown = card_renderer.render_private(bundle)
    # Four metrics plus the curve, all in one grid: the curve is worth about
    # a metric, so it gets a metric's cell rather than a band of its own.
    assert 'class="kpi" data-n="5"' in html
    assert html.count('<div class="m">') == 4 and html.count('<div class="m curve">') == 1
    assert html.count('<div class="trow">') == 3
    assert html.count('class="track"') == 3
    # The comparator rows stay; their headline figure does not. It is the same
    # excess the KPI tile already carries in full, and printing it again as a
    # display figure made a secondary fact the heaviest element in the block.
    assert 'class="attr-head"' not in html, \
        "the attribution headline must not restate the excess KPI tile"
    assert html.count('<div class="arow">') == 2
    assert '<div class="rx">' not in html, \
        "#301: the improve prescription list must not render on the card"
    assert "砍損耗" not in html and "砍損耗" not in markdown, \
        "#301: cut_loss rows are carried by the committed rule, not listed"
    # The headline already carries the primary-benchmark excess; comparator
    # rows are the alternatives only.
    assert "vs SPY" not in html and "vs QQQ" in html and "vs SOXX" in html
    for token in ("+$76,647", "-$1,000", "+243pp", "+96pp", "撐得住嗎"):
        assert token in html, f"missing from HTML: {token}"
        assert token in markdown, f"missing from Markdown: {token}"
    # Alpha below the credibility gate stays starred with its caveat.
    assert "+33% *" in html and "* 統計上還不可信" in html

    # Dropping the excess figure drops the comparator block as a whole: the
    # attribution facts and the excess tile read the same engine field under
    # the same single-scope gate, so a card cannot have one without the other.
    # That coupling is why the renderer's "tile already carries it" guard is
    # defensive rather than a live branch — asserted here so a future widening
    # of _attribution_facts does not silently resurrect the duplicate headline.
    no_excess = _rich_bundle("zh-TW")
    no_excess["engine_card"]["alpha_beta_breakdown"].pop("excess_vs_spy", None)
    no_excess_html = card_renderer.render_html(no_excess)
    assert 'class="attr-head"' not in no_excess_html \
        and '<div class="arow">' not in no_excess_html, \
        "without the excess figure the whole comparator block must be absent"


def test_rich_layout_degrades_to_plain_sections_when_facts_missing():
    """The stock fixture lacks the rich fields: KPI tiles still come from the
    overview, and every other rich block stays absent instead of inventing."""
    html = _session("zh-TW")["html"]
    assert 'class="kpi" data-n="3"' in html, \
        "two lit metrics plus the curve make three cells in one row"
    for marker in ('class="trow"', 'class="attr-head"', 'class="rx"'):
        assert marker not in html, f"unexpected rich block on plain fixture: {marker}"


def test_kpi_dashboard_uses_metric_boxes_not_flat_paragraphs():
    """#310: Total P&L, (realized) payoff ratio, benchmark excess, and
    annualized alpha — the four metrics #310 named — must render as
    card-template.html's `.kpi` row of labeled `.m` metric boxes, never
    degrade to bare `<p>` paragraphs with no dashboard structure around them.

    `<p class="lbl">` is emitted from exactly one place in the renderer (the
    grid-tile builder in ``render_html``'s ``kpi_grid()``), so pinning the
    literal ``<div class="m"><p class="lbl">...</p>`` shape for each named
    metric locks the nesting, not just the numbers — a regression that kept
    the numbers but dropped the surrounding grid/tile markup would still be
    caught here, which is exactly the failure #310 reported."""
    for language in ("zh-TW", "en"):
        kpi_copy = card_renderer.load_copy(language)["kpi"]

        # The plain/default fixture only lights pnl + payoff (no
        # alpha_beta_breakdown data), but those two must still be tiles.
        # The plain fixture lights two metrics; with the curve cell that is
        # three, so the column count is three -- not a hardcoded four that
        # would leave a quarter of the row empty.
        plain_html = _session(language)["html"]
        assert plain_html.count('<div class="kpi" data-n="3">') == 1, \
            f"{language} plain card must declare three columns for its three cells"
        for key in ("pnl", "payoff"):
            tile_open = f'<div class="m"><p class="lbl">{html.escape(kpi_copy[key])}</p>'
            assert tile_open in plain_html, \
                f"{language} {key!r} metric ({kpi_copy[key]!r}) is not inside a .kpi .m box"

        # The rich fixture also lights benchmark excess + annualized alpha —
        # all four of #310's named metrics, all four as metric boxes.
        # The rich fixture lights all four of #310's named metrics; with the
        # curve cell that is five, which wraps to two rows of three.
        rich_html = card_renderer.render_html(_rich_bundle(language))
        assert rich_html.count('<div class="kpi" data-n="') == 1
        assert rich_html.count('<div class="m">') == 4
        assert '<div class="kpi" data-n="5">' in rich_html, \
            f"{language} rich card has 4 metrics + the curve, so the grid declares 5 cells"
        for key in ("pnl", "payoff", "excess", "alpha"):
            tile_open = f'<div class="m"><p class="lbl">{html.escape(kpi_copy[key])}</p>'
            assert tile_open in rich_html, \
                f"{language} {key!r} metric ({kpi_copy[key]!r}) is not inside a .kpi .m box"


def test_card_template_matches_its_generator():
    """#401: card-template.html is generated, not hand-mirrored, from
    card-template.src.html plus the runtime's own `_HTML_SHIM_CSS` /
    `_HTML_WIDGET_CSS` literals (tools/gen_card_template.py). This replaces
    test_widget_fragment_css_stays_mirrored_with_card_template, which used to
    compare two independently hand-typed copies of the same CSS for
    declaration-for-declaration equality, exception list included. Generation
    makes that comparison unnecessary: the committed file and a fresh
    regeneration either match exactly, or the committed file is stale. This
    test only has to catch staleness -- someone edited card-template.src.html
    or a runtime CSS literal and forgot to rerun the generator.

    Importing tools/gen_card_template.py must not, by itself, rewrite
    anything: render() is a pure function, so this test's import is safe even
    though it lives in the same tools/ directory as design_bundle.py (which
    does rewrite its whole bundle at import time -- exactly the hazard
    render() is written to avoid)."""
    tools_dir = str(SKILL / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import gen_card_template

    committed = (SKILL / "card-template.html").read_text(encoding="utf-8")
    fresh = gen_card_template.render()
    assert committed == fresh, (
        "card-template.html does not match a fresh regeneration. Rerun "
        "`python3 skills/fomo-kernel/tools/gen_card_template.py` and commit "
        "the result (edit card-template.src.html first if the content itself "
        "needs to change, not the generated file).")


def test_layout_uses_the_token_scales_not_ad_hoc_pixels():
    """Spacing and type must come from the declared scales, not loose pixels.

    Colour was tokenized from the start; layout was not. That asymmetry is why
    every layout ruling had to name a pixel (`.m .sub` is 11px, the review
    window may not sit in a tile) instead of a scale, and why 16 different
    spacing values accumulated. Any spacing/type declaration that hardcodes a
    value bypasses the scale and starts that drift again, so this fails on the
    declaration rather than waiting for the visual regression.

    Geometry (bar heights, fixed column widths, hairline borders, pill radii)
    and media-query breakpoints legitimately carry raw pixels: they are not
    positions on a rhythm scale. Only the scale properties are checked, and
    1px optical adjustments are allowed.

    Scope is the runtime stylesheet, which is what actually renders.
    card-template.html is generated from this same stylesheet
    (tools/gen_card_template.py, #401), so it cannot independently drift from
    it; keeping the committed file in sync with its generator is
    ``test_card_template_matches_its_generator``'s job instead."""
    scale_props = ("font-size", "padding", "margin", "gap",
                   "padding-left", "padding-top", "margin-top")
    pattern = re.compile(r"(?<![-a-z])(" + "|".join(scale_props) + r")\s*:\s*([^;}]+)")
    # Custom-property definitions are where the scales are declared.
    body = re.sub(r"--rc-[a-z0-9-]+:[^;}]+", "", card_renderer._HTML_WIDGET_CSS)
    offenders = [(match.group(1), match.group(2).strip(), px)
                 for match in pattern.finditer(body)
                 for px in re.findall(r"(?<![\w.-])(\d+(?:\.\d+)?)px", match.group(2))
                 if float(px) > 1]
    assert not offenders, (
        "spacing/type declarations must use the token scales, "
        f"found hardcoded pixels: {offenders!r}")


def test_every_kpi_cell_has_the_same_three_part_shape():
    """Cells are uniform: label, one body slot, one sub. The curve included.

    The 209px row was never caused by "a chart sits in a cell" -- it was
    caused by one cell carrying five parts (label, value, sub, chart, caption)
    where its neighbours carried three. Grid rows stretch to their tallest
    cell, so that one cell set the height for the whole row and left the
    others with roughly 110px of dead space each. The curve may therefore live
    in a cell, as long as the line occupies the value's slot and the caption
    occupies the sub's."""
    for bundle in (_rich_bundle("zh-TW"), _rich_bundle("en")):
        card = card_renderer.render_html(bundle)
        metric_cells = re.findall(r'<div class="m">.*?</div>', card, re.S)
        assert metric_cells, "the rich fixture must render metric cells"
        for cell in metric_cells:
            assert "<svg" not in cell, \
                f"a metric cell must not carry a chart on top of its value: {cell[:80]!r}"
        curve_cell = re.search(r'<div class="m curve">(.*?)</div>\s*<div class="m">',
                               card, re.S)
        assert curve_cell, "the curve must render as its own cell inside the grid"
        body = curve_cell.group(1)
        assert '<div class="cval">' in body and "<svg" in body, \
            "the curve's line must occupy the value slot"
        assert '<p class="val' not in body, \
            "the curve cell replaces the value, it does not add to it"
        # It stands next to the figure it traces.
        assert re.search(r'<p class="val[^"]*">[^<]*</p><p class="sub">[^<]*</p></div>'
                         r'<div class="m curve">', card), \
            "the curve cell must follow the P&L metric it plots"


def test_next_step_is_the_cards_only_emphasis_ground():
    """The product promises exactly one thing to change, so exactly one
    section may carry the emphasis ground. Block 3's panels and Block 4's rule
    were previously the same `.panel` treatment, which left the single
    committed action visually indistinguishable from the diagnosis above it."""
    for language in ("zh-TW", "en"):
        for card in (card_renderer.render_html(_rich_bundle(language)),
                     _session(language)["html"]):
            assert card.count('<div class="sec keystep">') == 1, \
                f"{language}: exactly one section may carry the L1 ground"
            # It is the last content section: nothing may outrank the one action.
            keystep_at = card.index('<div class="sec keystep">')
            later = card.count('<div class="sec">', keystep_at)
            assert later == 0, \
                f"{language}: no ordinary section may follow the next-step block ({later} found)"


def test_rich_layout_zh_engine_strings_stay_off_the_english_card():
    """Legacy persisted zh literals (pre-#279 bundles) must not leak onto the
    English card; language-neutral blocks (grid, bars) still render. There is
    no read-time migration for these by owner ruling on #279 — zh renders them
    verbatim, en omits them."""
    html = card_renderer.render_html(_rich_bundle("en"))
    assert 'class="kpi"' in html and 'class="trow"' in html
    assert "押太重" not in html and "撐得住嗎" not in html
    assert 'class="rx"' not in html


def _hole_panel_chunk(html):
    """The hole panel's HTML (panels contain only <p> children, no nested divs)."""
    return html.split('<div class="panel hole">', 1)[1].split("</div>", 1)[0]


def _markdown_section(markdown, title):
    return markdown.split(f"## {title}", 1)[1].split("## ", 1)[0]


def _markdown_block(markdown, language, block):
    blocks = card_renderer.load_copy(language)["blocks"]
    return _markdown_section(markdown, blocks[block])


# The exit-follow-up and problem-ledger sentence pins moved to
# `tests/copy_corpus.py` (#402 knife 5). They were hand-mirrored literal blocks
# — a second copy of `copy/*.json` living inside a test function — and they
# pinned a renderer helper's return value rather than the card. The corpus
# renders the same fixtures through `render_private`/`render_html`/
# `render_public` and compares against a generated golden, which immediately
# showed that `_problem_lines` never reaches a card whose review scored no
# dimension: `_risks_block` returns the missing-diagnosis note first. A pin on
# the helper could not see that.


def test_stress_line_rides_block1_exposure_for_any_hole_dimension():
    """Output contract §2 (supersedes the #263/#265 split placement): the
    stress line rides Block 1's exposure indicator area unconditionally when
    its data exists. #265's intent survives — an unrelated hole never absorbs
    the stress fact — and the concentration-family holes no longer host it
    either; there is no standalone stress section on either surface."""
    # The literal, not copy["sections"]["stress"]: that key was pruned (#368,
    # 2026-07-23) precisely because nothing renders it, and reading a deleted
    # key to prove it never appears would be circular. Pinning the heading text
    # keeps this assertion falsifiable if the standalone section ever returns.
    stress_heading = "集中度壓測 · 回檔情境"
    cases = (
        ("加碼攤平", None),  # the rich fixture's own non-concentration hole
        ("分散", "前三大風險部位佔 83%，最大 driver 佔 98%。"),
        ("部位 sizing", "最大單一風險部位佔 49%，其餘平均 5%。"),
    )
    for dim, number_line in cases:
        bundle = _rich_bundle("zh-TW")
        if number_line is not None:
            hole = bundle["engine_card"]["top_holes"][0]
            hole["dim"] = hole["raw"]["dim"] = dim
            hole["number_line"] = number_line
        html = card_renderer.render_html(bundle)
        markdown = card_renderer.render_private(bundle)

        assert "撐得住嗎" not in _hole_panel_chunk(html), \
            f"the {dim} hole panel must not absorb the stress line"
        assert "撐得住嗎" in html and f"## {stress_heading}" not in markdown \
            and stress_heading not in html, \
            "the stress line must render without a standalone stress section"
        assert "撐得住嗎" in _markdown_block(markdown, "zh-TW", "performance"), \
            f"stress line must ride Block 1 when the top hole is {dim}"
        assert "撐得住嗎" not in _markdown_block(markdown, "zh-TW", "risks")


def test_keynote_and_four_blocks_in_order_on_both_surfaces():
    """Output contract §2: keynote + performance → key trades → risks →
    next step, with localized block titles from copy, on md and HTML."""
    for language in ("zh-TW", "en"):
        run = _session(language)
        blocks = card_renderer.load_copy(language)["blocks"]
        expected = [blocks[key] for key in ("performance", "trades", "risks", "next")]
        md_titles = [line[3:].strip() for line in run["markdown"].splitlines()
                     if line.startswith("## ")]
        assert md_titles == expected, f"{language} md blocks: {md_titles}"
        html_titles = re.findall(r"<h2>(.*?)</h2>", run["html"])
        assert html_titles == expected, f"{language} html blocks: {html_titles}"
        assert run["markdown"].count("# ") >= 1, "keynote headline missing"


def _html_section_chunk(html_text, title):
    """The full inner HTML for one ``<div class="sec"><h2>title</h2>...</div>``
    section, keyed by its heading text (mirrors ``_hole_panel_chunk`` above)."""
    marker = f"<h2>{html.escape(title)}</h2>"
    return html_text.split(marker, 1)[1].split("</div>", 1)[0]


def test_closing_synthesis_renders_as_fifth_block_after_next_step():
    """#345: narrative.synthesis, when authored, appends as a 5th block after
    Next step on both surfaces — a plain paragraph (no [mark] bracket, no KPI
    tile, no new CSS class), never inserted between or reordering the four
    mandatory blocks the 2026-07-21 ruling fixed."""
    synthesis_text = ("Concentration is what defined this period, and it is "
                      "still the single biggest swing factor going forward.")
    for language in ("zh-TW", "en"):
        bundle = copy.deepcopy(_session(language)["bundle"])
        bundle["narrative"]["synthesis"] = synthesis_text
        markdown = card_renderer.render_private(bundle)
        html_out = card_renderer.render_html(bundle)

        blocks = card_renderer.load_copy(language)["blocks"]
        expected = [blocks[key] for key in ("performance", "trades", "risks", "next")] \
            + [blocks["summary"]]
        md_titles = [line[3:].strip() for line in markdown.splitlines()
                     if line.startswith("## ")]
        assert md_titles == expected, f"{language} md blocks with synthesis: {md_titles}"
        html_titles = re.findall(r"<h2>(.*?)</h2>", html_out)
        assert html_titles == expected, f"{language} html blocks with synthesis: {html_titles}"

        summary_md = _markdown_section(markdown, blocks["summary"])
        assert synthesis_text in summary_md
        assert not summary_md.strip().startswith("["), \
            "closing synthesis must render as plain prose, not a [mark]-style panel"

        summary_html = _html_section_chunk(html_out, blocks["summary"])
        assert f"<p>{html.escape(synthesis_text)}</p>" in summary_html, \
            "closing synthesis must render as an unadorned <p>, reusing existing markup"
        assert 'class="panel' not in summary_html, \
            "closing synthesis is prose, not a strength/hole/rule panel"
        assert 'class="kpi"' not in summary_html and 'class="trow"' not in summary_html, \
            "closing synthesis must not invent a new KPI-tile or instrument-bar shape"


def test_closing_synthesis_absent_renders_no_fifth_block():
    """#345 fail-closed: without narrative.synthesis (the default v2 fixture,
    and any older committed session), the card renders exactly the four
    mandatory blocks on both surfaces — no Summary/總結 header and no empty
    placeholder — checked both at the rendered-surface level and directly on
    the _card_structure assembly both surfaces share."""
    for language in ("zh-TW", "en"):
        run = _session(language)
        blocks = card_renderer.load_copy(language)["blocks"]
        assert blocks["summary"] not in run["markdown"]
        assert blocks["summary"] not in run["html"]
        structure = card_renderer._card_structure(run["bundle"])
        assert [section["id"] for section in structure["sections"]] == \
            ["performance", "trades", "risks", "next"], \
            "narrative.synthesis absent must not add a 'summary' section"


def test_closing_synthesis_empty_string_is_rejected_not_silently_dropped():
    """An explicit empty-string synthesis is invalid input under the same rule
    every other optional narrative field already follows (validate_narrative
    rejects any blank value) — it fails validation loudly rather than being
    treated as a silent, well-formed 'omit this field'."""
    bundle = copy.deepcopy(_session("zh-TW")["bundle"])
    bundle["narrative"]["synthesis"] = ""
    try:
        card_renderer._card_structure(bundle)
    except card_renderer.RenderError as exc:
        assert "synthesis" in str(exc)
    else:
        raise AssertionError("an empty-string synthesis must fail validation, not render silently")


def test_zh_and_en_cards_light_the_same_blocks_from_the_same_state():
    """output-language.md §6 structure-equivalence: same state, both locales
    light the same blocks and block kinds — a locale gap is a defect."""
    shapes = {}
    for language in ("zh-TW", "en"):
        bundle = copy.deepcopy(_session("zh-TW")["bundle"])
        bundle["language"] = language
        structure = card_renderer._card_structure(bundle)
        shapes[language] = [
            (section["id"], [kind for kind, _ in section["blocks"]],
             [payload["style"] for kind, payload in section["blocks"] if kind == "panel"])
            for section in structure["sections"]
        ]
    assert shapes["zh-TW"] == shapes["en"], shapes


def test_all_honesty_collapses_into_block1_footnote_one_per_line():
    """Output contract §4 (2026-07-22 ruling, reversing the 2026-07-21
    per-number placement — real high-density accounts fragmented the
    indicator list): every triggered honesty sentence collapses into the
    Block-1 footnote, none of them ride an indicator line anymore, and the
    footnote itself prints one bulleted sentence per line instead of
    joining them into a run-on paragraph (2026-07-22 owner bullet pass)."""
    bundle = _rich_bundle("zh-TW")
    card = bundle["engine_card"]
    card["alpha_beta_breakdown"]["alpha_stat"] = {"alpha_ann": 0.33, "ci95": [0.10, 0.56]}
    card["honesty_ledger"] = [
        {"key": "alpha_credibility", "status": "gate", "data": {}},
        {"key": "accounting_reconciliation", "status": "unreconciled", "data": {}},
    ]
    bundle["narrative"]["honesty"] = {
        "alpha_credibility": "這裡的超額樣本仍薄，先當觀察不當能力。",
        "accounting_reconciliation": "匯入紀錄與快照對不攏，這期先不評分部位結構。",
    }
    markdown = card_renderer.render_private(bundle)
    html = card_renderer.render_html(bundle)

    block1 = _markdown_block(markdown, "zh-TW", "performance")
    lines = block1.splitlines()
    caveat_shaped = [line for line in lines if re.match(r"^[ \t]+[（(].*[)）][ \t]*$", line)]
    assert not caveat_shaped, \
        f"Block 1 must render zero inline caveat lines now, found: {caveat_shaped}"

    label_line = "資料備註："
    assert label_line in lines, "the footnote label must still live inside Block 1"
    footnote_lines = [x for x in lines[lines.index(label_line) + 1:] if x.strip()]
    assert footnote_lines[0].startswith("- ") and "先當觀察" in footnote_lines[0], \
        "alpha_credibility must land in the footnote, bulleted, in ledger order"
    assert footnote_lines[1].startswith("- ") and "對不攏" in footnote_lines[1], \
        "accounting_reconciliation must land in the footnote too, bulleted, on its own line"
    assert not any("先當觀察" in x and "對不攏" in x for x in lines), \
        "footnote sentences must never be joined onto a single line"

    block1_html = html.split("<h2>", 2)[1]
    assert 'class="cavt"' not in block1_html, \
        "Block 1 must not render any per-number caveat paragraph anymore"
    assert '<details class="fnote">' in block1_html, \
        "the collapsed footnote must still live inside Block 1"
    fnote_html = block1_html.split('<details class="fnote">', 1)[1]
    assert fnote_html.count("<ul>") == 1 and fnote_html.count("<li>") == 2, \
        f"the HTML footnote must render one shared <ul> with one <li> per honesty sentence, got: {fnote_html}"
    assert "先當觀察" in fnote_html and "匯入紀錄" in fnote_html, \
        "both honesty sentences must appear in the HTML footnote"


def test_price_source_rides_the_footnote_ahead_of_unrealized_coverage():
    """#289 under the 2026-07-22 footnote model (§4): the `price_source`
    disclosure carries no host number — like every honesty key it collapses
    into the Block-1 footnote. The one placement guarantee that survives is
    order: `build_honesty_ledger()` emits `price_source` before
    `unrealized_coverage` (cause before symptom), and because the footnote
    lists sentences in ledger order, that reading order must hold on the card.
    Both locales, since a locale gap here is a disclosure defect."""
    sentences = {
        "en": {"price_source": "The current prices did not come from the engine's own retrieval.",
               "unrealized_coverage": "Some positions lack a current price, so unrealized gain is incomplete."},
        "zh-TW": {"price_source": "這期的現價不是引擎自己抓到的，卡上據此說明來源。",
                  "unrealized_coverage": "部分持倉缺現價，未實現損益不是完整帳面。"},
    }
    for language in ("en", "zh-TW"):
        bundle = _rich_bundle(language)
        card = bundle["engine_card"]
        card["price_provenance"] = {"mode": "unavailable",
                                    "coverage": {"requested_n": 2, "priced_n": 0}}
        # Ledger order is the contract: cause (price_source) before symptom
        # (unrealized_coverage). The footnote must not reorder them.
        card["honesty_ledger"] = [
            {"key": "price_source", "status": "unavailable", "data": {}},
            {"key": "unrealized_coverage", "status": "present", "data": {}},
        ]
        bundle["narrative"]["honesty"] = dict(sentences[language])
        ps, uc = sentences[language]["price_source"], sentences[language]["unrealized_coverage"]

        markdown = card_renderer.render_private(bundle)
        block1 = _markdown_block(markdown, language, "performance")
        lines = block1.splitlines()

        # Footnote model: no inline per-number caveat line survives in Block 1.
        caveat_shaped = [line for line in lines if re.match(r"^[ \t]+[（(].*[)）][ \t]*$", line)]
        assert not caveat_shaped, f"{language}: price_source must not ride an inline caveat: {caveat_shaped}"

        label_line = card_renderer.load_copy(language)["footnote_label"] + (":" if language == "en" else "：")
        assert label_line in lines, f"{language}: the footnote label must live inside Block 1"
        footnote = [x for x in lines[lines.index(label_line) + 1:] if x.strip()]
        assert any(ps in x for x in footnote), f"{language}: price_source sentence must land in the footnote: {footnote}"
        assert any(uc in x for x in footnote), f"{language}: unrealized_coverage sentence must land in the footnote: {footnote}"
        ps_at = next(i for i, x in enumerate(footnote) if ps in x)
        uc_at = next(i for i, x in enumerate(footnote) if uc in x)
        assert ps_at < uc_at, \
            f"{language}: price_source (cause) must precede unrealized_coverage (symptom) in the footnote: {footnote}"

        # HTML surface: both land in the collapsed footnote, price_source first.
        html_card = card_renderer.render_html(bundle)
        block1_html = html_card.split("<h2>", 2)[1]
        fnote_html = block1_html.split('<details class="fnote">', 1)[1]
        assert ps in html.unescape(fnote_html) and uc in html.unescape(fnote_html), \
            f"{language}: both sentences must appear in the HTML footnote"
        assert html.unescape(fnote_html).index(ps) < html.unescape(fnote_html).index(uc), \
            f"{language}: HTML footnote must keep price_source ahead of unrealized_coverage"


def test_review_span_runs_to_the_price_date_the_card_is_valued_at():
    """#363 (owner ruling 2026-07-23): the card states one window, and it ends
    where the card's numbers end. `engine_state.date_end` is the last *trade*
    date, but every unrealized figure — market value, exposure, the drawdown
    scenario, the holdings-only return — is priced at `price_snapshot.as_of`.
    A long-term holder who last traded months ago would otherwise read a span
    that stopped well before the prices the whole card is built on.

    Fail-closed in both directions: an as-of date that is earlier, absent, or
    malformed leaves the span exactly as it renders today, so a bad value can
    never push the window past the engine's own dates."""
    for language in ("zh-TW", "en"):
        copy = card_renderer.load_copy(language)
        state = {"date_start": "2022-01-05", "date_end": "2025-11-03"}

        def span(as_of):
            engine_state = dict(state)
            if as_of is not None:
                engine_state["price_snapshot"] = {"as_of": as_of}
            return card_renderer._period_span({"engine_state": engine_state}, copy)

        assert "2026-07-23" in span("2026-07-23") and "2025-11-03" not in span("2026-07-23"), \
            f"{language}: the span must run to the price date the card is valued at"
        assert "2022-01-05" in span("2026-07-23"), \
            f"{language}: the first trade must still open the span"
        for degraded in (None, "2025-10-01", "not-a-date", ""):
            assert span(degraded) == span(None), \
                f"{language}: a missing/earlier/malformed as-of must not move the span ({degraded!r})"


def test_holdings_return_states_no_second_window_of_its_own():
    """#363: with the span above ending on the same price date this return is
    measured to (both are `px.index[-1]`), the sentence's own "over the N-day
    window" tail was a duration the reader had to convert back into a period
    the card had already given them in dates. It states the return alone now —
    and still renders when the engine reports no window at all."""
    for language in ("zh-TW", "en"):
        for window in ({"days": 1296}, {}, None):
            card = {"acct_perf": {"hold_twr": 7.03, "window": window}}
            texts = [item["text"] for item in card_renderer._performance_items(card, language)
                     if item.get("tag") == "account_hold"]
            assert len(texts) == 1, f"{language}: expected one holdings-return line, got {texts}"
            assert "703%" in texts[0], f"{language}: the engine number must survive: {texts[0]}"
            for banned in ("1296", "天窗口", "-day window"):
                assert banned not in texts[0], \
                    f"{language}: the sentence must state no window of its own: {texts[0]}"
_GATE_STATUSES = ("no_cash_anchor", "mixed_trade_footprint", "negative_cash_rollback",
                  "cash_residual", "chain_unavailable")


def test_account_gate_sentence_names_the_actual_blocker():
    """#375 (output-contract §4: a gap note names the *actual* blocker).

    The card used to print one hardcoded sentence — "locked until cash has a
    complete anchor" — for every reason the account pillar was gated. A real
    review that had already supplied the anchor, and was actually blocked by a
    missing cash footprint, was told to go do the thing it had just done. The
    engine now hands over {status, data} and each status gets its own sentence
    in both locales, so no two blockers can read alike.
    """
    seen = {"zh-TW": set(), "en": set()}
    for status in _GATE_STATUSES:
        for language in ("zh-TW", "en"):
            card = {"acct_perf": {"hold_twr": 0.5, "acct_twr": None,
                                  "gate": {"status": status, "data": {}}}}
            texts = [item["text"] for item in card_renderer._performance_items(card, language)
                     if item.get("tag") == "account_gate"]
            assert len(texts) == 1, f"{language}/{status}: expected one gate line, got {texts}"
            seen[language].add(texts[0])
    for language, sentences in seen.items():
        assert len(sentences) == len(_GATE_STATUSES), \
            f"{language}: every blocker needs its own wording, got {sorted(sentences)}"


# `test_account_gate_sentences_are_pinned_in_rendered_output` moved to
# `tests/copy_corpus.py` (#402 knife 5): twelve hand-mirrored sentences in a
# literal dict, one per status per locale, and one more required for every
# status the engine adds. The corpus renders all six statuses on all three
# delivery surfaces and fails on any wording drift the golden did not record.
# The distinctness invariant above stays here — it is logic, not a pin.



def test_account_gate_degrades_instead_of_rendering_blank():
    """A legacy bundle (free-text `note`, no status) and a status this renderer
    has never heard of both fall back to the generic sentence. Silence would be
    worse than a generic sentence: the reader would see the holdings pillar and
    no reason at all for the missing account line."""
    for acct in ({"hold_twr": 0.5, "acct_twr": None, "note": "legacy free text"},
                 {"hold_twr": 0.5, "acct_twr": None,
                  "gate": {"status": "a_status_added_later", "data": {}}}):
        for language in ("zh-TW", "en"):
            default = card_renderer.load_copy(language)["account_gate"]["default"]
            texts = [item["text"] for item
                     in card_renderer._performance_items({"acct_perf": acct}, language)
                     if item.get("tag") == "account_gate"]
            assert texts == [default], f"{language}/{acct}: expected the default, got {texts}"


def test_annualized_gap_note_names_the_actual_blocker():
    """The other hardcoded reason (#375): when the engine returns a bare gate
    and no holdings pillar, no account line renders at all and the Block-1 gap
    note speaks instead. It recited the cash-anchor reason for every one of
    those blockers too, including a snapshot that failed to reconcile."""
    for language in ("zh-TW", "en"):
        missing = card_renderer.load_copy(language)["block_missing"]
        for status, key in card_renderer.ANNUALIZED_GAP_NOTE_BY_GATE.items():
            if status == "no_prices":
                continue        # #289 owns this one via price_provenance, below
            card = {"acct_perf": {"gate": {"status": status, "data": {}}}}
            assert card_renderer._annualized_gap_note(card, missing) == missing[key], \
                f"{language}/{status}: gap note must name its own blocker"
        # #289 keeps precedence: price provenance is the authority on that blocker.
        blocked = {"acct_perf": {"gate": {"status": "short_price_series", "data": {}}},
                   "price_provenance": {"mode": "unavailable"}}
        assert card_renderer._annualized_gap_note(blocked, missing) == missing["annualized_prices"]
        # An unmapped status keeps today's generic wording rather than blanking.
        unknown = {"acct_perf": {"gate": {"status": "a_status_added_later", "data": {}}}}
        assert card_renderer._annualized_gap_note(unknown, missing) == missing["annualized"]


_BENCHMARK_SENTENCES = {
    "zh-TW": "持倉對 SPY 的超額報酬 +261 個百分點；β 2.05。",
    "en": "The holdings beat SPY by +261 pp; β 2.05.",
}
# port_tot / spy_tot as the card used to print them (321% / 60%). #363 sent the
# pair back to being internal; these must appear on no surface.
_RETIRED_ABSOLUTES = ("321%", "60%")
_SPLIT_SENTENCES = {
    "zh-TW": "贏大盤的 +261 個百分點拆為：市場／賽道配置 +80 個百分點、標的選擇 +181 個百分點。",
    "en": ("The portfolio's +261 pp excess split into +80 pp from market/sector allocation "
           "and +181 pp from security selection."),
}


def test_benchmark_sentence_states_the_excess_and_nothing_the_tile_repeats():
    """#363, "one concept, one indicator" (owner ruling 2026-07-23). The
    vs-market sentence used to open with two absolute total returns — the
    portfolio's `port_tot` and the benchmark's `spy_tot`. `port_tot` is the
    *same concept* as the card's cumulative return, computed on the
    regression's aligned day set, so the card answered "what did you make?"
    twice with two different numbers. The pair is internal again; the sentence
    states the excess it feeds, which is a genuinely different concept.

    What remains is exactly the excess tile's own value and sub, so HTML drops
    the sentence whole (the pnl/payoff treatment) rather than trimming it.
    Markdown, with no tile grid, keeps it as its only carrier — the shape
    `check_card.py`'s S-2 needles as proof the vs-market module rendered.

    The allocation/selection split right below carries no kpi_id and must
    survive — asserted, not assumed, since it quotes the same +261 pp figure
    and a too-broad dedup would take it too."""
    for language in ("zh-TW", "en"):
        sentence = _BENCHMARK_SENTENCES[language]
        split = _SPLIT_SENTENCES[language]
        bundle = _rich_bundle(language)
        bundle["engine_card"]["alpha_beta_breakdown"]["excess_split"] = {
            "allocation": 0.80, "selection": 1.81}
        markdown = card_renderer.render_private(bundle)
        html_card = card_renderer.render_html(bundle)
        block1_md = _markdown_block(markdown, language, "performance")
        block1_html = html.unescape(html_card.split("<h2>", 2)[1])

        assert sentence in block1_md, \
            f"{language}: Markdown must carry the excess sentence — nothing else there does"
        assert sentence not in block1_html, \
            f"{language}: the excess tile already states this; HTML must not repeat it as prose"
        # Gone from both surfaces: the regression intermediate that read as a
        # second, competing cumulative return.
        for retired in _RETIRED_ABSOLUTES:
            assert retired not in block1_md, \
                f"{language}: {retired} (port_tot/spy_tot) must not render on Markdown"
            assert retired not in block1_html, \
                f"{language}: {retired} (port_tot/spy_tot) must not render on HTML"
        # The figures the prose gave up are still on the card — in the tile.
        kpi_copy = card_renderer.load_copy(language)["kpi"]
        tile_head = block1_html.split(
            f'<p class="lbl">{kpi_copy["excess"]}</p>', 1)[1].split("</div>", 1)[0]
        assert "+261pp" in tile_head and "β 2.05" in tile_head, \
            f"{language}: the excess tile must carry the excess and β"
        # The split explains where the excess came from; no tile holds it.
        assert split in block1_md, f"{language}: Markdown lost the allocation/selection split"
        assert split in block1_html, \
            f"{language}: the allocation/selection split must survive HTML dedup"


def test_benchmark_sentence_stays_whole_where_no_excess_tile_exists():
    """#362/#363's other half: the drop is conditional on the tile actually
    being there. A mixed-market card renders per-market vs-market rows and no
    excess tile at all (a synthetic top-level figure is the one thing the
    engine refuses), so nothing carries the pp/β figures but the sentence
    itself — it must render on HTML, exactly as on Markdown."""
    for language in ("zh-TW", "en"):
        bundle = _rich_bundle(language)
        bundle["engine_card"]["alpha_beta_breakdown"] = {
            "by_market": {
                "TW": {"bench": "^TWII", "port_tot": 0.20, "spy_tot": 0.10,
                       "excess_vs_spy": 0.10, "beta": 1.10},
                "US": {"bench": "SPY", "port_tot": 0.05, "spy_tot": 0.08,
                       "excess_vs_spy": -0.03, "beta": 0.80},
            },
        }
        html_card = html.unescape(card_renderer.render_html(bundle))
        kpi_copy = card_renderer.load_copy(language)["kpi"]
        assert f'<p class="lbl">{kpi_copy["excess"]}</p>' not in html_card, \
            f"{language}: a mixed-market card must not grow an excess tile"
        expected = ("TW 部位對 ^TWII 的超額報酬 +10 個百分點；β 1.10。"
                    if language == "zh-TW" else
                    "TW holdings beat ^TWII by +10 pp; β 1.10.")
        assert expected in html_card, \
            f"{language}: with no tile to carry them, the pp/β figures must stay in the prose"
        # The per-market absolute returns are internal now too (#363).
        for retired in ("20%", "10%", "5%", "8%"):
            assert f"報酬 {retired}" not in html_card and f"returned {retired}" not in html_card, \
                f"{language}: per-market port_tot/spy_tot must not render either ({retired})"


def test_vs_market_groups_by_market_label_only_when_mixed():
    """Adjustment 2A (#276 2026-07-22 dogfood note: "台股和美股部分也比較混
    亂，最好分模塊"): a mixed-market card labels each market's vs-market
    cluster ([TW]/[US]) on both surfaces so the two markets read as separate
    modules instead of interleaved prose, and bullets each market's lines
    (2026-07-22 owner bullet pass) — reusing the existing <ul>/<li> markup,
    grouped per market so the list never fragments into one <ul> per line.
    A single-market card — the common case, and what every other test in
    this file renders — has nothing to disambiguate and must show no such
    label or bullet (regression guard for the 2+-market gate)."""
    bundle = _rich_bundle("zh-TW")
    bundle["engine_card"]["alpha_beta_breakdown"] = {
        "by_market": {
            "TW": {"bench": "^TWII", "port_tot": 0.20, "spy_tot": 0.10,
                   "excess_vs_spy": 0.10, "beta": 1.10},
            "US": {"bench": "SPY", "port_tot": 0.05, "spy_tot": 0.08,
                   "excess_vs_spy": -0.03, "beta": 0.80},
        },
    }
    markdown = card_renderer.render_private(bundle)
    html = card_renderer.render_html(bundle)

    block1 = _markdown_block(markdown, "zh-TW", "performance")
    lines = block1.splitlines()
    tw_at = next(i for i, x in enumerate(lines) if x == "[TW]")
    us_at = next(i for i, x in enumerate(lines) if x == "[US]")
    assert tw_at < us_at, "TW's cluster must precede US's (MARKET_BENCHMARKS order)"
    assert lines[tw_at + 1].startswith("- ") and "TW 部位對 ^TWII" in lines[tw_at + 1], \
        "the TW benchmark line must follow its [TW] label, bulleted"
    assert lines[us_at + 1].startswith("- ") and "US 部位對 SPY" in lines[us_at + 1], \
        "the US benchmark line must follow its [US] label, bulleted"

    block1_html = html.split("<h2>", 2)[1]
    assert block1_html.count('<p class="panel-label">[TW]</p>') == 1
    assert block1_html.count('<p class="panel-label">[US]</p>') == 1
    tw_label_at = block1_html.index('<p class="panel-label">[TW]</p>')
    us_label_at = block1_html.index('<p class="panel-label">[US]</p>')
    assert block1_html[tw_label_at:us_label_at].count("<ul>") == 1, \
        "TW's benchmark line must render inside one shared <ul>, right after its [TW] label"
    assert block1_html[us_label_at:].split("<p>", 1)[0].count("<ul>") == 1, \
        "US's benchmark line must render inside its own <ul>, right after its [US] label"

    # Single-market cards (every other test's fixture, and the common case)
    # render no grouping label or bullet at all — there is only one market
    # to show.
    single_market = _rich_bundle("zh-TW")  # alpha_beta_breakdown.bench == "SPY", no by_market
    single_md = card_renderer.render_private(single_market)
    single_html = card_renderer.render_html(single_market)
    assert "[TW]" not in single_md and "[US]" not in single_md, \
        "a single-market card has nothing to disambiguate; no label should appear"
    assert "panel-label\">[TW]" not in single_html and "panel-label\">[US]" not in single_html
    # Scoped to Block 1: Block 2's ETF facts legitimately render their own
    # unrelated <ul> (existing "bullets" kind, untouched by this change) —
    # this assertion is about the vs-market line specifically, not the card.
    single_block1_html = single_html.split("<h2>", 2)[1]
    assert "<ul>" not in single_block1_html, \
        "a single-market card must not bullet its (ungrouped) vs-market line either"


# #279 i18n phase 1: the engine now emits stable codes + raw params for tags,
# the stress scenario, and prescriptions. Shaped exactly like the new
# trade_recap output; semantic values mirror _RICH_CARD_FIELDS so the zh
# resolution can be compared byte-for-byte against the legacy literals.
_RICH_CARD_FIELDS_CODED = {
    "ticker_diagnosis": [
        {"ticker": "PLTR", "impact": 76647.0,
         "tags": [{"code": "too_heavy", "params": {"wpct": 0.49}}]},
        {"ticker": "NVDA", "impact": 58524.0,
         "tags": [{"code": "disciplined_hold", "params": {"cur": 1.50}}]},
        {"ticker": "AMD", "impact": -1000.0,
         "tags": [{"code": "roughly_neutral", "params": {}}]},
    ],
    "what_if": {"scenario": {"kind": "ai_thematic"}, "mval": 170963.0, "pct": 0.983,
                "drop30": 51289.0, "drop50": 85482.0},
    "prescriptions": [
        {"code": "cut_averaging_down", "kind": "cut_loss", "dim": "加碼攤平",
         "params": {"count": 12}, "verify": "虧損加碼次數(降→好)"},
        {"code": "cut_oversize", "kind": "cut_loss", "dim": "部位 sizing",
         "params": {"ticker": "PLTR", "max_pct": 0.49}, "verify": "單筆最大佔比(降→好)"},
    ],
    "alpha_beta_breakdown": copy.deepcopy(_RICH_CARD_FIELDS["alpha_beta_breakdown"]),
}


def _coded_bundle(language):
    bundle = copy.deepcopy(_session(language)["bundle"])
    bundle["engine_card"].update(copy.deepcopy(_RICH_CARD_FIELDS_CODED))
    return bundle


def test_coded_fields_resolve_zh_byte_identical_to_legacy_literals():
    """#279: for the fields whose legacy literals and coded forms describe the
    same facts (tags, stress), the zh card must render byte-identically from
    either shape — the copy templates inherit the engine wording verbatim.
    Prescriptions are excluded here because the legacy fixture's rows are
    synthetic strings, not engine output; their coded resolution is asserted
    in the English-card test below and in the zh tokens it shares."""
    legacy = _rich_bundle("zh-TW")
    coded = _coded_bundle("zh-TW")
    for bundle in (legacy, coded):
        bundle["engine_card"]["prescriptions"] = []
    assert card_renderer.render_private(legacy) == card_renderer.render_private(coded), \
        "coded tags/stress must resolve to the exact legacy zh wording (Markdown)"
    assert card_renderer.render_html(legacy) == card_renderer.render_html(coded), \
        "coded tags/stress must resolve to the exact legacy zh wording (HTML)"


def test_coded_fields_resolve_zh_prescriptions_from_copy():
    """#279 i18n: coded prescription rows resolve to the zh copy wording.

    Asserted on ``localized_prescription`` directly rather than through the
    card: #301 stopped rendering cut_loss rows on the v2 card (the committed
    rule carries them), while ``rich_card.py`` still resolves the full
    prescription layer through this same function."""
    rows = [{"code": "cut_averaging_down", "kind": "cut_loss", "params": {"count": 12}},
            {"code": "cut_oversize", "kind": "cut_loss",
             "params": {"ticker": "PLTR", "max_pct": 0.49}}]
    resolved = [card_renderer.localized_prescription(row, "zh-TW") for row in rows]
    assert resolved[0]["kind"] == "砍損耗"
    assert resolved[0]["text"] == "虧損中加碼 12 次是你操盤損耗的大宗——這是最該先砍的純扣分動作。"
    assert resolved[1]["text"] == "最大一筆 PLTR 佔 49%,單一押注過重。"


def test_coded_fields_render_localized_english_blocks():
    """#279 acceptance: the en card gains the stress line, the strength claim,
    and instrument behavior tags from copy/en.json — with zero zh leakage.

    The amplify row is the prescription content that still reaches the card
    after #301; it renders beside the Block-3 strength, not in Next step."""
    bundle = _coded_bundle("en")
    bundle["engine_card"]["prescriptions"] = list(
        bundle["engine_card"]["prescriptions"]) + [
        {"code": "amplify_selection_edge", "kind": "amplify", "params": {"selection": 1.81}}]
    html = card_renderer.render_html(bundle)
    markdown = card_renderer.render_private(bundle)
    for surface, name in ((html, "HTML"), (markdown, "Markdown")):
        for token in ("too heavy: 49% of the portfolio",
                      "disciplined hold: +150%",
                      "roughly neutral",
                      "could you sit through that?",
                      "in-sector stock selection still contributed +181pp"):
            assert token in surface, f"missing from en {name}: {token}"
        for zh_token in ("押太重", "紀律持有", "大致中性", "撐得住嗎", "砍損耗", "揚長"):
            assert zh_token not in surface, f"zh vocabulary leaked into en {name}: {zh_token}"
    # Markdown-only joins: halfwidth punctuation on the en card.
    assert "(too heavy: 49% of the portfolio)" in markdown, \
        "en Markdown must join tags with halfwidth punctuation"


def test_instrument_tag_price_note_stays_inline_without_growing_the_row():
    """#347 acceptance: current price / average cost ride inside the same tag
    string as cur_ret's percentage, on disciplined_hold / suspected_averaging
    _down_losing / deep_underwater. Owner's binding 2026-07-22 constraint: the
    key-trades row must not grow from one line to two, and must not gain an
    indent level. Compare a tag carrying price data against the identical tag
    without it: Markdown line count and HTML element counts must be
    unchanged, and the only textual diff must be the inline parenthetical —
    proof this is an appendage to the existing tag, not a restructuring."""
    for language, fragment in (("zh-TW", "(現 150.20／均 68.30)"),
                              ("en", " (now 150.20 / cost 68.30)")):
        def bundle_with(params):
            b = _coded_bundle(language)
            b["engine_card"]["ticker_diagnosis"] = [
                {"ticker": "NVDA", "impact": 58524.0,
                 "tags": [{"code": "disciplined_hold", "params": params}]},
                {"ticker": "PLTR", "impact": 76647.0,
                 "tags": [{"code": "too_heavy", "params": {"wpct": 0.49}}]},
            ]
            return b

        bare = bundle_with({"cur": 1.50})
        priced = bundle_with({"cur": 1.50, "px": 150.20, "avg_cost": 68.30})
        bare_md, priced_md = card_renderer.render_private(bare), card_renderer.render_private(priced)
        bare_html, priced_html = card_renderer.render_html(bare), card_renderer.render_html(priced)

        assert fragment in priced_md and fragment in priced_html, \
            f"{language}: price/cost fragment missing from a surface"
        assert fragment not in bare_md and fragment not in bare_html, \
            f"{language}: fragment must not appear without px/avg_cost params"
        assert bare_md.count("\n") == priced_md.count("\n"), \
            f"{language}: adding price/cost changed the Markdown line count"
        assert priced_md.replace(fragment, "") == bare_md, \
            f"{language}: the only Markdown diff must be the inline fragment"
        for cls in ('<div class="trow">', '<p class="rsub">', '<div class="track">'):
            assert bare_html.count(cls) == priced_html.count(cls), \
                f"{language}: {cls} count changed — price/cost must not add HTML structure"
        assert priced_html.replace(fragment, "") == bare_html, \
            f"{language}: the only HTML diff must be the inline fragment"


def test_preview_emits_html_and_finalize_cleans_pending():
    for language in ("zh-TW", "en"):
        run = _session(language)
        paths = run["preview_payload"].get("paths") or {}
        assert run["preview_html_path"], "preview must emit private_card_html_path"
        assert paths.get("card-private-preview.html") == run["preview_html_path"]
        assert run["preview_html_path"].endswith("card-private-preview.html")
        assert run["pending_existed"], "pending session directory must exist at preview"
        assert run["preview_html"] and '<div class="rc">' in run["preview_html"], \
            "pending preview HTML must be the structured card"
        assert not run["pending_exists_after_finalize"], \
            "finalize must clean the pending previews, HTML included"


def test_card_template_is_deorphaned():
    template = (SKILL / "card-template.html").read_text(encoding="utf-8")
    assert "SKILL.md Step 3" not in template, \
        "card-template.html must not claim a SKILL.md section that no longer exists"
    assert "card_renderer" in template and "render_html" in template, \
        "card-template.html must point at the runtime rendering truth"


def test_sparkline_is_failsoft_on_wrong_typed_curve():
    """A decorative curve field must never crash render_html: adapter or
    --card-json inputs can carry any JSON shape, and the Markdown card
    tolerates them, so the HTML card must too (review of #225)."""
    run = _session("zh-TW")
    for bad in ({"points": "n/a"}, {"points": ["2026", 0.1]}, {"points": {}},
                [], "note", {"points": [None, 3]}, {"points": [{"cum_ret": "x"}]}):
        bundle = copy.deepcopy(run["bundle"])
        bundle["engine_card"]["pnl_curve"] = bad
        html = card_renderer.render_html(bundle)  # must not raise
        assert "<svg" not in html, f"malformed curve {bad!r} must omit the sparkline"


def test_sparkline_tone_treats_negative_zero_as_loss():
    run = _session("zh-TW")
    bundle = copy.deepcopy(run["bundle"])
    bundle["engine_card"]["pnl_curve"] = {"points": [{"cum_ret": -0.02},
                                                     {"cum_ret": -0.0}]}
    html = card_renderer.render_html(bundle)
    assert 'class="spark neg"' in html, "a -0.0 final return must render as a loss"


def test_rule_grounding_sub_line_private_surfaces_only():
    """#248: the chosen candidate's engine-authored grounding renders as a
    muted sub-line under the committed rule on BOTH private surfaces (one
    _card_structure source), and never reaches the share-safe public card."""
    for language in ("zh-TW", "en"):
        run = _session(language)
        grounding = (run["bundle"].get("commitment") or {}).get("grounding")
        assert grounding and "PLTR" in grounding, \
            "the chosen candidate must carry its engine-authored grounding"
        assert grounding in run["markdown"], "grounding sub-line missing from the Markdown card"
        assert 'class="rground"' in run["html"], "HTML grounding must use the muted rule sub-line class"
        assert grounding in run["html"], "grounding sentence missing from the HTML card"
        public_md = card_renderer.render_public(run["bundle"])
        assert grounding not in public_md and "PLTR" not in public_md, \
            "grounding must never reach the share-safe public card"


def test_resume_exposes_preview_html_path_not_blob():
    """After preview, resume must surface the styled preview by the same
    private_card_html_path key the delivery contract names, and must not dump
    the HTML content into stdout."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "demo-root"
        card, state = _artifacts_with_curve(tmp)
        prepared = v2._run("prepare", "--test-drive", "--root", root,
                           "--card-json", card, "--state-json", state, "--language", "en")
        plan = json.loads(prepared.stdout)["review_plan"]
        answers = pathlib.Path(tmp) / "a.json"
        narrative = pathlib.Path(tmp) / "n.json"
        answers.write_text(json.dumps(v2._answers(plan), ensure_ascii=False), encoding="utf-8")
        narrative.write_text(json.dumps(v2._narrative("en"), ensure_ascii=False), encoding="utf-8")
        v2._run("preview", "--root", root, "--session-id", plan["session_id"],
                "--answers", answers, "--narrative", narrative)
        resumed = v2._run("resume", "--root", root, "--session-id", plan["session_id"])
        payload = json.loads(resumed.stdout)
        assert payload.get("private_card_html_path", "").endswith("card-private-preview.html")
        assert "card-private-preview-html" not in payload, \
            "resume must not surface the undocumented mangled key"
        assert "<div class=\"rc\">" not in json.dumps(payload), \
            "resume must not dump the HTML blob into stdout"


def _copy_key_paths(node, prefix=""):
    paths = set()
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(path)
            paths |= _copy_key_paths(value, path)
    return paths


def test_locale_copy_files_keep_key_parity():
    """#279: every locale ships the same key set (recursively), so a renderer
    resolution that works in one locale can never silently miss in another."""
    locales = {}
    for name in ("en", "zh-TW"):
        with open(SKILL / "copy" / f"{name}.json", encoding="utf-8") as f:
            locales[name] = _copy_key_paths(json.load(f))
    missing_in_zh = locales["en"] - locales["zh-TW"]
    missing_in_en = locales["zh-TW"] - locales["en"]
    assert not missing_in_zh and not missing_in_en, \
        f"copy key parity broken; missing in zh-TW: {sorted(missing_in_zh)}; " \
        f"missing in en: {sorted(missing_in_en)}"


# `test_reconciliation_statement_copy_is_pinned_in_rendered_output` moved to
# `tests/copy_corpus.py` (#402 knife 5), which pins both the metric and the
# plain form on all three surfaces from a bundle small enough to read.


def test_snapshot_overview_and_strength_copy_is_pinned_in_rendered_output():
    """#368 Phase 2 batch 2 mutation-probe finding: corrupting
    snapshot.overview.* or snapshot.strength.* survives both
    tests/persona_sweep.py --baseline (every mock persona is a
    transaction-history CSV; none takes the snapshot_review route the sweep
    would need to reach these functions) and tests/run_all.py
    (test_review_v2.test_snapshot_card_states_scope_once_and_leads_with_both_structural_holes
    asserts other markers on this same fixture shape -- the honesty scope
    sentence, the unlock hint, hole ticker/pct figures -- and never the
    opening/valuation/strength sentences themselves). Reuses that test's
    concentrated-snapshot payload and pins the exact, copy-resolved
    sentences it renders but does not check."""
    payload = {
        "as_of": "2026-07-20",
        "positions": [
            {"ticker": "NVDA", "shares": 40, "avg_cost": 152.3, "market": "US",
             "currency": "USD", "market_value": 6800},
            {"ticker": "PLTR", "shares": 200, "avg_cost": 18.5, "market": "US",
             "currency": "USD", "market_value": 4200},
            {"ticker": "SPY", "shares": 10, "avg_cost": 500, "market": "US",
             "currency": "USD", "market_value": 5300},
            {"ticker": "2330.TW", "shares": 1000, "avg_cost": 900, "market": "TW",
             "currency": "TWD", "market_value": 985000},
        ],
        "fx": {"USD": 1, "TWD": 0.0307},
    }
    with tempfile.TemporaryDirectory() as tmp:
        for language in ("zh-TW", "en"):
            root = pathlib.Path(tmp) / f"coach-{language}"
            plan, _path = v2._snapshot_prepare(
                tmp, root, payload=payload, language=language,
                name=f"positions-{language}.json")
            assert plan["engine_card"]["snapshot_summary"]["weights_available"] is True, \
                "fixture must keep weights available to exercise the market-value sentence"
            bundle = v2._snapshot_render_bundle(plan, language)
            # Hardcoded, not read from copy/*.json -- see the
            # reconciliation pin test above for why.
            snap_templates = {
                "zh-TW": {"subject_with_count": '使用者提供的 {positions} 個持倉',
                          "opening_as_of": '這是針對{subject}的開場組合檢查，快照截至 {as_of}。',
                          "valuation_market_value": '結構權重採使用者提供的市值口徑。',
                          "strength_weighted": '已用你提供的持倉建立組合結構基線，並據此評分權重；缺少的輸入維持明示，不用推測補齊。'},
                "en": {"subject_with_count": '{positions} supplied positions',
                       "opening_as_of": 'This is an opening portfolio check of {subject} as of {as_of}.',
                       "valuation_market_value": 'Structural weights use the supplied market-value basis.',
                       "strength_weighted": 'The supplied positions establish a structural baseline, and their weights are scored; missing inputs remain explicit rather than inferred.'},
            }[language]
            subject = snap_templates["subject_with_count"].format(positions="4")
            expected_opening = snap_templates["opening_as_of"].format(
                subject=subject, as_of="2026-07-20")
            expected_valuation = snap_templates["valuation_market_value"]
            # #549: the third branch of `_snapshot_strength_line` is gone with
            # the completeness flag. What is left is what the engine can see --
            # the supplied facts either supported weights or they did not -- and
            # this fixture supplies market values, so it takes the weighted one.
            expected_strength = snap_templates["strength_weighted"]
            markdown = card_renderer.render_private(bundle)
            html_card = html.unescape(card_renderer.render_html(bundle))
            for expected, label in ((expected_opening, "opening"),
                                    (expected_valuation, "valuation"),
                                    (expected_strength, "strength")):
                assert expected in markdown, \
                    f"{language}: snapshot {label} sentence missing/altered on Markdown"
                assert expected in html_card, \
                    f"{language}: snapshot {label} sentence missing/altered on HTML"

        # A well-diversified snapshot (weights available, nothing triggered)
        # takes the sibling branch of _snapshot_hole_lines -- pin that too.
        clean_payload = {
            "as_of": "2026-07-20",
            "positions": [
                {"ticker": t, "shares": 10, "avg_cost": 100, "market": "US",
                 "currency": "USD", "market_value": 2000}
                for t in ("MSTR", "HOOD", "CAVA", "MP", "ONDS", "NOK")
            ],
        }
        clean_root = pathlib.Path(tmp) / "coach-clean"
        clean_plan, _path = v2._snapshot_prepare(
            tmp, clean_root, payload=clean_payload, language="en", name="clean.json")
        assert clean_plan["engine_card"]["top_holes"] == [], \
            "fixture must be clean (no structural dimension triggered)"
        clean_bundle = v2._snapshot_render_bundle(clean_plan, "en", session_id="clean")
        clean_markdown = card_renderer.render_private(clean_bundle)
        # Hardcoded, not read from copy/en.json -- see above.
        expected_clean_structure = 'This position snapshot did not flag concentration or diversification as a structural risk.'
        assert expected_clean_structure in clean_markdown, \
            "clean-structure snapshot sentence missing/altered on Markdown"


# `test_best_strength_no_signal_copy_is_pinned_in_rendered_output` moved to
# `tests/copy_corpus.py` (#402 knife 5). The corpus pins all six strength
# sentences, not only the fallback: `_best_strength` renders whichever
# candidate ranks first, so the five per-dimension sentences need one scene
# each and none of them had a pin before.


def test_alpha_below_grid_notes_are_two_independent_triggers():
    """#363: the not-yet-credible legend and the negative-interval caveat fire
    on separate conditions, and the tile's `*` suffix tracks `credible` alone.

    This test owns the *branching*; `tests/copy_corpus.py` owns the sentences.
    Before #402 knife 5 it owned both, and the three templates below were a
    hand-copied duplicate of `copy/*.json` — the only way to catch a catalog
    corruption when no other check read those keys. The corpus pins their bytes
    against a generated golden now, which is what makes reading the needles
    back from `load_copy()` safe here: a broken catalog reddens the corpus, so
    this test is free to ask only which branch fired."""
    alpha_copy = {language: card_renderer.load_copy(language)["alpha_interval"]
                  for language in ("zh-TW", "en")}
    # (credible, ci95, expect_unreliable, expect_negative, low_text, high_text)
    cases = [
        ("both_clean", True, [0.07, 0.54], False, False, "+7", "+54"),
        ("negative_only", True, [-0.05, 0.74], False, True, "-5", "+74"),
        ("unreliable_only", False, [0.05, 0.74], True, False, "+5", "+74"),
        ("both_triggered", False, [-0.10, 0.74], True, True, "-10", "+74"),
    ]
    for label, credible, ci95, expect_unreliable, expect_negative, low, high in cases:
        for language in ("zh-TW", "en"):
            bundle = _rich_bundle(language)
            ab = bundle["engine_card"]["alpha_beta_breakdown"]
            ab["credible"] = credible
            ab["alpha_stat"] = {"alpha_ann": ab["alpha_ann"], "ci95": ci95}
            html_card = html.unescape(card_renderer.render_html(bundle))
            markdown = card_renderer.render_private(bundle)

            expected_sub = alpha_copy[language]["tile_sub"].format(low=low, high=high)
            assert expected_sub in html_card, \
                f"{label}/{language}: alpha tile sub must carry the interval: {expected_sub!r}"
            # The tile value's own " *" suffix logic is unchanged by #363 (it
            # is driven by `credible` alone, independent of the sub) — pin it
            # here too, since a regression that swapped the two conditions
            # would otherwise slip past every assertion above.
            expected_value = "+33% *" if not credible else "+33%"
            assert f'<p class="val">{expected_value}</p>' in html_card, \
                f"{label}/{language}: alpha tile value's \"*\" suffix must track `credible` alone"

            note_unreliable = alpha_copy[language]["below_unreliable"]
            note_negative = alpha_copy[language]["below_negative"]
            assert (note_unreliable in html_card) == expect_unreliable, \
                f"{label}/{language}: below_unreliable trigger mismatch"
            assert (note_negative in html_card) == expect_negative, \
                f"{label}/{language}: below_negative trigger mismatch"
            if not expect_unreliable and not expect_negative:
                # #363: the point of the change -- a credible, wholly-positive
                # card prints no alpha prose line below the grid at all.
                assert note_unreliable not in html_card and note_negative not in html_card

            # Markdown has no tile: it must still carry the complete original
            # sentence (headline + interval + caveat), untouched by any of
            # this -- the existing kpi_id/html_text mechanism, not a special
            # case. The tile-sized short form must NOT be what Markdown shows.
            assert expected_sub not in markdown, \
                f"{label}/{language}: Markdown must not carry the tile-sized short form"
            if expect_negative:
                assert "區間包含負值" in markdown or "includes a negative value" in markdown, \
                    f"{label}/{language}: Markdown must keep its own full negative_caveat clause"


def test_pnl_and_payoff_tile_subs_fit_the_two_line_cap_at_narrow_widths():
    """#382: `.sub`'s `-webkit-line-clamp:2; overflow:hidden` silently
    truncated the pnl and payoff tiles' realized/unrealized and win/loss
    breakdown at 5-6 digit magnitudes -- the alpha caveat #363 fixed (see
    the mutation-probe test above) was one of three clipped tiles that
    day, not the only one, because the fixtures every existing test drew
    on used 3-digit amounts (`avg_win: 140`, `total_pnl: -300`) short
    enough to never hit the cap.

    Measured with a real viewport resize, not `--window-size` (this
    Chrome build floors that flag around 500px, the same trap #363's
    ruling-log entry recorded, too coarse for the 320-390px band this
    tile actually clips in): both new templates, at the large-magnitude
    values below (the README's own headline figures -- #382 "these are
    the demo card's own magnitudes"), clear `scrollHeight<=clientHeight`
    *and* `scrollWidth<=clientWidth` (a single long token can silently
    overflow the box sideways instead of wrapping, which a height-only
    check misses) at every 2-column breakpoint tested: en 320/360/390px,
    zh-TW 320/360px -- the narrowest width each locale was reported
    broken at in the issue. zh-TW keeps both "already realized"/"not yet
    realized" words at full sentence structure once their figures compact
    to "k" (denser glyphs afford it at the same width); en needed the
    leading "realized" word dropped too -- the number stays, first and
    unlabeled, since the realized/unrealized order convention is already
    established elsewhere in this catalog (`pnl_lines.total`) -- while
    "unrealized" keeps its explicit label, since that is the figure a
    caveat actually attaches to (README lede: "almost all of it is held
    and never sold").

    Hardcoded literals, deliberately -- see
    test_account_gate_sentences_are_pinned_in_rendered_output above for why
    a version reading expectations back from load_copy() would compare the
    catalog against itself and stay green through a corruption."""
    large = {"total_pnl": 138058.0, "realized": 19000.0, "unrealized": 119058.0,
             "avg_win": 20000.0, "avg_loss": -8000.0, "payoff": 2.5}
    negative_unrealized = {"total_pnl": -100058.0, "realized": 19000.0, "unrealized": -119058.0,
                           "avg_win": 20000.0, "avg_loss": -8000.0, "payoff": 2.5}
    # (case, overrides) x language -> (expected pnl sub, expected payoff sub).
    # "small" is the unmodified _rich_bundle fixture (realized 200 / unrealized
    # -500 / avg_win 140 / avg_loss -100): below the $10,000 compaction
    # threshold, so it must render full precision, same as before #382 --
    # proof the fix does not misstate a small account's real number as "$0k".
    cases = (("large", large), ("small", {}), ("negative_unrealized", negative_unrealized))
    expected = {
        "en": {
            "large": ("+$19k, +$119k unrealized", "+$20,000 win vs $8,000 loss"),
            "small": ("+$200, -$500 unrealized", "+$140 win vs $100 loss"),
            "negative_unrealized": ("+$19k, -$119k unrealized", "+$20,000 win vs $8,000 loss"),
        },
        "zh-TW": {
            "large": ("已實現 +$19k · 未實現 +$119k", "賺 +$20,000 vs 賠 $8,000"),
            "small": ("已實現 +$200 · 未實現 -$500", "賺 +$140 vs 賠 $100"),
            "negative_unrealized": ("已實現 +$19k · 未實現 -$119k", "賺 +$20,000 vs 賠 $8,000"),
        },
    }
    for language in ("zh-TW", "en"):
        for case, overrides in cases:
            bundle = _rich_bundle(language)
            if overrides:
                bundle["engine_card"]["overview"].update(overrides)
            # Isolate defect 2 from the already-fixed defect 1 (#363): give
            # alpha a usable ci95 so its own sub takes the short interval
            # form, not the pre-#363 fallback legend _rich_bundle otherwise
            # exercises (see the #363 mutation-probe test's docstring above)
            # -- that legend clips on its own and would confound this test.
            ab = bundle["engine_card"]["alpha_beta_breakdown"]
            ab["credible"] = True
            ab["alpha_stat"] = {"alpha_ann": ab["alpha_ann"], "ci95": [0.07, 0.54]}
            html_card = html.unescape(card_renderer.render_html(bundle))
            pnl_expected, payoff_expected = expected[language][case]
            assert f'<p class="sub">{pnl_expected}</p>' in html_card, \
                f"{language}/{case}: pnl tile sub altered — expected {pnl_expected!r}"
            assert f'<p class="sub">{payoff_expected}</p>' in html_card, \
                f"{language}/{case}: payoff tile sub altered — expected {payoff_expected!r}"

    # Markdown has no line-clamp, so its mirror of the same sentence (#344's
    # kpi_id/html_text mechanism) keeps full precision -- the HTML tile's
    # compaction is a presentation-layer concession to one narrow box, not a
    # change to what the card knows or states elsewhere.
    for language, full_precision_fragment in (
        ("en", "+$19,000, +$119,058 unrealized"),
        ("zh-TW", "已實現 +$19,000 · 未實現 +$119,058"),
    ):
        bundle = _rich_bundle(language)
        bundle["engine_card"]["overview"].update(large)
        markdown = card_renderer.render_private(bundle)
        assert full_precision_fragment in markdown, \
            f"{language}: Markdown pnl line must stay full precision, not the HTML tile's compact form"
        assert "$19k" not in markdown and "$119k" not in markdown, \
            f"{language}: Markdown must never show the HTML tile's compact ($Nk) values"


def test_delivery_contract_exists_and_is_routed():
    contract = SKILL / "references" / "card-delivery.md"
    assert contract.is_file(), "references/card-delivery.md must exist"
    text = contract.read_text(encoding="utf-8")
    assert "WIDGET-FRAGMENT-START" in text and "WIDGET-FRAGMENT-END" in text, \
        "delivery contract must name the widget-fragment markers"
    # Pin the actual fallback rule, not the mere presence of one word: the
    # terminal/graphical surfaces must both fall back to the canonical Markdown.
    fallback = re.search(r"fall back[^\n]*Markdown|Markdown card text verbatim", text)
    assert fallback, "delivery contract must keep the verbatim-Markdown fallback rule"
    assert "do not put it in a code fence" in text, \
        "the conversation fallback must leave the Markdown hierarchy renderable"
    assert "same canonical Markdown artifact" in text, \
        "CLI fallback must reuse the canonical card rather than fork its copy"
    assert "--format private-markdown" in text, \
        "delivery contract must document the direct terminal fallback command"

    assert "references/card-delivery.md" in (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "references/card-delivery.md" in (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "references/card-delivery.md" in (SKILL / "card-spec.md").read_text(encoding="utf-8")
    assert FLOW_FILES, "at least one flow file must exist to route card delivery"
    # A flow that renders no card has nothing to route through the delivery
    # contract -- light-capture (#237 #4) and book-refresh (#485 Slice C) are
    # both such flows. The exemption is read off each flow's own declaration
    # rather than kept as a filename list here: a list in the test is a
    # maintenance duty nobody sees until it is already wrong, and it would let a
    # future card-rendering flow be exempted by a one-word edit in this file
    # instead of by a visible claim in the artifact itself.
    NO_CARD_CLAIM = "There is no card"
    exempt = []
    for name in FLOW_FILES:
        flow = (SKILL / "flows" / name).read_text(encoding="utf-8")
        if NO_CARD_CLAIM in flow:
            exempt.append(name)
            assert "references/card-delivery.md" not in flow, (
                f"flows/{name} claims it renders no card and also routes card delivery")
            continue
        assert "references/card-delivery.md" in flow, f"flows/{name} must route card delivery"
    assert len(exempt) < len(FLOW_FILES), "every flow cannot be card-free"


# ── Next-step coherence (#301 #302 #303 #317) ────────────────────────────────
# A card can hold a proven strength and a concentration risk that point at the
# same position. The regression these tests guard is the card presenting both
# as peer instructions and leaving the reader to arbitrate.


def _conflicted_bundle(language, dim):
    """A card that simultaneously claims a proven selection edge and an
    oversized position — the #301 QA scenario — committed to ``dim``."""
    bundle = copy.deepcopy(_session(language)["bundle"])
    card = bundle["engine_card"]
    card.update(copy.deepcopy(_RICH_CARD_FIELDS_CODED))
    card["prescriptions"] = [
        {"code": "amplify_hypothesis", "kind": "amplify_hypothesis",
         "params": {"excess": 2.47, "allocation": 0.67}},
        {"code": "amplify_selection_edge", "kind": "amplify", "params": {"selection": 1.81}},
        {"code": "cut_oversize", "kind": "cut_loss", "dim": "部位 sizing",
         "params": {"ticker": "PLTR", "max_pct": 0.49}, "rule": "cap"},
    ]
    card["dims_raw"] = [
        {"dim": "部位 sizing", "triggered": True, "max_ticker": "PLTR", "max_pct": 0.49,
         "risk_weights": {"PLTR": 0.49, "NVDA": 0.46, "ORCL": 0.12, "AMD": 0.03}},
        {"dim": "加碼攤平", "triggered": True, "count": 12, "breach": 2,
         "tickers": ["NVDA", "PLTR"], "ticker_counts": {"PLTR": 7, "NVDA": 5}},
    ]
    card["ticker_diagnosis"] = [
        {"ticker": "PLTR", "impact": 76647.0,
         "tags": [{"code": "too_heavy", "params": {"wpct": 0.49}}]},
        {"ticker": "TSLA", "impact": 8200.0,
         "tags": [{"code": "sold_winner_early", "params": {"win_early": 3, "win_n": 4}}]},
        {"ticker": "AMD", "impact": -1000.0,
         "tags": [{"code": "sold_winner_early", "params": {"win_early": 2, "win_n": 3}}]},
    ]
    bundle["commitment"] = {
        "rule": card_renderer.localized_rule(dim, language), "dim": dim,
        "metric_key": "max_pos_pct", "goal": "down",
        "grounding": card_renderer.localized_rule_grounding(dim, language, card),
    }
    return bundle


def _next_step_text(markdown):
    """The Next-step block only, on either locale."""
    parts = re.split(r"\n## ", markdown)
    return parts[-1]


def test_next_step_renders_exactly_one_action(self_check=None):
    """#301: Block 4 issues one instruction. The strength claims move to the
    Block-3 [v] panel; no prescription row may sit beside the rule."""
    for language in ("zh-TW", "en"):
        bundle = _conflicted_bundle(language, "position_sizing")
        markdown = card_renderer.render_private(bundle)
        block4 = _next_step_text(markdown)
        assert block4.count("[*]") == 1, f"{language}: Next step must hold one rule"
        for stray in ("揚長", "Amplify the edge", "砍損耗", "Cut the leak"):
            assert stray not in block4, \
                f"{language}: prescription row leaked back into Next step: {stray}"
        # The strength claim is still on the card — relocated, not dropped.
        edge = ("這是真 edge" if language == "zh-TW"
                else "in-sector stock selection still contributed")
        assert edge in markdown, f"{language}: the strength claim must survive"
        assert edge not in block4, f"{language}: the strength claim must not sit in Next step"


def test_next_step_reconciles_a_rule_that_contradicts_a_strength():
    """#301: when the rule shrinks a position the same card credits, the card
    states the relationship instead of leaving two opposing orders."""
    for language, marker in (("zh-TW", "不跟「選股是真 edge」衝突"),
                             ("en", "does not contradict the stock-picking edge")):
        block4 = _next_step_text(card_renderer.render_private(
            _conflicted_bundle(language, "position_sizing")))
        assert marker in block4, f"{language}: missing the trade-off reconciliation"
    # A rule on an unrelated dimension gets no such sentence: an unconditional
    # one would be exactly the caveat noise this change removes.
    block4 = _next_step_text(card_renderer.render_private(
        _conflicted_bundle("zh-TW", "averaging_down")))
    assert "衝突" not in block4, \
        "the reconciliation must not fire for a rule that shrinks nothing"


def test_rule_names_the_positions_it_would_act_on():
    """#302: the rule cites this period's actual positions or behavior counts,
    at sub-line level under the rule rather than as a detached footnote.
    PLTR/NVDA (49%/46%) sit above both POSITION_CAP and OVERSIZE_TRIGGER, and
    ORCL/AMD (12%/3%) sit below both, so this fixture does not itself pin
    which of the two thresholds gates the list — see
    test_rule_targets_filter_on_the_trigger_not_the_coach_cap for that."""
    sizing = _next_step_text(card_renderer.render_private(
        _conflicted_bundle("zh-TW", "position_sizing")))
    assert "PLTR 49%" in sizing and "NVDA 46%" in sizing, \
        "the sizing rule must name every position over the trigger"
    assert "ORCL" not in sizing and "AMD" not in sizing, \
        "positions under the trigger are not what the rule would catch"
    avgdown = _next_step_text(card_renderer.render_private(
        _conflicted_bundle("zh-TW", "averaging_down")))
    assert "PLTR 7 次" in avgdown and "NVDA 5 次" in avgdown, \
        "the averaging-down rule must name per-ticker counts"
    en = _next_step_text(card_renderer.render_private(
        _conflicted_bundle("en", "position_sizing")))
    assert "PLTR 49%, NVDA 46%" in en, "en must join targets with halfwidth punctuation"


def test_rule_targets_filter_on_the_trigger_not_the_coach_cap():
    """#328: a holding between POSITION_CAP (20%, the coach's suggested target)
    and OVERSIZE_TRIGGER (25%, the diagnostic line that actually opens the
    cut_oversize prescription) was never judged a problem by any engine path.
    Listing it under "what this rule would catch" made the card stricter than
    the engine's own judgment (the literal owner-verified repro: 27/22/21)."""
    for language, over, between in (("zh-TW", "AAA 27%", ("BBB 22%", "CCC 21%")),
                                     ("en", "AAA 27%", ("BBB 22%", "CCC 21%"))):
        bundle = _conflicted_bundle(language, "position_sizing")
        bundle["engine_card"]["dims_raw"][0]["risk_weights"] = {
            "AAA": 0.27, "BBB": 0.22, "CCC": 0.21, "DDD": 0.03}
        block4 = _next_step_text(card_renderer.render_private(bundle))
        assert over in block4, \
            f"{language}: a holding above the trigger must still be named"
        for name in between:
            assert name not in block4, \
                f"{language}: {name} sits between the cap and the trigger and " \
                "must not appear — the engine never flagged it as a hole"
        assert "DDD" not in block4, f"{language}: a holding under both lines stays absent"

    # A custom single-position cap override (#324) moves the trigger and the
    # cap to the same user-set value, per effective_oversize_trigger/
    # effective_position_cap: a holding that clears the override must show,
    # one that does not must be absent, matching the standing contract that
    # an override collapses the two thresholds into one.
    bundle = _conflicted_bundle("en", "position_sizing")
    bundle["engine_card"]["dims_raw"][0]["risk_weights"] = {
        "AAA": 0.35, "BBB": 0.28, "CCC": 0.22}
    bundle["engine_state"]["max_position_pct"] = 0.30
    block4 = _next_step_text(card_renderer.render_private(bundle))
    assert "AAA 35%" in block4, "a holding above the 30% override must show"
    assert "BBB" not in block4 and "CCC" not in block4, \
        "holdings below a 30% override must not show, even though both clear the universal 25% default"


def test_rule_targets_truncate_past_the_display_limit():
    """#349: past RULE_TARGETS_DISPLAY_LIMIT (4) named entries, the targets
    line reads as a raw data dump rather than a point of view (owner dogfood
    finding). The remainder collapses into one localized "+N more" tail, and
    the items shown are still the top-impact ones (already ranked upstream)."""
    weights = {"AAA": 0.40, "BBB": 0.35, "CCC": 0.30, "DDD": 0.29,
               "EEE": 0.28, "FFF": 0.26}
    for language, tail in (("zh-TW", "、及其他 2 檔"), ("en", ", and 2 more")):
        bundle = _conflicted_bundle(language, "position_sizing")
        bundle["engine_card"]["dims_raw"][0]["risk_weights"] = weights
        block4 = _next_step_text(card_renderer.render_private(bundle))
        for shown in ("AAA 40%", "BBB 35%", "CCC 30%", "DDD 29%"):
            assert shown in block4, f"{language}: top-impact entry {shown} must still be named"
        for hidden in ("EEE 28%", "FFF 26%"):
            assert hidden not in block4, \
                f"{language}: {hidden} is past the display limit and must fold into the tail"
        assert tail in block4, f"{language}: overflow must render as the localized '+N more' tail"

    # Exactly at the limit: no overflow, no tail, every entry named.
    bundle = _conflicted_bundle("en", "position_sizing")
    bundle["engine_card"]["dims_raw"][0]["risk_weights"] = {
        "AAA": 0.40, "BBB": 0.35, "CCC": 0.30, "DDD": 0.26}
    block4 = _next_step_text(card_renderer.render_private(bundle))
    assert all(t in block4 for t in ("AAA 40%", "BBB 35%", "CCC 30%", "DDD 26%")), \
        "exactly four entries must all be named"
    assert "more" not in block4, "four entries (the limit) must not trigger the overflow tail"

    # The truncation is generic across dims/kinds, not special-cased to pct:
    # averaging_down's "count" items must also fold past the limit.
    bundle = _conflicted_bundle("zh-TW", "averaging_down")
    bundle["engine_card"]["dims_raw"][1]["ticker_counts"] = {
        "AAA": 9, "BBB": 8, "CCC": 7, "DDD": 6, "EEE": 5}
    block4 = _next_step_text(card_renderer.render_private(bundle))
    for shown in ("AAA 9 次", "BBB 8 次", "CCC 7 次", "DDD 6 次"):
        assert shown in block4, f"averaging_down: {shown} must still be named"
    assert "EEE" not in block4, "averaging_down: the fifth ticker must fold into the tail"
    assert "、及其他 1 檔" in block4, "averaging_down: overflow tail must use the same copy contract"


def test_renderer_oversize_trigger_matches_the_engine_constant():
    """#328: the renderer keeps its own stdlib copy of the trigger, same
    reason and same boundary as POSITION_CAP. Pin them together."""
    import trade_recap  # engine path already on sys.path via test_review_v2
    assert card_renderer.OVERSIZE_TRIGGER == trade_recap.OVERSIZE_TRIGGER, \
        "card_renderer.OVERSIZE_TRIGGER and trade_recap.OVERSIZE_TRIGGER must stay in sync"


def test_exit_opportunity_cost_collects_into_one_read_only_panel():
    """#303: the scattered sold-winner tags become one [?] panel that names the
    instruments and says outright that no answer is expected."""
    for language, label, ticker in (("zh-TW", "不用回答", "TSLA 3/4"),
                                    ("en", "no answer needed", "TSLA 3/4")):
        markdown = card_renderer.render_private(_conflicted_bundle(language, "position_sizing"))
        risks = re.split(r"\n## ", markdown)[-2]
        assert "[?]" in risks, f"{language}: the pattern panel belongs in Risks and problems"
        assert label in risks, f"{language}: the panel must state that no answer is expected"
        assert ticker in risks, f"{language}: the panel must name the instruments"
        assert "[?]" not in _next_step_text(markdown), \
            f"{language}: an unjudged pattern is not a next step"


def test_exit_opportunity_cost_is_no_longer_scattered_across_key_trades():
    """#303: after consolidation the ``sold_winner_early`` tag no longer renders
    per-trade in Key trades — it lives only in the [?] panel. #326 added the
    panel but left the per-trade tags (``instrument_tags.sold_winner_early``);
    this completes the de-scatter, so the tag must be absent from every row."""
    for language, scattered in (("zh-TW", "賣後機會成本"),
                                ("en", "kept rising after the sell")):
        markdown = card_renderer.render_private(_conflicted_bundle(language, "position_sizing"))
        key_trades = next(block for block in re.split(r"\n## ", markdown)
                          if block.startswith(("關鍵交易", "Key trades")))
        assert scattered not in key_trades, \
            f"{language}: the exit tag must not sit scattered in a Key-trades row"
        assert "TSLA" in key_trades and "AMD" in key_trades, \
            f"{language}: the instrument rows themselves still render"
        # The fact survives once, consolidated, in the [?] panel.
        assert "[?]" in markdown and "TSLA 3/4" in markdown, \
            f"{language}: the consolidated observation must still carry the fact"


def test_exit_consistency_panel_yields_to_the_question_when_asked():
    """#303: when the review queues the answerable exit-consistency question, the
    read-only [?] observation panel is suppressed — the card must not say "no
    answer needed" about a pattern the user was just asked to explain."""
    asked = {"question_queue": [{"id": "exit_consistency", "kind": "exit_consistency",
                                 "required": True, "question": "q", "options": []}]}
    for language in ("zh-TW", "en"):
        without = _conflicted_bundle(language, "position_sizing")
        assert "[?]" in card_renderer.render_private(without), \
            f"{language}: the panel shows as an observation when nothing was asked"
        with_question = _conflicted_bundle(language, "position_sizing")
        with_question["review_plan"] = asked
        assert "[?]" not in card_renderer.render_private(with_question), \
            f"{language}: the observation panel must yield to the queued question"


def test_committed_rule_carries_its_threshold():
    """#317: the sizing rule prints the cap, so the reader is not left trying to
    remember what "the cap" was. The value tracks the renderer constant."""
    cap = f"{card_renderer.POSITION_CAP:.0%}"
    for language in ("zh-TW", "en"):
        block4 = _next_step_text(card_renderer.render_private(
            _conflicted_bundle(language, "position_sizing")))
        assert cap in block4, f"{language}: the rule must state its threshold"


def test_renderer_position_cap_matches_the_engine_constant():
    """The renderer keeps its own copy of the cap so it stays stdlib-only (same
    reason coach.py duplicates CYCLE_ID_RE). Pin them together."""
    import trade_recap  # engine path already on sys.path via test_review_v2
    assert card_renderer.POSITION_CAP == trade_recap.POSITION_CAP, \
        "card_renderer.POSITION_CAP and trade_recap.POSITION_CAP must stay in sync"


def test_committed_rule_carries_the_user_cap_override():
    """#324: the committed sizing rule prints the user's standing cap override in
    both locales, replacing #326's universal default. The default path and any
    invalid (fail-closed) override still show the universal POSITION_CAP."""
    universal = f"{card_renderer.POSITION_CAP:.0%}"
    for language in ("zh-TW", "en"):
        # Interpolation resolves the effective cap.
        assert universal in card_renderer.localized_rule("position_sizing", language), \
            f"{language}: no override keeps the universal cap"
        assert universal in card_renderer.localized_rule("position_sizing", language, cap=1.5), \
            f"{language}: an out-of-range override is fail-closed to the universal cap"
        overridden = card_renderer.localized_rule("position_sizing", language, cap=0.30)
        assert "30%" in overridden and universal not in overridden, \
            f"{language}: a valid override replaces the default in the rule text"
        # End-to-end: the committed rule the private card prints carries it.
        bundle = _conflicted_bundle(language, "position_sizing")
        bundle["commitment"]["rule"] = card_renderer.localized_rule(
            "position_sizing", language, cap=0.30)
        block4 = _next_step_text(card_renderer.render_private(bundle))
        assert "30%" in block4, f"{language}: committed rule must show the user's cap"


def test_public_committed_rule_carries_the_user_cap_override():
    """#324: the share-safe public card re-derives the canonical rule text (it
    must never echo the user-authored commitment rule), so — like the private
    card — it has to thread the standing single-position cap override itself. The
    override rides ``engine_state.max_position_pct`` (render_public receives only
    the bundle, not state); an unset or fail-closed cap still shows the universal
    POSITION_CAP. The public rule is the last ``##`` section, so
    ``_next_step_text`` scopes the assertion to it."""
    universal = f"{card_renderer.POSITION_CAP:.0%}"
    for language in ("zh-TW", "en"):
        bundle = _conflicted_bundle(language, "position_sizing")
        # A candidate-origin commitment is the only path that re-derives the rule;
        # a custom rule is replaced wholesale and never carries a cap.
        bundle["commitment"]["origin"] = "candidate"
        state = bundle["engine_state"] = dict(bundle.get("engine_state") or {})
        # No override on the state → the universal cap, same as the private card.
        state.pop("max_position_pct", None)
        default_rule = _next_step_text(card_renderer.render_public(bundle))
        assert universal in default_rule, \
            f"{language}: no override keeps the universal cap on the public card"
        # A valid standing override replaces the universal default in the public
        # rule text, matching the private card's committed rule.
        state["max_position_pct"] = 0.30
        overridden = _next_step_text(card_renderer.render_public(bundle))
        assert "30%" in overridden and universal not in overridden, \
            f"{language}: the user's cap must reach the public card's rule"
        # An out-of-range override is fail-closed back to the universal cap.
        state["max_position_pct"] = 1.5
        fail_closed = _next_step_text(card_renderer.render_public(bundle))
        assert universal in fail_closed, \
            f"{language}: an invalid override is fail-closed on the public card"


def _pre_commitment_bundle(language, rule_dim="部位 sizing", prior_commitment=None):
    """A pre-commitment preview bundle: the engine prescribed a rule but the
    user has not chosen one yet, so Block 4 falls to the placeholder branch.
    ``engine_state.rule`` carries the v1-only zh literal exactly as
    ``trade_recap.prescribe`` writes it, and ``rule_dim`` its legacy label.

    Default (``prior_commitment=None``) is a genuine first review: nothing was
    ever persisted to ``review_plan.state_snapshot.prior_commitment``, so
    (#546) Block 4 must use the pending-choice wrapper rather than claim a
    standing rule "remains". Pass a dict to build a returning user's preview
    instead — the same ``prior_commitment.rule`` predicate
    ``_reconciliation_lines`` already reads for #292. #645: the continuity
    wrapper additionally requires that dict to carry a ``dim`` naming the same
    dimension as ``rule_dim``, so a returning fixture that wants the standing
    copy passes e.g. ``{"rule": "...", "dim": "position_sizing"}``."""
    bundle = copy.deepcopy(_session(language)["bundle"])
    bundle["commitment"] = None
    answers = dict(bundle.get("answers") or {})
    answers.pop("commitment", None)
    bundle["answers"] = answers
    state = bundle["engine_state"] = dict(bundle.get("engine_state") or {})
    state["rule"] = "單筆部位上限定死 20%,超過就減"
    state["rule_dim"] = rule_dim
    state.pop("max_position_pct", None)
    plan = bundle["review_plan"] = dict(bundle.get("review_plan") or {})
    snapshot = dict(plan.get("state_snapshot") or {})
    if prior_commitment is None:
        snapshot.pop("prior_commitment", None)
    else:
        snapshot["prior_commitment"] = prior_commitment
    plan["state_snapshot"] = snapshot
    return bundle


def test_standing_rule_placeholder_resolves_copy_not_the_v1_literal():
    """#356: before the user picks a rule, Block 4 states the engine's
    prescription through the placeholder branch (which wrapper it picks is
    #546's concern, covered below). It used to interpolate ``engine_state.rule``
    verbatim — a zh literal ``trade_recap.prescribe`` hardcodes — so English
    cards printed a Chinese sentence inside an English wrapper (found in
    /fomo-qa dogfood, five of the thirteen mock personas). The placeholder now
    resolves the canonical text from ``copy/<locale>.json`` "rules" through
    ``engine_state.rule_dim``, the same resolution the committed and public
    rule paths already use.

    Expectations are the literal catalog sentences, not ``load_copy`` lookups:
    reading the expected value from the same source the renderer reads makes
    the assertion a tautology that passes however the wiring breaks."""
    expected = {
        "zh-TW": "單筆部位上限定死 20%；超過就減，不新增。",
        "en": "Cap any single position at 20%. Trim if it goes over, and do not add.",
    }
    for language, sentence in expected.items():
        block4 = _next_step_text(card_renderer.render_private(
            _pre_commitment_bundle(language)))
        assert sentence in block4, \
            f"{language}: the placeholder must print the catalog rule"
        assert "單筆部位上限定死 20%,超過就減" not in block4, \
            f"{language}: the v1 engine literal must never reach a v2 card"
    # The English card carries no CJK at all — the wrapper being translated is
    # not enough when the value interpolated into it is not.
    cjk = re.compile("[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff"
                     "\uf900-\ufaff\uff00-\uffef]")
    english = card_renderer.render_private(_pre_commitment_bundle("en"))
    leaked = [line for line in english.splitlines() if cjk.search(line)]
    assert not leaked, f"CJK leaked onto the English card: {leaked}"
    # A prescription whose dimension does not resolve falls back to the generic
    # localized line rather than the untranslated literal (fail-closed).
    for language, generic in (("zh-TW", "這次沒有新的規矩承諾"),
                              ("en", "No new rule commitment this time")):
        block4 = _next_step_text(card_renderer.render_private(
            _pre_commitment_bundle(language, rule_dim=None)))
        assert generic in block4, \
            f"{language}: an unresolvable dimension falls back to the generic line"
        assert "單筆部位上限定死" not in block4, \
            f"{language}: the fallback must not print the v1 literal either"


def test_first_review_preview_states_the_recommendation_as_pending_not_standing():
    """#546: the placeholder branch above fires on *every* preview —
    ``cmd_preview`` always drafts with ``require_commitment=False``, before
    the user has chosen a rule — not only on a user's very first review ever.
    "The standing rule remains" is a continuity claim, true only when
    ``review_plan.state_snapshot.prior_commitment`` actually holds a
    persisted rule; ``state.rule_dim`` is this period's fresh prescription,
    never a carried-forward answer. Same predicate ``_reconciliation_lines``
    already reads for #292's breach sentence.

    A fresh root (no prior commitment — the common real shape: a genuine
    first review, or any later one whose most recent finalize offered no
    candidate rule, confirmed against real personas by
    ``tests/persona_sweep.py --baseline``) must not claim otherwise; a
    returning user with a real persisted commitment that agrees with this
    period's prescription may still see the standing-rule continuity copy
    (#645 narrowed "may" to that agreement — the test below owns it). Both
    renderers share this branch through ``_card_structure`` (Markdown's
    compact text-first scan quotes the same panel via ``_read_first_panels``,
    and HTML's rule panel is scoped by the ``sec keystep`` section, the card's
    last content section)."""
    continuity_markers = {"en": ("remain", "keep", "standing"), "zh-TW": ("維持",)}
    for language in ("zh-TW", "en"):
        markers = continuity_markers[language]
        fresh = _pre_commitment_bundle(language)
        fresh_md_block4 = _next_step_text(card_renderer.render_private(fresh))
        fresh_html = card_renderer.render_html(fresh)
        fresh_html_tail = fresh_html[fresh_html.index('<div class="sec keystep">'):]
        for marker in markers:
            assert marker not in fresh_md_block4, \
                f"{language}: fresh-root Markdown preview must not claim rule " \
                f"continuity ({marker!r} found in Next step)"
            assert marker not in fresh_html_tail, \
                f"{language}: fresh-root HTML preview must not claim rule " \
                f"continuity ({marker!r} found in Next step)"

        returning = _pre_commitment_bundle(
            language, prior_commitment={"rule": "placeholder", "dim": "position_sizing"})
        returning_md_block4 = _next_step_text(card_renderer.render_private(returning))
        returning_html = card_renderer.render_html(returning)
        returning_html_tail = returning_html[returning_html.index('<div class="sec keystep">'):]
        assert any(marker in returning_md_block4 for marker in markers), \
            f"{language}: a returning user with a real prior commitment must keep " \
            f"the standing-rule continuity copy in Markdown"
        assert any(marker in returning_html_tail for marker in markers), \
            f"{language}: a returning user with a real prior commitment must keep " \
            f"the standing-rule continuity copy in HTML"


def test_a_returning_users_standing_rule_is_the_one_they_committed_to():
    """#645: the wrapper gate #546 added decided *whether* to claim continuity
    and left the quoted rule resolved from ``state.rule_dim`` — this period's
    fresh prescription — inside both wrappers. A returning user therefore read
    "the standing rule remains" followed by a rule they never chose: both
    halves of one sentence false at once, and the quoted half is the evidence
    offered for the other.

    The persisted commitment is the only record of what is standing, so it is
    what gets quoted, and continuity is claimed only when the commitment's own
    ``dim`` and ``state.rule_dim`` are *proven* to name the same dimension
    (``review._candidate_rules`` stamps the canonical ``dimension_id`` on every
    candidate row and ``_resolve_commitment`` carries it into the stored
    commitment; both sides normalize through ``dimension_id`` because a stored
    commitment holds the canonical id and ``rule_dim`` the legacy label).

    Four cases, and the expectations are literal catalog sentences rather than
    ``load_copy`` lookups — reading the expected value from the same source the
    renderer reads makes the assertion a tautology that passes however the
    wiring breaks:

    - **Diverge** — the standing rule is quoted verbatim, the prescription is
      named as a recommendation, and neither sibling wrapper's claim survives.
    - **Agree** — the continuity copy returns, still quoting the *committed*
      text; the catalog prescription for that same dimension must not be
      substituted for it, which is the narrow form of the same defect.
    - **Not comparable** — a commitment with no ``dim`` (a condition slot drops
      it deliberately; a custom rule need not carry one) fails safe onto the
      pending-choice wrapper: neither continuity nor divergence is asserted.
    - **First review** — unchanged, owned by the #546 test above.

    Markdown and HTML both, through the shared ``_card_structure`` path.
    """
    prescription = {
        "zh-TW": "單筆部位上限定死 20%；超過就減，不新增。",
        "en": "Cap any single position at 20%. Trim if it goes over, and do not add.",
    }
    committed = "Hold any new position at least 30 days before selling"

    def surfaces(bundle):
        html = card_renderer.render_html(bundle)
        return (("Markdown", _next_step_text(card_renderer.render_private(bundle))),
                ("HTML", html[html.index('<div class="sec keystep">'):]))

    for language in ("zh-TW", "en"):
        # The continuity claim itself, taken from the wrapper that makes it, so
        # the ban cannot drift away from the sentence it bans. Deriving a
        # *negative* expectation from the catalog is not the tautology the
        # positive literals above avoid: it stays exactly as wrong as the copy.
        wrappers = card_renderer.load_copy(language).get("block_missing") or {}
        continuity_claim = wrappers["rule_standing"].split("{rule}")[0].strip("\"「“: ；;")
        pending_claim = wrappers["rule_pending"].split("{rule}")[0].strip("\"「“: ；;")

        # rule_dim is position_sizing; the standing commitment is not.
        diverged = _pre_commitment_bundle(
            language, prior_commitment={"rule": committed, "dim": "holding_period"})
        for surface, text in surfaces(diverged):
            assert committed in text, \
                f"{language}/{surface}: the standing rule must be the one the " \
                f"user committed to, quoted verbatim"
            assert prescription[language] in text, \
                f"{language}/{surface}: the divergence line still names this " \
                f"period's prescription as a recommendation"
            assert continuity_claim not in text, \
                f"{language}/{surface}: a diverging period must not claim that " \
                f"the rule did not change ({continuity_claim!r} found)"
            assert pending_claim not in text, \
                f"{language}/{surface}: a returning user has a standing rule; " \
                f"the first-review wrapper must not absorb this case"

        # Agreement proven: the continuity copy returns, quoting what the user
        # actually wrote rather than the catalog text for that same dimension.
        agreed = _pre_commitment_bundle(
            language, prior_commitment={"rule": committed, "dim": "position_sizing"})
        for surface, text in surfaces(agreed):
            assert continuity_claim in text, \
                f"{language}/{surface}: an agreeing period keeps the continuity copy"
            assert committed in text, \
                f"{language}/{surface}: the continuity line quotes the committed " \
                f"rule, not the prescription that happens to share its dimension"
            assert prescription[language] not in text, \
                f"{language}/{surface}: this period's prescription must not be " \
                f"substituted for the rule the user committed to"

        # No comparable dimension: suppress the continuity claim rather than
        # guess. The pending-choice wrapper asserts nothing about the past.
        opaque = _pre_commitment_bundle(
            language, prior_commitment={"rule": committed})
        for surface, text in surfaces(opaque):
            assert continuity_claim not in text, \
                f"{language}/{surface}: a commitment the payload cannot compare " \
                f"must not be presented as unchanged"
            assert committed not in text, \
                f"{language}/{surface}: a commitment the payload cannot compare " \
                f"must not be presented as diverging either"
            assert prescription[language] in text, \
                f"{language}/{surface}: the fail-safe still states this " \
                f"period's recommendation"

    # Three locales move together (zh-CN has no persona run of its own, so the
    # catalog is asserted directly): same key, same two placeholders.
    for locale in ("en", "zh-TW", "zh-CN"):
        line = (card_renderer.load_copy(locale).get("block_missing") or {}).get("rule_diverged")
        assert isinstance(line, str) and line.strip(), \
            f"{locale}: block_missing.rule_diverged is missing"
        assert "{standing}" in line and "{recommendation}" in line, \
            f"{locale}: the divergence line must carry both rules, not one"


def test_standing_rule_placeholder_carries_the_user_cap_override():
    """#324 applies to the placeholder too: the standing rule the card restates
    is the same canonical text a candidate would offer, so a valid standing
    override moves its threshold and an out-of-range one is fail-closed."""
    universal = f"{card_renderer.POSITION_CAP:.0%}"
    for language in ("zh-TW", "en"):
        bundle = _pre_commitment_bundle(language)
        bundle["engine_state"]["max_position_pct"] = 0.30
        block4 = _next_step_text(card_renderer.render_private(bundle))
        assert "30%" in block4 and universal not in block4, \
            f"{language}: the user's cap must reach the standing-rule placeholder"
        bundle["engine_state"]["max_position_pct"] = 1.5
        block4 = _next_step_text(card_renderer.render_private(bundle))
        assert universal in block4, \
            f"{language}: an invalid override is fail-closed to the universal cap"


def main():
    tests = [
        test_finalize_html_is_structured_not_a_pre_dump,
        test_html_is_self_contained,
        test_html_supports_dark_mode,
        test_exactly_one_widget_fragment_pair,
        test_localized_title_from_copy_assets,
        test_engine_numbers_match_markdown_card,
        test_markdown_reader_path_surfaces_existing_risk_and_rule_before_performance,
        test_cli_private_markdown_is_the_committed_canonical_card,
        test_sparkline_renders_only_with_curve_points,
        test_sparkline_caption_names_peak_and_trough_without_the_window,
        test_rich_layout_renders_template_blocks_from_shared_facts,
        test_rich_layout_degrades_to_plain_sections_when_facts_missing,
        test_kpi_dashboard_uses_metric_boxes_not_flat_paragraphs,
        test_card_template_matches_its_generator,
        test_layout_uses_the_token_scales_not_ad_hoc_pixels,
        test_every_kpi_cell_has_the_same_three_part_shape,
        test_next_step_is_the_cards_only_emphasis_ground,
        test_rich_layout_zh_engine_strings_stay_off_the_english_card,
        test_stress_line_rides_block1_exposure_for_any_hole_dimension,
        test_keynote_and_four_blocks_in_order_on_both_surfaces,
        test_closing_synthesis_renders_as_fifth_block_after_next_step,
        test_closing_synthesis_absent_renders_no_fifth_block,
        test_closing_synthesis_empty_string_is_rejected_not_silently_dropped,
        test_zh_and_en_cards_light_the_same_blocks_from_the_same_state,
        test_all_honesty_collapses_into_block1_footnote_one_per_line,
        test_price_source_rides_the_footnote_ahead_of_unrealized_coverage,
        test_review_span_runs_to_the_price_date_the_card_is_valued_at,
        test_holdings_return_states_no_second_window_of_its_own,
        test_benchmark_sentence_states_the_excess_and_nothing_the_tile_repeats,
        test_benchmark_sentence_stays_whole_where_no_excess_tile_exists,
        test_vs_market_groups_by_market_label_only_when_mixed,
        test_coded_fields_resolve_zh_byte_identical_to_legacy_literals,
        test_coded_fields_resolve_zh_prescriptions_from_copy,
        test_coded_fields_render_localized_english_blocks,
        test_instrument_tag_price_note_stays_inline_without_growing_the_row,
        test_locale_copy_files_keep_key_parity,
        test_snapshot_overview_and_strength_copy_is_pinned_in_rendered_output,
        test_rule_grounding_sub_line_private_surfaces_only,
        test_preview_emits_html_and_finalize_cleans_pending,
        test_card_template_is_deorphaned,
        test_delivery_contract_exists_and_is_routed,
        test_engine_version_stamped_on_private_card_not_public,
        test_next_step_renders_exactly_one_action,
        test_next_step_reconciles_a_rule_that_contradicts_a_strength,
        test_rule_names_the_positions_it_would_act_on,
        test_rule_targets_filter_on_the_trigger_not_the_coach_cap,
        test_rule_targets_truncate_past_the_display_limit,
        test_renderer_oversize_trigger_matches_the_engine_constant,
        test_exit_opportunity_cost_collects_into_one_read_only_panel,
        test_exit_opportunity_cost_is_no_longer_scattered_across_key_trades,
        test_exit_consistency_panel_yields_to_the_question_when_asked,
        test_committed_rule_carries_its_threshold,
        test_renderer_position_cap_matches_the_engine_constant,
        test_committed_rule_carries_the_user_cap_override,
        test_public_committed_rule_carries_the_user_cap_override,
        test_standing_rule_placeholder_resolves_copy_not_the_v1_literal,
        test_first_review_preview_states_the_recommendation_as_pending_not_standing,
        test_a_returning_users_standing_rule_is_the_one_they_committed_to,
        test_standing_rule_placeholder_carries_the_user_cap_override,
        test_account_gate_sentence_names_the_actual_blocker,
        test_account_gate_degrades_instead_of_rendering_blank,
        test_annualized_gap_note_names_the_actual_blocker,
        test_alpha_below_grid_notes_are_two_independent_triggers,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS: {len(tests)} card HTML and delivery-contract tests")


if __name__ == "__main__":
    main()
