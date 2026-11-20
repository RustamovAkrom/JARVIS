"""
core/briefing.py — which language and place the opening briefing is for.

The briefing speaks before the user has said a word, so it cannot follow
their language the way an answer does. It used to take only the language
memory had picked up, and a German user with nothing in memory heard the news
in English (reported 2026-10-22). Now, first to last:

    1. what the user asked for this briefing (routines: builtin 'briefing',
       language) — their own words beat every guess
    2. the Speaking language setting
    3. the language memory knows them by
    4. the interface's language

The news is searched in that language and in the country the operating system
says the user lives in (core/region), so the headlines are theirs too.
"""
from __future__ import annotations


def language(options: dict | None, remembered: str = "") -> tuple[str, str]:
    """(the language's English name for the model, its code for the search).
    The code is "" when only a name is known that is not a language name."""
    from core import speech_languages as SL

    def setting() -> str:
        try:
            from memory.config_manager import get_speaking_language
            return get_speaking_language()
        except Exception:
            return ""

    def interface() -> str:
        try:
            from core import i18n
            return i18n.current()
        except Exception:
            return ""

    for get in (lambda: (options or {}).get("language"), setting, lambda: remembered, interface):
        value = " ".join(str(get() or "").split())[:60]
        if not value:
            continue
        found = SL.from_name(value)
        if found:
            return SL.english_name(found), found
        return value, ""
    return "", ""


# What the routine holds when the user never chose a topic (core/routines/desktop).
DEFAULT_TOPIC = "top world news today"


def news_query(topic: str, country: str = "", language: str = "") -> str:
    """What to search the news for. A topic the user chose is searched as they
    said it. The default is an English phrase, and asked with a language and a
    country it found no news at all (Turkish, 2026-10-25), while the plain
    "news" brought that country's headlines in its language."""
    topic = " ".join(str(topic or "").split())
    if (not topic or topic == DEFAULT_TOPIC) and (country or language):
        return "news"
    return topic or DEFAULT_TOPIC


def country() -> str:
    """Where the user lives (ISO code), or "" when the system does not say."""
    try:
        from core.region import home_country
        return home_country()
    except Exception:
        return ""
