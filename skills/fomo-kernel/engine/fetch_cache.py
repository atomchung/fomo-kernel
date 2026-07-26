#!/usr/bin/env python3
"""Same-day disk cache for prepare's two network fetches (#235 proposal 3).

``prepare`` is the only step that touches the network, and today every run —
including a ``resume`` on the same session — re-fetches from scratch. This
module keeps the previous fetch on disk so a same-day rerun answers from it.

Three properties are load-bearing, in order:

**A stale entry can never enter a deterministic test.** Both call sites consult
the cache *after* ``import yfinance`` succeeds, so ``test_state_loop``'s offline
shim (import raises ``ImportError``) returns the degraded result before the
cache is reachable. An agent-supplied envelope never reaches it either — that
path does not touch the network at all.

**A new day re-fetches.** One file holds one date; reading on a different date
discards the whole file rather than merging, so yesterday's closes cannot be
served today and no separate GC step is needed.

**The cache never creates the state root.** It is opportunistic: when the root
does not exist yet (a bare engine run in a test, a first-ever review) caching is
skipped entirely rather than writing a directory into someone's home.

The file lives at ``<root>/cache/<kind>.json``. That is a top-level directory,
so ``session.iter_canonical_bundles`` — which walks ``<root>/sessions`` only —
never sees it, and ``coach.DATA_FILES`` registers it so ``data reset`` deletes
it with everything else (the keys are the user's own tickers, which is private
even though the values are public market data).
"""

import datetime as dt
import hashlib
import json
import os
import tempfile

import session

_DIR = "cache"


def _today(today=None):
    return str(today or dt.date.today().isoformat())


def _root(root=None):
    return root or session.default_root()


def _path(kind, root=None):
    return os.path.join(_root(root), _DIR, f"{kind}.json")


def _key(inputs):
    """Stable digest of a call's inputs, so one file can hold several portfolios."""
    canonical = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _read_day(kind, root=None, today=None):
    """Return the file's entries when it belongs to ``today``, else an empty dict."""
    try:
        with open(_path(kind, root), encoding="utf-8") as handle:
            blob = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(blob, dict) or blob.get("date") != _today(today):
        return {}                                          # a different day is a miss, not a merge
    entries = blob.get("entries")
    return entries if isinstance(entries, dict) else {}


def load(kind, inputs, root=None, today=None):
    """Return the cached JSON value for these inputs, or None on any miss."""
    return _read_day(kind, root, today).get(_key(inputs))


def store(kind, inputs, value, root=None, today=None):
    """Persist ``value`` for these inputs. Never raises, never creates the root."""
    base = _root(root)
    if not os.path.isdir(base):
        return False                                       # opportunistic: do not create the state root
    entries = dict(_read_day(kind, root, today))
    entries[_key(inputs)] = value
    directory = os.path.join(base, _DIR)
    try:
        os.makedirs(directory, exist_ok=True)
        blob = {"date": _today(today), "entries": entries}
        # Same-day reruns can overlap (a resume while a prepare is still
        # writing); rename is atomic so a reader sees one whole day or none.
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory,
                                         delete=False) as handle:
            json.dump(blob, handle, ensure_ascii=False)
            staged = handle.name
        os.replace(staged, _path(kind, root))
        return True
    except OSError:
        return False                                       # a cache write must never fail a review
