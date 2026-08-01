#!/usr/bin/env python3
"""Deterministic synthetic witness oracle for the global output-voice contract.

This is deliberately not a runtime judge and does not read Markdown as
configuration. It classifies fixed, generic witness text in
``voice-witnesses.json`` so a route can prove that each named failure remains
distinct. Cross-host usefulness is reviewed separately by voice-cross-host.md
and ultimately by #610.
"""
import json
import pathlib
import re
import sys
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = ROOT / "tests" / "agent" / "voice-witnesses.json"

RULE_IDS = frozenset(f"V{number}" for number in range(1, 10))


def classify_failure(answer: str) -> Optional[str]:
    lowered = answer.lower()
    if re.search(r"\bsell (alp|brv)\b", lowered) and len(answer.split()) <= 6:
        return "concise_unsupported"
    if "engine returned" in lowered or "challenge block" in lowered or "json" in lowered:
        return "process_leakage"
    if "still, you should worry" in lowered or "longer risk memo" in lowered:
        return "manufactured_insight"
    if lowered.startswith("i cannot") and lowered.count("i cannot") >= 2:
        return "soft_evasion"
    if lowered.startswith("i cannot") or "upload it first" in lowered:
        return "hard_evasion"
    metric_count = len(re.findall(r"\d+%", answer))
    if metric_count >= 3 and not any(word in lowered for word in ("because", "therefore", "so that")):
        return "metric_evasion"
    if answer.count("?") >= 2 and not any(word in lowered for word in ("tension", "because", "means")):
        return "question_evasion"
    if lowered.startswith("for:") and lowered.count("for:") >= 2 and "tension" not in lowered:
        return "balanced_mush"
    return None


def check_scene(scene: dict) -> list[str]:
    problems = []
    kind = scene.get("kind")
    answer = scene.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return ["missing non-empty answer"]
    if kind == "positive":
        rules = scene.get("rules")
        if not isinstance(rules, list) or not rules:
            problems.append("positive scene has no rules")
        elif set(rules) - RULE_IDS:
            problems.append(f"unknown rule IDs: {sorted(set(rules) - RULE_IDS)}")
        for phrase in scene.get("required", []):
            if phrase not in answer:
                problems.append(f"missing required phrase {phrase!r}")
        lowered = answer.lower()
        for phrase in scene.get("forbidden", []):
            if phrase.lower() in lowered:
                problems.append(f"forbidden phrase {phrase!r}")
        observed = classify_failure(answer)
        if observed:
            problems.append(f"positive scene classified as {observed}")
    elif kind == "negative":
        expected = scene.get("failure")
        if expected not in {
            "hard_evasion", "soft_evasion", "metric_evasion", "question_evasion",
            "balanced_mush", "process_leakage", "manufactured_insight",
            "concise_unsupported",
        }:
            problems.append(f"unknown negative failure {expected!r}")
        observed = classify_failure(answer)
        if observed != expected:
            problems.append(f"expected {expected!r}, got {observed!r}")
    else:
        problems.append(f"unknown scene kind {kind!r}")
    return problems


def check_fixture(path: pathlib.Path = DEFAULT_FIXTURE) -> list[str]:
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"cannot load fixture: {error}"]
    if fixture.get("schema_version") != 1:
        return ["unsupported fixture schema_version"]
    if fixture.get("privacy") != "synthetic_only":
        return ["fixture must declare synthetic_only privacy"]
    scenes = fixture.get("scenes")
    if not isinstance(scenes, list):
        return ["scenes must be a list"]
    seen, problems = set(), []
    for scene in scenes:
        scene_id = scene.get("id") if isinstance(scene, dict) else None
        if not isinstance(scene_id, str) or not scene_id:
            problems.append("scene has no id")
            continue
        if scene_id in seen:
            problems.append(f"duplicated scene ID {scene_id!r}")
        seen.add(scene_id)
        problems.extend(f"{scene_id}: {problem}" for problem in check_scene(scene))
    required = {
        "selling_comparison", "computed_consider", "no_book_framing",
        "healthy_alignment", "a01_strategy_class_map", "a07_sector_is_not_broad",
        "a10_tactical_stop_not_long_horizon_policy", "concise_unsupported", "hard_evasion",
        "soft_evasion", "metric_dump", "question_outsourcing", "balanced_mush",
        "process_leakage", "manufactured_insight",
    }
    missing = required - seen
    if missing:
        problems.append(f"missing required scenes: {sorted(missing)}")
    return problems


def main() -> int:
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) == 2 else DEFAULT_FIXTURE
    problems = check_fixture(path)
    if problems:
        print("FAIL: output-voice witnesses")
        print("\n".join(f"- {problem}" for problem in problems))
        return 1
    print("PASS: output-voice witnesses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
