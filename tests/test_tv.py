# SPDX-License-Identifier: GPL-3.0-or-later
"""The TV step through a real stdio child, as ADR-XB-003 describes, against a stand-in for tv-mcp."""

import asyncio
import json
import pathlib
import sys

import pytest

from xbox_mcp import tv
from xbox_mcp.cli import tv_from
from xbox_mcp.drivers.tv_plugin import TvPlugin
from xbox_mcp.settings import SettingsError
from xbox_mcp.tv import SimulatedTvPlugin, TvSwitch, screen_on, show_xbox

FAKE = pathlib.Path(__file__).with_name("fake_tv_mcp.py")


def _plugin(tmp_path, screen):
    log = tmp_path / "calls.log"
    log.touch()
    plugin = TvPlugin(command=[sys.executable, str(FAKE), screen, str(log)], tv="the den", device="Xbox")
    return plugin, log


def _calls(log):
    return [json.loads(line)[0] for line in log.read_text().splitlines()]


def test_an_on_tv_is_switched_through_the_tv_plugins_own_tools(tmp_path):
    plugin, log = _plugin(tmp_path, "on")
    answer = asyncio.run(show_xbox(plugin, settle=0.0))
    assert _calls(log) == ["tv_status", "tv_input"]
    assert answer["outcome"] == "done"


def test_a_tv_that_never_comes_on_is_left_alone(tmp_path):
    plugin, log = _plugin(tmp_path, "off")
    answer = asyncio.run(show_xbox(plugin, settle=0.0))
    assert "tv_input" not in _calls(log)
    assert answer["outcome"] == "left_alone"


def test_the_night_rule_reads_the_screen_through_the_tv_plugin(tmp_path):
    plugin, log = _plugin(tmp_path, "on")
    assert asyncio.run(screen_on(plugin)) is True
    assert _calls(log) == ["tv_status"]


def test_the_tv_plugin_is_closed_after_each_use(tmp_path):
    plugin, _ = _plugin(tmp_path, "on")
    asyncio.run(show_xbox(plugin, settle=0.0))
    assert not plugin.is_open


def test_a_tv_plugin_that_cannot_start_is_said_not_raised(tmp_path):
    plugin = TvPlugin(command=[str(tmp_path / "no-such-program")], tv="the den", device="Xbox")
    answer = asyncio.run(show_xbox(plugin, settle=0.0))
    assert answer["success"] is False
    assert "TV" in answer["say"]
    assert asyncio.run(screen_on(plugin)) is None


def test_only_the_tv_status_and_input_tools_are_ever_called():
    tv = SimulatedTvPlugin(screen="on")
    assert asyncio.run(tv.call("tv_power_request", {"tv": tv.tv})) is None
    assert asyncio.run(tv.call("tv_power_confirm", {"token": "x"})) is None
    assert tv.calls == []
    assert TvSwitch.TOOLS == ("tv_status", "tv_input")


def test_a_tv_that_is_not_on_is_never_sent_an_input():
    tv = SimulatedTvPlugin(screen="off")
    answer = asyncio.run(show_xbox(tv, settle=0.0))
    assert [c[0] for c in tv.calls] == ["tv_status"]
    assert answer["outcome"] == "left_alone"


def test_the_tv_table_needs_a_command_and_a_name():
    assert tv_from({}) is None
    with pytest.raises(SettingsError):
        tv_from({"name": "the den"})
    plugin = tv_from({"command": ["tv-mcp", "serve"], "name": "the den"})
    assert plugin.device == "Xbox"


#: tv-mcp's tv_status answer for one TV, in the shape it really sends (seen 2026-11-11).
REAL = {"success": True, "say": "...", "tvs": [{"tv": "the living-room TV", "state": "on", "inputs": []}]}


def test_the_screen_is_read_from_the_tv_in_tv_mcps_list():
    assert tv.screen_of(REAL) == "on"
    assert tv.screen_of({**REAL, "tvs": [{**REAL["tvs"][0], "state": "off"}]}) == "off"
    assert tv.screen_of({**REAL, "tvs": [{**REAL["tvs"][0], "screen": "off"}]}) == "off", "screen wins"


def test_a_tv_answer_that_names_no_single_tv_counts_as_unknown():
    two = {**REAL, "tvs": [REAL["tvs"][0], REAL["tvs"][0]]}
    assert tv.screen_of(two) is None
    assert tv.screen_of({"success": False, "say": "..."}) is None
    assert tv.screen_of({"tvs": "on"}) is None
