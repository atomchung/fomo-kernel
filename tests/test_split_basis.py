#!/usr/bin/env python3
"""Split basis across every reader of a recorded share count (#558, #559).

#550 fixed one reader — `revisit.detect_exits`. This file locks the rest of
them, and the property they share: a quantity is only comparable to another
quantity when both are in the same split basis. The ledger keeps what was
transacted; the adjustment belongs to whoever adds those numbers up.

Each of the three points where `derive_holdings` applies the rule has its own
test here, because each fails on a different real book:

  anchor seeding  — a declaration, then a split, then trading resumes
  per trade       — quantities from either side of a split, added together
  tail            — a split after the last trade, which is the common case:
                    the user simply has not traded the ticker since

The counterweight (`test_a_real_full_exit_still_reads_as_a_full_exit`) is the
reason the others are not enough on their own. A "fix" that suppresses exit
detection, or that scales in the wrong direction, passes the bug tests and
fails that one.

Run:
  python3 tests/test_split_basis.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE)
import book_refresh as br  # noqa: E402
import ledger as lg  # noqa: E402
import revisit as rv  # noqa: E402
import snapshot_adapter  # noqa: E402

# NVDA's real ten-for-one, the split every case below is built on.
SPLITS = {"NVDA": [("2024-06-10", 10.0)]}


def _trade(date, action, qty, price, ticker="NVDA"):
    return {"type": "trade", "ticker": ticker, "action": action, "qty": qty,
            "price": price, "date": date, "market": "US", "currency": "USD"}


def _snapshot(as_of, shares, avg_cost=250.0, ticker="NVDA"):
    return {"type": "snapshot", "as_of": as_of, "source": "user_declared",
            "is_complete": True, "snapshot_id": f"snapshot-{as_of.replace('-', '')}0000",
            "positions": [{"ticker": ticker, "shares": shares, "avg_cost": avg_cost,
                           "market": "US", "currency": "USD"}]}


def _shares(events, splits=SPLITS, ticker="NVDA", **kw):
    held = lg.derive_holdings(events, splits=splits, **kw)["holdings"].get(ticker)
    return held["shares"] if held else None


# ─────────────────── the three application points ───────────────────

def test_a_declared_position_is_carried_across_a_later_split():
    """Anchor seeding. The declaration states 100 in its own day's basis; the
    split is after it, so the book holds 1,000 — and a trade that lands after
    the split must subtract from that, not from 100."""
    events = [_snapshot("2023-12-31", 100.0), _trade("2026-07-28", "SELL", 100.0, 197.0)]
    assert _shares(events) == 900.0, _shares(events)


def test_quantities_from_either_side_of_a_split_are_not_added_raw():
    """Per trade. This is #550's own repro ledger: 90 + 30 bought and 20 sold
    before the split, 100 sold after. Raw arithmetic reaches exactly zero,
    which is what made a ~10% trim read as a full liquidation."""
    events = [_trade("2023-01-10", "BUY", 90.0, 150.0),
              _trade("2023-11-15", "BUY", 30.0, 480.0),
              _trade("2024-05-20", "SELL", 20.0, 950.0),
              _trade("2026-07-28", "SELL", 100.0, 197.0)]
    assert _shares(events, splits=None) is None, "pre-#558 behaviour must be reproducible"
    assert _shares(events) == 900.0, _shares(events)


def test_a_split_after_the_last_trade_still_moves_the_count():
    """Tail. Nothing has been traded since the split — the single most common
    shape in the wild, and the one an adjustment applied only at trade time
    misses entirely."""
    assert _shares([_trade("2023-01-10", "BUY", 100.0, 150.0)]) == 1000.0


def test_a_split_already_reflected_in_the_declaration_is_not_applied_twice():
    """Anchor seeding, the other direction — and the case that actually pins
    it. A user who starts recording *after* the split declares the post-split
    count already: 1,000 is what their broker shows. Re-applying a split that
    predates the declaration would report 10,000.

    The mirror test above cannot catch a lost anchor basis, because treating
    the declaration as having no history happens to give the same answer when
    the only split falls after it. Here the split falls before, and the two
    readings diverge by a factor of ten."""
    events = [_snapshot("2025-01-01", 1000.0, avg_cost=25.0)]
    assert _shares(events) == 1000.0, _shares(events)
    with_trade = events + [_trade("2026-07-28", "SELL", 100.0, 197.0)]
    assert _shares(with_trade) == 900.0, _shares(with_trade)


def test_reading_the_book_before_the_split_does_not_see_it():
    """`holdings_as_of` bounds the tail. A declaration dated before the split
    is reconciled against the book as that day saw it, not as today does."""
    events = [_trade("2023-01-10", "BUY", 100.0, 150.0)]
    assert lg.holdings_as_of(events, "2024-01-01", splits=SPLITS)["NVDA"]["shares"] == 100.0
    assert lg.holdings_as_of(events, "2026-07-29", splits=SPLITS)["NVDA"]["shares"] == 1000.0


# ─────────────────────── the counterweight ───────────────────────

def test_a_real_full_exit_still_reads_as_a_full_exit():
    """The whole position, sold. A fix that merely stops positions from
    disappearing would pass every test above and fail this one."""
    events = [_trade("2023-01-10", "BUY", 90.0, 150.0),
              _trade("2023-11-15", "BUY", 30.0, 480.0),
              _trade("2024-05-20", "SELL", 20.0, 950.0),
              _trade("2026-07-28", "SELL", 1000.0, 197.0)]
    assert lg.derive_holdings(events, splits=SPLITS)["holdings"] == {}


def test_cost_is_untouched_by_a_split_so_avg_cost_falls_out_correct():
    """A split is a zero-dollar event. Shares scale, total cost does not, and
    the derived average has to follow — scaling cost too would leave the
    average unchanged across a ten-for-one."""
    held = lg.derive_holdings([_trade("2023-01-10", "BUY", 100.0, 150.0)],
                              splits=SPLITS)["holdings"]["NVDA"]
    assert held["shares"] == 1000.0
    assert held["cost_total"] == 15000.0
    assert held["avg_cost"] == 15.0


def test_cost_survives_the_carry_so_a_later_trade_prices_correctly():
    """The same zero-dollar property, on the path the test above does not
    reach. A position that is *carried* across a split — declared before it,
    traded after it — goes through the running-position rebase rather than the
    tail one. Scaling cost there too would leave the average at 250 on a book
    whose broker shows 25, and every downstream weight and return would inherit
    it."""
    events = [_snapshot("2023-12-31", 100.0, avg_cost=250.0),
              _trade("2026-07-28", "SELL", 100.0, 197.0)]
    held = lg.derive_holdings(events, splits=SPLITS)["holdings"]["NVDA"]
    assert held["shares"] == 900.0, held
    assert held["avg_cost"] == 25.0, held           # 250 / 10, not 250
    assert held["cost_total"] == 22500.0, held


def test_an_absent_split_map_reproduces_the_pre_558_answer():
    """`None` means no split information, not "no splits". Every caller that
    has nothing to supply must get exactly what it got before this change."""
    events = [_trade("2023-01-10", "BUY", 100.0, 150.0),
              _trade("2026-07-28", "SELL", 40.0, 197.0)]
    assert _shares(events, splits=None) == 60.0
    assert lg.derive_holdings(events)["holdings"]["NVDA"]["shares"] == 60.0


# ───────────────── what the user is actually asked ─────────────────

def test_a_split_position_is_not_presented_as_one_that_appeared_from_nowhere():
    """The product symptom. Before #558 the refresh lane asked a user who had
    held NVDA for years either to confirm a `large_change` or — once a trade
    landed after the split — to account for an `appearance`, a position it
    believed had materialised from nothing. Neither option set contained the
    true answer, and the appearance path went on to ask how long they had held
    something the ledger already knew."""
    for events, declared in (([_snapshot("2023-12-31", 100.0)], 1000.0),
                             ([_snapshot("2023-12-31", 100.0),
                               _trade("2026-07-28", "SELL", 100.0, 197.0)], 900.0)):
        snapshot, anchor = snapshot_adapter.normalize_book(
            {"as_of": "2026-07-29",
             "positions": [{"ticker": "NVDA", "shares": declared, "avg_cost": 25.0,
                            "market": "US", "currency": "USD"}]}, today="2026-07-29")
        blind = br.plan_refresh(events, snapshot, anchor)
        assert blind["pending_confirmations"], "the split-blind lane did raise something"
        adjusted = br.plan_refresh(events, snapshot, anchor, splits=SPLITS)
        assert not adjusted["pending_confirmations"], adjusted["pending_confirmations"]


# ──────────────────── #559: the inverted return ────────────────────

def test_a_pre_split_exit_is_not_compared_against_a_post_split_quote():
    """#559. Sold at 950 before a ten-for-one, quoted at 197 today: the raw
    comparison reports -79% for a position that actually doubled. The sign
    inverts, so the card would say "you sold before it fell" about one of the
    user's best decisions."""
    item = {"ticker": "NVDA", "exit_date": "2024-05-20", "exit_price": 950.0,
            "shares_sold": 20.0, "kind": "reduce"}
    assert rv.compare(item, {"NVDA": 197.0})["orig_ret"] < 0, "the pre-#559 defect"
    fixed = rv.compare(item, {"NVDA": 197.0}, splits=SPLITS)["orig_ret"]
    assert abs(fixed - 1.0737) < 1e-3, fixed


def test_an_exit_after_the_split_is_left_alone():
    """Only splits *after* the exit rebase it. An exit already quoted in
    today's basis must not be scaled a second time."""
    item = {"ticker": "NVDA", "exit_date": "2026-07-28", "exit_price": 197.0,
            "shares_sold": 100.0, "kind": "reduce"}
    assert rv.rebased_exit_price(item, SPLITS) == 197.0


def test_an_unpriced_exit_stays_unpriced():
    """A confirmed disappearance (#485 Slice C) has no execution price. It is
    not a missing quote and must not become a number."""
    assert rv.rebased_exit_price(
        {"ticker": "NVDA", "exit_date": "2024-05-20", "exit_price": None}, SPLITS) is None


def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except Exception as e:          # not AssertionError alone: a mutation
            failed += 1                 # that crashes must read as red, not as
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
