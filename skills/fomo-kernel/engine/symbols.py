#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The one canonical ticker identity rule (#803).

Two spellings of one symbol are one instrument. Before this module the engine
had no shared answer to that, and the consequence was arithmetic rather than
cosmetics: a premise written ``nvda`` against a held ``NVDA`` built a *second*
position beside the first, so an add reduced the displayed weight of the very
position being added to, a sell was refused as "not currently held", and the
prior-decision reader (#609) — which compares canonical stored tickers exactly,
correctly — lost the user's own earlier consultation of the same name.

What this rule is
-----------------

Surrounding whitespace and letter case, and nothing else:

- ``" nvda "`` and ``"NVDA"`` are the same instrument;
- ``"2330.tw"`` canonicalizes to ``"2330.TW"`` — the exchange suffix is part of
  the identity and is case-folded with it, while the numeric stem is untouched;
- a purely numeric symbol (``"2330"``) survives unchanged, because upper-casing
  digits is the identity function.

What this rule is *not*
-----------------------

- **Not validation.** Each envelope keeps its own accepted-symbol rule and its
  own error type — ``consequence._ticker``, ``price_feed._ticker``,
  ``snapshot_adapter._ticker`` reject exactly what they rejected before. Case
  is identity; shape is admission, and merging the two here would silently
  widen or narrow what the product accepts.
- **Not an alias, ADR or dual-listing map.** ``TSM`` and ``2330.TW`` are
  different instruments and stay that way (#803 non-goals). No fuzzy matching,
  no company-name resolution, no security master.

Why it lives in its own module
------------------------------

Every caller that authors or compares a ticker has to reach the *same* answer;
a second local rule — even one that agrees today — is the defect this file
exists to prevent, because arithmetic and recall would then be free to drift
apart one edit at a time. This module therefore imports nothing and depends on
nothing, so the lowest-level readers (``ledger``, ``splits``, ``instruments``)
can share it with the highest (``review``) without a cycle.
"""
from __future__ import annotations


def canonical_ticker(value):
    """This value's canonical instrument identity, or ``None``.

    ``None`` for anything that is not a non-empty string, so a caller keeps its
    own "missing ticker" error rather than inheriting one from here. Never
    raises: a validator that wants to reject a shape does so on the returned
    value, against its own rule.
    """
    if not isinstance(value, str):
        return None
    symbol = value.strip().upper()
    return symbol or None


def is_canonical(value):
    """Whether ``value`` is already exactly its own canonical identity.

    The predicate legacy readers use to tell "stored canonically" from "needs
    projecting", without a second copy of the rule at each call site.
    """
    return isinstance(value, str) and value == canonical_ticker(value)
