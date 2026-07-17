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
import horizon
import ledger
import problems
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
HORIZON_MARKER_LIMIT = 2
RULE_BREACH_LIMIT = 2
EXIT_DECISIONS = {"price_target", "thesis_broken", "swap", "anxiety", "other", "skip"}
RULE_BREACH_CHOICES = {"keep_tracking", "revise_rule", "exception"}


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

    Only future-dated rows reject the import (#169: the one zero-false-positive
    corruption signal).  Non-trade rows — deposits, dividends, interest, fees,
    reinvest notices — legitimately coexist in the same normalized CSV because
    the engine's cash pipeline consumes them; they are counted and reported,
    never fatal (#50: visible, not silent).
    """
    ledger_path = os.path.join(root, "ledger.jsonl")
    existing, skipped_lines = ledger.load_ledger(ledger_path)
    batches = []
    skipped_non_trade = skipped_future = 0
    for path in paths or []:
        trades, non_trade, future = ledger.trades_from_csv(path)
        batches.append(trades)
        skipped_non_trade += non_trade
        skipped_future += future
    if skipped_future:
        raise ReviewError(
            "ledger ingestion rejected normalized input before writing: "
            f"{skipped_future} future-dated row(s)"
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
        "skipped_non_trade": skipped_non_trade,
        "skipped_future_dated": skipped_future,
        "skipped_ledger_lines": skipped_lines,
    }


def _exit_narrative_index(root):
    """Map revisit_id -> latest captured exit narrative (canonical sessions win).

    Legacy `theses.jsonl` rows load first, then canonical bundles override them in
    session order, so capture identity and the recorded reason stay consistent
    with `_thesis_event_history` precedence.
    """
    index = {}
    for row in _jsonl(os.path.join(root, "theses.jsonl")):
        if row.get("event") == "exit_narrative" and row.get("revisit_id"):
            index[row["revisit_id"]] = row
    sessions = os.path.join(root, "sessions")
    if not os.path.isdir(sessions):
        return index
    bundles = []
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
        date = str((bundle.get("engine_state") or {}).get("date_end") or "")
        bundles.append(((date, session_id), bundle))
    # Same override order as _thesis_event_history — (date_end, session_id), not
    # directory-name order — so the replayed reason is the one continuity treats
    # as latest even when an undated bundle is present.
    for _key, bundle in sorted(bundles, key=lambda item: item[0]):
        for row in bundle.get("exit_narratives") or []:
            if row.get("revisit_id"):
                index[row["revisit_id"]] = row
    return index


def _thesis_event_history(root):
    """Load canonical thesis events first and retain pre-v2 legacy-only rows.

    Projection files remain supported, but deleting one cannot erase continuity
    while its canonical session bundle still exists.
    """
    legacy_theses = _jsonl(os.path.join(root, "theses.jsonl"))
    legacy_decisions = _jsonl(os.path.join(root, "thesis_decisions.jsonl"))
    canonical_sessions = set()
    bundles = []
    base = os.path.join(root, "sessions")
    if os.path.isdir(base):
        for session_id in sorted(os.listdir(base)):
            path = os.path.join(base, session_id, "bundle.json")
            if not os.path.isfile(path):
                continue
            try:
                bundle = session.read_json(path)
            except (OSError, ValueError):
                continue
            plan = bundle.get("review_plan") or {}
            if bundle.get("route") == "test_drive" or plan.get("persist") is False:
                continue
            canonical_sessions.add(session_id)
            date = str((bundle.get("engine_state") or {}).get("date_end") or "")
            bundles.append(((date, session_id), bundle))

    thesis_rows = [row for row in legacy_theses
                   if row.get("session_id") not in canonical_sessions]
    decision_rows = [row for row in legacy_decisions
                     if row.get("session_id") not in canonical_sessions]
    for _key, bundle in sorted(bundles, key=lambda item: item[0]):
        thesis_rows.extend(bundle.get("thesis_updates") or [])
        thesis_rows.extend(bundle.get("exit_narratives") or [])
        decision_rows.extend(bundle.get("thesis_decisions") or [])
    return thesis_rows, decision_rows


def _rule_breach_history(root):
    """Return the latest canonical breach decision per rule.

    The history stays in immutable bundles rather than a second mutable ledger.
    It is used only to enforce the first-breach-or-worsening question cadence.
    """
    rows = []
    base = os.path.join(root, "sessions")
    if not os.path.isdir(base):
        return {}
    for session_id in sorted(os.listdir(base)):
        path = os.path.join(base, session_id, "bundle.json")
        if not os.path.isfile(path):
            continue
        try:
            bundle = session.read_json(path)
        except (OSError, ValueError):
            continue
        plan = bundle.get("review_plan") or {}
        if bundle.get("route") == "test_drive" or plan.get("persist") is False:
            continue
        date = str((bundle.get("engine_state") or {}).get("date_end") or "")
        for row in bundle.get("rule_breach_decisions") or []:
            if row.get("rule_id"):
                rows.append(((date, session_id), row))
    latest = {}
    for _key, row in sorted(rows, key=lambda item: item[0]):
        latest[row["rule_id"]] = row
    return latest


def _prepare_exit_capture(root, state, persist):
    """Enqueue ledger exits and return capture, due-checkpoint, and backlog signals.

    Returns (recent, due, backlog, ingest_meta):
      recent  - fresh exits still inside the capture window and not yet captured
      due     - 30/60/90 checkpoints that matured after tracking started (#170);
                each row carries the prior recorded exit reason and the frozen
                engine-price swap comparison (missing prices stay honest)
      backlog - pre-activation historical exits: top items + aggregate summary
    """
    if not persist:
        return [], [], None, {"enqueued": 0, "skipped_dup": 0, "skipped_queue_lines": 0}
    ledger_path = os.path.join(root, "ledger.jsonl")
    queue_path = os.path.join(root, "revisit.jsonl")
    as_of = _review_date(state)
    new, dup = revisit.enqueue_from_ledger(ledger_path, queue_path, today=as_of)
    revisits, resolutions, skipped = revisit.load_queue(queue_path)
    narratives = _exit_narrative_index(root)
    raw_prices = ((state.get("price_snapshot") or {}).get("prices") or {})
    prices = {}
    for ticker, value in raw_prices.items():
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if value > 0:
            prices[str(ticker)] = value
    recent = [row for row in revisit.scan_recent_exits(revisits, as_of)
              if row.get("revisit_id") not in narratives]
    recent_ids = {row.get("revisit_id") for row in recent}
    due = []
    for row in revisit.scan_due(revisits, resolutions, as_of):
        if row.get("revisit_id") in recent_ids:
            continue  # capture wins while the exit is still inside its reason window
        item = row.get("item") or {}
        prior = narratives.get(row.get("revisit_id")) or {}
        due.append({
            "revisit_id": row.get("revisit_id"), "checkpoint": row.get("checkpoint"),
            "due_date": row.get("due_date"), "item": item,
            "compare": revisit.compare(item, prices),
            "prior_exit_reason": prior.get("exit_reason"),
            "prior_note": prior.get("note"),
            "prior_capture": prior.get("capture"),
        })
    topn, summary, total = revisit.scan_backlog(revisits, resolutions, prices=prices)
    backlog = {"items": topn[:2], "summary": summary, "total": total} if total else None
    return recent, due, backlog, {"enqueued": len(new), "skipped_dup": dup,
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
        "skip": ("保存為已略過，之後不再追問這筆的賣出理由。",
                 "Save this as skipped so this exit's reason is not asked again."),
    }
    return [{"value": key, "label": labels[key],
             "description": descriptions[key][1 if en else 0]}
            for key in ("price_target", "thesis_broken", "swap", "anxiety", "other", "skip")]


def _due_options(language):
    copy = card_renderer.load_copy(language)
    labels = copy.get("due_choices") or {}
    en = copy["language"] == "en"
    descriptions = {
        "still_valid": ("理由仍成立，賣早也是紀律。",
                        "The reason holds; selling early can still be discipline."),
        "modified": ("理由部分成立，需要修正。",
                     "The reason was partly right and needs an adjustment."),
        "falsified": ("真的判斷錯誤，記進教訓。",
                      "The reason was wrong; record it as a lesson."),
        "skip": ("不算已回答，下次復盤同一關會再出現。",
                 "Not saved as answered; the same checkpoint returns next review."),
    }
    return [{"value": key, "label": labels[key],
             "description": descriptions[key][1 if en else 0]}
            for key in ("still_valid", "modified", "falsified", "skip")]


def _rule_breach_options(language, can_revise=True):
    copy = card_renderer.load_copy(language)
    labels = copy.get("rule_breach_choices") or {}
    en = copy["language"] == "en"
    descriptions = {
        "keep_tracking": ("規矩合理，但這次沒有守住；照實記錄並繼續追。",
                          "The rule still fits, but it was not kept; record it and keep tracking."),
        "revise_rule": ("規矩本身不合理；在 note 簡述為何要改，收尾時用唯一 commitment 寫替代規矩。",
                        "The rule itself does not fit; note why it needs revision, then use the one final commitment for the replacement."),
        "exception": ("這次有正當例外；在 note 留下理由，事件仍保留在帳上。",
                      "This was a justified exception; record why in the note while keeping the event in history."),
    }
    keys = ("keep_tracking", "revise_rule", "exception") if can_revise else ("keep_tracking", "exception")
    return [{"value": key, "label": labels[key],
             "description": descriptions[key][1 if en else 0]} for key in keys]


def _breach_evidence_text(last_breach, language):
    events = (last_breach or {}).get("events") or []
    en = str(language).lower().startswith("en")
    parts = []
    for event in events:
        ticker = event.get("ticker")
        note = event.get("note")
        if ticker and note:
            parts.append(f"{ticker}: {note}")
        elif ticker or note:
            parts.append(str(ticker or note))
    if not parts:
        return "a recorded event" if en else "帳上有一筆事件"
    shown = "; ".join(parts)
    extra = int((last_breach or {}).get("event_count") or 0) - len(events)
    if extra > 0:
        shown += (f"; and {extra} more" if en else f"；另有 {extra} 筆")
    return shown


def _rule_breach_questions(problem_stats, history, language):
    if not problem_stats:
        return []
    top_rank = {key: index for index, key in enumerate(problem_stats.get("top") or [])}
    candidates = []
    for rule in problem_stats.get("rules_check") or []:
        breach = rule.get("last_breach") or {}
        rule_id = rule.get("rule_id")
        problem_key = rule.get("problem_key")
        if not rule_id or not breach.get("week"):
            continue
        stats = (problem_stats.get("per_key") or {}).get(problem_key) or {}
        prior = (history or {}).get(rule_id)
        if prior:
            if prior.get("breach_week") == breach.get("week"):
                continue
            worsened = stats.get("trend") == "worse" and (
                prior.get("trend") != "worse"
                or int(stats.get("recent_count") or 0) > int(prior.get("recent_count") or 0)
                or float(stats.get("recent_amount") or 0) > float(prior.get("recent_amount") or 0)
            )
            if not worsened:
                continue
        evidence_text = _breach_evidence_text(breach, language)
        if str(language).lower().startswith("en"):
            question = (f'The ledger recorded an event against rule "{rule.get("text") or rule_id}" '
                        f'in the review period ending {breach.get("week")} ({evidence_text}). '
                        'Which reading is accurate?')
        else:
            question = (f'問題帳在 {breach.get("week")} 這期記到一筆和規矩'
                        f'「{rule.get("text") or rule_id}」相衝的事件（{evidence_text}）。這次該怎麼定性？')
        digest = hashlib.sha256(f"{rule_id}|{breach.get('week')}".encode("utf-8")).hexdigest()[:12]
        rank = top_rank.get(problem_key, len(top_rank) + 1)
        can_revise = problem_key in set(session.PKEY.values())
        candidates.append({
            "id": f"rule_breach_{digest}", "kind": "rule_breach", "required": True,
            "question": question, "options": _rule_breach_options(language, can_revise=can_revise),
            "rule_id": rule_id, "rule_text": rule.get("text"), "problem_key": problem_key,
            "breach_week": breach.get("week"), "evidence": list(breach.get("events") or []),
            "recent_count": int(stats.get("recent_count") or 0),
            "recent_amount": float(stats.get("recent_amount") or 0), "trend": stats.get("trend"),
            "_priority": 1, "_importance": float(max(0, len(top_rank) - rank)), "_tie": 3,
        })
    candidates.sort(key=lambda row: (-float(row.get("_importance") or 0), str(row.get("id"))))
    return candidates[:RULE_BREACH_LIMIT]


def _due_question(row, language, card=None):
    """One 30/60/90 checkpoint question that replays the user's own recorded reason.

    The recalled label comes from the same kind-aware copy table the capture
    question showed and the card rendered — quoting anything else would put
    words in the user's mouth (a reduce answered price_target said 到了減碼點,
    not 到價了). The voice is interpolated, never patched afterwards, so an
    inferred capture can never read as user-confirmed.
    """
    item = row.get("item") or {}
    ticker = item.get("ticker") or "position"
    copy = card_renderer.load_copy(language)
    en = copy["language"] == "en"
    reason = row.get("prior_exit_reason")
    kind = item.get("kind") or "full"
    label = ((copy.get("exit_choices") or {}).get(kind) or {}).get(reason) if reason else None
    voice_guessed = row.get("prior_capture") == "inferred"
    base = (f"{ticker} was sold on {item.get('exit_date')} at {item.get('exit_price')}."
            if en else f"{ticker} 你在 {item.get('exit_date')} 以 {item.get('exit_price')} 賣出。")
    recall = ""
    if label:
        if en:
            lead = "At the time I guessed the reason was" if voice_guessed else "At the time you said"
            recall = f'{lead} "{label}".'
        else:
            lead = "我當時猜你是" if voice_guessed else "你當時說是"
            recall = f"{lead}「{label}」。"
    ask = (f"Looking back after {row.get('checkpoint')} days, does that reason still hold?" if en
           else f"{row.get('checkpoint')} 天後回頭看，當時的理由現在還成立嗎？")
    question = " ".join(part for part in (base, recall, ask) if part)
    digest = hashlib.sha256(f"{row.get('revisit_id')}|{row.get('checkpoint')}".encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"due_{digest}", "kind": "due_revisit", "ticker": ticker,
        "cycle_id": item.get("cycle_id"), "required": True, "question": question,
        "options": _due_options(language), "revisit_id": row.get("revisit_id"),
        "checkpoint": row.get("checkpoint"), "due_date": row.get("due_date"),
        "exit_date": item.get("exit_date"), "exit_price": item.get("exit_price"),
        "exit_kind": item.get("kind"), "currency": item.get("currency") or "USD",
        "swaps": item.get("swaps") or [], "compare": row.get("compare"),
        "prior_exit_reason": reason, "prior_note": row.get("prior_note"),
        "_importance": _exit_importance(item, card), "_tie": 2,
    }


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


CAPTURE_LIMIT = 2  # at most two exit-reason captures per session (c6850f0 contract)


def _question_queue(card, state, active, previous_state, language, recent_exits=None, thesis_states=None,
                    due_revisits=None, problem_stats=None, rule_history=None, horizon_markers=None):
    positions = _active_positions(state)
    by_ticker = {ticker: row for ticker, row in positions.items()}
    del previous_state  # retained in the call contract for older adapters
    thesis_states = thesis_states or active
    horizon_by_cycle = {row.get("cycle_id"): row for row in (horizon_markers or [])
                        if row.get("cycle_id")}
    candidates = []
    # Exit-reason capture is the only perishable question: its 14-day window
    # cannot be backfilled, while a skipped due checkpoint or an unanswered add
    # legitimately returns next review. Perishable questions therefore outrank
    # everything regardless of notional — but take at most CAPTURE_LIMIT slots
    # so one busy week cannot turn the review into an exit interrogation.
    for item in (recent_exits or [])[:CAPTURE_LIMIT]:
        question = _exit_question(item, language, card)
        prior = thesis_states.get(item.get("cycle_id")) or {}
        question["prior_thesis_id"] = prior.get("thesis_id")
        question["prior_event_id"] = prior.get("last_event_id") or prior.get("event_id")
        if item.get("cycle_id") in horizon_by_cycle:
            question["horizon_marker"] = horizon_by_cycle[item.get("cycle_id")]
        question["_priority"] = 0
        candidates.append(question)
    for row in due_revisits or []:
        candidates.append(_due_question(row, language, card))
    for index, item in enumerate(card.get("thesis_questions") or []):
        ticker = item.get("ticker")
        pos = by_ticker.get(ticker) or {}
        cycle_id = pos.get("cycle_id")
        old = active.get(cycle_id)
        decision_cursor = pos.get("decision_cursor")
        if old and decision_cursor and old.get("decision_cursor") == decision_cursor:
            continue
        if old and not decision_cursor and old.get("maturity") == "testable":
            continue
        if str(language).lower().startswith("en"):
            question = (f"For {ticker}, was the add based on new evidence, a pre-planned tranche, "
                        "a valuation change, or only the lower price?")
        else:
            question = (item.get("question") or
                        f"{ticker} 這次加碼，是新證據、事先分批、估值改變，還是只有價格下跌？")
        importance, basis = _ticker_importance(card, state, ticker)
        cursor_key = decision_cursor or f"{cycle_id}|legacy|{index}"
        question_digest = hashlib.sha256(cursor_key.encode("utf-8")).hexdigest()[:12]
        candidates.append({
            "id": f"add_{question_digest}", "kind": "add_thesis", "ticker": ticker,
            "cycle_id": cycle_id, "required": True, "question": question,
            "options": _add_options(language),
            "prior_thesis_id": (old or {}).get("thesis_id"),
            "prior_event_id": (old or {}).get("last_event_id") or (old or {}).get("event_id"),
            "decision_cursor": decision_cursor,
            "_importance": importance, "_importance_basis": basis, "_tie": 1,
        })
    candidates.extend(_rule_breach_questions(problem_stats, rule_history, language))
    if not candidates:
        top = ((card.get("top_holes") or [{}])[0]).get("dim") or state.get("headline_dim")
        top_label = card_renderer.localized_dimension(top, language)
        question = (f"What mainly drove the behavior behind {top_label}?" if str(language).lower().startswith("en")
                    else f"這次「{top}」背後，主要是事先規劃、情緒反應，還是外部限制？")
        candidates.append({"id": "headline_motive", "kind": "headline_motive", "required": True,
                           "question": question, "options": _generic_options(language),
                           "_importance": 0.0, "_tie": 2})
    # Priority tiers are semantic, then amount/rank resolves within a tier:
    # perishable exit capture -> unqualified chosen-rule breach -> due/add motive.
    candidates.sort(key=lambda row: (int(row.get("_priority", 2)),
                                     -float(row.get("_importance") or 0),
                                     int(row.get("_tie") or 0), str(row.get("id"))))
    queue = candidates[:QUESTION_LIMIT]
    for row in queue:
        row.pop("_importance", None)
        row.pop("_importance_basis", None)
        row.pop("_tie", None)
        row.pop("_priority", None)
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


def _problem_snapshot(root, state):
    """Fold the problem book and rules into review-ready stats.

    Offline and read-only: prepare must be able to show trends and rule verdicts
    without mutating the book (appending happens at finalize via projections).
    Assembly lives in problems.snapshot so the CLI and this path cannot drift.
    """
    payload = problems.snapshot(os.path.join(root, "problems.jsonl"),
                                os.path.join(root, "rules.jsonl"),
                                today=_review_date(state).isoformat())
    if not payload["events_n"] and not payload["marks_n"]:
        return None
    return payload


def _horizon_markers(state, thesis_states, active_cycle_ids, recent_exits):
    """Join stored theses with engine-owned position/exit dates and rank mirrors.

    Reductions remain active positions. Only a recent full exit receives an
    `exit_date`; otherwise horizon.scan would silently turn a reduction into a
    closed thesis. Ranking uses position cost or exit notional and is fixed here,
    never invented by the renderer.
    """
    as_of = state.get("date_end")
    if not as_of:
        return []
    by_cycle = {row.get("cycle_id"): row for row in thesis_states if row.get("cycle_id")}
    positions = _active_positions(state)
    costs = {}
    for row in positions.values():
        cycle_id = row.get("cycle_id")
        if not cycle_id:
            continue
        try:
            costs[cycle_id] = abs(float(row.get("cost") or 0))
        except (TypeError, ValueError):
            costs[cycle_id] = 0.0
    scan_rows = []
    importance = {}
    source = {}
    for cycle_id in active_cycle_ids:
        prior = by_cycle.get(cycle_id)
        if not prior:
            continue
        scan_rows.append({"cycle_id": cycle_id, "ticker": prior.get("ticker"),
                          "horizon": prior.get("horizon"), "maturity": prior.get("maturity")})
        importance[cycle_id] = costs.get(cycle_id, 0.0)
        source[cycle_id] = "active_thesis"
    for item in recent_exits or []:
        if item.get("kind") != "full":
            continue
        cycle_id = item.get("cycle_id")
        prior = by_cycle.get(cycle_id)
        if not prior:
            continue
        scan_rows.append({"cycle_id": cycle_id, "ticker": item.get("ticker") or prior.get("ticker"),
                          "horizon": prior.get("horizon"), "maturity": prior.get("maturity"),
                          "exit_date": item.get("exit_date")})
        importance[cycle_id] = abs(revisit._notional(item))
        source[cycle_id] = "recent_exit"
    try:
        markers = horizon.scan(scan_rows, str(as_of))
    except (TypeError, ValueError):
        return []
    for marker in markers:
        marker["source"] = source.get(marker.get("cycle_id"))
        marker["_importance"] = importance.get(marker.get("cycle_id"), 0.0)
    markers.sort(key=lambda marker: (0 if marker.get("kind") == "exit_too_fast" else 1,
                                     -float(marker.get("_importance") or 0),
                                     str(marker.get("ticker") or "")))
    for marker in markers:
        marker.pop("_importance", None)
    return markers[:HORIZON_MARKER_LIMIT]


def _build_plan(card, state, engine_meta, root, paths, route, language, fingerprint, nonce, persist,
                recent_exits=None, ledger_ingest=None, revisit_ingest=None,
                due_revisits=None, exit_backlog=None, problem_stats=None):
    positions = _active_positions(state)
    cycle_ids = [row.get("cycle_id") for row in positions.values() if row.get("cycle_id")]
    thesis_rows, decision_rows = _thesis_event_history(root)
    thesis_states = thesis.reconstruct_states(thesis_rows, decision_rows, cycle_ids)
    active_rows = [row for row in thesis_states
                   if row.get("cycle_id") in set(cycle_ids) and row.get("position_status") != "closed"]
    closed_rows = [row for row in thesis_states if row.get("position_status") == "closed"]
    active = {row.get("cycle_id"): row for row in active_rows}
    by_cycle = {row.get("cycle_id"): row for row in thesis_states}
    horizon_markers = _horizon_markers(state, thesis_states, cycle_ids, recent_exits)
    rule_history = _rule_breach_history(root)
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
                           "active_theses": active_rows, "closed_theses": closed_rows,
                           "thesis_states": thesis_states,
                           # audit summary only — the question payload is the single
                           # complete source the flow reads, so the two can't diverge
                           "due_revisits": [{"revisit_id": row.get("revisit_id"),
                                             "checkpoint": row.get("checkpoint"),
                                             "due_date": row.get("due_date"),
                                             "ticker": (row.get("item") or {}).get("ticker")}
                                            for row in due_revisits or []],
                           "recent_exits": list(recent_exits or []),
                           "exit_backlog": exit_backlog,
                           "problem_stats": problem_stats,
                           "market_context": state.get("market_context"),
                           "horizon_markers": horizon_markers,
                           "revisit_ingest": revisit_ingest},
        "question_queue": _question_queue(card, state, active, previous, language,
                                          recent_exits, by_cycle, due_revisits,
                                          problem_stats, rule_history, horizon_markers),
        "missing_thesis_positions": missing,
        "card_plan": {"candidate_rules": _candidate_rules(card, state, language),
                      "question_limit": QUESTION_LIMIT,
                      "horizon_ids": ["weeks", "quarters", "years"],
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
    recent_exits, due_revisits, exit_backlog, revisit_ingest = _prepare_exit_capture(root, state, persist)
    problem_stats = _problem_snapshot(root, state) if persist else None
    plan = _build_plan(card, state, engine_meta, root, paths, route, language, fingerprint,
                       args.session_nonce or "", persist,
                       recent_exits=recent_exits, ledger_ingest=ledger_ingest,
                       revisit_ingest=revisit_ingest, due_revisits=due_revisits,
                       exit_backlog=exit_backlog, problem_stats=problem_stats)
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
    allowed_horizons = (plan.get("card_plan") or {}).get("horizon_ids")
    thesis.validate_thesis_updates(updates, positions, allowed_horizons=allowed_horizons)
    needed = {row.get("cycle_id") for row in plan.get("missing_thesis_positions") or []}
    supplied = {row.get("cycle_id") for row in updates}
    missing = sorted(x for x in needed - supplied if x)
    if missing:
        raise ReviewError("missing inferred thesis updates for cycles: " + ", ".join(missing))
    return updates


def _assign_thesis_ids(plan, updates):
    date = (plan.get("engine_state") or {}).get("date_end")
    prior_rows = ((plan.get("state_snapshot") or {}).get("thesis_states") or [])
    prior_by_cycle = {row.get("cycle_id"): row for row in prior_rows if row.get("cycle_id")}
    rows = []
    for update in updates:
        row = dict(update)
        prior = prior_by_cycle.get(row.get("cycle_id")) or {}
        thesis_id = prior.get("thesis_id") or thesis.stable_thesis_id(row.get("cycle_id"))
        if row.get("thesis_id") and row["thesis_id"] != thesis_id:
            raise ReviewError(f"thesis update changes stable identity for cycle: {row.get('cycle_id')}")
        row["schema_version"] = 2
        row["thesis_id"] = thesis_id
        row["status"] = "open" if not prior else row.get("status") or "modified"
        if row["status"] == "active":
            row["status"] = "open"
        if row["status"] not in thesis.THESIS_STATUSES:
            raise ReviewError(f"invalid thesis status for cycle: {row.get('cycle_id')}")
        row["position_status"] = "open"
        row["session_date"] = date
        row["session_id"] = plan["session_id"]
        revises = prior.get("last_event_id") or prior.get("event_id")
        if row.get("revises") and row["revises"] != revises:
            raise ReviewError(f"thesis update has stale revises link for cycle: {row.get('cycle_id')}")
        if revises:
            row["revises"] = revises
        identity_payload = dict(row)
        supplied_event_id = identity_payload.pop("event_id", None)
        event_id = thesis.stable_event_id("thesis-update", identity_payload)
        if supplied_event_id and supplied_event_id != event_id:
            raise ReviewError(f"thesis update has invalid event_id for cycle: {row.get('cycle_id')}")
        row["event_id"] = event_id
        rows.append(row)
    return rows


def _clean_note(question_id, answer, context):
    """Shared note contract for narrated answers: evidence_delta is never valid,
    whitespace collapses, and 500 characters is the cap for every question kind."""
    if answer.get("evidence_delta") is not None:
        raise ReviewError(f"{question_id}: evidence_delta is not valid for {context}")
    note = " ".join(str(answer.get("note") or "").split()) or None
    if note and len(note) > 500:
        raise ReviewError(f"{question_id}: note must be at most 500 characters")
    return note


def _build_exit_narratives(plan, answers, amap=None):
    if amap is None:
        amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    thesis_states = {row.get("cycle_id"): row for row in
                     ((plan.get("state_snapshot") or {}).get("thesis_states") or [])
                     if row.get("cycle_id")}
    for question in plan.get("question_queue") or []:
        if question.get("kind") != "revisit":
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        if choice not in EXIT_DECISIONS:
            raise ReviewError(f"unsupported exit decision: {choice}")
        note = _clean_note(question["id"], answer, "an exit reason")
        if choice == "other" and not note:
            raise ReviewError(f"{question['id']}: other requires a short note")
        if choice == "skip":
            note = None
        event = {
            "event": "exit_narrative", "schema_version": 2,
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
        prior = thesis_states.get(question.get("cycle_id")) or {}
        if prior.get("thesis_id"):
            event["thesis_id"] = prior["thesis_id"]
            event["revises"] = prior.get("last_event_id") or prior.get("event_id")
        raw_id = f"{plan.get('session_id')}|{question.get('revisit_id')}|{choice}|{note or ''}"
        event["event_id"] = "exit-" + hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
        events.append(event)
    return events


def _build_revisit_resolutions(plan, answers, amap=None):
    """Turn due-checkpoint answers into revisit resolution events.

    `skip` is deliberately NOT saved: the checkpoint stays open and returns at
    the next review (the capture contract's skip-dedup applies to exit reasons,
    not to 30/60/90 verdicts — an unanswered verdict is missing data, not a
    decision).
    """
    if amap is None:
        amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    date = (plan.get("engine_state") or {}).get("date_end")
    for question in plan.get("question_queue") or []:
        if question.get("kind") != "due_revisit":
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        if choice == "skip":
            continue
        if choice not in revisit.STATUSES:
            raise ReviewError(f"unsupported revisit resolution: {choice}")
        note = _clean_note(question["id"], answer, "a revisit verdict")
        event = {
            "type": "resolution", "revisit_id": question.get("revisit_id"),
            "checkpoint": str(question.get("checkpoint")), "status": choice,
            "date": date, "session_id": plan.get("session_id"),
        }
        if note:
            event["note"] = note
        events.append(event)
    return events


def _build_rule_breach_decisions(plan, answers, amap=None):
    """Persist the user's qualitative reading without rewriting problem history."""
    if amap is None:
        amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    for question in plan.get("question_queue") or []:
        if question.get("kind") != "rule_breach":
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        offered = {option.get("value") for option in question.get("options") or []}
        if choice not in RULE_BREACH_CHOICES or choice not in offered:
            raise ReviewError(f"unsupported rule breach decision: {choice}")
        note = _clean_note(question["id"], answer, "a rule breach decision")
        if choice in {"revise_rule", "exception"} and not note:
            raise ReviewError(f"{question['id']}: {choice} requires a short note")
        event = {
            "event": "rule_breach_decision", "schema_version": 1,
            "session_id": plan.get("session_id"), "rule_id": question.get("rule_id"),
            "rule_text": question.get("rule_text"), "problem_key": question.get("problem_key"),
            "breach_week": question.get("breach_week"), "evidence": list(question.get("evidence") or []),
            "decision": choice, "note": note,
            "review_date": (plan.get("engine_state") or {}).get("date_end"),
            "recent_count": question.get("recent_count"),
            "recent_amount": question.get("recent_amount"), "trend": question.get("trend"),
        }
        identity = session.canonical(event)
        event["event_id"] = "rule-breach-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        events.append(event)
    return events


def _resolve_commitment(plan, answers):
    choice = answers.get("commitment") or {}
    selected = choice.get("choice")
    answer_map = {row.get("question_id"): row for row in answers.get("answers") or []
                  if isinstance(row, dict)}
    revise_questions = [
        row for row in plan.get("question_queue") or []
        if row.get("kind") == "rule_breach"
        and (answer_map.get(row.get("id")) or {}).get("choice") == "revise_rule"
    ]
    if len(revise_questions) > 1:
        raise ReviewError("one card can revise at most one rule")
    expected_revision = revise_questions[0] if revise_questions else None
    revises_rule_id = choice.get("revises_rule_id")
    if expected_revision and revises_rule_id != expected_revision.get("rule_id"):
        raise ReviewError("a revise_rule answer requires the one final commitment to revise that rule")
    if not expected_revision and revises_rule_id:
        raise ReviewError("revises_rule_id requires a revise_rule answer for that rule")
    if selected == "skip":
        if expected_revision:
            raise ReviewError("a revise_rule answer requires a replacement commitment")
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
    if expected_revision:
        replacement_key = session.PKEY.get(chosen.get("metric_key"))
        if replacement_key != expected_revision.get("problem_key"):
            raise ReviewError("replacement commitment must track the same problem_key as the revised rule")
        chosen["revises_rule_id"] = revises_rule_id
    if (plan.get("engine_state") or {}).get("insufficient_data"):
        chosen["baseline_note"] = "short-sample baseline"
    return chosen


def _draft_bundle(plan, answers, narrative, require_commitment):
    if answers.get("session_id") != plan.get("session_id"):
        raise ReviewError("answers.session_id does not match Review Plan")
    amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=not require_commitment)
    updates = _assign_thesis_ids(plan, _validate_thesis_completeness(plan, answers))
    decisions = thesis.build_decision_events(plan, answers, updates)
    exit_narratives = _build_exit_narratives(plan, answers, amap)
    revisit_resolutions = _build_revisit_resolutions(plan, answers, amap)
    rule_breach_decisions = _build_rule_breach_decisions(plan, answers, amap)
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
    bundle = {
        "schema_version": 2,
        "session_id": plan["session_id"],
        "route": plan["route"],
        "language": plan["language"],
        "review_plan": plan,
        "engine_state": plan["engine_state"],
        "engine_card": plan["engine_card"],
        "answers": answers,
        "narrative": narrative,
        "thesis_updates": updates,
        "thesis_decisions": decisions,
        "exit_narratives": exit_narratives,
        "commitment": commitment,
        "observations": list(answers.get("observations") or []),
    }
    # Only present when a due checkpoint was actually answered: sessions committed
    # before this key existed must re-draft to the identical canonical bundle, or
    # the documented-safe finalize retry would fail closed on every old session.
    if revisit_resolutions:
        bundle["revisit_resolutions"] = revisit_resolutions
    if rule_breach_decisions:
        bundle["rule_breach_decisions"] = rule_breach_decisions
    return bundle


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
    with session.finalize_transaction(root, args.session_id) as transaction:
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
        private_html = card_renderer.render_html(
            private_md, card_renderer.load_copy(plan["language"])["title"])
        result, projection, projection_error = transaction.commit_bundle(
            bundle, private_md, public_md, private_html, persist=bool(plan.get("persist"))
        )
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
