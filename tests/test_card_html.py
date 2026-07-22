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
        final = v2._run("finalize", "--root", root, "--session-id", plan["session_id"],
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
    for token in ("-$300", "+$200", "已實現盈虧比 1.4"):
        assert token in run["markdown"], f"engine value missing from Markdown: {token}"
        assert token in run["html"], f"engine value missing from HTML: {token}"


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
        finalized = v2._run("finalize", "--root", root, "--session-id", plan["session_id"],
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
    assert SVG_RE.sub("", html) == html_without, \
        "removing curve data may only remove the sparkline, nothing else"

    note_form = copy.deepcopy(run["bundle"])
    note_form["engine_card"]["pnl_curve"] = {"note": "無資料"}
    html_note = card_renderer.render_html(note_form)
    assert "<svg" not in html_note and "無資料" not in html_note, \
        "note-form curve must be omitted silently, not printed"
    assert SVG_RE.sub("", html) == html_note


def test_sparkline_caption_shows_date_range_and_peak_trough():
    """#312: the sparkline is not an unlabeled decoration — a caption under the
    line gives the start~end date range plus the peak/trough of the very same
    ``pnl_curve.points`` the path already traces (no invented number, no full
    axis system)."""
    for language, joiner in (("zh-TW", "高點"), ("en", "peak")):
        html = _session(language)["html"]
        trough_word = "低點" if language == "zh-TW" else "trough"
        caption = (f'<p class="cap">2026-06-30 ~ 2026-07-14 · '
                   f'{joiner} +3% · {trough_word} -1%</p>')
        assert caption in html, f"{language} sparkline caption missing or wrong: {caption!r}"
        # The caption must ride directly under the sparkline it describes, not
        # float free somewhere else in the section.
        assert re.search(r'<svg class="spark[^"]*".*?</svg>' + re.escape(caption), html, re.S), \
            f"{language} caption must sit immediately after its own <svg>"

    # A point missing only its date must drop the caption without touching the
    # line itself — the fail-soft contract is per-field, not all-or-nothing.
    run = _session("zh-TW")
    bundle = copy.deepcopy(run["bundle"])
    points = [dict(p) for p in bundle["engine_card"]["pnl_curve"]["points"]]
    points[0].pop("date", None)
    points[-1].pop("date", None)
    bundle["engine_card"]["pnl_curve"] = {"points": points}
    html_no_dates = card_renderer.render_html(bundle)
    assert "<svg" in html_no_dates and 'class="cap"' not in html_no_dates, \
        "points missing dates must drop only the caption, never the sparkline line"
    assert SVG_RE.sub("", run["html"]) == SVG_RE.sub("", html_no_dates), \
        "dropping the caption alone must not change any other text on the card"


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
    assert 'class="grid4"' in html and html.count('<div class="m">') == 4
    assert html.count('<div class="trow">') == 3
    assert html.count('class="track"') == 3
    assert 'class="attr-head"' in html and html.count('<div class="arow">') == 2
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


def test_rich_layout_degrades_to_plain_sections_when_facts_missing():
    """The stock fixture lacks the rich fields: KPI tiles still come from the
    overview, and every other rich block stays absent instead of inventing."""
    html = _session("zh-TW")["html"]
    assert 'class="grid4"' in html
    for marker in ('class="trow"', 'class="attr-head"', 'class="rx"'):
        assert marker not in html, f"unexpected rich block on plain fixture: {marker}"


def test_kpi_dashboard_uses_grid4_metric_boxes_not_flat_paragraphs():
    """#310: Total P&L, (realized) payoff ratio, benchmark excess, and
    annualized alpha — the four metrics #310 named — must render as
    card-template.html's `.grid4` row of labeled `.m` metric boxes, never
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
        plain_html = _session(language)["html"]
        assert plain_html.count('<div class="grid4">') == 1, \
            f"{language} plain card must carry exactly one KPI dashboard row"
        for key in ("pnl", "payoff"):
            tile_open = f'<div class="m"><p class="lbl">{html.escape(kpi_copy[key])}</p>'
            assert tile_open in plain_html, \
                f"{language} {key!r} metric ({kpi_copy[key]!r}) is not inside a .grid4 .m box"

        # The rich fixture also lights benchmark excess + annualized alpha —
        # all four of #310's named metrics, all four as metric boxes.
        rich_html = card_renderer.render_html(_rich_bundle(language))
        assert rich_html.count('<div class="grid4">') == 1
        assert rich_html.count('<div class="m">') == 4
        for key in ("pnl", "payoff", "excess", "alpha"):
            tile_open = f'<div class="m"><p class="lbl">{html.escape(kpi_copy[key])}</p>'
            assert tile_open in rich_html, \
                f"{language} {key!r} metric ({kpi_copy[key]!r}) is not inside a .grid4 .m box"


def test_grid4_metric_box_css_stays_mirrored_with_card_template():
    """CLAUDE.md "Mirrored surfaces": card-template.html's `.grid4`/`.m` rules
    are the documented design intent for the KPI dashboard (#310); the runtime
    `_HTML_WIDGET_CSS` must keep matching layout constraints for the classes
    that give the dashboard its shape. This does not require byte-identical
    CSS — the runtime legitimately renames every themed variable from the
    template's `--foo` to a locally-aliased `--rc-foo` (defined once at the
    top of `_HTML_WIDGET_CSS` as `var(--foo, <fallback>)`, per its own
    docstring) and adds host-theming fallbacks and a standalone `.spark` rule
    the static template never needed — only that every declaration the
    template makes for these selectors still holds at runtime, so the two
    cannot silently drift apart again."""
    template = (SKILL / "card-template.html").read_text(encoding="utf-8")

    def _rule(css_text, selector):
        match = re.search(re.escape(selector) + r"\{([^}]*)\}", css_text)
        assert match, f"selector {selector!r} not found"
        return {prop.strip() for prop in match.group(1).split(";") if prop.strip()}

    def _normalize(props):
        # Undo the runtime's "--foo" -> "--rc-foo" theming alias so a rule
        # copied verbatim from the template compares equal to its runtime form.
        return {prop.replace("--rc-", "--") for prop in props}

    # Selectors as written differ only in the `.rc ` ancestor prefix the
    # runtime widget fragment always renders under; compare their bodies.
    for selector in (".grid4", ".m", ".m .lbl", ".m .val", ".m .sub"):
        template_props = _normalize(_rule(template, ".rc " + selector))
        runtime_props = _normalize(_rule(card_renderer._HTML_WIDGET_CSS, ".rc " + selector))
        missing = template_props - runtime_props
        assert not missing, \
            f"{selector} lost {missing!r} vs card-template.html (runtime has {runtime_props!r})"


def test_rich_layout_zh_engine_strings_stay_off_the_english_card():
    """Legacy persisted zh literals (pre-#279 bundles) must not leak onto the
    English card; language-neutral blocks (grid, bars) still render. There is
    no read-time migration for these by owner ruling on #279 — zh renders them
    verbatim, en omits them."""
    html = card_renderer.render_html(_rich_bundle("en"))
    assert 'class="grid4"' in html and 'class="trow"' in html
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


def test_stress_line_rides_block1_exposure_for_any_hole_dimension():
    """Output contract §2 (supersedes the #263/#265 split placement): the
    stress line rides Block 1's exposure indicator area unconditionally when
    its data exists. #265's intent survives — an unrelated hole never absorbs
    the stress fact — and the concentration-family holes no longer host it
    either; there is no standalone stress section on either surface."""
    sections = card_renderer.load_copy("zh-TW")["sections"]
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
        assert "撐得住嗎" in html and f"## {sections['stress']}" not in markdown \
            and sections["stress"] not in html, \
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
    assert lines[tw_at + 1].startswith("- ") and "TW 部位報酬" in lines[tw_at + 1], \
        "the TW benchmark line must follow its [TW] label, bulleted"
    assert lines[us_at + 1].startswith("- ") and "US 部位報酬" in lines[us_at + 1], \
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
    # light-capture.md is the one deliberate exception (#237 #4): a light-tier
    # session never reaches preview/finalize, so it never renders a card and has
    # nothing to route through the delivery contract.
    card_routing_flows = tuple(name for name in FLOW_FILES if name != "light-capture.md")
    for name in card_routing_flows:
        flow = (SKILL / "flows" / name).read_text(encoding="utf-8")
        assert "references/card-delivery.md" in flow, f"flows/{name} must route card delivery"


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
    at sub-line level under the rule rather than as a detached footnote."""
    sizing = _next_step_text(card_renderer.render_private(
        _conflicted_bundle("zh-TW", "position_sizing")))
    assert "PLTR 49%" in sizing and "NVDA 46%" in sizing, \
        "the sizing rule must name every position over the cap"
    assert "ORCL" not in sizing and "AMD" not in sizing, \
        "positions inside the cap are not what the rule would catch"
    avgdown = _next_step_text(card_renderer.render_private(
        _conflicted_bundle("zh-TW", "averaging_down")))
    assert "PLTR 7 次" in avgdown and "NVDA 5 次" in avgdown, \
        "the averaging-down rule must name per-ticker counts"
    en = _next_step_text(card_renderer.render_private(
        _conflicted_bundle("en", "position_sizing")))
    assert "PLTR 49%, NVDA 46%" in en, "en must join targets with halfwidth punctuation"


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
        test_sparkline_caption_shows_date_range_and_peak_trough,
        test_rich_layout_renders_template_blocks_from_shared_facts,
        test_rich_layout_degrades_to_plain_sections_when_facts_missing,
        test_kpi_dashboard_uses_grid4_metric_boxes_not_flat_paragraphs,
        test_grid4_metric_box_css_stays_mirrored_with_card_template,
        test_rich_layout_zh_engine_strings_stay_off_the_english_card,
        test_stress_line_rides_block1_exposure_for_any_hole_dimension,
        test_keynote_and_four_blocks_in_order_on_both_surfaces,
        test_zh_and_en_cards_light_the_same_blocks_from_the_same_state,
        test_all_honesty_collapses_into_block1_footnote_one_per_line,
        test_price_source_rides_the_footnote_ahead_of_unrealized_coverage,
        test_vs_market_groups_by_market_label_only_when_mixed,
        test_coded_fields_resolve_zh_byte_identical_to_legacy_literals,
        test_coded_fields_resolve_zh_prescriptions_from_copy,
        test_coded_fields_render_localized_english_blocks,
        test_instrument_tag_price_note_stays_inline_without_growing_the_row,
        test_locale_copy_files_keep_key_parity,
        test_rule_grounding_sub_line_private_surfaces_only,
        test_preview_emits_html_and_finalize_cleans_pending,
        test_card_template_is_deorphaned,
        test_delivery_contract_exists_and_is_routed,
        test_engine_version_stamped_on_private_card_not_public,
        test_next_step_renders_exactly_one_action,
        test_next_step_reconciles_a_rule_that_contradicts_a_strength,
        test_rule_names_the_positions_it_would_act_on,
        test_exit_opportunity_cost_collects_into_one_read_only_panel,
        test_exit_opportunity_cost_is_no_longer_scattered_across_key_trades,
        test_exit_consistency_panel_yields_to_the_question_when_asked,
        test_committed_rule_carries_its_threshold,
        test_renderer_position_cap_matches_the_engine_constant,
        test_committed_rule_carries_the_user_cap_override,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS: {len(tests)} card HTML and delivery-contract tests")


if __name__ == "__main__":
    main()
