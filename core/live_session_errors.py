"""Classify expected Gemini Live session closures, including task groups."""


def _exceptions(exc: BaseException):
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            yield from _exceptions(child)
    else:
        yield exc


def policy_close_reason(exc: BaseException) -> str | None:
    """Return the known reason for a Gemini Live 1008 close, if present."""
    for leaf in _exceptions(exc):
        text = str(leaf).lower()
        code = str(
            getattr(leaf, "code", None) or getattr(leaf, "status_code", None) or ""
        )
        if "1008" not in text and code != "1008":
            continue
        if "inactiv" in text:
            return "inactivity"
        if "aborted" in text:
            return "aborted"
    return None
