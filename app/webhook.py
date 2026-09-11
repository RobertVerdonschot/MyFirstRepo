from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone

from flask import Blueprint, request

from app import telegram_api
from app.analysis import run_analysis
from app.config import Config, load_config
from app.sheets_db import SheetsDatabase
from app.food_extract import extract_food_tags
from app.garmin_client import GarminClient
from app.time_parser import parse_meal_time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("meal_stress_bot")

bp = Blueprint("webhook", __name__)

HELP_TEXT = (
    "Stuur gewoon een berichtje met wat je hebt gegeten, bijv.:\n"
    "  havermout met blauwe bessen\n"
    "  om 18:30 pizza margherita gegeten\n\n"
    "Staat er geen tijd in je bericht, dan gebruik ik het tijdstip van het Telegram-bericht.\n\n"
    "Commando's:\n"
    "/maaltijden - laatste gelogde maaltijden\n"
    "/verwijder <id> - een verkeerd gelogde maaltijd wissen\n"
    "/analyse - analyseer welk eten samenhangt met verhoogde stress\n"
    "/help - dit bericht"
)

_state: dict = {}
_state_lock = threading.Lock()


def _get_state() -> dict:
    if not _state:
        with _state_lock:
            if not _state:
                config = load_config()
                _state["config"] = config
                _state["db"] = SheetsDatabase(spreadsheet_id=config.spreadsheet_id)
                _state["garmin"] = GarminClient(
                    tokenstore_path=config.garmin_tokenstore,
                    tokens_b64=config.garmin_tokens_b64,
                )
    return _state


@bp.get("/health")
def health():
    return "ok", 200


@bp.post("/telegram-webhook")
def telegram_webhook():
    state = _get_state()
    config: Config = state["config"]

    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != config.telegram_secret_token:
        logger.warning("Webhook-aanroep met verkeerd/ontbrekend secret-token genegeerd.")
        return "forbidden", 403

    update = request.get_json(silent=True) or {}
    _handle_update(update, state)
    return "", 200


@bp.post("/tasks/fetch-garmin")
def fetch_garmin_task():
    state = _get_state()
    config: Config = state["config"]

    if request.headers.get("X-Scheduler-Secret") != config.scheduler_shared_secret:
        return "forbidden", 403

    db: SheetsDatabase = state["db"]
    garmin: GarminClient = state["garmin"]

    today = datetime.now(config.timezone).date()
    results = {}
    for day in (today - timedelta(days=1), today):
        date_str = day.isoformat()
        try:
            readings, raw_stress = garmin.get_stress_for_date(date_str, config.timezone)
            db.store_daily_stress(date_str, readings, raw_stress)

            raw_hr = garmin.get_heart_rate_for_date(date_str)
            db.store_raw_garmin(date_str, "heart_rate", raw_hr)

            raw_bb = garmin.get_body_battery_for_date(date_str)
            db.store_raw_garmin(date_str, "body_battery", raw_bb)

            results[date_str] = "ok"
        except Exception as exc:  # noqa: BLE001 - keep going, report per date
            logger.exception("Kon Garmin-data voor %s niet ophalen", date_str)
            db.mark_fetch_failed(date_str, str(exc))
            results[date_str] = f"error: {exc}"

    return results, 200


def _handle_update(update: dict, state: dict) -> None:
    config: Config = state["config"]
    message = update.get("message")
    if not message:
        return

    user = message.get("from") or {}
    if user.get("id") != config.allowed_telegram_user_id:
        logger.warning("Bericht van niet-toegestane user_id=%s genegeerd.", user.get("id"))
        return

    chat_id = message["chat"]["id"]
    text = message.get("text")
    if not text:
        return

    if text.startswith("/"):
        _handle_command(text, chat_id, message, state)
    else:
        _handle_meal(text, chat_id, message, state)


def _reply(config: Config, chat_id: int, text: str) -> None:
    telegram_api.send_message(config.telegram_bot_token, chat_id, text)


def _handle_command(text: str, chat_id: int, message: dict, state: dict) -> None:
    config: Config = state["config"]
    db: SheetsDatabase = state["db"]
    garmin: GarminClient = state["garmin"]

    command, *rest = text.split(maxsplit=1)
    command = command.split("@")[0].lower()  # strip /cmd@BotName
    args = rest[0].split() if rest else []

    if command in ("/start", "/help"):
        _reply(config, chat_id, HELP_TEXT)

    elif command in ("/maaltijden", "/meals"):
        meals = db.get_meals(limit=10)
        if not meals:
            _reply(config, chat_id, "Nog geen maaltijden gelogd.")
        else:
            lines = [
                f"#{m.id} {m.meal_time.strftime('%Y-%m-%d %H:%M')} - {m.raw_text}" for m in meals
            ]
            _reply(config, chat_id, "\n".join(lines))

    elif command in ("/verwijder", "/delete"):
        if not args:
            _reply(config, chat_id, "Gebruik: /verwijder <id>  (zie /maaltijden voor ids)")
        elif db.delete_meal(args[0]):
            _reply(config, chat_id, f"Maaltijd #{args[0]} verwijderd.")
        else:
            _reply(config, chat_id, f"Geen maaltijd met id #{args[0]} gevonden.")

    elif command in ("/analyse", "/analyze"):
        _reply(config, chat_id, "Bezig met analyseren, kan even duren...")
        report = run_analysis(db, garmin, config.timezone)
        _reply(config, chat_id, report)

    else:
        _reply(config, chat_id, f"Onbekend commando: {command}\n\n{HELP_TEXT}")


def _handle_meal(text: str, chat_id: int, message: dict, state: dict) -> None:
    config: Config = state["config"]
    db: SheetsDatabase = state["db"]

    message_time = datetime.fromtimestamp(message["date"], tz=timezone.utc)
    meal_time, source, spans = parse_meal_time(text, message_time, config.timezone)
    foods = extract_food_tags(text, spans)

    meal_id = db.insert_meal(
        telegram_message_id=message.get("message_id"),
        chat_id=chat_id,
        raw_text=text,
        meal_time=meal_time,
        time_source=source,
        foods=foods,
        raw_update=message,
    )

    time_note = {
        "message": "tijdstip van je bericht",
        "parsed": "tijd gevonden in je bericht",
        "parsed-approx": "dag aangepast op basis van je bericht, tijd bij benadering",
    }[source]

    _reply(
        config,
        chat_id,
        f"Gelogd (#{meal_id}) om {meal_time.strftime('%Y-%m-%d %H:%M')} ({time_note}).\n"
        f"Tags: {', '.join(foods) if foods else '(geen herkend)'}",
    )
