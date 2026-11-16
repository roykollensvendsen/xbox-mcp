# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: INP001 - scripts/ is a folder of scripts, not a package; they import this by name
"""How the mutation tool, the ratchet and the test suite clean up after themselves.

All three run the whole suite, often many times over, and keep temporary files
while they do. Three things here make sure none of it stays behind:

* **A held folder.** Each run works in a folder of its own, and keeps a lock open
  inside it for as long as it runs. A kill runs no cleanup, so whoever starts next
  sweeps away every folder of its kind whose lock nobody holds. A lock is held by
  an open file, not a process number, so a run in another process namespace, or a
  reused number, never fools the sweep. The way is the voice bridge's
  (``src/claude_voice/held_folder.py``); the cleanup checklist names it the
  reference.
* **A shared folder that is ours.** A folder with a fixed name in the shared
  temporary area could have been made by someone else, or be a link to somewhere
  else. It is used only once it is checked to be this user's own private folder.
* **Every stop cleans up.** Ctrl-C, a cancelled job and a closed terminal end a run
  as an error would, so every ``finally`` runs; once stopping, further stops are
  ignored.

It also checks, before anything is measured, that the code under test is this
checkout's: two checkouts sharing one environment once measured each other's.

Nothing here imports the plugin: the ratchet runs this file from a pull request in
the environment of the base it measures.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pathlib
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

#: The lock a held folder keeps open while it is in use.
LOCK = ".in-use"
#: How old a folder with no lock must be before it counts as left by a killed run. A guess:
#: long enough for any run to have gone from making its folder to locking it.
GRACE_SECONDS = 60 * 60
#: The signals that stop a run from outside: Ctrl-C, a cancelled CI job, and a closed terminal.
STOPS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class HeldFolder:
    """A new folder in `place`, named `prefix` and some letters, locked until `remove`."""

    def __init__(self, prefix: str, place: pathlib.Path | None = None) -> None:
        """Make the folder and take its lock, under another name first."""
        self.path = pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=place))
        # Locked under another name, then renamed, so the lock's name never stands unlocked.
        starting = self.path / (LOCK + "-starting")
        self._lock: int | None = os.open(starting, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self._lock, fcntl.LOCK_EX)
        starting.rename(self.path / LOCK)

    def remove(self) -> None:
        """Let go of the lock and remove the folder."""
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None
        remove(self.path)

    def __enter__(self) -> pathlib.Path:
        """The folder, held until the block ends."""
        return self.path

    def __exit__(self, *_exc: object) -> None:
        """Remove the folder, however the block ended."""
        self.remove()


def left_behind(path: pathlib.Path) -> bool:
    """Whether `path` is a held folder of this user's whose lock nobody holds."""
    try:
        found = path.lstat()
    except FileNotFoundError:
        return False  # another sweep removed it first
    # RULE: a sweep never touches another user's folder
    theirs = found.st_uid != os.getuid()
    if theirs or not stat.S_ISDIR(found.st_mode):
        return False  # another user's folder is never touched, and a link never followed
    try:
        # Opened only if it is there: a sweep never makes a lock in someone else's folder.
        lock = os.open(path / LOCK, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        # Made before its lock existed, or by a run killed between the two. The age is the
        # first look's, so a folder removed since is passed over rather than failing the run.
        # RULE: a folder with no lock is left alone for the grace period
        return time.time() - found.st_mtime > GRACE_SECONDS
    except OSError:
        return False
    try:
        # RULE: a held folder still in use is never removed
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    finally:
        os.close(lock)
    return True


def sweep(place: pathlib.Path, prefix: str) -> list[str]:
    """Remove every folder in `place` named `prefix`... whose run is gone; name each one."""
    removed = []
    for path in sorted(place.glob(prefix + "*")):
        # RULE: a held folder a killed run left is removed by the next sweep
        if left_behind(path):
            remove(path)
            removed.append(path.name)
    return removed


def ours(folder: pathlib.Path) -> pathlib.Path:
    """`folder`, made if missing, once it is checked to be this user's own private folder.

    A fixed name in the shared temporary area can be taken first by someone else, or
    be a link that leads elsewhere; the run would then keep its files there, or remove
    another's. So it is opened without following a link and checked as it is opened.
    """
    with contextlib.suppress(FileExistsError):
        folder.mkdir(mode=0o700)
    try:
        opened = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        found = f"cannot be opened as a folder ({error.strerror})"
    else:
        try:
            info = os.fstat(opened)
            # RULE: a shared folder that is not ours stops the run
            if info.st_uid != os.getuid():
                found = f"belongs to user {info.st_uid}"
            # RULE: a shared folder others can write to stops the run
            elif info.st_mode & 0o022:
                found = (
                    f"can be written to by others (mode {stat.S_IMODE(info.st_mode):o}),"
                    " who may have put files in it"
                )
            else:
                if info.st_mode & 0o077:
                    # RULE: a shared folder others may only read is closed to them
                    os.fchmod(opened, 0o700)
                return folder
        finally:
            os.close(opened)
    msg = (
        f"{folder} {found}, so this run will not keep its files there. Check what it is, and if "
        f"nothing needs it, remove it (rm -r {folder}) and start the run again."
    )
    raise SystemExit(msg)


def _open_up(place: pathlib.Path) -> None:
    """Give the owner back every right on one folder or file; a link is left alone."""
    if place.is_symlink():
        return
    with contextlib.suppress(OSError):
        place.chmod(place.stat().st_mode | stat.S_IRWXU)


def _writable_again(func: Callable[[str], object], path: str, _exc: object) -> None:
    """Let a refused delete through: open up the path and its folder, then try again."""
    for place in (pathlib.Path(path).parent, pathlib.Path(path)):
        _open_up(place)
    func(path)


def remove(folder: pathlib.Path) -> None:
    """Delete a folder, even where a test took away the rights to read or delete it."""
    if not folder.exists():
        return
    # A folder a test shut (mode 0) cannot even be listed, so open every folder first;
    # the handler then covers anything that still refuses.
    for place, folders, _ in os.walk(folder):
        for name in folders:
            _open_up(pathlib.Path(place) / name)
    shutil.rmtree(folder, onexc=_writable_again)


def _stopped(signum: int, _frame: object) -> None:
    """End the run as an error would, so every ``finally`` runs."""
    # Once stopping, a second stop would cut the cleanup short, so every further stop is ignored.
    # RULE: a second stop cannot cut a run's cleanup short
    for each in STOPS:
        signal.signal(each, signal.SIG_IGN)
    if signum == signal.SIGINT:
        raise KeyboardInterrupt
    raise SystemExit(128 + signum)


def stop_cleanly() -> None:
    """Make a stop from outside end the run through its ``finally`` blocks, not on the spot."""
    # RULE: a stopped run cleans up after itself
    for each in STOPS:
        signal.signal(each, _stopped)


def only_run(folder: pathlib.Path, what: str) -> int:
    """Lock `folder` for the whole run, without waiting; the open folder holding the lock is returned.

    Two mutation runs in one checkout each take the other's switched-off line for the
    original and leave the source switched off, so a second one stops before it touches
    anything. The lock goes when the process ends, however it ends.
    """
    held = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        # RULE: a second run in the same checkout stops at once
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(held)
        msg = f"{what} is already going in {folder.parent}. Wait for it to end, or stop it, and run again."
        raise SystemExit(msg) from None
    return held


def from_this_checkout(root: pathlib.Path, python: list[str]) -> None:
    """Stop unless `python` imports the package in `root`'s ``src/`` from there.

    Two checkouts that share one environment install each other's code: a run in one
    then measures the other's, and every rule fails at once. The check runs in a
    separate process, with the same interpreter and environment the measuring will
    use, because that is where the package is imported from. A checkout with no
    package under ``src/`` is refused too: the run would measure nothing of its own.
    """
    packages = sorted(p.parent.name for p in (root / "src").glob("*/__init__.py"))
    if not packages:
        empty = (
            f"There is no package in {root / 'src'} to check, so this run would measure nothing of its own."
            " Run it from the plugin's checkout, where src/xbox_mcp/ is."
        )
        # RULE: a run refuses a checkout with no package
        raise SystemExit(empty)
    package = packages[0]
    asked = subprocess.run(  # noqa: S603 - every argument is this script's own
        [*python, "-c", f"import {package}; print({package}.__file__)"],
        cwd=root,
        # No bytecode: a cached copy of the code as it is now would hide the changes made next.
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=False,
    )
    found = asked.stdout.strip().splitlines()[-1] if asked.stdout.strip() else ""
    source = (root / "src").resolve()
    # RULE: a run refuses a package imported from another checkout
    if not found or not pathlib.Path(found).resolve().is_relative_to(source):
        msg = (
            f"{package} is imported from {found or 'nowhere'} ({asked.stderr.strip()[-200:] or 'no error'}),"
            f" not from {source}. Run `uv sync` in {root} and start again."
        )
        raise SystemExit(msg)
