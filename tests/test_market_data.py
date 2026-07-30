#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""market_data provider contract (#605) — fully offline, deterministic, no pytest.

The provider is replaced by a recorded response, so every assertion below is
about *this repository's* normalization rather than about Yahoo's availability.
The one thing that needs a live provider — that the recorded shape is still the
shape Yahoo returns — is `test_network_response_shape_is_still_what_we_recorded`,
which runs only under `TR_TEST_NETWORK=1` and is a shape witness, never the
semantic oracle.

What this covers, in the order the facts travel:

  A. request normalization, and the one refusal: a window that cannot cover its
     own rebase origin.
  B. one request per distinct symbol, never twice — the call-count contract,
     measured with a counter rather than asserted in prose.
  C. what comes out of one response: closes, split observations, FX direction,
     FX series, per-symbol coverage.
  D. failure shapes: offline, provider missing, transport failure, empty
     response, unrecognized shape, unpriced symbol, unusable rate. Every one a
     stable gap code, and none of them a number.
  E. reuse: a superset bundle answers a subset request for free, and each of the
     four coverage axes refuses independently — including the rebase origin,
     which a symbols-and-day-only check would silently drop.
  F. the supplied envelope: authoritative, zero provider calls.
  G. the cache: same-day coverage reuse, a new day is a miss, a partial bundle is
     never frozen, and an unreadable entry is a miss rather than a crash.
"""
import datetime as dt
import json
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ENGINE = os.path.join(REPO, "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE)
import fetch_cache  # noqa: E402
import market_data  # noqa: E402
import price_feed  # noqa: E402

DAY = "2026-07-30"
NEXT = "2026-07-31"
ONLINE = {"TR_OFFLINE": "0"}


def _d(iso):
    return dt.date.fromisoformat(iso)


# A recorded response, in the shape `market_data._field` produces from a real
# `yf.download(..., actions=True)`: {symbol: [(date, value), ...]}. NVDA carries
# a real split; DEAD is present-but-empty, exactly how a delisted or misspelled
# symbol comes back.
RECORDED_CLOSES = {
    "NVDA": [(_d("2026-07-28"), 180.0), (_d("2026-07-29"), 190.0)],
    "AAPL": [(_d("2026-07-28"), 330.0), (_d("2026-07-29"), 338.19)],
    "SPY": [(_d("2026-07-28"), 725.0), (_d("2026-07-29"), 729.46)],
    "TSLA": [(_d("2026-07-28"), 300.0), (_d("2026-07-29"), 305.0)],
    "DEAD": [],
    # {CUR}=X quotes one USD in CUR: 32.3638 TWD per USD.
    "TWD=X": [(_d("2026-07-28"), 32.0), (_d("2026-07-29"), 32.3638)],
    "EUR=X": [(_d("2026-07-28"), 0.87), (_d("2026-07-29"), 0.872)],
    "JPY=X": [(_d("2026-07-29"), 155.0)],
}
RECORDED_SPLITS = {"NVDA": [(_d("2026-07-29"), 10.0)]}


class FakeProvider:
    """Stands in for `market_data._download`, counting calls and universes."""

    def __init__(self, closes=None, actions=None, raises=None, empty=False,
                 flat_columns=False):
        self.closes = RECORDED_CLOSES if closes is None else closes
        self.actions = RECORDED_SPLITS if actions is None else actions
        self.raises = raises
        self.empty = empty
        self.flat_columns = flat_columns
        self.calls = []

    def __call__(self, symbols, start, end=None):
        self.calls.append({"symbols": list(symbols), "start": start, "end": end})
        if self.raises is not None:
            raise self.raises
        import pandas as pd
        if self.empty:
            return pd.DataFrame()
        index = sorted({day for pairs in self.closes.values() for day, _ in pairs})
        if not index:
            return pd.DataFrame()
        idx = pd.DatetimeIndex([dt.datetime(d.year, d.month, d.day) for d in index])
        if self.flat_columns:                     # a shape this engine must refuse to guess at
            return pd.DataFrame({"Close": [1.0] * len(index)}, index=idx)
        data = {}
        for field, source in (("Close", self.closes), ("Stock Splits", self.actions)):
            for symbol in symbols:
                series = dict(source.get(symbol) or [])
                data[(field, symbol)] = [series.get(day, float("nan")) for day in index]
        frame = pd.DataFrame(data, index=idx)
        frame.columns = pd.MultiIndex.from_tuples(frame.columns)
        return frame


class provider:
    """Install a fake provider for a block, memo cleared, with an isolated root.

    The root is not a convenience. ``fetch_cache`` defaults to
    ``session.default_root()``, so a ``resolve`` in a test that omits one reads
    *and writes* the account's own ``~/.trade-coach`` — which pollutes a real
    user root and silently cross-contaminates the next test with a cached bundle
    it never asked for (observed while writing this suite). Every resolve below
    therefore goes through :meth:`resolve`, which always supplies this root and a
    fixed ``today``.
    """

    def __init__(self, fake=None):
        self.fake = fake if fake is not None else FakeProvider()

    def __enter__(self):
        self._real = market_data._download
        market_data._download = self.fake
        market_data.reset_memo()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        return self

    def __exit__(self, *exc):
        market_data._download = self._real
        market_data.reset_memo()
        self._tmp.cleanup()
        return False

    def resolve(self, request, *, feed=None, env=None, root=None, today=DAY):
        return market_data.resolve(request, feed=feed, root=root or self.root,
                                   today=today, env=env or ONLINE)

    def day_entries(self, today=DAY):
        return fetch_cache._read_day(market_data.CACHE_KIND, self.root, today)  # noqa: SLF001

    @property
    def calls(self):
        return self.fake.calls


def _request(**over):
    base = {"instruments": ["NVDA", "AAPL"], "benchmarks": ["SPY"],
            "currencies": ["TWD"], "window_start": "2026-07-01",
            "window_end": None, "rebase_origin": "2026-07-01"}
    base.update(over)
    return market_data.build_request(**base)


# ───────────────────────── A. request normalization ─────────────────────────

def test_a_request_is_deterministic_regardless_of_how_it_was_spelled():
    one = market_data.build_request(instruments=["nvda ", "AAPL", "AAPL"],
                                    benchmarks=["SPY"], currencies=["twd", "USD", "twd"],
                                    window_start=dt.date(2026, 7, 1))
    two = market_data.build_request(instruments=["AAPL", "nvda "],
                                    benchmarks=["SPY"], currencies=["TWD"],
                                    window_start="2026-07-01")
    assert one == two, ("two callers naming one universe must produce one request, or the "
                        f"cache and memo hold two entries for it: {one} vs {two}")
    assert one["currencies"] == ["TWD"], \
        f"USD is the numeraire and must never be requested from a provider: {one['currencies']}"
    assert one["rebase_origin"] == "2026-07-01", \
        "an unstated rebase origin must default to the window start, not to None"


def test_a_window_that_cannot_cover_its_own_rebase_origin_is_refused():
    try:
        market_data.build_request(instruments=["NVDA"], window_start="2026-01-01",
                                  rebase_origin="2025-01-01")
    except market_data.MarketDataError as exc:
        assert "rebase_origin" in str(exc) and "2025-01-01" in str(exc), \
            f"the refusal must name the origin it cannot cover: {exc}"
    else:
        raise AssertionError(
            "a rebase origin older than the window start must be refused: past it the split "
            "observations silently omit an event a consumer will look for, and a missing "
            "split reads as a factor of 1.0 with nothing anywhere looking wrong")


def test_a_request_naming_nothing_is_refused():
    for kwargs in ({"window_start": "2026-07-01"},
                   {"instruments": [], "currencies": ["USD"], "window_start": "2026-07-01"}):
        try:
            market_data.build_request(**kwargs)
        except market_data.MarketDataError:
            continue
        raise AssertionError(f"an empty universe must be refused, not fetched: {kwargs}")


def test_a_malformed_date_or_symbol_is_refused_rather_than_coerced():
    for kwargs in ({"instruments": ["NVDA"], "window_start": "not-a-date"},
                   {"instruments": ["NVDA", "  "], "window_start": "2026-07-01"},
                   {"instruments": ["NVDA"], "window_start": "2026-07-01",
                    "window_end": "2026-06-01"}):
        try:
            market_data.build_request(**kwargs)
        except market_data.MarketDataError:
            continue
        raise AssertionError(f"must be refused: {kwargs}")


# ─────────────────── B. one request per symbol, never twice ───────────────────

def test_b_one_pass_issues_exactly_one_request_for_the_deduplicated_universe():
    with provider() as p:
        bundle = p.resolve(_request(instruments=["NVDA", "AAPL", "SPY"], benchmarks=["SPY"],
                                    currencies=["TWD", "EUR"]))
        assert len(p.calls) == 1, \
            f"one resolver pass must make one provider call, made {len(p.calls)}"
        asked = p.calls[0]["symbols"]
    assert asked == sorted(set(asked)), f"a symbol must not be requested twice: {asked}"
    assert set(asked) == {"NVDA", "AAPL", "SPY", "TWD=X", "EUR=X"}, (
        "instruments, benchmarks and FX pairs must travel in one universe so closes, splits "
        f"and rates come from one instant: {asked}")
    assert bundle.source == "yahoo"


def test_b_a_second_resolve_for_a_subset_costs_no_request_at_all():
    with provider() as p:
        p.resolve(_request(instruments=["NVDA", "AAPL"], benchmarks=["SPY"],
                           currencies=["TWD", "EUR"]))
        assert len(p.calls) == 1
        # The shape a compatibility wrapper produces: same day, narrower universe.
        subset = p.resolve(_request(instruments=["NVDA"], benchmarks=[], currencies=["TWD"]))
        assert len(p.calls) == 1, (
            "a subset of an already-resolved universe must not fetch again; that is what makes "
            f"'never request a symbol twice' true across wrappers ({len(p.calls)} calls)")
        assert subset.fx["TWD"] > 0


def test_b_a_widened_universe_does_fetch_again():
    with provider() as p:
        p.resolve(_request(instruments=["NVDA"], benchmarks=[], currencies=[]))
        p.resolve(_request(instruments=["NVDA", "TSLA"], benchmarks=[], currencies=[]))
        assert len(p.calls) == 2, (
            "a symbol nobody has fetched yet must be fetched; a memo that answered anyway would "
            f"invent coverage ({len(p.calls)} calls)")


def test_b_the_memo_holds_even_when_no_state_root_is_reachable():
    """`fetch_cache` refuses to create a state root, so a bare engine run has no
    disk cache at all — and "never request a symbol twice" has to hold there too,
    or the guarantee is one an ordinary offline caller cannot observe."""
    with provider() as p:
        absent = os.path.join(p.root, "does-not-exist")
        p.resolve(_request(), root=absent)
        p.resolve(_request(instruments=["NVDA"], benchmarks=[], currencies=[]), root=absent)
        assert len(p.calls) == 1, f"the in-process memo must cover a rootless run ({len(p.calls)})"
        assert not os.path.exists(absent), "resolution must never create a state root"


# ──────────────────── C. what comes out of one response ────────────────────

def test_c_closes_splits_and_fx_all_come_from_the_one_response():
    with provider() as p:
        bundle = p.resolve(_request(currencies=["TWD", "EUR"]))
    assert bundle.as_of == "2026-07-29", f"as_of must be the newest observation: {bundle.as_of}"
    assert float(bundle.frame["NVDA"].dropna().iloc[-1]) == 190.0
    assert float(bundle.frame["SPY"].dropna().iloc[-1]) == 729.46
    assert bundle.splits == {"NVDA": [(_d("2026-07-29"), 10.0)]}, (
        "a split the response reported must arrive as an observation, and an empty column must "
        f"not become one: {bundle.splits}")
    # {CUR}=X quotes one USD in CUR, so the engine's usd_per_unit is its reciprocal.
    assert bundle.fx["TWD"] == round(1.0 / 32.3638, 6), \
        f"FX direction must be usd_per_unit: {bundle.fx}"
    assert bundle.fx["EUR"] == round(1.0 / 0.872, 6)
    assert bundle.fx["USD"] == 1.0, "USD must stay the arithmetic identity"
    assert abs(float(bundle.fx_frame["TWD"].iloc[-1]) - 1.0 / 32.3638) < 1e-12, \
        "the daily series must be reciprocated the same way the spot rate is"


def test_c_an_fx_pair_never_becomes_an_instrument_in_the_price_frame():
    with provider() as p:
        bundle = p.resolve(_request(currencies=["TWD"]))
    assert "TWD=X" not in list(bundle.frame.columns), (
        "a currency pair is a rate, not a holding; leaking it into the price frame would let "
        f"it be valued and weighted like a position: {list(bundle.frame.columns)}")
    assert "TWD=X" not in bundle.splits


def test_c_the_spot_rate_is_the_last_valid_observation_not_the_last_row():
    closes = dict(RECORDED_CLOSES, **{"TWD=X": [(_d("2026-07-28"), 32.0)]})
    with provider(FakeProvider(closes=closes)) as p:
        bundle = p.resolve(_request(currencies=["TWD"]))
    assert bundle.fx["TWD"] == round(1.0 / 32.0, 6), (
        "a provider gap on the last day must not read as a missing rate — that is a refusal "
        f"the user would see for a rate that is available: {bundle.fx}")
    assert not [g for g in bundle.gaps if g["code"] == "fx_unavailable"]


def test_c_coverage_states_what_was_asked_priced_and_missing():
    with provider() as p:
        bundle = p.resolve(_request(instruments=["NVDA", "DEAD"], benchmarks=["SPY"],
                                    currencies=[]))
    coverage = bundle.coverage()
    assert coverage["requested"] == ["DEAD", "NVDA", "SPY"]
    assert coverage["missing"] == ["DEAD"], (
        "a symbol present in the response but carrying no usable close is missing, not priced: "
        f"{coverage}")
    assert coverage["priced"] == ["NVDA", "SPY"]
    assert [g for g in bundle.gaps if g["code"] == "symbol_unpriced"], \
        "an unpriced symbol must also surface as a stable gap code, not only as a list"


# ─────────────────────────── D. failure shapes ───────────────────────────

def test_d_offline_degrades_exactly_as_a_missing_provider_does():
    with provider() as p:
        bundle = p.resolve(_request(), env={"TR_OFFLINE": "1"})
        assert not p.calls, (
            "TR_OFFLINE must be read before anything is requested; ninety-odd consider tests "
            "and the pinned evaluation_id digest depend on this being airtight")
    assert bundle.source == "unavailable"
    assert bundle.frame is None and bundle.splits == {} and bundle.fx == {"USD": 1.0}, (
        "an offline bundle must degrade to exactly today's no-network answer, or the existing "
        f"suite stops being an oracle for it: {bundle.to_json()}")
    assert [g["code"] for g in bundle.gaps] == ["network_disabled"]
    assert not bundle.usable


def test_d_every_provider_failure_is_a_stable_code_and_never_a_number():
    cases = [
        (FakeProvider(raises=RuntimeError("DNS boom")), "transport_failed"),
        (FakeProvider(empty=True), "empty_response"),
        (FakeProvider(flat_columns=True), "response_shape"),
    ]
    for fake, expected in cases:
        with provider(fake) as p:
            bundle = p.resolve(_request())
        assert [g["code"] for g in bundle.gaps] == [expected], \
            f"expected gap {expected}, got {bundle.gaps}"
        assert bundle.frame is None, f"{expected} must not produce a frame"
        assert bundle.fx == {"USD": 1.0}, (
            f"{expected} must not invent a rate: a missing rate resolved as 1.0 inverts which "
            f"holding is largest, the failure AGENTS.md boundary 6 forbids ({bundle.fx})")
        assert not bundle.usable, f"{expected} must never be cached as a successful day"


def test_d_an_unusable_rate_is_a_gap_while_the_prices_still_resolve():
    with provider(FakeProvider(closes=dict(RECORDED_CLOSES, **{"TWD=X": []}))) as p:
        bundle = p.resolve(_request(currencies=["TWD"]))
    assert "TWD" not in bundle.fx, \
        f"an unavailable rate must be absent, never 1.0: {bundle.fx}"
    assert [g["code"] for g in bundle.gaps] == ["fx_unavailable"]
    assert bundle.priced, "one missing rate must not discard the closes that did resolve"


def test_d_a_malformed_split_observation_is_reported_not_dropped():
    # A negative ratio survives the field read (it is a real number) and must
    # then be refused by splits.normalize_events rather than quietly applied.
    with provider(FakeProvider(actions={"NVDA": [(_d("2026-07-29"), -2.0)]})) as p:
        bundle = p.resolve(_request(currencies=[]))
    assert "NVDA" not in bundle.splits
    assert "response_shape" in [g["code"] for g in bundle.gaps], (
        "a ratio that cannot be trusted must be said out loud: silently dropping it produces a "
        f"confident wrong share count, which is splits.py's whole reason to fail closed: "
        f"{bundle.gaps}")
    assert bundle.priced, "one bad split observation must not discard every close in the response"


def test_d_gap_codes_are_declared_before_they_are_emitted():
    try:
        market_data._gap("invented_code", "x")
    except market_data.MarketDataError:
        pass
    else:
        raise AssertionError(
            "an undeclared gap code must be refused: GAP_CODES is what receipts and callers "
            "branch on, so a code that only exists at one emit site is one nobody can read")


# ──────────────────────────── E. coverage reuse ────────────────────────────

def test_e_each_coverage_axis_refuses_independently():
    with provider() as p:
        held = p.resolve(_request(instruments=["NVDA", "AAPL"], benchmarks=["SPY"],
                                  currencies=["TWD"], window_start="2026-01-01",
                                  rebase_origin="2026-02-01"))
    axes = {
        "a symbol nobody fetched": _request(instruments=["NVDA", "TSLA"], benchmarks=[],
                                            currencies=[], window_start="2026-01-01",
                                            rebase_origin="2026-02-01"),
        "a currency nobody fetched": _request(instruments=["NVDA"], benchmarks=[],
                                              currencies=["JPY"], window_start="2026-01-01",
                                              rebase_origin="2026-02-01"),
        "an earlier window": _request(instruments=["NVDA"], benchmarks=[], currencies=[],
                                      window_start="2025-01-01", rebase_origin="2026-02-01"),
    }
    for label, wider in axes.items():
        assert not held.covers(wider), (
            f"{label} must not be answered from a bundle that does not cover it")
    assert held.covers(_request(instruments=["NVDA"], benchmarks=[], currencies=["TWD"],
                                window_start="2026-03-01", rebase_origin="2026-03-01")), \
        "a genuine subset must be reusable, which is the whole point of the bundle cache"


def test_e_a_benchmark_already_fetched_covers_it_as_an_instrument():
    """A user who holds SPY must not cause a second request for it. The two
    groups are a statement about *why* a symbol is wanted, never about which
    request it belongs to."""
    with provider() as p:
        held = p.resolve(_request(instruments=["NVDA"], benchmarks=["SPY"], currencies=[]))
        assert held.covers(_request(instruments=["NVDA", "SPY"], benchmarks=[], currencies=[]))
        p.resolve(_request(instruments=["NVDA", "SPY"], benchmarks=[], currencies=[]))
        assert len(p.calls) == 1, f"a benchmark is already in the universe ({len(p.calls)} calls)"


def test_e_a_request_needing_older_split_history_re_resolves_rather_than_reusing():
    """Same ticker, same day, different split history needed.

    The axis with teeth, and the reason the window is what `covers` compares.
    `prepare` may resolve from a CSV's first trade while `consider` reads the same
    ticker against an older ledger anchor: reuse the shallow bundle and the map is
    complete for one route and silently short a split for the other, which is a
    tenfold share count under a valid `state_version`.

    Split completeness rides the window rather than a separate declared-origin
    comparison, because `build_request` already refuses an origin that precedes
    its own window start (`test_a_window_that_cannot_cover_its_own_rebase_origin`).
    Those two together are the whole guarantee; a bundle that had to compare
    declared origins as well would refuse requests whose data it demonstrably
    holds.
    """
    with provider() as p:
        shallow = p.resolve(_request(instruments=["NVDA"], benchmarks=[], currencies=[],
                                     window_start="2026-06-01", rebase_origin="2026-06-01"))
        deep = _request(instruments=["NVDA"], benchmarks=[], currencies=[],
                        window_start="2020-01-01", rebase_origin="2020-06-01")
        assert not shallow.covers(deep), (
            "a bundle whose window opens after the origin a caller will rebase from cannot "
            "answer it: the splits in between are simply not in the response")
        p.resolve(deep)
        assert len(p.calls) == 2, (
            "the deeper request must actually re-resolve rather than reuse a window that cannot "
            f"contain its splits ({len(p.calls)} calls)")
        assert p.calls[1]["start"] == "2020-01-01", \
            f"the re-resolve must widen the window it asks the provider for: {p.calls[1]}"
        # And the converse: a deep bundle serves a shallow request for free, which
        # is what makes one prepare pass answer the rest of the day.
        market_data.reset_memo()
        deep_bundle = p.resolve(deep)
        assert deep_bundle.covers(_request(instruments=["NVDA"], benchmarks=[], currencies=[],
                                          window_start="2026-06-01",
                                          rebase_origin="2026-06-01"))


def test_e_an_open_ended_request_is_not_served_from_a_closed_window():
    with provider() as p:
        closed = p.resolve(_request(window_end="2026-07-20"))
    assert not closed.covers(_request(window_end=None)), (
        "a bundle that stopped at a fixed date cannot answer 'the current price': that is how a "
        "stale close becomes today's weight")


# ────────────────────────── F. supplied envelope ──────────────────────────

def _envelope(path, closes, fx=None, splits=None, as_of="2026-07-29"):
    prices = []
    for ticker, close in closes.items():
        row = {"ticker": ticker, "close": close, "date": as_of, "currency": "USD"}
        if splits and ticker in splits:
            row["splits"] = splits[ticker]
        prices.append(row)
    payload = {"as_of": as_of, "source": "broker", "prices": prices}
    if fx:
        payload["fx"] = [{"currency": c, "usd_per_unit": r, "date": as_of}
                         for c, r in fx.items()]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return price_feed.load(path)


def test_f_a_supplied_envelope_is_authoritative_and_makes_no_provider_call():
    with provider() as p:
        feed = _envelope(os.path.join(p.root, "px.json"), {"NVDA": 190.0, "SPY": 729.46},
                         fx={"TWD": 0.0317})
        bundle = p.resolve(_request(instruments=["NVDA"], benchmarks=["SPY"],
                                    currencies=["TWD"]), feed=feed)
        assert not p.calls, (
            "an envelope exists because this host cannot reach the provider; a hidden top-up "
            "fetch would either fail slowly or mix two sources into one aggregate")
    assert bundle.source == "supplied"
    assert bundle.fx["TWD"] == 0.0317, f"the envelope's own rate must win: {bundle.fx}"
    assert float(bundle.frame["NVDA"].dropna().iloc[-1]) == 190.0


def test_f_an_envelope_that_covers_less_than_was_asked_says_so():
    with provider() as p:
        feed = _envelope(os.path.join(p.root, "px.json"), {"NVDA": 190.0})
        bundle = p.resolve(_request(instruments=["NVDA", "AAPL"], benchmarks=[],
                                    currencies=["TWD"]), feed=feed)
        assert not p.calls
    codes = {g["code"] for g in bundle.gaps}
    assert "symbol_unpriced" in codes and "fx_unavailable" in codes, (
        f"an incomplete envelope must state both halves of what it could not answer: {bundle.gaps}")
    assert "TWD" not in bundle.fx
    assert bundle.coverage()["missing"] == ["AAPL"]


def test_f_a_supplied_split_reaches_the_bundle_as_an_observation():
    with provider() as p:
        feed = _envelope(os.path.join(p.root, "px.json"), {"NVDA": 190.0},
                         splits={"NVDA": [["2026-07-29", 10.0]]})
        bundle = p.resolve(_request(instruments=["NVDA"], benchmarks=[], currencies=[]),
                           feed=feed)
    assert bundle.splits == {"NVDA": [(_d("2026-07-29"), 10.0)]}, (
        "both adapters must land on one split-observation shape, or the two routes apply "
        f"different rules to the same fact: {bundle.splits}")


def test_f_a_supplied_envelope_is_never_served_from_a_yahoo_cache_entry():
    """Precedence is not "whatever was resolved first". A run that supplies an
    envelope must read the envelope, even when a compatible Yahoo bundle for the
    same universe is sitting in the same-day cache."""
    with provider() as p:
        p.resolve(_request(instruments=["NVDA"], benchmarks=[], currencies=[]))
        feed = _envelope(os.path.join(p.root, "px.json"), {"NVDA": 111.0})
        bundle = p.resolve(_request(instruments=["NVDA"], benchmarks=[], currencies=[]),
                           feed=feed)
    assert bundle.source == "supplied" and float(bundle.frame["NVDA"].dropna().iloc[-1]) == 111.0, (
        "the supplied envelope must win over a cached provider bundle for the facts it declares: "
        f"got {bundle.source}")


# ──────────────────────────── G. the disk cache ────────────────────────────

def test_g_a_same_day_bundle_answers_a_later_subset_with_no_provider_call():
    with provider() as p:
        p.resolve(_request(instruments=["NVDA", "AAPL"], benchmarks=["SPY"], currencies=["TWD"]))
        assert len(p.calls) == 1
        market_data.reset_memo()                  # a *later process*, not the same one
        again = p.resolve(_request(instruments=["NVDA"], benchmarks=[], currencies=["TWD"]))
        assert len(p.calls) == 1, (
            "#605's own acceptance criterion: a compatible same-day prepare bundle must satisfy "
            f"a later consider with zero provider calls ({len(p.calls)})")
        assert again.fx["TWD"] == round(1.0 / 32.3638, 6)
        assert float(again.frame["NVDA"].dropna().iloc[-1]) == 190.0


def test_g_a_new_day_is_a_miss():
    with provider() as p:
        p.resolve(_request(), today=DAY)
        market_data.reset_memo()
        p.resolve(_request(), today=NEXT)
        assert len(p.calls) == 2, \
            f"yesterday's closes must never be served as today's ({len(p.calls)} calls)"


def test_g_a_transport_failure_is_never_frozen_as_a_day():
    with provider(FakeProvider(raises=RuntimeError("rate limited"))) as p:
        p.resolve(_request())
        assert not p.day_entries(), (
            "a transport failure cached as a day turns one network blip into the whole day's "
            "conclusion — #235's rule, and it has to survive the bundle rewrite")
    with provider() as p:
        p.resolve(_request())
        assert p.day_entries(), "a usable bundle must still be cached"


def test_g_a_response_that_answered_nothing_is_never_frozen_as_a_day():
    """The other half, and the one an exception-only test cannot reach: the
    provider *answered*, with rows, and none of them covers anything that was
    asked for. Caching that freezes "nothing is available" for the rest of the
    day over what is usually a transient upstream gap."""
    with provider() as p:
        bundle = p.resolve(_request(instruments=["DEAD"], benchmarks=[], currencies=[]))
        assert not bundle.usable, f"a bundle with nothing priced is not usable: {bundle.to_json()}"
        assert not p.day_entries(), (
            "a resolution that answered nothing must be retried, not frozen: unlike a partial "
            "bundle (real coverage plus stated gaps, which is legitimately cacheable) there is "
            "nothing here worth reusing")
        # …and the retry really does reach the provider again.
        market_data.reset_memo()
        p.resolve(_request(instruments=["DEAD"], benchmarks=[], currencies=[]))
        assert len(p.calls) == 2, f"the retry must not be served from a cache ({len(p.calls)})"


def test_g_a_partially_covered_bundle_is_cached_with_its_gaps():
    """The counterweight to the two above: partial is not failure. A book where
    one holding is delisted must not re-request the whole universe on every
    command for the rest of the day."""
    with provider() as p:
        bundle = p.resolve(_request(instruments=["NVDA", "DEAD"], benchmarks=[], currencies=[]))
        assert bundle.usable and bundle.coverage()["missing"] == ["DEAD"]
        assert p.day_entries(), "a bundle with real coverage must be cached"
        market_data.reset_memo()
        again = p.resolve(_request(instruments=["NVDA", "DEAD"], benchmarks=[], currencies=[]))
        assert len(p.calls) == 1, f"the cached partial bundle must answer again ({len(p.calls)})"
        assert [g["code"] for g in again.gaps] == ["symbol_unpriced"], (
            f"and it must still say what it could not price: {again.gaps}")


def test_g_an_unreadable_cache_entry_is_a_miss_rather_than_a_crash():
    with provider() as p:
        os.makedirs(os.path.join(p.root, "cache"), exist_ok=True)
        with open(os.path.join(p.root, "cache", f"{market_data.CACHE_KIND}.json"),
                  "w", encoding="utf-8") as handle:
            json.dump({"date": DAY, "entries": {"x": {"source": "yahoo"},
                                                "y": "not even an object"}}, handle)
        bundle = p.resolve(_request())
        assert len(p.calls) == 1 and bundle.usable, (
            "a cache is an optimization: an entry an older engine wrote must re-resolve, never "
            "raise into a user's review")


def test_g_an_unpriced_symbol_is_absent_from_the_frame_rather_than_a_zero_column():
    """The same posture ``price_feed.to_frame`` already takes for an envelope that
    covers nothing for a ticker: no column at all. A column of zeros would be
    valued and weighted; a column of NaN would work but invites a consumer to
    aggregate over a ghost. Either way the fact lives in ``coverage``."""
    with provider() as p:
        bundle = p.resolve(_request(instruments=["NVDA", "DEAD"], benchmarks=[], currencies=[]))
    assert "DEAD" not in list(bundle.frame.columns), \
        f"an unpriced symbol must not become a column: {list(bundle.frame.columns)}"
    assert bundle.coverage()["missing"] == ["DEAD"], \
        "and it must still be visibly missing, not silently gone"


def test_g_the_bundle_survives_a_json_round_trip_with_its_gaps_intact():
    # SPY quotes only on the first day, so its column carries a real hole — the
    # thing that has to survive the cache as a hole rather than as a zero.
    closes = dict(RECORDED_CLOSES, SPY=[(_d("2026-07-28"), 725.0)])
    with provider(FakeProvider(closes=closes)) as p:
        original = p.resolve(_request(instruments=["NVDA", "DEAD"], benchmarks=["SPY"],
                                      currencies=["TWD"]))
    restored = market_data.MarketDataBundle.from_json(original.to_json())
    assert restored is not None
    assert restored.splits == original.splits
    assert restored.fx == original.fx
    assert restored.gaps == original.gaps, (
        f"a gap that does not survive the cache becomes a silent success: {restored.gaps}")
    assert restored.coverage() == original.coverage()
    assert restored.as_of == original.as_of
    assert math.isnan(float(restored.frame["SPY"].iloc[-1])), (
        "NaN is not valid JSON, so a hole round-trips through null; if it came back as 0.0 the "
        "series would read as a total collapse on that day and every return differenced from it "
        "would inherit the error")
    assert float(restored.frame["NVDA"].iloc[-1]) == 190.0


def test_g_the_cache_writes_under_the_registered_cache_directory():
    with provider() as p:
        p.resolve(_request())
        path = os.path.join(p.root, "cache", f"{market_data.CACHE_KIND}.json")
        assert os.path.exists(path), (
            "the bundle must live under cache/, which coach.DATA_FILES registers as a directory — "
            "a file outside it is one data-export and data-reset cannot see (#452)")


# ───── I. the envelope: how a resolved bundle reaches the tested route ─────

def test_i_a_resolved_bundle_becomes_an_envelope_its_own_parser_accepts():
    """The whole point of emitting the existing shape: the two routes that consume
    envelopes will accept this one. A builder whose output `parse` rejects would
    have been discovered by a user, at the moment they asked a question."""
    with provider() as p:
        bundle = p.resolve(_request(instruments=["NVDA", "AAPL"], benchmarks=["SPY"],
                                    currencies=["TWD"]))
    envelope = market_data.to_price_feed_envelope(
        bundle, currency_by_ticker={"NVDA": "USD", "AAPL": "USD"})
    parsed = price_feed.parse(envelope)
    assert sorted(parsed["prices"]) == ["AAPL", "NVDA"], (
        "instruments only by default: a benchmark is not a holding, and letting one into the "
        f"envelope would put it in the book consider reasons about: {sorted(parsed['prices'])}")
    assert parsed["prices"]["NVDA"]["close"] == 190.0
    assert price_feed.fx_rates(parsed)["TWD"] == round(1.0 / 32.3638, 6)


def test_i_the_split_observations_travel_with_the_quotes():
    """They are what makes the two operands comparable through the existing
    machinery: `splits_map` turns them into the per-ticker override
    `review._effective_splits` prefers, for the same reason a supplied envelope's
    own splits win — they arrived with these quotes, at one instant."""
    with provider() as p:
        bundle = p.resolve(_request(instruments=["NVDA"], benchmarks=[], currencies=[]))
    envelope = market_data.to_price_feed_envelope(bundle, currency_by_ticker={"NVDA": "USD"})
    parsed = price_feed.parse(envelope)
    assert price_feed.splits_map(parsed) == {"NVDA": [(_d("2026-07-29"), 10.0)]}, (
        f"the observed split did not survive into the envelope: {envelope}")


def test_i_a_current_observation_declares_no_split_after_itself_so_no_basis_conflict():
    """A provider close fetched with `auto_adjust=True` is already on today's
    basis, and the envelope's contract is that a close is raw and gets rebased
    from its own date. Those two agree only because the observation is *current*:
    there are no splits after today to divide out, so `factor_after` is 1.0 on
    both sides and `basis_conflicts` — the fail-closed gate `consider` runs before
    reading any book — has nothing to refuse. If this ever fires for a resolved
    run, every consider on a split-crossing book refuses instead of answering."""
    with provider() as p:
        bundle = p.resolve(_request(instruments=["NVDA"], benchmarks=[], currencies=[]))
    parsed = price_feed.parse(market_data.to_price_feed_envelope(
        bundle, currency_by_ticker={"NVDA": "USD"}))
    book = price_feed.splits_map(parsed)
    assert price_feed.basis_conflicts(parsed, book) == [], (
        "a resolved envelope must not manufacture the refusal a genuinely mismatched supplied "
        f"one earns: {price_feed.basis_conflicts(parsed, book)}")
    assert parsed["prices"]["NVDA"]["split_factor"] == 1.0
    assert parsed["prices"]["NVDA"]["basis_date"] == parsed["prices"]["NVDA"]["observed_date"]


def test_i_the_declared_currency_is_the_callers_not_a_default():
    """`price_feed.currency_conflicts` compares this against the trades, so a
    per-ticker default would either invent a conflict or hide one."""
    with provider() as p:
        bundle = p.resolve(_request(instruments=["2330.TW", "NVDA"], benchmarks=[],
                                    currencies=["TWD"]))
    envelope = market_data.to_price_feed_envelope(
        bundle, currency_by_ticker={"2330.TW": "TWD", "NVDA": "USD"})
    declared = {row["ticker"]: row["currency"] for row in envelope["prices"]}
    assert declared == {"NVDA": "USD"}, (
        "only priced instruments belong in the envelope, each with the currency the caller "
        f"read from the book: {declared}")
    with provider(FakeProvider(closes=dict(RECORDED_CLOSES,
                                           **{"2330.TW": [(_d("2026-07-29"), 2205.0)]}))) as p:
        bundle = p.resolve(_request(instruments=["2330.TW"], benchmarks=[], currencies=["TWD"]))
    envelope = market_data.to_price_feed_envelope(bundle, currency_by_ticker={"2330.TW": "TWD"})
    assert envelope["prices"][0]["currency"] == "TWD", envelope
    price_feed.parse(envelope)                              # and it still parses


def test_i_a_bundle_that_priced_nothing_yields_no_envelope():
    """`parse` rightly refuses an envelope with no prices, so the builder must
    return None rather than hand one over: the caller then degrades to the
    pre-#605 answer instead of raising into a review."""
    with provider() as p:
        bundle = p.resolve(_request(instruments=["DEAD"], benchmarks=[], currencies=[]))
    assert market_data.to_price_feed_envelope(bundle) is None
    with provider() as p:
        offline = p.resolve(_request(), env={"TR_OFFLINE": "1"})
    assert market_data.to_price_feed_envelope(offline) is None


def test_i_the_envelope_as_of_is_the_newest_row_not_the_frames_own_date():
    """`parse` refuses any observation dated after `as_of`. A frame's last row can
    belong to one late-closing market, which would put the frame-level date ahead
    of every instrument in the envelope — or, with the mix reversed, behind one."""
    closes = dict(RECORDED_CLOSES, NVDA=[(_d("2026-07-28"), 180.0)])
    with provider(FakeProvider(closes=closes, actions={})) as p:
        bundle = p.resolve(_request(instruments=["NVDA"], benchmarks=["SPY"], currencies=[]))
    envelope = market_data.to_price_feed_envelope(bundle, currency_by_ticker={"NVDA": "USD"})
    assert envelope["as_of"] == "2026-07-28", (
        f"as_of must be the newest row actually present, not the frame's: {envelope}")
    price_feed.parse(envelope)                              # would raise on a future-dated row


def test_i_a_split_newer_than_the_close_it_would_divide_is_never_declared():
    """The double-adjustment trap, found by driving this builder's own output
    through `parse`.

    A provider close is already retro-adjusted, and `parse` divides a declared
    close by the splits *after* its date. So an observation dated 07-28 shipped
    beside a 07-29 split gets divided by ten a second time — the tenfold weight
    #583 exists to prevent, arriving through the repair for it. It also breaks
    `parse` outright, since a split after `as_of` is refused.

    Dropping the event is the correct pairing, not damage control: a split newer
    than every close this instrument printed has been applied to none of them, so
    quotes and share counts are both pre-split. The share side then falls back to
    the frozen map, where `basis_conflicts` refuses if the two cannot be
    reconciled.
    """
    closes = dict(RECORDED_CLOSES, NVDA=[(_d("2026-07-28"), 180.0)])
    actions = {"NVDA": [(_d("2026-07-29"), 10.0)]}           # newer than NVDA's own close
    with provider(FakeProvider(closes=closes, actions=actions)) as p:
        bundle = p.resolve(_request(instruments=["NVDA"], benchmarks=[], currencies=[]))
    assert bundle.splits == {"NVDA": [(_d("2026-07-29"), 10.0)]}, (
        "the bundle must still *observe* it — this is an envelope-pairing rule, not a reason to "
        f"discard what the provider reported: {bundle.splits}")
    envelope = market_data.to_price_feed_envelope(bundle, currency_by_ticker={"NVDA": "USD"})
    assert "splits" not in envelope["prices"][0], (
        f"a split newer than the close it would divide must not be declared: {envelope}")
    parsed = price_feed.parse(envelope)                      # must not raise
    assert parsed["prices"]["NVDA"]["close"] == 180.0, (
        "and the close must be left exactly as the provider reported it, not divided again: "
        f"{parsed['prices']['NVDA']}")
    # An in-window split at or before the close is still declared — the normal case
    # this must not have broken.
    with provider() as p:
        normal = p.resolve(_request(instruments=["NVDA"], benchmarks=[], currencies=[]))
    normal_envelope = market_data.to_price_feed_envelope(
        normal, currency_by_ticker={"NVDA": "USD"})
    assert normal_envelope["prices"][0]["splits"] == [["2026-07-29", 10.0]], normal_envelope


# ───────────────── H. the boundary itself: one provider site ─────────────────

#: Modules that still reach the provider directly. Each entry is debt with an
#: owner, not an exemption: #605's commits 2 and 3 remove them as `prepare` and
#: `consider` move onto the resolver. A module arriving here without that
#: migration is the drift this gate exists to make loud.
GRANDFATHERED_PROVIDER_SITES = {
    # Not on any supported route: `prepare` builds market context from the shared
    # price frame (`trade_recap.market_context_from_prices`), and this module's
    # own `fetch_series` is reachable only by invoking `engine/market_context.py`
    # directly, which AGENTS.md boundary 7 forbids. Left alone deliberately rather
    # than wrapped in an adapter no supported route reaches (#605 non-goals).
    "market_context.py",
}


def _engine_modules():
    for name in sorted(os.listdir(ENGINE)):
        if name.endswith(".py") and not name.startswith("test_"):
            yield name, os.path.join(ENGINE, name)


def test_h_only_the_adapter_imports_the_provider():
    """Parsed, not grepped: a docstring or a doctor message naming `yfinance` is
    prose, and only an actual import is a second acquisition path. This is the
    guard that keeps the next workflow from rediscovering symbol formatting, FX
    direction and cache policy for itself — the duplication #605 problem 1 is."""
    import ast
    offenders = {}
    for name, path in _engine_modules():
        if name in {"market_data.py"} | GRANDFATHERED_PROVIDER_SITES:
            continue
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(str(n).split(".")[0] == "yfinance" for n in names):
                offenders.setdefault(name, []).append(getattr(node, "lineno", "?"))
    assert not offenders, (
        f"these modules import the provider directly: {offenders}. Market data is acquired "
        "through market_data.resolve so prices, split observations and FX describe one instant "
        "and cost one request per symbol; add a grandfather entry only with the issue that "
        "removes it.")


def test_h_every_grandfathered_site_still_actually_has_the_import():
    """The exemption list decays on its own. A migrated module left in it turns
    the gate into a permanent hole — the same reason `test_split_basis.py`
    re-checks its own `_NO_MAP_NEEDED` entries against live call sites."""
    import ast
    stale = []
    for name in sorted(GRANDFATHERED_PROVIDER_SITES):
        path = os.path.join(ENGINE, name)
        if not os.path.exists(path):
            stale.append(f"{name} (module is gone)")
            continue
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        imports = [n for n in ast.walk(tree)
                   if isinstance(n, (ast.Import, ast.ImportFrom))
                   and any(str(getattr(a, "name", n if isinstance(n, ast.Import) else
                                       (n.module or ""))).split(".")[0] == "yfinance"
                           for a in (n.names if isinstance(n, ast.Import) else [n]))]
        if not imports:
            stale.append(f"{name} (no provider import left)")
    assert not stale, (
        f"remove these from GRANDFATHERED_PROVIDER_SITES — they have migrated: {stale}")


def test_h_the_offline_posture_has_exactly_one_reader():
    """`network_allowed` is the only place the posture is decided. A second read
    of the environment variable is a second policy, which is how a new route
    ends up quietly online in a deterministic suite."""
    import ast
    readers = {}
    for name, path in _engine_modules():
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        if market_data.OFFLINE_ENV not in source:
            continue
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == market_data.OFFLINE_ENV:
                readers.setdefault(name, []).append(node.lineno)
    assert set(readers) == {"market_data.py"}, (
        f"{market_data.OFFLINE_ENV} is read outside the resolver: {readers}. One reader, so the "
        "posture cannot be re-decided per route.")


# ─────────────────── the live shape witness (opt-in only) ───────────────────

def test_network_response_shape_is_still_what_we_recorded():
    """Not the oracle — the witness. Everything above is decided offline against
    a recorded response; this only asks whether the real provider still returns
    that shape, and reports the request count it actually cost."""
    if os.environ.get("TR_TEST_NETWORK") != "1":
        return
    request = market_data.build_request(
        instruments=["NVDA", "AAPL"], benchmarks=["SPY"], currencies=["TWD"],
        window_start="2024-01-01")
    calls = {"n": 0}
    original = None
    try:
        import curl_cffi.requests as cr
        original = cr.Session.request

        def counting(self, method, url, *a, **kw):
            calls["n"] += 1
            return original(self, method, url, *a, **kw)
        cr.Session.request = counting
    except ImportError:
        pass
    try:
        market_data.reset_memo()
        with tempfile.TemporaryDirectory() as root:
            bundle = market_data.resolve(request, root=root, env={"TR_OFFLINE": "0"})
    finally:
        if original is not None:
            cr.Session.request = original
        market_data.reset_memo()
    assert bundle.source == "yahoo" and bundle.priced, (
        f"live resolution produced nothing usable: {bundle.gaps}")
    assert bundle.fx.get("TWD", 0) > 0, f"live FX did not resolve: {bundle.gaps}"
    assert bundle.splits.get("NVDA"), (
        "NVDA's 2024-06-10 ten-for-one is inside this window; its absence means actions=True "
        f"stopped delivering split observations: {bundle.splits}")
    print(f"    (live: {calls['n']} provider requests for "
          f"{len(market_data.request_universe(request))} distinct symbols)")


def _tests():
    return [(name, obj) for name, obj in sorted(globals().items())
            if name.startswith("test_") and callable(obj)]


if __name__ == "__main__":
    failed = 0
    for name, fn in _tests():
        try:
            fn()
            print(f"✅ {name}")
        except AssertionError as exc:
            failed += 1
            print(f"❌ {name}: {exc}")
    total = len(_tests())
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
