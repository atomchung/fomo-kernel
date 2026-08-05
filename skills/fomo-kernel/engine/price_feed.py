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

Two coverage tiers for ``prices``, both accepted:

* ``close`` only — one closing price per instrument. Restores market value,
  unrealized P&L, total P&L, weights, concentration, and what-if.
* ``close`` plus ``history`` — a daily close series. Additionally restores the
  paths that need a series: beta/alpha, the benchmark window, the P&L curve,
  and account-level time-weighted return.

``prices`` itself is optional (#642) when the envelope carries ``fx``: a host
that can read a public FX rate but not every instrument's close can still
repair a book refused only for its missing rate. An envelope must still
declare at least one of the two — see :func:`parse`. Both tiers above describe
``prices`` specifically: an envelope with none supplied has neither, and
:func:`parse` omits ``coverage`` entirely rather than reporting one it never
earned (#652 — the old ternary read an empty ``prices`` dict as the smaller
tier, ``single_close``, instead of no tier at all).

Validation is fail-closed: a malformed envelope raises :class:`PriceFeedError`
instead of silently pricing part of a portfolio. Prices are money, and a price
that is quietly wrong is worse than a price that is honestly missing.

What a supplied price is *declared as* matters as much as the number (#583).
``close`` and ``history`` are **raw market observations, each as of its own
date**: the agent transcribes what the source printed that day and never
calculates an adjustment. The engine then normalizes every observation onto the
split basis its share count is stated in, using the row's own date against the
declared ``splits`` — ``splits.rebase_price``, the same read-time family
``revisit.rebased_exit_price`` belongs to. Before that, a structurally valid
envelope could pair a pre-split close with a post-split share count: 100 shares
became 1,000 across a ten-for-one and were valued at 100 rather than 10, a
tenfold portfolio with no limitation disclosed anywhere on the card.

Design notes:

* Pure and offline. No network, no yfinance, no engine state.
* ``parse`` accepts a decoded payload; ``load`` reads one from disk.
* Provenance travels with the numbers (``provenance``) so the card can disclose
  that the closes came from the agent rather than from the engine's own fetch.
* A parsed row keeps the observation it was built from (``observed_close``,
  ``observed_date``) beside the normalized value, so the alignment is
  replayable and a caller can still see exactly what the source printed.
"""
import datetime as dt
import json
import os
import re

import splits as split_policy
import symbols

# The envelope's own version. Bump only on a breaking shape change; the parser
# accepts a payload without the field so a hand-written envelope stays valid.
SCHEMA_VERSION = 1

_TICKER_RE = re.compile(r"^[A-Za-z0-9^][A-Za-z0-9.\-^=]{0,19}$")   # ^ leads index symbols (^VIX, ^TWII)
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_MAX_ROWS = 500                     # a real portfolio plus benchmarks; a runaway file is an input error
_MAX_HISTORY = 2000                 # ~8 trading years per instrument

# #330: soft plausibility cross-check against the ticker's own trade history.
# Structural validation above (positive, finite, currency-matched, no
# duplicates, date <= as_of) accepts any number that merely looks like a
# price -- a hallucinated-but-plausible close passes it outright and would
# otherwise be priced and disclosed as genuine. Comparing the supplied close
# to the last price the user actually transacted at is the one anchor the
# engine already holds without a network call.
#
# The band is deliberately wide (20x, either direction) and deliberately
# NOT scaled by the elapsed time since that last trade:
#   * A genuine long-held multi-bagger is real and must still price
#     normally -- this check only ever adds a disclosure, never blocks --
#     so the band is set to comfortably clear the kind of move retail
#     investors call a "ten-bagger" (2x headroom above it), not to chase
#     the last percentage point of specificity.
#   * A fabricated close is not reliably "more wrong" the older the last
#     trade is -- a mis-transcribed digit or a wrong-ticker mix-up is just
#     as likely against a last trade from yesterday as one from three years
#     ago -- so scaling the band by elapsed time would buy little real
#     protection while adding a second guessed constant (a growth-rate
#     assumption) on top of this one, for a check that is a soft,
#     best-effort caveat rather than a gate.
#   * Splits are handled on both sides before this check runs:
#     adjust_for_splits rebases the trade rows onto today's basis, and
#     since #583 ``parse`` rebases the supplied close onto the same one, so
#     the comparison is like-for-like. An *undisclosed* split still surfaces
#     here -- correctly, since the source failed to report it, which is the
#     one case neither rebase can see (#566).
PLAUSIBILITY_BAND = 20.0


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
    """Canonical (#803), then admitted by this envelope's own pattern.

    A supplied quote keys the map the book is priced from, so a close published
    for ``nvda`` has to reach the ``NVDA`` the holdings and the premise both
    name — otherwise the identity fix would leave a canonical book unpriced by
    its own price envelope."""
    symbol = symbols.canonical_ticker(_text(value, where))
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

    Returns ``{"as_of", "source", "prices", "fx"}``, plus ``"coverage"`` only
    when ``prices`` is non-empty, where ``prices`` maps engine symbol to
    ``{close, date, currency, source, history, splits}``. Raises
    :class:`PriceFeedError` on anything the engine should not price from.

    ``prices`` is optional (#642): a host whose own price retrieval is blocked
    can usually still read one FX rate off a public source even when
    transcribing every instrument's close is not practical, and the #612
    refusal that sends an agent here names exactly that repair — "supply the
    rate through --prices". An envelope carrying only ``fx`` is what makes that
    repair reachable. What is not optional is the envelope as a whole: one with
    neither ``prices`` nor ``fx`` has nothing for the engine to apply and is
    refused below, once both are parsed, so the message can name whichever of
    the two is genuinely empty.

    ``coverage`` states which of the two tiers above ``prices`` reached —
    never a claim about ``fx``. An fx-only envelope's ``prices`` is ``{}``, so
    there is no tier to report at all: ``coverage`` is omitted rather than
    defaulting to ``single_close``, the smaller tier, which would claim one
    close per instrument that was never supplied (#652).
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

    rows = payload.get("prices")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise PriceFeedError("price feed.prices must be a list")
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
        # #583. Every observation above is raw, in the basis its own session
        # printed; the share counts these get multiplied by are carried onto the
        # basis after every declared split. Rebase each observation from its own
        # date so the two operands are comparable, and keep the raw value beside
        # it so the alignment can be replayed. A split dated on an observation's
        # own day is deliberately not applied to it (``factor_after``): the
        # ex-date's own close already prints in post-split terms, which is also
        # why a close already on or after the latest split is untouched here.
        # No-split row: every factor is 1.0 and the values are byte-identical
        # to what this parser returned before the rule existed.
        try:
            basis_factor = split_policy.factor_after(splits, day)
            basis_day = split_policy.basis_date(day, splits)
            normalized_close = split_policy.rebase_price(close, day, splits)
            normalized_history = split_policy.rebase_series(sorted(merged.items()), splits)
        except split_policy.SplitDataError as exc:
            raise PriceFeedError(f"{at}.splits cannot establish a price basis: {exc}") from exc
        prices[ticker] = {
            "close": normalized_close,
            "date": day.isoformat(),
            "currency": currency,
            "source": _text(row.get("source"), f"{at}.source", required=False) or source,
            # The declared observation, kept so the normalization is replayable
            # and a reader can still see what the source actually printed.
            # ``observed_date`` duplicates ``date`` on purpose: ``date`` is what
            # every pre-#583 consumer already reads, and a second name that
            # cannot drift from it is cheaper than teaching each of them that
            # ``date`` now means "the date this basis was rebased *from*".
            "observed_close": close,
            "observed_date": day.isoformat(),
            "basis_date": basis_day.isoformat(),
            "split_factor": basis_factor,
            "history": [(d.isoformat(), value) for d, value in normalized_history],
            # ISO strings, exactly like ``history`` above. A parsed feed is not
            # a private in-memory value: ``review._fingerprint`` runs the whole
            # thing through ``session.canonical``, so a ``datetime.date`` left
            # in here is not serializable and every envelope that declared a
            # split crashed `prepare` before any work happened (#550). The one
            # documented way to supply split history on an offline host was
            # therefore unusable. ``splits_map`` converts back at its boundary,
            # so its callers still see ``fetch_splits`` shape.
            "splits": [(d.isoformat(), value) for d, value in splits],
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

    # #642: checked once both blocks are parsed, so the message names whichever
    # of the two is genuinely empty rather than hard-coding "prices" — an
    # envelope can legitimately carry fx alone (a rate-only repair) or prices
    # alone (the pre-#642 shape); it may not carry neither.
    if not prices and not fx:
        raise PriceFeedError(
            "price feed must declare at least one of prices or fx; an envelope "
            "with neither has nothing for the engine to apply")

    feed = {
        "as_of": as_of.isoformat(),
        "source": source,
        "prices": prices,
        "fx": fx,
    }
    # #652: `any()` over an empty `prices` is `False`, so this used to fall
    # through to the *smaller* tier, `single_close` — a claim of one close per
    # instrument for an envelope that supplied zero. `coverage` is a statement
    # about `prices` alone (never about `fx`), so when `prices` is empty there
    # is no tier to report and the key is omitted entirely, matching this
    # module's own "absent, never a placeholder" rule for a fact nothing
    # observed (`basis.price_observations`, #618) rather than inventing a new
    # sentinel value nothing else in this file uses. Every reader already
    # reaches this through `.get("coverage")` (`provenance` below), which
    # returns `None` for a missing key exactly as it would for one explicitly
    # set to `None` — so nothing downstream needed to change to stay honest.
    if prices:
        feed["coverage"] = ("daily_series"
                             if any(len(row["history"]) > 1 for row in prices.values())
                             else "single_close")
    return feed


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
    # Canonical on both sides (#803). The parsed feed is keyed canonically, but
    # a caller builds its request from its own rows — `trade_recap` requests the
    # broker's spelling verbatim — so an exact membership test drops a quote that
    # was actually retrieved and reports the instrument unpriced.
    wanted = ({symbols.canonical_ticker(t) for t in tickers} if tickers is not None
              else set(feed["prices"]))
    columns = {}
    for ticker, row in feed["prices"].items():
        if symbols.canonical_ticker(ticker) not in wanted:
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
            # The stored envelope keeps ISO strings so a parsed feed stays
            # JSON-serializable; the engine-side contract is dates.
            out[ticker] = [(dt.date.fromisoformat(str(day)), ratio)
                           for day, ratio in row["splits"]]
    return out


def basis_conflicts(feed, splits):
    """Supplied observations whose split basis the share map contradicts (#583).

    :func:`parse` normalizes every close onto the basis of the envelope's *own*
    declared splits. That is the whole story wherever the same envelope also
    supplies the share map — ``prepare`` with ``--prices`` performs no split
    retrieval of its own, so the two are the same events by construction. It is
    not the whole story for ``consider``, which falls back to the map the last
    review froze when the envelope declares no splits: the shares are then
    carried across a split the prices were never told about, and the pair is
    incomparable again with nothing on either side looking wrong.

    So compare the divisor each supplied observation actually received against
    the one its share count did, past that observation's own date. Equal, the
    operands are on one basis. Unequal, the basis cannot be established from the
    evidence supplied, and the caller refuses: silently applying the external
    map instead would have the engine assert a corporate action its price source
    never confirmed, and silently ignoring it is the tenfold error this returns
    a conflict for. Either repair is the agent's — declare the split in the
    envelope, or supply a close dated on or after it.

    Returns a list of ``{ticker, observed_date, declared_factor, book_factor,
    split_date}`` sorted by ticker. Never raises: a malformed external map is
    reported as a conflict for every ticker it covers rather than crashing a
    caller that is already in the middle of refusing.
    """
    try:
        book = split_policy.normalize(splits)
    except split_policy.SplitDataError as exc:
        book, book_error = {}, str(exc)
    else:
        book_error = None
    conflicts = []
    for ticker, row in (feed or {}).get("prices", {}).items():
        observed = row.get("observed_date") or row.get("date")
        book_events = book.get(ticker)
        if book_error is None and not book_events:
            continue
        try:
            declared = split_policy.factor_after(row.get("splits") or (), observed)
            book_factor = (split_policy.factor_after(book_events, observed)
                           if book_error is None else None)
            moved = (split_policy.last_applied(book_events, observed)
                     if book_error is None else None)
        except split_policy.SplitDataError as exc:
            declared, book_factor, moved, book_error = None, None, None, str(exc)
        if book_error is None and abs(declared - book_factor) <= 1e-9:
            continue
        conflicts.append({
            "ticker": ticker,
            "observed_date": str(observed),
            "declared_factor": declared,
            "book_factor": book_factor,
            "split_date": moved.isoformat() if moved is not None else None,
            "error": book_error,
        })
    return sorted(conflicts, key=lambda row: row["ticker"])


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


def plausibility_flags(feed, last_trade_price, *, band=PLAUSIBILITY_BAND):
    """Tickers whose supplied close deviates sharply from their last trade.

    A soft cross-check (#330), never a rejection: the caller discloses this
    on the card as an honesty caveat and still prices the position normally.
    See ``PLAUSIBILITY_BAND`` above for the chosen multiple and why it is not
    scaled by elapsed time.

    ``last_trade_price`` is ``{ticker: (price, date)}`` from the caller's own
    trade rows, taken *after* split adjustment so a disclosed split does not
    read as a false deviation. A ticker with no trade row (a benchmark) has
    no anchor to compare against and is silently skipped -- this check only
    ever concerns instruments the portfolio actually holds.

    Returns a list of ``{ticker, feed_close, feed_date, last_trade_price,
    last_trade_date, ratio}`` sorted by ticker. Never raises.
    """
    flags = []
    for ticker, row in (feed or {}).get("prices", {}).items():
        anchor = (last_trade_price or {}).get(ticker)
        if not anchor:
            continue
        price, last_date = anchor
        if not price or price <= 0:
            continue
        close = row["close"]
        ratio = close / price if close >= price else price / close
        if ratio > band:
            flags.append({
                "ticker": ticker,
                "feed_close": close,
                "feed_date": row["date"],
                "last_trade_price": round(float(price), 6),
                "last_trade_date": (last_date.isoformat() if hasattr(last_date, "isoformat")
                                    else str(last_date)),
                "ratio": round(ratio, 2),
            })
    return sorted(flags, key=lambda row: row["ticker"])


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


# A retrieval failure is classified into one of these stable reason codes. The
# needles are matched (case-insensitively, first table row wins) against the raw
# transport/yfinance string; see ``classify_error``.
_ERROR_SIGNATURES = (
    # DNS / host resolution — the #289 canonical sandbox failure.
    ("dns_failure", ("could not resolve", "resolve host", "name or service not known",
                     "nodename nor servname", "getaddrinfo", "failed to resolve",
                     "nameresolutionerror", "name resolution", "temporary failure in name",
                     "curl error 6", "curl: (6)")),
    # Connection or read timed out.
    ("timeout", ("timed out", "timeout", "etimedout", "curl error 28", "curl: (28)")),
    # Reachable host, transport- or HTTP-level rejection.
    ("http_error", ("http error", "httperror", "status code", "too many requests",
                    "connection reset", "connection refused", "connection aborted",
                    "remote end closed", "remote disconnected", "bad gateway",
                    "service unavailable", "ssl", "certificate", "curl error 7", "curl: (7)")),
    # Retrieval succeeded but returned nothing usable (empty frame, or a supplied
    # feed that matched none of the requested instruments).
    ("no_data", ("無資料", "no data", "empty", "covers none", "carries no prices",
                 "none of the requested")),
    # The retrieval client itself is absent (offline shim / uninstalled dep), or
    # was deliberately not used (#605's TR_OFFLINE posture). Those two share one
    # code on purpose: the resolver is built so an offline run degrades to exactly
    # the missing-provider answer, and every consumer downstream — including the
    # state sha256 that fixes session_id — must not be able to tell them apart.
    ("client_missing", ("未安裝", "not installed", "no module named", "cannot be applied",
                        "tr_offline")),
)


def classify_error(error):
    """Collapse a raw retrieval error into a stable, non-volatile reason code.

    The raw yfinance/transport string can embed volatile object reprs (memory
    addresses, per-attempt hostnames), and ``price_provenance`` enters engine
    state, whose sha256 fixes ``ledger.session_id_from_state()``. Two degraded
    runs that failed for the *same* reason must produce byte-identical state so
    a finalized degraded session stays re-detectable (#289 review finding 4).
    Only the code enters state; the verbose text travels to stderr for a human.

    Returns ``None`` for no error, a code from ``_ERROR_SIGNATURES`` on a match,
    or ``"unknown"`` for an unrecognized non-empty string (never the raw text).
    """
    if not error:
        return None
    text = str(error).lower()
    for code, needles in _ERROR_SIGNATURES:
        if any(needle in text for needle in needles):
            return code
    return "unknown"


def provenance(*, mode, feed=None, error=None, requested=(), priced=(),
               benchmarks_priced=(), fx_mode="not_needed", splits_applied=False, as_of=None):
    """Machine-readable record of where this run's prices came from.

    ``mode`` is ``engine_fetch`` (the engine's own retrieval worked),
    ``agent_feed`` (an operator-supplied envelope was applied), or
    ``unavailable`` (retrieval failed and no envelope was supplied).

    ``error`` is stored as a stable reason code (``classify_error``), not the
    raw string: the record enters engine state, and a volatile string there
    would shift ``session_id_from_state()`` between two same-cause degraded
    runs. The verbose original stays on stderr (the engine's price-status meta
    line), never in state.
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
        "error": classify_error(error),
    }
    if mode == "agent_feed" and feed:
        record["source"] = feed.get("source")
        # `feed["coverage"]` (the tier: single_close/daily_series) is a
        # different fact from `record["coverage"]` above (requested/priced/
        # missing counts) despite the shared name. `.get` already degrades to
        # `None` for a feed that priced nothing, since #652 made `parse` omit
        # the key rather than defaulting it to `single_close` — a claim of one
        # close per instrument an fx-only feed never supplied.
        record["series"] = feed.get("coverage")
        record["sources_by_ticker"] = {ticker: row["source"]
                                       for ticker, row in sorted(feed.get("prices", {}).items())
                                       if ticker in set(priced)}
    return record
