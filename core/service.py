"""
core/service.py — the subscription's own speech and language servers, for
plugins that ask.

Torvis can use its subscription's servers for listening, thinking and
speaking. Jarvis has none: `linked()` is False, so a plugin written for both
(the Video summarizer, for one) uses the user's own providers instead — the
same path Torvis takes when no subscription is signed in.
"""
from __future__ import annotations

LLM = "service_llm"
STT = "service_stt"
TTS = "service_tts"
PIPELINE = {"stt": STT, "llm": LLM, "tts": TTS}
DEFAULT_MODEL = "default"


def link() -> dict | None:
    return None


def linked() -> bool:
    return False


def base_url() -> str:
    return ""
