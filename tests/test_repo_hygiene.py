#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repository-integrity checks that no behavioural suite can see (#575).

The class this file exists for: a defect that changes no behaviour, raises
nothing, and is therefore invisible to every other suite in `run_all.py`. The
first member is an unresolved merge conflict marker.

`skills/fomo-kernel/engine/revisit.py` carried a stray `<<<<<<< HEAD` on line
67 — inside `detect_exits`' docstring — from #562 until #574 removed it. Its
`=======` / `>>>>>>>` partners had already been cleaned up, so nothing was lost
and nothing failed: a marker inside a triple-quoted string is valid Python, and
44 green suites passed over it on every run for the whole window. This is a
public repository external users clone.

A reviewer catches it only if the diff hunk happens to include that line, and
the marker's whole nature is to sit in a region nobody re-reads. That is the
development-guide §7 argument for a mechanical observer rather than a rule.

Run:
  python3 tests/test_repo_hygiene.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Built by concatenation so this file does not contain a live marker and fail
# itself — the first thing a check like this gets wrong.
_LT, _GT, _EQ = "<" * 7, ">" * 7, "=" * 7
OPENING = _LT + " "          # `<<<<<<< HEAD`, `<<<<<<< branch-name`
CLOSING = _GT + " "          # `>>>>>>> origin/main`
DIVIDER = _EQ               # bare `=======`, checked only in company (below)

# Suffixes worth reading as text. A marker in a Markdown contract or a JSON
# schema is worse than one in a comment, so this is deliberately not
# Python-only; binaries and images are skipped because they cannot carry one
# meaningfully and would only add decode noise.
TEXT_SUFFIXES = (".py", ".md", ".json", ".jsonl", ".txt", ".yml", ".yaml",
                 ".html", ".css", ".js", ".sh", ".toml", ".cfg", ".csv")


def _tracked_text_files():
    done = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                          capture_output=True, text=True, check=True)
    paths = [p for p in done.stdout.split("\0") if p]
    assert paths, "no tracked files found — the enumeration is broken, not the tree"
    return [p for p in paths if p.endswith(TEXT_SUFFIXES)]


def _lines(path):
    with open(os.path.join(ROOT, path), encoding="utf-8", errors="replace") as handle:
        return handle.read().splitlines()


def _markers(lines):
    """(lineno, kind) for every conflict marker in one file's lines.

    The two three-way markers are checked unconditionally: `<<<<<<< ` and
    `>>>>>>> ` with the trailing space are unambiguous, and no legitimate text
    in this repository begins with them.

    A bare `=======` is different — it is a Markdown setext heading underline
    and a common ASCII rule, both of which appear in ordinary documentation. It
    counts only in a file that also carries an opening or closing marker, where
    it is corroborated rather than guessed. A checker that cried wolf on every
    horizontal rule would be deleted, and then the class comes back.
    """
    found = [(n, kind) for n, line in enumerate(lines, 1)
             for kind, prefix in (("opening", OPENING), ("closing", CLOSING))
             if line.startswith(prefix)]
    if found:
        found += [(n, "divider") for n, line in enumerate(lines, 1)
                  if line.rstrip() == DIVIDER]
    return sorted(found)


def test_no_tracked_text_file_carries_a_conflict_marker():
    """One unresolved marker is enough. It does not have to be a whole
    conflict: #562's was a lone opening line whose partners had been removed,
    which is why this reports each marker rather than looking for a triple."""
    hits = []
    for path in _tracked_text_files():
        for lineno, kind in _markers(_lines(path)):
            hits.append(f"{path}:{lineno} ({kind})")
    assert not hits, (
        "unresolved merge conflict marker(s) in tracked text:\n  "
        + "\n  ".join(hits))


# Independently constructed from character codes, NOT from OPENING/CLOSING/
# DIVIDER above. An oracle that builds its input out of the constant under test
# is self-consistent rather than correct: a `CLOSING` typo'd to `>>>>>>>@` also
# typos the fixture, `_markers` still matches it, and the form goes permanently
# unwatched with the suite green. Verified by mutation, which is how this was
# found — the first version of this file made exactly that mistake.
_ORACLE_OPEN = chr(0x3C) * 7 + " "
_ORACLE_CLOSE = chr(0x3E) * 7 + " "
_ORACLE_DIVIDER = chr(0x3D) * 7


def test_the_check_recognises_each_marker_form():
    """The checker's own oracle. Without this, a typo in one prefix leaves that
    form permanently unwatched while the suite stays green — the shape
    development-guide §7 calls a checker that is not evidence."""
    conflicted = [
        "def f():",
        _ORACLE_OPEN + "HEAD",
        "    return 1",
        _ORACLE_DIVIDER,
        "    return 2",
        _ORACLE_CLOSE + "origin/main",
    ]
    kinds = [kind for _, kind in _markers(conflicted)]
    assert kinds == ["opening", "divider", "closing"], kinds

    # #562's actual shape: the opening line alone, partners already cleaned up.
    lone = ["def f():", '    """doc', _ORACLE_OPEN + "HEAD", "    more doc", '    """']
    assert [kind for _, kind in _markers(lone)] == ["opening"], lone

    # And the constants the scan actually runs with are those forms, so a
    # rename or a stray character in one is caught here rather than in the
    # silence of a file that stops being reported.
    assert (OPENING, CLOSING, DIVIDER) == (_ORACLE_OPEN, _ORACLE_CLOSE, _ORACLE_DIVIDER), \
        (OPENING, CLOSING, DIVIDER)


def test_a_divider_alone_is_not_a_conflict():
    """The false-positive arm, and the reason `=======` is corroborated rather
    than trusted: a setext heading underline and an ASCII rule are ordinary
    documentation. Reported on its own, this check would fire on real files in
    this repository and get switched off."""
    heading = ["A section title", _ORACLE_DIVIDER, "", "Body text.", _ORACLE_DIVIDER]
    assert _markers(heading) == [], heading


def test_the_documentation_corpus_is_actually_being_read():
    """A guard against the enumeration silently narrowing to nothing. The bug
    that makes a scan vacuous is never in its assertion — it is in the file
    list, and an empty list passes every assertion above."""
    paths = _tracked_text_files()
    assert len([p for p in paths if p.endswith(".py")]) > 30, len(paths)
    assert len([p for p in paths if p.endswith(".md")]) > 20, len(paths)
    assert "AGENTS.md" in paths and "skills/fomo-kernel/SKILL.md" in paths, \
        "the two guaranteed-delivery entry points must be in scope"


def _suites_that_drive_a_priced_route():
    """Every test file that runs `review.py consider|prepare` as a subprocess.

    Derived from the sources rather than hand-listed, so a suite added tomorrow
    is covered without anyone remembering to add it — the difference this
    repository keeps paying for between a rule in a primitive and a rule at a
    call site.
    """
    found = []
    tests_dir = os.path.join(ROOT, "tests")
    for name in sorted(os.listdir(tests_dir)):
        if not name.endswith(".py") or name == "offline_posture.py":
            continue
        with open(os.path.join(tests_dir, name), encoding="utf-8") as handle:
            src = handle.read()
        if "subprocess.run" not in src:
            continue
        if '"consider"' in src or '"prepare"' in src:
            found.append(name)
    return found


def test_every_suite_that_drives_a_priced_route_declares_its_market_posture():
    """#620: a suite's answer may not depend on how it was launched.

    `run_all.py` set `TR_OFFLINE=1` for the suites it spawned, which covered
    every run through it and no run around it. `python3 tests/test_consider.py`
    — the ordinary inner loop when iterating on one file — therefore resolved
    live closes and reported nine failures that `run_all.py` did not, on the same
    commit and the same machine. Two more were one market move away in
    `test_split_basis.py`.

    This is not about an offline product; nobody reviews trades without a
    network. It is that a test asserting a position is 51% of a book must not
    take today's price as an input, or a red run stops telling you whether your
    own change broke something.

    Checked mechanically because the failure is silent: a new suite that omits
    the declaration is green on the maintainer's machine, green in CI (no
    yfinance there), and wrong only when someone runs it directly on a machine
    that has it.
    """
    suites = _suites_that_drive_a_priced_route()
    assert len(suites) >= 7, (
        f"the scan narrowed to {suites} — an empty or shrunken list passes every "
        "assertion below, which is the way a check like this dies")
    missing = []
    for name in suites:
        with open(os.path.join(ROOT, "tests", name), encoding="utf-8") as handle:
            src = handle.read()
        if "offline_posture.apply()" not in src:
            missing.append(name)
    assert not missing, (
        "these suites drive a priced route without declaring a market posture, so running "
        f"them directly reaches the network and running them via run_all.py does not: {missing}. "
        "Add `offline_posture.apply()` beside the imports; see tests/offline_posture.py.")


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS all {len(tests)} repository hygiene tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
