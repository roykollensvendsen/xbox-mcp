<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# AGENTS.md

Instructions for coding agents working in this repository. People should
start with [README.md](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md);
this file is the same rules, for an agent.

## What this is

Voice control for an Xbox Series X|S: an MCP server that claude-voice can take
in as an optional plugin. It says whether the console is on, asleep or off and
what it runs, wakes it or puts it to sleep after the owner's spoken yes, and
starts a game from a list the owner keeps. It reaches the console through
Microsoft's cloud; why, and what it reuses, is in [`decisions/`](decisions/README.md).

## Hard rules

1. **Only the library calls on the allow-list are ever made.** No store,
   purchase, account, family, privacy, settings, friends or messages call, and
   no raw request through the library's session. The allow-list is in the
   cloud driver, and a test fails on any call outside it.
2. **Waking or sleeping the console waits for the owner's spoken yes**, through
   a single-use token that expires after 30 seconds. Nothing is retried or
   scheduled.
3. **At night the console is not woken unless the TV it is plugged into is
   already on**, because waking it switches that TV on through HDMI-CEC.
4. **A TV is never switched on or off by this plugin.** After a confirmed wake
   it may be switched to the Xbox's input, through the TV plugin's own
   `tv_input` tool.
5. **The Microsoft sign-in tokens are never printed, copied, read by an
   assistant, logged or committed.** They live in `~/.config/xbox-mcp`,
   readable by the owner alone. The owner signs in on their own, in a browser.
6. **Nothing about the owner's home is committed**: no home-network address,
   hardware address, room name, console name, account name or email address.
   Docs say "the living-room TV"; the room's own name lives only in the local
   settings. `scripts/private_data_scan.py` refuses the rest, in the
   pre-commit hook and in CI; never weaken it to make a commit pass.
7. **No link to a Claude session in a commit message.** No `Claude-Session:`
   line, whatever an assistant's own attribution says;
   `scripts/no_session_links.py` refuses it.
8. **Never touch the real console** unless the owner asks for it in this
   session. The simulator is for every try until then.
9. Every line marked `# RULE:` has a row in `scripts/mutations.toml` and a
   test named after it, and `uv run python scripts/mutate.py` must show each
   rule's own test going red.

## Commands

The gates are in [CONTRIBUTING.md](CONTRIBUTING.md#the-gates); run them all
after the last edit.
