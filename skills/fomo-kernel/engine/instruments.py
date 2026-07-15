#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Instrument policy for portfolio-behaviour diagnostics.

This module intentionally answers one narrow, deterministic question: should a
position be treated as a single-name concentration risk, or as a diversified
allocation instrument?  It does not fetch market data and it never guesses that
an unknown ticker is an ETF.  Callers may supply a local JSON map through
``TR_INSTRUMENT_MAP`` for instruments not covered by the conservative fallback.

Map shape::

    {
      "ACWI": {
        "kind": "broad_market_etf",
        "expense_ratio": 0.0032,
        "tracking_error": null
      }
    }

Only broad-market, regional, bond, and commodity ETFs receive the allocation
exemption.  Sector/thematic and leveraged ETFs remain concentration risk.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict


ALLOCATION_KINDS = {
    "broad_market_etf",
    "regional_etf",
    "bond_etf",
    "commodity_etf",
}
CONCENTRATED_ETF_KINDS = {"sector_etf", "thematic_etf", "leveraged_etf"}
ETF_KINDS = ALLOCATION_KINDS | CONCENTRATED_ETF_KINDS
VALID_KINDS = ETF_KINDS | {"equity", "fund", "cash", "unknown"}


def _rows(kind, tickers):
    return {ticker: {"kind": kind} for ticker in tickers.split()}


FALLBACK = {}
FALLBACK.update(_rows("broad_market_etf", "SPY VOO IVV VTI VT SCHB ITOT ACWI"))
FALLBACK.update(_rows("regional_etf", "VXUS VEA VWO EWY EWT EWJ VGK"))
FALLBACK.update(_rows("bond_etf", "BND AGG IEF TLT SHY SGOV TIP"))
FALLBACK.update(_rows("commodity_etf", "IAU GLD SLV DBC"))
FALLBACK.update(_rows("sector_etf", "XLK XLE XLF XLV XLI XLY XLP XLU XLC XLB XLRE"))
FALLBACK.update(_rows("thematic_etf", "QQQ SOXX SMH ARKK BOTZ ROBO"))
FALLBACK.update(_rows("leveraged_etf", "TQQQ SQQQ UPRO SPXU SOXL SOXS"))

_MAP = {ticker: dict(meta) for ticker, meta in FALLBACK.items()}
_LOAD_RESULT = {"loaded": 0, "skipped": 0, "error": None}


def reset_map():
    """Restore fallback data.  Primarily useful for deterministic tests."""
    global _MAP, _LOAD_RESULT
    _MAP = {ticker: dict(meta) for ticker, meta in FALLBACK.items()}
    _LOAD_RESULT = {"loaded": 0, "skipped": 0, "error": None}


def load_map(path):
    """Merge a local instrument map; malformed entries are skipped visibly."""
    global _LOAD_RESULT
    result = {"loaded": 0, "skipped": 0, "error": None}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        result["error"] = str(exc)
        _LOAD_RESULT = result
        return result
    if not isinstance(raw, dict):
        result["error"] = "instrument map must be an object"
        _LOAD_RESULT = result
        return result
    for ticker, meta in raw.items():
        if not isinstance(ticker, str) or not isinstance(meta, dict):
            result["skipped"] += 1
            continue
        kind = meta.get("kind")
        if kind not in VALID_KINDS:
            result["skipped"] += 1
            continue
        clean = {"kind": kind}
        for key in ("name", "expense_ratio", "tracking_error", "source", "as_of"):
            if key in meta:
                clean[key] = meta[key]
        _MAP[ticker.strip().upper()] = clean
        result["loaded"] += 1
    _LOAD_RESULT = result
    return result


def load_from_env():
    path = os.environ.get("TR_INSTRUMENT_MAP")
    return load_map(path) if path else dict(_LOAD_RESULT)


def info(ticker):
    """Return conservative metadata.  Unknown tickers never receive exemption."""
    symbol = str(ticker or "").strip().upper()
    meta = dict(_MAP.get(symbol) or {"kind": "equity"})
    meta["ticker"] = symbol
    meta["is_etf"] = meta["kind"] in ETF_KINDS
    meta["allocation_exempt"] = meta["kind"] in ALLOCATION_KINDS
    return meta


def is_diversified_allocation(ticker):
    return info(ticker)["allocation_exempt"]


def portfolio_analysis(weights):
    """Summarize portfolio structure without fabricating unavailable metadata."""
    weights = weights or {}
    by_kind = defaultdict(float)
    allocation = []
    concentrated = []
    missing = []
    for ticker, weight in sorted(weights.items()):
        meta = info(ticker)
        by_kind[meta["kind"]] += float(weight or 0)
        if not meta["is_etf"]:
            continue
        row = {"ticker": meta["ticker"], "kind": meta["kind"], "weight": float(weight or 0)}
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
        "allocation_weight": sum(row["weight"] for row in allocation),
        "concentrated_etf_weight": sum(row["weight"] for row in concentrated),
        "by_kind": dict(sorted(by_kind.items())),
        "allocation_etfs": allocation,
        "concentrated_etfs": concentrated,
        "metadata_gaps": missing,
    }
