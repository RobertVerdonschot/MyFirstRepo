from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from google.cloud import firestore


@dataclass
class Meal:
    id: str
    telegram_message_id: int | None
    raw_text: str
    meal_time: datetime
    time_source: str
    foods: list[str]


class FirestoreDatabase:
    """Firestore-backed storage.

    Two kinds of Garmin data are kept: `garmin_daily` holds the parsed stress
    values the analysis actually uses (fast to read), and `garmin_raw` holds
    the untouched API responses (stress, heart rate, body battery) so a
    different tool -- another AI, a notebook, whatever -- can re-derive
    something the current naive analysis doesn't.
    """

    def __init__(self, project: str | None = None) -> None:
        self._client = firestore.Client(project=project)

    # -- meals ----------------------------------------------------------

    def insert_meal(
        self,
        *,
        telegram_message_id: int | None,
        chat_id: int,
        raw_text: str,
        meal_time: datetime,
        time_source: str,
        foods: list[str],
        raw_update: dict[str, Any],
    ) -> str:
        meal_id = str(self._next_meal_id())
        self._client.collection("meals").document(meal_id).set(
            {
                "telegram_message_id": telegram_message_id,
                "chat_id": chat_id,
                "raw_text": raw_text,
                "meal_time": meal_time,
                "time_source": time_source,
                "foods": foods,
                "raw_update": raw_update,
                "created_at": firestore.SERVER_TIMESTAMP,
            }
        )
        return meal_id

    def _next_meal_id(self) -> int:
        counter_ref = self._client.collection("counters").document("meals")
        transaction = self._client.transaction()

        @firestore.transactional
        def bump(txn: firestore.Transaction) -> int:
            snapshot = counter_ref.get(transaction=txn)
            current = snapshot.to_dict().get("value", 0) if snapshot.exists else 0
            new_value = current + 1
            txn.set(counter_ref, {"value": new_value})
            return new_value

        return bump(transaction)

    def delete_meal(self, meal_id: str) -> bool:
        doc_ref = self._client.collection("meals").document(meal_id)
        if not doc_ref.get().exists:
            return False
        doc_ref.delete()
        return True

    def get_meals(self, limit: int | None = None) -> list[Meal]:
        query = self._client.collection("meals").order_by(
            "meal_time", direction=firestore.Query.DESCENDING
        )
        if limit is not None:
            query = query.limit(limit)
        return [self._doc_to_meal(doc) for doc in query.stream()]

    def get_all_meals_chronological(self) -> list[Meal]:
        query = self._client.collection("meals").order_by(
            "meal_time", direction=firestore.Query.ASCENDING
        )
        return [self._doc_to_meal(doc) for doc in query.stream()]

    @staticmethod
    def _doc_to_meal(doc: firestore.DocumentSnapshot) -> Meal:
        data = doc.to_dict()
        return Meal(
            id=doc.id,
            telegram_message_id=data.get("telegram_message_id"),
            raw_text=data["raw_text"],
            meal_time=data["meal_time"],
            time_source=data["time_source"],
            foods=data.get("foods", []),
        )

    # -- garmin -----------------------------------------------------------

    def has_fetched_date(self, date_str: str) -> bool:
        doc = self._client.collection("garmin_daily").document(date_str).get()
        return doc.exists and bool(doc.to_dict().get("ok"))

    def store_daily_stress(
        self,
        date_str: str,
        readings: list[tuple[datetime, int]],
        raw_payload: dict[str, Any],
    ) -> None:
        self._client.collection("garmin_daily").document(date_str).set(
            {
                "stress_values": [{"ts": ts.isoformat(), "value": v} for ts, v in readings],
                "fetched_at": firestore.SERVER_TIMESTAMP,
                "ok": True,
                "error": None,
            }
        )
        self.store_raw_garmin(date_str, "stress", raw_payload)

    def mark_fetch_failed(self, date_str: str, error: str) -> None:
        self._client.collection("garmin_daily").document(date_str).set(
            {"ok": False, "error": error, "fetched_at": firestore.SERVER_TIMESTAMP},
            merge=True,
        )

    def get_stress_readings(self, date_str: str) -> list[tuple[datetime, int]]:
        doc = self._client.collection("garmin_daily").document(date_str).get()
        if not doc.exists:
            return []
        data = doc.to_dict()
        return [
            (datetime.fromisoformat(r["ts"]), r["value"])
            for r in data.get("stress_values", [])
        ]

    def store_raw_garmin(self, date_str: str, kind: str, payload: Any) -> None:
        """Archive an untouched Garmin API response for a given day.

        kind is e.g. "stress", "heart_rate", "body_battery" -- anything the
        daily fetch job pulls. Kept separate from garmin_daily so the parsed
        fast-path data and the full archive can evolve independently.
        """
        self._client.collection("garmin_raw").document(date_str).set(
            {kind: payload, f"{kind}_fetched_at": firestore.SERVER_TIMESTAMP}, merge=True
        )
