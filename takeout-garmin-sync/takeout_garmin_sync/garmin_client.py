"""Garmin Connect client for uploading weight data via FIT files.

Authenticates using :class:`~takeout_garmin_sync.garmin_auth.GarminAuth`
and uploads one FIT file per weight measurement to the Garmin Connect
``/upload-service/upload`` endpoint.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import httpx

from takeout_garmin_sync.fit import encode_weight
from takeout_garmin_sync.garmin_auth import API_HEADERS, GarminAuth
from takeout_garmin_sync.source.takeout import WeightEntry

logger = logging.getLogger(__name__)

CONNECT_API = "https://connectapi.garmin.com"
UPLOAD_URL = f"{CONNECT_API}/upload-service/upload"
WEIGHT_URL = f"{CONNECT_API}/weight-service/weight/range"


class GarminClient:
    """Authenticated Garmin Connect client.

    Args:
        auth: A :class:`~takeout_garmin_sync.garmin_auth.GarminAuth` instance.
    """

    def __init__(self, auth: GarminAuth) -> None:
        self._auth = auth
        self._http = httpx.Client(timeout=30.0)

    def authenticate(self, allow_browser: bool = True) -> None:
        """Obtain a valid access token and configure the HTTP client headers."""
        token = self._auth.ensure_authenticated(allow_browser=allow_browser)
        self._http.headers.update({**API_HEADERS, "authorization": f"Bearer {token}"})
        logger.info("Authenticated to Garmin Connect")

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        """Make an authenticated request, transparently refreshing on 401."""
        resp = self._http.request(method, url, **kwargs)
        if resp.status_code == 401:
            logger.info("Got 401 — refreshing Garmin token")
            token = self._auth.ensure_authenticated()
            self._http.headers["authorization"] = f"Bearer {token}"
            resp = self._http.request(method, url, **kwargs)
        if resp.status_code >= 400:
            logger.error("Garmin API error %d: %s", resp.status_code, resp.text[:500])
        resp.raise_for_status()
        return resp

    def has_weight_on_date(self, dt: datetime) -> bool:
        """Return True if Garmin already has a weight entry on *dt*'s UTC date.

        Uses the Garmin weight-service range endpoint to check for existing
        entries. On API errors the check returns False (optimistic: let the
        upload proceed; Garmin de-dupes by timestamp server-side).
        """
        date_str = dt.strftime("%Y-%m-%d")
        try:
            resp = self._request("GET", f"{WEIGHT_URL}/{date_str}/{date_str}")
            data = resp.json()
            entries = data.get("dailyWeightSummaries", data.get("dateWeightList", []))
            return len(entries) > 0
        except Exception as exc:
            logger.warning("Duplicate-check for %s failed: %s — proceeding anyway", date_str, exc)
            return False

    def upload_weight(self, entry: WeightEntry) -> dict:
        """Upload a single weight entry to Garmin Connect.

        Builds a FIT file containing only the weight measurement and posts
        it to the upload endpoint.

        Args:
            entry: The :class:`~takeout_garmin_sync.source.takeout.WeightEntry`
                   to upload.

        Returns:
            The JSON response body from Garmin (or ``{"status": <code>}`` if
            the response body is empty).
        """
        fit_data = encode_weight(entry.timestamp, entry.weight_kg)
        resp = self._request(
            "POST",
            UPLOAD_URL,
            files={"file": ("weight.fit", fit_data)},
        )
        logger.info("Uploaded %.2f kg at %s", entry.weight_kg, entry.timestamp.isoformat())
        return resp.json() if resp.text else {"status": resp.status_code}

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def __enter__(self) -> "GarminClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
