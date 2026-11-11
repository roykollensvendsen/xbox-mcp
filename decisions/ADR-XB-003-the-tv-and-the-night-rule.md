# ADR-XB-003: Ask the TV plugin about the TV, and never wake the console at night with the TV off

## Status

Accepted, 2026-11-11, within the design the owner approved for this plugin.
The interface is mister-mcp's `TvSwitch` (its ADR-MM-002), taken over.

## Context

The console is plugged into the living-room TV, and HDMI-CEC is on: waking the
console switches that TV on and to the console's input. The owner set this up
on purpose (the README's "Before you start").

That makes waking the console a way to switch a TV on. The owner's working
rule is that nothing switches a TV on at night. At night a TV coming on by
itself wakes people, and it lights a room someone may be asleep in.

The TV plugin, tv-mcp, owns the TVs. It names each TV as the owner calls it,
names the device on each of its inputs, reports a TV's screen with
`tv_status`, and changes inputs with `tv_input`. `tv_input` refuses unless the
TV's screen is on. A plugin is a child process of the bridge and cannot ask
the bridge to call another plugin's tool. mister-mcp solved the same problem
by starting tv-mcp as its own child over stdio (ADR-MM-002).

## Options considered

- **No night rule, and let CEC do what it does.** Not taken: it breaks the
  owner's rule that nothing switches a TV on at night.
- **Switch CEC off.** Not taken: the owner wants the TV to come on with the
  console in the day, and set it up so.
- **Ask the voice to check the TV first.** Not taken as the only lock: the
  voice may not do it. The plugin holds its own rules, and the bridge's
  allow-list is the second lock.
- **Read tv-mcp's settings and ask the TV ourselves.** Not taken, as in
  ADR-MM-002: a second copy of the TV's guards, and another plugin's private
  file.

## Decision

1. **The night rule.** Between 22:00 and 07:00 by the computer's own clock,
   a wake is refused at the request unless the TV plugin reports the TV's
   screen on. The window is 22 to 07 unless the owner's settings set another.
   When the TV plugin cannot be reached, does not answer in time, or reports
   anything but "on", the wake is refused and the answer says why. At night
   an unknown TV counts as off. The rule is checked when the question is
   asked, and again when the yes comes, so a question asked at 21:59 cannot
   wake the console at 22:01.
2. **By day,** the question read aloud says the TV will come on too.
3. **Sleep is never refused by the clock.** Sleeping the console switches
   nothing on. CEC is set so the console does not switch other devices off.
4. **After a confirmed wake**, once the console's own status reads it as on,
   the plugin starts the TV plugin's MCP server as a child over stdio. It calls
   `tv_status` until the screen reads on, within the TV step's own deadline,
   and then `tv_input(source=<device>, tv=<tv>)`. In the owner's settings the
   device is "Xbox" and the TV is the living-room TV, by the name tv-mcp knows
   it. The room's name lives only in those settings, never in this repository.
5. **Only `tv_status` and `tv_input` are ever called.** No TV power tool, no
   other tool. The rest of the plugin sees one small interface: `TvSwitch`,
   with `screen_on()` for the night rule and `show_xbox()` after a wake.
   When plugins can ask the bridge to call another plugin's tool, a `TvSwitch`
   that does so replaces the stdio child, and nothing else changes.
6. The child is started for one question or one wake and closed after it.
   xbox-mcp never reads tv-mcp's settings.

## Consequences

- At night the console can be woken only to a TV someone has already
  switched on, and it is never woken on a guess about the TV.
- A wake by day may switch the TV on through CEC, and the question says so
  before the owner's yes.
- Starting tv-mcp adds a few seconds to a power question and to a wake, so the
  TV step has its own deadline inside the voice's time. Past it, the console
  is still woken, and the answer says the TV was not switched.
- A change to tv-mcp's tool names or answers breaks the TV step. The tests use
  a stand-in with the fields tv-mcp documents.
- The clock is the computer's. A wrong clock moves the window with it.

## Related

[ADR-XB-001](ADR-XB-001-reach-the-xbox-through-microsofts-cloud.md),
mister-mcp's ADR-MM-002, `src/xbox_mcp/tv.py` (to come with the tools).
