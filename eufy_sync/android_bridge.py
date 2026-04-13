"""Android bridge module for eufy-sync.

Called from Kotlin via Chaquopy.  Provides Android-specific entry points that
bypass macOS-only components (Playwright browser login, system keychain) while
re-using *all* core sync logic without modification.

On Android:
  - Tokens are stored as JSON files inside the app's private data directory.
  - ``keyring`` is unavailable so ``credentials.py`` automatically falls back
    to file-based storage with 0o600 permissions — no code changes required.
  - ``garmin_auth.browser_login()`` is never called; the Android WebView in
    ``GarminAuthActivity`` captures the service ticket and passes it here for
    the pure-HTTP token exchange.
  - ``strava_client.authorize_strava()`` is never called; ``StravaCallbackActivity``
    handles the OAuth redirect and passes the code here for the token exchange.

Data directory
--------------
All token / session / database files live under a single directory.  The Python
code uses ``Path.home() / ".garmin-sync"`` by default; on Android, Chaquopy
sets ``HOME`` to ``context.filesDir``, so the default becomes
``filesDir/.garmin-sync``.  Kotlin passes that same path to ``set_data_dir``
for explicitness, ensuring every file written here is in app-private storage.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("eufy_sync.android")

# Set by set_data_dir(); falls back to the standard ~/.garmin-sync when None.
_DATA_DIR: Path | None = None


# ---------------------------------------------------------------------------
# Data-directory helpers
# ---------------------------------------------------------------------------

def set_data_dir(path: str) -> None:
    """Configure the directory used for all token / session / database files.

    Must be called before any other function in this module.
    The Kotlin Application class calls this with ``context.filesDir + "/.garmin-sync"``.
    """
    global _DATA_DIR
    _DATA_DIR = Path(path)
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    logger.debug("Data directory set to %s", _DATA_DIR)


def _data_dir() -> Path:
    """Return the active data directory."""
    if _DATA_DIR is not None:
        return _DATA_DIR
    return Path.home() / ".garmin-sync"


# ---------------------------------------------------------------------------
# One-time auth helpers (called during setup, not during background sync)
# ---------------------------------------------------------------------------

def exchange_garmin_ticket(service_ticket: str) -> dict:
    """Exchange a Garmin service ticket captured by the Android WebView for OAuth2 tokens.

    The service ticket is obtained by ``GarminAuthActivity``'s WebView injecting
    the same fetch/XHR-intercepting JavaScript that ``garmin_auth.browser_login()``
    uses on macOS — the only difference is the host environment.

    Delegates to ``garmin_auth._exchange_ticket_for_tokens()`` (pure httpx, no
    Playwright) and saves the resulting ``GarminSession`` to ``session.json`` in
    the data directory so that future headless sync runs can load/refresh it.

    Returns a dict with keys: access_token, refresh_token, expires_at,
    refresh_expires_at.
    """
    from eufy_sync.garmin_auth import _exchange_ticket_for_tokens, GarminSession

    token = _exchange_ticket_for_tokens(service_ticket)
    session = GarminSession(di_token=token)

    session_path = _data_dir() / "session.json"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(session_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(session.to_dict(), indent=2))

    logger.info("Garmin session saved to %s", session_path)
    return {
        "access_token": token.access_token,
        "refresh_token": token.refresh_token,
        "expires_at": token.expires_at,
        "refresh_expires_at": token.refresh_expires_at,
    }


def exchange_strava_code(client_id: str, client_secret: str, code: str) -> dict:
    """Exchange a Strava OAuth authorization code for tokens.

    ``StravaCallbackActivity`` handles the ``eufysync://strava/callback``
    redirect, extracts the code, and passes it here — mirroring what
    ``strava_client.authorize_strava()`` does on macOS with a local HTTP server.

    Saves the tokens to ``strava_token.json`` in the data directory so that
    ``strava_client.StravaClient.authenticate()`` can load / refresh them.

    Returns a dict with keys: access_token, refresh_token, expires_at.
    """
    import httpx

    now = time.time()
    resp = httpx.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()

    tokens = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": data.get("expires_at", now + data.get("expires_in", 21600)),
    }

    token_path = _data_dir() / "strava_token.json"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(tokens, f)

    logger.info("Strava tokens saved to %s", token_path)
    return tokens


# ---------------------------------------------------------------------------
# Background sync entry point (called by SyncWorker via Chaquopy)
# ---------------------------------------------------------------------------

def run_sync(
    eufy_email: str,
    eufy_password: str,
    garmin_email: str | None,
    garmin_password: str | None,
    strava_client_id: str | None,
    strava_client_secret: str | None,
    data_dir: str | None = None,
    backfill_days: int | None = None,
) -> str:
    """Run one full sync cycle and return a JSON-encoded result string.

    Called from ``SyncWorker`` via Chaquopy once per day.  Delegates entirely to
    ``sync.sync_user()`` with ``headless=True`` (no browser — tokens must already
    exist from the one-time setup flow).

    Returns JSON:
      ``{"success": true,  "counts": {"garmin": N, "strava": N}}``
      ``{"success": false, "error":  "...message..."}``
    """
    if data_dir is not None:
        set_data_dir(data_dir)

    from eufy_sync.config import EufyConfig, GarminConfig, StravaConfig, UserConfig
    from eufy_sync.state import SyncState
    from eufy_sync.sync import sync_user

    user = UserConfig(
        name="android_user",
        eufy=EufyConfig(email=eufy_email, password=eufy_password),
        garmin=(
            GarminConfig(email=garmin_email, password=garmin_password)
            if garmin_email and garmin_password
            else None
        ),
        strava=(
            StravaConfig(client_id=strava_client_id, client_secret=strava_client_secret)
            if strava_client_id and strava_client_secret
            else None
        ),
    )

    db_path = _data_dir() / "state.db"
    state = SyncState(db_path)
    try:
        counts = sync_user(
            user=user,
            state=state,
            backfill_days=backfill_days,
            headless=True,   # Never open a browser; raises if re-auth is needed
            dry_run=False,
        )
        return json.dumps({"success": True, "counts": counts})
    except Exception as exc:
        logger.error("Sync failed: %s", exc, exc_info=True)
        return json.dumps({"success": False, "error": str(exc)})
    finally:
        state.close()


# ---------------------------------------------------------------------------
# Status query (called by MainActivity to display token health)
# ---------------------------------------------------------------------------

def get_token_status(data_dir: str | None = None) -> str:
    """Return JSON-encoded token health for Garmin and Strava.

    Returns JSON:
      ``{"garmin": {"state": "valid"|"refresh_needed"|"expired"|"no_session",
                    "days_remaining": N},
         "strava":  {"state": ..., "hours_remaining": N}}``
    """
    if data_dir is not None:
        set_data_dir(data_dir)

    status: dict = {}

    try:
        from eufy_sync.garmin_auth import GarminAuth
        auth = GarminAuth("", "", session_path=_data_dir() / "session.json")
        status["garmin"] = auth.token_status()
    except Exception as exc:
        status["garmin"] = {"state": "error", "error": str(exc)}

    try:
        from eufy_sync.strava_client import StravaClient
        from eufy_sync.config import StravaConfig
        client = StravaClient(StravaConfig("", ""))
        status["strava"] = client.token_status()
    except Exception as exc:
        status["strava"] = {"state": "error", "error": str(exc)}

    return json.dumps(status)
