# SPDX-License-Identifier: GPL-3.0-or-later
"""The cleanup the mutation tool, the ratchet and the suite share (scripts/cleanup.py).

A run keeps a lock open in its folder while it runs; the next run's sweep removes
only folders whose lock nobody holds, and leaves a folder with no lock alone for
an hour, since its run may be about to lock it. A fixed-name folder in the shared
temporary area is used only once it is checked to be this user's own. And before
measuring, a run checks the code it would measure is this checkout's own.
"""

import fcntl
import os
import pathlib
import sys
import time

import cleanup
import pytest

ROOT = pathlib.Path(__file__).parent.parent
AN_HOUR_AND_A_MINUTE_AGO = time.time() - cleanup.GRACE_SECONDS - 60


def _left_by_a_killed_run(place, name, *, locked_file=True, age=AN_HOUR_AND_A_MINUTE_AGO):
    folder = place / name
    folder.mkdir()
    (folder / "work.txt").write_text("x")
    if locked_file:
        (folder / cleanup.LOCK).write_text("")
    os.utime(folder, (age, age))
    return folder


def test_a_held_folder_still_in_use_is_never_removed(tmp_path):
    held = cleanup.HeldFolder("run-", tmp_path)
    os.utime(held.path, (AN_HOUR_AND_A_MINUTE_AGO, AN_HOUR_AND_A_MINUTE_AGO))
    assert cleanup.sweep(tmp_path, "run-") == []
    assert held.path.is_dir()
    held.remove()
    assert not held.path.exists()


def test_a_held_folder_a_killed_run_left_is_removed_by_the_next_sweep(tmp_path):
    left = _left_by_a_killed_run(tmp_path, "run-killed")
    other = _left_by_a_killed_run(tmp_path, "someone-else")
    assert cleanup.sweep(tmp_path, "run-") == ["run-killed"]
    assert not left.exists()
    assert other.exists()  # only folders of its own kind


def test_a_folder_with_no_lock_is_left_alone_for_the_grace_period(tmp_path):
    starting = _left_by_a_killed_run(tmp_path, "run-starting", locked_file=False, age=time.time())
    old = _left_by_a_killed_run(tmp_path, "run-old", locked_file=False)
    assert cleanup.sweep(tmp_path, "run-") == ["run-old"]
    assert not old.exists()
    assert starting.is_dir()
    assert not (starting / cleanup.LOCK).exists()  # a sweep never makes a lock in another's folder


def test_a_sweep_never_follows_a_link(tmp_path):
    outside = _left_by_a_killed_run(tmp_path, "outside")
    (tmp_path / "run-link").symlink_to(outside)
    assert cleanup.sweep(tmp_path, "run-") == []
    assert (outside / "work.txt").exists()


def _another_user(monkeypatch):
    """Make every folder this user owns look like another user's, as a second account would see it."""
    real = os.getuid()
    monkeypatch.setattr(cleanup.os, "getuid", lambda: real + 1)


def test_a_shared_folder_that_is_not_ours_stops_the_run(tmp_path, monkeypatch):
    made = cleanup.ours(tmp_path / "fresh")
    assert made.is_dir()
    assert made.stat().st_mode & 0o777 == 0o700
    (tmp_path / "link").symlink_to(made)
    with pytest.raises(SystemExit, match="cannot be opened as a folder"):
        cleanup.ours(tmp_path / "link")
    _another_user(monkeypatch)
    with pytest.raises(SystemExit, match="belongs to user") as refused:
        cleanup.ours(made)
    assert f"rm -r {made}" in str(refused.value)


@pytest.mark.parametrize("mode", [0o775, 0o757, 0o777])
def test_a_shared_folder_others_can_write_to_stops_the_run(tmp_path, mode):
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(mode)
    with pytest.raises(SystemExit, match="written to by others") as refused:
        cleanup.ours(shared)
    assert f"rm -r {shared}" in str(refused.value)


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o705])
def test_a_shared_folder_others_may_only_read_is_closed_to_them(tmp_path, mode):
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(mode)
    assert cleanup.ours(shared) == shared
    assert shared.stat().st_mode & 0o777 == 0o700


def test_a_sweep_never_touches_another_user_s_folder(tmp_path, monkeypatch):
    left = _left_by_a_killed_run(tmp_path, "run-other")
    _another_user(monkeypatch)
    assert not cleanup.left_behind(left)
    assert cleanup.sweep(tmp_path, "run-") == []
    assert (left / "work.txt").exists()


def test_a_run_refuses_a_package_imported_from_another_checkout(tmp_path, monkeypatch):
    cleanup.from_this_checkout(ROOT, [sys.executable])  # this checkout's own: no complaint
    other = tmp_path / "other-checkout" / "src" / "xbox_mcp"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("")
    monkeypatch.setenv("PYTHONPATH", str(other.parent))
    with pytest.raises(SystemExit, match="uv sync") as refused:
        cleanup.from_this_checkout(ROOT, [sys.executable])
    said = str(refused.value)
    assert str(other / "__init__.py") in said
    assert str(ROOT.resolve() / "src") in said


def test_a_run_refuses_a_checkout_with_no_package(tmp_path):
    (tmp_path / "src").mkdir()
    with pytest.raises(SystemExit, match="no package") as refused:
        cleanup.from_this_checkout(tmp_path, [sys.executable])
    assert str(tmp_path / "src") in str(refused.value)


def test_a_held_folder_s_lock_is_taken_before_its_name_stands(tmp_path):
    """The lock is made under another name and renamed, so no sweep sees `.in-use` unlocked."""
    held = cleanup.HeldFolder("run-", tmp_path)
    lock = os.open(held.path / cleanup.LOCK, os.O_RDONLY)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(lock)
        held.remove()
