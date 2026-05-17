from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

from eufy_sync.garmin_auth import (
    GarminAuth,
    GarminSession,
    TokenPair,
    _browser_context_options,
    _launch_browser,
)


def _make_token(access_expires_in: float = 3600, refresh_expires_in: float = 86400 * 365) -> TokenPair:
    now = time.time()
    return TokenPair(
        access_token="access",
        refresh_token="refresh",
        expires_at=now + access_expires_in,
        refresh_expires_at=now + refresh_expires_in,
    )


def test_token_status_valid():
    auth = GarminAuth("test@example.com", "pw")
    token = _make_token(access_expires_in=3600, refresh_expires_in=86400 * 30)
    session = GarminSession(di_token=token)

    with patch.object(auth, "_load_session", return_value=session):
        status = auth.token_status()

    assert status["state"] == "valid"
    assert status["days_remaining"] is not None
    assert status["days_remaining"] >= 29


def test_token_status_refresh_needed():
    auth = GarminAuth("test@example.com", "pw")
    # Access token expired, refresh token still valid
    token = _make_token(access_expires_in=-100, refresh_expires_in=86400 * 30)
    session = GarminSession(di_token=token)

    with patch.object(auth, "_load_session", return_value=session):
        status = auth.token_status()

    assert status["state"] == "refresh_needed"
    assert status["days_remaining"] >= 29


def test_token_status_expired():
    auth = GarminAuth("test@example.com", "pw")
    # Both tokens expired
    token = _make_token(access_expires_in=-100, refresh_expires_in=-100)
    session = GarminSession(di_token=token)

    with patch.object(auth, "_load_session", return_value=session):
        status = auth.token_status()

    assert status["state"] == "expired"
    assert status["days_remaining"] == 0


def test_token_status_no_session():
    auth = GarminAuth("test@example.com", "pw")

    with patch.object(auth, "_load_session", return_value=None):
        status = auth.token_status()

    assert status["state"] == "no_session"
    assert status["days_remaining"] is None


def test_browser_context_options_windows():
    with patch("eufy_sync.garmin_auth.platform.system", return_value="Windows"):
        assert _browser_context_options() == {}


def test_browser_context_options_non_windows():
    with patch("eufy_sync.garmin_auth.platform.system", return_value="Darwin"):
        opts = _browser_context_options()
    assert opts["is_mobile"] is True
    assert "user_agent" in opts
    assert "viewport" in opts


def test_launch_browser_windows_edge_first_fallback_to_chromium():
    playwright = MagicMock()
    playwright.chromium.launch.side_effect = [RuntimeError("no edge"), "fallback-browser"]
    with patch("eufy_sync.garmin_auth.platform.system", return_value="Windows"):
        browser = _launch_browser(playwright)
    assert browser == "fallback-browser"
    assert playwright.chromium.launch.call_count == 2
    first_call_kwargs = playwright.chromium.launch.call_args_list[0].kwargs
    second_call_kwargs = playwright.chromium.launch.call_args_list[1].kwargs
    assert first_call_kwargs == {"headless": False, "channel": "msedge"}
    assert second_call_kwargs == {"headless": False}


def test_launch_browser_non_windows_uses_default_chromium():
    playwright = MagicMock()
    playwright.chromium.launch.return_value = "browser"
    with patch("eufy_sync.garmin_auth.platform.system", return_value="Darwin"):
        browser = _launch_browser(playwright)
    assert browser == "browser"
    playwright.chromium.launch.assert_called_once_with(headless=False)
