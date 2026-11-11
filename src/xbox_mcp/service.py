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
    SIGN_IN_COMMAND,
    App,
    Cloud,
    Console,
    ConsoleStatus,
    Power,
    SignedOutError,
    UnreachableError,
)
from xbox_mcp.power import CONFIRM_WITHIN, Action, Question, Questions, is_night, local_hour
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
SIGNED_OUT = f"The Xbox plugin is not signed in to Microsoft. In a terminal, run {SIGN_IN_COMMAND}"
#: What the voice hears when what runs changed between a question and its yes.
CHANGED = "What the Xbox shows changed since I asked, so I did nothing. Ask again if you still want it."
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
        return answer("signed_out", error.say or SIGNED_OUT, **fields)
    return answer("unreachable", error.say or UNREACHABLE, **fields)


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
        self._console: Console | None = None
        self._looking = asyncio.Lock()

    async def console(self) -> Console:
        """The console the owner means: the one named in the settings, or the only one.

        It is looked up once and kept, so each later answer costs one call to the cloud fewer.
        """
        async with self._looking:
            if self._console is None:
                self._console = await self._find_console()
        return self._console

    async def warm(self) -> None:
        """Look the console up now, at start, so the first question does not wait for it."""
        try:
            await self.console()
        except UnreachableError:
            return

    async def _find_console(self) -> Console:
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
        except SignedOutError as error:
            return answer("signed_out", error.say or SIGNED_OUT, power=None, running=None)
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
        action: Action = "wake" if question.action == "wake" else "sleep"
        night = await self._night_refusal(action)
        if night:
            self.questions.spend(question)
            return answer("refused", night)
        command = self.cloud.wake if action == "wake" else self.cloud.sleep
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
        return await self._after(action, question.console_id)

    async def _watch(self, console_id: str, done: Callable[[ConsoleStatus], bool]) -> ConsoleStatus | None:
        """The console's status once `done` holds for it, or the last one read when the watch ends."""
        waited, status = 0.0, None
        try:
            async with asyncio.timeout(READ_BACK_WITHIN):
                while True:
                    try:
                        status = await self.cloud.status(console_id)
                    except UnreachableError:
                        status = None
                    if (status and done(status)) or waited >= READ_BACK_WITHIN:
                        return status
                    await self.wait(READ_BACK_EVERY)
                    waited += READ_BACK_EVERY
        except TimeoutError:
            return status

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
        running = await self._running(console.id, status)
        if running and running.aumid == app.aumid:
            return answer("done", f"{name} is already running.", title=name)
        if running:
            return self._ask_over(console.id, running, app, name)
        return await self._start(console.id, app, name)

    def _ask_over(self, console_id: str, running: App, app: App, name: str) -> dict[str, Any]:
        """Ask before starting `app` over the game in front; nothing is sent."""
        question = self.questions.ask(
            "launch", console_id, over=running.aumid, name=name, product_id=app.product_id, aumid=app.aumid
        )
        say = (
            f"{running.name} is running. It stays paused in the background if I start {name}. "
            f"Do you want me to start {name} instead?"
        )
        return answer("asked", say, token=question.token, title=name, expires_in=CONFIRM_WITHIN)

    async def launch_confirm(self, token: str) -> dict[str, Any]:
        """Carry out the owner's yes to starting a game over the one in front."""
        question = self.questions.take(token, ("launch",))
        if question is None or question.product_id is None or question.name is None:
            return answer("refused", NO_QUESTION, title=None)
        try:
            return await self._confirmed_start(question)
        except UnreachableError as error:
            return cloud_failure(error, title=None)

    async def _confirmed_start(self, question: Question) -> dict[str, Any]:
        status = await self.cloud.status(question.console_id)
        self.questions.spend(question)
        # RULE: a start over a game is checked again at the yes
        if status.focus != question.over or status.power != "on":
            return answer("refused", CHANGED, title=None)
        app = App(str(question.name), str(question.product_id), question.aumid)
        return await self._start(question.console_id, app, app.name)

    async def _start(self, console_id: str, app: App, name: str) -> dict[str, Any]:
        """Send the start of `app`, and read it back."""
        sent = await self.cloud.launch(console_id, app.product_id)
        if not sent.accepted:
            reason = sent.reason or "no reason given"
            say = f"Microsoft did not take the start ({reason}), so nothing changed. {REMOTE_FEATURES}"
            return answer("refused", say, title=None)
        # RULE: a start is said as done only once the xbox shows it
        if not await self._shows(console_id, app):
            say = f"I sent the start of {name}, but the Xbox does not show it yet: not confirmed. Ask soon."
            return answer("not_confirmed", say, title=None)
        return answer("done", f"Started {name}. The Xbox is running it.", title=name)

    async def _running(self, console_id: str, status: ConsoleStatus) -> App | None:
        """The app in front, when the console is on and shows something other than Home."""
        if status.power != "on" or status.focus in (None, HOME_AUMID):
            return None
        found = [a for a in await self.cloud.apps(console_id) if a.aumid == status.focus]
        return found[0] if found else App("An app I cannot name", "", status.focus)

    async def home_request(self) -> dict[str, Any]:
        """Ask the owner whether to send the console to Home, naming the game in front; nothing is sent."""
        try:
            console, status = await self.look()
            running = await self._running(console.id, status)
        except Refused as refused:
            return answer(refused.outcome, refused.say, title=None)
        except UnreachableError as error:
            return cloud_failure(error, title=None)
        if running is None:
            shown = "The Xbox shows Home." if status.power == "on" else describe(status, None)
            say = f"{shown} No game is in front, so there is nothing to stop. Ask what it runs, if unsure."
            return answer("refused", say, title=None)
        # RULE: only a listed title is ever sent home
        if _plain_words(running.name) not in {_plain_words(t) for t in self.settings.titles}:
            say = (
                f"{running.name} is running, but it is not on your list, so I won't stop it. "
                f"Add it to [launch] titles in {SETTINGS_FILE}, or use the controller."
            )
            return answer("refused", say, title=running.name)
        question = self.questions.ask("home", console.id, over=running.aumid, name=running.name)
        say = (
            f"{running.name} is running. If I send the Xbox to Home, {running.name} stays paused in the "
            "background. Do you want me to?"
        )
        return answer("asked", say, token=question.token, title=running.name, expires_in=CONFIRM_WITHIN)

    async def home_confirm(self, token: str) -> dict[str, Any]:
        """Carry out the owner's yes to sending the console to Home, checked again first."""
        question = self.questions.take(token, ("home",))
        if question is None:
            return answer("refused", NO_QUESTION, title=None)
        try:
            return await self._go_home(question)
        except UnreachableError as error:
            return cloud_failure(error, title=None)

    async def _go_home(self, question: Question) -> dict[str, Any]:
        status = await self.cloud.status(question.console_id)
        # RULE: a stop is checked again at the yes
        if status.power != "on" or status.focus != question.over:
            self.questions.spend(question)
            return answer("refused", CHANGED, title=None)
        sent = await self.cloud.home(question.console_id)
        self.questions.spend(question)
        if not sent.accepted:
            reason = sent.reason or "no reason given"
            say = f"Microsoft did not take the command ({reason}), so nothing changed. {REMOTE_FEATURES}"
            return answer("refused", say, title=None)
        shown = await self._watch(question.console_id, lambda s: s.focus == HOME_AUMID)
        if shown is None or shown.focus != HOME_AUMID:
            say = "I sent the Xbox to Home, but it does not show Home yet: not confirmed. Ask soon."
            return answer("not_confirmed", say, title=question.name)
        say = f"The Xbox shows Home now. {question.name} stays paused in the background."
        return answer("done", say, title=question.name)

    async def _shows(self, console_id: str, app: App) -> bool:
        """Whether the console shows `app` in front; never, for an app with no id to read back."""
        if app.aumid is None:
            return False
        status = await self._watch(console_id, lambda s: s.focus == app.aumid)
        return status is not None and status.focus == app.aumid


def _not_one(said: str, names: list[str], titles: tuple[str, ...]) -> str:
    """Why `said` names no title, or more than one, on the owner's list."""
    if names:
        return f"That could be {' or '.join(names)}. Say which one."
    return (
        f"{said} is not on the list of games I may start. The list has {', '.join(titles)}. "
        f"Say one of those, or add it to [launch] titles in {SETTINGS_FILE}."
    )
