# SPDX-License-Identifier: GPL-3.0-or-later
"""Stopping a game sends the Xbox to Home, only after the owner's yes, and only for a listed game.

Microsoft's cloud cannot close a game. Home brings the dashboard in front,
and the console keeps the game paused in the background (ADR-XB-001), so
every answer says so. The question names the game; the yes is checked again
against what runs then.
"""

import asyncio

from xbox_mcp.console import HOME_AUMID
from xbox_mcp.service import XboxService
from xbox_mcp.settings import Settings
from xbox_mcp.simulator import CONSOLE_ID, FORZA, HALO, MINECRAFT, SimulatedCloud

LISTED = Settings(titles=("Forza Horizon 5", "Minecraft"))


async def _no_wait(_seconds):
    return None


def _playing(app):
    cloud = SimulatedCloud(power="on")
    cloud.focus = app.aumid
    return cloud, XboxService(cloud, LISTED, wait=_no_wait)


def test_a_stop_asks_first_naming_the_game_and_sends_nothing():
    cloud, service = _playing(MINECRAFT)
    asked = asyncio.run(service.home_request())
    assert asked["outcome"] == "asked"
    assert asked["token"]
    assert asked["say"].startswith("Minecraft is running.")
    assert "paused in the background" in asked["say"]
    assert asked["say"].endswith("?")
    assert cloud.sent == []


def test_a_stop_is_sent_and_read_back_on_the_yes():
    cloud, service = _playing(MINECRAFT)

    async def run():
        asked = await service.home_request()
        return await service.home_confirm(asked["token"])

    done = asyncio.run(run())
    assert cloud.sent == [("home", CONSOLE_ID, None)]
    assert cloud.focus == HOME_AUMID
    assert done["outcome"] == "done"
    assert "Minecraft stays paused in the background" in done["say"]


def test_only_a_listed_title_is_ever_sent_home():
    cloud, service = _playing(HALO)
    answer = asyncio.run(service.home_request())
    assert answer["outcome"] == "refused"
    assert "token" not in answer
    assert "not on your list" in answer["say"]
    assert cloud.sent == []


def test_a_stop_is_checked_again_at_the_yes():
    cloud, service = _playing(MINECRAFT)

    async def run():
        asked = await service.home_request()
        cloud.focus = FORZA.aumid
        return await service.home_confirm(asked["token"])

    answer = asyncio.run(run())
    assert answer["outcome"] == "refused"
    assert cloud.sent == []


def test_nothing_is_stopped_when_home_is_in_front():
    cloud, service = _playing(MINECRAFT)
    cloud.focus = HOME_AUMID
    answer = asyncio.run(service.home_request())
    assert answer["outcome"] == "refused"
    assert "token" not in answer


def test_nothing_is_stopped_while_the_xbox_sleeps():
    cloud = SimulatedCloud(power="asleep")
    answer = asyncio.run(XboxService(cloud, LISTED, wait=_no_wait).home_request())
    assert answer["outcome"] == "refused"
    assert cloud.sent == []


def test_a_stop_is_said_as_done_only_once_home_is_in_front():
    cloud, service = _playing(MINECRAFT)

    async def run():
        asked = await service.home_request()
        cloud.ignores_commands = True
        return await service.home_confirm(asked["token"])

    answer = asyncio.run(run())
    assert answer["outcome"] == "not_confirmed"
    assert "not confirmed" in answer["say"]


def test_a_token_answers_only_the_question_it_was_asked_for():
    cloud, service = _playing(MINECRAFT)

    async def run():
        home = await service.home_request()
        as_power = await service.power_confirm(home["token"])
        as_launch = await service.launch_confirm(home["token"])
        power = await service.power_request("sleep")
        as_home = await service.home_confirm(power["token"])
        return as_power, as_launch, as_home

    for answer in asyncio.run(run()):
        assert answer["outcome"] == "refused"
    assert cloud.sent == []
