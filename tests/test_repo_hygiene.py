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

A second member joined for the same reason (#637): CI's own trigger
configuration in `.github/workflows/tests.yml` is itself unreached by any
behavioural suite, and a regression there -- an unfiltered `on.push`, or
yfinance reinstalled into the blocking job -- changes no engine behaviour and
raises nothing either. It is read here as text, not executed, because this
suite is offline and does not run GitHub Actions.

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


WORKFLOW_REL_PATH = os.path.join(".github", "workflows", "tests.yml")


def _nested_block(lines, key):
    """Lines nested one level under a `key:` mapping entry, the key line
    itself excluded, ending at the first sibling back at the same indent
    (or shallower) that `lines` itself starts at.

    This is not a YAML parser -- it is exactly as much indentation tracking
    as a hand-authored GitHub Actions workflow needs (block mappings and
    block sequences only: no flow style, no anchors, no multi-document) and
    no more. A real parser is a dependency this offline, stdlib-only suite
    does not carry; the tradeoff is that this scanner would mis-read a
    general YAML document, but `.github/workflows/tests.yml` is small,
    authored by this repository, and its shape is exactly what
    `test_the_workflow_block_scanner_reads_only_its_own_nesting_level`
    below exercises against a fixture built to fool a naive substring
    search. Returns `None` if `key:` is not found at `lines`' own indent,
    and `[]` if it is found but nothing is nested under it -- the two are
    different findings (a removed trigger vs. one that lost its filter) and
    a caller that conflated them would misreport which one regressed.
    """
    top_indent = None
    for line in lines:
        if line.strip() != "":
            top_indent = len(line) - len(line.lstrip(" "))
            break
    if top_indent is None:
        return None
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == "":
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent < top_indent:
            break
        if indent == top_indent and line.strip() == f"{key}:":
            start = idx + 1
            break
    if start is None:
        return None
    end = start
    while end < len(lines):
        line = lines[end]
        if line.strip() == "":
            end += 1
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= top_indent:
            break
        end += 1
    return lines[start:end]


def _sequence_items(lines):
    """Values of a YAML block sequence (`- item`) among already-nested lines."""
    return [line.strip()[2:].strip() for line in lines if line.strip().startswith("- ")]


def _installs_yfinance(job_lines):
    """Whether a job's own lines contain a real `pip install ... yfinance`,
    never a bare substring match. The `test` job's own comment explains, in
    prose, why yfinance is deliberately *not* installed there -- and that
    sentence contains the word "yfinance" -- so a naive `"yfinance" in line`
    scan reports the job as violating the posture its own comment is
    documenting. This is the type-1 fake-green shape once removed: not
    "asserts structure, not content" but its mirror, "matched text, not
    what the text means."
    """
    return any(
        "pip install" in line and "yfinance" in line and not line.strip().startswith("#")
        for line in job_lines
    )


def test_the_workflow_block_scanner_reads_only_its_own_nesting_level():
    """`_nested_block`'s own oracle (development-guide.md #2: a checker with
    no failing-mutation proof is not evidence). The fixture plants the two
    things that would fool a scanner that degraded to substring search: a
    coincidental `jobs:`/`test:` line sitting deep inside another job's
    `run:` block, and a bare `push:` key with nothing nested under it. A
    scanner not tracking indentation would truncate the real `test` job
    early or bleed the sibling job's content across the boundary, and still
    pass today's file -- this fixture is what would catch it.
    """
    fixture = """\
name: demo
on:
  push:
  pull_request:
  schedule:
    - cron: "1 2 3 4 5"
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - name: a
        run: |
          echo "jobs:"
          echo "test:"
  network-smoke:
    if: something
    steps:
      - name: b
        run: pip install yfinance
""".splitlines()

    on_block = _nested_block(fixture, "on")
    assert on_block == [
        "  push:", "  pull_request:", "  schedule:", '    - cron: "1 2 3 4 5"',
    ], on_block

    # Found, empty -- distinct from not-found, because a push trigger that
    # lost its branch filter (found, empty) is the #637 regression itself,
    # while a push trigger removed outright is a different failure.
    assert _nested_block(on_block, "push") == []
    assert _nested_block(on_block, "no-such-key") is None
    assert _sequence_items(_nested_block(on_block, "schedule")) == ['cron: "1 2 3 4 5"']

    jobs_block = _nested_block(fixture, "jobs")
    test_block = _nested_block(jobs_block, "test")
    assert any('echo "jobs:"' in line for line in test_block), test_block
    assert any('echo "test:"' in line for line in test_block), test_block
    assert not _installs_yfinance(test_block), (
        "the test job's own block must not include the sibling "
        "network-smoke job's content")

    network_block = _nested_block(jobs_block, "network-smoke")
    assert _installs_yfinance(network_block), network_block


def test_ci_cannot_run_the_offline_suite_twice_on_one_ref():
    """#637: `tests/test_market_data.py` is offline and deterministic -- its
    docstring was always true -- but CI ran it once for `push` and once for
    `pull_request` on the same PR branch, and the two triggers checked out
    two different trees (a plain push vs. `actions/checkout`'s ephemeral
    merge commit) that both reported against the same `headSha`. A
    maintainer reading one red and one green square for "the same commit"
    cannot tell that one of them describes code that will never exist on
    `main`.

    Restricting `on.push` to `main` closes it: a PR branch then gets
    exactly one CI run -- `pull_request`, over the merge result, which is
    the signal this repository actually needs, since independently-green
    PRs going red in combination has happened here -- and `main` keeps its
    own post-merge signal from `push`, since `main` has silently gone red
    before with nobody noticing. Checked mechanically because the failure
    is silent: reopening `on.push` to another branch is a one-line diff
    that changes no Python, so nothing else in this suite would notice.
    """
    on_block = _nested_block(_lines(WORKFLOW_REL_PATH), "on")
    assert on_block is not None, "tests.yml has no top-level `on:` trigger mapping"
    push_block = _nested_block(on_block, "push")
    assert push_block, (
        "on.push has no nested `branches:` filter -- an unfiltered `push:` "
        "triggers on every branch, which reopens the #637 double run the "
        "moment a PR branch's own push and its pull_request run both fire "
        "on the same commit")
    branches_block = _nested_block(push_block, "branches")
    assert branches_block, (
        "on.push does not filter by `branches:` -- it still triggers on "
        "every branch push, reopening #637")
    branches = _sequence_items(branches_block)
    assert branches == ["main"], (
        f"on.push must trigger on exactly ['main'], found {branches!r} -- "
        "main is the only ref that needs push's post-merge signal; any "
        "other branch still races its own pull_request run on one commit")


def test_the_blocking_test_job_never_installs_yfinance():
    """The suite every PR and every push to `main` blocks on must stay
    offline by construction, per #620/#625's rule that a suite's answer may
    not depend on how it was launched -- #637's root cause was exactly this
    posture missing on one of the two launch paths CI itself uses.
    `market_data.resolve` only reaches its recorded-response fake when no
    real provider is importable; installing yfinance in the blocking job
    would let it silently start resolving live closes, reintroducing the
    flake and the network dependency #625 removed and this job's own
    comment already promises against.
    """
    jobs_block = _nested_block(_lines(WORKFLOW_REL_PATH), "jobs")
    assert jobs_block is not None, "tests.yml has no top-level `jobs:` mapping"
    test_job = _nested_block(jobs_block, "test")
    assert test_job, "tests.yml has no `test:` job -- the blocking suite has no home"
    assert not _installs_yfinance(test_job), (
        "the blocking `test` job installs yfinance -- every PR and every "
        "push to main blocks on this suite staying offline by construction "
        "(#620, #625, #637); yfinance belongs only in network-smoke")

    # Sanity check on the scan's own scope, against the real file rather
    # than only the synthetic fixture above: network-smoke installs
    # yfinance on purpose (#62). If this goes false, the boundary above may
    # be scanning the wrong block, not proving the invariant it claims to.
    network_smoke = _nested_block(jobs_block, "network-smoke")
    assert network_smoke and _installs_yfinance(network_smoke), (
        "network-smoke is supposed to install yfinance on purpose -- if it "
        "no longer does, the scope check above needs re-reading, not trust")


def test_a_failing_run_can_be_attributed_to_the_tree_it_tested():
    """#637: two CI triggers on one branch reported against the same
    `headSha` while actually testing two different trees, and nothing in
    either run's own output said so -- a maintainer had to open both runs
    and reconstruct which checkout produced which answer. The blocking job
    must print its own trigger and the commit it actually resolved before
    the suite that might fail, or a red run still hides the one fact that
    would have explained it: a step placed after a failed step does not run
    by default, so attribution after the suite step is attribution nobody
    ever sees on the run that needed it.
    """
    jobs_block = _nested_block(_lines(WORKFLOW_REL_PATH), "jobs")
    test_job = _nested_block(jobs_block, "test") or []
    joined = "\n".join(test_job)
    assert "git rev-parse HEAD" in joined, (
        "the blocking `test` job never resolves and prints `git rev-parse "
        "HEAD` -- a failing run cannot be attributed to the tree it "
        "actually checked out (a plain push vs. a PR's ephemeral merge "
        "commit), which is what made the #637 double run unreadable")
    assert "github.event_name" in joined, (
        "the blocking `test` job never prints its own trigger event -- "
        "attribution needs both which tree and which trigger produced it")

    attribution_at = next(
        (i for i, line in enumerate(test_job) if "git rev-parse HEAD" in line), None)
    suite_at = next(
        (i for i, line in enumerate(test_job) if "run_all.py" in line), None)
    assert attribution_at is not None and suite_at is not None
    assert attribution_at < suite_at, (
        "the tree-attribution step must run before `tests/run_all.py` -- "
        "a failing suite step aborts the job before a later step runs, so "
        "attribution placed after it never shows up on the run it was "
        "meant to explain")


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS all {len(tests)} repository hygiene tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
