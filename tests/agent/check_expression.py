#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Check the deterministic half of the conversational expression contract
(offline, no dependencies).

Two of these assertions read an answer's text alone; two read an exemplar
scene, because what they decide is a relationship between an answer and the
shape its author declared for it.

**Text-only.** E-5 fails an answer that says an engine payload token at the
user (C4). E-6 (#830) fails one that renders a machine anchor: D7 says a fact
lives on exactly one floor, and the bottom floor is "payload only, never
rendered" — `basis.state_version`, a content hash whose only reader is a
mechanical QA comparison. Issue #825 retired E-1 through E-4, which classified
block position, a literal prefix, line count, and exact-string repetition; none
of them could tell whether a limitation mattered or where it read clearly.

**Exemplar-level (#832).** The answer pyramid — `docs/expression-contract.md`
§3 — is stated bindingly by the exemplar corpus rather than by prose, so its
oracle reads the corpus. E-7 fails a scene whose declared one-sentence answer
(`core`) does not appear in its answer's opening block: the top floor is the
one position the reader is guaranteed to read, and an answer that spends it on
anything else has no core, wherever the rest of it went. E-8 fails a scene
whose declared blocks are not all present in the answer in declared order, or
which declares no increment for a block, or which declares the same increment
twice — the increment gate's mechanical half.

**What E-7 and E-8 do not decide.** Whether the declared core is the *right*
call, and whether a declared increment was *worth* having, are read, not
matched. Two of §3's four named bans have no assertion at all — a system
default restated as insight, and a hedging couplet — and the corpus asserts
that gap instead of hiding it: a `counter` scene names the ban it violates and
must **pass** every assertion here, so the day an honest oracle for one of them
exists, that scene is what says the coverage boundary moved. A declared
increment is the author's claim about their own scene, exactly as `fails` is;
what this file verifies is that the declaration is faithful to the text (the
block really is there, at that point, exactly once) and internally coherent.

The token blacklist for E-5 is READ FROM THE SCHEMAS, never transcribed here.
`skills/fomo-kernel/schemas/*.schema.json` already enumerate the engine's own
vocabulary, so a new disclosure key or rule effect is covered the day it is
added rather than the day someone remembers this file. Hand-mirroring it is
what docs/maintainer-guide.md forbids and what borrowed enumerations go stale
from. The ban registry is read the same way, from the fixture itself.

Run:
  python3 tests/agent/check_expression.py <answer.md|->   # E-5 and E-6 only
  python3 tests/agent/check_expression.py                 # the whole corpus
Import:
  from check_expression import check_expression, check_fixture
  findings = check_expression(answer_text)   # list[Finding], text-only
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

# E-6's machine anchor, matched by SHAPE rather than by vocabulary — the one
# place this file deliberately does not read the schemas. A state version is
# `pb-v1:<sha256>` from `portfolio_basis.STATE_VERSION_PREFIX`, or
# `csv-v1:<sha256>` on the CSV-compatibility path, and a future anchor will
# have its own prefix; what none of them will stop being is a long run of hex.
# Deriving the prefixes would mean importing the engine, which this checker
# stays free of on purpose, and would go stale on the next prefix. A
# conversational answer about a trade contains no thirty-two-character hex run
# in any language, so the shape is exact enough to fail closed on and loose
# enough to survive a rename.
_MACHINE_ANCHOR = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])")


# Everything the fixture classifies. `TEXT_ASSERTIONS` is the subset a caller
# can run against an arbitrary answer, which is why the CLI path says so rather
# than printing four findings and inventing two of them.
TEXT_ASSERTIONS = ("E-5", "E-6")
EXEMPLAR_ASSERTIONS = ("E-7", "E-8")
ASSERTIONS = TEXT_ASSERTIONS + EXEMPLAR_ASSERTIONS

# §3.5's own words: "three to five canonical exemplars per conversational
# surface". Below the floor the corpus stops being a spec and becomes an
# anecdote; above the ceiling it stops being read, which is the failure the
# whole chapter is about.
MIN_EXEMPLARS, MAX_EXEMPLARS = 3, 5

SCENE_KINDS = ("positive", "negative", "counter")


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


def _e6_no_machine_anchor(text: str) -> Finding:
    label = "D7: no machine anchor is rendered to the user"
    hits = _MACHINE_ANCHOR.findall(text)
    return Finding("E-6", not hits, label,
                   "" if not hits else "machine anchor in the answer: " + hits[0][:16] + "…")


def check_expression(text: str, tokens=None) -> list:
    """The text-only expression assertions against one answer."""
    tokens = internal_tokens() if tokens is None else tokens
    return [_e5_no_internal_tokens(text, tokens), _e6_no_machine_anchor(text)]


# ──────────────────── the pyramid's two decidable halves ────────────────────

def _e7_the_core_leads(scene) -> Finding:
    """§3's top floor. The scene declares the one sentence that answers the
    question; this checks the answer actually opens on it. An opening block is
    everything before the first blank line, because that is what a reader gets
    without scrolling on every surface this product speaks on."""
    label = "§3 top floor: the declared one-sentence answer leads"
    answer = scene.get("answer") or ""
    core = scene.get("core")
    if not isinstance(core, str) or not core.strip():
        return Finding("E-7", False, label, "the scene declares no core")
    if core not in answer:
        return Finding("E-7", False, label, "the declared core is nowhere in the answer")
    if core not in answer.split("\n\n", 1)[0]:
        return Finding("E-7", False, label,
                       "the declared core is in the answer but not in its opening block")
    return Finding("E-7", True, label)


def _e8_every_block_adds_something_new(scene) -> Finding:
    """§3's increment gate, on the half a match can decide: the declared
    decomposition is faithful (each block's text is in the answer, at or after
    the previous one) and every block names a distinct increment. A block with
    no increment is a manufactured carrier; two blocks with one increment are
    the same point in a second form."""
    label = "§3 increment gate: each block is in place and adds a distinct increment"
    answer = scene.get("answer") or ""
    blocks = scene.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return Finding("E-8", False, label, "the scene declares no blocks")
    cursor, seen, problems = 0, {}, []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            problems.append(f"block {index} is not an object")
            continue
        text = block.get("text")
        if not isinstance(text, str) or not text.strip():
            problems.append(f"block {index} declares no text")
            continue
        position = answer.find(text, cursor)
        if position < 0:
            problems.append(
                f"block {index} is not in the answer at or after the block before it")
            continue
        cursor = position + len(text)
        adds = block.get("adds")
        if not isinstance(adds, str) or not adds.strip():
            problems.append(f"block {index} declares no increment")
            continue
        key = " ".join(adds.split()).casefold()
        if key in seen:
            problems.append(f"block {index} repeats the increment of block {seen[key]}")
        else:
            seen[key] = index
    return Finding("E-8", not problems, label, "; ".join(problems[:3]))


def check_exemplar(scene) -> list:
    """The assertions that need the scene's declared shape, not only its text."""
    return [_e7_the_core_leads(scene), _e8_every_block_adds_something_new(scene)]


# ─────────────────────────── witness fixture ───────────────────────────

def check_fixture(path: pathlib.Path = DEFAULT_FIXTURE) -> list:
    """Classify the exemplar corpus.

    A **positive** exemplar must produce no finding. A **negative** one must
    fail exactly the assertion it declares — no more, so a witness cannot pass
    by being broken in several ways at once. A **counter** exemplar names a ban
    nothing here can decide and must pass every assertion; that is the
    coverage boundary stated as a test rather than as a promise.

    Three corpus-level properties beyond the per-scene verdicts: every
    conversational surface carries §3.5's three to five positive exemplars,
    every declared ban is demonstrated by at least one scene, and every
    assertion has a negative witness."""
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"cannot load fixture: {error}"]
    if fixture.get("schema_version") != 2:
        return ["unsupported fixture schema_version"]
    if fixture.get("privacy") != "synthetic_only":
        return ["fixture must declare synthetic_only privacy"]
    surfaces = fixture.get("surfaces")
    if not isinstance(surfaces, dict) or not surfaces:
        return ["surfaces must be a non-empty object"]
    bans = fixture.get("bans")
    if not isinstance(bans, dict) or not bans:
        return ["bans must be a non-empty object"]
    scenes = fixture.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return ["scenes must be a non-empty list"]

    tokens = internal_tokens()
    seen, problems = set(), []
    covered, banned = set(), set()
    exemplars = {surface: 0 for surface in surfaces}
    for scene in scenes:
        scene_id = scene.get("id") if isinstance(scene, dict) else None
        if not isinstance(scene_id, str) or not scene_id:
            problems.append("scene has no id")
            continue
        if scene_id in seen:
            problems.append(f"duplicated scene ID {scene_id!r}")
        seen.add(scene_id)
        surface = scene.get("surface")
        if surface not in surfaces:
            problems.append(f"{scene_id}: unknown surface {surface!r}")
        answer = scene.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            problems.append(f"{scene_id}: missing non-empty answer")
            continue
        ban = scene.get("ban")
        if ban is not None:
            if ban not in bans:
                problems.append(f"{scene_id}: unknown ban {ban!r}")
            else:
                banned.add(ban)
        kind = scene.get("kind")
        if kind not in SCENE_KINDS:
            problems.append(f"{scene_id}: unknown scene kind {kind!r}")
            continue
        failed = {finding.assertion
                  for finding in check_expression(answer, tokens) + check_exemplar(scene)
                  if not finding.passed}
        if kind == "positive":
            if surface in exemplars:
                exemplars[surface] += 1
            if failed:
                problems.append(f"{scene_id}: positive exemplar failed {sorted(failed)}")
        elif kind == "negative":
            expected = scene.get("fails")
            if expected not in ASSERTIONS:
                problems.append(f"{scene_id}: unknown expected assertion {expected!r}")
                continue
            covered.add(expected)
            if failed != {expected}:
                problems.append(
                    f"{scene_id}: expected exactly {{{expected}}} to fail, got {sorted(failed)}")
        else:
            if ban is None:
                problems.append(
                    f"{scene_id}: a counter-exemplar must name the ban it demonstrates")
            if failed:
                problems.append(
                    f"{scene_id}: a counter-exemplar must pass every assertion — it "
                    f"exists to record that nothing mechanical catches its ban — but "
                    f"it failed {sorted(failed)}. If an assertion legitimately reaches "
                    f"this ban now, promote the scene to a negative witness.")

    for surface, count in sorted(exemplars.items()):
        if not MIN_EXEMPLARS <= count <= MAX_EXEMPLARS:
            problems.append(
                f"surface {surface!r} has {count} positive exemplars; "
                f"§3.5 asks for {MIN_EXEMPLARS} to {MAX_EXEMPLARS}")
    unbanned = sorted(set(bans) - banned)
    if unbanned:
        problems.append(f"no scene demonstrates the named ban: {unbanned}")
    missing = set(ASSERTIONS) - covered
    if missing:
        problems.append(f"no negative witness for: {sorted(missing)}")
    return problems


def main() -> int:
    if len(sys.argv) == 1:
        problems = check_fixture()
        if problems:
            print("FAIL: expression exemplars")
            print("\n".join(f"- {problem}" for problem in problems))
            return 1
        print("PASS: expression exemplars")
        return 0
    source = sys.argv[1]
    text = sys.stdin.read() if source == "-" else pathlib.Path(source).read_text(encoding="utf-8")
    findings = check_expression(text)
    for finding in findings:
        print(finding)
    print(f"(text-only: {', '.join(TEXT_ASSERTIONS)}. "
          f"{', '.join(EXEMPLAR_ASSERTIONS)} need a scene's declared core and blocks, "
          f"so they run over the corpus.)")
    return 1 if any(not finding.passed for finding in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
