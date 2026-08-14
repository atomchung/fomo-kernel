#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_expression.py — the expression contract's mechanical half for the
conversational surfaces (docs/expression-contract.md section 6; offline,
deterministic).

`check_card.py` has done this for the review card since #276: S-3 asserts
that the card's disclosures sit in one place and nowhere else. Nothing did it
for `consider`, freeform answers, no-book framing or the weekly market read,
which is the gap #823 names — the good rules existed and were surface-bound,
so the one surface with a checker was the one surface that behaved.

This is the E series, and it is the same philosophy check_card states in its
own header: anything a regex can decide never goes to an LLM judge.

  E-1  D1 — one disclosure block, and it is the tail of the answer.
  E-2  D3 — every line of it carries the registered prefix, and that prefix
       appears nowhere else.
  E-3  D5 — the block is at most five lines.
  E-4  D6 — no limitation is stated twice inside the block.
  E-5  C4 — no engine payload token reaches the user.

What is deliberately absent: D2 (which qualifiers stay inline) and D4
(whether a disclosure's condition actually fired). Both need a reader who
knows what the sentence is about. A prefix check that pretended to cover them
would be the structural gate this repository has already been burned by —
proof that a marker is present is not proof that the right thing sits behind
it.

The token blacklist for E-5 is READ FROM THE SCHEMAS, never transcribed here.
`skills/fomo-kernel/schemas/*.schema.json` already enumerate the engine's own
vocabulary, so a new disclosure key or rule effect is covered the day it is
added rather than the day someone remembers this file. Hand-mirroring it is
what docs/maintainer-guide.md forbids and what borrowed enumerations go stale
from.

Run:
  python3 tests/agent/check_expression.py <answer.md|->
  python3 tests/agent/check_expression.py            # check the witness fixture
Import:
  from check_expression import check_expression, check_fixture
  findings = check_expression(answer_text)   # list[Finding]
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import unicodedata
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "skills" / "fomo-kernel" / "schemas"
DEFAULT_FIXTURE = ROOT / "tests" / "agent" / "expression-witnesses.json"

# docs/expression-contract.md D3, "every conversational surface" row. The card
# footnote's own prefix is a different row of that registry and is checked by
# check_card.py S-3, not here.
PREFIX = "[i] "
_BLOCK_LINE_RE = re.compile(r"^\[i\] \S")
# Anything that looks like an attempt at the prefix. A line matching this but
# not _BLOCK_LINE_RE is a malformed disclosure line rather than prose, which is
# what makes E-2 a check and not a formatting preference.
_PREFIX_ATTEMPT_RE = re.compile(r"^\s*[\[(]\s*i(?:nfo)?\s*[\])]\s*", re.I)

# D5. Five, measured: see the expression contract's own derivation.
LINE_CAP = 5

# E-5's schemas. Two files, not the whole directory: these are the two that
# enumerate what a `consider` answer's payload can say, which is exactly the
# vocabulary C4 keeps out of prose. Widening to every schema would drag in
# vocabularies no answer surface ever reads.
_TOKEN_SCHEMAS = ("evaluation-challenge.schema.json", "trade-evaluation.schema.json")

# Tokens that are engine vocabulary but also ordinary product words a
# conversational answer legitimately uses. Kept explicitly rather than by
# loosening the pattern, so the exemption is visible and reviewable.
_TOKEN_EXEMPT = frozenset({"as_of"})


ASSERTIONS = ("E-1", "E-2", "E-3", "E-4", "E-5")


@dataclass
class Finding:
    assertion: str
    passed: bool
    label: str
    evidence: str = ""

    def __str__(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        tail = f"  -> {self.evidence}" if (self.evidence and not self.passed) else ""
        return f"{mark} {self.assertion}  {self.label}{tail}"


def _enum_values(node, out):
    """Every `enum` member anywhere in a JSON schema tree."""
    if isinstance(node, dict):
        values = node.get("enum")
        if isinstance(values, list):
            out.update(value for value in values if isinstance(value, str))
        for child in node.values():
            _enum_values(child, out)
    elif isinstance(node, list):
        for child in node:
            _enum_values(child, out)


def internal_tokens(schema_dir: pathlib.Path = SCHEMA_DIR) -> tuple:
    """The engine's own snake_case vocabulary, derived from the schemas.

    Mirrors `answer_provenance._INTERNAL_TOKENS`'s rule (an enum member
    containing an underscore) without importing the engine: this checker
    stays dependency-free and runnable on a machine that never installed the
    engine's optional requirements. The two lists are the same rule applied
    to the same source of truth, so a token added to `consequence.py` and
    mirrored into its schema — which `tests/test_consider.py` already pins —
    reaches both."""
    values = set()
    for name in _TOKEN_SCHEMAS:
        path = schema_dir / name
        if not path.exists():
            continue
        try:
            _enum_values(json.loads(path.read_text(encoding="utf-8")), values)
        except (OSError, json.JSONDecodeError):
            continue
    return tuple(sorted(token for token in values
                        if "_" in token and token not in _TOKEN_EXEMPT))


def _lines(text: str) -> list:
    return text.replace("\r\n", "\n").split("\n")


def _block_bounds(lines: list):
    """`(start, end)` of the disclosure block, or `None` when there is none.

    A block is the maximal run of prefixed lines. Finding it as a run rather
    than as "every prefixed line" is what lets E-1 report an interleaved
    answer as one failure with evidence instead of silently accepting the
    last run."""
    indices = [index for index, line in enumerate(lines) if _BLOCK_LINE_RE.match(line)]
    if not indices:
        return None
    return indices[0], indices[-1]


def _e1_single_tail_block(lines: list, bounds) -> Finding:
    label = "D1: disclosures form one block, and it ends the answer"
    if bounds is None:
        # D4: nothing fired, nothing rendered. Not a violation.
        return Finding("E-1", True, label)
    start, end = bounds
    problems = []
    stray = [lines[i].strip() for i in range(start, end + 1)
             if lines[i].strip() and not _BLOCK_LINE_RE.match(lines[i])]
    if stray:
        problems.append(f"prose interleaved with the block: {stray[0][:70]}")
    after = [line.strip() for line in lines[end + 1:] if line.strip()]
    if after:
        problems.append(f"content after the block: {after[0][:70]}")
    return Finding("E-1", not problems, label, "; ".join(problems))


def _e2_fixed_prefix(lines: list, bounds) -> Finding:
    """Every disclosure line carries the registered prefix exactly.

    Only the *form* is checkable here. Whether a line that carries no prefix
    at all is really a disclosure hiding in prose is a semantic question this
    file does not pretend to answer — what it does catch is the near miss:
    an indented `[i]`, an `[info]`, a `(i)`. Those are the shapes a prefix
    drifts into, and an unrecognized prefix means the block is invisible to
    every other check here, which is why a near miss is a failure rather than
    a formatting quibble."""
    label = f"D3: every disclosure line carries the exact prefix {PREFIX!r}"
    problems = []
    for line in lines:
        if not line.strip() or _BLOCK_LINE_RE.match(line):
            continue
        if _PREFIX_ATTEMPT_RE.match(line):
            problems.append(f"malformed disclosure prefix: {line.strip()[:70]}")
    return Finding("E-2", not problems, label, "; ".join(problems))


def _e3_line_cap(lines: list, bounds) -> Finding:
    label = f"D5: the disclosure block is at most {LINE_CAP} lines"
    if bounds is None:
        return Finding("E-3", True, label)
    start, end = bounds
    count = sum(1 for i in range(start, end + 1) if _BLOCK_LINE_RE.match(lines[i]))
    return Finding("E-3", count <= LINE_CAP, label,
                   "" if count <= LINE_CAP else f"{count} lines, cap is {LINE_CAP}")


def _normalize(line: str) -> str:
    """Fold a disclosure line to what it says, for E-4's repeat test.

    Case, punctuation and whitespace are dropped; wording is not. Two lines
    that state the same limitation in genuinely different words are not caught
    here, and this docstring says so rather than letting the check's name
    imply otherwise."""
    stripped = line[len(PREFIX):] if line.startswith(PREFIX) else line
    folded = "".join(char for char in unicodedata.normalize("NFKC", stripped).lower()
                     if not unicodedata.category(char).startswith("P")
                     and not char.isspace())
    return folded


def _e4_no_repeats(lines: list, bounds) -> Finding:
    label = "D6: no limitation is stated twice in the block"
    if bounds is None:
        return Finding("E-4", True, label)
    start, end = bounds
    seen, repeated = set(), None
    for index in range(start, end + 1):
        if not _BLOCK_LINE_RE.match(lines[index]):
            continue
        key = _normalize(lines[index])
        if key and key in seen and repeated is None:
            repeated = lines[index].strip()
        seen.add(key)
    return Finding("E-4", repeated is None, label,
                   "" if repeated is None else f"repeated line: {repeated[:70]}")


def _e5_no_internal_tokens(text: str, tokens) -> Finding:
    label = "C4: no engine payload token reaches the user"
    hits = [token for token in tokens
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", text)]
    return Finding("E-5", not hits, label,
                   "" if not hits else "engine vocabulary in the answer: " + ", ".join(hits[:5]))


def check_expression(text: str, tokens=None) -> list:
    """The E series against one conversational answer."""
    lines = _lines(text)
    bounds = _block_bounds(lines)
    tokens = internal_tokens() if tokens is None else tokens
    return [
        _e1_single_tail_block(lines, bounds),
        _e2_fixed_prefix(lines, bounds),
        _e3_line_cap(lines, bounds),
        _e4_no_repeats(lines, bounds),
        _e5_no_internal_tokens(text, tokens),
    ]


# ─────────────────────────── witness fixture ───────────────────────────

def check_fixture(path: pathlib.Path = DEFAULT_FIXTURE) -> list:
    """Classify the synthetic witnesses, the way `check_voice.py` does for
    V1–V9: a positive scene must produce no finding, and a negative scene
    must fail exactly the assertion it declares — no more, so a witness
    cannot pass by being broken in several ways at once."""
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"cannot load fixture: {error}"]
    if fixture.get("schema_version") != 1:
        return ["unsupported fixture schema_version"]
    if fixture.get("privacy") != "synthetic_only":
        return ["fixture must declare synthetic_only privacy"]
    scenes = fixture.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return ["scenes must be a non-empty list"]

    tokens = internal_tokens()
    seen, problems, covered = set(), [], set()
    for scene in scenes:
        scene_id = scene.get("id") if isinstance(scene, dict) else None
        if not isinstance(scene_id, str) or not scene_id:
            problems.append("scene has no id")
            continue
        if scene_id in seen:
            problems.append(f"duplicated scene ID {scene_id!r}")
        seen.add(scene_id)
        answer = scene.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            problems.append(f"{scene_id}: missing non-empty answer")
            continue
        failed = {finding.assertion for finding in check_expression(answer, tokens)
                  if not finding.passed}
        if scene.get("kind") == "positive":
            if failed:
                problems.append(f"{scene_id}: positive scene failed {sorted(failed)}")
        elif scene.get("kind") == "negative":
            expected = scene.get("fails")
            if expected not in ASSERTIONS:
                problems.append(f"{scene_id}: unknown expected assertion {expected!r}")
                continue
            covered.add(expected)
            if failed != {expected}:
                problems.append(
                    f"{scene_id}: expected exactly {{{expected}}} to fail, got {sorted(failed)}")
        else:
            problems.append(f"{scene_id}: unknown scene kind {scene.get('kind')!r}")

    missing = set(ASSERTIONS) - covered
    if missing:
        problems.append(f"no negative witness for: {sorted(missing)}")
    return problems


def main() -> int:
    if len(sys.argv) == 1:
        problems = check_fixture()
        if problems:
            print("FAIL: expression witnesses")
            print("\n".join(f"- {problem}" for problem in problems))
            return 1
        print("PASS: expression witnesses")
        return 0
    source = sys.argv[1]
    text = sys.stdin.read() if source == "-" else pathlib.Path(source).read_text(encoding="utf-8")
    findings = check_expression(text)
    for finding in findings:
        print(finding)
    return 1 if any(not finding.passed for finding in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
