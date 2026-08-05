#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""splits.py — what a corporate split does to a recorded share count (#550).

Every store in this engine records a trade the way it was transacted: 100
shares at 197 means exactly that, on the day it happened, in that day's share
basis. That is the right thing to keep. It is also why *any* running share
balance accumulated across a ticker's full history is wrong the moment a split
falls inside that history — 90 bought before a ten-for-one split and 100 sold
after it are not quantities that can be subtracted from each other.

Before this module the rule lived in exactly one place, ``trade_recap``'s
CSV-analytics path, and every other walker of the same facts silently used raw
quantities. That is the divergent-derivation shape docs/development-guide.md §7
names: the two agreed on every book without a split and split apart on the
first one that had one. ``revisit.detect_exits`` read a ~10% trim of a
post-split position as a full liquidation, which permanently closed the thesis
and printed "fully exited" on a saved card (#550).

So this module owns the rule, once, and both callers read it:

* **Rebase to today** (``rebase_rows``) — multiply the quantity, divide the
  price, for every split *after* the trade. Notional is preserved and every row
  ends up in today's basis, which is what a comparison against a current price
  needs. ``trade_recap.adjust_for_splits`` is this function.
* **Rebase as it happened** (``factor_between``) — walk trades in date order and
  scale the running position by each split as it arrives, so every comparison
  at a moment in time happens in that moment's own basis. This is what an exit
  classification needs, and it has a property today's basis does not: the answer
  for a past exit never changes again, so a stored ``revisit_id`` derived from
  it does not churn the next time the ticker splits.

Discipline: pure standard library, no network, no engine state. This module
never *finds* splits — retrieval belongs to ``engine/market_data.py``, which
resolves split **observations** from one provider response or from an
agent-supplied envelope (#605), and its result is passed in through
``trade_recap.fetch_splits``'s projection or ``price_feed.splits_map``. A caller
with no split data gets the unadjusted answer, which is the pre-existing
behavior; it does not get a guessed one.

One property of that boundary reaches into the arithmetic here, so it is worth
stating on this side too: a resolved map is complete only from its request's
window start, and every factor below applies splits strictly *after* some real
book date (a trade, an anchor, an observation). That is what makes a windowed map
sufficient, and it is why ``market_data.build_request`` refuses a window that
cannot cover the origin its caller will rebase from.

A quantity is only half of every number the product states. The other half is
the price it gets multiplied by, and a price is an observation with a date of
its own, so it has a split basis too (#583). ``rebase_price`` is that side of
the same rule: a close observed before a split is divided by the splits after
it, which puts one operand in the other's basis instead of leaving the pair
incomparable. It is the read-time family — the value is restated where it is
read and never written back — because a stored exit price and a stored close
are records of what happened, and the next split must not move them.

Everything above rests on one premise about the input, and #582 is the reason
it is now written down in three places rather than assumed in none: **a
transaction file records what executed, un-rebased**. ``rebase_rows`` is only
correct on a file of that kind. Hand it a broker export that has already
restated its own history onto today's basis and the factor is applied a second
time — ten times the real share count on a ten-for-one, with cost preserved, so
``avg_cost`` falls tenfold too and every weight, concentration verdict and
position-size rule is measured against a book that never existed. Nothing in
this engine could see that: #550's family *erased* positions, and a book
reaching zero is at least a visible anomaly, while this one inflates and
inflation reads as a large position rather than a broken one.
:func:`basis_disagreements` is the check that can see it, and it is here
because it is a statement about split basis and needs nothing this module does
not already own.

Input is fail-closed. A split ratio is a multiplier on someone's share count;
a malformed one that were quietly dropped would produce a confident wrong
number, which is the failure this module exists to remove. Dates are coerced
here rather than at each call site for the same reason: ``derive_holdings``
receives its ``as_of`` as a ``datetime.date`` from ``holdings_as_of`` and as an
ISO string from ``build_derived_book``, and the tail rebase compared the two
kinds directly — so recording the derived book for any split-crossing history
raised ``TypeError`` and took ``prepare`` down with it (found by driving the
real CLI, #583).
"""
import bisect
import datetime as dt

import symbols

__all__ = [
    "SplitDataError",
    "EXECUTION_BAND",
    "normalize",
    "normalize_events",
    "to_json",
    "factor_after",
    "factor_between",
    "last_applied",
    "basis_date",
    "rebase_price",
    "rebase_series",
    "rebase_rows",
    "basis_disagreements",
]

# The widest ratio between one fill and that session's own close that still
# reads as "these two numbers are the same day's price" (#582). It is the only
# constant this check carries, and everything else about it is derived.
#
# Deliberately wide, for the same reason `price_feed.PLAUSIBILITY_BAND` is: the
# cost of a false positive here is a user who cannot review at all, and no real
# fill prints 35% away from its own session's close. It is *not* the sensitivity
# of the detector — the two hypotheses being separated are "≈ the close" and
# "≈ the close divided by the split factor", which a real split puts at least a
# factor of two apart, so widening this band trades away nothing but false
# positives until it approaches the square root of the factor.
#
# That relation is where the separation requirement comes from rather than from
# a second guessed number: the two bands are disjoint exactly when the factor
# exceeds `band ** 2`, so a split too small to tell the readings apart — a
# three-for-two, at 1.5 — is not examined at all instead of being guessed at.
# Not being able to separate them is the honest answer there; the engine does
# not get to pick one (#416).
EXECUTION_BAND = 1.35


class SplitDataError(ValueError):
    """Raised when supplied split events cannot be trusted as-is."""


def _date(value, where):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise SplitDataError(f"{where}: {value!r} is not an ISO date") from None


def _ratio(value, where):
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        raise SplitDataError(f"{where}: {value!r} is not a split ratio") from None
    # A reverse split is a ratio below 1 (1-for-10 is 0.1) and is as real as a
    # forward one, so the only bound is "positive and finite". The chained
    # comparison rejects NaN and inf without importing math.
    if not 0 < ratio < 1e6:
        raise SplitDataError(f"{where}: {value!r} is not a usable split ratio")
    return ratio


def normalize_events(rows, where="split events"):
    """One ticker's ``[(date, ratio), ...]``, from any accepted spelling.

    Accepts ``datetime.date`` keys with float ratios (what
    ``trade_recap.fetch_splits`` and ``price_feed.splits_map`` return) and what
    survives a JSON round trip through ``state.json`` or a parsed price envelope
    (ISO strings, lists instead of tuples). Every factor below reads through
    here rather than trusting its caller's spelling: a raw envelope row whose
    date was still a string reached the comparison and raised ``TypeError``,
    which is not the fail-closed refusal this module promises.
    """
    if not isinstance(rows, (list, tuple)):
        raise SplitDataError(f"{where}: split events must be a list")
    events = []
    for i, row in enumerate(rows):
        at = f"{where}[{i}]"
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise SplitDataError(f"{at}: expected [date, ratio]")
        events.append((_date(row[0], at), _ratio(row[1], at)))
    # Sorted once here so every reader below can assume date order and none of
    # them has to remember to sort (or silently not).
    return sorted(events, key=lambda pair: pair[0])


def normalize(mapping):
    """``{ticker: [(date, ratio), ...]}`` from any accepted spelling of one.

    Accepts what ``trade_recap.fetch_splits`` and ``price_feed.splits_map``
    return (``datetime.date`` keys, float ratios) and what survives a JSON
    round trip through ``state.json`` (ISO strings, lists instead of tuples),
    so the same map can be frozen with a review and read back next week.
    ``None`` and ``{}`` both mean "no split information", not "no splits".
    """
    if not mapping:
        return {}
    if not isinstance(mapping, dict):
        raise SplitDataError("split events must be a {ticker: [[date, ratio], ...]} mapping")
    out = {}
    for ticker, rows in mapping.items():
        # Canonical (#803): this map is looked up by the book's own canonical
        # key, so a map written `nvda` must rebase the `NVDA` it describes.
        name = symbols.canonical_ticker(ticker)
        if not name:
            raise SplitDataError("split events carry an empty ticker")
        events = normalize_events(rows, name)
        if events:
            out[name] = events
    return out


def to_json(splits):
    """The canonical map as JSON-safe data, for freezing into a review state."""
    return {ticker: [[day.isoformat(), ratio] for day, ratio in events]
            for ticker, events in normalize(splits).items()}


def _bound(value, where):
    """A window edge: a coerced date, or ``None`` for "unbounded on this side"."""
    return None if value is None else _date(value, where)


def _applied(events, after, upto):
    """The split events inside ``(after, upto]``, in date order.

    The single walk every factor below reads, so "which splits apply to this
    window" is decided once. ``after``/``upto`` may each be ``None`` for an
    unbounded side.
    """
    after = _bound(after, "split window start")
    upto = _bound(upto, "split window end")
    return [(day, ratio) for day, ratio in normalize_events(events or ())
            if (after is None or day > after) and (upto is None or day <= upto)]


def factor_after(events, date):
    """Cumulative ratio of the splits strictly after ``date``.

    This is the "rebase onto today" factor: a trade is multiplied by every
    split that happened after it. A split dated on the trade date itself does
    not count — the ex-date's own fills already print in post-split terms.
    """
    return factor_between(events, date, None)


def factor_between(events, after, upto):
    """Cumulative ratio of the splits in ``(after, upto]``.

    This is the "carry a running position forward" factor. ``after`` is the
    date the position was last stated in its own basis (``None`` = the position
    has no history yet, so every split up to ``upto`` applies — to zero shares,
    which is why an old split before a ticker's first trade is harmless rather
    than something each caller must remember to skip). ``upto`` is ``None``
    when the window has no upper edge, which is what :func:`factor_after`
    means.
    """
    factor = 1.0
    for _day, ratio in _applied(events, after, upto):
        factor *= ratio
    return factor


def last_applied(events, after, upto=None):
    """The date of the newest split inside ``(after, upto]``, or ``None``.

    The companion to the factors: they say *how much* a window moved a
    quantity, this says *when* it last moved. A basis that has to be stated —
    a book's own quantity basis, a price's declared basis — is a date, not a
    multiplier, and deriving it from a second walk over the same events is the
    divergent-derivation shape (docs/development-guide.md §7).
    """
    applied = _applied(events, after, upto)
    return applied[-1][0] if applied else None


def basis_date(observed, events, upto=None):
    """The date whose split basis a rebased observation is stated in (#583).

    The companion statement to :func:`rebase_price`, and deliberately the only
    place it is derived: the newest split that was divided out, or the
    observation's own date when nothing was. Two surfaces need to say what basis
    a price is on — the envelope a caller supplies and the frozen valuation
    evidence built from the engine's own frame — and they must say it the same
    way or the frame and the feed disagree about a value neither of them
    changed.

    Note what it is *not*: the price's recency. A stale quote whose splits have
    all been divided out is on the current basis while being days old, and
    conflating the two is exactly what made a frame-level ``as_of`` unable to
    testify about a per-ticker observation (#583 §2).
    """
    return last_applied(events, observed, upto) or _date(observed, "observation date")


def rebase_price(price, date, events, upto=None):
    """A price observed on ``date``, restated in a later split basis (#583).

    The price half of ``rebase_rows``' quantity half, and the same arithmetic
    seen from the other side: a share count is *multiplied* by the splits after
    it, so a price per share is *divided* by them. A close of 100 observed the
    day before a ten-for-one is 10 in the basis the post-split share count is
    stated in; multiplying 1,000 shares by 100 is a tenfold portfolio, and
    every weight, concentration verdict and unrealized figure derived from it
    inherits the error with no limitation stated anywhere.

    ``upto`` bounds the target basis. Omitted, every later split applies, which
    means "restate this onto the current basis" — the default because that is
    what a share count carried to today needs. Supplied, only the splits up to
    that date apply, which is what a comparison against *another observation*
    needs: a quote whose own basis date is known must not be assumed to
    postdate every split in the map (#583 §4).

    Read-time only. Nothing here is ever written back: an exit price and a
    supplied close are records of what was observed, and ``revisit_id`` derives
    from what executed, so restating a stored number would churn identities the
    user has already answered about.
    """
    factor = factor_after(events, date) if upto is None else factor_between(events, date, upto)
    return float(price) if abs(factor - 1.0) <= 1e-12 else float(price) / factor


def rebase_series(pairs, events, upto=None):
    """``[(date, price), ...]`` restated onto one basis, each on its own date.

    A daily close series that spans a split carries the split as a step change,
    and every consumer that differences it — forward return, beta and alpha,
    the P&L curve, account-level time-weighted return — reads that step as a
    market move. Each observation is rebased from its own date, so the step
    disappears and the real moves survive.
    """
    return [(day, rebase_price(price, day, events, upto)) for day, price in pairs or ()]


def rebase_rows(rows, splits):
    """Rebase trade rows onto today's split-adjusted basis, in place.

    ``rows`` are ``trade_recap.load()``-shaped dicts (``ticker``, ``date``,
    ``qty``, ``price``). Shares are multiplied and price divided by the same
    factor, so the transacted amount is unchanged and every row lines up with
    a current, split-adjusted quote. Returns the number of rows changed.
    An empty ``splits`` leaves every row untouched.
    """
    events_by_ticker = normalize(splits)
    if not events_by_ticker:
        return 0
    changed = 0
    for row in rows:
        events = events_by_ticker.get(row["ticker"])
        if not events:
            continue
        factor = factor_after(events, row["date"])
        if abs(factor - 1.0) > 1e-9:
            row["qty"] = row["qty"] * factor
            row["price"] = row["price"] / factor
            changed += 1
    return changed


# ───────────── did the file already have the split applied? (#582) ─────────────


def _close_index(pairs, where="close series"):
    """``[(date, close), ...]`` coerced, positives only, in date order.

    Shape is fail-closed for the same reason a split ratio is; a value that is
    not a usable number is simply absent, which is the honest reading of a
    session the source did not price.
    """
    index = []
    for i, pair in enumerate(pairs or ()):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise SplitDataError(f"{where}[{i}]: expected [date, close]")
        day = _date(pair[0], f"{where}[{i}]")
        try:
            close = float(pair[1])
        except (TypeError, ValueError):
            continue
        if close > 0:                      # also drops NaN, which compares false
            index.append((day, close))
    index.sort(key=lambda pair: pair[0])
    return index


def _close_on(index, day, lookback_days):
    """That day's close, or the newest one within ``lookback_days`` before it.

    A trade dated on a market holiday, or on a session the source did not
    return, has no close of its own. Walking back a few days finds the session
    the market last printed; walking back further would compare a fill against
    a price from another week, so it stops instead and the row is simply not
    examinable.
    """
    if not index:
        return None
    at = bisect.bisect_right(index, (day, float("inf"))) - 1
    if at < 0:
        return None
    found_day, close = index[at]
    return None if (day - found_day).days > lookback_days else (found_day, close)


def _separable(factor, band):
    """Whether the two readings of one price are far enough apart to tell apart.

    ``band`` is how far a fill may sit from its own session's close, so the
    "as executed" reading occupies ``[1/band, band]`` and the "already adjusted"
    reading occupies that interval divided by ``factor``. They are disjoint
    exactly when the factor is outside ``[1/band², band²]``. Inside it the
    evidence cannot distinguish the two, and the answer is silence.
    """
    edge = band * band
    return factor > edge or factor < 1.0 / edge


def basis_disagreements(rows, closes, splits, *, band=EXECUTION_BAND, lookback_days=5):
    """Tickers whose recorded prices contradict the basis they were rebased onto (#582).

    ``rows`` are ``trade_recap.load()``-shaped dicts **after** :func:`rebase_rows`,
    ``closes`` is ``{ticker: [(date, close), ...]}`` from the retro-adjusted price
    frame the review already fetched, and ``splits`` is the very map the rebase
    used. Nothing here retrieves anything: the check exists because the engine
    already held both halves of the comparison and never put them side by side.

    The arithmetic is the whole argument. A rebased row and a retro-adjusted
    close for the same day are supposed to be the same kind of number, so their
    ratio is ``≈ 1`` — that is what "as executed" looks like, and it is what the
    engine assumes without checking. If the file was already adjusted, the row
    carried today's-basis price in, :func:`rebase_rows` divided it by the factor
    a second time, and the ratio is ``≈ 1/factor`` instead: a tenfold
    disagreement on a ten-for-one, not a matter of degree.

    What this deliberately does not do is decide which it is. An adjusted
    export, a currency written in the wrong unit and a genuinely strange fill
    can all print the same disagreement, and choosing between them from the
    numbers is the engine-side adjudication #416 forbids — so this reports the
    contradiction and its evidence, and the caller states it and asks. It never
    re-bases anything and never writes.

    Three properties keep it cheap and quiet:

    * **Only tickers the map carries a split for are looked at.** A ticker with
      no split event in the window is never indexed, never compared, and cannot
      produce a finding.
    * **Only rows a split falls after are examined.** After the split both
      readings agree, which is exactly why #330's check — anchored on the
      ticker's *most recent* trade — cannot see this defect at all.
    * **Every examined row must agree.** An export basis is a property of the
      whole file, so one row reading "as executed" and another "already
      adjusted" is a different problem and this stays silent rather than
      claiming the one it happens to be looking for.

    Returns a list of ``{ticker, examined_n, splits, rows}`` sorted by ticker,
    JSON-safe, with the row evidence capped so a caller can print it. Empty is
    the answer for an ordinary file, and for every book with no split at all.
    """
    events_by_ticker = normalize(splits)
    if not events_by_ticker or not closes:
        return []
    findings = []
    for ticker in sorted(events_by_ticker):
        events = events_by_ticker[ticker]
        index = _close_index((closes or {}).get(ticker), ticker)
        if not index:
            continue
        examined, verdicts, factors = [], set(), []
        for row in rows or ():
            if row.get("ticker") != ticker:
                continue
            day = _date(row.get("date"), f"{ticker}: trade date")
            factor = factor_after(events, day)
            if not _separable(factor, band):
                continue                       # post-split, or a split too small to read
            found = _close_on(index, day, lookback_days)
            if found is None:
                continue                       # the frame does not reach this trade
            try:
                price = float(row.get("price"))
            except (TypeError, ValueError):
                continue
            if not price > 0:
                continue
            close_day, close = found
            ratio = price / close
            if 1.0 / band <= ratio <= band:
                verdicts.add("as_executed")
            elif 1.0 / band <= ratio * factor <= band:
                verdicts.add("already_adjusted")
            else:
                verdicts.add("unexplained")
            factors.append(factor)
            examined.append({"date": day.isoformat(),
                             "rebased_price": round(price, 6),
                             "market_close": round(close, 6),
                             "close_date": close_day.isoformat(),
                             "ratio": round(ratio, 6),
                             "split_factor": round(factor, 6)})
        if not examined or verdicts != {"already_adjusted"}:
            continue
        earliest = min(row["date"] for row in examined)
        findings.append({
            "ticker": ticker,
            "examined_n": len(examined),
            "factor": round(max(factors), 6),
            "splits": [[day.isoformat(), ratio] for day, ratio in events
                       if day.isoformat() > earliest],
            # Evidence, not a dump: three rows say the same thing a hundred do,
            # and this list is printed back to a person.
            "rows": examined[:3],
        })
    return findings
