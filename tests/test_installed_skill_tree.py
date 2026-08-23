#!/usr/bin/env python3
"""The six non-negotiable boundaries, read from the tree an install actually gets (#838).

`README.md` installs this product by symlinking `skills/fomo-kernel/` alone.
An installed host's client therefore never receives `AGENTS.md`, `docs/`, or
anything else at the repository root -- `skills/fomo-kernel/` is the entire
world it can read before it answers a live decision. Every other test suite
in this repository runs against a full checkout, so a rule that lives only
at the repository root reads as present to all of them and is silently
absent for an installed user. That is exactly how #838 shipped unseen: the
six non-negotiable boundaries were stated once, in `AGENTS.md`, and nothing
that ran against the checkout could tell the difference between "stated"
and "stated somewhere an installed host can reach."

This suite is the difference. It builds its entire view of the world from
`skills/fomo-kernel/` and nothing else -- it must never read `AGENTS.md`,
`docs/`, or any file outside that directory -- and it fails the moment a
boundary's statement retreats to the repository root, or a file inside the
subtree cites `AGENTS.md` for a rule (a citation an installed reader cannot
follow, because the reader has no such file), or a markdown link resolves to
a path outside the subtree (#839 -- a relative link claims the file is right
there, and for an installed host it is not).

Deliberately independent of `tests/test_doc_language.py` and
`tests/test_repo_hygiene.py`: importing either would reintroduce a
root-reading suite as a load-bearing part of this one's logic, and the whole
point is that this suite proves nothing those two suites already prove.
"""
import posixpath
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_TREE = ROOT / "skills" / "fomo-kernel"

# Six entries, each a short label plus the (relative file, required phrases)
# pairs that carry it inside the installed subtree. Every phrase below is an
# exact substring copied from the on-disk files -- verified, not retyped from
# memory -- so a wording edit that drops the substance, not just the words,
# reddens this suite rather than a paraphrase drifting silently.
BOUNDARIES = [
    {
        "label": "product-state-only-through-review-cli",
        "files": {
            "SKILL.md": [
                "only through the `engine/review.py` CLI",
            ],
            "references/agent-boundaries.md": [
                "Call another `engine/*` script or import engine modules directly",
            ],
        },
    },
    {
        "label": "engine-owns-every-portfolio-fact",
        "files": {
            "SKILL.md": [
                "every portfolio-derived number, the portfolio basis, every "
                "identity, every `rule_effect`, and every state transition "
                "is the engine's",
                "never recompute, adjust, or fill its gaps",
            ],
        },
    },
    {
        "label": "four-states-never-promoted",
        "files": {
            "SKILL.md": [
                "considered, user-resolved, user-reported execution, "
                "transaction-proven execution",
                "only a transaction record proves a trade happened",
                "the user's report, not proof",
            ],
        },
    },
    {
        # Pinned as complete sentences, terminal punctuation included, not
        # fragments: an adversarial probe showed that fragment pins survive an
        # exception-clause rewrite ("never reach a third party or cloud memory
        # unless the user asks") with the gate green. A full-sentence pin makes
        # that rewrite break the pin; a *contradicting sentence added beside*
        # the pinned one is still review's to catch -- no phrase gate can
        # prove the absence of a contradiction elsewhere in the file.
        "label": "private-data-stays-local",
        "files": {
            "SKILL.md": [
                "Trades, holdings, amounts, motives, and cards never reach a "
                "third party or cloud memory.",
                "The review card is private to the user — local files, "
                "terminal output, and private-by-default in-client rendering "
                "are fine; publishing is not.",
                "Anything public — a shared card, an example, a bug report — "
                "carries synthetic data only.",
            ],
            "references/agent-boundaries.md": [
                "or let trades, holdings, amounts, motives, or cards reach a "
                "third party or cloud memory.",
                "Private data and durable state stay local; anything public "
                "carries synthetic data only.",
            ],
        },
    },
    {
        "label": "recommendation-first",
        "files": {
            "SKILL.md": [
                "then recommend what to do",
                "Ask only decision-changing questions",
                "machine anchors and engine narration nowhere",
                "A found event never becomes the user's motive until they "
                "confirm it is.",
                "Never claim what the user did or will do.",
            ],
        },
    },
    {
        "label": "persist-only-through-canonical-writer",
        "files": {
            "SKILL.md": [
                "Persistent `consider` records the evaluation",
                "Exploration leaves no canonical evaluation row",
            ],
            "references/agent-boundaries.md": [
                "Assemble engine card or state artifacts by hand",
                "Persist only through a canonical engine writer, and only "
                "when a named later reader exists — no field written for a "
                "reader nobody built.",
            ],
        },
    },
]

# The routed boundary file must be reachable, not merely present: a reference
# nothing names is text nothing loads (the `profile.md` precedent --
# registered, tested, named by no runtime surface, and so never written to in
# the product's lifetime). `SKILL.md` is the one file an installed host is
# guaranteed to load, so it must name the file that carries the boundary
# statements `SKILL.md` itself does not restate. An adversarial probe deleted
# `SKILL.md`'s single pointer to this file and every suite stayed green; this
# constant and its checker are the answer.
ROUTED_BOUNDARY_FILE = "references/agent-boundaries.md"

# Every relative path any boundary above reads, in one place, so the tree
# reader and the mutation fixtures agree on what "the subtree" means.
BOUNDARY_FILES = sorted({rel for boundary in BOUNDARIES for rel in boundary["files"]})


def load_boundary_sources(tree):
    """``{relative_path: text}`` for every file a boundary reads, under ``tree``.

    A file that does not exist under ``tree`` is simply absent from the
    result -- callers treat a missing key the same as an empty string, which
    is what lets a tree with no `SKILL.md` at all fail every boundary that
    names it, rather than crashing.
    """
    sources = {}
    for rel in BOUNDARY_FILES:
        path = tree / rel
        if path.is_file():
            sources[rel] = path.read_text(encoding="utf-8")
    return sources


def collapsed(text):
    """One-space form of ``text``, for phrase matching and mutation alike.

    A cosmetic hard-wrap re-flow of a pinned sentence must never read as a
    lost boundary, and a mutation must redden for the rule it removed, not
    for the line breaks around it -- the same reason `test_doc_language.py`
    matches its floor phrases collapsed.
    """
    return " ".join(text.split())


def phrase_present(sources, rel, phrase):
    """Whether ``phrase`` appears in ``sources[rel]``, whitespace-collapsed.

    The one membership definition every checker and mutation arm in this
    module shares -- two inline copies of this check drifting apart would let
    the live gate and its mutation proof disagree on what "present" means.
    """
    return collapsed(phrase) in collapsed(sources.get(rel, ""))


def boundary_violations(sources):
    """``(label, file, phrase)`` for every phrase missing from ``sources``.

    ``sources`` is a ``{relative_path: text}`` mapping, so a mutation arm can
    hand this a doctored copy of the real text without touching disk.
    """
    violations = []
    for boundary in BOUNDARIES:
        for rel, phrases in boundary["files"].items():
            for phrase in phrases:
                if not phrase_present(sources, rel, phrase):
                    violations.append((boundary["label"], rel, phrase))
    return violations


def reachability_violations(sources):
    """Reasons the routed boundary file is unreachable under ``sources``.

    Presence is not delivery: the boundaries `references/agent-boundaries.md`
    carries reach an installed host only through `SKILL.md` naming it, because
    `SKILL.md` is the sole file that host loads unprompted.
    """
    problems = []
    if not phrase_present(sources, "SKILL.md", f"`{ROUTED_BOUNDARY_FILE}`"):
        problems.append(
            f"SKILL.md does not name `{ROUTED_BOUNDARY_FILE}` -- the "
            "boundaries that file carries are text nothing loads")
    if ROUTED_BOUNDARY_FILE not in sources:
        problems.append(f"{ROUTED_BOUNDARY_FILE} is missing from the tree")
    return problems


# Every file type an installed reader can be routed to as instructions,
# contract, or template. ``.py`` is excluded deliberately, wherever it sits:
# boundary 1 keeps an installed agent out of engine internals, so an
# `AGENTS.md` mention in a code comment is maintainer-facing rationale, never
# an instruction an installed reader follows. The maintainer guide's
# mirrored-surfaces row states the same scope.
TEXT_RUNTIME_SUFFIXES = (".md", ".json", ".html", ".txt")


def iter_text_runtime_sources(tree):
    """``(relative_path, text)`` for every text runtime surface under ``tree``.

    Scope is ``TEXT_RUNTIME_SUFFIXES`` -- the card templates and
    `requirements.txt` are runtime surfaces too, not only the markdown.
    Paths are keyed in POSIX form so assertions match on every OS, and text
    is read strictly: an undecodable byte in a runtime surface is its own
    defect, not something to skip past silently.
    """
    for path in sorted(tree.rglob("*")):
        if path.is_file() and path.suffix in TEXT_RUNTIME_SUFFIXES:
            yield path.relative_to(tree).as_posix(), path.read_text(
                encoding="utf-8")


def citation_violations(sources):
    """``"file:line: text"`` for every line naming ``AGENTS.md`` in ``sources``.

    ``sources`` is an iterable of ``(relative_path, text)`` pairs, so a
    mutation arm can inject a citation into one file's text without touching
    disk. An installed reader has no `AGENTS.md` to open, so a rule stated
    only as "see AGENTS.md" is a dead pointer for that reader, not a rule.
    """
    return [
        f"{rel}:{number}: {line.strip()[:120]}"
        for rel, text in sources
        for number, line in enumerate(text.splitlines(), 1)
        if "AGENTS.md" in line
    ]


MARKDOWN_LINK_TARGET = re.compile(r"\]\(([^)\s]+)\)")
# An upward-relative path into a maintainer directory, in any syntax -- link
# target or inline code. `../schemas/...` never matches: schemas/ is inside
# the tree, and the directories named here are the maintainer layer.
UPWARD_MAINTAINER_PATH = re.compile(r"(?:\.\./)+(?:docs|evals|tests)/")


def escaping_reference_violations(md_sources):
    """``"file:line: reason"`` for every reference that escapes the tree (#839).

    The installed tree may *name* its out-of-tree authorities -- "the
    repository's `docs/expression-contract.md`" is a citation, like naming a
    book -- because every rule an answer needs is stated in-tree and the
    authority reference is provenance for maintainers. What it may not do is
    *link* there: a relative link promises the reader the file is right
    there, and on an installed host that promise is false. Two checks: every
    markdown link target must resolve inside the tree, and no upward-relative
    path into a maintainer directory may appear in any syntax.
    """
    violations = []
    for rel, text in md_sources:
        base = posixpath.dirname(rel)
        for number, line in enumerate(text.splitlines(), 1):
            for match in MARKDOWN_LINK_TARGET.finditer(line):
                target = match.group(1).split("#", 1)[0]
                if not target or target.startswith(
                        ("http://", "https://", "mailto:")):
                    continue
                resolved = posixpath.normpath(posixpath.join(base, target))
                if resolved.startswith(".."):
                    violations.append(
                        f"{rel}:{number}: link escapes the installed tree: "
                        f"{match.group(1)}")
            if UPWARD_MAINTAINER_PATH.search(line):
                violations.append(
                    f"{rel}:{number}: upward path into a maintainer "
                    f"directory: {line.strip()[:100]}")
    return violations


def markdown_sources(tree):
    return [(rel, text) for rel, text in iter_text_runtime_sources(tree)
            if rel.endswith(".md")]


def test_every_boundary_phrase_is_present_in_the_installed_tree():
    """Every one of the six boundaries' phrases must be readable from
    `skills/fomo-kernel/` alone -- the entire tree an installed host gets.
    """
    violations = boundary_violations(load_boundary_sources(SKILL_TREE))
    assert not violations, (
        "a non-negotiable boundary is not readable from inside "
        "skills/fomo-kernel/ (an installed host has nothing else to read):\n  "
        + "\n  ".join(
            f"{label}: {rel} is missing {phrase!r}"
            for label, rel, phrase in violations
        )
    )


def test_the_routed_boundary_file_is_reachable_from_skill_md():
    """`references/agent-boundaries.md` carries boundary statements `SKILL.md`
    does not restate (the CLI whitelist, the hand-assembly ban, the canonical
    writer rule). It must exist *and* be named by `SKILL.md`, or those
    boundaries become text nothing loads on an installed host.
    """
    violations = reachability_violations(load_boundary_sources(SKILL_TREE))
    assert not violations, (
        "the routed boundary file is unreachable from SKILL.md:\n  "
        + "\n  ".join(violations)
    )


def test_no_file_under_the_installed_tree_cites_agents_md():
    """An installed reader has no `AGENTS.md` -- a citation pointing at it
    from inside `skills/fomo-kernel/` is dead on arrival for that reader.
    """
    violations = citation_violations(iter_text_runtime_sources(SKILL_TREE))
    assert not violations, (
        "skills/fomo-kernel/ cites AGENTS.md, which an installed host cannot "
        "read:\n  " + "\n  ".join(violations)
    )


def test_no_reference_escapes_the_installed_tree():
    """#839: nine files linked `../../docs/...` and one linked `../../evals/`
    -- seventeen dead pointers on an installed host, the same delivery class
    as #838's unreachable `AGENTS.md`. Authorities are now cited by
    repository path in prose; the rules those authorities own are stated
    in-tree (the answer shape is SKILL.md's own projection, each reference
    opens with its exemplar, card structure is enforced by the engine
    renderer), so no link needs to leave the tree.
    """
    violations = escaping_reference_violations(markdown_sources(SKILL_TREE))
    assert not violations, (
        "references escape skills/fomo-kernel/ (dead on an installed "
        "host):\n  " + "\n  ".join(violations)
    )


def test_escape_gate_mutations_are_caught():
    """(a) the real tree is green; (b) an escaping link injected into a
    nested file reddens naming that file; (c) an upward inline-code path with
    no link syntax reddens; (d) an escaping link from a top-level file (one
    `../` is enough there) reddens; (e) an in-tree `../schemas/` link stays
    green -- the negative arm that proves the gate rejects escapes, not
    relative links as such.
    """
    real = markdown_sources(SKILL_TREE)
    assert not escaping_reference_violations(real), (
        "fixture assumption broken: the real tree already escapes")

    nested = "references/agent-boundaries.md"
    assert any(rel == nested for rel, _ in real), (
        "fixture assumption broken: the markdown walk did not reach "
        f"{nested}")

    def with_appended(rel_target, extra):
        return [(rel, text + extra if rel == rel_target else text)
                for rel, text in real]

    linked = escaping_reference_violations(
        with_appended(nested, "\nSee [the contract](../../docs/expression-contract.md).\n"))
    assert any(v.startswith(f"{nested}:") for v in linked), (
        "an escaping link injected into a nested file stayed green")

    coded = escaping_reference_violations(
        with_appended(nested, "\nSee `../../docs/expression-contract.md`.\n"))
    assert any(v.startswith(f"{nested}:") for v in coded), (
        "an upward inline-code path with no link syntax stayed green")

    top = escaping_reference_violations(
        with_appended("SKILL.md", "\nSee [the floor](../AGENTS.md).\n"))
    assert any(v.startswith("SKILL.md:") for v in top), (
        "a top-level file escaping with a single ../ stayed green")

    in_tree = escaping_reference_violations(
        with_appended(nested, "\nSee [the schema](../schemas/trade-premise.schema.json).\n"))
    assert not in_tree, (
        "an in-tree ../schemas/ link was flagged -- the gate must reject "
        f"escapes, not relative links as such: {in_tree}")


def test_boundary_and_citation_checks_are_mutation_proof():
    """(a) the real tree is green; (b) removing any one boundary phrase
    reddens the checker for that exact (label, file, phrase); (c) injecting
    an AGENTS.md citation into a real file's text reddens the citation
    checker for that file; (d) a tree with no SKILL.md at all is red.

    Follows the section-scoped mutation style of `test_doc_language.py`: each
    mutation targets one phrase and asserts the failure names it, not merely
    that some violation somewhere fired -- a checker that always reports
    everything wrong would pass a looser assertion for the wrong reason.
    """
    real_sources = load_boundary_sources(SKILL_TREE)
    assert not boundary_violations(real_sources), (
        "fixture assumption broken: the real installed tree is not green")

    for boundary in BOUNDARIES:
        for rel, phrases in boundary["files"].items():
            for phrase in phrases:
                assert phrase_present(real_sources, rel, phrase), (
                    f"fixture assumption broken: {phrase!r} not found "
                    f"in {rel}")
                # Mutate the collapsed form, the same form the checker
                # matches on -- removing the raw phrase would miss a pin
                # that the file wraps across lines.
                mutated = dict(real_sources)
                mutated[rel] = collapsed(real_sources[rel]).replace(
                    collapsed(phrase), "", 1)
                violations = boundary_violations(mutated)
                assert (boundary["label"], rel, phrase) in violations, (
                    f"removing {phrase!r} from {rel} did not redden "
                    f"boundary {boundary['label']!r}")

    real_md_json = dict(iter_text_runtime_sources(SKILL_TREE))
    # Two injection targets: the tree's top level, and a file down inside
    # `references/` -- the second pins that the walk actually descends into
    # subdirectories, so narrowing `rglob` to a flat listing reddens here
    # instead of silently shrinking what the citation check covers.
    for target_rel in ("SKILL.md", ROUTED_BOUNDARY_FILE):
        assert target_rel in real_md_json, (
            f"fixture assumption broken: the source walk did not reach "
            f"{target_rel}")
        mutated_pairs = [
            (rel, text if rel != target_rel
             else text + "\nper AGENTS.md invariant 4\n")
            for rel, text in real_md_json.items()
        ]
        injected_violations = citation_violations(mutated_pairs)
        assert any(
            v.startswith(f"{target_rel}:") for v in injected_violations), (
            f"injecting an AGENTS.md citation into {target_rel}'s text did "
            "not redden the citation checker")

    # Reachability arms: the real tree is green, dropping SKILL.md's single
    # pointer to the routed file is red, and losing the file itself is red.
    assert not reachability_violations(real_sources), (
        "fixture assumption broken: the routed boundary file is already "
        "unreachable")
    unpointed = dict(real_sources)
    unpointed["SKILL.md"] = collapsed(real_sources["SKILL.md"]).replace(
        f"`{ROUTED_BOUNDARY_FILE}`", "", 1)
    assert reachability_violations(unpointed), (
        "deleting SKILL.md's pointer to the routed boundary file would "
        "stay green")
    fileless = {rel: text for rel, text in real_sources.items()
                if rel != ROUTED_BOUNDARY_FILE}
    assert reachability_violations(fileless), (
        "a tree without the routed boundary file would stay green")

    with tempfile.TemporaryDirectory() as tmp:
        empty_tree = Path(tmp)
        assert boundary_violations(load_boundary_sources(empty_tree)), (
            "a tree with no SKILL.md at all would stay green")


_REGISTERED_TESTS = []  # populated by main(); see the registration self-check


def test_every_test_in_this_module_is_registered():
    """``main()`` runs a hand-maintained list, so a ``test_*`` added to this
    file but not to that list runs zero times while the module still prints
    PASS and exits 0 -- proven live by an adversarial probe during review,
    and the same incident `tests/test_doc_language.py` already records for
    itself. Same guard here: the defined set and the registered list must be
    identical.
    """
    defined = {name for name, value in sorted(globals().items())
               if name.startswith("test_") and callable(value)}
    registered = {test.__name__ for test in _REGISTERED_TESTS}
    assert registered, "main() has not populated the registry"
    assert defined == registered, (
        "test functions defined but never run (add them to main()'s list), "
        "or registered but not defined:\n"
        f"  unregistered: {sorted(defined - registered)}\n"
        f"  undefined: {sorted(registered - defined)}"
    )


def main():
    tests = [
        test_every_boundary_phrase_is_present_in_the_installed_tree,
        test_the_routed_boundary_file_is_reachable_from_skill_md,
        test_no_file_under_the_installed_tree_cites_agents_md,
        test_no_reference_escapes_the_installed_tree,
        test_escape_gate_mutations_are_caught,
        test_boundary_and_citation_checks_are_mutation_proof,
        test_every_test_in_this_module_is_registered,
    ]
    global _REGISTERED_TESTS
    _REGISTERED_TESTS = tests
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS: {len(tests)} installed skill tree boundary tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
