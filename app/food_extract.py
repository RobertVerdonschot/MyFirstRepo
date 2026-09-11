"""Naive tokenizer that turns a meal message into a list of "food tags".

This is NOT a real food/nutrition parser -- it just splits the text into
words and drops stopwords and anything that looks like a time. It's good
enough to notice "elke keer als ik 'pizza' typ, stijgt mijn stress", but it
won't merge synonyms ("brood" vs "boterham") or understand quantities.
"""

from __future__ import annotations

import re

from app.time_parser import TimeMatch

_WORD = re.compile(r"[a-zA-ZÀ-ÿ]+")

_STOPWORDS = {
    "ik", "heb", "had", "at", "eet", "gegeten", "genuttigd", "net", "zojuist",
    "vandaag", "gisteren", "gisteravond", "gisterochtend", "gistermiddag",
    "vanochtend", "vanmorgen", "vanmiddag", "vanavond", "om", "uur", "u",
    "de", "het", "een", "en", "met", "voor", "na", "van", "in", "op", "bij",
    "erbij", "plus", "wat", "beetje", "stuk", "stukje", "paar", "wel",
    "wat", "nog", "ook", "was", "is", "lekker", "lekkere", "mijn", "deze",
    "die", "dit", "dat", "als", "ontbijt", "lunch", "diner", "avondeten",
    "tussendoor", "tussendoortje", "snack", "gedronken", "gedronken",
}


def extract_food_tags(text: str, exclude_spans: list[TimeMatch] | None = None) -> list[str]:
    exclude_spans = exclude_spans or []
    cleaned = text
    for start, end in sorted(exclude_spans, reverse=True):
        cleaned = cleaned[:start] + " " + cleaned[end:]

    tags: list[str] = []
    seen: set[str] = set()
    for match in _WORD.finditer(cleaned):
        word = match.group(0).lower()
        if len(word) < 3 or word in _STOPWORDS:
            continue
        if word in seen:
            continue
        seen.add(word)
        tags.append(word)
    return tags
