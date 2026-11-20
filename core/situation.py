"""
core/situation.py — what is going on right now that the model cannot see:
a line or two said with every request (at its end, with the time:
providers/engine_pipeline._time_hint), never kept in the history.

    register(name, fn)    fn() -> str ("" when there is nothing to say)
    unregister(name)
    lines()               what every source says now

    host(name, label, fn)     something covering the screen shows panels itself:
                              fn(kind, **options) -> True when it showed one
    unhost(name)
    show_panel(kind, **o)     the label of the host that showed it, "" when none did

WHY
    A plugin can put something on the screen the model knows nothing about.
    The Holo Desk fills the screen with the camera picture and panels: asked
    "what am I holding?", the model looked at the computer's own screen
    (the screen agent, under the desk) and said nothing was held (live,
    2026-10-28). Told "the Holo Desk is open; held: Weather", it asks the desk.

Any plugin may use it. A source that fails or is slow says nothing: each line
is cut to LINE characters, and a source is asked only while registered.

PANELS ON WHAT COVERS THE SCREEN (host / show_panel)
    The Holo Desk covers the HUD. Asked "open the weather panel" with it open,
    the model reached the HUD's own panel (live_panels, always in its tools)
    and the panel opened UNDER the desk, unseen (live, 2026-10-28). Telling the
    model was not enough; now a plugin that shows a panel asks show_panel
    first, and whatever covers the screen shows it there.
"""
from __future__ import annotations

import threading
from typing import Callable

LINE = 600

_lock = threading.Lock()
_sources: dict[str, Callable[[], str]] = {}


def register(name: str, fn: Callable[[], str]) -> None:
    with _lock:
        _sources[str(name)] = fn


def unregister(name: str) -> None:
    with _lock:
        _sources.pop(str(name), None)


_hosts: dict[str, tuple[str, Callable[..., bool]]] = {}


def host(name: str, label: str, fn: Callable[..., bool]) -> None:
    with _lock:
        _hosts[str(name)] = (str(label), fn)


def unhost(name: str) -> None:
    with _lock:
        _hosts.pop(str(name), None)


def show_panel(kind: str, **options) -> str:
    """Show a panel of `kind` on what covers the screen, if anything does and
    takes that kind: its label ("Holo Desk"), else ""."""
    with _lock:
        hosts = list(_hosts.items())
    for name, (label, fn) in hosts:
        try:
            if fn(str(kind), **options):
                return label
        except Exception as e:
            print(f"[Situation] {name} could not show {kind}: {type(e).__name__}: {e}")
    return ""


def lines() -> list[str]:
    with _lock:
        fns = list(_sources.items())
    out = []
    for name, fn in fns:
        try:
            text = " ".join(str(fn() or "").split())
        except Exception as e:
            print(f"[Situation] {name}: {type(e).__name__}: {e}")
            continue
        if text:
            out.append(text[:LINE])
    return out
