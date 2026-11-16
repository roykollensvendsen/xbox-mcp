# SPDX-License-Identifier: GPL-3.0-or-later
"""The mutation tool's own rules, each with the test its row switches it off against.

These run the tool's functions in this process, with the test run it starts replaced by
a stand-in that answers as pytest would, so they need no tiny project of their own.
"""

import importlib.util
import pathlib
import subprocess

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
