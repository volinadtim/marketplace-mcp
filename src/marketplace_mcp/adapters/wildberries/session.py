"""Talking to Wildberries: a token from the browser, then plain HTTP.

WB authorises its account APIs with a bearer token — a JWT its sign-in SDK
leaves in the page's local storage, good for thirty days. So the browser is
opened once to read that token out of a signed-in profile, closed again, and
every read after it is an ordinary HTTPS request.

That is the whole difference from the Ozon session, which has to keep a browser
alive because its session lives in cookies that rotate on almost every call.
Same problem, different enough answer that sharing one implementation would
have meant writing a switch rather than a session.
"""

import base64
import binascii
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Final, Self

from curl_cffi import requests

from marketplace_mcp.adapters.wildberries.constants import (
    APP_TYPE,
    HOME,
    PURCHASES_PAGE,
    TOKEN_STORAGE_KEY,
)
from marketplace_mcp.core.errors import MarketplaceError

logger = logging.getLogger(__name__)

# Read the token again this long before it expires rather than at the moment it
# does: a walk that starts valid should not end unauthorised halfway.
_REFRESH_MARGIN_SECONDS: Final = 3600
# 401 is WB refusing the token; 498 is its antibot refusing the cookies. Both
# mean the session read out of the profile is no longer good enough, and both
# are answered the same way — read it again.
_STALE_SESSION: Final = frozenset({401, 498})


class WildberriesSignedOutError(MarketplaceError):
    """The stored profile no longer holds a usable token."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"not signed in to Wildberries: {detail}")


class WildberriesSession:
    """A bearer token kept fresh from a browser profile, and HTTP that uses it."""

    def __init__(self, profile_dir: Path, impersonate: str = "chrome124", timeout: float = 30.0) -> None:
        self._profile = profile_dir
        self._impersonate = impersonate
        self._timeout = timeout
        self._lock = threading.RLock()
        self._http: Any = None
        self._token: str | None = None
        self._cookies: dict[str, str] = {}
        self._expires_at: float = 0.0

    # -- the token -----------------------------------------------------------

    def _read_from_browser(self) -> tuple[str, dict[str, str]]:
        """Open the signed-in profile just long enough to read the session out.

        Both halves are needed, and they are not the same thing. The bearer
        token authorises the caller; the cookies satisfy the antibot in front of
        www.wildberries.ru, which answers 498 without them however good the
        token is. astro.wildberries.ru takes the token alone, which is what made
        the difference easy to miss.

        Imported here rather than at the top because this is the only path that
        needs Playwright at all: a process that already holds a valid token
        never launches a browser.
        """
        from playwright.sync_api import sync_playwright  # ruff: ignore[import-outside-top-level]

        from marketplace_mcp.adapters.ozon.constants import LAUNCH_ARGS  # ruff: ignore[import-outside-top-level]

        if not (self._profile / "Default").exists():
            msg = f"no browser profile at {self._profile} — run the sign-in once"
            raise WildberriesSignedOutError(msg)

        with sync_playwright() as play:
            context = play.chromium.launch_persistent_context(
                user_data_dir=str(self._profile),
                headless=False,
                args=list(LAUNCH_ARGS),
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                viewport={"width": 1366, "height": 900},
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(PURCHASES_PAGE, wait_until="domcontentloaded", timeout=90_000)
                page.wait_for_timeout(6_000)
                token = page.evaluate(f"() => localStorage.getItem({TOKEN_STORAGE_KEY!r})")
                cookies = {
                    cookie["name"]: cookie["value"]
                    for cookie in context.cookies()
                    if "wildberries" in cookie.get("domain", "") or "wb.ru" in cookie.get("domain", "")
                }
            finally:
                # Closing is what writes the profile out, token refresh included.
                context.close()

        if not token:
            msg = "the profile holds no access token — the sign-in has lapsed"
            raise WildberriesSignedOutError(msg)
        return str(token), cookies

    @staticmethod
    def expiry_of(token: str) -> float:
        """When a token stops being accepted, from its own claims.

        Read rather than assumed: the alternative is finding out mid-walk, and
        the claim is right there. A token that cannot be read at all is treated
        as expiring now, which sends the caller back to the browser.
        """
        try:
            payload = token.split(".")[1]
            padded = payload + "=" * (-len(payload) % 4)
            return float(json.loads(base64.urlsafe_b64decode(padded))["exp"])
        except (IndexError, ValueError, KeyError, binascii.Error):
            logger.warning("could not read the token's expiry; treating it as expired")
            return 0.0

    def token(self) -> str:
        """The current token, fetched from the browser if there is not one."""
        with self._lock:
            if self._token and time.time() < self._expires_at - _REFRESH_MARGIN_SECONDS:
                return self._token
            self._token, self._cookies = self._read_from_browser()
            self._expires_at = self.expiry_of(self._token)
            left = (self._expires_at - time.time()) / 86400
            logger.info("read a Wildberries token, good for %.1f days", left)
            return self._token

    def forget_token(self) -> None:
        """Drop the token so the next call reads a fresh one."""
        with self._lock:
            self._token = None
            self._cookies = {}
            self._expires_at = 0.0

    # -- http ----------------------------------------------------------------

    def _session(self) -> Any:
        if self._http is None:
            self._http = requests.Session(impersonate=self._impersonate)
        return self._http

    def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.token()}",
            "wb-apptype": APP_TYPE,
            "accept-language": "ru-RU",
            "origin": HOME,
            "referer": f"{HOME}/lk/myorders/archive",
        }

    def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self._call("GET", url, params=params)

    def post(self, url: str, body: Any = None) -> Any:
        return self._call("POST", url, json=body if body is not None else {})

    def _call(self, method: str, url: str, **kwargs: Any) -> Any:
        """One request, retried once if the stored session turned out to be stale.

        Only once, and only on the two answers that mean exactly that: anything
        else is the caller's problem, and a second browser launch for a server
        error would cost a minute to learn nothing.
        """
        with self._lock:
            response = self._send(method, url, **kwargs)
            if response.status_code in _STALE_SESSION:
                logger.info("Wildberries answered %s; reading the session again", response.status_code)
                self.forget_token()
                response = self._send(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

    def _send(self, method: str, url: str, **kwargs: Any) -> Any:
        headers = self._headers()  # reading these is what fetches the session
        return self._session().request(
            method, url, headers=headers, cookies=self._cookies, timeout=self._timeout, **kwargs
        )

    def close(self) -> None:
        with self._lock:
            if self._http is not None:
                self._http.close()
                self._http = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
