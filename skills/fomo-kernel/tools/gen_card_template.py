#!/usr/bin/env python3
"""Generate skills/fomo-kernel/card-template.html from card-template.src.html.

    python3 skills/fomo-kernel/tools/gen_card_template.py

card-template.html used to be a hand-typed copy of the runtime's `.rc`-scoped
CSS, held equal to it -- declaration for declaration, both directions -- by
`test_widget_fragment_css_stays_mirrored_with_card_template` (#368 Phase 1),
with a short documented exception list for the handful of declarations that
exist only because the reference still illustrates a few Tabler icons and one
accessibility idea the icon-free runtime does not implement. `CLAUDE.md`
measured (#401, `tools/change_surface.py`) that every one of the last several
template edits also touched that test: the mirror was not really a second
design decision, it was bookkeeping that followed from the first one.

This script removes the bookkeeping by generating the file instead of
asserting it stays equal to its source. It reads the authored reference
`card-template.src.html` -- which carries every piece of design-provenance
prose, the illustrative example markup, and two placeholder markers --
and substitutes runtime-derived CSS at each marker:

  <!-- @GENERATED:SHIM_CSS -->    the PREVIEW SHIM's `:root` / dark-`:root`
                                   host theme and layout-scale tokens
  <!-- @GENERATED:WIDGET_CSS -->  the WIDGET FRAGMENT's `.rc`-scoped rules

Both markers are filled from the exact same two runtime literals
`tools/design_bundle.py` already derives its own preview bundle from
(`card_renderer._HTML_SHIM_CSS`, `card_renderer._HTML_WIDGET_CSS`) -- the
identical move #368 Phase 1 made for `ds-bundle/`, applied to this file
(#401). Template-only extras (the illustrative icons, the accessibility
idea) are no longer a test allowlist; they are ordinary authored CSS in
card-template.src.html, layered on top of the generated rules by ordinary
cascade (a later rule for the same selector adds declarations, it does not
need to replace the generated one).

Output is byte-deterministic: no timestamps, and no dict-ordering hazard --
Python dicts preserve insertion order, and every dict built here is populated
by walking the runtime CSS text in file order, never from a hash-based set.
Regenerating on an unchanged checkout is a no-op diff; tests/test_card_html.py's
test_card_template_matches_its_generator enforces exactly that by importing
this module and comparing render() against the committed file, so a stale
commit fails the suite instead of drifting silently.

This module is import-safe (unlike design_bundle.py, which writes its whole
bundle at import time): render() is a pure function, and only main() -- run
via `if __name__ == "__main__"` -- touches disk. The test above imports this
module for render() alone and must not trigger a rewrite as a side effect.
"""
import pathlib
import re
import sys

TOOLS_DIR = pathlib.Path(__file__).resolve().parent
SKILL_DIR = TOOLS_DIR.parent
ENGINE_DIR = SKILL_DIR / "engine"
sys.path.insert(0, str(ENGINE_DIR))
import card_renderer  # noqa: E402

SRC_PATH = SKILL_DIR / "card-template.src.html"
OUT_PATH = SKILL_DIR / "card-template.html"

SHIM_MARKER = "<!-- @GENERATED:SHIM_CSS -->"
WIDGET_MARKER = "<!-- @GENERATED:WIDGET_CSS -->"

GENERATED_HEADER = (
    "<!--\n"
    "  GENERATED FILE -- edit card-template.src.html and rerun\n"
    "  tools/gen_card_template.py. Do not hand-edit this file; a regeneration\n"
    "  will silently overwrite it, and the committed copy is required to match\n"
    "  a fresh regeneration (tests/test_card_html.py).\n"
    "-->\n"
)

# --------------------------------------------------------------------------
# Minimal, stdlib-only CSS rule scanner. CSS parsing in this repo has no
# shared module -- tools/design_bundle.py and tests/test_card_html.py each
# already carry their own small copy of this exact shape -- so this is this
# file's copy. Comments are stripped first: a comment immediately before an
# @media block otherwise confuses naive brace counting (found the hard way
# while building the design_bundle.py version of this scanner).
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


def _is_alias_rule(selector, decls):
    """True for the two `.rc{--rc-*: ...}` blocks (light rule + its dark
    `@media` companion) that alias a host theme variable, or its fallback,
    into a local `--rc-*` name -- identified by shape (every declaration is a
    `--rc-*` custom property), not position. See design_bundle.py's function
    of the same name for the fuller argument, including why naively renaming
    them would create a self-referencing custom property. This file's `.rc`
    reads the bare host names directly from its own generated `:root` (see
    _extract_tokens), so these two rules have no textual counterpart in the
    widget CSS -- they exist here only to be read for their values."""
    return selector == ".rc" and bool(decls) and all(n.startswith("--rc-") for n, _ in decls)


def _extract_widget_css(widget_css):
    """Every _HTML_WIDGET_CSS rule except the two alias rules, `--rc-`
    renamed to `--` (this file's `.rc` consumes host variables directly, with
    no local aliasing indirection layer). Selectors stay `.rc` -- unlike
    design_bundle.py's `.rc2` preview variant, this file is loaded as though
    it were the runtime widget, not a side-by-side preview of it."""
    lines = []
    for selector, body, media in _split_top_level_rules(widget_css):
        decls = _declarations(body)
        if _is_alias_rule(selector, decls):
            continue
        new_body = "".join(f"{_dash_rc_to_dash(name)}:{_dash_rc_to_dash(value)};"
                            for name, value in decls)
        rule = f"{selector}{{{new_body}}}"
        if media:
            rule = f"{media}{{{rule}}}"
        lines.append(rule)
    return "\n".join(lines)


def _resolve_alias_value(value):
    """An alias declaration's value is either `var(--host-name, fallback)`
    (every color/border/radius token) or a literal (the spacing/type scale)
    or a bare `var(--rc-other)` reference to another local alias (only
    `--rc-r-md`, which rides `--rc-radius`). This file has no host to defer
    to, so a passthrough resolves to its fallback; a reference to another
    alias stays a live reference (rc-stripped) so overriding one still moves
    the other; a literal is unchanged."""
    value = value.strip()
    if value.startswith("var(") and value.endswith(")"):
        inner = value[4:-1]
        name, _, fallback = inner.partition(",")
        if fallback:
            return fallback.strip()
        return "var(" + _dash_rc_to_dash(name.strip()) + ")"
    return value


def _extract_tokens(widget_css):
    """(light, dark) dicts of bare token name -> the runtime's own default
    value, read from _HTML_WIDGET_CSS's two alias rules (see _is_alias_rule).
    This is the same reading design_bundle.py's TOKENS gives those two rules:
    they are the one place the runtime states its own default theme, so this
    reference's `:root` can use it instead of a second, hand-typed copy."""
    light, dark = {}, {}
    for selector, body, media in _split_top_level_rules(widget_css):
        decls = _declarations(body)
        if not _is_alias_rule(selector, decls):
            continue
        target = dark if media == "@media (prefers-color-scheme:dark)" else light
        for name, value in decls:
            target[name[len("--rc-"):]] = _resolve_alias_value(value)
    return light, dark


def _extract_page_background(shim_css):
    """(light, dark) page background from _HTML_SHIM_CSS's `body` rule -- the
    runtime's own document-level shim for opening a card standalone, which
    this file's PREVIEW SHIM performs the identical job for. Named
    `surface-0` here (the page layer beneath the card's own `surface-2`),
    the same value design_bundle.py's differently-laid-out bundle names
    `--page-bg`."""
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


# Prefix order used ONLY to group declarations onto readable lines -- surface
# colors, then text colors, then border/radius, then the layout scale, the
# same rough order a human would read the theme in. This is not an allowlist:
# _root_block below emits every name a table actually has, so a name that
# matches no prefix here still renders, just ungrouped at the end. That
# distinction matters -- an explicit name list would silently drop a new
# runtime token that this file was never updated to mention, which is
# exactly the kind of hand-sync point #401 exists to remove. A new `--rc-*`
# custom property in the runtime's alias rule (see _extract_tokens) reaches
# this file's `:root` on the next regeneration with no edit here required.
_GROUP_PREFIXES = ("surface", "text", "border", "radius", "sp-", "tx-", "r-")


def _group_of(name):
    for index, prefix in enumerate(_GROUP_PREFIXES):
        if name == prefix or name.startswith(prefix):
            return index
    return len(_GROUP_PREFIXES)  # unrecognized prefix: still emitted, grouped last


def _root_block(table):
    """Every name in `table` (insertion order preserved within its group),
    one `;`-joined line per group. No name is ever dropped for being
    unrecognized -- see _GROUP_PREFIXES."""
    buckets = {}
    for name, value in table.items():
        buckets.setdefault(_group_of(name), []).append(f"--{name}:{value}")
    return "\n".join(";".join(decls) + ";" for _, decls in sorted(buckets.items()))


def _build_shim_css(widget_css, shim_css):
    """The PREVIEW SHIM's `:root` blocks: every host theme color/radius the
    widget fragment falls back to, the layout scale, and the page background
    -- the concrete values a browser needs to render this file standalone,
    so the widget's `var(--x, fallback)` reads `--x` from here instead of
    ever touching the fallback."""
    light, dark = _extract_tokens(widget_css)
    page_bg_light, page_bg_dark = _extract_page_background(shim_css)
    # surface-0 (the page background) has no runtime `--rc-*` counterpart --
    # see _extract_page_background -- so it is merged in here rather than
    # flowing through _extract_tokens like every other name.
    light = {**light, "surface-0": page_bg_light}
    dark = {**dark, "surface-0": page_bg_dark}
    return (f":root{{\n{_root_block(light)}\n}}\n"
            f"@media (prefers-color-scheme:dark){{:root{{\n{_root_block(dark)}\n}}}}")


def render():
    """Return the generated card-template.html text, without touching disk."""
    source = SRC_PATH.read_text(encoding="utf-8")
    for marker in (SHIM_MARKER, WIDGET_MARKER):
        count = source.count(marker)
        # Exactly one real occurrence per marker: this is a plain substring
        # replace, so a stray second mention (e.g. prose in the header
        # comment quoting the marker literally) would silently have CSS
        # substituted into it too, instead of failing loudly. Caught while
        # building this script -- the first draft of the header comment did
        # exactly that.
        if count == 0:
            raise SystemExit(f"{SRC_PATH.name} is missing the {marker!r} marker")
        if count > 1:
            raise SystemExit(
                f"{SRC_PATH.name} contains {marker!r} {count} times; it must "
                "appear exactly once. Refer to it by name in prose (e.g. "
                "\"the WIDGET_CSS marker\") instead of quoting the literal "
                "HTML comment.")

    shim_css = _build_shim_css(card_renderer._HTML_WIDGET_CSS, card_renderer._HTML_SHIM_CSS)
    widget_css = _extract_widget_css(card_renderer._HTML_WIDGET_CSS)

    rendered = source.replace(SHIM_MARKER, shim_css).replace(WIDGET_MARKER, widget_css)
    return GENERATED_HEADER + rendered


def main():
    OUT_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT_PATH.relative_to(SKILL_DIR.parent.parent)}")


if __name__ == "__main__":
    main()
