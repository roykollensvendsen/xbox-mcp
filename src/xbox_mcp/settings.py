# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner's settings, read from ``~/.config/xbox-mcp``, never from this repository.

    [xbox]
    console = "Xbox"          # its name on the account; only needed with two consoles

    [launch]
    titles = ["Forza Horizon 5", "Minecraft"]   # the only games xbox_launch may start

    [tv]                      # the TV the console is plugged into, through tv-mcp
    command = ["uv", "run", "--directory", "~/tv-mcp", "tv-mcp", "serve"]
    name = "the living-room TV"   # as tv-mcp knows it
    device = "Xbox"           # the device on its input, as tv-mcp knows it

    [night]                   # when a wake needs that TV already on
    starts = 22
    ends = 7

The Microsoft sign-in tokens sit alone in ``tokens.json`` beside the settings,
readable by the owner only. Only the cloud driver reads them; they are never
logged or shown.
"""

from __future__ import annotations

import os
import pathlib
import tomllib
from dataclasses import dataclass, field
from typing import Any

#: The folder the owner's settings and tokens live in.
FOLDER = pathlib.Path("~/.config/xbox-mcp")
#: The night, from the hour it starts to the hour it ends (ADR-XB-003).
NIGHT = (22, 7)
HOURS_IN_A_DAY = 24


class SettingsError(ValueError):
    """The settings cannot be used as they are."""


@dataclass(frozen=True)
class Settings:
    """Which console, which games, which TV, and when it is night."""

    console: str | None = None
    titles: tuple[str, ...] = ()
    tv: dict[str, Any] = field(default_factory=dict)
    night: tuple[int, int] = NIGHT


def settings_path() -> pathlib.Path:
    """The settings file: ``$XBOX_MCP_SETTINGS``, or ``~/.config/xbox-mcp/config.toml``."""
    return pathlib.Path(os.environ.get("XBOX_MCP_SETTINGS", str(FOLDER / "config.toml"))).expanduser()


def tokens_path(settings: pathlib.Path) -> pathlib.Path:
    """The Microsoft sign-in tokens, beside the settings file."""
    return settings.with_name("tokens.json")


def _titles(launch: dict[str, Any]) -> tuple[str, ...]:
    titles = launch.get("titles", [])
    if not (isinstance(titles, list) and all(isinstance(t, str) and t.strip() for t in titles)):
        msg = '[launch] titles must be a list of game names, such as ["Minecraft"].'
        raise SettingsError(msg)
    return tuple(t.strip() for t in titles)


def _night(night: dict[str, Any]) -> tuple[int, int]:
    starts, ends = night.get("starts", NIGHT[0]), night.get("ends", NIGHT[1])
    if not all(isinstance(h, int) and 0 <= h < HOURS_IN_A_DAY for h in (starts, ends)):
        msg = "[night] starts and ends are hours of the day, 0 to 23."
        raise SettingsError(msg)
    return int(starts), int(ends)


def load_settings(path: pathlib.Path) -> Settings:
    """The settings in `path`; an absent file means nothing is set."""
    table = tomllib.loads(path.read_text()) if path.is_file() else {}
    console = table.get("xbox", {}).get("console")
    return Settings(
        console=str(console) if console else None,
        titles=_titles(table.get("launch", {})),
        tv=dict(table.get("tv", {})),
        night=_night(table.get("night", {})),
    )
