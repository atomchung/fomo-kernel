#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
book_refresh.py — the independent book-refresh lane (#485 Slice C, owner ruling 2)

Updating the recorded book is its own conversation. Before this module the only
way to hand the product a newer holdings view was ``review.py prepare --route
snapshot_review``, which drags the whole review lifecycle — question budget,
card generation, commitment step — into what is actually data maintenance. This
lane owns the has-anchor case: no card, no question queue, no commitment, no
review session. Review and ``consider`` simply read the book it maintains.

Two phases, and the boundary between them is the point:

  Phase 1 (no answers) — validate the supplied book, freeze the reconciliation
    diff, and derive the confirmations the engine needs before it will adopt
    anything. Writes nothing at all.
  Phase 2 (answers) — recompute phase 1 from scratch under the root projection
    lock, refuse if anything moved (``refresh_id`` is content-addressed over
    exactly what the user was shown), then adopt.

Three confirmation kinds, per the owner rulings recorded on #485 and #531:

  disappearance — the record holds a position the new view does not. The engine
    cannot tell "you sold it" from "the screenshot missed it", and those two
    answers lead to genuinely different states, so it asks. ``sold`` removes the
    position and records a fill-free ``position_absence``; ``not_captured``
    merges — the position is carried into the new anchor with ``carried: true``.
    A sale the ledger already contains never reaches here: it is not a
    difference, so there is nothing to ask (the question stays rare).
  appearance — the new view holds a position the record does not (#531). It
    destroys nothing, which is why this lane once adopted it silently, but it
    arrives with **no provenance** and the engine has no way to recover it
    later: a position bought last week and one transferred in after six years
    produce byte-identical ledger state, and the difference can only be asked
    about at the moment it appears. So the user is asked for a rough duration in
    months — not a date, and not a provenance label, because two months and
    thirty-six months already say which is which — plus an average cost when the
    declared envelope carries none (the input class that makes ``consider``
    refuse, #528, closed at entry instead of at refusal). "I don't know" is an
    ordinary answer and keeps its existing representation: no date is invented,
    the cycle becomes ``ticker#unknown``, and ``horizon._cycle_start`` drops that
    holding from holding-period diagnostics.
  large_change — shares moved a lot on a position that is a large part of the
    book. Confirm, or re-supply the view. Thresholds are engine constants below.

Everything else — small changes, cash differences, market/currency differences —
is adopted and disclosed with no ceremony. All three kinds go into ONE question
(``flows/book-refresh.md`` step 2); eight appearances are eight list items, never
eight turns.

The month count is converted here, never by the agent (``SKILL.md``
non-negotiable rule 1: numbers come from engine artifacts). The agent transcribes
what the user said; ``_months_before`` derives the cycle start, and it is stored
paired with a ``since_basis`` stamp so no reader can pick up the date without
also picking up "this is an estimate".

No answer memory in v1 (owner: 每次都問). A recurring "not captured" is friction
that correctly pushes the user to record the position properly.

Deliberately absent: ``is_complete``. #549 removed the flag outright — a user
who said their view covered part of an account had that view disqualified as a
baseline and their book silently stopped updating, which is the same defect this
lane exists to fix. The system maintains exactly one current book; every prior
state is a slice, never a competitor.
"""
import datetime as dt
import hashlib
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledger as lg  # noqa: E402
import portfolio_basis  # noqa: E402
import snapshot_adapter  # noqa: E402

SCHEMA_V = 1

# Conservative first version, tunable. Both must hold before the flow spends a
# question on a change: the move is large relative to the WHOLE book, and the
# position it moved is a meaningful part of that book. A trim on a small holding
# is adopted silently; that is the intended asymmetry, not an oversight.
REFRESH_MAJOR_DELTA = 0.05   # |Δvalue| / book
REFRESH_CORE_WEIGHT = 0.10   # the position's own weight in the recorded book

CONFIRMATION_KINDS = ("disappearance", "appearance", "large_change")
# The classifications a user's answer may carry. `review.CONSIDER_DECISIONS`'s
# precedent: one tuple, locked to the schema enum by a drift test, so the CLI,
# the schema and the flow document cannot disagree about what an answer is.
REFRESH_CLASSIFICATIONS = ("sold", "not_captured", "confirmed", "resupply")
# Which answers each kind accepts. A disappearance is never "confirmed": saying
# the position is gone *is* `sold`, and collapsing the two would let a
# capture gap silently close a live cycle. An appearance takes `confirmed` for
# the mirror-image reason — saying the position really is held *is* confirming
# it — and carries its detail (how long, and at what cost) as answer fields
# rather than as extra classifications: `bought` versus `carried_in` labelled
# where a position came from without producing the number the engine needs
# (owner ruling on #531, superseding that split).
CLASSIFICATIONS_BY_KIND = {
    "disappearance": ("sold", "not_captured", "resupply"),
    "appearance": ("confirmed", "resupply"),
    "large_change": ("confirmed", "resupply"),
}
# The rough holding duration an appearance answer may state, in months. `None`
# is the legal "I don't know". Month granularity is sufficient and that is
# load-bearing: horizon.py puts every holding-period judgment on four day
# boundaries (21, 60, 90, 180), and a month is at worst ±15 days, which clears
# all four. Asking for an exact date would manufacture precision the user does
# not have. The cap is a sanity bound, not a product rule.
HELD_MONTHS_MAX = 1200

# Reachable only for a root that has recorded no book at all — no holdings view
# and no transaction import that produced a position. Since #549 a transaction
# import records the book it derived, so "has trades" is no longer a root this
# sentence can be shown to; saying otherwise was factually false for a root
# holding twenty-three trades and three positions, and it sent that user to a
# door that refused them right back.
NO_ANCHOR = (
    "this coach root has recorded no holdings yet; open one with "
    "`review.py prepare --snapshot-json ...` (a holdings view) or by reviewing "
    "a transaction export, then refresh it here"
)
# The mirror of NO_ANCHOR, and the review lane raises it (#530). Updating the
# recorded book is a mandatory node, not an alternative to reviewing it, so a
# holdings view carrying something only the user can settle is recorded before
# it is discussed. "Something only the user can settle" is not a second rule
# written over there: it is exactly a non-empty `pending_confirmations` from
# `plan_refresh` below, so the set can never drift from what this lane asks.
NEEDS_BOOK_UPDATE = (
    "this holdings view has changes only you can settle before the recorded "
    "book can catch up; run `review.py refresh --snapshot-json ...` first, "
    "then review it"
)
REFRESH_STALE = (
    "the recorded book changed after this refresh was prepared; run refresh "
    "again without --answers to see the current difference"
)


class RefreshError(ValueError):
    """A refresh that must not proceed. Always fails closed, never partially."""


def _canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _finite_positive(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


# ───────────────────────── phase 1: freeze ─────────────────────────

def _unit_values(derived, declared, fx):
    """Per-ticker unit value in the aggregate currency, and how it was obtained.

    The refresh lane has no price feed and must not invent one, so a unit value
    comes from what the user actually supplied (``market_value / shares`` on the
    declared row) or from what the record already holds (``avg_cost``). Anything
    else is unavailable and is disclosed rather than valued at zero — a holding
    silently valued at zero would quietly shrink the denominator every other
    number here is measured against (#515's ruling, applied locally).

    FX comes from the supplied envelope; a currency with no rate is unavailable
    for the same reason. Nothing is converted with a guessed rate.
    """
    units = {}
    for ticker in sorted(set(derived) | set(declared)):
        fact = derived.get(ticker) or {}
        claim = declared.get(ticker) or {}
        currency = str(claim.get("currency") or fact.get("currency") or "USD").upper()
        rate = _finite_positive((fx or {}).get(currency))
        if rate is None:
            units[ticker] = None
            continue
        unit = None
        declared_value = _finite_positive(claim.get("market_value"))
        declared_shares = _finite_positive(claim.get("shares"))
        if declared_value is not None and declared_shares is not None:
            unit = declared_value / declared_shares
        elif _finite_positive(fact.get("avg_cost")) is not None:
            unit = float(fact["avg_cost"])
        elif _finite_positive(claim.get("avg_cost")) is not None:
            unit = float(claim["avg_cost"])
        units[ticker] = unit * rate if unit is not None else None
    return units


def _recorded_book_partition(derived, units):
    """One classification of the recorded book's value, via the shared primitive.

    ``portfolio_basis.value_partition`` is the same function ``sizing_projection``
    and ``trade_recap.current_book_projection`` consume (#477): the arithmetic is
    physically shared rather than mirrored, so this lane cannot drift into its
    own idea of what a position is worth.
    """
    rows = []
    for ticker, fact in sorted(derived.items()):
        shares = _finite_positive(fact.get("shares"))
        unit = units.get(ticker)
        price = unit if (shares is not None and unit is not None) else None
        rows.append((ticker, price, shares or 0.0, None))
    return portfolio_basis.value_partition(rows)


def _declared_map(anchor):
    return {row["ticker"]: row for row in anchor.get("positions") or []
            if isinstance(row, dict) and row.get("ticker")}


def _pending_confirmations(diff, derived, declared, partition, units):
    """The confirmations the engine requires before it will adopt this book."""
    pending = []
    for row in diff.get("positions") or []:
        ticker = row.get("ticker")
        if row.get("kind") == "only_derived":
            fact = derived.get(ticker) or {}
            pending.append({
                "kind": "disappearance", "ticker": ticker,
                "cycle_id": fact.get("cycle_id"),
                "derived_shares": fact.get("shares"),
                "options": list(CLASSIFICATIONS_BY_KIND["disappearance"]),
            })
    for row in diff.get("positions") or []:
        ticker = row.get("ticker")
        if row.get("kind") != "only_declared":
            continue
        claim = declared.get(ticker) or {}
        pending.append({
            "kind": "appearance", "ticker": ticker,
            "declared_shares": _finite_positive(row.get("declared")),
            # The engine states which detail this row still needs. A cost the
            # view already carries is never re-asked, and answering with a
            # second one is refused rather than silently preferred.
            "needs_avg_cost": claim.get("avg_cost") is None,
            "options": list(CLASSIFICATIONS_BY_KIND["appearance"]),
        })
    denominator = partition.get("denominator")
    if not _finite_positive(denominator):
        return pending
    for row in diff.get("positions") or []:
        if row.get("kind") != "shares":
            continue
        ticker = row.get("ticker")
        entry = (partition.get("values") or {}).get(ticker) or {}
        weight = entry.get("weight")
        unit = units.get(ticker)
        derived_shares = _finite_positive(row.get("derived"))
        declared_shares = _finite_positive(row.get("declared"))
        if weight is None or unit is None or derived_shares is None or declared_shares is None:
            continue
        delta_weight = abs(declared_shares - derived_shares) * unit / denominator
        if delta_weight < REFRESH_MAJOR_DELTA or weight < REFRESH_CORE_WEIGHT:
            continue
        pending.append({
            "kind": "large_change", "ticker": ticker,
            "derived_shares": derived_shares, "declared_shares": declared_shares,
            "weight": round(weight, 6), "delta_weight": round(delta_weight, 6),
            "options": list(CLASSIFICATIONS_BY_KIND["large_change"]),
        })
    return pending


def plan_refresh(events, snapshot, anchor):
    """Freeze one refresh: what differs, and what must be confirmed. Writes nothing.

    Raises ``RefreshError`` when there is no recorded book to refresh (that is
    onboarding's job, and routing it here would let a first declaration adopt
    without the initial-history boundary the review lane enforces) or when the
    supplied book is older than the current anchor.

    Since #549 that first condition means what it says. Every source records the
    book at its own time, so there is no longer such a thing as a root that
    holds positions but no recorded book — the state in which this lane refused
    the only users #485 wrote the cheaper correction surface for.
    """
    if lg.latest_anchor(events) is None:
        raise RefreshError(NO_ANCHOR)
    try:
        reconciliation = lg.snapshot_reconciliation(events, anchor)
    except ValueError as exc:
        raise RefreshError(str(exc)) from exc
    if reconciliation is None:                     # defensive: latest_anchor said otherwise
        raise RefreshError(NO_ANCHOR)

    # Read the book through the declaration's own end-of-day window, the same
    # one snapshot_reconciliation stated its diff on. Calling derive_holdings
    # here instead would attach today's shares, cycle id and cost basis to a
    # difference computed for the snapshot date.
    derived = lg.holdings_as_of(events, reconciliation["as_of"])
    declared = _declared_map(anchor)
    units = _unit_values(derived, declared, snapshot.get("fx"))
    partition = _recorded_book_partition(derived, units)
    diff = reconciliation.get("diff") or {}
    pending = _pending_confirmations(diff, derived, declared, partition, units)

    positions = diff.get("positions") or []
    summary = {
        "status": reconciliation.get("status"),
        "as_of": reconciliation.get("as_of"),
        "against_as_of": (reconciliation.get("against") or {}).get("as_of"),
        "positions_changed": sorted({row["ticker"] for row in positions
                                     if row.get("kind") not in ("only_declared", "only_derived")}),
        "only_declared": sorted({row["ticker"] for row in positions
                                 if row.get("kind") == "only_declared"}),
        "only_derived": sorted({row["ticker"] for row in positions
                                if row.get("kind") == "only_derived"}),
        "cash_currencies": sorted({row["currency"] for row in diff.get("cash") or []}),
        # The bounded read discloses what it could not value, the same duty
        # `sizing_coverage` carries on the card: a threshold evaluated over a
        # partial denominator must say the denominator is partial.
        "valuation_coverage": {
            "valued": sorted(partition.get("priced") or []),
            "unavailable": sorted(partition.get("unavailable") or []),
            "reason": partition.get("reason"),
        },
    }
    identity = {"schema_version": SCHEMA_V,
                "declared": snapshot_payload_of(anchor),
                "against": reconciliation.get("against"),
                "diff": diff,
                "pending_confirmations": pending}
    refresh_id = "refresh-" + hashlib.sha256(
        _canonical(identity).encode("utf-8")).hexdigest()[:16]
    return {"schema_version": SCHEMA_V, "refresh_id": refresh_id,
            "as_of": reconciliation.get("as_of"),
            "against": reconciliation.get("against"),
            "status": "pending_confirmation" if pending else "ready",
            "diff": diff, "pending_confirmations": pending, "summary": summary}


def snapshot_payload_of(anchor):
    """The anchor's stable identity payload, minus projection order.

    Mirrors ``session._snapshot_payload``'s field whitelist deliberately at the
    call level rather than importing it: ``session`` imports this lane's caller,
    and the refresh id only needs to be stable across two runs of *this* module.
    The adopted anchor's real identity is still assigned by ``session``.
    """
    payload = {key: anchor[key] for key in ("type", "as_of", "source", "positions", "cash")
               if key in anchor}
    return payload


# ───────────────────────── phase 2: adopt ─────────────────────────

def _held_months(value, ticker):
    """The user's rough holding duration in months, or ``None`` for "I don't know".

    The key must be present: a missing one is an unasked question, not a shrug,
    and the two must never collapse. Fractions are refused rather than rounded
    — a rounded number is one the engine invented.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RefreshError(
            f"{ticker}: held_months must be a whole number of months, or null "
            "if the user does not know")
    number = float(value)
    if not math.isfinite(number) or number != int(number):
        raise RefreshError(f"{ticker}: held_months must be a whole number of months")
    months = int(number)
    if months < 0 or months > HELD_MONTHS_MAX:
        raise RefreshError(
            f"{ticker}: held_months must be between 0 and {HELD_MONTHS_MAX}")
    return months


def _answer_avg_cost(value, ticker):
    """The average cost the user stated for an appearing position, or ``None``.

    ``None`` stays legal: a user who genuinely does not know their cost must not
    be pushed into inventing one, and the position simply stays value-only —
    the same state it would have reached silently before this question existed,
    now reached with the user having been asked.
    """
    if value is None:
        return None
    cost = _finite_positive(value)
    if cost is None or isinstance(value, bool):
        raise RefreshError(
            f"{ticker}: avg_cost must be a positive number, or null if the user "
            "does not know it")
    return cost


def _validated_answers(pending, answers):
    """Map ticker -> answer record, or fail closed.

    Accepts an answer only for an item the frozen plan actually raised (the
    ``condition-check.schema.json`` ``user_response`` pattern: the engine
    declares what may be answered, and an answer for anything else is refused
    by name rather than ignored), and refuses to adopt while any raised item is
    unanswered. An unanswered destructive change adopted "by default" is the
    silent data loss this whole lane exists to prevent.

    The same rule runs one level down, over the detail an answer may carry: a
    confirmed appearance MUST state ``held_months`` (``null`` being the legal
    "I don't know") and MUST state ``avg_cost`` exactly when the raised item
    said the declared view carries none. A field nobody asked for is refused by
    name, so a cost cannot arrive through a side channel and quietly beat the
    one the user actually declared.
    """
    if not isinstance(answers, list):
        raise RefreshError("answers must be a list of {ticker, classification} objects")
    by_ticker = {row["ticker"]: row for row in pending}
    seen = {}
    for index, row in enumerate(answers):
        if not isinstance(row, dict):
            raise RefreshError(f"answers[{index}] must be an object")
        ticker = row.get("ticker")
        if ticker not in by_ticker:
            raise RefreshError(
                f"this refresh raised no confirmation for {ticker!r}; "
                "answerable: " + ", ".join(sorted(by_ticker)))
        if ticker in seen:
            raise RefreshError(f"answers repeat {ticker}")
        raised = by_ticker[ticker]
        classification = row.get("classification")
        allowed = CLASSIFICATIONS_BY_KIND[raised["kind"]]
        if classification not in allowed:
            raise RefreshError(
                f"{ticker}: {classification!r} is not one of " + ", ".join(allowed))
        wants_detail = raised["kind"] == "appearance" and classification == "confirmed"
        expected = ({"held_months"} | ({"avg_cost"} if raised.get("needs_avg_cost") else set())
                    if wants_detail else set())
        supplied = set(row) & {"held_months", "avg_cost"}
        for extra in sorted(supplied - expected):
            raise RefreshError(f"{ticker}: this refresh did not ask for {extra}")
        for absent in sorted(expected - supplied):
            raise RefreshError(f"{ticker}: {absent} is required to adopt this appearance")
        seen[ticker] = {
            "classification": classification,
            "held_months": _held_months(row.get("held_months"), ticker) if wants_detail else None,
            "avg_cost": (_answer_avg_cost(row.get("avg_cost"), ticker)
                         if wants_detail and raised.get("needs_avg_cost") else None),
        }
    missing = sorted(set(by_ticker) - set(seen))
    if missing:
        raise RefreshError("unanswered confirmations: " + ", ".join(missing))
    return seen


def _months_before(day, months):
    """``day`` minus ``months`` calendar months, clamped to a real calendar day.

    Calendar arithmetic rather than a 30.44-day average, because the user's
    answer is calendar-shaped: "about six months" means the same day six months
    back, not 183 days back. The day-of-month clamp is the ordinary end-of-month
    case (March 31 minus one month is February 28 or 29, never March 3).
    """
    total = (day.year * 12 + day.month - 1) - int(months)
    year, month = divmod(total, 12)
    month += 1
    if month == 2:
        last = 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28
    elif month in (4, 6, 9, 11):
        last = 30
    else:
        last = 31
    return dt.date(year, month, min(day.day, last))


def _appearance_stamp(row, answer, as_of):
    """One appearing position's envelope row, with the engine's own derivation.

    The agent transcribed a month count; this is where it becomes a date, and
    the date is never emitted without ``since_basis`` beside it (the pairing is
    enforced again in ``snapshot_adapter``). "I don't know" produces no date at
    all — ``since_basis: "unknown"`` — which ``ledger.derive_holdings`` turns
    into a ``ticker#unknown`` cycle, the representation that already exists for
    a holding whose open cannot be dated.
    """
    row = dict(row)
    months = answer.get("held_months")
    if months is None:
        row["since_basis"] = "unknown"
    else:
        row["since_basis"] = "user_estimate"
        row["since"] = _months_before(dt.date.fromisoformat(as_of), months).isoformat()
    if answer.get("avg_cost") is not None:
        # Only reachable when the raised item said the view carried none;
        # `_validated_answers` refuses a second cost for a row that had one.
        row["avg_cost"] = answer["avg_cost"]
    return row


def _carry_forward_provenance(row, recorded, derived):
    """Keep an already-answered cycle start attached across later refreshes.

    Without this the stamp survives exactly one refresh: the next declaration is
    an ordinary envelope with no provenance on it, so the position's start would
    snap back to the new anchor's date and the user's answer would have bought
    exactly one review. The user is not asked again, and should not be — the
    position is not appearing, it is simply still held.

    Two gates, and both are about not carrying a stamp onto a different cycle.
    The stamp is read from the last declaration's own row (the only place that
    says a start was answered rather than assumed; a ``trades_derived``
    restatement never stamps one, so reading the newest recorded row instead
    would spend the user's answer the first time they imported a CSV). And it is applied only while
    the record still traces the position back to that anchor
    (``origin == "snapshot"``): a position sold and bought back reads
    ``origin == "trades"``, and its real recorded open date must win over a
    stamp describing the cycle before it.
    """
    ticker = row.get("ticker")
    stamp = (recorded.get(ticker) or {}).get("since_basis")
    if stamp not in lg.SINCE_BASES:
        return row
    if (derived.get(ticker) or {}).get("origin") != "snapshot":
        return row
    row = dict(row)
    row["since_basis"] = stamp
    row.pop("since", None)
    if stamp == "user_estimate":
        row["since"] = recorded[ticker].get("since")
    return row


def _carried_row(ticker, fact):
    """One position carried forward from the record, copied and never invented."""
    row = {"ticker": ticker, "shares": fact.get("shares"),
           "market": fact.get("market") or "US",
           "currency": fact.get("currency") or "USD",
           "carried": True}
    if fact.get("avg_cost") is not None:
        row["avg_cost"] = fact["avg_cost"]
    return row


def build_adoption(receipt, events, snapshot, anchor, answers, *, today=None):
    """Turn confirmed answers into the exact rows finalize will append.

    Returns ``{"status": "resupply", ...}`` when the user says the supplied view
    is wrong — nothing is written and the caller re-runs with a better book —
    otherwise ``{"status": "adopt", anchor, reconciliation, absences, carried,
    sold}``.

    The adopted anchor is re-validated through the same ``snapshot_adapter``
    envelope validator the supplied one went through, so a carried row cannot
    enter the book by a path that skips validation. The reconciliation attached
    here is recomputed against the *adopted* anchor, not the supplied one: with
    carried rows merged back in, the difference the ledger records must be the
    difference that actually happened.

    This is also the only place ``allow_engine_provenance`` is turned on. The
    envelope assembled here is not a caller's file: every ``since`` in it was
    computed a few lines above by ``_appearance_stamp`` or copied off the
    recorded anchor by ``_carry_forward_provenance``.
    """
    classifications = _validated_answers(receipt.get("pending_confirmations") or [], answers)
    resupply = sorted(ticker for ticker, value in classifications.items()
                      if value["classification"] == "resupply")
    if resupply:
        return {"status": "resupply", "refresh_id": receipt.get("refresh_id"),
                "tickers": resupply}

    derived = lg.holdings_as_of(events, snapshot["as_of"])
    recorded = _declared_map(lg.latest_anchor(events, declared_only=True) or {})
    by_ticker = {row["ticker"]: row for row in receipt.get("pending_confirmations") or []}
    carried, sold, appeared = [], [], {}
    for ticker, answer in sorted(classifications.items()):
        kind = by_ticker[ticker]["kind"]
        if kind == "appearance":
            appeared[ticker] = answer
            continue
        if kind != "disappearance":
            continue
        if answer["classification"] == "not_captured":
            fact = derived.get(ticker)
            if not fact:
                raise RefreshError(f"{ticker} is no longer in the recorded book to carry forward")
            carried.append(_carry_forward_provenance(
                _carried_row(ticker, fact), recorded, derived))
        elif answer["classification"] == "sold":
            sold.append(ticker)

    supplied = []
    for row in anchor.get("positions") or []:
        row = _supplied_row(row)
        answer = appeared.get(row.get("ticker"))
        supplied.append(_appearance_stamp(row, answer, snapshot["as_of"]) if answer
                        else _carry_forward_provenance(row, recorded, derived))
    envelope = {"as_of": snapshot["as_of"], "positions": supplied + carried}
    if snapshot.get("cash") is not None:
        envelope["cash"] = snapshot["cash"]
    if snapshot.get("fx"):
        envelope["fx"] = snapshot["fx"]
    try:
        _adopted_snapshot, adopted_anchor = snapshot_adapter.normalize_book(
            envelope, today=today or dt.date.fromisoformat(snapshot["as_of"]),
            allow_engine_provenance=True)
    except snapshot_adapter.SnapshotError as exc:
        raise RefreshError(f"the adopted book failed validation: {exc}") from exc

    try:
        reconciliation = lg.snapshot_reconciliation(events, adopted_anchor)
    except ValueError as exc:
        raise RefreshError(str(exc)) from exc
    if reconciliation is None:
        raise RefreshError(NO_ANCHOR)

    absences = []
    if reconciliation.get("status") == "adjusted":
        for ticker in sold:
            cycle_id = (derived.get(ticker) or {}).get("cycle_id")
            if not cycle_id:
                raise RefreshError(f"{ticker} has no recorded cycle to close")
            absences.append(lg.build_position_absence(
                date=adopted_anchor["as_of"], ticker=ticker, cycle_id=cycle_id,
                session_id=receipt.get("refresh_id")))
    elif sold:
        # A confirmed sale that leaves the book unchanged is a contradiction the
        # engine must not paper over: it would record an exit for a position the
        # adopted book still holds.
        raise RefreshError(
            "confirmed sales do not change the recorded book: " + ", ".join(sold))
    return {"status": "adopt", "refresh_id": receipt.get("refresh_id"),
            "anchor": adopted_anchor, "reconciliation": reconciliation,
            "absences": absences,
            "carried": [row["ticker"] for row in carried], "sold": sold,
            "appeared": sorted(appeared)}


def _supplied_row(position):
    """An anchor position back in envelope shape, so it re-validates unchanged."""
    row = {key: position[key] for key in ("ticker", "shares", "market", "currency")
           if key in position}
    for key in ("avg_cost", "market_value", "carried"):
        if position.get(key) is not None:
            row[key] = position[key]
    return row
