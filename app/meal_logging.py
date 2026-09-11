from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config import Config
from app.food_extract import extract_food_tags
from app.sheets_db import SheetsDatabase
from app.time_parser import parse_meal_time

TIME_NOTES = {
    "message": "tijdstip van je bericht",
    "parsed": "tijd gevonden in je bericht",
    "parsed-approx": "dag aangepast op basis van je bericht, tijd bij benadering",
}


@dataclass
class LoggedMeal:
    meal_id: str
    meal_time: datetime
    time_source: str
    time_note: str
    foods: list[str]


def log_meal(
    db: SheetsDatabase,
    config: Config,
    *,
    text: str,
    message_time: datetime,
    telegram_message_id: int | None = None,
    chat_id: int | None = None,
    raw_update: dict[str, Any] | None = None,
) -> LoggedMeal:
    """Shared by the Telegram webhook and the webapp: parse, tag, and store one meal."""
    meal_time, source, spans = parse_meal_time(text, message_time, config.timezone)
    foods = extract_food_tags(text, spans)

    meal_id = db.insert_meal(
        telegram_message_id=telegram_message_id,
        chat_id=chat_id or 0,
        raw_text=text,
        meal_time=meal_time,
        time_source=source,
        foods=foods,
        raw_update=raw_update if raw_update is not None else {"text": text},
    )

    return LoggedMeal(
        meal_id=meal_id,
        meal_time=meal_time,
        time_source=source,
        time_note=TIME_NOTES[source],
        foods=foods,
    )
