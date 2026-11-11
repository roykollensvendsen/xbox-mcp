# SPDX-License-Identifier: GPL-3.0-or-later
"""What each tool does, as answers the voice can read aloud.

Every answer has ``say``, read aloud as it is, and ``success`` with ``outcome``
for a program: ``done``, ``unreachable`` (the cloud did not answer),
``signed_out`` (no Microsoft sign-in), ``refused``, ``not_confirmed`` (a
command was taken, but the console's status does not show it yet), or
``working`` (still going after the voice's time).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from xbox_mcp.console import (
    HOME_AUMID,
    App,
    Cloud,
    Console,
    ConsoleStatus,
    Power,
    SignedOutError,
    UnreachableError,
)
from xbox_mcp.power import CONFIRM_WITHIN, Action, Questions, is_night, local_hour
from xbox_mcp.tv import TvSwitch, screen_on, show_xbox

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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
#: What the owner checks when Microsoft refuses a command.
REMOTE_FEATURES = (
    "Check that remote features are on: Settings, Devices & connections (Enheter og tilkoblinger), "
    "Remote features (Eksterne funksjoner)."
)
#: Where the owner's settings are, for an answer that sends them there.
SETTINGS_FILE = "~/.config/xbox-mcp/config.toml"
#: Seconds the console's status is watched after a command, for the change to show.
READ_BACK_WITHIN = 6.0
#: Seconds between looks while watching.
READ_BACK_EVERY = 1.0
#: What the owner may ask for, and the action each means.
ACTIONS: dict[str, Action] = {"wake": "wake", "on": "wake", "sleep": "sleep", "off": "sleep"}
#: What the voice hears when a power question is no longer open.
NO_QUESTION = (
    "That question has run out or was answered already. Ask again to wake the Xbox or put it to sleep."
)


class Refused(Exception):  # noqa: N818 - it is an answer, not a fault
    """An answer that refuses, with what the voice should say."""

    def __init__(self, say: str, outcome: str = "refused") -> None:
        """Refuse with `say`."""
        super().__init__(say)
        self.say, self.outcome = say, outcome


def answer(outcome: str, say: str, **fields: Any) -> dict[str, Any]:  # noqa: ANN401
    """One answer, in the shape every tool gives; only ``done`` and ``asked`` are a success."""
    return {"success": outcome in ("done", "asked"), "outcome": outcome, "say": say, **fields}


def cloud_failure(error: UnreachableError, **fields: Any) -> dict[str, Any]:  # noqa: ANN401
    """The answer when the cloud did not answer, or there is no sign-in."""
    if isinstance(error, SignedOutError):
        return answer("signed_out", SIGNED_OUT, **fields)
    return answer("unreachable", UNREACHABLE, **fields)


def _plain(power: Power) -> Power:
    """Asleep with an update running is asleep."""
    return "asleep" if power == "updating" else power


def _already(action: Action, status: ConsoleStatus) -> tuple[str, str] | None:
    """Why `action` cannot be asked for now, as (outcome, say); None when it can."""
    power = _plain(status.power)
    if power == "unknown":
        return (
            "unreachable",
            "Microsoft does not know whether the Xbox is on, so I won't switch it. Try again soon.",
        )
    if power == "off":
        return "refused", describe(status, None)
    if action == "wake" and power == "on":
        return "refused", "The Xbox is already on. Ask what it runs, or ask to put it to sleep."
    if action == "sleep" and power == "asleep":
        return "refused", "The Xbox is already asleep. Ask to wake it if you want it on."
    return None


def _plain_words(text: str) -> str:
    return " ".join(text.casefold().split())


def listed(said: str, titles: tuple[str, ...]) -> list[str]:
    """The titles on the owner's list that `said` names: the whole name, or else every word of it."""
    wanted = _plain_words(said)
    exact = [t for t in titles if _plain_words(t) == wanted]
    if exact:
        return exact
    words = wanted.split()
    return [t for t in titles if words and all(w in _plain_words(t) for w in words)]


def _question(action: Action, status: ConsoleStatus, *, night: bool) -> str:
    """The question read aloud: how things are, and then what to do."""
    if action == "sleep":
        return f"{describe(status, None)} Do you want me to put it to sleep?"
    tv = "" if night else " If I wake it, the TV will come on too."
    return f"{describe(status, None)}{tv} Do you want me to wake it?"


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
        "The Xbox is fully off, in energy-saving mode, so it can only be switched on at the console. "
        "Set Settings, General, Power options (Strømalternativer) to Sleep (Hvilemodus), so it can be woken."
    )


class XboxService:
    """The owner's console, reached through `cloud`."""

    def __init__(  # noqa: PLR0913 - each is a seam a test replaces
        self,
        cloud: Cloud,
        settings: Settings,
        *,
        tv: TvSwitch | None = None,
        hour: Callable[[], int] = local_hour,
        questions: Questions | None = None,
        wait: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Reach the console through `cloud`, with the owner's `settings`, and its TV through `tv`."""
        self.cloud, self.settings, self.tv = cloud, settings, tv
        self.hour, self.wait = hour, wait
        self.questions = questions or Questions()

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

    async def _night_refusal(self, action: Action) -> str | None:
        """Why a wake is refused at night; None when it is day, a sleep, or the TV is on."""
        if action != "wake" or not is_night(self.hour(), self.settings.night):
            return None
        on = await screen_on(self.tv) if self.tv else None
        # RULE: between 22 and 07 a wake is refused unless the tv is on
        if on is True:
            return None
        seen = "I can't tell whether the TV is on" if on is None else "the TV is off"
        return (
            f"It is night, and {seen}, so I won't wake the Xbox: waking it switches the TV on. "
            "Switch the TV on first, then ask again."
        )

    async def power_request(self, action: str) -> dict[str, Any]:
        """Ask the owner whether to wake the console or put it to sleep; nothing is sent."""
        wanted = ACTIONS.get(action.casefold().strip())
        if wanted is None:
            return answer("refused", "I can only wake the Xbox or put it to sleep. Say wake or sleep.")
        try:
            console, status = await self.look()
        except Refused as refused:
            return answer(refused.outcome, refused.say)
        except UnreachableError as error:
            return cloud_failure(error)
        already = _already(wanted, status)
        if already:
            return answer(*already, power=_plain(status.power))
        night = await self._night_refusal(wanted)
        if night:
            return answer("refused", night, power=_plain(status.power))
        question = self.questions.ask(wanted, console.id)
        say = _question(wanted, status, night=is_night(self.hour(), self.settings.night))
        return answer(
            "asked",
            say,
            token=question.token,
            action=wanted,
            expires_in=CONFIRM_WITHIN,
            power=_plain(status.power),
        )

    async def power_confirm(self, token: str) -> dict[str, Any]:
        """Carry out the owner's yes to the open power question, and read the console back."""
        question = self.questions.take(token)
        if question is None:
            return answer("refused", NO_QUESTION)
        night = await self._night_refusal(question.action)
        if night:
            self.questions.spend(question)
            return answer("refused", night)
        command = self.cloud.wake if question.action == "wake" else self.cloud.sleep
        try:
            sent = await command(question.console_id)
        except UnreachableError as error:
            # RULE: a token is spent only once the command was sent
            return cloud_failure(error, power=None)
        self.questions.spend(question)
        if not sent.accepted:
            reason = sent.reason or "no reason given"
            say = f"Microsoft did not take the command ({reason}), so nothing changed. {REMOTE_FEATURES}"
            return answer("refused", say, power=None)
        return await self._after(question.action, question.console_id)

    async def _watch(self, console_id: str, done: Callable[[ConsoleStatus], bool]) -> ConsoleStatus | None:
        """The console's status once `done` holds for it, or the last one read when the watch ends."""
        waited, status = 0.0, None
        while True:
            try:
                status = await self.cloud.status(console_id)
            except UnreachableError:
                status = None
            if (status and done(status)) or waited >= READ_BACK_WITHIN:
                return status
            await self.wait(READ_BACK_EVERY)
            waited += READ_BACK_EVERY

    async def _read_back(self, console_id: str, wanted: Power) -> Power | None:
        """The console's power once it reads `wanted`, or what it reads when the watch ends."""
        status = await self._watch(console_id, lambda s: _plain(s.power) == wanted)
        return _plain(status.power) if status else None

    async def _after(self, action: Action, console_id: str) -> dict[str, Any]:
        """What happened after a command was taken, read back from the console's status."""
        wanted: Power = "on" if action == "wake" else "asleep"
        power = await self._read_back(console_id, wanted)
        # RULE: a command is said as done only once the status reads it back
        if power != wanted:
            noun = "wake" if action == "wake" else "sleep command"
            reads = power or "nothing Microsoft can tell"
            say = f"I sent the {noun}, but the Xbox reads {reads}, so it is not confirmed. Ask again soon."
            return answer("not_confirmed", say, power=power)
        if action == "sleep":
            return answer("done", "Asleep. The Xbox is asleep now.", power=power)
        done = answer("done", "Woken. The Xbox is on.", power=power)
        if self.tv is None:
            return done
        tv = await show_xbox(self.tv)
        return done | {"say": f"{done['say']} {tv['say']}", "tv": tv}

    async def launch(self, title: str) -> dict[str, Any]:
        """Start a title from the owner's list, if it is installed and the Xbox is on; then read it back."""
        if not self.settings.titles:
            say = f"No games are on the list yet. Add them to [launch] titles in {SETTINGS_FILE}."
            return answer("refused", say, title=None)
        names = listed(title, self.settings.titles)
        # RULE: only a title on the owners list is ever started
        if len(names) != 1:
            return answer("refused", _not_one(title, names, self.settings.titles), title=None)
        try:
            return await self._launch(names[0])
        except Refused as refused:
            return answer(refused.outcome, refused.say, title=None)
        except UnreachableError as error:
            return cloud_failure(error, title=None)

    async def _launch(self, name: str) -> dict[str, Any]:
        console, status = await self.look()
        # RULE: a launch is refused unless the xbox is on
        if status.power != "on":
            say = f"{describe(status, None)} Wake it first, then ask for {name}."
            return answer("refused", say, title=None)
        installed = [
            a for a in await self.cloud.apps(console.id) if _plain_words(a.name) == _plain_words(name)
        ]
        # RULE: only an installed title is ever started
        if not installed:
            say = (
                f"{name} is on the list, but not installed on the Xbox. Install it, or take it off the list."
            )
            return answer("refused", say, title=None)
        app = installed[0]
        sent = await self.cloud.launch(console.id, app.product_id)
        if not sent.accepted:
            reason = sent.reason or "no reason given"
            say = f"Microsoft did not take the start ({reason}), so nothing changed. {REMOTE_FEATURES}"
            return answer("refused", say, title=None)
        shows = await self._read_focus(console.id, app)
        # RULE: a start is said as done only once the xbox shows it
        if shows != app.aumid:
            say = f"I sent the start of {name}, but the Xbox does not show it yet: not confirmed. Ask soon."
            return answer("not_confirmed", say, title=None)
        return answer("done", f"Started {name}. The Xbox is running it.", title=name)

    async def _read_focus(self, console_id: str, app: App) -> str | None:
        """The app in focus once it is `app`, or whatever is in focus when the watch ends."""
        status = await self._watch(console_id, lambda s: s.focus == app.aumid)
        return status.focus if status else None


def _not_one(said: str, names: list[str], titles: tuple[str, ...]) -> str:
    """Why `said` names no title, or more than one, on the owner's list."""
    if names:
        return f"That could be {' or '.join(names)}. Say which one."
    return (
        f"{said} is not on the list of games I may start. The list has {', '.join(titles)}. "
        f"Say one of those, or add it to [launch] titles in {SETTINGS_FILE}."
    )
