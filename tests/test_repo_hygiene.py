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

# #492: the two halves of the old single `test` job. Both run the offline,
# deterministic registry -- they differ in who a red result implicates and in
# whether it blocks a product merge, never in whether the suites ran. Named
# once so the checks below cannot drift into watching one half.
OFFLINE_CI_JOBS = ("product-contract", "qa-eval-tooling")


def _registry_run_lines(job_lines):
    """Lines in a job that actually invoke the suite registry.

    Comments are excluded, and so is the `--scope` call, which asks the
    registry a path-ownership question and runs no suite. Both would otherwise
    be matched by a bare `"run_all.py" in line` scan -- the same
    "matched text, not what the text means" trap `_installs_yfinance` above
    documents, and it caught this file's first draft.
    """
    return [line for line in job_lines
            if "run_all.py" in line
            and not line.strip().startswith("#")
            and "--scope" not in line]


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
    never a bare substring match. Each offline job's own comment explains, in
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
    coincidental `jobs:`/`product-contract:` line sitting deep inside another
    job's `run:` block, and a bare `push:` key with nothing nested under it. A
    scanner not tracking indentation would truncate the real blocking job
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
  product-contract:
    runs-on: ubuntu-latest
    steps:
      - name: a
        run: |
          echo "jobs:"
          echo "product-contract:"
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
    test_block = _nested_block(jobs_block, "product-contract")
    assert any('echo "jobs:"' in line for line in test_block), test_block
    assert any('echo "product-contract:"' in line for line in test_block), test_block
    assert not _installs_yfinance(test_block), (
        "the blocking job's own block must not include the sibling "
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


def test_the_offline_ci_jobs_never_install_yfinance():
    """The suites every PR and every push to `main` reads must stay offline
    by construction, per #620/#625's rule that a suite's answer may not
    depend on how it was launched -- #637's root cause was exactly this
    posture missing on one of the two launch paths CI itself uses.
    `market_data.resolve` only reaches its recorded-response fake when no
    real provider is importable; installing yfinance in either offline job
    would let it silently start resolving live closes, reintroducing the
    flake and the network dependency #625 removed and each job's own
    comment already promises against.

    #492 split the old single `test` job in two. Both halves are checked,
    because a posture that holds on the blocking job and not on its sibling
    is the same one-launch-path-only gap #637 was.
    """
    jobs_block = _nested_block(_lines(WORKFLOW_REL_PATH), "jobs")
    assert jobs_block is not None, "tests.yml has no top-level `jobs:` mapping"
    for name in OFFLINE_CI_JOBS:
        job = _nested_block(jobs_block, name)
        assert job, f"tests.yml has no `{name}:` job -- an offline suite has no home"
        assert not _installs_yfinance(job), (
            f"the offline `{name}` job installs yfinance -- every PR and every "
            "push to main reads these suites on the promise that they are "
            "offline by construction (#620, #625, #637); yfinance belongs "
            "only in network-smoke")

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
    and reconstruct which checkout produced which answer. A job that runs
    suites must print its own trigger and the commit it actually resolved
    before the suite that might fail, or a red run still hides the one fact
    that would have explained it: a step placed after a failed step does not
    run by default, so attribution after the suite step is attribution
    nobody ever sees on the run that needed it.

    #492 made this apply to two jobs rather than one. Attribution on the
    blocking half only would leave the QA/eval half exactly as unreadable as
    the whole thing was before #637.
    """
    jobs_block = _nested_block(_lines(WORKFLOW_REL_PATH), "jobs")
    for name in OFFLINE_CI_JOBS:
        job = _nested_block(jobs_block, name) or []
        joined = "\n".join(job)
        assert "git rev-parse HEAD" in joined, (
            f"the `{name}` job never resolves and prints `git rev-parse "
            "HEAD` -- a failing run cannot be attributed to the tree it "
            "actually checked out (a plain push vs. a PR's ephemeral merge "
            "commit), which is what made the #637 double run unreadable")
        assert "github.event_name" in joined, (
            f"the `{name}` job never prints its own trigger event -- "
            "attribution needs both which tree and which trigger produced it")

        runs = _registry_run_lines(job)
        attribution_at = next(
            (i for i, line in enumerate(job)
             if "git rev-parse HEAD" in line and not line.strip().startswith("#")), None)
        suite_at = next((i for i, line in enumerate(job) if line in runs), None)
        assert attribution_at is not None and suite_at is not None, name
        assert attribution_at < suite_at, (
            f"the tree-attribution step in `{name}` must run before "
            "`tests/run_all.py` -- a failing suite step aborts the job "
            "before a later step runs, so attribution placed after it never "
            "shows up on the run it was meant to explain")


# --- #492: product / QA-eval ownership, and the ways it collapses back ------
#
# The split is the kind of thing that changes no behaviour and therefore no
# behavioural suite can see -- the class this file exists for. Every way it
# dies is silent: a suite added without an owner, a group that quietly becomes
# the whole registry, CI running `all` under a name that says `product`, or the
# path-ownership map used to decide whether QA/eval blocks drifting away from
# the registry it is supposed to describe. Each of those leaves a green run
# reporting a boundary that no longer exists.


def _registry():
    """The live `tests/run_all.py` module.

    Imported rather than re-parsed or re-declared: a second copy of the suite
    list is the exact defect #492 forbids, and it would drift in the direction
    that always wins -- the copy nobody runs stops matching the one that gates.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_all_registry", os.path.join(ROOT, "tests", "run_all.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_registry_run_scan_reads_intent_not_substrings():
    """`_registry_run_lines`' own oracle (development-guide #2).

    Its first draft matched every line containing `run_all.py`, which pulled
    in a *comment* naming the path and reported a correct workflow as broken.
    That is `_installs_yfinance`'s "matched text, not what the text means"
    trap one file later. The `--scope` call is excluded for a different
    reason: it asks the registry a path-ownership question and runs no suite,
    so counting it would let a job satisfy "runs its group" by never running
    anything.
    """
    job = [
        "    steps:",
        "      # tests/run_all.py holds the one ownership declaration this reads",
        "      - name: Decide scope",
        "        run: git diff --name-only base HEAD | python3 tests/run_all.py --scope",
        "      - name: Run suites",
        "        run: python3 tests/run_all.py --group product",
    ]
    assert _registry_run_lines(job) == [
        "        run: python3 tests/run_all.py --group product"], _registry_run_lines(job)
    assert _registry_run_lines(["      - run: echo unrelated"]) == []


def test_every_registered_suite_names_its_owner():
    """A new suite must not be able to join without someone deciding whether
    a red result there implicates the product or the maintainer's own
    evidence system. Registration is a three-tuple, so the omission is a
    crash at import rather than a silent default -- which is what "the two
    groups cannot collapse back together" has to mean mechanically, since a
    default would quietly re-merge them one suite at a time.
    """
    registry = _registry()
    assert registry.GROUPS == ("product", "qa-eval"), registry.GROUPS
    problems = []
    for entry in registry.SUITES:
        if len(entry) != 3:
            problems.append(f"{entry!r}: expected (label, path, group)")
            continue
        label, rel, group = entry
        if group not in registry.GROUPS:
            problems.append(f"{rel}: unknown owner {group!r}, expected one of {registry.GROUPS}")
    assert not problems, "\n  ".join(["registry entries without a valid owner:"] + problems)


def test_the_two_groups_partition_the_registry_exactly():
    """`product` + `qa-eval` must be `all` -- same suites, same order, no
    suite claimed twice, none claimed by neither. A suite in both groups is
    the copy #492's rollback note forbids.

    What this deliberately does not claim: it derives `all` from `SUITES`, so
    deleting an entry outright keeps the partition consistent and stays green.
    That hole is older than the split -- the flat registry had it too -- and
    closing it needs a separate answer to "which suites *should* be
    registered", which is not this check and not #492. Said here because a
    docstring that implied otherwise would be the more dangerous half.
    """
    registry = _registry()
    everything = [rel for _, rel in registry.suites_for("all")]
    product = [rel for _, rel in registry.suites_for("product")]
    qa_eval = [rel for _, rel in registry.suites_for("qa-eval")]

    assert everything == [rel for _, rel, _ in registry.SUITES], (
        "`--group all` no longer runs the registry in registry order")
    assert not set(product) & set(qa_eval), (
        f"suites claimed by both groups: {sorted(set(product) & set(qa_eval))} -- "
        "move the suite, never copy its assertions into both groups")
    assert sorted(product + qa_eval) == sorted(everything), (
        "the two groups do not add up to the whole registry: "
        f"missing {sorted(set(everything) - set(product) - set(qa_eval))}")


def test_neither_group_can_quietly_become_the_whole_registry():
    """The vacuous forms, pinned at both extremes.

    If `qa-eval` empties, `--group product` silently becomes the old flat
    57-suite gate and #492 is undone with every command still green. If
    `product` empties or shrinks to a rump, the blocking CI job passes
    because it is testing almost nothing -- the fake-green shape this
    repository has now shipped several times. Neither is visible from a
    passing run, so both ends are asserted rather than the middle.
    """
    registry = _registry()
    product = registry.suites_for("product")
    qa_eval = registry.suites_for("qa-eval")
    total = len(registry.SUITES)

    assert qa_eval, (
        "no suite is owned by `qa-eval` -- `--group product` is now the whole "
        "registry under a narrower name, which is the state #492 removed")
    assert len(product) < total, (
        "`--group product` selects the entire registry -- the split exists in "
        "the labels and not in what runs")
    # The product group is the one a merge blocks on. It must stay the
    # substantial half: QA/eval is maintainer tooling, and a repository whose
    # blocking evidence is a minority of its suites has moved product
    # coverage out of the gate rather than moved QA tooling out of the way.
    assert len(product) > total // 2, (
        f"only {len(product)}/{total} suites block a product merge -- product "
        "coverage has been reclassified out of the blocking gate, which is "
        "not what `qa-eval` is for")


def test_ci_runs_each_group_under_its_own_named_job():
    """The split has to reach CI, not only the runner. `product-contract`
    running the default `all` would restore one indistinguishable result
    under a name promising two, and nothing in the runner's own tests would
    notice -- the job name would still be there and every suite would still
    run.
    """
    jobs_block = _nested_block(_lines(WORKFLOW_REL_PATH), "jobs")
    expected = {"product-contract": "--group product", "qa-eval-tooling": "--group qa-eval"}
    for name, flag in expected.items():
        job = _nested_block(jobs_block, name)
        assert job, f"tests.yml has no `{name}:` job"
        runs = _registry_run_lines(job)
        assert runs, f"the `{name}` job never runs tests/run_all.py"
        # The whole command, not a substring of it. A looser check passes on
        # `run: echo python3 tests/run_all.py --group product`, which runs
        # nothing, and on `--group product --group all`, which runs everything
        # because argparse keeps the last value -- both leaving a job named for
        # one group quietly doing something else.
        for line in runs:
            command = line.split("run:", 1)[1].strip() if "run:" in line else line.strip()
            assert command == f"python3 tests/run_all.py {flag}", (
                f"the `{name}` job's registry command is {command!r}, not "
                f"'python3 tests/run_all.py {flag}' -- a job named for one "
                "group must run that group, and only that group")


def test_the_path_owner_map_and_the_registry_cannot_drift():
    """CI asks one question the suite list alone cannot answer -- "did this
    diff touch QA/eval's own files?" -- and #492 forbids answering it from a
    second hand-maintained list. `--scope` reads the registry, so this pins
    the join: every QA/eval-owned suite must be recognised as QA/eval-owned
    by the same function CI calls, and a product suite must not be.

    Drift here is silent and one-directional in the worst way: the map stops
    covering a QA file, QA/eval stops blocking its own changes, and the
    only symptom is a green square.
    """
    registry = _registry()
    for _, rel, group in registry.SUITES:
        owned = registry.qa_eval_owns(rel)
        assert owned == (group == "qa-eval"), (
            f"{rel} is registered as `{group}` but the path-ownership map says "
            f"qa_eval_owns={owned} -- CI's blocking decision and the runner's "
            "suite selection disagree about who owns this file")

    # The evidence system's subjects that are not whole directories. A path map
    # naming a file that no longer exists has stopped covering anything, and
    # the symptom is a green square -- so the names are checked against disk,
    # and each must really be outside the prefix list or it is dead weight
    # pretending to add coverage.
    for rel in registry.QA_EVAL_OWNED_FILES:
        assert os.path.isfile(os.path.join(ROOT, rel)), (
            f"QA_EVAL_OWNED_FILES names {rel}, which does not exist -- the "
            "blocking decision no longer covers whatever replaced it")
        assert not any(rel.startswith(p) for p in registry.QA_EVAL_OWNED_PREFIXES), (
            f"{rel} is already covered by a prefix; listing it separately "
            "suggests coverage the prefix list already provides")
        assert registry.qa_eval_owns(rel), rel

    assert registry.scope_of(["skills/fomo-kernel/engine/review.py"]) == "product-only"
    assert registry.scope_of([]) == "product-only"
    qa_owned_suite = next(rel for _, rel, group in registry.SUITES if group == "qa-eval")
    assert registry.scope_of(
        ["skills/fomo-kernel/engine/review.py", qa_owned_suite]) == "qa-eval-owned", (
        "one QA/eval-owned path in a mixed diff must make the whole diff "
        "QA/eval-owned -- the blocking decision fails closed or it is not one")


def test_formal_qa_preflight_asks_for_the_whole_registry_explicitly():
    """`docs/qa-runbook.md`'s preflight is the formal-acceptance path, and
    #492 kept `all` as the default only for compatibility. A path that
    inherits a default is a path that silently narrows the day the default
    changes, and this one's whole job is to be the complete evidence run --
    so it must name `all` rather than rely on being handed it.
    """
    source = open(os.path.join(ROOT, "skills", "fomo-kernel", "tools", "qa_preflight.py"),
                  encoding="utf-8").read()
    assert '"--group", "all"' in source or "'--group', 'all'" in source, (
        "qa_preflight runs the deterministic suite without naming a group -- "
        "the formal QA evidence path must request `all` explicitly, not "
        "inherit whatever the runner's default happens to be")


def main():
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS all {len(tests)} repository hygiene tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
