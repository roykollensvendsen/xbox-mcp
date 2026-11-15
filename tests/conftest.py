# SPDX-License-Identifier: GPL-3.0-or-later
"""What holds around the whole test session, and around every test in it.

**The session's own temporary folder.** Each run keeps every temporary file its
tests make in one held folder of its own (``scripts/cleanup.py``): pytest's folders,
anything made with ``tempfile``, and anything a program the tests start makes under
``$TMPDIR``. The run holds a lock in that folder while it lasts and removes the
folder when it ends; a killed run never gets that far, so the next run sweeps away
any such folder whose lock nobody holds. pytest's own clean-up is not used for this:
it works on a folder every project on the machine shares.

**The stop handlers.** A test that runs ``scripts/mutate.py``'s or
``scripts/ratchet.py``'s ``main`` in this process installs their stop handlers,
which turn Ctrl-C into a clean exit. Left in place, the next test's Ctrl-C would be
handled as theirs; so each test gets the handlers back as it found them.
"""

import os
import pathlib
import signal
import tempfile

import cleanup
import pytest

STOPS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
PREFIX = "xbox-mcp-tests-"
#: This run's held folder, and $TMPDIR and tempfile's folder from before.
RUN = pytest.StashKey[tuple[cleanup.HeldFolder, str | None, str | None]]()


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    if config.option.basetemp:  # a run told where to keep them keeps them there
        return
    before, cached = os.environ.get("TMPDIR"), tempfile.tempdir
    temp = pathlib.Path(tempfile.gettempdir())
    cleanup.sweep(temp, PREFIX)
    held = cleanup.HeldFolder(PREFIX, temp)
    config.stash[RUN] = (held, before, cached)
    os.environ["TMPDIR"] = str(held.path)
    tempfile.tempdir = str(held.path)
    # A folder inside it, beside the lock: pytest empties its basetemp first.
    config.option.basetemp = str(held.path / "pytest")


def pytest_unconfigure(config: pytest.Config) -> None:
    run = config.stash.get(RUN, None)
    if run is None:
        return
    held, before, cached = run
    if before is None:
        os.environ.pop("TMPDIR", None)
    else:
        os.environ["TMPDIR"] = before
    tempfile.tempdir = cached
    # RULE: a test run removes its temporary folder when it ends
    held.remove()


@pytest.fixture(autouse=True)
def _stop_handlers_as_found():
    before = {each: signal.getsignal(each) for each in STOPS}
    yield
    for each, handler in before.items():
        # RULE: a test leaves the stop handlers as it found them
        signal.signal(each, handler)
