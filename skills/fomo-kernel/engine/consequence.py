#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""consequence.py — Layer 2: deterministic arithmetic over a hypothetical trade
(docs/decision-fomo-kernel-shape.md §3).

The product scenario: a user mid-decision asks "I'm thinking of buying NVDA —
what does that do to my book?" `prepare` today accepts only history, no
hypothesis input, so Layer 2 "barely exists" until this module supplies the
missing primitive. The engine answers with computed consequences, never
prose — locale wording for anything disclosed here belongs in renderer copy,
never in this file (CLAUDE.md, "Honesty decisions belong in code").

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
import re
from collections.abc import Mapping

import instruments
import trade_recap


class ConsequenceError(Exception):
    """Raised when a supplied trade premise cannot be evaluated as-is."""


SIDES = ("buy", "sell")

# Machine-readable, never prose (see module docstring). A caller renders
# wording from these keys; this module never constructs a sentence.
DISCLOSURES = ("cost_basis", "cash_unreliable", "unmapped_driver", "mixed_currency_no_fx")

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
    if not isinstance(value, str) or not value.strip():
        raise ConsequenceError("premise.ticker must be a non-empty string")
    symbol = value.strip()
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


def rows_from_portfolio_basis(basis):
    """Adapt one complete canonical current book to consequence input rows.

    ``PortfolioBasis`` is the owner of the held shares and average cost for a
    ledger-backed current-book question.  Replaying its source events here
    would create a second reader with a different lot convention (FIFO versus
    the ledger's average-cost book).  Instead, make one synthetic opening row
    per already-held position.  These rows are merely the input shape required
    by the established consequence arithmetic; their quantities and costs are
    copied from the frozen basis, never re-derived.

    A partial, unverified, damaged, empty, or cost-incomplete basis cannot
    safely make a current-book verdict.  The caller must keep historical CSV
    mode separate rather than quietly treating it as this adapter.
    """
    if not isinstance(basis, Mapping):
        raise ConsequenceError("canonical PortfolioBasis is not an object")
    if basis.get("completeness") != "declared_complete":
        raise ConsequenceError("canonical PortfolioBasis is not a complete declared current book")
    if basis.get("cost_basis") != "average_cost":
        raise ConsequenceError("canonical PortfolioBasis has incomplete cost basis")
    if basis.get("integrity"):
        raise ConsequenceError("canonical PortfolioBasis has claim-affecting integrity warnings")
    current_book = basis.get("current_book")
    holdings = current_book.get("holdings") if isinstance(current_book, Mapping) else None
    if not isinstance(holdings, Mapping) or not holdings:
        raise ConsequenceError("canonical PortfolioBasis has no held positions")
    try:
        as_of = dt.date.fromisoformat(str(basis["as_of"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ConsequenceError("canonical PortfolioBasis has invalid as_of") from exc

    rows = []
    for ticker in sorted(holdings):
        holding = holdings[ticker]
        if not isinstance(ticker, str) or not isinstance(holding, Mapping):
            raise ConsequenceError("canonical PortfolioBasis has invalid holding")
        try:
            qty = float(holding["shares"])
            # PortfolioBasis rounds avg_cost for display but preserves the
            # canonical cost_total.  Derive the synthetic row price from the
            # latter so a rounded display average cannot alter the held cost.
            price = float(holding["cost_total"]) / qty
        except (KeyError, TypeError, ValueError) as exc:
            raise ConsequenceError("canonical PortfolioBasis has unavailable holding cost") from exc
        if qty <= 0 or price <= 0:
            raise ConsequenceError("canonical PortfolioBasis has non-positive holding facts")
        rows.append({"ticker": ticker, "side": "buy", "qty": qty, "price": price,
                     "date": as_of, "market": holding.get("market", "US"),
                     "currency": holding.get("currency", "USD")})
    return rows


# ─────────────────────────────── validation ───────────────────────────────

def validate_premise(premise, rows):
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

    Returns a normalized dict with exactly {ticker, side, qty, price, date,
    currency}. `notional`, when supplied, is consumed here and converted to
    qty — it never appears on the normalized form, so nothing downstream of
    validation has to handle both shapes of the same fact.
    """
    if not isinstance(premise, dict):
        raise ConsequenceError("premise must be an object")
    unknown = set(premise) - _FIELDS
    if unknown:
        raise ConsequenceError("premise has unknown fields: " + ", ".join(sorted(unknown)))

    ticker = _ticker(premise.get("ticker"))
    side = premise.get("side")
    if side not in SIDES:
        raise ConsequenceError("premise.side must be one of " + ", ".join(SIDES))
    price = _positive_number(premise.get("price"), "price")

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
            "date": date, "currency": currency}


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

def portfolio_state(rows, last_px=None, max_pos_override=None, cash_anchor=None, fx=None):
    """The book as of the end of `rows`. Every number here is trade_recap's own
    function output, unmodified — this assembles a snapshot, it does not
    compute anything new. Calling it once on `rows` and once on `rows` plus
    one appended hypothetical row is how `consequence` derives before/after.

    `basis` records whether weights/pct fields came from market prices or
    cost: "priced" when `last_px` carries at least one close, "cost" when it
    is absent. A consequence computed on cost basis is a different claim from
    one computed on live prices, and a caller must be able to tell which one
    it is holding.
    """
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
    fx_gaps = sorted(c for c in currencies if c not in fx) if mixed_currency else []

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
    cash_flows = trade_recap.load_cash_flows([], trade_rows=rows)
    held_mv = sum((sh * lastpx_v[t]) if lastpx_v.get(t) else c for t, (sh, c) in held_v.items())
    cash = trade_recap.cash_position(cash_flows, held_mv, anchor=cash_anchor, fx=fx)

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
                 "source": cash["source"], "reliable": cash["reliable"]},
        "basis": "priced" if last_px else "cost",
        # Consumed by consequence()'s mixed_currency_no_fx disclosure.
        "mixed_currency": mixed_currency,
        "fx_gaps": fx_gaps,
    }


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
                before_override=None):
    """{"premise", "before", "after", "delta", "disclosures"} for one
    hypothetical trade. `after` is portfolio_state over `rows` plus the
    premise appended as one more row; `before` is portfolio_state over `rows`
    alone.

    `disclosures` is a list of machine-readable keys (DISCLOSURES), never
    prose: locale wording belongs in the copy catalog, not here (CLAUDE.md,
    "Honesty decisions belong in code"). A key is emitted when: the state is
    cost-basis rather than priced (`cost_basis`); cash is not reliable
    (`cash_unreliable`); the premise's ticker has no driver mapping, so
    sector/AI exposure cannot account for it (`unmapped_driver`); or the
    ledger mixes currencies without full fx coverage (`mixed_currency_no_fx`).
    """
    normalized = validate_premise(premise, rows)
    premise_row = _premise_row(normalized)

    before = before_override or portfolio_state(rows, last_px=last_px,
                                                max_pos_override=max_pos_override,
                                                cash_anchor=cash_anchor, fx=fx)
    after = portfolio_state(rows + [premise_row], last_px=last_px,
                            max_pos_override=max_pos_override, cash_anchor=cash_anchor, fx=fx)

    disclosures = []
    if after["basis"] == "cost":
        disclosures.append("cost_basis")
    if not after["cash"]["reliable"]:
        disclosures.append("cash_unreliable")
    sector, _is_ai = trade_recap.driver(normalized["ticker"])
    if sector == _UNCLASSIFIED_DRIVER:
        disclosures.append("unmapped_driver")
    if after["fx_gaps"]:
        disclosures.append("mixed_currency_no_fx")

    return {"premise": normalized, "before": before, "after": after,
            "delta": _delta(before, after, normalized["ticker"]), "disclosures": disclosures}


# ─────────────────────────────── rule collision ───────────────────────────────

def _worsened(before_value, after_value):
    """True when a reading moved in the bad (increasing) direction beyond
    float noise. Only meaningful for the already_over state — see the module
    docstring."""
    return (after_value - before_value) > _EPSILON


def _max_pos_pct_collision(ticker, before, after, max_pos_override):
    """(state, worsens) for a max_pos_pct rule.

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
    """
    line = trade_recap.effective_oversize_trigger(max_pos_override)
    ticker_before_over = before["weights"].get(ticker, 0.0) > line
    ticker_after_over = after["weights"].get(ticker, 0.0) > line
    if (not ticker_before_over) and ticker_after_over:
        return "would_breach", None
    if after["oversize_triggered"]:
        return "already_over", _worsened(before["max_pct"], after["max_pct"])
    return "clear", None


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
    """(state, worsens) for ai_pct / max_sector_pct / top3_pct.

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
    same before/after/line comparison rather than two different signals.
    """
    field = _CONCENTRATION_READING_FIELD[metric_key]
    line = _concentration_line(metric_key)
    before_over = before[field] > line
    after_over = after[field] > line
    if (not before_over) and after_over:
        return "would_breach", None
    if after_over:
        return "already_over", _worsened(before[field], after[field])
    return "clear", None


def _avgdown_collision(ticker, before_events, after_events):
    """would_breach / clear for the avgdown_count metric, mirroring
    build_problem_events' own avgdown_breach condition
    (`weight_then > AVGDOWN_BREACH_W`) exactly — same constant, same event
    shape positions() already produces, not a second threshold chosen
    independently.

    `before_events`/`after_events` are positions()'s own avg_down list for
    `rows` and `rows` plus the premise. positions() is a single causal left-
    to-right pass, so appending one row at the end can only ever add at most
    one trailing event — it cannot alter or remove any earlier one — and the
    two lists share every element up to that point."""
    if instruments.is_diversified_allocation(ticker):
        # Allocation ETFs are exempt from avgdown_breach — the same exemption
        # build_problem_events applies (a low-price add to a diversified
        # allocation instrument is rebalancing, not concentration risk).
        return "clear"
    if len(after_events) <= len(before_events):
        return "clear"  # the premise did not qualify as an average-down at all
    new_event = after_events[-1]
    return "would_breach" if new_event.get("weight_then", 0) > trade_recap.AVGDOWN_BREACH_W else "clear"


def rule_collision(rows, premise, rules_report, last_px=None, max_pos_override=None,
                   cash_anchor=None, fx=None, before_override=None):
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
    worsens}`, one per tracked rule, in `tracking`'s own order. `worsens` is
    `True`/`False` only when `state` is `already_over` and `None` otherwise
    — see the module docstring for why the book's pre-existing condition and
    this trade's own effect are carried as two fields rather than folded into
    one.
    """
    tracking = rules_report[0]
    result = consequence(rows, premise, last_px=last_px, max_pos_override=max_pos_override,
                         cash_anchor=cash_anchor, fx=fx, before_override=before_override)
    normalized = result["premise"]
    before = result["before"]
    after = result["after"]
    ticker = normalized["ticker"]
    premise_row = _premise_row(normalized)
    _, avg_down_before = trade_recap.positions(rows)
    _, avg_down_after = trade_recap.positions(rows + [premise_row])

    out = []
    for rule in tracking:
        metric_key = rule.get("metric_key")
        problem_key = rule.get("problem_key")
        worsens = None
        if problem_key is None:
            state = "unmapped"
        elif metric_key not in EVALUABLE_METRIC_KEYS:
            # exit_severity / hold_severity: realized selling/holding
            # behaviour across history, which one hypothetical trade cannot
            # settle. Any future metric_key this module has not been taught
            # to evaluate falls here too, never silently as "clear".
            state = "unjudged"
        elif metric_key == "max_pos_pct":
            state, worsens = _max_pos_pct_collision(ticker, before, after, max_pos_override)
        elif metric_key == "avgdown_count":
            # Already event-based and already attributed to the premise —
            # positions() only adds an event for this specific trade, so
            # there is no book-wide "already over" state to separate out.
            state = _avgdown_collision(ticker, avg_down_before, avg_down_after)
        else:
            state, worsens = _concentration_collision(metric_key, before, after)
        out.append({"rule_id": rule.get("rule_id"), "text": rule.get("text"),
                    "metric_key": metric_key, "problem_key": problem_key,
                    "state": state, "worsens": worsens})
    return out
