#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared position-rationale fixture (#450, #403 Front B).

One seeded stream every suite that reads rationales can build against, so the
integration slice, #450's later multi-source reader and this stream's own tests
assert over the *same* history instead of each inventing one. A fixture that
each suite rolls itself is how two readers end up disagreeing about what the
record says — the failure this stream exists to prevent, one layer up.

Deliberately not a golden file: the stream is content-addressed, so a checked-in
`.jsonl` would pin event ids and break on any legitimate contract change, while
seeding through the real writer proves the ids are reachable. Follows
`tests/offline_posture.py`'s precedent — a plain module under `tests/`, imported
with `sys.path.insert(0, os.path.dirname(...))`.

Usage:

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import rationale_fixture
    seeded = rationale_fixture.seed(root)
    seeded["acme"]["cycle_id"]          # the subject with a three-event history
    seeded["expect"]["acme_effective"]  # what the reader must call in force

What the history contains, and why each piece is here:

  ACME     three events -- a statement, a changed reason, then a confirmation.
           Exercises the derived `changed` classification and the walk back
           through a confirmation to the wording still in force.
  WIDGET   one statement. The shortest real history; a reader that only works
           past two events fails here.
  RELINKED statements written under a cycle id that #563's later-history upgrade
           has since moved. A reader that matches the current id alone reports
           nothing for it, which is exactly the "remembered, then silently gone"
           failure #403 names.
  ORPHAN   an active-looking subject with no rationale at all. A reader must
           return an empty history rather than raising or inventing one.
"""
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "skills", "fomo-kernel", "engine"))
import position_rationale as pr  # noqa: E402

ACME = {"cycle_id": "ACME#2026-06-30#1", "ticker": "ACME",
        "market": "US", "currency": "USD"}
WIDGET = {"cycle_id": "WIDGET#2026-05-04#1", "ticker": "WIDGET",
          "market": "US", "currency": "USD"}
# The id the statements were written under, before #563 moved the cycle start.
RELINKED_FORMER = "GLOBEX#2026-04-01#1"
RELINKED = {"cycle_id": "GLOBEX#2025-11-12#1", "ticker": "GLOBEX",
            "market": "US", "currency": "USD"}
ORPHAN = {"cycle_id": "INITECH#2026-07-02#1", "ticker": "INITECH",
          "market": "US", "currency": "USD"}

ACME_FIRST = "配息很穩，我當存股在放"
ACME_CURRENT = "改成看它買回庫藏股的力道"
WIDGET_ONLY = "entered on the new plant coming online"
RELINKED_FIRST = "held since before the history upgrade"
RELINKED_CURRENT = "and the reason has not really changed since"


def seed(root, *, state_version="sv-fixture-1"):
    """Write the shared history into ``root`` and return what it contains.

    Everything goes through the real writer, so ids, chain links and the
    `stated_at` ordering are the ones production would produce.
    """
    def say(subject, text, day, **kw):
        return pr.append(root, subject=subject, act="statement", user_statement=text,
                         stated_at=day, capture_source=kw.pop("source", "direct"),
                         state_version=state_version, **kw)

    acme_first = say(ACME, ACME_FIRST, "2026-07-01")
    acme_changed = say(ACME, ACME_CURRENT, "2026-07-14")
    acme_confirmed = pr.append(root, subject=ACME, act="confirmation",
                               stated_at="2026-07-28", capture_source="review",
                               origin_id="2026-07-28__fixture", state_version=state_version)
    widget = say(WIDGET, WIDGET_ONLY, "2026-06-02")

    former = {**RELINKED, "cycle_id": RELINKED_FORMER}
    relinked_first = say(former, RELINKED_FIRST, "2026-04-05")
    relinked_current = pr.append(
        root, subject=RELINKED, act="statement", user_statement=RELINKED_CURRENT,
        stated_at="2026-07-20", capture_source="direct", state_version=state_version,
        aliases=[RELINKED_FORMER])

    return {
        "root": root,
        "acme": {**ACME, "events": [acme_first, acme_changed, acme_confirmed]},
        "widget": {**WIDGET, "events": [widget]},
        "relinked": {**RELINKED, "aliases": [RELINKED_FORMER],
                     "events": [relinked_first, relinked_current]},
        "orphan": dict(ORPHAN),
        "state_version": state_version,
        # What any correct reader must report. A consumer asserting against these
        # rather than against its own copy is the point of the fixture.
        "expect": {
            "acme_effective": ACME_CURRENT,
            "acme_change": "no_change",      # latest act is the confirmation
            "acme_total": 3,
            "widget_effective": WIDGET_ONLY,
            "widget_change": "initial",
            "relinked_effective": RELINKED_CURRENT,
            "relinked_total": 2,             # only with the alias supplied
            "relinked_total_without_alias": 1,
            "orphan_total": 0,
        },
    }
