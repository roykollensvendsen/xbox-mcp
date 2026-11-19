#!/usr/bin/env python3
"""Turn every rule off in turn and see which tests notice.

Green on a first run proves nothing: a test that has never been red tests
nothing. Where a rule is written before its test, the test is run red first.
Where a rule already exists, this is the substitute. Every rule has one row,
in a file of its own under scripts/mutations/. Run it from the repository root:

    uv run python scripts/mutate.py [--write evidence/tests/rule-mutations.md]

Only one run goes at a time in a checkout; a second stops at once. A run stopped with
Ctrl-C, SIGTERM or SIGHUP puts its file back itself. After one was killed outright
(kill -9), the next run puts it back first; to do only that:

    uv run python scripts/mutate.py --repair-only

A kill is not enough on its own. A mutation breaks whatever it breaks, and it
can break a test for an incidental reason — a documented example whose output
happened to change — leaving a rule looking guarded when nothing guards it. So
this also checks that the test named after the rule is one of the ones that went
red, and fails the run when it is not. `tests/test_rules_have_evidence.py` is
what requires that test to exist in the first place.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import tomllib

import cleanup

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: One row per file: scripts/mutations/<the row's file, whole>/<the rule's words>.toml. Every pull
#: request that added a row at the end of the one shared table collided with every
#: other; a file per row only meets another pull request adding the same rule.
ROWS = ROOT / "scripts/mutations"
#: Where the one mutated file is recorded while it is mutated. A `finally` does
#: not run when the process is killed, so without this a hard stop leaves a
#: source file switched off and the next person wondering why the suite is red.
RECOVERY = ROOT / "scripts/.mutate-recovery.json"
#: Where each suite run keeps its temporary files, one folder per run, removed when it
#: ends. Outside the checkout, where a test making a git repository of its own would
#: find this one instead; named after the checkout, so two never sweep each other's.
MUTATE_TMP = pathlib.Path(tempfile.gettempdir()) / (
    "xbox-mcp-mutate-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:8]
)


def test_name_for(rule: str) -> str:
    """The test name a rule requires: its own words, as an identifier.

    Imported by ``tests/test_rules_have_evidence.py`` so the rule is written
    once. A rule named "a spoken reply is capped" wants a test called
    ``test_a_spoken_reply_is_capped``.
    """
    words = re.sub(r"[^a-z0-9]+", "_", rule.lower())
    return "test_" + re.sub(r"_+", "_", words).strip("_")


def row_path(rule: dict) -> pathlib.PurePosixPath:
    """Where a rule's row belongs, under ``ROWS``: the file it switches off, then its words.

    A rule named "a wake is asked first" in ``src/xbox_mcp/service.py`` goes in
    ``src/xbox_mcp/service.py/a-wake-is-asked-first.toml``. The whole path, extension
    included, is the same layout in every repository and every language, and no
    two source files can share a folder.
    """
    words = re.sub(r"[^a-z0-9]+", "-", str(rule["name"]).lower()).strip("-")
    if not words:
        msg = f"the rule {rule['name']!r} has no letter or digit to file its row under; reword it"
        raise SystemExit(msg)
    return pathlib.PurePosixPath(rule["file"]) / f"{words}.toml"


def _rows_in(path: pathlib.Path) -> list[dict]:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("rule", [])


def all_rows(folder: pathlib.Path = ROWS) -> list[dict]:
    """Every row, one file per row, in path order; a rule with two rows is refused."""
    found = []
    for path in sorted(folder.rglob("*.toml")) if folder.is_dir() else []:
        found += [(f"{folder.name}/{path.relative_to(folder).as_posix()}", rule) for rule in _rows_in(path)]
    where: dict[str, str] = {}
    for place, rule in found:
        if rule["name"] in where:
            msg = (
                f"the rule {rule['name']!r} has two rows, in {where[rule['name']]} and in {place};"
                f" delete one and keep {folder.name}/{row_path(rule)}"
            )
            raise SystemExit(msg)
        where[rule["name"]] = place
    return [rule for _, rule in found]


def misfiled(folder: pathlib.Path = ROWS) -> list[str]:
    """Row files that hold other than one row, or sit where their rule does not put them."""
    found = []
    for path in sorted(folder.rglob("*.toml")) if folder.is_dir() else []:
        here = pathlib.PurePosixPath(path.relative_to(folder).as_posix())
        rows = _rows_in(path)
        if len(rows) != 1:
            found.append(f"{here} holds {len(rows)} rows; give each rule a file of its own")
        elif here != (belongs := row_path(rows[0])):
            found.append(f"{here} should be {belongs}; move it there")
    return found


def line_of(text: str, pattern: str, occurrence: int) -> int:
    """Find the one line a rule lives on, by pattern rather than by number."""
    seen = 0
    for i, line in enumerate(text.splitlines()):
        if re.search(pattern, line):
            seen += 1
            if seen == occurrence:
                return i
    msg = f"no line {occurrence} matching {pattern!r}"
    raise SystemExit(msg)


def sweep() -> None:
    """Remove what an earlier run, killed before it could clean up, left behind; never a live run's."""
    # RULE: a killed run's temporary files are swept at the next start
    cleanup.sweep(cleanup.ours(MUTATE_TMP), "run-")


def repair() -> None:
    """Put back a file an earlier run was killed in the middle of mutating."""
    if not RECOVERY.is_file():
        return
    kept = json.loads(RECOVERY.read_text())
    path = ROOT / kept["file"]
    if path.read_text() == kept["mutated"]:
        path.write_text(kept["original"])
        print(f"repaired {kept['file']}, left mutated by a run that was killed")  # noqa: T201
    else:
        print(f"{kept['file']} was left mutated and has since changed; not touching it")  # noqa: T201
    RECOVERY.unlink()


def recover() -> None:
    """Undo what an earlier run, killed part way, left behind: a mutated file and its temporary files."""
    repair()
    sweep()


def restore(path: pathlib.Path, original: str, mutated: str) -> bool:
    """Put a file back, unless somebody else changed it while the suite ran."""
    if path.read_text() != mutated:
        # Restoring now would overwrite an edit made while this was running,
        # which is a silent way to lose somebody's work.
        # RULE: a file changed during the run is never restored over
        return False  # the edit stays, and the caller names the file
    path.write_text(original)
    return True


#: Colour codes pytest writes around FAILED, which a line-anchored pattern
#: would otherwise never match.
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def run_suite() -> tuple[int, list[str]]:
    """Run the tests and give back how many failed, and which ones."""
    # Two mutations that replace equal-length text leave files of identical
    # size, and CPython validates a cached .pyc from the source's modification
    # time and size -- both at one-second resolution. Inside the same second
    # the next run would import the previous mutation's bytecode and measure
    # the wrong rule, so no bytecode is written at all.
    # RULE: two mutations never share a bytecode cache
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    held = cleanup.held_in(MUTATE_TMP, "run-")
    try:
        r = subprocess.run(  # noqa: S603  -- every argument is this script's own
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=no",
                "--color=no",
                "-p",
                "no:cacheprovider",
                f"--basetemp={held.path / 'pytest'}",  # beside the lock: pytest empties its own
            ],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
            env=env,
        )
    finally:
        # RULE: a suite run leaves no temporary files behind
        held.remove()
    # --color=no covers the usual run; stripping covers a configuration that
    # turns colour back on, which would otherwise make every kill unattributable.
    # RULE: a coloured failure line is still attributed
    plain = ANSI.sub("", r.stdout)
    m = re.search(r"(\d+) (?:failed|error)", plain)
    failed = [f.split("::")[-1].split("[")[0] for f in re.findall(r"^FAILED (\S+)", plain, re.MULTILINE)]
    # A test whose setup broke went red without testing anything; it is named, never counted as a kill.
    # RULE: a test that errors is named as an error
    errored = re.findall(r"^ERROR (\S+)", plain, re.MULTILINE)
    failed += [f"{e.split('::')[-1].split('[')[0]} (error)" for e in errored]
    return int(m.group(1)) if m else 0, failed


#: A rule's mark in the code: ``# RULE: what it says``.
MARKER = re.compile(r"^[ \t]*#\s*RULE:\s*(.+?)\s*$")


def marked() -> list[str]:
    """Every rule the code marks, in src/ and in the scripts, by its words."""
    sources = [*(ROOT / "src").rglob("*.py"), *(p for p in (ROOT / "scripts").glob("*.py") if p.is_file())]
    lines = [line for source in sorted(sources) for line in source.read_text(encoding="utf-8").splitlines()]
    return [match.group(1) for line in lines if (match := MARKER.search(line))]


def nothing_to_check(rules: list[dict]) -> int | None:
    """The exit code when there are no rows to run, or None when there are."""
    # RULE: an empty rule table is not a crash
    # A scaffolded repository starts with no rules and no rows.
    if not rules and not marked():
        print(f"no rules in {ROWS.relative_to(ROOT)} yet; add one file per rule the code enforces")  # noqa: T201
        return 0
    # RULE: rules with no rows are a failure not a pass
    # A check that passes with nothing to check lets every rule go unproven: a rows folder
    # moved or emptied by mistake would leave the run green.
    if not rules:
        print(  # noqa: T201
            f"nothing to switch off: the code marks rules, but {ROWS.relative_to(ROOT)} has no rows."
            " Restore it from main (git checkout origin/main -- scripts/mutations), or add a row per rule."
        )
        return 1
    return None


def mutate_all(write: pathlib.Path | None = None) -> int:
    """Mutate every rule in turn, restore the tree, and report."""
    recover()
    rules = all_rows(ROWS)
    if (empty := nothing_to_check(rules)) is not None:
        return empty
    results = []
    lost: list[str] = []
    bad_rows: list[str] = []
    standing: tuple[pathlib.Path, str, str] | None = None
    try:
        for rule in rules:
            path = ROOT / rule["file"]
            # Read each file as it is now, not as it was when the run began: a
            # bulk snapshot taken up front is restored over anything edited
            # since, and losing an edit that way costs more than the run.
            text = path.read_text()
            mutated = switched_off(text, rule)
            why = broken(rule["file"], mutated)
            # RULE: a row that breaks the code is reported as broken
            if why:
                bad_rows.append(rule["name"])
                print(f"BROKEN   {rule['name']}: the replacement is not valid code ({why})", flush=True)  # noqa: T201
                continue
            RECOVERY.write_text(json.dumps({"file": rule["file"], "original": text, "mutated": mutated}))
            path.write_text(mutated)
            standing = (path, text, mutated)
            # Sent at once, not held until the run ends: in CI the output is a pipe, so a
            # run that hangs would otherwise leave no word of which rule it hung on.
            # RULE: a mutation run names each rule before it tests it
            print(f"testing  {rule['name']}", flush=True)  # noqa: T201
            count, failed = run_suite()
            if not restore(path, text, mutated):
                lost.append(rule["file"])
            standing = None
            RECOVERY.unlink(missing_ok=True)
            # A kill by some other test is not evidence about this rule.
            own = test_name_for(rule["name"])
            by_its_own = own in failed
            results.append((rule["name"], count, own, by_its_own))
            verdict = "KILLED  " if by_its_own else ("BY LUCK " if count else "SURVIVED")
            # A kill that can flip between runs has to say what flipped it.
            # RULE: a kill by another test names the tests that went red
            red = f", red: {', '.join(failed)}" if count and not by_its_own else ""
            print(f"{verdict} {rule['name']}: {count}{red}", flush=True)  # noqa: T201
    finally:
        if standing is not None:
            restore(*standing)
        RECOVERY.unlink(missing_ok=True)

    return _summary(results, bad_rows, lost, write)


def _summary(
    results: list[tuple[str, int, str, bool]],
    bad_rows: list[str],
    lost: list[str],
    write: pathlib.Path | None,
) -> int:
    """Say what the run found, write the evidence page if asked, and give the exit status."""
    survived = [name for name, count, _, _ in results if count == 0]
    lucky = [name for name, count, _, own in results if count and not own]
    proven = len(results) - len(survived) - len(lucky)
    print(f"\n{proven}/{len(results)} rules turned off the test that names them")  # noqa: T201
    if survived:
        print("SURVIVED, so nothing protects them:", ", ".join(survived))  # noqa: T201
    if lucky:
        print("KILLED BY ANOTHER TEST, so the evidence is not about them:", ", ".join(lucky))  # noqa: T201
    if bad_rows:
        print("BROKEN ROW, so nothing was tested; make each a whole valid line:", ", ".join(bad_rows))  # noqa: T201
    if lost:
        print("CHANGED WHILE MUTATED, so left as they are:", ", ".join(sorted(set(lost))))  # noqa: T201
        print("Edit the tree while this runs and the result is not about your code.")  # noqa: T201
    if write:
        write.write_text(report(results, survived, lucky, bad_rows))
    return 1 if survived or lucky or lost or bad_rows else 0


def switched_off(text: str, rule: dict) -> str:
    """The file's text with the rule's whole line replaced, keeping its indentation."""
    lines = text.splitlines()
    i = line_of(text, rule["find"], rule.get("occurrence", 1))
    # Keep the line's own indentation: a replacement that carries its own
    # would have to be kept in step with the source, and silently would not be.
    indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
    lines[i] = indent + rule["replace"].strip()
    return "\n".join(lines) + "\n"


def broken(file: str, mutated: str) -> str | None:
    """Why a mutated Python file no longer compiles, or None when it does.

    A replacement cut off mid-expression stops the module importing, so every
    test in its files errors: the run would count the rule as caught when the
    row itself is wrong.
    """
    if not file.endswith(".py"):
        return None
    try:
        compile(mutated, file, "exec")
    except SyntaxError as e:
        return f"line {e.lineno}: {e.msg}"
    return None


def report(
    results: list[tuple[str, int, str, bool]],
    survived: list[str],
    lucky: list[str],
    bad_rows: list[str] = (),
) -> str:
    """Render the run as the evidence page."""
    rows = "\n".join(
        f"| {name} | {count} | `{own}` | {'yes' if by_own else 'NO'} |"
        for name, count, own, by_own in results
    )
    proven = len(results) - len(survived) - len(lucky)
    verdict = (
        f"**Result: {proven} of {len(results)} rules turned off the test that names them.**"
        + (" None survived." if not survived else f" Survived: {', '.join(survived)}.")
        + ("" if not lucky else f" Killed only by other tests: {', '.join(lucky)}.")
        + ("" if not bad_rows else f" Broken rows, so nothing was tested: {', '.join(bad_rows)}.")
    )
    return f"""# Every rule, turned off in turn

*Run {dt.datetime.now(dt.UTC).date()} by `python scripts/mutate.py --write
evidence/tests/rule-mutations.md`, which is how to repeat it. The script
restores every file it touched before it exits.*

Green on a first run proves nothing: a test that has never been red tests
nothing. Where a rule is written before its test, the test is run red first.
Where a rule already exists, this is the substitute. Each rule is disabled in
turn, by replacing its one line with something that can never hold, and the
suite is run. A rule whose removal breaks no test is a rule nothing protects.

{verdict}

| Rule turned off | Tests that went red | The test that names it | Did that one go red? |
|---|---|---|---|
{rows}

## What this does not prove

That the rules are the right rules, or that a rule catches every case of what
it names. It proves each rule is load-bearing: switch it off and a named test
says so. The last column is why that is worth more than a count: a mutation can
break a test for an incidental reason, and a `NO` there fails this script even
though something went red. A new rule follows the order in `CONTRIBUTING.md`
instead, a failing test first, and gets a row of its own under `scripts/mutations/`,
which `tests/test_rules_have_evidence.py` requires.
"""


def main() -> int:
    """Mutate every rule in turn, and leave the temporary folder as it was found."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", type=pathlib.Path, help="write the evidence table here")
    parser.add_argument(
        "--repair-only", action="store_true", help="put back a file a killed run left mutated, and stop"
    )
    args = parser.parse_args()
    cleanup.stop_cleanly()
    # RULE: only one mutation run goes at a time in a checkout
    checkout = cleanup.only_run(RECOVERY.parent, "A mutation run")
    try:
        if args.repair_only:
            # Under the lock, so never the file of a run still going.
            # RULE: a repair-only run puts back a mutated file and does nothing else
            repair()
            return 0
        cleanup.from_this_checkout(ROOT, [sys.executable])
        return mutate_all(args.write)
    finally:
        # Only this run's own folders were removed; the shared one goes once it is empty,
        # and never while another run of this checkout is using it.
        # RULE: a mutation run leaves nothing in the temporary folder
        with contextlib.suppress(OSError):
            MUTATE_TMP.rmdir()
        os.close(checkout)


if __name__ == "__main__":
    sys.exit(main())
