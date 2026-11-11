# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line for xbox-mcp: serve the tools to a voice session, or ask the Xbox."""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

from xbox_mcp.console import Cloud, NoCloud
from xbox_mcp.drivers.tv_plugin import TvPlugin
from xbox_mcp.server import build_server
from xbox_mcp.service import XboxService
from xbox_mcp.settings import Settings, SettingsError, load_settings, settings_path
from xbox_mcp.simulator import SimulatedCloud
from xbox_mcp.tv import SimulatedTvPlugin, TvSwitch

#: Every verb this command has. The documentation test requires each one to
#: appear in a worked example, so a verb cannot be added without one.
COMMANDS: tuple[str, ...] = ("serve", "status")


def build_service(*, simulate: bool, settings_file: str | None) -> XboxService:
    """The service a command or a voice session uses: the pretend Xbox, or the owner's."""
    if simulate:
        cloud, tv = SimulatedCloud(), SimulatedTvPlugin(screen="off")
        cloud.on_wake = tv.cec_on  # as HDMI-CEC switches the TV on when the console wakes
        return XboxService(cloud, Settings(titles=("Forza Horizon 5", "Minecraft")), tv=tv)
    path = pathlib.Path(settings_file).expanduser() if settings_file else settings_path()
    settings = load_settings(path)
    real: Cloud = NoCloud()
    return XboxService(real, settings, tv=tv_from(settings.tv))


def tv_from(table: dict[str, object]) -> TvSwitch | None:
    """The TV step from the settings' [tv] table; None when there is none."""
    if not table:
        return None
    command, tv = table.get("command"), table.get("name")
    if not (isinstance(command, list) and command and isinstance(tv, str)):
        msg = "[tv] needs command = [...] and name = the TV as the TV plugin knows it."
        raise SettingsError(msg)
    return TvPlugin(command=[str(c) for c in command], tv=tv, device=str(table.get("device", "Xbox")))


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="xbox-mcp", description=__doc__)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--simulate", action="store_true", help="a pretend Xbox, no network")
    parser.add_argument("--settings-file", help="instead of ~/.config/xbox-mcp/config.toml")
    args = parser.parse_args(argv)
    try:
        service = build_service(simulate=args.simulate, settings_file=args.settings_file)
    except SettingsError as error:
        sys.stderr.write(f"xbox-mcp: {error}\n")
        return 2
    if args.command == "serve":  # pragma: no cover - runs until stdin closes
        asyncio.run(build_server(service, simulated=args.simulate).run_stdio_async())
        return 0
    answer = asyncio.run(service.status())
    prefix = "Simulated: " if args.simulate else ""
    sys.stdout.write(prefix + answer["say"] + "\n")
    return 0 if answer["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
