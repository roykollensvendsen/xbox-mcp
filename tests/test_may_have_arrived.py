# SPDX-License-Identifier: GPL-3.0-or-later
"""A command whose answer never came back may have reached the Xbox, so its yes is spent.

The cloud driver sends a wake, a sleep or a Home, and Microsoft may take it and
then not answer in time. The plugin cannot tell that apart from a command that
never left, so it treats it as sent: it reads the console back, and the same
yes never sends it a second time. Only a command that never reached Microsoft
(the connection itself failed) leaves the yes open to try again.

These tests drive the real service over the real cloud driver, with a stand-in
for the library's client underneath, so no test reaches Microsoft.
"""

import asyncio

import httpx
import pytest
from pythonxbox.api.provider.smartglass.models import (
    CommandResponse,
    InstalledPackagesList,
    SmartglassConsoleList,
    SmartglassConsoleStatus,
)
from test_cloud import APP, CONSOLE, OK, STATUS

from xbox_mcp.console import MayHaveArrivedError, UnreachableError
from xbox_mcp.drivers import cloud
from xbox_mcp.drivers.cloud import CloudConsole
from xbox_mcp.service import XboxService
from xbox_mcp.settings import Settings

MINECRAFT = APP["aumid"]
COMMANDS = ("wake_up", "turn_off", "go_home")


class Microsoft:
    """The library's client: answers reads, and fails each command the way it is told to."""

    def __init__(self, power, *, failure, fail_times=99):
        self.power, self.failure, self.fail_times = power, failure, fail_times
        self.sent: list[str] = []

    def __getattr__(self, name):
        return _Path(self, name)


class _Path:
    def __init__(self, microsoft, path):
        self._microsoft, self._path = microsoft, path

    def __getattr__(self, name):
        return _Path(self._microsoft, f"{self._path}.{name}")

    async def __call__(self, *_args, **_kwargs):
        microsoft, method = self._microsoft, self._path.rsplit(".", 1)[-1]
        if method == "get_console_list":
            listed = [{**CONSOLE, "powerState": microsoft.power}]
            return SmartglassConsoleList.model_validate({"result": listed, "status": OK})
        if method == "get_console_status":
            focus = MINECRAFT if microsoft.power == "On" else ""
            status = {**STATUS, "powerState": microsoft.power, "focusAppAumid": focus}
            return SmartglassConsoleStatus.model_validate(status)
        if method == "get_installed_apps":
            return InstalledPackagesList.model_validate({"result": [APP], "status": OK})
        assert method in COMMANDS, method
        return await self._command(method)

    async def _command(self, method):
        microsoft = self._microsoft
        if microsoft.fail_times > 0:
            microsoft.fail_times -= 1
            if isinstance(microsoft.failure, httpx.ConnectError):
                raise microsoft.failure  # it never left this machine
            microsoft.sent.append(method)  # it reached Microsoft ...
            if microsoft.failure == "slow":
                await asyncio.sleep(1)  # ... and the answer is slower than the plugin waits
            raise microsoft.failure  # ... and the answer was lost
        microsoft.sent.append(method)
        return CommandResponse.model_validate({"opId": "op1", "status": OK, "destination": None})


async def _no_wait(_seconds):
    return None


def _service(microsoft):
    return XboxService(CloudConsole(client=microsoft), Settings(titles=("Minecraft",)), wait=_no_wait)


LOST = [
    pytest.param(httpx.ReadTimeout("no answer"), id="the-answer-timed-out"),
    pytest.param(httpx.RemoteProtocolError("cut off"), id="the-answer-was-cut-off"),
    pytest.param("slow", id="slower-than-the-plugin-waits"),
]


@pytest.fixture
def short_wait(monkeypatch):
    """The plugin waits a hundredth of a second for a call, so a slow answer is late."""
    monkeypatch.setattr(cloud, "CALL_WITHIN", 0.01)


def _yes_twice(request, confirm):
    async def run():
        asked = await request()
        assert asked["outcome"] == "asked", asked
        first = await confirm(asked["token"])
        return first, await confirm(asked["token"])

    return asyncio.run(run())


@pytest.mark.usefixtures("short_wait")
@pytest.mark.parametrize("failure", LOST)
@pytest.mark.parametrize(
    ("power", "action", "method"), [("ConnectedStandby", "wake", "wake_up"), ("On", "sleep", "turn_off")]
)
def test_a_power_command_that_may_have_arrived_is_never_sent_again(failure, power, action, method):
    microsoft = Microsoft(power, failure=failure)
    service = _service(microsoft)
    first, second = _yes_twice(lambda: service.power_request(action), service.power_confirm)
    assert microsoft.sent == [method]
    assert first["outcome"] == "not_confirmed", first
    assert "not confirmed" in first["say"]
    assert second["outcome"] == "refused", second


@pytest.mark.usefixtures("short_wait")
@pytest.mark.parametrize("failure", LOST)
def test_a_stop_that_may_have_arrived_is_never_sent_again(failure):
    microsoft = Microsoft("On", failure=failure)
    service = _service(microsoft)
    first, second = _yes_twice(service.home_request, service.home_confirm)
    assert microsoft.sent == ["go_home"]
    assert first["outcome"] == "not_confirmed", first
    assert second["outcome"] == "refused", second


def test_a_wake_that_never_reached_microsoft_can_be_confirmed_again():
    microsoft = Microsoft("ConnectedStandby", failure=httpx.ConnectError("no route"), fail_times=1)
    service = _service(microsoft)
    first, second = _yes_twice(lambda: service.power_request("wake"), service.power_confirm)
    assert first["outcome"] == "unreachable", first
    assert microsoft.sent == ["wake_up"]
    assert second["outcome"] == "not_confirmed", second  # sent once; the stand-in never wakes


@pytest.mark.parametrize(
    ("failure", "never_sent"),
    [
        (httpx.ConnectError("no route"), True),
        (httpx.ConnectTimeout("no route"), True),
        (httpx.ReadTimeout("no answer"), False),
        (httpx.RemoteProtocolError("cut off"), False),
        (TimeoutError(), False),
    ],
)
def test_only_a_connection_that_never_opened_is_said_as_never_sent(failure, never_sent):
    driver = CloudConsole(client=Microsoft("On", failure=failure))
    with pytest.raises(UnreachableError) as raised:
        asyncio.run(driver.sleep("F4000000000000A1"))
    assert isinstance(raised.value, MayHaveArrivedError) is not never_sent
