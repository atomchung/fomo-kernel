#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline contract checks for the unified expression contract.

`docs/expression-contract.md` claims three things this file is the oracle for:
that its registries are complete and stable, that every output surface routes
to it instead of carrying its own expression rules, and that its remaining
deterministic C4 checker derives engine vocabulary from the schemas. Placement,
markers, and line count are deliberately not mechanical gates after #825.

The fourth section is about the obligation *floor* rather than the contract
document: `evaluation_challenge.must_state` must stay a short list of distinct
named facts rather than growing into the wide field table #823's evidence
found to be transcribed no better than no table at all (39 mechanical fields
≈ free-text baseline). "Short" is not a total-count assertion — a book with
fifty holdings legitimately owes more excluded-holding facts than a book with
five — so what is pinned is the shape that keeps it short: every fixed-size
topic has a declared ceiling, and every variable-size topic emits exactly one
entry per distinct named subject, so no entry is a duplicate another could
absorb.
"""
import importlib.util
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "docs" / "expression-contract.md"
VOICE = ROOT / "docs" / "output-voice.md"
CHECKER = ROOT / "tests" / "agent" / "check_expression.py"
REFERENCES = ROOT / "skills" / "fomo-kernel" / "references"
ENGINE_DIR = ROOT / "skills" / "fomo-kernel" / "engine"

sys.path.insert(0, str(ENGINE_DIR))
import evaluation_challenge  # noqa: E402

# Every surface that renders a user-visible answer, and the layout authority
# that owns it. #823's whole subject: before it, four of these six carried
# their own disclosure-placement rules and one of them stated the opposite of
# another.
SURFACES = {
    ROOT / "docs" / "output-contract.md": "the review card",
    VOICE: "the voice registry this contract routes to",
    REFERENCES / "trade-consequence.md": "consider",
    REFERENCES / "freeform-answers.md": "freeform answers",
    REFERENCES / "decision-framing.md": "no recorded book",
    REFERENCES / "weekly-market-read.md": "weekly market read",
}

DISCLOSURE_IDS = tuple(f"D{number}" for number in range(1, 7))
CITATION_IDS = tuple(f"C{number}" for number in range(1, 5))
ROW_RE = re.compile(r"^\|\s*([DC]\d+)\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|$", re.M)

def _load_checker():
    if "check_expression" in sys.modules:
        return sys.modules["check_expression"]
    spec = importlib.util.spec_from_file_location("check_expression", CHECKER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # Registered before execution: `@dataclass` resolves its own class through
    # `sys.modules[cls.__module__]`, which is None for a module still being
    # built. `check_voice.py` loads the same way and has no dataclass, so this
    # only bites here.
    sys.modules["check_expression"] = module
    spec.loader.exec_module(module)
    return module


def _rows(text):
    return [tuple(cell.strip() for cell in match.groups()) for match in ROW_RE.finditer(text)]


# ───────────────────────── 1. the registries ─────────────────────────

def test_both_registries_are_complete_and_ordered():
    rows = _rows(CONTRACT.read_text(encoding="utf-8"))
    ids = [row[0] for row in rows]
    assert ids == list(DISCLOSURE_IDS) + list(CITATION_IDS), (
        f"expected one ordered row per D and C rule, got {ids}")
    assert len(ids) == len(set(ids)), "duplicated expression-contract rule row"
    for rule_id, rule, verification, oracle in rows:
        assert rule and verification and oracle, f"{rule_id} has an incomplete mapping"


def test_registry_mutations_are_caught():
    """The same mutation dance `test_output_voice.py` runs on V1-V9: removing
    or duplicating a row must break the gate, or the gate is decorative."""
    text = CONTRACT.read_text(encoding="utf-8")
    for rule_id in DISCLOSURE_IDS + CITATION_IDS:
        mutated = text.replace(f"| {rule_id} |", "| removed |", 1)
        ids = [row[0] for row in _rows(mutated)]
        assert ids != list(DISCLOSURE_IDS) + list(CITATION_IDS), (
            f"removing {rule_id} would leave the registry gate green")
    duplicated = text.replace("| D6 |", "| D1 |", 1)
    ids = [row[0] for row in _rows(duplicated)]
    assert len(ids) != len(set(ids)), "duplicate mutation did not create a duplicate"


def test_unverified_rules_are_declared_unverified():
    """D1-D6 and C3 have no mechanical oracle, and the contract says so in the
    table rather than naming one it does not have. A gate that claims
    coverage it lacks is the structural-gate failure this repository has
    shipped before."""
    rows = {row[0]: (row[2], row[3]) for row in _rows(CONTRACT.read_text(encoding="utf-8"))}
    for rule_id in DISCLOSURE_IDS + ("C3",):
        verification, oracle = rows[rule_id]
        assert verification == "instruction only", f"{rule_id} claims {verification!r}"
        assert oracle == "—", f"{rule_id} names oracle {oracle!r} for an unverified rule"


# ───────────────────────── 2. every surface routes here ─────────────────────────

def test_every_surface_routes_to_the_contract():
    for path, surface in SURFACES.items():
        text = path.read_text(encoding="utf-8")
        assert "expression-contract.md" in text, (
            f"{path.relative_to(ROOT)} ({surface}) does not route to the expression contract")


def test_the_always_on_layer_states_relevance_without_a_template():
    """The always-on instruction keeps the semantic floor but does not impose
    the formatting rules #825 retired."""
    text = (ROOT / "skills" / "fomo-kernel" / "SKILL.md").read_text(encoding="utf-8")
    assert "material limitations" in text
    assert "denominator, unit, or pricing set" in text, (
        "SKILL.md omits D2's truth-critical qualifier enumeration")
    assert "tail block" not in text
    assert "`[i] `" not in text
    assert "at most five lines" not in text.lower()


def test_the_contract_routes_voice_rather_than_restating_it():
    """V1-V9 stay in one file. The contract may name them; it may not carry
    their rule text, or the product grows the second voice authority this
    whole change exists to remove."""
    text = CONTRACT.read_text(encoding="utf-8")
    assert "output-voice.md" in text, "the contract must route to the voice registry"
    voice_rows = re.findall(r"^\|\s*V\d+\s*\|", text, re.M)
    assert not voice_rows, f"the contract restates {len(voice_rows)} voice rows instead of routing"


# ───────────────────── 3. the remaining checker agrees ─────────────────────


def test_the_witness_oracle_passes():
    checker = _load_checker()
    assert checker.check_fixture() == []


def test_the_checker_derives_its_blacklist_from_the_schemas():
    """C4's token list is read from `schemas/*.schema.json`, never
    transcribed. This asserts the derivation is live: a token the schemas
    publish must appear, and the checker must not have quietly grown a
    hardcoded copy that a schema edit would leave stale."""
    checker = _load_checker()
    tokens = set(checker.internal_tokens())
    assert {"already_over", "cost_basis", "partial_book"} <= tokens
    assert checker.internal_tokens(pathlib.Path(os.devnull).parent / "nowhere") == ()


# ───────────── 4. the obligation floor stays a list, not a table ─────────────

def _dense_challenge():
    """Every topic lit at once: a stale unverified basis, three instruments
    priced at three different sessions, every disclosure the engine defines,
    two excluded holdings, four illegible ones, three speaking rules. Nothing
    about it is a plausible book; it is the upper bound of the shape."""
    basis = {"source": "transactions", "as_of": "2026-06-15", "stale_days": 45,
             "completeness": "unverified", "state_version": "pb-v1:" + "0" * 64,
             "price_observations": {"as_of": "2026-07-30",
                                    "by_ticker": {"AAA": "2026-07-28", "BBB": "2026-07-29",
                                                  "CCC": "2026-07-30"}}}
    consequence = {
        "before": {"weights": {"AAA": 0.30}},
        "after": {"max_pct": 0.38, "weights": {"AAA": 0.38}, "top3": 0.70, "ai_pct": 0.64,
                  "max_sector_pct": 0.65, "max_sector": "semis",
                  "oversize_triggered": True, "concentration_triggered": True,
                  "cash": {"balance": -112750.0, "weight": -0.12}},
        "delta": {"ticker_weight": 0.08},
        "disclosures": ["cost_basis", "cash_unreliable", "unmapped_driver", "unclassified_book",
                        "etf_not_decomposed", "partial_book", "cash_anchor_unmatched"],
        "excluded_holdings": [{"ticker": "XCL", "reason": "unavailable_cost"},
                              {"ticker": "YCL", "reason": "integrity_oversell"}],
        "unclassified_holdings": [{"ticker": "JNJ", "weight": 0.08}, {"ticker": "PGX", "weight": 0.07},
                                  {"ticker": "KOX", "weight": 0.06}],
        "undecomposed_etfs": [{"ticker": "EFA", "weight": 0.05}],
    }
    rules = [{"rule_id": f"rule-{index}", "text": "Cap any one name.", "state": "already_over",
              "worsens": True, "rule_effect": "worsened_existing_breach", "limit": 0.2,
              "limit_source": "user_cap"} for index in range(1, 4)]
    return evaluation_challenge.build_challenge(
        premise={"ticker": "AAA", "price": 127.5, "price_basis": "observed"},
        basis=basis, consequence=consequence, rule_collisions=rules,
        context={"reason": "r", "why_now": "w", "evidence_refs": ["e"]})


# Ceilings for the topics whose size does not depend on the book. Each is the
# count of fields that topic can ever emit, so a new one lands here loudly
# instead of widening the floor unnoticed.
FIXED_TOPIC_CEILINGS = {"basis": 5, "position": 4, "concentration": 5, "cash": 2}


def test_fixed_topics_stay_within_their_declared_ceiling():
    challenge = _dense_challenge()
    counts = {}
    for entry in challenge["must_state"]:
        counts[entry["topic"]] = counts.get(entry["topic"], 0) + 1
    for topic, ceiling in FIXED_TOPIC_CEILINGS.items():
        assert counts.get(topic, 0) <= ceiling, (
            f"{topic} emits {counts[topic]} entries against a declared ceiling of {ceiling}; "
            "a fixed-size topic that grew is how the floor becomes a field table")


def test_no_owed_fact_is_a_duplicate_another_could_absorb():
    """The real anti-table invariant. A variable-size topic is allowed to
    grow with the book, but only by naming a *distinct* subject each time:
    one entry per instrument, per rule, per disclosure key. Two entries
    stating the same subject would be two lines of prose saying one thing,
    which is exactly what the measured 39-field table was made of."""
    challenge = _dense_challenge()
    seen = set()
    for entry in challenge["must_state"]:
        key = (entry["topic"], entry.get("anchor"),
               json.dumps(entry.get("detail"), sort_keys=True), str(entry["value"]))
        assert key not in seen, f"must_state repeats a fact: {entry}"
        seen.add(key)


def test_every_topic_the_contract_governs_is_reachable():
    """A topic in `TOPICS` that no dense payload can light is a field written
    for a reader nobody built (AGENTS.md boundary 6). This is the check that
    caught out_of_scope needing a fixture at all."""
    topics = {entry["topic"] for entry in _dense_challenge()["must_state"]}
    assert set(evaluation_challenge.TOPICS) == topics, (
        f"unreachable topics: {sorted(set(evaluation_challenge.TOPICS) - topics)}")


def test_coverage_names_the_extent_of_every_illegible_disclosure():
    """#823's coverage half, at the challenge surface rather than the gate:
    each of the three extent-bearing disclosures contributes its own
    out_of_scope entry pointing at the list that names how much of the book
    was not read."""
    coverage = _dense_challenge()["required_coverage"]
    scoped = {entry["key"]: entry["path"] for entry in coverage if entry["owes"] == "out_of_scope"}
    assert scoped == {"unclassified_book": "consequence.unclassified_holdings",
                      "etf_not_decomposed": "consequence.undecomposed_etfs",
                      "partial_book": "consequence.excluded_holdings"}


_REGISTERED = []


def test_every_test_is_registered():
    defined = {name for name, value in globals().items()
               if name.startswith("test_") and callable(value)}
    assert defined == {test.__name__ for test in _REGISTERED}


def main():
    tests = [
        test_both_registries_are_complete_and_ordered,
        test_registry_mutations_are_caught,
        test_unverified_rules_are_declared_unverified,
        test_every_surface_routes_to_the_contract,
        test_the_always_on_layer_states_relevance_without_a_template,
        test_the_contract_routes_voice_rather_than_restating_it,
        test_the_witness_oracle_passes,
        test_the_checker_derives_its_blacklist_from_the_schemas,
        test_fixed_topics_stay_within_their_declared_ceiling,
        test_no_owed_fact_is_a_duplicate_another_could_absorb,
        test_every_topic_the_contract_governs_is_reachable,
        test_coverage_names_the_extent_of_every_illegible_disclosure,
        test_every_test_is_registered,
    ]
    _REGISTERED[:] = tests
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
