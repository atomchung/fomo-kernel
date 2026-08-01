#!/usr/bin/env python3
"""Deterministic contract witnesses for the bounded #715/#716 no-book slice."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "fomo-kernel" / "SKILL.md"
FRAMING = ROOT / "skills" / "fomo-kernel" / "references" / "decision-framing.md"
PRIORS = ROOT / "skills" / "fomo-kernel" / "references" / "research-priors.md"


def _section(text, heading):
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


def _answer_order_is_valid(section):
    baseline = "research-backed baseline"
    strategy_map = "applicable strategy-class map"
    final_question = "at most one discriminating question, last"
    forbidden_early_question = "First ask the material exception"
    before_map = section[:section.index("Then provide only the strategy classes")]
    return (
        section.index(baseline) < section.index(strategy_map) < section.index(final_question)
        and forbidden_early_question not in section
        and "Only after the baseline and map, ask one question" in section
        and "No question is allowed before the strategy-class map." in section
        and "?" not in before_map
        and "\nQuestion" not in before_map
    )


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


def test_b_the_guaranteed_no_book_loading_path_reaches_the_catalogue():
    assert "references/decision-framing.md" in SKILL.read_text(encoding="utf-8")
    assert "[research-priors.md](research-priors.md)" in FRAMING.read_text(encoding="utf-8")
    assert PRIORS.is_file()


def test_c_visible_value_and_strategy_map_precede_one_final_question():
    section = _section(
        FRAMING.read_text(encoding="utf-8"), "## Research-aware answer order")
    assert _answer_order_is_valid(section)
    assert "Do not ask about liquidity or a stop before the user sees the\nbaseline and map." in section
    assert "This research-aware strategy framing **replaces** the three-question sequence" in section
    ordinary = _section(FRAMING.read_text(encoding="utf-8"), "## The three questions")
    assert "ordinary, non-research-aware single-trade framing" in ordinary


def test_d_ordering_regression_reddens_when_any_question_is_restored_before_value():
    section = _section(
        FRAMING.read_text(encoding="utf-8"), "## Research-aware answer order")
    restored = "Question first: what is your time horizon?"
    mutated = section.replace(
        "For genuinely long-horizon risk capital", restored + ".\n\nFor genuinely long-horizon risk capital", 1)
    assert not _answer_order_is_valid(mutated)


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"{len(tests)}/{len(tests)} passed")
