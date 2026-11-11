# ADR-XB-001: Reach the Xbox through Microsoft's cloud, with python-xbox and a fixed allow-list

## Status

Accepted, 2026-11-11, within the design the owner approved for this plugin
(the cloud first; local SmartGlass only if a test on the console shows it still
answers). The library choice below was agreed with the manager session the
same day.

## Context

The plugin has to say whether the console is on, asleep or off and what it
runs, wake it or put it to sleep, and start an installed game. Read at their
sources on 2026-11-11:

- **The Xbox cloud ("SmartGlass" commands through `xccs.xboxlive.com`).** The
  console keeps a connection to Microsoft while it sleeps in Sleep mode
  (not "Shutdown (energy saving)") with remote features on. The cloud answers
  the console's power state, the app in focus and the installed apps, and
  forwards wake, sleep and launch. It needs a Microsoft sign-in by the owner.
- **xbox-webapi** (github.com/OpenXbox/xbox-webapi-python, MIT). The OpenXbox
  library for that cloud. Last release 2.1.0 on 2023-11-25, and nothing has
  been released since, though the repository still sees commits.
- **python-xbox** (github.com/tr4nt0r/python-xbox, MIT, import name
  `pythonxbox`). A maintained fork of xbox-webapi, with the same API under a new
  package name. Release 0.4.0 came out on 2026-10-18. Home Assistant's Xbox
  integration moved to it and pins `python-xbox==0.4.0`
  (homeassistant/components/xbox/manifest.json).
- **Local SmartGlass** (xbox-smartglass-core, UDP 5050 on the home network).
  Archived on 2021-10-20, last released 2020-11-02. It was documented for the
  Xbox One, and nothing shows that a Series X|S still answers it.

How Home Assistant uses the library (components/xbox, read on the `dev`
branch):

- It builds one `XboxLiveClient` from an `AuthenticationManager`.
- It polls `smartglass.get_console_status` every 10 seconds.
- It counts the console as on only when `power_state` is `On`.
- It wakes with `smartglass.wake_up`, sleeps with `smartglass.turn_off` and
  starts an app with `smartglass.launch_app(console_id, one_store_product_id)`.
- It treats a command whose `status.error_code` is not `OK` as failed.

It signs in through Home Assistant Cloud's account linking, or else through an
app the user registers in Azure.

The library's client has far more than the console. It has providers for
messages, people (friends), account (gamertag changes), presence, catalog
(the store), achievements, screen captures and more, plus the raw HTTP
session. Several of these change things in the owner's account.

## Options considered

- **Local SmartGlass.** Not taken now. It is archived, unproven on a Series
  console, and would need a second protocol stack. It stays on the deferred
  list, with its trigger.
- **xbox-webapi, the original.** Not taken: no release in three years, while
  its fork is maintained and is what Home Assistant runs today. The APIs are
  the same, so going back is one import path.
- **Home Assistant as a go-between** (its `media_player` and `remote`). Not
  taken: it would put a home-automation server and its tokens between the voice
  and the console, for three calls the library makes directly.
- **Our own HTTP client for the cloud.** Not taken: the sign-in chain (OAuth,
  then XAU, then XSTS tokens, signed requests) is exactly what the library is
  for.
- **Do nothing.** Not taken: the owner asked for voice control.

## Decision

The plugin uses **python-xbox, pinned to 0.4.0**. Exactly one module, the
cloud driver, imports it (`tests/test_layers.py` refuses any other import).
That driver calls the library only through an allow-list, and a test fails on
any call outside it. The allow-list is:

- `smartglass.get_console_list`, to find the console the owner names;
- `smartglass.get_console_status`, for its power state and the app in focus;
- `smartglass.get_installed_apps`, to find a title on the owner's list
  and to name what is running;
- `smartglass.wake_up` and `smartglass.turn_off`, only after a confirmed yes;
- `smartglass.launch_app`, only for an installed title on the owner's list;
- `AuthenticationManager.refresh_tokens`, plus `generate_authorization_url`
  and `request_tokens` in the owner's own sign-in command.

Nothing else is called. That rules out every store, purchase, account, family,
privacy, settings, friends, messages, presence and achievements call, the
other console commands (`press_button`, `insert_text`, `volume`, `reboot`,
...) and the raw session.

**The sign-in uses the library's own public client id**
(`388ea51c-0b25-4029-aae2-17df49d23905`, registered by OpenXbox as a desktop
app, so it needs no secret). Its redirect is `http://localhost:8080/auth/callback`.

- **No Azure app registration is needed** while Microsoft accepts that id.
- Registering our own would need one. Since June 2024 Microsoft requires every
  new app to live in a directory (tenant), and a personal account can no longer
  own one outside it. For a private person that means signing up to Azure,
  which may ask for a payment card. That step is the owner's to decide, so it
  is not taken unless the public id stops working, and then only after asking.

The owner signs in on their own, in a browser, with `xbox-mcp sign-in`. The tokens
go in `~/.config/xbox-mcp/tokens.json`, readable by the owner alone (mode
600). The plugin refuses the file when others can read it. The tokens never pass through
the bridge, a message, a log or this repository.

### Signing in when another program holds port 8080

Microsoft sends the sign-in back to `http://localhost:8080/auth/callback`,
the only address the public client id is known to have. The code in that
address can be redeemed without any secret by whoever holds it first. On the
owner's computer, a program owned by root already listens on port 8080, so it
would see the code.

Each sign-in carries a PKCE challenge, and the code is redeemed only with its
verifier, which never leaves the computer. Whether Microsoft's older sign-in
endpoint enforces the challenge for this client id has not been shown, and
cannot be shown without a real sign-in. So a code that reaches another
program is treated as redeemable:

- When port 8080 is held, `xbox-mcp sign-in` stops before it shows the link,
  and says to run it with `--other-device`. No code is asked for.
- With `--other-device`, the owner signs in on a phone, where nothing listens
  on port 8080, and pastes the address the phone ends on. The terminal does
  not show it. The weak spot moves to however the owner carries the address
  from the phone; it works once, within minutes, and only with the verifier.
- A device-code sign-in would avoid the redirect altogether, but Microsoft
  refuses it for this client id ("must be marked as 'mobile'"), as asked on
  2026-11-11.

## Consequences

- **Good:** one maintained library, the same calls Home Assistant has run for
  years, and a short allow-list that a test enforces.
- **Good:** no Azure account, and no billing, while the public client id
  works.
- **Worse:** the console is reached through Microsoft. When the cloud is slow
  or down, the plugin says it does not know, and does nothing.
- **Worse:** the public client id belongs to OpenXbox. If Microsoft or
  OpenXbox withdraws it, sign-in stops working until the owner decides about
  an app registration.
- **Worse:** a command the cloud accepts is not yet done. Every answer says
  "not confirmed" until the console's own status reads back the change.
- **Worse:** on the owner's computer, signing in needs a phone, until the
  program on port 8080 is stopped for the minute the sign-in takes.
- **Neutral:** the library's pydantic models change between releases. The pin
  is moved deliberately, with the allow-list test re-read.

## Related

ADR-XB-003 (the TV), `decisions/deferred.md` (local SmartGlass),
`src/xbox_mcp/drivers/cloud.py`.
