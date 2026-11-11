# SPDX-License-Identifier: GPL-3.0-or-later
"""Ask the TV plugin about the console's TV, and show the Xbox on it after a wake.

ADR-XB-003, after mister-mcp's ADR-MM-002: the TV plugin (tv-mcp) owns the TVs
and their guards. :class:`TvSwitch` is the small interface the rest of the
plugin sees: whether the TV's screen is on, for the night rule, and switching
it to the Xbox after a confirmed wake. It only ever calls ``tv_status`` and
``tv_input``, so it can never switch a TV on or off.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Self

#: Seconds a TV step may take, starting the TV plugin and waiting for HDMI-CEC included.
TV_WITHIN = 6.0
#: Seconds after a wake for HDMI-CEC to bring the TV's screen on.
CEC_SETTLE = 4.0
#: What the voice hears when the TV did not come on after a wake.
NOT_ON = "The TV did not come on, so I left it alone. Switch it on and to the Xbox with its remote."
#: Seconds between looks at the TV while it comes on.
LOOK_EVERY = 1.0


def screen_of(report: dict[str, Any]) -> str | None:
    """The TV's screen in tv-mcp's ``tv_status`` answer; None unless it names exactly one TV.

    tv-mcp answers with a list, ``tvs``, of one entry per TV asked about. Its
    screen is ``screen`` where the TV reports one, else ``state``.
    """
    tvs = report.get("tvs")
    if not (isinstance(tvs, list) and len(tvs) == 1 and isinstance(tvs[0], dict)):
        return None
    value = tvs[0].get("screen", tvs[0].get("state"))
    return value if isinstance(value, str) else None


def _alone(say: str) -> dict[str, Any]:
    return {"success": False, "outcome": "left_alone", "say": say}


class TvSwitch:
    """The TV the console is plugged into, reached through the TV plugin's own tools."""

    #: The only TV tools this plugin ever calls.
    TOOLS: ClassVar[tuple[str, ...]] = ("tv_status", "tv_input")

    def __init__(self, *, tv: str, device: str) -> None:
        """The TV the owner calls `tv`, whose input with the console is called `device`."""
        self.tv, self.device = tv, device

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """The TV plugin's answer to `tool`; None, and nothing sent, for a tool not listed."""
        # RULE: only the tv status and input tools are ever called
        if tool not in self.TOOLS:
            return None
        return await self._send(tool, arguments)

    async def _send(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def _offered(self) -> set[str]:
        raise NotImplementedError

    async def is_on(self) -> bool:
        """Whether the TV plugin reports the screen on."""
        report = await self.call("tv_status", {"tv": self.tv}) or {}
        return screen_of(report) == "on"

    async def show_xbox(self, settle: float) -> dict[str, Any]:
        """Wait up to `settle` seconds for the screen to come on, then switch it to the Xbox."""
        if "tv_input" not in await self._offered():
            return _alone(
                "The TV plugin does not offer to change inputs, so I left the TV alone. "
                "Switch it to the Xbox with its remote."
            )
        waited = 0.0
        while not await self.is_on():
            if waited >= settle:
                # RULE: a tv that is not on is never sent an input
                return _alone(NOT_ON)
            await asyncio.sleep(LOOK_EVERY)
            waited += LOOK_EVERY
        answer = await self.call("tv_input", {"source": self.device, "tv": self.tv}) or {}
        return {
            "success": bool(answer.get("success")),
            "outcome": str(answer.get("outcome", "refused")),
            "say": str(answer.get("say", "The TV did not say what it shows.")),
        }

    async def __aenter__(self) -> Self:
        """Nothing to open."""
        return self

    async def __aexit__(self, *_: object) -> None:
        """Nothing to close."""


class SimulatedTvPlugin(TvSwitch):
    """A pretend TV plugin, answering as tv-mcp does, that records what it was asked."""

    def __init__(
        self, *, screen: str | None = "off", tv: str = "the living-room TV", device: str = "Xbox"
    ) -> None:
        """A TV whose screen reads `screen`; None is a TV plugin that cannot tell."""
        super().__init__(tv=tv, device=device)
        self.screen = screen
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def cec_on(self) -> None:
        """What HDMI-CEC does when the console wakes: the screen comes on."""
        self.screen = "on"

    async def _offered(self) -> set[str]:
        return {"tv_status", "tv_input"}

    async def _send(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool, arguments))
        if tool == "tv_status":
            if self.screen is None:
                return {"success": False, "outcome": "unreachable", "say": "..."}
            return {"success": True, "say": "...", "tvs": [{"tv": self.tv, "state": self.screen}]}
        if self.screen != "on":
            return {
                "success": False,
                "outcome": "refused",
                "say": "The TV is not on, so its input was left alone.",
            }
        return {"success": True, "outcome": "done", "say": f"The TV shows the {self.device} now."}


async def screen_on(tv: TvSwitch, within: float = TV_WITHIN) -> bool | None:
    """Whether the TV's screen is on; None when the TV plugin cannot be asked in time."""
    try:
        async with asyncio.timeout(within):
            async with tv:
                return await tv.is_on()
    except Exception:  # noqa: BLE001 - an unknown TV is said as unknown, never raised
        return None


async def show_xbox(tv: TvSwitch, settle: float = CEC_SETTLE) -> dict[str, Any]:
    """The TV step after a confirmed wake, inside its own deadline, the TV plugin closed afterwards."""
    try:
        async with asyncio.timeout(TV_WITHIN):
            async with tv:
                return await tv.show_xbox(settle)
    except TimeoutError:
        return {
            "success": False,
            "outcome": "working",
            "say": "The TV did not answer in time. Ask what it shows, or switch it with its remote.",
        }
    except Exception:  # noqa: BLE001 - the console woke; a TV failure is said, not raised
        return {
            "success": False,
            "outcome": "refused",
            "say": (
                "I could not reach the TV plugin, so I could not switch the TV. "
                "Check the [tv] command in ~/.config/xbox-mcp/config.toml, or use the TV's remote."
            ),
        }
