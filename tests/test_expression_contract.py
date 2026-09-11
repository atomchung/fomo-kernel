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

WITNESSES = ROOT / "tests" / "agent" / "expression-witnesses.json"
SKILL = ROOT / "skills" / "fomo-kernel" / "SKILL.md"
GUIDE = ROOT / "docs" / "maintainer-guide.md"

# #832. Each surface that used to carry its own wording of answer-first, and
# the exact string that wording was. A surface may keep its history in a
# superseded-by clause; what it may not keep is the rule stated as if it were
# still the rule here. `output-voice.md` is deliberately absent: V1 keeps its
# ID as a failure class and quotes its own superseded definition, which is
# checked separately below rather than by absence.
RETIRED_ANSWER_FIRST_PHRASINGS = {
    SKILL: "Answer in the reader's own order",
    REFERENCES / "weekly-market-read.md": "The host shows value first",
    REFERENCES / "decision-framing.md": "lead with the bounded value already supported",
    REFERENCES / "trade-consequence.md": "### The reader's question chain",
}

DISCLOSURE_IDS = tuple(f"D{number}" for number in range(1, 8))
CITATION_IDS = tuple(f"C{number}" for number in range(1, 5))
# The rules whose table row must keep declaring that nothing mechanical
# decides them. D7 is deliberately not on this list: its bottom floor -- a
# machine anchor rendered at a person -- really is decided by E-6, and the
# table names that oracle rather than claiming the whole rule is checked.
INSTRUCTION_ONLY_IDS = tuple(f"D{number}" for number in range(1, 7)) + ("C3",)
ROW_RE = re.compile(r"^\|\s*([DC]\d+)\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|$", re.M)

# #834: the exemplar's second home. A fence tagged `exemplar` plus the scene id
# it copies is the whole marker format -- one line, no counting, and the id
# travels with the text rather than sitting in a table beside it.
EXEMPLAR_FENCE = re.compile(
    r"^```exemplar (?P<scene>[a-z0-9_]+)\n(?P<body>.*?)\n```$", re.M | re.S)
REFERENCE_IN_SURFACE = re.compile(r"references/(?P<name>[a-z0-9-]+\.md)")

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
    for rule_id in INSTRUCTION_ONLY_IDS:
        verification, oracle = rows[rule_id]
        assert verification == "instruction only", f"{rule_id} claims {verification!r}"
        assert oracle == "—", f"{rule_id} names oracle {oracle!r} for an unverified rule"
    # The other direction, for the one D rule that does claim a mechanical
    # half: a named oracle that stops existing is a claim of coverage this
    # repository has shipped before, and it must not be checkable only by
    # reading the table.
    verification, oracle = rows["D7"]
    assert verification != "instruction only" and oracle != "—", (
        "D7 declares a mechanical half; the table must name which half and whose oracle")
    assert "E-6" in oracle, oracle
    assert "E-6" in _load_checker().ASSERTIONS, (
        "the contract names E-6 as D7's oracle and the checker does not run it")


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


# ───────────── 2b. one communication method, and only one (#832) ─────────────

def _mother_chapter():
    """§3 alone. Reading the whole file would let a mention anywhere satisfy a
    check about what the mother chapter itself says."""
    text = CONTRACT.read_text(encoding="utf-8")
    start = text.index("\n## 3. ")
    return text[start:text.index("\n## 4. ", start)]


def test_the_mother_chapter_exists_and_carries_the_pyramid():
    chapter = _mother_chapter()
    for required in ("increment", "one sentence", "counter-exemplar"):
        assert required in chapter.lower(), (
            f"the mother chapter does not mention {required!r}")
    # Not a registry. The whole point of #832 is that shape stopped being an
    # ID-addressed rule, so a D8/C5/V10 row appearing here would be the sixth
    # phrasing wearing an ID.
    assert not re.search(r"^\|\s*[DCV]\d+\s*\|", chapter, re.M), (
        "the mother chapter grew a registry row; shape takes no new IDs")


def test_the_named_bans_are_declared_in_the_mother_chapter():
    """The corpus is where the bans are referenced by slug and the chapter is
    where they are defined. Neither is a copy of the other, so the link has to
    be gated or the corpus can name a ban no rule states."""
    bans = json.loads(WITNESSES.read_text(encoding="utf-8"))["bans"]
    chapter = _mother_chapter()
    assert len(bans) == 4, f"#832 names four bans; the corpus declares {len(bans)}"
    for slug in bans:
        assert f"`{slug}`" in chapter, (
            f"the corpus declares the ban {slug!r} and the mother chapter never names it")


def test_every_surface_declares_its_derivation_from_the_mother_chapter():
    """Routing to the contract was #823's bar and is no longer enough: a
    surface must say what it *adds* to the shape, including when the honest
    answer is nothing."""
    for path, surface in SURFACES.items():
        text = path.read_text(encoding="utf-8")
        assert "§3" in text, (
            f"{path.relative_to(ROOT)} ({surface}) does not point at the mother chapter")
        assert any(word in text for word in ("derivation", "derives", "derive")), (
            f"{path.relative_to(ROOT)} ({surface}) does not declare its derivation")


def test_no_surface_still_carries_a_local_answer_first_phrasing():
    """#832's grep-checkable acceptance. Five surfaces each stated answer-first
    in their own words, which is drift by construction; the sixth
    (`trade-consequence.md`'s reader-question-chain section) turned up in the
    audit. None of them may state it again."""
    for path, phrase in RETIRED_ANSWER_FIRST_PHRASINGS.items():
        text = path.read_text(encoding="utf-8")
        assert phrase not in text, (
            f"{path.relative_to(ROOT)} still states the answer shape itself "
            f"({phrase!r}); replace it with a derivation from the mother chapter")


def test_v1_became_a_failure_class_pointing_at_the_mother_chapter():
    """The one surface that may keep its old wording, because fixtures and
    cross-host rulings cite V1 by ID. What it may not do is present that
    wording as the current statement of the rule."""
    text = VOICE.read_text(encoding="utf-8")
    v1 = text[text.index("- **V1 —"):text.index("- **V2 —")]
    assert "expression-contract.md" in v1 and "§3" in v1, (
        "V1 does not route its shape half to the mother chapter")
    assert "superseded" in v1, (
        "V1 keeps its historical definition without marking it superseded")


def test_the_registry_freeze_for_shape_is_recorded():
    """The freeze is the governance half of #832 and has to be readable from
    both the contract and the maintainer route, or the next style fix arrives
    as V10."""
    chapter = _mother_chapter()
    assert "no new ID" in chapter or "no new IDs" in chapter, (
        "the mother chapter does not record the V/D/C freeze for shape and length")
    assert "V10" in VOICE.read_text(encoding="utf-8"), (
        "output-voice.md does not say V10 stays unallocated")
    guide = GUIDE.read_text(encoding="utf-8")
    assert "#832" in guide, "the maintainer guide has no #832 mirrored-surfaces row"
    assert "no new ID" in guide, (
        "the maintainer guide's #832 row does not record the registry freeze")


def test_no_character_count_cap_came_back():
    """#543's ceiling was deleted by #827 and stays deleted. A shape law is the
    place a length cap would most plausibly be smuggled back in, so the check
    lives here."""
    for path in list(SURFACES) + [SKILL, CONTRACT]:
        text = path.read_text(encoding="utf-8").lower()
        for banned in ("character cap", "character limit", "at most five lines"):
            assert banned not in text, f"{path.relative_to(ROOT)} reintroduces a {banned}"


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


# ───── 3b. the exemplar on the generation path is the corpus copy (#834) ─────


def _corpus():
    return json.loads(WITNESSES.read_text(encoding="utf-8"))


def _normalized(text):
    """Trailing whitespace and the fence's own padding are not drift."""
    return "\n".join(line.rstrip() for line in text.strip().split("\n"))


def _exemplars(text):
    """Every `(scene_id, body)` an exemplar fence declares, in order.

    The fence carries its scene id in the info string, so extraction needs no
    line offsets, no heading walk, and no second list of which file holds
    which exemplar.
    """
    return [(match.group("scene"), match.group("body"))
            for match in EXEMPLAR_FENCE.finditer(text)]


def test_every_surface_reference_opens_with_its_canonical_exemplar():
    """#834. Before it, the exemplars existed only in the QC layer: the model
    never saw one while generating, and prose norms alone did not bind scale.
    Each surface reference now opens with its own copy.

    The pairing is read from the two declarations that already existed -- the
    corpus's `surfaces` map names the reference file that owns each surface,
    and the reference file's own fence names the scene -- so this compares
    them rather than adding a third list to keep in step.
    """
    corpus = _corpus()
    scenes = {scene["id"]: scene for scene in corpus["scenes"]}
    for surface, description in corpus["surfaces"].items():
        named = REFERENCE_IN_SURFACE.findall(description)
        assert len(named) == 1, (
            f"the corpus surface {surface!r} names {len(named)} reference files; "
            "exactly one owns it")
        path = REFERENCES / named[0]
        assert path.is_file(), f"surface {surface!r} names a missing file: {named[0]}"
        text = path.read_text(encoding="utf-8")
        found = _exemplars(text)
        assert found, f"{path.relative_to(ROOT)} carries no exemplar block"
        headings = [match.start() for match in re.finditer(r"^## ", text, re.M)]
        fence = text.index("```exemplar ")
        assert headings and headings[0] < fence and (
            len(headings) == 1 or fence < headings[1]), (
            f"{path.relative_to(ROOT)} does not open with its exemplar; progressive "
            "disclosure only helps if the example is what the reader meets first")
        scene_ids = [scene_id for scene_id, _body in found]
        assert len(scene_ids) == len(set(scene_ids)), (
            f"{path.relative_to(ROOT)} repeats an exemplar scene: {scene_ids}")
        if len(headings) > 1:
            assert text.rindex("```exemplar ") < headings[1], (
                f"{path.relative_to(ROOT)} carries an exemplar fence outside its opening "
                "section; every witness sits where the reader meets the first one")
        # The first fence is the canonical exemplar the file opens with; any
        # later fence is a further witness of the same surface (a compact end
        # beside an upper one), held to the same corpus copy, the same surface,
        # and the same positive kind -- a counter-exemplar never sits on the
        # generation path.
        for scene_id, body in found:
            scene = scenes.get(scene_id)
            assert scene is not None, (
                f"{path.relative_to(ROOT)} names scene {scene_id!r}, absent from the corpus")
            assert scene["surface"] == surface, (
                f"{path.relative_to(ROOT)} carries a {scene['surface']!r} exemplar")
            assert scene["kind"] == "positive", (
                f"{path.relative_to(ROOT)} carries a {scene['kind']} exemplar ({scene_id})")
            assert _normalized(body) == _normalized(scene["answer"]), (
                f"{path.relative_to(ROOT)} and the witness copy of {scene_id!r} have drifted; "
                "one of the two was edited alone and they are no longer one exemplar")


def test_no_other_document_carries_an_exemplar_block():
    """The English-only carve-out `tests/test_doc_language.py` gives an
    exemplar fence is safe only while the surface references are the only
    files that have one. Otherwise the fence is a way to put unchecked,
    untranslated prose anywhere in the tree."""
    corpus = _corpus()
    owned = {REFERENCES / REFERENCE_IN_SURFACE.search(description).group("name")
             for description in corpus["surfaces"].values()}
    # Dot directories are skipped rather than scanned: a maintainer's own
    # `.claude/worktrees/` holds whole checkouts of this repository, and every
    # reference file in one of them would read as a stray copy of itself.
    candidates = [(path, path.relative_to(ROOT)) for path in sorted(ROOT.rglob("*.md"))]
    stray = [str(rel) for path, rel in candidates
             if not any(part.startswith(".") for part in rel.parts)
             and path not in owned
             and "```exemplar " in path.read_text(encoding="utf-8")]
    assert not stray, "exemplar fence outside a surface reference: " + ", ".join(stray)


def test_exemplar_drift_is_caught():
    """Mutation proof for the gate above, in both directions it can go blind:
    a one-character edit to either copy must redden, and a fence that loses
    its `exemplar` tag must stop being read as one rather than pass."""
    scene = next(item for item in _corpus()["scenes"]
                 if item["id"] == "freeform_positions_view")
    block = f"```exemplar {scene['id']}\n{scene['answer']}\n```"
    assert _exemplars(block) == [(scene["id"], scene["answer"])], \
        "the extractor does not read a well-formed block"
    edited = block.replace("六檔", "五檔", 1)
    assert _normalized(_exemplars(edited)[0][1]) != _normalized(scene["answer"]), \
        "a one-character edit to the reference copy left the gate green"
    assert _exemplars(f"```text\n{scene['answer']}\n```") == [], \
        "an untagged fence is being read as an exemplar"


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
# instead of widening the floor unnoticed. `basis` fell from five to two in
# #830 (the four-piece recital plus the hash became a date and its age), and
# the two families that left `must_state` keep their ceilings on `may_state`
# — an available list that grows unnoticed is the same defect one key over.
FIXED_TOPIC_CEILINGS = {"basis": 2, "position": 4}
FIXED_MAY_TOPIC_CEILINGS = {"concentration": 5, "cash": 2}
FIXED_MACHINE_TOPIC_CEILINGS = {"state_version": 1}


def _fixed_ceilings(challenge, key, ceilings):
    counts = {}
    for entry in challenge[key]:
        counts[entry["topic"]] = counts.get(entry["topic"], 0) + 1
    for topic, ceiling in ceilings.items():
        assert counts.get(topic, 0) <= ceiling, (
            f"{key}.{topic} emits {counts[topic]} entries against a declared ceiling of "
            f"{ceiling}; a fixed-size topic that grew is how a list becomes a field table")


def test_fixed_topics_stay_within_their_declared_ceiling():
    challenge = _dense_challenge()
    _fixed_ceilings(challenge, "must_state", FIXED_TOPIC_CEILINGS)
    _fixed_ceilings(challenge, "may_state", FIXED_MAY_TOPIC_CEILINGS)
    _fixed_ceilings(challenge, "machine_state", FIXED_MACHINE_TOPIC_CEILINGS)


def test_no_owed_fact_is_a_duplicate_another_could_absorb():
    """The real anti-table invariant. A variable-size topic is allowed to
    grow with the book, but only by naming a *distinct* subject each time:
    one entry per instrument, per rule, per disclosure key. Two entries
    stating the same subject would be two lines of prose saying one thing,
    which is exactly what the measured 39-field table was made of."""
    challenge = _dense_challenge()
    seen = set()
    for key in ("must_state", "may_state", "machine_state"):
        for entry in challenge[key]:
            fact = (entry["topic"], entry.get("anchor"),
                    json.dumps(entry.get("detail"), sort_keys=True), str(entry["value"]))
            assert fact not in seen, f"{key} repeats a fact: {entry}"
            seen.add(fact)


def test_the_owed_floor_stayed_smaller_than_the_whole_inventory():
    """#830's structural claim, on the payload the audit was run against.
    The block still computes everything; what shrank is the part an answer
    is told it may not drop. A change that quietly moved a family back onto
    the floor would leave every other test here green."""
    challenge = _dense_challenge()
    owed = {entry["topic"] for entry in challenge["must_state"]}
    assert not owed & set(evaluation_challenge.MAY_STATE_TOPICS), (
        f"an available family is back on the floor: {sorted(owed & set(evaluation_challenge.MAY_STATE_TOPICS))}")
    assert not owed & set(evaluation_challenge.MACHINE_TOPICS), (
        "a machine anchor is on the floor; that is the finding #830 opened on")
    assert challenge["may_state"] and challenge["machine_state"], (
        "the inventory shrank instead of splitting — a deletion of the data, not of the obligation")


def test_every_topic_the_contract_governs_is_reachable():
    """A topic in `TOPICS` that no dense payload can light is a field written
    for a reader nobody built (AGENTS.md boundary 6). This is the check that
    caught out_of_scope needing a fixture at all."""
    challenge = _dense_challenge()
    for key, declared in (("must_state", evaluation_challenge.TOPICS),
                          ("may_state", evaluation_challenge.MAY_STATE_TOPICS),
                          ("machine_state", evaluation_challenge.MACHINE_TOPICS)):
        topics = {entry["topic"] for entry in challenge[key]}
        assert set(declared) == topics, (
            f"unreachable {key} topics: {sorted(set(declared) - topics)}")


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
        test_the_mother_chapter_exists_and_carries_the_pyramid,
        test_the_named_bans_are_declared_in_the_mother_chapter,
        test_every_surface_declares_its_derivation_from_the_mother_chapter,
        test_no_surface_still_carries_a_local_answer_first_phrasing,
        test_v1_became_a_failure_class_pointing_at_the_mother_chapter,
        test_the_registry_freeze_for_shape_is_recorded,
        test_no_character_count_cap_came_back,
        test_the_contract_routes_voice_rather_than_restating_it,
        test_the_witness_oracle_passes,
        test_the_checker_derives_its_blacklist_from_the_schemas,
        test_every_surface_reference_opens_with_its_canonical_exemplar,
        test_no_other_document_carries_an_exemplar_block,
        test_exemplar_drift_is_caught,
        test_fixed_topics_stay_within_their_declared_ceiling,
        test_no_owed_fact_is_a_duplicate_another_could_absorb,
        test_the_owed_floor_stayed_smaller_than_the_whole_inventory,
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
