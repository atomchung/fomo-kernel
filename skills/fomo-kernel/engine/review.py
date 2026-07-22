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
import horizon
import ledger
import problems
import question_surface
import revisit
import session
import snapshot_adapter
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
    card["acct_perf"] = {"note": "accounting_reconciliation"}

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


def _initial_thesis_options(language):
    """Canonical first-review entry-motive choices (#291), localized labels.

    Engine owns the stable codes; the copy layer localizes the labels. `skip`
    is the standard escape consistent with the other kinds.
    """
    copy = card_renderer.load_copy(language)
    labels = copy.get("initial_thesis_choices") or {}
    en = copy["language"] == "en"
    descriptions = {
        "planned_entry": ("進場前就有明確的論點。",
                          "You had an explicit thesis before entering."),
        "momentum_follow": ("追了價格動能或 FOMO。",
                            "You chased price momentum or FOMO."),
        "external_call": ("KOL、朋友或研究報告推薦的。",
                          "A KOL, friend, or research recommendation drove it."),
        "no_clear_thesis": ("在沒有明確論點下持有。",
                            "You are holding without a clear thesis."),
        "skip": ("這次先不替進場動機下定論。",
                 "Leave the entry motive unresolved for now."),
    }
    return [{"value": key, "label": labels[key],
             "description": descriptions[key][1 if en else 0]}
            for key in ("planned_entry", "momentum_follow", "external_call",
                        "no_clear_thesis", "skip")]


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
    # #291: the caller (_question_queue) applies RULE_BREACH_LIMIT so it can
    # record the trimmed rows in the selection report. Direct callers see the
    # full ranked list, still bounded by the two-per-key/worsening cadence above.
    return candidates


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
    table = {
        "pnl_impact": ("它是你本週影響損益最大的部位",
                       "it is the position with the largest P&L impact this week"),
        "position_cost": ("它是你本週成本最大的部位",
                          "it is your largest position by cost this week"),
        "exit_notional": ("它是你近期金額最大的出場之一",
                          "it is one of your largest recent exits by amount"),
    }
    row = table.get(basis)
    if not row:
        return None
    return row[1] if str(language).lower().startswith("en") else row[0]


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
                    route=None, missing_thesis_positions=None):
    """Return (queue, selection_report). The report states, plan-internally, how
    the route's density band was filled: the eligible/selected counts, why the
    queue fell short of the route minimum, and every candidate rejected with its
    reason (#291). It is QA/agent-facing and never rendered on the card."""
    policy = QUESTION_POLICY.get(route) or QUESTION_POLICY["weekly_review"]
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
    thesis_states = thesis_states or active
    horizon_by_cycle = {row.get("cycle_id"): row for row in (horizon_markers or [])
                        if row.get("cycle_id")}
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
    if route == "first_review":
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
                                draft_week=state.get("date_end"))
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


def _build_plan(card, state, engine_meta, root, paths, route, language, fingerprint, nonce, persist,
                recent_exits=None, ledger_ingest=None, revisit_ingest=None,
                due_revisits=None, exit_backlog=None, problem_stats=None):
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
    closed_rows = [row for row in thesis_states if row.get("position_status") == "closed"]
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
    question_queue, question_selection = _question_queue(
        card, state, active, previous, language, recent_exits, by_cycle, due_revisits,
        problem_stats, rule_history, horizon_markers, route=route,
        missing_thesis_positions=missing)
    plan = {
        "schema_version": 2,
        "engine_version": _engine_version(),
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
                           "review_progress": {
                               "completed_reviews_before_start": completed_reviews,
                               "returning": completed_reviews > 0,
                           },
                           "cadence": cadence,
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
                           "headline_motive_events": headline_motive_events,
                           "market_context": state.get("market_context"),
                           "horizon_markers": horizon_markers,
                           "revisit_ingest": revisit_ingest},
        "question_queue": question_queue,
        "missing_thesis_positions": missing,
        "authoring_contract": _authoring_contract(route),
        "card_plan": {"candidate_rules": _candidate_rules(card, state, language),
                      "question_limit": question_selection["max"],
                      "question_policy": {"route": question_selection["route"],
                                          "min": question_selection["min"],
                                          "max": question_selection["max"]},
                      "question_selection": question_selection,
                      "horizon_ids": ["weeks", "quarters", "years"],
                      "required_honesty_keys": required_honesty_keys},
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
    language = args.language
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
    fingerprint = _fingerprint(paths, language, route, prepared=prepared, nonce=args.session_nonce or "")
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
        recent_exits, due_revisits, exit_backlog, revisit_ingest = [], [], None, None
        problem_stats = None
    else:
        recent_exits, due_revisits, exit_backlog, revisit_ingest = \
            _prepare_exit_capture(root, state, persist)
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
    next_action = ("for question_opportunity rows, author a private surface and bind it with "
                   "resume --question-surfaces, or keep the engine question/options fallback; "
                   "then ask every required question, author thesis_updates and prose-only narrative, "
                   "and run preview")
    if not persist:
        # The test drive lives in an isolated root that preview/finalize cannot
        # discover on their own; without this handoff they report "pending session
        # not found" against the default root.
        next_action += f"; test drive is isolated — pass --root {root} to every later command"
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
    agent_updates = _assign_thesis_ids(plan, _validate_thesis_completeness(plan, answers))
    cycle_relinks = list(
        ((plan.get("state_snapshot") or {}).get("thesis_cycle_relinks") or [])
    )
    updates = cycle_relinks + agent_updates
    decisions = thesis.build_decision_events(plan, answers, updates)
    exit_narratives = _build_exit_narratives(plan, answers, amap)
    revisit_resolutions = _build_revisit_resolutions(plan, answers, amap)
    rule_breach_decisions = _build_rule_breach_decisions(plan, answers, amap)
    headline_motive_events = _build_headline_motive_events(plan, answers, amap)
    initial_thesis_events = _build_initial_thesis_events(plan, answers, amap)
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
    # Absent-when-empty, same replay-compatibility contract as the keys above:
    # first-review-only, and only when at least one entry motive was classified.
    if initial_thesis_events:
        bundle["initial_thesis_events"] = initial_thesis_events
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
    render.set_defaults(func=cmd_render)
    repair = sub.add_parser("repair-projections")
    repair.add_argument("--root")
    repair.set_defaults(func=cmd_repair)
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
