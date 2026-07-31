#!/usr/bin/env python3
"""The independent book-refresh lane (#485 Slice C, C1b).

What this file locks is the owner ruling of 2026-07-28: updating the recorded
book is its OWN flow — no card, no review question budget, no session — and it
never adopts a destructive change the user did not confirm.

The four properties every check here defends:

1. **A disappearance always asks, and the two answers lead to different states.**
   If both branches ended in the same place the question would buy nothing,
   which is #429's failure class with a new name.
2. **Answers are accepted only for what the engine raised**, and any raised item
   left unanswered fails the whole refresh closed. This is
   ``condition-check.schema.json``'s ``user_response`` pattern.
3. **A frozen plan cannot be applied to a book that moved.** ``refresh_id`` is
   content-addressed over exactly what the user was shown, and phase 2
   recomputes phase 1 from scratch under the lock.
4. **Nothing is written on any refusal path.** Not on a validation error, not on
   a stale plan, not on ``resupply``.

Division of labour: ``test_position_absence.py`` owns the absence event and the
exit pipeline that reads it (C1a); this file owns the flow that produces one.

Run:
  python3 tests/test_book_refresh.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "skills", "fomo-kernel", "engine")
SCHEMA = os.path.join(ROOT, "skills", "fomo-kernel", "schemas", "book-refresh.schema.json")
sys.path.insert(0, ENGINE)

# The market must not be an input to these assertions (#620). Declared in
# tests/offline_posture.py so a direct `python3 tests/<this file>` run and a
# `run_all.py` run reach the same answer; TR_TEST_NETWORK=1 still opts in.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_posture  # noqa: E402
offline_posture.apply()
import book_refresh as br  # noqa: E402
import horizon  # noqa: E402
import ledger as lg  # noqa: E402
import revisit as rv  # noqa: E402
import snapshot_adapter  # noqa: E402

FLOW = os.path.join(ROOT, "skills", "fomo-kernel", "flows", "book-refresh.md")

SEED = {"type": "snapshot", "as_of": "2026-06-30", "source": "user_declared",
        "is_complete": True, "snapshot_id": "snapshot-seed0000000000",
        "positions": [
            {"ticker": "ACME", "shares": 100.0, "avg_cost": 12.0,
             "market": "US", "currency": "USD"},
            {"ticker": "WIDGET", "shares": 50.0, "avg_cost": 30.0,
             "market": "US", "currency": "USD"},
            {"ticker": "BIGCO", "shares": 200.0, "avg_cost": 40.0,
             "market": "US", "currency": "USD"}]}


def _root(tmp, events=(SEED,)):
    root = os.path.join(tmp, "coach")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "ledger.jsonl"), "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    return root


def _snapshot(tmp, positions, as_of="2026-07-15", **extra):
    path = os.path.join(tmp, "snapshot.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"as_of": as_of, "positions": positions, **extra}, handle)
    return path


def _cli(root, snapshot_path, answers=None):
    argv = [sys.executable, os.path.join(ENGINE, "review.py"), "refresh",
            "--root", root, "--snapshot-json", snapshot_path]
    if answers is not None:
        argv += ["--answers", json.dumps(answers)]
    out = subprocess.run(argv, capture_output=True, text=True, check=False)
    try:
        return json.loads(out.stdout)
    except ValueError as exc:  # noqa: BLE001
        raise AssertionError(f"refresh emitted no JSON: {out.stdout!r} {out.stderr!r}") from exc


def _ledger_rows(root):
    with open(os.path.join(root, "ledger.jsonl"), encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# Everything but ACME, so ACME disappears and nothing else moves enough to ask.
KEPT = [{"ticker": "WIDGET", "shares": 50, "avg_cost": 30.0, "market": "US", "currency": "USD"},
        {"ticker": "BIGCO", "shares": 200, "avg_cost": 40.0, "market": "US", "currency": "USD"}]


# ─────────────── A. phase 1 raises the right questions, and only those ───────────────

def test_an_unexplained_disappearance_always_asks():
    with tempfile.TemporaryDirectory() as tmp:
        receipt = _cli(_root(tmp), _snapshot(tmp, KEPT))
        assert receipt["status"] == "pending_confirmation"
        pending = receipt["pending_confirmations"]
        assert [row["ticker"] for row in pending] == ["ACME"]
        assert pending[0]["kind"] == "disappearance"
        assert pending[0]["cycle_id"] == "ACME#2026-06-30#1", (
            "the cycle that would close is engine-derived, never supplied")
        assert pending[0]["options"] == ["sold", "not_captured", "resupply"], (
            "a disappearance is never 'confirmed' -- saying it is gone is 'sold'")


def test_an_appearance_asks_how_long_and_at_what_cost():
    """Owner ruling 2026-07-29 (#531), superseding the original asymmetry.

    An appearance destroys nothing, which is why this lane once adopted it in
    silence. What that missed is that it arrives with **no provenance** and the
    engine has no way to recover it later: a position bought last week and one
    transferred in after six years produce byte-identical ledger state. The
    question can only be asked at the moment the difference appears.

    The item states which detail it still needs. NEWCO declares a cost, so only
    the duration is open; DARKCO declares none, so the cost is asked at the
    point of entry rather than left to make `consider` refuse later (#528).
    """
    with tempfile.TemporaryDirectory() as tmp:
        positions = list(KEPT) + [
            {"ticker": "ACME", "shares": 100, "avg_cost": 12.0, "market": "US", "currency": "USD"},
            {"ticker": "NEWCO", "shares": 10, "avg_cost": 5.0, "market": "US", "currency": "USD"},
            {"ticker": "DARKCO", "shares": 4, "market": "US", "currency": "USD"}]
        receipt = _cli(_root(tmp), _snapshot(tmp, positions))
        assert receipt["status"] == "pending_confirmation"
        assert receipt["pending_confirmations"] == [
            {"kind": "appearance", "ticker": "DARKCO", "declared_shares": 4.0,
             "needs_avg_cost": True, "options": ["confirmed", "resupply"]},
            {"kind": "appearance", "ticker": "NEWCO", "declared_shares": 10.0,
             "needs_avg_cost": False, "options": ["confirmed", "resupply"]}]
        assert receipt["summary"]["only_declared"] == ["DARKCO", "NEWCO"]


def test_a_confirmed_appearance_dates_its_cycle_from_the_months_the_user_gave():
    """The engine converts the month count, never the agent (SKILL.md rule 1).

    Eighteen months before the 2026-07-15 declaration is 2025-01-15, and that
    date — not the declaration's — is what the position's cycle is measured
    from. Before this, both a fresh buy and a six-year holding entered the book
    dated today, and every holding-period reading was computed against a
    bookkeeping artifact.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        positions = list(KEPT) + [
            {"ticker": "ACME", "shares": 100, "avg_cost": 12.0, "market": "US", "currency": "USD"},
            {"ticker": "NEWCO", "shares": 10, "market": "US", "currency": "USD"}]
        snapshot = _snapshot(tmp, positions)
        receipt = _cli(root, snapshot)
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "NEWCO", "classification": "confirmed",
             "held_months": 18, "avg_cost": 41.5}]})
        assert out["status"] == "adopted" and out["recorded_new"] == ["NEWCO"]
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        row = [p for p in lg.latest_anchor(events)["positions"] if p["ticker"] == "NEWCO"][0]
        assert row["since"] == "2025-01-15" and row["since_basis"] == "user_estimate", (
            "the date is stored paired with the stamp saying it is an estimate; "
            "neither travels alone")
        assert row["avg_cost"] == 41.5, "the cost asked for at entry is what enters the book"
        holding = lg.derive_holdings(events)["holdings"]["NEWCO"]
        assert holding["since"] == "2025-01-15"
        assert holding["cycle_id"] == "NEWCO#2025-01-15#1"
        assert not lg.derive_holdings(events)["integrity"]


def test_i_dont_know_records_the_position_without_inventing_a_date():
    """The accepted answer, and it keeps the representation that already exists.

    `horizon._cycle_start` resolves a two-segment `ticker#unknown` to None, so
    the holding drops out of holding-period diagnostics instead of carrying a
    manufactured start. Nothing about that path is re-implemented here.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        positions = list(KEPT) + [
            {"ticker": "ACME", "shares": 100, "avg_cost": 12.0, "market": "US", "currency": "USD"},
            {"ticker": "NEWCO", "shares": 10, "avg_cost": 5.0, "market": "US", "currency": "USD"}]
        snapshot = _snapshot(tmp, positions)
        receipt = _cli(root, snapshot)
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "NEWCO", "classification": "confirmed", "held_months": None}]})
        assert out["status"] == "adopted"
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        row = [p for p in lg.latest_anchor(events)["positions"] if p["ticker"] == "NEWCO"][0]
        assert row["since_basis"] == "unknown" and "since" not in row, (
            "no date is invented for a start the user does not know")
        holding = lg.derive_holdings(events)["holdings"]["NEWCO"]
        assert holding["cycle_id"] == "NEWCO#unknown"
        assert horizon._cycle_start(holding["cycle_id"]) is None
        assert horizon.scan([{"cycle_id": holding["cycle_id"], "horizon": "weeks",
                              "ticker": "NEWCO"}], "2026-12-31") == [], (
            "a holding with no known start is dropped from the diagnostic, not "
            "measured from a date nobody supplied")
        assert holding["since"] == "2026-07-15", (
            "`since` stays the bookkeeping fact it has always been -- the day "
            "the position entered the book -- so every existing reader still "
            "gets a real date; cycle_id is what carries the unknown")


def test_a_stamped_start_survives_the_next_refresh():
    """Otherwise the answer buys exactly one review.

    The next declaration is an ordinary envelope with no provenance on it, so
    without this the position's start snaps back to the new anchor's date and
    the question has to be asked again -- except it never is, because the
    position is no longer appearing. The user is not re-asked here; the stamp
    is copied off the recorded anchor.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        newco = {"ticker": "NEWCO", "shares": 10, "avg_cost": 5.0,
                 "market": "US", "currency": "USD"}
        acme = {"ticker": "ACME", "shares": 100, "avg_cost": 12.0,
                "market": "US", "currency": "USD"}
        first = _snapshot(tmp, list(KEPT) + [acme, newco], as_of="2026-07-15")
        receipt = _cli(root, first)
        _cli(root, first, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "NEWCO", "classification": "confirmed", "held_months": 18}]})
        # A later, ordinary view: NEWCO is simply held now, and moves a little.
        moved = dict(newco, shares=12)
        second = os.path.join(tmp, "second.json")
        with open(second, "w", encoding="utf-8") as handle:
            json.dump({"as_of": "2026-07-25", "positions": list(KEPT) + [acme, moved]}, handle)
        again = _cli(root, second)
        assert again["pending_confirmations"] == [], (
            "a held position is not an appearance and must not be asked about twice")
        out = _cli(root, second, {"refresh_id": again["refresh_id"], "answers": []})
        assert out["status"] == "adopted"
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        holding = lg.derive_holdings(events)["holdings"]["NEWCO"]
        assert holding["shares"] == 12.0, "the new view's numbers are adopted"
        assert holding["cycle_id"] == "NEWCO#2025-01-15#1", (
            "the answered start survives; snapping back to 2026-07-25 would "
            "silently spend the user's answer on one review")


def test_a_position_nobody_ever_asked_about_keeps_its_cycle_too():
    """#539: the stamp was never the fact worth carrying, and most rows have none.

    The appearance question is only asked for a position that appears *after* the
    book exists, so every position in the user's first declaration is
    permanently unstampable. Carrying only a stamp therefore reached exactly the
    rows that needed it least: a position the user was asked about kept its
    cycle, and the ones they were never asked about -- the whole opening book --
    reminted on every adoption, taking every thesis written against them with it.

    What is carried now is the start the record already holds, which for these
    rows is the date the book first saw them. That is the same lower bound the
    default produces on the first declaration; the defect was that each later
    declaration replaced it with a newer, weaker one.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        assert lg.derive_holdings(events)["holdings"]["ACME"]["cycle_id"] == \
            "ACME#2026-06-30#1"
        # WIDGET moves below both thresholds, so nothing is asked and the adopted
        # book really differs from the record -- without that the refresh
        # reconciles clean, keeps the old anchor, and proves nothing.
        second = _snapshot(tmp, [{"ticker": "ACME", "shares": 100, "avg_cost": 12.0,
                                  "market": "US", "currency": "USD"},
                                 dict(KEPT[0], shares=48), KEPT[1]], as_of="2026-07-25")
        receipt = _cli(root, second)
        assert receipt["pending_confirmations"] == []
        assert _cli(root, second, {"refresh_id": receipt["refresh_id"],
                                   "answers": []})["status"] == "adopted"
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        holdings = lg.derive_holdings(events)["holdings"]
        assert holdings["ACME"]["cycle_id"] == "ACME#2026-06-30#1", (
            "a position held right through two declarations is one position; "
            "reminting it to ACME#2026-07-25#1 is what re-asks its thesis")
        assert holdings["WIDGET"]["shares"] == 48.0, "the new view's numbers are adopted"
        assert holdings["WIDGET"]["cycle_id"] == "WIDGET#2026-06-30#1", (
            "a share count changing is not a position restarting")


def test_a_start_the_ledger_can_prove_is_not_overwritten_by_a_declaration():
    """#539, second symptom: the same defect where the engine knew the answer.

    A position opened by a real trade after the anchor carries the date it was
    actually bought. Before this, the next declaration replaced that with its own
    `as_of` -- discarding a proven fact in favour of a bookkeeping one, and
    reminting the cycle while doing it. Nobody is asked anything here; the record
    already holds the truth and adoption has only to keep it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        with open(os.path.join(root, "ledger.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "trade", "date": "2026-07-05",
                                     "ticker": "NEWCO", "action": "buy", "qty": 10,
                                     "price": 5.0, "market": "US",
                                     "currency": "USD"}, sort_keys=True) + "\n")
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        before = lg.derive_holdings(events)["holdings"]["NEWCO"]
        assert before["origin"] == "trades" and before["cycle_id"] == "NEWCO#2026-07-05#1"
        second = _snapshot(tmp, [{"ticker": "ACME", "shares": 100, "avg_cost": 12.0,
                                  "market": "US", "currency": "USD"},
                                 dict(KEPT[0], shares=48), KEPT[1],
                                 {"ticker": "NEWCO", "shares": 10, "avg_cost": 5.0,
                                  "market": "US", "currency": "USD"}],
                          as_of="2026-07-25")
        receipt = _cli(root, second)
        assert receipt["pending_confirmations"] == [], (
            "the record already holds NEWCO, so it is not an appearance")
        assert _cli(root, second, {"refresh_id": receipt["refresh_id"],
                                   "answers": []})["status"] == "adopted"
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        assert lg.derive_holdings(events)["holdings"]["NEWCO"]["cycle_id"] == \
            "NEWCO#2026-07-05#1", (
                "the buy date is the start; a declaration states what is held, "
                "never since when, so it may not overwrite one")
        stamped = {row["ticker"]: row.get("since_basis")
                   for row in lg.latest_anchor(events)["positions"]}
        assert stamped["NEWCO"] == "trade_event", (
            "and it is labelled by the evidence: the ledger watched this cycle "
            "open, so the date is exact rather than a declaration's lower bound")
        assert stamped["ACME"] == "snapshot_anchor"

        # The second declaration is where a transport-only marker would have lost
        # it. NEWCO now sits in the anchor and derives `origin == "snapshot"` like
        # every other row, so a basis recomputed from origin here would silently
        # demote a trade-proven date to a bookkeeping one -- and nothing
        # downstream could ever tell that it had been exact.
        third = _snapshot(tmp, [{"ticker": "ACME", "shares": 100, "avg_cost": 12.0,
                                 "market": "US", "currency": "USD"},
                                dict(KEPT[0], shares=47), KEPT[1],
                                {"ticker": "NEWCO", "shares": 10, "avg_cost": 5.0,
                                 "market": "US", "currency": "USD"}],
                          as_of="2026-07-30")
        again = _cli(root, third)
        assert _cli(root, third, {"refresh_id": again["refresh_id"],
                                  "answers": []})["status"] == "adopted"
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        row = [p for p in lg.latest_anchor(events)["positions"]
               if p["ticker"] == "NEWCO"][0]
        assert (row["since"], row["since_basis"]) == ("2026-07-05", "trade_event"), (
            "a start's evidence survives every later adoption; it is not "
            "recoverable afterwards, because by then `origin` describes the "
            "snapshot writer rather than how the start was learned")


def test_a_carried_start_never_hands_a_live_position_a_sold_cycles_identity():
    """The start alone is not the identity, and the gap is worse than reminting.

    A `cycle_id` is `ticker#since#seq`. Two cycles opened on the same day --
    a position flattened and bought back within one session, which is ordinary
    behavior -- differ in the sequence and nothing else. So an adoption that
    carries the date and not the sequence does not merely misname the live
    position: it gives it the exact id of the one that was sold, and the folded
    thesis for that cycle is closed, possibly falsified, with its own exit
    narrative and its own standing conditions. A user who is re-asked for a
    thesis has lost work; a user handed a sold cycle's thesis is being told
    something untrue about the position they hold.

    Nothing is asked here. The round trip is already in the ledger and the
    declaration agrees with it, so this passes through adoption in silence.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        with open(os.path.join(root, "ledger.jsonl"), "a", encoding="utf-8") as handle:
            for row in ({"type": "trade", "date": "2026-07-20", "ticker": "ACME",
                         "action": "sell", "qty": 100, "price": 14.0},
                        {"type": "trade", "date": "2026-07-20", "ticker": "ACME",
                         "action": "buy", "qty": 80, "price": 13.0}):
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        assert lg.derive_holdings(events)["holdings"]["ACME"]["cycle_id"] == \
            "ACME#2026-07-20#2", "the round trip opened a second cycle on one day"

        second = _snapshot(tmp, [{"ticker": "ACME", "shares": 80, "avg_cost": 13.0,
                                  "market": "US", "currency": "USD"},
                                 dict(KEPT[0], shares=48), KEPT[1]], as_of="2026-07-25")
        receipt = _cli(root, second)
        assert receipt["pending_confirmations"] == []
        assert _cli(root, second, {"refresh_id": receipt["refresh_id"],
                                   "answers": []})["status"] == "adopted"
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        book = lg.derive_holdings(events)
        assert book["holdings"]["ACME"]["cycle_id"] == "ACME#2026-07-20#2", (
            "ACME#2026-07-20#1 is the cycle the user sold; adopting a book may "
            "not hand its thesis, its conditions and its closed status to the "
            "position they are still holding")
        assert not book["integrity"]

        # And it settles: carrying a carried sequence changes nothing, so an
        # ordinary cadence of declarations cannot walk the identity anywhere.
        third = _snapshot(tmp, [{"ticker": "ACME", "shares": 80, "avg_cost": 13.0,
                                 "market": "US", "currency": "USD"},
                                dict(KEPT[0], shares=47), KEPT[1]], as_of="2026-07-30")
        again = _cli(root, third)
        _cli(root, third, {"refresh_id": again["refresh_id"], "answers": []})
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        assert lg.derive_holdings(events)["holdings"]["ACME"]["cycle_id"] == \
            "ACME#2026-07-20#2"
        stamped = {row["ticker"]: (row.get("since"), row.get("since_basis"))
                   for row in lg.latest_anchor(events)["positions"]}
        assert stamped["ACME"] == ("2026-07-20", "trade_event")
        assert stamped["WIDGET"] == ("2026-06-30", "snapshot_anchor"), (
            "a start the book only knows as a declaration date stays a lower "
            "bound however many declarations it survives; surviving is not "
            "evidence, and nothing may read it as a purchase date later")


def test_a_stamp_never_survives_onto_a_different_cycle():
    """The carry-forward's own limit, and it is not a hypothetical.

    A position sold and bought back is the same ticker and a different cycle.
    Carrying the stamp on ticker identity alone would report a position opened
    days ago as an eighteen-month holding — a number the user never said, in
    the field this issue exists to make honest. The record's own `origin`
    settles it: `trades` means the ledger has the real open date.

    #539 sharpened what "settles it" produces. The old carry-forward could only
    keep the wrong estimate or keep nothing, and keeping nothing dropped the
    position back to the new declaration's own date — still not the eighteen
    months, and still not the truth the ledger was holding. Now the real open
    date is what gets carried, stamped as the record's rather than the user's.
    The invariant under test is unchanged and is asserted twice below: the
    estimate does not survive, and the date the user never gave is not restated.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        newco = {"ticker": "NEWCO", "shares": 10, "avg_cost": 5.0,
                 "market": "US", "currency": "USD"}
        acme = {"ticker": "ACME", "shares": 100, "avg_cost": 12.0,
                "market": "US", "currency": "USD"}
        first = _snapshot(tmp, list(KEPT) + [acme, newco], as_of="2026-07-15")
        receipt = _cli(root, first)
        _cli(root, first, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "NEWCO", "classification": "confirmed", "held_months": 18}]})
        with open(os.path.join(root, "ledger.jsonl"), "a", encoding="utf-8") as handle:
            for row in ({"type": "trade", "date": "2026-07-18", "ticker": "NEWCO",
                         "action": "sell", "qty": 10, "price": 6.0},
                        {"type": "trade", "date": "2026-07-20", "ticker": "NEWCO",
                         "action": "buy", "qty": 6, "price": 7.0}):
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        assert lg.derive_holdings(events)["holdings"]["NEWCO"]["origin"] == "trades"
        second = os.path.join(tmp, "second.json")
        with open(second, "w", encoding="utf-8") as handle:
            # WIDGET also moves, below both thresholds, so the adopted book
            # really differs from the record and a new anchor is written --
            # without that the refresh reconciles clean, keeps the old anchor,
            # and this check would read the stamp it was meant to test for.
            json.dump({"as_of": "2026-07-25",
                       "positions": [dict(KEPT[0], shares=48), KEPT[1],
                                     acme, dict(newco, shares=6, avg_cost=7.0)]},
                      handle)
        again = _cli(root, second)
        assert again["pending_confirmations"] == []
        out = _cli(root, second, {"refresh_id": again["refresh_id"], "answers": []})
        assert out["status"] == "adopted", out
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        row = [p for p in lg.latest_anchor(events)["positions"] if p["ticker"] == "NEWCO"][0]
        assert row["since_basis"] == "trade_event", (
            "the estimate described the cycle that was sold; it must not be "
            "reattached to the one the ledger can date itself -- and the new "
            "cycle's start is labelled by the evidence that supports it")
        assert row["since"] == "2026-07-20", (
            "the rebuy is the start the ledger can prove, and it is the only "
            "start this position may carry")
        assert lg.derive_holdings(events)["holdings"]["NEWCO"]["cycle_id"] == \
            "NEWCO#2026-07-20#2", (
                "a rebought position opens its own cycle -- dated by the ledger "
                "rather than by whichever day the user next declared, and "
                "numbered so it is not the cycle it replaced")
        assert lg.derive_holdings(events)["holdings"]["NEWCO"]["cycle_id"] != "NEWCO#2025-01-15#1"


def test_an_appearance_now_routes_the_review_lane_here_too():
    """#530's refusal is a call into this lane, so widening what refresh asks
    widens the refusal with no rework over there. Before #531 this declaration
    reviewed straight through and NEWCO entered the book dated today, with
    nobody ever asked where it came from."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        before = _ledger_rows(root)
        positions = list(KEPT) + [
            {"ticker": "ACME", "shares": 100, "avg_cost": 12.0,
             "market": "US", "currency": "USD"},
            {"ticker": "NEWCO", "shares": 10, "avg_cost": 5.0,
             "market": "US", "currency": "USD"}]
        out = subprocess.run(
            [sys.executable, os.path.join(ENGINE, "review.py"), "prepare",
             "--route", "snapshot_review", "--root", root, "--language", "en",
             "--snapshot-json", _snapshot(tmp, positions)],
            capture_output=True, text=True, check=False)
        assert out.returncode == 2, out.stdout + out.stderr
        assert "refresh --snapshot-json" in json.loads(out.stdout)["error"], out.stdout
        assert _ledger_rows(root) == before, "the refusal happens before any append"


def test_a_supplied_view_may_not_hand_the_engine_a_cycle_start():
    """SKILL.md non-negotiable rule 1, enforced rather than documented.

    The agent transcribes a month count and the engine derives the date. An
    envelope that arrives with the date already in it is refused by name -- so
    the derivation cannot be quietly relocated into whatever composed the file.
    """
    with tempfile.TemporaryDirectory() as tmp:
        forged = [dict(row) for row in KEPT]
        forged[0] = dict(forged[0], since="2019-01-01", since_basis="user_estimate")
        out = _cli(_root(tmp), _snapshot(tmp, forged))
        assert out["status"] == "error", out
        assert "engine-assigned fields: since, since_basis" in out["error"], out


def test_the_two_provenance_fields_are_one_fact_and_travel_together():
    """A date with no stamp is the false precision rule 2 forbids, so the pair
    is validated as a pair at the only door into the book. This is what makes
    "never rendered as an exact date" true at the storage layer instead of a
    habit each renderer has to keep."""
    base = {"ticker": "ACME", "shares": 10, "market": "US", "currency": "USD"}
    cases = [
        (dict(base, since="2025-01-15"), "requires since_basis"),
        (dict(base, since_basis="user_estimate"), "no since was derived"),
        (dict(base, since_basis="unknown", since="2025-01-15"), "but a since was supplied"),
        (dict(base, since_basis="approximately"), "must be one of"),
        (dict(base, since="2026-09-01", since_basis="user_estimate"), "after the book's as_of"),
    ]
    for position, expected in cases:
        try:
            snapshot_adapter.normalize_book(
                {"as_of": "2026-07-15", "positions": [position]},
                today="2026-07-20", allow_engine_provenance=True)
        except snapshot_adapter.SnapshotError as exc:
            assert expected in str(exc), (position, exc)
            continue
        raise AssertionError(f"{position} must fail closed")


def test_only_the_refresh_lane_may_write_engine_assigned_provenance():
    """One privileged call site, and a mechanical count rather than a comment.

    `allow_engine_provenance` is a bypass of rule 1's gate. It is safe only
    while exactly one *implementation* uses it -- the one that assembles every
    value it passes, out of answers the engine itself converted or starts the
    record already held -- so the count is the check.

    #539/#536 made the reading precise rather than looser. Two lanes now adopt a
    book, and both reach `carry_recorded_starts`; what this counts is that
    neither grew its own way in. A second literal, in this file or any other,
    would be a second place a cycle start could be authored, which is the thing
    #531 forbids -- and it would be invisible to every other test here, because
    each lane's own behavior would look correct in isolation.
    """
    engine_files = sorted(name for name in os.listdir(ENGINE) if name.endswith(".py"))
    users = {}
    for name in engine_files:
        with open(os.path.join(ENGINE, name), encoding="utf-8") as handle:
            hits = handle.read().count("allow_engine_provenance=True")
        if hits:
            users[name] = hits
    assert users == {"book_refresh.py": 1}, (
        "a second writer of an engine-assigned cycle start is a second place "
        "the month count could be converted; put it behind build_adoption", users)


def test_a_large_change_on_a_core_position_asks_and_a_small_one_does_not():
    with tempfile.TemporaryDirectory() as tmp:
        # BIGCO is 8000 of a 10700 book; 200 -> 80 moves 4800/10700 = 45%.
        big = [{"ticker": "ACME", "shares": 100, "avg_cost": 12.0, "market": "US", "currency": "USD"},
               {"ticker": "WIDGET", "shares": 50, "avg_cost": 30.0, "market": "US", "currency": "USD"},
               {"ticker": "BIGCO", "shares": 80, "avg_cost": 40.0, "market": "US", "currency": "USD"}]
        receipt = _cli(_root(tmp), _snapshot(tmp, big))
        row = receipt["pending_confirmations"][0]
        assert row == {"kind": "large_change", "ticker": "BIGCO", "derived_shares": 200.0,
                       "declared_shares": 80.0, "weight": 0.747664, "delta_weight": 0.448598,
                       "options": ["confirmed", "resupply"]}
    with tempfile.TemporaryDirectory() as tmp:
        # WIDGET is 1500 of 10700; 50 -> 48 moves 60/10700 = 0.6%.
        small = [{"ticker": "ACME", "shares": 100, "avg_cost": 12.0, "market": "US", "currency": "USD"},
                 {"ticker": "WIDGET", "shares": 48, "avg_cost": 30.0, "market": "US", "currency": "USD"},
                 {"ticker": "BIGCO", "shares": 200, "avg_cost": 40.0, "market": "US", "currency": "USD"}]
        receipt = _cli(_root(tmp), _snapshot(tmp, small))
        assert receipt["pending_confirmations"] == [], (
            "a routine change must finalize without ceremony -- that is the ruling")
        assert receipt["summary"]["positions_changed"] == ["WIDGET"]


def test_a_sale_the_ledger_already_explains_is_never_asked_about():
    """This is what keeps the question rare rather than routine: an explained
    difference is not a difference at all, so nothing reaches the pending set."""
    with tempfile.TemporaryDirectory() as tmp:
        sell = {"type": "trade", "date": "2026-07-02", "ticker": "ACME", "action": "sell",
                "qty": 100, "price": 15.0, "market": "US", "currency": "USD"}
        receipt = _cli(_root(tmp, (SEED, sell)), _snapshot(tmp, KEPT))
        assert receipt["pending_confirmations"] == []
        assert receipt["summary"]["only_derived"] == []


def test_the_receipt_reads_the_book_through_the_declaration_s_own_window():
    """A snapshot is an end-of-day view. Every fact attached to its diff must be
    stated on the same day the diff was, or the user is shown a difference
    computed for July 15 with a share count from today.

    Only reachable when the ledger holds a trade newer than the snapshot --
    which is what a user creates by importing a fresh CSV alongside an older
    screenshot.
    """
    with tempfile.TemporaryDirectory() as tmp:
        later = {"type": "trade", "date": "2026-07-20", "ticker": "ACME", "action": "buy",
                 "qty": 50, "price": 20.0, "market": "US", "currency": "USD"}
        root = _root(tmp, (SEED, later))
        receipt = _cli(root, _snapshot(tmp, KEPT, as_of="2026-07-15"))
        row = receipt["pending_confirmations"][0]
        diff_row = [d for d in receipt["diff"]["positions"] if d["ticker"] == "ACME"][0]
        assert row["derived_shares"] == diff_row["derived"] == 100.0, (
            "the confirmation and the diff row it came from must state the same "
            "number; 150 here would be today's book, not the declaration's")


def test_a_root_with_no_history_at_all_is_routed_to_onboarding():
    """The name matters, and the old one was the defect (#549).

    This used to be called `test_a_root_with_no_recorded_book_is_routed_to_
    onboarding` while seeding an **empty** ledger -- so the test's own name
    asserted the false equivalence the code made: `latest_anchor is None`
    covered two different roots, an empty one and one holding trades, and only
    the empty half had an oracle. The trades-only half is the test below, and
    it now routes the other way.
    """
    with tempfile.TemporaryDirectory() as tmp:
        out = _cli(_root(tmp, ()), _snapshot(tmp, KEPT))
        assert out["status"] == "error" and "prepare --snapshot-json" in out["error"], out
        assert "no holdings yet" in out["error"], (
            "the refusal must describe the root it is actually shown to: this one "
            "really has nothing recorded")


def _trade(date, ticker, action, qty, price):
    return {"type": "trade", "date": date, "ticker": ticker, "action": action,
            "qty": qty, "price": price, "market": "US", "currency": "USD"}


def test_a_root_whose_book_came_from_trades_can_be_refreshed():
    """#549's whole point, and the case no test covered.

    A user onboards with a transaction CSV, gets a review, and later wants to
    correct the book with a holdings view -- the cheaper input #485 was written
    to support. Before this, both doors were locked and each pointed at the
    other: `refresh` said the root had no recorded book (it had trades and
    positions), and `prepare --route snapshot_review` said the history needed a
    reconciliation that could not be computed without one.

    The trades below are the same shape the ingest lane writes, plus the row it
    now writes beside them. WIDGET disappears from the view and NEWCO appears,
    so the lane raises exactly the two confirmations it exists to raise instead
    of refusing the user outright.
    """
    with tempfile.TemporaryDirectory() as tmp:
        trades = [_trade("2026-06-01", "ACME", "buy", 100, 12.0),
                  _trade("2026-06-02", "WIDGET", "buy", 50, 30.0)]
        recorded = lg.build_derived_book(trades, as_of="2026-06-30")
        assert recorded is not None and recorded["source"] == lg.DERIVED_BOOK_SOURCE
        root = _root(tmp, tuple(trades) + (recorded,))

        view = [{"ticker": "ACME", "shares": 100, "avg_cost": 12.0,
                 "market": "US", "currency": "USD"},
                {"ticker": "NEWCO", "shares": 7, "avg_cost": 20.0,
                 "market": "US", "currency": "USD"}]
        receipt = _cli(root, _snapshot(tmp, view))
        assert receipt["status"] == "pending_confirmation", receipt
        raised = {row["ticker"]: row for row in receipt["pending_confirmations"]}
        assert raised["WIDGET"]["kind"] == "disappearance"
        assert raised["WIDGET"]["cycle_id"] == "WIDGET#2026-06-02#1", (
            "the cycle that would close comes from the trades, not from the "
            "summary row -- a restatement is never the replay's base")
        assert raised["NEWCO"]["kind"] == "appearance"

        before = _ledger_rows(root)
        answers = {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "WIDGET", "classification": "sold"},
            {"ticker": "NEWCO", "classification": "confirmed", "held_months": 3}]}
        adopted = _cli(root, _snapshot(tmp, view), answers)
        assert adopted["status"] == "adopted", adopted
        assert adopted["recorded_absent"] == ["WIDGET"] and adopted["recorded_new"] == ["NEWCO"]
        after = _ledger_rows(root)
        assert after[:len(before)] == before, "adoption is append-only"
        assert lg.derive_holdings(after)["holdings"].keys() == {"ACME", "NEWCO"}


def test_an_unanswered_disappearance_on_a_trades_built_book_still_fails_closed():
    """The invariant #549 requires to survive: opening this lane to a book built
    from trades must not open a path where a disappearance is adopted without an
    answer. Same refusal as every other root, reached through the newly
    reachable one."""
    with tempfile.TemporaryDirectory() as tmp:
        trades = [_trade("2026-06-01", "ACME", "buy", 100, 12.0),
                  _trade("2026-06-02", "WIDGET", "buy", 50, 30.0)]
        root = _root(tmp, tuple(trades) + (lg.build_derived_book(trades, as_of="2026-06-30"),))
        view = [{"ticker": "ACME", "shares": 100, "avg_cost": 12.0,
                 "market": "US", "currency": "USD"}]
        receipt = _cli(root, _snapshot(tmp, view))
        before = _ledger_rows(root)
        out = _cli(root, _snapshot(tmp, view),
                   {"refresh_id": receipt["refresh_id"], "answers": []})
        assert out["status"] == "error", out
        assert _ledger_rows(root) == before, "a refusal writes nothing"


def test_a_root_with_a_recorded_book_routes_a_differing_view_back_to_this_lane():
    """The mirror image of the test above, and the pair is the whole point (#530).

    Onboarding and updating are different jobs with different entry points, and
    neither is reachable by accident: an empty root is sent from here to
    `prepare`, and an anchored root whose book has moved is sent from `prepare`
    back to here. Before this, the review lane accepted the differing view and
    adopted it at finalize -- so ACME, which is in the record and absent from
    the supplied view, left the book with nobody ever asked whether it was
    sold. This lane is the only one that asks, so it is the only one that can
    hand `append_book_adoption` an absence."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        before = _ledger_rows(root)
        out = subprocess.run(
            [sys.executable, os.path.join(ENGINE, "review.py"), "prepare",
             "--route", "snapshot_review", "--root", root, "--language", "en",
             "--snapshot-json", _snapshot(tmp, KEPT)],
            capture_output=True, text=True, check=False)
        assert out.returncode == 2, out.stdout + out.stderr
        assert "refresh --snapshot-json" in json.loads(out.stdout)["error"], out.stdout
        assert _ledger_rows(root) == before, "the refusal happens before any append"


def test_valuation_coverage_names_what_it_could_not_value():
    with tempfile.TemporaryDirectory() as tmp:
        seed = dict(SEED, positions=[
            {"ticker": "ACME", "shares": 100.0, "avg_cost": 12.0, "market": "US", "currency": "USD"},
            {"ticker": "DARK", "shares": 5.0, "market": "US", "currency": "USD"}])
        receipt = _cli(_root(tmp, (seed,)), _snapshot(
            tmp, [{"ticker": "ACME", "shares": 100, "avg_cost": 12.0,
                   "market": "US", "currency": "USD"}]))
        coverage = receipt["summary"]["valuation_coverage"]
        assert coverage["unavailable"] == ["DARK"], (
            "a holding with no supplied value and no recorded cost cannot be "
            "valued, and a bounded read must say so rather than count it as zero")
        assert coverage["valued"] == ["ACME"]


# ─────────────── B. answers are gated, and refusals write nothing ───────────────

def test_every_raised_item_must_be_answered():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        snapshot = _snapshot(tmp, KEPT)
        receipt = _cli(root, snapshot)
        before = _ledger_rows(root)
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": []})
        assert out["status"] == "error" and "unanswered confirmations: ACME" in out["error"]
        assert _ledger_rows(root) == before, "a refusal must write nothing"


def test_an_answer_nobody_asked_for_is_refused_by_name():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        snapshot = _snapshot(tmp, KEPT)
        receipt = _cli(root, snapshot)
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "WIDGET", "classification": "confirmed"}]})
        assert out["status"] == "error" and "WIDGET" in out["error"], out
        assert _ledger_rows(root) == [SEED]


def test_a_classification_the_kind_does_not_accept_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        snapshot = _snapshot(tmp, KEPT)
        receipt = _cli(root, snapshot)
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "ACME", "classification": "confirmed"}]})
        assert out["status"] == "error" and "not one of" in out["error"], out


def test_a_plan_prepared_against_a_book_that_moved_is_refused():
    """The dangerous shape is the one that still *looks* answerable.

    Adding to ACME between the two phases leaves the pending set identical in
    shape — one disappearance, same ticker, same options — so every other gate
    in this file still passes it. Only the frozen identity notices that the
    position the user agreed to close is no longer the position they were
    shown, and closing it would record an exit for 150 shares against an answer
    given about 100.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        snapshot = _snapshot(tmp, KEPT)
        receipt = _cli(root, snapshot)
        assert [row["derived_shares"] for row in receipt["pending_confirmations"]] == [100.0]
        with open(os.path.join(root, "ledger.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "trade", "date": "2026-07-01", "ticker": "ACME",
                                     "action": "buy", "qty": 50, "price": 13.0}) + "\n")
        fresh = _cli(root, snapshot)
        assert [row["ticker"] for row in fresh["pending_confirmations"]] == ["ACME"], (
            "the plan still looks answerable; that is what makes this the hazard")
        assert fresh["pending_confirmations"][0]["derived_shares"] == 150.0
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "ACME", "classification": "sold"}]})
        assert out["status"] == "error" and out["error"] == br.REFRESH_STALE, out
        assert [row["type"] for row in _ledger_rows(root)] == ["snapshot", "trade"]


def test_resupply_aborts_the_whole_refresh():
    """Not "adopt the parts that were fine". The supplied view is one artifact
    and a user who says it is wrong has invalidated all of it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        snapshot = _snapshot(tmp, KEPT)
        receipt = _cli(root, snapshot)
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "ACME", "classification": "resupply"}]})
        assert out["status"] == "resupply_requested" and out["tickers"] == ["ACME"]
        assert _ledger_rows(root) == [SEED], "nothing may be written on a resupply"


def test_a_confirmed_appearance_must_state_what_the_item_asked_for():
    """The gate that runs one level below the classification.

    A missing `held_months` is an unasked question, not a shrug — the shrug has
    its own spelling, `null`, and collapsing the two would let the flow skip the
    question and still adopt. In the other direction, a cost for a row that
    already declared one is refused rather than silently preferred: that is the
    prohibition `test_a_declared_market_value_never_becomes_an_undisclosed_cost_fallback`
    holds in `consider`, applied at the door instead.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        priced = {"ticker": "NEWCO", "shares": 10, "avg_cost": 5.0,
                  "market": "US", "currency": "USD"}
        acme = {"ticker": "ACME", "shares": 100, "avg_cost": 12.0,
                "market": "US", "currency": "USD"}
        snapshot = _snapshot(tmp, list(KEPT) + [acme, priced])
        receipt = _cli(root, snapshot)
        cases = [
            ({"ticker": "NEWCO", "classification": "confirmed"},
             "held_months is required"),
            ({"ticker": "NEWCO", "classification": "confirmed",
              "held_months": 6, "avg_cost": 9.0}, "did not ask for avg_cost"),
            ({"ticker": "NEWCO", "classification": "confirmed", "held_months": 1.5},
             "whole number of months"),
            ({"ticker": "NEWCO", "classification": "confirmed", "held_months": -3},
             "must be between 0 and"),
            ({"ticker": "NEWCO", "classification": "confirmed", "held_months": "18"},
             "whole number of months"),
        ]
        for answer, expected in cases:
            out = _cli(root, snapshot,
                       {"refresh_id": receipt["refresh_id"], "answers": [answer]})
            assert out["status"] == "error" and expected in out["error"], (answer, out)
        assert _ledger_rows(root) == [SEED], "every refusal writes nothing"


def test_an_appearance_with_no_declared_cost_must_be_answered_with_one():
    """#528's input class, closed at entry. `null` stays legal — a user who does
    not know their cost must not be pushed into inventing one — but the flow
    cannot reach adoption without having put the question to them."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        acme = {"ticker": "ACME", "shares": 100, "avg_cost": 12.0,
                "market": "US", "currency": "USD"}
        snapshot = _snapshot(tmp, list(KEPT) + [
            acme, {"ticker": "DARKCO", "shares": 4, "market": "US", "currency": "USD"}])
        receipt = _cli(root, snapshot)
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "DARKCO", "classification": "confirmed", "held_months": 3}]})
        assert out["status"] == "error" and "avg_cost is required" in out["error"], out
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "DARKCO", "classification": "confirmed",
             "held_months": 3, "avg_cost": None}]})
        assert out["status"] == "adopted", out
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        row = [p for p in lg.latest_anchor(events)["positions"] if p["ticker"] == "DARKCO"][0]
        assert "avg_cost" not in row, "a cost the user does not know is not manufactured"
        assert row["since"] == "2026-04-15"


def test_resupply_on_an_appearance_aborts_and_carries_no_detail():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        acme = {"ticker": "ACME", "shares": 100, "avg_cost": 12.0,
                "market": "US", "currency": "USD"}
        snapshot = _snapshot(tmp, list(KEPT) + [
            acme, {"ticker": "NEWCO", "shares": 10, "avg_cost": 5.0,
                   "market": "US", "currency": "USD"}])
        receipt = _cli(root, snapshot)
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "NEWCO", "classification": "resupply", "held_months": 6}]})
        assert out["status"] == "error" and "did not ask for held_months" in out["error"], out
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "NEWCO", "classification": "resupply"}]})
        assert out["status"] == "resupply_requested" and out["tickers"] == ["NEWCO"]
        assert _ledger_rows(root) == [SEED]


# ─────────────── C. the two branches reach different states ───────────────

def test_sold_removes_the_position_and_records_a_fill_free_absence():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        snapshot = _snapshot(tmp, KEPT)
        receipt = _cli(root, snapshot)
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "ACME", "classification": "sold"}]})
        assert out["status"] == "adopted"
        assert out["recorded_absent"] == ["ACME"] and out["carried_forward"] == []
        rows = _ledger_rows(root)
        assert [row["type"] for row in rows] == [
            "snapshot", "adjustment", "position_absence", "snapshot"], (
            "order is load-bearing: revisit reads the book as it stood BEFORE "
            "each absence, so an absence after its anchor would see nothing")
        absence = rows[2]
        assert absence["ticker"] == "ACME" and absence["date"] == "2026-07-15"
        assert not set(absence) & lg.ABSENCE_FORBIDDEN_KEYS, (
            "the exit price and date are unknown; the engine may not invent a fill")
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        assert "ACME" not in lg.derive_holdings(events)["holdings"]
        exits = {row["ticker"]: row for row in rv.detect_exits(events)}
        assert exits["ACME"]["exit_price"] is None and exits["ACME"]["kind"] == "full"
        assert exits["ACME"]["cost_basis"] == 1200.0


def test_not_captured_carries_the_position_forward_with_its_provenance():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        # WIDGET also moved, below both thresholds, so the adopted book really
        # does differ from the record and a new anchor is written.
        supplied = [{"ticker": "WIDGET", "shares": 48, "avg_cost": 30.0,
                     "market": "US", "currency": "USD"},
                    {"ticker": "BIGCO", "shares": 200, "avg_cost": 40.0,
                     "market": "US", "currency": "USD"}]
        snapshot = _snapshot(tmp, supplied)
        receipt = _cli(root, snapshot)
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "ACME", "classification": "not_captured"}]})
        assert out["status"] == "adopted"
        assert out["carried_forward"] == ["ACME"] and out["recorded_absent"] == []
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        assert not [row for row in events if row.get("type") == "position_absence"], (
            "a capture gap is not an exit and must leave no exit record")
        holdings = lg.derive_holdings(events)["holdings"]
        assert holdings["ACME"]["shares"] == 100.0, "the position stays in the book"
        assert holdings["WIDGET"]["shares"] == 48.0, "the change that was real is adopted"
        anchor = lg.latest_anchor(events)
        carried = [row for row in anchor["positions"] if row.get("carried")]
        assert [row["ticker"] for row in carried] == ["ACME"], (
            "the adopted book states which rows came from the record rather "
            "than from the view the user supplied")
        assert carried[0]["avg_cost"] == 12.0, "copied from the record, never invented"


def test_a_carried_position_keeps_the_canonical_current_book_usable():
    """The cross-lane oracle this slice shipped without.

    `carried` is a new field on an anchor position, and `portfolio_basis` --
    a different lane entirely, which the refresh flow never calls -- validates
    anchor positions against a strict key whitelist. Adding the field there
    made `query_current_book` return None for any book the refresh lane had
    carried a position into, which took `consider` down with it: the refresh
    lane's headline feature silently broke the book it had just written.

    The whole offline suite was green while that was true, because every
    refresh test read the ledger through `derive_holdings` and no test crossed
    from one lane into the other. That is the surface-listing rule in
    docs/development-guide.md, missed: a new field on a shared artifact reaches
    every reader of that artifact, not only the writer's own.
    """
    import portfolio_basis
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        supplied = [{"ticker": "WIDGET", "shares": 48, "avg_cost": 30.0,
                     "market": "US", "currency": "USD"},
                    {"ticker": "BIGCO", "shares": 200, "avg_cost": 40.0,
                     "market": "US", "currency": "USD"}]
        snapshot = _snapshot(tmp, supplied)
        receipt = _cli(root, snapshot)
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "ACME", "classification": "not_captured"}]})
        assert out["carried_forward"] == ["ACME"]
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        basis = portfolio_basis.query_current_book(events, reference_as_of="2026-07-20")
        assert basis is not None, (
            "a carried position must not make the canonical current book unreadable")
        assert basis.current_book["holdings"]["ACME"]["shares"] == 100.0
        projection = portfolio_basis.sizing_projection(basis)
        assert projection is not None and projection.applicable


def test_a_stamped_position_keeps_the_canonical_current_book_usable():
    """The cross-lane oracle `carried` taught this repo to write (#485 Slice C).

    `since`/`since_basis` are new fields on an anchor position, and a new field
    on a shared artifact reaches every reader of that artifact, not only the
    writer's own. `portfolio_basis` validates anchor positions and holdings
    against exact key sets and requires `since` to be a real date — miss any of
    that and `query_current_book` returns None, which takes `consider` down
    with it while every refresh test stays green.

    Also pinned: where the stamp stops. `_normalized_anchor` drops it, so a
    `current_book.anchor` position still carries only the book-affecting facts,
    exactly as it drops `carried`. It does *not* stop at the holdings, and that
    difference is the point rather than an oversight — `carried` says how a row
    was obtained, while a cycle start says when the position opened, and the
    derived `cycle_id` that every holding-period reading measures from is
    supposed to move when the user answers.
    """
    import portfolio_basis
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        acme = {"ticker": "ACME", "shares": 100, "avg_cost": 12.0,
                "market": "US", "currency": "USD"}
        supplied = list(KEPT) + [
            acme,
            {"ticker": "NEWCO", "shares": 10, "avg_cost": 5.0,
             "market": "US", "currency": "USD"},
            {"ticker": "OLDCO", "shares": 8, "avg_cost": 3.0,
             "market": "US", "currency": "USD"}]
        snapshot = _snapshot(tmp, supplied)
        receipt = _cli(root, snapshot)
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "NEWCO", "classification": "confirmed", "held_months": 30},
            {"ticker": "OLDCO", "classification": "confirmed", "held_months": None}]})
        assert out["status"] == "adopted", out
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        basis = portfolio_basis.query_current_book(events, reference_as_of="2026-07-20")
        assert basis is not None, (
            "a stamped position must not make the canonical current book "
            "unreadable -- that is what `consider` reads")
        assert basis.current_book["holdings"]["OLDCO"]["cycle_id"] == "OLDCO#unknown"
        projection = portfolio_basis.sizing_projection(basis)
        assert projection is not None and projection.applicable
        stamped = {row["ticker"]: row.get("since_basis")
                   for row in lg.latest_anchor(events)["positions"]}
        # #539: every adopted position states where its start came from, and the
        # basis says what the date is worth. The two the user was just asked
        # about keep their own answers; the three already on record keep the
        # start the record held -- which is what stops their cycle ids reminting
        # -- labelled as the lower bound a declaration date is, not upgraded.
        assert stamped == {"NEWCO": "user_estimate", "OLDCO": "unknown",
                           "ACME": "snapshot_anchor", "WIDGET": "snapshot_anchor",
                           "BIGCO": "snapshot_anchor"}
        for row in basis.current_book["anchor"]["positions"]:
            assert not {"since", "since_basis"} & set(row), (
                "the anchor projection carries book-affecting facts only; "
                "provenance is dropped there the way `carried` is")


def test_one_declaration_owns_which_fields_a_supplied_position_may_carry():
    """The fix for the above is a single declaration, not a third mirror.

    `carried` had to be accepted by three separate hand-written whitelists.
    Adding it to one and missing two is what shipped. The supplier-side ones
    now read `ledger.SNAPSHOT_POSITION_KEYS`, so the next field added to a
    position cannot be accepted by one lane and rejected by another.

    The basis lane's *other* whitelist is deliberately not unified with it: it
    validates what `_normalized_anchor` emits, which drops provenance on
    purpose so two declarations of the same book share one state_version. Two
    different facts, two sets, and the difference is stated where each lives.
    """
    import portfolio_basis
    assert snapshot_adapter.POSITION_KEYS == set(lg.SNAPSHOT_POSITION_KEYS)
    assert "carried" in lg.SNAPSHOT_POSITION_KEYS
    assert "carried" not in portfolio_basis._NORMALIZED_POSITION_KEYS, (
        "provenance must stay out of the book's own identity")
    anchor = {"type": "snapshot", "as_of": "2026-07-15", "source": "user_declared",
              "is_complete": True,
              "positions": [{"ticker": "ACME", "shares": 10, "avg_cost": 1.0,
                             "market": "US", "currency": "USD", "carried": "yes"}]}
    basis = portfolio_basis.query_current_book([anchor], reference_as_of="2026-07-20")
    assert basis is None or "ACME" not in (basis.current_book.get("holdings") or {}), (
        "a non-boolean carried flag must not be adopted as a book fact")


def test_carrying_everything_back_leaves_the_book_untouched():
    """When the only difference was the capture gap, the adopted book equals the
    recorded one — so the refresh records a clean reconciliation and writes no
    new anchor, exactly as the review lane's `reconciled` status does. Writing a
    fresh anchor here would mint a new content-addressed identity for a book
    that did not change."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        snapshot = _snapshot(tmp, KEPT)
        receipt = _cli(root, snapshot)
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "ACME", "classification": "not_captured"}]})
        assert out["status"] == "adopted" and out["reconciliation"] == "reconciled"
        rows = _ledger_rows(root)
        assert [row["type"] for row in rows] == ["snapshot", "reconciliation"]
        assert rows[1]["date"] == "2026-07-15", (
            "the mark records that the book was verified against a July view")
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        assert lg.derive_holdings(events)["holdings"]["ACME"]["shares"] == 100.0


def test_the_same_refresh_can_be_prepared_twice_and_adopts_once():
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        snapshot = _snapshot(tmp, KEPT)
        first = _cli(root, snapshot)
        second = _cli(root, snapshot)
        assert first["refresh_id"] == second["refresh_id"], (
            "preparing is read-only, so it must converge on the same identity")
        _cli(root, snapshot, {"refresh_id": first["refresh_id"], "answers": [
            {"ticker": "ACME", "classification": "sold"}]})
        after = _cli(root, snapshot)
        assert after["status"] == "ready" and after["pending_confirmations"] == []
        assert after["summary"]["status"] == "reconciled", (
            "the adopted book matches the supplied one, so a re-run has nothing "
            "left to ask or to change")


def test_a_same_day_refresh_wins_over_the_earlier_declaration():
    """The projection sequence is what makes this work; without it
    ``latest_anchor``'s file-order tie-break could keep the older same-day row."""
    with tempfile.TemporaryDirectory() as tmp:
        seed = dict(SEED, as_of="2026-07-15", snapshot_id="snapshot-sameday00000000")
        root = _root(tmp, (seed,))
        snapshot = _snapshot(tmp, KEPT, as_of="2026-07-15")
        receipt = _cli(root, snapshot)
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "ACME", "classification": "sold"}]})
        assert out["status"] == "adopted"
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        assert "ACME" not in lg.derive_holdings(events)["holdings"]
        assert lg.latest_anchor(events)["projection_sequence"] == 1


# ─────────────── D. one question, whatever it covers ───────────────

def test_every_kind_at_once_is_one_receipt_settled_by_one_answers_call():
    """The one-question rule, proven at the only layer the engine controls.

    `flows/book-refresh.md` step 2 is prose an agent obeys, but what makes it
    obeyable is structural: the engine emits ONE receipt with ONE flat
    `pending_confirmations` list, and ONE `--answers` call settles all of it.
    There is no per-ticker question object, no queue, and no protocol that
    forces a second turn — so eight appearances cannot become eight questions
    without the agent inventing the extra turns itself.

    Nine raised items across all three kinds, which before #531 would have been
    six: the three appearances are the ones this issue added, and they must not
    have added a single round trip.
    """
    with tempfile.TemporaryDirectory() as tmp:
        seed = dict(SEED, positions=[
            {"ticker": "BIGCO", "shares": 200.0, "avg_cost": 40.0,
             "market": "US", "currency": "USD"},
            {"ticker": "GONE1", "shares": 10.0, "avg_cost": 4.0,
             "market": "US", "currency": "USD"},
            {"ticker": "GONE2", "shares": 10.0, "avg_cost": 4.0,
             "market": "US", "currency": "USD"},
            {"ticker": "GONE3", "shares": 10.0, "avg_cost": 4.0,
             "market": "US", "currency": "USD"}])
        root = _root(tmp, (seed,))
        supplied = [{"ticker": "BIGCO", "shares": 80, "avg_cost": 40.0,
                     "market": "US", "currency": "USD"},
                    {"ticker": "NEW1", "shares": 5, "avg_cost": 2.0,
                     "market": "US", "currency": "USD"},
                    {"ticker": "NEW2", "shares": 5, "market": "US", "currency": "USD"},
                    {"ticker": "NEW3", "shares": 5, "avg_cost": 2.0,
                     "market": "US", "currency": "USD"}]
        snapshot = _snapshot(tmp, supplied)
        receipt = _cli(root, snapshot)
        raised = [(row["kind"], row["ticker"]) for row in receipt["pending_confirmations"]]
        assert raised == [
            ("disappearance", "GONE1"), ("disappearance", "GONE2"),
            ("disappearance", "GONE3"),
            ("appearance", "NEW1"), ("appearance", "NEW2"), ("appearance", "NEW3"),
            ("large_change", "BIGCO")], raised
        assert set(receipt) == {"schema_version", "refresh_id", "as_of", "against",
                                "status", "diff", "pending_confirmations", "summary"}, (
            "the receipt has exactly one place a question can come from; a "
            "second question-bearing field is how one turn becomes many")
        out = _cli(root, snapshot, {"refresh_id": receipt["refresh_id"], "answers": [
            {"ticker": "GONE1", "classification": "sold"},
            {"ticker": "GONE2", "classification": "sold"},
            {"ticker": "GONE3", "classification": "not_captured"},
            {"ticker": "NEW1", "classification": "confirmed", "held_months": 2},
            {"ticker": "NEW2", "classification": "confirmed",
             "held_months": None, "avg_cost": 1.5},
            {"ticker": "NEW3", "classification": "confirmed", "held_months": 36},
            {"ticker": "BIGCO", "classification": "confirmed"}]})
        assert out["status"] == "adopted", out
        assert out["recorded_absent"] == ["GONE1", "GONE2"]
        assert out["carried_forward"] == ["GONE3"]
        assert out["recorded_new"] == ["NEW1", "NEW2", "NEW3"]
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        holdings = lg.derive_holdings(events)["holdings"]
        assert holdings["NEW1"]["cycle_id"] == "NEW1#2026-05-15#1"
        assert holdings["NEW2"]["cycle_id"] == "NEW2#unknown"
        assert holdings["NEW3"]["cycle_id"] == "NEW3#2023-07-15#1", (
            "two months and thirty-six months are what say which was bought and "
            "which was carried in; the provenance label the body proposed is "
            "deleted, not stored elsewhere")


def test_step_2_offers_every_kind_the_engine_can_raise():
    """The prose and the enum, locked together.

    A kind the engine raises and the flow never mentions is a question the
    agent has no wording for and no legal answers to offer -- so it improvises,
    which is the one thing step 2 forbids.
    """
    with open(FLOW, encoding="utf-8") as handle:
        step_2 = handle.read().split("## Step 2")[1].split("\n## ")[0]
    for kind in br.CONFIRMATION_KINDS:
        assert f'`kind: "{kind}"`' in step_2, f"step 2 never tells the agent how to ask {kind}"
        for option in br.CLASSIFICATIONS_BY_KIND[kind]:
            assert f"`{option}`" in step_2, f"{kind}'s option {option} is unwritten"


# ─────────────── E. contract synchronization ───────────────

def test_the_classification_enum_matches_the_engine_constant():
    """`review.CONSIDER_DECISIONS`' precedent: one tuple, locked to the schema,
    so the CLI, the schema and the flow document cannot disagree about what an
    answer is."""
    with open(SCHEMA, encoding="utf-8") as handle:
        schema = json.load(handle)
    declared = schema["$defs"]["classification"]["enum"]
    assert tuple(declared) == br.REFRESH_CLASSIFICATIONS, (declared, br.REFRESH_CLASSIFICATIONS)
    kinds = schema["$defs"]["pending_confirmation"]["properties"]["kind"]["enum"]
    assert tuple(kinds) == br.CONFIRMATION_KINDS
    assert set(br.CLASSIFICATIONS_BY_KIND) == set(br.CONFIRMATION_KINDS)
    for kind, allowed in br.CLASSIFICATIONS_BY_KIND.items():
        unknown = set(allowed) - set(br.REFRESH_CLASSIFICATIONS)
        assert not unknown, f"{kind} allows an undeclared classification: {sorted(unknown)}"
    answer = (schema["$defs"]["input"]["properties"]["answers"]["items"]["properties"])
    assert answer["held_months"]["maximum"] == br.HELD_MONTHS_MAX
    # #539: four evidence classes, kept separable rather than collapsed into one
    # "carried" marker. What a carried date is worth cannot be recovered later --
    # after adoption `origin` describes the snapshot writer, not the evidence.
    assert set(lg.SINCE_BASES) == {"user_estimate", "unknown",
                                   "trade_event", "snapshot_anchor"}
    assert lg.ENGINE_ASSIGNED_POSITION_KEYS <= lg.SNAPSHOT_POSITION_KEYS, (
        "an engine-assigned field still has to be a field a position may carry")


def test_no_engine_module_reads_is_complete_any_more():
    """#549 removed the flag outright, so the old single-module guard is now a
    repo-wide one. A reader coming back anywhere in the engine would revive the
    same defect this issue reports: a declaration that disqualifies itself, and
    a book that silently stops updating."""
    import ast
    offenders = []
    for name in sorted(os.listdir(ENGINE)):
        if not name.endswith(".py") or name.startswith("test_"):
            continue
        source = open(os.path.join(ENGINE, name), encoding="utf-8").read()
        for node in ast.walk(ast.parse(source)):
            # An exact string constant is how this field would be read or
            # written -- `event["is_complete"]`, `.get("is_complete")`, a dict
            # literal key. Prose that merely names the removed flag mentions it
            # as a substring of a longer docstring and is not a reader, which is
            # why this parses rather than greps.
            if isinstance(node, ast.Constant) and node.value == "is_complete":
                offenders.append(f"{name}:{node.lineno}")
    assert not offenders, "is_complete is gone; these read or write it: " + ", ".join(offenders)


def test_the_adapter_validator_is_shared_not_reimplemented():
    """A carried row must not enter the book by a path that skips validation."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _root(tmp)
        events, _ = lg.load_ledger(os.path.join(root, "ledger.jsonl"))
        snapshot, anchor = snapshot_adapter.normalize_book(
            {"as_of": "2026-07-15", "positions": KEPT}, today="2026-07-20")
        receipt = br.plan_refresh(events, snapshot, anchor)
        try:
            br.build_adoption(receipt, events, snapshot,
                              dict(anchor, positions=[{"ticker": "ACME", "shares": -5,
                                                       "market": "US", "currency": "USD"}]),
                              [{"ticker": "ACME", "classification": "not_captured"}])
        except br.RefreshError as exc:
            assert "failed validation" in str(exc), exc
            return
        raise AssertionError("an invalid adopted book must fail closed")


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
