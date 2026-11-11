# SPDX-License-Identifier: GPL-3.0-or-later
"""What each tool does, as answers the voice can read aloud.

Every answer has ``say``, read aloud as it is, and ``success`` with ``outcome``
for a program: ``done``, ``unreachable`` (the cloud did not answer),
``signed_out`` (no Microsoft sign-in), ``refused``, ``not_confirmed`` (a
command was taken, but the console's status does not show it yet), or
``working`` (still going after the voice's time).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from xbox_mcp.console import HOME_AUMID, App, Cloud, Console, ConsoleStatus, SignedOutError, UnreachableError

if TYPE_CHECKING:
    from xbox_mcp.settings import Settings

#: What the voice hears when Microsoft's cloud does not answer.
UNREACHABLE = (
    "I can't reach the Xbox through Microsoft right now, so I don't know whether it is on. "
    "Try again in a minute."
)
#: What the voice hears when there is no Microsoft sign-in.
SIGNED_OUT = (
    "The Xbox plugin is not signed in to Microsoft. "
    "The owner has to run xbox-mcp sign-in on the computer, in a browser."
)


class Refused(Exception):  # noqa: N818 - it is an answer, not a fault
    """An answer that refuses, with what the voice should say."""

    def __init__(self, say: str, outcome: str = "refused") -> None:
        """Refuse with `say`."""
        super().__init__(say)
        self.say, self.outcome = say, outcome


def answer(outcome: str, say: str, **fields: Any) -> dict[str, Any]:  # noqa: ANN401
    """One answer, in the shape every tool gives; only ``done`` is a success."""
    return {"success": outcome == "done", "outcome": outcome, "say": say, **fields}


def _running(status: ConsoleStatus, apps: list[App]) -> str | None:
    """The name of the app in focus, "Home" at the home screen, None when it is not known."""
    if status.focus == HOME_AUMID:
        return "Home"
    named = [a.name for a in apps if status.focus and a.aumid == status.focus]
    return named[0] if named else None


def describe(status: ConsoleStatus, running: str | None) -> str:
    """The console's state as a sentence."""
    if status.power == "on":
        if running == "Home":
            return "The Xbox is on, at the home screen."
        return f"The Xbox is on, running {running}." if running else "The Xbox is on, running an app."
    if status.power == "updating":
        return "The Xbox is asleep, installing an update."
    if status.power == "asleep":
        return "The Xbox is asleep."
    return (
        "The Xbox is fully off, so it can only be switched on at the console. "
        "Set its power option to Sleep so it can be woken."
    )


class XboxService:
    """The owner's console, reached through `cloud`."""

    def __init__(self, cloud: Cloud, settings: Settings) -> None:
        """Reach the console through `cloud`, with the owner's `settings`."""
        self.cloud, self.settings = cloud, settings

    async def console(self) -> Console:
        """The console the owner means: the one named in the settings, or the only one."""
        consoles = await self.cloud.consoles()
        wanted = self.settings.console
        if wanted:
            named = [c for c in consoles if c.name.casefold() == wanted.casefold()]
            if not named:
                msg = f"The account has no console called {wanted}. Check the console name in the settings."
                raise Refused(msg)
            return named[0]
        if len(consoles) == 1:
            return consoles[0]
        names = " and ".join(c.name for c in consoles) or "no console"
        msg = f"The account has {names}. Name the one to use in the settings."
        raise Refused(msg)

    async def look(self) -> tuple[Console, ConsoleStatus]:
        """The console, and what the cloud reports about it now."""
        console = await self.console()
        return console, await self.cloud.status(console.id)

    async def status(self) -> dict[str, Any]:
        """Whether the console is on, asleep or off, and what it runs."""
        try:
            console, status = await self.look()
            apps = await self.cloud.apps(console.id) if status.power == "on" else []
        except Refused as refused:
            return answer(refused.outcome, refused.say, power=None, running=None)
        except SignedOutError:
            return answer("signed_out", SIGNED_OUT, power=None, running=None)
        except UnreachableError:
            # RULE: a cloud that does not answer is not called on or off
            return answer("unreachable", UNREACHABLE, power=None, running=None)
        # RULE: an unknown power state is not called on or off
        if status.power == "unknown":
            say = "Microsoft says it does not know whether the Xbox is on. Try again in a minute."
            return answer("unreachable", say, power=None, running=None)
        running = _running(status, apps)
        power = "asleep" if status.power == "updating" else status.power
        return answer("done", describe(status, running), power=power, running=running)
