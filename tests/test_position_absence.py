#!/usr/bin/env python3
"""Confirmed disappearances recorded without a fill (#485 Slice C, C1a).

The owner ruling this file locks: when a new snapshot omits a position the
record already held and the user confirms it was sold, the engine may record
that the position is absent as of that date -- and may **not** invent the trade.
Win rate, payoff and exit discipline must never be computed from a manufactured
number, the same prohibition ``condition-check.schema.json`` states about
engine-written verdicts.

Division of labour with the neighbouring suites:
- ``test_ledger.py``   -> the accounting fact layer's derivation and reconcile.
- ``test_revisit.py``  -> trade-sourced exit detection, queue dedupe, due maths.
- this file            -> the absence event's own contract, and the fact that an
  absence reaches the exit pipeline as a real exit rather than disappearing.

The two mutation proofs this file exists to fail (both verified by hand before
the checks were written):

  (a) strip absences out of ``revisit.detect_exits`` -> the "an absence reaches
      the exit pipeline" checks go red. Without them the confirmation answer is
      a written-never-read field, which is #429's failure class.
  (b) add a price/share field to ``ledger.build_position_absence`` -> the
      builder's own key-set gate raises, so every writer goes red rather than
      one lint. The engine, not a test, is the guard (see the maintainer note in
      ``docs/development-guide.md`` about agent-free defences).

Run:
  python3 tests/test_position_absence.py
"""
import datetime as dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "skills", "fomo-kernel", "engine")
sys.path.insert(0, ENGINE)
import ledger as lg  # noqa: E402
import revisit as rv  # noqa: E402
import review  # noqa: E402
import snapshot_adapter  # noqa: E402


def _snap(as_of, positions, **extra):
    return {"type": "snapshot", "as_of": as_of, "source": "user_declared",
            "positions": positions, **extra}


def _tr(date, ticker, action, qty, price):
    return {"type": "trade", "date": date, "ticker": ticker, "action": action,
            "qty": qty, "price": price}


def _normalize(raw, today="2026-07-20"):
    """Run the agent-facing envelope through the adapter's own validator.

    ``snapshot_adapter``'s only public entry point reads a file and then runs
    valuation and policy lookups this file has no business exercising, so the
    validator is driven the way ``prepare`` drives it: through ``_load``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "snapshot.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(raw, handle)
        return snapshot_adapter._load(path, dt.date.fromisoformat(today))


def _book_then_absence(absent="ACME", as_of="2026-06-30", absence_date="2026-07-15"):
    """Prior book with two holdings, then ACME confirmed gone, then a new anchor.

    This is exactly the row order the refresh flow appends in one call:
    ``[adjustment?, absence*, anchor]``.
    """
    prior = _snap(as_of, [
        {"ticker": "ACME", "shares": 100, "avg_cost": 12.0, "market": "US", "currency": "USD"},
        {"ticker": "WIDGET", "shares": 50, "avg_cost": 30.0, "market": "US", "currency": "USD"},
    ])
    absence = lg.build_position_absence(
        date=absence_date, ticker=absent,
        cycle_id=f"{absent}#{as_of}#1", session_id="sess-1")
    later = _snap(absence_date, [
        {"ticker": "WIDGET", "shares": 50, "avg_cost": 30.0, "market": "US", "currency": "USD"},
    ])
    return [prior, absence, later]


# ─────────────── A. the event's own contract ───────────────

def test_builder_is_content_addressed_and_fill_free():
    one = lg.build_position_absence(date="2026-07-15", ticker="ACME",
                                    cycle_id="ACME#2026-06-30#1", session_id="s1")
    two = lg.build_position_absence(date="2026-07-15", ticker="ACME",
                                    cycle_id="ACME#2026-06-30#1", session_id="s2")
    assert one["absence_id"] == two["absence_id"], (
        "absence_id must hash identity only, so re-confirming the same "
        "disappearance replays as the same row")
    assert one["type"] == "position_absence"
    assert set(one) <= lg.ABSENCE_KEYS, set(one) - lg.ABSENCE_KEYS
    leaked = set(one) & lg.ABSENCE_FORBIDDEN_KEYS
    assert not leaked, f"an absence must carry no fill facts, found {sorted(leaked)}"


def test_forbidden_and_declared_key_sets_cannot_overlap():
    """Mutation proof (b)'s other half.

    Adding ``price`` to the builder's output trips the ``unknown fields`` gate;
    the only way past that gate is to declare it in ABSENCE_KEYS, which this
    check refuses. Both doors, one invariant: the row has no fill.
    """
    overlap = lg.ABSENCE_KEYS & lg.ABSENCE_FORBIDDEN_KEYS
    assert not overlap, f"declared absence fields must never name a fill: {sorted(overlap)}"


def test_builder_fails_closed_on_missing_identity():
    for kwargs in ({"date": "not-a-date", "ticker": "ACME", "cycle_id": "c"},
                   {"date": "2026-07-15", "ticker": "", "cycle_id": "c"},
                   {"date": "2026-07-15", "ticker": "ACME", "cycle_id": ""}):
        try:
            lg.build_position_absence(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"expected a refusal for {kwargs}")


def test_ledger_reads_an_absence_as_a_known_row_not_a_bad_one():
    """#462's strict gate must not treat a new event type as an unreadable row.

    A ledger that refuses to derive anything because it met an absence would
    take the whole product down for the users the refresh flow serves.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ledger.jsonl")
        lg.append_events(path, _book_then_absence())
        events, skipped = lg.load_ledger(path)
        assert skipped == 0, "an absence row is a known event, not a scan issue"
        assert sum(1 for ev in events if ev.get("type") == "position_absence") == 1


def test_absence_does_not_move_derived_holdings_by_itself():
    """The absence is a signal and an audit trail; the new anchor moves the book.

    Applying both would double-count, the same reason ``adjustment`` stays out
    of derivation.
    """
    prior, absence, later = _book_then_absence()
    without = lg.derive_holdings([prior])["holdings"]
    with_absence = lg.derive_holdings([prior, absence])["holdings"]
    assert with_absence == without, "an absence alone must not change the book"
    after_anchor = lg.derive_holdings([prior, absence, later])["holdings"]
    assert "ACME" not in after_anchor and "WIDGET" in after_anchor


def test_position_absences_reader_skips_malformed_rows():
    rows = lg.position_absences([
        {"type": "position_absence", "date": "nope", "ticker": "A", "cycle_id": "c"},
        {"type": "position_absence", "date": "2026-07-15", "ticker": "B", "cycle_id": "c2",
         "absence_id": "absence-x"},
        {"type": "trade", "date": "2026-07-15", "ticker": "C", "action": "buy",
         "qty": 1, "price": 1},
    ])
    assert [row["ticker"] for row in rows] == ["B"]
    assert rows[0]["index"] == 1, "the reader must report where the row sits"


# ─────────────── B. the absence reaches the exit pipeline ───────────────

def test_absence_becomes_a_full_exit_with_no_fill_price():
    """Mutation proof (a): the check that goes red if detect_exits stops
    including absences."""
    exits = rv.detect_exits(_book_then_absence())
    acme = [row for row in exits if row["ticker"] == "ACME"]
    assert acme, "a confirmed disappearance must reach the exit pipeline"
    row = acme[0]
    assert row["kind"] == "full"
    assert row["exit_date"] == "2026-07-15"
    assert row["exit_price"] is None, "no fill was supplied and none may be invented"
    assert row["shares_sold"] == 100.0, "shares come from the recorded prior book"
    assert row["currency"] == "USD" and row["market"] == "US"
    assert row["cost_basis"] == 1200.0, "cost basis is copied, never derived from a price"
    assert row["absence_id"], "the exit row keeps its provenance"


def test_absence_exit_reaches_the_queue_and_dedupes_on_replay():
    with tempfile.TemporaryDirectory() as tmp:
        led = os.path.join(tmp, "ledger.jsonl")
        queue = os.path.join(tmp, "revisit.jsonl")
        lg.append_events(led, _book_then_absence())
        new1, dup1 = rv.enqueue_from_ledger(led, queue, today=dt.date(2026, 7, 16))
        new2, dup2 = rv.enqueue_from_ledger(led, queue, today=dt.date(2026, 7, 16))
        assert [row["ticker"] for row in new1] == ["ACME"] and dup1 == 0
        assert new2 == [] and dup2 == 1, "re-running finalize must not re-queue the exit"
        revisits, _res, skipped = rv.load_queue(queue)
        assert skipped == 0 and len(revisits) == 1
        item = next(iter(revisits.values()))
        assert item["exit_price"] is None and item["kind"] == "full"
        assert item["due"]["30"] == "2026-08-14", "an unpriced exit still gets 30/60/90"


def test_two_absences_in_one_batch_see_the_same_prior_book():
    prior = _snap("2026-06-30", [
        {"ticker": "ACME", "shares": 100, "avg_cost": 12.0, "market": "US", "currency": "USD"},
        {"ticker": "WIDGET", "shares": 50, "avg_cost": 30.0, "market": "US", "currency": "USD"},
    ])
    events = [prior]
    for ticker in ("ACME", "WIDGET"):
        events.append(lg.build_position_absence(
            date="2026-07-15", ticker=ticker, cycle_id=f"{ticker}#2026-06-30#1"))
    events.append(_snap("2026-07-15", [
        {"ticker": "NEWCO", "shares": 5, "avg_cost": 40.0, "market": "US", "currency": "USD"}]))
    rows = {row["ticker"]: row for row in rv.absence_exits(events)}
    assert set(rows) == {"ACME", "WIDGET"}
    assert rows["ACME"]["shares_sold"] == 100.0 and rows["WIDGET"]["shares_sold"] == 50.0, (
        "the second absence must not read a book the first one already emptied")


def test_absence_after_trades_uses_the_book_at_that_moment():
    events = [
        _snap("2026-06-30", [{"ticker": "ACME", "shares": 100, "avg_cost": 12.0,
                              "market": "US", "currency": "USD"}]),
        _tr("2026-07-05", "ACME", "buy", 20, 15.0),
    ]
    events.append(lg.build_position_absence(
        date="2026-07-15", ticker="ACME", cycle_id="ACME#2026-06-30#1"))
    row = rv.absence_exits(events)[0]
    assert row["shares_sold"] == 120.0, "the added 20 shares are part of what left"
    assert row["cost_basis"] == 1500.0


# ─────────────── C. downstream tolerates a missing fill ───────────────

def test_notional_falls_back_to_cost_basis_for_ranking_only():
    absent = {"ticker": "ACME", "exit_price": None, "shares_sold": 100.0,
              "cost_basis": 1200.0}
    priced = {"ticker": "WIDGET", "exit_price": 30.0, "shares_sold": 50.0}
    assert rv._notional(absent) == 1200.0, (
        "a zero here would push every unpriced exit to the bottom of every "
        "amount-ordered surface, which is indistinguishable from hiding it")
    assert rv._notional(priced) == 1500.0
    assert rv._notional({"exit_price": None}) == 0.0, "a missing cost basis stays 0, not a guess"


def test_is_priced_exit_is_the_single_reader():
    assert rv.is_priced_exit({"exit_price": 10.0}) is True
    assert rv.is_priced_exit({"exit_price": None}) is False
    assert rv.is_priced_exit({"exit_price": 0}) is False
    assert rv.is_priced_exit({"exit_price": "oops"}) is False
    assert rv.is_priced_exit({}) is False


def test_compare_does_not_ask_for_a_price_it_cannot_use():
    item = {"ticker": "ACME", "exit_price": None, "shares_sold": 100.0,
            "cost_basis": 1200.0, "swaps": [], "idle_cash": True}
    out = rv.compare(item, {"ACME": 18.0})
    assert out["orig_ret"] is None and out["swap_net_pp"] is None
    assert out["needs_prices"] == [], (
        "the gap is the fill, not the quote; asking for a price the maths "
        "cannot use is a dead instruction")
    assert out["unpriced_exit"] is True


def test_compare_shape_is_unchanged_for_a_priced_exit():
    item = {"ticker": "NVDA", "exit_price": 120.0, "shares_sold": 10.0,
            "swaps": [], "idle_cash": True}
    out = rv.compare(item, {"NVDA": 150.0})
    assert "unpriced_exit" not in out, (
        "the key is stamped only when true, so every stored plan and card "
        "fixture keeps the exact object shape it already carries")
    assert round(out["orig_ret"], 6) == 0.25


def test_backlog_summary_excludes_an_unpriced_exit_from_hindsight():
    revisits = {
        "a": {"revisit_id": "a", "ticker": "ACME", "exit_date": "2026-01-05",
              "exit_price": None, "shares_sold": 100.0, "cost_basis": 1200.0,
              "kind": "full", "enqueued_at": "2026-07-16",
              "due": {"30": "2026-02-04", "60": "2026-03-05", "90": "2026-04-04"}},
        "b": {"revisit_id": "b", "ticker": "WIDGET", "exit_date": "2026-01-05",
              "exit_price": 30.0, "shares_sold": 50.0, "kind": "full",
              "enqueued_at": "2026-07-16",
              "due": {"30": "2026-02-04", "60": "2026-03-05", "90": "2026-04-04"}},
    }
    _top, summary, total = rv.scan_backlog(revisits, {}, prices={"ACME": 18.0, "WIDGET": 33.0})
    assert total == 2, "the unpriced exit is still a real exit in the backlog"
    assert summary["priced"] == 1, (
        "hindsight return needs a fill to measure against; counting the "
        "unpriced row would divide by something nobody supplied")
    assert summary["sold_before_rise"] == 1


# ─────────────── D. what the user is asked and told ───────────────

def test_capture_and_due_stems_never_state_a_price_that_does_not_exist():
    item = {"ticker": "ACME", "revisit_id": "r1", "cycle_id": "ACME#2026-06-30#1",
            "exit_date": "2026-07-15", "exit_price": None, "shares_sold": 100.0,
            "shares_before": 100.0, "cost_basis": 1200.0, "kind": "full",
            "currency": "USD", "absence_id": "absence-x", "swaps": []}
    for language in ("en", "zh-TW"):
        capture = review._exit_question(item, language)
        assert "1,200" not in capture["question"] and "1200" not in capture["question"], (
            "the cost basis is a ranking magnitude, never presentable as proceeds")
        assert capture["exit_notional"] is None and capture["exit_price"] is None
        due = review._due_question({"revisit_id": "r1", "checkpoint": "30",
                                    "due_date": "2026-08-14", "item": item}, language)
        assert "None" not in due["question"], due["question"]
        assert "2026-07-15" in due["question"]


def test_priced_capture_stem_still_states_the_amount():
    item = {"ticker": "NVDA", "revisit_id": "r2", "cycle_id": "NVDA#2026-05-01#1",
            "exit_date": "2026-06-15", "exit_price": 120.0, "shares_sold": 10.0,
            "shares_before": 10.0, "kind": "full", "currency": "USD", "swaps": []}
    capture = review._exit_question(item, "en")
    assert "USD 1,200" in capture["question"] and capture["exit_notional"] == 1200.0


def test_unpriced_exit_is_disclosed_rather_than_silently_dropped():
    recent = [{"ticker": "ACME", "revisit_id": "r1", "exit_price": None,
               "absence_id": "absence-x"}]
    card = review._flag_unpriced_exits({}, recent, [], None)
    entry = [row for row in card["honesty_ledger"] if row["key"] == "unpriced_exits"]
    assert entry and entry[0]["data"]["tickers"] == ["ACME"]
    assert entry[0]["status"] == "excluded"


def test_a_trade_sourced_exit_never_borrows_the_disclosure():
    recent = [{"ticker": "NVDA", "revisit_id": "r2", "exit_price": 120.0}]
    assert review._flag_unpriced_exits({}, recent, [], None) == {}, (
        "only an absence is outside win rate / payoff / exit discipline; a "
        "priced exit claiming that exclusion would be a false statement")


def test_the_disclosure_sentence_exists_in_every_shipped_locale():
    for language in ("en", "zh-TW", "zh-CN"):
        import card_renderer
        sentence = (card_renderer.load_copy(language).get("honesty") or {}).get("unpriced_exits")
        assert sentence, f"{language} has no fallback sentence for unpriced_exits"


# ─────────────── E. carried-forward positions ───────────────

def test_adapter_accepts_carried_and_keeps_it_out_of_every_other_payload():
    raw = {"as_of": "2026-07-15", "positions": [
        {"ticker": "ACME", "shares": 100, "avg_cost": 12.0, "market": "US",
         "currency": "USD", "carried": True},
        {"ticker": "WIDGET", "shares": 50, "avg_cost": 30.0, "market": "US",
         "currency": "USD"}]}
    snapshot = _normalize(raw)
    by_ticker = {row["ticker"]: row for row in snapshot["positions"]}
    assert by_ticker["ACME"]["carried"] is True
    assert "carried" not in by_ticker["WIDGET"], (
        "carried must be absent rather than false, or every existing snapshot "
        "payload changes its content-addressed snapshot_id")


def test_carried_must_be_a_boolean():
    raw = {"as_of": "2026-07-15", "positions": [
        {"ticker": "ACME", "shares": 100, "market": "US", "currency": "USD",
         "carried": "yes"}]}
    try:
        _normalize(raw)
    except snapshot_adapter.SnapshotError:
        return
    raise AssertionError("a non-boolean carried flag must fail closed")


def test_merged_rows_are_carried_only_when_every_part_was():
    raw = {"as_of": "2026-07-15", "positions": [
        {"ticker": "ACME", "shares": 60, "market": "US", "currency": "USD", "carried": True},
        {"ticker": "ACME", "shares": 40, "market": "US", "currency": "USD"}]}
    snapshot = _normalize(raw)
    row = snapshot["positions"][0]
    assert row["shares"] == 100 and "carried" not in row, (
        "one actually-observed row means the position is on the supplied view")


def test_a_book_with_no_carried_row_keeps_its_snapshot_identity():
    """The regression this guards is silent and total: a new key on every
    position rewrites every ``snapshot_id`` and turns idempotent replay into
    duplicate anchors."""
    import session
    raw = {"as_of": "2026-07-15", "positions": [
        {"ticker": "ACME", "shares": 100, "avg_cost": 12.0, "market": "US", "currency": "USD"}]}
    anchor = snapshot_adapter._anchor(_normalize(raw))
    payload = session._snapshot_payload(anchor)
    assert json.dumps(payload["positions"], sort_keys=True) == json.dumps(
        [{"avg_cost": 12.0, "currency": "USD", "market": "US", "shares": 100.0,
          "ticker": "ACME"}], sort_keys=True)


def test_a_carried_row_states_itself_in_the_projected_anchor():
    raw = {"as_of": "2026-07-15", "positions": [
        {"ticker": "ACME", "shares": 100, "avg_cost": 12.0, "market": "US",
         "currency": "USD", "carried": True}]}
    anchor = snapshot_adapter._anchor(_normalize(raw))
    assert anchor["positions"][0]["carried"] is True, (
        "the adopted book must state which rows came from the record rather "
        "than from the view the user supplied")


# ─────────────────────────── runner ───────────────────────────

def _main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _main() else 0)
