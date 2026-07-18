"""Shared isolation helpers for the standalone regression suites."""

from contextlib import contextmanager


@contextmanager
def preserve_driver_state(module):
    """Restore driver-map globals even when a test replaces or mutates them.

    Several suites intentionally inject synthetic driver classifications.  The
    standard-library runners continue after a failed test, so cleanup must live
    outside each test body rather than depend on a trailing assignment.
    """
    original_map = module._DRIVER_MAP
    original_contents = dict(original_map)
    original_skipped = module._DM_SKIPPED
    try:
        yield
    finally:
        original_map.clear()
        original_map.update(original_contents)
        module._DRIVER_MAP = original_map
        module._DM_SKIPPED = original_skipped
