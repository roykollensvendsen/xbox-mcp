# SPDX-License-Identifier: GPL-3.0-or-later
"""The ratchet measures main in a copy holding that one commit, and nothing else.

A worktree would share every branch of this clone, and git keeps a record of it that a
killed run leaves behind. A repository of its own, fetched to one commit, shares nothing
and leaves no record. After grocery-mcp's tests of the same copy.
"""

import importlib.util
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).parent.parent


def _ratchet():
    spec = importlib.util.spec_from_file_location("ratchet_copy", ROOT / "scripts/ratchet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(where, *args):
    done = subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def _clone_with_history(tmp_path):
    """A clone with two commits on main, and another branch."""
    clone = tmp_path / "clone"
    clone.mkdir()
    _git(clone, "init", "-q", "-b", "main")
    _git(clone, "config", "user.email", "someone@example.com")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "config", "commit.gpgsign", "false")
    for text in ("first", "second"):
        (clone / "file.txt").write_text(text, encoding="utf-8")
        _git(clone, "add", "file.txt")
        _git(clone, "commit", "-q", "-m", text)
    _git(clone, "branch", "other")
    return clone


def test_the_base_is_measured_where_no_other_branch_can_be_seen(tmp_path, monkeypatch):
    ratchet = _ratchet()
    clone = _clone_with_history(tmp_path)
    monkeypatch.setattr(ratchet, "ROOT", clone)
    copy = tmp_path / "copy"
    ratchet.copy_of("main", copy)
    assert (copy / "file.txt").read_text(encoding="utf-8") == "second"
    assert _git(copy, "for-each-ref") == "", "the copy can see this clone's branches"
    assert _git(copy, "rev-list", "--all", "--count") == "1"
    assert len(_git(clone, "worktree", "list").splitlines()) == 1, "the clone keeps a record of the copy"


def test_the_base_may_be_a_commit_no_branch_points_at(tmp_path, monkeypatch):
    """CI's base is main as it was when the pull request began, which main may have moved past."""
    ratchet = _ratchet()
    clone = _clone_with_history(tmp_path)
    monkeypatch.setattr(ratchet, "ROOT", clone)
    copy = tmp_path / "copy"
    ratchet.copy_of("main~1", copy)  # named as git names it, not as a branch
    assert (copy / "file.txt").read_text(encoding="utf-8") == "first"
