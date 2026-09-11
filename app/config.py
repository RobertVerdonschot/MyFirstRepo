from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    allowed_telegram_user_id: int
    timezone: ZoneInfo
    telegram_secret_token: str
    scheduler_shared_secret: str
    webapp_token: str
    spreadsheet_id: str
    garmin_tokens_b64: str | None
    garmin_tokenstore: str | None


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is not set.")
    return value


def load_config() -> Config:
    """Config for the Cloud Run webhook app (main.py / app/webhook.py)."""
    return Config(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        allowed_telegram_user_id=int(_require("ALLOWED_TELEGRAM_USER_ID")),
        timezone=ZoneInfo(os.getenv("TIMEZONE", "Europe/Amsterdam")),
        telegram_secret_token=_require("TELEGRAM_SECRET_TOKEN"),
        scheduler_shared_secret=_require("SCHEDULER_SHARED_SECRET"),
        webapp_token=_require("WEBAPP_TOKEN"),
        spreadsheet_id=_require("SPREADSHEET_ID"),
        garmin_tokens_b64=os.getenv("GARMIN_TOKENS_B64") or None,
        garmin_tokenstore=os.getenv("GARMIN_TOKENSTORE") or None,
    )
