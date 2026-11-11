# SPDX-License-Identifier: GPL-3.0-or-later
"""A pretend Xbox behind a pretend cloud, for tests and for a first try by voice.

It answers as the cloud would, never touches the network, and keeps every
command it was sent, so a test can say what reached the console.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

from xbox_mcp.console import (
    HOME_AUMID,
    App,
    Console,
    ConsoleStatus,
    Power,
    Sent,
    SignedOutError,
    UnreachableError,
)

FORZA = App("Forza Horizon 5", "9NKX70BBCDRN", "Microsoft.SunriseBaseGame_8wekyb3d8bbwe!SunriseReleaseFinal")
MINECRAFT = App("Minecraft", "9MVXMVT8ZKWC", "Microsoft.MinecraftUWPConsole_8wekyb3d8bbwe!Game")
HALO = App("Halo Infinite", "9PP5G1F0C2B6", "Microsoft.254428597CFE2_8wekyb3d8bbwe!HaloInfinite")
#: What the pretend console has installed.
INSTALLED = (FORZA, MINECRAFT, HALO)
#: The pretend console's id, in the shape the cloud uses.
CONSOLE_ID = "F4000000000000A1"


class SimulatedCloud:
    """The cloud and one console (or two), without a network."""

    def __init__(
        self,
        *,
        power: Power = "asleep",
        reachable: bool = True,
        signed_in: bool = True,
        extra_console: tuple[str, str, Power] | None = None,
    ) -> None:
        """A console asleep, unless told otherwise; `extra_console` is (id, name, power)."""
        self.power: dict[str, Power] = {CONSOLE_ID: power}
        self.names = {CONSOLE_ID: "Xbox"}
        if extra_console:
            self.power[extra_console[0]] = extra_console[2]
            self.names[extra_console[0]] = extra_console[1]
        self.focus: str | None = HOME_AUMID if power == "on" else None
        self.reachable, self.signed_in = reachable, signed_in
        self.installed: list[App] = list(INSTALLED)
        #: Every command sent, as (what, console id, product id or None).
        self.sent: list[tuple[str, str, str | None]] = []
        #: When set, a command is accepted but the console does not do it.
        self.ignores_commands = False
        #: Called when the console wakes, as HDMI-CEC switches its TV on.
        self.on_wake: Callable[[], None] | None = None

    def _answers(self) -> None:
        if not self.signed_in:
            msg = "the simulated plugin is not signed in"
            raise SignedOutError(msg)
        if not self.reachable:
            msg = "the simulated cloud does not answer"
            raise UnreachableError(msg)

    async def consoles(self) -> list[Console]:
        """The pretend consoles."""
        self._answers()
        return [Console(i, n) for i, n in self.names.items()]

    async def status(self, console_id: str) -> ConsoleStatus:
        """The pretend console's power, and the app in focus while it is on."""
        self._answers()
        power = self.power[console_id]
        return ConsoleStatus(power, self.focus if power == "on" and console_id == CONSOLE_ID else None)

    async def apps(self, console_id: str) -> list[App]:  # noqa: ARG002 - one list for every console
        """What the pretend console has installed."""
        self._answers()
        return list(self.installed)

    def _send(self, what: Literal["wake", "sleep", "launch"], console_id: str, product: str | None) -> bool:
        self._answers()
        self.sent.append((what, console_id, product))
        return not self.ignores_commands

    async def wake(self, console_id: str) -> Sent:
        """Wake a sleeping console; it comes up at its home screen."""
        if self._send("wake", console_id, None) and self.power[console_id] in ("asleep", "updating"):
            self.power[console_id], self.focus = "on", HOME_AUMID
            if self.on_wake:
                self.on_wake()
        return Sent(accepted=True)

    async def sleep(self, console_id: str) -> Sent:
        """Put a console that is on to sleep."""
        if self._send("sleep", console_id, None) and self.power[console_id] == "on":
            self.power[console_id], self.focus = "asleep", None
        return Sent(accepted=True)

    async def launch(self, console_id: str, product_id: str) -> Sent:
        """Start an installed app on a console that is on."""
        found = [a for a in self.installed if a.product_id == product_id]
        if self._send("launch", console_id, product_id) and found and self.power[console_id] == "on":
            self.focus = found[0].aumid
        return Sent(accepted=bool(found), reason=None if found else "XboxDataNotFound")
