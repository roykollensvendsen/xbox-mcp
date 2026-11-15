# SPDX-License-Identifier: GPL-3.0-or-later
"""The autouse fixture in conftest.py gives every test the stop handlers as they were.

Two tests, run in this order: the first changes all three handlers and leaves them
changed, and the second finds them as Python sets them.
"""

import signal

from conftest import STOPS

AS_PYTHON_SETS_THEM = {
    signal.SIGINT: signal.default_int_handler,
    signal.SIGTERM: signal.SIG_DFL,
    signal.SIGHUP: signal.SIG_DFL,
}


def test_a_test_may_change_the_stop_handlers():
    for each in STOPS:
        signal.signal(each, signal.SIG_IGN)


def test_a_test_leaves_the_stop_handlers_as_it_found_them():
    assert {each: signal.getsignal(each) for each in STOPS} == AS_PYTHON_SETS_THEM
