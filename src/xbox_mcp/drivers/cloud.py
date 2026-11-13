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
import base64
import getpass
import hashlib
import os
import pathlib
import re
import secrets
import stat
import tempfile
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import parse_qs, urlparse

import httpx
from pythonxbox.api.client import XboxLiveClient
from pythonxbox.authentication.manager import AuthenticationManager
from pythonxbox.authentication.models import OAuth2TokenResponse
from pythonxbox.common.exceptions import AuthenticationException
from pythonxbox.common.signed_session import SignedSession

from xbox_mcp import console
from xbox_mcp.console import App, Console, ConsoleStatus, Power, Sent, SignedOutError, UnreachableError

if TYPE_CHECKING:
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
        "smartglass.go_home",
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
SIGN_IN = f"Sign in again: in a terminal on the computer, run {console.SIGN_IN_COMMAND}"
NOT_SIGNED_IN = (
    f"The Xbox plugin is not signed in to Microsoft yet. In a terminal, run {console.SIGN_IN_COMMAND}"
)
EXPIRED = f"The Xbox plugin's Microsoft sign-in has expired. {SIGN_IN}"
#: Microsoft's answers to a refresh token it no longer takes.
REFUSED_REFRESH = frozenset({400, 401, 403})
DAMAGED = f"The Microsoft sign-in file is damaged. {SIGN_IN}"


class NotAllowedError(AttributeError):
    """A library call that is not on the allow-list."""


def _signed_out(say: str) -> SignedOutError:
    """No sign-in the plugin can use, with what the owner should do."""
    return SignedOutError("no usable Microsoft sign-in", say=say)


def pkce_pair() -> tuple[str, str]:
    """A fresh PKCE verifier, and its S256 challenge (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class PkceAuth(AuthenticationManager):
    """The library's sign-in manager, redeeming the code only together with its PKCE verifier.

    The library sends no verifier of its own, and the public client id has no
    secret, so without this a code seen by another program could be redeemed by
    it. ``_oauth2_token_request`` is the library's, at the pinned 0.4.0.
    """

    def __init__(self, *args: Any, verifier: str) -> None:  # noqa: ANN401 - the library's own arguments
        """The library's manager, with the verifier whose challenge went in the sign-in link."""
        super().__init__(*args)
        self.verifier = verifier

    async def request_oauth_token(self, authorization_code: str) -> OAuth2TokenResponse:
        """Redeem the code, with the verifier."""
        # RULE: a sign-in code is redeemed only with its pkce verifier
        return await self._oauth2_token_request(
            {
                "grant_type": "authorization_code",
                "code": authorization_code,
                "scope": " ".join(self._scopes),
                "redirect_uri": self._redirect_uri,
                "code_verifier": self.verifier,
            }
        )


def with_challenge(url: str, challenge: str) -> str:
    """The sign-in link, asking Microsoft to tie the code to the challenge."""
    return str(
        httpx.URL(url).copy_merge_params({"code_challenge": challenge, "code_challenge_method": "S256"})
    )


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
        self._guarded = client

    def __getattr__(self, provider: str) -> _Provider:
        """The library's provider, when the allow-list has a call on it."""
        if provider not in self.PROVIDERS:
            raise NotAllowedError(provider)
        return _Provider(getattr(self._guarded, provider), provider)


def _refused(error: Exception) -> UnreachableError:
    """The plugin's error for what the library or the network raised."""
    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in (
        HTTP_UNAUTHORIZED,
        HTTP_FORBIDDEN,
    ):
        return _signed_out(f"Microsoft refused the sign-in. {SIGN_IN}")
    message = f"the cloud did not answer: {type(error).__name__}"
    # RULE: only a connection that never opened is said as never sent
    if isinstance(error, httpx.ConnectError | httpx.ConnectTimeout):
        return UnreachableError(message)
    return console.MayHaveArrivedError(message)


def write_tokens(path: pathlib.Path, text: str) -> None:
    """Replace the tokens in `path` whole, readable by the owner alone, in a folder only the owner can enter.

    They go to a new file beside it first, made 600 before anything is written,
    and take its place in one step; a failure leaves the old file as it was.
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".tokens-", suffix=".json")
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        pathlib.Path(name).replace(path)
    except BaseException:
        pathlib.Path(name).unlink(missing_ok=True)
        raise


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


async def _refreshed(auth: AuthenticationManager) -> None:
    """Refresh the sign-in; one Microsoft no longer takes is said as expired, with what to do."""
    try:
        await auth.refresh_tokens()
    except httpx.HTTPStatusError as error:
        if error.response.status_code in REFUSED_REFRESH:
            raise _signed_out(EXPIRED) from error
        raise


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
            await _refreshed(auth)
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
        except AuthenticationException as error:
            raise _signed_out(EXPIRED) from error
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

    async def home(self, console_id: str) -> Sent:
        """Ask the console to show Home; the app in front stays paused in the background."""
        return self._sent(
            await self._call(lambda api: api.smartglass.go_home(console_id, timeout=CALL_TIMEOUT))
        )


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


#: A sign-in code on its own, as Microsoft writes them (such as M.C5_BAY.2.U.…).
BARE_CODE = re.compile(r"[A-Za-z0-9._~!*$-]+")


def code_from(address: str) -> str | None:
    """The sign-in code in what the owner pasted: the address the browser ended on, or the code alone."""
    pasted = address.strip()
    if "?" not in pasted and BARE_CODE.fullmatch(pasted):
        return pasted
    codes = parse_qs(urlparse(pasted).query).get("code") or []
    return codes[0] if codes else None


PASTE = (
    "After you sign in, the browser lands on an error page (it cannot connect, or says 404). "
    "That is expected.\n"
    "Copy the WHOLE address from the browser's address bar (it starts with http://localhost:{port}/) "
    "and paste it here. It will not be shown."
)
BUSY = (
    "Another program on this computer uses port {port}, where Microsoft sends the sign-in back, "
    "so that program would see the sign-in code. I stopped before showing the sign-in link.\n"
    "Sign in from your phone instead. Run: {command} --other-device"
)
OTHER_DEVICE = "Open the link on another device, such as your phone, and sign in there, not on this computer."


async def _pasted_code(ask: Callable[[str], str], say: Callable[[str], None]) -> str | None:
    """The sign-in code the owner pastes from another device's browser."""
    say(OTHER_DEVICE)
    say(PASTE.format(port=REDIRECT_PORT))
    return code_from(await asyncio.to_thread(ask, "Paste the whole address: "))


async def _listened_code(url: str, server: HTTPServer, say: Callable[[str], None]) -> str | None:
    """Open `url` in this computer's browser, and the code Microsoft sends back to the listener."""
    webbrowser.open(url)
    say(f"Waiting up to {int(SIGN_IN_WITHIN // 60)} minutes for you to sign in, in the browser...")
    return await asyncio.to_thread(_wait_for_code, server)


async def _code_for(
    url: str, say: Callable[[str], None], ask: Callable[[str], str], *, other_device: bool
) -> tuple[str | None, str]:
    """The sign-in code for `url`, or None and what to tell the owner instead."""
    server = None if other_device else _listen()
    # RULE: a code is never asked for while another program holds the redirect port
    if server is None and not other_device:
        return None, BUSY.format(port=REDIRECT_PORT, command=console.SIGN_IN_COMMAND)
    say(f"Open this page and sign in with the Microsoft account the Xbox uses:\n\n  {url}\n")
    code = await (_listened_code(url, server, say) if server else _pasted_code(ask, say))
    return code, f"No sign-in code came back within five minutes. Run {console.SIGN_IN_COMMAND} again."


async def _redeemed(auth: PkceAuth, code: str) -> OAuth2TokenResponse | None:
    """The tokens Microsoft gave for `code`; None for a spent or refused code."""
    try:
        await auth.request_tokens(code)
    except httpx.HTTPError:
        return None
    return auth.oauth


async def sign_in(
    tokens: pathlib.Path,
    say: Callable[[str], None],
    ask: Callable[[str], str] = getpass.getpass,
    *,
    other_device: bool = False,
) -> bool:
    """The owner signs in to Microsoft in a browser; the tokens go to `tokens`, never to the screen.

    The redirect comes back to a listener on this computer. When another
    program holds the port, it would see the code, so the sign-in stops and
    points to `other_device`: the owner signs in on a phone and pastes the
    address it ends on, unshown (ADR-XB-001).
    """
    async with SignedSession() as session:
        verifier, challenge = pkce_pair()
        auth = PkceAuth(session, CLIENT_ID, "", REDIRECT_URI, verifier=verifier)
        url = with_challenge(auth.generate_authorization_url(), challenge)
        code, why = await _code_for(url, say, ask, other_device=other_device)
        if not code:
            say(why)
            return False
        oauth = await _redeemed(auth, code)
        if oauth is None:
            say(f"Microsoft did not accept the sign-in. Run {console.SIGN_IN_COMMAND} again.")
            return False
        write_tokens(tokens, oauth.model_dump_json())
        consoles = await AllowList(XboxLiveClient(auth)).smartglass.get_console_list(
            include_storage_devices=False
        )
    names = ", ".join(c.name for c in consoles.result) or "no console"
    say(f"Signed in. The sign-in is kept in {tokens}, readable by you alone.\nThe account has: {names}.")
    return True
