"""Tests for eufy_sync.android_bridge.

All network I/O and filesystem side-effects are mocked; no real credentials or
connectivity required.  Tests cover every exported function:

  set_data_dir / _data_dir
  exchange_garmin_ticket
  exchange_strava_code
  run_sync
  get_token_status
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_bridge():
    """Import the bridge module and reset its module-level state."""
    import importlib
    import eufy_sync.android_bridge as bridge
    importlib.reload(bridge)          # ensures _DATA_DIR is None between tests
    return bridge


# ---------------------------------------------------------------------------
# set_data_dir / _data_dir
# ---------------------------------------------------------------------------

class TestSetDataDir(unittest.TestCase):

    def test_creates_directory_when_missing(self):
        bridge = _import_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "new_subdir")
            self.assertFalse(os.path.exists(target))
            bridge.set_data_dir(target)
            self.assertTrue(os.path.isdir(target))

    def test_stores_path_object(self):
        bridge = _import_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            bridge.set_data_dir(tmp)
            self.assertEqual(bridge._DATA_DIR, Path(tmp))

    def test_data_dir_defaults_to_home_garmin_sync(self):
        bridge = _import_bridge()
        bridge._DATA_DIR = None
        self.assertEqual(bridge._data_dir(), Path.home() / ".garmin-sync")

    def test_data_dir_returns_configured_path(self):
        bridge = _import_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            bridge.set_data_dir(tmp)
            self.assertEqual(bridge._data_dir(), Path(tmp))


# ---------------------------------------------------------------------------
# exchange_garmin_ticket
# ---------------------------------------------------------------------------

class TestExchangeGarminTicket(unittest.TestCase):

    def _make_fake_token(self):
        from eufy_sync.garmin_auth import TokenPair
        return TokenPair(
            access_token="at-abc",
            refresh_token="rt-xyz",
            expires_at=time.time() + 3600,
            refresh_expires_at=time.time() + 86400 * 365,
        )

    def test_returns_token_dict(self):
        bridge = _import_bridge()
        fake_token = self._make_fake_token()
        with tempfile.TemporaryDirectory() as tmp:
            bridge.set_data_dir(tmp)
            with patch("eufy_sync.garmin_auth._exchange_ticket_for_tokens", return_value=fake_token):
                result = bridge.exchange_garmin_ticket("ST-test-ticket")

        self.assertEqual(result["access_token"], "at-abc")
        self.assertEqual(result["refresh_token"], "rt-xyz")
        self.assertIn("expires_at", result)
        self.assertIn("refresh_expires_at", result)

    def test_saves_session_json(self):
        bridge = _import_bridge()
        fake_token = self._make_fake_token()
        with tempfile.TemporaryDirectory() as tmp:
            bridge.set_data_dir(tmp)
            with patch("eufy_sync.garmin_auth._exchange_ticket_for_tokens", return_value=fake_token):
                bridge.exchange_garmin_ticket("ST-test-ticket")

            session_file = Path(tmp) / "session.json"
            self.assertTrue(session_file.exists())
            data = json.loads(session_file.read_text())
            # GarminSession.to_dict() shape
            self.assertIn("di_token", data)
            self.assertEqual(data["di_token"]["access_token"], "at-abc")

    def test_session_file_has_restricted_permissions(self):
        bridge = _import_bridge()
        fake_token = self._make_fake_token()
        with tempfile.TemporaryDirectory() as tmp:
            bridge.set_data_dir(tmp)
            with patch("eufy_sync.garmin_auth._exchange_ticket_for_tokens", return_value=fake_token):
                bridge.exchange_garmin_ticket("ST-test-ticket")

            session_file = Path(tmp) / "session.json"
            mode = oct(session_file.stat().st_mode & 0o777)
            self.assertEqual(mode, "0o600")

    def test_delegates_to_exchange_ticket_for_tokens(self):
        bridge = _import_bridge()
        fake_token = self._make_fake_token()
        with tempfile.TemporaryDirectory() as tmp:
            bridge.set_data_dir(tmp)
            with patch(
                "eufy_sync.garmin_auth._exchange_ticket_for_tokens",
                return_value=fake_token,
            ) as mock_exchange:
                bridge.exchange_garmin_ticket("MY-TICKET")

        mock_exchange.assert_called_once_with("MY-TICKET")

    def test_propagates_exchange_error(self):
        bridge = _import_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            bridge.set_data_dir(tmp)
            with patch(
                "eufy_sync.garmin_auth._exchange_ticket_for_tokens",
                side_effect=RuntimeError("Garmin server down"),
            ):
                with self.assertRaises(RuntimeError):
                    bridge.exchange_garmin_ticket("BAD-TICKET")


# ---------------------------------------------------------------------------
# exchange_strava_code
# ---------------------------------------------------------------------------

class TestExchangeStravaCode(unittest.TestCase):

    def _make_strava_response(self):
        return {
            "access_token": "strava-at",
            "refresh_token": "strava-rt",
            "expires_at": int(time.time()) + 21600,
        }

    def _mock_httpx_post(self, response_data: dict):
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_data
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def test_returns_token_dict(self):
        bridge = _import_bridge()
        resp_data = self._make_strava_response()
        with tempfile.TemporaryDirectory() as tmp:
            bridge.set_data_dir(tmp)
            with patch("httpx.post", return_value=self._mock_httpx_post(resp_data)):
                result = bridge.exchange_strava_code("cid", "csec", "auth-code")

        self.assertEqual(result["access_token"], "strava-at")
        self.assertEqual(result["refresh_token"], "strava-rt")
        self.assertIn("expires_at", result)

    def test_posts_correct_payload_to_strava(self):
        bridge = _import_bridge()
        resp_data = self._make_strava_response()
        with tempfile.TemporaryDirectory() as tmp:
            bridge.set_data_dir(tmp)
            with patch("httpx.post", return_value=self._mock_httpx_post(resp_data)) as mock_post:
                bridge.exchange_strava_code("my-cid", "my-csec", "the-code")

        _, kwargs = mock_post.call_args
        payload = kwargs["data"]
        self.assertEqual(payload["client_id"], "my-cid")
        self.assertEqual(payload["client_secret"], "my-csec")
        self.assertEqual(payload["code"], "the-code")
        self.assertEqual(payload["grant_type"], "authorization_code")

    def test_saves_strava_token_json(self):
        bridge = _import_bridge()
        resp_data = self._make_strava_response()
        with tempfile.TemporaryDirectory() as tmp:
            bridge.set_data_dir(tmp)
            with patch("httpx.post", return_value=self._mock_httpx_post(resp_data)):
                bridge.exchange_strava_code("cid", "csec", "code")

            token_file = Path(tmp) / "strava_token.json"
            self.assertTrue(token_file.exists())
            saved = json.loads(token_file.read_text())
            self.assertEqual(saved["access_token"], "strava-at")

    def test_token_file_has_restricted_permissions(self):
        bridge = _import_bridge()
        resp_data = self._make_strava_response()
        with tempfile.TemporaryDirectory() as tmp:
            bridge.set_data_dir(tmp)
            with patch("httpx.post", return_value=self._mock_httpx_post(resp_data)):
                bridge.exchange_strava_code("cid", "csec", "code")

            token_file = Path(tmp) / "strava_token.json"
            mode = oct(token_file.stat().st_mode & 0o777)
            self.assertEqual(mode, "0o600")

    def test_uses_expires_in_fallback_when_expires_at_missing(self):
        """If the API returns expires_in instead of expires_at we still record a timestamp."""
        bridge = _import_bridge()
        resp_data = {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 3600,   # no expires_at
        }
        with tempfile.TemporaryDirectory() as tmp:
            bridge.set_data_dir(tmp)
            before = time.time()
            with patch("httpx.post", return_value=self._mock_httpx_post(resp_data)):
                result = bridge.exchange_strava_code("cid", "csec", "code")
            after = time.time()

        self.assertGreater(result["expires_at"], before + 3590)
        self.assertLess(result["expires_at"], after + 3610)

    def test_propagates_http_error(self):
        bridge = _import_bridge()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP 400")
        with tempfile.TemporaryDirectory() as tmp:
            bridge.set_data_dir(tmp)
            with patch("httpx.post", return_value=mock_resp):
                with self.assertRaises(Exception):
                    bridge.exchange_strava_code("cid", "csec", "bad-code")


# ---------------------------------------------------------------------------
# run_sync
# ---------------------------------------------------------------------------

class TestRunSync(unittest.TestCase):

    def _fake_sync_user(self, user, state, backfill_days=None, headless=False, dry_run=False):
        return {"garmin": 2, "strava": 1}

    def test_returns_json_string(self):
        bridge = _import_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("eufy_sync.sync.sync_user", side_effect=self._fake_sync_user):
                result = bridge.run_sync("e@e.com", "pw", "g@g.com", "gpw", None, None, tmp)

        data = json.loads(result)
        self.assertTrue(data["success"])
        self.assertEqual(data["counts"]["garmin"], 2)

    def test_success_shape(self):
        bridge = _import_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("eufy_sync.sync.sync_user", return_value={"garmin": 1}):
                result = bridge.run_sync("e@e.com", "pw", "g@g.com", "gpw", None, None, tmp)

        data = json.loads(result)
        self.assertIn("success", data)
        self.assertIn("counts", data)
        self.assertNotIn("error", data)

    def test_failure_shape(self):
        bridge = _import_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("eufy_sync.sync.sync_user", side_effect=RuntimeError("token expired")):
                result = bridge.run_sync("e@e.com", "pw", "g@g.com", "gpw", None, None, tmp)

        data = json.loads(result)
        self.assertFalse(data["success"])
        self.assertIn("error", data)
        self.assertIn("token expired", data["error"])

    def test_builds_garmin_only_config_when_strava_absent(self):
        """When strava creds are None, UserConfig.strava should be None."""
        bridge = _import_bridge()
        captured = {}

        def fake_sync(user, state, **kwargs):
            captured["user"] = user
            return {}

        with tempfile.TemporaryDirectory() as tmp:
            with patch("eufy_sync.sync.sync_user", side_effect=fake_sync):
                bridge.run_sync("e@e.com", "pw", "g@g.com", "gpw", None, None, tmp)

        self.assertIsNotNone(captured["user"].garmin)
        self.assertIsNone(captured["user"].strava)

    def test_builds_strava_only_config_when_garmin_absent(self):
        bridge = _import_bridge()
        captured = {}

        def fake_sync(user, state, **kwargs):
            captured["user"] = user
            return {}

        with tempfile.TemporaryDirectory() as tmp:
            with patch("eufy_sync.sync.sync_user", side_effect=fake_sync):
                bridge.run_sync("e@e.com", "pw", None, None, "cid", "csec", tmp)

        self.assertIsNone(captured["user"].garmin)
        self.assertIsNotNone(captured["user"].strava)

    def test_passes_headless_true_to_sync(self):
        """headless=True must always be set so no browser is opened."""
        bridge = _import_bridge()
        captured = {}

        def fake_sync(user, state, headless=False, **kwargs):
            captured["headless"] = headless
            return {}

        with tempfile.TemporaryDirectory() as tmp:
            with patch("eufy_sync.sync.sync_user", side_effect=fake_sync):
                bridge.run_sync("e@e.com", "pw", "g@g.com", "gpw", None, None, tmp)

        self.assertTrue(captured["headless"])

    def test_passes_backfill_days(self):
        bridge = _import_bridge()
        captured = {}

        def fake_sync(user, state, backfill_days=None, **kwargs):
            captured["backfill_days"] = backfill_days
            return {}

        with tempfile.TemporaryDirectory() as tmp:
            with patch("eufy_sync.sync.sync_user", side_effect=fake_sync):
                bridge.run_sync("e@e.com", "pw", "g@g.com", "gpw", None, None, tmp, backfill_days=30)

        self.assertEqual(captured["backfill_days"], 30)

    def test_sets_data_dir_from_parameter(self):
        bridge = _import_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            with patch("eufy_sync.sync.sync_user", return_value={}):
                bridge.run_sync("e@e.com", "pw", "g@g.com", "gpw", None, None, data_dir=tmp)

            self.assertEqual(bridge._DATA_DIR, Path(tmp))

    def test_state_db_is_closed_even_on_failure(self):
        """SyncState.close() must be called even when sync raises."""
        bridge = _import_bridge()
        close_calls = []

        import eufy_sync.state

        original_cls = eufy_sync.state.SyncState

        class PatchedState(original_cls):
            def close(self):
                close_calls.append(True)
                super().close()

        with tempfile.TemporaryDirectory() as tmp:
            # Patch SyncState in the state module so the lazy import inside
            # run_sync picks it up.
            with patch("eufy_sync.state.SyncState", PatchedState):
                with patch("eufy_sync.sync.sync_user", side_effect=RuntimeError("fail")):
                    bridge.run_sync("e@e.com", "pw", "g@g.com", "gpw", None, None, tmp)

        self.assertEqual(len(close_calls), 1, "SyncState.close() must be called exactly once")


# ---------------------------------------------------------------------------
# get_token_status
# ---------------------------------------------------------------------------

class TestGetTokenStatus(unittest.TestCase):

    def test_returns_valid_json(self):
        bridge = _import_bridge()
        garmin_status = {"state": "valid", "days_remaining": 200}
        strava_status = {"state": "valid", "days_remaining": None, "hours_remaining": 5}

        with tempfile.TemporaryDirectory() as tmp:
            with patch("eufy_sync.garmin_auth.GarminAuth.token_status", return_value=garmin_status), \
                 patch("eufy_sync.strava_client.StravaClient.token_status", return_value=strava_status):
                result = bridge.get_token_status(tmp)

        data = json.loads(result)
        self.assertIn("garmin", data)
        self.assertIn("strava", data)

    def test_garmin_token_reported(self):
        bridge = _import_bridge()
        garmin_status = {"state": "refresh_needed", "days_remaining": 5}
        strava_status = {"state": "no_session", "days_remaining": None}

        with tempfile.TemporaryDirectory() as tmp:
            with patch("eufy_sync.garmin_auth.GarminAuth.token_status", return_value=garmin_status), \
                 patch("eufy_sync.strava_client.StravaClient.token_status", return_value=strava_status):
                result = bridge.get_token_status(tmp)

        data = json.loads(result)
        self.assertEqual(data["garmin"]["state"], "refresh_needed")

    def test_strava_token_reported(self):
        bridge = _import_bridge()
        garmin_status = {"state": "valid", "days_remaining": 100}
        strava_status = {"state": "expired", "days_remaining": 0}

        with tempfile.TemporaryDirectory() as tmp:
            with patch("eufy_sync.garmin_auth.GarminAuth.token_status", return_value=garmin_status), \
                 patch("eufy_sync.strava_client.StravaClient.token_status", return_value=strava_status):
                result = bridge.get_token_status(tmp)

        data = json.loads(result)
        self.assertEqual(data["strava"]["state"], "expired")

    def test_garmin_error_does_not_crash(self):
        """A broken token store should return an error state, not raise."""
        bridge = _import_bridge()
        strava_status = {"state": "valid"}

        with tempfile.TemporaryDirectory() as tmp:
            with patch("eufy_sync.garmin_auth.GarminAuth.token_status", side_effect=Exception("disk full")), \
                 patch("eufy_sync.strava_client.StravaClient.token_status", return_value=strava_status):
                result = bridge.get_token_status(tmp)

        data = json.loads(result)
        self.assertEqual(data["garmin"]["state"], "error")
        self.assertIn("disk full", data["garmin"]["error"])

    def test_strava_error_does_not_crash(self):
        bridge = _import_bridge()
        garmin_status = {"state": "valid", "days_remaining": 50}

        with tempfile.TemporaryDirectory() as tmp:
            with patch("eufy_sync.garmin_auth.GarminAuth.token_status", return_value=garmin_status), \
                 patch("eufy_sync.strava_client.StravaClient.token_status", side_effect=Exception("network")):
                result = bridge.get_token_status(tmp)

        data = json.loads(result)
        self.assertEqual(data["strava"]["state"], "error")

    def test_sets_data_dir_from_parameter(self):
        bridge = _import_bridge()
        garmin_status = {"state": "valid", "days_remaining": 10}
        strava_status = {"state": "no_session"}

        with tempfile.TemporaryDirectory() as tmp:
            with patch("eufy_sync.garmin_auth.GarminAuth.token_status", return_value=garmin_status), \
                 patch("eufy_sync.strava_client.StravaClient.token_status", return_value=strava_status):
                bridge.get_token_status(data_dir=tmp)

            self.assertEqual(bridge._DATA_DIR, Path(tmp))


if __name__ == "__main__":
    unittest.main()
