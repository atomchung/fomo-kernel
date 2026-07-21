#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validated private question surfaces for engine-selected opportunities.

The review engine still selects every question and owns its canonical answer
contract.  This module accepts only presentation copy for the three explicitly
enabled question kinds (add_thesis, headline_motive, initial_thesis), binds it
one-to-one to the engine choices, and returns an adapter-neutral presentation.
It performs no file or network access.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re


SCHEMA_VERSION = 1
ELIGIBLE_KINDS = frozenset({"add_thesis", "headline_motive", "initial_thesis"})
MAPPING_CONFIDENCE = frozenset({"high", "medium", "low"})
GROUNDING_REFS = frozenset({
    "context.ticker",
    "context.prior_thesis.text",
    "context.asked_because",
    "context.headline_dimension.label",
})

_ADD_REQUIREMENTS = {
    "new_evidence": ["evidence_delta.claim", "evidence_delta.source"],
    "planned_tranche": ["note"],
    "valuation_change": ["note"],
    "price_only": [],
    "skip": [],
}
_HEADLINE_REQUIREMENTS = {
    "deliberate_plan": [],
    "emotional_reaction": [],
    "external_constraint": [],
    "skip": [],
}
_INITIAL_THESIS_REQUIREMENTS = {
    "planned_entry": [],
    "momentum_follow": [],
    "external_call": [],
    "no_clear_thesis": [],
    "skip": [],
}
_DIGIT = re.compile(r"\d+(?:[.,]\d+)?%?")


class QuestionSurfaceError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _requirement_copy(language, kind):
    en = str(language).lower().startswith("en")
    if kind == "add_thesis":
        return {
            "new_evidence": ("Requires both the observed claim and its source."
                             if en else "必須同時提供觀察到的主張與來源。"),
            "planned_tranche": ("Requires a short note describing the pre-existing plan."
                                if en else "必須用一句短註記錄原先就存在的分批計畫。"),
            "valuation_change": ("Requires a short note describing the valuation change."
                                 if en else "必須用一句短註記錄估值判斷如何改變。"),
            "price_only": "",
            "skip": "",
        }
    if kind == "initial_thesis":
        return {key: "" for key in _INITIAL_THESIS_REQUIREMENTS}
    return {key: "" for key in _HEADLINE_REQUIREMENTS}


def build_opportunity(question, language, *, prior_thesis=None, headline_dimension=None):
    """Build the engine-owned opportunity beside the display-ready fallback."""
    kind = question.get("kind")
    if kind not in ELIGIBLE_KINDS:
        return None
    choices = [row.get("value") for row in question.get("options") or []]
    if not choices or any(not isinstance(value, str) or not value for value in choices):
        raise QuestionSurfaceError(f"{question.get('id')}: canonical choices are incomplete")
    context = {}
    if kind == "add_thesis":
        if question.get("ticker"):
            context["ticker"] = question["ticker"]
        if isinstance(prior_thesis, dict) and str(prior_thesis.get("text") or "").strip():
            context["prior_thesis"] = {
                "text": str(prior_thesis["text"]),
                "voice": prior_thesis.get("voice") or "user_confirmed",
            }
        if question.get("asked_because"):
            context["asked_because"] = question["asked_because"]
        intent = "classify_losing_position_add"
        requirements = _ADD_REQUIREMENTS
    elif kind == "initial_thesis":
        if question.get("ticker"):
            context["ticker"] = question["ticker"]
        if question.get("asked_because"):
            context["asked_because"] = question["asked_because"]
        intent = "classify_initial_thesis"
        requirements = _INITIAL_THESIS_REQUIREMENTS
    else:
        if isinstance(headline_dimension, dict):
            context["headline_dimension"] = {
                "id": headline_dimension.get("id"),
                "label": headline_dimension.get("label"),
            }
        intent = "classify_headline_motive"
        requirements = _HEADLINE_REQUIREMENTS
    expected = set(choices)
    if expected != set(requirements) or len(choices) != len(expected):
        raise QuestionSurfaceError(f"{question.get('id')}: canonical answer contract drifted")
    return {
        "schema_version": SCHEMA_VERSION,
        "intent": intent,
        "context": context,
        "answer_contract": {
            "canonical_choices": choices,
            "requirements_by_choice": {key: list(requirements[key]) for key in choices},
            "requirement_text_by_choice": _requirement_copy(language, kind),
            "allow_none_of_above": True,
            "max_clarifications": 1,
        },
    }


def _strict_keys(value, required, optional, label):
    if not isinstance(value, dict):
        raise QuestionSurfaceError(f"{label} must be an object")
    missing = sorted(set(required) - set(value))
    extra = sorted(set(value) - set(required) - set(optional))
    if missing:
        raise QuestionSurfaceError(f"{label} is missing: {', '.join(missing)}")
    if extra:
        raise QuestionSurfaceError(f"{label} changes engine-owned fields: {', '.join(extra)}")


def _text(value, label, maximum):
    if not isinstance(value, str) or not value.strip():
        raise QuestionSurfaceError(f"{label} must be non-empty text")
    if len(value) > maximum:
        raise QuestionSurfaceError(f"{label} exceeds {maximum} characters")
    return value


def _resolve_context_ref(context, ref):
    if not isinstance(ref, str) or ref not in GROUNDING_REFS:
        raise QuestionSurfaceError(f"invalid grounding ref: {ref}")
    value = context
    for part in ref.split(".")[1:]:
        if not isinstance(value, dict) or part not in value:
            raise QuestionSurfaceError(f"grounding ref is not in the opportunity: {ref}")
        value = value[part]
    if isinstance(value, (dict, list)) or value is None or str(value) == "":
        raise QuestionSurfaceError(f"grounding ref must resolve to one supplied fact: {ref}")
    return str(value)


def _validate_grounded_text(text, refs, context, label, *, extra_refs=None):
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        raise QuestionSurfaceError(f"{label}.grounding_refs must be an array of strings")
    values = []
    for ref in refs:
        if extra_refs and ref in extra_refs:
            value = str(extra_refs[ref])
        else:
            value = _resolve_context_ref(context, ref)
        if value.casefold() not in text.casefold():
            raise QuestionSurfaceError(f"{label} does not contain its grounded fact: {ref}")
        values.append(value)
    grounded = " ".join(values)
    invented = [token for token in _DIGIT.findall(text) if token not in grounded]
    if invented:
        raise QuestionSurfaceError(f"{label} contains an ungrounded numeric fact: {invented[0]}")


def _question_map(plan):
    return {row.get("id"): row for row in plan.get("question_queue") or [] if row.get("id")}


def validate_surfaces(plan, artifact):
    """Validate AI-authored presentation copy without changing engine decisions."""
    _strict_keys(artifact, {"schema_version", "session_id", "surfaces"}, set(),
                 "question surface artifact")
    if artifact.get("schema_version") != SCHEMA_VERSION:
        raise QuestionSurfaceError("question surface artifact has unsupported schema_version")
    if artifact.get("session_id") != plan.get("session_id"):
        raise QuestionSurfaceError("question surface session_id does not match Review Plan")
    surfaces = artifact.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        raise QuestionSurfaceError("question surface artifact needs at least one surface")
    questions = _question_map(plan)
    seen = set()
    for index, surface in enumerate(surfaces):
        label = f"surfaces[{index}]"
        _strict_keys(surface,
                     {"question_id", "stem", "stem_grounding_refs", "options", "none_of_above"},
                     {"clarification"}, label)
        question_id = surface.get("question_id")
        if question_id in seen:
            raise QuestionSurfaceError(f"duplicate question surface: {question_id}")
        seen.add(question_id)
        question = questions.get(question_id)
        if not question:
            raise QuestionSurfaceError(f"question surface references unknown question: {question_id}")
        if question.get("kind") not in ELIGIBLE_KINDS or not isinstance(question.get("question_opportunity"), dict):
            raise QuestionSurfaceError(f"question kind remains engine-rendered: {question_id}")
        opportunity = question["question_opportunity"]
        context = opportunity.get("context") or {}
        stem = _text(surface.get("stem"), f"{label}.stem", 1000)
        stem_refs = surface.get("stem_grounding_refs")
        if not stem_refs:
            raise QuestionSurfaceError(f"{label}.stem must cite at least one opportunity fact")
        _validate_grounded_text(stem, stem_refs, context, f"{label}.stem")
        options = surface.get("options")
        if not isinstance(options, list):
            raise QuestionSurfaceError(f"{label}.options must be an array")
        canonical_choices = (opportunity.get("answer_contract") or {}).get("canonical_choices") or []
        mapped = [row.get("maps_to") if isinstance(row, dict) else None for row in options]
        if mapped != canonical_choices:
            raise QuestionSurfaceError(
                f"{question_id}: surface options must map exactly once in canonical order"
            )
        for option_index, option in enumerate(options):
            option_label = f"{label}.options[{option_index}]"
            _strict_keys(option, {"label", "description", "maps_to", "grounding_refs"},
                         set(), option_label)
            display_label = _text(option.get("label"), f"{option_label}.label", 160)
            description = _text(option.get("description"), f"{option_label}.description", 700)
            refs = option.get("grounding_refs")
            _validate_grounded_text(
                display_label + " " + description, refs, context, option_label
            )
        none = surface.get("none_of_above")
        _strict_keys(none, {"label", "description"}, set(), f"{label}.none_of_above")
        none_text = (_text(none.get("label"), f"{label}.none_of_above.label", 160) + " " +
                     _text(none.get("description"), f"{label}.none_of_above.description", 500))
        _validate_grounded_text(none_text, [], context, f"{label}.none_of_above")
        clarification = surface.get("clarification")
        if clarification is not None:
            _strict_keys(clarification, {"stem", "grounding_refs"}, set(),
                         f"{label}.clarification")
            clarification_stem = _text(
                clarification.get("stem"), f"{label}.clarification.stem", 1000
            )
            clarification_refs = clarification.get("grounding_refs")
            if not clarification_refs:
                raise QuestionSurfaceError(
                    f"{label}.clarification must cite at least one opportunity fact"
                )
            _validate_grounded_text(
                clarification_stem, clarification_refs, context, f"{label}.clarification"
            )
    return copy.deepcopy(artifact)


def _presentation_digest(presentation):
    body = dict(presentation)
    body.pop("surface_digest", None)
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def build_presentations(plan, artifact=None):
    """Resolve validated custom copy or the unchanged engine fallback in queue order."""
    dynamic = {row["question_id"]: row for row in (artifact or {}).get("surfaces") or []}
    presentations = []
    for question in plan.get("question_queue") or []:
        opportunity = question.get("question_opportunity") or {}
        surface = dynamic.get(question.get("id"))
        if surface:
            contract = opportunity["answer_contract"]
            fallback_by_value = {row.get("value"): row for row in question.get("options") or []}
            options = []
            for authored in surface["options"]:
                value = authored["maps_to"]
                fallback = fallback_by_value[value]
                options.append({
                    "value": value,
                    "label": authored["label"],
                    "description": authored["description"],
                    "semantic_anchor": fallback["description"],
                    "payload_requirements": list(contract["requirements_by_choice"][value]),
                    "requirement_text": contract["requirement_text_by_choice"].get(value) or "",
                    "grounding_refs": list(authored.get("grounding_refs") or []),
                })
            row = {
                "question_id": question["id"],
                "kind": question["kind"],
                "required": question["required"],
                "source": "validated_dynamic",
                "stem": surface["stem"],
                "stem_grounding_refs": list(surface["stem_grounding_refs"]),
                "options": options,
                "none_of_above": copy.deepcopy(surface["none_of_above"]),
                "max_clarifications": contract["max_clarifications"],
            }
            if surface.get("clarification") is not None:
                row["clarification"] = copy.deepcopy(surface["clarification"])
        else:
            row = {
                "question_id": question["id"],
                "kind": question["kind"],
                "required": question["required"],
                "source": "engine_fallback",
                "stem": question["question"],
                "options": copy.deepcopy(question.get("options") or []),
            }
            if opportunity:
                row["max_clarifications"] = opportunity["answer_contract"]["max_clarifications"]
        row["surface_digest"] = _presentation_digest(row)
        presentations.append(row)
    return presentations


def _validate_response_provenance(question, answer, presentation=None):
    mode = answer.get("response_mode")
    provenance = answer.get("response_provenance")
    if mode is None and provenance is None:
        return 0
    if question.get("kind") not in ELIGIBLE_KINDS:
        raise QuestionSurfaceError(f"{question.get('id')}: own-words mapping is not enabled for this kind")
    if mode == "canonical_choice":
        if provenance is not None:
            raise QuestionSurfaceError(f"{question.get('id')}: canonical_choice must not add AI interpretation")
        return 0
    if mode != "own_words":
        raise QuestionSurfaceError(f"{question.get('id')}: invalid response_mode")
    _strict_keys(
        provenance,
        {"user_statement", "motive_summary", "summary_author", "mapping_confidence",
         "unresolved", "interpretation_confirmed"},
        {"clarification"},
        f"{question.get('id')}.response_provenance",
    )
    user_statement = _text(provenance.get("user_statement"), "user_statement", 1200)
    _text(provenance.get("motive_summary"), "motive_summary", 600)
    if provenance.get("summary_author") != "ai_interpretation":
        raise QuestionSurfaceError(f"{question.get('id')}: motive_summary must be attributed to AI interpretation")
    confidence = provenance.get("mapping_confidence")
    if confidence not in MAPPING_CONFIDENCE:
        raise QuestionSurfaceError(f"{question.get('id')}: invalid mapping_confidence")
    unresolved = provenance.get("unresolved")
    confirmed = provenance.get("interpretation_confirmed")
    if not isinstance(unresolved, bool) or not isinstance(confirmed, bool):
        raise QuestionSurfaceError(f"{question.get('id')}: unresolved and interpretation_confirmed must be booleans")
    if unresolved:
        if answer.get("choice") != "skip" or confidence != "low" or confirmed:
            raise QuestionSurfaceError(
                f"{question.get('id')}: unresolved own-words answers must remain low-confidence skip"
            )
        if answer.get("note") != user_statement:
            raise QuestionSurfaceError(f"{question.get('id')}: unresolved skip must preserve the exact user statement in note")
    elif answer.get("choice") == "skip" or not confirmed:
        raise QuestionSurfaceError(
            f"{question.get('id')}: a mapped own-words answer requires explicit user confirmation"
        )
    clarification = provenance.get("clarification")
    if clarification is None:
        return 0
    _strict_keys(clarification, {"stem", "grounding_refs", "user_statement"}, set(),
                 f"{question.get('id')}.clarification")
    stem = _text(clarification.get("stem"), "clarification.stem", 1000)
    _text(clarification.get("user_statement"), "clarification.user_statement", 1200)
    frozen = (presentation or {}).get("clarification")
    if not isinstance(frozen, dict):
        raise QuestionSurfaceError(
            f"{question.get('id')}: clarification was not validated before presentation"
        )
    if stem != frozen.get("stem") or clarification.get("grounding_refs") != frozen.get("grounding_refs"):
        raise QuestionSurfaceError(
            f"{question.get('id')}: clarification differs from the frozen private surface"
        )
    context = (question.get("question_opportunity") or {}).get("context") or {}
    refs = clarification.get("grounding_refs")
    if not refs:
        raise QuestionSurfaceError(f"{question.get('id')}: clarification must cite supplied context")
    _validate_grounded_text(stem, refs, context, "clarification.stem",
                            extra_refs=None)
    return 1


def validate_answer_contract(plan, answers, presentations=None):
    """Validate private interpretation provenance against frozen presentations."""
    questions = _question_map(plan)
    answer_rows = answers.get("answers") if isinstance(answers, dict) else None
    if not isinstance(answer_rows, list):
        raise QuestionSurfaceError("answers.answers must be an array")
    expected = {row["question_id"]: row for row in presentations or build_presentations(plan)}
    for index, answer in enumerate(answer_rows):
        _strict_keys(
            answer, {"question_id", "choice"},
            {"note", "evidence_delta", "response_mode", "response_provenance"},
            f"answers[{index}]",
        )
        question = questions.get(answer.get("question_id"))
        if not question:
            continue  # the canonical required-answer validator owns this error
        _validate_response_provenance(question, answer, expected.get(question["id"]))
