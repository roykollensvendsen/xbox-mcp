# SPDX-License-Identifier: GPL-3.0-or-later
"""xbox_status says whether the console is on, asleep or off, and what it runs.

It reads only what the cloud reports: the power state, and the app in focus,
named from the console's own list of installed apps.
"""

import asyncio

from xbox_mcp.service import XboxService
from xbox_mcp.settings import Settings
from xbox_mcp.simulator import FORZA, SimulatedCloud


def _status(cloud, settings=None):
    return asyncio.run(XboxService(cloud, settings or Settings()).status())


def test_an_xbox_asleep_is_said_to_be_asleep():
    answer = _status(SimulatedCloud(power="asleep"))
    assert answer["success"] is True
    assert answer["power"] == "asleep"
    assert answer["running"] is None
    assert "asleep" in answer["say"]


def test_an_xbox_on_at_home_says_so():
    answer = _status(SimulatedCloud(power="on"))
    assert answer["power"] == "on"
    assert answer["running"] == "Home"
    assert "home screen" in answer["say"]


def test_an_xbox_running_a_game_names_the_game():
    cloud = SimulatedCloud(power="on")
    cloud.focus = FORZA.aumid
    answer = _status(cloud)
    assert answer["running"] == "Forza Horizon 5"
    assert "Forza Horizon 5" in answer["say"]


def test_an_app_not_in_the_installed_list_is_called_an_app():
    cloud = SimulatedCloud(power="on")
    cloud.focus = "Some.Unknown_abc!App"
    answer = _status(cloud)
    assert answer["running"] is None
    assert "an app" in answer["say"]


def test_an_xbox_fully_off_says_it_cannot_be_woken_by_voice():
    answer = _status(SimulatedCloud(power="off"))
    assert answer["power"] == "off"
    assert "at the console" in answer["say"]


def test_an_xbox_updating_is_asleep_and_says_it_updates():
    answer = _status(SimulatedCloud(power="updating"))
    assert answer["power"] == "asleep"
    assert "update" in answer["say"]


def test_a_cloud_that_does_not_answer_is_not_called_on_or_off():
    answer = _status(SimulatedCloud(reachable=False))
    assert answer["success"] is False
    assert answer["outcome"] == "unreachable"
    assert answer["power"] is None
    assert "don't know" in answer["say"]


def test_an_unknown_power_state_is_not_called_on_or_off():
    answer = _status(SimulatedCloud(power="unknown"))
    assert answer["success"] is False
    assert answer["power"] is None


def test_a_plugin_not_signed_in_says_how_to_sign_in():
    answer = _status(SimulatedCloud(signed_in=False))
    assert answer["outcome"] == "signed_out"
    assert "xbox-mcp sign-in" in answer["say"]


def test_a_named_console_is_the_one_asked_about():
    cloud = SimulatedCloud(power="asleep", extra_console=("C2", "Bedroom box", "on"))
    answer = _status(cloud, Settings(console="Bedroom box"))
    assert answer["power"] == "on"


def test_two_consoles_and_no_name_is_refused_naming_both():
    cloud = SimulatedCloud(extra_console=("C2", "Bedroom box", "on"))
    answer = _status(cloud)
    assert answer["outcome"] == "refused"
    assert "Bedroom box" in answer["say"]
    assert cloud.sent == []


def test_a_console_name_not_on_the_account_is_refused():
    answer = _status(SimulatedCloud(), Settings(console="Nowhere"))
    assert answer["outcome"] == "refused"
    assert "Nowhere" in answer["say"]
