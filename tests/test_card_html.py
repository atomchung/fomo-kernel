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
    for name in FLOW_FILES:
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
        test_preview_emits_html_and_finalize_cleans_pending,
        test_card_template_is_deorphaned,
        test_delivery_contract_exists_and_is_routed,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS: {len(tests)} card HTML and delivery-contract tests")


if __name__ == "__main__":
    main()
