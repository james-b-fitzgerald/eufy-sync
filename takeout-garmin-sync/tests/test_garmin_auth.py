"""Tests for GarminAuth token state management (no network calls)."""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from takeout_garmin_sync.garmin_auth import (
    GarminAuth,
    GarminSession,
    TokenPair,
    _basic_auth_header,
    _exchange_ticket_for_tokens,
    _refresh_di_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_token(
    access_token: str = "access",
    refresh_token: str = "refresh",
    expires_in: float = 3600,
    refresh_expires_in: float = 86400 * 365,
) -> TokenPair:
    now = time.time()
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=now + expires_in,
        refresh_expires_at=now + refresh_expires_in,
    )


def _expired_token(**kwargs) -> TokenPair:
    return _valid_token(expires_in=-100, **kwargs)


def _fully_expired_token(**kwargs) -> TokenPair:
    return _valid_token(expires_in=-100, refresh_expires_in=-100, **kwargs)


# ---------------------------------------------------------------------------
# TokenPair
# ---------------------------------------------------------------------------

class TestTokenPair:
    def test_not_expired_when_fresh(self):
        token = _valid_token()
        assert not token.is_expired

    def test_expired_when_past_expiry(self):
        token = _expired_token()
        assert token.is_expired

    def test_refresh_expired(self):
        token = _fully_expired_token()
        assert token.refresh_is_expired

    def test_refresh_not_expired_when_only_access_expired(self):
        token = _expired_token()
        assert not token.refresh_is_expired


# ---------------------------------------------------------------------------
# GarminSession serialisation
# ---------------------------------------------------------------------------

class TestGarminSession:
    def test_round_trip(self):
        token = _valid_token("tok", "ref")
        session = GarminSession(di_token=token)
        restored = GarminSession.from_dict(session.to_dict())
        assert restored.di_token.access_token == "tok"
        assert restored.di_token.refresh_token == "ref"

    def test_to_dict_structure(self):
        session = GarminSession(di_token=_valid_token())
        d = session.to_dict()
        assert "di_token" in d
        assert "access_token" in d["di_token"]


# ---------------------------------------------------------------------------
# _basic_auth_header
# ---------------------------------------------------------------------------

class TestBasicAuthHeader:
    def test_format(self):
        header = _basic_auth_header("MY_CLIENT_ID")
        assert header.startswith("Basic ")
        import base64
        decoded = base64.b64decode(header[6:]).decode()
        assert decoded == "MY_CLIENT_ID:"


# ---------------------------------------------------------------------------
# GarminAuth — unit tests with mocked secrets + HTTP
# ---------------------------------------------------------------------------

class TestGarminAuth:
    @pytest.fixture
    def auth(self, tmp_path: Path) -> GarminAuth:
        return GarminAuth("test@example.com", "secret", session_path=tmp_path / "session.json")

    def test_needs_browser_login_when_no_session(self, auth: GarminAuth):
        with patch("takeout_garmin_sync.garmin_auth.GarminAuth._load_session", return_value=None):
            assert auth.needs_browser_login()

    def test_needs_browser_login_when_refresh_expired(self, auth: GarminAuth):
        session = GarminSession(di_token=_fully_expired_token())
        with patch("takeout_garmin_sync.garmin_auth.GarminAuth._load_session", return_value=session):
            assert auth.needs_browser_login()

    def test_does_not_need_browser_when_refresh_valid(self, auth: GarminAuth):
        session = GarminSession(di_token=_expired_token())
        with patch("takeout_garmin_sync.garmin_auth.GarminAuth._load_session", return_value=session):
            assert not auth.needs_browser_login()

    def test_ensure_authenticated_returns_valid_token(self, auth: GarminAuth):
        token = _valid_token(access_token="good-token")
        session = GarminSession(di_token=token)
        with patch("takeout_garmin_sync.garmin_auth.GarminAuth._load_session", return_value=session):
            result = auth.ensure_authenticated()
        assert result == "good-token"

    def test_ensure_authenticated_refreshes_expired_access(self, auth: GarminAuth):
        old_token = _expired_token(refresh_token="r-tok")
        new_token = _valid_token(access_token="new-access")
        session = GarminSession(di_token=old_token)
        with (
            patch("takeout_garmin_sync.garmin_auth.GarminAuth._load_session", return_value=session),
            patch("takeout_garmin_sync.garmin_auth._refresh_di_token", return_value=new_token),
            patch("takeout_garmin_sync.garmin_auth.GarminAuth._save_session"),
        ):
            result = auth.ensure_authenticated()
        assert result == "new-access"

    def test_ensure_authenticated_raises_when_no_browser_and_session_expired(
        self, auth: GarminAuth
    ):
        with patch("takeout_garmin_sync.garmin_auth.GarminAuth._load_session", return_value=None):
            with pytest.raises(RuntimeError, match="takeout-garmin-sync auth"):
                auth.ensure_authenticated(allow_browser=False)

    def test_token_status_no_session(self, auth: GarminAuth):
        with patch("takeout_garmin_sync.garmin_auth.GarminAuth._load_session", return_value=None):
            status = auth.token_status()
        assert status["state"] == "no_session"
        assert status["days_remaining"] is None

    def test_token_status_valid(self, auth: GarminAuth):
        session = GarminSession(di_token=_valid_token())
        with patch("takeout_garmin_sync.garmin_auth.GarminAuth._load_session", return_value=session):
            status = auth.token_status()
        assert status["state"] == "valid"
        assert status["days_remaining"] > 0

    def test_token_status_refresh_needed(self, auth: GarminAuth):
        session = GarminSession(di_token=_expired_token())
        with patch("takeout_garmin_sync.garmin_auth.GarminAuth._load_session", return_value=session):
            status = auth.token_status()
        assert status["state"] == "refresh_needed"

    def test_token_status_expired(self, auth: GarminAuth):
        session = GarminSession(di_token=_fully_expired_token())
        with patch("takeout_garmin_sync.garmin_auth.GarminAuth._load_session", return_value=session):
            status = auth.token_status()
        assert status["state"] == "expired"

    def test_load_session_from_file(self, tmp_path: Path):
        token = _valid_token("file-tok", "file-ref")
        session = GarminSession(di_token=token)
        session_file = tmp_path / "session.json"
        session_file.write_text(json.dumps(session.to_dict()))

        with patch("takeout_garmin_sync.secrets.load_session") as mock_load:
            mock_load.return_value = session.to_dict()
            auth = GarminAuth("e@e.com", "pw", session_path=session_file)
            loaded = auth._load_session()

        assert loaded is not None
        assert loaded.di_token.access_token == "file-tok"

    def test_ensure_authenticated_falls_back_to_browser_after_refresh_failure(
        self, auth: GarminAuth
    ):
        old_token = _expired_token(refresh_token="r-tok")
        new_di = _valid_token(access_token="browser-token")
        session = GarminSession(di_token=old_token)
        with (
            patch("takeout_garmin_sync.garmin_auth.GarminAuth._load_session", return_value=session),
            patch("takeout_garmin_sync.garmin_auth._refresh_di_token", side_effect=RuntimeError("fail")),
            patch("takeout_garmin_sync.garmin_auth.browser_login", return_value="ticket-xyz"),
            patch("takeout_garmin_sync.garmin_auth._exchange_ticket_for_tokens", return_value=new_di),
            patch("takeout_garmin_sync.garmin_auth.GarminAuth._save_session"),
        ):
            result = auth.ensure_authenticated(allow_browser=True)
        assert result == "browser-token"
