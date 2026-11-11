# xbox-mcp

Control an Xbox Series X|S by voice: an MCP server and claude-voice plugin.

It is a small MCP server over stdio, and an optional plugin for claude-voice.
It reaches the console through Microsoft's cloud with
[python-xbox](https://github.com/tr4nt0r/python-xbox), and makes only the
library calls listed in
[ADR-XB-001](decisions/ADR-XB-001-reach-the-xbox-through-microsofts-cloud.md).
No tool touches the store, purchases, the account, family, privacy, settings,
friends or messages.

## Before you start

The console has to be set up once, by hand. The Norwegian menu names are in
brackets.

1. **Settings → General → Power options** (Strømalternativer): choose
   **Sleep** (Hvilemodus), not "Full shutdown / energy saving". Otherwise
   neither the network nor HDMI-CEC can wake it. Auto-off after one hour (the
   default) is fine: in Sleep mode the console only goes to sleep.
2. **Settings → Devices & connections** (Enheter og tilkoblinger) **→ Remote
   features** (Eksterne funksjoner) **→ Enable remote features**. Set access
   to "Only from profiles signed in on this Xbox".
3. **Settings → TV & display options → Device control**: enable HDMI-CEC.
   Turn on:
   - Play To;
   - the console turns other devices on;
   - other devices can turn the console off;
   - volume commands;
   - switching the TV's input with the controller.

   Leave "the console turns off other devices" off.

   To check it, restart the console: the TV should come on and show the Xbox.

With HDMI-CEC on, waking the console switches its TV on. That is why the
plugin refuses to wake it at night unless that TV is already on
([ADR-XB-003](decisions/ADR-XB-003-the-tv-and-the-night-rule.md)).

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
