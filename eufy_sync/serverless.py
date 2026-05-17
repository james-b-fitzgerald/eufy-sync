from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from eufy_sync.cli import DEFAULT_CONFIG, DEFAULT_DB
from eufy_sync.config import load_config
from eufy_sync.state import SyncState
from eufy_sync.sync import sync_user


def _bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_value(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def run_sync_once(
    *,
    config_path: Path | None = None,
    db_path: Path | None = None,
    backfill_days: int | None = None,
    dry_run: bool = False,
    headless: bool = True,
    verbose: bool = False,
) -> dict[str, Any]:
    config_path = config_path or DEFAULT_CONFIG
    db_path = db_path or DEFAULT_DB

    if not config_path.exists():
        raise FileNotFoundError(
            f"Config not found at {config_path}. "
            "Create it first or set EUFY_SYNC_CONFIG for your runtime."
        )

    log_level = "DEBUG" if verbose else "WARNING"
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
    logger = logging.getLogger("eufy_sync")

    config = load_config(config_path)
    state = SyncState(db_path)

    total_counts: dict[str, int] = {}
    failures: list[dict[str, str]] = []

    try:
        for user in config.users:
            try:
                counts = sync_user(
                    user,
                    state,
                    backfill_days=backfill_days,
                    headless=headless,
                    dry_run=dry_run,
                )
                for target_name, count in counts.items():
                    total_counts[target_name] = total_counts.get(target_name, 0) + count
                logger.info("User %s: synced %s", user.name, counts)
            except Exception as exc:
                logger.exception("Failed to sync user %s", user.name)
                failures.append({"user": user.name, "error": str(exc)})
    finally:
        state.close()

    return {
        "ok": len(failures) == 0,
        "counts": total_counts,
        "total": sum(total_counts.values()),
        "failures": failures,
    }


def lambda_handler(event: dict[str, Any] | None, _context: Any) -> dict[str, Any]:
    payload = event or {}
    try:
        config_path = Path(
            payload.get("config_path")
            or os.environ.get("EUFY_SYNC_CONFIG")
            or str(DEFAULT_CONFIG)
        ).expanduser()
        db_path = Path(
            payload.get("db_path")
            or os.environ.get("EUFY_SYNC_DB")
            or str(DEFAULT_DB)
        ).expanduser()

        result = run_sync_once(
            config_path=config_path,
            db_path=db_path,
            backfill_days=_int_value(payload.get("backfill_days")),
            dry_run=_bool_value(payload.get("dry_run"), False),
            headless=_bool_value(payload.get("headless"), True),
            verbose=_bool_value(payload.get("verbose"), False),
        )
        status = 200 if result["ok"] else 500
        return {"statusCode": status, "body": json.dumps(result)}
    except Exception as exc:
        return {
            "statusCode": 500,
            "body": json.dumps({"ok": False, "error": str(exc)}),
        }


def azure_function_handler(req: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if req is not None and hasattr(req, "get_json"):
        try:
            payload = req.get_json() or {}
        except Exception:
            payload = {}

    lambda_result = lambda_handler(payload, None)
    return {
        "status_code": lambda_result["statusCode"],
        "body": lambda_result["body"],
        "headers": {"Content-Type": "application/json"},
    }
