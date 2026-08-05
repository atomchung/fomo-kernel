#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""market_data.py — the one acquisition boundary between a market-data source
and the deterministic engine (#605).

Before this module every workflow that needed a current market fact rediscovered
Yahoo symbol formatting, FX direction, cache policy, partial failure, freshness
and supplied-feed precedence for itself. Four live retrieval points did it four
times: ``trade_recap.fetch_prices`` (one batched download), ``fetch_splits``
(per ticker), ``fetch_fx`` (per currency) and ``fetch_fx_series`` (a second
batched download). Measured on a six-instrument, two-currency book that is 17
provider requests where the floor is 8 — ``fetch_splits`` re-requests the exact
chart endpoint the price download already hit for every ticker, and FX is
fetched twice. Worse than the latency, the four results describe four different
instants: ``^VIX``'s last close differed between two calls seconds apart while
every settled symbol matched, so a review could state a price, a split and a
rate that never coexisted.

So acquisition is one request in, one dated bundle out, and everything
downstream computes against that single bundle.

**What this module does not do.** It computes no weight, return, P&L,
concentration, recommendation or state transition. ``PortfolioBasis``,
``ValuationFrame``, ``trade_recap``, ``consequence`` and ``perf`` remain the
owners of book and calculation truth; this owns only *what was observed, when,
from where, and what is missing*. A split here is an **observation** the source
returned, never an adjustment — ``engine/splits.py`` still owns what a split
does to a quantity, and it is still stdlib-only and offline.

**One request per distinct symbol, never twice.** ``yf.download`` reads like a
batch endpoint and is not one: it is a thread-pool fan-out issuing one
``/v8/finance/chart/<SYMBOL>`` request per symbol. The achievable floor is
therefore one request per distinct symbol, and the contract here is that a
resolver pass never exceeds it. That is why instruments, benchmarks, market
context symbols and ``{CUR}=X`` FX pairs all travel in *one* universe with
``actions=True``: closes, split observations and FX come out of the same
response, on one instant, for one request each. :func:`resolve` also memoizes
in-process, so a compatibility wrapper calling it a second time for a subset of
an already-resolved universe costs zero requests even when no disk cache is
reachable.

**The split window is load-bearing, and it is why the request carries a rebase
origin.** ``fetch_splits`` used ``Ticker(t).splits``, which is unbounded — AAPL
back to 1987. A windowed download returns only the splits *inside* the window,
so a naive consolidation trades 9 requests for a factor of 1.0 where a real
split exists: the silent tenfold error #583 exists to prevent. It is safe for a
stated reason rather than by luck. Every consumer applies only splits strictly
*after* some real book date — ``ledger.derive_holdings`` from the anchor date or
a trade date (``basis_date`` is seeded at position creation, so
``splits.factor_between``'s unbounded ``after=None`` arm is unreachable for a
position that has shares), ``splits.rebase_rows`` from each row's date,
``price_feed``/``portfolio_basis``/``revisit`` from an observation or exit date.
A window starting at or before the earliest such date therefore loses nothing.

``rebase_origin`` is that earliest date, stated by the caller and stamped on the
bundle, and :func:`MarketDataBundle.covers` refuses a reuse that would need
splits from before it. The caller must derive it from the union of every
consumer's origin — for a review that is the earliest CSV trade **and** the
ledger anchor, because the map ``prepare`` freezes is later read by ``consider``
against an anchor ``prepare``'s own CSV never mentioned. A missing split does
not announce itself, so the only safe posture is that an uncovered origin is a
miss that re-resolves, never a quiet 1.0.

**Offline is a value this module reads, not discipline at each call site.**
``consider`` is reached by ninety-odd tests that pass no ``--prices`` and
provably touch no network today; making it resolve automatically would make
every one of them network-dependent on any machine with yfinance installed, and
would move the literal ``evaluation_id`` digest ``tests/test_consider.py`` pins.
``TR_OFFLINE=1`` (set once by ``tests/run_all.py``, so a new suite inherits it
rather than remembering it) makes the Yahoo adapter degrade *exactly* as a
missing yfinance already does — same empty bundle, same gap shape — which is
what keeps the existing suite a real oracle instead of a rewritten one. This is
the same conclusion #605's own ``cmd_exposure`` probe reached about split
discipline: a rule that lives in a shared primitive survives being copied into
a new route, and a rule that lives in a call site does not.

**Failure is explicit and never numeric.** Every degradation is a stable
``gaps`` code plus the coverage that is actually missing. Nothing here converts
an unavailable fact into zero, into a delisting, or into an identity FX rate;
the callers' established refusals (``consequence.portfolio_state`` on a missing
rate, ``price_feed.basis_conflicts`` on an unestablishable split basis) remain
the residual floor.

**"Unpriced" was two facts wearing one shape, and the conflation cost requests
(#687, #743).** ``yf.download`` does not raise when a symbol's own request
fails: it catches per symbol and returns an *empty column*, so a rate-limited
symbol arrives in exactly the shape of a delisted one. Read as a delisting, a
throttle became a permanent fact about a holding — and, because a bundle's
coverage was the set it had *priced*, it also made that bundle fail to cover
**the request that produced it**, re-fetching the whole universe on the next
same-day command and issuing more requests into the limit that caused it.

So the seam reports what failed (``_download`` returns ``(data, errors)``) and
the bundle carries :attr:`MarketDataBundle.unresolved`. A symbol the provider
*answered* nothing for is final for the day and covered; one whose request never
came back is outstanding, is not covered, and gets the retry #235's rule is
about. The distinction is tri-state on the wire — absent, not empty, on a bundle
that cannot state it — so an older cache entry keeps the strict rule instead of
being read as a clean bill of health.
"""
import datetime as dt
import os

import fetch_cache
import price_feed
import splits as split_policy
import symbols  # #803 一條 ticker identity 規則,與 price_feed 共用

__all__ = [
    "MarketDataError",
    "MarketDataBundle",
    "OFFLINE_ENV",
    "FROZEN_ENV",
    "GAP_CODES",
    "network_allowed",
    "frame_frozen",
    "build_request",
    "resolve",
    "reset_memo",
]

OFFLINE_ENV = "TR_OFFLINE"
#: A pass that may only reuse the exact frame an earlier pass already resolved
#: for the identical request, and may never reach a provider. `review.py
#: add-cash` sets it: that command amends a review the user has already been
#: shown, so a second observation instant is not fresher data, it is a different
#: review wearing the same session (#665).
FROZEN_ENV = "TR_MARKET_DATA_FROZEN"
CACHE_KIND = "market_data"

#: Every degradation this module can report. Stable strings: a caller may branch
#: on them and a receipt may archive them, so a code is renamed only with its
#: readers. ``detail`` carries the human half.
GAP_CODES = (
    "network_disabled",     # TR_OFFLINE — the process may not reach a provider
    "provider_missing",     # yfinance is not installed
    "transport_failed",     # the request raised (DNS, timeout, HTTP, rate limit)
    "empty_response",       # the provider answered with no rows at all
    "response_shape",       # the response did not carry the expected fields
    "symbol_unpriced",      # a requested symbol came back with no usable close
    "symbol_rate_limited",  # the provider refused this symbol's request for rate
    "symbol_request_failed",  # this symbol's own request failed for another reason
    "fx_unavailable",       # a requested currency has no usable rate
    "feed_incomplete",      # a supplied envelope does not cover what was asked
    "frozen_frame_gone",    # a frozen pass found no frame recorded for this request
)

# One resolved bundle per normalized request, for this process only. The disk
# cache cannot carry this weight alone: `fetch_cache.store` deliberately
# refuses to create the state root, so a bare engine run (a test, a first-ever
# review) has no cache at all — and "never request a symbol twice" has to hold
# there too, or the guarantee is one an ordinary offline test cannot observe.
_MEMO = {}


class MarketDataError(ValueError):
    """Raised when a request cannot be trusted as stated.

    Only ever a *request* problem — a malformed universe, a window that cannot
    cover its own rebase origin. A provider that fails is a ``gaps`` entry on a
    returned bundle, never an exception: acquisition failing must degrade the
    review, not crash it.
    """


# ───────────────────────────── the request ─────────────────────────────

def _iso(value, where):
    """Coerce a window edge, the same fail-closed way ``splits._date`` does."""
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    try:
        return dt.date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        raise MarketDataError(f"{where}: {value!r} is not an ISO date") from None


def _symbols(values, where):
    """A deterministic, de-duplicated symbol list. Order is sorted, not given:
    the request is hashed for the cache key and memo, so two callers naming the
    same universe in a different order must produce one entry, not two."""
    out = set()
    for value in values or ():
        # #803: the request is one side of a join — `coverage()` and
        # `to_price_feed_envelope` test it against `bundle.priced`, which the
        # parsed feed keys canonically. Stripping without folding case is how a
        # quote that was actually retrieved comes back as `missing`, and it is
        # also a second cache entry for one universe. `_currencies` below has
        # folded case for exactly as long as it has existed.
        name = symbols.canonical_ticker(value) or str(value or "").strip()
        if not name:
            raise MarketDataError(f"{where} carries an empty symbol")
        out.add(name)
    return sorted(out)


def _currencies(values):
    """Non-USD currency codes. USD is the numeraire and never a request:
    ``fetch_fx``'s identity anchor is arithmetic, not an observation, and asking
    a provider for ``USD=X`` would spend a request to be told 1.0."""
    out = set()
    for value in values or ():
        code = str(value or "").strip().upper()
        if code and code != "USD":
            out.add(code)
    return sorted(out)


def build_request(*, instruments=(), benchmarks=(), currencies=(),
                  window_start, window_end=None, rebase_origin=None):
    """Normalize and validate one acquisition request.

    ``window_start`` is the earliest date any *series* is needed from — the
    caller's own analytics window. ``rebase_origin`` is the earliest date any
    consumer will rebase a split *from*, which is a different and usually older
    fact (see the module docstring): a review's price window may begin at its
    first trade while the ledger anchor it also has to satisfy is older. It
    defaults to ``window_start`` only because that is the honest reading of a
    caller that did not state one.

    The one refusal here is a window that cannot cover its own rebase origin.
    That is not a tidiness check: past it, the bundle's split observations would
    silently omit an event a consumer is about to look for, and a missing split
    reads as a factor of 1.0 with nothing anywhere looking wrong.
    """
    start = _iso(window_start, "window_start")
    end = None if window_end is None else _iso(window_end, "window_end")
    origin = start if rebase_origin is None else _iso(rebase_origin, "rebase_origin")
    if end is not None and end < start:
        raise MarketDataError(f"window_end {end} precedes window_start {start}")
    if origin < start:
        raise MarketDataError(
            f"rebase_origin {origin} precedes window_start {start}: split observations "
            "would be incomplete for a date a consumer will rebase from, which reads as "
            "no split at all — widen the window instead")
    request = {
        "instruments": _symbols(instruments, "instruments"),
        "benchmarks": _symbols(benchmarks, "benchmarks"),
        "currencies": _currencies(currencies),
        "window_start": start,
        "window_end": end,
        "rebase_origin": origin,
    }
    if not (request["instruments"] or request["benchmarks"] or request["currencies"]):
        raise MarketDataError("a market-data request names no instrument, benchmark or currency")
    return request


def fx_symbol(currency):
    """Yahoo's spelling for a currency pair. One definition, because the
    request, the response read and the cache key all have to agree on it."""
    return f"{str(currency).strip().upper()}=X"


def request_universe(request):
    """Every symbol one pass must ask for, de-duplicated across all three
    groups. An instrument that is also a benchmark (a user who holds SPY) is one
    request, not two — which is the whole contract this module exists to hold."""
    return sorted(set(request["instruments"]) | set(request["benchmarks"])
                  | {fx_symbol(c) for c in request["currencies"]})


def _request_key(request):
    return fetch_cache._key(request)  # noqa: SLF001  # one digest definition, shared


# ───────────────────────────── the bundle ─────────────────────────────

class MarketDataBundle:
    """One dated set of acquisition facts, and what it could not observe.

    Deliberately not a dataclass carrying free-form extras: every field below is
    either an observation or a statement about coverage, and a calculated value
    that appeared here would be one this module has no business owning.
    """

    def __init__(self, *, source, request, frame=None, splits=None, fx=None,
                 fx_frame=None, gaps=None, as_of=None, unresolved=None):
        self.source = source                      # yahoo | supplied | unavailable
        self.request = request
        #: Symbols whose own request did not come back — rate limited, or failed
        #: for another reason. Tri-state on purpose. ``None`` means *this bundle
        #: does not state it*: a supplied envelope, a frozen frame, or a cache
        #: entry written before this field existed. Those keep the original
        #: strict coverage rule, so an older entry can never be read as "every
        #: unpriced symbol was definitively answered" and silently suppress a
        #: retry it never earned. ``[]`` is the positive statement that the
        #: provider answered for everything asked.
        self.unresolved = None if unresolved is None else sorted(set(unresolved))
        self.frame = frame                        # closes: index=dates, columns=symbols
        self.splits = splits or {}                # {ticker: [(date, ratio), ...]}
        self.fx = dict(fx or {"USD": 1.0})        # {currency: usd_per_unit}
        self.fx_frame = fx_frame                  # daily usd_per_unit, columns=currency
        self.gaps = sorted(gaps or [], key=lambda row: (row["code"], row.get("detail") or ""))
        self._as_of = as_of

    # ── observation metadata ──

    @property
    def as_of(self):
        """The newest observation date in this bundle, or ``None``.

        A frame-level date, and deliberately *not* a per-ticker one: that is
        ``trade_recap.price_observations``' job and #583 §2 is the reason the two
        must not be conflated. Here it answers only "how fresh is this bundle".
        """
        if self._as_of is not None:
            return self._as_of
        if self.frame is not None and len(self.frame.index):
            return self.frame.index[-1].date().isoformat()
        return None

    @property
    def window(self):
        return {"start": self.request["window_start"], "end": self.request["window_end"]}

    @property
    def rebase_origin(self):
        return self.request["rebase_origin"]

    @property
    def priced(self):
        """Symbols this bundle can actually value. A column of NaN is not a
        price: a delisted or misspelled symbol comes back present and empty."""
        if self.frame is None:
            return []
        return sorted(str(column) for column in self.frame.columns
                      if bool(self.frame[column].notna().any()))

    def coverage(self):
        """What was asked for, what came back, and what is missing — machine
        readable, because a caller that cannot see the gap states a number over
        it. FX symbols are excluded: a rate's coverage is ``fx`` and its own
        ``fx_unavailable`` gap, and mixing the two made "how many of my
        holdings are priced" unanswerable."""
        wanted = sorted(set(self.request["instruments"]) | set(self.request["benchmarks"]))
        have = set(self.priced)
        return {"requested": wanted,
                "priced": [s for s in wanted if s in have],
                "missing": [s for s in wanted if s not in have],
                "currencies_requested": list(self.request["currencies"]),
                "currencies_resolved": [c for c in self.request["currencies"] if c in self.fx]}

    @property
    def usable(self):
        """Whether this bundle observed anything worth caching or computing on.
        An empty bundle is a truthful answer and still must not be frozen as a
        successful day: #235's rule that a partial or failed fetch is retried
        rather than cached is the one this preserves."""
        return bool(self.priced) or bool(
            [c for c in self.request["currencies"] if c in self.fx])

    # ── reuse ──

    def _answered_symbols(self):
        """Symbols this bundle has a *final* same-day answer for.

        A price is an answer. So is a definitive "the provider returned nothing
        for this symbol" — but only from a bundle that can tell the difference,
        which is why `unresolved is None` falls back to the priced set alone.
        """
        priced = set(self.priced)
        if self.unresolved is None:
            return priced
        asked = set(self.request["instruments"]) | set(self.request["benchmarks"])
        return priced | (asked - set(self.unresolved))

    def _answered_currencies(self):
        """The same question for rates. A currency whose ``{CUR}=X`` request
        failed is unresolved; one the provider simply has no usable rate for is
        answered, and re-asking it today would re-fetch the whole universe for
        an answer that cannot have changed."""
        resolved = {code for code in self.request["currencies"] if code in self.fx}
        if self.unresolved is None:
            return resolved
        unresolved = set(self.unresolved)
        return resolved | {code for code in self.request["currencies"]
                           if fx_symbol(code) not in unresolved}

    def covers(self, request):
        """Whether this bundle can answer ``request`` with no new request.

        Three axes, and every one of them has to hold. The symbol set must be a
        superset — a subset request over an already-fetched universe is the whole
        point of #605's §D, and it is what lets a same-day ``prepare`` bundle
        answer a later ``consider`` for free. The window must start no later; and
        the end edge must not be narrower than what is being asked for, since an
        open-ended request (current prices) cannot be served from a bundle that
        stopped at a fixed date.

        **Split completeness is the window axis, not a fourth one.** These
        observations are complete for every date at or after ``window_start``, a
        request needs splits strictly after its own ``rebase_origin``, and
        :func:`build_request` refuses a request whose origin precedes its window
        start. So ``self.window_start <= request.window_start <=
        request.rebase_origin`` and completeness follows. Comparing the two
        *declared* origins here instead looked like extra safety and was strictly
        worse: a bundle fetched from 2020 but asked for a 2026 origin would have
        refused a 2021-origin request whose data it demonstrably holds — a wasted
        provider pass, dressed as rigour. The invariant is enforced once, where it
        is knowable, which is what keeps two readers from disagreeing about it.
        """
        # Coverage is about what this bundle *resolved*, not what it was asked
        # for. A bundle that requested {GOOD, DEAD} and priced only GOOD must not
        # "cover" a later request for DEAD by handing back a bundle with no DEAD
        # price when that symbol's request is the thing that failed — that would
        # suppress the retry a transient outage deserves (external review,
        # finding 5).
        #
        # But "unpriced" was two different facts wearing one shape, and #687 is
        # what the conflation costs. *The provider answered and there is no close*
        # — delisted, misspelled — is definitive: re-asking the same day cannot
        # change it, yet the strict rule made such a bundle fail to cover **the
        # exact request that produced it**, so every same-day command re-fetched
        # the whole universe. *This symbol's own request never came back* is the
        # transient one the paragraph above is about. Only the second may force a
        # re-resolve, and `unresolved` is what separates them (see `_download`).
        answered = self._answered_symbols()
        if set(request["instruments"]) - answered:
            return False
        if set(request["benchmarks"]) - answered:
            return False
        if set(request["currencies"]) - self._answered_currencies():
            return False
        if self.request["window_start"] > request["window_start"]:
            return False
        mine, theirs = self.request["window_end"], request["window_end"]
        if theirs is None and mine is not None:
            return False
        if theirs is not None and mine is not None and mine < theirs:
            return False
        return True

    # ── serialization (cache only; never canonical session state) ──

    def to_json(self):
        """JSON-safe, and lossless for everything a later reader consumes.

        NaN is not valid JSON, so a missing close is stored as ``null`` and read
        back as NaN — the same convention ``fetch_cache``'s price entry already
        used, and the reason a gap survives a cache round trip as a gap instead
        of as a zero.
        """
        return {
            "source": self.source,
            "request": self.request,
            "as_of": self.as_of,
            "frame": _frame_to_json(self.frame),
            "fx_frame": _frame_to_json(self.fx_frame),
            "splits": split_policy.to_json(self.splits),
            "fx": {code: rate for code, rate in sorted(self.fx.items())},
            "gaps": self.gaps,
            # Absent, not empty, on a bundle that does not state it — `from_json`
            # reads the difference and an older entry keeps the strict rule.
            "unresolved": self.unresolved,
        }

    @classmethod
    def from_json(cls, blob):
        """Rebuild a cached bundle, or return ``None`` when it cannot be read.

        Never raises. A cache is an optimization: an entry written by an older
        engine, truncated, or carrying a shape this version does not understand
        is a miss that re-resolves, never a failure the user sees.
        """
        if not isinstance(blob, dict):
            return None
        try:
            request = build_request(
                instruments=blob["request"]["instruments"],
                benchmarks=blob["request"]["benchmarks"],
                currencies=blob["request"]["currencies"],
                window_start=blob["request"]["window_start"],
                window_end=blob["request"]["window_end"],
                rebase_origin=blob["request"]["rebase_origin"])
            return cls(source=blob["source"], request=request,
                       frame=_frame_from_json(blob.get("frame")),
                       fx_frame=_frame_from_json(blob.get("fx_frame")),
                       splits=split_policy.normalize(blob.get("splits")),
                       fx=blob.get("fx") or {"USD": 1.0},
                       gaps=blob.get("gaps") or [],
                       as_of=blob.get("as_of"),
                       unresolved=blob.get("unresolved"))
        except (KeyError, TypeError, ValueError, MarketDataError,
                split_policy.SplitDataError):
            return None


def _frame_to_json(frame):
    if frame is None or not len(frame.index):
        return None
    return {
        "index": [idx.date().isoformat() for idx in frame.index],
        "columns": [str(column) for column in frame.columns],
        "data": [[None if (value is None or value != value) else float(value)
                  for value in row] for row in frame.to_numpy()],
    }


def _frame_from_json(blob):
    if not blob:
        return None
    try:
        import pandas as pd
    except ImportError:
        return None
    try:
        return pd.DataFrame(blob["data"], columns=blob["columns"],
                            index=pd.to_datetime(blob["index"]))
    except (KeyError, TypeError, ValueError):
        return None


def _gap(code, detail=None):
    if code not in GAP_CODES:
        raise MarketDataError(f"unknown market-data gap code {code!r}")
    return {"code": code, "detail": detail} if detail else {"code": code}


def _unavailable(request, gaps, source="unavailable", unresolved=None):
    return MarketDataBundle(source=source, request=request, gaps=gaps,
                            unresolved=unresolved)


def _symbol_failure_gap(symbol, detail):
    """One symbol's own request having failed, told apart from an outage.

    Rate limiting is the retryable one and the one this repository has already
    identified as common, so it gets its own stable code: a caller that cannot
    distinguish "the source is limiting us, this is worth another moment" from
    "the source is not answering" cannot say either to the user (#743).
    """
    if _rate_limited(detail):
        return _gap("symbol_rate_limited",
                    f"{symbol}: the provider rate-limited this symbol's request")
    return _gap("symbol_request_failed", f"{symbol}: this symbol's request failed ({detail})")


# ───────────────────────── the supplied adapter ─────────────────────────

def _from_feed(request, feed):
    """The agent-supplied envelope, normalized into the same bundle.

    Authoritative for what it declares and never topped up from Yahoo: a host
    supplies an envelope precisely because this machine cannot reach the
    provider, so a hidden fetch would either fail slowly or mix two sources into
    one aggregate. ``price_feed`` has already validated and split-normalized
    every close on the way in, so there is nothing to re-derive here — only
    coverage to state honestly.
    """
    universe = sorted(set(request["instruments"]) | set(request["benchmarks"]))
    frame, error = price_feed.to_frame(feed, universe)
    gaps = []
    if frame is None and error:
        gaps.append(_gap("feed_incomplete", error))
    rates = price_feed.fx_rates(feed)
    fx = {"USD": 1.0}
    for code in request["currencies"]:
        if code in rates:
            fx[code] = rates[code]
        else:
            gaps.append(_gap("fx_unavailable",
                             f"{code}: the supplied price feed declares no rate"))
    bundle = MarketDataBundle(
        source="supplied", request=request, frame=frame,
        splits=price_feed.splits_map(feed, universe),
        fx=fx,
        # An envelope carries spot rates only, by schema. The daily series stays
        # honestly absent so account-level valuation degrades to the documented
        # spot approximation rather than to a fabricated flat curve.
        fx_frame=None, gaps=gaps,
        as_of=(feed or {}).get("as_of"))
    for symbol in bundle.coverage()["missing"]:
        bundle.gaps.append(_gap("symbol_unpriced",
                                f"{symbol}: not covered by the supplied price feed"))
    bundle.gaps.sort(key=lambda row: (row["code"], row.get("detail") or ""))
    return bundle


# ────────────────────────── the Yahoo adapter ──────────────────────────

def network_allowed(env=None):
    """The single reader of whether this process may reach a provider.

    One function so the posture cannot be re-decided per route — the failure
    mode #605's ``cmd_exposure`` probe measured, where a new workflow inherited
    exactly the one rule that lived in a shared primitive.
    """
    value = (env if env is not None else os.environ).get(OFFLINE_ENV, "")
    return str(value).strip().lower() not in {"1", "true", "yes", "on"}


def frame_frozen(env=None):
    """Whether this process may only reuse an already-resolved frame (#665).

    A posture, read here for the same reason :func:`network_allowed` is: it must
    not be re-decided per route, or the next workflow inherits everything except
    the one rule that mattered. ``resolve`` is the single consumer.

    The distinction from ``TR_OFFLINE`` is the point. Offline means *this machine
    cannot reach a provider*, and the honest answer is a degraded review. Frozen
    means *this pass must answer with the frame an earlier pass already resolved*
    — the review is not being computed for the first time, it is being amended,
    and closes that moved since the user read the card are not fresher facts but
    a second review. Where offline degrades, frozen reuses.
    """
    value = (env if env is not None else os.environ).get(FROZEN_ENV, "")
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _from_frozen_frame(request, *, root, today=None):
    """The exact frame recorded for this request, or a stated absence.

    Exact key, not :meth:`MarketDataBundle.covers`. Coverage is the right rule
    for the ordinary cache — it lets a narrower later request ride along free,
    and it deliberately refuses to serve a request naming a symbol the stored
    bundle failed to price, so a transient provider outage gets the retry it
    deserves (#605 external review, finding 5). That refusal is exactly wrong
    here, and it is what #665 was: one unpriced symbol anywhere in the universe
    made every later pass on the same book re-resolve, `add-cash` re-priced the
    whole review at a second instant, and the recompute's own moved numbers were
    then reported to the user as "the facts moved". A pass amending a review does
    not want a retry; it wants the frame the card it is amending was rendered
    from, gaps and all.

    Never reaches the provider, on any outcome. A miss is stated as a gap and the
    review degrades exactly as an unreachable provider would, which the caller's
    own gate then refuses — the frame this session was built on being gone is a
    real change of facts, not something to paper over with fresh closes.
    """
    blob = fetch_cache.load(CACHE_KIND, request, root=root, today=today)
    bundle = MarketDataBundle.from_json(blob) if blob is not None else None
    if bundle is not None and bundle.usable:
        return bundle
    return _unavailable(request, [_gap(
        "frozen_frame_gone",
        "this pass may only reuse the frame already resolved for this request, "
        "and no usable frame is recorded for it")])


def _provider_available():
    """Whether :func:`_download` below could run at all — the other half of the
    provider seam, and module-level for exactly the same two reasons.

    This answer used to be an inline ``import yfinance`` probe inside
    ``_from_yahoo``, which put a second short-circuit *above* ``_download`` that
    no fake provider replaced. CI installs no yfinance on purpose, so every
    stubbed test returned ``provider_missing`` before reaching the fake: three of
    them reported "the route made no provider request at all" on every CI run
    for a release, while passing on any developer machine that happened to have
    yfinance installed (#621). A seam that only one half of can be replaced is
    not a seam — replacing the call without replacing the availability answer
    produces a fake that never runs.

    Kept here rather than folded into ``_download`` because ``_from_yahoo``'s
    order is load-bearing (#235): this must stay *above* the cache read, so an
    offline or provider-less run degrades before a stale entry is reachable.
    Moving it into ``_download`` would put it below that read.
    """
    try:
        import yfinance  # noqa: F401  # probe only; _download does the work
    except ImportError:
        return False
    return True


#: Substrings that mark a per-symbol failure as the provider throttling us
#: rather than failing. ``YFRateLimitError`` is what yfinance raises on an HTTP
#: 429; the other two catch the same condition arriving as a plain message from
#: a version that spells it differently.
_RATE_LIMIT_MARKERS = ("YFRateLimitError", "Too Many Requests", "429")


def _rate_limited(detail):
    return any(marker in str(detail or "") for marker in _RATE_LIMIT_MARKERS)


def _download(symbols, start, end=None):
    """The one provider call in this repository. Returns ``(data, errors)``.

    Isolated behind a module-level name for two reasons: the contract suite
    replaces it with a fake and counts invocations (the mechanical half of "one
    request per distinct symbol, never twice"), and it keeps the ``yfinance``
    import — the thing every guard greps for — inside this provider seam, which
    is this function and :func:`_provider_available` above it and nowhere else.

    ``actions=True`` is what makes closes and split observations come from one
    response; ``auto_adjust=True`` matches the basis every existing reader
    assumes (verified: with actions present, closes are still retro-adjusted, so
    a split leaves no step in the series).

    **Why this returns errors as well as data (#687).** ``yf.download`` does not
    raise when a symbol's request fails. It catches per symbol, records the
    exception in a private module global, and hands back an **empty column** —
    so a rate-limited symbol is delivered in exactly the shape of a delisted
    one. Measured against yfinance 1.3.0 with every HTTP entry point replaced by
    one raising ``YFRateLimitError``: no exception, a well-formed frame, and the
    limited symbol simply absent from ``priced``. Downstream that became
    ``symbol_unpriced`` — the delisting code — and the ambiguity is what makes a
    cached bundle fail to cover its own request, re-fetching the whole universe
    on the next same-day command and issuing more requests into the very limit
    that caused it.

    Reading ``shared._ERRORS`` here is the *only* place that ambiguity can be
    resolved, because it is the only place the distinction still exists. It is
    returned rather than exposed as a second seam function on purpose: #621's
    lesson is that a seam only half of which can be replaced is not a seam, and
    a fake replacing ``_download`` alone would leave a reader of the real global
    reporting whatever a previous real download had left behind.

    It is a private global, so it is read defensively and its absence degrades
    to "nothing is known to have failed" — which is exactly the behaviour that
    predates this, never a fabricated failure.
    """
    import yfinance as yf
    kwargs = {"start": start, "progress": False, "auto_adjust": True, "actions": True}
    if end is not None:
        kwargs["end"] = end
    data = yf.download(list(symbols), **kwargs)
    try:
        from yfinance import shared as _yf_shared
        errors = {str(symbol): str(detail)
                  for symbol, detail in dict(_yf_shared._ERRORS).items()}  # noqa: SLF001
    except Exception:  # noqa: BLE001  # a private global; its absence is not a failure
        errors = {}
    return data, errors


def _field(data, name):
    """One field of the response as ``{symbol: [(date, value), ...]}``.

    ``actions=True`` makes the columns a two-level MultiIndex even for a single
    symbol, which removes the ``ndim == 1`` special case each old fetcher
    carried separately. A shape this does not recognize returns ``None`` so the
    caller degrades rather than raising: "never crash the review" is the older
    contract and it still holds.
    """
    try:
        if getattr(data.columns, "nlevels", 1) < 2:
            return None
        block = data[name]
    except (KeyError, TypeError, AttributeError):
        return None
    out = {}
    for column in block.columns:
        series = block[column]
        # Both spellings of "no observation" are dropped here. A provider column
        # is normally float NaN, but a mixed-dtype block can carry ``None``, and
        # ``None == None`` is true — so a NaN-only filter would let it through
        # and ``float(None)`` would take the review down with a TypeError.
        out[str(column)] = [(idx.date(), float(value)) for idx, value in series.items()
                            if value is not None and value == value]
    return out


def _from_yahoo(request, *, root, today=None, env=None):
    """Resolve from Yahoo in one pass, or degrade with a stated reason.

    Order is load-bearing and inherited from #235: the cache is consulted only
    *past* the offline and import checks, so an offline run returns its degraded
    answer before a stale entry is reachable and a deterministic test can never
    read yesterday's closes.
    """
    if not network_allowed(env):
        return _unavailable(request, [_gap(
            "network_disabled",
            f"{OFFLINE_ENV} is set; no market data was retrieved")])
    if not _provider_available():
        return _unavailable(request, [_gap(
            "provider_missing", "yfinance is not installed")])

    cached = _cache_load(request, root=root, today=today)
    if cached is not None:
        return cached

    universe = request_universe(request)
    try:
        data, errors = _download(universe, request["window_start"], request["window_end"])
    except Exception as exc:  # noqa: BLE001  # transport failures are many-shaped
        return _unavailable(request, [_gap("transport_failed", f"{type(exc).__name__}: {exc}")])
    if data is None or not len(data):
        # Every symbol failing is the *shape* of an empty response, and saying
        # "the provider returned no rows" over a universe that was rate limited
        # is the same conflation `_download` exists to end — one degree larger.
        # When the failures are known, they are what gets reported.
        failed = sorted(set(errors) & set(universe))
        failures = [_symbol_failure_gap(symbol, errors[symbol]) for symbol in failed]
        return _unavailable(
            request,
            failures or [_gap("empty_response", "the provider returned no rows")],
            unresolved=failed)

    closes = _field(data, "Close")
    if closes is None:
        return _unavailable(request, [_gap(
            "response_shape", "the response carries no Close field per symbol")])
    actions = _field(data, "Stock Splits")
    if actions is None:
        # The request asked for actions; a response without them cannot say
        # whether a split happened, and "no split field" is indistinguishable
        # here from "no splits". Prices without that answer are the setup for a
        # tenfold share count, so the whole resolution degrades rather than
        # handing over closes nothing can be paired with (external review,
        # finding 2). Visible as a gap and, downstream, as an unpriced book with
        # a `price_request` — not as a quietly split-blind review.
        return _unavailable(request, [_gap(
            "response_shape",
            "the response carries closes but no Stock Splits field, so no share basis can be "
            "established for them")])

    bundle = _build_yahoo_bundle(request, closes, actions, errors)
    if bundle.usable:
        _cache_store(bundle, root=root, today=today)
    return bundle


def _build_yahoo_bundle(request, closes, actions, errors=None):
    """Assemble the bundle from one already-fetched response.

    Split out from :func:`_from_yahoo` so the contract suite can drive the pure
    normalization — FX direction, split filtering, coverage, gap codes — from a
    recorded response with no provider and no monkeypatching at all.

    ``errors`` is :func:`_download`'s per-symbol failure map. It decides only
    *which* fact an unpriced symbol is — a definitive absence or a request that
    never came back — and never invents an observation.
    """
    try:
        import pandas as pd
    except ImportError:
        return _unavailable(request, [_gap(
            "provider_missing", "pandas is not installed; a price frame cannot be built")])

    errors = errors or {}
    unresolved = sorted(set(errors) & set(request_universe(request)))
    gaps = []
    instrument_symbols = sorted(set(request["instruments"]) | set(request["benchmarks"]))
    frame = _series_frame(pd, {s: closes.get(s) or [] for s in instrument_symbols})
    for symbol in instrument_symbols:
        if not (closes.get(symbol) or []):
            gaps.append(_symbol_failure_gap(symbol, errors[symbol]) if symbol in errors
                        else _gap("symbol_unpriced",
                                  f"{symbol}: the provider returned no usable close"))

    # A split observation is what the source reported inside the window, kept
    # only for instruments: an FX pair carries a zero-filled Stock Splits column
    # and a currency does not split. Fail-closed on parse, because a dropped bad
    # ratio is a confident wrong share count (`splits.normalize_events`).
    observed_splits = {}
    for symbol in instrument_symbols:
        events = [(day, ratio) for day, ratio in (actions.get(symbol) or []) if ratio]
        if not events:
            continue
        try:
            observed_splits[symbol] = split_policy.normalize_events(events, symbol)
        except split_policy.SplitDataError as exc:
            gaps.append(_gap("response_shape", f"{symbol}: unusable split observation ({exc})"))

    fx, fx_frame = _fx_from_closes(pd, request, closes, gaps)
    return MarketDataBundle(source="yahoo", request=request, frame=frame,
                            splits=observed_splits, fx=fx, fx_frame=fx_frame, gaps=gaps,
                            unresolved=unresolved)


def _series_frame(pd, columns):
    """``{symbol: [(date, value), ...]}`` → the frame shape every existing
    consumer already reads (index=DatetimeIndex, columns=symbols)."""
    populated = {name: dict(pairs) for name, pairs in columns.items() if pairs}
    if not populated:
        return None
    index = sorted({day for series in populated.values() for day in series})
    return pd.DataFrame(
        {name: [series.get(day) for day in index] for name, series in sorted(populated.items())},
        index=pd.DatetimeIndex([dt.datetime(d.year, d.month, d.day) for d in index]))


def _fx_from_closes(pd, request, closes, gaps):
    """Spot rate and daily series for every requested currency, from the same
    response the prices came from.

    ``{CUR}=X`` quotes one USD in CUR, so every value is reciprocated into
    ``usd_per_unit`` — the direction the whole engine aggregates in, and the one
    place it is decided. Verified equal to the retired per-currency
    ``Ticker("{CUR}=X").history(period="5d")`` path to the last digit, so this is
    a substitution rather than a re-approximation. The spot rate is the last
    *valid* observation, not the last row: a stale weekend or a provider gap on
    the final day must not read as a missing rate.
    """
    fx = {"USD": 1.0}
    series_columns = {}
    for code in request["currencies"]:
        pairs = closes.get(fx_symbol(code)) or []
        usable = [(day, value) for day, value in pairs if value > 0]
        if not usable:
            gaps.append(_gap("fx_unavailable",
                             f"{code}: the provider returned no usable rate for "
                             f"{fx_symbol(code)}"))
            continue
        fx[code] = round(1.0 / usable[-1][1], 6)
        series_columns[code] = [(day, 1.0 / value) for day, value in usable]
    return fx, _series_frame(pd, series_columns)


# ─────────────────────────── cache and memo ───────────────────────────

def _cache_load(request, *, root, today=None):
    """The newest same-day entry that covers ``request``, or ``None``.

    Coverage, not equality: #605's §D exists so a same-day ``prepare`` bundle
    can answer a later ``consider`` without a request. ``fetch_cache`` already
    discards the whole file on a new date, so a stale day cannot be reached from
    here and no separate expiry is needed.
    """
    entries = fetch_cache._read_day(CACHE_KIND, root, today)  # noqa: SLF001
    if not entries:
        return None
    for blob in entries.values():
        bundle = MarketDataBundle.from_json(blob)
        if bundle is not None and bundle.usable and bundle.covers(request):
            return bundle
    return None


def _cache_store(bundle, *, root, today=None):
    """Freeze one usable bundle for the rest of the day. Never raises."""
    return fetch_cache.store(CACHE_KIND, bundle.request, bundle.to_json(),
                             root=root, today=today)


def reset_memo():
    """Drop the in-process memo. For tests, and for a long-lived caller that
    genuinely wants a second observation instant."""
    _MEMO.clear()


# ────────────────────────────── the entry ──────────────────────────────

def resolve(request, *, root, feed=None, today=None, env=None, memo=True):
    """Resolve one request into one bundle. The only supported entry point.

    Precedence is fixed: a supplied envelope answers for everything it declares
    and no Yahoo request is made at all, because the envelope exists for a host
    that cannot make one. Otherwise the in-process memo, then the same-day disk
    cache, then one provider pass.

    ``root`` is required, and required *here* rather than defaulted deeper down.
    The disk cache is state: its keys are the user's own tickers, `coach.py`
    registers it so `data reset` can delete it, and an isolated run's cache
    belongs to that run. When it defaulted to ``session.default_root()``, the one
    call site that omitted it — ``trade_recap``, reached by ``prepare`` — wrote
    the run's tickers into the account's real coach directory and could be
    answered from a different root's closes, while its session state went where
    it was told (#627). A caller that genuinely wants the account root names
    ``session.default_root()``, so the choice appears at the call site instead of
    being inherited by whoever forgets.

    Never raises on a provider problem — the bundle's ``gaps`` and ``coverage``
    carry it, and the caller's own established refusal decides whether the
    review can continue. It does raise :class:`MarketDataError` for a request
    that cannot be trusted as stated, which is a programming error at the call
    site rather than a runtime condition.
    """
    request = build_request(**request) if not _is_normalized(request) else request
    if feed is not None:
        return _from_feed(request, feed)
    # The posture is checked before the memo, not only inside the adapter. A
    # process that resolved online and then went offline would otherwise be
    # answered from its own warm memo — an online bundle returned under
    # TR_OFFLINE, which is precisely the equivalence this flag exists to
    # guarantee (external review, finding 6).
    if not network_allowed(env):
        # Checked before the frozen posture, not after. A process that may not
        # reach a provider has nothing to freeze, and routing it here keeps an
        # offline run's degradation byte-identical whether or not the caller
        # asked for a frozen frame — the gap code a review reports must describe
        # the machine it ran on, not which subcommand invoked it.
        return _from_yahoo(request, root=root, today=today, env=env)
    if frame_frozen(env):
        return _from_frozen_frame(request, root=root, today=today)
    if memo:
        for bundle in _MEMO.values():
            if bundle.covers(request):
                return bundle
    bundle = _from_yahoo(request, root=root, today=today, env=env)
    if memo and bundle.usable:
        _MEMO[_request_key(bundle.request)] = bundle
    return bundle


def _is_normalized(request):
    return (isinstance(request, dict)
            and set(request) == {"instruments", "benchmarks", "currencies",
                                 "window_start", "window_end", "rebase_origin"})


# ─────────────────── the envelope every other route already reads ───────────────────

def to_price_feed_envelope(bundle, currency_by_ticker=None, instruments_only=True):
    """One bundle as the existing supplied ``price-feed`` envelope (#605 §F).

    This is deliberately the *only* way a resolved bundle reaches ``consider``.
    The ``--prices`` lane is already the most thoroughly tested path into that
    command — currency conflicts, split-basis reconciliation, the valuation
    manifest, the frozen provenance — so pouring resolved facts into the same
    envelope means live resolution adds no second downstream path to get wrong,
    and #605's "a supplied run produces the same normalized valuation facts as an
    equivalent Yahoo fixture" holds by construction rather than by comparison.

    Two decisions about what goes in, both about honesty rather than convenience.

    **The latest close per instrument, dated its own observation day — no
    restated series.** A provider close fetched with ``auto_adjust=True`` is
    already retro-adjusted onto today's split basis, while the envelope's
    contract is that a close is a *raw* observation which ``price_feed.parse``
    then rebases from its own date. Emitting an adjusted daily series while
    declaring no splits would be a false statement that happens to compute
    correctly; emitting one current observation is simply true, because there are
    no splits after today to rebase across. Callers that need a series have the
    bundle's own frame.

    **The split observations travel with the quotes.** They are what makes the
    two operands comparable through the existing machinery: ``splits_map`` turns
    them into the per-ticker override ``review._effective_splits`` already
    prefers over the frozen map — right for the same reason a supplied
    envelope's own splits are, they arrived with these quotes, on one basis, at
    one instant — and ``basis_conflicts`` compares a factor of 1.0 on both sides
    for a current observation, so a resolved run cannot manufacture the refusal
    that a genuinely mismatched supplied envelope earns.

    ``currency_by_ticker`` is required for any ticker that is not USD:
    ``price_feed.currency_conflicts`` compares this against the trades, and
    guessing here would either invent a conflict or hide one.

    **Returns ``None`` when no instrument row survived** — nothing the bundle
    priced was both inside this envelope's scope (a benchmark is outside it
    unless ``instruments_only`` is false, so a bundle whose only successes are
    benchmarks yields no envelope) and reconcilable onto one split basis by the
    rule above. Stated as one condition rather than a list of causes on purpose:
    the claim this paragraph replaces went stale by enumerating. That is this
    lane's own rule and not a restatement of what ``price_feed.parse`` will
    accept: this envelope is defined over instrument closes and dates itself
    from them, since ``as_of`` is ``max(row date)`` read off the rows themselves,
    so with no rows there is no observed session to declare one from. Nothing is
    concealed by returning ``None`` — a dropped instrument is already recorded as
    a gap on the bundle before this point, so the coverage fact outlives the
    envelope, and the caller degrades to the pre-#605 unpriced answer rather than
    raising into a review.

    One consequence worth naming, because it is not obvious from the return
    value: a currency rate this bundle *did* resolve is discarded along with it,
    as ``fx`` is assembled below this point. A book whose held tickers all fail
    to price while its currency pair resolves therefore reaches ``consider``
    rateless, even though the bundle counts as ``usable`` on the strength of that
    very rate. Whether such a rate should be able to travel on its own is a
    question about this lane, open at the time of writing; it is not answered by
    what ``parse`` does or does not accept on the other side.
    """
    currency_by_ticker = currency_by_ticker or {}
    skipped = []
    wanted = set(bundle.request["instruments"])
    if not instruments_only:
        wanted |= set(bundle.request["benchmarks"])
    rows = []
    for ticker in sorted(set(bundle.priced) & wanted):
        series = bundle.frame[ticker].dropna()
        if not len(series):
            continue
        observed = series.index[-1].date().isoformat()
        row = {"ticker": ticker,
               "close": round(float(series.iloc[-1]), 6),
               "date": observed,
               "currency": str(currency_by_ticker.get(ticker, "USD")).upper()}
        # `price_feed.parse` divides a declared close by `factor_after(splits, its
        # own date)`, and a provider close is *already* retro-adjusted onto today's
        # basis. So a split newer than the close must not be declared — that would
        # divide an adjusted number a second time.
        #
        # But dropping it is not enough, and an earlier version of this function
        # stopped there on the wrong premise that such a close is "pre-split and
        # therefore comparable to a pre-split share count" (external review,
        # finding 3). Under `auto_adjust=True` it is the opposite: a stale close
        # from before a later split is *already* stated in the post-split basis,
        # while a share count with no split map is not. Pairing them understates
        # the position by the split factor — 100 old shares at an adjusted $18 read
        # as $1,800 instead of $18,000 — and a reverse split overstates it.
        #
        # There is no honest way to express that instrument in this envelope: the
        # contract is that a close is raw as of its own date, and this one is not.
        # So it is left out, with a gap, and reads as unpriced. Unpriced is a
        # fact the disclosure layer already carries; a tenfold value is not.
        later = [(day, ratio) for day, ratio in (bundle.splits.get(ticker) or ())
                 if day.isoformat() > observed]
        if later:
            skipped.append(
                f"{ticker}: last close {observed} predates a split dated "
                f"{later[0][0].isoformat()}, so the retro-adjusted quote and this book's share "
                "count cannot be put on one basis")
            continue
        events = [(day, ratio) for day, ratio in (bundle.splits.get(ticker) or ())
                  if day.isoformat() <= observed]
        if events:
            row["splits"] = [[day.isoformat(), ratio] for day, ratio in events]
        rows.append(row)
    for detail in skipped:
        # Recorded on the bundle, because an instrument dropped here is a
        # coverage fact its caller has to be able to see and disclose.
        gap = _gap("symbol_unpriced", detail)
        if gap not in bundle.gaps:
            bundle.gaps.append(gap)
    if skipped:
        bundle.gaps.sort(key=lambda row: (row["code"], row.get("detail") or ""))
    if not rows:
        return None
    # `parse` refuses any observation dated after `as_of`, so it must be the
    # newest date actually present rather than the bundle's own frame-level one:
    # a frame whose last row belongs to one late-closing market would otherwise
    # sit ahead of every instrument in it, or behind, depending on the mix.
    as_of = max(row["date"] for row in rows)
    fx_rows = [{"currency": code, "usd_per_unit": rate, "date": as_of}
               for code, rate in sorted(bundle.fx.items())
               if code != "USD" and code in set(bundle.request["currencies"])]
    return {"as_of": as_of, "source": f"{bundle.source} (engine resolver)",
            "prices": rows, "fx": fx_rows}
