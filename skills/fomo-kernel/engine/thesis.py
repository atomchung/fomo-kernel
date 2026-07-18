#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic thesis reconstruction and add-decision validation.

The agent may interpret motives and propose wording.  This module owns the
append-only semantics and the evidence gate so "new evidence" cannot be emitted
without an explicit delta that future reviews can revisit.
"""
from __future__ import annotations

import datetime as dt
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
THESIS_STATUSES = {"open", "still", "modified", "falsified", "closed"}
SOURCE_STATES = {"captured", "confirmed", "evaluated"}
# Inference-only accumulation vocabularies (#155/#38). These fields cannot be
# backfilled, so a misspelled value ("FOMO", "comfirmed") would fragment the
# store permanently — reject it at the gate instead of persisting it.
INFERENCE_ENUMS = {
    "emotion": {"fomo", "composed", "forced", "planned"},
    "confidence": {"high", "medium", "low"},
    "source_type": {"kol", "research", "self", "other"},
    "source_confidence": {"candidate", "confirmed"},
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


def _digest(prefix, payload):
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def stable_thesis_id(cycle_id):
    """Return the engine-owned thesis identity for a new position cycle."""
    if not cycle_id:
        raise ThesisError("stable thesis identity requires cycle_id")
    return _digest("thesis", {"cycle_id": cycle_id})


def stable_event_id(kind, payload):
    """Return a content-addressed event identity without relying on array order."""
    return _digest(kind, payload)


def _event_date(row):
    for key in ("session_date", "review_date", "recorded_at", "exit_date"):
        if row.get(key):
            return str(row[key])
    session_id = str(row.get("session_id") or "")
    prefix = session_id.split("__", 1)[0]
    return prefix if len(prefix) == 10 else ""


def _normalize_thesis_row(row):
    out = dict(row)
    cycle_id = out.get("cycle_id")
    if not out.get("thesis_id"):
        out["thesis_id"] = stable_thesis_id(cycle_id)
    if not out.get("event_id"):
        out["event_id"] = stable_event_id("legacy-thesis", row)
    if out.get("status") in {None, "active"}:
        out["status"] = "open"
    out.setdefault("position_status", "closed" if out.get("status") in {"closed", "falsified"} else "open")
    out["last_event_id"] = out["event_id"]
    return out


def _row_event_id(row):
    if row.get("event_id"):
        return row["event_id"]
    kind = row.get("event")
    return stable_event_id(f"legacy-{kind}" if kind else "legacy-thesis", row)


def _chain_depth(row, by_id, memo, visiting=None):
    event_id = _row_event_id(row)
    if event_id in memo:
        return memo[event_id]
    visiting = set(visiting or [])
    if event_id in visiting:
        memo[event_id] = 0
        return 0
    visiting.add(event_id)
    parent = by_id.get(row.get("revises"))
    depth = 0 if parent is None else _chain_depth(parent, by_id, memo, visiting) + 1
    memo[event_id] = depth
    return depth


def _evidence_record(event, default_source_state="captured"):
    """Normalize evidence provenance without upgrading legacy confidence."""
    delta = event.get("evidence_delta")
    if not isinstance(delta, dict):
        return None
    claim = str(delta.get("claim") or "").strip()
    source = str(delta.get("source") or "").strip()
    if not claim or not source:
        return None
    provenance = event.get("provenance") if isinstance(event.get("provenance"), dict) else {}
    source_state = provenance.get("source_state") or default_source_state
    if source_state not in SOURCE_STATES:
        source_state = default_source_state
    identity = {
        "thesis_id": event.get("thesis_id"),
        "cycle_id": event.get("cycle_id"),
        "claim": claim,
        "source": source,
        "observed_at": delta.get("observed_at"),
    }
    return {
        "evidence_id": event.get("evidence_id") or stable_event_id("evidence", identity),
        "claim": claim,
        "source": source,
        "observed_at": delta.get("observed_at"),
        "falsifier": delta.get("falsifier"),
        "source_state": source_state,
        "captured_at": provenance.get("captured_at") or event.get("review_date"),
        "evaluation": event.get("evaluation") or {"state": "pending", "evaluated_at": None},
        "decision_event_id": _row_event_id(event),
    }


def reconstruct_states(rows, decision_rows=None, active_cycle_ids=None):
    """Fold thesis, decision, and exit events into one state per position cycle.

    Legacy rows are normalized lazily in memory. Nothing is rewritten, so sessions
    produced before stable event identities remain compatible with projection repair.
    """
    active_cycle_ids = set(active_cycle_ids or [])
    events = []
    for source_rank, source in enumerate((rows or [], decision_rows or [])):
        for index, row in enumerate(source):
            if isinstance(row, dict) and row.get("cycle_id"):
                kind_rank = {None: 0, "thesis_cycle_relink": 0,
                             "thesis_decision": 1, "exit_narrative": 2}.get(row.get("event"), 3)
                fallback = (_event_date(row), str(row.get("session_id") or ""), kind_rank,
                            source_rank, index)
                events.append((fallback, row))
    by_id = {_row_event_id(row): row for _fallback, row in events}
    depth_memo = {}
    events.sort(key=lambda item: (_chain_depth(item[1], by_id, depth_memo), item[0]))

    states = {}
    for _key, raw in events:
        cycle_id = raw.get("cycle_id")
        kind = raw.get("event")
        if not kind or kind == "thesis_cycle_relink":
            current = states.get(cycle_id)
            row = _normalize_thesis_row(raw)
            if current:
                row.setdefault("revises", current.get("last_event_id") or current.get("event_id"))
                for key in ("decision_cursor", "last_decision", "last_exit", "final_outcome",
                            "evidence_history", "last_evidence", "source_state"):
                    if key not in row and key in current:
                        row[key] = current[key]
            states[cycle_id] = row
            continue

        current = states.get(cycle_id)
        if not current:
            # A decision without thesis content cannot cover a missing thesis. Keep
            # it in history, but do not manufacture an active thesis from metadata.
            continue
        event = dict(raw)
        event.setdefault("event_id", stable_event_id(f"legacy-{kind}", raw))
        current["last_event_id"] = event["event_id"]
        if kind == "thesis_decision":
            current["last_decision"] = event
            if event.get("decision_cursor"):
                current["decision_cursor"] = event["decision_cursor"]
            evidence = _evidence_record(event)
            if evidence:
                history = [row for row in current.get("evidence_history") or []
                           if row.get("evidence_id") != evidence["evidence_id"]]
                history.append(evidence)
                current["evidence_history"] = history
                current["last_evidence"] = evidence
                current["source_state"] = evidence["source_state"]
            if event.get("status") in THESIS_STATUSES:
                current["status"] = event["status"]
        elif kind == "exit_narrative":
            current["last_exit"] = event
            if event.get("exit_kind") == "full":
                final_status = "falsified" if event.get("exit_reason") == "thesis_broken" else "closed"
                current["status"] = final_status
                current["position_status"] = "closed"
                current["final_outcome"] = {
                    "status": final_status,
                    "side_state": "skipped" if event.get("capture") == "skipped" else "confirmed",
                    "event_id": event["event_id"],
                    "recorded_at": event.get("recorded_at") or event.get("exit_date"),
                }

    # An active engine cycle is authoritative for the position side only. Closed
    # outcomes remain explicit for cycles that disappeared from the latest CSV.
    for cycle_id, state in states.items():
        if active_cycle_ids and cycle_id in active_cycle_ids and not state.get("final_outcome"):
            state["position_status"] = "open"
    return [states[cycle_id] for cycle_id in sorted(states)]


def reconstruct_active(rows, active_cycle_ids=None, decision_rows=None):
    """Return the folded thesis state for currently active position cycles."""
    active_cycle_ids = set(active_cycle_ids or [])
    return [row for row in reconstruct_states(rows, decision_rows, active_cycle_ids)
            if (not active_cycle_ids or row.get("cycle_id") in active_cycle_ids)
            and row.get("position_status") != "closed"]


_RELINK_CONTENT_FIELDS = (
    "why", "horizon", "exit_trigger", "stop", "target_size", "driver",
    "maturity", "source_type", "source_name", "source_confidence",
    "emotion", "emotion_inferred", "confidence", "confidence_inferred",
)


def _iso_date(value):
    try:
        return dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def build_incomplete_snapshot_cycle_relinks(states, active_positions, session_id, review_date):
    """Build fail-closed, engine-owned links from provisional snapshot cycles.

    An incomplete opening snapshot knows that one ticker is held, but its
    snapshot-date cycle id is provisional.  A later transaction review can
    reveal an earlier start for the *same still-open holding*.  Reuse the
    inferred thesis only when one unambiguous candidate exists and the visible
    transaction cycle already existed at the snapshot date.  A cycle starting
    after the snapshot is a possible close/reopen and must receive a new thesis.
    """
    states = [row for row in (states or []) if isinstance(row, dict)]
    positions = active_positions or {}
    state_cycles = {row.get("cycle_id") for row in states if row.get("cycle_id")}
    open_by_ticker = {}
    for row in states:
        if (not row.get("ticker") or row.get("position_status") == "closed"
                or row.get("final_outcome")):
            continue
        open_by_ticker.setdefault(row["ticker"], []).append(row)

    review_day = _iso_date(review_date)
    relinks = []
    for ticker, position in sorted(positions.items()):
        if not isinstance(position, dict):
            continue
        target_cycle = position.get("cycle_id")
        if not target_cycle or target_cycle in state_cycles:
            continue
        open_states = open_by_ticker.get(ticker) or []
        if len(open_states) != 1:
            continue
        prior = open_states[0]
        provenance = prior.get("cycle_provenance")
        if not isinstance(provenance, dict):
            continue
        eligible = (
            prior.get("origin") == "snapshot"
            and prior.get("maturity") == "inferred"
            and prior.get("source_confidence") == "candidate"
            and provenance.get("kind") == "snapshot_inference"
            and provenance.get("snapshot_complete") is False
            and not prior.get("last_decision")
            and not prior.get("last_exit")
            and not prior.get("decision_cursor")
        )
        if not eligible:
            continue
        snapshot_day = _iso_date(provenance.get("snapshot_as_of"))
        cycle_day = _iso_date(position.get("cycle_start"))
        if snapshot_day is None or cycle_day is None or cycle_day > snapshot_day:
            continue
        if review_day is None or review_day < snapshot_day:
            continue
        revises = prior.get("last_event_id") or prior.get("event_id")
        if not prior.get("thesis_id") or not revises:
            continue

        row = {key: prior[key] for key in _RELINK_CONTENT_FIELDS if key in prior}
        row.update({
            "event": "thesis_cycle_relink",
            "schema_version": 2,
            "session_id": session_id,
            "session_date": review_day.isoformat(),
            "ticker": ticker,
            "cycle_id": target_cycle,
            "thesis_id": prior["thesis_id"],
            "revises": revises,
            "status": "open",
            "position_status": "open",
            "origin": "snapshot",
            "cycle_provenance": {
                "kind": "incomplete_snapshot_cycle_relink",
                "from_cycle_id": prior.get("cycle_id"),
                "snapshot_as_of": snapshot_day.isoformat(),
                "revealed_cycle_start": cycle_day.isoformat(),
                "basis": "unique_open_ticker",
            },
        })
        identity = dict(row)
        row["event_id"] = stable_event_id("thesis-cycle-relink", identity)
        relinks.append(row)
    return relinks


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


def build_decision_events(plan, answers, thesis_updates=None):
    """Create auditable add-decision events and enforce evidence semantics."""
    amap = validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    session_id = plan.get("session_id")
    updates = {row.get("cycle_id"): row for row in (thesis_updates or []) if row.get("cycle_id")}
    active = {row.get("cycle_id"): row for row in
              ((plan.get("state_snapshot") or {}).get("active_theses") or []) if row.get("cycle_id")}
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
            "schema_version": 2,
            "session_id": session_id,
            "cycle_id": q.get("cycle_id"),
            "ticker": q.get("ticker"),
            "decision": choice,
            "note": note,
            "evidence_delta": evidence,
            "review_date": (plan.get("engine_state") or {}).get("date_end"),
            "decision_cursor": q.get("decision_cursor"),
        }
        event["decision_id"] = _decision_id(session_id, q["id"], choice, event)
        prior = updates.get(q.get("cycle_id")) or active.get(q.get("cycle_id")) or {}
        event["thesis_id"] = prior.get("thesis_id") or q.get("prior_thesis_id") \
            or stable_thesis_id(q.get("cycle_id"))
        event["revises"] = prior.get("last_event_id") or prior.get("event_id") or q.get("prior_event_id")
        if choice == "new_evidence":
            evidence_identity = {
                "thesis_id": event["thesis_id"],
                "cycle_id": event.get("cycle_id"),
                "claim": str(evidence.get("claim") or "").strip(),
                "source": str(evidence.get("source") or "").strip(),
                "observed_at": evidence.get("observed_at"),
            }
            event["evidence_id"] = stable_event_id("evidence", evidence_identity)
            event["provenance"] = {
                "source": evidence_identity["source"],
                "source_state": "confirmed",
                "captured_at": event.get("review_date"),
                "observed_at": evidence.get("observed_at"),
            }
            event["evaluation"] = {"state": "pending", "evaluated_at": None}
        identity_payload = dict(event)
        identity_payload.pop("decision_id", None)
        event["event_id"] = stable_event_id("thesis-decision", identity_payload)
        events.append(event)
    return events


def validate_thesis_updates(rows, active_positions, allowed_horizons=None):
    """Validate agent-authored thesis revisions against engine-owned cycle ids.

    `allowed_horizons=None` preserves retries for plans prepared before stable
    locale-neutral IDs were published. New plans pass their explicit vocabulary.
    """
    active_positions = active_positions or {}
    valid_cycles = {p.get("cycle_id") for p in active_positions.values() if p.get("cycle_id")}
    horizon_ids = set(allowed_horizons) if allowed_horizons is not None else None
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
        if horizon_ids is not None and row.get("horizon") not in horizon_ids | {None}:
            allowed = ", ".join(sorted(horizon_ids))
            raise ThesisError(
                f"thesis_updates[{index}] has invalid horizon: {row.get('horizon')!r} "
                f"(allowed: {allowed}, or null)")
        for key, allowed in INFERENCE_ENUMS.items():
            value = row.get(key)
            if value is not None and value not in allowed:
                raise ThesisError(
                    f"thesis_updates[{index}] has invalid {key}: {value!r} (allowed: "
                    + ", ".join(sorted(allowed)) + ", or null)")
    return rows or []
