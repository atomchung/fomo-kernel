#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tool-neutral orchestration CLI for one-card trade reviews.

Lifecycle:

    prepare  -> agent asks the returned question_queue
    preview  -> validates answers/theses/narrative and renders a pending card
    finalize -> user chooses one commitment; commits an atomic session bundle
    resume   -> returns pending state after interruption

All commands emit JSON on stdout.  Human-readable diagnostics go to stderr.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import card_renderer
import ledger
import revisit
import session
import thesis


HERE = pathlib.Path(__file__).resolve().parent
TRADE_RECAP = HERE / "trade_recap.py"
MOCK_CSV = HERE.parent / "mock" / "mock_trades.csv"
DIM_METRIC = {
    "exit_discipline": "exit_severity",
    "position_sizing": "max_pos_pct",
    "diversification": "top3_pct",
    "holding_period": "hold_severity",
    "averaging_down": "avgdown_count",
}
QUESTION_LIMIT = 3
EXIT_DECISIONS = {"price_target", "thesis_broken", "swap", "anxiety", "other", "skip"}


class ReviewError(ValueError):
    pass


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def _load_json(path, label):
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError) as exc:
        raise ReviewError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewError(f"{label} must be a JSON object")
    return value


def _jsonl(path):
    return thesis.read_jsonl(path)


def _fingerprint(paths, language, route, prepared=None, nonce=""):
    # nonce participates so an explicit --session-nonce starts a genuinely new
    # session instead of being swallowed by same-content pending resume.
    h = hashlib.sha256()
    h.update(f"{language}\0{route}\0{nonce}\0".encode())
    if prepared:
        h.update(session.canonical(prepared).encode())
    for path in paths or []:
        p = os.path.abspath(path)
        h.update(p.encode() + b"\0")
        with open(p, "rb") as f:
            while True:
                block = f.read(1024 * 1024)
                if not block:
                    break
                h.update(block)
    return h.hexdigest()


def _pending_by_fingerprint(root, fingerprint):
    base = os.path.join(root, ".pending")
    if not os.path.isdir(base):
        return None
    for sid in sorted(os.listdir(base)):
        plan_path = os.path.join(base, sid, "plan.json")
        if not os.path.exists(plan_path):
            continue
        try:
            plan = session.read_json(plan_path)
        except (OSError, ValueError):
            continue
        if (plan.get("input") or {}).get("fingerprint") == fingerprint:
            return plan
    return None


def _has_history(root):
    sessions = os.path.join(root, "sessions")
    if os.path.isdir(sessions) and any(not n.startswith(".") for n in os.listdir(sessions)):
        return True
    return bool(_jsonl(os.path.join(root, "log.jsonl")))


def _previous_state(root):
    path = os.path.join(root, "last_state.json")
    if not os.path.exists(path):
        return None
    try:
        return session.read_json(path)
    except (OSError, ValueError):
        return None


def _review_date(state):
    try:
        return dt.date.fromisoformat(str((state or {}).get("date_end")))
    except (TypeError, ValueError):
        return dt.date.today()


def _ingest_trades(root, paths):
    """Validate all normalized CSVs, then append their trade facts once.

    Validation completes before the first write so a bad file cannot leave a
    partially ingested multi-file review.  Overlapping weekly files remain safe:
    each later batch deduplicates against both the existing ledger and earlier
    batches from this prepare call.
    """
    ledger_path = os.path.join(root, "ledger.jsonl")
    existing, skipped_lines = ledger.load_ledger(ledger_path)
    batches = []
    skipped_bad = skipped_future = 0
    for path in paths or []:
        trades, bad, future = ledger.trades_from_csv(path)
        batches.append(trades)
        skipped_bad += bad
        skipped_future += future
    if skipped_bad or skipped_future:
        raise ReviewError(
            "ledger ingestion rejected normalized input before writing: "
            f"{skipped_bad} invalid/non-trade row(s), {skipped_future} future-dated row(s)"
        )

    virtual = list(existing)
    fresh_all = []
    skipped_dup = 0
    for batch in batches:
        fresh, dup = ledger.dedupe_against(virtual, batch)
        fresh_all.extend(fresh)
        virtual.extend(fresh)
        skipped_dup += dup
    if fresh_all:
        ledger.append_events(ledger_path, fresh_all)
    return {
        "path": ledger_path,
        "appended": len(fresh_all),
        "skipped_dup": skipped_dup,
        "skipped_bad": skipped_bad,
        "skipped_future_dated": skipped_future,
        "skipped_ledger_lines": skipped_lines,
    }


def _captured_revisit_ids(root):
    """Read capture identity from canonical sessions plus legacy compatibility rows."""
    captured = {
        row.get("revisit_id") for row in _jsonl(os.path.join(root, "theses.jsonl"))
        if row.get("event") == "exit_narrative" and row.get("revisit_id")
    }
    sessions = os.path.join(root, "sessions")
    if not os.path.isdir(sessions):
        return captured
    for session_id in sorted(os.listdir(sessions)):
        bundle_path = os.path.join(sessions, session_id, "bundle.json")
        if not os.path.isfile(bundle_path):
            continue
        try:
            bundle = session.read_json(bundle_path)
        except (OSError, ValueError):
            continue
        plan = bundle.get("review_plan") or {}
        if bundle.get("route") == "test_drive" or plan.get("persist") is False:
            continue
        captured.update(
            row.get("revisit_id") for row in bundle.get("exit_narratives") or []
            if row.get("revisit_id")
        )
    return captured


def _prepare_exit_capture(root, state, persist):
    """Enqueue ledger exits and return fresh, not-yet-captured candidates."""
    if not persist:
        return [], {"enqueued": 0, "skipped_dup": 0, "skipped_queue_lines": 0}
    ledger_path = os.path.join(root, "ledger.jsonl")
    queue_path = os.path.join(root, "revisit.jsonl")
    as_of = _review_date(state)
    new, dup = revisit.enqueue_from_ledger(ledger_path, queue_path, today=as_of)
    revisits, _resolutions, skipped = revisit.load_queue(queue_path)
    captured = _captured_revisit_ids(root)
    recent = [row for row in revisit.scan_recent_exits(revisits, as_of)
              if row.get("revisit_id") not in captured]
    return recent, {"enqueued": len(new), "skipped_dup": dup,
                    "skipped_queue_lines": skipped, "path": queue_path}


def _run_engine(paths, root, args):
    os.makedirs(root, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fomo-review-") as tmp:
        state_path = os.path.join(tmp, "state.json")
        env = dict(os.environ, TR_JSON="1", TR_STATE_OUT=state_path,
                   TR_LEDGER=os.path.join(root, "ledger.jsonl"))
        previous = _previous_state(root)
        if previous and previous.get("date_end"):
            env["TR_PREV_END"] = str(previous["date_end"])
        for arg_name, env_name in (("driver_map", "TR_DRIVER_MAP"),
                                   ("instrument_map", "TR_INSTRUMENT_MAP"),
                                   ("cash", "TR_CASH")):
            value = getattr(args, arg_name, None)
            if value:
                env[env_name] = value
        run = subprocess.run([sys.executable, str(TRADE_RECAP)] + list(paths), cwd=str(HERE.parent),
                             env=env, capture_output=True, text=True, timeout=args.timeout)
        if run.returncode:
            raise ReviewError(f"engine failed ({run.returncode}): {run.stderr.strip()}")
        try:
            card = json.loads(run.stdout)
            state = session.read_json(state_path)
        except (ValueError, OSError) as exc:
            raise ReviewError(f"engine returned invalid artifacts: {exc}") from exc
        return card, state, run.stderr.strip()


def _active_positions(state):
    return ((state.get("holdings") or {}).get("positions") or {})


def _add_options(language):
    copy = card_renderer.load_copy(language)
    descriptions = {
        "planned_tranche": ("進場前已定好節奏，價格下跌不是新增理由。",
                            "The tranche schedule existed before the price move."),
        "new_evidence": ("必須補 claim 與 source，之後能回頭驗證。",
                         "Requires a claim and source that a later review can test."),
        "valuation_change": ("判斷沒變，但價格讓賠率或安全邊際改變。",
                             "The thesis is unchanged, but price changed the odds or margin of safety."),
        "price_only": ("沒有新事實，主要是想攤低成本或等回本。",
                       "No new fact; the main motive was lowering the cost basis or getting back to even."),
        "skip": ("先不定性，卡上只標未確認。", "Leave the motive unclassified for now."),
    }
    en = copy["language"] == "en"
    return [{"value": key, "label": copy["add_choices"][key],
             "description": descriptions[key][1 if en else 0]}
            for key in ("new_evidence", "planned_tranche", "valuation_change", "price_only", "skip")]


def _generic_options(language):
    if str(language).lower().startswith("en"):
        return [
            {"value": "deliberate_plan", "label": "Deliberate plan", "description": "The action followed a rule set before the trade."},
            {"value": "emotional_reaction", "label": "Emotional reaction", "description": "Fear, regret, or urgency drove the action."},
            {"value": "external_constraint", "label": "External constraint", "description": "Liquidity, tax, or another constraint drove it."},
            {"value": "skip", "label": "Skip", "description": "Leave the motive unresolved for now."},
        ]
    return [
        {"value": "deliberate_plan", "label": "事先規劃", "description": "行動遵循交易前就存在的規則。"},
        {"value": "emotional_reaction", "label": "情緒反應", "description": "恐懼、後悔或急迫感主導了行動。"},
        {"value": "external_constraint", "label": "外部限制", "description": "資金、稅務或其他限制主導了行動。"},
        {"value": "skip", "label": "先跳過", "description": "這次先不替動機下定論。"},
    ]


def _exit_options(language, exit_kind):
    copy = card_renderer.load_copy(language)
    labels = (copy.get("exit_choices") or {}).get(exit_kind) or {}
    en = copy["language"] == "en"
    descriptions = {
        "price_target": ("原先設定的目標或減碼條件已經完成。",
                         "A target or planned reduction condition was reached."),
        "thesis_broken": ("原本判斷失效，或信心因新事實而下降。",
                          "New facts broke or weakened the original thesis."),
        "swap": ("資金改放到另一個標的或用途。",
                 "Capital was reallocated to another position or use."),
        "anxiety": ("主要是怕回吐，所以先鎖住全部或部分成果。",
                    "The main motive was protecting gains from a possible reversal."),
        "other": ("以上都不符合，用自己的話留下一句。",
                  "None of these fit; save a short explanation in your own words."),
        "skip": ("保存為已略過，同一筆之後不再追問。",
                 "Save this as skipped so the same exit is not asked again."),
    }
    return [{"value": key, "label": labels[key],
             "description": descriptions[key][1 if en else 0]}
            for key in ("price_target", "thesis_broken", "swap", "anxiety", "other", "skip")]


def _format_notional(value, currency):
    value = float(value or 0)
    rendered = f"{value:,.0f}" if value.is_integer() else f"{value:,.2f}"
    return f"{currency or 'USD'} {rendered}"


def _exit_importance(item, card):
    """Compare exit amounts in the engine's aggregate currency when FX is available."""
    notional = revisit._notional(item)
    meta = (card or {}).get("currency_meta") or {}
    currency = str(item.get("currency") or "USD").upper()
    aggregate = str(meta.get("aggregate_currency") or currency).upper()
    if not meta.get("mixed") or currency == aggregate:
        return abs(notional)
    factor = (meta.get("fx") or {}).get(currency)
    try:
        return abs(notional * float(factor)) if factor is not None else abs(notional)
    except (TypeError, ValueError):
        return abs(notional)


def _exit_question(item, language, card=None):
    ticker = item.get("ticker") or "position"
    kind = item.get("kind") or "full"
    notional = revisit._notional(item)
    amount = _format_notional(notional, item.get("currency"))
    if str(language).lower().startswith("en"):
        action = "fully exited" if kind == "full" else "substantially reduced"
        question = (f"{ticker} was {action} on {item.get('exit_date')} for about {amount}. "
                    "What mainly drove that decision?")
    else:
        action = "全部出清" if kind == "full" else "大幅減倉"
        question = (f"{ticker} 在 {item.get('exit_date')} {action}，出場金額約 {amount}。"
                    "當時主要是什麼理由？")
    digest = hashlib.sha256(str(item.get("revisit_id")).encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"exit_{digest}", "kind": "revisit", "ticker": ticker,
        "cycle_id": item.get("cycle_id"), "required": True, "question": question,
        "options": _exit_options(language, kind), "revisit_id": item.get("revisit_id"),
        "exit_kind": kind, "exit_date": item.get("exit_date"),
        "exit_price": item.get("exit_price"), "shares_sold": item.get("shares_sold"),
        "shares_before": item.get("shares_before"), "currency": item.get("currency") or "USD",
        "exit_notional": notional, "_importance": _exit_importance(item, card), "_tie": 0,
    }


def _ticker_importance(card, state, ticker):
    for row in card.get("ticker_diagnosis") or []:
        if row.get("ticker") == ticker and row.get("impact") is not None:
            return abs(float(row["impact"])), "pnl_impact"
    pos = (_active_positions(state).get(ticker) or {})
    try:
        return abs(float(pos.get("cost") or 0)), "position_cost"
    except (TypeError, ValueError):
        return 0.0, "unknown"


def _question_queue(card, state, active, previous_state, language, recent_exits=None):
    positions = _active_positions(state)
    by_ticker = {ticker: row for ticker, row in positions.items()}
    current_adds = (state.get("metrics") or {}).get("avgdown_count") or 0
    previous_adds = ((previous_state or {}).get("metrics") or {}).get("avgdown_count") or 0
    add_behavior_changed = current_adds > previous_adds
    candidates = [_exit_question(item, language, card) for item in (recent_exits or [])]
    for index, item in enumerate(card.get("thesis_questions") or []):
        ticker = item.get("ticker")
        pos = by_ticker.get(ticker) or {}
        cycle_id = pos.get("cycle_id")
        old = active.get(cycle_id)
        if old and old.get("maturity") == "testable" and not add_behavior_changed:
            continue
        if str(language).lower().startswith("en"):
            question = (f"For {ticker}, was the add based on new evidence, a pre-planned tranche, "
                        "a valuation change, or only the lower price?")
        else:
            question = (item.get("question") or
                        f"{ticker} 這次加碼，是新證據、事先分批、估值改變，還是只有價格下跌？")
        importance, basis = _ticker_importance(card, state, ticker)
        candidates.append({
            "id": f"add_{index}_{ticker}", "kind": "add_thesis", "ticker": ticker,
            "cycle_id": cycle_id, "required": True, "question": question,
            "options": _add_options(language),
            "prior_thesis_id": (old or {}).get("thesis_id"),
            "_importance": importance, "_importance_basis": basis, "_tie": 1,
        })
    if not candidates:
        top = ((card.get("top_holes") or [{}])[0]).get("dim") or state.get("headline_dim")
        top_label = card_renderer.localized_dimension(top, language)
        question = (f"What mainly drove the behavior behind {top_label}?" if str(language).lower().startswith("en")
                    else f"這次「{top}」背後，主要是事先規劃、情緒反應，還是外部限制？")
        candidates.append({"id": "headline_motive", "kind": "headline_motive", "required": True,
                           "question": question, "options": _generic_options(language),
                           "_importance": 0.0, "_tie": 2})
    candidates.sort(key=lambda row: (-float(row.get("_importance") or 0),
                                     int(row.get("_tie") or 0), str(row.get("id"))))
    queue = candidates[:QUESTION_LIMIT]
    for row in queue:
        row.pop("_importance", None)
        row.pop("_importance_basis", None)
        row.pop("_tie", None)
    return queue


def _candidate_rules(card, state, language):
    candidates = []
    seen = set()
    source = list(card.get("candidate_rules") or [])
    for hole in card.get("top_holes") or []:
        source.append({"dim": hole.get("dim"), "rule": hole.get("lens_rule")})
    metrics = state.get("metrics") or {}
    for row in source:
        dim = row.get("dim") or row.get("kind")
        dim_id = card_renderer.dimension_id(dim)
        metric = DIM_METRIC.get(dim_id)
        if not dim or dim in seen or metric not in metrics:
            continue
        rule = card_renderer.localized_rule(dim, language) or row.get("rule")
        if not rule:
            continue
        seen.add(dim)
        candidates.append({"id": f"candidate_{len(candidates)}", "dim": dim_id, "rule": rule,
                           "metric_key": metric, "goal": "down"})
        if len(candidates) == 3:
            break
    return candidates


def _build_plan(card, state, engine_meta, root, paths, route, language, fingerprint, nonce, persist,
                recent_exits=None, ledger_ingest=None, revisit_ingest=None):
    positions = _active_positions(state)
    cycle_ids = [row.get("cycle_id") for row in positions.values() if row.get("cycle_id")]
    thesis_rows = _jsonl(os.path.join(root, "theses.jsonl"))
    active_rows = thesis.reconstruct_active(thesis_rows, cycle_ids)
    active = {row.get("cycle_id"): row for row in active_rows}
    missing = [{"ticker": ticker, "cycle_id": row.get("cycle_id")}
               for ticker, row in sorted(positions.items()) if row.get("cycle_id") not in active]
    previous = _previous_state(root)
    session_id = ledger.session_id_from_state(state, f"{nonce}|{route}|{language}")
    plan = {
        "schema_version": 2,
        "session_id": session_id,
        "status": "awaiting_answers",
        "route": route,
        "flow_path": f"flows/{route.replace('_', '-')}.md",
        "language": "en" if str(language).lower().startswith("en") else "zh-TW",
        "persist": bool(persist),
        "state_root": root,
        "input": {"paths": [os.path.abspath(p) for p in paths],
                  "kind": "positions_snapshot" if route == "snapshot_review" else "trades_csv",
                  "fingerprint": fingerprint, "engine_meta": engine_meta,
                  "ledger_ingest": ledger_ingest},
        "state_snapshot": {"prior_commitment": (previous or {}).get("commitment"),
                           "active_theses": active_rows, "due_revisits": [],
                           "recent_exits": list(recent_exits or []),
                           "revisit_ingest": revisit_ingest},
        "question_queue": _question_queue(card, state, active, previous, language, recent_exits),
        "missing_thesis_positions": missing,
        "card_plan": {"candidate_rules": _candidate_rules(card, state, language),
                      "question_limit": QUESTION_LIMIT,
                      "required_honesty_keys": [x.get("key") for x in card.get("honesty_ledger") or []]},
        "engine_card": card,
        "engine_state": state,
    }
    return plan


def cmd_prepare(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    language = args.language
    route = args.route
    persist = not args.test_drive
    if args.test_drive:
        route = "test_drive"
        if not args.root:
            root = tempfile.mkdtemp(prefix="fomo-kernel-test-drive-")
    elif route == "auto":
        route = "weekly_review" if _has_history(root) else "first_review"
    if route == "snapshot_review" and not (args.card_json and args.state_json):
        raise ReviewError("snapshot_review currently requires --card-json and --state-json from the snapshot adapter")
    paths = list(args.paths or ([] if args.card_json else [str(MOCK_CSV) if args.test_drive else None]))
    if any(p is None for p in paths) or (not paths and not args.card_json):
        raise ReviewError("provide at least one CSV path, or use --test-drive")
    # Resolve to absolute paths once: the engine subprocess runs with cwd at the
    # skill directory, so a caller-relative path would otherwise be fingerprinted
    # from one file and processed from another (or crash mid-run).
    paths = [os.path.abspath(os.path.expanduser(p)) for p in paths]
    prepared = None
    if args.card_json or args.state_json:
        if not (args.card_json and args.state_json):
            raise ReviewError("--card-json and --state-json must be provided together")
        card = _load_json(args.card_json, "engine card")
        state = _load_json(args.state_json, "engine state")
        prepared = {"card": card, "state": state}
        engine_meta = "prepared artifacts"
    fingerprint = _fingerprint(paths, language, route, prepared=prepared, nonce=args.session_nonce or "")
    existing = _pending_by_fingerprint(root, fingerprint)
    if existing:
        _emit({"status": "resumed", "session_id": existing["session_id"], "review_plan": existing,
               "next_action": "ask question_queue, then run preview"})
        return
    if prepared is None:
        card, state, engine_meta = _run_engine(paths, root, args)
    ledger_ingest = None
    if persist and route != "snapshot_review" and paths:
        ledger_ingest = _ingest_trades(root, paths)
    recent_exits, revisit_ingest = _prepare_exit_capture(root, state, persist)
    plan = _build_plan(card, state, engine_meta, root, paths, route, language, fingerprint,
                       args.session_nonce or "", persist, recent_exits, ledger_ingest, revisit_ingest)
    committed = session.session_dir(root, plan["session_id"])
    if os.path.isdir(committed):
        _emit({"status": "already_committed", "session_id": plan["session_id"], "path": committed})
        return
    session.save_pending(root, plan["session_id"], plan=plan)
    next_action = "ask every required question, author thesis_updates and prose-only narrative, then run preview"
    if not persist:
        # The test drive lives in an isolated root that preview/finalize cannot
        # discover on their own; without this handoff they report "pending session
        # not found" against the default root.
        next_action += f"; test drive is isolated — pass --root {root} to every later command"
    _emit({"status": "prepared", "session_id": plan["session_id"], "review_plan": plan,
           "next_action": next_action})


def _validate_thesis_completeness(plan, answers):
    updates = answers.get("thesis_updates") or []
    positions = _active_positions(plan.get("engine_state") or {})
    thesis.validate_thesis_updates(updates, positions)
    needed = {row.get("cycle_id") for row in plan.get("missing_thesis_positions") or []}
    supplied = {row.get("cycle_id") for row in updates}
    missing = sorted(x for x in needed - supplied if x)
    if missing:
        raise ReviewError("missing inferred thesis updates for cycles: " + ", ".join(missing))
    return updates


def _assign_thesis_ids(plan, updates):
    suffix = plan["session_id"].split("__")[-1]
    date = (plan.get("engine_state") or {}).get("date_end")
    rows = []
    for index, update in enumerate(updates):
        row = dict(update)
        row.setdefault("status", "active")
        row["session_date"] = date
        row["session_id"] = plan["session_id"]
        row.setdefault("thesis_id", f"{row['ticker']}-{date}-{suffix}-{index}")
        rows.append(row)
    return rows


def _build_exit_narratives(plan, answers):
    amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    for question in plan.get("question_queue") or []:
        if question.get("kind") != "revisit":
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        if choice not in EXIT_DECISIONS:
            raise ReviewError(f"unsupported exit decision: {choice}")
        if answer.get("evidence_delta") is not None:
            raise ReviewError(f"{question['id']}: evidence_delta is not valid for an exit reason")
        note = " ".join(str(answer.get("note") or "").split()) or None
        if choice == "other" and not note:
            raise ReviewError(f"{question['id']}: other requires a short note")
        if note and len(note) > 500:
            raise ReviewError(f"{question['id']}: note must be at most 500 characters")
        if choice == "skip":
            note = None
        event = {
            "event": "exit_narrative", "schema_version": 1,
            "session_id": plan.get("session_id"), "revisit_id": question.get("revisit_id"),
            "cycle_id": question.get("cycle_id"), "ticker": question.get("ticker"),
            "exit_date": question.get("exit_date"), "exit_kind": question.get("exit_kind"),
            "exit_price": question.get("exit_price"), "shares_sold": question.get("shares_sold"),
            "shares_before": question.get("shares_before"), "currency": question.get("currency"),
            "exit_notional": question.get("exit_notional"),
            "exit_reason": choice if choice not in {"other", "skip"} else None,
            "note": note, "capture": "skipped" if choice == "skip" else "confirmed",
            "recorded_at": (plan.get("engine_state") or {}).get("date_end"),
        }
        raw_id = f"{plan.get('session_id')}|{question.get('revisit_id')}|{choice}|{note or ''}"
        event["event_id"] = "exit-" + hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
        events.append(event)
    return events


def _resolve_commitment(plan, answers):
    choice = answers.get("commitment") or {}
    selected = choice.get("choice")
    if selected == "skip":
        return None
    candidates = {row["id"]: row for row in (plan.get("card_plan") or {}).get("candidate_rules") or []}
    if selected in candidates:
        chosen = dict(candidates[selected])
        chosen["origin"] = "candidate"
    elif selected == "custom":
        chosen = {"rule": (choice.get("rule") or "").strip(), "metric_key": choice.get("metric_key"),
                  "goal": choice.get("goal") or "down", "dim": choice.get("dim"), "origin": "custom"}
        if not chosen["rule"]:
            raise ReviewError("custom commitment requires rule")
    else:
        raise ReviewError("commitment.choice must be a candidate id, custom, or skip")
    metrics = (plan.get("engine_state") or {}).get("metrics") or {}
    if chosen.get("metric_key") not in metrics:
        raise ReviewError(f"commitment metric is not in engine state: {chosen.get('metric_key')}")
    chosen.pop("id", None)
    chosen["metric_value"] = metrics.get(chosen["metric_key"])
    chosen["source"] = "user_chosen"
    if (plan.get("engine_state") or {}).get("insufficient_data"):
        chosen["baseline_note"] = "short-sample baseline"
    return chosen


def _draft_bundle(plan, answers, narrative, require_commitment):
    if answers.get("session_id") != plan.get("session_id"):
        raise ReviewError("answers.session_id does not match Review Plan")
    thesis.validate_required_answers(plan, answers, allow_commitment_missing=not require_commitment)
    updates = _validate_thesis_completeness(plan, answers)
    decisions = thesis.build_decision_events(plan, answers)
    exit_narratives = _build_exit_narratives(plan, answers)
    card_renderer.validate_narrative(narrative)
    # #82 gate: every triggered honesty key must be covered by an agent-authored
    # sentence, and no sentence may claim a key the engine did not trigger.
    required = set((plan.get("card_plan") or {}).get("required_honesty_keys") or [])
    provided = set((narrative.get("honesty") or {}).keys())
    if required - provided:
        raise ReviewError("narrative.honesty is missing required keys: " + ", ".join(sorted(required - provided)))
    if provided - required:
        raise ReviewError("narrative.honesty has keys the ledger did not trigger: " + ", ".join(sorted(provided - required)))
    commitment = _resolve_commitment(plan, answers) if require_commitment else None
    return {
        "schema_version": 2,
        "session_id": plan["session_id"],
        "route": plan["route"],
        "language": plan["language"],
        "review_plan": plan,
        "engine_state": plan["engine_state"],
        "engine_card": plan["engine_card"],
        "answers": answers,
        "narrative": narrative,
        "thesis_updates": _assign_thesis_ids(plan, updates),
        "thesis_decisions": decisions,
        "exit_narratives": exit_narratives,
        "commitment": commitment,
        "observations": list(answers.get("observations") or []),
    }


def _load_interaction(args, pending):
    answers = _load_json(args.answers, "answers") if args.answers else pending.get("answers")
    narrative = _load_json(args.narrative, "narrative") if args.narrative else pending.get("narrative")
    if not answers or not narrative:
        raise ReviewError("answers and narrative are required (pass files or save them with preview)")
    return answers, narrative


def cmd_preview(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    pending = session.load_pending(root, args.session_id)
    plan = pending.get("plan")
    answers, narrative = _load_interaction(args, pending)
    bundle = _draft_bundle(plan, answers, narrative, require_commitment=False)
    private_md = card_renderer.render_private(bundle)
    public_md = card_renderer.render_public(bundle)
    paths = session.save_pending(root, args.session_id, answers=answers, narrative=narrative,
                                 **{"card-private-preview": private_md,
                                    "card-public-preview": public_md})
    _emit({"status": "previewed", "session_id": args.session_id,
           "private_card": private_md, "public_card": public_md,
           "candidate_rules": (plan.get("card_plan") or {}).get("candidate_rules") or [],
           "paths": paths, "next_action": "show the review-card preview; ask the user to choose one rule or skip; then finalize"})


def cmd_finalize(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    committed_path = session.session_dir(root, args.session_id)
    if os.path.isdir(committed_path):
        existing = session.load_committed(root, args.session_id)
        plan = existing.get("review_plan")
        pending = {"answers": existing.get("answers"), "narrative": existing.get("narrative")}
    else:
        pending = session.load_pending(root, args.session_id)
        plan = pending.get("plan")
    answers, narrative = _load_interaction(args, pending)
    bundle = _draft_bundle(plan, answers, narrative, require_commitment=True)
    private_md = card_renderer.render_private(bundle)
    public_md = card_renderer.render_public(bundle)
    private_html = card_renderer.render_html(private_md, card_renderer.load_copy(plan["language"])["title"])
    result = session.commit_bundle(root, bundle, private_md, public_md, private_html)
    projection = None
    projection_error = None
    if plan.get("persist"):
        try:
            projection = session.project_legacy(root, bundle, private_md)
        except Exception as exc:  # canonical bundle is already safe; repair-projections can retry
            projection_error = str(exc)
    _emit({"status": result["status"], "session_id": args.session_id, "path": result["path"],
           "private_card": os.path.join(result["path"], "card-private.md"),
           "public_card": os.path.join(result["path"], "card-public.md"),
           "projection": projection, "projection_error": projection_error,
           "recoverable": bool(projection_error)})


def cmd_resume(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    if args.session_id:
        _emit(session.load_pending(root, args.session_id))
        return
    base = os.path.join(root, ".pending")
    pending = [] if not os.path.isdir(base) else sorted(
        x for x in os.listdir(base) if os.path.isdir(os.path.join(base, x)))
    _emit({"status": "pending" if pending else "idle", "pending_sessions": pending,
           "next_action": "run resume with --session-id" if pending else "run prepare"})


def cmd_render(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    bundle = session.load_committed(root, args.session_id)
    private_md = card_renderer.render_private(bundle)
    public_md = card_renderer.render_public(bundle)
    _emit({"session_id": args.session_id, "private_card": private_md, "public_card": public_md})


def cmd_repair(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    outcome = session.repair_projections(root)
    _emit({"status": "repaired" if not outcome["errors"] else "partially_repaired", **outcome})


def build_parser():
    parser = argparse.ArgumentParser(description="fomo-kernel stable review orchestration")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="run engine and emit a resumable Review Plan")
    prepare.add_argument("paths", nargs="*", help="normalized trade CSV files")
    prepare.add_argument("--root")
    prepare.add_argument("--language", default="zh-TW", choices=("zh-TW", "en"))
    prepare.add_argument("--route", default="auto",
                         choices=("auto", "first_review", "weekly_review", "snapshot_review"))
    prepare.add_argument("--test-drive", action="store_true")
    prepare.add_argument("--session-nonce", default="")
    prepare.add_argument("--driver-map")
    prepare.add_argument("--instrument-map")
    prepare.add_argument("--cash", help="TR_CASH JSON string")
    prepare.add_argument("--card-json", help="precomputed engine card (adapter/testing)")
    prepare.add_argument("--state-json", help="precomputed engine state (adapter/testing)")
    prepare.add_argument("--timeout", type=int, default=180)
    prepare.set_defaults(func=cmd_prepare)

    for name, func in (("preview", cmd_preview), ("finalize", cmd_finalize)):
        p = sub.add_parser(name)
        p.add_argument("--session-id", required=True)
        p.add_argument("--root")
        p.add_argument("--answers")
        p.add_argument("--narrative")
        p.set_defaults(func=func)
    resume = sub.add_parser("resume")
    resume.add_argument("--session-id")
    resume.add_argument("--root")
    resume.set_defaults(func=cmd_resume)
    render = sub.add_parser("render")
    render.add_argument("--session-id", required=True)
    render.add_argument("--root")
    render.set_defaults(func=cmd_render)
    repair = sub.add_parser("repair-projections")
    repair.add_argument("--root")
    repair.set_defaults(func=cmd_repair)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (ReviewError, session.SessionError, thesis.ThesisError, card_renderer.RenderError) as exc:
        _emit({"status": "error", "error": str(exc)})
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
