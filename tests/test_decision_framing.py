#!/usr/bin/env python3
"""Mechanical witnesses for #715's isolated no-book A01 strategy map."""
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "skills" / "fomo-kernel" / "references" / "decision-framing.md"

MENU = (
    "Long-horizon broad-market policy",
    "Predetermined staged deployment of already-available cash",
    "Tactical or learning trade",
    "Defer",
)
QUESTION = "Which describes this decision best:"
RED_LINES = (
    "An ETF or index label alone is not proof of breadth.",
    "Do not import a tactical price stop into this policy.",
    "A tactical percentage stop is not a governance rule for a long-horizon policy.",
    "Future savings are not already-available cash.",
)


def test_a01_strategy_menu_precedes_exit_outsourcing():
    text = REFERENCE.read_text(encoding="utf-8")
    menu_heading = text.index("## When the user asks what strategy to use")
    first_exit_question = text.index("### Q3 — what would make you exit")
    assert menu_heading < first_exit_question
    for item in MENU:
        assert item in text
    assert QUESTION in text

    without_menu = text.replace(MENU[0], "", 1)
    assert MENU[0] not in without_menu
    without_question = text.replace(QUESTION, "", 1)
    assert QUESTION not in without_question


def test_a07_and_a10_keep_the_strategy_boundaries_explicit():
    text = REFERENCE.read_text(encoding="utf-8")
    for line in RED_LINES:
        assert line in text

    for line in RED_LINES:
        mutated = text.replace(line, "", 1)
        assert line not in mutated


if __name__ == "__main__":
    test_a01_strategy_menu_precedes_exit_outsourcing()
    test_a07_and_a10_keep_the_strategy_boundaries_explicit()
    print("decision framing tests: ok")
