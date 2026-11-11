# SPDX-License-Identifier: GPL-3.0-or-later
"""The cloud driver makes only the library calls on ADR-XB-001's allow-list.

A stand-in for the library's client records every attribute path the driver
touches, and answers with the library's own models, so no test reaches
Microsoft. A test fails on any call outside the allow-list; the guard itself
refuses one; and the source names no other part of the library.
"""

import ast
import asyncio
import json
import os
import pathlib

import httpx
import pytest
from pythonxbox.api.provider.smartglass.models import (
    CommandResponse,
    InstalledPackagesList,
    SmartglassConsoleList,
    SmartglassConsoleStatus,
)

from xbox_mcp.console import HOME_AUMID, SignedOutError, UnreachableError
from xbox_mcp.drivers import cloud
from xbox_mcp.drivers.cloud import ALLOWED, AllowList, CloudConsole, NotAllowedError

SOURCE = pathlib.Path(cloud.__file__)
OK = {"errorCode": "OK", "errorMessage": None}
CONSOLE = {
    "id": "F4000000000000A1",
    "name": "Xbox",
    "consoleType": "XboxSeriesX",
    "powerState": "ConnectedStandby",
    "consoleStreamingEnabled": False,
    "digitalAssistantRemoteControlEnabled": False,
    "remoteManagementEnabled": True,
}
STATUS = {
    "powerState": "On",
    "consoleStreamingEnabled": False,
    "digitalAssistantRemoteControlEnabled": False,
    "remoteManagementEnabled": True,
    "focusAppAumid": HOME_AUMID,
    "isTvConfigured": True,
    "playbackState": "Unknown",
    "status": OK,
}
APP = {
    "oneStoreProductId": "9MVXMVT8ZKWC",
    "titleId": 1,
    "aumid": "Microsoft.MinecraftUWPConsole_8wekyb3d8bbwe!Game",
    "name": "Minecraft",
    "contentType": "Game",
    "storageDeviceId": "S1",
    "uniqueId": "U1",
    "version": 1,
    "sizeInBytes": 1,
    "installTime": "2026-01-01T00:00:00Z",
}


def _command(code="OK"):
    return CommandResponse.model_validate(
        {"opId": "op1", "status": {"errorCode": code, "errorMessage": None}, "destination": None}
    )


class Recorder:
    """The library's client, as far as the driver may see it, recording every path touched."""

    def __init__(self, path="", log=None, *, power="On", code="OK", fails=None):
        self._path, self._log = path, log if log is not None else []
        self._power, self._code, self._fails = power, code, fails

    def __getattr__(self, name):
        path = f"{self._path}.{name}" if self._path else name
        return Recorder(path, self._log, power=self._power, code=self._code, fails=self._fails)

    async def __call__(self, *_args, **_kwargs):
        self._log.append(self._path)
        if self._fails:
            raise self._fails
        method = self._path.rsplit(".", 1)[-1]
        if method == "get_console_list":
            return SmartglassConsoleList.model_validate({"result": [CONSOLE], "status": OK})
        if method == "get_console_status":
            return SmartglassConsoleStatus.model_validate({**STATUS, "powerState": self._power})
        if method == "get_installed_apps":
            return InstalledPackagesList.model_validate({"result": [APP], "status": OK})
        return _command(self._code)


def _driver(**recorder):
    log = []
    return CloudConsole(client=Recorder(log=log, **recorder)), log


def _every_call(driver):
    async def run():
        consoles = await driver.consoles()
        console_id = consoles[0].id
        await driver.status(console_id)
        await driver.apps(console_id)
        await driver.wake(console_id)
        await driver.sleep(console_id)
        await driver.launch(console_id, "9MVXMVT8ZKWC")

    asyncio.run(run())


def test_a_test_fails_on_any_library_call_outside_the_allow_list():
    driver, log = _driver()
    _every_call(driver)
    assert log, "the stand-in saw no call; the driver did not use it"
    assert set(log) <= ALLOWED, set(log) - ALLOWED


def test_only_an_allow_listed_library_call_is_ever_made():
    guarded = AllowList(Recorder())
    for path in ("message.send_message", "people.get_friends_own", "account.change_gamertag", "catalog"):
        provider, _, method = path.partition(".")
        with pytest.raises(NotAllowedError):
            getattr(getattr(guarded, provider), method) if method else getattr(guarded, provider).x
    with pytest.raises(NotAllowedError):
        guarded.smartglass.press_button  # noqa: B018
    with pytest.raises(NotAllowedError):
        guarded.session  # noqa: B018


def test_the_allow_list_has_nothing_for_the_store_account_friends_or_messages():
    for path in ALLOWED:
        provider, method = path.split(".")
        assert provider == "smartglass", path
        assert method in {
            "get_console_list",
            "get_console_status",
            "get_installed_apps",
            "wake_up",
            "turn_off",
            "launch_app",
        }, path


def test_the_driver_source_names_no_other_part_of_the_library():
    tree = ast.parse(SOURCE.read_text())
    imported = {
        f"{node.module}.{alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("pythonxbox")
        for alias in node.names
    }
    assert imported <= {
        "pythonxbox.api.client.XboxLiveClient",
        "pythonxbox.authentication.manager.AuthenticationManager",
        "pythonxbox.authentication.models.OAuth2TokenResponse",
        "pythonxbox.common.signed_session.SignedSession",
    }, imported
    providers = {
        "smartglass",
        "people",
        "message",
        "account",
        "catalog",
        "presence",
        "achievements",
        "titlehub",
    }
    named = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in providers}
    assert named <= {"smartglass"}, named


@pytest.mark.parametrize(
    ("cloud_power", "power"),
    [
        ("On", "on"),
        ("ConnectedStandby", "asleep"),
        ("SystemUpdate", "updating"),
        ("Off", "off"),
        ("Unknown", "unknown"),
    ],
)
def test_the_clouds_power_states_are_read_as_the_plugin_speaks_of_them(cloud_power, power):
    driver, _ = _driver(power=cloud_power)
    assert asyncio.run(driver.status("F4000000000000A1")).power == power


def test_installed_apps_are_read_with_their_store_ids():
    driver, _ = _driver()
    (app,) = asyncio.run(driver.apps("F4000000000000A1"))
    assert (app.name, app.product_id) == ("Minecraft", "9MVXMVT8ZKWC")


def test_a_command_the_cloud_refuses_is_not_taken():
    driver, _ = _driver(code="RemoteManagementDisabled")
    sent = asyncio.run(driver.wake("F4000000000000A1"))
    assert sent.accepted is False
    assert sent.reason == "RemoteManagementDisabled"


def test_a_cloud_that_does_not_answer_is_unreachable():
    driver, _ = _driver(fails=httpx.ConnectTimeout("slow"))
    with pytest.raises(UnreachableError):
        asyncio.run(driver.status("F4000000000000A1"))


def test_a_sign_in_microsoft_refuses_is_signed_out():
    response = httpx.Response(401, request=httpx.Request("GET", "https://xccs.xboxlive.com"))
    driver, _ = _driver(fails=httpx.HTTPStatusError("no", request=response.request, response=response))
    with pytest.raises(SignedOutError):
        asyncio.run(driver.consoles())


def test_no_token_file_is_signed_out(tmp_path):
    driver = CloudConsole(tokens=tmp_path / "tokens.json")
    with pytest.raises(SignedOutError) as raised:
        asyncio.run(driver.consoles())
    assert "xbox-mcp sign-in" in str(raised.value.say)


def test_a_token_file_others_can_read_is_refused(tmp_path):
    tokens = tmp_path / "tokens.json"
    tokens.write_text(json.dumps({"placeholder": True}))
    os.chmod(tokens, 0o644)  # noqa: PTH101
    driver = CloudConsole(tokens=tokens)
    with pytest.raises(SignedOutError) as raised:
        asyncio.run(driver.consoles())
    assert "chmod 600" in str(raised.value.say)


def test_tokens_are_written_readable_by_the_owner_alone(tmp_path):
    tokens = tmp_path / "sub" / "tokens.json"
    cloud.write_tokens(tokens, '{"x": 1}')
    assert tokens.stat().st_mode & 0o777 == 0o600
    assert tokens.parent.stat().st_mode & 0o777 == 0o700
