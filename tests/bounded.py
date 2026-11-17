# SPDX-License-Identifier: GPL-3.0-or-later
"""Waits a test sets for itself, so a test that waits too long fails instead of hanging.

None of this limits a real run: a mutation run, the ratchet and the suite itself have no
time limit, by the owner's rule. These bound only a test's own wait for something it started.
"""

import contextlib
import os
import signal
import subprocess

import pytest

#: The one bound for every wait a test sets: generous enough to fire only on a real hang.
WITHIN = 300


def run_bounded(command, within=WITHIN, *, check=False, **options):
    """Run `command` the way subprocess.run would, but fail the test if it takes longer than `within`.

    subprocess.run's own timeout ends only the child it started, so a grandchild, as under
    `uv run` or `shell=True`, ran on into the next tests. The child starts a group of its
    own here, and running out ends the whole group. SIGKILL, never Ctrl-C: sent to a group
    under uv, a Ctrl-C reaches Python twice. A test that means to send one does it itself.
    """
    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, **options
    ) as child:
        try:
            out, err = child.communicate(timeout=within)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            out, err = child.communicate()
            pytest.fail(f"{command!r} did not end within {within} s. It wrote:\n{out}\n{err}")
    done = subprocess.CompletedProcess(command, child.returncode, out, err)
    if check:
        done.check_returncode()
    return done
