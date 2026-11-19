# SPDX-License-Identifier: GPL-3.0-or-later
"""The mutation tool's own rules, each with the test its row switches it off against.

These run the tool's functions in this process, with the test run it starts replaced by
a stand-in that answers as pytest would, so they need no tiny project of their own.
"""

import importlib.util
import pathlib
import subprocess
import types

ROOT = pathlib.Path(__file__).parent.parent


def _mutate(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mutate_rules", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "ROWS", tmp_path / "rows")
    monkeypatch.setattr(module, "RECOVERY", tmp_path / "recovery.json")
    monkeypatch.setattr(module, "MUTATE_TMP", tmp_path / "mutate-tmp")
    return module


def _suite_answers(module, monkeypatch, stdout):
    """Stand in for the suite: record how it was started, and answer with `stdout`."""
    started = {}

    def run(command, **kwargs):
        started.update(kwargs, command=command)
        return subprocess.CompletedProcess(command, 1, stdout, "")

    monkeypatch.setattr(module.subprocess, "run", run)
    return started


def test_a_file_changed_during_the_run_is_never_restored_over(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    source = tmp_path / "source.py"
    source.write_text("edited while the run went\n")
    assert not mutate.restore(source, "original\n", "mutated\n")
    assert source.read_text() == "edited while the run went\n"


def test_two_mutations_never_share_a_bytecode_cache(tmp_path, monkeypatch):
    # A mutation run starts every suite with this already set, which would hide its absence here.
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    mutate = _mutate(tmp_path, monkeypatch)
    started = _suite_answers(mutate, monkeypatch, "1 passed\n")
    mutate.run_suite()
    assert started["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_a_coloured_failure_line_is_still_attributed(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    coloured = (
        "\x1b[31mFAILED\x1b[0m tests/test_x.py::test_the_rule[case] - assert\n\x1b[31m1 failed\x1b[0m\n"
    )
    _suite_answers(mutate, monkeypatch, coloured)
    assert mutate.run_suite() == (1, ["test_the_rule"])


def test_an_empty_rule_table_is_not_a_crash(tmp_path, monkeypatch, capsys):
    mutate = _mutate(tmp_path, monkeypatch)
    (tmp_path / "rows").mkdir()
    assert mutate.mutate_all() == 0
    assert "no rules" in capsys.readouterr().out


def test_rules_with_no_rows_are_a_failure_not_a_pass(tmp_path, monkeypatch, capsys):
    mutate = _mutate(tmp_path, monkeypatch)
    (tmp_path / "rows").mkdir()  # emptied, or moved, by mistake
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ruled.py").write_text("def one():\n    # RULE: one is one\n    return 1\n")
    assert mutate.mutate_all() == 1
    said = capsys.readouterr().out
    assert "has no rows" in said
    assert "git checkout origin/main -- scripts/mutations" in said


class _Pipe:
    """Output that, like a pipe in CI, shows only what has been flushed."""

    def __init__(self):
        self.held, self.shown = "", ""

    def write(self, text):
        self.held += text
        return len(text)

    def flush(self):
        self.shown, self.held = self.shown + self.held, ""


def test_a_mutation_run_names_each_rule_before_it_tests_it(tmp_path, monkeypatch):
    """The name must be out of the process, not in its buffer, by the time the suite starts.

    In CI the output is a pipe, which Python holds back until it is full. A run that hung on
    one rule left nothing in the log to say which, and its result line has to get out as well.
    """
    mutate = _mutate(tmp_path, monkeypatch)
    (tmp_path / "rows").mkdir()
    (tmp_path / "rows" / "a-guard-holds.toml").write_text(
        '[[rule]]\nname = "a guard holds"\nfile = "rule.py"\nfind = "pass"\nreplace = "x = 1"\n'
    )
    (tmp_path / "rule.py").write_text("if guarded:\n    pass\n")
    pipe, seen = _Pipe(), []
    monkeypatch.setattr(mutate.sys, "stdout", pipe)

    def suite():
        seen.append(pipe.shown)
        return 1, ["test_a_guard_holds"]

    monkeypatch.setattr(mutate, "run_suite", suite)
    mutate.mutate_all()
    assert "testing  a guard holds" in seen[0]
    assert "KILLED   a guard holds: 1" in pipe.shown


def test_a_test_that_errors_is_named_as_an_error(tmp_path, monkeypatch):
    """A test whose setup broke went red without testing anything, and has to be told apart from a kill."""
    mutate = _mutate(tmp_path, monkeypatch)
    _suite_answers(mutate, monkeypatch, "ERROR tests/test_x.py::test_the_rule - OSError\n1 error\n")
    assert mutate.run_suite() == (1, ["test_the_rule (error)"])


def test_a_kill_by_another_test_names_the_tests_that_went_red(tmp_path, monkeypatch, capsys):
    """A rule killed by a neighbour can flip between runs; the run has to say which test it was."""
    mutate = _mutate(tmp_path, monkeypatch)
    (tmp_path / "rows").mkdir()
    (tmp_path / "rows" / "a-guard-holds.toml").write_text(
        '[[rule]]\nname = "a guard holds"\nfile = "rule.py"\nfind = "pass"\nreplace = "x = 1"\n'
    )
    (tmp_path / "rule.py").write_text("if guarded:\n    pass\n")
    monkeypatch.setattr(mutate, "run_suite", lambda: (2, ["test_a_neighbour", "test_the_rule (error)"]))
    assert mutate.mutate_all() == 1
    said = capsys.readouterr().out
    assert "BY LUCK  a guard holds: 2 in " in said
    assert " s, red: test_a_neighbour, test_the_rule (error)\n" in said


def test_a_row_that_breaks_the_code_is_reported_as_broken(tmp_path, monkeypatch, capsys):
    """A replacement that does not compile fails every test, which would pass for a kill.

    So it is named as the row's fault, nothing is run for it, the run fails, and the
    written evidence lists it too.
    """
    mutate = _mutate(tmp_path, monkeypatch)
    (tmp_path / "rows").mkdir()
    (tmp_path / "rows" / "a-rule.toml").write_text(
        '[[rule]]\nname = "a rule"\nfile = "rule.py"\nfind = "x = 1"\nreplace = "x = ("\n'
    )
    (tmp_path / "rule.py").write_text("x = 1\n")
    suites = []
    monkeypatch.setattr(mutate, "run_suite", lambda: suites.append(1) or (1, ["test_a_rule"]))
    page = tmp_path / "evidence.md"
    assert mutate.mutate_all(page) == 1
    said = capsys.readouterr().out
    assert "BROKEN   a rule: the replacement is not valid code" in said
    assert "KILLED" not in said
    assert suites == []
    assert (tmp_path / "rule.py").read_text() == "x = 1\n"
    written = page.read_text()
    assert "Broken rows, so nothing was tested: a rule." in written
    # Nothing ran for a broken row, so the page can't say none survived.
    assert "None survived" not in written


def test_a_broken_row_still_leaves_the_survivors_named(tmp_path, monkeypatch):
    """Dropping "none survived" beside a broken row must not drop the rules that did survive."""
    mutate = _mutate(tmp_path, monkeypatch)
    page = mutate.report([("kept", 0, "test_kept", False)], ["kept"], [], ["cut short"])
    assert "Survived: kept." in page
    assert "Broken rows, so nothing was tested: cut short." in page


def test_a_mutation_run_says_how_long_each_rule_took(tmp_path, monkeypatch, capsys):
    """Each result line says the seconds its suite took, so a slow rule stands out."""
    mutate = _mutate(tmp_path, monkeypatch)
    (tmp_path / "rows").mkdir()
    (tmp_path / "rows" / "a-guard-holds.toml").write_text(
        '[[rule]]\nname = "a guard holds"\nfile = "rule.py"\nfind = "pass"\nreplace = "x = 1"\n'
    )
    (tmp_path / "rule.py").write_text("if guarded:\n    pass\n")
    monkeypatch.setattr(mutate, "run_suite", lambda: (1, ["test_a_guard_holds"]))
    clock = iter([10.0, 52.4])
    monkeypatch.setattr(mutate, "time", types.SimpleNamespace(monotonic=lambda: next(clock)))
    mutate.mutate_all()
    assert "KILLED   a guard holds: 1 in 42 s\n" in capsys.readouterr().out


def test_a_clean_run_still_says_none_survived(tmp_path, monkeypatch):
    """With no survivor and no broken row, the page says so in words."""
    mutate = _mutate(tmp_path, monkeypatch)
    page = mutate.report([("kept", 1, "test_kept", True)], [], [], [])
    assert "None survived." in page
