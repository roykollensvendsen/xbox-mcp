# SPDX-License-Identifier: GPL-3.0-or-later
"""The TV plugin's own MCP server, started over stdio for one question or one wake.

ADR-XB-003, as mister-mcp's ADR-MM-002 does it. Only the calls that
:class:`xbox_mcp.tv.TvSwitch` lets through reach it: ``tv_status`` and
``tv_input``.
"""

from __future__ import annotations

import json
import pathlib
import shutil
from typing import Any, Self

from xbox_mcp.tv import TvSwitch


class TvPlugin(TvSwitch):
    """The TV plugin's own MCP server, started over stdio for one use and closed after it."""

    def __init__(self, *, command: list[str], tv: str, device: str) -> None:
        """Start the TV plugin with `command`, such as ``uv run --directory ~/tv-mcp tv-mcp serve``."""
        super().__init__(tv=tv, device=device)
        self._command = [
            str(pathlib.Path(part).expanduser()) if part.startswith("~") else part for part in command
        ]
        self._local_bin = str(pathlib.Path("~/.local/bin").expanduser())
        self._stack: Any = None
        self._session: Any = None

    async def __aenter__(self) -> Self:
        """Start the TV plugin and open an MCP session with it."""
        from contextlib import AsyncExitStack  # noqa: PLC0415 - only when a TV is used

        from mcp import ClientSession, StdioServerParameters  # noqa: PLC0415
        from mcp.client.stdio import stdio_client  # noqa: PLC0415

        program = shutil.which(self._command[0]) or shutil.which(self._command[0], path=self._local_bin)
        params = StdioServerParameters(command=program or self._command[0], args=self._command[1:])
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    @property
    def is_open(self) -> bool:
        """Whether the TV plugin is running for this plugin now."""
        return self._session is not None or self._stack is not None

    async def __aexit__(self, *_: object) -> None:
        """Close the session and stop the TV plugin, after every use."""
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = self._session = None

    async def _offered(self) -> set[str]:
        listed = await self._session.list_tools()
        return {tool.name for tool in listed.tools}

    async def _send(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._session.call_tool(tool, arguments)
        if isinstance(result.structured_content, dict):
            return dict(result.structured_content)
        for part in result.content:
            text = getattr(part, "text", None)
            if text:
                try:
                    loaded = json.loads(text)
                except ValueError:
                    return {"say": text}
                return loaded if isinstance(loaded, dict) else {"say": text}
        return {}
