# SPDX-License-Identifier: GPL-3.0-or-later
"""The MCP server: the Xbox's tools, over stdio for a voice session.

Run with ``xbox-mcp serve``; ``xbox-mcp serve --simulate`` answers the same
tools from a pretend Xbox (xbox_mcp.simulator), and every answer then says
"Simulated:" first.
"""

from __future__ import annotations

import asyncio
import functools
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
    "No tool touches the store, purchases, the account, family, privacy, settings, friends or messages, "
    "and none switches a TV on or off."
)
SIMULATED = "A SIMULATED Xbox, not a real one: say so. "
#: The Claude app gives up on a call after about 15 seconds; every tool answers before then.
ANSWER_WITHIN = 12.0
#: What the voice hears when a tool is still working by then; the work carries on.
WORKING = "That is taking a while, and it carries on. Ask again in a moment to hear how it went."
#: What the voice hears when xbox-mcp itself fails, so it never hears only that the tool failed.
BROKE = "That went wrong inside xbox-mcp. Try again, and if it fails again, check its log."

READ_ONLY = ToolAnnotations(read_only_hint=True)
CHANGES = ToolAnnotations(read_only_hint=False)


class _Guarded:
    """Registers each tool so a failure or a slow cloud is still an answer the voice can read."""

    def __init__(self, server: MCPServer[Any], *, simulated: bool) -> None:
        self._server, self._simulated = server, simulated
        #: Work still running after its answer was given, kept until it ends.
        self.running: set[asyncio.Future[Any]] = set()

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
                    work.add_done_callback(_ended)
                    work.add_done_callback(self.running.discard)
                    answer = {"success": False, "outcome": "working", "say": WORKING}
                except Exception:  # noqa: BLE001 - whatever broke, the voice gets a sentence
                    # RULE: every tool answers with a sentence, even when it fails
                    answer = {"success": False, "outcome": "refused", "say": BROKE}
                answer["simulated"] = self._simulated
                # RULE: every simulated answer says simulated first
                if self._simulated:
                    answer["say"] = "Simulated: " + answer["say"]
                return answer

            return register(answered)

        return guard


def _ended(work: asyncio.Future[Any]) -> None:
    """Collect what work left running ended with, so a failure there is not left unread."""
    if not work.cancelled():
        work.exception()


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

    return server
