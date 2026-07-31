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
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE)

# The market must not be an input to these assertions (#620). Declared in
# tests/offline_posture.py so a direct `python3 tests/<this file>` run and a
# `run_all.py` run reach the same answer; TR_TEST_NETWORK=1 still opts in.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_posture  # noqa: E402
offline_posture.apply()
import book_refresh as br  # noqa: E402
import ledger as lg  # noqa: E402
import portfolio_basis as pb  # noqa: E402
import price_feed as pf_module  # noqa: E402
import review  # noqa: E402
import revisit as rv  # noqa: E402
import snapshot_adapter  # noqa: E402
import splits as split_policy  # noqa: E402
import trade_recap  # noqa: E402

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


# ─────────── the map has to arrive, not merely be passed ───────────
#
# `test_every_reader_of_the_book_is_told_what_a_split_did_to_it` above is an
# AST check: it proves every reader is *handed* a `splits` argument. It
# cannot see what that argument is worth. `review._recorded_splits` is the
# supplier for every lane with no review of its own — `consider`, `refresh`,
# and the review's own exit capture — and gutting it to `return None` leaves
# this file, `test_consider.py`, `test_book_refresh.py` and
# `test_review_v2.py` all green while every split-crossing position in the
# product silently reverts to raw quantities.
#
# That is #558's own defect one level up: an argument proven present, and a
# value proven by nothing. #558's last acceptance line ("review and
# context-bearing/context-free `consider` read the same split-adjusted
# `state_version` and denominator") is what this closes, and it needs the
# real CLI, because the gap is entirely in the wiring between them.

def _reviewed_root(tmp, splits):
    """A root holding a position declared before a split, with the map a
    finished review would have frozen into last_state.json. `splits=None` is
    the never-reviewed root, which review._recorded_splits documents as
    degrading to unadjusted."""
    with open(os.path.join(tmp, "ledger.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(_snapshot("2024-06-01", 10, avg_cost=1000.0)) + "\n")
    if splits is not None:
        with open(os.path.join(tmp, "last_state.json"), "w", encoding="utf-8") as f:
            json.dump({"splits": splits}, f)
    return tmp


def _considered_book(tmp):
    """`consider`'s own view of the recorded book, through the CLI -- the
    boundary an agent actually calls (SKILL.md rule 2)."""
    run = subprocess.run(
        [sys.executable, os.path.join(ENGINE, "review.py"), "consider", "--root", tmp,
         "--premise", '{"ticker": "CASHY", "side": "buy", "qty": 1, "price": 1.0, '
                      '"currency": "USD"}'],
        capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, f"consider failed: {run.stdout}{run.stderr}"
    return json.loads(run.stdout)["evaluation"]


def test_the_frozen_split_map_actually_reaches_consider():
    """Ten shares declared before NVDA's ten-for-one, with the map a review
    froze. The answer must be built on a hundred."""
    with tempfile.TemporaryDirectory() as tmp:
        row = _considered_book(_reviewed_root(tmp, {"NVDA": [["2024-06-10", 10.0]]}))
        held = row["consequence"]["before"]["held"]["NVDA"]
        assert held["shares"] == 100.0, (
            f"consider answered on {held['shares']} shares; the review froze a ten-for-one "
            "and _recorded_splits is what has to carry it into this lane")
        assert held["cost"] == 10000.0, (
            "a split is a zero-dollar event: scaling cost alongside shares would move "
            "avg_cost and every weight derived from it")


def test_the_split_map_changes_the_books_own_identity():
    """The counterweight, and the half #558's acceptance line names. Two
    roots identical but for the frozen map must not answer under the same
    `state_version`: a book on a different share basis IS a different book,
    and an identity that could not tell them apart would let a stale
    evaluation reconcile against the wrong one."""
    with tempfile.TemporaryDirectory() as adjusted, tempfile.TemporaryDirectory() as raw:
        with_map = _considered_book(_reviewed_root(adjusted, {"NVDA": [["2024-06-10", 10.0]]}))
        without = _considered_book(_reviewed_root(raw, None))
        assert without["consequence"]["before"]["held"]["NVDA"]["shares"] == 10.0, (
            "a root that never ran a review carries no map and degrades to unadjusted "
            "(review._recorded_splits); if this changed, the test above proves nothing")
        assert with_map["basis"]["state_version"] != without["basis"]["state_version"]


# ───────── every route that reads the book, on one crossing book ─────────
#
# Owner ruling 2026-07-30, after three sessions each escaped
# `test_every_reader_of_the_book_is_told_what_a_split_did_to_it` for a
# different structural reason (#572: a caller one level above a watched
# callee; #577: a route that calls no watched callee at all, summing raw
# `trade_recap.load()` rows itself; #576: a supplier that returns nothing).
# The common shape is that the check audits the *call graph*, and all three
# escapes were things a call graph does not model.
#
# So this section audits *routes* instead. One book, one split, no trading
# since — the commonest shape in the wild — driven through every subcommand
# that reads the recorded book, asserting each route's own honest observable.
#
# The route list is derived from `review.py` itself, not hand-written, which
# is the part that makes this durable: a new subcommand that reads the book
# and is not classified here fails the suite. A hand list would have exactly
# the weakness that let three escapes through.

_BOOK_READERS_INTERNAL = frozenset({
    "derive_holdings", "holdings_as_of", "query_current_book",
    "query_current_book_from_ledger", "_rows_from_ledger", "_consider_rows",
    "load_ledger", "detect_exits", "plan_refresh", "_virtual_review_basis",
    "_virtual_valuation_frame", "build_derived_book",
})

# Routes driven below. Each name is a `cmd_<name>` in review.py.
_DRIVEN_ROUTES = ("consider", "prepare", "refresh", "positions", "record-rationale")

# A route may sit here only with a reason naming who owns it instead.
_ROUTES_NOT_DRIVEN = {
    # `add-cash` (#357) performs no book read of its own: it re-enters
    # `_prepare_session` — the `prepare` route driven above — with one extra
    # input, and then refuses unless the recomputed `engine_state` (holdings,
    # splits, valuation frame: everything but `cash`) is byte-identical to the
    # session it is amending. Whatever `prepare` establishes about the split
    # basis, this route reproduces exactly or does not answer at all, so there
    # is no second basis for it to get wrong. This is a mechanism, not a
    # call-graph argument: it is pinned by
    # tests/test_review_v2.py::test_add_cash_refuses_when_more_than_the_anchor_moved.
    "add-cash": "delegates to prepare and refuses any engine_state drift outside cash",
    # `finalize` (#403) reaches the book through exactly one call,
    # `_resolve_rationale_subject`, and only to answer the question
    # `record-rationale` — driven above — asks of it: is this ticker an open
    # position, and which cycle. It reads no share count, no cost and no weight,
    # so no number on the card or in the bundle moves with the split basis. And
    # the one thing that could go wrong there cannot reach the review: a book
    # that failed to resolve the subject is receipted as `rationale_error`
    # beside a card that still commits, per the owner's 2026-07-31 ruling that
    # a rationale and its neighbours are independent outcomes with separate
    # receipts. This is a mechanism, not a call-graph argument: it is pinned by
    # tests/test_rationale_review.py::test_a_rationale_that_cannot_be_recorded_does_not_fail_the_review.
    "finalize": "reads the book only to resolve a rationale subject; failure is receipted, not fatal",
}


def _routes_that_read_the_book():
    """Which `cmd_*` entry points reach a book reader, from review.py's own
    call graph. Shallow-recursive through review's module-level helpers,
    which is how `cmd_prepare` reaches `derive_holdings` several frames
    down."""
    tree = ast.parse(open(os.path.join(ENGINE, "review.py"), encoding="utf-8").read())
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    def reaches(name, seen, depth):
        if name in seen or depth > 3 or name not in funcs:
            return False
        seen.add(name)
        for node in ast.walk(funcs[name]):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            callee = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if callee in _BOOK_READERS_INTERNAL:
                return True
            if callee in funcs and reaches(callee, seen, depth + 1):
                return True
        return False

    return {name[len("cmd_"):].replace("_", "-") for name in funcs
            if name.startswith("cmd_") and reaches(name, set(), 0)}


def test_every_route_that_reads_the_book_is_classified():
    """The durable half. `render`, `preview`, `set-cap` and the rest touch no
    book and owe nothing; the three that do are named above. A new
    subcommand that reads the book arrives here as a failure rather than as
    an untested route."""
    found = _routes_that_read_the_book()
    classified = set(_DRIVEN_ROUTES) | set(_ROUTES_NOT_DRIVEN)
    assert found <= classified, (
        "these review.py routes read the recorded book and no split-basis route test "
        f"covers them: {sorted(found - classified)}. Drive it below, or add a reasoned "
        "entry to _ROUTES_NOT_DRIVEN naming who does.")
    assert classified <= found | {"consider"}, (
        f"classified routes that no longer read the book: {sorted(classified - found)}")


# One position declared before NVDA's real ten-for-one and untouched since,
# plus the map a finished review froze. The broker now shows the post-split
# count, which is the same position — not a purchase.
_CROSSING_LEDGER = {"type": "snapshot", "as_of": "2024-06-01", "source": "declared_book",
                    "positions": [{"ticker": "NVDA", "shares": 10, "avg_cost": 1000.0,
                                   "market": "US", "currency": "USD"}]}
_BROKER_VIEW = {"as_of": "2026-07-29",
                "positions": [{"ticker": "NVDA", "shares": 100, "avg_cost": 100.0,
                               "market": "US", "currency": "USD"}]}


def _crossing_root(tmp, with_map=True):
    with open(os.path.join(tmp, "ledger.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(_CROSSING_LEDGER) + "\n")
    if with_map:
        with open(os.path.join(tmp, "last_state.json"), "w", encoding="utf-8") as f:
            json.dump({"splits": {"NVDA": [["2024-06-10", 10.0]]}}, f)
    snapshot = os.path.join(tmp, "broker.json")
    with open(snapshot, "w", encoding="utf-8") as f:
        json.dump(_BROKER_VIEW, f)
    return snapshot


def _crossing_csv(tmp):
    """The same position as `_CROSSING_LEDGER`, handed over as transactions
    instead — the other book `consider` knows how to build."""
    path = os.path.join(tmp, "transactions.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Symbol", "Quantity", "Price", "Action", "TradeDate", "RecordType"])
        writer.writerow(["NVDA", 10, 1000.0, "BUY", "2024-06-01", "Trade"])
    return path


def _route(tmp, *args):
    env = dict(os.environ, TRADE_COACH_HOME=tmp)
    run = subprocess.run([sys.executable, os.path.join(ENGINE, "review.py"), *args,
                          "--root", tmp],
                         capture_output=True, text=True, env=env, timeout=180)
    return run.returncode, json.loads(run.stdout) if run.stdout.strip() else {}


def test_refresh_does_not_invent_a_change_a_split_explains():
    """`refresh`'s honest observable is not a share count — it is whether the
    book reconciles. Ten pre-split shares and a broker showing a hundred are
    the same position, so nothing changed and nothing needs confirming.

    Split-blind, this route tells the user their holding went from 10 to 100
    and their average cost from 1,000 to 100, and **raises a confirmation**:
    it spends a turn of their attention asking them to settle a ninety-share
    purchase that never happened."""
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = _crossing_root(tmp)
        code, payload = _route(tmp, "refresh", "--snapshot-json", snapshot)
        assert code == 0, payload
        assert payload["summary"]["status"] == "reconciled", payload["summary"]
        assert payload["diff"]["positions"] == [], payload["diff"]["positions"]
        assert payload["pending_confirmations"] == [], (
            "a split with no trading since must raise nothing to confirm")


_PREMISE = ('{"ticker": "NVDA", "side": "buy", "qty": 1, "price": 100.0, '
            '"currency": "USD"}')


def test_consider_answers_on_the_split_adjusted_book_on_both_of_its_routes():
    """`consider`'s observable is the share count itself, and it has two ways
    of building a book: the recorded ledger, and a CSV handed over on the
    spot. Both are asserted, because they are different implementations that
    happen to owe the same answer — the CSV route summed raw
    `trade_recap.load()` rows and was split-blind until #577, and a route
    test that covered only the ledger side is exactly how that survived
    #550, #558 and #572.

    The two are built differently on purpose (the ledger route carries the
    running position across each split inside the canonical book; the CSV
    route rebases the rows themselves onto today, because `last_px` is a
    current quote). Same book, same split, same count."""
    for label, extra in (("ledger", ()), ("csv", None)):
        with tempfile.TemporaryDirectory() as tmp:
            _crossing_root(tmp)
            if extra is None:
                extra = (_crossing_csv(tmp),)
            code, payload = _route(tmp, "consider", *extra, "--premise", _PREMISE)
            assert code == 0, (label, payload)
            held = payload["evaluation"]["consequence"]["before"]["held"]["NVDA"]
            assert held["shares"] == 100.0, (label, held)
            assert held["cost"] == 10000.0, (label, "a split is a zero-dollar event", held)


def test_positions_answers_on_the_split_adjusted_book():
    """`positions`'s (#561) honest observable is the share count itself.
    `positions` reads shares/cost/weight through the same canonical
    `portfolio_basis.query_current_book` reader `consider`'s ledger route
    uses (owner ruling 2026-07-30, after this PR's FIFO-reconstruction cut
    was found to disagree with `consider` on a multi-lot partial sell) --
    driven separately here because it is still a distinct CLI route with
    its own path to that reader in review.py's call graph, not a wrapper
    around an existing driven route, and the split map has to reach it
    through this call site specifically."""
    with tempfile.TemporaryDirectory() as tmp:
        _crossing_root(tmp)
        code, payload = _route(tmp, "positions")
        assert code == 0, payload
        rows = {row["ticker"]: row for row in payload["positions"]}
        assert rows["NVDA"]["shares"] == 100.0, rows["NVDA"]
        assert rows["NVDA"]["cost_total"] == 10000.0, (
            "a split is a zero-dollar event", rows["NVDA"])


def test_record_rationale_finds_the_position_on_a_split_crossing_book():
    """`record-rationale` (#403) reads the book to answer one question -- is this
    a position you hold, and which cycle is it -- and that answer is what decides
    whether the user's words are recorded at all.

    The observable is therefore the refusal, not a number. Split-blind, the
    share count is wrong in a way that can read as a position no longer held,
    and the route then refuses to record a reason for something the user does
    own: their words are turned away on the strength of an arithmetic error.
    That is worse than a wrong figure, because nothing about the message tells
    the user the book was the problem."""
    with tempfile.TemporaryDirectory() as tmp:
        _crossing_root(tmp)
        code, payload = _route(tmp, "record-rationale", "--ticker", "NVDA",
                               "--statement", "still holding it through the split")
        assert code == 0, payload
        assert payload["status"] == "appended", payload
        assert payload["ticker"] == "NVDA"
        assert payload["effective_statement"] == "still holding it through the split"
        assert payload["cycle_id"], "the subject resolves to a real cycle, not a guess"


def test_a_review_can_start_at_all_on_a_split_crossing_book():
    """The compounding failure, and the reason a route-level check is worth
    having. `prepare` reads the same book through `plan_refresh`, so a
    phantom change does not merely produce a wrong number — #530's
    catch-up gate refuses the review outright and sends the user to settle a
    difference that does not exist. Split-aware, the review just runs."""
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = _crossing_root(tmp)
        code, payload = _route(tmp, "prepare", "--snapshot-json", snapshot)
        assert code == 0, payload
        assert "review_plan" in payload, sorted(payload)


def test_without_the_map_every_route_degrades_together():
    """The counterweight. Each assertion above must be failing for the split
    basis and not for some unrelated reason the fixture happens to satisfy,
    so the same book with no frozen map has to break all four in the ways
    named — otherwise these tests prove nothing about splits."""
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = _crossing_root(tmp, with_map=False)
        _code, refresh = _route(tmp, "refresh", "--snapshot-json", snapshot)
        assert refresh["summary"]["status"] != "reconciled"
        assert [row for row in refresh["diff"]["positions"] if row["kind"] == "shares"], (
            "split-blind, refresh must be reporting the phantom share change")

        _code, considered = _route(
            tmp, "consider", "--premise",
            '{"ticker": "NVDA", "side": "buy", "qty": 1, "price": 100.0, "currency": "USD"}')
        assert considered["evaluation"]["consequence"]["before"]["held"]["NVDA"]["shares"] == 10.0

        code, _prepared = _route(tmp, "prepare", "--snapshot-json", snapshot)
        assert code != 0, "split-blind, the catch-up gate must be refusing the review"

        _code, positions = _route(tmp, "positions")
        rows = {row["ticker"]: row for row in positions["positions"]}
        assert rows["NVDA"]["shares"] == 10.0, (
            "split-blind, positions must be reporting the pre-split count", rows["NVDA"])


# ───────── the other operand: what basis the price is on (#583) ─────────
#
# Everything above this line is about quantities. A quantity is half of every
# number the product states; the price it is multiplied by is an observation
# with a date, and therefore a split basis, of its own. #558's sweep audited one
# dimension and reported itself complete — true, and true only of quantities.
#
# The book below is the same shape as `_CROSSING_LEDGER`'s: bought before
# GLYPH's ten-for-one and untouched since, so the share count is ten times what
# was transacted. Beside it sits a position the split never touched, and the two
# are sized so that the aligned answer and the split-blind one disagree about
# *which position is the largest* — not merely by how much.

_PRICE_SPLIT = ("2024-06-10", 10.0)
_PRICE_BOOK = ({"ticker": "GLYPH", "shares": 10, "avg_cost": 900.0,
                "market": "US", "currency": "USD"},
               {"ticker": "TARDY", "shares": 300, "avg_cost": 30.0,
                "market": "US", "currency": "USD"})


def _price_envelope(tmp, declare_split=True, name="prices.json"):
    """A supplied envelope holding one raw pre-split observation.

    GLYPH last printed 100 on 2024-06-07, three days before its ten-for-one.
    That is the number the source shows and the number the agent must send;
    the engine is what turns it into the 10 a post-split share count can be
    multiplied by."""
    glyph = {"ticker": "GLYPH", "close": 100.0, "date": "2024-06-07", "currency": "USD"}
    if declare_split:
        glyph["splits"] = [list(_PRICE_SPLIT)]
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"as_of": "2024-06-10", "source": "Example Exchange official closes",
                   "prices": [glyph,
                              {"ticker": "TARDY", "close": 32.0, "date": "2024-06-10",
                               "currency": "USD"}]}, f)
    return path


def _price_root(tmp, with_map=False):
    """The recorded book, and optionally the map a finished review froze."""
    with open(os.path.join(tmp, "ledger.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "snapshot", "as_of": "2024-06-01",
                            "source": "declared_book",
                            "positions": [dict(row) for row in _PRICE_BOOK]}) + "\n")
    if with_map:
        with open(os.path.join(tmp, "last_state.json"), "w", encoding="utf-8") as f:
            json.dump({"splits": {"GLYPH": [list(_PRICE_SPLIT)]}}, f)


def _price_csv(tmp):
    """The same book handed over as transactions — `consider`'s other route."""
    path = os.path.join(tmp, "transactions.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Symbol", "Quantity", "Price", "Action", "TradeDate", "RecordType"])
        for row in _PRICE_BOOK:
            writer.writerow([row["ticker"], row["shares"], row["avg_cost"], "BUY",
                             "2024-06-01", "Trade"])
    return path


_PRICE_PREMISE = ('{"ticker": "TARDY", "side": "buy", "qty": 1, "price": 32.0, '
                  '"currency": "USD"}')


def test_a_pre_split_close_cannot_value_post_split_shares_on_either_consider_route():
    """#583 §1, on the routes an agent actually calls. GLYPH's ten pre-split
    shares are a hundred now, and the supplied close is 100 in the basis its own
    session printed. Multiplied raw that is a 10,000 position against TARDY's
    9,600, so the engine reports GLYPH as the book's largest holding at 51%.
    Rebased onto the share count's basis it is 1,000 — nine per cent of the
    book, and TARDY is the largest position, which it always was.

    So this is not a precision caveat. Every verdict built on the denominator
    inverts: which position is too heavy, whether the concentration rule is
    broken, and what one more purchase does to any of it."""
    for label, extra in (("ledger", ()), ("csv", None)):
        with tempfile.TemporaryDirectory() as tmp:
            _price_root(tmp)
            if extra is None:
                extra = (_price_csv(tmp),)
            code, payload = _route(tmp, "consider", *extra, "--premise", _PRICE_PREMISE,
                                   "--prices", _price_envelope(tmp))
            assert code == 0, (label, payload)
            before = payload["evaluation"]["consequence"]["before"]
            assert before["held"]["GLYPH"]["shares"] == 100.0, (label, before["held"])
            assert before["max_ticker"] == "TARDY", (
                label, "a pre-split close made the split-crossing position look like the "
                       "largest holding in the book", before["weights"])
            assert abs(before["weights"]["GLYPH"] - 1000.0 / 10600.0) < 1e-6, (
                label, "GLYPH must be weighed at the rebased close, not the raw one",
                before["weights"])


def test_a_recorded_split_survives_an_unrelated_tickers_supplied_one():
    """#583 post-merge finding: precedence is per ticker, never per call.

    The root has already recorded splits for two tickers. The envelope
    declares GLYPH's — its close predates it — and legitimately omits PAIR's,
    because PAIR's close post-dates its split and is already in the
    compatible basis (references/price-feed.md says exactly that). Whole-map
    replacement read that omission as "PAIR never split": the recorded 2:1
    vanished, both consider routes read PAIR at its raw pre-split count, and
    `basis_conflicts` could not object because the map it checks no longer
    carried PAIR at all. The share counts below are the whole assertion —
    GLYPH on its supplied events, PAIR on its recorded ones, on both routes.
    """
    book = ({"ticker": "GLYPH", "shares": 10, "avg_cost": 1000.0, "currency": "USD"},
            {"ticker": "PAIR", "shares": 10, "avg_cost": 100.0, "currency": "USD"})
    for label in ("ledger", "csv"):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "ledger.jsonl"), "w", encoding="utf-8") as f:
                f.write(json.dumps({"type": "snapshot", "as_of": "2024-05-01",
                                    "source": "declared_book",
                                    "positions": [dict(row) for row in book]}) + "\n")
            with open(os.path.join(tmp, "last_state.json"), "w", encoding="utf-8") as f:
                json.dump({"splits": {"GLYPH": [list(_PRICE_SPLIT)],
                                      "PAIR": [["2024-05-15", 2.0]]}}, f)
            envelope = os.path.join(tmp, "prices.json")
            with open(envelope, "w", encoding="utf-8") as f:
                json.dump({"as_of": "2024-06-10", "source": "Example Exchange official closes",
                           "prices": [{"ticker": "GLYPH", "close": 100.0, "date": "2024-06-07",
                                       "currency": "USD", "splits": [list(_PRICE_SPLIT)]},
                                      {"ticker": "PAIR", "close": 60.0, "date": "2024-06-10",
                                       "currency": "USD"}]}, f)
            extra = ()
            if label == "csv":
                path = os.path.join(tmp, "transactions.csv")
                with open(path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Symbol", "Quantity", "Price", "Action",
                                     "TradeDate", "RecordType"])
                    for row in book:
                        writer.writerow([row["ticker"], row["shares"], row["avg_cost"],
                                         "BUY", "2024-05-01", "Trade"])
                extra = (path,)
            code, payload = _route(tmp, "consider", *extra,
                                   "--premise", _PRICE_PREMISE_PAIR,
                                   "--prices", envelope)
            assert code == 0, (label, payload)
            held = payload["evaluation"]["consequence"]["before"]["held"]
            assert held["GLYPH"]["shares"] == 100.0, (
                label, "the supplied split must still govern its own ticker", held)
            assert held["PAIR"]["shares"] == 20.0, (
                label, "an unrelated ticker's supplied split must not erase "
                       "PAIR's recorded one", held)


_PRICE_PREMISE_PAIR = ('{"ticker": "PAIR", "side": "buy", "qty": 1, "price": 60.0, '
                       '"currency": "USD"}')


def test_a_review_records_its_derived_book_on_a_split_crossing_history():
    """Found by driving the real CLI rather than by reading it. `prepare` writes
    down the book a transaction import derived (#549), and that writer passes its
    `as_of` to `derive_holdings` as an ISO string while `holdings_as_of` passes a
    date — so the tail rebase compared a `datetime.date` against a `str` and
    raised `TypeError`. Any history with a split took the whole review down with
    a traceback, which is not the fail-closed refusal `splits.py` promises, and
    the offline suite was green because the only split-crossing `prepare` test
    used a snapshot root that never reaches this writer.

    The fix is where the date semantics live: every factor coerces its window
    edges, so one reader decides what a date is."""
    with tempfile.TemporaryDirectory() as tmp:
        code, payload = _route(tmp, "prepare", _price_csv(tmp), "--language", "en",
                               "--prices", _price_envelope(tmp))
        assert code == 0, payload
        assert "review_plan" in payload, sorted(payload)
        recorded = [json.loads(line) for line
                    in open(os.path.join(tmp, "ledger.jsonl"), encoding="utf-8")
                    if line.strip()]
        derived = [row for row in recorded
                   if row.get("type") == "snapshot" and row.get("source") == lg.DERIVED_BOOK_SOURCE]
        assert derived, [row.get("source") for row in recorded]
        row = derived[-1]
        # The row is dated, and `holdings_as_of` bounds the tail rebase to that
        # date: it states the book as its own day saw it, which for a day before
        # the split is the pre-split count. Reading the same events today gives
        # 100, and both are true statements — which is exactly why the recorded
        # row must round-trip against its own date rather than against today's.
        trades = [ev for ev in recorded if ev.get("type") == "trade"]
        assert {p["ticker"]: p["shares"] for p in row["positions"]} == {
            ticker: fact["shares"] for ticker, fact
            in lg.holdings_as_of(trades, row["as_of"],
                                 splits={"GLYPH": [list(_PRICE_SPLIT)]}).items()}, row


def test_the_two_consider_routes_agree_about_the_price_basis():
    """The acceptance line #583 shares with #558: one set of synthetic facts,
    two independently built books, one interpretation. They are computed
    differently on purpose — the ledger route carries the running position
    inside the canonical book, the CSV route rebases the rows themselves — so
    equal weights here are a result, not a tautology."""
    weights = {}
    for label, extra in (("ledger", ()), ("csv", None)):
        with tempfile.TemporaryDirectory() as tmp:
            _price_root(tmp)
            if extra is None:
                extra = (_price_csv(tmp),)
            _code, payload = _route(tmp, "consider", *extra, "--premise", _PRICE_PREMISE,
                                    "--prices", _price_envelope(tmp))
            weights[label] = payload["evaluation"]["consequence"]["before"]["weights"]
    assert weights["ledger"].keys() == weights["csv"].keys(), weights
    for ticker in weights["ledger"]:
        assert abs(weights["ledger"][ticker] - weights["csv"][ticker]) < 1e-9, (ticker, weights)


def test_consider_refuses_when_the_price_and_share_bases_cannot_be_reconciled():
    """The fail-closed half. An envelope that declares no split still lets the
    frozen map carry the share count across one — the shares move, the price
    does not, and neither side looks wrong on its own. There is no honest number
    to compute here: applying the split to the price would assert a corporate
    action the price source never confirmed, and ignoring it is the tenfold
    error above. So the route refuses, and says which ticker, which observation
    date, and which split."""
    with tempfile.TemporaryDirectory() as tmp:
        _price_root(tmp, with_map=True)
        code, payload = _route(tmp, "consider", "--premise", _PRICE_PREMISE,
                               "--prices", _price_envelope(tmp, declare_split=False))
        assert code != 0, payload
        assert payload.get("status") == "error", payload
        message = payload.get("error", "")
        assert "GLYPH" in message and "2024-06-07" in message and _PRICE_SPLIT[0] in message, message
        assert "different split bases" in message, message


def test_declaring_the_split_in_the_envelope_is_the_repair_the_refusal_names():
    """The counterweight to the refusal, and the reason it is actionable rather
    than a dead end: the same root, the same frozen map, and the one change the
    message asks for makes the call succeed on the aligned number."""
    with tempfile.TemporaryDirectory() as tmp:
        _price_root(tmp, with_map=True)
        code, payload = _route(tmp, "consider", "--premise", _PRICE_PREMISE,
                               "--prices", _price_envelope(tmp, declare_split=True))
        assert code == 0, payload
        before = payload["evaluation"]["consequence"]["before"]
        assert before["max_ticker"] == "TARDY", before["weights"]


def test_a_supplied_series_is_rebased_before_a_return_consumer_reads_it():
    """#583's second acceptance line, through the production adapter pair.
    `price_feed.to_frame` is what `fetch_prices` returns for a supplied
    envelope, and `pnl_curve` is one of the consumers that differences it. A
    ten-for-one left un-rebased inside the series is a 90% one-day collapse the
    market never had, and the cumulative curve — the card's sparkline, and the
    same daily series beta, alpha and account-level return are regressed on —
    reads it as one."""
    days = [(dt.date(2024, 6, 3) + dt.timedelta(days=n)).isoformat() for n in range(10)]
    split_day = days[5]
    # A flat, boring instrument: 100 before its ten-for-one and 10 after, which
    # is the same company on the same day at the same market value.
    history = [[day, 100.0 if day < split_day else 10.0] for day in days]
    feed = pf_module.parse({"as_of": days[-1], "source": "Example Exchange",
                            "prices": [{"ticker": "GLYPH", "close": 10.0, "date": days[-1],
                                        "currency": "USD", "history": history,
                                        "splits": [[split_day, 10.0]]}]})
    frame, err = pf_module.to_frame(feed, ["GLYPH"])
    assert err is None, err
    series = frame["GLYPH"].tolist()
    worst = min(later / earlier - 1.0 for earlier, later in zip(series, series[1:]))
    assert worst > -0.5, (
        f"the supplied series still carries the split as a {worst:.0%} one-day move; "
        "every consumer that differences it reads that as the market", series)

    rows = [{"ticker": "GLYPH", "date": dt.date.fromisoformat(days[0]), "qty": 100.0,
             "price": 10.0, "side": "buy", "market": "US", "currency": "USD"}]
    curve = trade_recap.pnl_curve(rows, frame, market="US")
    assert "points" in curve, curve
    worst_point = max(abs(point["cum_ret"]) for point in curve["points"])
    assert worst_point < 0.05, (
        "a flat instrument that split must show a flat cumulative curve", curve)


# ───── per-ticker evidence: one fresh ticker cannot speak for a stale one ─────

def _engine_state(tmp, csv_path, prices_path):
    """The engine's own frozen state, through the same TR_STATE_OUT path
    `review._run_engine` uses — the artifact the review, the card and the exit
    comparison all read afterwards."""
    state_path = os.path.join(tmp, "state.json")
    env = dict(os.environ, TR_JSON="1", TR_STATE_OUT=state_path,
               TR_LEDGER=os.devnull, TR_PRICES=prices_path)
    run = subprocess.run([sys.executable, os.path.join(ENGINE, "trade_recap.py"), csv_path],
                         capture_output=True, text=True, env=env, cwd=ENGINE, timeout=180)
    assert run.returncode == 0, run.stderr[-800:]
    with open(state_path, encoding="utf-8") as f:
        return json.load(f)


def test_each_priced_ticker_keeps_its_own_observation_date_and_split_basis():
    """#583 §2. The frame carried one `as_of` and per-ticker
    `{price,currency,provenance}`, so a ticker whose last observation is days
    old was indistinguishable from a same-day one once frozen — and the frame
    could not testify at all about which split basis any of its numbers were in.

    GLYPH last printed on 2024-06-07 and TARDY on 2024-06-10. Both rows now say
    so, and both say which basis their value is stated in: GLYPH's is the split
    date, because the split is what moved it there."""
    with tempfile.TemporaryDirectory() as tmp:
        state = _engine_state(tmp, _price_csv(tmp), _price_envelope(tmp))
        frame = state["valuation_frame"]
        assert frame["prices"]["GLYPH"]["price"] == 10.0, frame["prices"]
        assert frame["prices"]["GLYPH"]["observed_at"] == "2024-06-07", frame["prices"]
        assert frame["prices"]["GLYPH"]["basis_date"] == _PRICE_SPLIT[0], frame["prices"]
        assert frame["prices"]["TARDY"]["observed_at"] == "2024-06-10", frame["prices"]
        assert frame["as_of"] == "2024-06-10", frame["as_of"]

        snapshot = state["price_snapshot"]
        assert snapshot["observations"]["GLYPH"] == {"observed_at": "2024-06-07",
                                                    "basis_date": _PRICE_SPLIT[0]}, snapshot
        assert snapshot["observations"]["TARDY"]["observed_at"] == "2024-06-10", snapshot


def test_a_frame_may_not_carry_that_evidence_for_only_some_of_its_tickers():
    """All-or-nothing within one frame. A frame stored before this evidence
    existed still validates untouched — that is the compatibility rule — but a
    half-stamped one is worse than either: the ticker without evidence reads as
    though it shared its priced neighbour's observation date, which is exactly
    the ambiguity the field was added to remove."""
    frame = _engine_frame()
    frame["prices"]["NVDA"] = dict(frame["prices"]["NVDA"], observed_at="2026-07-20",
                                   basis_date="2026-07-28")
    frame["prices"]["ACME"] = {"price": 12.0, "currency": "USD", "provenance": "test"}
    positions = {"NVDA": "USD", "ACME": "USD"}
    pb.validate_valuation_frame(_engine_frame(), positions={"NVDA": "USD"})  # legacy still valid
    try:
        pb.validate_valuation_frame(frame, positions=positions)
    except pb.PortfolioBasisError as exc:
        assert "partial" in str(exc), exc
    else:
        raise AssertionError("a half-stamped frame must not validate")


def test_a_price_basis_may_not_precede_its_own_observation():
    """The internal consistency of the pair. A value cannot be stated in a basis
    older than the session it was observed in; a frame claiming so is not
    describing a rebase that happened."""
    frame = _engine_frame()
    frame["prices"]["NVDA"] = dict(frame["prices"]["NVDA"], observed_at="2026-07-28",
                                   basis_date="2026-07-20")
    try:
        pb.validate_valuation_frame(frame, positions={"NVDA": "USD"})
    except pb.PortfolioBasisError as exc:
        assert "precedes its own observation" in str(exc), exc
    else:
        raise AssertionError("a basis before the observation must not validate")


# ───── the book states which basis its own quantities are on (#583 §3) ─────

def test_a_split_that_moved_a_held_quantity_is_part_of_the_books_own_date():
    """`PortfolioBasis` computed freshness from the anchor and the trades alone,
    so a book whose share count a split restated last month reported itself as
    of its last trade. A valuation dated in between then passed the
    `reference_as_of` gate — the basis object declaring itself fresh while its
    holdings had already moved into the later basis.

    A split is the day the share count changed, exactly as a trade is, so it
    belongs in that date."""
    events = [_snapshot("2023-12-31", 100.0)]
    blind = pb.query_current_book(events, skipped_lines=0)
    aware = pb.query_current_book(events, skipped_lines=0, splits=SPLITS)
    assert blind.as_of == "2023-12-31", blind.as_of
    assert aware.as_of == "2024-06-10", aware.as_of
    assert lg.derive_holdings(events, splits=SPLITS)["quantity_basis"] == "2024-06-10"
    # And the gate it exists for: a reference date after the last trade but
    # before the split can no longer present itself as the newer of the two.
    try:
        pb.query_current_book(events, skipped_lines=0, splits=SPLITS,
                              reference_as_of="2024-06-05")
    except pb.PortfolioBasisError as exc:
        assert "cannot precede" in str(exc), exc
    else:
        raise AssertionError("a reference before the applied split must not pass")


def test_a_book_no_split_touched_keeps_the_exact_identity_it_had():
    """The compatibility half, and the reason the freshness date was the
    representation chosen over a new field. `derive_holdings` reports no
    quantity basis for a book no split moved, so nothing enters the date and
    nothing enters `state_version` — an existing user's stored basis is
    byte-identical, with no migration and nothing to roll back."""
    events = [_trade("2023-01-10", "BUY", 100.0, 150.0, ticker="ACME")]
    plain = pb.query_current_book(events, skipped_lines=0)
    with_map = pb.query_current_book(events, skipped_lines=0, splits=SPLITS)
    assert lg.derive_holdings(events, splits=SPLITS)["quantity_basis"] is None
    assert plain.as_of == with_map.as_of == "2023-01-10"
    assert plain.state_version == with_map.state_version


def test_a_frame_priced_before_an_applied_split_is_refused_not_multiplied():
    """The fail-closed gate on the one surface that holds both operands. The
    frame says GLYPH was observed on 2026-07-20 and is stated in that day's
    basis; the book says it carried GLYPH across a split after that. The two
    numbers are not comparable, and the product of them is wrong by the split
    ratio while carrying a valid `state_version` and provenance."""
    events = [_snapshot("2020-01-01", 100.0)]
    stale = _engine_frame(as_of="2026-07-20")
    stale["prices"]["NVDA"] = dict(stale["prices"]["NVDA"], observed_at="2026-07-20",
                                   basis_date="2026-07-20")
    late = {"NVDA": [["2026-07-25", 4.0]]}
    try:
        pb.query_current_book(events, skipped_lines=0, splits=late, valuation_manifest=stale,
                              reference_as_of="2026-07-29")
    except pb.PortfolioBasisError as exc:
        assert "share basis" in str(exc) and "2026-07-25" in str(exc), exc
    else:
        raise AssertionError("a frame on an earlier share basis must not price this book")
    # The counterweight: the same frame, rebased across that split, prices it.
    aligned = _engine_frame(as_of="2026-07-20")
    aligned["prices"]["NVDA"] = dict(aligned["prices"]["NVDA"], observed_at="2026-07-20",
                                     basis_date="2026-07-25")
    assert pb.query_current_book(events, skipped_lines=0, splits=late,
                                 valuation_manifest=aligned,
                                 reference_as_of="2026-07-29") is not None


# ───── the exit comparison knows what basis its quote is on (#583 §4) ─────

def test_an_exit_is_rebased_only_across_the_splits_its_quote_postdates():
    """`compare` divided the exit price by every split after the exit and simply
    assumed the quote it was comparing against postdated all of them. True
    whenever the quote really is current — and unprovable, which was the
    objection: `_prepare_exit_capture` dropped the price snapshot's dates on the
    way in, so nothing in the comparison could tell.

    Sold at 950 on 2024-05-20, quoted at 900 on 2024-06-05 — five days before
    the ten-for-one. In the quote's own basis that is a small loss avoided.
    Rebased across a split the quote predates, the same pair reads as +847%."""
    item = {"ticker": "NVDA", "exit_date": "2024-05-20", "exit_price": 950.0,
            "shares_sold": 20.0, "kind": "reduce"}
    bounded = rv.compare(item, {"NVDA": 900.0}, splits=SPLITS,
                         price_basis={"NVDA": "2024-06-05"})["orig_ret"]
    assert abs(bounded - (900.0 / 950.0 - 1.0)) < 1e-6, bounded
    unbounded = rv.compare(item, {"NVDA": 900.0}, splits=SPLITS)["orig_ret"]
    assert unbounded > 8.0, ("without the quote's basis this is the pre-#583 reading", unbounded)
    # A quote that genuinely postdates the split gets the full rebase, which is
    # #559's answer and must not have changed.
    after = rv.compare(item, {"NVDA": 197.0}, splits=SPLITS,
                       price_basis={"NVDA": "2026-07-29"})["orig_ret"]
    assert abs(after - 1.0737) < 1e-3, after


def test_the_review_hands_the_exit_comparison_the_basis_it_froze():
    """The wiring, on the production path: `_prepare_exit_capture` builds the
    dict `compare` reads, and before #583 it kept only the prices out of
    `price_snapshot` and dropped every date beside them.

    The state here is assembled to make the two readings differ — a quote whose
    basis predates a split the map carries — because that is the only way to
    observe *which* basis the comparison used. What keeps the two agreeing in
    production is that one function, `trade_recap.price_observations`, states
    both the quote's basis and the map it was derived against."""
    state = {"date_end": "2024-09-30", "splits": SPLITS,
             "price_snapshot": {"as_of": "2024-06-05", "prices": {"NVDA": 900.0},
                                "observations": {"NVDA": {"observed_at": "2024-06-05",
                                                          "basis_date": "2024-06-05"}}}}
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "ledger.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps(_trade("2023-01-10", "BUY", 90.0, 150.0)) + "\n")
            # ≥50% of the pre-sale position, which is what `detect_exits`
            # records as a decision worth revisiting at all.
            f.write(json.dumps(_trade("2024-05-20", "SELL", 50.0, 950.0)) + "\n")
        _recent, _due, backlog, _meta = review._prepare_exit_capture(tmp, state, True)
    assert backlog and backlog["items"], backlog
    got = backlog["items"][0]["compare"]["orig_ret"]
    assert abs(got - (900.0 / 950.0 - 1.0)) < 1e-6, (
        "the frozen per-ticker basis did not reach the comparison", got, backlog)


# ───── the map must be complete for every origin it will be read from (#605) ─────
#
# Retrieval used to be unbounded: `Ticker(t).splits` returns AAPL back to 1987, so
# whatever a consumer's rebase origin was, the map covered it. One batched request
# with `actions=True` returns only the splits *inside its window*, which is a
# strictly cheaper way to be silently wrong — a missing split is a factor of 1.0,
# and every row above shows what a wrong factor does to a share count.
#
# What makes the window sufficient is stated in `market_data`: every consumer
# applies only splits strictly after a real book date. The dangerous one is the
# date that is *not* in the CSV — a review freezes the map, and `consider` and
# `refresh` later read it against the **ledger anchor**, which can predate every
# trade in the file the review was built from.

def test_the_request_window_covers_the_ledger_anchor_not_only_the_csv():
    """The cross-route hazard, and the reason `market_request` reads the ledger.

    Scoped to the CSV alone, the frozen map is complete for this review and short
    a split for the next `consider` on the same root — and neither side looks
    wrong, because the whole failure is an event that is simply absent.
    """
    import trade_recap as tr
    rows = [{"ticker": "NVDA", "date": dt.date(2026, 7, 1), "qty": 10.0,
             "price": 100.0, "market": "US", "currency": "USD"}]
    csv_only = tr.market_request(rows, "2026-07-30", None)
    widened = tr.market_request(rows, "2026-07-30", None, rebase_origin="2020-03-04")
    assert widened["window_start"] <= "2020-03-04", (
        "a rebase origin older than the CSV must widen the window, or the splits between the "
        f"anchor and the first trade are not in the response at all: {widened}")
    assert widened["rebase_origin"] <= "2020-03-04"
    assert csv_only["window_start"] > "2020-03-04", (
        "the CSV-only request is the narrow one this test exists to contrast with; if it "
        f"already reached back that far the assertion above proves nothing: {csv_only}")
    # And the widening is a widening, never a narrowing of the origin: narrowing
    # is the silent omission `market_data.build_request` refuses outright.
    import market_data
    for request in (csv_only, widened):
        assert request["rebase_origin"] >= request["window_start"], request
        market_data.build_request(**request)      # must not raise


def test_the_review_asks_the_ledger_for_that_origin_rather_than_assuming_one():
    """`_ledger_rebase_origin` is the supplier. A version that returned None
    would leave the window CSV-scoped with every gate above still green — the
    supply-side blind spot that shipped twice in this repository already."""
    import trade_recap as tr
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ledger.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "snapshot", "as_of": "2020-03-04", "source": "broker",
                "positions": [{"ticker": "NVDA", "shares": 10.0, "avg_cost": 5.0,
                               "currency": "USD", "market": "US"}]}) + "\n")
        saved = os.environ.get("TR_LEDGER")
        os.environ["TR_LEDGER"] = path
        try:
            got = tr._ledger_rebase_origin()
        finally:
            if saved is None:
                os.environ.pop("TR_LEDGER", None)
            else:
                os.environ["TR_LEDGER"] = saved
    assert got == "2020-03-04", (
        f"the recorded anchor is the oldest date a later consumer rebases from; got {got!r}")


def test_a_frozen_map_that_cannot_cover_the_anchor_refuses_instead_of_reconciling():
    """The residue of windowed retrieval, and it is a silent-wrong-number one.

    `refresh` and prepare's catch-up gate read the frozen map and may not
    re-resolve (#558: refresh is two CLI calls and a fetch between them could
    invalidate a `refresh_id` mid-answer). So a map whose window opens after the
    ledger anchor cannot say what happened to the share counts in between.

    Measured, not argued. 90 shares declared before a ten-for-one and never traded
    since reconcile cleanly against a post-split broker view of 900 when the map
    covers the split; one window short, `plan_refresh` returns a `large_change`
    asking the user to confirm going from 90 shares to 900 — and confirming it
    writes a wrong share count into the book.
    """
    import book_refresh as refresh_engine
    complete = {"NVDA": [["2024-06-10", 10.0]]}
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ledger.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "snapshot", "as_of": "2024-01-01", "source": "broker",
                "positions": [{"ticker": "NVDA", "shares": 90.0, "avg_cost": 40.0,
                               "currency": "USD", "market": "US"}]}) + "\n")
        events, _ = lg.load_ledger(path)
        snap = os.path.join(tmp, "snap.json")
        with open(snap, "w", encoding="utf-8") as handle:
            json.dump({"as_of": "2026-07-30", "positions": [
                {"ticker": "NVDA", "shares": 900.0, "avg_cost": 4.0,
                 "currency": "USD", "market": "US"}]}, handle)
        snapshot, anchor = snapshot_adapter.normalize_book(snap)

        covered = refresh_engine.plan_refresh(events, snapshot, anchor, splits=complete)
        assert covered["summary"]["status"] == "reconciled", (
            "with the split in the map this book has nothing to settle: "
            f"{covered['summary']}")
        short = refresh_engine.plan_refresh(events, snapshot, anchor, splits={})
        assert [row["kind"] for row in short["pending_confirmations"]] == ["large_change"], (
            "this is the damage being prevented — a share change the user never made: "
            f"{short['pending_confirmations']}")

        # The gate: a recorded window that opens after the anchor refuses.
        state_dir = os.path.join(tmp, "root")
        os.makedirs(state_dir, exist_ok=True)
        with open(os.path.join(state_dir, "last_state.json"), "w", encoding="utf-8") as handle:
            json.dump({"splits": {}, "splits_window": {"start": "2025-12-15",
                                                       "rebase_origin": "2025-12-15"}}, handle)
        try:
            review._refuse_an_unprovable_split_basis(state_dir, "2024-01-01")
        except review.ReviewError as exc:
            assert "2025-12-15" in str(exc) and "2024-01-01" in str(exc), (
                f"the refusal must name both dates so the user can act: {exc}")
        else:
            raise AssertionError(
                "a frozen map that opens after the anchor must refuse rather than reconcile "
                "against a basis nothing established")

        # And it stays silent for the ordinary shapes.
        review._refuse_an_unprovable_split_basis(state_dir, "2026-01-01")   # anchor inside window
        with open(os.path.join(state_dir, "last_state.json"), "w", encoding="utf-8") as handle:
            json.dump({"splits": {}}, handle)                # a pre-#605 review: map was unbounded
        review._refuse_an_unprovable_split_basis(state_dir, "2024-01-01")


def test_a_review_freezes_the_window_its_split_map_is_complete_from():
    """The stamp the refusal above reads. Without it the insufficiency is
    undetectable, which is how it would have shipped silently."""
    import trade_recap as tr
    rows = [{"ticker": "NVDA", "date": dt.date(2026, 7, 1), "qty": 10.0,
             "price": 100.0, "market": "US", "currency": "USD"}]
    request = tr.market_request(rows, "2026-07-30", None, rebase_origin="2020-03-04")
    assert request["window_start"] <= "2020-03-04"
    # `build_state` takes the window as a parameter and stamps it verbatim rather
    # than re-deriving it — a second derivation is what would let the stamp and the
    # map it describes drift apart. Checked at the signature, because driving the
    # whole state builder needs a full review's worth of inputs and would test
    # everything except this.
    import inspect
    assert "splits_window" in inspect.signature(tr.build_state).parameters, (
        "build_state must accept the window beside the map it describes")
    source = inspect.getsource(tr.build_state)
    assert '"splits_window": splits_window,' in source, (
        "the stamp must be the value it was handed, never a re-derivation inside build_state")


def test_a_trade_only_ledger_supplies_a_rebase_origin_too():
    """A ledger with trades and no declared snapshot still has a rebase origin —
    its first trade — because that is where `derive_holdings` starts accumulating.

    Reading only the declared anchor returned None for such a root, the window
    snapped back to the CSV, and every split between the first ledger trade and
    that CSV vanished from the frozen map (external review, finding 1).
    `review._consider_market_universe` had walked trades all along, so the two
    sides of the same question disagreed.
    """
    import trade_recap as tr
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ledger.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for day in ("2020-03-04", "2021-06-10"):
                handle.write(json.dumps({
                    "type": "trade", "date": day, "ticker": "NVDA", "action": "buy",
                    "qty": 10.0, "price": 50.0, "currency": "USD", "market": "US"}) + "\n")
        saved = os.environ.get("TR_LEDGER")
        os.environ["TR_LEDGER"] = path
        try:
            got = tr._ledger_rebase_origin()
        finally:
            if saved is None:
                os.environ.pop("TR_LEDGER", None)
            else:
                os.environ["TR_LEDGER"] = saved
    assert got == "2020-03-04", (
        "the oldest ledger trade is the oldest date a split gets rebased from on a root with no "
        f"declared anchor; got {got!r}")


def test_a_declared_anchor_and_an_older_trade_both_count():
    """Whichever is older wins: `derive_holdings` skips trades at or before the
    anchor, but a trade *before* an anchor still sits inside the history a reader
    can replay, and widening the window is free while missing a split is not."""
    import trade_recap as tr
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ledger.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "type": "trade", "date": "2019-01-02", "ticker": "NVDA", "action": "buy",
                "qty": 5.0, "price": 40.0, "currency": "USD", "market": "US"}) + "\n")
            handle.write(json.dumps({
                "type": "snapshot", "as_of": "2024-01-01", "source": "broker",
                "positions": [{"ticker": "NVDA", "shares": 5.0, "avg_cost": 40.0,
                               "currency": "USD", "market": "US"}]}) + "\n")
        saved = os.environ.get("TR_LEDGER")
        os.environ["TR_LEDGER"] = path
        try:
            got = tr._ledger_rebase_origin()
        finally:
            if saved is None:
                os.environ.pop("TR_LEDGER", None)
            else:
                os.environ["TR_LEDGER"] = saved
    assert got == "2019-01-02", f"the older of the two must win; got {got!r}"


def test_an_unreadable_ledger_widens_nothing_and_refuses_nothing():
    """It is a window hint, not a gate. A first-ever review, a corrupt line or an
    unset path must leave `prepare` working exactly as it does with no ledger."""
    import trade_recap as tr
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "broken.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json at all\n")
        saved = os.environ.get("TR_LEDGER")
        os.environ["TR_LEDGER"] = path
        try:
            assert tr._ledger_rebase_origin() is None
            os.environ["TR_LEDGER"] = os.path.join(tmp, "does-not-exist.jsonl")
            assert tr._ledger_rebase_origin() is None
        finally:
            if saved is None:
                os.environ.pop("TR_LEDGER", None)
            else:
                os.environ["TR_LEDGER"] = saved


# ───── was the split already applied before the file arrived? (#582) ─────
#
# Everything above assumes the input records what executed, un-rebased. Nothing
# checked it, and nothing said so anywhere a maintainer would find it. Hand the
# engine a broker export that has already restated its own history and the split
# is applied a second time: ten times the real share count on a ten-for-one,
# cost preserved, so avg_cost falls tenfold and every weight, concentration
# verdict and sizing rule is measured against a book that never existed.
#
# It survived every gate above because it *inflates*. #550's family erased
# positions, and a book reaching zero is at least a visible anomaly; a position
# ten times too large reads as a large position rather than a broken one.
#
# The comparison is between two things the review already fetched: the file's
# own price for a trade, rebased, and that day's retro-adjusted close. It has to
# reach a **pre-split** row — after the split both readings agree, which is
# exactly why #330's plausibility check, anchored on the ticker's most recent
# trade, cannot see this at all.

_S582_SPLIT = ("2024-06-10", 10.0)              # NVDA's public ten-for-one
_S582_AS_OF = "2026-07-24"
# Raw observations as printed on their own session dates. NVDA spans the split;
# STOIC never split, and its trade price is deliberately nowhere near its close.
_S582_ENVELOPE = {
    "as_of": _S582_AS_OF,
    "source": "Example Exchange official closes",
    "prices": [
        {"ticker": "NVDA", "close": 190.0, "date": _S582_AS_OF, "currency": "USD",
         "splits": [list(_S582_SPLIT)],
         "history": [["2023-01-10", 152.0], ["2023-11-15", 486.0],
                     ["2024-06-07", 1200.0], ["2024-06-11", 121.0],
                     [_S582_AS_OF, 190.0]]},
        {"ticker": "STOIC", "close": 505.0, "date": _S582_AS_OF, "currency": "USD",
         "history": [["2023-01-20", 242.0], ["2024-06-11", 440.0],
                     [_S582_AS_OF, 505.0]]},
    ],
}
# What executed, each row in its own day's basis.
_S582_AS_EXECUTED = [["NVDA", 90, 150.00, "BUY", "2023-01-10"],
                     ["NVDA", 30, 480.00, "BUY", "2023-11-15"],
                     ["STOIC", 70, 24.00, "BUY", "2023-01-20"],
                     ["STOIC", 20, 500.00, "SELL", _S582_AS_OF]]
# The same history as a broker that restates its own past would export it: the
# NVDA rows already carry post-split quantities and prices. STOIC is byte-identical
# in both files, and its 24.00 fill sits a factor of ten under its own close —
# the shape this check looks for, on a ticker that never split.
_S582_ALREADY_ADJUSTED = [["NVDA", 900, 15.00, "BUY", "2023-01-10"],
                          ["NVDA", 300, 48.00, "BUY", "2023-11-15"],
                          ["STOIC", 70, 24.00, "BUY", "2023-01-20"],
                          ["STOIC", 20, 500.00, "SELL", _S582_AS_OF]]


def _s582_envelope_on_the_post_split_basis():
    """The same envelope with NVDA's pre-split `history` typed on today's basis.

    `references/price-feed.md` and the schema both say a `history` entry is a raw
    observation as printed on its own session date, and `price_feed.parse`
    rebases it by the declared splits. Typing the already-adjusted close there
    instead divides it a second time. This is not a hostile input: it was built
    by a careful reader of that contract during review of #582, which is the
    argument for pinning what the check does with it.
    """
    envelope = json.loads(json.dumps(_S582_ENVELOPE))
    for row in envelope["prices"]:
        if row["ticker"] == "NVDA":
            row["history"] = [[day, close / 10.0 if day < _S582_SPLIT[0] else close]
                              for day, close in row["history"]]
    return envelope


def _s582_inputs(tmp, rows, envelope_payload=None):
    envelope = os.path.join(tmp, "prices.json")
    with open(envelope, "w", encoding="utf-8") as handle:
        json.dump(envelope_payload or _S582_ENVELOPE, handle)
    csv_path = os.path.join(tmp, "transactions.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Symbol", "Quantity", "Price", "Action", "TradeDate", "RecordType"])
        for row in rows:
            writer.writerow(list(row) + ["Trade"])
    return csv_path, envelope


def _s582_prepare(tmp, rows, envelope_payload=None):
    csv_path, envelope = _s582_inputs(tmp, rows, envelope_payload)
    return _route(tmp, "prepare", csv_path, "--prices", envelope, "--language", "en")


def test_an_already_adjusted_transaction_file_is_raised_not_reviewed():
    """The defect, end to end through the real CLI.

    Detection uses only what this review already fetched — the same supplied
    envelope that prices the book — and the refusal names the ticker, so the
    agent can put the question to the user rather than reporting a shrug."""
    with tempfile.TemporaryDirectory() as tmp:
        code, payload = _s582_prepare(tmp, _S582_ALREADY_ADJUSTED)
        assert code != 0, ("an already-adjusted file must not review silently", payload)
        error = payload.get("error") or ""
        assert "NVDA" in error, error
        assert "already split-adjusted" in error, error
        assert not os.path.exists(os.path.join(tmp, "ledger.jsonl")), (
            "the refusal must come before anything is recorded")


def test_the_same_history_as_executed_reviews_without_a_word():
    """The counterweight, and the half that makes the test above mean something.

    The identical positions, the identical envelope, the identical split — only
    the basis the file states them in differs. A check that cannot tell these
    two apart is a check that refuses every user who ever held through a
    split."""
    with tempfile.TemporaryDirectory() as tmp:
        code, payload = _s582_prepare(tmp, _S582_AS_EXECUTED)
        assert code == 0, payload
        assert "review_plan" in payload, sorted(payload)


def test_a_ticker_with_no_split_in_the_window_is_never_examined():
    """No cost and no false positives, on the same run as the finding above.

    STOIC's 24.00 fill is a factor of ten under its own close on that day —
    byte-identical in both files, and exactly the disagreement this check looks
    for. It has no split event, so it is not compared at all: there is no basis
    for it to be on the wrong side of, and inventing one would refuse an
    ordinary book over an unusual fill."""
    with tempfile.TemporaryDirectory() as tmp:
        code, payload = _s582_prepare(tmp, _S582_ALREADY_ADJUSTED)
        assert code != 0, payload
        assert "STOIC" not in (payload.get("error") or ""), payload["error"]
    with tempfile.TemporaryDirectory() as tmp:
        code, payload = _s582_prepare(tmp, _S582_AS_EXECUTED)
        assert code == 0, ("STOIC alone must never raise anything", payload)


def test_the_check_reads_the_real_close_series_and_not_merely_its_absence():
    """The trap this repository has walked into before: a gate that proves a
    parameter was passed and never that its value was real (#576).

    `close_series` is the supply, and it is driven here on the frame the review
    itself resolved. Empty in, silence out — so a supply that returns nothing
    turns the finding above off, which is what makes that test evidence."""
    import trade_recap as tr
    rows = [{"ticker": "NVDA", "date": dt.date(2023, 1, 10), "qty": 900.0, "price": 1.5}]
    events = {"NVDA": [list(_S582_SPLIT)]}
    parsed = pf_module.parse(json.loads(json.dumps(_S582_ENVELOPE)))
    frame, error = pf_module.to_frame(parsed, ["NVDA"])
    assert frame is not None, error
    closes = tr.close_series(frame, {"NVDA"})
    assert closes.get("NVDA"), ("the supply produced no series at all", closes)
    assert [row["ticker"] for row in
            split_policy.basis_disagreements(rows, closes, events)] == ["NVDA"]
    assert split_policy.basis_disagreements(rows, tr.close_series(None, {"NVDA"}), events) == []
    assert split_policy.basis_disagreements(rows, {}, events) == []


def test_a_split_too_small_to_separate_the_two_readings_says_nothing():
    """Silence is the honest answer, not a guess.

    A three-for-two puts the two readings 33% apart, inside the range one fill
    may legitimately sit from its own session's close. The engine cannot tell
    them apart, so it does not try — the alternative is a threshold that decides
    which reading is true, which is the adjudication #416 forbids."""
    closes = {"SMALL": [[dt.date(2023, 1, 10), 100.0]]}
    small = {"SMALL": [["2024-06-10", 1.5]]}
    big = {"SMALL": [["2024-06-10", 4.0]]}
    on_the_wrong_basis = [{"ticker": "SMALL", "date": dt.date(2023, 1, 10),
                           "qty": 100.0, "price": 100.0 / 1.5}]
    assert split_policy.basis_disagreements(on_the_wrong_basis, closes, small) == []
    quartered = [{"ticker": "SMALL", "date": dt.date(2023, 1, 10),
                  "qty": 100.0, "price": 25.0}]
    assert [row["ticker"] for row in
            split_policy.basis_disagreements(quartered, closes, big)] == ["SMALL"]


def test_one_row_on_each_basis_is_a_different_problem_and_stays_silent():
    """An export basis is a property of the whole file. One pre-split row
    reading as-executed beside one reading already-adjusted is not the defect
    this check names, so it reports nothing rather than the reading it happens
    to be looking for.

    `closes` here are **adjusted** closes, the basis this function is handed
    (15.2 and 48.6 are the raw 152 and 486 divided by the ten-for-one) — stated
    because getting that wrong is what made the first version of this test green
    for the wrong reason: both of its rows read `unexplained`, so it proved the
    unanimity rule only by accident and would have kept passing had the rule
    been dropped. Caught by the mutation that collapses `unexplained` into the
    finding.
    """
    closes = {"NVDA": [[dt.date(2023, 1, 10), 15.2], [dt.date(2023, 11, 15), 48.6]]}
    events = {"NVDA": [list(_S582_SPLIT)]}
    mixed = [{"ticker": "NVDA", "date": dt.date(2023, 1, 10), "qty": 900.0, "price": 1.5},
             {"ticker": "NVDA", "date": dt.date(2023, 11, 15), "qty": 30.0, "price": 48.0}]
    assert split_policy.basis_disagreements(mixed, closes, events) == []
    # Each half alone reads the way the mix says it does, which is what makes
    # the silence above the unanimity rule rather than two unreadable rows.
    assert [row["ticker"] for row in
            split_policy.basis_disagreements(mixed[:1], closes, events)] == ["NVDA"]
    assert split_policy.basis_disagreements(mixed[1:], closes, events) == []


def test_a_price_a_factor_above_its_close_is_the_third_outcome_and_stays_silent():
    """The disagreement that is *not* this defect's signature buys silence.

    Two readings are separable; a third is neither. A rebased row price a factor
    **above** its close is as far from "as executed" as the defect is, and in the
    opposite direction — so it is not a double-applied split and this check has
    nothing to say about it. Only `already_adjusted`, unanimously, is a finding;
    collapsing `unexplained` into it would turn every other price disagreement on
    a split-carrying ticker into an accusation.

    It is worth pinning because it is reachable by an ordinary mistake, not only
    by a contrived one. Typing an already-adjusted close into a `history` entry —
    which the schema defines as a raw observation, and `parse` therefore divides
    by the split a second time — produces exactly this ratio. That happened
    during review of #582, to a reader of the contract, on a file that really was
    already adjusted. The engine must run the review rather than accuse the user
    of the wrong thing about the right file.
    """
    closes = {"NVDA": [[dt.date(2023, 1, 10), 15.2]]}
    events = {"NVDA": [list(_S582_SPLIT)]}
    ten_times_high = [{"ticker": "NVDA", "date": dt.date(2023, 1, 10),
                       "qty": 90.0, "price": 152.0}]
    assert split_policy.basis_disagreements(ten_times_high, closes, events) == []
    # The same magnitude in the other direction is the defect, so the silence
    # above is about the direction and not about the size of the gap.
    ten_times_low = [{"ticker": "NVDA", "date": dt.date(2023, 1, 10),
                      "qty": 900.0, "price": 1.52}]
    assert [row["ticker"] for row in
            split_policy.basis_disagreements(ten_times_low, closes, events)] == ["NVDA"]
    # And end to end: the whole envelope typed on the wrong basis reviews rather
    # than raising, because the engine cannot tell a mistyped feed from a
    # mispriced fill and may not pick (#416).
    with tempfile.TemporaryDirectory() as tmp:
        code, payload = _s582_prepare(tmp, _S582_AS_EXECUTED,
                                      _s582_envelope_on_the_post_split_basis())
        assert code == 0, ("a feed on the wrong basis must not become an accusation "
                           "about the transaction file", payload)


def test_a_post_split_trade_alone_can_never_raise_this():
    """Why #330's check cannot see this defect and this one is not redundant
    with it. After the split both readings agree, and #330 anchors on the
    ticker's *most recent* trade — which for almost every real book is on that
    side of the split."""
    closes = {"NVDA": [[dt.date(2024, 6, 11), 121.0]]}
    events = {"NVDA": [list(_S582_SPLIT)]}
    after = [{"ticker": "NVDA", "date": dt.date(2024, 6, 11), "qty": 100.0, "price": 121.0}]
    assert split_policy.basis_disagreements(after, closes, events) == []


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
