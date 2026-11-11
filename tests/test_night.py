# SPDX-License-Identifier: GPL-3.0-or-later
"""At night the console is not woken unless its TV is already on (ADR-XB-003).

Waking the console switches its TV on through HDMI-CEC. Between 22 and 07 a
wake is refused unless the TV plugin reports that TV's screen on; a TV whose
state is unknown counts as off. The rule is checked at the question and again
at the yes.
"""

import asyncio

import pytest

from xbox_mcp.power import Questions, is_night
from xbox_mcp.service import XboxService
from xbox_mcp.settings import Settings
from xbox_mcp.simulator import SimulatedCloud
from xbox_mcp.tv import SimulatedTvPlugin


async def _no_wait(_seconds):
    return None


class Hour:
    def __init__(self, hour):
        self.hour = hour

    def __call__(self):
        return self.hour


def _service(cloud, *, hour, tv=None, settings=None):
    clock = hour if isinstance(hour, Hour) else Hour(hour)
    return XboxService(cloud, settings or Settings(), tv=tv, hour=clock, questions=Questions(), wait=_no_wait)


@pytest.mark.parametrize(
    ("hour", "night"), [(21, False), (22, True), (23, True), (0, True), (6, True), (7, False)]
)
def test_the_night_runs_from_22_to_07(hour, night):
    assert is_night(hour, (22, 7)) is night


def test_a_night_set_inside_one_day_is_read_as_such():
    assert is_night(14, (13, 15)) is True
    assert is_night(16, (13, 15)) is False


def test_between_22_and_07_a_wake_is_refused_unless_the_tv_is_on():
    cloud, tv = SimulatedCloud(power="asleep"), SimulatedTvPlugin(screen="off")
    asked = asyncio.run(_service(cloud, hour=23, tv=tv).power_request("wake"))
    assert asked["outcome"] == "refused"
    assert "night" in asked["say"]
    assert "token" not in asked
    assert cloud.sent == []


def test_at_night_a_wake_is_asked_when_the_tv_is_already_on():
    cloud, tv = SimulatedCloud(power="asleep"), SimulatedTvPlugin(screen="on")
    asked = asyncio.run(_service(cloud, hour=23, tv=tv).power_request("wake"))
    assert asked["outcome"] == "asked"
    assert "will come on" not in asked["say"]


def test_at_night_a_tv_whose_state_is_unknown_counts_as_off():
    cloud, tv = SimulatedCloud(power="asleep"), SimulatedTvPlugin(screen=None)
    asked = asyncio.run(_service(cloud, hour=1, tv=tv).power_request("wake"))
    assert asked["outcome"] == "refused"


def test_at_night_with_no_tv_set_a_wake_is_refused():
    cloud = SimulatedCloud(power="asleep")
    asked = asyncio.run(_service(cloud, hour=1).power_request("wake"))
    assert asked["outcome"] == "refused"
    assert cloud.sent == []


def test_the_night_rule_is_checked_again_at_the_yes():
    cloud, tv, hour = SimulatedCloud(power="asleep"), SimulatedTvPlugin(screen="off"), Hour(21)
    service = _service(cloud, hour=hour, tv=tv)

    async def run():
        asked = await service.power_request("wake")
        hour.hour = 22
        return asked, await service.power_confirm(asked["token"])

    asked, done = asyncio.run(run())
    assert asked["outcome"] == "asked"
    assert done["outcome"] == "refused"
    assert cloud.sent == []


def test_sleep_is_never_refused_by_the_clock():
    cloud = SimulatedCloud(power="on")
    service = _service(cloud, hour=2)

    async def run():
        asked = await service.power_request("sleep")
        return await service.power_confirm(asked["token"])

    assert asyncio.run(run())["outcome"] == "done"


def test_the_owner_may_set_other_night_hours():
    cloud, tv = SimulatedCloud(power="asleep"), SimulatedTvPlugin(screen="off")
    service = _service(cloud, hour=22, tv=tv, settings=Settings(night=(23, 6)))
    assert asyncio.run(service.power_request("wake"))["outcome"] == "asked"
