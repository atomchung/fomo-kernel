#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The durable identity a position cycle was already minted with (#807).

`symbols.canonical_ticker` decides which *instrument* two spellings name. This
module decides which *identifier* one of that instrument's position cycles has
already been filed under — a different question, and the half #805 answered only
for a book with one cycle.

Why the two cannot share an answer
----------------------------------

A `cycle_id` is `spelling#start#sequence`, and it is durable: `theses.jsonl`
holds foreign keys to that exact string (`thesis.stable_thesis_id` is its
digest), `decision_cursor` extends it, and `revisit._revisit_id` embeds it.
Reading a legacy book through one canonical identity is correct arithmetic; it
must not also re-mint the identifiers the book's own history is bound by.

#805 preserved the spelling by keeping the **first one seen across the ticker's
whole history**, while the sequence kept counting cycles on the **canonical**
ticker. Those two survive exactly one cycle together. After a full exit and a
re-entry spelled differently, they describe no cycle that ever existed:

.. code-block:: text

    buy aaa → sell aaa → buy AAA
    minted before #803:   AAA#2026-03-05#1
    #805 (main@d7dac38):  aaa#2026-03-05#2     ← both halves moved

The thesis written against the first string is not eligible for
`thesis.build_snapshot_cycle_relinks`' narrow snapshot-inference repair, so it
simply stops answering to the position it is about, and the user is asked to
restate a thesis they already recorded.

The rule
--------

**Bind at open.** A cycle's identity is decided by the fill that opened it and
never afterwards:

- the *spelling* is the one that opened this cycle, not the earliest spelling in
  the instrument's history;
- the *sequence* is counted per stored spelling, which is what the pre-#803
  producers counted — they keyed everything on the raw ticker, so `aaa` and
  `AAA` each ran their own sequence;
- an add or a sell spelled differently updates the canonical position and leaves
  the identity alone. A cycle has one opening.

Bind-at-open makes the identity unique by construction rather than by a
tie-break, which is the property #803 asked for: there is no insertion order to
resolve, because exactly one fill opens a cycle.

What it deliberately does not reconstruct
-----------------------------------------

Two spellings of one instrument inside a single cycle mean the pre-#803
producers were running **two positions** in parallel. While that cycle is open
there is still one right answer — the identity it opened with, which both
readings share. Once it closes, the second legacy cycle's fate is not derivable
from the rows: the next cycle to open has two prior identities with a claim on
it and no evidence to choose between them. That is reported through
:meth:`CycleIdentities.conflicts` and each caller fails closed or discloses
through the channel it already owns; no migration is authorized here, and
picking one silently is the failure mode #803 exists to prevent.

Like `symbols`, this module imports nothing, so the lowest-level readers
(`ledger`, `revisit`) and the CSV lane (`trade_recap`) can share the one rule
without a dependency cycle — three near-equal copies of it being precisely the
drift that produced the defect above.
"""
from __future__ import annotations


class CycleIdentities:
    """The stored spelling and sequence each open position cycle already had.

    One instance per replay. Callers drive it with the transitions they already
    compute — they keep owning what a position *is*; this owns only what the
    cycle is *called*.
    """

    def __init__(self):
        self._open = {}            # canonical → {"spelling", "seq", "spellings"}
        self._minted = {}          # stored spelling → highest sequence it has used
        self._unrecoverable = {}   # canonical → spellings of a closed mixed cycle
        self._conflicts = {}       # canonical → spellings with a claim on one cycle

    def opened(self, canonical, spelling, *, seq=None):
        """Bind the cycle this fill just opened.

        ``seq`` is the sequence a declaration carried (`ledger._anchored_cycle_seq`);
        omitted, the next one this spelling has not used yet — which reproduces
        the pre-#803 ``seq_base`` exactly, because that counter was keyed on the
        raw ticker and so ran independently per spelling.
        """
        spelling = spelling or canonical
        if canonical in self._unrecoverable:
            # A mixed cycle closed earlier, so more than one legacy identity has
            # a claim on the cycle opening now and the rows cannot say which.
            self._conflicts[canonical] = sorted(self._unrecoverable[canonical] | {spelling})
        if seq is None:
            seq = self._minted.get(spelling, 0) + 1
        self._minted[spelling] = max(self._minted.get(spelling, 0), seq)
        self._open[canonical] = {"spelling": spelling, "seq": seq, "spellings": {spelling}}

    def filled(self, canonical, spelling):
        """Record an add or a sell inside the open cycle.

        The identity does not move — that is the whole point — but the spelling
        is remembered, because a cycle that carried two of them is the one shape
        whose successor cannot be identified (see the module docstring).
        """
        cycle = self._open.get(canonical)
        if cycle is not None and spelling:
            cycle["spellings"].add(spelling)

    def closed(self, canonical):
        """Close the open cycle. Its sequence stays spent, so a rebuy gets the next."""
        cycle = self._open.pop(canonical, None)
        if cycle is not None and len(cycle["spellings"]) > 1:
            self._unrecoverable.setdefault(canonical, set()).update(cycle["spellings"])

    def spelling(self, canonical):
        """The spelling this instrument's open cycle was minted with, or ``None``."""
        cycle = self._open.get(canonical)
        return cycle["spelling"] if cycle is not None else None

    def sequence(self, canonical):
        """The sequence this instrument's open cycle was minted with, or ``None``."""
        cycle = self._open.get(canonical)
        return cycle["seq"] if cycle is not None else None

    def conflicts(self):
        """``{canonical: [spellings]}`` for every cycle with no single prior identity.

        Empty for every book whose cycles are each spelled one way — which is
        every book a broker export or the post-#803 engine wrote.
        """
        return {canonical: list(spellings) for canonical, spellings in sorted(self._conflicts.items())}
