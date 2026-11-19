"""Dynamic runtime situation context and panel-host routing."""
from __future__ import annotations
import threading
from collections.abc import Callable

LINE_MAX = 600
_lock = threading.RLock()
_sources: dict[str, Callable[[], str]] = {}
_hosts: dict[str, tuple[str, Callable[..., bool]]] = {}

def register(name: str, fn: Callable[[], str]) -> None:
    """Register a short current-state source. Registration replaces same name."""
    with _lock:
        _sources[str(name)] = fn

def unregister(name: str) -> None:
    with _lock:
        _sources.pop(str(name), None)

def lines() -> list[str]:
    """Read registered context sources defensively and bound their output."""
    with _lock:
        sources = list(_sources.items())
    result=[]
    for name, fn in sources:
        try:
            value=" ".join(str(fn() or "").split())
        except Exception as exc:
            print(f"[Situation] source {name} failed: {type(exc).__name__}: {exc}")
            continue
        if value:
            result.append(f"{name}: {value[:LINE_MAX]}")
    return result

def host(name: str, label: str, fn: Callable[..., bool]) -> None:
    """Register a surface that can show panels which would otherwise be hidden."""
    with _lock:
        _hosts[str(name)] = (str(label), fn)

def unhost(name: str) -> None:
    with _lock:
        _hosts.pop(str(name), None)

def show_panel(kind: str, **options) -> str:
    """Return the label of the host that accepted a panel, or empty string."""
    with _lock:
        hosts=list(_hosts.items())
    for name, (label, fn) in hosts:
        try:
            if fn(str(kind), **options):
                return label
        except Exception as exc:
            print(f"[Situation] host {name} failed for {kind}: {type(exc).__name__}: {exc}")
    return ""
