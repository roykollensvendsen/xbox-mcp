# SPDX-License-Identifier: GPL-3.0-or-later
"""Every answer that does not succeed says what is wrong and what to do next.

The plugin standard's rule 4, and the owner's standing rule for every plugin
(write-plugin checklist item 10): a bare "not answering" with no next step is a
bug. Each failure the plugin can give is produced here, and its 'say' must
hold a sentence that tells the owner what to do, with the exact command or
menu where there is one.
"""

import asyncio
import re

import pytest

from xbox_mcp import server, tv
from xbox_mcp.console import HOME_AUMID, Sent
from xbox_mcp.power import Questions
from xbox_mcp.service import XboxService
from xbox_mcp.settings import Settings
from xbox_mcp.simulator import FORZA, HALO, SimulatedCloud
from xbox_mcp.tv import SimulatedTvPlugin, show_xbox

#: A sentence that tells the owner what to do: it opens with one of these.
NEXT_STEP = re.compile(
    r"(?:^|[.?!]\s+)(?:Try|Ask|Check|Set|Switch|Say|Name|Wake|Install|Add|Use|In a terminal|Sign in again)\b"
)
LISTED = Settings(titles=("Forza Horizon 5", "Starfield"))


async def _no_wait(_seconds):
    return None


def _service(cloud, *, settings=LISTED, hour=12, tv_plugin=None):
    return XboxService(cloud, settings, tv=tv_plugin, hour=lambda: hour, questions=Questions(), wait=_no_wait)


def _refusing(cloud):
    cloud.ignores_commands = True
    return cloud


class Refusing(SimulatedCloud):
    async def wake(self, _console_id):
        return Sent(accepted=False, reason="RemoteManagementDisabled")


def _confirmed(service, action):
    async def run():
        asked = await service.power_request(action)
        return await service.power_confirm(asked["token"])

    return asyncio.run(run())


def _showing(app):
    cloud = SimulatedCloud(power="on")
    cloud.focus = app.aumid
    return cloud


def _changed(cloud, kind):
    service = _service(cloud)

    async def run():
        asked = await (service.home_request() if kind == "home" else service.launch("Forza"))
        cloud.focus = HOME_AUMID
        confirm = service.home_confirm if kind == "home" else service.launch_confirm
        return await confirm(asked["token"])

    return asyncio.run(run())


def _stop_unconfirmed():
    cloud = _showing(FORZA)
    service = _service(cloud)

    async def run():
        asked = await service.home_request()
        cloud.ignores_commands = True
        return await service.home_confirm(asked["token"])

    return asyncio.run(run())


FAILURES = {
    "cloud down": lambda: asyncio.run(_service(SimulatedCloud(reachable=False)).status()),
    "signed out": lambda: asyncio.run(_service(SimulatedCloud(signed_in=False)).status()),
    "unknown power": lambda: asyncio.run(_service(SimulatedCloud(power="unknown")).status()),
    "fully off": lambda: asyncio.run(_service(SimulatedCloud(power="off")).power_request("wake")),
    "two consoles": lambda: asyncio.run(
        _service(SimulatedCloud(extra_console=("C2", "Bedroom box", "on"))).status()
    ),
    "console not found": lambda: asyncio.run(
        _service(SimulatedCloud(), settings=Settings(console="Nowhere")).status()
    ),
    "already on": lambda: asyncio.run(_service(SimulatedCloud(power="on")).power_request("wake")),
    "already asleep": lambda: asyncio.run(_service(SimulatedCloud(power="asleep")).power_request("sleep")),
    "not wake or sleep": lambda: asyncio.run(_service(SimulatedCloud()).power_request("reboot")),
    "night, tv off": lambda: asyncio.run(
        _service(SimulatedCloud(), hour=23, tv_plugin=SimulatedTvPlugin(screen="off")).power_request("wake")
    ),
    "night, no tv": lambda: asyncio.run(_service(SimulatedCloud(), hour=23).power_request("wake")),
    "token run out": lambda: asyncio.run(_service(SimulatedCloud()).power_confirm("nope")),
    "command refused": lambda: _confirmed(_service(Refusing()), "wake"),
    "not confirmed": lambda: _confirmed(_service(_refusing(SimulatedCloud())), "wake"),
    "empty list": lambda: asyncio.run(_service(SimulatedCloud(power="on"), settings=Settings()).launch("x")),
    "not listed": lambda: asyncio.run(_service(SimulatedCloud(power="on")).launch("Halo")),
    "not installed": lambda: asyncio.run(_service(SimulatedCloud(power="on")).launch("Starfield")),
    "launch while asleep": lambda: asyncio.run(_service(SimulatedCloud()).launch("Forza")),
    "start not confirmed": lambda: asyncio.run(
        _service(_refusing(SimulatedCloud(power="on"))).launch("Forza")
    ),
    "tv never on": lambda: asyncio.run(show_xbox(SimulatedTvPlugin(screen="off"), settle=0.0)),
    "stop with home in front": lambda: asyncio.run(_service(SimulatedCloud(power="on")).home_request()),
    "stop while asleep": lambda: asyncio.run(_service(SimulatedCloud()).home_request()),
    "stop an unlisted game": lambda: asyncio.run(_service(_showing(HALO)).home_request()),
    "stop token run out": lambda: asyncio.run(_service(SimulatedCloud()).home_confirm("nope")),
    "stop changed at the yes": lambda: _changed(_showing(FORZA), "home"),
    "stop not confirmed": _stop_unconfirmed,
    "replace token run out": lambda: asyncio.run(_service(SimulatedCloud()).launch_confirm("nope")),
    "replace changed at the yes": lambda: _changed(_showing(HALO), "launch"),
}


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_every_failure_says_what_to_do_next(case):
    answer = FAILURES[case]()
    assert answer["success"] is False, case
    assert NEXT_STEP.search(answer["say"]), f"{case}: {answer['say']!r} gives no next step"


def test_the_fixed_failure_sentences_say_what_to_do_next():
    for say in (server.WORKING, server.BROKE, tv.NOT_ON):
        assert NEXT_STEP.search(say), say
