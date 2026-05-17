from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from eufy_sync.config import AppConfig, EufyConfig, UserConfig
from eufy_sync.serverless import lambda_handler, run_sync_once


@pytest.fixture
def app_config() -> AppConfig:
    return AppConfig(
        sync_interval_minutes=15,
        users=[
            UserConfig(
                name="default",
                eufy=EufyConfig(email="e@example.com", password="pw"),
                garmin=None,
                strava=None,
            )
        ],
    )


def test_run_sync_once_requires_existing_config(tmp_path: Path):
    missing_cfg = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError, match="Config not found"):
        run_sync_once(config_path=missing_cfg, db_path=tmp_path / "state.db")


@patch("eufy_sync.serverless.sync_user", return_value={"strava": 2})
@patch("eufy_sync.serverless.SyncState")
@patch("eufy_sync.serverless.load_config")
def test_run_sync_once_success(mock_load, mock_state_cls, mock_sync_user, app_config, tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("users: []")
    mock_load.return_value = app_config
    mock_state = mock_state_cls.return_value

    result = run_sync_once(config_path=cfg_path, db_path=tmp_path / "state.db", headless=True)

    assert result["ok"] is True
    assert result["counts"] == {"strava": 2}
    assert result["total"] == 2
    assert result["failures"] == []
    mock_sync_user.assert_called_once()
    mock_state.close.assert_called_once()


@patch("eufy_sync.serverless.run_sync_once")
def test_lambda_handler_success(mock_run):
    mock_run.return_value = {"ok": True, "counts": {"garmin": 1}, "total": 1, "failures": []}
    response = lambda_handler({"headless": True}, None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["ok"] is True
    assert body["total"] == 1


@patch("eufy_sync.serverless.run_sync_once", side_effect=RuntimeError("boom"))
def test_lambda_handler_failure(mock_run):
    response = lambda_handler({}, None)

    assert response["statusCode"] == 500
    body = json.loads(response["body"])
    assert body["ok"] is False
    assert "boom" in body["error"]


def test_azure_function_handler_uses_request_json():
    from eufy_sync.serverless import azure_function_handler

    with patch("eufy_sync.serverless.lambda_handler", return_value={"statusCode": 200, "body": '{"ok": true}'}) as mock_lambda:
        req = SimpleNamespace(get_json=lambda: {"dry_run": True})
        response = azure_function_handler(req)
        mock_lambda.assert_called_once_with({"dry_run": True}, None)
        assert response["status_code"] == 200
        assert json.loads(response["body"]) == {"ok": True}
