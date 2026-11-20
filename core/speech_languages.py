"""
core/speech_languages.py — the languages the user can SPEAK to the assistant.

Not the interface's languages (core/i18n: the menus, nine translated). These
are the languages the subscription's voice speaks: the relay lists them with
its voices (backend/gpu_proxy.speech_languages), so a language the servers
add appears in the app with no update. A relay that lists none (one from
before 0.4.2) gets DEFAULT_CODES, the default voice's own list, so the
choice is never empty.

Names are shown in the INTERFACE's language ("Danish" in English, "Danca" in
Turkish), from locales/names/language_names.json (made from Unicode CLDR by
tools/i18n/make_language_names.py); a language without a name there shows its
English name, and failing that its code.
"""
from __future__ import annotations

import json
from pathlib import Path

# VoxCPM2's 30 languages (its documentation), the same list as
# backend/gpu_proxy.DEFAULTS["tts_languages"] (tools/test_speaking_language.py
# checks they agree).
DEFAULT_CODES = ("ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "id",
                 "it", "ja", "km", "ko", "lo", "ms", "my", "nl", "no", "pl", "pt",
                 "ru", "sv", "sw", "th", "tl", "tr", "vi", "zh")

_names: dict | None = None


def code(value) -> str:
    """An ISO language code as stored ("da-DK" -> "da"), or "" if it is none."""
    c = str(value or "").strip().lower().replace("_", "-").split("-")[0]
    return c if 2 <= len(c) <= 3 and c.isalpha() else ""


def _table() -> dict:
    global _names
    if _names is None:
        try:
            from core.paths import resource
            import os
            base = os.environ.get("ASSISTANT_LOCALES_DIR")
            p = (Path(base) if base else resource("locales")) / "names" / "language_names.json"
            data = json.loads(p.read_text(encoding="utf-8"))
            _names = data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"[languages] names not read: {type(e).__name__}")
            _names = {}
    return _names


def name(value, ui: str = "") -> str:
    """The language's name in the interface's language (or `ui`)."""
    c = code(value)
    if not c:
        return str(value or "")
    if not ui:
        try:
            from core import i18n
            ui = i18n.current()
        except Exception:
            ui = "en"
    table = _table()
    ui = str(ui or "en").lower()
    for tag in (ui, ui.split("-")[0], "en"):
        got = (table.get(tag) or {}).get(c)
        if got:
            return got
    return c


def english_name(value) -> str:
    return name(value, "en")


def from_name(value) -> str:
    """The code of a language given by its name in any interface language
    ("Turkish", "Türkçe", "Türkisch" -> "tr"), or by its code; "" if unknown.
    For a language the user said in words, as memory keeps it."""
    c = code(value)
    text = " ".join(str(value or "").split()).casefold()
    if not text:
        return ""
    for names in _table().values():
        if isinstance(names, dict):
            for k, v in names.items():
                if isinstance(v, str) and v.casefold() == text:
                    return code(k)
    import re
    return c if c and re.fullmatch(r"[a-z]{2,3}(?:[-_][a-z0-9]{2,8})*", text) else ""


def available(codes=None) -> list[str]:
    """The codes to offer: the relay's, else the default voice's."""
    out = []
    for c in (codes or ()):
        c = code(c)
        if c and c not in out:
            out.append(c)
    return out or list(DEFAULT_CODES)


def options(codes=None, ui: str = "") -> list[tuple[str, str]]:
    """[(name, code)] sorted by name as the interface's language sorts it."""
    rows = [(name(c, ui), c) for c in available(codes)]
    return sorted(rows, key=lambda r: r[0].casefold())


def fill(box, codes=None, current: str = "", auto_label: str = "") -> None:
    """Fill a QComboBox: automatic first, then the languages; `current` kept
    selected (also when the list no longer has it: it is still honoured)."""
    current = code(current)
    box.blockSignals(True)
    box.clear()
    box.addItem(auto_label, "")
    for label, c in options(codes):
        box.addItem(label, c)
    if current and box.findData(current) < 0:
        box.addItem(name(current), current)
    box.setCurrentIndex(max(0, box.findData(current)))
    box.blockSignals(False)
