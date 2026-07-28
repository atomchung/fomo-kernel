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
import book_refresh as br  # noqa: E402
import ledger as lg  # noqa: E402
import revisit as rv  # noqa: E402
import snapshot_adapter  # noqa: E402

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


def test_an_appearance_is_adopted_without_a_question():
    """Owner ruling: the directions are not symmetric. A disappearance removes
    recorded history and is confirmed; an appearance removes nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        positions = list(KEPT) + [
            {"ticker": "ACME", "shares": 100, "avg_cost": 12.0, "market": "US", "currency": "USD"},
            {"ticker": "NEWCO", "shares": 10, "avg_cost": 5.0, "market": "US", "currency": "USD"}]
        receipt = _cli(_root(tmp), _snapshot(tmp, positions))
        assert receipt["pending_confirmations"] == []
        assert receipt["status"] == "ready"
        assert receipt["summary"]["only_declared"] == ["NEWCO"], (
            "the appearance is still disclosed, it just costs no question")


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


def test_a_root_with_no_recorded_book_is_routed_to_onboarding():
    with tempfile.TemporaryDirectory() as tmp:
        out = _cli(_root(tmp, ()), _snapshot(tmp, KEPT))
        assert out["status"] == "error" and "prepare --snapshot-json" in out["error"], out


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


# ─────────────── D. contract synchronization ───────────────

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


def test_the_refresh_lane_never_reads_is_complete():
    """Owner ruling 1: legacy `is_complete:false` rows stay historical slices and
    never become anchors retroactively. The lane that maintains the current book
    must not resurrect the concept the rest of M0 spent four PRs removing."""
    with open(os.path.join(ENGINE, "book_refresh.py"), encoding="utf-8") as handle:
        source = handle.read()
    body = source.split('"""', 2)[-1]      # skip the module docstring, which names it
    assert "is_complete" not in body, "book_refresh must not read or write is_complete"


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
