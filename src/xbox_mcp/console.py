# SPDX-License-Identifier: GPL-3.0-or-later
"""What the plugin needs from Microsoft's cloud, without knowing how it is reached.

The cloud driver (``xbox_mcp.drivers.cloud``) and the simulator both give this
interface; the rest of the plugin sees nothing else. Every method here is one
call on the allow-list in ADR-XB-001, and there is no method for anything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

#: The console's power, as the plugin speaks of it. "updating" is asleep with an update running.
Power = Literal["on", "asleep", "updating", "off", "unknown"]

#: The command that signs the plugin in to Microsoft, as the owner pastes it into a terminal.
SIGN_IN_COMMAND = "cd ~/xbox-mcp && uv run xbox-mcp sign-in"

#: The app in focus on the home screen, as the cloud names it.
HOME_AUMID = "Microsoft.Xbox.Dashboard_8wekyb3d8bbwe!Xbox.Dashboard.Application"


@dataclass(frozen=True)
class Console:
    """A console on the owner's Microsoft account."""

    id: str
    name: str


@dataclass(frozen=True)
class ConsoleStatus:
    """What the cloud reports about a console now."""

    power: Power
    focus: str | None = None  # the app in focus, by its AUMID


@dataclass(frozen=True)
class App:
    """An app or game installed on the console."""

    name: str
    product_id: str
    aumid: str | None = None


@dataclass(frozen=True)
class Sent:
    """Whether the cloud took a command; taking it is not the console doing it."""

    accepted: bool
    reason: str | None = None


class UnreachableError(Exception):
    """Microsoft's cloud did not answer, or answered with an error."""

    def __init__(self, message: str, *, say: str | None = None) -> None:
        """An error whose `say`, when given, tells the owner what is wrong and what to do."""
        super().__init__(message)
        self.say = say


class SignedOutError(UnreachableError):
    """There is no Microsoft sign-in, or it has run out."""


class Cloud(Protocol):
    """The console calls the plugin may make, and no others."""

    async def consoles(self) -> list[Console]:
        """The consoles on the owner's account."""
        ...

    async def status(self, console_id: str) -> ConsoleStatus:
        """The console's power and the app in focus."""
        ...

    async def apps(self, console_id: str) -> list[App]:
        """The apps and games installed on the console."""
        ...

    async def wake(self, console_id: str) -> Sent:
        """Ask the console to wake from sleep."""
        ...

    async def sleep(self, console_id: str) -> Sent:
        """Ask the console to go to sleep."""
        ...

    async def launch(self, console_id: str, product_id: str) -> Sent:
        """Ask the console to start an installed app by its store product id."""
        ...

    async def home(self, console_id: str) -> Sent:
        """Ask the console to show Home; the app in front stays paused in the background."""
        ...
