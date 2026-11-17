"""
Plugin — Gesture Control: control the assistant and the computer with your hands.

    "Turn on gesture control" → the camera watches for hands:

    ✋  open palm, held still      the assistant stops talking
    👉  swipe right / left         next / previous track
    ✌   two fingers, held          play / pause
    🤏  pinch, move up or down     volume (a live bar on screen)
    👍 / 👎  thumbs up / down       yes / no to the question on screen

While Gesture Control is active, the HUD shows a live camera preview. When a
hand is detected, its skeleton is drawn over the mirrored preview. "Turn off
gesture control" stops the camera. It can start with the assistant
(settings).

Hands: _hands.py (local models, one shared camera - the one chosen in AUDIO &
CAMERA). The Holo Desk (holo_desk.py) uses the same hands; while it is open it
owns the pinch and the swipes, and the palm still stops the assistant talking.
"""
from __future__ import annotations

import threading
import time

from core.lazyimport import installed

if not installed("cv2"):        # pragma: no cover
    raise ImportError("gesture_control needs OpenCV, which is not installed. "
                      "Run: pip install opencv-python")

try:
    from plugins import _hands as H
except ImportError:             # loaded outside the package
    import _hands as H  # type: ignore

_NS = "gesture_control"
_NAME = "gestures"
_SHOW_FPS = 20
_VOLUME_DEADZONE = 0.04      # picture heights a pinch must move before it is a volume drag
_VOLUME_PER_HEIGHT = 140.0   # percent per picture height

PLUGIN = {
    "level": "basic",
    "label": "Gesture Control",
    "summary": ("Control the assistant with your hands: an open palm stops it talking, swipes skip "
                "music, a pinch sets the volume, thumbs up says yes."),
    "setup": [
        "Needs a webcam - choose it in AUDIO & CAMERA.",
        "Say: \"Turn on gesture control.\"",
        "Open palm, held still: the assistant stops talking. Swipe right or left: next or previous track.",
        "Two fingers, held: play or pause. Pinch and move up or down: volume.",
        "Thumbs up or down: yes or no to the question on screen.",
        "Say: \"Turn off gesture control\" to stop the camera.",
    ],
    "name": "gesture_control",
    "brief": "Gesture control: start or stop controlling the assistant and the computer with hand gestures.",
    "description": (
        "Gesture control through the WEBCAM: an open palm stops the assistant talking, swipes "
        "change the track, two fingers play/pause, a pinch moved up or down sets the volume, "
        "thumbs up/down answer a confirmation. "
        "action='start': 'turn on gesture control', 'let me control you with my hands'. "
        "action='stop': 'turn off gesture control', 'stop watching my hands'. "
        "action='status': whether it is on and what the gestures are."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "'start', 'stop' or 'status'"},
        },
        "required": ["action"],
    },
}

PLUGIN_SETTINGS = {
    "namespace": _NS,
    "title": "✋  GESTURE CONTROL",
    "fields": [
        {"key": "start_on_launch", "label": "Start with the assistant", "type": "toggle", "default": False},
        {"key": "show_hands", "label": "Show my hands on the HUD while I use them", "type": "toggle",
         "default": True},
        {"key": "volume_pinch", "label": "Pinch and move to set the volume", "type": "toggle",
         "default": True},
    ],
}

# The Holo Desk takes the pinch and the swipes while it is open (it sets this).
captured = threading.Event()


def _setting(key: str, default):
    try:
        from memory.config_manager import get_plugin_setting
        val = get_plugin_setting(_NS, key, default)
    except Exception:
        return default
    return val if isinstance(val, type(default)) else default


def _ui():
    try:
        from core import inputlock
        return inputlock.interface(0)
    except Exception:
        return None


def _hud_signals():
    """Return live Qt signals, tolerating teardown between lookup and use."""
    ui = _ui()
    if ui is None:
        return None, None
    try:
        win = getattr(ui, "_win", None)
        if win is None:
            return None, None
        return getattr(win, "_cam_stream_sig", None), getattr(win, "_cam_frame_sig", None)
    except RuntimeError:
        return None, None


def _camera_preview_dismissed() -> bool:
    """Whether the person explicitly closed the HUD camera surface."""
    ui = _ui()
    try:
        win = getattr(ui, "_win", None) if ui is not None else None
        state = getattr(win, "_cam_preview_dismissed", None)
        return bool(state and state.is_set())
    except RuntimeError:
        return True


def _reset_camera_preview_dismissed() -> None:
    """A fresh Gesture Control session may show the preview again."""
    ui = _ui()
    try:
        win = getattr(ui, "_win", None) if ui is not None else None
        state = getattr(win, "_cam_preview_dismissed", None)
        if state is not None:
            state.clear()
    except RuntimeError:
        pass


class _Controller:
    def __init__(self):
        self.gestures = H.Gestures()
        self.on = False
        self.shown = False
        self.last_hand = 0.0
        self.last_show = 0.0
        self._preview_thread: threading.Thread | None = None
        self.vol = None              # {"start": %, "y": y0, "hand": id, "moving": bool, "now": %}
        self.vol_sent = 0.0
        self.flash = ("", 0.0)       # (what happened, until)

    # ── what the gestures do ────────────────────────────────────────────────
    def _act(self, event: str, began: float | None = None) -> None:
        """`began`: when the pose started (the frames' clock, time.monotonic)."""
        try:
            from core import inputlock
            if inputlock.held():
                return              # locked: the hands are nobody's until it opens
        except ImportError:
            pass
        ui = _ui()
        if event == "palm" and captured.is_set() and H.screen_user() is not None:
            return                  # a view on screen points with the open hand (_hands.on_screen)
        if event == "palm":
            fn = getattr(ui, "on_interrupt", None) if ui else None
            if callable(fn):
                fn()
            self._flash("stop")
            return
        if captured.is_set():
            return
        if event in ("thumbs_up", "thumbs_down"):
            try:
                from core import confirm
                shown = confirm.pending_since()
                # Only a thumb raised for the question answers it: one held
                # since before it appeared was meant for something else.
                if shown is not None and began is not None and began >= shown:
                    confirm.resolve(event == "thumbs_up")
                    self._flash("yes" if event == "thumbs_up" else "no")
            except Exception as e:
                print(f"[Gestures] confirmation: {e}")
            return
        try:
            from actions import computer_settings as cs
            if event == "swipe_right":
                cs.media_next()
                self._flash("next")
            elif event == "swipe_left":
                cs.media_previous()
                self._flash("previous")
            elif event == "two":
                cs.media_play_pause()
                self._flash("play/pause")
        except Exception as e:
            print(f"[Gestures] media: {type(e).__name__}: {e}")

    def _flash(self, what: str) -> None:
        print(f"[Gestures] {what}", flush=True)
        self.flash = (what, time.monotonic() + 1.2)

    def _volume(self, hands, t) -> None:
        """A pinch held and moved up or down sets the output volume."""
        if captured.is_set() or not _setting("volume_pinch", True):
            self.vol = None
            return
        pinch = next((h for h in hands if h.pinching), None)
        if pinch is None:
            self.vol = None
            return
        from core import audio_devices
        from memory.config_manager import get_output_device
        if self.vol is None or self.vol["hand"] != pinch.id:
            now = audio_devices.get_output_volume(get_output_device())
            if now is None:
                return
            self.vol = {"start": now, "y": pinch.knuckles[1], "hand": pinch.id, "moving": False, "now": now}
            return
        dy = self.vol["y"] - pinch.knuckles[1]              # up is louder; knuckles: no jump as fingers close
        if not self.vol["moving"] and abs(dy) < _VOLUME_DEADZONE:
            return
        self.vol["moving"] = True
        target = int(max(0, min(100, round(self.vol["start"] + dy * _VOLUME_PER_HEIGHT))))
        if target != self.vol["now"] and t - self.vol_sent > 0.08:
            self.vol["now"], self.vol_sent = target, t
            threading.Thread(target=audio_devices.set_output_volume,
                             args=(target, get_output_device()), daemon=True).start()

    # ── each frame ──────────────────────────────────────────────────────────
    def __call__(self, hands, frame, t) -> None:
        for event, value in self.gestures.update(hands, t):
            self._act(event, value if isinstance(value, (int, float)) else None)
        self._volume(hands, t)
        if hands:
            self.last_hand = t

    def _show(self, hands, frame, t) -> None:
        """The camera and the skeleton on the HUD while hands are in view."""
        if (
            captured.is_set()
            or not _setting("show_hands", True)
            or H.screen_user() is not None
            or _camera_preview_dismissed()
        ):
            self._hide()
            return
        stream, frame_sig = _hud_signals()
        if stream is None or frame_sig is None:
            return
        if t - self.last_show < 1.0 / _SHOW_FPS:
            return
        self.last_show = t
        if not self.shown:
            try:
                stream.emit(True)
                self.shown = True
            except RuntimeError:
                self.shown = False
                return
        img = (frame * 0.75).astype(frame.dtype)
        H.draw_hands(img, hands)
        if self.vol and self.vol.get("moving"):
            _draw_volume(img, self.vol["now"])
        what, until = self.flash
        if what and t < until:
            _draw_badge(img)
        ok, buf = H.cv2.imencode(".jpg", img, [H.cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            try:
                frame_sig.emit(buf.tobytes())
            except RuntimeError:
                self.shown = False

    def _hide(self) -> None:
        if not self.shown:
            return
        stream, _frame_sig = _hud_signals()
        if stream is not None:
            try:
                stream.emit(False)
            except RuntimeError:
                pass
        self.shown = False


def _draw_volume(img, percent: int) -> None:
    """A vertical bar on the right: how loud, as it changes."""
    cv2 = H.cv2
    h, w = img.shape[:2]
    x, top, bottom = w - 46, int(h * 0.18), int(h * 0.82)
    cv2.rectangle(img, (x, top), (x + 18, bottom), (60, 50, 0), -1)
    fill = int(bottom - (bottom - top) * percent / 100)
    cv2.rectangle(img, (x, fill), (x + 18, bottom), (255, 212, 0), -1)
    cv2.rectangle(img, (x, top), (x + 18, bottom), (255, 212, 0), 1, cv2.LINE_AA)


def _draw_badge(img) -> None:
    """A short green pulse in the corner: the gesture was understood."""
    cv2 = H.cv2
    h, w = img.shape[:2]
    cv2.circle(img, (34, 34), 14, (136, 255, 0), -1, cv2.LINE_AA)
    cv2.circle(img, (34, 34), 20, (136, 255, 0), 2, cv2.LINE_AA)


_controller = _Controller()


def _preview_loop() -> None:
    """Render the latest raw camera image separately from hand inference."""
    while _controller.on:
        try:
            if (
                captured.is_set()
                or not _setting("show_hands", True)
                or H.screen_user() is not None
                or _camera_preview_dismissed()
            ):
                _controller._hide()
                time.sleep(0.1)
                continue

            frame, frame_at = H.SERVICE.latest_camera_frame()
            if frame is None or time.monotonic() - frame_at > 1.0:
                time.sleep(0.05)
                continue

            hands, _tracked_frame, tracked_at = H.SERVICE.latest()
            # Inference may run slower than the camera. Only draw landmarks
            # while they still correspond closely to the current preview frame.
            if not tracked_at or time.monotonic() - tracked_at > 0.6:
                hands = []
            mirrored = H.cv2.flip(frame, 1)
            _controller._show(hands, mirrored, time.monotonic())
            time.sleep(1.0 / _SHOW_FPS)
        except Exception as exc:
            print(f"[Gestures] preview failed: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(0.1)


def start() -> tuple[bool, str]:
    if _controller.on:
        return True, "already on"
    if not H.SERVICE.acquire(_NAME):
        return False, H.SERVICE.error or "the hand models are missing"
    H.SERVICE.subscribe(_controller)
    _reset_camera_preview_dismissed()
    _controller.shown = False
    _controller.on = True
    threading.Thread(target=_follow_lock, daemon=True, name="gestures-lock").start()
    _controller._preview_thread = threading.Thread(
        target=_preview_loop, daemon=True, name="gestures-preview"
    )
    _controller._preview_thread.start()
    print("[Gestures] on", flush=True)
    return True, "on"


def _follow_lock() -> None:
    """Face Lock needs the camera while it holds the lock (core/inputlock):
    the hands let go of it then, and take it back after."""
    try:
        from core import inputlock
    except ImportError:
        return
    paused = False
    while _controller.on:
        if inputlock.held() and not paused:
            H.SERVICE.release(_NAME)
            _controller._hide()
            paused = True
        elif paused and not inputlock.held():
            time.sleep(1.0)                 # the lock's camera is let go first
            # Stop may have been requested while this worker slept. Do not
            # reacquire the webcam after Gesture Control has been disabled.
            if not _controller.on:
                break
            if H.SERVICE.acquire(_NAME):
                paused = False
            else:
                print(f"[Gestures] could not resume after input lock: {H.SERVICE.error}")
                break
        time.sleep(0.5)
    if paused:
        H.SERVICE.release(_NAME)


def stop() -> None:
    if not _controller.on:
        return
    H.SERVICE.unsubscribe(_controller)
    H.SERVICE.release(_NAME)
    _controller.on = False
    _controller._hide()
    preview_thread = _controller._preview_thread
    _controller._preview_thread = None
    if preview_thread and preview_thread is not threading.current_thread():
        preview_thread.join(timeout=1.0)
    print("[Gestures] off", flush=True)


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str((parameters or {}).get("action") or "").strip().lower()
    if action in ("start", "on", "enable"):
        user = H.screen_user()
        if user is not None:
            # a view on screen is moved by hand: the hands are its own now
            _name, label, take = user
            try:
                done, say = take()
            except Exception as e:
                done, say = False, f"{type(e).__name__}: {e}"
            if done:
                return (f"Hand control is on for the {label} on screen. {say}")
            print(f"[Gestures] the {label} could not take the hands: {say}")
        ok, why = start()
        if not ok:
            return f"status=failed reason={why}. Say so briefly in the user's language."
        time.sleep(2.5)                     # a camera that is busy says so within this
        if H.SERVICE.error:
            stop()
            return (f"status=failed reason={H.SERVICE.error}. Say so briefly in the user's "
                    "language; the camera is chosen in AUDIO & CAMERA.")
        return ("Gesture control is on. Tell the user, in one or two short sentences in their "
                "language: open palm stops you talking, swipe for tracks, pinch up or down for "
                "volume, thumbs up for yes.")
    if action in ("stop", "off", "disable"):
        stop()
        return "Gesture control is off; the camera is free."
    if action == "status":
        return ("Gesture control is on" if _controller.on else "Gesture control is off") + (
            ". Gestures: open palm = stop talking, swipe right/left = next/previous track, two "
            "fingers = play/pause, pinch moved up/down = volume, thumbs up/down = yes/no.")
    return "status=failed reason=unknown action; use start, stop or status."


def _start_on_launch() -> None:
    try:
        from core import inputlock
    except ImportError:
        return
    if inputlock.interface(120) is None:
        return
    while inputlock.held():                 # Face Lock first: one camera at a time
        time.sleep(1.0)
    if _setting("start_on_launch", False):
        ok, why = start()
        if not ok:
            print(f"[Gestures] could not start: {why}")


def _enabled() -> bool:
    try:
        from memory.config_manager import get_plugin_enabled
        return get_plugin_enabled(PLUGIN["name"])
    except Exception:
        return False


if _enabled():
    threading.Thread(target=_start_on_launch, daemon=True, name="gestures-start").start()
