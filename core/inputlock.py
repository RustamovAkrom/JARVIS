"""
core/inputlock.py — hold the computer's own input until a plugin lets go.

WHAT IT IS
    One shared capability any plugin may use: while something HOLDS the lock,
    the assistant does not listen to this computer. The first holder is Face
    Lock (plugins/face_lock.py), which keeps it until it has seen the owner.

    hold(owner, title, line)    take the lock (one per owner; any held = locked)
    release(owner)              let go of it
    held() / holder()           is anything holding it, and who
    show(owner, ...)            what the lock screen says: English, with
                                {placeholders} filled from `values` after it is
                                put in the interface language (core/i18n.phrase;
                                a plugin lists its lines in PLUGIN "phrases")
    frame(owner, jpeg)          a picture for the lock screen (a camera view)

WHAT IS HELD, AND WHAT IS NOT
    Held: this computer's microphone (main.py drops its audio before anything,
    the wake word included), the window (gui/lock_screen covers all of it:
    typing, buttons, settings, the plugin manager), and the things the
    assistant would say to the room by itself (routines wait - an opening
    briefing is not for whoever happens to sit down).
    Not held: requests from the phone. They arrive through the owner's own
    linked account, and somebody away from the computer has no face to show.

THE WAY BACK IN
    A camera breaks, a face changes, the light is wrong: the owner must never
    be locked out of what they pay for. The lock screen offers "unlock another
    way": the licence key this computer is signed in with, compared here in
    constant time and never logged. Wrong keys are slowed down. Without a saved
    licence (the open-source edition) there is no such way, so a holder that
    locks at start should check fallback_available() before it does.
    A hold with fallback=False (a short scan the user just asked for) shows no
    such offer: it ends by itself.

    It is a lock against people at the keyboard, not against a program running
    as this user, which can do anything this one can.

THE INTERFACE FOR HOLDERS
    A plugin is handed the interface with a tool call; a lock taken at start
    comes before any. main.py attaches it here once it exists (attach), with
    a test for "can something be said now" (can_speak), and holders wait for it
    with interface().
"""
from __future__ import annotations

import hmac
import threading
import time
from typing import Callable, Optional

_lock = threading.RLock()
_holds: dict[str, dict] = {}            # owner -> {"title", "line", "values", "tone", "fallback", "n"}
_order = [0]                            # holds taken so far: the newest is shown
_screen: dict[str, Callable] = {}       # the window's callbacks (bind_screen)
_on_release: list[Callable[[], None]] = []
_ui = None
_ui_ready = threading.Event()
_can_speak: Callable[[], bool] = lambda: False

# Wrong licence keys: a pause after a few, growing, so guessing is slow.
_FREE_TRIES = 5
_PAUSE_FIRST = 30.0
_PAUSE_MOST = 300.0
_tries = {"wrong": 0, "until": 0.0}

TONES = ("idle", "busy", "ok", "bad")


# ── holding ──────────────────────────────────────────────────────────────────

def hold(owner: str, title: str = "", line: str = "", fallback: bool = True) -> None:
    """Take the lock for `owner` (again: only updates its texts)."""
    owner = str(owner or "").strip()
    if not owner:
        raise ValueError("a hold needs an owner")
    with _lock:
        _holds[owner] = {"title": str(title or ""), "line": str(line or ""), "values": {},
                         "tone": "idle", "fallback": bool(fallback), "n": _order[0]}
        _order[0] += 1
    print(f"[Lock] held by {owner}", flush=True)
    _paint()


def release(owner: str) -> bool:
    """Let go; True when it was holding. The last one out unlocks."""
    with _lock:
        was = _holds.pop(str(owner or ""), None) is not None
        free = not _holds
    if was:
        print(f"[Lock] released by {owner}", flush=True)
        if free:
            _unlocked()
        else:
            _paint()
    return was


def held() -> bool:
    with _lock:
        return bool(_holds)


def holds(owner: str) -> bool:
    """Is `owner` holding it (a licence key may have let go for everyone)."""
    with _lock:
        return str(owner or "") in _holds


def holder() -> Optional[str]:
    """The newest holder - the one the lock screen shows."""
    with _lock:
        if not _holds:
            return None
        return max(_holds, key=lambda o: _holds[o]["n"])


def show(owner: str, title: Optional[str] = None, line: Optional[str] = None,
         tone: Optional[str] = None, values: Optional[dict] = None) -> None:
    """Change what the lock screen says while `owner` holds it. `tone` is one
    of TONES (its colour); `values` fill the {placeholders} of title and line."""
    with _lock:
        h = _holds.get(str(owner or ""))
        if h is None:
            return
        if title is not None:
            h["title"] = str(title)
        if line is not None:
            h["line"] = str(line)
        if tone is not None:
            h["tone"] = tone if tone in TONES else "idle"
        if values is not None:
            h["values"] = {str(k): str(v) for k, v in dict(values).items()}
        top = owner == holder()
    if top:
        _paint()


def frame(owner: str, jpeg: bytes) -> None:
    """A picture for the lock screen (the newest holder's only)."""
    if not jpeg or owner != holder():
        return
    cb = _screen.get("frame")
    if cb is not None:
        try:
            cb(bytes(jpeg))
        except Exception:
            pass


def on_release(fn: Callable[[], None]) -> None:
    """Called (on the releasing thread) each time the last holder lets go."""
    with _lock:
        _on_release.append(fn)


def _unlocked() -> None:
    cb = _screen.get("hide")
    if cb is not None:
        try:
            cb()
        except Exception:
            pass
    with _lock:
        fns = list(_on_release)
    for fn in fns:
        try:
            fn()
        except Exception as e:
            print(f"[Lock] after unlock: {type(e).__name__}: {e}")


def _state() -> Optional[dict]:
    with _lock:
        top = holder()
        if top is None:
            return None
        h = dict(_holds[top])
        h["values"] = dict(h.get("values") or {})
    h["owner"] = top
    h["fallback"] = bool(h.get("fallback")) and fallback_available()
    return h


def _paint() -> None:
    state = _state()
    if state is None:
        return
    cb = _screen.get("show")
    if cb is not None:
        try:
            cb(state)
        except Exception:
            pass


# ── the window ───────────────────────────────────────────────────────────────

def bind_screen(show: Callable[[dict], None], hide: Callable[[], None],
                frame: Callable[[bytes], None]) -> None:
    """The window's lock screen: show(state) / hide() / frame(jpeg). They are
    called from any thread - the window turns them into its own signals."""
    with _lock:
        _screen.update(show=show, hide=hide, frame=frame)
    _paint()


# ── the way back in ──────────────────────────────────────────────────────────

# Jarvis has no licence key. "Unlock another way" takes the user's own PIN
# instead, set in the window's settings (set_pin). Only a salted PBKDF2 hash is
# kept (config `lock_pin`): the PIN itself is never written anywhere. The same
# pause after wrong tries as for a licence key (_tries) keeps it from being
# guessed at the lock screen.
_PIN_ROUNDS = 200_000


def _pin_hash(pin: str, salt: bytes) -> str:
    import hashlib
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, _PIN_ROUNDS).hex()


def _saved_pin() -> dict:
    try:
        from memory.config_manager import load_api_keys
        v = load_api_keys().get("lock_pin")
        return v if isinstance(v, dict) and v.get("hash") and v.get("salt") else {}
    except Exception:
        return {}


def has_pin() -> bool:
    return bool(_saved_pin())


def pin_problem(pin: str) -> str:
    """"" when `pin` can be used, else why not (in English, for the caller)."""
    pin = str(pin or "").strip()
    if len(pin) < 4:
        return "at least 4 characters"
    if len(set(pin)) == 1:
        return "not one character repeated"
    return ""


def set_pin(pin: str, current: str = "") -> bool:
    """Save a new PIN. When one is set already, `current` must be it."""
    import os
    from memory.config_manager import update_config
    pin = str(pin or "").strip()
    if pin_problem(pin):
        return False
    saved = _saved_pin()
    if saved and not _pin_matches(current, saved):
        return False
    salt = os.urandom(16)
    update_config(lock_pin={"salt": salt.hex(), "hash": _pin_hash(pin, salt)})
    return True


def _pin_matches(typed: str, saved: dict) -> bool:
    try:
        got = _pin_hash(str(typed or "").strip(), bytes.fromhex(saved["salt"]))
    except Exception:
        return False
    return hmac.compare_digest(got, str(saved.get("hash") or ""))


def _saved_licence() -> str:
    """Kept for the name: Jarvis has no licence key."""
    return ""


def fallback_name() -> str:
    """What "unlock another way" takes here, in words a plugin can say
    (Torvis has no such call and says "licence key")."""
    return "unlock PIN"


def fallback_available() -> bool:
    """A PIN is set, so "unlock another way" can be offered."""
    return has_pin()


def wait_seconds() -> int:
    """How long before another licence key may be tried (0: now)."""
    with _lock:
        return max(0, int(round(_tries["until"] - time.monotonic())))


def unlock_with_licence(typed: str) -> tuple[bool, int]:
    """(unlocked, seconds to wait). The right PIN releases every hold. (The
    name is the one the lock screen and plugins use.)"""
    if wait_seconds():
        return False, wait_seconds()
    saved = _saved_pin()
    ok = bool(saved) and bool(str(typed or "").strip()) and _pin_matches(typed, saved)
    owners: list = []
    with _lock:
        if not ok:
            _tries["wrong"] += 1
            over = _tries["wrong"] - _FREE_TRIES
            if over >= 0:
                _tries["until"] = time.monotonic() + min(_PAUSE_MOST, _PAUSE_FIRST * (2 ** over))
        else:
            _tries.update(wrong=0, until=0.0)
            owners = list(_holds)
            _holds.clear()
    if not ok:
        print("[Lock] a PIN that does not match", flush=True)
        return False, wait_seconds()
    print(f"[Lock] unlocked with the PIN (held by {', '.join(owners) or 'nobody'})", flush=True)
    if owners:
        _unlocked()
    return True, 0


# ── the interface, for holders ───────────────────────────────────────────────

def attach(ui, can_speak: Optional[Callable[[], bool]] = None) -> None:
    """main.py: the interface exists (request_announce, write_log, ...)."""
    global _ui, _can_speak
    _ui = ui
    if can_speak is not None:
        _can_speak = can_speak
    _ui_ready.set()


def interface(timeout: Optional[float] = None):
    """The interface once attached (waits up to `timeout`), or None."""
    _ui_ready.wait(timeout)
    return _ui


def can_speak() -> bool:
    try:
        return bool(_can_speak())
    except Exception:
        return False


def _reset_for_tests() -> None:
    global _ui, _can_speak
    with _lock:
        _holds.clear()
        _screen.clear()
        _on_release.clear()
        _tries.update(wrong=0, until=0.0)
    _ui = None
    _can_speak = lambda: False
    _ui_ready.clear()
