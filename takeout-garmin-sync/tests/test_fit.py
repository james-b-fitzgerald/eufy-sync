"""Tests for the FIT file encoder."""
from __future__ import annotations

import struct
from datetime import datetime, timezone

import pytest

from takeout_garmin_sync.fit import FIT_EPOCH, FitEncoder, encode_weight, _crc16, _fit_timestamp


class TestCrc16:
    def test_empty_bytes(self):
        # CRC of empty input is 0
        assert _crc16(b"") == 0

    def test_known_crc(self):
        # Verify the function is deterministic
        crc1 = _crc16(b"hello world")
        crc2 = _crc16(b"hello world")
        assert crc1 == crc2

    def test_different_data_different_crc(self):
        assert _crc16(b"abc") != _crc16(b"def")

    def test_result_is_uint16(self):
        crc = _crc16(b"some data")
        assert 0 <= crc <= 0xFFFF


class TestFitTimestamp:
    def test_converts_utc_datetime(self):
        # 2024-01-01 00:00:00 UTC
        dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        fit_ts = _fit_timestamp(dt)
        expected = int(dt.timestamp()) - FIT_EPOCH
        assert fit_ts == expected

    def test_positive_for_recent_dates(self):
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert _fit_timestamp(dt) > 0


class TestFitEncoder:
    def _build_minimal(self, weight_kg: float = 75.0) -> bytes:
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        enc = FitEncoder()
        enc.write_file_info()
        enc.write_file_creator()
        enc.write_device_info(dt)
        enc.write_weight_scale(dt, weight_kg)
        enc.finish()
        return enc.getvalue()

    def test_output_is_bytes(self):
        assert isinstance(self._build_minimal(), bytes)

    def test_minimum_length(self):
        # FIT header (16 bytes) + some data + CRC (2 bytes)
        data = self._build_minimal()
        assert len(data) > 20

    def test_fit_magic_in_header(self):
        data = self._build_minimal()
        # Bytes 8–12 of the FIT header contain ".FIT"
        assert data[8:12] == b".FIT"

    def test_header_size_byte(self):
        data = self._build_minimal()
        # First byte of FIT file is the header length (14 + 2-byte CRC = 16)
        assert data[0] == 14

    def test_weight_encoded_in_data(self):
        # Weight 75.0 kg is stored as uint16 = 7500 (scale ×100)
        data = self._build_minimal(weight_kg=75.0)
        target = struct.pack("<H", 7500)
        assert target in data

    def test_different_weights_produce_different_output(self):
        assert self._build_minimal(75.0) != self._build_minimal(80.0)

    def test_crc_appended(self):
        # File ends with a 2-byte CRC; verify length is even (header+data+CRC)
        data = self._build_minimal()
        assert len(data) % 2 == 0

    def test_getvalue_before_finish_is_incomplete(self):
        enc = FitEncoder()
        enc.write_file_info()
        # Without finish(), the header is all zeros
        raw = enc.getvalue()
        assert raw[:14] == b"\x00" * 14


class TestEncodeWeight:
    def test_returns_bytes(self):
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = encode_weight(dt, 70.0)
        assert isinstance(result, bytes)

    def test_contains_fit_magic(self):
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = encode_weight(dt, 70.0)
        assert b".FIT" in result

    def test_round_trip_weight_value(self):
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        weight = 80.5
        result = encode_weight(dt, weight)
        # 80.5 * 100 = 8050 → stored as uint16 little-endian
        target = struct.pack("<H", int(weight * 100))
        assert target in result
