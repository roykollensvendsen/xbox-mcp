# SPDX-License-Identifier: GPL-3.0-or-later
"""A freshly started server lists its tools quickly, and never needs the Xbox to do it.

The bridge starts each plugin as a process of its own and asks it for its tools.
A plugin that does not answer in time is not offered (claude-voice PR 162 waits
60 seconds; it waited 15 before). So listing the tools must not wait on the
Xbox, the TV or the network: an Xbox that is asleep, or a cloud that is down, must still leave
the voice a plugin that says so. Both runs here start the real command, as the
bridge does, from a process that has never imported the package.
"""

import asyncio
import os
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

#: Seconds a cold start may take to list the tools, kept well inside the bridge's 15.
COLD_START_WITHIN = 10.0
TOOLS = {"xbox_status", "xbox_power_request", "xbox_power_confirm"}


def _listed(args, env=None):
    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "xbox_mcp.cli", *args], env=env)
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return {tool.name for tool in (await session.list_tools()).tools}

    started = time.monotonic()
    names = asyncio.run(run())
    return names, time.monotonic() - started


def test_a_cold_start_lists_the_tools_in_time():
    names, seconds = _listed(["serve", "--simulate"])
    assert names == TOOLS
    assert seconds <= COLD_START_WITHIN


def test_a_cold_start_never_waits_on_the_xbox(tmp_path):
    # No tokens beside the settings, and a TV plugin that does not exist:
    # neither the cloud nor the TV may be asked before the first call.
    settings = tmp_path / "config.toml"
    settings.write_text('[tv]\ncommand = ["no-such-tv-plugin"]\nname = "the den"\n')
    env = {**os.environ, "HOME": str(tmp_path)}
    names, seconds = _listed(["serve", "--settings-file", str(settings)], env=env)
    assert names == TOOLS
    assert seconds <= COLD_START_WITHIN
