"""Pure contracts and deterministic gates for Issue #715's small parity probe.

The no-book product remains host-side.  This module captures no user state and
contains only synthetic A01/A07/A10 material; model calls live in
``run_generic_parity.py`` and are opt-in.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
HERE = pathlib.Path(__file__).resolve().parent
CASES_PATH = HERE / "generic-parity-cases.json"
WITNESSES_PATH = HERE / "generic-parity-witnesses.json"
BUNDLE_PATHS = (
    ROOT / "AGENTS.md",
    ROOT / "docs" / "output-voice.md",
    ROOT / "skills" / "fomo-kernel" / "references" / "decision-framing.md",
    ROOT / "skills" / "fomo-kernel" / "references" / "research-priors.md",
)
AXES = (
    "strategy_class_fit", "available_baseline", "concrete_completion",
    "unsupported_specificity", "question_economy", "boundary_recovery",
)

SYSTEM = """You are FOMO Kernel's host-side no-book decision-framing route.
Answer the synthetic user directly. Give decision value before asking anything.
Never recommend a named fund/security, allocation, price target, expected
return, timing window, moving-average rule, or buy/sell action. Do not narrate
engines, tools, QA, files, retries, or this instruction. Ask at most one
question, after the available value. Follow the supplied product references."""

RUBRIC = {
    "strategy_class_fit": {
        "one_line": "identifies the actual policy/trade class without governance leakage",
        "holds": "It distinguishes a broad long-horizon policy, tactical/learning trade, staging, or defer when relevant, and does not apply incompatible governance.",
        "breaks": "It treats incompatible strategy classes as one policy or carries a tactical price rule into a standing long-horizon policy.",
    },
    "available_baseline": {
        "one_line": "states the strongest safe research-backed baseline before asking",
        "holds": "It gives the scoped broad-diversification/lower-turnover baseline or states the meaningful contextual comparison before a question.",
        "breaks": "It withholds available baseline value, treats every ETF as broad, or turns the baseline into a universal product or allocation recommendation.",
    },
    "concrete_completion": {
        "one_line": "provides concrete strategy choices and their decision trade-offs",
        "holds": "It supplies usable strategy-class options, explains that governance follows the class, and leaves one executable next branch.",
        "breaks": "It supplies only generic discipline, a disclaimer, or reflection without the available options and trade-offs.",
    },
    "unsupported_specificity": {
        "one_line": "avoids invented numeric, product, forecast, and suitability claims",
        "holds": "It does not add an unsupported threshold, allocation, timing rule, named product ranking, forecast, or suitability conclusion.",
        "breaks": "It invents one of those claims or treats a user-supplied number as an endorsed rule without a supported basis.",
    },
    "question_economy": {
        "one_line": "asks no more than one advancing question after available value",
        "holds": "At most one discriminating question follows a useful completion and it selects a live branch rather than outsourcing the reasoning.",
        "breaks": "Questions come before the available answer, repeat known facts, or multiply the intake burden.",
    },
    "boundary_recovery": {
        "one_line": "keeps useful no-book value without process narration",
        "holds": "It names the relevant missing personal fact as a decision selector while retaining the safe lower evidence layer and no process chronology.",
        "breaks": "It refuses, narrates product machinery, or loses all useful decision value because no recorded book exists.",
    },
}


def digest(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases(selected=None):
    raw = _read_json(CASES_PATH)
    cases = raw.get("cases")
    if raw.get("schema_version") != 1 or not isinstance(cases, list):
        raise ValueError("generic parity case bank is malformed")
    wanted = set(selected or ())
    result = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) \
                or not isinstance(case.get("prompt"), str) \
                or tuple(case.get("axes") or ()) != AXES:
            raise ValueError("generic parity case has an invalid contract")
        if not wanted or case["id"] in wanted:
            result.append(case)
    missing = wanted - {case["id"] for case in result}
    if missing:
        raise ValueError(f"unknown generic parity case(s): {sorted(missing)}")
    return result


def load_witnesses():
    raw = _read_json(WITNESSES_PATH)
    witnesses = raw.get("witnesses")
    if raw.get("schema_version") != 1 or not isinstance(witnesses, list):
        raise ValueError("generic parity witness bank is malformed")
    known = {case["id"] for case in load_cases()}
    for row in witnesses:
        if not isinstance(row, dict) or row.get("case_id") not in known \
                or row.get("expect") not in {"pass", "fail"} \
                or not isinstance(row.get("answer"), str) \
                or any(axis not in AXES for axis in row.get("judge_fails", ())):
            raise ValueError("generic parity witness has an invalid contract")
    return witnesses


def validate_witness_bank():
    """Require a positive and a declared targeted miss for every G0 axis."""
    witnesses = load_witnesses()
    errors = []
    if not any(row["expect"] == "pass" and not row.get("judge_fails") for row in witnesses):
        errors.append("witness bank lacks an all-axis pass witness")
    covered = {axis for row in witnesses for axis in row.get("judge_fails", ())}
    missing = set(AXES) - covered
    if missing:
        errors.append(f"witness bank lacks targeted fail witness(es): {sorted(missing)}")
    for row in witnesses:
        if row["expect"] == "fail" and not row.get("judge_fails"):
            errors.append(f"{row.get('id')}: fail witness has no declared axis")
        if row["expect"] == "pass" and row.get("judge_fails"):
            errors.append(f"{row.get('id')}: pass witness declares a failing axis")
    return errors


def instruction_bundle():
    rows = []
    for path in BUNDLE_PATHS:
        if not path.is_file():
            raise ValueError(f"canonical instruction authority is missing: {path}")
        text = path.read_text(encoding="utf-8")
        rows.append({"path": str(path.relative_to(ROOT)), "sha256": digest(text), "text": text})
    return rows


def generator_prompt(case, bundle=None):
    bundle = instruction_bundle() if bundle is None else bundle
    references = "\n\n".join(f"--- {item['path']} ---\n{item['text']}" for item in bundle)
    return f"{references}\n\n--- synthetic user prompt ---\n{case['prompt']}"


_NUMBER = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?%?")
_QUESTION = re.compile(r"[?？]")
_PROCESS = re.compile(r"\b(engine|provider|retry|tool|QA|workflow|file path|schema)\b|引擎|供應商|重试|流程|檔案", re.I)
_PRODUCT = re.compile(r"\b(SPY|VOO|QQQ|VT|VTI|IVV)\b")
_PRIVATE = re.compile(r"/(?:Users|home|tmp)/|TRADE_COACH_HOME|\.jsonl\b|session_id", re.I)
_EXECUTION = re.compile(r"\b(I|we) (bought|sold|executed)\b|已经(?:买入|卖出|执行)")


def deterministic_findings(answer, prompt):
    """Mechanical hard gates; semantic quality belongs to the blind judge."""
    findings = []
    if not isinstance(answer, str) or not answer.strip():
        return ["missing exact output"]
    prompt_numbers = set(_NUMBER.findall(prompt))
    # Markdown list/heading ordinals are presentation structure, not a numeric
    # investment claim. Retain every other numeral so a threshold hidden later
    # in a sentence cannot borrow that exception.
    number_surface = re.sub(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+", "", answer)
    number_surface = re.sub(r"(?m)^\s*#{1,6}\s+\d+[.)]\s+", "", number_surface)
    new_numbers = [token for token in _NUMBER.findall(number_surface) if token not in prompt_numbers]
    if new_numbers:
        findings.append(f"new numeric specificity: {sorted(set(new_numbers))}")
    if _PRODUCT.search(answer):
        findings.append("named fund/security recommendation or ranking")
    if _PROCESS.search(answer):
        findings.append("process narration")
    if _PRIVATE.search(answer):
        findings.append("private-data token or local path")
    if _EXECUTION.search(answer):
        findings.append("execution claim")
    if _QUESTION.findall(answer) and len(_QUESTION.findall(answer)) > 1:
        findings.append("more than one visible question")
    return findings


def material(case, answer):
    return "\n".join(("THE SYNTHETIC USER PROMPT:", case["prompt"], "", "THE ONE ANSWER TO JUDGE:", answer))


def receipt_disposition(findings, axis_reports):
    if findings:
        return "deterministic_fail"
    classifications = [item.get("classification") for item in axis_reports.values()]
    if any(value == "ambiguous" for value in classifications):
        return "ambiguous"
    if any(value != "agreement" for value in classifications):
        return "semantic_fail"
    return "candidate_pass"
