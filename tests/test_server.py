# SPDX-License-Identifier: GPL-3.0-or-later
"""The MCP tools, as the voice sees them, against the simulated Xbox."""

import asyncio
import re

from xbox_mcp.cli import build_service
from xbox_mcp.server import ANSWER_WITHIN, build_server
from xbox_mcp.service import XboxService
from xbox_mcp.settings import Settings
from xbox_mcp.simulator import SimulatedCloud

#: The tools that change nothing on the console or a TV (the plugin standard's rule 9).
READ_ONLY = {"xbox_status"}
#: The characters of a description the voice saw before loading a tool, 2026-11-10 (tv-mcp).
OPENING_LIMIT = 26


def _server(cloud=None, *, simulated=True):
    return build_server(XboxService(cloud or SimulatedCloud(), Settings()), simulated=simulated)


def _tools(server):
    return {tool.name: tool for tool in asyncio.run(server.list_tools())}


def _call(server, tool, arguments=None):
    return asyncio.run(server.call_tool(tool, arguments or {})).structured_content


def test_every_tool_is_named_after_the_plugin():
    assert all(name.startswith("xbox_") for name in _tools(_server()))


def test_the_tools_that_only_look_are_marked_read_only_and_no_others():
    for name, tool in _tools(_server()).items():
        assert tool.annotations is not None, name
        assert tool.annotations.read_only_hint is (name in READ_ONLY), name


def test_every_tool_opens_with_a_whole_sentence_the_voice_sees_in_full():
    openings = {}
    for name, tool in _tools(_server()).items():
        opening = (tool.description or "").split("\n", 1)[0]
        assert len(opening) <= OPENING_LIMIT, f"{name}: {opening!r}"
        assert re.fullmatch(r"[A-Z][^.?;:]*[.?]", opening), f"{name}: {opening!r}"
        start = " ".join(opening.casefold().split()[:2])
        assert openings.setdefault(start, name) == name, f"{name} and {openings[start]} open alike"


def test_every_simulated_answer_says_simulated_first():
    answer = _call(_server(), "xbox_status")
    assert answer["say"].startswith("Simulated: ")
    assert answer["simulated"] is True


def test_a_real_answer_does_not_say_simulated():
    answer = _call(_server(simulated=False), "xbox_status")
    assert not answer["say"].startswith("Simulated")
    assert answer["simulated"] is False


def test_the_instructions_state_the_safety_rules():
    server = _server(simulated=False)
    text = server.instructions or ""
    for rule in ("yes", "night", "store", "messages"):
        assert rule in text, rule


def test_xbox_status_says_whether_it_is_on():
    answer = _call(_server(SimulatedCloud(power="on")), "xbox_status")
    assert answer["outcome"] == "done"
    assert answer["power"] == "on"


def test_every_tool_answers_with_a_sentence_even_when_it_fails():
    class Broken(SimulatedCloud):
        async def consoles(self):
            raise RuntimeError

    answer = _call(_server(Broken()), "xbox_status")
    assert answer["success"] is False
    assert answer["say"].endswith(".")


def test_a_tool_still_working_at_the_deadline_says_it_carries_on(monkeypatch):
    class Slow(SimulatedCloud):
        async def consoles(self):
            await asyncio.sleep(1)
            return await super().consoles()

    monkeypatch.setattr("xbox_mcp.server.ANSWER_WITHIN", 0.01)
    answer = _call(_server(Slow()), "xbox_status")
    assert answer["outcome"] == "working"
    assert "carries on" in answer["say"]


def test_a_tools_longest_wait_fits_in_the_voices_time():
    voice_limit, margin = 15.0, 2.0
    assert ANSWER_WITHIN + margin <= voice_limit


def test_a_simulated_wake_asks_then_wakes_the_xbox_and_shows_it_on_the_tv():
    service = build_service(simulate=True, settings_file=None)
    service.hour = lambda: 12
    server = build_server(service, simulated=True)

    async def run():
        asked = (await server.call_tool("xbox_power_request", {"action": "wake"})).structured_content
        done = (await server.call_tool("xbox_power_confirm", {"token": asked["token"]})).structured_content
        return asked, done

    asked, done = asyncio.run(run())
    assert asked["say"].startswith("Simulated: The Xbox is asleep.")
    assert done["say"].startswith("Simulated: Woken. The Xbox is on.")
    assert done["tv"]["outcome"] == "done"
