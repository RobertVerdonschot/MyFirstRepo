"""Best-effort extraction of an explicit clock time from a Dutch meal message.

This is a deliberately narrow regex parser, not a full natural-language date
parser: it only recognizes explicit clock times ("18:30", "om 8 uur", "8u")
and a couple of "yesterday" markers. Anything else (relative phrases like
"net", "een uurtje geleden", "straks") is NOT understood and falls back to
the Telegram message timestamp. If that's wrong for a given message, log the
meal again with an explicit time in it, or edit the message.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_TIME_COLON = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b")
_TIME_OM_UUR = re.compile(r"\bom\s+(\d{1,2})\s*uur\b", re.IGNORECASE)
_TIME_UUR_SHORT = re.compile(r"\b(\d{1,2})\s*u\b", re.IGNORECASE)
_YESTERDAY = re.compile(r"\bgisteren\b|\bgisteravond\b|\bgisterochtend\b|\bgistermiddag\b", re.IGNORECASE)

# Spans matched here are excluded from food-tag extraction later on.
TimeMatch = tuple[int, int]


def parse_meal_time(
    text: str, message_time: datetime, tz: ZoneInfo
) -> tuple[datetime, str, list[TimeMatch]]:
    """Return (meal_time, source, matched_spans).

    source is "message" if no explicit time was found in the text, or
    "parsed" if a clock time was extracted from the message.
    """
    local_message_time = message_time.astimezone(tz)
    matched_spans: list[TimeMatch] = []

    hour = minute = None
    for pattern in (_TIME_COLON, _TIME_OM_UUR, _TIME_UUR_SHORT):
        match = pattern.search(text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2)) if match.lastindex and match.lastindex >= 2 and match.group(2) else 0
            matched_spans.append(match.span())
            break

    day_offset = 0
    yesterday_match = _YESTERDAY.search(text)
    if yesterday_match:
        day_offset = -1
        matched_spans.append(yesterday_match.span())

    if hour is None and day_offset == 0:
        return local_message_time, "message", matched_spans

    base_date = local_message_time.date() + timedelta(days=day_offset)
    if hour is not None:
        meal_time = datetime(
            base_date.year, base_date.month, base_date.day, hour, minute or 0, tzinfo=tz
        )
        source = "parsed"
    else:
        # Only a day-offset marker, no explicit clock time: keep the
        # message's time-of-day and just shift the date.
        meal_time = local_message_time.replace(
            year=base_date.year, month=base_date.month, day=base_date.day
        )
        source = "parsed-approx"

    return meal_time, source, matched_spans
