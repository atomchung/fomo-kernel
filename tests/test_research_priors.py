#!/usr/bin/env python3
"""Deterministic contract witnesses for the cross-route research baseline."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "fomo-kernel" / "SKILL.md"
REFERENCES = ROOT / "skills" / "fomo-kernel" / "references"
FRAMING = REFERENCES / "decision-framing.md"
CONSEQUENCE = REFERENCES / "trade-consequence.md"
FREEFORM = REFERENCES / "freeform-answers.md"
PRIORS = REFERENCES / "research-priors.md"

# #716. Every route that answers a decision, and the reference file `SKILL.md`
# routes that route through. The catalogue used to be linked from exactly one of
# them -- the one with *no* book -- so the product answered a user worse the
# more evidence they handed over: the baseline was reachable until they supplied
# a book, and then it was not. Reachability is checked as the whole walk
# (`SKILL.md` -> route file -> catalogue) rather than as "the catalogue exists",
# because a reference no runtime surface names is text nothing loads.
ROUTE_FILES = {
    "no recorded book": FRAMING,
    "consider": CONSEQUENCE,
    "freeform answers": FREEFORM,
}
# Derived, not hand-listed: ROUTE_FILES is the authoritative enumeration, and
# a route added or renamed there must not leave a second collection stale.
BOOK_BEARING = tuple(s for s in ROUTE_FILES if s != "no recorded book")
CATALOGUE_LINK = "[research-priors.md](research-priors.md)"

# #716 section 4, the boundary that makes the book-bearing routes safe. It is
# one sentence, and the three files carry it byte-identically rather than each
# wording it locally: five independent phrasings of one rule is the drift #832
# spent a whole chapter deleting, and a bare pointer would make the agent open a
# second file before it learned the limit. The same answer #834 gave for
# exemplars -- one text in several places, made mechanical instead of forbidden.
BOUNDARY_HEADING = "An engine fact dominates a prior"
BOUNDARY_SENTENCE = (
    "A prior may interpret a deterministic result. It may never replace one, "
    "substitute for one, or fill a gap in one."
)


def _collapsed(text):
    """Whitespace-normalized, so a hard-wrapped copy still compares equal."""
    return " ".join(text.split())


def _section(text, heading):
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


def _answer_default_is_valid(section):
    # #832 retired the local sentence this used to pin ("lead with the bounded
    # value already supported"). That sentence was one of six independent
    # phrasings of answer-first, and the rule it stated now lives once, in
    # `docs/expression-contract.md`'s mother chapter. What it *protected* --
    # the user sees the bounded value before any intake question -- is pinned
    # harder than before: the route's own block order must run baseline ->
    # map -> question, in that order, and must declare itself a derivation of
    # the shape rather than a second statement of it.
    baseline = "research-backed baseline"
    strategy_map = "applicable strategy-class map"
    question = "any question whose answer could change the recommendation"
    return (
        section.index(baseline) < section.index(strategy_map) < section.index(question)
        and "the parameter it adds to the pyramid" in section
        and "Ask only questions that separate remaining live branches" in section
        and "there is no universal count or last-slot rule" in section
        and "No question is allowed before" not in section
        and "asks zero or one" not in section
    )


def _reachability_failures(skill_text, route_texts):
    """Reasons the catalogue is not reachable from every decision route."""
    problems = []
    for surface, path in ROUTE_FILES.items():
        routed = f"references/{path.name}"
        if routed not in skill_text:
            problems.append(f"SKILL.md does not route the {surface} route to {routed}")
        if CATALOGUE_LINK not in route_texts[surface]:
            problems.append(f"{path.name} ({surface}) does not link the research catalogue")
    return problems


def _boundary_failures(texts):
    """Reasons the engine-fact-dominates boundary is not one sentence, everywhere."""
    problems = []
    catalogue = texts[PRIORS.name]
    if f"## {BOUNDARY_HEADING}" not in catalogue:
        problems.append(f"{PRIORS.name} has no '{BOUNDARY_HEADING}' section")
    for name, text in texts.items():
        if BOUNDARY_SENTENCE not in _collapsed(text):
            problems.append(f"{name} does not carry the boundary sentence verbatim")
    for surface in BOOK_BEARING:
        path = ROUTE_FILES[surface]
        if BOUNDARY_HEADING not in _collapsed(texts[path.name]):
            problems.append(
                f"{path.name} ({surface}) does not name the section the boundary comes from")
    return problems


def test_a_the_catalogue_has_only_the_audited_priors_and_the_required_fields():
    text = PRIORS.read_text(encoding="utf-8")
    ids = ("RP-001", "RP-002", "RP-003", "RP-004")
    assert sum(text.count(f"## {prior}") for prior in ids) == len(ids)
    assert text.count("\n## RP-") == len(ids)
    for prior in ids:
        section = _section(text, f"## {prior}")
        for field in (
            "**Applicable decision class:**",
            "**Bounded directional claim:**",
            "**Material exceptions:**",
            "**Forbidden overclaims:**",
            "**Reviewed date:**",
            "**Primary source",
        ):
            assert field in section, f"{prior} is missing {field}"


def test_b_the_catalogue_is_reachable_from_every_route():
    """#716's defect, stated as the thing that must now be true. The shipped
    implementation (#727) wired the no-book route only, and the issue's own
    title is *cross-route*: a user who hands over a book must not lose the
    baseline a user with no book was given."""
    assert PRIORS.is_file(), "the research catalogue is missing"
    skill_text = SKILL.read_text(encoding="utf-8")
    route_texts = {surface: path.read_text(encoding="utf-8")
                   for surface, path in ROUTE_FILES.items()}
    problems = _reachability_failures(skill_text, route_texts)
    assert not problems, "; ".join(problems)


def test_c_visible_value_precedes_intake_without_a_question_cap():
    section = _section(
        FRAMING.read_text(encoding="utf-8"), "## Research-aware strategy framing")
    assert _answer_default_is_valid(section)
    assert "Do not ask about liquidity or a stop before the user sees the\nbaseline and map." in section
    ordinary = _section(FRAMING.read_text(encoding="utf-8"), "## Question heuristics")
    assert "not a required sequence or count" in ordinary


def test_d_numeric_question_cap_regression_reddens():
    section = _section(
        FRAMING.read_text(encoding="utf-8"), "## Research-aware strategy framing")
    mutated = section.replace(
        "there is no universal count or last-slot rule",
        "ask at most one question, and it must be last",
        1,
    )
    assert not _answer_default_is_valid(mutated)


def test_e_the_engine_fact_boundary_is_one_sentence_in_three_places():
    """#716 section 4. Widening the catalogue's reach is only safe while a prior
    cannot outrank the arithmetic: it may read a computed number, never stand in
    for one, and never supply a cap, allocation or threshold the engine did not
    compute and the user has no rule for."""
    texts = {path.name: path.read_text(encoding="utf-8")
             for path in (PRIORS, CONSEQUENCE, FREEFORM)}
    problems = _boundary_failures(texts)
    assert not problems, "; ".join(problems)


def test_f_reachability_and_boundary_mutations_are_caught():
    """Mutation proof for both checks above, against the real committed text.
    A checker that stays green under its own mutation is not evidence
    (`docs/maintainer-guide.md`, development discipline)."""
    skill_text = SKILL.read_text(encoding="utf-8")
    route_texts = {surface: path.read_text(encoding="utf-8")
                   for surface, path in ROUTE_FILES.items()}
    assert not _reachability_failures(skill_text, route_texts), (
        "fixture assumption broken: the committed tree is already unreachable")

    # 1. A route file that stops linking the catalogue -- the exact shape of the
    #    defect #716 records, one route at a time.
    for surface, path in ROUTE_FILES.items():
        mutated = dict(route_texts)
        mutated[surface] = route_texts[surface].replace(CATALOGUE_LINK, "the baseline")
        assert _reachability_failures(skill_text, mutated), (
            f"dropping the catalogue link from {path.name} left the check green")

    # 2. `SKILL.md` that stops routing a route file at all: the catalogue would
    #    still be linked, from a document nothing loads.
    for surface, path in ROUTE_FILES.items():
        mutated_skill = skill_text.replace(f"references/{path.name}", "references/gone.md")
        assert _reachability_failures(mutated_skill, route_texts), (
            f"unrouting {path.name} from SKILL.md left the check green")

    boundary_texts = {path.name: path.read_text(encoding="utf-8")
                      for path in (PRIORS, CONSEQUENCE, FREEFORM)}
    assert not _boundary_failures(boundary_texts), (
        "fixture assumption broken: the committed boundary is already incomplete")

    # 3. Any single copy of the boundary sentence rewritten -- the drift a
    #    paraphrase would have introduced silently.
    softened = BOUNDARY_SENTENCE.replace("never replace one,", "usually not replace one,")
    for name in boundary_texts:
        mutated = dict(boundary_texts)
        collapsed = _collapsed(mutated[name])
        assert BOUNDARY_SENTENCE in collapsed
        mutated[name] = collapsed.replace(BOUNDARY_SENTENCE, softened, 1)
        assert _boundary_failures(mutated), (
            f"softening the boundary sentence in {name} left the check green")

    # 4. The catalogue losing the section the two routes point at.
    mutated = dict(boundary_texts)
    mutated[PRIORS.name] = boundary_texts[PRIORS.name].replace(
        f"## {BOUNDARY_HEADING}", "## Applying a prior", 1)
    assert _boundary_failures(mutated), (
        "removing the boundary section heading left the check green")


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"{len(tests)}/{len(tests)} passed")
