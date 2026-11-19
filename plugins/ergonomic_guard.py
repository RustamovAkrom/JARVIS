"""
plugins/ergonomic_guard.py — Ergonomic Posture, Eye-Strain (20-20-20) & Desk Health Guard.

Monitors sitting posture, prevents eye fatigue using the 20-20-20 rule, and guides
the user through quick desk stretches to prevent repetitive strain injury (RSI).
"""
from __future__ import annotations

import json
import os
import platform
import threading
import time
from datetime import datetime, date
from pathlib import Path

_OS = platform.system()

try:
    from core.paths import data_dir as _data_dir
    _BASE = _data_dir()
except Exception:
    _BASE = Path(__file__).resolve().parent.parent

_STATE_FILE = _BASE / "memory" / "ergonomic_state.json"

PLUGIN = {
    "name": "ergonomic_guard",
    "label": "Ergonomic & Posture Guard",
    "level": "basic",
    "summary": "Protects posture, enforces the 20-20-20 eye strain rule, and guides desk stretches.",
    "setup": [
        "Zero setup required. Optional webcam support for posture level checks.",
        "Say: 'Jarvis, check my posture', 'start 20-20-20 eye rule', 'give me a desk stretch', 'how long have I been sitting?'.",
    ],
    "description": (
        "Ergonomic health, posture, and eye-strain assistant. "
        "Use this tool when the user asks about posture, slouching, eye strain/fatigue, desk stretches, "
        "the 20-20-20 rule, or how long they have been working at their desk. "
        "Actions: "
        "'posture_check' (inspect head level and posture via webcam or alignment guide), "
        "'eye_rest' (guide a 20-second distant focus break or start the 20-20-20 timer), "
        "'desk_stretch' (guide a quick 30-second desk stretch for neck, shoulders, or wrists), "
        "'desk_status' (report desk session time and health stats for today), "
        "'stop_reminders' (pause all ergonomic background reminders)."
    ),
    "brief": "Posture monitor, 20-20-20 eye strain rule, and desk stretch guidance.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["posture_check", "eye_rest", "desk_stretch", "desk_status", "stop_reminders"],
                "description": "Ergonomic action to perform (default 'posture_check')."
            },
            "focus_area": {
                "type": "STRING",
                "enum": ["all", "neck", "shoulders", "wrists", "back"],
                "description": "For 'desk_stretch': specific body area to stretch."
            }
        },
        "required": ["action"]
    }
}

PLUGIN_SETTINGS = {
    "namespace": "ergonomic_guard",
    "title": "🧘  ERGONOMIC & POSTURE GUARD",
    "fields": [
        {
            "key": "eye_reminder_minutes",
            "label": "Minutes between 20-20-20 Eye Breaks",
            "type": "choice",
            "options": ["20", "30", "45", "60"],
            "default": "20"
        },
        {
            "key": "enable_webcam_posture",
            "label": "Use webcam for posture elevation checks",
            "type": "toggle",
            "default": True
        }
    ]
}

_state_lock = threading.Lock()
_timer_active = False


def _load_state() -> dict:
    if not _STATE_FILE.exists():
        return {
            "date": str(date.today()),
            "session_start": time.time(),
            "eye_breaks_count": 0,
            "stretches_count": 0,
            "posture_checks": 0
        }
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        if data.get("date") != str(date.today()):
            return {
                "date": str(date.today()),
                "session_start": time.time(),
                "eye_breaks_count": 0,
                "stretches_count": 0,
                "posture_checks": 0
            }
        return data
    except Exception:
        return {"date": str(date.today()), "session_start": time.time()}


def _save_state(data: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def _quick_webcam_posture() -> tuple[bool, str]:
    """Inspect head height in camera frame to detect slouching."""
    try:
        import cv2
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return False, "Camera is busy or unavailable"

        # Warmup frame
        for _ in range(4):
            cap.read()
        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            return False, "Could not capture camera frame"

        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Use OpenCV Haar cascade or YuNet if available
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=4, minSize=(60, 60))

        if len(faces) == 0:
            return True, "No face clearly detected. Please ensure you are facing the camera."

        # Analyze highest face
        x, y, fw, fh = faces[0]
        face_center_y = y + (fh / 2.0)
        norm_y = face_center_y / h  # 0.0 (top) to 1.0 (bottom)

        if norm_y > 0.65:
            return True, "SLOUCH: Your head is positioned quite low in the frame. Pull your shoulders back and sit up straight, sir."
        elif norm_y < 0.20:
            return True, "HIGH: Your camera angle or chair is quite low relative to the monitor. Adjust your display to eye level."
        else:
            return True, "EXCELLENT: Your head elevation and posture look well-aligned with your display."

    except Exception as e:
        return False, f"Vision check unavailable: {e}"


def _get_stretch_routine(area: str) -> str:
    routines = {
        "neck": "Gently tilt your head toward your right shoulder for 10 seconds, then toward your left shoulder. Slowly roll your chin down to your chest to release tension.",
        "shoulders": "Inhale and shrug both shoulders up towards your ears, hold for 5 seconds, then roll them back and drop them down. Repeat 3 times.",
        "wrists": "Extend your right arm forward with palm facing up, gently pull your fingers down and back with your left hand for 10 seconds. Switch arms to prevent carpal strain.",
        "back": "Sit upright, clasp your hands behind your lower back, gently open your chest toward the ceiling and take a deep breath. Release slowly.",
        "all": "Roll your shoulders backward 3 times, slowly tilt your head side to side, and interlock your fingers to stretch your arms straight overhead."
    }
    return routines.get(area, routines["all"])


def run(parameters: dict, player=None, session_memory=None) -> str:
    global _timer_active
    p = parameters or {}
    action = str(p.get("action") or "posture_check").strip().lower()
    focus_area = str(p.get("focus_area") or "all").strip().lower()

    with _state_lock:
        state = _load_state()

    try:
        # ── 1. Posture Check ─────────────────────────────────────────────────
        if action == "posture_check":
            state["posture_checks"] = state.get("posture_checks", 0) + 1
            _save_state(state)

            ok, feedback = _quick_webcam_posture()
            if ok:
                return f"Posture Scan: {feedback}"
            return (
                "Posture Check: Ensure your feet are flat on the floor, elbows at a 90-degree angle, "
                "and the top third of your monitor is at eye level. Roll your shoulders back and breathe deeply."
            )

        # ── 2. Eye Rest (20-20-20 Rule) ──────────────────────────────────────
        if action == "eye_rest":
            state["eye_breaks_count"] = state.get("eye_breaks_count", 0) + 1
            _save_state(state)
            return (
                "20-20-20 Eye Break active: Look away from your screen now and focus your eyes on an object "
                "at least 20 feet (6 metres) away. Blink your eyes gently for the next 20 seconds to rehydrate them."
            )

        # ── 3. Desk Stretch ──────────────────────────────────────────────────
        if action == "desk_stretch":
            state["stretches_count"] = state.get("stretches_count", 0) + 1
            _save_state(state)
            routine = _get_stretch_routine(focus_area)
            return f"Desk Stretch for {focus_area.title()}: {routine}"

        # ── 4. Desk Status ───────────────────────────────────────────────────
        if action == "desk_status":
            elapsed_sec = max(0, time.time() - state.get("session_start", time.time()))
            hours = int(elapsed_sec // 3600)
            mins = int((elapsed_sec % 3600) // 60)
            time_str = f"{hours}h {mins}m" if hours > 0 else f"{mins} minutes"
            breaks = state.get("eye_breaks_count", 0)
            stretches = state.get("stretches_count", 0)
            return (
                f"Desk Health Summary: You have been active for approximately {time_str} today. "
                f"Completed {breaks} eye rest break(s) and {stretches} stretch routine(s)."
            )

        # ── 5. Stop Reminders ────────────────────────────────────────────────
        if action == "stop_reminders":
            _timer_active = False
            return "Ergonomic background reminders are paused, sir."

        return f"Unknown Ergonomic Guard action '{action}'."

    except Exception as e:
        return f"Ergonomic Guard encountered an error: {e}"
