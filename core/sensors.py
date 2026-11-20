"""
Sensor watcher registry and compatibility API.

Legacy callers continue using start/stop/active/stop_kind/stop_all. The typed
SensorRegistry mirrors watcher lifecycle and capabilities without owning the
actual camera/screen resource.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from core.sensor_registry import SensorRegistry, SensorSpec, SensorState

KINDS = ("camera", "screen")

_lock = threading.RLock()
_active: dict[str, dict] = {}
_listeners: list[Callable[[], None]] = []
_registry = SensorRegistry()


def registry_snapshot():
    """Return typed snapshots while preserving the legacy UI API."""
    return _registry.snapshot()


def _notify() -> None:
    with _lock:
        listeners = tuple(_listeners)
    for fn in listeners:
        try:
            fn()
        except Exception as e:
            print(f"[Sensors] listener failed: {type(e).__name__}: {e}")


def subscribe(fn: Callable[[], None]) -> Callable[[], None]:
    """Subscribe to changes. Returns an unsubscribe callback."""
    with _lock:
        _listeners.append(fn)

    def unsubscribe() -> None:
        with _lock:
            try:
                _listeners.remove(fn)
            except ValueError:
                pass

    return unsubscribe


def _remove_registry_entry(key: str) -> None:
    snapshot = _registry.get(key)
    if snapshot is None:
        return
    try:
        if snapshot.state in {SensorState.ACTIVE, SensorState.STARTING}:
            _registry.transition(key, SensorState.REGISTERED if snapshot.state == SensorState.STARTING
                                 else SensorState.STOPPING)
            if snapshot.state == SensorState.ACTIVE:
                _registry.transition(key, SensorState.REGISTERED)
        elif snapshot.state == SensorState.STOPPING:
            # A registry stop request calls the feature's callback; the feature
            # then calls sensors.stop() during its own cleanup. Complete that
            # transition here so the registry entry cannot remain stale.
            _registry.transition(key, SensorState.REGISTERED)
        _registry.unregister(key)
    except (RuntimeError, ValueError):
        # Registry state must never break the legacy sensor API.
        pass


def start(key: str, kind: str, stop: Optional[Callable[[], None]] = None) -> None:
    if kind not in KINDS or not key:
        return

    with _lock:
        previous = _active.get(key)
        _active[key] = {"kind": kind, "stop": stop}

    if previous is not None:
        _remove_registry_entry(key)

    _registry.register(
        SensorSpec(
            sensor_id=key,
            kind=kind,
            capabilities={kind},
            description="Legacy sensor watcher",
        ),
        stop=stop,
    )
    _registry.transition(key, SensorState.STARTING)
    _registry.transition(key, SensorState.ACTIVE)
    _notify()


def stop(key: str) -> None:
    """The feature has ended (it calls this itself)."""
    with _lock:
        gone = _active.pop(key, None)
    if gone is not None:
        _remove_registry_entry(key)
        _notify()


def active() -> dict[str, list[str]]:
    """Return watcher IDs grouped by kind."""
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
        stop(key)
        return
    try:
        fn()
    except Exception as e:
        print(f"[Sensors] stopping {key} failed: {type(e).__name__}: {e}")
        stop(key)
