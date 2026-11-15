# SPDX-License-Identifier: GPL-3.0-or-later
"""A run of the mutation tool or the ratchet leaves nothing in the machine's temporary folder.

Both run the whole suite, often many times over, and each run made folders under
/tmp. A day of runs left hundreds of them, gigabytes in all. Each test here
points the temporary folder at an empty one of its own, runs the real script in
a separate process, and checks the folder is empty again afterwards: after a
run that finishes, after one that is told to stop part way, and after one told
to stop a second time while it is still cleaning up.
"""

import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).parent.parent

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stop signal is a POSIX one")

RULED = """def one():
    # RULE: one is one
    return 1
"""
ROW = """[[rule]]
name = "one is one"
file = "src/ruled.py"
find = 'return 1'
replace = 'return 2'
"""


def _project(tmp_path, test_body):
    """A tiny repository with one rule, its row, its test, and a copy of the mutation tool."""
    project = tmp_path / "project"
    (project / "scripts" / "mutations" / "src" / "ruled.py").mkdir(parents=True)
    (project / "src").mkdir()
    (project / "tests").mkdir()
    (project / "scripts" / "mutate.py").write_text((ROOT / "scripts" / "mutate.py").read_text())
    (project / "scripts" / "mutations" / "src" / "ruled.py" / "one-is-one.toml").write_text(ROW)
    (project / "src" / "ruled.py").write_text(RULED)
    (project / "tests" / "test_ruled.py").write_text(
        "import sys, time\nsys.path.insert(0, 'src')\nfrom ruled import one\n\n"
        f"def test_one_is_one():\n{test_body}    assert one() == 1\n"
    )
    return project


#: Loaded at start by every Python the test starts. In the script under test alone, each
#: removal of a folder first says it has begun and then waits, so a second stop lands inside it.
SLOW_CLEANUP = """
import os, pathlib, shutil, sys, time

_real = shutil.rmtree


def _slow(path, *args, **kwargs):
    if sys.argv[0].endswith(os.environ["SLOWED"]):
        pathlib.Path(os.environ["CLEANUP_STARTED"]).touch()
        time.sleep(1.5)
    return _real(path, *args, **kwargs)


shutil.rmtree = _slow
"""


def _temporary(tmp_path, slowed=""):
    folder = tmp_path / "tmp"
    folder.mkdir()
    env = {**os.environ, "TMPDIR": str(folder)}
    if slowed:
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        (hooks / "sitecustomize.py").write_text(SLOW_CLEANUP)
        env |= {"PYTHONPATH": str(hooks), "SLOWED": slowed, "CLEANUP_STARTED": str(tmp_path / "started")}
    return folder, env


def _left_in(folder):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


STOPS = [
    pytest.param(signal.SIGINT, id="ctrl-c"),
    pytest.param(signal.SIGTERM, id="a-cancelled-job"),
    pytest.param(signal.SIGHUP, id="a-closed-terminal"),
]


def _when(process, ready, within=30.0):
    deadline = time.monotonic() + within
    while not ready():
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, "the run never got that far"
        time.sleep(0.05)


def _stop_once(process, ready, stop=signal.SIGTERM, within=30.0):
    """Send a stop once `ready()` holds, the way Ctrl-C, a CI timeout or `kill` would."""
    _when(process, ready, within)
    process.send_signal(stop)
    process.communicate(timeout=within)


def _stop_twice(process, ready, started, within=30.0):
    """Ctrl-C once `ready()` holds, then, once the cleanup has begun, more stops until it ends.

    One more stop is not enough: a cleanup it cuts short is redone by the next `finally`
    out. Someone who presses Ctrl-C again and again cuts every one of them short.
    """
    _when(process, ready, within)
    process.send_signal(signal.SIGINT)
    _when(process, started.exists, within)
    deadline = time.monotonic() + within
    while process.poll() is None:
        assert time.monotonic() < deadline, "the run never ended"
        process.send_signal(signal.SIGTERM)
        time.sleep(0.1)
    process.communicate(timeout=within)


def test_a_mutation_run_leaves_nothing_in_the_temporary_folder(tmp_path):
    project = _project(tmp_path, "")
    folder, env = _temporary(tmp_path)
    done = subprocess.run(
        [sys.executable, "scripts/mutate.py"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "1/1 rules" in done.stdout, done.stdout + done.stderr
    assert _left_in(folder) == []


def _mutating(tmp_path, slowed=""):
    project = _project(tmp_path, "    time.sleep(60)\n")
    folder, env = _temporary(tmp_path, slowed)
    command = [sys.executable, "scripts/mutate.py"]
    process = subprocess.Popen(command, cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return project, folder, process


@pytest.mark.parametrize("stop", STOPS)
def test_a_stopped_mutation_run_cleans_up_after_itself(tmp_path, stop):
    project, folder, process = _mutating(tmp_path)
    _stop_once(process, lambda: any(folder.glob("*/run-*")), stop)
    assert _left_in(folder) == []
    assert (project / "src" / "ruled.py").read_text() == RULED


def test_a_second_stop_cannot_cut_a_mutation_run_s_cleanup_short(tmp_path):
    project, folder, process = _mutating(tmp_path, slowed="mutate.py")
    _stop_twice(process, lambda: any(folder.glob("*/run-*")), tmp_path / "started")
    assert _left_in(folder) == []
    assert (project / "src" / "ruled.py").read_text() == RULED


def _ratchet(tmp_path, slowed=""):
    """The ratchet's measure, with a stand-in `uv` that records its arguments and then waits."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    called = tmp_path / "called.txt"
    uv = bin_ / "uv"
    uv.write_text(f'#!/bin/sh\necho "$@" > {called}\nexec sleep 60\n')
    uv.chmod(0o755)
    folder, env = _temporary(tmp_path, slowed)
    env["PATH"] = f"{bin_}{os.pathsep}{env['PATH']}"
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "ratchet.py"), "measure"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return process, called, folder


def test_the_ratchet_s_test_run_keeps_its_files_in_its_own_folder(tmp_path):
    """Without a folder of its own, the ratchet's suite writes to pytest's shared one, never emptied."""
    process, called, folder = _ratchet(tmp_path)
    _stop_once(process, called.exists)
    words = called.read_text().split()
    basetemp = [w.removeprefix("--basetemp=") for w in words if w.startswith("--basetemp=")]
    assert basetemp, words
    assert pathlib.Path(basetemp[0]).is_relative_to(folder)


@pytest.mark.parametrize("stop", STOPS)
def test_a_stopped_ratchet_cleans_up_its_temporary_files(tmp_path, stop):
    process, called, folder = _ratchet(tmp_path)
    _stop_once(process, called.exists, stop)
    assert _left_in(folder) == []


def test_a_second_stop_cannot_cut_a_ratchet_s_cleanup_short(tmp_path):
    process, called, folder = _ratchet(tmp_path, slowed="ratchet.py")
    _stop_twice(process, called.exists, tmp_path / "started")
    assert _left_in(folder) == []
