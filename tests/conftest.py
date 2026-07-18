"""Pytest parity for the isolation used by the standalone test runners."""

import sys

import pytest

from test_support import preserve_driver_state


@pytest.fixture(autouse=True)
def _isolate_trade_recap_driver_state():
    module = sys.modules.get("trade_recap")
    if module is None:
        yield
        return
    with preserve_driver_state(module):
        yield
