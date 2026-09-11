from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    allowed_telegram_user_id: int
    timezone: ZoneInfo
    db_path: str
    garmin_tokenstore: str
    garmin_email: str | None
    garmin_password: str | None


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Environment variable {name} is not set. Copy .env.example to .env and fill it in."
        )
    return value


def load_config() -> Config:
    db_path = os.getenv("DB_PATH", "data/meals.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    tokenstore = os.getenv("GARMIN_TOKENSTORE", "data/garmin_tokens")
    Path(tokenstore).mkdir(parents=True, exist_ok=True)

    return Config(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        allowed_telegram_user_id=int(_require("ALLOWED_TELEGRAM_USER_ID")),
        timezone=ZoneInfo(os.getenv("TIMEZONE", "Europe/Amsterdam")),
        db_path=db_path,
        garmin_tokenstore=tokenstore,
        garmin_email=os.getenv("GARMIN_EMAIL") or None,
        garmin_password=os.getenv("GARMIN_PASSWORD") or None,
    )
