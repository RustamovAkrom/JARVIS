"""
core/phonegate.py — confirmations asked on the phone, for plugins that ask.

In Torvis a step requested from the phone is confirmed there with buttons.
Jarvis has no phone service, so no request ever comes from a phone: nothing is
active, nothing can be asked there, and a plugin falls back to asking on the
screen (core/confirm) — which is what every plugin does when `active()` is
empty. These are the same names, doing nothing, so a plugin written for both
loads and behaves here.
"""
from __future__ import annotations

from typing import Callable, Optional


def bind(ask, tell, log=None) -> None:
    pass


def unbind(ask) -> None:
    pass


def can_ask() -> bool:
    return False


def begin(chat, text: str = "") -> None:
    pass


def end(chat=None) -> None:
    pass


def active():
    """The phone chat a request came from: never one here."""
    return None


def confirmed_run() -> bool:
    return False


def describe(decl) -> str:
    return ""


def review(name: str, args: dict, describe: str = "", asked: str = "") -> dict:
    return {}


def gate(name: str, args: dict, run: Callable[[], str], describe: str = "") -> Optional[str]:
    """Not a phone request: nothing to gate, the caller runs it as usual."""
    return None


def ask(chat, title: str, detail: str, run: Callable[[], str],
        report: Optional[Callable[[str], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
        tool: str = "", action: str = "", times: Optional[int] = None) -> str:
    """There is no phone to ask on, so the step is NOT done - exactly what
    Torvis answers when its phone cannot be asked. (Unreachable in practice:
    a plugin asks here only for a request that came from the phone.)"""
    if on_cancel is not None:
        try:
            on_cancel()
        except Exception:
            pass
    return (f"[NOT_DONE] '{title}' needs a confirmation on the phone, and there is no "
            f"phone connected, so it was NOT done. Tell the user in one short sentence "
            f"to do it at the computer.")


def answer(chat, nonce: str, step: int, yes: bool) -> str:
    return ""


def pending_count() -> int:
    return 0
