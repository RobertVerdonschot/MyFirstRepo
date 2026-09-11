from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)


class GarminNotAuthenticated(RuntimeError):
    """Raised when there is no usable saved Garmin session.

    Fix: run `python scripts/garmin_login_setup.py` once (interactively, so
    it can prompt for MFA) to create the token store the bot reuses.
    """


class GarminClient:
    def __init__(self, tokenstore: str) -> None:
        self._tokenstore = tokenstore
        self._api: Garmin | None = None

    def _get_api(self) -> Garmin:
        if self._api is not None:
            return self._api
        api = Garmin()
        try:
            api.login(self._tokenstore)
        except (GarminConnectAuthenticationError, GarminConnectConnectionError) as exc:
            raise GarminNotAuthenticated(
                "No valid saved Garmin session. Run scripts/garmin_login_setup.py first."
            ) from exc
        self._api = api
        return api

    def get_stress_for_date(self, date: str, tz: ZoneInfo) -> list[tuple[datetime, int]]:
        """Return (timestamp, stress_value) pairs for the given YYYY-MM-DD date.

        Negative values in Garmin's raw data mean "not measured" and are
        dropped here.
        """
        api = self._get_api()
        try:
            raw = api.get_stress_data(date)
        except GarminConnectTooManyRequestsError:
            raise
        except (GarminConnectAuthenticationError, GarminConnectConnectionError) as exc:
            raise GarminNotAuthenticated(
                "Garmin session was rejected. Run scripts/garmin_login_setup.py again."
            ) from exc

        readings: list[tuple[datetime, int]] = []
        for epoch_ms, value in raw.get("stressValuesArray") or []:
            if value is None or value < 0:
                continue
            ts = datetime.fromtimestamp(epoch_ms / 1000, tz=tz)
            readings.append((ts, int(value)))
        return readings
