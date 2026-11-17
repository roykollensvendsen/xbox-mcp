# SPDX-License-Identifier: GPL-3.0-or-later
"""A stopped ratchet tells its child once, waits for it to clean up, and never kills it.

The ratchet runs the mutation tool and the suite as child processes. A mutation run
killed outright leaves a source file switched off, and a pytest told to stop twice cuts
its own cleanup short. These tests stand a small script in for the ratchet, running a
child through the ratchet's own `_run`, and stop it the ways a terminal, CI and kill do.
"""

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stops are POSIX signals")

#: Stands in for the ratchet: runs a command through its _run, and can be stopped.
RATCHET = """
import sys
sys.path.insert(0, sys.argv[1])
import cleanup, ratchet
cleanup.stop_cleanly()
if sys.argv[2] == "muted":  # as a mutation that switches the stop off leaves it
    ratchet._tell_to_stop = lambda step: None
ratchet._run(*sys.argv[4:], "-c", sys.argv[3])
"""

#: How the stand-in starts its child: straight, or through uv, as the ratchet starts its own.
STRAIGHT = (sys.executable,)
THROUGH_UV = ("uv", "run", "--no-project", "python")

#: Stands in for a mutation run: says it is going, and on Ctrl-C puts its line back. Every
#: Ctrl-C it gets is counted, because a second one would make pytest cut its cleanup short.
CHILD = """
import os, pathlib, signal, sys, time
marks = pathlib.Path(MARKS)
def put_back(*_):
    with open(marks / "put-back", "a") as told:
        told.write("x")
    time.sleep(1)  # room for a second Ctrl-C to arrive and be counted
    sys.exit(130)
signal.signal(signal.SIGINT, put_back)
(marks / "going").write_text(f"{os.getpid()} {os.getppid()}")  # this child, and what started it
time.sleep(120)
"""


def _stop_while_child_runs(tmp_path, stop, starts=STRAIGHT, *, muted=False):
    marks = tmp_path / "marks"
    marks.mkdir()
    child = CHILD.replace("MARKS", repr(str(marks)))
    mode = "muted" if muted else "-"
    stand_in = subprocess.Popen(
        [sys.executable, "-c", RATCHET, str(ROOT / "scripts"), mode, child, *starts],
        start_new_session=True,  # a process group of its own, as a terminal gives a command
    )
    try:
        for _ in range(600):
            if (marks / "going").exists() and (marks / "going").read_text():
                break
            time.sleep(0.05)
        assert (marks / "going").exists(), "the child never started"
        stop(stand_in)
        code = stand_in.wait(timeout=60)
    finally:
        if stand_in.poll() is None:
            os.killpg(stand_in.pid, signal.SIGKILL)
        _end_what_it_started(marks)
    return code, marks


def _end_what_it_started(marks):
    """End the child and what started it, if either is still running, and wait until both are gone.

    They run in a session of their own, which the stand-in's group kill never reaches. Each is
    ended by its own pid, never by group, and only while its command still names this test's
    folder, so a pid the system has since given to another process is left alone.
    """
    going = marks / "going"
    pids = [int(pid) for pid in going.read_text().split()] if going.exists() else []
    for pid in pids:
        with contextlib.suppress(OSError):
            if str(marks) in Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace"):
                os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 10
    while any(_running(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.05)


def test_a_stopped_ratchet_lets_its_child_clean_up_before_it_ends(tmp_path):
    code, marks = _stop_while_child_runs(tmp_path, lambda p: p.send_signal(signal.SIGTERM))  # kill, or CI
    assert code == 128 + signal.SIGTERM
    assert (marks / "put-back").read_text() == "x"  # told with Ctrl-C, not killed outright


def test_the_ratchet_s_child_hears_a_terminal_s_ctrl_c_only_through_the_ratchet(tmp_path):
    # A terminal's Ctrl-C goes to the whole process group: the ratchet and its child alike.
    code, marks = _stop_while_child_runs(tmp_path, lambda p: os.killpg(p.pid, signal.SIGINT))
    assert code != 0
    assert (marks / "put-back").read_text() == "x"  # once, not twice


@pytest.mark.skipif(
    shutil.which("uv") is None and not os.environ.get("CI"),
    reason="needs uv, which the ratchet starts its runs through (in CI, its absence fails instead)",
)
@pytest.mark.parametrize("stop", [signal.SIGTERM, signal.SIGINT], ids=["kill", "ctrl-c"])
def test_a_stopped_ratchet_tells_the_python_under_uv_once(tmp_path, stop):
    # The ratchet starts its runs through uv, and uv passes a Ctrl-C on to its own child.
    # Sent to uv's whole process group instead, it would reach that child twice.
    # In CI the proof must not drop out unseen: there a missing uv is a failure, not a skip.
    if shutil.which("uv") is None:
        pytest.fail("CI has no uv, so the proof that a stopped ratchet tells uv's child once cannot run")

    def send(stand_in):
        if stop == signal.SIGINT:
            os.killpg(stand_in.pid, stop)  # a terminal's Ctrl-C, to the ratchet's group
        else:
            stand_in.send_signal(stop)

    code, marks = _stop_while_child_runs(tmp_path, send, THROUGH_UV)
    assert code != 0
    assert (marks / "put-back").read_text() == "x"  # once, not twice


def _running(pid):
    """Whether process `pid` is still running, rather than ended or only a zombie left to reap."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def test_a_process_that_ends_while_it_is_read_counts_as_ended(monkeypatch):
    """Linux says "no such process" when a process ends between finding its file and reading it."""

    def ended(*_args, **_kwargs):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(Path, "read_text", ended)
    assert not _running(os.getpid())


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="reads running processes from /proc")
@pytest.mark.skipif(
    shutil.which("uv") is None and not os.environ.get("CI"),
    reason="needs uv, which the ratchet starts its runs through (in CI, its absence fails instead)",
)
def test_a_muted_stop_leaves_no_child_running_under_uv(tmp_path):
    """With the stop switched off, as a mutation leaves it, the child never ends by itself.

    It and its uv run in a session of their own, so the test must end them. Left running,
    the pair holds uv's cache lock for every later run on the machine.
    """
    if shutil.which("uv") is None:
        pytest.fail("CI has no uv, so the proof that a muted stop leaves nothing running cannot run")
    _, marks = _stop_while_child_runs(
        tmp_path, lambda p: p.send_signal(signal.SIGTERM), THROUGH_UV, muted=True
    )
    assert not (marks / "put-back").exists()  # the stop was muted: the child was never told
    pids = [int(pid) for pid in (marks / "going").read_text().split()]
    deadline = time.monotonic() + 10
    while any(_running(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.05)
    left = [pid for pid in pids if _running(pid)]
    assert not left, f"the child and its uv were left running: {left}"


#: Stands in for a suite the ratchet runs: says it is going, waits until the ratchet is gone,
#: then writes a lot, as pytest's summary does, and only then marks itself finished.
OUTLIVES = """
import os, pathlib, sys, time
marks = pathlib.Path(MARKS)
(marks / "going").write_text(str(os.getpid()))
while not (marks / "ratchet-gone").exists():
    time.sleep(0.05)
print("x" * 1_000_000, flush=True)
print("y" * 1_000_000, file=sys.stderr, flush=True)
(marks / "finished").touch()
"""


def test_a_child_outlives_a_ratchet_killed_with_kill_9_and_leaves_no_file(tmp_path, monkeypatch):
    # kill -9 is the way out the ratchet names when its child hangs; the child must then
    # still finish (a mutation run puts its line back), not die writing into a dead pipe.
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setenv("TMPDIR", str(temp))
    marks = tmp_path / "marks"
    marks.mkdir()
    child = OUTLIVES.replace("MARKS", repr(str(marks)))
    stand_in = subprocess.Popen([sys.executable, "-c", RATCHET, str(ROOT / "scripts"), "-", child, *STRAIGHT])
    pid = None
    try:
        for _ in range(600):
            if (marks / "going").exists() and (marks / "going").read_text():
                pid = int((marks / "going").read_text())
                break
            time.sleep(0.05)
        assert pid, "the child never started"
        stand_in.kill()
        stand_in.wait(timeout=60)
        (marks / "ratchet-gone").touch()
        for _ in range(600):
            if (marks / "finished").exists() or not Path(f"/proc/{pid}").exists():
                break
            time.sleep(0.05)
        assert (marks / "finished").exists(), "the child died once the ratchet was gone"
        assert sorted(p.name for p in temp.iterdir()) == []
    finally:
        if stand_in.poll() is None:
            stand_in.kill()
        if pid and Path(f"/proc/{pid}").exists():
            os.kill(pid, signal.SIGKILL)
