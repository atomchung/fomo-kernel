#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Whether a test run may reach a market-data provider — declared once (#620).

This is not about testing an offline product. Nobody reviews trades without a
network: the product is a skill run by an agent that needs one to hold the
conversation at all. What this posture prevents is **today's market becoming a
hidden input to a test about arithmetic**.

A suite that asserts a position is 51% of a book, and that lets the engine
resolve live closes, fails tomorrow because the market moved and passes the day
after for the same reason. Worse than the flakiness: a red run stops telling you
whether *your change* broke something, because two things changed. The same
suites also pin a literal ``evaluation_id`` digest, which is a hash over the
frozen consequence and therefore over the prices.

Separately — and this is the part the naming obscures — "the provider did not
answer" is an ordinary production event *with* a working network: Yahoo rate
limits constantly, a sandbox may allow HTTP but not that host, and the runner
installs no yfinance at all. The engine's degradation path is for that, not for
a user with no internet.

``run_all.py`` used to be the only place this was set, deliberately, so a suite
added to ``SUITES`` inherited it without opting in. The gap that left: a suite
run **directly** — the normal inner loop when iterating on one file — never went
through that primitive, so ``python3 tests/test_consider.py`` resolved live
prices and reported nine failures that ``python3 tests/run_all.py`` did not
(#620). The declaration therefore lives here, where both entry points can reach
it, rather than in one of them.

``TR_TEST_NETWORK=1`` remains the single documented opt-in, and it clears the
posture rather than competing with it.
"""
import os

NETWORK_OPT_IN = "TR_TEST_NETWORK"
OFFLINE_ENV = "TR_OFFLINE"


def apply():
    """Set the posture for this process and everything it spawns.

    Assigned, not ``setdefault``: an inherited ``TR_OFFLINE=0`` would otherwise
    survive and let a default run reach the network without anyone asking for it.
    Idempotent, so a suite that applies it and is then run by ``run_all.py``
    (which applies it too) is not a conflict.
    """
    if os.environ.get(NETWORK_OPT_IN) == "1":
        os.environ.pop(OFFLINE_ENV, None)
    else:
        os.environ[OFFLINE_ENV] = "1"


def is_offline():
    """What :func:`apply` decided, for a suite that needs to branch on it."""
    return os.environ.get(OFFLINE_ENV) == "1"
