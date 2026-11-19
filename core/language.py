"""Offline, confidence-gated language detection for typed requests."""
from __future__ import annotations
import re
import threading
import unicodedata

_MIN_PROBABILITY = 0.30
_MARGIN = 1.30
_LINKS = re.compile(r"(?:https?://|www\.)\S+|\S+@\S+")
_lock = threading.Lock()
_identifier = None
_failed = False

def _get_identifier():
    global _identifier, _failed
    if _identifier is None and not _failed:
        with _lock:
            if _identifier is None and not _failed:
                try:
                    from py3langid.langid import MODEL_FILE, LanguageIdentifier
                    _identifier = LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)
                except Exception as exc:
                    _failed = True
                    print(f"[Language] typed language detection unavailable: {type(exc).__name__}")
    return _identifier

def warm() -> None:
    """Load the model on a background thread, never on the Qt/audio thread."""
    _get_identifier()

def ready() -> bool:
    """True once the model is loaded or the optional detector failed."""
    return _identifier is not None or _failed

def written(text: str) -> str:
    """Return a confident ISO language code, or empty string when uncertain."""
    words = _LINKS.sub(" ", str(text or ""))
    if not re.search(r"[^\W\d_]", words):
        return ""
    identifier = _get_identifier()
    if identifier is None:
        return ""
    try:
        ranked = identifier.rank(words)[:2]
    except Exception:
        return ""
    if not ranked:
        return ""
    (code, probability), runner = ranked[0], ranked[1][1] if len(ranked) > 1 else 0.0
    return str(code) if probability >= _MIN_PROBABILITY and probability >= _MARGIN * runner else ""

def instruction(text: str) -> str:
    """A concise language hint for a typed user turn, if detection is confident."""
    code = written(text)
    if not code:
        return ""
    return (
        f"[LANGUAGE ROUTING: The user's typed message is in language code '{code}'. "
        f"Respond in that language unless the user explicitly requests another language.]"
    )
