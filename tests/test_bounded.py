# SPDX-License-Identifier: GPL-3.0-or-later
"""The bounds a test sets on its own waits fail that test, and leave nothing running."""

import contextlib
import os
import signal
import time
from pathlib import Path

import pytest
from bounded import run_bounded

pytestmark = pytest.mark.skipif(not Path("/proc").is_dir(), reason="reads running processes from /proc")


def _running(pid):
    """Whether process `pid` is still running, rather than ended or only a zombie left to reap."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def test_a_child_that_runs_out_of_time_leaves_no_process_of_its_group(tmp_path):
    """A shell's grandchild holds its output open, as one under uv or shell=True would.

    subprocess.run's own timeout ends only the shell, so the grandchild ran on into the next
    tests. The whole group must be gone once the test has failed.
    """
    told = tmp_path / "pids"
    script = f'sleep 30 & echo $$ $! > "{told}"; wait'
    try:
        started = time.monotonic()
        with pytest.raises(pytest.fail.Exception, match="did not end within 2 s"):
            run_bounded(["sh", "-c", script], 2)
        # Ending only the shell would leave the sleep holding the output, and reading it
        # would wait the sleep's whole 30 s, which it ends by itself before the checks below.
        assert time.monotonic() - started < 15, "the bound was held up by the grandchild"
        shell, grandchild = (int(pid) for pid in told.read_text().split())
        deadline = time.monotonic() + 10
        while _running(grandchild) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _running(grandchild), "the shell's grandchild was left running"
        with pytest.raises(ProcessLookupError):
            os.killpg(shell, 0)
    finally:
        _end_if_still_the_sleep(told)


def _end_if_still_the_sleep(told):
    """End the grandchild by its own pid, and only while it is still the sleep this test started."""
    if not told.exists():
        return
    grandchild = int(told.read_text().split()[1])
    with contextlib.suppress(OSError):
        if Path(f"/proc/{grandchild}/cmdline").read_bytes().split(b"\0")[:2] == [b"sleep", b"30"]:
            os.kill(grandchild, signal.SIGKILL)
