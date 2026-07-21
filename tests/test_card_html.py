#!/usr/bin/env python3
"""Structured HTML card and delivery-contract tests (#225; offline, stdlib only).

Drives a real test-drive session through the review.py CLI in an isolated root
(prepared artifacts, so no engine subprocess and no ledger access), then
asserts the preview/finalize HTML artifacts satisfy the renderer contract in
card-spec.md "Rendering": structured markup rather than a whole-document
``<pre>`` dump, self-contained (zero external requests), light/dark aware,
exactly one widget-fragment pair, localized from copy assets, numerically
consistent with the canonical Markdown card, and sparkline-conditional.
Doc-consistency assertions bind SKILL.md and the flows to
references/card-delivery.md and keep card-template.html de-orphaned.
"""
import copy
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

SVG_RE = re.compile(r"<svg.*?</svg>", re.S)
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
    for token in ("$-300", "$+200", "已實現盈虧比 1.4"):
        assert token in run["markdown"], f"engine value missing from Markdown: {token}"
        assert token in run["html"], f"engine value missing from HTML: {token}"


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


# #247: engine fields that light up the card-template rich layout. Values are
# synthetic but shaped exactly like trade_recap output on the committed mocks.
_RICH_CARD_FIELDS = {
    "ticker_diagnosis": [
        {"ticker": "PLTR", "impact": 76647.0, "tags": ["⚠押太重:佔組合 49%"]},
        {"ticker": "NVDA", "impact": 58524.0, "tags": ["✓紀律持有:賺 150%"]},
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
    instrument bars, stress row, attribution bars, improve rows — and every
    rich number appears in BOTH surfaces (one _card_structure facts source)."""
    bundle = _rich_bundle("zh-TW")
    html = card_renderer.render_html(bundle)
    markdown = card_renderer.render_private(bundle)
    assert 'class="grid4"' in html and html.count('<div class="m">') == 4
    assert html.count('<div class="trow">') == 3
    assert html.count('class="track"') == 3
    assert 'class="attr-head"' in html and html.count('<div class="arow">') == 2
    assert html.count('<div class="rx">') == 2
    # The headline already carries the primary-benchmark excess; comparator
    # rows are the alternatives only.
    assert "vs SPY" not in html and "vs QQQ" in html and "vs SOXX" in html
    for token in ("$+76,647", "$-1,000", "+243pp", "+96pp", "撐得住嗎", "砍損耗"):
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
    """The hole panel's HTML, up to the next section container."""
    return html.split('<div class="sec hole">', 1)[1].split('<div class="sec', 1)[0]


def _markdown_section(markdown, title):
    return markdown.split(f"## {title}", 1)[1].split("## ", 1)[0]


def test_stress_row_detaches_from_non_concentration_hole():
    """#263: the stress row argues a concentration exposure; a top hole from
    another dimension (the rich fixture's is averaging_down) must not absorb
    it.  The row moves to its own section on both surfaces instead."""
    bundle = _rich_bundle("zh-TW")
    html = card_renderer.render_html(bundle)
    markdown = card_renderer.render_private(bundle)
    sections = card_renderer.load_copy("zh-TW")["sections"]

    assert "撐得住嗎" not in _hole_panel_chunk(html), \
        "stress row may not ride inside the averaging-down hole panel"
    assert sections["stress"] in html and "撐得住嗎" in html, \
        "detached stress row must keep its own titled section"

    assert "撐得住嗎" not in _markdown_section(markdown, sections["hole"])
    assert "撐得住嗎" in _markdown_section(markdown, sections["stress"])


def test_stress_row_stays_inside_concentration_hole_panel():
    """#263 template provenance: when the top hole IS a concentration-family
    dimension, the stress row remains its supporting evidence inside the
    panel and no separate stress section appears."""
    sections = card_renderer.load_copy("zh-TW")["sections"]
    for dim, number_line in (("分散", "前三大風險部位佔 83%，最大 driver 佔 98%。"),
                             ("部位 sizing", "最大單一風險部位佔 49%，其餘平均 5%。")):
        bundle = _rich_bundle("zh-TW")
        hole = bundle["engine_card"]["top_holes"][0]
        hole["dim"] = hole["raw"]["dim"] = dim
        hole["number_line"] = number_line
        html = card_renderer.render_html(bundle)
        markdown = card_renderer.render_private(bundle)

        assert "撐得住嗎" in _hole_panel_chunk(html), \
            f"stress row must stay inside the {dim} hole panel"
        assert sections["stress"] not in html
        assert "撐得住嗎" in _markdown_section(markdown, sections["hole"])
        assert f"## {sections['stress']}" not in markdown


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


def test_coded_fields_render_zh_prescriptions_from_copy():
    markdown = card_renderer.render_private(_coded_bundle("zh-TW"))
    assert "砍損耗：虧損中加碼 12 次是你操盤損耗的大宗——這是最該先砍的純扣分動作。" in markdown
    assert "砍損耗：最大一筆 PLTR 佔 49%,單一押注過重。" in markdown


def test_coded_fields_render_localized_english_blocks():
    """#279 acceptance: the en card gains the stress line, improve rows, and
    instrument behavior tags from copy/en.json — with zero zh leakage."""
    bundle = _coded_bundle("en")
    html = card_renderer.render_html(bundle)
    markdown = card_renderer.render_private(bundle)
    for surface, name in ((html, "HTML"), (markdown, "Markdown")):
        for token in ("too heavy: 49% of the portfolio",
                      "disciplined hold: +150%",
                      "roughly neutral",
                      "could you sit through that?",
                      "Cut the leak",
                      "Adding to losing positions 12 times",
                      "Your largest position PLTR holds 49% of the portfolio"):
            assert token in surface, f"missing from en {name}: {token}"
        for zh_token in ("押太重", "紀律持有", "大致中性", "撐得住嗎", "砍損耗"):
            assert zh_token not in surface, f"zh vocabulary leaked into en {name}: {zh_token}"
    assert 'class="rx"' in html, "en card must now render prescription rows"
    # Markdown-only joins: halfwidth punctuation on the en card.
    assert "Cut the leak: Adding to losing positions 12 times" in markdown
    assert "(too heavy: 49% of the portfolio)" in markdown, \
        "en Markdown must join tags with halfwidth punctuation"


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


def main():
    tests = [
        test_finalize_html_is_structured_not_a_pre_dump,
        test_html_is_self_contained,
        test_html_supports_dark_mode,
        test_exactly_one_widget_fragment_pair,
        test_localized_title_from_copy_assets,
        test_engine_numbers_match_markdown_card,
        test_sparkline_renders_only_with_curve_points,
        test_rich_layout_renders_template_blocks_from_shared_facts,
        test_rich_layout_degrades_to_plain_sections_when_facts_missing,
        test_rich_layout_zh_engine_strings_stay_off_the_english_card,
        test_stress_row_detaches_from_non_concentration_hole,
        test_stress_row_stays_inside_concentration_hole_panel,
        test_coded_fields_resolve_zh_byte_identical_to_legacy_literals,
        test_coded_fields_render_zh_prescriptions_from_copy,
        test_coded_fields_render_localized_english_blocks,
        test_locale_copy_files_keep_key_parity,
        test_rule_grounding_sub_line_private_surfaces_only,
        test_preview_emits_html_and_finalize_cleans_pending,
        test_card_template_is_deorphaned,
        test_delivery_contract_exists_and_is_routed,
        test_engine_version_stamped_on_private_card_not_public,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS: {len(tests)} card HTML and delivery-contract tests")


if __name__ == "__main__":
    main()
