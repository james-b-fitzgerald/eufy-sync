"""Tests for the Google Takeout source parser."""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from takeout_garmin_sync.source.takeout import (
    WeightEntry,
    _parse_data_points,
    load,
    load_from_dir,
    load_from_zip,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_data_point(
    weight_kg: float,
    nanos: int,
    data_type: str = "com.google.weight",
) -> dict:
    return {
        "fitValue": [{"fpVal": weight_kg}],
        "startTimeNanos": str(nanos),
        "endTimeNanos": str(nanos),
        "dataTypeName": data_type,
        "originDataSourceId": "test-source",
        "modifiedTimeMillis": str(nanos // 1_000_000),
    }


_TS_2024_01_01 = int(datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc).timestamp()) * 1_000_000_000
_TS_2024_01_02 = int(datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc).timestamp()) * 1_000_000_000


# ---------------------------------------------------------------------------
# _parse_data_points
# ---------------------------------------------------------------------------

class TestParseDataPoints:
    def test_parses_weight_entry(self):
        data = {"Data Points": [_make_data_point(75.5, _TS_2024_01_01)]}
        entries = _parse_data_points(data)
        assert len(entries) == 1
        assert entries[0].weight_kg == pytest.approx(75.5)
        assert entries[0].timestamp.tzinfo is not None

    def test_skips_non_weight_types(self):
        data = {
            "Data Points": [
                _make_data_point(75.5, _TS_2024_01_01, "com.google.step_count.delta"),
                _make_data_point(70.0, _TS_2024_01_02),
            ]
        }
        entries = _parse_data_points(data)
        assert len(entries) == 1
        assert entries[0].weight_kg == pytest.approx(70.0)

    def test_skips_empty_fitvalue(self):
        point = {
            "fitValue": [],
            "startTimeNanos": str(_TS_2024_01_01),
            "dataTypeName": "com.google.weight",
        }
        entries = _parse_data_points({"Data Points": [point]})
        assert entries == []

    def test_skips_malformed_point(self):
        bad = {"dataTypeName": "com.google.weight", "fitValue": [{"fpVal": "not-a-float"}]}
        entries = _parse_data_points({"Data Points": [bad]})
        assert entries == []

    def test_empty_data_points(self):
        entries = _parse_data_points({"Data Points": []})
        assert entries == []

    def test_missing_data_points_key(self):
        entries = _parse_data_points({})
        assert entries == []

    def test_timestamp_is_utc(self):
        data = {"Data Points": [_make_data_point(80.0, _TS_2024_01_01)]}
        entries = _parse_data_points(data)
        assert entries[0].timestamp.tzinfo == timezone.utc

    def test_multiple_entries(self):
        data = {
            "Data Points": [
                _make_data_point(75.0, _TS_2024_01_01),
                _make_data_point(74.5, _TS_2024_01_02),
            ]
        }
        entries = _parse_data_points(data)
        assert len(entries) == 2

    def test_source_id_captured(self):
        data = {"Data Points": [_make_data_point(75.0, _TS_2024_01_01)]}
        entries = _parse_data_points(data)
        assert entries[0].source_id == "test-source"


# ---------------------------------------------------------------------------
# WeightEntry
# ---------------------------------------------------------------------------

class TestWeightEntry:
    def test_requires_timezone_aware_timestamp(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            WeightEntry(datetime(2024, 1, 1), 75.0)

    def test_frozen(self):
        entry = WeightEntry(datetime(2024, 1, 1, tzinfo=timezone.utc), 75.0)
        with pytest.raises(Exception):
            entry.weight_kg = 80.0  # type: ignore[misc]

    def test_repr(self):
        entry = WeightEntry(datetime(2024, 1, 1, 12, tzinfo=timezone.utc), 75.123)
        assert "75.123" in repr(entry)
        assert "2024-01-01" in repr(entry)


# ---------------------------------------------------------------------------
# load_from_dir
# ---------------------------------------------------------------------------

class TestLoadFromDir:
    def test_loads_weight_json(self, tmp_path):
        data = {
            "Data Points": [
                _make_data_point(75.0, _TS_2024_01_01),
                _make_data_point(74.5, _TS_2024_01_02),
            ]
        }
        fit_dir = tmp_path / "Takeout" / "Fit" / "All Data"
        fit_dir.mkdir(parents=True)
        (fit_dir / "weight.json").write_text(json.dumps(data))

        entries = load_from_dir(tmp_path)
        assert len(entries) == 2

    def test_skips_non_fit_json(self, tmp_path):
        (tmp_path / "other.json").write_text(json.dumps({"key": "value"}))
        entries = load_from_dir(tmp_path)
        assert entries == []

    def test_skips_non_json_files(self, tmp_path):
        (tmp_path / "readme.txt").write_text("hello")
        entries = load_from_dir(tmp_path)
        assert entries == []

    def test_handles_malformed_json(self, tmp_path):
        (tmp_path / "bad.json").write_text("{ this is not json")
        entries = load_from_dir(tmp_path)
        assert entries == []

    def test_multiple_files(self, tmp_path):
        for i, ts in enumerate([_TS_2024_01_01, _TS_2024_01_02]):
            data = {"Data Points": [_make_data_point(75.0 + i, ts)]}
            (tmp_path / f"weight_{i}.json").write_text(json.dumps(data))
        entries = load_from_dir(tmp_path)
        assert len(entries) == 2


# ---------------------------------------------------------------------------
# load_from_zip
# ---------------------------------------------------------------------------

class TestLoadFromZip:
    def test_loads_weight_from_zip(self, tmp_path):
        data = {
            "Data Points": [_make_data_point(75.0, _TS_2024_01_01)]
        }
        zip_path = tmp_path / "takeout.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("Takeout/Fit/All Data/weight.json", json.dumps(data))
        entries = load_from_zip(zip_path)
        assert len(entries) == 1
        assert entries[0].weight_kg == pytest.approx(75.0)

    def test_skips_non_json_entries(self, tmp_path):
        zip_path = tmp_path / "takeout.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("README.txt", "hello")
        entries = load_from_zip(zip_path)
        assert entries == []

    def test_skips_malformed_json_in_zip(self, tmp_path):
        zip_path = tmp_path / "takeout.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("data.json", "not json")
        entries = load_from_zip(zip_path)
        assert entries == []


# ---------------------------------------------------------------------------
# load (dispatcher)
# ---------------------------------------------------------------------------

class TestLoad:
    def test_dispatches_to_zip(self, tmp_path):
        data = {"Data Points": [_make_data_point(75.0, _TS_2024_01_01)]}
        zip_path = tmp_path / "takeout.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("weight.json", json.dumps(data))
        entries = load(zip_path)
        assert len(entries) == 1

    def test_dispatches_to_dir(self, tmp_path):
        data = {"Data Points": [_make_data_point(76.0, _TS_2024_01_01)]}
        (tmp_path / "weight.json").write_text(json.dumps(data))
        entries = load(tmp_path)
        assert len(entries) == 1

    def test_raises_for_nonexistent_path(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load(tmp_path / "no-such-path")

    def test_raises_for_non_zip_file(self, tmp_path):
        p = tmp_path / "data.csv"
        p.write_text("a,b")
        with pytest.raises(ValueError, match="Expected a .zip"):
            load(p)
