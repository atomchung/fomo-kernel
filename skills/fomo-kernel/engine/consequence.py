#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""consequence.py — Layer 2: deterministic arithmetic over a hypothetical trade
(docs/decision-fomo-kernel-shape.md §3).

The product scenario: a user mid-decision asks "I'm thinking of buying NVDA —
what does that do to my book?" `prepare` today accepts only history, no
hypothesis input, so Layer 2 "barely exists" until this module supplies the
missing primitive. The engine answers with computed consequences, never
prose — locale wording for anything disclosed here belongs in renderer copy,
never in this file (docs/maintainer-guide.md, "Honesty decisions belong in code").

Three public functions, each a thin assembly of trade_recap's own pure
functions — nothing here reimplements arithmetic trade_recap already owns:

  validate_premise   one agent-or-user-supplied hypothetical trade -> a
                      normalized dict, or ConsequenceError naming the field
  portfolio_state     the book as of the end of a row list — before or after,
                      depending only on whether the caller appended the
                      hypothetical trade as one more row
  consequence         {premise, before, after, delta, disclosures} for one
                      hypothetical trade
  rule_collision      for each currently-tracked rule, whether the same
                      hypothetical trade would collide with it right now

``rule_collision`` needs its own vocabulary discipline, mirrored from
conditions.py (module docstring, and lines 40-75) and the same firewall
reasoning conditions.py states at lines 66-72: a rule row
(problems.py's ``{rule_id, text, metric_key, problem_key, ...}``) carries no
structured threshold — ``text`` is free-form human language the user chose,
and the real thresholds are engine constants (OVERSIZE_TRIGGER,
SECTOR_MAX_TH, AVGDOWN_BREACH_W, ...). Only seven ``metric_key`` values map to
a ``problem_key`` at all (session.py's ``PKEY``, session.py:35-43). A rule
whose metric this module cannot evaluate from one hypothetical trade must
never render as "held" or "fine" — that is exactly the mistake
conditions.py's ``unmapped`` tier exists to prevent for a condition slot, and
the same discipline applies here:

===================  ============  ===================================
problem_key is None   "unmapped"    metric_key has no mapping at all
mapped, not built     "unjudged"    exit_severity/hold_severity describe
                                    realized selling/holding behaviour
                                    across history; one hypothetical trade
                                    cannot settle either
mapped and buildable  real verdict  would_breach / already_over / clear,
                                    judged against trade_recap's own named
                                    lines for the metric_key the rule names
===================  ============  ===================================

A real verdict answers two independent questions, and the second bug this
module shipped with was collapsing them into one (docs/development-guide.md
section 7 names the pattern: two independent dispositions folded into a
single field is the same disease as two readers deriving one fact). "Is the
book already over this line" and "did *this trade* cross it" do not agree in
general — a book that is already oversized in NVDA reads `oversize_triggered:
true` for a trade that sells half the position and materially improves it,
just as it does for one that makes the position bigger. Reading the after
state's triggered flag alone therefore told a user reducing an oversized
position that the trade broke their own rule. The fix carries both facts:
`state` is would_breach only when this premise is what crossed the line
(for max_pos_pct, the *premise ticker's own* weight against
effective_oversize_trigger — not the book's max_ticker, which may be a
different position entirely); already_over when the book was already over the
line before this trade and remains so, without this trade being the cause;
clear when the book is not over the line after the trade. `worsens` is the
already_over case's second axis — whether the relevant reading (the book's
max_pct for the sizing rule; this metric_key's own reading for the
concentration trio) moved in the bad direction — and is `None` everywhere
`already_over` does not apply, because a boolean that means nothing outside
one state is worse than an absent one.

`state` and `worsens` are both *diagnostics about the book*, and #579 is the
receipt for what happens when a decision-facing surface is left to compose a
verdict out of them: an owner asked about a sell that took a position from
80% to 75% against a self-authored 20% cap and was told the trade violated
the rule. `rule_effect` is the third field and the one every decision-facing
consumer reads — what this *transaction* does to the rule, derived by
`classify_rule_effect` from the same readings and the same line the state
beside it was judged on, never composed downstream. See RULE_EFFECTS for the
six transitions and for the two of them `state`/`worsens` cannot express at
all.

The concentration trio (ai_pct / max_sector_pct / top3_pct) shipped a third
bug of the same shape, caught by external review after this file's own
mutation suite passed clean: causality was judged on dim_diversify's shared
`triggered` flag, which is correct for build_problem_events' aggregate
"did a concentration problem occur" reconciliation but wrong for a rule,
because a rule names exactly one metric_key and the user committed to that
one line, not to the flag. Reading the shared flag hid a fresh cross of
ai_pct's own line behind top3 already being over its own unrelated one
(already_over instead of would_breach), and separately reported a fresh
cross of max_sector_pct's own 40% line as clear outright, because the shared
flag's max_sector arm carries dim_diversify's `>= 8` holdings guard — a false
negative, worse than the first bug because it says there is no collision
when there is one. The fix judges each metric_key against its own reading
and its own line (`_concentration_line`, sourced from trade_recap's named
constants so the two cannot drift), never the shared flag. See
`_concentration_line`'s docstring for why the holdings guard does not carry
forward into a rule collision.

``rule_collision`` is read-only: it never writes rules.jsonl and never calls
problems.check_rules. A hypothetical's collision state must never enter
held_streak or the graduation statistics a real reconciled breach earns — the
same firewall conditions.py already draws between a researched verdict and
the mechanically verified kind (docs/development-guide.md section 5).
"""
import datetime as dt
import math
import re
from collections.abc import Mapping

import instruments
import symbols
import trade_recap


class ConsequenceError(Exception):
    """Raised when a supplied trade premise cannot be evaluated as-is."""


SIDES = ("buy", "sell")

# Machine-readable, never prose (see module docstring). A caller renders
# wording from these keys; this module never constructs a sentence.
#
# `mixed_currency_no_fx` is deliberately absent (#600). It disclosed a book
# whose non-USD holdings had been summed into the denominator at a 1.0
# conversion factor, and the wording it carried — "aggregate figures are
# incomplete" — described a gap where the mechanism is an inversion: at ~31
# TWD to the dollar, the smaller of two holdings reads as 97% of the book and
# the larger as 3%. A reader told the numbers are incomplete cannot tell that
# the ranking is backwards. `portfolio_state` now refuses instead, which is
# what AGENTS.md invariant 2 already said ("a missing or incompatible
# valuation or FX rate ... fail closed") and what #497 established for the
# canonical PortfolioBasis lane; the legacy CSV lane was the one that never
# got it. The key survives in schemas/trade-evaluation.schema.json's enum for
# replay compatibility with rows written before this refusal existed, the
# same posture #549's `declared_partial` has.
#
# The last two are the "what did these numbers not see" pair (#598/#599).
# Both are about the *book*, not the premise: `unmapped_driver` above has
# always looked only at the ticker being asked about, so a book could carry
# several large unclassified or undecomposed positions — each contributing
# zero to `ai_pct` and dropped from `max_sector_pct`'s numerator entirely —
# with nothing in the response saying the concentration figures were computed
# over a partially-legible book.
#
# `cash_anchor_unmatched` (#688): distinct from `cash_unreliable` above, which
# means "the computed balance has no anchor and is a running sum" — this means
# the *opposite* problem, an anchor the user explicitly supplied that could
# not be folded into that balance at all, because its currency matches no
# cash-flow row in this book and no FX rate was available to convert it
# either (`trade_recap.cash_position`'s `unmatched_anchors`). Before #688 this
# was a pure silent drop: `balance`/`reliable` could read back a perfectly
# healthy `true` while a user-declared sum sat nowhere in the response at
# all — AGENTS.md invariant 2 requires this fail closed instead, the same way
# an unusable holding is excluded-and-named rather than dropped-and-silent.
DISCLOSURES = ("cost_basis", "cash_unreliable", "unmapped_driver",
               "unclassified_book", "etf_not_decomposed", "partial_book",
               "cash_anchor_unmatched")

# #751: a cash anchor states a real balance that, by construction, has never
# seen a trade that has not happened yet — there is no `as_of` at which it
# could legitimately already include a still-hypothetical premise's cash
# flow. `trade_recap._cash_balance_one_ccy` sums flows dated strictly after
# the anchor's `as_of`; stamping the premise's own synthetic flow with a date
# no real anchor will ever reach guarantees it is always counted, regardless
# of what `premise.date` says for every other purpose (position dating,
# holding period, splits). See `portfolio_state`'s `premise_row` parameter.
_PREMISE_CASH_FLOW_DATE = dt.date.max

# Keys no code path emits any more, kept valid on a *stored* row so an
# evaluation written before the change still validates and still replays. The
# declaration lives here rather than only in the schema so the drift test
# between the two can stay exact: schemas/trade-evaluation.schema.json's enum
# is DISCLOSURES plus this, and nothing else. The challenge schema does not
# carry them, because that block is emitted live from the current disclosures
# and never stored — there is no historical version of it to keep readable.
RETIRED_DISCLOSURES = ("mixed_currency_no_fx",)

# Why `rows_from_portfolio_basis` left a holding out of the usable book. The
# tuple is the single definition; schemas/trade-evaluation.schema.json and
# schemas/evaluation-challenge.schema.json mirror it, and tests/test_consider.py
# pins the two against this constant.
#
# The first two are missing facts about the holding itself (#515). The third is
# a different kind of gap and #673's whole subject: the ledger's integrity
# record names this holding, so its recorded share count and cost are the
# numbers in doubt — not absent, but not derivable from the history supplied.
# The distinction is load-bearing downstream, because an integrity-excluded
# holding is also the one thing a premise may not be *about* (see
# `_refuse_premise_on_integrity`), while a holding with no cost on record is
# merely outside the denominator.
EXCLUSION_REASONS = ("unusable_shares", "unavailable_cost", "integrity_oversell")

# The subset of the above that means "this holding's own record is untrustworthy".
INTEGRITY_EXCLUSION_REASONS = ("integrity_oversell",)

# ledger integrity `issue` → the exclusion reason it produces. An issue absent
# from this table is refused whole-book by `integrity_exclusions` rather than
# excluded, however clearly it names a ticker: an exclusion is a *disclosed*
# degradation, and this route cannot disclose a warning whose meaning for one
# holding it has no vocabulary for. Only `oversell` can reach here from a
# canonical basis — portfolio_basis.validate_portfolio_basis rejects every
# other issue outright, and `_bad_integrity` drops the whole basis for any
# `bad_*` row before that — so the table is exhaustive in production and the
# refusal below is the defensive floor under a hand-built basis.
_INTEGRITY_EXCLUSION_REASON = {"oversell": "integrity_oversell"}

# rule_collision's own vocabulary — deliberately distinct from
# conditions.VERDICTS and problems.check_rules' broke/held/skipped. Those
# answer "what happened over a realized period"; this answers "does one
# concrete hypothetical, evaluated right now, cross a tracked line".
# Collapsing the two into one vocabulary would let a reader mistake a
# prospective collision check for a retrospective reconciliation verdict.
#
# would_breach and already_over are both "over the line after this trade" —
# they differ only in whether this trade is what put it there. Collapsing
# them back into one value is exactly the bug this vocabulary exists to
# prevent: a book already oversized reads the same as one this trade just
# broke, and a user cutting an oversized position gets told the cut breaks
# their own rule (see the module docstring).
COLLISION_STATES = ("would_breach", "already_over", "clear", "unjudged", "unmapped")

# What the *transaction* does to the rule, which is a different question from
# COLLISION_STATES' "where does the book stand right now" (#579).
#
# The two were never the same question, and owner-live evidence on
# 2026-08-02 is what proved that composing them at prose time is not a
# reliable invariant: a book 80% in one name against a self-authored 20% cap,
# asked about a sell that takes it to 75%, emits `already_over` with
# `worsens: false` — arithmetically correct, and still described back to the
# user as a rule the trade violates or worsens. `already_over` is an absolute
# state and reads as an accusation; the direction was a nullable boolean one
# level down.
#
# Two of these six transitions are additionally *not derivable* from
# `state`/`worsens` at all, whatever the prompt says, which is why this is a
# code fix rather than a wording one:
#
#   improved_but_still_over   already_over + worsens:false
#   unchanged_existing_breach already_over + worsens:false   <- same payload
#   resolved_existing_breach  clear
#   compliant                 clear                          <- same payload
#
# `_worsened` is a strict `>`, so "reduced" and "did not move" collapse into
# one boolean; and `clear` cannot say whether the line was crossed before.
# `unjudged`/`unmapped` are carried through unchanged from COLLISION_STATES:
# an effect vocabulary that could not say "not evaluated" would have to
# invent a verdict for a rule the engine never judged, which is the one
# outcome the collision vocabulary exists to prevent.
RULE_EFFECTS = ("new_breach", "worsened_existing_breach", "improved_but_still_over",
                "resolved_existing_breach", "unchanged_existing_breach", "compliant",
                "unjudged", "unmapped")

# The effects that describe a transition of a real threshold — the ones a
# reversed reading can misstate. `compliant` is the true non-event and
# `unjudged`/`unmapped` are the absence of a judgment, so neither can be
# reversed into its opposite.
DIRECTIONAL_RULE_EFFECTS = ("new_breach", "worsened_existing_breach",
                            "improved_but_still_over", "resolved_existing_breach",
                            "unchanged_existing_breach")

# Which `state` each effect may legally accompany. This is the "validators
# must reject disagreement with the recomputed effect" clause of #579, stated
# once so a hand-built or replayed row carrying an effect that contradicts its
# own state fails closed rather than reaching an answer. It is deliberately a
# many-to-many table rather than a bijection, because the two vocabularies
# genuinely answer different questions:
#
#   `new_breach` accompanies `already_over` as well as `would_breach`. A sell
#   shrinks the denominator, so a *different* position's weight can cross the
#   line on a trade whose own ticker did not — `_max_pos_pct_collision`'s
#   second branch calls that `already_over` even though the book was under the
#   line before. The state vocabulary cannot express it; the effect can, and
#   saying `new_breach` there is the more truthful of the two readings.
#
#   `already_over` covers three effects because `worsens` collapses two of
#   them (above), and `clear` covers two because it says nothing about before.
_EFFECT_STATES = {
    "new_breach": ("would_breach", "already_over"),
    "worsened_existing_breach": ("already_over",),
    "improved_but_still_over": ("already_over",),
    "unchanged_existing_breach": ("already_over",),
    "resolved_existing_breach": ("clear",),
    "compliant": ("clear",),
    "unjudged": ("unjudged",),
    "unmapped": ("unmapped",),
}

# Where the line this rule was judged against came from. The user's own cap
# when they set one (`trade_recap.valid_position_cap` accepted it), otherwise
# a threshold this engine picked. Carried rather than inferred because the
# answer says "your 20% rule" out loud, and a product default wearing the
# user's name is a claim they never made.
LIMIT_SOURCES = ("user_cap", "engine_default")

# Tolerance for "did this reading move", below which a delta is float noise
# rather than a real direction — the same role trade_recap's own 1e-9/1e-6
# guards play throughout this module.
_EPSILON = 1e-9

# worsens (already_over only) reads this metric_key's own portfolio_state
# field — top3_pct's field is named "top3", not "top3_pct" (portfolio_state
# mirrors dim_diversify's own key name); the mapping makes that explicit
# rather than a silent string mismatch.
_CONCENTRATION_READING_FIELD = {"ai_pct": "ai_pct", "max_sector_pct": "max_sector_pct",
                                "top3_pct": "top3"}

# The metric_key values this module can evaluate from a single hypothetical
# trade (session.py's PKEY has seven keys total; the other two —
# exit_severity, hold_severity — describe realized behaviour across history
# and always fall to "unjudged", below).
EVALUABLE_METRIC_KEYS = frozenset({"max_pos_pct", "avgdown_count", "ai_pct",
                                   "max_sector_pct", "top3_pct"})

# trade_recap.driver()'s own fallback sentinel (trade_recap.py:95) for a
# ticker with no sector/AI entry. dim_diversify already special-cases this
# exact literal (trade_recap.py:1159) to keep an unmapped ticker from
# counting toward concentration; comparing against it here reads the same
# sentinel the engine already treats as "no driver map entry", not a second,
# independently chosen marker.
_UNCLASSIFIED_DRIVER = "未分類"

_FIELDS = frozenset({"ticker", "side", "price", "qty", "notional", "date", "currency"})
_TICKER_RE = re.compile(r"^[A-Za-z0-9^][A-Za-z0-9.\-^=]{0,19}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


# ─────────────────────────── field validators ───────────────────────────
# Small, local, and deliberately not imported from price_feed.py/conditions.py:
# each of those modules already hand-rolls the same primitives for its own
# envelope, and a numeric/date/ticker check is a stable enough primitive that
# copying the pattern (not the logic of anything that actually evolves) costs
# less than a cross-module dependency between two independent envelopes.

def _positive_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConsequenceError(f"premise.{field} must be a number")
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise ConsequenceError(f"premise.{field} must be a finite number")
    if value <= 0:
        raise ConsequenceError(f"premise.{field} must be positive (got {value})")
    return value


def _ticker(value):
    """The premise's instrument identity — canonical, then admitted.

    The exception to the "deliberately not imported" note above, and the reason
    it is an exception: shape is this envelope's own business, but *identity* is
    the book's. ``symbols.canonical_ticker`` is the one rule the holdings map,
    the price map and the prior-decision reader all read the same way, so a
    premise written ``nvda`` names the held ``NVDA`` here rather than minting a
    second position beside it (#803). ``_TICKER_RE`` still decides what this
    envelope accepts, unchanged, and is applied to the canonical form — the
    pattern already admits both cases, so no input that was accepted before is
    rejected now and none that was rejected is accepted.
    """
    if not isinstance(value, str) or not value.strip():
        raise ConsequenceError("premise.ticker must be a non-empty string")
    symbol = symbols.canonical_ticker(value)
    if not _TICKER_RE.match(symbol):
        raise ConsequenceError(f"premise.ticker is not a usable engine symbol: {value!r}")
    return symbol


def _currency(value):
    if not isinstance(value, str) or not value.strip():
        raise ConsequenceError("premise.currency must be a non-empty string")
    code = value.strip().upper()
    if not _CURRENCY_RE.match(code):
        raise ConsequenceError(f"premise.currency must be a three-letter currency code (got {value!r})")
    return code


def _date_value(value, field):
    if not isinstance(value, str):
        raise ConsequenceError(f"premise.{field} must be an ISO date string (YYYY-MM-DD)")
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        raise ConsequenceError(f"premise.{field} is not an ISO date: {value!r}")


def _fifo_held(rows):
    """The book's held shares/cost, FIFO basis — the convention trade_recap's
    own pipeline uses for every weight-bearing computation (dim_size,
    dim_diversify, cash_position's held_mv). positions()'s avg-cost held stays
    scoped to average-down detection only; see trade_recap.fifo_held's own
    docstring for why the two bases must not be mixed."""
    _, open_lots = trade_recap.round_trips(rows)
    return trade_recap.fifo_held(open_lots)


def _canonical_rows(rows):
    """``rows`` with one canonical instrument identity each (#803).

    The book side of the premise fix, and the reason it is here rather than at
    each row source: ``validate_premise`` now canonicalizes the premise, so a
    book still carrying a *non*-canonical spelling of that same instrument
    would no longer match it — the defect would simply move from "lower-case
    premise, upper-case book" to "canonical premise, lower-case book". Both
    supported row sources therefore pass through this one function: the CSV
    lane (``trade_recap.load``) and the ledger lane
    (:func:`rows_from_portfolio_basis`). It is the arithmetic's own boundary,
    so no ``cycle_id`` — a durable identifier derived from the *stored*
    spelling elsewhere in the engine — is re-derived by anything here.

    Identity is preserved when nothing changes: a book already spelled
    canonically (every broker export in the wild, every fixture in this
    repository) gets back the same list holding the same row objects. That is
    not an optimization. ``portfolio_state`` distinguishes the one appended
    hypothetical row from history by ``is not`` (#751), and copying rows
    unconditionally would silently break that identity check.

    Two rows whose spellings differ but canonicalize to one symbol are the same
    instrument's executions and are merged by every downstream sum, which is
    correct — unless the two stored rows disagree about what that instrument
    *is*. A ``TWD`` row and a ``USD`` row that collapse into one symbol are not
    reconcilable by case-folding, and merging them would put two currencies'
    face values behind a single share count. That fails closed, naming both
    spellings, rather than double-counting silently (#803's legacy-collision
    rule). A disagreement between two rows that already share one spelling is
    not this function's to judge — ``trade_recap.currency_map`` has owned that
    since #51 and still does.
    """
    if not rows:
        return rows
    spellings, facts = {}, {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        canonical = symbols.canonical_ticker(row.get("ticker"))
        if canonical is None:
            continue
        spellings.setdefault(canonical, set()).add(row["ticker"])
        # Compared canonically too (#803): `US` and `us` are one market, and
        # reading them as two would refuse an ordinary book for a collision
        # that exists only in the spelling of its metadata.
        facts.setdefault(canonical, set()).add(
            (str(row.get("currency") or "USD").strip().upper(),
             str(row.get("market") or "US").strip().upper()))
    for canonical, written in sorted(spellings.items()):
        # Both conditions, not either: one spelling recorded two ways is
        # trade_recap.currency_map's long-standing case and is left to it, and
        # two spellings agreeing on the facts are one instrument's executions.
        # Only the intersection is a collision case-folding created.
        if len(written) > 1 and len(facts[canonical]) > 1:
            raise ConsequenceError(
                f"this book records {', '.join(repr(name) for name in sorted(written))} — one "
                f"instrument ({canonical}) — under more than one currency/market: "
                + "; ".join(f"{cur}/{mkt}" for cur, mkt in sorted(facts[canonical]))
                + ". Case alone cannot reconcile them, and adding them together would put two "
                f"denominators behind one share count. Record {canonical} one way in the "
                "history, then ask again.")
    if all(written == {canonical} for canonical, written in spellings.items()):
        return rows          # already canonical throughout: same list, same row objects
    out = []
    for row in rows:
        canonical = symbols.canonical_ticker(row.get("ticker")) if isinstance(row, Mapping) else None
        out.append(row if canonical is None or row["ticker"] == canonical
                   else dict(row, ticker=canonical))
    return out


def integrity_exclusions(basis):
    """``{ticker: exclusion reason}`` for every integrity warning on ``basis``.

    Owner ruling on #673. The canonical integrity record is a list of
    per-holding accounting warnings — one ``oversell`` row per sell with no
    matching prior buy, each naming its own ticker — and gating the *whole*
    basis on that list being non-empty turned one such sell anywhere in the
    history into a permanent, account-wide refusal of every ``consider`` call.
    Permanent because the rows are derived from history that has already
    happened: no future trade clears them. The review lane treats the identical
    condition as a disclosure and delivers the card, so a book the review lane
    could review was a book the pre-trade lane could not evaluate at all.

    What an ``oversell`` actually damages is that one ticker's share count. The
    replay clamps the sell to the shares it can see, so a user who bought 100
    before the export window, then bought 10 and sold 30 inside it, has a
    recorded position of zero and a true position of 80. Every *other* holding
    is replayed independently and is untouched. So the warning is scoped to the
    holding it names, and this function is where that scoping happens.

    Raises ``ConsequenceError`` for a warning that cannot be scoped — no
    ticker, or an ``issue`` outside ``_INTEGRITY_EXCLUSION_REASON``. That
    refusal is still whole-book, deliberately: a warning this route cannot name
    a reason for is one it cannot disclose, and silently absorbing it into the
    usable book is the failure mode the exclusion exists to prevent.
    """
    if not isinstance(basis, Mapping):
        raise ConsequenceError("canonical PortfolioBasis is not an object")
    warnings = basis.get("integrity") or ()
    if not isinstance(warnings, (list, tuple)):
        raise ConsequenceError("canonical PortfolioBasis integrity record is not a list")
    out = {}
    for row in warnings:
        issue = row.get("issue") if isinstance(row, Mapping) else None
        ticker = row.get("ticker") if isinstance(row, Mapping) else None
        reason = _INTEGRITY_EXCLUSION_REASON.get(issue) if isinstance(issue, str) else None
        if reason is None or not isinstance(ticker, str) or not ticker.strip():
            named = issue if isinstance(issue, str) and issue else "an unnamed issue"
            raise ConsequenceError(
                "canonical PortfolioBasis has an integrity warning that cannot be scoped "
                f"to one holding: {named}")
        # Keyed canonically (#803), because every reader of this map compares it
        # against a holding or a premise that is canonical by the time it gets
        # here. A warning naming `nvda` scopes to the `NVDA` it is about.
        out[symbols.canonical_ticker(ticker)] = reason
    return out


def integrity_excluded_tickers(excluded_holdings):
    """The tickers in ``excluded_holdings`` that were excluded for an integrity
    reason rather than a missing valuation fact.

    One definition, read by every consumer that has to tell the two apart — the
    canonical-`before` facade in review.py must not accuse an integrity-excluded
    holding of having no cost on record, and the premise gate below must refuse
    only for this half. A caller re-deriving the distinction from the reason
    string beside its own presentation is how the two would come to disagree.
    """
    integrity = set(INTEGRITY_EXCLUSION_REASONS)
    return {row["ticker"] for row in excluded_holdings or ()
            if isinstance(row, Mapping) and row.get("reason") in integrity
            and isinstance(row.get("ticker"), str)}


def _refuse_premise_on_integrity(premise, excluded_holdings):
    """Refuse a premise that is *about* an integrity-excluded holding, naming
    the ticker and the reason.

    The bounded answer #673 restores is an answer about the rest of the book.
    It is not an answer about the damaged holding itself: the whole content of
    a consequence for ``ORPH`` is what the position becomes, and the position it
    starts from is exactly the number the integrity warning says is not
    derivable.

    Runs before ``validate_premise`` on purpose. That validator would reach a
    sell of an excluded holding first and refuse it as "not currently held",
    which is a claim about the book — and the wrong claim, since the recorded
    zero is precisely what is in doubt.

    Scoped to integrity exclusions. A holding excluded for a *missing* fact
    (`unavailable_cost`) is a different case that #528 owns, and this leaf does
    not change it.
    """
    if not isinstance(premise, Mapping):
        return
    ticker = symbols.canonical_ticker(premise.get("ticker"))
    if ticker is None:
        return
    for row in excluded_holdings or ():
        if (isinstance(row, Mapping) and symbols.canonical_ticker(row.get("ticker")) == ticker
                and row.get("reason") in INTEGRITY_EXCLUSION_REASONS):
            raise ConsequenceError(
                f"this book's record for {ticker} carries an integrity warning "
                f"({row['reason']}), so its recorded shares and cost are not derivable from the "
                f"supplied history and no consequence for a trade in {ticker} can be computed "
                "from them. The rest of the book is unaffected and can still be asked about; "
                f"supplying the transactions that {ticker}'s history is missing is what makes "
                "this question answerable.")


def rows_from_portfolio_basis(basis):
    """Adapt one canonical current book to consequence input rows.

    ``PortfolioBasis`` is the owner of the held shares and average cost for a
    ledger-backed current-book question.  Replaying its source events here
    would create a second reader with a different lot convention (FIFO versus
    the ledger's average-cost book).  Instead, make one synthetic opening row
    per already-held position.  These rows are merely the input shape required
    by the established consequence arithmetic; their quantities and costs are
    copied from the frozen basis, never re-derived.

    Returns ``(rows, excluded)``.

    An empty basis cannot make a current-book verdict at all.  A basis where
    *some* holding cannot be used is a different thing, and the owner ruled on
    it twice: for a holding that cannot be valued (#515, 2026-07-28) and then
    for a holding the integrity record names (#673, 2026-07-31).  Both rulings
    are the same sentence: compute over the part that can be used and name what
    was left out.  A user holding six positions where one has no cost -- or one
    whose history carries an unmatched sell -- gets an answer about the usable
    part of their book, not a refusal.  A refusal gives them nothing and is
    indistinguishable from a broken product.

    So a holding that cannot become a usable row is EXCLUDED and recorded here,
    never silently dropped and never fatal.  Every caller must carry
    ``excluded`` to wherever the derived numbers are stated: a partial
    denominator that does not say it is partial is worse than the refusal it
    replaced (#515's first invariant).  The only remaining refusal on this path
    is "nothing was usable", which is an empty denominator rather than a
    bounded one.

    ``basis["cost_basis"]`` is deliberately not read.  It is a whole-book
    summary of a per-holding fact (``partial_average_cost`` means *some*
    holding lacks a cost), and gating on it refused six positions because of
    one -- the exact defect #515 removed.  ``basis["integrity"]`` was the same
    shape of whole-book summary over a per-holding fact and was still gated
    whole until #673; it is now read through :func:`integrity_exclusions`,
    which scopes each warning to the holding it names.  How the book was
    declared -- snapshot anchor versus replayed trade history, partial versus
    unverified -- never gates this adapter either (#485); only a genuinely
    missing or genuinely underivable fact does, and only for the holding it is
    actually missing or underivable for.

    An integrity warning naming a ticker this book does not currently hold is
    still recorded in ``excluded``.  It removes nothing from the denominator --
    there was nothing of it there to remove -- but a recorded zero is exactly
    what an unmatched sell puts in doubt, so "this book has no position in X"
    is not a fact this answer may rest on, and the caller has to be able to say
    so.  It is also what the premise gate reads.
    """
    integrity = integrity_exclusions(basis)
    current_book = basis.get("current_book")
    holdings = current_book.get("holdings") if isinstance(current_book, Mapping) else None
    if not isinstance(holdings, Mapping) or not holdings:
        raise ConsequenceError("canonical PortfolioBasis has no held positions")
    try:
        as_of = dt.date.fromisoformat(str(basis["as_of"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ConsequenceError("canonical PortfolioBasis has invalid as_of") from exc

    rows, excluded = [], []
    for stored in sorted(holdings):
        holding = holdings[stored]
        if not isinstance(stored, str) or not isinstance(holding, Mapping):
            raise ConsequenceError("canonical PortfolioBasis has invalid holding")
        # #803. The basis is the ledger lane's canonical book and
        # ``ledger.derive_holdings`` already keys it canonically; a book written
        # before that did not, and this adapter is where it reaches arithmetic
        # that now compares against a canonical premise. Read through the one
        # rule rather than trusting the key, so a legacy row projects onto the
        # instrument it names instead of beside it.
        ticker = symbols.canonical_ticker(stored)
        if ticker in integrity:
            # Checked before shares and cost, because those are the values the
            # warning is about: a clamped replay leaves a perfectly well-formed
            # positive share count that is simply not the user's position.
            excluded.append({"ticker": ticker, "reason": integrity[ticker]})
            continue
        try:
            qty = float(holding["shares"])
        except (KeyError, TypeError, ValueError):
            qty = None
        if qty is None or not math.isfinite(qty) or qty <= 0:
            excluded.append({"ticker": ticker, "reason": "unusable_shares"})
            continue
        try:
            # PortfolioBasis rounds avg_cost for display but preserves the
            # canonical cost_total.  Derive the synthetic row price from the
            # latter so a rounded display average cannot alter the held cost.
            price = float(holding["cost_total"]) / qty
        except (KeyError, TypeError, ValueError):
            price = None
        if price is None or not math.isfinite(price) or price <= 0:
            excluded.append({"ticker": ticker, "reason": "unavailable_cost"})
            continue
        # The row carries the *stored* spelling, so ``_canonical_rows`` below
        # still sees two legacy spellings of one symbol and can refuse a book
        # whose two records disagree, instead of being handed a collapse that
        # already happened.
        rows.append({"ticker": stored, "side": "buy", "qty": qty, "price": price,
                     "date": as_of, "market": holding.get("market", "US"),
                     "currency": holding.get("currency", "USD")})
    # A warning naming a ticker this book does not hold excludes nothing from
    # the denominator, and is recorded anyway -- see the docstring. Appended
    # after the holdings loop, then sorted, so the list stays one ticker-ordered
    # record whichever half an entry came from.
    held_symbols = {symbols.canonical_ticker(stored) for stored in holdings
                    if isinstance(stored, str)}
    for ticker in sorted(set(integrity) - held_symbols):
        excluded.append({"ticker": ticker, "reason": integrity[ticker]})
    excluded.sort(key=lambda row: row["ticker"])
    if not rows:
        # The floor under exclude-and-disclose: an empty denominator is not a
        # bounded answer. Named with each holding's own reason, because "no
        # holding could be valued" is false for a book emptied by integrity
        # warnings, and the reason is what tells the user which repair --
        # a cost, or the missing transactions -- would make the book answerable.
        raise ConsequenceError(
            "canonical PortfolioBasis has no usable holding: "
            + ", ".join(f"{row['ticker']} ({row['reason']})" for row in excluded))
    return _canonical_rows(rows), excluded


# ─────────────────────────────── validation ───────────────────────────────

def validate_premise(premise, rows, last_px=None):
    """Validate one hypothetical trade into a normalized dict.

    Mirrors trade-premise.schema.json's rules in code — this offline suite
    carries no jsonschema dependency, the same reason conditions.py and
    price_feed.py hand-roll their own checks. Fails closed with
    ConsequenceError, naming the field, on: an unrecognized side, a
    non-positive price/qty/notional, both or neither of qty/notional, a date
    earlier than the ledger's last row, or a sell of a ticker not currently
    held (or of more shares than are held) — this layer computes the
    arithmetic of a real position change, never a speculative short.

    `rows` must already be chronologically sorted ascending — the same
    precondition every trade_recap function that walks rows in list order
    assumes (trade_recap.load sorts once, at the boundary; round_trips,
    positions, and dim_size do not re-sort).

    `price` is optional (#777, owner ruling 2026-08-02: "if the discussion
    doesn't state a price, use the transaction price ... the market
    transaction price"). When the caller states none, `last_px` — exactly
    the map `cmd_consider` already resolved before this call, the same one
    `portfolio_state` prices every holding from — supplies the engine's own
    observed close for this ticker, so a defaulted premise prices at the
    identical close the rest of the answer is computed at, never a second,
    independent lookup. Refused, naming the ticker, when neither a stated
    price nor an observed one is available: the acceptance criterion this
    default must not weaken is that a premise this engine cannot price is
    still refused, never silently answered on an invented number.

    Returns a normalized dict with exactly {ticker, side, qty, price,
    price_basis, date, currency}. `price_basis` is `"user_stated"` or
    `"observed"` and is never a caller-supplied field (`_FIELDS` excludes
    it) — it is what lets a later reader, including the evaluation's own
    stored row, tell which of the two happened. `notional`, when supplied,
    is consumed here and converted to qty — it never appears on the
    normalized form, so nothing downstream of validation has to handle both
    shapes of the same fact.

    `rows` reach the held-shares check through `_canonical_rows` (#803), the
    same boundary every other entry point here uses, so "is this held" is asked
    of one instrument identity on both sides rather than of two spellings.
    """
    rows = _canonical_rows(rows)
    if not isinstance(premise, dict):
        raise ConsequenceError("premise must be an object")
    unknown = set(premise) - _FIELDS
    if unknown:
        raise ConsequenceError("premise has unknown fields: " + ", ".join(sorted(unknown)))

    ticker = _ticker(premise.get("ticker"))
    side = premise.get("side")
    if side not in SIDES:
        raise ConsequenceError("premise.side must be one of " + ", ".join(SIDES))
    if premise.get("price") is not None:
        price = _positive_number(premise["price"], "price")
        price_basis = "user_stated"
    else:
        observed = (last_px or {}).get(ticker)
        if observed is None:
            raise ConsequenceError(
                f"premise.price was not supplied and the engine has no observed close for "
                f"{ticker} to default to; state a price, or supply --prices with a close for "
                f"{ticker}")
        price = _positive_number(observed, "price")
        price_basis = "observed"

    has_qty = premise.get("qty") is not None
    has_notional = premise.get("notional") is not None
    if has_qty == has_notional:
        raise ConsequenceError("premise must carry exactly one of qty or notional")
    if has_qty:
        qty = _positive_number(premise["qty"], "qty")
    else:
        notional = _positive_number(premise["notional"], "notional")
        qty = notional / price

    last_row_date = rows[-1]["date"] if rows else None
    if premise.get("date") is not None:
        date = _date_value(premise["date"], "date")
        if last_row_date is not None and date < last_row_date:
            raise ConsequenceError(
                f"premise.date ({date.isoformat()}) is earlier than the ledger's "
                f"last row ({last_row_date.isoformat()})")
    elif last_row_date is not None:
        date = last_row_date + dt.timedelta(days=1)
    else:
        date = dt.date.today()

    if premise.get("currency") is not None:
        currency = _currency(premise["currency"])
    else:
        cur_map, _currencies, _conflicts = trade_recap.currency_map(rows)
        currency = cur_map.get(ticker, "USD")

    if side == "sell":
        held = _fifo_held(rows)
        current_shares = held.get(ticker, (0.0, 0.0))[0]
        if current_shares <= 1e-9:
            raise ConsequenceError(f"premise sells {ticker}, which is not currently held")
        if qty > current_shares + 1e-6:
            raise ConsequenceError(
                f"premise sells {qty:g} shares of {ticker}, more than the "
                f"{current_shares:g} currently held")

    return {"ticker": ticker, "side": side, "qty": qty, "price": price,
            "price_basis": price_basis, "date": date, "currency": currency}


def _premise_row(normalized):
    """The normalized premise, shaped as one more trade_recap row so it can be
    appended to `rows` and run through round_trips/positions/dim_size/
    dim_diversify/cash_position unmodified.

    `market` is not part of the premise envelope — nothing this module calls
    reads a row's market field — and defaults to "US" purely for row-shape
    completeness with trade_recap.load's own output."""
    return dict(ticker=normalized["ticker"], side=normalized["side"],
                qty=normalized["qty"], price=normalized["price"],
                date=normalized["date"], market="US", currency=normalized["currency"])


# ─────────────────────────────── state ───────────────────────────────

def portfolio_state(rows, last_px=None, max_pos_override=None, cash_anchor=None, fx=None,
                     premise_row=None):
    """The book as of the end of `rows`. Every number here is trade_recap's own
    function output, unmodified — this assembles a snapshot, it does not
    compute anything new. Calling it once on `rows` and once on `rows` plus
    one appended hypothetical row is how `consequence` derives before/after.

    `basis` records whether weights/pct fields came from market prices or
    cost: "priced" when `last_px` carries at least one close, "cost" when it
    is absent. A consequence computed on cost basis is a different claim from
    one computed on live prices, and a caller must be able to tell which one
    it is holding.

    Raises ConsequenceError when the book mixes currencies and `fx` does not
    cover one of them (#600). Cost basis is a *different* denominator that is
    still internally consistent; an unconverted currency is not a denominator
    at all.

    `premise_row` (#751): pass the same row object appended onto the tail of
    `rows` when this state is the "after" side of one hypothetical trade —
    `consequence()` is the one caller that does. Every other caller leaves it
    at the default `None` and sees no change at all. When supplied, this
    row's own cash flow is computed separately from the rest of `rows` and
    stamped with `_PREMISE_CASH_FLOW_DATE` before reaching
    `trade_recap.cash_position`, so a `cash_anchor` whose `as_of` happens to
    fall on or after `premise_row`'s own date can no longer make the anchor's
    date-filtered sum skip it — the silent non-deduction #751 reported.
    Historical rows keep exactly the flow dates they always had; only the one
    hypothetical row's date is ever overridden, and only for this purpose —
    `held`/`weights`/round-trips below still read `premise_row`'s real date.

    Every ticker below is one canonical instrument identity (#803).
    `_canonical_rows` returns the caller's own list untouched when it already
    is one — which is what keeps `premise_row`'s `is not` identity above intact
    on the path that matters, since `consequence()` canonicalizes the history
    once before appending the (already canonical) hypothetical row.
    """
    rows = _canonical_rows(rows)
    last_px = last_px or {}
    fx = dict(fx or {})
    fx.setdefault("USD", 1.0)

    held = _fifo_held(rows)
    rts, _open_lots = trade_recap.round_trips(rows)

    # Multi-market currency (#51/#129): aggregation (weights, sizing,
    # diversification, cash) must happen in one common currency or a mixed
    # book silently adds TWD and USD face values together. A single-currency
    # book (including an all-TWD one) is self-consistent without fx, matching
    # trade_recap's own "單一幣別組合...零行為變化" convention.
    cur_map, currencies, _conflicts = trade_recap.currency_map(rows)
    mixed_currency = len(currencies) > 1
    # #612: one predicate, read rather than restated. `usd_view` raises on the
    # same condition, so this branch is what turns the shared refusal into this
    # lane's own error type before the conversion is reached; a second copy of
    # the rule here is how the two lanes drifted apart in the first place.
    fx_gaps = trade_recap.held_currency_fx_gaps(cur_map, fx)
    if fx_gaps:
        # #600. `usd_view` resolves a currency absent from `fx` as a factor of
        # 1.0, so every holding in it entered the denominator at raw face
        # value. That is not a gap in the numbers, it is a different number:
        # one large TWD position beside several USD ones reads at ~31x its
        # real weight, which suppresses every USD holding's share and can
        # invert which position is the largest. Nothing downstream — weights,
        # max_pct, top3, ai_pct, max_sector_pct, cash weight — survives it, so
        # there is no per-field carve-out to make in the shape `partial_book`
        # has; the whole state is refused. The remedy is in the message
        # because the caller can act on it in one round trip.
        raise ConsequenceError(
            f"this book holds {', '.join(currencies)} and no FX rate was supplied for "
            f"{', '.join(fx_gaps)}, so its holdings cannot be added into one denominator. "
            "Every weight, concentration figure and cash share would be computed with "
            f"1 {fx_gaps[0]} treated as 1 USD. Supply the rate through --prices (the "
            "`fx` block in references/price-feed.md), then ask again.")

    if mixed_currency:
        _rts_v, held_v, lastpx_v = trade_recap.usd_view(rts, held, last_px, cur_map, fx)
    else:
        held_v, lastpx_v = held, last_px

    size = trade_recap.dim_size(rows, held_v, lastpx_v, max_pos_override)
    diversify = trade_recap.dim_diversify(held_v, lastpx_v)

    # Cash flows derived from `rows` itself (no CSV paths: paths=[] with
    # trade_rows given makes load_cash_flows estimate qty x price per row,
    # #375's fallback path), so an appended hypothetical buy/sell flows
    # through the cash balance automatically.
    if premise_row is None:
        cash_flows = trade_recap.load_cash_flows([], trade_rows=rows)
    else:
        # #751. Identity, not equality: `_premise_row` builds a fresh dict
        # every call, so this isolates exactly the one appended row even
        # when a historical row happens to share the same ticker/date/price.
        historical_rows = [row for row in rows if row is not premise_row]
        cash_flows = trade_recap.load_cash_flows([], trade_rows=historical_rows)
        premise_flows = trade_recap.load_cash_flows([], trade_rows=[premise_row])
        for flow in premise_flows:
            flow["date"] = _PREMISE_CASH_FLOW_DATE
        cash_flows = cash_flows + premise_flows
    held_mv = sum((sh * lastpx_v[t]) if lastpx_v.get(t) else c for t, (sh, c) in held_v.items())
    cash = trade_recap.cash_position(cash_flows, held_mv, anchor=cash_anchor, fx=fx)
    unclassified_holdings, undecomposed_etfs = book_legibility(held, size["weights"])

    return {
        "held": {t: {"shares": sh, "cost": c} for t, (sh, c) in sorted(held.items())},
        "weights": size["weights"],
        "max_ticker": size["max_ticker"],
        "max_pct": size["max_pct"],
        "oversize_triggered": size["triggered"],
        "top3": diversify["top3"],
        "ai_pct": diversify["ai_pct"],
        "max_sector": diversify["max_sector"],
        "max_sector_pct": diversify["max_sector_pct"],
        # Not on the caller's required minimum, but rule_collision needs
        # dim_diversify's own combined trigger (ai_pct/max_sector_pct/top3_pct
        # all reconcile to the single "concentration" problem_key off this one
        # flag — see build_problem_events) and gets it by reading this field
        # rather than recomputing dim_diversify a second time.
        "concentration_triggered": diversify["triggered"],
        "n_holdings": len(held),
        "cash": {"balance": cash["balance"], "weight": cash["weight"],
                 "source": cash["source"], "reliable": cash["reliable"],
                 # #688: named anchors trade_recap.cash_position could not fold
                 # into balance/by_currency (no matching cash-flow currency and
                 # no fx rate to convert one at) — forwarded so a caller can
                 # name what was excluded rather than let it read as never
                 # having been supplied. Independent of cash_anchor's shape
                 # (single dict or per-currency list) and of the premise: an
                 # anchor's own currency plays no part in what the premise
                 # trade does, so before and after always carry the same list.
                 "unmatched_anchors": cash["unmatched_anchors"]},
        "basis": "priced" if last_px else "cost",
        # True means these weights were converted into one currency at the
        # caller's supplied rates before being added — a fact about how the
        # denominator was built, and the reason a reader may trust it. It is
        # not a limitation key: the case where it *would* be one no longer
        # reaches this return at all (#600, above). There is no companion
        # `fx_gaps` list any more, because after that refusal it could only
        # ever be empty, and a field whose only possible value is "nothing
        # was missing" is the written-never-read shape #429 names.
        "mixed_currency": mixed_currency,
        # #598/#599, and deliberately here rather than one layer up in
        # `consequence()`: these name the positions the two readings above were
        # measured *without*, so they belong in the same dict as the readings.
        # Any consumer of a state gets both or neither. See book_legibility.
        "unclassified_holdings": unclassified_holdings,
        "undecomposed_etfs": undecomposed_etfs,
    }


def book_legibility(held, weights):
    """What the concentration figures over this book could not see (#598/#599).

    Public and taking its two inputs directly, because the answer belongs
    beside the numbers it qualifies rather than one layer above them. It is
    called from `portfolio_state`, so **every** consumer of a state — including
    a future workflow that calls `portfolio_state` and never touches
    `consequence()` — gets `ai_pct` and `max_sector_pct` together with the
    positions those readings were measured without. That was not true when this
    lived in `consequence()` alone: a probe workflow written the obvious way
    (read the book, print the weights and `ai_pct`) got the silent version, with
    the whole suite green. A limitation that only some callers of a number
    receive is the honesty equivalent of the split map every reader of the book
    must be handed (#550), and it is placed here for the same reason.

    The one other call site is `review._canonical_consider_before`, which
    replaces a state's `weights` with the canonical projection's after
    `portfolio_state` has returned; it recomputes rather than keeping the
    superseded answer, so a listed weight is always the weight that will be
    shown. One implementation, two call sites — the rule is a single
    derivation, not a single caller.

    Two disjoint lists, each returning `[{ticker, weight,
    ...}]` sorted by weight descending. Every entry is a position whose weight
    is real and whose *composition* is not, which is the property `ai_pct` and
    `max_sector_pct` are silently measured around:

    - `unclassified` — a single name `trade_recap.driver()` has no entry for.
      It contributes zero to `ai_pct`, and `dim_diversify` drops the whole
      "未分類" bucket from `max_sector_pct`'s numerator on purpose
      (trade_recap.py, `classified_sec`) so an unbuilt driver map cannot
      impersonate a concentration signal. That is the right arithmetic and it
      is invisible: a book can be four-fifths semiconductors and report a
      comfortable `max_sector_pct` with nothing saying so. The remedy is
      `--driver-map`, which is why the disclosure exists at all — the engine's
      built-in table is an explicitly partial "common stock fallback" with, for
      example, no entry for any foreign listing, so the same company under its
      primary listing and under its US ADR classify differently.

    - `etfs` — a holding `instruments` recognizes as a fund. Nothing anywhere
      in this engine decomposes one into its constituents: an allocation kind
      is exempted from concentration wholesale, a sector/thematic kind counts
      as one opaque ticker, and neither contributes the sector or AI exposure
      it actually holds. A recognized semiconductor ETF and a broad world
      index fund of the same size are equally invisible to `ai_pct`.

    Disjoint on purpose, so one position is never owed twice: a fund the
    instrument map does not recognize is not an ETF as far as this engine is
    concerned — it is an unclassified single name, and it lands in the first
    list, where `--instrument-map` is the second half of the remedy.

    Both apply `trade_recap.RESIDUAL_POS_TH`, the residual floor #172 already
    uses to keep a dividend fraction or a one-share tail out of every
    diagnostic. A required disclosure is a sentence the answer owes; a 0.05%
    dust position does not move a concentration figure and does not earn one.
    """
    weights = weights or {}
    unclassified, etfs = [], []
    for ticker in sorted(held or {}):
        weight = float(weights.get(ticker) or 0.0)
        if weight < trade_recap.RESIDUAL_POS_TH:
            continue
        meta = instruments.info(ticker)
        if meta["is_etf"]:
            etfs.append({"ticker": ticker, "weight": weight, "kind": meta["kind"],
                         "allocation_exempt": meta["allocation_exempt"]})
        elif trade_recap.driver(ticker)[0] == _UNCLASSIFIED_DRIVER:
            unclassified.append({"ticker": ticker, "weight": weight})
    key = lambda row: (-row["weight"], row["ticker"])
    return sorted(unclassified, key=key), sorted(etfs, key=key)


_DELTA_FIELDS = ("max_pct", "top3", "ai_pct", "max_sector_pct", "n_holdings")


def _delta(before, after, ticker):
    """Signed after-minus-before changes for the numeric readings that moved.
    A field that did not move is absent, not zero — silence here means "no
    change", not "not computed" (contrast with a disclosure key, which exists
    precisely for the "not computed" case). Boolean flags
    (oversize_triggered, concentration_triggered) and identity fields
    (max_ticker, max_sector) are not "numeric readings" and are not
    duplicated here; a caller compares before/after directly for those."""
    out = {}
    for field in _DELTA_FIELDS:
        b, a = before.get(field), after.get(field)
        if isinstance(b, (int, float)) and isinstance(a, (int, float)) and abs(a - b) > 1e-9:
            out[field] = a - b
    b_w = before["weights"].get(ticker, 0.0)
    a_w = after["weights"].get(ticker, 0.0)
    if abs(a_w - b_w) > 1e-9:
        out["ticker_weight"] = a_w - b_w
    cash_delta = {}
    for field in ("balance", "weight"):
        b, a = before["cash"].get(field), after["cash"].get(field)
        if isinstance(b, (int, float)) and isinstance(a, (int, float)) and abs(a - b) > 1e-9:
            cash_delta[field] = a - b
    if cash_delta:
        out["cash"] = cash_delta
    return out


def consequence(rows, premise, last_px=None, max_pos_override=None, cash_anchor=None, fx=None,
                before_override=None, excluded_holdings=()):
    """{"premise", "before", "after", "delta", "disclosures"} for one
    hypothetical trade. `after` is portfolio_state over `rows` plus the
    premise appended as one more row; `before` is portfolio_state over `rows`
    alone.

    `disclosures` is a list of machine-readable keys (DISCLOSURES), never
    prose: locale wording belongs in the copy catalog, not here
    (docs/maintainer-guide.md, "Honesty decisions belong in code"). A key is
    emitted when: the state is cost-basis rather than priced (`cost_basis`);
    cash is not reliable
    (`cash_unreliable`); the premise's ticker has no driver mapping, so
    sector/AI exposure cannot account for it (`unmapped_driver`); the book
    itself carries positions the concentration figures could not read — an
    unclassified single name (`unclassified_book`) or a fund nothing
    decomposes (`etf_not_decomposed`), both #598/#599 and both named in the
    fields below; a position was left out of the usable book these numbers
    are measured against, because it could not be valued (#515) or because the
    integrity record names it (#673) — `partial_book` either way, with
    `excluded_holdings[].reason` saying which; or a supplied cash anchor's
    currency matched no cash-flow bucket and no fx rate could convert it
    either (`cash_anchor_unmatched`, #688), with `after["cash"]["unmatched_anchors"]`
    naming which currency, amount and as_of — the opposite condition from
    `cash_unreliable`, and the two may fire independently of each other.

    `unclassified_holdings` and `undecomposed_etfs` are to their keys what
    `excluded_holdings` is to `partial_book`: the key says THAT the book was
    partially legible, these say WHICH positions and at what weight, so an
    answer can state the size of what it could not see rather than only that
    something was missed. Both are `after`'s own, forwarded rather than
    recomputed — `portfolio_state` stamps them onto every state it builds
    (see `book_legibility`), so `before` carries its own pair for its own book
    and a caller who never reaches this function still gets them. There is no
    key for a currency this book could not convert — `portfolio_state` refuses
    that book outright (#600).

    `excluded_holdings` is what `rows_from_portfolio_basis` left out, passed
    through by the caller rather than re-derived here — `rows` alone cannot
    tell "this book has five positions" from "this book has six and one could
    not be valued", and that difference is the whole point. It is returned on
    the result so the ticker identities travel with every number computed from
    the partial denominator; the disclosure key says *that* something was
    excluded, this says *what* (#515's first invariant: the excluded holding is
    named wherever the derived number appears).

    A premise that is *about* an integrity-excluded holding is refused by name
    rather than answered over the bounded book (#673) — see
    `_refuse_premise_on_integrity` for why that one case is not a bounded
    answer at all.

    `last_px` is forwarded into `validate_premise` (#777) so an unstated
    `premise.price` can default to this same map's own observed close for
    the ticker — the identical price every other number below prices that
    instrument at, never a second lookup. `rule_collision` never calls
    `validate_premise` a second time; it reads this function's own returned
    `premise` back (see that function), so the default is resolved exactly
    once per `consider` call.
    """
    # #803. Once, here, before either state is built: `before` and `after` must
    # be the same book plus one row, and canonicalizing them independently would
    # be two chances to disagree. Everything below — including `portfolio_state`'s
    # own pass — then sees a list that is already canonical and is handed back
    # unchanged, which is what preserves `premise_row`'s identity in `after`.
    rows = _canonical_rows(rows)
    _refuse_premise_on_integrity(premise, excluded_holdings)
    normalized = validate_premise(premise, rows, last_px=last_px)
    premise_row = _premise_row(normalized)

    before = before_override or portfolio_state(rows, last_px=last_px,
                                                max_pos_override=max_pos_override,
                                                cash_anchor=cash_anchor, fx=fx)
    after = portfolio_state(rows + [premise_row], last_px=last_px,
                            max_pos_override=max_pos_override, cash_anchor=cash_anchor, fx=fx,
                            premise_row=premise_row)

    disclosures = []
    if after["basis"] == "cost":
        disclosures.append("cost_basis")
    if not after["cash"]["reliable"]:
        disclosures.append("cash_unreliable")
    # #688: independent of cash_unreliable above -- a book can be fully
    # anchored (reliable: true) in every currency its cash flows touch and
    # still have a separate, named anchor sitting outside all of them. Read
    # off `after` for the same reason unclassified_holdings/undecomposed_etfs
    # below are: cash_anchor is identical input to both portfolio_state calls,
    # so before/after always agree, and `after` is the book every other number
    # in this result already describes.
    if after["cash"]["unmatched_anchors"]:
        disclosures.append("cash_anchor_unmatched")
    sector, _is_ai = trade_recap.driver(normalized["ticker"])
    if sector == _UNCLASSIFIED_DRIVER:
        disclosures.append("unmapped_driver")
    # Read off `after`, the book every number in this result describes — and
    # the one the premise's own ticker has already joined, so an unclassified
    # or undecomposed name the user is asking to *buy* is disclosed as part of
    # the book it is about to become part of, not only through `unmapped_driver`
    # above. Read, not recomputed: `portfolio_state` already answered this for
    # the state it built, and asking a second time here is how the state and the
    # disclosure would come to disagree about the same book.
    unclassified_holdings = after["unclassified_holdings"]
    undecomposed_etfs = after["undecomposed_etfs"]
    if unclassified_holdings:
        disclosures.append("unclassified_book")
    if undecomposed_etfs:
        disclosures.append("etf_not_decomposed")
    excluded = [dict(row) for row in excluded_holdings or ()]
    if excluded:
        disclosures.append("partial_book")

    return {"premise": normalized, "before": before, "after": after,
            "delta": _delta(before, after, normalized["ticker"]),
            "disclosures": disclosures, "excluded_holdings": excluded,
            "unclassified_holdings": unclassified_holdings,
            "undecomposed_etfs": undecomposed_etfs}


# ─────────────────────────────── rule collision ───────────────────────────────

def _worsened(before_value, after_value):
    """True when a reading moved in the bad (increasing) direction beyond
    float noise. Only meaningful for the already_over state — see the module
    docstring."""
    return (after_value - before_value) > _EPSILON


def classify_rule_effect(before_reading, after_reading, limit):
    """The single deterministic derivation of what this trade does to one
    upper-bound rule (#579), from three facts and nothing else: the reading
    before, the reading after, and the line.

    One function, so every decision-facing consumer reads one verdict instead
    of composing `state` and `worsens` for itself. Composing them at prose
    time is what failed owner-live acceptance on 2026-08-02, and two of the
    six transitions below cannot be composed from those two fields at all —
    see RULE_EFFECTS.

    ``limit`` is an upper bound: over means strictly greater, the same
    comparison ``dim_size``/``dim_diversify`` already make against their own
    thresholds, so this function and the state it accompanies cannot disagree
    about which side of the line a reading is on. Movement uses the same
    ``_EPSILON`` noise floor ``_worsened`` uses, so "reduced" and "did not
    move" are told apart at exactly one tolerance rather than two.

    Returns one of the six real transitions in RULE_EFFECTS; never
    ``unjudged``/``unmapped``, which describe a rule this module could not
    evaluate at all and therefore have no readings to classify.
    """
    before_over = before_reading > limit
    after_over = after_reading > limit
    if not after_over:
        return "resolved_existing_breach" if before_over else "compliant"
    if not before_over:
        return "new_breach"
    if _worsened(before_reading, after_reading):
        return "worsened_existing_breach"
    if _worsened(after_reading, before_reading):
        return "improved_but_still_over"
    return "unchanged_existing_breach"


def effect_disagrees_with_state(state, effect):
    """The message naming why this pair cannot both be true, or None.

    #579's compatibility clause: a stored or hand-built row may carry an
    effect that contradicts its own state, and a validator has to reject that
    rather than let one of the two reach an answer. Stated here, beside the
    table it reads, so the challenge surface and the provenance gate ask one
    question rather than two.
    """
    if effect is None:
        return None  # a legacy row recorded before rule_effect existed
    if effect not in _EFFECT_STATES:
        return f"rule_effect {effect!r} is not one of {list(RULE_EFFECTS)}"
    allowed = _EFFECT_STATES[effect]
    if state not in allowed:
        return (f"rule_effect {effect!r} cannot accompany state {state!r} "
                f"(expected one of {list(allowed)})")
    return None


def _max_pos_pct_collision(ticker, before, after, max_pos_override):
    """(state, worsens, effect, limit) for a max_pos_pct rule.

    Causality is judged on the *premise ticker's own* weight against
    effective_oversize_trigger — not the book's max_ticker/max_pct, which can
    be a different position entirely (a tiny buy of a small holding must not
    read as breaching a cap some other, larger position already exceeds).
    A fresh cross of the ticker's own weight wins the classification even
    when the book was already over the line for an unrelated reason: that is
    still the most actionable "would_breach" answer for this trade. Only when
    the premise ticker itself did not freshly cross does the book's own
    before/after oversize_triggered decide already_over vs clear — the
    residual case where this trade's effect on its own subject is not what
    is wrong with the book.

    `worsens` (already_over only) reads the *book's* max_pct, per the module
    docstring: a user already over the cap is asking whether this trade digs
    deeper or climbs out, a book-level question even when the premise ticker
    is not the one currently driving max_pct.

    `effect` (#579) is classified from *the readings this branch judged*, not
    from a third, independently chosen pair: the premise ticker's own weight
    where causality was decided on it, the book's max_pct where it was not.
    Classifying both branches off one reading would produce the contradiction
    this pairing exists to avoid — a fresh cross of the ticker's own line
    reading as an improvement because the book's largest position happened to
    shrink on the same trade.
    """
    line = trade_recap.effective_oversize_trigger(max_pos_override)
    ticker_before = before["weights"].get(ticker, 0.0)
    ticker_after = after["weights"].get(ticker, 0.0)
    if (not ticker_before > line) and ticker_after > line:
        return "would_breach", None, classify_rule_effect(ticker_before, ticker_after, line), line
    if after["oversize_triggered"]:
        return ("already_over", _worsened(before["max_pct"], after["max_pct"]),
                classify_rule_effect(before["max_pct"], after["max_pct"], line), line)
    return "clear", None, classify_rule_effect(before["max_pct"], after["max_pct"], line), line


def _concentration_line(metric_key):
    """The exact threshold dim_diversify() itself compares this metric_key's
    own reading against — read live from trade_recap's named constants
    (never a copied literal), so this module and dim_diversify cannot
    silently drift apart on what the line is.

    max_sector_pct deliberately does NOT carry forward dim_diversify's own
    ``len(risk_w) >= 8`` holdings guard on that clause. That guard exists so
    a small book is not flagged as concentrated by the *engine's own
    unprompted diagnosis* — three stocks in three sectors are always >=33%
    in their biggest sector, and that is not a problem worth surfacing on
    its own. A rule collision answers a different question: the user
    committed to a specific number for this specific metric_key, and a
    six-holding book crossing that number is still crossing it. The guard is
    about whether the engine should raise this unprompted; it says nothing
    about whether a promise was kept, so it does not apply here.
    """
    if metric_key == "ai_pct":
        return trade_recap.AI_MAX_TH
    if metric_key == "max_sector_pct":
        return trade_recap.SECTOR_MAX_TH
    return trade_recap.TOP3_MAX_TH  # top3_pct


def _concentration_collision(metric_key, before, after):
    """(state, worsens, effect, limit) for ai_pct / max_sector_pct / top3_pct.

    Judged against this metric_key's OWN reading and OWN line
    (_concentration_line) — never dim_diversify's shared `triggered` flag.
    The three metric_keys reconcile to the same problem_key
    ("concentration") for realized-history reconciliation, where
    build_problem_events legitimately does not care which of the three
    tripped it (docs/development-guide.md section 7's shared-signal
    reasoning, correctly applied there). But a rule names exactly one
    metric_key, and the user committed to that one line, not to "some
    concentration reading or other". Reading the shared flag as causality
    for a single-metric rule shipped two real bugs (external review,
    counterexamples reproduced in tests/test_consequence.py): a fresh cross
    of this rule's own metric read as already_over because a DIFFERENT
    reading (top3) happened to already be over its own, unrelated line; and
    a fresh cross of max_sector_pct's own 40% line read as clear outright,
    because the shared flag's max_sector arm is additionally gated on
    dim_diversify's `>= 8` holdings guard (see _concentration_line) — a
    false negative, the worst shape this vocabulary can produce.

    `worsens` (already_over only) reads the same metric_key's own field —
    already guaranteed consistent with `state`, since both now come from the
    same before/after/line comparison rather than two different signals. So
    does `effect` (#579): one reading pair, one line, three consumers.
    """
    field = _CONCENTRATION_READING_FIELD[metric_key]
    line = _concentration_line(metric_key)
    effect = classify_rule_effect(before[field], after[field], line)
    before_over = before[field] > line
    after_over = after[field] > line
    if (not before_over) and after_over:
        return "would_breach", None, effect, line
    if after_over:
        return "already_over", _worsened(before[field], after[field]), effect, line
    return "clear", None, effect, line


def _avgdown_collision(ticker, before_events, after_events):
    """(state, effect) for the avgdown_count metric, mirroring
    build_problem_events' own avgdown_breach condition
    (`weight_then > AVGDOWN_BREACH_W`) exactly — same constant, same event
    shape positions() already produces, not a second threshold chosen
    independently.

    The reading pair here is a *count of breaching average-down events
    attributable to this trade*, against a limit of zero — which is why the
    only two effects this branch can produce are `new_breach` and
    `compliant`. There is no already-over axis to separate out (below), so
    there is no improving or worsening transition either, and the row carries
    no `limit`: the engine's weight threshold is not a line the answer can
    quote a before and an after against.

    `before_events`/`after_events` are positions()'s own avg_down list for
    `rows` and `rows` plus the premise. positions() is a single causal left-
    to-right pass, so appending one row at the end can only ever add at most
    one trailing event — it cannot alter or remove any earlier one — and the
    two lists share every element up to that point."""
    if instruments.is_diversified_allocation(ticker):
        # Allocation ETFs are exempt from avgdown_breach — the same exemption
        # build_problem_events applies (a low-price add to a diversified
        # allocation instrument is rebalancing, not concentration risk).
        return "clear", "compliant"
    if len(after_events) <= len(before_events):
        return "clear", "compliant"  # the premise did not qualify as an average-down at all
    new_event = after_events[-1]
    breached = new_event.get("weight_then", 0) > trade_recap.AVGDOWN_BREACH_W
    return ("would_breach", "new_breach") if breached else ("clear", "compliant")


def rule_collision(rows, premise, rules_report, last_px=None, max_pos_override=None,
                   cash_anchor=None, fx=None, before_override=None, excluded_holdings=()):
    """For each currently-tracked rule, whether this hypothetical trade would
    collide with it right now. See the module docstring for the full
    unmapped/unjudged/real-verdict discipline.

    `rules_report` is exactly what `problems.load_rules_report(path,
    muted_ids)` returns — `(tracking, muted, skipped)`. Only `tracking` (the
    first element) is evaluated: a muted rule opted out of the rotation, and
    "currently-tracked" excludes it the same way problems.check_rules'
    callers already do.

    Read-only. Never writes rules.jsonl and never calls problems.check_rules
    — a hypothetical's collision state must not enter held_streak or the
    graduation statistics a real reconciled breach earns (the firewall
    conditions.py states at lines 66-72 for an analogous case).

    Returns a list of `{rule_id, text, metric_key, problem_key, state,
    worsens, rule_effect, limit, limit_source}`, one per tracked rule, in
    `tracking`'s own order. `worsens` is `True`/`False` only when `state` is
    `already_over` and `None` otherwise — see the module docstring for why
    the book's pre-existing condition and this trade's own effect are carried
    as two fields rather than folded into one.

    `rule_effect` (#579) is the third and the one every decision-facing
    consumer reads: what this *transaction* does to the rule, classified by
    `classify_rule_effect` from the same readings and the same line the state
    beside it was judged on. `state` remains the absolute position of the
    book and is kept for compatibility; it is never the transaction's
    verdict. `limit` is the line that classification used, and `limit_source`
    says whether the user set it — both `None` for a rule with no threshold
    reading (avgdown_count) or none at all (unjudged/unmapped).

    When `excluded_holdings` is non-empty the book these states were judged
    against is a bounded subset, and every row additionally carries
    `partial_book: True` (stamped only when true, so an ordinary collision
    keeps the exact shape every existing reader already handles). This is
    #515's second invariant, and it is not decoration: the user wrote their
    rule against their whole book, and an excluded position reads as weight
    zero here — a cap that a hidden position is breaching comes back `clear`.
    A verdict computed on a different denominator than the promise was made
    against must say so rather than pass as the same claim.
    """
    tracking = rules_report[0]
    # #803. `consequence` canonicalizes its own copy, but the two
    # `trade_recap.positions` calls below read `rows` directly — the avgdown
    # rule would otherwise be judged against a differently-spelled book than the
    # concentration rules beside it.
    rows = _canonical_rows(rows)
    result = consequence(rows, premise, last_px=last_px, max_pos_override=max_pos_override,
                         cash_anchor=cash_anchor, fx=fx, before_override=before_override,
                         excluded_holdings=excluded_holdings)
    normalized = result["premise"]
    before = result["before"]
    after = result["after"]
    ticker = normalized["ticker"]
    premise_row = _premise_row(normalized)
    _, avg_down_before = trade_recap.positions(rows)
    _, avg_down_after = trade_recap.positions(rows + [premise_row])

    # Only max_pos_pct's line can be the user's own; every other threshold
    # this module compares against is an engine constant. Read once, from the
    # same helper the line itself comes from, so the line and its attribution
    # cannot disagree about whose number it is.
    position_limit_source = ("user_cap" if trade_recap.valid_position_cap(max_pos_override)
                             else "engine_default")

    out = []
    for rule in tracking:
        metric_key = rule.get("metric_key")
        problem_key = rule.get("problem_key")
        worsens = None
        limit = None
        limit_source = None
        if problem_key is None:
            state = effect = "unmapped"
        elif metric_key not in EVALUABLE_METRIC_KEYS:
            # exit_severity / hold_severity: realized selling/holding
            # behaviour across history, which one hypothetical trade cannot
            # settle. Any future metric_key this module has not been taught
            # to evaluate falls here too, never silently as "clear".
            state = effect = "unjudged"
        elif metric_key == "max_pos_pct":
            state, worsens, effect, limit = _max_pos_pct_collision(
                ticker, before, after, max_pos_override)
            limit_source = position_limit_source
        elif metric_key == "avgdown_count":
            # Already event-based and already attributed to the premise —
            # positions() only adds an event for this specific trade, so
            # there is no book-wide "already over" state to separate out.
            state, effect = _avgdown_collision(ticker, avg_down_before, avg_down_after)
        else:
            state, worsens, effect, limit = _concentration_collision(metric_key, before, after)
            limit_source = "engine_default"
        row = {"rule_id": rule.get("rule_id"), "text": rule.get("text"),
               "metric_key": metric_key, "problem_key": problem_key,
               "state": state, "worsens": worsens, "rule_effect": effect,
               "limit": limit, "limit_source": limit_source}
        if result["excluded_holdings"]:
            row["partial_book"] = True
        out.append(row)
    return out
