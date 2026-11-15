# SPDX-License-Identifier: GPL-3.0-or-later
"""What holds around every test in this suite.

A test that runs `scripts/mutate.py`'s or `scripts/ratchet.py`'s `main` in this
process installs their stop handlers, which turn Ctrl-C into a clean exit. Left in
place, the next test's Ctrl-C would be handled as theirs; so each test gets the
handlers back as it found them.
"""

import signal

import pytest

STOPS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


@pytest.fixture(autouse=True)
def _stop_handlers_as_found():
    before = {each: signal.getsignal(each) for each in STOPS}
    yield
    for each, handler in before.items():
        # RULE: a test leaves the stop handlers as it found them
        signal.signal(each, handler)
