"""Cooperative input lock shared by JARVIS plugins.

This is an application-level lock, not an operating-system security boundary.
It coordinates microphone gating and a UI adapter without installing global
keyboard hooks. UI implementations may expose ``show_input_lock``,
``update_input_lock`` and ``hide_input_lock`` methods; the module remains usable
in headless tests when no UI is registered.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class LockState:
    owner: str
    title: str
    line: str
    tone: str
    fallback: bool
    values: dict[str, Any]
    frame: Optional[bytes] = None


_lock = threading.RLock()
_changed = threading.Condition(_lock)
_interface: Any = None
_locks: dict[str, LockState] = {}
_fallback_check = None
_fallback_label = "unlock PIN"


def register_interface(interface: Any) -> None:
    """Register the live JarvisUI facade; call once after constructing it."""
    global _interface
    with _changed:
        _interface = interface
        states = tuple(_locks.values())
        _changed.notify_all()
    # Replay active lock state if the UI is attached after a plugin acquired it.
    for state in states:
        _render(state)


def unregister_interface(interface: Any | None = None) -> None:
    global _interface
    with _changed:
        if interface is None or _interface is interface:
            _interface = None
            _changed.notify_all()


def interface(wait: float = 0.0):
    """Return the registered UI, optionally waiting for UI startup."""
    deadline = time.monotonic() + max(0.0, float(wait))
    with _changed:
        while _interface is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            _changed.wait(min(remaining, 0.25))
        return _interface


def configure_fallback(check=None, label: str = "unlock PIN") -> None:
    """Install the edition-specific recovery-key check.

    ``check`` must validate that a usable recovery credential is configured;
    it must not prompt for or expose the credential itself.
    """
    global _fallback_check, _fallback_label
    with _lock:
        _fallback_check = check
        _fallback_label = str(label or "unlock PIN")


def fallback_available() -> bool:
    with _lock:
        check = _fallback_check
    if not callable(check):
        return False
    try:
        return bool(check())
    except Exception:
        return False


def fallback_name() -> str:
    with _lock:
        return _fallback_label


def set_pin(*_args, **_kwargs):
    """Compatibility hook for editions that provide their own PIN settings."""
    raise NotImplementedError("Configure the unlock PIN through JARVIS SETUP.")


def _render(state: LockState) -> None:
    ui = interface(0)
    if ui is None:
        return
    try:
        method = getattr(ui, "show_input_lock", None)
        if callable(method):
            method(state)
            return
        # Existing JARVIS UI has a PIN auth dialog, but not the live Face Lock
        # camera overlay. Use it only as a safe fallback; never claim that the
        # camera preview is being displayed when the UI cannot render it.
        if state.fallback:
            show_auth = getattr(ui, "show_auth_lock", None)
            if callable(show_auth):
                verifier = getattr(ui, "inputlock_verifier", None)
                if callable(verifier):
                    show_auth(verifier, "jarvis")
    except Exception as exc:
        print(f"[InputLock] UI update failed: {type(exc).__name__}: {exc}")


def hold(owner: str, title: str = "", line: str = "", *, fallback: bool = True,
         tone: str = "idle", values: Optional[dict[str, Any]] = None) -> bool:
    """Acquire/refresh a named lock. Locks compose: every owner must release."""
    key = str(owner or "").strip()
    if not key:
        raise ValueError("Input-lock owner must be a non-empty string")
    if fallback and not fallback_available():
        return False
    state = LockState(key, str(title or ""), str(line or ""), str(tone or "idle"),
                      bool(fallback), dict(values or {}))
    with _changed:
        _locks[key] = state
        _changed.notify_all()
    _render(state)
    return True


def show(owner: str, *, title: str | None = None, line: str | None = None,
         tone: str = "idle", values: Optional[dict[str, Any]] = None) -> bool:
    """Update the visible message for an existing lock owner."""
    key = str(owner or "").strip()
    with _changed:
        previous = _locks.get(key)
        if previous is None:
            return False
        state = LockState(key, previous.title if title is None else str(title),
                          previous.line if line is None else str(line), str(tone),
                          previous.fallback, dict(values or previous.values), previous.frame)
        _locks[key] = state
        _changed.notify_all()
    _render(state)
    return True


def frame(owner: str, jpeg: bytes) -> bool:
    """Publish the latest JPEG frame to the UI adapter (no frame queue)."""
    key = str(owner or "").strip()
    if not isinstance(jpeg, (bytes, bytearray, memoryview)):
        raise TypeError("inputlock.frame expects JPEG bytes")
    with _changed:
        previous = _locks.get(key)
        if previous is None:
            return False
        state = LockState(previous.owner, previous.title, previous.line,
                          previous.tone, previous.fallback, previous.values, bytes(jpeg))
        _locks[key] = state
    ui = interface(0)
    if ui is not None:
        try:
            update = getattr(ui, "update_input_lock", None)
            if callable(update):
                update(state)
        except Exception as exc:
            print(f"[InputLock] frame update failed: {type(exc).__name__}: {exc}")
    return True


def release(owner: str) -> bool:
    """Release one owner's lock without disturbing other active owners."""
    key = str(owner or "").strip()
    with _changed:
        removed = _locks.pop(key, None) is not None
        still_locked = bool(_locks)
        next_state = next(reversed(_locks.values()), None) if _locks else None
        _changed.notify_all()
    ui = interface(0)
    if ui is not None and removed:
        try:
            if next_state is not None:
                _render(next_state)
            else:
                hide = getattr(ui, "hide_input_lock", None)
                if callable(hide):
                    hide()
        except Exception as exc:
            print(f"[InputLock] release UI update failed: {type(exc).__name__}: {exc}")
    return removed


def holds(owner: str | None = None) -> bool:
    with _lock:
        return bool(_locks) if owner is None else str(owner) in _locks


def is_held() -> bool:
    """True while any plugin holds the local input/microphone gate."""
    return holds()


def held() -> bool:
    """Backward-compatible alias used by older imported plugins."""
    return is_held()


def can_speak() -> bool:
    """Speech is permitted while a lock is held; only local input is gated."""
    return interface(0) is not None


def active_locks() -> tuple[LockState, ...]:
    with _lock:
        return tuple(_locks.values())


def clear() -> None:
    """Release every lock during orderly application shutdown."""
    with _changed:
        owners = tuple(_locks)
    for owner in owners:
        release(owner)
