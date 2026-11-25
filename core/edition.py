"""
core/edition.py — which program this is, for plugins that ask.

Plugins written for Torvis (the same plugin file runs in both) check
`edition.is_product()` to switch off what only the paid product has (the relay,
the licence, the phone service). Jarvis is the open-source program: never the
product, named Jarvis, no relay.
"""
from __future__ import annotations


def load() -> dict:
    return {}


def is_product() -> bool:
    return False


def name() -> str:
    try:
        from memory.config_manager import get_assistant_name
        return get_assistant_name() or "Jarvis"
    except Exception:
        return "Jarvis"


def mark() -> str:
    return "Mark LIX"


def relay() -> dict:
    """No relay: everything goes to the user's own providers."""
    return {}


def link(kind: str) -> str:
    return ""


def terms_version() -> str:
    return ""
