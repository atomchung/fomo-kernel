#!/usr/bin/env python3
"""Gate for skills/fomo-kernel/tools/design_bundle.py (#442 item 3; offline,
stdlib only).

Nothing ran this tool before: it was absent from run_all.py, CI, and the
hooks, and its output directory (``ds-bundle/``) is gitignored, so a change
that broke the generator -- or quietly stopped it from reading the runtime
CSS it derives from -- would ship silently.
``test_card_html.py::test_card_template_matches_its_generator`` is the
sibling precedent: a generated file gated against its generator. This file
gates the generator itself, one step earlier, because design_bundle.py's only
"committed" output is gitignored and has nothing to compare against.

design_bundle.py hardcodes its output directory relative to its own
``__file__`` (``OUT = pathlib.Path(__file__).parent / "ds-bundle"``), so
running it unmodified in place would write into the repo tree -- gitignored,
but not a temp path, and not something a test should leave behind. This gate
instead runs the real script body from a byte-for-byte copy placed in a
``TemporaryDirectory``, never edited, so ``__file__`` -- and therefore
``OUT`` -- resolves inside the temp dir. ``card_renderer`` is imported under
its real module name before the copy executes, so the copy's own
``sys.path`` insertion of a sibling ``engine/`` directory that does not exist
next to the temp copy never has to succeed: Python resolves the copy's
``import card_renderer`` straight out of ``sys.modules``, against the exact
module object this file also reads ``_HTML_WIDGET_CSS``/``_HTML_SHIM_CSS``
from.
"""
import importlib.util
import pathlib
import re
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "fomo-kernel"
TOOLS_DIR = SKILL / "tools"
SCRIPT = TOOLS_DIR / "design_bundle.py"
README = TOOLS_DIR / "ds-bundle-README.md"

sys.path.insert(0, str(SKILL / "engine"))
import card_renderer  # noqa: E402  (must land in sys.modules under this exact
                       # name before _run_generation executes a copy of
                       # design_bundle.py -- see module docstring)


def _run_generation(out_parent):
    """Execute a fresh copy of design_bundle.py so its OUT resolves under
    ``out_parent`` (a temp directory) instead of the real tools/ds-bundle.
    Returns the executed module -- TOKENS, CARD, and OUT included -- for the
    caller to inspect."""
    tmp_script = out_parent / "design_bundle.py"
    shutil.copyfile(SCRIPT, tmp_script)
    shutil.copyfile(README, out_parent / "ds-bundle-README.md")
    spec = importlib.util.spec_from_file_location("design_bundle_probe", tmp_script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise AssertionError(f"design_bundle.py raised during generation: {exc!r}") from exc
    return module


def test_design_bundle_runs_clean_and_writes_outside_the_repo_tree():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        module = _run_generation(tmp)
        out = tmp / "ds-bundle"
        assert module.OUT == out, "OUT must resolve under the temp copy, not the real tools/ dir"
        assert out.is_dir(), "design_bundle.py must write its bundle under OUT"
        produced = sorted(str(p.relative_to(out)) for p in out.rglob("*.html"))
        assert produced, "design_bundle.py produced no HTML previews"
        for section in ("foundations", "components", "scenarios"):
            assert any(name.startswith(section + "/") for name in produced), (
                f"no {section}/ preview was generated")
        assert (out / "README.md").is_file()


def test_tokens_and_card_are_derived_from_the_current_runtime_css():
    """The deeper half: TOKENS/CARD must actually be read off the live
    card_renderer._HTML_WIDGET_CSS/_HTML_SHIM_CSS at run time, not a frozen
    copy. Each probe below re-reads the *current* runtime literal with its
    own small regex -- independent of design_bundle's own rule scanner in
    _extract_tokens/_extract_card_css -- and asserts that exact value
    round-trips into the derived output. A runtime CSS reshape that breaks
    the derivation, or a derivation that silently stops reading the runtime,
    makes one of these go red; a legitimate CSS value change does not, since
    the expected value is read fresh each run rather than hardcoded here."""
    widget_css = card_renderer._HTML_WIDGET_CSS

    # The widget's own (non-token) font-family declaration -- TOKENS' `body`
    # rule reuses it verbatim (_extract_widget_font_family's documented job).
    font_match = re.search(r"\.rc\{font-family:([^;]+);", widget_css)
    assert font_match, "fixture assumption: .rc declares font-family directly"
    expected_font = font_match.group(1)

    # The light-theme fallback for one host-alias token (_extract_tokens /
    # _resolve_alias_value). --text-danger is declared twice: once in the
    # light .rc block, again inside the dark @media companion; the light one
    # is the first match in source order, matching how _extract_tokens walks
    # the stylesheet top to bottom.
    danger_match = re.search(r"--rc-text-danger:var\(--text-danger,([^)]+)\)", widget_css)
    assert danger_match, "fixture assumption: .rc aliases --text-danger with a fallback"
    expected_danger = danger_match.group(1)

    with tempfile.TemporaryDirectory() as tmp:
        module = _run_generation(pathlib.Path(tmp))

    assert expected_font in module.TOKENS, (
        "TOKENS no longer carries the runtime widget font-family -- "
        "the derivation may have stopped reading _HTML_WIDGET_CSS")
    assert f"--text-danger:{expected_danger}" in module.TOKENS, (
        "TOKENS no longer carries the runtime --text-danger fallback")

    # CARD must be fully rc-stripped: no leftover --rc- custom property and
    # no leftover bare .rc selector -- only .rc2, the renamed class that
    # keeps these previews from colliding with a real widget on the same
    # page (see the module docstring).
    assert "--rc-" not in module.CARD, "CARD still contains an un-rewritten --rc- token"
    assert "--rc-" not in module.TOKENS, "TOKENS still contains a live --rc- reference"
    assert ".rc2" in module.CARD
    assert not re.search(r"\.rc(?!2)\b", module.CARD), \
        "CARD still contains an un-rewritten .rc selector"


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS all {len(tests)} design_bundle gate tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
