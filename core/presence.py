"""Privacy-preserving presence state machine for JARVIS.

This module deliberately does not use a camera or record input. OS idle/lock
readings are optional; unknown readings never imply that the user is away.
The state machine is independently testable and can later be wired to the
application lifecycle and notification delivery.
"""
from __future__ import annotations

import ctypes
import platform
import subprocess
import threading
import time
from typing import Callable, Optional

FRESH_INPUT_SECONDS = 4.0
LOCK_DEBOUNCE_SECONDS = 5.0
DECLARED_AWAY_GRACE_SECONDS = 20.0
INBOX_MAX_ITEMS = 12

_SYSTEM = platform.system()


def os_idle_seconds() -> Optional[float]:
    """Return OS keyboard/mouse idle time, or None when unsupported."""
    try:
        if _SYSTEM == "Windows":
            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
            info = LASTINPUTINFO()
            info.cbSize = ctypes.sizeof(info)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return None
            now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
            return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0
        if _SYSTEM == "Darwin":
            result = subprocess.run(
                ["ioreg", "-c", "IOHIDSystem", "-d", "4"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            for line in result.stdout.splitlines():
                if "HIDIdleTime" in line:
                    return max(0.0, int(line.rsplit("=", 1)[1].strip()) / 1e9)
        if _SYSTEM == "Linux":
            # X11 only. Wayland and headless sessions intentionally return unknown.
            import os
            if not os.environ.get("DISPLAY"):
                return None
            import ctypes.util
            x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            xss = ctypes.CDLL(ctypes.util.find_library("Xss") or "libXss.so.1")
            class Info(ctypes.Structure):
                _fields_ = [("window", ctypes.c_ulong), ("state", ctypes.c_int),
                           ("kind", ctypes.c_int), ("til_or_since", ctypes.c_ulong),
                           ("idle", ctypes.c_ulong), ("eventMask", ctypes.c_ulong)]
            x11.XOpenDisplay.restype = ctypes.c_void_p
            x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            x11.XDefaultRootWindow.restype = ctypes.c_ulong
            xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(Info)
            xss.XScreenSaverQueryInfo.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                                  ctypes.POINTER(Info)]
            display = x11.XOpenDisplay(None)
            if not display:
                return None
            root = x11.XDefaultRootWindow(display)
            info = xss.XScreenSaverAllocInfo()
            if xss.XScreenSaverQueryInfo(display, root, info):
                return max(0.0, info.contents.idle / 1000.0)
    except Exception:
        return None
    return None


def screen_locked() -> Optional[bool]:
    """Return lock state where available; None means unsupported/unknown."""
    try:
        if _SYSTEM == "Windows":
            user32 = ctypes.windll.user32
            user32.OpenInputDesktop.restype = ctypes.c_void_p
            desktop = user32.OpenInputDesktop(0, False, 0x0100)
            if not desktop:
                return True
            try:
                return not bool(user32.SwitchDesktop(ctypes.c_void_p(desktop)))
            finally:
                user32.CloseDesktop(ctypes.c_void_p(desktop))
        if _SYSTEM == "Linux":
            import os
            sid = os.environ.get("XDG_SESSION_ID")
            if not sid:
                return None
            result = subprocess.run(
                ["loginctl", "show-session", sid, "-p", "LockedHint", "--value"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            value = result.stdout.strip().lower()
            return {"yes": True, "no": False}.get(value)
    except Exception:
        return None
    return None


class Presence:
    """Thread-safe away/back state and bounded inbox; no autonomous thread."""

    def __init__(self, away_minutes: float = 10.0,
                 clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self.enabled = True
        self.away_minutes = max(1.0, float(away_minutes))
        self._away = False
        self._reason = ""
        self._since = 0.0
        self._declared_at: Optional[float] = None
        self._locked_since: Optional[float] = None
        self._activity = clock()
        self._last_return = (0.0, 0.0)
        self._inbox: list[dict] = []

    def note_activity(self, source: str = "voice") -> None:
        with self._lock:
            self._activity = self._clock()

    def declare_away(self) -> None:
        with self._lock:
            now = self._clock()
            self._declared_at = now
            if not self._away:
                self._away, self._reason, self._since = True, "declared away", now

    @property
    def away(self) -> bool:
        with self._lock:
            return self.enabled and self._away

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def strongly_away(self) -> bool:
        return self.away and self.reason in ("screen locked", "declared away")

    def away_for(self) -> float:
        with self._lock:
            return max(0.0, self._clock() - self._since) if self.away else 0.0

    def hold(self, kind: str, text: str, label: str = "") -> bool:
        text = " ".join(str(text or "").split())[:700]
        if not text:
            return False
        label = " ".join(str(label or "").split())[:140] or text[:140]
        with self._lock:
            if any(item["text"] == text for item in self._inbox):
                return False
            self._inbox.append({"kind": str(kind)[:40], "text": text,
                               "label": label, "at": time.strftime("%H:%M")})
            self._inbox = self._inbox[-INBOX_MAX_ITEMS:]
            return True

    def take_inbox(self) -> list[dict]:
        with self._lock:
            items, self._inbox = self._inbox, []
            return items

    def inbox_size(self) -> int:
        with self._lock:
            return len(self._inbox)

    def apply(self, settings: dict) -> None:
        settings = settings if isinstance(settings, dict) else {}
        with self._lock:
            self.enabled = bool(settings.get("enabled", True))
            try:
                self.away_minutes = max(1.0, float(settings.get("away_minutes", self.away_minutes)))
            except (TypeError, ValueError):
                pass
            if not self.enabled:
                self._away = False
                self._reason = ""
                self._declared_at = None
                self._locked_since = None

    def poll(self, idle: Optional[float], locked: Optional[bool],
              jarvis_input: bool = False) -> Optional[tuple[str, float]]:
        if not self.enabled:
            with self._lock:
                self._away = False
                self._reason = ""
                self._declared_at = None
                self._locked_since = None
            return None
        with self._lock:
            now = self._clock()
            voice_age = max(0.0, now - self._activity)
            input_known = idle is not None and not (jarvis_input and idle < FRESH_INPUT_SECONDS)
            activity_age = min(max(0.0, idle), voice_age) if input_known else voice_age

            if locked is True:
                if self._locked_since is None:
                    self._locked_since = now
            elif locked is False:
                self._locked_since = None
            locked_long = (self._locked_since is not None and
                           now - self._locked_since >= LOCK_DEBOUNCE_SECONDS)

            if not self._away:
                if locked_long:
                    self._away, self._reason = True, "screen locked"
                    self._since = self._locked_since or now
                    return ("away", 0.0)
                if input_known and activity_age >= self.away_minutes * 60:
                    self._away, self._reason = True, "idle"
                    self._since = now - activity_age
                    return ("away", 0.0)
                return None

            if locked is True:
                if locked_long:
                    self._reason = "screen locked"
                return None
            fresh = activity_age < FRESH_INPUT_SECONDS
            if self._declared_at is not None and now - self._declared_at < DECLARED_AWAY_GRACE_SECONDS:
                fresh = False
            if fresh:
                gone = max(0.0, now - self._since)
                self._away, self._reason, self._declared_at = False, "", None
                self._since = now
                self._last_return = (now, gone)
                return ("back", gone)
            return None

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": self.enabled, "away": self.away,
                "reason": self._reason if self.away else "",
                "away_minutes": self.away_for() / 60.0,
                "held": len(self._inbox),
            }

    def describe(self) -> str:
        with self._lock:
            if not self.enabled:
                return ""
            now = self._clock()
            if self._away:
                minutes = int((now - self._since) // 60)
                return (f"The user is away ({self._reason}, {minutes} min); "
                        "unprompted notifications are held.")
            returned_at, gone = self._last_return
            if returned_at and now - returned_at < 300 and gone >= 120:
                return f"The user returned {int((now-returned_at)//60)} min ago after {int(gone//60)} min away."
            return ""
