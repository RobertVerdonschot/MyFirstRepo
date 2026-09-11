from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_message_id INTEGER,
    raw_text TEXT NOT NULL,
    meal_time TEXT NOT NULL,
    time_source TEXT NOT NULL,
    foods TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stress_readings (
    date TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    stress_value INTEGER NOT NULL,
    PRIMARY KEY (date, timestamp)
);

CREATE TABLE IF NOT EXISTS stress_fetch_log (
    date TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT
);
"""


@dataclass
class Meal:
    id: int
    telegram_message_id: int | None
    raw_text: str
    meal_time: datetime
    time_source: str
    foods: list[str]
    created_at: datetime


class Database:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def insert_meal(
        self,
        *,
        telegram_message_id: int | None,
        raw_text: str,
        meal_time: datetime,
        time_source: str,
        foods: list[str],
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO meals
                    (telegram_message_id, raw_text, meal_time, time_source, foods, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    telegram_message_id,
                    raw_text,
                    meal_time.isoformat(),
                    time_source,
                    json.dumps(foods),
                    datetime.now(meal_time.tzinfo).isoformat(),
                ),
            )
            return int(cur.lastrowid)

    def delete_meal(self, meal_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM meals WHERE id = ?", (meal_id,))
            return cur.rowcount > 0

    def get_meals(self, limit: int | None = None) -> list[Meal]:
        query = "SELECT * FROM meals ORDER BY meal_time DESC"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [self._row_to_meal(row) for row in rows]

    def get_all_meals_chronological(self) -> list[Meal]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM meals ORDER BY meal_time ASC").fetchall()
        return [self._row_to_meal(row) for row in rows]

    @staticmethod
    def _row_to_meal(row: sqlite3.Row) -> Meal:
        return Meal(
            id=row["id"],
            telegram_message_id=row["telegram_message_id"],
            raw_text=row["raw_text"],
            meal_time=datetime.fromisoformat(row["meal_time"]),
            time_source=row["time_source"],
            foods=json.loads(row["foods"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def has_fetched_date(self, date_str: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ok FROM stress_fetch_log WHERE date = ?", (date_str,)
            ).fetchone()
        return row is not None and bool(row["ok"])

    def mark_fetch(self, date_str: str, ok: bool, error: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO stress_fetch_log (date, fetched_at, ok, error)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    fetched_at = excluded.fetched_at,
                    ok = excluded.ok,
                    error = excluded.error
                """,
                (date_str, datetime.now().isoformat(), int(ok), error),
            )

    def store_stress_readings(self, date_str: str, readings: list[tuple[datetime, int]]) -> None:
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO stress_readings (date, timestamp, stress_value)
                VALUES (?, ?, ?)
                """,
                [(date_str, ts.isoformat(), value) for ts, value in readings],
            )

    def get_stress_readings(self, date_str: str) -> list[tuple[datetime, int]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT timestamp, stress_value FROM stress_readings WHERE date = ? ORDER BY timestamp",
                (date_str,),
            ).fetchall()
        return [(datetime.fromisoformat(row["timestamp"]), row["stress_value"]) for row in rows]
