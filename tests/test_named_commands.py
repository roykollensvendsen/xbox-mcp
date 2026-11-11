# SPDX-License-Identifier: GPL-3.0-or-later
"""Every command the plugin tells the owner to run works as written.

An error that names a command has to name one that runs when pasted into a
terminal: from the right folder, through uv, with flags the command line knows.
Each string the plugin can say is searched for a command; each one found must
parse, and no answer may name the bare command without the folder and uv.
"""

import ast
import pathlib
import re
import shlex

import pytest

from xbox_mcp import cli
from xbox_mcp.console import SIGN_IN_COMMAND

SOURCE = pathlib.Path(cli.__file__).parent
RUNNABLE = re.compile(r"cd ~/xbox-mcp && uv run xbox-mcp(?: [a-z-]+)+")


def _said():
    """Every string literal in the source that is not a docstring."""
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text())
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                yield path.name, node.value


def test_the_sign_in_command_runs_from_any_folder():
    assert SIGN_IN_COMMAND == "cd ~/xbox-mcp && uv run xbox-mcp sign-in"


def test_every_command_the_plugin_names_parses(monkeypatch):
    found = {match for _, text in _said() for match in RUNNABLE.findall(text)}
    found.add(SIGN_IN_COMMAND + " --other-device")
    parser = cli.parser()
    for command in sorted(found):
        argv = shlex.split(command.split("uv run xbox-mcp", 1)[1])
        monkeypatch.setattr("sys.argv", ["xbox-mcp", *argv])
        try:
            parser.parse_args(argv)
        except SystemExit:
            pytest.fail(f"{command!r} does not run as written")


def test_no_answer_names_the_bare_command():
    bare = [
        (name, text)
        for name, text in _said()
        if "xbox-mcp sign-in" in text and "uv run xbox-mcp sign-in" not in text
    ]
    assert not bare, bare
