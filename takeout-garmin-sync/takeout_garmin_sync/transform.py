"""Normalize and deduplicate weight entries before uploading to Garmin.

Pipeline:
    1. ``filter_valid`` — drop readings outside a plausible human weight range.
    2. ``dedupe_by_date`` — keep one entry per UTC calendar date (latest wins).
    3. ``filter_new`` — drop entries that have already been synced (by ID).

A stable ``entry_id`` is derived from (timestamp, weight) so that the same
measurement always produces the same ID regardless of import order.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timezone

from takeout_garmin_sync.source.takeout import WeightEntry

logger = logging.getLogger(__name__)

# Plausible human weight bounds (inclusive)
MIN_WEIGHT_KG: float = 22.7   # ~50 lbs
MAX_WEIGHT_KG: float = 272.2  # ~600 lbs


# ---------------------------------------------------------------------------
# Individual helpers
# ---------------------------------------------------------------------------

def entry_id(entry: WeightEntry) -> str:
    """Return a stable 16-character hex ID for a weight entry.

    The ID is derived from the ISO timestamp + weight so that re-importing
    the same Takeout data always generates the same ID.
    """
    key = f"{entry.timestamp.isoformat()}:{entry.weight_kg:.4f}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def is_valid(entry: WeightEntry) -> bool:
    """Return True if the weight value is within plausible human bounds."""
    return MIN_WEIGHT_KG <= entry.weight_kg <= MAX_WEIGHT_KG


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def filter_valid(entries: list[WeightEntry]) -> list[WeightEntry]:
    """Drop entries whose weight falls outside :data:`MIN_WEIGHT_KG`–:data:`MAX_WEIGHT_KG`."""
    valid = [e for e in entries if is_valid(e)]
    dropped = len(entries) - len(valid)
    if dropped:
        logger.info("Dropped %d out-of-range weight entries", dropped)
    return valid


def dedupe_by_date(entries: list[WeightEntry]) -> list[WeightEntry]:
    """Keep the *latest* entry for each UTC calendar date.

    If two entries share the same date, the one with the newer ``timestamp``
    is retained. Returns entries sorted chronologically (oldest first).
    """
    by_date: dict[date, WeightEntry] = {}
    for entry in entries:
        d = entry.timestamp.date()
        if d not in by_date or entry.timestamp > by_date[d].timestamp:
            by_date[d] = entry
    result = sorted(by_date.values(), key=lambda e: e.timestamp)
    logger.debug(
        "Deduplicated %d entries → %d unique dates", len(entries), len(result)
    )
    return result


def filter_new(
    entries: list[WeightEntry],
    already_synced: set[str],
) -> list[WeightEntry]:
    """Return only entries whose ID is not in *already_synced*."""
    new = [e for e in entries if entry_id(e) not in already_synced]
    logger.debug(
        "Filtered %d already-synced entries; %d new entries remain",
        len(entries) - len(new),
        len(new),
    )
    return new


def filter_after(
    entries: list[WeightEntry],
    after: datetime | None,
) -> list[WeightEntry]:
    """Return only entries with timestamp strictly after *after* (UTC).

    If *after* is ``None``, all entries are returned (backfill mode).
    """
    if after is None:
        return entries
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    return [e for e in entries if e.timestamp > after]


# ---------------------------------------------------------------------------
# Convenience: full pipeline
# ---------------------------------------------------------------------------

def normalize(
    entries: list[WeightEntry],
    already_synced: set[str] | None = None,
    after: datetime | None = None,
) -> list[WeightEntry]:
    """Run the full normalization pipeline.

    Order:
        1. Validate weight range.
        2. Deduplicate by UTC date (keep latest per day).
        3. Optionally drop entries before *after* timestamp.
        4. Optionally drop entries already recorded in *already_synced*.

    Returns entries sorted oldest-first, ready for upload.
    """
    entries = filter_valid(entries)
    entries = dedupe_by_date(entries)
    if after is not None:
        entries = filter_after(entries, after)
    if already_synced:
        entries = filter_new(entries, already_synced)
    logger.info("%d entries ready to upload after normalization", len(entries))
    return entries
