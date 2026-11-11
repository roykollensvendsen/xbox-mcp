# SPDX-License-Identifier: GPL-3.0-or-later
"""No two tool names the voice could mix up (the plugin standard's rule 1).

Names that look or sound alike are listed here on purpose; each pair opens its
description with different words, which is what the voice chooses by.
"""

import asyncio
import difflib

from xbox_mcp.server import build_server
from xbox_mcp.service import XboxService
from xbox_mcp.settings import Settings
from xbox_mcp.simulator import SimulatedCloud

#: Pairs that look or sound alike: the power question and its confirm differ by one word.
CONFUSABLE = {
    frozenset({"xbox_power_request", "xbox_power_confirm"}),
}
#: How alike two names may be, as difflib reckons it, before the pair must be listed.
ALIKE = 0.75


def _tools():
    return asyncio.run(build_server(XboxService(SimulatedCloud(), Settings())).list_tools())


def test_no_two_tool_names_can_be_confused():
    tools = _tools()
    for i, one in enumerate(tools):
        for other in tools[i + 1 :]:
            alike = difflib.SequenceMatcher(None, one.name, other.name).ratio()
            if alike >= ALIKE:
                assert frozenset({one.name, other.name}) in CONFUSABLE, (one.name, other.name, alike)


def test_the_names_that_sound_alike_open_with_different_words():
    openings = {t.name: (t.description or "").split()[0] for t in _tools()}
    for pair in CONFUSABLE:
        one, other = sorted(pair)
        assert openings[one] != openings[other], pair


def test_power_is_switched_only_by_a_question_and_a_confirm():
    power = sorted(t.name for t in _tools() if "power" in t.name or "wake" in t.name or "sleep" in t.name)
    assert power == ["xbox_power_confirm", "xbox_power_request"]
