"""Garmin OAuth2 authentication via Playwright browser login + token refresh.

Flow:
1. **First run** – :func:`browser_login` opens the Garmin mobile SSO page in
   a Chromium window. The user logs in (including any MFA). A JS hook
   intercepts the ``serviceTicketId`` from the login XHR response.
2. **Exchange** – :func:`_exchange_ticket_for_tokens` converts the service
   ticket to a DI OAuth2 ``access_token`` + ``refresh_token``.
3. **Persist** – tokens are saved via :mod:`takeout_garmin_sync.secrets`.
4. **Subsequent runs** – :meth:`GarminAuth.ensure_authenticated` refreshes the
   access token automatically. A browser window is only required again when
   the *refresh* token expires (~1 year) or is revoked.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Garmin DI OAuth2 endpoints
DI_TOKEN_URL = "https://diauth.garmin.com/di-oauth2-service/oauth/token"

# Public client IDs from the Garmin Connect mobile app — not per-user secrets
DI_CLIENT_IDS = [
    "GARMIN_CONNECT_MOBILE_ANDROID_DI_2025Q2",
    "GARMIN_CONNECT_MOBILE_ANDROID_DI_2024Q4",
    "GARMIN_CONNECT_MOBILE_ANDROID_DI",
]

DI_GRANT_TYPE = (
    "https://connectapi.garmin.com/di-oauth2-service/oauth/grant/service_ticket"
)
SERVICE_URL = "https://mobile.integration.garmin.com/gcm/android"

SSO_LOGIN_URL = (
    "https://sso.garmin.com/mobile/sso/en_US/sign-in"
    "?clientId=GCM_ANDROID_DARK"
    "&service=https://mobile.integration.garmin.com/gcm/android"
)

# Login polling: 180 seconds total (360 × 500 ms)
_LOGIN_POLL_ITERATIONS = 360
_LOGIN_POLL_INTERVAL_MS = 500

API_HEADERS: dict[str, str] = {
    "user-agent": "GCM-Android-5.23",
    "x-garmin-client-platform": "Android",
    "x-app-ver": "10861",
    "x-lang": "en",
}

# Refresh the access token this many seconds before it formally expires
REFRESH_SAFETY_MARGIN = 300


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    expires_at: float           # Unix timestamp
    refresh_expires_at: float   # Unix timestamp

    @property
    def is_expired(self) -> bool:
        return time.time() >= (self.expires_at - REFRESH_SAFETY_MARGIN)

    @property
    def refresh_is_expired(self) -> bool:
        return time.time() >= (self.refresh_expires_at - REFRESH_SAFETY_MARGIN)


@dataclass
class GarminSession:
    di_token: TokenPair

    def to_dict(self) -> dict:
        return {"di_token": asdict(self.di_token)}

    @classmethod
    def from_dict(cls, data: dict) -> "GarminSession":
        return cls(di_token=TokenPair(**data["di_token"]))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _basic_auth_header(client_id: str) -> str:
    encoded = base64.b64encode(f"{client_id}:".encode()).decode()
    return f"Basic {encoded}"


def _exchange_ticket_for_tokens(service_ticket: str) -> TokenPair:
    """Exchange a Garmin SSO service ticket for DI OAuth2 tokens."""
    now = time.time()
    for client_id in DI_CLIENT_IDS:
        try:
            resp = httpx.post(
                DI_TOKEN_URL,
                headers={
                    "authorization": _basic_auth_header(client_id),
                    "content-type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": DI_GRANT_TYPE,
                    "service_ticket": service_ticket,
                    "service_url": SERVICE_URL,
                    "client_id": client_id,
                },
                timeout=30.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                logger.info("Exchanged service ticket using client_id: %s", client_id)
                return TokenPair(
                    access_token=data["access_token"],
                    refresh_token=data["refresh_token"],
                    expires_at=now + data["expires_in"],
                    refresh_expires_at=now + data.get(
                        "refresh_token_expires_in", 86400 * 365
                    ),
                )
            logger.debug(
                "client_id %s returned HTTP %d, trying next",
                client_id, resp.status_code,
            )
        except Exception as exc:
            logger.debug("client_id %s failed: %s", client_id, exc)

    raise RuntimeError(
        "Failed to exchange Garmin service ticket for tokens. "
        "This is usually a transient Garmin server issue — try again in a few minutes."
    )


def _refresh_di_token(token: TokenPair) -> TokenPair:
    """Use the refresh token to obtain a new access token."""
    now = time.time()
    for client_id in DI_CLIENT_IDS:
        try:
            resp = httpx.post(
                DI_TOKEN_URL,
                headers={
                    "authorization": _basic_auth_header(client_id),
                    "content-type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": token.refresh_token,
                    "client_id": client_id,
                },
                timeout=30.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                logger.info("Refreshed DI token using client_id: %s", client_id)
                return TokenPair(
                    access_token=data["access_token"],
                    refresh_token=data.get("refresh_token", token.refresh_token),
                    expires_at=now + data["expires_in"],
                    refresh_expires_at=now + data.get(
                        "refresh_token_expires_in", 86400 * 365
                    ),
                )
        except Exception as exc:
            logger.debug("Refresh with client_id %s failed: %s", client_id, exc)

    raise RuntimeError(
        "Failed to refresh Garmin token. "
        "Run 'takeout-garmin-sync auth' to re-authenticate."
    )


# ---------------------------------------------------------------------------
# Browser login
# ---------------------------------------------------------------------------

def browser_login(email: str, password: str) -> str:
    """Open a Chromium browser, fill in Garmin credentials, and capture the
    ``serviceTicketId`` from the mobile SSO login XHR response.

    The browser window is visible so the user can complete MFA / CAPTCHA
    challenges if required. Up to 3 minutes are allowed for the login to
    complete.

    Args:
        email: Garmin account email.
        password: Garmin account password.

    Returns:
        The ``serviceTicketId`` string from the login response.

    Raises:
        RuntimeError: If no service ticket is captured within the timeout.
    """
    from playwright.sync_api import sync_playwright

    captured_ticket: list[str] = []

    def _handle_capture(source: object, result_json: object) -> None:
        try:
            data = (
                json.loads(result_json)
                if isinstance(result_json, str)
                else result_json
            )
            if data.get("responseStatus", {}).get("type") == "SUCCESSFUL":
                ticket = data.get("serviceTicketId")
                if ticket:
                    captured_ticket.append(ticket)
                    logger.info("Captured service ticket from Garmin login response")
        except Exception as exc:
            logger.warning("Failed to parse login capture: %s", exc)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Linux; Android 13; sdk_gphone64_arm64)"
                " AppleWebKit/537.36 (KHTML, like Gecko)"
                " Chrome/121.0.0.0 Mobile Safari/537.36"
            ),
            viewport={"width": 412, "height": 915},
            is_mobile=True,
        )

        context.expose_binding(
            "tgsCapturLogin",
            lambda source, data: _handle_capture(source, data),
        )

        context.add_init_script("""
            (function() {
                const originalFetch = window.fetch;
                window.fetch = async function(...args) {
                    const response = await originalFetch.apply(this, args);
                    const url = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');
                    if (url.includes('/mobile/api/login')) {
                        try {
                            const clone = response.clone();
                            const data = await clone.json();
                            window.tgsCapturLogin(JSON.stringify(data));
                        } catch(e) {}
                    }
                    return response;
                };

                const origOpen = XMLHttpRequest.prototype.open;
                const origSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                    this._url = url;
                    return origOpen.call(this, method, url, ...rest);
                };
                XMLHttpRequest.prototype.send = function(...args) {
                    this.addEventListener('load', function() {
                        if (this._url && this._url.includes('/mobile/api/login')) {
                            try { window.tgsCapturLogin(this.responseText); } catch(e) {}
                        }
                    });
                    return origSend.apply(this, args);
                };
            })();
        """)

        page = context.new_page()
        page.goto(SSO_LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)

        page.wait_for_selector(
            "input[name='username'], input[name='email'], #username, #email",
            timeout=30_000,
        )

        for sel in ["input[name='username']", "input[name='email']", "#username", "#email"]:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.fill(email)
                    break
            except Exception:
                continue

        for sel in ["input[name='password']", "#password"]:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.fill(password)
                    break
            except Exception:
                continue

        for sel in ["button[type='submit']", "#login-btn-signin", "button.btn-primary"]:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    break
            except Exception:
                continue

        logger.info("Waiting for Garmin login (check the browser window for MFA prompts)…")
        for _ in range(_LOGIN_POLL_ITERATIONS):
            if captured_ticket:
                break
            page.wait_for_timeout(_LOGIN_POLL_INTERVAL_MS)

        browser.close()

    if not captured_ticket:
        raise RuntimeError(
            "Garmin login timed out — no service ticket was captured.\n"
            "Possible causes: wrong credentials, CAPTCHA, or slow MFA.\n"
            "Try again with: takeout-garmin-sync auth"
        )

    return captured_ticket[0]


# ---------------------------------------------------------------------------
# GarminAuth manager
# ---------------------------------------------------------------------------

class GarminAuth:
    """Manage Garmin OAuth2 tokens with automatic refresh.

    On the first call to :meth:`ensure_authenticated` the class attempts to
    load a previously persisted session. If the access token is still valid
    it is returned immediately. If it has expired but the refresh token is
    still valid the token is silently refreshed. A browser window is opened
    only when both tokens have expired.

    Args:
        email: Garmin account email (used only when browser login is needed).
        password: Garmin account password (used only when browser login is needed).
        session_path: Override the default session file path
                      (``~/.takeout-garmin-sync/session.json``).
    """

    def __init__(
        self,
        email: str,
        password: str,
        session_path: Path | None = None,
    ) -> None:
        self.email = email
        self.password = password
        self.session_path = session_path
        self._session: GarminSession | None = None

    def needs_browser_login(self) -> bool:
        """Return True if the stored refresh token is missing or expired."""
        session = self._load_session()
        return session is None or session.di_token.refresh_is_expired

    def ensure_authenticated(self, allow_browser: bool = True) -> str:
        """Return a valid DI access token, refreshing or re-logging-in as needed.

        Args:
            allow_browser: When False, raises :class:`RuntimeError` instead of
                           opening a browser window. Use this in headless/CI
                           environments.

        Returns:
            A valid ``access_token`` string for use as ``Bearer`` auth.

        Raises:
            RuntimeError: If *allow_browser* is False and a browser login
                          would be required.
        """
        if self._session is None:
            self._session = self._load_session()

        if self._session is not None:
            di = self._session.di_token
            if not di.is_expired:
                return di.access_token
            if not di.refresh_is_expired:
                try:
                    self._session.di_token = _refresh_di_token(di)
                    self._save_session()
                    return self._session.di_token.access_token
                except Exception:
                    logger.warning("Token refresh failed; falling back to browser login")

        if not allow_browser:
            raise RuntimeError(
                "Garmin session expired. "
                "Run 'takeout-garmin-sync auth' to re-authenticate interactively."
            )

        logger.info("Starting browser-based Garmin login")
        ticket = browser_login(self.email, self.password)
        di_token = _exchange_ticket_for_tokens(ticket)
        self._session = GarminSession(di_token=di_token)
        self._save_session()
        return self._session.di_token.access_token

    def force_reauth(self) -> str:
        """Force a fresh browser login regardless of current token state."""
        logger.info("Forcing Garmin browser re-authentication")
        ticket = browser_login(self.email, self.password)
        di_token = _exchange_ticket_for_tokens(ticket)
        self._session = GarminSession(di_token=di_token)
        self._save_session()
        return self._session.di_token.access_token

    def token_status(self) -> dict:
        """Return a summary of the current token health.

        Returns:
            A dict with keys:
            - ``state``: one of ``"valid"``, ``"refresh_needed"``,
              ``"expired"``, or ``"no_session"``.
            - ``days_remaining``: integer days until refresh token expiry,
              or ``None`` if there is no session.
        """
        session = self._load_session()
        if session is None:
            return {"state": "no_session", "days_remaining": None}
        di = session.di_token
        if di.refresh_is_expired:
            return {"state": "expired", "days_remaining": 0}
        days = int((di.refresh_expires_at - time.time()) / 86400)
        if di.is_expired:
            return {"state": "refresh_needed", "days_remaining": days}
        return {"state": "valid", "days_remaining": days}

    def _load_session(self) -> GarminSession | None:
        from takeout_garmin_sync.secrets import load_session
        data = load_session(self.session_path)
        if data is None:
            return None
        try:
            session = GarminSession.from_dict(data)
            logger.debug("Loaded Garmin session")
            return session
        except Exception as exc:
            logger.warning("Failed to parse stored Garmin session: %s", exc)
            return None

    def _save_session(self) -> None:
        from takeout_garmin_sync.secrets import store_session
        store_session(self._session.to_dict(), self.session_path)
