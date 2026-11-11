# SPDX-License-Identifier: GPL-3.0-or-later
"""xbox_launch starts only an installed title on the owner's own list, and only while the Xbox is on.

The list is [launch] titles in the owner's settings. A start is said as done
only once the console shows that title in front.
"""

import asyncio

from xbox_mcp.console import HOME_AUMID, App
from xbox_mcp.service import XboxService
from xbox_mcp.settings import Settings
from xbox_mcp.simulator import CONSOLE_ID, FORZA, MINECRAFT, SimulatedCloud

LISTED = Settings(titles=("Forza Horizon 5", "Minecraft", "Starfield"))


async def _no_wait(_seconds):
    return None


def _launch(cloud, title, settings=LISTED):
    return asyncio.run(XboxService(cloud, settings, wait=_no_wait).launch(title))


def test_a_listed_installed_title_is_started_and_read_back():
    cloud = SimulatedCloud(power="on")
    answer = _launch(cloud, "forza horizon 5")
    assert cloud.sent == [("launch", CONSOLE_ID, FORZA.product_id)]
    assert answer["outcome"] == "done"
    assert answer["title"] == "Forza Horizon 5"
    assert answer["say"].startswith("Started Forza Horizon 5.")


def test_some_words_of_a_listed_title_are_enough():
    cloud = SimulatedCloud(power="on")
    assert _launch(cloud, "minecraft")["title"] == "Minecraft"
    assert _launch(cloud, "forza")["title"] == "Forza Horizon 5"


def test_only_a_title_on_the_owners_list_is_ever_started():
    cloud = SimulatedCloud(power="on")
    answer = _launch(cloud, "Halo Infinite")
    assert answer["outcome"] == "refused"
    assert "list" in answer["say"]
    assert cloud.sent == []


def test_only_an_installed_title_is_ever_started():
    cloud = SimulatedCloud(power="on")
    answer = _launch(cloud, "Starfield")
    assert answer["outcome"] == "refused"
    assert "not installed" in answer["say"]
    assert cloud.sent == []


def test_a_launch_is_refused_unless_the_xbox_is_on():
    cloud = SimulatedCloud(power="asleep")
    answer = _launch(cloud, "Minecraft")
    assert answer["outcome"] == "refused"
    assert "asleep" in answer["say"]
    assert cloud.sent == []


def test_a_start_is_said_as_done_only_once_the_xbox_shows_it():
    cloud = SimulatedCloud(power="on")
    cloud.ignores_commands = True
    answer = _launch(cloud, "Minecraft")
    assert answer["outcome"] == "not_confirmed"
    assert "not confirmed" in answer["say"]


def test_a_name_that_fits_two_listed_titles_asks_which():
    cloud = SimulatedCloud(power="on")
    settings = Settings(titles=("Minecraft", "Minecraft Dungeons"))
    answer = _launch(cloud, "mine", settings)
    assert answer["outcome"] == "refused"
    assert "Minecraft Dungeons" in answer["say"]
    assert cloud.sent == []


def test_an_exact_name_wins_over_a_longer_one():
    cloud = SimulatedCloud(power="on")
    settings = Settings(titles=("Minecraft", "Minecraft Dungeons"))
    answer = _launch(cloud, "Minecraft", settings)
    assert cloud.sent == [("launch", CONSOLE_ID, MINECRAFT.product_id)]
    assert answer["outcome"] == "done"


def test_an_empty_list_says_how_the_owner_adds_titles():
    cloud = SimulatedCloud(power="on")
    answer = _launch(cloud, "Minecraft", Settings())
    assert answer["outcome"] == "refused"
    assert "[launch]" in answer["say"]
    assert cloud.sent == []


def test_a_launch_is_refused_when_the_cloud_does_not_answer():
    answer = _launch(SimulatedCloud(reachable=False), "Minecraft")
    assert answer["outcome"] == "unreachable"


def test_a_start_with_no_app_id_to_read_back_is_never_said_as_done():
    cloud = SimulatedCloud(power="on")
    cloud.installed = [App("Forza Horizon 5", FORZA.product_id, None)]
    cloud.focus = None
    answer = _launch(cloud, "Forza")
    assert answer["outcome"] == "not_confirmed"
    assert "not confirmed" in answer["say"]


def _over(running, title):
    cloud = SimulatedCloud(power="on")
    cloud.focus = running.aumid
    service = XboxService(cloud, LISTED, wait=_no_wait)
    return cloud, service, asyncio.run(service.launch(title))


def test_a_start_over_a_running_game_asks_first_naming_it():
    cloud, _, asked = _over(FORZA, "Minecraft")
    assert asked["outcome"] == "asked"
    assert asked["token"]
    assert asked["say"].startswith("Forza Horizon 5 is running.")
    assert "Minecraft" in asked["say"]
    assert asked["say"].endswith("?")
    assert cloud.sent == []


def test_a_start_over_a_running_game_happens_on_the_yes():
    cloud, service, asked = _over(FORZA, "Minecraft")
    done = asyncio.run(service.launch_confirm(asked["token"]))
    assert cloud.sent == [("launch", CONSOLE_ID, MINECRAFT.product_id)]
    assert done["outcome"] == "done"


def test_a_start_over_a_game_is_checked_again_at_the_yes():
    cloud, service, asked = _over(FORZA, "Minecraft")
    cloud.focus = HOME_AUMID
    answer = asyncio.run(service.launch_confirm(asked["token"]))
    assert answer["outcome"] == "refused"
    assert cloud.sent == []


def test_a_game_already_in_front_is_not_started_again():
    cloud, _, answer = _over(MINECRAFT, "Minecraft")
    assert answer["outcome"] == "done"
    assert "already" in answer["say"]
    assert cloud.sent == []
