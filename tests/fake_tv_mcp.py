# SPDX-License-Identifier: GPL-3.0-or-later
"""A stand-in for tv-mcp's server: tv_status, tv_input and a power tool, answering as tv-mcp does.

Run as ``python fake_tv_mcp.py <screen> <log file>``; each call is appended to the log.
"""

import json
import pathlib
import sys

from mcp.server.mcpserver import MCPServer

SCREEN, LOG = sys.argv[1], pathlib.Path(sys.argv[2])
server = MCPServer(name="tv")


def _log(tool, **arguments):
    with LOG.open("a") as log:
        log.write(json.dumps([tool, arguments]) + "\n")


@server.tool()
async def tv_status(tv: str) -> dict:
    _log("tv_status", tv=tv)
    return {"success": True, "say": "...", "tvs": [{"tv": "the living-room TV", "state": SCREEN}]}


@server.tool()
async def tv_input(source: str, tv: str) -> dict:
    _log("tv_input", source=source, tv=tv)
    return {"success": True, "outcome": "done", "say": f"{tv} shows HDMI 3, the {source}, now."}


@server.tool()
async def tv_power_request(tv: str) -> dict:
    _log("tv_power_request", tv=tv)
    return {"success": True, "outcome": "asked", "say": "Switch it on?"}


server.run()
