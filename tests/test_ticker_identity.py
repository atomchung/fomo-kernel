#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One canonical ticker identity, shared by arithmetic and recall (#803).

The defect this suite exists to keep dead: `consequence._ticker` stripped but
did not case-fold, so a premise written `aaa` against a held `AAA` was a
*different instrument* to the engine. The consequences were arithmetic, not
cosmetic — the book grew a fictional fourth holding, the position being added
to had its displayed weight go **down**, a sell was refused as "not currently
held", and `_prior_decision` (#609) lost the user's own earlier consultation of
the same name. Nothing disclosed any of it; the answer was delivered as fully
priced and valid.

Every case here runs through the real `review.py consider` CLI unless it is
about a function the CLI has no way to reach directly, because the shipped
defect lived in the wiring between four modules that each looked correct on its
own. Fictional symbols throughout (AAA/BBB/CCC/9999.TW/1234): this repository is
public.

Where the rest of the contract is proved:
- byte-compatibility for already-canonical input is the whole of the existing
  registry — `tests/test_consequence.py`, `tests/test_consider.py`,
  `tests/test_ledger.py`, `tests/test_portfolio_basis.py` and the rest pin
  literal weights, `state_version` digests and `evaluation_id` hashes over
  canonical fixtures, so a rule that moved a canonical book turns them red.
  Section F below pins the one comparison those cannot make: that two spellings
  of one premise produce the *same* frozen identity.
"""
import datetime as dt
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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_posture  # noqa: E402
offline_posture.apply()
import consequence as consequence_engine  # noqa: E402
import ledger as ledger_engine  # noqa: E402
import market_data as market_data_engine  # noqa: E402
import portfolio_basis as portfolio_basis_engine  # noqa: E402
import price_feed as price_feed_engine  # noqa: E402
import review as review_engine  # noqa: E402
import revisit as revisit_engine  # noqa: E402
import thesis as thesis_engine  # noqa: E402
import splits as split_engine  # noqa: E402
import trade_recap as tr_engine  # noqa: E402
import symbols  # noqa: E402


# ─────────────────────────────── helpers ───────────────────────────────

def _run(*args):
    return subprocess.run([sys.executable, str(REVIEW), *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)


def _ok(run):
    assert run.returncode == 0, f"expected success, got {run.returncode}: {run.stdout}{run.stderr}"
    return json.loads(run.stdout)


def _fails(run, fragment):
    assert run.returncode != 0, f"expected failure, got exit 0: {run.stdout}"
    payload = json.loads(run.stdout)
    assert payload.get("status") == "error", f"expected a status:error payload, got {payload}"
    assert fragment in payload["error"], f"wanted {fragment!r} in error, got {payload['error']!r}"
    return payload


def _snapshot(as_of, positions):
    return {"type": "snapshot", "as_of": as_of, "source": ledger_engine.DECLARED_BOOK_SOURCE,
            "positions": positions}


def _position(ticker, shares, avg_cost, currency="USD", market="US"):
    return {"ticker": ticker, "shares": shares, "avg_cost": avg_cost,
            "market": market, "currency": currency}


def _trade(date, ticker, action, qty, price, currency="USD", market="US"):
    return {"type": "trade", "date": date, "ticker": ticker, "action": action,
            "qty": qty, "price": price, "market": market, "currency": currency}


def _write_ledger(root, events):
    path = os.path.join(root, "ledger.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return path


_BOOK = [_snapshot("2026-01-01", [_position("AAA", 100, 100.0),
                                  _position("BBB", 100, 50.0),
                                  _position("CCC", 100, 30.0)])]


def _consider(root, premise, *extra):
    return _run("consider", "--root", root, "--premise", json.dumps(premise), *extra)


def _evaluation(root, premise, *extra):
    return _ok(_consider(root, premise, *extra))["evaluation"]


# ── A. the primitive ──

def test_the_rule_is_surrounding_whitespace_and_case_and_nothing_else():
    for raw, expected in [("aaa", "AAA"), ("  aaa  ", "AAA"), ("AAA", "AAA"),
                          ("\tAaA\n", "AAA")]:
        assert symbols.canonical_ticker(raw) == expected, raw
    # Idempotent, or "canonical" would depend on how many readers had seen it.
    for raw in ("aaa", " 9999.tw ", "1234", "BRK-B", "^ZZZ"):
        once = symbols.canonical_ticker(raw)
        assert symbols.canonical_ticker(once) == once, raw
        assert symbols.is_canonical(once) and not symbols.is_canonical(f" {once} ")


def test_exchange_suffixes_fold_and_numeric_stems_survive_unchanged():
    """#803's acceptance: an exchange suffix is part of the identity and folds
    with it; a numeric symbol means the same thing after canonicalization as
    before, because upper-casing digits is the identity function."""
    assert symbols.canonical_ticker("9999.tw") == "9999.TW"
    assert symbols.canonical_ticker("9999.TW") == "9999.TW"
    assert symbols.canonical_ticker("1234") == "1234"
    assert symbols.canonical_ticker("1234.hk") == "1234.HK"
    # Not an alias map, deliberately (#803 non-goals): two listings of one
    # company stay two instruments.
    assert symbols.canonical_ticker("zzz") != symbols.canonical_ticker("9999.tw")


def test_a_missing_ticker_is_none_rather_than_an_exception_or_an_empty_symbol():
    """Callers keep their own "missing ticker" error. A primitive that raised
    would move that decision here, and one that returned "" would let an empty
    string become a dict key in a holdings map."""
    for empty in (None, "", "   ", 123, [], {"ticker": "AAA"}):
        assert symbols.canonical_ticker(empty) is None, empty


# ── B. the premise no longer splits the book ──

def test_a_lower_case_buy_adds_to_the_held_position_instead_of_inventing_one():
    """The reproduced defect, on the ledger route. Before #803: n_holdings 3→4,
    a phantom `aaa` holding, and `AAA`'s own weight falling because the trade
    that increased it was counted as a different instrument."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(tmp, _BOOK)
        row = _evaluation(tmp, {"ticker": "aaa", "side": "buy", "price": 120.0, "qty": 10})
        before, after = row["consequence"]["before"], row["consequence"]["after"]
        assert row["premise"]["ticker"] == "AAA"
        assert after["n_holdings"] == before["n_holdings"] == 3, (
            "a lower-case add opened a second position beside the one it was adding to")
        assert set(after["held"]) == {"AAA", "BBB", "CCC"}
        assert after["held"]["AAA"]["shares"] == before["held"]["AAA"]["shares"] + 10
        assert after["weights"]["AAA"] > before["weights"]["AAA"], (
            "adding to a position must not reduce its displayed weight")


def test_a_lower_case_sell_targets_the_held_position_rather_than_failing_as_unheld():
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(tmp, _BOOK)
        row = _evaluation(tmp, {"ticker": "aaa", "side": "sell", "price": 120.0, "qty": 10})
        after = row["consequence"]["after"]
        assert after["held"]["AAA"]["shares"] == 90.0
        assert after["n_holdings"] == 3


def test_the_csv_route_reaches_the_same_identity_as_the_ledger_route():
    """Both supported book sources, because the fix has to hold wherever the
    rows came from — a premise canonicalized against a book that was not would
    simply move the split rather than close it."""
    import csv
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "trades.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Symbol", "Quantity", "Price", "Action", "TradeDate", "RecordType"])
            for symbol, qty, price in [("AAA", 100, 100.0), ("BBB", 100, 50.0)]:
                writer.writerow([symbol, qty, price, "BUY", "2026-01-02", "Trade"])
        row = _ok(_run("consider", path, "--root", tmp,
                       "--premise", json.dumps({"ticker": "aaa", "side": "buy",
                                                "price": 120.0, "qty": 10})))["evaluation"]
        after = row["consequence"]["after"]
        assert set(after["held"]) == {"AAA", "BBB"}
        assert after["held"]["AAA"]["shares"] == 110.0


def test_a_lower_case_csv_book_is_still_the_book_the_premise_is_about():
    """The regression canonicalizing the premise *alone* would have created.
    `trade_recap.load` keeps the spelling the export used — a broker file whose
    Symbol column is lower case would then hold `aaa` while every premise
    resolved to `AAA`, and the split would simply have moved rather than
    closed: the sell below would come back "not currently held" for a position
    plainly in the file. Both premise spellings, because neither may depend on
    the user matching the file's own casing.
    """
    import csv
    for premise_ticker in ("aaa", "AAA"):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trades.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Symbol", "Quantity", "Price", "Action", "TradeDate",
                                 "RecordType"])
                writer.writerow(["aaa", 100, 100.0, "BUY", "2026-01-02", "Trade"])
                writer.writerow(["bbb", 100, 50.0, "BUY", "2026-01-02", "Trade"])
                writer.writerow(["AAA", 20, 110.0, "BUY", "2026-01-03", "Trade"])
            row = _ok(_run("consider", path, "--root", tmp,
                           "--premise", json.dumps({"ticker": premise_ticker, "side": "sell",
                                                    "price": 120.0, "qty": 30})))["evaluation"]
            before, after = row["consequence"]["before"], row["consequence"]["after"]
            assert set(before["held"]) == {"AAA", "BBB"}, (
                f"the CSV book split under {premise_ticker!r}: {sorted(before['held'])}")
            assert before["held"]["AAA"]["shares"] == 120.0, (
                "two spellings of one instrument's own fills must be one position")
            assert after["held"]["AAA"]["shares"] == 90.0
            assert after["n_holdings"] == 2


def test_the_average_down_rule_is_judged_against_the_same_book_the_weights_are():
    """`rule_collision` reads `rows` a second time, directly, for the
    average-down count — the one rule whose reading does not come out of the
    consequence. A book left uncanonicalized on that path only would have the
    concentration rules judged against a merged position while this one saw the
    premise as a brand-new name, and the user's own rule would come back
    `clear` on a trade that breaks it.
    """
    import csv
    # Deliberately straddling: buying at 100 is an average-down against the two
    # fills combined (average cost 125) and is *not* one against the `AAA`-spelled
    # fill alone (cost 50). A book left uncanonicalized on this one path
    # therefore returns `compliant` for a trade that breaks the user's rule.
    for spellings, expected in [(("AAA", "AAA"), "new_breach"), (("AAA", "aaa"), "new_breach")]:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "trades.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Symbol", "Quantity", "Price", "Action", "TradeDate",
                                 "RecordType"])
                writer.writerow([spellings[0], 100, 50.0, "BUY", "2026-01-02", "Trade"])
                writer.writerow([spellings[1], 100, 200.0, "BUY", "2026-01-03", "Trade"])
                writer.writerow(["BBB", 100, 50.0, "BUY", "2026-01-02", "Trade"])
            with open(os.path.join(tmp, "rules.jsonl"), "w", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "type": "rule", "rule_id": "r1", "date": "2026-01-01",
                    "text": "No averaging down into a position that is already the book.",
                    "metric_key": "avgdown_count", "problem_key": "avgdown",
                    "status": "tracking"}) + "\n")
            row = _ok(_run("consider", path, "--root", tmp,
                           "--premise", json.dumps({"ticker": "aaa", "side": "buy",
                                                    "price": 100.0, "qty": 100})))["evaluation"]
            avgdown = [c for c in row["rule_collisions"] if c["metric_key"] == "avgdown_count"]
            assert avgdown, "the tracked average-down rule was not evaluated at all"
            assert avgdown[0]["rule_effect"] == expected, (
                f"book spelled {spellings}: the average-down rule was judged against a "
                f"different book than the weights beside it were: {avgdown[0]}")


def test_an_exchange_qualified_premise_matches_its_holding_across_case():
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(tmp, [_snapshot("2026-01-01", [
            _position("9999.TW", 1000, 500.0, currency="TWD", market="TW"),
            _position("1234", 500, 80.0, currency="TWD", market="TW")])])
        row = _evaluation(tmp, {"ticker": "9999.tw", "side": "buy", "price": 520.0, "qty": 100})
        after = row["consequence"]["after"]
        assert set(after["held"]) == {"9999.TW", "1234"}
        assert after["held"]["9999.TW"]["shares"] == 1100.0
        # The numeric symbol is untouched and still names its own position.
        numeric = _evaluation(tmp, {"ticker": "1234", "side": "buy", "price": 85.0, "qty": 100})
        assert numeric["consequence"]["after"]["held"]["1234"]["shares"] == 600.0


# ── C. recall reads the same identity the arithmetic does ──

def _resolved_prior(root, premise, context):
    """One genuinely stored, resolved consultation — minted through the CLI so
    the row is whatever the writer actually writes, then resolved through it
    too, never hand-assembled."""
    row = _evaluation(root, premise, "--decision-context", json.dumps(context))
    _ok(_run("consider", "--root", root, "--resolve", row["evaluation_id"],
             "--decision", "declined"))
    return row


_CONTEXT = {"reason": "trimming a position that outgrew its thesis",
            "why_now": "the quarter closed and the weight is still climbing"}
_LATER = {"reason": "the same position, asked again",
          "why_now": "nothing about the thesis has changed since"}


def test_the_same_decision_history_is_recallable_across_user_entered_case():
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(tmp, _BOOK)
        prior = _resolved_prior(tmp, {"ticker": "AAA", "side": "buy", "price": 120.0, "qty": 5},
                                _CONTEXT)
        payload = _ok(_consider(tmp, {"ticker": "aaa", "side": "buy", "price": 121.0, "qty": 9},
                                "--decision-context", json.dumps(_LATER)))
        recalled = payload.get("prior_decision")
        assert recalled is not None, (
            "a lower-case premise lost the user's own earlier consultation of the same name")
        assert recalled["evaluation_id"] == prior["evaluation_id"]
        assert recalled["ticker"] == "AAA"
        assert recalled["reason"] == _CONTEXT["reason"]


def test_a_legacy_row_stored_in_lower_case_is_still_recalled_and_its_bytes_are_untouched():
    """The append-only half. A row written before #803 froze whatever case the
    user typed; the reader restates the canonical identity without rewriting
    the file, and the stored `evaluation_id` never moves."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(tmp, _BOOK)
        prior = _resolved_prior(tmp, {"ticker": "AAA", "side": "buy", "price": 120.0, "qty": 5},
                                _CONTEXT)
        path = os.path.join(tmp, "trade_evaluations.jsonl")
        # Rewrite history the way the pre-fix engine would have stored it.
        legacy = []
        for line in open(path, encoding="utf-8"):
            row = json.loads(line)
            row["premise"]["ticker"] = "aaa"
            legacy.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(legacy) + "\n")
        before_bytes = open(path, "rb").read()

        payload = _ok(_consider(tmp, {"ticker": "AAA", "side": "buy", "price": 121.0, "qty": 9},
                                "--decision-context", json.dumps(_LATER)))
        recalled = payload.get("prior_decision")
        assert recalled is not None, "a legacy lower-case row stopped being recallable"
        assert recalled["evaluation_id"] == prior["evaluation_id"], (
            "the stored durable id must not move because a reader canonicalized")
        assert recalled["ticker"] == "AAA", (
            "the projection states the instrument, not the spelling that happened to be frozen")
        assert before_bytes == open(path, "rb").read()[:len(before_bytes)], (
            "reading a legacy row rewrote the append-only history it read")


def test_arithmetic_and_recall_agree_in_one_call_rather_than_each_having_a_rule():
    """The property a second, recall-only case rule would break: one lower-case
    premise, and the *same* identity has to reach the book and the memory. A
    fix applied to only one of them passes half of this file and fails here."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(tmp, _BOOK)
        _resolved_prior(tmp, {"ticker": "AAA", "side": "buy", "price": 120.0, "qty": 5}, _CONTEXT)
        payload = _ok(_consider(tmp, {"ticker": "aaa", "side": "buy", "price": 121.0, "qty": 9},
                                "--decision-context", json.dumps(_LATER)))
        row = payload["evaluation"]
        assert row["consequence"]["after"]["n_holdings"] == 3          # arithmetic
        assert payload["prior_decision"]["ticker"] == "AAA"            # recall
        assert payload["prior_decision"]["ticker"] == row["premise"]["ticker"]


# ── D. legacy stored rows read correctly without being rewritten ──

def test_a_legacy_lower_case_ledger_projects_onto_the_instrument_it_names():
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_ledger(tmp, [_snapshot("2026-01-01", [_position("AAA", 100, 100.0),
                                                            _position("BBB", 100, 50.0)]),
                                   _trade("2026-01-10", "aaa", "buy", 50, 120.0)])
        before_bytes = open(path, "rb").read()
        row = _evaluation(tmp, {"ticker": "AAA", "side": "buy", "price": 130.0, "qty": 10})
        before = row["consequence"]["before"]
        assert set(before["held"]) == {"AAA", "BBB"}, (
            "a legacy lower-case trade opened a phantom position beside the one it filled")
        assert before["held"]["AAA"]["shares"] == 150.0
        assert open(path, "rb").read() == before_bytes, "the ledger was rewritten in place"


def test_a_legacy_spelling_keeps_the_durable_cycle_id_it_already_minted():
    """theses.jsonl holds foreign keys to `cycle_id`. Reading a legacy book
    correctly must not silently re-mint the identifiers that book's own stored
    theses are bound by — so the holdings key canonicalizes and the durable id
    keeps the spelling the ledger actually stored."""
    events = [_trade("2026-01-05", "aaa", "buy", 100, 100.0)]
    derived = ledger_engine.derive_holdings(events)
    assert set(derived["holdings"]) == {"AAA"}
    assert derived["holdings"]["AAA"]["cycle_id"].startswith("aaa#"), (
        f"a stored cycle_id moved: {derived['holdings']['AAA']['cycle_id']}")
    # And a canonical book is byte-identical to what it always was.
    canonical = ledger_engine.derive_holdings([_trade("2026-01-05", "AAA", "buy", 100, 100.0)])
    assert canonical["holdings"]["AAA"]["cycle_id"].startswith("AAA#")


def test_new_csv_ingestion_writes_canonical_rows_so_the_split_cannot_recur():
    import csv
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "trades.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Symbol", "Quantity", "Price", "Action", "TradeDate", "RecordType"])
            writer.writerow(["aaa", 100, 100.0, "BUY", "2026-01-02", "Trade"])
        events, skipped, future = ledger_engine.trades_from_csv(path, today=__import__(
            "datetime").date(2026, 6, 1))
        assert (skipped, future) == (0, 0)
        assert [event["ticker"] for event in events] == ["AAA"], (
            "an append-only file must not be authored with a second spelling of one instrument")


# ── E. an irreconcilable legacy collision fails closed, by name ──

def test_two_declared_spellings_of_one_instrument_refuse_rather_than_merge_silently():
    """Which of two declared share counts is the position is not derivable from
    the record. Merging them would state a holding the user never declared, and
    picking one would do it silently — so the whole book fails closed and the
    refusal names both spellings, because a refusal nobody can act on is
    indistinguishable from a broken product."""
    events = [_snapshot("2026-01-01", [_position("AAA", 100, 100.0),
                                       _position("aaa", 50, 200.0),
                                       _position("BBB", 100, 50.0)])]
    derived = ledger_engine.derive_holdings(events)
    collisions = [row for row in derived["integrity"] if row["issue"] == "bad_ticker_collision"]
    assert len(collisions) == 1 and collisions[0]["ticker"] == "AAA"
    assert "'AAA'" in collisions[0]["detail"] and "'aaa'" in collisions[0]["detail"]
    assert portfolio_basis_engine.query_current_book(events) is None, (
        "a bad_ integrity row must make the whole book unknowable, not a merged guess")

    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(tmp, events)
        payload = _fails(_consider(tmp, {"ticker": "AAA", "side": "buy",
                                         "price": 130.0, "qty": 10}), "'aaa'")
        assert "'AAA'" in payload["error"]


def test_one_instrument_recorded_under_two_currencies_refuses_on_the_csv_route_too():
    """The row-level half of the same rule. Two spellings that agree about what
    the instrument is are one instrument's executions and merge; two that
    disagree would put two denominators behind one share count."""
    rows = [{"ticker": "AAA", "side": "buy", "qty": 10, "price": 100.0,
             "date": "2026-01-02", "currency": "USD", "market": "US"},
            {"ticker": "aaa", "side": "buy", "qty": 10, "price": 100.0,
             "date": "2026-01-03", "currency": "TWD", "market": "TW"}]
    try:
        consequence_engine.portfolio_state(rows)
    except consequence_engine.ConsequenceError as exc:
        assert "'AAA'" in str(exc) and "'aaa'" in str(exc), str(exc)
        assert "USD" in str(exc) and "TWD" in str(exc), str(exc)
    else:
        raise AssertionError("two currencies behind one canonical symbol were merged silently")


def test_a_malformed_ticker_stays_a_soft_integrity_row_rather_than_keying_the_book_on_none():
    """Canonicalization runs *before* each reader's existing malformed-row
    guard, never after it. Ordered the other way, a non-string ticker would
    canonicalize to `None` past a guard that had already let it through, and
    the holdings map would grow a `None` key — a crash on the next `sorted()`,
    for input the ledger has always tolerated as one bad row beside good ones.
    """
    for events, issue in [
        ([_snapshot("2026-01-01", [{"ticker": 123, "shares": 10, "avg_cost": 1.0},
                                   _position("AAA", 5, 2.0)])], "bad_snapshot_position"),
        ([{"type": "trade", "date": "2026-01-02", "ticker": 123, "action": "buy",
           "qty": 1, "price": 1.0}, _trade("2026-01-02", "AAA", "buy", 1, 1.0)],
         "bad_trade_event"),
    ]:
        derived = ledger_engine.derive_holdings(events)
        assert sorted(derived["holdings"]) == ["AAA"], derived["holdings"]
        assert [row["issue"] for row in derived["integrity"]] == [issue]


def test_a_ticker_declared_twice_under_one_spelling_keeps_its_existing_behaviour():
    """Scope guard. The pre-existing last-one-wins overwrite for an exactly
    duplicated declaration is not #803's to change, and `review._rows_from_ledger`
    deliberately mirrors it."""
    events = [_snapshot("2026-01-01", [_position("AAA", 100, 100.0),
                                       _position("AAA", 50, 200.0)])]
    derived = ledger_engine.derive_holdings(events)
    assert not [row for row in derived["integrity"] if row["issue"] == "bad_ticker_collision"]
    assert derived["holdings"]["AAA"]["shares"] == 50.0


# ── F. an already-canonical book is unchanged ──

def test_case_is_the_only_difference_two_spellings_of_one_premise_may_make():
    """The strongest available statement of "one owner": the entire frozen
    evaluation — every number, the identity hash over them, the rule
    collisions, the challenge — is byte-identical for `aaa` and `AAA`. A second
    normalization rule anywhere in the chain shows up here as a diff."""
    with tempfile.TemporaryDirectory() as lower, tempfile.TemporaryDirectory() as upper:
        _write_ledger(lower, _BOOK)
        _write_ledger(upper, _BOOK)
        low = _ok(_consider(lower, {"ticker": "aaa", "side": "buy", "price": 120.0, "qty": 10}))
        up = _ok(_consider(upper, {"ticker": "AAA", "side": "buy", "price": 120.0, "qty": 10}))
        assert low["evaluation"]["evaluation_id"] == up["evaluation"]["evaluation_id"], (
            "two spellings of one trade froze two identities, so recall cannot converge")
        assert low["evaluation"] == up["evaluation"]
        assert low["challenge"] == up["challenge"]


def test_surrounding_whitespace_still_normalizes_as_it_always_did():
    """The half of the rule that already worked keeps working — the fix widened
    `_ticker`'s normalization, it did not replace it."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(tmp, _BOOK)
        padded = _evaluation(tmp, {"ticker": "  AAA  ", "side": "buy",
                                   "price": 120.0, "qty": 10})
        assert padded["premise"]["ticker"] == "AAA"


def test_an_unusable_symbol_is_still_refused_by_the_envelope_that_owns_it():
    """Case is identity; shape is admission. Canonicalizing must not widen what
    the premise envelope accepts — the regex still decides, and it now decides
    on the canonical form."""
    with tempfile.TemporaryDirectory() as tmp:
        _write_ledger(tmp, _BOOK)
        for bad in ("aa aa", "@@@", "a" * 25):
            _fails(_consider(tmp, {"ticker": bad, "side": "buy", "price": 120.0, "qty": 1}),
                   "not a usable engine symbol")


# ── G. producer/consumer pairs outside the `consider` lane ──
#
# Every case above this section drives `consider`. That is exactly why the first
# cut of this fix shipped six one-sided normalizations: canonicalizing a producer
# (a writer, a map's keys, an index) while its matching consumer (a dedupe key, a
# lookup, a membership test) still read the raw spelling. Each pair below is a
# real defect that was green under 13/13 mutations and 47/47 suites, because
# nothing in the suite reached the lane it lived in.

def test_reimporting_a_csv_against_a_legacy_ledger_does_not_duplicate_the_trade():
    """`trades_from_csv` canonicalizes on the way in; `_trade_key` is what
    decides whether an incoming fill is one the ledger already has. With only
    the writer canonical, the weekly re-import stopped recognising its own
    earlier rows and appended them again — a silently doubled position, written
    into append-only state, which is the exact class of defect #803 exists to
    kill."""
    import csv
    legacy = [_trade("2026-01-05", "aaa", "buy", 100, 100.0)]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "trades.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Symbol", "Quantity", "Price", "Action", "TradeDate", "RecordType"])
            writer.writerow(["aaa", 100, 100.0, "BUY", "2026-01-05", "Trade"])
        incoming, _skipped, _future = ledger_engine.trades_from_csv(
            path, today=dt.date(2026, 6, 1))
        fresh, dup = ledger_engine.dedupe_against(legacy, incoming)
        assert (len(fresh), dup) == (0, 1), (
            f"the same fill was re-appended: fresh={len(fresh)} dup={dup}")
        holdings = ledger_engine.derive_holdings(legacy + fresh)["holdings"]
        assert holdings["AAA"]["shares"] == 100.0, (
            f"the position doubled on re-import: {holdings['AAA']['shares']}")


def test_a_split_map_rebases_the_rows_it_describes_whatever_case_either_uses():
    """`splits.normalize` keys the map canonically; `rebase_rows` is what reads
    it. Looking it up with the row's own spelling silently found nothing and
    left the row on a pre-split basis — a wrong share count, not a missing
    feature, and one that only appears months after the split."""
    for map_case, row_case in (("aaa", "aaa"), ("AAA", "aaa"), ("aaa", "AAA")):
        rows = [{"ticker": row_case, "side": "buy", "qty": 100.0, "price": 100.0,
                 "date": dt.date(2026, 1, 5), "market": "US", "currency": "USD"}]
        changed = split_engine.rebase_rows(rows, {map_case: [["2026-03-01", 10.0]]})
        assert changed == 1 and rows[0]["qty"] == 1000.0 and rows[0]["price"] == 10.0, (
            f"map {map_case!r} / row {row_case!r} left the row unrebased: {rows[0]}")


def test_metadata_case_alone_is_not_a_collision_but_a_real_disagreement_still_is():
    """The collision guard reads currency/market through the same case rule it
    reads tickers through. Without that, `US` and `us` looked like two different
    instruments and an ordinary book was refused outright — the guard firing on
    the spelling of its own metadata rather than on anything about the book. The
    second half is the half that matters: relaxing it must not make it stop
    catching the disagreement it exists for."""
    def rows(second_market, second_currency):
        return [{"ticker": "AAA", "side": "buy", "qty": 10, "price": 100.0,
                 "date": dt.date(2026, 1, 2), "currency": "USD", "market": "US"},
                {"ticker": "aaa", "side": "buy", "qty": 10, "price": 100.0,
                 "date": dt.date(2026, 1, 3), "currency": second_currency,
                 "market": second_market}]

    state = consequence_engine.portfolio_state(rows("us", "USD"))
    assert state["held"]["AAA"]["shares"] == 20, (
        "a book whose market differed only in case was refused as a collision")

    try:
        consequence_engine.portfolio_state(rows("TW", "TWD"))
    except consequence_engine.ConsequenceError as exc:
        assert "'AAA'" in str(exc) and "'aaa'" in str(exc), str(exc)
    else:
        raise AssertionError(
            "two genuinely different currencies behind one canonical symbol were merged")


def test_a_retrieved_quote_prices_the_instrument_whatever_case_requested_it():
    """The parsed feed is keyed canonically; a caller builds its request from its
    own rows and may spell them any way. An exact membership test dropped a
    quote that had actually been retrieved and reported the instrument
    unpriced — a book falling back to cost basis for no real reason."""
    feed = {"as_of": "2026-01-06", "prices": {
        "AAA": {"close": 120.0, "currency": "USD",
                "history": [["2026-01-05", 118.0], ["2026-01-06", 120.0]]}}}
    frame, note = price_feed_engine.to_frame(feed, tickers=["aaa"])
    assert frame is not None, f"a retrieved AAA quote was dropped for an 'aaa' request: {note}"
    assert "AAA" in frame.columns


def test_a_stored_statement_is_recalled_against_the_position_it_is_about():
    """`_evaluation_recall` indexes stored premises; `_recalled_entry_statement`
    looks the index up with a holding's own spelling. Indexing raw lost a legacy
    row's statement against a canonical position and a canonical row's against a
    legacy-spelled one — the user's own words, silently absent from the question
    that was written to carry them."""
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "trade_evaluations.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "evaluation_id": "eval-legacy0000000001", "created": "2026-01-05",
                "premise": {"ticker": "aaa", "side": "buy"},
                "context": {"reason": "the thesis I wrote down at entry",
                            "why_now": "the quarter had just closed"}}) + "\n")
        recall = review_engine._evaluation_recall(tmp)
        assert set(recall) == {"AAA"}, f"indexed under the stored spelling: {sorted(recall)}"
        for spelling in ("AAA", "aaa"):
            assert review_engine._recalled_entry_statement(
                recall, spelling, "AAA#2026-02-01#1") is not None, (
                f"the user's own words went missing for a {spelling!r} position")


def test_the_recorded_book_and_the_engine_state_agree_across_spelling():
    """`_overlay_ledger_holdings` compares the ledger's canonical book against
    whatever spelling the trade source wrote into engine state. An exact set
    comparison reported a phantom `ticker_set` difference for two books that
    match perfectly, and gated a valid one on a disagreement about case."""
    card = {}
    state = {"holdings": {"positions": {"aaa": {"shares": 100.0}}}}
    derived = {"holdings": {"AAA": {"shares": 100.0}}}
    _card, _state, reconciliation = review_engine._overlay_ledger_holdings(card, state, derived)
    # Scoped to `ticker_set` deliberately: this fixture carries no prices, so a
    # `valuation` mismatch is the correct reading of it and is not what this
    # case is about. What must not appear is "these two books hold different
    # instruments", asserted for a book whose share counts match exactly.
    kinds = [row["kind"] for row in reconciliation["mismatches"]]
    assert "ticker_set" not in kinds, (
        f"two spellings of one matching book read as different instruments: "
        f"{reconciliation['mismatches']}")
    assert reconciliation["raw_positions_n"] == reconciliation["canonical_positions_n"] == 1


def test_canonicalizing_a_comparison_never_hides_the_difference_it_reports():
    """Found reviewing the fix itself. Keying two maps canonically to compare
    them is right — unless two of one map's own keys collapse into one, which
    is precisely the disagreement the reconciliation exists to name. Resolved by
    insertion order it became a silently-dropped position and a count that
    under-reported its own input: the same defect class this whole change is
    about, reintroduced by the fix for it."""
    state = {"holdings": {"positions": {"aaa": {"shares": 100.0}, "AAA": {"shares": 50.0}}}}
    derived = {"holdings": {"AAA": {"shares": 150.0}}}
    _card, _state, rec = review_engine._overlay_ledger_holdings({}, state, derived)
    assert rec["raw_positions_n"] == 2, (
        f"the reconciliation under-reported its own input: {rec['raw_positions_n']} of 2")
    assert "ticker_set" in [row["kind"] for row in rec["mismatches"]], (
        "one instrument spelled two ways inside one book was resolved silently")


def test_a_ticker_the_rule_cannot_canonicalize_keeps_its_own_identity():
    """`_trade_key` is a dedupe key. Canonicalizing to `None` for a shape the
    rule does not handle makes every such row equal to every other, so dedupe
    drops one — silently, in the append-only path. The stored value is the
    fallback, so an unhandled shape is still itself."""
    def event(ticker):
        return {"type": "trade", "date": "2026-01-05", "ticker": ticker,
                "action": "buy", "qty": 1, "price": 1.0}
    fresh, dup = ledger_engine.dedupe_against([event(123)], [event(456)])
    assert (len(fresh), dup) == (1, 0), (
        f"two distinct malformed rows deduped onto one key: fresh={len(fresh)} dup={dup}")
    # And the ordinary case is untouched.
    fresh, dup = ledger_engine.dedupe_against([event("AAA")], [event("AAA")])
    assert (len(fresh), dup) == (0, 1)


def test_an_executed_trade_settles_the_evaluation_that_contemplated_it():
    """The other storage reader. An open evaluation is reconciled against the
    transaction record by ticker; the stored premise froze whatever case the
    user typed, the rows are the canonical book. Compared raw, a trade that
    plainly happened comes back `unmatched` — the engine telling the user it
    has no record of a fill sitting right there in their ledger."""
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "trade_evaluations.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "evaluation_id": "eval-legacy0000000002", "created": "2026-01-05",
                "decision": "open",
                "premise": {"ticker": "aaa", "side": "buy", "qty": 10, "price": 100.0}}) + "\n")
        rows = [{"ticker": "AAA", "side": "buy", "qty": 10, "price": 100.0,
                 "date": dt.date(2026, 1, 6), "currency": "USD", "market": "US"}]
        report = review_engine._evaluation_reconciliation(tmp, rows, "2026-01-31")
        assert report["items"], "the open evaluation was not reconciled at all"
        item = report["items"][0]
        assert item["status"] == "matched", (
            "a trade sitting in the ledger was reported as never executed "
            f"because the stored premise spelled it differently: {item}")


# ── H. the invariant, not another pair ──
#
# Sections B–G fix pairs. Pairs do not scale: two rounds of independent review
# found six one-sided comparisons each, because a system where two spellings can
# coexist grows a new defect at every place two components meet. The rule that
# ends that is not "canonicalize at more call sites" — it is:
#
#     canonical at the entry boundary  ->  canonical everywhere inside
#     the only readers that must project are readers of durable storage,
#     because stored bytes predate the rule and are never rewritten.
#
# These two cases assert that invariant instead of another pair, so a *new*
# entry point that forgets is caught by a test nobody has to remember to write.

def test_every_supported_entry_point_emits_a_canonical_ticker():
    """Every supported way a ticker enters the engine, checked one by one.

    A hand-maintained list, and honest about it: a route added without being
    added here is the one failure this case cannot catch. That is precisely why
    the end-to-end case below exists beside it — it needs no list.
    """
    import csv
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "trades.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Symbol", "Quantity", "Price", "Action", "TradeDate", "RecordType"])
            w.writerow(["aaa", 100, 100.0, "BUY", "2026-01-05", "Trade"])

        emitted = {
            "trade_recap.load":
                [row["ticker"] for row in tr_engine.load([path])],
            "ledger.trades_from_csv":
                [e["ticker"] for e in ledger_engine.trades_from_csv(
                    path, today=dt.date(2026, 6, 1))[0]],
            "splits.normalize":
                list(split_engine.normalize({"aaa": [["2026-03-01", 10.0]]})),
            "consequence.validate_premise":
                [consequence_engine.validate_premise(
                    {"ticker": "aaa", "side": "buy", "price": 1.0, "qty": 1},
                    [{"ticker": "AAA", "side": "buy", "qty": 1, "price": 1.0,
                      "date": dt.date(2026, 1, 5), "currency": "USD",
                      "market": "US"}])["ticker"]],
        }
        for entry, tickers in sorted(emitted.items()):
            assert tickers, f"{entry} emitted nothing; the case proves nothing"
            for ticker in tickers:
                assert symbols.is_canonical(ticker), (
                    f"{entry} let a non-canonical ticker into the engine: {ticker!r}")


def test_no_non_canonical_ticker_survives_into_a_delivered_answer():
    """The half the checklist above cannot fake. Drive the real CLI with a book
    spelled entirely in lower case and assert that *nothing anywhere* in the
    delivered payload — any key, any value, at any depth — is a ticker-shaped
    string that is not canonical. A new producer that leaks a raw spelling into
    an answer fails this without anyone having listed it."""
    import csv
    # Every ticker the book is written with, and every one of them lower-case,
    # so the scan is over the whole book rather than one symbol somebody
    # remembered to name. A leak in the *second* holding is the thing a
    # hard-coded symbol cannot see.
    book = [("aaa", 100, 100.0), ("bbb", 100, 50.0), ("ccc.tw", 100, 30.0)]
    canonical_book = {symbols.canonical_ticker(sym) for sym, _, _ in book}
    assert all(not symbols.is_canonical(sym) for sym, _, _ in book), (
        "the fixture must be written non-canonically or it proves nothing")

    def offenders(node):
        """Any spelling of any book ticker that is not that ticker's canonical
        identity — at any key, any value, any depth."""
        found = []
        if isinstance(node, dict):
            for key, value in node.items():
                found += offenders(key) + offenders(value)
        elif isinstance(node, list):
            for item in node:
                found += offenders(item)
        elif isinstance(node, str):
            if (symbols.canonical_ticker(node) in canonical_book
                    and not symbols.is_canonical(node)):
                found.append(node)
        return found

    # Both delivered routes, because one of them cannot see this defect at all.
    # `consider` re-derives the book through `consequence`, which canonicalizes
    # again on its own -- so `trade_recap.load` can stop canonicalizing
    # entirely and the `consider` payload stays clean. Verified by mutation:
    # reverting the boundary leaves this case green on `consider` alone. It is
    # `prepare` that hands the engine's own spelling to the user.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "trades.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Symbol", "Quantity", "Price", "Action", "TradeDate", "RecordType"])
            for sym, qty, px in book:
                w.writerow([sym, qty, px, "BUY", "2026-01-05", "Trade"])
        delivered = {
            "consider": _ok(_run("consider", path, "--root", tmp, "--premise",
                                 json.dumps({"ticker": "aaa", "side": "buy",
                                             "price": 120.0, "qty": 10}))),
            "prepare": _ok(_run("prepare", path, "--root", tmp,
                                "--route", "weekly_review")),
        }
        for route, payload in sorted(delivered.items()):
            leaked = offenders(payload)
            assert not leaked, (
                f"{route} put a raw spelling in the delivered answer: {sorted(set(leaked))}")
            # And the proof the scan can fail at all: the book's tickers really
            # are in this payload, canonically, so it was looking at something.
            serialized = json.dumps(payload)
            missing = sorted(t for t in canonical_book if t not in serialized)
            assert not missing, f"{route}'s payload never carried {missing}; the scan proved nothing"
        # The one deliberate exception, stated rather than left to be noticed:
        # a `cycle_id` embeds the spelling its book was recorded under and is
        # never re-minted (section I). It is not a ticker, so the scan above
        # does not flag it -- but a reader comparing the two rules should see
        # them named together.
        assert any("aaa#" in json.dumps(payload) for payload in delivered.values()), (
            "no durable id carried a stored spelling; section I's carve-out is untested here")


# ── I. the identifiers history is already bound by ──
#
# "Canonical everywhere inside" has exactly one carve-out, and it is not a
# weakening of the rule but the other half of it: a durable identifier minted
# *before* the rule may not be re-minted by it. `cycle_id` embeds the ticker's
# spelling, and `theses.jsonl` holds foreign keys to that string —
# `thesis.stable_thesis_id` is its digest — so canonicalizing a legacy book's
# rows without preserving the spelling files this week's position under an id
# no thesis was ever written against. #803's acceptance says it in as many
# words: legacy rows stay readable "without rewriting stored bytes or changing
# existing durable IDs". `ledger.derive_holdings` already carries this carve-out
# (`stored_as`); these cases are the CSV route reaching the same answer.

def _write_csv(path, rows):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Symbol", "Quantity", "Price", "Action", "TradeDate", "RecordType"])
        for row in rows:
            w.writerow(row)
    return path


def test_a_legacy_lower_case_book_keeps_the_cycle_id_its_theses_are_bound_to():
    """Driven through `prepare`, because that is the route the defect lives on
    and no search for `trade_recap.load(` can see it: `review._run_engine`
    starts `trade_recap.py` as a *subprocess*. Canonicalizing the row without
    preserving the spelling moved this position's `cycle_id` from
    `aaa#2026-01-05#1` to `AAA#2026-01-05#1` — a brand-new identifier for a
    cycle whose thesis is still filed under the old one, and nothing anywhere
    says so."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_csv(os.path.join(tmp, "legacy.csv"),
                          [["aaa", 100, 10.0, "BUY", "2026-01-05", "Trade"],
                           ["BBB", 50, 20.0, "BUY", "2026-02-10", "Trade"]])
        payload = _ok(_run("prepare", path, "--root", tmp, "--route", "weekly_review"))
        positions = (payload.get("review_plan") or {}).get("missing_thesis_positions") or []
        by_ticker = {p.get("ticker"): p for p in positions if isinstance(p, dict)}
        assert "AAA" in by_ticker, (
            f"the position is not keyed by its canonical identity: {sorted(by_ticker)}")
        assert by_ticker["AAA"]["cycle_id"] == "aaa#2026-01-05#1", (
            "the cycle_id theses.jsonl holds a foreign key to was re-minted: "
            f"{by_ticker['AAA']['cycle_id']!r}")
        # The already-canonical holding beside it is the control: its two
        # spellings are equal, so nothing about it may move either.
        assert by_ticker["BBB"]["cycle_id"] == "BBB#2026-02-10#1"


def test_a_reimport_whose_broker_changed_the_export_case_does_not_double_the_position():
    """The dedupe key is the instrument's identity, not the export's spelling.
    Keyed on the raw symbol, a weekly re-import whose broker started emitting
    upper case stopped recognising its own earlier rows and appended them a
    second time — 200 shares where the user holds 100, in append-only state."""
    with tempfile.TemporaryDirectory() as tmp:
        first = _write_csv(os.path.join(tmp, "week1.csv"),
                           [["aaa", 100, 10.0, "BUY", "2026-01-05", "Trade"]])
        second = _write_csv(os.path.join(tmp, "week2.csv"),
                            [["AAA", 100, 10.0, "BUY", "2026-01-05", "Trade"],
                             ["AAA", 50, 12.0, "BUY", "2026-02-10", "Trade"]])
        rows = tr_engine.load([first, second])
        held = {}
        for row in rows:
            held[row["ticker"]] = held.get(row["ticker"], 0.0) + row["qty"]
        assert held == {"AAA": 150.0}, (
            f"the re-imported fill was counted twice: {held}")
        assert tr_engine._LOAD_STATS["skip_dup"] == 1, (
            "the overlapping row was not recognised as one already loaded: "
            f"{tr_engine._LOAD_STATS}")
        # ...and the cycle it opened still answers to the id it was opened with.
        cursors = tr_engine.current_cycle_add_cursors(rows)
        assert cursors["AAA"]["cycle_id"] == "aaa#2026-01-05#1", cursors


def test_two_spellings_in_one_book_pick_the_stored_id_deterministically():
    """`load` sorts by date, so "the spelling this book stored" is the earliest
    fill's — not whichever file happened to be passed first. Without that, the
    same two CSVs in the other order would mint two different `cycle_id`s for
    one position, and a thesis would relink or not depending on argument order."""
    with tempfile.TemporaryDirectory() as tmp:
        early = _write_csv(os.path.join(tmp, "early.csv"),
                           [["aaa", 100, 10.0, "BUY", "2026-01-05", "Trade"]])
        late = _write_csv(os.path.join(tmp, "late.csv"),
                          [["AAA", 40, 11.0, "BUY", "2026-03-02", "Trade"]])
        forwards = tr_engine.current_cycle_add_cursors(tr_engine.load([early, late]))
        backwards = tr_engine.current_cycle_add_cursors(tr_engine.load([late, early]))
        assert forwards["AAA"]["cycle_id"] == backwards["AAA"]["cycle_id"] == "aaa#2026-01-05#1", (
            f"argument order changed the durable id: {forwards} vs {backwards}")


# ── J. every lane that uses a ticker as a key ──
#
# The failure this section answers is not "a comparison was missed" but the
# reason one keeps being missed: the previous rounds tested the lane that had
# just been edited. A ticker is a join key in more lanes than the one a change
# touches — the driver map, the market-data request, the split filter, the
# thesis relink, the exit queue — and each is a producer and a consumer that
# have to agree. Each case below drives one of those lanes and nothing else.

def test_a_supplied_driver_map_classifies_the_position_it_names():
    """The map is authored per-review against uncommon holdings; keyed raw
    while every `driver()` lookup arrives canonical, an entry the author wrote
    is silently invisible and the holding renders 未分類 — the one outcome the
    map exists to prevent, reported as if no entry had been supplied."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "driver_map.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"bbb": ["Test theme", 1]}, f)
        assert tr_engine.load_driver_map(path) == 1
        try:
            assert tr_engine.driver("BBB") == ("Test theme", 1), (
                f"a supplied classification did not reach the position: {tr_engine.driver('BBB')}")
        finally:
            # Both spellings: the module map is process-global, and a failure
            # here must not leave a stray key for the next case to trip over.
            tr_engine._DRIVER_MAP.pop("BBB", None)
            tr_engine._DRIVER_MAP.pop("bbb", None)


def test_a_market_data_request_is_the_same_key_the_bundle_answers_with():
    """`coverage()` and `to_price_feed_envelope` test the request against the
    parsed feed's own keys, and the feed is canonical. A request that only
    strips reports an instrument that *was* retrieved as `missing` — and asks
    the same universe twice under two cache keys."""
    request = market_data_engine.build_request(
        instruments=["aaa", "BBB"], currencies=["usd", "twd"],
        window_start="2026-01-01")
    assert request["instruments"] == ["AAA", "BBB"], request["instruments"]
    # The currency side has folded case since it was written; this is the
    # asymmetry that made the instrument side wrong.
    assert request["currencies"] == ["TWD"], request["currencies"]


def test_a_split_survives_the_filter_that_selects_it():
    """`to_frame` was fixed for exactly this and `splits_map` — the same filter
    over the same feed, one function below — was not. A dropped split means the
    share count never rebases across it."""
    feed = price_feed_engine.parse({
        "as_of": "2026-01-06", "source": "test",
        "prices": [{"ticker": "aaa", "date": "2026-01-06", "close": 10.0,
                    "currency": "USD",
                    "splits": [{"date": "2026-01-05", "ratio": 10.0}]}]})
    assert sorted(feed["prices"]) == ["AAA"], "the parsed feed is keyed canonically"
    assert price_feed_engine.splits_map(feed, tickers=["aaa"]), (
        "the 10:1 split was dropped by the filter that asked for it")
    assert (price_feed_engine.splits_map(feed, tickers=["aaa"])
            == price_feed_engine.splits_map(feed, tickers=["AAA"])), (
        "the filter answers differently depending on how the caller spelled it")


def test_a_legacy_thesis_relinks_onto_the_position_it_is_about():
    """`theses.jsonl` froze the spelling of the day; `positions` is the
    canonical book. Joined raw, the thesis is invisible to its own position:
    the relink never happens and the user is asked to restate a thesis they
    already wrote."""
    def relinks_for(stored_ticker):
        prior = {
            "cycle_id": f"{stored_ticker}#unknown", "ticker": stored_ticker,
            "thesis_id": "thesis-legacy000000001", "event_id": "ev-legacy000000001",
            "origin": "snapshot", "maturity": "inferred",
            "source_confidence": "candidate", "status": "open",
            "position_status": "open",
            "cycle_provenance": {"kind": "snapshot_inference",
                                 "snapshot_as_of": "2026-02-01"},
        }
        positions = {"AAA": {"cycle_id": "aaa#2026-01-05#1",
                             "cycle_start": "2026-01-05"}}
        return thesis_engine.build_snapshot_cycle_relinks(
            [prior], positions, "sess-000000000001", "2026-03-01")

    assert relinks_for("AAA"), (
        "the canonical control produced no relink; the case proves nothing")
    assert relinks_for("aaa"), (
        "a thesis stored under the older spelling never found its position")


def test_an_exit_is_detected_when_the_sale_is_spelled_differently_than_the_buy():
    """The exit queue reads the same ledger events the book does. Keyed raw
    while the book is canonical, the sale finds no shares to reduce and the
    exit disappears: the user closed a position and the 30/60/90 review never
    asks about it. The `cycle_id` still carries the stored spelling, because
    `revisit._revisit_id` embeds it and an already-queued exit has to keep
    deduping against itself."""
    events = [
        {"type": "trade", "date": "2026-01-05", "ticker": "aaa",
         "action": "buy", "qty": 100, "price": 10.0},
        {"type": "trade", "date": "2026-03-01", "ticker": "AAA",
         "action": "sell", "qty": 100, "price": 15.0},
    ]
    assert not (ledger_engine.derive_holdings(events).get("holdings") or {}), (
        "the book must read this as fully exited, or the case is about something else")
    exits = revisit_engine.detect_exits(events)
    assert len(exits) == 1, f"the exit vanished from the queue: {exits}"
    assert exits[0]["ticker"] == "AAA" and exits[0]["kind"] == "full"
    assert exits[0]["cycle_id"] == "aaa#2026-01-05#1", (
        f"the queued exit's durable id was re-minted: {exits[0]['cycle_id']}")


def test_a_queued_exit_finds_the_quote_the_review_is_already_holding():
    """The storage-reader half of the same lane: rows already in
    `revisit.jsonl` carry the spelling of the day, while the price map is
    canonical. Read raw, the comparison reports the price as missing and the
    card asks the user to supply something the engine has."""
    item = {"ticker": "aaa", "exit_price": 15.0, "exit_date": "2026-03-01",
            "shares_sold": 100.0}
    out = revisit_engine.compare(item, {"AAA": 18.0})
    assert out["needs_prices"] == [], (
        f"a quote the review already holds was reported missing: {out}")
    assert out["orig_ret"] is not None, out


def test_a_declaration_reconciles_against_the_ledger_it_agrees_with():
    """`ledger.reconcile`, the standalone sibling of `_overlay_ledger_holdings`
    — which was fixed for this and left this one raw. `derived` is canonical
    and the declaration keeps its stored spelling, so the report claimed *both*
    books were missing the other's position. The worst case needs no exotic
    input at all: a declaration and a ledger written the same non-canonical way
    still read as a total mismatch."""
    def report(declared, ledger_spelling):
        return ledger_engine.reconcile(
            [{"type": "trade", "date": "2026-01-05", "ticker": ledger_spelling,
              "action": "buy", "qty": 100, "price": 10.0}],
            [{"ticker": declared, "shares": 100}])

    for declared, ledger_spelling in (("aaa", "AAA"), ("AAA", "aaa"),
                                      ("aaa", "aaa"), ("AAA", "AAA")):
        out = report(declared, ledger_spelling)
        assert out["clean"] and out["match"] == ["AAA"], (
            f"declared {declared!r} against a ledger of {ledger_spelling!r} "
            f"reported a difference that does not exist: {out}")


def test_reconciling_across_case_still_names_a_declaration_that_disagrees_with_itself():
    """The carve-out that keeps the fix above from becoming the defect it
    replaces: two spellings inside *one* declaration are a real difference this
    function exists to report, not something to resolve by insertion order."""
    out = ledger_engine.reconcile(
        [{"type": "trade", "date": "2026-01-05", "ticker": "AAA",
          "action": "buy", "qty": 100, "price": 10.0}],
        [{"ticker": "aaa", "shares": 60}, {"ticker": "AAA", "shares": 40}])
    assert not out["clean"], (
        "a declaration holding one instrument under two spellings was silently merged")
    assert "only_declared" in {m["kind"] for m in out["mismatch"]}, out["mismatch"]


def test_a_confirmed_disappearance_carries_the_facts_of_the_position_that_left():
    """The third reader in the exit lane, and the one that degrades silently
    rather than refusing: a stored `position_absence` carries the spelling of
    the day while `holdings_as_of` is canonical, so the prior position is not
    found and every fact falls to its empty default — zero shares, no cost
    basis, and a TWD holding leaving the book as USD."""
    events = [
        {"type": "trade", "date": "2026-01-05", "ticker": "aaa", "action": "buy",
         "qty": 100, "price": 10.0, "market": "TW", "currency": "TWD"},
        {"type": "position_absence", "date": "2026-03-01", "ticker": "aaa",
         "cycle_id": "aaa#2026-01-05#1", "absence_id": "abs-000000000001"},
    ]
    rows = revisit_engine.absence_exits(events)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["shares_sold"] == 100.0, (
        f"the position that left was reported as empty: {row}")
    assert row["cost_basis"] == 1000.0, row
    assert (row["market"], row["currency"]) == ("TW", "TWD"), (
        f"a TWD holding left the book as {row['currency']}: {row}")
    assert row["ticker"] == "AAA"
    assert row["cycle_id"] == "aaa#2026-01-05#1", (
        "the stored absence's own cycle_id must be copied, never re-minted")


def test_a_newer_declaration_reconciles_against_the_book_it_agrees_with():
    """`snapshot_reconciliation` is `reconcile`'s sibling a hundred lines up in
    the same file, and had the same one-sided comparison. This is the `refresh`
    diff the user reads: two views that agree reported the whole position as
    both `only_derived` and `only_declared` — the book emptied and refilled
    itself. Two views written the same non-canonical way did it."""
    def status(recorded, declared):
        events = [{"type": "snapshot", "as_of": "2026-01-01",
                   "source": "user_declared",
                   "positions": [{"ticker": recorded, "shares": 100,
                                  "avg_cost": 10.0}]}]
        report = ledger_engine.snapshot_reconciliation(
            events, {"as_of": "2026-02-01",
                     "positions": [{"ticker": declared, "shares": 100,
                                    "avg_cost": 10.0}]})
        return report["status"], (report.get("diff") or {}).get("positions") or []

    for recorded, declared in (("aaa", "aaa"), ("aaa", "AAA"),
                               ("AAA", "aaa"), ("AAA", "AAA")):
        state, diff = status(recorded, declared)
        assert state == "reconciled" and not diff, (
            f"a book of {recorded!r} against a declaration of {declared!r} "
            f"reported a change that did not happen: {diff}")


def test_reconciling_a_declaration_across_case_still_names_one_that_disagrees_with_itself():
    """Same carve-out, the `refresh` side: a declaration naming one instrument
    twice is a real difference the diff exists to report."""
    events = [{"type": "snapshot", "as_of": "2026-01-01", "source": "user_declared",
               "positions": [{"ticker": "AAA", "shares": 100, "avg_cost": 10.0}]}]
    report = ledger_engine.snapshot_reconciliation(
        events, {"as_of": "2026-02-01",
                 "positions": [{"ticker": "aaa", "shares": 60, "avg_cost": 10.0},
                               {"ticker": "AAA", "shares": 40, "avg_cost": 10.0}]})
    assert report["status"] == "adjusted", report
    kinds = {row["kind"] for row in (report.get("diff") or {}).get("positions") or []}
    assert "only_declared" in kinds, (
        f"a declaration holding one instrument under two spellings was merged: {kinds}")


def _tests():
    return [(name, obj) for name, obj in sorted(globals().items())
            if name.startswith("test_") and callable(obj)]


if __name__ == "__main__":
    failed = 0
    for name, fn in _tests():
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 -- surface unexpected errors as failures
            failed += 1
            print(f"ERROR {name}: {exc!r}")
    total = len(_tests())
    print(f"\n{total - failed}/{total} passed")
    sys.exit(1 if failed else 0)
