# SPDX-License-Identifier: GPL-3.0-or-later
"""A stand-in for tv-mcp's server: tv_status, tv_input and a power tool, answering as tv-mcp does.

Run as ``python fake_tv_mcp.py <screen> <log file> [<seconds> <pid file>]``; each call is
appended to the log. With seconds, it writes its process id to the pid file and waits that long
before it answers anything, as a TV plugin starting on a busy machine does.
"""

import json
import os
import pathlib
import sys
import time

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


if len(sys.argv) > 3:
    pathlib.Path(sys.argv[4]).write_text(str(os.getpid()))
    time.sleep(float(sys.argv[3]))
server.run()
