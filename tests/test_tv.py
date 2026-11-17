# SPDX-License-Identifier: GPL-3.0-or-later
"""The TV step through a real stdio child, as ADR-XB-003 describes, against a stand-in for tv-mcp."""

import asyncio
import contextlib
import json
import os
import pathlib
import signal
import sys
import threading
import time

import pytest
from bounded import WITHIN

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


@pytest.fixture
def patient(monkeypatch):
    """Give the stand-in TV plugin the tests' one bound to start, not the voice's few seconds.

    These tests are about what is called and said, not how fast. Starting a Python program
    and its handshake can take longer than the plugin's own bound on a busy machine, and then
    the test failed with "working" for a TV that was fine. The wait still ends as soon as
    the plugin answers, and its own bound is tested by the slow-plugin tests below.
    """
    monkeypatch.setattr(tv, "TV_WITHIN", WITHIN)


def _calls(log):
    return [json.loads(line)[0] for line in log.read_text().splitlines()]


@pytest.mark.usefixtures("patient")
def test_an_on_tv_is_switched_through_the_tv_plugins_own_tools(tmp_path):
    plugin, log = _plugin(tmp_path, "on")
    answer = asyncio.run(show_xbox(plugin, settle=0.0))
    assert _calls(log) == ["tv_status", "tv_input"]
    assert answer["outcome"] == "done"


@pytest.mark.usefixtures("patient")
def test_a_tv_that_never_comes_on_is_left_alone(tmp_path):
    plugin, log = _plugin(tmp_path, "off")
    answer = asyncio.run(show_xbox(plugin, settle=0.0))
    assert "tv_input" not in _calls(log)
    assert answer["outcome"] == "left_alone"


def test_the_night_rule_reads_the_screen_through_the_tv_plugin(tmp_path):
    plugin, log = _plugin(tmp_path, "on")
    # Its bound is a default argument, fixed when the module loads, so the patch never reaches it.
    assert asyncio.run(screen_on(plugin, WITHIN)) is True
    assert _calls(log) == ["tv_status"]


@pytest.mark.usefixtures("patient")
def test_the_tv_plugin_is_closed_after_each_use(tmp_path):
    plugin, _ = _plugin(tmp_path, "on")
    asyncio.run(show_xbox(plugin, settle=0.0))
    assert not plugin.is_open


def _within(seconds, coroutine):
    """Run `coroutine`, failing in plain words if it is still running after `seconds`."""
    answer = {}

    def run():
        try:
            answer["it"] = asyncio.run(coroutine)
        except Exception as error:  # noqa: BLE001 - raised again in the test's own thread
            answer["error"] = error

    runner = threading.Thread(target=run, daemon=True)
    runner.start()
    runner.join(seconds)
    if runner.is_alive():
        pytest.fail(f"The TV step hung: it was still running {seconds} s after its time limit ran out")
    if "error" in answer:
        raise answer["error"]
    return answer["it"]


def _running_with(marker, within=10.0):
    """The processes, other than zombies, still running with `marker` in their command after `within` s."""
    deadline = time.monotonic() + within
    while True:
        found = []
        for proc in pathlib.Path("/proc").glob("[0-9]*"):
            try:
                if marker in proc.joinpath("cmdline").read_text() and "State:\tZ" not in (
                    proc.joinpath("status").read_text()
                ):
                    found.append(proc.name)
            except OSError:
                continue
        if not found or time.monotonic() >= deadline:
            return found
        time.sleep(0.05)


def _slow_plugin(tmp_path):
    """A TV plugin that waits a minute before it answers anything."""
    log = tmp_path / "calls.log"
    log.touch()
    command = [sys.executable, str(FAKE), "on", str(log), "60", str(tmp_path / "pid")]
    return TvPlugin(command=command, tv="the den", device="Xbox"), log


@pytest.fixture
def stop_the_fake(tmp_path):
    """End the stand-in TV plugin whatever the test found, so a failing test leaves nothing running."""
    yield
    pid = tmp_path / "pid"
    if pid.exists():
        with contextlib.suppress(ProcessLookupError, ValueError):
            os.kill(int(pid.read_text()), signal.SIGKILL)


@pytest.mark.usefixtures("stop_the_fake")
def test_a_tv_plugin_whose_start_is_cut_short_is_still_stopped(tmp_path, monkeypatch):
    """The TV's time runs out while its plugin is still starting, as on a busy machine."""
    monkeypatch.setattr(tv, "TV_WITHIN", 0.5)
    plugin, log = _slow_plugin(tmp_path)
    answer = _within(30, show_xbox(plugin, settle=0.0))
    assert answer["outcome"] == "working", answer
    assert not plugin.is_open
    if pathlib.Path("/proc").is_dir():  # where processes can be listed by their command
        left = _running_with(str(log))
        assert not left, f"the TV plugin was left running after its start was cut short: {left}"


@pytest.mark.usefixtures("stop_the_fake")
def test_a_stop_that_fails_never_hides_why_the_start_was_cut_short(tmp_path, monkeypatch):
    monkeypatch.setattr(tv, "TV_WITHIN", 0.5)
    plugin, _ = _slow_plugin(tmp_path)
    stop = TvPlugin.__aexit__

    async def failing_stop(self, *exc):
        try:
            await stop(self, *exc)
        finally:
            failed = "the stop failed"
            raise RuntimeError(failed)  # replaces whatever the stop raised, as a failing stop does

    monkeypatch.setattr(TvPlugin, "__aexit__", failing_stop)
    with pytest.raises(TimeoutError):
        _within(30, _started_within(plugin, 0.5))


async def _started_within(plugin, seconds):
    async with asyncio.timeout(seconds):
        await plugin.__aenter__()


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
