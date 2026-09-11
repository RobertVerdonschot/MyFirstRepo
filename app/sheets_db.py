from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import google.auth
import gspread

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

MEALS_HEADER = [
    "id", "meal_time", "time_source", "raw_text", "foods",
    "telegram_message_id", "chat_id", "created_at", "raw_update_json",
]
STRESS_HEADER = ["date", "timestamp", "value"]
DAILY_HEADER = ["date", "ok", "error", "fetched_at"]
RAW_HEADER = ["date", "kind", "fetched_at", "payload_json"]
COUNTERS_HEADER = ["name", "value"]

_TABS: list[tuple[str, list[str]]] = [
    ("meals", MEALS_HEADER),
    ("garmin_stress", STRESS_HEADER),
    ("garmin_daily", DAILY_HEADER),
    ("garmin_raw", RAW_HEADER),
    ("counters", COUNTERS_HEADER),
]


@dataclass
class Meal:
    id: str
    telegram_message_id: int | None
    raw_text: str
    meal_time: datetime
    time_source: str
    foods: list[str]


class SheetsDatabase:
    """Google Sheets-backed storage.

    One spreadsheet, five tabs: `meals` (including the full raw Telegram
    message as JSON), `garmin_stress` (flattened stress readings, what the
    analysis actually reads), `garmin_daily` (per-date fetch status),
    `garmin_raw` (untouched Garmin API responses per date/kind, so a
    different tool can re-derive something the current analysis doesn't),
    and `counters` (a single running id for meals).

    The spreadsheet itself must already exist and be shared (Editor) with
    the service account this runs as -- this class only creates the tabs
    inside it, not the spreadsheet file itself.
    """

    def __init__(self, spreadsheet_id: str) -> None:
        credentials, _ = google.auth.default(scopes=SCOPES)
        client = gspread.authorize(credentials)
        self._sheet = client.open_by_key(spreadsheet_id)
        self._ensure_tabs()

    def _ensure_tabs(self) -> None:
        existing = {ws.title for ws in self._sheet.worksheets()}
        for title, header in _TABS:
            if title not in existing:
                # rows=1: start minimal and let gspread grow the sheet as we
                # append, rather than pre-creating a block of blank rows that
                # append_row would have to search past.
                ws = self._sheet.add_worksheet(title=title, rows=1, cols=len(header))
                ws.append_row(header)
        counters = self._sheet.worksheet("counters")
        if counters.find("meals") is None:
            counters.append_row(["meals", 0])

    # -- meals ------------------------------------------------------------

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
        ws = self._sheet.worksheet("meals")
        ws.append_row(
            [
                meal_id,
                meal_time.isoformat(),
                time_source,
                raw_text,
                ",".join(foods),
                telegram_message_id or "",
                chat_id,
                datetime.now(meal_time.tzinfo).isoformat(),
                json.dumps(raw_update, ensure_ascii=False),
            ]
        )
        return meal_id

    def _next_meal_id(self) -> int:
        ws = self._sheet.worksheet("counters")
        cell = ws.find("meals")
        current_cell = ws.cell(cell.row, 2)
        new_value = int(current_cell.value or 0) + 1
        ws.update_cell(cell.row, 2, new_value)
        return new_value

    def delete_meal(self, meal_id: str) -> bool:
        ws = self._sheet.worksheet("meals")
        cell = ws.find(str(meal_id), in_column=1)
        if cell is None:
            return False
        ws.delete_rows(cell.row)
        return True

    def get_meals(self, limit: int | None = None) -> list[Meal]:
        meals = self._all_meals()
        meals.sort(key=lambda m: m.meal_time, reverse=True)
        return meals[:limit] if limit else meals

    def get_all_meals_chronological(self) -> list[Meal]:
        meals = self._all_meals()
        meals.sort(key=lambda m: m.meal_time)
        return meals

    def _all_meals(self) -> list[Meal]:
        ws = self._sheet.worksheet("meals")
        meals = []
        for r in ws.get_all_records():
            if not r.get("id"):
                continue
            meals.append(
                Meal(
                    id=str(r["id"]),
                    telegram_message_id=int(r["telegram_message_id"])
                    if r.get("telegram_message_id")
                    else None,
                    raw_text=r["raw_text"],
                    meal_time=datetime.fromisoformat(r["meal_time"]),
                    time_source=r["time_source"],
                    foods=[f for f in str(r.get("foods") or "").split(",") if f],
                )
            )
        return meals

    # -- garmin -------------------------------------------------------------

    def has_fetched_date(self, date_str: str) -> bool:
        ws = self._sheet.worksheet("garmin_daily")
        cell = ws.find(date_str, in_column=1)
        if cell is None:
            return False
        row = ws.row_values(cell.row)
        return len(row) > 1 and row[1].upper() == "TRUE"

    def store_daily_stress(
        self,
        date_str: str,
        readings: list[tuple[datetime, int]],
        raw_payload: dict[str, Any],
    ) -> None:
        self._upsert_daily(date_str, ok=True, error="")
        stress_ws = self._sheet.worksheet("garmin_stress")
        self._replace_date_block(stress_ws, date_str)
        if readings:
            stress_ws.append_rows(
                [[date_str, ts.isoformat(), value] for ts, value in readings]
            )
        self.store_raw_garmin(date_str, "stress", raw_payload)

    def mark_fetch_failed(self, date_str: str, error: str) -> None:
        self._upsert_daily(date_str, ok=False, error=error)

    def _upsert_daily(self, date_str: str, ok: bool, error: str) -> None:
        ws = self._sheet.worksheet("garmin_daily")
        cell = ws.find(date_str, in_column=1)
        row = [date_str, str(ok).upper(), error, datetime.now().isoformat()]
        if cell is not None:
            ws.update([row], f"A{cell.row}:D{cell.row}")
        else:
            ws.append_row(row)

    def get_stress_readings_by_dates(
        self, date_strs: set[str]
    ) -> dict[str, list[tuple[datetime, int]]]:
        """Read the whole garmin_stress tab once and bucket by date.

        Used instead of a per-date read because the sheet grows unbounded
        (hundreds of rows/day) -- reading it once per /analyse run instead of
        once per meal keeps this well under Sheets API rate limits.
        """
        ws = self._sheet.worksheet("garmin_stress")
        result: dict[str, list[tuple[datetime, int]]] = {d: [] for d in date_strs}
        for r in ws.get_all_records():
            date_str = r.get("date")
            if date_str in result:
                result[date_str].append((datetime.fromisoformat(r["timestamp"]), int(r["value"])))
        return result

    def store_raw_garmin(self, date_str: str, kind: str, payload: Any) -> None:
        ws = self._sheet.worksheet("garmin_raw")
        values = ws.get_all_values()
        target_row = next(
            (
                idx
                for idx, row in enumerate(values[1:], start=2)
                if len(row) >= 2 and row[0] == date_str and row[1] == kind
            ),
            None,
        )
        new_row = [date_str, kind, datetime.now().isoformat(), json.dumps(payload, ensure_ascii=False)]
        if target_row is not None:
            ws.update([new_row], f"A{target_row}:D{target_row}")
        else:
            ws.append_row(new_row)

    def _replace_date_block(self, ws: gspread.Worksheet, date_str: str) -> None:
        """Delete a date's existing rows so store_daily_stress can rewrite them.

        Assumes rows for one date are contiguous, which holds as long as this
        class is the only writer: it always appends a date's rows together.
        """
        values = ws.get_all_values()
        matching_rows = [i for i, row in enumerate(values[1:], start=2) if row and row[0] == date_str]
        if matching_rows:
            ws.delete_rows(matching_rows[0], matching_rows[-1])
