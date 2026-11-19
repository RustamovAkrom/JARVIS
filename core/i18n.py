"""Central interface localization API.

The existing phrase catalog in :mod:`memory.translations` remains the source
of truth. This module provides a stable key/phrase API for GUI components and
new code, with safe formatting and English fallback.
"""
from __future__ import annotations

from string import Formatter
from typing import Any

from memory.translations import LANGUAGES, translate


class _SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def current() -> str:
    """Return the persisted UI language, falling back to English."""
    try:
        from memory.config_manager import get_ui_language

        language = get_ui_language()
    except Exception:
        language = "en"
    return language if language in LANGUAGES else "en"


def supported() -> dict[str, str]:
    """Return a copy of supported interface languages."""
    return dict(LANGUAGES)


def t(text: str, /, **values: Any) -> str:
    """Translate a catalog phrase in the current UI language.

    Unknown phrases are intentionally returned unchanged. Missing format
    placeholders are preserved rather than raising and breaking the GUI.
    """
    if not isinstance(text, str):
        return text
    translated = translate(text, current())
    if not values:
        return translated
    try:
        return Formatter().vformat(translated, (), _SafeFormat(values))
    except (ValueError, TypeError, KeyError):
        return translated


def in_language(text: str, language: str, **values: Any) -> str:
    """Translate a phrase into an explicit supported language."""
    language = language if language in LANGUAGES else "en"
    translated = translate(text, language)
    if not values:
        return translated
    try:
        return Formatter().vformat(translated, (), _SafeFormat(values))
    except (ValueError, TypeError, KeyError):
        return translated
