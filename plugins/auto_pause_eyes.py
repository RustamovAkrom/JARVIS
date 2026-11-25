"""
JARVIS plugin — Auto-Pause When Walking Away (auto_pause_eyes.py).

Uses your webcam to detect when you get up and walk away from your desk.
Automatically pauses YouTube, Spotify, movies, or media players, and resumes
them the moment you sit back down.
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from typing import Any, Optional

try:
    import cv2
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


def _send_media_play_pause() -> None:
    """Send Windows global media play/pause key."""
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_EXTENDEDKEY, 0)
        user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
    except Exception:
        pass


PLUGIN = {
    "name": "auto_pause_eyes",
    "description": (
        "Automatically pauses whatever video, movie, or music is playing when you get up "
        "and walk away from your desk, and automatically resumes when you return. Uses the "
        "webcam for lightweight presence checking. Use for: 'turn on auto pause', 'auto pause "
        "when I walk away', 'start auto pause', 'stop auto pause', or 'is auto pause active'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["start", "stop", "status"],
                "description": "What to do: 'start' (or 'enable'), 'stop' (or 'disable'), or 'status'. Default is 'start'.",
            },
        },
        "required": [],
    },
}

_lock = threading.Lock()
_monitor_thread: Optional[threading.Thread] = None
_stop_event = threading.Event()
_is_active = False


def _presence_loop(player=None) -> None:
    global _is_active
    if not _CV2_AVAILABLE:
        return

    try:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)
    except Exception:
        return

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        # Try default backend
        cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        _is_active = False
        return

    away_count = 0
    present_count = 0
    paused_by_us = False

    try:
        while not _stop_event.is_set():
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(1.0)
                continue

            # Resize for super fast CPU inference
            small = cv2.resize(frame, (320, 240))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.3, minNeighbors=3, minSize=(30, 30))

            if len(faces) == 0:
                away_count += 1
                present_count = 0
                # If away for 2 consecutive cycles (~3 seconds)
                if away_count >= 2 and not paused_by_us:
                    paused_by_us = True
                    _send_media_play_pause()
                    if player and hasattr(player, "write_log"):
                        player.write_log("Auto-Pause: User away, paused media.")
            else:
                present_count += 1
                away_count = 0
                if present_count >= 1 and paused_by_us:
                    paused_by_us = False
                    _send_media_play_pause()
                    if player and hasattr(player, "write_log"):
                        player.write_log("Auto-Pause: User returned, resumed media.")

            # Sleep between checks for minimal CPU impact (~1.5s)
            time.sleep(1.5)
    except Exception:
        pass
    finally:
        cap.release()
        _is_active = False


def run(parameters: dict[str, Any], player=None) -> str:
    global _monitor_thread, _is_active
    action = str(parameters.get("action") or "start").strip().lower()

    if action in ["stop", "disable", "off"]:
        with _lock:
            if not _is_active:
                return "Auto-pause is not currently active."
            _stop_event.set()
            _is_active = False
        return "Auto-pause disabled. Your webcam presence monitoring is stopped."

    if action in ["status", "check"]:
        with _lock:
            active = _is_active
        return "Auto-pause is currently ACTIVE and watching." if active else "Auto-pause is currently OFF."

    # START / ENABLE
    with _lock:
        if _is_active:
            return "Auto-pause is already running and monitoring your desk."

        if not _CV2_AVAILABLE:
            return "Auto-pause requires OpenCV (cv2), which is not installed. Run: pip install opencv-python."

        _stop_event.clear()
        _is_active = True
        _monitor_thread = threading.Thread(target=_presence_loop, args=(player,), daemon=True, name="auto-pause-eyes")
        _monitor_thread.start()

    return "Auto-pause activated! When you get up from your chair, your media will pause, and it will resume when you return."
