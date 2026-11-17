# SPDX-License-Identifier: GPL-3.0-or-later
"""A run of the mutation tool or the ratchet leaves nothing in the machine's temporary folder.

Both run the whole suite, often many times over, and each run made folders under
/tmp. A day of runs left hundreds of them, gigabytes in all. Each test here
points the temporary folder at an empty one of its own, runs the real script in
a separate process, and checks the folder is empty again afterwards: after a
run that finishes, after one that is told to stop part way, and after one told
to stop a second time while it is still cleaning up.
"""

import json
import os
import pathlib
import signal
import subprocess
import sys
import time

import cleanup
import pytest
from bounded import run_bounded

ROOT = pathlib.Path(__file__).parent.parent

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stop signal is a POSIX one")

RULED = """def one():
    # RULE: one is one
    return 1
"""
ROW = """[[rule]]
name = "one is one"
file = "src/ruled/__init__.py"
find = 'return 1'
replace = 'return 2'
"""


def _project(tmp_path, test_body):
    """A tiny repository with one rule, its row, its test, and a copy of the mutation tool."""
    project = tmp_path / "project"
    (project / "scripts" / "mutations" / "src" / "ruled" / "__init__.py").mkdir(parents=True)
    (project / "src" / "ruled").mkdir(parents=True)
    (project / "tests").mkdir()
    for script in ("mutate.py", "cleanup.py"):
        (project / "scripts" / script).write_text((ROOT / "scripts" / script).read_text())
    (project / "scripts" / "mutations" / "src" / "ruled" / "__init__.py" / "one-is-one.toml").write_text(ROW)
    (project / "src" / "ruled" / "__init__.py").write_text(RULED)
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
    # The tiny project's package is found from its own src, as an installed checkout's is.
    env = {**os.environ, "TMPDIR": str(folder), "PYTHONPATH": str(tmp_path / "project" / "src")}
    if slowed:
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        (hooks / "sitecustomize.py").write_text(SLOW_CLEANUP)
        paths = f"{hooks}{os.pathsep}{env['PYTHONPATH']}"
        env |= {"PYTHONPATH": paths, "SLOWED": slowed, "CLEANUP_STARTED": str(tmp_path / "started")}
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
        assert process.poll() is None, process.communicate(timeout=within)
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
    done = run_bounded([sys.executable, "scripts/mutate.py"], cwd=project, env=env, text=True)
    assert "1/1 rules" in done.stdout, done.stdout + done.stderr
    assert _left_in(folder) == []


def _mutating(tmp_path, slowed=""):
    project = _project(tmp_path, "    time.sleep(60)\n")
    folder, env = _temporary(tmp_path, slowed)
    command = [sys.executable, "scripts/mutate.py"]
    process = subprocess.Popen(command, cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return project, folder, process


def _ratchet(tmp_path, slowed=""):
    """The ratchet's measure, with a stand-in `uv` that records its arguments and then waits."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    called = tmp_path / "called.txt"
    uv = bin_ / "uv"
    # `uv run python ...` is the check of where the package comes from: let it through.
    uv.write_text(
        f'#!/bin/sh\nif [ "$1 $2" = "run python" ]; then shift 2; exec {sys.executable} "$@"; fi\n'
        f'echo "$@" > {called}\nexec sleep 60\n'
    )
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


def _started(tmp_path, script, slowed=""):
    """The script under way: the mutation tool in a tiny project, or the ratchet with a stand-in uv."""
    if script == "mutate.py":
        project, folder, process = _mutating(tmp_path, slowed)
        return process, lambda: any(folder.glob("*/run-*")), folder, project
    process, called, folder = _ratchet(tmp_path, slowed)
    return process, called.exists, folder, None


SCRIPTS = ["mutate.py", "ratchet.py"]


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("stop", STOPS)
def test_a_stopped_run_cleans_up_after_itself(tmp_path, script, stop):
    process, under_way, folder, project = _started(tmp_path, script)
    _stop_once(process, under_way, stop)
    assert _left_in(folder) == []
    if project:
        assert (project / "src" / "ruled" / "__init__.py").read_text() == RULED


@pytest.mark.parametrize("script", SCRIPTS)
def test_a_second_stop_cannot_cut_a_run_s_cleanup_short(tmp_path, script):
    process, under_way, folder, project = _started(tmp_path, script, slowed=script)
    _stop_twice(process, under_way, tmp_path / "started")
    assert _left_in(folder) == []
    if project:
        assert (project / "src" / "ruled" / "__init__.py").read_text() == RULED


TEST_RUN = """
import tempfile


def test_it_makes_temporary_files(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    tempfile.mkdtemp()
    tempfile.mkstemp()
"""


def test_a_test_run_removes_its_temporary_folder_when_it_ends(tmp_path):
    """A run of this suite's own set-up, in a tiny project: nothing stays but a live run's folder."""
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "scripts").mkdir()
    (project / "scripts" / "cleanup.py").write_text((ROOT / "scripts" / "cleanup.py").read_text())
    (project / "tests" / "conftest.py").write_text((ROOT / "tests" / "conftest.py").read_text())
    (project / "tests" / "test_it.py").write_text(TEST_RUN)
    (project / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["scripts"]\n')
    folder, env = _temporary(tmp_path)
    live = cleanup.HeldFolder("xbox-mcp-tests-", folder)
    killed = folder / "xbox-mcp-tests-killed"
    killed.mkdir()
    (killed / cleanup.LOCK).write_text("")
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    done = run_bounded(command, cwd=project, env=env, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert _left_in(folder) == sorted(
        str(p.relative_to(folder)) for p in [live.path, live.path / cleanup.LOCK]
    )
    live.remove()


def test_only_one_mutation_run_goes_at_a_time_in_a_checkout(tmp_path):
    project = _project(tmp_path, "")
    folder, env = _temporary(tmp_path)
    running = cleanup.only_run(project / "scripts", "A mutation run")  # as a run going now holds it
    try:
        command = [sys.executable, "scripts/mutate.py"]
        done = run_bounded(command, cwd=project, env=env, text=True)
    finally:
        os.close(running)
    assert done.returncode != 0
    assert "already going" in done.stderr, done.stdout + done.stderr
    assert "rules" not in done.stdout
    assert (project / "src" / "ruled" / "__init__.py").read_text() == RULED
    assert _left_in(folder) == []


def test_a_repair_only_run_puts_back_a_mutated_file_and_does_nothing_else(tmp_path):
    project = _project(tmp_path, "")
    folder, env = _temporary(tmp_path)
    source = project / "src" / "ruled" / "__init__.py"
    mutated = RULED.replace("return 1", "return 2")
    source.write_text(mutated)
    recovery = project / "scripts" / ".mutate-recovery.json"
    recovery.write_text(json.dumps({"file": "src/ruled/__init__.py", "original": RULED, "mutated": mutated}))
    command = [sys.executable, "scripts/mutate.py", "--repair-only"]
    done = run_bounded(command, cwd=project, env=env, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert source.read_text() == RULED
    assert not recovery.exists()
    assert "rules" not in done.stdout  # no rule was switched off
    assert _left_in(folder) == []
