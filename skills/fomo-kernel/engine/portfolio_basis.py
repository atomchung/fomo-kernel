#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical, read-only current-book query for PortfolioBasis v1 (#484).

This module deliberately owns neither a workflow nor persistence.  It adapts
the ledger's established anchor/replay semantics into a small, versioned fact
object which later consumers can share.  In particular, it does *not* make an
incomplete snapshot an anchor and it does not calculate cash from trades.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

import ledger


CONTRACT_VERSION = "portfolio-basis-v1"
VALUATION_FRAME_VERSION = "valuation-frame-v1"
VALUATION_FRAME_ID_PREFIX = "vf-v1:"
STATE_VERSION_PREFIX = "pb-v1:"
_COMPLETENESS = {"declared_complete", "declared_partial", "unverified"}
_SOURCES = {"snapshot_anchor", "transaction_replay", "empty"}
_COST_BASES = {"average_cost", "partial_average_cost", "unavailable"}
_VALUATION_BASES = {"unpriced", "priced"}
_VALUE_SOURCES = {"price", "cost_fallback", "unavailable"}
_PROJECTION_REASONS = {None, "empty_current_book", "no_valued_holdings",
                       "non_positive_denominator", "mixed_native_currencies"}
_PROJECTION_REL_TOL = 1e-12
_PROJECTION_ABS_TOL = 1e-12
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class PortfolioBasisError(ValueError):
    """The supplied basis input is malformed or cannot be safely normalized."""


@dataclass(frozen=True)
class PortfolioBasis:
    """A typed, serializable, renderer-free current-book fact object."""

    source: str
    as_of: str
    stale_days: int
    completeness: str
    cost_basis: str
    valuation_basis: str
    reconciliation_ref: Optional[dict[str, Any]]
    state_version: str
    current_book: dict[str, Any]
    integrity: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": CONTRACT_VERSION,
            "source": self.source,
            "as_of": self.as_of,
            "stale_days": self.stale_days,
            "completeness": self.completeness,
            "cost_basis": self.cost_basis,
            "valuation_basis": self.valuation_basis,
            "reconciliation_ref": self.reconciliation_ref,
            "state_version": self.state_version,
            "current_book": self.current_book,
            "integrity": list(self.integrity),
        }


@dataclass(frozen=True)
class SizingProjection:
    """Renderer-free, canonical values and weights for one PortfolioBasis.

    This is deliberately separate from ``PortfolioBasis``: the basis remains
    the immutable fact envelope, while the projection is its one read-only
    valuation interpretation.  Consumers must not rebuild its denominator.
    """

    denominator: Optional[float]
    values: dict[str, dict[str, Any]]
    coverage: dict[str, Any]
    applicable: bool
    reason: Optional[str]
    aggregate_currency: Optional[str] = None
    frame: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {"denominator": self.denominator, "values": self.values,
                "coverage": self.coverage, "applicable": self.applicable,
                "reason": self.reason, "aggregate_currency": self.aggregate_currency,
                "frame": self.frame}


@dataclass(frozen=True)
class ValuationFrame:
    """Private, frozen native prices and explicit common-currency rates."""

    contract_version: str
    as_of: str
    aggregate_currency: str
    prices: dict[str, dict[str, Any]]
    fx_to_aggregate: dict[str, dict[str, Any]]
    coverage: dict[str, Any]
    usable: bool
    reason: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {"contract_version": self.contract_version, "as_of": self.as_of, "aggregate_currency": self.aggregate_currency,
                "prices": self.prices, "fx_to_aggregate": self.fx_to_aggregate,
                "coverage": self.coverage, "usable": self.usable, "reason": self.reason}


def _frame_reason(missing_price: list[dict[str, str]], missing_fx: list[str]) -> Optional[str]:
    if missing_price and missing_fx:
        return "missing_price_and_fx"
    if missing_price:
        return "missing_price"
    if missing_fx:
        return "missing_fx"
    return None


def build_valuation_frame(*, as_of: Any, positions: Mapping[str, Mapping[str, Any]],
                          prices: Mapping[str, Any], aggregate_currency: Any,
                          fx_to_aggregate: Mapping[str, Any],
                          price_provenance: Any, fx_provenance: Any) -> ValuationFrame:
    """Build the sole v1 native-price/FX receipt from already-frozen facts.

    This is intentionally a domain builder, not a renderer helper.  Every
    position is represented exactly once as a usable native price or as a
    ``{ticker,currency}`` gap; every non-aggregate position currency is likewise
    represented exactly once by an FX rate or an FX gap.
    """
    as_of_date = _date(as_of, "valuation_frame.as_of")
    if not isinstance(positions, Mapping) or not isinstance(prices, Mapping) or not isinstance(fx_to_aggregate, Mapping):
        raise PortfolioBasisError("valuation_frame inputs must be objects")
    if not _currency(aggregate_currency):
        raise PortfolioBasisError("valuation_frame aggregate_currency is invalid")
    if not isinstance(price_provenance, str) or not price_provenance:
        raise PortfolioBasisError("valuation_frame price provenance is invalid")
    if not isinstance(fx_provenance, str) or not fx_provenance:
        raise PortfolioBasisError("valuation_frame FX provenance is invalid")
    aggregate = aggregate_currency
    expected: dict[str, str] = {}
    for ticker, position in positions.items():
        if not isinstance(ticker, str) or not ticker or not isinstance(position, Mapping) or not _currency(position.get("currency")):
            raise PortfolioBasisError("valuation_frame position identity is invalid")
        expected[ticker] = position["currency"]
    if set(prices) - set(expected):
        raise PortfolioBasisError("valuation_frame prices contain unknown tickers")
    native, missing_price = {}, []
    for ticker, currency in sorted(expected.items()):
        price = prices.get(ticker)
        if _finite_number(price) and price > 0:
            native[ticker] = {"price": float(price), "currency": currency,
                              "provenance": price_provenance}
        else:
            missing_price.append({"ticker": ticker, "currency": currency})
    needed_fx = sorted(set(expected.values()) - {aggregate})
    # The fetcher supplies USD identity even on a single-currency book; the
    # builder remains owner of its receipt and writes canonical identity below.
    if set(fx_to_aggregate) - (set(needed_fx) | {aggregate}):
        raise PortfolioBasisError("valuation_frame FX contains unneeded currencies")
    rates = {aggregate: {"rate": 1.0, "provenance": "identity", "as_of": as_of_date.isoformat()}}
    missing_fx = []
    for currency in needed_fx:
        rate = fx_to_aggregate.get(currency)
        if _finite_number(rate) and rate > 0:
            rates[currency] = {"rate": float(rate), "provenance": fx_provenance,
                               "as_of": as_of_date.isoformat()}
        else:
            missing_fx.append(currency)
    reason = _frame_reason(missing_price, missing_fx)
    frame = ValuationFrame(VALUATION_FRAME_VERSION, as_of_date.isoformat(), aggregate,
                           native, rates,
                           {"missing_price": missing_price, "missing_fx": missing_fx},
                           reason is None, reason)
    validate_valuation_frame(frame.to_dict(), positions=expected)
    return frame


def _date(value: Any, field: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise PortfolioBasisError(f"{field} must be an ISO date") from exc


def _canonical(value: Any) -> Any:
    """Return a JSON-only deterministic value; reject non-finite numbers."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PortfolioBasisError("basis values must be finite")
        return value
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    raise PortfolioBasisError(f"unsupported basis value type: {type(value).__name__}")


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(_canonical(payload), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    return STATE_VERSION_PREFIX + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _valuation_frame_identity(frame: Mapping[str, Any]) -> str:
    """Stable identity for the exact frozen valuation frame used by sizing."""
    encoded = json.dumps(_canonical(frame), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    return VALUATION_FRAME_ID_PREFIX + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _partial_snapshot_present(events: Sequence[Mapping[str, Any]]) -> bool:
    return any(event.get("type") == "snapshot" and event.get("is_complete", True) is not True
               for event in events)


def _finite_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _currency(value: Any) -> bool:
    return isinstance(value, str) and _CURRENCY_RE.fullmatch(value) is not None


def _valid_snapshot(event: Mapping[str, Any]) -> bool:
    """Preflight *all* snapshots, including ones ledger would not select.

    ``latest_anchor`` intentionally skips unusable declarations to preserve its
    historical replay semantics.  PortfolioBasis cannot turn that skip into a
    trusted fingerprint: an invalid snapshot in a supplied book stream means
    the query does not know the complete state.
    """
    try:
        _date(event.get("as_of"), "snapshot.as_of")
    except PortfolioBasisError:
        return False
    positions = event.get("positions")
    if not isinstance(positions, list):
        return False
    position_keys = {"ticker", "shares", "avg_cost", "market_value", "market", "currency"}
    for position in positions:
        if not isinstance(position, Mapping) or not isinstance(position.get("ticker"), str) or not position["ticker"]:
            return False
        if set(position) - position_keys or not _finite_number(position.get("shares")) or position["shares"] <= 0:
            return False
        if position.get("avg_cost") is not None and (not _finite_number(position["avg_cost"]) or position["avg_cost"] <= 0):
            return False
        if position.get("market_value") is not None and (not _finite_number(position["market_value"]) or position["market_value"] <= 0):
            return False
        if "market" in position and (not isinstance(position["market"], str) or not position["market"]):
            return False
        if "currency" in position and not _currency(position["currency"]):
            return False
    if "source" in event and (not isinstance(event["source"], str) or not event["source"]):
        return False
    if "is_complete" in event and not isinstance(event["is_complete"], bool):
        return False
    if "projection_sequence" in event:
        sequence = event["projection_sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            return False
    if event.get("cash") is not None:
        if not isinstance(event["cash"], Mapping) or not all(_currency(currency) and _finite_number(amount)
                                                              for currency, amount in event["cash"].items()):
            return False
    return True


def _valid_trade(event: Mapping[str, Any]) -> bool:
    normalized = ledger._norm_trade(event)
    if normalized is None:
        return False
    _, ticker, _, qty, price = normalized
    if not (isinstance(ticker, str) and bool(ticker) and math.isfinite(qty) and math.isfinite(price)):
        return False
    return ("market" not in event or (isinstance(event["market"], str) and event["market"])) and (
        "currency" not in event or _currency(event["currency"]))


def _semantically_known_events(events: Sequence[Mapping[str, Any]]) -> bool:
    """Reject direct unknown and malformed book-affecting input before replay."""
    for event in events:
        event_type = event.get("type")
        if event_type not in ledger.EVENT_TYPES:
            return False
        if event_type == "snapshot" and not _valid_snapshot(event):
            return False
        if event_type == "trade" and not _valid_trade(event):
            return False
    return True


def _bad_integrity(integrity: Sequence[Mapping[str, Any]]) -> bool:
    # A malformed input is unknown state.  An oversell is a known, claim-affecting
    # accounting warning: retain it in the basis so downstream claim gates can
    # fail closed without pretending the replay never happened.
    return any(str(item.get("issue", "")).startswith("bad_") for item in integrity)


def _normalized_anchor(anchor: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    if anchor is None:
        return None
    positions = anchor.get("positions", [])
    if not isinstance(positions, list):
        raise PortfolioBasisError("snapshot positions must be a list")
    # Keep all anchor facts capable of changing the book.  Position order is
    # declaration noise, not a semantic difference.
    normalized_positions = []
    for position in positions:
        normalized_positions.append({
            "ticker": position["ticker"], "shares": float(position["shares"]),
            "avg_cost": float(position["avg_cost"]) if position.get("avg_cost") is not None else None,
            "market_value": float(position["market_value"]) if position.get("market_value") is not None else None,
            "market": position.get("market", "US"), "currency": position.get("currency", "USD"),
        })
    normalized_positions.sort(key=lambda item: json.dumps(item, ensure_ascii=False,
                                                            sort_keys=True, separators=(",", ":")))
    sequence = anchor.get("projection_sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
        sequence = None
    return {
        "as_of": str(anchor.get("as_of")),
        "source": str(anchor.get("source", "user_declared")),
        "projection_sequence": sequence,
        "is_complete": anchor.get("is_complete", True) is True,
        "positions": normalized_positions,
        "cash": ({currency: float(amount) for currency, amount in sorted(anchor.get("cash", {}).items())}
                 if anchor.get("cash") is not None else None),
    }


def _post_anchor_events(events: Sequence[Mapping[str, Any]], anchor_date: Optional[dt.date]) -> list[dict[str, Any]]:
    """Canonical applied-trade identity, matching ledger's date gate/order."""
    selected: list[tuple[dt.date, int, dict[str, Any]]] = []
    for index, event in enumerate(events):
        if event.get("type") != "trade":
            continue
        normalized = ledger._norm_trade(event)  # ledger's canonical validation semantics
        if normalized is None:
            continue
        date, ticker, action, qty, price = normalized
        if anchor_date is not None and date <= anchor_date:
            continue
        selected.append((date, index, {
            "date": date.isoformat(), "ticker": ticker, "action": action,
            "qty": qty, "price": price,
            "market": event.get("market", "US"), "currency": event.get("currency", "USD"),
        }))
    selected.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in selected]


def _version_evidence(events: Sequence[Mapping[str, Any]], anchor_date: Optional[dt.date]) -> list[dict[str, Any]]:
    """Trade identity at/after the anchor, including same-day non-stacked rows.

    Ledger correctly does not replay an anchor-day trade into holdings.  The
    imported fact still changes what this kernel knows about that account day,
    so optimistic concurrency must see it even when visible holdings do not.
    """
    evidence = []
    for index, event in enumerate(events):
        if event.get("type") != "trade":
            continue
        date, ticker, action, qty, price = ledger._norm_trade(event)
        if anchor_date is not None and date < anchor_date:
            continue
        evidence.append((date, index, {"date": date.isoformat(), "ticker": ticker,
                                       "action": action, "qty": qty, "price": price,
                                       "market": event.get("market", "US"),
                                       "currency": event.get("currency", "USD")}))
    evidence.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in evidence]


def _valuation_manifest(manifest: Optional[Mapping[str, Any]], *, positions: Optional[Mapping[str, Any]] = None) -> tuple[str, Optional[dict[str, Any]], Optional[dt.date]]:
    if manifest is None:
        return "unpriced", None, None
    if not isinstance(manifest, Mapping):
        raise PortfolioBasisError("valuation_manifest must be an object")
    if manifest.get("contract_version") == VALUATION_FRAME_VERSION:
        return _valuation_frame(manifest, positions=positions)
    if set(manifest) != {"as_of", "prices"} or not isinstance(manifest.get("prices"), Mapping):
        raise PortfolioBasisError("valuation_manifest requires exactly as_of and prices")
    manifest_date = _date(manifest["as_of"], "valuation_manifest.as_of")
    if not all(isinstance(ticker, str) and ticker and _finite_number(price) and price > 0
               for ticker, price in manifest["prices"].items()):
        raise PortfolioBasisError("valuation_manifest prices must be finite ticker-number pairs")
    normalized = _canonical(dict(manifest))
    normalized["as_of"] = manifest_date.isoformat()
    return "priced", normalized, manifest_date


def validate_valuation_frame(value: Mapping[str, Any], *, positions: Optional[Mapping[str, Any]] = None) -> None:
    """Validate a typed frame, optionally against the exact current holdings."""
    _exact_keys(value, {"contract_version", "as_of", "aggregate_currency", "prices", "fx_to_aggregate",
                        "coverage", "usable", "reason"}, "valuation_frame")
    if value["contract_version"] != VALUATION_FRAME_VERSION:
        raise PortfolioBasisError("unsupported valuation_frame version")
    as_of = _date(value["as_of"], "valuation_frame.as_of")
    aggregate, prices, fx, coverage = (value.get("aggregate_currency"), value.get("prices"),
                                        value.get("fx_to_aggregate"), value.get("coverage"))
    if not _currency(aggregate) or not isinstance(prices, Mapping) or not isinstance(fx, Mapping):
        raise PortfolioBasisError("valuation_frame has invalid identity")
    if not isinstance(coverage, Mapping) or set(coverage) != {"missing_price", "missing_fx"}:
        raise PortfolioBasisError("valuation_frame coverage is invalid")
    if (not isinstance(coverage["missing_price"], list) or not isinstance(coverage["missing_fx"], list)
            or not isinstance(value["usable"], bool) or value["reason"] not in {None, "missing_price", "missing_fx", "missing_price_and_fx"}):
        raise PortfolioBasisError("valuation_frame coverage is invalid")
    normalized_prices, normalized_fx = {}, {}
    for ticker, row in prices.items():
        if (not isinstance(ticker, str) or not ticker or not isinstance(row, Mapping)
                or set(row) != {"price", "currency", "provenance"} or not _finite_number(row["price"])
                or row["price"] <= 0 or not _currency(row["currency"])
                or not isinstance(row["provenance"], str) or not row["provenance"]):
            raise PortfolioBasisError("valuation_frame native price is invalid")
        normalized_prices[ticker] = {"price": float(row["price"]), "currency": row["currency"], "provenance": row["provenance"]}
    for currency, row in fx.items():
        if (not _currency(currency) or not isinstance(row, Mapping) or set(row) != {"rate", "provenance", "as_of"}
                or not _finite_number(row["rate"]) or row["rate"] <= 0
                or not isinstance(row["provenance"], str) or not row["provenance"]):
            raise PortfolioBasisError("valuation_frame FX is invalid")
        _date(row["as_of"], "valuation_frame.fx.as_of")
        normalized_fx[currency] = {"rate": float(row["rate"]), "provenance": row["provenance"], "as_of": _date(row["as_of"], "valuation_frame.fx.as_of").isoformat()}
    if (aggregate not in normalized_fx or normalized_fx[aggregate]["rate"] != 1.0
            or normalized_fx[aggregate]["provenance"] != "identity"
            or normalized_fx[aggregate]["as_of"] != as_of.isoformat()):
        raise PortfolioBasisError("valuation_frame aggregate identity FX is required")
    missing_price = []
    for row in coverage["missing_price"]:
        if (not isinstance(row, Mapping) or set(row) != {"ticker", "currency"}
                or not isinstance(row["ticker"], str) or not row["ticker"] or not _currency(row["currency"])):
            raise PortfolioBasisError("valuation_frame missing_price is invalid")
        missing_price.append({"ticker": row["ticker"], "currency": row["currency"]})
    missing_fx = coverage["missing_fx"]
    if any(not _currency(currency) for currency in missing_fx):
        raise PortfolioBasisError("valuation_frame missing_fx is invalid")
    if len({(row["ticker"], row["currency"]) for row in missing_price}) != len(missing_price) or len(set(missing_fx)) != len(missing_fx):
        raise PortfolioBasisError("valuation_frame coverage has duplicates")
    if value["reason"] != _frame_reason(missing_price, missing_fx) or value["usable"] != (value["reason"] is None):
        raise PortfolioBasisError("valuation_frame usability disagrees with coverage")
    if positions is not None:
        expected = {}
        for ticker, position in positions.items():
            currency = position.get("currency") if isinstance(position, Mapping) else position
            if not isinstance(ticker, str) or not ticker or not _currency(currency):
                raise PortfolioBasisError("valuation_frame position identity is invalid")
            expected[ticker] = currency
        priced = {(ticker, row["currency"]) for ticker, row in normalized_prices.items()}
        missing = {(row["ticker"], row["currency"]) for row in missing_price}
        expected_pairs = set(expected.items())
        if priced & missing or priced | missing != expected_pairs:
            raise PortfolioBasisError("valuation_frame prices do not exactly partition holdings")
        needed = set(expected.values()) - {aggregate}
        if (set(normalized_fx) != ((needed - set(missing_fx)) | {aggregate})
                or set(missing_fx) != needed - set(normalized_fx)):
            raise PortfolioBasisError("valuation_frame FX does not exactly partition needed currencies")


def _valuation_frame(value: Mapping[str, Any], positions: Optional[Mapping[str, Any]] = None) -> tuple[str, dict[str, Any], dt.date]:
    try:
        validate_valuation_frame(value, positions=positions)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, PortfolioBasisError):
            raise
        raise PortfolioBasisError("valuation_frame is invalid") from exc
    as_of = _date(value["as_of"], "valuation_frame.as_of")
    normalized = _canonical(dict(value))
    normalized["as_of"] = as_of.isoformat()
    return "priced", normalized, as_of


def _version_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Fields that describe the book, deliberately excluding presentation freshness."""
    return {key: value[key] for key in (
        "contract_version", "source", "as_of", "completeness", "cost_basis",
        "valuation_basis", "reconciliation_ref", "current_book", "integrity")}


def _exact_keys(value: Mapping[str, Any], keys: set[str], name: str) -> None:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise PortfolioBasisError(f"{name} fields do not match v1 contract")


def _validate_current_book(current_book: Mapping[str, Any]) -> None:
    _exact_keys(current_book, {"anchor", "holdings", "post_anchor_events", "version_evidence", "valuation_manifest"}, "current_book")
    anchor = current_book["anchor"]
    if anchor is not None:
        _exact_keys(anchor, {"as_of", "source", "projection_sequence", "is_complete", "positions", "cash"}, "current_book.anchor")
        _date(anchor["as_of"], "current_book.anchor.as_of")
        if not isinstance(anchor["source"], str) or not isinstance(anchor["is_complete"], bool):
            raise PortfolioBasisError("current_book.anchor has invalid scalar fields")
        sequence = anchor["projection_sequence"]
        if sequence is not None and (isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0):
            raise PortfolioBasisError("current_book.anchor projection_sequence is invalid")
        if not isinstance(anchor["positions"], list):
            raise PortfolioBasisError("current_book.anchor positions must be a list")
        position_keys = {"ticker", "shares", "avg_cost", "market_value", "market", "currency"}
        for position in anchor["positions"]:
            _exact_keys(position, position_keys, "current_book.anchor position")
            if not isinstance(position["ticker"], str) or not position["ticker"] or not _finite_number(position["shares"]) or position["shares"] <= 0:
                raise PortfolioBasisError("current_book.anchor position identity is invalid")
            if position["avg_cost"] is not None and (not _finite_number(position["avg_cost"]) or position["avg_cost"] <= 0):
                raise PortfolioBasisError("current_book.anchor position avg_cost is invalid")
            if position["market_value"] is not None and (not _finite_number(position["market_value"]) or position["market_value"] <= 0):
                raise PortfolioBasisError("current_book.anchor position market_value is invalid")
            if not isinstance(position["market"], str) or not position["market"] or not _currency(position["currency"]):
                raise PortfolioBasisError("current_book.anchor position market/currency is invalid")
        if anchor["cash"] is not None:
            if not isinstance(anchor["cash"], Mapping) or not all(_currency(currency) and _finite_number(amount)
                                                                   for currency, amount in anchor["cash"].items()):
                raise PortfolioBasisError("current_book.anchor cash is invalid")
    holdings = current_book["holdings"]
    if not isinstance(holdings, Mapping):
        raise PortfolioBasisError("current_book.holdings must be an object")
    holding_keys = {"shares", "avg_cost", "cost_total", "currency", "market", "origin", "since", "cycle_id", "add_count", "decision_cursor"}
    for ticker, holding in holdings.items():
        if not isinstance(ticker, str) or not ticker:
            raise PortfolioBasisError("current_book.holdings ticker is invalid")
        _exact_keys(holding, holding_keys, "current_book.holdings entry")
        if not _finite_number(holding["shares"]) or holding["shares"] <= 0:
            raise PortfolioBasisError("current_book.holdings shares is invalid")
        if holding["avg_cost"] is not None and not _finite_number(holding["avg_cost"]):
            raise PortfolioBasisError("current_book.holdings avg_cost is invalid")
        if holding["cost_total"] is not None and not _finite_number(holding["cost_total"]):
            raise PortfolioBasisError("current_book.holdings cost_total is invalid")
        if (not _currency(holding["currency"])
                or not all(isinstance(holding[key], str) and holding[key] for key in ("market", "origin", "since", "cycle_id"))):
            raise PortfolioBasisError("current_book.holdings identity is invalid")
        _date(holding["since"], "current_book.holdings.since")
        if isinstance(holding["add_count"], bool) or not isinstance(holding["add_count"], int) or holding["add_count"] < 0:
            raise PortfolioBasisError("current_book.holdings add_count is invalid")
        if holding["decision_cursor"] is not None and not isinstance(holding["decision_cursor"], str):
            raise PortfolioBasisError("current_book.holdings decision_cursor is invalid")
    events = current_book["post_anchor_events"]
    event_keys = {"date", "ticker", "action", "qty", "price", "market", "currency"}
    if not isinstance(events, list):
        raise PortfolioBasisError("current_book.post_anchor_events must be a list")
    for event in events:
        _exact_keys(event, event_keys, "current_book.post_anchor_event")
        if not _valid_trade({"type": "trade", **event}):
            raise PortfolioBasisError("current_book.post_anchor_event is invalid")
    evidence = current_book["version_evidence"]
    if not isinstance(evidence, list):
        raise PortfolioBasisError("current_book.version_evidence must be a list")
    for event in evidence:
        _exact_keys(event, event_keys, "current_book.version_evidence entry")
        if not _valid_trade({"type": "trade", **event}):
            raise PortfolioBasisError("current_book.version_evidence entry is invalid")
    valuation = current_book["valuation_manifest"]
    if valuation is not None:
        # A structurally valid frame can still price a different book.  The
        # persisted basis validator must retain the same holdings-bound exact
        # partition gate as the query path, including after a forged hash is
        # recomputed.
        _valuation_manifest(valuation, positions=holdings)


def validate_portfolio_basis(value: Mapping[str, Any]) -> None:
    """Small dependency-free typed-contract validator (mirrors the JSON schema)."""
    required = {"contract_version", "source", "as_of", "stale_days", "completeness",
                "cost_basis", "valuation_basis", "reconciliation_ref", "state_version",
                "current_book", "integrity"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise PortfolioBasisError("PortfolioBasis fields do not match v1 contract")
    if value["contract_version"] != CONTRACT_VERSION or value["source"] not in _SOURCES:
        raise PortfolioBasisError("unsupported PortfolioBasis version or source")
    if value["completeness"] not in _COMPLETENESS or value["cost_basis"] not in _COST_BASES:
        raise PortfolioBasisError("unsupported PortfolioBasis completeness or cost basis")
    if value["valuation_basis"] not in _VALUATION_BASES:
        raise PortfolioBasisError("unsupported PortfolioBasis valuation basis")
    _date(value["as_of"], "as_of")
    if isinstance(value["stale_days"], bool) or not isinstance(value["stale_days"], int) or value["stale_days"] < 0:
        raise PortfolioBasisError("stale_days must be a non-negative integer")
    if not isinstance(value["state_version"], str) or not value["state_version"].startswith(STATE_VERSION_PREFIX):
        raise PortfolioBasisError("state_version must use the PortfolioBasis v1 algorithm")
    reconciliation_ref = value["reconciliation_ref"]
    if reconciliation_ref is not None:
        _exact_keys(reconciliation_ref, {"anchor_as_of", "anchor_source", "projection_sequence"}, "reconciliation_ref")
        _date(reconciliation_ref["anchor_as_of"], "reconciliation_ref.anchor_as_of")
        if not isinstance(reconciliation_ref["anchor_source"], str):
            raise PortfolioBasisError("reconciliation_ref.anchor_source is invalid")
        sequence = reconciliation_ref["projection_sequence"]
        if sequence is not None and (isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0):
            raise PortfolioBasisError("reconciliation_ref.projection_sequence is invalid")
    _validate_current_book(value["current_book"])
    if not isinstance(value["integrity"], list):
        raise PortfolioBasisError("integrity must be a list")
    for item in value["integrity"]:
        _exact_keys(item, {"issue", "ticker", "date", "qty"}, "integrity entry")
        if item["issue"] != "oversell" or not isinstance(item["ticker"], str) or not item["ticker"]:
            raise PortfolioBasisError("integrity entry has invalid identity")
        _date(item["date"], "integrity.date")
        if not _finite_number(item["qty"]) or item["qty"] <= 0:
            raise PortfolioBasisError("integrity.qty is invalid")
    if value["state_version"] != _digest(_version_payload(value)):
        raise PortfolioBasisError("state_version does not match the declared current-book state")


def _projection_entry(value, source, reason, currency):
    return {"value": value, "weight": None, "source": source, "currency": currency,
            "applicable": False, "reason": reason}


def _frame_bound_entry(*, holding: Mapping[str, Any], price: Mapping[str, Any],
                       fx: Mapping[str, Any], frame_identity: str,
                       basis_state_version: str) -> Optional[dict[str, Any]]:
    native_value = float(holding["shares"]) * float(price["price"])
    converted_value = native_value * float(fx["rate"])
    if not math.isfinite(native_value) or not math.isfinite(converted_value) or converted_value <= 0:
        return None
    return {
        "value": converted_value, "weight": None, "source": "price",
        "currency": holding["currency"], "applicable": False, "reason": None,
        "frame_proof": {
            "native_currency": holding["currency"], "native_price": float(price["price"]),
            "fx_rate": float(fx["rate"]), "converted_value": converted_value,
            "frame_identity": frame_identity, "basis_state_version": basis_state_version,
        },
    }


def _validated_basis(basis):
    """Return only a typed, self-validating basis; malformed input fails closed."""
    if not isinstance(basis, PortfolioBasis):
        return None
    try:
        validate_portfolio_basis(basis.to_dict())
    except (PortfolioBasisError, TypeError, ValueError):
        return None
    return basis


def sizing_projection(basis):
    """Return canonical current-book values/weights, or None for an invalid basis.

    Price values come only from the basis valuation manifest. A holding with no
    usable price may use only its canonical positive cost total; otherwise it
    stays unavailable, never a zero. Consumers reuse this denominator rather
    than rebuilding one beside their own presentation.
    """
    basis = _validated_basis(basis)
    if basis is None:
        return None
    holdings = basis.current_book["holdings"]
    currencies = sorted({holding["currency"] for holding in holdings.values()})
    # Native prices and costs cannot be summed across currencies.  The sole
    # exception is a typed valuation frame that binds every native price and
    # FX rate to this exact current-book state.  `usable is True` is already
    # the genuine-missing-fact check here: `validate_valuation_frame` forces
    # it to mean `coverage.missing_price` and `coverage.missing_fx` are both
    # empty, verified against these exact holdings (#485).  `basis.completeness`
    # answers an unrelated question -- whether the user declared this snapshot
    # to be their complete external account -- and per #485 must not gate a
    # calculation the engine can already perform from facts it has.
    if len(currencies) > 1:
        manifest = basis.current_book.get("valuation_manifest")
        if (isinstance(manifest, Mapping)
                and manifest.get("contract_version") == VALUATION_FRAME_VERSION
                and manifest.get("usable") is True):
            frame_identity = _valuation_frame_identity(manifest)
            aggregate = manifest["aggregate_currency"]
            values = {}
            for ticker in sorted(holdings):
                holding = holdings[ticker]
                price = manifest["prices"].get(ticker)
                fx = manifest["fx_to_aggregate"].get(holding["currency"])
                if not isinstance(price, Mapping) or not isinstance(fx, Mapping):
                    return None
                entry = _frame_bound_entry(holding=holding, price=price, fx=fx,
                                           frame_identity=frame_identity,
                                           basis_state_version=basis.state_version)
                if entry is None:
                    return None
                values[ticker] = entry
            try:
                denominator = math.fsum(entry["value"] for entry in values.values())
            except OverflowError:
                return None
            if not math.isfinite(denominator) or denominator <= 0:
                return None
            for entry in values.values():
                entry.update(weight=entry["value"] / denominator, applicable=True, reason=None)
            coverage = {"scope": "full_current_book", "total_holdings": len(holdings),
                        "valued_holdings": len(holdings), "priced": sorted(holdings),
                        "cost_fallback": [], "unavailable": [], "currencies": currencies}
            projection = SizingProjection(
                denominator=denominator, values=values, coverage=coverage,
                applicable=True, reason=None,
                aggregate_currency=aggregate,
                frame={"identity": frame_identity, "aggregate_currency": aggregate,
                       "as_of": manifest["as_of"], "basis_state_version": basis.state_version},
            )
            validate_sizing_projection(projection.to_dict(), basis=basis)
            return projection
        values = {ticker: _projection_entry(None, "unavailable", "mixed_native_currencies",
                                             holdings[ticker]["currency"])
                  for ticker in sorted(holdings)}
        coverage = {"scope": "unavailable_mixed_currency", "total_holdings": len(holdings),
                    "valued_holdings": 0, "priced": [], "cost_fallback": [],
                    "unavailable": sorted(holdings), "currencies": currencies}
        projection = SizingProjection(denominator=None, values=values, coverage=coverage,
                                      applicable=False, reason="mixed_native_currencies")
        validate_sizing_projection(projection.to_dict(), basis=basis)
        return projection
    manifest = basis.current_book.get("valuation_manifest")
    prices = (manifest or {}).get("prices") or {}
    values, priced, cost_fallback, unavailable = {}, [], [], []
    for ticker in sorted(holdings):
        holding = holdings[ticker]
        shares, price, cost_total = holding["shares"], prices.get(ticker), holding["cost_total"]
        if _finite_number(price) and price > 0:
            value = float(shares) * float(price)
            if not math.isfinite(value):
                return None
            values[ticker] = _projection_entry(value, "price", None, holding["currency"])
            priced.append(ticker)
        elif _finite_number(cost_total) and cost_total > 0:
            values[ticker] = _projection_entry(float(cost_total), "cost_fallback", None, holding["currency"])
            cost_fallback.append(ticker)
        else:
            values[ticker] = _projection_entry(None, "unavailable", "value_unavailable", holding["currency"])
            unavailable.append(ticker)
    valued = [entry["value"] for entry in values.values() if entry["value"] is not None]
    try:
        denominator = math.fsum(valued) if valued else None
    except OverflowError:
        return None
    if denominator is not None and not math.isfinite(denominator):
        return None
    if not holdings:
        reason = "empty_current_book"
    elif denominator is None:
        reason = "no_valued_holdings"
    elif not math.isfinite(denominator) or denominator <= 0:
        reason = "non_positive_denominator"
    else:
        reason = None
    applicable = reason is None
    if applicable:
        for entry in values.values():
            if entry["value"] is not None:
                entry.update(weight=entry["value"] / denominator, applicable=True, reason=None)
    elif denominator is not None:
        for entry in values.values():
            if entry["value"] is not None:
                entry["reason"] = "denominator_unavailable"
    coverage = {"scope": "full_current_book" if not unavailable else "bounded_valued_subset",
                "total_holdings": len(holdings), "valued_holdings": len(valued),
                "priced": priced, "cost_fallback": cost_fallback, "unavailable": unavailable,
                "currencies": currencies}
    projection = SizingProjection(denominator=denominator, values=values, coverage=coverage,
                                  applicable=applicable, reason=reason)
    validate_sizing_projection(projection.to_dict(), basis=basis)
    return projection


def validate_sizing_projection(value, *, basis=None):
    """Validate the dependency-free projection contract."""
    _exact_keys(value, {"denominator", "values", "coverage", "applicable", "reason", "aggregate_currency", "frame"}, "sizing_projection")
    denominator, coverage = value["denominator"], value["coverage"]
    if not isinstance(value["applicable"], bool) or value["reason"] not in _PROJECTION_REASONS:
        raise PortfolioBasisError("sizing_projection applicability is invalid")
    if denominator is not None and (not _finite_number(denominator) or denominator <= 0):
        raise PortfolioBasisError("sizing_projection denominator is invalid")
    if value["applicable"] != (value["reason"] is None) or value["applicable"] != (denominator is not None):
        raise PortfolioBasisError("sizing_projection applicability disagrees")
    _exact_keys(coverage, {"scope", "total_holdings", "valued_holdings", "priced", "cost_fallback", "unavailable", "currencies"},
                "sizing_projection.coverage")
    if coverage["scope"] not in {"full_current_book", "bounded_valued_subset", "unavailable_mixed_currency"}:
        raise PortfolioBasisError("sizing_projection coverage scope is invalid")
    if any(isinstance(coverage[k], bool) or not isinstance(coverage[k], int) or coverage[k] < 0
           for k in ("total_holdings", "valued_holdings")):
        raise PortfolioBasisError("sizing_projection coverage counts are invalid")
    groups = ("priced", "cost_fallback", "unavailable")
    if not all(isinstance(coverage[k], list) and all(isinstance(t, str) and t for t in coverage[k]) for k in groups):
        raise PortfolioBasisError("sizing_projection coverage tickers are invalid")
    sets = [set(coverage[k]) for k in groups]
    currencies = coverage["currencies"]
    if not isinstance(currencies, list):
        raise PortfolioBasisError("sizing_projection coverage currencies are invalid")
    if ((not currencies and coverage["total_holdings"] != 0)
            or any(not _currency(currency) for currency in currencies)
            or len(currencies) != len(set(currencies))):
        raise PortfolioBasisError("sizing_projection coverage currencies are invalid")
    if (any(len(coverage[k]) != len(tickers) for k, tickers in zip(groups, sets))
            or coverage["total_holdings"] != len(value["values"]) or coverage["valued_holdings"] != len(sets[0]) + len(sets[1])
            or set.union(*sets) != set(value["values"]) or any(sets[i] & sets[j] for i in range(3) for j in range(i))
            or (coverage["scope"] == "full_current_book" and bool(sets[2]))
            or (coverage["scope"] == "bounded_valued_subset" and not sets[2])):
        raise PortfolioBasisError("sizing_projection coverage disagrees")
    mixed = len(currencies) > 1
    frame = value["frame"]
    frame_bound = frame is not None
    if frame_bound:
        _exact_keys(frame, {"identity", "aggregate_currency", "as_of", "basis_state_version"}, "sizing_projection.frame")
        if (not isinstance(frame["identity"], str) or not frame["identity"].startswith(VALUATION_FRAME_ID_PREFIX)
                or not _currency(frame["aggregate_currency"]) or not isinstance(frame["basis_state_version"], str)
                or not frame["basis_state_version"].startswith(STATE_VERSION_PREFIX)):
            raise PortfolioBasisError("sizing_projection frame identity is invalid")
        _date(frame["as_of"], "sizing_projection.frame.as_of")
        if value["aggregate_currency"] != frame["aggregate_currency"]:
            raise PortfolioBasisError("sizing_projection aggregate currency disagrees with frame")
    elif value["aggregate_currency"] is not None:
        raise PortfolioBasisError("sizing_projection aggregate currency requires a frame")
    if mixed:
        if value["applicable"]:
            if (not frame_bound or coverage["scope"] != "full_current_book" or sets[1] or sets[2]
                    or value["reason"] is not None):
                raise PortfolioBasisError("sizing_projection mixed currency frame is invalid")
            if _validated_basis(basis) is None:
                raise PortfolioBasisError("sizing_projection mixed currency requires its basis")
        elif (frame_bound or coverage["scope"] != "unavailable_mixed_currency"
              or value["reason"] != "mixed_native_currencies" or denominator is not None or sets[0] or sets[1]):
            raise PortfolioBasisError("sizing_projection mixed currency must be unavailable")
    elif frame_bound or coverage["scope"] == "unavailable_mixed_currency":
        raise PortfolioBasisError("sizing_projection currency scope disagrees")
    weight_sum = 0.0
    for ticker, entry in value["values"].items():
        if not isinstance(ticker, str) or not ticker:
            raise PortfolioBasisError("sizing_projection ticker is invalid")
        keys = {"value", "weight", "source", "currency", "applicable", "reason"}
        if frame_bound:
            keys.add("frame_proof")
        _exact_keys(entry, keys, "sizing_projection value")
        if (entry["source"] not in _VALUE_SOURCES or not _currency(entry["currency"])
                or not isinstance(entry["applicable"], bool)):
            raise PortfolioBasisError("sizing_projection value source is invalid")
        if entry["source"] == "unavailable":
            expected_reason = "mixed_native_currencies" if mixed else "value_unavailable"
            if entry["value"] is not None or entry["weight"] is not None or entry["applicable"] or entry["reason"] != expected_reason:
                raise PortfolioBasisError("sizing_projection unavailable value is invalid")
            if ticker not in sets[2]:
                raise PortfolioBasisError("sizing_projection unavailable source disagrees with coverage")
        elif not _finite_number(entry["value"]) or entry["value"] <= 0:
            raise PortfolioBasisError("sizing_projection value is invalid")
        elif value["applicable"]:
            if (not _finite_number(entry["weight"]) or entry["weight"] <= 0
                    or not entry["applicable"] or entry["reason"] is not None):
                raise PortfolioBasisError("sizing_projection applicable weight is invalid")
            expected = entry["value"] / denominator
            if not math.isclose(entry["weight"], expected, rel_tol=_PROJECTION_REL_TOL,
                                abs_tol=_PROJECTION_ABS_TOL):
                raise PortfolioBasisError("sizing_projection weight disagrees with denominator")
            weight_sum += entry["weight"]
        elif entry["weight"] is not None or entry["applicable"] or entry["reason"] != "denominator_unavailable":
            raise PortfolioBasisError("sizing_projection unavailable denominator value is invalid")
        if entry["source"] == "price" and ticker not in sets[0]:
            raise PortfolioBasisError("sizing_projection price source disagrees with coverage")
        if entry["source"] == "cost_fallback" and ticker not in sets[1]:
            raise PortfolioBasisError("sizing_projection cost source disagrees with coverage")
        if frame_bound:
            proof = entry["frame_proof"]
            _exact_keys(proof, {"native_currency", "native_price", "fx_rate", "converted_value",
                                "frame_identity", "basis_state_version"}, "sizing_projection frame proof")
            if (proof["native_currency"] != entry["currency"] or not _finite_number(proof["native_price"])
                    or proof["native_price"] <= 0 or not _finite_number(proof["fx_rate"])
                    or proof["fx_rate"] <= 0 or not _finite_number(proof["converted_value"])
                    or proof["converted_value"] <= 0 or proof["frame_identity"] != frame["identity"]
                    or proof["basis_state_version"] != frame["basis_state_version"]
                    or not math.isclose(entry["value"], proof["converted_value"], rel_tol=_PROJECTION_REL_TOL,
                                        abs_tol=_PROJECTION_ABS_TOL)):
                raise PortfolioBasisError("sizing_projection frame proof disagrees")
    if currencies != sorted({entry["currency"] for entry in value["values"].values()}):
        raise PortfolioBasisError("sizing_projection coverage currencies disagree")
    if value["applicable"] and not math.isclose(weight_sum, 1.0, rel_tol=_PROJECTION_REL_TOL,
                                                  abs_tol=_PROJECTION_ABS_TOL):
        raise PortfolioBasisError("sizing_projection weights do not sum to one")
    if frame_bound:
        _validate_frame_bound_projection(value, basis)


def _validate_frame_bound_projection(value: Mapping[str, Any], basis: PortfolioBasis) -> None:
    """Recompute a mixed-currency projection from its exact frozen basis/frame."""
    frame = value["frame"]
    if basis.state_version != frame["basis_state_version"]:
        raise PortfolioBasisError("sizing_projection frame state_version disagrees")
    manifest = basis.current_book["valuation_manifest"]
    holdings = basis.current_book["holdings"]
    if (not isinstance(manifest, Mapping) or manifest.get("contract_version") != VALUATION_FRAME_VERSION
            or manifest.get("usable") is not True or manifest["aggregate_currency"] != frame["aggregate_currency"]
            or manifest["as_of"] != frame["as_of"] or _valuation_frame_identity(manifest) != frame["identity"]):
        raise PortfolioBasisError("sizing_projection frame does not match basis")
    if set(value["values"]) != set(holdings):
        raise PortfolioBasisError("sizing_projection frame holdings disagree")
    recomputed = []
    for ticker, holding in holdings.items():
        entry = value["values"].get(ticker)
        price = manifest["prices"].get(ticker)
        fx = manifest["fx_to_aggregate"].get(holding["currency"])
        if not isinstance(entry, Mapping) or not isinstance(price, Mapping) or not isinstance(fx, Mapping):
            raise PortfolioBasisError("sizing_projection frame proof is incomplete")
        expected = _frame_bound_entry(holding=holding, price=price, fx=fx,
                                      frame_identity=frame["identity"], basis_state_version=basis.state_version)
        if expected is None:
            raise PortfolioBasisError("sizing_projection frame value is invalid")
        if entry["frame_proof"] != expected["frame_proof"] or not math.isclose(entry["value"], expected["value"],
                                                                                  rel_tol=_PROJECTION_REL_TOL,
                                                                                  abs_tol=_PROJECTION_ABS_TOL):
            raise PortfolioBasisError("sizing_projection frame value disagrees")
        recomputed.append(expected["value"])
    denominator = math.fsum(recomputed)
    if not math.isclose(value["denominator"], denominator, rel_tol=_PROJECTION_REL_TOL,
                        abs_tol=_PROJECTION_ABS_TOL):
        raise PortfolioBasisError("sizing_projection frame denominator disagrees")


def query_current_book(events: Sequence[Mapping[str, Any]], *, valuation_manifest: Optional[Mapping[str, Any]] = None,
                       reference_as_of: Optional[str] = None, skipped_lines: int = 0) -> Optional[PortfolioBasis]:
    """Return the canonical basis, or ``None`` when ledger state is unknowable.

    ``reference_as_of`` is an explicitly injected freshness reference (normally
    a price-manifest date).  It intentionally never defaults to wall-clock time.
    ``skipped_lines`` is the visibility count returned by ``ledger.load_ledger``;
    any skipped line means the source is not a complete known ledger and fails
    closed rather than minting a plausible-looking state version.
    """
    if isinstance(skipped_lines, bool) or not isinstance(skipped_lines, int) or skipped_lines < 0:
        raise PortfolioBasisError("skipped_lines must be a non-negative integer")
    if skipped_lines or not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        return None
    if not all(isinstance(event, Mapping) for event in events):
        return None
    events = list(events)
    if not _semantically_known_events(events):
        return None
    derived = ledger.derive_holdings(events)
    integrity = [_canonical(item) for item in derived["integrity"]]
    if _bad_integrity(integrity):
        return None

    anchor_raw = ledger.latest_anchor(events)
    anchor = _normalized_anchor(anchor_raw)
    anchor_date = _date(anchor["as_of"], "anchor.as_of") if anchor is not None else None
    applied_events = _post_anchor_events(events, anchor_date)
    version_evidence = _version_evidence(events, anchor_date)
    effective_candidates = ([] if anchor_date is None else [anchor_date]) + [
        _date(event["date"], "trade.date") for event in applied_events]
    effective_date = max(effective_candidates) if effective_candidates else None
    if effective_date is None:
        # An empty ledger is a valid but unverified empty current-book query.
        effective_date = _date(reference_as_of, "reference_as_of") if reference_as_of else dt.date(1970, 1, 1)

    holdings = _canonical(derived["holdings"])
    valuation_basis, valuation, valuation_date = _valuation_manifest(valuation_manifest, positions=holdings)
    reference_date = _date(reference_as_of, "reference_as_of") if reference_as_of else valuation_date
    if reference_date is not None and reference_date < effective_date:
        raise PortfolioBasisError("reference_as_of cannot precede current-book as_of")
    stale_days = (reference_date - effective_date).days if reference_date is not None else 0

    partial = _partial_snapshot_present(events)
    if anchor is not None:
        source = "snapshot_anchor"
        completeness = "declared_partial" if partial else "declared_complete"
    elif applied_events:
        source = "transaction_replay"
        completeness = "declared_partial" if partial else "unverified"
    else:
        source = "empty"
        completeness = "declared_partial" if partial else "unverified"
    cost_basis = "unavailable" if not holdings else (
        "average_cost" if all(row.get("avg_cost") is not None for row in holdings.values())
        else "partial_average_cost")
    reconciliation_ref = ({"anchor_as_of": anchor["as_of"], "anchor_source": anchor["source"],
                            "projection_sequence": anchor["projection_sequence"]}
                          if anchor is not None else None)
    current_book = {
        "anchor": anchor,
        "holdings": holdings,
        "post_anchor_events": applied_events,
        "version_evidence": version_evidence,
        "valuation_manifest": valuation,
    }
    unsigned = {
        "contract_version": CONTRACT_VERSION, "source": source, "as_of": effective_date.isoformat(),
        "stale_days": stale_days, "completeness": completeness, "cost_basis": cost_basis,
        "valuation_basis": valuation_basis, "reconciliation_ref": reconciliation_ref,
        "current_book": current_book, "integrity": integrity,
    }
    basis = PortfolioBasis(state_version=_digest(_version_payload(unsigned)), integrity=tuple(integrity),
                           **{key: unsigned[key] for key in unsigned if key not in {"contract_version", "integrity"}})
    validate_portfolio_basis(basis.to_dict())
    return basis


def query_current_book_from_ledger(path: str, *, valuation_manifest: Optional[Mapping[str, Any]] = None,
                                   reference_as_of: Optional[str] = None) -> Optional[PortfolioBasis]:
    """Read the canonical ledger through its strict corruption gate, then query it.

    This is the production-shaped convenience entry point.  It purposefully
    converts a corrupt JSONL source into ``None`` rather than falling back to
    diagnostic ``strict=False`` rows and deriving a potentially wrong book.
    """
    try:
        events, skipped_lines = ledger.load_ledger(path)
    except ledger.LedgerIntegrityError:
        return None
    return query_current_book(events, valuation_manifest=valuation_manifest,
                              reference_as_of=reference_as_of,
                              skipped_lines=skipped_lines)
