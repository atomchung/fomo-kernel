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


def _fx_envelope(path, closes, as_of="2026-07-30"):
    """A minimal --prices envelope. `closes` is {ticker: (close, currency)}."""
    payload = {"as_of": as_of, "source": "test",
               "prices": [{"ticker": ticker, "close": close, "date": as_of, "currency": currency}
                          for ticker, (close, currency) in sorted(closes.items())]}
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
        assert abs(nvda["avg_cost"] - 12400.0 / 120.0) < 1e-9
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
        # Weight still resolves from cost (dim_size's cost-fallback), so the
        # book's relative sizing is never simply unavailable for want of a quote.
        assert abs(rows["NVDA"]["weight"] - 12400.0 / (12400.0 + 3200.0)) < 1e-9
        # AMD's only P&L signal is its realized $100 -- still enough to clear
        # ticker_diagnosis's $1 floor and be reported, unlike NVDA (no sale,
        # no price, so no impact can be computed at all).
        assert rows["AMD"]["impact"] == 100.0
        assert rows["NVDA"]["impact"] is None
        assert rows["NVDA"]["tags"] == []


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
