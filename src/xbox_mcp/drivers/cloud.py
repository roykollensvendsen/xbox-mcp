# SPDX-License-Identifier: GPL-3.0-or-later
"""The Xbox, reached through Microsoft's cloud with python-xbox (ADR-XB-001).

This is the only module that imports the library (``tests/test_layers.py``
refuses any other), and it reaches the library's client only through
:class:`AllowList`, which refuses every call not in :data:`ALLOWED`. That
rules out the store, purchases, the account, family, privacy, settings,
friends, messages and the raw HTTP session.

The Microsoft sign-in tokens are read from, and written back to, one file
readable by the owner alone. They are never logged or shown.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import stat
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import parse_qs, urlparse

import httpx
from pythonxbox.api.client import XboxLiveClient
from pythonxbox.authentication.manager import AuthenticationManager
from pythonxbox.authentication.models import OAuth2TokenResponse
from pythonxbox.common.signed_session import SignedSession

from xbox_mcp.console import App, Console, ConsoleStatus, Power, Sent, SignedOutError, UnreachableError

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Awaitable, Callable

#: The only library calls the plugin makes, as provider.method on the library's client.
ALLOWED = frozenset(
    {
        "smartglass.get_console_list",
        "smartglass.get_console_status",
        "smartglass.get_installed_apps",
        "smartglass.wake_up",
        "smartglass.turn_off",
        "smartglass.launch_app",
    }
)
#: The library's public client id, registered by OpenXbox as a desktop app: no secret (ADR-XB-001).
CLIENT_ID = "388ea51c-0b25-4029-aae2-17df49d23905"
REDIRECT_PORT = 8080
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/auth/callback"
#: Seconds one request to the cloud may take, and one call with a token refresh before it.
CALL_TIMEOUT = 4.0
CALL_WITHIN = 5.0
#: Seconds the owner has to sign in, in the browser.
SIGN_IN_WITHIN = 300.0
#: The cloud's power states, as the plugin speaks of them.
POWER: dict[str, Power] = {
    "On": "on",
    "ConnectedStandby": "asleep",
    "SystemUpdate": "updating",
    "Off": "off",
    "Unknown": "unknown",
}
HTTP_UNAUTHORIZED, HTTP_FORBIDDEN = 401, 403
SIGN_IN = "Sign in again: run xbox-mcp sign-in in a terminal on the computer."
NOT_SIGNED_IN = (
    "The Xbox plugin is not signed in to Microsoft yet. Run xbox-mcp sign-in in a terminal on the computer."
)
DAMAGED = f"The Microsoft sign-in file is damaged. {SIGN_IN}"


class NotAllowedError(AttributeError):
    """A library call that is not on the allow-list."""


def _signed_out(say: str) -> SignedOutError:
    """No sign-in the plugin can use, with what the owner should do."""
    return SignedOutError("no usable Microsoft sign-in", say=say)


class _Provider:
    def __init__(self, provider: object, name: str) -> None:
        self._provider, self._name = provider, name

    def __getattr__(self, method: str) -> Any:  # noqa: ANN401 - whatever the library gives
        """The library's method, when it is on the allow-list."""
        path = f"{self._name}.{method}"
        # RULE: only an allow-listed library call is ever made
        if path not in ALLOWED:
            raise NotAllowedError(path)
        return getattr(self._provider, method)


class AllowList:
    """The library's client, through which only the calls in ALLOWED can be reached."""

    PROVIDERS: ClassVar[frozenset[str]] = frozenset(p.split(".")[0] for p in ALLOWED)

    def __init__(self, client: object) -> None:
        """Guard `client`, the library's XboxLiveClient."""
        self._client = client

    def __getattr__(self, provider: str) -> _Provider:
        """The library's provider, when the allow-list has a call on it."""
        if provider not in self.PROVIDERS:
            raise NotAllowedError(provider)
        return _Provider(getattr(self._client, provider), provider)


def _refused(error: Exception) -> UnreachableError:
    """The plugin's error for what the library or the network raised."""
    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in (
        HTTP_UNAUTHORIZED,
        HTTP_FORBIDDEN,
    ):
        return _signed_out(f"Microsoft refused the sign-in. {SIGN_IN}")
    return UnreachableError(f"the cloud did not answer: {type(error).__name__}")


def write_tokens(path: pathlib.Path, text: str) -> None:
    """Write the tokens to `path`, readable by the owner alone, in a folder only the owner can enter."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(text)
    path.chmod(0o600)


def read_tokens(path: pathlib.Path) -> OAuth2TokenResponse:
    """The tokens in `path`; refused when there are none, or others can read them."""
    if not path.is_file():
        raise _signed_out(NOT_SIGNED_IN)
    # RULE: a token file others can read is refused
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        say = f"The Microsoft sign-in file can be read by others, so I won't use it. Run: chmod 600 {path}"
        raise _signed_out(say)
    try:
        return OAuth2TokenResponse.model_validate_json(path.read_text())
    except ValueError as error:
        raise _signed_out(DAMAGED) from error


class CloudConsole:
    """The console calls of :class:`xbox_mcp.console.Cloud`, through Microsoft's cloud."""

    def __init__(self, *, tokens: pathlib.Path | None = None, client: object | None = None) -> None:
        """Sign in from `tokens` on the first call; a test passes `client` instead."""
        self._tokens = tokens
        self._api = AllowList(client) if client is not None else None
        self._auth: AuthenticationManager | None = None
        self._saved: str | None = None

    async def _client(self) -> AllowList:
        if self._api is None:
            if self._tokens is None:
                raise _signed_out(NOT_SIGNED_IN)
            auth = AuthenticationManager(SignedSession(), CLIENT_ID, "", REDIRECT_URI)
            auth.oauth = read_tokens(self._tokens)
            self._saved = auth.oauth.model_dump_json()
            await auth.refresh_tokens()
            self._auth, self._api = auth, AllowList(XboxLiveClient(auth))
            self._keep()
        return self._api

    def _keep(self) -> None:
        """Write the tokens back when the library refreshed them, so the next start can use them."""
        if self._auth is None or self._auth.oauth is None or self._tokens is None:
            return
        now = self._auth.oauth.model_dump_json()
        if now != self._saved:
            write_tokens(self._tokens, now)
            self._saved = now

    async def _call(self, work: Callable[[AllowList], Awaitable[Any]]) -> Any:  # noqa: ANN401 - the library's models
        try:
            async with asyncio.timeout(CALL_WITHIN):
                answer = await work(await self._client())
        except UnreachableError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError) as error:
            raise _refused(error) from error
        self._keep()
        return answer

    async def consoles(self) -> list[Console]:
        """The consoles on the owner's account."""
        listed = await self._call(
            lambda api: api.smartglass.get_console_list(include_storage_devices=False, timeout=CALL_TIMEOUT)
        )
        return [Console(c.id, c.name) for c in listed.result]

    async def status(self, console_id: str) -> ConsoleStatus:
        """The console's power and the app in focus."""
        found = await self._call(
            lambda api: api.smartglass.get_console_status(console_id, timeout=CALL_TIMEOUT)
        )
        return ConsoleStatus(POWER.get(str(found.power_state), "unknown"), found.focus_app_aumid or None)

    async def apps(self, console_id: str) -> list[App]:
        """The apps and games installed on the console, with the store id each starts by."""
        found = await self._call(
            lambda api: api.smartglass.get_installed_apps(device_id=console_id, timeout=CALL_TIMEOUT)
        )
        return [
            App(p.name, p.one_store_product_id, p.aumid)
            for p in found.result
            if p.name and p.one_store_product_id
        ]

    @staticmethod
    def _sent(response: Any) -> Sent:  # noqa: ANN401 - the library's CommandResponse
        code = str(response.status.error_code)
        return Sent(accepted=code == "OK", reason=None if code == "OK" else code)

    async def wake(self, console_id: str) -> Sent:
        """Ask the console to wake from sleep."""
        return self._sent(
            await self._call(lambda api: api.smartglass.wake_up(console_id, timeout=CALL_TIMEOUT))
        )

    async def sleep(self, console_id: str) -> Sent:
        """Ask the console to go to sleep."""
        return self._sent(
            await self._call(lambda api: api.smartglass.turn_off(console_id, timeout=CALL_TIMEOUT))
        )

    async def launch(self, console_id: str, product_id: str) -> Sent:
        """Ask the console to start an installed app by its store product id."""
        response = await self._call(
            lambda api: api.smartglass.launch_app(console_id, product_id, timeout=CALL_TIMEOUT)
        )
        return self._sent(response)


class _Callback(BaseHTTPRequestHandler):
    """Takes the one redirect back from Microsoft's sign-in page, on this computer only."""

    code: ClassVar[str | None] = None

    def do_GET(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        codes = query.get("code") or []
        _Callback.code = codes[0] if codes else None
        body = b"Signed in. You can close this tab." if _Callback.code else b"Sign-in failed. Try again."
        self.send_response(200 if _Callback.code else 400)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        """Say nothing: the request carries the sign-in code."""


def _listen() -> HTTPServer | None:
    """The one-shot listener for Microsoft's redirect; None when another program holds the port."""
    try:
        server = HTTPServer(("127.0.0.1", REDIRECT_PORT), _Callback)
    except OSError:
        return None
    server.timeout = SIGN_IN_WITHIN
    return server


def _wait_for_code(server: HTTPServer) -> str | None:
    _Callback.code = None
    try:
        server.handle_request()
    finally:
        server.server_close()
    return _Callback.code


def code_from(address: str) -> str | None:
    """The sign-in code in the address the browser ended on, as the owner pasted it."""
    codes = parse_qs(urlparse(address.strip()).query).get("code") or []
    return codes[0] if codes else None


PASTE = (
    "Another program on this computer uses port {port}, so the sign-in cannot come back here by itself.\n"
    "After you sign in, the browser ends on a page that fails or shows something else. That is expected.\n"
    "Copy the whole address from the browser's address bar, starting with http://localhost:{port}/, "
    "and paste it here. It will not be shown."
)


async def _await_code(url: str, say: Callable[[str], None], ask: Callable[[str], str]) -> str | None:
    """Open `url` in the browser, and the sign-in code Microsoft sends back, by listener or by paste."""
    server = _listen()
    if server is None:
        say(PASTE.format(port=REDIRECT_PORT))
    webbrowser.open(url)
    if server is None:
        return code_from(await asyncio.to_thread(ask, "Address: "))
    say(f"Waiting up to {int(SIGN_IN_WITHIN // 60)} minutes for you to sign in, in the browser...")
    return await asyncio.to_thread(_wait_for_code, server)


async def sign_in(
    tokens: pathlib.Path, say: Callable[[str], None], ask: Callable[[str], str] = getpass.getpass
) -> bool:
    """The owner signs in to Microsoft in a browser; the tokens go to `tokens`, never to the screen.

    The redirect comes back to a listener on this computer, or, when another
    program holds the port, through the address the owner pastes, unshown.
    """
    async with SignedSession() as session:
        auth = AuthenticationManager(session, CLIENT_ID, "", REDIRECT_URI)
        url = auth.generate_authorization_url()
        say(f"Open this page and sign in with the Microsoft account the Xbox uses:\n\n  {url}\n")
        code = await _await_code(url, say, ask)
        if not code:
            say("No sign-in code came back. Run xbox-mcp sign-in again, and sign in within five minutes.")
            return False
        await auth.request_tokens(code)
        if auth.oauth is None:
            say("Microsoft gave no sign-in. Run xbox-mcp sign-in again.")
            return False
        write_tokens(tokens, auth.oauth.model_dump_json())
        consoles = await AllowList(XboxLiveClient(auth)).smartglass.get_console_list(
            include_storage_devices=False
        )
    names = ", ".join(c.name for c in consoles.result) or "no console"
    say(f"Signed in. The sign-in is kept in {tokens}, readable by you alone.\nThe account has: {names}.")
    return True
