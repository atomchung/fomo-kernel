#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""review.py positions -- offline, deterministic, no pytest (#561).

The read-only current-book outlet: "what do I currently hold," asked away
from any review, a `consider` premise, or a `refresh` snapshot. It sources
the book from `<root>/ledger.jsonl` alone, through the same FIFO pipeline
(`round_trips` -> `fifo_held` -> `classify_adds` -> `dim_size` ->
`ticker_diagnosis`) `trade_recap.py`'s own CSV review runs, so this suite
settles what belongs to the CLI facade rather than to functions
tests/test_engine_units.py already covers: which tickers land in
`positions` versus `residual_positions`, that shares/cost/value/weight and
the diagnosis tags agree with a hand-computed expectation, the fail-closed
behavior with no recorded book, and -- the acceptance property #561 exists
for -- that the command truly writes nothing durable.

All fixtures are built under a temp root; nothing here reads or writes a
real coach root. Every subprocess call passes --root explicitly.
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "skills" / "fomo-kernel" / "engine"
REVIEW = ENGINE_DIR / "review.py"

# This suite drives a priced route, so it declares its market posture through
# tests/offline_posture.py so a direct `python3 tests/<this file>` run and a
# `run_all.py` run reach the same answer; TR_TEST_NETWORK=1 still opts in.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_posture  # noqa: E402
offline_posture.apply()

sys.path.insert(0, str(ENGINE_DIR))
import ledger as ledger_engine  # noqa: E402


# ─────────────────────────────── helpers ───────────────────────────────

def _run(*args):
    return subprocess.run([sys.executable, str(REVIEW), *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)


def _ok(run):
    assert run.returncode == 0, f"expected success, got {run.returncode}: {run.stdout}{run.stderr}"
    return json.loads(run.stdout)


def _fails(run, fragment):
    assert run.returncode != 0, f"expected failure, got exit 0: {run.stdout}"
    payload = json.loads(run.stdout)
    assert payload.get("status") == "error", f"expected a status:error payload, got {payload}"
    assert fragment in payload["error"], f"wanted {fragment!r} in error, got {payload['error']!r}"
    return payload


def _write_ledger(path, events):
    with open(path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _snapshot_event(as_of, positions):
    return {"type": "snapshot", "as_of": as_of,
            "source": ledger_engine.DECLARED_BOOK_SOURCE, "positions": positions}


def _trade_event(date, ticker, action, qty, price, market="US", currency="USD"):
    return {"type": "trade", "date": date, "ticker": ticker, "action": action,
            "qty": qty, "price": price, "market": market, "currency": currency}


def _fx_envelope(path, closes, as_of="2026-07-30", fx=None):
    """A minimal --prices envelope. `closes` is {ticker: (close, currency)};
    `fx` is {currency: usd_per_unit}, omitted entirely when None -- which is
    exactly the shape a mixed-currency book cannot be weighted from."""
    payload = {"as_of": as_of, "source": "test",
               "prices": [{"ticker": ticker, "close": close, "date": as_of, "currency": currency}
                          for ticker, (close, currency) in sorted(closes.items())]}
    if fx:
        payload["fx"] = [{"currency": currency, "usd_per_unit": rate, "date": as_of}
                         for currency, rate in sorted(fx.items())]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return str(path)


def _tree_snapshot(root):
    """Every file under root, as a {relative_path: bytes} map -- the
    before/after comparison `test_positions_writes_nothing_durable` needs.
    Byte content, not just names: a rewritten-but-same-named file would
    pass a names-only check and still be a durable write."""
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            with open(path, "rb") as f:
                out[rel] = f.read()
    return out


# The book every test below starts from: a declared anchor, one add, one
# partial sell -- small enough to hand-verify every number in the assertions.
_BOOK = (
    _snapshot_event("2026-01-01", [
        {"ticker": "NVDA", "shares": 100, "avg_cost": 100.0, "market": "US", "currency": "USD"},
        {"ticker": "AMD", "shares": 50, "avg_cost": 80.0, "market": "US", "currency": "USD"},
    ]),
    _trade_event("2026-02-01", "NVDA", "buy", 20, 120.0),
    _trade_event("2026-03-01", "AMD", "sell", 10, 90.0),
)
# NVDA: 120 shares, cost_total 12,400 (100*100 + 20*120), avg_cost 103.33...
# AMD:   40 shares, cost_total  3,200 (4,000 anchor cost minus 10 sold at the
#        80 average cost basis), avg_cost 80, realized 100 (10 * (90 - 80)).
_CLOSES = {"NVDA": (200.0, "USD"), "AMD": (100.0, "USD")}
# value: NVDA 120*200=24,000; AMD 40*100=4,000. weight: 24,000/28,000 and
# 4,000/28,000 -- NVDA clears the 25% too_heavy trigger, AMD does not.


def test_positions_reports_shares_avg_cost_value_and_weight():
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), _BOOK)
        prices = _fx_envelope(os.path.join(tmp, "prices.json"), _CLOSES)
        payload = _ok(_run("positions", "--root", tmp, "--prices", prices))

        assert payload["status"] == "ok"
        assert payload["basis"] == "priced"
        assert payload["mixed_currency"] is False
        assert payload["n_holdings"] == 2
        assert payload["residual_positions"] == []
        assert payload["unpriced"] == []

        rows = {row["ticker"]: row for row in payload["positions"]}
        assert set(rows) == {"NVDA", "AMD"}

        nvda = rows["NVDA"]
        assert nvda["shares"] == 120.0
        assert nvda["cost_total"] == 12400.0
        # portfolio_basis.derive_holdings rounds avg_cost to 4dp on the way
        # out (12400/120 has no exact decimal representation), so compare
        # against that same rounded expectation rather than the raw ratio.
        assert abs(nvda["avg_cost"] - round(12400.0 / 120.0, 4)) < 1e-6
        assert nvda["value"] == 24000.0
        assert abs(nvda["weight"] - 24000.0 / 28000.0) < 1e-9
        assert abs(nvda["impact"] - 11600.0) < 1e-6, "unrealized only: 120*200 - 12400"
        nvda_codes = {tag["code"] for tag in nvda["tags"]}
        assert nvda_codes == {"too_heavy", "disciplined_hold"}, nvda["tags"]

        amd = rows["AMD"]
        assert amd["shares"] == 40.0
        assert amd["cost_total"] == 3200.0
        assert amd["avg_cost"] == 80.0
        assert amd["value"] == 4000.0
        assert abs(amd["weight"] - 4000.0 / 28000.0) < 1e-9
        assert abs(amd["impact"] - 900.0) < 1e-6, "realized 100 (10 * (90-80)) + unrealized 800"
        amd_codes = {tag["code"] for tag in amd["tags"]}
        assert amd_codes == {"disciplined_hold"}, "AMD's 14% weight must not trip too_heavy"

        # Sorted by |impact| descending -- the same ranking ticker_diagnosis
        # (and the README-demoed card) already uses.
        assert [row["ticker"] for row in payload["positions"]] == ["NVDA", "AMD"]

        assert payload["price_snapshot"]["as_of"] == "2026-07-30"
        assert payload["price_snapshot"]["observed"] == {"AMD": "2026-07-30", "NVDA": "2026-07-30"}


def test_positions_reports_cost_basis_with_no_price_available():
    """No --prices and TR_OFFLINE degrade this the same way every other
    price-dependent path degrades: cost-basis weights, no value, no
    diagnosis for a ticker whose impact cannot be computed without a
    current price."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), _BOOK)
        env = dict(os.environ, TR_OFFLINE="1")
        run = subprocess.run([sys.executable, str(REVIEW), "positions", "--root", tmp],
                             cwd=ROOT, capture_output=True, text=True, timeout=60, env=env)
        payload = _ok(run)

        assert payload["basis"] == "cost"
        assert payload["price_snapshot"] is None
        assert sorted(payload["unpriced"]) == ["AMD", "NVDA"]
        rows = {row["ticker"]: row for row in payload["positions"]}
        assert rows["NVDA"]["value"] is None
        # Weight still resolves from cost (sizing_projection's cost
        # fallback), so the book's relative sizing is never simply
        # unavailable for want of a quote.
        assert abs(rows["NVDA"]["weight"] - 12400.0 / (12400.0 + 3200.0)) < 1e-9
        # impact needs a current price for every ticker, priced or not:
        # `current_value + lifetime_proceeds - lifetime_cost` cannot state a
        # total without knowing current_value, and printing only the
        # realized slice under the "impact" label would read as the whole
        # answer when it is not -- the same "do not print a partial number
        # under the label for a complete one" rule the owner's ruling states
        # for the mixed-basis case (#561).
        assert rows["AMD"]["impact"] is None
        assert rows["NVDA"]["impact"] is None
        assert rows["NVDA"]["tags"] == []


# Two lots at different prices, then a partial sell -- the exact shape that
# diverges between a FIFO reconstruction and the ledger's canonical
# average-cost book (owner reproduction, 2026-07-30, on this PR): FIFO
# matches the sell against the first lot and leaves the second lot's cost as
# what remains; average cost removes the sale at the *blended* average and
# leaves the rest at that same average. Same shares remaining, two different
# costs, two different weights for the same position at the same instant.
_MULTI_LOT_BOOK = (
    _trade_event("2026-01-01", "NVDA", "buy", 100, 10.0),
    _trade_event("2026-01-15", "NVDA", "buy", 100, 20.0),
    _trade_event("2026-02-01", "NVDA", "sell", 100, 50.0),
    _trade_event("2026-01-01", "MSFT", "buy", 100, 10.0),
)
_MULTI_LOT_CLOSES = {"NVDA": (30.0, "USD"), "MSFT": (20.0, "USD")}


def _consider_before(tmp, prices=None):
    args = ["consider", "--root", tmp]
    if prices:
        args += ["--prices", prices]
    args += ["--premise", '{"ticker": "MSFT", "side": "buy", "qty": 1, "price": 20.0, "currency": "USD"}']
    return _ok(_run(*args))["evaluation"]["consequence"]["before"]


def test_positions_and_consider_agree_on_weight_for_a_multi_lot_partial_sell():
    """The mechanical guard for the owner's 2026-07-30 ruling on this PR:
    `positions` and `consider` must report the identical weight -- the
    number this product's own rules are built on ("cap any single position
    at 20%") -- for the same book at the same instant, not two answers that
    happen to agree on the single-lot fixtures elsewhere in this file.
    Asserts the agreement directly against a live `consider` call rather
    than a hand-computed expectation, so this cannot go stale the way a
    comment claiming "these use the same basis" already has once (#456).

    Two sub-cases, because weight and cost do not diverge on the same
    signal: priced, weight comes from market value (shares x price, which
    does not depend on lot-matching) so *cost*/*avg_cost* are what a FIFO
    reconstruction would get wrong; unpriced, weight itself falls back to
    cost and is exactly what diverges. Checking only the priced case would
    let the weight assertion below pass by coincidence rather than by
    construction -- both must be driven to mean anything."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), _MULTI_LOT_BOOK)
        prices = _fx_envelope(os.path.join(tmp, "prices.json"), _MULTI_LOT_CLOSES)

        positions_payload = _ok(_run("positions", "--root", tmp, "--prices", prices))
        rows = {row["ticker"]: row for row in positions_payload["positions"]}
        assert rows["NVDA"]["shares"] == 100.0

        before = _consider_before(tmp, prices)
        assert before["held"]["NVDA"]["shares"] == rows["NVDA"]["shares"]
        assert abs(before["held"]["NVDA"]["cost"] - rows["NVDA"]["cost_total"]) < 1e-6, (
            "positions and consider disagree about NVDA's remaining cost on a "
            "multi-lot partial sell", before["held"]["NVDA"], rows["NVDA"])
        assert abs(before["weights"]["NVDA"] - rows["NVDA"]["weight"]) < 1e-9, (
            "positions and consider disagree about NVDA's weight -- the exact "
            "divergence the 2026-07-30 ruling closed",
            before["weights"]["NVDA"], rows["NVDA"]["weight"])
        # The average-cost answer specifically, not merely "some shared
        # answer": FIFO would report 2000.0/20.0 here instead.
        assert rows["NVDA"]["cost_total"] == 1500.0
        assert rows["NVDA"]["avg_cost"] == 15.0

        # Unpriced: weight itself is the signal that diverges under the
        # wrong basis (FIFO's cost-fallback weight here is 2000/3000 =
        # 0.6667; the canonical one is 1500/2500 = 0.6), so this half would
        # catch a regression the priced half's coincidence could not.
        env = dict(os.environ, TR_OFFLINE="1")
        unpriced_positions = _ok(subprocess.run(
            [sys.executable, str(REVIEW), "positions", "--root", tmp],
            cwd=ROOT, capture_output=True, text=True, timeout=60, env=env))
        unpriced_rows = {row["ticker"]: row for row in unpriced_positions["positions"]}
        unpriced_before = _consider_before(tmp)
        assert abs(unpriced_before["weights"]["NVDA"] - unpriced_rows["NVDA"]["weight"]) < 1e-9, (
            "positions and consider disagree about NVDA's cost-fallback weight",
            unpriced_before["weights"]["NVDA"], unpriced_rows["NVDA"]["weight"])
        assert abs(unpriced_rows["NVDA"]["weight"] - 1500.0 / 2500.0) < 1e-9


def test_positions_separates_a_residual_position_from_diagnosed_ones():
    """A ticker below the meaningful-position floor (#172) is still named
    with its shares/cost/weight -- never silently dropped from the book --
    but carries no diagnosis tags, matching the product's own "small lots
    not nitpicked" framing."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), (
            _snapshot_event("2026-01-01", [
                {"ticker": "NVDA", "shares": 1000, "avg_cost": 100.0,
                 "market": "US", "currency": "USD"},
                {"ticker": "DUST", "shares": 1, "avg_cost": 5.0,
                 "market": "US", "currency": "USD"},
            ]),
        ))
        env = dict(os.environ, TR_OFFLINE="1")
        run = subprocess.run([sys.executable, str(REVIEW), "positions", "--root", tmp],
                             cwd=ROOT, capture_output=True, text=True, timeout=60, env=env)
        payload = _ok(run)

        assert payload["n_holdings"] == 2
        assert [row["ticker"] for row in payload["positions"]] == ["NVDA"]
        residual = {row["ticker"]: row for row in payload["residual_positions"]}
        assert set(residual) == {"DUST"}
        assert residual["DUST"]["shares"] == 1.0
        assert residual["DUST"]["cost_total"] == 5.0
        assert "tags" not in residual["DUST"] and "impact" not in residual["DUST"]


# ────────────────────── mixed-currency weighting (#737) ──────────────────────

# A synthetic two-currency book. `TWAA.TW` is deliberately letter-based: real
# Taiwan listings are numeric, so nothing here can be mistaken for a holding
# anyone actually has. The TWD position dominates in USD terms, which is the
# whole point -- it is the position a null weight hides.
_MIXED_BOOK = (
    _snapshot_event("2026-01-01", [
        {"ticker": "TWAA.TW", "shares": 2000, "avg_cost": 850.0,
         "market": "TW", "currency": "TWD"},
        {"ticker": "AAA", "shares": 50, "avg_cost": 195.0,
         "market": "US", "currency": "USD"},
        {"ticker": "BBB", "shares": 30, "avg_cost": 120.0,
         "market": "US", "currency": "USD"},
    ]),
)
_MIXED_CLOSES = {"TWAA.TW": (2425.0, "TWD"), "AAA": (308.91, "USD"), "BBB": (200.75, "USD")}
_TWD_USD = 0.0325
# Native values: TWAA.TW 2000*2425 = 4,850,000 TWD; AAA 50*308.91 = 15,445.50
# USD; BBB 30*200.75 = 6,022.50 USD. Converted at 0.0325 USD/TWD the TWD leg is
# 157,625.00 USD, so the book is 179,093.00 USD and TWAA.TW is 88% of it. Summed
# at face value instead, the same leg would read as 99.6% -- which is why the
# engine refuses to aggregate unconverted currencies at all.
_MIXED_USD = {"TWAA.TW": 4_850_000.0 * _TWD_USD, "AAA": 15_445.5, "BBB": 6_022.5}
_MIXED_DENOM = sum(_MIXED_USD.values())


def _assert_every_null_weight_is_named(payload):
    """The invariant #737 is really about: a weight the engine could not
    compute is never merely absent. Vacuously true on a fully weighted book,
    which is the point -- it holds on both sides rather than only where the
    disclosure fires."""
    reported = payload["positions"] + payload["residual_positions"]
    null_weighted = {row["ticker"] for row in reported if row["weight"] is None}
    named = set(payload["sizing"]["unweighted"])
    assert null_weighted == named, (
        "every holding with no weight must be named in sizing.unweighted with the "
        "engine's own reason; silently null is the #737 defect",
        sorted(null_weighted), sorted(named))
    for ticker, reason in payload["sizing"]["unweighted"].items():
        assert reason, f"{ticker} is unweighted with no stated reason"


def test_positions_weights_a_fully_priced_mixed_currency_book():
    """#737. A mixed-currency book with every holding priced and a rate for
    every held currency returned `positions: []`, pushed all of it into
    `residual_positions`, and reported `weight: null` on every row -- while
    `basis: "priced"`, `unpriced: []` and `status: "ok"` all said the answer
    was complete. `consider` computed correct weights for the identical book
    in the identical root at the same instant.

    The cause was that `positions` hand-built a bare `{as_of, prices}` map,
    which `sizing_projection` will not aggregate across currencies -- correctly,
    since summing a ~31:1 currency at face value inverts which holding is the
    largest. Asserting against a live `consider` call rather than only against
    hand-computed numbers is deliberate: the guarantee is that the two commands
    describe one book, and that is the thing that silently stopped being true.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), _MIXED_BOOK)
        prices = _fx_envelope(os.path.join(tmp, "prices.json"), _MIXED_CLOSES,
                              fx={"TWD": _TWD_USD})
        payload = _ok(_run("positions", "--root", tmp, "--prices", prices))

        assert payload["status"] == "ok"
        assert payload["basis"] == "priced"
        assert payload["mixed_currency"] is True
        assert payload["unpriced"] == []
        assert payload["n_holdings"] == 3

        # The regression itself: real weights, and the largest holding is a
        # diagnosed position rather than residual dust.
        assert payload["residual_positions"] == [], (
            "a fully priced mixed-currency book has no residual holding; every "
            "row landing here is the #737 null-weight collapse", payload["residual_positions"])
        rows = {row["ticker"]: row for row in payload["positions"]}
        assert set(rows) == {"TWAA.TW", "AAA", "BBB"}
        assert [row["ticker"] for row in payload["positions"]][0] == "TWAA.TW", (
            "the largest holding must lead the diagnosed positions")
        for ticker, row in rows.items():
            assert row["weight"] is not None, f"{ticker} came back with a null weight"
            assert abs(row["weight"] - _MIXED_USD[ticker] / _MIXED_DENOM) < 1e-9, (
                ticker, row["weight"], _MIXED_USD[ticker] / _MIXED_DENOM)
        assert abs(sum(row["weight"] for row in rows.values()) - 1.0) < 1e-9
        assert abs(rows["TWAA.TW"]["weight"] - 0.88) < 0.01, (
            "the TWD leg is ~88% of the book once converted; ~99.6% would mean "
            "native values were summed at face value", rows["TWAA.TW"]["weight"])

        # `value` stays in each holding's own currency -- so `sizing` has to say
        # which currency the weights are measured in, or the two read as one basis.
        assert rows["TWAA.TW"]["value"] == 4_850_000.0, "native TWD value, not converted"
        assert payload["sizing"] == {"applicable": True, "reason": None,
                                     "aggregate_currency": "USD", "unweighted": {}}
        _assert_every_null_weight_is_named(payload)

        # And the two commands agree, which is the guarantee `cmd_positions`'
        # own comment claims and #737 falsified.
        before = _ok(_run(
            "consider", "--root", tmp, "--prices", prices,
            "--premise", '{"ticker": "AAA", "side": "buy", "qty": 1, "price": 308.91, '
                         '"currency": "USD"}'))["evaluation"]["consequence"]["before"]
        for ticker, row in rows.items():
            assert abs(before["weights"][ticker] - row["weight"]) < 1e-12, (
                "positions and consider disagree about a mixed-currency weight",
                ticker, before["weights"][ticker], row["weight"])


def test_positions_says_why_a_mixed_currency_weight_is_unavailable():
    """The other half, and the one that keeps the silent-null shape from
    coming back: when the weight genuinely cannot be computed -- here a
    mixed-currency book whose envelope carries no rate for the held currency
    -- the output must carry the engine's own reason rather than a bare null.

    `positions` still answers rather than refusing the way `consider` does:
    shares, average cost and each holding's native value are all true and are
    what "what do I currently hold" asked for. What it may not do is present
    that as a complete sizing answer.
    """
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), _MIXED_BOOK)
        prices = _fx_envelope(os.path.join(tmp, "prices.json"), _MIXED_CLOSES)  # no fx block
        payload = _ok(_run("positions", "--root", tmp, "--prices", prices))

        assert payload["status"] == "ok"
        sizing = payload["sizing"]
        assert sizing["applicable"] is False
        assert sizing["reason"] == "mixed_native_currencies", sizing
        assert sizing["aggregate_currency"] is None
        assert set(sizing["unweighted"]) == {"TWAA.TW", "AAA", "BBB"}
        _assert_every_null_weight_is_named(payload)
        # The actionable half: `mixed_native_currencies` names the refusal,
        # while the frame's own coverage names the repair -- the same missing
        # TWD rate `consider`'s recovery payload tells the agent to go fetch.
        assert sizing["valuation_gaps"] == {"missing_fx": ["TWD"], "missing_price": []}, sizing

        # Everything that is still true is still reported.
        rows = {row["ticker"]: row
                for row in payload["positions"] + payload["residual_positions"]}
        assert rows["TWAA.TW"]["shares"] == 2000.0
        assert rows["TWAA.TW"]["value"] == 4_850_000.0

        # Offline, with no prices at all, the same disclosure holds -- there is
        # no route on which a mixed-currency null weight goes unexplained.
        env = dict(os.environ, TR_OFFLINE="1")
        offline = _ok(subprocess.run(
            [sys.executable, str(REVIEW), "positions", "--root", tmp],
            cwd=ROOT, capture_output=True, text=True, timeout=60, env=env))
        assert offline["basis"] == "cost"
        assert offline["sizing"]["reason"] == "mixed_native_currencies"
        _assert_every_null_weight_is_named(offline)


# The provider seam, injected as `usercustomize` exactly as tests/test_consider.py
# does it (never `sitecustomize` -- Homebrew ships its own and shadowing it
# removes site-packages), so the real CLI runs against a deterministic provider.
# This is what makes the *engine-fetch* route -- `positions` with no `--prices`
# -- testable at all, and #737 turns on whether that route carries FX.
_FAKE_PROVIDER = '''
import datetime as dt
import os
import sys
sys.path.insert(0, os.environ["ENGINE_DIR"])
# Yahoo spells TWD/USD as `TWD=X` (TWD per USD), which market_data inverts into
# USD per TWD -- so 32.0 here is the 0.03125 rate the weights below come out of.
CLOSES = {"TWAA.TW": 2425.0, "AAA": 308.91, "BBB": 200.75, "TWD=X": 32.0}


def _fake_download(symbols, start, end=None):
    import pandas as pd
    open(os.environ["PROVIDER_LOG"], "a").write(",".join(sorted(symbols)) + "\\n")
    index = pd.DatetimeIndex([dt.datetime(2026, 7, 29)])
    data = {}
    for symbol in symbols:
        data[("Close", symbol)] = [CLOSES.get(symbol, 123.0)]
        data[("Stock Splits", symbol)] = [float("nan")]
    frame = pd.DataFrame(data, index=index)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    return frame, {}          # (data, per-symbol failures) -- nothing failed here


import market_data
market_data._download = _fake_download
market_data._provider_available = lambda: True
'''


def test_positions_resolves_fx_on_the_engine_fetch_route():
    """`positions` supports two price routes, and #737 is only fixed if both
    of them can produce a weight. The `--prices` route depends on the agent
    supplying an `fx` block; this is the other one -- no `--prices` at all --
    where the engine resolves the bundle itself.

    Asserted rather than reasoned about, because "the currency universe reaches
    the request" is exactly the kind of claim that reads as obviously true from
    the call graph and is worth one test to actually observe: the provider log
    must show `TWD=X` being asked for, and the weights must come out real.
    """
    try:
        import pandas  # noqa: F401
    except ImportError:                                     # pragma: no cover
        print("SKIP  test_positions_resolves_fx_on_the_engine_fetch_route (no pandas)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(os.path.join(tmp, "ledger.jsonl"), _MIXED_BOOK)
        sitedir = os.path.join(tmp, "provider-site")
        os.makedirs(sitedir, exist_ok=True)
        with open(os.path.join(sitedir, "usercustomize.py"), "w", encoding="utf-8") as handle:
            handle.write(_FAKE_PROVIDER)
        log = os.path.join(tmp, "provider.log")
        env = dict(os.environ)
        env.pop("TR_OFFLINE", None)     # the point is the resolution path, not the degradation
        env["ENGINE_DIR"] = str(ENGINE_DIR)
        env["PROVIDER_LOG"] = log
        env["PYTHONPATH"] = os.pathsep.join(
            [sitedir, str(ENGINE_DIR), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
        payload = _ok(subprocess.run(
            [sys.executable, str(REVIEW), "positions", "--root", tmp],
            cwd=ROOT, capture_output=True, text=True, timeout=120, env=env))

        with open(log, encoding="utf-8") as handle:
            asked = {symbol for line in handle for symbol in line.strip().split(",") if symbol}
        assert "TWD=X" in asked, (
            "the engine-fetch route must request the held currency's rate, or a "
            "mixed-currency book can never be weighted without --prices", sorted(asked))

        assert payload["basis"] == "priced"
        assert payload["mixed_currency"] is True
        # Weights first, so a regression here reads as the defect itself rather
        # than as a missing disclosure key.
        assert payload["residual_positions"] == []
        rows = {row["ticker"]: row for row in payload["positions"]}
        expected = {"TWAA.TW": 4_850_000.0 / 32.0, "AAA": 15_445.5, "BBB": 6_022.5}
        denominator = sum(expected.values())
        for ticker, row in rows.items():
            assert abs(row["weight"] - expected[ticker] / denominator) < 1e-9, (
                ticker, row["weight"], expected[ticker] / denominator)
        assert payload["sizing"]["applicable"] is True, payload["sizing"]
        assert payload["sizing"]["aggregate_currency"] == "USD"
        _assert_every_null_weight_is_named(payload)


def test_positions_fails_closed_with_no_recorded_book():
    with tempfile.TemporaryDirectory() as tmp:
        _fails(_run("positions", "--root", tmp), "no recorded book")


def test_positions_writes_nothing_durable():
    """The acceptance property #561 exists for: this command must create no
    session, append no row to any *.jsonl the coach root tracks, and ask no
    question. Compared by byte content under TR_OFFLINE, so no market-data
    acquisition cache enters the picture -- the root must come out exactly
    as it went in."""
    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = os.path.join(tmp, "ledger.jsonl")
        _write_ledger(ledger_path, _BOOK)
        before = _tree_snapshot(tmp)
        assert set(before) == {"ledger.jsonl"}

        env = dict(os.environ, TR_OFFLINE="1")
        run = subprocess.run([sys.executable, str(REVIEW), "positions", "--root", tmp],
                             cwd=ROOT, capture_output=True, text=True, timeout=60, env=env)
        assert run.returncode == 0, run.stdout + run.stderr

        after = _tree_snapshot(tmp)
        assert after == before, (
            "positions must not touch the coach root beyond reading it: "
            f"new or changed files: {sorted(set(after) - set(before)) or [k for k in before if before[k] != after.get(k)]}")

        # And run it again for good measure -- a passive lookup asked twice
        # must not accumulate anything on the second call either.
        run2 = subprocess.run([sys.executable, str(REVIEW), "positions", "--root", tmp],
                              cwd=ROOT, capture_output=True, text=True, timeout=60, env=env)
        assert run2.returncode == 0
        assert _tree_snapshot(tmp) == before


def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001 -- a crash must read as a failed test, not a traceback dump
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
