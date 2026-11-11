# SPDX-License-Identifier: GPL-3.0-or-later
"""When xbox-mcp itself breaks, the reason is written down where its answer says.

The voice hears BROKE, which names the log file; the traceback goes to that
file and to stderr. Nothing secret is in a traceback the tools can raise:
the sign-in tokens are only ever read, refreshed and written in the cloud
driver, which never logs them.
"""

import asyncio
import logging

from xbox_mcp import server
from xbox_mcp.server import BROKE, build_server
from xbox_mcp.service import XboxService
from xbox_mcp.settings import Settings
from xbox_mcp.simulator import SimulatedCloud


class Broken(SimulatedCloud):
    async def consoles(self):
        error = "a bug inside the plugin"
        raise RuntimeError(error)


def test_a_broken_tool_says_where_its_reason_is_written():
    assert str(server.LOG_FILE).replace(str(server.LOG_FILE.home()), "~") in BROKE


def test_a_broken_tool_writes_its_traceback_to_the_log(caplog):
    tools = build_server(XboxService(Broken(), Settings()))
    with caplog.at_level(logging.ERROR, logger="xbox_mcp"):
        answer = asyncio.run(tools.call_tool("xbox_status", {})).structured_content
    assert answer["say"] == BROKE
    (record,) = [r for r in caplog.records if r.name == "xbox_mcp"]
    assert "xbox_status" in record.getMessage()
    assert record.exc_info is not None
    assert "a bug inside the plugin" in str(record.exc_info[1])


def test_the_log_goes_to_a_file_readable_by_the_owner_alone(tmp_path):
    path = tmp_path / "state" / "xbox-mcp.log"
    handler = server.log_to(path)
    try:
        logging.getLogger("xbox_mcp").error("written")
        handler.flush()
        assert "written" in path.read_text()
        assert path.stat().st_mode & 0o777 == 0o600
    finally:
        logging.getLogger("xbox_mcp").removeHandler(handler)
        handler.close()
