# xbox-mcp

Control an Xbox Series X|S by voice: an MCP server and claude-voice plugin.

<!-- not run: there is nothing to run until the walking skeleton exists -->
```bash
# Replace this block with what xbox-mcp actually does, as a ```console block
# whose output is what the command really prints. The suite runs those blocks
# and compares them, so an example here cannot quietly stop being true.
```

## The gates

Every one of these runs in continuous integration, and all of them run here:

```console
$ uv run ruff check .
All checks passed!
```

<!-- not run: the workflow runs each of these; nesting them here would run the suite inside itself -->
```bash
uv run ruff format --check .   # layout
uv run mypy src                # types
uv run pytest -q               # behaviour
python scripts/mutate.py       # that each rule's own test can fail
```

`CONTRIBUTING.md` says what each one is for, and
[`decisions/`](decisions/README.md) records the choices that shaped them,
including how a change is made here.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
