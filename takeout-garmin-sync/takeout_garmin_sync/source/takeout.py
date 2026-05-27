"""Parse Google Fit weight data from Google Takeout archives.

Google Takeout exports Google Fit data as JSON files inside a zip archive
(or extracted directory). Weight data is stored under the ``com.google.weight``
data type.

Typical archive layout after extraction::

    Takeout/
    └── Fit/
        └── All Data/
            ├── derived_com.google.weight_...json
            └── ...

Each JSON file has the shape::

    {
      "Data Points": [
        {
          "fitValue": [{"fpVal": 75.5}],
          "startTimeNanos": "1609459200000000000",
          "endTimeNanos":   "1609459200000000000",
          "dataTypeName": "com.google.weight",
          "originDataSourceId": "...",
          "modifiedTimeMillis": "1609459200000"
        }
      ]
    }

The ``fpVal`` field is the weight in **kilograms**. The timestamps are
nanoseconds since the Unix epoch.
"""
from __future__ import annotations

import json
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

WEIGHT_DATA_TYPE = "com.google.weight"
NANOS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True)
class WeightEntry:
    """A single weight measurement parsed from Google Takeout."""

    timestamp: datetime  # UTC
    weight_kg: float
    source_id: str = ""  # originDataSourceId (informational)

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("WeightEntry.timestamp must be timezone-aware")

    def __repr__(self) -> str:
        return (
            f"WeightEntry(timestamp={self.timestamp.isoformat()}, "
            f"weight_kg={self.weight_kg:.3f})"
        )


def _parse_data_points(data: dict, filename: str = "") -> list[WeightEntry]:
    """Parse ``Data Points`` list from one Google Takeout JSON object."""
    entries: list[WeightEntry] = []
    for point in data.get("Data Points", []):
        if point.get("dataTypeName") != WEIGHT_DATA_TYPE:
            continue
        try:
            nanos = int(point["startTimeNanos"])
            ts = datetime.fromtimestamp(nanos / NANOS_PER_SECOND, tz=timezone.utc)
            fit_values = point.get("fitValue", [])
            if not fit_values:
                continue
            weight_kg = float(fit_values[0]["fpVal"])
            source_id = point.get("originDataSourceId", "")
            entries.append(WeightEntry(ts, weight_kg, source_id))
        except (KeyError, ValueError, TypeError, OSError) as exc:
            logger.debug("Skipping malformed data point in %s: %s", filename, exc)
    return entries


def _load_json_entries(raw: bytes | str, filename: str = "") -> list[WeightEntry]:
    """Attempt to parse weight entries from raw JSON bytes/string."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.debug("Skipping non-JSON file %s: %s", filename, exc)
        return []

    if not isinstance(data, dict) or "Data Points" not in data:
        return []

    return _parse_data_points(data, filename)


def load_from_dir(path: Path) -> list[WeightEntry]:
    """Load all weight entries from a Google Takeout extracted directory.

    Recursively searches *all* JSON files under ``path``. Files that do
    not contain weight data are silently skipped.
    """
    entries: list[WeightEntry] = []
    for json_file in sorted(path.rglob("*.json")):
        try:
            raw = json_file.read_bytes()
        except OSError as exc:
            logger.debug("Cannot read %s: %s", json_file, exc)
            continue
        found = _load_json_entries(raw, json_file.name)
        if found:
            logger.debug("Found %d weight entries in %s", len(found), json_file.name)
            entries.extend(found)

    logger.info("Loaded %d total weight entries from directory %s", len(entries), path)
    return entries


def load_from_zip(zip_path: Path) -> list[WeightEntry]:
    """Load all weight entries from a Google Takeout zip archive."""
    entries: list[WeightEntry] = []
    with zipfile.ZipFile(zip_path) as zf:
        for name in sorted(zf.namelist()):
            if not name.endswith(".json"):
                continue
            try:
                raw = zf.read(name)
            except Exception as exc:
                logger.debug("Cannot read zip entry %s: %s", name, exc)
                continue
            found = _load_json_entries(raw, name)
            if found:
                logger.debug("Found %d weight entries in %s", len(found), name)
                entries.extend(found)

    logger.info("Loaded %d total weight entries from zip %s", len(entries), zip_path)
    return entries


def load(path: Path) -> list[WeightEntry]:
    """Load weight entries from a zip archive or extracted directory.

    Args:
        path: Path to either a ``.zip`` file or a directory containing
              the extracted Takeout contents.

    Returns:
        Unsorted list of :class:`WeightEntry` objects. Deduplication and
        filtering are handled by :mod:`takeout_garmin_sync.transform`.

    Raises:
        ValueError: If *path* is neither a ``.zip`` file nor a directory.
        FileNotFoundError: If *path* does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")
    if path.is_file():
        if path.suffix.lower() == ".zip":
            return load_from_zip(path)
        raise ValueError(f"Expected a .zip archive or directory, got file: {path}")
    if path.is_dir():
        return load_from_dir(path)
    raise ValueError(f"Unsupported path type: {path}")
