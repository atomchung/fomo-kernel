#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical review-session storage and recoverable legacy projections.

The committed session directory is the source of truth.  It is assembled in a
staging directory and renamed into place in one filesystem operation.  Existing
JSONL files remain supported as projections so older tooling keeps working; a
projection failure never corrupts or invalidates the committed session.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile

import ledger


class SessionError(ValueError):
    pass


PKEY = {
    "max_pos_pct": "oversize",
    "avgdown_count": "avgdown_breach",
    "ai_pct": "concentration",
    "max_sector_pct": "concentration",
    "top3_pct": "concentration",
}


def default_root():
    return os.path.expanduser(os.environ.get("TRADE_COACH_HOME", "~/.trade-coach"))


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pretty(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _safe_id(session_id):
    if not session_id or session_id != os.path.basename(session_id) or session_id in {".", ".."}:
        raise SessionError("invalid session_id")
    return session_id


def pending_dir(root, session_id):
    return os.path.join(root, ".pending", _safe_id(session_id))


def session_dir(root, session_id):
    return os.path.join(root, "sessions", _safe_id(session_id))


def save_pending(root, session_id, **artifacts):
    """Atomically update named pending artifacts; returns their stable paths."""
    base = pending_dir(root, session_id)
    os.makedirs(base, exist_ok=True)
    paths = {}
    for name, value in artifacts.items():
        if value is None:
            continue
        ext = ".json" if isinstance(value, (dict, list)) else ".md"
        path = os.path.join(base, name + ext)
        text = pretty(value) if isinstance(value, (dict, list)) else str(value)
        if text and not text.endswith("\n"):
            text += "\n"
        ledger.atomic_write_text(path, text)
        paths[name] = path
    return paths


def load_pending(root, session_id):
    base = pending_dir(root, session_id)
    if not os.path.isdir(base):
        raise SessionError(f"pending session not found: {session_id}")
    out = {"session_id": session_id, "path": base}
    for name in ("plan", "answers", "narrative"):
        path = os.path.join(base, name + ".json")
        if os.path.exists(path):
            out[name] = read_json(path)
    for name in ("card-private-preview", "card-public-preview"):
        path = os.path.join(base, name + ".md")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                out[name] = f.read()
    return out


def _artifact_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def commit_bundle(root, bundle, private_md, public_md, private_html=None):
    """Commit an immutable canonical bundle via staging-directory rename."""
    session_id = _safe_id(bundle.get("session_id"))
    sessions = os.path.join(root, "sessions")
    os.makedirs(sessions, exist_ok=True)
    final = session_dir(root, session_id)
    if os.path.isdir(final):
        existing = read_json(os.path.join(final, "bundle.json"))
        if canonical(existing) != canonical(bundle):
            raise SessionError(f"session {session_id} already committed with different content")
        return {"status": "no-op", "path": final, "session_id": session_id}

    staging = tempfile.mkdtemp(prefix=f".{session_id}.staging-", dir=sessions)
    try:
        artifacts = {
            "bundle.json": pretty(bundle),
            "state.json": pretty(bundle.get("engine_state") or {}),
            "plan.json": pretty(bundle.get("review_plan") or {}),
            "answers.json": pretty(bundle.get("answers") or {}),
            "narrative.json": pretty(bundle.get("narrative") or {}),
            "card-private.md": private_md if private_md.endswith("\n") else private_md + "\n",
            "card-public.md": public_md if public_md.endswith("\n") else public_md + "\n",
        }
        if private_html is not None:
            artifacts["card-private.html"] = private_html if private_html.endswith("\n") else private_html + "\n"
        manifest = {name: _artifact_hash(text) for name, text in artifacts.items()}
        artifacts["manifest.json"] = pretty({"schema_version": 1, "sha256": manifest})
        for name, text in artifacts.items():
            ledger.atomic_write_text(os.path.join(staging, name), text)
        os.replace(staging, final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(pending_dir(root, session_id), ignore_errors=True)
    return {"status": "committed", "path": final, "session_id": session_id}


def _read_jsonl(path):
    rows = []
    if not os.path.exists(path):
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


def _append_session_rows(path, session_id, new_rows):
    """Atomic, idempotent append for one session; conflicting retries fail closed."""
    if not new_rows:
        return {"path": path, "appended": 0, "status": "empty"}
    existing = _read_jsonl(path)
    same = [row for row in existing if row.get("session_id") == session_id]
    old_set = {canonical(row) for row in same}
    new_set = {canonical(row) for row in new_rows}
    if same and old_set == new_set:
        return {"path": path, "appended": 0, "status": "no-op"}
    if same and not old_set.issubset(new_set):
        raise SessionError(f"legacy projection conflict: {path} / {session_id}")
    delta = [row for row in new_rows if canonical(row) not in old_set]
    merged = existing + delta
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in merged)
    ledger.atomic_write_text(path, text)
    return {"path": path, "appended": len(delta), "status": "projected"}


def _project_card(root, bundle, private_md):
    date = (bundle.get("engine_state") or {}).get("date_end") or "undated"
    suffix = bundle["session_id"].split("__")[-1]
    path = os.path.join(root, "cards", f"{date}--{suffix}.md")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if f.read() != private_md:
                raise SessionError(f"legacy card conflict: {path}")
        return {"path": path, "status": "no-op"}
    ledger.atomic_write_text(path, private_md)
    return {"path": path, "status": "projected"}


def project_legacy(root, bundle, private_md):
    """Project a committed bundle into v1 files. Safe to rerun after interruption."""
    session_id = bundle["session_id"]
    state = dict(bundle.get("engine_state") or {})
    commitment = bundle.get("commitment")
    state["commitment"] = commitment
    state["rule"] = (commitment or {}).get("rule")
    ledger.atomic_write_text(os.path.join(root, "last_state.json"), pretty(state))

    date_end = state.get("date_end")
    log_row = {
        "date_end": date_end,
        "headline_dim": state.get("headline_dim"),
        "commitment": commitment,
        "metrics_snapshot": dict(state.get("metrics") or {}),
        "session_id": session_id,
    }
    reports = [_append_session_rows(os.path.join(root, "log.jsonl"), session_id, [log_row])]

    thesis_updates = list(bundle.get("thesis_updates") or [])
    reports.append(_append_session_rows(os.path.join(root, "theses.jsonl"), session_id, thesis_updates))
    reports.append(_append_session_rows(os.path.join(root, "thesis_decisions.jsonl"), session_id,
                                        list(bundle.get("thesis_decisions") or [])))

    rule_rows = []
    if commitment and commitment.get("rule"):
        suffix = session_id.split("__")[-1]
        rule_rows.append({
            "rule_id": f"rule-{suffix}-0",
            "text": commitment["rule"],
            "metric_key": commitment.get("metric_key"),
            "problem_key": PKEY.get(commitment.get("metric_key")),
            "source": "user_chosen",
            "status": "tracking",
            "created": date_end,
            "session_id": session_id,
        })
    reports.append(_append_session_rows(os.path.join(root, "rules.jsonl"), session_id, rule_rows))

    problems = []
    for event in state.get("problem_events") or []:
        row = dict(event)
        row["session_id"] = session_id
        problems.append(row)
    reports.append(_append_session_rows(os.path.join(root, "problems.jsonl"), session_id, problems))
    card_report = _project_card(root, bundle, private_md)
    report = {"schema_version": 1, "session_id": session_id, "rows": reports, "card": card_report}
    ledger.atomic_write_text(os.path.join(root, "projections", session_id + ".json"), pretty(report))
    return report


def load_committed(root, session_id):
    path = session_dir(root, session_id)
    if not os.path.isdir(path):
        raise SessionError(f"committed session not found: {session_id}")
    return read_json(os.path.join(path, "bundle.json"))


def repair_projections(root):
    reports = []
    base = os.path.join(root, "sessions")
    if not os.path.isdir(base):
        return reports
    for session_id in sorted(os.listdir(base)):
        path = os.path.join(base, session_id)
        if not os.path.isdir(path) or session_id.startswith("."):
            continue
        bundle = read_json(os.path.join(path, "bundle.json"))
        with open(os.path.join(path, "card-private.md"), encoding="utf-8") as f:
            reports.append(project_legacy(root, bundle, f.read()))
    return reports
