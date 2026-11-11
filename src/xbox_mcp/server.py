# SPDX-License-Identifier: GPL-3.0-or-later
"""The MCP server: the Xbox's tools, over stdio for a voice session.

Run with ``xbox-mcp serve``; ``xbox-mcp serve --simulate`` answers the same
tools from a pretend Xbox (xbox_mcp.simulator), and every answer then says
"Simulated:" first.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import logging.handlers
import os
import pathlib
import sys
from typing import TYPE_CHECKING, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from xbox_mcp.service import XboxService

INSTRUCTIONS = (
    "Says whether the owner's Xbox is on, asleep or off, and what it runs. "
    "Read each 'say' aloud as it is. "
    "Waking or sleeping it needs the owner's spoken yes: read the question aloud, and confirm only if "
    "the owner clearly says yes. At night it is not woken unless its TV is already on. "
    "Stopping a game, and starting one over a running game, also asks first: read the question, which "
    "names the game, and confirm with its token only on a clear yes. Stopping sends the Xbox to Home; "
    "the game stays paused in the background. "
    "No tool touches the store, purchases, the account, family, privacy, settings, friends or messages, "
    "and none switches a TV on or off."
)
SIMULATED = "A SIMULATED Xbox, not a real one: say so. "
#: The Claude app gives up on a call after about 15 seconds; every tool answers before then.
ANSWER_WITHIN = 12.0
#: What the voice hears when a tool is still working by then; the work carries on.
WORKING = "That is taking a while, and it carries on. Ask again in a moment to hear how it went."
#: Where xbox-mcp writes why it broke; $XBOX_MCP_LOG overrides it.
LOG_FILE = pathlib.Path(os.environ.get("XBOX_MCP_LOG", "~/.local/state/xbox-mcp/xbox-mcp.log")).expanduser()
#: What the voice hears when xbox-mcp itself fails, so it never hears only that the tool failed.
BROKE = (
    "That went wrong inside xbox-mcp. Try again, and if it fails again, the reason is written in "
    + str(LOG_FILE).replace(str(pathlib.Path.home()), "~", 1)
    + "."
)
LOG = logging.getLogger("xbox_mcp")

READ_ONLY = ToolAnnotations(read_only_hint=True)
CHANGES = ToolAnnotations(read_only_hint=False)


class _Guarded:
    """Registers each tool so a failure or a slow cloud is still an answer the voice can read."""

    def __init__(self, server: MCPServer[Any], *, simulated: bool) -> None:
        self._server, self._simulated = server, simulated
        #: Work still running after its answer was given, kept until it ends.
        self.running: set[asyncio.Future[Any]] = set()
        #: What work that ran past its deadline ended with, said at the start of the next answer.
        self.late: list[str] = []

    def tool(self, **options: Any) -> Callable[[Callable[..., Awaitable[Any]]], Any]:  # noqa: ANN401
        register = self._server.tool(**options)

        def guard(tool: Callable[..., Awaitable[dict[str, Any]]]) -> Any:  # noqa: ANN401
            @functools.wraps(tool)
            async def answered(*args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
                work = asyncio.ensure_future(tool(*args, **kwargs))
                try:
                    # RULE: a tool still working at the deadline says it carries on
                    answer = await asyncio.wait_for(asyncio.shield(work), ANSWER_WITHIN)
                except TimeoutError:
                    self.running.add(work)
                    work.add_done_callback(self._ended)
                    work.add_done_callback(self.running.discard)
                    answer = {"success": False, "outcome": "working", "say": WORKING}
                except Exception:  # whatever broke, the voice gets a sentence
                    # RULE: every tool answers with a sentence, even when it fails
                    LOG.exception("%s broke", tool.__name__)
                    answer = {"success": False, "outcome": "refused", "say": BROKE}
                answer["say"] = self._earlier() + answer["say"]
                answer["simulated"] = self._simulated
                # RULE: every simulated answer says simulated first
                if self._simulated:
                    answer["say"] = "Simulated: " + answer["say"]
                return answer

            return register(answered)

        return guard

    def _earlier(self) -> str:
        """What work that ran past its deadline ended with, once, to go before the next answer."""
        said = "".join(f"Earlier: {late} " for late in self.late)
        self.late.clear()
        return said

    def _ended(self, work: asyncio.Future[Any]) -> None:
        """Keep what work that ran past its deadline ended with, so the next answer says it."""
        if work.cancelled():
            return
        error = work.exception()
        if error is not None:
            LOG.error("work past the deadline broke", exc_info=error)
            self.late.append(BROKE)
            return
        said = work.result().get("say")
        if isinstance(said, str):
            self.late.append(said)


def log_to(path: pathlib.Path = LOG_FILE) -> logging.Handler:
    """Write xbox-mcp's log to `path`, readable by the owner alone, and to stderr; the file's handler."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2)
    form = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for each in (handler, logging.StreamHandler(sys.stderr)):
        each.setFormatter(form)
        LOG.addHandler(each)
    LOG.setLevel(logging.INFO)
    return handler


def build_server(service: XboxService, *, simulated: bool = False) -> MCPServer[Any]:
    """The MCP server with the Xbox's tools."""
    server: MCPServer[Any] = MCPServer(
        name="xbox-simulated" if simulated else "xbox",
        instructions=(SIMULATED + INSTRUCTIONS) if simulated else INSTRUCTIONS,
    )
    guarded = _Guarded(server, simulated=simulated)

    @guarded.tool(
        annotations=READ_ONLY,
        description=(
            "Is it on, and what runs?\nWhether the Xbox is on, asleep or off, and the game or app it runs."
        ),
    )
    async def xbox_status() -> dict[str, Any]:
        return await service.status()

    @guarded.tool(
        annotations=CHANGES,
        description=(
            "Asks before wake or sleep.\nAction: wake or sleep. Sends nothing: read the question in 'say' "
            "aloud, and pass its token to xbox_power_confirm only on the owner's clear yes. At night a wake "
            "is refused unless the TV is already on."
        ),
    )
    async def xbox_power_request(action: str) -> dict[str, Any]:
        return await service.power_request(action)

    @guarded.tool(
        annotations=CHANGES,
        description=(
            "Carry out the owner's yes.\nToken: from the latest xbox_power_request, used once, within 30"
            " seconds. Wakes or sleeps the Xbox, says 'not confirmed' until its status shows it, and after"
            " a wake switches the TV to the Xbox."
        ),
    )
    async def xbox_power_confirm(token: str) -> dict[str, Any]:
        return await service.power_confirm(token)

    @guarded.tool(
        annotations=CHANGES,
        description=(
            "Start a listed game.\nTitle: words of a game on the owner's own list. Only an installed title"
            " on that list starts, only while the Xbox is on, and the answer says 'not confirmed' until the"
            " Xbox shows it."
        ),
    )
    async def xbox_launch(title: str) -> dict[str, Any]:
        return await service.launch(title)

    @guarded.tool(
        annotations=CHANGES,
        description=(
            "Replace the running game.\nToken: from the latest xbox_launch that asked, used once,"
            " within 30 seconds, only on the owner's clear yes. Checked again: if what runs changed,"
            " nothing starts."
        ),
    )
    async def xbox_launch_confirm(token: str) -> dict[str, Any]:
        return await service.launch_confirm(token)

    @guarded.tool(
        annotations=CHANGES,
        description=(
            "Stop a game, asking first.\nNames the listed game in front and asks whether to send the Xbox"
            " to Home, where the game stays paused in the background. Sends nothing: read the question"
            " aloud, and pass its token to xbox_home_confirm only on the owner's clear yes."
        ),
    )
    async def xbox_home_request() -> dict[str, Any]:
        return await service.home_request()

    @guarded.tool(
        annotations=CHANGES,
        description=(
            "Send the Xbox to Home.\nToken: from the latest xbox_home_request, used once, within 30"
            " seconds. Checked again: if another game is in front now, nothing is sent. Says 'not"
            " confirmed' until Home shows."
        ),
    )
    async def xbox_home_confirm(token: str) -> dict[str, Any]:
        return await service.home_confirm(token)

    return server
