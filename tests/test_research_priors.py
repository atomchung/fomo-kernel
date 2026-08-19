#!/usr/bin/env python3
"""Deterministic contract witnesses for research-aware no-book framing."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "fomo-kernel" / "SKILL.md"
FRAMING = ROOT / "skills" / "fomo-kernel" / "references" / "decision-framing.md"
PRIORS = ROOT / "skills" / "fomo-kernel" / "references" / "research-priors.md"


def _section(text, heading):
    start = text.index(heading)
    end = text.find("\n## ", start + len(heading))
    return text[start:] if end == -1 else text[start:end]


def _answer_default_is_valid(section):
    baseline = "research-backed baseline"
    strategy_map = "applicable strategy-class map"
    return (
        section.index(baseline) < section.index(strategy_map)
        and "lead with the bounded value already supported" in section
        and "Ask only questions that separate remaining live branches" in section
        and "there is no universal count or last-slot rule" in section
        and "No question is allowed before" not in section
        and "asks zero or one" not in section
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


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print(f"{len(tests)}/{len(tests)} passed")
