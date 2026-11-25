"""Redaction helpers for security-sensitive diagnostics.

These helpers are for logs and error messages only. They never mutate or
replace values returned to the caller or persisted as application data.
"""
from __future__ import annotations

import re
from typing import Any

_SECRET_NAMES = (
    "password", "passwd", "pwd", "secret", "client_secret", "api_key",
    "access_key", "private_key", "access_token", "refresh_token",
    "auth_token", "bot_token", "pair_code", "pairing_code",
    "recovery_code", "authorization", "cookie",
)
_SECRET_KEY = re.compile(
    r"(?i)(?:^|[_-])(?:" + "|".join(re.escape(name) for name in _SECRET_NAMES) + r")$"
)
_SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<prefix>
        ["']?
        (?:""" + "|".join(re.escape(name) for name in _SECRET_NAMES) + r""")
        ["']?
        \s*[:=]\s*
    )
    (?P<value>
        "(?:\\.|[^"\\])*"
        |
        '(?:\\.|[^'\\])*'
        |
        (?:Bearer|Basic)\s+[^\s,;]+
        |
        [^\s,;\}\]]+
    )
    """
)


def redact_sensitive_text(value: Any, *, replacement: str = "[REDACTED]") -> str:
    """Mask known credential assignments in text while preserving other text."""
    return _SECRET_ASSIGNMENT.sub(
        lambda match: match.group("prefix") + replacement,
        str(value),
    )


def redact_sensitive_mapping(value: Any) -> Any:
    """Return a recursively redacted copy of mappings and sequences."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)):
                result[key] = "[REDACTED]" if item not in (None, "") else item
            else:
                result[key] = redact_sensitive_mapping(item)
        return result
    if isinstance(value, list):
        return [redact_sensitive_mapping(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_mapping(item) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value
