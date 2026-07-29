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
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE)
import book_refresh as br  # noqa: E402
import ledger as lg  # noqa: E402
import portfolio_basis as pb  # noqa: E402
import review  # noqa: E402
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


def test_a_confirmed_disappearance_leaves_on_the_basis_the_book_recorded():
    """`detect_exits` returns rows from two code paths — the trade walk, and
    `absence_exits` for a disappearance confirmed without a fill (#485 Slice
    C). Both must be on one basis, or that single list is #550's own defect
    one level down: the book says 1,000 shares left and the exit row beside
    it says 100. `absence_exits` reads the prior book, so it needs the same
    map the trade walk gets; it shipped without it (#558 follow-up).

    The basis is the absence's own date, matching the trade lane — so
    `shares_sold` is the count the position actually stood at when it left,
    and `revisit_id`, which embeds it, does not churn when a later split
    arrives.
    """
    events = [_trade("2023-01-10", "BUY", 100.0, 150.0)]
    held = lg.derive_holdings(events, splits=SPLITS)["holdings"]["NVDA"]
    assert held["shares"] == 1000.0, held["shares"]        # the split is behind us
    events.append(lg.build_position_absence(
        date="2026-07-28", ticker="NVDA", cycle_id=held["cycle_id"]))

    rows = rv.detect_exits(events, splits=SPLITS)
    assert len(rows) == 1, rows                            # no trade exit in this history
    assert rows[0]["exit_price"] is None, rows[0]          # it is the absence row
    assert rows[0]["shares_sold"] == held["shares"], rows[0]
    assert rows[0]["shares_before"] == held["shares"], rows[0]


def test_an_unpriced_exit_stays_unpriced():
    """A confirmed disappearance (#485 Slice C) has no execution price. It is
    not a missing quote and must not become a number."""
    assert rv.rebased_exit_price(
        {"ticker": "NVDA", "exit_date": "2024-05-20", "exit_price": None}, SPLITS) is None


# ─────────── the review lane prices the book it is going to report ───────────

# #550's own repro ledger: 90 + 30 bought and 20 sold before the split, 100
# sold after. Raw arithmetic reaches exactly zero, so a split-blind reader
# reports no position at all — the sharpest shape for a ticker-discovery bug.
_CROSSING = [_trade("2023-01-10", "BUY", 90.0, 150.0),
             _trade("2023-11-15", "BUY", 30.0, 480.0),
             _trade("2024-05-20", "SELL", 20.0, 950.0),
             _trade("2026-07-28", "SELL", 100.0, 197.0)]


def _engine_frame(as_of="2026-07-28", priced=("NVDA",)):
    """A complete engine-side price frame, before review narrows it."""
    return {"contract_version": pb.VALUATION_FRAME_VERSION, "as_of": as_of,
            "aggregate_currency": "USD",
            "prices": {ticker: {"price": 197.0, "currency": "USD", "provenance": "test"}
                       for ticker in priced},
            "fx_to_aggregate": {"USD": {"rate": 1.0, "provenance": "identity", "as_of": as_of}},
            "coverage": {"missing_price": [], "missing_fx": []}, "usable": True, "reason": None}


def _review_basis(events, state):
    inputs = {"ledger_events": list(events), "candidate": {"files": []},
              "ledger_receipt": {"skipped_lines": 0}}
    return review._virtual_review_basis(inputs, [], state)[1]


def test_the_review_frame_is_narrowed_from_the_book_the_basis_will_report():
    """`prepare` reads the book twice: once to learn which tickers need a
    price, and once — canonically, split-aware — to build the basis it
    validates that frame against. Split-blind on the first read, a position
    whose raw quantities reach zero is dropped from the frame and is not even
    listed as `missing_price`; the canonical read then restores it and refuses
    with "prices do not exactly partition holdings".

    So the failure is not a wrong number on a card. It is `review prepare`
    declining to run at all on the same split-crossing book `derive_holdings`,
    `refresh` and `consider` were fixed to read correctly (#558 follow-up) —
    and the finalize-side re-verification narrows through the same function, so
    a review that somehow started could not have been saved either.
    """
    receipt = _review_basis(_CROSSING, {"valuation_frame": _engine_frame(), "splits": SPLITS})

    frame = receipt["valuation_frame"]
    assert "NVDA" in frame["prices"], frame                    # discovery kept the position
    assert frame["coverage"]["missing_price"] == [], frame     # and did not merely excuse it
    holdings = receipt["basis"]["current_book"]["holdings"]
    assert holdings["NVDA"]["shares"] == 900.0, holdings

    # The state version the review reports is the one a direct canonical query
    # produces from the same events — the acceptance criterion that review and
    # `consider` answer on one denominator, exercised rather than asserted.
    direct = pb.query_current_book(
        _CROSSING, valuation_manifest=frame, skipped_lines=0, splits=SPLITS,
        reference_as_of=review._basis_reference(
            frame["as_of"], pb.query_current_book(_CROSSING, skipped_lines=0, splits=SPLITS).as_of))
    assert receipt["basis_state_version"] == direct.state_version


def test_a_price_the_review_frame_really_lacks_is_still_reported_missing():
    """The counterweight. Telling the narrowing read about splits must not turn
    it into a read that accepts anything: a held position with no price still
    has to leave the frame unusable, or the fix would have bought its green by
    loosening the partition the previous test depends on."""
    receipt = _review_basis(_CROSSING, {"valuation_frame": _engine_frame(priced=()),
                                        "splits": SPLITS})
    frame = receipt["valuation_frame"]
    assert frame["coverage"]["missing_price"] == [{"ticker": "NVDA", "currency": "USD"}], frame
    assert frame["usable"] is False and frame["reason"] == "missing_price", frame


def test_a_review_with_no_split_map_still_reads_its_own_book():
    """`splits=None` is what an offline or never-fetched review carries, and it
    must go on working: unadjusted, consistently, on both reads. The frame is
    narrowed from the same book the basis reports, whatever basis that is."""
    events = [_trade("2023-01-10", "BUY", 100.0, 150.0),
              _trade("2026-07-28", "SELL", 40.0, 197.0)]
    receipt = _review_basis(events, {"valuation_frame": _engine_frame()})
    assert "NVDA" in receipt["valuation_frame"]["prices"]
    assert receipt["basis"]["current_book"]["holdings"]["NVDA"]["shares"] == 60.0


# ─────────────── every reader of the book is told about splits ───────────────

# Where `splits` sits in each signature, so a positional call counts as passing
# it and only a genuinely split-blind call is reported. `None` means the
# parameter is keyword-only, so no argument count can satisfy it.
#
# The wrapper readers are here for the reason the whole net exists. Watching
# only the two `ledger` functions made a split-blind `query_current_book` call
# invisible — it reads the book through `derive_holdings`, which passes its own
# map faithfully, so the audit saw a compliant call and the caller one level up
# went unexamined for a release. That caller was `review`'s provisional frame
# narrowing, and it made `prepare` refuse a split-crossing book (#558
# follow-up). A reader of the book is anything that returns one, not only the
# function that sums the quantities.
_BOOK_READERS = {"derive_holdings": 1, "holdings_as_of": 2,
                 "query_current_book": None, "query_current_book_from_ledger": None}

# (module, enclosing function) -> why this reader legitimately has no map.
# Keyed by name rather than line so it survives edits above it, and every entry
# is checked to still match a real call below: an exemption that outlives its
# call site is how an allowlist quietly becomes a licence.
_NO_MAP_NEEDED = {
    ("snapshot_adapter.py", "_state_positions"):
        "derives from a one-element list holding a single anchor row. There are "
        "no trades to replay, so no quantity is ever on two bases.",
    ("ledger.py", "main"):
        "the standalone diagnostic CLI. It has no review to read a stamped map "
        "from, which is why it has none, and SKILL.md rules 2 and 7 forbid an "
        "agent calling it — the stale basis reaches a maintainer, never a user.",
}


def _book_reader_calls():
    """Yield (module, enclosing function, lineno, callee, passes_splits)."""
    for mod in sorted(os.listdir(ENGINE)):
        if not mod.endswith(".py") or mod.startswith("test_"):
            continue
        path = os.path.join(ENGINE, mod)
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name not in _BOOK_READERS:
                continue
            position = _BOOK_READERS[name]
            passes = (any(k.arg == "splits" for k in node.keywords)
                      or (position is not None and len(node.args) > position))
            enclosing, cur = "<module>", node
            while cur in parents:
                cur = parents[cur]
                if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    enclosing = cur.name
                    break
            yield mod, enclosing, node.lineno, name, passes


def test_every_reader_of_the_book_is_told_what_a_split_did_to_it():
    """A caller that omits `splits` gets raw as-transacted quantities and no
    integrity issue — the failure is silent by construction, so nothing but a
    check like this one notices a reader that forgot. `absence_exits` was the
    first to forget and it took a hand audit of all fourteen call sites to
    find; this is what makes the second one cost nothing.

    Scope is honest about what it buys. It sees a *direct* call missing the
    argument, so it catches a reader that never asks for the map. It does not
    catch a caller that holds a map and fails to forward it to a helper whose
    own read is correctly parameterised — the other half of the `absence_exits`
    defect — because that call site looks identical either way. The behavioural
    test above is what covers that half; this is the net for the class.
    """
    missing = [f"{mod}:{line} {enclosing}() calls {callee}() with no split map"
               for mod, enclosing, line, callee, passes in _book_reader_calls()
               if not passes and (mod, enclosing) not in _NO_MAP_NEEDED]
    assert not missing, (
        "these read the recorded book without being told what a split did to it;\n"
        "pass `splits`, or add a reasoned entry to _NO_MAP_NEEDED:\n  "
        + "\n  ".join(missing))


def test_no_split_map_exemption_outlives_its_call_site():
    """An entry in _NO_MAP_NEEDED that matches nothing is an exemption still
    granting permission to a caller that has moved, been renamed, or started
    passing the map. Then the next reader to appear under that name inherits a
    licence nobody decided to give it."""
    live = {(mod, enclosing)
            for mod, enclosing, _, _, passes in _book_reader_calls() if not passes}
    stale = sorted(set(_NO_MAP_NEEDED) - live)
    assert not stale, (
        "these exemptions no longer match a split-blind call and must be removed: "
        + ", ".join(f"{m}:{f}()" for m, f in stale))


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
