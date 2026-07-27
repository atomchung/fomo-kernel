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
import math
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import card_renderer
import conditions
import consequence
import horizon
import instruments
import ledger
import price_feed
import problems
import question_surface
import revisit
import session
import snapshot_adapter
import thesis
import trade_recap
import verdicts


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
# Question density is a product contract per route (#291): a first review earns
# up to five questions when each extra answer creates durable decision-relevant
# information, weekly reviews stay one to three, a snapshot asks none. The min is
# a floor the min-backfill defends by information gain, never questionnaire
# volume. An unknown route falls back to the weekly band (defensive).
QUESTION_POLICY = {
    "first_review": {"min": 3, "max": 5},
    "weekly_review": {"min": 1, "max": 3},
    "snapshot_review": {"min": 0, "max": 0},
    "test_drive": {"min": 1, "max": 3},
}
HORIZON_MARKER_LIMIT = 2
RULE_BREACH_LIMIT = 2
INITIAL_THESIS_LIMIT = 2  # at most two first-review entry-thesis captures per review
INITIAL_THESIS_CHOICES = {"planned_entry", "momentum_follow", "external_call",
                          "no_clear_thesis", "skip"}
EXIT_DECISIONS = {"price_target", "thesis_broken", "swap", "anxiety", "other", "skip"}
RULE_BREACH_CHOICES = {"keep_tracking", "revise_rule", "exception"}
HEADLINE_MOTIVE_CHOICES = {"deliberate_plan", "emotional_reaction", "external_constraint"}
# #303: exit-consistency classifies the same motive axis as a headline motive
# (deliberate / emotional / external), so it reuses the choice contract and the
# `_generic_options` labels — only the durable event stream is kept separate.
EXIT_CONSISTENCY_CHOICES = HEADLINE_MOTIVE_CHOICES
# #434: how many condition slots one review asks the agent to look up. The plan
# used to send every stored slot raw, so a user with thirty standing conditions
# got a review whose per-turn context grew without bound and whose lookup work
# had no ceiling. The bound is on the *plan*, never on the record: the rest stay
# in conditions.jsonl, return next review (oldest-last-checked first), and the
# card says how many were left — a bounded surface must say what it dropped.
CONDITION_LOOKUP_CAP = 8
# At most one crossing question per review. A week that trips four conditions is
# a week with one conversation to have, not four; the rest state their facts and
# come back. (Owner ruling, #412.)
CONDITION_CROSSING_LIMIT = 1
CONDITION_CROSSING_NUMERIC_CHOICES = {"confirmed", "overridden", "skip"}
CONDITION_CROSSING_EVENT_CHOICES = {"yes", "no", "skip"}
CONDITION_BASIS_CHOICES = {"revise_threshold", "revise_metric", "keep", "skip"}
# `consider`'s own vocabulary (Layer 2, docs/decision-fomo-kernel-shape.md §3-4).
# CONSIDER_DECISIONS is what --resolve may record; "open" (schemas/pre-trade-
# consultation.schema.json's default) is a row's starting state, never something
# a caller resolves *to*. Kept as one tuple so the argparse choices and the
# schema enum cannot silently drift apart (tests/test_consider.py checks it).
CONSIDER_DECISIONS = ("acted", "declined", "modified")
# Layer 3 provenance vocabulary for an optional --agent-case claim
# (docs/decision-fomo-kernel-shape.md §3: "mark each claim as your record says,
# public fact, or my judgment"). engine_fact is that first category under a
# name that reads correctly next to consequence.py's own output; public_fact
# and agent_judgment are the doc's own words.
AGENT_CASE_PROVENANCE = ("engine_fact", "public_fact", "agent_judgment")
# #429's rule one layer up: a consultation nobody reconciles is the same dead-
# store shape that issue names for a question nobody reads. The bound is the
# same discipline CONDITION_LOOKUP_CAP states just above -- the Review Plan is
# re-sent as agent context on every later turn, so a user who never resolves
# old consultations must not grow it without limit. Oldest `created` first;
# _consultation_reconciliation's summary discloses whatever the cap holds
# back, the same "a bounded surface must say what it dropped" rule.
CONSULTATION_RECONCILE_CAP = 8


class ReviewError(ValueError):
    pass


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


_ENGINE_VERSION = None


def _engine_version():
    """Provenance stamp: which build produced this artifact.

    Pure metadata — it never enters narrative, numeric facts, or the public
    card. Resolution is fail-safe at every step so a missing git checkout or
    VERSION file can never break a review:

      1. a committed ``VERSION`` file (what a future release will ship);
      2. the git short SHA plus a dirty flag;
      3. ``unknown``.

    Cached per process so repeated prepare/preview/finalize calls agree.
    """
    global _ENGINE_VERSION
    if _ENGINE_VERSION is not None:
        return _ENGINE_VERSION
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(root, "VERSION"), encoding="utf-8") as handle:
            tag = handle.read().strip()
        if tag:
            _ENGINE_VERSION = {"id": tag, "source": "file"}
            return _ENGINE_VERSION
    except (OSError, UnicodeDecodeError):
        pass
    try:
        head = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if head.returncode == 0 and head.stdout.strip():
            status = subprocess.run(
                ["git", "-C", root, "status", "--porcelain"],
                capture_output=True, text=True, timeout=2,
            )
            _ENGINE_VERSION = {
                "id": head.stdout.strip()[:12],
                "source": "git",
                "dirty": bool(status.stdout.strip()),
            }
            return _ENGINE_VERSION
    except (OSError, subprocess.SubprocessError):
        pass
    _ENGINE_VERSION = {"id": "unknown", "source": "unknown"}
    return _ENGINE_VERSION


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


def _fingerprint(paths, language, route, prepared=None, nonce="", prices=None, cash=None,
                 condition_checks=None):
    # nonce participates so an explicit --session-nonce starts a genuinely new
    # session instead of being swallowed by same-content pending resume.
    h = hashlib.sha256()
    h.update(f"{language}\0{route}\0{nonce}\0".encode())
    if prepared:
        h.update(session.canonical(prepared).encode())
    if prices:
        # A supplied price envelope changes every valuation number, so it has to
        # change the fingerprint too. Without this, rerunning prepare after a
        # degraded run would resume the priceless pending session and silently
        # discard the prices the agent just retrieved (#289).
        h.update(b"prices\0" + session.canonical(prices).encode())
    if cash:
        # Same #289 class, for the cash anchor (#357/#369): the weekly flow may
        # legitimately learn the anchor only after a cash-less prepare resolved
        # the cadence tier, so `prepare --cash` must open a fresh session
        # instead of resuming the cash-less pending one and silently discarding
        # the balance the user just confirmed.
        try:
            canonical_cash = session.canonical(json.loads(cash))
        except (TypeError, ValueError):
            canonical_cash = str(cash)
        h.update(b"cash\0" + canonical_cash.encode())
    if condition_checks:
        # Same #289 class again (#412): the first prepare publishes what is due
        # and the second carries the results back. Without this the second pass
        # would resume the check-less pending session, and the crossing question
        # the lookups just earned would never be asked.
        h.update(b"condition_checks\0" + session.canonical(condition_checks).encode())
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


def _validate_initial_snapshot_root(root, anchor):
    """Resolve how a runtime snapshot declaration may enter this coach root.

    Empty history or an exact idempotent replay returns ``None`` (initial
    onboarding path, unchanged).  A different complete declaration against an
    anchored ledger returns the reconciliation the Review Plan freezes: the
    narrow fact diff plus the ``reconciled``/``adjusted`` verdict from
    ``ledger.snapshot_reconciliation``.  Everything else stays fail-closed —
    an incomplete second declaration, a declaration older than the current
    anchor, and history without a complete anchor (replay-only trades, unknown
    ledger event types, or an unrepaired ledger projection) are rejected.

    This is the prepare-time UX layer only; the authoritative check reruns
    under the root projection lock at finalize
    (``session._assert_initial_snapshot_boundary``) and fails closed when the
    frozen diff no longer matches the ledger.  Both layers share
    ``session.scan_initial_snapshot_conflicts`` and
    ``ledger.snapshot_reconciliation`` so their verdicts cannot drift.
    """
    if not isinstance(anchor, dict):
        return None
    if not session.scan_initial_snapshot_conflicts(root, anchor):
        return None
    if anchor.get("is_complete", True) is not True:
        raise ReviewError(session.INCOMPLETE_SNAPSHOT_RECONCILIATION)
    events, _skipped = ledger.load_ledger(os.path.join(root, "ledger.jsonl"))
    try:
        reconciliation = ledger.snapshot_reconciliation(events, anchor)
    except ValueError as exc:
        raise ReviewError(str(exc)) from exc
    if reconciliation is None:
        raise ReviewError(session.INITIAL_SNAPSHOT_CONFLICT)
    return reconciliation


def _apply_snapshot_reconciliation(card, state, reconciliation):
    """Freeze the reconciliation into both engine artifacts, honesty included.

    The full fact diff lives in ``state.snapshot_reconciliation`` (and thereby
    in the Review Plan the user confirms).  The card carries a summary through
    the existing honesty-ledger and data-integrity channels only — disclosure
    stays an engine decision (#82), wording stays with the renderer copy and
    the agent-authored narrative sentence.
    """
    card = dict(card)
    state = dict(state)
    state["snapshot_reconciliation"] = reconciliation
    diff = reconciliation.get("diff") or {}
    positions = diff.get("positions") or []
    summary = {
        "status": reconciliation.get("status"),
        "as_of": reconciliation.get("as_of"),
        "against_as_of": (reconciliation.get("against") or {}).get("as_of"),
        "positions_changed": sorted({row["ticker"] for row in positions
                                     if row.get("kind") not in ("only_declared", "only_derived")}),
        "only_declared": sorted({row["ticker"] for row in positions
                                 if row.get("kind") == "only_declared"}),
        "only_derived": sorted({row["ticker"] for row in positions
                                if row.get("kind") == "only_derived"}),
        "cash_currencies": sorted({row["currency"] for row in diff.get("cash") or []}),
    }
    integrity = dict(card.get("data_integrity") or {})
    integrity["snapshot_reconciliation"] = summary
    card["data_integrity"] = integrity
    honesty = [row for row in card.get("honesty_ledger") or []
               if row.get("key") != "snapshot_reconciliation"]
    honesty.append({"key": "snapshot_reconciliation",
                    "status": summary["status"], "data": summary})
    card["honesty_ledger"] = honesty
    return card, state


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
    # Canonical-bundle semantics, same as every other scanner (#215): a
    # finalized test drive in an explicit --root leaves a sessions/ directory,
    # and counting it flipped --route auto from first_review to weekly_review.
    if next(session.iter_canonical_bundles(root), None) is not None:
        return True
    return bool(_jsonl(os.path.join(root, "log.jsonl")))


def _completed_review_count(root, exclude_session_id=None):
    """Count completed local reviews without double-counting projections.

    Canonical persistent bundles are authoritative in v2, including when their
    legacy ``log.jsonl`` projection needs repair. Valid legacy log rows still
    count so pre-v2 history remains visible. Session ids deduplicate the same
    review across both stores; older rows without an id each represent one
    completed review. A matching current session id is excluded so an
    idempotent retry cannot present itself as a new return visit.
    """
    session_ids = set()
    for _name, bundle in session.iter_canonical_bundles(root):
        session_id = bundle.get("session_id")
        if session_id:
            session_ids.add(str(session_id))

    legacy_without_id = 0
    for row in _jsonl(os.path.join(root, "log.jsonl")):
        session_id = row.get("session_id")
        if session_id:
            session_ids.add(str(session_id))
        else:
            legacy_without_id += 1
    if exclude_session_id:
        session_ids.discard(str(exclude_session_id))
    return len(session_ids) + legacy_without_id


def _previous_state(root):
    path = os.path.join(root, "last_state.json")
    if not os.path.exists(path):
        return None
    try:
        return session.read_json(path)
    except (OSError, ValueError):
        return None


def _profile_path(root):
    return os.path.join(root, "profile.json")


def _position_cap_override(root):
    """The user's standing single-position cap from ``profile.json`` (#324).

    Validated to a (0,1) fraction or ``None`` (fail-closed): a missing,
    unreadable, or out-of-range profile silently falls back to the universal
    default rather than poisoning diagnosis. This is a standing preference, not
    per-session state, so it lives outside ``last_state.json`` (which the engine
    overwrites every run)."""
    path = _profile_path(root)
    if not os.path.exists(path):
        return None
    try:
        profile = session.read_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(profile, dict):
        return None
    return card_renderer.valid_position_cap(profile.get("max_position_pct"))


def _positive_fx_rate(value):
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return rate if math.isfinite(rate) and rate > 0 else None


def _apply_display_currency(card, state, previous, language):
    """Freeze the locale display-currency decision into both engine artifacts.

    The engine fetches current rates during ``prepare``.  If the requested
    display rate is unavailable, only presentation may reuse the prior state's
    rate; portfolio calculations keep their current honesty/fx-gap semantics.
    ``preview`` and ``finalize`` therefore remain deterministic and offline.
    """
    requested = card_renderer.default_display_currency(language)
    card_meta = dict((card or {}).get("currency_meta") or {})
    state_meta = dict((state or {}).get("currency_meta") or {})
    aggregate = str(card_meta.get("aggregate_currency") or
                    state_meta.get("aggregate_currency") or "USD").upper()
    mixed = bool(card_meta.get("mixed") if "mixed" in card_meta else state_meta.get("mixed"))

    rate = None
    source = "identity"
    reason = None
    as_of = None
    effective = aggregate
    if mixed:
        effective = requested
        current_fx = dict(state_meta.get("fx") or {})
        current_fx.update(card_meta.get("fx") or {})
        currencies = list(card_meta.get("currencies") or state_meta.get("currencies") or [])
        explicit_gaps = (((card or {}).get("data_integrity") or {}).get("fx_gaps") or [])
        held_rate_missing = bool(explicit_gaps) or bool(currencies and any(
            str(currency).upper() != "USD" and _positive_fx_rate(current_fx.get(str(currency).upper())) is None
            for currency in currencies
        ))
        if held_rate_missing:
            # The engine already aggregated the missing held currency with its
            # explicit 1:1 approximation.  A display-only cache cannot repair
            # that common-currency amount, so fall back to original buckets.
            effective = None
            source = "unavailable"
            reason = "portfolio_fx_gap"
        else:
            rate = 1.0 if requested == "USD" else _positive_fx_rate(current_fx.get(requested))
            source = "current"
        if not held_rate_missing and rate is None:
            previous_meta = ((previous or {}).get("currency_meta") or {})
            if previous_meta.get("mixed") and previous_meta.get("display_currency") == requested:
                rate = _positive_fx_rate(previous_meta.get("display_fx_rate"))
            if rate is None:
                rate = _positive_fx_rate((previous_meta.get("fx") or {}).get(requested))
            if rate is not None:
                source = "cached"
                as_of = previous_meta.get("display_fx_as_of") or (previous or {}).get("date_end")
            else:
                effective = None
                source = "unavailable"
                reason = "display_fx_gap"

    def enrich(artifact):
        out = dict(artifact or {})
        meta = dict(out.get("currency_meta") or {})
        meta.update({
            "requested_display_currency": requested,
            "display_currency": effective,
            "display_fx_source": source,
            "display_fx_rate": rate if mixed else None,
        })
        if reason:
            meta["display_fx_reason"] = reason
        else:
            meta.pop("display_fx_reason", None)
        if as_of:
            meta["display_fx_as_of"] = as_of
        else:
            meta.pop("display_fx_as_of", None)
        out["currency_meta"] = meta
        return out

    return enrich(card), enrich(state)


def _review_date(state):
    try:
        return dt.date.fromisoformat(str((state or {}).get("date_end")))
    except (TypeError, ValueError):
        return dt.date.today()


# Cadence detection (#237). The span between the previous review and this one
# decides how heavy a card is warranted: a short span is a high-frequency check
# that later stages should render light, while a long span, a first review, or a
# snapshot opening check warrants the full story card. The threshold is the one
# human knob ("how short counts as high-frequency"); everything else keys off
# real timestamps. This is a presentation-selection signal only — it never
# changes an engine number, so card output is unchanged until a consumer reads
# the tier.
CADENCE_LIGHT_MAX_DAYS = 5


def _review_span_days(date_end, previous):
    """Calendar days from the previous review's ``date_end`` to this one.

    Returns None when there is no comparable prior boundary (first review,
    missing or unparseable dates). An out-of-order re-run clamps to 0 instead of
    going negative, so a stale resend cannot read as a long span.
    """
    prev_end = (previous or {}).get("date_end")
    if not date_end or not prev_end:
        return None
    try:
        start = dt.date.fromisoformat(str(prev_end))
        end = dt.date.fromisoformat(str(date_end))
    except (TypeError, ValueError):
        return None
    return max(0, (end - start).days)


def _cadence(route, date_end, previous):
    """Classify this review's cadence tier from its span.

    ``light`` marks a short-span, high-frequency review that later stages should
    render as a light capture rather than the full story card; ``full`` marks a
    first review, a snapshot opening check, a returning review with no
    comparable prior boundary, or any span past the threshold. The tier is
    advisory metadata for downstream rendering and questioning; it does not gate
    or alter any engine calculation, so existing output is unchanged until a
    consumer opts in.
    """
    threshold = CADENCE_LIGHT_MAX_DAYS
    if route in ("first_review", "snapshot_review"):
        return {"tier": "full", "span_days": None, "threshold_days": threshold,
                "basis": route, "override": None}
    span = _review_span_days(date_end, previous)
    if span is None:
        return {"tier": "full", "span_days": None, "threshold_days": threshold,
                "basis": "no_prior_boundary", "override": None}
    tier = "light" if span <= threshold else "full"
    return {"tier": tier, "span_days": span, "threshold_days": threshold,
            "basis": "span", "override": None}


def _trade_span_days(date_start, date_end):
    """Calendar-day span of the trade file itself (first row to last).

    Mirrors ``trade_recap.build_state``'s own span basis; used only for the
    advisory ``durability_short`` flag in :func:`_review_tier`. Returns None when
    either boundary is missing or unparseable. Distinct from
    ``_review_span_days``, which measures the gap *between* reviews.
    """
    if not date_start or not date_end:
        return None
    try:
        start = dt.date.fromisoformat(str(date_start))
        end = dt.date.fromisoformat(str(date_end))
    except (TypeError, ValueError):
        return None
    return max(0, (end - start).days)


def _review_tier(state):
    """Engine-owned classification of how much a file can support (#306).

    Additive Review Plan metadata (frozen into ``state_snapshot``), deterministic
    and fail-closed. It records the entry decision in one place so later
    consumers — routing, question density, card framing — read a single
    engine-owned source instead of re-deriving thresholds in agent prose:

    - ``empty``:       no current holdings and no closed round trips — nothing to
                       diagnose; the flow must tell the user exactly what to add.
    - ``behavioral``:  at least ``MIN_ROUND_TRIPS`` closed round trips — enough
                       realized history for a full behavioral review.
    - ``structural``:  holdings exist but fewer round trips — an opening
                       structural check.

    Per #306 the calendar span is advisory only: ``durability_short`` flags a
    short trade window for a later note, but round-trip *count* — never span —
    decides ``behavioral``, so a high-frequency short-window file is not demoted
    the way the ``rts<3 or span<MIN_SPAN_DAYS`` commitment gate would demote it.
    Nothing consumes this field yet; emitting it first keeps the tier a single
    source of truth for the follow-up routing/rendering changes.
    """
    n_rt = state.get("n_round_trips") or 0
    n_held = state.get("n_held")
    if n_held is None:
        n_held = len(((state.get("holdings") or {}).get("positions")) or {})
    span_days = _trade_span_days(state.get("date_start"), state.get("date_end"))
    if n_held == 0 and n_rt == 0:
        tier = "empty"
    elif n_rt >= trade_recap.MIN_ROUND_TRIPS:
        tier = "behavioral"
    else:
        tier = "structural"
    return {
        "tier": tier,
        "n_round_trips": n_rt,
        "n_held": n_held,
        "span_days": span_days,
        "min_round_trips": trade_recap.MIN_ROUND_TRIPS,
        "min_span_days": trade_recap.MIN_SPAN_DAYS,
        "durability_short": span_days is not None and span_days < trade_recap.MIN_SPAN_DAYS,
    }


# Monthly vs-market cadence (#284, output contract §3): the vs-market
# comparison segment renders on the first full review of each calendar month.
# "First this month" derives from committed-session history — canonical
# bundles plus pre-v2 log.jsonl rows — judged by each review's own date_end,
# never the wall clock. The decision is frozen into the engine card at
# prepare time so preview/finalize retries and later re-renders stay
# deterministic even after other sessions commit.


def _month_key(value):
    """`YYYY-MM` of an ISO date, or None when the date cannot be parsed."""
    try:
        date = dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return f"{date.year:04d}-{date.month:02d}"


def _vs_market_gate(root, date_end, exclude_session_id=None):
    """Decide whether this review renders the vs-market segment (#284).

    Consumers of the monthly slot are committed reviews that could have
    rendered the segment: canonical persistent bundles on the trades routes
    (snapshot reviews suppress the segment by design and must not burn the
    month; test drives never touch coach memory; light-tier sessions never
    finalize a card, so they neither consume nor reset the slot) plus pre-v2
    ``log.jsonl`` rows not already represented by a canonical bundle. The
    current session id is excluded so an idempotent re-prepare of an already
    committed review cannot flip its own decision. Fail-closed toward
    showing: an unreadable history or an unparseable review date renders the
    segment — over-showing is safer than silently hiding the comparison.
    """
    month = _month_key(date_end)
    if month is None:
        return {"render": True, "basis": "no_review_date", "month": None}
    try:
        seen_sessions = set()
        for dir_session_id, bundle in session.iter_canonical_bundles(root):
            session_id = str(bundle.get("session_id") or dir_session_id)
            seen_sessions.add(session_id)
            if exclude_session_id and session_id == str(exclude_session_id):
                continue
            if bundle.get("route") == "snapshot_review":
                continue
            if _month_key((bundle.get("engine_state") or {}).get("date_end")) == month:
                return {"render": False, "basis": "already_rendered_this_month",
                        "month": month}
        for row in _jsonl(os.path.join(root, "log.jsonl")):
            session_id = row.get("session_id")
            if session_id and str(session_id) in seen_sessions:
                continue  # projection of a canonical bundle classified above
            if exclude_session_id and session_id and str(session_id) == str(exclude_session_id):
                continue
            if _month_key(row.get("date_end")) == month:
                return {"render": False, "basis": "already_rendered_this_month",
                        "month": month}
    except Exception:
        # Fail-closed toward showing (#284): a gate helper must never crash
        # prepare, and a history it cannot read must not hide the segment.
        return {"render": True, "basis": "history_unreadable", "month": month}
    return {"render": True, "basis": "first_full_review_of_month", "month": month}


_CURRENT_VIEW_DIMS = {"position_sizing", "diversification"}
_CURRENT_VIEW_METRICS = {
    "max_pos_pct", "max_pos_ticker", "ai_pct", "max_sector_pct", "top3_pct"
}


def _is_current_view_dimension(row):
    if not isinstance(row, dict):
        return False
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    dim = row.get("dim") or row.get("kind") or raw.get("dim")
    return bool(dim) and card_renderer.dimension_id(dim) in _CURRENT_VIEW_DIMS


def _empty_portfolio_structure(source=None):
    return {
        "schema_version": 1,
        "policy": (source or {}).get("policy"),
        "allocation_weight": None,
        "concentrated_etf_weight": None,
        "by_kind": {},
        "allocation_etfs": [],
        "concentrated_etfs": [],
        "metadata_gaps": [],
    }


def _gate_current_view(card, state, detail):
    """Remove raw-CSV current-position claims that disagree with the ledger.

    Transaction rows still support history diagnostics such as exits, holding
    time, averaging-down events, payoff, and attribution.  They cannot support
    current sizing, diversification, unrealized P&L, or ETF weights when the
    complete snapshot anchor plus later ledger trades says the current account
    contains something else.
    """
    card["dims_raw"] = [row for row in card.get("dims_raw") or []
                        if not _is_current_view_dimension(row)]
    card["top_holes"] = [row for row in card.get("top_holes") or []
                         if not _is_current_view_dimension(row)]
    card["candidate_rules"] = [row for row in card.get("candidate_rules") or []
                               if not _is_current_view_dimension(row)]
    card["prescriptions"] = [row for row in card.get("prescriptions") or []
                             if not _is_current_view_dimension(row)]
    card["what_if"] = None
    card["ticker_diagnosis"] = []
    card["strength"] = None

    overview = dict(card.get("overview") or {})
    overview["total_pnl"] = None
    overview["unrealized"] = None
    card["overview"] = overview
    card["acct_perf"] = {"gate": {"status": "accounting_reconciliation", "data": {}}}

    for artifact in (card, state):
        meta = dict(artifact.get("currency_meta") or {})
        meta["pnl_by_currency"] = None
        artifact["currency_meta"] = meta
        cash = artifact.get("cash")
        if isinstance(cash, dict):
            cash = dict(cash)
            cash["weight"] = None
            artifact["cash"] = cash

    prior_structure = card.get("portfolio_structure") or state.get("portfolio_structure") or {}
    structure = _empty_portfolio_structure(prior_structure)
    card["portfolio_structure"] = structure
    state["portfolio_structure"] = dict(structure)

    metrics = dict(state.get("metrics") or {})
    for key in _CURRENT_VIEW_METRICS:
        metrics[key] = None
    state["metrics"] = metrics
    state["rule"] = None
    state["problem_events"] = [
        row for row in state.get("problem_events") or []
        if row.get("key") not in {"oversize", "concentration"}
    ]
    opportunities = state.get("problem_opportunities")
    if isinstance(opportunities, dict):
        opportunities = dict(opportunities)
        opportunities["oversize"] = False
        opportunities["concentration"] = False
        state["problem_opportunities"] = opportunities

    first_hole = (card.get("top_holes") or [None])[0]
    if isinstance(first_hole, dict):
        dim = first_hole.get("dim") or (first_hole.get("raw") or {}).get("dim")
        metric_key = DIM_METRIC.get(card_renderer.dimension_id(dim)) if dim else None
        state["headline_dim"] = dim
        state["headline_metric"] = {
            "key": metric_key,
            "value": metrics.get(metric_key) if metric_key else None,
        }
    else:
        state["headline_dim"] = None
        state["headline_metric"] = {"key": None, "value": None}

    integrity = dict(card.get("data_integrity") or {})
    integrity["accounting_reconciliation"] = detail
    card["data_integrity"] = integrity
    honesty = [row for row in card.get("honesty_ledger") or []
               if row.get("key") != "accounting_reconciliation"]
    honesty.append({
        "key": "accounting_reconciliation",
        "status": "gated",
        "data": detail,
    })
    card["honesty_ledger"] = honesty


def _overlay_ledger_holdings(card, state, derived):
    """Make ledger holdings/cycles canonical and gate divergent card surfaces."""
    raw_positions = dict(((state.get("holdings") or {}).get("positions") or {}))
    canonical = dict(derived.get("holdings") or {})
    raw_tickers, canonical_tickers = set(raw_positions), set(canonical)
    mismatches = []
    for ticker in sorted(raw_tickers | canonical_tickers):
        raw, fact = raw_positions.get(ticker), canonical.get(ticker)
        if raw is None or fact is None:
            mismatches.append({"ticker": ticker, "kind": "ticker_set"})
            continue
        raw_shares = card_renderer._finite_number(raw.get("shares"))
        canonical_shares = card_renderer._finite_number(fact.get("shares"))
        if (raw_shares is None or canonical_shares is None
                or abs(raw_shares - canonical_shares) > ledger.SHARES_TOL):
            mismatches.append({"ticker": ticker, "kind": "shares"})
            continue
        # Transaction artifacts historically omit these fields for the default
        # US/USD case.  Default the raw side accordingly; falling back to the
        # canonical fact would hide a missing/misclassified non-US position.
        raw_market = str(raw.get("market") or "US").upper()
        raw_currency = str(raw.get("currency") or "USD").upper()
        if raw_market != str(fact.get("market") or "US").upper():
            mismatches.append({"ticker": ticker, "kind": "market"})
        if raw_currency != str(fact.get("currency") or "USD").upper():
            mismatches.append({"ticker": ticker, "kind": "currency"})

    prices = ((state.get("price_snapshot") or {}).get("prices") or {})
    full_price_coverage = bool(canonical) and all(
        card_renderer._finite_number(prices.get(ticker)) is not None
        and float(prices[ticker]) > 0 for ticker in canonical
    )
    # Current prices can verify today's market value, but they cannot repair a
    # divergent or unknown cost basis.  Unrealized and total P&L still depend
    # on that basis, so compare it even when every ticker has a live price.
    if not mismatches and canonical:
        for ticker, fact in sorted(canonical.items()):
            raw_cost = card_renderer._finite_number((raw_positions.get(ticker) or {}).get("cost"))
            canonical_cost = card_renderer._finite_number(fact.get("cost_total"))
            if (raw_cost is None or canonical_cost is None
                    or not math.isclose(raw_cost, canonical_cost, rel_tol=1e-6, abs_tol=0.05)):
                mismatches.append({"ticker": ticker, "kind": "valuation"})

    positions = {}
    for ticker, fact in sorted(canonical.items()):
        raw = dict(raw_positions.get(ticker) or {})
        observed_cycle = raw.get("cycle_id")
        observed_start = raw.get("cycle_start")
        add_count = int(fact.get("add_count") or 0)
        row = dict(raw)
        row.update({
            "shares": fact.get("shares"),
            "cost": fact.get("cost_total"),
            "avg_cost": fact.get("avg_cost"),
            "market": fact.get("market"),
            "currency": fact.get("currency"),
            "cycle_start": fact.get("since"),
            "cycle_id": fact.get("cycle_id"),
            "origin": fact.get("origin"),
            "left_truncated": fact.get("origin") == "snapshot",
            "add_count": add_count,
            "decision_cursor": fact.get("decision_cursor"),
        })
        if observed_cycle and observed_cycle != fact.get("cycle_id"):
            row["observed_cycle_id"] = observed_cycle
        if observed_start and observed_start != fact.get("since"):
            row["observed_cycle_start"] = observed_start
        positions[ticker] = row

    state["holdings"] = {
        "as_of": state.get("date_end") or (state.get("holdings") or {}).get("as_of"),
        "derived_from": "snapshot_plus_trades",
        "is_complete": True,
        "positions": positions,
    }
    state["n_held"] = len(positions)
    metrics = dict(state.get("metrics") or {})
    metrics["n_holdings"] = len(positions)
    state["metrics"] = metrics

    # A pre-anchor add is history, not a new decision after the opening snapshot.
    post_anchor_adds = {ticker for ticker, row in positions.items()
                        if row.get("decision_cursor")}
    card["thesis_questions"] = [row for row in card.get("thesis_questions") or []
                                if row.get("ticker") in post_anchor_adds]

    detail = {
        "status": "matched" if not mismatches else "current_view_gated",
        "raw_positions_n": len(raw_positions),
        "canonical_positions_n": len(positions),
        "full_price_coverage": full_price_coverage,
        "mismatches": mismatches,
    }
    if mismatches:
        _gate_current_view(card, state, detail)
    return card, state, detail


def _ingest_trades(root, paths, card, state):
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

    ledger_path = os.path.join(root, "ledger.jsonl")
    # This is one root-wide check/derive/append transaction.  Snapshot finalize
    # holds the same lock from its final empty-history check through canonical
    # commit and anchor projection, so neither path can observe an empty root
    # and then write across the other's boundary.
    with session.projection_transaction(root):
        existing, skipped_lines = ledger.load_ledger(ledger_path)
        virtual = list(existing)
        fresh_all = []
        skipped_dup = 0
        for batch in batches:
            fresh, dup = ledger.dedupe_against(virtual, batch)
            fresh_all.extend(fresh)
            virtual.extend(fresh)
            skipped_dup += dup
        reconciliation = None
        # A complete snapshot is the accounting source of truth for current
        # holdings.  Derive against the virtual post-import ledger before the first
        # write so the card can fail closed without leaving a partial import.
        if ledger.latest_anchor(existing) is not None:
            card, state, reconciliation = _overlay_ledger_holdings(
                card, state, ledger.derive_holdings(virtual)
            )
        if fresh_all:
            ledger.append_events(ledger_path, fresh_all)
        result = {
            "path": ledger_path,
            "appended": len(fresh_all),
            "skipped_dup": skipped_dup,
            "skipped_non_trade": skipped_non_trade,
            "skipped_future_dated": skipped_future,
            "skipped_ledger_lines": skipped_lines,
        }
        if reconciliation is not None:
            result["holdings_reconciliation"] = reconciliation
    return result, card, state


def _exit_narrative_index(root):
    """Map revisit_id -> latest captured exit narrative (canonical sessions win).

    Legacy `theses.jsonl` rows load first, then canonical bundles override them
    in the iterator's shared (date_end, session_id) order — the same precedence
    `_thesis_event_history` uses — so capture identity and the recorded reason
    stay consistent even when an undated bundle is present.
    """
    index = {}
    for row in _jsonl(os.path.join(root, "theses.jsonl")):
        if row.get("event") == "exit_narrative" and row.get("revisit_id"):
            index[row["revisit_id"]] = row
    for _session_id, bundle in session.iter_canonical_bundles(root, sort_by_date=True):
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
    ordered_bundles = []
    for session_id, bundle in session.iter_canonical_bundles(root, sort_by_date=True):
        canonical_sessions.add(session_id)
        ordered_bundles.append(bundle)

    thesis_rows = [row for row in legacy_theses
                   if row.get("session_id") not in canonical_sessions]
    decision_rows = [row for row in legacy_decisions
                     if row.get("session_id") not in canonical_sessions]
    for bundle in ordered_bundles:
        thesis_rows.extend(bundle.get("thesis_updates") or [])
        thesis_rows.extend(bundle.get("exit_narratives") or [])
        decision_rows.extend(bundle.get("thesis_decisions") or [])
    return thesis_rows, decision_rows


def _rule_breach_history(root):
    """Return the latest canonical breach decision per rule.

    The history stays in immutable bundles rather than a second mutable ledger.
    It is used only to enforce the first-breach-or-worsening question cadence.
    """
    latest = {}
    for _session_id, bundle in session.iter_canonical_bundles(root, sort_by_date=True):
        for row in bundle.get("rule_breach_decisions") or []:
            if row.get("rule_id"):
                latest[row["rule_id"]] = row
    return latest


def _headline_motive_history(root):
    """Reconstruct typed headline-motive decisions from canonical bundles.

    The JSONL file is only a compatibility projection.  Canonical bundles win
    for their session, so deleting or partially rebuilding projections cannot
    erase the user's recorded classification from a later Review Plan.
    """
    legacy = _jsonl(os.path.join(root, "headline_motives.jsonl"))
    canonical_sessions = set()
    rows = []
    for session_id, bundle in session.iter_canonical_bundles(root, sort_by_date=True):
        canonical_sessions.add(session_id)
        rows.extend(bundle.get("headline_motive_events") or [])
    return [row for row in legacy if row.get("session_id") not in canonical_sessions] + rows


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
                   TR_LEDGER=os.path.join(root, "ledger.jsonl"),
                   TR_DISPLAY_CURRENCY=card_renderer.default_display_currency(args.language))
        env.pop("TR_PRICES", None)      # only an explicit --prices may inject a price envelope
        if getattr(args, "prices", None):
            env["TR_PRICES"] = os.path.abspath(os.path.expanduser(args.prices))
        previous = _previous_state(root)
        if previous and previous.get("date_end"):
            env["TR_PREV_END"] = str(previous["date_end"])
            # #270: last_state.date_end is THIS run's own date_end when the
            # identical week is reviewed again (a prior finalize already
            # advanced the anchor to it) — this is decided before the engine
            # has parsed the CSV, so it cannot be detected here. Also pass the
            # prev_end that state itself was built from, so the engine can
            # fall back to the closest genuinely-earlier review boundary
            # instead of aliasing prev_end to its own date_end (#166).
            if previous.get("prev_end"):
                env["TR_PREV_PREV_END"] = str(previous["prev_end"])
        cap_override = _position_cap_override(root)          # #324:標準版單一部位上限,通用預設可被覆寫
        if cap_override is not None:
            env["TR_MAX_POSITION_PCT"] = repr(cap_override)
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
    descriptions = copy.get("add_descriptions") or {}
    return [{"value": key, "label": copy["add_choices"][key],
             "description": descriptions[key]}
            for key in ("new_evidence", "planned_tranche", "valuation_change", "price_only", "skip")]


def _generic_options(language):
    copy = card_renderer.load_copy(language)
    labels = copy.get("generic_choices") or {}
    descriptions = copy.get("generic_descriptions") or {}
    return [{"value": key, "label": labels[key], "description": descriptions[key]}
            for key in ("deliberate_plan", "emotional_reaction",
                        "external_constraint", "skip")]


def _exit_consistency_question(card, language):
    """#303: one answerable motive question for the aggregated early-exit
    pattern, or ``None`` when no instrument carried a ``sold_winner_early`` tag.

    The stem is grounded in the exact engine facts the read-only ``[?]`` panel
    would otherwise state (same counts, same named instruments), so a user can
    reply with why those exits happened instead of reading a verdict-free
    observation they cannot answer. The answer contract is the shared motive
    axis; only the durable ``exit_consistency`` event stream is distinct, so
    these classifications never pollute the headline-motive history (#296/#299).
    """
    copy = card_renderer.load_copy(language)
    facts, sentence = card_renderer.exit_consistency_line(card, copy)
    stem = (copy.get("exit_consistency") or {}).get("question")
    if not facts or not sentence or not stem:
        return None
    en = str(language).lower().startswith("en")
    question = f"{sentence} {stem}" if en else f"{sentence}{stem}"
    row = {
        "id": "exit_consistency", "kind": "exit_consistency", "required": True,
        "question": question, "options": _generic_options(language),
        "ticker": facts["instruments"][0]["ticker"], "asked_because": sentence,
        "_importance": 0.0, "_tie": 2,
    }
    row["question_opportunity"] = question_surface.build_opportunity(row, language)
    return row


def _initial_thesis_options(language):
    """Canonical first-review entry-motive choices (#291), localized labels.

    Engine owns the stable codes; the copy layer localizes the labels. `skip`
    is the standard escape consistent with the other kinds.
    """
    copy = card_renderer.load_copy(language)
    labels = copy.get("initial_thesis_choices") or {}
    descriptions = copy.get("initial_thesis_descriptions") or {}
    return [{"value": key, "label": labels[key],
             "description": descriptions[key]}
            for key in ("planned_entry", "momentum_follow", "external_call",
                        "no_clear_thesis", "skip")]


def _exit_options(language, exit_kind):
    copy = card_renderer.load_copy(language)
    labels = (copy.get("exit_choices") or {}).get(exit_kind) or {}
    descriptions = copy.get("exit_descriptions") or {}
    return [{"value": key, "label": labels[key],
             "description": descriptions[key]}
            for key in ("price_target", "thesis_broken", "swap", "anxiety", "other", "skip")]


def _due_options(language):
    copy = card_renderer.load_copy(language)
    labels = copy.get("due_choices") or {}
    descriptions = copy.get("due_descriptions") or {}
    return [{"value": key, "label": labels[key],
             "description": descriptions[key]}
            for key in ("still_valid", "modified", "falsified", "skip")]


def _rule_breach_options(language, can_revise=True):
    copy = card_renderer.load_copy(language)
    labels = copy.get("rule_breach_choices") or {}
    descriptions = copy.get("rule_breach_descriptions") or {}
    keys = ("keep_tracking", "revise_rule", "exception") if can_revise else ("keep_tracking", "exception")
    return [{"value": key, "label": labels[key],
             "description": descriptions[key]} for key in keys]


def _breach_evidence_text(last_breach, language):
    events = (last_breach or {}).get("events") or []
    parts = []
    for event in events:
        ticker = event.get("ticker")
        note = event.get("note")
        if ticker and note:
            parts.append(f"{ticker}: {note}")
        elif ticker or note:
            parts.append(str(ticker or note))
    breach_copy = (card_renderer.load_copy(language).get("breach_evidence") or {})
    if not parts:
        return breach_copy.get("none") or ""
    shown = (breach_copy.get("joiner") or "; ").join(parts)
    extra = int((last_breach or {}).get("event_count") or 0) - len(events)
    if extra > 0:
        shown += (breach_copy.get("more") or "").format(extra=extra)
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
    # #291: the caller (_question_queue) applies RULE_BREACH_LIMIT so it can
    # record the trimmed rows in the selection report. Direct callers see the
    # full ranked list, still bounded by the two-per-key/worsening cadence above.
    return candidates


# ──────────────── the per-period condition check flow (#412 / #434) ────────────────
#
# A condition slot without a check flow is a promise made into a file. The plan
# now names what is due, the agent performs each frozen query and submits the
# result, and the engine — never the agent's prose — decides whether a line was
# crossed. Two exchanges can follow, and only two: a crossing worth one
# two-sided question, and a doubt about whether the threshold still measures
# what it measured. Everything else is recorded and stays quiet.
#
# The two-turn shape mirrors the price envelope (#289, references/price-feed.md):
# `prepare` publishes the request, the agent looks the figures up, and reruns
# `prepare --condition-checks <path>` so the questions can be posed against real
# evidence. Skipping the second pass is a legal, degraded run — the checks still
# arrive with `answers` and are still recorded, only without the conversation.

def _condition_paths(root):
    return (os.path.join(root, "conditions.jsonl"),
            os.path.join(root, "condition_checks.jsonl"))


def _with_condition_line(commitment):
    """The prior commitment with its condition's line identity resolved.

    A root slot row carries no ``line_id`` on disk (it *is* its line's first
    row), and a revised one does. Resolving it once here — with the same
    ``conditions.slot_line_id`` every other reader uses — is what lets the card
    match this period's check back to last period's commitment without owning a
    second copy of the line rules. Matching on ``slot_id`` alone silently
    stopped working after a second revision, because the new check carries the
    newest slot_id while the prior commitment still names the one before it
    (external review, round 1)."""
    condition = (commitment or {}).get("condition")
    if not isinstance(condition, dict) or not condition.get("slot_id"):
        return commitment
    return {**commitment,
            "condition": {**condition, "line_id": conditions.slot_line_id(condition)}}


def _condition_store(root):
    """Both append-only condition stores, read once per review.

    Returns ``(slots, checks, unreadable)`` where ``unreadable`` counts the
    lines each file lost. Corruption is reported, never silently skipped: a
    dropped row would otherwise read as a condition the user never wrote."""
    slots_path, checks_path = _condition_paths(root)
    slots, slots_unreadable = conditions.load_slots(slots_path)
    checks, checks_unreadable = conditions.load_checks(checks_path)
    return slots, checks, {"slots": slots_unreadable, "checks": checks_unreadable}


def _last_check_summary(check):
    """What the plan says about a line's most recent check — four fields, not
    the row. The full history is on disk; what the next lookup needs to know is
    when it last happened, whether it worked, whether it found anything new,
    and what the verdict of record was."""
    if not check:
        return None
    return {"date_end": check.get("date_end"), "lookup_status": check.get("lookup_status"),
            "information_state": check.get("information_state"),
            "final_verdict": check.get("final_verdict")}


def _thesis_cycle_index(positions, thesis_rows, cycle_relinks):
    """``{cycle_id: {"ticker": ..., "live": bool}}`` — every position cycle a
    thesis-linked condition could name, and the one place liveness is decided.

    ``cycle_relinks`` is **required**, with no default, and this function is the
    only place the two row sources are joined. A relink built this session is
    not on disk until finalize writes it, so a caller reading only
    ``_thesis_event_history(root)`` sees a provisional cycle as dead and retires
    a condition the user is still holding. That is what happened: one derivation
    with its input assembled in two places is the same defect as two derivations
    (external review, round 3; docs/development-guide.md section 7). Making the
    argument mandatory is the guard — a new call site cannot forget it quietly.
    Plan-side consumers should use ``_plan_thesis_cycles`` rather than composing
    the arguments themselves.

    A thesis falsifier guards a position (#416 C2). Once that position is fully
    exited the cycle leaves ``holdings.positions``, and with it the reason the
    condition existed: there is nothing left to sell if it triggers. So liveness
    is exactly "this cycle is still held", read from the same rows that already
    own it — no second fold of the thesis event history to disagree with.

    A closed cycle stays in the map, with ``live: False`` and the ticker its
    thesis history recorded. That is not decoration: a condition that retires
    has to be able to say *which* thesis it stopped guarding, and the position
    it names is by then gone from ``positions``.

    The one wrinkle is the provisional cycle id an incomplete opening snapshot
    hands out. A later transaction review can relink that holding to its real
    cycle (``thesis.build_incomplete_snapshot_cycle_relinks``), and a condition
    written against the provisional id would then look closed while the user
    still holds the position. Raw thesis rows carry the mapping permanently, so
    the alias is read from them rather than from the folded state, which a later
    plain thesis update overwrites.

    Every other reader takes what this stamps (``thesis_link`` on a live entry,
    ``condition_slots_retired`` on a closed one), so nothing downstream
    re-derives which thesis a slot belongs to or whether it still stands."""
    # The one composition: persisted history plus this session's relinks, which
    # are real but not yet on disk. Every caller passes the two ingredients and
    # none of them concatenates.
    rows = [row for row in list(thesis_rows or []) + list(cycle_relinks or [])
            if isinstance(row, dict)]
    index = {row["cycle_id"]: {"ticker": ticker, "live": True}
             for ticker, row in (positions or {}).items()
             if isinstance(row, dict) and row.get("cycle_id")}
    # Aliases first, while the map holds only live cycles: a provisional id may
    # only inherit liveness from a cycle that actually has it. A genuinely
    # exited cycle is in no live entry, so nothing here can resurrect it.
    for row in rows:
        provenance = row.get("cycle_provenance")
        origin = provenance.get("from_cycle_id") if isinstance(provenance, dict) else None
        target = row.get("cycle_id")
        if origin and target in index and origin not in index:
            index[origin] = index[target]
    for row in rows:
        cycle_id, ticker = row.get("cycle_id"), row.get("ticker")
        if cycle_id and ticker and cycle_id not in index:
            index[cycle_id] = {"ticker": ticker, "live": False}
    return index


def _plan_thesis_cycles(plan):
    """The cycle index for a plan that already exists — the single way any
    finalize-side consumer obtains one.

    It reads both ingredients from where the plan itself carries them: the
    persisted thesis history under ``state_root``, and this session's relinks
    from ``state_snapshot.thesis_cycle_relinks``, which ``_build_plan`` stamped
    and ``_draft_bundle`` is about to persist. Before this existed, the card
    context re-read only the history, so a cycle that is live *only* via a
    relink read as dead and its condition was dropped from the card join
    (external review, round 3)."""
    thesis_rows, _decisions = _thesis_event_history(plan.get("state_root"))
    return _thesis_cycle_index(
        _active_positions(plan.get("engine_state") or {}), thesis_rows,
        ((plan.get("state_snapshot") or {}).get("thesis_cycle_relinks") or []))


def _condition_lines(root, thesis_cycles=None, previous_date_end=None):
    """``(live, retired, unreadable)`` — the whole standing-condition picture,
    derived once so no later surface has to work any of it out again.

    ``live`` is every line still standing, one entry per *line* (a revised
    criterion is the same line), ordered oldest-last-checked first, each stamped
    with ``line_id``, ``last_check``, and — when it guards a thesis —
    ``thesis_link``. It is deliberately **not** capped here: the cap is about
    how much lookup work one review asks for, and every other consumer (the
    question layer, the card's join, the summary's total) needs the whole live
    set. Scoping any of them to the capped slice makes a line the cap held back
    indistinguishable from one that no longer exists.

    ``retired`` is what left, because its thesis cycle closed (#416 C2): the
    position is fully exited, so there is nothing left to sell if the condition
    triggers. Each entry carries the ticker it guarded, the criterion, and
    whether *this* review is the one where it left — ``announce``, true when the
    line's last check belongs to the immediately preceding review, which is
    exactly the period it was still being looked at. That makes the card's
    retirement sentence an **event**, said once, rather than a state sentence
    that would print for the rest of the user's history.

    Nothing is deleted: the store is append-only and every row is a fact about
    what the user meant when they wrote it."""
    slots, checks, unreadable = _condition_store(root)
    lines = conditions.latest_by_line(slots)
    entries, retired = [], []
    for line_id, slot in lines.items():
        cycle_id = slot.get("thesis_cycle_id")
        last = conditions.last_check_for(checks, slots, slot)
        link = None
        if cycle_id:
            cycle = (thesis_cycles or {}).get(cycle_id) or {}
            if not cycle.get("live"):
                retired.append({
                    "cycle_id": cycle_id, "ticker": cycle.get("ticker"),
                    "line_id": str(line_id), "criterion": slot.get("criterion"),
                    "announce": bool(previous_date_end and last
                                     and last.get("date_end") == previous_date_end)})
                continue
            link = {"cycle_id": cycle_id, "ticker": cycle.get("ticker")}
        # `line_id` is stamped on every entry, including a root row that has
        # none on disk. It is how the card resolves a check back to the
        # condition it belongs to without owning a second copy of the line
        # semantics — one reader, here (external review, round 1). `thesis_link`
        # is stamped for the same reason: the renderer and the question layer
        # read which thesis this guards, they never work it out again.
        entry = {**slot, "line_id": str(line_id), "last_check": _last_check_summary(last)}
        if link:
            entry["thesis_link"] = link
        entries.append((str(last.get("date_end") or "") if last else "", str(line_id), entry))
    # A never-checked line sorts first on the empty string, which is exactly
    # "oldest": nothing has ever been looked up for it.
    entries.sort(key=lambda item: (item[0], item[1]))
    return [entry[2] for entry in entries], retired, unreadable


def _condition_due(root, thesis_cycles=None, previous_date_end=None):
    """``(due, summary, thesis_links, retired_now)`` — what this review publishes.

    The bounded lookup request is ``live[:CONDITION_LOOKUP_CAP]``, and the
    summary is what makes that bound honest: it states the total, the number
    sent, and the number held back, so no reader — agent or card — can mistake
    the list for the whole record.

    A retired line leaves ``lines_total`` and is counted in ``retired_lines``
    instead. Inside the total, the card's own arithmetic would report it as a
    concern "coming back next review" forever, which is the opposite of true.

    ``thesis_links`` and ``retired_now`` both span the full picture rather than
    the capped slice, for the reason ``_condition_lines`` states."""
    live, retired, unreadable = _condition_lines(root, thesis_cycles, previous_date_end)
    due = live[:CONDITION_LOOKUP_CAP]
    summary = {"lines_total": len(live), "due_now": len(due),
               "beyond_cap": max(0, len(live) - len(due)),
               "unmapped_lines": sum(1 for row in live if row.get("tier") == "unmapped"),
               "retired_lines": len(retired),
               "unreadable_slots": unreadable["slots"],
               "unreadable_checks": unreadable["checks"]}
    thesis_links = {row["line_id"]: row["thesis_link"] for row in live if row.get("thesis_link")}
    retired_now = [{"cycle_id": row["cycle_id"], "ticker": row["ticker"],
                    "criterion": row["criterion"]}
                   for row in retired if row["announce"]]
    return due, summary, thesis_links, retired_now


_CHECK_ENVELOPE_KEYS = frozenset({"slot_id", "check"})


def _condition_check_envelopes(raw, where):
    """Normalize the agent's submitted lookups into ``[(slot_id, check_input)]``.

    One shape in both places it can arrive — ``prepare --condition-checks`` and
    ``answers.condition_checks`` — because they are the same envelope submitted
    at two moments, and two shapes would make the equality gate below compare
    apples to pears."""
    if raw is None:
        return []
    rows = raw.get("condition_checks") if isinstance(raw, dict) else raw
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ReviewError(f"{where}.condition_checks must be an array")
    out, seen = [], set()
    for index, row in enumerate(rows):
        label = f"{where}.condition_checks[{index}]"
        if not isinstance(row, dict):
            raise ReviewError(f"{label} must be an object")
        unknown = set(row) - _CHECK_ENVELOPE_KEYS
        if unknown:
            raise ReviewError(f"{label} has unknown fields: " + ", ".join(sorted(unknown)))
        slot_id = str(row.get("slot_id") or "").strip()
        if not slot_id:
            raise ReviewError(f"{label} requires slot_id")
        if slot_id in seen:
            raise ReviewError(f"{label}: two checks for the same condition in one review — "
                              "a period has one result per line, not a running commentary")
        seen.add(slot_id)
        check = row.get("check")
        if not isinstance(check, dict):
            raise ReviewError(f"{label}.check must be the lookup envelope object")
        out.append((slot_id, check))
    return out


def _check_id(session_id, slot_id):
    """Content-addressed, so re-finalizing the same session rebuilds byte-identical
    rows and the idempotent append stays a no-op instead of doubling the record."""
    digest = hashlib.sha256(f"{session_id}|{slot_id}".encode("utf-8")).hexdigest()[:16]
    return "check-" + digest


def _resolve_check_slot(slots, slot_id):
    """The live head of the line ``slot_id`` belongs to.

    A check always evaluates the criterion that is current, and a revision
    changes slot_id while keeping the line — so an envelope naming a superseded
    version still lands on the right line rather than being refused for naming
    an id the user never saw change."""
    by_id = {row["slot_id"]: row for row in slots if isinstance(row, dict) and row.get("slot_id")}
    named = by_id.get(slot_id)
    if named is None:
        return None
    return conditions.latest_by_line(slots).get(conditions.slot_line_id(named)) or named


def _build_condition_checks(slots, checks, envelopes, session_id, date_end, responses=None):
    """Validate every submitted lookup into a durable check row.

    ``responses`` maps a line id to what the user's own answer contributes
    (``user_response`` / ``basis_resolution``). Those reach ``build_check`` as
    keyword arguments, never through the envelope — an envelope carrying either
    is refused there by name — so one row carries the complete story (the
    evidence, the engine's comparison, and what the user said) without the
    agent ever being able to author the last part.

    Dedup is by *line*, not by the ``slot_id`` the envelope happened to name. A
    revision changes slot_id while keeping the line, so a superseded id and the
    live head are two names for one condition; accepting both would append two
    rows for the same (condition, period), and the second would silently win
    every later read (external review, round 1).

    A rejection is the agent's to fix and is reported as such. In particular an
    answer paired with a lookup that did not succeed this period is refused by
    ``build_check`` rather than quietly attached: there is no fresh evidence for
    that answer to be about."""
    responses = responses or {}
    built, seen_lines = [], {}
    for slot_id, envelope in envelopes:
        slot = _resolve_check_slot(slots, slot_id)
        if slot is None:
            raise ReviewError(
                f"condition check names an unknown condition: {slot_id!r}. A check is a result for "
                "a condition the user committed to; there is no such condition in the record")
        line_id = conditions.slot_line_id(slot)
        if line_id in seen_lines:
            raise ReviewError(
                f"two condition checks for the same condition in one review: {seen_lines[line_id]!r} "
                f"and {slot_id!r} are the same line ({line_id!r}) — a re-stated criterion keeps its "
                "line, so those are two names for one condition, and a period has one result per "
                "condition, not a running commentary")
        seen_lines[line_id] = slot_id
        answer = responses.get(line_id) or {}
        try:
            built.append(conditions.build_check(
                envelope, slot=slot,
                previous=conditions.previous_check_for(checks, slots, slot),
                check_id=_check_id(session_id, slot["slot_id"]),
                session_id=session_id or None, date_end=date_end,
                user_response=answer.get("user_response"),
                basis_resolution=answer.get("basis_resolution")))
        except conditions.ConditionError as exc:
            raise ReviewError(f"condition check rejected ({slot['slot_id']}): {exc}") from exc
    return built


def _synthesized_not_checked(slots, checks, due, built, session_id, date_end):
    """One row per due condition nobody submitted a result for.

    Absence is recorded on the ``lookup_status`` axis rather than left as a gap
    in the file, because a gap and a successful quiet period are indistinguishable
    when read back — and "we did not look" is the one thing the record must never
    lose. Silence about a condition is exactly how a user comes to believe a
    tripwire is set."""
    answered = {row["slot_id"] for row in built}
    rows = []
    for entry in due:
        slot = _resolve_check_slot(slots, entry.get("slot_id")) or entry
        if slot.get("slot_id") in answered:
            continue
        try:
            rows.append(conditions.build_check(
                {"lookup_status": "not_checked", "reason": "not submitted this review"},
                slot=slot, previous=conditions.previous_check_for(checks, slots, slot),
                check_id=_check_id(session_id, slot["slot_id"]),
                session_id=session_id or None, date_end=date_end))
        except conditions.ConditionError as exc:            # pragma: no cover - defensive
            raise ReviewError(f"condition check rejected ({slot.get('slot_id')}): {exc}") from exc
    return rows


def _crossing_distance(slot, check):
    """How far this observation sits past the user's line, relative to the line.

    Positive means crossed; a ``near_line`` reading is negative and approaches
    zero as it approaches the line. Sorting descending therefore ranks the
    deepest breach first and, among the not-yet-crossed, the nearest — one
    ordering over both states, so a `met` reading is never out-ranked by a
    `near_line` one."""
    threshold = slot.get("threshold") or {}
    line = card_renderer._finite_number(threshold.get("value"))
    value = card_renderer._finite_number((check.get("observation") or {}).get("value"))
    if line is None or value is None:
        return 0.0
    past = (line - value) if threshold.get("direction") == "below" else (value - line)
    scale = abs(line) or abs(card_renderer._finite_number(slot.get("near_line")) or 0) or 1.0
    return past / scale


def _condition_evidence(check, slot):
    """The one evidence string a question stem may be grounded in: what was found,
    where, and as of when. Never a verdict — the verdict is the engine's field."""
    observation = check.get("observation") or {}
    if "value" in observation:
        found = card_renderer.condition_value(observation["value"],
                                              (slot.get("threshold") or {}).get("unit"))
    else:
        found = str(observation.get("summary") or "").strip()
    source, as_of = observation.get("source"), observation.get("as_of")
    if source and as_of:
        return f"{found} ({source}, as of {as_of})"
    return found


def _condition_question_id(prefix, session_id, line_id):
    digest = hashlib.sha256(f"{prefix}|{session_id}|{line_id}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _options_from_copy(language, register, values):
    """Engine-owned option rows from one copy register. Same shape every other
    kind emits (`value` / `label` / `description`), so a `plain_text` host
    renders them without a special case — and a missing key surfaces as an
    empty label rather than an internal enum value on the user's screen (#262)."""
    copy = card_renderer.load_copy(language)
    labels = copy.get(register) or {}
    descriptions = copy.get(register + "_descriptions") or {}
    return [{"value": value, "label": labels.get(value, ""),
             "description": descriptions.get(value, "")} for value in values]


def _condition_crossing_options(condition_kind, language):
    values = (("yes", "no") if condition_kind == "event" else ("confirmed", "overridden"))
    return _options_from_copy(language, "condition_crossing_choices", values + ("skip",))


def _condition_basis_options(language):
    return _options_from_copy(language, "condition_basis_choices",
                              ("revise_threshold", "revise_metric", "keep", "skip"))


def _condition_questions(built_checks, slots, session_id, language, rejected, thesis_links=None):
    """The at-most-one crossing question and every basis question this review earns.

    A crossing is emitted only from a lookup that succeeded — the engine's own
    comparison came back ``met``/``near_line``, or an event check carried the
    agent's alert. The stem is agent-authored (``question_opportunity``), which
    is the point: a crossing needs one sentence for acting and one for not, and
    a frozen template can only ever write one of them.

    `rejected` collects the crossings that lost the budget, so the plan states
    the deferral rather than the queue quietly shrinking.

    `thesis_links` is what ``_condition_due`` stamped for the conditions that
    guard a still-open thesis. It does two things here: it names the thesis on
    the question, so the adjudication never arrives detached from what it is
    about, and it is the liveness test for a thesis-linked line — a slot whose
    cycle is closed raises nothing even if a check for it was submitted out of
    band, because its position is gone and the answer could not change anything
    (#416 C2)."""
    by_id = {row["slot_id"]: row for row in slots if isinstance(row, dict) and row.get("slot_id")}
    thesis_links = thesis_links or {}
    crossings, basis = [], []
    for check in built_checks:
        slot = by_id.get(check.get("slot_id"))
        if slot is None or check.get("lookup_status") != "ok":
            continue
        line_id = conditions.slot_line_id(slot)
        link = thesis_links.get(line_id)
        if slot.get("thesis_cycle_id") and not link:
            continue
        condition_kind = slot.get("kind") or "numeric"
        evidence = _condition_evidence(check, slot)
        ticker = (link or {}).get("ticker")
        if check.get("basis_alert"):
            note = check["basis_alert"]["note"]
            row = {"id": _condition_question_id("condition_basis", session_id, line_id),
                   "kind": "condition_basis", "required": True,
                   "question": _condition_basis_stem(slot, note, language),
                   "options": _condition_basis_options(language),
                   "slot_id": slot["slot_id"], "line_id": line_id, "ticker": ticker,
                   "condition_kind": condition_kind, "criterion": slot.get("criterion"),
                   "evidence": evidence, "basis_note": note,
                   "_priority": 1, "_importance": 0.5, "_tie": 4}
            row["question_opportunity"] = question_surface.build_opportunity(row, language)
            basis.append(row)
        alerted = bool(check.get("event_alert"))
        if not alerted and check.get("engine_verdict") not in ("met", "near_line"):
            continue
        row = {"id": _condition_question_id("condition_crossing", session_id, line_id),
               "kind": "condition_crossing", "required": True,
               "question": _condition_crossing_stem(slot, check, evidence, language, ticker),
               "options": _condition_crossing_options(condition_kind, language),
               "slot_id": slot["slot_id"], "line_id": line_id, "ticker": ticker,
               "condition_kind": condition_kind, "criterion": slot.get("criterion"),
               "evidence": evidence,
               # An alerted event outranks every numeric reading: the engine can
               # re-derive a number next week, but an occurrence the user never
               # confirmed decays into "nobody said".
               "_priority": 1, "_tie": 0,
               "_crossing_rank": (0 if alerted else 1, -_crossing_distance(slot, check),
                                  str(slot["slot_id"]))}
        row["question_opportunity"] = question_surface.build_opportunity(row, language)
        crossings.append(row)
    crossings.sort(key=lambda row: row["_crossing_rank"])
    for row in crossings:
        row.pop("_crossing_rank", None)
        row["_importance"] = 1.0
    for row in crossings[CONDITION_CROSSING_LIMIT:]:
        rejected.append(_rejection(row.get("id"), "condition_crossing", "condition_crossing_limit"))
    return crossings[:CONDITION_CROSSING_LIMIT] + basis


def _condition_crossing_stem(slot, check, evidence, language, ticker=None):
    """The engine's fallback stem. Deliberately the flattest possible sentence:
    it states the criterion, what came back, and asks. The two-sided version is
    the agent's to author against `question_opportunity` — this one exists so an
    unauthored surface is still answerable, never so it reads well.

    A thesis-linked condition leads with the thesis it guards (#416 C2). The
    fallback carries it too, and not only the authored surface: a host that
    could not bind the private surface is exactly where an adjudication would
    otherwise arrive with no idea what it is about."""
    criterion = slot.get("criterion") or ""
    all_copy = card_renderer.load_copy(language)
    copy = all_copy.get("condition_crossing") or {}
    key = "event" if (slot.get("kind") or "numeric") == "event" else (
        "near_line" if check.get("engine_verdict") == "near_line" else "met")
    template = copy.get(key) or "{criterion} — {evidence}"
    stem = card_renderer._format_copy(template, criterion=criterion, evidence=evidence)
    guard = card_renderer.thesis_guard_sentence({"thesis_link": {"ticker": ticker}}, all_copy)
    return f"{guard} {stem}" if (guard and stem) else stem


def _condition_basis_stem(slot, note, language):
    copy = card_renderer.load_copy(language).get("condition_basis") or {}
    template = copy.get("stem") or "{criterion} — {note}"
    return card_renderer._format_copy(template, criterion=slot.get("criterion") or "", note=note)


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


QUOTE_CLIP = 80  # word-safe character budget for a replayed thesis quote in a stem


def _clip_quote(text):
    """Collapse whitespace and clip a quote near QUOTE_CLIP at a word boundary.

    Spaced scripts back off to the last complete word; CJK text has no spaces
    and keeps the raw character budget. A clipped quote always ends with an
    ellipsis so it never reads as the user's complete sentence.
    """
    text = " ".join(str(text or "").split())
    if len(text) <= QUOTE_CLIP:
        return text
    clipped = text[:QUOTE_CLIP]
    head = clipped.rpartition(" ")[0]
    if head:
        clipped = head
    return clipped.rstrip() + "…"


def _thesis_recall(prior, language, frame):
    """One lead sentence replaying the user's own recorded thesis (#226).

    Same voice contract as `_due_question`: only text the user confirmed reads
    as "you said"; an inferred thesis that was never confirmed stays a guess
    (first-review contract: never present it as user-confirmed). The quote is
    the stored `why` verbatim (clipped, never paraphrased) and the date uses
    the same event-date resolution the thesis fold orders by. A missing or
    corrupt record returns None so the caller keeps today's plain stem.

    frame "add" returns a lowercase clause the add stem prefixes with its
    ticker; frame "entry" returns a standalone sentence for the exit capture.
    """
    if not isinstance(prior, dict):
        return None
    quote = _clip_quote(prior.get("why"))
    if not quote:
        return None
    date = thesis._event_date(prior)
    guessed = prior.get("maturity") == "inferred"
    if str(language).lower().startswith("en"):
        quoted = f'"{quote}"'
        if frame == "entry":
            lead = f"At entry on {date}" if date else "At entry"
            return (f"{lead} I guessed your thesis was {quoted}." if guessed
                    else f"{lead} you said {quoted}.")
        if guessed:
            return (f"on {date} I guessed your thesis was {quoted}." if date
                    else f"earlier I guessed your thesis was {quoted}.")
        return (f"on {date} you said {quoted}." if date
                else f"earlier you said {quoted}.")
    quoted = f"『{quote}』"
    if frame == "entry":
        if guessed:
            return (f"進場時（{date}）我猜你的論點是{quoted}。" if date
                    else f"進場時我猜你的論點是{quoted}。")
        return (f"你進場時（{date}）說的是{quoted}。" if date
                else f"你進場時說的是{quoted}。")
    if guessed:
        return (f"我在 {date} 猜你的論點是{quoted}。" if date
                else f"我先前猜你的論點是{quoted}。")
    return (f"你在 {date} 說過{quoted}。" if date
            else f"你先前說過{quoted}。")


def _asked_because(basis, language):
    """Localized display reason a question was picked (#226, former option C).

    Only vetted display strings leave the engine; the raw basis key remains an
    internal sort detail that `_question_queue` strips with `_importance`.
    """
    table = (card_renderer.load_copy(language).get("asked_because") or {})
    return table.get(basis) or None


def _exit_question(item, language, card=None, prior=None):
    ticker = item.get("ticker") or "position"
    kind = item.get("kind") or "full"
    notional = revisit._notional(item)
    amount = _format_notional(notional, item.get("currency"))
    # #226: replay the entry thesis inside the stem. Without a prior thesis the
    # joined parts stay byte-identical to the historical plain stem.
    recall = _thesis_recall(prior, language, "entry")
    if str(language).lower().startswith("en"):
        action = "fully exited" if kind == "full" else "substantially reduced"
        base = f"{ticker} was {action} on {item.get('exit_date')} for about {amount}."
        ask = "What mainly drove that decision?"
        question = " ".join(part for part in (base, recall, ask) if part)
    else:
        action = "全部出清" if kind == "full" else "大幅減倉"
        base = f"{ticker} 在 {item.get('exit_date')} {action}，出場金額約 {amount}。"
        ask = "當時主要是什麼理由？"
        question = "".join(part for part in (base, recall, ask) if part)
    digest = hashlib.sha256(str(item.get("revisit_id")).encode("utf-8")).hexdigest()[:12]
    return {
        "id": f"exit_{digest}", "kind": "revisit", "ticker": ticker,
        "cycle_id": item.get("cycle_id"), "required": True, "question": question,
        "options": _exit_options(language, kind), "revisit_id": item.get("revisit_id"),
        "exit_kind": kind, "exit_date": item.get("exit_date"),
        "exit_price": item.get("exit_price"), "shares_sold": item.get("shares_sold"),
        "shares_before": item.get("shares_before"), "currency": item.get("currency") or "USD",
        "exit_notional": notional,
        "asked_because": _asked_because("exit_notional", language),
        "_importance": _exit_importance(item, card), "_tie": 0,
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


def _initial_thesis_id(cycle_id):
    return "initial_thesis_" + hashlib.sha256(str(cycle_id).encode("utf-8")).hexdigest()[:12]


def _initial_thesis_question(ticker, pos, cost, card, state, language):
    """One first-review entry-thesis capture (#291) grounded in ticker + cost.

    The stem cites the engine-owned cost basis (the deterministic per-position
    magnitude the engine stores; live-price weights are not persisted). Both the
    stem number and the stored `cost_basis` come from the same value so the card
    context and the recorded event cannot drift.
    """
    cycle_id = pos.get("cycle_id")
    currency = str(pos.get("currency") or "USD")
    amount = _format_notional(cost, currency)
    importance, basis = _ticker_importance(card, state, ticker)
    because = _asked_because(basis, language)
    if str(language).lower().startswith("en"):
        stem = (f"You are holding {ticker} at a cost basis of about {amount}. "
                "When you first entered this position, what was your thesis?")
        if because:
            stem += f" (Asked because {because}.)"
    else:
        stem = f"你持有 {ticker}，成本約 {amount}。當初第一次進場時，你的論點是什麼？"
        if because:
            stem += f"（問這題是因為{because}）"
    row = {
        "id": _initial_thesis_id(cycle_id), "kind": "initial_thesis", "ticker": ticker,
        "cycle_id": cycle_id, "required": True, "question": stem,
        "options": _initial_thesis_options(language),
        "cost_basis": cost, "currency": currency,
        "_importance": importance, "_importance_basis": basis, "_tie": 1,
    }
    if because:
        row["asked_because"] = because
    row["question_opportunity"] = question_surface.build_opportunity(row, language)
    return row


CAPTURE_LIMIT = 2  # at most two exit-reason captures per session (c6850f0 contract)


def _rejection(id_, kind, reason, cycle_id=None):
    """One question_selection.rejected row with a uniform shape (#291).

    `id` is the question id whenever the candidate became a real (if unqueued)
    question; `cycle_id` is the stable join key present whenever a cycle is
    known, so a QA tool always has one reliable key to cross-reference.
    """
    return {"id": id_, "kind": kind, "cycle_id": cycle_id, "reason": reason}


def _question_queue(card, state, active, previous_state, language, recent_exits=None, thesis_states=None,
                    due_revisits=None, problem_stats=None, rule_history=None, horizon_markers=None,
                    route=None, missing_thesis_positions=None, tier=None,
                    condition_questions=None):
    """Return (queue, selection_report). The report states, plan-internally, how
    the route's density band was filled: the eligible/selected counts, why the
    queue fell short of the route minimum, and every candidate rejected with its
    reason (#291). It is QA/agent-facing and never rendered on the card."""
    policy = QUESTION_POLICY.get(route) or QUESTION_POLICY["weekly_review"]
    # #306: a structural/empty first review is an opening structural check, not a
    # behavioral interrogation — suppress the question band entirely (0 required
    # questions) so a thin first file never triggers the 3–5 question string.
    # Scoped to first_review by design: a returning weekly review keeps its
    # perishable revisit / exit / due-checkpoint questions no matter how thin the
    # new activity is (there the tier is advisory, never a suppressor).
    if route == "first_review" and tier in ("structural", "empty"):
        policy = {"min": 0, "max": 0}
    report = {"route": route, "min": policy["min"], "max": policy["max"],
              "eligible": 0, "selected": 0, "shortfall_reason": None, "rejected": []}
    rejected = report["rejected"]
    # A position snapshot can establish structure and thesis baselines, but it
    # contains no action history.  Do not turn the generic fallback into a
    # fabricated motive question, and do not replay exit/problem questions from
    # an unrelated older ledger into this opening portfolio check.
    if route == "snapshot_review":
        return [], report
    positions = _active_positions(state)
    by_ticker = {ticker: row for ticker, row in positions.items()}
    del previous_state  # retained in the call contract for older adapters
    # #416: no longer read in here — its one use was stamping a per-question
    # field on queue rows that no flow read, now removed. state_snapshot's
    # plural roster is the surface flows actually consume, so the parameter
    # stays for call-contract compatibility with existing positional callers.
    del horizon_markers
    thesis_states = thesis_states or active
    candidates = []
    # Exit-reason capture is the only perishable question: its 14-day window
    # cannot be backfilled, while a skipped due checkpoint or an unanswered add
    # legitimately returns next review. Perishable questions therefore outrank
    # everything regardless of notional — but take at most CAPTURE_LIMIT slots
    # so one busy week cannot turn the review into an exit interrogation.
    for index, item in enumerate(recent_exits or []):
        # #226: the stem itself replays the entry thesis for this cycle, so the
        # agent never has to resolve the attached IDs from disk.
        prior = thesis_states.get(item.get("cycle_id")) or {}
        question = _exit_question(item, language, card, prior)
        if index >= CAPTURE_LIMIT:
            rejected.append(_rejection(question["id"], "revisit", "capture_limit",
                                       cycle_id=question.get("cycle_id")))
            continue
        question["prior_thesis_id"] = prior.get("thesis_id")
        question["prior_event_id"] = prior.get("last_event_id") or prior.get("event_id")
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
        # The add question id is a pure function of the cursor key, so derive it
        # once up front — the two dedup rejections below then carry the same
        # question id (plus cycle_id) that the emitted row would have used.
        cursor_key = decision_cursor or f"{cycle_id}|legacy|{index}"
        add_id = "add_" + hashlib.sha256(cursor_key.encode("utf-8")).hexdigest()[:12]
        if old and decision_cursor and old.get("decision_cursor") == decision_cursor:
            rejected.append(_rejection(add_id, "add_thesis", "already_captured", cycle_id=cycle_id))
            continue
        if old and not decision_cursor and old.get("maturity") == "testable":
            rejected.append(_rejection(add_id, "add_thesis", "already_captured", cycle_id=cycle_id))
            continue
        importance, basis = _ticker_importance(card, state, ticker)
        # #226: quote the cycle's own recorded thesis in the stem and say why
        # this question was picked. Without a prior thesis (or a mapped basis)
        # each part degrades independently to today's plain sentence.
        recall = _thesis_recall(old, language, "add")
        because = _asked_because(basis, language)
        if str(language).lower().startswith("en"):
            question = (f"For {ticker}: {recall} Was the add based on new evidence, "
                        "a pre-planned tranche, a valuation change, or only the lower price?"
                        if recall else
                        f"For {ticker}, was the add based on new evidence, a pre-planned tranche, "
                        "a valuation change, or only the lower price?")
            if because:
                question += f" (Asked because {because}.)"
        else:
            tail = (item.get("question") or
                    "這次加碼，是新證據、事先分批、估值改變，還是只有價格下跌？")
            question = (f"{ticker} {recall}{tail}" if recall
                        else (item.get("question") or f"{ticker} {tail}"))
            if because:
                question += f"（問這題是因為{because}）"
        row = {
            "id": add_id, "kind": "add_thesis", "ticker": ticker,
            "cycle_id": cycle_id, "required": True, "question": question,
            "options": _add_options(language),
            "prior_thesis_id": (old or {}).get("thesis_id"),
            "prior_event_id": (old or {}).get("last_event_id") or (old or {}).get("event_id"),
            "decision_cursor": decision_cursor,
            "_importance": importance, "_importance_basis": basis, "_tie": 1,
        }
        if because:
            row["asked_because"] = because
        prior_context = None
        prior_quote = _clip_quote(old.get("why")) if isinstance(old, dict) else None
        if prior_quote:
            prior_context = {
                "text": prior_quote,
                "voice": "inferred" if old.get("maturity") == "inferred" else "user_confirmed",
            }
        row["question_opportunity"] = question_surface.build_opportunity(
            row, language, prior_thesis=prior_context
        )
        candidates.append(row)
    # #291: first-review entry-thesis capture. Source is the same missing-thesis
    # set the inferred-skeleton path consumes; a cycle already covered by an
    # add_thesis question above needs no second motive question, and a cycle that
    # already carries a real (draft/testable) thesis is a no-duplicate rejection.
    # The over-INITIAL_THESIS_LIMIT rows are held in `initial_overflow`, not
    # rejected yet: a below-min queue prefers these grounded rows over the
    # generic motive backfill (refill loop below).
    initial_overflow = []
    if route == "first_review" and policy["max"]:
        add_covered = {row.get("cycle_id") for row in candidates
                       if row.get("kind") == "add_thesis"}
        missing_cycles = {entry.get("cycle_id") for entry in (missing_thesis_positions or [])
                          if entry.get("cycle_id")}
        initial_candidates = []
        for ticker, pos in sorted(positions.items()):
            cycle_id = pos.get("cycle_id")
            if not cycle_id or cycle_id in add_covered:
                continue
            existing = active.get(cycle_id)
            if existing and existing.get("maturity") in ("testable", "draft"):
                rejected.append(_rejection(_initial_thesis_id(cycle_id), "initial_thesis",
                                           "has_existing_thesis", cycle_id=cycle_id))
                continue
            if cycle_id not in missing_cycles:
                continue  # carries an inferred thesis already; nothing new to capture
            cost = card_renderer._finite_number(pos.get("cost"))
            if cost is None or cost <= 0:
                continue  # cannot ground the stem in a concrete magnitude
            initial_candidates.append(_initial_thesis_question(ticker, pos, cost, card, state, language))
        initial_candidates.sort(key=lambda row: (-float(row.get("_importance") or 0), str(row.get("id"))))
        candidates.extend(initial_candidates[:INITIAL_THESIS_LIMIT])
        initial_overflow = initial_candidates[INITIAL_THESIS_LIMIT:]
    # #412: a crossing or a doubted basis is evidence the user themselves asked
    # to be watched for, so it enters the ranking beside a rule breach rather
    # than behind the motive questions. Built by _build_plan (it needs the
    # store), budgeted there too — this only ranks what arrives.
    candidates.extend(condition_questions or [])
    breach_questions = _rule_breach_questions(problem_stats, rule_history, language)
    candidates.extend(breach_questions[:RULE_BREACH_LIMIT])
    for row in breach_questions[RULE_BREACH_LIMIT:]:
        rejected.append(_rejection(row.get("id"), "rule_breach", "rule_breach_limit",
                                   cycle_id=row.get("cycle_id")))
    # #291 P2-A: a below-min queue earns its extra slots through durable
    # information gain first — refill from the grounded initial-thesis overflow
    # (importance order) before falling back to the generic motive backfill
    # below. The non-shortfall case is unchanged: a queue already at the route
    # min keeps thesis questions capped at two and the leftover overflow is a
    # genuine over-limit trim.
    while len(candidates) < policy["min"] and initial_overflow:
        candidates.append(initial_overflow.pop(0))
    for row in initial_overflow:
        rejected.append(_rejection(row.get("id"), "initial_thesis", "initial_thesis_limit",
                                   cycle_id=row.get("cycle_id")))
    # #303: the aggregated early-exit pattern becomes one answerable motive
    # question when it fired. It is a grounded candidate (real tickers/counts),
    # not a min-only backfill, so it competes for a slot up to the route max —
    # "answerable when the queue has room". It ranks at the bottom of its tier
    # (_importance 0.0) so it never displaces a higher-signal question; when the
    # queue is already full it is trimmed below and the read-only `[?]` panel
    # states the same facts as an explicit observation instead. Counting it
    # here, before the generic headline backfill, keeps a grounded question
    # ahead of an invented one (#291).
    exit_consistency = _exit_consistency_question(card, language)
    if exit_consistency is not None:
        candidates.append(exit_consistency)
    # #291: route-min-aware min-backfill (was `if not candidates:`); weekly
    # min=1 makes it exactly equivalent to the prior behavior, while
    # first-review min=3 lets the motive question backfill when 1-2 grounded
    # candidates exist.
    if len(candidates) < policy["min"]:
        top_hole = (card.get("top_holes") or [{}])[0]
        top = top_hole.get("dim") or state.get("headline_dim")
        # An insufficient or quiet history can trigger no hole and carry
        # headline_dim=None (#227). With no dimension to anchor the motive
        # question to, asking would fabricate one; an empty queue is the same
        # legal contract the snapshot route already returns.
        if top is not None:
            top_label = card_renderer.localized_dimension(top, language)
            question = (f"What mainly drove the behavior behind {top_label}?" if str(language).lower().startswith("en")
                        else f"這次「{top}」背後，主要是事先規劃、情緒反應，還是外部限制？")
            row = {"id": "headline_motive", "kind": "headline_motive", "required": True,
                   "question": question, "options": _generic_options(language),
                   "_importance": 0.0, "_tie": 2}
            # Reuse the same deterministic, engine-owned fact selector as
            # candidate-rule grounding.  The selected top-hole dimension must
            # have both a renderable fact and at least one citable ticker; if
            # either is absent, preserve the dimension-only safe fallback.
            # This changes presentation grounding only—not ranking, choices,
            # calculations, or the canonical answer contract.
            top_dim_id = card_renderer.dimension_id(top)
            grounding_card = card
            top_raw = top_hole.get("raw")
            if isinstance(top_raw, dict):
                grounding_card = dict(card)
                other_dims = [dim for dim in (card.get("dims_raw") or [])
                              if (not isinstance(dim, dict)
                                  or card_renderer.dimension_id(dim.get("dim")) != top_dim_id)]
                # Keep other dimensions for facts such as diversification's
                # sizing weights, while making this exact selected hole the
                # authoritative row for its own dimension.
                grounding_card["dims_raw"] = other_dims + [top_raw]
            top_facts = card_renderer.rule_grounding_facts(grounding_card, top_dim_id)
            grounding = card_renderer.localized_rule_grounding(
                top, language, grounding_card
            )
            tickers = (top_facts or {}).get("tickers") or []
            if (top_hole.get("dim") is not None and grounding and tickers
                    and isinstance(tickers[0], str) and tickers[0]):
                row["ticker"] = tickers[0]
                row["asked_because"] = grounding
                row["question"] = f"{grounding} {question}"
            row["question_opportunity"] = question_surface.build_opportunity(
                row, language,
                headline_dimension={"id": top, "label": top_label},
            )
            candidates.append(row)
    # Priority tiers are semantic, then amount/rank resolves within a tier:
    # perishable exit capture -> unqualified chosen-rule breach -> due/add motive.
    candidates.sort(key=lambda row: (int(row.get("_priority", 2)),
                                     -float(row.get("_importance") or 0),
                                     int(row.get("_tie") or 0), str(row.get("id"))))
    # `eligible` counts everything that survived to the sort; the over-max rows
    # below are then also recorded in `rejected`, so `eligible + len(rejected)`
    # double-counts them. Each field is individually correct — do not sum them.
    report["eligible"] = len(candidates)
    queue = candidates[:policy["max"]]
    for row in candidates[policy["max"]:]:
        rejected.append(_rejection(row.get("id"), row.get("kind"), "over_max_capacity",
                                   cycle_id=row.get("cycle_id")))
    report["selected"] = len(queue)
    if len(queue) < policy["min"]:
        report["shortfall_reason"] = "insufficient_eligible_candidates"
    for row in queue:
        row.pop("_importance", None)
        row.pop("_importance_basis", None)
        row.pop("_tie", None)
        row.pop("_priority", None)
    return queue, report


def _candidate_rules(card, state, language):
    candidates = []
    seen = set()
    source = list(card.get("candidate_rules") or [])
    for hole in card.get("top_holes") or []:
        source.append({"dim": hole.get("dim"), "rule": hole.get("lens_rule")})
    metrics = state.get("metrics") or {}
    cap_override = (state or {}).get("max_position_pct")  # #324:sizing 規矩文案帶用戶自訂上限(engine 已回填 state)
    for row in source:
        dim = row.get("dim") or row.get("kind")
        dim_id = card_renderer.dimension_id(dim)
        metric = DIM_METRIC.get(dim_id)
        if not dim or dim in seen or metric not in metrics:
            continue
        rule = card_renderer.localized_rule(dim, language, cap=cap_override) or row.get("rule")
        if not rule:
            continue
        seen.add(dim)
        candidate = {"id": f"candidate_{len(candidates)}", "dim": dim_id, "rule": rule,
                     "metric_key": metric, "goal": "down"}
        # #248: engine-owned grounding ties the reusable rule template to this
        # period's actual positions (tickers + behavior fact). Omitted when the
        # dimension has nothing citable; the canonical rule text tracked in
        # rules.jsonl stays generic either way.
        grounding = card_renderer.localized_rule_grounding(dim, language, card)
        if grounding:
            candidate["grounding"] = grounding
        candidates.append(candidate)
        if len(candidates) == 3:
            break
    return candidates


def _severity_rank_key(card, dim_id):
    """The same severity x tier-weight key `trade_recap._rank_holes` uses to
    order the card's headline dimensions (#63 single fact source), looked up
    by canonical `dim_id`.

    Not a new score: `HEADLINE_TIER_W` and each dimension's `severity`/`tier`
    already live on `card['dims_raw']`. Returns None when the dimension has
    no raw entry or no numeric severity -- `_candidate_comparison` must never
    guess a rank for data it cannot see.
    """
    for row in card.get("dims_raw") or []:
        if not isinstance(row, dict) or card_renderer.dimension_id(row.get("dim")) != dim_id:
            continue
        severity = row.get("severity")
        if not isinstance(severity, (int, float)):
            return None
        weight = trade_recap.HEADLINE_TIER_W.get(row.get("tier", 2), 0.7)
        return float(severity) * weight
    return None


def _candidate_comparison(candidates, card, language):
    """#302(c): one engine-generated sentence explaining why the candidates
    other than the top-ranked one rank lower this period.

    Derived strictly from the existing severity x tier-weight ranking (the
    same key `_rank_holes` uses to pick `top_holes`) -- no new score is
    invented here. Consumed only by `card_plan.candidate_comparison` /
    `cmd_preview`'s echo of it, i.e. the interaction layer where the agent
    presents the candidate-rule choice (`references/interaction-delivery.md`);
    `card_renderer` never reads this field, so it cannot reach the rendered
    card.

    Degrades to None (no sentence -- never an empty or dangling one) whenever
    an honest ranking claim cannot be made: fewer than two candidates, a
    candidate whose severity cannot be located in `dims_raw`, or a tie at the
    top. The sentence explains *ranking* only; it never claims the top
    candidate is the objectively right rule for the user.
    """
    if len(candidates) < 2:
        return None
    ranked = []
    for candidate in candidates:
        key = _severity_rank_key(card, candidate.get("dim"))
        if key is None:
            return None  # can't compare fairly when any candidate's rank is unknown
        ranked.append((key, candidate))
    ranked.sort(key=lambda pair: -pair[0])
    top_key, top = ranked[0]
    others = ranked[1:]
    if top_key <= others[0][0]:
        return None  # tie at the top: no honest "ranked lower" claim to make
    top_label = card_renderer.localized_dimension(top.get("dim"), language)
    other_labels = [card_renderer.localized_dimension(c.get("dim"), language) for _, c in others]
    comparison = (card_renderer.load_copy(language).get("candidate_comparison") or {})
    others_text = (comparison.get("joiner") or ", ").join(other_labels)
    return (comparison.get("line") or "").format(
        top=top_label, others=others_text) or None


def _problem_snapshot(root, state):
    """Fold the problem book and rules into review-ready stats.

    Offline and read-only: prepare must be able to show trends and rule verdicts
    without mutating the book (appending happens at finalize via projections).
    Assembly lives in problems.snapshot so the CLI and this path cannot drift.

    #292: also feeds this period's not-yet-appended draft problem_events (plus
    its date_end) so `rules_check[*].draft_breach` can flag an in-progress
    breach of a just-committed rule — still read-only, since draft_events are
    only compared, never written to problems.jsonl here.
    """
    payload = problems.snapshot(os.path.join(root, "problems.jsonl"),
                                os.path.join(root, "rules.jsonl"),
                                today=_review_date(state).isoformat(), span_aware=True,
                                draft_events=state.get("problem_events"),
                                draft_week=state.get("date_end"),
                                muted_ids=_muted_rule_ids(root))
    if not payload["events_n"] and not payload["marks_n"]:
        return None
    return payload


def _horizon_markers_all(state, thesis_states, active_cycle_ids, recent_exits):
    """Join stored theses with engine-owned position/exit dates and rank mirrors.
    Every triggered marker, ranked but NOT truncated.

    Reductions remain active positions. Only a recent full exit receives an
    `exit_date`; otherwise horizon.scan would silently turn a reduction into a
    closed thesis. Ranking uses position cost or exit notional and is fixed here,
    never invented by the renderer.

    `_horizon_markers` (below) is this function's HORIZON_MARKER_LIMIT-bounded
    slice for the card's attention budget. #446 cut 1's verdict rows are built
    from THIS untruncated list, never from that slice: the cap exists to keep
    what the agent is asked about small, not to decide which periods get a
    durable record, and writing from the truncated slice would repeat #444's
    round 3-4 defect verbatim -- nothing downstream may be scoped to a display
    cap's slice.
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
    return markers


def _horizon_markers(state, thesis_states, active_cycle_ids, recent_exits):
    """`_horizon_markers_all`'s display-bounded slice: at most
    HORIZON_MARKER_LIMIT entries, for the card's attention budget and the
    question queue. See that function for the join/ranking rules."""
    return _horizon_markers_all(state, thesis_states, active_cycle_ids, recent_exits)[:HORIZON_MARKER_LIMIT]


def _horizon_verdict_rows(plan, session_id):
    """Behavior verdict rows this review's horizon judgments produce (#446 cut
    1), built from `_horizon_markers_all`'s UNTRUNCATED result -- never the
    display-capped `state_snapshot.horizon_markers` slice `_horizon_markers`
    returns.

    Every ingredient here is read from what `_build_plan` already stamped into
    the plan (`engine_state`, `state_snapshot.thesis_states`,
    `state_snapshot.recent_exits`) -- never re-derived from disk or assembled
    a second, differently-sourced way (development-guide.md section 7).
    `_active_positions` is a pure accessor of the stamped `engine_state`, so
    `active_cycle_ids` here is exactly what `_build_plan` itself used to build
    the display slice, not an independent reconstruction that could disagree
    with it on an edge case.

    Skipped entirely on `snapshot_review`, the same route `_horizon_markers`
    itself is skipped for: a position snapshot carries no review history for a
    said-vs-done judgment to compare against.
    """
    if plan.get("route") == "snapshot_review":
        return []
    state = plan.get("engine_state") or {}
    date_end = state.get("date_end")
    if not date_end:
        return []
    snapshot = plan.get("state_snapshot") or {}
    thesis_states = snapshot.get("thesis_states") or []
    by_cycle = {row.get("cycle_id"): row for row in thesis_states if row.get("cycle_id")}
    cycle_ids = [row.get("cycle_id") for row in _active_positions(state).values()
                if row.get("cycle_id")]
    recent_exits = snapshot.get("recent_exits") or []
    markers = _horizon_markers_all(state, thesis_states, cycle_ids, recent_exits)
    exit_dates = {item.get("cycle_id"): item.get("exit_date") for item in recent_exits
                 if item.get("kind") == "full"}
    rows = []
    for marker in markers:
        cycle_id = marker.get("cycle_id")
        prior = by_cycle.get(cycle_id) or {}
        window_to = exit_dates.get(cycle_id) if marker.get("exited") else date_end
        row = verdicts.build_horizon_verdict(
            marker, row_id=prior.get("event_id"), session_id=session_id,
            date_end=date_end, window_to=window_to)
        if row is not None:
            rows.append(row)
    return rows


def _missing_thesis_entry(ticker, position):
    """One uncovered-cycle row: the join keys plus the engine-owned provenance
    the agent needs to ground an inferred thesis without reading engine_state.
    origin is forwarded only when the ingestion path recorded it — the engine
    must not fabricate provenance it does not own."""
    entry = {"ticker": ticker, "cycle_id": position.get("cycle_id")}
    if position.get("origin"):
        entry["origin"] = position["origin"]
    return entry


def _authoring_contract(route):
    """Surface the artifact-authoring contract in the Review Plan so the agent
    self-checks before submitting instead of rediscovering field rules from
    engine source at runtime (#251).

    Every vocabulary here derives from the constant validation enforces
    (thesis.MATURITY_VALUES, thesis.INFERENCE_ENUMS, card_renderer.ALLOWED_NARRATIVE);
    a contract test pins the equivalence so this cannot drift into a second
    source of truth.
    """
    contract = {
        "thesis_updates": {
            "required_from_agent": ["cycle_id", "why", "exit_trigger"],
            "engine_prefilled_for_missing_cycles": {
                "ticker": "from missing_thesis_positions",
                "maturity": "inferred",
            },
            "optional_fields": ["horizon", "stop", "target_size", "driver",
                                "source_type", "source_name", "source_confidence",
                                "emotion", "emotion_inferred",
                                "confidence", "confidence_inferred"],
            "maturity_values": sorted(thesis.MATURITY_VALUES),
            "horizon_values": "card_plan.horizon_ids, or null",
            "inference_enums": {key: sorted(values)
                                for key, values in thesis.INFERENCE_ENUMS.items()},
            "engine_owned_identity": ["thesis_id", "event_id", "revises", "decision_cursor"],
        },
        "narrative": {
            "required": ["headline", "mirror"],
            "allowed_fields": sorted(card_renderer.ALLOWED_NARRATIVE),
            "digit_ban": ("no digits and no spelled-out numeric magnitudes in any field; "
                          "numbers come only from engine artifacts"),
            "honesty_keys": "cover exactly card_plan.required_honesty_keys",
            "unprompted_gaps": ("coverage gaps the engine chose not to ask about "
                                "(e.g. missing_thesis_positions) may appear only as "
                                "neutral coverage facts; do not frame them as the "
                                "user's negligence, and do not make them the central "
                                "judgment of the headline or mirror"),
            "synthesis": ("optional (#345); two to three sentences giving the period's "
                          "single most important cross-section judgment as the card's "
                          "closing block, after the next-step rule; it must connect facts "
                          "that otherwise sit in separate sections into one point of view "
                          "and must never restate a number, tile, or sentence already on "
                          "the card; omit it entirely, rather than a placeholder, when "
                          "nothing rises to that synthesis"),
        },
    }
    if route == "snapshot_review":
        contract["thesis_updates"]["engine_prefilled_for_missing_cycles"]["source_confidence"] = "candidate"
        contract["thesis_updates"]["route_locked"] = {"maturity": "inferred",
                                                      "source_confidence": "candidate"}
    return contract


def _flag_prior_commitment_breach(card, problem_stats, prior_commitment):
    """#292: surface an in-period breach of the rule the user committed to last time.

    `problems.check_rules` only writes `last_breach` once a *finalized* review
    boundary (a committed mark) closes over the breaching event; a breach that
    happens inside the still-open current period never crosses that boundary
    until the *next* review commits its own mark. `_rule_breach_questions`
    reads only `last_breach`, so it silently skips a same-period violation of
    a rule the user just promised to keep — the card ships with zero
    acknowledgment (#292). This reads the already-computed, read-only
    `draft_breach` (problems.py's additive draft-window judgment, keyed off
    this period's not-yet-appended problem_events) and, on a match against the
    rule `prior_commitment` names, appends one honesty_ledger entry. That entry
    flows into `required_honesty_keys` unchanged, so the existing
    narrative.honesty gate in `_draft_bundle` forces the agent to author one
    sentence about it — no new checker, and `last_breach`/`held_streak`/
    `verdict` are never touched.

    problem_key + text is a two-part match because one problem_key can carry
    more than one historical rule line (revisions); `session.PKEY` maps the
    commitment's metric_key the same way `session.py`'s finalize path derives
    a rules.jsonl row's problem_key from that same commitment, so the join is
    exact for the immediately-following review regardless of revision history.

    Returns `card` unchanged when nothing matches. On a match, returns a *new*
    dict with a freshly built honesty_ledger list — mirrors `_gate_current_view`
    (review.py) building a new list and reassigning rather than appending to
    the existing list object in place, so no caller-held reference is mutated.
    """
    if not prior_commitment or not problem_stats:
        return card
    problem_key = session.PKEY.get(prior_commitment.get("metric_key"))
    if not problem_key:
        return card
    rule_text = prior_commitment.get("rule")
    if not rule_text:
        return card
    for rule in problem_stats.get("rules_check") or []:
        if (rule.get("problem_key") != problem_key
                or rule.get("text") != rule_text
                or not rule.get("draft_breach")):
            continue
        ledger = list(card.get("honesty_ledger") or [])
        ledger.append({
            "key": "prior_commitment_breach",
            "status": "draft",
            "data": {"problem_key": problem_key, "week": rule["draft_breach"].get("week")},
        })
        return {**card, "honesty_ledger": ledger}
    return card


def _price_feed_status(card):
    """Agent-visible price availability for this run (#289).

    ``provenance`` records where the prices came from; ``request`` is the
    machine-readable manifest of what is still unpriced, present only when
    coverage is incomplete. A degraded run stays visible instead of quietly
    dropping the portfolio-level return.
    """
    provenance = (card or {}).get("price_provenance")
    request = (card or {}).get("price_request")
    if not provenance and not request:
        return None
    status = {"provenance": provenance}
    if request:
        status["request"] = request
        # Held instruments and benchmarks fail differently: unpriced holdings
        # remove P&L itself, unpriced benchmarks only remove the vs-market
        # segment. Saying which one is missing keeps the agent from treating an
        # optional enrichment as a blocker, or the reverse.
        blocking = bool(request.get("tickers"))
        scope = ("the instruments in input.price_feed.request.tickers — without them there is no "
                 "unrealized P&L or portfolio-level return" if blocking else
                 "the benchmark symbols in input.price_feed.request.benchmarks — holdings are "
                 "priced, but the benchmark comparison stays unavailable without them")
        status["next_action"] = (
            f"price coverage is incomplete for {scope}. Look those closes up in a recognized "
            "market-data source, transcribe them into the envelope documented in "
            "references/price-feed.md, and rerun prepare with --prices <path>. Never invent a "
            "price, and never read a missing price as a delisting or as a zero return")
    return status


def _build_plan(card, state, engine_meta, root, paths, route, language, fingerprint, nonce, persist,
                recent_exits=None, ledger_ingest=None,
                due_revisits=None, exit_backlog=None, problem_stats=None,
                submitted_condition_checks=None):
    positions = _active_positions(state)
    cycle_ids = [row.get("cycle_id") for row in positions.values() if row.get("cycle_id")]
    session_id = ledger.session_id_from_state(state, f"{nonce}|{route}|{language}")
    thesis_rows, decision_rows = _thesis_event_history(root)
    thesis_states = thesis.reconstruct_states(thesis_rows, decision_rows, cycle_ids)
    cycle_relinks = []
    if route != "snapshot_review":
        cycle_relinks = thesis.build_incomplete_snapshot_cycle_relinks(
            thesis_states, positions, session_id, state.get("date_end")
        )
        if cycle_relinks:
            thesis_states = thesis.reconstruct_states(
                thesis_rows + cycle_relinks, decision_rows, cycle_ids
            )
    active_rows = [row for row in thesis_states
                   if row.get("cycle_id") in set(cycle_ids) and row.get("position_status") != "closed"]
    active = {row.get("cycle_id"): row for row in active_rows}
    by_cycle = {row.get("cycle_id"): row for row in thesis_states}
    horizon_markers = ([] if route == "snapshot_review" else
                       _horizon_markers(state, thesis_states, cycle_ids, recent_exits))
    rule_history = {} if route == "snapshot_review" else _rule_breach_history(root)
    headline_motive_events = ([] if route == "snapshot_review" else
                              _headline_motive_history(root))
    missing = [_missing_thesis_entry(ticker, row)
               for ticker, row in sorted(positions.items()) if row.get("cycle_id") not in active]
    previous = _previous_state(root)
    completed_reviews = _completed_review_count(root, exclude_session_id=session_id)
    cadence = _cadence(route, state.get("date_end"), previous)
    if route != "snapshot_review":
        # #284: freeze the monthly vs-market decision into the card artifact
        # (precedent: _apply_display_currency). Snapshot cards keep their
        # existing route-level suppression — no second gate layered on top.
        card = {**card, "vs_market_gate": _vs_market_gate(
            root, state.get("date_end"), exclude_session_id=session_id)}
    # #292: read-only, additive check against this period's draft problem_events —
    # must run after problem_stats/previous are both available and before the
    # honesty_ledger is read into required_honesty_keys below, so a match cannot
    # be silently dropped from the agent's authoring gate.
    card = _flag_prior_commitment_breach(card, problem_stats, (previous or {}).get("commitment"))
    required_honesty_keys = [x.get("key") for x in card.get("honesty_ledger") or []]
    if card_renderer.vs_market_suppressed(card):
        # The ledger keeps recording what the engine triggered; the agent is
        # only asked to author sentences whose host lines render this month.
        required_honesty_keys = [key for key in required_honesty_keys
                                 if key not in card_renderer.VS_MARKET_HONESTY_KEYS]
    review_tier = _review_tier(state)
    # Carry the engine decision inside engine_state too, so the deterministic
    # renderer can frame a thin first file as an opening structural check without
    # re-deriving thresholds (#306). state_snapshot keeps the agent-facing copy.
    state["review_tier"] = review_tier
    flow_path = f"flows/{route.replace('_', '-')}.md"
    if route == "first_review" and review_tier["tier"] in ("structural", "empty"):
        # #306: a thin first file is an opening structural check, not a full
        # behavioral review — send the agent to the structural flow.
        flow_path = "flows/first-review-structural.md"
    # #412/#434: what this review asks the agent to look up, bounded, plus the
    # results the agent already submitted (the second `prepare` pass). Snapshot
    # reviews are excluded for the same reason they queue no questions: a
    # position snapshot carries no review history to reconcile against.
    # #416 C2: which position cycles a thesis-linked condition is still standing
    # on, derived once here and read everywhere else from what the plan stamps
    # (`thesis_link` on a live entry, `condition_slots_retired` on a closed one).
    thesis_cycles = _thesis_cycle_index(positions, thesis_rows, cycle_relinks)
    condition_due, condition_summary, thesis_links, condition_retired = (
        ([], None, {}, []) if route == "snapshot_review"
        else _condition_due(root, thesis_cycles, (previous or {}).get("date_end")))
    # #317/#429: reconcile any `consider` consultation still open against what
    # the local ledger actually shows happened. `_ledger_trade_events` reads
    # only real, dated trade events — never `_rows_from_ledger`'s synthesized
    # anchor rows, which exist for FIFO/cost-basis pricing `consider` needs
    # and fail closed on a position with no cost basis. A snapshot anchor
    # legitimately carries no cost basis at all, and `prepare` must never fail
    # over an unrelated position's pricing gap. Every route computes this —
    # unlike the condition-slot lookup queue, it needs no review history or
    # thesis cycle, only the ledger and date_end, both of which every route
    # already has.
    ledger_events, _skipped_ledger_lines = ledger.load_ledger(os.path.join(root, "ledger.jsonl"))
    consultation_reconciliation = _consultation_reconciliation(
        root, _ledger_trade_events(ledger_events), state.get("date_end"))
    condition_checks, condition_questions, condition_deferred = [], [], []
    if route != "snapshot_review" and submitted_condition_checks:
        condition_slots, prior_checks, _unreadable = _condition_store(root)
        condition_checks = _build_condition_checks(
            condition_slots, prior_checks, submitted_condition_checks, session_id,
            state.get("date_end"))
        condition_questions = _condition_questions(
            condition_checks, condition_slots, session_id, language, condition_deferred,
            thesis_links)
    question_queue, question_selection = _question_queue(
        card, state, active, previous, language, recent_exits, by_cycle, due_revisits,
        problem_stats, rule_history, horizon_markers, route=route,
        missing_thesis_positions=missing, tier=review_tier["tier"],
        condition_questions=condition_questions)
    question_selection["rejected"].extend(condition_deferred)
    candidate_rules = _candidate_rules(card, state, language)
    plan = {
        "schema_version": 2,
        "engine_version": _engine_version(),
        "session_id": session_id,
        "status": "awaiting_answers",
        "route": route,
        "flow_path": flow_path,
        "language": card_renderer.resolve_language(language),
        "persist": bool(persist),
        "state_root": root,
        "input": {"paths": [os.path.abspath(p) for p in paths],
                  "kind": "positions_snapshot" if route == "snapshot_review" else "trades_csv",
                  "fingerprint": fingerprint, "engine_meta": engine_meta,
                  "ledger_ingest": ledger_ingest,
                  "price_feed": _price_feed_status(card)},
        "state_snapshot": {"prior_commitment": _with_condition_line(
                               (previous or {}).get("commitment")),
                           # #412/#434: the conditions the engine cannot compute,
                           # as a bounded lookup request rather than the whole
                           # store. Each entry is the line's live row plus what
                           # its last check found; the summary beside it states
                           # the total, so nobody can mistake this list for the
                           # complete record. Look each one up per its frozen
                           # query and submit the results — the engine decides the
                           # verdict, never the prose (references/condition-slots.md).
                           "condition_slots_due": condition_due,
                           "condition_slots_summary": condition_summary,
                           # #416 C2: the thesis conditions that stopped being
                           # checked *this* period, because their position was
                           # fully exited. An event, so the card says it once;
                           # empty on every later review, which is what keeps
                           # a retirement from becoming permanent noise.
                           "condition_slots_retired": condition_retired,
                           # The results already submitted on this pass, validated
                           # into durable rows. Present only on the second prepare;
                           # a crossing question below was posed against these.
                           **({"condition_checks": condition_checks}
                              if condition_checks else {}),
                           "review_progress": {
                               "completed_reviews_before_start": completed_reviews,
                               "returning": completed_reviews > 0,
                           },
                           "cadence": cadence,
                           "review_tier": review_tier,
                           "active_theses": active_rows,
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
                           "headline_motive_events": headline_motive_events,
                           "market_context": state.get("market_context"),
                           "horizon_markers": horizon_markers},
        "question_queue": question_queue,
        "missing_thesis_positions": missing,
        "authoring_contract": _authoring_contract(route),
        "card_plan": {"candidate_rules": candidate_rules,
                      # #302(c): engine-authored, interaction-layer-only sentence
                      # explaining why the other candidates rank lower; None when
                      # fewer than two candidates or the ranking can't honestly
                      # compare them (see _candidate_comparison).
                      "candidate_comparison": _candidate_comparison(candidate_rules, card, language),
                      "question_policy": {"route": question_selection["route"],
                                          "min": question_selection["min"],
                                          "max": question_selection["max"]},
                      "question_selection": question_selection,
                      "horizon_ids": ["weeks", "quarters", "years"],
                      "required_honesty_keys": required_honesty_keys},
        # #317/#429: a supply-side fact surface, not a card section or a
        # question (issue #440 parks card layout; issue #453 puts the
        # judgment of whether this earns a turn on the agent, not the
        # engine). See schemas/review-plan.schema.json for the declared shape.
        "consultation_reconciliation": consultation_reconciliation,
        "engine_card": card,
        "engine_state": state,
    }
    if cycle_relinks:
        plan["state_snapshot"]["thesis_cycle_relinks"] = cycle_relinks
    return plan


# Fields the agent never reads but that inflate every subsequent turn.  The
# prepare stdout is re-sent by the agent as context on each later turn
# (narrative authoring, preview, finalize, and any retries), so anything the
# flow contract does not read is pure ballast multiplied by the turn count.
# engine_card and the bulk of engine_state are agent-unreadable by rule #1
# (the agent must never compute or alter a number) and are reloaded from the
# on-disk pending bundle by preview/finalize, so dropping them from the stdout
# copy is lossless for the agent.  The only engine_state field the flow reads
# directly is snapshot_reconciliation (SKILL.md, flows/snapshot-review.md,
# references/data-contract.md), which is preserved.
_AGENT_PLAN_DROP = ("engine_card", "engine_state")


def _plan_for_agent(plan):
    """Project the Review Plan down to what the flow contract reads.

    The full plan (with engine_card/engine_state) is still persisted on disk by
    save_pending; this only trims the copy echoed to stdout for the agent.
    """
    projection = {key: value for key, value in plan.items() if key not in _AGENT_PLAN_DROP}
    reconciliation = (plan.get("engine_state") or {}).get("snapshot_reconciliation")
    if reconciliation is not None:
        projection["engine_state"] = {"snapshot_reconciliation": reconciliation}
    return projection


def _pending_for_agent(bundle):
    """Trim a resumed pending bundle without dropping its frozen presentation."""
    projection = dict(bundle)
    if isinstance(projection.get("plan"), dict):
        projection["plan"] = _plan_for_agent(projection["plan"])
    # The resolved presentation is the runtime handoff. The authored candidate
    # remains private canonical state and would only duplicate that copy here.
    projection.pop("question_surfaces", None)
    return projection


def cmd_prepare(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    # Resolve once at the boundary (#389): everything downstream — fingerprint,
    # plan, renderer, display currency — sees only a canonical supported locale.
    # args.language is rewritten so helpers that read the namespace agree.
    language = args.language = card_renderer.resolve_language(args.language)
    route = args.route
    persist = not args.test_drive
    if args.snapshot_json:
        if args.test_drive:
            raise ReviewError("--snapshot-json cannot be combined with --test-drive")
        if route not in ("auto", "snapshot_review"):
            raise ReviewError("--snapshot-json requires --route snapshot_review")
        route = "snapshot_review"
    if args.test_drive:
        route = "test_drive"
        if not args.root:
            root = tempfile.mkdtemp(prefix="fomo-kernel-test-drive-")
    elif route == "auto":
        route = "weekly_review" if _has_history(root) else "first_review"
    if args.snapshot_json and (args.card_json or args.state_json):
        raise ReviewError("--snapshot-json cannot be combined with --card-json or --state-json")
    if args.snapshot_json and args.cash:
        raise ReviewError("--snapshot-json cannot be combined with --cash; include cash in the snapshot envelope")
    if args.snapshot_json and args.paths:
        raise ReviewError("pass the normalized snapshot only through --snapshot-json")
    # #289: validate the supplied price envelope here, before any engine work.
    # A malformed envelope is an input error the agent can fix and retry; it
    # must never reach the engine as half-usable prices.
    supplied_prices = None
    if getattr(args, "prices", None):
        if args.snapshot_json or args.card_json or args.state_json:
            raise ReviewError("--prices applies to transaction-history reviews; a snapshot or "
                              "precomputed artifacts already carry their own valuation basis")
        try:
            supplied_prices = price_feed.load(os.path.abspath(os.path.expanduser(args.prices)))
        except price_feed.PriceFeedError as exc:
            raise ReviewError(f"price feed rejected: {exc}") from exc
    # #412: the results of this period's condition lookups, submitted the same
    # way prices are — the plan publishes what is due, the agent runs each
    # frozen query, and this second pass is what lets a crossing be posed as a
    # question instead of only recorded. Parsed here, before any engine work,
    # so a malformed envelope is an input error the agent can fix and retry.
    submitted_checks = []
    if getattr(args, "condition_checks", None):
        if route == "snapshot_review":
            raise ReviewError("--condition-checks applies to a review with history; a position "
                              "snapshot has no standing conditions to check")
        submitted_checks = _condition_check_envelopes(
            _load_json(args.condition_checks, "condition checks"), "input")
    if route == "snapshot_review" and not args.snapshot_json and not (args.card_json and args.state_json):
        raise ReviewError("snapshot_review requires --snapshot-json")
    paths = ([args.snapshot_json] if args.snapshot_json else
             list(args.paths or ([] if args.card_json else
                                 [str(MOCK_CSV) if args.test_drive else None])))
    if any(p is None for p in paths) or (not paths and not args.card_json):
        raise ReviewError("provide at least one CSV path, --snapshot-json, or use --test-drive")
    # Resolve to absolute paths once: the engine subprocess runs with cwd at the
    # skill directory, so a caller-relative path would otherwise be fingerprinted
    # from one file and processed from another (or crash mid-run).
    paths = [os.path.abspath(os.path.expanduser(p)) for p in paths]
    prepared = None
    if args.snapshot_json:
        try:
            card, state, adapter_meta = snapshot_adapter.prepare(
                paths[0], driver_map=args.driver_map, instrument_map=args.instrument_map
            )
        except (OSError, ValueError, snapshot_adapter.SnapshotError) as exc:
            raise ReviewError(f"snapshot adapter rejected input: {exc}") from exc
        reconciliation = _validate_initial_snapshot_root(root, state.get("snapshot_anchor"))
        if reconciliation is not None:
            card, state = _apply_snapshot_reconciliation(card, state, reconciliation)
        prepared = {"card": card, "state": state}
        if isinstance(adapter_meta, str):
            engine_meta = adapter_meta
        else:
            # The Review Plan already contains the local input path and engine
            # artifacts.  Keep metadata diagnostic rather than duplicating the
            # full private anchor inside a display string.
            safe_meta = {key: adapter_meta.get(key) for key in (
                "source", "input_rows", "positions_n", "merged_rows",
                "valuation_basis", "weights_available", "driver_map", "instrument_map"
            ) if key in adapter_meta}
            engine_meta = session.canonical(safe_meta)
    elif args.card_json or args.state_json:
        if not (args.card_json and args.state_json):
            raise ReviewError("--card-json and --state-json must be provided together")
        card = _load_json(args.card_json, "engine card")
        state = _load_json(args.state_json, "engine state")
        prepared = {"card": card, "state": state}
        engine_meta = "prepared artifacts"
    fingerprint = _fingerprint(paths, language, route, prepared=prepared,
                               nonce=args.session_nonce or "", prices=supplied_prices,
                               cash=getattr(args, "cash", None),
                               condition_checks=submitted_checks)
    existing = _pending_by_fingerprint(root, fingerprint)
    if existing:
        _emit({"status": "resumed", "session_id": existing["session_id"],
               "review_plan": _plan_for_agent(existing),
               "next_action": ("run resume --session-id to reuse any validated question surface; "
                               "then ask question_queue and run preview")})
        return
    if prepared is None:
        card, state, engine_meta = _run_engine(paths, root, args)
    card, state = _apply_display_currency(card, state, _previous_state(root), language)
    ledger_ingest = None
    if persist and route == "snapshot_review" and state.get("snapshot_anchor"):
        if state["snapshot_anchor"].get("is_complete", True) is False:
            ledger_ingest = {"mode": "canonical_only", "kind": "positions_snapshot",
                             "reason": "incomplete_snapshot"}
        else:
            ledger_ingest = {"mode": "finalize_projection", "kind": "positions_snapshot"}
            if isinstance(state.get("snapshot_reconciliation"), dict):
                ledger_ingest["reconciliation"] = state["snapshot_reconciliation"].get("status")
    elif persist and paths:
        ledger_ingest, card, state = _ingest_trades(root, paths, card, state)
    if route == "snapshot_review":
        recent_exits, due_revisits, exit_backlog = [], [], None
        problem_stats = None
    else:
        # #416: _prepare_exit_capture still returns (recent, due, backlog,
        # ingest_meta); the 4th element (revisit-queue enqueue counters) is
        # discarded here — its only sink in state_snapshot was written but
        # never read by any flow, and that sink has been removed.
        recent_exits, due_revisits, exit_backlog, _ = \
            _prepare_exit_capture(root, state, persist)
        problem_stats = _problem_snapshot(root, state) if persist else None
    plan = _build_plan(card, state, engine_meta, root, paths, route, language, fingerprint,
                       args.session_nonce or "", persist,
                       recent_exits=recent_exits, ledger_ingest=ledger_ingest,
                       due_revisits=due_revisits,
                       exit_backlog=exit_backlog, problem_stats=problem_stats,
                       submitted_condition_checks=submitted_checks)
    committed = session.session_dir(root, plan["session_id"])
    if os.path.isdir(committed):
        _emit({"status": "already_committed", "session_id": plan["session_id"], "path": committed})
        return
    session.save_pending(root, plan["session_id"], plan=plan)
    next_action = ("for question_opportunity rows, author a private surface and bind it with "
                   "resume --question-surfaces, or keep the engine question/options fallback; "
                   "then ask every required question, author thesis_updates and prose-only narrative, "
                   "and run preview")
    if not persist:
        # The test drive lives in an isolated root that preview/finalize cannot
        # discover on their own; without this handoff they report "pending session
        # not found" against the default root.
        next_action += f"; test drive is isolated — pass --root {root} to every later command"
    due = ((plan.get("state_snapshot") or {}).get("condition_slots_due") or [])
    if due and not submitted_checks:
        # #412: the same recoverable-gap posture as the price request. A review
        # that never runs the lookups still finalizes — every due slot is
        # recorded as not_checked — but the crossing it might have caught is
        # never asked about, so say what is outstanding at the point the agent
        # decides what to do next.
        next_action = (f"{len(due)} standing condition(s) are due: run each frozen "
                       "state_snapshot.condition_slots_due[].query, transcribe the results into "
                       "the envelope in references/condition-slots.md, and rerun prepare with "
                       "--condition-checks <path>. Never assert a verdict — the engine performs "
                       "the comparison and the user answers an event; otherwise continue: "
                       + next_action)
    price_status = ((plan.get("input") or {}).get("price_feed") or {})
    if price_status.get("request"):
        # #289: a host that cannot retrieve prices is a recoverable input gap,
        # not a dead end. Say so at the point the agent decides what to do next.
        next_action = (price_status["next_action"] + "; otherwise continue: " + next_action)
    _emit({"status": "prepared", "session_id": plan["session_id"],
           "review_plan": _plan_for_agent(plan),
           "next_action": next_action})


def _apply_thesis_skeletons(plan, updates):
    """Merge engine-known defaults into agent updates for uncovered cycles (#251).

    The agent submits only the join key and the qualitative fields; the engine
    fills the mechanical fields it already owns. Explicit agent values are never
    rewritten — a snapshot-route override still reaches (and is rejected by)
    the provenance gates in _validate_thesis_completeness. A ticker that
    contradicts the engine-owned cycle mapping fails closed instead of being
    persisted."""
    missing = {row.get("cycle_id"): row
               for row in plan.get("missing_thesis_positions") or []
               if isinstance(row, dict) and row.get("cycle_id")}
    snapshot_route = plan.get("route") == "snapshot_review"
    merged = []
    for update in updates:
        cycle_id = update.get("cycle_id") if isinstance(update, dict) else None
        if not isinstance(cycle_id, str) or cycle_id not in missing:
            merged.append(update)
            continue
        row = dict(update)
        entry = missing[cycle_id]
        supplied_ticker = str(row.get("ticker") or "").strip()
        if supplied_ticker and supplied_ticker.upper() != entry.get("ticker"):
            raise ReviewError(
                f"thesis update ticker {supplied_ticker!r} does not match engine-owned "
                f"ticker {entry.get('ticker')!r} for cycle: {cycle_id}")
        row["ticker"] = entry.get("ticker")
        if row.get("maturity") is None:
            row["maturity"] = "inferred"
        if snapshot_route and row.get("source_confidence") is None:
            row["source_confidence"] = "candidate"
        merged.append(row)
    return merged


def _validate_thesis_completeness(plan, answers):
    updates = _apply_thesis_skeletons(plan, answers.get("thesis_updates") or [])
    positions = _active_positions(plan.get("engine_state") or {})
    allowed_horizons = (plan.get("card_plan") or {}).get("horizon_ids")
    thesis.validate_thesis_updates(updates, positions, allowed_horizons=allowed_horizons)
    needed = {row.get("cycle_id") for row in plan.get("missing_thesis_positions") or []}
    supplied = {row.get("cycle_id") for row in updates}
    missing = sorted(x for x in needed - supplied if x)
    if missing:
        raise ReviewError("missing inferred thesis updates for cycles: " + ", ".join(missing))
    # #291: a `planned_entry` initial-thesis answer asserts the user entered with
    # an explicit thesis, so a silently-inferred update is not an honest record of
    # it — that cycle must carry a real capture (maturity draft/testable). Every
    # other answer (momentum_follow/external_call/no_clear_thesis/skip) keeps
    # inferred legal, so different answers produce different downstream state.
    answer_choice = {row.get("question_id"): row.get("choice")
                     for row in (answers.get("answers") or []) if isinstance(row, dict)}
    planned_entry_cycles = {q.get("cycle_id") for q in plan.get("question_queue") or []
                            if q.get("kind") == "initial_thesis"
                            and answer_choice.get(q.get("id")) == "planned_entry"}
    update_by_cycle = {row.get("cycle_id"): row for row in updates}
    inferred_planned = sorted(
        cid for cid in planned_entry_cycles
        if cid and (update_by_cycle.get(cid) or {}).get("maturity") == "inferred")
    if inferred_planned:
        raise ReviewError(
            "planned_entry initial-thesis answers require a captured thesis "
            "(maturity draft or testable) for cycles: " + ", ".join(inferred_planned))
    if plan.get("route") == "snapshot_review":
        not_inferred = sorted(
            row.get("cycle_id") for row in updates
            if row.get("cycle_id") in needed and row.get("maturity") != "inferred"
        )
        if not_inferred:
            raise ReviewError(
                "snapshot-origin thesis updates must remain inferred for cycles: "
                + ", ".join(not_inferred)
            )
        non_candidate = sorted(
            row.get("cycle_id") for row in updates
            if row.get("cycle_id") in needed
            and row.get("source_confidence") != "candidate"
        )
        if non_candidate:
            raise ReviewError(
                "snapshot-origin thesis updates require candidate provenance for cycles: "
                + ", ".join(non_candidate)
            )
    return updates


def _split_thesis_conditions(updates):
    """``(clean_updates, [(cycle_id, exit_trigger, envelope)])`` (#416 C2).

    A thesis row may carry the falsifier it just stated as a condition envelope.
    Attaching it to the row rather than to a parallel array is #412 Ruling 1's
    same-exchange precedent: the pairing is structural, so there is no
    cross-reference between two lists that can drift.

    It is lifted **out** of the row before the row becomes a thesis event. A
    condition lives in ``conditions.jsonl`` with its own identity and its own
    check history; a second copy riding along inside ``theses.jsonl`` would be a
    mirror nobody reconciles, which is precisely what #416 ruled against."""
    clean, attached = [], []
    for row in updates:
        if not isinstance(row, dict) or "condition" not in row:
            clean.append(row)
            continue
        envelope = row["condition"]
        clean.append({key: value for key, value in row.items() if key != "condition"})
        if envelope is None:
            continue
        attached.append((row.get("cycle_id"), row.get("exit_trigger"), envelope))
    return clean, attached


def _build_thesis_condition_slots(plan, attached):
    """Every stated falsifier this review turns into a watched condition (#416 C2).

    ``thesis_cycle_id`` comes from the thesis row's own ``cycle_id`` — never
    from the envelope, which could otherwise attach a condition to a thesis the
    user never wrote.

    The criterion must be the ``exit_trigger`` verbatim, the same refusal
    ``_slot_commitment`` makes about ``commitment.rule``. The record stores
    ``criterion`` and the thesis stores ``exit_trigger``; one being a tidied
    paraphrase of the other is exactly what #396 forbids, and here it would also
    mean the condition being watched is not the one the thesis says breaks it."""
    if not attached:
        return []
    session_id = str(plan.get("session_id") or "")
    created = (plan.get("engine_state") or {}).get("date_end")
    rows = []
    for index, (cycle_id, exit_trigger, envelope) in enumerate(attached):
        if not isinstance(envelope, dict):
            raise ReviewError(f"thesis_updates condition for {cycle_id!r} must be an object")
        criterion = str(envelope.get("criterion") or "").strip()
        if criterion != str(exit_trigger or "").strip():
            raise ReviewError(
                f"thesis_updates condition for {cycle_id!r} must carry the thesis's own "
                "exit_trigger as its criterion, verbatim: a watched condition that paraphrases "
                "the falsifier is watching something the user did not say")
        try:
            rows.append(conditions.build_slot(
                envelope,
                slot_id="slot-" + (session_id.split("__")[-1] or "0") + f"-t{index}",
                created=created, session_id=session_id or None,
                thesis_cycle_id=cycle_id))
        except conditions.ConditionError as exc:
            raise ReviewError(f"thesis falsifier condition rejected ({cycle_id}): {exc}") from exc
    return rows


def _assign_thesis_ids(plan, updates):
    date = (plan.get("engine_state") or {}).get("date_end")
    prior_rows = ((plan.get("state_snapshot") or {}).get("thesis_states") or [])
    prior_by_cycle = {row.get("cycle_id"): row for row in prior_rows if row.get("cycle_id")}
    rows = []
    for update in updates:
        row = dict(update)
        # decision_cursor is written only by engine-built thesis_decision events;
        # an agent-supplied entry would poison question dedup on the next review
        # (reconstruct_states stops carrying the engine cursor forward once the
        # row carries the key — even with a null value), so key presence itself
        # fails closed.
        if "decision_cursor" in row:
            raise ReviewError(
                f"thesis update carries engine-owned decision_cursor for cycle: {row.get('cycle_id')}")
        if plan.get("route") == "snapshot_review":
            # Provenance is an engine-owned route fact, not an agent label.
            row["origin"] = "snapshot"
            anchor = (plan.get("engine_state") or {}).get("snapshot_anchor")
            row["cycle_provenance"] = {
                "kind": "snapshot_inference",
                "snapshot_as_of": anchor.get("as_of") if isinstance(anchor, dict) else None,
                "snapshot_complete": (anchor.get("is_complete", True)
                                      if isinstance(anchor, dict) else None),
            }
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


def _build_headline_motive_events(plan, answers, amap=None):
    """Consume a headline-motive answer into one typed canonical event.

    Only the canonical non-skip choice and engine-owned question context are
    persisted.  In particular, ticker/fact grounding is copied from the
    validated question opportunity when present; this function never derives
    or invents either.  A skip remains explicit in ``answers`` and deliberately
    creates no motive event.
    """
    if amap is None:
        amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    for question in plan.get("question_queue") or []:
        if question.get("kind") != "headline_motive":
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        if choice == "skip":
            continue
        offered = {option.get("value") for option in question.get("options") or []}
        if choice not in HEADLINE_MOTIVE_CHOICES or choice not in offered:
            raise ReviewError(f"unsupported headline motive decision: {choice}")
        if answer.get("evidence_delta") is not None:
            raise ReviewError(
                f"{question['id']}: evidence_delta is not valid for a headline motive")
        opportunity = question.get("question_opportunity") or {}
        context = opportunity.get("context") or {}
        event = {
            "event": "headline_motive_decision",
            "schema_version": 1,
            "session_id": plan.get("session_id"),
            "question_id": question.get("id"),
            "decision": choice,
            "context": json.loads(json.dumps(context, ensure_ascii=False, sort_keys=True)),
            "review_date": (plan.get("engine_state") or {}).get("date_end"),
        }
        identity = session.canonical(event)
        event["event_id"] = (
            "headline-motive-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        )
        events.append(event)
    return events


def _build_exit_consistency_events(plan, answers, amap=None):
    """Consume an exit-consistency answer into one typed canonical event (#303).

    Mirrors ``_build_headline_motive_events`` (#299): a non-skip classification
    becomes an append-only typed event carrying only the canonical choice and
    the engine-owned question context (the named instruments / counts). A skip
    stays explicit in ``answers`` and records nothing. The event lands in its
    own ``exit_consistency`` stream — never the headline-motive history — so the
    two motive axes stay separable in durable state.
    """
    if amap is None:
        amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    for question in plan.get("question_queue") or []:
        if question.get("kind") != "exit_consistency":
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        if choice == "skip":
            continue
        offered = {option.get("value") for option in question.get("options") or []}
        if choice not in EXIT_CONSISTENCY_CHOICES or choice not in offered:
            raise ReviewError(f"unsupported exit consistency decision: {choice}")
        if answer.get("evidence_delta") is not None:
            raise ReviewError(
                f"{question['id']}: evidence_delta is not valid for an exit consistency motive")
        opportunity = question.get("question_opportunity") or {}
        context = opportunity.get("context") or {}
        event = {
            "event": "exit_consistency_decision",
            "schema_version": 1,
            "session_id": plan.get("session_id"),
            "question_id": question.get("id"),
            "decision": choice,
            "context": json.loads(json.dumps(context, ensure_ascii=False, sort_keys=True)),
            "review_date": (plan.get("engine_state") or {}).get("date_end"),
        }
        identity = session.canonical(event)
        event["event_id"] = (
            "exit-consistency-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        )
        events.append(event)
    return events


def _build_initial_thesis_events(plan, answers, amap=None):
    """Persist the first-review entry-motive classification as append-only events (#291).

    Non-skip answers become typed events with the question's grounding facts
    (cost basis, currency) and session refs. `skip` records nothing — it is an
    explicit non-classification, not a decision. These rows project to their own
    `initial_theses.jsonl` audit log; they never enter the thesis-reconstruction
    streams, so they cannot corrupt `_thesis_event_history`.
    """
    if amap is None:
        amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=True)
    events = []
    for question in plan.get("question_queue") or []:
        if question.get("kind") != "initial_thesis":
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        offered = {option.get("value") for option in question.get("options") or []}
        if choice not in INITIAL_THESIS_CHOICES or choice not in offered:
            raise ReviewError(f"unsupported initial thesis decision: {choice}")
        note = _clean_note(question["id"], answer, "an initial thesis")
        if choice == "skip":
            continue
        event = {
            "event": "initial_thesis", "schema_version": 1,
            "session_id": plan.get("session_id"), "cycle_id": question.get("cycle_id"),
            "ticker": question.get("ticker"), "choice": choice, "note": note,
            "cost_basis": question.get("cost_basis"), "currency": question.get("currency"),
            "review_date": (plan.get("engine_state") or {}).get("date_end"),
        }
        identity = session.canonical(event)
        event["event_id"] = "initial-thesis-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        events.append(event)
    return events


_REVISION_KEYS = frozenset({"of_line_id", "condition"})
_CONDITION_REVISE_CHOICES = ("revise_threshold", "revise_metric")


def _condition_answers(plan, answers, amap):
    """What the user said about this review's condition questions, keyed by line.

    Returns ``(responses, revise_lines, crossed_lines)``:

    - ``responses`` are the extra check fields the answer contributes, folded
      into the envelope before ``build_check`` runs so one row carries the
      evidence, the engine's comparison and the user's word together.
    - ``revise_lines`` maps a line that answered `revise_*` to its question, and
      ``crossed_lines`` is every line that answered a crossing question — the
      two guards below are the only readers.

    A `skip` records nothing at all. It is the honest shape of "the question was
    posed and not answered": the check row still lands with the engine's own
    verdict, and nothing pretends the user weighed in."""
    responses, revise_lines, crossed_lines = {}, {}, set()
    date_end = (plan.get("engine_state") or {}).get("date_end")
    for question in plan.get("question_queue") or []:
        kind = question.get("kind")
        if kind not in ("condition_crossing", "condition_basis"):
            continue
        answer = amap[question["id"]]
        choice = answer.get("choice")
        offered = {option.get("value") for option in question.get("options") or []}
        valid = (CONDITION_BASIS_CHOICES if kind == "condition_basis" else
                 (CONDITION_CROSSING_EVENT_CHOICES if question.get("condition_kind") == "event"
                  else CONDITION_CROSSING_NUMERIC_CHOICES))
        if choice not in valid or choice not in offered:
            raise ReviewError(f"unsupported condition decision: {choice}")
        if answer.get("evidence_delta") is not None:
            raise ReviewError(f"{question['id']}: evidence_delta is not valid for a condition answer")
        line_id = question.get("line_id")
        if choice == "skip":
            continue
        if kind == "condition_crossing":
            crossed_lines.add(line_id)
            response = {"answer": choice, "answered_at": date_end}
            note = _clean_note(question["id"], answer, "a condition crossing")
            if note:
                response["note"] = note
            responses.setdefault(line_id, {})["user_response"] = response
            continue
        if choice == "keep":
            responses.setdefault(line_id, {})["basis_resolution"] = "kept"
            continue
        revise_lines[line_id] = question
        responses.setdefault(line_id, {})["basis_resolution"] = "revised"
    return responses, revise_lines, crossed_lines


def _condition_revisions(plan, answers, revise_lines, crossed_lines, slots, session_id):
    """Turn `revise_threshold`/`revise_metric` into new slot rows on the same lines.

    A re-stated criterion is a new row, never an edit: the old row is a fact
    about what the user meant when they wrote it, and every check already
    recorded points at it. ``build_slot(revises=...)`` carries the line forward
    so the history is not restarted.

    Three refusals, all firewalls rather than validation:

    - The revision must answer a basis question that was actually asked, and a
      basis question answered `revise_*` must carry one. Either half alone is a
      contradiction the engine names instead of resolving.
    - One change per line per session. A line that also answered a crossing this
      review has already been acted on; revising it in the same breath means the
      answer the user just gave was about a criterion that no longer exists.
      Same shape as #416's muted-then-revised rule guard.
    - The line must be live. A revision of something not in the record would
      create a second root rather than continue a line."""
    raw = answers.get("condition_revision")
    rows = []
    if raw is None:
        if revise_lines:
            question = next(iter(revise_lines.values()))
            raise ReviewError(
                f"{question['id']}: a revise answer requires answers.condition_revision carrying "
                "the re-stated condition — otherwise the user asked for a change that was never made")
        return rows
    submitted = raw if isinstance(raw, list) else [raw]
    seen = set()
    for index, entry in enumerate(submitted):
        label = f"answers.condition_revision[{index}]"
        if not isinstance(entry, dict):
            raise ReviewError(f"{label} must be an object")
        unknown = set(entry) - _REVISION_KEYS
        if unknown:
            raise ReviewError(f"{label} has unknown fields: " + ", ".join(sorted(unknown)))
        line_id = str(entry.get("of_line_id") or "").strip()
        if not line_id:
            raise ReviewError(f"{label} requires of_line_id")
        if line_id in seen:
            raise ReviewError(f"{label}: one change per condition per review")
        seen.add(line_id)
        if line_id not in revise_lines:
            raise ReviewError(
                f"{label}: no condition question this review asked to re-state {line_id!r}. A "
                "condition is re-stated because its basis was questioned, never on its own")
        if line_id in crossed_lines:
            raise ReviewError(
                f"{label}: condition {line_id!r} was also answered as a crossing this review — "
                "the answer you just gave is about the criterion as it stands, so replacing that "
                "criterion in the same review would leave the answer pointing at nothing. "
                "Re-state it next review, or answer the crossing without a replacement")
        parent = conditions.latest_by_line(slots).get(line_id)
        if parent is None:
            raise ReviewError(f"{label}: {line_id!r} is not a live condition in this record")
        try:
            rows.append(conditions.build_slot(
                entry.get("condition"),
                slot_id=f"slot-{session_id.split('__')[-1]}-r{index}",
                created=(plan.get("engine_state") or {}).get("date_end"),
                session_id=session_id or None, revises=parent))
        except conditions.ConditionError as exc:
            raise ReviewError(f"condition revision rejected ({line_id}): {exc}") from exc
    missing = sorted(set(revise_lines) - seen)
    if missing:
        raise ReviewError(
            f"{revise_lines[missing[0]]['id']}: a revise answer requires a replacement condition "
            f"for {missing[0]!r}")
    return rows


def _condition_card_context(plan, built):
    """The live condition behind every check row the plan's capped due list does
    not already carry (external review, round 2 BLOCK).

    The card joins a check to its condition through what the engine stamped —
    criterion, unit, kind, ``line_id``, ``thesis_link``. That stamp lived only on
    ``condition_slots_due``, which is capped, so a check submitted for a line
    beyond the cap — always legal, and after #416 C2 able to raise a crossing
    question — reached no card line at all while still counting toward the
    summary's `checked`. A crossed falsifier could be asked about and then be
    absent from the card, which is the exact "dropped without being named"
    defect this whole tier exists to prevent.

    It re-runs the *same* derivation the plan used — ``_condition_lines`` over a
    cycle index from ``_plan_thesis_cycles``, which assembles the same two row
    sources the plan did, this session's relinks included. Assembling that input
    a second way here is what round 3 caught: the derivation was single and its
    ingredients were not, so a cycle live only via a relink read as dead and its
    condition fell out of the join exactly as before.

    The renderer still reads one engine-stamped answer and never works liveness
    out for itself. A retired line is absent from the derivation and therefore
    stays absent here: retired means retired, and the retirement sentence is
    what speaks for it."""
    root = plan.get("state_root")
    checked = {row.get("slot_id") for row in built if isinstance(row, dict)}
    if not checked or not root or not os.path.isdir(root):
        return []
    already = {entry.get("slot_id")
               for entry in ((plan.get("state_snapshot") or {}).get("condition_slots_due") or [])}
    live, _retired, _unreadable = _condition_lines(root, _plan_thesis_cycles(plan))
    return [entry for entry in live
            if entry.get("slot_id") in checked and entry.get("slot_id") not in already]


def _build_condition_records(plan, answers, amap):
    """``(check_rows, revision_rows, card_context)`` — everything this review
    learned about the user's standing conditions, ready for the append-only
    stores, plus the join surface the card needs for the checks the plan's
    bounded due list does not carry.

    Order matters and is deliberate: the answers are resolved first so a check
    row is written *complete*, then the missing due slots are synthesized so the
    period has no silent gaps, then the revisions are built against the slots as
    they stood when the checks were taken."""
    root = plan.get("state_root")
    if not root or not os.path.isdir(root) or plan.get("route") == "snapshot_review":
        return [], [], []
    session_id = str(plan.get("session_id") or "")
    date_end = (plan.get("engine_state") or {}).get("date_end")
    slots, checks, _unreadable = _condition_store(root)
    responses, revise_lines, crossed_lines = _condition_answers(plan, answers, amap)
    envelopes = _condition_check_envelopes(answers, "answers")
    built = _build_condition_checks(slots, checks, envelopes, session_id, date_end, responses)
    _refuse_check_drift(plan, built)
    _refuse_dropped_ingested_check(plan, built)
    due = ((plan.get("state_snapshot") or {}).get("condition_slots_due") or [])
    built += _synthesized_not_checked(slots, checks, due, built, session_id, date_end)
    revisions = _condition_revisions(plan, answers, revise_lines, crossed_lines, slots, session_id)
    return built, revisions, _condition_card_context(plan, built)


def _refuse_dropped_ingested_check(plan, built):
    """A reading this session already took cannot quietly become "not checked".

    ``prepare --condition-checks`` ingests real lookups: they are validated into
    rows, frozen into the plan, and a crossing question may have been posed
    against them. If the answers then omit one of those slots, the synthesized
    ``not_checked`` path — which exists for conditions nobody looked at — would
    overwrite a lookup that *did* happen with a row saying nobody looked. The
    user could have been asked about a crossed line and the file would end up
    claiming the condition went unchecked that period.

    So the synthesized path is legal only for a due condition that never had a
    prepare-side check. Anything ingested must come back (external review,
    round 1)."""
    ingested = {row.get("slot_id")
                for row in ((plan.get("state_snapshot") or {}).get("condition_checks") or [])}
    dropped = sorted(ingested - {row.get("slot_id") for row in built})
    if dropped:
        raise ReviewError(
            "condition check(s) " + ", ".join(repr(slot_id) for slot_id in dropped) +
            " were looked up when this review was prepared but are missing from "
            "answers.condition_checks. Recording them as not-checked would erase a lookup that "
            "happened — resubmit the same envelope, or rerun prepare without it")


# What the user's own answer is allowed to move: their response, how the basis
# question ended, and the two verdict-of-record fields those derive. Everything
# else — the observation, `engine_verdict`, the alerts — is the evidence the
# question was posed against and must come back identical.
_ANSWER_ONLY_CHECK_FIELDS = ("user_response", "basis_resolution",
                             "final_verdict", "verdict_source")


def _refuse_check_drift(plan, built):
    """The question was posed against a number; the record must be that number.

    The plan freezes the check rows the crossing question was built from. If the
    envelope resubmitted with the answers produces a different row, the user
    answered about one reading and the file would store another — which is
    exactly the class of silent divergence the frozen question surfaces exist to
    prevent. The user's own contribution is excluded from the comparison,
    because adding it is the entire point of the second submission."""
    frozen = {row.get("slot_id"): row
              for row in ((plan.get("state_snapshot") or {}).get("condition_checks") or [])}
    if not frozen:
        return

    def _evidence(row):
        return session.canonical({key: value for key, value in row.items()
                                  if key not in _ANSWER_ONLY_CHECK_FIELDS})

    for row in built:
        prior = frozen.get(row.get("slot_id"))
        if prior is not None and _evidence(prior) != _evidence(row):
            raise ReviewError(
                f"condition check for {row.get('slot_id')!r} changed between the question and the "
                "answer: the user was asked about one reading and this would record another. "
                "Rerun prepare with the corrected envelope and ask again")


def _refuse_revision_of_a_muted_line(plan, expected_revision):
    """A rule silenced this session cannot also be replaced this session (#416)."""
    root = plan.get("state_root")
    if not root or not os.path.isdir(root):
        return
    muted_ids = _muted_rule_ids(root)
    if not muted_ids:
        return
    _tracking, muted = problems.load_rules(os.path.join(root, "rules.jsonl"), muted_ids)
    target = str(expected_revision.get("rule_id") or "")
    for row in muted:
        if target in {str(row.get("rule_id")), problems.rule_line_id(row)}:
            raise ReviewError(
                f"rule {problems.rule_line_id(row)!r} is muted, so it cannot be revised in the "
                "same review — a replacement would inherit the silence and never be asked "
                "about. Unmute it first, or answer the breach without a replacement")


def _slot_commitment(plan, chosen, condition, expected_revision):
    """#412: a condition the engine cannot compute becomes a stored slot.

    The metric-shaped fields (`metric_key`, `metric_value`, `goal`, `dim`) are
    dropped rather than nulled: they exist to join a commitment to an engine
    metric, and a slot has none — carrying them would be four written-never-read
    fields inviting a later reader to join on them anyway.

    Two refusals stay, both firewalls rather than validation:

    - A slot cannot replace a rule under `revise_rule`. `problems.check_rules`
      reconciles that rule mechanically against problem events every period; a
      researched condition has no problem key to join on, so accepting it would
      silently retire a tracked rule into something `held_streak` can never
      count (`docs/development-guide.md` section 5).
    - A commitment carries a metric_key or a condition, never both. Two anchors
      means two answers to "what did this review actually track", and the card's
      then/now reconciliation would have to pick one.
    """
    if expected_revision:
        raise ReviewError("a revise_rule replacement must be tracked by an engine metric; "
                          "a condition slot cannot enter the rule reconciliation")
    if chosen.get("metric_key"):
        raise ReviewError("a commitment carries either a metric_key or a condition, not both")
    session_id = str(plan.get("session_id") or "")
    try:
        slot = conditions.build_slot(
            condition,
            slot_id="slot-" + (session_id.split("__")[-1] or "0") + "-0",
            created=(plan.get("engine_state") or {}).get("date_end"),
            session_id=session_id or None)
    except conditions.ConditionError as exc:
        raise ReviewError(f"condition slot rejected: {exc}")
    rule = (chosen.get("rule") or "").strip()
    if rule and rule != slot["criterion"]:
        # The card prints `rule`; the record stores `criterion`. One of them
        # being a paraphrase of the other is exactly what #396 forbids.
        raise ReviewError("commitment.rule must be the condition's criterion, verbatim")
    return {"rule": slot["criterion"], "origin": "custom", "condition": slot}


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
    if expected_revision:
        # #416: the frozen question was posed before this session could mute that
        # rule. Without this, muting at step 8 and revising at step 9 both land:
        # the replacement inherits the line's silence and a rule the user authored
        # *this week* is born muted, absent from the rotation and from #292's
        # breach disclosure, with nothing said. The two answers contradict each
        # other, so the engine names the contradiction instead of picking one.
        _refuse_revision_of_a_muted_line(plan, expected_revision)
    if selected == "skip":
        if expected_revision:
            raise ReviewError("a revise_rule answer requires a replacement commitment")
        return None
    candidates = {row["id"]: row for row in (plan.get("card_plan") or {}).get("candidate_rules") or []}
    condition = choice.get("condition")
    if selected in candidates:
        if condition is not None:
            raise ReviewError("a candidate rule is already tracked by an engine metric; "
                              "a condition slot belongs to a self-authored commitment")
        chosen = dict(candidates[selected])
        chosen["origin"] = "candidate"
    elif selected == "custom":
        chosen = {"rule": (choice.get("rule") or "").strip(), "metric_key": choice.get("metric_key"),
                  "goal": choice.get("goal") or "down", "dim": choice.get("dim"), "origin": "custom"}
        if not chosen["rule"] and condition is None:
            raise ReviewError("custom commitment requires rule")
    else:
        raise ReviewError("commitment.choice must be a candidate id, custom, or skip")
    metrics = (plan.get("engine_state") or {}).get("metrics") or {}
    chosen.pop("id", None)
    if condition is not None:
        chosen = _slot_commitment(plan, chosen, condition, expected_revision)
    elif chosen.get("metric_key") not in metrics:
        # Still an error, deliberately: a metric_key the engine does not compute
        # is an agent mistake, not a user's condition. A condition arrives as a
        # condition (#412).
        raise ReviewError(f"commitment metric is not in engine state: {chosen.get('metric_key')}")
    else:
        chosen["metric_value"] = metrics.get(chosen["metric_key"])
    chosen["source"] = "user_chosen"
    if expected_revision:
        replacement_key = session.PKEY.get(chosen.get("metric_key"))
        if replacement_key != expected_revision.get("problem_key"):
            raise ReviewError("replacement commitment must track the same problem_key as the revised rule")
        chosen["revises_rule_id"] = revises_rule_id
    # `insufficient_data` describes this file's round-trip sample, which is what an
    # engine metric's baseline is computed from. A slot's baseline came from an
    # outside source and is not affected by it (#412).
    if (plan.get("engine_state") or {}).get("insufficient_data") and not chosen.get("condition"):
        chosen["baseline_note"] = "short-sample baseline"
    return chosen


def _draft_bundle(plan, answers, narrative, require_commitment,
                  question_surfaces=None, question_presentations=None):
    if answers.get("session_id") != plan.get("session_id"):
        raise ReviewError("answers.session_id does not match Review Plan")
    if question_surfaces is not None and not isinstance(question_presentations, list):
        raise ReviewError("validated question surface is missing its frozen presentation")
    question_surface.validate_answer_contract(
        plan, answers,
        presentations=question_presentations if question_surfaces is not None else None,
    )
    amap = thesis.validate_required_answers(plan, answers, allow_commitment_missing=not require_commitment)
    # #416 C2: the falsifier a thesis row states can arrive as a condition
    # envelope on that same row. It is lifted out before the row becomes a
    # thesis event and built into a slot below — one exchange, two records, no
    # copy of the condition inside the thesis store.
    validated_updates, thesis_conditions = _split_thesis_conditions(
        _validate_thesis_completeness(plan, answers))
    agent_updates = _assign_thesis_ids(plan, validated_updates)
    cycle_relinks = list(
        ((plan.get("state_snapshot") or {}).get("thesis_cycle_relinks") or [])
    )
    updates = cycle_relinks + agent_updates
    decisions = thesis.build_decision_events(plan, answers, updates)
    exit_narratives = _build_exit_narratives(plan, answers, amap)
    revisit_resolutions = _build_revisit_resolutions(plan, answers, amap)
    rule_breach_decisions = _build_rule_breach_decisions(plan, answers, amap)
    headline_motive_events = _build_headline_motive_events(plan, answers, amap)
    exit_consistency_events = _build_exit_consistency_events(plan, answers, amap)
    initial_thesis_events = _build_initial_thesis_events(plan, answers, amap)
    condition_checks, condition_revisions, condition_context = _build_condition_records(
        plan, answers, amap)
    thesis_condition_slots = _build_thesis_condition_slots(plan, thesis_conditions)
    card_renderer.validate_narrative(narrative)
    # #82 gate: every required honesty key must be covered by an agent-authored
    # sentence, and no sentence may claim a key the plan does not require —
    # either untriggered by the engine or month-gated out with its vs-market
    # host lines (#284).
    required = set((plan.get("card_plan") or {}).get("required_honesty_keys") or [])
    provided = set((narrative.get("honesty") or {}).keys())
    if required - provided:
        raise ReviewError("narrative.honesty is missing required keys: " + ", ".join(sorted(required - provided)))
    if provided - required:
        raise ReviewError("narrative.honesty has keys this review does not require: " + ", ".join(sorted(provided - required)))
    commitment = _resolve_commitment(plan, answers) if require_commitment else None
    bundle = {
        "schema_version": 2,
        "engine_version": plan.get("engine_version") or _engine_version(),
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
    if question_surfaces is not None:
        bundle["question_surfaces"] = question_surfaces
        bundle["question_presentations"] = question_presentations
    # Only present when a due checkpoint was actually answered: sessions committed
    # before this key existed must re-draft to the identical canonical bundle, or
    # the documented-safe finalize retry would fail closed on every old session.
    if revisit_resolutions:
        bundle["revisit_resolutions"] = revisit_resolutions
    if rule_breach_decisions:
        bundle["rule_breach_decisions"] = rule_breach_decisions
    if headline_motive_events:
        bundle["headline_motive_events"] = headline_motive_events
    # #303: absent-when-empty, same replay-compatibility contract as the keys
    # above — only present when at least one early-exit motive was classified.
    if exit_consistency_events:
        bundle["exit_consistency_events"] = exit_consistency_events
    # Absent-when-empty, same replay-compatibility contract as the keys above:
    # first-review-only, and only when at least one entry motive was classified.
    if initial_thesis_events:
        bundle["initial_thesis_events"] = initial_thesis_events
    # #412: this period's condition results, and any criterion the user re-stated
    # because its basis was questioned. Absent-when-empty for the same reason —
    # a session committed before the check flow existed must re-draft to the
    # identical canonical bundle or its documented-safe finalize retry fails.
    if condition_checks:
        bundle["condition_checks"] = condition_checks
    if condition_revisions:
        bundle["condition_revisions"] = condition_revisions
    # The conditions behind checks the plan's bounded due list does not carry.
    # Not a store — nothing here is appended anywhere; it is the card's join
    # surface for a beyond-cap reading, stamped by the engine so the renderer
    # never re-derives it (external review, round 2).
    if condition_context:
        bundle["condition_slots_context"] = condition_context
    # #416 C2: the falsifiers stated with this review's theses, now watched
    # conditions. Absent-when-empty for the same retry reason as the two above.
    if thesis_condition_slots:
        bundle["thesis_conditions"] = thesis_condition_slots
    # #446 cut 1: this review's horizon said-vs-done judgments, turned into
    # durable rows instead of being recomputed and discarded every period.
    # Absent-when-empty for the same retry reason as the keys above.
    horizon_verdicts = _horizon_verdict_rows(plan, plan["session_id"])
    if horizon_verdicts:
        bundle["verdicts"] = horizon_verdicts
    return bundle


def _load_interaction(args, pending):
    answers = _load_json(args.answers, "answers") if args.answers else pending.get("answers")
    narrative = _load_json(args.narrative, "narrative") if args.narrative else pending.get("narrative")
    if not answers or not narrative:
        raise ReviewError("answers and narrative are required (pass files or save them with preview)")
    return answers, narrative


CAPTURE_INFERENCE_FIELDS = ("source_type", "source_name", "source_confidence",
                           "emotion", "emotion_inferred", "confidence", "confidence_inferred")


def _load_capture_entries(path):
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError) as exc:
        raise ReviewError(f"cannot read entries: {exc}") from exc
    if not isinstance(value, list) or not value:
        raise ReviewError("entries must be a non-empty JSON array")
    for entry in value:
        if not isinstance(entry, dict):
            raise ReviewError("each capture entry must be a JSON object")
        if not entry.get("cycle_id"):
            raise ReviewError("each capture entry requires cycle_id")
        if not entry.get("note"):
            raise ReviewError("each capture entry requires note")
        for key in ("emotion", "confidence", "source_type", "source_confidence"):
            if key in entry and entry[key] not in thesis.INFERENCE_ENUMS[key]:
                raise ReviewError(f"invalid {key}: {entry[key]!r}")
    return value


def _capture_rows(entries, plan, capture_session_id):
    """Turn validated capture entries into `theses.jsonl`-safe rows (#237 #4).

    `thesis.reconstruct_states` treats a row with no ``event`` (or
    ``thesis_cycle_relink``) as a full-content replace of that cycle's thesis —
    the only carried-forward keys from the prior state are decision_cursor/
    last_decision/last_exit/final_outcome/evidence_history/last_evidence/
    source_state, never why/exit_trigger/horizon/etc. A capture entry for a
    cycle that already has an established thesis must never take that path, or
    it silently wipes the cycle's existing why/exit_trigger. It goes through a
    `thesis_decision` event instead, which only ever attaches to (never
    replaces) the cycle's content. A cycle with no established thesis yet takes
    the opposite risk: `thesis_decision` for a cycle with no current state is
    dropped entirely (`if not current: continue`), so it must go through the
    full-content path, which requires an honest why/exit_trigger the same way a
    full review's inferred-thesis path does — otherwise the capture is silently
    lost, exactly what #237's #4 is meant to prevent.

    Every row carries `session_id: capture_session_id` — `_append_session_rows`
    only uses its `session_id` argument to *filter* existing rows for dedup; it
    never stamps the tag onto what it writes, so the caller must.
    """
    active_cycle_ids = {row.get("cycle_id") for row in
                        (plan.get("state_snapshot") or {}).get("active_theses") or []}
    # missing_thesis_positions is plan-top-level, not under state_snapshot (see _build_plan).
    missing_by_cycle = {row.get("cycle_id"): row for row in plan.get("missing_thesis_positions") or []}
    rows = []
    for entry in entries:
        cycle_id = entry["cycle_id"]
        inference = {key: entry[key] for key in CAPTURE_INFERENCE_FIELDS if key in entry}
        if cycle_id in active_cycle_ids:
            rows.append({"event": "thesis_decision", "cycle_id": cycle_id,
                        "session_id": capture_session_id,
                        "note": entry["note"], **inference})
            continue
        missing = missing_by_cycle.get(cycle_id)
        if missing is None:
            raise ReviewError(
                f"cycle_id {cycle_id!r} is neither an active thesis nor a missing "
                "thesis position in this Review Plan; cannot capture against it")
        if not entry.get("why") or not entry.get("exit_trigger"):
            raise ReviewError(
                f"cycle_id {cycle_id!r} has no established thesis yet; capture "
                "entries for a new cycle must include why and exit_trigger, the "
                "same as a full review's inferred thesis")
        row = {"cycle_id": cycle_id, "ticker": missing.get("ticker"), "maturity": "inferred",
              "session_id": capture_session_id,
              "why": entry["why"], "exit_trigger": entry["exit_trigger"],
              "note": entry["note"], **inference}
        if missing.get("origin"):
            row["origin"] = missing["origin"]
        rows.append(row)
    return rows


def cmd_capture(args):
    """Light-tier capture-only action (#237 #4): no finalize, no review_mark,
    no commitment, no counted question budget. Appends directly to
    `theses.jsonl` under a distinct session id so a later real `finalize` for
    the same underlying state can never collide with what capture wrote (see
    `_capture_rows` for why the row shape itself must also stay non-destructive).

    Cleans up its `.pending/<session_id>/` entry once appended, so repeated
    captures do not grow `_pending_by_fingerprint`'s scan forever the way an
    abandoned full review already does today. That cleanup would otherwise
    break retry safety — an interrupted agent turn that is unsure whether its
    first `capture` call actually landed must be able to repeat the identical
    call and get the same answer, not a "pending session not found" crash — so
    a missing pending dir is first checked against `theses.jsonl` for rows
    already tagged with this session's derived capture id before it is treated
    as an error."""
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    capture_session_id = f"{args.session_id}--capture"
    theses_path = os.path.join(root, "theses.jsonl")
    try:
        pending = session.load_pending(root, args.session_id)
    except session.SessionError:
        already = [row for row in thesis.read_jsonl(theses_path)
                  if row.get("session_id") == capture_session_id]
        if not already:
            raise
        _emit({"status": "captured", "session_id": args.session_id,
              "capture_session_id": capture_session_id, "entries": len(already),
              "report": {"status": "no-op (already captured)"}})
        return
    plan = pending.get("plan") or {}
    tier = ((plan.get("state_snapshot") or {}).get("cadence") or {}).get("tier")
    if tier != "light":
        raise ReviewError(
            f"capture is only valid for a light-tier session (cadence.tier={tier!r}); "
            "a full-tier review must go through preview/finalize")
    entries = _load_capture_entries(args.entries)
    rows = _capture_rows(entries, plan, capture_session_id)
    with session.projection_transaction(root) as locked_root:
        report = session._append_session_rows(theses_path, capture_session_id, rows)
    shutil.rmtree(session.pending_dir(root, args.session_id), ignore_errors=True)
    _emit({"status": "captured", "session_id": args.session_id,
          "capture_session_id": capture_session_id, "entries": len(rows),
          "report": report})


def cmd_preview(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    pending = session.load_pending(root, args.session_id)
    plan = pending.get("plan")
    answers, narrative = _load_interaction(args, pending)
    bundle = _draft_bundle(
        plan, answers, narrative, require_commitment=False,
        question_surfaces=pending.get("question_surfaces"),
        question_presentations=pending.get("question_presentations"),
    )
    private_md = card_renderer.render_private(bundle)
    public_md = card_renderer.render_public(bundle)
    private_html = card_renderer.render_html(bundle)
    paths = session.save_pending(root, args.session_id, answers=answers, narrative=narrative,
                                 **{"card-private-preview": private_md,
                                    "card-public-preview": public_md,
                                    "card-private-preview.html": private_html})
    _emit({"status": "previewed", "session_id": args.session_id,
           "private_card": private_md, "public_card": public_md,
           "private_card_html_path": paths.get("card-private-preview.html"),
           "candidate_rules": (plan.get("card_plan") or {}).get("candidate_rules") or [],
           # #302(c): interaction-layer-only; None when there is nothing honest to compare.
           "candidate_comparison": (plan.get("card_plan") or {}).get("candidate_comparison"),
           "paths": paths, "next_action": "show the review-card preview (delivery contract: references/card-delivery.md); ask the user to choose one rule or skip; then finalize"})


def cmd_finalize(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    with session.finalize_transaction(root, args.session_id) as transaction:
        committed_path = session.session_dir(root, args.session_id)
        if os.path.isdir(committed_path):
            existing = session.load_committed(root, args.session_id)
            plan = existing.get("review_plan")
            pending = {"answers": existing.get("answers"), "narrative": existing.get("narrative"),
                       "question_surfaces": existing.get("question_surfaces"),
                       "question_presentations": existing.get("question_presentations")}
        else:
            pending = session.load_pending(root, args.session_id)
            plan = pending.get("plan")
        answers, narrative = _load_interaction(args, pending)
        bundle = _draft_bundle(
            plan, answers, narrative, require_commitment=True,
            question_surfaces=pending.get("question_surfaces"),
            question_presentations=pending.get("question_presentations"),
        )
        private_md = card_renderer.render_private(bundle)
        public_md = card_renderer.render_public(bundle)
        private_html = card_renderer.render_html(bundle)
        result, projection, projection_error = transaction.commit_bundle(
            bundle, private_md, public_md, private_html, persist=bool(plan.get("persist"))
        )
    # A no-op idempotent retry writes nothing and legacy sessions may lack the
    # HTML artifact; emit its path only when the file is really there so the
    # delivery contract's markdown fallback triggers instead of file-not-found.
    html_path = os.path.join(result["path"], "card-private.html")
    _emit({"status": result["status"], "session_id": args.session_id, "path": result["path"],
           "private_card": os.path.join(result["path"], "card-private.md"),
           "public_card": os.path.join(result["path"], "card-public.md"),
           "private_card_html": html_path if os.path.isfile(html_path) else None,
           "projection": projection, "projection_error": projection_error,
           "recoverable": bool(projection_error)})


def cmd_resume(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    if args.session_id:
        pending = session.load_pending(root, args.session_id)
        plan = pending.get("plan")
        existing = pending.get("question_surfaces")
        if args.question_surfaces:
            try:
                candidate = _load_json(args.question_surfaces, "question surfaces")
                validated = question_surface.validate_surfaces(plan, candidate)
            except (ReviewError, question_surface.QuestionSurfaceError) as exc:
                if existing is not None:
                    raise ReviewError(
                        "validated question surfaces are already fixed for this pending session"
                    ) from exc
                fallback = question_surface.build_presentations(plan)
                _emit({**_pending_for_agent(pending), "status": "surface_fallback",
                       "question_presentations": fallback,
                       "surface_error": str(exc),
                       "next_action": "present the unchanged engine question/options fallback"})
                return
            if existing is not None and session.canonical(existing) != session.canonical(validated):
                raise ReviewError("validated question surfaces are already fixed for this pending session")
            if existing is None:
                presentations = question_surface.build_presentations(plan, validated)
                session.save_pending(root, args.session_id,
                                     **{"question-presentations": presentations,
                                        "question-surfaces": validated})
                pending = session.load_pending(root, args.session_id)
            else:
                presentations = pending.get("question_presentations")
                if not isinstance(presentations, list):
                    raise ReviewError("validated question surface is missing its frozen presentation")
            _emit({**_pending_for_agent(pending), "status": "surface_validated",
                   "question_presentations": presentations,
                   "next_action": "present these exact questions in queue order, then run preview"})
            return
        if existing is not None:
            if not isinstance(pending.get("question_presentations"), list):
                raise ReviewError("validated question surface is missing its frozen presentation")
            pending["status"] = "surface_validated"
            pending["next_action"] = "reuse these exact question presentations, then run preview"
        else:
            pending["status"] = "engine_fallback"
            pending["question_presentations"] = question_surface.build_presentations(plan)
            pending["next_action"] = ("author eligible private surfaces with resume --question-surfaces, "
                                      "or present the unchanged engine fallback")
        _emit(_pending_for_agent(pending))
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
    if args.format == "private-markdown":
        sys.stdout.write(private_md)
        return
    if args.format == "public-markdown":
        sys.stdout.write(public_md)
        return
    _emit({"session_id": args.session_id, "private_card": private_md, "public_card": public_md})


def cmd_repair(args):
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    outcome = session.repair_projections(root)
    _emit({"status": "repaired" if not outcome["errors"] else "partially_repaired", **outcome})


def cmd_set_cap(args):
    """Record (or clear) the user's standing single-position cap (#324).

    Persists to ``profile.json`` so diagnosis, prescription, and the rule text
    all reconcile against the user's own number next review. Fail-closed: a cap
    outside (0,1) is rejected, never silently stored. The write is a whole-file
    atomic replace that preserves any other profile keys."""
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    os.makedirs(root, exist_ok=True)
    path = _profile_path(root)
    profile = {}
    if os.path.exists(path):
        try:
            loaded = session.read_json(path)
            if isinstance(loaded, dict):
                profile = loaded
        except (OSError, ValueError):
            profile = {}
    if args.clear:
        profile.pop("max_position_pct", None)
        ledger.atomic_write_text(path, session.pretty(profile))
        _emit({"status": "cleared", "root": root, "max_position_pct": None})
        return
    cap = card_renderer.valid_position_cap(args.pct)
    if cap is None:
        raise ReviewError(
            "max position cap must be a fraction strictly between 0 and 1 "
            "(for example 0.25 for 25%), not a percentage or an out-of-range value")
    profile["max_position_pct"] = cap
    ledger.atomic_write_text(path, session.pretty(profile))
    _emit({"status": "set", "root": root, "max_position_pct": cap})


def _muted_rule_ids(root):
    """The rule lines the user asked not to be asked about (#416).

    Standing preference, so it lives in ``profile.json`` beside the position cap
    and not in ``rules.jsonl``: that file is a tier-3 rebuildable projection
    (`references/data-contract.md`), rebuilt from committed bundles by
    ``repair-projections``, and a bundle carries commitments only. A mute stored
    there would survive until the first repair and then silently un-mute every
    rule — the user starts being asked again with no signal that anything
    changed. Same fail-soft posture as the cap: an unreadable profile means no
    mutes, never a crash."""
    path = _profile_path(root)
    if not os.path.exists(path):
        return []
    try:
        profile = session.read_json(path)
    except (OSError, ValueError):
        return []
    ids = profile.get("muted_rules") if isinstance(profile, dict) else None
    return [str(rule_id) for rule_id in ids if str(rule_id).strip()] if isinstance(ids, list) else []


def _write_mutes(root, line_ids):
    """Replace the profile's mute list, preserving every other preference."""
    path = _profile_path(root)
    profile = {}
    if os.path.exists(path):
        try:
            loaded = session.read_json(path)
            if isinstance(loaded, dict):
                profile = loaded
        except (OSError, ValueError):
            profile = {}
    if line_ids:
        profile["muted_rules"] = sorted(set(line_ids))
    else:
        profile.pop("muted_rules", None)
    ledger.atomic_write_text(path, session.pretty(profile))


def _clear_mute(root, line_id, muted_ids):
    _write_mutes(root, [rule_id for rule_id in muted_ids if rule_id != line_id])


def cmd_mute_rule(args):
    """Silence a rule without retiring it, or bring it back (#416).

    A rule the user no longer wants to be asked about has had exactly two exits:
    keep answering for it, or lose it. Muting is the third — the rule stops
    entering the card's attention rotation while its reconciliation keeps
    running, so the statistics are there when the user comes back to decide.

    Owner direction, 2026-07-26: establish the rule first, keep accumulating,
    look again once there is data. That only works if the accumulation is real,
    which is why `problems.snapshot` reconciles muted rules on the same path as
    tracked ones.

    Mute is **state on a stable identity**, never a new rule row. The identity is
    the rule *line* (`problems.rule_line_id` — the root of the `revises` chain),
    so a later revision of the same rule inherits the mute, and nothing about
    muting can move the `rule_id` that `_rule_breach_history` keys answered
    breach questions on. An earlier cut of this wrote a superseding row instead
    and produced exactly those two failures: a required question re-asked after
    an unmute, and a chain with two live heads when a revision landed on top.
    """
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    os.makedirs(root, exist_ok=True)
    muted_ids = _muted_rule_ids(root)
    tracking, muted = problems.load_rules(os.path.join(root, "rules.jsonl"), muted_ids)
    lines = {}
    for row in tracking + muted:
        lines[problems.rule_line_id(row)] = row
        lines[str(row.get("rule_id"))] = row          # the head id is what a payload shows
    rule = lines.get(str(args.rule_id))
    if rule is None:
        if args.unmute and str(args.rule_id) in set(muted_ids):
            # A silenced line whose rows are gone (a reset, a hand-edited file):
            # the profile entry is inert but permanent, and refusing here would
            # leave the user no way to clear it.
            _clear_mute(root, str(args.rule_id), muted_ids)
            _emit({"status": "tracking", "root": root, "rule_line_id": str(args.rule_id),
                   "rule_id": None, "text": None,
                   "note": "the rule itself is no longer in rules.jsonl; cleared the stale entry"})
            return
        raise ReviewError(
            f"no live rule matching {args.rule_id!r} — a superseded id names a version, not a "
            f"rule (live: {sorted({problems.rule_line_id(r) for r in tracking + muted}) or 'none'})")
    line_id = problems.rule_line_id(rule)
    target_muted = not args.unmute
    # Effective state, not the profile's copy of it: `load_rules` also honours a
    # row-level `status: "muted"` (contract since #137), so asking the profile
    # would tell a user their rule is tracked while the engine's own reader
    # silences it — one boolean with two sources of truth.
    silent_now = any(problems.rule_line_id(row) == line_id for row in muted)
    if silent_now == target_muted:
        raise ReviewError(f"rule {line_id!r} is already {'muted' if target_muted else 'tracking'}")
    if args.unmute and line_id not in set(muted_ids):
        raise ReviewError(
            f"rule {line_id!r} is silenced by a `status: \"muted\"` field in rules.jsonl, not by "
            "a preference this command owns — remove that field from the row to bring it back")
    remaining = [rule_id for rule_id in muted_ids if rule_id != line_id]
    if target_muted:
        remaining.append(line_id)
    _write_mutes(root, remaining)
    _emit({"status": "muted" if target_muted else "tracking", "root": root,
           "rule_line_id": line_id, "rule_id": rule.get("rule_id"), "text": rule.get("text")})


def _consultation_path(root):
    return os.path.join(root, "pre_trade_consultations.jsonl")


def _anchor_position_row(position, anchor_date):
    """One synthesized buy row for an anchored position, or None for a
    structurally malformed entry — ledger.derive_holdings tolerates the same
    shapes as a soft ``bad_snapshot_position`` integrity issue rather than
    raising, and reconstruction here mirrors that leniency.

    A missing ``avg_cost`` falls back to ``market_value / shares``, the same
    alternative cost basis ``snapshot_adapter._valuation()`` itself accepts.
    A position with neither fails closed by name: unlike ``derive_holdings``
    (which only needs share counts), this module must put a price on every
    row it hands to trade_recap's FIFO functions, and inventing one would
    silently mis-state a real held position's weight — worse than refusing.
    """
    if not isinstance(position, dict):
        return None
    ticker = position.get("ticker")
    if not isinstance(ticker, str) or not ticker.strip():
        return None
    try:
        shares = float(position.get("shares"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(shares) or shares <= 1e-9:
        return None
    price = position.get("avg_cost")
    if price is None:
        market_value = position.get("market_value")
        if market_value is not None:
            try:
                price = float(market_value) / shares
            except (TypeError, ValueError, ZeroDivisionError):
                price = None
    if price is None:
        raise ReviewError(
            f"the ledger's snapshot anchor carries no avg_cost or market_value for {ticker}; "
            "consider cannot price this position without a cost basis")
    try:
        price = float(price)
    except (TypeError, ValueError):
        raise ReviewError(f"the ledger's snapshot anchor has a non-numeric cost basis for {ticker}")
    if not math.isfinite(price) or price <= 0:
        raise ReviewError(f"the ledger's snapshot anchor has a non-positive cost basis for {ticker}")
    return {"ticker": ticker.strip(), "side": "buy", "qty": shares, "price": price,
            "date": anchor_date, "market": (position.get("market") or "US"),
            "currency": (position.get("currency") or "USD").upper()}


def _ledger_trade_row(event):
    """One trade_recap row from a ledger ``trade`` event
    (``{type:"trade", date, ticker, action, qty, price, market, currency}``),
    or None for a structurally malformed event. Mirrors ``ledger._norm_trade``'s
    own tolerance rather than calling it directly, because that helper drops
    market/currency, which this module's rows need."""
    try:
        date = dt.date.fromisoformat(str(event.get("date")))
        ticker = event["ticker"]
        side = str(event.get("action", "")).strip().lower()
        qty = float(event["qty"])
        price = float(event["price"])
    except (KeyError, TypeError, ValueError):
        return None
    if (not isinstance(ticker, str) or not ticker.strip() or side not in ("buy", "sell")
            or not math.isfinite(qty) or qty <= 0 or not math.isfinite(price) or price <= 0):
        return None
    return {"ticker": ticker.strip(), "side": side, "qty": qty, "price": price, "date": date,
            "market": (event.get("market") or "US"),
            "currency": (event.get("currency") or "USD").upper()}


def _ledger_trade_events(events):
    """Only real, dated trade events from the ledger — deliberately *not*
    ``_rows_from_ledger``'s reconstruction. That function also synthesizes one
    priced "buy" row per position from the latest snapshot anchor for the
    FIFO/cost-basis math ``consider`` needs, and fails closed when a position
    carries no cost basis at all. A snapshot anchor legitimately declares
    shares with no cost basis — a user re-syncing positions may not know it —
    and a synthesized anchor row has no genuine execution date to begin with:
    reading one as a matched trade would misrepresent a declared holding as
    something the user did on a specific day. A caller that only needs to
    know whether a real trade happened, never what the book would be worth,
    uses this instead — it never raises, matching ``_ledger_trade_row``'s own
    tolerance for a malformed event."""
    rows = []
    for event in events:
        if event.get("type") != "trade":
            continue
        row = _ledger_trade_row(event)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda row: row["date"])
    return rows


def _rows_from_ledger(events):
    """trade_recap.load()'s row shape, reconstructed from ledger events the
    way ``ledger.derive_holdings`` trusts them: the latest complete snapshot
    anchor (``ledger.latest_anchor``), synthesized as one buy per position
    dated at the anchor's ``as_of``, then every trade strictly after that
    date layered on top — an anchor is ``as_of``'s close-of-day state, so a
    same-day trade is already reflected in its declared numbers, the same
    cutoff ``derive_holdings`` applies. No anchor at all falls back to every
    trade event, matching ``derive_holdings``' own backward-compatible
    pure-replay path."""
    anchor = ledger.latest_anchor(events)
    anchor_date = None
    rows = []
    if anchor is not None:
        anchor_date = dt.date.fromisoformat(str(anchor.get("as_of")))
        # A ticker declared twice in one anchor is malformed input, not two
        # positions; keep only the last declaration, the same overwrite
        # ledger.derive_holdings' own `pos[t] = {...}` assignment produces,
        # so a hand-edited or corrupted ledger cannot double-count a position
        # here while derive_holdings reads it as one.
        by_ticker = {}
        for position in anchor.get("positions") or []:
            if isinstance(position, dict) and isinstance(position.get("ticker"), str):
                by_ticker[position["ticker"]] = position
            else:
                by_ticker[id(position)] = position   # malformed entry: let _anchor_position_row reject it
        for position in by_ticker.values():
            row = _anchor_position_row(position, anchor_date)
            if row is not None:
                rows.append(row)
    for event in events:
        if event.get("type") != "trade":
            continue
        row = _ledger_trade_row(event)
        if row is None:
            continue           # malformed event — ledger.derive_holdings tolerates the same rows
        if anchor_date is not None and row["date"] <= anchor_date:
            continue
        rows.append(row)
    rows.sort(key=lambda row: row["date"])
    return rows


def _consider_rows(args, root):
    """Resolve the book ``consider`` reasons over: the supplied CSV paths, or
    a reconstruction from ``<root>/ledger.jsonl`` when none are given (issue
    #456 names this the ledger basis, distinct from a review's own CSV/FIFO
    path — the two can disagree about a position's weight, and the record
    says which one it used rather than implying a currency it does not have).
    Fails closed when neither source yields a usable row: an empty book
    cannot answer a pre-trade question, and inventing one would be worse than
    refusing."""
    if args.paths:
        paths = [os.path.abspath(os.path.expanduser(p)) for p in args.paths]
        for path in paths:
            if not os.path.isfile(path):
                raise ReviewError(f"CSV path does not exist: {path}")
        rows = trade_recap.load(paths)
        if not rows:
            raise ReviewError(
                "none of the supplied CSV paths contained a usable BUY/SELL trade row; "
                "consider cannot answer against an empty book")
        return rows, "transactions"
    ledger_path = os.path.join(root, "ledger.jsonl")
    events, _skipped = ledger.load_ledger(ledger_path)
    rows = _rows_from_ledger(events)
    if not rows:
        raise ReviewError(
            f"no usable trade or snapshot history in {ledger_path}; run a review first, or pass "
            "CSV paths directly, before asking consider about a hypothetical trade")
    return rows, "ledger"


def _load_json_arg(value, label):
    """Accept ``value`` as either a path to a JSON file or an inline JSON
    object — both are equally natural for an agent to produce. A value that
    names a readable file is read from disk; anything else is parsed as
    inline JSON directly."""
    if not isinstance(value, str) or not value.strip():
        raise ReviewError(f"{label} must not be empty")
    candidate = os.path.abspath(os.path.expanduser(value))
    if os.path.isfile(candidate):
        try:
            with open(candidate, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError) as exc:
            raise ReviewError(f"cannot read {label} file {candidate}: {exc}") from exc
    else:
        try:
            payload = json.loads(value)
        except ValueError as exc:
            raise ReviewError(
                f"{label} is neither a readable file path nor valid inline JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReviewError(f"{label} must decode to a JSON object")
    return payload


def _validate_agent_case(payload):
    """Fail-closed structural check for ``--agent-case``, hand-rolled to
    mirror ``schemas/pre-trade-consultation.schema.json#/properties/agent_case``
    — the same "no jsonschema dependency" posture consequence.py,
    conditions.py, and price_feed.py already keep for their own envelopes.
    Both ``for`` and ``against`` are required once ``agent_case`` is sent at
    all: owner ruling 2026-07-27, the agent lists the case for and against
    and does not take a position, so a one-sided submission is refused
    rather than accepted."""
    if not isinstance(payload, dict):
        raise ReviewError("--agent-case must be a JSON object")
    unknown = set(payload) - {"for", "against"}
    if unknown:
        raise ReviewError("--agent-case has unknown fields: " + ", ".join(sorted(unknown)))
    for side in ("for", "against"):
        if side not in payload:
            raise ReviewError(f"--agent-case must carry both 'for' and 'against' (missing {side!r})")
        claims = payload[side]
        if not isinstance(claims, list):
            raise ReviewError(f"--agent-case.{side} must be a list")
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict) or set(claim) != {"claim", "provenance"}:
                raise ReviewError(
                    f"--agent-case.{side}[{index}] must be an object with exactly "
                    "'claim' and 'provenance'")
            if not isinstance(claim["claim"], str) or not claim["claim"].strip():
                raise ReviewError(f"--agent-case.{side}[{index}].claim must be a non-empty string")
            if claim["provenance"] not in AGENT_CASE_PROVENANCE:
                raise ReviewError(
                    f"--agent-case.{side}[{index}].provenance must be one of "
                    + ", ".join(AGENT_CASE_PROVENANCE))


def _json_safe_premise(normalized):
    """consequence.validate_premise()'s normalized premise carries a real
    ``datetime.date`` for ``date``; every other field is already JSON-safe.
    Converted once, here, so the hash seed, the stored row, and the emitted
    JSON all see the identical string form rather than three independent
    ``str()`` calls that could drift apart."""
    premise = dict(normalized)
    premise["date"] = premise["date"].isoformat()
    return premise


def _consultation_id(premise, basis, created, consequence_frozen, rule_collisions):
    """Engine-assigned, content-addressed identity — the same convention
    session.py uses for ``snapshot_id``/``adjustment_id``/``reconciliation_id``.

    Seeded on the frozen premise/basis/created *and* the computed
    consequence/rule_collisions — not on premise/basis/created alone.
    ``--cash``, ``--prices``, ``--driver-map``, ``--instrument-map``, and the
    position cap override can each change the computed answer without
    changing the premise text itself; naming each of those in the seed would
    still leave the next such input open the same way (external review: a
    same-day, same-premise call with a different ``--cash`` anchor produced
    the *same* id for two materially different frozen answers, and
    ``_fold_consultations``' latest-wins semantics silently treated the
    second as superseding the first — a ``--resolve`` naming that id then
    targets whichever one happened to be folded last). Seeding on the frozen
    result instead closes the whole class at once: any input that changes
    the answer necessarily changes what gets hashed, without this function
    having to enumerate that input by name.

    Two byte-identical inputs still produce a byte-identical seed and
    therefore the same id — ``_append_consultation_row`` relies on this for
    its no-op-repeat idempotency — and ``created`` stays in the seed so an
    unchanged premise asked again on a different day mints a fresh
    consultation rather than silently reusing yesterday's answer."""
    seed = session.canonical({"premise": premise, "basis": basis, "created": created,
                              "consequence": consequence_frozen, "rule_collisions": rule_collisions})
    return "consult-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _fold_consultations(rows):
    """Latest row per ``consultation_id``. File order decides ties — the same
    supersede-by-append convention ``conditions.fold_slots`` documents: a
    resolution is a new row carrying the same id, never a rewrite of the old
    one, and the last one written is the current fact."""
    latest = {}
    for row in rows:
        if isinstance(row, dict) and row.get("consultation_id"):
            latest[row["consultation_id"]] = row
    return latest


def _consultation_reconciliation(root, rows, date_end):
    """Reconcile every unsettled ``consider`` consultation against the
    transaction record, for ``_build_plan`` (#317; #429's rule one layer up —
    a stored answer nothing reads is a defect, and until this, nothing
    outside ``consider`` itself ever read ``pre_trade_consultations.jsonl``).

    For each folded consultation still at ``decision: "open"`` (a resolved
    one never reaches here — ``_fold_consultations`` already picked the
    latest row per id), search ``rows`` — trade_recap-shaped dicts carrying
    ``ticker``/``side``/``qty``/``price``/``date`` as a real ``datetime.date``,
    the same shape ``_ledger_trade_events``, ``_rows_from_ledger``, and
    ``trade_recap.load`` all produce — for a trade of the identical ticker and
    side, dated on or after the consultation's own ``created`` day and on or
    before ``date_end``. The earliest such trade, when more than one
    qualifies, is what gets reported. The caller decides which loader feeds
    ``rows``; this function only ever searches what it is given, and a
    synthesized position row has no business here — see
    ``_ledger_trade_events`` for why ``_build_plan`` uses that one.

    This states a fact, never a cause. A ``matched`` result means a
    qualifying trade exists in the record inside that window — it is not,
    and must never be read as, evidence the user traded *because of* this
    consultation. The same boundary schemas/condition-check.schema.json draws
    around ``user_response`` (an engine-computed verdict is never substituted
    for the user's own word) applies here: this function only ever reads
    ``decision``, never writes it — that field moves through
    ``consider --resolve`` alone.

    Returns ``{"items": [...], "summary": {...}}``. ``items`` is capped at
    ``CONSULTATION_RECONCILE_CAP``, oldest ``created`` first — the bounded-
    plan-surface shape ``_condition_due`` uses, and for the same reason
    (the comment on ``CONDITION_LOOKUP_CAP``): the plan is re-sent as agent
    context on every later turn, so a user who never resolves old
    consultations must not grow it without limit. ``summary`` states the
    total still open, how many are shown, and how many were held back, so a
    capped list can never be mistaken for the complete record.
    """
    consultations = _fold_consultations(thesis.read_jsonl(_consultation_path(root)))
    open_rows = [row for row in consultations.values() if row.get("decision") == "open"]
    open_rows.sort(key=lambda row: (str(row.get("created") or ""), str(row.get("consultation_id") or "")))

    try:
        end = dt.date.fromisoformat(str(date_end)) if date_end else None
    except ValueError:
        end = None

    items = []
    for row in open_rows:
        premise = row.get("premise") or {}
        ticker = premise.get("ticker")
        side = premise.get("side")
        created = row.get("created")
        try:
            created_date = dt.date.fromisoformat(str(created))
        except ValueError:
            created_date = None
        match = None
        if created_date is not None and end is not None and ticker and side:
            candidates = sorted(
                (r for r in rows if r.get("ticker") == ticker and r.get("side") == side
                 and isinstance(r.get("date"), dt.date) and created_date <= r["date"] <= end),
                key=lambda r: r["date"])
            if candidates:
                nearest = candidates[0]
                match = {"date": nearest["date"].isoformat(), "qty": nearest["qty"]}
        items.append({
            "consultation_id": row.get("consultation_id"),
            "created": created,
            "premise": premise,
            "status": "matched" if match else "unmatched",
            "matched_trade": match,
        })

    total = len(items)
    shown = items[:CONSULTATION_RECONCILE_CAP]
    return {"items": shown,
            "summary": {"open_total": total, "shown": len(shown),
                        "beyond_cap": max(0, total - len(shown))}}


def _append_consultation_row(root, row):
    """Append-only writer for ``pre_trade_consultations.jsonl``.

    Neither of this repo's existing append helpers fits. ``session.
    _append_session_rows``' idempotency key is a session_id, and a
    consultation has none — ``consider`` is explicitly session-less (see
    ``cmd_consider``'s docstring). ``ledger.append_events`` stamps every row
    with ``ledger.SCHEMA_V``, which names *ledger.jsonl's* own shape;
    borrowing it would tie an unrelated file's rows to a version number that
    can change for reasons that have nothing to do with this schema
    (docs/development-guide.md section 7's "two readers, one fact", one layer
    down — a stamp instead of a derivation, but the same silent-drift shape).

    So this hand-rolls the one property both of those helpers guarantee and
    this file still needs: build the complete line up front and issue exactly
    one ``write()`` call, so a concurrent reader never observes a torn line —
    the same trailing-newline guard ``_append_session_rows`` uses against a
    prior crash leaving the file without one. Idempotency is content-based
    rather than session-keyed: if the current latest row for this
    ``consultation_id`` is already byte-identical to ``row``, appending is
    skipped — a retried ``consider`` or ``--resolve`` call is a no-op, not a
    duplicate line.
    """
    path = _consultation_path(root)
    existing = thesis.read_jsonl(path)
    current = _fold_consultations(existing).get(row.get("consultation_id"))
    if current is not None and session.canonical(current) == session.canonical(row):
        return {"path": path, "appended": 0, "status": "no-op"}
    os.makedirs(root, exist_ok=True)
    prefix = ""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                prefix = "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(prefix + json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {"path": path, "appended": 1, "status": "appended"}


def _cmd_consider_resolve(root, consultation_id, decision, language):
    """``--resolve``: append a new row recording what the user did, never
    rewrite the old one (this repo decides supersession by chain, never by
    mutating history — ``conditions.fold_slots``, conditions.py:401)."""
    path = _consultation_path(root)
    current = _fold_consultations(thesis.read_jsonl(path)).get(consultation_id)
    if current is None:
        raise ReviewError(
            f"no consultation matching {consultation_id!r} in {path} — --resolve only applies "
            "to a consultation_id `consider` itself returned")
    updated = dict(current)
    updated["decision"] = decision
    updated["decided_on"] = dt.date.today().isoformat()
    report = _append_consultation_row(root, updated)
    _emit({"status": "resolved", "root": root, "language": language,
           "consultation_id": consultation_id, "decision": decision,
           "consultation": updated, "append": report})


def cmd_consider(args):
    """Layer 2 entry point (docs/decision-fomo-kernel-shape.md §3-4): the
    deterministic consequence of one hypothetical trade against the book the
    product already stores, asked away from any review — "I'm thinking of
    buying NVDA, what does that do to my book?". Two independent modes on one
    subcommand, matching the CLI whitelist's single ``consider`` entry
    (AGENTS.md, SKILL.md, references/agent-boundaries.md):

      --premise <path-or-inline-JSON>    compute and record a new consultation
      --resolve <consultation_id> --decision {acted,declined,modified}
                                          record what the user did with one

    Read-only with respect to review state: unlike every other mutating
    command in this file, ``consider`` never writes rules.jsonl, never calls
    problems.check_rules, and never creates or touches a session. The one
    thing it persists is its own append-only
    ``<root>/pre_trade_consultations.jsonl``
    (schemas/pre-trade-consultation.schema.json), registered in coach.py's
    DATA_FILES so data-export and data-reset see it like every other stored
    file (#452 is exactly the bug that shipped when a file skipped that step).

    Every stored field is a frozen value, never a pointer into mutable state
    (the frozen-subject design ratified in issue #446's specification
    comment) — a later ``--resolve``, or next review, must see what the
    engine actually said *at the time*, not what it would say recomputed
    against a ledger that has since grown. ``basis`` freezes which book
    answered (issue #456): the CSV/FIFO path a review uses, or a
    reconstruction from the local ledger when no CSV is handed over — the two
    can disagree about a position's weight, and how many days stale the
    record was when asked.
    """
    root = os.path.abspath(os.path.expanduser(args.root or session.default_root()))
    language = card_renderer.resolve_language(args.language)

    if args.resolve:
        conflicting = [name for name, value in (
            ("--premise", args.premise), ("CSV paths", args.paths),
            ("--prices", args.prices), ("--driver-map", args.driver_map),
            ("--instrument-map", args.instrument_map), ("--cash", args.cash),
            ("--agent-case", args.agent_case),
        ) if value]
        if conflicting:
            raise ReviewError("--resolve takes no premise; remove " + ", ".join(conflicting))
        if not args.decision:
            raise ReviewError("--resolve requires --decision {acted,declined,modified}")
        _cmd_consider_resolve(root, args.resolve, args.decision, language)
        return
    if args.decision:
        raise ReviewError("--decision only applies together with --resolve")
    if not args.premise:
        raise ReviewError("consider requires --premise, or --resolve together with --decision")

    premise_payload = _load_json_arg(args.premise, "--premise")
    rows, basis_source = _consider_rows(args, root)

    if args.driver_map:
        trade_recap.load_driver_map(os.path.abspath(os.path.expanduser(args.driver_map)))
    if args.instrument_map:
        instruments.load_map(os.path.abspath(os.path.expanduser(args.instrument_map)))

    last_px, fx = None, None
    if args.prices:
        try:
            feed = price_feed.load(os.path.abspath(os.path.expanduser(args.prices)))
        except price_feed.PriceFeedError as exc:
            raise ReviewError(f"price feed rejected: {exc}") from exc
        last_px = {ticker: row["close"] for ticker, row in feed["prices"].items()}
        fx = price_feed.fx_rates(feed)

    cash_anchor = None
    if args.cash:
        try:
            cash_anchor = json.loads(args.cash)
        except ValueError as exc:
            raise ReviewError(f"--cash is not valid JSON: {exc}") from exc

    agent_case = None
    if args.agent_case:
        agent_case = _load_json(os.path.abspath(os.path.expanduser(args.agent_case)), "--agent-case")
        _validate_agent_case(agent_case)

    max_pos_override = _position_cap_override(root)

    try:
        result = consequence.consequence(rows, premise_payload, last_px=last_px,
                                         max_pos_override=max_pos_override,
                                         cash_anchor=cash_anchor, fx=fx)
    except consequence.ConsequenceError as exc:
        raise ReviewError(str(exc)) from exc

    muted_ids = _muted_rule_ids(root)
    rules_report = problems.load_rules_report(os.path.join(root, "rules.jsonl"), muted_ids)
    collisions = consequence.rule_collision(rows, premise_payload, rules_report,
                                            last_px=last_px, max_pos_override=max_pos_override,
                                            cash_anchor=cash_anchor, fx=fx)

    premise_stored = _json_safe_premise(result["premise"])
    created = dt.date.today().isoformat()
    as_of = rows[-1]["date"]
    basis = {"source": basis_source, "as_of": as_of.isoformat(),
             "stale_days": (dt.date.today() - as_of).days}
    # Built once and reused for both the id seed and the stored field below —
    # a second, separately-assembled copy is exactly the "two readers, one
    # fact" shape this fix exists to close (docs/development-guide.md
    # section 7): the id must hash what the row actually carries, not a
    # parallel reconstruction of it that could drift.
    consequence_stored = {"before": result["before"], "after": result["after"],
                          "delta": result["delta"], "disclosures": result["disclosures"]}

    row = {
        "consultation_id": _consultation_id(premise_stored, basis, created,
                                            consequence_stored, collisions),
        "created": created,
        "premise": premise_stored,
        "basis": basis,
        "consequence": consequence_stored,
        "rule_collisions": collisions,
        "decision": "open",
        "decided_on": None,
    }
    if agent_case is not None:
        row["agent_case"] = agent_case

    report = _append_consultation_row(root, row)
    _emit({"status": "considered", "root": root, "language": language,
           "consultation": row, "append": report})


def cmd_doctor(args):
    """Report optional runtime dependencies and what each unlocks (#322).

    Read-only preflight. The engine fail-soft degrades and never crashes on a
    missing package, so a user who skipped ``pip install`` silently loses the
    price-dependent half of the card — disclosed only as a data-coverage gap
    that misattributes the cause. ``doctor`` names the real cause: it lists each
    runtime dependency, whether it imports, and the card capability it unlocks,
    then exits non-zero when a full-experience dependency (yfinance / pandas) is
    missing so an installer or CI can gate on it. ``rich`` is optional for the v2
    card and never gates the exit code.
    """
    checks = (
        ("yfinance", True, "current prices, unrealized P&L, alpha/beta, market context, FX"),
        ("pandas", True, "the P&L curve and the alpha/beta regression"),
        ("rich", False, "the v1 terminal card (the v2 review card renders without it)"),
    )
    lines = ["fomo-kernel runtime dependencies:"]
    missing_full = []
    for module, full_experience, unlocks in checks:
        try:
            __import__(module)
            present = True
        except Exception:                        # ImportError or a broken install
            present = False
            if full_experience:
                missing_full.append(module)
        lines.append(f"  [{'ok  ' if present else 'MISS'}] {module:9s} — {unlocks}")
    if missing_full:
        lines += ["",
                  "Full-experience dependencies missing: " + ", ".join(missing_full) + ".",
                  "The card silently drops price-dependent sections without them. Install with:",
                  "  pip install -r skills/fomo-kernel/requirements.txt"]
    print("\n".join(lines))
    if missing_full:
        raise SystemExit(1)


def build_parser():
    parser = argparse.ArgumentParser(description="fomo-kernel stable review orchestration")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="run engine and emit a resumable Review Plan")
    prepare.add_argument("paths", nargs="*", help="normalized trade CSV files")
    prepare.add_argument("--root")
    prepare.add_argument("--language", default="en",
                         help="any language tag; unsupported tags fall back to en "
                              "(supported locales are the copy/<locale>.json files)")
    prepare.add_argument("--route", default="auto",
                         choices=("auto", "first_review", "weekly_review", "snapshot_review"))
    prepare.add_argument("--test-drive", action="store_true")
    prepare.add_argument("--session-nonce", default="")
    prepare.add_argument("--driver-map")
    prepare.add_argument("--instrument-map")
    prepare.add_argument("--cash", help="TR_CASH JSON string")
    prepare.add_argument("--prices",
                         help="agent-supplied price envelope (references/price-feed.md); "
                              "use when the host cannot retrieve prices itself")
    prepare.add_argument("--condition-checks", dest="condition_checks",
                         help="this period's results for state_snapshot.condition_slots_due "
                              "(references/condition-slots.md); rerun prepare with it so a "
                              "crossing can be asked about rather than only recorded")
    prepare.add_argument("--snapshot-json",
                         help="normalized position-snapshot facts; valid only for snapshot_review")
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
    capture = sub.add_parser("capture", help="light-tier capture-only action (#237)")
    capture.add_argument("--session-id", required=True)
    capture.add_argument("--root")
    capture.add_argument("--entries", required=True)
    capture.set_defaults(func=cmd_capture)
    resume = sub.add_parser("resume")
    resume.add_argument("--session-id")
    resume.add_argument("--root")
    resume.add_argument("--question-surfaces",
                        help="private AI-authored surfaces to validate and freeze before presentation")
    resume.set_defaults(func=cmd_resume)
    render = sub.add_parser("render")
    render.add_argument("--session-id", required=True)
    render.add_argument("--root")
    render.add_argument("--format", choices=("json", "private-markdown", "public-markdown"),
                        default="json", help="emit JSON (default) or one canonical Markdown card")
    render.set_defaults(func=cmd_render)
    repair = sub.add_parser("repair-projections")
    repair.add_argument("--root")
    repair.set_defaults(func=cmd_repair)
    setcap = sub.add_parser("set-cap",
                            help="record the user's standing single-position cap (#324)")
    setcap.add_argument("--root")
    cap_group = setcap.add_mutually_exclusive_group(required=True)
    cap_group.add_argument("--pct",
                           help="single-position cap as a fraction in (0,1), e.g. 0.25 for 25%%")
    cap_group.add_argument("--clear", action="store_true",
                           help="remove the override and revert to the universal default")
    setcap.set_defaults(func=cmd_set_cap)
    mute = sub.add_parser("mute-rule",
                          help="stop asking about a rule while its statistics keep running (#416)")
    mute.add_argument("--root")
    mute.add_argument("--rule-id", required=True)
    mute.add_argument("--unmute", action="store_true",
                      help="bring a muted rule back into the card's rotation")
    mute.set_defaults(func=cmd_mute_rule)
    consider = sub.add_parser(
        "consider",
        help="deterministic consequence of one hypothetical trade against the current book "
             "(Layer 2, docs/decision-fomo-kernel-shape.md §3-4)")
    consider.add_argument("paths", nargs="*",
                          help="normalized trade CSV files; omit to reconstruct the book from "
                               "<root>/ledger.jsonl instead")
    consider.add_argument("--root")
    consider.add_argument("--premise",
                          help="the hypothetical trade: a path to a JSON file, or an inline JSON "
                               "object (schemas/trade-premise.schema.json)")
    consider.add_argument("--resolve", metavar="CONSULTATION_ID",
                          help="record what the user did with a prior consultation; takes no "
                               "premise")
    consider.add_argument("--decision", choices=CONSIDER_DECISIONS,
                          help="required together with --resolve")
    consider.add_argument("--prices",
                          help="agent-supplied price envelope (references/price-feed.md)")
    consider.add_argument("--driver-map")
    consider.add_argument("--instrument-map")
    consider.add_argument("--cash", help="TR_CASH-shaped JSON string: a single "
                                        "{as_of,amount,currency} anchor, or a list of them")
    consider.add_argument("--agent-case",
                          help="optional path to a JSON file: the structured case for and "
                               "against, {for: [...], against: [...]} "
                               "(references/trade-consequence.md)")
    consider.add_argument("--language", default="en",
                          help="any language tag; unsupported tags fall back to en")
    consider.set_defaults(func=cmd_consider)
    doctor = sub.add_parser(
        "doctor", help="check optional runtime dependencies and what each unlocks (#322)")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except (ReviewError, session.SessionError, thesis.ThesisError, card_renderer.RenderError,
            question_surface.QuestionSurfaceError) as exc:
        _emit({"status": "error", "error": str(exc)})
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
