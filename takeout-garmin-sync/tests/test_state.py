"""Tests for SQLite sync state persistence."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from takeout_garmin_sync.state import SyncState


@pytest.fixture
def state(tmp_path: Path) -> SyncState:
    with SyncState(tmp_path / "test_state.db") as s:
        yield s


class TestSyncState:
    def test_is_not_synced_initially(self, state: SyncState):
        assert not state.is_synced("abc123")

    def test_record_and_check_synced(self, state: SyncState):
        state.record_sync("abc123", "2024-01-01T12:00:00+00:00", 75.0)
        assert state.is_synced("abc123")

    def test_different_target_not_synced(self, state: SyncState):
        state.record_sync("abc123", "2024-01-01T12:00:00+00:00", 75.0, target="garmin")
        assert not state.is_synced("abc123", target="strava")

    def test_synced_ids_returns_set(self, state: SyncState):
        state.record_sync("id1", "2024-01-01T12:00:00+00:00", 75.0)
        state.record_sync("id2", "2024-01-02T12:00:00+00:00", 74.5)
        ids = state.synced_ids()
        assert ids == {"id1", "id2"}

    def test_synced_ids_per_target(self, state: SyncState):
        state.record_sync("id1", "2024-01-01T12:00:00+00:00", 75.0, target="garmin")
        state.record_sync("id2", "2024-01-02T12:00:00+00:00", 74.5, target="other")
        assert state.synced_ids("garmin") == {"id1"}
        assert state.synced_ids("other") == {"id2"}

    def test_get_latest_synced_timestamp_none_when_empty(self, state: SyncState):
        assert state.get_latest_synced_timestamp() is None

    def test_get_latest_synced_timestamp(self, state: SyncState):
        state.record_sync("id1", "2024-01-01T12:00:00+00:00", 75.0)
        state.record_sync("id2", "2024-01-03T12:00:00+00:00", 74.0)
        state.record_sync("id3", "2024-01-02T12:00:00+00:00", 74.5)
        latest = state.get_latest_synced_timestamp()
        assert latest is not None
        assert latest.date().day == 3

    def test_get_sync_count(self, state: SyncState):
        assert state.get_sync_count() == 0
        state.record_sync("id1", "2024-01-01T12:00:00+00:00", 75.0)
        assert state.get_sync_count() == 1
        state.record_sync("id2", "2024-01-02T12:00:00+00:00", 74.5)
        assert state.get_sync_count() == 2

    def test_get_recent(self, state: SyncState):
        for i in range(5):
            state.record_sync(
                f"id{i}",
                f"2024-01-0{i+1}T12:00:00+00:00",
                75.0 - i * 0.1,
            )
        recent = state.get_recent(limit=3)
        assert len(recent) == 3
        assert all("entry_id" in r for r in recent)
        assert all("weight_kg" in r for r in recent)

    def test_idempotent_record_sync(self, state: SyncState):
        # Recording the same entry_id twice should not raise
        state.record_sync("dup", "2024-01-01T12:00:00+00:00", 75.0)
        state.record_sync("dup", "2024-01-01T12:00:00+00:00", 75.0)
        assert state.get_sync_count() == 1

    def test_db_created_on_init(self, tmp_path: Path):
        db_path = tmp_path / "subdir" / "nested" / "state.db"
        assert not db_path.parent.exists()
        with SyncState(db_path):
            pass
        assert db_path.exists()

    def test_context_manager(self, tmp_path: Path):
        with SyncState(tmp_path / "state.db") as s:
            s.record_sync("x", "2024-01-01T00:00:00+00:00", 70.0)
        # After __exit__ the connection is closed; object is not usable,
        # but no exception should have been raised.
