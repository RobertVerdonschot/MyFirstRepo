from __future__ import annotations

import logging
from functools import wraps

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.analysis import run_analysis
from app.config import Config, load_config
from app.db import Database
from app.food_extract import extract_food_tags
from app.garmin_client import GarminClient
from app.time_parser import parse_meal_time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("meal_stress_bot")

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


def restricted(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        config: Config = context.bot_data["config"]
        user = update.effective_user
        if user is None or user.id != config.allowed_telegram_user_id:
            logger.warning("Genegeerd bericht van niet-toegestane user_id=%s", user.id if user else None)
            return
        return await func(update, context)

    return wrapper


@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT)


@restricted
async def log_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]

    text = update.message.text
    message_time = update.message.date  # tz-aware UTC datetime from Telegram

    meal_time, source, spans = parse_meal_time(text, message_time, config.timezone)
    foods = extract_food_tags(text, spans)

    meal_id = db.insert_meal(
        telegram_message_id=update.message.message_id,
        raw_text=text,
        meal_time=meal_time,
        time_source=source,
        foods=foods,
    )

    time_note = {
        "message": "tijdstip van je bericht",
        "parsed": "tijd gevonden in je bericht",
        "parsed-approx": "dag aangepast op basis van je bericht, tijd bij benadering",
    }[source]

    await update.message.reply_text(
        f"Gelogd (#{meal_id}) om {meal_time.strftime('%Y-%m-%d %H:%M')} ({time_note}).\n"
        f"Tags: {', '.join(foods) if foods else '(geen herkend)'}"
    )


@restricted
async def list_meals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    meals = db.get_meals(limit=10)
    if not meals:
        await update.message.reply_text("Nog geen maaltijden gelogd.")
        return
    lines = [
        f"#{m.id} {m.meal_time.strftime('%Y-%m-%d %H:%M')} - {m.raw_text}" for m in meals
    ]
    await update.message.reply_text("\n".join(lines))


@restricted
async def delete_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.bot_data["db"]
    if not context.args:
        await update.message.reply_text("Gebruik: /verwijder <id>  (zie /maaltijden voor ids)")
        return
    try:
        meal_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Dat id snap ik niet, gebruik een getal.")
        return
    if db.delete_meal(meal_id):
        await update.message.reply_text(f"Maaltijd #{meal_id} verwijderd.")
    else:
        await update.message.reply_text(f"Geen maaltijd met id #{meal_id} gevonden.")


@restricted
async def analyse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: Config = context.bot_data["config"]
    db: Database = context.bot_data["db"]
    garmin: GarminClient = context.bot_data["garmin"]

    await update.message.reply_text("Bezig met analyseren, kan even duren...")
    report = run_analysis(db, garmin, config.timezone)
    await update.message.reply_text(report)


def build_application() -> Application:
    config = load_config()
    db = Database(config.db_path)
    garmin = GarminClient(config.garmin_tokenstore)

    application = Application.builder().token(config.telegram_bot_token).build()
    application.bot_data["config"] = config
    application.bot_data["db"] = db
    application.bot_data["garmin"] = garmin

    application.add_handler(CommandHandler(["start", "help"], start))
    application.add_handler(CommandHandler(["maaltijden", "meals"], list_meals))
    application.add_handler(CommandHandler(["verwijder", "delete"], delete_meal))
    application.add_handler(CommandHandler(["analyse", "analyze"], analyse))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_meal))
    return application


def main() -> None:
    application = build_application()
    logger.info("Bot gestart, wacht op berichten (long polling)...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
