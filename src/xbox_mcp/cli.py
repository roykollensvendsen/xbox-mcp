# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line for xbox-mcp: serve the tools to a voice session, or ask the Xbox."""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

from xbox_mcp.drivers.cloud import CloudConsole, sign_in
from xbox_mcp.drivers.tv_plugin import TvPlugin
from xbox_mcp.server import build_server, log_to
from xbox_mcp.service import XboxService
from xbox_mcp.settings import Settings, SettingsError, load_settings, settings_path, tokens_path
from xbox_mcp.simulator import SimulatedCloud
from xbox_mcp.tv import SimulatedTvPlugin, TvSwitch

#: Every verb this command has. The documentation test requires each one to
#: appear in a worked example, so a verb cannot be added without one.
COMMANDS: tuple[str, ...] = ("serve", "status", "sign-in")


def build_service(*, simulate: bool, settings_file: str | None) -> XboxService:
    """The service a command or a voice session uses: the pretend Xbox, or the owner's."""
    if simulate:
        cloud, tv = SimulatedCloud(), SimulatedTvPlugin(screen="off")
        cloud.on_wake = tv.cec_on  # as HDMI-CEC switches the TV on when the console wakes
        return XboxService(cloud, Settings(titles=("Forza Horizon 5", "Minecraft")), tv=tv)
    path = pathlib.Path(settings_file).expanduser() if settings_file else settings_path()
    settings = load_settings(path)
    # Nothing is read or asked of Microsoft until the first call, so a cold start lists the tools at once.
    return XboxService(CloudConsole(tokens=tokens_path(path)), settings, tv=tv_from(settings.tv))


def tv_from(table: dict[str, object]) -> TvSwitch | None:
    """The TV step from the settings' [tv] table; None when there is none."""
    if not table:
        return None
    command, tv = table.get("command"), table.get("name")
    if not (isinstance(command, list) and command and isinstance(tv, str)):
        msg = "[tv] needs command = [...] and name = the TV as the TV plugin knows it."
        raise SettingsError(msg)
    return TvPlugin(command=[str(c) for c in command], tv=tv, device=str(table.get("device", "Xbox")))


def _sign_in(args: argparse.Namespace) -> int:  # pragma: no cover - the owner signs in, in a browser
    if args.simulate:
        sys.stderr.write("xbox-mcp: sign-in is for the real Xbox; leave out --simulate.\n")
        return 2
    path = pathlib.Path(args.settings_file).expanduser() if args.settings_file else settings_path()

    def say(text: str) -> None:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    return 0 if asyncio.run(sign_in(tokens_path(path), say, other_device=args.other_device)) else 1


def parser() -> argparse.ArgumentParser:
    """The command line's arguments."""
    found = argparse.ArgumentParser(prog="xbox-mcp", description=__doc__)
    found.add_argument("command", choices=COMMANDS)
    found.add_argument("--simulate", action="store_true", help="a pretend Xbox, no network")
    found.add_argument("--settings-file", help="instead of ~/.config/xbox-mcp/config.toml")
    found.add_argument(
        "--other-device", action="store_true", help="sign-in: open the link on another device, then paste"
    )
    return found


async def _serve(
    service: XboxService, *, simulated: bool
) -> None:  # pragma: no cover - runs until stdin closes
    """Serve the tools, looking the console up in the background meanwhile."""
    log_to()
    warming = asyncio.create_task(service.warm())
    try:
        await build_server(service, simulated=simulated).run_stdio_async()
    finally:
        warming.cancel()


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    args = parser().parse_args(argv)
    if args.command == "sign-in":  # pragma: no cover - the owner signs in, in a browser
        return _sign_in(args)
    try:
        service = build_service(simulate=args.simulate, settings_file=args.settings_file)
    except SettingsError as error:
        sys.stderr.write(f"xbox-mcp: {error}\n")
        return 2
    if args.command == "serve":  # pragma: no cover - runs until stdin closes
        asyncio.run(_serve(service, simulated=args.simulate))
        return 0
    answer = asyncio.run(service.status())
    prefix = "Simulated: " if args.simulate else ""
    sys.stdout.write(prefix + answer["say"] + "\n")
    return 0 if answer["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
