#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""price_feed.py — agent-supplied price fallback (#289).

The engine fetches prices itself. In a sandboxed host the fetch can fail before
any price is retrieved (observed: ``curl error 6: could not resolve host``), and
every price-dependent number — unrealized P&L, holdings return, weights,
benchmark comparison — degrades at once. The agent, however, usually still has a
working retrieval path of its own.

This module is the contract for that hand-off, and it mirrors the snapshot
adapter's division of labour: the agent transcribes *declared facts* from a
recognized market-data source into a normalized envelope, and the engine keeps
every calculation. The agent never computes a return, a weight, or a P&L number.

Two coverage tiers, both accepted:

* ``close`` only — one closing price per instrument. Restores market value,
  unrealized P&L, total P&L, weights, concentration, and what-if.
* ``close`` plus ``history`` — a daily close series. Additionally restores the
  paths that need a series: beta/alpha, the benchmark window, the P&L curve,
  and account-level time-weighted return.

Validation is fail-closed: a malformed envelope raises :class:`PriceFeedError`
instead of silently pricing part of a portfolio. Prices are money, and a price
that is quietly wrong is worse than a price that is honestly missing.

Design notes:

* Pure and offline. No network, no yfinance, no engine state.
* ``parse`` accepts a decoded payload; ``load`` reads one from disk.
* Provenance travels with the numbers (``provenance``) so the card can disclose
  that the closes came from the agent rather than from the engine's own fetch.
"""
import datetime as dt
import json
import os
import re

# The envelope's own version. Bump only on a breaking shape change; the parser
# accepts a payload without the field so a hand-written envelope stays valid.
SCHEMA_VERSION = 1

_TICKER_RE = re.compile(r"^[A-Za-z0-9^][A-Za-z0-9.\-^=]{0,19}$")   # ^ leads index symbols (^VIX, ^TWII)
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_MAX_ROWS = 500                     # a real portfolio plus benchmarks; a runaway file is an input error
_MAX_HISTORY = 2000                 # ~8 trading years per instrument


class PriceFeedError(ValueError):
    """Raised when a supplied price envelope cannot be trusted as-is."""


# ─────────────────────────── field validators ───────────────────────────

def _require(mapping, key, where):
    if not isinstance(mapping, dict) or key not in mapping or mapping[key] is None:
        raise PriceFeedError(f"{where}.{key} is required")
    return mapping[key]


def _date(value, where, not_after=None):
    if not isinstance(value, str):
        raise PriceFeedError(f"{where} must be an ISO date string (YYYY-MM-DD)")
    try:
        parsed = dt.date.fromisoformat(value.strip())
    except ValueError as exc:
        raise PriceFeedError(f"{where} is not an ISO date: {value!r}") from exc
    if not_after is not None and parsed > not_after:
        raise PriceFeedError(f"{where} ({parsed.isoformat()}) is after as_of ({not_after.isoformat()})")
    return parsed


def _price(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PriceFeedError(f"{where} must be a number")
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise PriceFeedError(f"{where} must be a finite number")
    if value <= 0:
        raise PriceFeedError(f"{where} must be positive (got {value})")
    return value


def _text(value, where, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PriceFeedError(f"{where} must be a non-empty string")
    return value.strip()


def _ticker(value, where):
    symbol = _text(value, where)
    if not _TICKER_RE.match(symbol):
        raise PriceFeedError(f"{where} is not a usable engine symbol: {value!r}")
    return symbol


def _currency(value, where):
    code = _text(value, where).upper()
    if not _CURRENCY_RE.match(code):
        raise PriceFeedError(f"{where} must be a three-letter currency code (got {value!r})")
    return code


def _pairs(rows, where, as_of, value_name, value_check):
    """Validate a ``[[date, value], ...]`` series into sorted, de-duplicated tuples."""
    if not isinstance(rows, list):
        raise PriceFeedError(f"{where} must be a list of [date, {value_name}] pairs")
    if len(rows) > _MAX_HISTORY:
        raise PriceFeedError(f"{where} has {len(rows)} entries; the limit is {_MAX_HISTORY}")
    out = {}
    for index, row in enumerate(rows):
        at = f"{where}[{index}]"
        if isinstance(row, dict):
            pair = (_require(row, "date", at), row.get(value_name))
            if pair[1] is None:
                raise PriceFeedError(f"{at}.{value_name} is required")
        elif isinstance(row, (list, tuple)) and len(row) == 2:
            pair = (row[0], row[1])
        else:
            raise PriceFeedError(f"{at} must be [date, {value_name}] or "
                                 f"{{\"date\": ..., \"{value_name}\": ...}}")
        day = _date(pair[0], f"{at}.date", not_after=as_of)
        value = value_check(pair[1], f"{at}.{value_name}")
        if day in out and out[day] != value:
            raise PriceFeedError(f"{at}.date {day.isoformat()} appears twice with different values")
        out[day] = value
    return [(day, out[day]) for day in sorted(out)]


# ─────────────────────────────── parsing ───────────────────────────────

def parse(payload):
    """Validate a decoded envelope into the engine's normalized feed structure.

    Returns ``{"as_of", "source", "prices", "fx", "coverage"}`` where ``prices``
    maps engine symbol to ``{close, date, currency, source, history, splits}``.
    Raises :class:`PriceFeedError` on anything the engine should not price from.
    """
    if not isinstance(payload, dict):
        raise PriceFeedError("price feed must be a JSON object")
    version = payload.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise PriceFeedError(f"unsupported price feed schema_version {version!r} "
                             f"(this engine reads {SCHEMA_VERSION})")
    as_of = _date(_require(payload, "as_of", "price feed"), "price feed.as_of")
    if as_of > dt.date.today():
        raise PriceFeedError(f"price feed.as_of ({as_of.isoformat()}) is in the future")
    source = _text(_require(payload, "source", "price feed"), "price feed.source")

    rows = _require(payload, "prices", "price feed")
    if not isinstance(rows, list) or not rows:
        raise PriceFeedError("price feed.prices must be a non-empty list")
    if len(rows) > _MAX_ROWS:
        raise PriceFeedError(f"price feed.prices has {len(rows)} rows; the limit is {_MAX_ROWS}")

    prices = {}
    for index, row in enumerate(rows):
        at = f"prices[{index}]"
        if not isinstance(row, dict):
            raise PriceFeedError(f"{at} must be an object")
        ticker = _ticker(_require(row, "ticker", at), f"{at}.ticker")
        if ticker in prices:
            raise PriceFeedError(f"{at}.ticker {ticker} appears twice; supply one row per instrument")
        close = _price(_require(row, "close", at), f"{at}.close")
        day = _date(_require(row, "date", at), f"{at}.date", not_after=as_of)
        currency = _currency(_require(row, "currency", at), f"{at}.currency")
        history = _pairs(row.get("history") or [], f"{at}.history", as_of, "close", _price)
        splits = _pairs(row.get("splits") or [], f"{at}.splits", as_of, "ratio", _price)
        merged = dict(history)
        if day in merged and abs(merged[day] - close) > 1e-9:
            raise PriceFeedError(f"{at}.history disagrees with close on {day.isoformat()}")
        merged[day] = close
        prices[ticker] = {
            "close": close,
            "date": day.isoformat(),
            "currency": currency,
            "source": _text(row.get("source"), f"{at}.source", required=False) or source,
            "history": [(d.isoformat(), value) for d, value in sorted(merged.items())],
            "splits": [(d, value) for d, value in splits],
        }

    fx_rows = payload.get("fx") or []
    if not isinstance(fx_rows, list):
        raise PriceFeedError("price feed.fx must be a list")
    fx = {}
    for index, row in enumerate(fx_rows):
        at = f"fx[{index}]"
        if not isinstance(row, dict):
            raise PriceFeedError(f"{at} must be an object")
        currency = _currency(_require(row, "currency", at), f"{at}.currency")
        if currency == "USD":
            raise PriceFeedError(f"{at}.currency USD is fixed at 1.0; do not supply it")
        if currency in fx:
            raise PriceFeedError(f"{at}.currency {currency} appears twice")
        fx[currency] = {
            "usd_per_unit": _price(_require(row, "usd_per_unit", at), f"{at}.usd_per_unit"),
            "date": _date(_require(row, "date", at), f"{at}.date", not_after=as_of).isoformat(),
            "source": _text(row.get("source"), f"{at}.source", required=False) or source,
        }

    return {
        "as_of": as_of.isoformat(),
        "source": source,
        "prices": prices,
        "fx": fx,
        "coverage": ("daily_series"
                     if any(len(row["history"]) > 1 for row in prices.values())
                     else "single_close"),
    }


def load(path):
    """Read and validate an envelope from disk."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise PriceFeedError(f"cannot read price feed {path}: {exc}") from exc
    except ValueError as exc:
        raise PriceFeedError(f"price feed {path} is not valid JSON: {exc}") from exc
    feed = parse(payload)
    feed["path"] = os.path.abspath(path)
    return feed


def load_from_env(variable="TR_PRICES"):
    """Load the feed the orchestration layer pinned, or ``None`` when unset."""
    path = (os.environ.get(variable) or "").strip()
    return load(path) if path else None


# ───────────────────────── engine-facing adapters ─────────────────────────

def to_frame(feed, tickers=None):
    """Build the DataFrame shape ``fetch_prices`` returns, or ``(None, reason)``.

    Columns are restricted to ``tickers`` when given so an over-broad feed
    cannot widen the price universe the engine reasons about. A single-close
    feed yields a one-row frame: ``last_prices`` reads it, and the series-only
    paths degrade exactly as they do offline.
    """
    if not feed or not feed.get("prices"):
        return None, "price feed carries no prices"
    try:
        import pandas as pd
    except ImportError:
        return None, "pandas is not installed; the supplied price feed cannot be applied"
    wanted = set(tickers) if tickers is not None else set(feed["prices"])
    columns = {}
    for ticker, row in feed["prices"].items():
        if ticker not in wanted:
            continue
        series = {dt.date.fromisoformat(day): value for day, value in row["history"]}
        if series:
            columns[ticker] = series
    if not columns:
        return None, "price feed covers none of the requested instruments"
    index = sorted({day for series in columns.values() for day in series})
    frame = pd.DataFrame(
        {ticker: [series.get(day) for day in index] for ticker, series in sorted(columns.items())},
        index=pd.DatetimeIndex([dt.datetime(day.year, day.month, day.day) for day in index]),
    )
    return frame, None


def fx_rates(feed):
    """``{currency: usd_per_unit}`` from the feed, with USD fixed at 1.0."""
    rates = {"USD": 1.0}
    for currency, row in (feed or {}).get("fx", {}).items():
        rates[currency] = float(row["usd_per_unit"])
    return rates


def splits_map(feed, tickers=None):
    """``{ticker: [(date, ratio), ...]}`` in ``fetch_splits`` shape."""
    wanted = None if tickers is None else set(tickers)
    out = {}
    for ticker, row in (feed or {}).get("prices", {}).items():
        if wanted is not None and ticker not in wanted:
            continue
        if row.get("splits"):
            out[ticker] = list(row["splits"])
    return out


def currency_conflicts(feed, currency_by_ticker):
    """Tickers whose feed currency contradicts the currency the trades declare.

    A mismatch means the closes are denominated in a different unit from the
    cost basis, which would corrupt every P&L number downstream. The caller
    fails closed on a non-empty result rather than pricing the position.
    """
    conflicts = []
    for ticker, row in (feed or {}).get("prices", {}).items():
        declared = (currency_by_ticker or {}).get(ticker)
        if declared and str(declared).upper() != row["currency"]:
            conflicts.append({"ticker": ticker, "feed": row["currency"],
                              "trades": str(declared).upper()})
    return sorted(conflicts, key=lambda row: row["ticker"])


# ─────────────────────── request manifest and provenance ───────────────────────

def build_request(*, tickers, benchmarks=(), currencies=(), window=None, as_of=None,
                  earliest_trade=None, reason=None, missing=None):
    """The machine-readable statement of what the engine still needs priced.

    Emitted whenever price coverage is incomplete, so a degraded run stays
    observable (#289) and the agent knows exactly which symbols, which
    currencies, and which window to look up before rerunning ``prepare``.
    """
    needed = sorted({str(t) for t in (missing if missing is not None else tickers) if t})
    request = {
        "as_of": as_of,
        "tickers": needed,
        "benchmarks": sorted({str(b) for b in benchmarks if b}),
        "currencies": sorted({str(c).upper() for c in currencies if c and str(c).upper() != "USD"}),
        "minimum": "one close per instrument in its own trading currency",
        "optional": "daily close history unlocks benchmark comparison, beta/alpha, "
                    "and account-level time-weighted return",
        "envelope": "references/price-feed.md",
    }
    if window:
        request["window"] = {"start": window[0], "end": window[1]}
    if earliest_trade:
        request["history_from"] = earliest_trade
    if reason:
        request["reason"] = reason
    return request


def provenance(*, mode, feed=None, error=None, requested=(), priced=(),
               benchmarks_priced=(), fx_mode="not_needed", splits_applied=False, as_of=None):
    """Machine-readable record of where this run's prices came from.

    ``mode`` is ``engine_fetch`` (the engine's own retrieval worked),
    ``agent_feed`` (an operator-supplied envelope was applied), or
    ``unavailable`` (retrieval failed and no envelope was supplied).
    """
    requested = sorted({str(t) for t in requested if t})
    priced = sorted({str(t) for t in priced if t})
    record = {
        "mode": mode,
        "as_of": as_of or (feed or {}).get("as_of"),
        "coverage": {
            "requested_n": len(requested),
            "priced_n": len(priced),
            "missing": [t for t in requested if t not in set(priced)],
        },
        "benchmarks_priced": sorted({str(b) for b in benchmarks_priced if b}),
        "fx": fx_mode,
        "splits_applied": bool(splits_applied),
        "error": error,
    }
    if mode == "agent_feed" and feed:
        record["source"] = feed.get("source")
        record["series"] = feed.get("coverage")
        record["sources_by_ticker"] = {ticker: row["source"]
                                       for ticker, row in sorted(feed.get("prices", {}).items())
                                       if ticker in set(priced)}
    return record
