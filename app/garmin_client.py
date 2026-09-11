from __future__ import annotations

import base64
import io
import tarfile
import tempfile
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
)


class GarminNotAuthenticated(RuntimeError):
    """Raised when there is no usable saved Garmin session.

    Fix: run `python scripts/garmin_login_setup.py` once (interactively, so
    it can prompt for MFA) and upload the result with
    `scripts/pack_and_upload_garmin_tokens.sh`.
    """


def _unpack_tokenstore(tokens_b64: str) -> str:
    tmp_dir = tempfile.mkdtemp(prefix="garmin_tokens_")
    raw = base64.b64decode(tokens_b64)
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        tar.extractall(tmp_dir, filter="data")
    return tmp_dir


class GarminClient:
    def __init__(self, tokenstore_path: str | None = None, tokens_b64: str | None = None) -> None:
        # Deliberately doesn't unpack/validate anything here: meal-logging
        # doesn't need Garmin at all, so a missing/bad tokenstore should only
        # break the code paths that actually call Garmin (see _get_api),
        # not construction of this client.
        self._tokenstore_path = tokenstore_path
        self._tokens_b64 = tokens_b64
        self._api: Garmin | None = None

    def _get_api(self) -> Garmin:
        if self._api is not None:
            return self._api

        if self._tokenstore_path:
            tokenstore = self._tokenstore_path
        elif self._tokens_b64:
            tokenstore = _unpack_tokenstore(self._tokens_b64)
        else:
            raise GarminNotAuthenticated(
                "No Garmin tokenstore configured (neither GARMIN_TOKENSTORE nor GARMIN_TOKENS_B64)."
            )

        api = Garmin()
        try:
            api.login(tokenstore)
        except (GarminConnectAuthenticationError, GarminConnectConnectionError) as exc:
            raise GarminNotAuthenticated(
                "Saved Garmin session was rejected. Re-run garmin_login_setup.py and re-upload it."
            ) from exc
        self._api = api
        return api

    def get_stress_for_date(
        self, date: str, tz: ZoneInfo
    ) -> tuple[list[tuple[datetime, int]], dict[str, Any]]:
        """Return ((timestamp, stress_value) pairs, raw API response) for a YYYY-MM-DD date.

        Negative values in the raw data mean "not measured" and are dropped
        from the parsed pairs, but kept in the raw response.
        """
        raw = self._call(lambda api: api.get_stress_data(date))
        readings: list[tuple[datetime, int]] = []
        for epoch_ms, value in raw.get("stressValuesArray") or []:
            if value is None or value < 0:
                continue
            ts = datetime.fromtimestamp(epoch_ms / 1000, tz=tz)
            readings.append((ts, int(value)))
        return readings, raw

    def get_heart_rate_for_date(self, date: str) -> dict[str, Any]:
        return self._call(lambda api: api.get_heart_rates(date))

    def get_body_battery_for_date(self, date: str) -> list[dict[str, Any]]:
        return self._call(lambda api: api.get_body_battery(date))

    def _call(self, fn):
        api = self._get_api()
        try:
            return fn(api)
        except (GarminConnectAuthenticationError, GarminConnectConnectionError) as exc:
            raise GarminNotAuthenticated(
                "Garmin session was rejected. Re-run garmin_login_setup.py and re-upload it."
            ) from exc
