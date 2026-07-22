#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic adapter from a normalized position snapshot to review artifacts.

This module deliberately has no CLI and performs no writes.  Runtime agents enter
through ``review.py``; the orchestrator calls :func:`prepare`, then owns pending
state, ledger projection, and canonical-session durability.

The accepted JSON envelope is intentionally small::

    {
      "as_of": "2026-07-17",
      "positions": [
        {"ticker": "NVDA", "shares": 40, "avg_cost": 152.3,
         "market": "US", "currency": "USD", "market_value": 6800}
      ],
      "cash": {"USD": 8200},
      "fx": {"USD": 1, "TWD": 0.0307},
      "is_complete": true
    }

``fx`` values are USD per unit of the original currency.  Missing FX is an
honest degradation, not an implicit one-to-one conversion: mixed-currency
snapshots without every held-currency rate keep their original-currency facts
but do not produce global weights.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import math
import os
import re
import threading

import instruments
import ledger
import trade_recap


class SnapshotError(ValueError):
    """The normalized snapshot violates the deterministic input contract."""


ENVELOPE_KEYS = {"as_of", "positions", "cash", "fx", "is_complete"}
POSITION_KEYS = {"ticker", "shares", "avg_cost", "market_value", "market", "currency"}
SUPPORTED_MARKETS = {"US", "TW"}
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.^_-]{0,31}$")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_POLICY_LOCK = threading.RLock()


def _strict_date(value, label):
    if not isinstance(value, str) or not ISO_DATE_RE.fullmatch(value):
        raise SnapshotError(f"{label} must be an ISO date (YYYY-MM-DD)")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise SnapshotError(f"{label} is not a valid calendar date") from exc


def _today(value):
    if value is None:
        return dt.date.today()
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return _strict_date(value, "today")


def _number(value, label, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SnapshotError(f"{label} must be a JSON number")
    number = float(value)
    if not math.isfinite(number):
        raise SnapshotError(f"{label} must be finite")
    if positive and number <= 0:
        raise SnapshotError(f"{label} must be greater than zero")
    return number


def _product(left, right, label):
    value = left * right
    if not math.isfinite(value) or value <= 0:
        raise SnapshotError(f"{label} produces an invalid valuation")
    return value


def _ticker(value, index):
    if not isinstance(value, str):
        raise SnapshotError(f"positions[{index}].ticker must be a string")
    symbol = value.strip().upper()
    if not TICKER_RE.fullmatch(symbol):
        raise SnapshotError(f"positions[{index}].ticker is not a normalized symbol")
    return symbol


def _market(value, index):
    if not isinstance(value, str):
        raise SnapshotError(f"positions[{index}].market must be a string")
    market = value.strip().upper()
    if market not in SUPPORTED_MARKETS:
        supported = ", ".join(sorted(SUPPORTED_MARKETS))
        raise SnapshotError(f"positions[{index}].market must be one of: {supported}")
    return market


def _currency(value, label):
    if not isinstance(value, str):
        raise SnapshotError(f"{label} must be a string")
    currency = value.strip().upper()
    if not CURRENCY_RE.fullmatch(currency):
        raise SnapshotError(f"{label} must be a three-letter currency code")
    return currency


def _normalize_position(raw, index):
    if not isinstance(raw, dict):
        raise SnapshotError(f"positions[{index}] must be an object")
    extra = set(raw) - POSITION_KEYS
    if extra:
        raise SnapshotError(
            f"positions[{index}] has unknown fields: " + ", ".join(sorted(extra))
        )
    missing = {"ticker", "shares", "market", "currency"} - set(raw)
    if missing:
        raise SnapshotError(
            f"positions[{index}] is missing: " + ", ".join(sorted(missing))
        )
    row = {
        "ticker": _ticker(raw["ticker"], index),
        "shares": _number(raw["shares"], f"positions[{index}].shares", positive=True),
        "market": _market(raw["market"], index),
        "currency": _currency(raw["currency"], f"positions[{index}].currency"),
        "avg_cost": None,
        "market_value": None,
    }
    if raw.get("avg_cost") is not None:
        row["avg_cost"] = _number(
            raw["avg_cost"], f"positions[{index}].avg_cost", positive=True
        )
    if raw.get("market_value") is not None:
        row["market_value"] = _number(
            raw["market_value"], f"positions[{index}].market_value", positive=True
        )
    return row


def _merge_positions(rows):
    """Combine same-instrument rows without asking an agent to calculate.

    A ticker cannot denote two markets or currencies in the current holdings
    contract, which is keyed by ticker.  Such input fails closed.  Average cost
    remains available only when every merged row supplied it; market value follows
    the same all-or-missing rule, so later valuation never mixes denominators.
    """
    grouped = {}
    counts = {}
    for row in rows:
        ticker = row["ticker"]
        if ticker not in grouped:
            grouped[ticker] = dict(row)
            counts[ticker] = 1
            continue
        current = grouped[ticker]
        if (current["market"], current["currency"]) != (row["market"], row["currency"]):
            raise SnapshotError(
                f"{ticker} appears with conflicting market or currency values"
            )
        old_shares = current["shares"]
        new_shares = old_shares + row["shares"]
        if not math.isfinite(new_shares):
            raise SnapshotError(f"{ticker} merged shares are not finite")
        if current["avg_cost"] is not None and row["avg_cost"] is not None:
            numerator = (
                old_shares * current["avg_cost"] + row["shares"] * row["avg_cost"]
            )
            current["avg_cost"] = numerator / new_shares
        else:
            current["avg_cost"] = None
        if current["market_value"] is not None and row["market_value"] is not None:
            total_value = current["market_value"] + row["market_value"]
            if not math.isfinite(total_value) or total_value <= 0:
                raise SnapshotError(f"{ticker} merged market value is invalid")
            current["market_value"] = total_value
        else:
            current["market_value"] = None
        current["shares"] = new_shares
        counts[ticker] += 1
    return [grouped[ticker] for ticker in sorted(grouped)], sum(counts.values()) - len(grouped)


def _normalize_cash(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SnapshotError("cash must be an object of currency to balance")
    out = {}
    for key, value in raw.items():
        currency = _currency(key, "cash currency")
        if currency in out:
            raise SnapshotError(f"cash contains duplicate normalized currency: {currency}")
        out[currency] = _number(value, f"cash.{currency}")
    return dict(sorted(out.items()))


def _normalize_fx(raw):
    if raw is None:
        return {"USD": 1.0}
    if not isinstance(raw, dict):
        raise SnapshotError("fx must be an object of currency to USD-per-unit rate")
    out = {}
    for key, value in raw.items():
        currency = _currency(key, "fx currency")
        if currency in out:
            raise SnapshotError(f"fx contains duplicate normalized currency: {currency}")
        out[currency] = _number(value, f"fx.{currency}", positive=True)
    if "USD" in out and not math.isclose(out["USD"], 1.0, rel_tol=0, abs_tol=1e-12):
        raise SnapshotError("fx.USD must equal one")
    out.setdefault("USD", 1.0)
    return dict(sorted(out.items()))


def _load(path, today):
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"cannot read normalized snapshot: {exc}") from exc
    if not isinstance(raw, dict):
        raise SnapshotError("normalized snapshot must be a JSON object")
    extra = set(raw) - ENVELOPE_KEYS
    if extra:
        raise SnapshotError("snapshot has unknown fields: " + ", ".join(sorted(extra)))
    missing = {"as_of", "positions"} - set(raw)
    if missing:
        raise SnapshotError("snapshot is missing: " + ", ".join(sorted(missing)))
    as_of = _strict_date(raw["as_of"], "as_of")
    if as_of > today:
        raise SnapshotError("as_of cannot be in the future")
    positions_raw = raw["positions"]
    if not isinstance(positions_raw, list) or not positions_raw:
        raise SnapshotError("positions must be a non-empty array")
    positions, merged_rows = _merge_positions(
        [_normalize_position(row, index) for index, row in enumerate(positions_raw)]
    )
    is_complete = raw.get("is_complete", True)
    if not isinstance(is_complete, bool):
        raise SnapshotError("is_complete must be a boolean")
    return {
        "as_of": as_of.isoformat(),
        "positions": positions,
        "cash": _normalize_cash(raw.get("cash")),
        "fx": _normalize_fx(raw.get("fx")),
        "is_complete": is_complete,
        "merged_rows": merged_rows,
        "input_rows": len(positions_raw),
    }


@contextlib.contextmanager
def _policy_context(driver_map, instrument_map):
    """Use existing policy functions without leaking their mutable module globals."""
    with _POLICY_LOCK:
        prior_driver = dict(trade_recap._DRIVER_MAP)
        prior_driver_skipped = trade_recap._DM_SKIPPED
        prior_lens = trade_recap._LENS
        prior_instruments = {key: dict(value) for key, value in instruments._MAP.items()}
        prior_instrument_result = dict(instruments._LOAD_RESULT)
        try:
            trade_recap._DRIVER_MAP = dict(trade_recap.DRIVER_FALLBACK)
            trade_recap._DM_SKIPPED = 0
            driver_loaded = trade_recap.load_driver_map(driver_map) if driver_map else 0
            driver_result = {
                "loaded": driver_loaded,
                "skipped": trade_recap._DM_SKIPPED,
                "path": os.path.abspath(driver_map) if driver_map else None,
            }
            instruments.reset_map()
            instrument_result = instruments.load_map(instrument_map) if instrument_map else {
                "loaded": 0, "skipped": 0, "error": None
            }
            instrument_result = dict(instrument_result)
            instrument_result["path"] = os.path.abspath(instrument_map) if instrument_map else None
            philosophy = trade_recap.load_lens()
            yield driver_result, instrument_result, philosophy
        finally:
            trade_recap._DRIVER_MAP = prior_driver
            trade_recap._DM_SKIPPED = prior_driver_skipped
            trade_recap._LENS = prior_lens
            instruments._MAP = prior_instruments
            instruments._LOAD_RESULT = prior_instrument_result


def _valuation(snapshot):
    positions = snapshot["positions"]
    all_market_value = all(row["market_value"] is not None for row in positions)
    all_avg_cost = all(row["avg_cost"] is not None for row in positions)
    basis = "market_value" if all_market_value else "cost" if all_avg_cost else None
    values = {}
    if basis == "market_value":
        values = {row["ticker"]: row["market_value"] for row in positions}
    elif basis == "cost":
        values = {
            row["ticker"]: _product(
                row["shares"], row["avg_cost"], f"{row['ticker']} cost basis"
            )
            for row in positions
        }
    return basis, values


def _global_values(snapshot, native_values):
    currencies = sorted({row["currency"] for row in snapshot["positions"]})
    mixed = len(currencies) > 1
    fx_gaps = sorted(currency for currency in currencies if currency not in snapshot["fx"]) if mixed else []
    if not native_values or not snapshot["is_complete"] or fx_gaps:
        return {}, currencies, fx_gaps
    currency_by_ticker = {row["ticker"]: row["currency"] for row in snapshot["positions"]}
    if not mixed:
        return dict(native_values), currencies, []
    values = {}
    for ticker, value in native_values.items():
        converted = value * snapshot["fx"][currency_by_ticker[ticker]]
        if not math.isfinite(converted) or converted <= 0:
            raise SnapshotError(f"{ticker} FX conversion produces an invalid valuation")
        values[ticker] = converted
    return values, currencies, []


def _anchor(snapshot):
    positions = []
    for row in snapshot["positions"]:
        position = {
            "ticker": row["ticker"],
            "shares": row["shares"],
            "market": row["market"],
            "currency": row["currency"],
        }
        if row["avg_cost"] is not None:
            position["avg_cost"] = row["avg_cost"]
        if row["market_value"] is not None:
            position["market_value"] = row["market_value"]
        positions.append(position)
    event = {
        "type": "snapshot",
        "as_of": snapshot["as_of"],
        "source": "user_declared",
        "is_complete": snapshot["is_complete"],
        "positions": positions,
    }
    if snapshot["cash"] is not None:
        event["cash"] = snapshot["cash"]
    return event


def _state_positions(snapshot, anchor):
    # ``ledger.derive_holdings`` correctly ignores incomplete declarations as
    # accounting anchors.  The opening-check card still needs deterministic
    # cycle identities for its bounded, canonical review state, so derive those
    # identities from an in-memory complete view only.  The original anchor
    # keeps ``is_complete:false`` and finalize never projects it to the ledger.
    cycle_anchor = dict(anchor)
    cycle_anchor["is_complete"] = True
    derived = ledger.derive_holdings([cycle_anchor])
    if derived.get("integrity"):
        raise SnapshotError("normalized snapshot could not produce a clean holdings anchor")
    by_ticker = {row["ticker"]: row for row in snapshot["positions"]}
    out = {}
    for ticker, fact in sorted((derived.get("holdings") or {}).items()):
        raw = by_ticker[ticker]
        cost = None
        if raw["avg_cost"] is not None:
            cost = _product(raw["shares"], raw["avg_cost"], f"{ticker} cost basis")
        row = {
            "shares": fact["shares"],
            "cost": round(cost, 2) if cost is not None else None,
            "avg_cost": fact.get("avg_cost"),
            "market": fact.get("market"),
            "currency": fact.get("currency"),
            "cycle_start": fact.get("since"),
            "cycle_id": fact.get("cycle_id"),
            "origin": "snapshot",
        }
        if raw["market_value"] is not None:
            row["market_value"] = raw["market_value"]
        out[ticker] = row
    if len(out) != len(snapshot["positions"]):
        raise SnapshotError("normalized snapshot lost a position while deriving cycles")
    return out


def _holes(dimensions):
    rows = []
    for dimension in trade_recap._rank_holes(dimensions)[:2]:
        rule, quote = trade_recap.card_for(dimension["dim"])
        number_line = trade_recap.number_line(dimension)
        if dimension.get("dim") == "分散" and not dimension.get("max_sector"):
            number_line = (
                f"你持有 {dimension.get('n', 0)} 檔；排除配置型 ETF 後，"
                f"top3 風險部位佔 {float(dimension.get('top3') or 0) * 100:.0f}%；"
                "driver 尚未完整分類"
            )
        rows.append({
            "dim": dimension["dim"],
            "severity": round(float(dimension.get("severity") or 0), 2),
            "tier_weight": trade_recap.HEADLINE_TIER_W.get(dimension.get("tier"), 0.7),
            "number_line": number_line,
            "lens_rule": rule,
            "lens_quote": quote,
            "raw": dimension,
        })
    return rows


def _portfolio_structure_without_weights(positions):
    """Preserve deterministic ETF classification when valuation is unavailable.

    Missing valuation must not erase the snapshot-supported distinction between
    allocation ETFs and focused ETFs.  Aggregate weights stay ``None`` and
    ``by_kind`` stays empty so no consumer can mistake classification coverage
    for a numeric allocation claim.
    """
    allocation = []
    concentrated = []
    missing = []
    for position in sorted(positions, key=lambda row: row["ticker"]):
        meta = instruments.info(position["ticker"])
        if not meta["is_etf"]:
            continue
        row = {"ticker": meta["ticker"], "kind": meta["kind"], "weight": None}
        for key in ("expense_ratio", "tracking_error", "source", "as_of"):
            if key in meta:
                row[key] = meta[key]
        (allocation if meta["allocation_exempt"] else concentrated).append(row)
        absent = [key for key in ("expense_ratio", "tracking_error") if meta.get(key) is None]
        if absent:
            missing.append({"ticker": meta["ticker"], "fields": absent})
    return {
        "schema_version": 1,
        "policy": "allocation_etfs_exempt_single_name;sector_thematic_leveraged_concentrated",
        "allocation_weight": None,
        "concentrated_etf_weight": None,
        "by_kind": {},
        "allocation_etfs": allocation,
        "concentrated_etfs": concentrated,
        "metadata_gaps": missing,
    }


def _honesty(snapshot, currencies, fx_gaps, unclassified, portfolio_structure):
    entries = [{
        "key": "snapshot_scope",
        "status": "limited",
        "data": {"is_complete": snapshot["is_complete"]},
    }]
    if len(currencies) > 1:
        entries.append({
            "key": "currency_mix",
            "status": "partial" if fx_gaps else "converted",
            "data": {"currencies": currencies, "fx_gaps": fx_gaps},
        })
    if unclassified:
        entries.append({
            "key": "unclassified_drivers",
            "status": "partial",
            "data": {"tickers": unclassified},
        })
    gaps = portfolio_structure.get("metadata_gaps") or []
    if gaps:
        entries.append({"key": "etf_metadata", "status": "partial", "data": {"gaps": gaps}})
    return entries


def prepare(path, driver_map=None, instrument_map=None, today=None):
    """Return ``(card, state, meta)`` for one normalized local snapshot.

    No history-only metric is synthesized.  When a complete, consistently valued
    portfolio cannot be put in one currency, ``weights_available`` is false and
    the adapter emits no sizing/diversification dimension.
    """
    snapshot = _load(os.path.abspath(os.path.expanduser(path)), _today(today))
    basis, native_values = _valuation(snapshot)
    global_values, currencies, fx_gaps = _global_values(snapshot, native_values)
    weights_available = bool(global_values)
    anchor = _anchor(snapshot)
    holdings = _state_positions(snapshot, anchor)
    missing_avg_cost = sorted(
        row["ticker"] for row in snapshot["positions"] if row["avg_cost"] is None
    )

    with _policy_context(driver_map, instrument_map) as (driver_result, instrument_result, philosophy):
        dimensions = []
        portfolio_structure = _portfolio_structure_without_weights(snapshot["positions"])
        if weights_available:
            held = {
                row["ticker"]: (row["shares"], global_values[row["ticker"]])
                for row in snapshot["positions"]
            }
            size = trade_recap.dim_size([], held, {})
            diversification = trade_recap.dim_diversify(held, {})
            dimensions = [size, diversification]
            portfolio_structure = instruments.portfolio_analysis(size.get("weights"))
        unclassified = sorted(
            row["ticker"] for row in snapshot["positions"]
            if not instruments.is_diversified_allocation(row["ticker"])
            and trade_recap.driver(row["ticker"])[0] == "未分類"
        )
        top_holes = _holes(dimensions)
        headline = trade_recap._pick_headline(dimensions)
        honesty = _honesty(snapshot, currencies, fx_gaps, unclassified, portfolio_structure)

    summary = {
        "as_of": snapshot["as_of"],
        "positions_n": len(snapshot["positions"]),
        "valuation_basis": basis,
        "weights_available": weights_available,
        "is_complete": snapshot["is_complete"],
        "missing_avg_cost": missing_avg_cost,
        "fx_gaps": fx_gaps,
    }
    currency_meta = {
        "currencies": currencies,
        "mixed": len(currencies) > 1,
        "aggregate_currency": "USD" if len(currencies) > 1 else currencies[0],
        "fx": ({key: value for key, value in snapshot["fx"].items() if key != "USD"} or None),
        "valuation_basis": basis,
    }
    data_integrity = {
        "source": "positions_snapshot",
        "snapshot_complete": snapshot["is_complete"],
        "missing_avg_cost": missing_avg_cost,
        "unclassified_drivers": unclassified,
    }
    if fx_gaps:
        data_integrity["fx_gaps"] = fx_gaps
    if not weights_available:
        if not snapshot["is_complete"]:
            reason = "incomplete_snapshot"
        elif basis is None:
            reason = "incomplete_valuation"
        else:
            reason = "fx_gap"
        data_integrity["weights_unavailable_reason"] = reason

    size = next((row for row in dimensions if row.get("dim") == "部位 sizing"), {})
    diversification = next((row for row in dimensions if row.get("dim") == "分散"), {})
    headline_metric = {"key": None, "value": None}
    if headline:
        if headline.get("dim") == "部位 sizing":
            headline_metric = {"key": "max_pos_pct", "value": headline.get("max_pct")}
        elif headline.get("dim") == "分散":
            headline_metric = {"key": "top3_pct", "value": headline.get("top3")}

    metrics = {
        "max_pos_pct": size.get("max_pct") if size else None,
        "max_pos_ticker": size.get("max_ticker") if size else None,
        "avgdown_count": None,
        "avgdown_breach": None,
        "payoff": None,
        "ai_pct": diversification.get("ai_pct") if diversification else None,
        "max_sector_pct": diversification.get("max_sector_pct") if diversification else None,
        "top3_pct": diversification.get("top3") if diversification else None,
        "n_holdings": len(snapshot["positions"]),
        "exit_severity": None,
        "hold_severity": None,
        "beta": None,
        "alpha_ann": None,
        "alpha_t": None,
        "alpha_credible": None,
    }
    state = {
        "schema_version": 2,
        "snapshot_only": True,
        "snapshot_summary": summary,
        "snapshot_anchor": anchor,
        "date_start": None,
        "date_end": snapshot["as_of"],
        "n_trades": 0,
        "n_round_trips": 0,
        "n_held": len(snapshot["positions"]),
        "headline_dim": headline.get("dim") if headline else None,
        "headline_metric": headline_metric,
        "commitment": None,
        "metrics": metrics,
        "rule": None,
        "insufficient_data": True,
        "holdings": {
            "as_of": snapshot["as_of"],
            "derived_from": "positions_snapshot",
            "is_complete": snapshot["is_complete"],
            "positions": holdings,
        },
        "currency_meta": currency_meta,
        "portfolio_structure": portfolio_structure,
        "cash": None,
        "price_snapshot": None,
        "market_context": None,
        "problem_events": [],
        "problem_opportunities": None,
    }
    card = {
        "schema_version": 1,
        "snapshot_only": True,
        "snapshot_summary": summary,
        "philosophy": philosophy,
        "strength": None,
        "overview": {},
        "what_if": None,
        "ticker_diagnosis": [],
        "thesis_questions": [],
        "top_holes": top_holes,
        "candidate_rules": [],
        "prescriptions": [],
        "alpha_beta_breakdown": {},
        "payoff_attribution": {},
        "dims_raw": dimensions,
        "data_integrity": data_integrity,
        "currency_meta": currency_meta,
        "portfolio_structure": portfolio_structure,
        "cash": None,
        "acct_perf": {},
        "honesty_ledger": honesty,
        "pnl_curve": {"note": "snapshot_only"},
    }
    meta = {
        "source": "positions_snapshot",
        "path": os.path.abspath(os.path.expanduser(path)),
        "anchor": anchor,
        "input_rows": snapshot["input_rows"],
        "positions_n": len(snapshot["positions"]),
        "merged_rows": snapshot["merged_rows"],
        "valuation_basis": basis,
        "weights_available": weights_available,
        "driver_map": driver_result,
        "instrument_map": instrument_result,
    }
    return card, state, meta
