"""Secret and session storage for takeout-garmin-sync.

Resolution order for the Garmin OAuth2 session blob:

1. ``GARMIN_SESSION_JSON`` environment variable (JSON string) — for
   serverless / CI environments where no filesystem persists.
2. System keyring (macOS Keychain, GNOME Keyring, etc.) if available.
3. ``~/.takeout-garmin-sync/session.json`` — file fallback at ``0600``.

Garmin credentials (email + password, needed for browser re-auth) are
resolved from environment variables ``GARMIN_EMAIL`` / ``GARMIN_PASSWORD``
first, with optional YAML config as fallback.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SERVICE_NAME = "takeout-garmin-sync"
DEFAULT_SESSION_DIR = Path.home() / ".takeout-garmin-sync"


# ---------------------------------------------------------------------------
# Keyring availability
# ---------------------------------------------------------------------------

def _keyring_available() -> bool:
    """Return True if a non-stub keyring backend is available."""
    try:
        import keyring
        backend = keyring.get_keyring()
        name = type(backend).__name__.lower()
        return "fail" not in name and "null" not in name
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Garmin credentials (email + password)
# ---------------------------------------------------------------------------

def get_garmin_credentials(
    email: str | None = None,
    password: str | None = None,
) -> tuple[str, str]:
    """Return ``(email, password)`` for Garmin, raising if either is missing.

    Resolution order:
        1. Values passed explicitly to this function.
        2. ``GARMIN_EMAIL`` / ``GARMIN_PASSWORD`` environment variables.

    Args:
        email: Optional override; skips env-var lookup if provided.
        password: Optional override; skips env-var lookup if provided.

    Raises:
        ValueError: If either credential cannot be resolved.
    """
    email = email or os.environ.get("GARMIN_EMAIL", "")
    password = password or os.environ.get("GARMIN_PASSWORD", "")
    if not email or not password:
        raise ValueError(
            "Garmin credentials are required.\n"
            "Set GARMIN_EMAIL and GARMIN_PASSWORD environment variables, or\n"
            "supply them in the config file and run: takeout-garmin-sync auth"
        )
    return email, password


# ---------------------------------------------------------------------------
# Garmin OAuth2 session (token blob)
# ---------------------------------------------------------------------------

def store_session(data: dict, session_path: Path | None = None) -> None:
    """Persist a Garmin OAuth2 session dict.

    Tries the system keyring first; falls back to a ``0600`` file.
    """
    if _keyring_available():
        try:
            import keyring
            keyring.set_password(SERVICE_NAME, "garmin_session", json.dumps(data))
            logger.info("Saved Garmin session to system keyring")
            # Remove any stale file so we don't load stale tokens later
            path = session_path or DEFAULT_SESSION_DIR / "session.json"
            if path.exists():
                path.unlink()
            return
        except Exception as exc:
            logger.warning("Keyring write failed (%s); falling back to file", exc)

    _store_session_to_file(data, session_path)


def _store_session_to_file(data: dict, session_path: Path | None = None) -> None:
    path = session_path or DEFAULT_SESSION_DIR / "session.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data, indent=2))
    # Explicitly enforce 0600 in case the file pre-existed with looser permissions
    os.chmod(str(path), 0o600)
    logger.info("Saved Garmin session to %s", path)


def load_session(session_path: Path | None = None) -> dict | None:
    """Load a Garmin OAuth2 session dict, or return ``None`` if not found.

    Resolution order:
        1. ``GARMIN_SESSION_JSON`` env var (JSON string) — serverless use.
        2. System keyring.
        3. ``~/.takeout-garmin-sync/session.json`` file.
    """
    # 1. Env var (serverless)
    env_json = os.environ.get("GARMIN_SESSION_JSON")
    if env_json:
        try:
            return json.loads(env_json)
        except json.JSONDecodeError:
            logger.warning("GARMIN_SESSION_JSON is set but contains invalid JSON")

    # 2. Keyring
    if _keyring_available():
        try:
            import keyring
            raw = keyring.get_password(SERVICE_NAME, "garmin_session")
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.debug("Keyring read failed: %s", exc)

    # 3. File
    path = session_path or DEFAULT_SESSION_DIR / "session.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception as exc:
            logger.warning("Failed to read session from %s: %s", path, exc)

    return None


def delete_session(session_path: Path | None = None) -> None:
    """Remove any stored Garmin session from all backends."""
    if _keyring_available():
        try:
            import keyring
            keyring.delete_password(SERVICE_NAME, "garmin_session")
        except Exception:
            pass

    path = session_path or DEFAULT_SESSION_DIR / "session.json"
    if path.exists():
        path.unlink()
