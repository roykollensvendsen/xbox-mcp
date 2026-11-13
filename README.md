# xbox-mcp

Control an Xbox Series X|S by voice: an MCP server and claude-voice plugin.

It is a small MCP server over stdio, and an optional plugin for claude-voice.
It reaches the console through Microsoft's cloud with
[python-xbox](https://github.com/tr4nt0r/python-xbox), and makes only the
library calls listed in
[ADR-XB-001](decisions/ADR-XB-001-reach-the-xbox-through-microsofts-cloud.md).
No tool touches the store, purchases, the account, family, privacy, settings,
friends or messages.

## Try it without an Xbox

`--simulate` answers from a pretend Xbox, and says so:

```console
$ uv run xbox-mcp status --simulate
Simulated: The Xbox is asleep.
```

The server itself, as a voice session starts it; it ends when its input does:

```console
$ uv run xbox-mcp serve --simulate < /dev/null
```

## The tools

| Tool | Does | Changes anything |
|---|---|---|
| `xbox_status` | Whether the Xbox is on, asleep or off, and the game or app it runs | No |
| `xbox_power_request` | Asks whether to wake the Xbox or put it to sleep, and hands out a token; sends nothing | No |
| `xbox_power_confirm` | With that token, within 30 seconds and once only: wakes or sleeps the Xbox, reads it back, and after a wake switches its TV to the Xbox | Wakes or sleeps the Xbox |
| `xbox_launch` | Starts a game from the owner's own list, only if it is installed and the Xbox is on, and reads it back. When another game is in front, it asks first, naming it, and returns a token | Starts a game |
| `xbox_launch_confirm` | With that token, within 30 seconds and once only: checks the same game is still in front, then starts the new one and reads it back | Starts a game |
| `xbox_home_request` | Names the listed game in front and asks whether to send the Xbox to Home; sends nothing, returns a token | Nothing |
| `xbox_home_confirm` | With that token, within 30 seconds and once only: checks the same game is still in front, sends the Xbox to Home and reads it back. The game stays paused in the background, since Microsoft's cloud cannot close it | Shows Home |

Every answer has `say`, read aloud as it is, and `success` with `outcome`
for a program: `done`, `asked` (a power question waits for the owner's yes), `unreachable` (Microsoft's cloud did not answer),
`signed_out` (no Microsoft sign-in yet), `refused`, `not_confirmed` (a command
was taken, but the console's status does not show it yet), or `working` (still
going after the voice's time, and carrying on). Every answer that does not
succeed also says what to do next.

Which actions are risky is the owner's call (the plugin standard's rule 7).
Waking and sleeping are: each is a question read aloud, and then a confirm
with a single-use token after the owner's spoken yes. When Microsoft's answer
to a command is lost, the command may still have arrived, so the yes is used up
and the plugin reads the Xbox back instead of sending it again. Between 22:00 and 07:00
a wake is refused unless the TV the Xbox is plugged into is already on,
because waking the Xbox switches that TV on through HDMI-CEC. By day, the
question says the TV will come on too.

## Sign in to Microsoft

The plugin reaches the Xbox through Microsoft's cloud, so it needs the owner's
Microsoft sign-in once. In a terminal on the computer the plugin runs on:

<!-- not run: it opens a browser and waits for the owner to sign in -->
```bash
cd ~/xbox-mcp && uv run xbox-mcp sign-in
```

It prints a link to Microsoft's sign-in page and opens it in the browser. Sign
in with the Microsoft account the Xbox uses, and allow the access it asks for.
The page then sends the browser back to this computer, and the terminal says
"Signed in" and names the consoles on the account.

If another program already uses port 8080, that program would see the
sign-in code on its way back, so the command stops before it shows the link
and says to sign in from a phone instead:

<!-- not run: it waits for the owner to sign in -->
```bash
cd ~/xbox-mcp && uv run xbox-mcp sign-in --other-device
```

This prints the link without opening a browser here. Open it on the phone and
sign in. The phone then lands on an error page (it cannot connect, or says
404). That is expected, since nothing listens there. Copy the WHOLE address
from the phone's address bar, and paste it where the terminal says "Paste the
whole address". The terminal does not show it.

The laptop's own browser works only when port 8080 is free; then the browser
comes back to the terminal by itself, with nothing to paste. The decision and its risk are in
[ADR-XB-001](decisions/ADR-XB-001-reach-the-xbox-through-microsofts-cloud.md).

The sign-in link carries a PKCE challenge, and the code is redeemed only with
its verifier, which never leaves the computer. A code that another program sees
on its way back cannot be used without it, where Microsoft checks the
challenge. The sign-in is kept in
`~/.config/xbox-mcp/tokens.json`, readable by you alone, and is never shown,
logged or sent to the voice. No Azure app registration is needed
([ADR-XB-001](decisions/ADR-XB-001-reach-the-xbox-through-microsofts-cloud.md)).

```console
$ uv run xbox-mcp sign-in --help
usage: xbox-mcp [-h] [--simulate] [--settings-file SETTINGS_FILE]
                [--other-device]
                {serve,status,sign-in}

Command line for xbox-mcp: serve the tools to a voice session, or ask the
Xbox.

positional arguments:
  {serve,status,sign-in}

options:
  -h, --help            show this help message and exit
  --simulate            a pretend Xbox, no network
  --settings-file SETTINGS_FILE
                        instead of ~/.config/xbox-mcp/config.toml
  --other-device        sign-in: open the link on another device, then paste
```

## When it breaks

When xbox-mcp itself fails, the voice hears that it went wrong and where the
reason is written: `~/.local/state/xbox-mcp/xbox-mcp.log`, readable by you
alone (set `XBOX_MCP_LOG` for another file). The same lines go to stderr, which
the voice bridge keeps in its own log. The sign-in tokens are never written
there.

## Settings

In `~/.config/xbox-mcp/config.toml`, never in this repository:

<!-- not run: a settings file, not a command -->
```toml
[launch]                      # the only games xbox_launch may start
titles = ["Forza Horizon 5", "Minecraft"]

[tv]
command = ["uv", "run", "--directory", "~/tv-mcp", "tv-mcp", "serve"]
name = "the living-room TV"   # the TV, as the TV plugin knows it
device = "Xbox"               # the device on its input, as the TV plugin knows it

[night]                       # when a wake needs that TV already on
starts = 22
ends = 7
```

The TV is reached through the TV plugin's own tools
([tv-mcp](https://github.com/roykollensvendsen/tv-mcp)): only `tv_status` and
`tv_input`, never a power tool.

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
