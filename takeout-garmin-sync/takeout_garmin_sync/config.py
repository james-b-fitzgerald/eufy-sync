"""Configuration loading from YAML file with environment-variable interpolation.

Config file format (``~/.takeout-garmin-sync/config.yaml``)::

    garmin:
      email: "${GARMIN_EMAIL}"
      password: "${GARMIN_PASSWORD}"

    # Optional overrides
    state_db: ~/.takeout-garmin-sync/state.db
    session_path: ~/.takeout-garmin-sync/session.json

Environment variables referenced as ``${VAR_NAME}`` are expanded at load
time. If a variable is not set, :class:`ValueError` is raised with a clear
message.

Credentials can also be omitted from the config entirely and supplied
purely through ``GARMIN_EMAIL`` / ``GARMIN_PASSWORD`` environment variables.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path.home() / ".takeout-garmin-sync" / "config.yaml"


@dataclass
class GarminConfig:
    email: str
    password: str


@dataclass
class AppConfig:
    garmin: GarminConfig
    state_db: Path = field(
        default_factory=lambda: Path.home() / ".takeout-garmin-sync" / "state.db"
    )
    session_path: Path = field(
        default_factory=lambda: Path.home() / ".takeout-garmin-sync" / "session.json"
    )
    min_weight_kg: float = 22.7
    max_weight_kg: float = 272.2


def _interpolate_env_vars(value: str) -> str:
    """Expand ``${VAR_NAME}`` placeholders using current environment variables."""
    def replacer(match: re.Match) -> str:
        var = match.group(1)
        val = os.environ.get(var)
        if val is None:
            raise ValueError(
                f"Environment variable '{var}' referenced in config is not set."
            )
        return val
    return re.sub(r"\$\{(\w+)}", replacer, value)


def _walk(obj: object) -> object:
    """Recursively interpolate env vars in all string values of a YAML structure."""
    if isinstance(obj, str):
        return _interpolate_env_vars(obj)
    if isinstance(obj, dict):
        return {k: _walk(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(item) for item in obj]
    return obj


def _resolve_path(value: str) -> Path:
    """Expand ``~`` and return a :class:`~pathlib.Path`."""
    return Path(value).expanduser()


def load_config(path: Path = DEFAULT_CONFIG) -> AppConfig:
    """Load and validate the application config from *path*.

    If *path* does not exist, an :class:`AppConfig` is constructed solely
    from environment variables (``GARMIN_EMAIL``, ``GARMIN_PASSWORD``).
    This allows fully config-file-free operation in serverless environments.

    Raises:
        ValueError: If required credentials are absent from both the config
                    file and environment variables.
    """
    raw: dict = {}
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        raw = _walk(raw)

    # Garmin credentials: config file → env vars
    garmin_raw = raw.get("garmin", {})
    email = garmin_raw.get("email") or os.environ.get("GARMIN_EMAIL", "")
    password = garmin_raw.get("password") or os.environ.get("GARMIN_PASSWORD", "")

    if not email or not password:
        raise ValueError(
            "Garmin credentials not found.\n"
            "Set GARMIN_EMAIL and GARMIN_PASSWORD environment variables, or\n"
            "add a 'garmin' section to your config file."
        )

    state_db = _resolve_path(
        raw.get("state_db", str(Path.home() / ".takeout-garmin-sync" / "state.db"))
    )
    session_path = _resolve_path(
        raw.get(
            "session_path",
            str(Path.home() / ".takeout-garmin-sync" / "session.json"),
        )
    )

    return AppConfig(
        garmin=GarminConfig(email=email, password=password),
        state_db=state_db,
        session_path=session_path,
        min_weight_kg=float(raw.get("min_weight_kg", 22.7)),
        max_weight_kg=float(raw.get("max_weight_kg", 272.2)),
    )
