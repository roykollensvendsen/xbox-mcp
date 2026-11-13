# SPDX-License-Identifier: GPL-3.0-or-later
"""The cloud driver makes only the library calls on ADR-XB-001's allow-list.

A stand-in for the library's client records every attribute path the driver
touches, and answers with the library's own models, so no test reaches
Microsoft. A test fails on any call outside the allow-list; the guard itself
refuses one; and the source names no other part of the library.
"""

import ast
import asyncio
import base64
import hashlib
import json
import os
import pathlib
import re
import threading
from http.server import HTTPServer
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pythonxbox.api.provider.smartglass.models import (
    CommandResponse,
    InstalledPackagesList,
    SmartglassConsoleList,
    SmartglassConsoleStatus,
)
from pythonxbox.common.exceptions import AuthenticationException

from xbox_mcp.console import HOME_AUMID, SIGN_IN_COMMAND, SignedOutError, UnreachableError
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
        await driver.home(console_id)

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
            "go_home",
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
        "pythonxbox.common.exceptions.AuthenticationException",
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


TOKENS = {
    "token_type": "bearer",
    "expires_in": 3600,
    "scope": "Xboxlive.signin Xboxlive.offline_access",
    "access_token": "old-access",
    "refresh_token": "old-refresh",
    "user_id": "u1",
}


class RefreshingAuth:
    """The library's sign-in manager, refreshing to a new token without reaching Microsoft."""

    def __init__(self, *_args):
        self.oauth = None

    async def refresh_tokens(self):
        self.oauth = self.oauth.model_copy(update={"access_token": "new-access"})


def test_tokens_the_library_refreshes_are_written_back_for_the_owner_alone(tmp_path, monkeypatch):
    tokens = tmp_path / "tokens.json"
    cloud.write_tokens(tokens, json.dumps(TOKENS))
    monkeypatch.setattr(cloud, "AuthenticationManager", RefreshingAuth)
    monkeypatch.setattr(cloud, "SignedSession", lambda: None)
    monkeypatch.setattr(cloud, "XboxLiveClient", lambda _auth: Recorder())
    assert asyncio.run(CloudConsole(tokens=tokens).consoles())[0].name == "Xbox"
    assert json.loads(tokens.read_text())["access_token"] == "new-access"  # noqa: S105 - a stand-in, no real token
    assert tokens.stat().st_mode & 0o777 == 0o600


def test_a_damaged_token_file_says_to_sign_in_again(tmp_path):
    tokens = tmp_path / "tokens.json"
    cloud.write_tokens(tokens, "not json")
    with pytest.raises(SignedOutError) as raised:
        asyncio.run(CloudConsole(tokens=tokens).consoles())
    assert "xbox-mcp sign-in" in str(raised.value.say)


def _redirect(query):
    cloud._Callback.code = None  # noqa: SLF001
    server = HTTPServer(("127.0.0.1", 0), cloud._Callback)  # noqa: SLF001
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    reply = httpx.get(f"http://127.0.0.1:{server.server_port}/auth/callback{query}")
    thread.join()
    server.server_close()
    return reply, cloud._Callback.code  # noqa: SLF001


def test_the_sign_in_redirect_takes_the_code_and_shows_no_secret():
    reply, code = _redirect("?code=abc")
    assert code == "abc"
    assert reply.status_code == 200
    assert "abc" not in reply.text


def test_a_redirect_without_a_code_is_a_failed_sign_in():
    reply, code = _redirect("?error=access_denied")
    assert code is None
    assert reply.status_code == 400


def test_the_code_is_read_from_the_address_the_owner_pastes():
    assert cloud.code_from("  http://localhost:8080/auth/callback?code=M.C5_abc&state=x\n") == "M.C5_abc"
    assert cloud.code_from("http://localhost:8080/auth/callback?error=access_denied") is None
    assert cloud.code_from("") is None


def test_a_port_another_program_holds_is_seen_as_held(monkeypatch):
    holder = HTTPServer(("127.0.0.1", 0), cloud._Callback)  # noqa: SLF001
    monkeypatch.setattr(cloud, "REDIRECT_PORT", holder.server_port)
    try:
        assert cloud._listen() is None  # noqa: SLF001
    finally:
        holder.server_close()


def test_a_code_is_never_asked_for_while_another_program_holds_the_redirect_port(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, "PkceAuth", SigningAuth)
    monkeypatch.setattr(cloud, "SignedSession", Session)
    monkeypatch.setattr(cloud, "_listen", lambda: None)
    monkeypatch.setattr(cloud.webbrowser, "open", lambda _url: pytest.fail("opened a browser"))
    said = []
    tokens = tmp_path / "tokens.json"
    ok = asyncio.run(cloud.sign_in(tokens, said.append, ask=lambda _p: pytest.fail("asked for a code")))
    text = "\n".join(said)
    assert ok is False
    assert "login.example.com" not in text, "the link was shown, so it could be opened here"
    assert f"{SIGN_IN_COMMAND} --other-device" in text
    assert not tokens.exists()


def test_the_paste_instructions_say_what_the_owner_will_see_and_do():
    text = cloud.PASTE.format(port=8080)
    assert "http://localhost:8080/" in text
    assert "expected" in text
    assert "not be shown" in text


MADE = []


class SigningAuth(RefreshingAuth):
    """The library's sign-in manager, handing out tokens for a code without reaching Microsoft."""

    def __init__(self, *args, verifier):
        super().__init__(*args)
        self.verifier = verifier
        MADE.append(self)

    def generate_authorization_url(self):
        return "https://login.example.com/authorize"

    async def request_tokens(self, code):
        if code == "spent":
            request = httpx.Request("POST", "https://login.live.com/oauth20_token.srf")
            response = httpx.Response(400, request=request)
            raise httpx.HTTPStatusError(code, request=request, response=response)
        self.oauth = None if code == "refused" else cloud.OAuth2TokenResponse.model_validate(TOKENS)


class Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


OPENED = []


def _sign_in(tmp_path, monkeypatch, pasted):
    monkeypatch.setattr(cloud, "PkceAuth", SigningAuth)
    monkeypatch.setattr(cloud, "SignedSession", Session)
    monkeypatch.setattr(cloud, "XboxLiveClient", lambda _auth: Recorder())
    monkeypatch.setattr(cloud, "_listen", lambda: pytest.fail("listened"))
    monkeypatch.setattr(cloud.webbrowser, "open", OPENED.append)
    said = []
    tokens = tmp_path / "xbox-mcp" / "tokens.json"
    ok = asyncio.run(cloud.sign_in(tokens, said.append, ask=lambda _prompt: pasted, other_device=True))
    return ok, "\n".join(said), tokens


def test_a_sign_in_keeps_the_tokens_for_the_owner_alone_and_never_prints_them(tmp_path, monkeypatch):
    ok, said, tokens = _sign_in(
        tmp_path, monkeypatch, "http://localhost:8080/auth/callback?code=SIGN-IN-CODE-NEVER-SHOWN"
    )
    assert ok is True
    assert "Signed in" in said
    assert "Xbox" in said
    assert str(tokens) in said
    assert tokens.stat().st_mode & 0o777 == 0o600
    for secret in ("old-access", "old-refresh", "SIGN-IN-CODE-NEVER-SHOWN"):
        assert secret not in said


def test_a_sign_in_with_no_code_says_to_run_it_again(tmp_path, monkeypatch):
    ok, said, tokens = _sign_in(tmp_path, monkeypatch, "http://localhost:8080/auth/callback?error=x")
    assert ok is False
    assert f"Run {SIGN_IN_COMMAND} again" in said
    assert not tokens.exists()


def test_a_sign_in_microsoft_refuses_says_to_run_it_again(tmp_path, monkeypatch):
    ok, said, tokens = _sign_in(tmp_path, monkeypatch, "http://localhost:8080/auth/callback?code=refused")
    assert ok is False
    assert f"Run {SIGN_IN_COMMAND} again" in said
    assert not tokens.exists()


def test_a_code_microsoft_will_not_redeem_says_to_run_it_again(tmp_path, monkeypatch):
    ok, said, tokens = _sign_in(tmp_path, monkeypatch, "http://localhost:8080/auth/callback?code=spent")
    assert ok is False
    assert f"Run {SIGN_IN_COMMAND} again" in said
    assert not tokens.exists()


def _s256(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def test_a_pkce_verifier_is_long_random_and_its_challenge_is_its_s256():
    verifier, challenge = cloud.pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert set(verifier) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    assert challenge == _s256(verifier)
    assert cloud.pkce_pair()[0] != verifier


def test_a_sign_in_link_carries_the_challenge_for_the_verifier_it_redeems_with(tmp_path, monkeypatch):
    MADE.clear()
    OPENED.clear()
    ok, said, _ = _sign_in(tmp_path, monkeypatch, "http://localhost:8080/auth/callback?code=c1")
    assert ok is True
    link = re.search(r"https://\S+", said).group(0)
    query = parse_qs(urlparse(link).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [_s256(MADE[0].verifier)]


class TokenEndpoint:
    """Microsoft's token endpoint, as far as the library posts to it, recording what it was sent."""

    def __init__(self):
        self.sent = []

    async def post(self, url, data):
        self.sent.append(data)
        return httpx.Response(200, json=TOKENS, request=httpx.Request("POST", url))


def test_a_sign_in_code_is_redeemed_only_with_its_pkce_verifier():
    endpoint = TokenEndpoint()
    auth = cloud.PkceAuth(endpoint, cloud.CLIENT_ID, "", cloud.REDIRECT_URI, verifier="v" * 50)
    oauth = asyncio.run(auth.request_oauth_token("c1"))
    assert oauth.refresh_token == "old-refresh"  # noqa: S105 - a stand-in, no real token
    (sent,) = endpoint.sent
    assert sent["code_verifier"] == "v" * 50
    assert sent["code"] == "c1"
    assert "client_secret" not in sent


def test_the_owner_may_paste_the_bare_code_with_spaces_around_it():
    assert cloud.code_from("  M.C5_BAY.2.U.ab-cd!*$ \n") == "M.C5_BAY.2.U.ab-cd!*$"
    assert cloud.code_from("not a code") is None


def test_a_sign_in_from_another_device_asks_for_the_paste_without_listening(tmp_path, monkeypatch):
    ok, said, _ = _sign_in(tmp_path, monkeypatch, "c9")
    assert ok is True
    assert "another device" in said


class ExpiredAuth(RefreshingAuth):
    """The library's sign-in manager, when Microsoft no longer takes the saved refresh token."""

    async def refresh_tokens(self):
        request = httpx.Request("POST", "https://login.live.com/oauth20_token.srf")
        response = httpx.Response(400, json={"error": "invalid_grant"}, request=request)
        error = "invalid_grant"
        raise httpx.HTTPStatusError(error, request=request, response=response)


def test_an_expired_sign_in_says_to_sign_in_again_with_the_command(tmp_path, monkeypatch):
    tokens = tmp_path / "tokens.json"
    cloud.write_tokens(tokens, json.dumps(TOKENS))
    monkeypatch.setattr(cloud, "AuthenticationManager", ExpiredAuth)
    monkeypatch.setattr(cloud, "SignedSession", lambda: None)
    with pytest.raises(SignedOutError) as raised:
        asyncio.run(CloudConsole(tokens=tokens).consoles())
    assert "expired" in raised.value.say
    assert SIGN_IN_COMMAND in raised.value.say


class ExpiresWhileRunning(RefreshingAuth):
    """The library's sign-in manager: the first refresh works, and then Microsoft stops taking it."""

    refreshes = 0

    async def refresh_tokens(self):
        type(self).refreshes += 1
        if type(self).refreshes > 1:
            await ExpiredAuth.refresh_tokens(self)


class RefreshingClient:
    """The library's client, refreshing the sign-in before every request, as the real one does."""

    def __init__(self, auth):
        self._auth, self._answers = auth, Recorder()

    def __getattr__(self, provider):
        return _Refreshes(self._auth, getattr(self._answers, provider))


class _Refreshes:
    def __init__(self, auth, provider):
        self._auth, self._provider = auth, provider

    def __getattr__(self, method):
        async def call(*args, **kwargs):
            await self._auth.refresh_tokens()
            return await getattr(self._provider, method)(*args, **kwargs)

        return call


def test_a_sign_in_refused_while_the_plugin_runs_says_to_sign_in_again(tmp_path, monkeypatch):
    tokens = tmp_path / "tokens.json"
    cloud.write_tokens(tokens, json.dumps(TOKENS))
    monkeypatch.setattr(ExpiresWhileRunning, "refreshes", 0)
    monkeypatch.setattr(cloud, "AuthenticationManager", ExpiresWhileRunning)
    monkeypatch.setattr(cloud, "SignedSession", lambda: None)
    monkeypatch.setattr(cloud, "XboxLiveClient", RefreshingClient)
    driver = CloudConsole(tokens=tokens)
    with pytest.raises(SignedOutError) as raised:
        asyncio.run(driver.status("F4000000000000A1"))
    assert "expired" in raised.value.say
    assert SIGN_IN_COMMAND in raised.value.say


def test_a_command_microsoft_answers_with_a_bad_request_is_not_a_lost_sign_in():
    request = httpx.Request("POST", "https://xccs.xboxlive.com/commands")
    response = httpx.Response(400, request=request)
    driver, _ = _driver(fails=httpx.HTTPStatusError("bad", request=request, response=response))
    with pytest.raises(UnreachableError) as raised:
        asyncio.run(driver.wake("F4000000000000A1"))
    assert not isinstance(raised.value, SignedOutError)


def test_a_sign_in_the_library_refuses_says_to_sign_in_again():
    driver, _ = _driver(fails=AuthenticationException("Failed to authorize you!"))
    with pytest.raises(SignedOutError) as raised:
        asyncio.run(driver.status("F4000000000000A1"))
    assert SIGN_IN_COMMAND in raised.value.say


def test_the_phone_route_explains_the_error_page_before_asking_for_the_whole_address(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, "PkceAuth", SigningAuth)
    monkeypatch.setattr(cloud, "SignedSession", Session)
    monkeypatch.setattr(cloud, "XboxLiveClient", lambda _auth: Recorder())
    said, asked = [], []

    def ask(prompt):
        asked.append((prompt, "\n".join(said)))
        return "http://localhost:8080/auth/callback?code=c1"

    tokens = tmp_path / "tokens.json"
    assert asyncio.run(cloud.sign_in(tokens, said.append, ask=ask, other_device=True)) is True
    ((prompt, before),) = asked
    assert prompt == "Paste the whole address: "
    assert "error page" in before
    assert "That is expected." in before
    assert "WHOLE address" in before
    assert before.index("https://") < before.index("WHOLE address"), "the link comes first"


def test_tokens_are_replaced_whole_or_not_at_all(tmp_path, monkeypatch):
    tokens = tmp_path / "tokens.json"
    cloud.write_tokens(tokens, '{"old": 1}')

    def fails(*_args):
        error = "disk full"
        raise OSError(error)

    monkeypatch.setattr(cloud.pathlib.Path, "replace", fails)
    with pytest.raises(OSError, match="disk full"):
        cloud.write_tokens(tokens, '{"new": 1}')
    assert tokens.read_text() == '{"old": 1}'
    assert [p.name for p in tmp_path.iterdir()] == ["tokens.json"], "no half-written file is left"


def test_nothing_reaches_past_the_allow_list_to_the_library_client():
    tree = ast.parse(SOURCE.read_text())
    guard = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AllowList")
    inside = {id(n) for n in ast.walk(guard)}
    reached = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr == "_guarded" and id(n) not in inside
    ]
    assert not reached, f"the guarded client is reached past AllowList at lines {reached}"
    assert "_guarded" in {n.attr for n in ast.walk(guard) if isinstance(n, ast.Attribute)}
