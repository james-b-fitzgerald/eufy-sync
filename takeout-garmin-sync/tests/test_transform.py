"""Tests for the transform / normalisation pipeline."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from takeout_garmin_sync.source.takeout import WeightEntry
from takeout_garmin_sync.transform import (
    MIN_WEIGHT_KG,
    MAX_WEIGHT_KG,
    dedupe_by_date,
    entry_id,
    filter_after,
    filter_new,
    filter_valid,
    is_valid,
    normalize,
)


def _entry(
    year: int, month: int, day: int, hour: int = 12, weight_kg: float = 75.0
) -> WeightEntry:
    ts = datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)
    return WeightEntry(ts, weight_kg)


# ---------------------------------------------------------------------------
# is_valid / filter_valid
# ---------------------------------------------------------------------------

class TestValidation:
    def test_accepts_normal_weight(self):
        assert is_valid(_entry(2024, 1, 1, weight_kg=75.0))

    def test_rejects_too_light(self):
        assert not is_valid(_entry(2024, 1, 1, weight_kg=MIN_WEIGHT_KG - 0.1))

    def test_rejects_too_heavy(self):
        assert not is_valid(_entry(2024, 1, 1, weight_kg=MAX_WEIGHT_KG + 0.1))

    def test_accepts_boundary_min(self):
        assert is_valid(_entry(2024, 1, 1, weight_kg=MIN_WEIGHT_KG))

    def test_accepts_boundary_max(self):
        assert is_valid(_entry(2024, 1, 1, weight_kg=MAX_WEIGHT_KG))

    def test_filter_valid_drops_outliers(self):
        entries = [
            _entry(2024, 1, 1, weight_kg=75.0),
            _entry(2024, 1, 2, weight_kg=5.0),   # too light
            _entry(2024, 1, 3, weight_kg=300.0),  # too heavy
        ]
        valid = filter_valid(entries)
        assert len(valid) == 1
        assert valid[0].weight_kg == pytest.approx(75.0)

    def test_filter_valid_empty_input(self):
        assert filter_valid([]) == []


# ---------------------------------------------------------------------------
# dedupe_by_date
# ---------------------------------------------------------------------------

class TestDedupeByDate:
    def test_keeps_one_per_day(self):
        entries = [
            _entry(2024, 1, 1, hour=8,  weight_kg=75.0),
            _entry(2024, 1, 1, hour=20, weight_kg=74.5),
        ]
        result = dedupe_by_date(entries)
        assert len(result) == 1
        # Hour 20 is later; it should win
        assert result[0].weight_kg == pytest.approx(74.5)

    def test_keeps_multiple_dates(self):
        entries = [
            _entry(2024, 1, 1, weight_kg=75.0),
            _entry(2024, 1, 2, weight_kg=74.8),
            _entry(2024, 1, 3, weight_kg=74.6),
        ]
        result = dedupe_by_date(entries)
        assert len(result) == 3

    def test_result_sorted_oldest_first(self):
        entries = [
            _entry(2024, 1, 3, weight_kg=74.0),
            _entry(2024, 1, 1, weight_kg=76.0),
            _entry(2024, 1, 2, weight_kg=75.0),
        ]
        result = dedupe_by_date(entries)
        dates = [e.timestamp.date() for e in result]
        assert dates == sorted(dates)

    def test_empty_input(self):
        assert dedupe_by_date([]) == []

    def test_single_entry(self):
        entry = _entry(2024, 1, 1)
        assert dedupe_by_date([entry]) == [entry]


# ---------------------------------------------------------------------------
# entry_id
# ---------------------------------------------------------------------------

class TestEntryId:
    def test_stable_for_same_entry(self):
        e = _entry(2024, 1, 1, weight_kg=75.123)
        assert entry_id(e) == entry_id(e)

    def test_different_weight_gives_different_id(self):
        e1 = _entry(2024, 1, 1, weight_kg=75.0)
        e2 = _entry(2024, 1, 1, weight_kg=75.1)
        assert entry_id(e1) != entry_id(e2)

    def test_different_timestamp_gives_different_id(self):
        e1 = _entry(2024, 1, 1, weight_kg=75.0)
        e2 = _entry(2024, 1, 2, weight_kg=75.0)
        assert entry_id(e1) != entry_id(e2)

    def test_id_length(self):
        e = _entry(2024, 1, 1)
        assert len(entry_id(e)) == 16

    def test_id_is_hex(self):
        e = _entry(2024, 1, 1)
        assert all(c in "0123456789abcdef" for c in entry_id(e))


# ---------------------------------------------------------------------------
# filter_new
# ---------------------------------------------------------------------------

class TestFilterNew:
    def test_excludes_already_synced(self):
        e1 = _entry(2024, 1, 1, weight_kg=75.0)
        e2 = _entry(2024, 1, 2, weight_kg=74.5)
        result = filter_new([e1, e2], {entry_id(e1)})
        assert len(result) == 1
        assert result[0].weight_kg == pytest.approx(74.5)

    def test_all_synced_returns_empty(self):
        e = _entry(2024, 1, 1)
        result = filter_new([e], {entry_id(e)})
        assert result == []

    def test_empty_synced_set_returns_all(self):
        entries = [_entry(2024, 1, i) for i in range(1, 4)]
        assert filter_new(entries, set()) == entries

    def test_empty_entries(self):
        assert filter_new([], {"abc123"}) == []


# ---------------------------------------------------------------------------
# filter_after
# ---------------------------------------------------------------------------

class TestFilterAfter:
    def test_returns_all_when_after_is_none(self):
        entries = [_entry(2024, 1, i) for i in range(1, 4)]
        assert filter_after(entries, None) == entries

    def test_filters_before_timestamp(self):
        entries = [
            _entry(2024, 1, 1),
            _entry(2024, 1, 2),
            _entry(2024, 1, 3),
        ]
        after = datetime(2024, 1, 1, 12, tzinfo=timezone.utc)
        result = filter_after(entries, after)
        assert all(e.timestamp > after for e in result)

    def test_naive_after_treated_as_utc(self):
        e = _entry(2024, 1, 2)
        after = datetime(2024, 1, 1)  # naive
        result = filter_after([e], after)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# normalize (full pipeline)
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_full_pipeline(self):
        entries = [
            _entry(2024, 1, 1, hour=8,  weight_kg=75.0),
            _entry(2024, 1, 1, hour=20, weight_kg=74.8),  # same day, later → wins
            _entry(2024, 1, 2, weight_kg=5.0),            # too light → dropped
            _entry(2024, 1, 3, weight_kg=74.5),
        ]
        result = normalize(entries)
        assert len(result) == 2
        assert result[0].timestamp.date().day == 1
        assert result[0].weight_kg == pytest.approx(74.8)
        assert result[1].timestamp.date().day == 3

    def test_already_synced_skipped(self):
        e1 = _entry(2024, 1, 1, weight_kg=75.0)
        e2 = _entry(2024, 1, 2, weight_kg=74.5)
        result = normalize([e1, e2], already_synced={entry_id(e1)})
        assert len(result) == 1
        assert result[0].weight_kg == pytest.approx(74.5)

    def test_after_filter_applied(self):
        entries = [_entry(2024, 1, i) for i in range(1, 6)]
        after = datetime(2024, 1, 3, tzinfo=timezone.utc)
        result = normalize(entries, after=after)
        assert all(e.timestamp > after for e in result)

    def test_empty_input(self):
        assert normalize([]) == []
