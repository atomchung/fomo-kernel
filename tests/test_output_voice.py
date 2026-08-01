#!/usr/bin/env python3
"""Offline contract and witness checks for #676's global output voice."""
import importlib.util
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
VOICE = ROOT / "docs" / "output-voice.md"
POINTERS = {
    ROOT / "AGENTS.md": "docs/output-voice.md",
    ROOT / "skills" / "fomo-kernel" / "SKILL.md": "docs/output-voice.md",
    ROOT / "skills" / "fomo-kernel" / "references" / "trade-consequence.md": "output-voice.md",
    ROOT / "skills" / "fomo-kernel" / "references" / "decision-framing.md": "output-voice.md",
}
RULE_IDS = tuple(f"V{number}" for number in range(1, 10))
ROW_RE = re.compile(r"^\|\s*(V\d+)\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|$", re.M)


def _rows(text: str) -> list[tuple[str, str, str, str, str]]:
    return [tuple(cell.strip() for cell in match.groups()) for match in ROW_RE.finditer(text)]


def test_global_authority_and_route_boundaries():
    text = VOICE.read_text(encoding="utf-8")
    assert "one global authority" in text
    assert "every user-visible FOMO Kernel surface" in text
    assert "Route references retain ownership of route-specific facts" in text
    assert "Phase 1 integrates and proves this authority only on `consider` and no-book" in text
    assert "do not select,\n  rank, or call one candidate stronger" in text


def test_actual_instruction_paths_load_the_authority():
    for path, pointer in POINTERS.items():
        text = path.read_text(encoding="utf-8")
        assert pointer in text, f"{path.relative_to(ROOT)} does not point to output voice"


def test_skill_keeps_the_global_pointer_instead_of_the_retired_route_local_voice_copy():
    text = (ROOT / "skills" / "fomo-kernel" / "SKILL.md").read_text(encoding="utf-8")
    assert "Apply the global output-voice contract to the route structure in `references/trade-consequence.md`." in text
    assert "Shape the reply decision-first: one supported decision tension leads" not in text


def test_stable_rule_registry_has_complete_unique_mappings():
    rows = _rows(VOICE.read_text(encoding="utf-8"))
    ids = [row[0] for row in rows]
    assert ids == list(RULE_IDS), f"expected one ordered mapping per rule, got {ids}"
    assert len(ids) == len(set(ids)), "duplicated output-voice rule mapping"
    for rule_id, mechanism, verification, oracle, witnesses in rows:
        assert mechanism and verification and witnesses, f"{rule_id} has an incomplete mapping"
        assert oracle == "`voice_witness_oracle`", f"{rule_id} has an unknown oracle {oracle!r}"


def test_registry_mutations_are_caught():
    text = VOICE.read_text(encoding="utf-8")
    for rule_id in RULE_IDS:
        mutated = text.replace(f"| {rule_id} |", "| removed |", 1)
        assert rule_id not in [row[0] for row in _rows(mutated)], rule_id
        assert [row[0] for row in _rows(mutated)] != list(RULE_IDS), (
            f"removing {rule_id} would leave the registry gate green")
    duplicated = text.replace("| V9 |", "| V1 |", 1)
    ids = [row[0] for row in _rows(duplicated)]
    assert len(ids) != len(set(ids)), "duplicate mutation did not create a duplicate"


def test_synthetic_witness_oracle_passes_and_covers_every_failure_class():
    spec = importlib.util.spec_from_file_location("check_voice", ROOT / "tests" / "agent" / "check_voice.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    assert module.check_fixture() == []


_REGISTERED = []


def test_every_test_is_registered():
    defined = {name for name, value in globals().items() if name.startswith("test_") and callable(value)}
    assert defined == {test.__name__ for test in _REGISTERED}


def main():
    tests = [
        test_global_authority_and_route_boundaries,
        test_actual_instruction_paths_load_the_authority,
        test_skill_keeps_the_global_pointer_instead_of_the_retired_route_local_voice_copy,
        test_stable_rule_registry_has_complete_unique_mappings,
        test_registry_mutations_are_caught,
        test_synthetic_witness_oracle_passes_and_covers_every_failure_class,
        test_every_test_is_registered,
    ]
    _REGISTERED[:] = tests
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
