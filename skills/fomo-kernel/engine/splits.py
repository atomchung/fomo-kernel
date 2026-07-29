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
never *finds* splits — the retrieval policy (``trade_recap.fetch_splits``:
yfinance, or an agent-supplied envelope via ``price_feed.splits_map``) stays
where it is, and its result is passed in. A caller with no split data gets the
unadjusted answer, which is the pre-existing behavior; it does not get a
guessed one.

Input is fail-closed. A split ratio is a multiplier on someone's share count;
a malformed one that were quietly dropped would produce a confident wrong
number, which is the failure this module exists to remove.
"""
import datetime as dt

__all__ = [
    "SplitDataError",
    "normalize",
    "to_json",
    "factor_after",
    "factor_between",
    "rebase_rows",
]


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
        name = str(ticker or "").strip()
        if not name:
            raise SplitDataError("split events carry an empty ticker")
        if not isinstance(rows, (list, tuple)):
            raise SplitDataError(f"{name}: split events must be a list")
        events = []
        for i, row in enumerate(rows):
            where = f"{name}[{i}]"
            if not isinstance(row, (list, tuple)) or len(row) != 2:
                raise SplitDataError(f"{where}: expected [date, ratio]")
            events.append((_date(row[0], where), _ratio(row[1], where)))
        if events:
            # Sorted once here so every reader below can assume date order and
            # none of them has to remember to sort (or silently not).
            out[name] = sorted(events, key=lambda pair: pair[0])
    return out


def to_json(splits):
    """The canonical map as JSON-safe data, for freezing into a review state."""
    return {ticker: [[day.isoformat(), ratio] for day, ratio in events]
            for ticker, events in normalize(splits).items()}


def factor_after(events, date):
    """Cumulative ratio of the splits strictly after ``date``.

    This is the "rebase onto today" factor: a trade is multiplied by every
    split that happened after it. A split dated on the trade date itself does
    not count — the ex-date's own fills already print in post-split terms.
    """
    factor = 1.0
    for day, ratio in events or ():
        if day > date:
            factor *= ratio
    return factor


def factor_between(events, after, upto):
    """Cumulative ratio of the splits in ``(after, upto]``.

    This is the "carry a running position forward" factor. ``after`` is the
    date the position was last stated in its own basis (``None`` = the position
    has no history yet, so every split up to ``upto`` applies — to zero shares,
    which is why an old split before a ticker's first trade is harmless rather
    than something each caller must remember to skip).
    """
    factor = 1.0
    for day, ratio in events or ():
        if (after is None or day > after) and day <= upto:
            factor *= ratio
    return factor


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
