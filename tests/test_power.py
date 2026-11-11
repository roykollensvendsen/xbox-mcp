# SPDX-License-Identifier: GPL-3.0-or-later
"""Waking or sleeping the console is a question, then the owner's yes with a single-use token.

Nothing is sent on the question alone. The token is good for one confirm and
for 30 seconds, and the answer says "not confirmed" until the console's own
status reads the change back.
"""

import asyncio

from xbox_mcp.power import CONFIRM_WITHIN, Questions
from xbox_mcp.service import XboxService
from xbox_mcp.settings import Settings
from xbox_mcp.simulator import CONSOLE_ID, SimulatedCloud
from xbox_mcp.tv import SimulatedTvPlugin

NOON, MIDNIGHT = 12, 0


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


async def _no_wait(_seconds):
    return None


def _service(cloud, *, hour=NOON, tv=None, clock=None, settings=None):
    return XboxService(
        cloud,
        settings or Settings(),
        tv=tv,
        hour=lambda: hour,
        questions=Questions(clock=clock or Clock()),
        wait=_no_wait,
    )


def _ask_and_confirm(service, action):
    async def run():
        asked = await service.power_request(action)
        return asked, await service.power_confirm(asked.get("token", ""))

    return asyncio.run(run())


def test_nothing_is_sent_on_the_question_alone():
    cloud = SimulatedCloud(power="asleep")
    asked = asyncio.run(_service(cloud).power_request("wake"))
    assert asked["outcome"] == "asked"
    assert asked["token"]
    assert asked["say"].endswith("?")
    assert cloud.sent == []


def test_a_wake_question_says_how_things_are_and_then_asks():
    asked = asyncio.run(_service(SimulatedCloud(power="asleep")).power_request("wake"))
    assert asked["say"].startswith("The Xbox is asleep.")
    assert "Do you want me to wake it?" in asked["say"]


def test_by_day_a_wake_question_says_the_tv_will_come_on_too():
    asked = asyncio.run(_service(SimulatedCloud(power="asleep")).power_request("wake"))
    assert "TV will come on too" in asked["say"]


def test_a_yes_wakes_the_xbox_and_reads_it_back():
    cloud = SimulatedCloud(power="asleep")
    _, done = _ask_and_confirm(_service(cloud), "wake")
    assert cloud.sent == [("wake", CONSOLE_ID, None)]
    assert done["outcome"] == "done"
    assert done["power"] == "on"
    assert done["say"].startswith("Woken.")


def test_a_yes_puts_the_xbox_to_sleep_and_reads_it_back():
    cloud = SimulatedCloud(power="on")
    _, done = _ask_and_confirm(_service(cloud), "sleep")
    assert done["outcome"] == "done"
    assert done["power"] == "asleep"


def test_a_command_is_said_as_done_only_once_the_status_reads_it_back():
    cloud = SimulatedCloud(power="asleep")
    cloud.ignores_commands = True
    _, done = _ask_and_confirm(_service(cloud), "wake")
    assert done["outcome"] == "not_confirmed"
    assert done["success"] is False
    assert "not confirmed" in done["say"]


def test_a_token_is_single_use():
    cloud = SimulatedCloud(power="asleep")
    service = _service(cloud)

    async def run():
        asked = await service.power_request("wake")
        await service.power_confirm(asked["token"])
        cloud.power[CONSOLE_ID] = "asleep"
        return await service.power_confirm(asked["token"])

    again = asyncio.run(run())
    assert again["outcome"] == "refused"
    assert len(cloud.sent) == 1


def test_a_token_older_than_thirty_seconds_is_refused():
    cloud, clock = SimulatedCloud(power="asleep"), Clock()
    service = _service(cloud, clock=clock)

    async def run():
        asked = await service.power_request("wake")
        clock.now += CONFIRM_WITHIN + 0.1
        return await service.power_confirm(asked["token"])

    late = asyncio.run(run())
    assert CONFIRM_WITHIN == 30.0
    assert late["outcome"] == "refused"
    assert cloud.sent == []


def test_a_made_up_token_is_refused():
    cloud = SimulatedCloud(power="asleep")
    service = _service(cloud)
    answer = asyncio.run(service.power_confirm("not-a-token"))
    assert answer["outcome"] == "refused"
    assert cloud.sent == []


def test_a_token_is_spent_only_once_the_command_was_sent():
    cloud = SimulatedCloud(power="asleep")
    service = _service(cloud)

    async def run():
        asked = await service.power_request("wake")
        cloud.reachable = False
        first = await service.power_confirm(asked["token"])
        cloud.reachable = True
        return first, await service.power_confirm(asked["token"])

    first, second = asyncio.run(run())
    assert first["outcome"] == "unreachable"
    assert second["outcome"] == "done"


def test_a_new_question_cancels_the_open_one():
    cloud = SimulatedCloud(power="asleep")
    service = _service(cloud)

    async def run():
        first = await service.power_request("wake")
        await service.power_request("wake")
        return await service.power_confirm(first["token"])

    assert asyncio.run(run())["outcome"] == "refused"
    assert cloud.sent == []


def test_waking_an_xbox_that_is_on_is_refused_as_already_done():
    asked = asyncio.run(_service(SimulatedCloud(power="on")).power_request("wake"))
    assert asked["outcome"] == "refused"
    assert "already on" in asked["say"]
    assert "token" not in asked


def test_a_fully_off_xbox_cannot_be_woken():
    asked = asyncio.run(_service(SimulatedCloud(power="off")).power_request("wake"))
    assert asked["outcome"] == "refused"
    assert "at the console" in asked["say"]


def test_sleeping_an_xbox_already_asleep_is_refused():
    asked = asyncio.run(_service(SimulatedCloud(power="asleep")).power_request("sleep"))
    assert asked["outcome"] == "refused"
    assert "already asleep" in asked["say"]


def test_a_power_question_is_refused_when_the_state_is_not_known():
    asked = asyncio.run(_service(SimulatedCloud(reachable=False)).power_request("wake"))
    assert asked["outcome"] == "unreachable"
    assert "token" not in asked


def test_an_action_other_than_wake_or_sleep_is_refused():
    cloud = SimulatedCloud(power="on")
    asked = asyncio.run(_service(cloud).power_request("reboot"))
    assert asked["outcome"] == "refused"
    assert "token" not in asked


def test_a_confirmed_wake_switches_the_tv_to_the_xbox():
    cloud, tv = SimulatedCloud(power="asleep"), SimulatedTvPlugin(screen="off")
    cloud.on_wake = tv.cec_on
    _, done = _ask_and_confirm(_service(cloud, tv=tv), "wake")
    assert [c[0] for c in tv.calls][-1] == "tv_input"
    assert tv.calls[-1][1] == {"source": "Xbox", "tv": tv.tv}
    assert done["tv"]["outcome"] == "done"


def test_a_confirmed_sleep_leaves_the_tv_alone():
    cloud, tv = SimulatedCloud(power="on"), SimulatedTvPlugin(screen="on")
    _ask_and_confirm(_service(cloud, tv=tv), "sleep")
    assert tv.calls == []


def test_a_wake_that_is_not_confirmed_leaves_the_tv_alone():
    cloud, tv = SimulatedCloud(power="asleep"), SimulatedTvPlugin(screen="on")
    cloud.ignores_commands = True
    _ask_and_confirm(_service(cloud, tv=tv), "wake")
    assert "tv_input" not in [c[0] for c in tv.calls]


def test_no_tv_tool_but_status_and_input_is_ever_called():
    cloud, tv = SimulatedCloud(power="asleep"), SimulatedTvPlugin(screen="on")
    cloud.on_wake = tv.cec_on
    _ask_and_confirm(_service(cloud, tv=tv, hour=MIDNIGHT), "wake")
    assert {c[0] for c in tv.calls} <= {"tv_status", "tv_input"}
