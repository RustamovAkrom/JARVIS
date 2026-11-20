"""
core/sensors.py — what is looking at the camera or the screen right now, and
one switch that stops all of it.

WHY
    The microphone has always had its indicator and its mute key. A feature
    that watches the camera (the push-up counter) or the screen (the screen
    agent, the chat takeover, the WhatsApp call guard) ran with nothing on the
    window saying so, and stopping it meant knowing which feature to ask. Now
    whatever watches registers here while it watches; the window shows each
    one in its status strip, a click stops that one, and "Stop all" stops
    every one of them and mutes the microphone.

HOW A FEATURE USES IT (plugins included — they call core, never the reverse)
    sensors.start("screen_agent", "screen", stop=lambda: ...)   while it runs
    sensors.stop("screen_agent")                                when it ends
    `stop` is what "Stop" does: it must make the feature end soon, and the
    feature then calls sensors.stop itself. A one-shot picture (a single
    screenshot or photo) is not registered: it is over before anyone could
    press anything.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

KINDS = ("camera", "screen")

_lock = threading.Lock()
_active: dict[str, dict] = {}            # key -> {"kind", "stop"}
_listeners: list[Callable[[], None]] = []


def _notify() -> None:
    for fn in list(_listeners):
        try:
            fn()
        except Exception as e:
            print(f"[Sensors] listener failed: {type(e).__name__}: {e}")


def subscribe(fn: Callable[[], None]) -> None:
    """`fn()` after every change, on whatever thread made it."""
    _listeners.append(fn)


def start(key: str, kind: str, stop: Optional[Callable[[], None]] = None) -> None:
    if kind not in KINDS or not key:
        return
    with _lock:
        _active[key] = {"kind": kind, "stop": stop}
    _notify()


def stop(key: str) -> None:
    """The feature has ended (it calls this itself)."""
    with _lock:
        gone = _active.pop(key, None)
    if gone is not None:
        _notify()


def active() -> dict[str, list[str]]:
    """{"camera": [keys], "screen": [keys]} — only kinds in use."""
    with _lock:
        out: dict[str, list[str]] = {}
        for key, item in _active.items():
            out.setdefault(item["kind"], []).append(key)
    return out


def stop_kind(kind: str) -> list[str]:
    """Ask every feature of this kind to stop. Returns their keys."""
    with _lock:
        items = [(k, v) for k, v in _active.items() if v["kind"] == kind]
    for key, item in items:
        _ask_to_stop(key, item)
    return [k for k, _ in items]


def stop_all() -> list[str]:
    """Ask every watching feature to stop. Returns their keys."""
    with _lock:
        items = list(_active.items())
    for key, item in items:
        _ask_to_stop(key, item)
    return [k for k, _ in items]


def _ask_to_stop(key: str, item: dict) -> None:
    fn = item.get("stop")
    if fn is None:
        stop(key)               # nothing to ask: it is only shown, so it goes
        return
    try:
        fn()
    except Exception as e:
        print(f"[Sensors] stopping {key} failed: {type(e).__name__}: {e}")
        stop(key)
