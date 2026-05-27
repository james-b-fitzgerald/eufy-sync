"""Minimal FIT file encoder for body-weight data.

Generates a Garmin-compatible FIT binary file containing a single
``weight_scale`` record. The file can be uploaded to Garmin Connect via
the ``/upload-service/upload`` endpoint.

This module is self-contained (no external dependencies) and adapted from
the FIT SDK protocol specification.
"""
from __future__ import annotations

import io
import struct
import time as _time
from datetime import datetime
from struct import pack

# FIT epoch: Dec 31, 1989 00:00:00 UTC
FIT_EPOCH = 631065600

# FIT global message numbers
_FILE_ID = 0
_FILE_CREATOR = 49
_DEVICE_INFO = 23
_WEIGHT_SCALE = 30

# FIT base types
_ENUM = 0x00
_UINT8 = 0x0D
_UINT16 = 0x84
_UINT32 = 0x86
_UINT16Z = 0x8B


def _crc16(data: bytes) -> int:
    """Calculate FIT CRC-16."""
    crc_table = [
        0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
        0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
    ]
    crc = 0
    for byte in data:
        tmp = crc_table[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ crc_table[byte & 0xF]
        tmp = crc_table[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ crc_table[(byte >> 4) & 0xF]
    return crc


def _fit_timestamp(dt: datetime) -> int:
    """Convert a :class:`~datetime.datetime` to a FIT timestamp.

    FIT timestamps are seconds since the FIT epoch (Dec 31, 1989 UTC).
    Timezone-aware datetimes use their actual UTC offset; naive datetimes
    are treated as local time (a warning is emitted).
    """
    if dt.tzinfo is None:
        import logging
        logging.getLogger(__name__).warning(
            "Naive datetime passed to _fit_timestamp; treating as local time. "
            "Pass a timezone-aware datetime to avoid incorrect FIT timestamps."
        )
        unix_ts = int(_time.mktime(dt.timetuple()))
    else:
        unix_ts = int(dt.timestamp())
    return unix_ts - FIT_EPOCH


class FitEncoder:
    """Build a FIT binary file in memory.

    Typical usage::

        enc = FitEncoder()
        enc.write_file_info()
        enc.write_file_creator()
        enc.write_device_info(dt)
        enc.write_weight_scale(dt, weight_kg=75.3)
        enc.finish()
        data = enc.getvalue()
    """

    def __init__(self) -> None:
        self._buf = io.BytesIO()
        self._data_size = 0
        # Write a 14-byte placeholder header; overwritten by finish()
        self._buf.write(b"\x00" * 14)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_raw(self, data: bytes) -> None:
        self._buf.write(data)
        self._data_size += len(data)

    def _write_definition(self, local_msg: int, global_msg: int, fields: list) -> None:
        """Write a definition message.

        *fields* is a list of ``(field_num, size_bytes, base_type)`` tuples.
        """
        header = 0x40 | (local_msg & 0x0F)
        self._write_raw(pack("B", header))
        self._write_raw(pack("BB", 0, 0))           # reserved + little-endian arch
        self._write_raw(pack("<H", global_msg))
        self._write_raw(pack("B", len(fields)))
        for field_num, size, base_type in fields:
            self._write_raw(pack("BBB", field_num, size, base_type))

    def _write_data(self, local_msg: int, values: bytes) -> None:
        """Write a data message."""
        self._write_raw(pack("B", local_msg & 0x0F))
        self._write_raw(values)

    # ------------------------------------------------------------------
    # Public message writers
    # ------------------------------------------------------------------

    def write_file_info(self, timestamp: int | None = None) -> None:
        """Write the required ``file_id`` message."""
        ts = timestamp if timestamp is not None else int(_time.time()) - FIT_EPOCH
        fields = [
            (0, 1, _ENUM),    # type
            (1, 2, _UINT16),  # manufacturer
            (2, 2, _UINT16),  # product
            (3, 4, 0x8C),     # serial_number (UINT32Z)
            (4, 4, _UINT32),  # time_created
        ]
        self._write_definition(0, _FILE_ID, fields)
        self._write_data(0, pack("<BHHII", 9, 1, 1, 0, ts))

    def write_file_creator(self) -> None:
        """Write the ``file_creator`` message."""
        self._write_definition(1, _FILE_CREATOR, [(0, 2, _UINT16)])
        self._write_data(1, pack("<H", 100))

    def write_device_info(self, dt: datetime) -> None:
        """Write a ``device_info`` message."""
        ts = _fit_timestamp(dt)
        fields = [
            (253, 4, _UINT32),  # timestamp
            (0,   2, _UINT16Z), # device_index
            (1,   1, _UINT8),   # device_type
            (2,   2, _UINT16),  # manufacturer
            (3,   2, _UINT16Z), # product
        ]
        self._write_definition(2, _DEVICE_INFO, fields)
        self._write_data(2, pack("<IHBHH", ts, 0, 0, 1, 1))

    def write_weight_scale(self, dt: datetime, weight_kg: float) -> None:
        """Write a ``weight_scale`` message.

        Only weight is required; all other body-composition fields are
        set to their FIT invalid sentinel values so Garmin ignores them.

        Args:
            dt: Measurement datetime (timezone-aware recommended).
            weight_kg: Weight in kilograms.
        """
        ts = _fit_timestamp(dt)
        fields = [
            (253, 4, _UINT32),  # timestamp
            (0,   2, _UINT16),  # weight          (scale ×100, kg)
            (1,   2, _UINT16),  # percent_fat     (scale ×100)
            (2,   2, _UINT16),  # percent_hydration (scale ×100)
            (4,   2, _UINT16),  # bone_mass       (scale ×100, kg)
            (5,   2, _UINT16),  # muscle_mass     (scale ×100, kg)
            (7,   2, _UINT16),  # basal_met       (scale ×4, kcal/day)
            (10,  1, _UINT8),   # metabolic_age
            (11,  1, _UINT8),   # visceral_fat_rating
        ]
        self._write_definition(3, _WEIGHT_SCALE, fields)
        self._write_data(
            3,
            pack(
                "<IHHHHHHBB",
                ts,
                int(weight_kg * 100),
                0xFFFF,  # percent_fat     — invalid
                0xFFFF,  # percent_hydration — invalid
                0xFFFF,  # bone_mass       — invalid
                0xFFFF,  # muscle_mass     — invalid
                0xFFFF,  # basal_met       — invalid
                0xFF,    # metabolic_age   — invalid
                0xFF,    # visceral_fat    — invalid
            ),
        )

    def finish(self) -> None:
        """Finalise the FIT file by writing the real header and CRC."""
        self._buf.seek(14)
        data_bytes = self._buf.read()

        header = pack(
            "<BBHI4s",
            14,                # header size
            0x20,              # protocol version 2.0
            0x08E8,            # profile version 2280
            len(data_bytes),   # data size
            b".FIT",           # data type magic
        )
        header += pack("<H", _crc16(header))

        data_crc = _crc16(data_bytes)

        self._buf = io.BytesIO()
        self._buf.write(header)
        self._buf.write(data_bytes)
        self._buf.write(pack("<H", data_crc))

    def getvalue(self) -> bytes:
        """Return the complete FIT file as bytes (call :meth:`finish` first)."""
        return self._buf.getvalue()


def encode_weight(dt: datetime, weight_kg: float) -> bytes:
    """Convenience function: build and return a complete FIT file for one weight reading."""
    enc = FitEncoder()
    enc.write_file_info()
    enc.write_file_creator()
    enc.write_device_info(dt)
    enc.write_weight_scale(dt, weight_kg)
    enc.finish()
    return enc.getvalue()
