from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean
from zoneinfo import ZoneInfo

from app.firestore_db import FirestoreDatabase, Meal
from app.garmin_client import GarminClient, GarminNotAuthenticated

PRE_WINDOW = timedelta(minutes=30)
POST_WINDOW_START = timedelta(minutes=60)
POST_WINDOW_END = timedelta(minutes=150)
MIN_READINGS_PER_WINDOW = 2
MIN_MEALS_FOR_SIGNAL = 2


@dataclass
class MealResult:
    meal: Meal
    pre_avg: float
    post_avg: float
    post_max: float
    delta: float


def _dates_needed(meals: list[Meal]) -> set[str]:
    dates = set()
    for meal in meals:
        dates.add(meal.meal_time.date().isoformat())
        # the post-meal window can spill into the next calendar day
        dates.add((meal.meal_time + POST_WINDOW_END).date().isoformat())
    return dates


def _ensure_stress_cached(
    db: FirestoreDatabase, garmin: GarminClient, dates: set[str], tz: ZoneInfo
) -> dict[str, str]:
    """Fetch and cache any missing dates. Returns {date: error} for failures."""
    errors: dict[str, str] = {}
    for date_str in sorted(dates):
        if db.has_fetched_date(date_str):
            continue
        try:
            readings, raw = garmin.get_stress_for_date(date_str, tz)
            db.store_daily_stress(date_str, readings, raw)
        except GarminNotAuthenticated:
            raise
        except Exception as exc:  # noqa: BLE001 - surface per-date, keep going
            db.mark_fetch_failed(date_str, str(exc))
            errors[date_str] = str(exc)
    return errors


def _window_avg(readings: list[tuple[datetime, int]], start: datetime, end: datetime) -> list[int]:
    return [value for ts, value in readings if start <= ts < end]


def _evaluate_meal(db: FirestoreDatabase, meal: Meal) -> MealResult | None:
    pre_start = meal.meal_time - PRE_WINDOW
    pre_end = meal.meal_time
    post_start = meal.meal_time + POST_WINDOW_START
    post_end = meal.meal_time + POST_WINDOW_END

    readings = db.get_stress_readings(meal.meal_time.date().isoformat())
    if post_end.date() != meal.meal_time.date():
        readings = readings + db.get_stress_readings(post_end.date().isoformat())

    pre_vals = _window_avg(readings, pre_start, pre_end)
    post_vals = _window_avg(readings, post_start, post_end)

    if len(pre_vals) < MIN_READINGS_PER_WINDOW or len(post_vals) < MIN_READINGS_PER_WINDOW:
        return None

    pre_avg = mean(pre_vals)
    post_avg = mean(post_vals)
    return MealResult(
        meal=meal,
        pre_avg=pre_avg,
        post_avg=post_avg,
        post_max=max(post_vals),
        delta=post_avg - pre_avg,
    )


def run_analysis(db: FirestoreDatabase, garmin: GarminClient, tz: ZoneInfo) -> str:
    meals = db.get_all_meals_chronological()
    if len(meals) < 3:
        return (
            "Nog te weinig maaltijden gelogd voor een zinnige analyse "
            f"({len(meals)} tot nu toe). Log er minstens een stuk of wat en probeer het later opnieuw."
        )

    needed_dates = _dates_needed(meals)
    try:
        fetch_errors = _ensure_stress_cached(db, garmin, needed_dates, tz)
    except GarminNotAuthenticated as exc:
        return f"Kan geen Garmin-data ophalen: {exc}"

    results = [r for r in (_evaluate_meal(db, m) for m in meals) if r is not None]
    skipped = len(meals) - len(results)

    if not results:
        return (
            "Geen enkele gelogde maaltijd had genoeg Garmin-stressdata eromheen "
            "(nodig: metingen in de 30 min ervoor en 1-2,5 uur erna). "
            "Draagt je horloge continu, ook rond etenstijd?"
        )

    food_deltas: dict[str, list[float]] = {}
    for result in results:
        for tag in result.meal.foods:
            food_deltas.setdefault(tag, []).append(result.delta)

    stats = [
        (tag, mean(deltas), len(deltas))
        for tag, deltas in food_deltas.items()
    ]
    signal = sorted(
        (s for s in stats if s[2] >= MIN_MEALS_FOR_SIGNAL), key=lambda s: s[1], reverse=True
    )
    anecdotal = sorted((s for s in stats if s[2] < MIN_MEALS_FOR_SIGNAL), key=lambda s: s[1], reverse=True)

    lines = [
        f"Analyse over {len(meals)} gelogde maaltijden ({len(results)} bruikbaar, {skipped} zonder genoeg Garmin-data).",
        "",
        "Stressverandering = gemiddelde stress 1-2,5u na de maaltijd min gemiddelde stress 30 min ervoor.",
        "",
    ]

    if signal:
        lines.append("Grootste stressverhogers (>=2 metingen):")
        for tag, avg_delta, n in signal[:10]:
            sign = "+" if avg_delta >= 0 else ""
            lines.append(f"  {tag}: {sign}{avg_delta:.1f} (n={n})")
        lines.append("")
        lowest = [s for s in signal if s[1] < 0][:5]
        if lowest:
            lines.append("Laagste / stressverlagend:")
            for tag, avg_delta, n in sorted(lowest, key=lambda s: s[1]):
                lines.append(f"  {tag}: {avg_delta:.1f} (n={n})")
            lines.append("")
    else:
        lines.append("Nog geen enkel voedingswoord dat vaker dan 1x voorkomt met bruikbare data.")
        lines.append("")

    if anecdotal:
        lines.append("Losse waarnemingen (n=1, nog geen patroon):")
        for tag, avg_delta, n in anecdotal[:10]:
            sign = "+" if avg_delta >= 0 else ""
            lines.append(f"  {tag}: {sign}{avg_delta:.1f}")
        lines.append("")

    if fetch_errors:
        lines.append(f"Let op: kon voor {len(fetch_errors)} dag(en) geen Garmin-data ophalen.")
        lines.append("")

    lines.append(
        "Kanttekening: kleine steekproef, correlatie is geen oorzaak, en de "
        "woord-extractie is naief (geen echte voedingsdatabase). Stress wordt ook "
        "beinvloed door slaap, beweging en drukte -- dit is een startpunt, geen diagnose."
    )
    return "\n".join(lines)
