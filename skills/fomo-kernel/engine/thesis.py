#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic thesis reconstruction and add-decision validation.

The agent may interpret motives and propose wording.  This module owns the
append-only semantics and the evidence gate so "new evidence" cannot be emitted
without an explicit delta that future reviews can revisit.
"""
from __future__ import annotations

import hashlib
import json
import os


ADD_DECISIONS = {
    "planned_tranche",
    "new_evidence",
    "valuation_change",
    "price_only",
    "skip",
}


class ThesisError(ValueError):
    pass


def read_jsonl(path):
    rows = []
    if not path or not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def reconstruct_active(rows, active_cycle_ids=None):
    """Return exactly one latest thesis per active position cycle.

    Non-thesis events (exit narratives and v2 thesis decisions) are ignored.
    Append order is authoritative; a later revision replaces the earlier row for
    the same cycle without erasing history.
    """
    active_cycle_ids = set(active_cycle_ids or [])
    latest = {}
    for row in rows or []:
        if row.get("event"):
            continue
        cycle_id = row.get("cycle_id")
        if not cycle_id:
            continue
        latest[cycle_id] = row
    if active_cycle_ids:
        latest = {cid: row for cid, row in latest.items() if cid in active_cycle_ids}
    return [latest[cid] for cid in sorted(latest)]


def _answer_map(answers):
    rows = answers.get("answers") if isinstance(answers, dict) else None
    if not isinstance(rows, list):
        raise ThesisError("answers.answers must be an array")
    out = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("question_id"):
            raise ThesisError("every answer needs question_id")
        if row["question_id"] in out:
            raise ThesisError(f"duplicate answer: {row['question_id']}")
        out[row["question_id"]] = row
    return out


def validate_required_answers(plan, answers, allow_commitment_missing=False):
    amap = _answer_map(answers)
    missing = []
    allowed = {}
    for q in plan.get("question_queue") or []:
        options = {o.get("value") for o in q.get("options") or [] if o.get("value")}
        allowed[q.get("id")] = options
        if q.get("required") and q.get("id") not in amap:
            missing.append(q.get("id"))
    if missing:
        raise ThesisError("missing required answers: " + ", ".join(missing))
    for qid, answer in amap.items():
        if qid not in allowed:
            raise ThesisError(f"answer references unknown question: {qid}")
        choice = answer.get("choice")
        if choice not in allowed[qid]:
            raise ThesisError(f"invalid choice for {qid}: {choice}")
    if not allow_commitment_missing and not isinstance(answers.get("commitment"), dict):
        raise ThesisError("answers.commitment is required before finalize")
    return amap


def _decision_id(session_id, question_id, choice, payload):
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]
    return f"decision-{session_id}-{question_id}-{choice}-{digest}"


def build_decision_events(plan, answers):
    """Create auditable add-decision events and enforce evidence semantics."""
    amap = validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    session_id = plan.get("session_id")
    for q in plan.get("question_queue") or []:
        if q.get("kind") != "add_thesis" or q.get("id") not in amap:
            continue
        answer = amap[q["id"]]
        choice = answer.get("choice")
        if choice not in ADD_DECISIONS:
            raise ThesisError(f"unsupported add decision: {choice}")
        evidence = answer.get("evidence_delta")
        note = (answer.get("note") or "").strip() or None
        if choice == "new_evidence":
            if not isinstance(evidence, dict):
                raise ThesisError(f"{q['id']}: new_evidence requires evidence_delta")
            absent = [key for key in ("claim", "source") if not str(evidence.get(key) or "").strip()]
            if absent:
                raise ThesisError(f"{q['id']}: evidence_delta missing {', '.join(absent)}")
        elif evidence is not None:
            raise ThesisError(f"{q['id']}: evidence_delta is only valid with new_evidence")
        if choice in {"planned_tranche", "valuation_change"} and not note:
            raise ThesisError(f"{q['id']}: {choice} requires a short note")
        event = {
            "event": "thesis_decision",
            "schema_version": 1,
            "session_id": session_id,
            "cycle_id": q.get("cycle_id"),
            "ticker": q.get("ticker"),
            "decision": choice,
            "note": note,
            "evidence_delta": evidence,
            "review_date": (plan.get("engine_state") or {}).get("date_end"),
        }
        event["decision_id"] = _decision_id(session_id, q["id"], choice, event)
        events.append(event)
    return events


def validate_thesis_updates(rows, active_positions):
    """Validate agent-authored thesis revisions against engine-owned cycle ids."""
    active_positions = active_positions or {}
    valid_cycles = {p.get("cycle_id") for p in active_positions.values() if p.get("cycle_id")}
    seen = set()
    for index, row in enumerate(rows or []):
        if not isinstance(row, dict):
            raise ThesisError(f"thesis_updates[{index}] must be an object")
        cycle_id = row.get("cycle_id")
        if cycle_id not in valid_cycles:
            raise ThesisError(f"thesis_updates[{index}] has unknown/inactive cycle_id: {cycle_id}")
        if cycle_id in seen:
            raise ThesisError(f"more than one thesis update for cycle: {cycle_id}")
        seen.add(cycle_id)
        if row.get("maturity") not in {"inferred", "testable", "draft"}:
            raise ThesisError(f"thesis_updates[{index}] has invalid maturity")
        for key in ("ticker", "why", "exit_trigger"):
            if not str(row.get(key) or "").strip():
                raise ThesisError(f"thesis_updates[{index}] missing {key}")
    return rows or []
