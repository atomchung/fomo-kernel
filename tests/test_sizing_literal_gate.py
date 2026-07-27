#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""No-bare-sizing-literal gate (#477) — offline, deterministic, no pytest.

#324 unified the single-position sizing thresholds (``OVERSIZE_TRIGGER``,
``POSITION_CAP``) so a user's ``review.py set-cap`` override moves every
reader in lockstep, through ``effective_oversize_trigger()`` /
``effective_position_cap()``. #477 found a fourth reader #324 missed:
``ticker_diagnosis``'s ``too_heavy`` tag still compared against a bare
``0.25``, 1,500 lines from the comment describing the fix it should have
joined. Per docs/development-guide.md section 2 ("the second recurrence of a
symptom is a design issue, not a second patch"), that line is not the last
bare literal anyone will ever type here — this gate stands in for re-reading
every future diff by eye.

Two independent AST-based checks (never grep: grep cannot tell a comparison
from a comment or a docstring, and this file's own history is full of both):

  A. NAME guard — zero false positives, no exemption list needed.
     ``OVERSIZE_TRIGGER``/``POSITION_CAP`` may be *read* only inside the
     ``effective_*``/``valid_position_cap`` functions built specifically to
     be everyone else's single entry point. Any other function reading the
     raw constant by name — bypassing the override entirely — is exactly the
     failure mode the issue's own diagnosis names ("OVERSIZE_TRIGGER should
     be reachable only through effective_oversize_trigger"). An identifier
     either is that exact name or it is not, so there is no ambiguity to
     exempt around.

  B. VALUE guard — no comparison (<, >, <=, >=, ==, !=) anywhere under
     skills/fomo-kernel/engine/ may use a numeral literal equal to
     OVERSIZE_TRIGGER's or POSITION_CAP's *current* value, read live off
     trade_recap/card_renderer rather than re-hardcoded a third time in this
     file (that would be the same bug one layer up). This is the check that
     would have caught #477 itself — the bug was a re-typed literal, not a
     name reference, so the name guard alone would have missed it.

     0.25 and 0.20 are common round numbers, and this repository already
     spends both on unrelated thresholds that have nothing to do with
     position sizing (confirmed by reading each site, not guessed — see
     VALUE_GUARD_EXEMPT below for exactly which ones and why). A blanket
     scan with no exemptions flags all of them, which would make the gate
     merely decorative: either it stays permanently red, or the next
     maintainer "fixes" it by weakening the check itself instead of their
     diff — the exact failure this file exists to prevent. Each exemption is
     keyed by the exact (file, enclosing function, literal value) triple,
     not by file or function alone, so a *second*, unrelated bare literal
     added later to the same function is still caught even though that
     function already has one exemption on record. Widening an exemption's
     grain (dropping the value or the function) is a decision for a human
     reading this file, and it should be rare — grep the git blame on
     VALUE_GUARD_EXEMPT before adding to it, and prefer fixing the ambiguity
     at its source (a named constant, like AVGDOWN_BREACH_W already is)
     over adding a fourth entry.
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE)
import card_renderer as cr  # noqa: E402
import trade_recap as tr  # noqa: E402

# Functions allowed to read OVERSIZE_TRIGGER/POSITION_CAP by name — the
# single entry point #324 (trade_recap.py) and #328 (card_renderer.py) built
# for everyone else. Each file carries its own independent pair of these
# constants and wrapper functions (a deliberate, tested mirror — see
# card_renderer.py:322-334 and tests/test_card_html.py); this allow-list
# applies per-file, so it is never a cross-file exemption.
NAME_GUARD_ALLOWED = {"valid_position_cap", "effective_oversize_trigger",
                      "effective_position_cap"}
GUARDED_NAMES = {"OVERSIZE_TRIGGER", "POSITION_CAP"}

# (relative filename, enclosing function, literal value): pre-existing,
# individually-audited, unrelated uses of the same round numbers. See the
# module docstring's part B for the general policy; the reason for each
# specific entry is below.
VALUE_GUARD_EXEMPT = {
    # dim_avgdown's breach check compares a position's cost weight *at the
    # time of a historical average-down purchase* against 0.25 — a
    # different, non-overridable invariant (AVGDOWN_BREACH_W, #94/#137),
    # not the live market-weight cap #324/#477 unified. #94 deliberately
    # rejected wiring this to today's size_dim weights (see the function's
    # own comment), so it must never be routed through
    # effective_oversize_trigger either.
    ("trade_recap.py", "dim_avgdown", 0.25):
        "AVGDOWN_BREACH_W-equivalent cost weight at purchase time, not the live oversize trigger",
    # ticker_diagnosis's own disciplined_hold tag compares *unrealized
    # return* (cur) against 0.20 — a return threshold, not a position
    # weight, that happens to numerically coincide with POSITION_CAP. This
    # sits in the very function #477 fixed, which is exactly why the
    # exemption is scoped to the value 0.20 and not to the whole function:
    # a fresh bare comparison against 0.25 in this same function (the #477
    # bug pattern) is still caught.
    ("trade_recap.py", "ticker_diagnosis", 0.20):
        "disciplined_hold's unrealized-return threshold, not a position weight",
    # _public_band buckets a generic 0-1 input into a public-disclosure
    # label (low/moderate/high/very_high); unrelated to any single
    # position's weight.
    ("card_renderer.py", "_public_band", 0.25):
        "public-disclosure banding cutoff, unrelated to position sizing",
}

ENGINE_FILES = sorted(f for f in os.listdir(ENGINE)
                      if f.endswith(".py") and not f.startswith("test_"))


class _Collector(ast.NodeVisitor):
    """Walks one module, tracking the innermost enclosing function so every
    hit — a Load of a guarded name, or a Compare against a guarded value —
    is attributed to the function that actually contains it (never a
    lexical parent: entering/leaving FunctionDef pushes/pops the stack),
    which is what lets exemptions be scoped this tightly."""

    def __init__(self):
        self.func_stack = ["<module>"]
        self.name_hits = []   # (function, lineno, name)
        self.value_hits = []  # (function, lineno, value)

    def _enter_function(self, node):
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    def visit_FunctionDef(self, node):
        self._enter_function(node)

    def visit_AsyncFunctionDef(self, node):
        self._enter_function(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load) and node.id in GUARDED_NAMES:
            self.name_hits.append((self.func_stack[-1], node.lineno, node.id))
        self.generic_visit(node)

    def visit_Attribute(self, node):
        # Catches a future `trade_recap.OVERSIZE_TRIGGER`-style read from a
        # different module, not just a bare name in the defining file.
        if isinstance(node.ctx, ast.Load) and node.attr in GUARDED_NAMES:
            self.name_hits.append((self.func_stack[-1], node.lineno, node.attr))
        self.generic_visit(node)

    def visit_Compare(self, node):
        for operand in [node.left] + list(node.comparators):
            if (isinstance(operand, ast.Constant)
                    and isinstance(operand.value, (int, float))
                    and not isinstance(operand.value, bool)):
                self.value_hits.append((self.func_stack[-1], node.lineno, operand.value))
        self.generic_visit(node)


def _collect(source_path):
    with open(source_path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=source_path)
    collector = _Collector()
    collector.visit(tree)
    return collector


def _guarded_values():
    """Read live off the two source-of-truth modules rather than
    re-hardcoded here — a third copy of "0.25"/"0.20" in this checker would
    be the same single-source-of-truth violation it exists to catch."""
    return {tr.OVERSIZE_TRIGGER, tr.POSITION_CAP, cr.OVERSIZE_TRIGGER, cr.POSITION_CAP}


def test_oversize_trigger_and_position_cap_are_read_only_through_effective_wrappers():
    violations = []
    for fname in ENGINE_FILES:
        collector = _collect(os.path.join(ENGINE, fname))
        for func, lineno, name in collector.name_hits:
            if func not in NAME_GUARD_ALLOWED:
                violations.append(f"{fname}:{lineno} in {func}() reads {name} directly")
    assert not violations, (
        "OVERSIZE_TRIGGER/POSITION_CAP read outside their effective_* wrapper -- "
        "route through effective_oversize_trigger()/effective_position_cap() instead (#477):\n  "
        + "\n  ".join(violations)
    )


def test_no_bare_comparison_against_the_sizing_literals():
    guarded_values = _guarded_values()
    violations = []
    for fname in ENGINE_FILES:
        collector = _collect(os.path.join(ENGINE, fname))
        for func, lineno, value in collector.value_hits:
            if value not in guarded_values:
                continue
            if (fname, func, value) in VALUE_GUARD_EXEMPT:
                continue
            violations.append(f"{fname}:{lineno} in {func}() compares against bare {value!r}")
    assert not violations, (
        "a bare literal matching OVERSIZE_TRIGGER's/POSITION_CAP's value was compared "
        "directly instead of going through effective_oversize_trigger()/"
        "effective_position_cap() -- this is the #477 bug pattern (a user's set-cap "
        "override would not reach this comparison). If this is a genuinely unrelated "
        "threshold that coincides in value, audit it like the existing "
        "VALUE_GUARD_EXEMPT entries and add it there with a reason:\n  "
        + "\n  ".join(violations)
    )


def test_exemption_registry_still_matches_something_real():
    """An exemption nobody's code trips is dead weight that quietly widens
    what the gate accepts. If a future edit removes the exempted
    comparison (e.g. dim_avgdown starts using AVGDOWN_BREACH_W instead of a
    bare 0.25), this catches the entry going stale instead of leaving it to
    rot as an unnoticed loophole for the *next* bare literal in that same
    function."""
    guarded_values = _guarded_values()
    seen = set()
    for fname in ENGINE_FILES:
        collector = _collect(os.path.join(ENGINE, fname))
        for func, _lineno, value in collector.value_hits:
            if value in guarded_values:
                seen.add((fname, func, value))
    stale = set(VALUE_GUARD_EXEMPT) - seen
    assert not stale, f"exemption(s) no longer matched by any real comparison: {stale}"


def test_engine_scan_finds_the_known_files():
    """Premise check for the two tests above: if ENGINE resolves to the
    wrong directory or the scan silently sees zero files, both gates would
    pass vacuously (a fake green, docs/development-guide.md section 2 type
    3). Pin that trade_recap.py and card_renderer.py -- the two files every
    other assertion here depends on -- are actually in the scanned set."""
    assert "trade_recap.py" in ENGINE_FILES
    assert "card_renderer.py" in ENGINE_FILES
    assert len(ENGINE_FILES) > 10, f"suspiciously few engine files found: {ENGINE_FILES}"


# ─────────────────────────── runner ───────────────────────────

def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _main() else 0)
