"""Sync state persistence using SQLite.

Tracks which weight entries have already been uploaded to each target so
that re-running the sync command never creates duplicate Garmin entries.

The database lives at ``~/.takeout-garmin-sync/state.db`` by default and
is created automatically on first use.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / ".takeout-garmin-sync" / "state.db"


class SyncState:
    """SQLite-backed store for uploaded weight entry IDs.

    Args:
        db_path: Path to the SQLite database file. The parent directory
                 is created automatically if it does not exist.
    """

    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._init_db()

    def _init_db(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sync_log (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id             TEXT    NOT NULL,
                measurement_timestamp TEXT   NOT NULL,
                weight_kg            REAL    NOT NULL,
                target               TEXT    NOT NULL DEFAULT 'garmin',
                synced_at            TEXT    NOT NULL,
                response             TEXT,
                UNIQUE(entry_id, target)
            );
            CREATE INDEX IF NOT EXISTS idx_measurement_timestamp
                ON sync_log(measurement_timestamp);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def is_synced(self, entry_id: str, target: str = "garmin") -> bool:
        """Return True if *entry_id* has already been uploaded to *target*."""
        cur = self._conn.execute(
            "SELECT 1 FROM sync_log WHERE entry_id = ? AND target = ?",
            (entry_id, target),
        )
        return cur.fetchone() is not None

    def synced_ids(self, target: str = "garmin") -> set[str]:
        """Return the set of all entry IDs that have been uploaded to *target*."""
        cur = self._conn.execute(
            "SELECT entry_id FROM sync_log WHERE target = ?", (target,)
        )
        return {row[0] for row in cur.fetchall()}

    def get_latest_synced_timestamp(self, target: str = "garmin") -> datetime | None:
        """Return the UTC timestamp of the most recently synced entry, or ``None``."""
        cur = self._conn.execute(
            "SELECT MAX(measurement_timestamp) FROM sync_log WHERE target = ?",
            (target,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return datetime.fromisoformat(row[0])
        return None

    def get_sync_count(self, target: str = "garmin") -> int:
        """Return total number of entries synced to *target*."""
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM sync_log WHERE target = ?", (target,)
        )
        return cur.fetchone()[0]

    def get_recent(self, target: str = "garmin", limit: int = 10) -> list[dict]:
        """Return the *limit* most recently synced entries as dicts."""
        cur = self._conn.execute(
            """SELECT entry_id, measurement_timestamp, weight_kg, synced_at
               FROM sync_log
               WHERE target = ?
               ORDER BY measurement_timestamp DESC
               LIMIT ?""",
            (target, limit),
        )
        return [
            {
                "entry_id": row[0],
                "measurement_timestamp": row[1],
                "weight_kg": row[2],
                "synced_at": row[3],
            }
            for row in cur.fetchall()
        ]

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record_sync(
        self,
        entry_id: str,
        measurement_timestamp: str,
        weight_kg: float,
        target: str = "garmin",
        response: str | None = None,
    ) -> None:
        """Record that *entry_id* was successfully uploaded to *target*."""
        synced_at = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            """INSERT OR REPLACE INTO sync_log
               (entry_id, measurement_timestamp, weight_kg, target, synced_at, response)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (entry_id, measurement_timestamp, weight_kg, target, synced_at, response),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()

    def __enter__(self) -> "SyncState":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
