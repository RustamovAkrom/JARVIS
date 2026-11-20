"""
core/writing.py — which writing system a text is in, from Unicode alone.

Used to notice a model answering in a different language than the user wrote
in (an English question answered in Arabic, 2026-10-20) without any list of
languages or words: LATIN, ARABIC, CYRILLIC, GREEK, HEBREW, DEVANAGARI, CJK...
come straight from each character's Unicode name. Same-script drift (English
to French) is not caught by this, on purpose: that would need word lists.
"""
from __future__ import annotations

import unicodedata


def script_of(text: str) -> str:
    """The writing system most of `text`'s letters belong to; '' when there
    are too few letters to tell."""
    counts: dict = {}
    for ch in str(text or "")[:4000]:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        script = name.split(" ")[0] if name else ""
        if script.startswith("CJK") or script in ("HIRAGANA", "KATAKANA"):
            script = "CJK"
        counts[script] = counts.get(script, 0) + 1
    if sum(counts.values()) < 4:
        return ""
    return max(counts, key=counts.get)


def same_writing(words: str, reply: str) -> bool:
    """False only when both are clearly written and in DIFFERENT scripts."""
    a, b = script_of(words), script_of(reply)
    return not a or not b or a == b
