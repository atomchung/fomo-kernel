#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check C4 of the conversational expression contract (offline,
deterministic).

Issue #825 retired E-1 through E-4. They classified block position, a literal
prefix, line count, and exact-string repetition; none could tell whether a
limitation mattered or where it read clearly. E-5 remains because leaking an
engine payload token is an exact, deterministic product defect.

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
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "skills" / "fomo-kernel" / "schemas"
DEFAULT_FIXTURE = ROOT / "tests" / "agent" / "expression-witnesses.json"

# E-5's schemas. Two files, not the whole directory: these are the two that
# enumerate what a `consider` answer's payload can say, which is exactly the
# vocabulary C4 keeps out of prose. Widening to every schema would drag in
# vocabularies no answer surface ever reads.
_TOKEN_SCHEMAS = ("evaluation-challenge.schema.json", "trade-evaluation.schema.json")

# Tokens that are engine vocabulary but also ordinary product words a
# conversational answer legitimately uses. Kept explicitly rather than by
# loosening the pattern, so the exemption is visible and reviewable.
_TOKEN_EXEMPT = frozenset({"as_of"})


ASSERTIONS = ("E-5",)


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


def _e5_no_internal_tokens(text: str, tokens) -> Finding:
    label = "C4: no engine payload token reaches the user"
    hits = [token for token in tokens
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", text)]
    return Finding("E-5", not hits, label,
                   "" if not hits else "engine vocabulary in the answer: " + ", ".join(hits[:5]))


def check_expression(text: str, tokens=None) -> list:
    """The deterministic expression assertion against one answer."""
    tokens = internal_tokens() if tokens is None else tokens
    return [_e5_no_internal_tokens(text, tokens)]


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
